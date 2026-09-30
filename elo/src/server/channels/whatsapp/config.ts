/**
 * A configuração do canal WhatsApp — e o mapa de número para escritório.
 *
 * ---------------------------------------------------------------------
 *  O tenant vem DAQUI, e não do webhook
 * ---------------------------------------------------------------------
 *  O payload da Meta chega com `phone_number_id` e sem nenhuma noção de
 *  quem somos nós. Se o tenant viesse do corpo, bastaria mandar um JSON
 *  com o tenant de outro escritório para escrever na conversa dele.
 *
 *  É a mesma regra da integração de clientes (S19), e aqui há um motivo
 *  técnico a mais: para LER o dono numa tabela seria preciso abrir o
 *  contexto de RLS, que exige justamente o tenant que se está tentando
 *  descobrir. É a circularidade de S12, e a saída é a mesma — a
 *  configuração é a autoridade, não o dado que chegou pela rede.
 *
 * ---------------------------------------------------------------------
 *  Por que configuração, e não tabela
 * ---------------------------------------------------------------------
 *  Um número de teste, um escritório. Uma tabela `Channel` teria hoje uma
 *  linha copiada do ambiente e nenhum leitor de verdade — "tabela sem
 *  leitor é dívida, não progresso" (S6).
 *
 *  Quando houver vários números (Embedded Signup, fase 1.8.4), isto vira
 *  tabela — e a circularidade acima volta a ser um problema real a
 *  resolver. Está registrado como débito.
 */
import { env } from "@/env";

export interface NumeroConfigurado {
  phoneNumberId: string;
  tenantId: string;
}

export type EstadoWhatsApp =
  | { pronto: true }
  | { pronto: false; motivo: "DESLIGADO" | "SEM_SEGREDOS" | "SEM_NUMERO" };

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

interface Cache {
  mapa: Map<string, NumeroConfigurado>;
  origem: string;
}

let cache: Cache | null = null;

/**
 * O mapa `phone_number_id → tenant`, lido uma vez.
 *
 * A chave do cache é o texto CRU da variável: em teste ela muda entre
 * casos, e um cache que não percebe isso faz o segundo teste medir a
 * configuração do primeiro.
 */
function mapa(): Map<string, NumeroConfigurado> {
  const cru = process.env.ELO_WHATSAPP_NUMBERS ?? env.ELO_WHATSAPP_NUMBERS;
  if (cache && cache.origem === cru) return cache.mapa;

  const novo = new Map<string, NumeroConfigurado>();

  try {
    const bruto = JSON.parse(cru) as Record<string, unknown>;
    for (const [phoneNumberId, valor] of Object.entries(bruto)) {
      if (typeof valor !== "object" || valor === null) continue;
      const tenantId = (valor as { tenantId?: unknown }).tenantId;
      // Um tenant que não é uuid não abre contexto de RLS — recusar aqui
      // é falhar na partida, e não às 3h da manhã com "tenant inválido".
      if (typeof tenantId !== "string" || !UUID.test(tenantId)) continue;
      novo.set(phoneNumberId, { phoneNumberId, tenantId });
    }
  } catch {
    // Ambiente malformado: nenhum número configurado. O webhook responde
    // 200 e ignora — ver a rota, e o porquê de não responder erro.
  }

  cache = { mapa: novo, origem: cru };
  return novo;
}

/** Apenas para teste: o ambiente muda entre casos. */
export function limparCacheDeNumeros(): void {
  cache = null;
}

/** O escritório dono deste número, ou `null` se não for um número nosso. */
export function tenantDoNumero(phoneNumberId: string): string | null {
  return mapa().get(phoneNumberId)?.tenantId ?? null;
}

export function numerosConfigurados(): NumeroConfigurado[] {
  return [...mapa().values()];
}

export function appSecret(): string {
  return process.env.ELO_WHATSAPP_APP_SECRET ?? env.ELO_WHATSAPP_APP_SECRET;
}

export function verifyToken(): string {
  return process.env.ELO_WHATSAPP_VERIFY_TOKEN ?? env.ELO_WHATSAPP_VERIFY_TOKEN;
}

export function accessToken(): string {
  return process.env.ELO_WHATSAPP_ACCESS_TOKEN ?? env.ELO_WHATSAPP_ACCESS_TOKEN;
}

export function versaoApi(): string {
  return process.env.ELO_WHATSAPP_API_VERSION ?? env.ELO_WHATSAPP_API_VERSION;
}

function ligado(): boolean {
  const cru = process.env.ELO_WHATSAPP_ENABLED;
  return cru === undefined ? env.ELO_WHATSAPP_ENABLED : cru === "true";
}

/**
 * Dá para ENVIAR?
 *
 * Receber é outra pergunta: o webhook funciona com App Secret e verify
 * token, sem access token. Separar as duas permite ligar o recebimento e
 * observar o tráfego antes de responder qualquer coisa a um cliente.
 */
export function estadoDoEnvio(): EstadoWhatsApp {
  if (!ligado()) return { pronto: false, motivo: "DESLIGADO" };
  if (accessToken().length === 0) return { pronto: false, motivo: "SEM_SEGREDOS" };
  if (mapa().size === 0) return { pronto: false, motivo: "SEM_NUMERO" };
  return { pronto: true };
}

/** Dá para RECEBER? */
export function estadoDoRecebimento(): EstadoWhatsApp {
  if (appSecret().length === 0 || verifyToken().length === 0) {
    return { pronto: false, motivo: "SEM_SEGREDOS" };
  }
  if (mapa().size === 0) return { pronto: false, motivo: "SEM_NUMERO" };
  return { pronto: true };
}
