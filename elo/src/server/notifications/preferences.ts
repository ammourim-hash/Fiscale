/**
 * Preferencias de notificacao — por PESSOA, nunca por escritorio.
 *
 * ---------------------------------------------------------------------
 *  Por que nao existe configuracao global do tenant aqui
 * ---------------------------------------------------------------------
 *  Quem escuta o som e quem le a prévia na tela bloqueada e a pessoa. Uma
 *  chave de tenant que ligasse a prévia para todo mundo entregaria o
 *  assunto do cliente na tela de quem nunca escolheu isso — e uma que a
 *  desligasse tiraria de quem precisa. As duas versoes acabam em alguem
 *  desligando o sistema inteiro.
 *
 *  O escritorio decide QUEM recebe (papel, area, disponibilidade). A
 *  pessoa decide COMO recebe.
 *
 * ---------------------------------------------------------------------
 *  A linha so nasce quando alguem muda alguma coisa
 * ---------------------------------------------------------------------
 *  Sem linha, valem os PADROES. Criar a linha no primeiro login daria uma
 *  tabela cheia de copias do padrao, e mudar o padrao depois nao alcancaria
 *  ninguem. Do jeito escolhido, quem nunca abriu a tela de configuracoes
 *  acompanha o padrao para sempre.
 */
import { requirePermission, type AuthContext } from "@/server/auth/context";
import { withTenant } from "@/server/tenancy";

export interface Preferencias {
  newMessages: boolean;
  newConversations: boolean;
  assignedToMe: boolean;
  transferredToMe: boolean;
  systemNotices: boolean;
  soundEnabled: boolean;
  showPreview: boolean;
  /** Minuto do dia (0..1439). Os dois nulos = "nao perturbe" desligado. */
  quietHoursStart: number | null;
  quietHoursEnd: number | null;
}

/**
 * O padrao de quem nunca configurou nada.
 *
 * Tudo ligado, MENOS a prévia. A assimetria e deliberada: receber o aviso
 * e o que a pessoa espera do produto; mostrar o texto do cliente na tela
 * bloqueada nao e — isso ela precisa pedir.
 */
export const PADRAO: Preferencias = {
  newMessages: true,
  newConversations: true,
  assignedToMe: true,
  transferredToMe: true,
  systemNotices: true,
  soundEnabled: true,
  showPreview: false,
  quietHoursStart: null,
  quietHoursEnd: null,
};

type Tx = Parameters<Parameters<typeof withTenant>[1]>[0];

/** Leitura sem contexto de sessao — e o caminho que o servico de decisao usa. */
export async function preferenciasDe(
  tx: Tx,
  membershipId: string,
): Promise<Preferencias> {
  const linha = await tx.notificationPreference.findUnique({
    where: { membershipId },
    select: {
      newMessages: true,
      newConversations: true,
      assignedToMe: true,
      transferredToMe: true,
      systemNotices: true,
      soundEnabled: true,
      showPreview: true,
      quietHoursStart: true,
      quietHoursEnd: true,
    },
  });
  return linha ?? PADRAO;
}

/** Varias de uma vez. A decisao de notificar consulta N pessoas por evento. */
export async function preferenciasDeVarios(
  tx: Tx,
  membershipIds: string[],
): Promise<Map<string, Preferencias>> {
  const mapa = new Map<string, Preferencias>();
  if (membershipIds.length === 0) return mapa;

  const linhas = await tx.notificationPreference.findMany({
    where: { membershipId: { in: membershipIds } },
  });
  for (const l of linhas) {
    mapa.set(l.membershipId, {
      newMessages: l.newMessages,
      newConversations: l.newConversations,
      assignedToMe: l.assignedToMe,
      transferredToMe: l.transferredToMe,
      systemNotices: l.systemNotices,
      soundEnabled: l.soundEnabled,
      showPreview: l.showPreview,
      quietHoursStart: l.quietHoursStart,
      quietHoursEnd: l.quietHoursEnd,
    });
  }
  // Quem nao tem linha usa o padrao — e nao "nao recebe nada".
  for (const id of membershipIds) if (!mapa.has(id)) mapa.set(id, PADRAO);
  return mapa;
}

export async function minhasPreferencias(ctx: AuthContext): Promise<Preferencias> {
  requirePermission(ctx, "notifications.manage_self");
  return withTenant(ctx.tenantId, (tx) => preferenciasDe(tx, ctx.membershipId));
}

export type Alteracao = Partial<Preferencias>;

const CAMPOS_BOOLEANOS = [
  "newMessages",
  "newConversations",
  "assignedToMe",
  "transferredToMe",
  "systemNotices",
  "soundEnabled",
  "showPreview",
] as const;

/**
 * Valida o que chegou do navegador.
 *
 * Campo desconhecido e RECUSADO, nao ignorado — a mesma regra da projecao
 * de clientes (S18). Ignorar em silencio faz um erro de digitacao virar
 * "salvei e nao mudou nada", que e o defeito mais irritante que uma tela
 * de configuracoes pode ter.
 */
export type FalhaPreferencia = "CAMPO_DESCONHECIDO" | "VALOR_INVALIDO" | "HORARIO_INCOMPLETO";

export function validar(
  entrada: unknown,
): { ok: true; value: Alteracao } | { ok: false; code: FalhaPreferencia; message: string } {
  if (typeof entrada !== "object" || entrada === null || Array.isArray(entrada)) {
    return { ok: false, code: "VALOR_INVALIDO", message: "Formato inválido." };
  }

  const bruto = entrada as Record<string, unknown>;
  const saida: Alteracao = {};

  for (const [chave, valor] of Object.entries(bruto)) {
    if ((CAMPOS_BOOLEANOS as readonly string[]).includes(chave)) {
      if (typeof valor !== "boolean") {
        return { ok: false, code: "VALOR_INVALIDO", message: `"${chave}" precisa ser sim ou não.` };
      }
      saida[chave as (typeof CAMPOS_BOOLEANOS)[number]] = valor;
      continue;
    }

    if (chave === "quietHoursStart" || chave === "quietHoursEnd") {
      if (valor === null) {
        saida[chave] = null;
        continue;
      }
      if (typeof valor !== "number" || !Number.isInteger(valor) || valor < 0 || valor > 1439) {
        return {
          ok: false,
          code: "VALOR_INVALIDO",
          message: "O horário precisa ser um minuto do dia entre 0 e 1439.",
        };
      }
      saida[chave] = valor;
      continue;
    }

    return {
      ok: false,
      code: "CAMPO_DESCONHECIDO",
      message: `Campo desconhecido: "${chave}".`,
    };
  }

  // Meia janela nao e janela. Aceitar so o inicio silenciaria da hora
  // escolhida ate o fim dos tempos, sem que ninguem entendesse por que.
  const temInicio = saida.quietHoursStart !== undefined && saida.quietHoursStart !== null;
  const temFim = saida.quietHoursEnd !== undefined && saida.quietHoursEnd !== null;
  if (temInicio !== temFim) {
    return {
      ok: false,
      code: "HORARIO_INCOMPLETO",
      message: "Informe o início e o fim do horário silencioso juntos.",
    };
  }

  return { ok: true, value: saida };
}

/** Grava. Upsert porque a linha pode nao existir — ver o cabecalho. */
export async function salvarPreferencias(
  ctx: AuthContext,
  alteracao: Alteracao,
): Promise<Preferencias> {
  requirePermission(ctx, "notifications.manage_self");

  return withTenant(ctx.tenantId, async (tx) => {
    const atual = await preferenciasDe(tx, ctx.membershipId);
    const novo: Preferencias = { ...atual, ...alteracao };

    await tx.notificationPreference.upsert({
      where: { membershipId: ctx.membershipId },
      create: { tenantId: ctx.tenantId, membershipId: ctx.membershipId, ...novo },
      update: novo,
    });

    return novo;
  });
}

/* ─── horario silencioso ─────────────────────────────────────────────── */

/**
 * Estamos dentro do "nao perturbe"?
 *
 * A janela pode ATRAVESSAR a meia-noite — 22:00 as 07:00 e o caso comum, e
 * e justamente o que uma comparacao ingenua (`inicio <= agora < fim`)
 * erra: com inicio 1320 e fim 420 ela nunca e verdadeira, e o silencio
 * nunca acontece.
 */
export function emHorarioSilencioso(p: Preferencias, agora: Date): boolean {
  const { quietHoursStart: inicio, quietHoursEnd: fim } = p;
  if (inicio === null || fim === null) return false;
  // Janela de tamanho zero e "desligado", nao "silencie o dia inteiro".
  if (inicio === fim) return false;

  const minuto = agora.getHours() * 60 + agora.getMinutes();
  return inicio < fim
    ? minuto >= inicio && minuto < fim
    : minuto >= inicio || minuto < fim;
}
