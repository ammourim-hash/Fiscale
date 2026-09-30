#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NF-e de vendas em modo paralelo (APURAÇÃO 6).

    python teste_apuracao6.py

O QUE ORIGINOU ESTA SUÍTE
    A APURAÇÃO 3 relatou "300 saídas" no acervo de NF-e. Estava errado, e o
    erro era da normalização: `_sentido_da_nfe` lia `tpNF` primeiro, e `tpNF`
    é a perspectiva de QUEM EMITIU. Como a Distribuição DF-e só entrega
    documentos que a empresa **não** gerou (decisão D2), quase toda nota chega
    com `tpNF=1` — saída do fornecedor, entrada nossa.

    As 301 NF-e do acervo foram TODAS emitidas por terceiros. São compras.

O QUE ESTA SUÍTE GARANTE
    Que o sentido é lido na perspectiva da empresa; que só nota emitida pela
    própria empresa pode virar receita de venda; e que a classificação por
    CFOP separa venda, transferência, devolução e não-receita **sem chutar**
    quando o código não é conhecido.

FIXTURES fictícias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import ast
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
import classificador as cls                        # noqa: E402
from ingestao import documento as dm               # noqa: E402
from ingestao import identificacao as idf          # noqa: E402
from ingestao import normalizacao as nz            # noqa: E402
from ingestao import parsers as prs                # noqa: E402
from ingestao import vendas as vd                  # noqa: E402

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
FORNECEDOR = T.FORNECEDOR
CLIENTE = T.EMPRESA_B


def operacao(*, emitente, destinatario, cfop_por_item=("5102",), chave=None,
             empresa=EMPRESA):
    """Uma NF-e normalizada, com o CFOP que se quiser em cada item."""
    ch = chave or T.chave_ficticia(emitente, numero=1)
    bruto = T.xml_nfe(ch, emitente=emitente, destinatario=destinatario,
                      itens=len(cfop_por_item))
    # A fixture usa CFOP 5102 em todos os itens; troca item a item.
    texto = bruto.decode("utf-8")
    for i, cfop in enumerate(cfop_por_item):
        texto = texto.replace("<CFOP>5102</CFOP>", f"<CFOP>{cfop}</CFOP>", 1)
    bruto = texto.encode("utf-8")
    ident = idf.identificar(bruto)
    doc = prs.ParserNFe55().interpretar(bruto, ident, empresa)
    return nz.de_documento_nfe(doc, caminho=f"{empresa}/acervo/x/original.xml")


# ══════════════════════════════════════════════════════════════════════════
secao("O sentido é lido na perspectiva DA EMPRESA, não do emitente")
op_compra = operacao(emitente=FORNECEDOR, destinatario=EMPRESA)
igual(op_compra.sentido, nz.ENTRADA,
      "nota emitida por FORNECEDOR para a empresa é ENTRADA")
ok(op_compra.emitente != EMPRESA, "mesmo com tpNF=1 no XML do fornecedor")
op_venda = operacao(emitente=EMPRESA, destinatario=CLIENTE)
igual(op_venda.sentido, nz.SAIDA, "nota emitida PELA empresa é SAÍDA")
igual(op_venda.emitente, EMPRESA, "com a empresa como emitente")

secao("Só nota emitida pela empresa pode virar receita de venda")
igual(vd.classificar(op_compra, EMPRESA), None,
      "compra não vira operação de venda — nem entra na lista")
v = vd.classificar(op_venda, EMPRESA)
ok(v is not None, "a venda vira operação")
igual(v.natureza, vd.VENDA, "classificada como VENDA")
ok(v.e_receita, "e forma receita")
igual(v.id_documento, op_venda.id_documento, "com o id_documento preservado")
ok(v.caminho.endswith("original.xml"), "e o caminho até o XML")
ok(v.chave, "com a chave do documento")

secao("NFS-e não entra por aqui")
op_servico = nz.Operacao(id_documento="x", identidade_empresa=EMPRESA,
                         especie=nz.NFSE, origem=nz.OrigemDocumento())
igual(vd.classificar(op_servico, EMPRESA), None,
      "operação de serviço não é classificada como venda de mercadoria")

# ══════════════════════════════════════════════════════════════════════════
secao("CFOP → natureza: venda")
for cfop in ("5101", "5102", "6102", "5103", "5105", "5109", "5401", "5403",
             "6404", "5551"):
    igual(vd.classificar_cfop(cfop), vd.VENDA, f"CFOP {cfop} é venda")

secao("CFOP → natureza: transferência (o que a regra atual não separa)")
for cfop in ("5151", "5152", "6152", "5153", "5155", "5156", "5408", "6409"):
    igual(vd.classificar_cfop(cfop), vd.TRANSFERENCIA,
          f"CFOP {cfop} é TRANSFERÊNCIA, não venda")

secao("CFOP → natureza: devolução e não-receita")
for cfop in ("5201", "5202", "5410", "6411", "5553"):
    igual(vd.classificar_cfop(cfop), vd.DEVOLUCAO, f"CFOP {cfop} é devolução")
for cfop in ("5901", "5905", "5915", "5920", "5929", "5949"):
    igual(vd.classificar_cfop(cfop), vd.NAO_RECEITA,
          f"CFOP {cfop} não é receita de venda")
for cfop in ("1102", "2102", "3102"):
    igual(vd.classificar_cfop(cfop), vd.NAO_RECEITA,
          f"CFOP {cfop} é de ENTRADA — não é receita da empresa")

secao("Na dúvida, INDETERMINADA — nunca um chute")
for cfop in ("5199", "5299", "7777", "abc", "", "51", "51022"):
    igual(vd.classificar_cfop(cfop), vd.INDETERMINADA,
          f"CFOP {cfop!r} fica indeterminado")

secao("Nota com itens de naturezas diferentes fica indeterminada")
op = operacao(emitente=EMPRESA, destinatario=CLIENTE,
              cfop_por_item=("5102", "5152"))
v = vd.classificar(op, EMPRESA)
igual(v.natureza, vd.INDETERMINADA, "venda + transferência na mesma nota")
ok(not v.e_receita, "e não entra na receita")
ok("naturezas diferentes" in v.motivo, "com o motivo escrito")
ok("5102=VENDA" in v.motivo and "5152=TRANSFERENCIA" in v.motivo,
   "e cada CFOP com a sua natureza, à vista")
igual(sorted(v.cfops), ["5102", "5152"], "os dois CFOP ficam registrados")

secao("Nota sem CFOP (resumo) é indeterminada e diz por quê")
op = operacao(emitente=EMPRESA, destinatario=CLIENTE)
sem = nz.Operacao(id_documento=op.id_documento,
                  identidade_empresa=EMPRESA, especie=nz.NFE,
                  origem=op.origem, sentido=nz.SAIDA,
                  emitente=EMPRESA, valor_bruto=Decimal("100"))
v = vd.classificar(sem, EMPRESA)
igual(v.natureza, vd.INDETERMINADA, "sem CFOP não se classifica")
ok("sem CFOP" in v.motivo, "e o motivo é dito")
igual(v.cfops, (), "sem CFOP nenhum registrado")

secao("Documento cancelado não forma receita, mas continua classificado")
op = operacao(emitente=EMPRESA, destinatario=CLIENTE)
cancelada = nz.com_situacao(op, nz.CANCELADA)
v = vd.classificar(cancelada, EMPRESA)
igual(v.natureza, vd.VENDA, "a natureza continua sendo venda")
ok(v.cancelada, "mas está marcada como cancelada")
ok(not v.e_receita, "e NÃO forma receita")
ok("cancelado" in v.motivo, "com o motivo registrado")
ok(v.id_documento, "e o id_documento preservado — continua rastreável")

# ══════════════════════════════════════════════════════════════════════════
secao("Receita de vendas por competência")
ops = [operacao(emitente=EMPRESA, destinatario=CLIENTE,
                chave=T.chave_ficticia(EMPRESA, numero=n),
                cfop_por_item=(cfop,))
       for n, cfop in ((1, "5102"), (2, "6102"), (3, "5152"), (4, "5202"),
                       (5, "5929"), (6, "5199"))]
r = vd.receita_de_vendas(ops, EMPRESA)
z = r.resumo()
igual(z["documentos"], 6, "seis operações de saída")
igual(z["por_natureza"]["VENDA"]["documentos"], 2, "duas vendas")
igual(z["por_natureza"]["TRANSFERENCIA"]["documentos"], 1, "uma transferência")
igual(z["por_natureza"]["DEVOLUCAO"]["documentos"], 1, "uma devolução")
igual(z["por_natureza"]["NAO_RECEITA"]["documentos"], 1, "uma não-receita")
igual(z["por_natureza"]["INDETERMINADA"]["documentos"], 1, "uma indeterminada")
comp = list(r.por_competencia.values())[0]
ok(comp["venda"] > 0, "a receita de venda é positiva")
ok(comp["transferencia"] > 0, "a transferência é somada em separado")
igual(len(comp["documentos_venda"]), 2, "com os documentos das vendas")
igual(len(comp["documentos_indeterminados"]), 1, "e os indeterminados listados")
ok(isinstance(r.total_venda, Decimal), "o total é Decimal")
igual(r.total_venda, comp["venda"], "e bate com a competência única")

secao("Rastreabilidade: da receita de volta aos documentos")
c = list(r.por_competencia)[0]
ids = r.documentos_de(c)
igual(len(ids), 2, "dois documentos formaram a receita do mês")
igual(ids, sorted(o.id_documento for o in r.operacoes if o.e_receita),
      "e são exatamente as vendas não canceladas")
por_id = {o.id_documento: o for o in r.operacoes}
for i in ids:
    ok(por_id[i].caminho, f"documento {i[:8]}… tem caminho até o XML")
    ok(por_id[i].chave, "e a chave de acesso")
    ok(por_id[i].para_json()["chave_final"], "com só o final da chave no relatório")

secao("Comparação com a regra atual: a divergência da transferência aparece")
difs = vd.comparar_com_regra_atual(r.operacoes)
ok(any("5152" in k for k in difs),
   "o CFOP 5152 diverge: transferência aqui, Comércio na regra atual")
igual(cls.tipo_por_cfop("5152"), "Comércio",
      "confirmado: a regra atual classifica 5152 como Comércio")
igual(vd.classificar_cfop("5152"), vd.TRANSFERENCIA,
      "e esta tabela como transferência")
ok(not any("5102" in k for k in difs), "onde as duas concordam, não há ruído")

secao("A regra atual NÃO foi alterada")
fonte_cls = (RAIZ / "nfse" / "backend" / "classificador.py").read_text("utf-8")
ok("TRANSFERENCIA" not in fonte_cls,
   "classificador.py continua sem a categoria transferência")
arv_cls = ast.parse(fonte_cls)
importados = {n.module for n in ast.walk(arv_cls) if isinstance(n, ast.ImportFrom)}
importados |= {x.name for n in ast.walk(arv_cls)
               if isinstance(n, (ast.Import, ast.ImportFrom)) for x in n.names}
ok(not any("vendas" == str(v).rsplit(".", 1)[-1] for v in importados if v),
   "e não importa o módulo novo")
for cfop, esperado in (("5102", "Comércio"), ("5101", "Indústria"),
                       ("1202", "Devolução/Entrada"), ("5933", "Serviço")):
    igual(cls.tipo_por_cfop(cfop), esperado,
          f"tipo_por_cfop({cfop}) continua devolvendo {esperado}")

secao("Comparação com lançamentos manuais de comércio/indústria")
lanc = [{"competencia": list(r.por_competencia)[0], "valor": 100.0,
         "tipo": "comercio"},
        {"competencia": "2099-01", "valor": 50.0, "tipo": "industria"},
        {"competencia": "2099-02", "valor": 70.0, "tipo": "servico_iii"}]
cmp = vd.comparar_com_manuais(r, lanc)
comps = {x["competencia"] for x in cmp}
ok("2099-01" in comps, "o lançamento de indústria entra na comparação")
ok("2099-02" not in comps, "o de serviço NÃO — não é venda de mercadoria")
linha = [x for x in cmp if x["competencia"] == "2099-01"][0]
igual(linha["venda_no_acervo"], "0", "sem NF-e naquele mês, o acervo traz zero")
igual(linha["lancamento_manual"], "50.0", "e o manual traz 50,00")
ok(not linha["iguais"], "a diferença é sinalizada, não somada")

secao("O módulo não calcula imposto nem toca no motor")
fonte = (RAIZ / "nfse" / "backend" / "ingestao" / "vendas.py").read_text("utf-8")
arv = ast.parse(fonte)
chamadas = {getattr(n.func, "attr", "") or getattr(n.func, "id", "")
            for n in ast.walk(arv) if isinstance(n, ast.Call)}
for proibida in ("calcular_das", "aliquota_efetiva", "reparticao",
                 "resolver_anexo", "apurar", "rbt12", "rbt12_de"):
    ok(proibida not in chamadas, f"não chama `{proibida}`")
# Procurar o termo no arquivo inteiro dá falso positivo: a própria docstring
# do módulo diz "não decide Anexo". O que interessa é o CÓDIGO — nomes, textos
# e atributos —, não a promessa escrita em prosa.
# `ast.get_docstring` devolve o texto já dedentado — comparar por VALOR não
# reconhece a docstring original. Identifica-se pelo nó.
docstrings, nos_doc = set(), set()
for n in ast.walk(arv):
    if not isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)):
        continue
    d = ast.get_docstring(n)
    if d is None:
        continue
    docstrings.add(d)
    nos_doc.add(id(n.body[0].value))
nomes_no_codigo = set()
for n in ast.walk(arv):
    if isinstance(n, ast.Name):
        nomes_no_codigo.add(n.id)
    elif isinstance(n, ast.Attribute):
        nomes_no_codigo.add(n.attr)
    elif isinstance(n, ast.arg):
        nomes_no_codigo.add(n.arg)
    elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
        nomes_no_codigo.add(n.name)
    elif isinstance(n, ast.Constant) and isinstance(n.value, str):
        if id(n) not in nos_doc:
            nomes_no_codigo.add(n.value)
for termo in ("TABELAS", "REPARTICAO", "ALIQUOTAS", "monofasico", "Anexo"):
    achado = sorted(x for x in nomes_no_codigo if termo in str(x))
    ok(not achado, f"e `{termo}` não aparece no código  ({achado[:2]})")
ok(any("Anexo" in (d or "") for d in docstrings),
   "— a docstring, essa sim, diz que Anexo não é decidido aqui")
numeros = {n.value for n in ast.walk(arv)
           if isinstance(n, ast.Constant) and isinstance(n.value, float)}
igual(numeros, set(), "nenhum literal decimal — não há alíquota escondida")

secao("Nada consome o módulo de vendas ainda")
backend = RAIZ / "nfse" / "backend"
consumidores = []
for arq in sorted(backend.rglob("*.py")):
    if arq.name == "vendas.py" or "__pycache__" in arq.parts:
        continue
    try:
        a = ast.parse(arq.read_text("utf-8", errors="replace"))
    except SyntaxError:
        continue
    nomes = {n.module for n in ast.walk(a) if isinstance(n, ast.ImportFrom)}
    nomes |= {x.name for n in ast.walk(a) if isinstance(n, ast.Import)
              for x in n.names}
    nomes |= {x.name for n in ast.walk(a) if isinstance(n, ast.ImportFrom)
              for x in n.names}
    if any(str(v).endswith("vendas") or str(v) == "vendas" for v in nomes):
        consumidores.append(arq.name)
# SEGUNDO CONSUMIDOR, DECLARADO — 27/09/2026.
#     `vendas.py` continua sendo CONFERÊNCIA, e é exatamente nessa condição que
#     a memória de cálculo o usa: para mostrar a NF-e do acervo ao lado da
#     apuração, com `entra_na_base = False`. O que este guarda protege segue de
#     pé — ninguém APURA por `vendas` —, e quem garante isso é
#     `teste_apuracao_memoria`, que reprova se o bloco de NF-e entrar em
#     qualquer base. Um terceiro consumidor continua reprovando aqui.
igual(consumidores, ["apuracao_memoria.py", "importacao.py"],
      "vendas é lido pelo portão (conferir o lote) e pela memória (mostrar), "
      "nunca para apurar")

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
print("APURAÇÃO 6: vendas em modo paralelo verde.")
