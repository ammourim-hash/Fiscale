/**
 * Normalizacao de telefone.
 *
 * A metade que importa deste arquivo e a lista do que NAO deve virar um
 * numero. Normalizar demais é o erro caro: entrega a conversa de um
 * cliente na tela de outro, sem erro nenhum aparecendo.
 */
import { describe, expect, it } from "vitest";

import {
  normalizePhone,
  normalizePhoneField,
  phoneSearchKey,
  splitPhoneField,
} from "@/server/customers/phone";

describe("normaliza o que da para normalizar", () => {
  it.each([
    ["+5581999991111", "+5581999991111", "com + e codigo do pais"],
    ["5581999991111", "+5581999991111", "sem +, com codigo do pais"],
    ["81999991111", "+5581999991111", "celular com DDD, sem mascara"],
    ["(81) 99999-1111", "+5581999991111", "celular com mascara"],
    ["(81) 3333-4444", "+558133334444", "fixo com mascara"],
    ["8133334444", "+558133334444", "fixo sem mascara"],
    ["  81 99999 1111  ", "+5581999991111", "com espacos"],
    ["+55 (81) 99999-1111", "+5581999991111", "internacional com mascara"],
    ["558133334444", "+558133334444", "fixo com codigo do pais"],
  ])("%s -> %s (%s)", (entrada, esperado) => {
    const r = normalizePhone(entrada);
    expect(r.status).toBe("OK");
    expect(r.e164).toBe(esperado);
    expect(r.raw).toBe(entrada.trim());
  });
});

describe("na duvida, nao normaliza", () => {
  it("numero sem DDD fica sem palpite", () => {
    // Inventar o DDD do escritorio acertaria quase sempre — e erraria
    // feio de vez em quando, sem ninguem perceber.
    for (const n of ["999991111", "33334444", "9999-1111"]) {
      const r = normalizePhone(n);
      expect(r.status, n).toBe("NO_AREA_CODE");
      expect(r.e164).toBeNull();
      expect(r.raw).toBe(n);
    }
  });

  it("nao acrescenta o nono digito", () => {
    // 10 digitos com o primeiro local = 9 nao e fixo nem celular valido;
    // "consertar" para 11 seria inventar um numero.
    const r = normalizePhone("8199991111");
    expect(r.e164).toBeNull();
    expect(r.status).toBe("INVALID");
  });

  it("DDD que nao existe e recusado", () => {
    for (const n of ["10999991111", "20999991111", "00999991111"]) {
      const r = normalizePhone(n);
      expect(r.status, n).toBe("INVALID");
      expect(r.e164).toBeNull();
    }
  });

  it("CPF digitado no campo de telefone nao vira telefone", () => {
    // 11 digitos com DDD invalido — e o motivo de a lista de DDD existir.
    expect(normalizePhone("02699999999").status).toBe("INVALID");
  });

  it("lixo, vazio e texto nao viram numero", () => {
    for (const n of ["", "   ", "abc", "-", "()", "0", "12"]) {
      const r = normalizePhone(n);
      expect(r.status, JSON.stringify(n)).toBe("INVALID");
      expect(r.e164).toBeNull();
    }
  });

  it("digitos demais sao recusados", () => {
    expect(normalizePhone("5581999991111999").e164).toBeNull();
  });

  it("o raw sempre sobrevive, mesmo quando nao normaliza", () => {
    const r = normalizePhone("ramal 22");
    expect(r.raw).toBe("ramal 22");
    expect(r.e164).toBeNull();
  });
});

describe("campo livre com mais de um numero", () => {
  it("separa em barra, ponto-e-virgula, virgula, pipe e 'e'", () => {
    expect(splitPhoneField("(81) 99999-1111 / 3333-4444")).toHaveLength(2);
    expect(splitPhoneField("81999991111; 8133334444")).toHaveLength(2);
    expect(splitPhoneField("81999991111, 8133334444")).toHaveLength(2);
    expect(splitPhoneField("81999991111 | 8133334444")).toHaveLength(2);
    expect(splitPhoneField("81999991111 e 8133334444")).toHaveLength(2);
  });

  it("espaco NAO separa — senao a mascara viraria dois numeros", () => {
    expect(splitPhoneField("(81) 99999-1111")).toEqual(["(81) 99999-1111"]);
  });

  it("normaliza cada pedaco por conta propria", () => {
    const r = normalizePhoneField("(81) 99999-1111 / 3333-4444");
    expect(r).toHaveLength(2);
    expect(r[0]?.e164).toBe("+5581999991111");
    // O segundo nao tem DDD: fica sem normalizacao, e nao herda o do
    // primeiro. Herdar seria adivinhar.
    expect(r[1]?.status).toBe("NO_AREA_CODE");
    expect(r[1]?.e164).toBeNull();
  });

  it("campo vazio nao vira telefone nenhum", () => {
    expect(normalizePhoneField("")).toEqual([]);
    expect(normalizePhoneField(null)).toEqual([]);
    expect(normalizePhoneField(undefined)).toEqual([]);
    expect(normalizePhoneField("   ")).toEqual([]);
  });

  it("repeticao literal e descartada", () => {
    expect(normalizePhoneField("81999991111 / 81999991111")).toHaveLength(1);
  });

  it("grafias diferentes do mesmo numero continuam sendo dois registros", () => {
    // A origem escreveu os dois; isso e informacao dela, nao ruido nosso.
    const r = normalizePhoneField("81999991111 / (81) 99999-1111");
    expect(r).toHaveLength(2);
    expect(r[0]?.e164).toBe(r[1]?.e164);
  });
});

describe("chave de busca", () => {
  it("so devolve chave quando ha certeza", () => {
    expect(phoneSearchKey("(81) 99999-1111")).toBe("+5581999991111");
    expect(phoneSearchKey("99999-1111")).toBeNull();
    expect(phoneSearchKey("abc")).toBeNull();
  });
});
