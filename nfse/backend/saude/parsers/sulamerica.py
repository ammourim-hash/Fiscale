"""sulamerica.py — SulAmérica, "RELATÓRIO DE FATURAMENTO DE SEGURADOS ATIVOS".

Layout conferido contra um relatório real. O exemplo abaixo é o mesmo relatório
com os identificadores trocados (ver amostras/sulamerica_familia.txt):

    DADOS DA EMPRESA
     Razão Social: ...        Empresa: <cód>      Apólice: <número>
     Período de Competência: 16/07/2026 a 15/08/2026     Vencimento: 16/07/2026
    Cód. Identificação | Nome | CPF | Plano | ID Funcional | Nascimento | Idade |
                       Parentesco | Início Vigência | Prêmio
     88888<idfunc>0011 <NOME> <cpf 10 díg.> 45150-EXATO <idfunc> 25/12/1979 46
                       TITULAR 16/09/2023 R$ 904,72
    Total da Familia: R$ 2.714,16
    Total Geral: R$ 2.714,16
    RESUMO PRÊMIO
     Total: R$ 2.714,16   Acertos: R$ 00,00   IOF: R$ 64,60   Total Geral: R$ 2.778,76

Aqui o parentesco é explícito e completo (TITULAR / CONJUGE / FILHOS) — não há
a ambiguidade que existe no Bradesco.

CPF: o relatório sai SEM os zeros à esquerda (chega com 10 dígitos quando o CPF
começa por zero). Completar à esquerda recupera o dado; o dígito verificador é
conferido e o resultado fica registrado em `cpf_ok`, para a tela poder avisar
sem recusar a importação.

FAMÍLIA: o bloco entre um "Total da Familia" e o anterior é uma família. Não
dá para agrupar pelo código de identificação sozinho porque ele embute o ID
funcional — que é do TITULAR, e por isso serve de confirmação, não de recorte.

CONFERÊNCIA (regra desta operadora, medida no relatório real):
    soma das vidas da família ... = Total da Familia
    soma das famílias ........... = Total Geral   (2.714,16)
    Total + Acertos + IOF ....... = Total Geral do RESUMO PRÊMIO (2.778,76)
"""
from __future__ import annotations

import re

from .base import Parser
from ..modelo import (Beneficiario, Cobranca, Conferencia, Extrato, Familia,
                      cpf_valido, dec, normaliza_cpf, normaliza_parentesco)

_LINHA_VIDA = re.compile(
    r"^(?P<cod>\d{10,25})\s+"
    r"(?P<nome>[A-ZÀ-Ü][A-ZÀ-Ü .'\-]{3,}?)\s+"
    r"(?P<cpf>\d{9,11})\s+"
    r"(?P<plano>\S+)\s+"
    r"(?P<matricula>\d{3,})\s+"
    r"(?P<nasc>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<idade>\d{1,3})\s+"
    r"(?P<parentesco>[A-ZÀ-Ü()]{3,20})\s+"
    r"(?P<inicio>\d{2}/\d{2}/\d{4})\s+"
    r"R?\$?\s*(?P<premio>[\d.]*\d,\d{2})\s*$"
)

_TOTAL_FAMILIA = re.compile(r"Total\s+da\s+Fam[ií]lia\s*:?\s*R?\$?\s*([\d.]*\d,\d{2})", re.I)
_TOTAL_GERAL = re.compile(r"Total\s+Geral\s*:?\s*R?\$?\s*([\d.]*\d,\d{2})", re.I)


class ParserSulAmerica(Parser):
    nome = "SulAmérica"
    versao = "1"
    ans = "006246"

    def confianca(self, texto: str) -> float:
        t = (texto or "").upper()
        pontos = 0.0
        if "SULAMERICA" in t.replace(" ", "") or "SUL AMERICA" in t or "SULAMÉRICA" in t.replace(" ", ""):
            pontos += 0.5
        if "RELATÓRIO DE FATURAMENTO DE SEGURADOS ATIVOS" in t or \
           "RELATORIO DE FATURAMENTO DE SEGURADOS ATIVOS" in t:
            pontos += 0.6
        if "RESUMO GRUPO SEGURÁVEL" in t or "RESUMO GRUPO SEGURAVEL" in t:
            pontos += 0.2
        if "01.685.053/0001-56" in t:              # CNPJ da SulAmérica Seguro Saúde
            pontos += 0.3
        if "VALOR DA US DO MÊS" in t or "VALOR DA US DO MES" in t:
            pontos += 0.2
        return min(pontos, 1.0)

    # ── leitura ──────────────────────────────────────────────────────────
    def ler(self, texto: str, ex: Extrato) -> None:
        linhas = [re.sub(r"[ \t]+", " ", l).strip()
                  for l in texto.split("\n") if l.strip()]
        junto = "\n".join(linhas)

        ex.empresa_nome = self._campo(r"Raz[ãa]o\s+Social\s*:\s*(.+)", junto)
        ex.empresa_cnpj = self._campo(r"CNPJ\s*:?\s*([\d./\-]{14,20})", junto)
        ex.contrato = self._campo(r"Empresa\s*:\s*(\S+)", junto)
        ex.apolice = self._campo(r"Ap[óo]lice\s*:\s*(\S+)", junto)
        ex.vencimento = self._campo(r"Vencimento\s*:\s*(\d{2}/\d{2}/\d{4})", junto)
        ex.periodo_cobertura = self._campo(
            r"Per[íi]odo\s+de\s+Compet[êe]ncia\s*:\s*(\d{2}/\d{2}/\d{4}\s*a\s*\d{2}/\d{2}/\d{4})",
            junto)
        ex.competencia = self._competencia(ex.periodo_cobertura, ex.vencimento)

        ex.familias = self._familias(linhas)

        # RESUMO PRÊMIO — os totais que o relatório declara
        resumo = self._resumo_premio(junto)
        ex.total_tecnico_declarado = resumo.get("total")
        ex.acertos_declarados = resumo.get("acertos") or dec(0)
        ex.iof_declarado = resumo.get("iof") or dec(0)
        ex.total_fatura_declarado = resumo.get("total_geral")

        ex.titulares_declarados = self._inteiro(r"Total\s+de\s+Titulares\s*:\s*(\d+)", junto)
        ex.dependentes_declarados = self._inteiro(r"Total\s+de\s+Dependentes\s*:\s*(\d+)", junto)

    @staticmethod
    def _campo(padrao, texto) -> str:
        m = re.search(padrao, texto, re.I)
        if not m:
            return ""
        # o valor pode vir seguido de outro rótulo na mesma linha do PDF
        return re.split(r"\s{2,}|\s+[A-ZÀ-Ü][a-zà-ü]+\s*:", m.group(1).strip())[0].strip()

    @staticmethod
    def _inteiro(padrao, texto):
        m = re.search(padrao, texto, re.I)
        return int(m.group(1)) if m else None

    @staticmethod
    def _competencia(periodo, vencimento) -> str:
        """A competência é o mês em que o período de cobertura COMEÇA.

        O relatório dá um intervalo (16/07 a 15/08) que atravessa dois meses;
        o mês de início é o que a operadora usa para nomear a fatura e é o que
        casa com o vencimento. Fica registrado em `periodo_cobertura` para quem
        precisar do intervalo cheio."""
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", periodo or "")
        if m:
            return f"{m.group(2)}/{m.group(3)}"
        m = re.match(r"\d{2}/(\d{2})/(\d{4})", vencimento or "")
        return f"{m.group(1)}/{m.group(2)}" if m else ""

    def _resumo_premio(self, junto) -> dict:
        """O bloco RESUMO PRÊMIO é o único lugar onde IOF e acertos aparecem."""
        i = junto.upper().find("RESUMO PR")
        trecho = junto[i:] if i >= 0 else junto
        out = {}
        for chave, padrao in (
                ("total", r"Total\s*:\s*R?\$?\s*([\d.]*\d,\d{2})"),
                ("acertos", r"Acertos\s*:\s*R?\$?\s*([\d.]*\d,\d{2})"),
                ("iof", r"IOF\s*:\s*R?\$?\s*([\d.]*\d,\d{2})"),
                ("total_geral", r"Total\s+Geral\s*:\s*R?\$?\s*([\d.]*\d,\d{2})")):
            m = re.search(padrao, trecho, re.I)
            if m:
                out[chave] = dec(m.group(1))
        return out

    # ── vidas e famílias ─────────────────────────────────────────────────
    def _familias(self, linhas) -> list:
        familias, atual = [], Familia()
        for l in linhas:
            m = _LINHA_VIDA.match(l)
            if m:
                b = self._vida(m)
                if b.parentesco == "TITULAR" and atual.titular is None:
                    atual.titular = b
                else:
                    atual.dependentes.append(b)
                continue
            mt = _TOTAL_FAMILIA.search(l)
            if mt and atual.vidas:
                atual.total_declarado = dec(mt.group(1))
                familias.append(atual)
                atual = Familia()
        if atual.vidas:
            familias.append(atual)
        return familias

    def _vida(self, m) -> Beneficiario:
        cpf = normaliza_cpf(m.group("cpf"))
        ok = cpf_valido(cpf)
        avisos = []
        if cpf and not ok:
            avisos.append(f"CPF {cpf} não passa no dígito verificador — conferir")
        if len(m.group("cpf")) < 11:
            avisos.append("CPF veio sem os zeros à esquerda; completei para 11 dígitos")
        return Beneficiario(
            nome=m.group("nome").strip(),
            cpf=cpf,
            cpf_ok=ok,
            codigo=m.group("cod"),
            matricula=m.group("matricula"),
            plano=m.group("plano"),
            parentesco=normaliza_parentesco(m.group("parentesco")),
            parentesco_original=m.group("parentesco"),
            nascimento=m.group("nasc"),
            inicio_vigencia=m.group("inicio"),
            cobranca=Cobranca(mensalidade=dec(m.group("premio"))),
            origem_chave="cpf" if ok else "codigo",
            avisos=avisos,
        )

    # ── conferência ──────────────────────────────────────────────────────
    def conferir(self, ex: Extrato) -> Conferencia:
        c = Conferencia(regra=(
            "SulAmérica: soma das vidas = Total da Família; soma das famílias = "
            "Total do RESUMO PRÊMIO; Total + Acertos + IOF = Total Geral."))
        c.divergencias.extend(self._conferir_familias(ex))

        if ex.total_tecnico_declarado is not None:
            c.divergencias.append(self._div(
                "Total dos prêmios (RESUMO PRÊMIO)",
                ex.total_tecnico_declarado, ex.total_individualizado,
                "soma das vidas identificadas"))

        if ex.total_fatura_declarado is not None:
            base = (ex.total_tecnico_declarado
                    if ex.total_tecnico_declarado is not None
                    else ex.total_individualizado)
            c.divergencias.append(self._div(
                "Total Geral da fatura", ex.total_fatura_declarado,
                (base + ex.acertos_declarados + ex.iof_declarado),
                "total dos prêmios + acertos + IOF"))

        if ex.titulares_declarados is not None and ex.titulares_declarados != ex.titulares:
            ex.avisos.append(
                f"O relatório declara {ex.titulares_declarados} titular(es) e "
                f"identifiquei {ex.titulares}")
        if ex.dependentes_declarados is not None and ex.dependentes_declarados != ex.dependentes:
            ex.avisos.append(
                f"O relatório declara {ex.dependentes_declarados} dependente(s) e "
                f"identifiquei {ex.dependentes}")
        return c
