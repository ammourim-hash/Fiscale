/**
 * GET   /api/conversations/:id   detalhe + histórico + quem visualizou
 * PATCH /api/conversations/:id   assumir · transferir · status
 *
 * O PATCH concentra as três ações porque as três alteram a MESMA linha e
 * disputam a mesma trava. Endpoints separados dariam a impressão de que
 * são independentes — e a primeira consequência seria alguém esquecer o
 * `expectedVersion` num deles.
 *
 * Abrir o detalhe registra visualização, e só isso: visualizar não é
 * assumir.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import {
  assumirAtendimento,
  mudarStatus,
  registrarVisualizacao,
  transferirAtendimento,
  type FalhaAtendimento,
} from "@/server/conversations/service";
import { obterAtendimento } from "@/server/conversations/queries";
import { listarNotas } from "@/server/conversations/notes";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Conflito é 409: a requisição estava certa, o mundo é que mudou. */
function paraErro(code: FalhaAtendimento, message: string): AppError {
  if (code === "NOT_FOUND") return new AppError("NOT_FOUND", message);
  if (code === "ALREADY_ASSIGNED" || code === "STALE") {
    // O texto especifico vai para a tela: "assumido por Aline • Fiscal"
    // e o que evita a pessoa clicar de novo achando que travou.
    return new AppError("CONFLICT", message, { details: { code }, publicMessage: message });
  }
  return new AppError("VALIDATION", message, { details: { code } });
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

    const visualizacao = await registrarVisualizacao(ctx, id);
    if (!visualizacao.ok) throw paraErro(visualizacao.code, visualizacao.message);

    const [conversation, notes] = await Promise.all([
      obterAtendimento(ctx, id),
      listarNotas(ctx, id),
    ]);
    if (!conversation) throw new AppError("NOT_FOUND", "Atendimento não encontrado.");

    return NextResponse.json({ conversation, notes }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}

const acao = z.discriminatedUnion("action", [
  z.object({ action: z.literal("assume") }),
  z.object({
    action: z.literal("transfer"),
    expectedVersion: z.number().int().min(0),
    toMembershipId: z.string().uuid().nullable().optional(),
    toDepartmentId: z.string().uuid().nullable().optional(),
    reason: z.string().max(200).nullable().optional(),
  }),
  z.object({
    action: z.literal("status"),
    expectedVersion: z.number().int().min(0),
    status: z.enum(["NEW", "IN_PROGRESS", "WAITING_CUSTOMER", "WAITING_OFFICE", "RESOLVED"]),
  }),
]);

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);
    const { id } = await params;

    const corpo = acao.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "ação inválida");

    const d = corpo.data;

    if (d.action === "assume") {
      const r = await assumirAtendimento(ctx, id);
      if (!r.ok) throw paraErro(r.code, r.message);
      return NextResponse.json(r.value, { headers: cabecalhos });
    }

    if (d.action === "transfer") {
      const r = await transferirAtendimento(ctx, id, {
        expectedVersion: d.expectedVersion,
        ...(d.toMembershipId !== undefined ? { toMembershipId: d.toMembershipId } : {}),
        ...(d.toDepartmentId !== undefined ? { toDepartmentId: d.toDepartmentId } : {}),
        ...(d.reason !== undefined ? { reason: d.reason } : {}),
      });
      if (!r.ok) throw paraErro(r.code, r.message);
      return NextResponse.json(r.value, { headers: cabecalhos });
    }

    const r = await mudarStatus(ctx, id, d.status, d.expectedVersion);
    if (!r.ok) throw paraErro(r.code, r.message);
    return NextResponse.json(r.value, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
