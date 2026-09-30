/**
 * GET /api/realtime  — Server-Sent Events
 *
 * ---------------------------------------------------------------------
 *  Por que SSE, e não Socket.IO
 * ---------------------------------------------------------------------
 *  Olhando a pilha que existe: Next 16 com App Router e Turbopack. O
 *  Socket.IO precisa de um servidor HTTP próprio; adotá-lo significaria
 *  abandonar `next dev` e `next start` e passar a manter um servidor
 *  customizado — mudança de arquitetura para resolver um problema que
 *  ainda não temos.
 *
 *  O que precisamos é de um fluxo servidor → cliente. Enviar mensagem é um
 *  POST comum, que já existe e já tem idempotência. Nada nesta fase pede
 *  canal bidirecional.
 *
 *  E o argumento decisivo é de segurança: SSE é uma rota como as outras.
 *  Autentica com o MESMO `requireAuth(req)` de todo o resto, pelo mesmo
 *  cookie, com a mesma sessão revogável. Um servidor de WebSocket ao lado
 *  teria o seu próprio caminho de autenticação — uma segunda porta para
 *  manter fechada, e a segunda porta é sempre a que fica aberta.
 *
 *  O EventSource do navegador reconecta sozinho. Ganhamos de graça o que
 *  seria o principal motivo para usar Socket.IO.
 *
 *  Custo aceito: uma conexão HTTP por aba, e um barramento em processo —
 *  ou seja, uma instância. Registrado como débito; quando houver duas, o
 *  adaptador entra dentro de `bus.ts` e mais nada muda.
 *
 * ---------------------------------------------------------------------
 *  Isolamento
 * ---------------------------------------------------------------------
 *  A inscrição é feita com o `tenantId` DA SESSÃO. O navegador não manda
 *  tenant, e se mandasse seria ignorado — não há parâmetro para isso.
 *
 *  O cliente pode pedir para acompanhar UMA conversa (`?conversation=`).
 *  Esse parâmetro só REDUZ o que ele recebe, e ainda assim é conferido
 *  contra o banco: com o RLS aberto no tenant da sessão, a conversa de
 *  outro escritório não existe, a inscrição é recusada e a conexão segue
 *  recebendo apenas os eventos de lista do próprio tenant.
 */
import type { NextRequest } from "next/server";

import { requireAuth } from "@/server/auth/request";
import { errorResponse } from "@/server/errors/http";
import { logger, newRequestId } from "@/server/logging/logger";
import { subscribe } from "@/server/realtime/bus";
import type { EloEvent } from "@/server/realtime/events";
import { entrar, sair } from "@/server/realtime/presence";
import { withTenant } from "@/server/tenancy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Sem isto, proxy e navegador fecham a conexão por ociosidade. */
const INTERVALO_BATIMENTO_MS = 25_000;

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function GET(req: NextRequest): Promise<Response> {
  // Sessão inválida devolve 401 como qualquer outra rota — e o EventSource
  // do navegador para de tentar sozinho quando o status não é 200.
  let ctx;
  try {
    ctx = await requireAuth(req);
  } catch (erro) {
    return errorResponse(erro, { requestId: newRequestId() });
  }

  const pedida = new URL(req.url).searchParams.get("conversation");
  let conversaAssinada: string | null = null;

  if (pedida && UUID.test(pedida)) {
    // A conferência que transforma "o cliente pediu" em "o cliente pode".
    const existe = await withTenant(ctx.tenantId, async (tx) =>
      tx.conversation.findUnique({ where: { id: pedida }, select: { id: true } }),
    );
    conversaAssinada = existe ? pedida : null;
  }

  // A aba diz se está em primeiro plano. Sem parâmetro, assume-se que
  // está: uma aba recém-aberta é uma aba que alguém acabou de olhar, e
  // errar para "presente" evita mandar notificação do sistema para quem
  // está com a conversa na frente.
  const visivelInicial = new URL(req.url).searchParams.get("visivel") !== "0";

  const codificador = new TextEncoder();
  let cancelar: (() => void) | null = null;
  let batimento: ReturnType<typeof setInterval> | null = null;
  let presenca: ReturnType<typeof entrar> | null = null;

  const fluxo = new ReadableStream<Uint8Array>({
    start(controlador) {
      const escrever = (texto: string): void => {
        try {
          controlador.enqueue(codificador.encode(texto));
        } catch {
          // Conexão já fechada pelo outro lado. Não é erro: é uma aba que
          // foi fechada enquanto um evento estava a caminho.
        }
      };

      // Presença: a partir daqui o servidor sabe que esta pessoa está com
      // o Elo aberto, e em qual conversa. É isso que impede a notificação
      // do sistema sobre a mensagem que ela está lendo neste instante.
      // Ver server/realtime/presence.ts.
      presenca = entrar(ctx.tenantId, {
        membershipId: ctx.membershipId,
        conversationId: conversaAssinada,
        visivel: visivelInicial,
      });

      // `retry` diz ao EventSource quanto esperar antes de reconectar.
      escrever(`retry: 3000\n\n`);
      escrever(
        `event: ready\ndata: ${JSON.stringify({
          conversation: conversaAssinada,
          // A tela avisa quando pediu uma conversa e não recebeu — sem
          // isso, um id errado viraria silêncio inexplicável.
          subscriptionDenied: Boolean(pedida) && conversaAssinada === null,
          // Id DESTA conexão. A aba o devolve em /api/presence quando vai
          // para o fundo ou volta. Não é credencial: a rota confere o
          // membership da sessão antes de aceitar.
          connection: presenca,
        })}\n\n`,
      );

      cancelar = subscribe(ctx.tenantId, (evento: EloEvent) => {
        // Evento de MENSAGEM só vai para quem assinou aquela conversa. O
        // de lista vai sempre — é ele que faz a fila subir sozinha para
        // quem está olhando a tela de Atendimentos.
        if (
          (evento.type === "message.created" || evento.type === "message.viewed") &&
          evento.conversationId !== conversaAssinada
        ) {
          return;
        }
        escrever(`event: ${evento.type}\ndata: ${JSON.stringify(evento)}\n\n`);
      });

      batimento = setInterval(() => escrever(`: ping\n\n`), INTERVALO_BATIMENTO_MS);

      // O `abort` chega quando a aba fecha, navega ou o navegador desiste.
      req.signal.addEventListener("abort", () => {
        cancelar?.();
        if (presenca) sair(presenca);
        if (batimento) clearInterval(batimento);
        try {
          controlador.close();
        } catch {
          /* já fechado */
        }
      });

      logger.info("realtime.connected", {
        tenantId: ctx.tenantId,
        membershipId: ctx.membershipId,
      });
    },

    cancel() {
      cancelar?.();
      if (presenca) sair(presenca);
      if (batimento) clearInterval(batimento);
    },
  });

  return new Response(fluxo, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-store, no-transform",
      connection: "keep-alive",
      // O Nginx/Caddy da frente não pode bufferizar: buffer em stream é o
      // mesmo que não ter stream.
      "x-accel-buffering": "no",
    },
  });
}
