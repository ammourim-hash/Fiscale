/**
 * O formato da Meta — e a fronteira onde ele morre.
 *
 * Nada além desta pasta conhece `entry`, `changes`, `wa_id` ou
 * `phone_number_id`. O que sai daqui são eventos em português com o
 * vocabulário do Elo, prontos para os serviços que já existem.
 *
 * A regra que isto serve (S34): se o nome de um provedor aparecer no
 * modelo de domínio, algo foi modelado errado.
 *
 * ---------------------------------------------------------------------
 *  Por que a validação é frouxa de propósito
 * ---------------------------------------------------------------------
 *  O webhook é um contrato de terceiro que muda sem avisar. Um schema
 *  rígido que recusa o payload inteiro porque apareceu um campo novo
 *  transformaria uma adição da Meta em queda de recebimento.
 *
 *  Então: percorre-se o que se entende, ignora-se o que não se entende, e
 *  o que não é reconhecido vira contagem — nunca exceção. Perder um tipo
 *  de mensagem novo é ruim; perder TODAS porque um campo novo apareceu é
 *  muito pior.
 */

/** O que o Elo entende de um webhook. Tudo o mais é descartado. */
export type EventoWhatsApp =
  | {
      tipo: "MENSAGEM";
      phoneNumberId: string;
      /** `wa_id` — telefone em E.164 SEM o "+". */
      de: string;
      /** `wamid...` — a chave de idempotência. */
      externalMessageId: string;
      /** Segundos desde a época, como a Meta manda. */
      timestamp: number;
      texto: string;
      /** Nome do perfil, quando o cliente permite. Só exibição. */
      nomeDoPerfil: string | null;
    }
  | {
      tipo: "STATUS";
      phoneNumberId: string;
      externalMessageId: string;
      status: "sent" | "delivered" | "read" | "failed";
      timestamp: number;
      /** Só quando `failed`. Código e título — nunca o payload inteiro. */
      erro: { codigo: number; titulo: string } | null;
    };

/** O que foi ignorado, para virar contagem no log em vez de silêncio. */
export interface Ignorado {
  /** "tipo_nao_suportado", "sem_texto", "campo_desconhecido"… */
  motivo: string;
  quantidade: number;
}

export interface PayloadInterpretado {
  eventos: EventoWhatsApp[];
  ignorados: Ignorado[];
}

/* ─── acesso defensivo ───────────────────────────────────────────────── */

function obj(v: unknown): Record<string, unknown> | null {
  return typeof v === "object" && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function texto(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** A Meta manda o timestamp como STRING de segundos. */
function segundos(v: unknown): number {
  const n = typeof v === "string" ? Number(v) : typeof v === "number" ? v : NaN;
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : Math.floor(Date.now() / 1000);
}

const STATUS_CONHECIDOS = new Set(["sent", "delivered", "read", "failed"]);

/* ─── interpretação ──────────────────────────────────────────────────── */

export function interpretar(bruto: unknown): PayloadInterpretado {
  const eventos: EventoWhatsApp[] = [];
  const contagem = new Map<string, number>();
  const ignorar = (motivo: string) =>
    contagem.set(motivo, (contagem.get(motivo) ?? 0) + 1);

  const raiz = obj(bruto);
  if (!raiz) {
    ignorar("payload_nao_e_objeto");
    return { eventos, ignorados: fechar(contagem) };
  }

  // `object: "whatsapp_business_account"` — outro produto do mesmo app
  // (Instagram, Messenger) chegaria na mesma URL se alguém assinasse.
  if (raiz.object !== "whatsapp_business_account") {
    ignorar("objeto_de_outro_produto");
    return { eventos, ignorados: fechar(contagem) };
  }

  for (const entradaBruta of arr(raiz.entry)) {
    const entrada = obj(entradaBruta);
    if (!entrada) continue;

    for (const mudancaBruta of arr(entrada.changes)) {
      const mudanca = obj(mudancaBruta);
      if (!mudanca) continue;

      // `field` diz de qual assinatura veio. Só `messages` interessa nesta
      // fase; `history`, `smb_message_echoes` e companhia são da 1.8.4.
      if (mudanca.field !== "messages") {
        ignorar(`campo_${typeof mudanca.field === "string" ? mudanca.field : "desconhecido"}`);
        continue;
      }

      const valor = obj(mudanca.value);
      if (!valor) continue;

      const metadata = obj(valor.metadata);
      const phoneNumberId = texto(metadata?.phone_number_id);
      if (!phoneNumberId) {
        ignorar("sem_phone_number_id");
        continue;
      }

      // O nome do perfil vem numa lista à parte, casada por `wa_id`.
      const nomes = new Map<string, string>();
      for (const contatoBruto of arr(valor.contacts)) {
        const contato = obj(contatoBruto);
        const waId = texto(contato?.wa_id);
        const nome = texto(obj(contato?.profile)?.name);
        if (waId && nome) nomes.set(waId, nome);
      }

      for (const mensagemBruta of arr(valor.messages)) {
        const m = obj(mensagemBruta);
        if (!m) continue;

        const id = texto(m.id);
        const de = texto(m.from);
        if (!id || !de) {
          ignorar("mensagem_sem_id_ou_remetente");
          continue;
        }

        // Só TEXT nesta fase. Mídia é a 1.8.3, e um `image` chegando aqui
        // hoje NÃO pode virar mensagem vazia no histórico do cliente.
        if (m.type !== "text") {
          ignorar(`tipo_${typeof m.type === "string" ? m.type : "desconhecido"}`);
          continue;
        }

        const corpo = texto(obj(m.text)?.body);
        if (!corpo) {
          ignorar("texto_vazio");
          continue;
        }

        eventos.push({
          tipo: "MENSAGEM",
          phoneNumberId,
          de,
          externalMessageId: id,
          timestamp: segundos(m.timestamp),
          texto: corpo,
          nomeDoPerfil: nomes.get(de) ?? null,
        });
      }

      for (const statusBruto of arr(valor.statuses)) {
        const s = obj(statusBruto);
        if (!s) continue;

        const id = texto(s.id);
        const estado = texto(s.status);
        if (!id || !estado || !STATUS_CONHECIDOS.has(estado)) {
          ignorar(`status_${estado ?? "sem_valor"}`);
          continue;
        }

        // Só código e título. O objeto de erro da Meta traz `error_data`
        // com detalhe que não precisa entrar no nosso banco.
        const primeiroErro = obj(arr(s.errors)[0]);
        const codigo = primeiroErro?.code;
        const titulo = texto(primeiroErro?.title);

        eventos.push({
          tipo: "STATUS",
          phoneNumberId,
          externalMessageId: id,
          status: estado as "sent" | "delivered" | "read" | "failed",
          timestamp: segundos(s.timestamp),
          erro:
            typeof codigo === "number"
              ? { codigo, titulo: titulo ?? "erro sem título" }
              : null,
        });
      }
    }
  }

  return { eventos, ignorados: fechar(contagem) };
}

function fechar(contagem: Map<string, number>): Ignorado[] {
  return [...contagem.entries()].map(([motivo, quantidade]) => ({ motivo, quantidade }));
}
