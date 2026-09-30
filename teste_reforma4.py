#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REFORMA 4 — vigência fiscal por competência.

    python teste_reforma4.py

O QUE ESTA SUÍTE PROVA

    1. a regra escolhida é a que CONTÉM a competência — nunca "a mais nova";
    2. cadastrar uma regra futura NÃO muda a resposta de um mês passado;
    3. tributos coexistem: PIS e CBS, ISS e IBS, cada um com a sua vigência,
       sem nenhuma regra de substituição;
    4. o regime da empresa é histórico: mudar de regime não apaga o passado;
    5. o Simples é modelado à parte, com e sem opção pelo regime regular;
    6. regra sem alíquota definida devolve PENDENTE — nunca número inventado;
    7. ausência de regra não autoriza cálculo nenhum;
    8. **vigência sem início cadastrado não vale para competência nenhuma** —
       nem para 1500, nem para hoje, nem para 9999. "Não sei desde quando"
       não é "desde sempre", e os quatro jeitos de não responder
       (SEM_REGRA_VIGENTE, VIGENCIA_NAO_CADASTRADA, PENDENTE_DE_DEFINICAO e
       CONFLITO_DE_VIGENCIA) continuam distintos entre si;
    9. `regime.py` não calcula tributo, não importa motor de apuração e só
       depende da biblioteca padrão.

AS VIGÊNCIAS DE TESTE SÃO FICTÍCIAS, E É DE PROPÓSITO
    O motor é o que está sob teste, não a lei. As competências usadas nos
    casos de duas versões sucessivas saem de catálogos montados AQUI, com
    fonte fictícia declarada. O catálogo real do módulo é verificado à parte,
    e dele se exige justamente o contrário: que não haja data inventada.

NADA AQUI TOCA A PASTA REAL NEM A REDE.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import regime as rg                                  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                             # noqa: E402

_connect_original = _socket.socket.connect


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
        _erros.append(desc)
        print(f"  FALHOU  {desc}")


def igual(a, b, desc):
    ok(a == b, desc + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


# ══════════════════════════════════════════════════════════════════════════
# Catálogos de teste — fonte FICTÍCIA, declarada como tal.
# ══════════════════════════════════════════════════════════════════════════
FONTE_FALSA = "Norma fictícia de teste"


def regra(tributo, versao, inicio="", fim="", tratamento=rg.DEVIDO,
          status=rg.VIGENTE, aliquota=rg.NO_MOTOR, precedencia=0):
    return rg.Regra(tributo=tributo, versao=versao, tratamento=tratamento,
                    vigencia=rg.Vigencia(inicio=inicio, fim=fim),
                    fonte=FONTE_FALSA, ato="TESTE", status=status,
                    aliquota=aliquota, precedencia=precedencia)


# ══════════════════════════════════════════════════════════════════════════
secao("competência: aceita as três formas que o projeto usa")
igual(rg.competencia("2026-07"), "2026-07", "'AAAA-MM'")
igual(rg.competencia("2026-07-15"), "2026-07", "'AAAA-MM-DD' vira o mês")
igual(rg.competencia(date(2026, 7, 1)), "2026-07", "`date` vira o mês")
igual(rg.competencia(None), "", "None não vira competência")
igual(rg.competencia("2026-13"), "", "mês 13 é recusado, não corrigido")
igual(rg.competencia("lixo"), "", "texto ilegível é recusado")

secao("competência ANTERIOR à regra: sem regra vigente")
cat = (regra(rg.CBS, "V1", inicio="2027-01"),)
d = rg.qual_regra(rg.CBS, "2026-12", catalogo=cat)
igual(d.estado, rg.SEM_REGRA_VIGENTE, "estado SEM_REGRA_VIGENTE")
ok(not d.aplicavel, "não é aplicável")
igual(d.devido, None, "`devido` é None — nunca False por omissão")
igual(d.regra, None, "e nenhuma regra é devolvida")
ok(d.motivo, "com motivo declarado")

secao("competência NO INÍCIO da vigência: aplicável (limite inclusivo)")
d = rg.qual_regra(rg.CBS, "2027-01", catalogo=cat)
igual(d.estado, rg.APLICAVEL, "estado APLICAVEL")
igual(d.regra.versao, "V1", "a regra V1 respondeu")
igual(d.devido, True, "e ela é devida")

secao("competência POSTERIOR, vigência aberta: continua valendo")
d = rg.qual_regra(rg.CBS, "2035-06", catalogo=cat)
igual(d.estado, rg.APLICAVEL, "vigência aberta não expira sozinha")
ok(cat[0].vigencia.aberta, "e a vigência se declara aberta")

secao("vigência FECHADA: depois do fim, acabou")
cat_fim = (regra(rg.PIS, "PIS_V1", inicio="2020-01", fim="2026-12"),)
igual(rg.qual_regra(rg.PIS, "2026-12", catalogo=cat_fim).estado, rg.APLICAVEL,
      "o último mês ainda vale (fim inclusivo)")
igual(rg.qual_regra(rg.PIS, "2027-01", catalogo=cat_fim).estado,
      rg.SEM_REGRA_VIGENTE, "o mês seguinte já não vale")

secao("DUAS VERSÕES sucessivas da mesma regra")
duas = (regra(rg.PIS, "V1", inicio="2020-01", fim="2026-12"),
        regra(rg.PIS, "V2", inicio="2027-01"))
igual(rg.qual_regra(rg.PIS, "2024-05", catalogo=duas).regra.versao, "V1",
      "2024 responde V1")
igual(rg.qual_regra(rg.PIS, "2026-12", catalogo=duas).regra.versao, "V1",
      "o último mês da V1 ainda é V1")
igual(rg.qual_regra(rg.PIS, "2027-01", catalogo=duas).regra.versao, "V2",
      "o primeiro mês da V2 já é V2")
igual(rg.qual_regra(rg.PIS, "2019-12", catalogo=duas).estado,
      rg.SEM_REGRA_VIGENTE, "antes das duas, nenhuma")

secao("IDEMPOTÊNCIA: cadastrar regra futura NÃO muda o passado")
antes = rg.qual_regra(rg.PIS, "2024-05", catalogo=duas)
com_futura = duas + (regra(rg.PIS, "V3", inicio="2030-01"),)
depois = rg.qual_regra(rg.PIS, "2024-05", catalogo=com_futura)
igual(depois.regra.versao, antes.regra.versao,
      "a mesma competência responde a mesma versão")
igual(depois.estado, antes.estado, "e o mesmo estado")
igual(rg.qual_regra(rg.PIS, "2030-06", catalogo=com_futura).regra.versao, "V3",
      "enquanto a competência nova responde a regra nova")
# A prova dura: a regra mais NOVA nunca é escolhida por ser nova.
igual(rg.qual_regra(rg.PIS, "2027-06", catalogo=com_futura).regra.versao, "V2",
      "2027 responde V2, não V3 — recência não é critério")

secao("REGRA MAIS ESPECÍFICA vence a mais ampla")
mistura = (regra(rg.ISS, "AMPLA", inicio="2000-01"),
           regra(rg.ISS, "RECORTE", inicio="2026-01", fim="2026-06"))
igual(rg.qual_regra(rg.ISS, "2026-03", catalogo=mistura).regra.versao,
      "RECORTE", "dentro do recorte, vence o recorte")
igual(rg.qual_regra(rg.ISS, "2026-09", catalogo=mistura).regra.versao,
      "AMPLA", "fora dele, volta a ampla")

secao("EMPATE de especificidade é CONFLITO, não escolha silenciosa")
empate = (regra(rg.ISS, "A", inicio="2026-01", fim="2026-12"),
          regra(rg.ISS, "B", inicio="2026-01", fim="2026-12"))
d = rg.qual_regra(rg.ISS, "2026-05", catalogo=empate)
igual(d.estado, rg.CONFLITO_DE_VIGENCIA, "estado CONFLITO_DE_VIGENCIA")
ok(not d.aplicavel, "conflito não é aplicável")
igual(sorted(d.candidatas), ["A", "B"], "e as duas candidatas são nomeadas")
desempatado = (empate[0], rg.replace(empate[1], precedencia=10))
igual(rg.qual_regra(rg.ISS, "2026-05", catalogo=desempatado).regra.versao, "B",
      "precedência maior desempata")

secao("COEXISTÊNCIA: PIS e CBS devidos na MESMA competência")
coex = (regra(rg.PIS, "PIS_V1", inicio="2020-01"),
        regra(rg.CBS, "CBS_V1", inicio="2027-01"))
igual(rg.qual_regra(rg.PIS, "2027-06", catalogo=coex).estado, rg.APLICAVEL,
      "PIS aplicável em 2027-06")
igual(rg.qual_regra(rg.CBS, "2027-06", catalogo=coex).estado, rg.APLICAVEL,
      "CBS também aplicável na MESMA competência")
ok(rg.qual_regra(rg.PIS, "2027-06", catalogo=coex).devido
   and rg.qual_regra(rg.CBS, "2027-06", catalogo=coex).devido,
   "os dois devidos ao mesmo tempo — nenhum substitui o outro")

secao("COEXISTÊNCIA: ISS e IBS durante a transição")
trans = (regra(rg.ISS, "ISS_V1", inicio="2003-01", fim="2032-12"),
         regra(rg.IBS_MUNICIPAL, "IBS_MUN_V1", inicio="2027-01"))
igual(rg.qual_regra(rg.ISS, "2029-01", catalogo=trans).estado, rg.APLICAVEL,
      "ISS ainda vigente em 2029")
igual(rg.qual_regra(rg.IBS_MUNICIPAL, "2029-01", catalogo=trans).estado,
      rg.APLICAVEL, "IBS municipal também")
igual(rg.qual_regra(rg.ISS, "2033-01", catalogo=trans).estado,
      rg.SEM_REGRA_VIGENTE, "depois do fim do ISS, só o IBS")
igual(rg.qual_regra(rg.IBS_MUNICIPAL, "2033-01", catalogo=trans).estado,
      rg.APLICAVEL, "e o IBS segue")

secao("PANORAMA da competência mostra todos os tributos")
p = rg.tributos_da_competencia("2027-06", catalogo=coex)
igual(sorted(p), sorted(rg.TRIBUTOS), "responde pelos seis tributos")
igual(p[rg.PIS].estado, rg.APLICAVEL, "PIS aplicável")
igual(p[rg.ISS].estado, rg.SEM_REGRA_VIGENTE,
      "ISS sem regra NESTE catálogo de teste")

secao("REGRA SEM ALÍQUOTA DEFINIDA → pendente, nunca número")
pend = (regra(rg.CBS, "CBS_PEND", status=rg.PENDENTE_DE_DEFINICAO,
              tratamento=rg.PENDENTE_DE_DEFINICAO,
              aliquota=rg.PENDENTE_DE_DEFINICAO),)
d = rg.qual_regra(rg.CBS, "2027-06", catalogo=pend)
igual(d.estado, rg.PENDENTE_DE_DEFINICAO, "estado PENDENTE_DE_DEFINICAO")
ok(not d.aplicavel, "pendente NÃO é aplicável")
igual(d.devido, None, "`devido` é None — não autoriza cálculo")
igual(d.regra.aliquota, rg.PENDENTE_DE_DEFINICAO, "e a alíquota é o sentinela")
ok(not isinstance(d.regra.aliquota, (int, float)),
   "que nunca é número")

secao("AUSÊNCIA DE REGRA não gera cálculo")
vazio = rg.qual_regra(rg.CBS, "2027-06", catalogo=())
igual(vazio.estado, rg.SEM_REGRA_VIGENTE, "sem catálogo: SEM_REGRA_VIGENTE")
igual(vazio.devido, None, "`devido` None")
igual(vazio.regra, None, "sem regra")
igual(rg.qual_regra("TRIBUTO_QUE_NAO_EXISTE", "2027-06", catalogo=coex).motivo,
      "tributo não cadastrado", "tributo desconhecido se identifica como tal")

secao("REGIME DA EMPRESA: histórico, com mudança sem apagar o passado")
h = rg.HistoricoRegime(identidade="00000000000191")
h = h.com(rg.PeriodoRegime(regime=rg.SIMPLES, fonte="cadastro",
                           vigencia=rg.Vigencia(inicio="2024-01", fim="2026-06")))
h2 = h.com(rg.PeriodoRegime(regime=rg.PRESUMIDO, fonte="cadastro",
                            vigencia=rg.Vigencia(inicio="2026-07")))
igual(h2.em("2025-03").regime, rg.SIMPLES, "2025 era Simples")
igual(h2.em("2026-06").regime, rg.SIMPLES, "junho/2026 ainda Simples")
igual(h2.em("2026-07").regime, rg.PRESUMIDO, "julho/2026 já Presumido")
igual(h2.em("2023-12"), None, "antes do primeiro período: None, não um palpite")
igual(len(h.periodos), 1, "o histórico ANTERIOR não foi alterado")
igual(len(h2.periodos), 2, "e o novo tem os dois períodos")
igual(h.em("2026-07"), None,
      "o histórico antigo continua respondendo o que respondia")
igual(h2.conflitos(), [], "sem sobreposição")

secao("REGIME: sobreposição de mesma especificidade é acusada")
ruim = rg.HistoricoRegime(periodos=(
    rg.PeriodoRegime(regime=rg.SIMPLES, fonte="x",
                     vigencia=rg.Vigencia(inicio="2026-01", fim="2026-12")),
    rg.PeriodoRegime(regime=rg.REAL, fonte="x",
                     vigencia=rg.Vigencia(inicio="2026-06", fim="2027-06"))))
ok(ruim.conflitos(), "o conflito é apontado")

secao("SIMPLES sem opção pelo regime regular")
so_simples = rg.HistoricoRegime(periodos=(
    rg.PeriodoRegime(regime=rg.SIMPLES, fonte="cadastro",
                     tratamento_ibs_cbs=rg.IBS_CBS_DENTRO_DO_DAS,
                     vigencia=rg.Vigencia(inicio="2024-01")),))
p = so_simples.em("2027-03")
ok(p.e_simples, "a empresa é do Simples")
ok(not p.opcao_regular.vigente_em("2027-03"), "e não tem opção regular vigente")
igual(p.tratamento_ibs_cbs, rg.IBS_CBS_DENTRO_DO_DAS,
      "o tratamento declarado é DENTRO_DO_DAS")

secao("SIMPLES COM opção pelo regime regular, com vigência própria")
com_opcao = rg.HistoricoRegime(periodos=(
    rg.PeriodoRegime(
        regime=rg.SIMPLES, fonte="cadastro",
        tratamento_ibs_cbs=rg.IBS_CBS_DENTRO_DO_DAS,
        vigencia=rg.Vigencia(inicio="2024-01"),
        opcao_regular=rg.OpcaoRegimeRegular(
            vigencia=rg.Vigencia(inicio="2027-01", fim="2027-12"),
            fonte=FONTE_FALSA)),))
p = com_opcao.em("2027-03")
ok(p.opcao_regular.vigente_em("2027-03"), "opção vigente em março/2027")
ok(not p.opcao_regular.vigente_em("2026-12"), "não vigente antes")
ok(not p.opcao_regular.vigente_em("2028-01"), "nem depois do fim")
ok(p.regime == rg.SIMPLES,
   "e o regime principal continua SIMPLES — a opção não o troca")
igual(com_opcao.em("2026-12").regime, rg.SIMPLES,
      "o mesmo período responde antes da opção")

secao("TRATAMENTO de IBS/CBS combina regra geral e situação da empresa")
d = rg.tratamento_ibs_cbs(com_opcao, "2027-03")
igual(d.estado, rg.PENDENTE_DE_DEFINICAO,
      "hoje é pendente, porque a regra geral da CBS é pendente")
igual(d.devido, None, "e não autoriza cálculo")
sem_regime = rg.HistoricoRegime()
igual(rg.tratamento_ibs_cbs(sem_regime, "2027-03").estado,
      rg.SEM_REGRA_VIGENTE, "empresa sem regime cadastrado: sem regra")

secao("VIGÊNCIA NÃO CADASTRADA não é vigência universal")
# O defeito que esta seção tranca: a primeira versão desta camada tratava
# `inicio` vazio como "vale desde sempre", e `qual_regra('PIS', '1500-01')`
# devolvia APLICÁVEL com `devido=True`. Lacuna de cadastro virava afirmação.
sem_inicio = (regra(rg.PIS, "SEM_INICIO"),)
for comp, quando in (("1500-01", "muito antes de qualquer acervo"),
                     ("1998-01", "antes do período conhecido"),
                     ("2026-09", "competência atual"),
                     ("2099-12", "competência futura"),
                     ("9999-12", "o fim do calendário")):
    d = rg.qual_regra(rg.PIS, comp, catalogo=sem_inicio)
    igual(d.estado, rg.VIGENCIA_NAO_CADASTRADA,
          f"{comp} ({quando}): VIGENCIA_NAO_CADASTRADA")
    ok(not d.aplicavel, f"{comp}: não aplicável")
    igual(d.devido, None, f"{comp}: `devido` é None, nunca True")
d = rg.qual_regra(rg.PIS, "2026-09", catalogo=sem_inicio)
ok(d.regra is not None, "a regra é devolvida, para se saber QUAL falta datar")
ok("desde qual competência" in d.motivo, "e o motivo diz o que falta")
ok(d.estado in rg.NAO_DECIDEM, "o estado está na lista dos que não decidem")

secao("a distinção entre os quatro 'não' é preservada")
igual(rg.qual_regra(rg.PIS, "2026-09", catalogo=()).estado,
      rg.SEM_REGRA_VIGENTE, "sem regra nenhuma: SEM_REGRA_VIGENTE")
igual(rg.qual_regra(rg.PIS, "2019-01",
                    catalogo=(regra(rg.PIS, "V", inicio="2020-01"),)).estado,
      rg.SEM_REGRA_VIGENTE, "regra datada, competência fora: SEM_REGRA_VIGENTE")
igual(rg.qual_regra(rg.PIS, "2026-09", catalogo=sem_inicio).estado,
      rg.VIGENCIA_NAO_CADASTRADA, "regra sem início: VIGENCIA_NAO_CADASTRADA")
igual(rg.qual_regra(rg.CBS, "2026-09",
                    catalogo=(regra(rg.CBS, "P", status=rg.PENDENTE_DE_DEFINICAO,
                                    aliquota=rg.PENDENTE_DE_DEFINICAO),)).estado,
      rg.PENDENTE_DE_DEFINICAO, "regra pendente: PENDENTE_DE_DEFINICAO")
ok(len(set(rg.NAO_DECIDEM)) == 4, "são quatro estados distintos, não um só")

secao("vigência sem início não cobre o calendário nem em histórico de regime")
h_sem = rg.HistoricoRegime(periodos=(
    rg.PeriodoRegime(regime=rg.SIMPLES, fonte="cadastro"),))
igual(h_sem.em("2026-09"), None,
      "período de regime sem início não responde por competência nenhuma")
igual(h_sem.em("1500-01"), None, "nem por uma competência remota")

secao("uma regra FUTURA não torna válida uma competência anterior")
futura = (regra(rg.CBS, "FUT", inicio="2030-01"),)
for comp in ("2026-09", "2029-12"):
    d = rg.qual_regra(rg.CBS, comp, catalogo=futura)
    igual(d.estado, rg.SEM_REGRA_VIGENTE, f"{comp} continua sem regra")
    igual(d.devido, None, f"{comp} não vira devido por causa do futuro")
igual(rg.qual_regra(rg.CBS, "2030-01", catalogo=futura).estado, rg.APLICAVEL,
      "e só a partir do início declarado ela responde")

secao("CATÁLOGO REAL do módulo — íntegro e sem data inventada")
igual(rg.catalogo_valido(), [], "o catálogo real passa na conferência")
por_tributo = {}
for r in rg.CATALOGO:
    por_tributo.setdefault(r.tributo, []).append(r)
for t in (rg.PIS, rg.COFINS, rg.ISS):
    d = rg.qual_regra(t, "2026-09")
    igual(d.estado, rg.VIGENCIA_NAO_CADASTRADA,
          f"{t} existe, mas não diz desde quando vale")
    ok(not d.aplicavel, f"{t} NÃO é aplicável enquanto faltar o início")
    igual(d.devido, None, f"e o {t} não autoriza cálculo")
    igual(d.regra.aliquota, rg.NO_MOTOR,
          f"a alíquota do {t} continua no motor, não aqui")
for t in (rg.CBS, rg.IBS_ESTADUAL, rg.IBS_MUNICIPAL):
    d = rg.qual_regra(t, "2026-09")
    igual(d.estado, rg.PENDENTE_DE_DEFINICAO, f"{t} está pendente")
    ok(not d.aplicavel, f"{t} não é aplicável")
    igual(d.regra.aliquota, rg.PENDENTE_DE_DEFINICAO,
          f"e a alíquota do {t} não foi inventada")
ok(all(r.fonte and r.ato for r in rg.CATALOGO),
   "toda regra cadastrada tem fonte normativa")
ok(len(rg.PENDENCIAS) >= 5, "as pendências estão declaradas no módulo")

secao("NENHUMA DATA DA REFORMA FOI INVENTADA NO CATÁLOGO REAL")
datas = [r.vigencia.inicio or r.vigencia.fim for r in rg.CATALOGO]
ok(not any(datas), "nenhuma regra do catálogo real declara competência")
ok(all(r.status == rg.PENDENTE_DE_DEFINICAO
       for r in rg.CATALOGO if r.tributo in (rg.CBS, rg.IBS_ESTADUAL,
                                             rg.IBS_MUNICIPAL)),
   "os três tributos da Reforma estão marcados como pendentes")

secao("A CAMADA NÃO CALCULA E NÃO CONHECE OS MOTORES")
import ast                                            # noqa: E402
import inspect                                        # noqa: E402

fonte = inspect.getsource(rg)
arvore = ast.parse(fonte)
importados = set()
for no in ast.walk(arvore):
    if isinstance(no, ast.Import):
        importados |= {a.name.split(".")[0] for a in no.names}
    elif isinstance(no, ast.ImportFrom) and no.module:
        importados.add(no.module.split(".")[0])
igual(sorted(importados), ["__future__", "dataclasses", "datetime"],
      "importa só a biblioteca padrão")
for proibido in ("classificador", "apuracao_federal", "iss", "retencao",
                 "parsers", "indice", "consulta", "documento", "normalizacao"):
    ok(proibido not in importados, f"não importa `{proibido}`")

aritmetica = [n for n in ast.walk(arvore)
              if isinstance(n, ast.BinOp)
              and isinstance(n.op, (ast.Mult, ast.Div, ast.Sub, ast.Mod,
                                    ast.Pow, ast.FloorDiv))]
igual(len(aritmetica), 0, "nenhuma multiplicação, divisão ou subtração")
ok("Decimal" not in fonte, "não há Decimal: valor monetário não passa por aqui")
# A prova de que nenhuma soma é de dinheiro: não existe literal decimal no
# módulo inteiro. Sem percentual, sem alíquota, sem limite — nada com que
# calcular. (Os `int` que restam são precedência e largura de vigência.)
flutuantes = [n for n in ast.walk(arvore)
              if isinstance(n, ast.Constant) and isinstance(n.value, float)]
igual(len(flutuantes), 0,
      "nenhum literal decimal no módulo — nenhuma alíquota mora aqui")
for palavra in ("aliquota_efetiva", "calcular", "apurar", "def total"):
    ok(palavra not in fonte, f"não existe `{palavra}` nesta camada")

_socket.socket.connect = _connect_original

print("\n" + "=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  - " + e)
    sys.exit(1)
print("REFORMA 4: vigência por competência — sem inventar data nem calcular.")
