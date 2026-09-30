"""bradesco.py — Bradesco Saúde, relatório "FATURA TECNICA".

Layout conferido contra uma fatura real (subfatura 345, ramo MEDICA, 07/2026).
Estrutura do documento:

    ...cabeçalho com boleto, estipulante, CNPJ, vencimento, "Valor do IOF"...
    Resumo: Titulares | Dependentes | Tot.Segurados | Part.Seg. | Valor | Lanç.
      1 4 5 5(TS)TOTAIS DA SUBFATURA 12.970,52 0,00
    ...linhas de vidas...
      0000019/00 <TITULAR>    16/06/1972 CAS      TNEE 29/11/2013 07/2026 3.349,37 0,00FEM
      0000019/01 <FILHA>      17/04/2003 SOLT FILH TNEE 29/11/2013 07/2026 1.207,44 0,00FEM

QUEM É O TITULAR — o certificado termina em `/00` para o titular, mas essa NÃO é
a evidência que usamos como principal: a própria linha traz a coluna de
parentesco, que vem VAZIA no titular e preenchida (FILH/CONJ/…) no dependente.
Parentesco é dado explícito do relatório; o sufixo é convenção de numeração.
Usamos o parentesco e conferimos contra o sufixo — divergindo, o extrato vai
para CONFERIR com aviso, em vez de escolher em silêncio.

O relatório NÃO traz CPF das vidas (o próprio Bradesco pede, nas mensagens da
fatura, que o estipulante mantenha o CPF atualizado). Então a identidade aqui
sai do certificado, que é estável entre competências.

CONFERÊNCIA (regra desta operadora, medida na fatura real):
    soma das vidas ............ 12.970,52 = (TS) TOTAIS DA SUBFATURA
    total técnico + IOF ....... 12.970,52 + 308,69 = 13.279,21 = Valor Cobrado
"""
from __future__ import annotations

import re

from decimal import Decimal

from .base import Parser
from ..modelo import (Beneficiario, Cobranca, Conferencia, Extrato, Familia,
                      dec, normaliza_parentesco)

# tokens de estado civil e de parentesco que aparecem entre o nascimento e o
# plano. Ficam em conjuntos (e não em posição fixa) porque o titular não traz a
# coluna de parentesco — posicional quebraria justamente na linha mais importante.
_EST_CIVIL = {"SOLT", "CAS", "CASA", "VIUV", "VIUVO", "DIVO", "DESQ", "SEP",
              "UNIA", "UNI", "OUTR"}
_PARENTESCO = {"FILH", "CONJ", "ENTE", "COMP", "AGRE", "TITU", "NETO", "IRMA",
               "PAIS", "TUTE", "MENO"}

_LINHA_VIDA = re.compile(
    r"^(?P<cert>\d{3,}/\d{2})\s+"
    r"(?P<nome>[A-ZÀ-Ü][A-ZÀ-Ü .'\-]{3,}?)\s+"
    r"(?P<nasc>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<meio>[A-Z0-9 .\-]{2,40}?)\s+"
    r"(?P<inicio>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<comp>\d{2}/\d{4})\s+"
    r"(?P<valor>[\d.]*\d,\d{2})\s+"
    r"(?P<part>[\d.]*\d,\d{2})"
    r"(?P<sexo>[A-Z]{3})?\s*(?P<mov>[A-Z])?\s*$"
)

_TOTAIS = re.compile(
    r"(?P<tit>\d+)\s+(?P<dep>\d+)\s+(?P<tot>\d+)\s+\d+\s*\(TS\)\s*TOTAIS DA SUBFATURA\s+"
    r"(?P<valor>[\d.]*\d,\d{2})\s+(?P<lanc>[\d.]*\d,\d{2})", re.I)

# A fatura traz VÁRIOS CNPJ: o da seguradora, o do banco cobrador e — o que a
# gente quer — o do estipulante. Pegar "o primeiro CNPJ do texto" traria a
# Bradesco Saúde, e o extrato inteiro ficaria arquivado na empresa errada.
# Comparação pela raiz (8 dígitos), que é o que não muda entre filiais.
_RAIZES_IGNORAR = {
    "92693118",   # BRADESCO SAUDE S/A
    "60746948",   # BANCO BRADESCO S/A
    "17298092",   # BRADESCO DENTAL S/A
}


class ParserBradesco(Parser):
    nome = "Bradesco Saúde"
    versao = "1"
    ans = "005711"

    def confianca(self, texto: str) -> float:
        t = (texto or "").upper()
        pontos = 0.0
        if "BRADESCO SAUDE" in t or "BRADESCO SAÚDE" in t:
            pontos += 0.5
        if "FATURA TECNICA" in t or "FATURA TÉCNICA" in t:
            pontos += 0.3
        if "92.693.118/0001-60" in t:          # CNPJ da Bradesco Saúde S/A
            pontos += 0.3
        if "TOTAIS DA SUBFATURA" in t:
            pontos += 0.2
        if "ESTIPULANTE" in t:
            pontos += 0.1
        return min(pontos, 1.0)

    # ── leitura ──────────────────────────────────────────────────────────
    def ler(self, texto: str, ex: Extrato) -> None:
        linhas = [re.sub(r"[ \t]+", " ", l).strip()
                  for l in texto.split("\n") if l.strip()]
        junto = "\n".join(linhas)

        ex.competencia = self._competencia(junto, linhas)
        ex.empresa_cnpj = self._cnpj_estipulante(junto)
        ex.empresa_nome = self._estipulante(linhas)
        ex.apolice = self._primeiro(r"^(\d{4})\s*-\s*\S", junto, flags=re.M)
        ex.contrato = self._primeiro(r"\b(\d{3})\s*-\s*([A-Z][A-Z ]{5,})", junto, grupo=0).strip()
        ex.vencimento = self._vencimento(junto, linhas)
        ex.periodo_cobertura = self._primeiro(
            r"(DE\s+\d{2}[./]\d{2}[./]\d{4}\s+A\s+\d{2}[./]\d{2}[./]\d{4})", junto)

        # IOF: rótulo numa linha, valor mascarado com asteriscos na seguinte
        ex.iof_declarado = dec(self._depois_de("Valor do IOF", linhas))
        ex.total_fatura_declarado = self._valor_cobrado(junto, linhas)

        m = _TOTAIS.search(junto)
        if m:
            ex.total_tecnico_declarado = dec(m.group("valor"))
            ex.outros_declarados = dec(m.group("lanc"))
            ex.titulares_declarados = int(m.group("tit"))
            ex.dependentes_declarados = int(m.group("dep"))

        ex.familias = self._familias(linhas, ex)

    def _competencia(self, junto, linhas) -> str:
        # "Subfatura345 MEDICA 07/2026 01" — a competência é o MM/AAAA do
        # cabeçalho da subfatura, que se repete em toda página.
        m = re.search(r"Subfatura\s*\d*\s+[A-ZÇÃ]+\s+(\d{2}/\d{4})", junto, re.I)
        if m:
            return m.group(1)
        m = re.search(r"\b(\d{2}/\d{4})\b", junto)
        return m.group(1) if m else ""

    @staticmethod
    def _cnpj_estipulante(junto) -> str:
        """O CNPJ do contratante, e não o da seguradora nem o do banco.

        Exige a PONTUAÇÃO. A fatura é cheia de campos de 14 dígitos corridos
        (nosso número, linha digitável, chave); aceitar dígitos soltos fez o
        primeiro teste arquivar a fatura sob "00.203.207/2337-53", que é o
        nosso número do boleto."""
        for bruto in re.findall(r"\b\d{2,3}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", junto):
            d = re.sub(r"\D", "", bruto)
            if len(d) == 15 and d.startswith("0"):      # "092.693.118/..." impresso com zero
                d = d[1:]
            if len(d) != 14 or d[:8] in _RAIZES_IGNORAR:
                continue
            return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        return ""

    @staticmethod
    def _vencimento(junto, linhas) -> str:
        """O vencimento do boleto vem impresso sozinho numa linha, logo abaixo
        da linha digitável. O `DE dd.mm.aaaa A dd.mm.aaaa` é o período de
        COBERTURA — confundir os dois dá a data certa por acaso em alguns meses
        e errada nos outros."""
        for i, l in enumerate(linhas):
            if re.match(r"^\d{5}\.\d{5}\s+\d{5}\.\d{6}", l):    # linha digitável
                for j in range(i + 1, min(i + 4, len(linhas))):
                    m = re.match(r"^(\d{2}/\d{2}/\d{4})$", linhas[j])
                    if m:
                        return m.group(1)
        m = re.search(r"Vencimento\s*:?\s*(\d{2}/\d{2}/\d{4})", junto, re.I)
        return m.group(1) if m else ""

    def _estipulante(self, linhas) -> str:
        """A razão social aparece isolada logo antes/depois do bloco da apólice.
        Pegamos a linha `0001 - NOME`, que é a que sempre existe."""
        for l in linhas:
            m = re.match(r"^\d{4}\s*-\s*([A-ZÀ-Ü][A-ZÀ-Ü .'\-]{4,})", l)
            if m:
                return m.group(1).split("  ")[0].strip()
        for l in linhas:
            if re.match(r"^[A-ZÀ-Ü][A-ZÀ-Ü .'\-]{9,}$", l):
                return l.strip()
        return ""

    @staticmethod
    def _primeiro(padrao, texto, grupo=1, flags=0) -> str:
        m = re.search(padrao, texto, flags)
        if not m:
            return ""
        return (m.group(grupo) if grupo else m.group(0)).strip()

    @staticmethod
    def _depois_de(rotulo, linhas) -> str:
        """Valor que vem na(s) linha(s) seguinte(s) ao rótulo, mascarado com '*'."""
        for i, l in enumerate(linhas):
            if rotulo.lower() in l.lower():
                for j in range(i, min(i + 3, len(linhas))):
                    m = re.search(r"\*+\s*([\d.]*\d,\d{2})", linhas[j])
                    if m:
                        return m.group(1)
                    if j > i:
                        m = re.search(r"([\d.]*\d,\d{2})\s*$", linhas[j])
                        if m:
                            return m.group(1)
        return ""

    def _valor_cobrado(self, junto, linhas):
        # três âncoras, da mais específica para a mais genérica
        for padrao in (r"cobrar o valor de R\$ ?([\d.]*\d,\d{2})",
                       r"R\$ \*+([\d.]*\d,\d{2})"):
            m = re.search(padrao, junto, re.I)
            if m:
                return dec(m.group(1))
        v = self._depois_de("Valor Cobrado", linhas)
        return dec(v) if v else None

    # ── vidas e famílias ─────────────────────────────────────────────────
    def _familias(self, linhas, ex) -> list:
        por_familia: dict[str, Familia] = {}
        ordem: list[str] = []
        for l in linhas:
            m = _LINHA_VIDA.match(l)
            if not m:
                continue
            b, fam_id = self._vida(m, ex)
            f = por_familia.get(fam_id)
            if f is None:
                f = Familia()
                por_familia[fam_id] = f
                ordem.append(fam_id)
            if b.parentesco == "TITULAR" and f.titular is None:
                f.titular = b
            elif b.parentesco == "TITULAR":
                # dois titulares no mesmo certificado: não decidimos, avisamos
                b.avisos.append("Segundo titular no mesmo certificado — conferir")
                ex.avisos.append(f"Certificado {fam_id}: mais de um titular")
                f.dependentes.append(b)
            else:
                f.dependentes.append(b)
        for fid in ordem:
            f = por_familia[fid]
            if f.titular is None and f.dependentes:
                # família sem titular identificado: promove nada, só avisa —
                # inventar titular estragaria o vínculo que o REINF vai usar
                ex.avisos.append(f"Certificado {fid}: nenhuma vida marcada como titular")
        return [por_familia[fid] for fid in ordem]

    def _vida(self, m, ex):
        cert = m.group("cert")
        fam_id, sufixo = cert.split("/", 1)
        meio = m.group("meio").split()
        parentesco_txt = ""
        plano = []
        for tok in meio:
            if tok in _PARENTESCO and not parentesco_txt:
                parentesco_txt = tok
            elif tok in _EST_CIVIL and not plano:
                continue                      # estado civil não interessa à cobrança
            else:
                plano.append(tok)

        # evidência principal: a coluna de parentesco. Vazia = titular.
        parentesco = normaliza_parentesco(parentesco_txt) if parentesco_txt else "TITULAR"
        titular_pelo_sufixo = sufixo == "00"
        avisos = []
        if (parentesco == "TITULAR") != titular_pelo_sufixo:
            avisos.append(
                f"Certificado {cert} termina em /{sufixo} mas a coluna de parentesco diz "
                f"'{parentesco_txt or '(vazia)'}' — usei o parentesco do relatório")
            ex.avisos.append(avisos[-1])

        b = Beneficiario(
            nome=m.group("nome").strip(),
            codigo=cert,
            matricula=fam_id,
            plano=" ".join(plano),
            parentesco=parentesco,
            parentesco_original=parentesco_txt,
            nascimento=m.group("nasc"),
            inicio_vigencia=m.group("inicio"),
            cobranca=Cobranca(mensalidade=dec(m.group("valor")),
                              coparticipacao=dec(m.group("part"))),
            origem_chave="codigo",
            avisos=avisos,
        )
        return b, fam_id

    # ── conferência ──────────────────────────────────────────────────────
    def conferir(self, ex: Extrato) -> Conferencia:
        c = Conferencia(regra=(
            "Bradesco: soma das vidas = TOTAIS DA SUBFATURA (total técnico); "
            "total técnico + IOF = Valor Cobrado."))
        c.divergencias.extend(self._conferir_familias(ex))

        copart = sum((b.cobranca.coparticipacao for _, b in ex.beneficiarios()),
                     Decimal("0.00"))

        if ex.total_tecnico_declarado is not None:
            d = self._div(
                "Total técnico (TOTAIS DA SUBFATURA)",
                ex.total_tecnico_declarado, ex.total_individualizado,
                "soma das vidas identificadas")
            # A amostra real que temos tinha a coluna "Part. Seg." zerada em
            # TODAS as vidas, então não dá para afirmar se o Valor do resumo
            # inclui ou não a coparticipação. Quando a diferença é exatamente
            # a coparticipação, dizemos isso — em vez de deixar o contador
            # caçando um erro que provavelmente não existe.
            if copart > 0 and abs(d.diferenca + copart) <= 0.02:
                d.contexto += (f" — a diferença é exatamente a coparticipação "
                               f"({copart}); nesta fatura o TOTAIS DA SUBFATURA "
                               f"parece NÃO incluir a Part. Seg.")
                ex.avisos.append(
                    "A diferença contra o total técnico é exatamente a "
                    "coparticipação. Confira na fatura se a Part. Seg. é cobrada "
                    "à parte — o Fiscale ainda não tem amostra que resolva isso.")
            c.divergencias.append(d)

        if ex.total_fatura_declarado is not None:
            base = (ex.total_tecnico_declarado
                    if ex.total_tecnico_declarado is not None
                    else ex.total_individualizado)
            c.divergencias.append(self._div(
                "Valor cobrado da fatura", ex.total_fatura_declarado,
                (base + ex.iof_declarado + ex.outros_declarados),
                "total técnico + IOF + lançamentos"))

        if ex.titulares_declarados is not None and ex.titulares_declarados != ex.titulares:
            ex.avisos.append(
                f"O relatório declara {ex.titulares_declarados} titular(es) e "
                f"identifiquei {ex.titulares}")
        if ex.dependentes_declarados is not None and ex.dependentes_declarados != ex.dependentes:
            ex.avisos.append(
                f"O relatório declara {ex.dependentes_declarados} dependente(s) e "
                f"identifiquei {ex.dependentes}")
        return c
