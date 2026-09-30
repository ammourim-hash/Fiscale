/**
 * GET /api/auth/destino?para=/atendimentos?abrir=…
 *
 * Guarda para onde a pessoa queria ir e manda para a tela de entrada.
 *
 * É uma rota, e não algo feito na própria página, por um motivo simples do
 * Next: Server Component não grava cookie. Só route handler e server
 * action podem — e uma rota de três linhas é mais honesta do que
 * contrabandear o destino por `sessionStorage`, que não sobrevive à volta
 * pelo Fiscale.
 *
 * O caminho é validado ANTES de virar cookie. Ver `caminhoSeguro`: sem
 * isso, `?para=//outro-site` transformaria o Elo num redirecionador aberto
 * — o tipo de coisa que aparece em phishing usando um domínio confiável
 * como trampolim.
 */
import { NextResponse, type NextRequest } from "next/server";

import {
  COOKIE_DESTINO,
  VIDA_DESTINO_SEGUNDOS,
  caminhoSeguro,
} from "@/server/auth/destination";
import { env } from "@/env";
import { sameHostUrl } from "@/server/http/same-host";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const pedido = new URL(req.url).searchParams.get("para");
  const destino = caminhoSeguro(pedido);

  const resposta = NextResponse.redirect(sameHostUrl(req, "/entrar"), 303);

  if (destino) {
    resposta.cookies.set(COOKIE_DESTINO, destino, {
      httpOnly: true,
      secure: env.ELO_COOKIE_SECURE,
      // `Lax` é o que permite o cookie viajar na navegação de volta do
      // Fiscale. `Strict` o bloquearia exatamente no momento em que ele
      // precisa existir.
      sameSite: "lax",
      path: "/",
      maxAge: VIDA_DESTINO_SEGUNDOS,
    });
  }

  resposta.headers.set("cache-control", "no-store");
  return resposta;
}
