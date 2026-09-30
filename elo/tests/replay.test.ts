/**
 * Uso unico do token de troca.
 *
 * O caso sequencial (usar duas vezes em seguida) e o facil. O que este
 * arquivo existe para provar e o dificil: duas requisicoes ao mesmo tempo,
 * com o mesmo token, sem intervalo entre elas. E ali que a implementacao
 * ingenua — consultar, depois gravar — deixa as duas passarem.
 */
import { randomUUID } from "node:crypto";

import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { exchange } from "@/server/auth/exchange";
import { consumeJti, purgeExpiredJtis } from "@/server/auth/replay";

import {
  assinarToken,
  criarTenantComPessoa,
  gerarPar,
  instalarChaves,
  removerTenant,
  type ParDeChaves,
  type TenantFixture,
} from "./auth-helpers";
import { conexaoApp, fecharPrisma } from "./helpers";

let par: ParDeChaves;
let A: TenantFixture;

beforeAll(async () => {
  par = gerarPar("replay-1");
  instalarChaves(par);
  A = await criarTenantComPessoa("ra");
});

afterAll(async () => {
  await removerTenant(A.id);
  await fecharPrisma();
});

describe("replay sequencial", () => {
  it("o mesmo token nao serve duas vezes", async () => {
    const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id });

    const primeira = await exchange(token);
    expect(primeira.ok).toBe(true);

    const segunda = await exchange(token);
    expect(segunda).toEqual({ ok: false, reason: "REPLAY" });
  });

  it("o replay nao cria sessao nova", async () => {
    const { withTenant } = await import("@/server/tenancy");
    const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id });

    await exchange(token);
    const antes = await withTenant(A.id, async (tx) => tx.session.count());
    await exchange(token);
    const depois = await withTenant(A.id, async (tx) => tx.session.count());

    expect(depois).toBe(antes);
  });

  it("o token queima mesmo quando a troca falha depois do consumo", async () => {
    // Token assinado corretamente, mas para uma pessoa que nao existe. A
    // primeira tentativa falha em IDENTITY_NOT_FOUND — e ainda assim o jti
    // e consumido. Sem isso, daria para segurar o token e tentar de novo
    // depois que o vinculo fosse criado.
    const token = assinarToken(par, { sub: "fantasma.xyz", tid: A.id });

    expect(await exchange(token)).toEqual({ ok: false, reason: "IDENTITY_NOT_FOUND" });
    expect(await exchange(token)).toEqual({ ok: false, reason: "REPLAY" });
  });
});

describe("concorrencia — duas requisicoes ao mesmo tempo", () => {
  it("dez rodadas de troca simultanea: exatamente uma vence em cada", async () => {
    for (let i = 0; i < 10; i++) {
      const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id });

      const [x, y] = await Promise.all([exchange(token), exchange(token)]);

      const vencedores = [x, y].filter((r) => r.ok).length;
      const recusados = [x, y].filter((r) => !r.ok && r.reason === "REPLAY").length;

      expect(vencedores, `rodada ${i}: deveria vencer exatamente uma`).toBe(1);
      expect(recusados, `rodada ${i}: a outra deveria ser REPLAY`).toBe(1);
    }
  });

  it("cinco tentativas simultaneas: uma vence, quatro sao replay", async () => {
    const token = assinarToken(par, { sub: A.fiscaleUid, tid: A.id });

    const resultados = await Promise.all(
      Array.from({ length: 5 }, () => exchange(token)),
    );

    expect(resultados.filter((r) => r.ok)).toHaveLength(1);
    expect(resultados.filter((r) => !r.ok && r.reason === "REPLAY")).toHaveLength(4);
  });

  it("consumeJti chamado em paralelo devolve first=true uma unica vez", async () => {
    const jti = randomUUID();
    const exp = new Date(Date.now() + 90_000);

    const r = await Promise.all(
      Array.from({ length: 8 }, () => consumeJti(jti, exp)),
    );

    expect(r.filter((x) => x.first)).toHaveLength(1);
  });
});

describe("a atomicidade e do banco, nao do JavaScript", () => {
  let c1: Client;
  let c2: Client;

  beforeAll(async () => {
    // Duas CONEXOES de verdade, em paralelo real — nao duas promessas
    // numa mesma conexao. Se a protecao dependesse de ordem no event loop
    // do Node, ela sumiria aqui.
    c1 = await conexaoApp();
    c2 = await conexaoApp();
  });

  afterAll(async () => {
    await c1.end();
    await c2.end();
  });

  it("duas conexoes inserindo o mesmo jti: uma grava, a outra afeta zero linhas", async () => {
    for (let i = 0; i < 15; i++) {
      const jti = randomUUID();
      const sql = `INSERT INTO consumed_exchange_tokens (jti, expires_at)
                   VALUES ($1, now() + interval '90 seconds')
                   ON CONFLICT DO NOTHING`;

      const [a, b] = await Promise.all([
        c1.query(sql, [jti]),
        c2.query(sql, [jti]),
      ]);

      expect(a.rowCount! + b.rowCount!, `iteracao ${i}`).toBe(1);
    }
  });

  it("o runtime nao consegue LER a tabela de jti consumidos", async () => {
    // Sem SELECT nao ha como enumerar token de ninguem — e a deteccao de
    // replay nunca precisou ler.
    await expect(c1.query("SELECT * FROM consumed_exchange_tokens")).rejects.toThrow();
  });
});

describe("faxina", () => {
  it("apaga os expirados e mantem os vigentes", async () => {
    const vencido = randomUUID();
    const vigente = randomUUID();

    await consumeJti(vencido, new Date(Date.now() - 60_000));
    await consumeJti(vigente, new Date(Date.now() + 600_000));

    await purgeExpiredJtis();

    // Sem SELECT, a prova e indireta e melhor assim: se a linha do vencido
    // sumiu, o mesmo jti pode ser consumido de novo; o vigente, nao.
    expect((await consumeJti(vencido, new Date(Date.now() + 60_000))).first).toBe(true);
    expect((await consumeJti(vigente, new Date(Date.now() + 600_000))).first).toBe(false);
  });
});
