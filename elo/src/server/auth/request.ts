/**
 * Ponte entre a requisicao HTTP e o contexto autenticado.
 *
 * Uma unica porta de entrada: `requireAuth(req)`. Rota que precisa de
 * usuario chama isto e recebe o contexto ja validado, ou uma excecao. Nao
 * ha caminho em que a rota "monta" o contexto sozinha a partir de header
 * ou de corpo — que e como tenantId de frontend viraria autoridade.
 */
import type { NextRequest } from "next/server";

import { AppError } from "@/server/errors/app-error";
import { logger } from "@/server/logging/logger";

import type { AuthContext } from "./context";
import { COOKIE_NAME, resolveSession, touchSession } from "./session";

export function clientIp(req: NextRequest): string | null {
  // Atras do Caddy, em producao, o IP real vem no cabecalho. Em
  // desenvolvimento nao ha proxy e o valor simplesmente nao existe.
  const encaminhado = req.headers.get("x-forwarded-for");
  if (encaminhado) return encaminhado.split(",")[0]?.trim() ?? null;
  return req.headers.get("x-real-ip");
}

export function requestMeta(req: NextRequest): { ip: string | null; userAgent: string | null } {
  return { ip: clientIp(req), userAgent: req.headers.get("user-agent") };
}

/** Contexto autenticado, ou `null`. Para rotas que funcionam dos dois jeitos. */
export async function optionalAuth(req: NextRequest): Promise<AuthContext | null> {
  const cookie = req.cookies.get(COOKIE_NAME)?.value;
  const r = await resolveSession(cookie);
  if (!r.ok) {
    if (r.reason !== "NO_COOKIE") {
      logger.warn("auth.session.rejected", { code: r.reason });
    }
    return null;
  }
  return r.context;
}

/**
 * Contexto autenticado, ou 401.
 *
 * O motivo da recusa nunca vai na resposta — "sessao revogada" e "cookie
 * inexistente" saem iguais. O codigo estruturado fica no log.
 */
export async function requireAuth(req: NextRequest): Promise<AuthContext> {
  const cookie = req.cookies.get(COOKIE_NAME)?.value;
  const r = await resolveSession(cookie);

  if (!r.ok) {
    logger.warn("auth.session.rejected", { code: r.reason });
    throw new AppError("UNAUTHENTICATED", `sessao invalida: ${r.reason}`, {
      details: { code: r.reason },
    });
  }

  // Falhar ao marcar atividade nao pode derrubar a requisicao.
  void touchSession(r.context).catch((erro: unknown) =>
    logger.warn("auth.session.touch_failed", { erro }),
  );

  return r.context;
}
