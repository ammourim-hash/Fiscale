/**
 * Mídia por HTTP de verdade: upload, download, Range e isolamento.
 *
 * O teste que carrega o arquivo é o de download cross-tenant: a sessão do
 * tenant B pedindo o anexo do tenant A precisa receber 404 — e não 403,
 * que já confirmaria a existência.
 */
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { exchange } from "@/server/auth/exchange";
import { criarAtendimento } from "@/server/conversations/service";
import { enviarMensagem } from "@/server/messages/service";
import { LocalStorageProvider } from "@/server/storage/local";
import { trocarStorage } from "@/server/storage";
import type { Role } from "@/generated/prisma/enums";

import {
  assinarToken,
  criarTenantComPessoa,
  gerarPar,
  instalarChaves,
  removerTenant,
  type ParDeChaves,
  type TenantFixture,
} from "./auth-helpers";
import { fecharPrisma } from "./helpers";
import { mp3Falso, pdfFalso, pngFalso } from "./media-helpers";

let par: ParDeChaves;
let A: TenantFixture;
let B: TenantFixture;
let cookieA: string;
let cookieB: string;
let raiz: string;

async function entrar(t: TenantFixture): Promise<string> {
  const r = await exchange(assinarToken(par, { sub: t.fiscaleUid, tid: t.id }));
  if (!r.ok) throw new Error(`troca falhou: ${r.reason}`);
  return r.session.cookieValue;
}

/** POST multipart em /api/uploads, como o navegador faria. */
async function subirPorHttp(
  cookie: string,
  nome: string,
  mime: string,
  dados: Buffer,
  extras: Record<string, string> = {},
): Promise<Response> {
  const { POST } = await import("@/app/api/uploads/route");

  const forma = new FormData();
  forma.append("file", new File([new Uint8Array(dados)], nome, { type: mime }));
  for (const [k, v] of Object.entries(extras)) forma.append(k, v);

  return POST(
    new NextRequest(
      new Request("http://localhost/api/uploads", {
        method: "POST",
        headers: { cookie: `elo_sessao=${cookie}` },
        body: forma,
      }),
    ),
  );
}

async function baixar(
  cookie: string,
  id: string,
  range?: string,
): Promise<Response> {
  const { GET } = await import("@/app/api/attachments/[id]/route");
  const headers: Record<string, string> = { cookie: `elo_sessao=${cookie}` };
  if (range) headers.range = range;

  return GET(
    new NextRequest(new Request(`http://localhost/api/attachments/${id}`, { headers })),
    { params: Promise.resolve({ id }) },
  );
}

beforeAll(async () => {
  raiz = await mkdtemp(join(tmpdir(), "elo-http-midia-"));
  trocarStorage(new LocalStorageProvider(raiz));

  par = gerarPar("md-1");
  instalarChaves(par);
  A = await criarTenantComPessoa("hma", "OWNER" as Role);
  B = await criarTenantComPessoa("hmb", "OWNER" as Role);
  cookieA = await entrar(A);
  cookieB = await entrar(B);
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
  await rm(raiz, { recursive: true, force: true });
});

/* upload por HTTP ───────────────────────────────────────────────────── */

describe("POST /api/uploads", () => {
  it("sem sessão, 401", async () => {
    const { POST } = await import("@/app/api/uploads/route");
    const forma = new FormData();
    forma.append("file", new File([new Uint8Array(pdfFalso())], "x.pdf", { type: "application/pdf" }));
    const r = await POST(
      new NextRequest(
        new Request("http://localhost/api/uploads", { method: "POST", body: forma }),
      ),
    );
    expect(r.status).toBe(401);
  });

  it("4. imagem válida sobe e devolve o id", async () => {
    const r = await subirPorHttp(cookieA, "foto.png", "image/png", pngFalso());
    expect(r.status).toBe(200);
    const d = (await r.json()) as { id: string; type: string };
    expect(d.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(d.type).toBe("IMAGE");
  });

  it("7. arquivo com conteúdo mentindo é recusado com 400", async () => {
    // .png por fora, PDF por dentro.
    const r = await subirPorHttp(cookieA, "mentira.png", "image/png", pdfFalso());
    expect(r.status).toBe(400);
    const d = (await r.json()) as { error: { message: string } };
    expect(d.error.message).toMatch(/não corresponde/i);
  });

  it("5. extensão não permitida é recusada", async () => {
    const r = await subirPorHttp(cookieA, "malware.exe", "application/octet-stream", pdfFalso());
    expect(r.status).toBe(400);
  });
});

/* download ──────────────────────────────────────────────────────────── */

describe("GET /api/attachments/:id", () => {
  it("sem sessão, 401 — arquivo privado não tem URL pública", async () => {
    const r = await subirPorHttp(cookieA, "privado.pdf", "application/pdf", pdfFalso());
    const { id } = (await r.json()) as { id: string };

    const { GET } = await import("@/app/api/attachments/[id]/route");
    const sem = await GET(
      new NextRequest(new Request(`http://localhost/api/attachments/${id}`)),
      { params: Promise.resolve({ id }) },
    );
    expect(sem.status).toBe(401);
  });

  it("11/13. documento vai como attachment, com nosniff", async () => {
    const r = await subirPorHttp(cookieA, "guia.pdf", "application/pdf", pdfFalso(600));
    const { id } = (await r.json()) as { id: string };

    const baixado = await baixar(cookieA, id);
    expect(baixado.status).toBe(200);
    // PDF inline seria script rodando na nossa origem.
    expect(baixado.headers.get("content-disposition")).toContain("attachment");
    expect(baixado.headers.get("content-disposition")).toContain("guia.pdf");
    expect(baixado.headers.get("x-content-type-options")).toBe("nosniff");
    expect(baixado.headers.get("content-type")).toBe("application/pdf");
    // Nada de proxy compartilhado guardando documento de cliente.
    expect(baixado.headers.get("cache-control")).toContain("private");

    const bytes = Buffer.from(await baixado.arrayBuffer());
    expect(bytes.byteLength).toBe(pdfFalso(600).byteLength);
  });

  it("14. imagem pode ir inline", async () => {
    const r = await subirPorHttp(cookieA, "foto.png", "image/png", pngFalso());
    const { id } = (await r.json()) as { id: string };
    const baixado = await baixar(cookieA, id);
    expect(baixado.headers.get("content-disposition")).toContain("inline");
  });

  it("o caminho interno do storage NUNCA aparece na resposta", async () => {
    const r = await subirPorHttp(cookieA, "segredo.pdf", "application/pdf", pdfFalso());
    const corpo = await r.text();
    expect(corpo).not.toContain("tenants/");
    expect(corpo).not.toContain("storageKey");
  });

  it("3. anexo do tenant A é 404 para a sessão do tenant B", async () => {
    const r = await subirPorHttp(cookieA, "confidencial.pdf", "application/pdf", pdfFalso());
    const { id } = (await r.json()) as { id: string };

    const invasao = await baixar(cookieB, id);
    // 404 e não 403: 403 confirmaria que o anexo existe.
    expect(invasao.status).toBe(404);
  });

  it("id inexistente também é 404", async () => {
    const r = await baixar(cookieA, "00000000-0000-4000-8000-000000000999");
    expect(r.status).toBe(404);
  });
});

/* 18 ─ range ────────────────────────────────────────────────────────── */

describe("Range — o que faz o seek do áudio funcionar", () => {
  it("18. responde 206 com a faixa pedida", async () => {
    const audio = mp3Falso(2048);
    const r = await subirPorHttp(cookieA, "recado.mp3", "audio/mpeg", audio);
    const { id } = (await r.json()) as { id: string };

    const parcial = await baixar(cookieA, id, "bytes=100-199");
    expect(parcial.status).toBe(206);
    expect(parcial.headers.get("content-range")).toBe(`bytes 100-199/${audio.byteLength}`);
    expect(parcial.headers.get("content-length")).toBe("100");

    const bytes = Buffer.from(await parcial.arrayBuffer());
    expect(bytes.byteLength).toBe(100);
    expect(bytes.equals(audio.subarray(100, 200))).toBe(true);
  });

  it("18. anuncia accept-ranges mesmo na resposta inteira", async () => {
    const r = await subirPorHttp(cookieA, "outro.mp3", "audio/mpeg", mp3Falso());
    const { id } = (await r.json()) as { id: string };
    const inteiro = await baixar(cookieA, id);
    // Sem este cabeçalho o navegador nem tenta buscar posição.
    expect(inteiro.headers.get("accept-ranges")).toBe("bytes");
  });

  it("18. `bytes=500-` vai até o fim", async () => {
    const audio = mp3Falso(1000);
    const r = await subirPorHttp(cookieA, "fim.mp3", "audio/mpeg", audio);
    const { id } = (await r.json()) as { id: string };

    const parcial = await baixar(cookieA, id, "bytes=500-");
    expect(parcial.status).toBe(206);
    expect(parcial.headers.get("content-range")).toBe(
      `bytes 500-${audio.byteLength - 1}/${audio.byteLength}`,
    );
  });

  it("18. faixa além do arquivo devolve 416", async () => {
    const r = await subirPorHttp(cookieA, "curto.mp3", "audio/mpeg", mp3Falso(10));
    const { id } = (await r.json()) as { id: string };

    const fora = await baixar(cookieA, id, "bytes=999999-1000000");
    expect(fora.status).toBe(416);
    expect(fora.headers.get("content-range")).toMatch(/^bytes \*\//);
  });
});

/* 23 ─ realtime ─────────────────────────────────────────────────────── */

describe("realtime", () => {
  it("23. o evento leva metadado da mídia, e não o arquivo", async () => {
    const { publish, subscribe } = await import("@/server/realtime/bus");
    void publish;

    const eventos: unknown[] = [];
    const parar = subscribe(A.id, (e) => eventos.push(e));

    const { id: conversa } = await criarAtendimento(A.id, {});
    const up = await subirPorHttp(cookieA, "relatorio.pdf", "application/pdf", pdfFalso(4096));
    const { id: anexoId } = (await up.json()) as { id: string };

    const ctx = {
      sessionId: "00000000-0000-4000-8000-000000000001",
      tenantId: A.id,
      membershipId: A.membership.membershipId,
      identityId: A.membership.identityId,
      email: A.email,
      name: "Pessoa",
      role: "OWNER" as Role,
    };
    await enviarMensagem(ctx, conversa, { content: "", attachmentIds: [anexoId] });
    parar();

    const criada = eventos.find(
      (e): e is { type: string; messageType: string; attachment: { fileName: string; sizeBytes: number } | null } =>
        typeof e === "object" && e !== null && (e as { type?: string }).type === "message.created",
    );

    expect(criada?.messageType).toBe("DOCUMENT");
    expect(criada?.attachment?.fileName).toBe("relatorio.pdf");
    expect(criada?.attachment?.sizeBytes).toBeGreaterThan(4000);

    // O evento tem METADADO. Os bytes não trafegam por aqui: nenhum campo
    // do evento chega perto do tamanho do arquivo.
    const serializado = JSON.stringify(criada);
    expect(serializado.length).toBeLessThan(1000);
    expect(serializado).not.toContain("storageKey");
  });
});

/* 25 ─ entrada de desenvolvimento ───────────────────────────────────── */

describe("entrada de desenvolvimento com mídia", () => {
  it("25. bloqueada sem a variável — 404, mesmo com sessão válida", async () => {
    const anterior = process.env.ELO_ALLOW_DEV_INBOUND;
    delete process.env.ELO_ALLOW_DEV_INBOUND;

    const { POST } = await import("@/app/api/dev/inbound/route");
    const { id } = await criarAtendimento(A.id, {});

    const r = await POST(
      new NextRequest(
        new Request("http://localhost/api/dev/inbound", {
          method: "POST",
          headers: { cookie: `elo_sessao=${cookieA}`, "content-type": "application/json" },
          body: JSON.stringify({ conversationId: id, content: "oi" }),
        }),
      ),
    );
    expect(r.status).toBe(404);

    if (anterior !== undefined) process.env.ELO_ALLOW_DEV_INBOUND = anterior;
  });

  it("25. liberada, entrega mídia como se fosse o cliente", async () => {
    const anterior = process.env.ELO_ALLOW_DEV_INBOUND;
    process.env.ELO_ALLOW_DEV_INBOUND = "1";

    const { POST } = await import("@/app/api/dev/inbound/route");
    const { id } = await criarAtendimento(A.id, {});

    const up = await subirPorHttp(cookieA, "do-cliente.png", "image/png", pngFalso());
    const { id: anexoId } = (await up.json()) as { id: string };

    const r = await POST(
      new NextRequest(
        new Request("http://localhost/api/dev/inbound", {
          method: "POST",
          headers: { cookie: `elo_sessao=${cookieA}`, "content-type": "application/json" },
          body: JSON.stringify({ conversationId: id, content: "", attachmentIds: [anexoId] }),
        }),
      ),
    );

    expect(r.status).toBe(200);
    const d = (await r.json()) as { direction: string; tipo: string };
    expect(d.direction).toBe("INBOUND");
    expect(d.tipo).toBe("IMAGE");

    if (anterior === undefined) delete process.env.ELO_ALLOW_DEV_INBOUND;
    else process.env.ELO_ALLOW_DEV_INBOUND = anterior;
  });
});
