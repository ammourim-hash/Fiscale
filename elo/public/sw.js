/**
 * Service worker do Elo.
 *
 * =====================================================================
 *  O QUE ESTE ARQUIVO FAZ — e a lista é curta de propósito
 * =====================================================================
 *    1. receber push e mostrar a notificação;
 *    2. abrir/focar o Elo quando alguém clica nela;
 *    3. manter o crachá do ícone quando o sistema suporta;
 *    4. servir uma página de "sem conexão" quando a rede não responde.
 *
 * =====================================================================
 *  O QUE ELE NÃO FAZ, E POR QUÊ
 * =====================================================================
 *  NÃO transforma o Elo num aplicativo offline. Um service worker que
 *  guarda respostas por padrão acaba guardando conversa, cliente, anexo e
 *  documento fiscal — em disco, fora do banco, sem RLS, sem expiração e
 *  sem ninguém saber. Numa máquina compartilhada do escritório isso é o
 *  histórico de atendimento de um cliente disponível para a pessoa
 *  seguinte, mesmo depois do logout.
 *
 *  A regra, escrita como código logo abaixo: **nada sob /api é tocado, e
 *  nenhuma resposta autenticada entra em cache.** O que se guarda é a
 *  casca: os ícones, o manifesto e a página de erro de conexão — três
 *  arquivos que não contam nada sobre ninguém.
 *
 * =====================================================================
 *  ATUALIZAÇÃO
 * =====================================================================
 *  `VERSAO` muda a cada alteração deste arquivo. O navegador percebe o
 *  byte diferente, instala o novo em segundo plano e avisa a aba — que
 *  mostra "Uma nova versão do ELO está disponível". Só quando a pessoa
 *  clica é que o `skipWaiting` acontece.
 *
 *  Nada de `skipWaiting()` automático no `install`: trocar o worker
 *  debaixo de uma aba que está com a conversa aberta pode deixar código
 *  novo conversando com uma tela velha. Quem decide a hora é quem está
 *  usando.
 */

const VERSAO = "elo-v1";
const CACHE_CASCA = `${VERSAO}-casca`;

/**
 * A casca. Só isto.
 *
 * Nenhuma rota de dado, nenhuma página de atendimento, nenhum anexo.
 */
const CASCA = [
  "/offline",
  "/manifest.webmanifest",
  "/icones/icone-192.png",
  "/icones/icone-512.png",
  "/icones/badge-96.png",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches.open(CACHE_CASCA).then((cache) =>
      // `addAll` falha inteiro se um arquivo faltar, e aí o worker antigo
      // continua no ar — que é o comportamento certo: melhor a versão
      // anterior do que uma instalação pela metade.
      cache.addAll(CASCA),
    ),
  );
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    (async () => {
      // Cache de versão anterior é lixo: some junto com a troca.
      const nomes = await caches.keys();
      await Promise.all(
        nomes.filter((n) => n.startsWith("elo-") && !n.startsWith(VERSAO)).map((n) => caches.delete(n)),
      );
      await self.clients.claim();
    })(),
  );
});

/** A aba pede a troca depois de a pessoa clicar em "Atualizar". */
self.addEventListener("message", (evento) => {
  if (evento.data && evento.data.tipo === "ATUALIZAR_AGORA") {
    self.skipWaiting();
  }
  if (evento.data && evento.data.tipo === "CRACHA") {
    aplicarCracha(evento.data.valor);
  }
});

/* ─────────────────────────────────────────────────────────────────────
 *  Rede
 * ──────────────────────────────────────────────────────────────────── */

self.addEventListener("fetch", (evento) => {
  const req = evento.request;

  // Só GET. POST/PATCH/DELETE nunca passam por cache — e um worker que
  // "ajuda" com escrita é como se reenvia mensagem sem querer.
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Outra origem não é assunto nosso.
  if (url.origin !== self.location.origin) return;

  // ---------------------------------------------------------------
  //  A LINHA QUE NÃO SE ATRAVESSA
  //
  //  /api é dado autenticado: mensagem, cliente, anexo, sessão. Nada
  //  disso encosta em cache, em nenhuma circunstância. O `return` sem
  //  `respondWith` devolve a requisição ao navegador, que a faz como se
  //  o service worker não existisse.
  // ---------------------------------------------------------------
  if (url.pathname.startsWith("/api/")) return;

  // A casca: cache primeiro, porque são arquivos imutáveis por versão.
  if (CASCA.includes(url.pathname) || url.pathname.startsWith("/icones/")) {
    evento.respondWith(
      caches.match(req).then((guardado) => guardado || fetch(req)),
    );
    return;
  }

  // Navegação (a pessoa abriu uma página): rede primeiro, SEMPRE. O que
  // aparece na tela é o que o servidor respondeu agora — nunca uma cópia
  // de ontem apresentada como se fosse atual.
  if (req.mode === "navigate") {
    evento.respondWith(
      fetch(req).catch(async () => {
        const casca = await caches.open(CACHE_CASCA);
        const offline = await casca.match("/offline");
        return (
          offline ||
          new Response("ELO está sem conexão.", {
            status: 503,
            headers: { "content-type": "text/plain; charset=utf-8" },
          })
        );
      }),
    );
    return;
  }

  // Todo o resto (JS, CSS, fontes do próprio domínio): o navegador
  // resolve. O Next já versiona esses arquivos pelo nome, e duplicar
  // esse controle aqui só criaria uma segunda política para manter.
});

/* ─────────────────────────────────────────────────────────────────────
 *  Push
 * ──────────────────────────────────────────────────────────────────── */

self.addEventListener("push", (evento) => {
  evento.waitUntil(mostrar(evento));
});

async function mostrar(evento) {
  let aviso = null;
  try {
    aviso = evento.data ? evento.data.json() : null;
  } catch {
    aviso = null;
  }

  // Push sem payload legível ainda precisa virar notificação: em vários
  // navegadores, receber um push e NÃO notificar faz o sistema revogar a
  // permissão do site. O texto genérico é o preço de continuar existindo.
  const titulo = (aviso && aviso.title) || "ELO";
  const corpo = (aviso && aviso.body) || "Você recebeu um novo aviso.";
  const tag = (aviso && aviso.tag) || "elo";

  await self.registration.showNotification(titulo, {
    body: corpo,
    tag,
    // `renotify` faz o aparelho avisar de novo quando o aviso é
    // SUBSTITUÍDO — sem isso, a segunda mensagem do mesmo cliente trocaria
    // o texto em silêncio e ninguém perceberia.
    renotify: true,
    icon: "/icones/icone-192.png",
    badge: "/icones/badge-96.png",
    timestamp: aviso && aviso.at ? Date.parse(aviso.at) : Date.now(),
    // O destino do clique. NÃO é credencial: ao abrir, sessão, tenant,
    // RBAC e RLS continuam obrigatórios do lado do servidor.
    data: { c: aviso && aviso.c ? aviso.c : null, t: aviso && aviso.t },
    // Nada de `requireInteraction`: aviso de atendimento não fica preso na
    // tela até alguém dispensar. Isso é para alarme, e não existe push
    // crítico neste sistema.
  });

  await recalcularCracha();
}

/* ─────────────────────────────────────────────────────────────────────
 *  Clique
 * ──────────────────────────────────────────────────────────────────── */

self.addEventListener("notificationclick", (evento) => {
  evento.notification.close();
  evento.waitUntil(abrir(evento.notification.data));
});

async function abrir(dados) {
  const destino = dados && dados.c ? `/atendimentos?abrir=${dados.c}` : "/atendimentos";
  const alvo = new URL(destino, self.location.origin).href;

  const janelas = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });

  // Já há uma janela do Elo: foca ELA e manda navegar. Abrir uma segunda
  // aba do mesmo sistema é o defeito clássico — a pessoa acaba com seis
  // abas do Elo e responde na errada.
  for (const janela of janelas) {
    if (new URL(janela.url).origin !== self.location.origin) continue;
    await janela.focus();
    if ("navigate" in janela) {
      try {
        await janela.navigate(alvo);
        return;
      } catch {
        // Alguns navegadores recusam `navigate` de outra origem de
        // navegação; a mensagem abaixo faz a própria aba se virar.
      }
    }
    janela.postMessage({ tipo: "ABRIR_ATENDIMENTO", conversationId: dados && dados.c });
    return;
  }

  // Elo fechado. A URL leva o destino; se a sessão tiver expirado, o
  // servidor manda para /entrar e o destino é preservado lá — ver
  // src/app/entrar/page.tsx.
  await self.clients.openWindow(alvo);
}

/* ─────────────────────────────────────────────────────────────────────
 *  Crachá do ícone
 * ──────────────────────────────────────────────────────────────────── */

/**
 * O número no ícone do aplicativo instalado.
 *
 * A API só existe no Chrome/Edge instalados; onde não existe, o `if` faz
 * a coisa certa — nada. O crachá DENTRO do Elo (ao lado de "Atendimentos")
 * não depende disto e continua funcionando em qualquer navegador.
 *
 * O número vem do servidor, não de uma contagem local de notificações:
 * duas notificações do mesmo atendimento não são dois não lidos, e um
 * aviso que a pessoa já leu em outro aparelho não é nenhum.
 */
function aplicarCracha(valor) {
  if (!("setAppBadge" in self.navigator)) return;
  if (typeof valor === "number" && valor > 0) {
    self.navigator.setAppBadge(valor).catch(() => {});
  } else {
    self.navigator.clearAppBadge().catch(() => {});
  }
}

async function recalcularCracha() {
  if (!("setAppBadge" in self.navigator)) return;
  try {
    // A rota exige sessão. Sem sessão válida (`401`), o crachá é limpo —
    // e não estimado a partir das notificações abertas.
    const r = await fetch("/api/notifications/badge", {
      credentials: "same-origin",
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!r.ok) {
      aplicarCracha(0);
      return;
    }
    const d = await r.json();
    aplicarCracha(d.naoLidos);
  } catch {
    // Sem rede não há número certo, e um número errado é pior que nenhum.
  }
}

/**
 * A inscrição foi trocada pelo navegador (acontece sozinho, de tempos em
 * tempos). Sem tratar isto, o aparelho para de receber e ninguém descobre
 * por que — é o defeito de push mais difícil de diagnosticar.
 */
self.addEventListener("pushsubscriptionchange", (evento) => {
  evento.waitUntil(reinscrever(evento));
});

async function reinscrever(evento) {
  try {
    const antiga = evento.oldSubscription || (await self.registration.pushManager.getSubscription());
    const chave =
      (evento.newSubscription && evento.newSubscription.options.applicationServerKey) ||
      (antiga && antiga.options && antiga.options.applicationServerKey);
    if (!chave) return;

    const nova =
      evento.newSubscription ||
      (await self.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: chave,
      }));

    const j = nova.toJSON();
    await fetch("/api/notifications/subscription", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        endpoint: nova.endpoint,
        p256dh: j.keys.p256dh,
        auth: j.keys.auth,
      }),
    });
  } catch {
    // Sem sessão válida não dá para reinscrever daqui. A tela de
    // Configurações mostra o estado real na próxima vez que alguém abrir.
  }
}
