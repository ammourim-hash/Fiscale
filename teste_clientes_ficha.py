#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLIENTES · A FICHA — quem manda em cada dado do cadastro.

    python teste_clientes_ficha.py

O QUE ESTA SUÍTE PROVA

    1. o mapa de fontes cobre TODO campo do cadastro — nenhum fica sem dono;
    2. `ficha()` é função PURA: não abre arquivo e não vai à rede;
    3. campo DERIVADO não é editável no cadastro (IE e IM nascem no XML);
    4. divergência entre a cópia do cadastro e a origem é RELATADA, com as duas
       pontas e o nome de quem manda — e nunca resolvida em silêncio;
    5. a situação CADASTRAL do CNPJ não se confunde com a situação FISCAL;
    6. os vínculos apontam para os módulos donos de cada dado;
    7. a projeção para o ELO continua sendo lista branca: campo novo no cadastro
       NÃO vaza para o ELO sozinho;
    8. a rota é do operador e não entrega segredo.

O QUE ELA NÃO FAZ
    Não vai à rede, não usa dado real, não toca na pasta de produção.
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_cadastro as cad                         # noqa: E402
import fiscale_elo_sync as sync                        # noqa: E402
import fiscale_papeis as pp                            # noqa: E402

_ok = _falhas = 0

CNPJ = "11222333000181"


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        print("  FALHOU   " + desc)


def registro(**extra):
    base = {"id": 7, "cnpj": CNPJ, "nome": "ALFA LTDA",
            "razao_social": "ALFA COMERCIO LTDA", "ie": "1234567",
            "im": "987654", "regime": "Simples Nacional",
            "situacao": "Ativa", "municipio": "Recife", "uf": "PE",
            "email": "contato@alfa.com.br", "tel": "(81) 99999-0000",
            "cert": {"arquivo": "alfa.pfx"}, "certValidade": "2020-01-01"}
    base.update(extra)
    return base


# ══════════════════════════════════════════════════════════════════════════
secao("1 · Todo campo do cadastro tem dono declarado")
ok(set(cad.FONTE_DO_CAMPO) == set(cad.CAMPOS),
   "o mapa cobre exatamente os %d campos do cadastro" % len(cad.CAMPOS))
for campo, (dono, onde) in cad.FONTE_DO_CAMPO.items():
    ok(dono in (cad.MESTRE, cad.DERIVADO, cad.SUGERIDO),
       "%s tem dono válido (%s)" % (campo, dono))
    ok(bool(onde), "e diz onde o valor vive (%s)" % onde[:42])
ok(cad.FONTE_DO_CAMPO["regime"][0] == cad.MESTRE,
   "o REGIME é do cadastro — a base pública apenas sugere")
ok("sugere" in cad.FONTE_DO_CAMPO["regime"][1],
   "e isso está escrito na própria declaração")
for campo in ("ie", "im"):
    ok(cad.FONTE_DO_CAMPO[campo][0] == cad.DERIVADO,
       "%s é DERIVADO — nasce no XML" % campo)
ok("certificado" in cad.FORA_DO_CADASTRO
   and "regularidade" in cad.FORA_DO_CADASTRO,
   "certificado e regularidade estão declarados FORA do cadastro")
ok("NÃO é o campo" in cad.FORA_DO_CADASTRO["regularidade"][1],
   "dizendo que regularidade não é o campo `situacao`")


secao("2 · `ficha()` é pura")
# A medição é sobre CÓDIGO: a prosa do docstring fala "dos", "os" e afins, e
# procurar substring no texto inteiro acusaria português.
fonte = inspect.getsource(cad.ficha)
corpo = fonte[fonte.index('"""', fonte.index('"""') + 3) + 3:]
for proibido in ("open(", "read_text", "requests", "urlopen", "Path(",
                 "json.load", "os.path", "os.environ", "subprocess"):
    ok(proibido not in corpo, "`ficha` não usa `%s`" % proibido)


secao("3 · Campo derivado não é editável no cadastro")
f = cad.ficha(registro())
ok(f["campos"]["ie"]["editavel_no_cadastro"] is False,
   "IE não é editável no cadastro")
ok(f["campos"]["im"]["editavel_no_cadastro"] is False, "nem IM")
ok(f["campos"]["razao_social"]["editavel_no_cadastro"] is True,
   "mas a razão social é")
ok(f["campos"]["regime"]["editavel_no_cadastro"] is True,
   "e o regime também — ele é do cadastro")
ok(f["derivado"] == ["ie", "im"],
   "a lista de derivados é exatamente IE e IM (%s)" % f["derivado"])
ok("cnpj" in f["mestre"] and "regime" in f["mestre"],
   "e a de mestres traz identidade e regime")


secao("4 · Divergência é relatada, não resolvida")
f = cad.ficha(registro(),
              certificado={"arquivo": True, "validade": "2027-05-10"},
              inscricoes={"ie": "9999999", "im": "987654"})
assuntos = {d["assunto"] for d in f["divergencias"]}
ok("validade do certificado" in assuntos,
   "a cópia velha da validade do certificado aparece")
ok(cad.ROTULOS["ie"] in assuntos, "e a IE divergente também")
ok(cad.ROTULOS["im"] not in assuntos,
   "a IM igual não vira divergência (987654 == 987654)")
d = next(x for x in f["divergencias"] if x["assunto"] == "validade do certificado")
ok(d["no_cadastro"] == "2020-01-01" and d["na_origem"] == "2027-05-10",
   "com as duas pontas: %s × %s" % (d["no_cadastro"], d["na_origem"]))
ok("certificados.json" in d["fonte_que_manda"],
   "e dizendo quem manda: %s" % d["fonte_que_manda"][:40])
ok(bool(d["o_que_fazer"]), "e o que fazer")
# A ficha NÃO corrige o registro que recebeu.
reg = registro()
cad.ficha(reg, certificado={"arquivo": True, "validade": "2027-05-10"})
ok(reg["certValidade"] == "2020-01-01",
   "o registro de entrada não foi alterado pela ficha")
# Sem origem para comparar, não há divergência inventada.
limpa = cad.ficha(registro(), inscricoes=None)
ok(limpa["divergencias"] == [],
   "sem origem informada, nenhuma divergência é inventada")
ok(limpa["vinculos"]["ie"]["no_xml"] == "",
   "e a coluna do XML fica vazia, não preenchida com o cadastro")


secao("5 · Situação cadastral não é situação fiscal")
ok(cad.ROTULOS["situacao"] == "Situação Cadastral",
   "o rótulo do campo é «%s»" % cad.ROTULOS["situacao"])
ok("situacao cadastral" in cad._SINONIMOS,
   "e o sinônimo da planilha já existia — a importação não quebra")
ok(f["vinculos"]["regularidade"]["fonte"].startswith("pacote `situacao/`"),
   "a regularidade declara vir do pacote próprio")
ok("do CNPJ" in f["vinculos"]["regularidade"]["nao_confundir_com"],
   "e avisa para não confundir com o campo do CNPJ")


secao("6 · Vínculos apontam para o módulo dono")
esperado = {"certificado": "clientes.html", "regularidade": "situacao.html",
            "acervo": "nfe_documentos.html", "apuracao": "classificador.html"}
for chave, onde in esperado.items():
    ok(f["vinculos"][chave]["onde"] == onde,
       "%s → %s" % (chave, onde))
    ok(f["vinculos"][chave]["dono"] == cad.DERIVADO,
       "e é declarado como derivado")


secao("7 · A projeção do ELO continua fechada")
proj = sync.projetar(registro())
ok(set(proj) == {"externalId", "displayName", "document", "email", "phone",
                 "active"},
   "a projeção tem 6 campos e só (%s)" % sorted(proj))
for vazado in ("ie", "im", "regime", "cert", "certValidade", "situacao",
               "cnae_principal", "municipio"):
    ok(vazado not in proj, "`%s` NÃO vai para o ELO" % vazado)
# Campo novo no cadastro não entra sozinho: é o que a lista branca garante.
proj2 = sync.projetar(registro(campo_novo_qualquer="x", segredo="y"))
ok(set(proj2) == set(proj), "campo novo no cadastro não aparece na projeção")
ok(sync.versao(proj) == sync.versao(sync.projetar(registro())),
   "e a versão é estável para o mesmo cadastro")
FONTE_SYNC = (RAIZ / "fiscale_elo_sync.py").read_text("utf-8")
ok("Lista branca explícita" in FONTE_SYNC,
   "a regra está escrita no módulo do ELO")


secao("8 · A rota é do operador e não entrega segredo")
ok(pp.pode(pp.OPERADOR, "GET", "/api/clientes/ficha"),
   "o operador alcança a ficha")
SERV = (RAIZ / "fiscale_server.py").read_text("utf-8")
ok('"/api/clientes/ficha"' in SERV, "a rota existe")
ok('"/api/clientes/ficha",' in SERV, "e está nas exceções do proxy")
_i = SERV.index('if rota == "/api/clientes/ficha"')
BLOCO = SERV[_i:SERV.index('if rota == "/api/centro"', _i)]
for segredo in ("senha", "token", "frase", "DPAPI"):
    ok(segredo not in BLOCO, "a rota não menciona `%s`" % segredo)
ok('"arquivo": bool(' in BLOCO,
   "o certificado sai como BOOLEANO — é o que impede o caminho de vazar")

# A prova forte é sobre a RESPOSTA, não sobre o texto do código: mesmo recebendo
# um caminho de .pfx, a ficha não o devolve em lugar nenhum.
import json as _json                                   # noqa: E402
vaza = cad.ficha(registro(),
                 certificado={"arquivo": "C:/dados/certs/segredo.pfx",
                              "caminho": "C:/dados/certs/segredo.pfx",
                              "validade": "2027-01-01",
                              "senha": "nao-deve-sair"})
bruto = _json.dumps(vaza, ensure_ascii=False)
for pedaco in ("segredo.pfx", "C:/dados", "nao-deve-sair", "certs"):
    ok(pedaco not in bruto, "a resposta não traz `%s`" % pedaco)
ok(vaza["vinculos"]["certificado"]["tem"] is True,
   "mas ela diz que o certificado existe")
ok(vaza["vinculos"]["certificado"]["validade"] == "2027-01-01",
   "e até quando vale")
ok("inscricoes=None" in BLOCO,
   "e não passa o próprio cadastro como se fosse a origem da IE/IM")

TELA = (RAIZ / "web" / "clientes.html").read_text("utf-8")
ok("/api/clientes/ficha" in TELA, "a tela pede a ficha")
ok("/api/clientes/inscricoes" in TELA,
   "e pede as inscrições ao módulo, que é quem lê o XML")
ok("painelFicha" in TELA and "pintarFicha" in TELA, "com área própria")
ok("não edite aqui" in TELA, "marcando o campo derivado como não editável")
_ini = TELA.index("function pintarFicha")
_fim = TELA.index("function linhaVinc")
CORPO = TELA[_ini:_fim]
numeros = re.findall(r">\s*(\d{2,})\s*<", CORPO)
ok(not numeros, "nenhum número fixo no HTML da ficha (achei %s)" % numeros[:5])


print("\n%d ok · %d falha(s)" % (_ok, _falhas))
sys.exit(1 if _falhas else 0)
