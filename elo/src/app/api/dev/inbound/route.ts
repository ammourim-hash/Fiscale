/**
 * POST /api/dev/inbound  — ENTRADA DE DESENVOLVIMENTO
 *
 * Simula a chegada de uma mensagem do cliente enquanto o canal do WhatsApp
 * não existe. Não é uma rota de produto: é andaime, e vai embora quando o
 * adaptador do canal chegar (MVP 1.7).
 *
 * Fechada por três travas — ver `dev-inbound.ts`. Desligada, responde 404:
 * não confirma nem desmente a existência da rota, que é o comportamento
 * certo para uma porta que não deveria estar aí.
 *
 * Exige sessão mesmo assim. "Só existe em desenvolvimento" não é motivo
 * para deixar aberta: banco de desenvolvimento também tem dado de cliente.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { CANAL_DEV, entradaDevLiberada, REMETENTE_DEV } from "@/server/messages/dev-inbound";
import { registrarMensagemRecebida } from "@/server/messages/service";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { logger, newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const corpoEsperado = z.object({
  conversationId: z.string().uuid(),
  content: z.string().max(4000).default(""),
  /** Para testar reentrega: o mesmo id não pode virar mensagem nova. */
  externalMessageId: z.string().min(1).max(120).optional(),
  /**
   * Mídia de entrada. O arquivo sobe por `/api/uploads` como qualquer
   * outro — a porta de teste simula o CLIENTE mandando, não abre um
   * caminho de upload sem validação.
   */
  attachmentIds: z.array(z.string().uuid()).max(5).optional(),
});

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const trava = entradaDevLiberada();
    if (!trava.liberada) {
      logger.warn("dev.inbound.blocked", { motivo: trava.motivo });
      throw new AppError("NOT_FOUND", `entrada dev bloqueada: ${trava.motivo}`);
    }

    const ctx = await requireAuth(req);

    const corpo = corpoEsperado.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "entrada inválida");

    // O tenant vem da SESSÃO, como em todo o resto. Nem em desenvolvimento
    // se aceita tenant do corpo — é o tipo de atalho que sobrevive à fase.
    const r = await registrarMensagemRecebida(ctx.tenantId, corpo.data.conversationId, {
      content: corpo.data.content,
      externalSenderId: REMETENTE_DEV,
      channel: CANAL_DEV,
      ...(corpo.data.attachmentIds !== undefined
        ? { attachmentIds: corpo.data.attachmentIds }
        : {}),
      ...(corpo.data.externalMessageId !== undefined
        ? { externalMessageId: corpo.data.externalMessageId }
        : {}),
    });

    if (!r.ok) {
      throw r.code === "NOT_FOUND"
        ? new AppError("NOT_FOUND", r.message)
        : new AppError("VALIDATION", r.message, { publicMessage: r.message });
    }

    return NextResponse.json(r.value, {
      headers: { "cache-control": "no-store", "x-request-id": requestId },
    });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
