#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da NF-e 5 — a interface passa a ler o índice.

    python teste_nfe5.py

O QUE ESTA SUÍTE PROVA
    1. O **contrato** da API: as chaves que a tela consome existem e significam
       o que significavam — a troca da fonte não pode mudar o formato debaixo
       de quem já consome.
    2. Que `/api/nfe/notas` lê o ÍNDICE e **não** varre pasta legada.
    3. Que os documentos que só existiam no acervo agora aparecem.
    4. Que o XML servido é o original do acervo, e que não há caminho de
       arquivo vindo do navegador.

POR QUE AS ROTAS SÃO CHAMADAS DIRETO
    O `TestClient` do FastAPI exige `httpx`, que o projeto não tem — e
    acrescentar dependência para testar seria pagar no pacote portátil por
    comodidade de teste. As funções de rota são chamadas como funções, com um
    dublê de `Request` que só carrega `query_params`. É o que elas usam.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402
import ingestao as ing                               # noqa: E402
from ingestao import acervo as acv                   # noqa: E402
from ingestao import consulta as cq                  # noqa: E402
from ingestao import documento as dm                 # noqa: E402
from ingestao import pipeline as pipe                # noqa: E402
from ingestao import prova_visibilidade as pv        # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                             # noqa: E402

_TENTATIVAS_DE_REDE: list[str] = []
_connect_original = _socket.socket.connect


def _connect_proibido(self, endereco, *a, **kw):
    _TENTATIVAS_DE_REDE.append(str(endereco))
    raise AssertionError(f"teste tentou abrir conexão de rede para {endereco}")


_socket.socket.connect = _connect_proibido


def secao(t):
    print(f"\n{t}")


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print(f"  ok   {desc}")
    else:
        _falhas += 1
        _erros.append(desc)
        print(f"  FALHOU  {desc}")


def igual(a, b, desc):
    ok(a == b, desc + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


def levanta(f, desc, status=None):
    try:
        f()
    except Exception as e:
        certo = status is None or getattr(e, "status_code", None) == status
        ok(certo, desc if certo else
           f"{desc} — status {getattr(e, 'status_code', '?')}, esperava {status}")
    else:
        ok(False, f"{desc} — não levantou nada")


from teste_fixturas_nfe import (                     # noqa: E402
    cnpj_ficticio, chave_ficticia, xml_nfe, xml_resumo, xml_evento, bruto,
    EMPRESA_A, FORNECEDOR,
)

OUTRA = cnpj_ficticio("888888880001")
CONTA = "conta-teste-1"      # o `id` do cadastro; o CNPJ nunca vai na URL


class Req:
    """Dublê de `Request`: a rota só lê `query_params`."""

    def __init__(self, **q):
        self.query_params = {k: str(v) for k, v in q.items() if v not in (None, "")}


# ══════════════════════════════════════════════════════════════════════════
# A tela em uma raiz temporária, com o `main` do NFS-e apontado para ela.
# ══════════════════════════════════════════════════════════════════════════
def montar(raiz, empresa=EMPRESA_A):
    """Documentos variados, incluindo um que SÓ existe no acervo."""
    import json
    import seguranca

    (Path(raiz) / "certs").mkdir(parents=True, exist_ok=True)
    (Path(raiz) / "certs" / "E.pfx").write_bytes(b"PFX-FALSO")
    # `id` diferente do CNPJ de propósito: é assim no cadastro real, e é o que
    # permite verificar que o CNPJ sai mascarado da API.
    (Path(raiz) / "certificados.json").write_text(json.dumps([{
        "id": "conta-teste-1", "cnpj": empresa, "nome": "EMPRESA DE TESTE LTDA",
        "caminho": "certs/E.pfx"}]), encoding="utf-8")

    ac = acv.abrir(raiz, empresa, servico="nfe_distribuicao",
                   ambiente=ing.PRODUCAO)
    ch, n = {}, 0

    def guarda(rotulo, conteudo, schema="procNFe_v4.00.xsd"):
        nonlocal n
        n += 1
        ac.preservar(bruto(conteudo, nsu=str(1000 + n), schema=schema))

    c1 = chave_ficticia(FORNECEDOR, 1); ch["recebida"] = c1
    guarda("recebida", xml_nfe(c1, valor="1000.00", numero="1"))

    c2 = chave_ficticia(EMPRESA_A, 2); ch["emitida"] = c2
    guarda("emitida", xml_nfe(c2, valor="2000.00", numero="2",
                              emitente=EMPRESA_A, destinatario=FORNECEDOR))

    c3 = chave_ficticia(FORNECEDOR, 3); ch["transporte"] = c3
    base = xml_nfe(c3, valor="9000.00", numero="3",
                   destinatario=OUTRA).decode("utf-8")
    base = base.replace("</infNFe>", f"<transp><transporta><CNPJ>{EMPRESA_A}"
                                     f"</CNPJ></transporta></transp></infNFe>")
    guarda("transporte", base.encode("utf-8"))

    c4 = chave_ficticia(FORNECEDOR, 4); ch["cancelada"] = c4
    guarda("cancelada", xml_nfe(c4, valor="400.00", numero="4"))
    guarda("evento", xml_evento(c4, tp="110111"), "procEventoNFe_v1.00.xsd")
    guarda("passagem", xml_evento(c4, tp="510630"), "procEventoNFe_v1.00.xsd")

    c5 = chave_ficticia(FORNECEDOR, 5); ch["resumo"] = c5
    guarda("resumo", xml_resumo(c5, valor="500.00"), "resNFe_v1.01.xsd")

    pipe.indexar_pendentes(raiz, empresa)
    return ch


def carregar_main(raiz):
    """Recarrega o `main` do NFS-e apontado para a raiz temporária."""
    os.environ["FISCALE_DADOS"] = str(raiz)
    import fiscale_dados as fd
    fd._raiz_cache = None
    fd.raiz(raiz)
    for mod in ("main",):
        sys.modules.pop(mod, None)
    import main
    return main


# ══════════════════════════════════════════════════════════════════════════
secao("A rota não varre mais a pasta legada")

_FONTE = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
_ini = _FONTE.index('@app.get("/api/nfe/notas")')
_fim = _FONTE.index('@app.get("/api/nfe/cte")')
_BLOCO = _FONTE[_ini:_fim]

for proibido in ("carregar_nfe", "nfemod", "glob(", "rglob("):
    ok(proibido not in _BLOCO,
       f"o bloco de `/api/nfe/notas` não usa `{proibido}`")
ok("_cq.consultar" in _BLOCO, "e chama a camada de consulta")

# O leitor legado CONTINUA no código — mas só para conferência.
ok("def carregar_nfe" in (RAIZ / "nfse" / "backend" / "nfe.py").read_text("utf-8"),
   "o leitor legado continua existindo, como fallback de auditoria")
ok("import nfe as legado" in
   (RAIZ / "nfse" / "backend" / "ingestao" / "equivalencia.py").read_text("utf-8"),
   "e quem o usa é a conferência, não a tela")


# ══════════════════════════════════════════════════════════════════════════
secao("Contrato da API — o que a tela consome não mudou de nome")
with apoio.raiz_temporaria("nfe5_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    r = main.nfe_notas(Req(), CONTA, tamanho=50)

    for campo in ("notas", "resumo", "por_papel", "sincronizado", "pagina",
                  "empresa"):
        ok(campo in r, f"o JSON traz `{campo}`")
    for campo in ("quantidade", "total", "completas", "resumos"):
        ok(campo in r["resumo"], f"`resumo.{campo}` continua existindo")
    for campo in ("total", "pagina", "tamanho", "paginas", "nesta_pagina"):
        ok(campo in r["pagina"], f"`pagina.{campo}`")

    n = r["notas"][0]
    for campo in ("chave", "numero", "serie", "data", "valor", "emit_cnpj",
                  "emit_nome", "cancelada", "completa"):
        ok(campo in n, f"cada nota traz `{campo}` (nome antigo, preservado)")
    for campo in ("situacao", "situacao_atual", "papel", "papeis", "conteudo",
                  "eventos", "id_documento", "uf", "modelo", "especie"):
        ok(campo in n, f"e o que a NF-e 4 acrescentou: `{campo}`")

    ok(isinstance(n["valor"], float), "`valor` é número, não texto")
    ok(isinstance(n["cancelada"], bool), "`cancelada` é booleano")
    ok(isinstance(n["papeis"], list), "`papeis` é lista")
    ok(r["sincronizado"] is True, "`sincronizado` diz que a empresa tem acervo")

    secao("A identidade da empresa sai MASCARADA")
    ok("***" in r["empresa"]["cnpj_mascarado"],
       "o CNPJ não vai inteiro para a tela")
    ok(EMPRESA_A not in str(r["empresa"]), "nem em outro campo")


# ══════════════════════════════════════════════════════════════════════════
secao("Paginação real, feita no banco")
with apoio.raiz_temporaria("nfe5_") as raiz:
    montar(raiz)
    main = carregar_main(raiz)

    p1 = main.nfe_notas(Req(), CONTA, pagina=1, tamanho=2)
    p2 = main.nfe_notas(Req(), CONTA, pagina=2, tamanho=2)
    igual(p1["pagina"]["total"], 5, "o total é do conjunto")
    igual(len(p1["notas"]), 2, "a página traz 2")
    igual(p1["pagina"]["paginas"], 3, "e são 3 páginas")
    chaves1 = {x["chave"] for x in p1["notas"]}
    chaves2 = {x["chave"] for x in p2["notas"]}
    igual(chaves1 & chaves2, set(), "sem sobreposição entre páginas")

    grande = main.nfe_notas(Req(), CONTA, tamanho=99999)
    ok(grande["pagina"]["tamanho"] <= cq.TAMANHO_MAXIMO,
       "o tamanho tem teto — a tela não derruba o servidor pedindo tudo")


# ══════════════════════════════════════════════════════════════════════════
secao("Filtros pela API")
with apoio.raiz_temporaria("nfe5_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    def total(**q):
        return main.nfe_notas(Req(**q), CONTA, tamanho=50)["pagina"]["total"]

    igual(total(papel="DESTINATARIO"), 2,
          "papel DESTINATARIO — o resumo NÃO entra: `resNFe` não nomeia "
          "destinatário, então o papel dele é indeterminado")
    igual(total(papel="EMITENTE"), 1, "papel EMITENTE")
    igual(total(papel="TRANSPORTADOR"), 1, "papel TRANSPORTADOR")
    igual(total(papel="compra"), 2, "o vocabulário antigo (`compra`) ainda vale")
    igual(total(papel="frete"), 1, "e `frete` também")
    igual(total(papel="todas"), 5, "`todas` não filtra")
    igual(total(papel="OUTRO"), 1, "e o resumo aparece como indeterminado")

    igual(total(cancelada="1"), 1, "cancelada pela situação ATUAL")
    igual(total(cancelada="0"), 4, "e o inverso")
    igual(total(situacao_atual="CANCELADO"), 1, "por situação atual")
    igual(total(chave=ch["recebida"]), 1, "por chave")
    igual(total(numero="2"), 1, "por número")
    igual(total(conteudo="RESUMO"), 1, "só resumos")
    igual(total(conteudo="COMPLETO"), 4, "só completos")
    igual(total(com_evento="1"), 1, "com evento")
    igual(total(cfop="5102"), 4, "por CFOP")
    igual(total(ncm="84713012"), 4, "por NCM")
    igual(total(texto="PRODUTO"), 4, "texto livre")
    igual(total(data_ini="2030-01-01"), 0, "período sem documento")
    igual(total(papel="DESTINATARIO", cancelada="1"), 1, "filtros combinados")

    secao("Filtro inválido é 400 com motivo, nunca lista vazia")
    levanta(lambda: main.nfe_notas(Req(papel="INVENTADO"), CONTA),
            "papel desconhecido é recusado", status=400)
    levanta(lambda: main.nfe_notas(Req(data_ini="31/12/2026"), CONTA),
            "data em formato errado é recusada", status=400)
    levanta(lambda: main.nfe_notas(Req(), CONTA, ordenar_por="; DROP"),
            "ordenação fora da lista é recusada", status=400)


# ══════════════════════════════════════════════════════════════════════════
secao("Cards: honestos por construção")
with apoio.raiz_temporaria("nfe5_") as raiz:
    montar(raiz)
    main = carregar_main(raiz)
    c = main.nfe_resumo(Req(), CONTA)

    igual(c["recebidas"]["quantidade"], 2, "recebidas = DESTINATARIO")
    igual(c["recebidas"]["valor"], 1400.0, "e o valor só delas")
    igual(c["emitidas"]["quantidade"], 1, "emitidas = EMITENTE")
    igual(c["transportes"]["quantidade"], 1, "transportes = TRANSPORTADOR")
    igual(c["canceladas"]["quantidade"], 1, "canceladas pela situação atual")
    igual(c["somente_resumo"]["quantidade"], 1, "só resumo")
    igual(c["total_documentos"], 5, "e o total")

    secao("O frete de terceiro NÃO entra no card de recebidas")
    ok(c["recebidas"]["valor"] < c["transportes"]["valor"],
       "os R$ 9.000 do frete ficam fora das recebidas")
    igual(c["recebidas"]["valor"] + c["emitidas"]["valor"]
          + c["transportes"]["valor"], 12400.0,
          "cada real está em um card só")

    secao("As advertências viajam no próprio JSON")
    for chave in ("recebidas", "transportes", "com_reforma", "periodo"):
        ok(chave in c["advertencias"], f"advertência sobre `{chave}`")
    ok("compra tributável" in c["advertencias"]["recebidas"],
       "e ela diz o que o número NÃO é")
    ok("dhSaiEnt" in c["advertencias"]["periodo"],
       "e que o período é por emissão, não por entrada")


# ══════════════════════════════════════════════════════════════════════════
secao("Detalhe do documento")
with apoio.raiz_temporaria("nfe5_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    d = main.nfe_documento(CONTA, chave=ch["cancelada"])
    doc = d["documento"]
    igual(doc["situacao"], dm.AUTORIZADO, "o documento original")
    igual(doc["situacao_atual"], dm.CANCELADO, "e a situação de hoje")
    ok(doc["cancelada"], "marcada como cancelada")
    igual(len(d["eventos"]), 2, "os dois eventos vêm juntos")
    ok(any(e["tp_evento"] == "110111" for e in d["eventos"]),
       "o cancelamento entre eles")
    ok(any(e["tp_evento"] == "510630" for e in d["eventos"]),
       "e o de trânsito, para a tela poder recolher")
    ok(len(d["itens"]) > 0, "com os itens")

    secao("Resumo não inventa itens")
    dr = main.nfe_documento(CONTA, chave=ch["resumo"])
    igual(dr["itens"], [], "resumo não tem itens — e a lista vem vazia")
    igual(dr["documento"]["conteudo"], cq.RESUMO, "marcado como resumo")

    secao("Ausência é ausência, não zero")
    it = d["itens"][0]
    ok(it["ipi"]["presente"] is False or it["ipi"]["ipi_cst"] is not None,
       "bloco ausente se declara ausente")
    for bloco in ("icms", "pis", "cofins", "ipi", "reforma"):
        ok("presente" in it[bloco], f"o bloco `{bloco}` diz se veio")

    secao("Documento inexistente e empresa errada")
    levanta(lambda: main.nfe_documento(CONTA, chave="9" * 44),
            "chave inexistente é 404", status=404)
    levanta(lambda: main.nfe_documento(CONTA),
            "sem chave nem id é 400", status=400)
    levanta(lambda: main.nfe_documento("id-que-nao-existe", chave=ch["recebida"]),
            "empresa inexistente é 404", status=404)


# ══════════════════════════════════════════════════════════════════════════
secao("XML: o original do acervo, e nada além dele")
with apoio.raiz_temporaria("nfe5_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    r = main.nfe_xml(CONTA, chave=ch["recebida"])
    igual(r.media_type, "application/xml", "servido como XML")
    ok(b"<nfeProc" in r.body, "e é o documento")

    # o byte a byte precisa bater com o acervo
    ac = acv.abrir(raiz, EMPRESA_A)
    d = cq.detalhe(raiz, EMPRESA_A, chave=ch["recebida"])
    caminho = ac.caminho_canonico(d["documento"]["especie"],
                                  d["documento"]["id_documento"])
    igual(hashlib.sha256(r.body).hexdigest(),
          hashlib.sha256(caminho.read_bytes()).hexdigest(),
          "byte a byte igual ao original do acervo — não reconstruído")

    baixa = main.nfe_xml(CONTA, chave=ch["recebida"], baixar=True)
    disp = baixa.headers.get("content-disposition", "")
    ok("attachment" in disp, "o download vem como anexo")
    ok(ch["recebida"] in disp, "com o nome derivado da CHAVE")
    ok("acervo" not in disp and ":\\" not in disp and "/" not in disp.split('"')[1],
       "e sem nada do caminho em disco no nome")

    secao("Segurança: nenhum caminho vem do navegador")
    for maligno in ("../../etc/passwd", "..\\..\\certificados.json",
                    "C:\\Windows\\win.ini", "%2e%2e%2f", "'; DROP TABLE--"):
        levanta(lambda m=maligno: main.nfe_xml(CONTA, chave=m),
                f"recusa {maligno[:22]!r}", status=404)
    ok("caminho_canonico" in _FONTE,
       "o caminho nasce da identidade, via `acervo.caminho_canonico`")

    secao("Isolamento entre empresas")
    outra_raiz_ch = chave_ficticia(FORNECEDOR, 77)
    levanta(lambda: main.nfe_xml(CONTA, chave=outra_raiz_ch),
            "documento de fora do acervo desta empresa é 404", status=404)


# ══════════════════════════════════════════════════════════════════════════
secao("Os documentos que só existiam no acervo aparecem")
with apoio.raiz_temporaria("nfe5_") as raiz:
    ch = montar(raiz)     # tudo foi para o ACERVO, nada para a pasta legada
    main = carregar_main(raiz)

    r = pv.invisiveis(raiz, EMPRESA_A)
    ok(r["invisiveis_para_o_legado"] > 0,
       "o leitor legado não enxerga estes documentos")
    igual(r["ainda_invisiveis"], 0,
          "e a consulta — a fonte da tela — enxerga TODOS")
    ok(r["resolvido"], "logo, nenhum documento capturado fica invisível")

    # e a API os devolve de verdade
    api = main.nfe_notas(Req(), CONTA, tamanho=50)
    devolvidas = {n["chave"] for n in api["notas"]}
    for rotulo in ("recebida", "emitida", "transporte", "cancelada", "resumo"):
        ok(ch[rotulo] in devolvidas, f"a API devolve `{rotulo}`")


# ══════════════════════════════════════════════════════════════════════════
secao("Empresa sem índice: estado próprio, não erro")
with apoio.raiz_temporaria("nfe5_") as raiz:
    import json
    (Path(raiz) / "certs").mkdir(parents=True, exist_ok=True)
    (Path(raiz) / "certs" / "E.pfx").write_bytes(b"X")
    (Path(raiz) / "certificados.json").write_text(json.dumps([{
        "id": "conta-sem-acervo", "cnpj": OUTRA, "nome": "SEM ACERVO LTDA",
        "caminho": "certs/E.pfx"}]), encoding="utf-8")
    main = carregar_main(raiz)

    r = main.nfe_notas(Req(), "conta-sem-acervo", tamanho=50)
    igual(r["notas"], [], "lista vazia")
    igual(r["pagina"]["total"], 0, "total zero")
    ok(r["sincronizado"] is False,
       "e `sincronizado: false` — a tela distingue 'nunca capturou' de "
       "'período vazio'")
    ok(not (Path(raiz) / OUTRA / "indice").exists(),
       "e consultar NÃO cria índice para ela")


# ══════════════════════════════════════════════════════════════════════════
secao("A tela existe, e é servida")
_HTML = (RAIZ / "web" / "nfe_documentos.html").read_text("utf-8")
ok("/api/nfe/notas" in _HTML, "a tela chama a API de listagem")
ok("/api/nfe/resumo" in _HTML, "e a de resumo")
ok("/api/nfe/documento" in _HTML, "e a de detalhe")
ok("/api/nfe/xml" in _HTML, "e a de XML")
for proibido in ("documentos.db", "sqlite", "/acervo/", "C:\\\\"):
    ok(proibido not in _HTML, f"a tela não conhece `{proibido}`")
ok("situacao_atual" in _HTML, "usa a situação ATUAL como verdade operacional")
ok("Documento original" in _HTML,
   "e mostra a original quando as duas diferem")
ok("lista-mobile" in _HTML and "@media (max-width:900px)" in _HTML,
   "tem apresentação própria para celular")
ok("aria-pressed" in _HTML and "aria-live" in _HTML,
   "tem atributos de acessibilidade")
ok("pt-BR" in _HTML, "formata valores em pt-BR")
# Na NF-e 5 aqui se cobrava o aviso "DANFE — em preparação": era o certo
# enquanto não havia gerador. A NF-e 6 fez o gerador, então o que se cobra é
# que o DANFE exista de verdade e continue se declarando auxiliar.
ok("Visualizar DANFE" in _HTML, "o DANFE saiu do 'em preparação' e existe")
ok("representação auxiliar" in _HTML,
   "e continua se declarando representação auxiliar do XML")

_NAV = (RAIZ / "web" / "fiscale-nav.js").read_text("utf-8")
ok("nfe_documentos.html" in _NAV, "a tela está no menu")


# ══════════════════════════════════════════════════════════════════════════
secao("A interface não move nada")
with apoio.raiz_temporaria("nfe5_") as raiz:
    montar(raiz)
    main = carregar_main(raiz)
    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO)
    cp.ult_nsu = "000000000000321"
    repo.salvar(cp)
    pasta = Path(raiz) / EMPRESA_A / "ingestao"
    antes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(pasta.glob("*.json"))}
    acervo_antes = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml")}

    for _ in range(3):
        main.nfe_notas(Req(papel="DESTINATARIO"), CONTA)
        main.nfe_resumo(Req(), CONTA)
        main.nfe_documento(CONTA, chave=chave_ficticia(FORNECEDOR, 1))

    igual({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(pasta.glob("*.json"))}, antes,
          "nenhum arquivo de checkpoint foi tocado")
    igual(repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO).ult_nsu,
          "000000000000321", "o ultNSU continua onde estava")
    igual({p: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml")},
          acervo_antes, "e o acervo está intacto")


secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS_DE_REDE, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede NÃO estava ativa")
except AssertionError:
    ok(True, "e a trava estava mesmo ativa (conferido)")
_socket.socket.connect = _connect_original


print("\n" + "=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  - " + e)
    sys.exit(1)
print("NF-e 5: a interface lê o índice, verde.")
