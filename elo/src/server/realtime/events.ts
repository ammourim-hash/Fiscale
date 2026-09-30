/**
 * Os eventos que o realtime carrega.
 *
 * Duas regras que valem para todos:
 *
 *   1. O evento é um AVISO, não o dado. Ele diz "a conversa X mudou" e o
 *      mínimo para a tela decidir o que fazer sem piscar. Quem tem a
 *      verdade continua sendo o banco: se o aviso se perder — e vai se
 *      perder, porque conexão cai —, a próxima leitura conserta. Realtime
 *      é mecanismo de atualização, não armazenamento.
 *
 *   2. Nada de conteúdo sensível. A prévia da mensagem existe porque a
 *      lista mostra prévia de qualquer jeito; nota interna, dado do
 *      cliente e permissão não atravessam daqui.
 *
 * O `tenantId` aparece no envelope, e não dentro de cada evento, porque é
 * ele que decide QUEM recebe — ver bus.ts.
 */

export type EloEvent =
  | {
      type: "message.created";
      conversationId: string;
      messageId: string;
      sequence: number;
      direction: "INBOUND" | "OUTBOUND" | "SYSTEM";
      /** Só o começo. A tela pede o resto quando precisar. */
      preview: string;
      /**
       * O bastante para a tela desenhar o balão certo — e nada além.
       *
       * O ARQUIVO não viaja aqui: o evento diz "chegou um áudio de 37
       * segundos", e o cliente busca os bytes pela rota autenticada
       * quando (e se) alguém for ouvir.
       */
      messageType: "TEXT" | "IMAGE" | "DOCUMENT" | "AUDIO" | "VOICE";
      attachment: {
        id: string;
        fileName: string;
        mimeType: string;
        sizeBytes: number;
        durationMs: number | null;
      } | null;
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
      /** Status, responsável, área, etiqueta, nota: tudo que move a lista. */
      type: "conversation.updated";
      conversationId: string;
      status: string;
      version: number;
      assignedMembershipId: string | null;
      lastActivityAt: string;
      reason: "status" | "assignment" | "message" | "note" | "tag";
      /**
       * De quem partiu, quando `reason` é "message". Só a DIREÇÃO — nada
       * de prévia, nada de autor.
       *
       * Existe por um motivo específico: o evento de mensagem só vai para
       * quem assinou aquela conversa (ver a rota SSE), e o som precisa
       * tocar justamente para quem NÃO está nela. Sem este campo, a única
       * forma de o Elo saber que houve mensagem nova em outro atendimento
       * seria receber o evento da mensagem — o que entregaria a prévia a
       * todas as abas do tenant, que é exatamente o que a rota evita.
       */
      messageDirection?: "INBOUND" | "OUTBOUND" | "SYSTEM";
    };

export type EloEventType = EloEvent["type"];
