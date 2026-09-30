"use client";

/**
 * O corpo da conversa: histórico, notas internas e o campo de envio.
 *
 * ---------------------------------------------------------------------
 *  As três regras visuais desta tela
 * ---------------------------------------------------------------------
 *
 *  1. A ASSINATURA APARECE SEMPRE, inclusive para quem escreveu. Nada de
 *     "Você": num atendimento compartilhado, o que importa é quem do
 *     escritório falou — e quem lê o histórico amanhã não é quem digitou
 *     hoje.
 *
 *  2. NOTA INTERNA É IMPOSSÍVEL DE CONFUNDIR COM MENSAGEM. Não tem forma
 *     de balão, tem faixa própria, e diz por extenso que não vai para o
 *     cliente. O campo de escrita muda de cor junto — o erro que a tela
 *     precisa impedir é a pessoa achar que escreveu uma nota e ter
 *     mandado ao cliente.
 *
 *  3. NADA DE APARÊNCIA DE WHATSAPP. Sem verde, sem fundo de papel de
 *     parede, sem rabinho no balão. O Elo é ferramenta de escritório.
 *
 * ---------------------------------------------------------------------
 *  Quando uma mensagem conta como lida
 * ---------------------------------------------------------------------
 *  Três condições ao mesmo tempo: a conversa está aberta, a JANELA está
 *  visível, e a mensagem entrou de fato na área visível (IntersectionObserver).
 *  Só então o navegador avisa o servidor.
 *
 *  Renderizar não basta. Se bastasse, prefetch e aba em segundo plano
 *  zerariam o contador de quem não olhou nada — e contador em que a equipe
 *  não confia é pior do que contador nenhum.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { formatSyncedAt } from "@/lib/format";

import { GravadorDeVoz, type GravacaoPronta } from "./gravador";
import { AnexoNaMensagem, formatarTamanho, type Anexo } from "./media";
import { EmptyState } from "./ui";

/* ─── tipos ──────────────────────────────────────────────────────────── */

export interface Mensagem {
  id: string;
  sequence: number;
  direction: "INBOUND" | "OUTBOUND" | "SYSTEM";
  type: "TEXT" | "IMAGE" | "DOCUMENT" | "AUDIO" | "VOICE";
  content: string;
  attachments: Anexo[];
  senderDisplayName: string | null;
  senderDepartmentName: string | null;
  senderMembershipId: string | null;
  externalSenderId: string | null;
  createdAt: string;
  viewedBy: { membershipId: string; name: string; department: string | null; viewedAt: string }[];
}

export interface NotaInterna {
  id: string;
  content: string;
  createdAt: string;
  author: { membershipId: string; name: string; department: string | null };
}

/** Uma mensagem que ainda não voltou do servidor. Só existe nesta aba. */
interface Pendente {
  clientMessageId: string;
  content: string;
  estado: "enviando" | "falhou";
  /** Descricao do que esta subindo: "Foto", "guia.pdf"... */
  rotulo?: string;
  /** Ids ja enviados. O retry REUSA — nao sobe o arquivo de novo. */
  attachmentIds?: string[];
  /** Guardado para o retry poder repetir o upload se ele e que falhou. */
  arquivo?: File | null;
}

interface Props {
  conversationId: string;
  mensagens: Mensagem[];
  notas: NotaInterna[];
  hasMore: boolean;
  carregandoAntigas: boolean;
  onCarregarAntigas: () => void;
  onEnviada: () => void;
  onNotaCriada: () => void;
  podeEnviar: boolean;
  /** Cabeçalho, ações e painéis: renderizados acima do histórico. */
  children?: React.ReactNode;
}

/**
 * O `accept` do seletor de arquivo.
 *
 * Conveniencia, nao seguranca: ele so filtra o que aparece na janela do
 * sistema, e qualquer um arrasta outra coisa. Quem decide de verdade e o
 * servidor, olhando extensao, MIME e magic bytes.
 */
const ACEITOS = [
  ".jpg,.jpeg,.png,.webp",
  ".pdf,.doc,.docx,.xls,.xlsx",
  ".mp3,.m4a,.aac,.ogg,.opus,.wav",
].join(",");

/* ─── a linha do tempo ───────────────────────────────────────────────── */

type Item =
  | { tipo: "mensagem"; em: number; m: Mensagem }
  | { tipo: "nota"; em: number; n: NotaInterna }
  | { tipo: "pendente"; em: number; p: Pendente };

export function MessageThread({
  conversationId,
  mensagens,
  notas,
  hasMore,
  carregandoAntigas,
  onCarregarAntigas,
  onEnviada,
  onNotaCriada,
  podeEnviar,
  children,
}: Props) {
  const [modo, setModo] = useState<"mensagem" | "nota">("mensagem");
  const [texto, setTexto] = useState("");
  const [pendentes, setPendentes] = useState<Pendente[]>([]);
  const [ocupado, setOcupado] = useState(false);

  const corpoRef = useRef<HTMLDivElement | null>(null);
  const fimRef = useRef<HTMLDivElement | null>(null);

  /**
   * Mensagens e notas na mesma linha do tempo.
   *
   * A ordem das MENSAGENS é a do `sequence`, e nada mais — é assim que o
   * servidor as devolve, e é o único critério que não empata. Ordenar o
   * conjunto por horário parece equivalente e não é: basta um relógio
   * fora de hora, uma importação futura com data de origem ou duas
   * mensagens no mesmo milissegundo para a conversa aparecer embaralhada.
   *
   * As notas são encaixadas POR HORÁRIO entre as mensagens, que é o que
   * faz sentido para elas — e sem nunca reordenar as mensagens.
   *
   * As notas só entram na janela já carregada: enquanto houver histórico
   * mais antigo por carregar, uma nota velha no topo daria a impressão de
   * que a conversa começou ali.
   */
  const itens = useMemo<Item[]>(() => {
    const maisAntiga = mensagens[0] ? Date.parse(mensagens[0].createdAt) : 0;

    const notasVisiveis = notas
      .map((n) => ({ n, em: Date.parse(n.createdAt) }))
      .filter(({ em }) => !hasMore || em >= maisAntiga)
      .sort((a, b) => a.em - b.em);

    const lista: Item[] = [];
    let proximaNota = 0;

    for (const m of mensagens) {
      const em = Date.parse(m.createdAt);
      // Toda nota anterior a esta mensagem entra antes dela.
      while (proximaNota < notasVisiveis.length && notasVisiveis[proximaNota]!.em < em) {
        const { n, em: emNota } = notasVisiveis[proximaNota]!;
        lista.push({ tipo: "nota", em: emNota, n });
        proximaNota += 1;
      }
      lista.push({ tipo: "mensagem", em, m });
    }

    for (; proximaNota < notasVisiveis.length; proximaNota += 1) {
      const { n, em } = notasVisiveis[proximaNota]!;
      lista.push({ tipo: "nota", em, n });
    }

    for (const p of pendentes) {
      lista.push({ tipo: "pendente", em: Number.MAX_SAFE_INTEGER, p });
    }
    return lista;
  }, [mensagens, notas, pendentes, hasMore]);

  /* ── marcar como lida ────────────────────────────────────────────── */

  // Acumula e envia em lote: rolar a tela dispara dezenas de interseções,
  // e uma requisição por mensagem visível seria absurdo.
  const naFila = useRef<Set<string>>(new Set());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const enfileirarLeitura = useCallback(
    (messageId: string) => {
      naFila.current.add(messageId);
      if (timer.current) return;
      timer.current = setTimeout(() => {
        timer.current = null;
        const ids = [...naFila.current];
        naFila.current.clear();
        if (ids.length === 0) return;
        void fetch(`/api/conversations/${conversationId}/messages/read`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ messageIds: ids }),
        }).catch(() => {
          /* sem rede: a próxima passagem pela tela tenta de novo */
        });
      }, 700);
    },
    [conversationId],
  );

  useEffect(() => {
    const corpo = corpoRef.current;
    if (!corpo) return;

    const observador = new IntersectionObserver(
      (entradas) => {
        // A janela precisa estar visível E em foco. Aba de fundo com a
        // conversa aberta não lê nada.
        if (document.visibilityState !== "visible" || !document.hasFocus()) return;
        for (const e of entradas) {
          if (!e.isIntersecting) continue;
          const id = (e.target as HTMLElement).dataset.mensagemNaoLida;
          if (id) {
            enfileirarLeitura(id);
            observador.unobserve(e.target);
          }
        }
      },
      { root: corpo, threshold: 0.6 },
    );

    for (const el of corpo.querySelectorAll<HTMLElement>("[data-mensagem-nao-lida]")) {
      observador.observe(el);
    }
    return () => observador.disconnect();
  }, [itens, enfileirarLeitura]);

  /* ── rolagem ─────────────────────────────────────────────────────── */

  const ultimaId = mensagens[mensagens.length - 1]?.id ?? null;
  useEffect(() => {
    // Só rola para o fim quando chega coisa nova — nunca ao carregar
    // histórico antigo, que jogaria a pessoa para longe do que ela leu.
    fimRef.current?.scrollIntoView({ block: "end" });
  }, [ultimaId, pendentes.length]);

  /* ── envio ───────────────────────────────────────────────────────── */

  /**
   * Sobe o arquivo e devolve o id.
   *
   * Dois estágios de propósito: o upload é o passo caro, e separá-lo do
   * POST da mensagem é o que permite o retry NÃO reenviar os bytes. Ver o
   * cabeçalho de api/uploads.
   */
  const subirArquivo = useCallback(
    async (arquivo: File, voz?: { durationMs: number }): Promise<string> => {
      const forma = new FormData();
      forma.append("file", arquivo);
      if (voz) {
        forma.append("voice", "1");
        forma.append("durationMs", String(voz.durationMs));
      }
      const r = await fetch("/api/uploads", { method: "POST", body: forma });
      const d = (await r.json()) as { id?: string; error?: { message: string } };
      if (!r.ok || !d.id) throw new Error(d.error?.message ?? "falha no upload");
      return d.id;
    },
    [],
  );

  const postarMensagem = useCallback(
    async (corpo: {
      content: string;
      clientMessageId: string;
      attachmentIds?: string[];
    }) => {
      const r = await fetch(`/api/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(corpo),
      });
      if (!r.ok) {
        const d = (await r.json().catch(() => null)) as { error?: { message: string } } | null;
        throw new Error(d?.error?.message ?? String(r.status));
      }
    },
    [conversationId],
  );

  /**
   * O caminho único de envio — texto, arquivo ou voz.
   *
   * A ordem é: sobe o arquivo (se houver), depois cria a mensagem. Se a
   * mensagem falhar, o arquivo fica PENDING e a faxina o recolhe em duas
   * horas; nunca fica mensagem apontando para arquivo inexistente.
   *
   * No retry, `attachmentIds` já preenchido é REUSADO: o mesmo arquivo não
   * sobe duas vezes, e o mesmo `clientMessageId` garante uma mensagem só.
   */
  const despachar = useCallback(
    async (p: Pendente, arquivo: File | null, voz?: { durationMs: number }) => {
      try {
        let ids = p.attachmentIds ?? [];
        if (arquivo && ids.length === 0) {
          ids = [await subirArquivo(arquivo, voz)];
          // Guarda o id: se o POST falhar, o retry reaproveita.
          setPendentes((atual) =>
            atual.map((x) =>
              x.clientMessageId === p.clientMessageId ? { ...x, attachmentIds: ids } : x,
            ),
          );
        }

        await postarMensagem({
          content: p.content,
          clientMessageId: p.clientMessageId,
          ...(ids.length > 0 ? { attachmentIds: ids } : {}),
        });

        setPendentes((atual) => atual.filter((x) => x.clientMessageId !== p.clientMessageId));
        onEnviada();
      } catch (erro: unknown) {
        setPendentes((atual) =>
          atual.map((x) =>
            x.clientMessageId === p.clientMessageId
              ? { ...x, estado: "falhou", motivo: erro instanceof Error ? erro.message : null }
              : x,
          ),
        );
      }
    },
    [subirArquivo, postarMensagem, onEnviada],
  );

  const enviarArquivo = useCallback(
    (arquivo: File, voz?: { durationMs: number }) => {
      const p: Pendente = {
        clientMessageId: criarId(),
        content: "",
        estado: "enviando",
        rotulo: voz ? "Mensagem de voz" : arquivo.name,
        arquivo,
      };
      setPendentes((atual) => [...atual, p]);
      void despachar(p, arquivo, voz);
    },
    [despachar],
  );

  const submeter = useCallback(async () => {
    const conteudo = texto.trim();
    if (!conteudo || ocupado) return;

    if (modo === "nota") {
      setOcupado(true);
      try {
        await fetch(`/api/conversations/${conversationId}/notes`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ content: conteudo }),
        });
        setTexto("");
        onNotaCriada();
      } finally {
        setOcupado(false);
      }
      return;
    }

    const p: Pendente = {
      clientMessageId: criarId(),
      content: conteudo,
      estado: "enviando",
    };
    setPendentes((atual) => [...atual, p]);
    setTexto("");
    void despachar(p, null);
  }, [texto, ocupado, modo, conversationId, despachar, onNotaCriada]);

  const tentarDeNovo = useCallback(
    (p: Pendente) => {
      setPendentes((atual) =>
        atual.map((x) =>
          x.clientMessageId === p.clientMessageId ? { ...x, estado: "enviando" } : x,
        ),
      );
      void despachar(p, p.arquivo ?? null);
    },
    [despachar],
  );

  /* ── render ──────────────────────────────────────────────────────── */

  return (
    <>
      <div className="conversa-corpo" ref={corpoRef}>
        {children}

        {hasMore ? (
          <div className="carregar-antigas">
            <button
              type="button"
              className="btn"
              disabled={carregandoAntigas}
              onClick={onCarregarAntigas}
            >
              {carregandoAntigas ? "Carregando…" : "Carregar mensagens anteriores"}
            </button>
          </div>
        ) : null}

        {itens.length === 0 ? (
          <EmptyState
            compact
            title="Nenhuma mensagem ainda."
            description="Escreva abaixo para começar, ou use uma nota interna para registrar o que já foi tratado."
          />
        ) : null}

        <ol className="lista-mensagens">
          {itens.map((item) =>
            item.tipo === "mensagem" ? (
              <Balao key={item.m.id} m={item.m} />
            ) : item.tipo === "nota" ? (
              <NotaNaLinha key={`n-${item.n.id}`} n={item.n} />
            ) : (
              <li key={item.p.clientMessageId} className="msg msg-saida msg-pendente">
                <div className="balao-msg">
                  <p className="msg-texto">
                    {item.p.content || item.p.rotulo || "Anexo"}
                  </p>
                  <p className="msg-rodape">
                    {item.p.estado === "enviando" ? (
                      "Enviando…"
                    ) : (
                      <>
                        <span className="msg-falhou">Falhou.</span>{" "}
                        <button
                          type="button"
                          className="link-botao"
                          onClick={() => void tentarDeNovo(item.p)}
                        >
                          Tentar novamente
                        </button>
                      </>
                    )}
                  </p>
                </div>
              </li>
            ),
          )}
        </ol>
        <div ref={fimRef} />
      </div>

      <form
        className={`rodape-envio ${modo === "nota" ? "rodape-nota" : ""}`}
        onSubmit={(e) => {
          e.preventDefault();
          void submeter();
        }}
      >
        <div className="rodape-abas" role="group" aria-label="O que você vai escrever">
          <button
            type="button"
            className="filtro"
            aria-pressed={modo === "mensagem"}
            onClick={() => setModo("mensagem")}
          >
            Mensagem
          </button>
          <button
            type="button"
            className="filtro filtro-nota"
            aria-pressed={modo === "nota"}
            onClick={() => setModo("nota")}
          >
            Nota interna
          </button>
        </div>

        {modo === "nota" ? (
          <p className="nota-alerta nota-alerta-forte">
            Nota interna — não será enviada ao cliente.
          </p>
        ) : null}

        <label className="so-leitor" htmlFor={`escrever-${conversationId}`}>
          {modo === "nota" ? "Nota interna" : "Mensagem para o cliente"}
        </label>
        <textarea
          id={`escrever-${conversationId}`}
          className="campo-envio"
          rows={2}
          value={texto}
          disabled={modo === "mensagem" && !podeEnviar}
          placeholder={
            modo === "nota"
              ? "Registrar algo para a equipe…"
              : podeEnviar
                ? "Escreva sua resposta. Enter envia, Shift+Enter quebra linha."
                : "Seu perfil não envia mensagens."
          }
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={(e) => {
            // Enter envia, Shift+Enter quebra linha. `isComposing` protege
            // quem usa teclado com acentuação por composição: sem isso, o
            // Enter que confirma o acento enviaria a mensagem pela metade.
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void submeter();
            }
          }}
        />

        <div className="rodape-acoes">
          {/* Anexo e voz só existem no modo mensagem: nota interna com
              arquivo ficou fora desta fase (ver o README). */}
          {modo === "mensagem" && podeEnviar ? (
            <>
              <label className="btn btn-anexar">
                <span aria-hidden="true">📎</span> Anexar
                <input
                  type="file"
                  className="so-leitor"
                  accept={ACEITOS}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    // Limpa o input para que escolher o MESMO arquivo de
                    // novo volte a disparar o evento.
                    e.target.value = "";
                    if (f) enviarArquivo(f);
                  }}
                />
              </label>

              <GravadorDeVoz
                ocupado={ocupado}
                aoEnviar={(g: GravacaoPronta) => {
                  const arquivo = new File([g.blob], g.nomeArquivo, { type: g.blob.type });
                  enviarArquivo(arquivo, { durationMs: g.durationMs });
                }}
              />
            </>
          ) : null}

          <button
            type="submit"
            className="btn btn-acao"
            disabled={!texto.trim() || ocupado || (modo === "mensagem" && !podeEnviar)}
          >
            {modo === "nota" ? "Salvar nota" : "Enviar"}
          </button>
        </div>
      </form>
    </>
  );
}

/* ─── peças ──────────────────────────────────────────────────────────── */

function Balao({ m }: { m: Mensagem }) {
  const entrada = m.direction === "INBOUND";
  const naoLidaPorMim = entrada;

  return (
    <li
      className={`msg ${entrada ? "msg-entrada" : "msg-saida"}`}
      {...(naoLidaPorMim ? { "data-mensagem-nao-lida": m.id } : {})}
    >
      <div className="balao-msg">
        {/* A assinatura aparece SEMPRE, inclusive para quem escreveu — e é
            a que foi congelada no envio, não o setor atual da pessoa. */}
        <p className="msg-autor">
          {entrada ? (
            "Cliente"
          ) : (
            <>
              <strong>{m.senderDisplayName ?? "Escritório"}</strong>
              {m.senderDepartmentName ? (
                <span className="msg-area"> • {m.senderDepartmentName}</span>
              ) : null}
            </>
          )}
        </p>
        {m.attachments.map((a) => (
          <AnexoNaMensagem key={a.id} anexo={a} />
        ))}

        {m.content ? <p className="msg-texto">{m.content}</p> : null}

        <p className="msg-rodape">
          <time dateTime={m.createdAt}>{hora(m.createdAt)}</time>
          {m.attachments[0] && m.type === "DOCUMENT" ? (
            <span className="msg-tamanho"> · {formatarTamanho(m.attachments[0].sizeBytes)}</span>
          ) : null}
        </p>
      </div>

      {m.viewedBy.length > 0 ? (
        <p className="msg-vistos">
          Visualizada por{" "}
          {m.viewedBy
            .map((v) => `${primeiro(v.name)}${v.department ? ` • ${v.department}` : ""}`)
            .join(", ")}
        </p>
      ) : null}
    </li>
  );
}

function NotaNaLinha({ n }: { n: NotaInterna }) {
  return (
    <li className="msg msg-nota">
      <div className="bloco-nota">
        <p className="nota-cabecalho">
          Nota interna · não enviada ao cliente
          <time dateTime={n.createdAt}>{hora(n.createdAt)}</time>
        </p>
        <p className="msg-texto">{n.content}</p>
        <p className="msg-rodape">
          {primeiro(n.author.name)}
          {n.author.department ? ` • ${n.author.department}` : ""}
        </p>
      </div>
    </li>
  );
}

/* ─── auxiliares ─────────────────────────────────────────────────────── */

function primeiro(nome: string): string {
  return nome.trim().split(/\s+/)[0] ?? nome;
}

function hora(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const hoje = new Date();
  const mesmoDia =
    d.getFullYear() === hoje.getFullYear() &&
    d.getMonth() === hoje.getMonth() &&
    d.getDate() === hoje.getDate();
  return mesmoDia
    ? d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })
    : formatSyncedAt(iso);
}

/** `randomUUID` não existe em contexto inseguro; o fallback cobre isso. */
function criarId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
