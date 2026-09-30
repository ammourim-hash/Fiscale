#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portão único de importação de NF-e próprias (APURAÇÃO 6B).

    python teste_apuracao6b.py

O QUE ESTA SUÍTE GARANTE
    Que existe UM caminho para XML que não veio da SEFAZ, que ele valida antes
    de gravar, que ele nunca chama de venda própria uma nota de terceiro, e que
    importar não move o checkpoint da distribuição — nem um byte.

    Garante também o que a fase se recusou a fazer: NFC-e não entra, imposto
    não é calculado, e a pasta antiga não é apagada.

FIXTURES fictícias, raízes temporárias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import ast
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_fixturas_nfe as T                     # noqa: E402
import nfe as nfemod                               # noqa: E402
from ingestao import acervo as acv                 # noqa: E402
from ingestao import conferencia as cf             # noqa: E402
from ingestao import identificacao as idf          # noqa: E402
from ingestao import documento as dm               # noqa: E402
from ingestao import indice as idx                 # noqa: E402
from ingestao import importacao as imp             # noqa: E402
from ingestao import normalizacao as nz            # noqa: E402
from ingestao.distribuicao import DocumentoBruto   # noqa: E402
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
    p = Path(tempfile.mkdtemp(prefix="fiscale6b-"))
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


def nfe_propria(numero=1, cfop="5102", **kw):
    """NF-e emitida PELA empresa, com o CFOP que se quiser."""
    ch = T.chave_ficticia(EMPRESA, numero)
    x = T.xml_nfe(ch, emitente=EMPRESA, destinatario=CLIENTE, **kw)
    if cfop != "5102":
        x = x.decode("utf-8").replace("<CFOP>5102</CFOP>",
                                      f"<CFOP>{cfop}</CFOP>").encode("utf-8")
    return ch, x


def chave_modelo(cnpj, modelo, numero=1):
    """Chave de 44 dígitos com o modelo que se pedir e DV correto."""
    corpo = f"26260{8}{cnpj}{modelo}{1:03d}{numero:09d}1{numero:08d}"
    return corpo + str(idf.dv_chave(corpo + "0"))


def um(d, nome, conteudo):
    return imp.importar(d, EMPRESA, [(nome, conteudo)]).vereditos[0]


# ══════════════════════════════════════════════════════════════════════════
secao("NF-e própria válida atravessa e é candidata a receita")
d = raiz_nova()
ch, x = nfe_propria(1)
v = um(d, "propria.xml", x)
ok(v.aceito, "aceita")
igual(v.papel, imp.PROPRIA, "reconhecida como emissão própria")
igual(v.resultado_acervo, acv.NOVO, "gravada como documento novo")
ok(v.pode_ser_receita, "nada impede que forme receita")
igual(v.impedimento_receita, imp.RECEITA_POSSIVEL, "sem impedimento registrado")
igual(v.id_documento, idf.id_de_nfe(ch), "id_documento é o canônico da chave")
ok((d / EMPRESA / "acervo").is_dir(), "o acervo da empresa foi criado")

secao("O XML original fica imutável e íntegro")
orig = next((d / EMPRESA / "acervo").rglob("original.xml"))
igual(orig.read_bytes(), x, "byte a byte igual ao que entrou")
cap = next((d / EMPRESA / "acervo").rglob("captura.json"))
ok(cap.exists(), "com captura.json ao lado")
ok(imp.FONTE_IMPORTACAO in cap.read_text("utf-8"),
   "e a fonte registrada como IMPORTACAO_XML — dá para saber de onde veio")

secao("Empresa emitente errada: nunca vira venda própria")
d = raiz_nova()
# terceiro → a empresa é destinatária: documento legítimo, mas não é venda dela
v = um(d, "compra.xml", T.xml_nfe(T.chave_ficticia(FORNECEDOR, 2),
                                  emitente=FORNECEDOR, destinatario=EMPRESA))
ok(v.aceito, "nota de fornecedor para a empresa é preservada")
igual(v.papel, imp.TERCEIRO, "classificada como TERCEIRO")
ok(not v.pode_ser_receita, "e NÃO pode formar receita")
igual(v.impedimento_receita, imp.NAO_E_EMISSAO_PROPRIA, "com o impedimento escrito")
# a empresa não participa de forma alguma
v = um(d, "alheia.xml", T.xml_nfe(T.chave_ficticia(FORNECEDOR, 3),
                                  emitente=FORNECEDOR, destinatario=CLIENTE))
ok(not v.aceito, "nota entre dois terceiros é recusada")
igual(v.motivo, imp.EMPRESA_AUSENTE, "com motivo EMPRESA_AUSENTE")
igual(v.resultado_acervo, "", "e nada foi gravado")

secao("Chave inválida: DV que não fecha")
d = raiz_nova()
ch, x = nfe_propria(4)
ruim = ch[:43] + ("0" if ch[43] != "0" else "1")
v = um(d, "dv.xml", x.decode("utf-8").replace(ch, ruim).encode("utf-8"))
ok(not v.aceito, "recusada")
igual(v.motivo, imp.CHAVE_INVALIDA, "motivo CHAVE_INVALIDA")
ok("DV" in v.detalhe, "e o detalhe mostra o DV informado × o calculado")
igual(list((d / EMPRESA / "acervo").rglob("original.xml")), [],
      "o acervo continua vazio — recusa acontece ANTES de gravar")

secao("Chave divergente do conteúdo do XML")
d = raiz_nova()
ch, x = nfe_propria(5)
outra = T.chave_ficticia(FORNECEDOR, 5)     # chave válida, mas de outro CNPJ
v = um(d, "trocada.xml", x.decode("utf-8").replace(ch, outra).encode("utf-8"))
ok(not v.aceito, "recusada")
igual(v.motivo, imp.CHAVE_DIVERGENTE, "motivo CHAVE_DIVERGENTE")
ok("CNPJ" in v.detalhe, "e o detalhe diz que o CNPJ da chave não é o do emitente")

secao("NFC-e entrou na 6D — com espécie própria, não sob o rótulo da NF-e")
# Esta seção já afirmou o contrário: "modelo diferente de 55 não entra". A
# recusa estava certa para o que existia então — a identificação decidia a
# espécie pela tag raiz, e `nfeProc` serve NF-e e NFC-e sem distinção, de modo
# que aceitar o cupom o teria gravado como `NFE55`. Um id que existe e mente é
# pior que uma recusa que explica.
# Na 6D quem decide passou a ser o MODELO NA CHAVE, e as duas convivem.
d = raiz_nova()
ch65 = chave_modelo(EMPRESA, "65", 6)
x65 = T.xml_nfe(ch65, emitente=EMPRESA, destinatario=CLIENTE)
x65 = x65.decode("utf-8").replace("<tpNF>1</tpNF>",
                                  "<mod>65</mod><tpNF>1</tpNF>").encode("utf-8")
v = um(d, "nfce.xml", x65)
ok(v.aceito, "NFC-e aceita")
igual(v.especie, idf.NFCE65, "com espécie própria NFCE65")
igual(v.id_documento, idf.id_de_nfce(ch65), "e id no espaço de nomes do cupom")
ok((d / EMPRESA / "acervo" / "NFCE65").is_dir(),
   "gravada na pasta da espécie dela, não na da NF-e")
ok(not (d / EMPRESA / "acervo" / "NFE55").exists(),
   "e nada foi parar sob NFE55")

secao("Modelo fora de 55 e 65 continua recusado")
d = raiz_nova()
ch57 = chave_modelo(EMPRESA, "57", 9)
v = um(d, "cte.xml", T.xml_nfe(ch57, emitente=EMPRESA, destinatario=CLIENTE))
ok(not v.aceito, "recusado")
igual(v.motivo, imp.MODELO_NAO_SUPORTADO, "motivo MODELO_NAO_SUPORTADO")
ok("57" in v.detalhe, "e o detalhe nomeia o modelo")
igual(list((d / EMPRESA / "acervo").rglob("original.xml")), [], "nada gravado")

secao("Protocolo ausente: preserva, mas não é receita")
d = raiz_nova()
ch, x = nfe_propria(7, com_protocolo=False)
v = um(d, "sem-prot.xml", x)
ok(v.aceito, "aceita — recusar guardaria menos documento, não mais verdade")
igual(v.papel, imp.PROPRIA, "continua sendo emissão própria")
igual(v.resultado_acervo, acv.NOVO, "e foi preservada")
ok(not v.pode_ser_receita, "mas NÃO pode formar receita")
igual(v.impedimento_receita, imp.SEM_PROTOCOLO, "com o impedimento nomeado")

secao("Duplicata é idempotente")
d = raiz_nova()
ch, x = nfe_propria(8)
r1 = imp.importar(d, EMPRESA, [("a.xml", x)])
n1 = sum(1 for _ in (d / EMPRESA / "acervo").rglob("original.xml"))
r2 = imp.importar(d, EMPRESA, [("a-de-novo.xml", x)])
n2 = sum(1 for _ in (d / EMPRESA / "acervo").rglob("original.xml"))
igual(r1.vereditos[0].resultado_acervo, acv.NOVO, "primeira vez: NOVO")
igual(r2.vereditos[0].resultado_acervo, acv.DUPLICATA, "segunda vez: DUPLICATA")
igual(n2, n1, "e o acervo não cresceu")
igual(r2.vereditos[0].id_documento, r1.vereditos[0].id_documento,
      "mesmo id_documento — nome de arquivo diferente não cria documento novo")
igual(r2.duplicados, 1, "o relatório conta a duplicata")
igual(r2.preservados, 0, "e não a conta como preservada")

secao("Evento de cancelamento entra pelo mesmo portão")
d = raiz_nova()
ch, x = nfe_propria(9)
r = imp.importar(d, EMPRESA, [("nf.xml", x), ("canc.xml", T.xml_evento(ch))])
ev = r.vereditos[1]
ok(ev.aceito, "o evento é aceito")
igual(ev.papel, imp.EVENTO, "classificado como EVENTO")
igual(ev.especie, idf.EVENTO_NFE, "espécie EVENTO_NFE")
igual(ev.chave, ch, "vinculado à chave da nota")
canc = imp.cancelamentos_no_acervo(d, EMPRESA)
igual(list(canc), [ch], "e `cancelamentos_no_acervo` o encontra")

secao("Ausência de evento NÃO é confirmação de que a nota está ativa")
d = raiz_nova()
ch_c, x_c = nfe_propria(10)
ch_s, x_s = nfe_propria(11)
imp.importar(d, EMPRESA, [("c.xml", x_c), ("s.xml", x_s),
                          ("ev.xml", T.xml_evento(ch_c))])
ops = cf.conferir_nfe(d, EMPRESA).operacoes
ops, sit = imp.situacao_das_operacoes(ops, imp.cancelamentos_no_acervo(d, EMPRESA))
igual(sit.canceladas, 1, "uma nota marcada CANCELADA")
igual(sit.sem_evento_conhecido, 1, "e uma SEM EVENTO CONHECIDO")
ok("NÃO confirma" in sit.resumo()["advertencia"],
   "o resumo adverte que ausência de evento não confirma nada")
por_chave = {o.origem.chave: o for o in ops}
igual(por_chave[ch_c].situacao, nz.CANCELADA, "a com evento está cancelada")
ok(por_chave[ch_s].situacao != nz.CANCELADA, "a sem evento não é dada por cancelada")
igual(vd.classificar(por_chave[ch_c], EMPRESA).e_receita, False,
      "a cancelada não forma receita em vendas.py")

# ══════════════════════════════════════════════════════════════════════════
secao("Transferência e devolução chegam corretamente ao vendas.py")
d = raiz_nova()
arqs = []
chaves = {}
for n, cfop in ((20, "5102"), (21, "5152"), (22, "5202"), (23, "5929")):
    c, x = nfe_propria(n, cfop)
    chaves[cfop] = c
    arqs.append((f"nf{n}.xml", x))
r = imp.importar(d, EMPRESA, arqs)
igual(r.preservados, 4, "quatro notas preservadas")
conf = cf.conferir_nfe(d, EMPRESA)
igual(conf.documentos, 4, "quatro chegam à normalização")
igual(conf.saidas, 4, "todas como SAÍDA — a empresa emitiu")
rv = vd.receita_de_vendas(conf.operacoes, EMPRESA)
z = rv.resumo()
igual(z["por_natureza"]["VENDA"]["documentos"], 1, "uma VENDA")
igual(z["por_natureza"]["TRANSFERENCIA"]["documentos"], 1, "uma TRANSFERÊNCIA")
igual(z["por_natureza"]["DEVOLUCAO"]["documentos"], 1, "uma DEVOLUÇÃO")
igual(z["por_natureza"]["NAO_RECEITA"]["documentos"], 1, "uma NÃO-RECEITA")
por_chave = {o.chave: o for o in rv.operacoes}
igual(por_chave[chaves["5152"]].natureza, vd.TRANSFERENCIA,
      "o CFOP 5152 chegou como transferência, não como venda")
igual(por_chave[chaves["5202"]].natureza, vd.DEVOLUCAO, "e o 5202 como devolução")
ok(rv.total_venda == por_chave[chaves["5102"]].valor,
   "só a venda entra no total")

secao("O id_documento sobrevive do portão até vendas.py")
esperado = {idf.id_de_nfe(c) for c in chaves.values()}
igual({v.id_documento for v in r.vereditos}, esperado, "o portão devolve os ids")
igual({o.id_documento for o in conf.operacoes}, esperado,
      "a normalização preserva os mesmos ids")
igual({o.id_documento for o in rv.operacoes}, esperado,
      "e vendas.py também — a rastreabilidade fecha")
for o in rv.operacoes:
    ok(o.caminho.endswith("original.xml"), f"{o.id_documento[:8]}… aponta ao XML")

# ══════════════════════════════════════════════════════════════════════════
secao("Importar NÃO move o checkpoint da distribuição")
d = raiz_nova()
cp = d / EMPRESA / "ingestao"
cp.mkdir(parents=True)
alvo = cp / "NFE_DISTRIBUICAO.producao.json"
conteudo = b'{"ult_nsu": "000000000000216", "servico": "NFE_DISTRIBUICAO"}'
alvo.write_bytes(conteudo)
antes = hashlib.sha256(alvo.read_bytes()).hexdigest()
imp.importar(d, EMPRESA, [(f"n{n}.xml", nfe_propria(n)[1]) for n in range(30, 40)])
igual(hashlib.sha256(alvo.read_bytes()).hexdigest(), antes,
      "dez notas importadas, checkpoint byte a byte igual")
igual(alvo.read_bytes(), conteudo, "conteúdo idêntico, conferido cru")
ok(sum(1 for _ in (d / EMPRESA / "acervo").rglob("original.xml")) == 10,
   "e as dez foram mesmo preservadas — o teste não passou por vacuidade")

secao("O portão não conhece checkpoint nem rede")
fonte = (RAIZ / "nfse" / "backend" / "ingestao" / "importacao.py").read_text("utf-8")
arv = ast.parse(fonte)
modulos = {n.module for n in ast.walk(arv) if isinstance(n, ast.ImportFrom)}
modulos |= {a.name for n in ast.walk(arv)
            if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
for proibido in ("checkpoint", "cpm", "requests", "urllib", "http", "socket",
                 "servico_distribuicao", "conectores", "pipeline"):
    ok(not any(proibido in str(m or "") for m in modulos),
       f"não importa `{proibido}`")
chamadas = {getattr(n.func, "attr", "") or getattr(n.func, "id", "")
            for n in ast.walk(arv) if isinstance(n, ast.Call)}
for proibida in ("calcular_das", "aliquota_efetiva", "rbt12", "apurar",
                 "consultar", "consultar_empresa"):
    ok(proibida not in chamadas, f"não chama `{proibida}`")

# ══════════════════════════════════════════════════════════════════════════
secao("`nfe.importar_xmls` delega ao portão e mantém o contrato da tela")
d = raiz_nova()
pasta = d / EMPRESA
pasta.mkdir()
ch, x = nfe_propria(50)
rel = nfemod.importar_xmls(pasta, EMPRESA, [
    ("v.xml", x),
    ("c.xml", T.xml_nfe(T.chave_ficticia(FORNECEDOR, 51),
                        emitente=FORNECEDOR, destinatario=EMPRESA)),
    ("ev.xml", T.xml_evento(ch)),
    ("lixo.xml", b"nao sou xml"),
])
for chave_antiga in ("venda", "nfce", "compra", "frete", "outra", "cte_emitido",
                     "cte_tomado", "repetidos", "nfse", "invalidos",
                     "outros_emitentes"):
    ok(chave_antiga in rel, f"a tela continua recebendo `{chave_antiga}`")
igual(rel["venda"], 1, "uma venda, como antes")
igual(rel["compra"], 1, "uma compra, como antes")
igual(rel["invalidos"], 1, "um inválido, como antes")
igual(rel["acervo_novos"], 3, "e o acervo recebeu os três documentos fiscais")
igual(rel["acervo_emissao_propria"], 1, "sendo um de emissão própria")
igual(rel["acervo_rejeitados"], 0, "sem rejeição no portão")

secao("`nfe_importadas` continua existindo — caixa de entrada, não verdade")
entrada = pasta / "nfe_importadas"
ok(entrada.is_dir(), "a pasta continua lá")
igual(len(list(entrada.glob("*.xml"))), 3, "com os arquivos ainda gravados")
ok(sum(1 for _ in (pasta / "acervo").rglob("original.xml")) == 3,
   "e o acervo tem os mesmos documentos — dupla escrita, migração reversível")
fonte_nfe = (RAIZ / "nfse" / "backend" / "nfe.py").read_text("utf-8")
for perigo in ("rmtree", "unlink", "shutil.move", "os.remove"):
    ok(perigo not in fonte_nfe, f"`nfe.py` não usa `{perigo}` — nada é apagado")

secao("Importação em lote não vaza documento entre empresas")
d = raiz_nova()
ch_a, x_a = nfe_propria(60)
ch_b = T.chave_ficticia(CLIENTE, 61)
x_b = T.xml_nfe(ch_b, emitente=CLIENTE, destinatario=FORNECEDOR)
r = imp.importar(d, EMPRESA, [("minha.xml", x_a), ("da_outra.xml", x_b)])
igual(r.preservados, 1, "só a da empresa apurada é preservada")
igual(r.rejeitados, 1, "a da outra empresa é recusada")
igual(r.vereditos[1].motivo, imp.EMPRESA_AUSENTE, "com motivo explícito")
ok(not (d / CLIENTE).exists(), "e nenhuma pasta foi criada para a outra empresa")

secao("Identidade inválida não grava nada")
d = raiz_nova()
r = imp.importar(d, "123", [("x.xml", nfe_propria(70)[1])])
igual(r.rejeitados, 1, "recusa o arquivo")
ok(not any(d.rglob("original.xml")), "e não cria acervo nenhum")

secao("Relatório não expõe a chave inteira")
d = raiz_nova()
ch, x = nfe_propria(80)
j = imp.importar(d, EMPRESA, [("n.xml", x)]).vereditos[0].para_json()
ok(ch not in str(j), "a chave completa não aparece no JSON do relatório")
igual(j["chave_final"], ch[-8:], "só o final dela, que basta para conferir")

# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#  O DEFEITO DO M1: documento no acervo e ausente do índice
# ══════════════════════════════════════════════════════════════════════════
secao("O defeito: documento novo no acervo + ausente no índice é DETECTADO")
d = raiz_nova()
ch, x = nfe_propria(90)
imp.importar(d, EMPRESA, [("a.xml", x)])
ind, ac = idx.abrir(d, EMPRESA), acv.abrir(d, EMPRESA)
igual(ind.ausentes(ac), [], "depois de importar, nada ausente")
ok(ind.em_dia_com(ac), "e o índice está em dia com o acervo")

# Agora o cenário exato do M1: o documento entra no acervo SEM passar pelo
# índice — foi assim que 510 documentos conviveram com 493 indexados.
ch2, x2 = nfe_propria(91)
acv.abrir(d, EMPRESA, servico="DIRETO").preservar(
    DocumentoBruto(nsu="", conteudo=x2, chave=ch2))
ausentes = ind.ausentes(ac)
igual(len(ausentes), 1, "o documento gravado por fora aparece como AUSENTE")
igual(ausentes[0][1], idf.id_de_nfe(ch2), "e é exatamente ele")
ok(not ind.em_dia_com(ac), "o índice sabe que NÃO está em dia")
igual(len(ac.listar()), 2, "acervo com 2")
igual(sum(ind.contar().values()), 1, "índice com 1 — a defasagem existe mesmo")

secao("`desatualizados()` não enxerga esse caso — por isso `ausentes()` existe")
igual(ind.desatualizados(), [],
      "quem nunca entrou no índice não aparece em `desatualizados()`")
ok(ind.ausentes(ac), "mas aparece em `ausentes()` — era este o ponto cego do M1")

secao("E a importação seguinte conserta sozinha")
r = imp.importar(d, EMPRESA, [("a-again.xml", x)])
igual(r.duplicados, 1, "o arquivo reimportado é duplicata")
igual(r.preservados, 0, "nada novo preservado")
igual(r.indexados, 1, "mas o documento órfão foi indexado")
ok(r.indice_em_dia, "e a importação termina com índice = acervo")
igual(sum(ind.contar().values()), 2, "índice agora com 2")
igual(ind.ausentes(ac), [], "nenhum ausente")

secao("Idempotência: a terceira passada não reindexa nada")
r = imp.importar(d, EMPRESA, [("a-again.xml", x)])
igual(r.indexados, 0, "zero reindexações desnecessárias")
igual(r.preservados, 0, "zero documentos novos")
igual(r.duplicados, 1, "só a duplicata")
ok(r.indice_em_dia, "e continua em dia")
igual(idx.indexar_ausentes(d, EMPRESA).total, 0,
      "`indexar_ausentes` sozinho também não acha nada a fazer")

secao("Importação que não preserva nada ainda assim confere o índice")
# É o caso mais importante: o defeito do M1 não foi "esquecemos de indexar o
# que gravamos" — foi o índice ficar atrás sem nada denunciar.
d = raiz_nova()
ch, x = nfe_propria(95)
acv.abrir(d, EMPRESA, servico="DIRETO").preservar(
    DocumentoBruto(nsu="", conteudo=x, chave=ch))
ind, ac = idx.abrir(d, EMPRESA), acv.abrir(d, EMPRESA)
igual(len(ind.ausentes(ac)), 1, "há um órfão antes")
r = imp.importar(d, EMPRESA, [("lixo.xml", b"nao sou xml")])
igual(r.preservados, 0, "a importação não preservou nada")
igual(r.rejeitados, 1, "só rejeitou")
igual(r.indexados, 1, "e mesmo assim indexou o órfão que já existia")
ok(r.indice_em_dia, "terminando com o índice em dia")

secao("O relatório informa novos, duplicados, rejeitados e indexados")
d = raiz_nova()
ch, x = nfe_propria(96)
imp.importar(d, EMPRESA, [("a.xml", x)])
z = imp.importar(d, EMPRESA, [("a.xml", x),
                              ("b.xml", nfe_propria(97)[1]),
                              ("ruim.xml", b"x")]).resumo()
for campo in ("preservados", "duplicados", "rejeitados", "indexados",
              "falhas_indexacao", "indice_em_dia"):
    ok(campo in z, f"o resumo traz `{campo}`")
igual(z["preservados"], 1, "um novo")
igual(z["duplicados"], 1, "um duplicado")
igual(z["rejeitados"], 1, "um rejeitado")
igual(z["indexados"], 1, "um indexado")
igual(z["falhas_indexacao"], 0, "nenhuma falha de indexação")

secao("Reconciliar o índice não move checkpoint")
d = raiz_nova()
cp = d / EMPRESA / "ingestao"
cp.mkdir(parents=True)
alvo = cp / "NFE_DISTRIBUICAO.producao.json"
alvo.write_bytes(b'{"ult_nsu": "000000000000216"}')
antes_cp = hashlib.sha256(alvo.read_bytes()).hexdigest()
imp.importar(d, EMPRESA, [(f"n{n}.xml", nfe_propria(n)[1]) for n in (98, 99)])
idx.indexar_ausentes(d, EMPRESA)
igual(hashlib.sha256(alvo.read_bytes()).hexdigest(), antes_cp,
      "importar + indexar, checkpoint byte a byte igual")

secao("`indice.py` também não conhece checkpoint nem rede")
fonte_idx = (RAIZ / "nfse" / "backend" / "ingestao" / "indice.py").read_text("utf-8")
arv_idx = ast.parse(fonte_idx)
mods_idx = {n.module for n in ast.walk(arv_idx) if isinstance(n, ast.ImportFrom)}
mods_idx |= {a.name for n in ast.walk(arv_idx)
             if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
for proibido in ("checkpoint", "requests", "urllib", "socket", "conectores"):
    ok(not any(proibido in str(m or "") for m in mods_idx),
       f"`indice.py` não importa `{proibido}`")

secao("A varredura do índice tem uma implementação só")
fonte_pipe = (RAIZ / "nfse" / "backend" / "ingestao" / "pipeline.py").read_text("utf-8")
ok("indexar_ausentes" in fonte_pipe,
   "`pipeline.indexar_pendentes` delega a `indice.indexar_ausentes`")
ok("SELECT id_documento FROM documentos" not in fonte_pipe,
   "e não tem mais a própria cópia da consulta — foi assim que as duas divergiram")

# ══════════════════════════════════════════════════════════════════════════
#  APURAÇÃO 6C — o painel operacional
# ══════════════════════════════════════════════════════════════════════════
secao("Nota sem protocolo NÃO forma receita — a barreira e a soma no mesmo lugar")
d = raiz_nova()
ch_ok, x_ok = nfe_propria(100)
ch_sp, x_sp = nfe_propria(101, com_protocolo=False)
q = imp.painel(d, EMPRESA, [("ok.xml", x_ok), ("sp.xml", x_sp)])
igual(q["vendas"], 2, "as duas são vendas pelo CFOP")
igual(q["sem_protocolo"], 1, "uma delas está sem protocolo")
igual(q["nfe_proprias_validas"], 1, "e só uma é própria VÁLIDA")
igual(q["receita_venda"], "1234.56", "a receita conta só a autorizada")
por_ch = {x["chave_final"]: x for x in q["documentos"]}
ok(por_ch[ch_sp[-8:]]["cancelada"], "a sem protocolo sai da receita")
ok("protocolo" in (por_ch[ch_sp[-8:]]["motivo"] or ""),
   "e o motivo diz `sem protocolo`, não `cancelado`")
ok(not por_ch[ch_ok[-8:]]["cancelada"], "a autorizada permanece")

secao("A situação vem do documento, não de suposição")
igual(nz._SITUACAO_NFE[dm.AUTORIZADO], nz.NORMAL, "AUTORIZADO → NORMAL")
igual(nz._SITUACAO_NFE[dm.CANCELADO], nz.CANCELADA, "CANCELADO → CANCELADA")
igual(nz._SITUACAO_NFE[dm.DENEGADO], nz.DENEGADA, "DENEGADO → DENEGADA")
for s in (nz.CANCELADA, nz.SUBSTITUIDA, nz.DENEGADA, nz.NAO_AUTORIZADA):
    ok(s in nz.FORA_DA_RECEITA, f"{s} está fora da receita")
ok(nz.NORMAL not in nz.FORA_DA_RECEITA, "NORMAL continua dentro")

secao("O painel responde tudo que a tela precisa mostrar")
d = raiz_nova()
arqs = []
for n, cfop in ((110, "5102"), (111, "5152"), (112, "5202"),
                (113, "5929"), (114, "5199")):
    arqs.append((f"n{n}.xml", nfe_propria(n, cfop)[1]))
arqs.append(("terceiro.xml", T.xml_nfe(T.chave_ficticia(FORNECEDOR, 115),
                                       emitente=FORNECEDOR, destinatario=EMPRESA)))
arqs.append(("alheia.xml", T.xml_nfe(T.chave_ficticia(FORNECEDOR, 116),
                                     emitente=FORNECEDOR, destinatario=CLIENTE)))
arqs.append(("evt.xml", T.xml_evento(T.chave_ficticia(EMPRESA, 110))))
q = imp.painel(d, EMPRESA, arqs)
for campo in ("xml_encontrados", "nfe_proprias_validas", "terceiros",
              "duplicados", "rejeitados", "rejeitados_por_motivo",
              "sem_protocolo", "eventos_importados", "vendas",
              "transferencias", "devolucoes", "nao_receita",
              "indeterminadas", "receita_venda", "documentos"):
    ok(campo in q, f"o painel traz `{campo}`")
igual(q["xml_encontrados"], 8, "oito arquivos")
igual(q["nfe_proprias_validas"], 5, "cinco NF-e próprias válidas")
igual(q["terceiros"], 1, "uma de terceiro")
igual(q["rejeitados"], 1, "uma recusada")
igual(q["eventos_importados"], 1, "um evento")
igual(q["vendas"], 1, "uma venda")
igual(q["transferencias"], 1, "uma transferência")
igual(q["devolucoes"], 1, "uma devolução")
igual(q["nao_receita"], 1, "uma não-receita")
igual(q["indeterminadas"], 1, "uma indeterminada")
igual(q["canceladas"], 1, "uma cancelada pelo evento")
ok(q["rejeitados_por_motivo"][0]["explicacao"],
   "a recusa vem com explicação em português, não só o código")

secao("O painel é conferência — e diz isso por escrito")
ok("NÃO alimentam" in q["apenas_conferencia"], "o aviso vem no próprio retorno")
ok("DAS" in q["apenas_conferencia"], "nomeando os tributos que não são tocados")

secao("Rastreabilidade: cada linha do painel volta ao XML")
for doc in q["documentos"]:
    ok(doc["id_documento"], "documento com id")
    ok(doc["caminho"].endswith("original.xml"), "e caminho até o XML original")
    ok((d / doc["caminho"]).exists(), "que existe mesmo em disco")
    ok(len(doc["chave_final"]) == 8, "com só o final da chave exposto")

secao("As contagens do painel fecham com a tabela que ele mostra")
d = raiz_nova()
ch_c, x_c = nfe_propria(140)
arqs = [("c.xml", x_c), ("s.xml", nfe_propria(141)[1]),
        ("sp.xml", nfe_propria(142, com_protocolo=False)[1]),
        ("compra.xml", T.xml_nfe(T.chave_ficticia(FORNECEDOR, 143),
                                 emitente=FORNECEDOR, destinatario=EMPRESA)),
        ("ev.xml", T.xml_evento(ch_c))]
q = imp.painel(d, EMPRESA, arqs)
igual(len(q["documentos"]), 3, "a tabela mostra as três saídas")
igual(q["canceladas"] + q["sem_evento_conhecido"], len(q["documentos"]),
      "canceladas + sem evento = linhas da tabela — a soma fecha")
igual(q["canceladas"], 1, "uma com evento de cancelamento")
igual(q["sem_evento_conhecido"], 2, "duas sem evento conhecido")
ok(q["terceiros"] == 1, "a compra de terceiro entrou no lote")
ok("compra" not in str(q["documentos"]),
   "mas não entra na tabela de saídas nem nas contagens de situação")

secao("`canceladas` e `fora_da_receita` são coisas diferentes")
igual(q["fora_da_receita"], 2,
      "duas fora da receita: a cancelada e a sem protocolo")
igual(q["canceladas"], 1, "mas só uma foi de fato cancelada por evento")
ok(q["fora_da_receita"] >= q["canceladas"],
   "fora da receita nunca é menor que canceladas")
motivos = {d["motivo"] for d in q["documentos"] if d["cancelada"]}
ok(any("cancelado" in (m or "") for m in motivos), "um motivo fala de cancelamento")
ok(any("protocolo" in (m or "") for m in motivos), "e o outro, de protocolo")

secao("Painel de pasta: lê a pasta e não mexe nela")
d = raiz_nova()
fonte_xml = raiz_nova() / "lote"
fonte_xml.mkdir()
for n in (120, 121):
    (fonte_xml / f"nf{n}.xml").write_bytes(nfe_propria(n)[1])
(fonte_xml / "leiame.txt").write_text("nao sou xml", encoding="utf-8")
antes_pasta = sorted(x.name for x in fonte_xml.iterdir())
q = imp.painel(d, EMPRESA, pasta=fonte_xml)
igual(q["xml_encontrados"], 2, "só os .xml são lidos")
igual(q["preservados"], 2, "os dois preservados")
ok(q["indice_em_dia"], "índice em dia ao terminar")
igual(sorted(x.name for x in fonte_xml.iterdir()), antes_pasta,
      "a pasta de origem continua exatamente igual")

secao("Segunda importação da mesma pasta: só duplicatas")
q2 = imp.painel(d, EMPRESA, pasta=fonte_xml)
igual(q2["preservados"], 0, "nada novo")
igual(q2["duplicados"], 2, "as duas como duplicata")
igual(q2["indexados"], 0, "zero reindexações")
igual(q2["vendas"], q["vendas"], "e o quadro continua mostrando o lote entregue")

secao("`xml_encontrados` conta o que o usuário entregou")
d = raiz_nova()
pasta = d / EMPRESA
pasta.mkdir()
rel = nfemod.importar_xmls(pasta, EMPRESA, [
    ("v.xml", nfe_propria(130)[1]), ("lixo.xml", b"nao sou xml")])
qq = rel["painel"]
igual(qq["xml_encontrados"], 2, "dois arquivos entregues")
igual(qq["xml_avaliados_pelo_portao"], 1, "um chegou ao portão")
igual(qq["xml_de_outro_destino"], 1, "o outro tinha destino diferente")

secao("A importação MANUAL de pasta foi aposentada")
# Ela existiu para a APURAÇÃO 6C: o contador apontava a pasta do emissor. A
# captura virou automática (autXML e conectores), e rota manual que nenhuma
# tela alcança é código morto que confunde quem lê a arquitetura depois.
#
# Esta guarda deixa de vigiar o CONTEÚDO daquele bloco (que não existe mais) e
# passa a vigiar a APOSENTADORIA: que ela não volte por descuido, e que o
# portão — que nunca foi dela — continue de pé.
fonte_main = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
codigo_main = "\n".join(l for l in fonte_main.splitlines()
                        if not l.lstrip().startswith("#"))
for rota in ('@app.post("/api/nfe/importar-pasta")', '@app.post("/api/nfe/entrada")'):
    ok(rota not in codigo_main, "a rota %s não existe mais" % rota[10:-2])
for morto in ("class ImportarPastaNFe", "class CaixaEntrada"):
    ok(morto not in codigo_main, "e o modelo `%s` saiu junto" % morto[6:])
ok("importar-pasta" in fonte_main,
   "mas a lápide EXPLICA a remoção, para o próximo leitor não reinventá-la")

# A PASTA VIGIADA NÃO É A MESMA COISA, e continua.
#     `entrada/config` e `entrada/processar` são automação: a pasta se enche
#     sozinha e termina no portão. Confundi-las com a importação manual e
#     apagar tudo junto teria removido automação em nome de remover código
#     manual.
for viva in ('@app.get("/api/nfe/entrada/config")',
             '@app.post("/api/nfe/entrada/config")',
             '@app.post("/api/nfe/entrada/processar")'):
    ok(viva in codigo_main, "a pasta vigiada continua: %s" % viva[10:-2])

secao("O portão continua sendo o único ponto de importação")
ok("_importacao" in fonte_main, "o portão segue importado pelo backend")
igual(fonte_main.count("nfemod.importar_xmls"), 1,
      "e continua havendo um só ponto de importação por arquivo")

secao("A tela ficou só com o Auditor, e sem sobra")
html = (RAIZ / "web" / "nfe.html").read_text("utf-8")
# A BUSCA É PELA CHAMADA, NÃO PELA MENÇÃO. A lápide no topo do <script>
# cita as duas rotas justamente para explicar a remoção — uma guarda que
# acusasse isso estaria reprovando a própria documentação dela.
for _morta in ("/api/nfe/importar-pasta", "/api/nfe/entrada'"):
    ok(("api('" + _morta) not in html,
       "a tela não CHAMA mais %s" % _morta.rstrip(chr(39)))
igual(html.count("<script"), html.count("</script>"), "o HTML segue equilibrado")
# NENHUM ELEMENTO FANTASMA.
#     A Etapa B tirou o HTML das abas e deixou 515 linhas de JS chamando
#     elementos que não existiam mais. Não dava erro porque ninguém as
#     chamava — mas é assim que código morto sobrevive: parecendo inofensivo.
_js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
_html_puro = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S)
_ids = set(re.findall(r'id="([A-Za-z0-9_]+)"', _html_puro))
_refs = set(re.findall(r"""\$\(['"]#([A-Za-z0-9_]+)['"]\)""", _js)) | \
        set(re.findall(r"""getElementById\(['"]([A-Za-z0-9_]+)['"]\)""", _js))
igual(sorted(r for r in _refs if r not in _ids), [],
      "nenhum id citado pelo JS ficou sem elemento no HTML")
for ajudante in ("pAno", "pMesAnterior"):
    ok(re.search(r"function\s+" + ajudante + r"\b", _js),
       "`%s` sobreviveu à limpeza — o Auditor ainda a usa" % ajudante)

secao("Nada foi conectado ao cálculo de imposto")
# `painel.py` saiu do projeto junto com a tela `painel.html`. Um módulo que
# não existe não pode conhecer o portão — mas lê-lo estoura, então ele sai da
# lista em vez de virar uma asserção que mente.
for modulo in ("classificador.py", "apuracao_federal.py", "core.py"):
    txt = (RAIZ / "nfse" / "backend" / modulo).read_text("utf-8", errors="replace")
    ok("importacao" not in txt, f"`{modulo}` não conhece o portão")
ok(not (RAIZ / "nfse" / "backend" / "painel.py").exists(),
   "`painel.py` saiu do projeto (e por isso não está na lista acima)")
consumidores = []
for arq in sorted((RAIZ / "nfse" / "backend").rglob("*.py")):
    if arq.name in ("importacao.py",) or "__pycache__" in arq.parts:
        continue
    try:
        a = ast.parse(arq.read_text("utf-8", errors="replace"))
    except SyntaxError:
        continue
    nomes = {x.name for n in ast.walk(a)
             if isinstance(n, (ast.Import, ast.ImportFrom)) for x in n.names}
    if any(str(v).rsplit(".", 1)[-1] == "importacao" for v in nomes):
        consumidores.append(arq.name)
igual(sorted(consumidores),
      ["autxml.py", "fontes_emissao.py", "main.py", "migracao_legado_xml.py",
       "nfe.py"],
      "portão consumido só pela rota, por `nfe.py`, pelas FONTES e pela"
      " MIGRAÇÃO do legado — nunca por módulo fiscal")
# `migracao_legado_xml.py` entrou na NF-e 3 e entrou pelo lugar certo: em vez
# de abrir um segundo caminho de escrita no acervo, ela atravessa este mesmo
# portão informando a procedência `LEGADO_NFE`. Foi esta guarda que cobrou a
# decisão — a lista não cresce por descuido.

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
print("APURAÇÃO 6B: portão único de importação verde.")
