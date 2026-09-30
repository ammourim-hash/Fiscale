/**
 * Apoio aos testes de isolamento.
 *
 * Dois caminhos de acesso, de proposito:
 *   - Prisma + withTenant()  — como a aplicacao ve o banco
 *   - pg cru como elo_app    — como o banco se comporta sem a aplicacao
 * Os dois precisam recusar o dado do outro tenant. Se so o primeiro
 * recusasse, o isolamento seria uma convencao de TypeScript.
 */
import { randomUUID } from "node:crypto";

import { Client } from "pg";

import { prisma } from "@/server/db/client";
import { provisionMembership, provisionTenant } from "@/server/admin/provisioning";
import { withTenant } from "@/server/tenancy";

export interface TenantFixture {
  id: string;
  slug: string;
  /** No MVP 1.1 o antigo `User` virou Membership; o nome do campo acompanha. */
  membershipId: string;
  identityId: string;
  email: string;
  contactId: string;
  contactPhone: string;
}

/** Sufixo por execucao: a suite pode rodar duas vezes sem colidir no unique. */
const RUN = randomUUID().slice(0, 8);

export async function criarTenant(rotulo: string): Promise<TenantFixture> {
  const slug = `${rotulo}-${RUN}`;
  const tenant = await provisionTenant({ slug, name: `Tenant ${rotulo}` });

  const email = `${rotulo}@${RUN}.teste`;
  const contactPhone = `5581${rotulo.charCodeAt(0)}${RUN.replace(/\D/g, "0").slice(0, 5)}`;

  const pessoa = await provisionMembership({
    tenantId: tenant.id,
    role: "ADMIN",
    email,
    name: `Pessoa ${rotulo}`,
    fiscaleUid: `${rotulo}.${RUN}`,
  });

  const contato = await withTenant(tenant.id, async (tx) =>
    tx.contact.create({
      data: { tenantId: tenant.id, phone: contactPhone, name: `Contato ${rotulo}` },
      select: { id: true },
    }),
  );

  return {
    id: tenant.id,
    slug,
    membershipId: pessoa.membershipId,
    identityId: pessoa.identityId,
    email,
    contactId: contato.id,
    contactPhone,
  };
}

/**
 * Remove o tenant e, por cascata, usuarios e contatos. A cascata roda na
 * checagem de integridade referencial, que nao passa pelo RLS — por isso
 * basta apagar a linha de `tenants` de dentro do proprio contexto.
 */
export async function removerTenant(id: string): Promise<void> {
  await withTenant(id, async (tx) => {
    await tx.tenant.delete({ where: { id } });
  });
}

export async function fecharPrisma(): Promise<void> {
  await prisma.$disconnect();
}

/**
 * Conexao crua com o papel elo_app — sem Prisma, sem withTenant, sem
 * nenhuma filtragem de aplicacao. E aqui que o RLS e testado sozinho.
 */
export async function conexaoApp(): Promise<Client> {
  const client = new Client({ connectionString: process.env.DATABASE_URL });
  await client.connect();
  return client;
}

/**
 * Conexao com o papel elo_owner — dono das tabelas. Existe so para o
 * controle negativo: mostrar que FORCE ROW LEVEL SECURITY tambem vale para
 * o dono. Sem FORCE, este seria o furo.
 */
export async function conexaoOwner(): Promise<Client> {
  const client = new Client({ connectionString: process.env.DATABASE_MIGRATION_URL });
  await client.connect();
  return client;
}

/**
 * Conexao com o superusuario. Usada exclusivamente como controle negativo:
 * confirma que as linhas dos dois tenants EXISTEM de fato. Sem isso, um
 * teste de isolamento passaria alegremente com a tabela vazia.
 *
 * A aplicacao nunca abre esta conexao. Superusuario ignora RLS — e o teste
 * abaixo prova exatamente isso, que e o motivo de o runtime nao ser um.
 */
export async function conexaoSuperusuario(): Promise<Client> {
  // Host, porta e banco saem da DATABASE_URL — a MESMA que a aplicacao usa.
  // Lidos de ELO_DB_NAME/ELO_DB_PORT, como era antes, esta conexao ia parar
  // no banco `elo` enquanto o resto da suite trabalhava no `elo_test`: o
  // controle negativo procurava as linhas no banco errado e nao achava nada.
  const alvo = new URL(process.env.DATABASE_URL ?? "");
  const banco = alvo.pathname.replace(/^\//, "");
  const senha = process.env.POSTGRES_SUPERUSER_PASSWORD;
  if (!senha) throw new Error("POSTGRES_SUPERUSER_PASSWORD ausente no .env");

  const client = new Client({
    host: alvo.hostname,
    port: Number(alvo.port),
    user: "postgres",
    password: senha,
    database: banco,
  });
  await client.connect();
  return client;
}

/** Executa SQL com o tenant fixado, no mesmo escopo transacional da aplicacao. */
export async function sqlComTenant<T = unknown>(
  client: Client,
  tenantId: string,
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  await client.query("BEGIN");
  try {
    await client.query("SELECT set_config('app.tenant_id', $1, true)", [tenantId]);
    const r = await client.query(sql, params);
    await client.query("COMMIT");
    return r.rows as T[];
  } catch (erro) {
    await client.query("ROLLBACK");
    throw erro;
  }
}

/** Executa SQL sem nenhum tenant definido. */
export async function sqlSemTenant<T = unknown>(
  client: Client,
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  await client.query("BEGIN");
  try {
    const r = await client.query(sql, params);
    await client.query("COMMIT");
    return r.rows as T[];
  } catch (erro) {
    await client.query("ROLLBACK");
    throw erro;
  }
}
