"use client";

/**
 * Quem navega no Elo passa por aqui.
 *
 * O modo embutido mora na URL (ver lib/embutido.ts). Uma navegação interna
 * que esquecesse o parâmetro traria a casca do Elo de volta DENTRO da casca
 * do FISCALE — duas navegações, duas buscas, dois seletores de tema. Em vez
 * de repetir a condição em cada link, o modo entra uma vez no contexto e
 * `LinkModo` cuida do resto.
 *
 * Fora do modo embutido o contexto vale `false`, `comModo()` devolve o
 * destino intocado e nenhuma URL ganha parâmetro nenhum.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { comModo } from "@/lib/embutido";

const Contexto = createContext(false);

export function ProvedorDeModo({
  embutido,
  children,
}: {
  embutido: boolean;
  children: ReactNode;
}) {
  return <Contexto.Provider value={embutido}>{children}</Contexto.Provider>;
}

/** O Elo está sendo desenhado dentro de outra casca? */
export function useEmbutido(): boolean {
  return useContext(Contexto);
}

/**
 * `next/link` com o modo preservado. É o único link que o Elo usa para
 * navegar entre as suas telas.
 */
export function LinkModo({
  href,
  children,
  ...resto
}: { href: string; children: ReactNode } & Omit<
  React.ComponentPropsWithoutRef<typeof Link>,
  "href" | "children"
>) {
  const embutido = useEmbutido();
  return (
    <Link href={comModo(href, embutido)} {...resto}>
      {children}
    </Link>
  );
}

/**
 * Para navegar por código (o clique numa notificação do sistema, por
 * exemplo), com o mesmo cuidado do `LinkModo`.
 */
export function useIrPara(): (destino: string) => void {
  const router = useRouter();
  const embutido = useEmbutido();
  return useCallback(
    (destino: string) => router.push(comModo(destino, embutido)),
    [router, embutido],
  );
}

/** Monta um destino com o modo, quando não dá para usar `LinkModo`. */
export function useCaminho(): (destino: string) => string {
  const embutido = useEmbutido();
  return useMemo(() => (destino: string) => comModo(destino, embutido), [embutido]);
}
