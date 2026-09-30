/**
 * Escalonamento — a coordenação é avisada do que ficou sem resposta.
 *
 * =====================================================================
 *  O par que faz sentido junto
 * =====================================================================
 *  `ATENDIMENTO_NOVO` deixou de avisar gestor (ver `recipients.ts`): a
 *  fila é de quem atende. Este arquivo é o contrapeso — a coordenação
 *  para de ser acordada por todo caso novo e passa a ser avisada do que
 *  não andou, que é onde ela pode fazer alguma coisa a respeito.
 *
 *  Uma coisa sem a outra seria pior do que o estado anterior: ou a
 *  coordenação some da operação, ou continua recebendo tudo.
 *
 * =====================================================================
 *  O relógio conta só o horário de atendimento
 * =====================================================================
 *  Cliente que escreve às 17h30 de sexta não gera escalonamento às 18h.
 *  O relógio PARA fora de segunda a sexta, 08h–17h, e volta a andar na
 *  abertura seguinte. É por isso que não dá para comparar dois
 *  timestamps e dividir por 60: a conta é de minutos ÚTEIS acumulados.
 *
 *  Fuso fixo de Recife (UTC−3). O Brasil não tem horário de verão desde
 *  2019, e uma tabela de fuso completa aqui seria precisão que ninguém
 *  vai usar — mas está numa constante só, para o dia em que for.
 *
 * =====================================================================
 *  Quem dispara
 * =====================================================================
 *  Não há agendador no Elo, e não se inventou um. A varredura é uma
 *  função pura de efeito, com `agora` injetável, chamada de dois lugares:
 *
 *    - de carona no webhook, como a faxina de anexos faz ("aproveita a
 *      passagem"). Escritório com movimento escalona sozinho;
 *    - por script, para uma Tarefa Agendada cobrir a madrugada e o
 *      escritório parado.
 *
 *  E ela recebe o tenant, pelo mesmo motivo da faxina: o RLS é FORÇADO, e
 *  uma varredura sem contexto leria o vazio para sempre dizendo que está
 *  tudo em dia.
 */
import { logger } from "@/server/logging/logger";
import { avisarSemBloquear } from "@/server/notifications/service";
import { withTenant } from "@/server/tenancy";

/** Minutos ÚTEIS sem resposta até a coordenação ser avisada. */
export const MINUTOS_PARA_ESCALAR = 30;

/** Recife: UTC−3 o ano inteiro. Sem horário de verão desde 2019. */
const FUSO_HORAS = -3;

/** Segunda a sexta, 08h–17h, no fuso acima. */
const ABRE_HORA = 8;
const FECHA_HORA = 17;

/* ─── o relógio comercial ────────────────────────────────────────────── */

/** O instante, visto no fuso do escritório. */
function local(d: Date): Date {
  return new Date(d.getTime() + FUSO_HORAS * 3600_000);
}

/** Segunda(1) a sexta(5), dentro da janela. */
export function dentroDoExpediente(quando: Date): boolean {
  const l = local(quando);
  const dia = l.getUTCDay();
  if (dia === 0 || dia === 6) return false;
  const hora = l.getUTCHours();
  return hora >= ABRE_HORA && hora < FECHA_HORA;
}

/**
 * Minutos de EXPEDIENTE entre dois instantes.
 *
 * Varre em passos de um minuto. É a implementação mais boba possível, e
 * de propósito: o intervalo real é de minutos a algumas horas, a conta
 * roda uma vez por conversa parada, e uma fórmula fechada com feriado,
 * virada de semana e fuso seria muito mais fácil de errar do que de
 * conferir. Se um dia o volume pedir, o teste que trava o comportamento
 * já está escrito.
 */
export function minutosDeExpediente(de: Date, ate: Date): number {
  if (ate <= de) return 0;

  let minutos = 0;
  // O PRIMEIRO minuto inteiro depois da chegada, e conta-se até `ate`
  // inclusive. Começar no próprio instante de chegada contaria as duas
  // pontas e daria 31 minutos entre 13h00 e 13h30 — um minuto a mais,
  // sempre, que é quanto basta para escalar antes da hora.
  const cursor = new Date(Math.floor(de.getTime() / 60_000) * 60_000 + 60_000);

  // Teto de segurança: 60 dias de minutos. Uma conversa esquecida há
  // meses não pode transformar a varredura num laço de milhões de voltas.
  const TETO = 60 * 24 * 60;
  let voltas = 0;

  while (cursor <= ate && voltas < TETO) {
    if (dentroDoExpediente(cursor)) minutos += 1;
    cursor.setTime(cursor.getTime() + 60_000);
    voltas += 1;
  }

  return minutos;
}

/* ─── a varredura ────────────────────────────────────────────────────── */

export interface ResultadoEscalonamento {
  /** Conversas que acabaram de ser escaladas agora. */
  escaladas: number;
  /** Olhadas e descartadas — já respondidas, ainda no prazo, já escaladas. */
  examinadas: number;
}

/**
 * Escala o que passou do prazo. Idempotente.
 *
 * O que NÃO acontece aqui, e é o ponto:
 *
 *   - o status da conversa não muda. Escalonamento é aviso, não
 *     atendimento, e mover para IN_PROGRESS diria que alguém assumiu;
 *   - `firstResponseAt` não é tocado. Carimbá-lo faria a métrica de
 *     primeira resposta medir o robô avisando, e não o escritório
 *     respondendo — mentira que só apareceria num relatório, meses depois;
 *   - nenhuma mensagem sai para o cliente.
 */
export async function escalonarPendentes(
  tenantId: string,
  agora: Date = new Date(),
): Promise<ResultadoEscalonamento> {
  const candidatas = await withTenant(tenantId, async (tx) =>
    tx.conversation.findMany({
      where: {
        // Sem resposta NENHUMA do escritório. É a definição de "parado"
        // que interessa — e ela se apaga sozinha quando alguém responde.
        firstResponseAt: null,
        status: { in: ["NEW", "WAITING_OFFICE"] },
        // Sem área não há coordenação para avisar: a conversa está em
        // "Sem setor", e quem resolve isso é a triagem humana.
        assignedDepartmentId: { not: null },
        // A trava de "já avisei": o evento é a marca.
        events: { none: { type: "ESCALATED" } },
      },
      select: { id: true, createdAt: true, assignedDepartmentId: true },
      take: 200,
    }),
  );

  let escaladas = 0;

  for (const c of candidatas) {
    const minutos = minutosDeExpediente(c.createdAt, agora);
    if (minutos < MINUTOS_PARA_ESCALAR) continue;

    // QUEM GRAVA O EVENTO É QUEM AVISA — e o banco decide quem grava.
    //
    // Ler "já existe?" e só então inserir NÃO resolve corrida: em READ
    // COMMITTED, duas varreduras simultâneas — o webhook e a tarefa
    // periódica no mesmo segundo — leem as duas "não existe" e gravam as
    // duas. Quem impede é o índice único PARCIAL sobre
    // (conversation_id) WHERE type = 'ESCALATED', e o `ON CONFLICT DO
    // NOTHING` que o consulta atomicamente.
    //
    // `RETURNING id` é o que diz se esta execução ganhou: linha devolvida
    // significa "fui eu, então eu aviso"; nenhuma linha significa que
    // outra já avisou, e esta cala a boca.
    const metadata = JSON.stringify({ minutos, regra: `${MINUTOS_PARA_ESCALAR}min-uteis` });

    const ganhou = await withTenant(tenantId, async (tx) => {
      const linhas = await tx.$queryRaw<{ id: string }[]>`
        INSERT INTO conversation_events
               (id, tenant_id, conversation_id, type, actor_membership_id, metadata, created_at)
        VALUES (gen_random_uuid(), ${tenantId}::uuid, ${c.id}::uuid,
                'ESCALATED'::conversation_event_type,
                -- NULO: quem escalou foi o relógio, não uma pessoa.
                NULL,
                -- Só código e número. Nada do conteúdo da conversa.
                ${metadata}::jsonb, now())
        ON CONFLICT DO NOTHING
        RETURNING id
      `;
      return linhas.length > 0;
    });

    const gravou = ganhou;

    if (!gravou) continue;

    escaladas += 1;
    avisarSemBloquear({
      tipo: "ATENDIMENTO_ESCALADO",
      tenantId,
      conversationId: c.id,
      minutos,
    });
  }

  if (escaladas > 0) {
    logger.info("escalonamento.aplicado", { tenantId, escaladas });
  }

  return { escaladas, examinadas: candidatas.length };
}
