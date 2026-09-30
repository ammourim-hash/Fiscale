/**
 * As ações sobre um atendimento.
 *
 * ---------------------------------------------------------------------
 *  A regra que governa o arquivo
 * ---------------------------------------------------------------------
 *  A conversa pertence ao TENANT. O funcionário é o responsável ATUAL.
 *  Transferir troca o responsável; não cria outro atendimento, não esconde
 *  o anterior de ninguém, não reescreve o histórico.
 *
 * ---------------------------------------------------------------------
 *  Concorrência: duas pessoas clicando "Assumir" ao mesmo tempo
 * ---------------------------------------------------------------------
 *  Não dá para ler, decidir e escrever. Entre o `SELECT` que vê "sem
 *  responsável" e o `UPDATE` que grava, a outra pessoa faz o mesmo — e as
 *  duas gravam. A última vence, a primeira acha que assumiu, e as duas
 *  respondem o mesmo cliente.
 *
 *  A condição vai DENTRO do UPDATE:
 *
 *      UPDATE ... SET assigned = eu
 *       WHERE id = ? AND assigned_membership_id IS NULL
 *
 *  Uma linha afetada: assumi. Zero: alguém chegou antes — e a resposta diz
 *  QUEM, em vez de fingir que deu certo.
 *
 *  Para transferir, resolver e mudar status vale o mesmo raciocínio com
 *  `version`: trava otimista. Quem envia a partir de uma tela velha recebe
 *  conflito em vez de sobrescrever a decisão de outro em silêncio.
 */
import { audit } from "@/server/auth/audit";
import type { AuthContext } from "@/server/auth/context";
import { requirePermission } from "@/server/auth/context";
import { logger } from "@/server/logging/logger";
import { avisarSemBloquear } from "@/server/notifications/service";
import { publish } from "@/server/realtime/bus";
import { withTenant, type TenantClient } from "@/server/tenancy";
import type { ConversationEventType, ConversationStatus } from "@/generated/prisma/enums";

/**
 * Avisa quem está com a tela aberta.
 *
 * Depois do COMMIT, sempre. Publicar de dentro da transação faria a outra
 * aba buscar um estado que ainda não existe para ela — e, se a transação
 * abortasse, o aviso já teria saído sobre um fato que não aconteceu.
 */
async function anunciarConversa(
  tenantId: string,
  conversationId: string,
  reason: "status" | "assignment" | "note" | "tag",
): Promise<void> {
  const c = await withTenant(tenantId, async (tx) =>
    tx.conversation.findUnique({
      where: { id: conversationId },
      select: {
        status: true,
        version: true,
        assignedMembershipId: true,
        lastActivityAt: true,
      },
    }),
  );
  if (!c) return;

  publish(tenantId, {
    type: "conversation.updated",
    conversationId,
    status: c.status,
    version: c.version,
    assignedMembershipId: c.assignedMembershipId,
    lastActivityAt: c.lastActivityAt.toISOString(),
    reason,
  });
}

export type FalhaAtendimento =
  | "NOT_FOUND"
  | "ALREADY_ASSIGNED"
  | "STALE"
  | "INVALID_TARGET"
  | "INVALID_STATUS";

export type Resultado<T> =
  | { ok: true; value: T }
  | { ok: false; code: FalhaAtendimento; message: string };

function falha<T>(code: FalhaAtendimento, message: string): Resultado<T> {
  return { ok: false, code, message };
}

/* ─── histórico ──────────────────────────────────────────────────────── */

interface EventoEntrada {
  conversationId: string;
  type: ConversationEventType;
  actorMembershipId?: string | null;
  fromMembershipId?: string | null;
  toMembershipId?: string | null;
  fromDepartmentId?: string | null;
  toDepartmentId?: string | null;
  fromStatus?: ConversationStatus | null;
  toStatus?: ConversationStatus | null;
  metadata?: Record<string, string | number | boolean> | null;
}

/** Uma linha na linha do tempo. Nunca conteúdo sensível em `metadata`. */
async function registrarEvento(
  tx: TenantClient,
  tenantId: string,
  e: EventoEntrada,
): Promise<void> {
  await tx.conversationEvent.create({
    data: {
      tenantId,
      conversationId: e.conversationId,
      type: e.type,
      actorMembershipId: e.actorMembershipId ?? null,
      fromMembershipId: e.fromMembershipId ?? null,
      toMembershipId: e.toMembershipId ?? null,
      fromDepartmentId: e.fromDepartmentId ?? null,
      toDepartmentId: e.toDepartmentId ?? null,
      fromStatus: e.fromStatus ?? null,
      toStatus: e.toStatus ?? null,
      ...(e.metadata ? { metadata: e.metadata } : {}),
    },
  });
}

/** Nome legível de um membership, para a mensagem de conflito. */
async function assinaturaDe(
  tx: TenantClient,
  membershipId: string | null,
): Promise<string> {
  if (!membershipId) return "ninguém";
  const m = await tx.membership.findUnique({
    where: { id: membershipId },
    select: {
      identity: { select: { name: true } },
      primaryDepartmentId: true,
    },
  });
  if (!m) return "outra pessoa";

  const nome = m.identity.name.trim().split(/\s+/)[0] ?? m.identity.name;
  if (!m.primaryDepartmentId) return nome;

  const d = await tx.department.findUnique({
    where: { id: m.primaryDepartmentId },
    select: { name: true },
  });
  return d ? `${nome} • ${d.name}` : nome;
}

/* ─── criar ──────────────────────────────────────────────────────────── */

export interface NovoAtendimento {
  customerReferenceId?: string | null;
  channel?: string;
  /**
   * Quem é o cliente DENTRO do canal externo — no WhatsApp, o `wa_id`.
   *
   * É o endereço de resposta e a chave que reencontra o atendimento
   * quando a mesma pessoa escreve de novo. Nulo nos canais internos.
   */
  externalContactId?: string | null;
  /** Escolha do cliente, quando houver. Nulo dos dois = Atendimento Geral. */
  assignedMembershipId?: string | null;
  assignedDepartmentId?: string | null;
}

export async function criarAtendimento(
  tenantId: string,
  dados: NovoAtendimento,
  ator?: AuthContext | null,
): Promise<{ id: string }> {
  const criado = await withTenant(tenantId, async (tx) => {
    const c = await tx.conversation.create({
      data: {
        tenantId,
        customerReferenceId: dados.customerReferenceId ?? null,
        channel: dados.channel ?? "WHATSAPP",
        externalContactId: dados.externalContactId ?? null,
        assignedMembershipId: dados.assignedMembershipId ?? null,
        assignedDepartmentId: dados.assignedDepartmentId ?? null,
        // Nasce NEW mesmo quando o cliente escolheu alguém: ser escolhido
        // não é ter atendido. IN_PROGRESS só quando a pessoa assume.
        status: "NEW",
        ...(dados.assignedMembershipId ? { firstAssignedAt: new Date() } : {}),
      },
      select: { id: true },
    });

    await registrarEvento(tx, tenantId, {
      conversationId: c.id,
      type: "CREATED",
      actorMembershipId: ator?.membershipId ?? null,
      toMembershipId: dados.assignedMembershipId ?? null,
      toDepartmentId: dados.assignedDepartmentId ?? null,
      toStatus: "NEW",
      metadata: { channel: dados.channel ?? "WHATSAPP" },
    });

    return c;
  });

  await audit({
    tenantId,
    membershipId: ator?.membershipId ?? null,
    action: "CONVERSATION_CREATED",
    targetType: "conversation",
    targetId: criado.id,
  });

  // Atendimento que já nasce com dono avisa o dono; sem dono, avisa a fila
  // elegível daquela área. Quem decide é notifications/recipients.ts — e
  // nunca "todo mundo do escritório".
  if (dados.assignedMembershipId) {
    avisarSemBloquear({
      tipo: "ATENDIMENTO_ATRIBUIDO",
      tenantId,
      conversationId: criado.id,
      destinoMembershipId: dados.assignedMembershipId,
      autorMembershipId: ator?.membershipId ?? null,
      transferencia: false,
    });
  } else {
    avisarSemBloquear({
      tipo: "ATENDIMENTO_NOVO",
      tenantId,
      conversationId: criado.id,
      autorMembershipId: ator?.membershipId ?? null,
    });
  }

  return criado;
}

/* ─── visualizar ─────────────────────────────────────────────────────── */

/**
 * Registra que a pessoa abriu o atendimento.
 *
 * NÃO mexe no responsável. Se abrir assumisse, ninguém olharia a caixa por
 * medo de virar dono do problema — o oposto de uma caixa compartilhada.
 *
 * O evento VIEWED entra no histórico só na PRIMEIRA vez de cada pessoa.
 * Quem abre um caso o abre dez vezes por dia; registrar todas encheria a
 * linha do tempo de ruído e esconderia o que importa.
 */
export async function registrarVisualizacao(
  ctx: AuthContext,
  conversationId: string,
): Promise<Resultado<{ primeiraVez: boolean }>> {
  requirePermission(ctx, "conversations.read");

  const r = await withTenant(ctx.tenantId, async (tx) => {
    const conversa = await tx.conversation.findUnique({
      where: { id: conversationId },
      select: { id: true, firstViewedAt: true },
    });
    if (!conversa) return null;

    const existente = await tx.conversationView.findUnique({
      where: {
        conversationId_membershipId: { conversationId, membershipId: ctx.membershipId },
      },
      select: { id: true },
    });

    const agora = new Date();

    if (existente) {
      await tx.conversationView.update({
        where: { id: existente.id },
        data: { lastViewedAt: agora, viewCount: { increment: 1 } },
      });
      return { primeiraVez: false };
    }

    await tx.conversationView.create({
      data: {
        tenantId: ctx.tenantId,
        conversationId,
        membershipId: ctx.membershipId,
        firstViewedAt: agora,
        lastViewedAt: agora,
      },
    });

    // `firstViewedAt` do atendimento é do ESCRITÓRIO: a primeira vez que
    // alguém olhou. Só se grava uma vez — a métrica "tempo até a primeira
    // visualização" não pode mudar quando a segunda pessoa abre.
    if (!conversa.firstViewedAt) {
      await tx.conversation.update({
        where: { id: conversationId },
        data: { firstViewedAt: agora },
      });
    }

    await registrarEvento(tx, ctx.tenantId, {
      conversationId,
      type: "VIEWED",
      actorMembershipId: ctx.membershipId,
    });

    return { primeiraVez: true };
  });

  if (!r) return falha("NOT_FOUND", "Atendimento não encontrado.");

  if (r.primeiraVez) {
    await audit({
      tenantId: ctx.tenantId,
      membershipId: ctx.membershipId,
      action: "CONVERSATION_VIEWED",
      targetType: "conversation",
      targetId: conversationId,
    });
  }

  return { ok: true, value: r };
}

/* ─── assumir ────────────────────────────────────────────────────────── */

export interface AtribuicaoFeita {
  conversationId: string;
  assignedMembershipId: string;
  status: ConversationStatus;
  version: number;
}

/**
 * Assumir para si.
 *
 * A condição "ainda sem responsável" vive dentro do UPDATE — é o que faz
 * dois cliques simultâneos terem exatamente um vencedor.
 */
export async function assumirAtendimento(
  ctx: AuthContext,
  conversationId: string,
): Promise<Resultado<AtribuicaoFeita>> {
  requirePermission(ctx, "conversations.assign");

  const r = await withTenant(ctx.tenantId, async (tx) => {
    const antes = await tx.conversation.findUnique({
      where: { id: conversationId },
      select: { id: true, status: true, assignedMembershipId: true },
    });
    if (!antes) return { tipo: "NAO_ENCONTRADO" as const };

    // A corrida acontece AQUI, e o banco resolve sozinho.
    const afetadas = await tx.$executeRaw`
      UPDATE conversations
         SET assigned_membership_id = ${ctx.membershipId}::uuid,
             status = CASE WHEN status = 'NEW'::conversation_status
                           THEN 'IN_PROGRESS'::conversation_status
                           ELSE status END,
             first_assigned_at = COALESCE(first_assigned_at, now()),
             last_activity_at = now(),
             updated_at = now(),
             version = version + 1
       WHERE id = ${conversationId}::uuid
         AND assigned_membership_id IS NULL
    `;

    if (afetadas === 0) {
      // Alguém chegou antes. Dizer QUEM é o que evita a pessoa clicar de
      // novo achando que travou.
      const atual = await tx.conversation.findUnique({
        where: { id: conversationId },
        select: { assignedMembershipId: true },
      });
      const quem = await assinaturaDe(tx, atual?.assignedMembershipId ?? null);
      return { tipo: "JA_ASSUMIDO" as const, quem };
    }

    const depois = await tx.conversation.findUniqueOrThrow({
      where: { id: conversationId },
      select: { status: true, version: true },
    });

    await registrarEvento(tx, ctx.tenantId, {
      conversationId,
      type: "ASSIGNED",
      actorMembershipId: ctx.membershipId,
      toMembershipId: ctx.membershipId,
      ...(antes.status !== depois.status
        ? { fromStatus: antes.status, toStatus: depois.status }
        : {}),
    });

    return { tipo: "OK" as const, status: depois.status, version: depois.version };
  });

  if (r.tipo === "NAO_ENCONTRADO") {
    return falha("NOT_FOUND", "Atendimento não encontrado.");
  }
  if (r.tipo === "JA_ASSUMIDO") {
    logger.info("conversation.assume.conflict", {
      tenantId: ctx.tenantId,
      conversationId,
    });
    return falha(
      "ALREADY_ASSIGNED",
      `Este atendimento acabou de ser assumido por ${r.quem}.`,
    );
  }

  await audit({
    tenantId: ctx.tenantId,
    membershipId: ctx.membershipId,
    action: "CONVERSATION_ASSIGNED",
    targetType: "conversation",
    targetId: conversationId,
  });

  await anunciarConversa(ctx.tenantId, conversationId, "assignment");

  return {
    ok: true,
    value: {
      conversationId,
      assignedMembershipId: ctx.membershipId,
      status: r.status,
      version: r.version,
    },
  };
}

/* ─── transferir / atribuir a outra pessoa ───────────────────────────── */

export interface Transferencia {
  /** Nulo devolve o atendimento à fila. */
  toMembershipId?: string | null;
  toDepartmentId?: string | null;
  /** Versão que a tela tinha. Divergiu, é conflito. */
  expectedVersion: number;
  reason?: string | null;
}

/**
 * Transferir.
 *
 * Não muda status: transferir não resolve nem reabre nada. O atendimento
 * continua o mesmo, com outro responsável.
 */
export async function transferirAtendimento(
  ctx: AuthContext,
  conversationId: string,
  dados: Transferencia,
): Promise<Resultado<{ version: number }>> {
  requirePermission(ctx, "conversations.transfer");

  const r = await withTenant(ctx.tenantId, async (tx) => {
    const antes = await tx.conversation.findUnique({
      where: { id: conversationId },
      select: {
        id: true,
        version: true,
        assignedMembershipId: true,
        assignedDepartmentId: true,
      },
    });
    if (!antes) return { tipo: "NAO_ENCONTRADO" as const };

    // O destino precisa ser deste tenant e estar apto. O RLS já impede ver
    // membership de outro escritório — a consulta abaixo simplesmente não
    // encontra, e a transferência é recusada.
    if (dados.toMembershipId) {
      const destino = await tx.membership.findUnique({
        where: { id: dados.toMembershipId },
        select: { active: true, availableForAssignment: true },
      });
      if (!destino) return { tipo: "DESTINO_INVALIDO" as const, motivo: "pessoa inexistente" };
      if (!destino.active) {
        return { tipo: "DESTINO_INVALIDO" as const, motivo: "pessoa inativa" };
      }
      if (!destino.availableForAssignment) {
        return {
          tipo: "DESTINO_INVALIDO" as const,
          motivo: "pessoa indisponível para receber atendimento",
        };
      }
    }

    if (dados.toDepartmentId) {
      const dep = await tx.department.findUnique({
        where: { id: dados.toDepartmentId },
        select: { id: true },
      });
      if (!dep) return { tipo: "DESTINO_INVALIDO" as const, motivo: "área inexistente" };
    }

    const novoDepartamento =
      dados.toDepartmentId === undefined ? antes.assignedDepartmentId : dados.toDepartmentId;

    const afetadas = await tx.$executeRaw`
      UPDATE conversations
         SET assigned_membership_id = ${dados.toMembershipId ?? null}::uuid,
             assigned_department_id = ${novoDepartamento}::uuid,
             first_assigned_at = COALESCE(first_assigned_at,
                                          CASE WHEN ${dados.toMembershipId ?? null}::uuid IS NULL
                                               THEN NULL ELSE now() END),
             last_activity_at = now(),
             updated_at = now(),
             version = version + 1
       WHERE id = ${conversationId}::uuid
         AND version = ${dados.expectedVersion}
    `;

    if (afetadas === 0) return { tipo: "DESATUALIZADO" as const };

    const depois = await tx.conversation.findUniqueOrThrow({
      where: { id: conversationId },
      select: { version: true },
    });

    const trocouPessoa = antes.assignedMembershipId !== (dados.toMembershipId ?? null);
    const trocouArea = antes.assignedDepartmentId !== novoDepartamento;

    await registrarEvento(tx, ctx.tenantId, {
      conversationId,
      // Sem destino é devolver à fila, e isso tem nome próprio.
      type: dados.toMembershipId ? "TRANSFERRED" : "UNASSIGNED",
      actorMembershipId: ctx.membershipId,
      ...(trocouPessoa
        ? {
            fromMembershipId: antes.assignedMembershipId,
            toMembershipId: dados.toMembershipId ?? null,
          }
        : {}),
      ...(trocouArea
        ? { fromDepartmentId: antes.assignedDepartmentId, toDepartmentId: novoDepartamento }
        : {}),
      ...(dados.reason ? { metadata: { motivo: dados.reason.slice(0, 200) } } : {}),
    });

    return { tipo: "OK" as const, version: depois.version };
  });

  if (r.tipo === "NAO_ENCONTRADO") return falha("NOT_FOUND", "Atendimento não encontrado.");
  if (r.tipo === "DESTINO_INVALIDO") {
    return falha("INVALID_TARGET", `Não é possível transferir: ${r.motivo}.`);
  }
  if (r.tipo === "DESATUALIZADO") {
    return falha(
      "STALE",
      "Este atendimento mudou enquanto você olhava. Atualize a tela e tente de novo.",
    );
  }

  await audit({
    tenantId: ctx.tenantId,
    membershipId: ctx.membershipId,
    action: "CONVERSATION_TRANSFERRED",
    targetType: "conversation",
    targetId: conversationId,
  });

  await anunciarConversa(ctx.tenantId, conversationId, "assignment");

  // Devolver à fila (`toMembershipId` nulo) NÃO gera aviso de
  // transferência: não há destino. O atendimento volta a ser da fila, e a
  // fila é avisada por mensagem nova, não por alguém ter soltado o caso.
  if (dados.toMembershipId) {
    avisarSemBloquear({
      tipo: "ATENDIMENTO_ATRIBUIDO",
      tenantId: ctx.tenantId,
      conversationId,
      destinoMembershipId: dados.toMembershipId,
      autorMembershipId: ctx.membershipId,
      transferencia: true,
    });
  }

  return { ok: true, value: { version: r.version } };
}

/* ─── status ─────────────────────────────────────────────────────────── */

const STATUS_VALIDOS: ConversationStatus[] = [
  "NEW",
  "IN_PROGRESS",
  "WAITING_CUSTOMER",
  "WAITING_OFFICE",
  "RESOLVED",
];

/**
 * Muda o status.
 *
 * `RESOLVED` e a volta dele têm evento próprio (RESOLVED / REOPENED) além
 * do STATUS_CHANGED: resolver e reabrir são os dois momentos que as
 * métricas vão procurar, e caçá-los dentro de "mudou de status" seria
 * trabalho à toa.
 */
export async function mudarStatus(
  ctx: AuthContext,
  conversationId: string,
  novo: ConversationStatus,
  expectedVersion: number,
): Promise<Resultado<{ status: ConversationStatus; version: number }>> {
  requirePermission(ctx, "conversations.status");

  if (!STATUS_VALIDOS.includes(novo)) {
    return falha("INVALID_STATUS", "Status desconhecido.");
  }

  const r = await withTenant(ctx.tenantId, async (tx) => {
    const antes = await tx.conversation.findUnique({
      where: { id: conversationId },
      select: { status: true, resolvedAt: true },
    });
    if (!antes) return { tipo: "NAO_ENCONTRADO" as const };
    if (antes.status === novo) {
      return { tipo: "SEM_MUDANCA" as const, status: antes.status };
    }

    const virandoResolvido = novo === "RESOLVED";
    const reabrindo = antes.status === "RESOLVED" && novo !== "RESOLVED";

    const afetadas = await tx.$executeRaw`
      UPDATE conversations
         SET status = ${novo}::conversation_status,
             resolved_at = CASE
               WHEN ${virandoResolvido} THEN now()
               WHEN ${reabrindo} THEN NULL
               ELSE resolved_at END,
             last_activity_at = now(),
             updated_at = now(),
             version = version + 1
       WHERE id = ${conversationId}::uuid
         AND version = ${expectedVersion}
    `;

    if (afetadas === 0) return { tipo: "DESATUALIZADO" as const };

    const depois = await tx.conversation.findUniqueOrThrow({
      where: { id: conversationId },
      select: { status: true, version: true },
    });

    await registrarEvento(tx, ctx.tenantId, {
      conversationId,
      type: "STATUS_CHANGED",
      actorMembershipId: ctx.membershipId,
      fromStatus: antes.status,
      toStatus: novo,
    });

    if (virandoResolvido || reabrindo) {
      await registrarEvento(tx, ctx.tenantId, {
        conversationId,
        type: virandoResolvido ? "RESOLVED" : "REOPENED",
        actorMembershipId: ctx.membershipId,
        fromStatus: antes.status,
        toStatus: novo,
      });
    }

    return {
      tipo: "OK" as const,
      status: depois.status,
      version: depois.version,
      virandoResolvido,
      reabrindo,
    };
  });

  if (r.tipo === "NAO_ENCONTRADO") return falha("NOT_FOUND", "Atendimento não encontrado.");
  if (r.tipo === "SEM_MUDANCA") {
    // Reenviar o mesmo status não é erro nem escrita: é idempotência.
    const atual = await withTenant(ctx.tenantId, async (tx) =>
      tx.conversation.findUniqueOrThrow({
        where: { id: conversationId },
        select: { version: true },
      }),
    );
    return { ok: true, value: { status: r.status, version: atual.version } };
  }
  if (r.tipo === "DESATUALIZADO") {
    return falha(
      "STALE",
      "Este atendimento mudou enquanto você olhava. Atualize a tela e tente de novo.",
    );
  }

  await audit({
    tenantId: ctx.tenantId,
    membershipId: ctx.membershipId,
    action: r.virandoResolvido
      ? "CONVERSATION_RESOLVED"
      : r.reabrindo
        ? "CONVERSATION_REOPENED"
        : "CONVERSATION_STATUS_CHANGED",
    targetType: "conversation",
    targetId: conversationId,
    metadata: { status: r.status },
  });

  await anunciarConversa(ctx.tenantId, conversationId, "status");

  return { ok: true, value: { status: r.status, version: r.version } };
}

/* ─── triagem: o CLIENTE escolheu o setor ────────────────────────────── */

export interface EscolhaDeSetor {
  departmentId: string;
  /** `fiscal`, `contabil`, `dp`. Vai para o metadata como código. */
  slug: string;
  /** "Fiscal", "Contábil", "DP". Vai para o metadata como rótulo. */
  rotulo: string;
}

/**
 * Aplica a escolha de setor feita pelo cliente.
 *
 * ---------------------------------------------------------------------
 *  Não é transferência, e o evento diz isso
 * ---------------------------------------------------------------------
 *  `TRANSFERRED` é decisão do escritório e tem ator. `TRIAGED` é escolha
 *  do cliente e nasce com `actorMembershipId` NULO. Reaproveitar o
 *  primeiro apagaria a diferença — que é justamente a que alguém vai
 *  querer auditar quando perguntar "quem mandou isso para o Fiscal?".
 *
 * ---------------------------------------------------------------------
 *  O que NÃO acontece aqui
 * ---------------------------------------------------------------------
 *  O status continua `NEW` e ninguém vira responsável. Escolher o setor é
 *  entrar na fila daquela área, não ser atendido: `IN_PROGRESS` continua
 *  significando "uma pessoa assumiu", como em todo o resto do sistema.
 *
 *  O UPDATE é condicional de propósito. Entre a mensagem chegar e a
 *  triagem rodar cabe um atendente assumindo o caso pela tela, e nesse
 *  caso a automação não pode mexer em nada — por isso as três condições
 *  viajam no WHERE, e não num `if` lido antes.
 */
export async function registrarEscolhaDeSetor(
  tenantId: string,
  conversationId: string,
  escolha: EscolhaDeSetor,
): Promise<{ aplicada: boolean }> {
  const r = await withTenant(tenantId, async (tx) => {
    const afetadas = await tx.$executeRaw`
      UPDATE conversations
         SET assigned_department_id = ${escolha.departmentId}::uuid,
             last_activity_at = now(),
             updated_at = now(),
             version = version + 1
       WHERE id = ${conversationId}::uuid
         AND status = 'NEW'::conversation_status
         AND assigned_department_id IS NULL
         AND assigned_membership_id IS NULL
    `;

    if (afetadas === 0) return false;

    await registrarEvento(tx, tenantId, {
      conversationId,
      type: "TRIAGED",
      // NULO: foi o cliente. Ver o cabeçalho.
      actorMembershipId: null,
      toDepartmentId: escolha.departmentId,
      // Só código e rótulo. O texto que o cliente digitou fica na mensagem,
      // que já é o lugar dele — copiá-lo para cá criaria um segundo lugar
      // de onde vazar a mesma coisa.
      metadata: { setor: escolha.slug, rotulo: escolha.rotulo },
    });

    return true;
  });

  if (!r) return { aplicada: false };

  await anunciarConversa(tenantId, conversationId, "assignment");

  // A fila da área precisa saber que chegou caso novo. `filaElegivel` já
  // recorta pelo departamento da conversa — que acabou de ser preenchido —,
  // então o aviso vai só para quem é daquele setor.
  avisarSemBloquear({
    tipo: "ATENDIMENTO_NOVO",
    tenantId,
    conversationId,
    autorMembershipId: null,
  });

  return { aplicada: true };
}
