"use client";

/**
 * Clientes: lista, busca e o perfil em gaveta.
 *
 * Os dados vêm da projeção real (`GET /api/customers`) — a mesma que a
 * sincronização do Fiscale alimenta. Nada de lista de mentira.
 *
 * A decisão de UX que governa este arquivo: o cabeçalho fica LIMPO. CNPJ,
 * e-mail e telefone não ficam permanentemente na tela; aparecem quando
 * alguém clica no cliente. A conversa é a protagonista, e dado
 * complementar que fica sempre aberto vira ruído que ninguém mais lê.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { formatDocument, formatPhone, formatSyncedAt } from "@/lib/format";

import {
  Avatar,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
  StatusDot,
} from "./ui";

export interface CustomerPhone {
  raw: string;
  e164: string | null;
  status: string;
}

export interface Customer {
  id: string;
  source: string;
  externalId: string;
  displayName: string;
  documentDigits: string | null;
  email: string | null;
  active: boolean;
  syncedAt: string;
  missingSince: string | null;
  phones: CustomerPhone[];
}

type Situacao =
  | { estado: "carregando" }
  | { estado: "pronto"; clientes: Customer[] }
  | { estado: "erro" };

export function CustomerBrowser({ termoInicial = "" }: { termoInicial?: string }) {
  const [termo, setTermo] = useState(termoInicial);
  const [incluirInativos, setIncluirInativos] = useState(false);
  const [situacao, setSituacao] = useState<Situacao>({ estado: "carregando" });
  const [selecionado, setSelecionado] = useState<Customer | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const buscar = useCallback(
    async (q: string, inativos: boolean) => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      setSituacao({ estado: "carregando" });
      try {
        const params = new URLSearchParams();
        // Sem termo, `q` vazio traz a carteira toda pela busca por nome.
        params.set("q", q.trim().length >= 2 ? q.trim() : "");
        if (inativos) params.set("inactive", "1");

        const r = await fetch(`/api/customers?${params.toString()}`, {
          signal: ctrl.signal,
          headers: { accept: "application/json" },
        });
        if (!r.ok) throw new Error(String(r.status));
        const dados = (await r.json()) as { customers: Customer[] };
        setSituacao({ estado: "pronto", clientes: dados.customers });
      } catch (erro) {
        if ((erro as Error).name === "AbortError") return;
        setSituacao({ estado: "erro" });
      }
    },
    [],
  );

  // Espera a digitação parar. Sem isso, "padaria" dispara sete buscas.
  useEffect(() => {
    const t = setTimeout(() => void buscar(termo, incluirInativos), 250);
    return () => clearTimeout(t);
  }, [termo, incluirInativos, buscar]);

  return (
    <div className="clientes">
      <div className="clientes-controles">
        <div className="campo-busca">
          <span className="busca-icone" aria-hidden="true">
            ⌕
          </span>
          <input
            type="search"
            value={termo}
            onChange={(e) => setTermo(e.target.value)}
            placeholder="Nome, CNPJ/CPF ou telefone"
            aria-label="Procurar cliente"
          />
        </div>

        <label className="alternar">
          <input
            type="checkbox"
            checked={incluirInativos}
            onChange={(e) => setIncluirInativos(e.target.checked)}
          />
          Mostrar inativos
        </label>
      </div>

      {situacao.estado === "carregando" ? <LoadingState label="Carregando clientes…" /> : null}

      {situacao.estado === "erro" ? (
        <ErrorState
          title="Não foi possível carregar os clientes."
          description="Verifique a conexão e tente de novo."
          onRetry={() => void buscar(termo, incluirInativos)}
        />
      ) : null}

      {situacao.estado === "pronto" && situacao.clientes.length === 0 ? (
        <EmptyState
          title={termo.trim() ? "Nenhum cliente encontrado." : "Nenhum cliente sincronizado ainda."}
          description={
            termo.trim()
              ? "Tente outro nome, CNPJ ou telefone."
              : "Os clientes aparecem aqui depois que o Fiscale sincroniza o cadastro."
          }
        />
      ) : null}

      {situacao.estado === "pronto" && situacao.clientes.length > 0 ? (
        <ul className="lista-clientes">
          {situacao.clientes.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="cliente-item"
                onClick={() => setSelecionado(c)}
                aria-haspopup="dialog"
              >
                <Avatar name={c.displayName} size={38} square />
                <span className="cliente-texto">
                  <span className="cliente-nome">
                    {/* Ponto discreto em vez de etiqueta: em toda linha da
                        lista, etiqueta colorida vira papel de parede. */}
                    <StatusDot active={c.active} />
                    {c.displayName}
                  </span>
                  <span className="cliente-sub">
                    {formatDocument(c.documentDigits)}
                    {c.phones[0] ? ` · ${formatPhone(c.phones[0].e164, c.phones[0].raw)}` : ""}
                  </span>
                </span>
                {c.missingSince ? <StatusBadge tone="atencao">Ausente na origem</StatusBadge> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {selecionado ? (
        <CustomerProfileDrawer customer={selecionado} onClose={() => setSelecionado(null)} />
      ) : null}
    </div>
  );
}

/**
 * Perfil do contato, em gaveta.
 *
 * Mostra só o que existe na projeção. Regime, IE, IM, certificado e DAS
 * não aparecem porque não estão aqui — e não estão aqui de propósito
 * (minimização, MVP 1.2). Não há campo cinza esperando dado que nunca vem.
 */
export function CustomerProfileDrawer({
  customer,
  onClose,
}: {
  customer: Customer;
  onClose: () => void;
}) {
  const painel = useRef<HTMLDivElement>(null);

  // Esc fecha, e o foco entra na gaveta ao abrir. Sem isso, quem usa
  // teclado continua navegando a lista atrás do painel.
  useEffect(() => {
    const aoTeclar = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", aoTeclar);
    painel.current?.focus();
    return () => document.removeEventListener("keydown", aoTeclar);
  }, [onClose]);

  const telefonesUteis = customer.phones.filter((t) => t.e164);
  const telefonesDuvidosos = customer.phones.filter((t) => !t.e164);

  return (
    <>
      <button type="button" className="gaveta-fundo gaveta-fundo-visivel" aria-label="Fechar perfil" onClick={onClose} />
      <div
        className="gaveta"
        role="dialog"
        aria-modal="true"
        aria-label={`Perfil de ${customer.displayName}`}
        tabIndex={-1}
        ref={painel}
      >
        <header className="gaveta-topo">
          <Avatar name={customer.displayName} size={44} square />
          <div className="gaveta-titulo">
            <h2>{customer.displayName}</h2>
            <p>
              {customer.active ? (
                <StatusBadge tone="ok">Ativo</StatusBadge>
              ) : (
                <StatusBadge tone="neutro">Inativo</StatusBadge>
              )}
            </p>
          </div>
          <button type="button" className="btn btn-icone" onClick={onClose} aria-label="Fechar perfil">
            <span aria-hidden="true">✕</span>
          </button>
        </header>

        <dl className="gaveta-dados">
          <dt>CNPJ/CPF</dt>
          <dd>{formatDocument(customer.documentDigits)}</dd>

          <dt>Telefones</dt>
          <dd>
            {telefonesUteis.length === 0 && telefonesDuvidosos.length === 0 ? "—" : null}
            {telefonesUteis.map((t) => (
              <span key={t.raw} className="linha-dado">
                {formatPhone(t.e164, t.raw)}
              </span>
            ))}
            {/* Número que não normalizou aparece como veio, marcado. Some
                da tela seria pior: o dado existe no cadastro. */}
            {telefonesDuvidosos.map((t) => (
              <span key={t.raw} className="linha-dado linha-dado-fraca">
                {t.raw}
                <StatusBadge tone="atencao">
                  {t.status === "NO_AREA_CODE" ? "sem DDD" : "não reconhecido"}
                </StatusBadge>
              </span>
            ))}
          </dd>

          <dt>E-mail</dt>
          <dd>{customer.email ?? "—"}</dd>

          <dt>Origem</dt>
          <dd>
            {customer.source} · nº {customer.externalId}
          </dd>

          <dt>Última sincronização</dt>
          <dd>{formatSyncedAt(customer.syncedAt)}</dd>

          {customer.missingSince ? (
            <>
              <dt>Atenção</dt>
              <dd>
                Não veio na última sincronização completa (
                {formatSyncedAt(customer.missingSince)}). O registro foi mantido.
              </dd>
            </>
          ) : null}
        </dl>

        <p className="gaveta-nota">
          Dados fiscais — regime, IE, IM, apuração — permanecem no Fiscale e não
          são copiados para cá.
        </p>
      </div>
    </>
  );
}
