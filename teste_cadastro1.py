#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cadastro 1 — importar planilha de empresas e ordenar em um lugar só.

    python teste_cadastro1.py

O QUE SE PROVA
    Que a conciliação é pelo DOCUMENTO com dígito verificador — e que o
    documento pode ser CPF, porque um dos clientes reais é pessoa física.
    Que nada é sobrescrito em silêncio: valor diferente é CONFLITO, e conflito
    não se aplica sozinho. Que a planilha é dado e nunca programa. E que a
    ordem das empresas vem de UM lugar, usado por todas as telas.

    Nenhum dado real aparece aqui: os CNPJs são inventados e têm DV válido.
"""
from __future__ import annotations

import ast
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_cadastro as cad                         # noqa: E402
import fiscale_papeis as fp                            # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                               # noqa: E402


def _connect_proibido(self, endereco, *a, **kw):
    raise AssertionError("teste tentou abrir rede para " + str(endereco))


_socket.socket.connect = _connect_proibido


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
    if a == b:
        ok(True, desc)
    else:
        ok(False, "%s  (obtive %.160r, esperava %.160r)" % (desc, a, b))


# CNPJs inventados, com dígito verificador correto.
A = "11222333000181"
B = "11444777000161"
C = "66789006000106"
CPF = "12345678909"          # DV válido; representa a pessoa física do cadastro


def csv_de(linhas) -> bytes:
    saida = io.StringIO()
    for l in linhas:
        saida.write(";".join(str(c) for c in l) + "\r\n")
    return saida.getvalue().encode("utf-8")


def xlsx_de(linhas, com_macro=False, formula_sem_valor=False) -> bytes:
    """Um .xlsx de verdade, montado à mão — sem depender de openpyxl."""
    def col(i):
        s, i = "", i + 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    linhas_xml = []
    for r, linha in enumerate(linhas, start=1):
        cs = []
        for c, valor in enumerate(linha):
            ref = "%s%d" % (col(c), r)
            if formula_sem_valor and r == 2 and c == 1:
                cs.append('<c r="%s"><f>A1</f></c>' % ref)
            else:
                texto = str(valor).replace("&", "&amp;").replace("<", "&lt;")
                cs.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>'
                          % (ref, texto))
        linhas_xml.append('<row r="%d">%s</row>' % (r, "".join(cs)))
    folha = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxml'
             'formats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData>'
             '</worksheet>' % "".join(linhas_xml))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook/>')
        z.writestr("xl/worksheets/sheet1.xml", folha)
        if com_macro:
            z.writestr("xl/vbaProject.bin", b"\x00macro fingida")
    return buf.getvalue()


CABECALHO = ["CNPJ", "Razão Social", "Nome Fantasia", "CNAE Principal",
             "CNAEs Secundários", "Inscrição Estadual", "Inscrição Municipal",
             "Município", "UF", "Código IBGE", "Regime Tributário",
             "Situação", "E-mail", "Contato"]


def linha_de(cnpj, razao, **extra):
    d = {"CNAE Principal": "6920-6/01", "UF": "PE", "Município": "Recife"}
    d.update(extra)
    return [cnpj, razao, d.get("Nome Fantasia", ""), d.get("CNAE Principal"),
            d.get("CNAEs Secundários", ""), d.get("Inscrição Estadual", ""),
            d.get("Inscrição Municipal", ""), d.get("Município"), d.get("UF"),
            d.get("Código IBGE", ""), d.get("Regime Tributário", ""),
            d.get("Situação", ""), d.get("E-mail", ""), d.get("Contato", "")]


def main():
    # ── Documento ────────────────────────────────────────────────────────
    secao("O documento: CNPJ ou CPF, com dígito verificador")
    for v in (A, "11.222.333/0001-81", " 11222333000181 "):
        igual(cad.normalizar_documento(v), A, "normaliza %r" % str(v)[:22])
    ok(cad.documento_valido(A), "CNPJ com DV correto passa")
    ok(not cad.documento_valido("11222333000199"), "DV errado é recusado")
    ok(not cad.documento_valido("00000000000000"), "tudo zero é recusado")
    ok(not cad.documento_valido("11111111111111"), "dígito repetido é recusado")

    # O ZERO À ESQUERDA — o erro mais comum de planilha.
    igual(cad.normalizar_documento(5678005000191), "05678005000191",
          "CNPJ guardado como NÚMERO recupera o zero à esquerda")

    # PESSOA FÍSICA — o ensaio sobre o cadastro real encontrou uma.
    igual(cad.normalizar_documento(CPF), CPF, "CPF de 11 dígitos fica com 11")
    ok(cad.documento_valido(CPF), "e é validado pelo DV de CPF")
    ok(cad.eh_pessoa_fisica(CPF), "reconhecido como pessoa física")
    ok(not cad.eh_pessoa_fisica(A), "e o CNPJ não")
    ok(len(cad.normalizar_documento(CPF)) == 11,
       "o CPF NÃO é completado com zeros até 14 — isso o transformaria "
       "num documento que não é de ninguém")
    igual(cad.formatar_cnpj(CPF), "123.456.789-09", "e é formatado como CPF")

    # ── CNAE ─────────────────────────────────────────────────────────────
    secao("CNAE: sete dígitos, sem repetição, sem descrição inventada")
    for v in ("69.20-6/01", "6920-6/01", "6920601", " 6920601 "):
        igual(cad.normalizar_cnae(v), "6920601", "normaliza %r" % v.strip())
    igual(cad.normalizar_cnae("692060"), "", "menos de 7 dígitos é inválido")
    igual(cad.normalizar_cnae(""), "", "vazio é vazio")
    igual(cad.normalizar_cnaes("6201-5/01;6209-1/00;6201501"),
          ["6201501", "6209100"], "secundárias sem repetição")
    igual(cad.normalizar_cnaes("6920601;6201501", principal="6920601"),
          ["6201501"], "e sem repetir a principal")
    igual(cad.normalizar_cnaes(""), [], "vazio dá lista vazia")
    fonte = (RAIZ / "fiscale_cadastro.py").read_text(encoding="utf-8")
    ok("descricao_cnae" not in fonte and "DESCRICOES" not in fonte,
       "o módulo não tem tabela de descrição de CNAE — não inventa o que "
       "a planilha não trouxe")

    # ── Ordenação central ────────────────────────────────────────────────
    secao("Ordenação: Razão Social, Nome Fantasia, documento")
    lista = [
        {"razao_social": "Zeta", "cnpj": A},
        {"razao_social": "álfa", "nome_fantasia": "B", "cnpj": B},
        {"razao_social": "ALFA", "nome_fantasia": "A", "cnpj": C},
        {"razao_social": "Alfa", "nome_fantasia": "A", "cnpj": A},
        {"nome": "Beta", "cnpj": B},
    ]
    ordenada = cad.ordenar(lista)
    # "Alfa" e "ALFA" dobram para a MESMA chave, e aí quem decide é o
    # documento — não a caixa das letras. Foi isso que a primeira versão deste
    # teste errou: eu esperava a ordem alfabética do original, que é
    # exatamente o que a dobra existe para ignorar.
    igual([e.get("razao_social") or e.get("nome") for e in ordenada],
          ["Alfa", "ALFA", "álfa", "Beta", "Zeta"],
          "acento e caixa não mudam a posição")
    igual([e["cnpj"] for e in ordenada[:2]], [A, C],
          "mesmo nome e mesmo fantasia: o documento desempata")
    ok(cad.ordenar([]) == [], "lista vazia não estoura")
    ok(cad.chave_ordenacao({}) == ("", "", ""), "registro vazio não estoura")
    # `nome` e `apelido` são os nomes ANTIGOS dos campos: a ordem tem de
    # funcionar no cadastro que já existe, não só no formato novo.
    igual(cad.chave_ordenacao({"nome": "Água", "apelido": "X", "cnpj": A}),
          ("agua", "x", A),
          "entende os campos antigos (nome/apelido)")

    # ── Leitura de planilha ──────────────────────────────────────────────
    secao("A planilha é dado, nunca programa")
    dados = [CABECALHO, linha_de(A, "Alfa Ltda")]
    for rotulo, bruto, nome in (("CSV", csv_de(dados), "e.csv"),
                                ("XLSX", xlsx_de(dados), "e.xlsx")):
        linhas, avisos = cad.ler_planilha(bruto, nome)
        igual(len(linhas), 1, "%s: uma linha de dados" % rotulo)
        igual(cad.normalizar_documento(linhas[0]["cnpj"]), A,
              "%s: o CNPJ chegou" % rotulo)
        igual(linhas[0]["razao_social"], "Alfa Ltda",
              "%s: a razão social chegou" % rotulo)

    try:
        cad.ler_planilha(xlsx_de(dados, com_macro=True), "m.xlsx")
        ok(False, "planilha com macro deveria ser recusada")
    except cad.PlanilhaInvalida as e:
        ok("macros" in str(e), "planilha com macro é recusada, e diz por quê")

    linhas, _ = cad.ler_planilha(
        xlsx_de([CABECALHO, linha_de(A, "Alfa")], formula_sem_valor=True),
        "f.xlsx")
    ok(any("#FORMULA" == v for v in linhas[0].values()),
       "fórmula sem resultado gravado é marcada, não silenciada")
    arv = ast.parse(fonte)
    nomes = {getattr(n.func, "id", "") for n in ast.walk(arv)
             if isinstance(n, ast.Call)}
    ok("eval" not in nomes and "exec" not in nomes,
       "o módulo não avalia nada — fórmula não é executada")
    ok("externalLink" not in fonte and "http" not in fonte.split('"""')[2],
       "e não abre vínculo externo")

    secao("Limites da planilha")
    try:
        cad.ler_planilha(b"x" * (cad.TAMANHO_MAX + 1), "g.csv")
        ok(False, "arquivo grande demais deveria ser recusado")
    except cad.PlanilhaInvalida as e:
        ok("limite" in str(e), "arquivo acima do limite de tamanho é recusado")
    try:
        cad.ler_planilha(csv_de([CABECALHO] + [linha_de(A, "X")] *
                                (cad.LINHAS_MAX + 5)), "g.csv")
        ok(False, "linhas demais deveriam ser recusadas")
    except cad.PlanilhaInvalida as e:
        ok("linhas" in str(e), "arquivo com linhas demais é recusado")
    for bruto, nome, porque in ((b"x", "e.exe", "extensão não aceita"),
                                (b"", "e.csv", "arquivo vazio"),
                                (b"x", "e.xlsx", "xlsx que não é ZIP")):
        try:
            cad.ler_planilha(bruto, nome)
            ok(False, "%s deveria ser recusado" % porque)
        except cad.PlanilhaInvalida:
            ok(True, "%s é recusado" % porque)
    try:
        cad.ler_planilha(csv_de([["Coluna A", "Coluna B"], ["1", "2"]]), "s.csv")
        ok(False, "planilha sem coluna CNPJ deveria ser recusada")
    except cad.PlanilhaInvalida as e:
        ok("CNPJ" in str(e), "planilha sem a coluna CNPJ é recusada")

    secao("Cabeçalhos são reconhecidos com folga")
    for bruto, esperado in (("CNPJ", "cnpj"), ("cnpj", "cnpj"),
                            ("Razão Social", "razao_social"),
                            ("RAZAO SOCIAL", "razao_social"),
                            ("Inscrição Estadual", "ie"), ("IE", "ie"),
                            ("Código IBGE", "ibge"), ("Cidade", "municipio"),
                            ("Telefone", "contato"), ("Coluna X", "")):
        igual(cad.campo_do_cabecalho(bruto), esperado,
              "cabeçalho %r -> %r" % (bruto, esperado or "(ignorado)"))

    # ── Conciliação ──────────────────────────────────────────────────────
    secao("Conciliação: novos, atualizados, inalterados, conflitos e erros")
    atuais = [
        {"id": 1, "cnpj": A, "nome": "Alfa Ltda", "mun": "Recife", "uf": "PE",
         "ie": "111", "regime": "Simples Nacional"},
        {"id": 2, "cnpj": CPF, "nome": "Fulano de Tal", "uf": "PE"},
    ]
    planilha = [
        linha_de(A, "Alfa Ltda", **{"Inscrição Estadual": "111",
                                    "Código IBGE": "2611606"}),   # atualiza
        linha_de(B, "Beta Ltda"),                                  # novo
        linha_de(C, "Gama Ltda", **{"CNAE Principal": ""}),        # erro
        linha_de("11222333000199", "DV errado"),                   # erro
        linha_de(CPF, "Fulano de Tal", **{"UF": "SP"}),            # conflito
        ["", "Sem documento", "", "6920601", "", "", "", "", "", "", "", "", "", ""],
    ]
    linhas, _ = cad.ler_planilha(csv_de([CABECALHO] + planilha), "p.csv")
    previa = cad.conciliar(atuais, linhas)
    r = previa["resumo"]
    igual(r["novos"], 1, "um cadastro novo")
    igual(r["atualizados"], 1, "um atualizado (campo vazio preenchido)")
    igual(r["conflitos"], 1, "um conflito (UF diferente)")
    igual(r["erros"], 3, "três erros")
    motivos = " | ".join(e["motivo"] for e in previa["erros"])
    ok("dígito verificador" in motivos, "o DV inválido é apontado")
    ok("CNAE" in motivos, "cadastro novo sem CNAE principal é apontado")
    ok("vazio ou ilegível" in motivos, "linha sem documento é apontada")
    ok(previa["conflitos"][0]["campos"][0]["campo"] == "uf",
       "o conflito diz QUAL campo choca")
    ok(previa["conflitos"][0]["campos"][0]["atual"] == "PE"
       and previa["conflitos"][0]["campos"][0]["planilha"] == "SP",
       "mostrando o que está no cadastro e o que a planilha traz")
    ok("ibge" in previa["atualizados"][0]["preencher"],
       "a atualização é do campo que estava VAZIO")
    ok("ie" not in previa["atualizados"][0]["preencher"],
       "e não do que já tinha o mesmo valor")

    secao("Documento repetido na planilha")
    dup, _ = cad.ler_planilha(
        csv_de([CABECALHO, linha_de(A, "X"), linha_de("11.222.333/0001-81", "Y")]),
        "d.csv")
    p2 = cad.conciliar([], dup)
    igual(p2["resumo"]["erros"], 1,
          "o mesmo documento escrito de dois jeitos é UMA empresa, não duas")
    ok("repetido" in p2["erros"][0]["motivo"], "e o motivo diz isso")

    secao("Nome com pontuação ou caixa diferente NÃO duplica")
    p3 = cad.conciliar(
        [{"id": 1, "cnpj": A, "nome": "ALFA LTDA."}],
        cad.ler_planilha(csv_de([CABECALHO, linha_de(A, " alfa ltda. ")]),
                         "n.csv")[0])
    igual(p3["resumo"]["novos"], 0, "não criou empresa nova")
    igual(p3["resumo"]["conflitos"], 0,
          "e não achou conflito: é o mesmo nome com outra caixa")

    # ── Aplicar ──────────────────────────────────────────────────────────
    secao("Aplicar: só o que a prévia mostrou")
    novos, aplicado = cad.aplicar(atuais, previa)
    igual(aplicado["criados"], 1, "criou um")
    igual(aplicado["atualizados"], 1, "atualizou um")
    igual(aplicado["conflitos_aplicados"], 0,
          "e NÃO tocou no conflito — trocar o que alguém escreveu é decisão "
          "de gente")
    igual(len(novos), 3, "o cadastro foi de 2 para 3")
    por_doc = {cad.normalizar_documento(e["cnpj"]): e for e in novos}
    igual(por_doc[CPF].get("uf"), "PE", "a UF em conflito continua como estava")
    igual(por_doc[A].get("ibge"), "2611606", "o campo vazio foi preenchido")
    igual(por_doc[A].get("id"), 1, "e o id interno de quem já existia NÃO mudou")
    ok(por_doc[B].get("id") not in (None, 1, 2), "o novo ganhou id próprio")
    ok(novos == cad.ordenar(novos), "o resultado sai na ordem central")

    secao("Aplicar com conflitos aceitos, quando alguém DECIDE")
    novos2, aplicado2 = cad.aplicar(atuais, previa, aceitar_conflitos=True)
    igual(aplicado2["conflitos_aplicados"], 1, "aí sim o conflito é aplicado")
    igual({cad.normalizar_documento(e["cnpj"]): e
           for e in novos2}[CPF].get("uf"), "SP", "com o valor da planilha")

    secao("Os cadastros existentes são preservados")
    vazia, _ = cad.ler_planilha(csv_de([CABECALHO]), "v.csv") \
        if False else ([], [])
    intactos, ap = cad.aplicar(atuais, cad.conciliar(atuais, []))
    igual(len(intactos), len(atuais), "planilha sem linhas não muda o cadastro")
    igual(ap, {"criados": 0, "atualizados": 0, "campos": 0,
               "conflitos_aplicados": 0}, "e nada é contado como feito")

    # ── Modelo ───────────────────────────────────────────────────────────
    secao("O modelo para download")
    modelo = cad.modelo_csv()
    ok(modelo.startswith(b"\xef\xbb\xbf"),
       "tem BOM — sem ele o Excel brasileiro abre tudo numa célula só")
    linhas_m, avisos_m = cad.ler_planilha(modelo, "modelo.csv")
    igual(len(linhas_m), 2,
          "o próprio modelo é uma planilha válida (duas linhas de exemplo)")
    igual(avisos_m, [], "e não tem coluna que o sistema ignore")
    prev_m = cad.conciliar([], linhas_m)
    igual(prev_m["resumo"]["novos"], 2,
          "as linhas de exemplo do modelo são importáveis")
    igual(prev_m["resumo"]["erros"], 0, "sem erro nenhum")
    for rotulo in cad.ROTULOS.values():
        ok(rotulo.encode("utf-8") in modelo,
           "o modelo traz a coluna %r" % rotulo)

    # ── Servidor: rotas, papéis, backup, auditoria ───────────────────────
    secao("As rotas do servidor")
    servidor = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8")
    sem_comentario = "\n".join(l for l in servidor.splitlines()
                               if not l.strip().startswith("#"))
    # O BLOCO E RECORTADO POR INDENTACAO, NAO POR VIZINHO.
    #     A versao anterior deste teste terminava o recorte no
    #     `/api/backup/inspecionar`, que por acaso vinha depois. Quando o bloco
    #     mudou de lugar - justamente a correcao que este teste deveria pegar -
    #     o marcador sumiu e o teste explodiu em vez de reprovar. Indentacao e
    #     do proprio bloco: ele so termina quando o `do_POST` volta a margem.
    linhas_srv = sem_comentario.splitlines()
    inicio = next(n for n, l in enumerate(linhas_srv)
                  if l.strip() == 'if rota.startswith("/api/clientes/importar/"):')
    fim = next((n for n in range(inicio + 1, len(linhas_srv))
                if linhas_srv[n].strip()
                and len(linhas_srv[n]) - len(linhas_srv[n].lstrip()) <= 8),
               len(linhas_srv))
    bloco = "\n".join(linhas_srv[inicio:fim])

    # O BLOCO VEM DEPOIS DOS PORTOES. Antes ele vinha antes, e se defendia
    #     sozinho com o proprio `eh_admin`: quem nao tinha sessao levava 403 em
    #     vez de 401, e a classificacao em `fiscale_papeis` - a fonte unica de
    #     quem alcanca o que - nao governava estas duas rotas coisa nenhuma.
    corpo_post = sem_comentario[sem_comentario.index("def do_POST"):]
    ok(corpo_post.index("self._exigir_login(rota)")
       < corpo_post.index('if rota.startswith("/api/clientes/importar/"):'),
       "o bloco de importacao vem DEPOIS do portao de login")
    ok(corpo_post.index("self._barrar_por_papel(rota)")
       < corpo_post.index('if rota.startswith("/api/clientes/importar/"):'),
       "e depois do portao de papeis")

    i = bloco.index('if rota == "/api/clientes/importar/previa":')
    j = bloco.index('if rota == "/api/clientes/importar/aplicar":', i)
    guarda = bloco[:i]
    r_previa, r_aplicar = bloco[i:j], bloco[j:]

    ok("gravar_json_atomico" not in r_previa and "shutil.copy" not in r_previa,
       "a PRÉVIA não escreve nada")
    ok("conciliar" in r_previa, "ela só concilia")
    ok("shutil.copy2" in r_aplicar,
       "aplicar faz backup do cadastro ANTES de escrever")
    ok(r_aplicar.index("shutil.copy2") < r_aplicar.index("gravar_json_atomico"),
       "e o backup vem antes da gravação")
    ok("gravar_json_atomico" in r_aplicar, "a gravação é atômica")
    ok("_auditoria_ou_recusa()" in r_aplicar,
       "e é recusada quando não há como auditar")
    ok("auditar(" in r_aplicar, "a aplicação é registrada na auditoria")
    ok("b64" not in r_aplicar.split("auditar(")[1][:300]
       and "linhas" not in r_aplicar.split("auditar(")[1][:300],
       "sem guardar o conteúdo da planilha no registro")
    ok("eh_admin" in guarda,
       "as duas passam pela mesma guarda de administrador")

    for metodo, rota in (("POST", "/api/clientes/importar/previa"),
                         ("POST", "/api/clientes/importar/aplicar")):
        igual(fp.classificar(metodo, rota), fp.ADMIN,
              "%s é exclusiva do administrador" % rota)
        ok(not fp.pode(fp.OPERADOR, metodo, rota),
           "o operador não alcança %s" % rota)
    igual(fp.classificar("GET", "/api/clientes/modelo"), fp.OPERADOR,
          "o modelo, que não tem dado de ninguém, o operador baixa")

    secao("A ordenação central chegou nas telas")
    ok('mod == "clientes"' in servidor and "fiscale_cadastro.ordenar" in servidor,
       "Clientes: ordenado no servidor")
    principal = (RAIZ / "nfse" / "backend" / "main.py").read_text(encoding="utf-8")
    ok("fcad.ordenar(saida)" in principal,
       "Certificados: /api/certificados sai ordenado")
    emp = (RAIZ / "nfse" / "backend" / "empresas_nfe.py").read_text(encoding="utf-8")
    ok("_cad.ordenar(operacionais)" in emp,
       "NF-e / NFC-e / CT-e: o seletor sai ordenado")
    ok("_cad.chave_ordenacao(p)" in emp,
       "e as pendências desempatam pela mesma ordem")
    # NFS-e: o seletor dele lê /api/certificados, que já vem ordenado.
    tela_nfse = (RAIZ / "nfse" / "frontend" / "index.html").read_text(
        encoding="utf-8", errors="ignore")
    ok("/api/certificados" in tela_nfse,
       "NFS-e: o seletor lê /api/certificados, que já sai ordenado")

    # Uma fonte só para a ordem: ninguém pode reordenar por conta própria.
    for arquivo, proibido in ((principal, "sorted(saida"),
                              (emp, "operacionais.sort(")):
        ok(proibido not in arquivo,
           "e ninguém reordena por fora (%r)" % proibido)

    secao("Nada foi preparado para vincular certificado ainda")
    ok("vincular_certificado" not in fonte and "deposito" not in fonte.lower(),
       "a fase seguinte não foi antecipada")
    ok("cnae_principal" in cad.CAMPOS and "ibge" in cad.CAMPOS,
       "mas o cadastro já tem os campos que ela vai usar")

    # ── Cadastro 1.1 ─────────────────────────────────────────────────────
    secao("1.1 · O tipo do documento é dito, não adivinhado")
    igual(cad.tipo_documento(A), "CNPJ", "CNPJ é reconhecido")
    igual(cad.tipo_documento(CPF), "CPF", "CPF é reconhecido")
    igual(cad.tipo_documento("123"), "", "lixo não vira tipo nenhum")
    igual(cad.documento_de({"id": A}), A, "lê do campo `id` (certificados)")
    igual(cad.documento_de({"cnpj": "123.456.789-09"}), CPF,
          "lê do campo `cnpj` (clientes), com pontuação")
    igual(cad.documento_de({"documento": CPF, "cnpj": A}), CPF,
          "`documento` vence, quando existe")
    igual(cad.documento_de({}), "", "registro sem documento não estoura")
    enriquecido = cad.com_tipo({"nome": "Fulano", "cnpj": CPF})
    igual(enriquecido["tipo_documento"], "CPF", "com_tipo informa o tipo")
    igual(enriquecido["documento_formatado"], "123.456.789-09",
          "e o documento formatado")
    igual(enriquecido["cnpj"], CPF,
          "sem substituir o campo antigo — ele continua lá")
    igual(enriquecido["nome"], "Fulano", "e o resto do cadastro intacto")

    secao("1.1 · Pessoa física não precisa de CNAE")
    igual(cad.obrigatorios_de(CPF), ("cnpj", "razao_social"),
          "PF: documento e nome")
    igual(cad.obrigatorios_de(A), ("cnpj", "razao_social", "cnae_principal"),
          "PJ: e CNAE principal")
    sem_cnae = [linha_de(CPF, "Fulano de Tal", **{"CNAE Principal": ""}),
                linha_de(B, "Beta Ltda", **{"CNAE Principal": ""})]
    lin, _ = cad.ler_planilha(csv_de([CABECALHO] + sem_cnae), "pf.csv")
    prev = cad.conciliar([], lin)
    igual(prev["resumo"]["novos"], 1, "a pessoa física entra sem CNAE")
    igual(prev["novos"][0]["razao_social"], "Fulano de Tal", "e é ela mesma")
    igual(prev["resumo"]["erros"], 1, "a pessoa jurídica sem CNAE é recusada")
    ok("CNAE" in prev["erros"][0]["motivo"], "com o motivo dito")

    secao("1.1 · Fórmula é recusada, TENHA OU NÃO resultado gravado")
    com_valor = xlsx_de([CABECALHO, linha_de(A, "Alfa")])
    # injeta uma fórmula COM resultado guardado
    import re as _re
    bruto_zip = io.BytesIO(com_valor)
    novo = io.BytesIO()
    with zipfile.ZipFile(bruto_zip) as zin, zipfile.ZipFile(novo, "w") as zout:
        for item in zin.infolist():
            dados = zin.read(item.filename)
            if item.filename.endswith("sheet1.xml"):
                dados = dados.replace(
                    b'<c r="B2" t="inlineStr"><is><t>Alfa</t></is></c>',
                    b'<c r="B2" t="str"><f>A1</f><v>Alfa</v></c>')
            zout.writestr(item, dados)
    linhas_f, _ = cad.ler_planilha(novo.getvalue(), "f.xlsx")
    ok(any(v == "#FORMULA" for v in linhas_f[0].values()),
       "a fórmula COM resultado guardado é marcada")
    pf2 = cad.conciliar([], linhas_f)
    igual(pf2["resumo"]["erros"], 1, "e a linha é recusada")
    ok("fórmula" in pf2["erros"][0]["motivo"], "dizendo que há fórmula")
    ok("cole como valor" in pf2["erros"][0]["motivo"],
       "e o que fazer a respeito")

    secao("1.1 · Bomba de descompressão")
    bomba = io.BytesIO()
    with zipfile.ZipFile(bomba, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook/>')
        # 60 MB de zeros comprimem a quase nada: é a bomba clássica.
        z.writestr("xl/worksheets/sheet1.xml", b"0" * (60 * 1024 * 1024))
    ok(len(bomba.getvalue()) < cad.TAMANHO_MAX,
       "o arquivo compactado passa no limite de tamanho (%.0f KB)"
       % (len(bomba.getvalue()) / 1024))
    try:
        cad.ler_planilha(bomba.getvalue(), "b.xlsx")
        ok(False, "a bomba deveria ter sido recusada")
    except cad.PlanilhaInvalida as e:
        ok(True, "mas é recusada ao abrir: %s" % str(e)[:52])

    secao("1.1 · Nada é extraído para o disco")
    fonte_cad = (RAIZ / "fiscale_cadastro.py").read_text(encoding="utf-8")
    arv_cad = ast.parse(fonte_cad)
    chamadas = {getattr(n.func, "attr", "") for n in ast.walk(arv_cad)
                if isinstance(n, ast.Call)}
    for perigo in ("extract", "extractall"):
        ok(perigo not in chamadas,
           "o módulo não chama %r — a planilha nunca toca o disco" % perigo)
    ok("open(" not in fonte_cad.split("def _ler_xlsx")[1].split("def ")[0],
       "e o leitor de xlsx não abre arquivo nenhum")

    secao("1.1 · A rota de inscrições aceita pessoa física")
    principal_txt = (RAIZ / "nfse" / "backend" / "main.py").read_text(
        encoding="utf-8")
    i = principal_txt.index('def clientes_inscricoes')
    j = principal_txt.index("\n@app.", i)
    rota_i = principal_txt[i:j]
    ok("len(c) != 14" not in rota_i,
       "não recusa mais por ter 11 dígitos")
    ok("documento_valido" in rota_i, "valida o documento, seja qual for")
    ok("NÃO APLICÁVEL A CPF" in rota_i,
       "e devolve situação explícita quando o campo não se aplica")
    ok("ie_situacao" in rota_i and "tipo_documento" in rota_i,
       "informando a situação e o tipo")
    ok("eh_pessoa_fisica" in rota_i, "pela pergunta central, não por len()")

    secao("1.1 · O rótulo visível fala dos dois")
    tela_cli = (RAIZ / "web" / "clientes.html").read_text(encoding="utf-8")
    ok("<label>CPF/CNPJ</label>" in tela_cli,
       "o campo do cadastro diz CPF/CNPJ")
    ok("nem todo cliente é empresa" in tela_cli,
       "e a tela explica por quê")
    ok("pessoa física não</b>" in tela_cli,
       "a importação avisa que pessoa física não precisa de CNAE")
    igual(cad.ROTULOS["cnpj"], "CPF/CNPJ",
          "e o rótulo do módulo — que vira o cabeçalho do modelo — também")

    secao("1.1 · O modelo tem as duas naturezas")
    modelo2 = cad.modelo_csv()
    linhas_m2, _ = cad.ler_planilha(modelo2, "m.csv")
    igual(len(linhas_m2), 2, "duas linhas de exemplo")
    prev_m2 = cad.conciliar([], linhas_m2)
    igual(prev_m2["resumo"]["novos"], 2, "as duas importam")
    igual(prev_m2["resumo"]["erros"], 0, "sem erro")
    tipos = sorted(cad.tipo_documento(n["cnpj"]) for n in prev_m2["novos"])
    igual(tipos, ["CNPJ", "CPF"], "uma pessoa jurídica e uma física")
    for cabecalho in ("CPF", "Documento", "CPF/CNPJ", "CNPJ cpf"):
        igual(cad.campo_do_cabecalho(cabecalho), "cnpj",
              "cabeçalho %r é reconhecido" % cabecalho)

    # ── Correções do teste manual ────────────────────────────────────────
    secao("O download do modelo · a resposta HTTP inteira")
    servidor2 = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8")
    i = servidor2.index('if rota == "/api/clientes/modelo":')
    j = servidor2.index('if rota == "', i + 20)
    rota_modelo = servidor2[i:j]
    ok("text/csv" in rota_modelo, "Content-Type é text/csv")
    ok("charset=utf-8" in rota_modelo, "com charset declarado")
    ok("attachment" in rota_modelo and "filename=" in rota_modelo,
       "Content-Disposition manda BAIXAR, com nome de arquivo")
    ok("Content-Length" in rota_modelo, "e informa o tamanho")
    ok("modelo-empresas-fiscale.csv" in rota_modelo, "o nome do arquivo")

    # O DEFEITO QUE ISTO FIXA — a rota existia e nunca era alcançada.
    ok("/api/clientes/modelo" in servidor2.split("NFSE_EXCECOES")[1][:400],
       "e ela está na lista de exceções do proxy, senão vira 404 silencioso")

    corpo = cad.modelo_csv()
    ok(corpo[:3] == bytes((0xEF, 0xBB, 0xBF)), "o CSV começa com BOM")
    texto = corpo.decode("utf-8-sig")
    primeira = texto.splitlines()[0]
    ok(";" in primeira and primeira.count(";") > primeira.count(","),
       "separado por ponto e vírgula, não vírgula")
    ok(primeira.startswith("CPF/CNPJ"), "e a primeira coluna é CPF/CNPJ")

    secao("O modelo baixado importa — em dados isolados")
    isolado = Path(tempfile.mkdtemp(prefix="fiscale_cad_"))
    (isolado / "modelo-baixado.csv").write_bytes(corpo)
    baixado = (isolado / "modelo-baixado.csv").read_bytes()
    igual(baixado, corpo, "o arquivo em disco é byte a byte o que a rota manda")
    linhas_b, avisos_b = cad.ler_planilha(baixado, "modelo-baixado.csv")
    igual(len(linhas_b), 2, "duas linhas")
    igual(avisos_b, [], "nenhuma coluna ignorada")
    prev_b = cad.conciliar([], linhas_b)
    igual(prev_b["resumo"]["erros"], 0, "sem erro")
    novos_b, ap_b = cad.aplicar([], prev_b)
    igual(ap_b["criados"], 2, "e as duas empresas entram num cadastro vazio")
    tipos_b = sorted(cad.tipo_documento(cad.documento_de(e)) for e in novos_b)
    igual(tipos_b, ["CNPJ", "CPF"], "uma PJ e uma PF")
    shutil.rmtree(isolado, ignore_errors=True)

    secao("A tela baixa buscando, e diz o que houve se falhar")
    tela_c = (RAIZ / "web" / "clientes.html").read_text(encoding="utf-8")
    ok("baixarModelo(this)" in tela_c, "o botão chama uma função")
    ok("location.href='/api/clientes/modelo'" not in tela_c,
       "e NÃO navega — navegar tira a pessoa da tela e mostra o JSON do erro")
    i = tela_c.index("async function baixarModelo")
    corpo_btn = tela_c[i:tela_c.index("\n}", i)]
    ok("await fetch" in corpo_btn, "busca o arquivo")
    ok("URL.createObjectURL" in corpo_btn and "download" in corpo_btn,
       "e o oferece como download de verdade")
    ok("try" in corpo_btn and "catch" in corpo_btn, "com try/catch")
    # Esta asserção cobrava `alert(`. O recado passou a aparecer NA TELA, num
    # elemento com `aria-live`: o `alert` some ao clicar em OK, e quem estava
    # lendo o motivo do erro perde a frase. A intenção continua a mesma — dizer
    # o que houve —, só o meio mudou.
    ok("r.ok" in corpo_btn and "_statusModelo(" in corpo_btn,
       "e mostra erro claro quando a resposta não é ok")
    ok("alert(" not in corpo_btn,
       "sem `alert`: a mensagem fica na tela para ser lida com calma")
    ok("revokeObjectURL" in corpo_btn, "soltando a URL temporária depois")

    secao("Pessoa física aparece na lista, com o rótulo certo")
    ok("const rotuloDoc" in tela_c,
       "o rótulo do documento é decidido pelo tamanho, não fixo em CNPJ")
    ok("${rotuloDoc(c.cnpj)}" in tela_c,
       "e a linha da lista usa esse rótulo")
    ok("CNPJ ${c.cnpj||'—'}" not in tela_c,
       "o rótulo fixo 'CNPJ' saiu da lista")
    i = tela_c.index("const fmtDoc")
    corpo_fmt = tela_c[i:tela_c.index("\n};", i)]
    ok("11" in corpo_fmt and "14" in corpo_fmt,
       "a formatação conhece os DOIS tamanhos")
    ok("pessoa física" in tela_c,
       "e a linha marca quem é pessoa física")

    secao("Nenhum teste desta suíte foi à rede")
    try:
        _socket.socket().connect(("127.0.0.1", 9))
        ok(False, "a trava de rede não estava ativa")
    except AssertionError:
        ok(True, "a trava estava mesmo ativa (conferido)")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
