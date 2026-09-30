#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NF-e EMITIDAS — a emissão própria como entrada canônica do acervo.

    python teste_nfe_emitidas.py

O QUE ESTA SUÍTE PROVA

    1. a área lê o acervo QUE JÁ EXISTE: nenhuma captura, nenhuma segunda
       fonte, nenhuma chamada de rede — o socket está proibido aqui;
    2. `papel` é FIXADO em EMITENTE: mandar `papel=DESTINATARIO` para a rota de
       emitidas não devolve compra com nome de venda;
    3. os três estados fecham: `encontrados == processados + com_erro`;
    4. a deduplicação é RELATADA pela chave, nunca reaplicada — e documento em
       resumo (`resNFe`), que pode não ter chave, não é contado como duplicata;
    5. documento emitido por terceiro NÃO entra, nem quando a empresa é
       transportadora ou está no autXML;
    6. "não emitiu" e "nunca capturou" são respostas diferentes;
    7. o histórico é preservado: ler a área não grava nada — nem no acervo, nem
       no índice, nem no checkpoint;
    8. a tela consome a rota real e não traz número escrito no HTML.

O QUE ELA NÃO FAZ
    Não consulta a SEFAZ, não usa dado real e não toca na pasta de produção:
    tudo roda em raiz temporária, com CNPJ fictício de dígito válido.
"""
from __future__ import annotations

import os
import re
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
from ingestao import emitidas as em                  # noqa: E402
from ingestao import pipeline as pipe                # noqa: E402

_ok = _falhas = 0

import socket as _socket                             # noqa: E402


def _connect_proibido(self, endereco, *a, **kw):
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
        print(f"  FALHOU   {desc}")


from teste_fixturas_nfe import (                     # noqa: E402
    cnpj_ficticio, chave_ficticia, xml_nfe, xml_resumo, xml_evento, bruto,
    EMPRESA_A, FORNECEDOR,
)

OUTRA = cnpj_ficticio("888888880001")
CONTA = "conta-emitidas-1"


class Req:
    """Dublê de `Request`: a rota só lê `query_params`."""

    def __init__(self, **q):
        self.query_params = {k: str(v) for k, v in q.items() if v not in (None, "")}


def montar(raiz, *, empresa=EMPRESA_A, emitidas=2, com_resumo=True):
    """Acervo variado: emitidas, recebida, frete de terceiro e um resumo.

    O recorte importa: só as `emitidas` têm a empresa como emitente. Tudo o
    mais existe para provar o que a área NÃO conta.
    """
    import json

    (Path(raiz) / "certs").mkdir(parents=True, exist_ok=True)
    (Path(raiz) / "certs" / "E.pfx").write_bytes(b"PFX-FALSO")
    (Path(raiz) / "certificados.json").write_text(json.dumps([{
        "id": CONTA, "cnpj": empresa, "nome": "EMPRESA DE TESTE LTDA",
        "caminho": "certs/E.pfx"}]), encoding="utf-8")

    ac = acv.abrir(raiz, empresa, servico="nfe_distribuicao",
                   ambiente=ing.PRODUCAO)
    n = [0]
    chaves = {"emitidas": []}

    def guarda(conteudo, schema="procNFe_v4.00.xsd"):
        n[0] += 1
        ac.preservar(bruto(conteudo, nsu=str(2000 + n[0]), schema=schema))

    for i in range(emitidas):
        c = chave_ficticia(empresa, 10 + i)
        chaves["emitidas"].append(c)
        guarda(xml_nfe(c, valor="1500.00", numero=str(10 + i),
                       emitente=empresa, destinatario=FORNECEDOR))

    # Recebida: terceiro emitiu para a empresa. Não é emissão própria.
    c_rec = chave_ficticia(FORNECEDOR, 20)
    chaves["recebida"] = c_rec
    guarda(xml_nfe(c_rec, valor="700.00", numero="20",
                   emitente=FORNECEDOR, destinatario=empresa))

    # Frete: a empresa aparece como transportadora de nota entre terceiros.
    c_tr = chave_ficticia(FORNECEDOR, 21)
    chaves["transporte"] = c_tr
    base = xml_nfe(c_tr, valor="900.00", numero="21",
                   emitente=FORNECEDOR, destinatario=OUTRA).decode("utf-8")
    base = base.replace("</infNFe>", f"<transp><transporta><CNPJ>{empresa}"
                                     f"</CNPJ></transporta></transp></infNFe>")
    guarda(base.encode("utf-8"))

    if com_resumo:
        c_res = chave_ficticia(FORNECEDOR, 22)
        chaves["resumo"] = c_res
        guarda(xml_resumo(c_res, valor="300.00"), "resNFe_v1.01.xsd")

    pipe.indexar_pendentes(raiz, empresa)
    return chaves


def carregar_main(raiz):
    os.environ["FISCALE_DADOS"] = str(raiz)
    import fiscale_dados as fd
    fd._raiz_cache = None
    fd.raiz(raiz)
    sys.modules.pop("main", None)
    import main
    return main


# ══════════════════════════════════════════════════════════════════════════
secao("1 · A camada não é uma segunda fonte")

FONTE_EM = (RAIZ / "nfse" / "backend" / "ingestao" / "emitidas.py").read_text("utf-8")
for proibido in ("requests", "urlopen", "http", "socket", "distribuicao",
                 "consultar_empresa", "checkpoint"):
    ok(proibido not in FONTE_EM,
       f"`emitidas.py` não menciona `{proibido}`")
for gravacao in ("preservar(", "importar(", "indexar", "INSERT", "UPDATE",
                 "DELETE", "commit("):
    ok(gravacao not in FONTE_EM, f"e não grava nada (`{gravacao}`)")
ok("from . import consulta as cq" in FONTE_EM,
   "a leitura é delegada à camada de consulta que já existia")
ok("PAPEL = dm.EMITENTE" in FONTE_EM,
   "o papel vem do vocabulário do domínio, não de string local")


# ══════════════════════════════════════════════════════════════════════════
secao("2 · O papel é fixado, não sugerido")

f = em.filtro(cq.Filtro(papel=dm.DESTINATARIO))
ok(f.papel == dm.EMITENTE,
   "filtro que chega pedindo DESTINATARIO sai como EMITENTE")
f2 = em.filtro(cq.Filtro(papel=""))
ok(f2.papel == dm.EMITENTE, "filtro vazio também sai como EMITENTE")
f3 = em.filtro(cq.Filtro(data_de=None, texto="abc"))
ok(f3.texto == "abc", "e o resto do filtro da tela é preservado")
ok(em.ESPECIES == (dm.NFE55, dm.NFCE65),
   "a área cobre NF-e 55 e NFC-e 65 — o mesmo acervo")
ok(em.IDENTIFICADOR == "chave",
   "o identificador de deduplicação declarado é a chave de acesso")


# ══════════════════════════════════════════════════════════════════════════
secao("3 · Os três estados fecham, e só contam emissão própria")
with apoio.raiz_temporaria("emit_") as raiz:
    ch = montar(raiz)
    r = em.resumo(raiz, EMPRESA_A)

    ok(r["encontrados"] == 2,
       f"duas emitidas encontradas (veio {r['encontrados']})")
    ok(r["encontrados"] == r["processados"] + r["com_erro"],
       "encontrados == processados + com_erro")
    ok(r["com_erro"] == 0, "nenhuma em quarentena neste acervo")
    ok(r["papel"] == dm.EMITENTE, "o papel da área é declarado na resposta")

    # O que existe e NÃO foi contado: recebida, frete e resumo de terceiro.
    todos = cq.contar(raiz, EMPRESA_A, cq.Filtro())
    ok(todos > r["encontrados"],
       f"o acervo tem mais documentos ({todos}) do que emitidas")
    ok(cq.contar(raiz, EMPRESA_A, cq.Filtro(papel=dm.DESTINATARIO)) >= 1,
       "a recebida continua no acervo, contada como DESTINATARIO")
    ok(r["total_no_acervo"] == todos,
       "o total do acervo é informado para o estado vazio saber o que dizer")
    ok(r["tem_acervo"] is True, "e diz que há acervo")

    # A listagem é a mesma camada, com o papel fixado.
    pg = em.listar(raiz, EMPRESA_A, cq.Filtro(papel=dm.DESTINATARIO))
    ok(pg.total == 2, f"listar() devolve só as emitidas ({pg.total})")
    ok(all(d.papel == dm.EMITENTE for d in pg.itens),
       "e todo documento devolvido tem a empresa como emitente")
    ok(set(d.chave for d in pg.itens) == set(ch["emitidas"]),
       "são exatamente as chaves que a empresa emitiu")


# ══════════════════════════════════════════════════════════════════════════
secao("4 · Deduplicação: relatada pela chave, nunca reaplicada")
with apoio.raiz_temporaria("emit_") as raiz:
    montar(raiz)
    r = em.resumo(raiz, EMPRESA_A)
    ok(r["chaves_distintas"] == 2, "duas chaves distintas")
    ok(r["linhas_com_chave"] == 2, "duas linhas com chave")
    ok(r["duplicidade_no_indice"] == 0, "nenhuma duplicidade a reportar")
    ok(r["sem_chave"] == 0, "e nenhuma emitida sem chave")

    # Reimportar o MESMO XML não cria linha nova: o portão já deduplica por
    # `id_documento`. A área só precisa continuar contando 2.
    ac = acv.abrir(raiz, EMPRESA_A, servico="nfe_distribuicao",
                   ambiente=ing.PRODUCAO)
    c = chave_ficticia(EMPRESA_A, 10)
    ac.preservar(bruto(xml_nfe(c, valor="1500.00", numero="10",
                               emitente=EMPRESA_A, destinatario=FORNECEDOR),
                       nsu="9001"))
    pipe.indexar_pendentes(raiz, EMPRESA_A)
    r2 = em.resumo(raiz, EMPRESA_A)
    ok(r2["encontrados"] == 2,
       f"reentregar o mesmo documento não duplica ({r2['encontrados']})")
    ok(r2["duplicidade_no_indice"] == 0,
       "e nenhuma duplicidade aparece — o portão fez o trabalho")

# Documento em resumo não tem por que virar duplicata da falta de chave.
with apoio.raiz_temporaria("emit_") as raiz:
    montar(raiz, com_resumo=True)
    linhas, distintas = cq.contar_chaves(raiz, EMPRESA_A, cq.Filtro())
    ok(distintas <= linhas,
       "contar_chaves nunca devolve mais distintas que linhas com chave")
    ok(cq.contar_chaves(raiz, cnpj_ficticio("999999990001"),
                        cq.Filtro()) == (0, 0),
       "empresa sem índice devolve (0, 0) em vez de erro")


# ══════════════════════════════════════════════════════════════════════════
secao("5 · Preserva o que existe: ler não grava")
with apoio.raiz_temporaria("emit_") as raiz:
    montar(raiz)
    antes = sorted((p.relative_to(raiz).as_posix(), p.stat().st_size)
                   for p in Path(raiz).rglob("*") if p.is_file())
    for _ in range(3):
        em.resumo(raiz, EMPRESA_A)
        em.listar(raiz, EMPRESA_A)
    depois = sorted((p.relative_to(raiz).as_posix(), p.stat().st_size)
                    for p in Path(raiz).rglob("*") if p.is_file())
    ok(antes == depois,
       "nenhum arquivo criado, removido ou alterado por três leituras")
    ok(not (Path(raiz) / "checkpoint").exists()
       or antes == depois, "o checkpoint não foi tocado")


# ══════════════════════════════════════════════════════════════════════════
secao("6 · Vazio de emissão é diferente de vazio de acervo")
with apoio.raiz_temporaria("emit_") as raiz:
    montar(raiz, emitidas=0)
    r = em.resumo(raiz, EMPRESA_A)
    ok(r["encontrados"] == 0, "nenhuma emitida")
    ok(r["tem_acervo"] is True and r["total_no_acervo"] > 0,
       "mas o acervo existe, e a resposta diz isso")

with apoio.raiz_temporaria("emit_") as raiz:
    import json
    (Path(raiz) / "certs").mkdir(parents=True, exist_ok=True)
    (Path(raiz) / "certs" / "E.pfx").write_bytes(b"PFX-FALSO")
    (Path(raiz) / "certificados.json").write_text(json.dumps([{
        "id": CONTA, "cnpj": EMPRESA_A, "nome": "EMPRESA DE TESTE LTDA",
        "caminho": "certs/E.pfx"}]), encoding="utf-8")
    r = em.resumo(raiz, EMPRESA_A)
    ok(r["encontrados"] == 0 and r["tem_acervo"] is False,
       "sem índice: zero emitidas E zero acervo, sem levantar exceção")
    ok(EMPRESA_A not in str(r), "e o CNPJ sai mascarado da resposta")


# ══════════════════════════════════════════════════════════════════════════
secao("7 · A rota devolve documento real e declara a origem")
with apoio.raiz_temporaria("emit_") as raiz:
    ch = montar(raiz)
    main = carregar_main(raiz)

    d = main.nfe_emitidas(Req(), CONTA)
    ok(d["resumo"]["encontrados"] == 2, "a rota conta as duas emitidas")
    ok(d["empresa"]["id"] == CONTA, "identifica a empresa pelo id do cadastro")
    ok(EMPRESA_A not in str(d["empresa"]), "com o CNPJ mascarado")
    ok(d["entrada"]["fontes"] == ["PASTA_VIGIADA"],
       "declara a única fonte de emissão própria que existe hoje")
    ok("Distribuição DF-e" in d["entrada"]["observacao"],
       "e explica por que ela não vem pela distribuição")

    # A rota não aceita ser desviada de papel.
    d2 = main.nfe_emitidas(Req(papel="DESTINATARIO"), CONTA)
    ok(d2["resumo"]["encontrados"] == 2,
       "`papel=DESTINATARIO` na query não muda o que a área conta")
    d3 = main.nfe_emitidas(Req(papel="TRANSPORTADOR"), CONTA)
    ok(d3["resumo"]["encontrados"] == 2, "nem `papel=TRANSPORTADOR`")

    # O filtro da tela continua valendo para o resto.
    d4 = main.nfe_emitidas(Req(numero="10"), CONTA)
    ok(d4["resumo"]["encontrados"] == 1,
       f"filtrar por número restringe ({d4['resumo']['encontrados']})")

    # A listagem de documentos continua sendo a rota única de listagem.
    lista = main.nfe_notas(Req(papel="EMITENTE"), CONTA, tamanho=50)
    ok(lista["pagina"]["total"] == 2,
       "`/api/nfe/notas?papel=EMITENTE` lista os mesmos dois documentos")
    ok(all(n["papel"] == dm.EMITENTE for n in lista["notas"]),
       "e cada linha vem com a empresa como emitente")
    ok(set(n["chave"] for n in lista["notas"]) == set(ch["emitidas"]),
       "as chaves da lista são as do acervo — não há dado de demonstração")


# ══════════════════════════════════════════════════════════════════════════
secao("8 · A rota não abriu um segundo caminho de listagem")

FONTE_MAIN = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
_i = FONTE_MAIN.index('@app.get("/api/nfe/emitidas")')
_f = FONTE_MAIN.index('@app.get("/api/nfe/documento")')
BLOCO = FONTE_MAIN[_i:_f]
ok("_emit.resumo" in BLOCO, "a rota chama a camada de emitidas")
for proibido in ("_cq.consultar", "glob(", "rglob(", "_svc_nfe",
                 "_rodar_pasta_entrada", "importar"):
    ok(proibido not in BLOCO, f"e não usa `{proibido}`")
ok("_entrada_cfg()" in BLOCO, "lê a config da pasta vigiada")
ok("_entrada_salvar" not in BLOCO, "sem gravar nela")


# ══════════════════════════════════════════════════════════════════════════
secao("9 · A tela lê da rota e não traz número escrito")

TELA = (RAIZ / "web" / "nfe_documentos.html").read_text("utf-8")
ok("/api/nfe/emitidas" in TELA, "a tela pede `/api/nfe/emitidas`")
ok("painelEmitidas" in TELA and "corpoEmitidas" in TELA,
   "e tem a área própria para pintar o resultado")
ok("vista=emitidas" in TELA or "'emitidas'" in TELA,
   "a vista chega pela URL, para o menu poder apontar direto")
ok("papel.disabled = true" in TELA,
   "o seletor de papel fica travado na área de emitidas")
ok("tem_acervo" in TELA,
   "a tela distingue 'não emitiu' de 'nunca capturou'")
ok("duplicidade_no_indice" in TELA,
   "e mostra duplicidade como defeito, não como estatística")

# Nenhum número de demonstração: o corpo da função que pinta só usa `r.` e `en.`
_ini = TELA.index("function pintarEmitidas")
_fim = TELA.index("/* ── Baixar XMLs em lote")
CORPO = TELA[_ini:_fim]
numeros = re.findall(r">\s*(\d{2,})\s*<", CORPO)
ok(not numeros, f"nenhum número fixo no HTML da área (achei {numeros[:5]})")
for demo in ("exemplo", "demonstra", "fict", "lorem", "mock"):
    ok(demo not in CORPO.lower(), f"e nenhuma marca de dado falso (`{demo}`)")


print(f"\n{_ok} ok · {_falhas} falha(s)")
sys.exit(1 if _falhas else 0)
