/* Fiscale — ponte com o servidor local (persistência + uploads) */
window.Fiscale = {
  /* Versão do estado que esta tela carregou, por módulo.
     Vai de volta no If-Match ao salvar: é assim que o servidor sabe recusar a
     gravação de quem abriu a tela antes de outra pessoa salvar. Com o
     escritório inteiro no mesmo servidor, sem isto a última a salvar apaga o
     trabalho da primeira — e ninguém fica sabendo. */
  _versao: {},

  async carregar(mod){
    try{
      const r = await fetch('/api/state/'+mod);
      if(r.ok){
        const et = r.headers.get('ETag');
        if(et) this._versao[mod] = et.replace(/"/g,'');
        return await r.json();
      }
    }catch(e){}
    return null;
  },

  /* Devolve {ok} · {ok:false,conflito:true} · {ok:false,erro}.
     Avisa sozinho no conflito porque quase ninguém confere o retorno, e
     perder o que foi digitado em silêncio é o defeito que isto conserta.
     Passe {silencioso:true} quando a tela quiser tratar o aviso por conta. */
  async salvar(mod, dados, op){
    const cab = {'Content-Type':'application/json'};
    if(this._versao[mod]) cab['If-Match'] = this._versao[mod];
    let r;
    try{
      r = await fetch('/api/state/'+mod,{method:'POST',headers:cab,body:JSON.stringify(dados)});
    }catch(e){
      return {ok:false, erro:'Não consegui falar com o Fiscale.'};
    }
    const et = r.headers.get('ETag');
    if(et) this._versao[mod] = et.replace(/"/g,'');
    if(r.ok) return {ok:true};

    let d = {};
    try{ d = await r.json(); }catch(e){}
    if(r.status === 409){
      // A versão nova já foi guardada acima: salvar de novo (depois de
      // recarregar) passa. Não recarrego a tela por conta própria — isso
      // apagaria o que a pessoa acabou de digitar.
      if(!(op && op.silencioso)) alert(d.erro || 'Outra pessoa salvou esta tela.');
      return {ok:false, conflito:true, erro:d.erro};
    }
    if(!(op && op.silencioso)) alert(d.erro || 'Não foi possível salvar.');
    return {ok:false, erro:d.erro};
  },
  async enviarArquivo(file, kind, extra){
    const fd = new FormData();
    fd.append('file', file);
    fd.append('kind', kind||'pdf');
    if(extra) for(const k in extra) fd.append(k, extra[k]);
    const r = await fetch('/api/upload',{method:'POST',body:fd});
    return await r.json();
  },
  /* substitui o conteúdo de um array mantendo a mesma referência (para const) */
  repor(arr, novos){ arr.length=0; if(Array.isArray(novos)) novos.forEach(x=>arr.push(x)); }
};
