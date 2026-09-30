/**
 * Busca na projecao, e o casamento telefone -> cliente.
 *
 * ---------------------------------------------------------------------
 *  Por que findCustomerByPhone mora aqui, sozinho
 * ---------------------------------------------------------------------
 *  Quando o WhatsApp entrar (MVP 1.7), o adapter vai chamar ESTA funcao.
 *  Se a logica de telefone estiver espalhada por rotas, cada lugar vai
 *  resolver o empate de um jeito, e o pior deles — "pega o primeiro" —
 *  entrega a conversa de um cliente na tela de outro.
 *
 *  Por isso o resultado tem tres estados, e AMBIGUOUS e um deles. Dois
 *  clientes com o mesmo telefone acontece de verdade: matriz e filial,
 *  contador que atende duas empresas, celular do socio. Nesses casos a
 *  resposta certa e "nao sei", e quem decide e a pessoa no atendimento.
 */
import { withTenant } from "@/server/tenancy";

import { documentSearchKey } from "./document";
import { phoneSearchKey } from "./phone";

export interface CustomerSummary {
  id: string;
  source: string;
  externalId: string;
  displayName: string;
  documentDigits: string | null;
  email: string | null;
  active: boolean;
  /** Quando a origem confirmou este registro pela ultima vez. */
  syncedAt: Date;
  missingSince: Date | null;
  phones: { raw: string; e164: string | null; status: string }[];
}

const SELECT = {
  id: true,
  source: true,
  externalId: true,
  displayName: true,
  documentDigits: true,
  email: true,
  active: true,
  syncedAt: true,
  missingSince: true,
  phones: { select: { raw: true, e164: true, status: true } },
} as const;

export type PhoneMatch =
  | { status: "EXACT"; customer: CustomerSummary }
  | { status: "NONE"; reason: "NOT_NORMALIZABLE" | "NOT_FOUND" }
  | { status: "AMBIGUOUS"; candidates: CustomerSummary[] };

/**
 * Telefone -> cliente, dentro do tenant.
 *
 * Nao normalizou com certeza? `NONE` com `NOT_NORMALIZABLE`. Melhor dizer
 * "nao sei" do que procurar por um palpite e achar o cliente errado.
 */
export async function findCustomerByPhone(
  tenantId: string,
  telefone: string,
): Promise<PhoneMatch> {
  const chave = phoneSearchKey(telefone);
  if (!chave) return { status: "NONE", reason: "NOT_NORMALIZABLE" };

  const achados = await withTenant(tenantId, async (tx) =>
    tx.externalCustomerReference.findMany({
      // So telefones que normalizaram participam do casamento. Numero com
      // status NO_AREA_CODE fica visivel na tela, mas nao casa sozinho.
      where: { phones: { some: { e164: chave } } },
      select: SELECT,
      orderBy: { displayName: "asc" },
    }),
  );

  if (achados.length === 0) return { status: "NONE", reason: "NOT_FOUND" };
  if (achados.length === 1) return { status: "EXACT", customer: achados[0]! };
  return { status: "AMBIGUOUS", candidates: achados };
}

export interface SearchOptions {
  /** Inativos ficam de fora por padrao; o historico continua existindo. */
  includeInactive?: boolean;
  limit?: number;
}

/**
 * Busca por nome, documento ou telefone. O tipo do termo e deduzido, e a
 * deducao e conservadora: so vira busca por telefone o que normaliza com
 * certeza; so vira busca por documento o que tem digitos suficientes.
 */
export async function searchCustomers(
  tenantId: string,
  termo: string,
  options: SearchOptions = {},
): Promise<CustomerSummary[]> {
  const t = termo.trim();
  const limit = Math.min(options.limit ?? 50, 100);

  // Sem termo, a resposta certa é a carteira — não uma lista vazia. A tela
  // de clientes abre assim, e devolver nada ali faria parecer que a
  // sincronização falhou.
  if (t.length === 0) {
    return withTenant(tenantId, async (tx) =>
      tx.externalCustomerReference.findMany({
        where: options.includeInactive ? {} : { active: true },
        select: SELECT,
        orderBy: { displayName: "asc" },
        take: limit,
      }),
    );
  }

  // Uma letra só casaria com quase tudo; não é busca, é ruído.
  if (t.length < 2) return [];
  const porTelefone = phoneSearchKey(t);
  const porDocumento = documentSearchKey(t);

  return withTenant(tenantId, async (tx) =>
    tx.externalCustomerReference.findMany({
      where: {
        ...(options.includeInactive ? {} : { active: true }),
        OR: [
          { displayName: { contains: t, mode: "insensitive" } },
          ...(porDocumento ? [{ documentDigits: { startsWith: porDocumento } }] : []),
          ...(porTelefone ? [{ phones: { some: { e164: porTelefone } } }] : []),
        ],
      },
      select: SELECT,
      orderBy: { displayName: "asc" },
      take: limit,
    }),
  );
}

export async function getCustomer(
  tenantId: string,
  id: string,
): Promise<CustomerSummary | null> {
  return withTenant(tenantId, async (tx) =>
    tx.externalCustomerReference.findUnique({ where: { id }, select: SELECT }),
  );
}

export interface ProjectionStatus {
  total: number;
  active: number;
  missing: number;
  lastSyncAt: Date | null;
  lastSyncMode: string | null;
  lastSyncStatus: string | null;
}

/** Diagnostico: "sincronizado em", quantas projecoes, quantas ausentes. */
export async function projectionStatus(tenantId: string): Promise<ProjectionStatus> {
  return withTenant(tenantId, async (tx) => {
    const [total, active, missing, ultima] = await Promise.all([
      tx.externalCustomerReference.count(),
      tx.externalCustomerReference.count({ where: { active: true } }),
      tx.externalCustomerReference.count({ where: { missingSince: { not: null } } }),
      tx.syncRun.findFirst({
        orderBy: { startedAt: "desc" },
        select: { finishedAt: true, startedAt: true, mode: true, status: true },
      }),
    ]);

    return {
      total,
      active,
      missing,
      lastSyncAt: ultima?.finishedAt ?? ultima?.startedAt ?? null,
      lastSyncMode: ultima?.mode ?? null,
      lastSyncStatus: ultima?.status ?? null,
    };
  });
}
