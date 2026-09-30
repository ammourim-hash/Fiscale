/**
 * GET /api/auth/me — quem esta logado, em qual tenant, com o que pode.
 *
 * Devolve as permissoes ja resolvidas em vez do papel cru. A tela nao
 * precisa saber traduzir papel em poder — e se precisasse, teria uma
 * segunda copia da regra para divergir da primeira.
 */
import { NextResponse, type NextRequest } from "next/server";

import { permissionsOf } from "@/server/auth/permissions";
import { requireAuth } from "@/server/auth/request";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { withTenant } from "@/server/tenancy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);

    const { departamentos, principalId } = await withTenant(ctx.tenantId, async (tx) => {
      const [lista, vinculo] = await Promise.all([
        tx.departmentMembership.findMany({
          where: { membershipId: ctx.membershipId },
          select: { department: { select: { id: true, slug: true, name: true } } },
        }),
        tx.membership.findUnique({
          where: { id: ctx.membershipId },
          select: { primaryDepartmentId: true },
        }),
      ]);
      return {
        departamentos: lista.map((d) => d.department),
        principalId: vinculo?.primaryDepartmentId ?? null,
      };
    });

    return NextResponse.json(
      {
        identity: { id: ctx.identityId, name: ctx.name, email: ctx.email },
        tenant: { id: ctx.tenantId },
        membership: { id: ctx.membershipId, role: ctx.role },
        permissions: permissionsOf(ctx.role),
        departments: departamentos,
        // A área que aparece ao lado do nome ("Aline • Fiscal"). Nulo
        // enquanto ninguem escolher — nao deduzimos "o primeiro da lista".
        primaryDepartment: departamentos.find((d) => d.id === principalId) ?? null,
      },
      { headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
