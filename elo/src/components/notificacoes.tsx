"use client";

/**
 * O lado do navegador: service worker, permissão, inscrição, som e crachá.
 *
 * =====================================================================
 *  A PERMISSÃO SÓ É PEDIDA DEPOIS DE UM CLIQUE
 * =====================================================================
 *  Nada neste arquivo chama `Notification.requestPermission()` sozinho. O
 *  navegador que pede permissão no primeiro segundo recebe "Bloquear" —
 *  e "Bloquear" no Chrome é uma decisão que a pessoa comum não sabe
 *  desfazer. Um único diálogo mal colocado tira o recurso do escritório
 *  inteiro, para sempre, sem que ninguém entenda por quê.
 *
 *  Então: o registro do service worker acontece sozinho (é invisível e não
 *  pede nada), e a permissão só quando alguém clica em "Ativar
 *  notificações", numa tela que explica o que vai acontecer.
 */
import { useCallback, useEffect, useRef, useState } from "react";

/* ─── registro do service worker ─────────────────────────────────────── */

export type EstadoAtualizacao = "atual" | "disponivel";

export function useServiceWorker(): {
  pronto: boolean;
  atualizacao: EstadoAtualizacao;
  aplicarAtualizacao: () => void;
} {
  const [pronto, setPronto] = useState(false);
  const [atualizacao, setAtualizacao] = useState<EstadoAtualizacao>("atual");
  const esperando = useRef<ServiceWorker | null>(null);

  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;

    let vivo = true;

    // O `try` cobre o caso em que `serviceWorker` existe mas `register`
    // não é chamável — acontece em navegador com a API desligada por
    // política e em ambiente de teste. Uma exceção aqui derrubaria a
    // casca inteira por causa de um recurso acessório.
    let registro: Promise<ServiceWorkerRegistration>;
    try {
      registro = navigator.serviceWorker.register("/sw.js", { scope: "/" });
    } catch {
      return;
    }

    void registro
      .then((reg) => {
        if (!vivo) return;
        setPronto(true);

        // Já há uma versão nova instalada e esperando (a pessoa fechou a
        // aba antes de atualizar da última vez).
        if (reg.waiting) {
          esperando.current = reg.waiting;
          setAtualizacao("disponivel");
        }

        reg.addEventListener("updatefound", () => {
          const novo = reg.installing;
          if (!novo) return;
          novo.addEventListener("statechange", () => {
            // `installed` + já haver um controlador = É uma ATUALIZAÇÃO,
            // não a primeira instalação. Sem essa segunda condição, todo
            // primeiro acesso mostraria "nova versão disponível".
            if (novo.state === "installed" && navigator.serviceWorker.controller) {
              esperando.current = novo;
              setAtualizacao("disponivel");
            }
          });
        });
      })
      .catch(() => {
        // Service worker exige HTTPS (ou localhost). Num ambiente sem ele
        // o Elo continua inteiro: perde push e página offline, e nada mais.
      });

    // Trocou o worker: a página precisa recarregar para falar com o novo.
    let recarregando = false;
    const aoTrocar = () => {
      if (recarregando) return;
      recarregando = true;
      window.location.reload();
    };
    navigator.serviceWorker.addEventListener("controllerchange", aoTrocar);

    return () => {
      vivo = false;
      navigator.serviceWorker.removeEventListener("controllerchange", aoTrocar);
    };
  }, []);

  const aplicarAtualizacao = useCallback(() => {
    esperando.current?.postMessage({ tipo: "ATUALIZAR_AGORA" });
  }, []);

  return { pronto, atualizacao, aplicarAtualizacao };
}

/* ─── permissão e inscrição ──────────────────────────────────────────── */

export type EstadoPermissao =
  /** O navegador não tem notificação, ou não estamos em contexto seguro. */
  | "indisponivel"
  /** Nunca perguntamos. É o estado em que o botão faz sentido. */
  | "nao-configurada"
  | "permitida"
  /** A pessoa (ou o sistema) bloqueou. Não adianta pedir de novo. */
  | "bloqueada";

export function permissaoAtual(): EstadoPermissao {
  if (typeof window === "undefined") return "indisponivel";
  if (!("Notification" in window) || !("serviceWorker" in navigator)) return "indisponivel";
  if (!("PushManager" in window)) return "indisponivel";

  switch (Notification.permission) {
    case "granted":
      return "permitida";
    case "denied":
      return "bloqueada";
    default:
      return "nao-configurada";
  }
}

/**
 * base64url → bytes, que é o que `applicationServerKey` exige.
 *
 * O `ArrayBuffer` explícito não é firula de tipos: o `Uint8Array` genérico
 * do TypeScript pode estar sobre um `SharedArrayBuffer`, que a API de push
 * não aceita. Alocar o buffer primeiro deixa isso resolvido no tipo.
 */
function chaveParaBytes(base64url: string): ArrayBuffer {
  const preenchido = base64url.padEnd(base64url.length + ((4 - (base64url.length % 4)) % 4), "=");
  const bruto = atob(preenchido.replace(/-/g, "+").replace(/_/g, "/"));
  const buffer = new ArrayBuffer(bruto.length);
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bruto.length; i += 1) bytes[i] = bruto.charCodeAt(i);
  return buffer;
}

export type ResultadoAtivacao =
  | { ok: true; endpointHash: string }
  | { ok: false; motivo: "INDISPONIVEL" | "NEGADA" | "SEM_CHAVE" | "FALHOU" };

/** SHA-256 em hex — o mesmo hash que o servidor calcula do endpoint. */
export async function hashDoEndpoint(endpoint: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * O caminho inteiro, do clique à inscrição gravada.
 *
 * A ordem importa: pedir permissão ANTES de inscrever. `subscribe()` sem
 * permissão dispara o diálogo do navegador por conta própria, num momento
 * que não escolhemos — e sem a explicação que a tela acabou de dar.
 *
 * ---------------------------------------------------------------------
 *  O aparelho pode trocar de dono, e quem resolve isso é o NAVEGADOR
 * ---------------------------------------------------------------------
 *  A inscrição de push pertence ao navegador, não à pessoa. Um computador
 *  compartilhado no escritório, com um perfil do Chrome só, produz o MESMO
 *  endpoint para quem quer que entre nele — e sem tratar isso o aviso
 *  "Empresa ABC entrou em contato" tocaria no aparelho enquanto quem está
 *  ali é outra pessoa.
 *
 *  A saída é `unsubscribe()`: ele mata o endpoint NO PROVEDOR. Qualquer
 *  linha antiga apontando para ele — deste escritório ou de outro — passa
 *  a receber 410 na primeira tentativa e é revogada sozinha. Isso resolve
 *  o caso inteiro, inclusive entre tenants, coisa que uma limpeza no nosso
 *  banco não conseguiria fazer sem furar o RLS.
 *
 * @param hashesMeus os aparelhos que o servidor diz serem DESTA pessoa.
 *   Uma inscrição existente que não esteja nessa lista é de outra pessoa
 *   (ou de um par VAPID antigo), e precisa morrer antes.
 */
export async function ativarNotificacoes(
  vapidPublicKey: string,
  hashesMeus: readonly string[] = [],
): Promise<ResultadoAtivacao> {
  if (permissaoAtual() === "indisponivel") return { ok: false, motivo: "INDISPONIVEL" };
  if (!vapidPublicKey) return { ok: false, motivo: "SEM_CHAVE" };

  const permissao = await Notification.requestPermission();
  if (permissao !== "granted") return { ok: false, motivo: "NEGADA" };

  try {
    const reg = await navigator.serviceWorker.ready;

    let existente = await reg.pushManager.getSubscription();

    if (existente) {
      const hash = await hashDoEndpoint(existente.endpoint);
      if (!hashesMeus.includes(hash)) {
        // Não é minha. Ver o cabeçalho: matar o endpoint no provedor é o
        // que impede o dono anterior de continuar recebendo aqui.
        await existente.unsubscribe();
        existente = null;
      }
    }

    // Reaproveita a própria inscrição quando ela já é desta pessoa: chamar
    // `subscribe` com uma chave diferente da anterior dá erro — e é
    // exatamente o que acontece quando alguém troca o par VAPID.
    const inscricao =
      existente ??
      (await reg.pushManager.subscribe({
        // Obrigatório nos navegadores atuais: promete que todo push vira
        // uma notificação visível. É o contrário de push silencioso — e é
        // por isso que o service worker sempre mostra algo.
        userVisibleOnly: true,
        applicationServerKey: chaveParaBytes(vapidPublicKey),
      }));

    const dados = inscricao.toJSON();
    if (!dados.keys?.p256dh || !dados.keys?.auth) return { ok: false, motivo: "FALHOU" };

    const r = await fetch("/api/notifications/subscription", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        endpoint: inscricao.endpoint,
        p256dh: dados.keys.p256dh,
        auth: dados.keys.auth,
      }),
    });
    if (!r.ok) return { ok: false, motivo: "FALHOU" };

    const corpo = (await r.json()) as { dispositivo: { endpointHash: string } };
    guardarEsteDispositivo(corpo.dispositivo.endpointHash);
    return { ok: true, endpointHash: corpo.dispositivo.endpointHash };
  } catch {
    return { ok: false, motivo: "FALHOU" };
  }
}

/**
 * "Desativar neste dispositivo".
 *
 * Desfaz nos dois lados: a inscrição do navegador e a linha do servidor.
 * Só um dos dois deixaria um estado impossível de explicar — servidor
 * mandando para um endpoint que o navegador esqueceu, ou navegador
 * inscrito sem ninguém para lhe mandar nada.
 */
export async function desativarNesteDispositivo(): Promise<boolean> {
  const hash = esteDispositivo();

  try {
    const reg = await navigator.serviceWorker.ready;
    const inscricao = await reg.pushManager.getSubscription();
    if (inscricao) await inscricao.unsubscribe();
  } catch {
    // O navegador pode já ter esquecido. Segue para o servidor: é lá que
    // a inscricão precisa morrer para o push parar.
  }

  if (!hash) return false;

  const r = await fetch("/api/notifications/subscription", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ endpointHash: hash }),
  });

  esquecerEsteDispositivo();
  return r.ok;
}

/* ─── "este dispositivo" ─────────────────────────────────────────────── */

/**
 * O hash da inscrição DESTE navegador, guardado localmente.
 *
 * É o que permite a tela dizer "Este dispositivo: notificações ativas" em
 * vez de listar aparelhos anônimos. Guardar o hash — e não o endpoint — é
 * a mesma regra do servidor: o endpoint é um segredo de capacidade, e
 * `localStorage` é lido por qualquer script da origem.
 */
const CHAVE_LOCAL = "elo.dispositivo";

export function esteDispositivo(): string | null {
  try {
    return window.localStorage.getItem(CHAVE_LOCAL);
  } catch {
    return null;
  }
}

function guardarEsteDispositivo(hash: string): void {
  try {
    window.localStorage.setItem(CHAVE_LOCAL, hash);
  } catch {
    /* modo privado, cota cheia: a tela só fica menos específica */
  }
}

function esquecerEsteDispositivo(): void {
  try {
    window.localStorage.removeItem(CHAVE_LOCAL);
  } catch {
    /* idem */
  }
}

/* ─── som ────────────────────────────────────────────────────────────── */

/**
 * Um som discreto, sintetizado — sem arquivo de áudio.
 *
 * Duas notas curtas, suaves, com queda rápida. A escolha de sintetizar em
 * vez de embarcar um `.mp3`: um arquivo teria que ser baixado, versionado
 * e cacheado, e o resultado seria o mesmo bipe. E `AudioContext` permite
 * ajustar volume sem depender de o arquivo ter sido masterizado direito.
 *
 * Nunca toca duas vezes seguidas em menos de um segundo. Cinco mensagens
 * chegando juntas fariam cinco bipes sobrepostos, que é como se ensina uma
 * equipe a desligar o som.
 */
let contexto: AudioContext | null = null;
let ultimoSomMs = 0;

export const INTERVALO_MINIMO_SOM_MS = 1000;

export function tocarAvisoDeMensagem(agoraMs = Date.now()): boolean {
  if (agoraMs - ultimoSomMs < INTERVALO_MINIMO_SOM_MS) return false;
  ultimoSomMs = agoraMs;

  try {
    const Ctx =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return false;

    contexto ??= new Ctx();
    // O navegador suspende o contexto até haver interação do usuário. Uma
    // sessão de trabalho sempre teve alguma, então isso resolve sozinho —
    // e falhar aqui não pode derrubar a tela.
    if (contexto.state === "suspended") void contexto.resume();

    const inicio = contexto.currentTime;
    for (const [atraso, frequencia] of [
      [0, 880],
      [0.12, 1174.7],
    ] as const) {
      const osc = contexto.createOscillator();
      const ganho = contexto.createGain();
      osc.type = "sine";
      osc.frequency.value = frequencia;
      // Ataque e queda curtos: sem eles o oscilador começa e termina com
      // um clique audível, que é o que faz um bipe soar barato.
      ganho.gain.setValueAtTime(0, inicio + atraso);
      ganho.gain.linearRampToValueAtTime(0.06, inicio + atraso + 0.01);
      ganho.gain.exponentialRampToValueAtTime(0.0001, inicio + atraso + 0.18);
      osc.connect(ganho).connect(contexto.destination);
      osc.start(inicio + atraso);
      osc.stop(inicio + atraso + 0.2);
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * Esquece a janela mínima e o contexto de áudio.
 *
 * Usado em dois lugares, e os dois precisam das DUAS coisas:
 *
 *   - ao marcar "som" nas Configurações, para a amostra tocar na hora em
 *     vez de esbarrar na janela de um segundo;
 *   - no teste, onde o contexto guardado de um caso anterior continuaria
 *     vivo e os osciladores iriam para o objeto errado — que foi
 *     exatamente o defeito que este comentário existe para lembrar.
 *
 * O contexto é FECHADO antes de ser esquecido: o navegador limita quantos
 * `AudioContext` uma página pode ter, e vazar um a cada clique numa
 * caixinha acabaria em silêncio inexplicável.
 */
export function reiniciarSom(): void {
  ultimoSomMs = 0;
  try {
    void contexto?.close?.();
  } catch {
    /* já fechado, ou um substituto de teste sem `close` */
  }
  contexto = null;
}

/* ─── crachá do ícone do aplicativo ──────────────────────────────────── */

/**
 * O número no ícone da PWA instalada.
 *
 * Só existe no Chrome/Edge instalados. Onde não existe, a função não faz
 * nada — e o crachá DENTRO do Elo, ao lado de "Atendimentos", continua
 * funcionando em qualquer navegador. É por isso que o de dentro é o
 * principal e este é o extra.
 */
export function aplicarCrachaDoApp(naoLidos: number): void {
  const nav = navigator as Navigator & {
    setAppBadge?: (n?: number) => Promise<void>;
    clearAppBadge?: () => Promise<void>;
  };
  try {
    if (naoLidos > 0) void nav.setAppBadge?.(naoLidos)?.catch(() => {});
    else void nav.clearAppBadge?.()?.catch(() => {});
  } catch {
    /* API ausente ou recusada: o crachá interno já cobre o caso */
  }
}
