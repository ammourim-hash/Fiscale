/**
 * Concorrência de verdade no envio de mensagens.
 *
 * ---------------------------------------------------------------------
 *  Por que este arquivo existe separado
 * ---------------------------------------------------------------------
 *  `tests/setup.ts` fixa o pool em UMA conexão, de propósito: é o que
 *  força o reuso e faz qualquer vazamento de contexto de tenant aparecer
 *  em toda a suíte.
 *
 *  Só que uma conexão SERIALIZA as transações. Um `Promise.all` de dois
 *  envios, ali, não roda em paralelo — roda um depois do outro. E um teste
 *  de concorrência que não tem concorrência passa com a implementação
 *  errada, que é o pior resultado possível: dá confiança sem dar garantia.
 *
 *  Descoberto por mutação: com o `sequence` vindo de um
 *  `SELECT MAX(sequence) + 1` — a versão ingênua, que perde a corrida — o
 *  teste "dez envios simultâneos" continuava passando no arquivo comum.
 *
 *  Aqui o pool é aberto ANTES de o cliente Prisma existir, e por isso
 *  todos os imports são dinâmicos: `import` estático é içado para cima do
 *  arquivo e criaria o pool com o tamanho errado.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

// Antes de qualquer import que toque no banco. O cliente lê esta variável
// uma vez, ao ser criado.
process.env.ELO_DB_POOL_MAX = "8";

type Ctx = import("@/server/auth/context").AuthContext;
type Fixture = import("./auth-helpers").TenantFixture;

let A: Fixture;
let ctxA: Ctx;

let criarAtendimento: typeof import("@/server/conversations/service").criarAtendimento;
let enviarMensagem: typeof import("@/server/messages/service").enviarMensagem;
let listarMensagens: typeof import("@/server/messages/queries").listarMensagens;
let removerTenant: typeof import("./auth-helpers").removerTenant;
let fecharPrisma: typeof import("./helpers").fecharPrisma;

beforeAll(async () => {
  const auth = await import("./auth-helpers");
  const helpers = await import("./helpers");
  const conversas = await import("@/server/conversations/service");
  const mensagens = await import("@/server/messages/service");
  const consultas = await import("@/server/messages/queries");

  criarAtendimento = conversas.criarAtendimento;
  enviarMensagem = mensagens.enviarMensagem;
  listarMensagens = consultas.listarMensagens;
  removerTenant = auth.removerTenant;
  fecharPrisma = helpers.fecharPrisma;

  A = await auth.criarTenantComPessoa("cc", "OWNER" as never);
  ctxA = {
    sessionId: "00000000-0000-4000-8000-000000000001",
    tenantId: A.id,
    membershipId: A.membership.membershipId,
    identityId: A.membership.identityId,
    email: A.email,
    name: "Pessoa de Teste",
    role: "OWNER" as never,
  };
});

afterAll(async () => {
  await removerTenant(A.id);
  await fecharPrisma();
});

async function novoAtendimento(): Promise<string> {
  const { id } = await criarAtendimento(A.id, {});
  return id;
}

describe("o pool permite paralelismo de verdade", () => {
  it("duas transações se sobrepõem no tempo", async () => {
    // Se este teste falhar, todos os outros deste arquivo perdem o
    // sentido: estariam medindo execução sequencial.
    const { prisma } = await import("@/server/db/client");

    // `pg_sleep` devolve void e o Prisma não desserializa void: por isso
    // ele vai no FROM, e a projeção devolve um inteiro.
    const dormir = () => prisma.$queryRaw<{ ok: number }[]>`SELECT 1 AS ok FROM pg_sleep(0.4)`;

    const inicio = Date.now();
    await Promise.all([dormir(), dormir()]);
    const decorrido = Date.now() - inicio;

    // Em série seriam ~800ms. Em paralelo, ~400ms.
    expect(decorrido, `${decorrido}ms — parece serializado`).toBeLessThan(700);
  });
});

describe("16. ordenação sob concorrência real", () => {
  it("dez envios simultâneos recebem dez sequências distintas", async () => {
    const id = await novoAtendimento();

    const resultados = await Promise.all(
      Array.from({ length: 10 }, (_, i) =>
        enviarMensagem(ctxA, id, { content: `paralela ${i}` }),
      ),
    );

    expect(resultados.every((r) => r.ok), "todos os envios deveriam gravar").toBe(true);

    const pagina = await listarMensagens(ctxA, id, { limite: 100 });
    const seqs = (pagina?.messages ?? []).map((m) => m.sequence);

    expect(seqs).toHaveLength(10);
    // Sem repetição, sem buraco. É o `UPDATE ... RETURNING` que garante:
    // a linha da conversa fica travada, e cada envio pega o número
    // seguinte em vez de todos lerem o mesmo máximo.
    expect(new Set(seqs).size, `sequências repetidas: ${seqs.join(",")}`).toBe(10);
    expect([...seqs].sort((a, b) => a - b)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });

  it("vinte envios em duas rajadas continuam sem buraco", async () => {
    const id = await novoAtendimento();

    await Promise.all(
      Array.from({ length: 10 }, (_, i) => enviarMensagem(ctxA, id, { content: `a${i}` })),
    );
    await Promise.all(
      Array.from({ length: 10 }, (_, i) => enviarMensagem(ctxA, id, { content: `b${i}` })),
    );

    const pagina = await listarMensagens(ctxA, id, { limite: 100 });
    const seqs = (pagina?.messages ?? []).map((m) => m.sequence);
    expect(seqs).toEqual(Array.from({ length: 20 }, (_, i) => i + 1));
  });
});

describe("15. idempotência sob concorrência real", () => {
  it("duas requisições simultâneas com o mesmo id criam UMA mensagem", async () => {
    for (let i = 0; i < 5; i++) {
      const id = await novoAtendimento();
      const cid = `paralelo-${Date.now()}-${i}`;

      const [x, y] = await Promise.all([
        enviarMensagem(ctxA, id, { content: "reenvio", clientMessageId: cid }),
        enviarMensagem(ctxA, id, { content: "reenvio", clientMessageId: cid }),
      ]);

      expect(x.ok && y.ok, `rodada ${i}: as duas deveriam responder ok`).toBe(true);
      if (!x.ok || !y.ok) return;
      expect(x.value.id, `rodada ${i}: ids diferentes = mensagem duplicada`).toBe(y.value.id);

      const pagina = await listarMensagens(ctxA, id);
      expect(pagina?.messages, `rodada ${i}`).toHaveLength(1);
    }
  });

  it("cinco tentativas simultâneas do mesmo retry ainda são uma mensagem", async () => {
    const id = await novoAtendimento();
    const cid = `rajada-${Date.now()}`;

    const rs = await Promise.all(
      Array.from({ length: 5 }, () =>
        enviarMensagem(ctxA, id, { content: "clique nervoso", clientMessageId: cid }),
      ),
    );

    expect(rs.every((r) => r.ok)).toBe(true);
    const ids = new Set(rs.map((r) => (r.ok ? r.value.id : "erro")));
    expect(ids.size, "deveria haver um id só").toBe(1);

    const pagina = await listarMensagens(ctxA, id);
    expect(pagina?.messages).toHaveLength(1);
  });
});
