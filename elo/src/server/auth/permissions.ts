/**
 * Autorizacao central.
 *
 * A regra que este arquivo existe para impedir: `if (role === "ADMIN")`
 * espalhado pelo codigo. Espalhado, ninguem consegue responder "quem pode
 * apagar departamento?" sem varrer o projeto inteiro — e a resposta muda
 * silenciosamente quando alguem copia um `if` errado.
 *
 * Aqui o papel nunca e consultado direto. Pergunta-se por PERMISSAO, e a
 * tabela abaixo e a unica que sabe traduzir papel em permissao.
 *
 * PAPEL nao e DEPARTAMENTO. Papel e nivel de poder (OWNER..VIEWER);
 * departamento e area operacional (Fiscal, Contabil, DP) e vive em dados,
 * nao aqui. A Aline pode ser ADMIN do Fiscal; outro pode ser AGENT do DP.
 */
import { Role } from "@/generated/prisma/enums";

export const PERMISSIONS = [
  "tenant.manage",
  "users.read",
  "users.manage",
  "departments.read",
  "departments.manage",
  "sessions.read",
  "sessions.manage",
  "audit.read",
  // MVP 1.4 — atendimento.
  "conversations.read",
  "conversations.assign",
  "conversations.transfer",
  "conversations.status",
  "notes.read",
  "notes.write",
  "tags.read",
  "tags.manage",
  // MVP 1.5 — mensagens.
  "messages.read",
  "messages.send",
  "messages.view_receipts",
  // MVP 1.7 — notificacoes.
  //
  // `manage_self` e so isto: as MINHAS preferencias e os MEUS aparelhos.
  // Nao existe (e nao deve existir) permissao para mexer no aviso de
  // outra pessoa — desligar o alerta de um colega e desligar o trabalho
  // dele sem que ele saiba.
  //
  // Nao ha `notifications.manage_others` nem equivalente. Se um dia
  // houver motivo, sera outra permissao, escrita aqui, e nao um `if` de
  // papel espalhado numa rota.
  "notifications.manage_self",
] as const;

export type Permission = (typeof PERMISSIONS)[number];

/**
 * Papel -> permissoes. Deliberadamente explicito, sem heranca entre
 * papeis: heranca economiza cinco linhas e cobra caro quando alguem
 * precisa tirar UMA permissao de um nivel intermediario.
 *
 * `sessions.manage` e o encerrar-outras-sessoes. Todo mundo pode encerrar
 * as proprias — isso nao passa por permissao, passa por dono da sessao.
 */
/**
 * Sobre o atendimento: AGENT recebe `conversations.transfer` de proposito.
 * Num escritorio de quinze pessoas, passar um caso para o colega antes de
 * sair de ferias e rotina, nao excecao administrativa. Quem nao transfere
 * e o VIEWER, que so olha.
 *
 * `tags.manage` (criar e apagar etiqueta do escritorio) fica no MANAGER
 * para cima: aplicar etiqueta e trabalho; inventar etiqueta nova a cada
 * atendimento vira lista de trinta variacoes de "urgente".
 */
const POR_PAPEL: Record<Role, readonly Permission[]> = {
  OWNER: [
    "tenant.manage",
    "users.read",
    "users.manage",
    "departments.read",
    "departments.manage",
    "sessions.read",
    "sessions.manage",
    "audit.read",
    "conversations.read",
    "conversations.assign",
    "conversations.transfer",
    "conversations.status",
    "notes.read",
    "notes.write",
    "tags.read",
    "tags.manage",
    "messages.read",
    "messages.send",
    "messages.view_receipts",
    "notifications.manage_self",
  ],
  ADMIN: [
    "users.read",
    "users.manage",
    "departments.read",
    "departments.manage",
    "sessions.read",
    "sessions.manage",
    "audit.read",
    "conversations.read",
    "conversations.assign",
    "conversations.transfer",
    "conversations.status",
    "notes.read",
    "notes.write",
    "tags.read",
    "tags.manage",
    "messages.read",
    "messages.send",
    "messages.view_receipts",
    "notifications.manage_self",
  ],
  MANAGER: [
    "users.read",
    "departments.read",
    "departments.manage",
    "sessions.read",
    "conversations.read",
    "conversations.assign",
    "conversations.transfer",
    "conversations.status",
    "notes.read",
    "notes.write",
    "tags.read",
    "tags.manage",
    "messages.read",
    "messages.send",
    "messages.view_receipts",
    "notifications.manage_self",
  ],
  AGENT: [
    "users.read",
    "departments.read",
    "conversations.read",
    "conversations.assign",
    "conversations.transfer",
    "conversations.status",
    "notes.read",
    "notes.write",
    "tags.read",
    "messages.read",
    "messages.send",
    "messages.view_receipts",
    "notifications.manage_self",
  ],
  // VIEWER le e nao age: ve a conversa e quem visualizou, e nao envia.
  VIEWER: [
    "departments.read",
    "conversations.read",
    "notes.read",
    "tags.read",
    "messages.read",
    "messages.view_receipts",
    "notifications.manage_self",
  ],
};

export function permissionsOf(role: Role): readonly Permission[] {
  return POR_PAPEL[role];
}

export function roleHas(role: Role, permission: Permission): boolean {
  return POR_PAPEL[role].includes(permission);
}
