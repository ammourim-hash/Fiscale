#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fase C — a varredura de Optantes: carteira, CSV, PDF e a referência da base.

    python teste_optantes.py

POR QUE ESTE TESTE EXISTE

    1. A REFERÊNCIA DA BASE É O CAMPO MAIS IMPORTANTE DO CABEÇALHO, e é o mais
       fácil de errar. A base de Dados Abertos é atualizada em lote,
       mensalmente: medido em 12/09/2026, ela respondia `2026-08`. Um relatório
       sem essa data parece atual e não é — uma exclusão do Simples feita em
       agosto ainda sai como "optante".

       O erro que este teste impede é o pior de todos: **carimbar a data de
       hoje** quando a fonte não informa. Aqui, fonte muda devolve
       "não informada pela fonte", e a data de hoje nunca aparece como
       referência.

    2. CARIMBAR A DATA DA MINHA RECEITA NUM REGISTRO DA BRASILAPI seria
       emprestar procedência de uma fonte para o dado de outra. Só a Minha
       Receita publica a referência (`/updated`); a BrasilAPI não tem endpoint
       equivalente. Então a Minha Receita é primária NO RELATÓRIO, e linha que
       vier da retaguarda sai marcada na própria linha.

    3. A REGRA WHITE-LABEL SÓ É REGRA SE FOR VARRIDA NOS BYTES. O `pypdf` grava
       `/Producer: pypdf` por conta própria; o `fpdf2` não, mas o título é
       visível na aba do navegador. A varredura é a mesma do dossiê, e o nome
       do arquivo entra nela — de nada serviria limpar metadados e chamar o
       arquivo de "fiscale-optantes.pdf".

    4. A COLUNA DO CERTIFICADO distingue "não tem" de "cadastrado, mas o
       arquivo sumiu". São problemas diferentes e mostrá-los iguais manda
       alguém procurar um arquivo que nunca existiu.

O QUE ESTE TESTE **NÃO** FAZ
    Não vai à rede: `varrer()` recebe um `consultar` de mentira. Não lê a pasta
    de dados real. E não tenta adivinhar quantas empresas o escritório tem — o
    universo é construído a partir de cadastros montados aqui.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import optantes  # noqa: E402
import relatorio_base as base  # noqa: E402

# AS MARCAS QUE NAO PODEM APARECER. Minusculas, porque a busca e insensivel.
MARCAS = (b"fiscale", b"pypdf", b"fpdf", b"pyfpdf")

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


# ── cadastros de mentira ────────────────────────────────────────────────
CLIENTES = [
    {"nome": "ALFA COMERCIO DE MATERIAIS LTDA", "cnpj": "11.222.333/0001-81"},
    {"nome": "BETA SERVICOS LTDA", "cnpj": "04.103.256/0001-85"},
    {"nome": "GAMA CONSULTORIA LTDA", "cnpj": "62.345.002/0001-70"},
    {"nome": "Pessoa Fisica Cliente", "cnpj": "123.456.789-09"},
]


def certificados(tmp: Path):
    """Três situações de cofre, de propósito: arquivo presente, ausente, e
    empresa que existe SÓ como conta de certificado (sem registro em Clientes).
    """
    presente = tmp / "alfa.pfx"
    presente.write_bytes(b"nao e um pfx de verdade, e nao precisa ser")
    return [
        {"cnpj": "11222333000181", "nome": "ALFA COMERCIO DE MATERIAIS LTDA",
         "caminho": str(presente)},
        {"cnpj": "04103256000185", "nome": "BETA SERVICOS LTDA",
         "caminho": str(tmp / "nao-existe.pfx")},
        {"cnpj": "55444333000122", "apelido": "DELTA SO CERTIFICADO",
         "caminho": str(presente)},
    ]


def consultar_falso(respostas):
    """Um `cnpj_publico.consultar` de mentira, com a mesma assinatura."""
    def consultar(cnpj, usar_cache=True, fontes=None):
        return dict(respostas.get(cnpj, {"ok": False, "erro": "sem resposta"}))
    return consultar


RESPOSTAS = {
    "11222333000181": {"ok": True, "fonte": "Minha Receita",
                       "razao_social": "ALFA COMERCIO DE MATERIAIS LTDA",
                       "situacao": "ATIVA", "optante_simples": True, "mei": False,
                       "data_opcao_pelo_simples": None,
                       "data_opcao_simples": "2019-01-01",
                       "inicio_atividade": "2018-05-10",
                       "municipio": "RECIFE", "uf": "PE"},
    "04103256000185": {"ok": True, "fonte": "BrasilAPI",   # ← a retaguarda
                       "razao_social": "BETA SERVICOS LTDA",
                       "situacao": "ATIVA", "optante_simples": False, "mei": False,
                       "data_exclusao_simples": "2025-12-31",
                       "municipio": "OLINDA", "uf": "PE"},
    "62345002000170": {"ok": False, "erro": "HTTP 500"},   # ← falha de consulta
    "55444333000122": {"ok": True, "fonte": "Minha Receita",
                       "razao_social": "DELTA SO CERTIFICADO",
                       "situacao": "ATIVA", "optante_simples": True, "mei": True,
                       "municipio": "PAULISTA", "uf": "PE"},
}


def main():
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fiscale_teste_optantes_"))

    secao("A carteira: as duas fontes de cadastro, sem repetir")
    u = optantes.universo(CLIENTES, certificados(tmp))
    igual(len(u), 5, "5 documentos distintos (4 do Clientes + 1 só do cofre)")
    por_doc = {l["documento"]: l for l in u}
    ok("55444333000122" in por_doc,
       "a empresa que existe SÓ como certificado entra na lista")
    igual(por_doc["55444333000122"]["cadastros"], ["Certificado"],
          "e ela diz de onde veio")
    igual(por_doc["11222333000181"]["cadastros"], ["Clientes", "Certificado"],
          "quem está nas duas fontes mostra as duas")

    secao("A coluna do certificado tem TRÊS estados, não dois")
    igual(por_doc["11222333000181"]["certificado"], optantes.CERT_SIM,
          "arquivo no cofre: sim")
    igual(por_doc["04103256000185"]["certificado"], optantes.CERT_SEM_ARQUIVO,
          "cadastrado mas o .pfx não está no disco: estado próprio")
    igual(por_doc["62345002000170"]["certificado"], optantes.CERT_NAO,
          "sem certificado nenhum: não")
    ok(optantes.CERT_SEM_ARQUIVO != optantes.CERT_NAO,
       "e os dois problemas NÃO são o mesmo rótulo")

    secao("CPF fica fora do escopo, com motivo — não por omissão")
    cpf = por_doc["12345678909"]
    ok(not cpf["consultavel"], "não é consultável")
    ok("só para CNPJ" in (cpf.get("motivo") or ""),
       "e o motivo está escrito: a Consulta Optantes é só para CNPJ")

    secao("A varredura")
    res = optantes.varrer(u, consultar=consultar_falso(RESPOSTAS))
    igual(res["total"], 5, "5 linhas no total")
    igual(res["consultadas"], 3, "3 responderam")
    igual(res["falhas"], 1, "1 não consultada")
    igual(res["fora_do_escopo"], 1, "1 fora do escopo (o CPF)")
    igual(res["retaguarda"], 1, "e 1 veio da fonte de retaguarda")

    linhas = {l["documento"]: l for l in res["linhas"]}
    igual(linhas["11222333000181"]["estado"], "optante", "ALFA é optante")
    igual(linhas["04103256000185"]["estado"], "não optante", "BETA não é")
    igual(linhas["55444333000122"]["estado"], "MEI", "DELTA é MEI")
    ok("retaguarda" in linhas["04103256000185"]["observacao"],
       "a linha da retaguarda sai MARCADA nela mesma")
    ok(not linhas["11222333000181"]["observacao"],
       "e a linha da fonte primária não leva marca nenhuma")

    # ═══════════════════════════════════════════════════════════════════
    secao("A REFERÊNCIA DA BASE, que é o motivo de o cabeçalho existir")
    pdf = optantes.pdf_bytes(res, {"ok": True, "referencia": "2026-08"})
    import pypdf
    texto = "\n".join(p.extract_text() for p in
                      pypdf.PdfReader(io.BytesIO(pdf)).pages)
    ok("Referência da base: 08/2026" in texto,
       "a data vem CRAVADA no cabeçalho, em 08/2026")
    ok("atualizada em lote" in texto,
       "com a explicação de por que ela importa")
    ok("retaguarda" in texto,
       "e o cabeçalho avisa que há linha sem referência publicada")

    secao("Fonte muda: 'não informada' — NUNCA a data de hoje")
    import datetime as dt
    hoje = dt.date(2026, 9, 12)
    sem = optantes.pdf_bytes(res, {"ok": False, "referencia": "", "erro": "timeout"},
                             hoje=hoje)
    t_sem = "\n".join(p.extract_text() for p in
                      pypdf.PdfReader(io.BytesIO(sem)).pages)
    ok(optantes.SEM_REFERENCIA in t_sem,
       "o cabeçalho diz %r" % optantes.SEM_REFERENCIA)
    ok("Referência da base: 12/09/2026" not in t_sem,
       "e a data de HOJE não aparece como referência")
    ok("Referência da base: 09/2026" not in t_sem,
       "nem o mês de hoje disfarçado de referência")
    ok("Emitido em 12/09/2026" in t_sem,
       "a data de hoje aparece só onde é verdade: 'Emitido em'")

    secao("O que o documento É, dito no rodapé")
    ok("Nao e certidao" in t_sem or "Nao e certidao" in texto,
       "o rodapé nega que seja certidão")
    ok("Consulta Optantes oficial" in texto,
       "e manda à consulta oficial em caso de divergência")

    # ═══════════════════════════════════════════════════════════════════
    secao("A REGRA: nenhuma marca do sistema, em lugar nenhum")
    # A MESMA VARREDURA DO DOSSIE. Varre os BYTES INTEIROS -- texto,
    # metadados, streams, tudo.
    bruto = pdf.lower()
    for marca in MARCAS:
        ok(marca not in bruto,
           "a palavra %r não aparece em nenhum byte do PDF" % marca.decode())

    meta = pypdf.PdfReader(io.BytesIO(pdf)).metadata or {}
    print("     metadados: %s" % dict(meta))
    for chave in ("/Producer", "/Creator", "/Author"):
        ok(not (meta.get(chave) or "").strip(), "%s está vazio" % chave)
    igual(meta.get("/Title"), optantes.TITULO,
          "e o título é o do CONTEÚDO: %r" % optantes.TITULO)

    secao("O nome do arquivo também é superfície da regra")
    for nome in (optantes.nome_pdf(hoje), optantes.nome_csv(hoje)):
        ok(nome.isascii(), "%r é ASCII (vai no Content-Disposition)" % nome)
        for marca in MARCAS:
            ok(marca.decode() not in nome.lower(),
               "%r não carrega %r" % (nome, marca.decode()))
    igual(optantes.nome_pdf(hoje), "optantes-simples-nacional-20260912.pdf",
          "o nome descreve o conteúdo e leva a data")
    ok(optantes.nome_csv(hoje).endswith(".csv"), "e o CSV sai com .csv")

    secao("O CSV: a referência antes de tudo, e o Excel pt-BR abrindo")
    csv_b = optantes.csv_bytes(res, {"ok": True, "referencia": "2026-08"})
    ok(csv_b.startswith(b"\xef\xbb\xbf"),
       "começa com BOM — é o que faz o Excel pt-BR abrir certo")
    linhas_csv = csv_b.decode("utf-8-sig").splitlines()
    ok(linhas_csv[0].startswith("Referência da base;2026-08"),
       "a PRIMEIRA linha é a referência da base")
    igual(linhas_csv[3].split(";")[0], "Empresa", "o cabeçalho vem depois")
    igual(len(linhas_csv[3].split(";")), len(optantes.COLUNAS),
          "com as %d colunas declaradas" % len(optantes.COLUNAS))
    ok("Certificado no cofre" in linhas_csv[3],
       "inclusive a coluna do certificado")
    igual(len([l for l in linhas_csv[4:] if l.strip()]), 5,
          "e uma linha por empresa, o CPF incluído")
    corpo = "\n".join(linhas_csv[4:])
    ok("Arquivo ausente" in corpo,
       "o estado 'arquivo ausente' aparece na planilha")
    ok("retaguarda" in corpo, "e a marca da retaguarda também")

    sem_ref = optantes.csv_bytes(res, {"ok": False, "referencia": ""})
    ok(optantes.SEM_REFERENCIA in sem_ref.decode("utf-8-sig").splitlines()[0],
       "sem referência, o CSV diz isso na primeira linha")

    secao("A fonte primária do relatório é a que publica a referência")
    import cnpj_publico
    igual(cnpj_publico.FONTES_COM_REFERENCIA[0][0], optantes.FONTE_PRIMARIA,
          "Minha Receita é a primeira em FONTES_COM_REFERENCIA")
    igual(cnpj_publico.FONTES[0][0], "BrasilAPI",
          "e a ordem da TELA continua como era (nada mudou para ela)")
    ok("minhareceita.org/updated" in cnpj_publico.URL_REFERENCIA,
       "a referência vem do endpoint separado da Minha Receita")

    secao("Carteira vazia não derruba o relatório")
    vazio = optantes.varrer([], consultar=consultar_falso({}))
    igual(vazio["total"], 0, "zero linhas")
    b_pdf = optantes.pdf_bytes(vazio, {"ok": True, "referencia": "2026-08"})
    ok(b_pdf[:4] == b"%PDF", "o PDF sai de pé mesmo assim")
    ok(optantes.csv_bytes(vazio, {"ok": True, "referencia": "2026-08"}),
       "e o CSV também")

    secao("A regra white-label tem UMA implementação")
    import situacao.relatorio as sit_rel
    ok(sit_rel.METADADOS == base.metadados(sit_rel.TITULO),
       "o dossiê usa os metadados de `relatorio_base`")
    ok(sit_rel._seguro is base.seguro,
       "e o saneador de texto é o mesmo objeto, não uma cópia")
    ok(issubclass(sit_rel._Capa, base.Capa),
       "a capa do dossiê herda a base white-label")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"{_ok} ok · {_falhas} falha(s)")
    for e in _erros:
        print("  - " + e)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
