/**
 * POST /api/conversations/:id/notes — nota interna.
 *
 * Nota NUNCA vai para o cliente. Não há rota que a envie, e não há campo
 * que a transforme em mensagem: são tabelas diferentes.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { criarNota, listarNotas } from "@/server/conversations/notes";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const corpoNota = z.object({ content: z.string().min(1).max(5000) });

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);
    const { id } = await params;

    const corpo = corpoNota.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "nota vazia ou longa demais");

    const criada = await criarNota(ctx, id, corpo.data.content);
    if (!criada) throw new AppError("NOT_FOUND", "Atendimento não encontrado.");

    const notes = await listarNotas(ctx, id);
    return NextResponse.json(
      { notes },
      { headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
