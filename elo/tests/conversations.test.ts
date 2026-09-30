/**
 * O modelo de atendimento.
 *
 * Dois testes carregam o peso do arquivo: o de concorrência ao assumir e o
 * de isolamento entre tenants. O resto existe para que, quando algum deles
 * quebrar, se saiba exatamente o que mudou.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import type { AuthContext } from "@/server/auth/context";
import {
  assumirAtendimento,
  criarAtendimento,
  mudarStatus,
  registrarVisualizacao,
  transferirAtendimento,
} from "@/server/conversations/service";
import { contarPorFiltro, listarAtendimentos, obterAtendimento } from "@/server/conversations/queries";
import { criarNota, listarNotas } from "@/server/conversations/notes";
import { alternarEtiqueta, criarEtiqueta, listarEtiquetas } from "@/server/conversations/tags";
import { getCustomerVisibleAssignees, podeReceberEscolha } from "@/server/conversations/assignees";
import { provisionMembership } from "@/server/admin/provisioning";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import {
  criarDepartamentoTeste,
  criarTenantComPessoa,
  removerTenant,
  type TenantFixture,
} from "./auth-helpers";
import { fecharPrisma } from "./helpers";

let A: TenantFixture;
let B: TenantFixture;
let ctxA: AuthContext;
let ctxB: AuthContext;

/** Contexto sintético: os testes de sessão já cobrem o caminho do cookie. */
function contexto(t: TenantFixture, role: Role = "OWNER" as Role): AuthContext {
  return {
    sessionId: "00000000-0000-4000-8000-000000000001",
    tenantId: t.id,
    membershipId: t.membership.membershipId,
    identityId: t.membership.identityId,
    email: t.email,
    name: "Pessoa de Teste",
    role,
  };
}

async function clienteDe(t: TenantFixture, nome: string): Promise<string> {
  return withTenant(t.id, async (tx) => {
    const c = await tx.externalCustomerReference.create({
      data: {
        tenantId: t.id,
        source: "FISCALE",
        externalId: `ext-${nome}`,
        displayName: nome,
        sourceVersion: "v1",
        contentHash: `h-${nome}`,
        syncedAt: new Date(),
      },
      select: { id: true },
    });
    return c.id;
  });
}

beforeAll(async () => {
  A = await criarTenantComPessoa("ca", "OWNER" as Role);
  B = await criarTenantComPessoa("cb", "OWNER" as Role);
  ctxA = contexto(A);
  ctxB = contexto(B);
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

/* 1 ─ criar ─────────────────────────────────────────────────────────── */

describe("criar atendimento", () => {
  it("nasce NEW, sem responsável e com o evento CREATED", async () => {
    const cliente = await clienteDe(A, "Padaria");
    const { id } = await criarAtendimento(A.id, { customerReferenceId: cliente });

    const d = await obterAtendimento(ctxA, id);
    expect(d?.status).toBe("NEW");
    expect(d?.assignee).toBeNull();
    expect(d?.customer?.displayName).toBe("Padaria");
    expect(d?.events.map((e) => e.type)).toContain("CREATED");
  });

  it("Atendimento Geral: sem pessoa e sem área é um estado válido", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const d = await obterAtendimento(ctxA, id);
    expect(d?.assignee).toBeNull();
    expect(d?.department).toBeNull();
    expect(d?.status).toBe("NEW");
  });

  it("escolhido pelo cliente já nasce com destino, mas ainda NEW", async () => {
    // Ser escolhido não é ter sido atendido.
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: A.membership.membershipId,
    });
    const d = await obterAtendimento(ctxA, id);
    expect(d?.assignee?.membershipId).toBe(A.membership.membershipId);
    expect(d?.status).toBe("NEW");
  });

  it("fila departamental: área sem pessoa", async () => {
    const dep = await criarDepartamentoTeste(A.id, "fila-fiscal", "Fiscal");
    const { id } = await criarAtendimento(A.id, { assignedDepartmentId: dep.id });
    const d = await obterAtendimento(ctxA, id);
    expect(d?.department?.name).toBe("Fiscal");
    expect(d?.assignee).toBeNull();
  });
});

/* 2 ─ isolamento ────────────────────────────────────────────────────── */

describe("isolamento entre tenants", () => {
  it("B não enxerga, não abre, não assume, não transfere e não anota o de A", async () => {
    const { id } = await criarAtendimento(A.id, {});

    expect(await obterAtendimento(ctxB, id)).toBeNull();
    expect((await listarAtendimentos(ctxB, "todos")).map((c) => c.id)).not.toContain(id);

    expect(await registrarVisualizacao(ctxB, id)).toMatchObject({ ok: false, code: "NOT_FOUND" });
    expect(await assumirAtendimento(ctxB, id)).toMatchObject({ ok: false, code: "NOT_FOUND" });
    expect(
      await transferirAtendimento(ctxB, id, {
        expectedVersion: 0,
        toMembershipId: B.membership.membershipId,
      }),
    ).toMatchObject({ ok: false, code: "NOT_FOUND" });
    expect(await mudarStatus(ctxB, id, "RESOLVED", 0)).toMatchObject({
      ok: false,
      code: "NOT_FOUND",
    });
    expect(await criarNota(ctxB, id, "invadindo")).toBeNull();
  });

  it("transferir para pessoa de OUTRO tenant é recusado", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const d = await obterAtendimento(ctxA, id);

    const r = await transferirAtendimento(ctxA, id, {
      expectedVersion: d!.version,
      // Membership existe — mas no tenant B. O RLS faz a busca não achar.
      toMembershipId: B.membership.membershipId,
    });
    expect(r).toMatchObject({ ok: false, code: "INVALID_TARGET" });
  });

  it("nota de A é invisível para B", async () => {
    const { id } = await criarAtendimento(A.id, {});
    await criarNota(ctxA, id, "combinado com o cliente por telefone");

    expect(await listarNotas(ctxA, id)).toHaveLength(1);
    expect(await listarNotas(ctxB, id)).toHaveLength(0);
  });
});

/* 3-4 ─ visualizar ──────────────────────────────────────────────────── */

describe("visualização", () => {
  it("registra quem abriu, com data e contagem", async () => {
    const { id } = await criarAtendimento(A.id, {});
    await registrarVisualizacao(ctxA, id);
    await registrarVisualizacao(ctxA, id);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.views).toHaveLength(1);
    // Duas aberturas, uma linha: o que importa é quem viu, não quantos
    // cliques deu. (A terceira vem do próprio obterAtendimento? Não: só
    // registrarVisualizacao grava.)
    expect(d?.views[0]?.viewCount).toBe(2);
    expect(d?.firstViewedAt).not.toBeNull();
  });

  it("VISUALIZAR NÃO É ASSUMIR", async () => {
    const { id } = await criarAtendimento(A.id, {});
    await registrarVisualizacao(ctxA, id);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.assignee).toBeNull();
    expect(d?.status).toBe("NEW");
  });

  it("o evento VIEWED entra uma vez por pessoa, não a cada abertura", async () => {
    const { id } = await criarAtendimento(A.id, {});
    await registrarVisualizacao(ctxA, id);
    await registrarVisualizacao(ctxA, id);
    await registrarVisualizacao(ctxA, id);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.events.filter((e) => e.type === "VIEWED")).toHaveLength(1);
  });

  it("duas pessoas: duas linhas de visualização", async () => {
    const outra = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `dupla-${Date.now()}@teste.local`,
      name: "Carlos Segundo",
      fiscaleUid: `dupla-${Date.now()}`,
    });
    const ctxOutro: AuthContext = {
      ...ctxA,
      membershipId: outra.membershipId,
      identityId: outra.identityId,
      role: "AGENT" as Role,
    };

    const { id } = await criarAtendimento(A.id, {});
    await registrarVisualizacao(ctxA, id);
    await registrarVisualizacao(ctxOutro, id);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.views).toHaveLength(2);
  });
});

/* 5-6 ─ assumir e concorrência ──────────────────────────────────────── */

describe("assumir", () => {
  it("assume, vira IN_PROGRESS e marca firstAssignedAt", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const r = await assumirAtendimento(ctxA, id);

    expect(r.ok).toBe(true);
    const d = await obterAtendimento(ctxA, id);
    expect(d?.assignee?.membershipId).toBe(A.membership.membershipId);
    expect(d?.status).toBe("IN_PROGRESS");
    expect(d?.firstAssignedAt).not.toBeNull();
    expect(d?.events.map((e) => e.type)).toContain("ASSIGNED");
  });

  it("dois cliques simultâneos: exatamente um vence, e o outro sabe quem levou", async () => {
    // O teste central da fase. Sem a condição dentro do UPDATE, os dois
    // "assumiriam" e duas pessoas responderiam o mesmo cliente.
    const outra = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `corrida-${Date.now()}@teste.local`,
      name: "Carlos Corrida",
      fiscaleUid: `corrida-${Date.now()}`,
    });
    const ctxOutro: AuthContext = {
      ...ctxA,
      membershipId: outra.membershipId,
      identityId: outra.identityId,
      role: "AGENT" as Role,
    };

    for (let i = 0; i < 8; i++) {
      const { id } = await criarAtendimento(A.id, {});

      const [x, y] = await Promise.all([
        assumirAtendimento(ctxA, id),
        assumirAtendimento(ctxOutro, id),
      ]);

      const vencedores = [x, y].filter((r) => r.ok);
      const perdedores = [x, y].filter((r) => !r.ok);

      expect(vencedores, `rodada ${i}`).toHaveLength(1);
      expect(perdedores, `rodada ${i}`).toHaveLength(1);

      const perdedor = perdedores[0]!;
      if (perdedor.ok) continue;
      expect(perdedor.code).toBe("ALREADY_ASSIGNED");
      // A mensagem diz QUEM assumiu — é o que impede a pessoa de clicar
      // de novo achando que travou.
      expect(perdedor.message).toMatch(/assumido por/i);

      // E o banco tem UM responsável, não o último que escreveu.
      const d = await obterAtendimento(ctxA, id);
      expect(d?.assignee).not.toBeNull();
      expect(d?.events.filter((e) => e.type === "ASSIGNED")).toHaveLength(1);
    }
  });

  it("cinco tentativas ao mesmo tempo: uma vence, quatro sabem o porquê", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const r = await Promise.all(Array.from({ length: 5 }, () => assumirAtendimento(ctxA, id)));

    expect(r.filter((x) => x.ok)).toHaveLength(1);
    expect(r.filter((x) => !x.ok && x.code === "ALREADY_ASSIGNED")).toHaveLength(4);
  });
});

/* 7-9 ─ transferir e departamento ───────────────────────────────────── */

describe("transferência", () => {
  it("troca o responsável, registra de-quem-para-quem e NÃO resolve", async () => {
    const outra = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `transf-${Date.now()}@teste.local`,
      name: "Carlos Destino",
      fiscaleUid: `transf-${Date.now()}`,
    });

    const { id } = await criarAtendimento(A.id, {});
    await assumirAtendimento(ctxA, id);
    const antes = await obterAtendimento(ctxA, id);

    const r = await transferirAtendimento(ctxA, id, {
      expectedVersion: antes!.version,
      toMembershipId: outra.membershipId,
      reason: "área errada",
    });
    expect(r.ok).toBe(true);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.assignee?.membershipId).toBe(outra.membershipId);
    // Transferir não resolve nada.
    expect(d?.status).toBe("IN_PROGRESS");

    const ev = d?.events.find((e) => e.type === "TRANSFERRED");
    expect(ev?.from).toBeTruthy();
    expect(ev?.to).toBeTruthy();
  });

  it("devolver à fila deixa sem responsável e registra UNASSIGNED", async () => {
    const { id } = await criarAtendimento(A.id, {});
    await assumirAtendimento(ctxA, id);
    const antes = await obterAtendimento(ctxA, id);

    const r = await transferirAtendimento(ctxA, id, {
      expectedVersion: antes!.version,
      toMembershipId: null,
    });
    expect(r.ok).toBe(true);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.assignee).toBeNull();
    expect(d?.events.map((e) => e.type)).toContain("UNASSIGNED");
  });

  it("versão desatualizada é conflito, não sobrescrita silenciosa", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const tela = await obterAtendimento(ctxA, id);

    // Alguém mexeu antes.
    await assumirAtendimento(ctxA, id);

    const r = await transferirAtendimento(ctxA, id, {
      expectedVersion: tela!.version,
      toMembershipId: null,
    });
    expect(r).toMatchObject({ ok: false, code: "STALE" });
  });

  it("pessoa indisponível não recebe transferência", async () => {
    const ferias = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `ferias-${Date.now()}@teste.local`,
      name: "Mariana Ferias",
      fiscaleUid: `ferias-${Date.now()}`,
    });
    await withTenant(A.id, async (tx) => {
      await tx.membership.update({
        where: { id: ferias.membershipId },
        data: { availableForAssignment: false },
      });
    });

    const { id } = await criarAtendimento(A.id, {});
    const d = await obterAtendimento(ctxA, id);

    const r = await transferirAtendimento(ctxA, id, {
      expectedVersion: d!.version,
      toMembershipId: ferias.membershipId,
    });
    expect(r).toMatchObject({ ok: false, code: "INVALID_TARGET" });
  });

  it("trocar de área registra o de-onde-para-onde", async () => {
    const fiscal = await criarDepartamentoTeste(A.id, `f-${Date.now()}`, "Fiscal");
    const contabil = await criarDepartamentoTeste(A.id, `c-${Date.now()}`, "Contábil");

    const { id } = await criarAtendimento(A.id, { assignedDepartmentId: fiscal.id });
    const antes = await obterAtendimento(ctxA, id);

    await transferirAtendimento(ctxA, id, {
      expectedVersion: antes!.version,
      toDepartmentId: contabil.id,
    });

    const d = await obterAtendimento(ctxA, id);
    expect(d?.department?.name).toBe("Contábil");
  });
});

/* 13-15 ─ status, resolver, reabrir ─────────────────────────────────── */

describe("status", () => {
  it("percorre os cinco estados", async () => {
    const { id } = await criarAtendimento(A.id, {});
    let v = (await obterAtendimento(ctxA, id))!.version;

    for (const s of ["IN_PROGRESS", "WAITING_CUSTOMER", "WAITING_OFFICE"] as const) {
      const r = await mudarStatus(ctxA, id, s, v);
      expect(r.ok, s).toBe(true);
      if (r.ok) v = r.value.version;
    }

    const d = await obterAtendimento(ctxA, id);
    expect(d?.status).toBe("WAITING_OFFICE");
  });

  it("resolver grava resolvedAt e um evento RESOLVED próprio", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const v = (await obterAtendimento(ctxA, id))!.version;

    const r = await mudarStatus(ctxA, id, "RESOLVED", v);
    expect(r.ok).toBe(true);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.status).toBe("RESOLVED");
    expect(d?.resolvedAt).not.toBeNull();
    expect(d?.events.map((e) => e.type)).toContain("RESOLVED");
  });

  it("reabrir limpa resolvedAt e registra REOPENED", async () => {
    const { id } = await criarAtendimento(A.id, {});
    let v = (await obterAtendimento(ctxA, id))!.version;

    const res = await mudarStatus(ctxA, id, "RESOLVED", v);
    if (res.ok) v = res.value.version;

    const re = await mudarStatus(ctxA, id, "IN_PROGRESS", v);
    expect(re.ok).toBe(true);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.status).toBe("IN_PROGRESS");
    expect(d?.resolvedAt).toBeNull();
    expect(d?.events.map((e) => e.type)).toContain("REOPENED");
  });

  it("reenviar o mesmo status não escreve nem falha", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const v = (await obterAtendimento(ctxA, id))!.version;

    const r = await mudarStatus(ctxA, id, "NEW", v);
    expect(r.ok).toBe(true);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.events.filter((e) => e.type === "STATUS_CHANGED")).toHaveLength(0);
  });

  it("status com versão velha é conflito", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const tela = await obterAtendimento(ctxA, id);
    await mudarStatus(ctxA, id, "IN_PROGRESS", tela!.version);

    const r = await mudarStatus(ctxA, id, "RESOLVED", tela!.version);
    expect(r).toMatchObject({ ok: false, code: "STALE" });
  });
});

/* 10-12 ─ etiquetas e notas ─────────────────────────────────────────── */

describe("etiquetas", () => {
  it("cria, aplica e remove", async () => {
    const t = await criarEtiqueta(ctxA, { slug: `urgente-${Date.now()}`, name: "Urgente", tone: "erro" });
    expect(t).not.toBeNull();

    const { id } = await criarAtendimento(A.id, {});
    expect(await alternarEtiqueta(ctxA, id, t!.id, true)).toBe("APLICADA");
    expect((await obterAtendimento(ctxA, id))?.tags.map((x) => x.name)).toContain("Urgente");

    expect(await alternarEtiqueta(ctxA, id, t!.id, true)).toBe("SEM_MUDANCA");
    expect(await alternarEtiqueta(ctxA, id, t!.id, false)).toBe("REMOVIDA");
    expect((await obterAtendimento(ctxA, id))?.tags).toHaveLength(0);
  });

  it("etiqueta de outro tenant não se aplica", async () => {
    const daB = await criarEtiqueta(ctxB, { slug: `sob-${Date.now()}`, name: "Só do B" });
    const { id } = await criarAtendimento(A.id, {});

    expect(await alternarEtiqueta(ctxA, id, daB!.id, true)).toBe("NAO_ENCONTRADO");
    expect(await listarEtiquetas(ctxB)).toEqual(
      expect.arrayContaining([expect.objectContaining({ name: "Só do B" })]),
    );
    expect((await listarEtiquetas(ctxA)).map((t) => t.name)).not.toContain("Só do B");
  });
});

describe("notas internas", () => {
  it("grava com autor e aparece na linha do tempo — sem o texto", async () => {
    const { id } = await criarAtendimento(A.id, {});
    await criarNota(ctxA, id, "cliente pediu retorno na segunda");

    const notas = await listarNotas(ctxA, id);
    expect(notas).toHaveLength(1);
    expect(notas[0]?.content).toBe("cliente pediu retorno na segunda");
    expect(notas[0]?.author.name).toBeTruthy();

    const d = await obterAtendimento(ctxA, id);
    const ev = d?.events.find((e) => e.type === "NOTE_CREATED");
    expect(ev).toBeTruthy();
    // O histórico registra QUE houve nota, nunca o conteúdo.
    expect(JSON.stringify(ev?.metadata)).not.toContain("retorno na segunda");
  });

  it("nota vazia não entra", async () => {
    const { id } = await criarAtendimento(A.id, {});
    expect(await criarNota(ctxA, id, "   ")).toBeNull();
  });
});

/* 16-18 ─ lista externa ─────────────────────────────────────────────── */

describe("lista de funcionários para o cliente", () => {
  it("só aparece quem foi marcado como visível", async () => {
    const C = await criarTenantComPessoa("cc", "ADMIN" as Role);
    try {
      const antes = await getCustomerVisibleAssignees(C.id);
      // Padrão seguro: ninguém é exposto por ter sido cadastrado.
      expect(antes.people).toHaveLength(0);

      await withTenant(C.id, async (tx) => {
        await tx.membership.update({
          where: { id: C.membership.membershipId },
          data: { visibleToCustomers: true },
        });
      });

      const depois = await getCustomerVisibleAssignees(C.id);
      expect(depois.people).toHaveLength(1);
    } finally {
      await removerTenant(C.id);
    }
  });

  it("devolve nome e área — e nada de e-mail, papel ou permissão", async () => {
    const D = await criarTenantComPessoa("cd", "MANAGER" as Role);
    try {
      const dep = await criarDepartamentoTeste(D.id, "gerencia", "Gerência");
      await withTenant(D.id, async (tx) => {
        await tx.membership.update({
          where: { id: D.membership.membershipId },
          data: { visibleToCustomers: true, primaryDepartmentId: dep.id },
        });
      });

      const menu = await getCustomerVisibleAssignees(D.id);
      const pessoa = menu.people[0]!;

      expect(pessoa.department).toBe("Gerência");
      expect(Object.keys(pessoa).sort()).toEqual([
        "availableForAssignment",
        "department",
        "displayName",
        "membershipId",
      ]);
      const texto = JSON.stringify(menu);
      expect(texto).not.toContain("@");
      expect(texto).not.toContain("MANAGER");
    } finally {
      await removerTenant(D.id);
    }
  });

  it("sempre existe Atendimento Geral", async () => {
    const menu = await getCustomerVisibleAssignees(A.id);
    expect(menu.general.label).toBe("Atendimento Geral");
  });

  it("visível mas indisponível aparece na lista, marcado", async () => {
    const E = await criarTenantComPessoa("ce", "MANAGER" as Role);
    try {
      await withTenant(E.id, async (tx) => {
        await tx.membership.update({
          where: { id: E.membership.membershipId },
          data: { visibleToCustomers: true, availableForAssignment: false },
        });
      });

      const menu = await getCustomerVisibleAssignees(E.id);
      expect(menu.people[0]?.availableForAssignment).toBe(false);
      // Visível para consulta, fora da distribuição: a escolha do cliente
      // é recusada na hora de criar.
      expect(await podeReceberEscolha(E.id, E.membership.membershipId)).toBe(false);
    } finally {
      await removerTenant(E.id);
    }
  });

  it("a lista de um tenant não vaza para o outro", async () => {
    await withTenant(A.id, async (tx) => {
      await tx.membership.update({
        where: { id: A.membership.membershipId },
        data: { visibleToCustomers: true },
      });
    });

    const deB = await getCustomerVisibleAssignees(B.id);
    expect(deB.people.map((p) => p.membershipId)).not.toContain(A.membership.membershipId);
  });
});

/* 20-21 ─ histórico e filtros ───────────────────────────────────────── */

describe("histórico", () => {
  it("conta a vida do atendimento na ordem em que aconteceu", async () => {
    const { id } = await criarAtendimento(A.id, {});
    await registrarVisualizacao(ctxA, id);
    await assumirAtendimento(ctxA, id);
    const v = (await obterAtendimento(ctxA, id))!.version;
    await mudarStatus(ctxA, id, "RESOLVED", v);

    const d = await obterAtendimento(ctxA, id);
    const tipos = d!.events.map((e) => e.type);

    expect(tipos[0]).toBe("CREATED");
    expect(tipos).toContain("VIEWED");
    expect(tipos).toContain("ASSIGNED");
    expect(tipos).toContain("STATUS_CHANGED");
    expect(tipos).toContain("RESOLVED");

    const datas = d!.events.map((e) => new Date(e.createdAt).getTime());
    expect([...datas].sort((a, b) => a - b)).toEqual(datas);
  });
});

describe("filtros", () => {
  it("cada recorte devolve o que promete", async () => {
    const F = await criarTenantComPessoa("cf", "OWNER" as Role);
    const ctxF = contexto(F);
    try {
      const novo = await criarAtendimento(F.id, {});
      const meu = await criarAtendimento(F.id, {});
      const esperando = await criarAtendimento(F.id, {});

      await assumirAtendimento(ctxF, meu.id);

      const vEsperando = (await obterAtendimento(ctxF, esperando.id))!.version;
      await mudarStatus(ctxF, esperando.id, "WAITING_CUSTOMER", vEsperando);

      const ids = (l: { id: string }[]) => l.map((x) => x.id);

      // NOVOS: só o que ninguém tocou.
      expect(ids(await listarAtendimentos(ctxF, "novos"))).toContain(novo.id);
      expect(ids(await listarAtendimentos(ctxF, "novos"))).not.toContain(meu.id);

      // MEUS: sou o responsável.
      expect(ids(await listarAtendimentos(ctxF, "meus"))).toEqual([meu.id]);

      // AGUARDANDO: parado esperando alguém.
      expect(ids(await listarAtendimentos(ctxF, "aguardando"))).toEqual([esperando.id]);

      // TODOS: tudo.
      expect(ids(await listarAtendimentos(ctxF, "todos"))).toHaveLength(3);
    } finally {
      await removerTenant(F.id);
    }
  });

  it("NÃO LIDOS é pessoal: some da minha lista quando EU abro", async () => {
    const G = await criarTenantComPessoa("cg", "OWNER" as Role);
    const ctxG = contexto(G);
    try {
      const { id } = await criarAtendimento(G.id, {});
      expect((await listarAtendimentos(ctxG, "nao-lidos")).map((c) => c.id)).toContain(id);

      await registrarVisualizacao(ctxG, id);

      expect((await listarAtendimentos(ctxG, "nao-lidos")).map((c) => c.id)).not.toContain(id);
      // E continua não lido para o colega que não abriu — é por pessoa.
      const outra = await provisionMembership({
        tenantId: G.id,
        role: "AGENT" as Role,
        email: `naolido-${Date.now()}@teste.local`,
        name: "Outra Pessoa",
        fiscaleUid: `naolido-${Date.now()}`,
      });
      const ctxOutro: AuthContext = {
        ...ctxG,
        membershipId: outra.membershipId,
        identityId: outra.identityId,
      };
      expect((await listarAtendimentos(ctxOutro, "nao-lidos")).map((c) => c.id)).toContain(id);
    } finally {
      await removerTenant(G.id);
    }
  });

  it("as contagens batem com as listas", async () => {
    const H = await criarTenantComPessoa("ch", "OWNER" as Role);
    const ctxH = contexto(H);
    try {
      await criarAtendimento(H.id, {});
      await criarAtendimento(H.id, {});

      const c = await contarPorFiltro(ctxH);
      expect(c.todos).toBe(2);
      expect(c.novos).toBe(2);
      expect(c.meus).toBe(0);
      expect(c.naoLidos).toBe(2);
    } finally {
      await removerTenant(H.id);
    }
  });
});

/* 22 ─ RBAC ─────────────────────────────────────────────────────────── */

describe("RBAC", () => {
  it("VIEWER lê, mas não assume, não transfere, não muda status e não anota", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const viewer: AuthContext = { ...ctxA, role: "VIEWER" as Role };

    // Ler pode.
    expect(await obterAtendimento(viewer, id)).not.toBeNull();
    expect(await listarNotas(viewer, id)).toEqual([]);

    // Agir, não.
    await expect(assumirAtendimento(viewer, id)).rejects.toThrow();
    await expect(
      transferirAtendimento(viewer, id, { expectedVersion: 0, toMembershipId: null }),
    ).rejects.toThrow();
    await expect(mudarStatus(viewer, id, "RESOLVED", 0)).rejects.toThrow();
    await expect(criarNota(viewer, id, "tentando")).rejects.toThrow();
  });

  it("AGENT trabalha, mas não cria etiqueta do escritório", async () => {
    const agent: AuthContext = { ...ctxA, role: "AGENT" as Role };
    const { id } = await criarAtendimento(A.id, {});

    expect((await assumirAtendimento(agent, id)).ok).toBe(true);
    expect(await criarNota(agent, id, "anotado")).not.toBeNull();

    await expect(
      criarEtiqueta(agent, { slug: `nao-${Date.now()}`, name: "Não deveria" }),
    ).rejects.toThrow();
  });
});
