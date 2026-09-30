/**
 * Departamentos — a primeira rota a exercitar o RBAC de verdade.
 *
 *   GET   departments.read     todos os papeis, ate VIEWER
 *   POST  departments.manage   OWNER, ADMIN e MANAGER
 *
 * Fiscal, Contabil, DP, Financeiro e Atendimento nao aparecem em lugar
 * nenhum do codigo: sao linhas de tabela, criadas por quem administra o
 * escritorio. Escritorio que organiza as areas de outro jeito nao precisa
 * de alteracao no sistema.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requirePermission } from "@/server/auth/context";
import { requireAuth } from "@/server/auth/request";
import { ValidationError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { withTenant } from "@/server/tenancy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const novoDepartamento = z.object({
  slug: z
    .string()
    .min(2)
    .max(40)
    .regex(/^[a-z0-9][a-z0-9-]*$/, "use minusculas, numeros e hifen"),
  name: z.string().min(2).max(80),
});

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);
    requirePermission(ctx, "departments.read");

    // Sem `where` de tenant: o RLS ja limita. A rota nao tem como devolver
    // departamento de outro escritorio nem se quisesse.
    const lista = await withTenant(ctx.tenantId, async (tx) =>
      tx.department.findMany({
        select: { id: true, slug: true, name: true },
        orderBy: { name: "asc" },
      }),
    );

    return NextResponse.json(
      { departments: lista },
      { headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);
    requirePermission(ctx, "departments.manage");

    const corpo = novoDepartamento.safeParse(await req.json().catch(() => null));
    if (!corpo.success) {
      throw new ValidationError("departamento invalido", {
        issues: corpo.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`),
      });
    }

    const criado = await withTenant(ctx.tenantId, async (tx) =>
      tx.department.create({
        data: { tenantId: ctx.tenantId, slug: corpo.data.slug, name: corpo.data.name },
        select: { id: true, slug: true, name: true },
      }),
    );

    return NextResponse.json(
      { department: criado },
      { status: 201, headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
