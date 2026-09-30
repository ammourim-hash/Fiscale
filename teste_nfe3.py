#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da NF-e 3 — migração dos XML legados para o acervo.

    python teste_nfe3.py

O QUE ESTA SUÍTE PROVA
    1. Que o ensaio (`--dry-run`) decide pelo MESMO caminho da migração real —
       um ensaio que decidisse por outro caminho não valeria como ensaio.
    2. Que a migração é **cópia**: o legado termina byte a byte como começou.
    3. Que rodar duas vezes produz o mesmo estado — idempotência por
       construção, não por contador.
    4. Que nada disso toca rede, checkpoint ou scheduler.

NADA AQUI TOCA A PASTA REAL
    `teste_apoio.raiz_temporaria()` liga a trava contra `~/Fiscale/dados`.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402
import ingestao as ing                               # noqa: E402
from ingestao import acervo as acv                   # noqa: E402
from ingestao import consulta as cq
from ingestao import documento as dm                  # noqa: E402
from ingestao import equivalencia as eqv             # noqa: E402
from ingestao import importacao as imp               # noqa: E402
from ingestao import indice as idx                   # noqa: E402
from ingestao import migracao_legado_xml as mig      # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

# ── trava de rede ───────────────────────────────────────────────────────────
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


from teste_fixturas_nfe import (                     # noqa: E402
    cnpj_ficticio, chave_ficticia, xml_nfe, xml_resumo, xml_evento,
    EMPRESA_A, FORNECEDOR, XML_MALFORMADO,
)

OUTRA_EMPRESA = cnpj_ficticio("888888880001")


# ══════════════════════════════════════════════════════════════════════════
# Cenário legado: os arquivos como o `nfe.py` os gravava — por NSU, soltos.
# ══════════════════════════════════════════════════════════════════════════
def montar_legado(raiz, empresa=EMPRESA_A):
    pasta = Path(raiz) / empresa / "nfe"
    pasta.mkdir(parents=True, exist_ok=True)
    chaves = {}

    def grava(nsu, sufixo, conteudo):
        (pasta / f"{nsu:015d}-{sufixo}.xml").write_bytes(conteudo)

    # 3 notas completas
    for n in (1, 2, 3):
        c = chave_ficticia(FORNECEDOR, n)
        chaves[f"c{n}"] = c
        grava(1000 + n, "procNFe", xml_nfe(c, valor=f"{n}000.00", numero=str(n)))

    # 1 resumo SEM completo — é o caso que a NF-e 3 descobriu que se perderia
    c4 = chave_ficticia(FORNECEDOR, 4)
    chaves["c4_so_resumo"] = c4
    grava(1004, "resNFe", xml_resumo(c4, valor="4000.00"))

    # 1 resumo COM completo — não pode rebaixar o canônico
    c5 = chave_ficticia(FORNECEDOR, 5)
    chaves["c5"] = c5
    grava(1005, "procNFe", xml_nfe(c5, valor="5000.00", numero="5"))
    grava(1006, "resNFe", xml_resumo(c5, valor="5000.00"))

    # 1 evento
    grava(1007, "procEventoNFe", xml_evento(chaves["c1"], tp="110111"))

    # 1 XML malformado — preservável, não interpretável
    grava(1008, "procNFe", XML_MALFORMADO)

    # 1 nota de OUTRA empresa (a empresa não participa dela)
    c9 = chave_ficticia(cnpj_ficticio("999999990001"), 9)
    chaves["c9_alheia"] = c9
    grava(1009, "procNFe", xml_nfe(c9, valor="9000.00",
                                   emitente=cnpj_ficticio("999999990001"),
                                   destinatario=OUTRA_EMPRESA, numero="9"))

    # 1 duplicata DENTRO do próprio legado: mesmo conteúdo, outro NSU
    grava(1010, "procNFe", xml_nfe(chaves["c1"], valor="1000.00", numero="1"))
    return chaves


def hashes_do_legado(raiz, empresa=EMPRESA_A) -> dict:
    pasta = Path(raiz) / empresa / "nfe"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(pasta.glob("*.xml"))}


# ══════════════════════════════════════════════════════════════════════════
secao("A migração não conhece a SEFAZ")

_FONTE = (RAIZ / "nfse" / "backend" / "ingestao"
          / "migracao_legado_xml.py").read_text("utf-8")
_importados = set()
for no in ast.walk(ast.parse(_FONTE)):
    if isinstance(no, ast.Import):
        _importados |= {a.name.split(".")[0] for a in no.names}
    elif isinstance(no, ast.ImportFrom) and no.module:
        _importados.add(no.module.split(".")[0])

for proibido in ("requests", "socket", "urllib", "ssl", "conectores",
                 "checkpoint", "controlador", "servico_distribuicao",
                 "piloto_nfe"):
    ok(proibido not in _importados, f"não importa `{proibido}`")

_sem_doc = _FONTE.split('"""', 2)[-1]
_codigo = "\n".join(l for l in _sem_doc.splitlines()
                    if not l.lstrip().startswith("#"))
for termo in ("distNSU", "consNSU", "consChNFe", "ult_nsu", "max_nsu",
              "avancar", "NFeDistribuicaoDFe"):
    ok(termo not in _codigo, f"nenhum uso de `{termo}` no código")


# ══════════════════════════════════════════════════════════════════════════
secao("Inventário — a fotografia do estado anterior")
with apoio.raiz_temporaria("nfe3_") as raiz:
    montar_legado(raiz)
    inv = mig.inventariar(raiz, EMPRESA_A)
    igual(inv.arquivos, 10, "conta os 10 arquivos legados")
    igual(inv.por_pasta.get("nfe"), 10, "todos na pasta `nfe`")
    igual(inv.por_tipo.get("procNFe"), 7,
          "7 arquivos com cara de completo (inclui o malformado, o alheio e a "
          "duplicata) — o inventário conta ARQUIVO, não documento")
    igual(inv.por_tipo.get("resNFe"), 2, "2 resumos")
    igual(inv.por_tipo.get("procEventoNFe"), 1, "1 evento")
    ok(inv.bytes > 0, "e soma os bytes")
    igual(inv.documentos_no_acervo, 0, "o acervo começa vazio")

    igual(mig.inventariar(raiz, "não é cnpj").arquivos, 0,
          "identidade inválida devolve inventário vazio, sem levantar")
    igual(mig.empresas_com_legado(raiz), [EMPRESA_A],
          "e a carteira descobre quem tem pasta legada")


# ══════════════════════════════════════════════════════════════════════════
secao("Manifesto pré-migração")
with apoio.raiz_temporaria("nfe3_") as raiz:
    montar_legado(raiz)
    destino = Path(apoio.pasta_temp("nfe3_manif_"))
    m = mig.manifesto_pre_migracao(raiz, destino=destino)
    ok(Path(m["arquivo"]).exists(), "o manifesto é gravado")
    igual(m["arquivos_catalogados"], 10, "com o hash de cada arquivo legado")
    ok(len(m["sha256"]) == 64, "e o próprio manifesto tem SHA-256")
    ok((destino / (Path(m["arquivo"]).name.replace(".manifesto.json", ".sha256")
                   )).exists(), "gravado ao lado, em arquivo próprio")

    corpo = json.loads(Path(m["arquivo"]).read_text("utf-8"))
    texto = json.dumps(corpo, ensure_ascii=False)
    ok(".pfx" not in texto and "senha" not in texto.lower(),
       "o manifesto não carrega certificado nem segredo")

    c = mig.conferir_manifesto(raiz, m["arquivo"])
    ok(c["intacto"], "e a conferência confirma que nada mudou")
    igual(c["conferidos"], 10, "conferindo os 10")

    (Path(raiz) / EMPRESA_A / "nfe" / f"{1001:015d}-procNFe.xml").write_bytes(b"<x/>")
    c2 = mig.conferir_manifesto(raiz, m["arquivo"])
    ok(not c2["intacto"], "um arquivo alterado é detectado")
    igual(len(c2["alterados"]), 1, "e nomeado")


# ══════════════════════════════════════════════════════════════════════════
secao("Ensaio (--dry-run) — decide e não escreve")
with apoio.raiz_temporaria("nfe3_") as raiz:
    chaves = montar_legado(raiz)
    antes = hashes_do_legado(raiz)

    r = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=True)
    ok(r.ensaio, "o relatório se declara ensaio")
    igual(r.arquivos, 10, "percorreu os 10 arquivos")
    ok(r.explicados, "todo arquivo recebeu um veredito nomeado")
    igual(len(r.itens), 10, "um veredito por arquivo, nenhum sem")

    v = r.por_veredito
    igual(v.get(mig.MIGRARIA), 5, "5 migrariam (3 notas + resumo órfão + c5)")
    igual(v.get(mig.EVENTO), 1, "1 evento")
    igual(v.get(mig.COPIA_MENOR), 1, "1 resumo que não rebaixaria o completo")
    igual(v.get(mig.DUPLICADO), 1, "1 duplicata dentro do próprio legado")
    igual(v.get(mig.XML_INVALIDO), 1, "1 XML inválido")
    igual(v.get(mig.EMPRESA_NAO_IDENTIFICADA), 1, "1 nota alheia à empresa")
    igual(r.bloqueantes, [], "nenhum bloqueante")

    secao("E o ensaio não escreveu NADA")
    ok(not (Path(raiz) / EMPRESA_A / "acervo").exists(),
       "o acervo não foi criado")
    ok(not (Path(raiz) / EMPRESA_A / idx.PASTA / idx.ARQUIVO).exists(),
       "o índice não foi criado")
    igual(hashes_do_legado(raiz), antes, "e o legado está intacto")


# ══════════════════════════════════════════════════════════════════════════
secao("Migração real")
with apoio.raiz_temporaria("nfe3_") as raiz:
    chaves = montar_legado(raiz)
    antes_legado = hashes_do_legado(raiz)
    ensaio = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=True)

    r = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)
    ok(not r.ensaio, "o relatório se declara execução real")
    igual(r.arquivos, 10, "percorreu os mesmos 10")
    ok(r.explicados, "todo arquivo explicado")

    secao("O ensaio previu o que aconteceu")
    igual(r.por_veredito, ensaio.por_veredito,
          "veredito por veredito, ensaio e execução dizem o mesmo")

    secao("O LEGADO NÃO FOI TOCADO")
    igual(hashes_do_legado(raiz), antes_legado,
          "cada arquivo legado continua byte a byte como estava")
    igual(len(list((Path(raiz) / EMPRESA_A / "nfe").glob("*.xml"))), 10,
          "e nenhum foi apagado, movido ou renomeado")

    secao("O documento chegou ao acervo E ao índice")
    ac = acv.abrir(raiz, EMPRESA_A, servico=mig.FONTE_LEGADO,
                   ambiente=ing.PRODUCAO)
    originais = list((Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml"))
    igual(len(originais), 6, "6 documentos no acervo: as 5 notas e o evento")
    ok(r.indice_em_dia, "e o índice alcançou o acervo")
    # O XML ilegível NÃO entra: sem conseguir abrir o arquivo não há chave, e
    # sem chave não há identidade fiscal para endereçar o documento. Ele não se
    # perde — continua na pasta legada, que a migração não toca — mas fica
    # fora do acervo, com o motivo registrado. É comportamento do portão, não
    # desta migração.
    ok(len(originais) < r.arquivos, "e o que não pôde ser identificado ficou de fora")

    total = cq.contar(raiz, EMPRESA_A)
    igual(total, 5, "a consulta enxerga as 5 notas")
    achadas = {d.chave for d in cq.consultar(raiz, EMPRESA_A, tamanho=50).itens}
    for rotulo in ("c1", "c2", "c3", "c4_so_resumo", "c5"):
        ok(chaves[rotulo] in achadas, f"incluindo {rotulo}")

    secao("O resumo órfão foi salvo — era o buraco que a fase existe para fechar")
    so_resumo = cq.consultar(raiz, EMPRESA_A,
                             cq.Filtro(chave=chaves["c4_so_resumo"])).itens[0]
    igual(so_resumo.conteudo, cq.RESUMO, "e continua marcado como resumo")
    ok(so_resumo.valor_total is not None, "com o valor que o resumo trazia")

    secao("Resumo NÃO rebaixa o completo")
    c5 = cq.consultar(raiz, EMPRESA_A, cq.Filtro(chave=chaves["c5"])).itens[0]
    igual(c5.conteudo, cq.COMPLETO,
          "a nota que tinha os dois continua canônica como COMPLETO")

    secao("Evento não virou NF-e")
    ok(all(d.especie != "EVENTO_NFE"
           for d in cq.consultar(raiz, EMPRESA_A, tamanho=50).itens),
       "nenhum evento na listagem de notas")
    igual(len(cq.eventos_da_chave(raiz, EMPRESA_A, chaves["c1"])), 1,
          "mas o evento existe, ligado à sua nota")

    secao("Procedência preservada")
    cap = json.loads(next(
        p for p in (Path(raiz) / EMPRESA_A / "acervo").rglob("captura.json")
    ).read_text("utf-8"))
    ok("fonte" in cap, "o `captura.json` registra a fonte")
    igual(cap["fonte"], mig.FONTE_LEGADO,
          "e ela é LEGADO_NFE, não importação manual")


# ══════════════════════════════════════════════════════════════════════════
secao("Idempotência — rodar duas vezes")
with apoio.raiz_temporaria("nfe3_") as raiz:
    montar_legado(raiz)
    primeira = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)
    total1 = cq.contar(raiz, EMPRESA_A)
    originais1 = sorted(p.relative_to(raiz).as_posix() for p in
                        (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml"))
    bytes1 = {p: hashlib.sha256((Path(raiz) / p).read_bytes()).hexdigest()
              for p in originais1}

    segunda = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)
    total2 = cq.contar(raiz, EMPRESA_A)
    originais2 = sorted(p.relative_to(raiz).as_posix() for p in
                        (Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml"))

    igual(total2, total1, "a contagem de documentos não muda")
    igual(originais2, originais1, "nenhum documento novo no acervo")
    igual({p: hashlib.sha256((Path(raiz) / p).read_bytes()).hexdigest()
           for p in originais2}, bytes1,
          "e nenhum original foi reescrito")
    igual(segunda.por_veredito.get(mig.MIGRARIA), None,
          "a segunda execução não migra nada")
    ok(segunda.por_veredito.get(mig.JA_EXISTE, 0) >= 5,
       "tudo o que era novo agora responde JA_EXISTE")
    igual(segunda.por_veredito.get(mig.CONFLITO), None,
          "e nada virou conflito por ter rodado de novo")


# ══════════════════════════════════════════════════════════════════════════
secao("Conflito de verdade × conflito só de serialização")
with apoio.raiz_temporaria("nfe3_") as raiz:
    c = chave_ficticia(FORNECEDOR, 20)
    pasta = Path(raiz) / EMPRESA_A / "nfe"
    pasta.mkdir(parents=True, exist_ok=True)
    original = xml_nfe(c, valor="100.00", numero="20")
    (pasta / f"{2001:015d}-procNFe.xml").write_bytes(original)
    mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)

    secao("Mesma árvore XML, serialização diferente")
    # Espaço a mais dentro da tag de abertura. É a mesma classe de diferença
    # que a NF-e 3 encontrou 17 vezes no acervo real (lá era ` />` contra `/>`
    # no bloco `<Signature>`): serialização, não conteúdo.
    reserializado = original.replace(b"<ide>", b"<ide >")
    ok(reserializado != original, "os bytes são mesmo diferentes")
    ok(mig._so_muda_serializacao(original, reserializado),
       "mas as árvores XML são idênticas — e o diagnóstico reconhece isso")
    (pasta / f"{2002:015d}-procNFe.xml").write_bytes(reserializado)

    r = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=True)
    igual(r.por_veredito.get(mig.CONFLITO_SO_FORMATACAO), 1,
          "o ensaio classifica como CONFLITO_SO_FORMATACAO")
    igual(r.bloqueantes, [], "que NÃO é bloqueante — é conflito já explicado")

    r2 = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)
    igual(r2.por_veredito.get(mig.CONFLITO_SO_FORMATACAO), 1,
          "e a execução real o pula, em vez de gravar")
    igual(r2.quarentena, 0, "sem criar quarentena para pergunta já respondida")

    secao("Conteúdo fiscal diferente continua CONFLITO")
    adulterado = xml_nfe(c, valor="999.99", numero="20")
    ok(not mig._so_muda_serializacao(original, adulterado),
       "valor diferente NÃO é diferença de serialização")
    (pasta / f"{2003:015d}-procNFe.xml").write_bytes(adulterado)
    r3 = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=True)
    igual(r3.por_veredito.get(mig.CONFLITO), 1, "vira CONFLITO")
    igual(len(r3.bloqueantes), 1, "e é bloqueante")

    secao("A carteira PARA num bloqueante")
    saida = mig.migrar_carteira(raiz, [EMPRESA_A], ensaio=True,
                                parar_em_bloqueante=True)
    ok(saida["consolidado"]["parou_em"], "a migração se interrompe e diz onde")


# ══════════════════════════════════════════════════════════════════════════
secao("Checkpoint e scheduler intocados")
with apoio.raiz_temporaria("nfe3_") as raiz:
    montar_legado(raiz)
    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO)
    cp.ult_nsu = "000000000000777"
    repo.salvar(cp)
    pasta_cp = Path(raiz) / EMPRESA_A / "ingestao"
    antes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(pasta_cp.glob("*.json"))}
    ok(antes, "há checkpoint gravado antes da migração")

    mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)

    depois = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(pasta_cp.glob("*.json"))}
    igual(depois, antes, "nenhum arquivo de checkpoint foi tocado")
    igual(repo.carregar(EMPRESA_A, "NFE_DISTRIBUICAO", ing.PRODUCAO).ult_nsu,
          "000000000000777", "e o ultNSU continua exatamente onde estava")


# ══════════════════════════════════════════════════════════════════════════
secao("Acervo pré-existente é preservado, não sobrescrito")
with apoio.raiz_temporaria("nfe3_") as raiz:
    # um documento que já veio da SEFAZ, com a procedência dela
    c = chave_ficticia(FORNECEDOR, 30)
    xml = xml_nfe(c, valor="300.00", numero="30")
    imp.importar(raiz, EMPRESA_A, [("da-sefaz.xml", xml)],
                 fonte="NFE_DISTRIBUICAO")
    cap_antes = json.loads(next(
        (Path(raiz) / EMPRESA_A / "acervo").rglob("captura.json")
    ).read_text("utf-8"))
    igual(cap_antes["fonte"], "NFE_DISTRIBUICAO", "a procedência original")

    # e o MESMO documento também está na pasta legada
    pasta = Path(raiz) / EMPRESA_A / "nfe"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{3001:015d}-procNFe.xml").write_bytes(xml)
    r = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)

    igual(r.por_veredito.get(mig.JA_EXISTE), 1, "a migração reconhece JA_EXISTE")
    cap_depois = json.loads(next(
        (Path(raiz) / EMPRESA_A / "acervo").rglob("captura.json")
    ).read_text("utf-8"))
    igual(cap_depois["fonte"], "NFE_DISTRIBUICAO",
          "e NÃO sobrescreve a procedência de quem capturou primeiro")
    igual(cap_depois["hash_conteudo"], cap_antes["hash_conteudo"],
          "nem o conteúdo canônico")

    secao("Múltiplas procedências, um documento só")
    igual(len(list((Path(raiz) / EMPRESA_A / "acervo").rglob("original.xml"))), 1,
          "o mesmo documento por dois caminhos continua UM documento")


# ══════════════════════════════════════════════════════════════════════════
secao("Falha de parser não perde documento")
with apoio.raiz_temporaria("nfe3_") as raiz:
    pasta = Path(raiz) / EMPRESA_A / "nfe"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{4001:015d}-procNFe.xml").write_bytes(XML_MALFORMADO)
    r = mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)
    igual(r.por_veredito.get(mig.XML_INVALIDO), 1,
          "o XML ilegível é classificado, não engolido")
    ok(not r.erro, "e não derruba a migração da empresa")
    ok((pasta / f"{4001:015d}-procNFe.xml").exists(),
       "o arquivo legado continua lá — nada é descartado")


# ══════════════════════════════════════════════════════════════════════════
secao("Uma empresa ruim não derruba as outras")
with apoio.raiz_temporaria("nfe3_") as raiz:
    montar_legado(raiz, EMPRESA_A)
    montar_legado(raiz, OUTRA_EMPRESA)
    saida = mig.migrar_carteira(raiz, [EMPRESA_A, "cnpj-invalido", OUTRA_EMPRESA],
                                ensaio=True, parar_em_bloqueante=False)
    igual(saida["consolidado"]["empresas"], 3, "as três entram no relatório")
    com_erro = [e for e in saida["empresas"] if e["erro"]]
    igual(len(com_erro), 1, "só a inválida tem erro")
    boas = [e for e in saida["empresas"] if not e["erro"] and e["arquivos"]]
    igual(len(boas), 2, "e as duas boas foram processadas normalmente")


# ══════════════════════════════════════════════════════════════════════════
secao("Equivalência pós-migração — os dois leitores convergem")
with apoio.raiz_temporaria("nfe3_") as raiz:
    chaves = montar_legado(raiz)
    antes = eqv.comparar_empresa(raiz, EMPRESA_A)
    ok(antes.apenas_legado > 0, "antes da migração o legado tem documento que o índice não tem")
    igual(antes.em_ambos, 0, "e nada em comum")

    mig.migrar_empresa(raiz, EMPRESA_A, ensaio=False)

    depois = eqv.comparar_empresa(raiz, EMPRESA_A)
    ok(depois.em_ambos > antes.em_ambos,
       "depois da migração os dois leitores passam a ver os mesmos documentos")
    ok(depois.cobertura > antes.cobertura,
       "e a cobertura subiu — que é o que esta fase existe para fazer")
    igual(depois.valor_em_ambos_legado, depois.valor_em_ambos_indice,
          "com o MESMO VALOR somado dos dois lados — zero de diferença")
    igual(depois.apenas_indice, 0,
          "e nada mais está só no índice")

    secao("As duas divergências que sobram, e por que cada uma existe")
    cats = depois.por_categoria
    igual(cats.get(eqv.VALOR_DIFERENTE), None, "nenhuma diferença de VALOR")
    igual(cats.get(eqv.DATA_DIFERENTE), None, "nenhuma diferença de DATA")

    igual(cats.get(eqv.CANCELAMENTO), None,
          "nenhuma divergência de CANCELAMENTO")
    # Quando esta suíte foi escrita havia 1 aqui, e ela documentava o defeito: o
    # legado varria os eventos da pasta e marcava a nota como cancelada, e o
    # índice mostrava só o que o documento dizia de si mesmo (`protNFe`
    # cStat=100 → AUTORIZADO). A NF-e 4A fechou isso — `consulta.situacao_atual`
    # deriva de documento + eventos, com o mesmo critério do acervo. Os dois
    # leitores passaram a concordar, e por isso a expectativa mudou de 1 para 0.
    d_cancelada = [x for x in cq.consultar(raiz, EMPRESA_A, tamanho=50).itens
                   if x.chave == chaves["c1"]][0]
    ok(d_cancelada.cancelada,
       "e a nota com evento aparece CANCELADA na consulta")
    igual(d_cancelada.situacao, dm.AUTORIZADO,
          "com o documento original ainda dizendo AUTORIZADO")

    igual(cats.get(eqv.PAPEL_DIFERENTE), 1, "1 divergência de PAPEL")
    # Aqui o índice está mais certo: é o resumo órfão, e `resNFe` não nomeia
    # destinatário. O legado afirma "compra" porque o arquivo estava na pasta
    # da distribuição — uma inferência que a NF-e 2 proibiu explicitamente.
    papel = [d for d in depois.diferencas if d.categoria == eqv.PAPEL_DIFERENTE][0]
    igual(papel.legado, "compra", "o legado afirma compra")
    igual(papel.indice, "OUTRO",
          "e o índice recusa afirmar o que o resumo não diz")


# ══════════════════════════════════════════════════════════════════════════
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
print("NF-e 3: migração do legado verde.")
