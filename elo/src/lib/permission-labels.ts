/**
 * Nome de permissão em português de gente.
 *
 * `users.manage` é o identificador do sistema — bom para código, péssimo
 * numa tela. E mesmo traduzido, essa lista só faz sentido para quem
 * administra o escritório: para a pessoa que atende, saber que possui
 * `departments.read` não muda nada do dia dela.
 *
 * Por isso o mapa vive na camada de interface, e não junto da tabela de
 * permissões: quem decide o que pode é o servidor; isto aqui só escolhe
 * como escrever.
 */
export const ROTULO_PERMISSAO: Record<string, string> = {
  "tenant.manage": "Administrar o escritório",
  "users.read": "Ver a equipe",
  "users.manage": "Gerenciar a equipe",
  "departments.read": "Ver as áreas",
  "departments.manage": "Gerenciar as áreas",
  "sessions.read": "Ver sessões abertas",
  "sessions.manage": "Encerrar sessões de outras pessoas",
  "audit.read": "Consultar o histórico de acessos",
  "notifications.manage_self": "Configurar os próprios avisos",
};

export function rotuloPermissao(chave: string): string {
  return ROTULO_PERMISSAO[chave] ?? chave;
}

/** Papéis com poder de administração — só eles veem a lista de permissões. */
const PAPEIS_ADMIN = new Set(["OWNER", "ADMIN"]);

export function ehAdministrador(papel: string): boolean {
  return PAPEIS_ADMIN.has(papel);
}

/** "ADMIN" na tela vira algo que se lê. */
export const ROTULO_PAPEL: Record<string, string> = {
  OWNER: "Responsável pelo escritório",
  ADMIN: "Administrador",
  MANAGER: "Coordenação",
  AGENT: "Atendimento",
  VIEWER: "Somente leitura",
};

export function rotuloPapel(papel: string): string {
  return ROTULO_PAPEL[papel] ?? papel;
}
