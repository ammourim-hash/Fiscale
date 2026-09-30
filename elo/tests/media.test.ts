/**
 * Mídia: validação, storage, isolamento, download e faxina.
 *
 * Os testes que carregam o arquivo são os de validação de conteúdo (um
 * `.exe` renomeado não pode entrar) e o de isolamento (anexo do tenant A
 * é 404 para o tenant B, e a chave de storage não é adivinhável).
 */
import { mkdtemp, readdir, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import type { AuthContext } from "@/server/auth/context";
import { criarAtendimento } from "@/server/conversations/service";
import {
  limparAnexosOrfaos,
  receberUpload,
  type AnexoCriado,
} from "@/server/media/service";
import {
  avaliarArquivo,
  extensaoDe,
  limites,
  nomeSeguro,
} from "@/server/media/tipos";
import { listarMensagens } from "@/server/messages/queries";
import { enviarMensagem, registrarMensagemRecebida } from "@/server/messages/service";
import { LocalStorageProvider } from "@/server/storage/local";
import { montarChave, trocarStorage } from "@/server/storage";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

import { criarTenantComPessoa, removerTenant, type TenantFixture } from "./auth-helpers";
import { fecharPrisma } from "./helpers";
import {
  docxFalso,
  exeFalso,
  jpegFalso,
  mp3Falso,
  pdfFalso,
  pngFalso,
  webmFalso,
} from "./media-helpers";

let A: TenantFixture;
let B: TenantFixture;
let ctxA: AuthContext;
let raiz: string;

function contexto(t: TenantFixture, role: Role = "OWNER" as Role): AuthContext {
  return {
    sessionId: "00000000-0000-4000-8000-000000000001",
    tenantId: t.id,
    membershipId: t.membership.membershipId,
    identityId: t.membership.identityId,
    email: t.email,
    name: "Pessoa de Teste",
    role,
  };
}

async function subir(
  ctx: AuthContext,
  nome: string,
  mime: string,
  dados: Buffer,
  extras: { gravacaoDeVoz?: boolean; durationMs?: number } = {},
): Promise<AnexoCriado> {
  const r = await receberUpload(ctx, {
    nome,
    mimeDeclarado: mime,
    dados,
    ...(extras.gravacaoDeVoz !== undefined ? { gravacaoDeVoz: extras.gravacaoDeVoz } : {}),
    ...(extras.durationMs !== undefined ? { durationMs: extras.durationMs } : {}),
  });
  if (!r.ok) throw new Error(`upload falhou: ${r.message}`);
  return r.value;
}

beforeAll(async () => {
  // Storage isolado por execução: o teste não escreve na pasta de
  // desenvolvimento de quem o roda.
  raiz = await mkdtemp(join(tmpdir(), "elo-storage-teste-"));
  trocarStorage(new LocalStorageProvider(raiz));

  A = await criarTenantComPessoa("mda", "OWNER" as Role);
  B = await criarTenantComPessoa("mdb", "OWNER" as Role);
  ctxA = contexto(A);
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
  await rm(raiz, { recursive: true, force: true });
});

/* 5-9 ─ validação ───────────────────────────────────────────────────── */

describe("validação de arquivo", () => {
  it("5. extensão fora da lista é recusada", () => {
    const r = avaliarArquivo({
      nome: "script.sh",
      mimeDeclarado: "text/plain",
      dados: Buffer.from("#!/bin/sh"),
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.motivo).toBe("EXTENSAO_NAO_PERMITIDA");
  });

  it("5. `foto.jpg.exe` é lido pela ÚLTIMA extensão", () => {
    // O ponto do teste: "contém .jpg" não é extensão.
    expect(extensaoDe("foto.jpg.exe")).toBe("exe");

    const r = avaliarArquivo({
      nome: "foto.jpg.exe",
      mimeDeclarado: "image/jpeg",
      dados: exeFalso(),
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.motivo).toBe("EXTENSAO_NAO_PERMITIDA");
  });

  it("6. MIME que não corresponde à extensão é recusado", () => {
    const r = avaliarArquivo({
      nome: "documento.pdf",
      mimeDeclarado: "text/html",
      dados: pdfFalso(),
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.motivo).toBe("MIME_NAO_PERMITIDO");
  });

  it("7. MAGIC BYTES incompatíveis: um .exe renomeado para .png não entra", () => {
    // Extensão certa, MIME certo, conteúdo mentindo. É a terceira
    // checagem que pega — e é o motivo de ela existir.
    const r = avaliarArquivo({
      nome: "inocente.png",
      mimeDeclarado: "image/png",
      dados: exeFalso(),
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.motivo).toBe("CONTEUDO_NAO_CONFERE");
  });

  it("7. o mesmo vale para PDF", () => {
    const r = avaliarArquivo({
      nome: "guia.pdf",
      mimeDeclarado: "application/pdf",
      dados: pngFalso(),
    });
    expect(r.ok).toBe(false);
  });

  it("8. arquivo acima do limite da categoria é recusado", () => {
    const teto = limites().IMAGE;
    const gigante = Buffer.concat([pngFalso(0), Buffer.alloc(teto + 1024, 0x20)]);
    const r = avaliarArquivo({
      nome: "enorme.png",
      mimeDeclarado: "image/png",
      dados: gigante,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.motivo).toBe("GRANDE_DEMAIS");
  });

  it("arquivo vazio é recusado", () => {
    const r = avaliarArquivo({
      nome: "vazio.png",
      mimeDeclarado: "image/png",
      dados: Buffer.alloc(0),
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.motivo).toBe("ARQUIVO_VAZIO");
  });

  it("9. nome perigoso é neutralizado", () => {
    expect(nomeSeguro("../../etc/passwd")).toBe("passwd");
    expect(nomeSeguro("C:\\Windows\\system32\\cmd.exe")).toBe("cmd.exe");
    // Aspas e quebra de linha dentro de Content-Disposition deixariam
    // quem enviou escrever cabeçalho de resposta.
    expect(nomeSeguro('arq"uivo.pdf')).not.toContain('"');
    expect(nomeSeguro("linha\r\nInjetada: 1.pdf")).not.toContain("\n");
    expect(nomeSeguro("...oculto.pdf")).toBe("oculto.pdf");
    expect(nomeSeguro("")).toBe("arquivo");
  });

  it("4. imagem válida passa, com a categoria certa", () => {
    const r = avaliarArquivo({
      nome: "documento.jpg",
      mimeDeclarado: "image/jpeg",
      dados: jpegFalso(),
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.categoria).toBe("IMAGE");
      expect(r.mimeType).toBe("image/jpeg");
    }
  });

  it("docx é ZIP por dentro, e isso é aceito", () => {
    const r = avaliarArquivo({
      nome: "contrato.docx",
      mimeDeclarado:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      dados: docxFalso(),
    });
    expect(r.ok).toBe(true);
  });

  it("gravação de voz só aceita formato de gravador", () => {
    const voz = avaliarArquivo({
      nome: "mensagem-de-voz.webm",
      mimeDeclarado: "audio/webm",
      dados: webmFalso(),
      gravacaoDeVoz: true,
    });
    expect(voz.ok).toBe(true);
    if (voz.ok) expect(voz.categoria).toBe("VOICE");

    // Um mp3 não pode se declarar "voz" para escapar de outro limite.
    const disfarce = avaliarArquivo({
      nome: "musica.mp3",
      mimeDeclarado: "audio/mpeg",
      dados: mp3Falso(),
      gravacaoDeVoz: true,
    });
    expect(disfarce.ok).toBe(false);
  });
});

/* 1, 10 ─ criação e chave ───────────────────────────────────────────── */

describe("upload", () => {
  it("1. cria o anexo com metadado e status PENDING", async () => {
    const a = await subir(ctxA, "guia.pdf", "application/pdf", pdfFalso(512));

    expect(a.type).toBe("DOCUMENT");
    expect(a.originalFileName).toBe("guia.pdf");
    expect(a.sizeBytes).toBe(pdfFalso(512).byteLength);

    const linha = await withTenant(A.id, async (tx) =>
      tx.messageAttachment.findUniqueOrThrow({
        where: { id: a.id },
        select: { status: true, expiresAt: true, sha256: true, storageProvider: true },
      }),
    );
    expect(linha.status).toBe("PENDING");
    expect(linha.expiresAt).toBeTruthy();
    expect(linha.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(linha.storageProvider).toBe("local");
  });

  it("10. a storageKey é aleatória, tem o tenant no prefixo e NÃO tem o nome", async () => {
    const a = await subir(ctxA, "MinhaFotoSecreta.png", "image/png", pngFalso());

    const { storageKey } = await withTenant(A.id, async (tx) =>
      tx.messageAttachment.findUniqueOrThrow({
        where: { id: a.id },
        select: { storageKey: true },
      }),
    );

    expect(storageKey).toContain(`tenants/${A.id}/`);
    expect(storageKey.toLowerCase()).not.toContain("minhafoto");
    expect(storageKey).not.toContain(".png");
    // 32 hex no fim: adivinhar exige força bruta sobre 128 bits.
    expect(storageKey).toMatch(/[0-9a-f]{32}$/);
  });

  it("duas chaves nunca colidem", () => {
    const chaves = new Set(Array.from({ length: 200 }, () => montarChave(A.id)));
    expect(chaves.size).toBe(200);
  });

  it("os bytes vão para o disco, e não para o banco", async () => {
    const a = await subir(ctxA, "no-disco.pdf", "application/pdf", pdfFalso(1024));
    const { storageKey } = await withTenant(A.id, async (tx) =>
      tx.messageAttachment.findUniqueOrThrow({
        where: { id: a.id },
        select: { storageKey: true },
      }),
    );
    const info = await stat(join(raiz, storageKey));
    expect(info.size).toBe(pdfFalso(1024).byteLength);
  });
});

/* 2, 3 ─ isolamento ─────────────────────────────────────────────────── */

describe("isolamento entre tenants", () => {
  it("2/3. anexo do tenant A não existe para o tenant B", async () => {
    const a = await subir(ctxA, "confidencial.pdf", "application/pdf", pdfFalso());

    const paraB = await withTenant(B.id, async (tx) =>
      tx.messageAttachment.findUnique({ where: { id: a.id }, select: { id: true } }),
    );
    // Não é "sem permissão": para B a linha não existe. Quem decide é o
    // RLS, e é por isso que o download devolve 404 e não 403.
    expect(paraB).toBeNull();
  });

  it("B não consegue listar anexo de A nem contando", async () => {
    await subir(ctxA, "outro.pdf", "application/pdf", pdfFalso());
    const quantos = await withTenant(B.id, async (tx) => tx.messageAttachment.count());
    expect(quantos).toBe(0);
  });

  it("a chave de A carrega o id de A, não o de B", async () => {
    const chave = montarChave(A.id);
    expect(chave).toContain(A.id);
    expect(chave).not.toContain(B.id);
  });
});

/* 13-17, 24 ─ tipos na mensagem ─────────────────────────────────────── */

describe("mídia na mensagem", () => {
  it("14. imagem vira mensagem do tipo IMAGE", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "foto.jpg", "image/jpeg", jpegFalso());

    const r = await enviarMensagem(ctxA, id, { content: "", attachmentIds: [a.id] });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.tipo).toBe("IMAGE");

    const pagina = await listarMensagens(ctxA, id);
    const m = pagina?.messages[0];
    expect(m?.type).toBe("IMAGE");
    expect(m?.attachments).toHaveLength(1);
    expect(m?.attachments[0]?.fileName).toBe("foto.jpg");
  });

  it("13. documento vira DOCUMENT e mantém nome e tamanho", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "DAS_08-2026.pdf", "application/pdf", pdfFalso(2048));
    await enviarMensagem(ctxA, id, { content: "", attachmentIds: [a.id] });

    const pagina = await listarMensagens(ctxA, id);
    const anexo = pagina?.messages[0]?.attachments[0];
    expect(pagina?.messages[0]?.type).toBe("DOCUMENT");
    expect(anexo?.fileName).toBe("DAS_08-2026.pdf");
    expect(anexo?.sizeBytes).toBeGreaterThan(2000);
  });

  it("15. arquivo de áudio vira AUDIO", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "recado.mp3", "audio/mpeg", mp3Falso());
    const r = await enviarMensagem(ctxA, id, { content: "", attachmentIds: [a.id] });
    expect(r.ok && r.value.tipo).toBe("AUDIO");
  });

  it("16/17. gravação vira VOICE e guarda a duração", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "mensagem-de-voz.webm", "audio/webm", webmFalso(), {
      gravacaoDeVoz: true,
      durationMs: 37_000,
    });

    expect(a.type).toBe("VOICE");
    expect(a.durationMs).toBe(37_000);

    await enviarMensagem(ctxA, id, { content: "", attachmentIds: [a.id] });
    const pagina = await listarMensagens(ctxA, id);
    // A duração vem do metadado: dá para escrever "0:37" sem baixar nada.
    expect(pagina?.messages[0]?.attachments[0]?.durationMs).toBe(37_000);
    expect(pagina?.messages[0]?.type).toBe("VOICE");
  });

  it("24. mídia OUTBOUND mantém a assinatura Nome • Área", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "foto.png", "image/png", pngFalso());
    await enviarMensagem(ctxA, id, { content: "", attachmentIds: [a.id] });

    const pagina = await listarMensagens(ctxA, id);
    // Sem texto, a assinatura continua lá — ela não depende de haver
    // conteúdo escrito.
    expect(pagina?.messages[0]?.senderDisplayName).toBeTruthy();
  });

  it("mensagem só com anexo é válida; vazia sem anexo não é", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "so-anexo.pdf", "application/pdf", pdfFalso());

    expect((await enviarMensagem(ctxA, id, { content: "", attachmentIds: [a.id] })).ok).toBe(
      true,
    );
    expect((await enviarMensagem(ctxA, id, { content: "   " })).ok).toBe(false);
  });

  it("anexo já usado não é adotado por uma segunda mensagem", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "unico.pdf", "application/pdf", pdfFalso());

    await enviarMensagem(ctxA, id, { content: "primeira", attachmentIds: [a.id] });
    await enviarMensagem(ctxA, id, { content: "segunda", attachmentIds: [a.id] });

    const pagina = await listarMensagens(ctxA, id);
    const comAnexo = (pagina?.messages ?? []).filter((m) => m.attachments.length > 0);
    expect(comAnexo).toHaveLength(1);
  });

  it("mídia de ENTRADA também funciona", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "cliente-mandou.jpg", "image/jpeg", jpegFalso());

    const r = await registrarMensagemRecebida(A.id, id, {
      content: "",
      attachmentIds: [a.id],
      channel: "DEV",
    });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.direction).toBe("INBOUND");
    expect(r.value.tipo).toBe("IMAGE");
  });
});

/* 19-22 ─ idempotência e órfãos ─────────────────────────────────────── */

describe("idempotência e arquivos órfãos", () => {
  it("19/35. retry com o mesmo clientMessageId e o mesmo anexo não duplica", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "retry.pdf", "application/pdf", pdfFalso());
    const cid = `midia-${Date.now()}`;

    const um = await enviarMensagem(ctxA, id, {
      content: "",
      clientMessageId: cid,
      attachmentIds: [a.id],
    });
    const dois = await enviarMensagem(ctxA, id, {
      content: "",
      clientMessageId: cid,
      attachmentIds: [a.id],
    });

    expect(um.ok && dois.ok).toBe(true);
    if (!um.ok || !dois.ok) return;
    expect(dois.value.id).toBe(um.value.id);

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages).toHaveLength(1);
    expect(pagina?.messages[0]?.attachments).toHaveLength(1);
  });

  it("20. duas requisições concorrentes com o mesmo id criam uma mensagem", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "concorrente.pdf", "application/pdf", pdfFalso());
    const cid = `midia-conc-${Date.now()}`;

    const [x, y] = await Promise.all([
      enviarMensagem(ctxA, id, { content: "", clientMessageId: cid, attachmentIds: [a.id] }),
      enviarMensagem(ctxA, id, { content: "", clientMessageId: cid, attachmentIds: [a.id] }),
    ]);

    expect(x.ok && y.ok).toBe(true);
    if (!x.ok || !y.ok) return;
    expect(x.value.id).toBe(y.value.id);

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages).toHaveLength(1);
  });

  it("21. upload sem mensagem fica PENDING com prazo", async () => {
    const a = await subir(ctxA, "orfao.pdf", "application/pdf", pdfFalso());
    const linha = await withTenant(A.id, async (tx) =>
      tx.messageAttachment.findUniqueOrThrow({
        where: { id: a.id },
        select: { status: true, messageId: true, expiresAt: true },
      }),
    );
    expect(linha.status).toBe("PENDING");
    expect(linha.messageId).toBeNull();
    expect(linha.expiresAt!.getTime()).toBeGreaterThan(Date.now());
  });

  it("22. a faxina apaga o órfão vencido — linha E arquivo", async () => {
    const a = await subir(ctxA, "vai-sumir.pdf", "application/pdf", pdfFalso());
    const { storageKey } = await withTenant(A.id, async (tx) =>
      tx.messageAttachment.findUniqueOrThrow({
        where: { id: a.id },
        select: { storageKey: true },
      }),
    );

    // O arquivo existe agora.
    await expect(stat(join(raiz, storageKey))).resolves.toBeTruthy();

    // Uma hora no futuro: o prazo venceu.
    const r = await limparAnexosOrfaos(A.id, new Date(Date.now() + 3 * 60 * 60 * 1000));
    expect(r.apagados).toBeGreaterThan(0);

    const sobrou = await withTenant(A.id, async (tx) =>
      tx.messageAttachment.findUnique({ where: { id: a.id }, select: { id: true } }),
    );
    expect(sobrou).toBeNull();
    await expect(stat(join(raiz, storageKey))).rejects.toThrow();
  });

  it("22. a faxina NÃO toca em anexo que virou mensagem", async () => {
    const { id } = await criarAtendimento(A.id, {});
    const a = await subir(ctxA, "guardado.pdf", "application/pdf", pdfFalso());
    await enviarMensagem(ctxA, id, { content: "", attachmentIds: [a.id] });

    await limparAnexosOrfaos(A.id, new Date(Date.now() + 99 * 60 * 60 * 1000));

    const ainda = await withTenant(A.id, async (tx) =>
      tx.messageAttachment.findUnique({
        where: { id: a.id },
        select: { status: true, expiresAt: true },
      }),
    );
    // ATTACHED e sem prazo: a adoção tirou o anexo do alcance da faxina.
    expect(ainda?.status).toBe("ATTACHED");
    expect(ainda?.expiresAt).toBeNull();
  });
});

/* 29 ─ desempenho ───────────────────────────────────────────────────── */

describe("desempenho", () => {
  it("29. histórico de texto não carrega mídia nenhuma", async () => {
    const { id } = await criarAtendimento(A.id, {});
    await withTenant(A.id, async (tx) => {
      await tx.$executeRaw`
        INSERT INTO messages (id, tenant_id, conversation_id, sequence, direction,
                              type, content, channel, created_at)
        SELECT gen_random_uuid(), ${A.id}::uuid, ${id}::uuid, g,
               'INBOUND'::message_direction, 'TEXT'::message_type,
               'volume ' || g, 'DEV', now()
          FROM generate_series(1, 5000) g
      `;
      await tx.$executeRaw`UPDATE conversations SET message_seq = 5000 WHERE id = ${id}::uuid`;
    });

    const antes = Date.now();
    const pagina = await listarMensagens(ctxA, id, { limite: 40 });
    const decorrido = Date.now() - antes;

    expect(pagina?.messages).toHaveLength(40);
    // Nenhuma das 40 tem anexo, então nenhum byte de mídia foi tocado.
    expect(pagina?.messages.every((m) => m.attachments.length === 0)).toBe(true);
    expect(decorrido, `${decorrido}ms`).toBeLessThan(3000);
  });
});

/* storage ───────────────────────────────────────────────────────────── */

describe("provedor de storage", () => {
  it("18. leitura por faixa devolve só o pedaço pedido", async () => {
    const provedor = new LocalStorageProvider(raiz);
    const chave = `tenants/${A.id}/messages/2026/08/${"a".repeat(32)}`;
    const conteudo = Buffer.from("0123456789ABCDEF");
    await provedor.put(chave, conteudo, "application/octet-stream");

    const faixa = await provedor.getRange(chave, 4, 9);
    expect(faixa.corpo.toString()).toBe("456789");
    expect(faixa.inicio).toBe(4);
    expect(faixa.fim).toBe(9);
    expect(faixa.tamanhoTotal).toBe(16);
  });

  it("18. faixa além do fim é aparada, não estoura", async () => {
    const provedor = new LocalStorageProvider(raiz);
    const chave = `tenants/${A.id}/messages/2026/08/${"b".repeat(32)}`;
    await provedor.put(chave, Buffer.from("abcdef"), "application/octet-stream");

    const faixa = await provedor.getRange(chave, 3, 9999);
    expect(faixa.corpo.toString()).toBe("def");
    expect(faixa.fim).toBe(5);
  });

  it("recusa chave que tenta sair da raiz", async () => {
    const provedor = new LocalStorageProvider(raiz);
    // Travessia de caminho é o defeito clássico de storage em disco.
    await expect(provedor.get("../../../etc/passwd")).rejects.toThrow(/raiz/);
    await expect(
      provedor.put("../fuga.txt", Buffer.from("x"), "text/plain"),
    ).rejects.toThrow(/raiz/);
  });

  it("apagar duas vezes não é erro", async () => {
    const provedor = new LocalStorageProvider(raiz);
    const chave = `tenants/${A.id}/messages/2026/08/${"c".repeat(32)}`;
    await provedor.put(chave, Buffer.from("x"), "text/plain");
    await provedor.delete(chave);
    await expect(provedor.delete(chave)).resolves.toBeUndefined();
  });

  it("metadata devolve null quando não existe", async () => {
    const provedor = new LocalStorageProvider(raiz);
    expect(await provedor.metadata("tenants/x/nao-existe")).toBeNull();
  });

  it("11. o provedor local NÃO emite URL — o download é pela rota", async () => {
    const provedor = new LocalStorageProvider(raiz);
    // `null` é resposta legítima, e obriga quem chama a usar a rota
    // autenticada em vez de supor que existe link público.
    expect(await provedor.signedUrl("qualquer", 60)).toBeNull();
  });

  it("o storage guarda os arquivos sob o prefixo do tenant", async () => {
    await subir(ctxA, "arquivo-a.pdf", "application/pdf", pdfFalso());
    const pastas = await readdir(join(raiz, "tenants"));
    expect(pastas).toContain(A.id);
  });
});

/* storage fora do ar ────────────────────────────────────────────────── */

describe("storage indisponivel", () => {
  it("a midia falha de forma controlada e o TEXTO continua funcionando", async () => {
    const { StorageError } = await import("@/server/storage/provider");

    // Um provedor que so sabe falhar — disco cheio, S3 fora, credencial
    // vencida: do ponto de vista do Elo e tudo a mesma coisa.
    const quebrado = {
      nome: "quebrado",
      put: () => Promise.reject(new StorageError("sem disco", "put")),
      get: () => Promise.reject(new StorageError("sem disco", "get")),
      getRange: () => Promise.reject(new StorageError("sem disco", "getRange")),
      delete: () => Promise.resolve(),
      metadata: () => Promise.resolve(null),
      signedUrl: () => Promise.resolve(null),
    };
    const anterior = trocarStorage(quebrado as never);

    try {
      const r = await receberUpload(ctxA, {
        nome: "nao-vai.pdf",
        mimeDeclarado: "application/pdf",
        dados: pdfFalso(),
      });

      // Falha ANUNCIADA, com motivo proprio — nao uma excecao que sobe
      // ate derrubar a requisicao.
      expect(r.ok).toBe(false);
      if (!r.ok) {
        expect(r.motivo).toBe("STORAGE");
        expect(r.message).toMatch(/tente de novo/i);
      }

      // E o essencial: a conversa continua. Texto nao depende de storage.
      const { id } = await criarAtendimento(A.id, {});
      const texto = await enviarMensagem(ctxA, id, { content: "O sistema continua de pe." });
      expect(texto.ok).toBe(true);

      const pagina = await listarMensagens(ctxA, id);
      expect(pagina?.messages[0]?.content).toBe("O sistema continua de pe.");
    } finally {
      if (anterior) trocarStorage(anterior);
    }
  });

  it("nenhuma linha de anexo fica para tras quando o storage falha", async () => {
    const { StorageError } = await import("@/server/storage/provider");
    const antes = await withTenant(A.id, async (tx) => tx.messageAttachment.count());

    const quebrado = {
      nome: "quebrado",
      put: () => Promise.reject(new StorageError("sem disco", "put")),
      get: () => Promise.reject(new StorageError("x", "get")),
      getRange: () => Promise.reject(new StorageError("x", "getRange")),
      delete: () => Promise.resolve(),
      metadata: () => Promise.resolve(null),
      signedUrl: () => Promise.resolve(null),
    };
    const anterior = trocarStorage(quebrado as never);

    try {
      await receberUpload(ctxA, {
        nome: "nada.pdf",
        mimeDeclarado: "application/pdf",
        dados: pdfFalso(),
      });
      // Os bytes vem primeiro: falhando ali, a linha nunca chega a nascer.
      const depois = await withTenant(A.id, async (tx) => tx.messageAttachment.count());
      expect(depois).toBe(antes);
    } finally {
      if (anterior) trocarStorage(anterior);
    }
  });
});

/* 26/27 ─ RLS e RBAC ────────────────────────────────────────────────── */

describe("RBAC", () => {
  it("27. VIEWER não envia anexo", async () => {
    const viewer = contexto(A, "VIEWER" as Role);
    await expect(
      receberUpload(viewer, {
        nome: "tentativa.pdf",
        mimeDeclarado: "application/pdf",
        dados: pdfFalso(),
      }),
    ).rejects.toThrow(/messages.send/);
  });
});
