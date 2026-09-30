/**
 * Web Push — VAPID (RFC 8292) e cifra de payload (RFC 8291).
 *
 * ---------------------------------------------------------------------
 *  Por que escrito a mao, com o `crypto` do proprio Node
 * ---------------------------------------------------------------------
 *  A biblioteca usual (`web-push`) faz exatamente o que esta aqui, e traz
 *  junto um cliente HTTP proprio e a sua arvore de dependencias. O que
 *  este arquivo precisa e ECDH P-256, HKDF-SHA256, AES-128-GCM e uma
 *  assinatura ES256 — as quatro coisas que o Node ja tem nativas, e as
 *  quatro definidas por RFC com vetores publicos.
 *
 *  E o mesmo raciocinio do verificador de JWT do MVP 1.1 (S13): caminho
 *  unico, algoritmo fixo, nada escolhido por dado de fora. Aqui nao ha
 *  negociacao de cifra: `aes128gcm` sempre, `ES256` sempre.
 *
 * ---------------------------------------------------------------------
 *  O que a cifra garante — e o que nao garante
 * ---------------------------------------------------------------------
 *  O provedor de push (Google, Mozilla, Microsoft) e um intermediario que
 *  NAO deve ler o aviso. A cifra do RFC 8291 usa chaves que so o navegador
 *  do funcionario possui (`p256dh` e `auth`), entao o provedor transporta
 *  bytes que nao consegue abrir.
 *
 *  O que ela nao esconde: QUE houve um aviso, QUANDO e para qual aparelho.
 *  Metadado de entrega e do transporte, e nenhuma cifra de payload o
 *  elimina. Por isso o payload tambem e minimo por decisao de produto, e
 *  nao apenas por ser cifrado — ver notifications/payload.ts.
 */
import {
  createCipheriv,
  createHmac,
  createPrivateKey,
  createPublicKey,
  createSign,
  diffieHellman,
  generateKeyPairSync,
  randomBytes,
  type KeyObject,
} from "node:crypto";

/* ─── base64url ──────────────────────────────────────────────────────── */

export function b64url(b: Buffer): string {
  return b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function deB64url(s: string): Buffer {
  return Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");
}

/* ─── chaves P-256 ───────────────────────────────────────────────────── */

/** Ponto nao comprimido: 0x04 || X(32) || Y(32). E o formato que o
 *  navegador entrega em `p256dh` e o que ele espera em `applicationServerKey`. */
export function publicaCrua(chave: KeyObject): Buffer {
  const jwk = chave.export({ format: "jwk" }) as { x?: string; y?: string };
  if (!jwk.x || !jwk.y) throw new Error("chave publica P-256 invalida");
  return Buffer.concat([Buffer.from([0x04]), deB64url(jwk.x), deB64url(jwk.y)]);
}

/** O caminho inverso: 65 bytes crus viram uma chave que o Node sabe usar. */
export function publicaDeCrua(crua: Buffer): KeyObject {
  if (crua.length !== 65 || crua[0] !== 0x04) {
    throw new Error("chave publica precisa ter 65 bytes e comecar com 0x04");
  }
  return createPublicKey({
    key: {
      kty: "EC",
      crv: "P-256",
      x: b64url(crua.subarray(1, 33)),
      y: b64url(crua.subarray(33, 65)),
    },
    format: "jwk",
  });
}

export interface ParVapid {
  /** 65 bytes crus, base64url. Vai para o navegador. */
  publicKey: string;
  /** PKCS#8 DER, base64. NUNCA sai do servidor. */
  privateKey: string;
}

/** Gera um par novo. Usado pelo script `npm run vapid`. */
export function gerarParVapid(): ParVapid {
  const par = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  return {
    publicKey: b64url(publicaCrua(par.publicKey)),
    privateKey: par.privateKey.export({ type: "pkcs8", format: "der" }).toString("base64"),
  };
}

export function privadaDeEnv(pkcs8Base64: string): KeyObject {
  return createPrivateKey({
    key: Buffer.from(pkcs8Base64, "base64"),
    format: "der",
    type: "pkcs8",
  });
}

/* ─── VAPID (RFC 8292) ───────────────────────────────────────────────── */

/** Doze horas. A RFC permite ate 24; metade disso reduz a janela de reuso
 *  de um token que porventura vaze num log de proxy alheio. */
const VIDA_JWT_SEGUNDOS = 12 * 60 * 60;

/**
 * `Authorization: vapid t=<jwt>, k=<chave publica>`.
 *
 * O `aud` e a ORIGEM do endpoint, nunca a URL inteira: a URL contem o
 * identificador do aparelho, e ele nao precisa aparecer dentro de um token
 * que o provedor guarda.
 */
export function cabecalhoVapid(
  endpoint: string,
  chaves: { publicKey: string; privateKey: string; subject: string },
  agora = new Date(),
): string {
  const aud = new URL(endpoint).origin;

  const cabecalho = { typ: "JWT", alg: "ES256" };
  const corpo = {
    aud,
    exp: Math.floor(agora.getTime() / 1000) + VIDA_JWT_SEGUNDOS,
    sub: chaves.subject,
  };

  const naoAssinado =
    `${b64url(Buffer.from(JSON.stringify(cabecalho)))}.` +
    `${b64url(Buffer.from(JSON.stringify(corpo)))}`;

  // `ieee-p1363` = r||s cru, 64 bytes. O padrao do Node e DER, que o
  // provedor de push recusa — e recusa com 401, que parece problema de
  // chave e leva horas para descobrir.
  const assinatura = createSign("SHA256")
    .update(naoAssinado)
    .sign({ key: privadaDeEnv(chaves.privateKey), dsaEncoding: "ieee-p1363" });

  return `vapid t=${naoAssinado}.${b64url(assinatura)}, k=${chaves.publicKey}`;
}

/* ─── cifra do payload (RFC 8291, content-encoding aes128gcm) ────────── */

function hmac(chave: Buffer, dado: Buffer): Buffer {
  return createHmac("sha256", chave).update(dado).digest();
}

/** HKDF-Expand com um bloco so — que e tudo de que o RFC 8291 precisa. */
function derivar(prk: Buffer, info: Buffer, tamanho: number): Buffer {
  return hmac(prk, Buffer.concat([info, Buffer.from([0x01])])).subarray(0, tamanho);
}

/** Tamanho de registro. Um registro so: o aviso tem dezenas de bytes. */
const TAMANHO_REGISTRO = 4096;
export const PAYLOAD_MAXIMO = TAMANHO_REGISTRO - 17;

export interface ChavesDoNavegador {
  /** Chave publica P-256 do navegador, base64url, 65 bytes crus. */
  p256dh: string;
  /** Segredo de autenticacao do navegador, base64url, 16 bytes. */
  auth: string;
}

/**
 * Cifra o payload no formato que o navegador sabe abrir.
 *
 * Corpo final:
 *
 *     salt(16) | rs(4) | idlen(1)=65 | chave efemera(65) | cifra
 *
 * A chave efemera do servidor e NOVA a cada envio. Reaproveita-la faria
 * duas notificacoes ao mesmo aparelho compartilharem a mesma chave de
 * conteudo — que e o erro que transforma cifra em enfeite.
 */
export function cifrarPayload(texto: string, navegador: ChavesDoNavegador): Buffer {
  const claro = Buffer.from(texto, "utf8");
  if (claro.length > PAYLOAD_MAXIMO) {
    throw new Error(`payload de push acima de ${PAYLOAD_MAXIMO} bytes`);
  }

  const salt = randomBytes(16);
  const efemero = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const efemeroPublico = publicaCrua(efemero.publicKey);

  const uaPublicoCru = deB64url(navegador.p256dh);
  const uaPublico = publicaDeCrua(uaPublicoCru);
  const authSecret = deB64url(navegador.auth);

  const compartilhado = diffieHellman({
    privateKey: efemero.privateKey,
    publicKey: uaPublico,
  });

  // A "combinacao" do RFC 8291: o segredo ECDH e o `auth` do navegador
  // entram juntos, e as duas chaves publicas entram no `info`. E isso que
  // amarra o material derivado A ESTE par de aparelho e servidor.
  const prkCombinado = hmac(authSecret, compartilhado);
  const infoChave = Buffer.concat([
    Buffer.from("WebPush: info\0", "utf8"),
    uaPublicoCru,
    efemeroPublico,
  ]);
  const ikm = derivar(prkCombinado, infoChave, 32);

  const prk = hmac(salt, ikm);
  const cek = derivar(prk, Buffer.from("Content-Encoding: aes128gcm\0", "utf8"), 16);
  const nonce = derivar(prk, Buffer.from("Content-Encoding: nonce\0", "utf8"), 12);

  // 0x02 e o delimitador de ULTIMO registro. Sem ele o navegador espera
  // mais dados e descarta a mensagem em silencio.
  const registro = Buffer.concat([claro, Buffer.from([0x02])]);

  const cifrador = createCipheriv("aes-128-gcm", cek, nonce);
  const cifra = Buffer.concat([
    cifrador.update(registro),
    cifrador.final(),
    cifrador.getAuthTag(),
  ]);

  const rs = Buffer.alloc(4);
  rs.writeUInt32BE(TAMANHO_REGISTRO, 0);

  return Buffer.concat([salt, rs, Buffer.from([efemeroPublico.length]), efemeroPublico, cifra]);
}

/* ─── envio ──────────────────────────────────────────────────────────── */

export type ResultadoEnvio =
  /** O provedor aceitou. Nao significa que a pessoa viu. */
  | { tipo: "ENTREGUE"; status: number }
  /** 404/410: a inscricao morreu. Revogar e parar de tentar. */
  | { tipo: "EXPIRADA"; status: number }
  /** 429 ou 5xx: problema do provedor. Tentar de novo faz sentido. */
  | { tipo: "TEMPORARIA"; status: number }
  /** 400/413 e afins: o defeito e nosso, e retry so repete o defeito. */
  | { tipo: "RECUSADA"; status: number }
  /** Nem chegou a haver resposta — rede, DNS, timeout. */
  | { tipo: "SEM_RESPOSTA"; erro: string };

export interface DestinoPush extends ChavesDoNavegador {
  endpoint: string;
}

export interface OpcoesEnvio {
  /** Segundos que o provedor guarda o aviso se o aparelho estiver offline. */
  ttlSegundos?: number;
  /**
   * Chave de agrupamento. Dois avisos com o mesmo `topic` viram UM no
   * aparelho — o segundo substitui o primeiro. E o que impede vinte
   * mensagens seguidas do mesmo cliente virarem vinte notificacoes.
   */
  topic?: string | null;
  agora?: Date;
}

/** Meia hora. Aviso de atendimento envelhece: entregar um "nova mensagem"
 *  de ontem quando o aparelho religa e ruido, nao servico. */
const TTL_PADRAO = 1800;

/** Sem isto, um provedor lento segura a requisicao que originou a mensagem. */
const TIMEOUT_MS = 10_000;

export async function enviarPush(
  destino: DestinoPush,
  payload: string,
  chaves: { publicKey: string; privateKey: string; subject: string },
  opcoes: OpcoesEnvio = {},
): Promise<ResultadoEnvio> {
  let corpo: Buffer;
  try {
    corpo = cifrarPayload(payload, destino);
  } catch {
    // Cifra que falha e defeito NOSSO: chave malformada na linha, payload
    // grande demais. Retry repetiria o mesmo defeito, entao sai como
    // recusa — e o `status: 0` diz que nem houve requisicao.
    return { tipo: "RECUSADA", status: 0 };
  }

  const cabecalhos: Record<string, string> = {
    authorization: cabecalhoVapid(destino.endpoint, chaves, opcoes.agora),
    "content-encoding": "aes128gcm",
    "content-type": "application/octet-stream",
    ttl: String(opcoes.ttlSegundos ?? TTL_PADRAO),
    // `normal` deixa o aparelho agrupar entregas para poupar bateria.
    // `high` existe e nao e usado: nao ha push critico nesta fase, e
    // marcar tudo como urgente e o caminho para o sistema operacional
    // parar de dar atencao ao aplicativo.
    urgency: "normal",
  };
  if (opcoes.topic) cabecalhos.topic = opcoes.topic;

  const abortar = AbortSignal.timeout(TIMEOUT_MS);

  try {
    const r = await fetch(destino.endpoint, {
      method: "POST",
      headers: cabecalhos,
      body: new Uint8Array(corpo),
      signal: abortar,
    });

    if (r.status === 404 || r.status === 410) return { tipo: "EXPIRADA", status: r.status };
    if (r.status === 429 || r.status >= 500) return { tipo: "TEMPORARIA", status: r.status };
    if (r.status >= 400) return { tipo: "RECUSADA", status: r.status };
    return { tipo: "ENTREGUE", status: r.status };
  } catch (erro: unknown) {
    return { tipo: "SEM_RESPOSTA", erro: erro instanceof Error ? erro.name : "erro" };
  }
}
