# -*- coding: utf-8 -*-
"""regularidade.py — a empresa está regular perante a Receita? A regra ÚNICA.

SÓ A ESFERA FEDERAL (decisão de 12/09/2026)
    A Situação Fiscal opera exclusivamente com o SITFIS (Integra Contador). O
    escopo municipal do Recife foi retirado: o portal não oferece API e exige
    CAPTCHA resolvido por pessoa, então não há automação possível — e o fluxo
    manual contraria a premissa do sistema. O consolidado deixou de cruzar
    esferas: a situação geral é a situação federal, com os mesmos freios.

ESTE MÓDULO NÃO TOCA DISCO NEM REDE.
    Recebe o que o índice já sabe (documentos, tentativas, interpretações) e
    devolve o status federal e o consolidado dele. É a única função do
    projeto que responde "regular / pendência / não sei" — a tela, o lote e o
    relatório perguntam aqui, e por isso não podem discordar entre si.

A REGRA QUE ORGANIZA TUDO: FALHA NUNCA VIRA REGULAR
    "Regular" só nasce de um DOCUMENTO do órgão que diz isso — lido pelo
    leitor calibrado, ou classificado por uma pessoa que abriu o PDF. Uma
    consulta que falhou, uma credencial que faltou, um portal fora do ar: tudo
    isso produz um status de "precisa consultar", nunca um verde.

    E o inverso também vale: uma empresa sem documento nenhum não está
    "pendente" — está NÃO CONSULTADA. Vermelho sem documento é tão falso
    quanto verde sem documento.

TRÊS CAMADAS, QUE NÃO SE MISTURAM
    documento      o PDF do órgão, imutável            (armazenamento)
    resultado      a tentativa, com desfecho           (índice: tentativa)
    interpretação  o que o FISCALE conclui do PDF      (interpretacao.py)

    A interpretação humana vence a automática PARA O MESMO DOCUMENTO — quem
    abriu o PDF e leu a lista de pendências sabe mais que um leitor que só
    reconhece a frase de ausência. Mas ela nunca altera o documento, e nunca
    vale para outro documento.

O DOCUMENTO MAIS RECENTE RESPONDE PELA ESFERA
    Não o "melhor". Um extrato de hoje com pendência e uma certidão negativa
    de três meses atrás não fazem uma empresa regular: o retrato novo é a
    realidade de hoje. Escolher o mais favorável seria exatamente o viés que o
    módulo existe para não ter.

CERTIDÃO SEM VALIDADE LIDA ENVELHECE COMO EXTRATO
    Quando ninguém leu a validade, não há carimbo para respeitar — e calcular
    "emissão + 180" é proibido (ver modelo.py: uma amostra real deu 194).
    Então vale a política de frescor do extrato, que é conservadora: pede
    consulta nova antes da hora, e nunca depois.
"""
from __future__ import annotations

import datetime as _dt

from . import modelo

# ── Status POR ESFERA ─────────────────────────────────────────────────────
REGULAR = "REGULAR"
PENDENCIA = "PENDENCIA"
CPD_EN = "CERTIDAO_POSITIVA_COM_EFEITOS_DE_NEGATIVA"
POSITIVA = "CERTIDAO_POSITIVA"
NAO_CONSULTADO = "NAO_CONSULTADO"
CONSULTA_EXPIRADA = "CONSULTA_EXPIRADA"
ERRO_ACESSO = "ERRO_DE_ACESSO"
REQUER_REPRESENTACAO = "ACESSO_REQUER_REPRESENTACAO"
REQUER_INTERACAO = "ACESSO_REQUER_INTERACAO_DO_USUARIO"
STATUS_ESFERA = (REGULAR, PENDENCIA, CPD_EN, POSITIVA, NAO_CONSULTADO,
                 CONSULTA_EXPIRADA, ERRO_ACESSO, REQUER_REPRESENTACAO,
                 REQUER_INTERACAO)

# O que uma PESSOA pode afirmar ao classificar um documento. "Não consultado"
# ou "erro" não se classificam: são fatos da tentativa, não leitura do PDF.
CLASSIFICAVEIS = (REGULAR, PENDENCIA, CPD_EN, POSITIVA)

# A CPD-EN vale como negativa (licitação, crédito) — ver modelo.regular().
# Ela entra no grupo "regular" do consolidado, mas a tela a mostra com nome
# próprio: há débito, com exigibilidade suspensa, e isso decide negócio.
_GRUPO_REGULAR = (REGULAR, CPD_EN)
_GRUPO_PENDENCIA = (PENDENCIA, POSITIVA)
_GRUPO_NECESSARIA = (CONSULTA_EXPIRADA, ERRO_ACESSO, REQUER_REPRESENTACAO,
                     REQUER_INTERACAO)

# ── Status CONSOLIDADO ────────────────────────────────────────────────────
G_REGULAR = "REGULAR"
G_COM_PENDENCIA = "COM_PENDENCIA"
G_NAO_CONSULTADO = "NAO_CONSULTADO"
G_CONSULTA_NECESSARIA = "CONSULTA_NECESSARIA"
STATUS_GERAL = (G_REGULAR, G_COM_PENDENCIA, G_NAO_CONSULTADO,
                G_CONSULTA_NECESSARIA)

ROTULO = {
    REGULAR: "Regular",
    PENDENCIA: "Pendência",
    CPD_EN: "Positiva com efeitos de negativa",
    POSITIVA: "Certidão positiva",
    NAO_CONSULTADO: "Não consultado",
    CONSULTA_EXPIRADA: "Consulta expirada",
    ERRO_ACESSO: "Erro de acesso",
    REQUER_REPRESENTACAO: "Requer representação",
    REQUER_INTERACAO: "Requer interação",
}
ROTULO_GERAL = {
    G_REGULAR: "Regular",
    G_COM_PENDENCIA: "Com pendência",
    G_NAO_CONSULTADO: "Não consultado",
    G_CONSULTA_NECESSARIA: "Consulta necessária",
}


def _data(v):
    """`date` de ISO (data ou data-hora), ou `None`. Não inventa."""
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def automatica(doc: dict):
    """O que o LEITOR permite concluir de um documento, ou `None`.

    Só afirma o que foi observado em amostra real:
      • certidão com natureza lida → a natureza manda;
      • extrato SITFIS com a frase "não foram detectadas pendências" → REGULAR.

    Extrato cuja frase de ausência NÃO apareceu devolve `None`, e não
    PENDENCIA: não há amostra real de relatório com pendências (ver
    leitores/sitfis.py). Deduzir pendência da ausência de uma frase seria
    pintar de vermelho um PDF que talvez só tenha mudado de redação.
    """
    tipo = (doc.get("tipo") or "").upper()
    natureza = doc.get("natureza") or ""
    if tipo == modelo.CERTIDAO:
        return {modelo.NEGATIVA: REGULAR, modelo.CPD_EN: CPD_EN,
                modelo.POSITIVA: POSITIVA}.get(natureza)
    if tipo == modelo.EXTRATO:
        if doc.get("sem_pendencias") in (1, True, "1"):
            # A certidão CITADA no relatório pode ser CPD-EN mesmo sem
            # pendência ativa (exigibilidade suspensa); aí o nome próprio vale.
            return CPD_EN if natureza == modelo.CPD_EN else REGULAR
        if natureza == modelo.POSITIVA:
            return POSITIVA
    return None


def _frescor(doc: dict, interp: dict, hoje, dias_frescor, dias_aviso):
    """`(expirado, vence_em)` do documento. Validade carimbada manda."""
    validade = _data((interp or {}).get("validade")) or _data(doc.get("validade"))
    tipo = (doc.get("tipo") or "").upper()
    if tipo == modelo.CERTIDAO and validade:
        return validade < hoje, validade.isoformat()
    base = (_data(doc.get("apurado_em")) or _data(doc.get("emissao"))
            or _data(doc.get("capturado_utc")))
    if base is None:
        return True, ""
    return (hoje - base).days > dias_frescor, ""


def avaliar_esfera(esfera: str, documentos=(), tentativas=(),
                   interpretacoes=(), hoje=None, canal: dict = None,
                   dias_frescor: int = modelo.DIAS_FRESCOR,
                   dias_aviso: int = modelo.DIAS_AVISO) -> dict:
    """O status de UMA esfera. Nunca levanta.

    `documentos`, `tentativas` e `interpretacoes` são linhas do índice DESTA
    esfera, em qualquer ordem — a ordenação é feita aqui, pelo instante, para
    que nenhum chamador consiga mudar a resposta mudando a ordem da consulta.

    `canal` descreve como esta esfera PODE ser consultada:
        {"automatico": bool, "representacao": bool, "motivo": str,
         "interacao": bool}
    Ele só entra quando NÃO há documento — é contexto, não evidência.
    """
    h = _data(hoje) or _dt.date.today()
    canal = dict(canal or {})
    saida = {"esfera": esfera, "status": NAO_CONSULTADO, "rotulo": "",
             "motivo": "", "documento": {}, "interpretacao": {},
             "fonte_status": "", "ultima_consulta": "", "ultima_tentativa": {},
             "quantidade_pendencias": None, "valor_total": None,
             "validade": "", "aviso": "", "canal": canal}

    docs = sorted((dict(d) for d in documentos),
                  key=lambda d: (d.get("capturado_utc") or "", d.get("id") or ""),
                  reverse=True)
    tents = sorted((dict(t) for t in tentativas),
                   key=lambda t: (t.get("quando_utc") or "", t.get("id") or 0),
                   reverse=True)
    interps = sorted((dict(i) for i in interpretacoes),
                     key=lambda i: i.get("quando_utc") or "", reverse=True)

    ultima_t = tents[0] if tents else {}
    saida["ultima_tentativa"] = ultima_t
    instantes = [x for x in ((docs[0].get("capturado_utc") if docs else ""),
                             ultima_t.get("quando_utc", "")) if x]
    saida["ultima_consulta"] = max(instantes) if instantes else ""

    def _pela_tentativa(t):
        """Status de uma tentativa SEM documento. Nunca REGULAR."""
        d = t.get("desfecho")
        detalhe = t.get("detalhe") or ""
        if d == modelo.NAO_EMITIDA:
            # O órgão disse, com todas as letras, que há pendência e por isso
            # não emite. É desfecho de negócio — não é falha nossa.
            return PENDENCIA, "o órgão recusou emitir: há pendência. " + detalhe
        if d == modelo.RECUSADA:
            if canal.get("representacao") is False:
                return (REQUER_REPRESENTACAO,
                        canal.get("motivo") or "não há procurador configurado")
            return ERRO_ACESSO, detalhe or "o pedido foi recusado"
        if d in (modelo.INDISPONIVEL, modelo.ERRO, modelo.ILEGIVEL):
            return ERRO_ACESSO, detalhe or "a consulta não trouxe documento"
        return ERRO_ACESSO, detalhe or ("desfecho %s" % d)

    if not docs:
        if ultima_t:
            st, motivo = _pela_tentativa(ultima_t)
        elif canal.get("interacao"):
            st, motivo = NAO_CONSULTADO, canal.get("motivo") or ""
        else:
            st, motivo = NAO_CONSULTADO, canal.get("motivo") or \
                "nenhuma consulta registrada"
        saida.update(status=st, motivo=motivo.strip())
        saida["rotulo"] = ROTULO[st]
        return saida

    doc = docs[0]
    saida["documento"] = doc
    interp = next((i for i in interps
                   if i.get("documento_id") == doc.get("id")), {})
    saida["interpretacao"] = interp

    if interp and interp.get("status") in CLASSIFICAVEIS:
        st = interp["status"]
        saida["fonte_status"] = "HUMANA"
        saida["quantidade_pendencias"] = interp.get("quantidade_pendencias")
        saida["valor_total"] = interp.get("valor_total")
    else:
        st = automatica(doc)
        saida["fonte_status"] = "LEITOR" if st else ""
        if doc.get("valor_total") is not None:
            saida["valor_total"] = doc.get("valor_total")

    expirado, vence = _frescor(doc, interp, h, dias_frescor, dias_aviso)
    saida["validade"] = vence

    # Uma tentativa POSTERIOR ao documento que falhou não apaga o documento,
    # mas precisa aparecer: é a pista de que a próxima renovação vai emperrar.
    if ultima_t and (ultima_t.get("quando_utc") or "") > (doc.get("capturado_utc") or "") \
            and ultima_t.get("desfecho") != modelo.OBTIDA:
        saida["aviso"] = "a consulta mais recente não trouxe documento (%s)" % (
            ultima_t.get("desfecho") or "?")

    if st is None:
        saida.update(status=REQUER_INTERACAO,
                     motivo="o documento foi guardado, mas o sistema não "
                            "consegue concluir a situação lendo o PDF — abra "
                            "e classifique")
    elif expirado:
        # O status antigo vai junto, como contexto: "estava regular em
        # agosto" é informação útil; "está regular" seria mentira.
        saida.update(status=CONSULTA_EXPIRADA,
                     motivo="o último documento (%s em %s) está vencido ou "
                            "velho demais para valer hoje" % (
                                ROTULO.get(st, st),
                                (doc.get("capturado_utc") or "")[:10]))
        saida["status_anterior"] = st
    else:
        saida.update(status=st, motivo="")
    saida["rotulo"] = ROTULO[saida["status"]]
    return saida


def consolidar(federal: dict) -> dict:
    """O status GERAL da empresa — que é o federal, e só ele.

    Não há mais peso cruzado de outra esfera. O que continua valendo:
      • pendência conhecida → COM PENDÊNCIA;
      • REGULAR só com documento federal regular e vigente;
      • nada consultado → NÃO CONSULTADO;
      • erro, recusa, expirada, requer interação → CONSULTA NECESSÁRIA.
        Nunca regular.
    """
    st = (federal or {}).get("status")
    if st in _GRUPO_PENDENCIA:
        g, m = G_COM_PENDENCIA, "pendência na Receita Federal / PGFN"
    elif st in _GRUPO_REGULAR:
        g, m = G_REGULAR, ""
    elif st in (NAO_CONSULTADO, None):
        g, m = G_NAO_CONSULTADO, "nenhuma consulta federal registrada"
    else:
        g, m = G_CONSULTA_NECESSARIA, "Federal: " + ROTULO.get(st, st)
    return {"status": g, "rotulo": ROTULO_GERAL[g], "motivo": m}


def grupo(status_esfera: str) -> str:
    """`regular` · `pendencia` · `necessaria` · `nao_consultado`."""
    if status_esfera in _GRUPO_REGULAR:
        return "regular"
    if status_esfera in _GRUPO_PENDENCIA:
        return "pendencia"
    if status_esfera in _GRUPO_NECESSARIA:
        return "necessaria"
    return "nao_consultado"
