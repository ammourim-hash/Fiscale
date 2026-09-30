/**
 * Sem sessão.
 *
 * O Elo não tem tela de login com senha, e isso é decisão de arquitetura,
 * não falta: quem autentica é o Fiscale. Esta página explica o caminho em
 * vez de mostrar um formulário que não existe.
 */
import { EloLogo } from "@/components/elo-logo";
import { pageSession } from "@/server/auth/page-session";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function Entrar() {
  // Quem já tem sessão não fica preso nesta tela.
  if (await pageSession()) redirect("/");

  return (
    <main className="entrada">
      <div className="entrada-cartao">
        <EloLogo size={52} />
        <h1>Fiscale Elo</h1>
        <p className="entrada-sub">Central de atendimento do escritório</p>

        <p className="entrada-texto">
          A entrada é pelo Fiscale. Abra o Fiscale, faça login como sempre e
          clique em <strong>🔗 Elo</strong> na barra do topo.
        </p>

        <p className="entrada-nota">
          Não há senha separada aqui: sua identidade vem do Fiscale, e o Elo
          cria uma sessão própria a partir dela.
        </p>
      </div>
    </main>
  );
}
