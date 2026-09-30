/**
 * Agrupamento: vinte mensagens seguidas nao viram vinte notificacoes.
 *
 * ---------------------------------------------------------------------
 *  Duas camadas, e as duas sao necessarias
 * ---------------------------------------------------------------------
 *  1. `tag` / `Topic` — o sistema operacional SUBSTITUI o aviso anterior
 *     do mesmo atendimento em vez de empilhar. Resolve a bagunca visual, e
 *     e o que o Windows e o Android fazem melhor.
 *
 *  2. A JANELA daqui — o servidor deixa de ENVIAR dentro de um intervalo
 *     curto. Resolve o resto: vibracao, som do sistema e bateria, que a
 *     substituicao nao resolve, porque cada aviso substituido ainda
 *     chegou, ainda vibrou e ainda acordou a tela.
 *
 * ---------------------------------------------------------------------
 *  A escolha: sem temporizador
 * ---------------------------------------------------------------------
 *  A versao com `setTimeout` mandaria um aviso final com o total exato ao
 *  fim da rajada. Ela tambem colocaria um temporizador vivo dentro de um
 *  processo web que pode ser reciclado a qualquer momento — e um aviso que
 *  depende de o processo continuar de pe e um aviso que as vezes nao sai,
 *  sem que ninguem descubra por quê.
 *
 *  Entao: o PRIMEIRO da rajada sai na hora, os seguintes ficam contados, e
 *  o proximo que sair depois da janela leva o total acumulado
 *  ("Empresa ABC — 5 novas mensagens").
 *
 *  O que isso custa, escrito para nao virar surpresa: se a rajada PARAR
 *  dentro da janela, as ultimas mensagens nao geram um segundo aviso do
 *  sistema. A pessoa ja foi avisada pelo primeiro; a lista, o crachá e o
 *  contador de nao lidas continuam certos, porque nenhum deles depende
 *  disto. O que se perde e uma segunda batida na tela, e nao a informacao.
 */

/** Trinta segundos. Rajada de cliente digitando cabe aqui; conversa de
 *  verdade, com resposta no meio, nao. */
export const JANELA_MS = 30_000;

interface Rajada {
  ultimoEnvioMs: number;
  /** Quantas mensagens chegaram DEPOIS do ultimo aviso enviado. */
  pendentes: number;
}

interface Estado {
  porChave: Map<string, Rajada>;
}

const global = globalThis as unknown as { __eloCoalescing?: Estado };
const estado: Estado = (global.__eloCoalescing ??= { porChave: new Map() });

/** A chave e a PESSOA mais a CONVERSA: dois clientes falando ao mesmo
 *  tempo sao dois avisos, e agrupa-los esconderia o segundo. */
function chaveDe(membershipId: string, conversationId: string): string {
  return `${membershipId}:${conversationId}`;
}

export interface Decisao {
  enviar: boolean;
  /** Quantas mensagens este aviso representa. 1 = a primeira da rajada. */
  agrupadas: number;
}

/**
 * @param agoraMs injetado para o teste nao depender de relogio de parede.
 */
export function registrar(
  membershipId: string,
  conversationId: string,
  agoraMs: number = Date.now(),
): Decisao {
  const chave = chaveDe(membershipId, conversationId);
  const atual = estado.porChave.get(chave);

  if (!atual || agoraMs - atual.ultimoEnvioMs >= JANELA_MS) {
    // Fora da janela: envia, levando junto o que ficou acumulado.
    const agrupadas = (atual?.pendentes ?? 0) + 1;
    estado.porChave.set(chave, { ultimoEnvioMs: agoraMs, pendentes: 0 });
    return { enviar: true, agrupadas };
  }

  // Dentro da janela: conta e cala.
  estado.porChave.set(chave, { ...atual, pendentes: atual.pendentes + 1 });
  return { enviar: false, agrupadas: atual.pendentes + 1 };
}

/**
 * A pessoa leu a conversa: a rajada acabou.
 *
 * Sem isto, ela leria tudo, o cliente mandaria uma mensagem nova dez
 * segundos depois, e o aviso diria "6 novas mensagens" — cinco das quais
 * ela ja tinha lido. Contador que mente e pior que contador nenhum.
 */
export function esquecer(membershipId: string, conversationId: string): void {
  estado.porChave.delete(chaveDe(membershipId, conversationId));
}

/** Apenas para teste. */
export function limparCoalescing(): void {
  estado.porChave.clear();
}
