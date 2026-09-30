/**
 * Normalizacao de telefone.
 *
 * ---------------------------------------------------------------------
 *  A regra que governa este arquivo
 * ---------------------------------------------------------------------
 *  Na duvida, NAO normaliza.
 *
 *  Amanha isto vai casar mensagem de WhatsApp com cliente. Um numero
 *  normalizado errado nao da erro: entrega a conversa de um cliente na
 *  tela de outro, e ninguem percebe. Entao qualquer palpite — inventar
 *  DDD, acrescentar o nono digito, "consertar" um numero curto — esta
 *  proibido. O que nao da para normalizar com certeza fica como veio,
 *  marcado, e simplesmente nao participa do casamento automatico.
 *
 * ---------------------------------------------------------------------
 *  Por que o campo de origem pode ter varios numeros
 * ---------------------------------------------------------------------
 *  O Fiscale guarda UM campo `tel`, texto livre, sem mascara obrigatoria
 *  e sem validacao. Na pratica isso vira "(81) 99999-1111 / 3333-4444",
 *  que e como escritorio preenche cadastro. Comprimir isso num campo so
 *  obrigaria a escolher um dos numeros na hora de casar a mensagem — a
 *  decisao silenciosa que este arquivo existe para evitar.
 *
 *  A separacao e conservadora: so em separadores EXPLICITOS (/ ; , | e).
 *  Espaco nao separa, senao "(81) 99999-1111" viraria dois pedacos.
 */

export type PhoneStatus = "OK" | "NO_AREA_CODE" | "INVALID";

export interface NormalizedPhone {
  /** Exatamente como veio, sem tocar. */
  raw: string;
  /** +55DDDNNNNNNNNN, ou null quando nao deu para ter certeza. */
  e164: string | null;
  status: PhoneStatus;
}

/**
 * DDDs que existem no Brasil. Nao e purismo: sem esta lista, "12345678901"
 * — que pode ser um CPF digitado no campo errado — viraria "+5512 3456
 * 7890 1" e entraria no indice de busca como telefone de verdade.
 */
const DDD_VALIDOS = new Set([
  11, 12, 13, 14, 15, 16, 17, 18, 19,
  21, 22, 24, 27, 28,
  31, 32, 33, 34, 35, 37, 38,
  41, 42, 43, 44, 45, 46, 47, 48, 49,
  51, 53, 54, 55,
  61, 62, 63, 64, 65, 66, 67, 68, 69,
  71, 73, 74, 75, 77, 79,
  81, 82, 83, 84, 85, 86, 87, 88, 89,
  91, 92, 93, 94, 95, 96, 97, 98, 99,
]);

const SEPARADORES = /\s*(?:[/;,|]|\be\b)\s*/i;

/** Quebra o campo livre em candidatos. Um campo vazio devolve lista vazia. */
export function splitPhoneField(campo: string | null | undefined): string[] {
  if (!campo) return [];
  return campo
    .split(SEPARADORES)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}

function soDigitos(v: string): string {
  return v.replace(/\D/g, "");
}

/**
 * Normaliza UM numero.
 *
 * Aceita, nesta ordem:
 *   +<qualquer>      ja internacional — respeita como veio
 *   55 + 10 ou 11    Brasil com codigo de pais
 *   10 ou 11         DDD + numero, sem codigo de pais -> prefixa +55
 *   8 ou 9 digitos   numero local SEM DDD -> NO_AREA_CODE, nao adivinha
 *   resto            INVALID
 */
export function normalizePhone(raw: string): NormalizedPhone {
  const original = raw.trim();
  if (original.length === 0) {
    return { raw: original, e164: null, status: "INVALID" };
  }

  const internacional = original.startsWith("+");
  const digitos = soDigitos(original);

  if (digitos.length === 0) {
    return { raw: original, e164: null, status: "INVALID" };
  }

  // Ja veio com + : quem escreveu disse o pais. Nao mexemos.
  if (internacional) {
    if (digitos.length < 8 || digitos.length > 15) {
      return { raw: original, e164: null, status: "INVALID" };
    }
    // Se o pais e o Brasil, ainda vale conferir o DDD.
    if (digitos.startsWith("55") && (digitos.length === 12 || digitos.length === 13)) {
      const ddd = Number(digitos.slice(2, 4));
      if (!DDD_VALIDOS.has(ddd)) {
        return { raw: original, e164: null, status: "INVALID" };
      }
    }
    return { raw: original, e164: `+${digitos}`, status: "OK" };
  }

  // Brasil com codigo de pais, sem o +.
  if (digitos.startsWith("55") && (digitos.length === 12 || digitos.length === 13)) {
    const ddd = Number(digitos.slice(2, 4));
    if (!DDD_VALIDOS.has(ddd)) {
      return { raw: original, e164: null, status: "INVALID" };
    }
    return { raw: original, e164: `+${digitos}`, status: "OK" };
  }

  // DDD + numero.
  if (digitos.length === 10 || digitos.length === 11) {
    const ddd = Number(digitos.slice(0, 2));
    if (!DDD_VALIDOS.has(ddd)) {
      return { raw: original, e164: null, status: "INVALID" };
    }
    // Celular com 11 digitos comeca com 9 depois do DDD; fixo com 10
    // comeca de 2 a 5. Fora disso, nao arriscamos.
    const primeiro = digitos[2];
    const ehCelular = digitos.length === 11 && primeiro === "9";
    const ehFixo = digitos.length === 10 && primeiro !== undefined && primeiro >= "2" && primeiro <= "5";
    if (!ehCelular && !ehFixo) {
      return { raw: original, e164: null, status: "INVALID" };
    }
    return { raw: original, e164: `+55${digitos}`, status: "OK" };
  }

  // Numero local sem DDD. Existe, e valido no papel — mas nao da para
  // discar nem para casar sem saber a cidade. NAO inventamos o DDD do
  // escritorio: seria acertar quase sempre e errar feio de vez em quando.
  if (digitos.length === 8 || digitos.length === 9) {
    return { raw: original, e164: null, status: "NO_AREA_CODE" };
  }

  return { raw: original, e164: null, status: "INVALID" };
}

/**
 * Campo livre inteiro -> lista de telefones normalizados, sem repeticao.
 *
 * A deduplicacao usa o `raw`, nao o E.164: dois textos diferentes que
 * chegam ao mesmo numero continuam sendo dois registros, porque a origem
 * escreveu os dois e essa informacao e da origem.
 */
export function normalizePhoneField(campo: string | null | undefined): NormalizedPhone[] {
  const vistos = new Set<string>();
  const saida: NormalizedPhone[] = [];

  for (const parte of splitPhoneField(campo)) {
    if (vistos.has(parte)) continue;
    vistos.add(parte);
    saida.push(normalizePhone(parte));
  }

  return saida;
}

/**
 * Normaliza um numero para BUSCA. Devolve null quando nao ha certeza —
 * procurar por um palpite acharia o cliente errado.
 */
export function phoneSearchKey(entrada: string): string | null {
  const r = normalizePhone(entrada);
  return r.status === "OK" ? r.e164 : null;
}
