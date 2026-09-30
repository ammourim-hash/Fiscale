/**
 * GET /api/health
 *
 * Distingue duas coisas que costumam ser confundidas:
 *   - `status: "ok"`       aplicacao viva E banco respondendo
 *   - `status: "degraded"` aplicacao viva, banco fora  -> HTTP 503
 * Se nem a aplicacao estiver de pe, nao ha resposta nenhuma — que e a
 * terceira informacao, e a mais obvia.
 *
 * O que NAO sai daqui: connection string, host, usuario, versao do Postgres,
 * nome de container, stack trace. Monitor nao precisa disso; atacante gosta.
 */
import { NextResponse } from "next/server";

import { checkDatabase } from "@/server/db/health";
import { errorResponse } from "@/server/errors/http";
import { logger, newRequestId } from "@/server/logging/logger";

// Saude nunca pode vir de cache.
export const dynamic = "force-dynamic";
export const revalidate = 0;

interface HealthBody {
  status: "ok" | "degraded";
  uptimeSeconds: number;
  checks: {
    app: "ok";
    database: { status: "ok" | "down"; latencyMs: number };
  };
}

export async function GET(): Promise<NextResponse> {
  const requestId = newRequestId();
  const log = logger.child({ requestId });

  try {
    const db = await checkDatabase();

    const body: HealthBody = {
      status: db.reachable ? "ok" : "degraded",
      uptimeSeconds: Math.round(process.uptime()),
      checks: {
        app: "ok",
        database: {
          status: db.reachable ? "ok" : "down",
          latencyMs: db.latencyMs,
        },
      },
    };

    log.debug("health.checked", {
      status: body.status,
      dbLatencyMs: db.latencyMs,
    });

    return NextResponse.json(body, {
      status: db.reachable ? 200 : 503,
      headers: {
        "cache-control": "no-store",
        "x-request-id": requestId,
      },
    });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
