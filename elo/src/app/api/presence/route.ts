/**
 * POST /api/presence
 *
 * "Esta aba foi para o fundo" / "voltou". Só isso.
 *
 * ---------------------------------------------------------------------
 *  Por que uma rota, e não reabrir o SSE
 * ---------------------------------------------------------------------
 *  A conexão do realtime já sabe QUAL conversa a aba acompanha; o que ela
 *  não sabe é se a janela está na frente. Derrubar e refazer o SSE a cada
 *  alt-tab seria uma reconexão por troca de janela, o dia inteiro, para
 *  transportar um booleano.
 *
 * ---------------------------------------------------------------------
 *  O id da conexão não é credencial
 * ---------------------------------------------------------------------
 *  Ele é aleatório e vem pelo próprio SSE autenticado, mas a rota confere
 *  o tenant e o membership da SESSÃO antes de aceitar a mudança. Adivinhar
 *  o id de outra pessoa não serviria de nada: a atualização é recusada.
 *
 *  O pior que alguém consegue com o próprio id é mentir sobre a própria
 *  presença — e o efeito disso é receber ou deixar de receber o próprio
 *  aviso. Não há nada a proteger aí.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { atualizar } from "@/server/realtime/presence";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const corpoEsperado = z.object({
  connection: z.string().uuid(),
  visivel: z.boolean(),
});

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);

    const corpo = corpoEsperado.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "presença inválida");

    const aplicado = atualizar(
      corpo.data.connection,
      { tenantId: ctx.tenantId, membershipId: ctx.membershipId },
      { visivel: corpo.data.visivel },
    );

    // `aplicado: false` quer dizer que a conexão já morreu — o servidor
    // reiniciou, ou o SSE caiu antes deste POST chegar. Não é erro: a aba
    // vai reconectar sozinha e se registrar de novo.
    return NextResponse.json({ aplicado }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
