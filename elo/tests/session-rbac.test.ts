/**
 * Sessao, RBAC e departamentos.
 *
 * A pergunta central: uma sessao legitima do Tenant A consegue, de algum
 * jeito, virar acesso ao Tenant B? Nem trocando o cookie, nem por id
 * conhecido, nem pela tabela global de identidades.
 */
import { randomUUID } from "node:crypto";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { exchange } from "@/server/auth/exchange";
import { can, requirePermission, type AuthContext } from "@/server/auth/context";
import { permissionsOf } from "@/server/auth/permissions";
import {
  hashToken,
  resolveSession,
  revokeOtherSessions,
  revokeSession,
} from "@/server/auth/session";
import { createDepartment, provisionMembership } from "@/server/admin/provisioning";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import {
  assinarToken,
  criarTenantComPessoa,
  desativarMembership,
  gerarPar,
  instalarChaves,
  removerTenant,
  type ParDeChaves,
  type TenantFixture,
} from "./auth-helpers";
import { conexaoApp, fecharPrisma } from "./helpers";

let par: ParDeChaves;
let A: TenantFixture;
let B: TenantFixture;

async function entrar(t: TenantFixture): Promise<string> {
  const r = await exchange(assinarToken(par, { sub: t.fiscaleUid, tid: t.id }));
  if (!r.ok) throw new Error(`troca falhou: ${r.reason}`);
  return r.session.cookieValue;
}

async function contexto(t: TenantFixture): Promise<AuthContext> {
  const s = await resolveSession(await entrar(t));
  if (!s.ok) throw new Error(`sessao invalida: ${s.reason}`);
  return s.context;
}

beforeAll(async () => {
  par = gerarPar("sess-1");
  instalarChaves(par);
  A = await criarTenantComPessoa("sa2", "OWNER" as Role);
  B = await criarTenantComPessoa("sb2", "OWNER" as Role);
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

describe("ciclo de vida da sessao", () => {
  it("cookie valido resolve para o contexto certo", async () => {
    const ctx = await contexto(A);
    expect(ctx.tenantId).toBe(A.id);
    expect(ctx.membershipId).toBe(A.membership.membershipId);
    expect(ctx.role).toBe("OWNER");
  });

  it("cookie ausente ou sem forma de cookie e recusado", async () => {
    expect(await resolveSession(undefined)).toEqual({ ok: false, reason: "NO_COOKIE" });
    expect(await resolveSession("")).toEqual({ ok: false, reason: "NO_COOKIE" });
    expect(await resolveSession("semponto")).toEqual({ ok: false, reason: "MALFORMED_COOKIE" });
    expect(await resolveSession(".semtenant")).toEqual({ ok: false, reason: "MALFORMED_COOKIE" });
    expect(await resolveSession("nao-uuid.token")).toEqual({
      ok: false,
      reason: "MALFORMED_COOKIE",
    });
  });

  it("token inventado com tenant real e recusado", async () => {
    const r = await resolveSession(`${A.id}.${randomUUID()}${randomUUID()}`);
    expect(r).toEqual({ ok: false, reason: "NOT_FOUND" });
  });

  it("sessao revogada para de valer na hora", async () => {
    const cookie = await entrar(A);
    const antes = await resolveSession(cookie);
    expect(antes.ok).toBe(true);
    if (!antes.ok) return;

    await revokeSession(antes.context);

    expect(await resolveSession(cookie)).toEqual({ ok: false, reason: "REVOKED" });
  });

  it("sessao expirada e recusada", async () => {
    const cookie = await entrar(A);
    const s = await resolveSession(cookie);
    expect(s.ok).toBe(true);
    if (!s.ok) return;

    await withTenant(A.id, async (tx) => {
      await tx.session.update({
        where: { id: s.context.sessionId },
        data: { expiresAt: new Date(Date.now() - 1000) },
      });
    });

    expect(await resolveSession(cookie)).toEqual({ ok: false, reason: "EXPIRED" });
  });

  it("desativar o vinculo derruba a sessao existente", async () => {
    const C = await criarTenantComPessoa("sc2");
    try {
      const cookie = await entrar(C);
      expect((await resolveSession(cookie)).ok).toBe(true);

      await desativarMembership(C.id, C.membership.membershipId);

      expect(await resolveSession(cookie)).toEqual({
        ok: false,
        reason: "MEMBERSHIP_INACTIVE",
      });
    } finally {
      await removerTenant(C.id);
    }
  });

  it("o banco guarda o hash do token, nunca o token", async () => {
    const cookie = await entrar(A);
    const token = cookie.slice(cookie.indexOf(".") + 1);

    const linhas = await withTenant(A.id, async (tx) =>
      tx.session.findMany({ select: { tokenHash: true } }),
    );

    expect(linhas.some((l) => l.tokenHash === hashToken(token))).toBe(true);
    expect(linhas.some((l) => l.tokenHash === token)).toBe(false);
  });

  it("encerrar as outras sessoes mantem a atual", async () => {
    const D = await criarTenantComPessoa("sd2");
    try {
      const c1 = await entrar(D);
      const c2 = await entrar(D);
      const c3 = await entrar(D);

      const atual = await resolveSession(c3);
      expect(atual.ok).toBe(true);
      if (!atual.ok) return;

      const encerradas = await revokeOtherSessions(atual.context);
      expect(encerradas).toBe(2);

      expect((await resolveSession(c3)).ok).toBe(true);
      expect(await resolveSession(c1)).toEqual({ ok: false, reason: "REVOKED" });
      expect(await resolveSession(c2)).toEqual({ ok: false, reason: "REVOKED" });
    } finally {
      await removerTenant(D.id);
    }
  });
});

describe("sessao nao atravessa tenant", () => {
  it("trocar o tenant no cookie nao da acesso ao outro — da 401", async () => {
    // Este e o teste que sustenta a decisao de carregar o tenant no
    // cookie: ele e chave de busca, nao autoridade. O par (tenant, hash)
    // precisa existir; adulterar so a etiqueta nao abre gaveta nenhuma.
    const cookie = await entrar(A);
    const token = cookie.slice(cookie.indexOf(".") + 1);

    const forjado = `${B.id}.${token}`;
    expect(await resolveSession(forjado)).toEqual({ ok: false, reason: "NOT_FOUND" });
  });

  it("a sessao de A nunca aparece na listagem de B", async () => {
    await entrar(A);
    const ctxB = await contexto(B);

    const sessoesVistasPorB = await withTenant(ctxB.tenantId, async (tx) =>
      tx.session.findMany({ select: { tenantId: true } }),
    );
    expect(sessoesVistasPorB.every((s) => s.tenantId === B.id)).toBe(true);
  });

  it("B nao alcanca a sessao de A nem pelo id conhecido", async () => {
    const ctxA = await contexto(A);
    const achado = await withTenant(B.id, async (tx) =>
      tx.session.findUnique({ where: { id: ctxA.sessionId } }),
    );
    expect(achado).toBeNull();
  });

  it("B nao alcanca o membership de A pelo id conhecido", async () => {
    const achado = await withTenant(B.id, async (tx) =>
      tx.membership.findUnique({ where: { id: A.membership.membershipId } }),
    );
    expect(achado).toBeNull();
  });

  it("a identidade de A e invisivel para B, mesmo sendo tabela global", async () => {
    const porId = await withTenant(B.id, async (tx) =>
      tx.identity.findUnique({ where: { id: A.membership.identityId } }),
    );
    expect(porId).toBeNull();

    // Nem pelo e-mail, que e unico no sistema inteiro.
    const porEmail = await withTenant(B.id, async (tx) =>
      tx.identity.findUnique({ where: { email: A.email } }),
    );
    expect(porEmail).toBeNull();

    // E a propria identidade de B continua visivel — controle negativo.
    const propria = await withTenant(B.id, async (tx) =>
      tx.identity.findUnique({ where: { id: B.membership.identityId } }),
    );
    expect(propria?.email).toBe(B.email);
  });

  it("o provedor de identidade de A e invisivel para B", async () => {
    const achado = await withTenant(B.id, async (tx) =>
      tx.identityProvider.findFirst({
        where: { provider: "fiscale", providerUid: A.fiscaleUid },
      }),
    );
    expect(achado).toBeNull();
  });

  it("nem em SQL cru, sem Prisma", async () => {
    const db = await conexaoApp();
    try {
      await db.query("BEGIN");
      await db.query("SELECT set_config('app.tenant_id', $1, true)", [B.id]);
      const identidades = await db.query("SELECT id FROM identities");
      const sessoes = await db.query("SELECT id, tenant_id FROM sessions");
      await db.query("COMMIT");

      expect(identidades.rows.map((r) => r.id)).not.toContain(A.membership.identityId);
      expect(sessoes.rows.every((r) => r.tenant_id === B.id)).toBe(true);
    } finally {
      await db.end();
    }
  });
});

describe("RBAC", () => {
  it("a tabela de permissoes e a mesma para todo mundo", () => {
    expect(permissionsOf("OWNER" as Role)).toContain("tenant.manage");
    expect(permissionsOf("ADMIN" as Role)).not.toContain("tenant.manage");
    // VIEWER so olha: le atendimento, mensagem, nota, etiqueta e quem
    // visualizou — e nao age em nada. Ler recibo e leitura; enviar nao.
    //
    // `notifications.manage_self` (MVP 1.7) entrou e NAO contradiz a
    // regra: configurar o PROPRIO aviso nao age sobre nada do escritorio.
    // Um VIEWER sem ela nao conseguiria nem desligar o som que ele ouve.
    expect(permissionsOf("VIEWER" as Role)).toEqual([
      "departments.read",
      "conversations.read",
      "notes.read",
      "tags.read",
      "messages.read",
      "messages.view_receipts",
      "notifications.manage_self",
    ]);
    expect(permissionsOf("VIEWER" as Role)).not.toContain("conversations.assign");
    expect(permissionsOf("VIEWER" as Role)).not.toContain("messages.send");

    // Todo papel configura os proprios avisos. Nao existe papel que
    // receba notificacao sem poder desliga-la.
    for (const papel of ["OWNER", "ADMIN", "MANAGER", "AGENT", "VIEWER"]) {
      expect(permissionsOf(papel as Role)).toContain("notifications.manage_self");
    }
  });

  it("permite a acao valida", async () => {
    const ctx = await contexto(A); // OWNER
    expect(can(ctx, "departments.manage")).toBe(true);
    expect(() => requirePermission(ctx, "departments.manage")).not.toThrow();
  });

  it("bloqueia a acao indevida", async () => {
    const E = await criarTenantComPessoa("se2", "VIEWER" as Role);
    try {
      const ctx = await contexto(E);
      expect(can(ctx, "departments.read")).toBe(true);
      expect(can(ctx, "departments.manage")).toBe(false);
      expect(can(ctx, "users.manage")).toBe(false);
      expect(() => requirePermission(ctx, "departments.manage")).toThrow();
    } finally {
      await removerTenant(E.id);
    }
  });

  it("o erro de permissao nao conta ao cliente o que faltou", async () => {
    const E = await criarTenantComPessoa("sf2", "AGENT" as Role);
    try {
      const ctx = await contexto(E);
      try {
        requirePermission(ctx, "audit.read");
        throw new Error("deveria ter barrado");
      } catch (erro) {
        const e = erro as { publicMessage?: string; message: string; status?: number };
        expect(e.status).toBe(403);
        expect(e.publicMessage).toBe("Acesso negado.");
        // O detalhe existe, mas so do lado de dentro.
        expect(e.message).toContain("audit.read");
      }
    } finally {
      await removerTenant(E.id);
    }
  });

  it("AGENT le mas nao administra", async () => {
    const F = await criarTenantComPessoa("sg2", "AGENT" as Role);
    try {
      const ctx = await contexto(F);
      expect(can(ctx, "users.read")).toBe(true);
      expect(can(ctx, "users.manage")).toBe(false);
      expect(can(ctx, "sessions.manage")).toBe(false);
    } finally {
      await removerTenant(F.id);
    }
  });
});

describe("departamentos", () => {
  it("sao dados por tenant, nao constantes do codigo", async () => {
    await createDepartment({ tenantId: A.id, slug: "fiscal", name: "Fiscal" });
    await createDepartment({ tenantId: A.id, slug: "dp", name: "Departamento Pessoal" });
    // O mesmo slug em outro tenant e outro departamento, e nao conflito.
    await createDepartment({ tenantId: B.id, slug: "fiscal", name: "Fiscal do B" });

    const deA = await withTenant(A.id, async (tx) =>
      tx.department.findMany({ select: { slug: true } }),
    );
    const deB = await withTenant(B.id, async (tx) =>
      tx.department.findMany({ select: { slug: true, name: true } }),
    );

    expect(deA.map((d) => d.slug).sort()).toEqual(["dp", "fiscal"]);
    expect(deB).toHaveLength(1);
    expect(deB[0]?.name).toBe("Fiscal do B");
  });

  it("o departamento de A e inacessivel a B, mesmo pelo id", async () => {
    const dep = await createDepartment({ tenantId: A.id, slug: "contabil", name: "Contabil" });

    const porB = await withTenant(B.id, async (tx) =>
      tx.department.findUnique({ where: { id: dep.id } }),
    );
    expect(porB).toBeNull();
  });

  it("uma pessoa pode estar em mais de um departamento", async () => {
    const G = await criarTenantComPessoa("sh2");
    try {
      const fiscal = await createDepartment({ tenantId: G.id, slug: "fiscal", name: "Fiscal" });
      const dp = await createDepartment({ tenantId: G.id, slug: "dp", name: "DP" });

      await withTenant(G.id, async (tx) => {
        await tx.departmentMembership.createMany({
          data: [
            { tenantId: G.id, departmentId: fiscal.id, membershipId: G.membership.membershipId },
            { tenantId: G.id, departmentId: dp.id, membershipId: G.membership.membershipId },
          ],
        });
      });

      const meus = await withTenant(G.id, async (tx) =>
        tx.departmentMembership.findMany({
          where: { membershipId: G.membership.membershipId },
          select: { department: { select: { slug: true } } },
        }),
      );
      expect(meus.map((m) => m.department.slug).sort()).toEqual(["dp", "fiscal"]);
    } finally {
      await removerTenant(G.id);
    }
  });
});

describe("uma pessoa em dois tenants", () => {
  it("mesma identidade, dois vinculos, papeis diferentes e nenhum vazamento", async () => {
    // A prova de que a modelagem Identity/Membership resolve o que o
    // `User` com tenantId impedia.
    const H = await criarTenantComPessoa("si2", "ADMIN" as Role);
    const I = await criarTenantComPessoa("sj2", "OWNER" as Role);
    try {
      const segundo = await provisionMembership({
        tenantId: I.id,
        role: "VIEWER" as Role,
        identityId: H.membership.identityId,
      });

      expect(segundo.identityId).toBe(H.membership.identityId);
      expect(segundo.membershipId).not.toBe(H.membership.membershipId);

      // O token do Fiscale e o mesmo `sub`; o que muda e o `tid`.
      const emH = await exchange(assinarToken(par, { sub: H.fiscaleUid, tid: H.id }));
      const emI = await exchange(assinarToken(par, { sub: H.fiscaleUid, tid: I.id }));

      expect(emH.ok).toBe(true);
      expect(emI.ok).toBe(true);
      if (!emH.ok || !emI.ok) return;

      const ctxH = await resolveSession(emH.session.cookieValue);
      const ctxI = await resolveSession(emI.session.cookieValue);
      expect(ctxH.ok && ctxH.context.role).toBe("ADMIN");
      expect(ctxI.ok && ctxI.context.role).toBe("VIEWER");

      // Duas sessoes vivas ao mesmo tempo, cada uma no seu escritorio, e
      // nenhuma enxergando a outra.
      if (!ctxI.ok) return;
      const sessoesEmI = await withTenant(I.id, async (tx) =>
        tx.session.findMany({ select: { tenantId: true } }),
      );
      expect(sessoesEmI.every((s) => s.tenantId === I.id)).toBe(true);
    } finally {
      await removerTenant(H.id);
      await removerTenant(I.id);
    }
  });
});
