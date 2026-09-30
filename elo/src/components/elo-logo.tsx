/**
 * Símbolo do Elo.
 *
 * Dois elos de corrente entrelaçados cujo vão forma um balão de conversa —
 * conforme FISCALE_ELO.md. Petróleo atrás, ciano na frente: a mesma dupla
 * do F do Fiscale, para os dois ícones se reconhecerem como família.
 *
 * As cores vêm das variáveis do tema, então o símbolo acompanha claro e
 * escuro sem precisar de dois arquivos.
 */
export function EloLogo({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="Fiscale Elo"
      fill="none"
    >
      <rect width="64" height="64" rx="15" fill="var(--petroleo-solido, #10444e)" />
      {/* elo de trás */}
      <rect
        x="12.5"
        y="19.5"
        width="27"
        height="25"
        rx="12.5"
        stroke="#7fd1de"
        strokeWidth="5"
      />
      {/* elo da frente — o vão entre os dois é o balão */}
      <rect
        x="24.5"
        y="19.5"
        width="27"
        height="25"
        rx="12.5"
        stroke="#e8eff3"
        strokeWidth="5"
      />
      {/* rabicho do balão */}
      <path d="M30 45.5 L30 52 L36.5 45.5 Z" fill="#e8eff3" />
    </svg>
  );
}

export function EloWordmark({ size = 28 }: { size?: number }) {
  return (
    <span className="marca">
      <EloLogo size={size} />
      <span className="marca-texto">
        <strong>Elo</strong>
        <small>Fiscale</small>
      </span>
    </span>
  );
}
