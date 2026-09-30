/**
 * O nome do canal, num lugar só.
 *
 * `Conversation.channel` e `Message.channel` são texto livre desde o MVP
 * 1.4 — de propósito, para que canal novo não exija migration. O preço de
 * texto livre é que uma letra trocada num literal cria um canal fantasma
 * que ninguém encontra. Uma constante resolve isso sem enum.
 */
export const CANAL_WHATSAPP = "WHATSAPP";
