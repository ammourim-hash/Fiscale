/**
 * PATCH /api/team/:id   papel, situação, disponibilidade, visibilidade, nome
 *
 *   users.manage — OWNER e ADMIN
 *
 * Um PATCH para os cinco campos, e não cinco rotas: são a MESMA linha, e
 * endpoints separados dariam a impressão de que mudam coisas
 * independentes. O `:id` é o `membershipId` — o vínculo com ESTE
 * escritório —, nunca o `identityId`, que é global.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { ValidationError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { editarPessoa } from "@/server/team/service";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const PAPEIS = ["OWNER", "ADMIN", "MANAGER", "AGENT", "VIEWER"] as const;

const mudancas = z
  .object({
    name: z.string().trim().min(2).max(120).optional(),
    role: z.enum(PAPEIS).optional(),
    active: z.boolean().optional(),
    availableForAssignment: z.boolean().optional(),
    visibleToCustomers: z.boolean().optional(),
  })
  // Corpo vazio é engano de quem chama, não "salvar nada com sucesso".
  .refine((v) => Object.keys(v).length > 0, "informe ao menos um campo");

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);
    const { id } = await params;

    const corpo = mudancas.safeParse(await req.json().catch(() => null));
    if (!corpo.success) {
      throw new ValidationError("alteracao invalida", {
        issues: corpo.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`),
      });
    }

    const person = await editarPessoa(ctx, id, corpo.data);

    return NextResponse.json({ person }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
