#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aquisição pelo `autXML` — o escritório como ator autorizado (FASE 7B).

    python teste_autxml.py

O QUE ESTA SUÍTE GARANTE
    Que o escritório consulta UMA vez, com o checkpoint dele, e que o lote é
    roteado para a empresa certa — ou preservado sem dono, nunca atribuído a
    uma empresa errada.

    A fonte real não é exercitada: não há certificado com `autXML` configurado
    para validar em produção. O que se prova aqui é o roteamento, a posse do
    checkpoint e a idempotência, com uma `Fonte` de mentira que devolve lotes
    fixos — a mesma técnica das suítes da ING.

FIXTURES fictícias, raízes temporárias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import ast
import hashlib
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import acervo as acv                 # noqa: E402
from ingestao import autxml as ax                  # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import identificacao as idf          # noqa: E402
from ingestao import importacao as imp             # noqa: E402
from ingestao import indice as idx                 # noqa: E402
from ingestao.ambiente import PRODUCAO             # noqa: E402
from ingestao.distribuicao import DocumentoBruto, Lote   # noqa: E402

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
    p = Path(tempfile.mkdtemp(prefix="autxml-"))
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


# ── os atores ──────────────────────────────────────────────────────────────
ESCRITORIO = T.cnpj_ficticio("999999990001")
CLIENTE_A = T.EMPRESA_A
CLIENTE_B = T.EMPRESA_B
FORNECEDOR = T.FORNECEDOR
DESCONHECIDA = T.cnpj_ficticio("888888880001")
CADASTRADAS = (CLIENTE_A, CLIENTE_B)


def com_autxml(bruto: bytes, autorizado=ESCRITORIO) -> bytes:
    """Acrescenta o grupo `autXML` — é ele que dá acesso ao escritório."""
    return bruto.decode("utf-8").replace(
        "<det ", f"<autXML><CNPJ>{autorizado}</CNPJ></autXML><det ", 1
    ).encode("utf-8")


def nfe(emitente, destinatario, numero=1, autorizado=ESCRITORIO):
    ch = T.chave_ficticia(emitente, numero)
    return ch, com_autxml(T.xml_nfe(ch, emitente=emitente,
                                    destinatario=destinatario), autorizado)


@dataclass
class FonteFalsa:
    """Devolve lotes combinados. Não abre socket — e a trava prova isso."""
    lotes: list = field(default_factory=list)
    consultas: list = field(default_factory=list)

    def consultar(self, desde_nsu: str):
        self.consultas.append(desde_nsu)
        return self.lotes.pop(0) if self.lotes else Lote(sem_documentos=True)


def lote(docs, ult, mx):
    return Lote(documentos=tuple(
        DocumentoBruto(nsu=str(i + 1).zfill(15), conteudo=c)
        for i, c in enumerate(docs)), ult_nsu=ult, max_nsu=mx)


def config(d, *, ativa=True, clientes=(CLIENTE_A, CLIENTE_B)):
    cfg = ax.Configuracao(identidade=ESCRITORIO, certificado="escritorio.pfx",
                          ambiente="producao", ativa=ativa)
    for c in clientes:
        cfg.declarar(c)
    ax.salvar_config(d, cfg)
    return cfg


# ══════════════════════════════════════════════════════════════════════════
secao("O escritório em `autXML` é participante do documento")
ch, x = nfe(CLIENTE_A, FORNECEDOR, 1)
motivo, detalhe, papel, chave, imped = imp.avaliar(x, ESCRITORIO)
igual(motivo, "", "o portão aceita: o escritório consta em autXML")
igual(papel, imp.AUTORIZADO, "com papel AUTORIZADO")
igual(imped, imp.SOMENTE_AUTORIZADO, "e impedido de virar receita dele")
motivo2, _, papel2, _, imped2 = imp.avaliar(x, CLIENTE_A)
igual(papel2, imp.PROPRIA, "para o CLIENTE A a mesma nota é emissão própria")
igual(imped2, imp.RECEITA_POSSIVEL, "e pode formar receita")
_, _, papel3, _, _ = imp.avaliar(x, CLIENTE_B)
igual(papel3, "", "e o CLIENTE B não participa dela")

secao("Sem `autXML`, o escritório continua sendo estranho ao documento")
ch_s = T.chave_ficticia(CLIENTE_A, 2)
x_s = T.xml_nfe(ch_s, emitente=CLIENTE_A, destinatario=FORNECEDOR)
motivo, _, _, _, _ = imp.avaliar(x_s, ESCRITORIO)
igual(motivo, imp.EMPRESA_AUSENTE, "recusado — o autXML é o que autoriza")

# ══════════════════════════════════════════════════════════════════════════
secao("Roteamento: nota emitida pelo Cliente A vai para o Cliente A")
d = ax.destino_do_documento(nfe(CLIENTE_A, FORNECEDOR, 3)[1], CADASTRADAS)
igual(d.identidade, CLIENTE_A, "destino é o CLIENTE A")
igual(d.papel, ax.EMISSAO_PROPRIA, "como emissão própria")

secao("Mesma identidade recebendo documento do Cliente B")
d = ax.destino_do_documento(nfe(CLIENTE_B, FORNECEDOR, 4)[1], CADASTRADAS)
igual(d.identidade, CLIENTE_B, "destino é o CLIENTE B")
igual(d.papel, ax.EMISSAO_PROPRIA, "também emissão própria — dele")

secao("Nota recebida: o cliente é o destinatário")
d = ax.destino_do_documento(nfe(FORNECEDOR, CLIENTE_A, 5)[1], CADASTRADAS)
igual(d.identidade, CLIENTE_A, "destino é o CLIENTE A")
igual(d.papel, ax.RECEBIDO, "como documento recebido")

secao("Emitente vence destinatário — o que buscamos é a emissão própria")
d = ax.destino_do_documento(nfe(CLIENTE_A, CLIENTE_B, 6)[1], CADASTRADAS)
igual(d.identidade, CLIENTE_A, "nota de A para B fica com A")
igual(d.papel, ax.EMISSAO_PROPRIA, "porque A foi quem emitiu")

secao("Empresa não cadastrada não é atribuída a ninguém")
d = ax.destino_do_documento(nfe(DESCONHECIDA, FORNECEDOR, 7)[1], CADASTRADAS)
igual(d.papel, ax.NAO_ROTEADO, "fica NAO_ROTEADO")
igual(d.identidade, "", "sem destino")
igual(d.motivo, ax.EMPRESA_NAO_CADASTRADA, "e o motivo é dito")

secao("Evento é roteado pela chave")
ch_a = T.chave_ficticia(CLIENTE_A, 8)
d = ax.destino_do_documento(T.xml_evento(ch_a), CADASTRADAS)
igual(d.identidade, CLIENTE_A, "o CNPJ do emitente está dentro da chave")
igual(d.papel, ax.EVENTO, "com papel EVENTO")
d = ax.destino_do_documento(T.xml_evento(T.chave_ficticia(DESCONHECIDA, 9)),
                            CADASTRADAS)
igual(d.papel, ax.NAO_ROTEADO, "evento de empresa desconhecida não é atribuído")

secao("XML ilegível não vira dono errado")
d = ax.destino_do_documento(b"nao sou xml", CADASTRADAS)
igual(d.papel, ax.NAO_ROTEADO, "NAO_ROTEADO")
igual(d.motivo, ax.SEM_PARTICIPANTE_LEGIVEL, "com motivo próprio")

# ══════════════════════════════════════════════════════════════════════════
secao("Um lote misto chega e cada documento vai ao seu lugar")
d = raiz_nova()
cfg = config(d)
docs = [(f"n{i}.xml", x) for i, x in enumerate([
    nfe(CLIENTE_A, FORNECEDOR, 10)[1],
    nfe(CLIENTE_A, FORNECEDOR, 11)[1],
    nfe(CLIENTE_B, FORNECEDOR, 12)[1],
    nfe(FORNECEDOR, CLIENTE_B, 13)[1],
    nfe(DESCONHECIDA, FORNECEDOR, 14)[1],
])]
r = ax.rotear_documentos(d, cfg, docs, CADASTRADAS)
z = r.resumo()
igual(z["documentos"], 5, "cinco documentos no lote")
igual(r.por_empresa.get(CLIENTE_A), 2, "duas para o CLIENTE A")
igual(r.por_empresa.get(CLIENTE_B), 2, "duas para o CLIENTE B")
igual(z["nao_roteados"], 1, "uma sem dono")
igual(z["por_papel"].get(ax.EMISSAO_PROPRIA), 3, "três de emissão própria")
igual(z["por_papel"].get(ax.RECEBIDO), 1, "uma recebida")
igual(z["preservados"], 5, "as cinco preservadas — nenhuma descartada")
ok(z["indice_em_dia"], "índice em dia ao terminar")

secao("Cada empresa recebeu só o que é dela")
for cliente, esperado in ((CLIENTE_A, 2), (CLIENTE_B, 2)):
    igual(len(acv.abrir(d, cliente).listar()), esperado,
          f"acervo de {cliente[:2]}… com {esperado} documento(s)")
igual(len(acv.abrir(d, ESCRITORIO).listar()), 1,
      "a não roteada ficou sob o escritório, preservada")
ok(not (d / DESCONHECIDA).exists(),
   "e NENHUMA pasta foi criada para a empresa desconhecida")

secao("O documento sem dono continua rastreável e nunca vira receita")
v = acv.abrir(d, ESCRITORIO).listar()[0]
cap = acv.abrir(d, ESCRITORIO).ler_captura(*v)
igual(cap["identidade_empresa"], ESCRITORIO, "guardado sob o escritório")
ok(cap.get("chave"), "com a chave preservada — dá para reatribuir depois")
_, _, papel, _, imped = imp.avaliar(
    acv.abrir(d, ESCRITORIO).ler_original(*v), ESCRITORIO)
igual(papel, imp.AUTORIZADO, "papel AUTORIZADO")
igual(imped, imp.SOMENTE_AUTORIZADO, "impedido de formar receita do escritório")

secao("Evento que chega DEPOIS da nota se liga a ela")
d = raiz_nova()
cfg = config(d)
ch, x = nfe(CLIENTE_A, FORNECEDOR, 20)
ax.rotear_documentos(d, cfg, [("nota.xml", x)], CADASTRADAS)
igual(len(imp.cancelamentos_no_acervo(d, CLIENTE_A)), 0, "sem evento ainda")
ax.rotear_documentos(d, cfg, [("ev.xml", T.xml_evento(ch))], CADASTRADAS)
igual(list(imp.cancelamentos_no_acervo(d, CLIENTE_A)), [ch],
      "o cancelamento chegou depois e achou a nota pela chave")
especies = sorted({e for e, _ in acv.abrir(d, CLIENTE_A).listar()})
igual(especies, ["EVENTO_NFE", "NFE55"], "nota e evento, cada um na sua espécie")

secao("Duplicidade entre autXML e outra fonte: o acervo resolve")
d = raiz_nova()
cfg = config(d)
ch, x = nfe(CLIENTE_A, FORNECEDOR, 30)
# primeiro pela importação manual da 6C
r1 = imp.importar(d, CLIENTE_A, [("manual.xml", x)])
igual(r1.preservados, 1, "a manual preservou")
antes = len(acv.abrir(d, CLIENTE_A).listar())
# agora o MESMO documento chega pelo autXML
r2 = ax.rotear_documentos(d, cfg, [("nsu-42.xml", x)], CADASTRADAS)
igual(r2.duplicados, 1, "o autXML reconhece a duplicata")
igual(r2.preservados, 0, "e não preserva de novo")
igual(len(acv.abrir(d, CLIENTE_A).listar()), antes,
      "o acervo não cresceu — não há segunda nota")
igual(len({i for _, i in acv.abrir(d, CLIENTE_A).listar()}), antes,
      "e o id_documento é o mesmo, por identidade e hash")

# ══════════════════════════════════════════════════════════════════════════
secao("O checkpoint é do ESCRITÓRIO, não dos clientes")
d = raiz_nova()
cfg = config(d)
fonte = FonteFalsa(lotes=[lote([nfe(CLIENTE_A, FORNECEDOR, 40)[1],
                                nfe(CLIENTE_B, FORNECEDOR, 41)[1]],
                               "000000000000042", "000000000000042")])
rel = ax.sincronizar(d, cfg, lambda i, a: fonte, CADASTRADAS)
ok(rel.executou, "a sincronização rodou")
igual(rel.nsu_antes, "000000000000000", "começou do zero")
igual(rel.nsu_depois, "000000000000042", "e avançou para o NSU do lote")
repo = cpm.RepositorioCheckpoint(d)
cp_esc = repo.carregar(ESCRITORIO, ax.SERVICO, PRODUCAO)
igual(cp_esc.ult_nsu, "000000000000042", "o checkpoint do escritório andou")
for cliente in (CLIENTE_A, CLIENTE_B):
    ok(not repo.existe(cliente, ax.SERVICO, PRODUCAO),
       f"e o cliente {cliente[:2]}… NÃO ganhou checkpoint nenhum")
igual(len(fonte.consultas), 1,
      "UMA chamada trouxe as notas dos dois clientes")

secao("Uma consulta serve todos os clientes")
igual(len(fonte.consultas), 1,
      "o número de consultas não cresce com o número de clientes")
ok(len(fonte.consultas) < len(CADASTRADAS),
   "menos consultas do que clientes — não há uma por CNPJ")
igual(fonte.consultas[0], "000000000000000",
      "e ela parte do NSU do escritório, não de nenhum cliente")
igual(rel.roteamento.por_empresa.get(CLIENTE_A), 1, "e roteou para o A")
igual(rel.roteamento.por_empresa.get(CLIENTE_B), 1, "e para o B")

secao("Reinício preserva a sequência")
fonte2 = FonteFalsa(lotes=[lote([nfe(CLIENTE_A, FORNECEDOR, 50)[1]],
                                "000000000000043", "000000000000043")])
cfg2 = ax.carregar_config(d)          # relê do disco, como faria um reinício
igual(cfg2.identidade, ESCRITORIO, "a config voltou do disco")
rel2 = ax.sincronizar(d, cfg2, lambda i, a: fonte2, CADASTRADAS)
igual(rel2.nsu_antes, "000000000000042", "retomou de onde parou")
igual(fonte2.consultas[0], "000000000000042", "e perguntou a partir dali")
igual(rel2.nsu_depois, "000000000000043", "avançando um NSU")

secao("Lote vazio não move o ponteiro")
fonte3 = FonteFalsa(lotes=[])
rel3 = ax.sincronizar(d, ax.carregar_config(d), lambda i, a: fonte3, CADASTRADAS)
igual(rel3.nsu_depois, "000000000000043", "o NSU ficou onde estava")
igual(rel3.roteamento.documentos, 0, "nenhum documento")

secao("A ING não é tocada: checkpoints isolados")
d = raiz_nova()
cfg = config(d)
repo = cpm.RepositorioCheckpoint(d)
cp_ing = repo.carregar(CLIENTE_A, ax.SERVICO, PRODUCAO)
cp_ing.ult_nsu = "000000000000216"
repo.salvar(cp_ing)
antes = hashlib.sha256(
    repo.caminho(CLIENTE_A, ax.SERVICO, PRODUCAO).read_bytes()).hexdigest()
fonte = FonteFalsa(lotes=[lote([nfe(CLIENTE_A, FORNECEDOR, 60)[1]],
                               "000000000000099", "000000000000099")])
ax.sincronizar(d, cfg, lambda i, a: fonte, CADASTRADAS)
igual(hashlib.sha256(
    repo.caminho(CLIENTE_A, ax.SERVICO, PRODUCAO).read_bytes()).hexdigest(),
    antes, "o checkpoint do CLIENTE A ficou byte a byte igual")
igual(repo.carregar(CLIENTE_A, ax.SERVICO, PRODUCAO).ult_nsu, "000000000000216",
      "no NSU em que a ING o deixou")
igual(repo.carregar(ESCRITORIO, ax.SERVICO, PRODUCAO).ult_nsu, "000000000000099",
      "enquanto o do escritório andou sozinho")

# ══════════════════════════════════════════════════════════════════════════
secao("Situação: declaração não basta, evidência manda")
d = raiz_nova()
cfg = ax.Configuracao(identidade=ESCRITORIO, ativa=True)
c = cfg.declarar(CLIENTE_A)
igual(c.situacao, ax.AGUARDANDO, "declarar deixa AGUARDANDO, nunca ATIVA")
igual(cfg.cliente(CLIENTE_B), None, "quem não declarou nem consta")
fonte = FonteFalsa(lotes=[lote([nfe(CLIENTE_A, FORNECEDOR, 70)[1]],
                               "000000000000001", "000000000000001")])
ax.sincronizar(d, cfg, lambda i, a: fonte, CADASTRADAS)
igual(cfg.cliente(CLIENTE_A).situacao, ax.ATIVA,
      "chegou documento → ATIVA, por evidência")
ok(cfg.cliente(CLIENTE_A).primeira_evidencia_em, "com a data da primeira prova")
igual(cfg.cliente(CLIENTE_A).documentos_roteados, 1, "e a contagem por empresa")
ok(cfg.cliente(CLIENTE_A).ultima_sincronizacao, "e a última sincronização")

secao("Silêncio repetido vira SEM_EVIDENCIA, não ATIVA")
d = raiz_nova()
cfg = ax.Configuracao(identidade=ESCRITORIO, ativa=True)
cfg.declarar(CLIENTE_B)
for _ in range(ax.SINCRONIZACOES_PARA_DUVIDAR):
    ax.sincronizar(d, cfg, lambda i, a: FonteFalsa(), CADASTRADAS)
igual(cfg.cliente(CLIENTE_B).situacao, ax.SEM_EVIDENCIA,
      f"após {ax.SINCRONIZACOES_PARA_DUVIDAR} sincronizações sem nada")
igual(cfg.cliente(CLIENTE_B).documentos_roteados, 0, "sem documento nenhum")

secao("ATIVA não regride por um lote vazio")
d = raiz_nova()
cfg = ax.Configuracao(identidade=ESCRITORIO, ativa=True)
cfg.declarar(CLIENTE_A)
fonte = FonteFalsa(lotes=[lote([nfe(CLIENTE_A, FORNECEDOR, 80)[1]],
                               "000000000000001", "000000000000001")])
ax.sincronizar(d, cfg, lambda i, a: fonte, CADASTRADAS)
for _ in range(5):
    ax.sincronizar(d, cfg, lambda i, a: FonteFalsa(), CADASTRADAS)
igual(cfg.cliente(CLIENTE_A).situacao, ax.ATIVA,
      "a autorização já foi comprovada uma vez — não se desprova por silêncio")

secao("Desativada por padrão, e desativada não consulta")
igual(ax.Configuracao().ativa, False, "nasce desativada")
d = raiz_nova()
cfg = ax.Configuracao(identidade=ESCRITORIO, ativa=False)
fonte = FonteFalsa(lotes=[lote([nfe(CLIENTE_A, FORNECEDOR, 90)[1]], "1", "1")])
rel = ax.sincronizar(d, cfg, lambda i, a: fonte, CADASTRADAS)
ok(not rel.executou, "não executou")
ok("desativada" in rel.motivo, "e disse por quê")
igual(fonte.consultas, [], "a fonte não foi consultada nenhuma vez")

secao("Identidade inválida não consulta")
rel = ax.sincronizar(raiz_nova(), ax.Configuracao(identidade="123", ativa=True),
                     lambda i, a: FonteFalsa(), CADASTRADAS)
ok(not rel.executou, "não executou")
ok("inválida" in rel.motivo, "com o motivo escrito")

secao("Config sobrevive ao disco e não expõe a identidade no relatório")
d = raiz_nova()
cfg = config(d)
lida = ax.carregar_config(d)
igual(lida.identidade, ESCRITORIO, "a identidade volta inteira (é preciso consultar)")
igual(len(lida.clientes), 2, "com os dois clientes")
ok(ESCRITORIO not in str(lida.para_json()), "mas o relatório mascara")
igual(ax.carregar_config(raiz_nova()).identidade, "", "sem arquivo, config vazia")
ax.caminho_config(d).write_text("{ nao é json", encoding="utf-8")
igual(ax.carregar_config(d).ativa, False, "config corrompida vira desativada")

# ══════════════════════════════════════════════════════════════════════════
secao("É uma FONTE, não um motor fiscal")
fonte_txt = (RAIZ / "nfse" / "backend" / "ingestao" / "autxml.py").read_text("utf-8")
arv = ast.parse(fonte_txt)
chamadas = {getattr(n.func, "attr", "") or getattr(n.func, "id", "")
            for n in ast.walk(arv) if isinstance(n, ast.Call)}
# Com o RECEPTOR junto: `preservar` mudou de sentido na NF-e 7.
# `acervo.preservar` grava DOCUMENTO e continua proibido aqui — é papel do
# portão. `resposta_bruta.preservar` grava a PROVA DO TRANSPORTE, que é outro
# ato: nenhum documento fiscal passa por ela, e todo caminho de captura tem de
# deixar essa prova antes de mover o ponteiro.
qualificadas = {f"{getattr(n.func.value, 'id', '')}.{n.func.attr}"
                for n in ast.walk(arv) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(getattr(n.func, "value", None), ast.Name)}
for proibida in ("identificar", "de_documento_nfe",
                 "receita_de_vendas", "classificar", "calcular_das", "rbt12"):
    ok(proibida not in chamadas, f"não chama `{proibida}`")
for proibida in ("acv.preservar", "acervo.preservar", "ac.preservar"):
    ok(proibida not in qualificadas,
       f"não chama `{proibida}` — documento é do portão")
ok("bruta.preservar" in qualificadas,
   "mas CHAMA `bruta.preservar`: a prova do transporte é obrigatória em "
   "todo caminho que move checkpoint")
ok("importar" in chamadas, "e chama o portão")
modulos = {n.module for n in ast.walk(arv) if isinstance(n, ast.ImportFrom)}
for proibido in ("requests", "urllib", "socket", "conectores",
                 "servico_distribuicao", "pipeline"):
    ok(not any(proibido in str(m or "") for m in modulos),
       f"não importa `{proibido}` — o transporte é injetado")
numeros = {n.value for n in ast.walk(arv)
           if isinstance(n, ast.Constant) and isinstance(n.value, float)}
igual(numeros, set(), "nenhum literal decimal — não há fórmula aqui")

secao("NFC-e não é forçada nesta via")
ok("NFCE" not in fonte_txt.replace("NFCE65", ""),
   "o módulo não trata NFC-e como equivalente")
ok("NFC-e" in fonte_txt, "e diz explicitamente por que ficou de fora")

secao("Quem consome o autXML, e para quê")
# Até a NF-e 7 nada importava este módulo, e a suíte guardava isso. Mudou por
# uma razão só: o `controlador` precisa saber QUEM É O HUB para não consultá-lo
# no ciclo por empresa. Ele lê a identidade e nada mais — não sincroniza, não
# roteia, não captura. A fonte continua desligada; o que passou a existir é
# alguém perguntando o nome do dono da fila.
backend = RAIZ / "nfse" / "backend"
consumidores = []
for arq in sorted(backend.rglob("*.py")):
    if arq.name == "autxml.py" or "__pycache__" in arq.parts:
        continue
    try:
        a = ast.parse(arq.read_text("utf-8", errors="replace"))
    except SyntaxError:
        continue
    nomes = {x.name for n in ast.walk(a)
             if isinstance(n, (ast.Import, ast.ImportFrom)) for x in n.names}
    if any(str(v).rsplit(".", 1)[-1] == "autxml" for v in nomes):
        consumidores.append(arq.name)
igual(consumidores, ["controlador.py"],
      "só o controlador consome, e a lista é fechada de propósito")

ctl_txt = (backend / "ingestao" / "controlador.py").read_text("utf-8")
ok("_autxml.e_hub" in ctl_txt, "e ele chama apenas `e_hub`")
for proibido in ("sincronizar", "rotear_documentos", "destino_do_documento"):
    ok(f"_autxml.{proibido}" not in ctl_txt,
       f"o controlador NÃO chama `{proibido}` — captura não é papel dele")


# ══════════════════════════════════════════════════════════════════════════
secao("A RESPOSTA BRUTA é preservada, e antes do checkpoint")
# Este é o único caminho de captura que não passa pelo `DistribuicaoRunner`:
# ele carrega o próprio checkpoint. Por isso não herdou a preservação que o
# runner ganhou na NF-e 7, e seria a única via a avançar o NSU sem deixar
# prova do que o serviço respondeu.
from ingestao import resposta_bruta as _bruta                   # noqa: E402


class FonteComBruto(FonteFalsa):
    """Como a real: expõe `ultima_resposta.bruto`."""

    class _Resp:
        def __init__(self, bruto):
            self.bruto = bruto

    def __init__(self, lotes=None, bruto=b"<retDistDFeInt>prova</retDistDFeInt>"):
        FonteFalsa.__init__(self, lotes=list(lotes or []))
        self._bruto = bruto
        self.ultima_resposta = None

    def consultar(self, desde_nsu):
        r = FonteFalsa.consultar(self, desde_nsu)
        self.ultima_resposta = self._Resp(self._bruto)
        return r


raiz = raiz_nova()
cfg = config(raiz)
_, doc = nfe(CLIENTE_A, FORNECEDOR, 701)
fonte = FonteComBruto([lote([doc], "000000000000010", "000000000000010")])
rel = ax.sincronizar(raiz, cfg, lambda i, a: fonte, CADASTRADAS)

guardadas = sorted((Path(raiz) / ESCRITORIO / "respostas").rglob("*.xml"))
igual(len(guardadas), 1, "a resposta bruta foi guardada")
igual(guardadas[0].read_bytes(), b"<retDistDFeInt>prova</retDistDFeInt>",
      "com os bytes exatos do transporte")
ok(_bruta.conferir(guardadas[0]), "e o SHA-256 do manifesto confere")

import json as _json
manif = _json.loads(guardadas[0].with_suffix(".json").read_text("utf-8"))
igual(manif["servico"], ax.SERVICO, "com o serviço")
ok(manif["tentativa"], "e o identificador da tentativa")
ok("***" in manif["empresa"], "com o escritório MASCARADO no recibo")
ok(ESCRITORIO not in _json.dumps(manif),
   "e o CNPJ inteiro não vaza no manifesto")

ok(guardadas[0].parent.parts[-3] == ESCRITORIO
   or ESCRITORIO in str(guardadas[0]),
   "a prova fica sob o ESCRITÓRIO, que é quem consultou")


secao("Não conseguir guardar a prova SEGURA o checkpoint")
raiz = raiz_nova()
cfg = config(raiz)
_, doc = nfe(CLIENTE_A, FORNECEDOR, 702)
# Chave privada nunca vai para o disco: a preservação recusa.
fonte = FonteComBruto([lote([doc], "000000000000099", "000000000000099")],
                      bruto=b"-----BEGIN PRIVATE KEY-----")
rel = ax.sincronizar(raiz, cfg, lambda i, a: fonte, CADASTRADAS)

cp = cpm.RepositorioCheckpoint(raiz).carregar(
    ESCRITORIO, ax.SERVICO, PRODUCAO)
igual(cp.ult_nsu, "000000000000000",
      "o checkpoint NÃO andou — sem prova no disco, o ponteiro fica")
ok("resposta bruta" in (rel.motivo or ""),
   "e o motivo diz exatamente o que faltou")
igual(len(list((Path(raiz) / ESCRITORIO / "respostas").rglob("*.xml"))
          if (Path(raiz) / ESCRITORIO / "respostas").exists() else []), 0,
      "nada de sensível foi escrito")


secao("Fonte sem `bruto` continua funcionando")
raiz = raiz_nova()
cfg = config(raiz)
_, doc = nfe(CLIENTE_A, FORNECEDOR, 703)
fonte = FonteFalsa([lote([doc], "000000000000007", "000000000000007")])
ax.sincronizar(raiz, cfg, lambda i, a: fonte, CADASTRADAS)
cp = cpm.RepositorioCheckpoint(raiz).carregar(
    ESCRITORIO, ax.SERVICO, PRODUCAO)
igual(cp.ult_nsu, "000000000000007",
      "o ponteiro anda: a preservação só age quando há bruto")


# ══════════════════════════════════════════════════════════════════════════
secao("O HUB: quem é, e de onde vem essa verdade")
raiz = raiz_nova()
igual(ax.identidade_do_hub(raiz), "",
      "sem `autxml.json`, não há hub")
config(raiz)
igual(ax.identidade_do_hub(raiz), ESCRITORIO,
      "com o arquivo, o hub é quem está em `identidade`")
ok(ax.e_hub(raiz, ESCRITORIO), "o escritório É o hub")
ok(not ax.e_hub(raiz, CLIENTE_A), "e o cliente NÃO é")
ok(not ax.e_hub(raiz, ""), "identidade vazia não é hub")

# A fonte da verdade é uma só: o arquivo. Não há flag paralela.
fonte_txt_hub = (RAIZ / "nfse" / "backend" / "ingestao"
                 / "autxml.py").read_text("utf-8")
corpo_hub = fonte_txt_hub.split("def identidade_do_hub")[1].split("def e_hub")[0]
ok("carregar_config" in corpo_hub,
   "`identidade_do_hub` lê o próprio `autxml.json`, sem segundo cadastro")
ok("ARQUIVO_CONFIG" not in corpo_hub.replace("carregar_config", ""),
   "e não abre o arquivo por conta própria: usa a leitura que já existe")


secao("O ciclo por empresa NÃO consulta o hub — e diz por quê")
# Os dois caminhos usam o MESMO serviço `NFE_DISTRIBUICAO`, logo o MESMO
# arquivo de checkpoint. Se o ciclo também consultasse o escritório, dois
# donos escreveriam o mesmo ponteiro e o segundo pularia o que o primeiro
# trouxe.
from ingestao import controlador as _ctl                        # noqa: E402

igual(ax.SERVICO, "NFE_DISTRIBUICAO",
      "(o autXML usa o mesmo serviço do ciclo)")
igual(_ctl.SERVICO_PADRAO, ax.SERVICO,
      "(e portanto o MESMO arquivo de checkpoint)")

raiz = raiz_nova()
config(raiz)
e = _ctl.avaliar(raiz, ESCRITORIO)
ok(not e.pode, "o hub não entra no ciclo por empresa")
igual(e.motivo, _ctl.MOTIVO_HUB, "com o motivo do hub")
ok("autXML" in e.motivo, "que diz qual é a via correta dele")
ok("***" in e.identidade_mascarada,
   "e ele APARECE no relatório, mascarado — recusa é resultado com "
   "motivo, nunca pré-filtro silencioso")

d = _ctl.avaliar(raiz, CLIENTE_A)
ok(d.motivo != _ctl.MOTIVO_HUB, "o cliente não é confundido com o hub")


secao("Sem `autxml.json`, ninguém é hub — e o ciclo não muda")
raiz = raiz_nova()
e = _ctl.avaliar(raiz, ESCRITORIO)
ok(e.motivo != _ctl.MOTIVO_HUB,
   "sem configuração, o escritório é uma empresa como outra qualquer")

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
print("autXML: fonte pronta e desligada, roteamento verde.")
