/**
 * O cliente Prisma. Um por processo.
 *
 * Conecta com a DATABASE_URL, que e o papel elo_app: sem SUPERUSER, sem
 * BYPASSRLS, sem DDL. Isso importa — superusuario ignora RLS em silencio, e
 * uma politica que nao vale nao avisa que nao vale.
 *
 * Este modulo NAO e o que o resto do codigo usa para ler dados de tenant.
 * Quem le dado protegido passa por withTenant() em ../tenancy. O cliente
 * cru fica aqui para: (1) a sonda de saude, (2) a propria withTenant abrir a
 * transacao.
 */
import { PrismaPg } from "@prisma/adapter-pg";

import { env } from "@/env";
import { PrismaClient } from "@/generated/prisma/client";

export type { Prisma } from "@/generated/prisma/client";

/**
 * Tamanho do pool. Baixo de proposito no MVP: o unico consumidor e este
 * processo. Sobe quando houver carga medida, nao antes.
 *
 * O pool e justamente o motivo de o contexto de tenant ser transacional:
 * conexao voltando suja para o pool e o vazamento classico de RLS. Ver
 * tests/pool-reuse.test.ts, que roda com max = 1 para forcar o reuso.
 */
function poolMax(): number {
  const bruto = process.env.ELO_DB_POOL_MAX;
  const n = bruto ? Number.parseInt(bruto, 10) : Number.NaN;
  return Number.isFinite(n) && n > 0 ? n : 10;
}

function criar(): PrismaClient {
  const adapter = new PrismaPg({
    connectionString: env.DATABASE_URL,
    max: poolMax(),
    // Conexao presa e pior do que conexao recusada: falha rapido.
    connectionTimeoutMillis: 5_000,
    idleTimeoutMillis: 30_000,
  });

  return new PrismaClient({ adapter });
}

/**
 * Em desenvolvimento o Next recarrega o modulo a cada alteracao. Sem o
 * cache global, cada recarga abriria um pool novo e o Postgres esgotaria as
 * conexoes em poucos minutos.
 */
const cacheGlobal = globalThis as unknown as { eloPrisma?: PrismaClient };

export const prisma: PrismaClient = cacheGlobal.eloPrisma ?? criar();

if (env.NODE_ENV !== "production") {
  cacheGlobal.eloPrisma = prisma;
}

/** Fecha o pool. Usado pelos testes; em producao o processo simplesmente morre. */
export async function disconnect(): Promise<void> {
  await prisma.$disconnect();
  delete cacheGlobal.eloPrisma;
}
