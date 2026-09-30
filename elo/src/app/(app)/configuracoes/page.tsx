/**
 * Configurações — seis seções, nessa ordem:
 * Meu perfil · Área principal · Outras áreas · Notificações · Aparência ·
 * Sessão.
 *
 * Notificações entra em quarto lugar, e não no fim: é a seção que alguém
 * abre com uma pergunta na cabeça ("por que não recebo aviso?"), enquanto
 * as outras se visitam uma vez e nunca mais.
 *
 * Não é painel administrativo. Gerir pessoas, papéis e áreas do escritório
 * é outra tela, de outra fase.
 *
 * A lista de permissões técnicas saiu da tela de quem atende: saber que se
 * possui `departments.read` não muda nada do dia dessa pessoa. Para quem
 * administra, ela aparece — com nome de gente, e recolhida.
 */
import { redirect } from "next/navigation";

import { ConfiguracoesNotificacoes } from "@/components/configuracoes-notificacoes";
import { ThemeToggle } from "@/components/theme";
import { Avatar, EmptyState, StatusBadge } from "@/components/ui";
import { OutgoingPreview } from "@/components/conversations";
import { ehAdministrador, rotuloPapel, rotuloPermissao } from "@/lib/permission-labels";
import { primeiroNome } from "@/lib/format";
import { pageSession } from "@/server/auth/page-session";

export const dynamic = "force-dynamic";

export default async function Configuracoes() {
  const sessao = await pageSession();
  if (!sessao) redirect("/entrar");

  const { context: eu, primaryDepartment, departments, permissions } = sessao;
  const outras = departments.filter((d) => d.id !== primaryDepartment?.id);
  const admin = ehAdministrador(eu.role);

  return (
    <div className="pagina pagina-estreita">
      <header className="pagina-topo">
        <h1>Configurações</h1>
      </header>

      {/* 1 ─ Meu perfil */}
      <section className="secao">
        <h2 className="secao-titulo">Meu perfil</h2>
        <div className="painel">
          <div className="perfil-cabecalho">
            <Avatar name={eu.name} size={52} />
            <div>
              <p className="perfil-nome">{eu.name}</p>
              <p className="perfil-email">{eu.email}</p>
              <p className="perfil-etiquetas">
                <StatusBadge tone="neutro">{rotuloPapel(eu.role)}</StatusBadge>
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* 2 ─ Área principal */}
      <section className="secao">
        <h2 className="secao-titulo">Área principal</h2>
        <div className="painel">
          {primaryDepartment ? (
            <p className="painel-texto">
              Você atende como <strong>{primaryDepartment.name}</strong>. É essa área
              que acompanha o seu nome nas mensagens.
            </p>
          ) : (
            <p className="painel-texto">
              Você ainda não tem uma área principal definida, então as mensagens saem
              apenas com o seu nome. Quem administra o escritório faz essa escolha — o
              sistema não decide sozinho, porque a assinatura que o cliente lê não pode
              mudar quando alguém reordena um cadastro.
            </p>
          )}
          <OutgoingPreview
            name={primeiroNome(eu.name)}
            department={primaryDepartment?.name ?? null}
          />
        </div>
      </section>

      {/* 3 ─ Outras áreas */}
      <section className="secao">
        <h2 className="secao-titulo">Outras áreas</h2>
        {outras.length === 0 ? (
          <div className="painel">
            <EmptyState
              compact
              title="Você participa apenas da sua área principal."
              description="Participar de mais de uma área é possível; quem administra o escritório faz o vínculo."
            />
          </div>
        ) : (
          <ul className="lista-simples">
            {outras.map((d) => (
              <li key={d.id}>
                <span>{d.name}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 4 ─ Notificações */}
      <section className="secao">
        <h2 className="secao-titulo">Notificações</h2>
        <ConfiguracoesNotificacoes />
      </section>

      {/* 5 ─ Aparência */}
      <section className="secao">
        <h2 className="secao-titulo">Aparência</h2>
        <div className="painel">
          <p className="painel-texto">
            A escolha fica guardada neste navegador e vale imediatamente — não depende
            do servidor.
          </p>
          <ThemeToggle />
        </div>
      </section>

      {/* 6 ─ Sessão */}
      <section className="secao">
        <h2 className="secao-titulo">Sessão</h2>
        <form action="/api/auth/logout" method="post" className="painel painel-linha">
          <p className="painel-texto">Sair encerra esta sessão no servidor.</p>
          <button type="submit" className="btn btn-perigo">
            Sair do Elo
          </button>
        </form>
      </section>

      {admin ? (
        <section className="secao">
          <details className="recolhivel">
            <summary>O que o seu acesso permite</summary>
            <ul className="lista-simples lista-permissoes">
              {permissions.map((p) => (
                <li key={p}>
                  <span>{rotuloPermissao(p)}</span>
                </li>
              ))}
            </ul>
          </details>
        </section>
      ) : null}
    </div>
  );
}
