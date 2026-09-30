/* Fiscale — abrir o Elo com o login que a pessoa já fez.
 *
 * Saiu da Home (portal, Fase 2) para que a Central de Aplicações e a Home
 * usem o MESMO caminho. Duas cópias disto divergiriam justamente nos detalhes
 * de segurança abaixo, que são os que ninguém relê.
 *
 * Pede o bilhete de entrada ao Fiscale e o entrega ao Elo por um formulário
 * POST em aba nova.
 *
 * POST, e não um link com o token na URL, por dois motivos: a URL fica no
 * histórico do navegador e viaja no cabeçalho Referer; e o Elo precisa gravar
 * o cookie dele numa navegação de verdade, não numa chamada em segundo plano.
 * O token vale 90 segundos e serve uma vez só.
 *
 * Se o Elo estiver fora do ar, isto avisa e acabou — o Fiscale segue
 * funcionando normalmente.
 *
 *   abrirElo(botao)                  avisa com alert()
 *   abrirElo(botao, mostrarErro)     entrega a mensagem a quem chamou
 *   abrirEloEmbutido(iframe, aoErro, aoPronto)  desenha o Elo AQUI dentro
 */
(function(){
  // Quanto se espera pelo ELO dentro do iframe antes de chamar de falha.
  //
  // 15 s é folgado para uma aplicação na rede local e curto o bastante para
  // ninguém ficar olhando uma tela parada sem saber se deve esperar mais. Não
  // é tempo de resposta do servidor: é o fluxo inteiro — a porta, o bilhete
  // por POST e o redirecionamento para a tela embutida.
  var ESPERA_MAXIMA_MS = 15000;

  // Um caminho so para o bilhete. Os dois modos de abrir o ELO pedem
  // aqui; duas chamadas diferentes divergiriam no primeiro ajuste, e o
  // bilhete e justamente a parte que ninguem quer ver divergir.
  async function pedirBilhete(){
    var r = await fetch('/api/elo/abrir', {method:'POST'});
    var d = await r.json();
    if(!d.ok) throw new Error(d.erro || 'Não foi possível abrir o Elo.');
    return d;
  }

  // O bilhete vai SEMPRE por POST, nunca na URL — nem na do iframe. Ele
  // vale 90 s e serve uma vez; ainda assim, URL fica no histórico e no
  // Referer, e o iframe tem os dois.
  function entregarBilhete(destino, token, alvo){
    var f = document.createElement('form');
    f.method = 'POST';
    f.action = destino;
    f.target = alvo;
    f.setAttribute('rel', 'noreferrer');
    var i = document.createElement('input');
    i.type = 'hidden'; i.name = 'token'; i.value = token;
    f.appendChild(i);
    document.body.appendChild(f);
    f.submit();
    setTimeout(function(){ f.remove(); }, 0);
  }

  async function abrirElo(botao, mostrarErro){
    var avisar = typeof mostrarErro === 'function' ? mostrarErro : function(m){ alert(m); };
    var texto = botao ? botao.innerHTML : '';
    if(botao){ botao.disabled = true; botao.textContent = 'Abrindo…'; }
    try{
      var d = await pedirBilhete();

      // Abre a janela ANTES de montar o formulário: como estamos dentro do
      // clique do usuário, o navegador não trata como popup. Depois o
      // formulário mira essa janela pelo nome.
      //
      // Não use target="_blank" direto: navegador que bloqueia a janela
      // degrada o POST para uma navegação GET, e o Elo recusa com 405 —
      // exatamente o que aconteceu no primeiro teste.
      var janela = window.open('', 'fiscale_elo');

      // Sem janela (bloqueada), envia na própria aba: é melhor sair do
      // Fiscale do que não entrar no Elo.
      entregarBilhete(d.destino, d.token, janela ? 'fiscale_elo' : '_self');
    }catch(e){
      avisar('O Elo não respondeu. O Fiscale continua funcionando normalmente.');
    }finally{
      if(botao){ botao.disabled = false; botao.innerHTML = texto; }
    }
  }
  /**
   * Desenha o ELO DENTRO desta página, num iframe.
   *
   * Duas navegações, nesta ordem, e o motivo de cada uma:
   *
   *   1. `/api/auth/destino?para=/atendimentos?embutido=1` — o ELO guarda
   *      o destino num cookie próprio (curto, HttpOnly) e mostra a porta.
   *      É o mecanismo que ele já usa para quem clica numa notificação com
   *      a sessão vencida; aqui ele serve para a troca cair direto na tela
   *      embutida, em vez de passar pela home com a casca do ELO à mostra.
   *   2. o bilhete, por POST, no mesmo iframe. O ELO cria a sessão DELE e
   *      redireciona para o destino guardado.
   *
   * Não há segunda sessão, endpoint novo nem bilhete em URL: é o mesmo
   * fluxo da aba nova, com outro alvo.
   */
  async function abrirEloEmbutido(iframe, mostrarErro, aoPronto){
    var avisar = typeof mostrarErro === 'function' ? mostrarErro : function(m){ alert(m); };
    if(!iframe) return;
    if(!iframe.name) iframe.name = 'fiscale_elo_embutido';

    // Um caminho de falha só. `avisar` é o mesmo dos dois modos de abrir, e
    // `falhou` existe para que timeout, bilhete recusado e exceção terminem
    // exatamente no mesmo lugar — quem chamou rearma a tentativa uma vez, e
    // não uma vez por tipo de erro.
    var encerrado = false;
    var relogio = null;
    function falhou(msg){
      if(encerrado) return;
      encerrado = true;
      if(relogio){ clearTimeout(relogio); relogio = null; }
      // Solta o iframe: sem isto, uma carga que chegue atrasada dispararia o
      // `aoPronto` de uma tentativa que já foi declarada perdida.
      try{ iframe.src = 'about:blank'; }catch(e){}
      avisar(msg);
    }

    try{
      var d = await pedirBilhete();
      // O endereço do ELO vem do `elo_config.json`, pelo servidor. Sem ele o
      // embutido não tem para onde apontar. A verificação fica AQUI e não em
      // `pedirBilhete`: o modo aba nova só precisa de `destino`, e reprová-lo
      // por um campo que ele não usa seria quebrar o que funciona.
      if(!d.base){
        falhou('Este Fiscale não sabe o endereço do Elo (elo_config.json).');
        return;
      }

      // O destino é do ELO e começa com uma barra só: ele recusa qualquer
      // outra coisa (caminhoSeguro), então isto não vira redirecionador.
      var tela = '/atendimentos?embutido=1';

      iframe.addEventListener('load', function aoAbrirPorta(){
        iframe.removeEventListener('load', aoAbrirPorta);
        if(encerrado) return;
        entregarBilhete(d.destino, d.token, iframe.name);
        iframe.addEventListener('load', function pronto(){
          iframe.removeEventListener('load', pronto);
          if(encerrado) return;
          encerrado = true;
          if(relogio){ clearTimeout(relogio); relogio = null; }
          // Avisa quem chamou, em vez de marcar o elemento e esperar que
          // alguem olhe: a marca dependia da ORDEM dos ouvintes de `load`,
          // e nessa corrida a tela ficava em "Abrindo o ELO..." para sempre.
          if(typeof aoPronto === 'function') aoPronto();
        });
      });

      // O RELÓGIO É ARMADO ANTES DA PRIMEIRA NAVEGAÇÃO.
      //     O pior modo de falha deste fluxo não é erro: é silêncio. Se o ELO
      //     estiver fora, se o endereço estiver errado, ou se ele acrescentar
      //     um redirecionamento a mais do que os dois que este código conta,
      //     `aoPronto` simplesmente não vem — e a tela ficava em "Abrindo o
      //     ELO…" para sempre, sem mensagem e sem como tentar de novo.
      //
      //     Armado ANTES do `iframe.src` de propósito: uma falha imediata de
      //     rede acontece antes da próxima volta do laço, e um relógio armado
      //     depois poderia nem existir quando ela ocorresse.
      relogio = setTimeout(function(){
        falhou('O Elo não respondeu em ' + (ESPERA_MAXIMA_MS / 1000)
               + ' segundos. O Fiscale continua funcionando normalmente.');
      }, ESPERA_MAXIMA_MS);

      iframe.src = d.base + '/api/auth/destino?para=' + encodeURIComponent(tela);
    }catch(e){
      falhou(e && e.message ? e.message
             : 'O Elo não respondeu. O Fiscale continua funcionando normalmente.');
    }
  }

  window.abrirElo = abrirElo;
  window.abrirEloEmbutido = abrirEloEmbutido;
})();
