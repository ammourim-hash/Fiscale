/**
 * Roda antes de qualquer arquivo de teste.
 *
 * O pool vai para UMA conexao de proposito. Assim todo teste — nao so o de
 * vazamento — reaproveita a mesma conexao fisica entre operacoes de tenants
 * diferentes. Se o contexto de tenant escapasse do escopo da transacao, a
 * suite inteira mostraria o problema, nao apenas um caso preparado.
 */
import "dotenv/config";
import { config as carregarEnv } from "dotenv";
// Matchers de DOM (toHaveTextContent, toHaveAttribute...). Em ambiente
// Node nao atrapalha: e so registro de matcher.
import "@testing-library/jest-dom/vitest";

// O `.env` aponta para o banco `elo`, que guarda a projecao real do
// escritorio: os clientes sincronizados do Fiscale, a pessoa vinculada e o
// historico de atendimento. Ate 19/09/2026 a suite rodava LA — cada execucao
// criava tenants e deixava identidades, auditorias e sync_runs para tras.
//
// O `.env.test` traz so as duas URLs do `elo_test` e sobrescreve as do `.env`;
// chave de troca, storage e VAPID continuam vindo do `.env`, porque duplicar
// isso aqui criaria uma segunda verdade para manter.
carregarEnv({ path: ".env.test", override: true });

process.env.ELO_DB_POOL_MAX = "1";

// Os testes criam tenants e pessoas, o que so a ferramenta administrativa
// pode fazer. Em producao esta variavel nao existe no processo web — e e
// justamente por isso que provisionamento nao vira rota por acidente.
process.env.ELO_ADMIN_TOOL = "1";

if (!process.env.DATABASE_URL) {
  throw new Error(
    "DATABASE_URL ausente — copie .env.example para .env e rode `npm run db:up`",
  );
}

// Trava, nao convencao. Sem ela, um `.env.test` ausente, mal copiado ou
// sobrescrito devolveria a suite ao banco real em silencio — e o estrago so
// apareceria depois, como sobra. Aqui a suite INTEIRA para antes do primeiro
// teste, dizendo em qual banco ela ia mexer.
const BANCO_DE_TESTE = "elo_test";
const bancoAtual = new URL(process.env.DATABASE_URL).pathname.replace(/^\//, "");
if (bancoAtual !== BANCO_DE_TESTE) {
  throw new Error(
    `a suite so roda contra o banco ${BANCO_DE_TESTE}, e a DATABASE_URL aponta para ` +
      `${bancoAtual} — crie elo/.env.test com as URLs do ${BANCO_DE_TESTE}`,
  );
}

/**
 * O jsdom nao implementa EventSource, e a tela de Atendimentos abre um ao
 * montar. Sem este substituto, todo teste de interface morreria em
 * "EventSource is not defined" — o que nao diria nada sobre a interface.
 *
 * Ele nao simula realtime: so registra ouvintes e nunca emite. O que o
 * realtime faz de verdade e testado contra o barramento e contra HTTP, em
 * tests/realtime.test.ts, e nao aqui.
 */
if (typeof window !== "undefined" && !("EventSource" in globalThis)) {
  class EventSourceFalso {
    onerror: (() => void) | null = null;
    addEventListener(): void {}
    removeEventListener(): void {}
    close(): void {}
  }
  (globalThis as unknown as { EventSource: unknown }).EventSource = EventSourceFalso;
}
