/**
 * Admin > Equipe — as pessoas do escritório, administradas de dentro do Elo.
 *
 * =====================================================================
 *  Nada de modelo novo
 * =====================================================================
 *  Pessoa continua sendo `Identity` + `Membership` + `IdentityProvider`,
 *  exatamente como a CLI sempre criou. Este arquivo é a mesma receita sob
 *  outra autorização — e não um segundo jeito de existir no sistema.
 *
 * =====================================================================
 *  Por que NÃO chama `provisionMembership`
 * =====================================================================
 *  Aquela função começa com `assertAdminTool()`, que exige
 *  `ELO_ADMIN_TOOL=1`. A variável não existe no processo web, e isso é
 *  proteção, não descuido: ela transforma "exposto por engano" em "quebra
 *  na primeira chamada". Afrouxá-la derrubaria a trava do módulo inteiro
 *  de provisionamento para ganhar uma chamada.
 *
 *  Então o caminho web tem a sua própria porta — `requirePermission` —, e
 *  repete a sequência de escrita. A duplicação é consciente e pequena; o
 *  que NÃO se duplica é a decisão de quem pode entrar.
 *
 * =====================================================================
 *  A ordem de escrita importa, e é a mesma da CLI
 * =====================================================================
 *      1. INSERT cru em `identities`  (sem RETURNING)
 *      2. `Membership`                 → agora o RLS deixa ler a identidade
 *      3. `IdentityProvider`           → o login do Fiscale
 *
 *  Inverter 1 e 2 é impossível; ler a identidade antes do passo 2 devolve
 *  vazio, porque a política só a enxerga quando há vínculo neste tenant.
 */
import { randomUUID } from "node:crypto";

import { requirePermission, type AuthContext } from "@/server/auth/context";
import { roleHas } from "@/server/auth/permissions";
import { AppError } from "@/server/errors/app-error";
import { filaElegivel, PAPEL_DE_COORDENACAO } from "@/server/notifications/recipients";
import { withTenant } from "@/server/tenancy";
import type { Role } from "@/generated/prisma/enums";

const PROVEDOR_FISCALE = "fiscale";

/* ─── leitura ────────────────────────────────────────────────────────── */

export interface PessoaDaEquipe {
  membershipId: string;
  identityId: string;
  name: string;
  email: string;
  role: Role;
  active: boolean;
  identityActive: boolean;
  availableForAssignment: boolean;
  visibleToCustomers: boolean;
  fiscaleUid: string | null;
  departments: { id: string; slug: string; name: string }[];
  primaryDepartmentId: string | null;
}

export async function listarEquipe(ctx: AuthContext): Promise<PessoaDaEquipe[]> {
  requirePermission(ctx, "users.read");

  const linhas = await withTenant(ctx.tenantId, async (tx) =>
    tx.membership.findMany({
      select: {
        id: true,
        identityId: true,
        role: true,
        active: true,
        availableForAssignment: true,
        visibleToCustomers: true,
        primaryDepartmentId: true,
        identity: {
          select: {
            name: true,
            email: true,
            active: true,
            providers: {
              where: { provider: PROVEDOR_FISCALE },
              select: { providerUid: true },
              take: 1,
            },
          },
        },
        departments: {
          select: { department: { select: { id: true, slug: true, name: true } } },
        },
      },
      orderBy: { createdAt: "asc" },
    }),
  );

  return linhas.map((m) => ({
    membershipId: m.id,
    identityId: m.identityId,
    name: m.identity.name,
    email: m.identity.email,
    role: m.role as Role,
    active: m.active,
    identityActive: m.identity.active,
    availableForAssignment: m.availableForAssignment,
    visibleToCustomers: m.visibleToCustomers,
    fiscaleUid: m.identity.providers[0]?.providerUid ?? null,
    departments: m.departments
      .map((d) => d.department)
      .sort((a, b) => a.name.localeCompare(b.name, "pt-BR")),
    primaryDepartmentId: m.primaryDepartmentId,
  }));
}

export async function listarDepartamentos(
  ctx: AuthContext,
): Promise<{ id: string; slug: string; name: string }[]> {
  requirePermission(ctx, "departments.read");

  return withTenant(ctx.tenantId, async (tx) =>
    tx.department.findMany({
      select: { id: true, slug: true, name: true },
      orderBy: { name: "asc" },
    }),
  );
}

/* ─── cobertura ──────────────────────────────────────────────────────── */

export interface CoberturaDeSetor {
  departmentId: string;
  slug: string;
  name: string;
  /** Quem REALMENTE seria avisado. Ver o comentário abaixo. */
  elegiveis: number;
  /** Desses, quantos têm papel de coordenação. */
  gestores: number;
  vinculados: number;
}

/**
 * Quantas pessoas de fato receberiam um atendimento novo em cada área.
 *
 * A fonte é `filaElegivel()` — a MESMA função que o aviso usa —, e não um
 * `count` de `DepartmentMembership`. A diferença não é estética: a
 * contagem crua diria "3 no Fiscal" com as três de férias
 * (`availableForAssignment = false`), com o vínculo desativado ou sem
 * permissão de leitura. O número que interessa é o de quem o sistema
 * encontraria na hora, e só a fila responde isso.
 *
 * `vinculados` vai junto exatamente para tornar a diferença visível: "5
 * vinculados, 2 elegíveis" é uma informação, não um erro de conta.
 */
export async function coberturaDosSetores(ctx: AuthContext): Promise<CoberturaDeSetor[]> {
  requirePermission(ctx, "users.read");

  return withTenant(ctx.tenantId, async (tx) => {
    const areas = await tx.department.findMany({
      select: { id: true, slug: true, name: true },
      orderBy: { name: "asc" },
    });

    const cobertura: CoberturaDeSetor[] = [];

    for (const area of areas) {
      // `excluir` nulo: aqui ninguém é o autor do evento — queremos a
      // fila inteira, como ela estaria diante de um caso novo.
      // `semGestao`: "elegíveis" precisa significar quem REALMENTE
      // receberia o aviso. Sem isto a tela contaria a coordenação junto e
      // diria "2 elegíveis" onde só uma pessoa seria avisada.
      const fila = await filaElegivel(tx, area.id, null, { semGestao: true });

      // A coordenação não está mais na fila, então precisa de consulta
      // própria: os dois números medem coisas diferentes e não dá para
      // tirar um do outro.
      const gestores = await tx.membership.findMany({
        where: {
          active: true,
          role: PAPEL_DE_COORDENACAO,
          identity: { is: { active: true } },
          departments: { some: { departmentId: area.id } },
        },
        select: { id: true },
      });

      const vinculados = await tx.departmentMembership.count({
        where: { departmentId: area.id },
      });

      cobertura.push({
        departmentId: area.id,
        slug: area.slug,
        name: area.name,
        elegiveis: fila.length,
        // Só MANAGER: é o papel que coordena. Contagem, não privilégio —
        // não há regra de distribuição própria para ele além do
        // escalonamento.
        gestores: gestores.length,
        vinculados,
      });
    }

    return cobertura;
  });
}



/* ─── criação ────────────────────────────────────────────────────────── */

export interface NovaPessoa {
  name: string;
  email: string;
  /** O login do FISCALE. NÃO é o e-mail — ver `exchange.ts`. */
  fiscaleUid: string;
  role: Role;
  active?: boolean | undefined;
  availableForAssignment?: boolean | undefined;
  visibleToCustomers?: boolean | undefined;
  departmentIds?: string[] | undefined;
  primaryDepartmentId?: string | null | undefined;
}

export async function criarPessoa(
  ctx: AuthContext,
  dados: NovaPessoa,
): Promise<PessoaDaEquipe> {
  requirePermission(ctx, "users.manage");

  const email = dados.email.trim().toLowerCase();
  const uid = dados.fiscaleUid.trim().toLowerCase();
  const nome = dados.name.trim();

  if (!email || !uid || !nome) {
    throw new AppError("VALIDATION", "nome, e-mail e login do Fiscale são obrigatórios");
  }

  const areas = dados.departmentIds ?? [];
  conferirPrincipal(areas, dados.primaryDepartmentId ?? null);

  const identityId = randomUUID();

  const membershipId = await withTenant(ctx.tenantId, async (tx) => {
    await conferirAreas(tx, areas);

    try {
      // Sem RETURNING: a política de leitura da identidade só passa
      // depois que existe vínculo neste tenant.
      await tx.$executeRaw`
        INSERT INTO identities (id, email, name, active, created_at)
        VALUES (${identityId}::uuid, ${email}, ${nome}, true, now())
      `;
    } catch (erro: unknown) {
      throw conflitoDeEmail(erro, email);
    }

    const m = await tx.membership.create({
      data: {
        tenantId: ctx.tenantId,
        identityId,
        role: dados.role,
        active: dados.active ?? true,
        availableForAssignment: dados.availableForAssignment ?? true,
        // Padrão FALSE, como o schema manda: ninguém é exposto ao cliente
        // por ter sido cadastrado.
        visibleToCustomers: dados.visibleToCustomers ?? false,
      },
      select: { id: true },
    });

    try {
      await tx.identityProvider.create({
        data: { identityId, provider: PROVEDOR_FISCALE, providerUid: uid },
      });
    } catch (erro: unknown) {
      throw conflitoDeLogin(erro, uid);
    }

    await gravarAreas(tx, ctx.tenantId, m.id, areas, dados.primaryDepartmentId ?? null);

    return m.id;
  });

  return (await umaPessoa(ctx, membershipId))!;
}

/* ─── edição ─────────────────────────────────────────────────────────── */

export interface MudancasNaPessoa {
  name?: string | undefined;
  role?: Role | undefined;
  active?: boolean | undefined;
  availableForAssignment?: boolean | undefined;
  visibleToCustomers?: boolean | undefined;
}

/**
 * Edita papel, situação e as duas chaves de distribuição.
 *
 * A trava do próprio pé: ninguém pode, na mesma operação, tirar de si o
 * poder de administrar — nem trocando o papel, nem se desativando. Sem
 * isso, um OWNER que se rebaixa a VIEWER tranca o escritório com todo
 * mundo do lado de fora, e o conserto vira SQL na mão.
 *
 * Não é regra de papel nova: a pergunta é feita com `roleHas`, a mesma
 * tabela de sempre.
 */
export async function editarPessoa(
  ctx: AuthContext,
  membershipId: string,
  mudancas: MudancasNaPessoa,
): Promise<PessoaDaEquipe> {
  requirePermission(ctx, "users.manage");

  const souEu = membershipId === ctx.membershipId;

  if (souEu && mudancas.role !== undefined && !roleHas(mudancas.role, "users.manage")) {
    throw new AppError(
      "VALIDATION",
      "você perderia a administração da equipe nesta mesma operação",
      {
        publicMessage:
          "Você não pode tirar de si o acesso de administrar a equipe. Peça a outra pessoa com esse acesso.",
      },
    );
  }

  if (souEu && mudancas.active === false) {
    throw new AppError("VALIDATION", "desativar a si mesmo", {
      publicMessage: "Você não pode desativar o próprio acesso.",
    });
  }

  const nome = mudancas.name?.trim();

  await withTenant(ctx.tenantId, async (tx) => {
    const alvo = await tx.membership.findUnique({
      where: { id: membershipId },
      select: { identityId: true },
    });
    if (!alvo) throw new AppError("NOT_FOUND", "pessoa não encontrada");

    await tx.membership.update({
      where: { id: membershipId },
      data: {
        ...(mudancas.role !== undefined ? { role: mudancas.role } : {}),
        ...(mudancas.active !== undefined ? { active: mudancas.active } : {}),
        ...(mudancas.availableForAssignment !== undefined
          ? { availableForAssignment: mudancas.availableForAssignment }
          : {}),
        ...(mudancas.visibleToCustomers !== undefined
          ? { visibleToCustomers: mudancas.visibleToCustomers }
          : {}),
      },
    });

    if (nome) {
      await tx.identity.update({ where: { id: alvo.identityId }, data: { name: nome } });
    }
  });

  return (await umaPessoa(ctx, membershipId))!;
}

/* ─── departamentos ──────────────────────────────────────────────────── */

/**
 * Substitui o CONJUNTO de áreas da pessoa.
 *
 * Conjunto, e não "adicionar": a tela mostra caixas marcadas, e salvar
 * precisa significar exatamente o que está na tela. Some-se a isso que
 * até aqui não existia caminho nenhum para DESVINCULAR — `assignDepartment`
 * só sabe criar —, e tirar alguém de um setor dependia de SQL na mão.
 *
 * A remoção é estreita de propósito: apaga apenas vínculos DESTA pessoa e
 * apenas os que saíram da seleção. E se a área principal for uma das que
 * saem, ela cai junto — principal sem vínculo seria uma assinatura de uma
 * área a que a pessoa não pertence mais.
 */
export async function definirDepartamentos(
  ctx: AuthContext,
  membershipId: string,
  dados: { departmentIds: string[]; primaryDepartmentId?: string | null | undefined },
): Promise<PessoaDaEquipe> {
  requirePermission(ctx, "users.manage");

  const desejados = [...new Set(dados.departmentIds)];
  const principal = dados.primaryDepartmentId ?? null;
  conferirPrincipal(desejados, principal);

  await withTenant(ctx.tenantId, async (tx) => {
    const alvo = await tx.membership.findUnique({
      where: { id: membershipId },
      select: { id: true },
    });
    if (!alvo) throw new AppError("NOT_FOUND", "pessoa não encontrada");

    await conferirAreas(tx, desejados);

    const atuais = await tx.departmentMembership.findMany({
      where: { membershipId },
      select: { departmentId: true },
    });
    const tinha = new Set(atuais.map((a) => a.departmentId));

    const remover = [...tinha].filter((id) => !desejados.includes(id));
    if (remover.length > 0) {
      await tx.departmentMembership.deleteMany({
        where: { membershipId, departmentId: { in: remover } },
      });
    }

    await gravarAreas(tx, ctx.tenantId, membershipId, desejados, principal, tinha);
  });

  return (await umaPessoa(ctx, membershipId))!;
}

/* ─── apoio ──────────────────────────────────────────────────────────── */

type Tx = Parameters<Parameters<typeof withTenant>[1]>[0];

/** Principal fora da seleção seria assinar por uma área a que não se pertence. */
function conferirPrincipal(areas: string[], principal: string | null): void {
  if (principal && !areas.includes(principal)) {
    throw new AppError("VALIDATION", "principal fora da seleção", {
      publicMessage: "A área principal precisa estar entre as áreas selecionadas.",
    });
  }
}

/** Área de outro escritório simplesmente não é encontrada — o RLS resolve. */
async function conferirAreas(tx: Tx, ids: string[]): Promise<void> {
  if (ids.length === 0) return;
  const achadas = await tx.department.count({ where: { id: { in: ids } } });
  if (achadas !== new Set(ids).size) {
    throw new AppError("VALIDATION", "área inexistente neste escritório");
  }
}

async function gravarAreas(
  tx: Tx,
  tenantId: string,
  membershipId: string,
  areas: string[],
  principal: string | null,
  jaTinha: Set<string> = new Set(),
): Promise<void> {
  for (const departmentId of areas) {
    if (jaTinha.has(departmentId)) continue;
    await tx.departmentMembership.create({ data: { tenantId, departmentId, membershipId } });
  }

  await tx.membership.update({
    where: { id: membershipId },
    data: { primaryDepartmentId: principal },
  });
}

async function umaPessoa(
  ctx: AuthContext,
  membershipId: string,
): Promise<PessoaDaEquipe | null> {
  const todas = await listarEquipe(ctx);
  return todas.find((p) => p.membershipId === membershipId) ?? null;
}

/**
 * Violação de único em coluna GLOBAL vira mensagem que se entende.
 *
 * `identities.email` e `(provider, provider_uid)` são únicos no banco
 * INTEIRO, não por escritório — e o RLS impede consultar antes para
 * avisar com antecedência. Então o erro do banco é a única fonte.
 *
 * Quem decide QUAL dos dois bateu é o ponto de chamada, não o texto do
 * erro. Farejar o nome da constraint na mensagem funcionava até o driver
 * mudar o formato — e mudou: o nome da constraint vive em
 * `meta.driverAdapterError.cause.originalMessage`, não no `message`. Cada
 * `catch` sabe o que estava gravando; é ele quem diz.
 */
function ehUnicoViolado(erro: unknown): boolean {
  if (typeof erro !== "object" || erro === null) return false;
  const codigo = (erro as { code?: unknown }).code;
  if (codigo === "P2002") return true;
  // P2010 é o `$executeRaw` cru: a informação fica no erro do driver.
  return /UniqueConstraintViolation|23505|duplicate key/i.test(JSON.stringify(erro));
}

function conflitoDeEmail(erro: unknown, email: string): unknown {
  if (!ehUnicoViolado(erro)) return erro;
  return new AppError("CONFLICT", `e-mail ${email} já existe`, {
    publicMessage:
      "Este e-mail já está cadastrado no sistema — possivelmente em outro escritório. Use outro e-mail ou peça o vínculo da pessoa que já existe.",
  });
}

function conflitoDeLogin(erro: unknown, uid: string): unknown {
  if (!ehUnicoViolado(erro)) return erro;
  return new AppError("CONFLICT", `login ${uid} já usado`, {
    publicMessage: "Este login do FISCALE já está associado a outra pessoa.",
  });
}
