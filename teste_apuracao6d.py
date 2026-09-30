#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fontes de emissão própria e espécie NFCE65 (APURAÇÃO 6D).

    python teste_apuracao6d.py

O QUE ESTA SUÍTE GARANTE
    Que existe uma camada de FONTES que só entrega bytes, e que toda fonte
    termina no mesmo portão da 6B — não num segundo pipeline.

    E que a NFC-e passou a ter espécie própria, `NFCE65`, em vez de entrar
    disfarçada de `NFE55`: id diferente, pasta diferente no acervo, filtro por
    espécie que não mistura — convergindo só onde deve convergir, em
    `vendas.py`, porque as duas são venda de mercadoria.

FIXTURES fictícias, raízes temporárias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import acervo as acv                 # noqa: E402
from ingestao import conferencia as cf             # noqa: E402
from ingestao import documento as dm               # noqa: E402
from ingestao import fontes_emissao as fe          # noqa: E402
from ingestao import identificacao as idf          # noqa: E402
from ingestao import importacao as imp             # noqa: E402
from ingestao import indice as idx                 # noqa: E402
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

_TEMPS: list[Path] = []


def raiz_nova() -> Path:
    p = Path(tempfile.mkdtemp(prefix="fiscale6d-"))
    _TEMPS.append(p)
    return p


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
CLIENTE = T.EMPRESA_B
FORNECEDOR = T.FORNECEDOR


def chave_modelo(cnpj, modelo, numero=1):
    """Chave de 44 dígitos com o modelo pedido e DV correto."""
    corpo = f"262608{cnpj}{modelo}{1:03d}{numero:09d}1{numero:08d}"
    if len(corpo) != 43:
        raise ValueError(f"corpo com {len(corpo)} dígitos")
    return corpo + str(idf.dv_chave(corpo + "0"))


def xml_nfe55(numero=1, cfop="5102", emitente=EMPRESA, **kw):
    ch = T.chave_ficticia(emitente, numero)
    x = T.xml_nfe(ch, emitente=emitente, destinatario=CLIENTE, **kw)
    if cfop != "5102":
        x = x.decode("utf-8").replace("<CFOP>5102</CFOP>",
                                      f"<CFOP>{cfop}</CFOP>").encode("utf-8")
    return ch, x


def xml_nfce65(numero=1, cfop="5102", emitente=EMPRESA, **kw):
    """Cupom: MESMAS tags da NF-e, `mod` 65 e o 65 dentro da chave."""
    ch = chave_modelo(emitente, "65", numero)
    x = T.xml_nfe(ch, emitente=emitente, destinatario=CLIENTE, **kw)
    t = x.decode("utf-8").replace("<tpNF>1</tpNF>", "<mod>65</mod><tpNF>1</tpNF>")
    if cfop != "5102":
        t = t.replace("<CFOP>5102</CFOP>", f"<CFOP>{cfop}</CFOP>")
    return ch, t.encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════
#  NFCE65 — espécie própria
# ══════════════════════════════════════════════════════════════════════════
secao("A espécie vem do MODELO NA CHAVE, não da tag raiz")
ch55, x55 = xml_nfe55(1)
ch65, x65 = xml_nfce65(2)
igual(idf.identificar(x55).raiz, "nfeProc", "a NF-e tem raiz nfeProc")
igual(idf.identificar(x65).raiz, "nfeProc", "e o cupom tem a MESMA raiz nfeProc")
igual(idf.identificar(x55).especie, idf.NFE55, "mesmo assim a NF-e é NFE55")
igual(idf.identificar(x65).especie, idf.NFCE65, "e o cupom é NFCE65")
igual(idf.modelo_da_chave(ch55), "55", "o modelo 55 está na chave")
igual(idf.modelo_da_chave(ch65), "65", "e o 65 também")
igual(idf.especie_da_nota(ch65), idf.NFCE65, "`especie_da_nota` concorda")
igual(idf.modelo_da_chave("123"), "", "chave curta não devolve modelo inventado")

secao("Identidades separadas — o cupom não nasce sob o nome da NF-e")
igual(idf.identificar(x65).id_documento, idf.id_de_nfce(ch65),
      "o id do cupom é o de NFCE65")
ok(idf.id_de_nfce(ch65) != idf._id(idf.NFE55, ch65),
   "e é diferente do que seria sob NFE55 — o prefixo entra no hash")
igual(idf.id_de_nfe(ch65), idf.id_de_nfce(ch65),
      "`id_de_nfe` de uma chave 65 devolve o id de cupom, não o errado")
igual(idf.id_de_nfe(ch55), idf._id(idf.NFE55, ch55), "e a NF-e segue igual")
ok(idf.NFCE65 in idf.ESPECIES, "NFCE65 está entre as espécies conhecidas")
igual(idf.ESPECIES_NOTA, (idf.NFE55, idf.NFCE65),
      "e as duas espécies de nota estão nomeadas juntas")

secao("O mesmo parser lê as duas — a estrutura é idêntica")
for chave, bruto, especie in ((ch55, x55, idf.NFE55), (ch65, x65, idf.NFCE65)):
    ident = idf.identificar(bruto)
    p = prs.para(ident)
    ok(p is not None, f"há parser para {especie}")
    doc = p.interpretar(bruto, ident, EMPRESA)
    igual(doc.especie, especie, f"e o documento sai como {especie}")
    ok(doc.extensao.itens, "com os itens lidos")

secao("A espécie atravessa a normalização")
d = raiz_nova()
q = imp.painel(d, EMPRESA, [("nfe.xml", x55), ("nfce.xml", x65)])
igual(q["preservados"], 2, "as duas preservadas")
igual(q["rejeitados"], 0, "nenhuma recusada")
por_esp = {o.especie: o for o in cf.conferir_nfe(d, EMPRESA).operacoes}
igual(sorted(por_esp), [nz.NFCE, nz.NFE], "a normalização preserva as duas espécies")
igual(por_esp[nz.NFCE].natureza, nz.MERCADORIA, "cupom é documento de mercadoria")
igual(por_esp[nz.NFCE].sentido, nz.SAIDA, "e é saída da empresa")

secao("No acervo elas ficam separadas")
especies = sorted({e for e, _ in acv.abrir(d, EMPRESA).listar()})
igual(especies, ["NFCE65", "NFE55"], "duas pastas de espécie no acervo")
ok((d / EMPRESA / "acervo" / "NFCE65").is_dir(), "a pasta NFCE65 existe")
igual(len(acv.abrir(d, EMPRESA).listar(idf.NFCE65)), 1, "com um documento")
igual(len(acv.abrir(d, EMPRESA).listar(idf.NFE55)), 1, "e a NF-e no lugar dela")

secao("E convergem em vendas.py — as duas são venda de mercadoria")
rv = vd.receita_de_vendas(cf.conferir_nfe(d, EMPRESA).operacoes, EMPRESA)
igual(len(rv.operacoes), 2, "as duas entram na classificação")
igual({o.natureza for o in rv.operacoes}, {vd.VENDA}, "ambas como VENDA")
igual(q["vendas"], 2, "o painel conta as duas")
ok(rv.total_venda > 0, "e as duas somam receita de venda")
igual(nz.ESPECIES_MERCADORIA, (nz.NFE, nz.NFCE),
      "a convergência é declarada, não implícita")

secao("O índice acompanha as duas espécies")
ind, ac = idx.abrir(d, EMPRESA), acv.abrir(d, EMPRESA)
ok(ind.em_dia_com(ac), "índice em dia com o acervo")
igual(len(ind.listar(especie=idf.NFCE65)), 1, "o índice sabe filtrar cupom")
igual(len(ind.listar(especie=idf.NFE55)), 1, "e NF-e")
igual(ind.ausentes(ac), [], "nada ausente")

secao("Modelo fora de 55/65 continua recusado, com o número à vista")
d = raiz_nova()
ch57 = chave_modelo(EMPRESA, "57", 3)
v = imp.importar(d, EMPRESA, [("cte.xml", T.xml_nfe(ch57, emitente=EMPRESA,
                                                    destinatario=CLIENTE))]).vereditos[0]
ok(not v.aceito, "recusado")
igual(v.motivo, imp.MODELO_NAO_SUPORTADO, "motivo MODELO_NAO_SUPORTADO")
ok("57" in v.detalhe, "e o detalhe nomeia o modelo")

secao("Modelo divergente entre chave e XML é divergência, não modelo inválido")
d = raiz_nova()
ch, x = xml_nfe55(4)
x_mentiroso = x.decode("utf-8").replace("<tpNF>1</tpNF>",
                                        "<mod>65</mod><tpNF>1</tpNF>").encode("utf-8")
v = imp.importar(d, EMPRESA, [("m.xml", x_mentiroso)]).vereditos[0]
ok(not v.aceito, "recusado")
igual(v.motivo, imp.CHAVE_DIVERGENTE, "porque a chave diz 55 e o XML diz 65")
igual(list((d / EMPRESA / "acervo").rglob("original.xml")), [], "e nada gravado")

secao("Cupom de terceiro nunca vira venda da empresa")
d = raiz_nova()
_, x_alheio = xml_nfce65(5, emitente=FORNECEDOR)
v = imp.importar(d, EMPRESA, [("alheio.xml", x_alheio)]).vereditos[0]
ok(not v.aceito, "recusado — a empresa não participa")
igual(v.motivo, imp.EMPRESA_AUSENTE, "com motivo EMPRESA_AUSENTE")

# ══════════════════════════════════════════════════════════════════════════
#  A camada de fontes
# ══════════════════════════════════════════════════════════════════════════
secao("Uma fonte só entrega bytes — não valida, não grava")
fonte = fe.PastaEmissor(identidade=EMPRESA, pasta=raiz_nova())
for proibido in ("validar", "preservar", "importar", "salvar", "gravar"):
    ok(not hasattr(fonte, proibido), f"a fonte não tem `{proibido}`")
ok(hasattr(fonte, "documentos") and hasattr(fonte, "descrever"),
   "tem só `documentos()` e `descrever()`")
ok(isinstance(fonte, fe.FonteEmissao), "e cumpre o contrato `FonteEmissao`")

secao("PastaEmissor lê pasta de emissor, inclusive em subpastas")
d = raiz_nova()
emissor = raiz_nova() / "EMISSOR"
(emissor / "2026" / "08").mkdir(parents=True)
chaves = []
for n in (10, 11, 12):
    c, x = xml_nfe55(n)
    chaves.append(c)
    (emissor / "2026" / "08" / f"NFe{n}.xml").write_bytes(x)
c65, x65b = xml_nfce65(13)
(emissor / "2026" / "08" / "NFCe13.xml").write_bytes(x65b)
(emissor / "leiame.txt").write_text("nao sou xml", encoding="utf-8")
f = fe.PastaEmissor(identidade=EMPRESA, pasta=emissor)
docs = list(f.documentos())
igual(len(docs), 4, "quatro XML encontrados, em subpasta")
ok(all(d_.origem.get("caminho") for d_ in docs), "cada um com o caminho de origem")
ok(all(d_.origem.get("modificado_em") for d_ in docs), "e a data de modificação")
ok(all(isinstance(d_.conteudo, bytes) for d_ in docs), "conteúdo em bytes crus")
ok(f.descrever()["disponivel"], "a fonte se declara disponível")

secao("Coletar termina no portão — e no acervo")
antes = sorted(x.name for x in (emissor / "2026" / "08").iterdir())
r = fe.coletar(d, EMPRESA, f)
z = r.resumo()
igual(z["encontrados_na_fonte"], 4, "quatro vindos da fonte")
igual(z["preservados"], 4, "quatro preservados pelo portão")
igual(z["rejeitados"], 0, "nenhum recusado")
igual(z["indexados"], 4, "quatro indexados")
ok(z["indice_em_dia"], "índice em dia ao terminar")
igual(z["proprias_validas"], 4, "todas de emissão própria")
igual(sorted({e for e, _ in acv.abrir(d, EMPRESA).listar()}), ["NFCE65", "NFE55"],
      "as duas espécies chegaram ao acervo pela fonte")

secao("A fonte NÃO move nem apaga o que leu")
igual(sorted(x.name for x in (emissor / "2026" / "08").iterdir()), antes,
      "a pasta de origem continua idêntica")
ok((emissor / "leiame.txt").exists(), "e o arquivo que não é XML segue lá")

secao("Coletar de novo: só duplicatas, zero reindexação")
z2 = fe.coletar(d, EMPRESA, f).resumo()
igual(z2["encontrados_na_fonte"], 4, "a fonte devolve os mesmos quatro")
igual(z2["preservados"], 0, "nada novo")
igual(z2["duplicados"], 4, "quatro duplicatas")
igual(z2["indexados"], 0, "nenhuma reindexação")
ok(z2["indice_em_dia"], "e continua em dia")

secao("A fonte não guarda memória do que já leu")
fonte_txt = (RAIZ / "nfse" / "backend" / "ingestao"
             / "fontes_emissao.py").read_text("utf-8")
for termo in ("ja_lido", "processados", "ultimo_lido", "cursor", "offset"):
    ok(termo not in fonte_txt, f"não há `{termo}` — o acervo é a única memória")

secao("Pasta indisponível não quebra e não mente")
ausente = fe.PastaEmissor(identidade=EMPRESA, pasta=raiz_nova() / "nao-existe")
ok(not ausente.disponivel, "a fonte se declara indisponível")
igual(list(ausente.documentos()), [], "não devolve documento nenhum")
r = fe.coletar(raiz_nova(), EMPRESA, ausente)
ok(r.indisponivel, "e a coleta diz que a fonte está indisponível")
igual(r.resumo()["preservados"], 0, "sem preservar nada")

secao("Configuração por empresa")
d = raiz_nova()
igual(fe.carregar_config(d, EMPRESA), [], "sem config, lista vazia")
igual(fe.fontes_da_empresa(d, EMPRESA), [], "e nenhuma fonte")
fe.salvar_config(d, EMPRESA, [{"tipo": "pasta", "pasta": str(emissor)}])
igual(len(fe.fontes_da_empresa(d, EMPRESA)), 1, "config salva vira uma fonte")
igual(fe.fontes_da_empresa(d, EMPRESA)[0].tipo, fe.PASTA, "do tipo pasta")
ok(fe.caminho_config(d, EMPRESA).name == fe.ARQUIVO_CONFIG,
   "guardada num arquivo por empresa")
res = fe.coletar_todas(d, EMPRESA)
igual(len(res), 1, "coletar_todas percorre a configurada")
igual(res[0].resumo()["preservados"], 4, "e traz os documentos")

secao("Tipo previsto sem conector não levanta — e não inventa integração")
igual(fe.de_config(EMPRESA, {"tipo": fe.API_ERP, "url": "x"}), None,
      "API de ERP devolve None: contrato previsto, conector inexistente")
fe.salvar_config(d, EMPRESA, [{"tipo": fe.API_ERP}, {"tipo": "pasta",
                                                     "pasta": str(emissor)}])
igual(len(fe.fontes_da_empresa(d, EMPRESA)), 1,
      "uma config de ERP não impede a pasta de funcionar")
ok(fe.API_ERP in fe.TIPOS_PREVISTOS, "o tipo está previsto, e só isso")

secao("Config corrompida não derruba a coleta")
d = raiz_nova()
fe.caminho_config(d, EMPRESA).parent.mkdir(parents=True, exist_ok=True)
fe.caminho_config(d, EMPRESA).write_text("{ isto não é json", encoding="utf-8")
igual(fe.carregar_config(d, EMPRESA), [], "config ilegível vira lista vazia")
igual(fe.coletar_todas(d, EMPRESA), [], "e a coleta não levanta")

# ══════════════════════════════════════════════════════════════════════════
#  As garantias que não podem regredir
# ══════════════════════════════════════════════════════════════════════════
secao("Não existe segundo pipeline: toda fonte passa pelo portão")
arv = ast.parse(fonte_txt)
chamadas = {getattr(n.func, "attr", "") or getattr(n.func, "id", "")
            for n in ast.walk(arv) if isinstance(n, ast.Call)}
ok("painel" in chamadas or "importar" in chamadas,
   "`fontes_emissao` chama o portão")
for proibida in ("preservar", "indexar_ausentes", "indexar_documento",
                 "identificar", "de_documento_nfe", "receita_de_vendas"):
    ok(proibida not in chamadas,
       f"e NÃO chama `{proibida}` — isso é do portão, não da fonte")
modulos = {n.module for n in ast.walk(arv) if isinstance(n, ast.ImportFrom)}
modulos |= {a.name for n in ast.walk(arv)
            if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
for proibido in ("acervo", "indice", "checkpoint", "requests", "urllib",
                 "socket", "conectores", "servico_distribuicao", "pipeline"):
    ok(not any(proibido in str(m or "") for m in modulos),
       f"a camada de fontes não importa `{proibido}`")

secao("Nenhuma fórmula tributária entrou")
numeros = {n.value for n in ast.walk(arv)
           if isinstance(n, ast.Constant) and isinstance(n.value, float)}
igual(numeros, set(), "nenhum literal decimal em `fontes_emissao`")
for termo in ("aliquota", "rbt12", "calcular_das", "Anexo", "monofasico"):
    ok(termo not in fonte_txt, f"e nenhuma menção a `{termo}`")

secao("Importar por fonte não move o checkpoint da distribuição")
d = raiz_nova()
cp = d / EMPRESA / "ingestao"
cp.mkdir(parents=True)
alvo = cp / "NFE_DISTRIBUICAO.producao.json"
conteudo = b'{"ult_nsu": "000000000000216", "servico": "NFE_DISTRIBUICAO"}'
alvo.write_bytes(conteudo)
antes_cp = hashlib.sha256(alvo.read_bytes()).hexdigest()
fe.coletar(d, EMPRESA, fe.PastaEmissor(identidade=EMPRESA, pasta=emissor))
igual(hashlib.sha256(alvo.read_bytes()).hexdigest(), antes_cp,
      "coleta inteira, checkpoint byte a byte igual")
igual(alvo.read_bytes(), conteudo, "conteúdo conferido cru")
igual(len(acv.abrir(d, EMPRESA).listar()), 4,
      "e os quatro documentos entraram mesmo — sem vacuidade")

secao("XML byte a byte, id_documento e rastreabilidade preservados")
por_id = {o.id_documento: o for o in cf.conferir_nfe(d, EMPRESA).operacoes}
igual(len(por_id), 4, "quatro operações rastreáveis")
for id_doc, op in por_id.items():
    caminho = d / op.origem.caminho
    ok(caminho.exists(), f"{id_doc[:8]}… tem XML em disco")
    origem = emissor / "2026" / "08"
    iguais = [a for a in origem.glob("*.xml")
              if a.read_bytes() == caminho.read_bytes()]
    ok(len(iguais) == 1, "byte a byte igual ao arquivo da fonte")
    esperado = idf.id_de_nfe(op.origem.chave)
    igual(id_doc, esperado, "e o id_documento é o canônico da chave")

secao("A tela 6C continua funcionando com as duas espécies")
d = raiz_nova()
q = imp.painel(d, EMPRESA, [("a.xml", xml_nfe55(20)[1]), ("b.xml", xml_nfce65(21)[1])])
for campo in ("xml_encontrados", "nfe_proprias_validas", "vendas",
              "transferencias", "devolucoes", "nao_receita", "indeterminadas",
              "receita_venda", "documentos", "indice_em_dia"):
    ok(campo in q, f"o painel da 6C continua trazendo `{campo}`")
igual(q["vendas"], 2, "e conta NF-e e NFC-e juntas como venda")
igual(len(q["documentos"]), 2, "com as duas na tabela")

secao("Nenhum módulo fiscal foi ligado às espécies novas")
backend = RAIZ / "nfse" / "backend"
for modulo in ("classificador.py", "apuracao_federal.py", "core.py"):
    txt = (backend / modulo).read_text("utf-8", errors="replace")
    ok("NFCE65" not in txt, f"`{modulo}` não conhece NFCE65")
    ok("fontes_emissao" not in txt, f"`{modulo}` não conhece a camada de fontes")
consumidores = []
for arq in sorted(backend.rglob("*.py")):
    if arq.name == "fontes_emissao.py" or "__pycache__" in arq.parts:
        continue
    try:
        a = ast.parse(arq.read_text("utf-8", errors="replace"))
    except SyntaxError:
        continue
    nomes = {x.name for n in ast.walk(a)
             if isinstance(n, (ast.Import, ast.ImportFrom)) for x in n.names}
    if any(str(v).rsplit(".", 1)[-1] == "fontes_emissao" for v in nomes):
        consumidores.append(arq.name)
igual(consumidores, [],
      "nada consome a camada de fontes ainda — ela fica pronta e desligada")

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

for p in _TEMPS:
    shutil.rmtree(p, ignore_errors=True)

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("APURAÇÃO 6D: fontes de emissão e NFCE65 verdes.")
