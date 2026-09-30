#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DANFSe montado aqui — o plano B para quando a Receita não entrega o PDF.

    python teste_danfse_local.py

POR QUE ESTE TESTE EXISTE
    Em 03/09/2026 o `/danfse` do ADN passou a responder 503 para todas as
    empresas e todas as chaves, enquanto o `/contribuintes/DFe` da MESMA sessão
    mTLS respondia 200. Ou seja: o certificado estava bom, o serviço de PDF é
    que estava fora do ar — e como o visualizador só sabia buscar o oficial, a
    nota simplesmente não podia ser vista.

    O `pdflocal` monta o mesmo documento a partir do XML, que é a fonte oficial.
    Para ele servir como substituto, duas coisas precisam continuar valendo:

    1. O DESENHO tem que bater com o do portal. As medidas do layout foram
       tiradas de um DANFSe real (posição de cada coluna, corpo de cada texto,
       altura de cada faixa). Se alguém mexer nelas sem querer, o PDF deixa de
       ser reconhecível — e ninguém percebe olhando o código.
    2. A ROTA de nota única tem que existir e não aceitar caminho de fora da
       pasta da empresa. A chave vem do navegador.

O QUE ESTE TESTE **NÃO** FAZ
    Não fala com a Receita, não usa certificado e não lê a pasta de dados real:
    monta um XML de mentira numa raiz temporária (`teste_apoio`).
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402


def _texto(pdf: bytes) -> str:
    """O texto do PDF, para conferir o que está escrito e não só se abriu."""
    import io
    import pypdf
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def modulo_nfse(dados: Path):
    """Sobe o módulo NFS-e sozinho, numa porta própria e com dados próprios.

    Porta sorteada e `FISCALE_DADOS` apontando para a raiz temporária: o
    FISCALE do usuário fica na 8777 o dia inteiro com dados de verdade, e nem
    a porta nem a pasta dele são tocadas aqui. No fim, mata só o processo que
    este teste lançou — nunca por nome nem por porta.
    """
    porta = _porta_livre()
    env = dict(os.environ)
    env["FISCALE_DADOS"] = str(dados)
    env["PYTHONIOENCODING"] = "utf-8"
    log = open(dados / "modulo.log", "wb")
    proc = subprocess.Popen(
        [sys.executable, str(RAIZ / "nfse" / "runner.py"), str(porta)],
        cwd=str(RAIZ), env=env, stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT)

    def pedir(caminho):
        c = http.client.HTTPConnection("127.0.0.1", porta, timeout=60)
        try:
            c.request("GET", caminho)
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    try:
        for _ in range(120):
            try:
                pedir("/api/certificados")
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("o módulo NFS-e não subiu")
        yield pedir
    finally:
        try:
            apoio.encerrar_arvore(proc)
        finally:
            log.close()

_ok = _falhas = 0
_erros: list[str] = []


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        _erros.append(desc)
        print("  FALHOU  " + desc)


def igual(a, b, desc):
    ok(a == b, desc if a == b else "%s  (obtive %r, esperava %r)" % (desc, a, b))


# ── uma NFS-e de mentira, no layout nacional ────────────────────────────────
CHAVE = "26116062212345678000199000000000012326070000000001"
XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">
 <infNFSe Id="NFS{CHAVE}">
  <nNFSe>123</nNFSe><cStat>100</cStat>
  <dhProc>2026-07-03T15:22:24-03:00</dhProc>
  <xLocEmi>Recife</xLocEmi><xLocPrestacao>Recife</xLocPrestacao>
  <cLocIncid>2611606</cLocIncid><xLocIncid>Recife</xLocIncid>
  <xTribNac>Advocacia</xTribNac>
  <emit>
   <CNPJ>12345678000199</CNPJ><IM>5603960</IM>
   <xNome>ESCRITORIO DE TESTE LTDA</xNome><xFant>TESTE</xFant>
   <fone>8188888888</fone><email>teste@exemplo.com.br</email>
   <enderNac><xLgr>RUA DE TESTE</xLgr><nro>217</nro><xBairro>CENTRO</xBairro>
    <cMun>2611606</cMun><UF>PE</UF><CEP>52070100</CEP></enderNac>
  </emit>
  <valores><vBC>450.00</vBC><pAliqAplic>2.00</pAliqAplic>
   <vISSQN>9.00</vISSQN><vLiq>450.00</vLiq></valores>
  <DPS><infDPS>
   <dhEmi>2026-07-03T15:22:24-03:00</dhEmi><serie>70000</serie><nDPS>295</nDPS>
   <dCompet>2026-07-02</dCompet>
   <prest><regTrib><opSimpNac>3</opSimpNac><regApTribSN>1</regApTribSN>
     <regEspTrib>0</regEspTrib></regTrib></prest>
   <serv><cServ><cTribNac>171401</cTribNac><xDescServ>Honorarios Tutela</xDescServ></cServ>
    <locPrest><cLocPrestacao>2611606</cLocPrestacao></locPrest></serv>
   <valores><vServPrest><vServ>450.00</vServ></vServPrest>
    <trib><tribMun><tribISSQN>1</tribISSQN><tpRetISSQN>1</tpRetISSQN></tribMun></trib></valores>
  </infDPS></DPS>
 </infNFSe>
</NFSe>
"""


def main():
    import pdflocal

    secao("O PDF sai de pé, a partir do XML e sem falar com ninguém")
    pdf = pdflocal.gerar(XML)
    ok(pdf[:4] == b"%PDF", "o que volta é um PDF")
    ok(len(pdf) > 5000, "e tem conteúdo (não é uma página em branco)")
    igual(pdf.count(b"/Type /Page\n"), 1, "uma nota simples cabe numa página só")

    secao("As medidas do layout são as do DANFSe oficial")
    # Medidas lidas de um DANFSe baixado do portal nacional. Não são gosto:
    # são o que faz o documento sair reconhecível ao lado do oficial.
    igual(pdflocal.COL_X, (14.2, 155.9, 297.6, 439.4), "as quatro colunas")
    igual(pdflocal.COL_W, 141.7, "o passo entre colunas")
    igual((pdflocal.REG_X0, pdflocal.REG_X1), (10.8, 577.7),
          "os extremos das réguas entre as seções")
    igual((pdflocal.S_ROT, pdflocal.S_VAL), (7.0, 8.0),
          "o corpo do rótulo e do valor")
    igual((pdflocal.MOL_X0, pdflocal.MOL_Y0, pdflocal.MOL_X1, pdflocal.MOL_Y1),
          (5.0, 5.0, 590.0, 837.0), "a moldura da página")
    ok(pdflocal.URL_CONSULTA.startswith("https://www.nfse.gov.br/ConsultaPublica/"),
       "o QR aponta para a consulta pública do portal nacional")

    secao("O texto do documento é o do DANFSe oficial")
    texto = _texto(pdf)
    for termo in ("DANFSe v1.0", "Documento Auxiliar da NFS-e",
                  "Chave de Acesso da NFS-e", "EMITENTE DA NFS-e",
                  "Prestador do Serviço", "SERVIÇO PRESTADO",
                  "TRIBUTAÇÃO MUNICIPAL", "TRIBUTAÇÃO FEDERAL",
                  "VALOR TOTAL DA NFS-E", "TOTAIS APROXIMADOS DOS TRIBUTOS",
                  "INFORMAÇÕES COMPLEMENTARES", "Valor Líquido da NFS-e"):
        ok(termo in texto, f"traz {termo!r}")
    ok(CHAVE in texto, "e a chave de acesso por extenso")
    ok("Prefeitura do Recife" in texto, "e a prefeitura do município emitente")

    secao("Sem tomador, o oficial imprime uma faixa — e nós também")
    # Este XML não tem <toma>. O oficial, nesse caso, não desenha a seção
    # vazia: troca tudo por uma faixa centralizada.
    ok("TOMADOR DO SERVIÇO NÃO IDENTIFICADO NA NFS-e" in texto,
       "a faixa aparece no lugar da seção")
    ok("Nome / Nome Empresarial" in texto,
       "e a seção do emitente continua completa (a faixa é só do tomador)")
    ok("INTERMEDIÁRIO DO SERVIÇO NÃO IDENTIFICADO NA NFS-e" in texto,
       "o mesmo vale para o intermediário")

    secao("Nome de município comprido não atropela a linha de baixo")
    # "Jaboatão dos Guararapes" quebrava em duas linhas e caía por cima de
    # "Secretaria de Finanças", porque as três linhas do canto têm posição fixa.
    longo = XML.replace("<xLocEmi>Recife</xLocEmi>",
                        "<xLocEmi>Jaboatao dos Guararapes</xLocEmi>")
    longo = longo.replace("<cMun>2611606</cMun>", "<cMun>2607901</cMun>")
    ok(pdflocal.gerar(longo)[:4] == b"%PDF",
       "município de nome longo também gera")

    secao("A rota de nota única existe, serve o PDF e não sai da pasta")
    # Pela REDE, contra o módulo NFS-e de verdade. Chamar a função direto não
    # prova nada sobre a rota: a URL, o tipo do conteúdo e o cabeçalho de cache
    # só existem no caminho HTTP.
    with apoio.raiz_temporaria() as raiz:
        (raiz / "certificados.json").write_text(json.dumps([{
            "id": "12345678000199", "cnpj": "12345678000199",
            "nome": "ESCRITORIO DE TESTE LTDA", "apelido": None,
            "caminho": "certs/nao-existe.pfx", "senha_protegida": None,
        }]), "utf-8")
        xmls = raiz / "12345678000199" / "xmls"
        xmls.mkdir(parents=True)
        (xmls / f"nfse-{CHAVE}.xml").write_text(XML, "utf-8")

        with modulo_nfse(raiz) as pedir:
            st, cab, corpo = pedir(f"/api/danfse-local?id=12345678000199&chave={CHAVE}")
            igual(st, 200, "a nota conhecida devolve 200")
            igual(cab.get("content-type"), "application/pdf",
                  "e o tipo é application/pdf")
            ok(corpo[:4] == b"%PDF", "e o corpo é mesmo um PDF")
            ok("no-store" in (cab.get("cache-control") or ""),
               "o PDF não fica no cache do navegador")

            st, _, _ = pedir("/api/danfse-local?id=12345678000199&chave=nao-existe")
            igual(st, 404, "chave desconhecida dá 404, não erro cru")

            st, _, _ = pedir("/api/danfse-local?id=12345678000199"
                             "&chave=..%2F..%2Fcertificados.json")
            igual(st, 404, "e caminho para fora da pasta da empresa não passa")

            st, _, _ = pedir(f"/api/danfse-local?id=00000000000000&chave={CHAVE}")
            igual(st, 404, "empresa que não está no cadastro dá 404")

    secao("O operador alcança a rota (ela é leitura do acervo)")
    import fiscale_papeis as papeis
    ok(papeis.pode("operador", "GET", "/api/danfse-local"),
       "o operador vê o PDF montado aqui, como já via o oficial")

    print("\n" + "=" * 62)
    print(f"{_ok} ok · {_falhas} falha(s)")
    for e in _erros:
        print("  - " + e)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
