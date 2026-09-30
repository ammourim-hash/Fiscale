/**
 * Sessoes da propria pessoa.
 *
 *   GET     /api/auth/sessions   lista as ativas
 *   DELETE  /api/auth/sessions   encerra TODAS AS OUTRAS
 *
 * Encerrar as proprias sessoes nao pede permissao de RBAC: e a pessoa
 * agindo sobre si mesma. `sessions.manage` existe para o dia em que um
 * administrador precisar derrubar a sessao de OUTRA pessoa — o que ainda
 * nao tem rota.
 *
 * A listagem nunca devolve `tokenHash`. Nem o hash: ele identifica a
 * sessao e nao ha razao de a tela conhece-lo.
 */
import { NextResponse, type NextRequest } from "next/server";

import { audit } from "@/server/auth/audit";
import { requireAuth } from "@/server/auth/request";
import { revokeOtherSessions } from "@/server/auth/session";
import { errorResponse } from "@/server/errors/http";
import { logger, newRequestId } from "@/server/logging/logger";
import { withTenant } from "@/server/tenancy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);

    const sessoes = await withTenant(ctx.tenantId, async (tx) =>
      tx.session.findMany({
        where: { membershipId: ctx.membershipId, revokedAt: null },
        select: {
          id: true,
          createdAt: true,
          lastSeenAt: true,
          expiresAt: true,
          userAgent: true,
        },
        orderBy: { lastSeenAt: "desc" },
      }),
    );

    return NextResponse.json(
      {
        sessions: sessoes.map((s) => ({ ...s, current: s.id === ctx.sessionId })),
      },
      { headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}

export async function DELETE(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();

  try {
    const ctx = await requireAuth(req);
    const encerradas = await revokeOtherSessions(ctx);

    await audit({
      tenantId: ctx.tenantId,
      membershipId: ctx.membershipId,
      action: "SESSION_REVOKED",
      targetType: "session",
      metadata: { scope: "others", count: encerradas },
    });

    logger.child({ requestId }).info("auth.sessions.revoked_others", {
      tenantId: ctx.tenantId,
      membershipId: ctx.membershipId,
      count: encerradas,
    });

    return NextResponse.json(
      { revoked: encerradas },
      { headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
