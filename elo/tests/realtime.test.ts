/**
 * O realtime.
 *
 * O teste que carrega o arquivo é o de isolamento: uma conexão do tenant A
 * não pode receber NADA do tenant B. Ele é exercitado nos dois níveis —
 * no barramento e por HTTP de verdade, contra a rota SSE com sessão real —
 * porque isolamento que só vale numa camada não é isolamento.
 *
 * O resto verifica que cada ação publica o aviso certo, e que uma conexão
 * fechada some do barramento em vez de virar vazamento.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import type { AuthContext } from "@/server/auth/context";
import { exchange } from "@/server/auth/exchange";
import {
  assumirAtendimento,
  criarAtendimento,
  mudarStatus,
} from "@/server/conversations/service";
import { obterAtendimento } from "@/server/conversations/queries";
import {
  enviarMensagem,
  marcarVisualizadas,
  registrarMensagemRecebida,
} from "@/server/messages/service";
import { publish, subscribe, subscriberCount } from "@/server/realtime/bus";
import type { EloEvent } from "@/server/realtime/events";
import type { Role } from "@/generated/prisma/enums";

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
let ctxA: AuthContext;

function contexto(t: TenantFixture): AuthContext {
  return {
    sessionId: "00000000-0000-4000-8000-000000000001",
    tenantId: t.id,
    membershipId: t.membership.membershipId,
    identityId: t.membership.identityId,
    email: t.email,
    name: "Pessoa de Teste",
    role: "OWNER" as Role,
  };
}

/** Coletor de eventos com cancelamento — o padrão de todos os testes daqui. */
function ouvir(tenantId: string): { eventos: EloEvent[]; parar: () => void } {
  const eventos: EloEvent[] = [];
  const parar = subscribe(tenantId, (e) => eventos.push(e));
  return { eventos, parar };
}

beforeAll(async () => {
  par = gerarPar("rt-1");
  instalarChaves(par);
  A = await criarTenantComPessoa("rta", "OWNER" as Role);
  B = await criarTenantComPessoa("rtb", "OWNER" as Role);
  ctxA = contexto(A);
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

/* ─── o barramento ───────────────────────────────────────────────────── */

describe("barramento", () => {
  it("23. um inscrito do tenant A não recebe evento do tenant B", () => {
    const a = ouvir(A.id);
    const b = ouvir(B.id);

    publish(B.id, {
      type: "conversation.updated",
      conversationId: "00000000-0000-4000-8000-00000000000b",
      status: "NEW",
      version: 1,
      assignedMembershipId: null,
      lastActivityAt: new Date().toISOString(),
      reason: "status",
    });

    // O ponto do teste: nada atravessa. A chave do mapa é o tenant, então
    // vazar exigiria reescrever `publish`, não esquecer um `if`.
    expect(a.eventos).toHaveLength(0);
    expect(b.eventos).toHaveLength(1);

    a.parar();
    b.parar();
  });

  it("24. cancelar a inscrição tira a conexão do barramento", () => {
    expect(subscriberCount(A.id)).toBe(0);
    const a = ouvir(A.id);
    expect(subscriberCount(A.id)).toBe(1);

    a.parar();
    // Sem isso, cada aba fechada deixaria um ouvinte morto para sempre.
    expect(subscriberCount(A.id)).toBe(0);
  });

  it("25. duas abas do mesmo tenant recebem as duas", () => {
    const aba1 = ouvir(A.id);
    const aba2 = ouvir(A.id);

    publish(A.id, {
      type: "conversation.updated",
      conversationId: "00000000-0000-4000-8000-00000000000a",
      status: "NEW",
      version: 1,
      assignedMembershipId: null,
      lastActivityAt: new Date().toISOString(),
      reason: "status",
    });

    expect(aba1.eventos).toHaveLength(1);
    expect(aba2.eventos).toHaveLength(1);

    aba1.parar();
    aba2.parar();
  });

  it("um ouvinte que explode não impede a entrega aos outros", () => {
    const parar1 = subscribe(A.id, () => {
      throw new Error("conexão morta");
    });
    const bom = ouvir(A.id);

    publish(A.id, {
      type: "conversation.updated",
      conversationId: "00000000-0000-4000-8000-00000000000a",
      status: "NEW",
      version: 1,
      assignedMembershipId: null,
      lastActivityAt: new Date().toISOString(),
      reason: "status",
    });

    expect(bom.eventos).toHaveLength(1);
    parar1();
    bom.parar();
  });
});

/* ─── 19-22: cada ação publica o seu aviso ───────────────────────────── */

describe("as ações avisam", () => {
  it("19. mensagem nova gera message.created e conversation.updated", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = ouvir(A.id);

    await registrarMensagemRecebida(A.id, id, { content: "chegou agora" });

    const criada = a.eventos.find((e) => e.type === "message.created");
    expect(criada).toBeDefined();
    if (criada?.type === "message.created") {
      expect(criada.conversationId).toBe(id);
      expect(criada.direction).toBe("INBOUND");
      expect(criada.preview).toBe("chegou agora");
    }

    // O segundo evento é o que faz a LISTA subir para quem nem abriu a
    // conversa. Sem ele, a fila só se atualizaria ao abrir o caso.
    expect(a.eventos.some((e) => e.type === "conversation.updated")).toBe(true);
    a.parar();
  });

  it("a prévia do evento é curta — o evento é aviso, não transporte", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = ouvir(A.id);

    await enviarMensagem(ctxA, id, { content: "x".repeat(400) });

    const e = a.eventos.find((x) => x.type === "message.created");
    if (e?.type === "message.created") {
      expect(e.preview.length).toBeLessThanOrEqual(120);
    }
    a.parar();
  });

  it("20. visualizar mensagem gera message.viewed", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const m = await registrarMensagemRecebida(A.id, id, { content: "para ler" });
    if (!m.ok) throw new Error("preparo falhou");

    const a = ouvir(A.id);
    await marcarVisualizadas(ctxA, id, [m.value.id]);

    const visto = a.eventos.find((e) => e.type === "message.viewed");
    expect(visto).toBeDefined();
    if (visto?.type === "message.viewed") {
      expect(visto.messageId).toBe(m.value.id);
      expect(visto.membershipId).toBe(A.membership.membershipId);
    }
    a.parar();
  });

  it("21. mudar status avisa, com o status novo", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const versao = (await obterAtendimento(ctxA, id))!.version;

    const a = ouvir(A.id);
    await mudarStatus(ctxA, id, "WAITING_CUSTOMER", versao);

    const e = a.eventos.find((x) => x.type === "conversation.updated");
    expect(e).toBeDefined();
    if (e?.type === "conversation.updated") {
      expect(e.status).toBe("WAITING_CUSTOMER");
      expect(e.reason).toBe("status");
    }
    a.parar();
  });

  it("22. assumir avisa, com o responsável novo", async () => {
    const { id } = await criarAtendimento(A.id, {});

    const a = ouvir(A.id);
    await assumirAtendimento(ctxA, id);

    const e = a.eventos.find((x) => x.type === "conversation.updated");
    expect(e).toBeDefined();
    if (e?.type === "conversation.updated") {
      expect(e.assignedMembershipId).toBe(A.membership.membershipId);
      expect(e.reason).toBe("assignment");
    }
    a.parar();
  });

  it("nota interna avisa que houve atividade, sem levar o texto", async () => {
    const { criarNota } = await import("@/server/conversations/notes");
    const { id } = await criarAtendimento(A.id, {});

    const a = ouvir(A.id);
    await criarNota(ctxA, id, "SEGREDO INTERNO que não pode viajar");

    const e = a.eventos.find((x) => x.type === "conversation.updated");
    expect(e?.type).toBe("conversation.updated");
    // O texto da nota não está em lugar nenhum do que foi publicado.
    expect(JSON.stringify(a.eventos)).not.toContain("SEGREDO");
    a.parar();
  });
});

/* ─── a rota SSE, por HTTP de verdade ────────────────────────────────── */

async function entrar(t: TenantFixture): Promise<string> {
  const r = await exchange(assinarToken(par, { sub: t.fiscaleUid, tid: t.id }));
  if (!r.ok) throw new Error(`troca falhou: ${r.reason}`);
  return r.session.cookieValue;
}

interface Conexao {
  resposta: Response;
  lido: () => string;
  fechar: () => void;
  esperar: (trecho: string, ms?: number) => Promise<boolean>;
}

/** Abre a rota SSE com um cookie real e vai acumulando o que chega. */
async function abrirSse(cookie: string, conversation?: string): Promise<Conexao> {
  const { GET } = await import("@/app/api/realtime/route");

  const controle = new AbortController();
  const url = conversation
    ? `http://localhost/api/realtime?conversation=${conversation}`
    : "http://localhost/api/realtime";

  const req = new NextRequest(
    new Request(url, { headers: { cookie: `elo_sessao=${cookie}` }, signal: controle.signal }),
  );

  const resposta = await GET(req);
  let acumulado = "";

  if (resposta.body) {
    const leitor = resposta.body.getReader();
    const decodificador = new TextDecoder();
    void (async () => {
      try {
        for (;;) {
          const { done, value } = await leitor.read();
          if (done) break;
          acumulado += decodificador.decode(value, { stream: true });
        }
      } catch {
        /* abortado: é como a conexão termina */
      }
    })();
  }

  const esperar = async (trecho: string, ms = 2000): Promise<boolean> => {
    const limite = Date.now() + ms;
    while (Date.now() < limite) {
      if (acumulado.includes(trecho)) return true;
      await new Promise((r) => setTimeout(r, 25));
    }
    return false;
  };

  return {
    resposta,
    lido: () => acumulado,
    fechar: () => controle.abort(),
    esperar,
  };
}

describe("rota /api/realtime", () => {
  it("sem sessão, 401 — e não um fluxo aberto", async () => {
    const { GET } = await import("@/app/api/realtime/route");
    const r = await GET(new NextRequest(new Request("http://localhost/api/realtime")));
    expect(r.status).toBe(401);
  });

  it("com sessão, abre o fluxo e anuncia o que assinou", async () => {
    const cookie = await entrar(A);
    const { id } = await criarAtendimento(A.id, {});

    const c = await abrirSse(cookie, id);
    expect(c.resposta.headers.get("content-type")).toContain("text/event-stream");
    expect(await c.esperar("event: ready")).toBe(true);
    expect(c.lido()).toContain(id);

    c.fechar();
  });

  it("23. HTTP: a conexão de A não recebe mensagem do tenant B", async () => {
    const cookieA = await entrar(A);
    const { id: daA } = await criarAtendimento(A.id, {});
    const { id: daB } = await criarAtendimento(B.id, {});

    // A conexão é de A, e pede a conversa de A.
    const c = await abrirSse(cookieA, daA);
    expect(await c.esperar("event: ready")).toBe(true);

    // Mensagem no OUTRO escritório, ao mesmo tempo.
    await registrarMensagemRecebida(B.id, daB, { content: "CONFIDENCIAL DO TENANT B" });
    await new Promise((r) => setTimeout(r, 250));

    const recebido = c.lido();
    expect(recebido).not.toContain("CONFIDENCIAL");
    expect(recebido).not.toContain(daB);

    // E a própria conversa continua funcionando — o teste não passou por
    // a conexão estar morta.
    await registrarMensagemRecebida(A.id, daA, { content: "esta é minha" });
    expect(await c.esperar("esta é minha")).toBe(true);

    c.fechar();
  });

  it("pedir conversa de OUTRO tenant é recusado, e não silenciado", async () => {
    const cookieA = await entrar(A);
    const { id: daB } = await criarAtendimento(B.id, {});

    const c = await abrirSse(cookieA, daB);
    expect(await c.esperar("event: ready")).toBe(true);

    // A tela precisa saber que pediu e não recebeu; sem isso, um id
    // errado viraria silêncio inexplicável.
    expect(c.lido()).toContain('"subscriptionDenied":true');
    expect(c.lido()).toContain('"conversation":null');

    await registrarMensagemRecebida(B.id, daB, { content: "SEGREDO DE B" });
    await new Promise((r) => setTimeout(r, 250));
    expect(c.lido()).not.toContain("SEGREDO DE B");

    c.fechar();
  });

  it("mensagem de conversa NÃO assinada não vem; a atualização de lista vem", async () => {
    const cookie = await entrar(A);
    const { id: assinada } = await criarAtendimento(A.id, {});
    const { id: outra } = await criarAtendimento(A.id, {});

    const c = await abrirSse(cookie, assinada);
    expect(await c.esperar("event: ready")).toBe(true);

    await registrarMensagemRecebida(A.id, outra, { content: "TEXTO DA OUTRA CONVERSA" });

    // A lista precisa subir — mas o conteúdo da conversa que não está
    // aberta não tem por que trafegar.
    expect(await c.esperar("conversation.updated")).toBe(true);
    expect(c.lido()).not.toContain("TEXTO DA OUTRA CONVERSA");

    c.fechar();
  });

  it("26. dois funcionários, duas conexões: os dois recebem", async () => {
    const cookie1 = await entrar(A);
    const cookie2 = await entrar(A);
    const { id } = await criarAtendimento(A.id, {});

    const aline = await abrirSse(cookie1, id);
    const carlos = await abrirSse(cookie2, id);
    expect(await aline.esperar("event: ready")).toBe(true);
    expect(await carlos.esperar("event: ready")).toBe(true);

    await registrarMensagemRecebida(A.id, id, { content: "Preciso da guia." });

    expect(await aline.esperar("Preciso da guia")).toBe(true);
    expect(await carlos.esperar("Preciso da guia")).toBe(true);

    aline.fechar();
    carlos.fechar();
  });

  it("24. fechar a conexão libera o lugar no barramento", async () => {
    const cookie = await entrar(A);
    const antes = subscriberCount(A.id);

    const c = await abrirSse(cookie);
    expect(await c.esperar("event: ready")).toBe(true);
    expect(subscriberCount(A.id)).toBe(antes + 1);

    c.fechar();
    await new Promise((r) => setTimeout(r, 200));

    expect(subscriberCount(A.id)).toBe(antes);
  });
});

/* ─── 34: a entrada de desenvolvimento ───────────────────────────────── */

describe("entrada de desenvolvimento", () => {
  it("34. bloqueada quando a variável de confirmação não está posta", async () => {
    const anterior = process.env.ELO_ALLOW_DEV_INBOUND;
    delete process.env.ELO_ALLOW_DEV_INBOUND;

    const { entradaDevLiberada } = await import("@/server/messages/dev-inbound");
    const r = entradaDevLiberada();

    expect(r.liberada).toBe(false);
    expect(r.motivo).toContain("ELO_ALLOW_DEV_INBOUND");

    if (anterior !== undefined) process.env.ELO_ALLOW_DEV_INBOUND = anterior;
  });

  it("34. a rota responde 404 quando bloqueada — não confirma que existe", async () => {
    const anterior = process.env.ELO_ALLOW_DEV_INBOUND;
    delete process.env.ELO_ALLOW_DEV_INBOUND;

    const { POST } = await import("@/app/api/dev/inbound/route");
    const r = await POST(
      new NextRequest(
        new Request("http://localhost/api/dev/inbound", {
          method: "POST",
          body: JSON.stringify({ conversationId: A.id, content: "oi" }),
        }),
      ),
    );

    expect(r.status).toBe(404);

    if (anterior !== undefined) process.env.ELO_ALLOW_DEV_INBOUND = anterior;
  });

  it("34. liberada, exige as duas condições ao mesmo tempo", async () => {
    process.env.ELO_ALLOW_DEV_INBOUND = "1";
    const { entradaDevLiberada } = await import("@/server/messages/dev-inbound");
    expect(entradaDevLiberada().liberada).toBe(true);
    delete process.env.ELO_ALLOW_DEV_INBOUND;
  });
});
