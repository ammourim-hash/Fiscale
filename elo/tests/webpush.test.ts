/**
 * Web Push: VAPID (RFC 8292) e cifra do payload (RFC 8291).
 *
 * O teste que carrega o arquivo é o de IDA E VOLTA: o payload é cifrado
 * pelo código do Elo e DECIFRADO aqui do jeito que o navegador decifra —
 * ECDH com a chave privada do "navegador", HKDF, AES-128-GCM. Se a cifra
 * estiver errada em qualquer passo, o texto não volta.
 *
 * Isso importa porque um erro de cifra em Web Push é INVISÍVEL na
 * produção: o provedor aceita o POST, responde 201, e o navegador
 * simplesmente descarta o que não consegue abrir. A notificação não chega
 * e não há erro em lugar nenhum. Sem este teste, o defeito só apareceria
 * como "às vezes não avisa".
 */
import {
  createDecipheriv,
  createHmac,
  createVerify,
  diffieHellman,
  generateKeyPairSync,
} from "node:crypto";
import { describe, expect, it } from "vitest";

import {
  b64url,
  cabecalhoVapid,
  cifrarPayload,
  deB64url,
  enviarPush,
  gerarParVapid,
  publicaCrua,
  publicaDeCrua,
  PAYLOAD_MAXIMO,
} from "@/server/push/webpush";

/* ─── um "navegador" de mentira, com criptografia de verdade ─────────── */

function navegadorFalso() {
  const par = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const publicaBytes = publicaCrua(par.publicKey);
  const authSecret = Buffer.from("0123456789abcdef", "utf8");
  return {
    privada: par.privateKey,
    publicaBytes,
    authSecret,
    chaves: { p256dh: b64url(publicaBytes), auth: b64url(authSecret) },
  };
}

const hmac = (k: Buffer, d: Buffer) => createHmac("sha256", k).update(d).digest();
const derivar = (prk: Buffer, info: Buffer, n: number) =>
  hmac(prk, Buffer.concat([info, Buffer.from([1])])).subarray(0, n);

/** Exatamente o que o navegador faz ao receber um push `aes128gcm`. */
function decifrarComoNavegador(
  corpo: Buffer,
  nav: ReturnType<typeof navegadorFalso>,
): string {
  const salt = corpo.subarray(0, 16);
  const idlen = corpo.readUInt8(20);
  const efemero = corpo.subarray(21, 21 + idlen);
  const cifra = corpo.subarray(21 + idlen);

  const compartilhado = diffieHellman({
    privateKey: nav.privada,
    publicKey: publicaDeCrua(efemero),
  });

  const prkComb = hmac(nav.authSecret, compartilhado);
  const ikm = derivar(
    prkComb,
    Buffer.concat([Buffer.from("WebPush: info\0"), nav.publicaBytes, efemero]),
    32,
  );
  const prk = hmac(salt, ikm);
  const cek = derivar(prk, Buffer.from("Content-Encoding: aes128gcm\0"), 16);
  const nonce = derivar(prk, Buffer.from("Content-Encoding: nonce\0"), 12);

  const d = createDecipheriv("aes-128-gcm", cek, nonce);
  d.setAuthTag(cifra.subarray(cifra.length - 16));
  const registro = Buffer.concat([
    d.update(cifra.subarray(0, cifra.length - 16)),
    d.final(),
  ]);

  // O último byte é o delimitador de registro (0x02).
  expect(registro[registro.length - 1]).toBe(0x02);
  return registro.subarray(0, registro.length - 1).toString("utf8");
}

/* ─── cifra ──────────────────────────────────────────────────────────── */

describe("cifra do payload (RFC 8291)", () => {
  it("o navegador consegue decifrar o que o Elo cifrou", () => {
    const nav = navegadorFalso();
    const claro = JSON.stringify({
      t: "message.new",
      c: "9f1c2e30-0000-4000-8000-000000000001",
      title: "ELO",
      body: "Empresa ABC entrou em contato.",
    });

    const corpo = cifrarPayload(claro, nav.chaves);
    expect(decifrarComoNavegador(corpo, nav)).toBe(claro);
  });

  it("o cabeçalho do corpo segue o formato aes128gcm", () => {
    const nav = navegadorFalso();
    const corpo = cifrarPayload("oi", nav.chaves);

    // salt(16) | rs(4) | idlen(1) | chave(65) | cifra
    expect(corpo.readUInt32BE(16)).toBe(4096);
    expect(corpo.readUInt8(20)).toBe(65);
    expect(corpo.subarray(21, 22)[0]).toBe(0x04); // ponto não comprimido
  });

  it("cada envio usa uma chave efêmera nova", () => {
    // Reaproveitar a efêmera faria dois avisos ao mesmo aparelho
    // compartilharem a chave de conteúdo — que é o erro que transforma
    // cifra em enfeite.
    const nav = navegadorFalso();
    const a = cifrarPayload("mesma coisa", nav.chaves);
    const b = cifrarPayload("mesma coisa", nav.chaves);

    expect(a.subarray(21, 86).equals(b.subarray(21, 86))).toBe(false);
    // E o salt também muda, então nem o texto cifrado se repete.
    expect(a.subarray(0, 16).equals(b.subarray(0, 16))).toBe(false);
    expect(a.equals(b)).toBe(false);
  });

  it("chaves de aparelhos diferentes produzem cifras que não se cruzam", () => {
    const alice = navegadorFalso();
    const carlos = navegadorFalso();
    const corpo = cifrarPayload("segredo", alice.chaves);

    expect(() => decifrarComoNavegador(corpo, carlos)).toThrow();
  });

  it("payload acima do teto é recusado, não truncado", () => {
    const nav = navegadorFalso();
    expect(() => cifrarPayload("a".repeat(PAYLOAD_MAXIMO + 1), nav.chaves)).toThrow();
    expect(() => cifrarPayload("a".repeat(PAYLOAD_MAXIMO), nav.chaves)).not.toThrow();
  });

  it("chave pública malformada é recusada", () => {
    expect(() =>
      cifrarPayload("oi", { p256dh: b64url(Buffer.alloc(10)), auth: b64url(Buffer.alloc(16)) }),
    ).toThrow();
  });
});

/* ─── VAPID ──────────────────────────────────────────────────────────── */

/** O corpo do JWT que viaja no cabecalho `Authorization: vapid t=...`. */
function corpoDoJwt(cabecalho: string): unknown {
  const jwt = cabecalho.split("t=")[1]?.split(",")[0] ?? "";
  return JSON.parse(deB64url(jwt.split(".")[1] ?? "").toString());
}

describe("VAPID (RFC 8292)", () => {
  const par = gerarParVapid();
  const chaves = { ...par, subject: "mailto:elo@fiscale.local" };

  it("a chave pública tem 65 bytes crus em base64url", () => {
    expect(par.publicKey).toHaveLength(87);
    const bytes = deB64url(par.publicKey);
    expect(bytes).toHaveLength(65);
    expect(bytes[0]).toBe(0x04);
  });

  it("assina com ES256 e a assinatura confere com a própria chave pública", () => {
    const cabecalho = cabecalhoVapid("https://fcm.googleapis.com/fcm/send/ABC", chaves);
    const casa = /^vapid t=([^,]+), k=(.+)$/.exec(cabecalho);
    expect(casa).not.toBeNull();

    const jwt = casa?.[1] ?? "";
    expect(casa?.[2]).toBe(par.publicKey);

    const [h = "", p = "", s = ""] = jwt.split(".");
    expect((JSON.parse(deB64url(h).toString()) as { alg: string }).alg).toBe("ES256");

    // `ieee-p1363` é o formato cru r||s. O padrão do Node é DER, que o
    // provedor recusa com 401 — erro que parece problema de chave.
    const assinatura = deB64url(s);
    expect(assinatura).toHaveLength(64);

    const ok = createVerify("SHA256")
      .update(`${h}.${p}`)
      .verify(
        { key: publicaDeCrua(deB64url(par.publicKey)), dsaEncoding: "ieee-p1363" },
        assinatura,
      );
    expect(ok).toBe(true);
  });

  it("o `aud` é a ORIGEM do endpoint, nunca a URL inteira", () => {
    // A URL contém o identificador do aparelho. Ele não precisa aparecer
    // dentro de um token que o provedor guarda.
    const cabecalho = cabecalhoVapid(
      "https://updates.push.services.mozilla.com/wpush/v2/gAAAAA-identificador-do-aparelho",
      chaves,
    );
    const corpo = corpoDoJwt(cabecalho) as { aud: string; sub: string; exp: number };

    expect(corpo.aud).toBe("https://updates.push.services.mozilla.com");
    expect(corpo.aud).not.toContain("identificador-do-aparelho");
    expect(corpo.sub).toBe("mailto:elo@fiscale.local");
  });

  it("o token expira", () => {
    const agora = new Date("2026-08-10T12:00:00Z");
    const cabecalho = cabecalhoVapid("https://fcm.googleapis.com/x", chaves, agora);
    const corpo = corpoDoJwt(cabecalho) as { exp: number };

    const vida = corpo.exp - Math.floor(agora.getTime() / 1000);
    expect(vida).toBeGreaterThan(0);
    // A RFC permite até 24 h; usamos 12, e o teste trava isso.
    expect(vida).toBeLessThanOrEqual(24 * 3600);
    expect(vida).toBe(12 * 3600);
  });
});

/* ─── envio e classificação da resposta ──────────────────────────────── */

describe("envio", () => {
  const par = gerarParVapid();
  const chaves = { ...par, subject: "mailto:elo@fiscale.local" };
  const nav = navegadorFalso();
  const destino = { endpoint: "https://push.exemplo/aparelho-1", ...nav.chaves };

  async function comFetch(
    resposta: Response | Error,
    executar: () => Promise<unknown>,
  ): Promise<{ req: Request | null; resultado: unknown }> {
    const original = globalThis.fetch;
    let capturada: Request | null = null;
    globalThis.fetch = ((url: string, init: RequestInit) => {
      capturada = new Request(url, init);
      return resposta instanceof Error ? Promise.reject(resposta) : Promise.resolve(resposta);
    }) as typeof fetch;
    try {
      // O `await` PRIMEIRO: num literal de objeto as propriedades são
      // avaliadas na ordem em que aparecem, e `req: capturada` antes do
      // `await` leria o valor de antes da chamada — ou seja, `null`.
      const resultado = await executar();
      return { req: capturada, resultado };
    } finally {
      globalThis.fetch = original;
    }
  }

  it("manda os cabeçalhos que o protocolo exige", async () => {
    const { req } = await comFetch(new Response(null, { status: 201 }), () =>
      enviarPush(destino, "oi", chaves, { topic: "eloabc" }),
    );

    expect(req!.method).toBe("POST");
    expect(req!.headers.get("content-encoding")).toBe("aes128gcm");
    expect(req!.headers.get("content-type")).toBe("application/octet-stream");
    expect(req!.headers.get("authorization")).toMatch(/^vapid t=.+, k=.+$/);
    expect(req!.headers.get("ttl")).toBe("1800");
    expect(req!.headers.get("topic")).toBe("eloabc");
    // Nada de urgência alta: não existe push crítico nesta fase.
    expect(req!.headers.get("urgency")).toBe("normal");
  });

  it("o corpo que sai pela rede está cifrado — o texto não aparece nele", async () => {
    const segredo = "Meu faturamento foi R$ 200 mil";
    const { req } = await comFetch(new Response(null, { status: 201 }), () =>
      enviarPush(destino, segredo, chaves),
    );

    const bytes = Buffer.from(await req!.arrayBuffer());
    expect(bytes.includes(Buffer.from(segredo, "utf8"))).toBe(false);
    // E o que está lá dentro é exatamente o que se quis mandar.
    expect(decifrarComoNavegador(bytes, nav)).toBe(segredo);
  });

  it("201 é entrega", async () => {
    const { resultado } = await comFetch(new Response(null, { status: 201 }), () =>
      enviarPush(destino, "oi", chaves),
    );
    expect(resultado).toEqual({ tipo: "ENTREGUE", status: 201 });
  });

  it("404 e 410 marcam a inscrição como EXPIRADA", async () => {
    for (const status of [404, 410]) {
      const { resultado } = await comFetch(new Response(null, { status }), () =>
        enviarPush(destino, "oi", chaves),
      );
      expect(resultado).toEqual({ tipo: "EXPIRADA", status });
    }
  });

  it("429 e 5xx são falhas TEMPORÁRIAS — o provedor pode voltar", async () => {
    for (const status of [429, 500, 503]) {
      const { resultado } = await comFetch(new Response(null, { status }), () =>
        enviarPush(destino, "oi", chaves),
      );
      expect(resultado).toEqual({ tipo: "TEMPORARIA", status });
    }
  });

  it("400 é recusa nossa — repetir só repetiria o defeito", async () => {
    const { resultado } = await comFetch(new Response(null, { status: 400 }), () =>
      enviarPush(destino, "oi", chaves),
    );
    expect(resultado).toEqual({ tipo: "RECUSADA", status: 400 });
  });

  it("rede fora não vira exceção: vira resultado", async () => {
    const { resultado } = await comFetch(new Error("getaddrinfo ENOTFOUND"), () =>
      enviarPush(destino, "oi", chaves),
    );
    expect(resultado).toMatchObject({ tipo: "SEM_RESPOSTA" });
  });

  it("chave malformada na linha não derruba nada — recusa sem requisição", async () => {
    const original = globalThis.fetch;
    let chamou = false;
    globalThis.fetch = (() => {
      chamou = true;
      return Promise.resolve(new Response(null, { status: 201 }));
    }) as typeof fetch;
    try {
      const r = await enviarPush(
        { endpoint: "https://push.exemplo/x", p256dh: "invalida", auth: "curta" },
        "oi",
        chaves,
      );
      expect(r).toEqual({ tipo: "RECUSADA", status: 0 });
      expect(chamou).toBe(false);
    } finally {
      globalThis.fetch = original;
    }
  });
});
