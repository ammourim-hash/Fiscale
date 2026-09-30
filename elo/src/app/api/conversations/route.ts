/**
 * GET /api/conversations?filtro=novos|nao-lidos|meus|aguardando|todos
 *
 * O tenant vem da sessão. `filtro` só escolhe o recorte — nunca amplia o
 * alcance, e o RLS confirma por baixo.
 */
import { NextResponse, type NextRequest } from "next/server";

import { requireAuth } from "@/server/auth/request";
import { contarPorFiltro, ehFiltro, listarAtendimentos } from "@/server/conversations/queries";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);
    const bruto = new URL(req.url).searchParams.get("filtro") ?? "novos";
    const filtro = ehFiltro(bruto) ? bruto : "novos";

    const [conversations, counts] = await Promise.all([
      listarAtendimentos(ctx, filtro),
      contarPorFiltro(ctx),
    ]);

    return NextResponse.json({ filtro, conversations, counts }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
