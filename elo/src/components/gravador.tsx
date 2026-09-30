"use client";

/**
 * Gravação de mensagem de voz.
 *
 * ---------------------------------------------------------------------
 *  Formato: o navegador decide, não nós
 * ---------------------------------------------------------------------
 *  Nenhum navegador grava MP3 nativamente. Chrome e Edge produzem
 *  `audio/webm;codecs=opus`; o Safari, `audio/mp4` (AAC). Assumir um
 *  formato daria um gravador que funciona numa máquina e falha na outra
 *  sem explicação — por isso a escolha é feita com
 *  `MediaRecorder.isTypeSupported`, na ordem de preferência, e o servidor
 *  aceita os dois.
 *
 *  Não há transcodificação no servidor: converter para MP3 exigiria
 *  ffmpeg no processo web, e o ganho seria estético.
 *
 * ---------------------------------------------------------------------
 *  Microfone negado não quebra a conversa
 * ---------------------------------------------------------------------
 *  Quem nega a permissão vê uma frase clara e continua com o campo de
 *  texto intacto. O gravador é um recurso a mais, nunca um pré-requisito.
 *
 *  E a trilha do microfone é encerrada SEMPRE — no envio, no cancelamento
 *  e ao desmontar. Sem isso a luz do microfone fica acesa depois de a
 *  pessoa cancelar, que é a diferença entre um app em que se confia e um
 *  em que não se confia.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { formatarDuracao } from "./media";

/** Na ordem de preferência. Opus é menor e melhor para voz. */
const FORMATOS: { mime: string; extensao: string }[] = [
  { mime: "audio/webm;codecs=opus", extensao: "webm" },
  { mime: "audio/webm", extensao: "webm" },
  { mime: "audio/mp4", extensao: "m4a" },
  { mime: "audio/ogg;codecs=opus", extensao: "ogg" },
];

export function formatoSuportado(): { mime: string; extensao: string } | null {
  if (typeof MediaRecorder === "undefined") return null;
  for (const f of FORMATOS) {
    try {
      if (MediaRecorder.isTypeSupported(f.mime)) return f;
    } catch {
      /* navegador antigo: segue para o próximo */
    }
  }
  return null;
}

export interface GravacaoPronta {
  blob: Blob;
  nomeArquivo: string;
  durationMs: number;
}

type Estado = "parado" | "gravando" | "pausado" | "revisando";

export function GravadorDeVoz({
  aoEnviar,
  ocupado,
}: {
  aoEnviar: (g: GravacaoPronta) => void | Promise<void>;
  ocupado: boolean;
}) {
  const [estado, setEstado] = useState<Estado>("parado");
  const [segundos, setSegundos] = useState(0);
  const [aviso, setAviso] = useState<string | null>(null);
  const [previa, setPrevia] = useState<{ url: string; blob: Blob; ms: number } | null>(null);

  const gravador = useRef<MediaRecorder | null>(null);
  const trilha = useRef<MediaStream | null>(null);
  const pedacos = useRef<Blob[]>([]);
  const relogio = useRef<ReturnType<typeof setInterval> | null>(null);
  const formato = useRef<{ mime: string; extensao: string } | null>(null);

  /** Solta o microfone. Chamado em todo caminho de saída, sem exceção. */
  const soltarMicrofone = useCallback(() => {
    trilha.current?.getTracks().forEach((t) => t.stop());
    trilha.current = null;
    if (relogio.current) {
      clearInterval(relogio.current);
      relogio.current = null;
    }
  }, []);

  useEffect(() => soltarMicrofone, [soltarMicrofone]);

  const comecar = useCallback(async () => {
    setAviso(null);

    const f = formatoSuportado();
    if (!f) {
      setAviso("Este navegador não grava áudio. Você pode anexar um arquivo de áudio.");
      return;
    }
    formato.current = f;

    let entrada: MediaStream;
    try {
      entrada = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      // Negado, sem microfone, ou origem insegura. A pessoa precisa saber
      // o que fazer, e a conversa continua funcionando.
      setAviso(
        "Não foi possível acessar o microfone. Verifique a permissão do navegador.",
      );
      return;
    }

    trilha.current = entrada;
    pedacos.current = [];
    setSegundos(0);

    const mr = new MediaRecorder(entrada, { mimeType: f.mime });
    gravador.current = mr;
    mr.ondataavailable = (e) => {
      if (e.data.size > 0) pedacos.current.push(e.data);
    };
    mr.start();

    relogio.current = setInterval(() => setSegundos((s) => s + 1), 1000);
    setEstado("gravando");
  }, []);

  const parar = useCallback(
    (revisar: boolean) => {
      const mr = gravador.current;
      const duracaoMs = segundos * 1000;

      if (!mr || mr.state === "inactive") {
        soltarMicrofone();
        setEstado("parado");
        return;
      }

      mr.onstop = () => {
        soltarMicrofone();
        if (!revisar) {
          pedacos.current = [];
          setEstado("parado");
          setSegundos(0);
          return;
        }
        const tipo = formato.current?.mime.split(";")[0] ?? "audio/webm";
        const blob = new Blob(pedacos.current, { type: tipo });
        pedacos.current = [];
        setPrevia({ url: URL.createObjectURL(blob), blob, ms: duracaoMs });
        setEstado("revisando");
      };
      mr.stop();
    },
    [segundos, soltarMicrofone],
  );

  const descartar = useCallback(() => {
    if (previa) URL.revokeObjectURL(previa.url);
    setPrevia(null);
    setSegundos(0);
    setEstado("parado");
  }, [previa]);

  /* ── revisão: ouvir antes de mandar ─────────────────────────────── */

  if (estado === "revisando" && previa) {
    return (
      <div className="gravador gravador-revisao">
        <span className="gravador-rotulo">Ouça antes de enviar</span>
        {/* Prévia local, de um blob que ainda não subiu: aqui o
            `controls` nativo basta e não vale um player próprio. */}
        <audio src={previa.url} controls preload="metadata" />
        <span className="gravador-tempo">{formatarDuracao(previa.ms)}</span>
        <button type="button" className="btn" onClick={descartar} disabled={ocupado}>
          Descartar
        </button>
        <button
          type="button"
          className="btn btn-acao"
          disabled={ocupado}
          onClick={() => {
            const extensao = formato.current?.extensao ?? "webm";
            void aoEnviar({
              blob: previa.blob,
              nomeArquivo: `mensagem-de-voz.${extensao}`,
              durationMs: previa.ms,
            });
            URL.revokeObjectURL(previa.url);
            setPrevia(null);
            setSegundos(0);
            setEstado("parado");
          }}
        >
          Enviar voz
        </button>
      </div>
    );
  }

  /* ── gravando ───────────────────────────────────────────────────── */

  if (estado === "gravando" || estado === "pausado") {
    return (
      <div className="gravador gravador-ativo">
        <span className="gravador-ponto" aria-hidden="true" />
        <span className="gravador-tempo" role="timer" aria-live="off">
          {formatarDuracao(segundos * 1000)}
        </span>

        <button
          type="button"
          className="btn"
          onClick={() => {
            const mr = gravador.current;
            if (!mr) return;
            // `pause`/`resume` não existem em todo navegador; só se
            // oferece o botão quando o próprio gravador confirma que sabe.
            if (estado === "gravando" && typeof mr.pause === "function") {
              mr.pause();
              if (relogio.current) clearInterval(relogio.current);
              relogio.current = null;
              setEstado("pausado");
            } else if (typeof mr.resume === "function") {
              mr.resume();
              relogio.current = setInterval(() => setSegundos((s) => s + 1), 1000);
              setEstado("gravando");
            }
          }}
        >
          {estado === "gravando" ? "Pausar" : "Continuar"}
        </button>

        <button type="button" className="btn" onClick={() => parar(false)}>
          Cancelar
        </button>
        <button type="button" className="btn btn-acao" onClick={() => parar(true)}>
          Parar
        </button>
      </div>
    );
  }

  /* ── parado ─────────────────────────────────────────────────────── */

  return (
    <div className="gravador">
      <button
        type="button"
        className="btn"
        onClick={() => void comecar()}
        disabled={ocupado}
        aria-label="Gravar mensagem de voz"
      >
        <span aria-hidden="true">🎤</span> Gravar voz
      </button>
      {aviso ? (
        <p className="gravador-aviso" role="alert">
          {aviso}
        </p>
      ) : null}
    </div>
  );
}
