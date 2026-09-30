/**
 * GET /api/customers?q=…            busca por nome, documento ou telefone
 * GET /api/customers?phone=…        casamento telefone -> cliente
 * GET /api/customers?status=1       diagnostico da projecao
 *
 * Tudo dentro do tenant da sessao. O `q` nunca vira filtro de tenant: o
 * tenant sai do contexto autenticado, e o RLS confirma por baixo.
 *
 * A resposta traz `syncedAt` de proposito. A projecao pode estar velha —
 * o Fiscale desligado nao atualiza nada — e a tela precisa poder dizer
 * "sincronizado em", em vez de apresentar copia antiga como se fosse
 * tempo real.
 */
import { NextResponse, type NextRequest } from "next/server";

import { requirePermission } from "@/server/auth/context";
import { requireAuth } from "@/server/auth/request";
import {
  findCustomerByPhone,
  projectionStatus,
  searchCustomers,
} from "@/server/customers/lookup";
import { errorResponse } from "@/server/errors/http";
import { newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const cabecalhos = { "cache-control": "no-store", "x-request-id": requestId };

  try {
    const ctx = await requireAuth(req);
    // Ler cliente e parte de atender; quem consegue ver departamento
    // consegue ver a carteira. Papel proprio entra quando houver motivo.
    requirePermission(ctx, "departments.read");

    const url = new URL(req.url);

    if (url.searchParams.has("status")) {
      return NextResponse.json(await projectionStatus(ctx.tenantId), { headers: cabecalhos });
    }

    const telefone = url.searchParams.get("phone");
    if (telefone) {
      const r = await findCustomerByPhone(ctx.tenantId, telefone);
      return NextResponse.json(r, { headers: cabecalhos });
    }

    const termo = url.searchParams.get("q") ?? "";
    const incluirInativos = url.searchParams.get("inactive") === "1";
    const achados = await searchCustomers(ctx.tenantId, termo, {
      includeInactive: incluirInativos,
    });

    return NextResponse.json({ customers: achados }, { headers: cabecalhos });
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
