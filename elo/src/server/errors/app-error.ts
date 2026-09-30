/**
 * Erros da aplicacao.
 *
 * Dois textos por erro, e a distincao e o ponto do arquivo:
 *   - `publicMessage` vai para o cliente. Generico de proposito.
 *   - `message` e os `details` ficam no log do servidor.
 *
 * Stack trace nunca sai na resposta. Em desenvolvimento a resposta ganha um
 * campo `debug`; em producao, nada.
 */

export type ErrorKind =
  | "VALIDATION"
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "CONFLICT"
  | "TENANT_CONTEXT"
  | "DATABASE"
  | "INTERNAL";

const STATUS: Record<ErrorKind, number> = {
  VALIDATION: 400,
  UNAUTHENTICATED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  TENANT_CONTEXT: 403,
  DATABASE: 503,
  INTERNAL: 500,
};

const MENSAGEM_PUBLICA: Record<ErrorKind, string> = {
  VALIDATION: "Dados invalidos.",
  UNAUTHENTICATED: "Autenticacao necessaria.",
  FORBIDDEN: "Acesso negado.",
  NOT_FOUND: "Nao encontrado.",
  // Unico caso em que a mensagem interna VAI para o cliente: "assumido
  // por Aline • Fiscal" e exatamente o que a pessoa precisa ler para
  // entender por que o botao nao funcionou. Nao ha segredo em dizer quem
  // e o colega que pegou o atendimento.
  CONFLICT: "Este atendimento mudou enquanto voce olhava.",
  // Nao diz "tenant ausente": quem tenta atravessar nao precisa saber por
  // que falhou.
  TENANT_CONTEXT: "Acesso negado.",
  DATABASE: "Servico indisponivel no momento.",
  INTERNAL: "Erro interno.",
};

export class AppError extends Error {
  readonly kind: ErrorKind;
  readonly status: number;
  readonly publicMessage: string;
  readonly details: Record<string, unknown> | undefined;

  constructor(
    kind: ErrorKind,
    message: string,
    options?: {
      details?: Record<string, unknown>;
      cause?: unknown;
      /**
       * Texto que VAI para o cliente, no lugar do generico.
       *
       * Usar com parcimonia — a regra do projeto e nao contar detalhe
       * interno. A excecao legitima e o conflito de atendimento: dizer
       * "assumido por Aline - Fiscal" e o que faz a pessoa entender por
       * que o botao nao funcionou, e nao ha segredo nisso.
       */
      publicMessage?: string;
    },
  ) {
    super(message, options?.cause !== undefined ? { cause: options.cause } : {});
    this.name = "AppError";
    this.kind = kind;
    this.status = STATUS[kind];
    this.publicMessage = options?.publicMessage ?? MENSAGEM_PUBLICA[kind];
    this.details = options?.details;
  }
}

export class ValidationError extends AppError {
  constructor(message: string, details?: Record<string, unknown>) {
    super("VALIDATION", message, details ? { details } : {});
    this.name = "ValidationError";
  }
}

/**
 * Falta de contexto de tenant. Nao e "erro de programacao a ser tolerado":
 * e a falha fechada do sistema, e por isso tem tipo proprio e alerta no log.
 */
export class TenantContextError extends AppError {
  constructor(message: string, details?: Record<string, unknown>) {
    super("TENANT_CONTEXT", message, details ? { details } : {});
    this.name = "TenantContextError";
  }
}

export class DatabaseError extends AppError {
  constructor(message: string, cause?: unknown) {
    super("DATABASE", message, cause !== undefined ? { cause } : {});
    this.name = "DatabaseError";
  }
}

export function toAppError(erro: unknown): AppError {
  if (erro instanceof AppError) return erro;
  if (erro instanceof Error) {
    return new AppError("INTERNAL", erro.message, { cause: erro });
  }
  return new AppError("INTERNAL", "erro desconhecido", {
    details: { valor: String(erro) },
  });
}
