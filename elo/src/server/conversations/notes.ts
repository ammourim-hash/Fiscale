/**
 * Notas internas.
 *
 * NOTA NUNCA VAI PARA O CLIENTE. Isso é estrutural, e não uma marcação:
 * nota é uma TABELA separada de mensagem. Um campo `interna: boolean`
 * dentro de mensagem deixaria a nota do escritório a um `false` de
 * distância do WhatsApp do cliente — e esse `false` chega um dia, num
 * merge apressado, sem ninguém perceber.
 *
 * Separadas, não existe caminho de código em que uma nota seja enviada.
 */
import { audit } from "@/server/auth/audit";
import { requirePermission, type AuthContext } from "@/server/auth/context";
import { publish } from "@/server/realtime/bus";
import { withTenant } from "@/server/tenancy";

export interface Nota {
  id: string;
  content: string;
  createdAt: Date;
  updatedAt: Date;
  author: { membershipId: string; name: string; department: string | null };
}

const TAMANHO_MAXIMO = 5000;

export async function listarNotas(
  ctx: AuthContext,
  conversationId: string,
): Promise<Nota[]> {
  requirePermission(ctx, "notes.read");

  const linhas = await withTenant(ctx.tenantId, async (tx) =>
    tx.internalNote.findMany({
      where: { conversationId },
      orderBy: { createdAt: "asc" },
      select: {
        id: true,
        content: true,
        createdAt: true,
        updatedAt: true,
        authorMembershipId: true,
        author: {
          select: {
            primaryDepartmentId: true,
            identity: { select: { name: true } },
          },
        },
      },
    }),
  );

  const areas = await nomesDeAreas(
    ctx,
    linhas.map((l) => l.author.primaryDepartmentId),
  );

  return linhas.map((l) => ({
    id: l.id,
    content: l.content,
    createdAt: l.createdAt,
    updatedAt: l.updatedAt,
    author: {
      membershipId: l.authorMembershipId,
      name: l.author.identity.name,
      department: l.author.primaryDepartmentId
        ? (areas.get(l.author.primaryDepartmentId) ?? null)
        : null,
    },
  }));
}

async function nomesDeAreas(
  ctx: AuthContext,
  ids: (string | null)[],
): Promise<Map<string, string>> {
  const unicos = [...new Set(ids.filter((i): i is string => Boolean(i)))];
  if (unicos.length === 0) return new Map();

  const deps = await withTenant(ctx.tenantId, async (tx) =>
    tx.department.findMany({ where: { id: { in: unicos } }, select: { id: true, name: true } }),
  );
  return new Map(deps.map((d) => [d.id, d.name]));
}

export async function criarNota(
  ctx: AuthContext,
  conversationId: string,
  conteudo: string,
): Promise<{ id: string } | null> {
  requirePermission(ctx, "notes.write");

  const texto = conteudo.trim();
  if (texto.length === 0 || texto.length > TAMANHO_MAXIMO) return null;

  const criada = await withTenant(ctx.tenantId, async (tx) => {
    // O RLS já esconde atendimento de outro tenant; não achar aqui é a
    // resposta certa, e não um erro a explicar.
    const conversa = await tx.conversation.findUnique({
      where: { id: conversationId },
      select: { id: true },
    });
    if (!conversa) return null;

    const nota = await tx.internalNote.create({
      data: {
        tenantId: ctx.tenantId,
        conversationId,
        authorMembershipId: ctx.membershipId,
        content: texto,
      },
      select: { id: true },
    });

    await tx.conversationEvent.create({
      data: {
        tenantId: ctx.tenantId,
        conversationId,
        type: "NOTE_CREATED",
        actorMembershipId: ctx.membershipId,
        // O histórico registra QUE houve nota, nunca o texto dela.
        metadata: { noteId: nota.id },
      },
    });

    await tx.conversation.update({
      where: { id: conversationId },
      data: { lastActivityAt: new Date() },
    });

    return nota;
  });

  if (!criada) return null;

  await audit({
    tenantId: ctx.tenantId,
    membershipId: ctx.membershipId,
    action: "INTERNAL_NOTE_CREATED",
    targetType: "conversation",
    targetId: conversationId,
  });

  // A lista sobe porque houve atividade. O evento leva o FATO, nunca o
  // texto: nota interna nao viaja no barramento.
  const estado = await withTenant(ctx.tenantId, async (tx) =>
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
  if (estado) {
    publish(ctx.tenantId, {
      type: "conversation.updated",
      conversationId,
      status: estado.status,
      version: estado.version,
      assignedMembershipId: estado.assignedMembershipId,
      lastActivityAt: estado.lastActivityAt.toISOString(),
      reason: "note",
    });
  }

  return criada;
}
