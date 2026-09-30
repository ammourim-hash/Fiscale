#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes do módulo Plano de Saúde (HEALTH 1).

Só biblioteca padrão + o próprio módulo — mesmo estilo dos outros teste_*.py do
Fiscale, para rodar com um duplo clique sem instalar nada.

    python teste_saude.py

As amostras vivem em nfse/backend/saude/amostras/ e são SANITIZADAS: têm a
estrutura dos relatórios reais e pessoas inventadas.
"""
from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

# Pastas temporárias saem de `teste_apoio.pasta_temp()`, que as apaga no fim
# do processo. Antes eram `tempfile.mkdtemp()` soltos, e sobravam em %TEMP%.
import teste_apoio as _ta  # noqa: E402

from saude import armazenamento as arm            # noqa: E402
from saude import servico                          # noqa: E402
from saude.deteccao import detectar                # noqa: E402
from saude.modelo import Cobranca, cpf_valido, dec, normaliza_cpf  # noqa: E402
from saude.parsers import PARSERS                  # noqa: E402
from saude.texto import texto_de                   # noqa: E402

AMOSTRAS = RAIZ / "nfse" / "backend" / "saude" / "amostras"

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
    ok(a == b, f"{desc}" + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


def amostra(nome: str) -> bytes:
    return (AMOSTRAS / nome).read_bytes()


def importar(pasta, nome, **kw):
    return servico.importar_um(pasta, nome, amostra(nome), **kw)


# ══════════════════════════════════════════════════════════════════════════
secao("Decimal — dinheiro nunca em float")
igual(dec("R$ 1.234,56"), Decimal("1234.56"), "lê o formato brasileiro com R$")
igual(dec("904,72"), Decimal("904.72"), "vírgula decimal")
igual(dec("1234.56"), Decimal("1234.56"), "ponto decimal (formato de máquina)")
igual(dec(""), Decimal("0.00"), "campo vazio vira zero, não exceção")
igual(dec("xis"), Decimal("0.00"), "texto que não é número vira zero")
igual(dec("-50,00"), Decimal("-50.00"), "negativo")
ok(isinstance(dec("10,00"), Decimal), "o tipo é Decimal")
igual(dec("0,1") + dec("0,2"), Decimal("0.30"),
      "0,1 + 0,2 dá exatamente 0,30 (é o que float erraria)")
igual(sum((dec("904,72") for _ in range(3)), Decimal("0.00")), Decimal("2714.16"),
      "três parcelas iguais somam sem sobra de centavo")

secao("Cobrança — cada natureza no seu campo, total derivado")
c = Cobranca(mensalidade=dec("1000,00"), coparticipacao=dec("150,00"),
             acertos=dec("-20,00"), outros=dec("5,00"), iof=dec("10,00"))
igual(c.total, Decimal("1145.00"), "total = mensalidade + copart + acertos + outros + IOF")
ok(not hasattr(Cobranca(), "valor"), "não existe uma coluna genérica 'valor'")

secao("CPF")
ok(cpf_valido("11144477735"), "CPF válido passa")
ok(not cpf_valido("11111111111"), "CPF com todos os dígitos iguais é recusado")
ok(not cpf_valido("12345678900"), "CPF com DV errado é recusado")
igual(normaliza_cpf("1234567890"), "01234567890",
      "completa os zeros à esquerda que a SulAmérica corta")
ok(cpf_valido(normaliza_cpf("1234567890")), "e o CPF completado passa no DV")

# ══════════════════════════════════════════════════════════════════════════
secao("Detecção de operadora pelo conteúdo")
d = detectar(texto_de(amostra("sulamerica_familia.txt")))
ok(not d.precisa_confirmar, "reconhece a SulAmérica sozinho")
igual(d.parser.nome, "SulAmérica", "e acerta a operadora")
d = detectar(texto_de(amostra("bradesco_familia.txt")))
ok(not d.precisa_confirmar, "reconhece o Bradesco sozinho")
igual(d.parser.nome, "Bradesco Saúde", "e acerta a operadora")
d = detectar(texto_de(amostra("nao_reconhecido.txt")))
ok(d.precisa_confirmar, "documento estranho: pede confirmação em vez de chutar")
ok(d.parser is None, "e não escolhe operadora nenhuma")
ok(bool(d.motivo), "explica o motivo para o usuário")

# ══════════════════════════════════════════════════════════════════════════
secao("SulAmérica — titular, dependentes, totais e IOF")
p = Path(_ta.pasta_temp(prefix="saude_sa_"))
r = importar(p, "sulamerica_familia.txt")
e = r["extrato"]
igual(r["resultado"], "novo", "importa")
igual(e["operadora"], "SulAmérica", "operadora detectada")
igual(e["competencia"], "07/2026", "competência = mês em que o período começa")
igual(e["periodo_cobertura"], "16/07/2026 a 15/08/2026", "guarda o período inteiro")
igual(e["empresa_nome"], "EMPRESA DE TESTE FISCALE LTDA", "razão social")
igual(e["apolice"], "100000001", "apólice")
igual(e["titulares"], 1, "1 titular")
igual(e["dependentes"], 2, "2 dependentes")
igual(e["vidas"], 3, "3 vidas")
igual(e["total_individualizado"], "2714.16", "soma das vidas")
igual(e["total_tecnico_declarado"], "2714.16", "total declarado no RESUMO PRÊMIO")
igual(e["iof_declarado"], "64.60", "IOF")
igual(e["acertos_declarados"], "0.00", "acertos")
igual(e["total_fatura_declarado"], "2778.76", "total geral da fatura")
igual(e["status"], "CONFERIDO", "fecha")
ok(e["conferencia"]["fecha"], "conferência sem divergência relevante")
ok("Total + Acertos + IOF" in e["conferencia"]["regra"],
   "o parser declara a SUA regra de conferência")

fam = e["familias"][0]
igual(fam["titular"]["parentesco"], "TITULAR", "o titular é o TITULAR do relatório")
igual(fam["titular"]["nome"], "ANTONIO DE SOUZA EXEMPLO", "nome do titular")
igual(len(fam["dependentes"]), 2, "dois dependentes na família")
igual(fam["dependentes"][0]["parentesco"], "CONJUGE", "parentesco cônjuge preservado")
igual(fam["dependentes"][1]["parentesco"], "FILHO", "parentesco filho normalizado")
igual(fam["dependentes"][1]["parentesco_original"], "FILHOS",
      "e o texto original do relatório é preservado")
igual(fam["total_declarado"], "2714.16", "total da família declarado")
igual(fam["total_calculado"], "2714.16", "total da família calculado")
igual(fam["titular"]["cpf"], "11144477735", "CPF do titular")
ok(fam["titular"]["cpf_ok"], "CPF do titular passa no DV")
igual(fam["dependentes"][1]["cpf"], "01234567890",
      "CPF do filho veio com 10 dígitos e foi completado")
ok(any("zeros à esquerda" in a for a in fam["dependentes"][1]["avisos"]),
   "e o ajuste do CPF fica registrado como aviso")
igual(fam["titular"]["chave"], "cpf:11144477735", "a chave técnica sai do CPF")

secao("SulAmérica — várias famílias e acertos")
r = importar(p, "sulamerica_duas_familias.txt")
e = r["extrato"]
igual(len(e["familias"]), 2, "separa as duas famílias")
igual(e["titulares"], 2, "2 titulares")
igual(e["dependentes"], 3, "3 dependentes")
igual(e["familias"][1]["titular"]["nome"], "DANIELA ROCHA EXEMPLO", "titular da 2ª família")
igual(e["familias"][1]["total_calculado"], "2000.00", "total da 2ª família")
igual(e["total_individualizado"], "4714.16", "soma geral das vidas")
igual(e["acertos_declarados"], "150.00", "acertos entram")
igual(e["total_fatura_declarado"], "4976.16", "total = prêmios + acertos + IOF")
igual(e["status"], "CONFERIDO", "fecha mesmo com acertos")

secao("SulAmérica — CPF ausente e CPF inválido")
r = importar(p, "sulamerica_sem_cpf.txt")
e = r["extrato"]
fam = e["familias"][0]
ok(not fam["titular"]["cpf_ok"], "CPF 111.111.111-11 é recusado no DV")
ok(any("dígito verificador" in a for a in fam["titular"]["avisos"]),
   "e o motivo fica registrado")
ok(not fam["titular"]["chave"].startswith("cpf:"),
   "com CPF inválido a chave NÃO usa o CPF como identidade confiável")
ok(fam["dependentes"][0]["chave"].startswith("cod:")
   or fam["dependentes"][0]["chave"].startswith("cpf?:"),
   "sem CPF utilizável, a identidade cai no código da operadora")
igual(e["status"], "CONFERIDO", "CPF ruim não impede a conferência financeira")

secao("Divergência — o relatório declara mais do que as vidas somam")
r = importar(p, "sulamerica_divergente.txt")
e = r["extrato"]
igual(e["status"], "CONFERIR", "status vira CONFERIR")
ok(not e["conferencia"]["fecha"], "a conferência não fecha")
div = [x for x in e["conferencia"]["divergencias"] if x["relevante"]]
ok(len(div) >= 1, "aponta pelo menos uma divergência relevante")
d0 = div[0]
igual(d0["declarado"], "2714.16", "mostra o VALOR DO RELATÓRIO")
igual(d0["identificado"], "1809.44", "mostra o VALOR IDENTIFICADO")
igual(d0["diferenca"], "904.72", "mostra a DIFERENÇA")

# ══════════════════════════════════════════════════════════════════════════
secao("Bradesco — titular sem coluna de parentesco, 4 dependentes")
pb = Path(_ta.pasta_temp(prefix="saude_br_"))
r = importar(pb, "bradesco_familia.txt")
e = r["extrato"]
igual(e["operadora"], "Bradesco Saúde", "operadora detectada")
igual(e["competencia"], "07/2026", "competência da subfatura")
igual(e["empresa_cnpj"], "11.222.333/0001-81",
      "CNPJ do ESTIPULANTE, não o da seguradora nem o do banco")
igual(e["vencimento"], "29/07/2026", "vencimento do boleto (não o início da cobertura)")
igual(e["titulares"], 1, "1 titular")
igual(e["dependentes"], 4, "4 dependentes")
igual(e["total_individualizado"], "12970.52", "soma das 5 vidas")
igual(e["total_tecnico_declarado"], "12970.52", "TOTAIS DA SUBFATURA")
igual(e["iof_declarado"], "308.69", "IOF")
igual(e["total_fatura_declarado"], "13279.21", "valor cobrado")
igual(e["status"], "CONFERIDO", "fecha: técnico + IOF = cobrado")
igual(e["titulares_declarados"], 1, "lê os titulares declarados no resumo")
igual(e["dependentes_declarados"], 4, "lê os dependentes declarados no resumo")

fam = e["familias"][0]
igual(fam["titular"]["nome"], "HELENA MARTINS EXEMPLO",
      "titular = a vida SEM parentesco na coluna (evidência do relatório)")
igual(fam["titular"]["codigo"], "0000019/00", "certificado do titular")
igual(len(fam["dependentes"]), 4, "quatro dependentes na mesma família")
igual(fam["dependentes"][3]["parentesco"], "CONJUGE",
      "cônjuge é dependente mesmo vindo por último")
igual(fam["dependentes"][0]["parentesco"], "FILHO", "FILH vira FILHO")
igual(fam["titular"]["plano"], "TNEE", "plano lido, sem confundir com estado civil")
igual(fam["titular"]["cpf"], "", "a fatura técnica do Bradesco não traz CPF")
igual(fam["titular"]["chave"], "cod:0000019/00",
      "sem CPF, a identidade sai do certificado — nunca do nome")
ok(not e["avisos"], "sem avisos: parentesco e sufixo /00 concordam")

secao("Bradesco — coparticipação em campo próprio")
r = importar(pb, "bradesco_copart.txt")
e = r["extrato"]
fam = e["familias"][0]
igual(fam["titular"]["cobranca"]["mensalidade"], "1000.00", "mensalidade separada")
igual(fam["titular"]["cobranca"]["coparticipacao"], "150.00", "coparticipação separada")
igual(fam["titular"]["cobranca"]["total"], "1150.00", "total da vida soma as duas")
igual(e["total_individualizado"], "1900.00", "soma das vidas com coparticipação")
ok(any("coparticipação" in a for a in e["avisos"]),
   "quando a diferença é exatamente a coparticipação, o sistema DIZ isso "
   "em vez de deixar o contador caçando erro")

# ══════════════════════════════════════════════════════════════════════════
secao("Arquivo original preservado")
e = importar(pb, "bradesco_familia.txt")["extrato"]
orig = Path(e["arquivo_caminho"])
ok(orig.exists(), "o arquivo original fica guardado")
igual(orig.read_bytes(), amostra("bradesco_familia.txt"),
      "byte a byte, exatamente como veio")
ok(len(e["arquivo_hash"]) == 64, "com hash SHA-256 do conteúdo")
ok(e["parser_versao"], "e a versão do parser que produziu a leitura")

secao("Duplicidade — importar o mesmo arquivo duas vezes")
pd = Path(_ta.pasta_temp(prefix="saude_dup_"))
r1 = importar(pd, "sulamerica_familia.txt")
r2 = importar(pd, "sulamerica_familia.txt")
igual(r1["resultado"], "novo", "a primeira importação entra")
igual(r2["resultado"], "duplicado", "a segunda é recusada como duplicada")
igual(len(arm.carregar(pd)["extratos"]), 1, "e existe UM único extrato gravado")
comps = servico.listar_competencias(pd)
igual(len(comps), 1, "uma competência")
igual(comps[0]["total"], "2778.76", "com o valor lançado UMA vez, não em dobro")

secao("Reprocessamento — relê o original, sem apagar o resultado anterior")
extrato_id = arm.carregar(pd)["extratos"][0]["id"]
rr = servico.reprocessar(pd, extrato_id)
ok(rr["ok"], "reprocessa a partir do arquivo original")
igual(rr["resultado"], "reprocessado", "e marca como reprocessado")
novo = arm.achar(arm.carregar(pd), extrato_id)
igual(len(arm.carregar(pd)["extratos"]), 1, "continua sendo um extrato só")
ok(len(novo.get("historico") or []) == 1, "a leitura anterior foi para o histórico")
igual(novo["historico"][0]["parser_versao"], novo["parser_versao"],
      "com o parserVersao que a produziu")
igual(novo["total_individualizado"], "2714.16", "e o resultado continua o mesmo")

secao("Importação em lote — um arquivo ruim não derruba os outros")
pl = Path(_ta.pasta_temp(prefix="saude_lote_"))
lote = [(n, amostra(n)) for n in ("sulamerica_familia.txt",
                                  "nao_reconhecido.txt",
                                  "bradesco_familia.txt",
                                  "sulamerica_duas_familias.txt")]
res = servico.importar_arquivos(pl, lote)
igual(res["resumo"]["total"], 4, "processa os quatro")
igual(res["resumo"]["novo"], 3, "três entram")
igual(res["resumo"]["confirmar"], 1, "e o não reconhecido pede confirmação")
ok(res["itens"][2]["extrato"]["operadora"] == "Bradesco Saúde",
   "o arquivo DEPOIS do problemático foi importado normalmente")
igual(len(servico.listar_competencias(pl)), 3, "três competências × operadora")

secao("Parser errado — forçar a operadora que não é a do arquivo")
pe = Path(_ta.pasta_temp(prefix="saude_err_"))
r = importar(pe, "sulamerica_familia.txt", operadora="Bradesco Saúde")
igual(r["resultado"], "erro", "não finge que leu")
ok("não encontrei nenhuma vida" in r["extrato"]["erro"],
   "e explica o que houve, em português")
igual(r["extrato"]["status"], "ERRO", "fica registrado com status ERRO")
ok(Path(r["extrato"]["arquivo_caminho"]).exists(),
   "mas o original continua guardado, para reprocessar depois")

r = importar(pe, "sulamerica_familia.txt", operadora="Operadora Que Não Existe")
igual(r["resultado"], "erro", "operadora sem parser dá erro claro")

secao("CNPJ da empresa — importar na empresa errada é avisado")
pc = Path(_ta.pasta_temp(prefix="saude_cnpj_"))
r = importar(pc, "bradesco_familia.txt", cnpj_empresa="99888777000166")
e = r["extrato"]
igual(e["status"], "CONFERIR", "o extrato é rebaixado para CONFERIR")
ok(any("importado na empresa" in a for a in e["avisos"]),
   "com aviso explicando que o relatório é de outro CNPJ")
r = importar(pc, "sulamerica_familia.txt", cnpj_empresa="11222333000181")
igual(r["extrato"]["empresa_cnpj"], "11222333000181",
      "quando o relatório não traz CNPJ, usa o da empresa da pasta")

secao("Histórico por competência")
ph = Path(_ta.pasta_temp(prefix="saude_hist_"))
servico.importar_arquivos(ph, [(n, amostra(n)) for n in
                               ("sulamerica_familia.txt", "sulamerica_duas_familias.txt")])
linha = servico.historico_beneficiario(ph, "cpf:11144477735")
igual(len(linha), 2, "a mesma pessoa aparece nas duas competências")
igual([x["competencia"] for x in linha], ["07/2026", "08/2026"],
      "em ordem cronológica (e não alfabética)")
igual(linha[0]["valor"], "904.72", "com o valor de cada mês")
novo_dep = servico.historico_beneficiario(ph, "cpf:45678912364")
igual(len(novo_dep), 1, "dependente novo aparece só a partir do mês em que entrou")
igual(novo_dep[0]["titular"], "DANIELA ROCHA EXEMPLO", "vinculado ao titular certo")

bs = servico.beneficiarios(ph)
igual(len(bs), 5, "cinco vidas distintas nas duas competências")
ok(all(b["chave"] for b in bs), "toda vida tem chave técnica")
antonio = next(b for b in bs if b["nome"] == "ANTONIO DE SOUZA EXEMPLO")
igual(antonio["ultima_competencia"], "08/2026", "a lista mostra a posição mais recente")

secao("Nome não é chave técnica")
from saude.modelo import Beneficiario                              # noqa: E402
b1 = Beneficiario(nome="JOSE DA SILVA", cpf="11144477735", cpf_ok=True, codigo="A1")
b2 = Beneficiario(nome="JOSE DA SILVA", cpf="52998224725", cpf_ok=True, codigo="B2")
ok(b1.chave_tecnica() != b2.chave_tecnica(),
   "dois homônimos com CPF diferente são pessoas diferentes")
b3 = Beneficiario(nome="JOSE DA SILVA JUNIOR", cpf="11144477735", cpf_ok=True)
igual(b3.chave_tecnica(), b1.chave_tecnica(),
      "e o mesmo CPF com o nome grafado diferente é a mesma pessoa")

secao("Privacidade — só o necessário para a conferência")
campos = set(Beneficiario().__dataclass_fields__)
proibidos = {"diagnostico", "cid", "procedimento", "guia", "carencia",
             "internacao", "doenca", "sexo", "estado_civil"}
ok(not (campos & proibidos),
   "nenhum campo clínico ou irrelevante à cobrança no modelo")

# ══════════════════════════════════════════════════════════════════════════
secao("Nada de REINF nesta fase")
import saude as _saude                                              # noqa: E402
fontes = "\n".join((Path(_saude.__file__).parent / f).read_text("utf-8")
                   for f in ("modelo.py", "servico.py", "armazenamento.py",
                             "deteccao.py", "texto.py"))
for proibido in ("evtRet", "R-4010", "R-4020", "envioLoteEventos", "xmldsig"):
    ok(proibido not in fontes, f"o módulo não menciona {proibido}")

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Todos os testes passaram.")
