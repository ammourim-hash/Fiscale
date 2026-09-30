"""
danfe.py — a representação auxiliar da NF-e modelo 55, gerada do XML.

O QUE O DANFE É, E O QUE ELE NÃO É
    Ele é **auxiliar**. O documento fiscal é o XML autorizado, e é ele que o
    FISCALE preserva byte a byte no acervo. O PDF daqui é uma leitura do XML
    em papel — descartável, reconstruível, e sem nenhum valor próprio.

    Por isso o rodapé de toda página diz de onde o arquivo veio e como
    conferir. Um PDF que se apresenta como o documento é o começo de alguém
    arquivar o PDF e perder o XML.

POR QUE `fpdf2`, E NÃO OUTRA COISA
    Ela **já está no projeto** e já gera o DANFSe da NFS-e (`pdflocal.py`).
    Escolher outra biblioteca custaria uma dependência nova em um sistema que
    é distribuído como pacote portátil de 82 MB, sem instalador e sem rede —
    cada MB e cada roda nova é problema de distribuição, não detalhe.

    `brazilfiscalreport` resolveria o DANFE pronto e é a escolha óbvia num
    projeto com `pip install` disponível. Aqui ela significaria acrescentar a
    biblioteca e as dependências dela ao portátil, e revisar licença de algo
    que produz documento fiscal. Ficou registrada como alternativa; não foi
    adotada nesta fase.

O QUE ELE RECUSA
    XML de RESUMO (`resNFe`) não vira DANFE. O resumo tem emitente, valor e
    situação — não tem itens, não tem tributo, não tem transporte. Um PDF
    "quase DANFE" com metade dos campos vazios seria pior do que a recusa,
    porque pareceria completo.

CODE-128C
    A chave de 44 dígitos entra em código de barras CODE-128C, que é o que o
    padrão da NF-e manda. `fpdf2` só traz Code39, e nenhuma biblioteca de
    código de barras está no projeto — então a codificação está aqui, com a
    tabela de padrões e o dígito verificador conferidos por teste.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

# Sobe quando o desenho muda. Entra na chave do cache derivado: um PDF gerado
# por versão antiga não pode ser servido como se fosse desta.
VERSAO = 1

NS = "{http://www.portalfiscal.inf.br/nfe}"


class DanfeIndisponivel(Exception):
    """O documento existe, mas não dá para representá-lo. Diz o porquê."""


# ════════════════════════════════════════════════════════════════════════════
#  CODE-128C
# ════════════════════════════════════════════════════════════════════════════
# Cada padrão são 6 larguras (barra, espaço, barra, espaço, barra, espaço),
# 11 módulos ao todo. O último (STOP) tem 13 módulos em 7 larguras.
_PADROES = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()
assert len(_PADROES) == 107, "a tabela do Code128 tem 107 símbolos (0..106)"

_START_C = 105
_STOP = 106


def code128c(digitos: str) -> list[tuple[int, bool]]:
    """`(largura_em_modulos, é_barra)` da esquerda para a direita.

    Só dígitos, e em quantidade par — que é o caso da chave de acesso (44).
    Devolver a geometria em vez de uma imagem deixa o desenho para quem sabe o
    tamanho da página, e deixa a codificação testável sem abrir PDF.
    """
    d = "".join(c for c in str(digitos or "") if c.isdigit())
    if not d:
        raise ValueError("código de barras sem dígitos")
    if len(d) % 2:
        d = "0" + d          # CODE-C só codifica pares
    valores = [_START_C] + [int(d[i:i + 2]) for i in range(0, len(d), 2)]

    # Dígito verificador: soma ponderada pela posição, módulo 103. O START
    # entra com peso 1, e não com o peso da posição dele — é assim na norma.
    soma = valores[0] + sum(i * v for i, v in enumerate(valores[1:], start=1))
    valores.append(soma % 103)
    valores.append(_STOP)

    saida: list[tuple[int, bool]] = []
    for v in valores:
        barra = True
        for largura in _PADROES[v]:
            saida.append((int(largura), barra))
            barra = not barra
    return saida


# ════════════════════════════════════════════════════════════════════════════
#  Leitura do XML
# ════════════════════════════════════════════════════════════════════════════
def _txt(el, *caminho) -> str:
    if el is None:
        return ""
    alvo = el
    for parte in caminho:
        if alvo is None:
            return ""
        alvo = alvo.find(NS + parte)
    if alvo is None:
        return ""
    return (alvo.text or "").strip()


def _dec(v) -> Decimal | None:
    """Ausente é `None`, nunca `Decimal('0')`. `0` afirma que o documento
    informou zero, e no DANFE isso vira uma linha de imposto que não existe."""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _n(v, casas: int = 2) -> str:
    """`1234.5` → `1.234,50`. Ausente vira string vazia, não `0,00`."""
    d = _dec(v)
    if d is None:
        return ""
    q = Decimal(1).scaleb(-casas)
    texto = f"{d.quantize(q):,}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _data(v: str) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    d = s[:10].split("-")
    if len(d) != 3:
        return s
    hora = s[11:16] if len(s) >= 16 and "T" in s else ""
    return f"{d[2]}/{d[1]}/{d[0]}" + (f" {hora}" if hora else "")


def _doc(v: str) -> str:
    d = "".join(c for c in str(v or "") if c.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d


def _chave_formatada(c: str) -> str:
    d = "".join(x for x in str(c or "") if x.isdigit())
    return " ".join(d[i:i + 4] for i in range(0, len(d), 4))


def _l1(texto: str) -> str:
    """As fontes básicas do PDF são Latin-1. Caractere fora dela vira `?` em
    vez de derrubar a geração — um DANFE com um símbolo trocado é melhor que
    nenhum, e o XML original continua íntegro do lado."""
    return str(texto or "").encode("latin-1", "replace").decode("latin-1")


def _endereco(el) -> str:
    if el is None:
        return ""
    partes = [_txt(el, "xLgr"), _txt(el, "nro")]
    rua = " ".join(p for p in partes if p)
    comp = _txt(el, "xCpl")
    if comp:
        rua += f" - {comp}"
    return rua


def ler(xml: bytes | str) -> dict:
    """O XML vira o dicionário que o desenho consome. Sem inventar campo.

    Levanta `DanfeIndisponivel` quando o documento não é uma NF-e completa —
    é onde o resumo é recusado, antes de qualquer página existir.
    """
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as e:
        raise DanfeIndisponivel(f"XML ilegível: {type(e).__name__}") from e

    local = raiz.tag.split("}")[-1]
    if local == "resNFe":
        raise DanfeIndisponivel(
            "DANFE indisponível — XML completo necessário. Este documento "
            "está no acervo apenas como RESUMO da distribuição (`resNFe`), "
            "que não traz itens, tributos nem transporte.")

    inf = raiz.find(f".//{NS}infNFe")
    if inf is None:
        raise DanfeIndisponivel(
            "DANFE indisponível — XML completo necessário: o arquivo não tem "
            "o bloco `infNFe`.")

    ide = inf.find(NS + "ide")
    emit = inf.find(NS + "emit")
    dest = inf.find(NS + "dest")
    transp = inf.find(NS + "transp")
    transporta = transp.find(NS + "transporta") if transp is not None else None
    vol = transp.find(NS + "vol") if transp is not None else None
    tot = inf.find(f"{NS}total/{NS}ICMSTot")
    prot = raiz.find(f".//{NS}infProt")
    adic = inf.find(NS + "infAdic")

    chave = (inf.get("Id") or "").replace("NFe", "")
    if not chave:
        chave = _txt(prot, "chNFe")

    modelo = _txt(ide, "mod") or (chave[20:22] if len(chave) >= 22 else "55")
    if modelo and modelo != "55":
        raise DanfeIndisponivel(
            f"este gerador é do DANFE da NF-e modelo 55; o documento é modelo "
            f"{modelo}. Modelo 65 (NFC-e) usa o DANFE-NFC-e, que tem outro "
            f"layout e não foi implementado.")

    itens = []
    for det in inf.findall(NS + "det"):
        prod = det.find(NS + "prod")
        imp = det.find(NS + "imposto")
        icms = _grupo(imp, "ICMS")
        ipi = _grupo(imp, "IPI")
        itens.append({
            "numero": det.get("nItem") or "",
            "codigo": _txt(prod, "cProd"), "descricao": _txt(prod, "xProd"),
            "ncm": _txt(prod, "NCM"), "cest": _txt(prod, "CEST"),
            "cfop": _txt(prod, "CFOP"), "unidade": _txt(prod, "uCom"),
            "quantidade": _txt(prod, "qCom"),
            "valor_unitario": _txt(prod, "vUnCom"),
            "valor": _txt(prod, "vProd"),
            "desconto": _txt(prod, "vDesc"),
            # CST e CSOSN em campos separados; o DANFE imprime na mesma coluna
            # precedido da origem, e é o próprio documento que diz qual dos
            # dois preencheu.
            "origem": _txt(icms, "orig"),
            "cst": _txt(icms, "CST") or _txt(icms, "CSOSN"),
            "icms_base": _txt(icms, "vBC"), "icms_valor": _txt(icms, "vICMS"),
            "icms_aliquota": _txt(icms, "pICMS"),
            "ipi_valor": _txt(ipi, "vIPI"), "ipi_aliquota": _txt(ipi, "pIPI"),
            "reforma": _reforma(imp),
        })

    return {
        "chave": chave,
        "modelo": modelo,
        "numero": _txt(ide, "nNF"), "serie": _txt(ide, "serie"),
        "natureza": _txt(ide, "natOp"),
        "tipo": _txt(ide, "tpNF"),          # 0 entrada · 1 saída
        "emissao": _txt(ide, "dhEmi") or _txt(ide, "dEmi"),
        "saida_entrada": _txt(ide, "dhSaiEnt") or _txt(ide, "dSaiEnt"),
        "emitente": _parte(emit),
        "destinatario": _parte(dest),
        "totais": {c: _txt(tot, c) for c in (
            "vBC", "vICMS", "vBCST", "vST", "vProd", "vFrete", "vSeg",
            "vDesc", "vII", "vIPI", "vPIS", "vCOFINS", "vOutro", "vNF",
            "vFCP", "vTotTrib")},
        "transporte": {
            "modalidade": _txt(transp, "modFrete"),
            "nome": _txt(transporta, "xNome"),
            "documento": _txt(transporta, "CNPJ") or _txt(transporta, "CPF"),
            "ie": _txt(transporta, "IE"),
            "endereco": _txt(transporta, "xEnder"),
            "municipio": _txt(transporta, "xMun"),
            "uf": _txt(transporta, "UF"),
            "quantidade": _txt(vol, "qVol"), "especie": _txt(vol, "esp"),
            "marca": _txt(vol, "marca"),
            "peso_liquido": _txt(vol, "pesoL"), "peso_bruto": _txt(vol, "pesoB"),
        },
        "protocolo": {
            "numero": _txt(prot, "nProt"),
            "recebimento": _txt(prot, "dhRecbto"),
            "status": _txt(prot, "cStat"), "motivo": _txt(prot, "xMotivo"),
        },
        "adicionais": {"fisco": _txt(adic, "infAdFisco"),
                       "contribuinte": _txt(adic, "infCpl")},
        "itens": itens,
    }


def _grupo(imposto, nome: str):
    """O ICMS vem dentro de `ICMS00`, `ICMS40`, `ICMSSN102`… — o filho é que
    diz a tributação. Pega-se o primeiro filho do grupo, seja qual for."""
    if imposto is None:
        return None
    bloco = imposto.find(NS + nome)
    if bloco is None:
        return None
    for filho in bloco:
        if filho.tag.split("}")[-1].startswith(nome):
            return filho
    return bloco


def _reforma(imposto) -> dict:
    """IBS/CBS/IS, se o documento os trouxer. Lidos, nunca calculados."""
    if imposto is None:
        return {}
    bloco = imposto.find(NS + "IBSCBS")
    if bloco is None:
        return {}
    uf = bloco.find(f"{NS}gIBSCBS/{NS}gIBSUF")
    mun = bloco.find(f"{NS}gIBSCBS/{NS}gIBSMun")
    cbs = bloco.find(f"{NS}gIBSCBS/{NS}gCBS")
    g = bloco.find(NS + "gIBSCBS")
    return {k: v for k, v in {
        "cst": _txt(bloco, "CST"),
        "classificacao": _txt(bloco, "cClassTrib"),
        "base": _txt(g, "vBC"),
        "ibs_uf_aliquota": _txt(uf, "pIBSUF"), "ibs_uf_valor": _txt(uf, "vIBSUF"),
        "ibs_mun_aliquota": _txt(mun, "pIBSMun"),
        "ibs_mun_valor": _txt(mun, "vIBSMun"),
        "cbs_aliquota": _txt(cbs, "pCBS"), "cbs_valor": _txt(cbs, "vCBS"),
    }.items() if v}


def _parte(el) -> dict:
    if el is None:
        return {}
    ender = el.find(NS + "enderEmit")
    if ender is None:
        ender = el.find(NS + "enderDest")
    return {
        "nome": _txt(el, "xNome"), "fantasia": _txt(el, "xFant"),
        "documento": _txt(el, "CNPJ") or _txt(el, "CPF"),
        "ie": _txt(el, "IE"), "iest": _txt(el, "IEST"), "im": _txt(el, "IM"),
        "endereco": _endereco(ender), "bairro": _txt(ender, "xBairro"),
        "cep": _txt(ender, "CEP"), "municipio": _txt(ender, "xMun"),
        "uf": _txt(ender, "UF"), "fone": _txt(ender, "fone"),
    }


# ════════════════════════════════════════════════════════════════════════════
#  Desenho
# ════════════════════════════════════════════════════════════════════════════
_TIPO = {"0": "0 - ENTRADA", "1": "1 - SAIDA"}
_FRETE = {"0": "0-Emitente", "1": "1-Dest/Rem", "2": "2-Terceiros",
          "3": "3-Prop/Rem", "4": "4-Prop/Dest", "9": "9-Sem frete"}

RODAPE = ("Representacao auxiliar gerada pelo FISCALE a partir do XML "
          "autorizado, que e o documento fiscal e permanece integro no acervo. "
          "Confira a validade em www.nfe.fazenda.gov.br pela chave de acesso.")


class _Pagina:
    """Fininha de proposito: só o que o desenho precisa da folha."""

    def __init__(self, pdf, margem=6.0):
        self.pdf = pdf
        self.m = margem
        self.largura = pdf.w - 2 * margem

    def caixa(self, x, y, w, h, rotulo="", valor="", *, tamanho=7,
              alinhar="L", negrito=False):
        p = self.pdf
        p.rect(x, y, w, h)
        if rotulo:
            p.set_font("Helvetica", "", 5)
            p.set_xy(x + 1, y + 0.6)
            p.cell(w - 2, 2.4, _l1(rotulo))
        p.set_font("Helvetica", "B" if negrito else "", tamanho)
        p.set_xy(x + 1, y + (3.2 if rotulo else 1))
        p.cell(w - 2, h - (3.8 if rotulo else 2), _l1(valor), align=alinhar)

    def titulo(self, x, y, w, texto, *, tamanho=6):
        p = self.pdf
        p.set_font("Helvetica", "B", tamanho)
        p.set_xy(x, y)
        p.cell(w, 3, _l1(texto))


def _barras(pdf, x, y, largura, altura, digitos):
    modulos = code128c(digitos)
    total = sum(w for w, _ in modulos)
    unidade = largura / total
    pdf.set_fill_color(0, 0, 0)
    pos = x
    for w, barra in modulos:
        if barra:
            pdf.rect(pos, y, w * unidade, altura, style="F")
        pos += w * unidade


def gerar(xml: bytes | str) -> bytes:
    """XML autorizado → PDF do DANFE. Levanta `DanfeIndisponivel` se não der.

    O PDF NÃO é documento fiscal e o rodapé diz isso em toda página.
    """
    d = ler(xml)
    from fpdf import FPDF

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(False)
    pdf.set_margins(6, 6, 6)
    pdf.set_line_width(0.15)
    pdf.add_page()
    pg = _Pagina(pdf)
    L, x0 = pg.largura, pg.m
    y = pg.m

    # ── canhoto ─────────────────────────────────────────────────────────────
    recibo = L * 0.78
    pg.caixa(x0, y, recibo, 8,
             "RECEBEMOS DE " + (d["emitente"].get("nome") or "") +
             " OS PRODUTOS CONSTANTES DA NOTA FISCAL INDICADA AO LADO", "",
             tamanho=5)
    pg.caixa(x0, y + 8, recibo * 0.32, 8, "DATA DE RECEBIMENTO", "")
    pg.caixa(x0 + recibo * 0.32, y + 8, recibo * 0.68, 8,
             "IDENTIFICACAO E ASSINATURA DO RECEBEDOR", "")
    pg.caixa(x0 + recibo, y, L - recibo, 16, "", "")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(x0 + recibo, y + 3)
    pdf.cell(L - recibo, 4, "NF-e", align="C")
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(x0 + recibo, y + 8)
    pdf.cell(L - recibo, 4, _l1(f"No {d['numero']}  SERIE {d['serie']}"),
             align="C")
    y += 18

    # ── cabeçalho ───────────────────────────────────────────────────────────
    cab = 26
    emitente_w, danfe_w = L * 0.40, L * 0.22
    chave_w = L - emitente_w - danfe_w

    e = d["emitente"]
    pdf.rect(x0, y, emitente_w, cab)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(x0 + 2, y + 2)
    pdf.multi_cell(emitente_w - 4, 3.6, _l1(e.get("nome") or ""))
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_xy(x0 + 2, pdf.get_y() + 0.5)
    endereco = ", ".join(p for p in (
        e.get("endereco"), e.get("bairro"),
        f"{e.get('municipio','')} - {e.get('uf','')}".strip(" -"),
        f"CEP {e.get('cep','')}" if e.get("cep") else "",
        f"Fone {e.get('fone','')}" if e.get("fone") else "") if p)
    pdf.multi_cell(emitente_w - 4, 3, _l1(endereco))

    pdf.rect(x0 + emitente_w, y, danfe_w, cab)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_xy(x0 + emitente_w, y + 1.5)
    pdf.cell(danfe_w, 5, "DANFE", align="C")
    pdf.set_font("Helvetica", "", 5.2)
    pdf.set_xy(x0 + emitente_w + 1, y + 6.5)
    pdf.multi_cell(danfe_w - 2, 2.4,
                   _l1("Documento Auxiliar da Nota Fiscal Eletronica"),
                   align="C")
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(x0 + emitente_w, y + 12)
    pdf.cell(danfe_w, 3.5, _l1(_TIPO.get(d["tipo"], d["tipo"] or "")),
             align="C")
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_xy(x0 + emitente_w, y + 16)
    pdf.cell(danfe_w, 4, _l1(f"No {d['numero']}"), align="C")
    pdf.set_xy(x0 + emitente_w, y + 20)
    pdf.cell(danfe_w, 4, _l1(f"SERIE {d['serie']}   FOLHA 1/1"), align="C")

    xc = x0 + emitente_w + danfe_w
    pdf.rect(xc, y, chave_w, cab)
    if d["chave"]:
        _barras(pdf, xc + 3, y + 2, chave_w - 6, 11, d["chave"])
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(xc + 1, y + 14)
    pdf.cell(chave_w - 2, 2.4, "CHAVE DE ACESSO")
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(xc + 1, y + 16.5)
    pdf.cell(chave_w - 2, 3.5, _l1(_chave_formatada(d["chave"])), align="C")
    pdf.set_font("Helvetica", "", 5)
    pdf.set_xy(xc + 1, y + 20.5)
    pdf.multi_cell(chave_w - 2, 2.4, _l1(
        "Consulta de autenticidade no portal nacional da NF-e "
        "www.nfe.fazenda.gov.br/portal ou no site da Sefaz autorizadora"),
        align="C")
    y += cab

    pg.caixa(x0, y, L * 0.55, 8, "NATUREZA DA OPERACAO", d["natureza"])
    pr = d["protocolo"]
    protocolo = " - ".join(p for p in (pr["numero"], _data(pr["recebimento"]))
                           if p)
    pg.caixa(x0 + L * 0.55, y, L * 0.45, 8,
             "PROTOCOLO DE AUTORIZACAO DE USO", protocolo)
    y += 8

    pg.caixa(x0, y, L * 0.34, 8, "INSCRICAO ESTADUAL", e.get("ie", ""))
    pg.caixa(x0 + L * 0.34, y, L * 0.33, 8,
             "INSCR. ESTADUAL DO SUBST. TRIBUTARIO", e.get("iest", ""))
    pg.caixa(x0 + L * 0.67, y, L * 0.33, 8, "CNPJ / CPF",
             _doc(e.get("documento", "")))
    y += 10

    # ── destinatário ────────────────────────────────────────────────────────
    dd = d["destinatario"]
    pg.titulo(x0, y, L, "DESTINATARIO / REMETENTE")
    y += 3
    pg.caixa(x0, y, L * 0.58, 7, "NOME / RAZAO SOCIAL", dd.get("nome", ""))
    pg.caixa(x0 + L * 0.58, y, L * 0.24, 7, "CNPJ / CPF",
             _doc(dd.get("documento", "")))
    pg.caixa(x0 + L * 0.82, y, L * 0.18, 7, "DATA DA EMISSAO",
             _data(d["emissao"]))
    y += 7
    pg.caixa(x0, y, L * 0.44, 7, "ENDERECO", dd.get("endereco", ""))
    pg.caixa(x0 + L * 0.44, y, L * 0.22, 7, "BAIRRO", dd.get("bairro", ""))
    pg.caixa(x0 + L * 0.66, y, L * 0.16, 7, "CEP", dd.get("cep", ""))
    # `dhSaiEnt` é campo OPCIONAL: em branco quer dizer que o documento não o
    # informou. Repetir a emissão aqui seria afirmar uma data que ninguem deu.
    pg.caixa(x0 + L * 0.82, y, L * 0.18, 7, "DATA DA SAIDA / ENTRADA",
             _data(d["saida_entrada"]))
    y += 7
    pg.caixa(x0, y, L * 0.44, 7, "MUNICIPIO", dd.get("municipio", ""))
    pg.caixa(x0 + L * 0.44, y, L * 0.10, 7, "UF", dd.get("uf", ""))
    pg.caixa(x0 + L * 0.54, y, L * 0.28, 7, "INSCRICAO ESTADUAL",
             dd.get("ie", ""))
    pg.caixa(x0 + L * 0.82, y, L * 0.18, 7, "HORA DA SAIDA",
             (d["saida_entrada"][11:16] if len(d["saida_entrada"]) >= 16
              else ""))
    y += 9

    # ── cálculo do imposto ──────────────────────────────────────────────────
    t = d["totais"]
    pg.titulo(x0, y, L, "CALCULO DO IMPOSTO")
    y += 3
    linha1 = (("BASE DE CALCULO DO ICMS", t["vBC"]),
              ("VALOR DO ICMS", t["vICMS"]),
              ("BASE DE CALCULO DO ICMS ST", t["vBCST"]),
              ("VALOR DO ICMS ST", t["vST"]),
              ("VALOR TOTAL DOS PRODUTOS", t["vProd"]))
    linha2 = (("VALOR DO FRETE", t["vFrete"]), ("VALOR DO SEGURO", t["vSeg"]),
              ("DESCONTO", t["vDesc"]), ("OUTRAS DESPESAS", t["vOutro"]),
              ("VALOR DO IPI", t["vIPI"]), ("VALOR TOTAL DA NOTA", t["vNF"]))
    for linha in (linha1, linha2):
        w = L / len(linha)
        for i, (rot, val) in enumerate(linha):
            pg.caixa(x0 + i * w, y, w, 8, rot, _n(val), alinhar="R",
                     negrito=(rot == "VALOR TOTAL DA NOTA"))
        y += 8
    y += 2

    # ── transportador ───────────────────────────────────────────────────────
    tr = d["transporte"]
    pg.titulo(x0, y, L, "TRANSPORTADOR / VOLUMES TRANSPORTADOS")
    y += 3
    pg.caixa(x0, y, L * 0.42, 7, "NOME / RAZAO SOCIAL", tr["nome"])
    pg.caixa(x0 + L * 0.42, y, L * 0.16, 7, "FRETE POR CONTA",
             _FRETE.get(tr["modalidade"], tr["modalidade"]), tamanho=6)
    pg.caixa(x0 + L * 0.58, y, L * 0.20, 7, "INSCRICAO ESTADUAL", tr["ie"])
    pg.caixa(x0 + L * 0.78, y, L * 0.22, 7, "CNPJ / CPF", _doc(tr["documento"]))
    y += 7
    pg.caixa(x0, y, L * 0.16, 7, "QUANTIDADE", tr["quantidade"])
    pg.caixa(x0 + L * 0.16, y, L * 0.20, 7, "ESPECIE", tr["especie"])
    pg.caixa(x0 + L * 0.36, y, L * 0.20, 7, "MARCA", tr["marca"])
    pg.caixa(x0 + L * 0.56, y, L * 0.22, 7, "PESO BRUTO", tr["peso_bruto"])
    pg.caixa(x0 + L * 0.78, y, L * 0.22, 7, "PESO LIQUIDO", tr["peso_liquido"])
    y += 9

    # ── produtos ────────────────────────────────────────────────────────────
    colunas = (("COD", 0.07, "L"), ("DESCRICAO", 0.28, "L"),
               ("NCM", 0.07, "C"), ("CST", 0.05, "C"), ("CFOP", 0.05, "C"),
               ("UN", 0.04, "C"), ("QTDE", 0.08, "R"),
               ("V.UNIT", 0.09, "R"), ("V.TOTAL", 0.09, "R"),
               ("BC ICMS", 0.07, "R"), ("V.ICMS", 0.06, "R"),
               ("ALIQ", 0.05, "R"))
    pg.titulo(x0, y, L, "DADOS DOS PRODUTOS / SERVICOS")
    y += 3
    y = _cabecalho_itens(pdf, x0, y, L, colunas)

    limite = pdf.h - 46
    for it in d["itens"]:
        if y > limite:
            _rodape(pdf, d)
            pdf.add_page()
            y = pg.m
            y = _cabecalho_itens(pdf, x0, y, L, colunas)
            limite = pdf.h - 20
        y = _linha_item(pdf, x0, y, L, colunas, it)

    # ── Reforma Tributária ──────────────────────────────────────────────────
    # POR QUE UM BLOCO SEPARADO, E NAO COLUNAS NA GRADE ACIMA
    #     O layout oficial do DANFE com IBS/CBS/IS depende da Nota Tecnica
    #     vigente, e nao ha aqui como conferi-la (o FISCALE trabalha offline).
    #     Inventar posicao dentro da grade oficial seria produzir um documento
    #     que PARECE o padrao novo e nao e. Fora da grade, rotulado, o dado
    #     aparece sem se disfarcar de norma.
    com_reforma = [it for it in d["itens"] if it["reforma"]]
    if com_reforma:
        if y > pdf.h - 60:
            _rodape(pdf, d)
            pdf.add_page()
            y = pg.m
        y += 2
        pg.titulo(x0, y, L, "TRIBUTOS DA REFORMA (IBS / CBS / IS) - "
                            "INFORMADOS NO XML")
        y += 3
        pdf.rect(x0, y, L, 6)
        pdf.set_font("Helvetica", "", 5.4)
        pdf.set_xy(x0 + 1, y + 1)
        pdf.multi_cell(L - 2, 2.4, _l1(
            "Valores informados pelo proprio documento. O FISCALE nao calcula "
            "IBS, CBS nem Imposto Seletivo. Bloco fora da grade oficial do "
            "DANFE porque o posicionamento visual desses campos depende da "
            "Nota Tecnica vigente, nao conferida nesta versao."))
        y += 6
        cols_r = (("ITEM", 0.06, "C"), ("CST", 0.07, "C"),
                  ("cClassTrib", 0.11, "C"), ("BASE", 0.13, "R"),
                  ("IBS UF", 0.13, "R"), ("IBS MUN", 0.13, "R"),
                  ("CBS", 0.13, "R"), ("TOTAL IBS+CBS", 0.24, "R"))
        y = _cabecalho_itens(pdf, x0, y, L, cols_r)
        for it in com_reforma:
            r = it["reforma"]
            soma = sum(x for x in (_dec(r.get("ibs_uf_valor")),
                                   _dec(r.get("ibs_mun_valor")),
                                   _dec(r.get("cbs_valor"))) if x is not None)
            y = _linha_generica(pdf, x0, y, L, cols_r, [
                it["numero"], r.get("cst", ""), r.get("classificacao", ""),
                _n(r.get("base")), _n(r.get("ibs_uf_valor")),
                _n(r.get("ibs_mun_valor")), _n(r.get("cbs_valor")),
                _n(soma) if soma else ""])

    # ── dados adicionais ────────────────────────────────────────────────────
    ad = d["adicionais"]
    texto = "  ".join(p for p in (ad["contribuinte"], ad["fisco"]) if p)
    if y > pdf.h - 40:
        _rodape(pdf, d)
        pdf.add_page()
        y = pg.m
    y += 2
    pg.titulo(x0, y, L, "DADOS ADICIONAIS")
    y += 3
    altura = 14
    pdf.rect(x0, y, L, altura)
    pdf.set_font("Helvetica", "", 5.6)
    pdf.set_xy(x0 + 1, y + 1)
    pdf.multi_cell(L - 2, 2.5, _l1(texto[:1400]), max_line_height=2.5)

    _rodape(pdf, d)
    saida = pdf.output()
    return bytes(saida)


def _cabecalho_itens(pdf, x0, y, L, colunas) -> float:
    pdf.set_fill_color(232, 236, 239)
    pdf.rect(x0, y, L, 4.5, style="FD")
    pdf.set_font("Helvetica", "B", 5.2)
    x = x0
    for rot, frac, _al in colunas:
        w = L * frac
        pdf.set_xy(x, y + 0.6)
        pdf.cell(w, 3.2, _l1(rot), align="C")
        x += w
    return y + 4.5


def _linha_item(pdf, x0, y, L, colunas, it) -> float:
    valores = [
        it["codigo"], it["descricao"], it["ncm"],
        (it["origem"] + it["cst"]) if it["cst"] else it["origem"],
        it["cfop"], it["unidade"], _n(it["quantidade"], 4),
        _n(it["valor_unitario"], 4), _n(it["valor"]),
        _n(it["icms_base"]), _n(it["icms_valor"]), _n(it["icms_aliquota"]),
    ]
    return _linha_generica(pdf, x0, y, L, colunas, valores)


def _linha_generica(pdf, x0, y, L, colunas, valores) -> float:
    altura = 3.6
    pdf.set_font("Helvetica", "", 5.4)
    x = x0
    for (rot, frac, al), valor in zip(colunas, valores):
        w = L * frac
        pdf.rect(x, y, w, altura)
        pdf.set_xy(x + 0.6, y + 0.3)
        texto = _l1(str(valor or ""))
        # Corta pelo que cabe na coluna em vez de deixar o texto invadir a
        # vizinha — descricao longa e regra, nao excecao.
        while texto and pdf.get_string_width(texto) > w - 1.2:
            texto = texto[:-1]
        pdf.cell(w - 1.2, altura - 0.6, texto, align=al)
        x += w
    return y + altura


def _rodape(pdf, d) -> None:
    pdf.set_font("Helvetica", "I", 5)
    pdf.set_xy(6, pdf.h - 9)
    pdf.multi_cell(pdf.w - 12, 2.4, _l1(RODAPE), align="C")
