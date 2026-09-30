# -*- coding: utf-8 -*-
"""classificacao.py — o que cada movimento do extrato PARECE ser.

É SUGESTÃO, E SÓ SUGESTÃO
    Esta função devolve um dicionário. Ela não recebe banco, não cria
    lançamento, não altera conciliação. É estruturalmente incapaz de tornar
    uma sugestão definitiva — e é assim de propósito, para que "a IA lançou
    sozinha" não seja um defeito possível neste módulo.

    Cada sugestão diz a REGRA que a produziu e a CONFIANÇA. Quem decide é a
    pessoa, na tela, e o que ela decide vai para `categoria_confirmada`, ao
    lado — a sugestão original não é sobrescrita.

AS REGRAS, DA MAIS FORTE PARA A MAIS FRACA
    1. o documento da contraparte é o da PRÓPRIA empresa → transferência
       entre contas (não é receita nem despesa);
    2. palavras que não têm dois sentidos: IOF, TARIFA, DARF, SALÁRIO...;
    3. o documento ou o nome da contraparte está no acervo fiscal como
       cliente (tomador de NFS-e emitida) ou fornecedor (emitente de NF-e de
       compra) → CLIENTE / FORNECEDOR;
    4. o meio (PIX, TED, BOLETO) com o sentido do dinheiro, e a contraparte
       com cara de empresa (LTDA, ME, S/A, CNPJ) → CLIENTE ou FORNECEDOR;
    5. o `TRNTYPE` do OFX, quando o texto não disse nada;
    6. nada disso → NÃO IDENTIFICADO. Nunca um palpite disfarçado de regra.

NÃO É REGRA TRIBUTÁRIA
    "TRIBUTOS" aqui quer dizer "o banco pagou uma guia". Qual tributo, de que
    competência, e se o valor está certo, não é pergunta deste módulo.
"""
from __future__ import annotations

import re

from . import modelo as m

_CNPJ_FMT = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
_CPF_FMT = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_DIGITOS = re.compile(r"(?<!\d)(\d{14}|\d{11})(?!\d)")

_PJ = re.compile(r"\b(LTDA|ME|EPP|EIRELI|S/?A|S\.A|CIA|SA|COMERCIO|SERVICOS|"
                 r"INDUSTRIA|DISTRIBUIDORA|TRANSPORTES|ENGENHARIA|CONSULTORIA|"
                 r"LTD|INC)\b")


def _tem(texto: str, *palavras) -> bool:
    return any(re.search(r"(?<![A-Z0-9])" + re.escape(p) + r"(?![A-Z0-9])",
                         texto) for p in palavras)


def documentos_na_descricao(descricao: str) -> list:
    """CNPJ/CPF que aparecem na descrição, só os com dígito verificador certo.
    CPF mascarado (`***.456.789-**`) não é documento e não é devolvido."""
    achados = []
    for rx in (_CNPJ_FMT, _CPF_FMT):
        for x in rx.findall(descricao or ""):
            d = m.so_digitos(x)
            if d not in achados:
                achados.append(d)
    for d in _DIGITOS.findall(descricao or ""):
        if d in achados:
            continue
        if (len(d) == 14 and m.cnpj_valido(d)) or (len(d) == 11 and m.cpf_valido(d)):
            achados.append(d)
    return [d for d in achados if m.cnpj_valido(d) or m.cpf_valido(d)]


_PREFIXOS = (
    "PIX RECEBIDO", "PIX ENVIADO", "PIX EMITIDO", "PIX TRANSF", "PIX TRANSFERENCIA",
    "TRANSFERENCIA PIX", "TRANSF PIX", "PIX QRS", "PIX QR", "RECEBIMENTO PIX",
    "PAGAMENTO PIX", "PIX", "TED RECEBIDA", "TED ENVIADA", "TED", "DOC",
    "TEF", "TRANSFERENCIA", "TRANSF", "RECEBIDO", "ENVIADO", "PAGAMENTO",
    "PAGTO", "PGTO", "RECEBIMENTO", "BOLETO", "PARA", "DE", "REM", "DEST",
    "FAVORECIDO", "PAGADOR", "FORNECEDOR", "CLIENTE")


def nome_na_descricao(descricao: str) -> str:
    """O que sobra da descrição depois de tirar meio, data, valor e documento.
    É "o nome lido da descrição" — a tela chama assim, e não de razão social."""
    t = m.normalizar_texto(descricao).replace("|", " ")
    t = _CNPJ_FMT.sub(" ", t)
    t = _CPF_FMT.sub(" ", t)
    t = re.sub(r"\*{2,}[\d.\-*]*", " ", t)
    t = re.sub(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b", " ", t)
    t = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\b", " ", t)
    t = re.sub(r"R\$\s*[\d.,]+", " ", t)
    t = re.sub(r"\b\d[\d.,/-]*\b", " ", t)
    t = re.sub(r"[-:;,]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    mudou = True
    while mudou and t:
        mudou = False
        for p in _PREFIXOS:
            if t == p:
                t, mudou = "", True
                break
            if t.startswith(p + " "):
                t, mudou = t[len(p) + 1:].strip(), True
                break
    return t if sum(c.isalpha() for c in t) >= 3 else ""


def _sug(categoria, sugestao, confianca, regra, doc="", nome=""):
    return {"categoria": categoria, "sugestao": sugestao,
            "confianca": confianca, "regra": regra,
            "contraparte_doc": doc, "contraparte_nome": nome}


def _procurar_nome(nome: str, nomes: dict) -> str:
    """Nome lido do extrato contra os nomes do acervo. Devolve o documento
    achado, ou ''. Só casa nome com pelo menos duas palavras em comum no
    começo — "SILVA" sozinho casaria metade do Brasil."""
    if not nome:
        return ""
    alvo = nome.split()
    if len(alvo) < 2:
        return ""
    for doc, outro in nomes.items():
        o = m.normalizar_texto(outro).split()
        if len(o) >= 2 and o[:2] == alvo[:2]:
            return doc
    return ""


def classificar(descricao: str, valor: int | None, tipo_operacao: str = "",
                contexto: dict | None = None) -> dict:
    ctx = contexto or {}
    t = m.normalizar_texto(descricao)
    tipo = m.normalizar_texto(tipo_operacao)
    docs = documentos_na_descricao(descricao)
    doc = docs[0] if docs else ""
    nome = nome_na_descricao(descricao)

    if valor is None:
        return _sug(m.NAO_IDENTIFICADO, "valor ilegível — conferir no "
                    "extrato", m.BAIXA, "SEM_VALOR", doc, nome)
    entra = valor > 0

    # 1 · a própria empresa do outro lado
    propria = m.so_digitos(ctx.get("empresa", ""))
    if doc and propria and (doc == propria or (len(doc) == 14 and len(propria) == 14
                                               and doc[:8] == propria[:8])):
        return _sug(m.TRANSFERENCIA, "NÃO É RECEITA NEM DESPESA — mesma "
                    "titularidade", m.ALTA, "DOC_DA_PROPRIA_EMPRESA", doc, nome)
    if _tem(t, "ENTRE CONTAS", "MESMA TITULARIDADE", "MESMA TITUL", "MESMA TIT",
            "CONTA PROPRIA", "TRANSF PROPRIA", "TRANSFERENCIA PROPRIA",
            "TRANSF CC", "TRANSF CONTAS"):
        return _sug(m.TRANSFERENCIA, "NÃO É RECEITA NEM DESPESA", m.ALTA,
                    "PALAVRA_TRANSFERENCIA", doc, nome)

    # 2 · palavras de sentido único
    if _tem(t, "IOF"):
        return _sug(m.IOF, "DESPESA FINANCEIRA — IOF", m.ALTA, "PALAVRA_IOF")
    if _tem(t, "TARIFA", "TAR", "CESTA", "PACOTE SERVICOS", "MANUTENCAO CONTA",
            "MANUT CONTA", "TX MANUT", "ANUIDADE") or tipo in ("FEE", "SRVCHG"):
        if entra:
            return _sug(m.OUTROS, "ESTORNO DE TARIFA", m.MEDIA,
                        "TARIFA_A_CREDITO")
        return _sug(m.TARIFA, "DESPESA BANCÁRIA", m.ALTA, "PALAVRA_TARIFA")
    if _tem(t, "JUROS", "ENCARGOS", "MORA", "JUROS CHEQUE ESPECIAL",
            "JUROS LIMITE"):
        return _sug(m.JUROS, "RECEITA FINANCEIRA" if entra
                    else "DESPESA FINANCEIRA", m.ALTA, "PALAVRA_JUROS")
    if _tem(t, "RENDIMENTO", "RENDIMENTOS", "REND PAGO", "REMUNERACAO APLIC"):
        return _sug(m.JUROS, "RECEITA FINANCEIRA — rendimento", m.MEDIA,
                    "PALAVRA_RENDIMENTO")
    # "DAS" sozinho é também a preposição ("LOJA DAS FLORES"): só vale no
    # começo da descrição ou colado ao que o identifica como guia.
    if t.startswith("DAS ") or _tem(t, "GUIA DAS", "PGTO DAS", "PAGTO DAS",
                                    "DAS SIMPLES", "DAS MEI") or \
            _tem(t, "DARF", "SIMPLES NACIONAL", "GPS", "FGTS", "GRF", "DAE",
            "DARE", "GARE", "ICMS", "ISS", "ISSQN", "IPTU", "IPVA", "TRIBUTO",
            "TRIBUTOS", "IMPOSTO", "IMPOSTOS", "SEFAZ", "RECEITA FEDERAL",
            "INSS", "DAM", "GNRE"):
        return _sug(m.TRIBUTOS, "PAGAMENTO DE TRIBUTO" if not entra
                    else "RESTITUIÇÃO/ESTORNO DE TRIBUTO", m.ALTA,
                    "PALAVRA_TRIBUTO")
    if _tem(t, "SALARIO", "SALARIOS", "FOLHA", "PAG FOLHA", "PAGTO FOLHA",
            "PROVENTOS", "FERIAS", "RESCISAO", "13 SALARIO"):
        return _sug(m.SALARIO, "FOLHA DE PAGAMENTO", m.ALTA, "PALAVRA_SALARIO",
                    doc, nome)
    if _tem(t, "EMPRESTIMO", "FINANCIAMENTO", "CAPITAL DE GIRO", "CONSIGNADO",
            "PRONAMPE", "LIBERACAO CRED", "CRED EMPRESTIMO", "PARCELA EMP",
            "PARC EMPREST"):
        return _sug(m.EMPRESTIMO, "ENTRADA DE EMPRÉSTIMO" if entra
                    else "PAGAMENTO DE EMPRÉSTIMO", m.ALTA, "PALAVRA_EMPRESTIMO")
    if _tem(t, "PRO LABORE", "PROLABORE", "PRO-LABORE", "DISTRIBUICAO LUCROS",
            "DISTRIB LUCROS", "LUCROS", "APORTE", "INTEGRALIZACAO",
            "AUMENTO DE CAPITAL", "RETIRADA SOCIO"):
        return _sug(m.SOCIO, "APORTE DE SÓCIO" if entra else "RETIRADA DE SÓCIO",
                    m.ALTA, "PALAVRA_SOCIO", doc, nome)
    if _tem(t, "APLICACAO", "APLIC", "RESGATE", "RESG", "CDB", "LCI", "LCA",
            "POUPANCA", "FUNDO", "INVEST", "INVESTIMENTO", "COMPRA DE ATIVO",
            "CONSORCIO"):
        return _sug(m.INVESTIMENTO, "APLICAÇÃO/RESGATE — não é receita nem "
                    "despesa", m.MEDIA, "PALAVRA_INVESTIMENTO")

    # 3 · o acervo fiscal diz quem é
    clientes = ctx.get("clientes") or {}
    fornecedores = ctx.get("fornecedores") or {}
    if entra and doc and doc in clientes:
        return _sug(m.CLIENTE, "CLIENTE / RECEBIMENTO", m.ALTA,
                    "DOC_DE_CLIENTE_NO_ACERVO", doc, clientes[doc] or nome)
    if not entra and doc and doc in fornecedores:
        return _sug(m.FORNECEDOR, "FORNECEDOR / PAGAMENTO", m.ALTA,
                    "DOC_DE_FORNECEDOR_NO_ACERVO", doc, fornecedores[doc] or nome)
    if entra:
        achado = _procurar_nome(nome, clientes)
        if achado:
            return _sug(m.CLIENTE, "CLIENTE / RECEBIMENTO", m.MEDIA,
                        "NOME_DE_CLIENTE_NO_ACERVO", achado, nome)
    else:
        achado = _procurar_nome(nome, fornecedores)
        if achado:
            return _sug(m.FORNECEDOR, "FORNECEDOR / PAGAMENTO", m.MEDIA,
                        "NOME_DE_FORNECEDOR_NO_ACERVO", achado, nome)

    # 4 · o meio, o sentido e a cara da contraparte
    pj = bool(_PJ.search(t)) or len(doc) == 14
    if _tem(t, "FORNECEDOR", "FORNEC") and not entra:
        return _sug(m.FORNECEDOR, "FORNECEDOR / PAGAMENTO", m.MEDIA,
                    "PALAVRA_FORNECEDOR", doc, nome)
    if _tem(t, "BOLETO", "PAGTO TITULO", "PAG TIT", "PAGAMENTO TITULO",
            "COBRANCA", "LIQUIDACAO", "TITULO") or tipo == "PAYMENT":
        if entra:
            return _sug(m.BOLETO, "CLIENTE / RECEBIMENTO (cobrança)", m.MEDIA,
                        "BOLETO_A_CREDITO", doc, nome)
        return _sug(m.BOLETO, "PAGAMENTO DE BOLETO — identificar fornecedor",
                    m.MEDIA, "BOLETO_A_DEBITO", doc, nome)
    eh_pix = _tem(t, "PIX")
    eh_ted = _tem(t, "TED", "DOC", "TEF", "TRANSFERENCIA", "TRANSF") \
        or tipo == "XFER"
    if eh_pix or eh_ted:
        meio = m.PIX_RECEBIDO if (eh_pix and entra) else \
            m.PIX_ENVIADO if eh_pix else m.TED_TEF
        if pj and entra:
            return _sug(m.CLIENTE, "CLIENTE / RECEBIMENTO", m.MEDIA,
                        "PIX_TED_DE_EMPRESA" if eh_pix else "TED_DE_EMPRESA",
                        doc, nome)
        if pj and not entra:
            return _sug(m.FORNECEDOR, "FORNECEDOR / PAGAMENTO", m.MEDIA,
                        "PIX_TED_PARA_EMPRESA" if eh_pix else "TED_PARA_EMPRESA",
                        doc, nome)
        return _sug(meio, "RECEBIMENTO — identificar a origem" if entra
                    else "PAGAMENTO — identificar o destino", m.BAIXA,
                    "MEIO_SEM_CONTRAPARTE_CONHECIDA", doc, nome)

    # 5 · o que o OFX disse, quando o texto não disse nada
    if _tem(t, "CHEQUE", "CHQ") or tipo == "CHECK":
        return _sug(m.OUTROS, "CHEQUE — identificar", m.BAIXA, "CHEQUE", doc, nome)
    if _tem(t, "SAQUE") or tipo in ("ATM", "CASH"):
        return _sug(m.OUTROS, "SAQUE — identificar destino", m.BAIXA, "SAQUE")
    if _tem(t, "DEPOSITO", "DEP") or tipo in ("DEP", "DIRECTDEP"):
        return _sug(m.OUTROS, "DEPÓSITO — identificar origem", m.BAIXA,
                    "DEPOSITO", doc, nome)
    if _tem(t, "ESTORNO", "DEVOLUCAO", "DEVOL"):
        return _sug(m.OUTROS, "ESTORNO/DEVOLUÇÃO", m.BAIXA, "ESTORNO", doc, nome)
    if tipo == "INT":
        return _sug(m.JUROS, "RECEITA FINANCEIRA" if entra else
                    "DESPESA FINANCEIRA", m.BAIXA, "TRNTYPE_INT")
    return _sug(m.NAO_IDENTIFICADO, "", m.BAIXA, "NENHUMA_REGRA", doc, nome)
