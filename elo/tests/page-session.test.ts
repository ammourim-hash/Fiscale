/**
 * Sessão nas páginas (Server Components).
 *
 * As páginas usam `pageSession()` em vez do `requireAuth(req)` das rotas,
 * porque num Server Component não existe `NextRequest`. Este arquivo
 * garante que os dois caminhos recusam pelas mesmas razões — seria fácil
 * o novo ficar mais frouxo sem ninguém notar.
 */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { exchange } from "@/server/auth/exchange";
import { revokeSession } from "@/server/auth/session";
import { resolveSession } from "@/server/auth/session";
import { createDepartment } from "@/server/admin/provisioning";
import { withTenant } from "@/server/tenancy";

import {
  assinarToken,
  criarTenantComPessoa,
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

/** Troca o cookie que `next/headers` devolveria. */
function comCookie(valor: string | undefined) {
  vi.doMock("next/headers", () => ({
    cookies: async () => ({ get: (n: string) => (valor && n === "elo_sessao" ? { value: valor } : undefined) }),
  }));
}

async function carregarPageSession() {
  const mod = await import("@/server/auth/page-session");
  return mod.pageSession;
}

async function entrar(t: TenantFixture): Promise<string> {
  const r = await exchange(assinarToken(par, { sub: t.fiscaleUid, tid: t.id }));
  if (!r.ok) throw new Error(`troca falhou: ${r.reason}`);
  return r.session.cookieValue;
}

beforeAll(async () => {
  par = gerarPar("pg-1");
  instalarChaves(par);
  A = await criarTenantComPessoa("ga");
  B = await criarTenantComPessoa("gb");
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

describe("acesso sem sessao", () => {
  it("sem cookie, nao ha sessao — e a pagina redireciona", async () => {
    vi.resetModules();
    comCookie(undefined);
    const pageSession = await carregarPageSession();
    expect(await pageSession()).toBeNull();
  });

  it("cookie inventado nao vira sessao", async () => {
    vi.resetModules();
    comCookie(`${A.id}.token-que-nunca-existiu-0000000000`);
    const pageSession = await carregarPageSession();
    expect(await pageSession()).toBeNull();
  });

  it("cookie malformado nao vira sessao", async () => {
    vi.resetModules();
    comCookie("sem-ponto-nenhum");
    const pageSession = await carregarPageSession();
    expect(await pageSession()).toBeNull();
  });

  it("sessao revogada deixa de valer para as paginas tambem", async () => {
    const cookie = await entrar(A);
    const s = await resolveSession(cookie);
    expect(s.ok).toBe(true);
    if (!s.ok) return;
    await revokeSession(s.context);

    vi.resetModules();
    comCookie(cookie);
    const pageSession = await carregarPageSession();
    expect(await pageSession()).toBeNull();
  });
});

describe("acesso autenticado", () => {
  it("devolve identidade, tenant, papel e permissoes", async () => {
    const cookie = await entrar(A);
    vi.resetModules();
    comCookie(cookie);
    const pageSession = await carregarPageSession();

    const sessao = await pageSession();
    expect(sessao).not.toBeNull();
    expect(sessao!.context.tenantId).toBe(A.id);
    expect(sessao!.context.email).toBe(A.email);
    expect(sessao!.permissions.length).toBeGreaterThan(0);
  });

  it("sem departamento principal escolhido, vem nulo — nao o primeiro da lista", async () => {
    const C = await criarTenantComPessoa("gc");
    try {
      const fiscal = await createDepartment({ tenantId: C.id, slug: "fiscal", name: "Fiscal" });
      await withTenant(C.id, async (tx) => {
        await tx.departmentMembership.create({
          data: {
            tenantId: C.id,
            departmentId: fiscal.id,
            membershipId: C.membership.membershipId,
          },
        });
      });

      const cookie = await entrar(C);
      vi.resetModules();
      comCookie(cookie);
      const pageSession = await carregarPageSession();

      const sessao = await pageSession();
      expect(sessao!.departments).toHaveLength(1);
      // Pertence ao Fiscal, mas ninguem disse que e o principal.
      expect(sessao!.primaryDepartment).toBeNull();
    } finally {
      await removerTenant(C.id);
    }
  });

  it("com principal escolhido, ele vem — e e o que assina a mensagem", async () => {
    const D = await criarTenantComPessoa("gd");
    try {
      const fiscal = await createDepartment({ tenantId: D.id, slug: "fiscal", name: "Fiscal" });
      const dp = await createDepartment({ tenantId: D.id, slug: "dp", name: "Departamento Pessoal" });

      await withTenant(D.id, async (tx) => {
        await tx.departmentMembership.createMany({
          data: [
            { tenantId: D.id, departmentId: fiscal.id, membershipId: D.membership.membershipId },
            { tenantId: D.id, departmentId: dp.id, membershipId: D.membership.membershipId },
          ],
        });
        await tx.membership.update({
          where: { id: D.membership.membershipId },
          data: { primaryDepartmentId: dp.id },
        });
      });

      const cookie = await entrar(D);
      vi.resetModules();
      comCookie(cookie);
      const pageSession = await carregarPageSession();

      const sessao = await pageSession();
      expect(sessao!.departments).toHaveLength(2);
      // O principal e o escolhido, nao o primeiro em ordem alfabetica.
      expect(sessao!.primaryDepartment?.name).toBe("Departamento Pessoal");
    } finally {
      await removerTenant(D.id);
    }
  });
});

describe("a pagina nao atravessa tenant", () => {
  it("o cookie de A com o tenant de B trocado nao abre nada", async () => {
    const cookie = await entrar(A);
    const token = cookie.slice(cookie.indexOf(".") + 1);

    vi.resetModules();
    comCookie(`${B.id}.${token}`);
    const pageSession = await carregarPageSession();
    expect(await pageSession()).toBeNull();
  });

  it("a sessao de B nao enxerga departamento de A", async () => {
    await createDepartment({ tenantId: A.id, slug: "so-do-a", name: "Exclusivo do A" });

    const cookie = await entrar(B);
    vi.resetModules();
    comCookie(cookie);
    const pageSession = await carregarPageSession();

    const sessao = await pageSession();
    expect(sessao!.context.tenantId).toBe(B.id);
    expect(sessao!.departments.map((d) => d.slug)).not.toContain("so-do-a");
  });
});
