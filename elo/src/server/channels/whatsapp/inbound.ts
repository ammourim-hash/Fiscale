/**
 * Entrada: evento da Meta → atendimento e mensagem do Elo.
 *
 * ---------------------------------------------------------------------
 *  O que este arquivo NÃO faz
 * ---------------------------------------------------------------------
 *  Não cria modelo novo. `Conversation` e `Message` são as mesmas do MVP
 *  1.4 e 1.5, e quem grava a mensagem é `registrarMensagemRecebida` — o
 *  mesmo serviço que a entrada de desenvolvimento usa desde o 1.5.
 *
 *  Consequência que vale dizer: **notificação e realtime funcionam sem
 *  uma linha nova**. A mensagem nasce pelo caminho de sempre, e o SSE e o
 *  Web Push do MVP 1.7 já estão pendurados nele.
 *
 * ---------------------------------------------------------------------
 *  A identificação do cliente reusa o MVP 1.2, com as três respostas
 * ---------------------------------------------------------------------
 *      EXACT      → vincula o atendimento ao cliente
 *      NONE       → atendimento sem cliente ("Contato não identificado")
 *      AMBIGUOUS  → **atendimento sem cliente**, e uma pessoa decide
 *
 *  `AMBIGUOUS` não escolhe sozinho, e isso é decisão registrada (S22):
 *  matriz e filial com o mesmo telefone é rotina em contabilidade, e
 *  escolher em silêncio entrega a conversa de um cliente na ficha de
 *  outro. A tela de vínculo manual é da fase 1.8.2.
 */
import { audit } from "@/server/auth/audit";
import { criarAtendimento } from "@/server/conversations/service";
import { escalonarPendentes } from "@/server/conversations/escalonamento";
import { triarSePreciso } from "@/server/conversations/triagem";
import { findCustomerByPhone } from "@/server/customers/lookup";
import { logger } from "@/server/logging/logger";
import { registrarMensagemRecebida } from "@/server/messages/service";
import { withTenant } from "@/server/tenancy";

import { CANAL_WHATSAPP } from "./constantes";
import type { EventoWhatsApp } from "./payload";

export interface ResultadoEntrada {
  /** Mensagens gravadas agora. */
  criadas: number;
  /** Reentregas da Meta que a idempotência absorveu. */
  duplicadas: number;
  /** Eventos de um número que não é nosso, ou que falharam. */
  recusadas: number;
}

/* ─── telefone ───────────────────────────────────────────────────────── */

/**
 * O `wa_id` vem sem "+". A busca do MVP 1.2 espera E.164.
 *
 * Nada além disso: **não** se completa DDI, não se adivinha DDD, não se
 * "conserta" número curto. É a regra S22 — número normalizado errado não
 * dá erro, entrega a conversa de um cliente na tela de outro.
 */
export function waIdParaE164(waId: string): string {
  const digitos = waId.replace(/\D/g, "");
  return digitos.length > 0 ? `+${digitos}` : waId;
}

/* ─── atendimento ────────────────────────────────────────────────────── */

/**
 * O atendimento daquele contato, criando se ainda não existir.
 *
 * A corrida é real: três mensagens seguidas no WhatsApp chegam em
 * webhooks quase simultâneos. Quem resolve é o índice único
 * `(tenant, canal, contato externo)` — não um `findFirst` seguido de
 * `create`, entre os quais cabe a segunda mensagem inteira. Mesma lição
 * de S26 e S36: o perdedor lê o vencedor e segue.
 */
async function atendimentoDoContato(
  tenantId: string,
  waId: string,
  nomeDoPerfil: string | null,
): Promise<string> {
  const existente = await withTenant(tenantId, async (tx) =>
    tx.conversation.findFirst({
      where: { channel: CANAL_WHATSAPP, externalContactId: waId },
      select: { id: true },
    }),
  );
  if (existente) return existente.id;

  const telefone = waIdParaE164(waId);
  const casamento = await findCustomerByPhone(tenantId, telefone);

  if (casamento.status === "AMBIGUOUS") {
    // Dois clientes com o mesmo telefone. O atendimento nasce SEM cliente
    // e o registro fica no log; escolher aqui seria a decisão silenciosa
    // que o MVP 1.2 existe para tornar impossível.
    logger.warn("whatsapp.contato.ambiguo", {
      tenantId,
      candidatos: casamento.candidates.length,
    });
  }

  try {
    const { id } = await criarAtendimento(tenantId, {
      customerReferenceId: casamento.status === "EXACT" ? casamento.customer.id : null,
      channel: CANAL_WHATSAPP,
      externalContactId: waId,
    });
    return id;
  } catch (erro: unknown) {
    // P2002 = outra mensagem do mesmo contato ganhou a corrida. Ler a que
    // venceu é o comportamento certo — e o único que não perde mensagem.
    if (!ehConflitoDeUnico(erro)) throw erro;

    const vencedor = await withTenant(tenantId, async (tx) =>
      tx.conversation.findFirstOrThrow({
        where: { channel: CANAL_WHATSAPP, externalContactId: waId },
        select: { id: true },
      }),
    );
    return vencedor.id;
  } finally {
    // O nome do perfil é só exibição e não vale um caminho de escrita
    // próprio nesta fase — fica registrado para a 1.8.2, junto da tela de
    // vínculo manual.
    void nomeDoPerfil;
  }
}

function ehConflitoDeUnico(erro: unknown): boolean {
  return (
    typeof erro === "object" &&
    erro !== null &&
    "code" in erro &&
    (erro as { code?: unknown }).code === "P2002"
  );
}

/* ─── processamento ──────────────────────────────────────────────────── */

/**
 * Processa os eventos já interpretados.
 *
 * @param tenantPorNumero resolve `phone_number_id → tenant`. Vem de fora
 *   para o teste poder exercitar "número que não é nosso" sem mexer no
 *   ambiente — e para deixar explícito que o tenant NUNCA sai do payload.
 */
export async function processar(
  eventos: EventoWhatsApp[],
  tenantPorNumero: (phoneNumberId: string) => string | null,
): Promise<ResultadoEntrada> {
  const r: ResultadoEntrada = { criadas: 0, duplicadas: 0, recusadas: 0 };

  for (const evento of eventos) {
    const tenantId = tenantPorNumero(evento.phoneNumberId);

    if (!tenantId) {
      // Webhook de um número que não é nosso. Acontece de verdade quando
      // um app atende mais de uma WABA — e é exatamente o que não pode
      // virar mensagem no banco de alguém.
      logger.warn("whatsapp.numero_desconhecido", {
        phoneNumberId: evento.phoneNumberId,
      });
      r.recusadas += 1;
      continue;
    }

    try {
      if (evento.tipo === "MENSAGEM") {
        const conversationId = await atendimentoDoContato(
          tenantId,
          evento.de,
          evento.nomeDoPerfil,
        );

        const gravada = await registrarMensagemRecebida(tenantId, conversationId, {
          content: evento.texto,
          externalSenderId: evento.de,
          // A trava de idempotência. A Meta REENTREGA por dias quando não
          // recebe 200 — e reentrega não é mensagem nova (S36).
          externalMessageId: evento.externalMessageId,
          channel: CANAL_WHATSAPP,
        });

        if (!gravada.ok) {
          logger.warn("whatsapp.mensagem.recusada", { tenantId, code: gravada.code });
          r.recusadas += 1;
          continue;
        }

        if (gravada.value.duplicada) {
          // Reentrega da Meta. A mensagem original já passou pela triagem;
          // repetir criaria um segundo TRIAGED e uma segunda orientação
          // para o mesmo fato.
          r.duplicadas += 1;
          continue;
        }

        r.criadas += 1;

        // A TRIAGEM VEM DEPOIS, E NUNCA DERRUBA NADA.
        //
        // Neste ponto a mensagem está gravada, o atendimento existe, o SSE
        // já saiu e o Web Push já foi disparado. O que vier a falhar aqui
        // é ruído: vira log e a conversa fica em "Sem setor", que é uma
        // fila visível — nunca um limbo.
        try {
          const triagem = await triarSePreciso(tenantId, conversationId, evento.texto);
          if (triagem.tipo !== "NAO_APLICA") {
            logger.info("triagem.resultado", { tenantId, resultado: triagem.tipo });
          }
        } catch (erro: unknown) {
          logger.error("triagem.falhou", { tenantId, erro: String(erro) });
        }
        continue;
      }

      await aplicarStatus(tenantId, evento);
    } catch (erro: unknown) {
      // Um evento com defeito não pode derrubar o lote inteiro: os outros
      // do mesmo webhook continuam, e a Meta não reentrega o que já deu
      // certo. Sem isto, uma mensagem estranha bloquearia a fila.
      logger.error("whatsapp.evento.falhou", {
        tenantId,
        tipo: evento.tipo,
        erro: String(erro),
      });
      r.recusadas += 1;
    }
  }

  // DE CARONA: a varredura de escalonamento, uma vez por lote.
  //
  // Não há agendador no Elo, e não se inventou um aqui. A faxina de
  // anexos já usa este mesmo caminho — "aproveita a passagem" —, e o
  // efeito é que escritório com movimento escalona sozinho. O silêncio
  // da madrugada fica para a Tarefa Agendada, que chama o mesmo código.
  //
  // Sem `await` e com `catch`: a Meta espera 200 desta rota, e uma
  // varredura lenta ou quebrada não pode atrasar nem derrubar o webhook.
  for (const tenantId of new Set(
    eventos.map((e) => tenantPorNumero(e.phoneNumberId)).filter((t): t is string => !!t),
  )) {
    void escalonarPendentes(tenantId).catch((erro: unknown) => {
      logger.error("escalonamento.falhou", { tenantId, erro: String(erro) });
    });
  }

  return r;
}

/* ─── status de entrega ──────────────────────────────────────────────── */

/**
 * `sent`/`delivered`/`read`/`failed` → `MessageDeliveryStatus`.
 *
 * ---------------------------------------------------------------------
 *  Duas regras, e as duas evitam confusão que já custou caro em outros
 *  sistemas
 * ---------------------------------------------------------------------
 *  1. **Status não anda para trás.** A Meta não garante ordem: o `read`
 *     pode chegar antes do `delivered`. Sem esta trava, um ✓✓ azul
 *     viraria ✓ cinza sozinho na tela, e ninguém entenderia por quê.
 *
 *  2. **`READ` é do CLIENTE, no WhatsApp.** Não é `MessageView`, que é a
 *     Aline abrindo no Elo. São lados diferentes da conversa (S40), e um
 *     dia aparecem juntos na mesma tela.
 */
const ORDEM: Record<string, number> = {
  QUEUED: 0,
  SENT: 1,
  DELIVERED: 2,
  READ: 3,
  // `FAILED` é terminal e fica FORA da escala: uma falha depois de um
  // `sent` continua sendo falha, e não um retrocesso a ser ignorado.
  FAILED: 99,
};

const MAPA = {
  sent: "SENT",
  delivered: "DELIVERED",
  read: "READ",
  failed: "FAILED",
} as const;

async function aplicarStatus(
  tenantId: string,
  evento: Extract<EventoWhatsApp, { tipo: "STATUS" }>,
): Promise<void> {
  const novo = MAPA[evento.status];

  await withTenant(tenantId, async (tx) => {
    const mensagem = await tx.message.findFirst({
      where: { externalMessageId: evento.externalMessageId },
      select: { id: true, deliveryStatus: true },
    });

    if (!mensagem) {
      // Status de uma mensagem que não é nossa, ou que chegou antes de a
      // resposta do envio ter sido gravada. Não é erro — e inventar uma
      // mensagem a partir de um status seria bem pior.
      logger.debug("whatsapp.status.sem_mensagem", { tenantId });
      return;
    }

    const atual = ORDEM[mensagem.deliveryStatus] ?? 0;
    const proposto = ORDEM[novo] ?? 0;
    if (proposto <= atual) return;

    await tx.message.update({
      where: { id: mensagem.id },
      data: { deliveryStatus: novo },
    });
  });

  if (novo === "FAILED") {
    // Falha de entrega é fato que uma pessoa precisa poder investigar.
    // Código e título; nunca o payload, que carrega telefone e conteúdo.
    logger.warn("whatsapp.entrega.falhou", {
      tenantId,
      codigo: evento.erro?.codigo ?? null,
      titulo: evento.erro?.titulo ?? null,
    });

    await audit({
      tenantId,
      membershipId: null,
      action: "WHATSAPP_DELIVERY_FAILED",
      targetType: "message",
      metadata: { codigo: String(evento.erro?.codigo ?? "") },
    });
  }
}
