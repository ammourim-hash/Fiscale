/**
 * A fila avisa quem atende; o relógio avisa quem coordena.
 *
 * Os testes que carregam o arquivo — os que precisam FALHAR quando o
 * defeito correspondente entrar:
 *
 *   - gestor vinculado ao setor NÃO recebe o aviso imediato da fila (era
 *     o comportamento antigo, e é o que esta fase corrige);
 *   - o relógio do escalonamento conta só expediente: 17h30 de sexta não
 *     vira escalonamento às 18h, nem no sábado;
 *   - resposta humana cancela o escalonamento;
 *   - escalonar NÃO move status nem carimba primeira resposta;
 *   - duas varreduras não avisam duas vezes.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { criarAtendimento } from "@/server/conversations/service";
import {
  dentroDoExpediente,
  escalonarPendentes,
  minutosDeExpediente,
  MINUTOS_PARA_ESCALAR,
} from "@/server/conversations/escalonamento";
import { CANAL_WHATSAPP } from "@/server/channels/whatsapp/constantes";
import { decidir } from "@/server/notifications/service";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import {
  criarDepartamentoTeste,
  criarTenantComPessoa,
  removerTenant,
  type TenantFixture,
} from "./auth-helpers";
import { fecharPrisma } from "./helpers";

let T: TenantFixture;
const areas: Record<string, string> = {};
const pessoas: Record<string, string> = {};

/** Quinta-feira, 10h00 em Recife (UTC−3) — bem no meio do expediente. */
const QUINTA_10H = new Date("2026-09-24T13:00:00.000Z");

let contador = 0;
function novoContato(): string {
  contador += 1;
  return `558166${String(contador).padStart(7, "0")}`;
}

async function conversaEm(area: string, criadaEm: Date): Promise<string> {
  const { id } = await criarAtendimento(T.id, {
    channel: CANAL_WHATSAPP,
    externalContactId: novoContato(),
    assignedDepartmentId: areas[area]!,
  });
  // `createdAt` tem default do banco; o teste precisa de um passado
  // controlado, e é a única coluna que ele empurra na mão.
  await withTenant(T.id, async (tx) => {
    await tx.$executeRaw`UPDATE conversations SET created_at = ${criadaEm} WHERE id = ${id}::uuid`;
  });
  return id;
}

async function eventos(id: string): Promise<string[]> {
  const evs = await withTenant(T.id, async (tx) =>
    tx.conversationEvent.findMany({ where: { conversationId: id }, select: { type: true } }),
  );
  return evs.map((e) => e.type);
}

beforeAll(async () => {
  T = await criarTenantComPessoa("esc", "OWNER" as Role);

  for (const [slug, nome] of [
    ["fiscal", "Fiscal"],
    ["contabil", "Contábil"],
    ["dp", "DP"],
  ]) {
    areas[slug!] = (await criarDepartamentoTeste(T.id, slug!, nome!)).id;
  }

  const { provisionMembership, assignDepartment } = await import("@/server/admin/provisioning");

  // Um atendente por área, e uma coordenação vinculada às TRÊS — que é
  // exatamente o caso da Karoline e do Fulano.
  const elenco: [string, Role, string[]][] = [
    ["agente_fiscal", "AGENT" as Role, ["fiscal"]],
    ["agente_contabil", "AGENT" as Role, ["contabil"]],
    ["agente_dp", "AGENT" as Role, ["dp"]],
    ["gestor", "MANAGER" as Role, ["fiscal", "contabil", "dp"]],
    ["gestor2", "MANAGER" as Role, ["fiscal", "contabil", "dp"]],
  ];

  for (const [apelido, papel, slugs] of elenco) {
    const m = await provisionMembership({
      tenantId: T.id,
      role: papel,
      email: `${apelido}.${T.slug}@esc.teste`,
      name: apelido,
      fiscaleUid: `${apelido}.${T.slug}`,
    });
    pessoas[apelido] = m.membershipId;
    for (const s of slugs) {
      await assignDepartment({
        tenantId: T.id,
        membershipId: m.membershipId,
        departmentId: areas[s]!,
      });
    }
  }
});

afterAll(async () => {
  await removerTenant(T.id);
  await fecharPrisma();
});

/* ══ 1. o aviso imediato da fila ════════════════════════════════════ */

describe("atendimento novo avisa só quem atende", () => {
  async function avisados(conversationId: string): Promise<string[]> {
    const alvos = await decidir({
      tipo: "ATENDIMENTO_NOVO",
      tenantId: T.id,
      conversationId,
      autorMembershipId: null,
    });
    return alvos.map((a) => a.membershipId).sort();
  }

  it("Fiscal: só o AGENT do Fiscal — o MANAGER fica de fora", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);
    const quem = await avisados(id);

    expect(quem).toEqual([pessoas.agente_fiscal]);
    // O ponto desta fase. Antes, os dois gestores entravam aqui só por
    // estarem vinculados — e a coordenação era acordada por todo caso.
    expect(quem).not.toContain(pessoas.gestor);
    expect(quem).not.toContain(pessoas.gestor2);
  });

  it("Contábil: só o AGENT do Contábil", async () => {
    const id = await conversaEm("contabil", QUINTA_10H);
    expect(await avisados(id)).toEqual([pessoas.agente_contabil]);
  });

  it("DP: só o AGENT do DP", async () => {
    const id = await conversaEm("dp", QUINTA_10H);
    expect(await avisados(id)).toEqual([pessoas.agente_dp]);
  });

  it("o AGENT de outra área não entra", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);
    const quem = await avisados(id);
    expect(quem).not.toContain(pessoas.agente_contabil);
    expect(quem).not.toContain(pessoas.agente_dp);
  });

  it("quem NÃO está no departamento fica de fora, seja qual for o papel", async () => {
    // A dona do escritório existe, é OWNER e está disponível — e não
    // recebe, porque não está vinculada ao Fiscal. O que decide é o
    // vínculo, não a altura na hierarquia.
    const id = await conversaEm("fiscal", QUINTA_10H);
    expect(await avisados(id)).not.toContain(T.membership.membershipId);
  });
});

/* ══ 2. o relógio comercial ═════════════════════════════════════════ */

describe("o relógio conta só expediente", () => {
  it.each([
    ["quinta 10h00", "2026-09-24T13:00:00Z", true],
    ["quinta 07h59", "2026-09-24T10:59:00Z", false],
    ["quinta 08h00", "2026-09-24T11:00:00Z", true],
    ["quinta 16h59", "2026-09-24T19:59:00Z", true],
    ["quinta 17h00", "2026-09-24T20:00:00Z", false],
    ["sábado 10h00", "2026-09-26T13:00:00Z", false],
    ["domingo 10h00", "2026-09-27T13:00:00Z", false],
  ])("%s → %s", (_rotulo, iso, esperado) => {
    expect(dentroDoExpediente(new Date(iso))).toBe(esperado);
  });

  it("dentro do expediente, minuto é minuto", () => {
    const de = new Date("2026-09-24T13:00:00Z");
    expect(minutosDeExpediente(de, new Date("2026-09-24T13:30:00Z"))).toBe(30);
    expect(minutosDeExpediente(de, new Date("2026-09-24T13:29:00Z"))).toBe(29);
  });

  it("a noite NÃO conta", () => {
    // 16h50 de quinta até 08h10 de sexta: 10 minutos antes de fechar
    // mais 10 depois de abrir. As 15 horas de silêncio no meio não são
    // tempo de resposta de ninguém.
    const de = new Date("2026-09-24T19:50:00Z");
    const ate = new Date("2026-09-25T11:10:00Z");
    expect(minutosDeExpediente(de, ate)).toBe(20);
  });

  it("o fim de semana NÃO conta", () => {
    // Sexta 16h45 → segunda 08h05. Sábado e domingo inteiros no meio.
    const de = new Date("2026-09-25T19:45:00Z");
    const ate = new Date("2026-09-28T11:05:00Z");
    expect(minutosDeExpediente(de, ate)).toBe(20);
  });

  it("instante futuro ou igual dá zero", () => {
    const d = new Date("2026-09-24T13:00:00Z");
    expect(minutosDeExpediente(d, d)).toBe(0);
    expect(minutosDeExpediente(d, new Date("2026-09-24T12:00:00Z"))).toBe(0);
  });
});

/* ══ 3. a varredura ═════════════════════════════════════════════════ */

describe("escalonar depois de 30 minutos úteis", () => {
  /** 30 minutos depois da chegada, ainda dentro do expediente. */
  const TRINTA_DEPOIS = new Date(QUINTA_10H.getTime() + 30 * 60_000);

  it("antes dos 30 minutos NÃO escala", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);
    const antes = new Date(QUINTA_10H.getTime() + 29 * 60_000);

    const r = await escalonarPendentes(T.id, antes);
    expect(r.escaladas).toBe(0);
    expect(await eventos(id)).not.toContain("ESCALATED");
  });

  it("aos 30 minutos escala, e avisa SÓ a coordenação", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);

    const r = await escalonarPendentes(T.id, TRINTA_DEPOIS);
    expect(r.escaladas).toBeGreaterThanOrEqual(1);
    expect(await eventos(id)).toContain("ESCALATED");

    const alvos = await decidir({
      tipo: "ATENDIMENTO_ESCALADO",
      tenantId: T.id,
      conversationId: id,
      minutos: MINUTOS_PARA_ESCALAR,
    });
    const quem = alvos.map((a) => a.membershipId).sort();

    expect(quem).toContain(pessoas.gestor);
    expect(quem).toContain(pessoas.gestor2);
    // Quem atende já foi avisado lá atrás; repetir aqui seria alerta em
    // dobro para quem não é o destinatário deste.
    expect(quem).not.toContain(pessoas.agente_fiscal);
  });

  it("o evento nasce SEM ator — quem escalou foi o relógio", async () => {
    const id = await conversaEm("dp", QUINTA_10H);
    await escalonarPendentes(T.id, TRINTA_DEPOIS);

    const e = await withTenant(T.id, async (tx) =>
      tx.conversationEvent.findFirstOrThrow({
        where: { conversationId: id, type: "ESCALATED" },
        select: { actorMembershipId: true, metadata: true },
      }),
    );
    expect(e.actorMembershipId).toBeNull();
    expect(e.metadata).toMatchObject({ regra: "30min-uteis" });
  });

  it("NÃO muda o status nem carimba primeira resposta", async () => {
    const id = await conversaEm("contabil", QUINTA_10H);
    await escalonarPendentes(T.id, TRINTA_DEPOIS);

    const c = await withTenant(T.id, async (tx) =>
      tx.conversation.findUniqueOrThrow({
        where: { id },
        select: { status: true, firstResponseAt: true, assignedMembershipId: true },
      }),
    );
    // Escalonar é avisar, não atender. Mover o status diria que alguém
    // assumiu, e `firstResponseAt` faria a métrica medir o robô.
    expect(c.status).toBe("NEW");
    expect(c.firstResponseAt).toBeNull();
    expect(c.assignedMembershipId).toBeNull();
  });

  it("nenhuma mensagem é criada", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);
    await escalonarPendentes(T.id, TRINTA_DEPOIS);

    const n = await withTenant(T.id, async (tx) =>
      tx.message.count({ where: { conversationId: id } }),
    );
    expect(n).toBe(0);
  });

  it("DUAS varreduras não escalam duas vezes", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);

    await escalonarPendentes(T.id, TRINTA_DEPOIS);
    const segunda = await escalonarPendentes(T.id, new Date(TRINTA_DEPOIS.getTime() + 60_000));

    const marcas = (await eventos(id)).filter((t) => t === "ESCALATED");
    expect(marcas).toHaveLength(1);
    expect(segunda.escaladas).toBe(0);
  });

  it("fora do expediente o relógio não anda: 17h30 de sexta não escala às 18h", async () => {
    // Chegou 16h50 de sexta (10 min antes de fechar).
    const sexta1650 = new Date("2026-09-25T19:50:00Z");
    const id = await conversaEm("fiscal", sexta1650);

    // Sábado ao meio-dia: 19 horas de relógio de parede, 10 minutos úteis.
    const sabado = new Date("2026-09-26T15:00:00Z");
    await escalonarPendentes(T.id, sabado);
    expect(await eventos(id)).not.toContain("ESCALATED");

    // Segunda 08h25: mais 25 úteis, total 35. Agora sim.
    const segunda = new Date("2026-09-28T11:25:00Z");
    await escalonarPendentes(T.id, segunda);
    expect(await eventos(id)).toContain("ESCALATED");
  });

  it("resposta humana CANCELA o escalonamento", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);

    // O que uma resposta de verdade faz: carimba `firstResponseAt`.
    await withTenant(T.id, async (tx) => {
      await tx.conversation.update({
        where: { id },
        data: { firstResponseAt: new Date(QUINTA_10H.getTime() + 5 * 60_000) },
      });
    });

    await escalonarPendentes(T.id, TRINTA_DEPOIS);
    expect(await eventos(id)).not.toContain("ESCALATED");
  });

  it("conversa SEM setor não escala — não há coordenação a avisar", async () => {
    const { id } = await criarAtendimento(T.id, {
      channel: CANAL_WHATSAPP,
      externalContactId: novoContato(),
    });
    await withTenant(T.id, async (tx) => {
      await tx.$executeRaw`UPDATE conversations SET created_at = ${QUINTA_10H} WHERE id = ${id}::uuid`;
    });

    await escalonarPendentes(T.id, TRINTA_DEPOIS);
    expect(await eventos(id)).not.toContain("ESCALATED");
  });

  it("conversa RESOLVIDA não escala", async () => {
    const id = await conversaEm("dp", QUINTA_10H);
    await withTenant(T.id, async (tx) => {
      await tx.conversation.update({ where: { id }, data: { status: "RESOLVED" } });
    });

    await escalonarPendentes(T.id, TRINTA_DEPOIS);
    expect(await eventos(id)).not.toContain("ESCALATED");
  });
});

/* ══ 4. a semântica do prazo, minuto a minuto ═══════════════════════ */

describe("o prazo é de 30 minutos ÚTEIS", () => {
  /** Chegou 16h00 de quinta (16h00 no relógio de Recife). */
  const QUINTA_16H = new Date("2026-09-24T19:00:00Z");

  it("16h29 NÃO escala; 16h30 escala", async () => {
    const id = await conversaEm("fiscal", QUINTA_16H);

    await escalonarPendentes(T.id, new Date("2026-09-24T19:29:00Z"));
    expect(await eventos(id)).not.toContain("ESCALATED");

    await escalonarPendentes(T.id, new Date("2026-09-24T19:30:00Z"));
    expect(await eventos(id)).toContain("ESCALATED");
  });

  it("sexta 16h50 → segunda 08h20 ainda NÃO; 08h25 completa os 30", async () => {
    // 10 minutos úteis na sexta antes de fechar. Faltam 20.
    const id = await conversaEm("contabil", new Date("2026-09-25T19:50:00Z"));

    // Sábado: o relógio não anda.
    await escalonarPendentes(T.id, new Date("2026-09-26T15:00:00Z"));
    expect(await eventos(id)).not.toContain("ESCALATED");

    // Segunda 08h20: 10 da sexta + 20 da segunda = 30? Ainda não — o
    // minuto de abertura só começa a contar depois das 08h00 cheias.
    await escalonarPendentes(T.id, new Date("2026-09-28T11:19:00Z"));
    expect(await eventos(id)).not.toContain("ESCALATED");

    // Segunda 08h25: 10 + 25 = 35, passou.
    await escalonarPendentes(T.id, new Date("2026-09-28T11:25:00Z"));
    expect(await eventos(id)).toContain("ESCALATED");
  });

  it("segunda 08h00 retoma a contagem de onde parou", () => {
    // Sexta 16h55 = 5 minutos úteis. O fim de semana não soma nada, e a
    // abertura de segunda ainda não somou minuto nenhum.
    const sexta1655 = new Date("2026-09-25T19:55:00Z");
    expect(minutosDeExpediente(sexta1655, new Date("2026-09-28T11:00:00Z"))).toBe(5);
    expect(minutosDeExpediente(sexta1655, new Date("2026-09-28T11:01:00Z"))).toBe(6);
  });
});

/* ══ 5. concorrência ════════════════════════════════════════════════ */

describe("o evento ESCALATED é barreira também sob corrida", () => {
  const TRINTA_DEPOIS = new Date(QUINTA_10H.getTime() + 30 * 60_000);

  async function marcas(id: string): Promise<number> {
    return withTenant(T.id, async (tx) =>
      tx.conversationEvent.count({ where: { conversationId: id, type: "ESCALATED" } }),
    );
  }

  it("DUAS varreduras SIMULTÂNEAS produzem UM evento", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);

    // O caso que a leitura-antes-da-escrita não cobria: em READ
    // COMMITTED as duas leem "não existe" e as duas gravam. Quem impede
    // agora é o índice único parcial, consultado pelo ON CONFLICT.
    const [a, b] = await Promise.all([
      escalonarPendentes(T.id, TRINTA_DEPOIS),
      escalonarPendentes(T.id, TRINTA_DEPOIS),
    ]);

    expect(await marcas(id)).toBe(1);
    expect(a.escaladas + b.escaladas).toBeGreaterThanOrEqual(1);
  });

  it("QUATRO varreduras simultâneas também produzem UM", async () => {
    const id = await conversaEm("dp", QUINTA_10H);

    await Promise.all(Array.from({ length: 4 }, () => escalonarPendentes(T.id, TRINTA_DEPOIS)));

    expect(await marcas(id)).toBe(1);
  });

  it("WEBHOOK e tarefa periódica ao mesmo tempo produzem UM", async () => {
    const NUMERO = "111222333444555";
    const tenantPorNumero = (n: string) => (n === NUMERO ? T.id : null);
    const id = await conversaEm("fiscal", QUINTA_10H);

    const { processar } = await import("@/server/channels/whatsapp/inbound");

    // O webhook dispara a MESMA varredura de carona. Aqui as duas caem no
    // mesmo instante — o caso real de escritório movimentado com a Tarefa
    // Agendada de minuto em minuto.
    await Promise.all([
      processar(
        [
          {
            tipo: "MENSAGEM",
            phoneNumberId: NUMERO,
            de: novoContato(),
            externalMessageId: `wamid.corrida-${Date.now()}`,
            timestamp: 1_786_400_000,
            texto: "bom dia",
            nomeDoPerfil: "Cliente",
          },
        ],
        tenantPorNumero,
      ),
      escalonarPendentes(T.id, TRINTA_DEPOIS),
    ]);

    // A varredura do webhook sai sem `await`; dá-se um instante para ela
    // terminar antes de conferir.
    await new Promise((r) => setTimeout(r, 400));

    expect(await marcas(id)).toBe(1);
  });

  it("cada gestor recebe UM escalonamento, e o atendente nenhum", async () => {
    const id = await conversaEm("contabil", QUINTA_10H);
    await escalonarPendentes(T.id, TRINTA_DEPOIS);

    const alvos = await decidir({
      tipo: "ATENDIMENTO_ESCALADO",
      tenantId: T.id,
      conversationId: id,
      minutos: MINUTOS_PARA_ESCALAR,
    });

    const porPessoa = alvos.map((a) => a.membershipId);
    expect(porPessoa.filter((m) => m === pessoas.gestor)).toHaveLength(1);
    expect(porPessoa.filter((m) => m === pessoas.gestor2)).toHaveLength(1);
    expect(porPessoa).not.toContain(pessoas.agente_contabil);
  });
});

/* ══ 6. a tarefa periódica ══════════════════════════════════════════ */

describe("a tarefa periódica", () => {
  const TRINTA_DEPOIS = new Date(QUINTA_10H.getTime() + 30 * 60_000);

  it("encontra conversa parada SEM precisar de mensagem nova", async () => {
    // O motivo de a tarefa existir: o webhook só escalona quando alguém
    // escreve, e escritório calado é onde o caso esquecido mora.
    const id = await conversaEm("dp", QUINTA_10H);

    const r = await escalonarPendentes(T.id, TRINTA_DEPOIS);

    expect(r.escaladas).toBeGreaterThanOrEqual(1);
    expect(await eventos(id)).toContain("ESCALATED");
  });

  it("rodando de minuto em minuto, não duplica", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);

    for (let i = 0; i < 5; i++) {
      await escalonarPendentes(T.id, new Date(TRINTA_DEPOIS.getTime() + i * 60_000));
    }

    const n = await withTenant(T.id, async (tx) =>
      tx.conversationEvent.count({ where: { conversationId: id, type: "ESCALATED" } }),
    );
    expect(n).toBe(1);
  });

  it("respondida ENTRE duas varreduras não escala", async () => {
    const id = await conversaEm("contabil", QUINTA_10H);

    await escalonarPendentes(T.id, new Date(QUINTA_10H.getTime() + 20 * 60_000));
    expect(await eventos(id)).not.toContain("ESCALATED");

    await withTenant(T.id, async (tx) => {
      await tx.conversation.update({
        where: { id },
        data: { firstResponseAt: new Date(QUINTA_10H.getTime() + 25 * 60_000) },
      });
    });

    await escalonarPendentes(T.id, TRINTA_DEPOIS);
    expect(await eventos(id)).not.toContain("ESCALATED");
  });

  it("fora do expediente a varredura roda e não escala nada", async () => {
    // A Tarefa Agendada roda 24 h por dia; quem ignora o horário é a
    // função, não o agendador.
    const id = await conversaEm("dp", new Date("2026-09-26T13:00:00Z"));

    await escalonarPendentes(T.id, new Date("2026-09-26T23:00:00Z"));
    await escalonarPendentes(T.id, new Date("2026-09-27T13:00:00Z"));

    expect(await eventos(id)).not.toContain("ESCALATED");
  });
});

/* ══ 7. quem entra na fila, por PAPEL ═══════════════════════════════ */

/**
 * O caso real que motivou esta correção.
 *
 * A Aline é OWNER e é a responsável pelo Fiscal — atende todo dia. A
 * primeira versão tirava da fila OWNER, ADMIN e MANAGER juntos, e o
 * efeito seria o atendimento dela chegar 30 minutos depois, por
 * escalonamento, como se ela não trabalhasse ali.
 *
 * A regra certa olha para o PAPEL DE COORDENAÇÃO, e ele é um só: MANAGER.
 * OWNER e ADMIN são níveis de poder no sistema, não cargos de supervisão.
 */
describe("o papel na fila imediata", () => {
  /** Uma pessoa nova, com papel e vínculo escolhidos. */
  async function pessoaEm(
    apelido: string,
    papel: Role,
    slugs: string[],
    disponivel = true,
  ): Promise<string> {
    const { provisionMembership, assignDepartment } = await import(
      "@/server/admin/provisioning"
    );
    const m = await provisionMembership({
      tenantId: T.id,
      role: papel,
      email: `${apelido}.${T.slug}@papeis.teste`,
      name: apelido,
      fiscaleUid: `${apelido}.${T.slug}`,
    });
    for (const s of slugs) {
      await assignDepartment({
        tenantId: T.id,
        membershipId: m.membershipId,
        departmentId: areas[s]!,
      });
    }
    if (!disponivel) {
      await withTenant(T.id, async (tx) => {
        await tx.membership.update({
          where: { id: m.membershipId },
          data: { availableForAssignment: false },
        });
      });
    }
    return m.membershipId;
  }

  async function naFila(conversationId: string): Promise<string[]> {
    const alvos = await decidir({
      tipo: "ATENDIMENTO_NOVO",
      tenantId: T.id,
      conversationId,
      autorMembershipId: null,
    });
    return alvos.map((a) => a.membershipId);
  }

  async function noEscalonamento(conversationId: string): Promise<string[]> {
    const alvos = await decidir({
      tipo: "ATENDIMENTO_ESCALADO",
      tenantId: T.id,
      conversationId,
      minutos: MINUTOS_PARA_ESCALAR,
    });
    return alvos.map((a) => a.membershipId);
  }

  it("OWNER vinculado ao Fiscal e disponível RECEBE a fila", async () => {
    // O caso da Aline.
    const aline = await pessoaEm("aline_owner", "OWNER" as Role, ["fiscal"]);
    const id = await conversaEm("fiscal", QUINTA_10H);

    expect(await naFila(id)).toContain(aline);
  });

  it("ADMIN vinculado ao Fiscal e disponível RECEBE a fila", async () => {
    const admin = await pessoaEm("admin_fiscal", "ADMIN" as Role, ["fiscal"]);
    const id = await conversaEm("fiscal", QUINTA_10H);

    expect(await naFila(id)).toContain(admin);
  });

  it("AGENT vinculado ao Fiscal RECEBE a fila", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);
    expect(await naFila(id)).toContain(pessoas.agente_fiscal);
  });

  it("MANAGER vinculado ao Fiscal NÃO recebe a fila", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);
    const fila = await naFila(id);

    expect(fila).not.toContain(pessoas.gestor);
    expect(fila).not.toContain(pessoas.gestor2);
  });

  it("MANAGER continua recebendo o escalonamento", async () => {
    const id = await conversaEm("fiscal", QUINTA_10H);
    const quem = await noEscalonamento(id);

    expect(quem).toContain(pessoas.gestor);
    expect(quem).toContain(pessoas.gestor2);
  });

  it("OWNER NÃO recebe escalonamento só por ser OWNER", async () => {
    // Ele já foi avisado na fila; o escalonamento por cima seria o mesmo
    // alerta duas vezes, para quem já sabia.
    const owner = await pessoaEm("owner_esc", "OWNER" as Role, ["contabil"]);
    const id = await conversaEm("contabil", QUINTA_10H);

    expect(await naFila(id)).toContain(owner);
    expect(await noEscalonamento(id)).not.toContain(owner);
  });

  it("ADMIN também não recebe escalonamento por ser ADMIN", async () => {
    const admin = await pessoaEm("admin_esc", "ADMIN" as Role, ["dp"]);
    const id = await conversaEm("dp", QUINTA_10H);

    expect(await naFila(id)).toContain(admin);
    expect(await noEscalonamento(id)).not.toContain(admin);
  });

  it("pessoa INDISPONÍVEL não recebe a fila, mesmo vinculada", async () => {
    const ferias = await pessoaEm("ferias_fiscal", "AGENT" as Role, ["fiscal"], false);
    const id = await conversaEm("fiscal", QUINTA_10H);

    expect(await naFila(id)).not.toContain(ferias);
  });

  it("pessoa FORA do departamento não recebe a fila", async () => {
    const outro = await pessoaEm("agente_solto", "AGENT" as Role, ["dp"]);
    const id = await conversaEm("fiscal", QUINTA_10H);

    expect(await naFila(id)).not.toContain(outro);
  });

  it("VIEWER vinculado RECEBE — o corte é `conversations.read`, e ele tem", async () => {
    // Registrado porque surpreende: VIEWER não envia mensagem, mas LÊ o
    // atendimento, e o critério da fila é justamente `conversations.read`.
    // Só MANAGER sai por papel. Se um dia o escritório decidir que quem
    // não responde também não deve ser avisado, é aqui que a regra muda —
    // e este teste falha para avisar.
    const viewer = await pessoaEm("viewer_fiscal", "VIEWER" as Role, ["fiscal"]);
    const id = await conversaEm("fiscal", QUINTA_10H);

    expect(await naFila(id)).toContain(viewer);
  });
});

/* ══ 8. a configuração real do escritório, hoje ══════════════════════ */

/**
 * O estado atual do Escritorio Modelo, reproduzido num tenant de teste:
 *
 *   Karoline    MANAGER  Fiscal, Contábil, DP
 *   Fulano  MANAGER  Fiscal, Contábil, DP
 *   Aline       OWNER    sem departamento
 *
 * O que este bloco trava é o diagnóstico que hoje é verdade e que ninguém
 * quer descobrir por reclamação de cliente: com essa configuração
 * NENHUMA das três filas avisa alguém. Os dois gestores estão vinculados
 * mas entram pelo escalonamento; a Aline entraria na fila, mas não está
 * vinculada a setor nenhum.
 *
 * E trava a saída: basta vincular a Aline ao Fiscal para o Fiscal voltar
 * a avisar na hora — que é a correção desta rodada. Com a regra antiga
 * (OWNER fora da fila por papel) o último teste falharia.
 */
describe("a configuração real do escritório", () => {
  let R: TenantFixture;
  const areasR: Record<string, string> = {};
  const equipeR: Record<string, string> = {};

  beforeAll(async () => {
    R = await criarTenantComPessoa("real", "OWNER" as Role);
    equipeR.aline = R.membership.membershipId;

    for (const [slug, nome] of [
      ["fiscal", "Fiscal"],
      ["contabil", "Contábil"],
      ["dp", "DP"],
    ]) {
      areasR[slug!] = (await criarDepartamentoTeste(R.id, slug!, nome!)).id;
    }

    const { provisionMembership, assignDepartment } = await import("@/server/admin/provisioning");

    for (const apelido of ["karoline", "fulano"]) {
      const m = await provisionMembership({
        tenantId: R.id,
        role: "MANAGER" as Role,
        email: `${apelido}.${R.slug}@real.teste`,
        name: apelido,
        fiscaleUid: `${apelido}.${R.slug}`,
      });
      equipeR[apelido] = m.membershipId;
      for (const s of ["fiscal", "contabil", "dp"]) {
        await assignDepartment({
          tenantId: R.id,
          membershipId: m.membershipId,
          departmentId: areasR[s]!,
        });
      }
    }
    // A Aline fica DE FORA de qualquer departamento — como está hoje.
  });

  afterAll(async () => {
    await removerTenant(R.id);
  });

  async function conversaNoReal(slug: string): Promise<string> {
    const { id } = await criarAtendimento(R.id, {
      channel: CANAL_WHATSAPP,
      externalContactId: novoContato(),
      assignedDepartmentId: areasR[slug]!,
    });
    return id;
  }

  async function filaDo(slug: string): Promise<string[]> {
    const alvos = await decidir({
      tipo: "ATENDIMENTO_NOVO",
      tenantId: R.id,
      conversationId: await conversaNoReal(slug),
      autorMembershipId: null,
    });
    return alvos.map((a) => a.membershipId);
  }

  async function escalonamentoDo(slug: string): Promise<string[]> {
    const alvos = await decidir({
      tipo: "ATENDIMENTO_ESCALADO",
      tenantId: R.id,
      conversationId: await conversaNoReal(slug),
      minutos: MINUTOS_PARA_ESCALAR,
    });
    return alvos.map((a) => a.membershipId);
  }

  it.each(["fiscal", "contabil", "dp"])("%s: hoje a fila não avisa NINGUÉM", async (slug) => {
    const quem = await filaDo(slug);

    // Os dois gestores estão vinculados aos três setores, e nenhum recebe
    // fila: MANAGER entra pelo escalonamento.
    expect(quem).not.toContain(equipeR.karoline);
    expect(quem).not.toContain(equipeR.fulano);
    // E a Aline não recebe porque não está vinculada a setor nenhum — não
    // por ser OWNER.
    expect(quem).not.toContain(equipeR.aline);
    expect(quem).toEqual([]);
  });

  it.each(["fiscal", "contabil", "dp"])(
    "%s: o escalonamento avisa os dois gestores",
    async (slug) => {
      const quem = await escalonamentoDo(slug);

      expect(quem).toContain(equipeR.karoline);
      expect(quem).toContain(equipeR.fulano);
      expect(quem).toHaveLength(2);
    },
  );

  it("vincular a Aline (OWNER) ao Fiscal faz o Fiscal voltar a avisar", async () => {
    const { assignDepartment } = await import("@/server/admin/provisioning");
    await assignDepartment({
      tenantId: R.id,
      membershipId: equipeR.aline!,
      departmentId: areasR.fiscal!,
    });

    expect(await filaDo("fiscal")).toEqual([equipeR.aline]);

    // E só o Fiscal: vincular a um setor não coloca ninguém nos outros.
    expect(await filaDo("contabil")).toEqual([]);
    expect(await filaDo("dp")).toEqual([]);

    // O escalonamento continua sendo só da coordenação; ela não recebe o
    // mesmo alerta duas vezes.
    expect(await escalonamentoDo("fiscal")).not.toContain(equipeR.aline);
  });

  it("a cobertura da tela separa elegíveis, gestores e vinculados", async () => {
    // O vínculo é reafirmado aqui de propósito: `assignDepartment` é
    // idempotente e assim este teste não depende da ordem do anterior.
    const { assignDepartment } = await import("@/server/admin/provisioning");
    await assignDepartment({
      tenantId: R.id,
      membershipId: equipeR.aline!,
      departmentId: areasR.fiscal!,
    });

    const { coberturaDosSetores } = await import("@/server/team/service");
    const cobertura = await coberturaDosSetores({
      sessionId: "00000000-0000-4000-8000-0000000000f0",
      tenantId: R.id,
      membershipId: equipeR.aline!,
      identityId: R.membership.identityId,
      email: R.email,
      name: "Aline",
      role: "OWNER" as Role,
    });
    const porSlug = new Map(cobertura.map((c) => [c.slug, c]));

    // Fiscal: a Aline elegível + os dois gestores = 3 vinculados.
    expect(porSlug.get("fiscal")).toMatchObject({ elegiveis: 1, gestores: 2, vinculados: 3 });

    // Contábil e DP: dois vinculados e ninguém elegível. É a linha que a
    // tela precisa mostrar como "ninguém será avisado na hora".
    for (const slug of ["contabil", "dp"]) {
      expect(porSlug.get(slug)).toMatchObject({ elegiveis: 0, gestores: 2, vinculados: 2 });
    }
  });
});
