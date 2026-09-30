/**
 * Com quem o cliente pode falar.
 *
 * ---------------------------------------------------------------------
 *  Esta lista sai do escritório
 * ---------------------------------------------------------------------
 *  É o menu que o cliente vai ver no WhatsApp (MVP 1.7). Por isso ela
 *  devolve o MÍNIMO: nome e área, mais um id para referenciar a escolha.
 *
 *  Não devolve e-mail, papel, permissões, sessão, telefone interno nem
 *  qualquer dado administrativo. A regra não é "filtrar na tela" — é não
 *  buscar: o que não sai daqui não vaza numa serialização distraída lá na
 *  frente.
 *
 * ---------------------------------------------------------------------
 *  Quem aparece
 * ---------------------------------------------------------------------
 *  Só quem tem `visibleToCustomers = true`. Não é "todos os logins", e não
 *  é derivado do papel: um gerente aparece porque alguém marcou que ele
 *  aparece, não porque `role === MANAGER`. Regra por papel engessaria a
 *  decisão num lugar que o escritório não controla.
 *
 *  `availableForAssignment` é outra coisa, e por isso vem separado: dá
 *  para estar visível ao cliente e fora da distribuição — o gerente que
 *  atende consulta mas não entra na fila. Quem está de férias sai dos
 *  dois.
 *
 *  Nada disso é "online". Presença em tempo real é do MVP 1.5 em diante;
 *  aqui é configuração, não estado do momento.
 */
import { withTenant } from "@/server/tenancy";

export interface OpcaoDeAtendimento {
  /** Referência para a escolha do cliente. Não é dado pessoal. */
  membershipId: string;
  displayName: string;
  /** A área principal — "Aline • Fiscal". Nulo se ninguém definiu. */
  department: string | null;
  /** Aparece na lista mas não recebe fila automática. */
  availableForAssignment: boolean;
}

export interface MenuDeAtendimento {
  people: OpcaoDeAtendimento[];
  /**
   * O caminho de quem não quer escolher pessoa. Sempre existe: obrigar o
   * cliente a escolher um nome para poder falar com o escritório é criar
   * um obstáculo onde deveria haver uma porta.
   */
  general: { label: string };
}

/**
 * Só nome e área. O `tenantId` vem do contexto validado — nunca de quem
 * chama de fora.
 */
export async function getCustomerVisibleAssignees(
  tenantId: string,
): Promise<MenuDeAtendimento> {
  const pessoas = await withTenant(tenantId, async (tx) => {
    const membros = await tx.membership.findMany({
      where: { active: true, visibleToCustomers: true },
      select: {
        id: true,
        primaryDepartmentId: true,
        availableForAssignment: true,
        identity: { select: { name: true, active: true } },
      },
    });

    const idsAreas = [
      ...new Set(membros.map((m) => m.primaryDepartmentId).filter((i): i is string => Boolean(i))),
    ];
    const areas = idsAreas.length
      ? new Map(
          (
            await tx.department.findMany({
              where: { id: { in: idsAreas } },
              select: { id: true, name: true },
            })
          ).map((d) => [d.id, d.name]),
        )
      : new Map<string, string>();

    return membros
      // A pessoa pode estar inativa globalmente mesmo com o vínculo ativo.
      .filter((m) => m.identity.active)
      .map((m) => ({
        membershipId: m.id,
        displayName: primeiroNome(m.identity.name),
        department: m.primaryDepartmentId
          ? (areas.get(m.primaryDepartmentId) ?? null)
          : null,
        availableForAssignment: m.availableForAssignment,
      }))
      .sort((a, b) => a.displayName.localeCompare(b.displayName, "pt-BR"));
  });

  return { people: pessoas, general: { label: "Atendimento Geral" } };
}

/**
 * O cliente escolheu alguém: essa escolha é válida?
 *
 * Verificar de novo no momento da criação não é paranoia — entre ver o
 * menu e responder pode passar um dia, e nesse intervalo a pessoa pode ter
 * saído de férias ou do escritório.
 */
export async function podeReceberEscolha(
  tenantId: string,
  membershipId: string,
): Promise<boolean> {
  return withTenant(tenantId, async (tx) => {
    const m = await tx.membership.findUnique({
      where: { id: membershipId },
      select: {
        active: true,
        visibleToCustomers: true,
        availableForAssignment: true,
        identity: { select: { active: true } },
      },
    });
    return Boolean(
      m?.active && m.identity.active && m.visibleToCustomers && m.availableForAssignment,
    );
  });
}

function primeiroNome(nome: string): string {
  return nome.trim().split(/\s+/)[0] ?? nome;
}
