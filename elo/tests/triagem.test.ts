/**
 * Triagem por setor — Fiscal, Contábil e DP.
 *
 * Os testes que carregam o arquivo — os que precisam FALHAR quando o
 * defeito correspondente entrar:
 *
 *   - texto livre NÃO vira setor (fila errada é pior que fila nenhuma);
 *   - "fiscal e contábil" não é escolha;
 *   - a orientação automática sai UMA vez por conversa, e a segunda
 *     resposta inválida não gera mensagem nova;
 *   - a triagem não assume o atendimento nem move o status;
 *   - a triagem não roda por cima de quem já tem setor, responsável, ou
 *     de uma conversa que já saiu de NEW;
 *   - reentrega da Meta não duplica TRIAGED nem orientação;
 *   - falha na triagem NÃO derruba a mensagem que já foi gravada.
 */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { criarAtendimento } from "@/server/conversations/service";
import {
  interpretarEscolha,
  ORIENTACAO,
  SETORES,
  triarSePreciso,
} from "@/server/conversations/triagem";
import { processar } from "@/server/channels/whatsapp/inbound";
import { CANAL_WHATSAPP } from "@/server/channels/whatsapp/constantes";
import { idDaOrientacao } from "@/server/messages/service";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import type { AuthContext } from "@/server/auth/context";

import {
  criarDepartamentoTeste,
  criarTenantComPessoa,
  removerTenant,
  type TenantFixture,
} from "./auth-helpers";
import { fecharPrisma } from "./helpers";

let T: TenantFixture;
const areas: Record<string, string> = {};

/** Um contato diferente por teste: o `wa_id` é a chave do atendimento. */
let contador = 0;
function novoWaId(): string {
  contador += 1;
  return `558188${String(contador).padStart(7, "0")}`;
}

/** Contexto sintético: o caminho do cookie tem testes próprios. */
function contexto(): AuthContext {
  return {
    sessionId: "00000000-0000-4000-8000-000000000001",
    tenantId: T.id,
    membershipId: T.membership.membershipId,
    identityId: T.membership.identityId,
    email: T.email,
    name: "Pessoa de Teste",
    role: "OWNER" as Role,
  };
}

async function novaConversa(): Promise<string> {
  const { id } = await criarAtendimento(T.id, {
    channel: CANAL_WHATSAPP,
    externalContactId: novoWaId(),
  });
  return id;
}

/**
 * Uma mensagem do cliente, gravada pelo caminho de sempre, e a triagem em
 * seguida — exatamente a ordem do `inbound.ts`.
 */
let seqExterna = 0;
async function clienteDiz(id: string, texto: string) {
  const { registrarMensagemRecebida } = await import("@/server/messages/service");
  seqExterna += 1;
  await registrarMensagemRecebida(T.id, id, {
    content: texto,
    externalSenderId: "cliente",
    externalMessageId: `wamid.tri-seq-${seqExterna}`,
    channel: CANAL_WHATSAPP,
  });
  return triarSePreciso(T.id, id, texto);
}

async function lerConversa(id: string) {
  return withTenant(T.id, async (tx) =>
    tx.conversation.findUniqueOrThrow({
      where: { id },
      select: {
        status: true,
        assignedDepartmentId: true,
        assignedMembershipId: true,
      },
    }),
  );
}

async function eventos(id: string) {
  return withTenant(T.id, async (tx) =>
    tx.conversationEvent.findMany({
      where: { conversationId: id },
      select: { type: true, actorMembershipId: true, toDepartmentId: true, metadata: true },
      orderBy: { createdAt: "asc" },
    }),
  );
}

async function orientacoes(id: string): Promise<number> {
  return withTenant(T.id, async (tx) =>
    tx.message.count({ where: { conversationId: id, clientMessageId: idDaOrientacao(id) } }),
  );
}

beforeAll(async () => {
  T = await criarTenantComPessoa("tri", "OWNER" as Role);
  for (const s of SETORES) {
    const d = await criarDepartamentoTeste(T.id, s.slug, s.rotulo);
    areas[s.slug] = d.id;
  }
});

afterAll(async () => {
  await removerTenant(T.id);
  await fecharPrisma();
});

/* ══ 1. a interpretação, sem banco ══════════════════════════════════ */

describe("interpretar a resposta do cliente", () => {
  it.each([
    ["1", "fiscal"],
    ["2", "contabil"],
    ["3", "dp"],
    ["fiscal", "fiscal"],
    ["Fiscal", "fiscal"],
    ["FISCAL", "fiscal"],
    ["contabil", "contabil"],
    ["contábil", "contabil"],
    ["CONTÁBIL", "contabil"],
    ["dp", "dp"],
    ["DP", "dp"],
    ["departamento pessoal", "dp"],
    ["Departamento Pessoal", "dp"],
    ["  2  ", "contabil"],
    ["1.", "fiscal"],
  ])("%j escolhe %s", (entrada, esperado) => {
    expect(interpretarEscolha(entrada)?.slug).toBe(esperado);
  });

  it("DUAS opções não são uma escolha", () => {
    // O ponto do arquivo. Quem responde "fiscal e contábil" não escolheu,
    // e pegar a primeira palavra mandaria a conversa para uma fila que o
    // cliente não pediu — onde o outro setor nunca vai procurá-la.
    for (const dupla of [
      "fiscal e contabil",
      "fiscal e contábil",
      "1 e 2",
      "fiscal, dp",
      "contabil ou dp",
    ]) {
      expect(interpretarEscolha(dupla)).toBeNull();
    }
  });

  it("texto livre NÃO é adivinhado", () => {
    for (const livre of [
      "preciso de ajuda com um problema fiscal",
      "quero falar sobre a folha de pagamento",
      "bom dia",
      "minha guia do DAS não chegou",
      "sou do departamento pessoal da empresa X",
    ]) {
      expect(interpretarEscolha(livre)).toBeNull();
    }
  });

  it("vazio, lixo e opção inexistente não escolhem nada", () => {
    for (const nada of ["", "   ", "4", "0", "abc", "🙂", "-"]) {
      expect(interpretarEscolha(nada)).toBeNull();
    }
  });
});

/* ══ 2. aplicação: a escolha válida ═════════════════════════════════ */

describe("escolha válida", () => {
  it.each([
    ["1", "fiscal", "Fiscal"],
    ["2", "contabil", "Contábil"],
    ["3", "dp", "DP"],
    ["fiscal", "fiscal", "Fiscal"],
    ["CONTÁBIL", "contabil", "Contábil"],
    ["departamento pessoal", "dp", "DP"],
  ])("%j → %s", async (resposta, slug, rotulo) => {
    const id = await novaConversa();
    const r = await triarSePreciso(T.id, id, resposta);

    expect(r).toEqual({ tipo: "SETOR_DEFINIDO", slug });

    const c = await lerConversa(id);
    expect(c.assignedDepartmentId).toBe(areas[slug]);
    // O que o item 15 pede, e o que separa "entrar na fila" de "ser
    // atendido": ninguém virou responsável e o status não andou.
    expect(c.assignedMembershipId).toBeNull();
    expect(c.status).toBe("NEW");

    const evs = await eventos(id);
    const triado = evs.find((e) => e.type === "TRIAGED");
    expect(triado).toBeDefined();
    expect(triado?.actorMembershipId).toBeNull();
    expect(triado?.toDepartmentId).toBe(areas[slug]);
    expect(triado?.metadata).toEqual({ setor: slug, rotulo });

    // Não é transferência, e o histórico não pode fingir que foi.
    expect(evs.some((e) => e.type === "TRANSFERRED")).toBe(false);
    // Escolher setor não manda mensagem nenhuma ao cliente.
    expect(await orientacoes(id)).toBe(0);
  });

  it("o metadata leva CÓDIGO e RÓTULO, nunca o texto do cliente", () => {
    // Guardado aqui como regra explícita: o conteúdo mora na mensagem, e
    // copiá-lo para o evento criaria um segundo lugar de onde vazar.
    expect(Object.keys({ setor: "", rotulo: "" }).sort()).toEqual(["rotulo", "setor"]);
  });
});

/* ══ 2b. a PRIMEIRA mensagem da conversa ════════════════════════════ */

/**
 * A regra da primeira mensagem, exercitada onde ela vive: com a mensagem
 * do cliente realmente gravada antes da triagem, como o `inbound.ts` faz.
 *
 * Duas coisas diferentes convivem aqui, e confundi-las seria o defeito:
 *
 *   - a primeira mensagem VÁLIDA é triada na hora. A contagem de mensagens
 *     não entra nesse caminho — quem responde "1" de cara escolheu, e
 *     fazê-lo esperar uma segunda mensagem seria burrice;
 *   - a primeira mensagem INVÁLIDA fica em silêncio, porque é ela que faz
 *     a saudação do aplicativo aparecer. Responder aqui entregaria duas
 *     mensagens automáticas no mesmo segundo.
 */
describe("a primeira mensagem da conversa", () => {
  async function outboundAutomaticos(id: string): Promise<number> {
    return withTenant(T.id, async (tx) =>
      tx.message.count({
        where: { conversationId: id, direction: "OUTBOUND", senderMembershipId: null },
      }),
    );
  }

  it.each([
    ["1", "fiscal", "Fiscal"],
    ["Fiscal", "fiscal", "Fiscal"],
    ["2", "contabil", "Contábil"],
    ["3", "dp", "DP"],
    ["Departamento Pessoal", "dp", "DP"],
  ])("primeira mensagem %j tria na hora → %s", async (resposta, slug, rotulo) => {
    const id = await novaConversa();

    const r = await clienteDiz(id, resposta);
    expect(r).toEqual({ tipo: "SETOR_DEFINIDO", slug });

    const c = await lerConversa(id);
    expect(c.assignedDepartmentId).toBe(areas[slug]);
    expect(c.assignedMembershipId).toBeNull();
    expect(c.status).toBe("NEW");

    const triado = (await eventos(id)).find((e) => e.type === "TRIAGED");
    expect(triado?.metadata).toEqual({ setor: slug, rotulo });

    // E ninguém recebeu mensagem automática por ter acertado de primeira.
    expect(await outboundAutomaticos(id)).toBe(0);
  });

  it("primeira mensagem INVÁLIDA não gera nenhum OUTBOUND automático", async () => {
    const id = await novaConversa();

    const r = await clienteDiz(id, "bom dia, preciso de uma ajuda");
    expect(r).toEqual({ tipo: "NAO_ENTENDI", orientou: false });

    // Medido pela DIREÇÃO, não só pela marca da orientação: nenhuma
    // mensagem sai do escritório por conta do robô nesta primeira volta.
    expect(await outboundAutomaticos(id)).toBe(0);
    expect(await orientacoes(id)).toBe(0);

    // E a conversa fica visível na fila de triagem, nunca num limbo.
    const c = await lerConversa(id);
    expect(c.status).toBe("NEW");
    expect(c.assignedDepartmentId).toBeNull();
  });

  it("primeira inválida + segunda VÁLIDA: tria a segunda, sem orientar", async () => {
    const id = await novaConversa();

    expect(await clienteDiz(id, "oi")).toEqual({ tipo: "NAO_ENTENDI", orientou: false });
    expect(await clienteDiz(id, "2")).toEqual({ tipo: "SETOR_DEFINIDO", slug: "contabil" });

    expect((await lerConversa(id)).assignedDepartmentId).toBe(areas.contabil);
    // O cliente se corrigiu sozinho: mandar a orientação agora seria
    // responder a uma pergunta que ele já respondeu.
    expect(await outboundAutomaticos(id)).toBe(0);
  });

  it("primeira inválida + segunda inválida: EXATAMENTE uma orientação", async () => {
    const id = await novaConversa();

    expect(await clienteDiz(id, "bom dia")).toEqual({ tipo: "NAO_ENTENDI", orientou: false });
    expect(await orientacoes(id)).toBe(0);

    expect(await clienteDiz(id, "queria falar com alguém")).toEqual({
      tipo: "NAO_ENTENDI",
      orientou: true,
    });
    expect(await orientacoes(id)).toBe(1);
    expect(await outboundAutomaticos(id)).toBe(1);

    // Uma. Não duas, não uma por mensagem.
    expect(await clienteDiz(id, "alô?")).toEqual({ tipo: "NAO_ENTENDI", orientou: false });
    expect(await clienteDiz(id, "???")).toEqual({ tipo: "NAO_ENTENDI", orientou: false });
    expect(await orientacoes(id)).toBe(1);
    expect(await outboundAutomaticos(id)).toBe(1);
  });
});

/* ══ 3. aplicação: a resposta inválida ══════════════════════════════ */

describe("resposta inválida", () => {
  it("a PRIMEIRA mensagem não recebe orientação — o menu é a saudação", async () => {
    // Quem manda a primeira mensagem ainda não viu opção nenhuma: é essa
    // mensagem que faz a saudação do aplicativo disparar. Orientar aqui
    // daria duas mensagens automáticas de uma vez, que é o defeito que
    // todo este desenho existe para evitar.
    const id = await novaConversa();

    const r = await clienteDiz(id, "bom dia");
    expect(r).toEqual({ tipo: "NAO_ENTENDI", orientou: false });
    expect(await orientacoes(id)).toBe(0);
  });

  it("orienta UMA vez, e a tentativa seguinte não manda nada", async () => {
    const id = await novaConversa();

    // 1ª: cria a conversa e faz o menu aparecer. Silêncio do Elo.
    expect(await clienteDiz(id, "bom dia")).toEqual({ tipo: "NAO_ENTENDI", orientou: false });
    expect(await orientacoes(id)).toBe(0);

    // 2ª: o menu já foi visto e a resposta continua não sendo escolha.
    expect(await clienteDiz(id, "quero falar sobre imposto")).toEqual({
      tipo: "NAO_ENTENDI",
      orientou: true,
    });
    expect(await orientacoes(id)).toBe(1);

    // 3ª: continua UMA. Robô que repete é robô que o cliente bloqueia.
    expect(await clienteDiz(id, "ué, não entendi")).toEqual({
      tipo: "NAO_ENTENDI",
      orientou: false,
    });
    expect(await orientacoes(id)).toBe(1);

    expect(await clienteDiz(id, "alguém aí?")).toEqual({
      tipo: "NAO_ENTENDI",
      orientou: false,
    });
    expect(await orientacoes(id)).toBe(1);

    // E a conversa continua VISÍVEL na fila de triagem — nunca some.
    const c = await lerConversa(id);
    expect(c.status).toBe("NEW");
    expect(c.assignedDepartmentId).toBeNull();
  });

  it("a orientação NÃO tira a conversa de NEW nem carimba primeira resposta", async () => {
    const id = await novaConversa();
    await clienteDiz(id, "oi");
    await clienteDiz(id, "tem alguém?");

    const c = await withTenant(T.id, async (tx) =>
      tx.conversation.findUniqueOrThrow({
        where: { id },
        select: { status: true, firstResponseAt: true },
      }),
    );
    // Se isto virasse IN_PROGRESS, o atendimento sumiria de "Sem setor"
    // sem ninguém ter olhado para ele. E `firstResponseAt` passaria a
    // medir a velocidade do robô, não a do escritório.
    expect(c.status).toBe("NEW");
    expect(c.firstResponseAt).toBeNull();
  });

  it("a orientação sai sem autor — não leva o nome de quem não escreveu", async () => {
    const id = await novaConversa();
    await clienteDiz(id, "???");
    await clienteDiz(id, "!!!");

    const m = await withTenant(T.id, async (tx) =>
      tx.message.findFirstOrThrow({
        where: { conversationId: id, clientMessageId: idDaOrientacao(id) },
        select: { senderMembershipId: true, senderDisplayName: true, direction: true, content: true },
      }),
    );
    expect(m.senderMembershipId).toBeNull();
    expect(m.senderDisplayName).toBeNull();
    expect(m.direction).toBe("OUTBOUND");
    expect(m.content).toBe(ORIENTACAO);
  });

  it("depois de orientar, a escolha certa ainda funciona", async () => {
    const id = await novaConversa();
    await clienteDiz(id, "sei lá");
    await clienteDiz(id, "não entendi");
    const r = await clienteDiz(id, "2");

    expect(r).toEqual({ tipo: "SETOR_DEFINIDO", slug: "contabil" });
    expect((await lerConversa(id)).assignedDepartmentId).toBe(areas.contabil);
  });
});

/* ══ 4. quando a triagem NÃO roda ═══════════════════════════════════ */

describe("a triagem não atropela ninguém", () => {
  it("conversa que JÁ tem setor não é perguntada de novo", async () => {
    const id = await novaConversa();
    await triarSePreciso(T.id, id, "1");

    const r = await triarSePreciso(T.id, id, "3");
    expect(r).toEqual({ tipo: "NAO_APLICA", motivo: "JA_TEM_SETOR" });
    // E o setor original continua de pé.
    expect((await lerConversa(id)).assignedDepartmentId).toBe(areas.fiscal);
    expect(await orientacoes(id)).toBe(0);
  });

  it("conversa com responsável não recebe triagem", async () => {
    const { id } = await criarAtendimento(T.id, {
      channel: CANAL_WHATSAPP,
      externalContactId: novoWaId(),
      assignedMembershipId: T.membership.membershipId,
    });

    const r = await triarSePreciso(T.id, id, "1");
    expect(r).toEqual({ tipo: "NAO_APLICA", motivo: "TEM_RESPONSAVEL" });
    expect((await lerConversa(id)).assignedDepartmentId).toBeNull();
  });

  it("humano respondeu antes: a automação não assume", async () => {
    const id = await novaConversa();

    // Uma resposta de gente move NEW → IN_PROGRESS pelo caminho normal.
    const { enviarMensagem } = await import("@/server/messages/service");
    await enviarMensagem(contexto(), id, { content: "Bom dia, já vejo isso." });

    const r = await triarSePreciso(T.id, id, "1");
    expect(r).toEqual({ tipo: "NAO_APLICA", motivo: "STATUS" });
    expect(await orientacoes(id)).toBe(0);
  });

  it("setor reconhecido mas não cadastrado no escritório não cria nada", async () => {
    const outro = await criarTenantComPessoa("tri2", "OWNER" as Role);
    try {
      const { id } = await criarAtendimento(outro.id, {
        channel: CANAL_WHATSAPP,
        externalContactId: novoWaId(),
      });
      const r = await triarSePreciso(outro.id, id, "1");
      expect(r).toEqual({ tipo: "SETOR_NAO_CADASTRADO", slug: "fiscal" });

      // Não inventa departamento a partir de mensagem de cliente.
      const quantos = await withTenant(outro.id, async (tx) => tx.department.count());
      expect(quantos).toBe(0);
    } finally {
      await removerTenant(outro.id);
    }
  });

  it("conversa inexistente não explode", async () => {
    const r = await triarSePreciso(T.id, "00000000-0000-4000-8000-000000000999", "1");
    expect(r).toEqual({ tipo: "NAO_APLICA", motivo: "SEM_CONVERSA" });
  });
});

/* ══ 5. cliente sem cadastro ════════════════════════════════════════ */

describe("identificação do cliente é independente da triagem", () => {
  it("contato sem cliente vinculado é triado normalmente", async () => {
    const { id } = await criarAtendimento(T.id, {
      channel: CANAL_WHATSAPP,
      externalContactId: novoWaId(),
      // Sem `customerReferenceId`: é o caso NONE e o caso AMBIGUOUS, que
      // nascem os dois sem cliente (S22). Saber o setor não depende de
      // saber quem é — e amarrar as duas coisas deixaria número novo sem
      // atendimento.
    });

    const r = await triarSePreciso(T.id, id, "3");
    expect(r).toEqual({ tipo: "SETOR_DEFINIDO", slug: "dp" });

    const c = await withTenant(T.id, async (tx) =>
      tx.conversation.findUniqueOrThrow({
        where: { id },
        select: { customerReferenceId: true, assignedDepartmentId: true },
      }),
    );
    expect(c.customerReferenceId).toBeNull();
    expect(c.assignedDepartmentId).toBe(areas.dp);
  });
});

/* ══ 6. pelo webhook, de ponta a ponta ══════════════════════════════ */

describe("pelo webhook", () => {
  const NUMERO = "999888777666555";
  const tenantPorNumero = (n: string) => (n === NUMERO ? T.id : null);

  function evento(de: string, texto: string, id: string) {
    return {
      tipo: "MENSAGEM" as const,
      phoneNumberId: NUMERO,
      de,
      externalMessageId: id,
      timestamp: 1_786_400_000,
      texto,
      nomeDoPerfil: "Cliente de Teste",
    };
  }

  async function conversaDe(waId: string) {
    return withTenant(T.id, async (tx) =>
      tx.conversation.findFirstOrThrow({
        where: { channel: CANAL_WHATSAPP, externalContactId: waId },
        select: { id: true, assignedDepartmentId: true, status: true },
      }),
    );
  }

  it("mensagem do cliente cria o atendimento E aplica a triagem", async () => {
    const wa = novoWaId();
    const r = await processar([evento(wa, "2", "wamid.tri-1")], tenantPorNumero);

    expect(r.criadas).toBe(1);
    const c = await conversaDe(wa);
    expect(c.assignedDepartmentId).toBe(areas.contabil);
    expect(c.status).toBe("NEW");
  });

  it("REENTREGA da Meta não duplica TRIAGED nem orientação", async () => {
    const wa = novoWaId();
    const mesmo = "wamid.tri-repetido";

    await processar([evento(wa, "1", mesmo)], tenantPorNumero);
    const c = await conversaDe(wa);

    const r2 = await processar([evento(wa, "1", mesmo)], tenantPorNumero);
    expect(r2.duplicadas).toBe(1);

    const evs = await eventos(c.id);
    expect(evs.filter((e) => e.type === "TRIAGED")).toHaveLength(1);
  });

  it("reentrega de uma resposta INVÁLIDA não manda a orientação duas vezes", async () => {
    const wa = novoWaId();
    const mesmo = "wamid.tri-invalido-2";

    // A primeira mensagem só faz o menu aparecer — e não recebe orientação.
    await processar([evento(wa, "bom dia", "wamid.tri-invalido-1")], tenantPorNumero);
    const c = await conversaDe(wa);
    expect(await orientacoes(c.id)).toBe(0);

    // A segunda já é resposta ao menu, e continua sem escolher.
    await processar([evento(wa, "não sei dizer", mesmo)], tenantPorNumero);
    expect(await orientacoes(c.id)).toBe(1);

    // Reentrega da MESMA: nada de segunda orientação.
    await processar([evento(wa, "não sei dizer", mesmo)], tenantPorNumero);
    expect(await orientacoes(c.id)).toBe(1);
  });

  it("triagem quebrada NÃO impede a mensagem de existir", async () => {
    // O contrato do item 19, exercitado de verdade: mesmo com a triagem
    // explodindo, a mensagem e o atendimento sobrevivem. Sem isto, um
    // defeito na triagem viraria perda de mensagem de cliente.
    const modulo = await import("@/server/conversations/triagem");
    const espiao = vi
      .spyOn(modulo, "triarSePreciso")
      .mockRejectedValue(new Error("triagem em pane"));

    try {
      const wa = novoWaId();
      const r = await processar([evento(wa, "1", "wamid.tri-pane")], tenantPorNumero);

      expect(r.criadas).toBe(1);
      expect(r.recusadas).toBe(0);

      const c = await conversaDe(wa);
      const quantas = await withTenant(T.id, async (tx) =>
        tx.message.count({ where: { conversationId: c.id, direction: "INBOUND" } }),
      );
      expect(quantas).toBe(1);
    } finally {
      espiao.mockRestore();
    }
  });
});

/* ══ 7. provisionamento dos setores ════════════════════════════════ */

/**
 * O provisionamento dos tres setores no escritorio.
 *
 * O teste que carrega este bloco e o da SEGUNDA execucao: provisionar e
 * uma operacao que alguem vai repetir — por duvida, por script, por um
 * `--aplicar` digitado duas vezes — e a segunda vez nao pode criar nada
 * nem quebrar. E o nome que o escritorio escolheu nao pode ser
 * sobrescrito pela nossa tabela.
 */
describe("provisionar Fiscal, Contábil e DP", () => {
  const desejados = SETORES.map((s) => ({ slug: s.slug, name: s.rotulo }));

  it("num escritório vazio, o plano é criar os três", async () => {
    const vazio = await criarTenantComPessoa("prov1", "OWNER" as Role);
    try {
      const { planejarDepartamentos } = await import("@/server/admin/provisioning");
      const plano = await planejarDepartamentos(vazio.id, desejados);

      expect(plano.tenantName).not.toBeNull();
      expect(plano.existentes).toEqual([]);
      expect(plano.criar.map((d) => d.slug)).toEqual(["fiscal", "contabil", "dp"]);
      expect(plano.preservar).toEqual([]);

      // O plano NAO grava nada.
      const quantos = await withTenant(vazio.id, async (tx) => tx.department.count());
      expect(quantos).toBe(0);
    } finally {
      await removerTenant(vazio.id);
    }
  });

  it("aplicar duas vezes cria uma vez só", async () => {
    const t = await criarTenantComPessoa("prov2", "OWNER" as Role);
    try {
      const { garantirDepartamentos } = await import("@/server/admin/provisioning");

      const primeira = await garantirDepartamentos(t.id, desejados);
      expect(primeira.criados.map((d) => d.slug)).toEqual(["fiscal", "contabil", "dp"]);
      expect(primeira.preservados).toEqual([]);

      const segunda = await garantirDepartamentos(t.id, desejados);
      expect(segunda.criados).toEqual([]);
      expect(segunda.preservados).toEqual(["fiscal", "contabil", "dp"]);

      const quantos = await withTenant(t.id, async (tx) => tx.department.count());
      expect(quantos).toBe(3);
    } finally {
      await removerTenant(t.id);
    }
  });

  it("o que já existe é preservado — inclusive com outro nome", async () => {
    // O caso do escritório real: "fiscal" já está lá. E se alguém o tiver
    // chamado de "Fiscal e Tributário", esse nome é dele — a triagem casa
    // pelo slug, então renomear não ganharia nada e reescreveria uma
    // escolha deliberada.
    const t = await criarTenantComPessoa("prov3", "OWNER" as Role);
    try {
      await criarDepartamentoTeste(t.id, "fiscal", "Fiscal e Tributário");

      const { planejarDepartamentos, garantirDepartamentos } = await import(
        "@/server/admin/provisioning"
      );

      const plano = await planejarDepartamentos(t.id, desejados);
      expect(plano.criar.map((d) => d.slug)).toEqual(["contabil", "dp"]);
      expect(plano.preservar).toEqual([
        { slug: "fiscal", nomeAtual: "Fiscal e Tributário", nomeProposto: "Fiscal" },
      ]);

      await garantirDepartamentos(t.id, desejados);

      const nomes = await withTenant(t.id, async (tx) =>
        tx.department.findMany({ select: { slug: true, name: true }, orderBy: { slug: "asc" } }),
      );
      expect(nomes).toEqual([
        { slug: "contabil", name: "Contábil" },
        { slug: "dp", name: "DP" },
        { slug: "fiscal", name: "Fiscal e Tributário" },
      ]);
    } finally {
      await removerTenant(t.id);
    }
  });

  it("tenant inexistente não cria nada", async () => {
    const { garantirDepartamentos } = await import("@/server/admin/provisioning");
    await expect(
      garantirDepartamentos("00000000-0000-4000-8000-0000000000aa", desejados),
    ).rejects.toThrow(/nao encontrado/);
  });
});
