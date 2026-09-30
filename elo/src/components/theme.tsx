"use client";

/**
 * Tema: Claro / Escuro / Sistema.
 *
 * Não depende do servidor. A preferência mora no localStorage e é aplicada
 * antes da primeira pintura pelo script de `layout.tsx` — sem isso, quem
 * usa o modo escuro leva um flash branco a cada carregamento.
 *
 * O localStorage e o `prefers-color-scheme` são estado EXTERNO ao React, e
 * é por isso que a leitura passa por `useSyncExternalStore` em vez de
 * `useState` + `useEffect`: com efeito, o primeiro render mostraria o tema
 * errado e o segundo corrigiria, o que é exatamente o flash que o script
 * do `<head>` existe para evitar.
 *
 * "Sistema" não é "claro": é seguir o SO, inclusive quando ele muda no meio
 * do expediente — daí a inscrição no matchMedia.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";

export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "elo:tema";

/** Roda antes da hidratação. Precisa ser pequeno e não lançar nunca. */
export const THEME_SCRIPT = `(function(){try{
var t=localStorage.getItem('${THEME_STORAGE_KEY}')||'system';
var d=t==='dark'||(t==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);
document.documentElement.dataset.theme=d?'dark':'light';
}catch(e){document.documentElement.dataset.theme='light';}})();`;

/* ─── a fonte externa ────────────────────────────────────────────────── */

const ouvintes = new Set<() => void>();

function avisar(): void {
  for (const f of ouvintes) f();
}

function consultaMidia(): MediaQueryList | null {
  if (typeof window === "undefined" || !window.matchMedia) return null;
  return window.matchMedia("(prefers-color-scheme: dark)");
}

function inscrever(aoMudar: () => void): () => void {
  ouvintes.add(aoMudar);
  const mq = consultaMidia();
  // `storage` cobre a mesma preferência mudando em OUTRA aba.
  window.addEventListener("storage", aoMudar);
  mq?.addEventListener("change", aoMudar);
  return () => {
    ouvintes.delete(aoMudar);
    window.removeEventListener("storage", aoMudar);
    mq?.removeEventListener("change", aoMudar);
  };
}

function preferenciaGuardada(): Theme {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* navegador com storage bloqueado: segue com "system" */
  }
  return "system";
}

/**
 * `getSnapshot` precisa devolver um valor estável entre renders — objeto
 * novo a cada chamada faria o React entrar em laço. Daí a string única
 * "escolha:resolvido", desmontada depois.
 */
function instantaneo(): string {
  const escolha = preferenciaGuardada();
  const escuro = escolha === "dark" || (escolha === "system" && (consultaMidia()?.matches ?? false));
  return `${escolha}:${escuro ? "dark" : "light"}`;
}

/** No servidor não há preferência. O script do `<head>` corrige antes de pintar. */
function instantaneoServidor(): string {
  return "system:light";
}

/* ─── contexto ───────────────────────────────────────────────────────── */

interface ThemeContexto {
  theme: Theme;
  resolved: ResolvedTheme;
  setTheme: (t: Theme) => void;
}

const Ctx = createContext<ThemeContexto | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const bruto = useSyncExternalStore(inscrever, instantaneo, instantaneoServidor);
  const [escolha, resolvido] = bruto.split(":") as [Theme, ResolvedTheme];

  // Sincronizar o DOM com o estado é justamente o que um efeito deve
  // fazer: escrever no sistema externo.
  useEffect(() => {
    document.documentElement.dataset.theme = resolvido;
  }, [resolvido]);

  const setTheme = useCallback((t: Theme) => {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, t);
    } catch {
      /* sem storage a escolha não persiste — melhor que quebrar a tela */
    }
    avisar();
  }, []);

  const valor = useMemo(
    () => ({ theme: escolha, resolved: resolvido, setTheme }),
    [escolha, resolvido, setTheme],
  );

  return <Ctx.Provider value={valor}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeContexto {
  const c = useContext(Ctx);
  if (!c) throw new Error("useTheme precisa estar dentro de <ThemeProvider>");
  return c;
}

/* ─── seletor ────────────────────────────────────────────────────────── */

const OPCOES: { valor: Theme; rotulo: string; icone: string }[] = [
  { valor: "light", rotulo: "Claro", icone: "☀" },
  { valor: "dark", rotulo: "Escuro", icone: "☾" },
  { valor: "system", rotulo: "Sistema", icone: "◐" },
];

/**
 * Três botões num grupo, e não um interruptor de dois estados: "Sistema"
 * é uma escolha de verdade, e num toggle ela ficaria invisível.
 */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="tema" role="group" aria-label="Aparência">
      {OPCOES.map((o) => (
        <button
          key={o.valor}
          type="button"
          className="tema-opcao"
          aria-pressed={theme === o.valor}
          onClick={() => setTheme(o.valor)}
          title={o.rotulo}
        >
          <span aria-hidden="true">{o.icone}</span>
          <span className="tema-rotulo">{o.rotulo}</span>
        </button>
      ))}
    </div>
  );
}
