/**
 * Espera o Postgres do container aceitar conexao.
 *
 *   npm run db:espera
 *
 * O `docker compose up -d` devolve o controle assim que o container sobe, e
 * o bootstrap de papeis e bancos ainda esta rodando la dentro. Sem esta
 * espera, o `prisma migrate deploy` logo em seguida falha por banco
 * inexistente — e o erro nao diz que o problema era so pressa.
 *
 * Em script de npm um `until ... sleep` nao serve: no Windows o comando roda
 * pelo cmd, que nao entende aspas simples nem esse laco.
 */
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..");
const LIMITE_SEGUNDOS = 120;

const banco = process.env.ELO_DB_NAME ?? "elo";
const inicio = Date.now();

function pronto() {
  // Sem `shell`: o docker e executavel proprio, e com shell o Node emite o
  // aviso DEP0190 sobre argumentos concatenados.
  const r = spawnSync(
    "docker",
    ["compose", "exec", "-T", "postgres", "pg_isready", "-U", "postgres", "-d", banco],
    { cwd: RAIZ, stdio: "ignore" },
  );
  return r.status === 0;
}

while (!pronto()) {
  if ((Date.now() - inicio) / 1000 > LIMITE_SEGUNDOS) {
    console.error(
      `\n[elo] o banco '${banco}' nao respondeu em ${LIMITE_SEGUNDOS}s. ` +
        "Veja `npm run db:logs`.\n",
    );
    process.exit(1);
  }
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1000);
}

console.log(`[elo] banco '${banco}' no ar.`);
