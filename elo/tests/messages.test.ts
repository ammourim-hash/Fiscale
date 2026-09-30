/**
 * O motor de mensagens.
 *
 * Quatro testes carregam o peso do arquivo, e são os quatro que precisam
 * FALHAR quando o defeito correspondente é introduzido:
 *
 *   - idempotência concorrente (duas requisições, uma mensagem);
 *   - ordenação concorrente (dez envios juntos, dez sequências distintas);
 *   - snapshot da assinatura (mudar de setor não reescreve o passado);
 *   - isolamento entre tenants (mensagem de A invisível para B).
 *
 * Os demais existem para que, quando algum deles quebrar, se saiba
 * exatamente o que mudou.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import type { AuthContext } from "@/server/auth/context";
import { criarAtendimento, mudarStatus } from "@/server/conversations/service";
import { contarPorFiltro, listarAtendimentos, obterAtendimento } from "@/server/conversations/queries";
import { criarNota, listarNotas } from "@/server/conversations/notes";
import { listarMensagens, resumoDeMensagens } from "@/server/messages/queries";
import {
  enviarMensagem,
  marcarVisualizadas,
  registrarMensagemRecebida,
} from "@/server/messages/service";
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

function contexto(
  t: TenantFixture,
  role: Role = "OWNER" as Role,
  membershipId?: string,
): AuthContext {
  return {
    sessionId: "00000000-0000-4000-8000-000000000001",
    tenantId: t.id,
    membershipId: membershipId ?? t.membership.membershipId,
    identityId: t.membership.identityId,
    email: t.email,
    name: "Pessoa de Teste",
    role,
  };
}

/** Um atendimento novo, do tenant A, para o teste não depender dos outros. */
async function novoAtendimento(t: TenantFixture = A): Promise<string> {
  const { id } = await criarAtendimento(t.id, {});
  return id;
}

beforeAll(async () => {
  A = await criarTenantComPessoa("ma", "OWNER" as Role);
  B = await criarTenantComPessoa("mb", "OWNER" as Role);
  ctxA = contexto(A);
  ctxB = contexto(B);
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

/* 1-3 ─ criar, entrada e saída ──────────────────────────────────────── */

describe("criar mensagem", () => {
  it("1. grava uma mensagem TEXT de saída", async () => {
    const id = await novoAtendimento();
    const r = await enviarMensagem(ctxA, id, { content: "Bom dia, vou verificar." });

    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.content).toBe("Bom dia, vou verificar.");
    expect(r.value.direction).toBe("OUTBOUND");
    expect(r.value.sequence).toBe(1);

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages).toHaveLength(1);
    expect(pagina?.messages[0]?.type).toBe("TEXT");
  });

  it("2. mensagem de entrada não finge ter vindo de um funcionário", async () => {
    const id = await novoAtendimento();
    const r = await registrarMensagemRecebida(A.id, id, {
      content: "Preciso da guia.",
      externalSenderId: "5581999990000",
    });

    expect(r.ok).toBe(true);
    if (!r.ok) return;
    // O ponto: identidade EXTERNA, e nenhum membership envolvido.
    expect(r.value.senderMembershipId).toBeNull();
    expect(r.value.senderDisplayName).toBeNull();

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages[0]?.externalSenderId).toBe("5581999990000");
  });

  it("3. entrada e saída convivem na mesma conversa, em ordem", async () => {
    const id = await novoAtendimento();
    await registrarMensagemRecebida(A.id, id, { content: "Oi" });
    await enviarMensagem(ctxA, id, { content: "Bom dia" });
    await registrarMensagemRecebida(A.id, id, { content: "Obrigado" });

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages.map((m) => m.direction)).toEqual([
      "INBOUND",
      "OUTBOUND",
      "INBOUND",
    ]);
    expect(pagina?.messages.map((m) => m.sequence)).toEqual([1, 2, 3]);
  });

  it("recusa mensagem vazia e mensagem gigante", async () => {
    const id = await novoAtendimento();
    const vazia = await enviarMensagem(ctxA, id, { content: "   " });
    expect(vazia.ok).toBe(false);

    const gigante = await enviarMensagem(ctxA, id, { content: "x".repeat(5000) });
    expect(gigante.ok).toBe(false);
  });
});

/* 4-5 ─ snapshot da assinatura ──────────────────────────────────────── */

describe("identidade histórica do funcionário", () => {
  it("4. a mensagem guarda Nome • Área de quando foi escrita", async () => {
    const fiscal = await criarDepartamentoTeste(A.id, "snap-fiscal", "Fiscal");
    const pessoa = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `aline.snap@${A.slug}.teste`,
      name: "Aline Souza",
      fiscaleUid: `aline.snap.${A.slug}`,
    });
    await withTenant(A.id, async (tx) => {
      await tx.membership.update({
        where: { id: pessoa.membershipId },
        data: { primaryDepartmentId: fiscal.id },
      });
    });

    const id = await novoAtendimento();
    const r = await enviarMensagem(
      contexto(A, "AGENT" as Role, pessoa.membershipId),
      id,
      { content: "Bom dia! Vou verificar para você." },
    );

    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.senderDisplayName).toBe("Aline Souza");
    expect(r.value.senderDepartmentName).toBe("Fiscal");
  });

  it("5. mudar de departamento NÃO reescreve a mensagem antiga", async () => {
    const fiscal = await criarDepartamentoTeste(A.id, "mud-fiscal", "Fiscal");
    const contabil = await criarDepartamentoTeste(A.id, "mud-contabil", "Contábil");

    const pessoa = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `carla.mud@${A.slug}.teste`,
      name: "Carla Lima",
      fiscaleUid: `carla.mud.${A.slug}`,
    });
    await withTenant(A.id, async (tx) => {
      await tx.membership.update({
        where: { id: pessoa.membershipId },
        data: { primaryDepartmentId: fiscal.id },
      });
    });

    const ctxPessoa = contexto(A, "AGENT" as Role, pessoa.membershipId);
    const id = await novoAtendimento();
    await enviarMensagem(ctxPessoa, id, { content: "Mensagem de janeiro." });

    // Em março ela muda de área.
    await withTenant(A.id, async (tx) => {
      await tx.membership.update({
        where: { id: pessoa.membershipId },
        data: { primaryDepartmentId: contabil.id },
      });
    });
    await enviarMensagem(ctxPessoa, id, { content: "Mensagem de março." });

    const pagina = await listarMensagens(ctxA, id);
    const [janeiro, marco] = pagina?.messages ?? [];

    // Este é o teste: o passado continua dizendo Fiscal.
    expect(janeiro?.senderDepartmentName).toBe("Fiscal");
    expect(marco?.senderDepartmentName).toBe("Contábil");
  });
});

/* 6 ─ firstResponseAt ───────────────────────────────────────────────── */

describe("primeira resposta", () => {
  it("6. firstResponseAt é gravado uma vez e não muda depois", async () => {
    const id = await novoAtendimento();
    expect((await obterAtendimento(ctxA, id))?.firstResponseAt).toBeNull();

    await enviarMensagem(ctxA, id, { content: "primeira" });
    const depoisDaPrimeira = (await obterAtendimento(ctxA, id))?.firstResponseAt;
    expect(depoisDaPrimeira).toBeTruthy();

    await new Promise((r) => setTimeout(r, 30));
    await enviarMensagem(ctxA, id, { content: "segunda" });
    const depoisDaSegunda = (await obterAtendimento(ctxA, id))?.firstResponseAt;

    // "Tempo até a primeira resposta" não pode melhorar nem piorar quando
    // a segunda mensagem sai.
    expect(depoisDaSegunda?.toISOString()).toBe(depoisDaPrimeira?.toISOString());
  });

  it("mensagem de entrada não preenche firstResponseAt", async () => {
    const id = await novoAtendimento();
    await registrarMensagemRecebida(A.id, id, { content: "cliente falando" });
    expect((await obterAtendimento(ctxA, id))?.firstResponseAt).toBeNull();
  });
});

/* máquina de estados ────────────────────────────────────────────────── */

describe("status ao enviar e ao receber", () => {
  it("responder tira de NEW e leva a IN_PROGRESS", async () => {
    const id = await novoAtendimento();
    await enviarMensagem(ctxA, id, { content: "oi" });
    expect((await obterAtendimento(ctxA, id))?.status).toBe("IN_PROGRESS");
  });

  it("responder em WAITING_OFFICE devolve a bola: vira IN_PROGRESS", async () => {
    const id = await novoAtendimento();
    const v = (await obterAtendimento(ctxA, id))!.version;
    await mudarStatus(ctxA, id, "WAITING_OFFICE", v);

    await enviarMensagem(ctxA, id, { content: "aqui está" });
    expect((await obterAtendimento(ctxA, id))?.status).toBe("IN_PROGRESS");
  });

  it("responder em WAITING_CUSTOMER NÃO muda o status — cobrar não é ser respondido", async () => {
    const id = await novoAtendimento();
    const v = (await obterAtendimento(ctxA, id))!.version;
    await mudarStatus(ctxA, id, "WAITING_CUSTOMER", v);

    await enviarMensagem(ctxA, id, { content: "lembrete" });
    expect((await obterAtendimento(ctxA, id))?.status).toBe("WAITING_CUSTOMER");
  });

  it("cliente responder em WAITING_CUSTOMER passa para WAITING_OFFICE", async () => {
    const id = await novoAtendimento();
    const v = (await obterAtendimento(ctxA, id))!.version;
    await mudarStatus(ctxA, id, "WAITING_CUSTOMER", v);

    await registrarMensagemRecebida(A.id, id, { content: "aqui o documento" });
    expect((await obterAtendimento(ctxA, id))?.status).toBe("WAITING_OFFICE");
  });

  it("mensagem em atendimento RESOLVIDO não reabre sozinha", async () => {
    const id = await novoAtendimento();
    const v = (await obterAtendimento(ctxA, id))!.version;
    await mudarStatus(ctxA, id, "RESOLVED", v);

    await registrarMensagemRecebida(A.id, id, { content: "mais uma coisa" });
    const d = await obterAtendimento(ctxA, id);

    // Reabrir é decisão de gente. O que a mensagem faz é subir na lista e
    // aparecer como não lida — e isso sim precisa acontecer.
    expect(d?.status).toBe("RESOLVED");
    expect(d?.unreadCount).toBe(1);
  });
});

/* 7-9 ─ isolamento ──────────────────────────────────────────────────── */

describe("isolamento entre tenants", () => {
  it("7/8. mensagem do tenant A é invisível para o tenant B", async () => {
    const id = await novoAtendimento(A);
    await enviarMensagem(ctxA, id, { content: "segredo do escritório A" });

    // Nem a conversa existe para B — e é o banco que decide isso.
    expect(await listarMensagens(ctxB, id)).toBeNull();
    expect(await obterAtendimento(ctxB, id)).toBeNull();
  });

  it("B não consegue ESCREVER numa conversa de A", async () => {
    const id = await novoAtendimento(A);
    const r = await enviarMensagem(ctxB, id, { content: "invasão" });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.code).toBe("NOT_FOUND");
  });

  it("9. recibo de leitura cross-tenant é recusado", async () => {
    const id = await novoAtendimento(A);
    const m = await registrarMensagemRecebida(A.id, id, { content: "do cliente de A" });
    expect(m.ok).toBe(true);
    if (!m.ok) return;

    const r = await marcarVisualizadas(ctxB, id, [m.value.id]);
    expect(r.marcadas).toBe(0);

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages[0]?.viewedBy).toHaveLength(0);
  });

  it("a contagem de não lidas de B não enxerga conversa de A", async () => {
    const id = await novoAtendimento(A);
    await registrarMensagemRecebida(A.id, id, { content: "de A" });

    const resumo = await resumoDeMensagens(ctxB, [id]);
    expect(resumo.get(id)?.unreadCount ?? 0).toBe(0);
  });
});

/* 10-13 ─ visualização por pessoa ───────────────────────────────────── */

describe("visualização de mensagem", () => {
  it("10. marcar visualizada registra quem e quando", async () => {
    const id = await novoAtendimento();
    const m = await registrarMensagemRecebida(A.id, id, { content: "Preciso da guia." });
    if (!m.ok) throw new Error("preparo falhou");

    await marcarVisualizadas(ctxA, id, [m.value.id]);

    const pagina = await listarMensagens(ctxA, id);
    const vistos = pagina?.messages[0]?.viewedBy ?? [];
    expect(vistos).toHaveLength(1);
    expect(vistos[0]?.membershipId).toBe(A.membership.membershipId);
  });

  it("11. visualizar mensagem NÃO assume o atendimento", async () => {
    const id = await novoAtendimento();
    const m = await registrarMensagemRecebida(A.id, id, { content: "oi" });
    if (!m.ok) throw new Error("preparo falhou");

    await marcarVisualizadas(ctxA, id, [m.value.id]);

    const d = await obterAtendimento(ctxA, id);
    expect(d?.assignee).toBeNull();
    expect(d?.status).toBe("NEW");
  });

  it("12/13. Aline leu e Carlos não: a contagem é de cada um", async () => {
    const carlos = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `carlos.leitura@${A.slug}.teste`,
      name: "Carlos Prado",
      fiscaleUid: `carlos.leitura.${A.slug}`,
    });
    const ctxCarlos = contexto(A, "AGENT" as Role, carlos.membershipId);

    const id = await novoAtendimento();
    const m = await registrarMensagemRecebida(A.id, id, { content: "mensagem única" });
    if (!m.ok) throw new Error("preparo falhou");

    await marcarVisualizadas(ctxA, id, [m.value.id]);

    const deAline = await resumoDeMensagens(ctxA, [id]);
    const deCarlos = await resumoDeMensagens(ctxCarlos, [id]);

    expect(deAline.get(id)?.unreadCount).toBe(0);
    expect(deCarlos.get(id)?.unreadCount).toBe(1);
  });

  it("marcar duas vezes não duplica o recibo", async () => {
    const id = await novoAtendimento();
    const m = await registrarMensagemRecebida(A.id, id, { content: "rolando a tela" });
    if (!m.ok) throw new Error("preparo falhou");

    await marcarVisualizadas(ctxA, id, [m.value.id]);
    const segunda = await marcarVisualizadas(ctxA, id, [m.value.id]);

    expect(segunda.marcadas).toBe(0);
    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages[0]?.viewedBy).toHaveLength(1);
  });

  it("mensagem de SAÍDA não vira recibo de leitura", async () => {
    // Recibo é sobre o que o cliente mandou. Marcar a própria resposta
    // como lida não significa nada.
    const id = await novoAtendimento();
    const m = await enviarMensagem(ctxA, id, { content: "minha resposta" });
    if (!m.ok) throw new Error("preparo falhou");

    const r = await marcarVisualizadas(ctxA, id, [m.value.id]);
    expect(r.marcadas).toBe(0);
  });
});

/* 14-15 ─ idempotência ──────────────────────────────────────────────── */

describe("idempotência", () => {
  it("14. o mesmo clientMessageId não cria duas mensagens", async () => {
    const id = await novoAtendimento();
    const cid = `cli-${Date.now()}-a`;

    const um = await enviarMensagem(ctxA, id, { content: "duplo clique", clientMessageId: cid });
    const dois = await enviarMensagem(ctxA, id, { content: "duplo clique", clientMessageId: cid });

    expect(um.ok && dois.ok).toBe(true);
    if (!um.ok || !dois.ok) return;

    expect(dois.value.id).toBe(um.value.id);
    expect(dois.value.duplicada).toBe(true);

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages).toHaveLength(1);
  });

  it("15. CONCORRENTE: duas requisições ao mesmo tempo, uma mensagem só", async () => {
    // O caso que a versão ingênua — SELECT e depois INSERT — deixa passar:
    // as duas consultam antes de qualquer uma gravar.
    for (let i = 0; i < 5; i++) {
      const id = await novoAtendimento();
      const cid = `concorrente-${Date.now()}-${i}`;

      const [x, y] = await Promise.all([
        enviarMensagem(ctxA, id, { content: "reenvio", clientMessageId: cid }),
        enviarMensagem(ctxA, id, { content: "reenvio", clientMessageId: cid }),
      ]);

      expect(x.ok && y.ok, "as duas devem responder ok").toBe(true);
      if (!x.ok || !y.ok) return;
      expect(x.value.id, `rodada ${i}: ids diferentes = mensagem duplicada`).toBe(y.value.id);

      const pagina = await listarMensagens(ctxA, id);
      expect(pagina?.messages, `rodada ${i}`).toHaveLength(1);
    }
  });

  it("clientMessageId diferente é mensagem diferente, mesmo com o texto igual", async () => {
    const id = await novoAtendimento();
    await enviarMensagem(ctxA, id, { content: "ok", clientMessageId: `d-${Date.now()}-1` });
    await enviarMensagem(ctxA, id, { content: "ok", clientMessageId: `d-${Date.now()}-2` });

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages).toHaveLength(2);
  });

  it("reentrega do canal não vira mensagem nova", async () => {
    const id = await novoAtendimento();
    const externo = `wamid-${Date.now()}`;

    await registrarMensagemRecebida(A.id, id, { content: "oi", externalMessageId: externo });
    const repetida = await registrarMensagemRecebida(A.id, id, {
      content: "oi",
      externalMessageId: externo,
    });

    expect(repetida.ok).toBe(true);
    if (!repetida.ok) return;
    expect(repetida.value.duplicada).toBe(true);

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages).toHaveLength(1);
  });
});

/* 16 ─ ordenação sob concorrência ───────────────────────────────────── */

describe("ordenação", () => {
  it("16. dez envios simultâneos recebem dez sequências distintas", async () => {
    const id = await novoAtendimento();

    await Promise.all(
      Array.from({ length: 10 }, (_, i) =>
        enviarMensagem(ctxA, id, { content: `mensagem ${i}` }),
      ),
    );

    const pagina = await listarMensagens(ctxA, id, { limite: 100 });
    const sequencias = (pagina?.messages ?? []).map((m) => m.sequence);

    expect(sequencias).toHaveLength(10);
    // Sem buraco, sem repetição, e em ordem crescente na leitura.
    expect(new Set(sequencias).size).toBe(10);
    expect(sequencias).toEqual([...sequencias].sort((a, b) => a - b));
    expect(sequencias).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });

  it("a ordem não depende do relógio", async () => {
    // Duas mensagens no mesmo milissegundo continuam ordenadas.
    const id = await novoAtendimento();
    await Promise.all([
      enviarMensagem(ctxA, id, { content: "a" }),
      enviarMensagem(ctxA, id, { content: "b" }),
    ]);

    const pagina = await listarMensagens(ctxA, id);
    const [p, s] = pagina?.messages ?? [];
    expect(p!.sequence).toBeLessThan(s!.sequence);
  });
});

/* 17-18, 35 ─ paginação e volume ────────────────────────────────────── */

describe("paginação", () => {
  it("17. a primeira página traz as mais RECENTES", async () => {
    const id = await novoAtendimento();
    for (let i = 1; i <= 12; i++) {
      await enviarMensagem(ctxA, id, { content: `n${i}` });
    }

    const pagina = await listarMensagens(ctxA, id, { limite: 5 });
    expect(pagina?.messages.map((m) => m.content)).toEqual(["n8", "n9", "n10", "n11", "n12"]);
    expect(pagina?.hasMore).toBe(true);
    expect(pagina?.oldestSequence).toBe(8);
  });

  it("18. subindo com o cursor, chega-se às antigas sem repetir nem pular", async () => {
    const id = await novoAtendimento();
    for (let i = 1; i <= 12; i++) {
      await enviarMensagem(ctxA, id, { content: `v${i}` });
    }

    const vistas: string[] = [];
    let cursor: number | null = null;
    let restam = true;

    while (restam) {
      const p: Awaited<ReturnType<typeof listarMensagens>> = await listarMensagens(ctxA, id, {
        antesDe: cursor,
        limite: 5,
      });
      if (!p) break;
      vistas.unshift(...p.messages.map((m) => m.content));
      restam = p.hasMore;
      cursor = p.oldestSequence;
    }

    expect(vistas).toEqual(Array.from({ length: 12 }, (_, i) => `v${i + 1}`));
    expect(new Set(vistas).size).toBe(12);
  });

  it("35. VOLUME: 5.000 mensagens não são carregadas de uma vez", async () => {
    const id = await novoAtendimento();

    // Inserção em massa direta: passar por `enviarMensagem` 5.000 vezes
    // testaria a velocidade do teste, não a da consulta.
    await withTenant(A.id, async (tx) => {
      await tx.$executeRaw`
        INSERT INTO messages (id, tenant_id, conversation_id, sequence, direction,
                              type, content, channel, created_at)
        SELECT gen_random_uuid(), ${A.id}::uuid, ${id}::uuid, g,
               'INBOUND'::message_direction, 'TEXT'::message_type,
               'mensagem de volume ' || g, 'DEV', now()
          FROM generate_series(1, 5000) g
      `;
      await tx.$executeRaw`
        UPDATE conversations SET message_seq = 5000 WHERE id = ${id}::uuid
      `;
    });

    const inicio = Date.now();
    const pagina = await listarMensagens(ctxA, id, { limite: 40 });
    const decorrido = Date.now() - inicio;

    expect(pagina?.messages).toHaveLength(40);
    expect(pagina?.hasMore).toBe(true);
    // Última página = últimas mensagens.
    expect(pagina?.messages[39]?.sequence).toBe(5000);
    // Um teto generoso: o que se quer provar é que não há varredura das
    // 5.000, e não medir o hardware de quem roda a suíte.
    expect(decorrido, `demorou ${decorrido}ms`).toBeLessThan(3000);

    // E a lista de atendimentos continua respondendo com esse volume.
    const inicioLista = Date.now();
    const lista = await listarAtendimentos(ctxA, "todos");
    expect(Date.now() - inicioLista).toBeLessThan(5000);
    const naLista = lista.find((c) => c.id === id);
    expect(naLista?.unreadCount).toBe(5000);
    expect(naLista?.preview?.content).toContain("mensagem de volume");
  });
});

/* 27 ─ nota interna nunca vira mensagem ─────────────────────────────── */

describe("nota interna", () => {
  it("27. nota NUNCA aparece entre as mensagens", async () => {
    const id = await novoAtendimento();
    await criarNota(ctxA, id, "SEGREDO: cliente atrasado, cobrar antes de enviar.");
    await enviarMensagem(ctxA, id, { content: "Bom dia!" });

    const pagina = await listarMensagens(ctxA, id);

    expect(pagina?.messages).toHaveLength(1);
    expect(pagina?.messages[0]?.content).toBe("Bom dia!");
    for (const m of pagina?.messages ?? []) {
      expect(m.content).not.toContain("SEGREDO");
    }

    // E continua existindo do lado de dentro.
    const notas = await listarNotas(ctxA, id);
    expect(notas.some((n) => n.content.includes("SEGREDO"))).toBe(true);
  });

  it("nenhuma mensagem de saída pode ter nascido de uma nota", async () => {
    const id = await novoAtendimento();
    await criarNota(ctxA, id, "conteúdo interno");

    const saida = await withTenant(A.id, async (tx) =>
      tx.message.count({ where: { conversationId: id, direction: "OUTBOUND" } }),
    );
    // Criar nota não produz mensagem nenhuma. É estrutural: são tabelas
    // diferentes, e não existe conversão entre elas.
    expect(saida).toBe(0);
  });

  it("ESTRUTURAL: o módulo de notas não conhece o de mensagens", async () => {
    // A garantia real não é este teste — é não haver caminho de código. O
    // teste existe para que criar esse caminho exija apagá-lo, o que é
    // uma decisão visível numa revisão.
    const { readFile } = await import("node:fs/promises");
    const fonte = await readFile("src/server/conversations/notes.ts", "utf8");

    expect(fonte).not.toMatch(/enviarMensagem|messages\/service|registrarMensagemRecebida/);
  });
});

/* 30-31 ─ filtro e contadores ───────────────────────────────────────── */

describe("não lidos e contadores", () => {
  it("30/31. o filtro e o contador enxergam mensagem, não só abertura", async () => {
    const zelador = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `zelador@${A.slug}.teste`,
      name: "Zelador Teste",
      fiscaleUid: `zelador.${A.slug}`,
    });
    const ctxZ = contexto(A, "AGENT" as Role, zelador.membershipId);

    const id = await novoAtendimento();
    const m = await registrarMensagemRecebida(A.id, id, { content: "chegou agora" });
    if (!m.ok) throw new Error("preparo falhou");

    const antes = await listarAtendimentos(ctxZ, "nao-lidos");
    expect(antes.some((c) => c.id === id)).toBe(true);
    const contagemAntes = await contarPorFiltro(ctxZ);

    await marcarVisualizadas(ctxZ, id, [m.value.id]);

    const depois = await listarAtendimentos(ctxZ, "nao-lidos");
    expect(depois.some((c) => c.id === id)).toBe(false);

    const contagemDepois = await contarPorFiltro(ctxZ);
    expect(contagemDepois.naoLidos).toBe(contagemAntes.naoLidos - 1);
  });

  it("mensagem NOVA devolve a conversa para não lidos", async () => {
    const leitor = await provisionMembership({
      tenantId: A.id,
      role: "AGENT" as Role,
      email: `leitor@${A.slug}.teste`,
      name: "Leitor Teste",
      fiscaleUid: `leitor.${A.slug}`,
    });
    const ctxL = contexto(A, "AGENT" as Role, leitor.membershipId);

    const id = await novoAtendimento();
    const m1 = await registrarMensagemRecebida(A.id, id, { content: "primeira" });
    if (!m1.ok) throw new Error("preparo falhou");
    await marcarVisualizadas(ctxL, id, [m1.value.id]);
    expect((await listarAtendimentos(ctxL, "nao-lidos")).some((c) => c.id === id)).toBe(false);

    await registrarMensagemRecebida(A.id, id, { content: "segunda" });

    // O erro que a regra antiga cometeria: esconder um caso que acabou de
    // receber mensagem só porque a pessoa já tinha aberto o atendimento.
    expect((await listarAtendimentos(ctxL, "nao-lidos")).some((c) => c.id === id)).toBe(true);
  });

  it("a lista traz a prévia da última mensagem", async () => {
    const id = await novoAtendimento();
    await registrarMensagemRecebida(A.id, id, { content: "primeira coisa" });
    await enviarMensagem(ctxA, id, { content: "última coisa dita" });

    const lista = await listarAtendimentos(ctxA, "todos");
    const c = lista.find((x) => x.id === id);
    expect(c?.preview?.content).toBe("última coisa dita");
    expect(c?.preview?.direction).toBe("OUTBOUND");
  });
});

/* 32 ─ RBAC ─────────────────────────────────────────────────────────── */

describe("RBAC", () => {
  it("32. VIEWER lê mensagem e não envia", async () => {
    const id = await novoAtendimento();
    await enviarMensagem(ctxA, id, { content: "para o viewer ler" });

    const ctxViewer = contexto(A, "VIEWER" as Role);

    const pagina = await listarMensagens(ctxViewer, id);
    expect(pagina?.messages.length).toBeGreaterThan(0);

    await expect(enviarMensagem(ctxViewer, id, { content: "não deveria" })).rejects.toThrow(
      /messages.send/,
    );
  });

  it("AGENT envia", async () => {
    const id = await novoAtendimento();
    const r = await enviarMensagem(contexto(A, "AGENT" as Role), id, { content: "do agente" });
    expect(r.ok).toBe(true);
  });
});
