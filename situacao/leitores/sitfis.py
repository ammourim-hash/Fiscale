# -*- coding: utf-8 -*-
"""sitfis.py — lê o Relatório de Situação Fiscal da RFB/PGFN.

O QUE FOI CALIBRADO CONTRA AMOSTRA REAL, E O QUE NÃO
    Os RÓTULOS vêm de um PDF capturado do trial do Integra Contador em
    07/09/2026 (`fixturas/sitfis/emitir-sucesso.json`). Ele traz o layout de
    verdade, com os VALORES mascarados pelo próprio SERPRO:

        INFORMAÇÕES DE APOIO PARA EMISSÃO DE CERTIDÃO
        CPF: 999.999.999-99 – ZZZZZZ ZZZZZZ
        ...
        Certidão Emitida ____________________________________
        Certidão Negativa:  ZZZZ.ZZZZ.ZZZZ.ZZZZ Emissão: 99/99/9999 Data de Validade: 99/99/9999
        ____ Diagnóstico Fiscal na Receita Federal e Procuradoria-Geral ____
        Não foram detectadas pendências/exigibilidades suspensas nos controles...

    Então: onde os campos ficam e como se chamam está conferido contra o órgão.
    A CONVERSÃO das datas não pôde ser: `99/99/9999` não é data. Ela é testada
    à parte, com o mesmo layout e datas plausíveis, e isso está dito aqui para
    ninguém supor uma calibragem que não houve.

    Também não há amostra de relatório COM pendências: o trial devolve sempre
    o mesmo PDF limpo. A frase de ausência é reconhecida; a lista de pendências
    não é interpretada, e `sem_pendencias` fica ausente em vez de virar `False`
    por dedução.

O RELATÓRIO É UM EXTRATO, E A DATA DELE É A DO CABEÇALHO
    O carimbo `99/99/9999 99:99:99` no topo é QUANDO o retrato foi tirado — é
    o `apurado_em`. A validade que aparece mais abaixo é da CERTIDÃO que o
    relatório menciona, e não deste documento: um extrato não vence.

    Confundir as duas colocaria uma data de vencimento num documento que não
    tem vencimento, e faria a tela mentir exatamente onde o modelo separa.

ESTE MÓDULO NUNCA LEVANTA. Ver o contrato em `leitores/__init__.py`.
"""
from __future__ import annotations

import datetime as _dt
import io
import re

from .. import modelo

VERSAO = 1

# Assinatura do documento: se isto não estiver no texto, não é um SITFIS, e
# devolver campos "lidos" de outro PDF qualquer seria pior que devolver nada.
ASSINATURA = "INFORMAÇÕES DE APOIO PARA EMISSÃO DE CERTIDÃO"

# O carimbo do relatório, no cabeçalho.
RE_APURADO = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})")

# A linha da certidão mencionada. O rótulo antes dos dois-pontos é a NATUREZA.
RE_CERTIDAO = re.compile(
    r"Certid[ãa]o\s+(Negativa|Positiva\s+com\s+Efeitos\s+de\s+Negativa|Positiva)"
    r"\s*:\s*([0-9A-Z.]{4,40})?"
    r"(?:.*?Emiss[ãa]o\s*:\s*(\d{2}/\d{2}/\d{4}))?"
    r"(?:.*?Data\s+de\s+Validade\s*:\s*(\d{2}/\d{2}/\d{4}))?",
    re.I | re.S)

SEM_PENDENCIA = "não foram detectadas pendências"

NATUREZAS = {
    "negativa": modelo.NEGATIVA,
    "positiva com efeitos de negativa": modelo.CPD_EN,
    "positiva": modelo.POSITIVA,
}


def _texto(bruto: bytes) -> str:
    """Todo o texto do PDF, ou vazio. Não levanta.

    O `pypdf` reclama no log de todo arquivo que não é PDF ("invalid pdf
    header", "EOF marker not found"). Este leitor RECEBE lixo de propósito —
    é a política —, então o ruído seria constante, e console sempre sujo é
    onde um erro de verdade passa despercebido. O silêncio vale só durante a
    leitura, e é devolvido logo depois.
    """
    try:
        import logging
        import pypdf
    except ImportError:
        return ""
    ruidoso = logging.getLogger("pypdf")
    antes = ruidoso.level
    ruidoso.setLevel(logging.CRITICAL)
    try:
        leitor = pypdf.PdfReader(io.BytesIO(bruto))
        return "\n".join((p.extract_text() or "") for p in leitor.pages)
    except Exception:
        return ""
    finally:
        ruidoso.setLevel(antes)


def _data_br(texto: str) -> str:
    """`dd/mm/aaaa` -> `aaaa-mm-dd`, ou `""`.

    O `99/99/9999` das amostras mascaradas cai aqui e vira vazio — que é
    exatamente o que se quer: campo não lido some, e o estado vira
    `SEM_VALIDADE` em vez de uma data inventada.
    """
    if not texto:
        return ""
    try:
        d = _dt.datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return ""
    # Ano fora do razoável é erro de leitura, não data. `9999` passaria pelo
    # strptime se o dia e o mês fossem válidos.
    if not (1990 <= d.year <= 2100):
        return ""
    return d.isoformat()


def ler(bruto: bytes) -> dict:
    """Os campos que dá para ler. Ausência significa 'não sei'."""
    texto = _texto(bruto)
    if not texto or ASSINATURA.lower() not in texto.lower():
        return {}

    achado = {"leitura_versao": VERSAO}

    m = RE_APURADO.search(texto)
    if m:
        dia = _data_br(m.group(1))
        if dia:
            achado["apurado_em"] = dia

    m = RE_CERTIDAO.search(texto)
    if m:
        chave = " ".join(m.group(1).split()).lower()
        natureza = NATUREZAS.get(chave)
        if natureza:
            achado["natureza"] = natureza
        codigo = (m.group(2) or "").strip(" .")
        # Código todo mascarado (só Z, X ou 9) não é código.
        if codigo and not re.fullmatch(r"[ZX9.\-]+", codigo):
            achado["codigo"] = codigo
        # EMISSÃO E VALIDADE SÃO DA CERTIDÃO CITADA, NÃO DESTE DOCUMENTO.
        #     Vão com nome próprio para que quem gravar no índice não as
        #     confunda com a validade do extrato — que não existe.
        emissao = _data_br(m.group(3) or "")
        validade = _data_br(m.group(4) or "")
        if emissao:
            achado["certidao_emissao"] = emissao
        if validade:
            achado["certidao_validade"] = validade

    if SEM_PENDENCIA in texto.lower():
        achado["sem_pendencias"] = True
    # Não há `else`: sem amostra de relatório COM pendências, deduzir `False`
    # da ausência da frase seria afirmar o que não se observou.

    # Só a versão não é leitura nenhuma. Devolver `{}` deixa claro que o
    # documento não rendeu campo algum.
    return achado if len(achado) > 1 else {}
