/**
 * Quem esta com o Elo aberto, e olhando o quê.
 *
 * ---------------------------------------------------------------------
 *  Para que isto existe: nao avisar duas vezes a mesma coisa
 * ---------------------------------------------------------------------
 *  Sem presenca, a mensagem que chega enquanto a Aline esta LENDO aquela
 *  conversa produziria tres coisas ao mesmo tempo: o balao aparecendo pelo
 *  SSE, um som, e uma notificacao do Windows sobre algo que ela esta
 *  olhando. A terceira e ruido, e ruido treina a pessoa a desligar tudo.
 *
 *  A regra, escrita:
 *
 *    conversa aberta + janela visivel  → so o SSE. Nem push, nem som.
 *    Elo aberto, outra conversa        → som (se ligado) e contador. Sem push.
 *    Elo aberto, janela escondida      → push.
 *    Elo fechado                       → push.
 *
 * ---------------------------------------------------------------------
 *  O limite honesto disto
 * ---------------------------------------------------------------------
 *  Presenca em processo sabe o que a aba CONTOU. Uma aba que morre sem
 *  avisar (queda de energia, aba morta pelo sistema) fica registrada ate a
 *  conexao SSE cair, o que leva alguns segundos. Nesse intervalo o push
 *  pode ser suprimido para alguem que ja nao esta la.
 *
 *  A escolha e deliberada: errar para o lado de UM aviso a menos numa
 *  janela de segundos, em vez de errar para o lado do aviso duplicado o
 *  tempo todo. O atendimento continua na lista, continua nao lido, e
 *  continua contando no crachá — nada se perde, so o alerta imediato.
 *
 *  Como o barramento (`bus.ts`), isto vive em UM processo. Com duas
 *  instancias, a presenca precisa sair para um lugar compartilhado; e o
 *  mesmo debito, no mesmo lugar.
 */

export interface Sessao {
  membershipId: string;
  /** A conversa que esta aba acompanha, ou null para so a lista. */
  conversationId: string | null;
  /** A aba esta em primeiro plano? Vem de `document.visibilityState`. */
  visivel: boolean;
}

interface Registro extends Sessao {
  tenantId: string;
}

/**
 * Identificador da CONEXAO, nao da pessoa.
 *
 * E um id aleatorio que a rota SSE entrega a aba no evento `ready`, e que
 * a aba devolve para dizer "fui para o fundo" / "voltei". Aleatorio, e nao
 * sequencial, para que uma aba nao consiga adivinhar o id de outra — e
 * mesmo assim a rota que o recebe confere o membership da sessao antes de
 * aceitar, porque adivinhar id nao pode ser suficiente.
 */
export type ChavePresenca = string;

interface Estado {
  porChave: Map<ChavePresenca, Registro>;
}

const global = globalThis as unknown as { __eloPresenca?: Estado };
const estado: Estado = (global.__eloPresenca ??= { porChave: new Map() });

/** A rota SSE registra ao abrir e devolve a chave para atualizar e sair. */
export function entrar(tenantId: string, sessao: Sessao): ChavePresenca {
  const chave = crypto.randomUUID();
  estado.porChave.set(chave, { tenantId, ...sessao });
  return chave;
}

/**
 * Mudou de conversa ou a aba foi para o fundo.
 *
 * Exige tenant E membership: o id da conexao sozinho nao autoriza nada.
 * Devolve `false` quando a conexao nao existe ou nao e de quem pediu — e
 * quem chama trata isso como "a conexao morreu", nao como erro.
 */
export function atualizar(
  chave: ChavePresenca,
  dono: { tenantId: string; membershipId: string },
  mudanca: Partial<Pick<Sessao, "conversationId" | "visivel">>,
): boolean {
  const atual = estado.porChave.get(chave);
  if (!atual) return false;
  if (atual.tenantId !== dono.tenantId || atual.membershipId !== dono.membershipId) {
    return false;
  }
  estado.porChave.set(chave, { ...atual, ...mudanca });
  return true;
}

export function sair(chave: ChavePresenca): void {
  estado.porChave.delete(chave);
}

/**
 * Como esta pessoa esta, agora, em relacao a ESTA conversa.
 *
 * Uma pessoa pode ter varias abas. A resposta e a MAIS presente delas: se
 * qualquer aba estiver visivel e naquela conversa, ela esta acompanhando.
 */
export type Atencao =
  /** Esta olhando exatamente esta conversa. */
  | "NA_CONVERSA"
  /** Esta com o Elo aberto e visivel, em outro lugar. */
  | "NO_ELO"
  /** Nao ha aba visivel: fechada, minimizada ou em outra aba do navegador. */
  | "AUSENTE";

export function atencaoDe(
  tenantId: string,
  membershipId: string,
  conversationId: string | null,
): Atencao {
  let melhor: Atencao = "AUSENTE";

  for (const r of estado.porChave.values()) {
    if (r.tenantId !== tenantId || r.membershipId !== membershipId) continue;
    if (!r.visivel) continue;
    if (conversationId && r.conversationId === conversationId) return "NA_CONVERSA";
    melhor = "NO_ELO";
  }

  return melhor;
}

/** Quantas conexoes este tenant tem registradas. Teste e diagnostico. */
export function contarPresencas(tenantId: string): number {
  let n = 0;
  for (const r of estado.porChave.values()) if (r.tenantId === tenantId) n += 1;
  return n;
}

/** Apenas para teste: zera o estado entre casos. */
export function limparPresenca(): void {
  estado.porChave.clear();
}
