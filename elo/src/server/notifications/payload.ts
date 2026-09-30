/**
 * O texto do aviso — e o que ele NAO pode conter.
 *
 * ---------------------------------------------------------------------
 *  A notificacao aparece na tela BLOQUEADA
 * ---------------------------------------------------------------------
 *  No Windows ela cruza o canto da tela enquanto alguem passa pela mesa.
 *  No celular ela aparece com o aparelho trancado, no bolso de cima da
 *  mesa do almoco. O escritorio trata de assunto de terceiro: faturamento,
 *  DAS atrasado, folha. Nada disso pode sair por padrao.
 *
 *  Por isso o padrao e dizer QUEM falou, nunca O QUE falou:
 *
 *      ELO
 *      Empresa ABC entrou em contato.
 *
 *  Com a prévia LIGADA — escolha explicita de cada pessoa — entra um
 *  trecho curto, e mesmo assim midia vira descricao ("enviou uma foto"),
 *  nunca o arquivo nem o nome dele.
 *
 * ---------------------------------------------------------------------
 *  O payload e minimo por decisao, nao so por ser cifrado
 * ---------------------------------------------------------------------
 *  Ele viaja cifrado (RFC 8291), e ainda assim carrega o minimo: tipo do
 *  evento, id da conversa, titulo, corpo e hora. Nada de historico, nada
 *  de dado fiscal, nada de anexo. O `conversationId` que vai aqui NAO
 *  concede acesso: ao clicar, sessao, tenant, RBAC e RLS continuam
 *  obrigatorios — ver a rota /atendimentos.
 */
import type { MessageType } from "@/generated/prisma/enums";

/** O que aconteceu. E o mesmo vocabulario das preferencias. */
export type TipoAviso =
  | "message.new"
  | "conversation.new"
  | "conversation.assigned"
  | "conversation.transferred"
  // Atendimento parado. Vocabulario proprio para que a bandeja e um dia
  // a metrica consigam separar "chegou caso novo" de "caso nao andou".
  | "conversation.escalated"
  | "system";

export interface AvisoPush {
  /** Curto porque o payload cifrado tem teto e porque o aviso e um aviso. */
  t: TipoAviso;
  /** Destino do clique. Nao e credencial: ver o cabecalho. */
  c?: string;
  title: string;
  body: string;
  /** ISO. A tela usa para nao mostrar aviso de ontem como se fosse agora. */
  at: string;
  /**
   * Agrupamento no sistema operacional. Dois avisos com a mesma `tag` viram
   * UM: o segundo substitui o primeiro.
   */
  tag?: string;
}

/** Um trecho curto e uma linha. Notificacao nao e leitor de mensagem. */
export const LIMITE_PREVIA = 120;

export function truncar(texto: string, limite = LIMITE_PREVIA): string {
  const limpo = texto.replace(/\s+/g, " ").trim();
  if (limpo.length <= limite) return limpo;
  // O corte inclui as reticencias DENTRO do limite: um "limite" que o
  // resultado ultrapassa nao e limite.
  return `${limpo.slice(0, limite - 1)}…`;
}

/**
 * Midia vira descricao, nunca conteudo.
 *
 * O nome do arquivo TAMBEM fica de fora, inclusive com a prévia ligada:
 * "Balancete_MARIA_SILVA_2026.pdf" conta a mesma historia que o texto que
 * a prévia existe para esconder.
 */
export function descricaoDeMidia(tipo: MessageType): string | null {
  switch (tipo) {
    case "IMAGE":
      return "enviou uma foto";
    case "DOCUMENT":
      return "enviou um documento";
    case "AUDIO":
      return "enviou um áudio";
    case "VOICE":
      return "enviou uma mensagem de voz";
    case "TEXT":
    default:
      return null;
  }
}

export interface DadosMensagem {
  /** Nome do cliente, ou null quando o contato nao foi identificado. */
  cliente: string | null;
  tipo: MessageType;
  /** O texto cru da mensagem. So e usado se `preview` for true. */
  conteudo: string;
  /** Quantas mensagens este aviso representa. 1 = a primeira do lote. */
  agrupadas?: number;
}

const REMETENTE_DESCONHECIDO = "Um contato";

/**
 * O aviso de mensagem nova.
 *
 * Tres formas, e a escolha nao e estetica:
 *
 *   agrupado         "Empresa ABC — 5 novas mensagens"
 *   prévia desligada "Empresa ABC entrou em contato."
 *   prévia ligada    "Empresa ABC: bom dia, preciso da guia"
 */
export function avisoDeMensagem(
  conversationId: string,
  dados: DadosMensagem,
  preview: boolean,
  agora: Date,
): AvisoPush {
  const quem = dados.cliente ?? REMETENTE_DESCONHECIDO;
  const agrupadas = dados.agrupadas ?? 1;

  const body =
    agrupadas > 1
      ? `${quem} — ${agrupadas} novas mensagens`
      : preview
        ? corpoComPrevia(quem, dados)
        : `${quem} entrou em contato.`;

  return {
    t: "message.new",
    c: conversationId,
    title: "ELO",
    body,
    at: agora.toISOString(),
    // A tag e a CONVERSA: mensagens do mesmo atendimento se substituem, e
    // atendimentos diferentes continuam sendo avisos diferentes. Uma tag
    // global esconderia o segundo cliente atras do primeiro.
    tag: `c:${conversationId}`,
  };
}

function corpoComPrevia(quem: string, dados: DadosMensagem): string {
  const midia = descricaoDeMidia(dados.tipo);
  if (midia) return `${quem} ${midia}.`;

  const texto = truncar(dados.conteudo);
  // Mensagem sem texto e sem midia nao deveria existir; se existir, o
  // aviso volta a ser o seguro em vez de sair vazio.
  return texto ? `${quem}: ${texto}` : `${quem} entrou em contato.`;
}

/** Atendimento novo na fila. Nunca leva prévia: ninguem assumiu ainda. */
export function avisoDeAtendimentoNovo(
  conversationId: string,
  cliente: string | null,
  area: string | null,
  agora: Date,
): AvisoPush {
  const quem = cliente ?? REMETENTE_DESCONHECIDO;
  return {
    t: "conversation.new",
    c: conversationId,
    title: "ELO — novo atendimento",
    body: area ? `${quem} • ${area}` : `${quem} está aguardando atendimento.`,
    at: agora.toISOString(),
    tag: `c:${conversationId}`,
  };
}

/**
 * O escalonamento: um atendimento ficou sem resposta.
 *
 * Nunca leva previa nem nome de cliente no corpo alem do que o aviso de
 * fila ja levaria — e o que a coordenacao precisa saber e que existe caso
 * parado, nao o assunto dele.
 */
export function avisoDeEscalonamento(
  conversationId: string,
  cliente: string | null,
  area: string | null,
  minutos: number,
  agora: Date,
): AvisoPush {
  const quem = cliente ?? REMETENTE_DESCONHECIDO;
  return {
    t: "conversation.escalated",
    c: conversationId,
    title: "ELO — atendimento sem resposta",
    body: `${quem}${area ? ` • ${area}` : ""} está há ${minutos} min sem resposta.`,
    at: agora.toISOString(),
    // Tag propria: o escalonamento NAO pode substituir na bandeja o aviso
    // da conversa, nem ser substituido por ele.
    tag: `esc:${conversationId}`,
  };
}

export function avisoDeAtribuicao(
  conversationId: string,
  cliente: string | null,
  quemAtribuiu: string | null,
  transferencia: boolean,
  agora: Date,
): AvisoPush {
  const alvo = cliente ?? REMETENTE_DESCONHECIDO;
  const acao = transferencia ? "transferiu" : "atribuiu";
  return {
    t: transferencia ? "conversation.transferred" : "conversation.assigned",
    c: conversationId,
    title: transferencia ? "ELO — transferência" : "ELO — atendimento atribuído",
    body: quemAtribuiu
      ? `${quemAtribuiu} ${acao} o atendimento de ${alvo} para você.`
      : `O atendimento de ${alvo} é seu agora.`,
    at: agora.toISOString(),
    tag: `c:${conversationId}`,
  };
}

/**
 * Serializa para o transporte.
 *
 * Passa por aqui de proposito: e o unico lugar que transforma o aviso em
 * bytes, entao e onde se confere que nenhum campo a mais entrou no
 * caminho.
 */
export function serializar(aviso: AvisoPush): string {
  const { t, c, title, body, at, tag } = aviso;
  return JSON.stringify({ t, ...(c ? { c } : {}), title, body, at, ...(tag ? { tag } : {}) });
}
