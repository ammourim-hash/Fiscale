"use client";

/**
 * A ponte com o realtime, do lado do navegador.
 *
 * =====================================================================
 *  UMA conexão por aba — e agora isso é estrutura, não convenção
 * =====================================================================
 *  No MVP 1.5 havia um chamador só, e "uma conexão por aba" era uma
 *  consequência feliz. No 1.7 há dois: a tela de Atendimentos, que segue a
 *  conversa aberta, e a casca, que precisa do contador e do som em
 *  QUALQUER tela. Dois `EventSource` por aba dobrariam o custo no servidor
 *  para transportar exatamente os mesmos eventos — e dobrariam também a
 *  presença registrada, que é o que decide se o Web Push sai ou não.
 *
 *  Então o `EventSource` virou um SINGLETON do módulo. Os componentes
 *  assinam e desassinam; a conexão nasce com o primeiro e morre com o
 *  último. A API pública (`useRealtime`) não mudou.
 *
 * =====================================================================
 *  O que este arquivo NÃO faz: guardar estado
 * =====================================================================
 *  O evento é um aviso de que algo mudou; quem tem a verdade é a API. Se
 *  um evento se perder — e vai se perder, porque conexão cai —, a próxima
 *  leitura conserta. Por isso a reconexão dispara uma revalidação em vez
 *  de tentar recuperar o que passou: pedir de novo é barato e sempre certo.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

export type EventoRealtime =
  | {
      type: "message.created";
      conversationId: string;
      messageId: string;
      sequence: number;
      direction: "INBOUND" | "OUTBOUND" | "SYSTEM";
      preview: string;
      senderDisplayName: string | null;
      senderDepartmentName: string | null;
      createdAt: string;
    }
  | {
      type: "message.viewed";
      conversationId: string;
      messageId: string;
      membershipId: string;
      viewedAt: string;
    }
  | {
      type: "conversation.updated";
      conversationId: string;
      status: string;
      version: number;
      assignedMembershipId: string | null;
      lastActivityAt: string;
      reason: string;
      /** Só quando `reason` é "message". Ver o comentário em events.ts. */
      messageDirection?: "INBOUND" | "OUTBOUND" | "SYSTEM";
    };

export type EstadoConexao = "conectando" | "conectado" | "reconectando";

export interface Realtime {
  estado: EstadoConexao;
  /** Sobe a cada reconexão bem-sucedida. Quem depende de dados revalida. */
  geracao: number;
}

/* ─── a conexão única ────────────────────────────────────────────────── */

interface Assinante {
  /** A conversa que ESTE assinante quer acompanhar, ou null. */
  conversa: string | null;
  aoEvento: (e: EventoRealtime) => void;
  aoEstado: (estado: EstadoConexao, geracao: number) => void;
}

interface Gerente {
  fonte: EventSource | null;
  /** A conversa com que a conexão atual foi aberta. */
  conversaAtual: string | null;
  estado: EstadoConexao;
  geracao: number;
  jaConectou: boolean;
  assinantes: Set<Assinante>;
  /** Id da conexão no servidor, para avisar mudança de visibilidade. */
  conexao: string | null;
  ouvindoVisibilidade: boolean;
}

const gerente: Gerente = {
  fonte: null,
  conversaAtual: null,
  estado: "conectando",
  geracao: 0,
  jaConectou: false,
  assinantes: new Set(),
  conexao: null,
  ouvindoVisibilidade: false,
};

/**
 * Qual conversa a conexão deve seguir.
 *
 * Só uma conversa fica aberta por vez na interface, então "a última que
 * alguém pediu" é a resposta certa — e continua certa se um dia houver
 * duas telas, porque o pior caso é a segunda não receber os eventos de
 * mensagem e cair na revalidação por `conversation.updated`, que todo
 * mundo recebe.
 */
function conversaDesejada(): string | null {
  let alvo: string | null = null;
  for (const a of gerente.assinantes) if (a.conversa) alvo = a.conversa;
  return alvo;
}

function anunciarEstado(): void {
  for (const a of gerente.assinantes) a.aoEstado(gerente.estado, gerente.geracao);
}

function abrir(): void {
  const conversa = conversaDesejada();
  const url = conversa
    ? `/api/realtime?conversation=${encodeURIComponent(conversa)}`
    : "/api/realtime";

  const fonte = new EventSource(url);
  gerente.fonte = fonte;
  gerente.conversaAtual = conversa;

  const receber = (ev: MessageEvent<string>): void => {
    if (gerente.fonte !== fonte) return;
    try {
      const evento = JSON.parse(ev.data) as EventoRealtime;
      for (const a of gerente.assinantes) a.aoEvento(evento);
    } catch {
      /* evento malformado não pode derrubar a tela */
    }
  };

  fonte.addEventListener("ready", (ev) => {
    if (gerente.fonte !== fonte) return;
    try {
      const d = JSON.parse((ev as MessageEvent<string>).data) as { connection?: string };
      gerente.conexao = d.connection ?? null;
    } catch {
      gerente.conexao = null;
    }
    gerente.estado = "conectado";
    if (gerente.jaConectou) gerente.geracao += 1;
    gerente.jaConectou = true;
    anunciarEstado();
    // Reconectou com a aba escondida? O servidor assume "visível" ao
    // abrir; corrigir aqui evita suprimir push de quem não está olhando.
    enviarVisibilidade();
  });

  fonte.addEventListener("message.created", receber as EventListener);
  fonte.addEventListener("message.viewed", receber as EventListener);
  fonte.addEventListener("conversation.updated", receber as EventListener);

  // O EventSource reconecta sozinho; aqui só se conta ao usuário o que
  // está acontecendo. Sumir com o aviso sem ter voltado seria mentira.
  fonte.onerror = () => {
    if (gerente.fonte !== fonte) return;
    gerente.estado = "reconectando";
    anunciarEstado();
  };
}

function fechar(): void {
  gerente.fonte?.close();
  gerente.fonte = null;
  gerente.conexao = null;
}

function reconciliar(): void {
  if (gerente.assinantes.size === 0) {
    fechar();
    return;
  }
  const desejada = conversaDesejada();
  if (!gerente.fonte) {
    abrir();
    return;
  }
  // Trocar a conversa acompanhada exige uma conexão nova: o parâmetro
  // viaja na URL do `EventSource`, e ele não muda de URL em voo.
  if (desejada !== gerente.conversaAtual) {
    fechar();
    abrir();
  }
}

/* ─── visibilidade ───────────────────────────────────────────────────── */

/**
 * Avisa o servidor quando a aba vai para o fundo, e quando volta.
 *
 * É isto que separa "está lendo a conversa" de "deixou o Elo aberto numa
 * aba atrás do Excel". Sem o aviso, quem minimiza o navegador continuaria
 * contando como presente e deixaria de receber a notificação do sistema —
 * que é o único caso que esta fase inteira existe para resolver.
 */
function enviarVisibilidade(): void {
  if (!gerente.conexao) return;
  const visivel = document.visibilityState === "visible";
  void fetch("/api/presence", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ connection: gerente.conexao, visivel }),
    // `keepalive` para o aviso de "escondi a aba" sobreviver ao momento em
    // que o navegador congela a página.
    keepalive: true,
  }).catch(() => {
    /* presença é otimização: falhar aqui só faz chegar uma notificação
       a mais, nunca uma a menos */
  });
}

function ligarVisibilidade(): void {
  if (gerente.ouvindoVisibilidade) return;
  if (typeof document === "undefined") return;
  gerente.ouvindoVisibilidade = true;
  document.addEventListener("visibilitychange", enviarVisibilidade);
}

/* ─── o hook ─────────────────────────────────────────────────────────── */

/**
 * @param conversationId conversa a acompanhar, ou `null` para só a lista.
 * @param aoEvento chamado para cada evento. Mantido em ref para não
 *   reabrir a conexão a cada render do componente que o passa.
 */
export function useRealtime(
  conversationId: string | null,
  aoEvento: (e: EventoRealtime) => void,
): Realtime {
  const [estado, setEstado] = useState<EstadoConexao>("conectando");
  const [geracao, setGeracao] = useState(0);

  // A função vem em ref, e a ref é atualizada num efeito — nunca durante
  // o render. Assim o componente pode passar uma função nova a cada
  // render sem que a conexão seja derrubada e reaberta junto.
  const callback = useRef(aoEvento);
  useEffect(() => {
    callback.current = aoEvento;
  }, [aoEvento]);

  useEffect(() => {
    if (typeof EventSource === "undefined") return;

    const assinante: Assinante = {
      conversa: conversationId,
      aoEvento: (e) => callback.current(e),
      aoEstado: (novoEstado, novaGeracao) => {
        setEstado(novoEstado);
        setGeracao(novaGeracao);
      },
    };

    gerente.assinantes.add(assinante);
    ligarVisibilidade();
    reconciliar();
    // Quem entra depois da conexão já aberta precisa saber o estado atual.
    assinante.aoEstado(gerente.estado, gerente.geracao);

    return () => {
      gerente.assinantes.delete(assinante);
      reconciliar();
    };
  }, [conversationId]);

  return { estado, geracao };
}

/* ─── qual conversa está aberta na frente ────────────────────────────── */

/**
 * O id da conversa aberta, para quem precisa saber sem ser quem a abriu.
 *
 * Existe por um caso só: o som. Quem toca é a casca; quem sabe qual
 * conversa está na tela é a lista de atendimentos. Passar isso por
 * propriedade exigiria levantar o estado até o layout do servidor, que não
 * pode ser cliente. Um valor de módulo, com assinantes, custa vinte linhas
 * e não inverte a árvore.
 */
let conversaAberta: string | null = null;
const ouvintesDeAbertura = new Set<() => void>();

export function definirConversaAberta(id: string | null): void {
  if (conversaAberta === id) return;
  conversaAberta = id;
  for (const o of ouvintesDeAbertura) o();
}

export function conversaAbertaAgora(): string | null {
  return conversaAberta;
}

/**
 * `useSyncExternalStore`, e não `useState` + efeito.
 *
 * É exatamente o caso para que ele existe: um valor que vive fora do
 * React. Com `useState`, o primeiro render mostraria `null` e um efeito
 * corrigiria depois — um render a mais e, no meio dele, uma janela em que
 * o som tocaria para a conversa que já está aberta.
 */
export function useConversaAberta(): string | null {
  return useSyncExternalStore(
    (aoMudar) => {
      ouvintesDeAbertura.add(aoMudar);
      return () => ouvintesDeAbertura.delete(aoMudar);
    },
    () => conversaAberta,
    // No servidor não há conversa aberta, e fingir que há causaria erro de
    // hidratação.
    () => null,
  );
}
