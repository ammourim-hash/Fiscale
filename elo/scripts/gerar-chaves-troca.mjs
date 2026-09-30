/**
 * Gera um par Ed25519 para o token de troca Fiscale -> Elo.
 *
 *   node scripts/gerar-chaves-troca.mjs [kid]
 *
 * Imprime duas coisas, e elas vao para lugares diferentes:
 *
 *   PRIVADA  -> so o Fiscale, em dados/elo_chave_ed25519.json na maquina
 *               do escritorio. Nunca no repositorio, nunca no VPS.
 *   PUBLICA  -> ELO_EXCHANGE_PUBLIC_KEYS no .env do Elo.
 *
 * Na pratica voce nao precisa rodar isto: o Fiscale cria o par sozinho na
 * primeira vez que alguem clica em "Abrir ELO", e mostra a linha pronta
 * para colar. O script existe para gerar chave de teste e para o dia da
 * rotacao.
 *
 * ROTACAO: gere um par com `kid` novo, acrescente a chave publica ao JSON
 * mantendo a antiga, troque a privada no Fiscale, e so depois remova a
 * antiga. Com as duas configuradas ao mesmo tempo, nenhum token em voo se
 * perde na virada.
 */
import { generateKeyPairSync, randomUUID } from "node:crypto";

const kid = process.argv[2] ?? `k${new Date().toISOString().slice(0, 10)}-${randomUUID().slice(0, 4)}`;

const { publicKey, privateKey } = generateKeyPairSync("ed25519");

const publicaBase64 = publicKey.export({ format: "der", type: "spki" }).toString("base64");
const privadaPem = privateKey.export({ format: "pem", type: "pkcs8" }).toString();

console.log(`kid: ${kid}\n`);
console.log("--- FISCALE (privada) — dados/elo_chave_ed25519.json ---");
console.log(JSON.stringify({ kid, privada_pem: privadaPem, publica_b64: publicaBase64 }, null, 2));
console.log("\n--- ELO (publica) — linha do .env ---");
console.log(`ELO_EXCHANGE_PUBLIC_KEYS='${JSON.stringify({ [kid]: publicaBase64 })}'`);
