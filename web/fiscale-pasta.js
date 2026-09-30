/* Fiscale — seletor de pasta, um só para o sistema inteiro.

   POR QUE ISTO EXISTE COMO ARQUIVO SEPARADO

   Até a PORT 4 escolher pasta abria uma janela do `tkinter`. Saiu por dois
   motivos, e o segundo é o que importa aqui: a janela abria na máquina do
   SERVIDOR. Quem acessava o Fiscale pela rede clicava em "Escolher pasta" e
   nada acontecia na tela dele — a janela tinha aberto no outro computador.

   O substituto é este navegador: o servidor só LISTA subpastas (`/api/pastas`)
   e quem navega é a tela. Funciona igual local e pela rede, e não exige
   biblioteca gráfica no runtime portátil.

   Só que ele nasceu copiado dentro de `nfe.html`, duas vezes (`pv*` e `ei*`),
   e a tela do NFS-e ficou para trás chamando `/api/escolher-pasta` — uma rota
   que já não existe. Três telas, três comportamentos, um deles quebrado. Este
   arquivo é a resposta: uma implementação, servida por `fiscale_server` a
   partir de `web/`, que qualquer tela do Fiscale carrega com

       <script src="fiscale-pasta.js"></script>

   O caminho é relativo de propósito. A tela do NFS-e é servida pela raiz
   (`/nfse.html` → o backend do módulo), então lá ele vira `/fiscale-pasta.js`
   — que NÃO está em `NFSE_PREFIXOS` e por isso é servido daqui mesmo.

   USO

       const pasta = await FiscalePasta.escolher({inicial: campo.value});
       if (pasta) campo.value = pasta;          // null = o usuário desistiu

   A promessa resolve com o caminho escolhido, ou `null` se fechou sem
   escolher. A tela decide o que fazer com ele — este componente não salva
   preferência, não valida e não exporta nada.
*/
(function () {
  if (window.FiscalePasta) return;

  var CSS = [
    '.fpasta-fundo{position:fixed;inset:0;z-index:9000;background:rgba(10,42,49,.38);',
    '  display:flex;align-items:center;justify-content:center;padding:18px}',
    '.fpasta-caixa{background:#fff;border-radius:18px;width:min(620px,100%);',
    '  max-height:min(78vh,660px);display:flex;flex-direction:column;overflow:hidden;',
    '  box-shadow:0 24px 60px -18px rgba(10,42,49,.55);font:14px system-ui,-apple-system,'
      + '"Segoe UI",Roboto,sans-serif;color:#12212A}',
    '.fpasta-topo{display:flex;align-items:center;gap:9px;padding:13px 15px;',
    '  border-bottom:1px solid #DDE4E7;background:#F7F9FA}',
    '.fpasta-titulo{font-weight:800;font-size:15px;flex:1}',
    '.fpasta-b{border:1px solid #DDE4E7;background:#fff;border-radius:999px;',
    '  padding:5px 12px;font-size:12.5px;font-weight:700;cursor:pointer;color:#12212A}',
    '.fpasta-b:hover{background:#EEF4F6}',
    '.fpasta-b.pri{background:#10444E;border-color:#10444E;color:#fff}',
    '.fpasta-b.pri:hover{background:#0C3038}',
    '.fpasta-b[disabled]{opacity:.4;cursor:default}',
    '.fpasta-caminho{padding:9px 15px;font-size:12.5px;color:#5A6B72;',
    '  word-break:break-all;border-bottom:1px solid #EDF1F2;font-family:ui-monospace,'
      + 'Consolas,monospace}',
    '.fpasta-lista{flex:1;overflow:auto;min-height:120px}',
    '.fpasta-item{padding:9px 15px;border-bottom:1px solid #EDF1F2;cursor:pointer;',
    '  font-size:13px;display:flex;align-items:center;gap:8px}',
    '.fpasta-item:hover,.fpasta-item:focus{background:#F7F9FA;outline:none}',
    '.fpasta-vazio{padding:16px;color:#5A6B72;font-size:13px}',
    '.fpasta-erro{padding:11px 15px;background:#FEF7F6;color:#8A2B1E;font-size:13px}',
    '.fpasta-pe{display:flex;align-items:center;gap:9px;padding:11px 15px;',
    '  border-top:1px solid #DDE4E7;background:#F7F9FA}',
    '.fpasta-pe input{flex:1;border:1px solid #DDE4E7;border-radius:11px;',
    '  padding:7px 10px;font-size:12.5px;font-family:ui-monospace,Consolas,monospace}',
    '@media (prefers-reduced-motion:reduce){.fpasta-caixa{transition:none}}'
  ].join('\n');

  function estilo() {
    if (document.getElementById('fpasta-css')) return;
    var s = document.createElement('style');
    s.id = 'fpasta-css';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function pedir(caminho) {
    var u = '/api/pastas' + (caminho ? '?caminho=' + encodeURIComponent(caminho) : '');
    return fetch(u).then(function (r) {
      if (r.ok) return r.json();
      return r.json().catch(function () { return {}; }).then(function (j) {
        throw new Error(j.detail || ('Erro ' + r.status));
      });
    });
  }

  function texto(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  /* `inicial` é só uma sugestão: se o caminho já não existir — pasta apagada,
     pendrive fora —, o navegador volta para os pontos de partida em vez de
     ficar preso num erro. */
  function escolher(opcoes) {
    var o = opcoes || {};
    estilo();

    return new Promise(function (resolve) {
      var atual = null;
      var fundo = document.createElement('div');
      fundo.className = 'fpasta-fundo';
      fundo.innerHTML =
        '<div class="fpasta-caixa" role="dialog" aria-modal="true"'
          + ' aria-label="Escolher pasta">'
        + '<div class="fpasta-topo">'
        +   '<button class="fpasta-b" data-acima>↑ Acima</button>'
        +   '<div class="fpasta-titulo">' + texto(o.titulo || 'Escolher pasta') + '</div>'
        +   '<button class="fpasta-b" data-fechar aria-label="Fechar">✕</button>'
        + '</div>'
        + '<div class="fpasta-caminho" data-caminho></div>'
        + '<div class="fpasta-lista" data-lista></div>'
        + '<div class="fpasta-pe">'
        +   '<input data-digitado spellcheck="false" placeholder="ou escreva o caminho">'
        +   '<button class="fpasta-b pri" data-usar>Usar esta pasta</button>'
        + '</div></div>';
      document.body.appendChild(fundo);

      var $ = function (sel) { return fundo.querySelector(sel); };
      var lista = $('[data-lista]'), rotulo = $('[data-caminho]');
      var bAcima = $('[data-acima]'), bUsar = $('[data-usar]');
      var campo = $('[data-digitado]');

      function fim(valor) {
        document.removeEventListener('keydown', tecla);
        fundo.remove();
        resolve(valor);
      }
      function tecla(ev) { if (ev.key === 'Escape') fim(null); }
      document.addEventListener('keydown', tecla);

      fundo.addEventListener('click', function (ev) {
        if (ev.target === fundo) fim(null);       // clicar fora desiste
      });
      $('[data-fechar]').onclick = function () { fim(null); };
      bUsar.onclick = function () {
        var escrito = campo.value.trim();
        fim(escrito || atual || null);
      };
      bAcima.onclick = function () {
        if (!atual) return;
        pedir(atual).then(function (r) { abrir(r.pai || null); })
                    .catch(function () { abrir(null); });
      };
      campo.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') { ev.preventDefault(); bUsar.click(); }
      });

      function abrir(caminho, jaCaiu) {
        lista.innerHTML = '<div class="fpasta-vazio">Carregando…</div>';
        pedir(caminho).then(function (r) {
          atual = r.atual;
          rotulo.textContent = r.atual || 'Escolha por onde começar';
          bAcima.disabled = !r.pai;
          bUsar.disabled = false;
          var itens = (r.raizes && r.raizes.length) ? r.raizes : (r.pastas || []);
          if (!itens.length) {
            lista.innerHTML = '<div class="fpasta-vazio">Esta pasta não tem '
              + 'subpastas. Use <b>Usar esta pasta</b>.</div>';
            return;
          }
          lista.innerHTML = itens.map(function (p) {
            return '<div class="fpasta-item" tabindex="0" data-ir="'
              + texto(p.caminho) + '">📁 ' + texto(p.nome) + '</div>';
          }).join('');
          Array.prototype.forEach.call(lista.querySelectorAll('[data-ir]'), function (el) {
            el.onclick = function () { abrir(el.getAttribute('data-ir')); };
            el.onkeydown = function (ev) {
              if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); el.click(); }
            };
          });
        }).catch(function (e) {
          /* Caminho ruim volta para o início UMA vez. Sem essa trava, uma
             falha nos pontos de partida viraria recursão. */
          if (caminho && !jaCaiu) { abrir(null, true); return; }
          lista.innerHTML = '<div class="fpasta-erro">' + texto(e.message) + '</div>';
          bUsar.disabled = !atual;
        });
      }

      campo.value = '';
      abrir((o.inicial || '').trim() || null);
    });
  }

  window.FiscalePasta = { escolher: escolher };
})();
