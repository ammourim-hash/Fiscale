/**
 * Verificacao do token de troca Fiscale -> Elo.
 *
 * ---------------------------------------------------------------------
 *  Por que assimetrico
 * ---------------------------------------------------------------------
 *  A chave PRIVADA fica so no Fiscale, dentro do escritorio. O Elo — que
 *  amanha roda num VPS, a superficie mais exposta dos dois — recebe apenas
 *  a PUBLICA. Se o VPS for comprometido, o atacante consegue verificar
 *  tokens; nao consegue emitir nenhum. Com segredo compartilhado (HS256),
 *  invadir o VPS seria invadir o Fiscale junto.
 *
 * ---------------------------------------------------------------------
 *  Por que escrito a mao e nao com uma biblioteca de JWT
 * ---------------------------------------------------------------------
 *  A familia de ataques classica do JWT e a CONFUSAO DE ALGORITMO: a
 *  biblioteca le `alg` do cabecalho — que o atacante controla — e escolhe
 *  o que fazer. Trocando para `none` ou para `HS256` com a chave publica
 *  como segredo, a verificacao passa.
 *
 *  Aqui `alg` nao escolhe nada. Ha um unico caminho de verificacao,
 *  Ed25519, sempre. O campo e apenas conferido: se nao for exatamente
 *  "EdDSA", o token e recusado antes de qualquer conta. Nao existe ramo
 *  para `none`, nem para HMAC, nem para RSA — nao ha o que confundir.
 */
import { createPublicKey, timingSafeEqual, verify, type KeyObject } from "node:crypto";

import { env } from "@/env";

export interface ExchangeClaims {
  iss: string;
  aud: string;
  /** Identificador da pessoa no Fiscale (o login). */
  sub: string;
  /** Tenant a que o token da acesso. Vem assinado — nao do navegador. */
  tid: string;
  iat: number;
  exp: number;
  jti: string;
}

export type ExchangeFailure =
  | "MALFORMED"
  | "BAD_HEADER"
  | "UNKNOWN_KID"
  | "BAD_SIGNATURE"
  | "BAD_CLAIMS"
  | "BAD_ISSUER"
  | "BAD_AUDIENCE"
  | "EXPIRED"
  | "NOT_YET_VALID"
  | "LIFETIME_TOO_LONG"
  | "MISSING_JTI";

export type ExchangeVerification =
  | { ok: true; claims: ExchangeClaims; kid: string }
  | { ok: false; reason: ExchangeFailure };

/** Tolerancia de relogio entre as duas maquinas. */
const LEEWAY_SECONDS = 30;

function decodeSegment(seg: string): unknown {
  const json = Buffer.from(seg, "base64url").toString("utf8");
  return JSON.parse(json) as unknown;
}

/**
 * Chaves publicas conhecidas, por `kid`. O `kid` no cabecalho e o que
 * torna a rotacao possivel sem parada: durante a virada, as duas chaves
 * ficam configuradas e cada token e verificado com a sua.
 */
let cache: Map<string, KeyObject> | null = null;

export function publicKeys(): Map<string, KeyObject> {
  if (cache) return cache;

  // Lido de process.env, e nao do `env` congelado na importacao, para que
  // trocar a chave e chamar resetPublicKeyCache() baste — sem reiniciar o
  // processo. O formato ja foi validado por env.ts na partida.
  const texto = process.env.ELO_EXCHANGE_PUBLIC_KEYS ?? env.ELO_EXCHANGE_PUBLIC_KEYS;
  const bruto = JSON.parse(texto) as Record<string, string>;
  const mapa = new Map<string, KeyObject>();

  for (const [kid, spkiBase64] of Object.entries(bruto)) {
    const der = Buffer.from(spkiBase64, "base64");
    const chave = createPublicKey({ key: der, format: "der", type: "spki" });
    if (chave.asymmetricKeyType !== "ed25519") {
      throw new Error(`chave de troca '${kid}' nao e Ed25519`);
    }
    mapa.set(kid, chave);
  }

  cache = mapa;
  return mapa;
}

/** Usado pelos testes, que trocam as chaves entre casos. */
export function resetPublicKeyCache(): void {
  cache = null;
}

function textoIgual(a: string, b: string): boolean {
  const x = Buffer.from(a, "utf8");
  const y = Buffer.from(b, "utf8");
  return x.length === y.length && timingSafeEqual(x, y);
}

/**
 * Verifica assinatura e claims. Nao toca no banco: existencia de tenant,
 * de pessoa e de vinculo sao conferidas depois, em exchange.ts.
 *
 * Nenhum campo e aceito por estar presente. Cada um e comparado com o que
 * se espera dele.
 */
export function verifyExchangeToken(
  token: string,
  agora: Date = new Date(),
): ExchangeVerification {
  const partes = token.split(".");
  if (partes.length !== 3) return { ok: false, reason: "MALFORMED" };

  const [cabecalho64, corpo64, assinatura64] = partes as [string, string, string];

  let cabecalho: { alg?: unknown; typ?: unknown; kid?: unknown };
  try {
    cabecalho = decodeSegment(cabecalho64) as typeof cabecalho;
  } catch {
    return { ok: false, reason: "MALFORMED" };
  }

  // `alg` nao escolhe caminho; so precisa ser o unico que existe.
  if (cabecalho.alg !== "EdDSA") return { ok: false, reason: "BAD_HEADER" };
  if (cabecalho.typ !== undefined && cabecalho.typ !== "JWT") {
    return { ok: false, reason: "BAD_HEADER" };
  }
  if (typeof cabecalho.kid !== "string" || cabecalho.kid.length === 0) {
    return { ok: false, reason: "BAD_HEADER" };
  }

  const chave = publicKeys().get(cabecalho.kid);
  if (!chave) return { ok: false, reason: "UNKNOWN_KID" };

  let assinatura: Buffer;
  try {
    assinatura = Buffer.from(assinatura64, "base64url");
  } catch {
    return { ok: false, reason: "MALFORMED" };
  }

  const assinado = Buffer.from(`${cabecalho64}.${corpo64}`, "utf8");
  // `null` como algoritmo e o que a API do Node pede para Ed25519: o
  // algoritmo de digest vem da propria curva, nao de um parametro.
  if (!verify(null, assinado, chave, assinatura)) {
    return { ok: false, reason: "BAD_SIGNATURE" };
  }

  let corpo: Record<string, unknown>;
  try {
    corpo = decodeSegment(corpo64) as Record<string, unknown>;
  } catch {
    return { ok: false, reason: "MALFORMED" };
  }

  const { iss, aud, sub, tid, iat, exp, jti } = corpo;

  if (
    typeof iss !== "string" ||
    typeof aud !== "string" ||
    typeof sub !== "string" ||
    typeof tid !== "string" ||
    typeof iat !== "number" ||
    typeof exp !== "number" ||
    sub.length === 0 ||
    tid.length === 0
  ) {
    return { ok: false, reason: "BAD_CLAIMS" };
  }

  // jti ausente e recusa explicita, nao "ah, entao nao ha replay a checar".
  if (typeof jti !== "string" || jti.length < 8) {
    return { ok: false, reason: "MISSING_JTI" };
  }

  if (!textoIgual(iss, env.ELO_EXCHANGE_ISSUER)) {
    return { ok: false, reason: "BAD_ISSUER" };
  }
  if (!textoIgual(aud, env.ELO_EXCHANGE_AUDIENCE)) {
    return { ok: false, reason: "BAD_AUDIENCE" };
  }

  const segundos = Math.floor(agora.getTime() / 1000);
  if (exp <= segundos - LEEWAY_SECONDS) return { ok: false, reason: "EXPIRED" };
  if (iat > segundos + LEEWAY_SECONDS) return { ok: false, reason: "NOT_YET_VALID" };

  // Um bilhete de entrada com validade de um dia nao e bilhete de entrada.
  // Recusar aqui impede que uma mudanca descuidada no Fiscale transforme o
  // token de troca em credencial de longa duracao sem ninguem perceber.
  if (exp - iat > env.ELO_EXCHANGE_MAX_LIFETIME_SECONDS) {
    return { ok: false, reason: "LIFETIME_TOO_LONG" };
  }

  return {
    ok: true,
    kid: cabecalho.kid,
    claims: { iss, aud, sub, tid, iat, exp, jti },
  };
}
