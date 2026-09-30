/**
 * Escolha do provedor e montagem da chave.
 *
 * ---------------------------------------------------------------------
 *  A chave carrega o tenant, e não carrega o nome do arquivo
 * ---------------------------------------------------------------------
 *
 *      tenants/<tenantId>/messages/<ano>/<mês>/<32 hex>
 *
 *  Três decisões dentro disso:
 *
 *  1. O TENANT vem primeiro. Não é só organização: é a segunda camada de
 *     isolamento. Mesmo que uma consulta esquecesse o `where`, a chave de
 *     um tenant não se parece com a de outro, e a rota de download só
 *     chega à chave passando pelo banco com RLS aberto.
 *
 *  2. O nome original NÃO entra. `../../etc/passwd`, `CON`, emoji, 300
 *     caracteres, dois arquivos chamados `foto.jpg` — tudo isso deixa de
 *     ser problema quando o nome é apenas metadado. O nome sobrevive na
 *     coluna, para exibir e para o download.
 *
 *  3. O sufixo é ALEATÓRIO, não sequencial nem derivado do conteúdo.
 *     Sequencial se enumera; hash do conteúdo faria dois clientes que
 *     enviaram o mesmo PDF compartilharem caminho — e nenhum dos dois
 *     deveria saber disso.
 */
import { randomBytes } from "node:crypto";
import { join } from "node:path";

import { LocalStorageProvider } from "./local";
import type { StorageProvider } from "./provider";

export * from "./provider";

/**
 * Onde o provedor local guarda: `.storage/` na raiz do projeto.
 *
 * Fora de `public/` e fora de `.next/` — o primeiro viraria URL pública
 * permanente, o segundo some no próximo build.
 *
 * O caminho é CONSTANTE, e não configurável por ambiente, por um motivo
 * concreto: com o valor vindo de `process.env`, o Turbopack avisa que a
 * leitura de disco tem caminho dinâmico e passa a rastrear o projeto
 * inteiro, empacotando todo o código-fonte junto do servidor. Trocar um
 * risco de deploy por uma variável que só serve em desenvolvimento seria
 * mau negócio.
 *
 * Isto é o provedor de DESENVOLVIMENTO. Em produção o storage é S3/R2,
 * onde "onde fica" é a configuração do bucket — e não um caminho de
 * disco. Quem precisar de outro ponto de montagem local usa um link
 * simbólico.
 */
function raizLocal(): string {
  return join(process.cwd(), ".storage");
}

const cacheGlobal = globalThis as unknown as { eloStorage?: StorageProvider };

function criar(): StorageProvider {
  const escolhido = process.env.ELO_STORAGE_PROVIDER ?? "local";

  if (escolhido === "local") return new LocalStorageProvider(raizLocal());

  // Falha na partida, com o nome da variável. Um provedor desconhecido
  // que "cai no local" silenciosamente gravaria mídia de produção no
  // disco efêmero de um contêiner — e ninguém descobriria antes de o
  // contêiner reiniciar.
  throw new Error(
    `ELO_STORAGE_PROVIDER desconhecido: ${escolhido}. Hoje só existe "local"; ` +
      `S3/R2 entram implementando StorageProvider em src/server/storage/.`,
  );
}

export function storage(): StorageProvider {
  return (cacheGlobal.eloStorage ??= criar());
}

/** Só para teste: troca o provedor e devolve o anterior. */
export function trocarStorage(novo: StorageProvider): StorageProvider | undefined {
  const anterior = cacheGlobal.eloStorage;
  cacheGlobal.eloStorage = novo;
  return anterior;
}

export function montarChave(tenantId: string): string {
  const agora = new Date();
  const ano = agora.getUTCFullYear();
  const mes = String(agora.getUTCMonth() + 1).padStart(2, "0");
  const aleatorio = randomBytes(16).toString("hex");
  return `tenants/${tenantId}/messages/${ano}/${mes}/${aleatorio}`;
}
