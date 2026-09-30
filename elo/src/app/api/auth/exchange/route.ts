/**
 * POST /api/auth/exchange
 *
 * A porta de entrada. Recebe o token curto emitido pelo Fiscale, devolve
 * um cookie de sessao do Elo e manda para a home.
 *
 * O token chega no CORPO, nunca na URL. Query string vaza por toda parte:
 * fica no historico do navegador, no cabecalho Referer da proxima
 * requisicao, no log de acesso do servidor e do proxy reverso. Um token
 * de uso unico e 90 segundos aguenta pouco desse tipo de exposicao — e
 * nao ha motivo para aceitar nenhuma.
 *
 * Aceita `application/x-www-form-urlencoded` porque o Fiscale entra por
 * um formulario auto-submetido (navegacao de verdade, para o cookie ser
 * gravado no dominio do Elo) e tambem JSON, que e o que os testes usam.
 */
import { NextResponse, type NextRequest } from "next/server";

import { COOKIE_DESTINO, caminhoSeguro } from "@/server/auth/destination";
import { exchange, exchangeDenied } from "@/server/auth/exchange";
import { requestMeta } from "@/server/auth/request";
import { sessionCookie } from "@/server/auth/session";
import { errorResponse } from "@/server/errors/http";
import { AppError } from "@/server/errors/app-error";
import { logger, newRequestId } from "@/server/logging/logger";
import { sameHostUrl } from "@/server/http/same-host";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function lerToken(req: NextRequest): Promise<string | null> {
  const tipo = req.headers.get("content-type") ?? "";

  if (tipo.includes("application/x-www-form-urlencoded")) {
    const form = await req.formData();
    const v = form.get("token");
    return typeof v === "string" && v.length > 0 ? v : null;
  }

  if (tipo.includes("application/json")) {
    const corpo = (await req.json()) as unknown;
    if (corpo && typeof corpo === "object" && "token" in corpo) {
      const v = (corpo as { token: unknown }).token;
      return typeof v === "string" && v.length > 0 ? v : null;
    }
  }

  return null;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const log = logger.child({ requestId });

  try {
    const token = await lerToken(req).catch(() => null);
    if (!token) {
      // Mesmo erro de token invalido: nao vale distinguir "faltou o campo"
      // de "assinatura errada" para quem esta sondando de fora.
      throw new AppError("UNAUTHENTICATED", "token de troca ausente no corpo", {
        details: { code: "MISSING_TOKEN" },
      });
    }

    const r = await exchange(token, requestMeta(req));
    if (!r.ok) throw exchangeDenied(r.reason);

    log.info("auth.exchange.session_issued", {
      tenantId: r.tenantId,
      membershipId: r.membershipId,
      sessionId: r.session.sessionId,
    });

    // O destino pretendido, se a pessoa chegou aqui por ter clicado numa
    // notificacao com a sessao expirada. Sem isso, ela faz login e cai na
    // home — e o atendimento que ela queria abrir sumiu.
    //
    // O caminho e validado DE NOVO na leitura. O cookie e nosso e e
    // HttpOnly, mas confiar num valor so porque nos o gravamos e como se
    // aceita um redirecionamento aberto: o custo de reconferir e uma
    // funcao pura.
    const destino =
      caminhoSeguro(req.cookies.get(COOKIE_DESTINO)?.value) ?? "/";

    // 303: o navegador troca o POST por um GET no destino. Sem isso, um F5
    // reenviaria o token — que ja foi queimado — e o usuario veria erro.
    const resposta = NextResponse.redirect(sameHostUrl(req, destino), 303);

    // Queimado junto com o token: um destino que sobrevivesse a troca
    // sequestraria os proximos logins daquele navegador por 15 minutos.
    resposta.cookies.set(COOKIE_DESTINO, "", { path: "/", maxAge: 0 });

    const cookie = sessionCookie(r.session);
    resposta.cookies.set(cookie.name, cookie.value, {
      httpOnly: cookie.httpOnly,
      secure: cookie.secure,
      sameSite: cookie.sameSite,
      path: cookie.path,
      expires: cookie.expires,
    });
    resposta.headers.set("x-request-id", requestId);
    // A pagina de entrada nunca deve ficar em cache de proxy.
    resposta.headers.set("cache-control", "no-store");
    return resposta;
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
