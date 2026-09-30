"""
pdflocal.py — Gera um PDF da NFS-e a partir do XML (offline, ilimitado),
reproduzindo o layout do DANFSe oficial v1.0 medida por medida.

Reproduz as mesmas seções e campos do documento oficial (cabeçalho com logo,
QR code, emitente, tomador, intermediário, serviço, tributação municipal e
federal, valor total, totais aproximados e informações complementares). O
rodapé indica que foi gerado localmente. Não depende do serviço da Receita.
"""

from __future__ import annotations

import re as _re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://www.sped.fazenda.gov.br/nfse}"

# Logo oficial NFS-e empacotado junto ao frontend (PyInstaller: _MEIPASS)
_RES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
_LOGO = _RES / "frontend" / "danfse_logo.png"

# Tabela IBGE código -> nome do município (para resolver qualquer cidade,
# como o DANFSe oficial faz). Carregada uma vez; vazia se o arquivo faltar.
try:
    import json as _json
    _MUNS = _json.loads((Path(__file__).resolve().parent / "municipios.json").read_text("utf-8"))
except Exception:
    _MUNS = {}

# Contato da prefeitura no cabeçalho (dado municipal, fora do XML) — apenas
# municípios conhecidos; os demais mostram só "Secretaria de Finanças".
CONTATO_MUN = {"2611606": "faleconosco@recife.pe.gov.br"}

UF_PREFIXO = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP",
    "17": "TO", "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB",
    "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
    "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
    "51": "MT", "52": "GO", "53": "DF",
}
OP_SIMP = {
    "1": "Não optante",
    "2": "Optante - Microempreendedor Individual (MEI)",
    "3": "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)",
}
REG_AP_SN = {
    "1": "Regime de apuração dos tributos federais e municipal pelo Simples Nacional",
    "2": "Regime de apuração dos tributos federais pelo SN e ISSQN por fora do SN",
    "3": "Regime de apuração dos tributos federais e municipal por fora do SN",
}
REG_ESP = {
    "0": "Nenhum", "1": "Ato Cooperado", "2": "Estimativa",
    "3": "Microempresa Municipal", "4": "Notário ou Registrador",
    "5": "Profissional Autônomo", "6": "Sociedade de Profissionais",
}
TRIB_ISSQN = {
    "1": "Operação Tributável", "2": "Exportação de Serviço",
    "3": "Não Incidência", "4": "Imunidade",
    "5": "Exigibilidade Suspensa por Decisão Judicial",
    "6": "Exigibilidade Suspensa por Processo Administrativo",
}
RET_ISSQN = {"1": "Não Retido", "2": "Retido pelo Tomador", "3": "Retido pelo Intermediário"}
TP_IMUN = {
    "0": "Imunidade (tipo não informado)",
    "1": "Patrimônio, renda ou serviços entre entes federados",
    "2": "Templos de qualquer culto", "3": "Partidos políticos e sindicatos",
    "4": "Livros, jornais e periódicos", "5": "Fonogramas e videofonogramas",
}



# ─────────────────────────────────────────────────────────────────────────────
# Geometria do DANFSe oficial
#
# Todos os números abaixo foram MEDIDOS num DANFSe baixado do portal nacional
# (posição, corpo e altura de linha de cada texto, e as réguas horizontais),
# não estimados no olho. É o que faz o PDF gerado aqui sair praticamente
# sobreponível ao da Receita — inclusive o fato de que o documento oficial
# não usa negrito em lugar nenhum: o que separa título, rótulo e valor é só o
# corpo da fonte (9 / 8 / 7 pt).
#
# Unidade: PONTOS (a página A4 tem 595 x 842 pt).
# ─────────────────────────────────────────────────────────────────────────────
MOL_X0, MOL_Y0, MOL_X1, MOL_Y1 = 5.0, 5.0, 590.0, 837.0   # moldura da página
LARG_MOLDURA = 1.0
REG_X0, REG_X1 = 10.8, 577.7          # extremos das réguas entre as seções
LARG_REGUA = 0.5
COL_X = (14.2, 155.9, 297.6, 439.4)   # início do texto em cada coluna
COL_W = 141.7                         # passo entre colunas
PAD = 6.8                             # respiro à direita dentro da coluna

S_CAB, S_TIT, S_VAL, S_ROT, S_MIUDO = 9.0, 8.0, 8.0, 7.0, 6.0
H_ROT, H_VAL = 7.9, 9.0               # altura da linha do rótulo e do valor
H_GAP = 4.4                           # respiro no rodapé de cada faixa
H_TITULO = 13.3                       # faixa ocupada só pelo título da seção
H_FAIXA = 9.5                         # faixa do "NÃO IDENTIFICADO NA NFS-e"

Y_TOPO = 41.2                         # régua que fecha o cabeçalho
Y_LEGENDA = 98.1                      # 1ª linha da legenda do QR
Y_POS_QR = 123.3                      # onde a seção EMITENTE pode começar
Y_CONTINUA = 14.2                     # topo do conteúdo nas páginas seguintes
LIM_Y = 830.0                         # última linha útil antes da moldura

LOGO_X, LOGO_Y, LOGO_W, LOGO_H = 14.2, 12.6, 113.4, 22.5
TIT_X, TIT_W = 141.0, 220.0           # bloco "DANFSe v1.0", centralizado
QR_X, QR_Y, QR_L = 481.8, 46.0, 50.0

# URL lida do QR de um DANFSe oficial. Não inventar: é por ela que o fiscal
# confere a nota no portal nacional.
URL_CONSULTA = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave="
LEGENDA_QR = (
    "A autenticidade desta NFS-e pode ser verificada",
    "pela leitura deste código QR ou pela consulta da",
    "chave de acesso no portal nacional da NFS-e",
)

# Municípios cujo nome pede "do" em vez de "de" ("Prefeitura do Recife").
ARTIGO_DO = {"recife", "rio de janeiro", "cabo de santo agostinho",
             "crato", "guarujá", "jaboatão dos guararapes", "salvador"}


def _txt(el, path):
    if el is None:
        return None
    a = el.find(path)
    return a.text if a is not None else None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _brl(v) -> str:
    s = f"{_num(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _money(v) -> str:
    return _brl(v) if _num(v) > 0 else "-"


def _pct(v) -> str:
    return (f"{_num(v):.2f}".replace(".", ",") + " %") if _num(v) else "-"


def _doc(d) -> str:
    d = "".join(ch for ch in (d or "") if ch.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d or "-"


def _cep(c) -> str:
    c = "".join(ch for ch in (c or "") if ch.isdigit())
    return f"{c[:5]}-{c[5:]}" if len(c) == 8 else (c or "-")


def _fone(f) -> str:
    f = "".join(ch for ch in (f or "") if ch.isdigit())
    if len(f) == 11:
        return f"({f[:2]}) {f[2:7]}-{f[7:]}"
    if len(f) == 10:
        return f"({f[:2]}) {f[2:6]}-{f[6:]}"
    return f or "-"


def _dt(iso) -> str:
    iso = iso or ""
    if len(iso) >= 19 and "T" in iso:
        d, h = iso[:10], iso[11:19]
        return f"{d[8:10]}/{d[5:7]}/{d[0:4]} {h}"
    return _d(iso)


def _d(iso) -> str:
    iso = (iso or "")[:10]
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}" if len(iso) == 10 else (iso or "-")


def _s(t) -> str:
    """Fontes core do PDF usam latin-1 (cobre o português acentuado)."""
    return (t or "").encode("latin-1", "replace").decode("latin-1")


def _sem_uf(nome) -> str:
    """Tira o " - UF" que alguns municípios grudam no próprio nome.

    O xLocEmi/xLocPrestacao de algumas prefeituras já vem "QUIRINOPOLIS - GO";
    colar a UF de novo produzia "QUIRINÓPOLIS - GO - GO" no PDF."""
    nome = (nome or "").strip()
    return _re.sub(r"\s*[-/]\s*[A-Za-z]{2}$", "", nome)


def _resumir(desc, n: int = 57) -> str:
    """Trunca a descrição como no DANFSe oficial (57 caracteres + '...')."""
    d = (desc or "").strip()
    return d if len(d) <= n else d[:n].rstrip() + "..."


def gerar(xml_text: str) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import MethodReturnValue, XPos, YPos

    root = ET.fromstring(xml_text)
    inf = root.find(f"{NS}infNFSe")
    if inf is None:
        raise ValueError("XML sem infNFSe")

    # ── extração ───────────────────────────────────────────────────────────
    chave = (inf.get("Id") or "").replace("NFS", "", 1)
    numero = _txt(inf, f"{NS}nNFSe") or "-"
    dh_proc = _txt(inf, f"{NS}dhProc") or ""
    x_loc_emi = _txt(inf, f"{NS}xLocEmi")
    x_loc_incid = _txt(inf, f"{NS}xLocIncid")
    c_loc_incid = _txt(inf, f"{NS}cLocIncid")
    x_loc_prest = _txt(inf, f"{NS}xLocPrestacao")
    x_trib_nac = _txt(inf, f"{NS}xTribNac")
    x_trib_mun = _txt(inf, f"{NS}xTribMun")

    emit = inf.find(f"{NS}emit")
    emit_fant = _txt(emit, f"{NS}xFant")
    emit_doc = _txt(emit, f"{NS}CNPJ") or _txt(emit, f"{NS}CPF")
    emit_im = _txt(emit, f"{NS}IM")
    emit_nome = _txt(emit, f"{NS}xNome")
    emit_fone = _txt(emit, f"{NS}fone")
    emit_email = _txt(emit, f"{NS}email")
    en = emit.find(f"{NS}enderNac") if emit is not None else None
    emit_uf = _txt(en, f"{NS}UF")
    emit_cmun = _txt(en, f"{NS}cMun")
    emit_end = ", ".join(x for x in [
        _txt(en, f"{NS}xLgr"), _txt(en, f"{NS}nro"),
        _txt(en, f"{NS}xCpl"), _txt(en, f"{NS}xBairro")] if x)
    emit_cep = _cep(_txt(en, f"{NS}CEP"))

    vbc = _txt(inf, f"{NS}valores/{NS}vBC")
    paliq = _txt(inf, f"{NS}valores/{NS}pAliqAplic")
    vissqn = _txt(inf, f"{NS}valores/{NS}vISSQN")
    vliq = _txt(inf, f"{NS}valores/{NS}vLiq")

    dps = inf.find(f"{NS}DPS/{NS}infDPS")
    dh_emi = _txt(dps, f"{NS}dhEmi")
    serie = _txt(dps, f"{NS}serie") or "-"
    ndps = _txt(dps, f"{NS}nDPS") or "-"
    dcompet = _txt(dps, f"{NS}dCompet")
    prest = dps.find(f"{NS}prest") if dps is not None else None
    op_simp = _txt(prest, f"{NS}regTrib/{NS}opSimpNac")
    reg_ap = _txt(prest, f"{NS}regTrib/{NS}regApTribSN")
    reg_esp = _txt(prest, f"{NS}regTrib/{NS}regEspTrib")

    toma = dps.find(f"{NS}toma") if dps is not None else None
    toma_doc = _txt(toma, f"{NS}CNPJ") or _txt(toma, f"{NS}CPF")
    toma_im = _txt(toma, f"{NS}IM")
    toma_nome = _txt(toma, f"{NS}xNome")
    toma_fone = _txt(toma, f"{NS}fone")
    toma_email = _txt(toma, f"{NS}email")
    tend = toma.find(f"{NS}end") if toma is not None else None
    toma_cmun = _txt(tend, f"{NS}endNac/{NS}cMun")
    toma_cep = _cep(_txt(tend, f"{NS}endNac/{NS}CEP"))
    toma_end = ", ".join(x for x in [
        _txt(tend, f"{NS}xLgr"), _txt(tend, f"{NS}nro"),
        _txt(tend, f"{NS}xCpl"), _txt(tend, f"{NS}xBairro")] if x)

    interm = dps.find(f"{NS}interm") if dps is not None else None

    serv = dps.find(f"{NS}serv") if dps is not None else None
    c_trib_nac = _txt(serv, f"{NS}cServ/{NS}cTribNac")
    c_trib_mun = _txt(serv, f"{NS}cServ/{NS}cTribMun")
    xdesc = _txt(serv, f"{NS}cServ/{NS}xDescServ")
    pais_prest = _txt(serv, f"{NS}locPrest/{NS}cPaisPrestacao")
    c_loc_prest = _txt(serv, f"{NS}locPrest/{NS}cLocPrestacao")
    x_inf_comp = _txt(serv, f"{NS}infoCompl/{NS}xInfComp") or _txt(dps, f".//{NS}xInfComp")

    tribmun = dps.find(f".//{NS}tribMun") if dps is not None else None
    trib_issqn = _txt(tribmun, f"{NS}tribISSQN")
    tp_ret_iss = _txt(tribmun, f"{NS}tpRetISSQN")
    tp_imun = _txt(tribmun, f"{NS}tpImunidade")
    v_serv = _txt(dps, f".//{NS}vServ")
    v_desc_incond = _txt(dps, f".//{NS}vDescIncond")
    v_desc_cond = _txt(dps, f".//{NS}vDescCond")
    v_ded_red = _txt(dps, f".//{NS}vDR")

    pc = inf.find(f".//{NS}piscofins")
    v_pis = _num(_txt(pc, f"{NS}vPis")) if pc is not None else 0.0
    v_cofins = _num(_txt(pc, f"{NS}vCofins")) if pc is not None else 0.0
    tp_ret_pc = _txt(pc, f"{NS}tpRetPisCofins") if pc is not None else None
    v_csll = _num(_txt(inf, f".//{NS}vRetCSLL"))
    v_irrf = _num(_txt(inf, f".//{NS}vRetIRRF"))
    v_cp = _num(_txt(inf, f".//{NS}vRetCP"))
    iss_retido = _num(vissqn) if tp_ret_iss in ("2", "3") else 0.0
    # A MESMA regra da tela (retencao.compor): somar vPis/vCofins por cima de
    # um vRetCSLL que já é o consolidado duplicava o total do documento.
    import retencao as _ret
    _v_serv = _num(_txt(inf, f".//{NS}vServ"))
    tot_ret_fed = v_irrf + v_cp + _ret.compor(tp_ret_pc, v_pis, v_cofins,
                                              v_csll, _v_serv)["total"]
    # PIS/COFINS de apuração própria (não retidos)
    pc_proprio = (v_pis + v_cofins) if tp_ret_pc in ("2",) else 0.0

    def municipio(cmun, uf=None):
        nome = _MUNS.get(cmun or "") \
            or (x_loc_incid if cmun == c_loc_incid else (x_loc_emi if cmun == emit_cmun else None))
        uf = uf or UF_PREFIXO.get((cmun or "")[:2])
        if nome and uf:
            return f"{nome} - {uf}"
        return nome or (f"{cmun} - {uf}" if cmun and uf else (cmun or "-"))

    emit_mun = municipio(emit_cmun, emit_uf)
    toma_mun = municipio(toma_cmun)
    comp = _d(dcompet)

    def cod_desc(cod, desc):
        # Igual ao oficial: código + descrição resumida em 57 caracteres ("...").
        # Sem código mas com descrição, o oficial mostra "- descrição...".
        c = f"{cod[:2]}.{cod[2:4]}.{cod[4:]}" if (cod and cod.isdigit() and len(cod) == 6) else (cod or "")
        d = _resumir(desc)
        if c and d:
            return f"{c} - {d}"
        if d:
            return f"- {d}"
        return c or "-"

    # ── desenho ────────────────────────────────────────────────────────────
    pdf = FPDF(format="A4", unit="pt")
    pdf.set_auto_page_break(False)
    pdf.set_margins(0, 0, 0)
    pdf.set_right_margin(0)
    pdf.c_margin = 0            # o x que eu passo é o x do texto, sem folga
    pdf.set_text_color(0, 0, 0)

    cur = [0.0]                      # cursor vertical (topo da próxima faixa)

    def _n_linhas(texto, size, larg, lh) -> int:
        pdf.set_font("Helvetica", "", size)
        h = pdf.multi_cell(larg, lh, _s(texto), dry_run=True,
                           output=MethodReturnValue.HEIGHT)
        return max(1, round(h / lh))

    def _escrever(x, y, texto, size, larg, lh, align="L"):
        # O multi_cell centraliza o texto na altura da linha, e o DANFSe alinha
        # pelo topo. Esta correção é a distância entre um e outro: metade da
        # sobra da linha mais o vão que a Helvetica deixa acima da maiúscula.
        # Sem ela o documento inteiro sobe ~1,5 pt em relação ao oficial.
        dy = (lh - size) / 2 + size * 0.145
        pdf.set_font("Helvetica", "", size)
        pdf.set_xy(x, y + dy)
        pdf.multi_cell(larg, lh, _s(texto), align=align,
                       new_x=XPos.LEFT, new_y=YPos.TOP)

    def regua(y):
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(LARG_REGUA)
        pdf.line(REG_X0, y, REG_X1, y)

    def abrir_pagina():
        pdf.add_page()
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(LARG_MOLDURA)
        pdf.rect(MOL_X0, MOL_Y0, MOL_X1 - MOL_X0, MOL_Y1 - MOL_Y0)
        cur[0] = Y_CONTINUA

    def espaco(altura):
        """Garante `altura` livre nesta página; senão abre a próxima."""
        if cur[0] + altura > LIM_Y:
            abrir_pagina()

    def _medir(campos):
        med = []
        for col, span, rot, val in campos:
            larg = span * COL_W - PAD
            nr = _n_linhas(rot, S_ROT, larg, H_ROT) if rot else 0
            texto = val if val not in (None, "") else "-"
            nv = _n_linhas(texto, S_VAL, larg, H_VAL)
            med.append((col, larg, rot, texto, nr, nv))
        return med

    def _pintar(med, y):
        for col, larg, rot, texto, nr, nv in med:
            x = COL_X[col]
            if rot:
                _escrever(x, y, rot, S_ROT, larg, H_ROT)
            _escrever(x, y + nr * H_ROT, texto, S_VAL, larg, H_VAL)

    def linha(campos):
        """Uma faixa rótulo/valor da grade.

        `campos` = (coluna, colunas_ocupadas, rótulo, valor). Reproduz a grade
        do DANFSe oficial: quatro colunas de largura fixa, rótulo de 7 pt em
        cima e valor de 8 pt embaixo, ambos alinhados à esquerda da coluna. A
        altura da faixa é a do campo mais alto."""
        med = _medir(campos)
        alt = max(nr * H_ROT + nv * H_VAL for *_, nr, nv in med) + H_GAP
        espaco(alt)
        y = cur[0]
        _pintar(med, y)
        cur[0] = y + alt

    def secao(titulo, campos=None, subtitulo=None):
        """Cabeçalho de seção, sempre precedido da régua horizontal.

        Sem `campos` o título fica numa faixa só dele (como SERVIÇO PRESTADO);
        com `campos` ele divide a faixa com os primeiros dados da seção, na
        primeira coluna (como EMITENTE DA NFS-e)."""
        if campos is None:
            espaco(H_TITULO + H_FAIXA)
            regua(cur[0] - 0.2)
            _escrever(COL_X[0], cur[0], titulo, S_TIT, 3 * COL_W, H_VAL)
            cur[0] += H_TITULO
            return
        med = _medir(campos)
        alt_tit = (2 if subtitulo else 1) * H_VAL
        alt = max([alt_tit] + [nr * H_ROT + nv * H_VAL for *_, nr, nv in med]) + H_GAP
        espaco(alt)
        y = cur[0]
        regua(y - 0.2)
        _escrever(COL_X[0], y, titulo, S_TIT, COL_W - PAD, H_VAL)
        if subtitulo:
            _escrever(COL_X[0], y + H_VAL, subtitulo, S_VAL, COL_W - PAD, H_VAL)
        _pintar(med, y)
        cur[0] = y + alt

    def faixa(texto):
        """Aviso centralizado, como o "... NÃO IDENTIFICADO NA NFS-e"."""
        espaco(H_FAIXA * 2)
        regua(cur[0] - 0.2)
        _escrever(REG_X0, cur[0], texto, S_TIT, REG_X1 - REG_X0, H_VAL,
                  align="C")
        cur[0] += H_FAIXA

    # ── página 1: cabeçalho ────────────────────────────────────────────────
    abrir_pagina()
    if _LOGO.exists():
        pdf.image(str(_LOGO), x=LOGO_X, y=LOGO_Y, w=LOGO_W, h=LOGO_H)
    else:
        _escrever(LOGO_X, LOGO_Y + 4, "NFS-e", 16, LOGO_W, LOGO_H)
    _escrever(TIT_X, 14.5, "DANFSe v1.0", S_CAB, TIT_W, 10.2, align="C")
    _escrever(TIT_X, 24.7, "Documento Auxiliar da NFS-e", S_CAB, TIT_W, 10.2,
              align="C")

    # "Prefeitura do Recife", "Prefeitura de Olinda" — o artigo muda com a
    # cidade, e o oficial imprime o nome do município do emitente.
    nome_mun = _MUNS.get(emit_cmun or "") or _sem_uf(x_loc_emi) or "-"
    art = "do" if nome_mun.strip().lower() in ARTIGO_DO else "de"
    larg_dir = REG_X1 - COL_X[3]
    # As três linhas do canto direito têm posição fixa, como no oficial. Nome
    # comprido ("Jaboatão dos Guararapes") quebrava em duas e caía por cima da
    # linha de baixo; aqui o corpo encolhe até caber numa linha só.
    prefeitura = f"Prefeitura {art} {nome_mun}"
    corpo = S_VAL
    while corpo > 5.5 and _n_linhas(prefeitura, corpo, larg_dir, 9.1) > 1:
        corpo -= 0.5
    _escrever(COL_X[3], 8.5, prefeitura, corpo, larg_dir, 9.1)
    _escrever(COL_X[3], 17.6, "Secretaria de Finanças", S_MIUDO, larg_dir, 6.7)
    email_mun = CONTATO_MUN.get(emit_cmun or "")
    if email_mun:
        _escrever(COL_X[3], 24.3, email_mun, S_MIUDO, larg_dir, 6.7)

    # ── chave de acesso, identificação e QR ────────────────────────────────
    regua(Y_TOPO)
    import qrcode

    # A URL do QR é a mesma do DANFSe oficial (lida do código de um PDF do
    # portal). Com outra URL o QR abre uma página que não existe — e é o QR
    # que o fiscal usa para conferir a nota.
    pdf.image(qrcode.make(URL_CONSULTA + chave).get_image(),
              x=QR_X, y=QR_Y, w=QR_L, h=QR_L)
    for i, ln in enumerate(LEGENDA_QR):
        _escrever(COL_X[3], Y_LEGENDA + i * 6.8, ln, S_MIUDO, larg_dir, 6.7)

    cur[0] = 45.7
    _escrever(COL_X[0], cur[0], "Chave de Acesso da NFS-e", S_ROT,
              3 * COL_W - PAD, H_ROT)
    _escrever(COL_X[0], cur[0] + H_ROT, chave or "-", S_VAL,
              3 * COL_W - PAD, H_VAL)
    cur[0] = 66.9
    linha([(0, 1, "Número da NFS-e", numero),
           (1, 1, "Competência da NFS-e", comp),
           (2, 1, "Data e Hora da emissão da NFS-e", _dt(dh_proc))])
    linha([(0, 1, "Número da DPS", ndps),
           (1, 1, "Série da DPS", serie),
           (2, 1, "Data e Hora da emissão da DPS", _dt(dh_emi))])
    cur[0] = max(cur[0], Y_POS_QR)     # o QR e a legenda são mais altos

    # ── emitente ───────────────────────────────────────────────────────────
    secao("EMITENTE DA NFS-e", subtitulo="Prestador do Serviço", campos=[
        (1, 1, "CNPJ / CPF / NIF", _doc(emit_doc)),
        (2, 1, "Inscrição Municipal", emit_im),
        (3, 1, "Telefone", _fone(emit_fone))])
    linha([(0, 2, "Nome / Nome Empresarial", emit_nome),
           (2, 2, "E-mail", emit_email)])
    linha([(0, 2, "Endereço", emit_end),
           (2, 1, "Município", emit_mun),
           (3, 1, "CEP", emit_cep)])
    linha([(0, 2, "Simples Nacional na Data de Competência",
            OP_SIMP.get(op_simp, "-")),
           (2, 2, "Regime de Apuração Tributária pelo SN",
            REG_AP_SN.get(reg_ap, "-"))])

    # ── tomador ────────────────────────────────────────────────────────────
    # O oficial não desenha a seção vazia: sem tomador na NFS-e ele imprime uma
    # faixa única dizendo isso. Vale o mesmo para o intermediário.
    if toma is None or not (toma_doc or toma_nome):
        faixa("TOMADOR DO SERVIÇO NÃO IDENTIFICADO NA NFS-e")
    else:
        secao("TOMADOR DO SERVIÇO", campos=[
            (1, 1, "CNPJ / CPF / NIF", _doc(toma_doc)),
            (2, 1, "Inscrição Municipal", toma_im),
            (3, 1, "Telefone", _fone(toma_fone))])
        linha([(0, 2, "Nome / Nome Empresarial", toma_nome),
               (2, 2, "E-mail", toma_email)])
        linha([(0, 2, "Endereço", toma_end),
               (2, 1, "Município", toma_mun),
               (3, 1, "CEP", toma_cep)])

    # ── intermediário ──────────────────────────────────────────────────────
    if interm is None:
        faixa("INTERMEDIÁRIO DO SERVIÇO NÃO IDENTIFICADO NA NFS-e")
    else:
        secao("INTERMEDIÁRIO DO SERVIÇO", campos=[
            (1, 1, "CNPJ / CPF / NIF",
             _doc(_txt(interm, f"{NS}CNPJ") or _txt(interm, f"{NS}CPF"))),
            (2, 2, "Nome / Nome Empresarial", _txt(interm, f"{NS}xNome"))])

    # ── serviço prestado ───────────────────────────────────────────────────
    secao("SERVIÇO PRESTADO")
    uf_prest = UF_PREFIXO.get((c_loc_prest or "")[:2])
    loc_prest = x_loc_prest
    if x_loc_prest and uf_prest and not x_loc_prest.rstrip().upper().endswith(uf_prest):
        loc_prest = f"{x_loc_prest} - {uf_prest}"
    linha([(0, 1, "Código de Tributação Nacional",
            cod_desc(c_trib_nac, x_trib_nac)),
           (1, 1, "Código de Tributação Municipal",
            cod_desc(c_trib_mun, x_trib_mun)),
           (2, 1, "Local da Prestação", loc_prest),
           (3, 1, "País da Prestação", pais_prest)])
    linha([(0, 4, "Descrição do Serviço",
            _re.sub(r"\n{2,}", "\n", (xdesc or "-").strip()))])

    # ── tributação municipal ───────────────────────────────────────────────
    secao("TRIBUTAÇÃO MUNICIPAL")
    linha([(0, 1, "Tributação do ISSQN", TRIB_ISSQN.get(trib_issqn, "-")),
           (1, 1, "País Resultado da Prestação do Serviço", "-"),
           (2, 1, "Município de Incidência do ISSQN", municipio(c_loc_incid)),
           (3, 1, "Regime Especial de Tributação", REG_ESP.get(reg_esp, "-"))])
    linha([(0, 1, "Tipo de Imunidade",
            TP_IMUN.get(tp_imun, "-") if tp_imun else "-"),
           (1, 1, "Suspensão da Exigibilidade do ISSQN", "Não"),
           (2, 1, "Número Processo Suspensão", "-"),
           (3, 1, "Benefício Municipal", "-")])
    linha([(0, 1, "Valor do Serviço", _brl(v_serv or vbc)),
           (1, 1, "Desconto Incondicionado", _money(v_desc_incond)),
           (2, 1, "Total Deduções/Reduções", _money(v_ded_red)),
           (3, 1, "Cálculo do BM", "-")])
    linha([(0, 1, "BC ISSQN", _money(vbc)),
           (1, 1, "Alíquota Aplicada", _pct(paliq)),
           (2, 1, "Retenção do ISSQN", RET_ISSQN.get(tp_ret_iss, "-")),
           (3, 1, "ISSQN Apurado", _money(vissqn))])

    # ── tributação federal ─────────────────────────────────────────────────
    secao("TRIBUTAÇÃO FEDERAL")
    linha([(0, 1, "IRRF", _money(v_irrf)),
           (1, 1, "Contribuição Previdenciária - Retida", _money(v_cp)),
           (2, 1, "Contribuições Sociais - Retidas", _money(v_csll)),
           (3, 1, "Descrição Contrib. Sociais - Retidas", "-")])
    linha([(0, 1, "PIS - Débito Apuração Própria",
            _money(v_pis if tp_ret_pc == "2" else 0)),
           (1, 1, "COFINS - Débito Apuração Própria",
            _money(v_cofins if tp_ret_pc == "2" else 0))])

    # ── valor total ────────────────────────────────────────────────────────
    secao("VALOR TOTAL DA NFS-E")
    linha([(0, 1, "Valor do Serviço", _brl(v_serv or vbc)),
           (1, 1, "Desconto Condicionado", _money(v_desc_cond)),
           (2, 1, "Desconto Incondicionado", _money(v_desc_incond)),
           (3, 1, "ISSQN Retido", _money(iss_retido))])
    linha([(0, 1, "Total das Retenções Federais", _money(tot_ret_fed)),
           (1, 1, "PIS/COFINS - Débito Apur. Própria", _money(pc_proprio)),
           (3, 1, "Valor Líquido da NFS-e", _brl(vliq))])

    # ── totais aproximados ─────────────────────────────────────────────────
    secao("TOTAIS APROXIMADOS DOS TRIBUTOS")
    espaco(H_ROT + H_VAL + H_GAP)
    y = cur[0]
    terco = (REG_X1 - REG_X0) / 3
    for i, rot in enumerate(("Federais", "Estaduais", "Municipais")):
        x = REG_X0 + i * terco
        _escrever(x, y, rot, S_ROT, terco, H_ROT, align="C")
        _escrever(x, y + H_ROT, "-", S_VAL, terco, H_VAL, align="C")
    cur[0] = y + H_ROT + H_VAL + H_GAP

    # ── informações complementares ─────────────────────────────────────────
    secao("INFORMAÇÕES COMPLEMENTARES")
    infos = []
    if emit_fant:
        infos.append(f"Nome: {emit_fant}")
    if x_inf_comp:
        infos.append(x_inf_comp.strip())
    if infos:
        espaco(H_VAL + H_GAP)
        _escrever(COL_X[0], cur[0], "\n".join(infos), S_VAL,
                  4 * COL_W - PAD, H_VAL)

    return bytes(pdf.output())
