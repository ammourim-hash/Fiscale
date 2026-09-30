"use client";

/**
 * Mídia dentro da conversa: imagem, documento e áudio.
 *
 * ---------------------------------------------------------------------
 *  Nada é carregado antes de ser preciso
 * ---------------------------------------------------------------------
 *  O histórico traz METADADO. Uma conversa com quarenta áudios não baixa
 *  quarenta arquivos ao abrir: a imagem usa `loading="lazy"`, e o áudio
 *  usa `preload="none"` — só busca bytes quando alguém aperta play.
 *
 *  A duração vem gravada no anexo justamente para isso: dá para escrever
 *  "0:37" sem ter baixado nada.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface Anexo {
  id: string;
  type: "IMAGE" | "DOCUMENT" | "AUDIO" | "VOICE" | "TEXT";
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  width: number | null;
  height: number | null;
  durationMs: number | null;
}

/** A única forma de chegar aos bytes: rota autenticada, nunca o storage. */
function urlDo(anexo: Anexo): string {
  return `/api/attachments/${anexo.id}`;
}

export function formatarTamanho(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB`;
  return `${(kb / 1024).toFixed(1).replace(".", ",")} MB`;
}

export function formatarDuracao(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "0:00";
  const total = Math.round(ms / 1000);
  const min = Math.floor(total / 60);
  const seg = total % 60;
  return `${min}:${String(seg).padStart(2, "0")}`;
}

/* ─── despachante ────────────────────────────────────────────────────── */

export function AnexoNaMensagem({ anexo }: { anexo: Anexo }) {
  if (anexo.type === "IMAGE") return <ImagemAnexada anexo={anexo} />;
  if (anexo.type === "AUDIO" || anexo.type === "VOICE") {
    return <PlayerDeAudio anexo={anexo} voz={anexo.type === "VOICE"} />;
  }
  return <DocumentoAnexado anexo={anexo} />;
}

/* ─── imagem ─────────────────────────────────────────────────────────── */

/**
 * Miniatura que abre em tamanho maior.
 *
 * Não há pipeline de thumbnail nesta fase: a miniatura é a própria imagem
 * limitada por CSS. Funciona para foto de documento tirada no celular e é
 * honesto sobre o custo — quando houver derivativos, eles entram como
 * anexo derivado, sem sobrescrever o original.
 *
 * `width`/`height` vão no elemento para o navegador reservar o espaço
 * antes de a imagem chegar: sem isso a conversa "pula" enquanto carrega.
 */
function ImagemAnexada({ anexo }: { anexo: Anexo }) {
  const [aberta, setAberta] = useState(false);
  const [falhou, setFalhou] = useState(false);

  if (falhou) {
    return (
      <p className="anexo-falha">
        Não foi possível carregar a imagem ({anexo.fileName}).
      </p>
    );
  }

  return (
    <>
      <button
        type="button"
        className="miniatura"
        onClick={() => setAberta(true)}
        aria-label={`Abrir imagem ${anexo.fileName}`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={urlDo(anexo)}
          alt={anexo.fileName}
          loading="lazy"
          decoding="async"
          {...(anexo.width ? { width: anexo.width } : {})}
          {...(anexo.height ? { height: anexo.height } : {})}
          onError={() => setFalhou(true)}
        />
      </button>

      {aberta ? (
        <VisorDeImagem anexo={anexo} aoFechar={() => setAberta(false)} />
      ) : null}
    </>
  );
}

function VisorDeImagem({ anexo, aoFechar }: { anexo: Anexo; aoFechar: () => void }) {
  const fecharRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    // Esc fecha, e o foco vai para o botão de fechar: sem isso, quem usa
    // teclado abre a imagem e fica preso atrás dela.
    const aoTeclar = (e: KeyboardEvent): void => {
      if (e.key === "Escape") aoFechar();
    };
    document.addEventListener("keydown", aoTeclar);
    fecharRef.current?.focus();
    return () => document.removeEventListener("keydown", aoTeclar);
  }, [aoFechar]);

  return (
    <div className="visor" role="dialog" aria-modal="true" aria-label={anexo.fileName}>
      <button type="button" className="visor-fundo" aria-hidden="true" tabIndex={-1} onClick={aoFechar} />
      <div className="visor-caixa">
        <div className="visor-topo">
          <span className="visor-nome">{anexo.fileName}</span>
          <a className="btn" href={urlDo(anexo)} download={anexo.fileName}>
            Baixar
          </a>
          <button type="button" className="btn" ref={fecharRef} onClick={aoFechar}>
            Fechar
          </button>
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={urlDo(anexo)} alt={anexo.fileName} className="visor-imagem" />
      </div>
    </div>
  );
}

/* ─── documento ──────────────────────────────────────────────────────── */

function DocumentoAnexado({ anexo }: { anexo: Anexo }) {
  const extensao = anexo.fileName.split(".").pop()?.toUpperCase() ?? "ARQUIVO";

  return (
    <a className="cartao-arquivo" href={urlDo(anexo)} download={anexo.fileName}>
      <span className="cartao-icone" aria-hidden="true">
        📄
      </span>
      <span className="cartao-texto">
        <span className="cartao-nome">{anexo.fileName}</span>
        <span className="cartao-sub">
          {extensao} · {formatarTamanho(anexo.sizeBytes)}
        </span>
      </span>
      <span className="cartao-acao">Baixar</span>
    </a>
  );
}

/* ─── áudio ──────────────────────────────────────────────────────────── */

const VELOCIDADES = [1, 1.5, 2] as const;

/**
 * Player próprio, simples.
 *
 * O `<audio controls>` do navegador seria mais barato e traria a barra
 * nativa, que muda de aparência em cada sistema e não tem controle de
 * velocidade em todos. Ouvir recado de cliente em 1,5× é o recurso que
 * mais economiza tempo num escritório — e é o que justifica o player.
 *
 * `preload="none"`: abrir a conversa não baixa áudio nenhum.
 */
function PlayerDeAudio({ anexo, voz }: { anexo: Anexo; voz: boolean }) {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [tocando, setTocando] = useState(false);
  const [posicao, setPosicao] = useState(0);
  const [duracao, setDuracao] = useState(
    anexo.durationMs != null ? anexo.durationMs / 1000 : 0,
  );
  const [velocidade, setVelocidade] = useState<number>(1);
  const [erro, setErro] = useState(false);

  const alternar = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    if (el.paused) {
      void el.play().catch(() => setErro(true));
    } else {
      el.pause();
    }
  }, []);

  const trocarVelocidade = useCallback(() => {
    const atual = VELOCIDADES.indexOf(velocidade as (typeof VELOCIDADES)[number]);
    const proxima = VELOCIDADES[(atual + 1) % VELOCIDADES.length]!;
    setVelocidade(proxima);
    if (ref.current) ref.current.playbackRate = proxima;
  }, [velocidade]);

  if (erro) {
    return <p className="anexo-falha">Não foi possível carregar o áudio.</p>;
  }

  return (
    <div className={`player ${voz ? "player-voz" : ""}`}>
      <audio
        ref={ref}
        src={urlDo(anexo)}
        preload="none"
        onPlay={() => setTocando(true)}
        onPause={() => setTocando(false)}
        onEnded={() => {
          setTocando(false);
          setPosicao(0);
        }}
        onTimeUpdate={(e) => setPosicao(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => {
          const d = e.currentTarget.duration;
          // Gravação do navegador às vezes reporta `Infinity` até o
          // arquivo ser percorrido; nesse caso vale a duração que o
          // gravador mediu e gravou no anexo.
          if (Number.isFinite(d) && d > 0) setDuracao(d);
          e.currentTarget.playbackRate = velocidade;
        }}
        onError={() => setErro(true)}
      />

      <button
        type="button"
        className="player-botao"
        onClick={alternar}
        aria-label={tocando ? "Pausar" : `Reproduzir ${voz ? "mensagem de voz" : anexo.fileName}`}
      >
        <span aria-hidden="true">{tocando ? "❚❚" : "▶"}</span>
      </button>

      <label className="player-barra">
        <span className="so-leitor">Posição do áudio</span>
        <input
          type="range"
          min={0}
          max={Math.max(duracao, 0.1)}
          step={0.1}
          value={posicao}
          onChange={(e) => {
            const v = Number(e.target.value);
            setPosicao(v);
            // O seek depende de o servidor responder Range; ver a rota
            // de download.
            if (ref.current) ref.current.currentTime = v;
          }}
        />
      </label>

      <span className="player-tempo" aria-hidden="true">
        {formatarDuracao(posicao * 1000)} / {formatarDuracao(duracao * 1000)}
      </span>

      <button
        type="button"
        className="player-velocidade"
        onClick={trocarVelocidade}
        aria-label={`Velocidade ${velocidade}x. Clique para mudar.`}
      >
        {velocidade}x
      </button>

      {voz ? null : <span className="player-nome">{anexo.fileName}</span>}
    </div>
  );
}
