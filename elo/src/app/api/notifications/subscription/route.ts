/**
 * POST   /api/notifications/subscription   inscreve ESTE dispositivo
 * DELETE /api/notifications/subscription   desativa ESTE dispositivo
 *
 * As duas rotas falam sempre do aparelho de quem chamou, com o membership
 * da sessão. Não há parâmetro de pessoa — é por isso que ninguém desliga o
 * aviso de um colega.
 *
 * O DELETE recebe o `endpointHash`, e não o endpoint: a tela nunca teve o
 * endpoint. Se o navegador já esqueceu a inscrição (permissão revogada no
 * sistema, perfil limpo), o hash guardado localmente ainda revoga a linha
 * do servidor — sem ele, sobraria uma inscrição viva sem dono.
 */
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { requireAuth } from "@/server/auth/request";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { inscrever, revogar } from "@/server/notifications/subscriptions";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const inscricaoEsperada = z.object({
  // 2048 é o teto prático dos provedores. Sem limite, um POST de 10 MB
  // viraria uma linha de 10 MB na tabela.
  endpoint: z.string().min(20).max(2048),
  p256dh: z.string().min(80).max(200),
  auth: z.string().min(16).max(64),
});

const revogacaoEsperada = z.object({
  endpointHash: z.string().regex(/^[0-9a-f]{64}$/),
});

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);

    const corpo = inscricaoEsperada.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "inscrição de push inválida");

    const r = await inscrever(ctx, {
      ...corpo.data,
      userAgent: req.headers.get("user-agent"),
    });

    if (!r.ok) {
      throw new AppError("VALIDATION", r.code, { publicMessage: r.message });
    }

    return NextResponse.json({ dispositivo: r.value }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}

export async function DELETE(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);

    const corpo = revogacaoEsperada.safeParse(await req.json().catch(() => null));
    if (!corpo.success) throw new AppError("VALIDATION", "hash de dispositivo inválido");

    // Revogar algo que já não existe devolve 0 e status 200. Não é erro:
    // "desativar duas vezes" é a mesma intenção duas vezes.
    const r = await revogar(ctx, corpo.data.endpointHash);

    return NextResponse.json(r, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
