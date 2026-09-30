# -*- coding: utf-8 -*-
"""carteira.py — a carteira de regularidade FEDERAL do escritório.

O QUE ELE É
    A costura entre o índice (o que se sabe) e a regra de `regularidade.py`
    (o que isso significa). Não lê PDF, não vai à rede e não grava nada.

SÓ A RECEITA FEDERAL (decisão de 12/09/2026)
    A carteira avalia cada empresa exclusivamente pelo SITFIS (Integra
    Contador). O escopo municipal do Recife foi retirado por inviabilidade de
    automação — portal sem API e com CAPTCHA —, e com ele a Inscrição
    Mercantil e o acionamento da bandeja deixaram de fazer parte desta tela.

O UNIVERSO VEM DO CADASTRO — nunca das pastas
    Derivar a lista das pastas em disco já fez uma empresa sumir em silêncio
    (memória "o universo do ciclo vem do cadastro"). Quem chama entrega as
    empresas do cadastro; empresa sem documento aparece como NÃO CONSULTADA,
    que é justamente o que precisa ser visto.
"""
from __future__ import annotations

from . import modelo, regularidade as reg

ESFERA = modelo.FEDERAL


def canal_federal(cofre) -> dict:
    """Como a esfera federal PODE ser consultada hoje. Nunca abre segredo.

    Olha só a PRESENÇA das chaves no cofre, sem decifrá-las: esta função roda
    a cada abertura da tela, e decifrar credencial para desenhar uma pastilha
    seria expor segredo em memória sem necessidade.
    """
    if cofre is None:
        return {"automatico": False, "representacao": None,
                "motivo": "credenciais do Integra Contador não configuradas"}
    dados = getattr(cofre, "dados", {}) or {}
    procuradores = [p for p in cofre.procuradores() if p.ativo and p.valido()]
    segredos = dados.get("segredos") or {}
    contratante = "".join(c for c in str(dados.get("contratante") or "")
                          if c.isdigit())
    ambiente = str(dados.get("ambiente") or "trial").lower()

    if not procuradores:
        return {"automatico": False, "representacao": False,
                "motivo": "nenhum procurador configurado — o SITFIS exige "
                          "procuração eletrônica do contribuinte para quem assina"}
    if len(contratante) != 14:
        return {"automatico": False, "representacao": True,
                "motivo": "o contratante do Integra Contador não está configurado"}
    if ambiente != "producao":
        return {"automatico": False, "representacao": True,
                "motivo": "o Integra Contador está no ambiente de demonstração, "
                          "que só responde contribuintes fictícios"}
    faltam = [n for n in ("consumer_key", "consumer_secret")
              if not isinstance(segredos.get(n), dict)]
    if faltam and not isinstance(segredos.get("token"), dict):
        return {"automatico": False, "representacao": True,
                "motivo": "faltam %s do contrato SERPRO no cofre — sem eles o "
                          "token de produção não é gerado" % " e ".join(faltam)}
    return {"automatico": True, "representacao": True, "motivo": ""}


def _doc_resumo(d: dict) -> dict:
    if not d:
        return {}
    return {k: d.get(k) for k in ("id", "esfera", "tipo", "origem", "sha256",
                                  "bytes", "capturado_utc", "natureza",
                                  "validade", "apurado_em", "emissao", "codigo")}


def avaliar_empresa(ix, empresa: dict, canal_fed: dict = None, hoje=None) -> dict:
    """A linha completa de uma empresa: a esfera federal e o consolidado dela."""
    ident = empresa["identidade"]
    docs = ix.documentos(ident, ESFERA)
    tents = ix.tentativas_da_esfera(ident, ESFERA)
    ints = ix.interpretacoes(ident, ESFERA)
    fed = reg.avaliar_esfera(ESFERA, docs, tents, ints, hoje, canal=canal_fed)
    # O ÚLTIMO DE CADA TIPO, para os botões "relatório" e "certidão".
    fed["relatorio"] = _doc_resumo(next(
        (d for d in docs if d.get("tipo") == modelo.EXTRATO), {}))
    fed["certidao"] = _doc_resumo(next(
        (d for d in docs if d.get("tipo") == modelo.CERTIDAO), {}))
    fed["documento"] = _doc_resumo(fed.get("documento") or {})
    fed["grupo"] = reg.grupo(fed["status"])
    return {
        "identidade": ident,
        "nome": empresa.get("nome") or ident,
        "federal": fed,
        "geral": reg.consolidar(fed),
        "ultima_consulta": fed.get("ultima_consulta") or "",
    }


def montar(ix, empresas, canal_fed: dict = None, hoje=None) -> dict:
    """A carteira inteira, com os contadores do topo."""
    linhas = [avaliar_empresa(ix, e, canal_fed, hoje) for e in empresas]
    conta = {"empresas": len(linhas), "regulares": 0, "com_pendencia": 0,
             "nao_consultadas": 0, "consulta_necessaria": 0, "erro": 0}
    for l in linhas:
        g = l["geral"]["status"]
        if g == reg.G_REGULAR:
            conta["regulares"] += 1
        elif g == reg.G_COM_PENDENCIA:
            conta["com_pendencia"] += 1
        elif g == reg.G_NAO_CONSULTADO:
            conta["nao_consultadas"] += 1
        else:
            conta["consulta_necessaria"] += 1
        if l["federal"]["status"] == reg.ERRO_ACESSO:
            conta["erro"] += 1
    return {"empresas": linhas, "contadores": conta,
            "canal_federal": canal_fed or {},
            "rotulos": dict(reg.ROTULO), "rotulos_geral": dict(reg.ROTULO_GERAL)}


def historico(ix, identidade: str, esfera: str = ESFERA) -> list:
    """Consultas e classificações de UMA esfera, da mais recente para a antiga.

    Nada aqui é apagado ou resumido: é o registro de quem fez o quê, quando,
    por qual método, e com qual documento (hash) — o que a auditoria pede.

    `esfera` nasce em FEDERAL para que quem já chamava continue recebendo o que
    recebia. O municipal passa a caber pela mesma função em vez de ganhar uma
    segunda, que envelheceria diferente desta.
    """
    eventos = []
    for t in ix.tentativas_da_esfera(identidade, esfera, limite=500):
        eventos.append({
            "quando_utc": t.get("quando_utc"), "evento": "CONSULTA",
            "esfera": t.get("esfera"), "tipo": t.get("tipo"),
            "resultado": t.get("desfecho"), "detalhe": t.get("detalhe") or "",
            "origem": t.get("origem") or "",
            "origem_legivel": modelo.origem_legivel(t.get("origem") or ""),
            "metodo": t.get("metodo") or "", "usuario": t.get("usuario") or "",
            "documento_id": t.get("documento_id") or "",
            "sha256": t.get("sha256") or ""})
    for i in ix.interpretacoes(identidade, esfera):
        eventos.append({
            "quando_utc": i.get("quando_utc"), "evento": "CLASSIFICACAO",
            "esfera": i.get("esfera"), "tipo": i.get("tipo"),
            "resultado": i.get("status"),
            "rotulo": reg.ROTULO.get(i.get("status"), i.get("status")),
            "detalhe": i.get("observacao") or "",
            "quantidade_pendencias": i.get("quantidade_pendencias"),
            "valor_total": i.get("valor_total"),
            "pendencias": i.get("pendencias") or [],
            "usuario": i.get("usuario") or "",
            "documento_id": i.get("documento_id") or ""})
    eventos.sort(key=lambda e: e.get("quando_utc") or "", reverse=True)
    return eventos


# ════════════════════════════════════════════════════════════════════════════
#  Esfera MUNICIPAL — canal assistido, e declarado como tal
# ════════════════════════════════════════════════════════════════════════════
#  Por que ela é uma função separada, e não mais uma esfera dentro de
#  `avaliar_empresa`:
#
#    1. o consolidado é FEDERAL por decisão (12/09/2026). Injetar o municipal
#       em `avaliar_empresa` mudaria, em silêncio, o significado do verde que
#       a carteira já mostra hoje — e isso é decisão de produto, não refactor;
#    2. o municipal não tem consulta automática: o portal do Recife exige
#       CAPTCHA e o PDF medido aqui é imagem, sem camada de texto. Então esta
#       esfera nunca produz "regular" por leitura — só por CLASSIFICAÇÃO
#       humana, e a resposta diz isso explicitamente.
#
#  A regra de sempre continua valendo: sem documento não é pendência, é NÃO
#  CONSULTADA; e falha nunca vira regular.
MUNICIPAL = modelo.MUNICIPAL

AUTOMATICO_INDISPONIVEL = (
    "o portal municipal não oferece API e exige CAPTCHA resolvido por pessoa; "
    "o documento entra pelo canal assistido (você baixa, o FISCALE guarda)")


def _motivo_sem_leitura(tentativas: list, doc: dict) -> str:
    """O `detalhe` da tentativa que trouxe ESTE documento, quando ele explica.

    Só vale para o documento em questão: o motivo de um PDF não se aplica a
    outro. E "duplicata" não é motivo de nada — é informação sobre a chegada.
    """
    if not doc:
        return ""
    for t in tentativas:
        if t.get("documento_id") != doc.get("id"):
            continue
        d = (t.get("detalhe") or "").strip()
        if d and d != "duplicata":
            return d
    return ""


def municipal(ix, identidade: str) -> dict:
    """O que se sabe da esfera municipal desta empresa. Sem consulta, sem rede.

    Devolve documento mais recente, histórico e o status — que aqui vem
    **apenas** de classificação humana. Sem classificação, o documento existe e
    o status é declarado como não interpretado, nunca como regular.
    """
    docs = ix.documentos(identidade, MUNICIPAL)
    ints = ix.interpretacoes(identidade, MUNICIPAL)
    tents = ix.tentativas_da_esfera(identidade, MUNICIPAL, limite=500)

    doc = docs[0] if docs else {}
    # A interpretação vale para UM documento. A de outro documento não responde
    # por este — é a mesma regra da esfera federal.
    interp = next((i for i in ints
                   if not doc or i.get("documento_id") == doc.get("id")), {})

    # O VOCABULÁRIO DO STATUS É O DA REGULARIDADE, não o do documento.
    #     `modelo.SEM_DOCUMENTO`/`SEM_VALIDADE` descrevem o ESTADO DE UM
    #     DOCUMENTO; `reg.*` descreve a SITUAÇÃO DA EMPRESA. Misturar os dois
    #     aqui faria a tela pintar farol com uma palavra que o resto do sistema
    #     lê como outra coisa. O que o documento tem de próprio vai em campos
    #     próprios (`quantidade_documentos`, `sem_leitura`).
    if not docs:
        status = reg.NAO_CONSULTADO
        motivo = "nenhum documento municipal guardado para esta empresa"
    elif interp:
        # Classificação humana é a ÚNICA forma de a esfera municipal concluir
        # algo: não há leitor, então não existe conclusão automática aqui.
        status = interp.get("status") or reg.NAO_CONSULTADO
        motivo = "classificado por %s" % (interp.get("usuario") or "alguém")
    else:
        status = reg.NAO_CONSULTADO
        motivo = ("documento guardado, ainda não classificado — não há leitor "
                  "para esta esfera, então a conclusão depende de alguém abrir "
                  "o PDF")

    return {
        "esfera": MUNICIPAL,
        "status": status,
        "rotulo": reg.ROTULO.get(status, status),
        "grupo": reg.grupo(status),
        "motivo": motivo,
        # A conclusão nunca é automática nesta esfera, e isso é dito, não
        # deduzido pela tela.
        "fonte_status": "HUMANA" if interp else "",
        "automatico": False,
        "motivo_sem_automatico": AUTOMATICO_INDISPONIVEL,
        "documento": _doc_resumo(doc),
        "documentos": [_doc_resumo(d) for d in docs],
        "quantidade_documentos": len(docs),
        "interpretacao": {
            "status": interp.get("status") or "",
            "rotulo": reg.ROTULO.get(interp.get("status"),
                                     interp.get("status") or ""),
            "usuario": interp.get("usuario") or "",
            "quando_utc": interp.get("quando_utc") or "",
            "observacao": interp.get("observacao") or "",
        } if interp else None,
        # Por que a validade não foi lida. Vem do `detalhe` da tentativa deste
        # documento — o índice não guarda o `extra` da captura, e duplicar o
        # motivo numa coluna nova seria dado em dois lugares para divergir.
        "sem_leitura": _motivo_sem_leitura(tents, doc),
        "ultima_tentativa": (tents[0].get("quando_utc") if tents else ""),
        "historico": historico(ix, identidade, MUNICIPAL),
        # O municipal NÃO entra no consolidado — dito na resposta para a tela
        # não precisar saber disso por convenção.
        "entra_no_consolidado": False,
    }
