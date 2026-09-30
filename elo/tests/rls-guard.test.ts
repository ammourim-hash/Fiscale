/**
 * Guarda estrutural: impede a regressao mais provavel deste projeto.
 *
 * Os outros testes conferem as tabelas que existem hoje. Este confere a
 * REGRA — nenhuma tabela visivel ao elo_app pode estar sem RLS ligado e
 * forcado. Uma migration futura que criar tabela e esquecer de chamar
 * `SELECT elo_apply_rls();` quebra o build aqui, e nao em producao seis
 * meses depois.
 */
import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { conexaoApp } from "./helpers";

interface Tabela {
  nome: string;
  rls: boolean;
  forcado: boolean;
  legivel: boolean;
  gravavel: boolean;
  tem_tenant: boolean;
}

let db: Client;
let tabelas: Tabela[];

beforeAll(async () => {
  db = await conexaoApp();
  const r = await db.query<Tabela>(`
    SELECT c.relname                                     AS nome,
           c.relrowsecurity                              AS rls,
           c.relforcerowsecurity                         AS forcado,
           has_table_privilege('elo_app', c.oid, 'SELECT') AS legivel,
           has_table_privilege('elo_app', c.oid, 'INSERT,UPDATE,DELETE') AS gravavel,
           EXISTS (
             SELECT 1 FROM pg_attribute a
              WHERE a.attrelid = c.oid
                AND a.attname = 'tenant_id'
                AND NOT a.attisdropped
           )                                             AS tem_tenant
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind = 'r'
     ORDER BY c.relname
  `);
  tabelas = r.rows;
});

afterAll(async () => {
  await db.end();
});

describe("nenhuma tabela fica de fora do RLS", () => {
  it("existe pelo menos uma tabela para conferir", () => {
    expect(tabelas.length).toBeGreaterThan(0);
  });

  it("toda tabela legivel pelo elo_app tem RLS ligado", () => {
    const faltando = tabelas.filter((t) => t.legivel && !t.rls).map((t) => t.nome);
    expect(faltando, `sem ENABLE ROW LEVEL SECURITY: ${faltando.join(", ")}`).toEqual([]);
  });

  it("toda tabela legivel pelo elo_app tem RLS FORCADO", () => {
    // Sem FORCE, o dono da tabela (elo_owner) le tudo. E o erro classico.
    const faltando = tabelas.filter((t) => t.legivel && !t.forcado).map((t) => t.nome);
    expect(faltando, `sem FORCE ROW LEVEL SECURITY: ${faltando.join(", ")}`).toEqual([]);
  });

  it("toda tabela com tenant_id tem RLS, mesmo que ninguem a leia ainda", () => {
    const faltando = tabelas
      .filter((t) => t.tem_tenant && (!t.rls || !t.forcado))
      .map((t) => t.nome);
    expect(faltando, `tem tenant_id mas nao tem RLS: ${faltando.join(", ")}`).toEqual([]);
  });

  it("toda tabela em que o elo_app pode ESCREVER tem RLS forcado", () => {
    // Escrita sem politica seria pior que leitura sem politica: daria para
    // plantar linha com o tenant de outro.
    const faltando = tabelas
      .filter((t) => t.gravavel && (!t.rls || !t.forcado))
      .map((t) => t.nome);
    expect(faltando, `gravavel sem RLS forcado: ${faltando.join(", ")}`).toEqual([]);
  });

  it("_prisma_migrations nao e legivel pelo runtime", () => {
    const hist = tabelas.find((t) => t.nome === "_prisma_migrations");
    expect(hist?.legivel).toBe(false);
  });

  it("consumed_exchange_tokens e somente-escrita", () => {
    // Nao ha SELECT porque a deteccao de replay nao le — e sem leitura
    // ninguem enumera jti alheio.
    const t = tabelas.find((x) => x.nome === "consumed_exchange_tokens");
    expect(t, "a tabela deveria existir").toBeDefined();
    expect(t?.legivel).toBe(false);
    expect(t?.gravavel).toBe(true);
    expect(t?.rls).toBe(true);
    expect(t?.forcado).toBe(true);
  });

  it("a unica coluna legivel de consumed_exchange_tokens e expires_at", async () => {
    // A faxina precisa avaliar `WHERE expires_at < now()`, e o Postgres
    // exige leitura da coluna do filtro. `jti` continua ilegivel — sem
    // isso, a tabela que impede replay viraria lista de tokens.
    const r = await db.query<{ column_name: string }>(`
      SELECT column_name
        FROM information_schema.column_privileges
       WHERE grantee = 'elo_app'
         AND table_name = 'consumed_exchange_tokens'
         AND privilege_type = 'SELECT'
    `);
    expect(r.rows.map((x) => x.column_name)).toEqual(["expires_at"]);
  });

  it("as tabelas globais de identidade estao sob RLS por associacao", () => {
    for (const nome of ["identities", "identity_providers"]) {
      const t = tabelas.find((x) => x.nome === nome);
      expect(t, `${nome} deveria existir`).toBeDefined();
      expect(t?.tem_tenant, `${nome} nao deveria ter tenant_id`).toBe(false);
      expect(t?.rls, `${nome} sem RLS`).toBe(true);
      expect(t?.forcado, `${nome} sem FORCE`).toBe(true);
    }
  });

  it("toda tabela com RLS tem politica de isolamento", async () => {
    const r = await db.query<{ nome: string; politicas: string }>(`
      SELECT c.relname AS nome, count(p.polname)::text AS politicas
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_policy p ON p.polrelid = c.oid
       WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
       GROUP BY c.relname
    `);
    expect(r.rows.length).toBeGreaterThan(0);
    for (const linha of r.rows) {
      expect(Number(linha.politicas), `${linha.nome} sem politica`).toBeGreaterThan(0);
    }
  });
});
