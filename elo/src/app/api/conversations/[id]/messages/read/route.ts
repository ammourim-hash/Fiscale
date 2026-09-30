/**
 * POST /api/conversations/:id/messages/read
 *
 * Marca mensagens do cliente como visualizadas POR MIM.
 *
 * Existe como rota separada, e não como efeito do GET, porque leitura
 * precisa de intenção. Quem decide o momento é a interface, e o critério
 * está escrito lá: conversa aberta + janela visível + a mensagem de fato
 * dentro da área visível. Prefetch, render e requisição de servidor não
 * contam — leitura falsa é pior que nenhuma leitura.
 *
 * A informação é INTERNA ao escritório. Nada aqui vira "visto" para o
 * cliente: o status de leitura do canal externo é outro conceito, de outro
 * lado da conversa, e chegará com o canal.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { marcarVisualizadas } from "@/server/messages/service";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const corpoEsperado = z.object({
  messageIds: z.array(z.string().uuid()).min(1).max(200),
});

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);
    const { id } = await params;

    const corpo = corpoEsperado.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "lista de mensagens inválida");

    // Ids de outra conversa ou de outro tenant simplesmente não casam: o
    // serviço filtra por conversationId e o RLS pelo tenant.
    const r = await marcarVisualizadas(ctx, id, corpo.data.messageIds);

    return NextResponse.json(r, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
