"""
identidade.py — quem é a empresa, em formato canônico. FONTE ÚNICA.

O PROBLEMA QUE ISTO RESOLVE
    A mesma regra ("tira tudo que não é dígito") estava escrita em cinco lugares:
    `cnpj_publico.so_digitos`, `apuracao_federal._so_digitos`, `inscricoes._dig`,
    `nfe._dig` e `saude/modelo.so_digitos`. Cinco cópias é cinco chances de
    divergir justamente na hora de casar um documento fiscal com uma empresa —
    e quando isso erra, o documento de uma empresa aparece na pasta de outra.

O QUE É "CANÔNICO"
    PJ  → CNPJ com 14 dígitos, sem máscara, com os zeros à esquerda.
    PF  → CPF com 11 dígitos, sem máscara.

    Sem máscara é o ponto: `04.999.001/0001-43` e `04999001000143` são a mesma
    empresa, e só um dos dois pode ser a chave. Escolhemos os dígitos porque é o
    formato que os XML fiscais usam.

ZEROS À ESQUERDA
    `normalizar()` completa com zero à esquerda quando o valor é curto — mas
    **só aceita o resultado se os dígitos verificadores fecharem**, e registra o
    ajuste em `.ajustes`. Planilha e JSON mal exportados transformam
    `04999001000143` em `4999001000143`; restaurar isso é necessário. Fazer isso
    em silêncio, não: um ajuste que ninguém vê é um erro esperando a vez.

O QUE ISTO **NÃO** FAZ
    Não consulta a Receita, não vai à rede e não decide se a empresa existe.
    Dígito verificador só prova que o número foi digitado direito.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TIPO_CNPJ = "CNPJ"
TIPO_CPF = "CPF"

_NAO_DIGITO = re.compile(r"\D")


def so_digitos(v) -> str:
    """Só os dígitos. É a única definição desta regra no projeto."""
    return _NAO_DIGITO.sub("", str(v or ""))


def cnpj_valido(v) -> bool:
    """Dígitos verificadores do CNPJ (mesmo algoritmo de `cnpj_publico`)."""
    c = so_digitos(v)
    if len(c) != 14 or c == c[0] * 14:
        return False
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(c[:tam], pesos)) % 11
        if int(c[tam]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def cpf_valido(v) -> bool:
    """Dígitos verificadores do CPF.

    O cadastro real tem identificadores de 11 dígitos — profissionais autônomos
    entram como pessoa física, e o certificado A1 deles é e-CPF. Tratar tudo
    como CNPJ deixaria essas contas de fora.
    """
    c = so_digitos(v)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for tam in (9, 10):
        resto = sum(int(d) * (tam + 1 - i) for i, d in enumerate(c[:tam])) * 10 % 11
        if int(c[tam]) != (0 if resto == 10 else resto):
            return False
    return True


@dataclass(frozen=True)
class Identificador:
    """Identificador fiscal já normalizado, com o histórico do que foi ajustado.

    `valor` é o que serve de chave — e só deve ser usado como chave quando
    `valido` é verdadeiro. `bruto` guarda o que veio da fonte, para o
    diagnóstico poder mostrar de onde saiu.
    """
    valor: str = ""
    tipo: str = ""
    valido: bool = False
    motivo: str = ""
    bruto: str = ""
    ajustes: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.valido

    def __str__(self) -> str:
        return self.valor

    def mascarado(self) -> str:
        """Para log e tela: mantém o que identifica, esconde o miolo.

        Nunca imprima o identificador inteiro em log — CPF é dado pessoal, e
        log vai parar em lugar que não controlamos.
        """
        v = self.valor
        if self.tipo == TIPO_CNPJ and len(v) == 14:
            return f"{v[:2]}.***.***/{v[8:12]}-{v[12:]}"
        if self.tipo == TIPO_CPF and len(v) == 11:
            return f"***.{v[3:6]}.***-{v[9:]}"
        return "<inválido>"


def normalizar(v, tipo_esperado: str = "") -> Identificador:
    """Devolve o identificador canônico. NUNCA levanta exceção.

    `tipo_esperado` ("CNPJ" ou "CPF") força a interpretação; vazio decide pelo
    comprimento. Quem chama precisa checar `.valido` — devolver um objeto
    inválido em vez de estourar é o que permite a reconciliação listar o
    problema em vez de morrer no primeiro registro torto.
    """
    bruto = str(v or "")
    d = so_digitos(bruto)
    if not d:
        return Identificador(bruto=bruto, motivo="vazio")

    candidatos: list[tuple[str, str, tuple[str, ...]]] = []   # (valor, tipo, ajustes)
    if tipo_esperado in ("", TIPO_CNPJ):
        if len(d) == 14:
            candidatos.append((d, TIPO_CNPJ, ()))
        elif 11 < len(d) < 14:
            candidatos.append((d.zfill(14), TIPO_CNPJ, ("zeros à esquerda restaurados",)))
    if tipo_esperado in ("", TIPO_CPF):
        if len(d) == 11:
            candidatos.append((d, TIPO_CPF, ()))
        elif 8 < len(d) < 11:
            candidatos.append((d.zfill(11), TIPO_CPF, ("zeros à esquerda restaurados",)))

    for valor, tipo, ajustes in candidatos:
        ok = cnpj_valido(valor) if tipo == TIPO_CNPJ else cpf_valido(valor)
        if ok:
            return Identificador(valor=valor, tipo=tipo, valido=True,
                                 bruto=bruto, ajustes=ajustes)

    if not candidatos:
        return Identificador(bruto=bruto,
                             motivo=f"comprimento inesperado ({len(d)} dígitos)")
    tipos = "/".join(sorted({t for _, t, _ in candidatos}))
    return Identificador(bruto=bruto, motivo=f"dígito verificador não confere ({tipos})")


def mesma_empresa(a, b) -> bool:
    """Duas referências apontam para a mesma empresa?

    Só compara identificador válido. Nome NÃO entra: "MONTE ASSESSORIA LTDA" e
    "Monte Assessoria e Consultoria Ltda" podem ser a mesma empresa ou duas
    diferentes, e não é o nome que decide isso.
    """
    ia, ib = normalizar(a), normalizar(b)
    return ia.valido and ib.valido and ia.valor == ib.valor and ia.tipo == ib.tipo
