/**
 * Etiquetas.
 *
 * São dados do escritório, não constantes do código: "Urgente",
 * "Documento pendente" e "Cobrança" mudam de escritório para escritório, e
 * escrevê-las aqui obrigaria a alterar o sistema para renomear uma.
 *
 * Aplicar etiqueta é trabalho de quem atende (`tags.read`). Criar e apagar
 * a lista do escritório é de quem coordena (`tags.manage`) — sem isso, a
 * carteira acaba com trinta variações de "urgente" e nenhuma serve para
 * filtrar.
 */
import { audit } from "@/server/auth/audit";
import { requirePermission, type AuthContext } from "@/server/auth/context";
import { withTenant } from "@/server/tenancy";

export interface Etiqueta {
  id: string;
  slug: string;
  name: string;
  tone: string;
}

const TONS = new Set(["neutro", "acao", "ok", "atencao", "erro"]);

export async function listarEtiquetas(ctx: AuthContext): Promise<Etiqueta[]> {
  requirePermission(ctx, "tags.read");

  return withTenant(ctx.tenantId, async (tx) =>
    tx.tag.findMany({
      select: { id: true, slug: true, name: true, tone: true },
      orderBy: { name: "asc" },
    }),
  );
}

export async function criarEtiqueta(
  ctx: AuthContext,
  dados: { slug: string; name: string; tone?: string },
): Promise<Etiqueta | null> {
  requirePermission(ctx, "tags.manage");

  const slug = dados.slug.trim().toLowerCase();
  const name = dados.name.trim();
  const tone = dados.tone && TONS.has(dados.tone) ? dados.tone : "neutro";

  if (!/^[a-z0-9][a-z0-9-]{1,39}$/.test(slug) || name.length < 2 || name.length > 60) {
    return null;
  }

  return withTenant(ctx.tenantId, async (tx) =>
    tx.tag.create({
      data: { tenantId: ctx.tenantId, slug, name, tone },
      select: { id: true, slug: true, name: true, tone: true },
    }),
  );
}

export type ResultadoEtiqueta = "APLICADA" | "REMOVIDA" | "SEM_MUDANCA" | "NAO_ENCONTRADO";

/**
 * Aplica ou remove uma etiqueta do atendimento.
 *
 * A etiqueta precisa ser do mesmo tenant — e é o RLS que garante isso:
 * `tag.findUnique` de outro escritório simplesmente não encontra, e a
 * operação para aqui.
 */
export async function alternarEtiqueta(
  ctx: AuthContext,
  conversationId: string,
  tagId: string,
  aplicar: boolean,
): Promise<ResultadoEtiqueta> {
  requirePermission(ctx, "tags.read");

  const r = await withTenant(ctx.tenantId, async (tx) => {
    const [conversa, etiqueta] = await Promise.all([
      tx.conversation.findUnique({ where: { id: conversationId }, select: { id: true } }),
      tx.tag.findUnique({ where: { id: tagId }, select: { id: true, name: true } }),
    ]);
    if (!conversa || !etiqueta) return "NAO_ENCONTRADO" as const;

    const existente = await tx.conversationTag.findUnique({
      where: { conversationId_tagId: { conversationId, tagId } },
      select: { id: true },
    });

    if (aplicar && existente) return "SEM_MUDANCA" as const;
    if (!aplicar && !existente) return "SEM_MUDANCA" as const;

    if (aplicar) {
      await tx.conversationTag.create({
        data: {
          tenantId: ctx.tenantId,
          conversationId,
          tagId,
          addedByMembershipId: ctx.membershipId,
        },
      });
    } else {
      await tx.conversationTag.delete({ where: { id: existente!.id } });
    }

    await tx.conversationEvent.create({
      data: {
        tenantId: ctx.tenantId,
        conversationId,
        type: aplicar ? "TAG_ADDED" : "TAG_REMOVED",
        actorMembershipId: ctx.membershipId,
        metadata: { tag: etiqueta.name },
      },
    });

    await tx.conversation.update({
      where: { id: conversationId },
      data: { lastActivityAt: new Date() },
    });

    return aplicar ? ("APLICADA" as const) : ("REMOVIDA" as const);
  });

  if (r === "APLICADA" || r === "REMOVIDA") {
    await audit({
      tenantId: ctx.tenantId,
      membershipId: ctx.membershipId,
      action: "TAG_CHANGED",
      targetType: "conversation",
      targetId: conversationId,
      metadata: { acao: r },
    });
  }

  return r;
}
