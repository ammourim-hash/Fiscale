/**
 * POST /api/auth/logout
 *
 * Sair de verdade: a sessao e revogada no servidor E o cookie e apagado.
 * Só limpar o cookie seria teatro — o token continuaria valido para quem
 * tivesse copiado o valor.
 *
 * O cookie e apagado mesmo quando nao havia sessao valida. Quem chega com
 * cookie estragado quer sair; devolver 401 e deixar o cookie ali so
 * prende a pessoa numa tela quebrada.
 */
import { NextResponse, type NextRequest } from "next/server";

import { audit } from "@/server/auth/audit";
import { optionalAuth } from "@/server/auth/request";
import { clearedCookie, revokeSession } from "@/server/auth/session";
import { errorResponse } from "@/server/errors/http";
import { logger, newRequestId } from "@/server/logging/logger";
import { sameHostUrl } from "@/server/http/same-host";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const log = logger.child({ requestId });

  try {
    const ctx = await optionalAuth(req);

    if (ctx) {
      await revokeSession(ctx);
      await audit({
        tenantId: ctx.tenantId,
        membershipId: ctx.membershipId,
        action: "LOGOUT",
        targetType: "session",
        targetId: ctx.sessionId,
      });
      log.info("auth.logout", {
        tenantId: ctx.tenantId,
        membershipId: ctx.membershipId,
        sessionId: ctx.sessionId,
      });
    }

    // Formulário HTML espera navegar; fetch espera JSON. Sem esta
    // distinção, clicar em "Sair" deixaria a pessoa olhando `{"ok":true}`.
    const vindoDeFormulario = (req.headers.get("accept") ?? "").includes("text/html");

    const resposta = vindoDeFormulario
      ? NextResponse.redirect(sameHostUrl(req, "/entrar"), 303)
      : NextResponse.json(
          { ok: true },
          { headers: { "cache-control": "no-store", "x-request-id": requestId } },
        );
    resposta.headers.set("cache-control", "no-store");
    resposta.headers.set("x-request-id", requestId);
    const cookie = clearedCookie();
    resposta.cookies.set(cookie.name, cookie.value, {
      httpOnly: cookie.httpOnly,
      secure: cookie.secure,
      sameSite: cookie.sameSite,
      path: cookie.path,
      maxAge: 0,
    });
    return resposta;
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
