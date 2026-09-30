/**
 * Autenticacao de maquina da integracao Fiscale -> Elo.
 *
 * ---------------------------------------------------------------------
 *  Por que assinatura, e nao uma API key
 * ---------------------------------------------------------------------
 *  Chave estatica pura tem dois problemas que importam aqui: ela viaja em
 *  toda requisicao (basta um log de proxy mal configurado para vazar), e
 *  quem a captura pode reenviar o que quiser, para sempre.
 *
 *  Com assinatura, o segredo nunca sai da maquina do escritorio. O que
 *  viaja e a prova de que ele existe, valida para AQUELA requisicao — um
 *  metodo, um caminho, um corpo, um instante, um nonce.
 *
 *  E assimetrica, Ed25519, pelo mesmo motivo do token de troca: o Elo vai
 *  para um VPS, e la nao pode existir nada capaz de FORJAR requisicao do
 *  Fiscale. O Elo guarda so a chave publica.
 *
 * ---------------------------------------------------------------------
 *  Chave separada da chave de login
 * ---------------------------------------------------------------------
 *  Poderia reaproveitar o par do exchange token — mesma maquina, mesmo
 *  dominio de confianca. Sao pares distintos porque servem a coisas
 *  distintas: uma assina "esta pessoa acabou de entrar", a outra assina
 *  "este cadastro mudou". Separadas, cada uma rotaciona no seu tempo, e
 *  comprometer uma nao entrega a outra.
 *
 * ---------------------------------------------------------------------
 *  O tenant vem da CHAVE
 * ---------------------------------------------------------------------
 *  Cada `kid` registrado aponta para um tenant. O tenant NUNCA vem do
 *  corpo da requisicao. Se o payload trouxer um `tenantId` diferente, e
 *  recusa explicita — nao descarte silencioso: divergencia ali e erro de
 *  configuracao ou tentativa, e as duas precisam aparecer.
 */
import { createHash, createPublicKey, timingSafeEqual, verify, type KeyObject } from "node:crypto";

import { env } from "@/env";
import { prisma } from "@/server/db/client";
import { logger } from "@/server/logging/logger";

export const HEADER_KEY_ID = "x-elo-key-id";
export const HEADER_TIMESTAMP = "x-elo-timestamp";
export const HEADER_NONCE = "x-elo-nonce";
export const HEADER_SIGNATURE = "x-elo-signature";

/** Janela de tolerancia do relogio. Fora dela, a requisicao e velha. */
export const JANELA_SEGUNDOS = 300;

export interface IntegrationIdentity {
  kid: string;
  tenantId: string;
  source: string;
}

interface ChaveRegistrada extends IntegrationIdentity {
  publicKey: KeyObject;
}

export type SignatureFailure =
  | "MISSING_HEADERS"
  | "UNKNOWN_KEY"
  | "BAD_TIMESTAMP"
  | "STALE_TIMESTAMP"
  | "BODY_MISMATCH"
  | "BAD_SIGNATURE"
  | "REPLAY";

export type SignatureVerification =
  | { ok: true; identity: IntegrationIdentity }
  | { ok: false; reason: SignatureFailure };

let cache: Map<string, ChaveRegistrada> | null = null;

/**
 * Registro das chaves. Formato:
 *   {"<kid>": {"tenantId": "<uuid>", "source": "FISCALE", "publicKey": "<SPKI DER base64>"}}
 *
 * Mais de uma entrada permite rotacionar sem parada, e tambem atender
 * mais de um escritorio no mesmo Elo — cada um com a sua chave e o seu
 * tenant.
 */
export function integrationKeys(): Map<string, ChaveRegistrada> {
  if (cache) return cache;

  const texto = process.env.ELO_INTEGRATION_PUBLIC_KEYS ?? env.ELO_INTEGRATION_PUBLIC_KEYS;
  const bruto = JSON.parse(texto) as Record<
    string,
    { tenantId?: string; source?: string; publicKey?: string }
  >;

  const mapa = new Map<string, ChaveRegistrada>();
  for (const [kid, v] of Object.entries(bruto)) {
    if (!v.tenantId || !v.publicKey) {
      throw new Error(`chave de integracao '${kid}' sem tenantId ou publicKey`);
    }
    const chave = createPublicKey({
      key: Buffer.from(v.publicKey, "base64"),
      format: "der",
      type: "spki",
    });
    if (chave.asymmetricKeyType !== "ed25519") {
      throw new Error(`chave de integracao '${kid}' nao e Ed25519`);
    }
    mapa.set(kid, {
      kid,
      tenantId: v.tenantId,
      source: v.source ?? "FISCALE",
      publicKey: chave,
    });
  }

  cache = mapa;
  return mapa;
}

export function resetIntegrationKeyCache(): void {
  cache = null;
}

export function bodyHash(corpo: string): string {
  return createHash("sha256").update(corpo, "utf8").digest("hex");
}

/**
 * O texto que e assinado.
 *
 * Metodo e caminho entram de proposito: sem eles, uma assinatura valida
 * para `/customers/sync` serviria em qualquer outro endpoint que aceitasse
 * o mesmo corpo.
 */
export function signingString(
  method: string,
  path: string,
  timestamp: string,
  nonce: string,
  hashDoCorpo: string,
): string {
  return [method.toUpperCase(), path, timestamp, nonce, hashDoCorpo].join("\n");
}

function igual(a: string, b: string): boolean {
  const x = Buffer.from(a, "utf8");
  const y = Buffer.from(b, "utf8");
  return x.length === y.length && timingSafeEqual(x, y);
}

/**
 * Marca o nonce como usado. Um comando, decisao do indice — o mesmo
 * desenho do anti-replay do token de troca, pelos mesmos motivos: nao
 * existe intervalo entre consultar e gravar onde duas copias passariam.
 */
async function consumirNonce(nonce: string, expiraEm: Date): Promise<boolean> {
  const afetadas = await prisma.$executeRaw`
    INSERT INTO consumed_integration_nonces (nonce, expires_at)
    VALUES (${nonce}, ${expiraEm})
    ON CONFLICT DO NOTHING
  `;
  return afetadas === 1;
}

export async function purgeExpiredNonces(): Promise<number> {
  return prisma.$executeRaw`
    DELETE FROM consumed_integration_nonces WHERE expires_at < now()
  `;
}

export interface SignedRequest {
  method: string;
  path: string;
  headers: {
    keyId: string | null;
    timestamp: string | null;
    nonce: string | null;
    signature: string | null;
  };
  /** Corpo CRU, exatamente como chegou. Reserializar mudaria o hash. */
  rawBody: string;
}

/**
 * Confere a requisicao. A ordem e do mais barato para o mais caro, e o
 * nonce so e consumido depois de a assinatura passar — senao qualquer um
 * poderia queimar nonces alheios mandando lixo.
 */
export async function verifySignedRequest(
  req: SignedRequest,
  agora: Date = new Date(),
): Promise<SignatureVerification> {
  const { keyId, timestamp, nonce, signature } = req.headers;

  if (!keyId || !timestamp || !nonce || !signature) {
    return { ok: false, reason: "MISSING_HEADERS" };
  }
  if (nonce.length < 8 || nonce.length > 100) {
    return { ok: false, reason: "MISSING_HEADERS" };
  }

  const registrada = integrationKeys().get(keyId);
  if (!registrada) return { ok: false, reason: "UNKNOWN_KEY" };

  const segundos = Number(timestamp);
  if (!Number.isFinite(segundos) || !Number.isInteger(segundos)) {
    return { ok: false, reason: "BAD_TIMESTAMP" };
  }

  const diferenca = Math.abs(Math.floor(agora.getTime() / 1000) - segundos);
  if (diferenca > JANELA_SEGUNDOS) return { ok: false, reason: "STALE_TIMESTAMP" };

  const hash = bodyHash(req.rawBody);

  let assinatura: Buffer;
  try {
    assinatura = Buffer.from(signature, "base64url");
  } catch {
    return { ok: false, reason: "BAD_SIGNATURE" };
  }

  const texto = signingString(req.method, req.path, timestamp, nonce, hash);
  if (!verify(null, Buffer.from(texto, "utf8"), registrada.publicKey, assinatura)) {
    // Pode ser corpo adulterado ou chave errada; do lado de fora e a
    // mesma coisa, e nao ha por que ajudar a distinguir.
    return { ok: false, reason: "BAD_SIGNATURE" };
  }

  // A assinatura ja garante que o corpo e o assinado. Esta comparacao
  // existe para o caso de o hash ter sido calculado sobre outra coisa por
  // engano numa refatoracao futura — barata, e o teste segura o contrato.
  if (!igual(hash, bodyHash(req.rawBody))) {
    return { ok: false, reason: "BODY_MISMATCH" };
  }

  // Vale ate o fim da janela; depois disso o proprio timestamp recusa.
  const expiraEm = new Date((segundos + JANELA_SEGUNDOS) * 1000);
  if (!(await consumirNonce(nonce, expiraEm))) {
    logger.warn("integration.replay", { kid: keyId });
    return { ok: false, reason: "REPLAY" };
  }

  void purgeExpiredNonces().catch((erro: unknown) =>
    logger.warn("integration.purge_failed", { erro }),
  );

  return {
    ok: true,
    identity: {
      kid: registrada.kid,
      tenantId: registrada.tenantId,
      source: registrada.source,
    },
  };
}
