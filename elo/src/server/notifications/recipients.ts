/**
 * Quem deve ser avisado.
 *
 * ---------------------------------------------------------------------
 *  A regra que atravessa o arquivo
 * ---------------------------------------------------------------------
 *  Notificar todo mundo de tudo e o caminho mais rapido para ninguem ler
 *  nada. Um escritorio de quinze pessoas que recebe quinze avisos por
 *  mensagem desliga as notificacoes na primeira semana — e aí o recurso
 *  deixou de existir, com o custo todo pago.
 *
 *  Entao cada evento tem um destinatario definido, nesta ordem:
 *
 *    MENSAGEM em atendimento ASSUMIDO      → o responsavel, e so ele
 *    MENSAGEM em atendimento SEM dono      → a fila elegivel (abaixo)
 *    ATENDIMENTO NOVO sem responsavel      → a fila elegivel
 *    ATRIBUICAO / TRANSFERENCIA            → quem recebeu
 *
 * ---------------------------------------------------------------------
 *  O que "fila elegivel" quer dizer
 * ---------------------------------------------------------------------
 *  Vinculo ativo · pessoa ativa · `availableForAssignment` · permissao
 *  `conversations.read` — e, quando o atendimento tem AREA, pertencer
 *  aquela area.
 *
 *  Sem area definida, a fila e o escritorio inteiro (menos quem nao
 *  cumpre os criterios acima). Isso e o "Atendimento Geral": nao ha para
 *  onde direcionar, e deixar sem aviso seria pior do que avisar demais.
 *
 *  `visibleToCustomers` NAO entra: aparecer na lista que o cliente ve e
 *  outra coisa, e quem esta fora dela continua trabalhando.
 *
 * ---------------------------------------------------------------------
 *  Quem NUNCA e avisado
 * ---------------------------------------------------------------------
 *  Quem fez a acao. A Aline manda a mensagem e a Aline nao recebe "nova
 *  mensagem no ELO". Parece obvio escrito assim, e e o defeito mais comum
 *  desse tipo de sistema — porque o codigo que avisa costuma nascer sem
 *  saber quem originou o evento.
 */
import { roleHas } from "@/server/auth/permissions";
import type { withTenant } from "@/server/tenancy";

type Tx = Parameters<Parameters<typeof withTenant>[1]>[0];

export interface Candidato {
  membershipId: string;
  nome: string;
}

/**
 * A fila de uma area — ou do escritorio, quando nao ha area.
 *
 * @param excluir quem originou o evento. Nunca se avisa a si mesmo.
 */
/**
 * O papel que COORDENA — e e um so.
 *
 * Nao e uma regra de acesso: para isso existe o RBAC, e ele nao muda
 * aqui. E uma regra de DISTRIBUICAO, e ela olha para o PAPEL DE
 * COORDENACAO, nao para "altura" na hierarquia.
 *
 * A primeira versao tirava da fila OWNER, ADMIN e MANAGER — e estava
 * errada. A dona do escritorio atende o Fiscal todo dia; ser OWNER e um
 * nivel de poder no sistema, nao um cargo de supervisao. Tira-la da fila
 * do proprio setor faria o atendimento dela chegar 30 minutos depois,
 * por escalonamento, como se ela nao trabalhasse ali.
 *
 * MANAGER e diferente: esse papel EXISTE para coordenar. Quem o tem
 * entra pelo escalonamento, quando o atendimento fica sem resposta.
 */
export const PAPEL_DE_COORDENACAO = "MANAGER";

export interface OpcoesDaFila {
  /**
   * Tira a COORDENACAO (MANAGER) do resultado.
   *
   * Karoline e Fulano estao vinculados aos tres setores para ACESSAR
   * e GERIR — e devem continuar. Estar vinculado nao pode ser o mesmo que
   * estar na escala de quem recebe o primeiro aviso: se fosse, todo
   * atendimento novo tocaria o celular da coordenacao antes de qualquer
   * atendente olhar.
   *
   * OWNER e ADMIN NAO saem por aqui. Quem esta vinculado ao setor e
   * disponivel recebe a fila, seja qual for o papel — e e o caso da
   * Aline, OWNER e responsavel pelo Fiscal.
   */
  semGestao?: boolean | undefined;
}

export async function filaElegivel(
  tx: Tx,
  departmentId: string | null,
  excluir: string | null,
  opcoes: OpcoesDaFila = {},
): Promise<Candidato[]> {
  const membros = await tx.membership.findMany({
    where: {
      active: true,
      availableForAssignment: true,
      identity: { is: { active: true } },
      ...(departmentId ? { departments: { some: { departmentId } } } : {}),
      ...(excluir ? { id: { not: excluir } } : {}),
    },
    select: {
      id: true,
      role: true,
      identity: { select: { name: true } },
    },
  });

  return (
    membros
      // A permissao decide, e nao o papel: quem nao pode LER o atendimento
      // nao pode ser avisado dele. Sem isto, um VIEWER de outra area
      // receberia aviso de uma conversa que a tela nao vai deixar abrir.
      .filter((m) => roleHas(m.role, "conversations.read"))
      .filter((m) => !(opcoes.semGestao && m.role === PAPEL_DE_COORDENACAO))
      .map((m) => ({ membershipId: m.id, nome: m.identity.name }))
  );
}

/**
 * A coordenacao de uma area — os destinatarios do ESCALONAMENTO.
 *
 * Nao filtra por `availableForAssignment`: esse campo diz "recebe
 * atendimento novo", e escalonamento nao e atendimento novo. Um gestor
 * fora da escala continua sendo quem precisa saber que um caso ficou
 * parado.
 */
export async function gestoresDoDepartamento(
  tx: Tx,
  departmentId: string | null,
): Promise<Candidato[]> {
  const membros = await tx.membership.findMany({
    where: {
      active: true,
      identity: { is: { active: true } },
      ...(departmentId ? { departments: { some: { departmentId } } } : {}),
    },
    select: { id: true, role: true, identity: { select: { name: true } } },
  });

  return membros
    // So MANAGER. OWNER e ADMIN sao niveis de poder, nao supervisao: se
    // um deles atende o setor, ja foi avisado na fila — receber tambem o
    // escalonamento seria o mesmo alerta duas vezes, para quem ja sabia.
    .filter((m) => m.role === PAPEL_DE_COORDENACAO)
    .filter((m) => roleHas(m.role, "conversations.read"))
    .map((m) => ({ membershipId: m.id, nome: m.identity.name }));
}

/**
 * Uma pessoa especifica ainda pode receber aviso?
 *
 * Vale para o responsavel e para o destino de uma transferencia. Verificar
 * de novo na hora do envio nao e paranoia: entre a atribuicao e a mensagem
 * seguinte pode passar uma semana, e nesse intervalo o vinculo pode ter
 * sido desativado. Membership desativado nao recebe mais nada — e e este
 * o mecanismo que o item 36 pede, do lado do servidor, sem depender de a
 * pessoa ter feito logout.
 */
export async function podeReceberAviso(
  tx: Tx,
  membershipId: string,
): Promise<Candidato | null> {
  const m = await tx.membership.findUnique({
    where: { id: membershipId },
    select: {
      id: true,
      active: true,
      role: true,
      identity: { select: { name: true, active: true } },
    },
  });

  if (!m || !m.active || !m.identity.active) return null;
  if (!roleHas(m.role, "conversations.read")) return null;

  return { membershipId: m.id, nome: m.identity.name };
}

/**
 * Destinatarios de uma MENSAGEM que chegou.
 *
 * Com responsavel, e ele — e so ele. Avisar a fila inteira de um caso que
 * ja tem dono transforma o dono em mais um espectador.
 */
export async function paraMensagem(
  tx: Tx,
  conversa: {
    assignedMembershipId: string | null;
    assignedDepartmentId: string | null;
  },
  autor: string | null,
): Promise<Candidato[]> {
  if (conversa.assignedMembershipId) {
    if (conversa.assignedMembershipId === autor) return [];
    const dono = await podeReceberAviso(tx, conversa.assignedMembershipId);
    return dono ? [dono] : [];
  }
  return filaElegivel(tx, conversa.assignedDepartmentId, autor);
}
