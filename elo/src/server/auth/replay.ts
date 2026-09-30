/**
 * Protecao contra replay do token de troca.
 *
 * ---------------------------------------------------------------------
 *  Por que nao Redis
 * ---------------------------------------------------------------------
 *  A decisao original previa `jti` no Redis com TTL. Redis resolveria bem
 *  — mas nao ha Redis no MVP, o volume aqui e de alguns tokens por dia, e
 *  o Postgres ja esta no ar com garantia transacional. Trazer um segundo
 *  banco para guardar dezenas de linhas seria pagar operacao (subir,
 *  monitorar, fazer backup, tratar indisponibilidade) sem ganho.
 *
 *  E ha um ponto tecnico a favor do Postgres: a atomicidade vem de graca.
 *
 * ---------------------------------------------------------------------
 *  Por que UM comando, e nao dois
 * ---------------------------------------------------------------------
 *  A implementacao ingenua e:
 *
 *      1. SELECT ... WHERE jti = $1     -> nao existe
 *      2. INSERT
 *
 *  Duas requisicoes simultaneas com o MESMO token executam o passo 1
 *  antes de qualquer uma chegar ao passo 2. As duas veem "nao existe", as
 *  duas seguem, e o token e usado duas vezes. A janela e de milissegundos
 *  — que e exatamente o intervalo em que um replay automatizado opera.
 *
 *  Aqui existe UM comando:
 *
 *      INSERT ... ON CONFLICT DO NOTHING
 *
 *  Quem grava a linha e a primeira; a segunda colide com a chave primaria
 *  e afeta zero linhas. A decisao e do indice, dentro do banco, sem
 *  intervalo em que as duas possam passar. Nao ha `SELECT` em lugar
 *  nenhum — nem seria possivel: o elo_app nao tem leitura nesta tabela.
 *
 *  O alvo do conflito e omitido de proposito. Escrever `ON CONFLICT (jti)`
 *  faria o Postgres exigir privilegio de SELECT sobre a coluna, e dar
 *  leitura aqui abriria a enumeracao de jti que a tabela existe para
 *  evitar. Sem alvo, a semantica e a mesma: `jti` e a chave primaria e a
 *  unica restricao da tabela, entao nao ha outro conflito a confundir.
 */
import { prisma } from "@/server/db/client";

export interface ConsumeResult {
  /** true = este e o primeiro uso. false = replay, recusar. */
  first: boolean;
}

/**
 * Marca o jti como usado. Roda FORA de withTenant de proposito: o consumo
 * precisa valer globalmente, e nao depender de o tenant do token existir.
 */
export async function consumeJti(jti: string, expiresAt: Date): Promise<ConsumeResult> {
  const afetadas = await prisma.$executeRaw`
    INSERT INTO consumed_exchange_tokens (jti, expires_at)
    VALUES (${jti}, ${expiresAt})
    ON CONFLICT DO NOTHING
  `;
  return { first: afetadas === 1 };
}

/**
 * Faxina dos expirados. Depois de `exp` o token ja e recusado pela
 * validacao de tempo, entao guardar a linha nao protege mais nada.
 *
 * Chamada de forma oportunista no proprio fluxo de troca — no volume
 * desta fase, uma tarefa agendada so para isso seria peso morto.
 */
export async function purgeExpiredJtis(): Promise<number> {
  return prisma.$executeRaw`
    DELETE FROM consumed_exchange_tokens WHERE expires_at < now()
  `;
}
