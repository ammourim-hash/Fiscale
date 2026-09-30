/**
 * Modo embutido: o Elo desenhado DENTRO de outra casca (o FISCALE), sem
 * mostrar a sua.
 *
 * É só layout. Não cria sessão, não decide identidade, não muda API, não
 * encosta em cookie. A autenticação continua sendo a mesma `pageSession()`
 * de sempre, resolvida no servidor antes de qualquer render — embutido ou
 * não, quem não tem sessão não vê tela nenhuma.
 *
 * A decisão vem da URL pedida (`?embutido=1`), que o middleware já entrega
 * ao layout no cabeçalho `x-elo-caminho`. De propósito NÃO usamos cookie
 * nem localStorage: um estado guardado transformaria "abrir uma vez dentro
 * do FISCALE" em "o Elo ficou sem menu para sempre nesta máquina", e o
 * jeito de sair disso não estaria em lugar nenhum da tela.
 *
 * Como o modo mora na URL, ele só sobrevive se cada navegação interna o
 * levar junto — dai `comModo()`. Quem navega no Elo usa `LinkModo` ou
 * `useCaminho()` (components/embutido.tsx), que chamam esta função. Fora do
 * modo embutido ela devolve o caminho intocado: nenhuma URL ganha o
 * parâmetro por engano.
 */

/** Nome e valor ficam aqui, e só aqui. */
const PARAMETRO = "embutido";
const VALOR = "1";
const MARCA = `${PARAMETRO}=${VALOR}`;

/**
 * O caminho pedido está em modo embutido?
 *
 * @param caminhoComBusca caminho + query, como o middleware o entrega.
 */
export function modoEmbutido(caminhoComBusca: string | null | undefined): boolean {
  if (!caminhoComBusca) return false;
  const busca = caminhoComBusca.split("?")[1];
  if (!busca) return false;
  // Comparação de par inteiro, não `includes`: "embutido=11" e
  // "naoembutido=1" não podem ligar o modo por parecerem com ele.
  return busca.split("&").some((par) => par === MARCA);
}

/**
 * Devolve o destino com o modo preservado. Fora do modo, devolve o destino
 * como veio.
 *
 * Aceita caminho interno (`/clientes`, `/atendimentos?abrir=…`). Destino
 * externo (`http…`, `//…`) sai intocado: o modo é do Elo e não viaja para
 * fora dele.
 */
export function comModo(destino: string, embutido: boolean): string {
  if (!embutido) return destino;
  if (/^[a-z]+:|^\/\//i.test(destino)) return destino;

  const [semAncora, ancora] = destino.split("#");
  const base = semAncora ?? "";
  const fim = ancora === undefined ? "" : `#${ancora}`;

  const [caminho, busca] = base.split("?");
  if (busca !== undefined && busca.split("&").some((par) => par === MARCA)) {
    return destino; // já está lá; acrescentar duas vezes seria mentira na URL
  }

  const separador = busca ? "&" : "?";
  return `${caminho}${busca ? `?${busca}` : ""}${separador}${MARCA}${fim}`;
}
