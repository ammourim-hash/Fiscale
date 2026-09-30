/**
 * Sessão para páginas (Server Components).
 *
 * `requireAuth` do MVP 1.1 recebe um `NextRequest`, que não existe num
 * Server Component. Aqui a mesma resolução acontece a partir de `cookies()`.
 *
 * A regra não muda: o tenant vem da sessão validada no servidor. Esconder
 * itens de menu não é autorização — as rotas de API continuam exigindo
 * sessão por conta própria, e é lá que o dado é protegido.
 */
import { cookies } from "next/headers";

import { withTenant } from "@/server/tenancy";
import { logger } from "@/server/logging/logger";

import type { AuthContext } from "./context";
import { permissionsOf, type Permission } from "./permissions";
import { COOKIE_NAME, resolveSession } from "./session";

export interface PageSession {
  context: AuthContext;
  permissions: readonly Permission[];
  primaryDepartment: { id: string; slug: string; name: string } | null;
  departments: { id: string; slug: string; name: string }[];
}

/** Sessão da página, ou `null`. Quem decide o que fazer é a página. */
export async function pageSession(): Promise<PageSession | null> {
  const jar = await cookies();
  const r = await resolveSession(jar.get(COOKIE_NAME)?.value);

  if (!r.ok) {
    if (r.reason !== "NO_COOKIE") logger.warn("page.session.rejected", { code: r.reason });
    return null;
  }

  const ctx = r.context;

  const dados = await withTenant(ctx.tenantId, async (tx) => {
    const [vinculo, deptos] = await Promise.all([
      tx.membership.findUnique({
        where: { id: ctx.membershipId },
        select: { primaryDepartmentId: true },
      }),
      tx.departmentMembership.findMany({
        where: { membershipId: ctx.membershipId },
        select: { department: { select: { id: true, slug: true, name: true } } },
      }),
    ]);
    return {
      primaryId: vinculo?.primaryDepartmentId ?? null,
      departments: deptos.map((d) => d.department),
    };
  });

  return {
    context: ctx,
    permissions: permissionsOf(ctx.role),
    // Sem principal escolhido, fica nulo — não elegemos "o primeiro da
    // lista". A assinatura externa não pode mudar sozinha.
    primaryDepartment: dados.departments.find((d) => d.id === dados.primaryId) ?? null,
    departments: dados.departments,
  };
}
