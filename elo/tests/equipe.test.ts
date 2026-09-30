/**
 * Admin > Equipe — serviço e rotas.
 *
 * Os testes que carregam o arquivo — os que precisam FALHAR quando o
 * defeito correspondente entrar:
 *
 *   - quem não tem `users.manage` recebe 403, e o item de menu escondido
 *     não é o que segura isso;
 *   - criar pessoa gera Identity + Membership + IdentityProvider, e o
 *     login do FISCALE é gravado como login, não como e-mail;
 *   - ninguém tira de si o acesso administrativo na mesma operação;
 *   - área principal fora da seleção é recusada;
 *   - salvar áreas é um CONJUNTO: o que saiu, sai mesmo;
 *   - a cobertura vem de `filaElegivel`, e não de contar vínculo — pessoa
 *     indisponível conta como vínculo e NÃO conta como cobertura.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { provisionMembership } from "@/server/admin/provisioning";
import { exchange } from "@/server/auth/exchange";
import { withTenant } from "@/server/tenancy";
import {
  coberturaDosSetores,
  criarPessoa,
  definirDepartamentos,
  editarPessoa,
  listarEquipe,
} from "@/server/team/service";
import type { AuthContext } from "@/server/auth/context";
import type { Role } from "@/generated/prisma/enums";

import {
  criarDepartamentoTeste,
  criarTenantComPessoa,
  gerarPar,
  instalarChaves,
  assinarToken,
  removerTenant,
  type ParDeChaves,
  type TenantFixture,
} from "./auth-helpers";
import { fecharPrisma } from "./helpers";

let par: ParDeChaves;
let A: TenantFixture;
let carlos: { membershipId: string; uid: string };
const areas: Record<string, string> = {};

let cookieDono: string;
let cookieAgente: string;

async function entrar(t: TenantFixture, uid = t.fiscaleUid): Promise<string> {
  const r = await exchange(assinarToken(par, { sub: uid, tid: t.id }));
  if (!r.ok) throw new Error(`troca falhou: ${r.reason}`);
  return r.session.cookieValue;
}

function req(caminho: string, cookie: string | null, init: RequestInit = {}): NextRequest {
  return new NextRequest(
    new Request(`http://localhost${caminho}`, {
      ...init,
      headers: {
        ...(init.headers as Record<string, string> | undefined),
        ...(cookie ? { cookie: `elo_sessao=${cookie}` } : {}),
      },
    }),
  );
}

function corpo(metodo: string, dados: unknown): RequestInit {
  return {
    method: metodo,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(dados),
  };
}

/** Contexto sintético do dono. O caminho do cookie tem testes próprios. */
function ctxDono(): AuthContext {
  return {
    sessionId: "00000000-0000-4000-8000-000000000001",
    tenantId: A.id,
    membershipId: A.membership.membershipId,
    identityId: A.membership.identityId,
    email: A.email,
    name: "Dona do escritório",
    role: "OWNER" as Role,
  };
}

let seq = 0;
function novoEmail(): string {
  seq += 1;
  return `pessoa${seq}.${A.slug}@equipe.teste`;
}

beforeAll(async () => {
  par = gerarPar();
  instalarChaves(par);

  A = await criarTenantComPessoa("eqp", "OWNER" as Role);

  for (const [slug, nome] of [
    ["fiscal", "Fiscal"],
    ["contabil", "Contábil"],
    ["dp", "DP"],
  ]) {
    const d = await criarDepartamentoTeste(A.id, slug!, nome!);
    areas[slug!] = d.id;
  }

  const c = await provisionMembership({
    tenantId: A.id,
    role: "AGENT" as Role,
    email: `carlos.${A.slug}@equipe.teste`,
    name: "Carlos Souza",
    fiscaleUid: `carlos.${A.slug}`,
  });
  carlos = { membershipId: c.membershipId, uid: `carlos.${A.slug}` };

  cookieDono = await entrar(A);
  cookieAgente = await entrar(A, carlos.uid);
});

afterAll(async () => {
  await removerTenant(A.id);
  await fecharPrisma();
});

/* ══ 1. permissões nas rotas ════════════════════════════════════════ */

describe("as rotas cobram permissão", () => {
  it("users.read: o AGENT lista", async () => {
    const { GET } = await import("@/app/api/team/route");
    const r = await GET(req("/api/team", cookieAgente));
    expect(r.status).toBe(200);
  });

  it("sem sessão nenhuma, 401", async () => {
    const { GET } = await import("@/app/api/team/route");
    expect((await GET(req("/api/team", null))).status).toBe(401);
  });

  it("users.manage: o AGENT recebe 403 ao criar", async () => {
    // O ponto: o item some do menu dele, e isso não é o que segura. Quem
    // chama a rota direto leva 403 do mesmo jeito.
    const { POST } = await import("@/app/api/team/route");
    const r = await POST(
      req(
        "/api/team",
        cookieAgente,
        corpo("POST", {
          name: "Tentativa",
          email: novoEmail(),
          fiscaleUid: "tentativa",
          role: "AGENT",
        }),
      ),
    );
    expect(r.status).toBe(403);
  });

  it("users.manage: o AGENT recebe 403 ao editar e ao mexer em áreas", async () => {
    const { PATCH } = await import("@/app/api/team/[id]/route");
    const p = Promise.resolve({ id: carlos.membershipId });
    expect(
      (await PATCH(req("/api/team/x", cookieAgente, corpo("PATCH", { role: "OWNER" })), { params: p }))
        .status,
    ).toBe(403);

    const { PUT } = await import("@/app/api/team/[id]/departments/route");
    expect(
      (
        await PUT(
          req("/api/team/x/departments", cookieAgente, corpo("PUT", { departmentIds: [] })),
          { params: Promise.resolve({ id: carlos.membershipId }) },
        )
      ).status,
    ).toBe(403);
  });

  it("o dono cria pela rota, e sai 201", async () => {
    const { POST } = await import("@/app/api/team/route");
    const r = await POST(
      req(
        "/api/team",
        cookieDono,
        corpo("POST", {
          name: "Pela Rota",
          email: novoEmail(),
          fiscaleUid: `rota.${A.slug}`,
          role: "AGENT",
        }),
      ),
    );
    expect(r.status).toBe(201);
  });
});

/* ══ 2. criação ═════════════════════════════════════════════════════ */

describe("criar pessoa", () => {
  it("gera Identity + Membership + IdentityProvider, com o login do FISCALE", async () => {
    const email = novoEmail();
    const uid = `joana.${A.slug}`;

    const p = await criarPessoa(ctxDono(), {
      name: "Joana Ribeiro",
      email: email.toUpperCase(), // normalização é da aplicação
      fiscaleUid: uid.toUpperCase(),
      role: "AGENT" as Role,
    });

    expect(p.email).toBe(email);
    // O campo mais perigoso da tela: se virar o e-mail, a pessoa é criada,
    // parece certa e NÃO consegue entrar.
    expect(p.fiscaleUid).toBe(uid);
    expect(p.fiscaleUid).not.toBe(email);

    const conferido = await withTenant(A.id, async (tx) => ({
      identidade: await tx.identity.findUnique({ where: { id: p.identityId } }),
      provedor: await tx.identityProvider.findFirst({
        where: { identityId: p.identityId, provider: "fiscale" },
        select: { providerUid: true },
      }),
      vinculo: await tx.membership.findUnique({ where: { id: p.membershipId } }),
    }));

    expect(conferido.identidade?.email).toBe(email);
    expect(conferido.provedor?.providerUid).toBe(uid);
    expect(conferido.vinculo?.role).toBe("AGENT");
  });

  it("os padrões: ativo, assume, e NÃO visível ao cliente", async () => {
    const p = await criarPessoa(ctxDono(), {
      name: "Padrões",
      email: novoEmail(),
      fiscaleUid: `padroes.${A.slug}`,
      role: "AGENT" as Role,
    });

    expect(p.active).toBe(true);
    expect(p.availableForAssignment).toBe(true);
    // O padrão que protege: ninguém é exposto ao cliente por ter sido
    // cadastrado.
    expect(p.visibleToCustomers).toBe(false);
  });

  it("e-mail repetido dá conflito com mensagem que se entende", async () => {
    const email = novoEmail();
    await criarPessoa(ctxDono(), {
      name: "Primeira",
      email,
      fiscaleUid: `primeira.${A.slug}`,
      role: "AGENT" as Role,
    });

    await expect(
      criarPessoa(ctxDono(), {
        name: "Segunda",
        email,
        fiscaleUid: `segunda.${A.slug}`,
        role: "AGENT" as Role,
      }),
    ).rejects.toMatchObject({ kind: "CONFLICT" });
  });

  it("login do FISCALE repetido também dá conflito", async () => {
    const uid = `repetido.${A.slug}`;
    await criarPessoa(ctxDono(), {
      name: "Dono do login",
      email: novoEmail(),
      fiscaleUid: uid,
      role: "AGENT" as Role,
    });

    await expect(
      criarPessoa(ctxDono(), {
        name: "Outro",
        email: novoEmail(),
        fiscaleUid: uid,
        role: "AGENT" as Role,
      }),
    ).rejects.toMatchObject({ kind: "CONFLICT" });
  });

  it("nasce em VÁRIAS áreas, com principal entre elas", async () => {
    const p = await criarPessoa(ctxDono(), {
      name: "Em três áreas",
      email: novoEmail(),
      fiscaleUid: `tres.${A.slug}`,
      role: "MANAGER" as Role,
      departmentIds: [areas.fiscal!, areas.contabil!, areas.dp!],
      primaryDepartmentId: areas.contabil!,
    });

    expect(p.departments.map((d) => d.slug).sort()).toEqual(["contabil", "dp", "fiscal"]);
    expect(p.primaryDepartmentId).toBe(areas.contabil);
  });

  it("principal FORA da seleção é recusado", async () => {
    await expect(
      criarPessoa(ctxDono(), {
        name: "Principal solta",
        email: novoEmail(),
        fiscaleUid: `solta.${A.slug}`,
        role: "AGENT" as Role,
        departmentIds: [areas.fiscal!],
        primaryDepartmentId: areas.dp!,
      }),
    ).rejects.toMatchObject({ kind: "VALIDATION" });
  });
});

/* ══ 3. edição ══════════════════════════════════════════════════════ */

describe("editar pessoa", () => {
  async function alguem(papel: Role = "AGENT" as Role) {
    return criarPessoa(ctxDono(), {
      name: "Alguém",
      email: novoEmail(),
      fiscaleUid: `alguem${seq}.${A.slug}`,
      role: papel,
    });
  }

  it("muda papel, situação, disponibilidade e visibilidade", async () => {
    const p = await alguem();

    const r = await editarPessoa(ctxDono(), p.membershipId, {
      role: "MANAGER" as Role,
      active: false,
      availableForAssignment: false,
      visibleToCustomers: true,
    });

    expect(r.role).toBe("MANAGER");
    expect(r.active).toBe(false);
    expect(r.availableForAssignment).toBe(false);
    expect(r.visibleToCustomers).toBe(true);
  });

  it("RECUSA tirar de si o acesso administrativo", async () => {
    // Um OWNER que se rebaixa a VIEWER tranca o escritório por fora, e o
    // conserto vira SQL na mão.
    await expect(
      editarPessoa(ctxDono(), A.membership.membershipId, { role: "VIEWER" as Role }),
    ).rejects.toMatchObject({ kind: "VALIDATION" });

    await expect(
      editarPessoa(ctxDono(), A.membership.membershipId, { role: "AGENT" as Role }),
    ).rejects.toMatchObject({ kind: "VALIDATION" });
  });

  it("RECUSA desativar a si mesmo", async () => {
    await expect(
      editarPessoa(ctxDono(), A.membership.membershipId, { active: false }),
    ).rejects.toMatchObject({ kind: "VALIDATION" });
  });

  it("mas aceita trocar o PRÓPRIO papel por outro que ainda administra", async () => {
    const eu = await listarEquipe(ctxDono());
    const antes = eu.find((p) => p.membershipId === A.membership.membershipId)!.role;

    const r = await editarPessoa(ctxDono(), A.membership.membershipId, { role: "ADMIN" as Role });
    expect(r.role).toBe("ADMIN");

    await editarPessoa(ctxDono(), A.membership.membershipId, { role: antes });
  });

  it("pessoa inexistente dá NOT_FOUND", async () => {
    await expect(
      editarPessoa(ctxDono(), "00000000-0000-4000-8000-0000000000bb", { active: true }),
    ).rejects.toMatchObject({ kind: "NOT_FOUND" });
  });
});

/* ══ 4. áreas como CONJUNTO ═════════════════════════════════════════ */

describe("áreas da pessoa", () => {
  it("salvar substitui: o que saiu da seleção é REMOVIDO", async () => {
    const p = await criarPessoa(ctxDono(), {
      name: "Vai mudar de área",
      email: novoEmail(),
      fiscaleUid: `muda.${A.slug}`,
      role: "AGENT" as Role,
      departmentIds: [areas.fiscal!, areas.contabil!],
      primaryDepartmentId: areas.fiscal!,
    });
    expect(p.departments).toHaveLength(2);

    const r = await definirDepartamentos(ctxDono(), p.membershipId, {
      departmentIds: [areas.dp!],
      primaryDepartmentId: areas.dp!,
    });

    // Até aqui desvincular dependia de SQL na mão: `assignDepartment` só
    // sabe criar.
    expect(r.departments.map((d) => d.slug)).toEqual(["dp"]);
    expect(r.primaryDepartmentId).toBe(areas.dp);

    const sobrou = await withTenant(A.id, async (tx) =>
      tx.departmentMembership.count({ where: { membershipId: p.membershipId } }),
    );
    expect(sobrou).toBe(1);
  });

  it("esvaziar a seleção tira de todas, e o principal cai junto", async () => {
    const p = await criarPessoa(ctxDono(), {
      name: "Vai ficar sem área",
      email: novoEmail(),
      fiscaleUid: `semarea.${A.slug}`,
      role: "AGENT" as Role,
      departmentIds: [areas.fiscal!],
      primaryDepartmentId: areas.fiscal!,
    });

    const r = await definirDepartamentos(ctxDono(), p.membershipId, { departmentIds: [] });
    expect(r.departments).toEqual([]);
    // Principal sem vínculo seria assinatura de uma área a que não se
    // pertence mais.
    expect(r.primaryDepartmentId).toBeNull();
  });

  it("principal fora da seleção é recusado também aqui", async () => {
    const p = await criarPessoa(ctxDono(), {
      name: "Principal invalida",
      email: novoEmail(),
      fiscaleUid: `pinv.${A.slug}`,
      role: "AGENT" as Role,
    });

    await expect(
      definirDepartamentos(ctxDono(), p.membershipId, {
        departmentIds: [areas.fiscal!],
        primaryDepartmentId: areas.dp!,
      }),
    ).rejects.toMatchObject({ kind: "VALIDATION" });
  });

  it("salvar o MESMO conjunto duas vezes não duplica", async () => {
    const p = await criarPessoa(ctxDono(), {
      name: "Idempotente",
      email: novoEmail(),
      fiscaleUid: `idem.${A.slug}`,
      role: "AGENT" as Role,
    });

    const conjunto = { departmentIds: [areas.fiscal!, areas.dp!] };
    await definirDepartamentos(ctxDono(), p.membershipId, conjunto);
    const r = await definirDepartamentos(ctxDono(), p.membershipId, conjunto);

    expect(r.departments).toHaveLength(2);
  });

  it("área de outro escritório não existe aqui", async () => {
    const outro = await criarTenantComPessoa("eqp2", "OWNER" as Role);
    try {
      const alheia = await criarDepartamentoTeste(outro.id, "fiscal", "Fiscal");
      const p = await criarPessoa(ctxDono(), {
        name: "Nao vai",
        email: novoEmail(),
        fiscaleUid: `naovai.${A.slug}`,
        role: "AGENT" as Role,
      });

      await expect(
        definirDepartamentos(ctxDono(), p.membershipId, { departmentIds: [alheia.id] }),
      ).rejects.toMatchObject({ kind: "VALIDATION" });
    } finally {
      await removerTenant(outro.id);
    }
  });
});

/* ══ 5. cobertura ═══════════════════════════════════════════════════ */

describe("cobertura dos setores", () => {
  it("conta quem SERIA avisado, não quem está vinculado", async () => {
    const t = await criarTenantComPessoa("cob", "OWNER" as Role);
    try {
      const fiscal = await criarDepartamentoTeste(t.id, "fiscal", "Fiscal");
      const ctx: AuthContext = {
        sessionId: "00000000-0000-4000-8000-000000000002",
        tenantId: t.id,
        membershipId: t.membership.membershipId,
        identityId: t.membership.identityId,
        email: t.email,
        name: "Dona",
        role: "OWNER" as Role,
      };

      const ativa = await criarPessoa(ctx, {
        name: "Trabalha",
        email: `ativa.${t.slug}@equipe.teste`,
        fiscaleUid: `ativa.${t.slug}`,
        role: "AGENT" as Role,
        departmentIds: [fiscal.id],
      });

      const ferias = await criarPessoa(ctx, {
        name: "De férias",
        email: `ferias.${t.slug}@equipe.teste`,
        fiscaleUid: `ferias.${t.slug}`,
        role: "AGENT" as Role,
        availableForAssignment: false,
        departmentIds: [fiscal.id],
      });

      const cobertura = await coberturaDosSetores(ctx);
      const c = cobertura.find((x) => x.slug === "fiscal")!;

      // Duas pessoas no setor; UMA seria avisada. É essa diferença que a
      // contagem crua esconderia.
      expect(c.vinculados).toBe(2);
      expect(c.elegiveis).toBe(1);

      expect(ativa.membershipId).not.toBe(ferias.membershipId);
    } finally {
      await removerTenant(t.id);
    }
  });

  it("um MANAGER nas TRÊS áreas aparece nas três coberturas", async () => {
    const t = await criarTenantComPessoa("cob2", "OWNER" as Role);
    try {
      const ctx: AuthContext = {
        sessionId: "00000000-0000-4000-8000-000000000003",
        tenantId: t.id,
        membershipId: t.membership.membershipId,
        identityId: t.membership.identityId,
        email: t.email,
        name: "Dona",
        role: "OWNER" as Role,
      };

      const tres: string[] = [];
      for (const [slug, nome] of [
        ["fiscal", "Fiscal"],
        ["contabil", "Contábil"],
        ["dp", "DP"],
      ]) {
        tres.push((await criarDepartamentoTeste(t.id, slug!, nome!)).id);
      }

      await criarPessoa(ctx, {
        name: "Coordenação",
        email: `gestor.${t.slug}@equipe.teste`,
        fiscaleUid: `gestor.${t.slug}`,
        role: "MANAGER" as Role,
        departmentIds: tres,
      });

      const cobertura = await coberturaDosSetores(ctx);
      expect(cobertura).toHaveLength(3);
      for (const c of cobertura) {
        // Ele aparece nas três áreas porque PERTENCE às três, e por
        // nenhum outro motivo. Mas conta como COORDENAÇÃO, não como
        // atendente: quem é MANAGER não recebe o aviso imediato, então
        // somá-lo aos elegíveis faria a tela prometer um aviso que não
        // vai sair.
        expect(c.elegiveis).toBe(0);
        expect(c.gestores).toBe(1);
        expect(c.vinculados).toBe(1);
      }
    } finally {
      await removerTenant(t.id);
    }
  });
});
