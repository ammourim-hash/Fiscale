/**
 * Sem conexão.
 *
 * ---------------------------------------------------------------------
 *  O que esta tela existe para NÃO fazer
 * ---------------------------------------------------------------------
 *  Não mostrar dado velho como se fosse atual. Um aplicativo que, offline,
 *  exibe a lista de atendimentos de ontem sem dizer nada é pior do que um
 *  que diz "estou sem conexão": num escritório de contabilidade, responder
 *  a partir de uma conversa desatualizada é responder errado ao cliente.
 *
 *  Por isso não há cache de conversa, de cliente nem de anexo — ver
 *  public/sw.js. O que existe offline é esta página, e ela é honesta sobre
 *  o que não sabe.
 */
import { EloLogo } from "@/components/elo-logo";
import { BotaoTentarDeNovo } from "@/components/offline";

export const metadata = {
  title: "ELO — sem conexão",
};

export default function Offline() {
  return (
    <main className="entrada">
      <div className="entrada-cartao">
        <EloLogo size={52} />
        <h1>ELO está sem conexão.</h1>

        <p className="entrada-texto">
          Não foi possível falar com o servidor. Enquanto isso, o Elo não mostra
          atendimentos: um dado de minutos atrás apresentado como atual faria
          alguém responder ao cliente pela informação errada.
        </p>

        <p className="entrada-nota">
          Nada se perde. As mensagens que chegaram continuam no servidor e
          aparecem assim que a conexão voltar.
        </p>

        <BotaoTentarDeNovo />
      </div>
    </main>
  );
}
