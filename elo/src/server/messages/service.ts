/**
 * O motor de mensagens.
 *
 * ---------------------------------------------------------------------
 *  Três coisas que este arquivo existe para acertar
 * ---------------------------------------------------------------------
 *
 *  1. ORDEM. `sequence` vem de um `UPDATE conversations SET message_seq =
 *     message_seq + 1 ... RETURNING`. O próprio UPDATE trava a linha, então
 *     dois envios simultâneos saem com números diferentes, sempre. Ordenar
 *     por timestamp empataria no mesmo milissegundo — e "a ordem é a que o
 *     banco devolveu" não é ordem.
 *
 *  2. IDEMPOTÊNCIA. Duplo clique, retry e reconexão mandam o MESMO
 *     `clientMessageId`. Quem decide não é um `SELECT` antes do `INSERT` —
 *     entre os dois cabe a segunda requisição inteira. Quem decide é o
 *     índice único: o perdedor recebe P2002, lê a mensagem que já existe e
 *     devolve ELA. Do lado de fora, o retry parece ter dado certo na
 *     primeira vez, que é exatamente o que deveria parecer.
 *
 *  3. SNAPSHOT. Nome e área de quem escreveu são COPIADOS para a linha. Se
 *     a Aline mudar de setor em março, a mensagem de janeiro continua
 *     dizendo "Aline • Fiscal". Reconstruir a assinatura consultando o
 *     departamento atual reescreveria o passado a cada mudança de
 *     organograma.
 *
 * ---------------------------------------------------------------------
 *  A máquina de estados do status ao enviar/receber
 * ---------------------------------------------------------------------
 *  Enviar (escritório → cliente):
 *
 *      NEW              → IN_PROGRESS   quem responde está atendendo
 *      WAITING_OFFICE   → IN_PROGRESS   a bola estava conosco e saiu
 *      IN_PROGRESS      → IN_PROGRESS   sem mudança
 *      WAITING_CUSTOMER → inalterado    continuamos esperando o cliente;
 *                                       cobrar não é ser respondido
 *      RESOLVED         → inalterado    reabrir é decisão de gente
 *
 *  Receber (cliente → escritório):
 *
 *      WAITING_CUSTOMER → WAITING_OFFICE   o cliente respondeu
 *      NEW              → inalterado       continua novo e sem dono
 *      IN_PROGRESS      → inalterado
 *      WAITING_OFFICE   → inalterado
 *      RESOLVED         → inalterado       NÃO reabre sozinho: sobe na
 *                                          lista e aparece como não lido,
 *                                          e quem reabre é uma pessoa
 *
 *  O que a regra evita: mensagem mexendo em RESOLVED e em WAITING_CUSTOMER
 *  por conta própria. Status foi decisão de alguém; sobrescrevê-lo em
 *  silêncio faz a equipe parar de usá-lo.
 */
import { audit } from "@/server/auth/audit";
import { requirePermission, type AuthContext } from "@/server/auth/context";
import { despacharSemBloquear } from "@/server/channels/whatsapp/outbound";
import { logger } from "@/server/logging/logger";
import { tipoDosAnexos, vincularAnexos } from "@/server/media/service";
import { rotuloDeMidia } from "@/server/messages/queries";
import { esquecer } from "@/server/notifications/coalescing";
import { avisarSemBloquear } from "@/server/notifications/service";
import { publish } from "@/server/realtime/bus";
import { withTenant, type TenantClient } from "@/server/tenancy";
import type {
  ConversationStatus,
  MessageDirection,
  MessageType,
} from "@/generated/prisma/enums";

export const TAMANHO_MAXIMO = 4000;

export type FalhaMensagem = "NOT_FOUND" | "EMPTY" | "TOO_LONG";

export type ResultadoMensagem<T> =
  | { ok: true; value: T }
  | { ok: false; code: FalhaMensagem; message: string };

export interface MensagemCriada {
  id: string;
  conversationId: string;
  sequence: number;
  direction: MessageDirection;
  /** TEXT, IMAGE, DOCUMENT, AUDIO ou VOICE — decidido pelo anexo. */
  tipo: MessageType;
  content: string;
  senderMembershipId: string | null;
  senderDisplayName: string | null;
  senderDepartmentName: string | null;
  createdAt: Date;
  /** `true` quando o `clientMessageId` já existia: retry, não duplicata. */
  duplicada: boolean;
}

/* ─── assinatura congelada ───────────────────────────────────────────── */

/**
 * Nome e área de AGORA, para gravar na mensagem.
 *
 * Devolve os dois campos separados, e não uma string pronta: quem monta
 * "Aline • Fiscal" é a interface. Guardar o texto formatado impediria a
 * tela de mudar a apresentação sem reescrever o histórico.
 */
async function assinaturaAtual(
  tx: TenantClient,
  membershipId: string,
): Promise<{ nome: string; area: string | null }> {
  const m = await tx.membership.findUnique({
    where: { id: membershipId },
    select: { primaryDepartmentId: true, identity: { select: { name: true } } },
  });
  if (!m) return { nome: "Atendente", area: null };

  if (!m.primaryDepartmentId) return { nome: m.identity.name, area: null };

  const d = await tx.department.findUnique({
    where: { id: m.primaryDepartmentId },
    select: { name: true },
  });
  return { nome: m.identity.name, area: d?.name ?? null };
}

/* ─── numeração + estado da conversa ─────────────────────────────────── */

interface Avanco {
  sequence: number;
  status: ConversationStatus;
  version: number;
  lastActivityAt: Date;
  assignedMembershipId: string | null;
}

/** O par que sai de dentro da transação: a mensagem e o estado novo. */
interface Gravada {
  mensagem: MensagemCriada;
  avanco: Avanco;
  /** Metadado do primeiro anexo, para o evento de realtime. */
  anexo?: {
    id: string;
    fileName: string;
    mimeType: string;
    sizeBytes: number;
    durationMs: number | null;
  } | null;
}

/**
 * Reserva o próximo `sequence` e move o status pela regra do cabeçalho —
 * num comando só.
 *
 * O `UPDATE` trava a linha da conversa: qualquer outro envio na mesma
 * conversa espera aqui, pega o número seguinte, e ninguém precisa de
 * `SELECT max(sequence)`, que é a versão que se parece certa e não é.
 */
async function avancarConversa(
  tx: TenantClient,
  conversationId: string,
  direcao: MessageDirection,
  primeiraResposta: boolean,
): Promise<Avanco | null> {
  const linhas = await tx.$queryRaw<
    {
      sequence: number;
      status: ConversationStatus;
      version: number;
      last_activity_at: Date;
      assigned_membership_id: string | null;
    }[]
  >`
    UPDATE conversations
       SET message_seq = message_seq + 1,
           status = CASE
             WHEN ${direcao}::message_direction = 'OUTBOUND'::message_direction
              AND status IN ('NEW'::conversation_status,
                             'WAITING_OFFICE'::conversation_status)
               THEN 'IN_PROGRESS'::conversation_status
             WHEN ${direcao}::message_direction = 'INBOUND'::message_direction
              AND status = 'WAITING_CUSTOMER'::conversation_status
               THEN 'WAITING_OFFICE'::conversation_status
             ELSE status END,
           first_response_at = CASE
             WHEN ${primeiraResposta} THEN COALESCE(first_response_at, now())
             ELSE first_response_at END,
           last_activity_at = now(),
           updated_at = now(),
           version = version + 1
     WHERE id = ${conversationId}::uuid
     RETURNING message_seq AS sequence, status, version, last_activity_at,
               assigned_membership_id
  `;

  const l = linhas[0];
  return l
    ? {
        sequence: l.sequence,
        status: l.status,
        version: l.version,
        lastActivityAt: l.last_activity_at,
        assignedMembershipId: l.assigned_membership_id,
      }
    : null;
}

/** Só as primeiras palavras. Vai para a lista e para o evento de realtime. */
function previa(texto: string): string {
  const limpo = texto.replace(/\s+/g, " ").trim();
  return limpo.length <= 120 ? limpo : `${limpo.slice(0, 119)}…`;
}

/** P2002 = violação de índice único. É assim que o retry é reconhecido. */
function ehConflitoDeUnico(erro: unknown): boolean {
  return (
    typeof erro === "object" &&
    erro !== null &&
    "code" in erro &&
    (erro as { code?: unknown }).code === "P2002"
  );
}

/* ─── enviar (escritório → cliente) ──────────────────────────────────── */

export interface NovaMensagem {
  content: string;
  /** Gerado pelo navegador ANTES do envio. É o que torna o retry seguro. */
  clientMessageId?: string | null;
  /**
   * Anexos já enviados, ainda PENDING, que esta mensagem adota.
   *
   * A adoção acontece na MESMA transação que cria a mensagem: ou nascem
   * juntos, ou o anexo continua pendente e a faxina o recolhe. Ver
   * media/service.ts.
   */
  attachmentIds?: string[];
}

export async function enviarMensagem(
  ctx: AuthContext,
  conversationId: string,
  dados: NovaMensagem,
): Promise<ResultadoMensagem<MensagemCriada>> {
  requirePermission(ctx, "messages.send");

  const texto = dados.content.trim();
  const anexos = dados.attachmentIds ?? [];

  // Mensagem so de midia nao tem texto — e obrigar uma legenda seria
  // inventar trabalho para quem so quer mandar o PDF da guia.
  if (texto.length === 0 && anexos.length === 0) {
    return { ok: false, code: "EMPTY", message: "Escreva alguma coisa antes de enviar." };
  }
  if (texto.length > TAMANHO_MAXIMO) {
    return {
      ok: false,
      code: "TOO_LONG",
      message: `Mensagem muito longa (máximo ${TAMANHO_MAXIMO} caracteres).`,
    };
  }

  const clientMessageId = dados.clientMessageId?.trim() || null;

  let gravada: Gravada | null = null;

  try {
    gravada = await withTenant(ctx.tenantId, async (tx) => {
      const conversa = await tx.conversation.findUnique({
        where: { id: conversationId },
        select: { id: true, firstResponseAt: true },
      });
      if (!conversa) return null;

      // A primeira resposta é a primeira MESMO. Depois disso o campo não
      // se mexe: "tempo até a primeira resposta" não pode melhorar nem
      // piorar quando a segunda mensagem sai.
      const primeiraResposta = conversa.firstResponseAt === null;

      const assinatura = await assinaturaAtual(tx, ctx.membershipId);

      // O tipo sai do anexo: uma imagem faz a mensagem ser IMAGE, uma
      // gravacao faz ser VOICE. Sem anexo, TEXT.
      const tipo = (await tipoDosAnexos(tx, ctx.tenantId, anexos)) ?? "TEXT";

      const avanco = await avancarConversa(tx, conversationId, "OUTBOUND", primeiraResposta);
      if (!avanco) return null;

      const msg = await tx.message.create({
        data: {
          tenantId: ctx.tenantId,
          conversationId,
          sequence: avanco.sequence,
          direction: "OUTBOUND",
          type: tipo,
          senderMembershipId: ctx.membershipId,
          // Congelado aqui. Ver o cabeçalho do arquivo.
          senderDisplayName: assinatura.nome,
          senderDepartmentName: assinatura.area,
          channel: "INTERNAL",
          content: texto,
          clientMessageId,
        },
        select: { id: true, createdAt: true },
      });

      // A adocao acontece AQUI dentro: ou mensagem e anexo nascem juntos,
      // ou o anexo continua pendente e a faxina o recolhe. Nunca mensagem
      // apontando para anexo que nao existe.
      await vincularAnexos(tx, ctx.tenantId, msg.id, anexos);

      // Metadado do anexo para o evento — uma consulta, ja dentro da
      // transacao em que ele acabou de ser adotado.
      const primeiro = anexos.length
        ? await tx.messageAttachment.findFirst({
            where: { messageId: msg.id },
            select: {
              id: true,
              originalFileName: true,
              mimeType: true,
              sizeBytes: true,
              durationMs: true,
            },
            orderBy: { createdAt: "asc" },
          })
        : null;
      const anexo = primeiro
        ? {
            id: primeiro.id,
            fileName: primeiro.originalFileName,
            mimeType: primeiro.mimeType,
            sizeBytes: primeiro.sizeBytes,
            durationMs: primeiro.durationMs,
          }
        : null;

      return {
        mensagem: {
          id: msg.id,
          conversationId,
          sequence: avanco.sequence,
          direction: "OUTBOUND" as MessageDirection,
          tipo,
          content: texto,
          senderMembershipId: ctx.membershipId,
          senderDisplayName: assinatura.nome,
          senderDepartmentName: assinatura.area,
          createdAt: msg.createdAt,
          duplicada: false,
        },
        avanco,
        anexo,
      };
    });
  } catch (erro: unknown) {
    if (!ehConflitoDeUnico(erro) || !clientMessageId) throw erro;

    // Retry de uma mensagem que JÁ foi gravada. A transação acima morreu
    // inteira — inclusive o incremento do `sequence` —, então basta ler a
    // que venceu e devolvê-la como se fosse a resposta original.
    const existente = await withTenant(ctx.tenantId, async (tx) =>
      tx.message.findFirst({
        where: { senderMembershipId: ctx.membershipId, clientMessageId },
        select: {
          id: true,
          conversationId: true,
          sequence: true,
          direction: true,
          type: true,
          content: true,
          senderDisplayName: true,
          senderDepartmentName: true,
          createdAt: true,
        },
      }),
    );

    if (!existente) throw erro;

    logger.info("message.send.duplicate", {
      tenantId: ctx.tenantId,
      conversationId,
      messageId: existente.id,
    });

    const { type, ...resto } = existente;
    return {
      ok: true,
      value: {
        ...resto,
        tipo: type,
        senderMembershipId: ctx.membershipId,
        duplicada: true,
      },
    };
  }

  if (!gravada) {
    return { ok: false, code: "NOT_FOUND", message: "Atendimento não encontrado." };
  }

  await audit({
    tenantId: ctx.tenantId,
    membershipId: ctx.membershipId,
    action: "MESSAGE_SENT",
    targetType: "conversation",
    targetId: conversationId,
    // O conteúdo pertence à tabela `messages`. Copiá-lo para a trilha de
    // auditoria criaria um segundo lugar de onde vazar a mesma coisa.
    metadata: {
      messageId: gravada.mensagem.id,
      sequence: String(gravada.mensagem.sequence),
    },
  });

  anunciar(ctx.tenantId, gravada);

  // O canal externo, se houver um. Depois do COMMIT e sem `await`: a
  // Meta fora do ar não pode fazer o funcionário ver erro numa mensagem
  // que JÁ está gravada. Ela fica `QUEUED` (ou `FAILED`) e visível.
  //
  // Conversa interna não vai a lugar nenhum — quem decide é o canal da
  // conversa, dentro do adaptador. O domínio não sabe o que é WhatsApp.
  despacharSemBloquear(
    ctx.tenantId,
    conversationId,
    gravada.mensagem.id,
    gravada.mensagem.content,
    {
      // A assinatura CONGELADA na mensagem (S37), não a de agora. Quem a
      // transforma em texto é o adaptador, na borda.
      nome: gravada.mensagem.senderDisplayName,
      area: gravada.mensagem.senderDepartmentName,
    },
  );

  // Mensagem de SAÍDA também notifica — quando o atendimento é de outra
  // pessoa. É o caso do colega que responde no seu lugar, e o responsável
  // precisa saber. Quem enviou nunca recebe: ver recipients.ts.
  avisarSemBloquear({
    tipo: "MENSAGEM_RECEBIDA",
    tenantId: ctx.tenantId,
    conversationId,
    autorMembershipId: ctx.membershipId,
    messageType: gravada.mensagem.tipo,
    conteudo: gravada.mensagem.content,
  });

  return { ok: true, value: gravada.mensagem };
}

/* ─── receber (cliente → escritório) ─────────────────────────────────── */

export interface MensagemRecebida {
  content: string;
  /** Midia que chega junto. Hoje so a entrada de desenvolvimento usa. */
  attachmentIds?: string[];
  /** Quem, do outro lado. Telefone, id do canal — nunca um Membership. */
  externalSenderId?: string | null;
  /** Id no canal de origem. O canal reentrega; reentrega não é mensagem nova. */
  externalMessageId?: string | null;
  channel?: string;
}

/**
 * Registra mensagem de entrada.
 *
 * Não recebe `AuthContext`: quem chama é o canal, não uma pessoa. Hoje o
 * único chamador é a entrada de desenvolvimento; amanhã será o adaptador
 * do WhatsApp, sem que nada aqui precise saber disso.
 */
export async function registrarMensagemRecebida(
  tenantId: string,
  conversationId: string,
  dados: MensagemRecebida,
): Promise<ResultadoMensagem<MensagemCriada>> {
  const texto = dados.content.trim();
  const anexos = dados.attachmentIds ?? [];
  if (texto.length === 0 && anexos.length === 0) {
    return { ok: false, code: "EMPTY", message: "Mensagem vazia." };
  }
  if (texto.length > TAMANHO_MAXIMO) {
    return { ok: false, code: "TOO_LONG", message: "Mensagem muito longa." };
  }

  const externalMessageId = dados.externalMessageId?.trim() || null;

  let gravada: Gravada | null = null;

  try {
    gravada = await withTenant(tenantId, async (tx) => {
      const conversa = await tx.conversation.findUnique({
        where: { id: conversationId },
        select: { id: true },
      });
      if (!conversa) return null;

      const tipo = (await tipoDosAnexos(tx, tenantId, anexos)) ?? "TEXT";

      const avanco = await avancarConversa(tx, conversationId, "INBOUND", false);
      if (!avanco) return null;

      const msg = await tx.message.create({
        data: {
          tenantId,
          conversationId,
          sequence: avanco.sequence,
          direction: "INBOUND",
          type: tipo,
          externalSenderId: dados.externalSenderId ?? null,
          externalMessageId,
          channel: dados.channel ?? "DEV",
          content: texto,
        },
        select: { id: true, createdAt: true },
      });

      await vincularAnexos(tx, tenantId, msg.id, anexos);

      // Metadado do anexo para o evento — uma consulta, ja dentro da
      // transacao em que ele acabou de ser adotado.
      const primeiro = anexos.length
        ? await tx.messageAttachment.findFirst({
            where: { messageId: msg.id },
            select: {
              id: true,
              originalFileName: true,
              mimeType: true,
              sizeBytes: true,
              durationMs: true,
            },
            orderBy: { createdAt: "asc" },
          })
        : null;
      const anexo = primeiro
        ? {
            id: primeiro.id,
            fileName: primeiro.originalFileName,
            mimeType: primeiro.mimeType,
            sizeBytes: primeiro.sizeBytes,
            durationMs: primeiro.durationMs,
          }
        : null;

      return {
        mensagem: {
          id: msg.id,
          conversationId,
          sequence: avanco.sequence,
          direction: "INBOUND" as MessageDirection,
          tipo,
          content: texto,
          senderMembershipId: null,
          senderDisplayName: null,
          senderDepartmentName: null,
          createdAt: msg.createdAt,
          duplicada: false,
        },
        avanco,
        anexo,
      };
    });
  } catch (erro: unknown) {
    if (!ehConflitoDeUnico(erro) || !externalMessageId) throw erro;

    const existente = await withTenant(tenantId, async (tx) =>
      tx.message.findFirst({
        where: { externalMessageId },
        select: {
          id: true,
          conversationId: true,
          sequence: true,
          direction: true,
          type: true,
          content: true,
          createdAt: true,
        },
      }),
    );
    if (!existente) throw erro;

    const { type, ...resto } = existente;
    return {
      ok: true,
      value: {
        ...resto,
        tipo: type,
        senderMembershipId: null,
        senderDisplayName: null,
        senderDepartmentName: null,
        duplicada: true,
      },
    };
  }

  if (!gravada) {
    return { ok: false, code: "NOT_FOUND", message: "Atendimento não encontrado." };
  }

  await audit({
    tenantId,
    membershipId: null,
    action: "MESSAGE_RECEIVED",
    targetType: "conversation",
    targetId: conversationId,
    metadata: {
      messageId: gravada.mensagem.id,
      sequence: String(gravada.mensagem.sequence),
    },
  });

  anunciar(tenantId, gravada);

  // O aviso sai DEPOIS de a mensagem estar gravada, e sem que ninguém
  // espere por ele. Provedor de push fora do ar não pode fazer um cliente
  // receber erro ao mandar mensagem — ver notifications/service.ts.
  avisarSemBloquear({
    tipo: "MENSAGEM_RECEBIDA",
    tenantId,
    conversationId,
    autorMembershipId: null,
    messageType: gravada.mensagem.tipo,
    conteudo: gravada.mensagem.content,
  });

  return { ok: true, value: gravada.mensagem };
}

/* ─── visualização de mensagem ───────────────────────────────────────── */

/**
 * Marca mensagens como visualizadas POR MIM.
 *
 * Existe como ação explícita, e nunca como efeito de um GET. Se abrir a
 * conversa marcasse tudo, um prefetch do navegador ou um render de
 * componente zeraria o contador de alguém que não olhou nada — e um número
 * de não lidas em que a equipe não confia é pior do que não ter número.
 *
 * `createMany` + `skipDuplicates`: marcar duas vezes não é erro, é a
 * mesma pessoa rolando a tela para cima e para baixo.
 */
export async function marcarVisualizadas(
  ctx: AuthContext,
  conversationId: string,
  messageIds: string[],
): Promise<{ marcadas: number }> {
  requirePermission(ctx, "messages.read");
  if (messageIds.length === 0) return { marcadas: 0 };

  const novas = await withTenant(ctx.tenantId, async (tx) => {
    // Só mensagens DESTA conversa e DESTE tenant — o RLS já garante o
    // segundo, e o `conversationId` no filtro garante que um id solto de
    // outra conversa não crie leitura onde não devia.
    const alvo = await tx.message.findMany({
      where: { id: { in: messageIds }, conversationId, direction: "INBOUND" },
      select: { id: true },
    });
    if (alvo.length === 0) return [];

    const antes = await tx.messageView.findMany({
      where: { messageId: { in: alvo.map((m) => m.id) }, membershipId: ctx.membershipId },
      select: { messageId: true },
    });
    const jaVistas = new Set(antes.map((v) => v.messageId));
    const inéditas = alvo.filter((m) => !jaVistas.has(m.id));
    if (inéditas.length === 0) return [];

    await tx.messageView.createMany({
      data: inéditas.map((m) => ({
        tenantId: ctx.tenantId,
        messageId: m.id,
        membershipId: ctx.membershipId,
      })),
      skipDuplicates: true,
    });

    return inéditas.map((m) => m.id);
  });

  if (novas.length === 0) return { marcadas: 0 };

  // Li a conversa: a rajada acabou. Sem isto, o próximo aviso desta
  // conversa diria "6 novas mensagens" incluindo as cinco que acabei de
  // ler — e contador que mente é pior do que contador nenhum.
  esquecer(ctx.membershipId, conversationId);

  const agora = new Date().toISOString();
  for (const messageId of novas) {
    publish(ctx.tenantId, {
      type: "message.viewed",
      conversationId,
      messageId,
      membershipId: ctx.membershipId,
      viewedAt: agora,
    });
  }

  await audit({
    tenantId: ctx.tenantId,
    membershipId: ctx.membershipId,
    action: "MESSAGE_VIEWED",
    targetType: "conversation",
    targetId: conversationId,
    metadata: { quantidade: String(novas.length) },
  });

  return { marcadas: novas.length };
}

/* ─── aviso ao realtime ──────────────────────────────────────────────── */

/**
 * Dois eventos por mensagem, de propósito: um para quem está com a
 * conversa aberta e outro para quem só tem a lista na tela. Sem o segundo,
 * a lista de atendimentos só se atualizaria ao abrir a conversa — que é
 * justamente quando já não importa.
 */
function anunciar(tenantId: string, { mensagem: m, avanco, anexo }: Gravada): void {
  publish(tenantId, {
    type: "message.created",
    conversationId: m.conversationId,
    messageId: m.id,
    sequence: m.sequence,
    direction: m.direction,
    messageType: m.tipo,
    // Metadado, nunca bytes. Ver events.ts.
    attachment: anexo ?? null,
    preview: previa(m.content) || rotuloDeMidia(m.tipo, anexo?.fileName ?? null),
    senderDisplayName: m.senderDisplayName,
    senderDepartmentName: m.senderDepartmentName,
    createdAt: m.createdAt.toISOString(),
  });

  publish(tenantId, {
    type: "conversation.updated",
    conversationId: m.conversationId,
    status: avanco.status,
    version: avanco.version,
    assignedMembershipId: avanco.assignedMembershipId,
    lastActivityAt: avanco.lastActivityAt.toISOString(),
    reason: "message",
    // Só a direção. É o mínimo para a casca decidir se toca o som, sem
    // que a prévia da mensagem chegue a quem não assinou esta conversa.
    messageDirection: m.direction,
  });
}

/* ─── orientação automática (triagem) ────────────────────────────────── */

/**
 * O identificador determinístico da orientação de triagem.
 *
 * Uma conversa recebe no máximo UMA orientação automática, e é este valor
 * que garante isso. Ele é derivado da conversa — não sorteado —, então
 * reentrega da Meta, retry e reinício do processo chegam todos ao mesmo
 * `clientMessageId`.
 */
export function idDaOrientacao(conversationId: string): string {
  return `elo:triage-reorient:v1:${conversationId}`;
}

/**
 * A orientação automática da triagem. Sai UMA vez por conversa.
 *
 * ---------------------------------------------------------------------
 *  Por que não reusa `enviarMensagem`
 * ---------------------------------------------------------------------
 *  Duas razões, e as duas são de comportamento, não de gosto:
 *
 *  1. `enviarMensagem` exige `AuthContext` e `messages.send`. Não há ator
 *     aqui: quem "escreveu" foi o sistema, e inventar um membership para
 *     satisfazer a assinatura colocaria o nome de uma pessoa numa frase
 *     que ela não escreveu.
 *
 *  2. `avancarConversa` com OUTBOUND move `NEW → IN_PROGRESS` e carimba
 *     `first_response_at`. Para uma resposta automática isso seria mentira
 *     duas vezes: o atendimento sairia da fila "Sem setor" sem ninguém ter
 *     olhado para ele, e a métrica de "tempo até a primeira resposta"
 *     passaria a medir a velocidade do robô. O UPDATE abaixo mexe só no
 *     que precisa: sequência, atividade e versão.
 *
 * ---------------------------------------------------------------------
 *  A trava contra o envio em dobro
 * ---------------------------------------------------------------------
 *  `clientMessageId` determinístico MAIS uma consulta antes de gravar. O
 *  índice único não serve sozinho: ele é
 *  `(tenant, senderMembershipId, clientMessageId)`, e `senderMembershipId`
 *  é NULO nesta mensagem. No PostgreSQL, NULO não colide com NULO — a
 *  proteção simplesmente não existiria justamente para as mensagens que
 *  precisam dela.
 */
export async function enviarOrientacaoDoSistema(
  tenantId: string,
  conversationId: string,
  texto: string,
): Promise<{ enviada: boolean; motivo?: "JA_ENVIADA" | "NAO_ENCONTRADO" }> {
  const clientMessageId = idDaOrientacao(conversationId);

  const gravada = await withTenant(tenantId, async (tx) => {
    const jaExiste = await tx.message.findFirst({
      where: { conversationId, clientMessageId },
      select: { id: true },
    });
    if (jaExiste) return { tipo: "JA_ENVIADA" as const };

    const linhas = await tx.$queryRaw<
      {
        sequence: number;
        status: ConversationStatus;
        version: number;
        last_activity_at: Date;
        assigned_membership_id: string | null;
      }[]
    >`
      UPDATE conversations
         SET message_seq = message_seq + 1,
             last_activity_at = now(),
             updated_at = now(),
             version = version + 1
       WHERE id = ${conversationId}::uuid
       RETURNING message_seq AS sequence, status, version, last_activity_at,
                 assigned_membership_id
    `;
    const l = linhas[0];
    if (!l) return { tipo: "NAO_ENCONTRADO" as const };

    const msg = await tx.message.create({
      data: {
        tenantId,
        conversationId,
        sequence: l.sequence,
        direction: "OUTBOUND",
        type: "TEXT",
        // Sem autor, de propósito: ninguém do escritório escreveu isto.
        senderMembershipId: null,
        senderDisplayName: null,
        senderDepartmentName: null,
        channel: "INTERNAL",
        content: texto,
        clientMessageId,
      },
      select: { id: true, createdAt: true },
    });

    return {
      tipo: "OK" as const,
      messageId: msg.id,
      createdAt: msg.createdAt,
      sequence: l.sequence,
      status: l.status,
      version: l.version,
      lastActivityAt: l.last_activity_at,
      assignedMembershipId: l.assigned_membership_id,
    };
  });

  if (gravada.tipo === "JA_ENVIADA") return { enviada: false, motivo: "JA_ENVIADA" };
  if (gravada.tipo === "NAO_ENCONTRADO") return { enviada: false, motivo: "NAO_ENCONTRADO" };

  publish(tenantId, {
    type: "message.created",
    conversationId,
    messageId: gravada.messageId,
    sequence: gravada.sequence,
    direction: "OUTBOUND",
    messageType: "TEXT",
    attachment: null,
    preview: previa(texto),
    senderDisplayName: null,
    senderDepartmentName: null,
    createdAt: gravada.createdAt.toISOString(),
  });

  publish(tenantId, {
    type: "conversation.updated",
    conversationId,
    status: gravada.status,
    version: gravada.version,
    assignedMembershipId: gravada.assignedMembershipId,
    lastActivityAt: gravada.lastActivityAt.toISOString(),
    reason: "message",
    messageDirection: "OUTBOUND",
  });

  // Depois do COMMIT e sem `await`, como todo o resto: a Meta fora do ar
  // deixa a orientação QUEUED e visível, nunca derruba a triagem.
  despacharSemBloquear(tenantId, conversationId, gravada.messageId, texto, {
    nome: null,
    area: null,
  });

  // Não avisa ninguém: é o robô falando com o cliente, e encher o celular
  // do escritório com "saiu a mensagem automática" não informa nada.
  return { enviada: true };
}
