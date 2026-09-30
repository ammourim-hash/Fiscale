#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da Central de Atualizações Fiscais.

    python teste_central_fiscal.py

O QUE ESTA SUÍTE PROVA

    1. Que a Central lê RSS 1.0, RSS 2.0 e Atom — os três formatos que os
       portais oficiais usam.
    2. Que **item sem link não entra**, e que a estrutura do portal (pasta,
       coleção, atalho, imagem) não vira notícia.
    3. Que fonte que falha vira **"não verificada"**, nunca "nada mudou" — e
       que ela continua aparecendo na lista.
    4. Que reverificar não duplica.
    5. Que a Central **não altera cálculo, cadastro, acervo nem checkpoint**.
       Ela é jornal, não motor.
    6. Que o contador de não lidos anda e zera.
    7. Que nada disso toca a rede de verdade — o buscador é injetado.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import fiscal_atualizacoes as fa                     # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                             # noqa: E402


def _connect_proibido(self, endereco, *a, **kw):
    raise AssertionError("teste tentou abrir rede para " + str(endereco))


_socket.socket.connect = _connect_proibido


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        _erros.append(desc)
        print("  FALHOU  " + desc)


def igual(a, b, desc):
    ok(a == b, desc if a == b else desc + "  (obtive %r, esperava %r)" % (a, b))


# ── feeds de mentira, nos três formatos reais ───────────────────────────────
# O da Receita é RSS 1.0 (RDF) com `dc:type` — e é ele que mistura a estrutura
# do portal com o conteúdo. Reproduzido aqui como está lá.
RSS1 = b"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns="http://purl.org/rss/1.0/">
  <item>
    <title>Carrossel</title>
    <link>https://exemplo.gov.br/noticias/carrossel</link>
    <dc:type>Folder</dc:type>
  </item>
  <item>
    <title>Foto de capa</title>
    <link>https://exemplo.gov.br/noticias/foto.png/view</link>
    <dc:type>Image</dc:type>
  </item>
  <item>
    <title>Ultimas Noticias</title>
    <link>https://exemplo.gov.br/noticias/ultimas</link>
    <dc:type>Collection</dc:type>
  </item>
  <item>
    <title>Todas as noticias</title>
    <link>https://exemplo.gov.br/noticias/todas</link>
    <dc:type>Link</dc:type>
  </item>
  <item>
    <title>Receita divulga regras da CBS e do IBS para 2027</title>
    <link>https://exemplo.gov.br/noticias/cbs-ibs-2027</link>
    <description>&lt;p&gt;A Reforma Tributaria entra em fase de teste.&lt;/p&gt;</description>
    <dc:date>2026-08-20T10:00:00</dc:date>
    <dc:type>collective.nitf.content</dc:type>
  </item>
  <item>
    <title>Sem link nenhum</title>
    <dc:type>collective.nitf.content</dc:type>
  </item>
</rdf:RDF>"""

RSS2 = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Portal</title>
  <item>
    <title>Nota Tecnica 2026.001 da NF-e publicada</title>
    <link>https://exemplo.gov.br/nfe/nt-2026-001</link>
    <description>Layout novo para o campo de rastreabilidade.</description>
    <pubDate>Mon, 18 Aug 2026 09:00:00 -0300</pubDate>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Novo limite do Simples Nacional em discussao</title>
    <link href="https://exemplo.gov.br/simples/limite"/>
    <summary>O CGSN avalia o teto do MEI.</summary>
    <updated>2026-08-19T14:00:00-03:00</updated>
  </entry>
</feed>"""


def buscador_de(mapa, falhar=()):
    """Devolve um buscador que não usa rede: `url -> bytes`."""
    def buscar(url, tempo_limite=None):
        if url in falhar:
            raise TimeoutError("fonte fora do ar")
        if url not in mapa:
            raise ValueError("url nao cadastrada no teste: " + url)
        return mapa[url]
    return buscar


FEED_RFB = fa.FONTES_POR_ID["rfb"].feed


def central_de(raiz, mapa=None, falhar=()):
    return fa.Central(raiz, buscador=buscador_de(
        mapa if mapa is not None else {FEED_RFB: RSS1}, falhar))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fiscale_central_"))

    # ═══════════════════════════════════════════════════════════════════
    secao("Os três formatos que os portais oficiais usam")
    for nome, bruto, esperado in (("RSS 1.0 (RDF)", RSS1, 1),
                                  ("RSS 2.0", RSS2, 1),
                                  ("Atom", ATOM, 1)):
        pubs = fa.interpretar(bruto, "x")
        igual(len(pubs), esperado, "%s: %d publicação(ões)" % (nome, esperado))
    ok(fa.interpretar(ATOM, "x")[0].link.endswith("/simples/limite"),
       "no Atom o link vem do atributo href, não do texto")

    secao("A estrutura do portal não é notícia")
    pubs = fa.interpretar(RSS1, "rfb")
    igual([p.titulo for p in pubs],
          ["Receita divulga regras da CBS e do IBS para 2027"],
          "pasta, coleção, atalho e imagem ficaram de fora")
    for tipo in ("Folder", "Collection", "Link", "Image", "File"):
        ok(fa.estrutural(tipo, "https://x/y"), "%r é estrutura" % tipo)
    ok(fa.estrutural("", "https://x/foto.png/view"),
       "imagem servida pela view do Plone também")
    ok(not fa.estrutural("collective.nitf.content",
                         "https://x/noticias/algo"),
       "e a notícia de verdade passa")

    secao("Item sem link não entra")
    ok(all(p.link for p in pubs), "toda publicação guardada tem link")
    ok("Sem link nenhum" not in [p.titulo for p in pubs],
       "o item sem link foi descartado, não guardado sem link")

    secao("Classificação por tema")
    igual(fa.classificar("Regras da CBS e do IBS", ""), ["REFORMA"],
          "CBS/IBS caem em Reforma Tributária")
    igual(fa.classificar("Nota Técnica da NF-e", ""), ["DOCUMENTOS"],
          "NF-e cai em Documentos fiscais")
    igual(fa.classificar("Limite do Simples Nacional", ""), ["SIMPLES"],
          "Simples Nacional cai no seu tema")
    igual(fa.classificar("Prazo da DCTF", ""), ["OBRIGACOES"],
          "DCTF cai em Obrigações")
    igual(fa.classificar("Leilão de bens apreendidos", ""), ["GERAL"],
          "o que não casa vira Geral, e não some")
    ok(len(fa.classificar("EFD sobre CBS", "")) == 2,
       "um texto pode tocar dois temas")

    # ═══════════════════════════════════════════════════════════════════
    secao("A coleta guarda, e reverificar não duplica")
    c = central_de(tmp)
    r = c.verificar()
    igual(r["novas"], 1, "a primeira verificação trouxe 1")
    igual(len(c.guardadas()), 1, "e guardou 1")
    r2 = c.verificar()
    igual(r2["novas"], 0, "a segunda não trouxe nada de novo")
    igual(len(c.guardadas()), 1, "e não duplicou")

    secao("Fonte que falha vira 'não verificada', nunca 'nada mudou'")
    tmp2 = Path(tempfile.mkdtemp(prefix="fiscale_central2_"))
    c2 = central_de(tmp2, falhar=(FEED_RFB,))
    r3 = c2.verificar()
    igual(r3["novas"], 0, "nada entrou")
    est_rfb = [f for f in r3["fontes"] if f["fonte"] == "rfb"][0]
    igual(est_rfb["estado"], fa.NAO_VERIFICADO,
          "a fonte que caiu está marcada como NÃO VERIFICADA")
    ok("TimeoutError" in est_rfb["detalhe"], "com o motivo registrado")
    ok(r3["nao_verificadas"] >= 1,
       "e o resultado conta quantas fontes ficaram sem verificação")
    lista = c2.listar()
    ok(any(f["id"] == "rfb" for f in lista["fontes"]),
       "a fonte que falhou CONTINUA na lista — não some da tela")

    secao("Fonte sem feed oficial é declarada, não escondida")
    sem_feed = [f for f in fa.FONTES if not f.tem_feed]
    ok(len(sem_feed) >= 1,
       "há fontes sem feed cadastradas (%d)" % len(sem_feed))
    ok(all(f.observacao and f.site for f in sem_feed),
       "cada uma diz por que e para onde ir à mão")
    est = [f for f in r["fontes"] if f["fonte"] == "confaz"][0]
    igual(est["estado"], fa.SEM_FEED, "e o estado delas é SEM_FEED")

    secao("O contador de não lidos")
    igual(c.nao_lidos(), 1, "1 não lido depois da primeira coleta")
    c.marcar_lido()
    igual(c.nao_lidos(), 0, "zerou ao marcar como lido")
    c3 = fa.Central(tmp, buscador=buscador_de({FEED_RFB: RSS1.replace(
        b"cbs-ibs-2027", b"cbs-ibs-2028")}))
    c3.verificar()
    igual(c3.nao_lidos(), 1, "publicação nova depois da leitura conta de novo")

    secao("A cadência tem limites")
    e = c.configurar(intervalo=1)
    igual(e["intervalo"], 900,
          "menos de 15 min é elevado a 15 min — não se bate de minuto em "
          "minuto na porta do governo")
    e = c.configurar(intervalo=999999)
    igual(e["intervalo"], 86400, "e o teto é um dia")
    e = c.configurar(ligado=False)
    ok(not c.precisa_verificar(), "desligada, ela não pede verificação")
    c.configurar(ligado=True)

    # ═══════════════════════════════════════════════════════════════════
    secao("É jornal, não motor")
    fonte_bruta = (RAIZ / "nfse" / "backend"
                   / "fiscal_atualizacoes.py").read_text("utf-8")
    import ast as _ast
    arvore = _ast.parse(fonte_bruta)
    for no in _ast.walk(arvore):
        if isinstance(no, (_ast.Module, _ast.FunctionDef, _ast.ClassDef)):
            if _ast.get_docstring(no, clean=False) and no.body and \
                    isinstance(no.body[0], _ast.Expr):
                no.body.pop(0)
    codigo = _ast.unparse(arvore)
    for proibido in ("aliquota", "alíquota", "calcular_das", "rbt12",
                     "classificador", "acervo", "indice", "checkpoint",
                     "certificado", "state_clientes", "pipeline",
                     "distNSU", "servico_distribuicao"):
        ok(proibido not in codigo,
           "o CÓDIGO da Central não menciona %r" % proibido)
    ok("unlink" not in codigo and "rmtree" not in codigo,
       "e não apaga nada")

    secao("A Central escreve só na área dela")
    dentro = [p for p in Path(tmp).rglob("*") if p.is_file()]
    ok(all("atualizacoes" in str(p) for p in dentro),
       "todo arquivo criado está em fiscal/atualizacoes/ (%d arquivos)"
       % len(dentro))
    igual(sorted(p.name for p in dentro),
          ["estado.json", "publicacoes.json"],
          "e são só dois: o arquivo e o estado")

    secao("Arquivo corrompido não derruba a tela")
    c.arq_publicacoes.write_text("{isto não é json", encoding="utf-8")
    igual(c.guardadas(), [], "publicações corrompidas viram lista vazia")
    ok(isinstance(c.estado(), dict), "e o estado continua respondendo")

    # ═══════════════════════════════════════════════════════════════════
    secao("A tela")
    html = (RAIZ / "web" / "central_fiscal.html").read_text("utf-8")
    ok("não altera cálculo" in html, "a tela declara que não altera cálculo")
    ok("não verificada" in html,
       "e explica o que significa fonte não verificada")
    ok("tempo real" in html,
       "e é honesta sobre não existir aviso em tempo real")
    for consulta in ("distNSU", "consChNFe", "NFeDistribuicaoDFe"):
        ok(consulta not in html, "a tela não fala com a SEFAZ (%s)" % consulta)

    nav = (RAIZ / "web" / "fiscale-nav.js").read_text("utf-8")
    ok("fscSino" in nav, "o sino existe na barra de módulos")
    ok("central_fiscal.html" in nav, "e leva à Central")
    ok("/api/fiscal/atualizacoes/resumo" in nav,
       "consultando só o contador, que não toca a rede")

    papeis = (RAIZ / "fiscale_papeis.py").read_text("utf-8")
    ok('("GET", "/api/fiscal/atualizacoes")' in papeis,
       "ler a Central é do operador também — é jornal")
    ok('("POST", "/api/fiscal/atualizacoes/config")' in papeis,
       "mas a cadência é do administrador")

    secao("Nenhum teste desta suíte foi à rede")
    try:
        _socket.socket().connect(("127.0.0.1", 9))
        ok(False, "a trava de rede não estava ativa")
    except AssertionError:
        ok(True, "a trava estava mesmo ativa (conferido)")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
