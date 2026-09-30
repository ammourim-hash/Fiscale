/**
 * A rota do webhook, por HTTP de verdade.
 *
 * É a ÚNICA rota do Elo sem sessão, e por isso a que mais precisa de
 * teste pela porta real: o que a protege não é `requireAuth`, é a
 * assinatura — e uma assinatura conferida "quase certo" protege nada.
 *
 * O teste que carrega o arquivo: **payload forjado não vira mensagem.**
 * Se ele falhar, qualquer pessoa que descubra a URL escreve no histórico
 * de um cliente e dispara notificação para o escritório inteiro.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { limparCacheDeNumeros } from "@/server/channels/whatsapp/config";
import { CANAL_WHATSAPP } from "@/server/channels/whatsapp/constantes";
import { assinar } from "@/server/channels/whatsapp/signature";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import { criarTenantComPessoa, removerTenant, type TenantFixture } from "./auth-helpers";
import { fecharPrisma } from "./helpers";

let A: TenantFixture;

const NUMERO = "555555555555555";
const SEGREDO = "segredo-do-app-para-teste-http";
const VERIFY = "token-de-verificacao-inventado";

let contador = 0;
function novoWaId(): string {
  contador += 1;
  return `558188${String(contador).padStart(7, "0")}`;
}

function corpoDeMensagem(waId: string, texto: string, id: string): string {
  return JSON.stringify({
    object: "whatsapp_business_account",
    entry: [
      {
        id: "WABA",
        changes: [
          {
            field: "messages",
            value: {
              messaging_product: "whatsapp",
              metadata: { display_phone_number: "5581999999999", phone_number_id: NUMERO },
              contacts: [{ profile: { name: "Cliente" }, wa_id: waId }],
              messages: [
                { from: waId, id, timestamp: "1786400000", type: "text", text: { body: texto } },
              ],
            },
          },
        ],
      },
    ],
  });
}

/** POST na rota, assinando com o segredo pedido. */
async function postar(corpo: string, segredoParaAssinar: string | null): Promise<Response> {
  const { POST } = await import("@/app/api/webhooks/whatsapp/route");
  return POST(
    new NextRequest(
      new Request("http://localhost/api/webhooks/whatsapp", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...(segredoParaAssinar
            ? { "x-hub-signature-256": assinar(corpo, segredoParaAssinar) }
            : {}),
        },
        body: corpo,
      }),
    ),
  );
}

beforeAll(async () => {
  A = await criarTenantComPessoa("wh", "OWNER" as Role);

  process.env.ELO_WHATSAPP_APP_SECRET = SEGREDO;
  process.env.ELO_WHATSAPP_VERIFY_TOKEN = VERIFY;
  process.env.ELO_WHATSAPP_NUMBERS = JSON.stringify({ [NUMERO]: { tenantId: A.id } });
  limparCacheDeNumeros();
});

afterAll(async () => {
  delete process.env.ELO_WHATSAPP_APP_SECRET;
  delete process.env.ELO_WHATSAPP_VERIFY_TOKEN;
  delete process.env.ELO_WHATSAPP_NUMBERS;
  limparCacheDeNumeros();
  await removerTenant(A.id);
  await fecharPrisma();
});

/* ══ verificação (o handshake) ══════════════════════════════════════ */

describe("GET — verificação da Meta", () => {
  async function verificar(params: Record<string, string>): Promise<Response> {
    const { GET } = await import("@/app/api/webhooks/whatsapp/route");
    const url = new URL("http://localhost/api/webhooks/whatsapp");
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    return GET(new NextRequest(new Request(url)));
  }

  it("devolve o desafio em TEXTO PURO", async () => {
    // A Meta compara byte a byte. Um JSON aqui faz a verificação falhar
    // sem dizer por quê — e é o erro que custa uma tarde.
    const r = await verificar({
      "hub.mode": "subscribe",
      "hub.verify_token": VERIFY,
      "hub.challenge": "1158201444",
    });

    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toContain("text/plain");
    expect(await r.text()).toBe("1158201444");
  });

  it("recusa token errado", async () => {
    const r = await verificar({
      "hub.mode": "subscribe",
      "hub.verify_token": "chutado",
      "hub.challenge": "123",
    });
    expect(r.status).toBe(403);
    expect(await r.text()).not.toContain("123");
  });

  it("recusa modo diferente de `subscribe`", async () => {
    const r = await verificar({
      "hub.mode": "unsubscribe",
      "hub.verify_token": VERIFY,
      "hub.challenge": "123",
    });
    expect(r.status).toBe(403);
  });

  it("FALHA FECHADA: sem token configurado, ninguém verifica nada", async () => {
    const guardado = process.env.ELO_WHATSAPP_VERIFY_TOKEN;
    delete process.env.ELO_WHATSAPP_VERIFY_TOKEN;
    try {
      const r = await verificar({
        "hub.mode": "subscribe",
        "hub.verify_token": "",
        "hub.challenge": "123",
      });
      expect(r.status).toBe(403);
    } finally {
      process.env.ELO_WHATSAPP_VERIFY_TOKEN = guardado;
    }
  });
});

/* ══ assinatura ═════════════════════════════════════════════════════ */

describe("POST — a assinatura é a porta", () => {
  afterEach(() => limparCacheDeNumeros());

  it("PAYLOAD FORJADO NÃO VIRA MENSAGEM", async () => {
    const waId = novoWaId();
    const corpo = corpoDeMensagem(waId, "sou um atacante", "wamid.forjado");

    const r = await postar(corpo, "segredo-que-o-atacante-inventou");
    expect(r.status).toBe(403);

    const quantas = await withTenant(A.id, async (tx) =>
      tx.conversation.count({ where: { externalContactId: waId } }),
    );
    expect(quantas).toBe(0);
  });

  it("sem cabeçalho de assinatura, 403 e nada gravado", async () => {
    const waId = novoWaId();
    const r = await postar(corpoDeMensagem(waId, "sem assinatura", "wamid.sem"), null);

    expect(r.status).toBe(403);
    expect(
      await withTenant(A.id, async (tx) =>
        tx.conversation.count({ where: { externalContactId: waId } }),
      ),
    ).toBe(0);
  });

  it("a resposta de recusa não devolve o corpo recebido", async () => {
    // Guardar ou ecoar o payload de quem forja é construir o arquivo do
    // ataque alheio.
    const corpo = corpoDeMensagem(novoWaId(), "texto secreto do atacante", "wamid.eco");
    const r = await postar(corpo, "outro-segredo");
    const texto = await r.text();

    expect(texto).not.toContain("texto secreto do atacante");
    expect(texto).not.toContain(SEGREDO);
  });

  it("assinatura VÁLIDA grava, e responde 200", async () => {
    const waId = novoWaId();
    const corpo = corpoDeMensagem(waId, "bom dia, preciso da guia", "wamid.valida.1");

    const r = await postar(corpo, SEGREDO);
    expect(r.status).toBe(200);

    const conversa = await withTenant(A.id, async (tx) =>
      tx.conversation.findFirstOrThrow({
        where: { channel: CANAL_WHATSAPP, externalContactId: waId },
        select: { messages: { select: { content: true, direction: true } } },
      }),
    );
    expect(conversa.messages).toEqual([
      { content: "bom dia, preciso da guia", direction: "INBOUND" },
    ]);
  });

  it("a MESMA entrega repetida responde 200 e continua uma mensagem só", async () => {
    // A Meta reentrega por dias quando não recebe 200. É o caminho real
    // pelo qual a mesma mensagem chega duas vezes.
    const waId = novoWaId();
    const corpo = corpoDeMensagem(waId, "reentregue", "wamid.reentrega");

    expect((await postar(corpo, SEGREDO)).status).toBe(200);
    expect((await postar(corpo, SEGREDO)).status).toBe(200);
    expect((await postar(corpo, SEGREDO)).status).toBe(200);

    const quantas = await withTenant(A.id, async (tx) =>
      tx.message.count({ where: { conversation: { externalContactId: waId } } }),
    );
    expect(quantas).toBe(1);
  });

  it("JSON quebrado com assinatura válida: 200, e nada quebra", async () => {
    // 200 de propósito: um defeito nosso não pode fazer a Meta reentregar
    // o mesmo evento por dias.
    const corpo = "{ isto nao e json";
    const r = await postar(corpo, SEGREDO);
    expect(r.status).toBe(200);
  });

  it("número de outra WABA é ignorado — e não escreve no nosso banco", async () => {
    const waId = novoWaId();
    const corpo = JSON.stringify({
      object: "whatsapp_business_account",
      entry: [
        {
          id: "OUTRA",
          changes: [
            {
              field: "messages",
              value: {
                messaging_product: "whatsapp",
                metadata: { phone_number_id: "000000000000000" },
                messages: [
                  { from: waId, id: "wamid.outro", timestamp: "1", type: "text", text: { body: "oi" } },
                ],
              },
            },
          ],
        },
      ],
    });

    expect((await postar(corpo, SEGREDO)).status).toBe(200);
    expect(
      await withTenant(A.id, async (tx) =>
        tx.conversation.count({ where: { externalContactId: waId } }),
      ),
    ).toBe(0);
  });

  it("a auditoria do webhook recusado guarda o MOTIVO, nunca o corpo", async () => {
    const corpo = corpoDeMensagem(novoWaId(), "conteudo que nao pode ser arquivado", "wamid.aud");
    await postar(corpo, "segredo-errado");

    const linhas = await withTenant(A.id, async (tx) =>
      tx.auditLog.findMany({ where: { action: "WHATSAPP_WEBHOOK_REJECTED" } }),
    );
    expect(linhas.length).toBeGreaterThan(0);

    const texto = JSON.stringify(linhas);
    expect(texto).toContain("NAO_CONFERE");
    expect(texto).not.toContain("conteudo que nao pode ser arquivado");
    expect(texto).not.toContain(SEGREDO);
  });

  it("TOKEN AUSENTE: sem App Secret, o webhook não aceita nada", async () => {
    const guardado = process.env.ELO_WHATSAPP_APP_SECRET;
    delete process.env.ELO_WHATSAPP_APP_SECRET;
    limparCacheDeNumeros();

    try {
      const waId = novoWaId();
      // Assinado com o segredo "certo", mas o servidor não tem segredo
      // nenhum para conferir. Falha fechada: 403.
      const r = await postar(corpoDeMensagem(waId, "x", "wamid.sem.segredo"), SEGREDO);
      expect(r.status).toBe(403);
      expect(
        await withTenant(A.id, async (tx) =>
          tx.conversation.count({ where: { externalContactId: waId } }),
        ),
      ).toBe(0);
    } finally {
      process.env.ELO_WHATSAPP_APP_SECRET = guardado;
      limparCacheDeNumeros();
    }
  });
});

/* ══ o segredo não vaza ═════════════════════════════════════════════ */

describe("segredos", () => {
  it("nenhum componente de cliente conhece as variáveis do WhatsApp", async () => {
    // A mesma barreira estrutural criada para a chave VAPID no MVP 1.7.
    const { readdirSync, readFileSync } = await import("node:fs");
    const { join } = await import("node:path");

    const varrer = (dir: string): string[] => {
      const saida: string[] = [];
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const caminho = join(dir, e.name);
        if (e.isDirectory()) {
          if (e.name !== "generated") saida.push(...varrer(caminho));
        } else if (/\.tsx?$/.test(e.name)) saida.push(caminho);
      }
      return saida;
    };

    const arquivos = varrer(join(process.cwd(), "src"));

    const conhecem = arquivos.filter((f) =>
      /ELO_WHATSAPP_(APP_SECRET|ACCESS_TOKEN|VERIFY_TOKEN)/.test(readFileSync(f, "utf8")),
    );
    const relativos = conhecem
      .map((f) => f.slice(process.cwd().length + 1).replace(/\\/g, "/"))
      .sort();

    expect(relativos).toEqual(["src/env.ts", "src/server/channels/whatsapp/config.ts"]);

    // E nenhum arquivo `"use client"` importa o adaptador.
    const clientes = arquivos.filter((f) =>
      readFileSync(f, "utf8").trimStart().startsWith('"use client"'),
    );
    for (const f of clientes) {
      expect(readFileSync(f, "utf8")).not.toContain("@/server/channels/");
    }
  });
});
