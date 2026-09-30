// @vitest-environment jsdom
/**
 * Player e gravador.
 *
 * O jsdom não reproduz áudio nem grava microfone — e é justamente por
 * isso que estes testes existem: eles verificam o que o COMPONENTE faz
 * (chama play, muda playbackRate, solta a trilha, mostra o aviso), e não
 * o que o navegador faria com o som. A parte que depende de hardware está
 * declarada como limitação no relatório, não simulada aqui.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { GravadorDeVoz, formatoSuportado } from "@/components/gravador";
import { AnexoNaMensagem, formatarDuracao, formatarTamanho, type Anexo } from "@/components/media";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function anexo(over: Partial<Anexo> = {}): Anexo {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    type: "AUDIO",
    fileName: "recado.mp3",
    mimeType: "audio/mpeg",
    sizeBytes: 1024 * 512,
    width: null,
    height: null,
    durationMs: 37_000,
    ...over,
  };
}

/* ─── formatação ─────────────────────────────────────────────────────── */

describe("formatação", () => {
  it("duração em minutos e segundos", () => {
    expect(formatarDuracao(37_000)).toBe("0:37");
    expect(formatarDuracao(95_000)).toBe("1:35");
    expect(formatarDuracao(0)).toBe("0:00");
    expect(formatarDuracao(null)).toBe("0:00");
  });

  it("tamanho legível", () => {
    expect(formatarTamanho(512)).toBe("512 B");
    expect(formatarTamanho(2048)).toBe("2 KB");
    expect(formatarTamanho(1024 * 1024 * 1.2)).toBe("1,2 MB");
  });
});

/* 30, 31 ─ player ───────────────────────────────────────────────────── */

describe("30/31. player de áudio", () => {
  it("mostra a duração sem ter baixado o arquivo", () => {
    render(<AnexoNaMensagem anexo={anexo()} />);
    // "0:37" vem do metadado. `preload=none`: nenhum byte foi buscado.
    expect(screen.getByText(/0:37/)).toBeTruthy();

    const audio = document.querySelector("audio");
    expect(audio?.getAttribute("preload")).toBe("none");
  });

  it("o áudio aponta para a rota autenticada, nunca para o storage", () => {
    render(<AnexoNaMensagem anexo={anexo()} />);
    const audio = document.querySelector("audio");
    expect(audio?.getAttribute("src")).toBe(
      "/api/attachments/11111111-1111-4111-8111-111111111111",
    );
  });

  it("o botão tem nome acessível e chama play", () => {
    const play = vi.fn().mockResolvedValue(undefined);
    // jsdom não implementa play(); sem isto o clique lançaria.
    Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
      configurable: true,
      value: play,
    });

    render(<AnexoNaMensagem anexo={anexo()} />);
    const botao = screen.getByRole("button", { name: /reproduzir/i });
    fireEvent.click(botao);
    expect(play).toHaveBeenCalled();
  });

  it("31. a velocidade cicla 1x → 1.5x → 2x → 1x e chega ao elemento", () => {
    render(<AnexoNaMensagem anexo={anexo()} />);
    const audio = document.querySelector("audio")!;
    const botao = screen.getByRole("button", { name: /velocidade/i });

    expect(botao.textContent).toBe("1x");

    fireEvent.click(botao);
    expect(botao.textContent).toBe("1.5x");
    expect(audio.playbackRate).toBe(1.5);

    fireEvent.click(botao);
    expect(botao.textContent).toBe("2x");
    expect(audio.playbackRate).toBe(2);

    fireEvent.click(botao);
    expect(botao.textContent).toBe("1x");
    expect(audio.playbackRate).toBe(1);
  });

  it("arrastar a barra move a posição do áudio", () => {
    render(<AnexoNaMensagem anexo={anexo()} />);
    const audio = document.querySelector("audio")!;
    const barra = screen.getByLabelText(/posição do áudio/i);

    fireEvent.change(barra, { target: { value: "12" } });
    // O seek só funciona de verdade porque o servidor responde Range —
    // testado em tests/media-http.test.ts.
    expect(audio.currentTime).toBe(12);
  });

  it("o player é operável por teclado", () => {
    render(<AnexoNaMensagem anexo={anexo()} />);
    // Botões e input[range] são focáveis por natureza; o que quebraria
    // isso seria usar <div onClick>.
    for (const nome of [/reproduzir/i, /velocidade/i]) {
      const b = screen.getByRole("button", { name: nome });
      b.focus();
      expect(document.activeElement).toBe(b);
    }
  });
});

/* ─── imagem e documento ─────────────────────────────────────────────── */

describe("imagem e documento", () => {
  it("imagem entra como miniatura preguiçosa e abre num visor", () => {
    render(
      <AnexoNaMensagem
        anexo={anexo({ type: "IMAGE", fileName: "foto.png", mimeType: "image/png", durationMs: null })}
      />,
    );

    const img = document.querySelector("img")!;
    expect(img.getAttribute("loading")).toBe("lazy");

    fireEvent.click(screen.getByRole("button", { name: /abrir imagem foto\.png/i }));
    expect(screen.getByRole("dialog", { name: "foto.png" })).toBeTruthy();
  });

  it("o visor fecha com Esc", async () => {
    render(
      <AnexoNaMensagem
        anexo={anexo({ type: "IMAGE", fileName: "foto.png", mimeType: "image/png", durationMs: null })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /abrir imagem/i }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("documento mostra nome, tipo e tamanho", () => {
    render(
      <AnexoNaMensagem
        anexo={anexo({
          type: "DOCUMENT",
          fileName: "DAS_08-2026.pdf",
          mimeType: "application/pdf",
          sizeBytes: 1024 * 1024 * 1.2,
          durationMs: null,
        })}
      />,
    );
    expect(screen.getByText("DAS_08-2026.pdf")).toBeTruthy();
    expect(screen.getByText(/PDF · 1,2 MB/)).toBeTruthy();
  });
});

/* 32-34 ─ gravador ──────────────────────────────────────────────────── */

/** MediaRecorder de mentira: registra o que foi chamado. */
function instalarGravadorFalso(): { pararTrilha: ReturnType<typeof vi.fn> } {
  const pararTrilha = vi.fn();

  vi.stubGlobal(
    "MediaRecorder",
    class {
      static isTypeSupported(t: string): boolean {
        return t.startsWith("audio/webm");
      }
      state = "recording";
      ondataavailable: ((e: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      start(): void {}
      stop(): void {
        this.ondataavailable?.({ data: new Blob(["x"], { type: "audio/webm" }) });
        this.state = "inactive";
        this.onstop?.();
      }
      pause(): void {
        this.state = "paused";
      }
      resume(): void {
        this.state = "recording";
      }
    },
  );

  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: vi.fn().mockResolvedValue({
        getTracks: () => [{ stop: pararTrilha }],
      }),
    },
  });

  return { pararTrilha };
}

describe("32-34. gravador de voz", () => {
  it("escolhe um formato que o navegador suporta", () => {
    instalarGravadorFalso();
    const f = formatoSuportado();
    // Nenhum navegador grava MP3: o formato é escolhido, não presumido.
    expect(f?.mime).toContain("audio/webm");
    expect(f?.extensao).toBe("webm");
  });

  it("32. grava, para e oferece ouvir antes de enviar", async () => {
    instalarGravadorFalso();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:falso"),
      revokeObjectURL: vi.fn(),
    });

    render(<GravadorDeVoz aoEnviar={vi.fn()} ocupado={false} />);

    fireEvent.click(screen.getByRole("button", { name: /gravar mensagem de voz/i }));
    await screen.findByRole("button", { name: /parar/i });

    fireEvent.click(screen.getByRole("button", { name: /^parar$/i }));
    // Ouvir antes de mandar: recado de voz não tem como ser corrigido
    // depois de enviado.
    await waitFor(() => expect(screen.getByText(/ouça antes de enviar/i)).toBeTruthy());
    expect(screen.getByRole("button", { name: /enviar voz/i })).toBeTruthy();
  });

  it("34. cancelar não envia nada e solta o microfone", async () => {
    const { pararTrilha } = instalarGravadorFalso();
    const aoEnviar = vi.fn();

    render(<GravadorDeVoz aoEnviar={aoEnviar} ocupado={false} />);
    fireEvent.click(screen.getByRole("button", { name: /gravar mensagem de voz/i }));
    await screen.findByRole("button", { name: /cancelar/i });

    fireEvent.click(screen.getByRole("button", { name: /cancelar/i }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /gravar mensagem de voz/i })).toBeTruthy(),
    );
    expect(aoEnviar).not.toHaveBeenCalled();
    // A luz do microfone não pode ficar acesa depois de cancelar.
    expect(pararTrilha).toHaveBeenCalled();
  });

  it("33. microfone negado mostra aviso claro e não quebra a conversa", async () => {
    instalarGravadorFalso();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });

    render(<GravadorDeVoz aoEnviar={vi.fn()} ocupado={false} />);
    fireEvent.click(screen.getByRole("button", { name: /gravar mensagem de voz/i }));

    const aviso = await screen.findByRole("alert");
    expect(aviso.textContent).toMatch(/não foi possível acessar o microfone/i);
    expect(aviso.textContent).toMatch(/permissão do navegador/i);
    // O botão continua lá: dá para tentar de novo depois de liberar.
    expect(screen.getByRole("button", { name: /gravar mensagem de voz/i })).toBeTruthy();
  });

  it("navegador sem MediaRecorder avisa em vez de estourar", async () => {
    vi.stubGlobal("MediaRecorder", undefined);
    render(<GravadorDeVoz aoEnviar={vi.fn()} ocupado={false} />);
    fireEvent.click(screen.getByRole("button", { name: /gravar mensagem de voz/i }));

    const aviso = await screen.findByRole("alert");
    expect(aviso.textContent).toMatch(/não grava áudio/i);
  });

  it("envia com nome e duração medidos pelo gravador", async () => {
    instalarGravadorFalso();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:falso"),
      revokeObjectURL: vi.fn(),
    });
    const aoEnviar = vi.fn();

    render(<GravadorDeVoz aoEnviar={aoEnviar} ocupado={false} />);
    fireEvent.click(screen.getByRole("button", { name: /gravar mensagem de voz/i }));
    await screen.findByRole("button", { name: /^parar$/i });
    fireEvent.click(screen.getByRole("button", { name: /^parar$/i }));

    const enviar = await screen.findByRole("button", { name: /enviar voz/i });
    fireEvent.click(enviar);

    expect(aoEnviar).toHaveBeenCalledTimes(1);
    const g = aoEnviar.mock.calls[0]![0] as { nomeArquivo: string; durationMs: number };
    // A extensão acompanha o formato escolhido pelo navegador.
    expect(g.nomeArquivo).toBe("mensagem-de-voz.webm");
    expect(typeof g.durationMs).toBe("number");
  });
});
