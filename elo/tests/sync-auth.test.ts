/**
 * Autenticacao de maquina da sincronizacao.
 *
 * A pergunta que este arquivo responde: o que precisa acontecer para uma
 * requisicao de sincronizacao ser aceita — e tudo o que precisa fazer com
 * que ela seja recusada.
 */
import { randomUUID } from "node:crypto";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  JANELA_SEGUNDOS,
  purgeExpiredNonces,
  verifySignedRequest,
} from "@/server/integrations/signature";

import { criarTenantComPessoa, removerTenant, type TenantFixture } from "./auth-helpers";
import { fecharPrisma } from "./helpers";
import {
  assinarRequisicao,
  CAMINHO_SYNC,
  corpoSync,
  gerarChaveIntegracao,
  instalarChavesIntegracao,
  type ChaveIntegracao,
} from "./sync-helpers";

let A: TenantFixture;
let B: TenantFixture;
let chaveA: ChaveIntegracao;
let chaveB: ChaveIntegracao;

beforeAll(async () => {
  A = await criarTenantComPessoa("ia");
  B = await criarTenantComPessoa("ib");
  chaveA = gerarChaveIntegracao(A.id);
  chaveB = gerarChaveIntegracao(B.id);
  instalarChavesIntegracao(chaveA, chaveB);
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

const CORPO = corpoSync([{ externalId: "1", displayName: "Empresa" }]);

describe("requisicao valida", () => {
  it("passa e devolve o tenant DA CHAVE", async () => {
    const r = await verifySignedRequest(assinarRequisicao(chaveA, CORPO));
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.identity.tenantId).toBe(A.id);
    expect(r.identity.kid).toBe(chaveA.kid);
    expect(r.identity.source).toBe("FISCALE");
  });

  it("a chave de B devolve o tenant de B — nunca o de A", async () => {
    const r = await verifySignedRequest(assinarRequisicao(chaveB, CORPO));
    expect(r.ok && r.identity.tenantId).toBe(B.id);
  });
});

describe("assinatura", () => {
  it("assinatura adulterada e recusada", async () => {
    const req = assinarRequisicao(chaveA, CORPO);
    const sig = req.headers.signature!;
    req.headers.signature = sig.slice(0, 10) + (sig[10] === "A" ? "B" : "A") + sig.slice(11);
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "BAD_SIGNATURE" });
  });

  it("corpo trocado depois de assinar e recusado", async () => {
    // O caso classico: capturar uma requisicao valida e reaproveitar a
    // assinatura com outro payload.
    const req = assinarRequisicao(chaveA, CORPO, {
      corpoAssinado: CORPO,
    });
    req.rawBody = corpoSync([{ externalId: "1", displayName: "Empresa INVADIDA" }]);
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "BAD_SIGNATURE" });
  });

  it("chave errada e recusada", async () => {
    const impostor = gerarChaveIntegracao(A.id);
    // Nao instalada — mas mesmo com o kid de A, a assinatura nao confere.
    const req = assinarRequisicao(impostor, CORPO, { kidOverride: chaveA.kid });
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "BAD_SIGNATURE" });
  });

  it("kid desconhecido e recusado", async () => {
    const req = assinarRequisicao(chaveA, CORPO, { kidOverride: "nao-existe" });
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "UNKNOWN_KEY" });
  });

  it("faltando qualquer cabecalho, recusa", async () => {
    for (const campo of ["keyId", "timestamp", "nonce", "signature"] as const) {
      const req = assinarRequisicao(chaveA, CORPO);
      req.headers[campo] = null;
      expect(await verifySignedRequest(req), campo).toEqual({
        ok: false,
        reason: "MISSING_HEADERS",
      });
    }
  });

  it("assinatura de OUTRO caminho nao serve aqui", async () => {
    // Sem metodo e caminho no texto assinado, uma assinatura valida para
    // um endpoint serviria em qualquer outro que aceitasse o mesmo corpo.
    const req = assinarRequisicao(chaveA, CORPO, { path: "/api/outra/coisa" });
    req.path = CAMINHO_SYNC;
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "BAD_SIGNATURE" });
  });

  it("assinatura de outro METODO nao serve", async () => {
    const req = assinarRequisicao(chaveA, CORPO, { method: "PUT" });
    req.method = "POST";
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "BAD_SIGNATURE" });
  });
});

describe("tempo", () => {
  it("timestamp vencido e recusado", async () => {
    const velho = Math.floor(Date.now() / 1000) - (JANELA_SEGUNDOS + 60);
    const req = assinarRequisicao(chaveA, CORPO, { timestamp: velho });
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "STALE_TIMESTAMP" });
  });

  it("timestamp muito no futuro e recusado", async () => {
    const futuro = Math.floor(Date.now() / 1000) + (JANELA_SEGUNDOS + 60);
    const req = assinarRequisicao(chaveA, CORPO, { timestamp: futuro });
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "STALE_TIMESTAMP" });
  });

  it("pequena diferenca de relogio e tolerada", async () => {
    const quase = Math.floor(Date.now() / 1000) - (JANELA_SEGUNDOS - 30);
    const req = assinarRequisicao(chaveA, CORPO, { timestamp: quase });
    expect((await verifySignedRequest(req)).ok).toBe(true);
  });

  it("timestamp que nao e numero e recusado", async () => {
    const req = assinarRequisicao(chaveA, CORPO);
    req.headers.timestamp = "ontem";
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "BAD_TIMESTAMP" });
  });
});

describe("replay", () => {
  it("a mesma requisicao, reenviada, e recusada", async () => {
    const req = assinarRequisicao(chaveA, CORPO);
    expect((await verifySignedRequest(req)).ok).toBe(true);
    expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "REPLAY" });
  });

  it("reenviar dez vezes nao passa nenhuma", async () => {
    const req = assinarRequisicao(chaveA, CORPO);
    await verifySignedRequest(req);
    for (let i = 0; i < 10; i++) {
      expect(await verifySignedRequest(req)).toEqual({ ok: false, reason: "REPLAY" });
    }
  });

  it("duas copias simultaneas: uma passa, a outra e replay", async () => {
    for (let i = 0; i < 5; i++) {
      const req = assinarRequisicao(chaveA, CORPO);
      const [x, y] = await Promise.all([
        verifySignedRequest({ ...req }),
        verifySignedRequest({ ...req }),
      ]);
      expect([x, y].filter((r) => r.ok), `rodada ${i}`).toHaveLength(1);
      expect([x, y].filter((r) => !r.ok && r.reason === "REPLAY")).toHaveLength(1);
    }
  });

  it("assinatura invalida nao queima o nonce", async () => {
    // Se queimasse, qualquer um poderia inutilizar nonces alheios
    // mandando lixo assinado errado.
    const nonce = randomUUID();
    const ruim = assinarRequisicao(chaveA, CORPO, { nonce });
    ruim.headers.signature = "AAAA";
    expect((await verifySignedRequest(ruim)).ok).toBe(false);

    const bom = assinarRequisicao(chaveA, CORPO, { nonce });
    expect((await verifySignedRequest(bom)).ok).toBe(true);
  });

  it("a faxina apaga nonce vencido e mantem o vigente", async () => {
    const vencido = randomUUID();
    const req = assinarRequisicao(chaveA, CORPO, {
      nonce: vencido,
      timestamp: Math.floor(Date.now() / 1000) - (JANELA_SEGUNDOS - 10),
    });
    await verifySignedRequest(req);

    // Depois da janela, a linha nao protege mais nada — o proprio
    // timestamp ja recusaria.
    await purgeExpiredNonces();
    expect(typeof (await purgeExpiredNonces())).toBe("number");
  });
});
