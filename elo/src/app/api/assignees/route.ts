/**
 * GET /api/assignees              quem pode receber transferência (interno)
 * GET /api/assignees?menu=cliente lista que o cliente vê (nome + área)
 *
 * A segunda é a que o adapter do WhatsApp vai usar no MVP 1.7. Ela existe
 * agora, e já com o formato final, para que a fase seguinte não invente
 * outra: o que sai são nome, área e um id — nada de e-mail, papel ou
 * permissão.
 */
import { NextResponse, type NextRequest } from "next/server";

import { requirePermission } from "@/server/auth/context";
import { requireAuth } from "@/server/auth/request";
import { getCustomerVisibleAssignees } from "@/server/conversations/assignees";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { withTenant } from "@/server/tenancy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);
    const url = new URL(req.url);

    if (url.searchParams.get("menu") === "cliente") {
      requirePermission(ctx, "users.read");
      return NextResponse.json(await getCustomerVisibleAssignees(ctx.tenantId), {
        headers: cabecalhos,
      });
    }

    // Lista interna: quem está apto a receber uma transferência. Inclui
    // quem não aparece para o cliente — visibilidade externa e capacidade
    // de receber trabalho são coisas diferentes.
    requirePermission(ctx, "conversations.transfer");

    const dados = await withTenant(ctx.tenantId, async (tx) => {
      const membros = await tx.membership.findMany({
        where: { active: true, availableForAssignment: true },
        select: {
          id: true,
          primaryDepartmentId: true,
          identity: { select: { name: true, active: true } },
        },
      });
      const deps = await tx.department.findMany({ select: { id: true, name: true } });
      const areas = new Map(deps.map((d) => [d.id, d.name]));

      return {
        people: membros
          .filter((m) => m.identity.active)
          .map((m) => ({
            membershipId: m.id,
            name: m.identity.name,
            department: m.primaryDepartmentId
              ? (areas.get(m.primaryDepartmentId) ?? null)
              : null,
          }))
          .sort((a, b) => a.name.localeCompare(b.name, "pt-BR")),
        departments: deps.sort((a, b) => a.name.localeCompare(b.name, "pt-BR")),
      };
    });

    return NextResponse.json(dados, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
