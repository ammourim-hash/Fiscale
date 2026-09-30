/**
 * O unico caminho para dado de tenant.
 *
 * ---------------------------------------------------------------------
 *  O problema que este arquivo resolve
 * ---------------------------------------------------------------------
 *  O RLS do Postgres decide o que devolver lendo `app.tenant_id` da sessao.
 *  "Sessao", no Postgres, e a CONEXAO — e conexao vive num pool, sendo
 *  emprestada e devolvida o tempo todo. Se o valor fosse gravado com
 *
 *      SET app.tenant_id = '<A>'
 *
 *  ele ficaria colado na conexao depois que a requisicao do tenant A
 *  terminasse. A proxima requisicao — do tenant B — poderia receber
 *  exatamente aquela conexao e, se por qualquer motivo nao redefinisse o
 *  valor, leria os dados de A. Sem erro, sem log, sem sintoma.
 *
 *  A solucao e nunca sair do escopo da transacao:
 *
 *      BEGIN
 *      SELECT set_config('app.tenant_id', '<A>', true);   -- `true` = LOCAL
 *      ... consultas ...
 *      COMMIT                                             -- o Postgres desfaz
 *
 *  Com o terceiro argumento `true`, o proprio banco reverte o ajuste no
 *  COMMIT e no ROLLBACK. Nao ha caminho — nem excecao, nem timeout, nem
 *  `return` no meio — que devolva a conexao ao pool com contexto de tenant.
 *  tests/pool-reuse.test.ts prova isso com o pool limitado a uma conexao.
 *
 * ---------------------------------------------------------------------
 *  A regra
 * ---------------------------------------------------------------------
 *  `SET LOCAL` nao se escreve em nenhum outro lugar do projeto. Quem
 *  precisa de dado protegido chama withTenant(). O cliente cru de
 *  ../db/client so aparece aqui e na sonda de saude.
 */
import { randomUUID } from "node:crypto";

import { prisma } from "@/server/db/client";
import { TenantContextError } from "@/server/errors/app-error";
import type { Prisma } from "@/generated/prisma/client";

/** Cliente restrito a um tenant. Nao expoe `$transaction` nem `$connect`. */
export type TenantClient = Prisma.TransactionClient;

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * Falha fechada, e o mais cedo possivel. Sem um uuid valido nao se abre
 * transacao nenhuma: melhor um erro aqui do que uma consulta que roda com
 * `app.tenant_id` vazio e devolve zero linha por acidente em vez de por
 * decisao.
 */
export function assertTenantId(valor: unknown): asserts valor is string {
  if (typeof valor !== "string" || !UUID_V4.test(valor)) {
    throw new TenantContextError(
      "operacao sem tenant valido no contexto — recusada antes de tocar o banco",
      { recebido: typeof valor },
    );
  }
}

export interface WithTenantOptions {
  /** Tempo maximo da transacao. O padrao do Prisma (5 s) e curto para lote. */
  timeoutMs?: number;
}

/**
 * Executa `fn` dentro de uma transacao com o tenant fixado.
 *
 * Tudo o que `fn` fizer com o cliente recebido passa pelo RLS. Consulta que
 * "esqueceu" o `where tenantId` nao vaza: o banco filtra de qualquer jeito.
 */
export async function withTenant<T>(
  tenantId: string,
  fn: (tx: TenantClient) => Promise<T>,
  options: WithTenantOptions = {},
): Promise<T> {
  return withTenantOn(prisma, tenantId, fn, options);
}

/** O minimo que `withTenantOn` precisa de um cliente Prisma. */
export interface TenantCapableClient {
  $transaction<T>(
    fn: (tx: TenantClient) => Promise<T>,
    options?: { timeout?: number },
  ): Promise<T>;
}

/**
 * A mesma coisa, sobre um cliente informado.
 *
 * Existe para a ferramenta administrativa, que conecta com outro papel
 * (elo_owner) e ainda assim precisa abrir o contexto exatamente do mesmo
 * jeito. Duas maneiras de fixar o tenant seriam duas maneiras de errar.
 */
export async function withTenantOn<T>(
  client: TenantCapableClient,
  tenantId: string,
  fn: (tx: TenantClient) => Promise<T>,
  options: WithTenantOptions = {},
): Promise<T> {
  assertTenantId(tenantId);

  return client.$transaction(
    async (tx) => {
      // set_config(..., true) = LOCAL a transacao. O `true` e a linha mais
      // importante deste arquivo.
      await tx.$executeRaw`SELECT set_config('app.tenant_id', ${tenantId}::text, true)`;
      return fn(tx);
    },
    { timeout: options.timeoutMs ?? 10_000 },
  );
}

/**
 * Gera o id de um tenant novo.
 *
 * A politica de `tenants` exige `id = app_tenant_id()`, entao o id nasce
 * fora do banco e a transacao ja abre com ele fixado. Consequencia boa:
 * nem para criar tenant e preciso conexao privilegiada. Quem usa isto e a
 * ferramenta administrativa — ver src/server/admin/provisioning.ts.
 */
export function newTenantId(): string {
  return randomUUID();
}
