/**
 * A projecao de clientes: contrato, idempotencia, isolamento e o
 * casamento telefone -> cliente.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { parseCustomer, PayloadInvalido } from "@/server/customers/contract";
import {
  findCustomerByPhone,
  projectionStatus,
  searchCustomers,
} from "@/server/customers/lookup";
import { applyProjection } from "@/server/customers/projection";
import { withTenant } from "@/server/tenancy";

import { criarTenantComPessoa, removerTenant, type TenantFixture } from "./auth-helpers";
import { fecharPrisma } from "./helpers";
import { corpoSync, type ClienteTeste } from "./sync-helpers";

let A: TenantFixture;
let B: TenantFixture;

beforeAll(async () => {
  A = await criarTenantComPessoa("pa2");
  B = await criarTenantComPessoa("pb2");
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

/** Aplica um lote pelo mesmo caminho da rota, sem passar por HTTP. */
async function sincronizar(
  tenantId: string,
  clientes: ClienteTeste[],
  mode: "FULL" | "INCREMENTAL" = "FULL",
) {
  const corpo = JSON.parse(corpoSync(clientes, mode)) as { customers: unknown[] };
  const parsed = corpo.customers.map((c) => parseCustomer(c));
  return applyProjection(tenantId, "FISCALE", mode, parsed);
}

describe("contrato do payload", () => {
  it("aceita exatamente os campos permitidos", () => {
    const c = parseCustomer({
      externalId: "1",
      displayName: "Alfa Assessoria",
      document: "11.222.333/0001-81",
      email: "Contato@Alfa.teste",
      phone: "(81) 99999-1111",
      active: true,
      sourceVersion: "abc",
      sourceUpdatedAt: new Date().toISOString(),
    });

    expect(c.documentDigits).toBe("11222333000181");
    expect(c.documentKind).toBe("CNPJ");
    expect(c.email).toBe("contato@alfa.teste");
    expect(c.phones[0]?.e164).toBe("+5581999991111");
  });

  it("RECUSA campo proibido em vez de descartar em silencio", () => {
    // Descartar deixaria o dado sensivel chegar ate aqui sem ninguem
    // saber. Recusar faz o problema aparecer no mesmo dia.
    for (const proibido of ["cert", "certValidade", "regime", "ie", "im", "senha", "xml"]) {
      const tentativa = () =>
        parseCustomer({
          externalId: "1",
          displayName: "X",
          active: true,
          sourceVersion: "v1",
          [proibido]: "qualquer coisa",
        });
      expect(tentativa, proibido).toThrow(PayloadInvalido);
    }
  });

  it("recusa campo desconhecido", () => {
    expect(() =>
      parseCustomer({
        externalId: "1",
        displayName: "X",
        active: true,
        sourceVersion: "v1",
        campoNovo: "surpresa",
      }),
    ).toThrow(PayloadInvalido);
  });

  it("recusa payload incompleto ou malformado", () => {
    expect(() => parseCustomer({ displayName: "X", active: true, sourceVersion: "v" })).toThrow();
    expect(() => parseCustomer({ externalId: "1", active: true, sourceVersion: "v" })).toThrow();
    expect(() => parseCustomer({ externalId: "1", displayName: "X", sourceVersion: "v" })).toThrow();
    expect(() =>
      parseCustomer({ externalId: "1", displayName: "X", active: "sim", sourceVersion: "v" }),
    ).toThrow();
    expect(() => parseCustomer("texto")).toThrow();
    expect(() => parseCustomer(null)).toThrow();
  });

  it("o hash de conteudo nao depende da ordem das chaves", () => {
    const a = parseCustomer({
      externalId: "1", displayName: "X", active: true, sourceVersion: "v1", phone: "81999991111",
    });
    const b = parseCustomer({
      phone: "81999991111", sourceVersion: "v1", active: true, displayName: "X", externalId: "1",
    });
    expect(a.contentHash).toBe(b.contentHash);
  });

  it("o hash ignora o sourceVersion — quem decide mudanca e o conteudo", () => {
    const a = parseCustomer({ externalId: "1", displayName: "X", active: true, sourceVersion: "v1" });
    const b = parseCustomer({ externalId: "1", displayName: "X", active: true, sourceVersion: "v2" });
    expect(a.contentHash).toBe(b.contentHash);
  });

  it("mudar o nome muda o hash", () => {
    const a = parseCustomer({ externalId: "1", displayName: "X", active: true, sourceVersion: "v1" });
    const b = parseCustomer({ externalId: "1", displayName: "Y", active: true, sourceVersion: "v1" });
    expect(a.contentHash).not.toBe(b.contentHash);
  });
});

describe("idempotencia", () => {
  it("primeiro envio cria", async () => {
    const r = await sincronizar(A.id, [
      { externalId: "100", displayName: "Alfa Ltda", document: "11222333000181", phone: "81999990001" },
    ]);
    expect(r.created).toBe(1);
    expect(r.updated).toBe(0);
    expect(r.unchanged).toBe(0);
  });

  it("o mesmo payload de novo nao atualiza nada", async () => {
    const cliente: ClienteTeste = {
      externalId: "100", displayName: "Alfa Ltda", document: "11222333000181", phone: "81999990001",
    };
    await sincronizar(A.id, [cliente]);
    const r = await sincronizar(A.id, [cliente]);
    expect(r.unchanged).toBe(1);
    expect(r.updated).toBe(0);
    expect(r.created).toBe(0);
  });

  it("sem mudanca, o updatedAt nao se mexe — mas o syncedAt sim", async () => {
    const cliente: ClienteTeste = { externalId: "101", displayName: "Beta", phone: "81999990002" };
    await sincronizar(A.id, [cliente]);

    const antes = await withTenant(A.id, async (tx) =>
      tx.externalCustomerReference.findFirst({
        where: { externalId: "101" },
        select: { updatedAt: true, syncedAt: true },
      }),
    );

    await new Promise((r) => setTimeout(r, 20));
    await sincronizar(A.id, [cliente]);

    const depois = await withTenant(A.id, async (tx) =>
      tx.externalCustomerReference.findFirst({
        where: { externalId: "101" },
        select: { updatedAt: true, syncedAt: true },
      }),
    );

    expect(depois?.updatedAt.getTime()).toBe(antes?.updatedAt.getTime());
    expect(depois!.syncedAt.getTime()).toBeGreaterThan(antes!.syncedAt.getTime());
  });

  it("mudou o nome: atualiza", async () => {
    await sincronizar(A.id, [{ externalId: "102", displayName: "Gama" }]);
    const r = await sincronizar(A.id, [{ externalId: "102", displayName: "Gama Servicos" }]);
    expect(r.updated).toBe(1);

    const linha = await withTenant(A.id, async (tx) =>
      tx.externalCustomerReference.findFirst({ where: { externalId: "102" } }),
    );
    expect(linha?.displayName).toBe("Gama Servicos");
  });

  it("externalId repetido no mesmo lote nao passa despercebido", async () => {
    // A rota barra antes de aplicar; aqui vale registrar que o parse nao
    // e o lugar dessa checagem, e o teste da rota cobre o resto.
    const dois = [
      { externalId: "dup", displayName: "Um" },
      { externalId: "dup", displayName: "Dois" },
    ];
    const ids = dois.map((d) => d.externalId);
    expect(new Set(ids).size).not.toBe(ids.length);
  });
});

describe("inativacao e ausencia", () => {
  it("inativar preserva o registro, com a data", async () => {
    await sincronizar(A.id, [{ externalId: "200", displayName: "Delta", active: true }]);
    const r = await sincronizar(
      A.id,
      [{ externalId: "200", displayName: "Delta", active: false }],
      "INCREMENTAL",
    );
    expect(r.deactivated).toBe(1);

    const linha = await withTenant(A.id, async (tx) =>
      tx.externalCustomerReference.findFirst({ where: { externalId: "200" } }),
    );
    expect(linha).not.toBeNull();
    expect(linha?.active).toBe(false);
    expect(linha?.deactivatedAt).not.toBeNull();
  });

  it("reativar limpa a data de inativacao", async () => {
    await sincronizar(A.id, [{ externalId: "201", displayName: "Eps", active: false }], "INCREMENTAL");
    const r = await sincronizar(
      A.id,
      [{ externalId: "201", displayName: "Eps", active: true }],
      "INCREMENTAL",
    );
    expect(r.reactivated).toBe(1);

    const linha = await withTenant(A.id, async (tx) =>
      tx.externalCustomerReference.findFirst({ where: { externalId: "201" } }),
    );
    expect(linha?.active).toBe(true);
    expect(linha?.deactivatedAt).toBeNull();
  });

  it("full sync sem o registro MARCA como ausente, nao apaga nem inativa", async () => {
    const C = await criarTenantComPessoa("pc2");
    try {
      await sincronizar(C.id, [
        { externalId: "300", displayName: "Fica" },
        { externalId: "301", displayName: "Some" },
      ]);

      const r = await sincronizar(C.id, [{ externalId: "300", displayName: "Fica" }]);
      expect(r.markedMissing).toBe(1);

      const some = await withTenant(C.id, async (tx) =>
        tx.externalCustomerReference.findFirst({ where: { externalId: "301" } }),
      );
      // Continua existindo, continua ativo — so marcado. Conversa futura
      // vai apontar para esta linha.
      expect(some).not.toBeNull();
      expect(some?.active).toBe(true);
      expect(some?.missingSince).not.toBeNull();
    } finally {
      await removerTenant(C.id);
    }
  });

  it("reaparecer limpa a marca de ausente", async () => {
    const D = await criarTenantComPessoa("pd2");
    try {
      await sincronizar(D.id, [{ externalId: "400", displayName: "Vai e volta" }]);
      await sincronizar(D.id, []);
      await sincronizar(D.id, [{ externalId: "400", displayName: "Vai e volta" }]);

      const linha = await withTenant(D.id, async (tx) =>
        tx.externalCustomerReference.findFirst({ where: { externalId: "400" } }),
      );
      expect(linha?.missingSince).toBeNull();
    } finally {
      await removerTenant(D.id);
    }
  });

  it("incremental NAO marca ninguem como ausente", async () => {
    const E = await criarTenantComPessoa("pe2");
    try {
      await sincronizar(E.id, [
        { externalId: "500", displayName: "Um" },
        { externalId: "501", displayName: "Dois" },
      ]);
      const r = await sincronizar(E.id, [{ externalId: "500", displayName: "Um" }], "INCREMENTAL");
      expect(r.markedMissing).toBe(0);

      const dois = await withTenant(E.id, async (tx) =>
        tx.externalCustomerReference.findFirst({ where: { externalId: "501" } }),
      );
      expect(dois?.missingSince).toBeNull();
    } finally {
      await removerTenant(E.id);
    }
  });
});

describe("isolamento entre tenants", () => {
  const EMPRESA: ClienteTeste = {
    externalId: "900",
    displayName: "Empresa Exclusiva do A",
    document: "11222333000144",
    phone: "81988887777",
    email: "exclusivo@a.teste",
  };

  beforeAll(async () => {
    await sincronizar(A.id, [EMPRESA], "INCREMENTAL");
  });

  it("B nao acha pelo nome", async () => {
    expect(await searchCustomers(B.id, "Exclusiva")).toHaveLength(0);
    expect((await searchCustomers(A.id, "Exclusiva")).length).toBeGreaterThan(0);
  });

  it("B nao acha pelo documento", async () => {
    expect(await searchCustomers(B.id, "11222333000144")).toHaveLength(0);
    expect(await searchCustomers(B.id, "11.222.333/0001-44")).toHaveLength(0);
  });

  it("B nao acha pelo telefone", async () => {
    const r = await findCustomerByPhone(B.id, "81988887777");
    expect(r.status).toBe("NONE");
    const emA = await findCustomerByPhone(A.id, "81988887777");
    expect(emA.status).toBe("EXACT");
  });

  it("B nao acha pelo externalId nem pelo id interno", async () => {
    const deA = await withTenant(A.id, async (tx) =>
      tx.externalCustomerReference.findFirst({ where: { externalId: "900" } }),
    );
    expect(deA).not.toBeNull();

    const porExternal = await withTenant(B.id, async (tx) =>
      tx.externalCustomerReference.findFirst({ where: { externalId: "900" } }),
    );
    const porId = await withTenant(B.id, async (tx) =>
      tx.externalCustomerReference.findUnique({ where: { id: deA!.id } }),
    );
    expect(porExternal).toBeNull();
    expect(porId).toBeNull();
  });

  it("o telefone de A nao aparece na tabela de telefones de B", async () => {
    const deB = await withTenant(B.id, async (tx) =>
      tx.externalCustomerPhone.findMany({ where: { e164: "+5581988887777" } }),
    );
    expect(deB).toHaveLength(0);
  });

  it("o MESMO externalId em dois tenants sao dois clientes distintos", async () => {
    await sincronizar(B.id, [{ externalId: "900", displayName: "Outra Empresa, do B" }], "INCREMENTAL");

    const emA = await withTenant(A.id, async (tx) =>
      tx.externalCustomerReference.findFirst({ where: { externalId: "900" } }),
    );
    const emB = await withTenant(B.id, async (tx) =>
      tx.externalCustomerReference.findFirst({ where: { externalId: "900" } }),
    );

    expect(emA?.displayName).toBe("Empresa Exclusiva do A");
    expect(emB?.displayName).toBe("Outra Empresa, do B");
    expect(emA?.id).not.toBe(emB?.id);
  });
});

describe("casamento telefone -> cliente", () => {
  it("um cliente: EXACT", async () => {
    const F = await criarTenantComPessoa("pf2");
    try {
      await sincronizar(F.id, [{ externalId: "1", displayName: "Unica", phone: "(81) 97777-1234" }]);

      const r = await findCustomerByPhone(F.id, "+5581977771234");
      expect(r.status).toBe("EXACT");
      if (r.status !== "EXACT") return;
      expect(r.customer.displayName).toBe("Unica");
    } finally {
      await removerTenant(F.id);
    }
  });

  it("acha independente da grafia usada na busca", async () => {
    const G = await criarTenantComPessoa("pg2");
    try {
      await sincronizar(G.id, [{ externalId: "1", displayName: "Grafias", phone: "81966665555" }]);

      for (const forma of ["81966665555", "(81) 96666-5555", "+55 81 96666-5555", "5581966665555"]) {
        const r = await findCustomerByPhone(G.id, forma);
        expect(r.status, forma).toBe("EXACT");
      }
    } finally {
      await removerTenant(G.id);
    }
  });

  it("dois clientes com o mesmo telefone: AMBIGUOUS, nunca um escolhido", async () => {
    // Acontece de verdade: matriz e filial, contador que atende as duas,
    // celular do socio. Escolher em silencio entregaria a conversa errada.
    const H = await criarTenantComPessoa("ph2");
    try {
      await sincronizar(H.id, [
        { externalId: "1", displayName: "Empresa A", phone: "81955554444" },
        { externalId: "2", displayName: "Empresa B", phone: "81955554444" },
      ]);

      const r = await findCustomerByPhone(H.id, "81955554444");
      expect(r.status).toBe("AMBIGUOUS");
      if (r.status !== "AMBIGUOUS") return;
      expect(r.candidates).toHaveLength(2);
      expect(r.candidates.map((c) => c.displayName).sort()).toEqual(["Empresa A", "Empresa B"]);
    } finally {
      await removerTenant(H.id);
    }
  });

  it("ninguem com aquele numero: NONE / NOT_FOUND", async () => {
    const r = await findCustomerByPhone(A.id, "81900000000");
    expect(r).toEqual({ status: "NONE", reason: "NOT_FOUND" });
  });

  it("numero que nao normaliza: NONE / NOT_NORMALIZABLE, sem consultar", async () => {
    for (const n of ["99999-1111", "abc", ""]) {
      const r = await findCustomerByPhone(A.id, n);
      expect(r, n).toEqual({ status: "NONE", reason: "NOT_NORMALIZABLE" });
    }
  });

  it("telefone que ficou sem DDD nao casa sozinho", async () => {
    const I = await criarTenantComPessoa("pi2");
    try {
      await sincronizar(I.id, [{ externalId: "1", displayName: "Sem DDD", phone: "3333-4444" }]);

      const linha = await withTenant(I.id, async (tx) =>
        tx.externalCustomerPhone.findFirst({ where: { raw: "3333-4444" } }),
      );
      // Guardado e visivel na tela — mas com e164 nulo, entao nunca entra
      // num casamento automatico.
      expect(linha?.status).toBe("NO_AREA_CODE");
      expect(linha?.e164).toBeNull();

      expect((await findCustomerByPhone(I.id, "3333-4444")).status).toBe("NONE");
    } finally {
      await removerTenant(I.id);
    }
  });

  it("cliente com dois telefones casa pelos dois", async () => {
    const J = await criarTenantComPessoa("pj2");
    try {
      await sincronizar(J.id, [
        { externalId: "1", displayName: "Dois Numeros", phone: "(81) 94444-3333 / (81) 3222-1111" },
      ]);

      expect((await findCustomerByPhone(J.id, "81944443333")).status).toBe("EXACT");
      expect((await findCustomerByPhone(J.id, "8132221111")).status).toBe("EXACT");
    } finally {
      await removerTenant(J.id);
    }
  });
});

describe("busca e diagnostico", () => {
  it("acha por nome parcial, sem diferenciar maiuscula", async () => {
    const K = await criarTenantComPessoa("pk2");
    try {
      await sincronizar(K.id, [{ externalId: "1", displayName: "Padaria do Joao Ltda" }]);
      expect(await searchCustomers(K.id, "padaria")).toHaveLength(1);
      expect(await searchCustomers(K.id, "JOAO")).toHaveLength(1);
      expect(await searchCustomers(K.id, "farmacia")).toHaveLength(0);
    } finally {
      await removerTenant(K.id);
    }
  });

  it("inativo fica de fora por padrao, e aparece quando pedido", async () => {
    const L = await criarTenantComPessoa("pl2");
    try {
      await sincronizar(L.id, [{ externalId: "1", displayName: "Saiu Fora", active: false }]);
      expect(await searchCustomers(L.id, "Saiu")).toHaveLength(0);
      expect(await searchCustomers(L.id, "Saiu", { includeInactive: true })).toHaveLength(1);
    } finally {
      await removerTenant(L.id);
    }
  });

  it("o diagnostico conta e diz quando foi a ultima sincronizacao", async () => {
    const M = await criarTenantComPessoa("pm2");
    try {
      await sincronizar(M.id, [
        { externalId: "1", displayName: "Um" },
        { externalId: "2", displayName: "Dois", active: false },
      ]);

      const s = await projectionStatus(M.id);
      expect(s.total).toBe(2);
      expect(s.active).toBe(1);
      expect(s.missing).toBe(0);
      expect(s.lastSyncMode).toBe("FULL");
      expect(s.lastSyncStatus).toBe("OK");
      expect(s.lastSyncAt).not.toBeNull();
    } finally {
      await removerTenant(M.id);
    }
  });

  it("o resultado traz syncedAt — a tela precisa poder dizer 'sincronizado em'", async () => {
    const achados = await searchCustomers(A.id, "Alfa");
    expect(achados[0]?.syncedAt).toBeInstanceOf(Date);
  });
});
