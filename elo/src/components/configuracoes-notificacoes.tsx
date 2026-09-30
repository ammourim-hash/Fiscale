"use client";

/**
 * Configurações → Notificações.
 *
 * =====================================================================
 *  A tela existe para uma pergunta ser respondível
 * =====================================================================
 *  "Por que eu não recebo aviso?" tem quatro respostas possíveis, e sem
 *  esta tela nenhuma delas é visível para quem trabalha:
 *
 *    1. o navegador nunca perguntou;
 *    2. o navegador está BLOQUEADO (e isso não se resolve aqui dentro);
 *    3. este aparelho não está inscrito;
 *    4. a preferência está desligada.
 *
 *  Um sistema de notificação sem esta tela vira "às vezes chega".
 *
 * =====================================================================
 *  O botão pede a permissão. Nada mais pede.
 * =====================================================================
 *  `Notification.requestPermission()` só é chamado dentro do clique em
 *  "Ativar notificações", e depois de a pessoa ler o que vai acontecer.
 *  Pedir na abertura do sistema é como se ganha um "Bloquear" permanente
 *  — decisão que a pessoa comum não sabe desfazer, e que tira o recurso do
 *  escritório inteiro.
 */
import { useCallback, useEffect, useState } from "react";

import {
  ativarNotificacoes,
  desativarNesteDispositivo,
  esteDispositivo,
  permissaoAtual,
  tocarAvisoDeMensagem,
  reiniciarSom,
  type EstadoPermissao,
} from "./notificacoes";
import { StatusBadge } from "./ui";

interface Preferencias {
  newMessages: boolean;
  newConversations: boolean;
  assignedToMe: boolean;
  transferredToMe: boolean;
  systemNotices: boolean;
  soundEnabled: boolean;
  showPreview: boolean;
  quietHoursStart: number | null;
  quietHoursEnd: number | null;
}

interface Dispositivo {
  id: string;
  endpointHash: string;
  deviceLabel: string | null;
  createdAt: string;
  lastUsedAt: string | null;
}

type Push =
  | { disponivel: true; vapidPublicKey: string }
  | { disponivel: false; motivo: "DESLIGADO" | "SEM_CHAVES" };

/**
 * A ordem da lista é a ordem de importância para quem atende — mensagem
 * primeiro, aviso de sistema por último. A prévia fica separada embaixo,
 * porque não é "mais uma caixinha": é a única que expõe conteúdo.
 */
const OPCOES: { chave: keyof Preferencias; rotulo: string; ajuda?: string }[] = [
  { chave: "newMessages", rotulo: "Novas mensagens" },
  { chave: "newConversations", rotulo: "Novos atendimentos" },
  { chave: "assignedToMe", rotulo: "Atendimento atribuído a mim" },
  { chave: "transferredToMe", rotulo: "Transferência para mim" },
  { chave: "systemNotices", rotulo: "Notificações do sistema" },
  {
    chave: "soundEnabled",
    rotulo: "Som quando o ELO estiver aberto",
    ajuda: "Só toca com o Elo na frente, e nunca na conversa que você está lendo.",
  },
];

export function ConfiguracoesNotificacoes() {
  const [carregando, setCarregando] = useState(true);
  const [prefs, setPrefs] = useState<Preferencias | null>(null);
  const [push, setPush] = useState<Push | null>(null);
  const [dispositivos, setDispositivos] = useState<Dispositivo[]>([]);
  // Inicializador preguicoso, e nao efeito: sao valores do NAVEGADOR, e
  // le-los no primeiro render evita o "corrigir depois" que o React
  // desaconselha. Nao ha risco de divergencia com o servidor porque o
  // primeiro render (aqui e la) mostra "Carregando...", e estes valores so
  // aparecem depois que a consulta volta.
  const [permissao, setPermissao] = useState<EstadoPermissao>(() => permissaoAtual());
  const [ocupado, setOcupado] = useState(false);
  const [recado, setRecado] = useState<string | null>(null);
  const [esteHash, setEsteHash] = useState<string | null>(() => esteDispositivo());

  const carregar = useCallback(async () => {
    try {
      const r = await fetch("/api/notifications", { headers: { accept: "application/json" } });
      if (!r.ok) throw new Error(String(r.status));
      const d = (await r.json()) as {
        preferencias: Preferencias;
        dispositivos: Dispositivo[];
        push: Push;
      };
      setPrefs(d.preferencias);
      setDispositivos(d.dispositivos);
      setPush(d.push);
    } catch {
      setRecado("Não foi possível carregar suas preferências.");
    } finally {
      setCarregando(false);
    }
  }, []);

  // A busca inicial vive dentro de uma funcao assincrona anonima — o
  // mesmo formato do resto do projeto. Chamar direto uma funcao que grava
  // estado no CORPO do efeito e o que o React desaconselha: o primeiro
  // render dispararia um segundo antes de qualquer coisa chegar da rede.
  useEffect(() => {
    void (async () => {
      await carregar();
    })();
  }, [carregar]);

  const salvar = useCallback(
    async (mudanca: Partial<Preferencias>) => {
      // A tela muda na hora e volta atrás se o servidor recusar. Esperar a
      // resposta para marcar uma caixinha faz a interface parecer travada
      // numa rede de escritório.
      const anterior = prefs;
      setPrefs((p) => (p ? { ...p, ...mudanca } : p));
      setRecado(null);

      try {
        const r = await fetch("/api/notifications/preferences", {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(mudanca),
        });
        if (!r.ok) throw new Error(String(r.status));
        const d = (await r.json()) as { preferencias: Preferencias };
        setPrefs(d.preferencias);
      } catch {
        setPrefs(anterior);
        setRecado("Não foi possível salvar. Tente de novo.");
      }
    },
    [prefs],
  );

  const ativar = useCallback(async () => {
    if (!push?.disponivel) return;
    setOcupado(true);
    setRecado(null);

    // A lista dos MEUS aparelhos vai junto: uma inscricao que ja exista
    // neste navegador e nao esteja nela pertence a outra pessoa, e precisa
    // ser desfeita no provedor antes. Ver ativarNotificacoes.
    const r = await ativarNotificacoes(
      push.vapidPublicKey,
      dispositivos.map((d) => d.endpointHash),
    );
    setPermissao(permissaoAtual());

    if (r.ok) {
      setEsteHash(r.endpointHash);
      await carregar();
      setRecado("Notificações ativadas neste dispositivo.");
    } else if (r.motivo === "NEGADA") {
      setRecado("O navegador não concedeu a permissão.");
    } else if (r.motivo === "INDISPONIVEL") {
      setRecado("Este navegador não oferece notificações do sistema.");
    } else {
      setRecado("Não foi possível ativar. Tente de novo.");
    }

    setOcupado(false);
  }, [push, dispositivos, carregar]);

  const desativar = useCallback(async () => {
    setOcupado(true);
    setRecado(null);
    await desativarNesteDispositivo();
    setEsteHash(null);
    await carregar();
    setRecado("Notificações desativadas neste dispositivo.");
    setOcupado(false);
  }, [carregar]);

  if (carregando) {
    return (
      <div className="painel">
        <p className="painel-texto">Carregando…</p>
      </div>
    );
  }

  if (!prefs) {
    return (
      <div className="painel">
        <p className="painel-texto">{recado ?? "Preferências indisponíveis."}</p>
      </div>
    );
  }

  const inscritoAqui = Boolean(esteHash && dispositivos.some((d) => d.endpointHash === esteHash));
  const outros = dispositivos.filter((d) => d.endpointHash !== esteHash);

  return (
    <>
      {/* ── este dispositivo ────────────────────────────────────────── */}
      <div className="painel">
        <div className="dispositivo-linha">
          <div>
            <p className="painel-titulo">Este dispositivo</p>
            <p className="painel-texto">
              <EstadoDoDispositivo
                permissao={permissao}
                inscrito={inscritoAqui}
                push={push}
              />
            </p>
          </div>

          {inscritoAqui ? (
            <button
              type="button"
              className="btn"
              onClick={() => void desativar()}
              disabled={ocupado}
            >
              Desativar neste dispositivo
            </button>
          ) : permissao === "bloqueada" ? null : (
            <button
              type="button"
              className="btn btn-acao"
              onClick={() => void ativar()}
              disabled={ocupado || permissao === "indisponivel" || !push?.disponivel}
            >
              Ativar notificações
            </button>
          )}
        </div>

        {!inscritoAqui && permissao !== "bloqueada" ? (
          <p className="painel-nota">
            Receba avisos de novos atendimentos mesmo quando não estiver olhando
            para o ELO — inclusive com o navegador fechado.
          </p>
        ) : null}

        {permissao === "bloqueada" ? (
          <p className="painel-nota">
            As notificações estão <strong>bloqueadas no navegador</strong>. O Elo
            não consegue desfazer isso de dentro: abra o cadeado ao lado do
            endereço, mude Notificações para <em>Permitir</em> e recarregue a
            página. No Windows, confira também se o Foco/Assistente de foco não
            está silenciando os avisos.
          </p>
        ) : null}

        {outros.length > 0 ? (
          <p className="painel-nota">
            Você também recebe em {outros.length}{" "}
            {outros.length === 1 ? "outro aparelho" : "outros aparelhos"}
            {outros.some((d) => d.deviceLabel)
              ? ` (${outros.map((d) => d.deviceLabel ?? "aparelho").join(", ")})`
              : ""}
            . Desativar aqui não mexe {outros.length === 1 ? "nele" : "neles"}.
          </p>
        ) : null}

        {recado ? (
          <p className="painel-nota" role="status">
            {recado}
          </p>
        ) : null}
      </div>

      {/* ── o que avisar ────────────────────────────────────────────── */}
      <div className="painel">
        <p className="painel-titulo">O que avisar</p>
        <ul className="lista-opcoes">
          {OPCOES.map((o) => (
            <li key={o.chave}>
              <label className="opcao">
                <input
                  type="checkbox"
                  checked={Boolean(prefs[o.chave])}
                  onChange={(e) => {
                    void salvar({ [o.chave]: e.target.checked } as Partial<Preferencias>);
                    // Marcar o som é o único lugar em que a pessoa pode
                    // OUVIR o que acabou de escolher. Sem isso, "som
                    // discreto" é uma promessa que ela só confere quando
                    // um cliente escrever.
                    if (o.chave === "soundEnabled" && e.target.checked) {
                      reiniciarSom();
                      tocarAvisoDeMensagem();
                    }
                  }}
                />
                <span>
                  {o.rotulo}
                  {o.ajuda ? <small className="opcao-ajuda">{o.ajuda}</small> : null}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </div>

      {/* ── privacidade ─────────────────────────────────────────────── */}
      <div className="painel">
        <p className="painel-titulo">Privacidade</p>

        <label className="opcao">
          <input
            type="checkbox"
            checked={prefs.showPreview}
            onChange={(e) => void salvar({ showPreview: e.target.checked })}
          />
          <span>
            Mostrar prévia da mensagem
            <small className="opcao-ajuda">
              Desligado, o aviso diz quem falou. Ligado, mostra um trecho.
            </small>
          </span>
        </label>

        <p className="painel-nota">
          A notificação aparece na <strong>tela bloqueada</strong> do Windows e do
          celular. Por isso a prévia vem desligada: o escritório trata de assunto
          de terceiro, e um valor de faturamento não deveria cruzar a tela de quem
          passa pela mesa.
        </p>

        <div className="previa-aviso" aria-label="Como o aviso aparece">
          <span className="previa-aviso-titulo">ELO</span>
          <span className="previa-aviso-corpo">
            {prefs.showPreview
              ? "Empresa ABC: bom dia, preciso da guia do DAS deste mês"
              : "Empresa ABC entrou em contato."}
          </span>
        </div>
      </div>

      {/* ── não perturbe ────────────────────────────────────────────── */}
      <div className="painel">
        <p className="painel-titulo">Horário silencioso</p>
        <p className="painel-texto">
          Silencia o <strong>som</strong> dentro do Elo no intervalo escolhido. As
          notificações do sistema continuam chegando — quem controla o silêncio
          delas é o Windows ou o celular, e duplicar esse controle aqui daria dois
          lugares para desligar a mesma coisa.
        </p>

        <HorarioSilencioso
          inicio={prefs.quietHoursStart}
          fim={prefs.quietHoursEnd}
          onMudar={(inicio, fim) =>
            void salvar({ quietHoursStart: inicio, quietHoursEnd: fim })
          }
        />
      </div>
    </>
  );
}

function EstadoDoDispositivo({
  permissao,
  inscrito,
  push,
}: {
  permissao: EstadoPermissao;
  inscrito: boolean;
  push: Push | null;
}) {
  if (push && !push.disponivel) {
    return (
      <>
        <StatusBadge tone="atencao">Indisponível no servidor</StatusBadge>{" "}
        {push.motivo === "SEM_CHAVES"
          ? "O servidor não tem as chaves de notificação configuradas."
          : "As notificações estão desligadas neste servidor."}
      </>
    );
  }

  if (permissao === "indisponivel") {
    return (
      <>
        <StatusBadge tone="neutro">Sem suporte</StatusBadge> Este navegador não
        oferece notificações do sistema.
      </>
    );
  }

  if (permissao === "bloqueada") {
    return (
      <>
        <StatusBadge tone="erro">Bloqueada pelo navegador</StatusBadge>
      </>
    );
  }

  if (permissao === "nao-configurada") {
    return (
      <>
        <StatusBadge tone="neutro">Não configurada</StatusBadge> O navegador ainda
        não perguntou.
      </>
    );
  }

  return inscrito ? (
    <>
      <StatusBadge tone="ok">Ativas</StatusBadge> Este aparelho recebe avisos.
    </>
  ) : (
    <>
      <StatusBadge tone="atencao">Permitida, sem inscrição</StatusBadge> Ative para
      este aparelho receber.
    </>
  );
}

/* ─── horário ────────────────────────────────────────────────────────── */

function paraHHMM(minutos: number | null): string {
  if (minutos === null) return "";
  const h = String(Math.floor(minutos / 60)).padStart(2, "0");
  const m = String(minutos % 60).padStart(2, "0");
  return `${h}:${m}`;
}

function deHHMM(valor: string): number | null {
  const casa = /^(\d{2}):(\d{2})$/.exec(valor);
  if (!casa) return null;
  const minutos = Number(casa[1]) * 60 + Number(casa[2]);
  return minutos >= 0 && minutos <= 1439 ? minutos : null;
}

function HorarioSilencioso({
  inicio,
  fim,
  onMudar,
}: {
  inicio: number | null;
  fim: number | null;
  onMudar: (inicio: number | null, fim: number | null) => void;
}) {
  const ligado = inicio !== null && fim !== null;

  return (
    <>
      <label className="opcao">
        <input
          type="checkbox"
          checked={ligado}
          onChange={(e) =>
            // Ao ligar, um intervalo que faz sentido para um escritório —
            // 20h às 8h. Começar com 00:00–00:00 seria uma janela vazia
            // parecendo ligada.
            onMudar(e.target.checked ? 20 * 60 : null, e.target.checked ? 8 * 60 : null)
          }
        />
        <span>Não perturbe entre horários</span>
      </label>

      {ligado ? (
        <div className="horarios">
          <label>
            <span>Das</span>
            <input
              type="time"
              value={paraHHMM(inicio)}
              onChange={(e) => onMudar(deHHMM(e.target.value) ?? inicio, fim)}
            />
          </label>
          <label>
            <span>às</span>
            <input
              type="time"
              value={paraHHMM(fim)}
              onChange={(e) => onMudar(inicio, deHHMM(e.target.value) ?? fim)}
            />
          </label>
        </div>
      ) : null}
    </>
  );
}
