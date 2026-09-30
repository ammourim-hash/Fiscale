/**
 * As chaves VAPID, lidas do ambiente uma vez.
 *
 * Fica separado de `webpush.ts` de proposito: aquele arquivo e cifra pura
 * e da para testar com um par gerado na hora, sem ambiente nenhum. Este
 * aqui e o unico ponto do projeto que toca na chave privada.
 *
 * Sem chaves configuradas o push simplesmente NAO acontece — nada quebra,
 * nada e lancado, e `estadoDoPush()` diz o motivo para a tela de
 * configuracoes poder explica-lo a quem esta olhando. Um sistema de
 * notificacao que derruba o envio de mensagem porque falta uma variavel de
 * ambiente inverteu as prioridades.
 */
import { env } from "@/env";

export interface ChavesVapid {
  publicKey: string;
  privateKey: string;
  subject: string;
}

export type EstadoPush =
  | { disponivel: true; publicKey: string }
  | { disponivel: false; motivo: "DESLIGADO" | "SEM_CHAVES" };

/** 65 bytes crus em base64url dao 87 caracteres. */
const TAMANHO_CHAVE_PUBLICA = 87;

export function estadoDoPush(): EstadoPush {
  if (!env.ELO_PUSH_ENABLED) return { disponivel: false, motivo: "DESLIGADO" };
  if (
    env.ELO_VAPID_PUBLIC_KEY.length !== TAMANHO_CHAVE_PUBLICA ||
    env.ELO_VAPID_PRIVATE_KEY.length === 0
  ) {
    return { disponivel: false, motivo: "SEM_CHAVES" };
  }
  return { disponivel: true, publicKey: env.ELO_VAPID_PUBLIC_KEY };
}

/**
 * As chaves para assinar. `null` quando o push nao esta disponivel.
 *
 * NUNCA devolver isto por uma rota: o objeto carrega a chave privada.
 */
export function chavesVapid(): ChavesVapid | null {
  const estado = estadoDoPush();
  if (!estado.disponivel) return null;
  return {
    publicKey: env.ELO_VAPID_PUBLIC_KEY,
    privateKey: env.ELO_VAPID_PRIVATE_KEY,
    subject: env.ELO_VAPID_SUBJECT,
  };
}
