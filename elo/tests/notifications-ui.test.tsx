// @vitest-environment jsdom
/**
 * A interface das notificações.
 *
 * O jsdom não tem `Notification`, `PushManager` nem `serviceWorker`, e
 * isso é o ponto: os testes exercitam o que o COMPONENTE faz diante de
 * cada estado do navegador — permitida, bloqueada, não configurada, sem
 * suporte —, e não o que o navegador faria. O que depende de navegador de
 * verdade está declarado como limitação no relatório, nunca simulado como
 * se tivesse sido validado.
 *
 * O teste que carrega o arquivo é o da PERMISSÃO: nada pode chamar
 * `Notification.requestPermission()` sem um clique. Um diálogo no primeiro
 * segundo vira "Bloquear" permanente, e "Bloquear" no Chrome é uma decisão
 * que a pessoa comum não sabe desfazer — o recurso morre para o escritório
 * inteiro sem ninguém entender por quê.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ConfiguracoesNotificacoes } from "@/components/configuracoes-notificacoes";
import { ThemeProvider } from "@/components/theme";
import {
  permissaoAtual,
  reiniciarSom,
  tocarAvisoDeMensagem,
  INTERVALO_MINIMO_SOM_MS,
} from "@/components/notificacoes";

/**
 * O `AppShell` usa `usePathname` e `useRouter`, e os dois exigem o router
 * do Next montado — que so existe dentro de uma aplicacao de verdade.
 *
 * O substituto e deliberadamente burro: a navegacao NAO e o assunto deste
 * arquivo, e simular roteamento aqui daria a impressao de estar testando
 * algo que nao esta. O que se mede e o cracha.
 */
vi.mock("next/navigation", () => ({
  usePathname: () => "/atendimentos",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const PREFERENCIAS_PADRAO = {
  newMessages: true,
  newConversations: true,
  assignedToMe: true,
  transferredToMe: true,
  systemNotices: true,
  soundEnabled: true,
  showPreview: false,
  quietHoursStart: null,
  quietHoursEnd: null,
};

const CHAVE_PUBLICA = "B".repeat(87);

interface Cenario {
  preferencias?: Partial<typeof PREFERENCIAS_PADRAO>;
  dispositivos?: { id: string; endpointHash: string; deviceLabel: string | null }[];
  push?: { disponivel: boolean; vapidPublicKey?: string; motivo?: string };
}

/** O servidor, de mentira. Guarda o que foi salvo para o teste conferir. */
function servidor(c: Cenario = {}) {
  const salvos: unknown[] = [];
  const preferencias = { ...PREFERENCIAS_PADRAO, ...c.preferencias };

  const fetchFalso = vi.fn(async (url: string | URL, init?: RequestInit) => {
    const caminho = String(url);

    if (caminho === "/api/notifications") {
      return new Response(
        JSON.stringify({
          preferencias,
          dispositivos: c.dispositivos ?? [],
          push: c.push ?? { disponivel: true, vapidPublicKey: CHAVE_PUBLICA },
        }),
        { headers: { "content-type": "application/json" } },
      );
    }

    if (caminho === "/api/notifications/preferences") {
      const corpo = JSON.parse(String(init?.body)) as Record<string, unknown>;
      salvos.push(corpo);
      Object.assign(preferencias, corpo);
      return new Response(JSON.stringify({ preferencias }), {
        headers: { "content-type": "application/json" },
      });
    }

    return new Response("{}", { headers: { "content-type": "application/json" } });
  });

  vi.stubGlobal("fetch", fetchFalso);
  return { salvos, fetchFalso };
}

/** Estado do navegador quanto a notificações. */
function navegador(estado: "permitida" | "bloqueada" | "nao-configurada" | "sem-suporte") {
  if (estado === "sem-suporte") {
    vi.stubGlobal("Notification", undefined);
    vi.stubGlobal("PushManager", undefined);
    return;
  }
  const permission =
    estado === "permitida" ? "granted" : estado === "bloqueada" ? "denied" : "default";
  vi.stubGlobal("Notification", { permission, requestPermission: vi.fn() });
  vi.stubGlobal("PushManager", class {});
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      ready: Promise.resolve({}),
      // O registro nunca resolve de proposito: o jsdom nao tem service
      // worker, e fingir que tem daria a impressao de estar validando
      // instalacao — o que so o navegador de verdade valida.
      register: () => new Promise<never>(() => {}),
      addEventListener() {},
      removeEventListener() {},
    },
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/* ══ 1. estado da permissão ═════════════════════════════════════════ */

describe("estado da permissão", () => {
  it("sem suporte no navegador", () => {
    navegador("sem-suporte");
    expect(permissaoAtual()).toBe("indisponivel");
  });

  it("os três estados que a tela precisa distinguir", () => {
    navegador("nao-configurada");
    expect(permissaoAtual()).toBe("nao-configurada");
    navegador("permitida");
    expect(permissaoAtual()).toBe("permitida");
    navegador("bloqueada");
    expect(permissaoAtual()).toBe("bloqueada");
  });
});

/* ══ 2. a tela ══════════════════════════════════════════════════════ */

describe("Configurações → Notificações", () => {
  it("NÃO pede permissão ao abrir — só depois do clique", async () => {
    navegador("nao-configurada");
    servidor();

    render(<ConfiguracoesNotificacoes />);
    await screen.findByText("Este dispositivo");

    const pedir = (globalThis as unknown as { Notification: { requestPermission: () => void } })
      .Notification.requestPermission;
    expect(pedir).not.toHaveBeenCalled();

    // E o botão existe, com a explicação do que vai acontecer.
    expect(screen.getByRole("button", { name: /ativar notificações/i })).toBeInTheDocument();
    expect(
      screen.getByText(/mesmo quando não estiver olhando para o ELO/i),
    ).toBeInTheDocument();
  });

  it("bloqueada: explica que a mudança é no navegador, e some com o botão", async () => {
    navegador("bloqueada");
    servidor();

    render(<ConfiguracoesNotificacoes />);
    await screen.findByText(/bloqueadas no navegador/i);

    // Não fica chamando `requestPermission` de novo: o navegador não
    // pergunta mais, e o botão só ensinaria a clicar em algo inútil.
    expect(screen.queryByRole("button", { name: /ativar notificações/i })).toBeNull();
    expect(screen.getByText(/Bloqueada pelo navegador/i)).toBeInTheDocument();
  });

  it("sem chaves no servidor, a tela diz isso em vez de oferecer o botão", async () => {
    navegador("nao-configurada");
    servidor({ push: { disponivel: false, motivo: "SEM_CHAVES" } });

    render(<ConfiguracoesNotificacoes />);
    await screen.findByText(/não tem as chaves de notificação/i);

    expect(screen.getByRole("button", { name: /ativar notificações/i })).toBeDisabled();
  });

  it("este dispositivo inscrito: mostra Ativas e oferece desativar", async () => {
    navegador("permitida");
    servidor({
      dispositivos: [{ id: "1", endpointHash: "abc123", deviceLabel: "Chrome no Windows" }],
    });
    window.localStorage.setItem("elo.dispositivo", "abc123");

    render(<ConfiguracoesNotificacoes />);
    await screen.findByText(/Ativas/);

    expect(
      screen.getByRole("button", { name: /desativar neste dispositivo/i }),
    ).toBeInTheDocument();
    window.localStorage.clear();
  });

  it("outros aparelhos aparecem, e a tela diz que desativar aqui não os toca", async () => {
    navegador("permitida");
    servidor({
      dispositivos: [
        { id: "1", endpointHash: "aqui", deviceLabel: "Chrome no Windows" },
        { id: "2", endpointHash: "celular", deviceLabel: "Chrome no Android" },
      ],
    });
    window.localStorage.setItem("elo.dispositivo", "aqui");

    render(<ConfiguracoesNotificacoes />);
    await screen.findByText(/outro aparelho/i);
    expect(screen.getByText(/Chrome no Android/)).toBeInTheDocument();
    expect(screen.getByText(/não mexe nele/i)).toBeInTheDocument();
    window.localStorage.clear();
  });
});

/* ══ 3. privacidade ═════════════════════════════════════════════════ */

describe("privacidade da prévia", () => {
  it("desligada, a amostra mostra QUEM falou — nunca o quê", async () => {
    navegador("permitida");
    servidor({ preferencias: { showPreview: false } });

    render(<ConfiguracoesNotificacoes />);
    await screen.findByText("Empresa ABC entrou em contato.");

    expect(screen.queryByText(/preciso da guia/i)).toBeNull();
    expect(screen.getByLabelText(/como o aviso aparece/i)).toBeInTheDocument();
  });

  it("ligada, a amostra mostra o trecho — e a mudança é salva", async () => {
    navegador("permitida");
    const { salvos } = servidor({ preferencias: { showPreview: false } });

    render(<ConfiguracoesNotificacoes />);
    const caixa = await screen.findByLabelText(/mostrar prévia da mensagem/i);
    expect(caixa).not.toBeChecked();

    fireEvent.click(caixa);

    await waitFor(() => expect(salvos).toContainEqual({ showPreview: true }));
    await screen.findByText(/preciso da guia do DAS/i);
  });

  it("a tela avisa que a notificação aparece na tela bloqueada", async () => {
    navegador("permitida");
    servidor();
    render(<ConfiguracoesNotificacoes />);
    await screen.findByText(/tela bloqueada/i);
  });
});

/* ══ 4. preferências ════════════════════════════════════════════════ */

describe("preferências", () => {
  it("as sete opções estão na tela", async () => {
    navegador("permitida");
    servidor();
    render(<ConfiguracoesNotificacoes />);
    await screen.findByLabelText(/novas mensagens/i);

    for (const rotulo of [
      /novas mensagens/i,
      /novos atendimentos/i,
      /atendimento atribuído a mim/i,
      /transferência para mim/i,
      /notificações do sistema/i,
      /som quando o ELO estiver aberto/i,
      /mostrar prévia da mensagem/i,
    ]) {
      expect(screen.getByLabelText(rotulo)).toBeInTheDocument();
    }
  });

  it("desmarcar salva só o campo mexido", async () => {
    navegador("permitida");
    const { salvos } = servidor();

    render(<ConfiguracoesNotificacoes />);
    const caixa = await screen.findByLabelText(/novos atendimentos/i);
    fireEvent.click(caixa);

    await waitFor(() => expect(salvos).toContainEqual({ newConversations: false }));
    // Nada além do que mudou: um PUT do objeto inteiro faria duas abas
    // abertas se sobrescreverem com estado velho.
    expect(salvos.every((s) => Object.keys(s as object).length === 1)).toBe(true);
  });

  it("o horário silencioso começa desligado e liga com um intervalo que faz sentido", async () => {
    navegador("permitida");
    const { salvos } = servidor();

    render(<ConfiguracoesNotificacoes />);
    const caixa = await screen.findByLabelText(/não perturbe entre horários/i);
    expect(caixa).not.toBeChecked();

    fireEvent.click(caixa);

    // 20h às 8h. Começar com 00:00–00:00 seria uma janela vazia parecendo
    // ligada.
    await waitFor(() =>
      expect(salvos).toContainEqual({ quietHoursStart: 20 * 60, quietHoursEnd: 8 * 60 }),
    );
  });
});

/* ══ 5. som ═════════════════════════════════════════════════════════ */

describe("som", () => {
  beforeEach(() => reiniciarSom());

  function audioFalso() {
    const osciladores: { start: unknown; stop: unknown }[] = [];
    const ganho = () => ({
      gain: {
        setValueAtTime: vi.fn(),
        linearRampToValueAtTime: vi.fn(),
        exponentialRampToValueAtTime: vi.fn(),
      },
      connect: vi.fn(() => ({ connect: vi.fn() })),
    });

    class ContextoFalso {
      state = "running";
      currentTime = 0;
      destination = {};
      resume = vi.fn();
      createGain = vi.fn(ganho);
      createOscillator = vi.fn(() => {
        const o = {
          type: "",
          frequency: { value: 0 },
          connect: vi.fn(() => ({ connect: vi.fn() })),
          start: vi.fn(),
          stop: vi.fn(),
        };
        osciladores.push(o);
        return o;
      });
    }

    vi.stubGlobal("AudioContext", ContextoFalso);
    return osciladores;
  }

  it("toca quando é chamado", () => {
    const osciladores = audioFalso();
    expect(tocarAvisoDeMensagem(1_000)).toBe(true);
    // Duas notas curtas — discreto, e não um alarme.
    expect(osciladores).toHaveLength(2);
  });

  it("NÃO toca duas vezes em menos de um segundo", () => {
    // Cinco mensagens juntas fariam cinco bipes sobrepostos, que é como
    // se ensina uma equipe a desligar o som.
    audioFalso();
    expect(tocarAvisoDeMensagem(5_000)).toBe(true);
    expect(tocarAvisoDeMensagem(5_100)).toBe(false);
    expect(tocarAvisoDeMensagem(5_500)).toBe(false);
    expect(tocarAvisoDeMensagem(5_000 + INTERVALO_MINIMO_SOM_MS)).toBe(true);
  });

  it("sem AudioContext no navegador, não quebra — apenas não toca", () => {
    vi.stubGlobal("AudioContext", undefined);
    expect(tocarAvisoDeMensagem(50_000)).toBe(false);
  });

  it("marcar a opção de som toca uma amostra na hora", async () => {
    navegador("permitida");
    servidor({ preferencias: { soundEnabled: false } });
    const osciladores = audioFalso();

    render(<ConfiguracoesNotificacoes />);
    const caixa = await screen.findByLabelText(/som quando o ELO estiver aberto/i);
    fireEvent.click(caixa);

    // Sem isso, "som discreto" é uma promessa que a pessoa só confere
    // quando um cliente escrever.
    await waitFor(() => expect(osciladores.length).toBeGreaterThan(0));
  });

  it("DESmarcar não toca nada", async () => {
    navegador("permitida");
    servidor({ preferencias: { soundEnabled: true } });
    const osciladores = audioFalso();

    render(<ConfiguracoesNotificacoes />);
    const caixa = await screen.findByLabelText(/som quando o ELO estiver aberto/i);
    fireEvent.click(caixa);

    await waitFor(() => expect(caixa).not.toBeChecked());
    expect(osciladores).toHaveLength(0);
  });
});

/* ══ 6. o crachá na navegação ═══════════════════════════════════════ */

describe("crachá de não lidos", () => {
  /**
   * A casca abre uma conexão de realtime ao montar. O `EventSource` falso
   * do `tests/setup.ts` não emite nada — o que se testa aqui é o número
   * que o servidor entregou e o que a tela faz com ele, não o realtime,
   * que tem testes próprios contra o barramento e contra HTTP.
   */
  function sessao() {
    return {
      name: "Aline Exemplo",
      email: "aline@escritorio.teste",
      role: "AGENT",
      primaryDepartment: "Fiscal",
      departments: [],
    };
  }

  /** A casca inteira, com o tema — o seletor de tema vive nela. */
  async function renderCasca(naoLidos: number) {
    const { AppShell } = await import("@/components/app-shell");
    render(
      <ThemeProvider>
        <AppShell session={sessao()} naoLidosIniciais={naoLidos}>
          <p>conteúdo</p>
        </AppShell>
      </ThemeProvider>,
    );
  }

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ preferencias: PREFERENCIAS_PADRAO, naoLidos: 0 }), {
          headers: { "content-type": "application/json" },
        }),
      ),
    );
  });

  it("mostra o número que veio do servidor", async () => {
    await renderCasca(3);

    const cracha = screen.getByTestId("cracha-nao-lidos");
    expect(cracha).toHaveTextContent("3");
    // O leitor de tela precisa da unidade; o olho, só do número.
    expect(cracha).toHaveTextContent(/atendimentos não lidos/i);
  });

  it("zerado, o crachá simplesmente NÃO EXISTE", async () => {
    // Um "0" na navegação é ruído permanente: a pessoa aprende a ignorar
    // aquele canto da tela, e aí o número deixa de servir quando importa.
    await renderCasca(0);
    expect(screen.queryByTestId("cracha-nao-lidos")).toBeNull();
  });

  it("acima de 99 vira 99+, e o crachá não estica a navegação", async () => {
    await renderCasca(137);
    expect(screen.getByTestId("cracha-nao-lidos")).toHaveTextContent("99+");
  });

  it("o crachá fica no item Atendimentos, e em nenhum outro", async () => {
    await renderCasca(5);

    const item = screen.getByRole("link", { name: /atendimentos/i });
    expect(within(item).getByTestId("cracha-nao-lidos")).toBeInTheDocument();
    expect(screen.getAllByTestId("cracha-nao-lidos")).toHaveLength(1);
  });
});
