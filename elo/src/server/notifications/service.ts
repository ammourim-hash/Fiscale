/**
 * O servico de decisao de notificacoes.
 *
 * ---------------------------------------------------------------------
 *  A MENSAGEM VEM PRIMEIRO. Sempre.
 * ---------------------------------------------------------------------
 *  Nada aqui pode impedir uma mensagem de ser criada. O provedor de push
 *  pode estar fora, as chaves VAPID podem faltar, a rede pode cair: a
 *  mensagem ja esta gravada, o SSE ja avisou quem esta com a tela aberta, e
 *  a notificacao e um EFEITO SECUNDARIO.
 *
 *  Por isso o ponto de entrada e `avisarSemBloquear()`: ele nao devolve
 *  promessa para o chamador esperar, e nenhuma excecao daqui atravessa
 *  para o POST da mensagem. Falha de aviso vira log — nunca 500 numa
 *  requisicao que ja fez o que precisava fazer.
 *
 * ---------------------------------------------------------------------
 *  SSE e Push sao complementares, e a fronteira e a PRESENCA
 * ---------------------------------------------------------------------
 *      conversa aberta + janela visivel  → so o SSE
 *      Elo aberto, outra conversa        → SSE (som e crachá na tela)
 *      Elo escondido ou fechado          → Web Push
 *
 *  A presenca em processo nao sabe tudo (ver realtime/presence.ts). O erro
 *  aceito e para o lado de UM aviso a menos, nunca do aviso duplicado.
 *
 * ---------------------------------------------------------------------
 *  O que este arquivo NAO faz
 * ---------------------------------------------------------------------
 *  Nao decide quem pode LER a conversa (isso e RBAC, em recipients.ts),
 *  nao monta texto (payload.ts), nao cifra (push/webpush.ts) e nao guarda
 *  estado de leitura. Ele orquestra, e a orquestracao cabe numa tela.
 */
import { logger } from "@/server/logging/logger";
import { chavesVapid, estadoDoPush } from "@/server/push/config";
import { enviarPush, type ResultadoEnvio } from "@/server/push/webpush";
import { atencaoDe } from "@/server/realtime/presence";
import { withTenant } from "@/server/tenancy";
import type { MessageType } from "@/generated/prisma/enums";

import { registrar } from "./coalescing";
import {
  avisoDeAtendimentoNovo,
  avisoDeAtribuicao,
  avisoDeEscalonamento,
  avisoDeMensagem,
  serializar,
  type AvisoPush,
} from "./payload";
import { PADRAO, preferenciasDeVarios, type Preferencias } from "./preferences";
import {
  filaElegivel,
  gestoresDoDepartamento,
  paraMensagem,
  podeReceberAviso,
  type Candidato,
} from "./recipients";

/* ─── os eventos que o resto do sistema anuncia ──────────────────────── */

export type EventoNotificavel =
  | {
      tipo: "MENSAGEM_RECEBIDA";
      tenantId: string;
      conversationId: string;
      /** Quem escreveu, se foi alguem do escritorio. Nunca recebe aviso. */
      autorMembershipId: string | null;
      messageType: MessageType;
      conteudo: string;
    }
  | {
      tipo: "ATENDIMENTO_ATRIBUIDO";
      tenantId: string;
      conversationId: string;
      /** Quem passou a ser responsavel. */
      destinoMembershipId: string;
      /** Quem fez a atribuicao. Se for o proprio destino, ninguem e avisado. */
      autorMembershipId: string | null;
      transferencia: boolean;
    }
  | {
      tipo: "ATENDIMENTO_NOVO";
      tenantId: string;
      conversationId: string;
      autorMembershipId: string | null;
    }
  | {
      /**
       * Atendimento parado. Vai para a COORDENACAO da area, e so ela.
       *
       * E o contrapeso de `ATENDIMENTO_NOVO` nao avisar mais gestor: a
       * coordenacao deixa de ser acordada por todo caso novo e passa a
       * ser avisada do que ficou sem resposta — que e onde ela pode
       * fazer alguma coisa a respeito.
       */
      tipo: "ATENDIMENTO_ESCALADO";
      tenantId: string;
      conversationId: string;
      /** Minutos de HORARIO COMERCIAL sem resposta. */
      minutos: number;
    };

/**
 * O ponto de entrada de todo o resto do sistema.
 *
 * Dispara e devolve na hora. O `void` na frente da promessa e literal: se
 * o aviso demorar dez segundos, a resposta da mensagem nao espera.
 */
const emVoo = new Set<Promise<unknown>>();

export function avisarSemBloquear(evento: EventoNotificavel): void {
  const promessa = avisar(evento)
    .catch((erro: unknown) => {
      logger.warn("notifications.failed", { tipo: evento.tipo, erro: String(erro) });
    })
    .finally(() => emVoo.delete(promessa));

  emVoo.add(promessa);
}

/**
 * Espera os avisos em voo terminarem.
 *
 * O servidor web NUNCA chama isto: lá o processo continua vivo e o aviso
 * termina sozinho — é essa a razão de o envio não bloquear a mensagem.
 *
 * Quem precisa são os processos CURTOS. Um script de seed que fecha a
 * conexão logo depois de criar um atendimento derrubava o aviso no meio,
 * com um "transação não encontrada" no log. Errado não era o aviso: era
 * desligar a luz antes de todo mundo sair da sala.
 *
 * O laço existe porque um aviso pode nascer de outro; sair no primeiro
 * `allSettled` deixaria justamente o último de fora.
 */
export async function aguardarAvisos(): Promise<void> {
  while (emVoo.size > 0) await Promise.allSettled([...emVoo]);
}

export interface ResumoAviso {
  /** Quem entrou na conta de destinatarios. */
  candidatos: number;
  /** Quem passou por preferencia, presenca e agrupamento. */
  notificados: number;
  /** Aparelhos que receberam de fato. */
  enviados: number;
  /** Inscricoes revogadas porque o provedor disse que morreram. */
  revogadas: number;
  /** Falhas temporarias — o provedor pode ter estado fora do ar. */
  falhas: number;
}

/** A versao esperavel, para teste e para quem quiser o resultado. */
export async function avisar(
  evento: EventoNotificavel,
  agora: Date = new Date(),
): Promise<ResumoAviso> {
  const resumo: ResumoAviso = {
    candidatos: 0,
    notificados: 0,
    enviados: 0,
    revogadas: 0,
    falhas: 0,
  };

  const decisao = await decidir(evento, agora);
  if (decisao.length === 0) return resumo;

  resumo.candidatos = decisao.length;

  for (const d of decisao) {
    if (!d.notificar) continue;

    // O AGRUPAMENTO ACONTECE AQUI, e não em `decidir` — ver o comentário
    // do tipo `Alvo`.
    let aviso = d.aviso;
    if (d.agrupavel) {
      const { enviar, agrupadas } = registrar(
        d.membershipId,
        evento.conversationId,
        agora.getTime(),
      );
      if (!enviar) continue;
      if (agrupadas > 1) aviso = d.comAgrupamento(agrupadas);
    }

    resumo.notificados += 1;

    const r = await entregar(evento.tenantId, d.membershipId, aviso);
    resumo.enviados += r.enviados;
    resumo.revogadas += r.revogadas;
    resumo.falhas += r.falhas;
  }

  return resumo;
}

/* ─── decisao ────────────────────────────────────────────────────────── */

interface Alvo {
  membershipId: string;
  aviso: AvisoPush;
  /** `false` = candidato descartado por preferência ou por presença. */
  notificar: boolean;
  /**
   * Este aviso passa pela janela de agrupamento?
   *
   * Só mensagem. Atribuição e transferência não: "este caso agora é seu"
   * acontece uma vez e não pode ser engolido por uma rajada de mensagens
   * da mesma conversa.
   */
  agrupavel: boolean;
  /** O mesmo aviso, redigido como "Empresa ABC — 5 novas mensagens". */
  comAgrupamento: (agrupadas: number) => AvisoPush;
}

/**
 * De candidatos a alvos.
 *
 * Separado do envio de proposito: e a parte que precisa ser lida por
 * inteiro para conferir a regra, e a parte que o teste exercita sem
 * precisar de rede nem de chave VAPID.
 */
export async function decidir(
  evento: EventoNotificavel,
  agora: Date = new Date(),
): Promise<Alvo[]> {
  return withTenant(evento.tenantId, async (tx) => {
    const conversa = await tx.conversation.findUnique({
      where: { id: evento.conversationId },
      select: {
        id: true,
        assignedMembershipId: true,
        assignedDepartmentId: true,
        customer: { select: { displayName: true } },
        department: { select: { name: true } },
      },
    });
    if (!conversa) return [];

    const cliente = conversa.customer?.displayName ?? null;

    /* quem sao os candidatos */
    let candidatos: Candidato[];
    if (evento.tipo === "MENSAGEM_RECEBIDA") {
      candidatos = await paraMensagem(tx, conversa, evento.autorMembershipId);
    } else if (evento.tipo === "ATENDIMENTO_NOVO") {
      // `semGestao`: a fila e de quem atende. Estar vinculado a area
      // para acessar e gerir nao pode significar receber o primeiro
      // toque de todo atendimento novo.
      candidatos = await filaElegivel(
        tx,
        conversa.assignedDepartmentId,
        evento.autorMembershipId,
        { semGestao: true },
      );
    } else if (evento.tipo === "ATENDIMENTO_ESCALADO") {
      candidatos = await gestoresDoDepartamento(tx, conversa.assignedDepartmentId);
    } else {
      // Atribuir a si mesmo nao gera aviso: a pessoa acabou de clicar.
      if (evento.destinoMembershipId === evento.autorMembershipId) return [];
      const destino = await podeReceberAviso(tx, evento.destinoMembershipId);
      candidatos = destino ? [destino] : [];
    }

    if (candidatos.length === 0) return [];

    const prefs = await preferenciasDeVarios(
      tx,
      candidatos.map((c) => c.membershipId),
    );

    // O nome de quem originou, para "Carlos transferiu … para voce".
    const autor =
      evento.tipo === "ATENDIMENTO_ATRIBUIDO" && evento.autorMembershipId
        ? ((
            await tx.membership.findUnique({
              where: { id: evento.autorMembershipId },
              select: { identity: { select: { name: true } } },
            })
          )?.identity.name ?? null)
        : null;

    return candidatos.map((c) =>
      montarAlvo(evento, c, prefs.get(c.membershipId) ?? PADRAO, {
        cliente,
        area: conversa.department?.name ?? null,
        autor,
        agora,
      }),
    );
  });
}

/**
 * De um candidato a um alvo — SEM EFEITO COLATERAL.
 *
 * `decidir` (e portanto esta funcao) e uma pergunta: "esta pessoa deve ser
 * avisada, e com qual texto?". Perguntar duas vezes precisa dar a mesma
 * resposta.
 *
 * A primeira versao consumia a janela de agrupamento aqui dentro, e o
 * defeito apareceu num teste que chamava `decidir` e depois `avisar`: a
 * segunda chamada dizia "nao notificar" porque a PRIMEIRA ja tinha gasto a
 * janela. Numa tela de diagnostico, isso teria virado "o sistema diz que
 * vai avisar e nao avisa" — sem nada no log.
 *
 * O agrupamento, que e um efeito, mora em `avisar`, na hora de entregar.
 */
function montarAlvo(
  evento: EventoNotificavel,
  candidato: Candidato,
  pref: Preferencias,
  ctx: { cliente: string | null; area: string | null; autor: string | null; agora: Date },
): Alvo {
  const alvo = (aviso: AvisoPush, notificar: boolean, agrupavel = false): Alvo => ({
    membershipId: candidato.membershipId,
    aviso,
    notificar,
    agrupavel,
    comAgrupamento: (agrupadas: number) =>
      evento.tipo === "MENSAGEM_RECEBIDA"
        ? avisoDeMensagem(
            evento.conversationId,
            {
              cliente: ctx.cliente,
              tipo: evento.messageType,
              conteudo: evento.conteudo,
              agrupadas,
            },
            pref.showPreview,
            ctx.agora,
          )
        : aviso,
  });

  if (evento.tipo === "ATENDIMENTO_ATRIBUIDO") {
    const aviso = avisoDeAtribuicao(
      evento.conversationId,
      ctx.cliente,
      ctx.autor ? primeiroNome(ctx.autor) : null,
      evento.transferencia,
      ctx.agora,
    );
    const querido = evento.transferencia ? pref.transferredToMe : pref.assignedToMe;
    // Atribuicao e transferencia NAO olham presenca: mesmo com a conversa
    // aberta na tela, "este caso agora e seu" e um fato novo que a lista
    // sozinha nao conta. E nao agrupam, para nao serem engolidas por uma
    // rajada de mensagens da mesma conversa.
    return alvo(aviso, querido);
  }

  if (evento.tipo === "ATENDIMENTO_ESCALADO") {
    const aviso = avisoDeEscalonamento(
      evento.conversationId,
      ctx.cliente,
      ctx.area,
      evento.minutos,
      ctx.agora,
    );
    // `systemNotices`: o escalonamento e aviso DO SISTEMA sobre o estado
    // da fila, nao mensagem de cliente. A preferencia ja existia na tela
    // e nao tinha quem a consumisse — passa a ter.
    if (!pref.systemNotices) return alvo(aviso, false);
    // NAO olha presenca, de proposito. "Esta parado ha 30 minutos" e um
    // fato novo mesmo para quem esta com a conversa aberta na tela — e e
    // justamente quem esta olhando sem responder que precisa ver isto.
    return alvo(aviso, true);
  }


  if (evento.tipo === "ATENDIMENTO_NOVO") {
    const aviso = avisoDeAtendimentoNovo(
      evento.conversationId,
      ctx.cliente,
      ctx.area,
      ctx.agora,
    );
    if (!pref.newConversations) return alvo(aviso, false);
    // Quem esta com o Elo aberto e visivel ve a fila subir sozinha.
    const atencao = atencaoDe(evento.tenantId, candidato.membershipId, evento.conversationId);
    return alvo(aviso, atencao === "AUSENTE");
  }

  /* MENSAGEM_RECEBIDA */
  const atencao = atencaoDe(
    evento.tenantId,
    candidato.membershipId,
    evento.conversationId,
  );

  // Esta LENDO esta conversa: o balao aparece pelo SSE e isso basta.
  // Esta no Elo, em outra conversa: a tela mostra crachá e toca o som, e
  // uma notificacao do sistema por cima seria o alerta em dobro.
  const semPush = atencao !== "AUSENTE";

  const aviso = avisoDeMensagem(
    evento.conversationId,
    { cliente: ctx.cliente, tipo: evento.messageType, conteudo: evento.conteudo },
    pref.showPreview,
    ctx.agora,
  );

  return alvo(aviso, pref.newMessages && !semPush, true);
}

function primeiroNome(nome: string): string {
  return nome.trim().split(/\s+/)[0] ?? nome;
}

/* ─── entrega ────────────────────────────────────────────────────────── */

interface ResultadoEntrega {
  enviados: number;
  revogadas: number;
  falhas: number;
}

/**
 * Manda para todos os aparelhos ativos daquela pessoa.
 *
 * Um funcionario tem notebook do escritorio, computador de casa e celular.
 * O aviso vai para os tres: adivinhar em qual ele esta seria adivinhar.
 */
async function entregar(
  tenantId: string,
  membershipId: string,
  aviso: AvisoPush,
): Promise<ResultadoEntrega> {
  const resultado: ResultadoEntrega = { enviados: 0, revogadas: 0, falhas: 0 };

  const chaves = chavesVapid();
  if (!chaves) {
    // Sem chaves nao ha push, e isso NAO e erro: a tela de configuracoes
    // mostra o motivo, e a mensagem ja foi entregue por quem devia.
    logger.debug("push.indisponivel", { motivo: estadoDoPush() });
    return resultado;
  }

  const inscricoes = await withTenant(tenantId, async (tx) =>
    tx.pushSubscription.findMany({
      where: { membershipId, revokedAt: null },
      select: { id: true, endpoint: true, p256dh: true, auth: true },
    }),
  );
  if (inscricoes.length === 0) return resultado;

  const corpo = serializar(aviso);

  for (const i of inscricoes) {
    const r = await enviarPush(
      { endpoint: i.endpoint, p256dh: i.p256dh, auth: i.auth },
      corpo,
      chaves,
      { topic: topicoDe(aviso) },
    );
    await anotarResultado(tenantId, i.id, r, resultado);
  }

  return resultado;
}

/**
 * `Topic` do protocolo: no maximo 32 caracteres de base64url.
 *
 * A `tag` interna e `c:<uuid>` (38 caracteres, com hifens que o cabecalho
 * nao aceita). Um hash curto do id resolve os dois problemas de uma vez, e
 * mantem a propriedade que importa: mesma conversa, mesmo topico.
 */
function topicoDe(aviso: AvisoPush): string | null {
  if (!aviso.c) return null;
  let h = 0x811c9dc5;
  for (let i = 0; i < aviso.c.length; i += 1) {
    h ^= aviso.c.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `elo${h.toString(36)}`;
}

/**
 * O que fazer com a resposta do provedor.
 *
 * EXPIRADA e o caso que importa: 404 e 410 significam que aquela inscricao
 * morreu — o navegador foi desinstalado, o perfil apagado, a permissao
 * revogada no sistema. Insistir nela e gastar requisicao para sempre. A
 * linha e revogada, e nao apagada: saber que o aparelho existiu e foi
 * desligado tem valor.
 */
async function anotarResultado(
  tenantId: string,
  subscriptionId: string,
  r: ResultadoEnvio,
  acumulado: ResultadoEntrega,
): Promise<void> {
  if (r.tipo === "ENTREGUE") {
    acumulado.enviados += 1;
    await withTenant(tenantId, async (tx) => {
      await tx.pushSubscription.updateMany({
        where: { id: subscriptionId },
        data: { lastUsedAt: new Date(), failureCount: 0 },
      });
    });
    return;
  }

  if (r.tipo === "EXPIRADA") {
    acumulado.revogadas += 1;
    await withTenant(tenantId, async (tx) => {
      await tx.pushSubscription.updateMany({
        where: { id: subscriptionId, revokedAt: null },
        data: { revokedAt: new Date(), revokedReason: "EXPIRED" },
      });
    });
    logger.info("push.subscription.expirada", { tenantId, status: r.status });
    return;
  }

  // TEMPORARIA, RECUSADA, SEM_RESPOSTA: conta a falha. Depois de um teto,
  // a inscricao e desligada — um endpoint que o provedor aceita e nunca
  // entrega e indistinguivel, daqui, de um endpoint morto.
  acumulado.falhas += 1;
  await withTenant(tenantId, async (tx) => {
    const linha = await tx.pushSubscription.update({
      where: { id: subscriptionId },
      data: { failureCount: { increment: 1 } },
      select: { failureCount: true },
    });
    if (linha.failureCount >= TETO_DE_FALHAS) {
      await tx.pushSubscription.updateMany({
        where: { id: subscriptionId, revokedAt: null },
        data: { revokedAt: new Date(), revokedReason: "EXPIRED" },
      });
    }
  });

  logger.warn("push.envio.falhou", {
    tenantId,
    tipo: r.tipo,
    ...(r.tipo === "SEM_RESPOSTA" ? { erro: r.erro } : { status: r.status }),
  });
}

/** Dez falhas seguidas. Generoso o bastante para atravessar uma queda do
 *  provedor, curto o bastante para nao insistir num aparelho morto. */
export const TETO_DE_FALHAS = 10;
