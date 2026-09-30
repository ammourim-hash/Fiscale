/**
 * Trilha de auditoria.
 *
 * Registra QUE algo aconteceu, nunca o segredo envolvido. Token, cookie,
 * hash de sessao e conteudo de mensagem nao entram — nem em `metadata`.
 * A regra vale mesmo quando parece util para depurar: log de auditoria e
 * lido por gente e sobrevive a backups.
 */
import { withTenant } from "@/server/tenancy";
import { logger } from "@/server/logging/logger";

export const AUDIT_ACTIONS = [
  "AUTH_EXCHANGE_SUCCESS",
  "AUTH_EXCHANGE_FAILED",
  "LOGIN_SESSION_CREATED",
  "LOGOUT",
  "SESSION_REVOKED",
  // MVP 1.2 — sincronizacao da projecao de clientes.
  "FISCALE_SYNC_STARTED",
  "FISCALE_SYNC_COMPLETED",
  "FISCALE_SYNC_FAILED",
  "CUSTOMER_PROJECTION_CREATED",
  "CUSTOMER_PROJECTION_UPDATED",
  "CUSTOMER_PROJECTION_DEACTIVATED",
  // MVP 1.4 — atendimento.
  "CONVERSATION_CREATED",
  "CONVERSATION_VIEWED",
  "CONVERSATION_ASSIGNED",
  "CONVERSATION_TRANSFERRED",
  "CONVERSATION_STATUS_CHANGED",
  "CONVERSATION_RESOLVED",
  "CONVERSATION_REOPENED",
  "INTERNAL_NOTE_CREATED",
  "TAG_CHANGED",
  // MVP 1.5 — mensagens. Registram QUE houve mensagem, com o id e a
  // ordem. O TEXTO fica em `messages` e so la: copia-lo para ca criaria
  // um segundo lugar de onde o mesmo conteudo pode vazar, com politica de
  // retencao diferente.
  "MESSAGE_SENT",
  "MESSAGE_RECEIVED",
  "MESSAGE_VIEWED",
  // MVP 1.6 — midia. Registra QUE houve arquivo, com tipo e tamanho. O
  // conteudo fica no storage, e o nome original na tabela do anexo.
  "ATTACHMENT_UPLOADED",
  // MVP 1.7 — notificacoes. Registram QUE um aparelho foi inscrito ou
  // desligado, e QUE alguem mexeu nas proprias preferencias.
  //
  // O que NAO entra: o endpoint, o `p256dh`, o `auth` e o conteudo de
  // qualquer aviso. O endpoint e um segredo de capacidade — quem o tem
  // entrega notificacao naquele aparelho —, e a trilha de auditoria e
  // lida por gente e sobrevive a backups. Do aparelho fica o ROTULO
  // ("Chrome no Windows"), que e o que uma pessoa precisa para reconhecer
  // o que aconteceu.
  "PUSH_SUBSCRIBED",
  "PUSH_UNSUBSCRIBED",
  "NOTIFICATION_PREFERENCE_CHANGED",
  // MVP 1.8.1 — canal WhatsApp.
  //
  // Registram QUE o canal recusou algo, com codigo. NUNCA entram aqui:
  // access token, App Secret, telefone do cliente, conteudo da mensagem
  // nem o payload do webhook. Um webhook recusado guarda o MOTIVO — se
  // guardasse o corpo, a trilha de auditoria viraria o arquivo do que um
  // atacante mandou.
  "WHATSAPP_WEBHOOK_REJECTED",
  "WHATSAPP_SEND_FAILED",
  "WHATSAPP_DELIVERY_FAILED",
] as const;

export type AuditAction = (typeof AUDIT_ACTIONS)[number];

export interface AuditEntry {
  tenantId: string;
  membershipId?: string | null;
  action: AuditAction;
  targetType?: string | null;
  targetId?: string | null;
  /** Codigos e contagens. Nunca valores sensiveis. */
  metadata?: Record<string, string | number | boolean> | null;
  ipHash?: string | null;
}

/**
 * Grava a entrada. Falha de auditoria nao derruba a operacao — mas vira
 * `error` no log, porque auditoria silenciosamente quebrada e pior do que
 * auditoria ausente: ninguem percebe.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function audit(entry: AuditEntry): Promise<void> {
  // Uma tentativa de troca pode trazer um tenant que nem e uuid. Nao ha
  // onde gravar essa auditoria — a tabela e por tenant e o tenant nao
  // existe. Registrar no log e a resposta certa; tratar como falha de
  // escrita transformaria ruido de sondagem em alarme.
  if (!UUID.test(entry.tenantId)) {
    logger.warn("audit.skipped_invalid_tenant", { action: entry.action });
    return;
  }

  try {
    await withTenant(entry.tenantId, async (tx) => {
      await tx.auditLog.create({
        data: {
          tenantId: entry.tenantId,
          membershipId: entry.membershipId ?? null,
          action: entry.action,
          targetType: entry.targetType ?? null,
          targetId: entry.targetId ?? null,
          ...(entry.metadata ? { metadata: entry.metadata } : {}),
          ipHash: entry.ipHash ?? null,
        },
      });
    });
  } catch (erro) {
    logger.error("audit.write_failed", { action: entry.action, erro });
  }
}
