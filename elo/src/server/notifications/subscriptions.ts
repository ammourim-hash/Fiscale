/**
 * Inscricoes de Web Push — um APARELHO cada.
 *
 * ---------------------------------------------------------------------
 *  Varios dispositivos, e revogar um nao toca nos outros
 * ---------------------------------------------------------------------
 *  Notebook do escritorio, computador de casa, celular. Cada navegador
 *  produz o seu proprio endpoint, entao sao tres linhas independentes.
 *  "Desativar neste dispositivo" revoga UMA — e e por isso que a tela nao
 *  precisa de uma central de aparelhos para ser util.
 *
 * ---------------------------------------------------------------------
 *  O endpoint e um segredo, e por isso ele nao sai daqui
 * ---------------------------------------------------------------------
 *  Quem tem o endpoint mais `p256dh` e `auth` entrega notificacao naquele
 *  aparelho. Nada nesta camada devolve o endpoint: a tela recebe o
 *  `endpointHash`, que serve para o navegador reconhecer "este dispositivo
 *  sou eu" e para mais nada. A auditoria tambem so ve o hash.
 *
 * ---------------------------------------------------------------------
 *  O aparelho pode trocar de dono
 * ---------------------------------------------------------------------
 *  O endpoint pertence ao NAVEGADOR, nao a pessoa. Um computador
 *  compartilhado, com um perfil do Chrome so, produz o mesmo endpoint para
 *  quem quer que entre nele.
 *
 *  Dentro do tenant, quem resolve e a UNIQUE de (tenant, endpoint): a
 *  reinscricao vira UPDATE e o `membershipId` passa a ser de quem esta
 *  entrando agora. Uma linha, um dono.
 *
 *  Entre tenants, quem resolve e o NAVEGADOR, com `unsubscribe()` — que
 *  mata o endpoint no provedor e faz a linha antiga receber 410 e ser
 *  revogada sozinha. O porque de nao ser resolvido aqui esta escrito na
 *  migration 20260810230000_notifications: exigiria BYPASSRLS.
 */
import { createHash } from "node:crypto";

import { audit } from "@/server/auth/audit";
import { requirePermission, type AuthContext } from "@/server/auth/context";
import { logger } from "@/server/logging/logger";
import { withTenant } from "@/server/tenancy";

export function hashDoEndpoint(endpoint: string): string {
  return createHash("sha256").update(endpoint).digest("hex");
}

export interface NovaInscricao {
  endpoint: string;
  p256dh: string;
  auth: string;
  /** O User-Agent cru. Vira rotulo aqui e NAO e guardado inteiro. */
  userAgent?: string | null;
}

export type FalhaInscricao = "ENDPOINT_INVALIDO" | "CHAVES_INVALIDAS";

export type ResultadoInscricao<T> =
  | { ok: true; value: T }
  | { ok: false; code: FalhaInscricao; message: string };

/**
 * Aparelho para a tela: "Chrome no Windows".
 *
 * Guardar o User-Agent inteiro seria dado pessoal a mais para uma frase de
 * quatro palavras — e o UA identifica versao de sistema, arquitetura e as
 * vezes o modelo do aparelho.
 */
export function rotuloDoAparelho(ua: string | null | undefined): string | null {
  if (!ua) return null;

  const navegador = /Edg\//.test(ua)
    ? "Edge"
    : /OPR\/|Opera/.test(ua)
      ? "Opera"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Chrome\//.test(ua)
          ? "Chrome"
          : /Safari\//.test(ua)
            ? "Safari"
            : null;

  const sistema = /Windows/.test(ua)
    ? "Windows"
    : /Android/.test(ua)
      ? "Android"
      : /iPhone|iPad|iOS/.test(ua)
        ? "iOS"
        : /Mac OS X|Macintosh/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : null;

  if (navegador && sistema) return `${navegador} no ${sistema}`;
  return navegador ?? sistema;
}

/** Base64url de 65 bytes = 87 caracteres; de 16 bytes = 22. */
const TAMANHO_P256DH = 87;
const TAMANHO_AUTH = 22;
const BASE64URL = /^[A-Za-z0-9_-]+$/;

export interface InscricaoResumo {
  id: string;
  endpointHash: string;
  deviceLabel: string | null;
  createdAt: Date;
  lastUsedAt: Date | null;
}

export async function inscrever(
  ctx: AuthContext,
  dados: NovaInscricao,
): Promise<ResultadoInscricao<InscricaoResumo>> {
  requirePermission(ctx, "notifications.manage_self");

  // Endpoint precisa ser HTTPS: e URL de provedor externo, e um `http://`
  // aqui significa ou engano ou tentativa de nos fazer bater numa maquina
  // da rede interna.
  let url: URL;
  try {
    url = new URL(dados.endpoint);
  } catch {
    return { ok: false, code: "ENDPOINT_INVALIDO", message: "Endereço de inscrição inválido." };
  }
  if (url.protocol !== "https:") {
    return { ok: false, code: "ENDPOINT_INVALIDO", message: "Endereço de inscrição inválido." };
  }

  if (
    dados.p256dh.length !== TAMANHO_P256DH ||
    !BASE64URL.test(dados.p256dh) ||
    dados.auth.length !== TAMANHO_AUTH ||
    !BASE64URL.test(dados.auth)
  ) {
    return {
      ok: false,
      code: "CHAVES_INVALIDAS",
      message: "As chaves de notificação deste navegador não foram aceitas.",
    };
  }

  const endpointHash = hashDoEndpoint(dados.endpoint);
  const deviceLabel = rotuloDoAparelho(dados.userAgent);

  let anterior: string | null = null;

  const resultado = await withTenant(ctx.tenantId, async (tx) => {
    // Quem era o dono antes — para o log distinguir "reinscricao" de
    // "outra pessoa entrou nesta maquina".
    anterior =
      (
        await tx.pushSubscription.findUnique({
          where: { tenantId_endpointHash: { tenantId: ctx.tenantId, endpointHash } },
          select: { membershipId: true },
        })
      )?.membershipId ?? null;

    // Reinscrever o MESMO navegador atualiza a linha. O navegador troca as
    // chaves sozinho de tempos em tempos e reenvia o mesmo endpoint; criar
    // linha nova a cada vez daria dez "aparelhos" para uma maquina so, e
    // nove deles entregando erro.
    const linha = await tx.pushSubscription.upsert({
      where: { tenantId_endpointHash: { tenantId: ctx.tenantId, endpointHash } },
      create: {
        tenantId: ctx.tenantId,
        membershipId: ctx.membershipId,
        endpoint: dados.endpoint,
        endpointHash,
        p256dh: dados.p256dh,
        auth: dados.auth,
        deviceLabel,
      },
      update: {
        // O membership entra no update de proposito: o mesmo navegador
        // pode estar sendo usado por outra pessoa do MESMO escritorio.
        membershipId: ctx.membershipId,
        endpoint: dados.endpoint,
        p256dh: dados.p256dh,
        auth: dados.auth,
        deviceLabel,
        revokedAt: null,
        revokedReason: null,
        failureCount: 0,
      },
      select: {
        id: true,
        endpointHash: true,
        deviceLabel: true,
        createdAt: true,
        lastUsedAt: true,
        membershipId: true,
      },
    });

    return linha;
  });

  if (anterior && anterior !== ctx.membershipId) {
    // Trocou de dono dentro do escritorio. Sem hash e sem endpoint no log:
    // o que interessa e QUE houve a troca.
    logger.info("push.endpoint.novo_dono", { tenantId: ctx.tenantId });
  }

  await audit({
    tenantId: ctx.tenantId,
    membershipId: ctx.membershipId,
    action: "PUSH_SUBSCRIBED",
    targetType: "push_subscription",
    targetId: resultado.id,
    // NUNCA o endpoint nem as chaves. O rotulo do aparelho e o que uma
    // pessoa lendo a trilha precisa para reconhecer o que aconteceu.
    metadata: { dispositivo: resultado.deviceLabel ?? "desconhecido" },
  });

  const { membershipId: _ignorado, ...resumo } = resultado;
  return { ok: true, value: resumo };
}

/**
 * "Desativar neste dispositivo".
 *
 * Recebe o HASH, e nao o endpoint: a tela nunca teve o endpoint. E revoga
 * apenas dentro do proprio tenant e do proprio membership — ninguem
 * desliga o aviso de um colega.
 */
export async function revogar(
  ctx: AuthContext,
  endpointHash: string,
  motivo: "USER" | "EXPIRED" = "USER",
): Promise<{ revogadas: number }> {
  requirePermission(ctx, "notifications.manage_self");

  const revogadas = await withTenant(ctx.tenantId, async (tx) =>
    tx.pushSubscription.updateMany({
      where: { endpointHash, membershipId: ctx.membershipId, revokedAt: null },
      data: { revokedAt: new Date(), revokedReason: motivo },
    }),
  );

  if (revogadas.count > 0) {
    await audit({
      tenantId: ctx.tenantId,
      membershipId: ctx.membershipId,
      action: "PUSH_UNSUBSCRIBED",
      targetType: "push_subscription",
      metadata: { motivo, quantidade: revogadas.count },
    });
  }

  return { revogadas: revogadas.count };
}

/** Os aparelhos ativos DESTA pessoa. Sem endpoint, sem chaves. */
export async function meusDispositivos(ctx: AuthContext): Promise<InscricaoResumo[]> {
  requirePermission(ctx, "notifications.manage_self");

  return withTenant(ctx.tenantId, async (tx) =>
    tx.pushSubscription.findMany({
      where: { membershipId: ctx.membershipId, revokedAt: null },
      select: {
        id: true,
        endpointHash: true,
        deviceLabel: true,
        createdAt: true,
        lastUsedAt: true,
      },
      orderBy: { createdAt: "desc" },
    }),
  );
}
