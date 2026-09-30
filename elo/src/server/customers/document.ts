/**
 * Normalizacao de CNPJ/CPF.
 *
 * O documento NAO e chave de nada aqui. Ele muda de formatacao, pode nao
 * existir (contato que e pessoa, nao empresa) e nao serve como identidade
 * tecnica — a identidade e o `externalId` da origem. O que guardamos e uma
 * versao so-digitos, para que procurar "11.222.333/0001-81" e
 * "11222333000181" ache a mesma coisa.
 *
 * Nao validamos digito verificador. Cadastro real tem documento provisorio
 * e erro de digitacao; recusar aqui esconderia o cliente do atendimento
 * por um motivo que nao e do atendimento.
 */

export type DocumentKind = "CNPJ" | "CPF" | "OUTRO";

export interface NormalizedDocument {
  digits: string | null;
  kind: DocumentKind | null;
}

export function normalizeDocument(bruto: string | null | undefined): NormalizedDocument {
  const digits = (bruto ?? "").replace(/\D/g, "");
  if (digits.length === 0) return { digits: null, kind: null };

  const kind: DocumentKind =
    digits.length === 14 ? "CNPJ" : digits.length === 11 ? "CPF" : "OUTRO";

  return { digits, kind };
}

/** Chave de busca por documento. Devolve null quando nao ha o que procurar. */
export function documentSearchKey(entrada: string): string | null {
  const d = entrada.replace(/\D/g, "");
  return d.length >= 3 ? d : null;
}
