/**
 * PATCH /api/notifications/preferences
 *
 * Salva as preferências de QUEM ESTÁ PEDINDO. Não existe parâmetro de
 * pessoa, e isso é a autorização: não há como pedir para alterar o aviso
 * de um colega porque não há onde escrever o id dele.
 *
 * PATCH, e não PUT: a tela manda o que mudou. Um PUT obrigaria o cliente a
 * reenviar o objeto inteiro, e duas abas abertas se sobrescreveriam com
 * estado velho.
 */
import { NextResponse, type NextRequest } from "next/server";

import { requireAuth } from "@/server/auth/request";
import { audit } from "@/server/auth/audit";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";
import { salvarPreferencias, validar } from "@/server/notifications/preferences";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function PATCH(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);

    const bruto: unknown = await req.json().catch(() => null);
    const validado = validar(bruto);
    if (!validado.ok) {
      // A mensagem é específica de propósito: aqui não há oráculo a
      // proteger — errar o nome de um campo de configuração não conta
      // nada sobre ninguém, e "erro de validação" seco faria a pessoa
      // tentar de novo às cegas.
      throw new AppError("VALIDATION", validado.code, {
        publicMessage: validado.message,
      });
    }

    const preferencias = await salvarPreferencias(ctx, validado.value);

    await audit({
      tenantId: ctx.tenantId,
      membershipId: ctx.membershipId,
      action: "NOTIFICATION_PREFERENCE_CHANGED",
      targetType: "membership",
      targetId: ctx.membershipId,
      // Só QUAIS campos mudaram. O valor de cada um está na tabela; a
      // trilha registra que houve mudança, e por quem.
      metadata: { campos: Object.keys(validado.value).join(",") || "nenhum" },
    });

    return NextResponse.json({ preferencias }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
