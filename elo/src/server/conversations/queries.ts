/**
 * Leitura de atendimentos: lista, detalhe, histórico e quem visualizou.
 *
 * Os filtros têm significado definido, e vale escrevê-lo — "não lidos"
 * pode querer dizer três coisas diferentes, e escolher errado faz a
 * equipe deixar de confiar no número:
 *
 *   NOVOS       status = NEW. Chegou e ninguém assumiu ainda.
 *   NÃO LIDOS   tem mensagem DO CLIENTE que EU não visualizei. É pessoal,
 *               não do escritório: se a Aline leu e o Carlos não, some da
 *               lista dela e continua na dele.
 *   MEUS        sou o responsável atual.
 *   AGUARDANDO  parado esperando alguém — cliente ou escritório.
 *   TODOS       tudo, inclusive resolvido. Ordenado por atividade, o
 *               resolvido afunda sozinho; e é o único caminho para achar
 *               um caso encerrado sem inventar outra tela.
 *
 * ---------------------------------------------------------------------
 *  "Não lidos" mudou de significado no MVP 1.5
 * ---------------------------------------------------------------------
 *  Antes de existir mensagem, a única leitura possível era "abri o
 *  atendimento". Agora o que importa é a mensagem do cliente: um caso que
 *  eu abri ontem e que recebeu mensagem hoje de manhã está NÃO LIDO, e a
 *  regra antiga o esconderia — que é o pior erro que este filtro pode
 *  cometer.
 *
 *  Sobra um caso: atendimento SEM nenhuma mensagem de entrada (aberto pelo
 *  escritório, ou vindo do seed). Para esse vale a regra antiga — nunca
 *  abri o atendimento —, senão ele não apareceria em não-lidos nunca, e
 *  passaria despercebido justamente por ser silencioso.
 */
import { requirePermission, type AuthContext } from "@/server/auth/context";
import { resumoDeMensagens } from "@/server/messages/queries";
import { withTenant } from "@/server/tenancy";
import type { ConversationStatus } from "@/generated/prisma/enums";

export const FILTROS = [
  "novos",
  "nao-lidos",
  "meus",
  "aguardando",
  "sem-setor",
  "todos",
] as const;
export type Filtro = (typeof FILTROS)[number];

export function ehFiltro(v: string): v is Filtro {
  return (FILTROS as readonly string[]).includes(v);
}

/**
 * O `where` de "não lidos", num lugar só.
 *
 * A lista e o contador PRECISAM usar exatamente esta condição. Duas
 * escritas da mesma regra divergem no primeiro ajuste, e aí o filtro mostra
 * três casos enquanto o crachá diz cinco — momento em que a equipe deixa de
 * confiar nos dois.
 */
export function whereNaoLidos(membershipId: string) {
  return {
    OR: [
      // Tem mensagem do cliente que eu não abri.
      {
        messages: {
          some: {
            direction: "INBOUND" as const,
            deletedAt: null,
            views: { none: { membershipId } },
          },
        },
      },
      // Nenhuma mensagem do cliente ainda: vale "nunca abri o atendimento".
      {
        messages: { none: { direction: "INBOUND" as const } },
        views: { none: { membershipId } },
      },
    ],
  };
}

export interface ResumoAtendimento {
  id: string;
  status: ConversationStatus;
  channel: string;
  version: number;
  createdAt: Date;
  lastActivityAt: Date;
  customer: { id: string; displayName: string } | null;
  assignee: { membershipId: string; name: string } | null;
  department: { id: string; name: string } | null;
  tags: { id: string; name: string; tone: string }[];
  /** Eu já abri o ATENDIMENTO? Não confundir com ter lido as mensagens. */
  viewedByMe: boolean;
  /** Mensagens do cliente que EU não visualizei. Pessoal, calculado. */
  unreadCount: number;
  /** A última mensagem, para a lista dizer do que se trata. */
  preview: { direction: "INBOUND" | "OUTBOUND" | "SYSTEM"; content: string } | null;
}

const SELECT_RESUMO = {
  id: true,
  status: true,
  channel: true,
  version: true,
  createdAt: true,
  lastActivityAt: true,
  assignedMembershipId: true,
  customer: { select: { id: true, displayName: true } },
  assignee: { select: { id: true, identity: { select: { name: true } } } },
  department: { select: { id: true, name: true } },
  tags: { select: { tag: { select: { id: true, name: true, tone: true } } } },
} as const;

export async function listarAtendimentos(
  ctx: AuthContext,
  filtro: Filtro,
  limite = 50,
): Promise<ResumoAtendimento[]> {
  requirePermission(ctx, "conversations.read");

  const take = Math.min(limite, 200);

  const linhas = await withTenant(ctx.tenantId, async (tx) => {
    const where = (() => {
      switch (filtro) {
        case "novos":
          return { status: "NEW" as ConversationStatus };
        case "meus":
          return { assignedMembershipId: ctx.membershipId };
        case "aguardando":
          return {
            status: {
              in: ["WAITING_CUSTOMER", "WAITING_OFFICE"] as ConversationStatus[],
            },
          };
        case "nao-lidos":
          return whereNaoLidos(ctx.membershipId);
        // A fila de triagem. Estado DERIVADO, sem coluna propria: o
        // atendimento chegou, ninguem assumiu e o cliente ainda nao disse
        // de que setor precisa.
        case "sem-setor":
          return WHERE_SEM_SETOR;
        case "todos":
        default:
          return {};
      }
    })();

    return tx.conversation.findMany({
      where,
      select: {
        ...SELECT_RESUMO,
        views: {
          where: { membershipId: ctx.membershipId },
          select: { id: true },
          take: 1,
        },
      },
      orderBy: { lastActivityAt: "desc" },
      take,
    });
  });

  // Fora da transação de propósito: `resumoDeMensagens` abre a sua, e
  // transação dentro de transação pediria uma segunda conexão do pool para
  // cada listagem.
  const resumo = await resumoDeMensagens(ctx, linhas.map((l) => l.id));

  return linhas.map((l) => {
    const r = resumo.get(l.id);
    return {
      id: l.id,
      status: l.status,
      channel: l.channel,
      version: l.version,
      createdAt: l.createdAt,
      lastActivityAt: l.lastActivityAt,
      customer: l.customer,
      assignee: l.assignee
        ? { membershipId: l.assignee.id, name: l.assignee.identity.name }
        : null,
      department: l.department,
      tags: l.tags.map((t) => t.tag),
      viewedByMe: l.views.length > 0,
      unreadCount: r?.unreadCount ?? 0,
      preview: r?.preview
        ? { direction: r.preview.direction, content: r.preview.content }
        : null,
    };
  });
}

/**
 * Só o número de não lidos — o crachá da navegação e o do ícone da PWA.
 *
 * Usa exatamente o `whereNaoLidos` do filtro, e não uma segunda escrita da
 * mesma regra: se o crachá dissesse cinco e o filtro mostrasse três, a
 * equipe pararia de confiar nos dois.
 */
export async function contarNaoLidos(ctx: AuthContext): Promise<number> {
  requirePermission(ctx, "conversations.read");
  return withTenant(ctx.tenantId, async (tx) =>
    tx.conversation.count({ where: whereNaoLidos(ctx.membershipId) }),
  );
}

/** NEW, sem area. O "aguardando setor" da triagem, lido dos dados. */
const WHERE_SEM_SETOR = {
  status: "NEW" as ConversationStatus,
  assignedDepartmentId: null,
} as const;

export interface ContagemPorFiltro {
  novos: number;
  naoLidos: number;
  meus: number;
  aguardando: number;
  semSetor: number;
  todos: number;
}

export async function contarPorFiltro(ctx: AuthContext): Promise<ContagemPorFiltro> {
  requirePermission(ctx, "conversations.read");

  return withTenant(ctx.tenantId, async (tx) => {
    const [novos, naoLidos, meus, aguardando, semSetor, todos] = await Promise.all([
      tx.conversation.count({ where: { status: "NEW" } }),
      tx.conversation.count({ where: whereNaoLidos(ctx.membershipId) }),
      tx.conversation.count({ where: { assignedMembershipId: ctx.membershipId } }),
      tx.conversation.count({
        where: { status: { in: ["WAITING_CUSTOMER", "WAITING_OFFICE"] } },
      }),
      tx.conversation.count({ where: WHERE_SEM_SETOR }),
      tx.conversation.count(),
    ]);
    return { novos, naoLidos, meus, aguardando, semSetor, todos };
  });
}

export interface EventoHistorico {
  id: string;
  type: string;
  createdAt: Date;
  actor: string | null;
  from: string | null;
  to: string | null;
  fromStatus: ConversationStatus | null;
  toStatus: ConversationStatus | null;
  metadata: unknown;
}

export interface DetalheAtendimento extends ResumoAtendimento {
  firstViewedAt: Date | null;
  firstAssignedAt: Date | null;
  firstResponseAt: Date | null;
  resolvedAt: Date | null;
  customerDetail: {
    id: string;
    displayName: string;
    documentDigits: string | null;
    email: string | null;
    active: boolean;
    syncedAt: Date;
    phones: { raw: string; e164: string | null; status: string }[];
  } | null;
  views: { membershipId: string; name: string; department: string | null; firstViewedAt: Date; lastViewedAt: Date; viewCount: number }[];
  events: EventoHistorico[];
}

export async function obterAtendimento(
  ctx: AuthContext,
  id: string,
): Promise<DetalheAtendimento | null> {
  requirePermission(ctx, "conversations.read");

  // A anotação não é decoração: sem ela o `preview: null` do literal
  // estreita o tipo para `null` e o preenchimento lá embaixo não compila.
  const detalhe = await withTenant<DetalheAtendimento | null>(ctx.tenantId, async (tx) => {
    const c = await tx.conversation.findUnique({
      where: { id },
      select: {
        ...SELECT_RESUMO,
        firstViewedAt: true,
        firstAssignedAt: true,
        firstResponseAt: true,
        resolvedAt: true,
        customer: {
          select: {
            id: true,
            displayName: true,
            documentDigits: true,
            email: true,
            active: true,
            syncedAt: true,
            phones: { select: { raw: true, e164: true, status: true } },
          },
        },
        views: {
          select: {
            membershipId: true,
            firstViewedAt: true,
            lastViewedAt: true,
            viewCount: true,
            membership: {
              select: { primaryDepartmentId: true, identity: { select: { name: true } } },
            },
          },
          orderBy: { firstViewedAt: "asc" },
        },
        events: {
          select: {
            id: true,
            type: true,
            createdAt: true,
            actorMembershipId: true,
            fromMembershipId: true,
            toMembershipId: true,
            fromStatus: true,
            toStatus: true,
            metadata: true,
          },
          orderBy: { createdAt: "asc" },
          take: 200,
        },
      },
    });

    if (!c) return null;

    // Um mapa de nomes resolve todos os membros citados no histórico e nas
    // visualizações de uma vez — sem isso seriam N consultas para montar
    // uma linha do tempo de vinte eventos.
    const idsCitados = new Set<string>();
    for (const e of c.events) {
      for (const m of [e.actorMembershipId, e.fromMembershipId, e.toMembershipId]) {
        if (m) idsCitados.add(m);
      }
    }
    const nomes = await nomesDeMembros(tx, [...idsCitados]);
    const areas = await nomesDeDepartamentos(
      tx,
      c.views.map((v) => v.membership.primaryDepartmentId),
    );

    const meuView = c.views.find((v) => v.membershipId === ctx.membershipId);

    return {
      id: c.id,
      status: c.status,
      channel: c.channel,
      version: c.version,
      createdAt: c.createdAt,
      lastActivityAt: c.lastActivityAt,
      firstViewedAt: c.firstViewedAt,
      firstAssignedAt: c.firstAssignedAt,
      firstResponseAt: c.firstResponseAt,
      resolvedAt: c.resolvedAt,
      customer: c.customer ? { id: c.customer.id, displayName: c.customer.displayName } : null,
      customerDetail: c.customer ?? null,
      assignee: c.assignee
        ? { membershipId: c.assignee.id, name: c.assignee.identity.name }
        : null,
      department: c.department,
      tags: c.tags.map((t) => t.tag),
      viewedByMe: Boolean(meuView),
      // Preenchidos abaixo, fora da transação.
      unreadCount: 0,
      preview: null,
      views: c.views.map((v) => ({
        membershipId: v.membershipId,
        name: v.membership.identity.name,
        department: v.membership.primaryDepartmentId
          ? (areas.get(v.membership.primaryDepartmentId) ?? null)
          : null,
        firstViewedAt: v.firstViewedAt,
        lastViewedAt: v.lastViewedAt,
        viewCount: v.viewCount,
      })),
      events: c.events.map((e) => ({
        id: e.id,
        type: e.type,
        createdAt: e.createdAt,
        actor: e.actorMembershipId ? (nomes.get(e.actorMembershipId) ?? null) : null,
        from: e.fromMembershipId ? (nomes.get(e.fromMembershipId) ?? null) : null,
        to: e.toMembershipId ? (nomes.get(e.toMembershipId) ?? null) : null,
        fromStatus: e.fromStatus,
        toStatus: e.toStatus,
        metadata: e.metadata,
      })),
    };
  });

  if (!detalhe) return null;

  const resumo = await resumoDeMensagens(ctx, [detalhe.id]);
  const r = resumo.get(detalhe.id);
  detalhe.unreadCount = r?.unreadCount ?? 0;
  detalhe.preview = r?.preview
    ? { direction: r.preview.direction, content: r.preview.content }
    : null;

  return detalhe;
}

type Tx = Parameters<Parameters<typeof withTenant>[1]>[0];

async function nomesDeMembros(tx: Tx, ids: string[]): Promise<Map<string, string>> {
  if (ids.length === 0) return new Map();
  const ms = await tx.membership.findMany({
    where: { id: { in: ids } },
    select: { id: true, identity: { select: { name: true } } },
  });
  return new Map(ms.map((m) => [m.id, m.identity.name]));
}

async function nomesDeDepartamentos(
  tx: Tx,
  ids: (string | null)[],
): Promise<Map<string, string>> {
  const unicos = [...new Set(ids.filter((i): i is string => Boolean(i)))];
  if (unicos.length === 0) return new Map();
  const ds = await tx.department.findMany({
    where: { id: { in: unicos } },
    select: { id: true, name: true },
  });
  return new Map(ds.map((d) => [d.id, d.name]));
}
