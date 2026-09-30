"""modelo.py — as entidades de plano de saúde.

Decisões que valem a pena registrar aqui, porque explicam a forma das classes:

1. **Decimal, nunca float.** Dinheiro de fatura fecha na casa do centavo; float
   erra por arredondamento binário e a conferência passaria a acusar diferença
   onde não há (ou, pior, a esconder onde há). Todo valor entra por `dec()`.

2. **Não existe uma coluna "valor".** A operadora cobra em parcelas com natureza
   diferente (mensalidade, coparticipação, acerto, IOF) e o REINF futuro precisa
   saber qual é qual. Por isso `Cobranca` tem campos separados e `total` é
   derivado, não digitado.

3. **Nome não é chave.** Homônimo existe, e o mesmo nome muda de grafia entre
   competências ("MARIA E SILVA" x "MARIA EDUARDA SILVA"). A identidade sai
   de `chave_tecnica()`, que prefere CPF > matrícula/certificado > código da
   operadora, e só cai no nome quando não sobrou nada — marcando isso no
   `origem_chave` para a tela poder avisar.

4. **Privacidade.** Só entra o que a conferência de fatura exige: quem é,
   de que família, quanto custou, em que competência. Nada clínico
   (diagnóstico, procedimento, carência, guia) é capturado, mesmo quando o
   relatório traz.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENTAVO = Decimal("0.01")

# Diferença que a gente aceita sem apontar como problema. É o arredondamento
# legítimo de rateio (a operadora divide um prêmio por N vidas e sobra centavo).
# Deliberadamente minúsculo: tolerância grande esconde erro de verdade.
TOLERANCIA = Decimal("0.02")

STATUS = ("IMPORTADO", "LIDO", "CONFERIDO", "CONFERIR", "ERRO")

# Parentesco normalizado. Guardamos o texto original também — o relatório é a
# evidência, a normalização é conveniência de tela.
PARENTESCOS = ("TITULAR", "CONJUGE", "FILHO", "ENTEADO", "OUTRO")

_MAPA_PARENTESCO = {
    "TITULAR": "TITULAR", "TIT": "TITULAR", "": "TITULAR",
    "CONJUGE": "CONJUGE", "CONJ": "CONJUGE", "CÔNJUGE": "CONJUGE",
    "ESPOSA": "CONJUGE", "ESPOSO": "CONJUGE", "COMPANHEIRO": "CONJUGE",
    "COMPANHEIRA": "CONJUGE",
    "FILHOS": "FILHO", "FILHO": "FILHO", "FILHA": "FILHO", "FILH": "FILHO",
    "FILHO(A)": "FILHO", "DEPENDENTE": "OUTRO",
    "ENTEADO": "ENTEADO", "ENTEADA": "ENTEADO", "ENTE": "ENTEADO",
}


def dec(v) -> Decimal:
    """Converte para Decimal aceitando os formatos que aparecem nos relatórios.

    Aceita "R$ 1.234,56", "1234.56", "12,34", 1234, Decimal. Devolve 0 no que
    não for número — o parser é que decide se ausência de valor é erro, aqui
    não se inventa exceção para um campo em branco."""
    if isinstance(v, Decimal):
        return v.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    if v is None:
        return Decimal("0.00")
    if isinstance(v, int):
        return Decimal(v).quantize(CENTAVO)
    if isinstance(v, float):
        # float só chega aqui vindo de JSON antigo; passa por str para não
        # herdar o lixo binário (0.1 + 0.2).
        v = repr(v)
    s = str(v).strip()
    if not s:
        return Decimal("0.00")
    s = re.sub(r"[R$\s ]", "", s)
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = s.strip("()-")
    if "," in s:                     # pt-BR: ponto é milhar, vírgula é decimal
        s = s.replace(".", "").replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return Decimal("0.00")
    d = d.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    return -d if neg else d


def so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def cpf_valido(cpf) -> bool:
    """Dígitos verificadores do CPF. Relatório de operadora traz CPF errado com
    frequência (digitação, truncamento); marcar é melhor do que recusar."""
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for corte in (9, 10):
        soma = sum(int(d[i]) * (corte + 1 - i) for i in range(corte))
        dv = (soma * 10) % 11 % 10
        if dv != int(d[corte]):
            return False
    return True


def normaliza_cpf(cpf) -> str:
    """CPF com 11 dígitos. A SulAmérica exporta sem os zeros à esquerda, então
    um CPF que começa por zero chega com 10 dígitos — completar é recuperar o
    dado, não adivinhar."""
    d = so_digitos(cpf)
    if not d:
        return ""
    if len(d) < 11:
        d = d.rjust(11, "0")
    return d[:11] if len(d) == 11 else d


def normaliza_parentesco(texto) -> str:
    t = re.sub(r"[^A-ZÇÃÕÁÉÍÓÚÂÊÔÀ()]", "", (texto or "").upper())
    return _MAPA_PARENTESCO.get(t, "OUTRO" if t else "TITULAR")


def _nome_canonico(nome) -> str:
    """Só para comparar: maiúsculas, sem acento, espaço único."""
    n = (nome or "").upper()
    for de, para in (("ÁÀÂÃ", "A"), ("ÉÊ", "E"), ("Í", "I"), ("ÓÔÕ", "O"),
                     ("ÚÜ", "U"), ("Ç", "C")):
        for c in de:
            n = n.replace(c, para)
    return re.sub(r"[^A-Z ]", " ", n).strip()


@dataclass
class Cobranca:
    """As parcelas de UMA vida numa competência. Cada natureza em seu campo."""
    mensalidade: Decimal = Decimal("0.00")     # prêmio / mensalidade do plano
    coparticipacao: Decimal = Decimal("0.00")  # participação do segurado
    acertos: Decimal = Decimal("0.00")         # retroativos, estornos, proporcional
    outros: Decimal = Decimal("0.00")
    iof: Decimal = Decimal("0.00")             # em regra vem só no total da fatura

    @property
    def total(self) -> Decimal:
        return (self.mensalidade + self.coparticipacao + self.acertos
                + self.outros + self.iof).quantize(CENTAVO)

    def dict(self):
        d = {k: str(v) for k, v in asdict(self).items()}
        d["total"] = str(self.total)
        return d


@dataclass
class Beneficiario:
    """Uma vida. `parentesco == TITULAR` é quem encabeça a família."""
    nome: str = ""
    cpf: str = ""
    codigo: str = ""            # código de identificação / certificado / carteirinha
    matricula: str = ""         # ID funcional / matrícula na empresa
    plano: str = ""
    parentesco: str = "TITULAR"
    parentesco_original: str = ""
    nascimento: str = ""        # dd/mm/aaaa como veio; a tela formata
    inicio_vigencia: str = ""
    cobranca: Cobranca = field(default_factory=Cobranca)
    # rastro de qualidade do dado — a tela usa para avisar, não para recusar
    cpf_ok: bool = False
    origem_chave: str = ""      # "cpf" | "codigo" | "matricula" | "nome"
    avisos: list = field(default_factory=list)

    def chave_tecnica(self) -> str:
        """Identidade da pessoa entre competências. Nome é o último recurso."""
        if self.cpf_ok:
            return "cpf:" + self.cpf
        if self.codigo:
            return "cod:" + self.codigo
        if self.matricula and self.nome:
            return "mat:" + self.matricula + "|" + _nome_canonico(self.nome)
        if self.cpf:                      # CPF com DV inválido ainda identifica
            return "cpf?:" + self.cpf
        return "nome:" + _nome_canonico(self.nome)

    def dict(self):
        d = asdict(self)
        d["cobranca"] = self.cobranca.dict()
        d["chave"] = self.chave_tecnica()
        return d


@dataclass
class Familia:
    """Titular + dependentes. `total_declarado` é o que o relatório afirma;
    `total_calculado` é o que as vidas somam. Os dois existem de propósito —
    a conferência é justamente a diferença entre eles."""
    titular: Beneficiario | None = None
    dependentes: list = field(default_factory=list)
    total_declarado: Decimal | None = None

    @property
    def vidas(self) -> list:
        return ([self.titular] if self.titular else []) + list(self.dependentes)

    @property
    def total_calculado(self) -> Decimal:
        return sum((b.cobranca.total for b in self.vidas), Decimal("0.00")).quantize(CENTAVO)

    def dict(self):
        return {
            "titular": self.titular.dict() if self.titular else None,
            "dependentes": [d.dict() for d in self.dependentes],
            "total_declarado": None if self.total_declarado is None else str(self.total_declarado),
            "total_calculado": str(self.total_calculado),
            "vidas": len(self.vidas),
        }


@dataclass
class Divergencia:
    o_que: str
    declarado: Decimal
    identificado: Decimal
    contexto: str = ""

    @property
    def diferenca(self) -> Decimal:
        return (self.declarado - self.identificado).quantize(CENTAVO)

    @property
    def relevante(self) -> bool:
        return abs(self.diferenca) > TOLERANCIA

    def dict(self):
        return {"o_que": self.o_que, "declarado": str(self.declarado),
                "identificado": str(self.identificado),
                "diferenca": str(self.diferenca), "contexto": self.contexto,
                "relevante": self.relevante}


@dataclass
class Conferencia:
    """Resultado da regra que o PARSER declarou. Cada operadora estrutura a
    cobrança do seu jeito; forçar uma fórmula única produziria divergência
    falsa. Por isso `regra` é texto vindo do parser, e vai para a tela."""
    regra: str = ""
    divergencias: list = field(default_factory=list)

    @property
    def fecha(self) -> bool:
        return not any(d.relevante for d in self.divergencias)

    def dict(self):
        return {"regra": self.regra, "fecha": self.fecha,
                "divergencias": [d.dict() for d in self.divergencias]}


@dataclass
class Extrato:
    """Um relatório importado: o arquivo, o que ele declara e o que rendeu.

    `arquivo_*` guarda o original para poder reprocessar quando um parser for
    corrigido — o PDF é a verdade, o parse é uma leitura dela."""
    operadora: str = ""
    competencia: str = ""            # MM/AAAA
    empresa_nome: str = ""
    empresa_cnpj: str = ""
    apolice: str = ""
    contrato: str = ""
    vencimento: str = ""
    periodo_cobertura: str = ""

    familias: list = field(default_factory=list)

    # totais que o RELATÓRIO declara (nunca calculados por nós)
    total_tecnico_declarado: Decimal | None = None
    iof_declarado: Decimal = Decimal("0.00")
    acertos_declarados: Decimal = Decimal("0.00")
    outros_declarados: Decimal = Decimal("0.00")
    total_fatura_declarado: Decimal | None = None
    titulares_declarados: int | None = None
    dependentes_declarados: int | None = None

    # procedência
    arquivo_nome: str = ""
    arquivo_hash: str = ""
    arquivo_caminho: str = ""
    importado_em: str = ""
    parser: str = ""
    parser_versao: str = ""
    deteccao_confianca: float = 0.0
    status: str = "IMPORTADO"
    conferencia: Conferencia = field(default_factory=Conferencia)
    erro: str = ""
    avisos: list = field(default_factory=list)

    @property
    def id(self) -> str:
        """Identidade do extrato = arquivo + contexto. Reimportar o mesmo PDF
        para a mesma empresa/competência não pode duplicar lançamento; o mesmo
        PDF em outro contexto é outro extrato."""
        base = "|".join([self.arquivo_hash, so_digitos(self.empresa_cnpj),
                         self.competencia, self.operadora])
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

    @property
    def titulares(self) -> int:
        return sum(1 for f in self.familias if f.titular)

    @property
    def dependentes(self) -> int:
        return sum(len(f.dependentes) for f in self.familias)

    @property
    def vidas(self) -> int:
        return sum(len(f.vidas) for f in self.familias)

    @property
    def total_individualizado(self) -> Decimal:
        return sum((f.total_calculado for f in self.familias), Decimal("0.00")).quantize(CENTAVO)

    def beneficiarios(self):
        for f in self.familias:
            for b in f.vidas:
                yield f, b

    def dict(self):
        return {
            "id": self.id,
            "operadora": self.operadora, "competencia": self.competencia,
            "empresa_nome": self.empresa_nome, "empresa_cnpj": self.empresa_cnpj,
            "apolice": self.apolice, "contrato": self.contrato,
            "vencimento": self.vencimento, "periodo_cobertura": self.periodo_cobertura,
            "familias": [f.dict() for f in self.familias],
            "titulares": self.titulares, "dependentes": self.dependentes,
            "vidas": self.vidas,
            "titulares_declarados": self.titulares_declarados,
            "dependentes_declarados": self.dependentes_declarados,
            "total_individualizado": str(self.total_individualizado),
            "total_tecnico_declarado": (None if self.total_tecnico_declarado is None
                                        else str(self.total_tecnico_declarado)),
            "iof_declarado": str(self.iof_declarado),
            "acertos_declarados": str(self.acertos_declarados),
            "outros_declarados": str(self.outros_declarados),
            "total_fatura_declarado": (None if self.total_fatura_declarado is None
                                       else str(self.total_fatura_declarado)),
            "arquivo_nome": self.arquivo_nome, "arquivo_hash": self.arquivo_hash,
            "arquivo_caminho": self.arquivo_caminho, "importado_em": self.importado_em,
            "parser": self.parser, "parser_versao": self.parser_versao,
            "deteccao_confianca": round(self.deteccao_confianca, 2),
            "status": self.status, "erro": self.erro, "avisos": self.avisos,
            "conferencia": self.conferencia.dict(),
        }
