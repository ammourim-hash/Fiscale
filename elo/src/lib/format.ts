/**
 * Formatação para leitura humana.
 *
 * Só apresentação: nada aqui altera dado. O telefone guardado continua
 * sendo o E.164; o que muda é como ele aparece na tela.
 */

/** `+5581999991111` → `(81) 99999-1111`. Não-brasileiro sai como veio. */
export function formatPhone(e164: string | null, raw?: string): string {
  if (!e164) return raw ?? "—";
  const m = /^\+55(\d{2})(\d{4,5})(\d{4})$/.exec(e164);
  if (!m) return e164;
  return `(${m[1]}) ${m[2]}-${m[3]}`;
}

/** `11222333000181` → `11.222.333/0001-81`; CPF idem. Outro tamanho sai cru. */
export function formatDocument(digits: string | null): string {
  if (!digits) return "—";
  if (digits.length === 14) {
    return digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5");
  }
  if (digits.length === 11) {
    return digits.replace(/^(\d{3})(\d{3})(\d{3})(\d{2})$/, "$1.$2.$3-$4");
  }
  return digits;
}

/**
 * "Hoje, 14:32" / "Ontem, 09:10" / "12/08, 14:32".
 *
 * Projeção antiga precisa parecer antiga: mostrar só "14:32" faria uma
 * sincronização de três semanas atrás passar por recente.
 */
export function formatSyncedAt(valor: string | Date | null): string {
  if (!valor) return "—";
  const d = typeof valor === "string" ? new Date(valor) : valor;
  if (Number.isNaN(d.getTime())) return "—";

  const hora = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const hoje = new Date();
  const dia = (x: Date) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;

  if (dia(d) === dia(hoje)) return `Hoje, ${hora}`;

  const ontem = new Date(hoje);
  ontem.setDate(hoje.getDate() - 1);
  if (dia(d) === dia(ontem)) return `Ontem, ${hora}`;

  const data = d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  const ano = d.getFullYear() === hoje.getFullYear() ? "" : `/${d.getFullYear()}`;
  return `${data}${ano}, ${hora}`;
}

export function saudacao(agora: Date = new Date()): string {
  const h = agora.getHours();
  if (h < 12) return "Bom dia";
  if (h < 18) return "Boa tarde";
  return "Boa noite";
}

/** Primeiro nome — cabeçalho fica melhor com "Bom dia, Aline". */
export function primeiroNome(nome: string): string {
  return nome.trim().split(/\s+/)[0] ?? nome;
}
