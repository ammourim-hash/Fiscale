/**
 * Peças pequenas e reaproveitáveis.
 *
 * A regra que atravessa o arquivo: nada de `<div onClick>`. Quem age é
 * `<button>`, quem navega é `<a>`. Div clicável não recebe foco, não
 * responde ao Enter e não é anunciada como controle — a interface parece
 * funcionar e não funciona para quem usa teclado ou leitor de tela.
 */
import type { ReactNode } from "react";

/* ─── avatar ─────────────────────────────────────────────────────────── */

function iniciais(nome: string): string {
  const partes = nome.trim().split(/\s+/).filter(Boolean);
  if (partes.length === 0) return "?";
  if (partes.length === 1) return partes[0]!.slice(0, 2).toUpperCase();
  return (partes[0]![0]! + partes[partes.length - 1]![0]!).toUpperCase();
}

/** Matiz derivada do nome: a mesma pessoa tem sempre a mesma cor. */
function matiz(semente: string): number {
  let h = 0;
  for (let i = 0; i < semente.length; i++) h = (h * 31 + semente.charCodeAt(i)) % 360;
  return h;
}

export function Avatar({
  name,
  size = 36,
  square = false,
}: {
  name: string;
  size?: number;
  square?: boolean;
}) {
  const h = matiz(name);
  return (
    <span
      className="avatar"
      aria-hidden="true"
      style={{
        width: size,
        height: size,
        borderRadius: square ? "var(--raio-p)" : "50%",
        fontSize: Math.round(size * 0.36),
        background: `oklch(0.62 0.09 ${h})`,
      }}
    >
      {iniciais(name)}
    </span>
  );
}

/* ─── identidade do funcionário ──────────────────────────────────────── */

/**
 * "Aline • Fiscal" — a assinatura que o cliente vai ver em cada mensagem.
 *
 * Sem departamento principal escolhido, mostra só o nome. Deduzir "o
 * primeiro departamento da lista" daria uma assinatura externa que muda
 * sozinha quando alguém reordena um cadastro.
 */
export function EmployeeBadge({
  name,
  department,
  size = "md",
}: {
  name: string;
  department?: string | null;
  size?: "sm" | "md";
}) {
  return (
    <span className={`assinatura assinatura-${size}`}>
      <strong>{name}</strong>
      {department ? (
        <>
          <span className="assinatura-ponto" aria-hidden="true">
            •
          </span>
          <span className="assinatura-area">{department}</span>
        </>
      ) : null}
    </span>
  );
}

export function DepartmentBadge({ name, primary = false }: { name: string; primary?: boolean }) {
  return (
    <span className={`etiqueta ${primary ? "etiqueta-acao" : ""}`}>
      {name}
      {primary ? <span className="etiqueta-nota"> · principal</span> : null}
    </span>
  );
}

export type StatusTone = "ok" | "atencao" | "erro" | "neutro" | "acao";

export function StatusBadge({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  return <span className={`etiqueta etiqueta-${tone}`}>{children}</span>;
}

/* ─── estados ────────────────────────────────────────────────────────── */

export function LoadingState({ label = "Carregando…" }: { label?: string }) {
  return (
    <div className="estado" role="status" aria-live="polite">
      <span className="girando" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  compact = false,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  /** Menos respiro. Para vazios que convivem com outro conteúdo na tela. */
  compact?: boolean;
}) {
  return (
    <div className={`estado ${compact ? "estado-compacto" : ""}`}>
      <p className="estado-titulo">{title}</p>
      {description ? <p className="estado-desc">{description}</p> : null}
      {action}
    </div>
  );
}

/**
 * Ativo/inativo sem gritar.
 *
 * Etiqueta colorida em toda linha da lista viraria papel de parede e
 * pararia de ser lida. Um ponto pequeno informa e sai da frente — com
 * `title` e texto para leitor de tela, porque cor sozinha não é informação
 * para quem não a enxerga.
 */
export function StatusDot({ active }: { active: boolean }) {
  const texto = active ? "Ativo" : "Inativo";
  return (
    <span className={`ponto-status ${active ? "ponto-ativo" : "ponto-inativo"}`} title={texto}>
      <span className="so-leitor">{texto}</span>
    </span>
  );
}

export function ErrorState({
  title = "Não foi possível carregar.",
  description,
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="estado" role="alert">
      <p className="estado-titulo estado-erro">{title}</p>
      {description ? <p className="estado-desc">{description}</p> : null}
      {onRetry ? (
        <button type="button" className="btn btn-acao" onClick={onRetry}>
          Tentar novamente
        </button>
      ) : null}
    </div>
  );
}
