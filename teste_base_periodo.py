#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Base do período (emissão × competência), início de atividade e Anexo/Fator R.

    python teste_base_periodo.py

POR QUE ESTE TESTE EXISTE
    A tela do Classificador listava as notas por DATA DE EMISSÃO e o quadro do
    DAS logo abaixo somava por COMPETÊNCIA. Nos meses em que os dois critérios
    não coincidem — nota emitida em setembro com competência de agosto — a
    lista mostrava um conjunto de notas e o DAS apurava outro. Quem conferia
    via dois números que não fechavam e não tinha como saber qual estava certo,
    porque os dois estavam: eram panoramas diferentes.

    Agora a base é UMA SÓ, escolhida na tela, e atravessa período, RBT12,
    Fator R e a apuração de cada mês. Este teste trava três coisas:

    1. os dois panoramas realmente produzem números diferentes quando as datas
       divergem (senão a opção seria decorativa);
    2. a base "competência" continua dando EXATAMENTE o que o sistema dava
       antes — é o número conferido contra guia de DAS real, e não pode mudar
       de carona numa mudança de filtro;
    3. o início de atividade sai do CADASTRO de clientes, não da tela.

    Cobre ainda a convivência de Anexo III fixado por lei com Anexo por Fator R
    na MESMA empresa: são notas de itens diferentes da LC 116, e cada uma
    resolve o seu.

    E trava a regra que decide, SOZINHA, se a empresa é de Fator R. Isso não é
    preferência de tela: quem responde é o item da LC 116 (LC 123, art. 18,
    §§5º-D e 5º-I). Antes o sistema devolvia "CONFIGURAR" e empurrava de volta
    ao contador uma pergunta que já estava respondida na lei.

O QUE ESTE TESTE **NÃO** FAZ
    Não fala com a Receita, não usa certificado e não lê a pasta de dados real.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        _erros.append(desc)
        print("  FALHOU  " + desc)


def igual(a, b, desc):
    ok(a == b, desc if a == b else "%s  (obtive %r, esperava %r)" % (desc, a, b))


CNPJ = "12345678000199"
NS = "http://www.sped.fazenda.gov.br/nfse"


def nfse(numero, chave, emissao, competencia, valor, ctrib="171901"):
    """Uma NFS-e nacional mínima, com emissão e competência independentes."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="{NS}">
 <infNFSe Id="NFS{chave}">
  <nNFSe>{numero}</nNFSe><cStat>100</cStat>
  <dhProc>{emissao}T10:00:00-03:00</dhProc>
  <xLocEmi>Recife</xLocEmi><cLocIncid>2611606</cLocIncid>
  <emit><CNPJ>{CNPJ}</CNPJ><xNome>EMPRESA DE TESTE LTDA</xNome>
   <enderNac><cMun>2611606</cMun><UF>PE</UF></enderNac></emit>
  <valores><vBC>{valor:.2f}</vBC><vISSQN>0.00</vISSQN><vLiq>{valor:.2f}</vLiq></valores>
  <DPS><infDPS>
   <dhEmi>{emissao}T10:00:00-03:00</dhEmi><serie>1</serie><nDPS>{numero}</nDPS>
   <dCompet>{competencia}</dCompet>
   <prest><regTrib><opSimpNac>3</opSimpNac><regApTribSN>1</regApTribSN>
     <regEspTrib>0</regEspTrib></regTrib></prest>
   <serv><cServ><cTribNac>{ctrib}</cTribNac><xDescServ>Servico de teste</xDescServ></cServ></serv>
   <valores><vServPrest><vServ>{valor:.2f}</vServ></vServPrest>
    <trib><tribMun><tribISSQN>1</tribISSQN><tpRetISSQN>1</tpRetISSQN></tribMun></trib></valores>
  </infDPS></DPS>
 </infNFSe>
</NFSe>
"""


def montar(pasta: Path, notas):
    xd = pasta / CNPJ / "xmls"
    xd.mkdir(parents=True, exist_ok=True)
    for i, (emissao, competencia, valor, ctrib) in enumerate(notas, 1):
        chave = f"26116062{CNPJ}{i:012d}26070000000{i:03d}"
        (xd / f"nfse-{chave}.xml").write_text(
            nfse(i, chave, emissao, competencia, valor, ctrib), "utf-8")
    return pasta / CNPJ


def main():
    import classificador as cls

    secao("A nota vai para o mês certo em cada base")
    n = {"data": "2026-09-03", "data_competencia": "2026-08-31",
         "competencia": "2026-08"}
    igual(cls.mes_base(n, "emissao"), "2026-09", "por emissão, setembro")
    igual(cls.mes_base(n, "competencia"), "2026-08", "por competência, agosto")
    igual(cls.data_base(n, "competencia"), "2026-08-31",
          "e a competência é a data cheia, não o dia 1")
    igual(cls.mes_base({"competencia": "2026-04"}, "competencia"), "2026-04",
          "só com o mês da competência, ainda cai no mês certo")
    igual(cls.mes_base({"data": "2026-04-09"}, "competencia"), "2026-04",
          "e sem competência nenhuma a nota NÃO some: cai pela emissão")

    secao("Histórico municipal continua pela regra antiga")
    # O histórico do Recife guarda a competência real em `competencia_xml`, mas
    # o campo `competencia` dele sempre foi o mês da EMISSÃO — e é sobre ele que
    # a anti-duplicidade e o RBT12 foram conferidos contra uma guia real.
    mun = {"origem": "NFS-e (Recife)", "data": "2026-06-08",
           "competencia": "2026-06", "competencia_xml": "2026-05-31"}
    igual(cls.mes_base(mun, "competencia"), "2026-06",
          "competencia_xml NÃO é usado para mudar a base do histórico")

    secao("Os dois panoramas dão números diferentes quando as datas divergem")
    with apoio.raiz_temporaria() as raiz:
        # 3 notas de agosto e 1 emitida em setembro com competência de agosto.
        pasta = montar(raiz, [
            # histórico: sem RBT12 o DAS sai zero e nada se compara
            ("2025-10-10", "2025-10-10", 30000.0, "171901"),
            ("2025-12-10", "2025-12-10", 30000.0, "171901"),
            ("2026-03-10", "2026-03-10", 30000.0, "171901"),
            ("2026-08-05", "2026-08-05", 1000.0, "171901"),
            ("2026-08-20", "2026-08-20", 1000.0, "171901"),
            ("2026-08-28", "2026-08-28", 1000.0, "171901"),
            ("2026-09-02", "2026-08-31", 5000.0, "171901"),
        ])
        cfg = {"regime": "simples", "anexoServico": "III"}
        ago = ("2026-08-01", "2026-08-31")

        emi = cls.classificar(pasta, CNPJ, dict(cfg), *ago, "emissao")
        cmp_ = cls.classificar(pasta, CNPJ, dict(cfg), *ago, "competencia")

        igual(emi["resumo"]["quantidade"], 3, "por emissão, agosto tem 3 notas")
        igual(cmp_["resumo"]["quantidade"], 4, "por competência, agosto tem 4")
        igual(emi["resumo"]["total"], 3000.0, "e R$ 3.000,00 de receita")
        igual(cmp_["resumo"]["total"], 8000.0, "contra R$ 8.000,00")

        def das_de(r, mes):
            return next((d for d in r["das"] if d["competencia"] == mes), None)

        d_emi, d_cmp = das_de(emi, "2026-08"), das_de(cmp_, "2026-08")
        ok(d_emi is not None and d_cmp is not None, "os dois apuram agosto")
        igual(d_emi["receita_mes"], 3000.0,
              "o DAS por emissão apura sobre as notas listadas por emissão")
        igual(d_cmp["receita_mes"], 8000.0,
              "e o DAS por competência, sobre as listadas por competência")
        ok(d_emi["das_estimado"] != d_cmp["das_estimado"],
           "logo o DAS dos dois panoramas é DIFERENTE — que é o ponto")
        igual(d_emi["base_data"], "emissao", "o quadro diz em que base foi apurado")
        igual(d_cmp["base_data"], "competencia", "nos dois casos")
        igual(cmp_["base_data"], "competencia", "e a classificação também informa")

    secao("Anexo III por lei e Anexo por Fator R convivem na mesma empresa")
    with apoio.raiz_temporaria() as raiz:
        # 8.02 (ensino) é Anexo III fixado em lei; 17.01 depende do Fator R.
        pasta = montar(raiz, [
            ("2026-08-10", "2026-08-10", 4000.0, "080201"),
            ("2026-08-11", "2026-08-11", 6000.0, "170101"),
        ])
        # Folha alta -> Fator R >= 28% -> a nota que depende dele vira Anexo III
        alta = cls.classificar(pasta, CNPJ, {"regime": "simples", "folha12m": 9000.0},
                               "2026-08-01", "2026-08-31", "competencia")
        anexos = {n["anexo"] for n in alta["notas"]}
        bases = {n["anexo"]: n["base_anexo"] for n in alta["notas"]}
        igual(anexos, {"III"}, "com Fator R alto, as duas ficam no Anexo III")
        ok("por lei" in bases["III"] or "Fator R" in bases["III"],
           "e a tela diz de onde veio o anexo de cada nota")

        # Folha baixa -> Fator R < 28% -> a mesma nota vai para o Anexo V,
        # mas a de ensino CONTINUA no III, porque nela a lei não dá escolha.
        baixa = cls.classificar(pasta, CNPJ, {"regime": "simples", "folha12m": 100.0},
                                "2026-08-01", "2026-08-31", "competencia")
        por_item = {n["item_lc116"]: n["anexo"] for n in baixa["notas"]}
        igual(por_item.get("8.02"), "III",
              "ensino (8.02) fica no Anexo III mesmo com Fator R baixo")
        igual(por_item.get("17.01"), "V",
              "e a nota que depende do Fator R cai para o Anexo V")
        ok("por lei" in {n["base_anexo"] for n in baixa["notas"]
                         if n["item_lc116"] == "8.02"}.pop(),
           "a justificativa do 8.02 é a lei, não o Fator R")

    secao("O sistema decide SOZINHO se a empresa é de Fator R")
    # A pergunta "é Fator R?" não é preferência de tela: quem responde é o item
    # da LC 116 (LC 123, art. 18, §§5º-D e 5º-I). Antes disto o classificador
    # devolvia "CONFIGURAR" e empurrava a pergunta de volta para o contador.
    ok(cls.sujeito_a_fator_r("4.01").startswith("§5º-I, I"),
       "medicina é Fator R por lei")
    igual(cls.sujeito_a_fator_r("10.09")[:10], "§5º-I, VII",
          "representação também")
    igual(cls.sujeito_a_fator_r("17.19"), "",
          "contabilidade NÃO: o §5º-B já fixa o Anexo III")
    igual(cls.sujeito_a_fator_r("17.14"), "",
          "advocacia NÃO: o §5º-C já fixa o Anexo IV")
    igual(cls.sujeito_a_fator_r("13.05"), "",
          "e item fora das listas continua sem resposta — não se inventa")
    ok(not (set(cls.LC116_FATOR_R) & (cls.LC116_ANEXO_III | cls.LC116_ANEXO_IV)),
       "nenhum item está em duas listas ao mesmo tempo")

    secao("Sem marcar nada na tela, a nota de medicina já sabe o que é")
    anexo, base = cls.resolver_anexo({"item_lc116": "4.01"}, {}, None)
    igual(anexo, "FATOR R", "sem a folha, fica pendente do NÚMERO")
    ok("por lei" in base and "medicina" in base,
       "mas a mensagem já afirma a sujeição e cita o inciso")
    ok("Folha 12m" in base, "e pede exatamente o que falta")

    igual(cls.resolver_anexo({"item_lc116": "4.01"}, {}, 0.35)[0], "III",
          "com folha de 35%, Anexo III")
    igual(cls.resolver_anexo({"item_lc116": "4.01"}, {}, 0.10)[0], "V",
          "com 10%, Anexo V")
    igual(cls.resolver_anexo({"item_lc116": "17.19"}, {}, 0.10)[0], "III",
          "e contabilidade segue no III mesmo com folha baixa")
    igual(cls.resolver_anexo({"item_lc116": "17.14"}, {}, 0.90)[0], "IV",
          "advocacia segue no IV mesmo com folha alta")

    secao("O anexo fixo do cadastro é ponte, não fuga")
    # A IMUNOCARE tem Anexo V escolhido à mão e emite 4.01 (medicina). Enquanto
    # não houver folha, a escolha dela vale — tirá-la apagaria um DAS que hoje
    # é calculado. Quando a folha chegar, a lei vence.
    anexo, base = cls.resolver_anexo({"item_lc116": "4.01"}, {"anexoServico": "V"}, None)
    igual(anexo, "V", "sem folha, a escolha do contador é mantida")
    ok("Aten" in base and "Fator R por lei" in base, "com o aviso da sujeição")
    igual(cls.resolver_anexo({"item_lc116": "4.01"}, {"anexoServico": "V"}, 0.35)[0],
          "III", "com folha de 35%, o Fator R vence o anexo fixo")

    # E quem só MARCOU a caixa, sem a lei mandar, segue como estava: a ponte
    # acima vale só para sujeição legal. Sem isso a EMPRESA J (caixa marcada,
    # Anexo III fixo, item 13.05) saltava de R$ 0 para R$ 43 mil de DAS no ano.
    igual(cls.resolver_anexo({"item_lc116": "13.05"},
                             {"fatorR": True, "anexoServico": "III"}, None)[0],
          "FATOR R",
          "caixa marcada sem folha continua pendente, como sempre foi")

    secao("A classificação diz o veredito, para a tela não precisar perguntar")
    with apoio.raiz_temporaria() as raiz:
        pasta = montar(raiz, [
            ("2026-08-10", "2026-08-10", 5000.0, "040100"),   # 4.01 medicina
            ("2026-08-11", "2026-08-11", 3000.0, "080201"),   # 8.02 ensino
        ])
        r = cls.classificar(pasta, CNPJ, {"regime": "simples"},
                            "2026-08-01", "2026-08-31", "competencia")
        ok(r["resumo"]["sujeita_fator_r"] is True,
           "o resumo afirma que a empresa é de Fator R")
        igual(sorted(r["resumo"]["fator_r_incisos"]), ["4.01"],
              "e diz QUAL atividade obriga — só a medicina, não o ensino")
        ok(any("é de Fator R" in a for a in r["resumo"]["alertas"]),
           "o alerta na tela afirma isso em português")

        semfr = cls.classificar(pasta, CNPJ, {"regime": "simples",
                                              "anexoServico": "III"},
                                "2026-08-01", "2026-08-31", "competencia")
        ok(semfr["resumo"]["sujeita_fator_r"] is True,
           "o veredito não depende de o contador ter marcado nada")

    secao("Início de atividade vem do CADASTRO de clientes")
    with apoio.raiz_temporaria() as raiz:
        (raiz / "certificados.json").write_text(json.dumps([{
            "id": CNPJ, "cnpj": CNPJ, "nome": "EMPRESA DE TESTE LTDA",
            "apelido": None, "caminho": "certs/nao-existe.pfx",
            "senha_protegida": None}]), "utf-8")
        (raiz / "state_clientes.json").write_text(json.dumps({"seq": 2, "clientes": [
            {"id": 1, "nome": "EMPRESA DE TESTE LTDA", "cnpj": "12.345.678/0001-99",
             "regime": "Simples Nacional", "inicioAtividade": "2026-03"}]}), "utf-8")

        import importlib
        import main as api
        importlib.reload(api)

        igual(api.inicio_atividade_da_empresa(CNPJ), "2026-03",
              "o cadastro responde pelo início de atividade")
        igual(api.inicio_atividade_da_empresa(CNPJ, {CNPJ: {"inicioAtividade": "2020-01"}}),
              "2026-03", "e VENCE a configuração antiga da tela do Classificador")
        igual(api.inicio_atividade_da_empresa("99999999999999",
                                              {"99999999999999": {"inicioAtividade": "2021-07"}}),
              "2021-07", "empresa fora do cadastro ainda cai na reserva da tela")
        igual(api.inicio_atividade_da_empresa("99999999999999"), None,
              "e sem nenhuma das duas, não inventa data")

    secao("Sem início de atividade o RBT12 dos 12 primeiros meses sai MENOR")
    # CGSN 140/2018, art. 21, §2º: proporcionalizar, não somar.
    notas = [{"data": "2026-03-10", "data_competencia": "2026-03-10",
              "competencia": "2026-03", "valor": 10000.0}]
    com = cls.rbt12_de(notas, "2026-04", None, "2026-03")["total"]
    sem = cls.rbt12_de(notas, "2026-04", None, None)["total"]
    igual(com, 120000.0, "com a data, o 1º mês é proporcionalizado (10.000 × 12)")
    igual(sem, 10000.0, "sem ela, soma só o que houver")
    ok(com > sem, "ou seja: sem a data no cadastro, a alíquota sai baixa")

    print("\n" + "=" * 62)
    print(f"{_ok} ok · {_falhas} falha(s)")
    for e in _erros:
        print("  - " + e)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
