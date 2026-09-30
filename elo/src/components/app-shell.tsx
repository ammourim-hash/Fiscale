"use client";

/**
 * A casca: navegação à esquerda, conteúdo à direita.
 *
 * No desktop as colunas convivem. Abaixo de 900px a navegação vira uma
 * gaveta — três colunas espremidas num celular não é responsividade, é
 * desistir de ler.
 *
 * A partir do MVP 1.7 a casca também carrega três coisas de notificação,
 * e todas moram aqui porque valem para o Elo inteiro, não para uma tela:
 * o crachá de não lidos, o som de mensagem nova e o aviso de versão nova.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { ehAdministrador } from "@/lib/permission-labels";

import { EloWordmark } from "./elo-logo";
import { ProvedorDeModo, useIrPara } from "./embutido";
import { aplicarCrachaDoApp, tocarAvisoDeMensagem, useServiceWorker } from "./notificacoes";
import { useConversaAberta, useRealtime, type EventoRealtime } from "./realtime";
import { ThemeToggle } from "./theme";
import { Avatar, EmployeeBadge } from "./ui";

export interface SessionInfo {
  name: string;
  email: string;
  role: string;
  primaryDepartment: string | null;
  departments: { id: string; slug: string; name: string }[];
}

interface ItemNav {
  href: string;
  rotulo: string;
  icone: string;
  /** Só "Atendimentos" mostra contador. */
  cracha?: boolean;
}

/**
 * Só o que existe.
 *
 * Nada de item apagado com "em breve": a navegação de quem trabalha não é
 * lugar de mostrar roadmap. Não lidas, Aguardando e Arquivadas voltam
 * quando forem telas de verdade — por ora vivem como filtros dentro de
 * Atendimentos, que é onde fazem sentido.
 */
const NAVEGACAO: ItemNav[] = [
  { href: "/", rotulo: "Início", icone: "◆" },
  { href: "/atendimentos", rotulo: "Atendimentos", icone: "❑", cracha: true },
  { href: "/clientes", rotulo: "Clientes", icone: "❖" },
  { href: "/configuracoes", rotulo: "Configurações", icone: "⚙" },
];

/**
 * Administração. Fica FORA da lista de cima porque não é para todo mundo.
 *
 * Esconder o item é conveniência, nunca autorização: quem chamar
 * `/api/team` direto leva 403 pelo `requirePermission`, tendo visto o
 * menu ou não. O item some para não oferecer a quem não pode usar.
 */
const NAVEGACAO_ADMIN: ItemNav[] = [{ href: "/admin/equipe", rotulo: "Equipe", icone: "⛭" }];

export function AppShell({
  session,
  naoLidosIniciais = 0,
  embutido = false,
  children,
}: {
  session: SessionInfo;
  /** Vem do servidor: sem ele o crachá piscaria 0 a cada navegação. */
  naoLidosIniciais?: number;
  /**
   * O Elo desenhado dentro da casca do FISCALE: some a navegação DELE,
   * some a barra de cima, e o conteúdo ocupa a largura inteira. Só isso —
   * nenhum comportamento muda, e os efeitos abaixo (service worker,
   * crachá, aviso de versão) continuam ligados, porque quem está
   * embutido trabalha igual.
   */
  embutido?: boolean;
  children: ReactNode;
}) {
  const caminho = usePathname();
  const irPara = useIrPara();
  const [gavetaAberta, setGavetaAberta] = useState(false);
  const navId = useId();

  // Administração entra no fim, para quem administra. Ver NAVEGACAO_ADMIN.
  const itens = ehAdministrador(session.role)
    ? [...NAVEGACAO, ...NAVEGACAO_ADMIN]
    : NAVEGACAO;

  const { atualizacao, aplicarAtualizacao } = useServiceWorker();
  const naoLidos = useCrachaNaoLidos(naoLidosIniciais);

  // Clique numa notificação do sistema com o Elo já aberto: o service
  // worker foca esta janela e manda a mensagem. Navegar aqui, e não lá,
  // porque quem sabe rotear dentro do app é o router do Next.
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    const aoReceber = (e: MessageEvent) => {
      const dado = e.data as { tipo?: string; conversationId?: string } | null;
      if (dado?.tipo !== "ABRIR_ATENDIMENTO") return;
      irPara(
        dado.conversationId ? `/atendimentos?abrir=${dado.conversationId}` : "/atendimentos",
      );
    };
    navigator.serviceWorker.addEventListener("message", aoReceber);
    return () => navigator.serviceWorker.removeEventListener("message", aoReceber);
  }, [irPara]);

  // Navegar fecha a gaveta: no celular, ficar com o menu por cima do
  // conteúdo recém-aberto é o erro clássico. Fecha no CLIQUE, e não como
  // reação à mudança de rota — reagir ao caminho seria pedir um render a
  // mais para desfazer algo que o próprio clique já sabia.
  const fechar = () => setGavetaAberta(false);

  useEffect(() => {
    if (!gavetaAberta) return;
    const aoTeclar = (e: KeyboardEvent) => {
      if (e.key === "Escape") setGavetaAberta(false);
    };
    document.addEventListener("keydown", aoTeclar);
    return () => document.removeEventListener("keydown", aoTeclar);
  }, [gavetaAberta]);

  // O modo entra no contexto UMA vez; `LinkModo` e `useIrPara` leem dele.
  // Assim nenhuma tela precisa saber que o modo existe para preserva-lo.
  if (embutido) {
    return (
      <ProvedorDeModo embutido>
        <div className="casca casca-embutida">
          <a className="pular" href="#conteudo">
            Pular para o conteúdo
          </a>
          <div className="area">
            {atualizacao === "disponivel" ? (
              <AvisoDeVersao onAtualizar={aplicarAtualizacao} />
            ) : null}
            <main id="conteudo" className="conteudo">
              {children}
            </main>
          </div>
        </div>
      </ProvedorDeModo>
    );
  }

  return (
    <ProvedorDeModo embutido={false}>
      <div className={`casca ${gavetaAberta ? "casca-gaveta-aberta" : ""}`}>
      <a className="pular" href="#conteudo">
        Pular para o conteúdo
      </a>

      <button
        type="button"
        className="gaveta-fundo"
        aria-label="Fechar navegação"
        tabIndex={gavetaAberta ? 0 : -1}
        onClick={fechar}
      />

      <nav className="nav" id={navId} aria-label="Navegação principal">
        <div className="nav-topo">
          <Link href="/" className="marca-link" aria-label="Fiscale Elo — início" onClick={fechar}>
            <EloWordmark />
          </Link>
        </div>

        <ul className="nav-lista">
          {itens.map((i) => (
            <li key={i.href}>
              <Link
                href={i.href}
                className="nav-item"
                aria-current={caminho === i.href ? "page" : undefined}
                onClick={fechar}
              >
                <span className="nav-icone" aria-hidden="true">
                  {i.icone}
                </span>
                <span className="nav-rotulo">{i.rotulo}</span>
                {i.cracha && naoLidos > 0 ? (
                  <span className="nav-cracha" data-testid="cracha-nao-lidos">
                    {naoLidos > 99 ? "99+" : naoLidos}
                    <span className="so-leitor">
                      {naoLidos === 1 ? " atendimento não lido" : " atendimentos não lidos"}
                    </span>
                  </span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>

        <div className="nav-rodape">
          <Link href="/configuracoes" className="perfil" onClick={fechar}>
            <Avatar name={session.name} size={34} />
            <span className="perfil-texto">
              <EmployeeBadge
                name={session.name}
                department={session.primaryDepartment}
                size="sm"
              />
              <span className="perfil-status">
                <span className="ponto-online" aria-hidden="true" />
                Disponível
              </span>
            </span>
          </Link>
        </div>
      </nav>

      <div className="area">
        <header className="barra">
          <button
            type="button"
            className="btn btn-icone somente-estreito"
            aria-expanded={gavetaAberta}
            aria-controls={navId}
            aria-label="Abrir navegação"
            onClick={() => setGavetaAberta(true)}
          >
            <span aria-hidden="true">☰</span>
          </button>

          <BuscaGlobal />

          <div className="barra-direita">
            <ThemeToggle />
          </div>
        </header>

        {atualizacao === "disponivel" ? (
          <AvisoDeVersao onAtualizar={aplicarAtualizacao} />
        ) : null}

          <main id="conteudo" className="conteudo">
            {children}
          </main>
        </div>
      </div>
    </ProvedorDeModo>
  );
}

/* ─── crachá e som ───────────────────────────────────────────────────── */

/**
 * O contador de não lidos, e o som que o acompanha.
 *
 * ---------------------------------------------------------------------
 *  O número vem do banco, sempre
 * ---------------------------------------------------------------------
 *  A tentação é decrementar no cliente quando a pessoa abre uma conversa.
 *  É mais rápido e está errado na segunda aba: ela abre num lugar, o outro
 *  continua mostrando o número velho, e os dois divergem para sempre.
 *  Aqui, qualquer evento de realtime faz PERGUNTAR de novo — uma contagem
 *  barata e sempre certa.
 *
 * ---------------------------------------------------------------------
 *  Quando o som toca
 * ---------------------------------------------------------------------
 *  Mensagem DO CLIENTE (`INBOUND`), com a preferência ligada, e não na
 *  conversa que está aberta na frente. Mensagem que eu mesmo enviei nunca
 *  toca — e a que chega na conversa que estou lendo também não: eu já a
 *  estou vendo aparecer.
 */
function useCrachaNaoLidos(inicial: number): number {
  const [naoLidos, setNaoLidos] = useState(inicial);
  const [somLigado, setSomLigado] = useState(false);
  const aberta = useConversaAberta();

  // A preferência de som é lida uma vez por carregamento. Mudou nas
  // Configurações? A própria tela grava e esta leitura acontece de novo na
  // próxima navegação — som é o tipo de coisa cujo atraso ninguém nota.
  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const r = await fetch("/api/notifications", { headers: { accept: "application/json" } });
        if (!r.ok) return;
        const d = (await r.json()) as { preferencias: { soundEnabled: boolean } };
        if (vivo) setSomLigado(d.preferencias.soundEnabled);
      } catch {
        /* som é acessório: falhar aqui não pode aparecer na tela */
      }
    })();
    return () => {
      vivo = false;
    };
  }, []);

  const recontar = useCallback(() => {
    void (async () => {
      try {
        const r = await fetch("/api/notifications/badge", {
          headers: { accept: "application/json" },
        });
        if (!r.ok) return;
        const d = (await r.json()) as { naoLidos: number };
        setNaoLidos(d.naoLidos);
        aplicarCrachaDoApp(d.naoLidos);
      } catch {
        /* mantém o número anterior: melhor defasado do que zerado errado */
      }
    })();
  }, []);

  // Qual conversa está aberta na frente. Em ref, e não em dependência do
  // `useCallback`: trocar de conversa não pode reabrir a conexão SSE.
  const abertaRef = useRef<string | null>(aberta);
  useEffect(() => {
    abertaRef.current = aberta;
  }, [aberta]);

  const aoEvento = useCallback(
    (e: EventoRealtime) => {
      recontar();

      // O evento da MENSAGEM só chega a quem assinou aquela conversa — e
      // é justamente para quem NÃO está nela que o som existe. Por isso o
      // gatilho é o evento de lista, que carrega só a direção.
      if (e.type !== "conversation.updated") return;
      if (e.reason !== "message" || e.messageDirection !== "INBOUND") return;
      if (!somLigado) return;

      // A conversa que está aberta na frente não toca: a pessoa está
      // vendo o balão aparecer.
      if (e.conversationId === abertaRef.current) return;

      // Documento escondido: quem avisa é o Web Push, com o som do
      // sistema. Dois sons para o mesmo fato é o que faz a pessoa desligar
      // os dois.
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;

      tocarAvisoDeMensagem();
    },
    [recontar, somLigado],
  );

  // `null` porque a casca acompanha a LISTA, não uma conversa: a conexão
  // que segue a conversa aberta é a da tela de Atendimentos.
  useRealtime(null, aoEvento);

  useEffect(() => {
    aplicarCrachaDoApp(inicial);
  }, [inicial]);

  return naoLidos;
}

/* ─── nova versão ────────────────────────────────────────────────────── */

/**
 * "Uma nova versão do ELO está disponível."
 *
 * Aparece, e não age sozinha. Trocar o service worker debaixo de alguém
 * que está com uma conversa aberta recarrega a página — e recarregar
 * enquanto a pessoa digita a resposta de um cliente perde o texto. Quem
 * escolhe a hora é quem está usando.
 */
function AvisoDeVersao({ onAtualizar }: { onAtualizar: () => void }) {
  return (
    <div className="faixa-versao" role="status">
      <span>Uma nova versão do ELO está disponível.</span>
      <button type="button" className="btn btn-mini" onClick={onAtualizar}>
        Atualizar
      </button>
    </div>
  );
}

/**
 * A busca do topo leva para a tela de clientes.
 *
 * Não é busca em mensagens: mensagens ainda não existem, e uma caixa que
 * promete procurar conversa e não procura é pior do que caixa nenhuma.
 */
function BuscaGlobal() {
  const [termo, setTermo] = useState("");

  return (
    // `action` de verdade: Enter navega para /clientes?q=… mesmo antes de
    // o JavaScript carregar, e o botão de voltar do navegador funciona.
    <form className="busca-global" role="search" action="/clientes">
      <span className="busca-icone" aria-hidden="true">
        ⌕
      </span>
      <input
        type="search"
        name="q"
        value={termo}
        onChange={(e) => setTermo(e.target.value)}
        placeholder="Procurar cliente por nome, CNPJ ou telefone"
        aria-label="Procurar cliente por nome, CNPJ ou telefone"
      />
    </form>
  );
}
