# -*- coding: utf-8 -*-
"""modelo.py — o vocabulário do Contábil, e as duas regras de dinheiro e data.

VOCABULÁRIO FECHADO
    Status, origem, categoria e estado de conciliação são listas aqui, e
    `conferir_*` recusa o que não estiver nelas. Texto livre nesses campos vira
    dado sujo em seis meses — foi o que aconteceu com a `origem` da Situação
    Fiscal antes de ela virar lista.

DINHEIRO É INTEIRO, EM CENTAVOS
    Nunca `float`. Somar float em ordem diferente muda a última casa, e numa
    partida dobrada a última casa é a diferença entre "fecha" e "não fecha".
    O valor entra como texto ("1.234,56", "-49.90", "R$ 5.000"), vira
    `Decimal`, e é guardado como inteiro de centavos.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class Invalido(ValueError):
    """Entrada recusada. A mensagem vai para a tela: escrita para gente."""


# ── Lançamento ────────────────────────────────────────────────────────────
PENDENTE = "PENDENTE"
CONFIRMADO = "CONFIRMADO"
AJUSTADO = "AJUSTADO"
CANCELADO = "CANCELADO"
STATUS = (PENDENTE, CONFIRMADO, AJUSTADO, CANCELADO)

FISCAL = "FISCAL"
FOLHA = "FOLHA"
BANCO = "BANCO"
FINANCEIRO = "FINANCEIRO"
MANUAL = "MANUAL"
IMPORTACAO = "IMPORTACAO"
AJUSTE = "AJUSTE"
ORIGENS = (FISCAL, FOLHA, BANCO, FINANCEIRO, MANUAL, IMPORTACAO, AJUSTE)
ROTULO_ORIGEM = {IMPORTACAO: "IMPORTAÇÃO"}

DEBITO = "D"
CREDITO = "C"

# ── Plano de contas ───────────────────────────────────────────────────────
SINTETICA = "SINTETICA"
ANALITICA = "ANALITICA"
TIPOS_CONTA = (SINTETICA, ANALITICA)

DEVEDORA = "DEVEDORA"
CREDORA = "CREDORA"
NATUREZAS = (DEVEDORA, CREDORA)

ATIVO = "ATIVO"
PASSIVO = "PASSIVO"
PATRIMONIO_LIQUIDO = "PATRIMONIO_LIQUIDO"
RECEITA = "RECEITA"
CUSTO = "CUSTO"
DESPESA = "DESPESA"
COMPENSACAO = "COMPENSACAO"
GRUPOS = (ATIVO, PASSIVO, PATRIMONIO_LIQUIDO, RECEITA, CUSTO, DESPESA,
          COMPENSACAO)
# Os grupos de RESULTADO. Transferência entre contas nunca pode ter
# contrapartida aqui: seria receita ou despesa que não aconteceu.
GRUPOS_RESULTADO = (RECEITA, CUSTO, DESPESA)

# ── Movimento bancário: categorias operacionais (SUGESTÃO) ───────────────
PIX_RECEBIDO = "PIX_RECEBIDO"
PIX_ENVIADO = "PIX_ENVIADO"
TED_TEF = "TED_TEF"
TRANSFERENCIA = "TRANSFERENCIA_ENTRE_CONTAS"
BOLETO = "BOLETO"
TARIFA = "TARIFA_BANCARIA"
JUROS = "JUROS"
IOF = "IOF"
SALARIO = "SALARIO"
TRIBUTOS = "TRIBUTOS"
FORNECEDOR = "FORNECEDOR"
CLIENTE = "CLIENTE"
EMPRESTIMO = "EMPRESTIMO"
SOCIO = "APORTE_RETIRADA_SOCIO"
INVESTIMENTO = "INVESTIMENTO_IMOBILIZADO"
OUTROS = "OUTROS"
NAO_IDENTIFICADO = "NAO_IDENTIFICADO"
CATEGORIAS = (PIX_RECEBIDO, PIX_ENVIADO, TED_TEF, TRANSFERENCIA, BOLETO,
              TARIFA, JUROS, IOF, SALARIO, TRIBUTOS, FORNECEDOR, CLIENTE,
              EMPRESTIMO, SOCIO, INVESTIMENTO, OUTROS, NAO_IDENTIFICADO)
ROTULO_CATEGORIA = {
    PIX_RECEBIDO: "PIX RECEBIDO", PIX_ENVIADO: "PIX ENVIADO",
    TED_TEF: "TED/TEF", TRANSFERENCIA: "TRANSFERÊNCIA ENTRE CONTAS",
    BOLETO: "BOLETO", TARIFA: "TARIFA BANCÁRIA", JUROS: "JUROS", IOF: "IOF",
    SALARIO: "SALÁRIO", TRIBUTOS: "TRIBUTOS", FORNECEDOR: "FORNECEDOR",
    CLIENTE: "CLIENTE", EMPRESTIMO: "EMPRÉSTIMO",
    SOCIO: "APORTE/RETIRADA DE SÓCIO",
    INVESTIMENTO: "INVESTIMENTO/IMOBILIZADO", OUTROS: "OUTROS",
    NAO_IDENTIFICADO: "NÃO IDENTIFICADO",
}

ALTA, MEDIA, BAIXA = "ALTA", "MEDIA", "BAIXA"

# ── Fato contábil (Contábil 2) ────────────────────────────────────────────
# O que um documento fiscal SIGNIFICA para a contabilidade. Não é
# classificação fiscal: a fiscal é lida de `ingestao.vendas` (CFOP) e do
# sentido que a normalização apurou, e entra aqui como `natureza_fiscal`.
RECEITA_SERVICO = "RECEITA_SERVICO"
RECEITA_VENDA = "RECEITA_VENDA"
COMPRA_MERCADORIA = "COMPRA_MERCADORIA"
COMPRA_IMOBILIZADO = "COMPRA_IMOBILIZADO"
SERVICO_TOMADO = "SERVICO_TOMADO"
FRETE_TOMADO = "FRETE_TOMADO"
DEVOLUCAO_VENDA = "DEVOLUCAO_VENDA"
DEVOLUCAO_COMPRA = "DEVOLUCAO_COMPRA"
TRANSFERENCIA_ESTABELECIMENTO = "TRANSFERENCIA_ESTABELECIMENTO"
OUTRA_ENTRADA = "OUTRA_ENTRADA"
OUTRA_SAIDA = "OUTRA_SAIDA"
SEM_EFEITO = "SEM_EFEITO"            # cancelada, denegada, substituída
INDETERMINADO = "INDETERMINADO"
TIPOS_FATO = (RECEITA_SERVICO, RECEITA_VENDA, COMPRA_MERCADORIA,
              COMPRA_IMOBILIZADO, SERVICO_TOMADO, FRETE_TOMADO,
              DEVOLUCAO_VENDA, DEVOLUCAO_COMPRA,
              TRANSFERENCIA_ESTABELECIMENTO, OUTRA_ENTRADA, OUTRA_SAIDA,
              SEM_EFEITO, INDETERMINADO)
ROTULO_TIPO_FATO = {
    RECEITA_SERVICO: "Receita de serviço", RECEITA_VENDA: "Receita de venda",
    COMPRA_MERCADORIA: "Compra de mercadoria",
    COMPRA_IMOBILIZADO: "Compra de imobilizado / uso e consumo",
    SERVICO_TOMADO: "Serviço tomado", FRETE_TOMADO: "Frete tomado",
    DEVOLUCAO_VENDA: "Devolução de venda",
    DEVOLUCAO_COMPRA: "Devolução de compra",
    TRANSFERENCIA_ESTABELECIMENTO: "Transferência entre estabelecimentos",
    OUTRA_ENTRADA: "Outra entrada", OUTRA_SAIDA: "Outra saída",
    SEM_EFEITO: "Sem efeito fiscal", INDETERMINADO: "Indeterminado",
}

SEM_LANCAMENTO = "SEM_LANCAMENTO"
LANCADO = "LANCADO"
IGNORADO = "IGNORADO"
ESTADOS_FATO = (SEM_LANCAMENTO, LANCADO, IGNORADO, SEM_EFEITO)

# ── Papéis de conta ───────────────────────────────────────────────────────
# A sugestão de lançamento fala em PAPEL ("clientes", "fornecedores"), nunca
# em código de conta: o plano é de cada empresa, e o Contábil não inventa
# conta nem adivinha qual é qual. Quem liga papel a conta é uma pessoa, uma
# vez, em Plano de Contas → Contas padrão. Sem o papel mapeado, o fato fica
# SEM_LANCAMENTO dizendo qual papel falta — nunca com um palpite.
PAPEIS = {
    "CLIENTES": ((ATIVO,), "Clientes / contas a receber"),
    "FORNECEDORES": ((PASSIVO,), "Fornecedores / contas a pagar"),
    "RECEITA_SERVICOS": ((RECEITA,), "Receita de serviços"),
    "RECEITA_VENDAS": ((RECEITA,), "Receita de vendas"),
    "MERCADORIAS": ((ATIVO, CUSTO, DESPESA), "Compras / estoque de mercadorias"),
    "IMOBILIZADO": ((ATIVO,), "Imobilizado e material de uso e consumo"),
    "SERVICOS_TOMADOS": ((CUSTO, DESPESA), "Serviços tomados de terceiros"),
    "FRETES": ((CUSTO, DESPESA), "Fretes e carretos"),
    "DEVOLUCOES_VENDAS": ((RECEITA, DESPESA), "Devoluções de vendas"),
    "DEVOLUCOES_COMPRAS": ((ATIVO, CUSTO, DESPESA, RECEITA),
                           "Devoluções de compras"),
    "RETENCOES_A_RECUPERAR": ((ATIVO,), "Tributos retidos a recuperar"),
}

# ── Estado da competência (fechamento) ────────────────────────────────────
ABERTA = "ABERTA"
EM_CONFERENCIA = "EM_CONFERENCIA"
FECHADA = "FECHADA"
ESTADOS_COMPETENCIA = (ABERTA, EM_CONFERENCIA, FECHADA)

ENTRADA = "ENTRADA"
SAIDA = "SAIDA"

# ── Conciliação ───────────────────────────────────────────────────────────
CONCILIADO = "CONCILIADO"
SUGESTAO = "SUGESTAO"
PENDENTE_CONC = "PENDENTE"
DIVERGENTE = "DIVERGENTE"
ESTADOS_CONCILIACAO = (CONCILIADO, SUGESTAO, PENDENTE_CONC, DIVERGENTE)

ALVO_LANCAMENTO = "LANCAMENTO"
ALVO_DOCUMENTO = "DOCUMENTO_FISCAL"
ALVOS = (ALVO_LANCAMENTO, ALVO_DOCUMENTO)


def conferir(valor: str, lista, nome: str) -> str:
    v = (valor or "").strip().upper()
    if v not in lista:
        raise Invalido("%s fora da lista: %r (aceitos: %s)"
                       % (nome, valor, ", ".join(lista)))
    return v


# ── Dinheiro ──────────────────────────────────────────────────────────────
_LIMPA_VALOR = re.compile(r"[^\d,.\-+()]")


def centavos(texto, decimal_ponto: bool | None = None) -> int:
    """Texto de dinheiro → inteiro de centavos, com sinal.

    `decimal_ponto`:
        True   o ponto é o separador decimal (OFX: "-49.90");
        False  a vírgula é o decimal e o ponto é milhar ("1.234,56");
        None   decide pelo texto: com os dois, o ÚLTIMO é o decimal; só
               vírgula, ela é o decimal; só ponto, é decimal a menos que
               tenha exatamente três dígitos depois ("1.234" é mil).

    Aceita "R$", parênteses como negativo, sinal no fim ("49,90-") e as
    letras D/C no fim ("49,90 D"). Qualquer outra coisa é recusada — um
    valor adivinhado é pior que um valor recusado.
    """
    if isinstance(texto, int):
        return texto
    if isinstance(texto, Decimal):
        return int((texto * 100).quantize(Decimal("1"), ROUND_HALF_UP))
    s = str(texto or "").strip()
    if not s:
        raise Invalido("valor vazio")
    negativo = False
    sufixo = s[-1:].upper()
    if sufixo in ("D", "C") and len(s) > 1 and not s[-2:-1].isalpha():
        negativo = sufixo == "D"
        s = s[:-1].strip()
    s = s.replace("R$", "").replace(" ", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    if s.endswith("-"):
        negativo, s = True, s[:-1]
    if s.startswith("-"):
        negativo, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if not s or _LIMPA_VALOR.sub("", s) != s or "-" in s or "+" in s \
            or "(" in s:
        raise Invalido("valor ilegível: %r" % (texto,))
    if decimal_ponto is None:
        if "," in s and "." in s:
            decimal_ponto = s.rfind(".") > s.rfind(",")
        elif "," in s:
            decimal_ponto = False
        elif "." in s:
            decimal_ponto = not (s.count(".") >= 1
                                 and len(s.rsplit(".", 1)[1]) == 3)
        else:
            decimal_ponto = True
    if decimal_ponto:
        s = s.replace(",", "")
    else:
        s = s.replace(".", "").replace(",", ".")
    if s.count(".") > 1:
        raise Invalido("valor ilegível: %r" % (texto,))
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise Invalido("valor ilegível: %r" % (texto,))
    c = int((d * 100).quantize(Decimal("1"), ROUND_HALF_UP))
    return -c if negativo else c


def reais(c: int | None) -> str:
    """Centavos → "1.234,56" (com sinal). Só para mensagens e tela."""
    if c is None:
        return ""
    sinal = "-" if c < 0 else ""
    c = abs(int(c))
    inteiro = "{:,}".format(c // 100).replace(",", ".")
    return "%s%s,%02d" % (sinal, inteiro, c % 100)


# ── Datas ─────────────────────────────────────────────────────────────────
def data_iso(texto) -> str:
    """Aceita AAAA-MM-DD, DD/MM/AAAA, DD/MM/AA, DD-MM-AAAA, DD.MM.AAAA."""
    if isinstance(texto, datetime):
        return texto.date().isoformat()
    if isinstance(texto, date):
        return texto.isoformat()
    s = str(texto or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y",
                "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise Invalido("data ilegível: %r" % (texto,))


def competencia(texto) -> str:
    """AAAA-MM. Aceita também MM/AAAA e uma data inteira."""
    s = str(texto or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if not m:
        m2 = re.fullmatch(r"(\d{2})/(\d{4})", s)
        if m2:
            m = re.fullmatch(r"(\d{4})-(\d{2})", "%s-%s" % (m2.group(2), m2.group(1)))
    if m:
        if not 1 <= int(m.group(2)) <= 12:
            raise Invalido("competência com mês inválido: %r" % (texto,))
        return "%s-%s" % (m.group(1), m.group(2))
    return data_iso(s)[:7]


# ── Texto ─────────────────────────────────────────────────────────────────
def normalizar_texto(s: str) -> str:
    """Maiúsculas, sem acento, espaços colapsados. Para comparar, nunca
    para guardar: a descrição do banco é guardada exatamente como veio."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.upper()).strip()


def so_digitos(s) -> str:
    return "".join(c for c in str(s or "") if c.isdigit())


def cnpj_valido(d: str) -> bool:
    d = so_digitos(d)
    if len(d) != 14 or d == d[0] * 14:
        return False
    def dv(base, pesos):
        s = sum(int(a) * b for a, b in zip(base, pesos)) % 11
        return "0" if s < 2 else str(11 - s)
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1
    return d[12] == dv(d[:12], p1) and d[13] == dv(d[:13], p2)


def cpf_valido(d: str) -> bool:
    d = so_digitos(d)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        s = sum(int(d[i]) * (n + 1 - i) for i in range(n)) * 10 % 11
        if str(s % 10) != d[n]:
            return False
    return True


def empresa_valida(identidade) -> str:
    """O documento da empresa: 14 dígitos (CNPJ) ou 11 (CPF). Sem isso não
    há banco — e sem banco não há como misturar empresas."""
    d = so_digitos(identidade)
    if len(d) not in (11, 14):
        raise Invalido("empresa sem documento válido: %r" % (identidade,))
    return d
