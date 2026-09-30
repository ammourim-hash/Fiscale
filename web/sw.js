/* FISCALE — service worker do aplicativo (portal, Fase 3 — 13/09/2026).
 *
 * A LIÇÃO QUE ESTE ARQUIVO NÃO PODE ESQUECER
 *   O SW antigo guardava as PÁGINAS num cache e, depois de atualizar o sistema,
 *   continuava mostrando as telas antigas. Ele foi trocado por um SW que só se
 *   desativava. Este aqui volta a existir porque o aplicativo instalado precisa
 *   de um — e por isso obedece a três regras:
 *
 *   1. NENHUMA página, script, estilo ou resposta de /api vai para cache.
 *      Tudo continua vindo do servidor, que já manda "no-store". Atualizar o
 *      FISCALE continua sendo só atualizar o servidor.
 *   2. O único conteúdo guardado é a tela "sem conexão" (com o símbolo
 *      embutido nela) — nada que fale de pessoa, empresa, sessão ou documento.
 *   3. Só navegações GET da própria origem passam por aqui, e só para trocar
 *      um erro de rede pela tela "sem conexão". Login, logout, formulários,
 *      downloads e o bilhete do ELO seguem direto, sem SW no meio.
 *
 * Cookie de sessão, token e senha nunca passam por este arquivo: o cookie é
 * HttpOnly e o navegador o anexa sozinho à requisição que segue para a rede.
 */
// v2: o ícone saiu da lista (passou a vir embutido na tela). Trocar o nome
// faz o `activate` apagar o cache v1 dos aparelhos que já o tinham.
const CACHE = 'fiscale-offline-v2';
const OFFLINE = '/offline.html';
const GUARDAR = [OFFLINE];

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await c.addAll(GUARDAR.map((u) => new Request(u, { cache: 'reload' })));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    // Apaga QUALQUER outro cache — inclusive os que o SW antigo deixou.
    for (const nome of await caches.keys()) {
      if (nome !== CACHE) await caches.delete(nome);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const r = e.request;
  if (r.method !== 'GET' || r.mode !== 'navigate') return;          // regra 3
  if (new URL(r.url).origin !== self.location.origin) return;
  e.respondWith((async () => {
    try {
      return await fetch(r);                                          // regra 1
    } catch (_) {
      const off = await caches.match(OFFLINE);
      return off || new Response('FISCALE sem conexão com o servidor.',
        { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
    }
  })());
});
