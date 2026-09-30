/**
 * Log estruturado. Uma linha JSON por evento, na saida padrao.
 *
 * Nao e uma biblioteca de log: e o contrato minimo para que ninguem escreva
 * `console.log` espalhado (o eslint proibe fora daqui). Campos fixos —
 * requestId, tenantId, userId, event — para que a busca no log de producao
 * seja por campo e nao por regex.
 *
 * Regra dura: NENHUM valor sensivel entra aqui. `redact()` corta as chaves
 * conhecidas, mas a defesa real e nao passar o dado.
 */
import { env } from "@/env";

export type LogLevel = "debug" | "info" | "warn" | "error";

const ORDEM: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

/** Contexto que atravessa a requisicao. Preenchido pelo middleware da rota. */
export interface LogContext {
  requestId?: string;
  tenantId?: string;
  userId?: string;
}

export type LogFields = Record<string, unknown>;

/**
 * Chaves cujo valor nunca sai no log, venha de onde vier. A lista cobre os
 * nomes que aparecem no Elo e no Fiscale (DPAPI, certificado, token da Meta).
 */
const CHAVES_PROIBIDAS = [
  "password",
  "senha",
  "secret",
  "token",
  "authorization",
  "cookie",
  "apikey",
  "api_key",
  "accesstoken",
  "refreshtoken",
  "connectionstring",
  "database_url",
  "pfx",
  "certificado",
  "dpapi",
  "privatekey",
];

const MASCARA = "[REDIGIDO]";
const PROFUNDIDADE_MAXIMA = 4;

function chaveProibida(chave: string): boolean {
  const k = chave.toLowerCase().replace(/[-_\s]/g, "");
  return CHAVES_PROIBIDAS.some((p) => k.includes(p.replace(/[-_]/g, "")));
}

export function redact(valor: unknown, profundidade = 0): unknown {
  if (valor === null || valor === undefined) return valor;
  if (profundidade >= PROFUNDIDADE_MAXIMA) return "[PROFUNDO]";

  if (Array.isArray(valor)) {
    return valor.slice(0, 20).map((v) => redact(v, profundidade + 1));
  }

  if (valor instanceof Error) {
    return { name: valor.name, message: valor.message };
  }

  if (typeof valor === "object") {
    const saida: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(valor as Record<string, unknown>)) {
      saida[k] = chaveProibida(k) ? MASCARA : redact(v, profundidade + 1);
    }
    return saida;
  }

  return valor;
}

export interface Logger {
  debug(event: string, fields?: LogFields): void;
  info(event: string, fields?: LogFields): void;
  warn(event: string, fields?: LogFields): void;
  error(event: string, fields?: LogFields): void;
  /** Deriva um logger que carrega o contexto — nao muta o original. */
  child(context: LogContext): Logger;
}

function emitir(
  level: LogLevel,
  context: LogContext,
  event: string,
  fields?: LogFields,
): void {
  if (ORDEM[level] < ORDEM[env.LOG_LEVEL]) return;

  const linha = {
    ts: new Date().toISOString(),
    level,
    event,
    ...context,
    ...(fields ? (redact(fields) as LogFields) : {}),
  };

  const texto = JSON.stringify(linha);
  if (level === "error" || level === "warn") {
    process.stderr.write(`${texto}\n`);
  } else {
    process.stdout.write(`${texto}\n`);
  }
}

function criar(context: LogContext): Logger {
  return {
    debug: (event, fields) => emitir("debug", context, event, fields),
    info: (event, fields) => emitir("info", context, event, fields),
    warn: (event, fields) => emitir("warn", context, event, fields),
    error: (event, fields) => emitir("error", context, event, fields),
    child: (extra) => criar({ ...context, ...extra }),
  };
}

export const logger: Logger = criar({});

/** Id de correlacao da requisicao. Entra em todo log dela e na resposta. */
export function newRequestId(): string {
  return crypto.randomUUID();
}
