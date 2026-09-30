/**
 * Aplica as migrations num banco que NAO e o de trabalho.
 *
 *   npm run db:migrate:test   -> .env.test -> elo_test
 *   npm run db:migrate:dev    -> .env.dev  -> elo_dev
 *
 * Existe porque `prisma migrate deploy` le a DATABASE_MIGRATION_URL do
 * .env, que aponta para o banco de trabalho. Rodar o deploy "no outro
 * banco" dependia de lembrar de exportar a variavel na mao — e esquecer
 * disso migrava o banco errado sem avisar.
 *
 * Um script para os dois ambientes, e nao um por ambiente: duas copias da
 * mesma trava viram duas travas diferentes no primeiro ajuste.
 *
 * Nao ha fallback: se o arquivo de ambiente nao existir, ou nao tiver a
 * URL, ou a URL apontar para outro banco, este script PARA. Herdar a URL
 * do ambiente seria justamente o acidente que ele evita.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..");

/** Cada ambiente: de qual arquivo le e qual banco aceita. Nada mais. */
const AMBIENTES = {
  test: { arquivo: ".env.test", banco: "elo_test" },
  dev: { arquivo: ".env.dev", banco: "elo_dev" },
};

function morrer(mensagem) {
  console.error(`\n[elo] ${mensagem}\n`);
  process.exit(1);
}

const nome = process.argv[2];
const ambiente = AMBIENTES[nome];
if (!ambiente) {
  morrer(`uso: node scripts/migrar-banco.mjs <${Object.keys(AMBIENTES).join("|")}>`);
}

const caminho = join(RAIZ, ambiente.arquivo);
if (!existsSync(caminho)) {
  morrer(
    `elo/${ambiente.arquivo} nao existe. Copie ${ambiente.arquivo}.example e ajuste as ` +
      `senhas — as mesmas do .env, trocando so o nome do banco por ${ambiente.banco}.`,
  );
}

// Leitura propria, sem dotenv: aqui nao se quer NADA no process.env. O
// valor vai direto para o processo filho.
const variaveis = new Map();
for (const linha of readFileSync(caminho, "utf8").split(/\r?\n/)) {
  const casou = /^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/.exec(linha);
  if (casou) variaveis.set(casou[1], casou[2].trim().replace(/^["']|["']$/g, ""));
}

const url = variaveis.get("DATABASE_MIGRATION_URL");
if (!url) morrer(`DATABASE_MIGRATION_URL ausente em ${caminho}.`);

let banco;
try {
  banco = new URL(url).pathname.replace(/^\//, "");
} catch {
  morrer(`DATABASE_MIGRATION_URL de ${caminho} nao e uma URL valida.`);
}

if (banco !== ambiente.banco) {
  morrer(
    `este comando so migra o banco ${ambiente.banco}, e ${ambiente.arquivo} aponta para ` +
      `${banco}. Para migrar o banco de trabalho use \`npm run prisma:deploy\`.`,
  );
}

console.log(`[elo] aplicando migrations em ${banco}…`);

// Chama o CLI do Prisma pelo arquivo, com o proprio node. `npx` no Windows
// e um .cmd, que o Node se recusa a executar sem `shell: true` — e com
// shell ele concatena os argumentos numa linha so e avisa (DEP0190).
const prisma = join(RAIZ, "node_modules", "prisma", "build", "index.js");
if (!existsSync(prisma)) morrer("prisma nao encontrado em node_modules — rode `npm install`.");

const r = spawnSync(process.execPath, [prisma, "migrate", "deploy"], {
  cwd: RAIZ,
  stdio: "inherit",
  // Explicito vence o .env que o prisma.config.ts carrega: dotenv nao
  // sobrescreve variavel que ja existe no ambiente do processo.
  env: { ...process.env, DATABASE_MIGRATION_URL: url },
});

if (r.status !== 0) {
  morrer(
    `migrations falharam. Se o banco ${banco} nao existir, ele nasce com o container ` +
      "(docker/postgres/initdb/00-roles.sh) — e esse script so roda em volume novo.",
  );
}
