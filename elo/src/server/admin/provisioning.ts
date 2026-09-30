/**
 * Provisionamento — ferramenta administrativa, NAO rota.
 *
 * ---------------------------------------------------------------------
 *  O debito tecnico que este arquivo paga
 * ---------------------------------------------------------------------
 *  O MVP 1.0 deixou `provisionTenant` solta, sem rota e sem autorizacao.
 *  A resposta desta fase nao e criar um endpoint de "criar tenant": um
 *  escritorio novo nasce quando alguem contrata o sistema, o que acontece
 *  algumas vezes por ano e por decisao humana. Expor isso na web seria
 *  criar superficie de ataque para uma operacao que nao precisa dela.
 *
 *  Entao vira ferramenta de linha de comando (scripts/admin.ts), com uma
 *  trava explicita: sem ELO_ADMIN_TOOL=1 no ambiente, nada aqui roda.
 *  Se um dia alguem importar estas funcoes de dentro de uma rota, o
 *  processo web nao tem essa variavel e a chamada falha alto, na hora —
 *  em vez de funcionar em silencio.
 *
 * ---------------------------------------------------------------------
 *  Tudo isto roda como elo_app
 * ---------------------------------------------------------------------
 *  Nenhuma funcao aqui usa papel privilegiado, e isso e proposital: o
 *  modelo de seguranca continua sendo um so. Duas consequencias visiveis:
 *
 *  - A identidade e inserida com SQL sem RETURNING. A politica so deixa
 *    LER identidade que ja tenha vinculo no tenant corrente, e o vinculo
 *    ainda nao existe no instante do INSERT. Prisma sempre usa RETURNING,
 *    entao aqui e SQL puro com o uuid gerado antes.
 *
 *  - Para colocar alguem que JA existe num segundo tenant, e preciso
 *    informar `identityId`. O tenant novo nao enxerga identidade sem
 *    vinculo — nem pelo e-mail. Isso e o isolamento funcionando, nao um
 *    defeito: descobrir "esse e-mail existe no sistema" a partir de um
 *    tenant que nao conhece a pessoa seria vazamento.
 */
import { randomUUID } from "node:crypto";

import { newTenantId, withTenant } from "@/server/tenancy";
import { AppError } from "@/server/errors/app-error";
import type { Role } from "@/generated/prisma/enums";

const PROVEDOR_FISCALE = "fiscale";

/**
 * A trava. Barata, e o bastante para transformar "exposto por engano" em
 * "quebra na primeira chamada".
 */
export function assertAdminTool(): void {
  if (process.env.ELO_ADMIN_TOOL !== "1") {
    throw new AppError(
      "FORBIDDEN",
      "provisionamento so roda na ferramenta administrativa (ELO_ADMIN_TOOL=1)",
    );
  }
}

export interface ProvisionedTenant {
  id: string;
  slug: string;
  name: string;
}

export async function provisionTenant(dados: {
  slug: string;
  name: string;
}): Promise<ProvisionedTenant> {
  assertAdminTool();
  const id = newTenantId();

  return withTenant(id, async (tx) =>
    tx.tenant.create({
      data: { id, slug: dados.slug, name: dados.name },
      select: { id: true, slug: true, name: true },
    }),
  );
}

export interface ProvisionMembershipInput {
  tenantId: string;
  role: Role;
  /** Pessoa nova: informe e-mail, nome e o login dela no Fiscale. */
  email?: string;
  name?: string;
  fiscaleUid?: string;
  /**
   * Pessoa que ja existe (entrando num segundo tenant): informe o id da
   * identidade. Obtido a partir de um tenant onde ela ja tem vinculo.
   */
  identityId?: string;
}

export interface ProvisionedMembership {
  membershipId: string;
  identityId: string;
  tenantId: string;
  role: Role;
}

export async function provisionMembership(
  input: ProvisionMembershipInput,
): Promise<ProvisionedMembership> {
  assertAdminTool();

  const identidadeNova = input.identityId === undefined;
  if (identidadeNova && (!input.email || !input.name || !input.fiscaleUid)) {
    throw new AppError(
      "VALIDATION",
      "para criar identidade nova informe email, name e fiscaleUid; para reaproveitar uma existente informe identityId",
    );
  }

  const identityId = input.identityId ?? randomUUID();

  return withTenant(input.tenantId, async (tx) => {
    if (identidadeNova) {
      // Sem RETURNING: ver a nota no topo do arquivo.
      await tx.$executeRaw`
        INSERT INTO identities (id, email, name, active, created_at)
        VALUES (${identityId}::uuid, ${input.email!.trim().toLowerCase()}, ${input.name!}, true, now())
      `;
    }

    const membership = await tx.membership.create({
      data: { tenantId: input.tenantId, identityId, role: input.role },
      select: { id: true, role: true },
    });

    if (identidadeNova) {
      // Agora existe vinculo neste tenant, entao a politica de leitura da
      // identidade passa e o Prisma pode usar RETURNING normalmente.
      await tx.identityProvider.create({
        data: {
          identityId,
          provider: PROVEDOR_FISCALE,
          providerUid: input.fiscaleUid!.trim().toLowerCase(),
        },
      });
    }

    return {
      membershipId: membership.id,
      identityId,
      tenantId: input.tenantId,
      role: membership.role as Role,
    };
  });
}

export async function createDepartment(dados: {
  tenantId: string;
  slug: string;
  name: string;
}): Promise<{ id: string; slug: string; name: string }> {
  assertAdminTool();

  return withTenant(dados.tenantId, async (tx) =>
    tx.department.create({
      data: { tenantId: dados.tenantId, slug: dados.slug, name: dados.name },
      select: { id: true, slug: true, name: true },
    }),
  );
}

/**
 * Coloca a pessoa num departamento, opcionalmente marcando-o como o
 * PRINCIPAL — o que aparece ao lado do nome nas mensagens.
 *
 * Marcar o principal e um ato deliberado de quem administra. O sistema
 * nao elege sozinho: a assinatura que o cliente le nao pode mudar porque
 * alguem reordenou um cadastro.
 */
export async function assignDepartment(dados: {
  tenantId: string;
  membershipId: string;
  departmentId: string;
  primary?: boolean;
}): Promise<{ membershipId: string; departmentId: string; primary: boolean }> {
  assertAdminTool();

  return withTenant(dados.tenantId, async (tx) => {
    await tx.departmentMembership.upsert({
      where: {
        departmentId_membershipId: {
          departmentId: dados.departmentId,
          membershipId: dados.membershipId,
        },
      },
      create: {
        tenantId: dados.tenantId,
        departmentId: dados.departmentId,
        membershipId: dados.membershipId,
      },
      update: {},
    });

    if (dados.primary) {
      await tx.membership.update({
        where: { id: dados.membershipId },
        data: { primaryDepartmentId: dados.departmentId },
      });
    }

    return {
      membershipId: dados.membershipId,
      departmentId: dados.departmentId,
      primary: dados.primary === true,
    };
  });
}

/* ─── departamentos em lote, de forma idempotente ────────────────────── */

export interface SetorDesejado {
  slug: string;
  name: string;
}

export interface PlanoDeDepartamentos {
  tenantId: string;
  /** Nulo quando o tenant nao existe — e ai nao ha plano nenhum. */
  tenantName: string | null;
  /** Tudo que o escritorio ja tem hoje. */
  existentes: { slug: string; name: string }[];
  /** O que seria criado. */
  criar: SetorDesejado[];
  /** O que ja existe e fica como esta, inclusive o nome. */
  preservar: { slug: string; nomeAtual: string; nomeProposto: string }[];
}

/**
 * O PLANO, sem escrever nada.
 *
 * Existe separado de quem aplica para que o ensaio e a execucao leiam o
 * mesmo codigo: um dry-run que calcula o plano por um caminho e aplica
 * por outro mente na primeira divergencia entre os dois.
 */
export async function planejarDepartamentos(
  tenantId: string,
  desejados: SetorDesejado[],
): Promise<PlanoDeDepartamentos> {
  assertAdminTool();

  return withTenant(tenantId, async (tx) => {
    const tenant = await tx.tenant.findUnique({
      where: { id: tenantId },
      select: { name: true },
    });

    const existentes = await tx.department.findMany({
      select: { slug: true, name: true },
      orderBy: { slug: "asc" },
    });

    const porSlug = new Map(existentes.map((d) => [d.slug, d]));

    return {
      tenantId,
      tenantName: tenant?.name ?? null,
      existentes,
      criar: desejados.filter((d) => !porSlug.has(d.slug)),
      preservar: desejados
        .filter((d) => porSlug.has(d.slug))
        .map((d) => ({
          slug: d.slug,
          nomeAtual: porSlug.get(d.slug)!.name,
          nomeProposto: d.name,
        })),
    };
  });
}

/**
 * Cria o que falta. Nao toca no que existe.
 *
 * ---------------------------------------------------------------------
 *  Por que NAO renomeia
 * ---------------------------------------------------------------------
 *  Se o escritorio chamou o setor de "Contabilidade" em vez de
 *  "Contabil", esse nome e dele. A triagem casa pelo `slug`, nunca pelo
 *  nome, entao renomear nao melhoraria nada e reescreveria uma escolha
 *  que alguem fez de proposito.
 *
 *  Idempotente de verdade: rodar duas vezes seguidas nao cria nada na
 *  segunda, e o conflito de unico que escapar pela corrida e lido como
 *  "ja existe", nao como erro.
 */
export async function garantirDepartamentos(
  tenantId: string,
  desejados: SetorDesejado[],
): Promise<{ criados: SetorDesejado[]; preservados: string[] }> {
  assertAdminTool();

  const plano = await planejarDepartamentos(tenantId, desejados);
  if (plano.tenantName === null) {
    throw new AppError("NOT_FOUND", `tenant ${tenantId} nao encontrado`);
  }

  const criados: SetorDesejado[] = [];
  for (const d of plano.criar) {
    try {
      await createDepartment({ tenantId, slug: d.slug, name: d.name });
      criados.push(d);
    } catch (erro: unknown) {
      const codigo =
        typeof erro === "object" && erro !== null && "code" in erro
          ? (erro as { code?: unknown }).code
          : null;
      // P2002 = alguem criou o mesmo slug entre o plano e agora. O
      // resultado desejado ja e o que esta no banco.
      if (codigo !== "P2002") throw erro;
    }
  }

  return { criados, preservados: plano.preservar.map((p) => p.slug) };
}
