"use client";

/**
 * Admin > Equipe — as pessoas do escritório.
 *
 * =====================================================================
 *  A cobertura vem primeiro
 * =====================================================================
 *  No topo, antes da lista: quantas pessoas cada área teria de fato para
 *  receber um atendimento. Não é enfeite — é a pergunta que a tela existe
 *  para responder. Hoje dá para ter um setor com três pessoas e zero
 *  elegíveis (todas de férias), e a triagem manda a conversa para lá do
 *  mesmo jeito, sem avisar ninguém.
 *
 *  O número vem de `filaElegivel()`, a MESMA função do aviso. Por isso a
 *  linha mostra "elegíveis" e "vinculados" lado a lado quando eles
 *  divergem: a diferença é informação, não erro de conta.
 *
 * =====================================================================
 *  O menu não autoriza nada
 * =====================================================================
 *  Esta tela some para quem não administra, e isso é conveniência. A
 *  autorização mora no `requirePermission` das rotas: quem chamar
 *  /api/team direto leva 403 tendo visto o menu ou não.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  Avatar,
  DepartmentBadge,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
  StatusDot,
} from "@/components/ui";
import { rotuloPapel } from "@/lib/permission-labels";

/* ─── o que a API devolve ────────────────────────────────────────────── */

export interface Area {
  id: string;
  slug: string;
  name: string;
}

export interface Pessoa {
  membershipId: string;
  identityId: string;
  name: string;
  email: string;
  role: string;
  active: boolean;
  identityActive: boolean;
  availableForAssignment: boolean;
  visibleToCustomers: boolean;
  fiscaleUid: string | null;
  departments: Area[];
  primaryDepartmentId: string | null;
}

export interface Cobertura {
  departmentId: string;
  slug: string;
  name: string;
  elegiveis: number;
  gestores: number;
  vinculados: number;
}

interface Dados {
  people: Pessoa[];
  departments: Area[];
  coverage: Cobertura[];
}

const PAPEIS = ["OWNER", "ADMIN", "MANAGER", "AGENT", "VIEWER"] as const;

/** O combinado do escritório. Sinaliza; não impede. */
const ALVO_POR_SETOR = 3;

/* ─── tela ───────────────────────────────────────────────────────────── */

export function Equipe() {
  const [dados, setDados] = useState<Dados | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [formAberto, setFormAberto] = useState(false);
  const [editando, setEditando] = useState<Pessoa | null>(null);

  const [fPapel, setFPapel] = useState<string>("todos");
  const [fArea, setFArea] = useState<string>("todas");
  const [fSituacao, setFSituacao] = useState<"todas" | "ativos" | "inativos">("todas");

  const carregar = useCallback(async () => {
    setErro(null);
    try {
      const r = await fetch("/api/team", { cache: "no-store" });
      if (!r.ok) {
        setErro(
          r.status === 403
            ? "Você não tem acesso à administração da equipe."
            : "Não foi possível carregar a equipe.",
        );
        return;
      }
      setDados((await r.json()) as Dados);
    } catch {
      setErro("Não foi possível carregar a equipe.");
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await carregar();
    })();
  }, [carregar]);

  const visiveis = useMemo(() => {
    if (!dados) return [];
    return dados.people.filter((p) => {
      if (fPapel !== "todos" && p.role !== fPapel) return false;
      if (fArea !== "todas" && !p.departments.some((d) => d.id === fArea)) return false;
      if (fSituacao === "ativos" && !p.active) return false;
      if (fSituacao === "inativos" && p.active) return false;
      return true;
    });
  }, [dados, fPapel, fArea, fSituacao]);

  if (erro) return <ErrorState description={erro} onRetry={() => void carregar()} />;
  if (!dados) return <LoadingState label="Carregando a equipe…" />;

  return (
    <>
      <section aria-label="Cobertura dos setores" className="filtros">
        {dados.coverage.length === 0 ? (
          <EmptyState
            compact
            title="Nenhuma área cadastrada."
            description="Sem área, a triagem não tem para onde encaminhar."
          />
        ) : (
          dados.coverage.map((c) => {
            const falta = ALVO_POR_SETOR - c.elegiveis;
            const tom = c.elegiveis === 0 ? "erro" : falta > 0 ? "atencao" : "ok";
            return (
              <div key={c.departmentId} className="painel">
                <p className="painel-titulo">{c.name}</p>
                <p className="painel-texto">
                  <strong>
                    {c.elegiveis} de {ALVO_POR_SETOR}
                  </strong>{" "}
                  atendentes elegíveis
                  {c.vinculados !== c.elegiveis ? (
                    <span className="painel-nota"> · {c.vinculados} vinculados</span>
                  ) : null}
                </p>
                <p className="painel-texto">
                  {c.gestores} {c.gestores === 1 ? "gestor" : "gestores"}
                </p>
                <StatusBadge tone={tom}>
                  {c.elegiveis === 0
                    ? "ninguém será avisado"
                    : falta > 0
                      ? `faltam ${falta}`
                      : "cobertura completa"}
                </StatusBadge>
              </div>
            );
          })
        )}
      </section>

      <div className="clientes-controles">
        <div className="filtros" role="group" aria-label="Filtrar equipe">
          <label className="opcao">
            <span className="so-leitor">Papel</span>
            <select value={fPapel} onChange={(e) => setFPapel(e.target.value)} aria-label="Papel">
              <option value="todos">Todos os papéis</option>
              {PAPEIS.map((p) => (
                <option key={p} value={p}>
                  {rotuloPapel(p)}
                </option>
              ))}
            </select>
          </label>

          <label className="opcao">
            <span className="so-leitor">Área</span>
            <select value={fArea} onChange={(e) => setFArea(e.target.value)} aria-label="Área">
              <option value="todas">Todas as áreas</option>
              {dados.departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>

          <label className="opcao">
            <span className="so-leitor">Situação</span>
            <select
              value={fSituacao}
              onChange={(e) => setFSituacao(e.target.value as typeof fSituacao)}
              aria-label="Situação"
            >
              <option value="todas">Ativos e inativos</option>
              <option value="ativos">Só ativos</option>
              <option value="inativos">Só inativos</option>
            </select>
          </label>
        </div>

        <button
          type="button"
          className="btn btn-acao"
          onClick={() => {
            setEditando(null);
            setFormAberto(true);
          }}
        >
          Nova pessoa
        </button>
      </div>

      {visiveis.length === 0 ? (
        <EmptyState
          title="Ninguém por aqui."
          description="Nenhuma pessoa corresponde aos filtros escolhidos."
        />
      ) : (
        <ul className="lista-clientes">
          {visiveis.map((p) => (
            <li key={p.membershipId}>
              <button
                type="button"
                className="cliente-item"
                onClick={() => {
                  setEditando(p);
                  setFormAberto(true);
                }}
              >
                <Avatar name={p.name} />
                <span className="cliente-texto">
                  <span className="cliente-nome">
                    <StatusDot active={p.active} /> {p.name}
                  </span>
                  <span className="cliente-sub">{p.email}</span>
                  <span className="cliente-sub">
                    <StatusBadge tone="neutro">{rotuloPapel(p.role)}</StatusBadge>
                    {p.departments.map((d) => (
                      <DepartmentBadge
                        key={d.id}
                        name={d.name}
                        primary={d.id === p.primaryDepartmentId}
                      />
                    ))}
                    {p.availableForAssignment ? (
                      <StatusBadge tone="ok">assume atendimento</StatusBadge>
                    ) : (
                      <StatusBadge tone="atencao">não assume</StatusBadge>
                    )}
                    {p.visibleToCustomers ? (
                      <StatusBadge tone="acao">visível ao cliente</StatusBadge>
                    ) : null}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {formAberto ? (
        <FormularioPessoa
          pessoa={editando}
          areas={dados.departments}
          aoFechar={() => setFormAberto(false)}
          aoSalvar={() => {
            setFormAberto(false);
            void carregar();
          }}
        />
      ) : null}
    </>
  );
}

/* ─── formulário ─────────────────────────────────────────────────────── */

function FormularioPessoa({
  pessoa,
  areas,
  aoFechar,
  aoSalvar,
}: {
  pessoa: Pessoa | null;
  areas: Area[];
  aoFechar: () => void;
  aoSalvar: () => void;
}) {
  const novo = pessoa === null;

  const [name, setName] = useState(pessoa?.name ?? "");
  const [email, setEmail] = useState(pessoa?.email ?? "");
  const [fiscaleUid, setFiscaleUid] = useState(pessoa?.fiscaleUid ?? "");
  const [role, setRole] = useState(pessoa?.role ?? "AGENT");
  const [active, setActive] = useState(pessoa?.active ?? true);
  const [assume, setAssume] = useState(pessoa?.availableForAssignment ?? true);
  // Padrão FALSE, como o modelo manda: ninguém é exposto ao cliente por
  // ter sido cadastrado.
  const [visivel, setVisivel] = useState(pessoa?.visibleToCustomers ?? false);
  const [selecionadas, setSelecionadas] = useState<string[]>(
    pessoa?.departments.map((d) => d.id) ?? [],
  );
  const [principal, setPrincipal] = useState<string>(pessoa?.primaryDepartmentId ?? "");

  const [erro, setErro] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);

  function alternarArea(id: string) {
    setSelecionadas((atual) => {
      const proxima = atual.includes(id) ? atual.filter((x) => x !== id) : [...atual, id];
      // Tirar a área principal da seleção tira o principal junto: assinar
      // por uma área a que não se pertence não é estado válido.
      if (!proxima.includes(principal)) setPrincipal("");
      return proxima;
    });
  }

  async function salvar() {
    setErro(null);

    if (principal && !selecionadas.includes(principal)) {
      setErro("A área principal precisa estar entre as áreas selecionadas.");
      return;
    }

    setSalvando(true);
    try {
      if (novo) {
        const r = await fetch("/api/team", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            name,
            email,
            fiscaleUid,
            role,
            active,
            availableForAssignment: assume,
            visibleToCustomers: visivel,
            departmentIds: selecionadas,
            primaryDepartmentId: principal || null,
          }),
        });
        if (!r.ok) {
          setErro(await mensagemDoErro(r));
          return;
        }
      } else {
        const r = await fetch(`/api/team/${pessoa.membershipId}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            name,
            role,
            active,
            availableForAssignment: assume,
            visibleToCustomers: visivel,
          }),
        });
        if (!r.ok) {
          setErro(await mensagemDoErro(r));
          return;
        }

        const r2 = await fetch(`/api/team/${pessoa.membershipId}/departments`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            departmentIds: selecionadas,
            primaryDepartmentId: principal || null,
          }),
        });
        if (!r2.ok) {
          setErro(await mensagemDoErro(r2));
          return;
        }
      }
      aoSalvar();
    } catch {
      setErro("Não foi possível salvar.");
    } finally {
      setSalvando(false);
    }
  }

  return (
    <>
      <div className="gaveta-fundo gaveta-fundo-visivel" onClick={aoFechar} aria-hidden="true" />
      <aside className="gaveta" role="dialog" aria-label={novo ? "Nova pessoa" : "Editar pessoa"}>
        <header className="gaveta-topo">
          <h2 className="gaveta-titulo">{novo ? "Nova pessoa" : pessoa.name}</h2>
          <button type="button" className="btn btn-icone" onClick={aoFechar} aria-label="Fechar">
            ×
          </button>
        </header>

        <div className="gaveta-dados">
          <label className="opcao">
            Nome
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>

          <label className="opcao">
            E-mail
            <input
              type="email"
              value={email}
              disabled={!novo}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>

          <label className="opcao">
            Login do FISCALE
            <input
              value={fiscaleUid}
              disabled={!novo}
              onChange={(e) => setFiscaleUid(e.target.value)}
            />
            <span className="opcao-ajuda">
              Login do FISCALE: informe o mesmo login que a pessoa usa para entrar no FISCALE, não o
              e-mail.
            </span>
          </label>

          <label className="opcao">
            Papel
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {PAPEIS.map((p) => (
                <option key={p} value={p}>
                  {rotuloPapel(p)}
                </option>
              ))}
            </select>
          </label>

          <label className="opcao">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
            Ativo
          </label>

          <label className="opcao">
            <input type="checkbox" checked={assume} onChange={(e) => setAssume(e.target.checked)} />
            Disponível para assumir atendimento
          </label>

          <label className="opcao">
            <input
              type="checkbox"
              checked={visivel}
              onChange={(e) => setVisivel(e.target.checked)}
            />
            Visível ao cliente
          </label>

          <fieldset className="lista-opcoes">
            <legend>Áreas</legend>
            {areas.length === 0 ? (
              <p className="opcao-ajuda">Nenhuma área cadastrada.</p>
            ) : (
              areas.map((a) => (
                <label key={a.id} className="opcao">
                  <input
                    type="checkbox"
                    checked={selecionadas.includes(a.id)}
                    onChange={() => alternarArea(a.id)}
                  />
                  {a.name}
                </label>
              ))
            )}
          </fieldset>

          <label className="opcao">
            Área principal
            <select value={principal} onChange={(e) => setPrincipal(e.target.value)}>
              <option value="">Nenhuma</option>
              {areas
                .filter((a) => selecionadas.includes(a.id))
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
            <span className="opcao-ajuda">
              É ela que assina as mensagens: &quot;{name || "Nome"} • Área&quot;.
            </span>
          </label>

          {erro ? (
            <p className="painel-nota" role="alert">
              {erro}
            </p>
          ) : null}

          <div className="rodape-acoes">
            <button type="button" className="btn" onClick={aoFechar}>
              Cancelar
            </button>
            <button
              type="button"
              className="btn btn-acao"
              disabled={salvando}
              onClick={() => void salvar()}
            >
              {salvando ? "Salvando…" : "Salvar"}
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

async function mensagemDoErro(r: Response): Promise<string> {
  if (r.status === 403) return "Você não tem acesso para esta operação.";
  const corpo = (await r.json().catch(() => null)) as { error?: { message?: string } } | null;
  return corpo?.error?.message ?? "Não foi possível salvar.";
}
