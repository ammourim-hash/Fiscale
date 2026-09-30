/**
 * Leitura de mensagens.
 *
 * ---------------------------------------------------------------------
 *  Paginação por cursor, e não por página
 * ---------------------------------------------------------------------
 *  Conversa é lista que cresce pela ponta. Com `OFFSET`, cada mensagem
 *  nova empurra tudo: a pessoa rola para cima e vê de novo o que já leu,
 *  ou pula uma. O cursor é o `sequence`, que não se move.
 *
 *  Ao abrir, carregam-se as ÚLTIMAS. Subindo, pede-se `antesDe`.
 *
 * ---------------------------------------------------------------------
 *  Não lidas: por pessoa, calculado, nunca guardado
 * ---------------------------------------------------------------------
 *  Um contador materializado na conversa divergiria — e um número errado
 *  de não lidas é pior que nenhum. O cálculo é "mensagem de entrada que
 *  não tem linha minha em message_views", numa consulta só para a lista
 *  inteira.
 */
import { can, requirePermission, type AuthContext } from "@/server/auth/context";
import { withTenant, type TenantClient } from "@/server/tenancy";
import type { MessageDirection, MessageType } from "@/generated/prisma/enums";

export const PAGINA_PADRAO = 40;
export const PAGINA_MAXIMA = 100;

/**
 * Metadado do anexo. NUNCA a `storageKey` — o caminho interno do storage
 * nao sai daqui, e o cliente pede os bytes por `/api/attachments/<id>`.
 */
export interface AnexoNaTela {
  id: string;
  type: MessageType;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  width: number | null;
  height: number | null;
  durationMs: number | null;
}

export interface MensagemNaTela {
  id: string;
  sequence: number;
  direction: MessageDirection;
  type: MessageType;
  content: string;
  attachments: AnexoNaTela[];
  /** Congelado no envio. NÃO é o departamento atual da pessoa. */
  senderDisplayName: string | null;
  senderDepartmentName: string | null;
  senderMembershipId: string | null;
  externalSenderId: string | null;
  createdAt: Date;
  /** Só do escritório. Nunca sai para o cliente. */
  viewedBy: { membershipId: string; name: string; department: string | null; viewedAt: Date }[];
}

export interface PaginaDeMensagens {
  messages: MensagemNaTela[];
  /** Existe coisa mais antiga para carregar? */
  hasMore: boolean;
  /** Cursor para a próxima subida. */
  oldestSequence: number | null;
}

export async function listarMensagens(
  ctx: AuthContext,
  conversationId: string,
  opcoes: { antesDe?: number | null; limite?: number } = {},
): Promise<PaginaDeMensagens | null> {
  requirePermission(ctx, "messages.read");

  const limite = Math.min(Math.max(opcoes.limite ?? PAGINA_PADRAO, 1), PAGINA_MAXIMA);
  // Quem não pode ver recibos não recebe a lista vazia: nem se consulta.
  // O que não sai do banco não vaza numa serialização distraída.
  const podeVerRecibos = can(ctx, "messages.view_receipts");

  return withTenant(ctx.tenantId, async (tx) => {
    const conversa = await tx.conversation.findUnique({
      where: { id: conversationId },
      select: { id: true },
    });
    if (!conversa) return null;

    // Pede-se UM a mais do que cabe. Se vier, existe página anterior — e
    // isso custa uma linha em vez de um `count` sobre a conversa inteira.
    const linhas = await tx.message.findMany({
      where: {
        conversationId,
        ...(opcoes.antesDe != null ? { sequence: { lt: opcoes.antesDe } } : {}),
      },
      orderBy: { sequence: "desc" },
      take: limite + 1,
      select: {
        id: true,
        sequence: true,
        direction: true,
        type: true,
        content: true,
        senderDisplayName: true,
        senderDepartmentName: true,
        senderMembershipId: true,
        externalSenderId: true,
        createdAt: true,
        // Metadado apenas. Um historico de 40 mensagens nao carrega 40
        // arquivos: os bytes so sao buscados quando alguem abre ou toca.
        attachments: {
          where: { status: "ATTACHED" },
          select: {
            id: true,
            type: true,
            originalFileName: true,
            mimeType: true,
            sizeBytes: true,
            width: true,
            height: true,
            durationMs: true,
          },
          orderBy: { createdAt: "asc" },
        },
      },
    });

    const hasMore = linhas.length > limite;
    const pagina = hasMore ? linhas.slice(0, limite) : linhas;
    // Do banco vêm em ordem decrescente porque o cursor olha para trás; na
    // tela a conversa é lida de cima para baixo.
    pagina.reverse();

    const recibos = podeVerRecibos
      ? await recibosDe(tx, pagina.map((m) => m.id))
      : new Map<string, MensagemNaTela["viewedBy"]>();

    return {
      messages: pagina.map((m) => ({
        ...m,
        attachments: m.attachments.map((a) => ({
          id: a.id,
          type: a.type,
          fileName: a.originalFileName,
          mimeType: a.mimeType,
          sizeBytes: a.sizeBytes,
          width: a.width,
          height: a.height,
          durationMs: a.durationMs,
        })),
        viewedBy: recibos.get(m.id) ?? [],
      })),
      hasMore,
      oldestSequence: pagina[0]?.sequence ?? null,
    };
  });
}

/** Quem, do escritório, já abriu cada uma destas mensagens. */
async function recibosDe(
  tx: TenantClient,
  messageIds: string[],
): Promise<Map<string, MensagemNaTela["viewedBy"]>> {
  const mapa = new Map<string, MensagemNaTela["viewedBy"]>();
  if (messageIds.length === 0) return mapa;

  const vistas = await tx.messageView.findMany({
    where: { messageId: { in: messageIds } },
    orderBy: { viewedAt: "asc" },
    select: {
      messageId: true,
      membershipId: true,
      viewedAt: true,
      membership: {
        select: { primaryDepartmentId: true, identity: { select: { name: true } } },
      },
    },
  });
  if (vistas.length === 0) return mapa;

  const areas = new Map<string, string>();
  const ids = [
    ...new Set(vistas.map((v) => v.membership.primaryDepartmentId).filter((i): i is string => !!i)),
  ];
  if (ids.length > 0) {
    const ds = await tx.department.findMany({
      where: { id: { in: ids } },
      select: { id: true, name: true },
    });
    for (const d of ds) areas.set(d.id, d.name);
  }

  for (const v of vistas) {
    const lista = mapa.get(v.messageId) ?? [];
    lista.push({
      membershipId: v.membershipId,
      name: v.membership.identity.name,
      department: v.membership.primaryDepartmentId
        ? (areas.get(v.membership.primaryDepartmentId) ?? null)
        : null,
      viewedAt: v.viewedAt,
    });
    mapa.set(v.messageId, lista);
  }
  return mapa;
}

/**
 * O que a lista mostra quando a mensagem nao tem texto.
 *
 * "Foto", "Audio" — e nao o nome do arquivo, que costuma ser
 * `IMG_20260810_193045.jpg` e nao diz nada. Documento e a excecao: ali o
 * nome E a informacao ("DAS_08-2026.pdf").
 */
export function rotuloDeMidia(tipo: MessageType, nomeArquivo: string | null): string {
  switch (tipo) {
    case "IMAGE":
      return "Foto";
    case "DOCUMENT":
      return nomeArquivo ?? "Documento";
    case "AUDIO":
      return "Áudio";
    case "VOICE":
      return "Mensagem de voz";
    default:
      return "";
  }
}

/* ─── apoio à lista de atendimentos ──────────────────────────────────── */

export interface ResumoDeMensagens {
  /** Quantas mensagens do cliente EU ainda não abri. */
  unreadCount: number;
  preview: { direction: MessageDirection; content: string; createdAt: Date } | null;
}

/**
 * Não lidas e prévia para uma lista inteira de atendimentos, em duas
 * consultas — não em duas por linha.
 *
 * O `NOT EXISTS` é por membership: "não lido" é meu, e não do escritório.
 * Se a Aline abriu e o Carlos não, para ela sai da lista e para ele fica.
 */
export async function resumoDeMensagens(
  ctx: AuthContext,
  conversationIds: string[],
): Promise<Map<string, ResumoDeMensagens>> {
  const mapa = new Map<string, ResumoDeMensagens>();
  if (conversationIds.length === 0) return mapa;

  await withTenant(ctx.tenantId, async (tx) => {
    const naoLidas = await tx.$queryRaw<{ conversation_id: string; total: bigint }[]>`
      SELECT m.conversation_id, count(*) AS total
        FROM messages m
       WHERE m.conversation_id = ANY(${conversationIds}::uuid[])
         AND m.direction = 'INBOUND'::message_direction
         AND m.deleted_at IS NULL
         AND NOT EXISTS (
               SELECT 1 FROM message_views v
                WHERE v.message_id = m.id
                  AND v.membership_id = ${ctx.membershipId}::uuid)
       GROUP BY m.conversation_id
    `;

    // DISTINCT ON pega a última de cada conversa numa varredura só. A
    // alternativa — um `findFirst` por conversa — seria N consultas para
    // desenhar uma lista.
    const ultimas = await tx.$queryRaw<
      {
        conversation_id: string;
        direction: MessageDirection;
        content: string;
        type: MessageType;
        created_at: Date;
        file_name: string | null;
      }[]
    >`
      SELECT DISTINCT ON (m.conversation_id)
             m.conversation_id, m.direction, m.content, m.type, m.created_at,
             (SELECT a.original_file_name
                FROM message_attachments a
               WHERE a.message_id = m.id
               ORDER BY a.created_at ASC
               LIMIT 1) AS file_name
        FROM messages m
       WHERE m.conversation_id = ANY(${conversationIds}::uuid[])
         AND m.deleted_at IS NULL
       ORDER BY m.conversation_id, m.sequence DESC
    `;

    for (const id of conversationIds) mapa.set(id, { unreadCount: 0, preview: null });

    for (const linha of naoLidas) {
      const atual = mapa.get(linha.conversation_id);
      if (atual) atual.unreadCount = Number(linha.total);
    }
    for (const linha of ultimas) {
      const atual = mapa.get(linha.conversation_id);
      if (atual) {
        // Midia sem legenda nao pode virar linha em branco na lista: sem
        // isto, um atendimento que so recebeu uma foto aparece sem
        // nenhuma indicacao do que chegou.
        const texto =
          linha.content.trim().length > 0
            ? linha.content
            : rotuloDeMidia(linha.type, linha.file_name);

        atual.preview = {
          direction: linha.direction,
          content: texto.length > 120 ? `${texto.slice(0, 119)}…` : texto,
          createdAt: linha.created_at,
        };
      }
    }
  });

  return mapa;
}
