/**
 * Todas as páginas autenticadas passam por aqui.
 *
 * A sessão é resolvida no SERVIDOR antes de qualquer coisa renderizar. Sem
 * sessão, redireciona — não existe versão "vazia" da tela para quem não
 * entrou, e nenhum dado é buscado antes da checagem.
 *
 * Sem sessão, o destino pretendido é guardado antes de mandar para a
 * entrada. É o que faz clicar numa notificação às 22h, com a sessão
 * vencida, terminar no atendimento certo depois do login — e não na home.
 * Ver server/auth/destination.ts.
 */
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { pageSession } from "@/server/auth/page-session";
import { can } from "@/server/auth/context";
import { caminhoSeguro } from "@/server/auth/destination";
import { contarNaoLidos } from "@/server/conversations/queries";
import { CABECALHO_CAMINHO } from "@/middleware";
import { modoEmbutido } from "@/lib/embutido";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: ReactNode }) {
  const cabecalhos = await headers();
  const sessao = await pageSession();

  if (!sessao) {
    // O caminho pedido vem do middleware. Quando não vier — e um dia não
    // vem, porque é detalhe de framework —, o destino simplesmente não é
    // guardado e a pessoa cai na home. Degradar é aceitável; quebrar o
    // login por causa de uma conveniência, não.
    const pedido = caminhoSeguro(cabecalhos.get(CABECALHO_CAMINHO));
    redirect(pedido ? `/api/auth/destino?para=${encodeURIComponent(pedido)}` : "/entrar");
  }

  // O crachá inicial vem do servidor: sem ele a barra lateral piscaria "0"
  // e depois o número certo, a cada carregamento de página.
  const naoLidos = can(sessao.context, "conversations.read")
    ? await contarNaoLidos(sessao.context)
    : 0;

  return (
    <AppShell
      session={{
        name: sessao.context.name,
        email: sessao.context.email,
        role: sessao.context.role,
        primaryDepartment: sessao.primaryDepartment?.name ?? null,
        departments: sessao.departments,
      }}
      naoLidosIniciais={naoLidos}
      // `?embutido=1`: o Elo dentro da casca do FISCALE. Puro layout — a
      // sessão acima é a mesma, resolvida do mesmo jeito.
      embutido={modoEmbutido(cabecalhos.get(CABECALHO_CAMINHO))}
    >
      {children}
    </AppShell>
  );
}
