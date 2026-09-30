/**
 * Pre-condicoes da Triagem v1, antes de aplicar a migration no banco de
 * trabalho.
 *
 *     node scripts/triagem-precondicoes.mjs
 *
 * SO LE. Nenhum comando de alteracao sai daqui — nem DDL, nem DML, nem
 * `migrate`. Existe para responder quatro perguntas de uma vez, e sem
 * precisar decorar quatro consultas:
 *
 *   1. a migration esta no disco?
 *   2. o banco `elo` ainda NAO tem o valor TRIAGED?
 *   3. `elo_test` e `elo_dev` JA tem?
 *   4. ha alguma migration pendente ANTES dela no `elo`?
 *
 * A quarta e a que mais importa e a que ninguem lembra de fazer: aplicar
 * a migration nova por cima de um banco que ficou para tras aplica junto
 * tudo que estava pendente, e o "so um enum novo" vira outra coisa.
 *
 * Leitura de ambiente igual a do `migrar-banco.mjs`: arquivo proprio,
 * sem dotenv, sem herdar nada do `process.env`. Herdar a URL seria o
 * acidente que este script existe para evitar.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import pg from "pg";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..");
const MIGRATION = "20260924144200_triagem_evento";

/** Cada banco: de qual arquivo le, e se DEVE ou NAO ter o TRIAGED hoje. */
const BANCOS = [
  { nome: "elo", arquivo: ".env", esperaTriaged: false, papel: "trabalho (producao local)" },
  { nome: "elo_test", arquivo: ".env.test", esperaTriaged: true, papel: "suite de testes" },
  { nome: "elo_dev", arquivo: ".env.dev", esperaTriaged: true, papel: "ensaio manual" },
];

let falhas = 0;

function ok(texto) {
  console.log(`  OK    ${texto}`);
}
function erro(texto) {
  console.log(`  FALHA ${texto}`);
  falhas += 1;
}
function aviso(texto) {
  console.log(`  ....  ${texto}`);
}

function lerUrl(arquivo) {
  const caminho = join(RAIZ, arquivo);
  if (!existsSync(caminho)) return null;
  const variaveis = new Map();
  for (const linha of readFileSync(caminho, "utf8").split(/\r?\n/)) {
    const casou = /^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/.exec(linha);
    if (casou) variaveis.set(casou[1], casou[2].trim().replace(/^["']|["']$/g, ""));
  }
  // A do OWNER: e ela que enxerga `_prisma_migrations`.
  return variaveis.get("DATABASE_MIGRATION_URL") ?? variaveis.get("DATABASE_URL") ?? null;
}

function migrationsNoDisco() {
  const dir = join(RAIZ, "prisma", "migrations");
  return readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort();
}

async function inspecionar(url) {
  const cliente = new pg.Client({ connectionString: url });
  await cliente.connect();
  try {
    const enum_ = await cliente.query(
      `select count(*)::int as n
         from pg_enum e
         join pg_type t on t.oid = e.enumtypid
        where t.typname = 'conversation_event_type'
          and e.enumlabel = 'TRIAGED'`,
    );
    const aplicadas = await cliente.query(
      `select migration_name
         from _prisma_migrations
        where finished_at is not null and rolled_back_at is null
        order by migration_name`,
    );
    return {
      temTriaged: enum_.rows[0].n > 0,
      aplicadas: aplicadas.rows.map((r) => r.migration_name),
    };
  } finally {
    await cliente.end();
  }
}

async function main() {
  console.log("\n=== Pre-condicoes da Triagem v1 (somente leitura) ===\n");

  /* 1. a migration esta no disco? */
  console.log("1. migration no disco");
  const disco = migrationsNoDisco();
  const sql = join(RAIZ, "prisma", "migrations", MIGRATION, "migration.sql");
  if (disco.includes(MIGRATION) && existsSync(sql)) {
    ok(`${MIGRATION}/migration.sql presente`);
  } else {
    erro(`${MIGRATION} nao encontrada em prisma/migrations`);
  }

  /* 2, 3 e 4: cada banco */
  for (const banco of BANCOS) {
    console.log(`\n2/3/4. banco ${banco.nome} — ${banco.papel}`);

    const url = lerUrl(banco.arquivo);
    if (!url) {
      erro(`${banco.arquivo} ausente ou sem URL — nao da para conferir`);
      continue;
    }
    if (!new RegExp(`/${banco.nome}(\\?|$)`).test(url)) {
      erro(`${banco.arquivo} aponta para outro banco que nao ${banco.nome}`);
      continue;
    }

    let estado;
    try {
      estado = await inspecionar(url);
    } catch (e) {
      erro(`nao consegui ler ${banco.nome}: ${String(e).split("\n")[0]}`);
      continue;
    }

    if (estado.temTriaged === banco.esperaTriaged) {
      ok(
        banco.esperaTriaged
          ? "enum TRIAGED presente, como esperado"
          : "enum TRIAGED AUSENTE, como esperado (nada foi aplicado aqui)",
      );
    } else {
      erro(
        banco.esperaTriaged
          ? "enum TRIAGED AUSENTE — rode a migration deste ambiente antes"
          : "enum TRIAGED JA EXISTE no banco de trabalho — alguem aplicou",
      );
    }

    const pendentes = disco.filter((m) => !estado.aplicadas.includes(m));
    const anteriores = pendentes.filter((m) => m < MIGRATION);

    if (anteriores.length === 0) {
      ok(`sem migration pendente ANTERIOR a ${MIGRATION}`);
    } else {
      erro(`PENDENTES antes da nossa: ${anteriores.join(", ")}`);
    }
    aviso(
      `aplicadas: ${estado.aplicadas.length} | pendentes: ${
        pendentes.length ? pendentes.join(", ") : "nenhuma"
      }`,
    );
  }

  console.log(
    falhas === 0
      ? "\n=== TUDO PRONTO. Nenhuma alteracao foi executada. ===\n"
      : `\n=== ${falhas} PRE-CONDICAO(OES) NAO ATENDIDA(S). Nada foi executado. ===\n`,
  );
  process.exit(falhas === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
