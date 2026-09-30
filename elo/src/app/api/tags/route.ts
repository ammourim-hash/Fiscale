/**
 * GET  /api/tags   etiquetas do escritório
 * POST /api/tags   cria uma (só quem coordena)
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { criarEtiqueta, listarEtiquetas } from "@/server/conversations/tags";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  try {
    const ctx = await requireAuth(req);
    return NextResponse.json(
      { tags: await listarEtiquetas(ctx) },
      { headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}

const nova = z.object({
  slug: z.string().min(2).max(40),
  name: z.string().min(2).max(60),
  tone: z.enum(["neutro", "acao", "ok", "atencao", "erro"]).optional(),
});

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  try {
    const ctx = await requireAuth(req);

    const corpo = nova.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "etiqueta inválida");

    const { slug, name, tone } = corpo.data;
    const criada = await criarEtiqueta(ctx, {
      slug,
      name,
      ...(tone !== undefined ? { tone } : {}),
    });
    if (!criada) throw new AppError("VALIDATION", "etiqueta inválida");

    return NextResponse.json(
      { tag: criada },
      { status: 201, headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
