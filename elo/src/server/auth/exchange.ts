/**
 * A troca: token curto do Fiscale -> sessao propria do Elo.
 *
 * A ordem dos passos e deliberada, do mais barato e mais decisivo para o
 * mais caro:
 *
 *   1. assinatura e claims   — nao toca no banco; token forjado morre aqui
 *   2. consumo do jti        — atomico; replay morre aqui, antes de existir
 *                              qualquer efeito colateral
 *   3. tenant, pessoa, vinculo — dentro do contexto de RLS do tenant do token
 *   4. sessao                — so entao
 *
 * O passo 2 vem antes do 3 de proposito. Se viesse depois, duas
 * requisicoes simultaneas poderiam validar o mesmo token em paralelo e so
 * colidir no fim, com uma delas ja tendo criado sessao.
 *
 * Toda recusa devolve o MESMO erro para fora. O motivo vai para o log e
 * para a auditoria com codigo estruturado. Dizer "usuario nao existe" e
 * "usuario inativo" separadamente entrega ao atacante um oraculo de quem
 * trabalha no escritorio.
 */
import { env } from "@/env";
import { withTenant } from "@/server/tenancy";
import { logger } from "@/server/logging/logger";
import { AppError } from "@/server/errors/app-error";

import { audit } from "./audit";
import { verifyExchangeToken, type ExchangeFailure } from "./exchange-token";
import { consumeJti, purgeExpiredJtis } from "./replay";
import { createSession, hashIp, type IssuedSession, type SessionMeta } from "./session";

export type ExchangeRejection =
  | ExchangeFailure
  | "REPLAY"
  | "TENANT_NOT_FOUND"
  | "TENANT_INACTIVE"
  | "IDENTITY_NOT_FOUND"
  | "IDENTITY_INACTIVE"
  | "MEMBERSHIP_NOT_FOUND"
  | "MEMBERSHIP_INACTIVE";

export type ExchangeOutcome =
  | { ok: true; session: IssuedSession; tenantId: string; membershipId: string }
  | { ok: false; reason: ExchangeRejection };

const PROVEDOR = "fiscale";

export async function exchange(
  token: string,
  meta: SessionMeta = {},
): Promise<ExchangeOutcome> {
  const ipHash = hashIp(meta.ip);

  // ── 1. Assinatura e claims ────────────────────────────────────────
  const verificado = verifyExchangeToken(token);
  if (!verificado.ok) {
    logger.warn("auth.exchange.rejected", { code: verificado.reason });
    return { ok: false, reason: verificado.reason };
  }

  const { claims } = verificado;

  // ── 2. Uso unico ──────────────────────────────────────────────────
  // Antes de qualquer consulta de negocio: o token queima na primeira
  // tentativa, mesmo que ela venha a falhar por outro motivo adiante.
  // Um token que falhou por vinculo invalido nao deve poder ser tentado
  // de novo depois que o vinculo for criado.
  const consumo = await consumeJti(claims.jti, new Date(claims.exp * 1000));
  if (!consumo.first) {
    logger.warn("auth.exchange.rejected", { code: "REPLAY", jti: claims.jti });
    await audit({
      tenantId: claims.tid,
      action: "AUTH_EXCHANGE_FAILED",
      metadata: { code: "REPLAY" },
      ipHash,
    });
    return { ok: false, reason: "REPLAY" };
  }

  // ── 3. Tenant, pessoa e vinculo ───────────────────────────────────
  // Tudo dentro do contexto do tenant que veio ASSINADO no token. Se o
  // tenant nao existir, o contexto abre vazio e nada e encontrado — a
  // mesma falha fechada do MVP 1.0.
  let alvo: { membershipId: string; identityId: string } | null = null;
  let recusa: ExchangeRejection | null = null;

  try {
    await withTenant(claims.tid, async (tx) => {
      const tenant = await tx.tenant.findFirst({ select: { id: true, active: true } });
      if (!tenant) {
        recusa = "TENANT_NOT_FOUND";
        return;
      }
      if (!tenant.active) {
        recusa = "TENANT_INACTIVE";
        return;
      }

      // A identidade so e visivel se tiver membership neste tenant — e a
      // politica de RLS que garante isso, nao este `where`. Pessoa de
      // outro escritorio simplesmente nao aparece.
      const provedor = await tx.identityProvider.findFirst({
        where: { provider: PROVEDOR, providerUid: claims.sub },
        select: {
          identity: {
            select: {
              id: true,
              active: true,
              memberships: {
                where: { tenantId: claims.tid },
                select: { id: true, active: true },
              },
            },
          },
        },
      });

      if (!provedor) {
        recusa = "IDENTITY_NOT_FOUND";
        return;
      }
      if (!provedor.identity.active) {
        recusa = "IDENTITY_INACTIVE";
        return;
      }

      const vinculo = provedor.identity.memberships[0];
      if (!vinculo) {
        recusa = "MEMBERSHIP_NOT_FOUND";
        return;
      }
      if (!vinculo.active) {
        recusa = "MEMBERSHIP_INACTIVE";
        return;
      }

      alvo = { membershipId: vinculo.id, identityId: provedor.identity.id };
    });
  } catch (erro) {
    // Tenant com uuid malformado no claim cai aqui.
    logger.warn("auth.exchange.rejected", { code: "TENANT_NOT_FOUND", erro });
    recusa = "TENANT_NOT_FOUND";
  }

  if (recusa !== null || alvo === null) {
    const code: ExchangeRejection = recusa ?? "MEMBERSHIP_NOT_FOUND";
    logger.warn("auth.exchange.rejected", { code });
    await audit({
      tenantId: claims.tid,
      action: "AUTH_EXCHANGE_FAILED",
      metadata: { code },
      ipHash,
    });
    return { ok: false, reason: code };
  }

  // ── 4. Sessao ─────────────────────────────────────────────────────
  const encontrado: { membershipId: string; identityId: string } = alvo;
  const sessao = await createSession(
    { tenantId: claims.tid, membershipId: encontrado.membershipId },
    meta,
  );

  logger.info("auth.exchange.ok", {
    tenantId: claims.tid,
    membershipId: encontrado.membershipId,
    sessionId: sessao.sessionId,
  });

  await audit({
    tenantId: claims.tid,
    membershipId: encontrado.membershipId,
    action: "AUTH_EXCHANGE_SUCCESS",
    ipHash,
  });
  await audit({
    tenantId: claims.tid,
    membershipId: encontrado.membershipId,
    action: "LOGIN_SESSION_CREATED",
    targetType: "session",
    targetId: sessao.sessionId,
    ipHash,
  });

  // Faxina oportunista dos jti ja expirados. Falhar aqui nao pode
  // atrapalhar quem acabou de entrar.
  void purgeExpiredJtis().catch((erro: unknown) =>
    logger.warn("auth.exchange.purge_failed", { erro }),
  );

  return {
    ok: true,
    session: sessao,
    tenantId: claims.tid,
    membershipId: encontrado.membershipId,
  };
}

/**
 * O erro unico que sai para fora. Sempre o mesmo texto, sempre 401,
 * qualquer que tenha sido o motivo.
 */
export function exchangeDenied(reason: ExchangeRejection): AppError {
  return new AppError("UNAUTHENTICATED", `troca recusada: ${reason}`, {
    details: { code: reason },
  });
}

export const EXCHANGE_ISSUER = env.ELO_EXCHANGE_ISSUER;
