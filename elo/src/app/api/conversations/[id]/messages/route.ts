/**
 * GET  /api/conversations/:id/messages?antesDe=<sequence>&limite=<n>
 * POST /api/conversations/:id/messages
 *
 * O GET **não** marca nada como lido. Leitura é ação explícita e tem rota
 * própria (`messages/read`): se um GET marcasse, um prefetch do navegador
 * zeraria o contador de quem não olhou nada.
 *
 * O POST é idempotente por `clientMessageId`. Reenviar o mesmo id devolve
 * a MESMA mensagem, com 200 — do lado de fora, o retry parece ter
 * funcionado na primeira tentativa, que é o que deveria parecer.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { listarMensagens, PAGINA_MAXIMA } from "@/server/messages/queries";
import { enviarMensagem, type FalhaMensagem } from "@/server/messages/service";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function paraErro(code: FalhaMensagem, message: string): AppError {
  if (code === "NOT_FOUND") return new AppError("NOT_FOUND", message);
  return new AppError("VALIDATION", message, {
    details: { code },
    publicMessage: message,
  });
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);
    const { id } = await params;

    const url = new URL(req.url);
    const antes = Number(url.searchParams.get("antesDe"));
    const limite = Number(url.searchParams.get("limite"));

    const pagina = await listarMensagens(ctx, id, {
      antesDe: Number.isFinite(antes) && antes > 0 ? antes : null,
      ...(Number.isFinite(limite) && limite > 0
        ? { limite: Math.min(limite, PAGINA_MAXIMA) }
        : {}),
    });

    if (!pagina) throw new AppError("NOT_FOUND", "Atendimento não encontrado.");

    return NextResponse.json(pagina, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}

const envio = z.object({
  // Vazio é válido quando há anexo: mandar só o PDF da guia não exige
  // inventar uma legenda. O serviço recusa vazio-sem-anexo.
  content: z.string().max(8000).default(""),
  /** Gerado pelo navegador. É o que torna o retry seguro. */
  clientMessageId: z.string().min(8).max(64).optional(),
  /** Uploads já feitos que esta mensagem adota. */
  attachmentIds: z.array(z.string().uuid()).max(5).optional(),
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

    const corpo = envio.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "mensagem inválida");

    const r = await enviarMensagem(ctx, id, {
      content: corpo.data.content,
      ...(corpo.data.clientMessageId !== undefined
        ? { clientMessageId: corpo.data.clientMessageId }
        : {}),
      ...(corpo.data.attachmentIds !== undefined
        ? { attachmentIds: corpo.data.attachmentIds }
        : {}),
    });
    if (!r.ok) throw paraErro(r.code, r.message);

    return NextResponse.json(r.value, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
