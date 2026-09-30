# -*- coding: utf-8 -*-
"""relatorio.py — compila os documentos guardados num dossiê. Não os redesenha.

A DIFERENÇA ENTRE COMPILAR E REDESENHAR, QUE É A RAZÃO DESTE MÓDULO EXISTIR
    **Anexar** o PDF oficial dentro de uma compilação é juntada de documento —
    o que qualquer processo faz. **Redesenhar** o PDF oficial a partir dos
    dados produziria um documento com aparência de certidão da Receita que foi
    desenhado por nós: uma falsificação, mesmo feita de boa-fé, mesmo para uso
    interno. Basta um deles chegar a um banco ou a uma licitação.

    No caso federal seria ainda desnecessário: o SITFIS entrega o PDF
    verdadeiro da RFB, e o envelope o guarda byte a byte.

    (O `pdflocal.py` reproduz o DANFSe e isso é outro caso: ali o documento
    legal é o XML, e o DANFSe é representação auxiliar de algo que possuímos.
    Certidão não tem representação — tem original, ou não tem nada.)

SEM MARCA DO SISTEMA, EM LUGAR NENHUM
    Regra de negócio, decidida em 11/09/2026: o nome do sistema não aparece no
    PDF gerado. Não na capa, não no rodapé, não no título — e **não nos
    metadados**, que é por onde ele escaparia sem ninguém ver.

    Medido: o `pypdf` grava `/Producer: pypdf` por conta própria ao escrever.
    Por isso os metadados do documento final são zerados explicitamente, e há
    teste varrendo os BYTES INTEIROS do PDF atrás de qualquer marca. Regra que
    não é testada é intenção, não regra.

O QUE A CAPA MANTÉM, E POR QUÊ
    Cada documento anexado leva procedência: origem, quando foi capturado e o
    SHA-256. Isso não é assinatura nossa — é dado sobre o documento do cliente,
    e é o que dá cadeia de custódia à compilação. Sem ele, o dossiê é um
    amontoado de páginas sem resposta para "de onde veio este PDF?".

DUAS RECUSAS QUE ELE PRATICA
    Documento cujo hash não confere **não é anexado**: o envelope promete que o
    original não muda, e anexar um arquivo divergente quebraria essa promessa
    no lugar mais visível. Ele é omitido, e a capa diz que foi.

    PDF que o `pypdf` não consegue abrir não derruba o dossiê inteiro. Ele é
    registrado como não anexável e o resto segue — relatório que falha inteiro
    por causa de um arquivo ruim é relatório que ninguém emite no dia em que
    mais precisa.
"""
from __future__ import annotations

import datetime as _dt
import io
from pathlib import Path

import relatorio_base as _base

from . import armazenamento, modelo

TITULO = "Relatório de Situação Fiscal"

# A regra white-label mora em `relatorio_base` — UMA implementação, usada
# também pelo relatório de Optantes (que não é documento de órgão e não entra
# no envelope, mas obedece à mesma regra). Os nomes locais seguem existindo
# porque este módulo e a suíte dele os usam em dezenas de lugares.
METADADOS = _base.metadados(TITULO)
TRACO = _base.TRACO
_seguro = _base.seguro
_br = _base.br
_doc = _base.doc


def nome_de_arquivo(base: str, hoje=None) -> str:
    """Nome neutro do dossiê. Ver `relatorio_base.nome_de_arquivo`."""
    return _base.nome_de_arquivo(base, hoje, "situacao-fiscal")

ESFERA_ROTULO = {
    modelo.FEDERAL: "Federal",
    modelo.ESTADUAL: "Estadual",
    modelo.MUNICIPAL: "Municipal",
}
TIPO_ROTULO = {modelo.CERTIDAO: "Certidão", modelo.EXTRATO: "Extrato"}
ESTADO_ROTULO = {
    modelo.VALIDA: "Válida",
    modelo.VENCE_EM_BREVE: "A vencer",
    modelo.VENCIDA: "Vencida",
    modelo.APURADO: "Em dia",
    modelo.DESATUALIZADO: "Desatualizado",
    modelo.SEM_VALIDADE: "Sem data lida",
    modelo.SEM_DOCUMENTO: "Sem documento",
}
NATUREZA_ROTULO = {
    modelo.NEGATIVA: "Negativa",
    modelo.CPD_EN: "Positiva com efeitos de negativa",
    modelo.POSITIVA: "Positiva",
    modelo.NAO_LIDA: "Não lida",
}

# Motivos pelos quais um documento existente NÃO foi anexado.
OMITIDO_HASH = "o arquivo no disco não confere com o hash registrado"
OMITIDO_ILEGIVEL = "o PDF não pôde ser aberto"
OMITIDO_SUMIU = "o arquivo não está mais no disco"


# ── o que dá para anexar ────────────────────────────────────────────────
def levantar(raiz, indice, identidade: str, hoje=None) -> list:
    """Uma linha por esfera×tipo, já dizendo se o documento é anexável.

    A CONFERÊNCIA DE INTEGRIDADE ACONTECE AQUI, e não na hora de anexar: quem
    monta a capa precisa saber o que vai entrar para poder dizer o que ficou
    de fora. Capa que promete um anexo que não veio é pior que capa sem ele.
    """
    armazem = armazenamento.abrir(raiz, identidade)
    linhas = []
    for esfera in modelo.ESFERAS:
        for tipo in modelo.TIPOS:
            st = indice.estado(identidade, esfera, tipo, hoje)
            doc = st.get("documento") or {}
            linha = {"esfera": esfera, "tipo": tipo, "estado": st["estado"],
                     "natureza": st.get("natureza") or "",
                     "documento": doc, "anexar": None, "omitido": ""}
            if doc.get("id"):
                caminho = Path(doc.get("caminho") or "")
                if not caminho.is_file():
                    linha["omitido"] = OMITIDO_SUMIU
                elif not armazem.conferir_integridade(esfera, tipo, doc["id"]):
                    linha["omitido"] = OMITIDO_HASH
                else:
                    linha["anexar"] = caminho
            linhas.append(linha)
    return linhas


# ── a capa ──────────────────────────────────────────────────────────────
class _Capa(_base.Capa):
    """A capa do dossiê: a base white-label mais os blocos desta seara.

    A tipografia, a moldura e os metadados neutros vêm de `relatorio_base`;
    o que sobra aqui é o que só o dossiê sabe desenhar — esfera, tipo,
    procedência e o índice dos anexos.
    """

    def __init__(self):
        super().__init__(TITULO)

    # -- blocos ----------------------------------------------------------
    def cabecalho(self, nome: str, documento: str, hoje: _dt.date):
        self.titulo(TITULO)
        self.pdf.set_font("Helvetica", "", 11)
        self.pdf.set_text_color(60, 72, 80)
        self.pdf.cell(0, 6, _seguro(nome or _doc(documento)),
                      new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_font("Helvetica", "", 9)
        self.pdf.set_text_color(110, 120, 128)
        self.pdf.cell(0, 5, _seguro("CNPJ/CPF %s" % _doc(documento)),
                      new_x="LMARGIN", new_y="NEXT")
        self.pdf.cell(0, 5, _seguro("Emitido em %s" % hoje.strftime("%d/%m/%Y")),
                      new_x="LMARGIN", new_y="NEXT")
        self.risco()

    def quadro(self, linhas: list):
        self.rotulo("Situação por esfera")
        self.pdf.ln(1)
        larguras = (30, 26, 34, 52, 32)
        cabec = ("Esfera", "Tipo", "Situação", "Natureza", "Validade")
        self.pdf.set_font("Helvetica", "B", 8)
        self.pdf.set_text_color(110, 120, 128)
        for L, c in zip(larguras, cabec):
            self.pdf.cell(L, 6, _seguro(c))
        self.pdf.ln(6)
        self.pdf.set_draw_color(225, 231, 235)

        for l in linhas:
            doc = l["documento"] or {}
            data = (doc.get("validade") if l["tipo"] == modelo.CERTIDAO
                    else doc.get("apurado_em"))
            valores = (
                ESFERA_ROTULO.get(l["esfera"], l["esfera"]),
                TIPO_ROTULO.get(l["tipo"], l["tipo"]),
                ESTADO_ROTULO.get(l["estado"], l["estado"]),
                NATUREZA_ROTULO.get(l["natureza"], TRACO) if l["natureza"] else TRACO,
                _br(data),
            )
            vazio = l["estado"] == modelo.SEM_DOCUMENTO
            self.pdf.set_font("Helvetica", "", 9)
            self.pdf.set_text_color(*((150, 158, 164) if vazio else (30, 40, 50)))
            y = self.pdf.get_y()
            for L, v in zip(larguras, valores):
                self.pdf.cell(L, 6, _seguro(v))
            self.pdf.ln(6)
            self.pdf.line(self.MARGEM, self.pdf.get_y() - 1,
                          210 - self.MARGEM, self.pdf.get_y() - 1)
            if l["omitido"]:
                self.pdf.set_font("Helvetica", "I", 8)
                self.pdf.set_text_color(150, 70, 60)
                self.pdf.cell(0, 5, _seguro("      documento não anexado: %s"
                                       % l["omitido"]), new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(2)

    def indice(self, anexos: list):
        """Os documentos que vêm nas páginas seguintes, com procedência."""
        if not anexos:
            self.risco()
            self.texto("Nenhum documento anexado.", 10,
                       cor=(150, 158, 164))
            return
        self.risco()
        self.rotulo("Documentos anexados")
        self.pdf.ln(1)
        for i, a in enumerate(anexos, 1):
            self.pdf.set_font("Helvetica", "B", 9)
            self.pdf.set_text_color(30, 40, 50)
            self.pdf.cell(0, 5, _seguro("%d. %s - %s   (%d pág.)"
                          % (i, ESFERA_ROTULO.get(a["esfera"], a["esfera"]),
                             TIPO_ROTULO.get(a["tipo"], a["tipo"]),
                             a["paginas"])),
                          new_x="LMARGIN", new_y="NEXT")
            # PROCEDÊNCIA: dado sobre o documento do cliente, não assinatura
            # nossa. É o que responde "de onde veio este PDF?".
            doc = a["documento"]
            self.pdf.set_font("Helvetica", "", 7.5)
            self.pdf.set_text_color(120, 130, 138)
            self.pdf.cell(0, 4, _seguro("     obtido em %s por %s"
                          % (_br(doc.get("capturado_utc")),
                             (doc.get("origem") or TRACO).lower())),
                          new_x="LMARGIN", new_y="NEXT")
            self.pdf.cell(0, 4, _seguro("     SHA-256 %s" % (doc.get("sha256") or TRACO)),
                          new_x="LMARGIN", new_y="NEXT")
            self.pdf.ln(1)


# ── a compilação ────────────────────────────────────────────────────────
def _paginas(caminho: Path) -> int:
    """Quantas páginas o PDF tem, ou 0 se não abrir. Nunca levanta."""
    try:
        import logging
        import pypdf
        ruidoso = logging.getLogger("pypdf")
        antes = ruidoso.level
        ruidoso.setLevel(logging.CRITICAL)
        try:
            return len(pypdf.PdfReader(str(caminho)).pages)
        finally:
            ruidoso.setLevel(antes)
    except Exception:
        return 0


def _juntar(capa: bytes, anexos: list) -> bytes:
    """Capa + anexos num PDF só, com metadados LIMPOS.

    Os metadados são zerados aqui e não antes: o `pypdf` grava
    `/Producer: pypdf` por conta própria ao escrever, e sobrescrever depois é
    a única forma de garantir que ele não fique.
    """
    import pypdf
    escritor = pypdf.PdfWriter()
    escritor.append(io.BytesIO(capa))
    for a in anexos:
        try:
            escritor.append(str(a["caminho"]))
        except Exception:
            continue            # já foi contado como não anexável
    escritor.add_metadata(METADADOS)
    saida = io.BytesIO()
    escritor.write(saida)
    escritor.close()
    return saida.getvalue()


def dossie(raiz, indice, identidade: str, nome: str = "", hoje=None) -> dict:
    """O dossiê de UMA empresa. Devolve `{pdf, anexados, omitidos, linhas}`."""
    hoje = hoje or _dt.date.today()
    linhas = levantar(raiz, indice, identidade, hoje)

    anexos = []
    for l in linhas:
        if l["anexar"] is None:
            continue
        n = _paginas(l["anexar"])
        if n == 0:
            l["omitido"] = OMITIDO_ILEGIVEL
            l["anexar"] = None
            continue
        anexos.append({"esfera": l["esfera"], "tipo": l["tipo"],
                       "caminho": l["anexar"], "paginas": n,
                       "documento": l["documento"]})

    capa = _Capa()
    capa.pagina()
    capa.cabecalho(nome, identidade, hoje)
    capa.quadro(linhas)
    capa.indice(anexos)

    return {"pdf": _juntar(capa.bytes(), anexos),
            "anexados": len(anexos),
            "omitidos": [l["omitido"] for l in linhas if l["omitido"]],
            "linhas": linhas}


def lote(raiz, indice, empresas, hoje=None) -> dict:
    """Um PDF para a carteira inteira: quadro consolidado + cada empresa.

    UMA EMPRESA COM PROBLEMA NÃO DERRUBA O LOTE. Sem isso, a primeira pasta
    com um PDF ruim deixaria as dezoito seguintes de fora, e o relatório do
    dia diria "falhou" em vez de "faltou uma".
    """
    hoje = hoje or _dt.date.today()
    capa = _Capa()
    capa.pagina()
    capa.titulo(TITULO)
    capa.pdf.set_font("Helvetica", "", 9)
    capa.pdf.set_text_color(110, 120, 128)
    capa.pdf.cell(0, 5, _seguro("%d empresas · emitido em %s"
                  % (len(empresas), hoje.strftime("%d/%m/%Y"))),
                  new_x="LMARGIN", new_y="NEXT")
    capa.risco()

    anexos, resumo = [], []
    capa.rotulo("Quadro consolidado")
    capa.pdf.ln(1)
    for emp in empresas:
        ident = emp.get("identidade") or ""
        nome = emp.get("nome") or _doc(ident)
        try:
            linhas = levantar(raiz, indice, ident, hoje)
        except Exception as e:
            capa.pdf.set_font("Helvetica", "I", 8)
            capa.pdf.set_text_color(150, 70, 60)
            capa.pdf.cell(0, 5, _seguro("%s - não foi possível ler (%s)"
                          % (nome[:48], type(e).__name__)),
                          new_x="LMARGIN", new_y="NEXT")
            resumo.append({"identidade": ident, "erro": type(e).__name__})
            continue

        contagem = {}
        for l in linhas:
            contagem[l["estado"]] = contagem.get(l["estado"], 0) + 1
            if l["anexar"] is not None:
                n = _paginas(l["anexar"])
                if n:
                    anexos.append({"esfera": l["esfera"], "tipo": l["tipo"],
                                   "caminho": l["anexar"], "paginas": n,
                                   "documento": l["documento"],
                                   "empresa": nome})
        capa.pdf.set_font("Helvetica", "B", 9)
        capa.pdf.set_text_color(30, 40, 50)
        capa.pdf.cell(0, 5, _seguro(nome[:70]), new_x="LMARGIN", new_y="NEXT")
        capa.pdf.set_font("Helvetica", "", 8)
        capa.pdf.set_text_color(110, 120, 128)
        partes = ["%d %s" % (n, ESTADO_ROTULO.get(e, e).lower())
                  for e, n in sorted(contagem.items(), key=lambda x: -x[1])
                  if e != modelo.SEM_DOCUMENTO]
        capa.pdf.cell(0, 5, _seguro("     " + (" - ".join(partes)
                                    or "sem documento")),
                      new_x="LMARGIN", new_y="NEXT")
        resumo.append({"identidade": ident, "nome": nome,
                       "contagem": contagem})

    capa.indice(anexos)
    return {"pdf": _juntar(capa.bytes(), anexos),
            "anexados": len(anexos), "empresas": resumo}
