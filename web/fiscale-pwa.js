/* FISCALE — aplicativo instalável (portal, Fase 3 — 13/09/2026).
 *
 * Duas coisas, e só:
 *   1. registra o service worker (web/sw.js), que NÃO guarda páginas;
 *   2. mostra "Instalar FISCALE" quando — e somente quando — o navegador
 *      avisa que a instalação é possível (Chrome e Edge, em HTTPS ou no
 *      próprio computador). Em qualquer outro caso o botão nem aparece.
 *
 * Nada de sessão, token ou senha passa por aqui.
 *
 * Uso: qualquer elemento com `data-instalar-fiscale` vira o botão. Ele nasce
 * escondido (atributo `hidden`) e é revelado por este arquivo.
 */
(function () {
  var pedido = null;

  function botoes() { return document.querySelectorAll('[data-instalar-fiscale]'); }
  function mostrar(sim) { botoes().forEach(function (b) { b.hidden = !sim; }); }

  // Já aberto como aplicativo: não há o que instalar.
  function emJanelaPropria() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)
        || window.navigator.standalone === true;
  }
  if (emJanelaPropria()) document.documentElement.classList.add('fiscale-app');

  // Service worker só existe em contexto seguro (HTTPS, ou localhost). Pela
  // rede do escritório em HTTP o navegador recusa — e está tudo bem: o
  // sistema funciona igual, só não fica instalável.
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {});
    });
  }

  window.addEventListener('beforeinstallprompt', function (ev) {
    ev.preventDefault();              // a instalação é oferecida pelo nosso botão, não por um balão
    pedido = ev;
    if (!emJanelaPropria()) mostrar(true);
  });

  window.addEventListener('appinstalled', function () {
    pedido = null;
    mostrar(false);
  });

  async function instalar() {
    if (!pedido) return;
    var p = pedido;
    pedido = null;
    mostrar(false);
    // O pedido do navegador só serve uma vez. Se a pessoa recusar, o botão
    // volta quando o navegador oferecer de novo (novo `beforeinstallprompt`).
    try {
      p.prompt();
      await p.userChoice;
    } catch (e) { /* o navegador recusou mostrar; nada a fazer */ }
  }

  document.addEventListener('click', function (ev) {
    var alvo = ev.target.closest && ev.target.closest('[data-instalar-fiscale]');
    if (alvo) { ev.preventDefault(); instalar(); }
  });

  window.FiscaleApp = { instalar: instalar, emJanelaPropria: emJanelaPropria };
})();
