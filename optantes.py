# -*- coding: utf-8 -*-
"""optantes.py — a varredura da carteira na base pública, em CSV e em PDF.

O QUE ESTE RELATÓRIO É, E O QUE ELE NÃO É
    É **tabulação nossa** sobre a base de Dados Abertos do CNPJ. Não é
    documento de órgão, não é certidão, não tem valor de prova.

    Por isso ele **não entra no envelope** da Situação Fiscal. Guardá-lo em
    `situacao/` ao lado de certidões verdadeiras misturaria exatamente as duas
    coisas que aquele módulo existe para separar: o que o fisco emitiu e o que
    nós montamos. Ele é exportação — vive fora, e o painel não o enxerga.

    A regra white-label vale igual, e pelo mesmo motivo: o PDF vai para o
    cliente. Ela vem de `relatorio_base`, em UMA implementação.

A DATA DE REFERÊNCIA, QUE É O CAMPO MAIS IMPORTANTE DO CABEÇALHO
    A base é atualizada **em lote, mensalmente**. Medido em 12/09/2026: ela
    respondia `2026-08` — um mês atrás. Uma exclusão do Simples feita em
    agosto ainda aparecia como "optante" na consulta de hoje.

    Um relatório sem essa data parece atual e não é. Com ela, quem lê sabe
    exatamente o que tem na mão. Então:

      • ela é consultada UMA VEZ por relatório (`/updated` da Minha Receita);
      • a Minha Receita é a fonte PRIMÁRIA aqui, porque é a única que publica
        a referência — carimbar a data dela num registro que veio da BrasilAPI
        seria emprestar procedência, e ninguém perceberia;
      • linha que veio da retaguarda sai MARCADA na própria linha;
      • se `/updated` falhar, o cabeçalho diz "não informada pela fonte".
        Nunca a data de hoje disfarçada de referência.

A COLUNA DO CERTIFICADO
    Diz se a empresa tem `.pfx` no cofre — e distingue "não tem" de "está
    cadastrada mas o arquivo não está no disco". São problemas diferentes: o
    primeiro é cadastro a fazer, o segundo é certificado que sumiu, e quem lê
    a coluna precisa saber qual dos dois está olhando.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import re
from concurrent.futures import ThreadPoolExecutor

import cnpj_publico
import relatorio_base as _base

TITULO = "Relatório de Optantes do Simples Nacional"
SUBTITULO = "Base de Dados Abertos do CNPJ — Receita Federal"

# A coluna do certificado, em três estados honestos.
CERT_SIM = "sim"
CERT_SEM_ARQUIVO = "cadastrado, arquivo ausente"
CERT_NAO = "nao"

_CERT_ROTULO = {CERT_SIM: "Sim", CERT_SEM_ARQUIVO: "Arquivo ausente",
                CERT_NAO: "-"}

FONTE_PRIMARIA = "Minha Receita"

SEM_REFERENCIA = "não informada pela fonte"

COLUNAS = ("Empresa", "CNPJ", "Situação cadastral", "Optante do Simples",
           "MEI", "Opção em", "Exclusão em", "Início de atividade",
           "Município", "UF", "Certificado no cofre", "Fonte", "Observação")


def _dig(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


# ── quem entra na varredura ─────────────────────────────────────────────
def universo(clientes, certificados, resolver_cert=None) -> list:
    """A carteira a varrer: uma linha por documento, sem repetir.

    Junta as DUAS fontes de cadastro de propósito. O cadastro de Clientes é o
    oficial, mas há empresa que existe só como conta de certificado (a TRX
    ficou meses assim) — deixá-la fora faria o relatório dizer "toda a
    carteira" sobre uma parte dela.

    CPF fica de fora com motivo registrado: a Consulta Optantes é só para
    CNPJ, e um cliente pessoa física não é omissão, é natureza.
    """
    por_doc: dict[str, dict] = {}

    def guardar(doc, nome, fonte_cadastro):
        d = _dig(doc)
        if not d:
            return
        alvo = por_doc.setdefault(d, {
            "documento": d, "nome": "", "cadastros": [],
            "certificado": CERT_NAO})
        if nome and not alvo["nome"]:
            alvo["nome"] = str(nome).strip()
        if fonte_cadastro not in alvo["cadastros"]:
            alvo["cadastros"].append(fonte_cadastro)

    for c in (clientes or []):
        guardar(c.get("cnpj"), c.get("nome") or c.get("razao_social"), "Clientes")
    for c in (certificados or []):
        guardar(c.get("cnpj"), c.get("apelido") or c.get("nome"), "Certificado")
        d = _dig(c.get("cnpj"))
        if not d or d not in por_doc:
            continue
        caminho = c.get("caminho") or ""
        existe = False
        if caminho:
            try:
                from pathlib import Path
                alvo = resolver_cert(caminho) if resolver_cert else caminho
                existe = Path(alvo).is_file()
            except Exception:
                existe = False
        por_doc[d]["certificado"] = CERT_SIM if existe else CERT_SEM_ARQUIVO

    linhas = list(por_doc.values())
    for l in linhas:
        l["consultavel"] = len(l["documento"]) == 14
        if not l["consultavel"]:
            l["motivo"] = ("documento de 11 dígitos: a Consulta Optantes "
                           "é só para CNPJ")
    linhas.sort(key=lambda l: (l["nome"] or l["documento"]).casefold())
    return linhas


# ── a varredura ─────────────────────────────────────────────────────────
def varrer(empresas, consultar=None, paralelo: int = 8) -> dict:
    """Consulta a base pública de cada CNPJ. Nunca levanta.

    `consultar` existe para o teste injetar respostas sem ir à rede — a
    assinatura é a de `cnpj_publico.consultar`.
    """
    consultar = consultar or cnpj_publico.consultar
    alvos = [e for e in empresas if e.get("consultavel")]

    def um(e):
        try:
            r = consultar(e["documento"],
                          fontes=cnpj_publico.FONTES_COM_REFERENCIA)
        except Exception as exc:                       # defesa, não expectativa
            r = {"ok": False, "erro": "%s" % type(exc).__name__}
        return e["documento"], r

    respostas: dict[str, dict] = {}
    if alvos:
        with ThreadPoolExecutor(max_workers=max(1, paralelo)) as ex:
            for doc, r in ex.map(um, alvos):
                respostas[doc] = r

    linhas = []
    for e in empresas:
        linha = dict(e)
        r = respostas.get(e["documento"]) or {}
        if not e.get("consultavel"):
            linha.update(estado="fora do escopo", fonte="", observacao=e.get("motivo", ""))
        elif not r.get("ok"):
            linha.update(estado="não consultada", fonte="",
                         observacao=(r.get("erro") or "sem resposta")[:160])
        else:
            linha.update(
                nome=linha["nome"] or r.get("razao_social") or "",
                estado=("MEI" if r.get("mei")
                        else "optante" if r.get("optante_simples")
                        else "não optante"),
                situacao=r.get("situacao") or "",
                optante=bool(r.get("optante_simples")),
                mei=bool(r.get("mei")),
                data_opcao=r.get("data_opcao_simples") or "",
                data_exclusao=r.get("data_exclusao_simples") or "",
                inicio_atividade=r.get("inicio_atividade") or "",
                municipio=r.get("municipio") or "",
                uf=r.get("uf") or "",
                fonte=r.get("fonte") or "",
                observacao="",
            )
            # A MARCA NA PRÓPRIA LINHA. Sem ela, a data do cabeçalho valeria
            # para uma linha que não veio da fonte que publica a data.
            if linha["fonte"] and linha["fonte"] != FONTE_PRIMARIA:
                linha["observacao"] = ("veio da fonte de retaguarda (%s): "
                                       "sem referência publicada" % linha["fonte"])
        linhas.append(linha)

    return {
        "linhas": linhas,
        "consultadas": len([l for l in linhas if l.get("fonte")]),
        "total": len(linhas),
        "retaguarda": len([l for l in linhas
                           if l.get("fonte") and l["fonte"] != FONTE_PRIMARIA]),
        "falhas": len([l for l in linhas if l.get("estado") == "não consultada"]),
        "fora_do_escopo": len([l for l in linhas
                               if l.get("estado") == "fora do escopo"]),
    }


# ── CSV ─────────────────────────────────────────────────────────────────
def csv_bytes(resultado: dict, referencia: dict) -> bytes:
    """CSV com BOM e `;` — é o que o Excel pt-BR abre com dois cliques.

    A referência da base vai na PRIMEIRA linha, antes do cabeçalho: planilha
    sem ela é planilha que em três meses ninguém sabe de quando é.
    """
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Referência da base",
                referencia.get("referencia") or SEM_REFERENCIA])
    w.writerow(["Fonte", SUBTITULO])
    w.writerow([])
    w.writerow(list(COLUNAS))
    for l in resultado["linhas"]:
        w.writerow([
            l.get("nome") or "",
            _base.doc(l["documento"]),
            l.get("situacao") or "",
            "Sim" if l.get("optante") else ("Não" if l.get("fonte") else ""),
            "Sim" if l.get("mei") else ("Não" if l.get("fonte") else ""),
            _base.br(l.get("data_opcao")) if l.get("data_opcao") else "",
            _base.br(l.get("data_exclusao")) if l.get("data_exclusao") else "",
            _base.br(l.get("inicio_atividade")) if l.get("inicio_atividade") else "",
            l.get("municipio") or "",
            l.get("uf") or "",
            _CERT_ROTULO.get(l.get("certificado"), ""),
            l.get("fonte") or "",
            l.get("observacao") or "",
        ])
    return buf.getvalue().encode("utf-8-sig")


# ── PDF ─────────────────────────────────────────────────────────────────
_LARG = (62, 32, 26, 22, 26)     # empresa, CNPJ, situação, optante, certificado


def pdf_bytes(resultado: dict, referencia: dict, hoje=None) -> bytes:
    """O consolidado, em uma tabela. Sem marca do sistema em byte nenhum."""
    hoje = hoje or _dt.date.today()
    capa = _base.Capa(TITULO)
    capa.pagina()

    capa.titulo(TITULO)
    capa.pdf.set_font("Helvetica", "", 9.5)
    capa.pdf.set_text_color(60, 72, 80)
    capa.pdf.cell(0, 5, _base.seguro(SUBTITULO), new_x="LMARGIN", new_y="NEXT")

    # ── A DATA DE REFERÊNCIA, EM DESTAQUE E EXPLÍCITA ──
    ref = referencia.get("referencia") or ""
    capa.pdf.ln(1)
    capa.pdf.set_font("Helvetica", "B", 10.5)
    capa.pdf.set_text_color(20, 30, 40)
    capa.pdf.cell(0, 6, _base.seguro(
        "Referência da base: %s" % (_mes_br(ref) if ref else SEM_REFERENCIA)),
        new_x="LMARGIN", new_y="NEXT")
    capa.pdf.set_font("Helvetica", "", 8.5)
    capa.pdf.set_text_color(110, 120, 128)
    capa.pdf.multi_cell(capa.LARGURA, 4, _base.seguro(
        "A base publica e atualizada em lote, mensalmente. O que estiver nela "
        "reflete a data acima, nao o dia da consulta: alteracao posterior "
        "(inclusive exclusao do Simples) ainda nao aparece aqui."
        if ref else
        "A fonte nao informou a data de referencia nesta consulta. Sem ela nao "
        "e possivel afirmar a que mes os dados abaixo se referem."))
    capa.pdf.ln(1)
    capa.pdf.set_font("Helvetica", "", 8.5)
    capa.pdf.cell(0, 4.5, _base.seguro(
        "Emitido em %s - %d empresa(s), %d consultada(s)"
        % (hoje.strftime("%d/%m/%Y"), resultado["total"], resultado["consultadas"])),
        new_x="LMARGIN", new_y="NEXT")
    if resultado.get("retaguarda"):
        capa.pdf.multi_cell(capa.LARGURA, 4, _base.seguro(
            "%d linha(s) vieram da fonte de retaguarda e estao marcadas: para "
            "elas nao existe data de referencia publicada."
            % resultado["retaguarda"]))
    capa.risco()

    _tabela(capa, resultado)

    # ── o rodapé diz o que o documento É, para não ser lido como certidão ──
    capa.pdf.ln(3)
    capa.pdf.set_font("Helvetica", "", 7.5)
    capa.pdf.set_text_color(130, 138, 145)
    capa.pdf.multi_cell(capa.LARGURA, 3.4, _base.seguro(
        "Levantamento sobre base publica de consulta. Nao e certidao e nao "
        "substitui a Consulta Optantes oficial do Simples Nacional, que deve "
        "ser usada em caso de divergencia."))

    return capa.bytes()


def _mes_br(ref: str) -> str:
    """'2026-08' -> '08/2026'. Com dia, mantém a data cheia."""
    p = str(ref).split("-")
    if len(p) >= 3:
        return _base.br(ref)
    return "%s/%s" % (p[1], p[0]) if len(p) == 2 else str(ref)


def _caber(capa, texto: str, larg_mm: float) -> str:
    """Corta o texto para caber na coluna, medindo a LARGURA e não o número
    de caracteres.

    Cortar em N caracteres não funciona: "ANDRADE NASCIMENTO REPRESENTACOES
    LTDA" tem 38 letras e ainda passa de 62 mm, invadindo a coluna do CNPJ ao
    lado. Em tabela, texto que transborda não é feio — é ilegível, porque o
    leitor não sabe onde um campo termina e o outro começa.
    """
    t = _base.seguro(texto)
    folga = larg_mm - 1.5
    if capa.pdf.get_string_width(t) <= folga:
        return t
    while t and capa.pdf.get_string_width(t + "...") > folga:
        t = t[:-1]
    return (t.rstrip() + "...") if t else ""


def _tabela(capa, resultado: dict) -> None:
    def cabecalho():
        capa.pdf.set_font("Helvetica", "B", 7.5)
        capa.pdf.set_text_color(110, 120, 128)
        for rot, larg in zip(("Empresa", "CNPJ", "Situação", "Simples",
                              "Certificado"), _LARG):
            capa.pdf.cell(larg, 5, _base.seguro(rot))
        capa.pdf.ln(5)

    cabecalho()
    for l in resultado["linhas"]:
        if capa.pdf.get_y() > 262:
            capa.pagina()
            cabecalho()
        capa.pdf.set_font("Helvetica", "", 8)
        capa.pdf.set_text_color(30, 40, 50)
        estado = l.get("estado") or ""
        valores = (
            l.get("nome") or _base.doc(l["documento"]),
            _base.doc(l["documento"]),
            l.get("situacao") or _base.TRACO,
            {"optante": "Sim", "MEI": "MEI", "não optante": "Nao"}.get(
                estado, _base.TRACO),
            _CERT_ROTULO.get(l.get("certificado"), _base.TRACO),
        )
        for v, larg in zip(valores, _LARG):
            capa.pdf.cell(larg, 5, _caber(capa, v, larg))
        capa.pdf.ln(5)
        obs = l.get("observacao") or ""
        if obs:
            capa.pdf.set_font("Helvetica", "", 7)
            capa.pdf.set_text_color(140, 110, 60)
            capa.pdf.cell(0, 4, _base.seguro("      " + obs[:120]),
                          new_x="LMARGIN", new_y="NEXT")


def nome_csv(hoje=None) -> str:
    return _base.nome_de_arquivo("", hoje, "optantes-simples-nacional.csv")


def nome_pdf(hoje=None) -> str:
    return _base.nome_de_arquivo("", hoje, "optantes-simples-nacional.pdf")
