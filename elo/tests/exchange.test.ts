/**
 * O token de troca Fiscale -> Elo.
 *
 * Cobre os dois lados: o que precisa passar e, principalmente, tudo o que
 * precisa ser recusado. A lista de recusas e mais longa que a de sucessos
 * de proposito — e nela que mora a seguranca.
 */
import { randomUUID } from "node:crypto";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { exchange } from "@/server/auth/exchange";
import { verifyExchangeToken } from "@/server/auth/exchange-token";
import { resolveSession } from "@/server/auth/session";

import {
  assinarToken,
  criarTenantComPessoa,
  desativarIdentidade,
  desativarMembership,
  desativarTenant,
  gerarPar,
  instalarChaves,
  removerTenant,
  type ParDeChaves,
  type TenantFixture,
} from "./auth-helpers";
import { fecharPrisma } from "./helpers";

let par: ParDeChaves;
let A: TenantFixture;
let B: TenantFixture;

beforeAll(async () => {
  par = gerarPar("teste-1");
  instalarChaves(par);
  A = await criarTenantComPessoa("xa");
  B = await criarTenantComPessoa("xb");
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

function tokenValido(t: TenantFixture = A, extra: Record<string, unknown> = {}): string {
  return assinarToken(par, { sub: t.fiscaleUid, tid: t.id, ...extra });
}

describe("troca valida", () => {
  it("cria sessao e o cookie resolve para a pessoa certa", async () => {
    const r = await exchange(tokenValido());
    expect(r.ok).toBe(true);
    if (!r.ok) return;

    expect(r.tenantId).toBe(A.id);
    expect(r.membershipId).toBe(A.membership.membershipId);
    // O cookie carrega o tenant na frente do token — ver session.ts.
    expect(r.session.cookieValue.startsWith(`${A.id}.`)).toBe(true);

    const sessao = await resolveSession(r.session.cookieValue);
    expect(sessao.ok).toBe(true);
    if (!sessao.ok) return;
    expect(sessao.context.tenantId).toBe(A.id);
    expect(sessao.context.email).toBe(A.email);
    expect(sessao.context.role).toBe("ADMIN");
  });

  it("registra AUTH_EXCHANGE_SUCCESS e LOGIN_SESSION_CREATED na auditoria", async () => {
    const { withTenant } = await import("@/server/tenancy");
    const r = await exchange(tokenValido());
    expect(r.ok).toBe(true);

    const registros = await withTenant(A.id, async (tx) =>
      tx.auditLog.findMany({ select: { action: true }, orderBy: { createdAt: "desc" }, take: 2 }),
    );
    const acoes = registros.map((x) => x.action);
    expect(acoes).toContain("AUTH_EXCHANGE_SUCCESS");
    expect(acoes).toContain("LOGIN_SESSION_CREATED");
  });

  it("a auditoria nao guarda o token nem o hash da sessao", async () => {
    const { withTenant } = await import("@/server/tenancy");
    const token = tokenValido();
    await exchange(token);

    const registros = await withTenant(A.id, async (tx) =>
      tx.auditLog.findMany({ select: { metadata: true, targetId: true } }),
    );
    const texto = JSON.stringify(registros);
    expect(texto).not.toContain(token);
    expect(texto).not.toContain(token.split(".")[2]);
  });
});

describe("assinatura e cabecalho", () => {
  it("assinatura adulterada e recusada", async () => {
    const token = tokenValido();
    const partes = token.split(".");
    const sig = partes[2]!;
    // Troca um caractere do MEIO da assinatura. No ultimo caractere de um
    // base64url nem todos os bits sao significativos: 'A' e 'B' ali podem
    // decodificar para os mesmos bytes, e o teste passaria sem ter
    // adulterado nada.
    const trocado = sig.slice(0, 10) + (sig[10] === "A" ? "B" : "A") + sig.slice(11);
    expect(trocado).not.toBe(sig);
    const r = await exchange(`${partes[0]}.${partes[1]}.${trocado}`);
    expect(r).toEqual({ ok: false, reason: "BAD_SIGNATURE" });
  });

  it("corpo adulterado invalida a assinatura", async () => {
    const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id });
    const [cab, , sig] = token.split(".") as [string, string, string];
    // Troca o tenant no corpo mantendo a assinatura antiga.
    const corpoFalso = Buffer.from(
      JSON.stringify({
        iss: "fiscale.local",
        aud: "elo",
        sub: A.fiscaleUid,
        tid: B.id,
        iat: Math.floor(Date.now() / 1000),
        exp: Math.floor(Date.now() / 1000) + 90,
        jti: randomUUID(),
      }),
      "utf8",
    ).toString("base64url");

    const r = await exchange(`${cab}.${corpoFalso}.${sig}`);
    expect(r).toEqual({ ok: false, reason: "BAD_SIGNATURE" });
  });

  it("chave errada e recusada", async () => {
    const outro = gerarPar("teste-1"); // mesmo kid, chave diferente
    const token = assinarToken(outro, { sub: A.fiscaleUid, tid: A.id });
    const r = await exchange(token);
    expect(r).toEqual({ ok: false, reason: "BAD_SIGNATURE" });
  });

  it("kid desconhecido e recusado antes de verificar assinatura", () => {
    const token = assinarToken(par, {
      sub: A.fiscaleUid,
      tid: A.id,
      kidOverride: "kid-que-nao-existe",
    });
    expect(verifyExchangeToken(token)).toEqual({ ok: false, reason: "UNKNOWN_KID" });
  });

  it("alg=none e recusado", () => {
    const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id, alg: "none" });
    expect(verifyExchangeToken(token)).toEqual({ ok: false, reason: "BAD_HEADER" });
  });

  it("alg=HS256 e recusado — nao existe caminho de HMAC", () => {
    const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id, alg: "HS256" });
    expect(verifyExchangeToken(token)).toEqual({ ok: false, reason: "BAD_HEADER" });
  });

  it("token malformado e recusado", async () => {
    expect(await exchange("isto.nao.e.um.token")).toEqual({ ok: false, reason: "MALFORMED" });
    expect(await exchange("")).toEqual({ ok: false, reason: "MALFORMED" });
  });
});

describe("claims", () => {
  it("issuer diferente e recusado", async () => {
    const r = await exchange(tokenValido(A, { iss: "outro.sistema" }));
    expect(r).toEqual({ ok: false, reason: "BAD_ISSUER" });
  });

  it("audience diferente e recusada", async () => {
    const r = await exchange(tokenValido(A, { aud: "outro-app" }));
    expect(r).toEqual({ ok: false, reason: "BAD_AUDIENCE" });
  });

  it("token expirado e recusado", async () => {
    const agora = Math.floor(Date.now() / 1000);
    const r = await exchange(
      assinarToken(par, { sub: A.fiscaleUid, tid: A.id, iat: agora - 600, lifetime: 90 }),
    );
    expect(r).toEqual({ ok: false, reason: "EXPIRED" });
  });

  it("token emitido no futuro e recusado", async () => {
    const agora = Math.floor(Date.now() / 1000);
    const r = await exchange(
      assinarToken(par, { sub: A.fiscaleUid, tid: A.id, iat: agora + 600 }),
    );
    expect(r).toEqual({ ok: false, reason: "NOT_YET_VALID" });
  });

  it("vida longa demais e recusada mesmo com assinatura valida", async () => {
    // Um "token de troca" de 8 horas seria credencial permanente disfarcada.
    const r = await exchange(
      assinarToken(par, { sub: A.fiscaleUid, tid: A.id, lifetime: 28_800 }),
    );
    expect(r).toEqual({ ok: false, reason: "LIFETIME_TOO_LONG" });
  });

  it("token sem jti e recusado", async () => {
    const r = await exchange(assinarToken(par, { sub: A.fiscaleUid, tid: A.id, jti: null }));
    expect(r).toEqual({ ok: false, reason: "MISSING_JTI" });
  });

  it("jti curto demais e recusado", async () => {
    const r = await exchange(assinarToken(par, { sub: A.fiscaleUid, tid: A.id, jti: "abc" }));
    expect(r).toEqual({ ok: false, reason: "MISSING_JTI" });
  });
});

describe("pessoa, vinculo e tenant", () => {
  it("pessoa inexistente e recusada", async () => {
    const r = await exchange(assinarToken(par, { sub: "ninguem.aqui", tid: A.id }));
    expect(r).toEqual({ ok: false, reason: "IDENTITY_NOT_FOUND" });
  });

  it("tenant inexistente e recusado", async () => {
    const r = await exchange(
      assinarToken(par, { sub: A.fiscaleUid, tid: "00000000-0000-4000-8000-000000000000" }),
    );
    expect(r).toEqual({ ok: false, reason: "TENANT_NOT_FOUND" });
  });

  it("tid que nem e uuid e recusado", async () => {
    const r = await exchange(assinarToken(par, { sub: A.fiscaleUid, tid: "nao-uuid" }));
    expect(r).toEqual({ ok: false, reason: "TENANT_NOT_FOUND" });
  });

  it("pessoa de A pedindo o tenant B e recusada — nao ha vinculo", async () => {
    // O token e legitimo e assinado; o que falta e a associacao. E a
    // politica de RLS que esconde a identidade de A do tenant B.
    const r = await exchange(assinarToken(par, { sub: A.fiscaleUid, tid: B.id }));
    expect(r).toEqual({ ok: false, reason: "IDENTITY_NOT_FOUND" });
  });

  it("vinculo desativado e recusado", async () => {
    const C = await criarTenantComPessoa("xc");
    try {
      await desativarMembership(C.id, C.membership.membershipId);
      const r = await exchange(assinarToken(par, { sub: C.fiscaleUid, tid: C.id }));
      expect(r).toEqual({ ok: false, reason: "MEMBERSHIP_INACTIVE" });
    } finally {
      await removerTenant(C.id);
    }
  });

  it("pessoa desativada e recusada", async () => {
    const D = await criarTenantComPessoa("xd");
    try {
      await desativarIdentidade(D.id, D.membership.identityId);
      const r = await exchange(assinarToken(par, { sub: D.fiscaleUid, tid: D.id }));
      expect(r).toEqual({ ok: false, reason: "IDENTITY_INACTIVE" });
    } finally {
      await removerTenant(D.id);
    }
  });

  it("tenant desativado e recusado", async () => {
    const E = await criarTenantComPessoa("xe");
    try {
      await desativarTenant(E.id);
      const r = await exchange(assinarToken(par, { sub: E.fiscaleUid, tid: E.id }));
      expect(r).toEqual({ ok: false, reason: "TENANT_INACTIVE" });
    } finally {
      await removerTenant(E.id);
    }
  });
});

describe("rotacao de chave", () => {
  it("com as duas chaves configuradas, tokens das duas passam", async () => {
    const antiga = par;
    const nova = gerarPar("teste-2");
    instalarChaves(antiga, nova);
    try {
      expect((await exchange(assinarToken(antiga, { sub: A.fiscaleUid, tid: A.id }))).ok).toBe(true);
      expect((await exchange(assinarToken(nova, { sub: A.fiscaleUid, tid: A.id }))).ok).toBe(true);
    } finally {
      instalarChaves(par);
    }
  });

  it("removida a antiga, o token dela para de valer", async () => {
    const nova = gerarPar("teste-3");
    const tokenAntigo = assinarToken(par, { sub: A.fiscaleUid, tid: A.id });
    instalarChaves(nova);
    try {
      expect(await exchange(tokenAntigo)).toEqual({ ok: false, reason: "UNKNOWN_KID" });
    } finally {
      instalarChaves(par);
    }
  });
});
