# -*- coding: utf-8 -*-
"""sitfis.py — o Relatório de Situação Fiscal, pelo Integra Contador.

O QUE ELE É, E O QUE ELE NÃO É
    O SITFIS é o **extrato de pendências** da RFB/PGFN. Ele **não é certidão** e
    não substitui CND em licitação, crédito ou contrato. Por isso ele entra no
    envelope como `EXTRATO`, e não como `CERTIDAO` — misturar os dois faria a
    tela dizer "certidão em dia" para quem não tem certidão nenhuma.

    O Integra Contador **não emite CND**: isso é a API Consulta CND, produto e
    contrato separados. Ver FISCALE_SITUACAO_FISCAL.md.

TODA RESPOSTA É HTTP 200 — INCLUSIVE O ERRO
    Este é o motivo de este módulo existir com este cuidado. Nas nove chamadas
    capturadas no trial em 07/09/2026, **todas** vieram com `HTTP 200`; o estado
    real está no campo `status` do corpo (200 ou 400).

    Um conector que lesse `response.status_code` trataria toda falha como
    sucesso, e ninguém perceberia até faltar relatório de alguma empresa.
    Aqui o `status_code` só serve para o caso de o gateway devolver algo que
    nem JSON é.

O CORPO TEM JSON DENTRO DE STRING
    `dados` vem como texto, e precisa ser desserializado de novo para chegar em
    `protocoloRelatorio`, `tempoEspera` ou `pdf`.

O FLUXO É ASSÍNCRONO, E O SERVIÇO DIZ QUANTO ESPERAR
    /Apoiar  SOLICITARPROTOCOLO91  -> protocoloRelatorio + tempoEspera (ms)
    (espera)
    /Emitir  RELATORIOSITFIS92     -> pdf (base64)

    O `tempoEspera` vem na resposta (4000 ms na amostra). Ele é respeitado, e
    não substituído por um número nosso: quem sabe quanto o serviço demora é o
    serviço.

O CÓDIGO DA MENSAGEM NÃO É CHAVE
    O /Apoiar devolve `[Sucesso-Sitfis-SC01]` e o /Emitir devolve
    `[Sucesso-Sitfis-SC01]]` — com colchete duplo. Casar por texto exato
    quebraria numa das duas pontas. Aqui a decisão vem da ESTRUTURA: o `status`
    e a presença do campo esperado.

O QUE NÃO TEM AMOSTRA, NÃO É SIMULADO
    Protocolo ainda não pronto, protocolo expirado, contribuinte sem
    procuração, relatório com pendências × limpo: nada disso o trial produz.
    Esses caminhos caem em desfecho genérico com o texto que o serviço mandar,
    e **não** em código inventado por nós. Ver `fixturas/sitfis/LEIA-ME.md`.

O QUE ESTE MÓDULO NÃO FAZ
    Não grava, não indexa, não lê o PDF, não decide empresa. Devolve
    `Colheita`. Quem guarda é o portão.
"""
from __future__ import annotations

import base64
import binascii
import datetime as _dt
import json
import time

from .. import modelo
from . import Colheita

ORIGEM = "SITFIS"

BASE_TRIAL = "https://gateway.apiserpro.serpro.gov.br/integra-contador-trial/v1"
BASE_PRODUCAO = "https://gateway.apiserpro.serpro.gov.br/integra-contador/v1"

# O token público da página de demonstração. Não é segredo, e não vale em
# produção — lá é OAuth2 com consumerKey/secret e certificado e-CNPJ, e o
# `jwt_token` passa a ser obrigatório.
TOKEN_TRIAL = "06aef429-a981-3ec5-a1f8-71d38d86481e"

SISTEMA = "SITFIS"
SERVICO_PROTOCOLO = "SOLICITARPROTOCOLO91"
SERVICO_RELATORIO = "RELATORIOSITFIS92"
VERSAO = "1.0"

PJ, PF = 2, 1

ESPERA_MAXIMA_S = 60      # teto de sanidade sobre o `tempoEspera` do serviço

# RELATÓRIO AINDA EM PROCESSAMENTO.
#     A documentação oficial (página "Tempo de espera", conferida em
#     12/09/2026) diz que o /Emitir pode responder **202** ("em
#     processamento", com `tempoEspera` novo) ou **204** (sem corpo; o tempo
#     vem no cabeçalho ETag) quando o relatório não está pronto, e que se deve
#     esperar e repetir com o MESMO protocolo.
#
#     ISTO NÃO FOI CAPTURADO — o trial nunca devolve 202/204. Por isso a
#     tradução é mínima e travada: repete poucas vezes, só esperando o que o
#     serviço mandou, e ao esgotar devolve INDISPONIVEL com a frase dita. O
#     transporte não entrega cabeçalhos, então no 204 reaproveita-se a última
#     espera conhecida. Nada de laço aberto: é consulta de governo.
REPETICOES_EMITIR = 3


def tipo_do_documento(identidade: str) -> int:
    """11 dígitos = PF, 14 = PJ. Levanta se não for nem um nem outro."""
    d = "".join(c for c in str(identidade) if c.isdigit())
    if len(d) == 11:
        return PF
    if len(d) == 14:
        return PJ
    raise modelo.Invalido("identidade não é CPF nem CNPJ: %d dígitos" % len(d))


def montar_pedido(contratante: str, autor: str, contribuinte: str,
                  id_servico: str, dados: str = "") -> dict:
    """O corpo, exatamente na forma que o trial aceitou."""
    return {
        "contratante": {"numero": contratante, "tipo": tipo_do_documento(contratante)},
        "autorPedidoDados": {"numero": autor, "tipo": tipo_do_documento(autor)},
        "contribuinte": {"numero": contribuinte,
                         "tipo": tipo_do_documento(contribuinte)},
        "pedidoDados": {"idSistema": SISTEMA, "idServico": id_servico,
                        "versaoSistema": VERSAO, "dados": dados},
    }


def ler_dados(corpo: dict) -> dict:
    """Desembrulha o `dados`, que vem como JSON DENTRO de string.

    Devolve `{}` quando não dá para ler — nunca levanta. O chamador decide o
    desfecho a partir da AUSÊNCIA do campo que esperava, e não de uma exceção
    que o obrigaria a adivinhar o que faltou.
    """
    bruto = (corpo or {}).get("dados")
    if isinstance(bruto, dict):
        return bruto
    if not isinstance(bruto, str) or not bruto.strip():
        return {}
    try:
        d = json.loads(bruto)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def mensagem_de(corpo: dict) -> str:
    """O texto que o serviço mandou, para mostrar a quem está na tela."""
    msgs = (corpo or {}).get("mensagens") or []
    partes = []
    for m in msgs:
        if isinstance(m, dict):
            t = (m.get("texto") or "").strip()
            if t:
                partes.append(t)
    return " · ".join(partes)


class ConectorSitfis:
    """Pede o protocolo, espera, e traz o relatório.

    `transporte(url, corpo, cabecalhos) -> (status_http, dict_ou_None, bytes)`
        é injetado. Em teste ele serve as fixturas capturadas; em produção ele
        é `requests`. Assim a suíte percorre a MESMA lógica sem rede — e sem
        que o teste precise de credencial nenhuma.

    `dormir(segundos)` também é injetado: o `tempoEspera` do serviço é real,
    mas nenhum teste deve gastá-lo.
    """

    def __init__(self, transporte, contratante: str, autor: str = "",
                 base: str = BASE_TRIAL, token: str = TOKEN_TRIAL,
                 jwt: str = "", dormir=time.sleep):
        self.transporte = transporte
        self.contratante = contratante
        self.autor = autor or contratante
        self.base = base.rstrip("/")
        self.token = token
        self.jwt = jwt
        self.dormir = dormir

    # ── cabeçalhos ───────────────────────────────────────────────────────
    def _cabecalhos(self) -> dict:
        c = {"Authorization": "Bearer " + self.token,
             "Content-Type": "application/json",
             "Accept": "text/plain"}
        # No trial o `jwt_token` não é exigido; em produção é obrigatório.
        # Ele não aparece em log nenhum deste módulo.
        if self.jwt:
            c["jwt_token"] = self.jwt
        return c

    # ── uma chamada ──────────────────────────────────────────────────────
    def _chamar(self, caminho: str, pedido: dict):
        status_http, corpo, bruto = self.transporte(
            self.base + caminho, pedido, self._cabecalhos())
        return status_http, (corpo if isinstance(corpo, dict) else None), bruto

    # ── o fluxo ──────────────────────────────────────────────────────────
    def obter(self, identidade: str) -> Colheita:
        respostas = []

        # ── 1 · protocolo ────────────────────────────────────────────────
        try:
            pedido = montar_pedido(self.contratante, self.autor, identidade,
                                   SERVICO_PROTOCOLO)
        except modelo.Invalido as e:
            return Colheita(modelo.RECUSADA, detalhe=str(e), origem=ORIGEM)

        st, corpo, bruto = self._chamar("/Apoiar", pedido)
        respostas.append(bruto)
        if corpo is None:
            return Colheita(modelo.INDISPONIVEL, origem=ORIGEM,
                            respostas=respostas,
                            detalhe="a resposta do /Apoiar não é JSON "
                                    "(HTTP %s)" % st)
        # O ESTADO VEM DO CORPO, NÃO DO HTTP. Ver o cabeçalho do módulo.
        if corpo.get("status") != 200:
            return Colheita(_desfecho_do_status(corpo.get("status")),
                            origem=ORIGEM, respostas=respostas,
                            detalhe=mensagem_de(corpo) or
                            "o serviço recusou o pedido de protocolo "
                            "(status %s)" % corpo.get("status"))

        dados = ler_dados(corpo)
        protocolo = dados.get("protocoloRelatorio") or ""
        if not protocolo:
            # Sucesso sem protocolo: pode ser o estado assíncrono que o trial
            # não produz. Não inventamos código para ele.
            return Colheita(modelo.INDISPONIVEL, origem=ORIGEM,
                            respostas=respostas,
                            detalhe=mensagem_de(corpo) or
                            "o serviço respondeu sem protocolo")

        # ── 2 · a espera que o PRÓPRIO serviço pediu ─────────────────────
        try:
            espera_ms = float(dados.get("tempoEspera") or 0)
        except (TypeError, ValueError):
            espera_ms = 0.0
        espera_s = max(0.0, min(espera_ms / 1000.0, ESPERA_MAXIMA_S))
        if espera_s:
            self.dormir(espera_s)

        # ── 3 · relatório ────────────────────────────────────────────────
        pedido = montar_pedido(self.contratante, self.autor, identidade,
                               SERVICO_RELATORIO,
                               json.dumps({"protocoloRelatorio": protocolo}))
        st, corpo, bruto = self._chamar("/Emitir", pedido)
        respostas.append(bruto)
        repetidas = 0
        # 202 pode vir no HTTP ou no `status` do corpo: este serviço já provou
        # que põe o estado real no corpo com HTTP 200 (ver o cabeçalho).
        def _processando():
            return st in (202, 204) or (corpo or {}).get("status") == 202

        while _processando() and repetidas < REPETICOES_EMITIR:
            repetidas += 1
            try:
                nova_ms = float(ler_dados(corpo or {}).get("tempoEspera") or 0)
            except (TypeError, ValueError):
                nova_ms = 0.0
            if nova_ms:
                espera_s = max(0.0, min(nova_ms / 1000.0, ESPERA_MAXIMA_S))
            self.dormir(espera_s or 4.0)
            st, corpo, bruto = self._chamar("/Emitir", pedido)
            respostas.append(bruto)
        if _processando():
            return Colheita(modelo.INDISPONIVEL, origem=ORIGEM,
                            respostas=respostas,
                            detalhe="o relatório ainda estava em processamento "
                                    "depois de %d esperas; tente mais tarde"
                                    % repetidas)
        if corpo is None:
            return Colheita(modelo.INDISPONIVEL, origem=ORIGEM,
                            respostas=respostas,
                            detalhe="a resposta do /Emitir não é JSON "
                                    "(HTTP %s)" % st)
        if corpo.get("status") != 200:
            return Colheita(_desfecho_do_status(corpo.get("status")),
                            origem=ORIGEM, respostas=respostas,
                            detalhe=mensagem_de(corpo) or
                            "o serviço recusou emitir o relatório "
                            "(status %s)" % corpo.get("status"))

        b64 = (ler_dados(corpo).get("pdf") or "").strip()
        if not b64:
            return Colheita(modelo.INDISPONIVEL, origem=ORIGEM,
                            respostas=respostas,
                            detalhe=mensagem_de(corpo) or
                            "o serviço respondeu sem o relatório")
        try:
            documento = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            return Colheita(modelo.ILEGIVEL, origem=ORIGEM, respostas=respostas,
                            detalhe="o relatório veio em base64 inválido")

        # CONFERE QUE É PDF. O portal que devolve HTML de erro com cara de
        # sucesso é o caso que mais acontece — e guardar essa página como se
        # fosse o relatório é pior do que não guardar nada.
        if documento[:5] != b"%PDF-":
            return Colheita(modelo.ILEGIVEL, origem=ORIGEM, respostas=respostas,
                            detalhe="o que veio não é um PDF")

        return Colheita(modelo.OBTIDA, documento=documento, origem=ORIGEM,
                        respostas=respostas,
                        detalhe=mensagem_de(corpo),
                        extra={"protocolo_usado": True,
                               "tempo_espera_ms": espera_ms,
                               # QUANDO NOS PEDIMOS. O carimbo do relatorio
                               # esta no PDF e o leitor o prefere; este aqui e
                               # a rede de seguranca, e e fato observado --
                               # nao estimativa. Sem ele, um relatorio cujo
                               # cabecalho o leitor nao entenda ficaria sem
                               # data nenhuma, e um extrato sem data nao serve
                               # para dizer se esta velho.
                               "apurado_em": _dt.date.today().isoformat()})


def _desfecho_do_status(status) -> str:
    """Traduz o `status` do CORPO para o vocabulário do envelope.

    O 400 do trial é sempre "Dados inválidos" — parâmetro, serviço inexistente
    ou contribuinte fora do cenário. Isso é RECUSADA: não adianta tentar de
    novo igual.
    """
    if status == 400:
        return modelo.RECUSADA
    return modelo.ERRO


def transporte_requests(tempo_limite: int = 90):
    """O transporte de verdade. Só é montado quando alguém vai à rede.

    `requests` é importado AQUI dentro de propósito: assim o pacote inteiro —
    e a suíte — não depende de rede nem de biblioteca de rede para importar.
    """
    import requests

    def transporte(url, corpo, cabecalhos):
        r = requests.post(url, json=corpo, headers=cabecalhos,
                          timeout=tempo_limite)
        try:
            d = r.json()
        except ValueError:
            d = None
        return r.status_code, d, r.content

    return transporte


def de_credencial(credencial, transporte, token: str = TOKEN_TRIAL,
                  jwt: str = "", dormir=time.sleep) -> ConectorSitfis:
    """Monta o conector a partir da credencial configurada.

    O PONTO DELICADO ESTÁ NUMA LINHA SÓ: `autor=credencial.autor`.

        As procurações das empresas não estão no e-CNPJ do escritório — estão
        no CPF de quem assina. Então `autorPedidoDados` é o PROCURADOR, e
        `contratante` é quem tem o contrato SERPRO. São documentos diferentes.

        Passar o contratante nos dois campos funciona no trial (que só aceita o
        cenário fictício) e é recusado em produção por falta de procuração —
        num ponto do código longe daqui, e com mensagem que não aponta a causa.

    Nenhuma identidade é fixada aqui: quem sabe quem assina é o
    `credencial.py`, e quem responde por ele é o arquivo de configuração.
    """
    base = BASE_PRODUCAO if credencial.eh_producao else BASE_TRIAL
    return ConectorSitfis(transporte=transporte,
                          contratante=credencial.contratante,
                          autor=credencial.autor,
                          base=base, token=token, jwt=jwt, dormir=dormir)
