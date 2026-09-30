/**
 * Vazamento por reutilizacao de conexao.
 *
 * Este e o teste que justifica a arquitetura de with-tenant.ts. O pool esta
 * limitado a UMA conexao (tests/setup.ts), entao toda operacao aqui usa
 * fisicamente a mesma conexao que a anterior devolveu. Se o `app.tenant_id`
 * sobrevivesse ao fim da transacao — que e o que aconteceria com `SET` em
 * vez de `set_config(..., true)` — o tenant seguinte herdaria o contexto do
 * anterior e este arquivo falharia.
 *
 * E a falha mais silenciosa que existe em multitenancy: nao ha erro, nao ha
 * log, so o dado errado na tela certa.
 */
import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { prisma } from "@/server/db/client";
import { withTenant } from "@/server/tenancy";

import {
  conexaoApp,
  criarTenant,
  fecharPrisma,
  removerTenant,
  type TenantFixture,
} from "./helpers";

let A: TenantFixture;
let B: TenantFixture;

beforeAll(async () => {
  A = await criarTenant("pa");
  B = await criarTenant("pb");
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

describe("alternando A e B na mesma conexao", () => {
  it("40 alternancias, e nenhuma ve o contexto da anterior", async () => {
    const esperado = [
      { tenant: A, contato: A.contactId },
      { tenant: B, contato: B.contactId },
    ];

    for (let volta = 0; volta < 20; volta++) {
      for (const { tenant, contato } of esperado) {
        const contatos = await withTenant(tenant.id, async (tx) => tx.contact.findMany());

        expect(contatos, `volta ${volta}, tenant ${tenant.slug}`).toHaveLength(1);
        expect(contatos[0]?.id).toBe(contato);
        expect(contatos[0]?.tenantId).toBe(tenant.id);
      }
    }
  });

  it("depois de uma transacao que falhou, a proxima nao herda nada", async () => {
    // ROLLBACK tambem tem de limpar o contexto. Se so o COMMIT limpasse,
    // qualquer erro de negocio deixaria a conexao suja.
    await expect(
      withTenant(A.id, async (tx) => {
        await tx.contact.findMany();
        throw new Error("falha proposital no meio da transacao");
      }),
    ).rejects.toThrow("falha proposital");

    const semTenant = await prisma.$queryRaw<
      { t: string | null }[]
    >`SELECT current_setting('app.tenant_id', true) AS t`;
    expect(semTenant[0]?.t ?? "").toBe("");

    const deB = await withTenant(B.id, async (tx) => tx.contact.findMany());
    expect(deB).toHaveLength(1);
    expect(deB[0]?.id).toBe(B.contactId);
  });

  it("fora de withTenant a conexao esta limpa e nao devolve dado", async () => {
    await withTenant(A.id, async (tx) => tx.contact.findMany());

    // Mesma conexao fisica, agora sem contexto: tem de vir vazio.
    const vazio = await prisma.contact.findMany();
    expect(vazio).toHaveLength(0);

    const ctx = await prisma.$queryRaw<
      { t: string | null }[]
    >`SELECT current_setting('app.tenant_id', true) AS t`;
    expect(ctx[0]?.t ?? "").toBe("");
  });
});

describe("o mesmo, numa conexao pg crua", () => {
  let db: Client;

  beforeAll(async () => {
    db = await conexaoApp();
  });

  afterAll(async () => {
    await db.end();
  });

  it("SET LOCAL nao sobrevive ao COMMIT", async () => {
    await db.query("BEGIN");
    await db.query("SELECT set_config('app.tenant_id', $1, true)", [A.id]);
    const dentro = await db.query("SELECT current_setting('app.tenant_id', true) AS t");
    await db.query("COMMIT");

    const fora = await db.query("SELECT current_setting('app.tenant_id', true) AS t");

    expect(dentro.rows[0]?.t).toBe(A.id);
    expect(fora.rows[0]?.t ?? "").toBe("");
  });

  it("30 alternancias cruas, sem contaminacao", async () => {
    for (let i = 0; i < 15; i++) {
      for (const t of [A, B]) {
        await db.query("BEGIN");
        await db.query("SELECT set_config('app.tenant_id', $1, true)", [t.id]);
        const r = await db.query("SELECT id, tenant_id FROM contacts");
        await db.query("COMMIT");

        expect(r.rows, `iteracao ${i} / ${t.slug}`).toHaveLength(1);
        expect(r.rows[0]?.tenant_id).toBe(t.id);
      }
    }
  });

  it("uma transacao abortada nao deixa o contexto para tras", async () => {
    await db.query("BEGIN");
    await db.query("SELECT set_config('app.tenant_id', $1, true)", [A.id]);
    await db.query("ROLLBACK");

    const r = await db.query("SELECT * FROM contacts");
    expect(r.rows).toHaveLength(0);
  });
});
