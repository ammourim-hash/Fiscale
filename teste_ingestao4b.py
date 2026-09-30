#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da ING 4B — materialização de estado histórico e classificação.

    python teste_ingestao4b.py

O QUE ESTA SUÍTE EXISTE PARA IMPEDIR

    1. Que uma empresa com divergência externa registrada volte a ser
       classificada como empresa nova. Foi o que aconteceu com a MONTE: o `656`
       ficou só no `ultimo_erro`, os campos estruturados não existiam, e no
       inventário ela apareceu como `SEM_HISTORICO_LEGADO` /
       `COMPLETA_DESDE_ZERO` — indistinguível de quem nunca foi consultado.

    2. Que uma reconstrução de estado invente informação. O `ultNSU` devolvido
       pela SEFAZ **não está no disco**; só o `cStat` e o `xMotivo` estão. Quem
       reconstrói precisa declarar de onde tirou o número.

    3. Que um registro reconstruído se passe por registro real na trilha.

REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import acervo as acv                 # noqa: E402
from ingestao import auditoria as aud              # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import migracao_legado as mig        # noqa: E402
from ingestao import migracao_r2 as r2             # noqa: E402
from ingestao.ambiente import HOMOLOGACAO, PRODUCAO  # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402

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
    ok(a == b, desc + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


EMPRESA = T.EMPRESA_A
FONTE = "documento X, linha Y — saída do console da consulta real"
ERRO_656 = ("cStat 656: Rejeicao: Consumo Indevido (Deve ser utilizado o ultNSU "
            "nas solicitacoes subsequentes. Tente apos 1 hora).")


def plantar_656(raiz, cnpj=EMPRESA, ambiente=PRODUCAO, erro=ERRO_656,
                ult_nsu=cpm.NSU_ZERO, docs=0):
    """Checkpoint como o de 13/08: o 656 no `ultimo_erro`, e nada mais."""
    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(cnpj, cpm.NFE_DISTRIBUICAO, ambiente)
    cp.ult_nsu = cpm.normalizar_nsu(ult_nsu)
    cp.status = cpm.ERRO_TRANSITORIO
    cp.ultimo_erro = erro
    cp.ultima_consulta = "2026-08-14T00:26:56+00:00"
    cp.falhas_consecutivas = 1
    cp.documentos_recebidos = docs
    repo.salvar(cp)
    return cp


def ler(raiz, cnpj=EMPRESA, ambiente=PRODUCAO):
    return RepositorioCheckpoint(raiz).carregar(cnpj, cpm.NFE_DISTRIBUICAO, ambiente)


# ══════════════════════════════════════════════════════════════════════════
secao("A evidência do 656 é conferida ANTES de qualquer escrita")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    c = r2.conferir(raiz, EMPRESA)
    ok(c["ok"], "a conferência roda")
    ok(c["erro_e_656"], "reconhece o cStat 656 no ultimo_erro")
    igual(c["ult_nsu"], cpm.NSU_ZERO, "com o acervo em zero")
    igual(c["documentos_no_acervo"], 0, "e nenhum documento no acervo")
    ok(not c["nsu_no_texto_do_erro"],
       "e confirma que o ultNSU NÃO está no texto do erro")
    ok(not c["ja_migrado"], "ainda não migrado")

secao("Migração do estado histórico")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    res = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                               fonte_do_valor=FONTE)
    igual(res.resultado, r2.MIGRADO, "migrou")

    cp = ler(raiz)
    igual(cp.ult_nsu, cpm.NSU_ZERO, "checkpoint_acervo continua ZERO")
    igual(cp.nsu_observado_sefaz, "000000000001361", "observado = 1361")
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_DIVERGENCIA,
          "estado = DIVERGENCIA_EXTERNA")
    igual(cp.diagnostico, cpm.POSSIVEL_CONSUMIDOR_EXTERNO,
          "diagnóstico = POSSIVEL_CONSUMIDOR_EXTERNO")
    igual(cp.cobertura_anterior, cpm.COBERTURA_DESCONHECIDA,
          "cobertura = DESCONHECIDA")
    igual(cp.distancia_para_sefaz, 1361, "distância calculada")
    igual(cp.migracao_r2["origem"], r2.ORIGEM, "origem MIGRACAO_R2_DE_EVENTO_HISTORICO")
    igual(cp.migracao_r2["fonte_do_valor"], FONTE, "com a fonte do valor gravada")
    ok(cp.migracao_r2["evidencia_em_disco"]["ultimo_erro"],
       "e a evidência que sustentou a reconstrução")
    ok("NÃO estava no disco" in cp.migracao_r2["aviso"],
       "com o aviso de que o ultNSU não veio do disco")

secao("O que a migração NUNCA faz")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                         fonte_do_valor=FONTE)
    cp = ler(raiz)
    igual(cp.ult_nsu, cpm.NSU_ZERO, "não altera ult_nsu")
    igual(cp.marco_cobertura, {}, "não cria INICIO_COBERTURA_DFE")
    igual(cp.origem_ult_nsu, cpm.ORIGEM_INDEFINIDA,
          "não inventa origem para o ult_nsu, que não veio de lugar nenhum")

secao("Idempotência")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    arq = RepositorioCheckpoint(raiz).caminho(EMPRESA, cpm.NFE_DISTRIBUICAO, PRODUCAO)
    r1 = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                              fonte_do_valor=FONTE)
    b1 = arq.read_bytes()
    r_2 = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                               fonte_do_valor=FONTE)
    r_3 = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                               fonte_do_valor=FONTE)
    igual(r1.resultado, r2.MIGRADO, "a primeira migra")
    igual(r_2.resultado, r2.JA_MIGRADO, "a segunda não")
    igual(r_3.resultado, r2.JA_MIGRADO, "a terceira também não")
    igual(arq.read_bytes(), b1, "e o arquivo fica byte a byte igual")
    igual(len([x for x in aud.abrir(raiz, EMPRESA).ler()
               if x.get("ato") == aud.CONSULTA_RECONSTRUIDA]), 1,
          "e só UM registro reconstruído é criado")

secao("Recusa: sem evidência, sem migração")
casos = [
    ("sem checkpoint em disco", None, r2.SEM_CHECKPOINT),
    ("erro que não é 656", "cStat 137: Nenhum documento localizado",
     r2.SEM_EVIDENCIA_656),
    ("erro vazio", "", r2.SEM_EVIDENCIA_656),
]
for rotulo, erro, esperado in casos:
    with apoio.raiz_temporaria("i4b_") as raiz:
        if erro is not None:
            plantar_656(raiz, erro=erro)
        r = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                                 fonte_do_valor=FONTE)
        igual(r.resultado, esperado, f"{rotulo}: recusado")
        igual(ler(raiz).estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
              f"{rotulo}: estado intacto")

secao("Recusa: fonte do valor é obrigatória")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    for fonte in ("", "   ", None):
        r = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                                 fonte_do_valor=fonte)
        igual(r.resultado, r2.NSU_INVALIDO, f"fonte {fonte!r} é recusada")
    igual(ler(raiz).nsu_observado_sefaz, "", "nada foi gravado")

secao("Recusa: NSU inválido ou que não diverge")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    for rotulo, nsu, esperado in (("zero", "0", r2.NSU_INVALIDO),
                                  ("vazio", "", r2.NSU_INVALIDO),
                                  ("texto", "abc", r2.NSU_INVALIDO)):
        igual(r2.migrar_estado_656(raiz, EMPRESA, nsu_observado=nsu,
                                   fonte_do_valor=FONTE).resultado, esperado,
              f"NSU {rotulo}: recusado")

with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz, ult_nsu="000000000002000")
    r = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                             fonte_do_valor=FONTE)
    igual(r.resultado, r2.ACERVO_NAO_VAZIO,
          "com acervo já avançado, o estado não se aplica")

secao("Recusa: já houve incorporação de documentos")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    ac = acv.abrir(raiz, EMPRESA, servico="nfe_distribuicao", ambiente="producao")
    ac.preservar(T.bruto(T.xml_nfe(T.CHAVE_1), "000000000000001"))
    r = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                             fonte_do_valor=FONTE)
    igual(r.resultado, r2.ACERVO_NAO_VAZIO, "com documento no acervo, recusa")
    igual(ler(raiz).estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
          "e o estado fica intacto")

secao("Ambiente e serviço não se misturam")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz, ambiente=PRODUCAO)
    r = r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                             fonte_do_valor=FONTE, ambiente=HOMOLOGACAO)
    igual(r.resultado, r2.SEM_CHECKPOINT,
          "homologação não herda a evidência de produção")
    r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                         fonte_do_valor=FONTE, ambiente=PRODUCAO)
    igual(ler(raiz, ambiente=HOMOLOGACAO).estado_sincronismo,
          cpm.SINCRONISMO_SEM_INFORMACAO, "e continua sem estado depois")

# ══════════════════════════════════════════════════════════════════════════
secao("[!] A empresa com esse estado NUNCA é classificada como nova")
# Reproduz a classificação do inventário da ING 4B.
PRONTA, SEM_HIST, DIVERG = "PRONTA_PARA_MIGRACAO", "SEM_HISTORICO_LEGADO", "DIVERGENCIA_EXTERNA"


def classificar(raiz, cnpj) -> str:
    """Mesma ordem de decisão do inventário: divergência ANTES de 'sem histórico'."""
    cp = ler(raiz, cnpj)
    prev = mig.prever(raiz, cnpj)
    if cp.estado_sincronismo == cpm.SINCRONISMO_DIVERGENCIA:
        return DIVERG
    if prev.get("avancaria_para"):
        return PRONTA
    return SEM_HIST


with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    igual(classificar(raiz, EMPRESA), SEM_HIST,
          "ANTES da migração ela parecia empresa nova — era o defeito")

    r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                         fonte_do_valor=FONTE)
    igual(classificar(raiz, EMPRESA), DIVERG,
          "DEPOIS da migração ela é DIVERGENCIA_EXTERNA")

    cp = ler(raiz)
    ok(cp.ha_divergencia_externa, "a propriedade de divergência responde True")
    ok(cp.alerta_administrativo, "há alerta administrativo para a tela")
    ok(cp.cobertura_anterior != cpm.COBERTURA_COMPLETA_DESDE_ZERO,
       "e a cobertura NÃO afirma histórico completo")

secao("O estado sobrevive a releitura e ao backup")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                         fonte_do_valor=FONTE)
    bruto = json.loads(RepositorioCheckpoint(raiz).caminho(
        EMPRESA, cpm.NFE_DISTRIBUICAO, PRODUCAO).read_text("utf-8"))
    for campo in ("nsu_observado_sefaz", "estado_sincronismo", "diagnostico",
                  "cobertura_anterior", "migracao_r2"):
        ok(campo in bruto, f"'{campo}' está NO ARQUIVO, não só em memória")
    igual(bruto["ult_nsu"], cpm.NSU_ZERO, "e o ult_nsu gravado continua zero")

# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
secao("Materializar EM_SINCRONIA a partir de CONSULTA ja auditada")
# A regra do EM_SINCRONIA nasceu depois de a 00.***-53 ter sido consultada. A
# resposta (137, ultNSU == maxNSU == 216) esta na trilha; o estado ficou
# SEM_INFORMACAO porque o codigo que o grava ainda nao existia.


def plantar_consulta(raiz, cnpj, cstat, ult, mx, resultado, ok_transp=True,
                     erro=None):
    aud.abrir(raiz, cnpj).registrar_consulta({
        "inicio": "2026-08-16T15:54:26-03:00", "fim": "2026-08-16T15:54:26-03:00",
        "servico": cpm.NFE_DISTRIBUICAO, "ambiente": "producao",
        "tipo_consulta": "distNSU", "nsu_enviado": ult,
        "endpoint": "www1.nfe.fazenda.gov.br", "transporte_ok": ok_transp,
        "cstat": cstat, "xmotivo": "x", "ult_nsu": ult, "max_nsu": mx,
        "doczip": 0, "resultado": resultado, "erro_tecnico": erro})


with apoio.raiz_temporaria("i4b_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(EMPRESA, cpm.NFE_DISTRIBUICAO, PRODUCAO)
    cp.ult_nsu = "000000000000216"; cp.max_nsu = "000000000000216"
    cp.status = cpm.EM_DIA; cp.origem_ult_nsu = cpm.ORIGEM_LEGADO
    cp.cobertura_anterior = cpm.COBERTURA_ACERVO_LEGADO
    repo.salvar(cp)
    plantar_consulta(raiz, EMPRESA, "137", "000000000000216",
                     "000000000000216", "NENHUM_DOCUMENTO")
    antes_cob = ler(raiz).cobertura_anterior

    r = r2.materializar_sincronia_de_consulta(raiz, EMPRESA)
    igual(r.resultado, r2.SINCRONIA_MATERIALIZADA, "materializou")
    cp = ler(raiz)
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_EM_DIA, "estado = EM_SINCRONIA")
    igual(cp.ult_nsu, "000000000000216", "checkpoint NAO mudou")
    igual(cp.cobertura_anterior, antes_cob, "cobertura NAO mudou")
    igual(cp.origem_ult_nsu, cpm.ORIGEM_LEGADO, "origem preservada")
    ok(cp.sincronia_confirmada_em, "com o carimbo da confirmacao")
    s = cp.migracao_r2["sincronia"]
    igual(s["origem"], r2.ORIGEM_SINCRONIA, "com a origem registrada")
    igual(s["cstat"], "137", "e o cStat da consulta auditada")
    ok("nenhuma chamada nova" in s["aviso"], "e o aviso de que nao houve rede")

    igual(len(aud.abrir(raiz, EMPRESA).consultas()), 1,
          "NENHUMA consulta nova foi criada")
    igual(len([x for x in aud.abrir(raiz, EMPRESA).ler()
               if x.get("ato") == aud.CONSULTA_RECONSTRUIDA]), 0,
          "nem uma reconstruida — a consulta real ja estava registrada")

    arq = repo.caminho(EMPRESA, cpm.NFE_DISTRIBUICAO, PRODUCAO)
    b1 = arq.read_bytes()
    r_2 = r2.materializar_sincronia_de_consulta(raiz, EMPRESA)
    igual(r_2.resultado, r2.JA_EM_SINCRONIA, "segunda execucao e idempotente")
    igual(arq.read_bytes(), b1, "e o arquivo fica byte a byte igual")

secao("Materializacao recusa o que nao prova sincronia")
casos = [
    ("656", "656", "000000000001361", "000000000000000", "CONSUMO_INDEVIDO",
     True, None, r2.SEM_CONSULTA_UTIL),
    ("falha de transporte", None, None, None, "FALHA_TRANSPORTE", False,
     "timeout", r2.SEM_CONSULTA_UTIL),
    ("ultNSU < maxNSU", "137", "000000000000216", "000000000000900",
     "NENHUM_DOCUMENTO", True, None, r2.NAO_FECHA_EM_DIA),
]
for rotulo, cstat, ult, mx, resultado, transp, erro, esperado in casos:
    with apoio.raiz_temporaria("i4b_") as raiz:
        repo = RepositorioCheckpoint(raiz)
        cp = repo.carregar(EMPRESA, cpm.NFE_DISTRIBUICAO, PRODUCAO)
        cp.ult_nsu = "000000000000216"; repo.salvar(cp)
        plantar_consulta(raiz, EMPRESA, cstat, ult or "000000000000216",
                         mx or "000000000000216", resultado, transp, erro)
        r = r2.materializar_sincronia_de_consulta(raiz, EMPRESA)
        igual(r.resultado, esperado, f"{rotulo}: recusado")
        igual(ler(raiz).estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
              f"{rotulo}: estado intacto")

with apoio.raiz_temporaria("i4b_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    repo.salvar(repo.carregar(EMPRESA, cpm.NFE_DISTRIBUICAO, PRODUCAO))
    igual(r2.materializar_sincronia_de_consulta(raiz, EMPRESA).resultado,
          r2.SEM_CONSULTA_UTIL, "sem trilha, nao materializa")

secao("Materializacao nao apaga divergencia externa")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                         fonte_do_valor=FONTE)
    plantar_consulta(raiz, EMPRESA, "137", cpm.NSU_ZERO, cpm.NSU_ZERO,
                     "NENHUM_DOCUMENTO")
    r = r2.materializar_sincronia_de_consulta(raiz, EMPRESA)
    igual(r.resultado, r2.NAO_FECHA_EM_DIA,
          "uma consulta que fecha em zero nao apaga a divergencia de 1361")
    igual(ler(raiz).estado_sincronismo, cpm.SINCRONISMO_DIVERGENCIA,
          "a divergencia continua")

secao("Auditoria reconstruída é distinguível de registro real")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                         fonte_do_valor=FONTE)
    tr = aud.abrir(raiz, EMPRESA)
    igual(len(tr.consultas()), 0,
          "não conta como consulta REAL — o contador de chamadas não infla")
    igual(len(tr.consultas(incluir_reconstruidas=True)), 1,
          "mas aparece quando se pede o histórico completo")

    rec = [x for x in tr.ler() if x.get("ato") == aud.CONSULTA_RECONSTRUIDA][0]
    igual(rec["ato"], aud.CONSULTA_RECONSTRUIDA, "o ato tem nome próprio")
    ok(rec["reconstruido"] is True, "marcado como reconstruído")
    ok(rec["fonte_da_reconstrucao"], "com a fonte declarada")
    ok("NÃO produzido no momento da chamada" in rec["aviso"],
       "e o aviso diz que não foi produzido na hora")
    igual(rec["cstat"], "656", "traz o cStat, que é comprovado")
    igual(rec["ult_nsu"], "000000000001361", "e o ultNSU declarado")
    igual(rec["doczip"], 0, "zero documentos associados")

secao("O que não se sabe fica ausente, não inventado")
with apoio.raiz_temporaria("i4b_") as raiz:
    plantar_656(raiz)
    r2.migrar_estado_656(raiz, EMPRESA, nsu_observado="000000000001361",
                         fonte_do_valor=FONTE)
    rec = [x for x in aud.abrir(raiz, EMPRESA).ler()
           if x.get("ato") == aud.CONSULTA_RECONSTRUIDA][0]
    for campo in ("fim", "max_nsu", "endpoint", "duracao_s"):
        ok(campo not in rec,
           f"'{campo}' não foi preservado, então não aparece — sem precisão falsa")
    ok("inicio" in rec, "o início existe: veio de `ultima_consulta` do checkpoint")

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
print("ING 4B: todos os testes passaram.")
