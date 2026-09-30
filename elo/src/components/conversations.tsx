"use client";

/**
 * Atendimentos — lista, conversa e as ações sobre o caso.
 *
 * Três regras que o arquivo aplica o tempo todo:
 *
 *   ABRIR NÃO É ASSUMIR. Selecionar um atendimento registra visualização
 *   e nada mais. Assumir é um botão, com nome.
 *
 *   ABRIR TAMBÉM NÃO É LER. Visualizar o ATENDIMENTO e visualizar cada
 *   MENSAGEM são registros diferentes. Quem marca mensagem como lida é o
 *   observador de visibilidade em messages.tsx, e não este arquivo.
 *
 *   A API É A VERDADE. O realtime só avisa que algo mudou; quem responde
 *   "o que mudou" é uma nova leitura. Assim um evento perdido numa queda
 *   de conexão custa um atraso, nunca uma tela errada.
 *
 * As ações mandam `expectedVersion` junto. Se o atendimento mudou desde
 * que a tela carregou, o servidor devolve 409 e a pessoa lê o que houve —
 * em vez de sobrescrever a decisão de um colega sem saber.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { LinkModo } from "./embutido";

import { formatDocument, formatPhone, formatSyncedAt } from "@/lib/format";

import { MessageThread, type Mensagem } from "./messages";
import { definirConversaAberta, useRealtime, type EventoRealtime } from "./realtime";
import {
  Avatar,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
  type StatusTone,
} from "./ui";

/* ─── tipos ──────────────────────────────────────────────────────────── */

export type Status =
  | "NEW"
  | "IN_PROGRESS"
  | "WAITING_CUSTOMER"
  | "WAITING_OFFICE"
  | "RESOLVED";

export const ROTULO_STATUS: Record<Status, string> = {
  NEW: "Novo",
  IN_PROGRESS: "Em atendimento",
  WAITING_CUSTOMER: "Aguardando cliente",
  WAITING_OFFICE: "Aguardando escritório",
  RESOLVED: "Resolvido",
};

const TOM_STATUS: Record<Status, StatusTone> = {
  NEW: "acao",
  IN_PROGRESS: "ok",
  WAITING_CUSTOMER: "atencao",
  WAITING_OFFICE: "atencao",
  RESOLVED: "neutro",
};

export interface Resumo {
  id: string;
  status: Status;
  channel: string;
  version: number;
  createdAt: string;
  lastActivityAt: string;
  customer: { id: string; displayName: string } | null;
  assignee: { membershipId: string; name: string } | null;
  department: { id: string; name: string } | null;
  tags: { id: string; name: string; tone: string }[];
  viewedByMe: boolean;
  unreadCount: number;
  preview: { direction: "INBOUND" | "OUTBOUND" | "SYSTEM"; content: string } | null;
}

interface Contagens {
  novos: number;
  naoLidos: number;
  meus: number;
  aguardando: number;
  semSetor: number;
  todos: number;
}

const FILTROS = [
  { id: "novos", rotulo: "Novos", chave: "novos" as const, vazio: "Nenhum atendimento novo." },
  { id: "nao-lidos", rotulo: "Não lidos", chave: "naoLidos" as const, vazio: "Você já abriu todos." },
  { id: "meus", rotulo: "Meus", chave: "meus" as const, vazio: "Nenhum atendimento sob sua responsabilidade." },
  { id: "aguardando", rotulo: "Aguardando", chave: "aguardando" as const, vazio: "Ninguém aguardando resposta." },
  // A fila da triagem. Fica ANTES de "Todos" porque é fila de trabalho, e
  // não um recorte de consulta: quem está aqui não foi para setor nenhum.
  { id: "sem-setor", rotulo: "Sem setor", chave: "semSetor" as const, vazio: "Ninguém esperando triagem." },
  { id: "todos", rotulo: "Todos", chave: "todos" as const, vazio: "Nenhum atendimento ainda." },
] as const;

type FiltroId = (typeof FILTROS)[number]["id"];

/* ─── tela ───────────────────────────────────────────────────────────── */

export function ConversationsScreen({
  membershipId,
  podeEnviar,
  entradaDev = false,
  abrirInicial = null,
}: {
  membershipId: string;
  podeEnviar: boolean;
  /** A entrada de mensagem de teste só existe em desenvolvimento. */
  entradaDev?: boolean;
  /** Atendimento a abrir de saída — o destino de uma notificação. */
  abrirInicial?: string | null;
}) {
  const [filtro, setFiltro] = useState<FiltroId>("novos");

  // `?abrir=<id>` é o destino de um clique em notificação. Ele decide
  // apenas QUAL tela abrir: o atendimento em si continua sendo carregado
  // pela API, com sessão, tenant, RBAC e RLS — um id de outro escritório
  // simplesmente não é encontrado, e a tela mostra o erro de sempre.
  const [selecionado, setSelecionado] = useState<string | null>(abrirInicial ?? null);

  // A casca precisa saber qual conversa está na frente para não tocar o
  // som da mensagem que a pessoa está lendo. Publicar aqui, e não elevar
  // o estado, porque o layout é Server Component e não pode segurá-lo.
  useEffect(() => {
    definirConversaAberta(selecionado);
    return () => definirConversaAberta(null);
  }, [selecionado]);

  // `tentativa` existe para o botão "tentar novamente" poder pedir uma
  // nova busca sem que o efeito precise escrever estado antes do fetch.
  const [tentativa, setTentativa] = useState(0);
  const [dados, setDados] = useState<{
    filtro: FiltroId;
    lista: Resumo[];
    contagens: Contagens;
  } | null>(null);
  const [erro, setErro] = useState<FiltroId | null>(null);

  // Nada de setState no corpo do efeito: o "carregando" é DERIVADO de o
  // que está na tela não ser o filtro pedido. Resetar estado antes de
  // buscar causaria um render a mais só para apagar o que já ia sumir.
  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const r = await fetch(`/api/conversations?filtro=${filtro}`, {
          headers: { accept: "application/json" },
        });
        if (!r.ok) throw new Error(String(r.status));
        const d = (await r.json()) as { conversations: Resumo[]; counts: Contagens };
        if (!vivo) return;
        setDados({ filtro, lista: d.conversations, contagens: d.counts });
        setErro(null);
      } catch {
        if (vivo) setErro(filtro);
      }
    })();
    return () => {
      vivo = false;
    };
  }, [filtro, tentativa]);

  const recarregar = useCallback(() => setTentativa((n) => n + 1), []);

  // Um contador só para o detalhe: evento sobre a conversa aberta manda
  // ELA recarregar, sem refazer a lista inteira a cada mensagem.
  const [pulsoDetalhe, setPulsoDetalhe] = useState(0);

  const aoEvento = useCallback(
    (e: EventoRealtime) => {
      // Qualquer evento move a lista: prévia, contador, ordem por
      // atividade. Recarregar é uma consulta barata e sempre correta —
      // remendar a lista no cliente seria manter um segundo estado que
      // diverge do banco na primeira condição de corrida.
      recarregar();
      if (e.conversationId === selecionado) setPulsoDetalhe((n) => n + 1);
    },
    [recarregar, selecionado],
  );

  const { estado: conexao, geracao } = useRealtime(selecionado, aoEvento);

  // Reconectou: nada garante que os eventos do intervalo tenham chegado.
  // A resposta é reler, não tentar adivinhar o que se perdeu.
  const primeiraGeracao = useRef(true);
  useEffect(() => {
    if (primeiraGeracao.current) {
      primeiraGeracao.current = false;
      return;
    }
    recarregar();
    setPulsoDetalhe((n) => n + 1);
  }, [geracao, recarregar]);

  const lista = dados?.filtro === filtro ? dados.lista : null;
  const contagens = dados?.contagens ?? null;
  const falhou = erro === filtro;

  const atual = FILTROS.find((f) => f.id === filtro) ?? FILTROS[0];

  return (
    <div className={`duas-colunas ${selecionado ? "com-conversa" : ""}`}>
      <section className="coluna-lista" aria-label="Atendimentos">
        <header className="coluna-topo">
          <h2>Atendimentos</h2>
          {conexao === "reconectando" ? (
            <span className="conexao-aviso" role="status">
              Reconectando…
            </span>
          ) : null}
        </header>

        <div className="filtros" role="group" aria-label="Filtrar atendimentos">
          {FILTROS.map((f) => (
            <button
              key={f.id}
              type="button"
              className="filtro"
              aria-pressed={filtro === f.id}
              onClick={() => setFiltro(f.id)}
            >
              {f.rotulo}
              {contagens && contagens[f.chave] > 0 ? (
                <span className="filtro-contador">{contagens[f.chave]}</span>
              ) : null}
            </button>
          ))}
        </div>

        {lista === null && !falhou ? <LoadingState /> : null}

        {falhou ? (
          <ErrorState
            title="Não foi possível carregar os atendimentos."
            onRetry={recarregar}
          />
        ) : null}

        {lista?.length === 0 ? (
          <EmptyState
            compact
            title={atual.vazio}
            description="As conversas aparecem aqui quando o canal do WhatsApp for ligado."
          />
        ) : null}

        {lista && lista.length > 0 ? (
          <ul className="lista-atendimentos">
            {lista.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className="atendimento-item"
                  aria-current={selecionado === c.id ? "true" : undefined}
                  onClick={() => setSelecionado(c.id)}
                >
                  <Avatar name={c.customer?.displayName ?? "Contato novo"} size={34} square />
                  <span className="atendimento-texto">
                    <span className="atendimento-nome">
                      {c.unreadCount > 0 || !c.viewedByMe ? (
                        <span className="ponto-nao-lido" title="Não lido por você">
                          <span className="so-leitor">Não lido</span>
                        </span>
                      ) : null}
                      {c.customer?.displayName ?? "Contato não identificado"}
                    </span>
                    {/* A prévia diz do que se trata; sem ela a lista é uma
                        coluna de nomes e a pessoa abre um por um. */}
                    <span className="atendimento-previa">
                      {c.preview
                        ? `${c.preview.direction === "OUTBOUND" ? "Você: " : ""}${c.preview.content}`
                        : c.assignee
                          ? primeiro(c.assignee.name)
                          : "Não atribuído"}
                    </span>
                    <span className="atendimento-sub">
                      {c.assignee ? primeiro(c.assignee.name) : "Não atribuído"}
                      {c.department ? ` · ${c.department.name}` : ""}
                    </span>
                  </span>
                  <span className="atendimento-direita">
                    <StatusBadge tone={TOM_STATUS[c.status]}>
                      {ROTULO_STATUS[c.status]}
                    </StatusBadge>
                    {c.unreadCount > 0 ? (
                      <span className="contador-nao-lidas">
                        {c.unreadCount}
                        <span className="so-leitor"> mensagens não lidas</span>
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {selecionado ? (
        <ConversationDetail
          key={selecionado}
          id={selecionado}
          membershipId={membershipId}
          podeEnviar={podeEnviar}
          entradaDev={entradaDev}
          pulso={pulsoDetalhe}
          onChange={recarregar}
          onFechar={() => setSelecionado(null)}
        />
      ) : (
        <EmptyConversation total={contagens?.todos ?? 0} />
      )}
    </div>
  );
}

function primeiro(nome: string): string {
  return nome.trim().split(/\s+/)[0] ?? nome;
}

/* ─── área principal vazia ───────────────────────────────────────────── */

export function EmptyConversation({ total }: { total: number }) {
  const primeiraVez = total === 0;

  return (
    <section className="coluna-conversa" aria-label="Conversa">
      <div className="conversa-vazia">
        <h2>
          {primeiraVez ? "Selecione um atendimento para começar." : "Selecione um atendimento."}
        </h2>

        {primeiraVez ? (
          <>
            <p>
              Enquanto o canal não está ligado, você pode consultar a carteira em{" "}
              <LinkModo href="/clientes">Clientes</LinkModo>.
            </p>

            <div className="fluxo">
              <p className="fluxo-titulo">Como o atendimento vai funcionar</p>
              <ol>
                <li>
                  <StatusBadge tone="neutro">Recebida</StatusBadge>
                  <span>a mensagem chega e fica na caixa do escritório</span>
                </li>
                <li>
                  <StatusBadge tone="neutro">Visualizada</StatusBadge>
                  <span>alguém abriu — sem assumir a responsabilidade</span>
                </li>
                <li>
                  <StatusBadge tone="atencao">Assumida</StatusBadge>
                  <span>uma pessoa passa a ser a responsável</span>
                </li>
                <li>
                  <StatusBadge tone="ok">Respondida</StatusBadge>
                  <span>o cliente recebeu a resposta</span>
                </li>
              </ol>
              <p className="fluxo-nota">
                Abrir uma conversa não é assumi-la. A conversa pertence ao escritório;
                a responsabilidade é de quem assume.
              </p>
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}

/* ─── detalhe ────────────────────────────────────────────────────────── */

interface Detalhe extends Resumo {
  firstViewedAt: string | null;
  firstAssignedAt: string | null;
  resolvedAt: string | null;
  customerDetail: {
    id: string;
    displayName: string;
    documentDigits: string | null;
    email: string | null;
    active: boolean;
    syncedAt: string;
    phones: { raw: string; e164: string | null; status: string }[];
  } | null;
  views: {
    membershipId: string;
    name: string;
    department: string | null;
    firstViewedAt: string;
    viewCount: number;
  }[];
  events: {
    id: string;
    type: string;
    createdAt: string;
    actor: string | null;
    from: string | null;
    to: string | null;
    fromStatus: Status | null;
    toStatus: Status | null;
    metadata: unknown;
  }[];
}

interface Nota {
  id: string;
  content: string;
  createdAt: string;
  author: { membershipId: string; name: string; department: string | null };
}

interface Pessoa {
  membershipId: string;
  name: string;
  department: string | null;
}

export function ConversationDetail({
  id,
  membershipId,
  podeEnviar,
  entradaDev,
  pulso,
  onChange,
  onFechar,
}: {
  id: string;
  membershipId: string;
  podeEnviar: boolean;
  entradaDev: boolean;
  /** Sobe quando o realtime avisa que ESTA conversa mudou. */
  pulso: number;
  onChange: () => void;
  onFechar: () => void;
}) {
  const [dados, setDados] = useState<{ conversation: Detalhe; notes: Nota[] } | null>(null);
  const [erro, setErro] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [pessoas, setPessoas] = useState<Pessoa[]>([]);
  const [etiquetas, setEtiquetas] = useState<{ id: string; name: string; tone: string }[]>([]);

  const [tentativa, setTentativa] = useState(0);

  // Abrir registra visualização DO ATENDIMENTO — e só isso. Ler cada
  // mensagem é outro registro, e quem o faz é o observador de
  // visibilidade em messages.tsx.
  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const r = await fetch(`/api/conversations/${id}`, {
          headers: { accept: "application/json" },
        });
        if (!r.ok) throw new Error(String(r.status));
        const d = (await r.json()) as { conversation: Detalhe; notes: Nota[] };
        if (!vivo) return;
        setDados(d);
        setErro(false);
      } catch {
        if (vivo) setErro(true);
      }
    })();
    return () => {
      vivo = false;
    };
  }, [id, tentativa, pulso]);

  const carregar = useCallback(async () => {
    setTentativa((n) => n + 1);
  }, []);

  /* ── mensagens ───────────────────────────────────────────────────── */

  const [mensagens, setMensagens] = useState<Mensagem[]>([]);
  const [temMais, setTemMais] = useState(false);
  const [maisAntiga, setMaisAntiga] = useState<number | null>(null);
  const [carregandoAntigas, setCarregandoAntigas] = useState(false);

  // A página mais recente. Recarregada quando o realtime pulsa: a
  // resposta certa a "algo mudou" é perguntar, não deduzir.
  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const r = await fetch(`/api/conversations/${id}/messages`, {
          headers: { accept: "application/json" },
        });
        if (!r.ok) return;
        const d = (await r.json()) as {
          messages: Mensagem[];
          hasMore: boolean;
          oldestSequence: number | null;
        };
        if (!vivo) return;
        setMensagens(d.messages);
        setTemMais(d.hasMore);
        setMaisAntiga(d.oldestSequence);
      } catch {
        /* a tela continua com o que já tem; o aviso de conexão explica */
      }
    })();
    return () => {
      vivo = false;
    };
  }, [id, pulso]);

  const carregarAntigas = useCallback(() => {
    if (maisAntiga === null) return;
    setCarregandoAntigas(true);
    void (async () => {
      try {
        const r = await fetch(`/api/conversations/${id}/messages?antesDe=${maisAntiga}`, {
          headers: { accept: "application/json" },
        });
        if (!r.ok) return;
        const d = (await r.json()) as {
          messages: Mensagem[];
          hasMore: boolean;
          oldestSequence: number | null;
        };
        // Antigas na frente. O `sequence` garante que não há duplicata
        // nem buraco, mesmo se uma mensagem nova chegar no meio disso.
        setMensagens((atual) => [...d.messages, ...atual]);
        setTemMais(d.hasMore);
        if (d.oldestSequence !== null) setMaisAntiga(d.oldestSequence);
      } finally {
        setCarregandoAntigas(false);
      }
    })();
  }, [id, maisAntiga]);

  useEffect(() => {
    void (async () => {
      try {
        const [a, t] = await Promise.all([
          fetch("/api/assignees").then((r) => (r.ok ? r.json() : { people: [] })),
          fetch("/api/tags").then((r) => (r.ok ? r.json() : { tags: [] })),
        ]);
        setPessoas((a as { people: Pessoa[] }).people);
        setEtiquetas((t as { tags: { id: string; name: string; tone: string }[] }).tags);
      } catch {
        /* sem permissão para transferir ou etiquetar: a tela só não oferece */
      }
    })();
  }, []);

  const agir = useCallback(
    async (corpo: Record<string, unknown>) => {
      setOcupado(true);
      setAviso(null);
      try {
        const r = await fetch(`/api/conversations/${id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(corpo),
        });
        const d = (await r.json()) as { error?: { message: string } };
        if (!r.ok) {
          // 409 traz a mensagem específica: "assumido por Aline • Fiscal".
          setAviso(d.error?.message ?? "Não foi possível concluir.");
        }
        await carregar();
        onChange();
      } catch {
        setAviso("Falha de rede. Tente de novo.");
      } finally {
        setOcupado(false);
      }
    },
    [id, carregar, onChange],
  );

  if (erro) {
    return (
      <section className="coluna-conversa">
        <ErrorState title="Não foi possível carregar o atendimento." onRetry={() => void carregar()} />
      </section>
    );
  }
  if (!dados) {
    return (
      <section className="coluna-conversa">
        <LoadingState />
      </section>
    );
  }

  const c = dados.conversation;
  const souResponsavel = c.assignee?.membershipId === membershipId;

  return (
    <section className="coluna-conversa coluna-conversa-cheia" aria-label="Atendimento">
      <header className="atendimento-topo">
        {/* No celular a lista e a conversa não convivem: entra-se numa e
            volta-se para a outra. Sem o botão, o caminho de volta seria o
            do navegador — que sai da tela inteira. */}
        <button type="button" className="btn-voltar somente-estreito" onClick={onFechar}>
          <span aria-hidden="true">←</span>
          <span className="so-leitor">Voltar para a lista</span>
        </button>

        <Avatar name={c.customer?.displayName ?? "Contato"} size={38} square />
        <div className="atendimento-titulo">
          <h2>{c.customer?.displayName ?? "Contato não identificado"}</h2>
          <p>
            <StatusBadge tone={TOM_STATUS[c.status]}>{ROTULO_STATUS[c.status]}</StatusBadge>
            {c.assignee ? (
              <span className="atendimento-resp">
                {primeiro(c.assignee.name)}
                {c.department ? ` • ${c.department.name}` : ""}
              </span>
            ) : (
              <span className="atendimento-resp atendimento-sem-resp">
                Não atribuído{c.department ? ` · fila ${c.department.name}` : ""}
              </span>
            )}
          </p>
        </div>
      </header>

      {aviso ? (
        <p className="aviso" role="alert">
          {aviso}
        </p>
      ) : null}

      <div className="acoes">
        {!c.assignee ? (
          <button
            type="button"
            className="btn btn-acao"
            disabled={ocupado}
            onClick={() => void agir({ action: "assume" })}
          >
            Assumir atendimento
          </button>
        ) : null}

        {souResponsavel ? (
          <button
            type="button"
            className="btn"
            disabled={ocupado}
            onClick={() =>
              void agir({
                action: "transfer",
                expectedVersion: c.version,
                toMembershipId: null,
              })
            }
          >
            Devolver à fila
          </button>
        ) : null}

        {pessoas.length > 0 ? (
          <label className="acao-campo">
            <span className="so-leitor">Transferir para</span>
            <select
              value=""
              disabled={ocupado}
              onChange={(e) => {
                if (!e.target.value) return;
                void agir({
                  action: "transfer",
                  expectedVersion: c.version,
                  toMembershipId: e.target.value,
                });
              }}
            >
              <option value="">Transferir para…</option>
              {pessoas
                .filter((p) => p.membershipId !== c.assignee?.membershipId)
                .map((p) => (
                  <option key={p.membershipId} value={p.membershipId}>
                    {p.name}
                    {p.department ? ` • ${p.department}` : ""}
                  </option>
                ))}
            </select>
          </label>
        ) : null}

        <label className="acao-campo">
          <span className="so-leitor">Status</span>
          <select
            value={c.status}
            disabled={ocupado}
            onChange={(e) =>
              void agir({
                action: "status",
                expectedVersion: c.version,
                status: e.target.value,
              })
            }
          >
            {(Object.keys(ROTULO_STATUS) as Status[]).map((s) => (
              <option key={s} value={s}>
                {ROTULO_STATUS[s]}
              </option>
            ))}
          </select>
        </label>

        {entradaDev ? <BotaoEntradaDev id={id} /> : null}
      </div>

      {etiquetas.length > 0 ? (
        <div className="etiquetas-linha">
          {etiquetas.map((t) => {
            const aplicada = c.tags.some((x) => x.id === t.id);
            return (
              <button
                key={t.id}
                type="button"
                className="filtro"
                aria-pressed={aplicada}
                disabled={ocupado}
                onClick={() => {
                  void (async () => {
                    await fetch(`/api/conversations/${id}/tags`, {
                      method: "POST",
                      headers: { "content-type": "application/json" },
                      body: JSON.stringify({ tagId: t.id, apply: !aplicada }),
                    });
                    await carregar();
                    onChange();
                  })();
                }}
              >
                {t.name}
              </button>
            );
          })}
        </div>
      ) : null}

      <MessageThread
        conversationId={id}
        mensagens={mensagens}
        notas={dados.notes}
        hasMore={temMais}
        carregandoAntigas={carregandoAntigas}
        onCarregarAntigas={carregarAntigas}
        onEnviada={() => {
          void carregar();
          onChange();
        }}
        onNotaCriada={() => {
          void carregar();
          onChange();
        }}
        podeEnviar={podeEnviar}
      >
        {/* Recolhidos, no topo do histórico: ficam a um clique sem
            disputar espaço com a conversa, que é o assunto da tela. */}
        <div className="paineis-topo">
          <details className="painel-lateral">
            <summary>Quem visualizou o atendimento ({c.views.length})</summary>
            <p className="nota-alerta">Informação interna. O cliente não vê.</p>
            <ul className="lista-simples">
              {c.views.map((v) => (
                <li key={v.membershipId}>
                  <span>
                    {primeiro(v.name)}
                    {v.department ? ` • ${v.department}` : ""}
                  </span>
                  <span className="visto-hora">{formatSyncedAt(v.firstViewedAt)}</span>
                </li>
              ))}
            </ul>
          </details>

          <details className="painel-lateral">
            <summary>Histórico ({c.events.length})</summary>
            <ol className="linha-tempo">
              {c.events.map((e) => (
                <li key={e.id}>
                  <time>{formatSyncedAt(e.createdAt)}</time>
                  <span>{descreverEvento(e)}</span>
                </li>
              ))}
            </ol>
          </details>

          {c.customerDetail ? (
            <details className="painel-lateral">
              <summary>Dados do cliente</summary>
              <dl className="gaveta-dados">
                <dt>CNPJ/CPF</dt>
                <dd>{formatDocument(c.customerDetail.documentDigits)}</dd>
                <dt>Telefones</dt>
                <dd>
                  {c.customerDetail.phones.length === 0 ? "—" : null}
                  {c.customerDetail.phones.map((t) => (
                    <span key={t.raw} className="linha-dado">
                      {formatPhone(t.e164, t.raw)}
                    </span>
                  ))}
                </dd>
                <dt>E-mail</dt>
                <dd>{c.customerDetail.email ?? "—"}</dd>
                <dt>Sincronizado</dt>
                <dd>{formatSyncedAt(c.customerDetail.syncedAt)}</dd>
              </dl>
            </details>
          ) : null}
        </div>
      </MessageThread>
    </section>
  );
}

/**
 * O botão que finge ser o cliente.
 *
 * Só aparece quando a entrada de desenvolvimento está liberada NO
 * SERVIDOR — e o servidor confere de novo ao receber. Botão escondido não
 * é proteção; a proteção está lá, esta é a conveniência de testar
 * recebimento sem abrir o terminal.
 */
function BotaoEntradaDev({ id }: { id: string }) {
  const [enviando, setEnviando] = useState(false);

  return (
    <button
      type="button"
      className="btn btn-dev"
      disabled={enviando}
      title="Só existe em desenvolvimento"
      onClick={() => {
        setEnviando(true);
        void (async () => {
          try {
            await fetch("/api/dev/inbound", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                conversationId: id,
                content: "Bom dia! Preciso da guia deste mês, por favor.",
              }),
            });
          } finally {
            setEnviando(false);
          }
        })();
      }}
    >
      DEV: receber mensagem
    </button>
  );
}

/**
 * O rótulo do setor, tirado do `metadata` do evento.
 *
 * Está no metadata, e não numa consulta ao departamento, porque o evento
 * guarda o que era verdade NA HORA: renomear "Contábil" amanhã não deve
 * reescrever o histórico de ontem. Mesma razão da assinatura congelada
 * das mensagens.
 */
function rotuloDoSetor(e: Detalhe["events"][number]): string | null {
  const m = e.metadata;
  if (typeof m !== "object" || m === null) return null;
  const r = (m as { rotulo?: unknown }).rotulo;
  return typeof r === "string" && r.trim() ? r : null;
}

/** O histórico em português, não em nome de enum. */
function descreverEvento(e: Detalhe["events"][number]): string {
  const quem = e.actor ? primeiro(e.actor) : "O sistema";
  switch (e.type) {
    case "CREATED":
      return "Atendimento criado";
    case "VIEWED":
      return `${quem} visualizou`;
    case "ASSIGNED":
      return `${quem} assumiu`;
    case "UNASSIGNED":
      return `${quem} devolveu à fila`;
    case "TRANSFERRED":
      return `${quem} transferiu${e.from ? ` de ${primeiro(e.from)}` : ""}${
        e.to ? ` para ${primeiro(e.to)}` : ""
      }`;
    case "STATUS_CHANGED":
      return `${quem} mudou o status${
        e.fromStatus ? ` de ${ROTULO_STATUS[e.fromStatus]}` : ""
      }${e.toStatus ? ` para ${ROTULO_STATUS[e.toStatus]}` : ""}`;
    case "RESOLVED":
      return `${quem} resolveu`;
    case "REOPENED":
      return `${quem} reabriu`;
    case "NOTE_CREATED":
      return `${quem} registrou uma nota interna`;
    case "TAG_ADDED":
      return `${quem} aplicou uma etiqueta`;
    case "TAG_REMOVED":
      return `${quem} removeu uma etiqueta`;
    // Sem `quem`: o ator é NULO de propósito, porque foi o cliente. Dizer
    // "O sistema direcionou" daria o crédito a quem não escolheu.
    case "TRIAGED":
      return `Cliente direcionou o atendimento para ${rotuloDoSetor(e) ?? "um setor"}`;
    default:
      return e.type;
  }
}

/* ─── prévia da assinatura (usada em Configurações e no Início) ──────── */

export function OutgoingPreview({
  name,
  department,
}: {
  name: string;
  department: string | null;
}) {
  return (
    <div className="previa">
      <p className="previa-titulo">Sua assinatura nas mensagens</p>
      <div className="balao">
        <span className="balao-autor">
          <strong>{name}</strong>
          {department ? ` • ${department}` : ""}
        </span>
        <span className="balao-texto">Bom dia! Vou verificar isso para você.</span>
        <span className="balao-hora">
          10:32 <span aria-label="entregue">✓✓</span>
        </span>
      </div>
      <p className="previa-nota">
        {department
          ? "Preenchida automaticamente a partir da sua área principal."
          : "Defina uma área principal para ela aparecer ao lado do seu nome."}
      </p>
    </div>
  );
}
