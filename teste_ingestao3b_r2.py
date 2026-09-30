#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da ING 3B-R2 — checkpoint local × observado, e trilha de CONSULTA.

    python teste_ingestao3b_r2.py

O QUE ESTA SUÍTE PROVA
    Que o FISCALE nunca confunde **o que tem** com **o que a SEFAZ diz que
    existe**, e que toda tentativa de aquisição deixa registro — inclusive as
    que não trazem documento nenhum.

O CASO REAL QUE ORIGINOU TUDO
    13/08/2026, MONTE, produção: `cStat 656`, `ultNSU` devolvido = 1361,
    checkpoint local = 0, zero documentos. Se `ult_nsu` tivesse absorvido 1361,
    o sistema afirmaria possuir 1.361 documentos que nunca viu — e os pularia
    para sempre, porque a Distribuição DF-e não deixa voltar.

REDE BLOQUEADA
    `socket.socket.connect` levanta durante toda a suíte, conferido no fim.
"""
from __future__ import annotations

import base64
import gzip
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
from ingestao import pipeline as pipe              # noqa: E402
from ingestao.ambiente import PRODUCAO             # noqa: E402
from ingestao.checkpoint import Checkpoint, RepositorioCheckpoint  # noqa: E402
from ingestao.conectores import nfe_dfe as N       # noqa: E402
from ingestao.distribuicao import ErroTransitorio, RespostaInvalida  # noqa: E402

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


# ── fixtures de resposta ────────────────────────────────────────────────────
def zipar(xml: bytes) -> str:
    return base64.b64encode(gzip.compress(xml)).decode("ascii")


def doczip(nsu: str, schema: str, xml: bytes) -> str:
    return f'<docZip NSU="{nsu}" schema="{schema}">{zipar(xml)}</docZip>'


def resposta(cstat="138", motivo="Documento localizado", ult="000000000000002",
             maximo="000000000000010", docs=()) -> bytes:
    lote = f"<loteDistDFeInt>{''.join(docs)}</loteDistDFeInt>" if docs else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<retDistDFeInt xmlns="{N.NS_NFE}" versao="1.01">
  <tpAmb>1</tpAmb><cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo>
  <dhResp>2026-08-13T21:26:57-03:00</dhResp>
  <ultNSU>{ult}</ultNSU><maxNSU>{maximo}</maxNSU>{lote}
</retDistDFeInt></soap:Body></soap:Envelope>""".encode("utf-8")


# A resposta REAL de 13/08/2026, com o texto que a SEFAZ devolveu.
RESPOSTA_656_REAL = resposta(
    cstat="656",
    motivo=("Rejeicao: Consumo Indevido (Deve ser utilizado o ultNSU nas "
            "solicitacoes subsequentes. Tente apos 1 hora)"),
    ult="000000000001361", maximo="000000000000000")


class TransporteFalso:
    def __init__(self, respostas):
        self._fila = list(respostas)
        self.enviados: list[bytes] = []

    def enviar(self, url: str, corpo: bytes) -> bytes:
        self.enviados.append(corpo)
        if not self._fila:
            return resposta("137", "Nenhum documento localizado",
                            "000000000000010", "000000000000010")
        item = self._fila.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fonte(transporte):
    return N.FonteNFeDistribuicaoDFe(identidade=T.EMPRESA_A,
                                     transporte=transporte,
                                     ambiente=PRODUCAO, cuf="26")


def cp_novo(**kw) -> Checkpoint:
    base = dict(identidade=T.EMPRESA_A, servico=cpm.NFE_DISTRIBUICAO,
                ambiente="producao")
    base.update(kw)
    return Checkpoint(**base)


# ══════════════════════════════════════════════════════════════════════════
secao("Os dois números têm nomes diferentes")
cp = cp_novo()
igual(cp.ult_nsu, cpm.NSU_ZERO, "checkpoint_acervo nasce em zero")
igual(cp.nsu_observado_sefaz, "", "checkpoint_sefaz_observado nasce vazio")
igual(cp.estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
      "e o estado é SEM_INFORMACAO, não 'em dia'")
ok(not cp.ha_divergencia_externa, "sem notícia, não há divergência declarada")
igual(cp.cobertura_anterior, cpm.COBERTURA_COMPLETA_DESDE_ZERO,
      "cobertura nasce COMPLETA_DESDE_ZERO (nunca houve salto)")

secao("Observar a SEFAZ NUNCA move o acervo")
cp = cp_novo()
antes = cp.ult_nsu
divergiu = cp.registrar_observacao_sefaz("000000000001361", cstat="656")
ok(divergiu, "1361 > 0 é reconhecido como divergência")
igual(cp.ult_nsu, antes, "o checkpoint do ACERVO continua exatamente onde estava")
igual(cp.nsu_observado_sefaz, "000000000001361", "e o observado guarda 1361")
igual(cp.estado_sincronismo, cpm.SINCRONISMO_DIVERGENCIA, "estado = DIVERGENCIA_EXTERNA")
igual(cp.nsu_observado_origem, "656", "com o cStat que originou a informação")
ok(cp.nsu_observado_em, "e o carimbo de quando foi observado")
igual(cp.distancia_para_sefaz, 1361, "a distância é calculada, não guardada")

secao("Divergência derruba a promessa sobre o passado")
igual(cp.cobertura_anterior, cpm.COBERTURA_DESCONHECIDA,
      "a cobertura anterior passa a DESCONHECIDA — não se promete o que não se baixou")

secao("Observação IGUAL ou ATRÁS não é divergência")
cp2 = cp_novo(ult_nsu="000000000001361")
ok(not cp2.registrar_observacao_sefaz("000000000001361", cstat="137"),
   "SEFAZ na mesma posição não é divergência")
igual(cp2.estado_sincronismo, cpm.SINCRONISMO_EM_DIA, "e o estado vira EM_SINCRONIA")
igual(cp2.cobertura_anterior, cpm.COBERTURA_COMPLETA_DESDE_ZERO,
      "a cobertura continua íntegra")
cp3 = cp_novo(ult_nsu="000000000002000")
ok(not cp3.registrar_observacao_sefaz("000000000001361", cstat="137"),
   "SEFAZ atrás do nosso acervo também não é divergência")

secao("Observação vazia ou zero é ignorada")
cp4 = cp_novo(ult_nsu="000000000000005")
ok(not cp4.registrar_observacao_sefaz("", cstat="137"), "vazio não vira observação")
ok(not cp4.registrar_observacao_sefaz("0", cstat="137"), "zero também não")
igual(cp4.nsu_observado_sefaz, "", "e nada foi guardado")
igual(cp4.ult_nsu, "000000000000005", "nem o acervo mexeu")

secao("Diagnóstico é hipótese, e é separado do estado")
cp = cp_novo()
cp.registrar_observacao_sefaz("000000000001361", cstat="656")
igual(cp.diagnostico, cpm.DIAGNOSTICO_NENHUM,
      "observar a divergência NÃO diagnostica sozinho")
cp.marcar_possivel_consumidor_externo("cStat 656 devolveu ultNSU à frente")
igual(cp.diagnostico, cpm.POSSIVEL_CONSUMIDOR_EXTERNO, "o diagnóstico é explícito")
alerta = cp.alerta_administrativo
ok("Ambiente Nacional informa uma posição de NSU superior" in alerta,
   "o alerta administrativo descreve o fato")
ok("Outro sistema pode estar consultando" in alerta, "e levanta a hipótese")
for nome in ("Domínio", "Dominio", "ERP", "Contmatic", "Alterdata"):
    ok(nome not in alerta, f"sem acusar '{nome}' — não se conclui qual aplicação é")

secao("Marco INICIO_COBERTURA_DFE")
cp = cp_novo()
cp.registrar_observacao_sefaz("000000000001361", cstat="656")
marco = cp.marcar_inicio_cobertura("000000000001361", cstat="656",
                                   motivo="sincronizar da posição corrente")
igual(marco["marco"], "INICIO_COBERTURA_DFE", "o marco tem nome próprio")
for campo in ("empresa", "servico", "ambiente", "em", "checkpoint_local_anterior",
              "nsu_informado_sefaz", "cstat_de_origem", "motivo", "aviso"):
    ok(campo in marco and marco[campo] not in (None, ""), f"o marco traz '{campo}'")
igual(marco["checkpoint_local_anterior"], cpm.NSU_ZERO,
      "registra de onde o acervo estava saindo")
igual(marco["nsu_informado_sefaz"], "000000000001361", "e para onde a SEFAZ apontou")
ok("podem NÃO existir no acervo" in marco["aviso"],
   "e diz, em texto, que o passado pode não estar aqui")
ok(T.EMPRESA_A not in str(marco), "a empresa vai MASCARADA no marco")
igual(cp.cobertura_anterior, cpm.COBERTURA_A_PARTIR_DE_MARCO,
      "a cobertura passa a valer A_PARTIR_DE_MARCO")
igual(cp.ult_nsu, cpm.NSU_ZERO, "e marcar o início NÃO move o acervo tampouco")

secao("O estado sobrevive à gravação e à releitura")
with apoio.raiz_temporaria("r2_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    cp = cp_novo()
    cp.registrar_observacao_sefaz("000000000001361", cstat="656")
    cp.marcar_possivel_consumidor_externo("teste")
    cp.marcar_inicio_cobertura("000000000001361", cstat="656")
    repo.salvar(cp)

    lido = repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao")
    igual(lido.ult_nsu, cpm.NSU_ZERO, "o acervo continua em zero depois de reler")
    igual(lido.nsu_observado_sefaz, "000000000001361", "o observado sobreviveu")
    igual(lido.estado_sincronismo, cpm.SINCRONISMO_DIVERGENCIA, "o estado também")
    igual(lido.diagnostico, cpm.POSSIVEL_CONSUMIDOR_EXTERNO, "o diagnóstico também")
    igual(lido.cobertura_anterior, cpm.COBERTURA_A_PARTIR_DE_MARCO, "a cobertura também")
    igual(lido.marco_cobertura.get("marco"), "INICIO_COBERTURA_DFE", "e o marco")

secao("Checkpoint antigo (sem os campos novos) continua legível")
with apoio.raiz_temporaria("r2_") as raiz:
    import json
    repo = RepositorioCheckpoint(raiz)
    caminho = repo.caminho(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao", criar=True)
    caminho.write_text(json.dumps({
        "identidade": T.EMPRESA_A, "servico": cpm.NFE_DISTRIBUICAO,
        "ambiente": "producao", "ult_nsu": "000000000000042",
        "max_nsu": "000000000000042", "status": "EM_DIA", "versao": 1,
    }), encoding="utf-8")
    velho = repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao")
    igual(velho.ult_nsu, "000000000000042", "o NSU antigo é preservado")
    igual(velho.estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
          "e os campos novos assumem o padrão sem quebrar nada")
    igual(velho.cobertura_anterior, cpm.COBERTURA_COMPLETA_DESDE_ZERO,
          "cobertura padrão para instalação que nunca divergiu")

# ══════════════════════════════════════════════════════════════════════════
secao("Carregar um checkpoint NÃO liga o objeto ao dicionário de origem")
# Descoberto simulando o estado da MONTE: `de_json` entregava a MESMA lista
# `observacoes` do JSON de origem. Um `append` depois alterava o dicionário de
# quem chamou, à distância — o dado "lido do disco" mudava sozinho em memória.
import json as _json
_bruto = {"identidade": T.EMPRESA_A, "servico": cpm.NFE_DISTRIBUICAO,
          "ambiente": "producao", "ult_nsu": "000000000000007",
          "observacoes": ["nota original"], "marco_cobertura": {"a": 1}}
_copia = _json.loads(_json.dumps(_bruto))
_cp = Checkpoint.de_json(_bruto)
_cp.observacoes.append("acrescentada depois")
_cp.marco_cobertura["b"] = 2
igual(_bruto, _copia, "o dicionário de origem continua exatamente como estava")
igual(len(_cp.observacoes), 2, "e o checkpoint tem a sua própria lista")

secao("Trilha de CONSULTA — registra mesmo sem documento")
with apoio.raiz_temporaria("r2_") as raiz:
    res = pipe.ingerir(raiz, T.EMPRESA_A, fonte(TransporteFalso([RESPOSTA_656_REAL])),
                       ambiente="producao", usar_trava=False)
    igual(res.consultas_registradas, 1, "a tentativa foi registrada")
    consultas = aud.abrir(raiz, T.EMPRESA_A).consultas()
    igual(len(consultas), 1, "há uma linha CONSULTA na trilha")
    c = consultas[0]
    for campo in aud.CAMPOS_CONSULTA:
        ok(campo in c, f"a linha traz '{campo}'")
    igual(c["cstat"], "656", "com o cStat")
    igual(c["tipo_consulta"], "distNSU", "o tipo de consulta")
    igual(c["nsu_enviado"], cpm.NSU_ZERO, "o NSU que foi enviado")
    igual(c["ult_nsu"], "000000000001361", "o ultNSU devolvido")
    igual(c["endpoint"], "www1.nfe.fazenda.gov.br", "o endpoint lógico (host)")
    ok(c["transporte_ok"], "o transporte funcionou")
    ok(c["divergencia_externa"], "e a divergência foi sinalizada")
    ok("Consumo Indevido" in c["xmotivo"], "o xMotivo sanitizado")
    igual(c["doczip"], 0, "zero docZip")
    ok(c["duracao_s"] is not None, "e a duração")

secao("A trilha de CONSULTA vale para todos os desfechos")
casos = [
    ("137 sem documentos", TransporteFalso([resposta("137", "Nenhum documento localizado",
                                                     "000000000000010", "000000000000010")]),
     None, "NENHUM_DOCUMENTO", True),
    # `maximo` igual ao `ult` para o motor parar em UM lote. Sem isso ele
    # consulta de novo (corretamente) e a trilha ganha DUAS linhas — o que
    # também está certo, e foi o que este teste descobriu na primeira versão.
    ("138 com documento", TransporteFalso([resposta(
        ult="000000000000002", maximo="000000000000002",
        docs=[doczip("000000000000002", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1))])]),
     None, "DOCUMENTOS_ENCONTRADOS", True),
    ("656 consumo indevido", TransporteFalso([RESPOSTA_656_REAL]),
     None, "CONSUMO_INDEVIDO", True),
    ("timeout de leitura", TransporteFalso([ErroTransitorio("tempo esgotado ao LER")]),
     None, "FALHA_TRANSPORTE", False),
    ("TLS recusado", TransporteFalso([N.CertificadoRecusado("TLS recusado")]),
     None, "FALHA_TRANSPORTE", False),
    ("SOAP inválido", TransporteFalso([b"<nao><fecha>"]),
     None, "RESPOSTA_INVALIDA", True),
    ("HTTP inesperado", TransporteFalso([ErroTransitorio("HTTP 503 do servico")]),
     None, "FALHA_TRANSPORTE", False),
]
for rotulo, transporte, _x, esperado, transporte_ok in casos:
    with apoio.raiz_temporaria("r2_") as raiz:
        pipe.ingerir(raiz, T.EMPRESA_A, fonte(transporte), ambiente="producao",
                     usar_trava=False)
        cs = aud.abrir(raiz, T.EMPRESA_A).consultas()
        if len(cs) != 1:
            ok(False, f"{rotulo}: esperava 1 linha CONSULTA, obtive {len(cs)}")
            continue
        igual(cs[0]["resultado"], esperado, f"{rotulo}: resultado registrado")
        igual(bool(cs[0]["transporte_ok"]), transporte_ok,
              f"{rotulo}: sucesso de transporte registrado")
        if not transporte_ok:
            ok(cs[0].get("erro_tecnico"), f"{rotulo}: com o erro técnico preservado")
            ok(cs[0].get("erro_classe"), f"{rotulo}: e a classe do erro")

secao("Cada consulta do laço vira UMA linha na trilha")
with apoio.raiz_temporaria("r2_") as raiz:
    # Dois lotes + a consulta final que descobre que acabou = três tentativas.
    pipe.ingerir(raiz, T.EMPRESA_A, fonte(TransporteFalso([
        resposta(ult="000000000000001", maximo="000000000000009", docs=[
            doczip("000000000000001", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1))]),
        resposta(ult="000000000000002", maximo="000000000000009", docs=[
            doczip("000000000000002", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_2))]),
    ])), ambiente="producao", usar_trava=False)
    cs = aud.abrir(raiz, T.EMPRESA_A).consultas()
    igual(len(cs), 3, "três chamadas ao serviço, três linhas de CONSULTA")
    igual([c["nsu_enviado"] for c in cs],
          ["000000000000000", "000000000000001", "000000000000002"],
          "cada linha registra o NSU que ELA enviou")
    igual(cs[-1]["resultado"], "NENHUM_DOCUMENTO", "a última encerrou o laço")

secao("A trilha de CONSULTA não vaza segredo nem documento")
with apoio.raiz_temporaria("r2_") as raiz:
    pipe.ingerir(raiz, T.EMPRESA_A, fonte(TransporteFalso([resposta(docs=[
        doczip("000000000000002", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1))])])),
        ambiente="producao", usar_trava=False)
    texto = aud.abrir(raiz, T.EMPRESA_A).caminho().read_text("utf-8")
    ok(T.EMPRESA_A not in texto, "o CNPJ vai mascarado")
    ok("PRODUTO 1" not in texto, "o conteúdo do XML não aparece")
    ok("nfeProc" not in texto, "nem a estrutura do documento")
    ok("https://" not in texto, "o endpoint é o host, não a URL inteira")

# ══════════════════════════════════════════════════════════════════════════
secao("Ponta a ponta: o caso real da MONTE, reproduzido")
with apoio.raiz_temporaria("r2_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    res = pipe.ingerir(raiz, T.EMPRESA_A, fonte(TransporteFalso([RESPOSTA_656_REAL])),
                       ambiente="producao", usar_trava=False)
    ok(not res.sucesso, "a execução não teve sucesso")
    ok(res.divergencia_externa, "e a divergência externa foi detectada")

    cp = repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao")
    igual(cp.ult_nsu, cpm.NSU_ZERO, "checkpoint_acervo = 0")
    igual(cp.nsu_observado_sefaz, "000000000001361", "checkpoint_sefaz_observado = 1361")
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_DIVERGENCIA,
          "estado_sincronismo = DIVERGENCIA_EXTERNA")
    igual(cp.diagnostico, cpm.POSSIVEL_CONSUMIDOR_EXTERNO,
          "diagnostico = POSSIVEL_CONSUMIDOR_EXTERNO")
    igual(cp.cobertura_anterior, cpm.COBERTURA_DESCONHECIDA,
          "cobertura_anterior = DESCONHECIDA")
    igual(len(acv.abrir(raiz, T.EMPRESA_A).listar()), 0, "nada foi persistido")
    ok(cp.alerta_administrativo, "há alerta administrativo para a tela")

    r = cp.resumo()
    igual(r["checkpoint_acervo"], cpm.NSU_ZERO, "o resumo separa o local")
    igual(r["checkpoint_sefaz_observado"], "000000000001361", "do observado")
    igual(r["distancia_para_sefaz"], 1361, "e mostra a distância")

secao("Documento que CHEGA move o acervo — o caminho normal não regrediu")
with apoio.raiz_temporaria("r2_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    pipe.ingerir(raiz, T.EMPRESA_A, fonte(TransporteFalso([resposta(
        ult="000000000000002", maximo="000000000000002",
        docs=[doczip("000000000000002", "procNFe_v4.00.xsd", T.xml_nfe(T.CHAVE_1))])])),
        ambiente="producao", usar_trava=False)
    cp = repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao")
    igual(cp.ult_nsu, "000000000000002", "com documento preservado, o acervo avança")
    # Esta asserção dizia SEM_INFORMACAO até 16/08/2026. Era o defeito: a
    # resposta fechou `ultNSU == maxNSU` e mesmo assim o estado ficava como se
    # ninguém tivesse perguntado. Corrigida para o comportamento certo.
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_EM_DIA,
          "e a resposta atual confirma EM_SINCRONIA")
    ok(not cp.ha_divergencia_externa, "sem divergência a declarar")
    igual(cp.cobertura_anterior, cpm.COBERTURA_COMPLETA_DESDE_ZERO,
          "a cobertura continua íntegra")

# ══════════════════════════════════════════════════════════════════════════
secao("EM_SINCRONIA — 'checkpoint nao mudou' nao e 'estado desconhecido'")
# O caso real: 00.***-53 estava em 216, a SEFAZ respondeu 137 com
# ultNSU == maxNSU == 216, e o estado ficava SEM_INFORMACAO — como se ninguem
# tivesse perguntado. Perguntamos, e a resposta foi "nao ha mais nada".
with apoio.raiz_temporaria("r2_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    cp = cp_novo(ult_nsu="000000000000216", max_nsu="000000000000216",
                 status=cpm.OK, origem_ult_nsu=cpm.ORIGEM_LEGADO,
                 cobertura_anterior=cpm.COBERTURA_ACERVO_LEGADO)
    repo.salvar(cp)
    igual(repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao").estado_sincronismo,
          cpm.SINCRONISMO_SEM_INFORMACAO, "antes da consulta: SEM_INFORMACAO")

    tr = TransporteFalso([resposta("137", "Nenhum documento localizado",
                                   "000000000000216", "000000000000216")])
    res = pipe.ingerir(raiz, T.EMPRESA_A, fonte(tr), ambiente="producao",
                       usar_trava=False)
    cp = repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao")
    igual(cp.ult_nsu, "000000000000216", "o checkpoint NAO mudou")
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_EM_DIA,
          "mas o estado agora e EM_SINCRONIA")
    ok(cp.sincronia_confirmada_em, "com o carimbo de quando foi confirmado")
    igual(cp.origem_ult_nsu, cpm.ORIGEM_LEGADO, "a origem e preservada")
    igual(cp.cobertura_anterior, cpm.COBERTURA_ACERVO_LEGADO,
          "e a cobertura tambem")
    igual(len(acv.abrir(raiz, T.EMPRESA_A).listar()), 0, "sem documento nenhum")

secao("EM_SINCRONIA tambem depois de um 138 que fecha em dia")
with apoio.raiz_temporaria("r2_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    tr = TransporteFalso([resposta(ult="000000000000002", maximo="000000000000002",
                                   docs=[doczip("000000000000002",
                                                "procNFe_v4.00.xsd",
                                                T.xml_nfe(T.CHAVE_1))])])
    pipe.ingerir(raiz, T.EMPRESA_A, fonte(tr), ambiente="producao", usar_trava=False)
    cp = repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao")
    igual(cp.ult_nsu, "000000000000002", "o checkpoint avancou")
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_EM_DIA, "e o estado e EM_SINCRONIA")

secao("O que NAO marca em sincronia")
def estado_apos(raiz, transporte):
    pipe.ingerir(raiz, T.EMPRESA_A, fonte(transporte), ambiente="producao",
                 usar_trava=False)
    return RepositorioCheckpoint(raiz).carregar(
        T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao")

with apoio.raiz_temporaria("r2_") as raiz:
    cp = estado_apos(raiz, TransporteFalso([RESPOSTA_656_REAL]))
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_DIVERGENCIA,
          "656 nao marca em sincronia — marca divergencia")

with apoio.raiz_temporaria("r2_") as raiz:
    cp = estado_apos(raiz, TransporteFalso([N.CertificadoRecusado("TLS recusado")]))
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
          "erro de TLS nao marca em sincronia")

with apoio.raiz_temporaria("r2_") as raiz:
    cp = estado_apos(raiz, TransporteFalso([ErroTransitorio("HTTP 503")]))
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
          "erro HTTP nao marca em sincronia")

with apoio.raiz_temporaria("r2_") as raiz:
    cp = estado_apos(raiz, TransporteFalso([b"<nao><fecha>"]))
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
          "SOAP invalido nao marca em sincronia")

with apoio.raiz_temporaria("r2_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    repo.salvar(cp_novo(ult_nsu="000000000000100"))
    tr = TransporteFalso([resposta("137", "Nenhum documento localizado",
                                   "000000000000100", "000000000000900")])
    pipe.ingerir(raiz, T.EMPRESA_A, fonte(tr), ambiente="producao", usar_trava=False)
    cp = repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao")
    igual(cp.estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO,
          "ultNSU < maxNSU nao marca em sincronia")

secao("Guarda 2: nao se declara sincronia sobre posicao que nao alcancamos")
cp = cp_novo(ult_nsu="000000000000100")
ok(not cp.registrar_sincronia_confirmada("000000000000900", "000000000000900"),
   "resposta fecha em 900, mas o acervo esta em 100: recusa")
igual(cp.estado_sincronismo, cpm.SINCRONISMO_SEM_INFORMACAO, "e o estado nao muda")

secao("Guarda 3: divergencia externa NAO e apagada por caminho incorreto")
cp = cp_novo()
cp.registrar_observacao_sefaz("000000000001361", cstat="656")
igual(cp.estado_sincronismo, cpm.SINCRONISMO_DIVERGENCIA, "divergencia registrada")
ok(not cp.registrar_sincronia_confirmada(cpm.NSU_ZERO, cpm.NSU_ZERO),
   "uma resposta que fecha em ZERO nao apaga a divergencia de 1361")
igual(cp.estado_sincronismo, cpm.SINCRONISMO_DIVERGENCIA, "o estado continua")
igual(cp.nsu_observado_sefaz, "000000000001361", "e o observado tambem")

secao("A divergencia SO se resolve quando a alcancamos de fato")
cp = cp_novo()
cp.registrar_observacao_sefaz("000000000001361", cstat="656")
cp.marcar_possivel_consumidor_externo("teste")
cp.ult_nsu = "000000000001361"          # alcancamos, com documentos preservados
ok(cp.registrar_sincronia_confirmada("000000000001361", "000000000001361"),
   "chegando a 1361, a sincronia e aceita")
igual(cp.estado_sincronismo, cpm.SINCRONISMO_EM_DIA, "estado = EM_SINCRONIA")
igual(cp.diagnostico, cpm.DIAGNOSTICO_NENHUM,
      "e o diagnostico de consumidor externo deixa de valer")

secao("Sincronia confirmada NUNCA move o acervo")
with apoio.raiz_temporaria("r2_") as raiz:
    repo = RepositorioCheckpoint(raiz)
    repo.salvar(cp_novo(ult_nsu="000000000000216"))
    tr = TransporteFalso([resposta("137", "Nenhum documento localizado",
                                   "000000000000216", "000000000000216")])
    pipe.ingerir(raiz, T.EMPRESA_A, fonte(tr), ambiente="producao", usar_trava=False)
    igual(repo.carregar(T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, "producao").ult_nsu,
          "000000000000216", "ult_nsu intacto")

secao("Nenhum caminho de código copia ultNSU para o acervo")
import ast as _ast
_ATRIBUICOES_PROIBIDAS = []
for arq in sorted((RAIZ / "nfse" / "backend" / "ingestao").rglob("*.py")):
    arvore = _ast.parse(arq.read_text("utf-8"))
    for no in _ast.walk(arvore):
        if not isinstance(no, _ast.Assign):
            continue
        destino = _ast.unparse(no.targets[0]) if no.targets else ""
        origem = _ast.unparse(no.value)
        if not destino.endswith(".ult_nsu"):
            continue
        # Só é aceitável quando a origem é o NSU JÁ VALIDADO do lote — nunca
        # um campo cru vindo de uma resposta.
        if "resposta" in origem or "ultima_resposta" in origem or "resp." in origem:
            _ATRIBUICOES_PROIBIDAS.append(f"{arq.name}: {destino} = {origem}")
igual(_ATRIBUICOES_PROIBIDAS, [],
      "nenhum `checkpoint.ult_nsu = resposta.ultNSU` no pacote inteiro")

secao("Um fluxo de distNSU por vez para (CNPJ, serviço, ambiente)")
with apoio.raiz_temporaria("r2_") as raiz:
    from ingestao.trava import TravaOcupada, travar
    with travar(raiz, T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, PRODUCAO):
        try:
            with travar(raiz, T.EMPRESA_A, cpm.NFE_DISTRIBUICAO, PRODUCAO):
                ok(False, "a segunda trava deveria ter sido recusada")
        except TravaOcupada:
            ok(True, "a segunda varredura da MESMA chave é recusada")
        # outra empresa não é bloqueada pela primeira
        with travar(raiz, T.EMPRESA_B, cpm.NFE_DISTRIBUICAO, PRODUCAO):
            ok(True, "outra empresa roda em paralelo sem impedimento")
ok(True, "a trava é interna: NÃO impede um ERP externo de consultar a SEFAZ")

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
print("ING 3B-R2: todos os testes passaram.")
