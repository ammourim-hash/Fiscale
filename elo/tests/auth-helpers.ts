/**
 * Apoio aos testes de identidade e sessao.
 *
 * Emite tokens de troca de verdade — par Ed25519 gerado na hora, assinado
 * com a mesma estrutura que o Fiscale usa. Nada de simular a verificacao:
 * o que roda no teste e o mesmo caminho criptografico da producao.
 */
import { generateKeyPairSync, randomUUID, sign, type KeyObject } from "node:crypto";

import { resetPublicKeyCache } from "@/server/auth/exchange-token";
import {
  provisionMembership,
  provisionTenant,
  type ProvisionedMembership,
} from "@/server/admin/provisioning";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

export interface ParDeChaves {
  kid: string;
  publicKey: KeyObject;
  privateKey: KeyObject;
  spkiBase64: string;
}

export function gerarPar(kid = `k-${randomUUID().slice(0, 8)}`): ParDeChaves {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  return {
    kid,
    publicKey,
    privateKey,
    spkiBase64: publicKey.export({ format: "der", type: "spki" }).toString("base64"),
  };
}

/**
 * Instala as chaves publicas no ambiente e limpa o cache do verificador.
 * Aceita varias para os testes de rotacao e de kid desconhecido.
 */
export function instalarChaves(...pares: ParDeChaves[]): void {
  const mapa: Record<string, string> = {};
  for (const p of pares) mapa[p.kid] = p.spkiBase64;
  process.env.ELO_EXCHANGE_PUBLIC_KEYS = JSON.stringify(mapa);
  resetPublicKeyCache();
}

function b64url(v: object): string {
  return Buffer.from(JSON.stringify(v), "utf8").toString("base64url");
}

export interface OpcoesToken {
  sub: string;
  tid: string;
  iss?: string;
  aud?: string;
  jti?: string | null;
  iat?: number;
  /** Segundos de vida. O Fiscale usa 90. */
  lifetime?: number;
  /** Para o caso "kid que o Elo nao conhece". */
  kidOverride?: string;
  alg?: string;
  typ?: string;
}

/** Monta e assina um token de troca. */
export function assinarToken(par: ParDeChaves, o: OpcoesToken): string {
  const iat = o.iat ?? Math.floor(Date.now() / 1000);
  const exp = iat + (o.lifetime ?? 90);

  const cabecalho: Record<string, unknown> = {
    alg: o.alg ?? "EdDSA",
    typ: o.typ ?? "JWT",
    kid: o.kidOverride ?? par.kid,
  };

  const corpo: Record<string, unknown> = {
    iss: o.iss ?? "fiscale.local",
    aud: o.aud ?? "elo",
    sub: o.sub,
    tid: o.tid,
    iat,
    exp,
  };
  // `null` = teste do token sem jti.
  if (o.jti !== null) corpo.jti = o.jti ?? randomUUID();

  const assinado = `${b64url(cabecalho)}.${b64url(corpo)}`;
  const assinatura = sign(null, Buffer.from(assinado, "utf8"), par.privateKey);
  return `${assinado}.${assinatura.toString("base64url")}`;
}

export interface TenantFixture {
  id: string;
  slug: string;
  membership: ProvisionedMembership;
  fiscaleUid: string;
  email: string;
}

const RUN = randomUUID().slice(0, 8);

export async function criarTenantComPessoa(
  rotulo: string,
  role: Role = "ADMIN" as Role,
): Promise<TenantFixture> {
  const slug = `${rotulo}-${RUN}`;
  const tenant = await provisionTenant({ slug, name: `Tenant ${rotulo}` });

  const fiscaleUid = `${rotulo}.${RUN}`;
  const email = `${rotulo}@${RUN}.teste`;

  const membership = await provisionMembership({
    tenantId: tenant.id,
    role,
    email,
    name: `Pessoa ${rotulo}`,
    fiscaleUid,
  });

  return { id: tenant.id, slug, membership, fiscaleUid, email };
}

export async function removerTenant(id: string): Promise<void> {
  await withTenant(id, async (tx) => {
    await tx.tenant.delete({ where: { id } });
  });
}

/** Desativa o vinculo sem apagar nada — o caso "pessoa afastada". */
export async function desativarMembership(
  tenantId: string,
  membershipId: string,
): Promise<void> {
  await withTenant(tenantId, async (tx) => {
    await tx.membership.update({ where: { id: membershipId }, data: { active: false } });
  });
}

export async function desativarIdentidade(
  tenantId: string,
  identityId: string,
): Promise<void> {
  await withTenant(tenantId, async (tx) => {
    await tx.identity.update({ where: { id: identityId }, data: { active: false } });
  });
}

export async function desativarTenant(tenantId: string): Promise<void> {
  await withTenant(tenantId, async (tx) => {
    await tx.tenant.update({ where: { id: tenantId }, data: { active: false } });
  });
}

/** Departamento de apoio aos testes de atendimento. */
export async function criarDepartamentoTeste(
  tenantId: string,
  slug: string,
  name: string,
): Promise<{ id: string; name: string }> {
  const { createDepartment } = await import("@/server/admin/provisioning");
  const d = await createDepartment({ tenantId, slug, name });
  return { id: d.id, name: d.name };
}
