/**
 * Isolamento no nivel do banco, sem Prisma e sem a aplicacao.
 *
 * O arquivo anterior prova que a camada TypeScript se comporta. Este prova
 * que o comportamento nao DEPENDE dela: conexao crua, SQL escrito a mao,
 * `SELECT *` sem clausula nenhuma. Se o RLS estiver frouxo, aparece aqui.
 */
import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  conexaoApp,
  conexaoOwner,
  conexaoSuperusuario,
  criarTenant,
  fecharPrisma,
  removerTenant,
  sqlComTenant,
  sqlSemTenant,
  type TenantFixture,
} from "./helpers";

let db: Client;
let A: TenantFixture;
let B: TenantFixture;

beforeAll(async () => {
  A = await criarTenant("sa");
  B = await criarTenant("sb");
  db = await conexaoApp();
});

afterAll(async () => {
  await db.end();
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

describe("o papel do runtime nao tem como escapar do RLS", () => {
  it("nao e superusuario e nao tem BYPASSRLS", async () => {
    const [papel] = await sqlSemTenant<{
      rolname: string;
      rolsuper: boolean;
      rolbypassrls: boolean;
    }>(db, "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user");

    expect(papel?.rolname).toBe("elo_app");
    expect(papel?.rolsuper).toBe(false);
    expect(papel?.rolbypassrls).toBe(false);
  });

  it("nao consegue desligar a politica", async () => {
    await expect(
      sqlSemTenant(db, "ALTER TABLE contacts DISABLE ROW LEVEL SECURITY"),
    ).rejects.toThrow();
  });

  it("nao consegue criar tabela no schema", async () => {
    await expect(sqlSemTenant(db, "CREATE TABLE fuga (id int)")).rejects.toThrow();
  });

  it("nao enxerga o historico de migrations", async () => {
    await expect(
      sqlSemTenant(db, "SELECT * FROM _prisma_migrations"),
    ).rejects.toThrow();
  });
});

describe("SELECT cru respeita o tenant", () => {
  it("com o tenant de A, so vem linha de A", async () => {
    const linhas = await sqlComTenant<{ id: string; tenant_id: string }>(
      db,
      A.id,
      "SELECT id, tenant_id FROM contacts",
    );
    expect(linhas).toHaveLength(1);
    expect(linhas[0]?.tenant_id).toBe(A.id);
  });

  it("pedindo explicitamente a linha de B com o tenant de A, vem vazio", async () => {
    const linhas = await sqlComTenant(
      db,
      A.id,
      "SELECT id FROM contacts WHERE id = $1",
      [B.contactId],
    );
    expect(linhas).toHaveLength(0);
  });

  it("filtrando por tenant_id = B com o tenant de A, vem vazio", async () => {
    // A politica e AND com o WHERE do usuario: pedir o tenant do outro nao
    // ajuda em nada.
    const linhas = await sqlComTenant(
      db,
      A.id,
      "SELECT id FROM memberships WHERE tenant_id = $1",
      [B.id],
    );
    expect(linhas).toHaveLength(0);
  });

  it("um JOIN entre as duas tabelas tambem nao atravessa", async () => {
    const linhas = await sqlComTenant(
      db,
      A.id,
      `SELECT c.id FROM contacts c
         JOIN tenants t ON t.id = c.tenant_id
        WHERE t.id = $1`,
      [B.id],
    );
    expect(linhas).toHaveLength(0);
  });

  it("contagem global so conta o proprio tenant", async () => {
    const [contA] = await sqlComTenant<{ n: string }>(
      db,
      A.id,
      "SELECT count(*)::text AS n FROM contacts",
    );
    const [contB] = await sqlComTenant<{ n: string }>(
      db,
      B.id,
      "SELECT count(*)::text AS n FROM contacts",
    );
    expect(contA?.n).toBe("1");
    expect(contB?.n).toBe("1");
  });
});

describe("sem app.tenant_id, o banco nao devolve nada", () => {
  it("app_tenant_id() e NULL", async () => {
    const [r] = await sqlSemTenant<{ t: string | null }>(
      db,
      "SELECT app_tenant_id() AS t",
    );
    expect(r?.t).toBeNull();
  });

  it("as tres tabelas vem vazias", async () => {
    for (const tabela of ["tenants", "memberships", "contacts", "departments", "sessions", "audit_logs"]) {
      const linhas = await sqlSemTenant(db, `SELECT * FROM ${tabela}`);
      expect(linhas, `${tabela} deveria vir vazia sem tenant`).toHaveLength(0);
    }
  });

  it("valor invalido em app.tenant_id fecha, nao abre", async () => {
    // A funcao captura a excecao de cast e devolve NULL — o resultado e
    // tela vazia, nunca a tabela inteira.
    await db.query("BEGIN");
    await db.query("SELECT set_config('app.tenant_id', 'lixo', true)");
    const r = await db.query("SELECT * FROM contacts");
    await db.query("ROLLBACK");
    expect(r.rows).toHaveLength(0);
  });

  it("INSERT sem tenant e recusado pelo WITH CHECK", async () => {
    await expect(
      sqlSemTenant(
        db,
        "INSERT INTO contacts (id, tenant_id, phone) VALUES (gen_random_uuid(), $1, '5581999990000')",
        [A.id],
      ),
    ).rejects.toThrow();
  });
});

/**
 * Controle negativo. Sem este bloco, todos os testes acima passariam com a
 * tabela vazia — "nao vazou" e "nao tem nada" produzem o mesmo resultado.
 * Aqui se prova que os dados dos dois tenants existem mesmo, e que o que os
 * separa e o RLS.
 */
describe("controle negativo — os dados existem", () => {
  it("o superusuario ve as linhas dos DOIS tenants", async () => {
    const su = await conexaoSuperusuario();
    try {
      const r = await su.query(
        "SELECT tenant_id FROM contacts WHERE tenant_id = ANY($1::uuid[])",
        [[A.id, B.id]],
      );
      // Se isto der 0, os testes de isolamento acima nao provam nada.
      expect(r.rows).toHaveLength(2);
    } finally {
      await su.end();
    }
  });

  it("o dono das tabelas tambem e barrado — e para isso que serve o FORCE", async () => {
    const owner = await conexaoOwner();
    try {
      const semContexto = await owner.query("SELECT id FROM contacts");
      expect(semContexto.rows).toHaveLength(0);

      await owner.query("BEGIN");
      await owner.query("SELECT set_config('app.tenant_id', $1, true)", [A.id]);
      const comA = await owner.query("SELECT id, tenant_id FROM contacts");
      await owner.query("COMMIT");

      expect(comA.rows).toHaveLength(1);
      expect(comA.rows[0]?.tenant_id).toBe(A.id);
    } finally {
      await owner.end();
    }
  });
});
