/**
 * Saída: mensagem do Elo → Cloud API → WhatsApp do cliente.
 *
 * ---------------------------------------------------------------------
 *  A MENSAGEM VEM PRIMEIRO — de novo
 * ---------------------------------------------------------------------
 *  A mesma regra do Web Push (MVP 1.7): a `Message` já está gravada
 *  quando este arquivo é chamado. Se a Meta estiver fora do ar, a
 *  mensagem continua existindo no Elo, com `deliveryStatus = FAILED` e
 *  visível para quem escreveu.
 *
 *  O que NÃO pode acontecer é o funcionário escrever, ver erro, e não
 *  saber se a mensagem existe. Ela existe; o que falhou foi o transporte.
 *
 * ---------------------------------------------------------------------
 *  A assinatura é montada AQUI, na borda
 * ---------------------------------------------------------------------
 *      Aline • Fiscal
 *      Bom dia, Maria! Vou verificar para você.
 *
 *  Dentro do Elo, nome e área são dois campos congelados na mensagem
 *  (S37), e quem os junta é a interface. Se o serviço concatenasse, o
 *  texto formatado iria para o banco e não daria mais para mudar a
 *  apresentação sem reescrever o histórico.
 *
 *  O adaptador é a fronteira: é aqui que os dois campos viram o texto que
 *  o cliente lê — e é por isso que o cliente vê a pessoa sem que exista
 *  perfil de WhatsApp por funcionário (decisão D1).
 */
import { audit } from "@/server/auth/audit";
import { logger } from "@/server/logging/logger";
import { withTenant } from "@/server/tenancy";

import { accessToken, estadoDoEnvio, versaoApi } from "./config";
import { CANAL_WHATSAPP } from "./constantes";

/* ─── classificação da resposta ──────────────────────────────────────── */

export type ResultadoEnvio =
  /** A Meta aceitou. `wamid` é a chave para casar os status depois. */
  | { tipo: "ACEITA"; externalMessageId: string }
  /** 429 ou 5xx: o problema é de lá, e repetir faz sentido. */
  | { tipo: "TEMPORARIA"; status: number }
  /** 4xx de validação, template exigido, telefone inválido: repetir repete o erro. */
  | { tipo: "PERMANENTE"; status: number; codigo: number | null; titulo: string | null }
  /** Nem houve resposta: rede, DNS, timeout. */
  | { tipo: "SEM_RESPOSTA"; erro: string };

/** Dez segundos. Sem teto, uma Meta lenta segura a requisição do usuário. */
const TIMEOUT_MS = 10_000;

/** Três tentativas no total. Ver `entregar`. */
export const TENTATIVAS = 3;

/** Espera entre tentativas: 0,5 s e 1,5 s. Curto — há gente esperando. */
const ESPERA_MS = [500, 1500];

export interface Destino {
  phoneNumberId: string;
  /** `wa_id` — telefone sem "+". */
  para: string;
}

/**
 * Monta o texto que o cliente lê.
 *
 * Sem nome, sai só o corpo — e isso é melhor do que um "• " solto ou um
 * nome inventado. Quem não tem área principal definida assina só com o
 * nome, exatamente como a interface faz (D8).
 */
export function comAssinatura(
  corpo: string,
  nome: string | null,
  area: string | null,
): string {
  if (!nome) return corpo;
  const assinatura = area ? `${nome} • ${area}` : nome;
  return `${assinatura}\n${corpo}`;
}

/* ─── a chamada ──────────────────────────────────────────────────────── */

/** Uma tentativa. Sem retry aqui — quem repete é `entregar`. */
export async function enviarTexto(
  destino: Destino,
  texto: string,
): Promise<ResultadoEnvio> {
  const url = `https://graph.facebook.com/${versaoApi()}/${destino.phoneNumberId}/messages`;

  try {
    const r = await fetch(url, {
      method: "POST",
      headers: {
        authorization: `Bearer ${accessToken()}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        messaging_product: "whatsapp",
        recipient_type: "individual",
        to: destino.para,
        type: "text",
        // `preview_url: false`: link em mensagem de escritório não deve
        // virar cartão com imagem do site alheio sem ninguém pedir.
        text: { body: texto, preview_url: false },
      }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });

    if (r.status === 429 || r.status >= 500) {
      return { tipo: "TEMPORARIA", status: r.status };
    }

    if (!r.ok) {
      const corpo = (await r.json().catch(() => null)) as {
        error?: { code?: number; message?: string; error_subcode?: number };
      } | null;
      return {
        tipo: "PERMANENTE",
        status: r.status,
        codigo: corpo?.error?.code ?? null,
        // A `message` da Meta descreve o defeito ("Invalid parameter"), e
        // não o conteúdo enviado. É o que uma pessoa precisa para
        // entender — e não carrega telefone nem texto do cliente.
        titulo: corpo?.error?.message ?? null,
      };
    }

    const corpo = (await r.json()) as { messages?: { id?: string }[] };
    const id = corpo.messages?.[0]?.id;
    if (!id) {
      // 200 sem `wamid` não deveria acontecer. Se acontecer, tratar como
      // permanente é mais honesto do que repetir e mandar em duplicidade.
      return { tipo: "PERMANENTE", status: r.status, codigo: null, titulo: "resposta sem wamid" };
    }

    return { tipo: "ACEITA", externalMessageId: id };
  } catch (erro: unknown) {
    return { tipo: "SEM_RESPOSTA", erro: erro instanceof Error ? erro.name : "erro" };
  }
}

/* ─── entrega, com retry e gravação do resultado ─────────────────────── */

/**
 * Repete só o que vale repetir.
 *
 * | Situação | Repete? |
 * |---|---|
 * | timeout, rede | sim |
 * | 429 | sim |
 * | 5xx | sim |
 * | 4xx de validação | **não** — o defeito é nosso |
 * | template exigido (fora da janela) | **não** — decisão de gente |
 * | telefone inválido | **não** |
 *
 * Insistir num erro permanente é o que transforma fila em entulho, e faz
 * o número parecer com problema quando o problema é o payload.
 */
export async function entregar(
  tenantId: string,
  messageId: string,
  destino: Destino,
  texto: string,
  esperar: (ms: number) => Promise<void> = dormir,
): Promise<ResultadoEnvio> {
  let ultimo: ResultadoEnvio = { tipo: "SEM_RESPOSTA", erro: "nao_tentado" };

  for (let tentativa = 0; tentativa < TENTATIVAS; tentativa += 1) {
    ultimo = await enviarTexto(destino, texto);

    if (ultimo.tipo === "ACEITA") {
      await marcar(tenantId, messageId, "SENT", ultimo.externalMessageId);
      return ultimo;
    }

    if (ultimo.tipo === "PERMANENTE") break;

    const espera = ESPERA_MS[tentativa];
    if (espera !== undefined) await esperar(espera);
  }

  await marcar(tenantId, messageId, "FAILED", null);

  logger.warn("whatsapp.envio.falhou", {
    tenantId,
    tipo: ultimo.tipo,
    ...(ultimo.tipo === "PERMANENTE"
      ? { status: ultimo.status, codigo: ultimo.codigo }
      : ultimo.tipo === "TEMPORARIA"
        ? { status: ultimo.status }
        : { erro: ultimo.erro }),
  });

  await audit({
    tenantId,
    membershipId: null,
    action: "WHATSAPP_SEND_FAILED",
    targetType: "message",
    targetId: messageId,
    metadata: { tipo: ultimo.tipo },
  });

  return ultimo;
}

async function marcar(
  tenantId: string,
  messageId: string,
  status: "SENT" | "FAILED",
  externalMessageId: string | null,
): Promise<void> {
  await withTenant(tenantId, async (tx) => {
    await tx.message.update({
      where: { id: messageId },
      data: {
        deliveryStatus: status,
        ...(externalMessageId ? { externalMessageId } : {}),
      },
    });
  });
}

function dormir(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/* ─── o ponto de entrada do domínio ──────────────────────────────────── */

/**
 * Despacha, sem bloquear.
 *
 * Chamado depois do COMMIT da mensagem, e sem `await` do lado de quem
 * escreve — a mesma forma do `avisarSemBloquear` do MVP 1.7, e pelo mesmo
 * motivo: o POST de mensagem já fez o que precisava fazer.
 */
const emVoo = new Set<Promise<unknown>>();

export function despacharSemBloquear(
  tenantId: string,
  conversationId: string,
  messageId: string,
  conteudo: string,
  assinatura: { nome: string | null; area: string | null },
): void {
  const promessa = despachar(tenantId, conversationId, messageId, conteudo, assinatura)
    .catch((erro: unknown) => {
      logger.error("whatsapp.despacho.falhou", { tenantId, erro: String(erro) });
    })
    .finally(() => emVoo.delete(promessa));

  emVoo.add(promessa);
}

/** Espera os envios em voo. Só para script e teste — ver o MVP 1.7. */
export async function aguardarEnvios(): Promise<void> {
  while (emVoo.size > 0) await Promise.allSettled([...emVoo]);
}

async function despachar(
  tenantId: string,
  conversationId: string,
  messageId: string,
  conteudo: string,
  assinatura: { nome: string | null; area: string | null },
): Promise<void> {
  const estado = estadoDoEnvio();
  if (!estado.pronto) {
    // Sem credencial não há o que enviar, e isso NÃO é erro: a mensagem
    // fica QUEUED, visível, e o Elo continua sendo o registro interno.
    logger.debug("whatsapp.envio.indisponivel", { motivo: estado.motivo });
    return;
  }

  const conversa = await withTenant(tenantId, async (tx) =>
    tx.conversation.findUnique({
      where: { id: conversationId },
      select: { channel: true, externalContactId: true },
    }),
  );

  // Conversa interna (INTERNAL, DEV) não vai para lugar nenhum.
  if (!conversa || conversa.channel !== CANAL_WHATSAPP || !conversa.externalContactId) return;

  const numeros = (await import("./config")).numerosConfigurados();
  const numero = numeros.find((n) => n.tenantId === tenantId);
  if (!numero) {
    logger.warn("whatsapp.sem_numero_para_tenant", { tenantId });
    return;
  }

  await entregar(
    tenantId,
    messageId,
    { phoneNumberId: numero.phoneNumberId, para: conversa.externalContactId },
    comAssinatura(conteudo, assinatura.nome, assinatura.area),
  );
}
