#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Camada de normalização (APURAÇÃO 2).

    python teste_apuracao2.py

O QUE ESTA SUÍTE GARANTE
    Que NF-e e NFS-e entram no MESMO contrato sem perder o que cada uma tem;
    que `id_documento` e a referência ao XML atravessam tudo (é o que sustenta
    a rastreabilidade da D44); que dinheiro é `Decimal`; que ausente continua
    `None`; e que documento cancelado sai da receita **sem sair do acervo**.

    Garante também o que a camada NÃO faz: não calcula imposto, não chama o
    motor atual e não esconde as divergências achadas na APURAÇÃO 1.

FIXTURES fictícias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_fiscal as FF                 # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
import classificador as cls                        # noqa: E402
import core                                        # noqa: E402
from ingestao import documento as dm               # noqa: E402
from ingestao import identificacao as idf          # noqa: E402
from ingestao import normalizacao as nz            # noqa: E402
from ingestao import parsers as prs                # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                           # noqa: E402
_TENTATIVAS: list[str] = []
_connect_original = _socket.socket.connect


def _proibido(self, endereco, *a, **kw):
    _TENTATIVAS.append(str(endereco))
    raise AssertionError(f"teste tentou abrir conexão de rede para {endereco}")


_socket.socket.connect = _proibido


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
    ok(a == b, desc if a == b else f"{desc}  (obtive {a!r}, esperava {b!r})")


EMPRESA = T.EMPRESA_A
CHAVE = T.CHAVE_1


def doc_nfe(**kw) -> dm.Documento:
    """Documento NF-e como o parser do acervo devolve."""
    bruto = T.xml_nfe(CHAVE, **{k: v for k, v in kw.items()
                                if k in ("valor", "itens", "com_protocolo",
                                         "com_pis", "emitente", "destinatario")})
    ident = idf.identificar(bruto)
    return prs.ParserNFe55().interpretar(bruto, ident, EMPRESA)


# ══════════════════════════════════════════════════════════════════════════
secao("Contrato: o que TODA operação carrega, sem exceção")
d = doc_nfe()
op = nz.de_documento_nfe(d, caminho=f"{EMPRESA}/acervo/NFE55/ab/xyz/original.xml")
for campo in ("id_documento", "identidade_empresa", "especie", "origem",
              "competencia", "sentido", "natureza", "valor_bruto", "situacao"):
    ok(hasattr(op, campo), f"a operação tem `{campo}`")
ok(bool(op.id_documento), "id_documento preenchido")
igual(op.id_documento, d.id_documento, "e é EXATAMENTE o do documento do acervo")
igual(op.identidade_empresa, EMPRESA, "a empresa é preservada")
igual(op.especie, nz.NFE, "espécie NF-e")
igual(op.origem.fonte_leitor, nz.FONTE_ACERVO, "a origem diz de qual leitor veio")
ok(op.origem.caminho.endswith("original.xml"),
   "e o caminho aponta o XML original")
igual(op.origem.arquivo, "original.xml", "com o nome do arquivo")
igual(op.origem.chave, d.chave, "e a chave de acesso")

secao("Determinismo: mesmo documento -> mesma operação")
a = nz.de_documento_nfe(doc_nfe(), caminho="x/original.xml")
b = nz.de_documento_nfe(doc_nfe(), caminho="x/original.xml")
igual(a, b, "duas normalizações do mesmo documento são IGUAIS")
igual(a.id_documento, b.id_documento, "com o mesmo id_documento")
igual(hash(a.origem), hash(b.origem), "e a mesma origem")

secao("A operação é imutável — fato não se edita no meio do caminho")
try:
    a.valor_bruto = Decimal("1")
    ok(False, "deveria recusar alteração")
except Exception as exc:
    ok(type(exc).__name__ in ("FrozenInstanceError", "AttributeError"),
       "alterar um campo levanta — a operação é congelada")
nova = nz.com_situacao(a, nz.CANCELADA)
igual(nova.situacao, nz.CANCELADA, "com_situacao() devolve uma CÓPIA alterada")
igual(a.situacao, nz.NORMAL, "e o original continua intacto")
igual(nova.id_documento, a.id_documento, "sem perder o id_documento")

secao("Dinheiro é Decimal — nunca float")
ok(isinstance(op.valor_bruto, Decimal), "valor_bruto é Decimal")
for it in op.itens:
    for campo in ("quantidade", "valor_unitario", "valor_produto"):
        v = getattr(it, campo)
        ok(v is None or isinstance(v, Decimal), f"item.{campo} é Decimal ou None")
try:
    nz._de_legado(True)
    ok(False, "bool deveria ser recusado")
except Exception:
    ok(True, "bool não passa por dinheiro (é int disfarçado)")
igual(nz._de_legado(0.1), Decimal("0.1"),
      "float legado vira Decimal('0.1') exato — passa por str, não por binário")
ok(nz._de_legado(0.1) != Decimal(0.1),
   "e NÃO é o Decimal(0.1) binário, que seria 0.1000000000000000055...")
igual(nz._de_legado(None), None, "None continua None")
igual(nz._de_legado(0.0), Decimal("0.0"), "zero continua zero, e é Decimal")

secao("Ausente é None, informado-zero é zero — a diferença sobrevive")
d_sem_pis = doc_nfe(com_pis=False)
op_sem = nz.de_documento_nfe(d_sem_pis)
it = op_sem.itens[0]
igual(it.cst_pis, "", "sem grupo PIS, o CST fica vazio")
r = nz.Retencoes()
ok(not r.informada, "retenção não informada não se diz informada")
igual(r.pis, None, "e cada campo é None")
r0 = nz.Retencoes(pis=Decimal("0.00"))
ok(r0.informada, "retenção informada como ZERO é informada")
igual(r0.pis, Decimal("0.00"), "e vale zero, não None")

secao("Campos que a normalização não consegue determinar são DECLARADOS")
resumo = T.xml_resumo(CHAVE) if hasattr(T, "xml_resumo") else None
if resumo:
    ident = idf.identificar(resumo)
    doc_res = prs.ParserNFe55().interpretar(resumo, ident, EMPRESA)
    op_res = nz.de_documento_nfe(doc_res)
    ok("itens" in op_res.indeterminados,
       "resumo de NF-e não tem itens — e isso é dito, não fingido")
    ok(any("resumo" in a for a in op_res.avisos), "com o aviso explicando")
    igual(op_res.itens, (), "e a lista de itens fica vazia, não inventada")

# ══════════════════════════════════════════════════════════════════════════
secao("NF-e: sentido na perspectiva DA EMPRESA, sem regra fiscal")
# Esta asserção já disse o contrário — "tpNF=1 é saída" — e o contrário estava
# errado. `tpNF` é a perspectiva de QUEM EMITIU. A fixture é emitida pelo
# FORNECEDOR para a EMPRESA: `tpNF=1` ali significa saída do fornecedor, ou
# seja, ENTRADA nossa. Como a Distribuição DF-e só entrega documento que a
# empresa não gerou (D2), o engano transformou 301 compras em "saídas" num
# relatório meu da APURAÇÃO 3. Fica registrado, não apagado.
igual(op.sentido, nz.ENTRADA,
      "nota emitida por terceiro é ENTRADA, mesmo com tpNF=1")
d_saida = doc_nfe(emitente=EMPRESA, destinatario=T.EMPRESA_B)
igual(nz.de_documento_nfe(d_saida).sentido, nz.SAIDA,
      "e só é SAÍDA quando a própria empresa emitiu")
igual(op.natureza, nz.MERCADORIA, "NF-e 55 é documento de mercadoria")
ok(op.natureza_operacao, "e o texto da natureza da operação é preservado cru")

secao("NF-e: itens preservam CFOP, NCM e CST")
d3 = doc_nfe(itens=3)
op3 = nz.de_documento_nfe(d3)
igual(len(op3.itens), 3, "três itens normalizados")
i0 = op3.itens[0]
ok(i0.cfop, "o CFOP veio")
ok(i0.ncm, "o NCM veio")
ok(i0.cst_icms, "o CST do ICMS veio")
igual(i0.numero, 1, "com a numeração do item")
ok(isinstance(i0.valor_produto, Decimal), "e o valor do produto é Decimal")
ok(not hasattr(i0, "anexo"), "o item NÃO carrega classificação fiscal")
ok(not hasattr(i0, "monofasico"), "nem marcação de monofásico")
ok(not hasattr(i0, "substituicao_tributaria"), "nem de ST")

secao("NF-e: a estrutura está pronta, mas nenhuma regra foi aplicada")
_fonte = (RAIZ / "nfse" / "backend" / "ingestao" / "normalizacao.py").read_text("utf-8")
import ast                                          # noqa: E402
arv = ast.parse(_fonte)
chamadas = [getattr(n.func, "attr", "") or getattr(n.func, "id", "")
            for n in ast.walk(arv) if isinstance(n, ast.Call)]
for proibida in ("calcular_das", "aliquota_efetiva", "resolver_anexo",
                 "apurar", "apurar_trimestre", "rbt12", "rbt12_de"):
    ok(proibida not in chamadas, f"a normalização não chama `{proibida}`")
# Import de verdade só aparece no AST; citar o módulo na docstring é
# documentação, não dependência.
importados = {n.module for n in ast.walk(arv) if isinstance(n, ast.ImportFrom)}
importados |= {a.name for n in ast.walk(arv) if isinstance(n, ast.Import)
               for a in n.names}
for modulo in ("classificador", "apuracao_federal", "core"):
    ok(modulo not in importados, f"a normalização não importa `{modulo}`")
ok("documento" in " ".join(str(m) for m in importados),
   "ela depende só do modelo do acervo")

# ══════════════════════════════════════════════════════════════════════════
secao("NFS-e pelo core: as três situações sobrevivem")
with apoio.raiz_temporaria("ap2_") as raiz:
    PA = "2026-06"
    ch1, ch2, ch3 = FF.chave_nfse(1), FF.chave_nfse(2), FF.chave_nfse(3)
    FF.gravar(raiz, [
        FF.xml_nfse(1, PA, 1000.0),
        FF.xml_nfse(2, PA, 2000.0),
        FF.xml_nfse(3, PA, 2500.0),
        FF.xml_evento_nfse(ch1, "Cancelamento de NFS-e"),
        FF.xml_evento_nfse(ch2, "Cancelamento por Substituição", chave_substituta=ch3),
    ])
    ops = {}
    for n in core.carregar_notas(raiz):
        o = nz.de_nota_core(n, FF.PRESTADOR)
        ops[n["chave"]] = o
    igual(ops[ch1].situacao, nz.CANCELADA, "cancelada continua cancelada")
    igual(ops[ch2].situacao, nz.SUBSTITUIDA, "substituída continua substituída")
    igual(ops[ch3].situacao, nz.SUBSTITUTA, "e a substituta continua substituta")
    ok(not ops[ch1].entra_na_receita, "a cancelada sai da receita")
    ok(not ops[ch2].entra_na_receita, "a substituída também")
    ok(ops[ch3].entra_na_receita, "mas a substituta ENTRA — é a válida do par")
    igual(len(ops), 3, "e as TRÊS continuam existindo como operação")
    ok(all(o.id_documento for o in ops.values()),
       "nenhuma perdeu o identificador ao ser excluída da receita")
    ok(all(o.origem.chave for o in ops.values()),
       "nem a referência ao documento")

secao("NFS-e pelo core: campos do contrato")
with apoio.raiz_temporaria("ap2_") as raiz:
    PA = "2026-06"
    FF.gravar(raiz, [FF.xml_nfse(1, PA, 1234.56, tp_ret_piscofins="1",
                                 ret_pis=8.02, ret_cofins=37.04,
                                 ret_csll=12.35, ret_irrf=18.52,
                                 iss_retido=True, valor_iss=61.73)])
    n = core.carregar_notas(raiz)[0]
    o = nz.de_nota_core(n, FF.PRESTADOR)
    igual(o.especie, nz.NFSE, "espécie NFS-e")
    igual(o.natureza, nz.SERVICO, "natureza serviço")
    igual(o.sentido, nz.SAIDA, "emitida pela empresa = saída")
    igual(o.competencia, date(2026, 6, 1), "competência no primeiro dia do mês")
    igual(o.valor_bruto, Decimal("1234.56"), "valor bruto em Decimal")
    igual(o.retencoes.pis, Decimal("8.02"), "PIS retido")
    igual(o.retencoes.cofins, Decimal("37.04"), "COFINS retido")
    igual(o.retencoes.csll, Decimal("12.35"), "CSLL retida")
    igual(o.retencoes.irrf, Decimal("18.52"), "IRRF retido")
    igual(o.retencoes.iss, Decimal("61.73"), "ISS retido")
    ok(o.retencoes.informada, "e a retenção se declara informada")
    igual(o.origem.fonte_leitor, nz.FONTE_CORE, "a origem diz que veio do core")

secao("NFS-e recebida de terceiro é ENTRADA")
with apoio.raiz_temporaria("ap2_") as raiz:
    FF.gravar(raiz, [FF.xml_nfse(1, "2026-06", 500.0, emitente=FF.OUTRO)])
    n = core.carregar_notas(raiz)[0]
    o = nz.de_nota_core(n, FF.PRESTADOR)
    igual(o.sentido, nz.ENTRADA, "quem não emitiu, recebeu")

secao("NFS-e pelo classificador: o que SÓ ele tem")
with apoio.raiz_temporaria("ap2_") as raiz:
    PA = "2026-06"
    FF.gravar(raiz, [FF.xml_nfse(1, PA, 1000.0, ctribnac="171901")])
    n = cls.ler_nfse(raiz, FF.PRESTADOR)[0]
    o = nz.de_nota_classificador(n, FF.PRESTADOR)
    igual(o.codigo_servico, "171901", "o cTribNac é preservado")
    igual(o.item_lc116, "17.19", "e o item da LC 116 que o leitor derivou")
    ok(o.municipio_incidencia, "com o município de incidência")
    igual(o.origem.fonte_leitor, nz.FONTE_CLASSIFICADOR, "origem correta")
    igual(o.especie, nz.NFSE, "mesma espécie do contrato")
    igual(o.natureza, nz.SERVICO, "mesma natureza")

secao("A divergência da APURAÇÃO 1 é REGISTRADA, não escondida")
with apoio.raiz_temporaria("ap2_") as raiz:
    PA = "2026-06"
    ch2, ch3 = FF.chave_nfse(2), FF.chave_nfse(3)
    FF.gravar(raiz, [
        FF.xml_nfse(2, PA, 2000.0),
        FF.xml_nfse(3, PA, 2500.0),
        FF.xml_evento_nfse(ch2, "Cancelamento por Substituição", chave_substituta=ch3),
    ])
    n_core = [x for x in core.carregar_notas(raiz) if x["chave"] == ch2][0]
    n_cls = [x for x in cls.ler_nfse(raiz, FF.PRESTADOR) if x["chave"] == ch2][0]
    o_core = nz.de_nota_core(n_core, FF.PRESTADOR)
    o_cls = nz.de_nota_classificador(n_cls, FF.PRESTADOR)
    igual(o_core.situacao, nz.SUBSTITUIDA, "o core sabe que foi substituição")
    igual(o_cls.situacao, nz.CANCELADA, "o classificador só sabe que saiu")
    ok("motivo_da_exclusao" in o_cls.indeterminados,
       "e a operação DECLARA que o motivo é indeterminado nesse leitor")
    ok(not o_core.entra_na_receita and not o_cls.entra_na_receita,
       "os dois concordam no que importa: a nota sai da receita")
    igual(o_core.id_documento, o_cls.id_documento,
          "e as duas apontam para o MESMO documento")

secao("comparar(): a ferramenta que a APURAÇÃO 3 vai usar")
with apoio.raiz_temporaria("ap2_") as raiz:
    PA = "2026-06"
    FF.gravar(raiz, [FF.xml_nfse(1, PA, 1000.0)])
    o_core = nz.de_nota_core(core.carregar_notas(raiz)[0], FF.PRESTADOR)
    o_cls = nz.de_nota_classificador(cls.ler_nfse(raiz, FF.PRESTADOR)[0],
                                     FF.PRESTADOR)
    r = nz.comparar(o_core, o_cls)
    ok(r["mesmo_documento"], "os dois falam do mesmo documento")
    igual(sorted(r["fontes"]), sorted([nz.FONTE_CORE, nz.FONTE_CLASSIFICADOR]),
          "e a comparação diz de onde cada lado veio")
    for campo in ("competencia", "valor_bruto", "situacao", "sentido", "numero"):
        ok(campo not in r["diferencas"],
           f"nota simples: `{campo}` é idêntico nos dois leitores")
    for campo in ("retencoes.pis", "retencoes.cofins", "retencoes.csll",
                  "retencoes.irrf"):
        ok(campo not in r["diferencas"], f"e `{campo}` também")
    # DIVERGÊNCIA REAL, achada por esta camada: quando NÃO há ISS retido, o
    # core devolve 0,00 (informou zero) e o classificador devolve None (não
    # informou). A normalização preserva os dois — é justamente a distinção
    # "ausente != zero" aplicada a um caso concreto.
    igual(r["diferencas"], ["retencoes.iss"],
          "a ÚNICA diferença é o ISS: zero no core, não-informado no classificador")
    igual(o_core.retencoes.iss, Decimal("0.0"), "core: informou zero")
    igual(o_cls.retencoes.iss, None, "classificador: não informou")
    # LIMITE DOS LEITORES ATUAIS, que a camada expõe sem consertar: nenhum dos
    # dois distingue "não informado" de zero nas retenções FEDERAIS — os dois
    # devolvem 0.0 sempre. Só o ISS chega a ser None, e mesmo assim porque a
    # normalização o condiciona a `iss_retido`. Para a APURAÇÃO 3 isso importa:
    # abater uma retenção "zero" e uma retenção ausente é a mesma conta hoje,
    # e deixaria de ser se algum dia o leitor passar a devolver None.
    ok(o_core.retencoes.informada and o_cls.retencoes.informada,
       "os DOIS se dizem informados: as retenções federais nunca vêm como None")
    igual(o_cls.retencoes.pis, Decimal("0.0"),
          "o classificador devolve 0.0, não None, quando não há PIS retido")
    igual(o_core.retencoes.pis, Decimal("0.0"), "e o core também")

secao("comparar(): a nota com tpRetPisCofins fora do bloco separa os leitores")
with apoio.raiz_temporaria("ap2_") as raiz:
    PA = "2026-06"
    FF.gravar(raiz, [FF.xml_nfse(1, PA, 10000.0, tp_ret_piscofins="1",
                                 ret_pis=65.0, ret_cofins=300.0,
                                 com_bloco_piscofins=False)])
    o_core = nz.de_nota_core(core.carregar_notas(raiz)[0], FF.PRESTADOR)
    o_cls = nz.de_nota_classificador(cls.ler_nfse(raiz, FF.PRESTADOR)[0],
                                     FF.PRESTADOR)
    r = nz.comparar(o_core, o_cls)
    ok("retencoes.pis" in r["diferencas"], "a divergência de PIS aparece")
    ok("retencoes.cofins" in r["diferencas"], "e a de COFINS")
    igual(o_cls.retencoes.pis, Decimal("65.0"), "o classificador lê a retenção")
    igual(o_core.retencoes.pis, Decimal("0.0"), "e o core lê zero")
    ok(r["mesmo_documento"],
       "mas o documento é o mesmo — a comparação não some com nenhum dos dois")

secao("NF-e e NFS-e no MESMO contrato, sem perder o específico")
with apoio.raiz_temporaria("ap2_") as raiz:
    FF.gravar(raiz, [FF.xml_nfse(1, "2026-06", 1000.0, ctribnac="171901")])
    o_servico = nz.de_nota_classificador(cls.ler_nfse(raiz, FF.PRESTADOR)[0],
                                         FF.PRESTADOR)
    o_venda = nz.de_documento_nfe(doc_nfe(itens=2))
    for o in (o_servico, o_venda):
        ok(isinstance(o, nz.Operacao), "as duas são Operacao")
        ok(bool(o.id_documento), "as duas têm id_documento")
        ok(o.situacao in (nz.NORMAL, nz.CANCELADA, nz.SUBSTITUIDA,
                          nz.SUBSTITUTA, nz.SITUACAO_INDEFINIDA),
           "as duas têm situação do mesmo vocabulário")
        ok(isinstance(o.para_json(), dict), "e as duas serializam igual")
    igual(o_servico.itens, (), "serviço não tem itens de mercadoria")
    ok(o_servico.item_lc116, "mas tem item da LC 116")
    ok(len(o_venda.itens) == 2, "mercadoria tem itens")
    igual(o_venda.item_lc116, "", "e não tem item da LC 116")
    ok(o_venda.itens[0].ncm, "com NCM no item")
    igual(o_servico.natureza, nz.SERVICO, "natureza serviço")
    igual(o_venda.natureza, nz.MERCADORIA, "natureza mercadoria")

secao("para_json(): mascarado, sem conteúdo de documento")
j = o_venda.para_json() if "o_venda" in dir() else nz.de_documento_nfe(doc_nfe()).para_json()
ok("/" in j["empresa"] and "***" in j["empresa"], "o CNPJ sai mascarado")
ok(EMPRESA not in str(j), "o CNPJ inteiro NÃO aparece no JSON")
ok(isinstance(j["valor_bruto"], (str, type(None))),
   "o valor vai como texto — Decimal não vira float no caminho")
ok("origem" in j and "id_documento" in j,
   "e a origem e o id vão junto, para a rastreabilidade não depender de memória")

secao("Identidade da NFS-e é derivada da chave, e é estável")
a1 = nz._id_nfse(FF.chave_nfse(1))
a2 = nz._id_nfse(FF.chave_nfse(1))
b1 = nz._id_nfse(FF.chave_nfse(2))
igual(a1, a2, "mesma chave, mesmo identificador")
ok(a1 != b1, "chaves diferentes, identificadores diferentes")
igual(len(a1), 32, "32 hex, como o do acervo")
igual(nz._id_nfse(""), "", "sem chave não se inventa identidade")

secao("A camada não foi conectada ao motor — ninguém a consome ainda")
import subprocess                                   # noqa: E402
backend = RAIZ / "nfse" / "backend"
consumidores = []
for arq in sorted(backend.rglob("*.py")):
    if arq.name == "normalizacao.py" or "__pycache__" in arq.parts:
        continue
    txt = arq.read_text("utf-8", errors="replace")
    if "normalizacao" in txt:
        consumidores.append(arq.name)
# `conferencia.py` (APURAÇÃO 3) consome a normalização de propósito: ele
# COMPARA os dois caminhos, somente leitura, e não alimenta cálculo nenhum.
# O que continua proibido é o motor fiscal consumir — e é isso que se afirma.
# `conferencia.py` compara os dois caminhos; `ponte_motor.py` traduz a
# operação para o motor atual. Os dois são somente leitura e nenhum calcula
# imposto — quem calcula continua sendo o motor de sempre.
igual(sorted(consumidores), ["conferencia.py", "importacao.py", "ponte_motor.py", "vendas.py"],
      "só conferência, importação, ponte e vendas consomem a normalização")
for modulo in ("classificador.py", "apuracao_federal.py",
               "core.py", "main.py"):
    ok(modulo not in consumidores,
       f"`{modulo}` NÃO consome a normalização — a conexão é fase própria")

# ══════════════════════════════════════════════════════════════════════════
secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede não estava ativa")
except AssertionError as exc:
    ok("conexão de rede" in str(exc), "e a trava estava mesmo ativa (conferido)")
finally:
    _TENTATIVAS.clear()
    _socket.socket.connect = _connect_original

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("APURAÇÃO 2: normalização verde.")
