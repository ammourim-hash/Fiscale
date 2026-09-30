/**
 * Sonda do banco. Nao toca em tabela de tenant, so confirma que a conexao
 * responde. O que ela devolve para fora e um booleano e uma latencia —
 * nunca host, usuario, versao ou mensagem do driver.
 */
import { logger } from "@/server/logging/logger";

import { prisma } from "./client";

export interface DatabaseHealth {
  reachable: boolean;
  latencyMs: number;
}

export async function checkDatabase(): Promise<DatabaseHealth> {
  const inicio = performance.now();
  try {
    await prisma.$queryRaw`SELECT 1`;
    return { reachable: true, latencyMs: Math.round(performance.now() - inicio) };
  } catch (erro) {
    // O detalhe fica no log do servidor; a resposta HTTP so vera `false`.
    logger.error("health.database.unreachable", { erro });
    return { reachable: false, latencyMs: Math.round(performance.now() - inicio) };
  }
}
