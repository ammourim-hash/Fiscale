/**
 * Apoio aos testes de sincronizacao.
 *
 * Assina requisicoes de verdade, com par Ed25519 gerado na hora — o mesmo
 * caminho criptografico que o Fiscale usa em producao.
 */
import { generateKeyPairSync, randomUUID, sign, type KeyObject } from "node:crypto";

import {
  bodyHash,
  resetIntegrationKeyCache,
  signingString,
  HEADER_KEY_ID,
  HEADER_NONCE,
  HEADER_SIGNATURE,
  HEADER_TIMESTAMP,
} from "@/server/integrations/signature";

export interface ChaveIntegracao {
  kid: string;
  tenantId: string;
  source: string;
  privateKey: KeyObject;
  spkiBase64: string;
}

export function gerarChaveIntegracao(tenantId: string, source = "FISCALE"): ChaveIntegracao {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  return {
    kid: `int-${randomUUID().slice(0, 8)}`,
    tenantId,
    source,
    privateKey,
    spkiBase64: publicKey.export({ format: "der", type: "spki" }).toString("base64"),
  };
}

export function instalarChavesIntegracao(...chaves: ChaveIntegracao[]): void {
  const mapa: Record<string, { tenantId: string; source: string; publicKey: string }> = {};
  for (const c of chaves) {
    mapa[c.kid] = { tenantId: c.tenantId, source: c.source, publicKey: c.spkiBase64 };
  }
  process.env.ELO_INTEGRATION_PUBLIC_KEYS = JSON.stringify(mapa);
  resetIntegrationKeyCache();
}

export const CAMINHO_SYNC = "/api/integrations/fiscale/customers/sync";

export interface OpcoesAssinatura {
  path?: string;
  method?: string;
  timestamp?: number;
  nonce?: string;
  /** Para o caso "assinou uma coisa, mandou outra". */
  corpoAssinado?: string;
  kidOverride?: string;
}

export interface RequisicaoAssinada {
  method: string;
  path: string;
  headers: {
    keyId: string | null;
    timestamp: string | null;
    nonce: string | null;
    signature: string | null;
  };
  rawBody: string;
}

/** Monta a requisicao assinada no formato que verifySignedRequest espera. */
export function assinarRequisicao(
  chave: ChaveIntegracao,
  corpo: string,
  o: OpcoesAssinatura = {},
): RequisicaoAssinada {
  const method = o.method ?? "POST";
  const path = o.path ?? CAMINHO_SYNC;
  const timestamp = String(o.timestamp ?? Math.floor(Date.now() / 1000));
  const nonce = o.nonce ?? randomUUID();
  const hash = bodyHash(o.corpoAssinado ?? corpo);

  const texto = signingString(method, path, timestamp, nonce, hash);
  const assinatura = sign(null, Buffer.from(texto, "utf8"), chave.privateKey);

  return {
    method,
    path,
    headers: {
      keyId: o.kidOverride ?? chave.kid,
      timestamp,
      nonce,
      signature: assinatura.toString("base64url"),
    },
    rawBody: corpo,
  };
}

/** Cabecalhos HTTP prontos, para o teste ponta a ponta por rede. */
export function cabecalhosAssinatura(r: RequisicaoAssinada): Record<string, string> {
  return {
    "content-type": "application/json",
    [HEADER_KEY_ID]: r.headers.keyId ?? "",
    [HEADER_TIMESTAMP]: r.headers.timestamp ?? "",
    [HEADER_NONCE]: r.headers.nonce ?? "",
    [HEADER_SIGNATURE]: r.headers.signature ?? "",
  };
}

export interface ClienteTeste {
  externalId: string;
  displayName: string;
  document?: string | null;
  email?: string | null;
  phone?: string | null;
  active?: boolean;
  sourceVersion?: string;
  sourceUpdatedAt?: string;
}

export function corpoSync(
  clientes: ClienteTeste[],
  mode: "FULL" | "INCREMENTAL" = "FULL",
  extra: Record<string, unknown> = {},
): string {
  return JSON.stringify({
    source: "FISCALE",
    mode,
    generatedAt: new Date().toISOString(),
    customers: clientes.map((c) => ({
      externalId: c.externalId,
      displayName: c.displayName,
      document: c.document ?? null,
      email: c.email ?? null,
      phone: c.phone ?? null,
      active: c.active ?? true,
      sourceVersion: c.sourceVersion ?? `v-${c.displayName}-${c.phone ?? ""}`,
      sourceUpdatedAt: c.sourceUpdatedAt ?? new Date().toISOString(),
    })),
    ...extra,
  });
}
