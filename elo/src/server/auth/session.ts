/**
 * Sessao do Elo.
 *
 * ---------------------------------------------------------------------
 *  Sessao no banco, nao JWT
 * ---------------------------------------------------------------------
 *  JWT de sessao nao se revoga. "Encerrar sessao" viraria promessa vazia:
 *  o token continuaria valido ate expirar, e demitir alguem as 9h nao o
 *  tiraria do sistema as 9h01. Sessao em tabela se apaga.
 *
 * ---------------------------------------------------------------------
 *  O cookie: `<tenantId>.<token>`
 * ---------------------------------------------------------------------
 *  Ha um problema de ordem. Para LER a tabela de sessoes e preciso ter o
 *  contexto de RLS aberto — e o contexto exige saber o tenant, que so se
 *  descobre lendo a sessao. Circular.
 *
 *  As saidas seriam: uma funcao SECURITY DEFINER que escapa do RLS, ou um
 *  papel com BYPASSRLS. As duas abrem, no codigo, a porta que o MVP 1.0
 *  fechou com cuidado.
 *
 *  A saida escolhida e mais simples: o cookie carrega o tenant junto com
 *  o token. E preciso ser claro sobre o que isso significa, porque parece
 *  violar "nunca aceite tenantId vindo do navegador":
 *
 *      o tenant do cookie NAO e autoridade. E chave de busca.
 *
 *  A consulta exige que o par (tenant, hash do token) case com uma linha
 *  real. Trocar o tenant no cookie nao da acesso ao tenant B — da 401,
 *  porque no tenant B nao existe sessao com aquele hash. O segredo
 *  continua sendo so o token. O tenant e a etiqueta que diz em qual
 *  gaveta procurar; a chave que abre e outra. Ha teste para isso.
 *
 * ---------------------------------------------------------------------
 *  O que fica gravado
 * ---------------------------------------------------------------------
 *  SHA-256 do token, nunca o token. Quem levar um backup leva hashes:
 *  para entrar precisaria do valor original, que existe apenas no cookie
 *  do navegador de quem esta logado. Senha nao aparece em lugar nenhum —
 *  quem autentica e o Fiscale.
 */
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

import { env } from "@/env";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import type { AuthContext } from "./context";

export const COOKIE_NAME = "elo_sessao";

const SEPARADOR = ".";

export function hashToken(token: string): string {
  return createHash("sha256").update(token, "utf8").digest("hex");
}

/** Guardamos o IP como hash: serve para comparar origem sem manter o dado. */
export function hashIp(ip: string | null | undefined): string | null {
  if (!ip) return null;
  return createHash("sha256").update(`${env.ELO_IP_HASH_SALT}${ip}`, "utf8").digest("hex");
}

export interface IssuedSession {
  /** Valor do cookie: `<tenantId>.<token>`. So existe aqui e no navegador. */
  cookieValue: string;
  sessionId: string;
  expiresAt: Date;
}

export interface SessionSubject {
  tenantId: string;
  membershipId: string;
}

export interface SessionMeta {
  ip?: string | null;
  userAgent?: string | null;
}

export async function createSession(
  subject: SessionSubject,
  meta: SessionMeta = {},
): Promise<IssuedSession> {
  // 32 bytes de aleatorio criptografico. Nada derivado de id, e-mail ou
  // hora — o token nao pode ser adivinhavel a partir de nada visivel.
  const token = randomBytes(32).toString("base64url");
  const tokenHash = hashToken(token);
  const expiresAt = new Date(Date.now() + env.ELO_SESSION_TTL_HOURS * 3_600_000);

  const sessao = await withTenant(subject.tenantId, async (tx) =>
    tx.session.create({
      data: {
        tenantId: subject.tenantId,
        membershipId: subject.membershipId,
        tokenHash,
        expiresAt,
        ipHash: hashIp(meta.ip),
        userAgent: meta.userAgent?.slice(0, 300) ?? null,
      },
      select: { id: true },
    }),
  );

  return {
    cookieValue: `${subject.tenantId}${SEPARADOR}${token}`,
    sessionId: sessao.id,
    expiresAt,
  };
}

export type SessionFailure =
  | "NO_COOKIE"
  | "MALFORMED_COOKIE"
  | "NOT_FOUND"
  | "EXPIRED"
  | "REVOKED"
  | "MEMBERSHIP_INACTIVE";

export type SessionResolution =
  | { ok: true; context: AuthContext }
  | { ok: false; reason: SessionFailure };

function partirCookie(valor: string): { tenantId: string; token: string } | null {
  const corte = valor.indexOf(SEPARADOR);
  if (corte <= 0) return null;
  const tenantId = valor.slice(0, corte);
  const token = valor.slice(corte + 1);
  if (!tenantId || !token) return null;
  return { tenantId, token };
}

/**
 * Traduz o cookie em contexto autenticado, ou recusa.
 *
 * Toda recusa e igual para quem esta do lado de fora: 401 sem detalhe. O
 * motivo especifico volta so para o log.
 */
export async function resolveSession(
  cookieValue: string | undefined,
  agora: Date = new Date(),
): Promise<SessionResolution> {
  if (!cookieValue) return { ok: false, reason: "NO_COOKIE" };

  const partes = partirCookie(cookieValue);
  if (!partes) return { ok: false, reason: "MALFORMED_COOKIE" };

  // Um tenant invalido no cookie nem chega ao banco: withTenant recusa.
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  if (!UUID.test(partes.tenantId)) return { ok: false, reason: "MALFORMED_COOKIE" };

  const tokenHash = hashToken(partes.token);

  const achado = await withTenant(partes.tenantId, async (tx) =>
    tx.session.findFirst({
      // O RLS ja limita ao tenant; repetir o filtro aqui e a primeira
      // camada fazendo a parte dela em vez de confiar na segunda.
      where: { tokenHash, tenantId: partes.tenantId },
      select: {
        id: true,
        tenantId: true,
        expiresAt: true,
        revokedAt: true,
        tokenHash: true,
        membership: {
          select: {
            id: true,
            role: true,
            active: true,
            identity: { select: { id: true, email: true, name: true, active: true } },
          },
        },
      },
    }),
  );

  if (!achado) return { ok: false, reason: "NOT_FOUND" };

  // A busca por hash ja e uma igualdade exata no indice; a comparacao em
  // tempo constante aqui e barata e fecha a discussao sobre timing.
  const a = Buffer.from(achado.tokenHash, "utf8");
  const b = Buffer.from(tokenHash, "utf8");
  if (a.length !== b.length || !timingSafeEqual(a, b)) {
    return { ok: false, reason: "NOT_FOUND" };
  }

  if (achado.revokedAt) return { ok: false, reason: "REVOKED" };
  if (achado.expiresAt <= agora) return { ok: false, reason: "EXPIRED" };
  if (!achado.membership.active || !achado.membership.identity.active) {
    // Desativar a pessoa derruba a sessao na hora, sem esperar expirar.
    return { ok: false, reason: "MEMBERSHIP_INACTIVE" };
  }

  return {
    ok: true,
    context: {
      sessionId: achado.id,
      tenantId: achado.tenantId,
      membershipId: achado.membership.id,
      identityId: achado.membership.identity.id,
      email: achado.membership.identity.email,
      name: achado.membership.identity.name,
      role: achado.membership.role as Role,
    },
  };
}

/** Marca a atividade. Barato o suficiente para rodar a cada requisicao. */
export async function touchSession(ctx: AuthContext): Promise<void> {
  await withTenant(ctx.tenantId, async (tx) => {
    await tx.session.updateMany({
      where: { id: ctx.sessionId, revokedAt: null },
      data: { lastSeenAt: new Date() },
    });
  });
}

export async function revokeSession(ctx: AuthContext): Promise<void> {
  await withTenant(ctx.tenantId, async (tx) => {
    await tx.session.updateMany({
      where: { id: ctx.sessionId, revokedAt: null },
      data: { revokedAt: new Date() },
    });
  });
}

/**
 * "Encerrar todas as outras sessoes". A atual sobrevive de proposito —
 * quem clicou nao quer se deslogar.
 */
export async function revokeOtherSessions(ctx: AuthContext): Promise<number> {
  return withTenant(ctx.tenantId, async (tx) => {
    const r = await tx.session.updateMany({
      where: {
        membershipId: ctx.membershipId,
        id: { not: ctx.sessionId },
        revokedAt: null,
      },
      data: { revokedAt: new Date() },
    });
    return r.count;
  });
}

/** Todas as sessoes de uma pessoa. Usado ao desativar um membership. */
export async function revokeAllSessionsOfMembership(
  tenantId: string,
  membershipId: string,
): Promise<number> {
  return withTenant(tenantId, async (tx) => {
    const r = await tx.session.updateMany({
      where: { membershipId, revokedAt: null },
      data: { revokedAt: new Date() },
    });
    return r.count;
  });
}

export interface CookieOptions {
  name: string;
  value: string;
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  expires?: Date;
  maxAge?: number;
}

export function sessionCookie(sessao: IssuedSession): CookieOptions {
  return {
    name: COOKIE_NAME,
    value: sessao.cookieValue,
    // Sem acesso por JavaScript: XSS na pagina nao leva a sessao embora.
    httpOnly: true,
    // Em desenvolvimento o Elo roda em http; exigir Secure ali impediria
    // o cookie de existir. Em producao e obrigatorio.
    secure: env.ELO_COOKIE_SECURE,
    // Lax, e nao Strict: a entrada vem de uma navegacao iniciada pelo
    // Fiscale, que e outra origem. Com Strict o cookie nao acompanharia
    // esse primeiro salto e o usuario cairia deslogado logo apos entrar.
    sameSite: "lax",
    path: "/",
    expires: sessao.expiresAt,
  };
}

export function clearedCookie(): CookieOptions {
  return {
    name: COOKIE_NAME,
    value: "",
    httpOnly: true,
    secure: env.ELO_COOKIE_SECURE,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  };
}
