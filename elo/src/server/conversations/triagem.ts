/**
 * Triagem por setor — o cliente escolhe Fiscal, Contábil ou DP.
 *
 * =====================================================================
 *  Quem pergunta é a saudação do WhatsApp Business, não o Elo
 * =====================================================================
 *  O número do escritório está em Coexistence, e a saudação automática do
 *  aplicativo continua funcionando (M25). Ela já dispara na primeira
 *  interação — e também depois de 14 dias de silêncio —, que é exatamente
 *  o momento em que um menu do Elo dispararia.
 *
 *  Duas automações no mesmo gatilho dariam duas mensagens ao cliente, no
 *  mesmo segundo, para sempre. Então o menu é DELA; a interpretação e o
 *  roteamento são do Elo. Este arquivo nunca manda a pergunta inicial.
 *
 * =====================================================================
 *  A escolha é exata — adivinhar é pior do que não saber
 * =====================================================================
 *  A comparação é contra a mensagem INTEIRA, normalizada. "fiscal" vale;
 *  "preciso falar sobre um problema fiscal" não vale.
 *
 *  Parece rígido e é o ponto. Mandar para a fila errada é pior do que
 *  deixar sem fila: em "Sem setor" alguém vê e resolve; no Fiscal errado,
 *  o Contábil nunca fica sabendo que existia uma conversa para ele. Por
 *  isso não há busca por palavra dentro da frase, não há pontuação de
 *  similaridade e não há "quase certeza".
 *
 *  É também o que recusa "fiscal e contábil" sem precisar de regra
 *  própria: a frase inteira não é nenhum dos termos aceitos. Duas opções
 *  não são uma escolha.
 *
 * =====================================================================
 *  Estado derivado, sem coluna nova
 * =====================================================================
 *      aguardando setor = status NEW  ∧  assignedDepartmentId IS NULL
 *      fila do setor    = status NEW  ∧  assignedDepartmentId IS NOT NULL
 *      em atendimento   = IN_PROGRESS ∧  assignedMembershipId IS NOT NULL
 *
 *  Um estado que já se lê nos dados não ganha uma segunda fonte de
 *  verdade para divergir dela depois.
 */
import { logger } from "@/server/logging/logger";
import { enviarOrientacaoDoSistema } from "@/server/messages/service";
import { withTenant } from "@/server/tenancy";

import { registrarEscolhaDeSetor } from "./service";

/* ─── os setores ─────────────────────────────────────────────────────── */

export interface Setor {
  slug: string;
  rotulo: string;
  /** Tudo que o cliente pode responder, já normalizado. */
  aceita: string[];
}

/**
 * Os três setores iniciais.
 *
 * O `slug` é o do `Department` no banco, e a ligação é por ele — não por
 * nome, que muda ("Contábil" pode virar "Contabilidade" amanhã sem que a
 * triagem precise saber).
 */
export const SETORES: Setor[] = [
  { slug: "fiscal", rotulo: "Fiscal", aceita: ["1", "fiscal"] },
  { slug: "contabil", rotulo: "Contábil", aceita: ["2", "contabil"] },
  { slug: "dp", rotulo: "DP", aceita: ["3", "dp", "departamento pessoal"] },
];

/** A única mensagem automática que o Elo manda na triagem. */
export const ORIENTACAO = [
  "Não consegui identificar o setor. Responda com:",
  "1 Fiscal",
  "2 Contábil",
  "3 DP",
].join("\n");

/* ─── interpretação ──────────────────────────────────────────────────── */

/**
 * Tira acento, caixa, espaço repetido e pontuação de borda.
 *
 * "CONTÁBIL" , " contábil. " e "Contabil" viram a mesma coisa; o miolo da
 * frase continua intacto, para que "fiscal e contabil" NÃO vire "fiscal".
 */
function normalizar(texto: string): string {
  return texto
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[.,;:!?)\]}"'…]+$/u, "")
    .replace(/^[(\[{"']+/u, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * O setor escolhido, ou `null`.
 *
 * `null` cobre tudo que não é escolha: texto livre, duas opções, opção
 * inexistente, vazio. Quem chama não precisa distinguir — o tratamento é
 * o mesmo, e é não fazer nada.
 */
export function interpretarEscolha(texto: string): Setor | null {
  const alvo = normalizar(texto);
  if (!alvo) return null;

  const achados = SETORES.filter((s) => s.aceita.includes(alvo));

  // Dois setores para a mesma resposta seria erro de configuração, não do
  // cliente — e escolher um deles em silêncio seria o defeito que o resto
  // deste arquivo existe para evitar.
  return achados.length === 1 ? (achados[0] ?? null) : null;
}

/* ─── aplicação ──────────────────────────────────────────────────────── */

export type ResultadoTriagem =
  | { tipo: "NAO_APLICA"; motivo: "STATUS" | "JA_TEM_SETOR" | "TEM_RESPONSAVEL" | "SEM_CONVERSA" }
  | { tipo: "SETOR_DEFINIDO"; slug: string }
  | { tipo: "SETOR_NAO_CADASTRADO"; slug: string }
  | { tipo: "NAO_ENTENDI"; orientou: boolean };

/**
 * Roda DEPOIS de a mensagem estar gravada. Sempre.
 *
 * Quem chama trata qualquer exceção daqui como ruído: a mensagem, o
 * atendimento, o SSE e o Web Push já aconteceram, e nenhuma falha de
 * triagem pode desfazê-los. Ver o `catch` em `inbound.ts`.
 */
export async function triarSePreciso(
  tenantId: string,
  conversationId: string,
  texto: string,
): Promise<ResultadoTriagem> {
  const conversa = await withTenant(tenantId, async (tx) =>
    tx.conversation.findUnique({
      where: { id: conversationId },
      select: { status: true, assignedDepartmentId: true, assignedMembershipId: true },
    }),
  );

  if (!conversa) return { tipo: "NAO_APLICA", motivo: "SEM_CONVERSA" };

  // Já tem setor: a pergunta foi respondida um dia, e repetir seria tratar
  // o cliente como se ele não tivesse respondido.
  if (conversa.assignedDepartmentId) return { tipo: "NAO_APLICA", motivo: "JA_TEM_SETOR" };

  // Alguém assumiu. A automação não entra na frente de uma pessoa.
  if (conversa.assignedMembershipId) return { tipo: "NAO_APLICA", motivo: "TEM_RESPONSAVEL" };

  // Fora de NEW o atendimento já andou — inclusive porque alguém do
  // escritório respondeu, o que move NEW para IN_PROGRESS.
  if (conversa.status !== "NEW") return { tipo: "NAO_APLICA", motivo: "STATUS" };

  const setor = interpretarEscolha(texto);

  if (!setor) {
    // A PRIMEIRA mensagem do cliente não é uma resposta ao menu — ela é o
    // que FAZ o menu aparecer. A saudação do aplicativo dispara na
    // primeira interação, e nesse instante o cliente ainda não viu opção
    // nenhuma. Orientar aqui entregaria duas mensagens automáticas de uma
    // vez, que é exatamente o que este desenho existe para evitar.
    //
    // Por isso a orientação é "adicional" no sentido literal: ela só faz
    // sentido a partir da segunda mensagem, quando o menu já foi visto e
    // ainda assim a resposta não deu para entender.
    const recebidas = await withTenant(tenantId, async (tx) =>
      tx.message.count({ where: { conversationId, direction: "INBOUND" } }),
    );

    if (recebidas < 2) return { tipo: "NAO_ENTENDI", orientou: false };

    const r = await enviarOrientacaoDoSistema(tenantId, conversationId, ORIENTACAO);
    // `enviada: false` aqui é o caso NORMAL da terceira mensagem sem
    // escolha: a orientação já saiu uma vez, e a conversa fica para
    // triagem humana, visível em "Sem setor".
    return { tipo: "NAO_ENTENDI", orientou: r.enviada };
  }

  const departamento = await withTenant(tenantId, async (tx) =>
    tx.department.findUnique({
      where: { tenantId_slug: { tenantId, slug: setor.slug } },
      select: { id: true },
    }),
  );

  if (!departamento) {
    // Setor reconhecido, área inexistente neste escritório. Não se cria
    // departamento a partir de uma mensagem de cliente: isso é decisão
    // administrativa, e criar aqui encheria a base de área fantasma.
    logger.warn("triagem.setor_nao_cadastrado", { tenantId, slug: setor.slug });
    return { tipo: "SETOR_NAO_CADASTRADO", slug: setor.slug };
  }

  const { aplicada } = await registrarEscolhaDeSetor(tenantId, conversationId, {
    departmentId: departamento.id,
    slug: setor.slug,
    rotulo: setor.rotulo,
  });

  // `aplicada: false` significa que o estado mudou entre a leitura e o
  // UPDATE — alguém assumiu no meio. O WHERE condicional resolveu, e não
  // há nada a corrigir.
  return aplicada
    ? { tipo: "SETOR_DEFINIDO", slug: setor.slug }
    : { tipo: "NAO_APLICA", motivo: "TEM_RESPONSAVEL" };
}
