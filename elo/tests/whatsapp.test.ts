/**
 * O canal WhatsApp: assinatura, interpretação, entrada, saída e status.
 *
 * Os testes que carregam o arquivo — os que precisam FALHAR quando o
 * defeito correspondente entrar:
 *
 *   - payload com assinatura errada NÃO vira mensagem;
 *   - o tenant vem da CONFIGURAÇÃO; um `phone_number_id` desconhecido é
 *     recusado, e um payload não consegue escolher o escritório;
 *   - a mesma mensagem reentregue pela Meta continua sendo UMA;
 *   - três mensagens simultâneas do mesmo contato dão UM atendimento;
 *   - status não anda para trás.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { criarAtendimento } from "@/server/conversations/service";
import {
  limparCacheDeNumeros,
  tenantDoNumero,
} from "@/server/channels/whatsapp/config";
import { processar, waIdParaE164 } from "@/server/channels/whatsapp/inbound";
import { interpretar } from "@/server/channels/whatsapp/payload";
import { assinar, conferirAssinatura } from "@/server/channels/whatsapp/signature";
import {
  comAssinatura,
  entregar,
  enviarTexto,
  TENTATIVAS,
} from "@/server/channels/whatsapp/outbound";
import { CANAL_WHATSAPP } from "@/server/channels/whatsapp/constantes";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import { criarTenantComPessoa, removerTenant, type TenantFixture } from "./auth-helpers";
import { fecharPrisma } from "./helpers";

let A: TenantFixture;
let B: TenantFixture;

const NUMERO_A = "111111111111111";
const NUMERO_B = "222222222222222";
const SEGREDO = "app-secret-de-teste-1234567890";

/** Um cliente diferente por teste: o `wa_id` é a chave do atendimento. */
let contador = 0;
function novoWaId(): string {
  contador += 1;
  return `558199${String(contador).padStart(7, "0")}`;
}

/* ─── payloads ───────────────────────────────────────────────────────── */

function payloadMensagem(
  phoneNumberId: string,
  de: string,
  texto: string,
  id = `wamid.${Math.random().toString(36).slice(2)}`,
): { corpo: unknown; id: string } {
  return {
    id,
    corpo: {
      object: "whatsapp_business_account",
      entry: [
        {
          id: "WABA_ID",
          changes: [
            {
              field: "messages",
              value: {
                messaging_product: "whatsapp",
                metadata: { display_phone_number: "5581999999999", phone_number_id: phoneNumberId },
                contacts: [{ profile: { name: "Maria da Padaria" }, wa_id: de }],
                messages: [
                  {
                    from: de,
                    id,
                    timestamp: "1786400000",
                    type: "text",
                    text: { body: texto },
                  },
                ],
              },
            },
          ],
        },
      ],
    },
  };
}

function payloadStatus(
  phoneNumberId: string,
  externalMessageId: string,
  status: string,
  erro?: { code: number; title: string },
): unknown {
  return {
    object: "whatsapp_business_account",
    entry: [
      {
        id: "WABA_ID",
        changes: [
          {
            field: "messages",
            value: {
              messaging_product: "whatsapp",
              metadata: { display_phone_number: "5581999999999", phone_number_id: phoneNumberId },
              statuses: [
                {
                  id: externalMessageId,
                  status,
                  timestamp: "1786400100",
                  recipient_id: "5581988887777",
                  ...(erro ? { errors: [{ code: erro.code, title: erro.title }] } : {}),
                },
              ],
            },
          },
        ],
      },
    ],
  };
}

beforeAll(async () => {
  A = await criarTenantComPessoa("wa", "OWNER" as Role);
  B = await criarTenantComPessoa("wb", "OWNER" as Role);

  process.env.ELO_WHATSAPP_NUMBERS = JSON.stringify({
    [NUMERO_A]: { tenantId: A.id },
    [NUMERO_B]: { tenantId: B.id },
  });
  limparCacheDeNumeros();
});

afterAll(async () => {
  delete process.env.ELO_WHATSAPP_NUMBERS;
  limparCacheDeNumeros();
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

/* ══ 1. assinatura ══════════════════════════════════════════════════ */

describe("assinatura do webhook", () => {
  const corpo = JSON.stringify({ object: "whatsapp_business_account", entry: [] });

  it("aceita a assinatura que a Meta produziria", () => {
    expect(conferirAssinatura(corpo, assinar(corpo, SEGREDO), SEGREDO)).toEqual({ valida: true });
  });

  it("RECUSA quando UM byte do corpo muda", () => {
    // É o ponto do arquivo: quem não tem o App Secret não consegue
    // produzir a assinatura de um corpo que ele escolheu.
    const assinatura = assinar(corpo, SEGREDO);
    const adulterado = corpo.replace("entry", "entrY");
    expect(conferirAssinatura(adulterado, assinatura, SEGREDO)).toEqual({
      valida: false,
      motivo: "NAO_CONFERE",
    });
  });

  it("RECUSA assinatura feita com outro segredo", () => {
    expect(conferirAssinatura(corpo, assinar(corpo, "outro-segredo"), SEGREDO)).toEqual({
      valida: false,
      motivo: "NAO_CONFERE",
    });
  });

  it("falha FECHADA: sem segredo configurado, nada é válido", () => {
    // Configuração incompleta não pode virar porta aberta.
    expect(conferirAssinatura(corpo, assinar(corpo, SEGREDO), "")).toEqual({
      valida: false,
      motivo: "SEM_SEGREDO",
    });
  });

  it("recusa cabeçalho ausente, vazio ou malformado — sem lançar", () => {
    // Um `throw` aqui seria um jeito de derrubar a rota com um cabeçalho
    // curto: `timingSafeEqual` explode com buffers de tamanhos diferentes.
    expect(conferirAssinatura(corpo, null, SEGREDO).valida).toBe(false);
    expect(conferirAssinatura(corpo, "", SEGREDO).valida).toBe(false);
    expect(conferirAssinatura(corpo, "abc", SEGREDO)).toEqual({ valida: false, motivo: "FORMATO" });
    expect(conferirAssinatura(corpo, "sha256=xyz", SEGREDO)).toEqual({
      valida: false,
      motivo: "FORMATO",
    });
    expect(conferirAssinatura(corpo, "sha256=" + "a".repeat(63), SEGREDO)).toEqual({
      valida: false,
      motivo: "FORMATO",
    });
  });

  it("o corpo é hasheado como veio — reserializar quebraria", () => {
    // A razão de a rota usar `req.text()` e só depois `JSON.parse`.
    const comAcento = JSON.stringify({ texto: "atenção à guia" });
    const assinatura = assinar(comAcento, SEGREDO);
    const reserializado = JSON.stringify(JSON.parse(comAcento));

    expect(conferirAssinatura(comAcento, assinatura, SEGREDO).valida).toBe(true);
    // Neste caso o round-trip até coincide; o que o teste trava é que a
    // conferência é sobre BYTES, e um espaço a mais já muda tudo:
    expect(conferirAssinatura(`${reserializado} `, assinatura, SEGREDO).valida).toBe(false);
  });
});

/* ══ 2. interpretação ═══════════════════════════════════════════════ */

describe("interpretação do payload", () => {
  it("extrai uma mensagem de texto", () => {
    const { corpo, id } = payloadMensagem(NUMERO_A, "5581988887777", "bom dia");
    const { eventos } = interpretar(corpo);

    expect(eventos).toHaveLength(1);
    expect(eventos[0]).toMatchObject({
      tipo: "MENSAGEM",
      phoneNumberId: NUMERO_A,
      de: "5581988887777",
      externalMessageId: id,
      texto: "bom dia",
      nomeDoPerfil: "Maria da Padaria",
    });
  });

  it("IGNORA tipo que esta fase não trata — e não vira mensagem vazia", () => {
    // Uma imagem chegando hoje NÃO pode virar um balão em branco no
    // histórico do cliente. Mídia é a fase 1.8.3.
    const { corpo } = payloadMensagem(NUMERO_A, "5581988887777", "x");
    const c = corpo as { entry: { changes: { value: { messages: unknown[] } }[] }[] };
    c.entry[0]!.changes[0]!.value.messages = [
      { from: "5581988887777", id: "wamid.img", timestamp: "1", type: "image", image: { id: "1" } },
    ];

    const { eventos, ignorados } = interpretar(corpo);
    expect(eventos).toEqual([]);
    expect(ignorados).toContainEqual({ motivo: "tipo_image", quantidade: 1 });
  });

  it("campo que não é `messages` é ignorado, e contado", () => {
    // `history`, `smb_message_echoes` etc. são da fase 1.8.4. Chegando
    // hoje, não podem virar nada — mas precisam aparecer no log.
    const { eventos, ignorados } = interpretar({
      object: "whatsapp_business_account",
      entry: [{ id: "W", changes: [{ field: "history", value: {} }] }],
    });
    expect(eventos).toEqual([]);
    expect(ignorados).toContainEqual({ motivo: "campo_history", quantidade: 1 });
  });

  it("payload de outro produto do mesmo app é ignorado", () => {
    const { eventos, ignorados } = interpretar({ object: "instagram", entry: [] });
    expect(eventos).toEqual([]);
    expect(ignorados).toContainEqual({ motivo: "objeto_de_outro_produto", quantidade: 1 });
  });

  it("lixo não lança exceção — vira contagem", () => {
    // O webhook é contrato de terceiro. Recusar o lote inteiro porque
    // apareceu um campo novo transformaria uma adição da Meta em queda de
    // recebimento.
    for (const entrada of [null, 42, "texto", [], {}, { object: "whatsapp_business_account" }]) {
      expect(() => interpretar(entrada)).not.toThrow();
    }
  });

  it("extrai status, com erro quando houver", () => {
    const { eventos } = interpretar(
      payloadStatus(NUMERO_A, "wamid.abc", "failed", { code: 131047, title: "Re-engagement" }),
    );
    expect(eventos[0]).toMatchObject({
      tipo: "STATUS",
      status: "failed",
      externalMessageId: "wamid.abc",
      erro: { codigo: 131047, titulo: "Re-engagement" },
    });
  });

  it("status desconhecido é ignorado", () => {
    const { eventos, ignorados } = interpretar(payloadStatus(NUMERO_A, "wamid.x", "inventado"));
    expect(eventos).toEqual([]);
    expect(ignorados).toContainEqual({ motivo: "status_inventado", quantidade: 1 });
  });
});

/* ══ 3. o tenant vem da configuração ════════════════════════════════ */

describe("resolução do tenant", () => {
  it("cada número aponta para o seu escritório", () => {
    expect(tenantDoNumero(NUMERO_A)).toBe(A.id);
    expect(tenantDoNumero(NUMERO_B)).toBe(B.id);
  });

  it("número desconhecido não resolve para ninguém", () => {
    expect(tenantDoNumero("999999999999999")).toBeNull();
  });

  it("O PAYLOAD NÃO ESCOLHE O ESCRITÓRIO", async () => {
    // O teste que carrega a seção. Um webhook de um número que não é
    // nosso não pode escrever no banco de ninguém.
    const { corpo } = payloadMensagem("999999999999999", novoWaId(), "sou de outro numero");
    const { eventos } = interpretar(corpo);

    const r = await processar(eventos, tenantDoNumero);
    expect(r).toEqual({ criadas: 0, duplicadas: 0, recusadas: 1 });
  });

  it("tenant configurado com uuid inválido é descartado na leitura", () => {
    process.env.ELO_WHATSAPP_NUMBERS = JSON.stringify({ "333": { tenantId: "nao-e-uuid" } });
    limparCacheDeNumeros();
    expect(tenantDoNumero("333")).toBeNull();

    process.env.ELO_WHATSAPP_NUMBERS = JSON.stringify({
      [NUMERO_A]: { tenantId: A.id },
      [NUMERO_B]: { tenantId: B.id },
    });
    limparCacheDeNumeros();
  });
});

/* ══ 4. entrada ═════════════════════════════════════════════════════ */

describe("mensagem recebida", () => {
  it("cria atendimento e mensagem pelo domínio que já existe", async () => {
    const waId = novoWaId();
    const { corpo } = payloadMensagem(NUMERO_A, waId, "preciso da guia do DAS");

    const r = await processar(interpretar(corpo).eventos, tenantDoNumero);
    expect(r.criadas).toBe(1);

    const conversa = await withTenant(A.id, async (tx) =>
      tx.conversation.findFirstOrThrow({
        where: { channel: CANAL_WHATSAPP, externalContactId: waId },
        select: { id: true, status: true, messages: { select: { content: true, direction: true } } },
      }),
    );

    expect(conversa.status).toBe("NEW");
    expect(conversa.messages).toEqual([
      { content: "preciso da guia do DAS", direction: "INBOUND" },
    ]);
  });

  it("A MESMA MENSAGEM REENTREGUE CONTINUA SENDO UMA", async () => {
    // A Meta reentrega por dias quando não recebe 200. Reentrega não é
    // mensagem nova (S36) — e quem decide é o índice único.
    const waId = novoWaId();
    const { corpo } = payloadMensagem(NUMERO_A, waId, "mensagem unica");
    const eventos = interpretar(corpo).eventos;

    const primeira = await processar(eventos, tenantDoNumero);
    const segunda = await processar(eventos, tenantDoNumero);
    const terceira = await processar(eventos, tenantDoNumero);

    expect(primeira.criadas).toBe(1);
    expect(segunda).toMatchObject({ criadas: 0, duplicadas: 1 });
    expect(terceira).toMatchObject({ criadas: 0, duplicadas: 1 });

    const quantas = await withTenant(A.id, async (tx) =>
      tx.message.count({ where: { conversation: { externalContactId: waId } } }),
    );
    expect(quantas).toBe(1);
  });

  it("mensagens SIMULTÂNEAS do mesmo contato dão UM atendimento", async () => {
    // A corrida real: três mensagens seguidas no WhatsApp chegam em
    // webhooks quase juntos. Sem o índice único, seriam três atendimentos
    // do mesmo cliente na tela.
    const waId = novoWaId();
    const lotes = ["primeira", "segunda", "terceira"].map(
      (t) => interpretar(payloadMensagem(NUMERO_A, waId, t).corpo).eventos,
    );

    await Promise.all(lotes.map((e) => processar(e, tenantDoNumero)));

    const conversas = await withTenant(A.id, async (tx) =>
      tx.conversation.findMany({
        where: { channel: CANAL_WHATSAPP, externalContactId: waId },
        select: { id: true, messages: { select: { sequence: true, direction: true } } },
      }),
    );

    expect(conversas).toHaveLength(1);

    // As três mensagens do cliente têm sequências distintas, sem buraco.
    const recebidas = conversas[0]!.messages.filter((m) => m.direction === "INBOUND");
    expect(recebidas.map((m) => m.sequence).sort((a, b) => a - b)).toEqual([1, 2, 3]);

    // A quarta, quando existe, é a orientação da triagem: nenhuma das três
    // é escolha de setor, e a partir da SEGUNDA o cliente já viu o menu da
    // saudação. Continua sendo UMA, e a sequência segue sem buraco — que é
    // o que este teste protege.
    const seqs = conversas[0]!.messages.map((m) => m.sequence).sort((a, b) => a - b);
    expect(seqs).toEqual(Array.from({ length: seqs.length }, (_, i) => i + 1));
    expect(
      conversas[0]!.messages.filter((m) => m.direction === "OUTBOUND").length,
    ).toBeLessThanOrEqual(1);
  });

  it("a mesma pessoa escrevendo depois volta para o MESMO atendimento", async () => {
    // Inclusive quando o caso já foi resolvido: S42 diz que mensagem nova
    // não abre caso novo — ela sobe na lista e aparece como não lida.
    const waId = novoWaId();
    await processar(interpretar(payloadMensagem(NUMERO_A, waId, "primeira").corpo).eventos, tenantDoNumero);

    const conversa = await withTenant(A.id, async (tx) =>
      tx.conversation.findFirstOrThrow({
        where: { externalContactId: waId },
        select: { id: true },
      }),
    );
    await withTenant(A.id, async (tx) => {
      await tx.conversation.update({ where: { id: conversa.id }, data: { status: "RESOLVED" } });
    });

    await processar(interpretar(payloadMensagem(NUMERO_A, waId, "voltei").corpo).eventos, tenantDoNumero);

    const depois = await withTenant(A.id, async (tx) =>
      tx.conversation.findMany({ where: { externalContactId: waId }, select: { id: true, status: true } }),
    );
    expect(depois).toHaveLength(1);
    expect(depois[0]!.id).toBe(conversa.id);
    // E continua RESOLVIDO: mensagem não reabre sozinha (S42).
    expect(depois[0]!.status).toBe("RESOLVED");
  });

  it("dois escritórios, o mesmo telefone: cada um com o seu atendimento", async () => {
    const waId = novoWaId();
    await processar(interpretar(payloadMensagem(NUMERO_A, waId, "para o A").corpo).eventos, tenantDoNumero);
    await processar(interpretar(payloadMensagem(NUMERO_B, waId, "para o B").corpo).eventos, tenantDoNumero);

    const noA = await withTenant(A.id, async (tx) =>
      tx.conversation.findMany({ where: { externalContactId: waId } }),
    );
    const noB = await withTenant(B.id, async (tx) =>
      tx.conversation.findMany({ where: { externalContactId: waId } }),
    );

    expect(noA).toHaveLength(1);
    expect(noB).toHaveLength(1);
    expect(noA[0]!.id).not.toBe(noB[0]!.id);
  });

  it("o `wa_id` vira E.164 sem adivinhação", () => {
    // S22: número normalizado errado não dá erro — entrega a conversa de
    // um cliente na tela de outro.
    expect(waIdParaE164("5581988887777")).toBe("+5581988887777");
    expect(waIdParaE164("+5581988887777")).toBe("+5581988887777");
    // Nada de completar DDI ou inventar DDD:
    expect(waIdParaE164("988887777")).toBe("+988887777");
  });
});

/* ══ 5. status de entrega ═══════════════════════════════════════════ */

describe("status de entrega", () => {
  async function mensagemDeSaida(externalMessageId: string): Promise<string> {
    const { id: conversationId } = await criarAtendimento(A.id, {
      channel: CANAL_WHATSAPP,
      externalContactId: novoWaId(),
    });

    return withTenant(A.id, async (tx) => {
      await tx.conversation.update({
        where: { id: conversationId },
        data: { messageSeq: { increment: 1 } },
      });
      const m = await tx.message.create({
        data: {
          tenantId: A.id,
          conversationId,
          sequence: 1,
          direction: "OUTBOUND",
          type: "TEXT",
          channel: CANAL_WHATSAPP,
          content: "resposta",
          externalMessageId,
          deliveryStatus: "QUEUED",
        },
        select: { id: true },
      });
      return m.id;
    });
  }

  async function estado(id: string): Promise<string> {
    const m = await withTenant(A.id, async (tx) =>
      tx.message.findUniqueOrThrow({ where: { id }, select: { deliveryStatus: true } }),
    );
    return m.deliveryStatus;
  }

  it("sent → delivered → read", async () => {
    const wamid = `wamid.status.${Date.now()}`;
    const id = await mensagemDeSaida(wamid);

    for (const [status, esperado] of [
      ["sent", "SENT"],
      ["delivered", "DELIVERED"],
      ["read", "READ"],
    ] as const) {
      await processar(interpretar(payloadStatus(NUMERO_A, wamid, status)).eventos, tenantDoNumero);
      expect(await estado(id)).toBe(esperado);
    }
  });

  it("STATUS NÃO ANDA PARA TRÁS", async () => {
    // A Meta não garante ordem: o `read` pode chegar antes do
    // `delivered`. Sem esta trava, o ✓✓ azul viraria ✓ cinza sozinho.
    const wamid = `wamid.ordem.${Date.now()}`;
    const id = await mensagemDeSaida(wamid);

    await processar(interpretar(payloadStatus(NUMERO_A, wamid, "read")).eventos, tenantDoNumero);
    expect(await estado(id)).toBe("READ");

    await processar(interpretar(payloadStatus(NUMERO_A, wamid, "delivered")).eventos, tenantDoNumero);
    await processar(interpretar(payloadStatus(NUMERO_A, wamid, "sent")).eventos, tenantDoNumero);
    expect(await estado(id)).toBe("READ");
  });

  it("o MESMO status repetido não muda nada", async () => {
    const wamid = `wamid.repetido.${Date.now()}`;
    const id = await mensagemDeSaida(wamid);

    const eventos = interpretar(payloadStatus(NUMERO_A, wamid, "delivered")).eventos;
    await processar(eventos, tenantDoNumero);
    await processar(eventos, tenantDoNumero);
    await processar(eventos, tenantDoNumero);

    expect(await estado(id)).toBe("DELIVERED");
  });

  it("`failed` vence mesmo depois de `sent`", async () => {
    // Falha é terminal: uma entrega que falhou depois de aceita continua
    // sendo falha, e não um retrocesso a ignorar.
    const wamid = `wamid.falha.${Date.now()}`;
    const id = await mensagemDeSaida(wamid);

    await processar(interpretar(payloadStatus(NUMERO_A, wamid, "sent")).eventos, tenantDoNumero);
    await processar(
      interpretar(payloadStatus(NUMERO_A, wamid, "failed", { code: 131026, title: "Undeliverable" }))
        .eventos,
      tenantDoNumero,
    );

    expect(await estado(id)).toBe("FAILED");
  });

  it("status de mensagem que não existe não cria nada nem lança", async () => {
    const antes = await withTenant(A.id, async (tx) => tx.message.count());
    const r = await processar(
      interpretar(payloadStatus(NUMERO_A, "wamid.que.nao.existe", "read")).eventos,
      tenantDoNumero,
    );
    const depois = await withTenant(A.id, async (tx) => tx.message.count());

    expect(r.recusadas).toBe(0);
    expect(depois).toBe(antes);
  });

  it("status de OUTRO escritório não alcança a mensagem daqui", async () => {
    const wamid = `wamid.cross.${Date.now()}`;
    const id = await mensagemDeSaida(wamid);

    // O mesmo wamid, mas chegando pelo número do tenant B.
    await processar(interpretar(payloadStatus(NUMERO_B, wamid, "read")).eventos, tenantDoNumero);

    // O RLS impede: a consulta roda no contexto de B e não encontra nada.
    expect(await estado(id)).toBe("QUEUED");
  });
});

/* ══ 6. envio ═══════════════════════════════════════════════════════ */

describe("envio", () => {
  const destino = { phoneNumberId: NUMERO_A, para: "5581988887777" };

  beforeEach(() => {
    process.env.ELO_WHATSAPP_ACCESS_TOKEN = "token-de-teste";
    process.env.ELO_WHATSAPP_ENABLED = "true";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.ELO_WHATSAPP_ACCESS_TOKEN;
    delete process.env.ELO_WHATSAPP_ENABLED;
  });

  function respostas(...seq: (Response | Error)[]) {
    let i = 0;
    const chamadas: Request[] = [];
    const falso = vi.fn((url: string, init: RequestInit) => {
      chamadas.push(new Request(url, init));
      const r = seq[Math.min(i, seq.length - 1)] ?? new Response("{}", { status: 500 });
      i += 1;
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r.clone());
    });
    vi.stubGlobal("fetch", falso);
    return chamadas;
  }

  const aceita = () =>
    new Response(JSON.stringify({ messages: [{ id: "wamid.enviada" }] }), { status: 200 });

  it("manda o corpo que a Cloud API espera, com o token no cabeçalho", async () => {
    const chamadas = respostas(aceita());
    const r = await enviarTexto(destino, "Aline • Fiscal\nBom dia!");

    expect(r).toEqual({ tipo: "ACEITA", externalMessageId: "wamid.enviada" });

    const req = chamadas[0]!;
    expect(req.url).toContain(`/${NUMERO_A}/messages`);
    expect(req.headers.get("authorization")).toBe("Bearer token-de-teste");

    const corpo = (await req.json()) as {
      messaging_product: string;
      to: string;
      type: string;
      text: { body: string; preview_url: boolean };
    };
    expect(corpo.messaging_product).toBe("whatsapp");
    expect(corpo.to).toBe("5581988887777");
    expect(corpo.type).toBe("text");
    expect(corpo.text.body).toBe("Aline • Fiscal\nBom dia!");
    // Link de cliente não vira cartão com imagem de site alheio.
    expect(corpo.text.preview_url).toBe(false);
  });

  it("a assinatura do funcionário vai no CORPO da mensagem", () => {
    // Decisão D1: o contato é o escritório; a pessoa aparece dentro da
    // mensagem. Sem perfil de WhatsApp por funcionário.
    expect(comAssinatura("Bom dia!", "Aline", "Fiscal")).toBe("Aline • Fiscal\nBom dia!");
    // Sem área principal definida, só o nome (D8).
    expect(comAssinatura("Bom dia!", "Aline", null)).toBe("Aline\nBom dia!");
    // Sem nome, só o corpo — melhor que um "• " solto.
    expect(comAssinatura("Bom dia!", null, null)).toBe("Bom dia!");
  });

  it("429 e 5xx são TEMPORÁRIOS", async () => {
    for (const status of [429, 500, 503]) {
      respostas(new Response("{}", { status }));
      expect(await enviarTexto(destino, "x")).toEqual({ tipo: "TEMPORARIA", status });
    }
  });

  it("4xx é PERMANENTE, com código e título — e sem o payload", async () => {
    respostas(
      new Response(
        JSON.stringify({ error: { code: 131047, message: "Re-engagement message" } }),
        { status: 400 },
      ),
    );
    expect(await enviarTexto(destino, "x")).toEqual({
      tipo: "PERMANENTE",
      status: 400,
      codigo: 131047,
      titulo: "Re-engagement message",
    });
  });

  it("rede fora vira resultado, não exceção", async () => {
    respostas(new Error("ENOTFOUND"));
    expect(await enviarTexto(destino, "x")).toMatchObject({ tipo: "SEM_RESPOSTA" });
  });

  it("200 sem `wamid` é tratado como permanente — repetir mandaria em dobro", async () => {
    respostas(new Response(JSON.stringify({ messages: [] }), { status: 200 }));
    expect(await enviarTexto(destino, "x")).toMatchObject({ tipo: "PERMANENTE" });
  });

  it("REPETE o temporário e grava o wamid quando dá certo", async () => {
    const wamid = `wamid.retry.${Date.now()}`;
    const id = await mensagemQueued();

    const chamadas = respostas(
      new Response("{}", { status: 503 }),
      new Response(JSON.stringify({ messages: [{ id: wamid }] }), { status: 200 }),
    );

    const r = await entregar(A.id, id, destino, "texto", async () => {});
    expect(r).toEqual({ tipo: "ACEITA", externalMessageId: wamid });
    expect(chamadas).toHaveLength(2);

    const m = await withTenant(A.id, async (tx) =>
      tx.message.findUniqueOrThrow({
        where: { id },
        select: { deliveryStatus: true, externalMessageId: true },
      }),
    );
    expect(m.deliveryStatus).toBe("SENT");
    expect(m.externalMessageId).toBe(wamid);
  });

  it("NÃO repete o permanente — repetir repete o defeito", async () => {
    const id = await mensagemQueued();
    const chamadas = respostas(
      new Response(JSON.stringify({ error: { code: 131047, message: "fora da janela" } }), {
        status: 400,
      }),
    );

    await entregar(A.id, id, destino, "texto", async () => {});

    expect(chamadas).toHaveLength(1);
    expect(await estadoDaMensagem(id)).toBe("FAILED");
  });

  it("desiste depois do teto de tentativas, e marca FAILED", async () => {
    const id = await mensagemQueued();
    const chamadas = respostas(new Response("{}", { status: 503 }));

    await entregar(A.id, id, destino, "texto", async () => {});

    expect(chamadas).toHaveLength(TENTATIVAS);
    expect(await estadoDaMensagem(id)).toBe("FAILED");
  });

  it("A MENSAGEM SOBREVIVE À FALHA DO CANAL", async () => {
    // A regra que atravessa o projeto, e a mesma do Web Push: o
    // transporte é efeito secundário. Meta fora do ar não pode fazer a
    // mensagem deixar de existir no Elo.
    const id = await mensagemQueued();
    respostas(new Error("rede fora"));

    await entregar(A.id, id, destino, "texto", async () => {});

    const m = await withTenant(A.id, async (tx) =>
      tx.message.findUniqueOrThrow({ where: { id }, select: { content: true, deliveryStatus: true } }),
    );
    expect(m.content).toBe("resposta");
    expect(m.deliveryStatus).toBe("FAILED");
  });

  async function mensagemQueued(): Promise<string> {
    const { id: conversationId } = await criarAtendimento(A.id, {
      channel: CANAL_WHATSAPP,
      externalContactId: novoWaId(),
    });
    return withTenant(A.id, async (tx) => {
      await tx.conversation.update({
        where: { id: conversationId },
        data: { messageSeq: { increment: 1 } },
      });
      const m = await tx.message.create({
        data: {
          tenantId: A.id,
          conversationId,
          sequence: 1,
          direction: "OUTBOUND",
          type: "TEXT",
          channel: CANAL_WHATSAPP,
          content: "resposta",
          deliveryStatus: "QUEUED",
        },
        select: { id: true },
      });
      return m.id;
    });
  }

  async function estadoDaMensagem(id: string): Promise<string> {
    const m = await withTenant(A.id, async (tx) =>
      tx.message.findUniqueOrThrow({ where: { id }, select: { deliveryStatus: true } }),
    );
    return m.deliveryStatus;
  }
});
