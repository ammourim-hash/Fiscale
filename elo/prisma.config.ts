/**
 * Configuracao da CLI do Prisma (Prisma 7).
 *
 * Este arquivo so e lido por comandos de linha de comando — `migrate`,
 * `db`, `studio`. A aplicacao em execucao NAO passa por aqui: ela monta o
 * cliente em src/server/db/client.ts com o driver adapter e a DATABASE_URL
 * do papel elo_app.
 *
 * Por isso a URL daqui e a DATABASE_MIGRATION_URL, do papel elo_owner: o
 * unico que pode criar tabela. Se um dia alguem apontar isto para a
 * DATABASE_URL, a migration falha por falta de permissao — que e o
 * comportamento desejado.
 */
import "dotenv/config";
import { defineConfig, env } from "prisma/config";

export default defineConfig({
  schema: "prisma/schema.prisma",
  migrations: {
    path: "prisma/migrations",
  },
  datasource: {
    url: env("DATABASE_MIGRATION_URL"),
  },
});
