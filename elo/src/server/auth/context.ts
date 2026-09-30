/**
 * O contexto autenticado: quem e a pessoa, em qual tenant, com qual papel.
 *
 * Este objeto so nasce de uma sessao validada no servidor. Nada aqui vem
 * do corpo da requisicao, de header ou de campo escondido — e a razao de o
 * `tenantId` ser confiavel o suficiente para abrir o contexto de RLS.
 */
import { AppError } from "@/server/errors/app-error";
import type { Role } from "@/generated/prisma/enums";

import { roleHas, type Permission } from "./permissions";

export interface AuthContext {
  sessionId: string;
  tenantId: string;
  membershipId: string;
  identityId: string;
  email: string;
  name: string;
  role: Role;
}

export function can(ctx: AuthContext, permission: Permission): boolean {
  return roleHas(ctx.role, permission);
}

/**
 * Barra a acao se faltar permissao. Lanca FORBIDDEN, cuja mensagem publica
 * e apenas "Acesso negado." — o motivo detalhado fica no log do servidor.
 */
export function requirePermission(ctx: AuthContext, permission: Permission): void {
  if (!can(ctx, permission)) {
    throw new AppError(
      "FORBIDDEN",
      `papel ${ctx.role} nao tem a permissao ${permission}`,
      { details: { permission, role: ctx.role } },
    );
  }
}
