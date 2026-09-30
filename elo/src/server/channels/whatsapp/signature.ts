/**
 * `X-Hub-Signature-256` — a porta de entrada do webhook.
 *
 * ---------------------------------------------------------------------
 *  O que este arquivo impede
 * ---------------------------------------------------------------------
 *  O webhook é uma URL pública. Qualquer pessoa na internet pode fazer um
 *  POST para ela com um JSON parecido com o da Meta. Sem esta conferência,
 *  isso significaria: criar atendimento, inserir mensagem no histórico do
 *  cliente errado e disparar notificação para o escritório inteiro — de
 *  graça, para quem descobrisse o endereço.
 *
 *  A Meta assina o corpo com HMAC-SHA256 usando o App Secret. Quem não
 *  tem o segredo não produz a assinatura.
 *
 * ---------------------------------------------------------------------
 *  Três detalhes que fazem a diferença entre validar e fingir que valida
 * ---------------------------------------------------------------------
 *  1. **O corpo CRU.** O HMAC é sobre os bytes exatos que chegaram. Ler
 *     como JSON e serializar de novo muda espaço, ordem de chave e escape
 *     de acento — e a assinatura deixa de bater por motivo nenhum. Por
 *     isso a rota lê `await req.text()` e só depois faz o parse.
 *
 *  2. **Comparação em tempo constante.** `a === b` sai no primeiro byte
 *     diferente, e a diferença de tempo entre "errou no primeiro" e
 *     "errou no último" é medível pela rede. `timingSafeEqual` não tem
 *     esse vazamento.
 *
 *  3. **Falha fechada.** Sem segredo configurado, sem cabeçalho ou com
 *     formato estranho, a resposta é a mesma: NÃO É VÁLIDA. Nunca "passa
 *     porque não deu para conferir" — que é como uma configuração
 *     incompleta vira porta aberta em produção.
 */
import { createHmac, timingSafeEqual } from "node:crypto";

export type ResultadoAssinatura =
  | { valida: true }
  | { valida: false; motivo: "SEM_SEGREDO" | "SEM_CABECALHO" | "FORMATO" | "NAO_CONFERE" };

const PREFIXO = "sha256=";

/**
 * @param corpoCru os bytes exatos do corpo, sem `JSON.parse` no caminho.
 */
export function conferirAssinatura(
  corpoCru: string,
  cabecalho: string | null,
  segredo: string,
): ResultadoAssinatura {
  if (!segredo) return { valida: false, motivo: "SEM_SEGREDO" };
  if (!cabecalho) return { valida: false, motivo: "SEM_CABECALHO" };
  if (!cabecalho.startsWith(PREFIXO)) return { valida: false, motivo: "FORMATO" };

  const recebida = cabecalho.slice(PREFIXO.length);
  // 32 bytes em hexadecimal. Conferir o tamanho antes evita que
  // `timingSafeEqual` lance por buffers de tamanhos diferentes — e um
  // `throw` aqui seria um jeito de derrubar a rota com um cabeçalho curto.
  if (!/^[0-9a-f]{64}$/i.test(recebida)) return { valida: false, motivo: "FORMATO" };

  const esperada = createHmac("sha256", segredo).update(corpoCru, "utf8").digest("hex");

  const a = Buffer.from(recebida.toLowerCase(), "hex");
  const b = Buffer.from(esperada, "hex");
  if (a.length !== b.length) return { valida: false, motivo: "FORMATO" };

  return timingSafeEqual(a, b) ? { valida: true } : { valida: false, motivo: "NAO_CONFERE" };
}

/**
 * A assinatura que a Meta produziria para este corpo.
 *
 * Existe para o TESTE poder assinar um payload de verdade em vez de
 * simular a conferência. Um teste que substitui a validação por um `true`
 * não prova nada sobre a validação.
 */
export function assinar(corpoCru: string, segredo: string): string {
  return `${PREFIXO}${createHmac("sha256", segredo).update(corpoCru, "utf8").digest("hex")}`;
}
