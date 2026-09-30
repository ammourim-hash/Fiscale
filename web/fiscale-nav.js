/* Fiscale — barra de módulos + linguagem visual (balão e sobreposição)

   Este arquivo roda em TODAS as telas. Por isso ele faz duas coisas:

     1. desenha a barra lateral esquerda de módulos;
     2. injeta o tema de balão sobre os estilos que cada tela já tem.

   O tema entra por <style> no fim do <head>, depois do CSS embutido de cada
   página — assim vence no desempate por ordem, sem precisar de !important e sem
   reescrever as onze telas uma a uma. Só mexe em aparência (raio, sombra, cor,
   espaço); nada de layout estrutural, para não quebrar tela nenhuma.

   Barra lateral:
     • coluna fixa à esquerda (236px), sempre visível, com rótulo ao lado do
       ícone — a leitura vertical acompanha a lista, e sobra largura para
       nomes compridos que na horizontal precisavam sumir;
     • abaixo de 1180px encolhe para só os ícones (72px), sem esconder módulo
       nenhum: o que sai é o rótulo, não o destino;
     • submenu abre AO LADO do item, não embaixo;
     • marca o módulo atual e respeita prefers-reduced-motion.
*/
(function () {
  if (window.__fiscaleNav) return;           // não duplica se a página já tem
  window.__fiscaleNav = true;

  var MODULOS = [
    // "Início" não entra na lista: o logo à esquerda já leva para a Home.
    { href: 'clientes.html',         icone: '👥', nome: 'Clientes',           cor: '#10444E' , desc: 'As empresas do escritório — certificado, regime, IE e IM lidos do próprio documento.' },
    // Um módulo para os documentos fiscais, com as espécies em abas por
    // dentro. O NFS-e ainda tem entrada própria: ele é servido por outro
    // backend, e a mudança dele para cá é a Etapa B.
    { href: 'nfe_documentos.html',   icone: '📦', nome: 'Documentos Fiscais', cor: '#B45309' , desc: 'NFS-e, NF-e, NFC-e e CT-e — escolha a espécie no menu do módulo. Traz também a caixa de entrada de XML e o auditor.' ,
      sub: [
        { href: 'nfse.html',                        icone: '🧾', nome: 'NFS-e',  nota: 'serviços' },
        { href: 'nfe_documentos.html?especie=NFE55',icone: '📄', nome: 'NF-e',   nota: 'modelo 55' },
        { href: 'nfe_documentos.html?especie=NFCE65',icone: '🛒', nome: 'NFC-e', nota: 'modelo 65' },
        // Vai para o módulo do ACERVO, não para a aba de CT-e desta tela: a
        // aba lê a pasta legada, o módulo lê o acervo capturado da SEFAZ.
        { href: 'cte.html',                         icone: '🚚', nome: 'CT-e',   nota: 'modelo 57' },
        // Divisor: acima estao as ESPECIES de documento; abaixo, a tela de
        // ferramentas. Sem a separacao, ela leria como uma quinta especie.
        { divisor: true },
        { href: 'nfe.html',                         icone: '🔎', nome: 'Auditor de XML',
          nota: 'créditos por regime · conferência de NCM' }
      ] },
    { href: 'situacao.html',         icone: '🛡', nome: 'Situação Fiscal',    cor: '#0F8A5F' , desc: 'Certidões e extratos das três esferas — o que está válido, o que vence e o que falta.' },
    { href: 'cclasstrib.html',       icone: '🏷', nome: 'CClassTrib',         cor: '#1D4ED8' , desc: 'A classificação tributária da Reforma, para chegar antes da obrigatoriedade.' },
    { href: 'consulta_optantes.html',icone: '🔎', nome: 'Consulta Optantes',  cor: '#B07D10' , desc: 'Situação no Simples Nacional consultada em lote, direto na base pública.' },
    { href: 'central_fiscal.html',   icone: '📰', nome: 'Central Fiscal',     cor: '#0E7490' , desc: 'O que mudou no fiscal, com o link para conferir na fonte oficial.' }
  ];

  // Barra LATERAL flutuante: descolada das bordas, sobrepondo o conteúdo.
  var MARGEM = 12, LARGURA = 236, ESTREITA = 72;
  var FOLGA = MARGEM + LARGURA + 10;             // espaço que o corpo reserva à esquerda
  var FOLGA_ESTREITA = MARGEM + ESTREITA + 10;
  var atual = (location.pathname.split('/').pop() || 'home_portal.html').toLowerCase();

  var css = document.createElement('style');
  css.textContent = [
    /* ── BARRA LATERAL ────────────────────────────────────────────────── */
    '#fscNav{position:fixed;left:' + MARGEM + 'px;top:' + MARGEM + 'px;bottom:' + MARGEM + 'px;',
    '  width:' + LARGURA + 'px;box-sizing:border-box;z-index:900;border-radius:22px;',
    '  background:linear-gradient(165deg,#12454F 0%,#0C3038 68%,#0A2A31 100%);',
    '  color:#EAF2F3;display:flex;flex-direction:column;gap:10px;padding:14px 10px 12px;',
    '  border:1px solid rgba(255,255,255,.10);',
    '  box-shadow:0 2px 8px rgba(10,42,49,.16), 0 20px 46px -18px rgba(10,42,49,.50)}',

    '#fscNav .topo{display:flex;align-items:center;gap:10px;text-decoration:none;color:inherit;',
    '  flex:0 0 auto;padding:2px 4px 12px;',
    '  border-bottom:1px solid rgba(255,255,255,.12)}',
    '#fscNav .logo{flex:0 0 36px;width:36px;height:36px;display:block;border-radius:12px;',
    '  background:rgba(255,255,255,.10);padding:5px}',
    '#fscNav .marca{font-weight:800;font-size:15.5px;letter-spacing:.01em;white-space:nowrap}',

    /* Coluna que rola quando não cabe na altura da janela. */
    '#fscNav .itens{flex:1;display:flex;flex-direction:column;align-items:stretch;gap:3px;',
    '  overflow-y:auto;overflow-x:hidden;scrollbar-width:none;-ms-overflow-style:none}',
    '#fscNav .itens::-webkit-scrollbar{display:none}',

    /* Cada módulo é uma pílula com o ícone em bolha. */
    '#fscNav a.item{display:flex;align-items:center;gap:9px;padding:7px 10px 7px 7px;',
    '  color:#B9CFD4;text-decoration:none;font-size:12.5px;font-weight:700;',
    '  background:transparent;border:0;box-shadow:none;',
    '  border-radius:14px;flex:0 0 auto;transition:background .16s ease,color .16s ease}',
    /* Nem os filhos: `h2`, `p` e `small` de dentro de `.item` também são
       nomes que as telas usam. */
    '#fscNav a.item > *{background:transparent;border:0;box-shadow:none;margin:0}',
    /* O rótulo pode ter duas linhas: na vertical há largura para isso, e
       cortar "Consulta Optantes" com reticências seria perder informação
       que a coluna não precisa esconder. */
    '#fscNav a.item .tx{flex:1;min-width:0;line-height:1.25}',
    '#fscNav a.item .ic{flex:0 0 30px;width:30px;height:30px;border-radius:10px;',
    '  display:flex;align-items:center;justify-content:center;font-size:15px;line-height:1;',
    '  background:rgba(255,255,255,.07);transition:background .18s ease,box-shadow .18s ease}',
    '#fscNav a.item:hover{color:#fff;background:rgba(255,255,255,.07)}',
    '#fscNav a.item:hover .ic{background:rgba(255,255,255,.16)}',
    /* Ativo: pílula acesa + bolha na cor do módulo. */
    '#fscNav a.item.on{color:#fff;background:rgba(255,255,255,.12)}',
    '#fscNav a.item.on .ic{background:var(--c,#0E7490);',
    '  box-shadow:0 0 0 3px rgba(255,255,255,.10), 0 5px 14px -4px var(--c,#0E7490)}',
    /* Em telas apertadas some o RÓTULO — nenhum módulo sai da barra. */
    '@media (max-width:1180px){',
    '  #fscNav{width:' + ESTREITA + 'px}',
    '  #fscNav a.item .tx,#fscNav .marca,#fscNav a.item .seta{display:none}',
    '  #fscNav a.item{padding:6px;justify-content:center}',
    '  #fscNav .topo{justify-content:center;padding-left:0;padding-right:0}}',

    /* Seta do item que abre submenu. */
    '#fscNav a.item .seta{flex:0 0 auto;font-size:9px;opacity:.7;margin-left:1px;',
    '  transition:transform .16s ease}',
    '#fscNav a.item[aria-expanded="true"] .seta{transform:rotate(90deg);opacity:1}',

    /* PAINEL DO SUBMENU — `fixed`, pendurado no body.
       Dentro de `.itens` ele seria recortado: aquela fila tem overflow-x
       para poder rolar, e overflow recorta filho posicionado. */
    '#fscSub{position:fixed;z-index:960;min-width:212px;padding:6px;border-radius:16px;',
    '  background:linear-gradient(160deg,#12454F 0%,#0C3038 100%);',
    '  border:1px solid rgba(255,255,255,.12);',
    '  box-shadow:0 8px 20px rgba(10,42,49,.30), 0 24px 50px -18px rgba(10,42,49,.65);',
    '  display:none;flex-direction:column;gap:2px}',
    '#fscSub[data-aberto="1"]{display:flex}',
    '#fscSub a{display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:11px;',
    '  background:transparent;border:0;box-shadow:none;',
    '  color:#CFE3E6;text-decoration:none;font-size:13px;font-weight:700;white-space:nowrap}',
    '#fscSub a .ic{flex:0 0 26px;width:26px;height:26px;border-radius:9px;display:flex;',
    '  align-items:center;justify-content:center;font-size:14px;background:rgba(255,255,255,.08)}',
    '#fscSub a small{display:block;font-size:10.5px;font-weight:600;color:#7FA5AC;margin-top:1px}',
    '#fscSub a:hover,#fscSub a:focus-visible{background:rgba(255,255,255,.13);color:#fff;outline:none}',
    '#fscSub a:hover .ic{background:rgba(255,255,255,.2)}',
    '#fscSub .risco{height:1px;margin:5px 8px;background:rgba(255,255,255,.14)}',

    /* Sino da Central Fiscal: fica no fim da barra, sempre visível. */
    /* `margin-top:auto` prega o sino no pé da coluna: ele é o último
       recurso da barra, não mais um módulo no meio da lista. */
    '#fscSino{flex:0 0 auto;position:relative;display:flex;align-items:center;',
    '  justify-content:center;width:38px;height:38px;border-radius:12px;',
    '  background:rgba(255,255,255,.07);color:#EAF2F3;text-decoration:none;',
    '  font-size:17px;margin:auto 0 0 4px;transition:background .16s ease}',
    '#fscSino:hover{background:rgba(255,255,255,.18)}',
    '#fscSino .cont{position:absolute;top:-3px;right:-3px;min-width:18px;',
    '  height:18px;padding:0 5px;border-radius:999px;background:#B3382C;',
    '  color:#fff;font-size:11px;font-weight:800;display:none;',
    '  align-items:center;justify-content:center;line-height:1;',
    '  box-shadow:0 0 0 2px #12454F}',
    '#fscSino[data-tem="1"] .cont{display:flex}',

    'body{padding-left:' + FOLGA + 'px;padding-top:' + MARGEM + 'px}',
    /* DEPOIS da regra base, de propósito: mesma especificidade, e quem vem
       por último vence. Dentro da media query lá de cima esta linha perdia o
       desempate e o corpo reservava 258px para uma barra de 72. */
    '@media (max-width:1180px){body{padding-left:' + FOLGA_ESTREITA + 'px}}',

    /* ── CABEÇALHO DE MÓDULO — o mesmo em todas as telas ───────────────
       O modelo é o `.cabecalho` escuro que o `nfe_documentos` e o `cte` já
       usavam: é o que informa mais e o que combina com esta barra e com a
       Home. As outras telas recebem um igual, injetado abaixo. */
    'body > header{display:none!important}',      /* barra "< Home + nome": o logo daqui já leva à Home */
    '#barraFiscale{display:none!important}',      /* a mesma coisa, no módulo NFS-e */
    /* O MESMO GRADIENTE DA BARRA, e não um parecido.
           Ele nasceu `100deg` quando a barra era horizontal: numa faixa larga
           e baixa isso leva a parada escura até a borda direita, e o cartão
           lê mais pesado que a barra vertical ao lado. Mesmo ângulo e mesmas
           paradas resolvem — e passam a mudar juntos, que é o ponto. */
    'header.cabecalho,.mod-cabeca{',
    '  background:linear-gradient(165deg,#12454F 0%,#0C3038 68%,#0A2A31 100%)!important;',
    '  color:#EAF2F3!important;border:1px solid rgba(255,255,255,.09)!important;',
    '  border-radius:22px!important;padding:22px 26px!important;margin:0 0 16px!important;',
    '  box-shadow:0 2px 8px rgba(10,42,49,.14), 0 20px 44px -20px rgba(10,42,49,.55)!important;',
    '  display:block!important}',
    'header.cabecalho h1,.mod-cabeca h1{font-size:26px!important;font-weight:800!important;',
    '  letter-spacing:-.015em!important;color:#fff!important;margin:0!important;line-height:1.2!important}',
    'header.cabecalho .sub,.mod-cabeca p,header.cabecalho p{color:#A9C4C8!important;',
    '  font-size:13.5px!important;margin:5px 0 0!important;line-height:1.55!important}',
    /* O azulejo do NFS-e continua, mas alinhado ao fundo escuro. */
    '.mod-cabeca{display:flex!important;align-items:center!important;gap:16px!important}',
    '.mod-cabeca .tile{background:rgba(255,255,255,.12)!important;box-shadow:none!important}',
    /* Pílulas de contexto: legíveis sobre o escuro em qualquer tela. */
    'header.cabecalho .pilula{background:rgba(255,255,255,.10)!important;',
    '  border:1px solid rgba(255,255,255,.16)!important;color:#CFE3E6!important}',

    /* Uma largura só: a mesma janela deixa de mudar de tamanho por módulo. */
    'main,.wrap,.capa{max-width:1400px!important;margin-left:auto!important;',
    '  margin-right:auto!important}',

    /* ── TEMA BALÃO — vale para todas as telas ────────────────────────── */
    ':root{--raio:24px;--borda:#DDE4E7;--fundo:#EAEFF1;',
    '  --sombra:0 1px 2px rgba(18,33,42,.05), 0 12px 32px -10px rgba(18,33,42,.16)}',
    'body{background:var(--fundo)}',
    /* manchas de profundidade atrás de tudo */
    'body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;',
    '  background:radial-gradient(680px 420px at 12% -8%, rgba(16,68,78,.13), transparent 60%),',
    '   radial-gradient(520px 380px at 92% 4%, rgba(179,56,44,.07), transparent 62%),',
    '   radial-gradient(760px 520px at 62% 106%, rgba(15,138,95,.08), transparent 60%)}',

    /* painéis viram balões */
    '.painel,.card,.bloco{border-radius:var(--raio)!important;border-color:var(--borda)!important;',
    '  box-shadow:var(--sombra)!important}',
    '.painel > h2,.card > h2{border-bottom-color:var(--borda)!important}',

    /* controles em pílula */
    '.btn,button.btn{border-radius:999px!important;font-weight:800!important;transition:.18s}',
    '.btn-primario:hover,.btn-primary:hover{transform:translateY(-1px)}',
    'input:not([type=checkbox]):not([type=radio]),select,textarea{border-radius:13px!important;',
    '  border-color:var(--borda)!important}',
    'input:focus,select:focus,textarea:focus{outline:none!important;border-color:#10444E!important;',
    '  box-shadow:0 0 0 4px rgba(16,68,78,.10)!important}',

    /* cartões de número flutuam */
    '.stat{border-radius:18px!important;border-color:var(--borda)!important;',
    '  box-shadow:0 2px 6px rgba(18,33,42,.05), 0 16px 40px -14px rgba(18,33,42,.22)!important;',
    '  transition:transform .18s cubic-bezier(.22,.61,.36,1)}',
    '.stat:hover{transform:translateY(-3px)}',

    /* etiquetas e avisos arredondados */
    '.tag,.chip,.badge{border-radius:999px!important}',
    '.aviso,.alerta,.status{border-radius:16px!important}',

    '@media (prefers-reduced-motion:reduce){#fscNav,body,#fscNav *,.stat,.btn{',
    '  transition:none!important;animation:none!important;transform:none!important}}',
    '@media print{#fscNav{display:none!important}',
    '  body{padding-left:0!important;padding-top:0!important}',
    '  body::before{display:none!important}}'
  ].join('\n');
  document.head.appendChild(css);

  // A Home monta a grade de módulos a partir DESTA lista. Uma segunda
  // lista lá acabaria discordando desta no dia em que só uma mudasse.
  window.Fiscale = window.Fiscale || {};
  window.Fiscale.MODULOS = MODULOS;

  var nav = document.createElement('nav');
  nav.id = 'fscNav';
  nav.setAttribute('aria-label', 'Módulos do Fiscale');
  nav.innerHTML =
    '<a class="topo" href="home_portal.html" title="Início">' +
      '<img class="logo" src="fiscale-logo.svg" alt="Fiscale" width="38" height="38">' +
      '<div class="marca">Fiscale</div></a>' +
    '<div class="itens">' +
      MODULOS.map(function (m, i) {
        // O pai acende tambem quando a tela aberta e uma das FILHAS. Sem isto,
        // estar no NFS-e ou no CT-e nao acendia nada na barra -- e a pessoa
        // perde a referencia de onde esta.
        var aqui = (m.href.toLowerCase() === atual) ||
          (m.sub || []).some(function (f) {
            return f.href && f.href.toLowerCase().split('?')[0] === atual;
          });
        var on = aqui ? ' on' : '';
        var abre = m.sub ? ' aria-haspopup="true" aria-expanded="false" data-sub="' + i + '"' : '';
        return '<a class="item' + on + '" href="' + m.href + '" style="--c:' + m.cor + '" title="' + m.nome + '"' + abre + '>' +
               '<span class="ic">' + m.icone + '</span><span class="tx">' + m.nome + '</span>' +
               (m.sub ? '<span class="seta">▼</span>' : '') + '</a>';
      }).join('') +
    '</div>' +
    '<a id="fscSino" href="central_fiscal.html" title="Central Fiscal — o que mudou">' +
      '\u{1F4EC}<span class="cont">0</span></a>';
  document.body.appendChild(nav);

  /* ── SUBMENU ────────────────────────────────────────────────────────────
     "Documentos Fiscais" deixa de ser um destino e passa a ser uma escolha:
     NFS-e, NF-e, NFC-e ou CT-e. O clique no item PAI abre o painel em vez de
     navegar -- ir para uma das quatro e o que o usuário veio fazer, e abrir
     uma delas por acidente o obrigaria a voltar. */
  var painel = document.createElement('div');
  painel.id = 'fscSub';
  painel.setAttribute('data-aberto', '0');
  document.body.appendChild(painel);
  var donoAberto = null;

  function fecharSub() {
    painel.setAttribute('data-aberto', '0');
    if (donoAberto) donoAberto.setAttribute('aria-expanded', 'false');
    donoAberto = null;
  }

  function abrirSub(pai, itens) {
    painel.innerHTML = itens.map(function (f) {
      if (f.divisor) return '<div class="risco"></div>';
      return '<a href="' + f.href + '"><span class="ic">' + f.icone + '</span>' +
             '<span>' + f.nome + (f.nota ? '<small>' + f.nota + '</small>' : '') +
             '</span></a>';
    }).join('');
    painel.setAttribute('data-aberto', '1');
    pai.setAttribute('aria-expanded', 'true');
    donoAberto = pai;

    /* AO LADO DO ITEM, não embaixo. Numa coluna, abrir para baixo cobriria
       os módulos seguintes — que é justamente a lista que a pessoa está
       percorrendo quando abre o submenu. */
    var r = pai.getBoundingClientRect();
    var esq = r.right + 8;
    if (esq + painel.offsetWidth > window.innerWidth - 12)
      esq = Math.max(12, r.left - painel.offsetWidth - 8);   // não cabe: abre para dentro
    painel.style.left = esq + 'px';
    var topo = Math.min(r.top, window.innerHeight - painel.offsetHeight - 12);
    painel.style.top = Math.max(12, topo) + 'px';
  }

  nav.querySelectorAll('a.item[data-sub]').forEach(function (pai) {
    var itens = MODULOS[Number(pai.getAttribute('data-sub'))].sub || [];
    pai.addEventListener('click', function (ev) {
      ev.preventDefault();
      if (donoAberto === pai) fecharSub(); else abrirSub(pai, itens);
    });
  });

  document.addEventListener('click', function (ev) {
    if (!painel.contains(ev.target) && !ev.target.closest('a.item[data-sub]')) fecharSub();
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') fecharSub();
  });
  // Rolar ou redimensionar move a pílula; o painel fixo ficaria para trás.
  window.addEventListener('resize', fecharSub);
  nav.querySelector('.itens').addEventListener('scroll', fecharSub);

  // A barra é sempre visível: não há mais recolher/fixar. O item ativo entra
  // no campo de visão sozinho quando a fila precisa rolar.
  var ativo = nav.querySelector('a.item.on');
  if (ativo && ativo.scrollIntoView) {
    ativo.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  /* ── CABEÇALHO DE MÓDULO ────────────────────────────────────────────────
     As telas que ainda usam a barra legada (`body > header`) ficariam sem
     título nenhum, porque o CSS acima a esconde. Esta função põe no lugar o
     mesmo cartão escuro que as telas novas já têm.

     O NOME NÃO É INVENTADO. Vem, nesta ordem:
       1. da própria barra legada (`.modulo`) — vale para as telas de
          administração, que não estão na lista de módulos e ficariam mudas;
       2. da lista de módulos;
       3. do `<title>` da página.

     A Home não entra: ela tem a própria abertura. */
  function cabecalho() {
    if (atual === 'home_portal.html') return;
    if (document.querySelector('header.cabecalho, .mod-cabeca')) return;  // já tem

    var eu = null;
    MODULOS.forEach(function (m) {
      if (m.href.toLowerCase() === atual) eu = m;
      (m.sub || []).forEach(function (f) {
        if (f.href && f.href.toLowerCase().split('?')[0] === atual) eu = eu || m;
      });
    });

    var legado = document.querySelector('body > header .modulo');
    var nome = (legado && legado.textContent.trim())
            || (eu && eu.nome)
            || (document.title || '').split('·')[0].split('—')[0].trim();
    if (!nome) return;                       // sem nome, não inventa cabeçalho

    var cab = document.createElement('header');
    cab.className = 'cabecalho';
    var h = document.createElement('h1');
    h.textContent = nome;
    cab.appendChild(h);
    if (eu && eu.desc) {
      var sub = document.createElement('p');
      sub.className = 'sub';
      sub.textContent = eu.desc;
      cab.appendChild(sub);
    }

    var dentro = document.querySelector('main') || document.querySelector('.capa');
    if (dentro) dentro.insertBefore(cab, dentro.firstChild);
    else document.body.insertBefore(cab, document.body.firstChild);

    /* O TÍTULO ANTIGO SAI DE CENA.

       Nem toda tela guarda o título num `header.cabecalho`: no `central_fiscal`
       ele é um `<h1>` solto dentro de `.capa`. A guarda acima não o reconhece,
       o cabeçalho padrão entra, e a tela fica com o mesmo título duas vezes,
       um debaixo do outro.

       ESCONDE, não remove: o `<h1>` original pode ser lido pelo código da
       própria tela, e um `querySelector('h1')` que passa a devolver `null`
       quebra em silêncio. */
    if (dentro) {
      var velho = null;
      for (var n = cab.nextElementSibling; n; n = n.nextElementSibling) {
        if (n.tagName === 'H1') { velho = n; break; }
        if (n.querySelector && n.querySelector('h1')) break;   // título de outro bloco: não é duplicata
      }
      if (velho) {
        velho.style.display = 'none';
        var seg = velho.nextElementSibling;
        if (seg && seg.classList.contains('sub')) seg.style.display = 'none';
      }
    }
  }

  /* ESPERA O DOCUMENTO FICAR PRONTO.

     Em metade das telas este arquivo é carregado ANTES do `<main>` — no
     `cte.html` o script está na linha 76 e o `<main>` na 79. Rodando ali, a
     busca por um cabeçalho já existente não acha nada e injeta um segundo:
     foi exatamente o que aconteceu no `cte` e no `nfe_documentos`, que
     ficaram com DOIS títulos, e o de cima dizendo o nome errado. */
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', cabecalho);
  else cabecalho();

  /* ── Sino da Central Fiscal ──────────────────────────────────────────────
     Só o CONTADOR é consultado aqui, e ele não toca a rede: lê dois arquivos
     do disco. Quem decide ir aos portais é a própria consulta, quando o
     intervalo escolhido já passou — e ela roda depois, sem segurar o
     desenho da barra.

     Sem isto, a novidade só apareceria para quem abrisse a Central. O sino
     é o que faz "chegou coisa nova" alcançar quem está em outra tela. */
  var sino = document.getElementById('fscSino');

  function pintarSino(n) {
    if (!sino) return;
    sino.setAttribute('data-tem', n > 0 ? '1' : '0');
    sino.querySelector('.cont').textContent = n > 99 ? '99+' : String(n);
    sino.title = n > 0
      ? ('Central Fiscal — ' + n + (n === 1 ? ' novidade' : ' novidades'))
      : 'Central Fiscal — o que mudou no fiscal';
  }

  function olharCentral() {
    fetch('/api/fiscal/atualizacoes/resumo')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        pintarSino(d.nao_lidos || 0);
        /* Passou do intervalo: busca agora, e repinta com o que chegou. A
           tela não espera por isso. */
        if (d.precisa_verificar && d.ligado !== false) {
          fetch('/api/fiscal/atualizacoes/verificar', { method: 'POST' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function () { olharCentral(); })
            .catch(function () {});
        }
      })
      .catch(function () {});   // sem Central, a barra segue inteira
  }

  window.Fiscale = window.Fiscale || {};
  window.Fiscale.atualizarSinoFiscal = olharCentral;
  olharCentral();
  setInterval(olharCentral, 120000);      // 2 min: é o sino, não a coleta

  /* ── Papel do usuário ────────────────────────────────────────────────────
     O servidor já recusa o que o operador não alcança (403). Isto aqui é só
     para a tela não OFERECER o que sempre será recusado — esconder não
     protege nada, e nunca deve ser confundido com proteção.

     Duas marcas, uma para cada lado:

         <div data-admin>…</div>      some para o operador
         <div data-operador>…</div>   some para o admin (serve para explicar
                                      ao operador o que ficou de fora)

     `data-operador` começa escondido no CSS abaixo e só aparece quando o
     papel confirma. É de propósito: se aparecesse antes da resposta, o admin
     veria por um instante um aviso que não é para ele.

     Esta barra está em todas as telas menos o login, então este é o único
     lugar onde a regra precisa existir. */
  var papelCss = document.createElement('style');
  papelCss.textContent = '[data-operador]{display:none}';
  document.head.appendChild(papelCss);

  /* `Fiscale.papelPronto` resolve com o papel. Serve para a tela NÃO chamar
     uma rota que ela já sabe que vai levar 403:

         await Fiscale.papelPronto;
         if (Fiscale.ehAdmin) carregarConfiguracao();

     Sem isso, cada carregamento de tela do operador deixava um 403 vermelho
     no console. A tela funcionava — o erro era engolido —, mas console
     sempre vermelho é onde um erro de verdade passa despercebido depois. */
  window.Fiscale = window.Fiscale || {};
  window.Fiscale.papelPronto = fetch('/api/quem')
    .then(function (r) { return r.json(); })
    .then(function (q) {
      var papel = q.papel || (q.admin ? 'admin' : 'operador');
      document.body.setAttribute('data-papel', papel);
      window.Fiscale.papel = papel;
      window.Fiscale.ehAdmin = (papel === 'admin');
      papelCss.textContent = (papel === 'admin')
        ? '[data-operador]{display:none!important}'
        : '[data-admin]{display:none!important}';
      return papel;
    })
    .catch(function () {
      /* Sem resposta, assume o mais permissivo NA TELA e deixa o servidor
         decidir — esconder botão nunca foi a proteção. */
      window.Fiscale.papel = 'admin';
      window.Fiscale.ehAdmin = true;
      papelCss.textContent = '[data-operador]{display:none!important}';
      return 'admin';
    });

})();
