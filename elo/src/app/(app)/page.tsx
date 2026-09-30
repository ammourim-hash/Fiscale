/**
 * Início.
 *
 * Poucos números, todos reais. "0 novos" é a verdade enquanto o canal não
 * existe; inventar 12 atendimentos deixaria a tela bonita e o sistema
 * mentiroso — e no dia em que o número virasse real ninguém acreditaria
 * nele.
 *
 * A assinatura "Nome • Área" aparece como UMA LINHA no rodapé, não como
 * painel. Ela é uma configuração que a pessoa confere de vez em quando,
 * não algo que precise ocupar meia tela todo dia. O exemplo completo mora
 * em Configurações.
 */
import { LinkModo } from "@/components/embutido";
import { redirect } from "next/navigation";

import { EmptyState } from "@/components/ui";
import { primeiroNome, saudacao, formatSyncedAt } from "@/lib/format";
import { pageSession } from "@/server/auth/page-session";
import { projectionStatus } from "@/server/customers/lookup";

export const dynamic = "force-dynamic";

export default async function Inicio() {
  const sessao = await pageSession();
  if (!sessao) redirect("/entrar");

  const projecao = await projectionStatus(sessao.context.tenantId);
  const nome = primeiroNome(sessao.context.name);
  const area = sessao.primaryDepartment?.name ?? null;

  return (
    <div className="pagina">
      <header className="pagina-topo">
        <h1>
          {saudacao()}, {nome}.
        </h1>
      </header>

      <div className="cartoes">
        <div className="cartao">
          <span className="cartao-rotulo">Atendimentos novos</span>
          <span className="cartao-numero">0</span>
          <span className="cartao-nota">o canal ainda não está ligado</span>
        </div>
        <div className="cartao">
          <span className="cartao-rotulo">Aguardando resposta</span>
          <span className="cartao-numero">0</span>
          <span className="cartao-nota">o canal ainda não está ligado</span>
        </div>
        <LinkModo href="/clientes" className="cartao cartao-link">
          <span className="cartao-rotulo">Clientes</span>
          <span className="cartao-numero">{projecao.active}</span>
          <span className="cartao-nota">
            {projecao.total === projecao.active
              ? "ativos na carteira"
              : `ativos · ${projecao.total - projecao.active} inativos`}
          </span>
        </LinkModo>
      </div>

      <section className="secao secao-junta">
        <h2 className="secao-titulo">Seus atendimentos</h2>
        <div className="painel">
          <EmptyState
            compact
            title="Seus atendimentos aparecerão aqui."
            description="Quando o canal do WhatsApp for ligado, as conversas sob sua responsabilidade ficam nesta lista."
          />
        </div>
      </section>

      <footer className="rodape-inicio">
        <span>
          Última sincronização com o FISCALE:{" "}
          <strong>{formatSyncedAt(projecao.lastSyncAt)}</strong>
          {projecao.missing > 0 ? ` · ${projecao.missing} ausente(s) na última carga` : ""}
        </span>
        <span className="rodape-sep" aria-hidden="true">
          ·
        </span>
        <span>
          {area ? (
            <>
              Suas mensagens saem assinadas como{" "}
              <strong>
                {nome} • {area}
              </strong>
            </>
          ) : (
            <>Suas mensagens saem assinadas apenas com o seu nome</>
          )}{" "}
          <LinkModo href="/configuracoes">alterar</LinkModo>
        </span>
      </footer>
    </div>
  );
}
