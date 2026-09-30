/**
 * Ponte entre AppError e a resposta HTTP. Toda rota devolve erro por aqui —
 * assim o formato e o que vaza (nada) sao decididos num lugar so.
 */
import { NextResponse } from "next/server";

import { isProduction } from "@/env";
import { logger, newRequestId, type LogContext } from "@/server/logging/logger";

import { toAppError } from "./app-error";

export interface ErrorBody {
  error: {
    kind: string;
    message: string;
    requestId: string;
    /** So em desenvolvimento. Em producao o campo nem existe. */
    debug?: string;
  };
}

export function errorResponse(
  erro: unknown,
  context: LogContext = {},
): NextResponse<ErrorBody> {
  const app = toAppError(erro);
  const requestId = context.requestId ?? newRequestId();

  const log = logger.child({ ...context, requestId });
  const campos = {
    kind: app.kind,
    status: app.status,
    // A mensagem interna e os detalhes ficam AQUI, no servidor.
    reason: app.message,
    ...(app.details ? { details: app.details } : {}),
  };

  if (app.status >= 500 || app.kind === "TENANT_CONTEXT") {
    log.error("request.failed", campos);
  } else {
    log.warn("request.rejected", campos);
  }

  const body: ErrorBody = {
    error: {
      kind: app.kind,
      message: app.publicMessage,
      requestId,
    },
  };

  if (!isProduction) {
    body.error.debug = app.message;
  }

  return NextResponse.json(body, {
    status: app.status,
    headers: { "x-request-id": requestId },
  });
}
