/**
 * POST /api/conversations/:id/tags — aplica ou remove uma etiqueta.
 *
 * `{ tagId, apply: boolean }`. Um verbo só para os dois lados: aplicar e
 * remover são a mesma decisão vista de dois ângulos, e separá-los em
 * POST/DELETE só duplicaria a validação.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { alternarEtiqueta } from "@/server/conversations/tags";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const corpoEtiqueta = z.object({ tagId: z.string().uuid(), apply: z.boolean() });

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);
    const { id } = await params;

    const corpo = corpoEtiqueta.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "etiqueta inválida");

    const r = await alternarEtiqueta(ctx, id, corpo.data.tagId, corpo.data.apply);
    if (r === "NAO_ENCONTRADO") {
      throw new AppError("NOT_FOUND", "Atendimento ou etiqueta não encontrados.");
    }

    return NextResponse.json(
      { result: r },
      { headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
