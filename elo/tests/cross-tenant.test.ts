/**
 * Isolamento entre tenants pelo caminho da aplicacao (Prisma + withTenant).
 *
 * Criterio de reprovacao do MVP 1.0: se qualquer teste deste arquivo mostrar
 * dado de um tenant chegando ao outro, a fundacao esta reprovada.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { TenantContextError } from "@/server/errors/app-error";
import { withTenant } from "@/server/tenancy";

import { criarTenant, fecharPrisma, removerTenant, type TenantFixture } from "./helpers";

let A: TenantFixture;
let B: TenantFixture;

beforeAll(async () => {
  A = await criarTenant("a");
  B = await criarTenant("b");
});

afterAll(async () => {
  await removerTenant(A.id);
  await removerTenant(B.id);
  await fecharPrisma();
});

describe("o tenant enxerga o que e dele", () => {
  it("A le os proprios contato e usuario", async () => {
    const visto = await withTenant(A.id, async (tx) => ({
      contatos: await tx.contact.findMany(),
      usuarios: await tx.membership.findMany(),
      tenant: await tx.tenant.findFirst(),
    }));

    expect(visto.contatos).toHaveLength(1);
    expect(visto.contatos[0]?.id).toBe(A.contactId);
    expect(visto.usuarios[0]?.id).toBe(A.membershipId);
    expect(visto.tenant?.id).toBe(A.id);
  });

  it("B le os proprios, e nao os de A", async () => {
    const visto = await withTenant(B.id, async (tx) => tx.contact.findMany());

    expect(visto).toHaveLength(1);
    expect(visto[0]?.id).toBe(B.contactId);
    expect(visto.map((c) => c.id)).not.toContain(A.contactId);
  });
});

describe("o tenant nao enxerga o do outro", () => {
  it("A nao ve nenhum registro de B numa listagem sem filtro", async () => {
    const { contatos, usuarios, tenants } = await withTenant(A.id, async (tx) => ({
      // Sem `where` de proposito: e exatamente a consulta que um bug
      // escreveria. Quem filtra e o banco.
      contatos: await tx.contact.findMany(),
      usuarios: await tx.membership.findMany(),
      tenants: await tx.tenant.findMany(),
    }));

    expect(contatos.every((c) => c.tenantId === A.id)).toBe(true);
    expect(usuarios.every((u) => u.tenantId === A.id)).toBe(true);
    expect(tenants).toHaveLength(1);
    expect(tenants[0]?.id).toBe(A.id);
  });

  it("A buscando o id conhecido de um contato de B nao encontra nada", async () => {
    const achado = await withTenant(A.id, async (tx) =>
      tx.contact.findUnique({ where: { id: B.contactId } }),
    );
    expect(achado).toBeNull();
  });

  it("B buscando o id conhecido de um usuario de A nao encontra nada", async () => {
    const achado = await withTenant(B.id, async (tx) =>
      tx.membership.findUnique({ where: { id: A.membershipId } }),
    );
    expect(achado).toBeNull();
  });

  it("A buscando o tenant B pelo id nao encontra nada", async () => {
    const achado = await withTenant(A.id, async (tx) =>
      tx.tenant.findUnique({ where: { id: B.id } }),
    );
    expect(achado).toBeNull();
  });

  it("A nao consegue alterar registro de B", async () => {
    const alterados = await withTenant(A.id, async (tx) =>
      tx.contact.updateMany({
        where: { id: B.contactId },
        data: { name: "invadido" },
      }),
    );
    expect(alterados.count).toBe(0);

    const intacto = await withTenant(B.id, async (tx) =>
      tx.contact.findUnique({ where: { id: B.contactId } }),
    );
    expect(intacto?.name).toBe("Contato b");
  });

  it("A nao consegue apagar registro de B", async () => {
    const apagados = await withTenant(A.id, async (tx) =>
      tx.contact.deleteMany({ where: { id: B.contactId } }),
    );
    expect(apagados.count).toBe(0);

    const aindaExiste = await withTenant(B.id, async (tx) =>
      tx.contact.findUnique({ where: { id: B.contactId } }),
    );
    expect(aindaExiste).not.toBeNull();
  });

  it("A nao consegue gravar linha carimbada com o tenant de B", async () => {
    // O WITH CHECK da politica barra a escrita, nao so a leitura.
    await expect(
      withTenant(A.id, async (tx) =>
        tx.contact.create({
          data: { tenantId: B.id, phone: "5581000000000", name: "plantado" },
        }),
      ),
    ).rejects.toThrow();

    const contatosDeB = await withTenant(B.id, async (tx) => tx.contact.findMany());
    expect(contatosDeB).toHaveLength(1);
  });
});

describe("sem tenant valido, nada", () => {
  it("string vazia e recusada antes de abrir transacao", async () => {
    await expect(withTenant("", async (tx) => tx.contact.findMany())).rejects.toBeInstanceOf(
      TenantContextError,
    );
  });

  it("uuid malformado e recusado", async () => {
    await expect(
      withTenant("nao-e-uuid", async (tx) => tx.contact.findMany()),
    ).rejects.toBeInstanceOf(TenantContextError);
  });

  it("uuid valido de tenant inexistente nao devolve dado de ninguem", async () => {
    const inexistente = "00000000-0000-4000-8000-000000000000";
    const visto = await withTenant(inexistente, async (tx) => ({
      contatos: await tx.contact.findMany(),
      usuarios: await tx.membership.findMany(),
      tenants: await tx.tenant.findMany(),
    }));

    expect(visto.contatos).toHaveLength(0);
    expect(visto.usuarios).toHaveLength(0);
    expect(visto.tenants).toHaveLength(0);
  });

  it("o erro de contexto nao conta ao cliente qual foi o problema", () => {
    const erro = new TenantContextError("faltou tenant na sessao X");
    expect(erro.publicMessage).toBe("Acesso negado.");
    expect(erro.publicMessage).not.toContain("tenant");
  });
});
