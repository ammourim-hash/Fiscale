/**
 * GET /api/notifications/badge
 *
 * O contador de não lidos DESTA pessoa.
 *
 * ---------------------------------------------------------------------
 *  Por que o número vem do banco, sempre
 * ---------------------------------------------------------------------
 *  A tentação é diminuir o contador no navegador quando a pessoa abre uma
 *  conversa. É mais rápido e está errado na segunda aba: ela abriu num
 *  lugar, o outro continua mostrando o número velho, e a partir daí os dois
 *  divergem para sempre. Contador em que a equipe não confia é pior do que
 *  contador nenhum.
 *
 *  Então: qualquer evento de realtime faz a tela PERGUNTAR de novo. É uma
 *  consulta barata (duas contagens) e sempre certa.
 *
 * ---------------------------------------------------------------------
 *  É pessoal, não do escritório
 * ---------------------------------------------------------------------
 *  Usa exatamente o `whereNaoLidos` do filtro "Não lidos" — a mesma
 *  condição, no mesmo lugar. Duas escritas da mesma regra divergem no
 *  primeiro ajuste, e aí a lista mostra três casos enquanto o crachá diz
 *  cinco.
 *
 *  A Aline e o Carlos, no mesmo atendimento, têm números diferentes — e é
 *  isso que se espera de "não lido POR MIM".
 */
import { NextResponse, type NextRequest } from "next/server";

import { requireAuth } from "@/server/auth/request";
import { can } from "@/server/auth/context";
import { contarNaoLidos } from "@/server/conversations/queries";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);

    // Quem não pode ler atendimento não tem o que contar. Zero em vez de
    // 403: o crachá é enfeite de navegação, e derrubar a barra lateral
    // inteira por causa dele seria desproporcional.
    if (!can(ctx, "conversations.read")) {
      return NextResponse.json({ naoLidos: 0 }, { headers: cabecalhos });
    }

    return NextResponse.json(
      { naoLidos: await contarNaoLidos(ctx) },
      { headers: cabecalhos },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
