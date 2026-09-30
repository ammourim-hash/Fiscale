/**
 * PUT /api/team/:id/departments   substitui o CONJUNTO de áreas da pessoa
 *
 *   users.manage — OWNER e ADMIN
 *
 * PUT, e não POST: a tela mostra caixas marcadas, e salvar precisa
 * significar exatamente o que está lá. Um POST "adiciona" daria uma tela
 * em que desmarcar não desmarca nada — e até aqui desvincular alguém de
 * uma área dependia de SQL na mão, porque `assignDepartment` só sabe
 * criar.
 *
 * `primaryDepartmentId` viaja junto porque depende da mesma seleção: o
 * principal precisa estar entre as áreas, e se sair, cai junto.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { ValidationError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { definirDepartamentos } from "@/server/team/service";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const conjunto = z.object({
  departmentIds: z.array(z.string().uuid()).max(50),
  primaryDepartmentId: z.string().uuid().nullable().optional(),
});

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);
    const { id } = await params;

    const corpo = conjunto.safeParse(await req.json().catch(() => null));
    if (!corpo.success) {
      throw new ValidationError("areas invalidas", {
        issues: corpo.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`),
      });
    }

    const person = await definirDepartamentos(ctx, id, corpo.data);

    return NextResponse.json({ person }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
