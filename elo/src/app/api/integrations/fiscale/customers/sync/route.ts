/**
 * POST /api/integrations/fiscale/customers/sync
 *
 * A porta por onde a projecao de clientes entra. NAO usa cookie de
 * usuario nem token de login: e maquina falando com maquina, e por isso
 * tem identidade propria — assinatura Ed25519 por requisicao.
 *
 * O tenant vem da CHAVE que assinou, nunca do corpo. Um payload que traga
 * `tenantId` diferente do da chave e recusado.
 *
 * A resposta traz as contagens (criados, atualizados, sem mudanca...)
 * para que o Fiscale saiba o que aconteceu sem precisar consultar depois.
 */
import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/env";
import { audit } from "@/server/auth/audit";
import { parseCustomer, PayloadInvalido, type CustomerProjectionInput } from "@/server/customers/contract";
import { applyProjection, type SyncMode } from "@/server/customers/projection";
import {
  HEADER_KEY_ID,
  HEADER_NONCE,
  HEADER_SIGNATURE,
  HEADER_TIMESTAMP,
  verifySignedRequest,
} from "@/server/integrations/signature";
import { AppError } from "@/server/errors/app-error";
import { errorResponse } from "@/server/errors/http";
import { logger, newRequestId } from "@/server/logging/logger";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

interface SyncBody {
  source?: unknown;
  mode?: unknown;
  tenantId?: unknown;
  generatedAt?: unknown;
  customers?: unknown;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const requestId = newRequestId();
  const log = logger.child({ requestId });

  try {
    // Corpo CRU: a assinatura e sobre estes bytes. Reserializar depois de
    // um JSON.parse mudaria espacos e ordem, e o hash nao bateria.
    const rawBody = await req.text();

    const verificacao = await verifySignedRequest({
      method: "POST",
      path: new URL(req.url).pathname,
      headers: {
        keyId: req.headers.get(HEADER_KEY_ID),
        timestamp: req.headers.get(HEADER_TIMESTAMP),
        nonce: req.headers.get(HEADER_NONCE),
        signature: req.headers.get(HEADER_SIGNATURE),
      },
      rawBody,
    });

    if (!verificacao.ok) {
      log.warn("integration.sync.rejected", { code: verificacao.reason });
      // Para fora, sempre o mesmo 401. O codigo fica no log.
      throw new AppError("UNAUTHENTICATED", `integracao recusada: ${verificacao.reason}`, {
        details: { code: verificacao.reason },
      });
    }

    const { tenantId, source } = verificacao.identity;
    const escopo = log.child({ tenantId });

    let corpo: SyncBody;
    try {
      corpo = JSON.parse(rawBody) as SyncBody;
    } catch {
      throw new AppError("VALIDATION", "corpo nao e JSON valido");
    }

    // O tenant do payload, se vier, precisa concordar com o da chave.
    // Divergencia e erro de configuracao ou tentativa — nas duas o
    // silencio seria pior do que a recusa.
    if (typeof corpo.tenantId === "string" && corpo.tenantId !== tenantId) {
      escopo.error("integration.sync.tenant_mismatch", { kid: verificacao.identity.kid });
      await audit({
        tenantId,
        action: "FISCALE_SYNC_FAILED",
        metadata: { code: "TENANT_MISMATCH" },
      });
      throw new AppError("FORBIDDEN", "tenant do payload diverge da chave da integracao", {
        details: { code: "TENANT_MISMATCH" },
      });
    }

    if (typeof corpo.source === "string" && corpo.source !== source) {
      throw new AppError("VALIDATION", "source diverge da chave da integracao");
    }

    const mode: SyncMode = corpo.mode === "FULL" ? "FULL" : "INCREMENTAL";

    if (!Array.isArray(corpo.customers)) {
      throw new AppError("VALIDATION", "customers deveria ser uma lista");
    }
    if (corpo.customers.length > env.ELO_SYNC_MAX_CUSTOMERS) {
      throw new AppError(
        "VALIDATION",
        `lote acima do limite de ${env.ELO_SYNC_MAX_CUSTOMERS} clientes`,
      );
    }

    await audit({
      tenantId,
      action: "FISCALE_SYNC_STARTED",
      metadata: { mode, received: corpo.customers.length },
    });

    // Valida TUDO antes de gravar QUALQUER coisa. Meio lote aplicado com
    // o resto recusado deixaria o Fiscale sem saber o que reenviar.
    const clientes: CustomerProjectionInput[] = [];
    try {
      for (const bruto of corpo.customers) clientes.push(parseCustomer(bruto));
    } catch (erro) {
      if (erro instanceof PayloadInvalido) {
        escopo.warn("integration.sync.invalid_payload", { campo: erro.campo });
        await audit({
          tenantId,
          action: "FISCALE_SYNC_FAILED",
          metadata: { code: "INVALID_PAYLOAD", campo: erro.campo },
        });
        throw new AppError("VALIDATION", `payload invalido — ${erro.message}`, {
          details: { campo: erro.campo },
        });
      }
      throw erro;
    }

    // externalId repetido no mesmo lote: a ultima venceria em silencio, e
    // ninguem saberia que a origem mandou dois.
    const vistos = new Set<string>();
    for (const c of clientes) {
      if (vistos.has(c.externalId)) {
        throw new AppError("VALIDATION", `externalId repetido no lote: ${c.externalId}`, {
          details: { campo: "externalId" },
        });
      }
      vistos.add(c.externalId);
    }

    const r = await applyProjection(tenantId, source, mode, clientes);

    await audit({
      tenantId,
      action: "FISCALE_SYNC_COMPLETED",
      targetType: "sync_run",
      targetId: r.runId,
      metadata: {
        mode,
        received: r.received,
        created: r.created,
        updated: r.updated,
        unchanged: r.unchanged,
        markedMissing: r.markedMissing,
      },
    });
    if (r.created > 0) {
      await audit({ tenantId, action: "CUSTOMER_PROJECTION_CREATED", metadata: { count: r.created } });
    }
    if (r.updated > 0) {
      await audit({ tenantId, action: "CUSTOMER_PROJECTION_UPDATED", metadata: { count: r.updated } });
    }
    if (r.deactivated > 0) {
      await audit({
        tenantId,
        action: "CUSTOMER_PROJECTION_DEACTIVATED",
        metadata: { count: r.deactivated },
      });
    }

    return NextResponse.json(
      {
        runId: r.runId,
        mode,
        received: r.received,
        created: r.created,
        updated: r.updated,
        unchanged: r.unchanged,
        reactivated: r.reactivated,
        deactivated: r.deactivated,
        markedMissing: r.markedMissing,
        finishedAt: r.finishedAt,
      },
      { headers: { "cache-control": "no-store", "x-request-id": requestId } },
    );
  } catch (erro) {
    return errorResponse(erro, { requestId });
  }
}
