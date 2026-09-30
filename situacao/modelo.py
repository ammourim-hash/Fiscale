# -*- coding: utf-8 -*-
"""modelo.py — o vocabulário da situação fiscal, e as decisões que ele carrega.

ESTE MÓDULO NÃO TOCA DISCO NEM REDE.
    Ele diz o que as coisas são e deriva estado a partir de datas. Tudo o que
    grava está no `armazenamento`; tudo o que busca está em `fontes/`.

DUAS COISAS COM A MESMA FORMA, E UMA DIFERENÇA QUE NÃO PODE SER ACHATADA
    Certidão e extrato são ambos "documento oficial + esfera + instante + hash
    + proveniência", e por isso moram no mesmo envelope. Mas:

      • a CERTIDÃO tem VALIDADE — ela vale até uma data que o órgão carimbou;
      • o EXTRATO é RETRATO DE UM INSTANTE — não vence, envelhece.

    Derivar "vencida" para um extrato seria inventar um carimbo que ninguém
    deu. Derivar "apurado há 40 dias" para uma certidão seria esconder que ela
    ainda vale. Por isso são duas funções, e não uma com `if`.

ESTADO É DERIVADO, NUNCA GRAVADO
    "Vencida" é função da validade e da data de hoje. Uma linha gravada como
    "Válida" passa a mentir na virada do dia seguinte, e ninguém percebe —
    porque a linha continua lá, afirmando com a mesma confiança de antes.

    A mesma regra do índice do acervo: o documento é a verdade, o resto é
    conveniência reconstruível.

A VALIDADE NUNCA É CALCULADA
    A documentação da Consulta CND diz 180 dias. Das amostras reais colhidas em
    07/09/2026, uma deu exatamente 180 e outra deu **194**. Calcular
    `emissão + 180` mostraria a certidão vencida duas semanas antes da hora.

    Aqui não há aritmética de validade: o que o órgão carimbou é o que vale, e
    quando não veio carimbo o estado é `SEM_VALIDADE`, que é honesto.
"""
from __future__ import annotations

import datetime as _dt

# ── Esferas ──────────────────────────────────────────────────────────────
FEDERAL = "FEDERAL"
ESTADUAL = "ESTADUAL"
MUNICIPAL = "MUNICIPAL"
ESFERAS = (FEDERAL, ESTADUAL, MUNICIPAL)

# ── Tipos de documento ───────────────────────────────────────────────────
CERTIDAO = "CERTIDAO"
EXTRATO = "EXTRATO"
TIPOS = (CERTIDAO, EXTRATO)

# ── Natureza (só faz sentido para certidão) ──────────────────────────────
#     A CPD-EN não é detalhe: ela vale como negativa para licitação e crédito,
#     mas diz que HÁ débito, com exigibilidade suspensa. Colapsar as duas em
#     "regular" apaga informação que decide negócio.
NEGATIVA = "NEGATIVA"
CPD_EN = "POSITIVA_COM_EFEITOS_DE_NEGATIVA"
POSITIVA = "POSITIVA"
NAO_LIDA = "NAO_LIDA"           # o documento existe; o parser não o entendeu
NATUREZAS = (NEGATIVA, CPD_EN, POSITIVA, NAO_LIDA)

# Da API Consulta CND, confirmado pelo TEXTO dos PDFs colhidos — não pelo
# número, e não pela documentação.
NATUREZA_POR_TIPOCERTIDAO = {1: NEGATIVA, 2: CPD_EN}

# ── Estado derivado ──────────────────────────────────────────────────────
SEM_DOCUMENTO = "SEM_DOCUMENTO"
VALIDA = "VALIDA"
VENCE_EM_BREVE = "VENCE_EM_BREVE"
VENCIDA = "VENCIDA"
SEM_VALIDADE = "SEM_VALIDADE"       # documento guardado, validade não lida
APURADO = "APURADO"
DESATUALIZADO = "DESATUALIZADO"

DIAS_AVISO = 30         # janela em que a certidão entra em "vence em breve"
DIAS_FRESCOR = 30       # depois disso, o extrato é retrato velho

# ── Desfecho de TENTATIVA ────────────────────────────────────────────────
#     Tentativa e documento são coisas diferentes, e é por isso que "erro" não
#     é estado de certidão. "Não emitida" chega com HTTP 200 e sem documento:
#     é desfecho legítimo de negócio, não falha de sistema. Se morasse na mesma
#     tabela do documento, toda recusa criaria uma certidão fantasma — uma
#     linha afirmando a existência de algo que não existe em lugar nenhum.
OBTIDA = "OBTIDA"
NAO_EMITIDA = "NAO_EMITIDA"         # há pendência; o órgão recusa emitir
RECUSADA = "RECUSADA"               # credencial, procuração, parâmetro
INDISPONIVEL = "INDISPONIVEL"       # órgão fora do ar, limite de taxa
ILEGIVEL = "ILEGIVEL"               # veio algo que não é o documento
ERRO = "ERRO"                       # o que não coube acima
DESFECHOS = (OBTIDA, NAO_EMITIDA, RECUSADA, INDISPONIVEL, ILEGIVEL, ERRO)


# ── Método da consulta (auditoria) ──────────────────────────────────────
#     COMO o documento chegou, que é diferente de DE ONDE (a origem). O mesmo
#     extrato do Recife pode entrar pela bandeja ou pelo formulário, e a
#     pergunta "quem conferiu isto à mão?" só tem resposta se o método ficar.
METODO_INTEGRACAO = "INTEGRACAO"     # conector oficial, sem mão humana
METODO_UPLOAD = "UPLOAD"             # a pessoa escolheu o arquivo
METODO_BANDEJA = "BANDEJA"           # a pasta vigiada ofereceu, a pessoa confirmou
METODOS = (METODO_INTEGRACAO, METODO_UPLOAD, METODO_BANDEJA)


class Invalido(ValueError):
    """Rótulo fora do vocabulário. Falha alto, na entrada, e não no relatório."""


# ── Origens ──────────────────────────────────────────────────
#     POR QUE ISTO DEIXOU DE SER TEXTO LIVRE
#         Enquanto havia um conector só, `origem` era "SITFIS" ou "ASSISTIDA"
#         e ninguém errava. Com quatro canais entrando pela bandeja, texto
#         livre vira dado sujo em seis meses: "RECIFE", "Recife", "recife" e
#         "PREF_RECIFE" seriam quatro origens distintas no índice, e a pergunta
#         "de onde vieram os documentos deste mês?" passaria a ter quatro
#         respostas certas e nenhuma útil.
#
#     A origem é PROVENÊNCIA, e proveniência sem vocabulário é anótacao.
SITFIS = "SITFIS"                        # conector oficial, Integra Contador
MANUAL = "MANUAL"                        # a pessoa escolheu o arquivo no formulário
ASSISTIDA_RECIFE = "ASSISTIDA_RECIFE"    # baixado do portal do Recife, pela bandeja
ASSISTIDA_SEFAZ_PE = "ASSISTIDA_SEFAZ_PE"
ASSISTIDA_ECAC = "ASSISTIDA_ECAC"
# A CND vem de OUTRO produto SERPRO, bilhetado à parte, e por isso é origem
# própria: saber que um documento custou consulta paga é o que permite,
# depois, perguntar quanto o ciclo do mês gastou. Ela não existe como conector
# ainda — o vocabulário nasce completo porque a primeira versão desta lista
# esqueceu justamente dela, e a suíte do índice acusou na hora.
CONSULTA_CND = "CONSULTA_CND"

ORIGENS = (SITFIS, CONSULTA_CND, MANUAL, ASSISTIDA_RECIFE,
           ASSISTIDA_SEFAZ_PE, ASSISTIDA_ECAC)

# APELIDOS DE COMPATIBILIDADE, QUE NÃO PODEM SUMIR.
#     `captura.json` é imutável por contrato: o que foi gravado como
#     "ASSISTIDA" continuará dizendo "ASSISTIDA" para sempre no disco. Reescrever
#     o arquivo para "arrumar" o vocabulário quebraria a promessa central do
#     envelope. Então o apelido é resolvido na LEITURA, e nada no disco muda.
APELIDOS = {"ASSISTIDA": MANUAL}


def conferir_origem(origem: str) -> str:
    """Normaliza a origem. Levanta `Invalido` se não for do vocabulário.

    Vazio vira `MANUAL`: quem não declarou origem foi alguém entregando um
    arquivo à mão, que é o único caminho sem conector.
    """
    o = (origem or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not o:
        return MANUAL
    o = APELIDOS.get(o, o)
    if o not in ORIGENS:
        raise Invalido("origem desconhecida: %r (esperado %s)"
                       % (origem, "/".join(ORIGENS)))
    return o


def origem_legivel(origem: str) -> str:
    """Como a origem aparece para uma pessoa. Nunca levanta."""
    o = (origem or "").strip().upper()
    o = APELIDOS.get(o, o)
    return {
        SITFIS: "SITFIS (Integra Contador)",
        CONSULTA_CND: "Consulta CND (SERPRO)",
        MANUAL: "entregue à mão",
        ASSISTIDA_RECIFE: "portal do Recife",
        ASSISTIDA_SEFAZ_PE: "e-Fisco (SEFAZ-PE)",
        ASSISTIDA_ECAC: "e-CAC (RFB)",
    }.get(o, origem or "—")


def conferir(esfera: str, tipo: str) -> tuple:
    """Normaliza e valida o par `(esfera, tipo)`. Levanta `Invalido`.

    Existe para que um typo vire exceção AQUI, e não uma pasta nova chamada
    `Fedaral` que ninguém encontra depois.
    """
    e = (esfera or "").strip().upper()
    t = (tipo or "").strip().upper()
    if e not in ESFERAS:
        raise Invalido("esfera desconhecida: %r (esperado %s)"
                       % (esfera, "/".join(ESFERAS)))
    if t not in TIPOS:
        raise Invalido("tipo desconhecido: %r (esperado %s)"
                       % (tipo, "/".join(TIPOS)))
    return e, t


def _data(valor):
    """Aceita `date`, `datetime` ou ISO; devolve `date` ou `None`.

    NÃO INVENTA DATA. Texto que não é data vira `None`, e `None` vira
    `SEM_VALIDADE` lá na frente — que é o estado honesto para "o documento está
    aqui, mas eu não sei até quando ele vale".
    """
    if valor is None or valor == "":
        return None
    if isinstance(valor, _dt.datetime):
        return valor.date()
    if isinstance(valor, _dt.date):
        return valor
    texto = str(valor).strip()
    try:
        return _dt.date.fromisoformat(texto[:10])
    except ValueError:
        return None


def estado_certidao(validade, hoje=None, dias_aviso: int = DIAS_AVISO) -> str:
    """`VALIDA` · `VENCE_EM_BREVE` · `VENCIDA` · `SEM_VALIDADE` · `SEM_DOCUMENTO`.

    O dia da validade AINDA VALE. A certidão diz "válida até", e até inclui o
    dia — tratar o último dia como vencido faria o sistema mandar renovar uma
    certidão que o cliente ainda pode apresentar.
    """
    if validade is _SEM:
        return SEM_DOCUMENTO
    d = _data(validade)
    if d is None:
        return SEM_VALIDADE
    h = _data(hoje) or _dt.date.today()
    if d < h:
        return VENCIDA
    if (d - h).days <= dias_aviso:
        return VENCE_EM_BREVE
    return VALIDA


def estado_extrato(apurado_em, hoje=None, dias_frescor: int = DIAS_FRESCOR) -> str:
    """`APURADO` · `DESATUALIZADO` · `SEM_VALIDADE` · `SEM_DOCUMENTO`.

    Extrato não vence — envelhece. `DESATUALIZADO` é POLÍTICA NOSSA (quanto
    tempo aceitamos olhar para um retrato antigo), não carimbo de órgão, e é
    por isso que o prazo é parâmetro e não constante enterrada.
    """
    if apurado_em is _SEM:
        return SEM_DOCUMENTO
    d = _data(apurado_em)
    if d is None:
        return SEM_VALIDADE
    h = _data(hoje) or _dt.date.today()
    return APURADO if (h - d).days <= dias_frescor else DESATUALIZADO


class _Sentinela:
    """Marca 'não existe documento nenhum' — diferente de 'existe, sem data'."""
    def __repr__(self):
        return "SEM_DOCUMENTO"


_SEM = _Sentinela()
SEM = _SEM          # nome público para quem chama de fora


def regular(natureza: str) -> bool:
    """A empresa está regular NESTA esfera?

    CPD-EN conta como regular: ela vale como negativa. `NAO_LIDA` não conta —
    "não sei" nunca é "sim".
    """
    return natureza in (NEGATIVA, CPD_EN)
