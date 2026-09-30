/**
 * Notificações: quem é avisado, com que texto, em qual aparelho.
 *
 * Os testes que carregam o arquivo — os que precisam FALHAR quando o
 * defeito correspondente entrar:
 *
 *   - quem enviou NÃO é avisado da própria ação;
 *   - atendimento com dono avisa o dono, e só ele;
 *   - a prévia desligada não deixa o texto do cliente sair;
 *   - inscrição do tenant A é invisível para o tenant B;
 *   - membership desativado para de receber, sem ninguém fazer nada.
 */
import { generateKeyPairSync } from "node:crypto";

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { provisionMembership } from "@/server/admin/provisioning";
import type { AuthContext } from "@/server/auth/context";
import { criarAtendimento, transferirAtendimento } from "@/server/conversations/service";
import { registrarMensagemRecebida } from "@/server/messages/service";
import { limparCoalescing, registrar, JANELA_MS } from "@/server/notifications/coalescing";
import {
  avisoDeMensagem,
  descricaoDeMidia,
  serializar,
  truncar,
  LIMITE_PREVIA,
} from "@/server/notifications/payload";
import {
  emHorarioSilencioso,
  minhasPreferencias,
  salvarPreferencias,
  validar,
  PADRAO,
} from "@/server/notifications/preferences";
import { decidir, avisar } from "@/server/notifications/service";
import {
  hashDoEndpoint,
  inscrever,
  meusDispositivos,
  revogar,
  rotuloDoAparelho,
} from "@/server/notifications/subscriptions";
import { entrar, limparPresenca, sair } from "@/server/realtime/presence";
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

/** Uma segunda pessoa no tenant A — a colega que recebe transferência. */
let carlos: { membershipId: string; identityId: string };
let ctxCarlos: AuthContext;

function contexto(
  t: TenantFixture,
  membershipId?: string,
  role: Role = "OWNER" as Role,
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

/**
 * Um endpoint por aparelho, com chave P-256 DE VERDADE.
 *
 * Sessenta e cinco bytes de lixo passariam pela validação de tamanho e
 * quebrariam só na hora de cifrar — que é onde este projeto quase caiu:
 * o primeiro fixture usava `0x04` seguido de bytes repetidos, e a entrega
 * falhava com "chave inválida" num teste que media outra coisa. Chave de
 * teste tem que ser chave.
 */
const CHAVE_DO_NAVEGADOR = (() => {
  const par = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const jwk = par.publicKey.export({ format: "jwk" }) as { x: string; y: string };
  const crua = Buffer.concat([
    Buffer.from([4]),
    Buffer.from(jwk.x, "base64url"),
    Buffer.from(jwk.y, "base64url"),
  ]);
  return crua.toString("base64url");
})();

function aparelho(nome: string) {
  return {
    endpoint: `https://push.exemplo.test/${nome}`,
    p256dh: CHAVE_DO_NAVEGADOR,
    auth: Buffer.alloc(16, 9).toString("base64url"),
  };
}

/**
 * Um atendimento gravado DIRETO, sem passar pelo servico.
 *
 * `criarAtendimento` dispara o aviso de atribuicao em segundo plano — de
 * proposito, e e isso que se quer na producao. Num teste que mede a
 * ENTREGA, esse aviso paralelo consome o resultado que o teste ia medir.
 * Aqui a conversa e so o cenario, entao ela nasce sem barulho.
 */
async function conversaCrua(tenantId: string, assignedMembershipId: string): Promise<string> {
  return withTenant(tenantId, async (tx) => {
    const c = await tx.conversation.create({
      data: { tenantId, channel: "DEV", status: "NEW", assignedMembershipId },
      select: { id: true },
    });
    return c.id;
  });
}

/**
 * O primeiro alvo, com erro claro quando nao ha nenhum.
 *
 * `alvos[0]` com `noUncheckedIndexedAccess` e `possibly undefined`, e
 * `alvos[0]!` esconderia justamente o caso que interessa: lista vazia
 * quando se esperava alguem.
 */
function primeiroAlvo<T>(alvos: T[]): T {
  expect(alvos.length).toBeGreaterThan(0);
  const a = alvos[0];
  if (!a) throw new Error("nenhum destinatario");
  return a;
}

/** Evita saída pela rede: nenhum teste daqui deve tocar num provedor. */
function semRede(): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = (() =>
    Promise.resolve(new Response(null, { status: 201 }))) as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
}

beforeAll(async () => {
  A = await criarTenantComPessoa("na", "OWNER" as Role);
  B = await criarTenantComPessoa("nb", "OWNER" as Role);
  ctxA = contexto(A);
  ctxB = contexto(B);

  const c = await provisionMembership({
    tenantId: A.id,
    role: "AGENT" as Role,
    email: `carlos.${A.slug}@teste`,
    name: "Carlos Souza",
    fiscaleUid: `carlos.${A.slug}`,
  });
  carlos = { membershipId: c.membershipId, identityId: c.identityId };
  ctxCarlos = contexto(A, c.membershipId, "AGENT" as Role);
});

afterEach(() => {
  limparCoalescing();
  limparPresenca();
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

/* ══ 1. preferências individuais ═════════════════════════════════════ */

describe("preferências", () => {
  it("quem nunca configurou nada segue o padrão — e o padrão esconde a prévia", async () => {
    const p = await minhasPreferencias(ctxCarlos);
    expect(p).toEqual(PADRAO);
    expect(p.newMessages).toBe(true);
    // O único padrão restritivo, e é de propósito: a notificação aparece
    // na tela bloqueada.
    expect(p.showPreview).toBe(false);
  });

  it("são POR PESSOA: mexer nas minhas não mexe nas do colega", async () => {
    await salvarPreferencias(ctxA, { showPreview: true, soundEnabled: false });

    expect((await minhasPreferencias(ctxA)).showPreview).toBe(true);
    expect((await minhasPreferencias(ctxCarlos)).showPreview).toBe(false);
    expect((await minhasPreferencias(ctxCarlos)).soundEnabled).toBe(true);

    await salvarPreferencias(ctxA, { showPreview: false, soundEnabled: true });
  });

  it("salvar duas vezes não cria duas linhas", async () => {
    await salvarPreferencias(ctxCarlos, { newConversations: false });
    await salvarPreferencias(ctxCarlos, { newConversations: true });

    const n = await withTenant(A.id, async (tx) =>
      tx.notificationPreference.count({ where: { membershipId: carlos.membershipId } }),
    );
    expect(n).toBe(1);
  });

  it("campo desconhecido é RECUSADO, não ignorado em silêncio", () => {
    const r = validar({ newMessages: true, inventado: 1 });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.code).toBe("CAMPO_DESCONHECIDO");
  });

  it("horário silencioso pela metade é recusado", () => {
    expect(validar({ quietHoursStart: 1320 }).ok).toBe(false);
    expect(validar({ quietHoursStart: 1320, quietHoursEnd: 420 }).ok).toBe(true);
    expect(validar({ quietHoursStart: null, quietHoursEnd: null }).ok).toBe(true);
    expect(validar({ quietHoursStart: 1500, quietHoursEnd: 10 }).ok).toBe(false);
  });

  it("o horário silencioso atravessa a meia-noite", () => {
    // 22h às 7h. A comparação ingênua (inicio <= agora < fim) nunca seria
    // verdadeira aqui, e o silêncio nunca aconteceria.
    const p = { ...PADRAO, quietHoursStart: 22 * 60, quietHoursEnd: 7 * 60 };
    const em = (h: number, m = 0) => new Date(2026, 7, 10, h, m);

    expect(emHorarioSilencioso(p, em(23))).toBe(true);
    expect(emHorarioSilencioso(p, em(3))).toBe(true);
    expect(emHorarioSilencioso(p, em(6, 59))).toBe(true);
    expect(emHorarioSilencioso(p, em(7))).toBe(false);
    expect(emHorarioSilencioso(p, em(14))).toBe(false);
    expect(emHorarioSilencioso(p, em(21, 59))).toBe(false);
  });

  it("janela vazia é 'desligado', não 'silencie o dia inteiro'", () => {
    const p = { ...PADRAO, quietHoursStart: 600, quietHoursEnd: 600 };
    expect(emHorarioSilencioso(p, new Date(2026, 7, 10, 10, 0))).toBe(false);
  });
});

/* ══ 2. texto do aviso e privacidade ═════════════════════════════════ */

describe("o texto do aviso", () => {
  const agora = new Date("2026-08-10T15:00:00Z");
  const conversa = "9f1c2e30-0000-4000-8000-000000000001";

  it("prévia DESLIGADA: diz quem falou, nunca o que falou", () => {
    const a = avisoDeMensagem(
      conversa,
      { cliente: "Empresa ABC", tipo: "TEXT", conteudo: "Meu faturamento foi R$ 200 mil" },
      false,
      agora,
    );

    expect(a.title).toBe("ELO");
    expect(a.body).toBe("Empresa ABC entrou em contato.");
    expect(serializar(a)).not.toContain("200 mil");
    expect(serializar(a)).not.toContain("faturamento");
  });

  it("prévia LIGADA: mostra um trecho, e só se a pessoa pediu", () => {
    const a = avisoDeMensagem(
      conversa,
      { cliente: "Empresa ABC", tipo: "TEXT", conteudo: "bom dia, preciso da guia" },
      true,
      agora,
    );
    expect(a.body).toBe("Empresa ABC: bom dia, preciso da guia");
  });

  it("o trecho é truncado dentro do limite, incluindo as reticências", () => {
    const longo = "x".repeat(500);
    const a = avisoDeMensagem(
      conversa,
      { cliente: "Padaria do João", tipo: "TEXT", conteudo: longo },
      true,
      agora,
    );

    const trecho = a.body.replace("Padaria do João: ", "");
    expect(trecho.length).toBeLessThanOrEqual(LIMITE_PREVIA);
    expect(trecho.endsWith("…")).toBe(true);
    expect(truncar("curto")).toBe("curto");
  });

  it("mídia vira DESCRIÇÃO — nunca o arquivo e nunca o nome dele", () => {
    // "Balancete_MARIA_SILVA_2026.pdf" conta a mesma história que a prévia
    // existe para esconder.
    for (const [tipo, esperado] of [
      ["IMAGE", "Empresa ABC enviou uma foto."],
      ["DOCUMENT", "Empresa ABC enviou um documento."],
      ["AUDIO", "Empresa ABC enviou um áudio."],
      ["VOICE", "Empresa ABC enviou uma mensagem de voz."],
    ] as const) {
      const a = avisoDeMensagem(
        conversa,
        { cliente: "Empresa ABC", tipo, conteudo: "Balancete_MARIA_SILVA.pdf" },
        true,
        agora,
      );
      expect(a.body).toBe(esperado);
      expect(a.body).not.toContain("MARIA_SILVA");
    }
    expect(descricaoDeMidia("TEXT")).toBeNull();
  });

  it("contato não identificado não vira string vazia", () => {
    const a = avisoDeMensagem(conversa, { cliente: null, tipo: "TEXT", conteudo: "oi" }, false, agora);
    expect(a.body).toBe("Um contato entrou em contato.");
  });

  it("o payload carrega o mínimo — nada de histórico, anexo ou dado fiscal", () => {
    const a = avisoDeMensagem(
      conversa,
      { cliente: "Empresa ABC", tipo: "TEXT", conteudo: "oi" },
      false,
      agora,
    );
    expect(Object.keys(JSON.parse(serializar(a)) as object).sort()).toEqual([
      "at",
      "body",
      "c",
      "tag",
      "t",
      "title",
    ].sort());
  });

  it("a tag agrupa por CONVERSA, não globalmente", () => {
    // Uma tag global esconderia o segundo cliente atrás do primeiro.
    const a = avisoDeMensagem(conversa, { cliente: "A", tipo: "TEXT", conteudo: "1" }, false, agora);
    const b = avisoDeMensagem(
      "9f1c2e30-0000-4000-8000-000000000002",
      { cliente: "B", tipo: "TEXT", conteudo: "2" },
      false,
      agora,
    );
    expect(a.tag).not.toBe(b.tag);
    expect(a.tag).toContain(conversa);
  });
});

/* ══ 3. agrupamento ══════════════════════════════════════════════════ */

describe("agrupamento", () => {
  it("vinte mensagens seguidas não viram vinte notificações", () => {
    const t0 = 1_000_000;
    const enviados: number[] = [];

    for (let i = 0; i < 20; i += 1) {
      // Uma por segundo: tudo dentro da janela.
      const d = registrar("m1", "c1", t0 + i * 1000);
      if (d.enviar) enviados.push(d.agrupadas);
    }

    expect(enviados).toEqual([1]);
  });

  it("passada a janela, o próximo aviso leva o total acumulado", () => {
    const t0 = 2_000_000;
    registrar("m2", "c2", t0);
    for (let i = 1; i <= 4; i += 1) registrar("m2", "c2", t0 + i * 1000);

    const depois = registrar("m2", "c2", t0 + JANELA_MS + 1);
    expect(depois.enviar).toBe(true);
    expect(depois.agrupadas).toBe(5);
  });

  it("agrupa por PESSOA e por CONVERSA — dois clientes são dois avisos", () => {
    const t0 = 3_000_000;
    expect(registrar("m3", "cA", t0).enviar).toBe(true);
    expect(registrar("m3", "cB", t0).enviar).toBe(true);
    expect(registrar("m4", "cA", t0).enviar).toBe(true);
  });

  it("o aviso agrupado diz o número no texto", () => {
    const a = avisoDeMensagem(
      "9f1c2e30-0000-4000-8000-000000000001",
      { cliente: "Empresa ABC", tipo: "TEXT", conteudo: "segredo", agrupadas: 5 },
      // Mesmo com a prévia LIGADA o texto some no agrupado: cinco trechos
      // não cabem numa linha, e escolher um seria arbitrário.
      true,
      new Date(),
    );
    expect(a.body).toBe("Empresa ABC — 5 novas mensagens");
    expect(a.body).not.toContain("segredo");
  });
});

/* ══ 4. quem é notificado ════════════════════════════════════════════ */

describe("destinatários", () => {
  it("o próprio remetente NÃO recebe aviso da própria ação", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const alvos = await decidir({
      tipo: "MENSAGEM_RECEBIDA",
      tenantId: A.id,
      conversationId: id,
      autorMembershipId: A.membership.membershipId,
      messageType: "TEXT",
      conteudo: "resposta que eu mesmo escrevi",
    });

    expect(alvos.map((a) => a.membershipId)).not.toContain(A.membership.membershipId);
  });

  it("com responsável, avisa O RESPONSÁVEL — e só ele", async () => {
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: carlos.membershipId,
    });

    const alvos = await decidir({
      tipo: "MENSAGEM_RECEBIDA",
      tenantId: A.id,
      conversationId: id,
      autorMembershipId: null,
      messageType: "TEXT",
      conteudo: "oi",
    });

    expect(alvos).toHaveLength(1);
    expect(primeiroAlvo(alvos).membershipId).toBe(carlos.membershipId);
    expect(primeiroAlvo(alvos).notificar).toBe(true);
  });

  it("sem responsável, avisa a fila elegível", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const alvos = await decidir({
      tipo: "MENSAGEM_RECEBIDA",
      tenantId: A.id,
      conversationId: id,
      autorMembershipId: null,
      messageType: "TEXT",
      conteudo: "oi",
    });

    const ids = alvos.map((a) => a.membershipId);
    expect(ids).toContain(A.membership.membershipId);
    expect(ids).toContain(carlos.membershipId);
  });

  it("a fila de uma ÁREA só avisa quem é daquela área", async () => {
    const fiscal = await criarDepartamentoTeste(A.id, `fisc-${A.slug}`, "Fiscal");
    await withTenant(A.id, async (tx) => {
      await tx.departmentMembership.create({
        data: { tenantId: A.id, departmentId: fiscal.id, membershipId: carlos.membershipId },
      });
    });

    const { id } = await criarAtendimento(A.id, { assignedDepartmentId: fiscal.id });
    const alvos = await decidir({
      tipo: "ATENDIMENTO_NOVO",
      tenantId: A.id,
      conversationId: id,
      autorMembershipId: null,
    });

    expect(alvos.map((a) => a.membershipId)).toEqual([carlos.membershipId]);
  });

  it("quem está indisponível para atribuição não entra na fila", async () => {
    await withTenant(A.id, async (tx) => {
      await tx.membership.update({
        where: { id: carlos.membershipId },
        data: { availableForAssignment: false },
      });
    });

    const { id } = await criarAtendimento(A.id, {});
    const alvos = await decidir({
      tipo: "MENSAGEM_RECEBIDA",
      tenantId: A.id,
      conversationId: id,
      autorMembershipId: null,
      messageType: "TEXT",
      conteudo: "oi",
    });

    expect(alvos.map((a) => a.membershipId)).not.toContain(carlos.membershipId);

    await withTenant(A.id, async (tx) => {
      await tx.membership.update({
        where: { id: carlos.membershipId },
        data: { availableForAssignment: true },
      });
    });
  });

  it("transferência avisa QUEM RECEBEU, com o nome de quem transferiu", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const antes = await withTenant(A.id, async (tx) =>
      tx.conversation.findUniqueOrThrow({ where: { id }, select: { version: true } }),
    );

    const r = await transferirAtendimento(ctxA, id, {
      toMembershipId: carlos.membershipId,
      expectedVersion: antes.version,
    });
    expect(r.ok).toBe(true);

    const alvos = await decidir({
      tipo: "ATENDIMENTO_ATRIBUIDO",
      tenantId: A.id,
      conversationId: id,
      destinoMembershipId: carlos.membershipId,
      autorMembershipId: A.membership.membershipId,
      transferencia: true,
    });

    expect(alvos).toHaveLength(1);
    expect(primeiroAlvo(alvos).membershipId).toBe(carlos.membershipId);
    expect(primeiroAlvo(alvos).notificar).toBe(true);
    expect(primeiroAlvo(alvos).aviso.body).toContain("transferiu");
  });

  it("assumir para si mesmo não gera aviso", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const alvos = await decidir({
      tipo: "ATENDIMENTO_ATRIBUIDO",
      tenantId: A.id,
      conversationId: id,
      destinoMembershipId: carlos.membershipId,
      autorMembershipId: carlos.membershipId,
      transferencia: false,
    });
    expect(alvos).toEqual([]);
  });

  it("MEMBERSHIP DESATIVADO para de receber — sem ninguém fazer nada", async () => {
    // É este o mecanismo servidor-side que o requisito pede: sair do Elo
    // não apaga a inscrição do navegador, mas perder o vínculo corta a
    // entrega na origem.
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: carlos.membershipId,
    });

    await withTenant(A.id, async (tx) => {
      await tx.membership.update({ where: { id: carlos.membershipId }, data: { active: false } });
    });

    const alvos = await decidir({
      tipo: "MENSAGEM_RECEBIDA",
      tenantId: A.id,
      conversationId: id,
      autorMembershipId: null,
      messageType: "TEXT",
      conteudo: "oi",
    });
    expect(alvos).toEqual([]);

    await withTenant(A.id, async (tx) => {
      await tx.membership.update({ where: { id: carlos.membershipId }, data: { active: true } });
    });
  });

  it("a preferência desligada tira a pessoa da entrega", async () => {
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: carlos.membershipId,
    });

    await salvarPreferencias(ctxCarlos, { newMessages: false });
    const alvos = await decidir({
      tipo: "MENSAGEM_RECEBIDA",
      tenantId: A.id,
      conversationId: id,
      autorMembershipId: null,
      messageType: "TEXT",
      conteudo: "oi",
    });

    // Continua sendo candidata — o que muda é a entrega.
    expect(alvos).toHaveLength(1);
    expect(primeiroAlvo(alvos).notificar).toBe(false);

    await salvarPreferencias(ctxCarlos, { newMessages: true });
  });
});

/* ══ 5. presença: não avisar duas vezes ══════════════════════════════ */

describe("presença", () => {
  it("com a conversa aberta e a janela visível, NÃO sai push", async () => {
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: carlos.membershipId,
    });

    const chave = entrar(A.id, {
      membershipId: carlos.membershipId,
      conversationId: id,
      visivel: true,
    });

    try {
      const alvos = await decidir({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });
      expect(primeiroAlvo(alvos).notificar).toBe(false);
    } finally {
      sair(chave);
    }
  });

  it("com o Elo aberto em OUTRA conversa, também não sai push", async () => {
    // A tela mostra crachá e toca o som; uma notificação do sistema por
    // cima seria o alerta em dobro.
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: carlos.membershipId,
    });

    const chave = entrar(A.id, {
      membershipId: carlos.membershipId,
      conversationId: "9f1c2e30-0000-4000-8000-0000000000ff",
      visivel: true,
    });

    try {
      const alvos = await decidir({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });
      expect(primeiroAlvo(alvos).notificar).toBe(false);
    } finally {
      sair(chave);
    }
  });

  it("com a aba ESCONDIDA, o push volta a sair", async () => {
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: carlos.membershipId,
    });

    const chave = entrar(A.id, {
      membershipId: carlos.membershipId,
      conversationId: id,
      visivel: false,
    });

    try {
      const alvos = await decidir({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });
      expect(primeiroAlvo(alvos).notificar).toBe(true);
    } finally {
      sair(chave);
    }
  });

  it("a presença de OUTRA pessoa não suprime o meu aviso", async () => {
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: carlos.membershipId,
    });

    const chave = entrar(A.id, {
      membershipId: A.membership.membershipId,
      conversationId: id,
      visivel: true,
    });

    try {
      const alvos = await decidir({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });
      expect(primeiroAlvo(alvos).membershipId).toBe(carlos.membershipId);
      expect(primeiroAlvo(alvos).notificar).toBe(true);
    } finally {
      sair(chave);
    }
  });

  it("a presença é por TENANT: a mesma conversa em outro escritório não conta", async () => {
    const { id } = await criarAtendimento(A.id, {
      assignedMembershipId: carlos.membershipId,
    });

    const chave = entrar(B.id, {
      membershipId: carlos.membershipId,
      conversationId: id,
      visivel: true,
    });

    try {
      const alvos = await decidir({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });
      expect(primeiroAlvo(alvos).notificar).toBe(true);
    } finally {
      sair(chave);
    }
  });
});

/* ══ 6. inscrições e dispositivos ════════════════════════════════════ */

describe("inscrições de dispositivo", () => {
  it("cria a inscrição e devolve só o hash — nunca o endpoint", async () => {
    const r = await inscrever(ctxA, {
      ...aparelho("notebook-escritorio"),
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36",
    });

    expect(r.ok).toBe(true);
    if (!r.ok) return;

    expect(r.value.endpointHash).toBe(hashDoEndpoint(aparelho("notebook-escritorio").endpoint));
    expect(JSON.stringify(r.value)).not.toContain("push.exemplo.test");
    expect(r.value.deviceLabel).toBe("Chrome no Windows");
  });

  it("um funcionário pode ter vários aparelhos", async () => {
    await inscrever(ctxA, { ...aparelho("casa"), userAgent: "Firefox/130 Linux" });
    await inscrever(ctxA, { ...aparelho("celular"), userAgent: "Android Chrome/140" });

    const meus = await meusDispositivos(ctxA);
    expect(meus.length).toBeGreaterThanOrEqual(3);
  });

  it("reinscrever o MESMO navegador atualiza a linha em vez de criar outra", async () => {
    const antes = (await meusDispositivos(ctxA)).length;
    await inscrever(ctxA, aparelho("casa"));
    expect((await meusDispositivos(ctxA)).length).toBe(antes);
  });

  it("revogar UM aparelho não toca nos outros", async () => {
    const antes = await meusDispositivos(ctxA);
    const alvo = hashDoEndpoint(aparelho("celular").endpoint);

    const r = await revogar(ctxA, alvo);
    expect(r.revogadas).toBe(1);

    const depois = await meusDispositivos(ctxA);
    expect(depois.map((d) => d.endpointHash)).not.toContain(alvo);
    expect(depois).toHaveLength(antes.length - 1);
    // Os outros continuam recebendo.
    expect(depois.map((d) => d.endpointHash)).toContain(
      hashDoEndpoint(aparelho("casa").endpoint),
    );
  });

  it("revogar duas vezes não é erro", async () => {
    const alvo = hashDoEndpoint(aparelho("celular").endpoint);
    expect((await revogar(ctxA, alvo)).revogadas).toBe(0);
  });

  it("ninguém revoga o aparelho de um colega", async () => {
    await inscrever(ctxCarlos, aparelho("carlos-notebook"));
    const alvo = hashDoEndpoint(aparelho("carlos-notebook").endpoint);

    // Chamada com a sessão de OUTRA pessoa do mesmo tenant.
    expect((await revogar(ctxA, alvo)).revogadas).toBe(0);
    expect((await meusDispositivos(ctxCarlos)).map((d) => d.endpointHash)).toContain(alvo);
  });

  it("endpoint http:// é recusado", async () => {
    const r = await inscrever(ctxA, {
      endpoint: "http://push.exemplo.test/inseguro",
      p256dh: aparelho("x").p256dh,
      auth: aparelho("x").auth,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.code).toBe("ENDPOINT_INVALIDO");
  });

  it("chaves com tamanho errado são recusadas", async () => {
    const r = await inscrever(ctxA, {
      endpoint: "https://push.exemplo.test/tamanho",
      p256dh: "curta",
      auth: "curta",
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.code).toBe("CHAVES_INVALIDAS");
  });

  it("o rótulo do aparelho não guarda o User-Agent inteiro", () => {
    expect(
      rotuloDoAparelho(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.7000.1 Edg/140",
      ),
    ).toBe("Edge no Windows");
    expect(rotuloDoAparelho("Mozilla/5.0 (Linux; Android 14; SM-A546E) Chrome/140")).toBe(
      "Chrome no Android",
    );
    expect(rotuloDoAparelho(null)).toBeNull();
  });
});

/* ══ 7. isolamento entre tenants ═════════════════════════════════════ */

describe("isolamento entre tenants", () => {
  it("a inscrição do tenant A é INVISÍVEL para o tenant B", async () => {
    await inscrever(ctxA, aparelho("so-do-A"));
    const hash = hashDoEndpoint(aparelho("so-do-A").endpoint);

    const doB = await withTenant(B.id, async (tx) =>
      tx.pushSubscription.findMany({ where: { endpointHash: hash } }),
    );
    expect(doB).toEqual([]);

    // E B também não consegue revogá-la.
    expect((await revogar(ctxB, hash)).revogadas).toBe(0);

    const doA = await withTenant(A.id, async (tx) =>
      tx.pushSubscription.findMany({ where: { endpointHash: hash, revokedAt: null } }),
    );
    expect(doA).toHaveLength(1);
  });

  it("o tenant B nunca entrega push num aparelho do tenant A", async () => {
    const restaurar = semRede();
    try {
      const { id } = await criarAtendimento(B.id, {
        assignedMembershipId: B.membership.membershipId,
      });

      // O responsável do B não tem aparelho nenhum; o do A tem vários.
      const r = await avisar({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: B.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });

      expect(r.notificados).toBe(1);
      expect(r.enviados).toBe(0);
    } finally {
      restaurar();
    }
  });

  it("a máquina compartilhada: dentro do escritório, o novo dono assume a linha", async () => {
    // Um perfil do Chrome só, duas pessoas do MESMO escritório. A UNIQUE
    // de (tenant, endpoint) faz a reinscrição virar UPDATE, e o dono passa
    // a ser quem entrou agora. Uma linha, um dono — nunca duas vivas.
    const compartilhado = aparelho("maquina-do-balcao");
    const hash = hashDoEndpoint(compartilhado.endpoint);

    await inscrever(ctxA, compartilhado);
    await inscrever(ctxCarlos, compartilhado);

    const linhas = await withTenant(A.id, async (tx) =>
      tx.pushSubscription.findMany({ where: { endpointHash: hash } }),
    );
    expect(linhas).toHaveLength(1);
    expect(primeiroAlvo(linhas).membershipId).toBe(carlos.membershipId);
    expect(primeiroAlvo(linhas).revokedAt).toBeNull();

    // E o aparelho sai da lista de quem estava antes.
    expect((await meusDispositivos(ctxA)).map((d) => d.endpointHash)).not.toContain(hash);
    expect((await meusDispositivos(ctxCarlos)).map((d) => d.endpointHash)).toContain(hash);
  });

  it("entre escritórios, cada um só enxerga a própria linha", async () => {
    // O mesmo endpoint pode existir nos dois — e nenhum vê o do outro.
    // Quem desfaz o de verdade é o navegador, com `unsubscribe()`, que
    // mata o endpoint no provedor: a linha órfã recebe 410 na primeira
    // tentativa e é revogada sozinha. Ver a migration 20260810230000.
    const compartilhado = aparelho("maquina-entre-escritorios");
    const hash = hashDoEndpoint(compartilhado.endpoint);

    await inscrever(ctxA, compartilhado);
    await inscrever(ctxB, compartilhado);

    const noA = await withTenant(A.id, async (tx) =>
      tx.pushSubscription.findMany({ where: { endpointHash: hash } }),
    );
    const noB = await withTenant(B.id, async (tx) =>
      tx.pushSubscription.findMany({ where: { endpointHash: hash } }),
    );

    expect(noA).toHaveLength(1);
    expect(noB).toHaveLength(1);
    expect(primeiroAlvo(noA).id).not.toBe(primeiroAlvo(noB).id);
    expect(primeiroAlvo(noA).tenantId).toBe(A.id);
    expect(primeiroAlvo(noB).tenantId).toBe(B.id);
  });
});

/* ══ 8. entrega, falha e expiração ═══════════════════════════════════ */

describe("entrega", () => {
  it("404 do provedor REVOGA a inscrição — não fica tentando para sempre", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = (() =>
      Promise.resolve(new Response(null, { status: 410 }))) as typeof fetch;

    try {
      const id = await conversaCrua(A.id, carlos.membershipId);

      const morto = aparelho("aparelho-morto");
      await inscrever(ctxCarlos, morto);

      const r = await avisar({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });

      expect(r.revogadas).toBeGreaterThanOrEqual(1);

      const linha = await withTenant(A.id, async (tx) =>
        tx.pushSubscription.findFirstOrThrow({
          where: { endpointHash: hashDoEndpoint(morto.endpoint) },
        }),
      );
      expect(linha.revokedAt).not.toBeNull();
      expect(linha.revokedReason).toBe("EXPIRED");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("falha temporária conta, mas não revoga", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = (() =>
      Promise.resolve(new Response(null, { status: 503 }))) as typeof fetch;

    try {
      const id = await conversaCrua(A.id, carlos.membershipId);

      const instavel = aparelho("provedor-instavel");
      await inscrever(ctxCarlos, instavel);

      const r = await avisar({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });

      expect(r.falhas).toBeGreaterThanOrEqual(1);

      const linha = await withTenant(A.id, async (tx) =>
        tx.pushSubscription.findFirstOrThrow({
          where: { endpointHash: hashDoEndpoint(instavel.endpoint) },
        }),
      );
      expect(linha.revokedAt).toBeNull();
      expect(linha.failureCount).toBeGreaterThanOrEqual(1);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("O PUSH NÃO BLOQUEIA A MENSAGEM: provedor fora, mensagem gravada", async () => {
    // A regra que governa a fase inteira. Se este teste falhar, o produto
    // trocou "avisar" por "conseguir conversar".
    const original = globalThis.fetch;
    globalThis.fetch = (() => Promise.reject(new Error("rede fora"))) as typeof fetch;

    try {
      const { id } = await criarAtendimento(A.id, {
        assignedMembershipId: carlos.membershipId,
      });
      await inscrever(ctxCarlos, aparelho("vai-falhar"));

      const r = await registrarMensagemRecebida(A.id, id, {
        content: "o cliente escreveu mesmo com o push fora",
      });

      expect(r.ok).toBe(true);
      if (r.ok) expect(r.value.sequence).toBe(1);

      const gravadas = await withTenant(A.id, async (tx) =>
        tx.message.count({ where: { conversationId: id } }),
      );
      expect(gravadas).toBe(1);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("chave que não é um ponto P-256 falha na ENTREGA, sem derrubar nada", async () => {
    // Sessenta e cinco bytes de lixo passam pela validação de tamanho da
    // inscrição — só a cifra descobre que não é uma chave. O resultado
    // precisa ser uma falha contada, e não uma exceção subindo.
    const restaurar = semRede();
    try {
      // Pessoa dedicada: com os aparelhos do Carlos no meio, o teste
      // mediria a soma de entregas boas com a falha.
      const so = await provisionMembership({
        tenantId: A.id,
        role: "AGENT" as Role,
        email: `chave.${A.slug}@teste`,
        name: "Chave Ruim",
        fiscaleUid: `chave.${A.slug}`,
      });
      const ctxSo = contexto(A, so.membershipId, "AGENT" as Role);

      const id = await conversaCrua(A.id, so.membershipId);
      await inscrever(ctxSo, {
        endpoint: "https://push.exemplo.test/chave-que-nao-e-chave",
        p256dh: Buffer.concat([Buffer.from([4]), Buffer.alloc(64, 7)]).toString("base64url"),
        auth: Buffer.alloc(16, 9).toString("base64url"),
      });

      const r = await avisar({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });

      expect(r.notificados).toBe(1);
      expect(r.enviados).toBe(0);
      expect(r.falhas).toBeGreaterThanOrEqual(1);
    } finally {
      restaurar();
    }
  });

  it("sem inscrição nenhuma, avisar não faz nada e não quebra", async () => {
    const restaurar = semRede();
    try {
      const semAparelho = await provisionMembership({
        tenantId: A.id,
        role: "AGENT" as Role,
        email: `sem.${A.slug}@teste`,
        name: "Sem Aparelho",
        fiscaleUid: `sem.${A.slug}`,
      });

      const { id } = await criarAtendimento(A.id, {
        assignedMembershipId: semAparelho.membershipId,
      });

      const r = await avisar({
        tipo: "MENSAGEM_RECEBIDA",
        tenantId: A.id,
        conversationId: id,
        autorMembershipId: null,
        messageType: "TEXT",
        conteudo: "oi",
      });

      expect(r.notificados).toBe(1);
      expect(r.enviados).toBe(0);
      expect(r.falhas).toBe(0);
    } finally {
      restaurar();
    }
  });
});
