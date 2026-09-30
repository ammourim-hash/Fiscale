/**
 * O contrato da sincronizacao — e a lista do que NAO atravessa.
 *
 * ---------------------------------------------------------------------
 *  Minimizacao
 * ---------------------------------------------------------------------
 *  O cadastro real do Fiscale (`state_clientes.json`) tem estes campos:
 *
 *      id · cert{arquivo,titular,cnpjLido} · nome · cnpj · regime · ie ·
 *      im · mun · uf · email · tel · certValidade
 *
 *  Destes, o Elo recebe CINCO: id, nome, cnpj, email, tel — mais o
 *  ativo/inativo, que o Fiscale deriva. O resto fica onde esta:
 *
 *   cert / certValidade   nome de arquivo .pfx e validade de certificado.
 *                         Nunca sai da maquina do escritorio. Sequer o
 *                         NOME do arquivo: ele carrega razao social e
 *                         CNPJ e diria onde procurar.
 *   regime · ie · im      dado fiscal. Nao ajuda a identificar nem a
 *                         atender; ajuda a apurar, e apuracao e do
 *                         Fiscale. Aparece na tela do MVP 1.7, consultado
 *                         ao vivo, sem copia.
 *   mun · uf              nao e necessario para reconhecer quem escreveu
 *                         nem para responder. Entraria "porque um dia
 *                         talvez" — que e exatamente o criterio proibido.
 *
 *  O `nome fantasia` estava na lista pedida, mas NAO EXISTE no cadastro
 *  do Fiscale. Coluna que nunca teria valor e peso morto; quando a origem
 *  passar a ter o campo, acrescentar e uma migration aditiva de uma linha.
 *
 *  Esta lista e verificada por teste: campo fora do contrato faz o
 *  payload ser recusado, em vez de entrar em silencio.
 */
import { createHash } from "node:crypto";

import { normalizeDocument } from "./document";
import { normalizePhoneField, type NormalizedPhone } from "./phone";

/** Exatamente os campos que o Elo aceita por cliente. Nada alem. */
export const CAMPOS_PERMITIDOS = [
  "externalId",
  "displayName",
  "document",
  "email",
  "phone",
  "active",
  "sourceVersion",
  "sourceUpdatedAt",
] as const;

export type CampoPermitido = (typeof CAMPOS_PERMITIDOS)[number];

/**
 * Campos que, se aparecerem, sao motivo de RECUSA — nao de descarte
 * silencioso. Se o Fiscale comecar a mandar `regime`, alguem precisa
 * descobrir isso no mesmo dia, e nao seis meses depois num dump.
 */
export const CAMPOS_PROIBIDOS = [
  "cert",
  "certificado",
  "certValidade",
  "pfx",
  "senha",
  "password",
  "chave",
  "privateKey",
  "dpapi",
  "regime",
  "ie",
  "im",
  "das",
  "apuracao",
  "guia",
  "xml",
  "pdf",
  "token",
  "credencial",
  "mun",
  "uf",
] as const;

export interface CustomerPayload {
  externalId: string;
  displayName: string;
  document?: string | null;
  email?: string | null;
  /** Campo livre da origem. Pode conter mais de um numero. */
  phone?: string | null;
  active: boolean;
  sourceVersion: string;
  sourceUpdatedAt?: string | null;
}

export interface CustomerProjectionInput {
  externalId: string;
  displayName: string;
  documentDigits: string | null;
  documentKind: string | null;
  email: string | null;
  phones: NormalizedPhone[];
  active: boolean;
  sourceVersion: string;
  sourceUpdatedAt: Date | null;
  /** Calculado AQUI, sobre o que chegou. Ver a nota abaixo. */
  contentHash: string;
}

/**
 * Hash de conteudo, calculado pelo Elo.
 *
 * O Fiscale manda o `sourceVersion` dele, e usa esse valor para decidir o
 * que enfileirar. Mas quem decide se a PROJECAO mudou e este hash, porque
 * assim a idempotencia nao depende de o outro lado calcular certo — nem
 * de Python e TypeScript produzirem o mesmo digest. Um bug no hash do
 * Fiscale causaria, no pior caso, um envio a mais; nunca uma atualizacao
 * perdida.
 *
 * A serializacao e explicita e ordenada: nada de JSON.stringify sobre
 * objeto, cuja ordem de chaves depende de como o objeto foi construido.
 */
export function contentHashOf(p: Omit<CustomerProjectionInput, "contentHash">): string {
  const partes = [
    p.externalId,
    p.displayName,
    p.documentDigits ?? "",
    p.email ?? "",
    p.active ? "1" : "0",
    // Telefone entra pelo par (raw, e164): mudar a grafia na origem e uma
    // mudanca de verdade, ainda que o numero final seja o mesmo.
    ...p.phones
      .map((t) => `${t.raw}|${t.e164 ?? ""}|${t.status}`)
      .sort(),
  ];

  // Junta com quebra de linha, e nao com espaco: os campos podem
  // conter espaco, e ai "A B" + "" e "A" + "B" dariam o mesmo hash.
  return createHash("sha256").update(partes.join("\n"), "utf8").digest("hex");
}

export class PayloadInvalido extends Error {
  readonly campo: string;
  constructor(campo: string, motivo: string) {
    super(`${campo}: ${motivo}`);
    this.name = "PayloadInvalido";
    this.campo = campo;
  }
}

function textoObrigatorio(v: unknown, campo: string, max: number): string {
  if (typeof v !== "string" || v.trim().length === 0) {
    throw new PayloadInvalido(campo, "obrigatorio");
  }
  const t = v.trim();
  if (t.length > max) throw new PayloadInvalido(campo, `passa de ${max} caracteres`);
  return t;
}

function textoOpcional(v: unknown, campo: string, max: number): string | null {
  if (v === undefined || v === null || v === "") return null;
  if (typeof v !== "string") throw new PayloadInvalido(campo, "deveria ser texto");
  const t = v.trim();
  if (t.length === 0) return null;
  if (t.length > max) throw new PayloadInvalido(campo, `passa de ${max} caracteres`);
  return t;
}

/**
 * Valida e converte um cliente do payload em projecao.
 *
 * Recusa campo desconhecido em vez de ignorar: um campo que ninguem
 * esperava e ou um erro do outro lado, ou dado sensivel vazando. Nos dois
 * casos, silencio e a pior resposta.
 */
export function parseCustomer(bruto: unknown): CustomerProjectionInput {
  if (typeof bruto !== "object" || bruto === null || Array.isArray(bruto)) {
    throw new PayloadInvalido("customer", "deveria ser um objeto");
  }

  const obj = bruto as Record<string, unknown>;

  for (const chave of Object.keys(obj)) {
    const proibido = CAMPOS_PROIBIDOS.find(
      (p) => chave.toLowerCase() === p.toLowerCase(),
    );
    if (proibido) {
      throw new PayloadInvalido(chave, "campo proibido no contrato de sincronizacao");
    }
    if (!(CAMPOS_PERMITIDOS as readonly string[]).includes(chave)) {
      throw new PayloadInvalido(chave, "campo fora do contrato de sincronizacao");
    }
  }

  const externalId = textoObrigatorio(obj.externalId, "externalId", 100);
  const displayName = textoObrigatorio(obj.displayName, "displayName", 300);
  const documento = normalizeDocument(textoOpcional(obj.document, "document", 40));
  const email = textoOpcional(obj.email, "email", 200)?.toLowerCase() ?? null;
  const phones = normalizePhoneField(textoOpcional(obj.phone, "phone", 300));
  const sourceVersion = textoObrigatorio(obj.sourceVersion, "sourceVersion", 128);

  if (typeof obj.active !== "boolean") {
    throw new PayloadInvalido("active", "deveria ser booleano");
  }

  let sourceUpdatedAt: Date | null = null;
  const bruteUpdated = textoOpcional(obj.sourceUpdatedAt, "sourceUpdatedAt", 40);
  if (bruteUpdated) {
    const d = new Date(bruteUpdated);
    if (Number.isNaN(d.getTime())) {
      throw new PayloadInvalido("sourceUpdatedAt", "data invalida");
    }
    sourceUpdatedAt = d;
  }

  const sem = {
    externalId,
    displayName,
    documentDigits: documento.digits,
    documentKind: documento.kind,
    email,
    phones,
    active: obj.active,
    sourceVersion,
    sourceUpdatedAt,
  };

  return { ...sem, contentHash: contentHashOf(sem) };
}
