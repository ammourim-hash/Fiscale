/**
 * GET /api/notifications
 *
 * Tudo o que a tela de Configurações precisa saber sobre notificações,
 * numa consulta só: as preferências desta pessoa, os aparelhos dela, e se
 * o servidor tem push disponível.
 *
 * A chave pública VAPID sai por aqui — e não por uma variável
 * `NEXT_PUBLIC_`. A chave pública PODE ser conhecida (é o
 * `applicationServerKey` que o navegador exige), mas entregá-la por
 * variável de build é como o par acaba junto no mesmo lugar e a privada
 * escorrega para o bundle numa cópia distraída. Aqui ela vem por rota
 * autenticada, e o único arquivo do projeto que toca na privada é
 * `push/config.ts`, no servidor.
 */
import { NextResponse, type NextRequest } from "next/server";

import { requireAuth } from "@/server/auth/request";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { minhasPreferencias } from "@/server/notifications/preferences";
import { meusDispositivos } from "@/server/notifications/subscriptions";
import { estadoDoPush } from "@/server/push/config";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);

    const [preferencias, dispositivos] = await Promise.all([
      minhasPreferencias(ctx),
      meusDispositivos(ctx),
    ]);

    const push = estadoDoPush();

    return NextResponse.json(
      {
        preferencias,
        // Sem endpoint e sem chaves — só o hash, que serve para o
        // navegador reconhecer "este dispositivo sou eu".
        dispositivos,
        push: push.disponivel
          ? { disponivel: true as const, vapidPublicKey: push.publicKey }
          : { disponivel: false as const, motivo: push.motivo },
      },
      { headers: cabecalhos },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
