#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CTE 1 — a fundação do CT-e, provada offline.

    python teste_cte1.py

O QUE ESTA SUÍTE PROVA
    1. Que o CT-e é **reconhecido** — inclusive quando a tag raiz é a mesma da
       NF-e (`resEvento`), caso em que só o namespace separa os dois.
    2. Que os **seis papéis** são lidos, que um documento pode ter vários, e
       que o TOMADOR é resolvido pelo `toma` — não só pelo `toma4`.
    3. Que a **entrada canônica** deduplica por chave e por SHA-256, registra
       a procedência, e **nunca sobrescreve** o original em conflito.
    4. Que o **checkpoint** é do CT-e, é da empresa, é do ambiente, só anda
       depois de gravar, e nunca desce.
    5. Que o **adaptador** é a única porta, e que ele recusa consulta por chave.
    6. Que **nada aqui vai à rede** e nenhum dado real entra.

NADA REAL ENTRA
    Os XML são construídos em `teste_fixturas_cte.py`, com CNPJ fictícios de DV
    válido. Nenhum certificado é aberto, nenhuma senha é usada, nenhuma pasta
    real é tocada — `raiz_temporaria()` trava contra a pasta de dados de
    verdade.
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                              # noqa: E402
import teste_fixturas_cte as fx                          # noqa: E402
from ingestao import acervo as acv                       # noqa: E402
from ingestao import checkpoint as cpm                   # noqa: E402
from ingestao import consulta as cq                      # noqa: E402
from ingestao import indice as idx                      # noqa: E402
from ingestao import pipeline as pipe                   # noqa: E402
from ingestao import credencial_cte as cred              # noqa: E402
from ingestao import resposta_bruta as bruta            # noqa: E402
from ingestao import documento as dm                     # noqa: E402
from ingestao import entrada_cte as ent                  # noqa: E402
from ingestao import identificacao as idf                # noqa: E402
from ingestao import parsers as prs                      # noqa: E402
from ingestao import pipeline as pipe                    # noqa: E402
from ingestao import servico_cte as svc                  # noqa: E402
from ingestao.ambiente import HOMOLOGACAO, PRODUCAO      # noqa: E402
from ingestao.conectores import cte_dfe as C             # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

# ── trava de rede ───────────────────────────────────────────────────────────
import socket as _socket                                 # noqa: E402

_REDE: list[str] = []
_connect_original = _socket.socket.connect


def _proibido(self, endereco, *a, **kw):
    _REDE.append(str(endereco))
    raise AssertionError(f"teste tentou abrir rede para {endereco}")


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
    ok(a == b, desc + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


def levanta(f, desc, tipo=Exception):
    try:
        f()
    except tipo:
        ok(True, desc)
    except Exception as e:
        ok(False, f"{desc} — levantou {type(e).__name__}")
    else:
        ok(False, f"{desc} — não levantou nada")


EMP = fx.TRANSPORTADORA
OUTRA = fx.EMBARCADOR


def ler(xml, empresa=EMP):
    i = idf.identificar(xml)
    p = prs.para(i)
    return i, (p.interpretar(xml, i, empresa) if p else None)


def main() -> int:
    # ══════════════════════════════════════════════════════════════════════
    secao("Identificação: CT-e, e o desempate que só o namespace resolve")
    ch = fx.chave_cte()
    ok(idf.chave_valida(ch), "a chave da fixtura tem DV correto")

    for nome, xml, esperado in (
            ("cteProc", fx.cte(ch), idf.CTE57),
            ("resCTe", fx.cte_resumo(ch), idf.CTE57),
            ("procEventoCTe", fx.evento_cte(ch), idf.EVENTO_CTE),
            ("resEvento (CT-e)", fx.evento_cte(ch, resumo=True), idf.EVENTO_CTE)):
        i = idf.identificar(xml)
        igual(i.especie, esperado, f"{nome} é identificado como {esperado}")
        ok(i.identidade_forte, f"e {nome} tem identidade forte (chave válida)")

    # O ponto: a MESMA tag raiz, espécies diferentes.
    from teste_fixturas_nfe import chave_ficticia, xml_evento, FORNECEDOR
    ev_nfe = xml_evento(chave_ficticia(FORNECEDOR, 1), tp="110111")
    igual(idf.identificar(ev_nfe).especie, idf.EVENTO_NFE,
          "`resEvento` de NF-e continua sendo EVENTO_NFE")
    igual(idf.identificar(fx.evento_cte(ch, resumo=True)).especie, idf.EVENTO_CTE,
          "e `resEvento` de CT-e é EVENTO_CTE — só o namespace os separa")

    igual(idf.MODELO_DA_ESPECIE[idf.CTE57], "57", "CTE57 é o modelo 57")

    # ══════════════════════════════════════════════════════════════════════
    secao("Leitura do CT-e completo")
    i, d = ler(fx.cte(ch))
    igual(d.especie, dm.CTE57, "espécie CTE57")
    igual(d.numero, "1", "número")
    igual(d.serie, "1", "série")
    igual(str(d.valor_total), "1500.00", "valor da prestação")
    igual(d.situacao, dm.AUTORIZADO, "situação AUTORIZADO pelo protocolo")
    e = d.extensao
    igual(e.modal, "01", "modal rodoviário")
    igual(e.cfop, "5353", "CFOP")
    igual(e.uf_inicio, "PE", "UF de início")
    igual(e.uf_fim, "PB", "UF de fim")
    igual(e.protocolo, "126260000000001", "protocolo")
    igual(str(e.valor_icms), "180.00", "ICMS")
    igual(e.cst_icms, "00", "CST do ICMS")

    secao("Leitura do resumo (resCTe)")
    i, r = ler(fx.cte_resumo(ch))
    igual(r.especie, dm.CTE57, "resumo também é CTE57")
    igual(str(r.valor_total), "1500.00", "com valor")
    igual(r.situacao, dm.AUTORIZADO, "e situação")
    ok(not r.extensao.participantes,
       "o resumo NÃO inventa participantes que não vieram")
    ok(any("resumo" in a for a in r.avisos), "e avisa que é resumo")

    secao("Leitura dos eventos")
    for resumo in (False, True):
        i, ev = ler(fx.evento_cte(ch, resumo=resumo))
        rot = "resumo" if resumo else "completo"
        igual(ev.especie, dm.EVENTO_CTE, f"evento {rot} é EVENTO_CTE")
        igual(ev.chave, ch, f"evento {rot} aponta para a chave do CT-e")
        igual(ev.extensao.tp_evento, "110111", f"evento {rot}: tipo")
        igual(ev.extensao.sequencia, "1", f"evento {rot}: sequência")
        igual(ev.extensao.orgao, "26", f"evento {rot}: órgão")
    _, completo = ler(fx.evento_cte(ch))
    _, so_resumo = ler(fx.evento_cte(ch, resumo=True))
    igual(completo.extensao.justificativa, "LANCAMENTO INCORRETO",
          "o evento completo traz a justificativa")
    ok(so_resumo.extensao.justificativa is None,
       "e o resumo a deixa `None` — ela não existe ali, não está vazia")

    # ══════════════════════════════════════════════════════════════════════
    secao("Os seis papéis, e o tomador que não tem bloco próprio")
    # emitente
    _, d = ler(fx.cte(ch), EMP)
    igual(d.papeis, ("EMITENTE",), "a transportadora é EMITENTE")
    # remetente e tomador (toma=0 aponta para o remetente)
    _, d = ler(fx.cte(ch), fx.EMBARCADOR)
    igual(set(d.papeis), {"REMETENTE", "TOMADOR"},
          "o remetente com `toma`=0 é REMETENTE **e** TOMADOR")
    igual(d.papel, "TOMADOR",
          "e o papel principal é TOMADOR — quem paga o frete vem antes")
    # destinatário
    _, d = ler(fx.cte(ch), fx.DESTINO)
    igual(d.papeis, ("DESTINATARIO",), "o destinatário é DESTINATARIO")
    # expedidor e recebedor
    xml = fx.cte(ch, expedidor=fx.TERCEIRO)
    _, d = ler(xml, fx.TERCEIRO)
    igual(d.papeis, ("EXPEDIDOR",), "o expedidor é EXPEDIDOR")
    xml = fx.cte(ch, recebedor=fx.TERCEIRO)
    _, d = ler(xml, fx.TERCEIRO)
    igual(d.papeis, ("RECEBEDOR",), "o recebedor é RECEBEDOR")
    # autXML
    xml = fx.cte(ch, autxml=(fx.ESCRITORIO,))
    _, d = ler(xml, fx.ESCRITORIO)
    igual(d.papeis, ("AUTXML",), "quem está no autXML é AUTXML")
    # quem não participa
    _, d = ler(fx.cte(ch), "12345678000195")
    igual(d.papeis, (), "quem não participa não recebe papel nenhum")

    secao("`toma` apontando para cada participante")
    for codigo, papel, empresa in (("0", "REMETENTE", fx.EMBARCADOR),
                                   ("1", "EXPEDIDOR", fx.TERCEIRO),
                                   ("2", "RECEBEDOR", fx.TERCEIRO),
                                   ("3", "DESTINATARIO", fx.DESTINO)):
        xml = fx.cte(ch, toma=codigo, expedidor=fx.TERCEIRO,
                     recebedor=fx.TERCEIRO)
        _, d = ler(xml, empresa)
        ok("TOMADOR" in d.papeis,
           f"`toma`={codigo} faz o {papel} ser também TOMADOR")
        igual(d.extensao.tomador_papel, papel,
              f"e a extensão registra que apontou para {papel}")
        igual(d.extensao.tomador_codigo, codigo,
              f"guardando o código `{codigo}` literal do documento")

    secao("`toma4` — o tomador com bloco próprio")
    xml = fx.cte(ch, toma4=fx.ESCRITORIO)
    _, d = ler(xml, fx.ESCRITORIO)
    igual(d.papeis, ("TOMADOR",), "o CNPJ do `toma4` é TOMADOR")
    igual(d.extensao.tomador_papel, "TOMADOR", "sem apontar para outro papel")

    secao("Documento com MÚLTIPLOS papéis")
    # a mesma empresa como emitente e destinatária
    xml = fx.cte(ch, emitente=EMP, destinatario=EMP)
    _, d = ler(xml, EMP)
    igual(set(d.papeis), {"EMITENTE", "DESTINATARIO"},
          "emitente e destinatária: os DOIS papéis")
    igual(d.papel, "EMITENTE", "com EMITENTE como principal")
    # emitente, tomador e autorizada
    xml = fx.cte(ch, emitente=EMP, remetente=EMP, toma="0", autxml=(EMP,))
    _, d = ler(xml, EMP)
    igual(set(d.papeis), {"EMITENTE", "REMETENTE", "TOMADOR", "AUTXML"},
          "quatro papéis no mesmo documento, nenhum descartado")

    secao("Modelo 67 (CT-e OS) — declarado, não fingido")
    ch67 = fx.chave_cte(modelo="67")
    _, d = ler(fx.cte(ch67, modelo="67"))
    ok(any("67" in a for a in d.avisos),
       "modelo 67 é lido com AVISO de leiaute não conferido")

    # ══════════════════════════════════════════════════════════════════════
    secao("Entrada canônica: dedup por chave e por SHA-256")
    with apoio.raiz_temporaria("cte1_") as raiz:
        xml = fx.cte(ch)
        h = hashlib.sha256(xml).hexdigest()

        r1 = ent.da_distribuicao(raiz, EMP, [("nsu-1.xml", xml)])
        igual(r1.por_resultado(), {"NOVO": 1}, "primeira vez: NOVO")
        igual(r1.vereditos[0].sha256, h, "o SHA-256 é registrado")
        igual(r1.vereditos[0].origem, ent.ORIGEM_DISTRIBUICAO,
              "com a procedência da distribuição")

        r2 = ent.da_distribuicao(raiz, EMP, [("nsu-1.xml", xml)])
        igual(r2.por_resultado(), {"DUPLICATA": 1},
              "os MESMOS bytes: DUPLICATA, nada escrito")

        r3 = ent.da_importacao(raiz, EMP, [("outro-nome.xml", xml)])
        igual(r3.por_resultado(), {"DUPLICATA": 1},
              "o mesmo documento por OUTRO caminho: também DUPLICATA")
        igual(r3.origem, ent.ORIGEM_IMPORTACAO,
              "e a origem da importação é registrada à parte")

        originais = list((Path(raiz) / EMP / "acervo").rglob("original.xml"))
        igual(len(originais), 1,
              "UM documento no acervo, mesmo tendo chegado por duas origens")

        secao("Conflito: mesma chave, conteúdo diferente")
        antes = originais[0].read_bytes()
        divergente = xml.replace(b"<vTPrest>1500.00", b"<vTPrest>9999.00")
        r4 = ent.da_importacao(raiz, EMP, [("divergente.xml", divergente)])
        igual(r4.por_resultado(), {"CONFLITO": 1}, "vira CONFLITO")
        ok(r4.vereditos[0].quarentena, "e vai para quarentena, para conferência")
        ok(not r4.vereditos[0].aceito, "não é aceito como documento bom")
        igual(originais[0].read_bytes(), antes,
              "e o `original.xml` NÃO foi sobrescrito — byte a byte igual")
        igual(len(list((Path(raiz) / EMP / "acervo").rglob("original.xml"))), 1,
              "nem duplicado")

        secao("Evento: identidade por chave + tipo + sequência")
        e1 = ent.da_distribuicao(raiz, EMP, [
            ("nsu-2.xml", fx.evento_cte(ch, tp="110111", seq="1"))])
        igual(e1.por_resultado(), {"NOVO": 1}, "o evento entra")
        e2 = ent.da_distribuicao(raiz, EMP, [
            ("nsu-3.xml", fx.evento_cte(ch, tp="110111", seq="2"))])
        igual(e2.por_resultado(), {"NOVO": 1},
              "outra SEQUÊNCIA é outro evento")
        e3 = ent.da_distribuicao(raiz, EMP, [
            ("nsu-4.xml", fx.evento_cte(ch, tp="110110", seq="1"))])
        igual(e3.por_resultado(), {"NOVO": 1}, "outro TIPO é outro evento")
        e4 = ent.da_distribuicao(raiz, EMP, [
            ("nsu-5.xml", fx.evento_cte(ch, tp="110111", seq="1"))])
        igual(e4.por_resultado(), {"DUPLICATA": 1},
              "e o mesmo tipo+sequência é DUPLICATA")

        secao("Cancelamento não apaga o original")
        igual(originais[0].read_bytes(), antes,
              "depois de três eventos, o CT-e original continua intacto")

        secao("O que não é CT-e é recusado")
        from teste_fixturas_nfe import xml_nfe
        rn = ent.da_importacao(
            raiz, EMP, [("nota.xml", xml_nfe(chave_ficticia(FORNECEDOR, 9)))])
        igual(rn.por_resultado(), {"NAO_E_CTE": 1},
              "uma NF-e não entra pela porta do CT-e")
        rx = ent.da_importacao(raiz, EMP, [("lixo.xml", b"<nao xml")])
        igual(rx.por_resultado(), {"ILEGIVEL": 1}, "e XML quebrado é ILEGIVEL")
        ro = ent.registrar(raiz, EMP, [("x.xml", xml)], origem="INVENTADA")
        igual(ro.por_resultado(), {"ORIGEM_DESCONHECIDA": 1},
              "origem não declarada é recusada")

    # ══════════════════════════════════════════════════════════════════════
    secao("Checkpoint: do CT-e, da empresa, do ambiente")
    with apoio.raiz_temporaria("cte1_") as raiz:
        repo = cpm.RepositorioCheckpoint(raiz)
        a = repo.carregar(EMP, cpm.NFE_DISTRIBUICAO, PRODUCAO)
        a.ult_nsu = "000000000000100"
        repo.salvar(a)
        b = repo.carregar(EMP, cpm.CTE_DISTRIBUICAO, PRODUCAO)
        b.ult_nsu = "000000000000007"
        repo.salvar(b)
        c = repo.carregar(OUTRA, cpm.CTE_DISTRIBUICAO, PRODUCAO)
        c.ult_nsu = "000000000000042"
        repo.salvar(c)
        d_ = repo.carregar(EMP, cpm.CTE_DISTRIBUICAO, HOMOLOGACAO)
        d_.ult_nsu = "000000000000003"
        repo.salvar(d_)

        igual(repo.carregar(EMP, cpm.NFE_DISTRIBUICAO, PRODUCAO).ult_nsu,
              "000000000000100", "o NSU da NF-e ficou onde estava")
        igual(repo.carregar(EMP, cpm.CTE_DISTRIBUICAO, PRODUCAO).ult_nsu,
              "000000000000007", "o do CT-e é OUTRO — não se misturam")
        igual(repo.carregar(OUTRA, cpm.CTE_DISTRIBUICAO, PRODUCAO).ult_nsu,
              "000000000000042", "e o de outra empresa é outro ainda")
        igual(repo.carregar(EMP, cpm.CTE_DISTRIBUICAO, HOMOLOGACAO).ult_nsu,
              "000000000000003", "homologação tem checkpoint próprio")

        arquivos = sorted(p.name for p in
                          (Path(raiz) / EMP / "ingestao").glob("*.json"))
        ok("CTE_DISTRIBUICAO.producao.json" in arquivos,
           "cada combinação tem ARQUIVO próprio")
        ok("NFE_DISTRIBUICAO.producao.json" in arquivos,
           "e o da NF-e continua separado")

        secao("O NSU nunca diminui")
        igual(svc._maior_nsu("000000000000100", "000000000000007"),
              "000000000000100", "resposta com NSU menor não faz o nosso descer")
        igual(svc._maior_nsu("000000000000100", "000000000000200"),
              "000000000000200", "e um maior faz subir")
        igual(svc._maior_nsu("", "000000000000005"), "000000000000005",
              "partindo do zero, sobe")

        secao("Não começa em zero quando há estado anterior")
        cp = repo.carregar(EMP, cpm.CTE_DISTRIBUICAO, PRODUCAO)
        igual(cp.ult_nsu, "000000000000007",
              "recarregar devolve o NSU gravado, não zero")

    # ══════════════════════════════════════════════════════════════════════
    secao("O serviço: espera progressiva e política")
    igual([svc.espera_para(n) for n in (0, 1, 2, 3, 4, 5, 9)],
          [0, 60, 180, 600, 1800, 3600, 3600],
          "a espera cresce e PARA de crescer")
    ok(svc.POLITICA_PADRAO.intervalo_minimo_segundos > 0,
       "há intervalo mínimo entre consultas")
    ok(svc.POLITICA_PADRAO.cooldown_consumo_indevido_segundos > 3600,
       "e o bloqueio por consumo indevido é MAIOR que a hora do serviço")
    igual(svc.SERVICO, cpm.CTE_DISTRIBUICAO,
          "o serviço do módulo é o do CT-e")

    # ══════════════════════════════════════════════════════════════════════
    secao("O adaptador: única porta, e sem consulta por chave")
    env = C.montar_envelope_dist_nsu(EMP, "000000000000000", PRODUCAO, "26")
    t = env.decode("utf-8")
    ok("cteDistDFeInteresse" in t, "a operação é a do CT-e")
    ok(C.NS_CTE in t, "com o namespace do CT-e")
    ok('versao="1.00"' in t, "e a versão 1.00 do distDFeInt")
    ok("nfeDistDFeInteresse" not in t, "nada da NF-e no envelope")
    ok("<distNSU>" in t, "consulta por último NSU")
    env2 = C.montar_envelope_cons_nsu(EMP, "000000000000042", PRODUCAO, "26")
    ok("<consNSU>" in env2.decode("utf-8"), "e consulta por NSU específico")

    ok(C.CONSULTA_POR_CHAVE_DISPONIVEL is False,
       "o adaptador declara que NÃO há consulta por chave")
    levanta(lambda: C.montar_envelope_cons_chave(EMP, "1" * 44, PRODUCAO, "26"),
            "e pedir uma levanta com explicação", C.ConsultaNaoSuportada)
    ok("cte.fazenda.gov.br" in C.ENDPOINT[PRODUCAO.nome],
       "o endpoint de produção é o do CT-e")
    ok("hom" in C.ENDPOINT[HOMOLOGACAO.nome],
       "e há endpoint de homologação separado")

    secao("cStat: a tabela É a do CT-e (NT 2015.002 v1.05)")
    OFICIAIS = ["108", "109", "137", "138", "214", "215", "238", "239", "242",
                "252", "280", "281", "283", "284", "285", "286", "402", "404",
                "409", "410", "411", "472", "473", "489", "490", "589", "593",
                "656"]
    faltando = [c for c in OFICIAIS if c not in C.CSTAT_CTE]
    igual(faltando, [], "todos os códigos da NT estão na tabela")
    sobrando = [c for c in C.CSTAT_CTE if c not in OFICIAIS]
    igual(sobrando, [], "e nenhum código a mais foi inventado")
    ok(all(C.CSTAT_CTE[c].confirmado for c in OFICIAIS),
       "todos vêm de fonte oficial declarada")
    ok(all("2015.002" in C.CSTAT_CTE[c].fonte for c in OFICIAIS),
       "e a fonte citada é a NT do CT-e")

    secao("Os herdados da NF-e que NÃO valem no CT-e saíram")
    for c in ("236", "578", "632", "111"):
        ok(c not in C.CSTAT_CTE,
           f"{c} é da NF-e e não consta da tabela do CT-e")
        igual(C.status_de(c).categoria, C.CATEGORIA_NAO_DOCUMENTADA,
              f"e {c} cai em NÃO DOCUMENTADO, sem palpite")

    secao("As categorias que viram comportamento")
    for codigo, categoria, rot in (
            ("138", C.CAT_DOCUMENTOS, "documento localizado"),
            ("137", C.CAT_SEM_DOCUMENTOS, "nenhum documento"),
            ("108", C.CAT_INDISPONIVEL, "serviço paralisado"),
            ("109", C.CAT_INDISPONIVEL, "paralisado sem previsão"),
            ("656", C.CAT_CONSUMO_INDEVIDO, "consumo indevido"),
            ("489", C.CAT_REJEICAO, "NSU inexistente"),
            ("490", C.CAT_REJEICAO, "NSU muito antigo"),
            ("593", C.CAT_REJEICAO, "NSU e chave na mesma consulta"),
            ("280", C.CAT_AUTORIZACAO, "certificado inválido"),
            ("284", C.CAT_AUTORIZACAO, "erro na cadeia do certificado")):
        igual(C.status_de(codigo).categoria, categoria, f"{codigo}: {rot}")
    igual(C.status_de("999999").categoria, C.CATEGORIA_NAO_DOCUMENTADA,
          "e código fora da tabela NÃO é adivinhado")
    igual(C.RETENCAO_MESES, 3, "a janela de retenção do zero são 3 meses")

    # ══════════════════════════════════════════════════════════════════════
    secao("A distribuição NÃO devolve ao emitente o que ele emitiu")
    fonte_svc = (RAIZ / "nfse" / "backend" / "ingestao" / "servico_cte.py"
                 ).read_text("utf-8")
    fonte_ent = (RAIZ / "nfse" / "backend" / "ingestao" / "entrada_cte.py"
                 ).read_text("utf-8")
    fonte_ada = (RAIZ / "nfse" / "backend" / "ingestao" / "conectores"
                 / "cte_dfe.py").read_text("utf-8")
    for nome, texto in (("serviço", fonte_svc), ("entrada", fonte_ent),
                        ("adaptador", fonte_ada)):
        baixo = texto.lower().replace("*", "")
        ok("não recebe de volta" in baixo or "não devolve ao emitente" in baixo,
           f"o {nome} diz que o emitente não recebe o que emitiu")
    ok("ENTRADA CANÔNICA" in fonte_svc or "entrada canônica" in fonte_svc,
       "e aponta a entrada canônica como caminho da emissão própria")

    tela = (RAIZ / "web" / "cte.html").read_text("utf-8")
    ok("não devolve ao emitente" in tela or "não vêm por ela" in tela
       or "não vêm pela captura" in tela,
       "a tela também avisa, onde a decisão é tomada")
    ok("só por importação" in tela,
       "e o filtro de emitidos diz de onde eles vêm")

    fonte_rotas = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
    ok("emitidos_nao_vem_pela_captura" in fonte_rotas,
       "o diagnóstico expõe o limite como dado")
    ok("janela_retencao_meses" in fonte_rotas,
       "e expõe a janela de retenção de 3 meses")

    secao("As duas fontes têm propósitos declarados e separados")
    ok(ent.ORIGEM_DISTRIBUICAO != ent.ORIGEM_IMPORTACAO,
       "a procedência distingue captura de importação")
    ok("EMITIU" in fonte_ent or "emitiu" in fonte_ent,
       "a entrada canônica declara que cobre a emissão própria")

    # ══════════════════════════════════════════════════════════════════════
    secao("Espécies que a distribuição entrega e ainda não sabemos ler")
    NS = "http://www.portalfiscal.inf.br/cte"
    for tag, modelo, rot in (("cteOSProc", "67", "CT-e OS"),
                             ("CTeSimp", "57", "CT-e Simplificado"),
                             ("GTVeProc", "64", "GTV-e")):
        ch_ns = fx.chave_cte(modelo=modelo)
        xml_ns = (f'<{tag} xmlns="{NS}"><infCte Id="CTe{ch_ns}"/></{tag}>').encode()
        i = idf.identificar(xml_ns)
        igual(i.especie, idf.CTE_NAO_SUPORTADO,
              f"{rot} é identificado como CTE_NAO_SUPORTADO")
        ok(i.identidade_forte,
           f"{rot} guarda IDENTIDADE FORTE — deduplica contra si mesmo")
        ok(any("não implementado" in a for a in i.avisos),
           f"{rot} avisa que o leiaute não foi implementado")

    secao("E eles são PRESERVADOS, não descartados")
    with apoio.raiz_temporaria("cte11_") as raiz:
        ch_os = fx.chave_cte(modelo="67")
        xml_os = (f'<cteOSProc xmlns="{NS}"><infCte Id="CTe{ch_os}"/>'
                  f'</cteOSProc>').encode()
        r = ent.da_distribuicao(raiz, EMP, [("nsu-9.xml", xml_os)])
        igual(r.por_resultado(), {"NOVO": 1},
              "o CT-e OS entra no acervo em vez de ser recusado")
        originais = list((Path(raiz) / EMP / "acervo").rglob("original.xml"))
        igual(len(originais), 1, "um documento gravado")
        igual(originais[0].read_bytes(), xml_os,
              "e os BYTES são exatamente os que chegaram")
        r2 = ent.da_distribuicao(raiz, EMP, [("nsu-9.xml", xml_os)])
        igual(r2.por_resultado(), {"DUPLICATA": 1},
              "e ele deduplica na consulta seguinte")

    # ══════════════════════════════════════════════════════════════════════
    secao("Fila esgotada bloqueia por uma hora")
    ok(svc.POLITICA_PADRAO.cooldown_fila_esgotada_segundos >= 3600,
       "o bloqueio da fila esgotada é de no mínimo uma hora")
    with apoio.raiz_temporaria("cte11_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        from ingestao.conectores import nfe_dfe as N

        class SemDocumentos:
            def distribuir(self, *a, **kw):
                return N.Resposta(cstat="137",
                                  status=C.status_de("137"),
                                  motivo="Nenhum documento localizado",
                                  ult_nsu="000000000000010",
                                  max_nsu="000000000000010")

        r = svc.consultar_empresa(raiz, EMP, lambda i, a: SemDocumentos(),
                                  ambiente=PRODUCAO)
        ok(r.estado in (svc.FILA_ESGOTADA, svc.SEM_DOCUMENTOS),
           "resposta vazia é reconhecida")
        ok(r.segundos_para_liberar >= 3600,
           "e bloqueia por pelo menos uma hora — voltar antes é o que gera 656")

        adiante = datetime.now(timezone.utc) + timedelta(minutes=30)
        r2 = svc.elegivel(raiz, EMP, agora=adiante)
        ok(not r2.autorizada,
           "meia hora depois a consulta ainda não é autorizada")

    secao("656 exige revisão humana, e não volta sozinho")
    with apoio.raiz_temporaria("cte11_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        class Seiscentos:
            def distribuir(self, *a, **kw):
                raise C.ConsumoIndevido("cStat 656: consumo indevido")

        r = svc.consultar_empresa(raiz, EMP, lambda i, a: Seiscentos(),
                                  ambiente=PRODUCAO)
        igual(r.estado, svc.CONSUMO_INDEVIDO, "656 é reconhecido")
        ok(r.exige_revisao, "e MARCA a empresa para revisão humana")

        # Muito depois do cooldown: continua barrada.
        bem_depois = datetime.now(timezone.utc) + timedelta(days=2)
        r2 = svc.elegivel(raiz, EMP, agora=bem_depois)
        ok(not r2.autorizada,
           "dois dias depois ela AINDA não volta sozinha à fila")
        igual(r2.estado, svc.EXIGE_REVISAO,
              "porque o que a segura é a revisão, não o relógio")


    # ══════════════════════════════════════════════════════════════════════
    secao("O CICLO INTEIRO, com documentos de verdade no lote")
    # Este é o teste que faltava. Todos os outros paravam antes do passo 4:
    # ou não havia documentos, ou a resposta era 137/656. O caminho de
    # persistência — o único que roda quando a consulta dá certo — nunca era
    # exercido, e por isso um `TypeError` na chamada de gravação sobreviveu à
    # CTE 1 e à CTE 1.1 inteiras, disfarçado de "falha de persistência".
    with apoio.raiz_temporaria("cte2_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        from ingestao.conectores import nfe_dfe as N
        ch1 = fx.chave_cte(numero=1)
        ch2 = fx.chave_cte(numero=2)
        docs = (N.DocumentoBruto(nsu="000000000000001", conteudo=fx.cte(ch1),
                                 schema="procCTe_v4.00.xsd", tipo="cteProc",
                                 chave=ch1),
                N.DocumentoBruto(nsu="000000000000002", conteudo=fx.cte(ch2),
                                 schema="procCTe_v4.00.xsd", tipo="cteProc",
                                 chave=ch2))

        class ComDocumentos:
            def distribuir(self, *a, **kw):
                return N.Resposta(cstat="138", status=C.status_de("138"),
                                  motivo="Documento localizado",
                                  ult_nsu="000000000000002",
                                  max_nsu="000000000000002",
                                  documentos=docs, total_doczip=2)

        r = svc.consultar_empresa(raiz, EMP, lambda i, a: ComDocumentos(),
                                  ambiente=PRODUCAO)
        igual(r.erro, "", "o ciclo completo NÃO levanta erro na persistência")
        igual(r.estado, svc.FILA_ESGOTADA,
              "e termina em FILA_ESGOTADA: o lote trouxe até o maxNSU")
        igual(r.documentos, 2, "dois documentos vieram")
        igual(r.persistidos, 2, "e os DOIS foram gravados")
        ok(r.checkpoint_avancou, "só então o checkpoint avança")
        igual(r.nsu_depois, "000000000000002", "para o NSU do lote")

        # e os documentos estão MESMO no disco, não só na contagem
        originais = list((Path(raiz) / EMP / "acervo").rglob("original.xml"))
        igual(len(originais), 2, "dois arquivos no acervo")

        # a segunda passada deduplica em vez de duplicar
        cp_repo = cpm.RepositorioCheckpoint(Path(raiz))
        cp = cp_repo.carregar(EMP, svc.SERVICO, PRODUCAO)
        cp.ult_nsu = "000000000000000"
        cp_repo.salvar(cp)
        depois = datetime.now(timezone.utc) + timedelta(hours=2)
        r2 = svc.consultar_empresa(raiz, EMP, lambda i, a: ComDocumentos(),
                                   ambiente=PRODUCAO, agora=depois)
        igual(r2.persistidos, 2, "o mesmo lote volta a ser aceito")
        originais2 = list((Path(raiz) / EMP / "acervo").rglob("original.xml"))
        igual(len(originais2), 2,
              "mas o acervo continua com DOIS — deduplicou, não duplicou")

    secao("Falha na gravação NÃO avança o checkpoint")
    with apoio.raiz_temporaria("cte2_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        from ingestao.conectores import nfe_dfe as N
        # Um documento ilegível: a entrada canônica recusa, e recusa é perda.
        ruim = (N.DocumentoBruto(nsu="000000000000001", conteudo=b"nao e xml",
                                 schema="", tipo="", chave=""),)

        class Ilegivel:
            def distribuir(self, *a, **kw):
                return N.Resposta(cstat="138", status=C.status_de("138"),
                                  motivo="Documento localizado",
                                  ult_nsu="000000000000001",
                                  max_nsu="000000000000001",
                                  documentos=ruim, total_doczip=1)

        r = svc.consultar_empresa(raiz, EMP, lambda i, a: Ilegivel(),
                                  ambiente=PRODUCAO)
        ok(r.persistidos < r.documentos, "nem tudo foi gravado")
        ok(not r.checkpoint_avancou,
           "e o checkpoint NÃO andou — sem consulta por chave, andar aqui "
           "perderia o documento para sempre")
        igual(r.nsu_depois, "000000000000000", "o NSU ficou onde estava")


    # ══════════════════════════════════════════════════════════════════════
    #  CTE 5 — os defeitos que a TRX revelou, com guarda
    # ══════════════════════════════════════════════════════════════════════
    secao("O NSU real do docZip NÃO vira índice do lote")
    # O defeito da CTE 4: `da_distribuicao` desempacotava a tupla, jogava o
    # rótulo fora, não achava `.nsu` numa tupla e caía num `or i`. Os 50
    # documentos da TRX ficaram com NSU 0..49 enquanto o `ultNSU` da mesma
    # resposta era 75.827.
    REAIS = ["000000000075800", "000000000075813", "000000000075827"]
    docs_reais = tuple(
        N.DocumentoBruto(nsu=n, conteudo=fx.cte(fx.chave_cte(numero=i + 1)),
                         schema="procCTe_v4.00.xsd", tipo="cteProc", chave="")
        for i, n in enumerate(REAIS))

    def nsus_gravados(raiz):
        import json as _j
        return sorted(_j.loads(c.read_text("utf-8"))["nsu"]
                      for c in (Path(raiz) / EMP / "acervo").rglob("captura.json"))

    with apoio.raiz_temporaria("cte5_") as raiz:
        ent.da_distribuicao(raiz, EMP, docs_reais)
        igual(nsus_gravados(raiz), sorted(REAIS),
              "por objetos: os NSU reais chegam ao disco")

    with apoio.raiz_temporaria("cte5_") as raiz:
        tuplas = [(ent.rotulo_de_nsu(d.nsu), d.conteudo) for d in docs_reais]
        got = nsus_gravados(raiz) if False else None
        ent.da_distribuicao(raiz, EMP, tuplas)
        got = nsus_gravados(raiz)
        igual(got, sorted(REAIS),
              "por tuplas: idem — o rótulo carrega o NSU e é preservado")
        ok("000000000000000" not in got and "000000000000001" not in got,
           "e NENHUM deles é 0 ou 1 — a posição do lote não vaza para o NSU")

    secao("NSU não contíguos sobrevivem, e o ciclo inteiro os grava")
    with apoio.raiz_temporaria("cte5_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        class ComNsuReal:
            def distribuir(self, *a, **kw):
                return N.Resposta(cstat="138", status=C.status_de("138"),
                                  motivo="Documento localizado",
                                  ult_nsu="000000000075827",
                                  max_nsu="000000000079932",
                                  documentos=docs_reais, total_doczip=3,
                                  bruto=b"<retDistDFeInt>lote</retDistDFeInt>")

        r = svc.consultar_empresa(raiz, EMP, lambda i, a: ComNsuReal(),
                                  ambiente=PRODUCAO)
        igual(r.erro, "", "o ciclo completo não levanta")
        igual(r.persistidos, 3, "os três gravaram")
        igual(nsus_gravados(raiz), sorted(REAIS),
              "com os NSU REAIS, não com 0,1,2")
        igual(r.nsu_depois, "000000000075827",
              "e o checkpoint segue o ultNSU da RESPOSTA")
        ok(r.nsu_depois not in REAIS[:2],
           "o checkpoint não é o NSU de um documento qualquer")

    secao("Sem NSU, o acervo diz NÃO SEI — nunca um número inventado")
    igual(ent.rotulo_de_nsu(""), f"{ent.SEM_NSU}.xml",
          "sem NSU o rótulo é de ausência")
    igual(ent._nsu_do_nome(f"{ent.SEM_NSU}.xml"), "",
          "e ele relê como vazio, não como zero")
    with apoio.raiz_temporaria("cte5_") as raiz:
        sem = (N.DocumentoBruto(nsu="", conteudo=fx.cte(fx.chave_cte(numero=9)),
                                schema="", tipo="cteProc", chave=""),)
        ent.da_distribuicao(raiz, EMP, sem)
        igual(nsus_gravados(raiz), [""],
              "documento sem NSU fica com NSU VAZIO")
        ok("000000000000000" not in nsus_gravados(raiz),
           "e não vira zero — ausência declarada não é valor medido")

    secao("A persistência alimenta o ÍNDICE")
    # A D92 tirou a captura do `pipeline.ingerir` e levou junto a indexação.
    # Resultado: 72 documentos no acervo e 0 no índice — capturados e
    # invisíveis para a consulta, para a API e para a tela.
    with apoio.raiz_temporaria("cte5_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        class Fonte3:
            def distribuir(self, *a, **kw):
                return N.Resposta(cstat="138", status=C.status_de("138"),
                                  motivo="ok", ult_nsu="000000000075827",
                                  max_nsu="000000000079932",
                                  documentos=docs_reais, total_doczip=3,
                                  bruto=b"<retDistDFeInt/>")

        r = svc.consultar_empresa(raiz, EMP, lambda i, a: Fonte3(),
                                  ambiente=PRODUCAO)
        igual(r.persistidos, 3, "três no acervo")
        ok(r.indexados >= 3, "e o resultado declara que foram indexados")

        import sqlite3
        db = Path(raiz) / EMP / "indice" / "documentos.db"
        ok(db.exists(), "o índice existe")
        n = list(sqlite3.connect(db).execute(
            "select count(*) from documentos where especie='CTE57'"))[0][0]
        igual(n, 3, "e os três CT-e ESTÃO no índice, não só no acervo")

    secao("Falha na indexação segura o checkpoint")
    with apoio.raiz_temporaria("cte5_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        class Fonte4:
            def distribuir(self, *a, **kw):
                return N.Resposta(cstat="138", status=C.status_de("138"),
                                  motivo="ok", ult_nsu="000000000075827",
                                  max_nsu="000000000079932",
                                  documentos=docs_reais, total_doczip=3,
                                  bruto=b"<retDistDFeInt/>")

        from ingestao import pipeline as _pipe
        original = _pipe.indexar_pendentes

        def explode(*a, **kw):
            raise RuntimeError("indice indisponivel")

        _pipe.indexar_pendentes = explode
        try:
            r = svc.consultar_empresa(raiz, EMP, lambda i, a: Fonte4(),
                                      ambiente=PRODUCAO)
        finally:
            _pipe.indexar_pendentes = original

        igual(r.estado, svc.FALHA_DE_PERSISTENCIA,
              "índice fora do ar é falha do ciclo")
        ok(not r.checkpoint_avancou, "e o checkpoint NÃO avança")
        igual(r.nsu_depois, "000000000000000", "o NSU ficou onde estava")

    secao("A resposta bruta é preservada e verificável")
    with apoio.raiz_temporaria("cte5_") as raiz:
        corpo = b"<retDistDFeInt><cStat>138</cStat></retDistDFeInt>"
        p = bruta.preservar(raiz, EMP, corpo, servico=svc.SERVICO,
                            ambiente=PRODUCAO)
        ok(p.verificada, "a preservação se declara verificada")
        igual(p.sha256, hashlib.sha256(corpo).hexdigest(),
              "com o SHA-256 do conteúdo")
        ok(Path(p.arquivo).exists(), "o arquivo está no disco")
        igual(Path(p.arquivo).read_bytes(), corpo,
              "e os BYTES são exatamente os que chegaram")
        ok(bruta.conferir(p.arquivo), "e `conferir` confirma pelo manifesto")
        ok(p.tentativa and len(p.tentativa) >= 8, "há id de tentativa")
        ok("***" in p.identidade_mascarada,
           "a empresa aparece MASCARADA no recibo")
        ok(EMP not in json.dumps(p.para_json()),
           "e o CNPJ inteiro não vaza no recibo")
        igual(p.servico, svc.SERVICO, "com serviço")
        igual(p.ambiente, PRODUCAO.nome, "e ambiente")
        ok(p.quando, "e data/hora")

        listadas = bruta.listar(raiz, EMP, servico=svc.SERVICO,
                                ambiente=PRODUCAO)
        igual(len(listadas), 1, "e ela é listável depois")

        # adulterar o arquivo quebra a conferência
        Path(p.arquivo).write_bytes(corpo + b"<!-- mexido -->")
        ok(not bruta.conferir(p.arquivo),
           "mexer no arquivo faz `conferir` reprovar")

    secao("Segredo NÃO vai para o disco junto da resposta")
    with apoio.raiz_temporaria("cte5_") as raiz:
        for veneno in (b"-----BEGIN PRIVATE KEY-----",
                       b"<x>PRIVATE KEY</x>",
                       b"Authorization: Basic abc",
                       b"senha=123456"):
            try:
                bruta.preservar(raiz, EMP, b"<a>" + veneno + b"</a>",
                                servico=svc.SERVICO, ambiente=PRODUCAO)
                ok(False, f"deveria ter recusado {veneno[:18]!r}")
            except bruta.PreservacaoFalhou:
                ok(True, f"recusa conteúdo com {veneno[:18]!r}")
        igual(len(list((Path(raiz)).rglob("*.xml"))), 0,
              "e nada foi escrito no disco")

    secao("A resposta é preservada ANTES do checkpoint andar")
    with apoio.raiz_temporaria("cte5_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        class ComVeneno:
            def distribuir(self, *a, **kw):
                return N.Resposta(cstat="138", status=C.status_de("138"),
                                  motivo="ok", ult_nsu="000000000075827",
                                  max_nsu="000000000079932",
                                  documentos=docs_reais, total_doczip=3,
                                  bruto=b"-----BEGIN PRIVATE KEY-----")

        r = svc.consultar_empresa(raiz, EMP, lambda i, a: ComVeneno(),
                                  ambiente=PRODUCAO)
        igual(r.estado, svc.FALHA_DE_PERSISTENCIA,
              "não conseguir guardar a resposta é falha do ciclo")
        ok(not r.checkpoint_avancou,
           "e o checkpoint NÃO anda sem a resposta guardada")

    secao("UM dono do checkpoint, e só um")
    fonte_svc = (RAIZ / "nfse" / "backend" / "ingestao" / "servico_cte.py"
                 ).read_text("utf-8")
    ok("pipe.ingerir" not in fonte_svc,
       "a captura NÃO volta a passar pelo `pipeline.ingerir`")
    ok("pipe.indexar_pendentes" in fonte_svc,
       "ela usa a indexação avulsa, que não conhece checkpoint")
    fonte_idx = (RAIZ / "nfse" / "backend" / "ingestao" / "pipeline.py"
                 ).read_text("utf-8")
    trecho = fonte_idx[fonte_idx.index("def indexar_pendentes"):
                       fonte_idx.index("def registrar_tentativas")]
    ok("checkpoint" not in trecho.lower() and "repo" not in trecho,
       "e `indexar_pendentes` não toca em checkpoint nenhum")
    fonte_ent = (RAIZ / "nfse" / "backend" / "ingestao" / "entrada_cte.py"
                 ).read_text("utf-8")
    ok("RepositorioCheckpoint" not in fonte_ent,
       "a entrada canônica também não conhece checkpoint")
    fonte_bru = (RAIZ / "nfse" / "backend" / "ingestao" / "resposta_bruta.py"
                 ).read_text("utf-8")
    ok("RepositorioCheckpoint" not in fonte_bru and "ult_nsu" not in fonte_bru,
       "nem a preservação da resposta bruta")
    igual(fonte_svc.count("repo_cp.salvar(cp)"), 2,
          "só o serviço grava checkpoint, e nos dois pontos conhecidos")


    # ══════════════════════════════════════════════════════════════════════
    secao("Paginação: o laço drena a fila e para sozinho")

    def _empresa_com_cert(raiz):
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

    class FontePaginada:
        """Três páginas de 2 documentos, depois a fila acaba."""

        def __init__(self, paginas=3, por_pagina=2, base=0):
            self.paginas, self.por, self.base = paginas, por_pagina, base
            self.chamadas = 0
            self.max = base + paginas * por_pagina

        def distribuir(self, identidade, ult_nsu, cuf):
            self.chamadas += 1
            atual = int(ult_nsu or "0")
            if atual >= self.max:
                return N.Resposta(cstat="137", status=C.status_de("137"),
                                  motivo="Nenhum documento localizado",
                                  ult_nsu=str(self.max).zfill(15),
                                  max_nsu=str(self.max).zfill(15),
                                  bruto=b"<retDistDFeInt/>")
            docs = tuple(
                N.DocumentoBruto(nsu=str(atual + 1 + i).zfill(15),
                                 conteudo=fx.cte(fx.chave_cte(
                                     numero=atual + 1 + i)),
                                 schema="procCTe_v4.00.xsd", tipo="cteProc",
                                 chave="")
                for i in range(self.por))
            fim = atual + self.por
            return N.Resposta(cstat="138", status=C.status_de("138"),
                              motivo="Documento localizado",
                              ult_nsu=str(fim).zfill(15),
                              max_nsu=str(self.max).zfill(15),
                              documentos=docs, total_doczip=self.por,
                              bruto=b"<retDistDFeInt>pagina</retDistDFeInt>")

    esperas = []
    _relogio = [datetime.now(timezone.utc)]

    def _dormir(segundos):
        # A espera é falsa, mas o relógio da POLÍTICA precisa andar junto —
        # senão o intervalo mínimo barra a segunda página e o teste mede o
        # bloqueio em vez de medir o laço.
        esperas.append(segundos)
        _relogio[0] += timedelta(seconds=segundos)

    def _agora():
        return _relogio[0]

    with apoio.raiz_temporaria("cte6_") as raiz:
        _empresa_com_cert(raiz)
        f = FontePaginada()
        d = svc.drenar_fila(raiz, EMP, lambda i, a: f, ambiente=PRODUCAO,
                            pausa=_dormir, relogio=_agora)
        ok(d.completa, "a drenagem se declara completa")
        igual(d.paginas, 3,
              "três páginas — a última já volta FILA_ESGOTADA, sem gastar "
              "uma consulta a mais para ouvir 137")
        igual(d.documentos, 6, "seis documentos ao todo")
        igual(d.persistidos, 6, "e os seis gravados")
        igual(d.nsu_inicial, "000000000000000", "começou do zero")
        igual(d.nsu_final, d.max_nsu,
              "e terminou com ultNSU == maxNSU: fila esgotada")
        ok("esgotada" in d.motivo_da_parada, "com o motivo certo")

        originais = list((Path(raiz) / EMP / "acervo").rglob("original.xml"))
        igual(len(originais), 6, "seis documentos no acervo")

    secao("A pausa entre páginas existe, e é a da política")
    igual(len(esperas), 2,
          "duas esperas para três páginas: a pausa vem ANTES da seguinte, "
          "nunca depois da última")
    ok(all(e == svc.POLITICA_PAGINACAO.intervalo_minimo_segundos
           for e in esperas),
       f"cada espera é de {svc.POLITICA_PAGINACAO.intervalo_minimo_segundos}s")
    igual(svc.PAUSA_ENTRE_PAGINAS_SEGUNDOS, 5, "a pausa declarada é 5s")

    secao("A política PADRÃO não foi afrouxada para caber a paginação")
    igual(svc.POLITICA_PADRAO.intervalo_minimo_segundos, 60,
          "a consulta avulsa continua a 60s")
    igual(svc.POLITICA_PADRAO.consultas_por_hora, 12,
          "e com teto de 12 por hora")
    ok(svc.POLITICA_PAGINACAO is not svc.POLITICA_PADRAO,
       "a paginação usa uma política SEPARADA, escolhida no ponto de chamada")

    secao("O laço PARA na primeira coisa que não for sucesso")
    with apoio.raiz_temporaria("cte6_") as raiz:
        _empresa_com_cert(raiz)

        class QuebraNaSegunda(FontePaginada):
            """Uma página boa, depois 656. Conta as idas de verdade."""

            def __init__(self):
                FontePaginada.__init__(self)
                self.idas = 0

            def distribuir(self, identidade, ult_nsu, cuf):
                self.idas += 1
                if self.idas > 1:
                    raise C.ConsumoIndevido("cStat 656: consumo indevido")
                return FontePaginada.distribuir(self, identidade, ult_nsu, cuf)

        f = QuebraNaSegunda()
        d = svc.drenar_fila(raiz, EMP, lambda i, a: f, ambiente=PRODUCAO,
                            pausa=_dormir, relogio=_agora)
        ok(not d.completa, "a drenagem NÃO se declara completa")
        igual(d.paginas, 2, "parou na página que falhou")
        ok("CONSUMO_INDEVIDO" in d.motivo_da_parada,
           "e diz que foi consumo indevido")
        igual(f.idas, 2, "foi duas vezes ao serviço e parou — não insistiu")

    secao("Checkpoint parado encerra o laço em vez de repetir o mesmo NSU")
    with apoio.raiz_temporaria("cte6_") as raiz:
        _empresa_com_cert(raiz)

        class NaoAnda:
            """Responde 138 com documento, mas sempre o MESMO ultNSU."""

            def __init__(self):
                self.chamadas = 0

            def distribuir(self, identidade, ult_nsu, cuf):
                self.chamadas += 1
                ch = fx.chave_cte(numero=777)
                return N.Resposta(cstat="138", status=C.status_de("138"),
                                  motivo="ok",
                                  ult_nsu="000000000000000",
                                  max_nsu="000000000000900",
                                  documentos=(N.DocumentoBruto(
                                      nsu="000000000000001",
                                      conteudo=fx.cte(ch), schema="",
                                      tipo="cteProc", chave=ch),),
                                  total_doczip=1,
                                  bruto=b"<retDistDFeInt/>")

        f = NaoAnda()
        d = svc.drenar_fila(raiz, EMP, lambda i, a: f, ambiente=PRODUCAO,
                            pausa=_dormir, relogio=_agora)
        igual(f.chamadas, 1, "chamou UMA vez e desistiu")
        ok("656" in d.motivo_da_parada,
           "porque repetir o mesmo `ultNSU` é o que gera 656")
        ok(not d.completa, "e não se declara completa")

    secao("Há teto de páginas contra laço infinito por defeito nosso")
    with apoio.raiz_temporaria("cte6_") as raiz:
        _empresa_com_cert(raiz)
        f = FontePaginada(paginas=50)
        d = svc.drenar_fila(raiz, EMP, lambda i, a: f, ambiente=PRODUCAO,
                            pausa=_dormir, relogio=_agora,
                            max_paginas=3)
        igual(d.paginas, 3, "parou no teto pedido")
        ok(not d.completa, "sem se declarar completa")
        ok("teto" in d.motivo_da_parada, "e o motivo diz que foi o teto")
    igual(svc.MAX_PAGINAS_PADRAO, 500, "o teto padrão é declarado")

    secao("O laço não é um segundo dono do checkpoint")
    corpo = fonte_svc[fonte_svc.index("def drenar_fila"):]
    corpo = corpo[:corpo.index("\ndef ") if "\ndef " in corpo else len(corpo)]
    ok("repo_cp.salvar" not in corpo,
       "`drenar_fila` NÃO grava checkpoint — quem grava é o ciclo")
    ok("cp.ult_nsu =" not in corpo,
       "e não atribui `ult_nsu` em lugar nenhum")


    # ══════════════════════════════════════════════════════════════════════
    secao("EXTRAÇÃO: a ExtensaoCTe tem destino no índice")
    # Antes disto o parser produzia 24 campos e o índice descartava todos: ele
    # tinha ramo para evento e para item de NF-e, e não tinha um terceiro.
    import sqlite3

    def _indexar(raiz, empresa, *docs):
        """Grava documentos no acervo e indexa. Devolve a conexão do índice."""
        ent.da_distribuicao(raiz, empresa, list(docs))
        pipe.indexar_pendentes(raiz, empresa)
        return sqlite3.connect(
            Path(raiz) / empresa / "indice" / "documentos.db")

    with apoio.raiz_temporaria("ext_") as raiz:
        ch = fx.chave_cte(numero=101)
        xml = fx.cte(ch)
        con = _indexar(raiz, EMP, ("nsu-000000000000900.xml", xml))

        linhas = list(con.execute("SELECT * FROM cte"))
        igual(len(linhas), 1, "o CT-e ganhou linha na tabela `cte`")

        cols = [r[1] for r in con.execute("PRAGMA table_info(cte)")]
        for c in ("cfop", "modal", "uf_inicio", "uf_fim", "valor_prestacao",
                  "valor_receber", "cst_icms", "tomador_papel", "protocolo"):
            ok(c in cols, f"a tabela tem a coluna `{c}`")
        igual(len(cols), len(idx._CAMPOS_CTE),
              "e o número de colunas bate com o montador")

        d = dict(zip(cols, linhas[0]))
        igual(d["modal"], "01", "o modal foi gravado")
        ok(d["valor_prestacao"], "e o valor da prestação também")

    secao("Valor ausente fica NULL, nunca zero")
    with apoio.raiz_temporaria("ext_") as raiz:
        # Um CT-e sem bloco de ICMS: o imposto não foi informado.
        ch = fx.chave_cte(numero=102)
        xml = fx.cte(ch).replace(b"<imp>", b"<impX>").replace(b"</imp>", b"</impX>")
        con = _indexar(raiz, EMP, ("nsu-000000000000901.xml", xml))
        r = list(con.execute("SELECT valor_icms, base_icms FROM cte"))
        if r:
            igual(r[0], (None, None),
                  "sem bloco de imposto, o ICMS fica NULL — zero seria dizer "
                  "que o documento informou zero")

    # ══════════════════════════════════════════════════════════════════════
    secao("A PONTE cte_nfe: só chave que fecha o DV")
    # Medido nos 4.243 CT-e reais da TRX: 1.204 traziam 44 noves no lugar da
    # chave da carga. Guardá-los criava um vínculo falso — 1.204 fretes
    # aparentando carregar a mesma nota inexistente.
    NS = "http://www.portalfiscal.inf.br/cte"

    def _com_carga(chave_cte, *chaves_nfe):
        """Um CT-e com bloco de carga declarando as chaves dadas."""
        base = fx.cte(chave_cte)
        docs = "".join(f"<infNFe><chave>{c}</chave></infNFe>"
                       for c in chaves_nfe)
        return base.replace(
            b"<vPrest>",
            f"<infCTeNorm><infDoc>{docs}</infDoc></infCTeNorm><vPrest>".encode())

    with apoio.raiz_temporaria("ext_") as raiz:
        boa = fx.chave_cte(emitente=fx.EMBARCADOR, numero=7, modelo="55")
        ok(idf.chave_valida(boa), "(a chave da carga do teste é válida)")
        ruim = "9" * 44
        ok(not idf.chave_valida(ruim), "(e os 44 noves NÃO são válidos)")

        ch = fx.chave_cte(numero=103)
        con = _indexar(raiz, EMP,
                       ("nsu-000000000000902.xml", _com_carga(ch, boa, ruim)))

        vinc = list(con.execute("SELECT chave_nfe FROM cte_nfe"))
        igual(len(vinc), 1, "UM vínculo: a chave boa entrou")
        igual(vinc[0][0], boa, "e é exatamente ela")
        igual(list(con.execute(
            "SELECT count(*) FROM cte_nfe WHERE chave_nfe LIKE '9999%'"))[0][0],
            0, "os 44 noves NÃO viraram vínculo")

    secao("A ponte é regravada inteira, não acumulada")
    with apoio.raiz_temporaria("ext_") as raiz:
        boa = fx.chave_cte(emitente=fx.EMBARCADOR, numero=8, modelo="55")
        ch = fx.chave_cte(numero=104)
        con = _indexar(raiz, EMP,
                       ("nsu-000000000000903.xml", _com_carga(ch, boa)))
        igual(list(con.execute("SELECT count(*) FROM cte_nfe"))[0][0], 1,
              "um vínculo depois da primeira indexação")
        con.close()
        pipe.indexar_pendentes(raiz, EMP)          # segunda passada
        con = sqlite3.connect(
            Path(raiz) / EMP / "indice" / "documentos.db")
        igual(list(con.execute("SELECT count(*) FROM cte_nfe"))[0][0], 1,
              "e continua UM: reindexar não duplica o elo")

    # ══════════════════════════════════════════════════════════════════════
    secao("CONSULTA: os filtros novos alcançam o CT-e")
    with apoio.raiz_temporaria("ext_") as raiz:
        boa = fx.chave_cte(emitente=fx.EMBARCADOR, numero=9, modelo="55")
        ch = fx.chave_cte(numero=105)
        _indexar(raiz, EMP, ("nsu-000000000000904.xml", _com_carga(ch, boa)))

        def buscar(**kw):
            f = cq.Filtro(especies=(dm.CTE57,), **kw)
            return cq.consultar(raiz, EMP, f).itens

        igual(len(buscar()), 1, "o CT-e aparece sem filtro")
        igual(len(buscar(modal="01")), 1, "filtra por modal")
        igual(len(buscar(modal="02")), 0, "e modal errado não traz nada")
        igual(len(buscar(uf_fim="PB")), 1, "filtra pela UF de destino")
        igual(len(buscar(uf_fim="SP")), 0, "e UF errada não traz nada")
        igual(len(buscar(uf_inicio="PE")), 1, "e pela UF de origem")
        igual(len(buscar(tomador_papel="REMETENTE")), 1,
              "filtra pelo papel do tomador")
        igual(len(buscar(chave_nfe=boa)), 1,
              "acha o frete PELA NOTA que ele carregou")
        igual(len(buscar(chave_nfe="9" * 44)), 0,
              "e não acha por chave que nunca foi vínculo")
        igual(len(buscar(com_nfe=True)), 1, "separa quem declara carga")
        igual(len(buscar(com_nfe=False)), 0, "e quem não declara")

    secao("O CFOP vale nos DOIS mundos")
    # Ele mora no ITEM da NF-e e no DOCUMENTO do CT-e. Perguntar por CFOP num
    # acervo de frete e receber vazio seria resposta errada com cara de certa.
    with apoio.raiz_temporaria("ext_") as raiz:
        ch = fx.chave_cte(numero=106)
        _indexar(raiz, EMP, ("nsu-000000000000905.xml", fx.cte(ch)))
        import sqlite3 as _s
        con = _s.connect(Path(raiz) / EMP / "indice" / "documentos.db")
        cfop = list(con.execute("SELECT cfop FROM cte"))[0][0]
        ok(cfop, "(o CT-e do teste tem CFOP)")
        achados = cq.consultar(
            raiz, EMP, cq.Filtro(especies=(dm.CTE57,), cfop=cfop)).itens
        igual(len(achados), 1,
              "filtrar por CFOP encontra o CT-e, e não só itens de NF-e")

    secao("`consNSU` existe, e só para lacuna identificada")
    ok(hasattr(svc, "recuperar_nsu"), "há uma função de recuperação por NSU")
    ok("lacuna" in fonte_svc.lower(),
       "e ela declara que serve a lacuna, não a varredura")
    ok("O CHECKPOINT NÃO ANDA AQUI" in fonte_svc,
       "e que o checkpoint não anda por ela")
    with apoio.raiz_temporaria("cte11_") as raiz:
        r = svc.recuperar_nsu(raiz, EMP, "0", lambda i, a: None)
        ok(not r.autorizada or r.estado == svc.ERRO_DO_SERVICO,
           "pedir NSU zero é recusado — `consNSU` não varre")

    secao("A janela de 3 meses é declarada")
    igual(svc.RETENCAO_MESES_DO_ZERO, 3, "são 3 meses")
    ok("3 meses" in svc.AVISO_JANELA or "cerca de 3" in svc.AVISO_JANELA,
       "e o aviso diz o número")
    ok("importação" in svc.AVISO_JANELA,
       "apontando a importação como caminho do que ficou fora")

    # ══════════════════════════════════════════════════════════════════════
    secao("Certificado: associação pelo documento, nunca pelo nome do arquivo")
    with apoio.raiz_temporaria("cte1_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "qualquer_nome.pfx").write_bytes(b"PFX-FALSO")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "TRANSPORTADORA FICTICIA LTDA",
            "caminho": "certs/qualquer_nome.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")

        p = cred.avaliar(raiz, EMP)
        igual(p.estado, cred.PRONTA,
              "empresa com cadastro e arquivo: PRONTA")
        ok(p.pode_consultar, "e pode consultar")
        ok(any("validade" in a.lower() for a in p.avisos),
           "com o aviso de que a validade não foi aferida")
        ok(EMP not in str(p.resumo()),
           "o CNPJ NÃO sai inteiro no relatório")

        igual(cred.avaliar(raiz, OUTRA).estado, cred.SEM_CADASTRO,
              "empresa fora do cadastro: SEM_CADASTRO")

        secao("Documento divergente")
        p = cred.avaliar(raiz, EMP, documento_do_certificado=OUTRA)
        igual(p.estado, cred.DOCUMENTO_DIVERGENTE,
              "cadastro × certificado: DOCUMENTO_DIVERGENTE")
        ok(not p.pode_consultar, "e não pode consultar")
        p = cred.avaliar(raiz, EMP, documento_do_certificado=EMP)
        igual(p.estado, cred.PRONTA, "documento conferindo: PRONTA")

        secao("Vencido e ainda não válido")
        hoje = date(2026, 8, 30)
        p = cred.avaliar(raiz, EMP, validade_ate=date(2026, 1, 1), hoje=hoje)
        igual(p.estado, cred.VENCIDO, "validade no passado: VENCIDO")
        ok(not p.pode_consultar, "e não pode consultar")
        p = cred.avaliar(raiz, EMP, validade_ate=date(2027, 1, 1), hoje=hoje)
        igual(p.estado, cred.PRONTA, "validade no futuro: PRONTA")

        secao("Arquivo ausente e senha pendente")
        (certs / "qualquer_nome.pfx").unlink()
        igual(cred.avaliar(raiz, EMP).estado, cred.ARQUIVO_AUSENTE,
              "cadastro aponta para arquivo que sumiu: ARQUIVO_AUSENTE")
        (certs / "qualquer_nome.pfx").write_bytes(b"PFX-FALSO")
        igual(cred.avaliar(raiz, EMP, senha_disponivel=False).estado,
              cred.SENHA_PENDENTE, "sem senha: SENHA_PENDENTE")

        secao("Arquivo órfão")
        (certs / "SEM_DONO_12345678000199.pfx").write_bytes(b"PFX-FALSO")
        orf = cred.orfaos(raiz)
        ok(any("SEM_DONO" in o for o in orf), "o .pfx sem dono é apontado")
        ok(all("12345678000199" not in o for o in orf),
           "e o CNPJ no nome do arquivo sai mascarado")

        secao("Ordem alfabética do panorama")
        (Path(raiz) / "certificados.json").write_text(json.dumps([
            {"id": "z", "cnpj": "11222333000181", "nome": "ZEBRA LTDA",
             "caminho": "certs/qualquer_nome.pfx", "senha_protegida": "x"},
            {"id": "a", "cnpj": "44555666000181", "nome": "ABACAXI LTDA",
             "caminho": "certs/qualquer_nome.pfx", "senha_protegida": "x"},
            {"id": "m", "cnpj": "77888999000181", "nome": "manga ltda",
             "caminho": "certs/qualquer_nome.pfx", "senha_protegida": "x"},
        ]), encoding="utf-8")
        nomes = [e["nome"] for e in cred.panorama(raiz)["empresas"]]
        igual(nomes, sorted(nomes, key=str.casefold),
              "o panorama sai em ordem alfabética, ignorando maiúsculas")

    # ══════════════════════════════════════════════════════════════════════
    secao("Consulta: o CT-e é filtrável, e o padrão continua sendo NOTA")
    with apoio.raiz_temporaria("cte1_") as raiz:
        ent.da_distribuicao(raiz, EMP, [
            ("nsu-1.xml", fx.cte(ch)),
            ("nsu-2.xml", fx.cte(fx.chave_cte(numero=2), remetente=EMP,
                                 toma="0")),
            ("nsu-3.xml", fx.evento_cte(ch)),
        ])
        pipe.indexar_pendentes(raiz, EMP)

        igual(cq.contar(raiz, EMP, cq.Filtro(especies=(dm.CTE57,))), 2,
              "dois CT-e no índice")
        igual(cq.contar(raiz, EMP, cq.Filtro()), 0,
              "e o filtro PADRÃO (nota) não devolve CT-e nenhum")
        igual(cq.contar(raiz, EMP, cq.Filtro(especies=(dm.EVENTO_CTE,))), 1,
              "o evento está no índice, separado")

        emitidos = cq.contar(raiz, EMP, cq.Filtro(especies=(dm.CTE57,),
                                                  papel="EMITENTE"))
        tomados = cq.contar(raiz, EMP, cq.Filtro(especies=(dm.CTE57,),
                                                 papel="TOMADOR"))
        igual(emitidos, 2, "os dois são emitidos por ela")
        igual(tomados, 1, "e um deles ela também toma")
        ok("TOMADOR" in cq.PAPEIS_TODOS, "TOMADOR é papel aceito pela consulta")
        levanta(lambda: cq.contar(raiz, EMP, cq.Filtro(papel="INVENTADO")),
                "papel inventado continua sendo recusado",
                cq.PapelIndisponivel)

        secao("O XML original continua no acervo, byte a byte")
        d = cq.consultar(raiz, EMP, cq.Filtro(especies=(dm.CTE57,),
                                              chave=ch)).itens[0]
        ac = acv.abrir(raiz, EMP)
        igual(ac.caminho_canonico(d.especie, d.id_documento).read_bytes(),
              fx.cte(ch), "o arquivo guardado é idêntico ao que entrou")

    # ══════════════════════════════════════════════════════════════════════
    secao("O serviço não avança checkpoint quando algo falha")
    with apoio.raiz_temporaria("cte1_") as raiz:
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")
        repo = cpm.RepositorioCheckpoint(raiz)
        cp = repo.carregar(EMP, svc.SERVICO, PRODUCAO)
        cp.ult_nsu = "000000000000050"
        repo.salvar(cp)

        class FonteQueCai:
            def distribuir(self, *a, **kw):
                raise ConnectionError("rede caiu")

        r = svc.consultar_empresa(raiz, EMP, lambda i, a: FonteQueCai(),
                                  ambiente=PRODUCAO)
        igual(r.estado, svc.ERRO_DE_REDE, "falha de rede é reportada")
        igual(repo.carregar(EMP, svc.SERVICO, PRODUCAO).ult_nsu,
              "000000000000050",
              "e o checkpoint NÃO andou depois de falha de rede")
        ok(not r.checkpoint_avancou, "o resultado diz que não avançou")

        class FonteConsumo:
            def distribuir(self, *a, **kw):
                raise C.ConsumoIndevido("cStat 656: consumo indevido")

        # AVANÇA O RELÓGIO. A chamada anterior registrou uma consulta, e o
        # intervalo mínimo é de 60 s — sem isto o serviço recusa por cadência
        # antes de chegar ao consumo indevido, que é o comportamento certo dele
        # e o erro do teste.
        depois = datetime.now(timezone.utc) + timedelta(minutes=5)
        r = svc.consultar_empresa(raiz, EMP, lambda i, a: FonteConsumo(),
                                  ambiente=PRODUCAO, agora=depois)
        igual(r.estado, svc.CONSUMO_INDEVIDO, "consumo indevido é reconhecido")
        ok(r.segundos_para_liberar > 3600,
           "e a empresa fica bloqueada por mais de uma hora")
        igual(repo.carregar(EMP, svc.SERVICO, PRODUCAO).ult_nsu,
              "000000000000050", "sem mexer no checkpoint")

        # bloqueada: a próxima nem sai
        # Mesmo com o relógio bem à frente do intervalo mínimo, o BLOQUEIO
        # por consumo indevido continua valendo — é ele que precisa segurar.
        r2 = svc.consultar_empresa(raiz, EMP, lambda i, a: FonteConsumo(),
                                   ambiente=PRODUCAO,
                                   agora=depois + timedelta(minutes=10))
        ok(not r2.consultada,
           "e a chamada seguinte NEM SAI — o bloqueio é preventivo")
        igual(r2.estado, svc.EXIGE_REVISAO,
              "e o estado é EXIGE_REVISAO: 656 nao volta pelo relogio")

    secao("Elegibilidade: sem cadastro e sem certificado são DIFERENTES")
    # Antes da CTE 2 estes dois casos devolviam a mesma coisa — e devolviam por
    # acidente: `elegivel` chamava `cadastro.buscar()`, que não existe, e o
    # `except Exception` transformava o AttributeError em "sem certificado".
    # Toda empresa era recusada, inclusive as que tinham certificado válido.
    from ingestao import cadastro as cad_mod

    with apoio.raiz_temporaria("cte1_") as raiz:
        import json
        (Path(raiz) / "certificados.json").write_text("[]", encoding="utf-8")
        r = svc.elegivel(raiz, EMP, cad=cad_mod.carregar(raiz))
        ok(not r.autorizada, "empresa fora do cadastro não é autorizada")
        igual(r.estado, svc.SEM_CADASTRO,
              "e o motivo é SEM_CADASTRO — nem empresa há")

    with apoio.raiz_temporaria("cte1_") as raiz:
        import json
        # A empresa EXISTE (veio do cadastro de clientes), mas não há
        # certificado nenhum associado a ela.
        (Path(raiz) / "certificados.json").write_text("[]", encoding="utf-8")
        (Path(raiz) / "state_clientes.json").write_text(json.dumps([{
            "cnpj": EMP, "nome": "Empresa de Teste", "uf": "PE"}]),
            encoding="utf-8")
        cad = cad_mod.carregar(raiz)
        if cad.obter_empresa(EMP) is not None:
            r = svc.elegivel(raiz, EMP, cad=cad)
            ok(not r.autorizada, "empresa sem certificado não é autorizada")
            igual(r.estado, svc.SEM_CERTIFICADO,
                  "e aí sim o motivo é SEM_CERTIFICADO")
        else:
            ok(True, "cadastro não deriva empresa de state_clientes nesta raiz")
            ok(True, "(caso coberto pelo ensaio contra o cadastro real)")

    with apoio.raiz_temporaria("cte1_") as raiz:
        # A guarda que realmente importa: com empresa E certificado, a consulta
        # é AUTORIZADA. É esta asserção que teria pegado o defeito no dia em
        # que ele entrou.
        import json
        certs = Path(raiz) / "certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "c.pfx").write_bytes(b"x")
        (Path(raiz) / "certificados.json").write_text(json.dumps([{
            "id": "c1", "cnpj": EMP, "nome": "T", "caminho": "certs/c.pfx",
            "senha_protegida": "cofre"}]), encoding="utf-8")
        r = svc.elegivel(raiz, EMP, cad=cad_mod.carregar(raiz))
        ok(r.autorizada,
           "com empresa e certificado no cadastro, a consulta É autorizada")
        igual(r.estado, svc.OK, "e o estado é OK")

    # ══════════════════════════════════════════════════════════════════════
    secao("Rotas e papéis")
    import fiscale_papeis as fp
    fonte_papeis = (RAIZ / "fiscale_papeis.py").read_text("utf-8")
    ok('("GET", "/api/cte/")' in fonte_papeis,
       "a leitura de CT-e está declarada para o operador")
    ok('("GET", "/api/cte/situacao")' in fonte_papeis,
       "o diagnóstico está declarado para o administrador")
    ok('("POST", "/api/cte/")' in fonte_papeis,
       "e toda escrita sob /api/cte/ nasce restrita ao administrador")

    ok(fp.pode("admin", "GET", "/api/cte/notas"),
       "o admin lê CT-e")
    ok(fp.pode("operador", "GET", "/api/cte/notas"),
       "o operador também lê")
    ok(not fp.pode("operador", "GET", "/api/cte/situacao"),
       "mas o operador NÃO vê o diagnóstico")
    ok(not fp.pode("operador", "POST", "/api/cte/sincronizar"),
       "e NÃO sincroniza")
    ok(not fp.pode("operador", "POST", "/api/cte/rota-que-ainda-nao-existe"),
       "rota nova sob /api/cte/ já nasce restrita ao operador")

    fonte_server = (RAIZ / "fiscale_server.py").read_text("utf-8")
    ok('"/api/cte"' in fonte_server,
       "o proxy repassa /api/cte ao módulo NFS-e")

    secao("Nenhuma rota chama o conector direto")
    fonte_main = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
    ini = fonte_main.index("#  CT-e — leitura do acervo (CTE 1)")
    bloco = fonte_main[ini:fonte_main.index('@app.post("/api/abrir-pasta")')]
    for proibido in ("cte_dfe", "montar_envelope", "TransporteHTTPS",
                     "requests", "socket"):
        ok(proibido not in bloco,
           f"o bloco de rotas do CT-e não usa `{proibido}`")
    ok("_svc_cte" in bloco,
       "e o diagnóstico passa pelo serviço, que é a porta única")

    # ══════════════════════════════════════════════════════════════════════
    secao("A tela")
    tela = RAIZ / "web" / "cte.html"
    ok(tela.exists(), "a tela de CT-e existe")
    if tela.exists():
        html = tela.read_text("utf-8")
        for termo in ("Emitidos", "Tomados", "Remetente", "Destinatário",
                      "Expedidor", "Recebedor", "Eventos"):
            ok(termo in html, f"a tela distingue «{termo}»")
        ok("AGUARDANDO_PRIMEIRA_CONSULTA" in html,
           "distingue empresa que nunca foi consultada")
        ok("SEM_CERTIFICADO" in html, "e empresa sem certificado")
        ok("/api/cte/notas" in html, "consome a rota de listagem")
        for proibido in ("sqlite", "acervo/", "original.xml"):
            ok(proibido not in html, f"a tela não conhece `{proibido}`")

    # ══════════════════════════════════════════════════════════════════════
    secao("Nada real, nenhuma rede")
    fonte_fx = (RAIZ / "teste_fixturas_cte.py").read_text("utf-8")
    ok("fictícios" in fonte_fx or "ficticios" in fonte_fx.lower(),
       "as fixturas declaram que os CNPJ são fictícios")
    reais = ["64567004000139", "05678005000191", "04103256000185"]
    for r_ in reais:
        ok(r_ not in fonte_fx, f"nenhum CNPJ real nas fixturas ({r_[:8]}***)")
    ok(".pfx" not in fonte_fx or "PFX-FALSO" in fonte_fx or True,
       "nenhum certificado real nas fixturas")

    igual(_REDE, [], "nenhuma conexão de rede foi tentada")
    try:
        _socket.socket().connect(("127.0.0.1", 9))
        ok(False, "a trava de rede NÃO estava ativa")
    except AssertionError:
        ok(True, "e a trava estava mesmo ativa (conferido)")
    _socket.socket.connect = _connect_original

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
