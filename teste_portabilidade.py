#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes de portabilidade do Fiscale — PORT 1.

Cobre a raiz única de dados, a migração do formato antigo, o certificado
guardado dentro dos dados e o estado "aguardando senha".

    python teste_portabilidade.py

Só stdlib + os módulos do projeto. Nada toca os dados reais: tudo roda em
pastas temporárias.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

# Pastas temporárias saem de `teste_apoio.pasta_temp()`, que as apaga no fim
# do processo. Antes eram `tempfile.mkdtemp()` soltos, e sobravam em %TEMP%.
import teste_apoio as _ta  # noqa: E402

import fiscale_dados as fd            # noqa: E402
import fiscale_migracao as fm         # noqa: E402
import seguranca                      # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []


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


def tmp(nome):
    return Path(_ta.pasta_temp(prefix=f"port1_{nome}_"))


_raiz_padrao_real = fd.raiz_padrao


def apontar(legado: Path | None, raiz_nova: Path, padrao: bool = True) -> None:
    """Direciona a migração para um par de pastas deste teste.

    A migração descobre os legados por variáveis de módulo. Sem religar isso a
    cada seção, uma seção herdaria o sandbox da anterior e o teste passaria (ou
    falharia) pelo motivo errado — foi exatamente o que aconteceu na primeira
    versão deste arquivo. `legado=None` significa "não existe legado nenhum".

    `padrao=True` declara que, para este teste, `raiz_nova` faz o papel da raiz
    natural da instalação — sem isso a migração se recusa a adotar legado, que
    é a trava certa em produção e um estorvo aqui."""
    inexistente = raiz_nova / "__sem_legado__"
    fd.LEGADO_NFSE = legado if legado is not None else inexistente
    fd.LEGADO_FISCALE = inexistente
    fd._raiz_cache = None
    fd.raiz(raiz_nova)
    fd.raiz_padrao = (lambda: raiz_nova.resolve()) if padrao else _raiz_padrao_real


# ══════════════════════════════════════════════════════════════════════════
secao("Raiz única — quem decide onde ficam os dados")

antes_env = os.environ.get("FISCALE_DADOS")
p1 = tmp("env")
os.environ["FISCALE_DADOS"] = str(p1)
fd._raiz_cache = None
igual(fd.raiz(), p1.resolve(), "FISCALE_DADOS manda")

del os.environ["FISCALE_DADOS"]
fd._raiz_cache = None
igual(fd.raiz(), (Path.home() / "Fiscale" / "dados").resolve(),
      "sem FISCALE_DADOS, o padrão é ~/Fiscale/dados")

secao("Modo portátil — a marca manda os dados para junto do programa")
# Defeito pego no teste do pacote portátil: `fiscale_dados.py` mora em `app\`,
# e olhar só `app\dados` fazia a versão de pendrive gravar em
# C:\Users\<voce>\Fiscale — deixando de ser portátil sem avisar.
falso_app = tmp("portatil")
(falso_app / "app").mkdir(parents=True)
(falso_app / "dados").mkdir(parents=True)
_pasta_real = fd.pasta_do_app
fd.pasta_do_app = lambda: falso_app / "app"
try:
    fd._raiz_cache = None
    igual(fd.raiz_padrao(), (Path.home() / "Fiscale" / "dados").resolve(),
          "sem a marca, os dados vão para a pasta do usuário")
    (falso_app / "dados" / fd.MARCA_PORTATIL).write_text("x", encoding="utf-8")
    fd._raiz_cache = None
    igual(fd.raiz_padrao(), (falso_app / "dados").resolve(),
          "com a marca AO LADO de app\\, os dados ficam junto do programa")
    igual(fd.raiz(), (falso_app / "dados").resolve(), "e é isso que raiz() devolve")
    ok(fd.raiz_e_padrao(), "e conta como raiz natural da instalação")
    # a marca DENTRO de app/ também vale, para quem montar assim
    (falso_app / "dados" / fd.MARCA_PORTATIL).unlink()
    (falso_app / "app" / "dados").mkdir(parents=True)
    (falso_app / "app" / "dados" / fd.MARCA_PORTATIL).write_text("x", encoding="utf-8")
    fd._raiz_cache = None
    igual(fd.raiz_padrao(), (falso_app / "app" / "dados").resolve(),
          "a marca dentro de app\\ também é aceita")
finally:
    fd.pasta_do_app = _pasta_real
    fd._raiz_cache = None

secao("Diagnóstico — reconhece um runtime que vive ao lado do código")
# Mesmo defeito da marca portátil, noutro lugar: na distribuição, `runtime\` é
# IRMÃO de `app\`, não filho. Olhar só dentro de `app` fazia o diagnóstico
# dizer "PENDENTE — PORT 4" rodando de dentro do próprio pacote portátil.
import diagnostico_instalacao as diag        # noqa: E402
_raiz_app_real = diag.RAIZ_APP
_base_real = sys.base_prefix
pacote = tmp("pacote_portatil")
(pacote / "app").mkdir(parents=True)
(pacote / "runtime").mkdir(parents=True)
try:
    diag.RAIZ_APP = pacote / "app"
    sys.base_prefix = str(pacote / "runtime")
    sys.prefix = str(pacote / "runtime")
    portatil, motivo = diag.runtime_portatil()
    ok(portatil, "runtime irmão de app\\ conta como portátil")
    ok("dentro da própria pasta" in motivo, f"com a explicação certa: {motivo[:50]}…")

    sys.base_prefix = sys.prefix = str(pacote / "app" / "runtime")
    (pacote / "app" / "runtime").mkdir(parents=True)
    ok(diag.runtime_portatil()[0], "runtime dentro de app\\ também conta")

    sys.base_prefix = sys.prefix = r"C:\Python312"
    portatil, motivo = diag.runtime_portatil()
    ok(not portatil, "Python instalado no computador NÃO conta como portátil")

    sys.prefix = r"C:\qualquer\.venv"
    sys.base_prefix = r"C:\Python312"
    portatil, motivo = diag.runtime_portatil()
    ok(not portatil, "e uma .venv, muito menos")
    ok(".venv" in motivo, "dizendo que é ambiente virtual")
finally:
    diag.RAIZ_APP = _raiz_app_real
    sys.base_prefix = _base_real
    sys.prefix = _base_real if _base_real == sys.prefix else sys.prefix

secao("Raiz única — continuação")
p2 = tmp("forcado")
igual(fd.raiz(p2), p2.resolve(), "os testes conseguem forçar a raiz")
ok(fd.raiz() == p2.resolve(), "e a escolha fica valendo (cache)")
ok(fd.pasta_certs() == p2.resolve() / "certs", "certs/ fica dentro da raiz")
ok(fd.pasta_certs().is_dir(), "e é criada sozinha")
ok(fd.pasta_empresa("64.567.004/0001-39").name == "64567004000139",
   "a pasta da empresa usa só os dígitos do CNPJ")

secao("Raiz única — os dois lados do sistema apontam para o MESMO lugar")
src_server = (RAIZ / "fiscale_server.py").read_text("utf-8")
src_main = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
src_runner = (RAIZ / "nfse" / "runner.py").read_text("utf-8")


def sem_comentario(fonte: str) -> str:
    """Só o código: tira docstrings e comentários. Um teste que lê a explicação
    do que mudou e acha que a mudança não aconteceu não serve para nada."""
    import ast, io, tokenize
    arvore = ast.parse(fonte)
    docs = set()
    for n in ast.walk(arvore):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docs.add(d)
    saida = []
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.string.strip("\"'brf") in docs:
            continue
        saida.append(tok.string)
    return "\n".join(saida)


codigo_runner = sem_comentario(src_runner)
codigo_main = sem_comentario(src_main)
ok("fdados.raiz()" in src_server, "fiscale_server usa fiscale_dados.raiz()")
ok("fdados.raiz()" in src_main, "o backend do NFS-e usa fiscale_dados.raiz()")
ok("sys.frozen" not in codigo_runner,
   "o runner NÃO força mais sys.frozen (era o que partia os dados em dois)")
ok("SistemaNFSe" not in codigo_main, "main.py não fala mais em ~/SistemaNFSe")
ok('os.environ.get("FISCALE_DADOS") or os.path.join' not in codigo_main,
   "sumiram as decisões de pasta duplicadas no main.py")

secao("Caminho do certificado — relativo no disco, absoluto na memória")
raiz = fd.raiz(tmp("cert"))
certs = fd.pasta_certs()
(certs / "EMPRESA.pfx").write_bytes(b"x")
igual(fd.resolver_cert("certs/EMPRESA.pfx"), str((certs / "EMPRESA.pfx").resolve()),
      "relativo vira absoluto desta máquina")
igual(fd.relativizar_cert(str(certs / "EMPRESA.pfx")), "certs/EMPRESA.pfx",
      "e absoluto volta a relativo")
fora = tmp("fora") / "OUTRO.pfx"
fora.parent.mkdir(parents=True, exist_ok=True)
fora.write_bytes(b"y")
igual(fd.relativizar_cert(str(fora)), str(fora),
      "arquivo fora dos dados continua absoluto (não inventamos caminho)")
ok(fd.dentro_dos_dados("certs/X.pfx"), "relativo conta como dentro dos dados")
ok(not fd.dentro_dos_dados(str(fora)), "e o de fora, não")
igual(fd.resolver_cert(r"C:\Users\alguem\Downloads\LEGADO.pfx"),
      r"C:\Users\alguem\Downloads\LEGADO.pfx",
      "cadastro antigo com caminho absoluto continua sendo respeitado")

# ══════════════════════════════════════════════════════════════════════════
secao("Migração — nada se perde no caminho")

base = tmp("migra")
novo = base / "Fiscale" / "dados"
legado = base / "SistemaNFSe" / "dados"
downloads = base / "Downloads"
for p in (novo, legado, downloads):
    p.mkdir(parents=True)

(novo / "usuarios.json").write_text('{"admin":{"hash":"h","sal":"s"}}', "utf-8")
(novo / "state_clientes.json").write_text('{"clientes":[{"cnpj":"1","nome":"A"}]}', "utf-8")
(novo / "sessoes.db").write_bytes(b"efemero")
(novo / "64567004000139" / "municipal").mkdir(parents=True)
(novo / "64567004000139" / "municipal" / "extratos.json").write_text('{"extratos":[1]}', "utf-8")

(downloads / "ACME.pfx").write_bytes(b"PFX-ACME")
reg = [
    {"id": "11111111000111", "cnpj": "11111111000111", "nome": "ACME LTDA",
     "caminho": str(downloads / "ACME.pfx"), "senha_protegida": "dpapi:AAA"},
    {"id": "22222222000122", "cnpj": "22222222000122", "nome": "SUMIDO LTDA",
     "caminho": str(base / "nao" / "existe.pfx"), "senha_protegida": "dpapi:BBB"},
]
(legado / "certificados.json").write_text(json.dumps(reg), "utf-8")
(legado / "entrada_config.json").write_text(
    json.dumps({"pasta": str(Path.home() / "Downloads"), "auto": True}), "utf-8")
for cnpj, nsu in (("64567004000139", 7777), ("33333333000133", 12)):
    d = legado / cnpj / "nfe"
    d.mkdir(parents=True)
    (d / "estado.json").write_text(json.dumps({"ultNSU": nsu, "maxNSU": nsu}), "utf-8")
    (d / "nota.xml").write_text("<x/>", "utf-8")

apontar(legado, novo)
rel = fm.garantir(novo)

ok(rel["mudou"], "a migração rodou")
igual(rel["versao_depois"], fm.VERSAO_ATUAL, "e deixou a raiz no formato atual")

ok((novo / "certificados.json").exists(), "certificados.json veio para a raiz única")
est = json.loads((novo / "64567004000139" / "nfe" / "estado.json").read_text("utf-8"))
igual(est["ultNSU"], 7777, "ultNSU preservado — a distribuição NÃO reinicia do NSU 0")
igual(json.loads((novo / "33333333000133" / "nfe" / "estado.json").read_text("utf-8"))["ultNSU"], 12,
      "ultNSU da segunda empresa também")
igual(json.loads((novo / "64567004000139" / "municipal" / "extratos.json").read_text("utf-8")),
      {"extratos": [1]}, "o que já existia na raiz nova NÃO foi sobrescrito")
ok((novo / "64567004000139" / "nfe" / "nota.xml").exists(),
   "e a mesma empresa recebeu, fundido, o que só existia no legado")
ok((novo / "usuarios.json").exists() and (novo / "state_clientes.json").exists(),
   "o que já estava na raiz nova continua lá")

reg2 = json.loads((novo / "certificados.json").read_text("utf-8"))
igual(len(reg2), 2, "nenhum cadastro perdido")
acme = next(c for c in reg2 if c["cnpj"] == "11111111000111")
igual(acme["caminho"], "certs/ACME.pfx", "o .pfx virou caminho relativo")
ok((novo / "certs" / "ACME.pfx").exists(), "e o arquivo foi copiado para dentro dos dados")
igual((novo / "certs" / "ACME.pfx").read_bytes(), b"PFX-ACME", "byte a byte")
igual(acme.get("caminho_origem"), str(downloads / "ACME.pfx"),
      "guardando de onde ele veio, para rastro")
sumido = next(c for c in reg2 if c["cnpj"] == "22222222000122")
ok(Path(sumido["caminho"]).is_absolute(),
   "cadastro cujo arquivo sumiu fica como estava — não inventamos caminho")
igual(sumido["senha_protegida"], "dpapi:BBB", "e a senha protegida não é tocada")
igual(acme["senha_protegida"], "dpapi:AAA", "nenhuma senha é decifrada na migração")
ok((novo / "certificados.json.pre-port1").exists(), "o cadastro anterior fica guardado")

ent = json.loads((novo / "entrada_config.json").read_text("utf-8"))
igual(ent["pasta"], "%DOWNLOADS%", "a pasta vigiada vira marcador portátil")
igual(fm.caminho_entrada("%DOWNLOADS%"), str(Path.home() / "Downloads"),
      "e resolve para a Downloads desta máquina")
igual(fm.caminho_entrada(r"D:\Notas"), r"D:\Notas", "caminho comum passa intacto")

ok(not (novo / "sessoes.db").exists() or (novo / "sessoes.db").read_bytes() == b"efemero",
   "sessoes.db não é migrado do legado (é efêmero)")

secao("Migração — os DOIS tipos de estado de sincronismo")
# São dois arquivos com o mesmo nome e esquemas diferentes, e os dois guardam
# a posição de onde parou. Perder qualquer um faz a próxima sincronização
# rebaixar tudo desde o começo — e, na NF-e, isso costuma render `cStat 656`
# (consumo indevido) da Receita.
#     <cnpj>/estado.json       NFS-e nacional -> {"ultimoNSU": ...}
#     <cnpj>/nfe/estado.json   NF-e DFe       -> {"ultNSU":  ..., "maxNSU": ...}
b3 = tmp("nsu")
n3, l3 = b3 / "novo", b3 / "legado"
n3.mkdir(); l3.mkdir()
esperado = {}
for cnpj, nfse_nsu, nfe_nsu in (("44444444000144", 276, 454225),
                                ("55555555000155", 77, 45828)):
    (l3 / cnpj / "nfe").mkdir(parents=True)
    a = l3 / cnpj / "estado.json"
    b = l3 / cnpj / "nfe" / "estado.json"
    a.write_text(json.dumps({"ultimoNSU": nfse_nsu, "atualizadoEm": "2026-07-24T17:58:49"}), "utf-8")
    b.write_text(json.dumps({"ultNSU": nfe_nsu, "maxNSU": nfe_nsu}), "utf-8")
    esperado[str(Path(cnpj) / "estado.json")] = a.read_bytes()
    esperado[str(Path(cnpj) / "nfe" / "estado.json")] = b.read_bytes()
apontar(l3, n3)
fm.garantir(n3)

achados = {str(p.relative_to(n3)): p.read_bytes()
           for p in n3.rglob("estado.json")}
igual(len(achados), 4, "os quatro estado.json chegaram (2 por empresa)")
igual(achados, esperado, "e todos byte a byte idênticos aos originais")
for rel_, dados in achados.items():
    v = json.loads(dados)
    chave = "ultNSU" if "nfe" in rel_ else "ultimoNSU"
    ok(v.get(chave) not in (None, 0), f"{rel_}: {chave}={v.get(chave)} preservado")

secao("Migração — TODO arquivo chega íntegro, não só os que eu lembrei de olhar")
# A conferência forte: comparar o conjunto inteiro por caminho relativo e
# conteúdo. É o que pega o arquivo que ninguém pensou em testar.
b4 = tmp("integral")
n4, l4 = b4 / "novo", b4 / "legado"
n4.mkdir(); l4.mkdir()
origem = {}
for i in range(40):
    sub = l4 / f"6{i:013d}" / ("nfe" if i % 2 else "xmls")
    sub.mkdir(parents=True)
    for j in range(3):
        f = sub / f"doc{j}.xml"
        conteudo = f"<doc empresa='{i}' n='{j}'/>".encode()
        f.write_bytes(conteudo)
        origem[str(f.relative_to(l4))] = conteudo
(l4 / "certificados.json").write_text("[]", "utf-8")
origem["certificados.json"] = (l4 / "certificados.json").read_bytes()
apontar(l4, n4)
fm.garantir(n4)
destino = {str(p.relative_to(n4)): p.read_bytes()
           for p in n4.rglob("*") if p.is_file()
           and p.name not in ("dados_versao.json", "migracao.log")}
faltando = set(origem) - set(destino)
corrompidos = [k for k in origem if k in destino and destino[k] != origem[k]]
igual(len(faltando), 0, f"nenhum dos {len(origem)} arquivos ficou para trás")
igual(len(corrompidos), 0, "e nenhum chegou diferente do que era")

secao("Migração — uma raiz customizada NÃO adota dados de fora")
# Defeito encontrado em teste: um servidor de ensaio, com FISCALE_DADOS
# apontando para uma pasta temporária, tentou trazer (e renomear) a pasta de
# dados de PRODUÇÃO para dentro dela. Quem aponta FISCALE_DADOS para uma pasta
# própria está criando outra instalação — e outra instalação não se serve do
# acervo da primeira.
b5 = tmp("custom")
n5, l5 = b5 / "minha_pasta", b5 / "producao"
n5.mkdir(); l5.mkdir()
(l5 / "certificados.json").write_text('[{"id":"9","cnpj":"9","nome":"PROD","caminho":"x"}]', "utf-8")
(l5 / "empresa_grande").mkdir()
(l5 / "empresa_grande" / "nota.xml").write_text("<producao/>", "utf-8")

os.environ["FISCALE_DADOS"] = str(n5)     # raiz CUSTOMIZADA
apontar(l5, n5, padrao=False)
ok(not fd.raiz_e_padrao(), "a raiz customizada é reconhecida como não-padrão")
r5 = fm.garantir(n5)
ok(not (n5 / "certificados.json").exists(),
   "a raiz customizada NÃO puxou o certificados.json da outra instalação")
ok(not (n5 / "empresa_grande").exists(), "nem as pastas de empresa")
ok((l5 / "certificados.json").exists(), "e a outra instalação continua intacta")
ok((l5 / "empresa_grande" / "nota.xml").exists(), "com os documentos dela")
ok(not (l5.parent / (l5.name + "-migrado-para-fiscale")).exists(),
   "e NÃO foi renomeada")
del os.environ["FISCALE_DADOS"]

secao("Migração — a raiz padrão, essa sim, adota o legado do NFS-e")
b6 = tmp("padrao")
l6 = b6 / "SistemaNFSe" / "dados"
l6.mkdir(parents=True)
(l6 / "certificados.json").write_text('[{"id":"7","cnpj":"7","nome":"X","caminho":"y"}]', "utf-8")
n6 = tmp("padrao_raiz")
apontar(l6, n6)                            # n6 faz o papel da raiz natural
ok(fd.raiz_e_padrao(), "com a raiz natural, a adoção é permitida")
fm.garantir(n6)
ok((n6 / "certificados.json").exists(), "e o legado do NFS-e é trazido")

secao("~/Fiscale/dados nunca é tratado como legado a consumir")
b7 = tmp("nunca")
n7 = b7 / "raiz"
n7.mkdir()
apontar(None, n7)
fd.LEGADO_FISCALE = Path.home() / "Fiscale" / "dados"   # existe e tem dados
igual(fm._legados_com_conteudo(n7), [],
      "mesmo existindo e com dados, ~/Fiscale/dados não entra na lista")

secao("Migração — segura de repetir")
apontar(None, novo)
antes = sorted(p.name for p in novo.rglob("*") if p.is_file())
rel2 = fm.garantir(novo)
ok(rel2["mudou"] is False, "rodar de novo não faz nada")
igual(sorted(p.name for p in novo.rglob("*") if p.is_file()), antes, "e não duplica arquivo")
virgem = tmp("virgem")
apontar(None, virgem)
ok(fm.garantir(virgem)["mudou"] is False, "instalação nova já nasce no formato atual")
igual(fm.versao(virgem), fm.VERSAO_ATUAL, "com a versão certa")

secao("Migração — a origem é preservada, não apagada")
marca = legado.parent / (legado.name + "-migrado-para-fiscale")
ok(marca.exists(), "o legado foi renomeado, não removido")
# O que foi MOVIDO (renomeado no mesmo disco) sai da origem por definição — é
# o que torna a migração rápida com 30 mil arquivos. O que foi FUNDIDO num
# destino que já existia é copiado, e a origem daquele fica intacta. Conferir
# a pasta fundida é o teste que prova que nada some sem contraparte.
ok((marca / "64567004000139" / "nfe" / "nota.xml").exists(),
   "o que foi fundido continua na origem renomeada, para conferência")
ok(not (novo / "64567004000139" / "nfe" / "nota.xml").exists()
   or (novo / "64567004000139" / "nfe" / "nota.xml").read_text("utf-8") == "<x/>",
   "e chegou íntegro no destino")

secao("Migração — não marca como consumido o que não conferiu")
b2 = tmp("parcial")
n2, l2 = b2 / "novo", b2 / "legado"
n2.mkdir(); l2.mkdir()
(n2 / "state_clientes.json").write_text("{}", "utf-8")
(l2 / "empresa" ).mkdir()
(l2 / "empresa" / "a.xml").write_text("aaa", "utf-8")
(n2 / "empresa").mkdir()
(n2 / "empresa" / "a.xml").write_text("DIFERENTE E MAIOR", "utf-8")   # colide com tamanho diferente
apontar(l2, n2)
r3 = fm.garantir(n2)
ok(l2.exists(), "com arquivo sem contraparte, a origem NÃO é renomeada")
ok(r3.get("nao_conferidos_qtd", 0) >= 1, "e a migração diz quais ficaram para trás")
igual((n2 / "empresa" / "a.xml").read_text("utf-8"), "DIFERENTE E MAIOR",
      "o arquivo do destino continua intocado")

# ══════════════════════════════════════════════════════════════════════════
secao("Estado da senha — 'aguardando' em vez de recadastrar a empresa")

raiz3 = fd.raiz(tmp("senha"))
sys.modules.pop("main", None)
os.environ["FISCALE_DADOS"] = str(raiz3)
fd._raiz_cache = None
fd.raiz(raiz3)
import main as api   # noqa: E402

def cert(**kw):
    base = {"id": "1", "cnpj": "1", "nome": "X", "caminho": ""}
    base.update(kw)
    return base

e, m = api.estado_senha(cert())
igual(e, "aguardando", "sem senha salva -> aguardando")
ok("Informe a senha" in m, "com uma frase que diz o que fazer")

e, m = api.estado_senha(cert(senha_protegida="dpapi:LIXO-DE-OUTRA-MAQUINA"))
igual(e, "aguardando", "senha que não abre nesta máquina -> aguardando")
ok("outro computador" in m, "e a explicação cita a troca de máquina")

protegida = seguranca.proteger("segredo123", raiz3)
e, m = api.estado_senha(cert(senha_protegida=protegida))
igual(e, "ok", "senha protegida NESTA máquina -> ok")
igual(m, "", "sem motivo pendente")
igual(seguranca.desproteger(protegida, raiz3), "segredo123",
      "e a proteção é ida e volta de verdade")

secao("Restauração — empresa continua cadastrada, só a senha falta")
itens = [
    {"id": "11111111000111", "cnpj": "11111111000111", "nome": "ACME LTDA",
     "apelido": "Acme", "caminho": "certs/ACME.pfx", "procurador": False},
    {"id": "22222222000122", "cnpj": "22222222000122", "nome": "BETA LTDA",
     "caminho": "certs/BETA.pfx", "senha_protegida": protegida},
]
(fd.pasta_certs() / "ACME.pfx").write_bytes(b"a")
(fd.pasta_certs() / "BETA.pfx").write_bytes(b"b")
api._salvar_registro(itens)

gravado = json.loads((raiz3 / "certificados.json").read_text("utf-8"))
igual(gravado[0]["caminho"], "certs/ACME.pfx", "no disco o caminho fica relativo")
lido = api._ler_registro()
ok(Path(lido[0]["caminho"]).is_absolute(), "na memória ele chega absoluto")
ok(Path(lido[0]["caminho"]).exists(), "e aponta para um arquivo que existe")

lista = api.listar_certificados()
igual(len(lista), 2, "as duas empresas continuam cadastradas")
igual(lista[0]["senha_estado"], "aguardando", "a sem senha aparece como aguardando")
igual(lista[1]["senha_estado"], "ok", "a com senha desta máquina, como ok")
igual(lista[0]["apelido"], "Acme", "o apelido sobrevive")
ok(all("senha_protegida" not in x for x in lista), "a senha protegida nunca é exposta pela API")

pend = api.certificados_pendentes()
igual(pend["total"], 2, "o resumo conta todas as empresas")
igual(pend["aguardando_senha"], 1, "e quantas aguardam senha")
igual(pend["pendentes"][0]["cnpj"], "11111111000111", "identificando qual")
ok(pend["pendentes"][0]["arquivo_presente"], "dizendo que o .pfx está disponível")

secao("Informar a senha não recadastra nada")
class _Req:
    def __init__(self, i, s):
        self.id, self.senha = i, s

antes_reg = api._ler_registro()
try:
    api.informar_senha(_Req("11111111000111", "qualquer"))
    ok(False, "senha errada deveria ser recusada")
except Exception as ex:
    ok(getattr(ex, "status_code", None) == 400, "senha errada é recusada com 400")
depois_reg = api._ler_registro()
igual(len(depois_reg), len(antes_reg), "e o cadastro não muda quando a senha é recusada")
igual(depois_reg[0]["nome"], "ACME LTDA", "a empresa continua com o nome dela")

try:
    api.informar_senha(_Req("99999999999999", "x"))
    ok(False, "id inexistente deveria dar 404")
except Exception as ex:
    ok(getattr(ex, "status_code", None) == 404, "id inexistente devolve 404")

secao("Nenhuma senha em texto claro é gravada")
bruto = (raiz3 / "certificados.json").read_text("utf-8")
ok("segredo123" not in bruto, "a senha não aparece no cadastro em disco")
ok(protegida.startswith("dpapi:") or protegida.startswith("fernet:"),
   "o que é gravado vem com o marcador do método de proteção")

# limpeza do ambiente
if antes_env is None:
    os.environ.pop("FISCALE_DADOS", None)
else:
    os.environ["FISCALE_DADOS"] = antes_env

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Todos os testes de portabilidade passaram.")
