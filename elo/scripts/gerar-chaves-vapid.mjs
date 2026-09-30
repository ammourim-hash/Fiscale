/**
 * Gera o par VAPID do Web Push.
 *
 *   npm run vapid
 *
 * Imprime as duas linhas do `.env`. As duas ficam no SERVIDOR — a
 * diferenca e outra:
 *
 *   ELO_VAPID_PUBLIC_KEY   pode ser entregue ao navegador (e o
 *                          `applicationServerKey` da inscricao). Mesmo
 *                          assim sai por rota autenticada, e nao por
 *                          variavel `NEXT_PUBLIC_`.
 *   ELO_VAPID_PRIVATE_KEY  NUNCA sai do servidor. Quem a tem consegue
 *                          assinar avisos em nome do Elo para qualquer
 *                          aparelho inscrito.
 *
 * TROCAR O PAR INVALIDA TODAS AS INSCRICOES. O navegador amarra a
 * inscricao a chave publica com que ela foi criada; com um par novo, os
 * endpoints antigos passam a responder erro e cada pessoa precisa ativar
 * as notificacoes de novo. Gere uma vez e guarde — nao ha rotacao suave
 * aqui, ao contrario do token de troca.
 */
import { generateKeyPairSync } from "node:crypto";

function b64url(b) {
  return b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const par = generateKeyPairSync("ec", { namedCurve: "prime256v1" });

const jwk = par.publicKey.export({ format: "jwk" });
const publicaCrua = Buffer.concat([
  Buffer.from([0x04]),
  Buffer.from(jwk.x.replace(/-/g, "+").replace(/_/g, "/"), "base64"),
  Buffer.from(jwk.y.replace(/-/g, "+").replace(/_/g, "/"), "base64"),
]);

const publica = b64url(publicaCrua);
const privada = par.privateKey.export({ type: "pkcs8", format: "der" }).toString("base64");

console.log("--- ELO — linhas do .env ---\n");
console.log(`ELO_VAPID_PUBLIC_KEY=${publica}`);
console.log(`ELO_VAPID_PRIVATE_KEY=${privada}`);
console.log(`ELO_VAPID_SUBJECT=mailto:contato@seu-dominio.com.br`);
console.log("\nA privada nao entra em repositorio, backup compartilhado nem log.");
