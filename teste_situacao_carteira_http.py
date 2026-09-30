#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A central de regularidade FEDERAL pela rede — carteira, detalhe, visor e auditoria.

    python teste_situacao_carteira_http.py

POR QUE ESTE TESTE EXISTE, E POR QUE ELE SOBE UM SERVIDOR
    As rotas desta suíte já tiveram, em outras fases, o defeito no CAMINHO até
    o handler (prefixo repassado ao proxy, bloco aninhado no `if` errado) —
    e chamar a função direto passava verde. Aqui o pedido percorre o servidor
    de verdade, com sessão, papel e cabeçalhos.

O QUE ELE PROTEGE
    • carteira e detalhe exigem sessão; o operador lê e CLASSIFICA, mas não
      guarda documento nem consulta o órgão;
    • o universo é o CADASTRO, e a avaliação é SÓ FEDERAL: nenhuma linha traz
      esfera municipal nem Inscrição Mercantil (escopo retirado em 12/09/2026);
    • consultar a federal SEM credencial registra a recusa — com o usuário —
      e a empresa NÃO fica regular;
    • qualquer esfera que não seja a federal é recusada na consulta;
    • um documento MUNICIPAL antigo no envelope não pesa na carteira;
    • o PDF guardado é servido com páginas e hash, e "baixar original"
      entrega os MESMOS bytes;
    • classificar grava autor e muda o status; classificação inválida é 400;
    • a tela chama as rotas novas, tem o visualizador, e NÃO tem bandeja nem
      atalho de portal;
    • o cartão MUNICIPAL existe (escopo reaberto em 27/09/2026) como canal
      assistido — sem consulta automática, porque o portal exige CAPTCHA.

O QUE ELE NÃO FAZ
    Não toca no Fiscale de produção, não vai à Receita, não usa certificado.
    Instância, porta e pasta próprias; derruba a árvore no fim.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_situacao_http as base                    # noqa: E402

_ok = _falhas = 0
_erros: list = []

SENHA = "carteira de caju no quintal"                # inventada aqui
ALFA = "11222333000181"
BETA = "66789006000106"
GAMA = "45997418000153"                              # fora do cadastro


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
    ok(a == b, desc if a == b else "%s  (obtive %.160r, esperava %.160r)"
       % (desc, a, b))


def main():
    inst = base.Instancia()
    try:
        secao("Uma instância isolada")
        ok(inst.subir(), "o Fiscale subiu na porta %d" % inst.porta)

        secao("1 · Sem sessão, nada")
        for rota in ("/api/situacao/carteira", "/api/situacao/empresa?identidade=" + ALFA):
            s, _, _ = inst.pedir("GET", rota)
            igual(s, 401, "%s sem sessão -> 401" % rota.split("?")[0])

        s, _ = inst.json("POST", "/api/primeiro-acesso", {"senha": SENHA})
        igual(s, 200, "admin de teste criado")
        inst.cookie = ""
        s, _ = inst.json("POST", "/api/login", {"usuario": "admin", "senha": SENHA})
        igual(s, 200, "login do admin")

        # O cadastro de Clientes, no formato em que o módulo o grava — com
        # município e inscrição, que a carteira agora IGNORA.
        (inst.dados / "state_clientes.json").write_text(json.dumps({"clientes": [
            {"id": "c1", "nome": "ALFA COMERCIO LTDA", "cnpj": "11.222.333/0001-81",
             "mun": "RECIFE", "uf": "PE", "im": "473.900-0"},
            {"id": "c2", "nome": "BETA SERVICOS LTDA", "cnpj": "66.789.006/0001-06",
             "mun": "OLINDA", "uf": "PE", "im": "1065661"},
        ]}, ensure_ascii=False), encoding="utf-8")

        secao("2 · A carteira nasce honesta e só federal")
        s, _, d = inst.pedir("GET", "/api/situacao/carteira")
        igual(s, 200, "GET carteira -> 200")
        c = json.loads(d)
        linhas = {l["identidade"]: l for l in c["empresas"]}
        igual(sorted(linhas), sorted([ALFA, BETA]), "o universo é o cadastro")
        for ident in (ALFA, BETA):
            ok("municipal" not in linhas[ident] and "im" not in linhas[ident],
               "%s: sem esfera municipal e sem inscrição na linha" % ident[:8])
        igual(linhas[ALFA]["federal"]["status"], "NAO_CONSULTADO", "ALFA não consultada")
        igual(linhas[ALFA]["geral"]["status"], "NAO_CONSULTADO", "consolidado idem")
        igual(c["contadores"]["regulares"], 0, "zero regulares")
        igual(c["contadores"]["nao_consultadas"], 2, "duas não consultadas")
        igual(c["canal_federal"]["automatico"], False,
              "sem cofre do Integra Contador, federal não é automática")

        secao("3 · Só a esfera federal é consultável")
        for esfera in ("MUNICIPAL", "ESTADUAL"):
            s, r = inst.json("POST", "/api/situacao/consultar",
                             {"identidade": ALFA, "esfera": esfera})
            igual(s, 400, "POST consultar %s -> 400" % esfera)
            ok("somente na esfera federal" in r.get("erro", ""),
               "dizendo que a central é só federal: %r" % r.get("erro", "")[:60])
        s, r = inst.json("POST", "/api/situacao/consultar",
                         {"identidade": GAMA, "esfera": "FEDERAL"})
        igual(s, 404, "empresa fora do cadastro -> 404")

        secao("4 · Federal sem credencial: a recusa fica registrada, e NÃO vira regular")
        s, r = inst.json("POST", "/api/situacao/consultar",
                         {"identidade": ALFA, "esfera": "FEDERAL"})
        igual(s, 200, "POST consultar FEDERAL -> 200 (a rota respondeu)")
        igual(r["resultado"]["desfecho"], "RECUSADA", "desfecho RECUSADA")
        igual(r["resultado"]["guardado"], False, "nada guardado")
        s, _, d = inst.pedir("GET", "/api/situacao/empresa?identidade=" + ALFA)
        e = json.loads(d)
        igual(e["federal"]["status"], "ACESSO_REQUER_REPRESENTACAO",
              "sem procurador configurado -> REQUER REPRESENTAÇÃO")
        igual(e["geral"]["status"], "CONSULTA_NECESSARIA", "consolidado: consulta necessária")
        cons = [h for h in e["historico"] if h["evento"] == "CONSULTA"]
        igual(cons[0]["usuario"], "admin", "a tentativa registra QUEM pediu")
        igual(cons[0]["metodo"], "INTEGRACAO", "e o método")

        secao("5 · Relatório federal guardado: origem, usuário, método")
        pdf = base.pdf_de("RELATORIO DE SITUACAO FISCAL (TESTE)", 2)
        corpo, tipo = base.multipart(
            {"identidade": BETA, "esfera": "FEDERAL", "tipo": "EXTRATO",
             "origem": "ASSISTIDA_ECAC"},
            ("arquivo", "sitfis.pdf", pdf, "application/pdf"))
        s, _, d = inst.pedir("POST", "/api/situacao/importar", corpo, tipo)
        igual(s, 200, "POST importar -> 200")
        doc = json.loads(d)["documento"]
        igual(doc["origem"], "ASSISTIDA_ECAC", "origem do canal preservada")
        s, _, d = inst.pedir("GET", "/api/situacao/empresa?identidade=" + BETA)
        e = json.loads(d)
        igual(e["federal"]["status"], "ACESSO_REQUER_INTERACAO_DO_USUARIO",
              "PDF que o leitor não entende -> REQUER INTERAÇÃO (não regular)")
        up = [h for h in e["historico"] if h.get("metodo") == "UPLOAD"][0]
        igual(up["usuario"], "admin", "upload com usuário")
        igual(up["sha256"], doc["sha256"], "e com o hash")
        igual(len(e["documentos"]), 1, "o documento aparece na lista")

        secao("6 · Documento MUNICIPAL antigo no envelope não pesa")
        corpo, tipo = base.multipart(
            {"identidade": ALFA, "esfera": "MUNICIPAL", "tipo": "EXTRATO"},
            ("arquivo", "antigo.pdf", base.pdf_de("EXTRATO MUNICIPAL ANTIGO", 1),
             "application/pdf"))
        s, _, _ = inst.pedir("POST", "/api/situacao/importar", corpo, tipo)
        igual(s, 200, "o envelope ainda aceita (é o mesmo portão)")
        s, _, d = inst.pedir("GET", "/api/situacao/empresa?identidade=" + ALFA)
        e = json.loads(d)
        igual(e["documentos"], [], "mas o detalhe só lista documentos federais")
        ok(all(h["esfera"] == "FEDERAL" for h in e["historico"]),
           "e o histórico só traz eventos federais")
        igual(e["geral"]["status"], "CONSULTA_NECESSARIA",
              "o status continua sendo o da federal")

        secao("7 · O visor: páginas, hash, e o original intacto no download")
        url = ("/api/situacao/documento?identidade=%s&esfera=FEDERAL&tipo=EXTRATO&id=%s"
               % (BETA, doc["id"]))
        s, h, bruto = inst.pedir("GET", url)
        igual(s, 200, "GET documento -> 200")
        igual(h.get("x-paginas"), "2", "X-Paginas: 2 — o visor sabe até onde vai")
        igual(h.get("x-sha256"), doc["sha256"], "X-Sha256 é o do registro")
        ok(h.get("content-disposition", "").startswith("inline"), "sem baixar: inline")
        igual(bruto, pdf, "os bytes são exatamente os enviados")
        s, h, bruto2 = inst.pedir("GET", url + "&baixar=1")
        ok(h.get("content-disposition", "").startswith("attachment"),
           "com baixar=1: attachment")
        igual(hashlib.sha256(bruto2).hexdigest(), doc["sha256"],
              "o 'baixar original' confere com o hash")

        secao("8 · Classificar: autor, pendências, e o status muda")
        s, r = inst.json("POST", "/api/situacao/classificar",
                         {"identidade": BETA, "esfera": "FEDERAL", "tipo": "EXTRATO",
                          "documento_id": doc["id"], "status": "INVENTADO"})
        igual(s, 400, "status fora do vocabulário -> 400")
        s, r = inst.json("POST", "/api/situacao/classificar",
                         {"identidade": BETA, "esfera": "FEDERAL", "tipo": "EXTRATO",
                          "documento_id": doc["id"], "status": "PENDENCIA",
                          "valor_total": "812,40",
                          "pendencias": [{"tributo": "IRPJ", "competencia": "06/2026",
                                          "total": "812,40", "situacao": "em aberto"}]})
        igual(s, 200, "POST classificar -> 200")
        igual(r["interpretacao"]["usuario"], "admin", "gravada com o autor da sessão")
        s, _, d = inst.pedir("GET", "/api/situacao/carteira")
        l = {x["identidade"]: x for x in json.loads(d)["empresas"]}[BETA]
        igual(l["federal"]["status"], "PENDENCIA", "federal agora PENDÊNCIA")
        igual(l["federal"]["valor_total"], 812.4, "com o valor")
        igual(l["geral"]["status"], "COM_PENDENCIA", "consolidado: COM PENDÊNCIA")
        s, _, bruto3 = inst.pedir("GET", url)
        igual(bruto3, pdf, "o PDF continua o mesmo depois de classificado")

        secao("9 · O operador lê e classifica; não consulta nem guarda")
        s, r = inst.json("POST", "/api/usuarios",
                         {"usuario": "aline", "senha": SENHA, "admin": False,
                          "nome": "Aline", "email": "aline@exemplo.com.br"})
        igual(s, 200, "operador criado: %r" % (r.get("erro") or "ok"))
        inst.cookie = ""
        s, _ = inst.json("POST", "/api/login", {"usuario": "aline", "senha": SENHA})
        igual(s, 200, "login do operador")
        s, _, _ = inst.pedir("GET", "/api/situacao/carteira")
        igual(s, 200, "operador GET carteira -> 200")
        s, _, _ = inst.pedir("GET", "/api/situacao/empresa?identidade=" + BETA)
        igual(s, 200, "operador GET empresa -> 200")
        s, r = inst.json("POST", "/api/situacao/classificar",
                         {"identidade": BETA, "esfera": "FEDERAL", "tipo": "EXTRATO",
                          "documento_id": doc["id"], "status": "REGULAR",
                          "observacao": "debito pago"})
        igual(s, 200, "operador POST classificar -> 200")
        igual((r.get("interpretacao") or {}).get("usuario"), "aline",
              "gravado com o nome do OPERADOR, não do admin")
        s, _, d = inst.pedir("GET", "/api/situacao/empresa?identidade=" + BETA)
        e = json.loads(d)
        igual(e["federal"]["status"], "REGULAR", "a reclassificação dela vale")
        classif = [h for h in e["historico"] if h["evento"] == "CLASSIFICACAO"]
        igual([h["usuario"] for h in classif], ["aline", "admin"],
              "e a do admin continua no histórico, abaixo")
        s, _, bruto4 = inst.pedir("GET", url)
        igual(bruto4, pdf, "o PDF segue intacto")
        corpo2, tipo2 = base.multipart(
            {"identidade": BETA, "esfera": "FEDERAL", "tipo": "CERTIDAO"},
            ("arquivo", "c.pdf", base.pdf_de("CERTIDAO", 1), "application/pdf"))
        s, _, _ = inst.pedir("POST", "/api/situacao/importar", corpo2, tipo2)
        igual(s, 403, "operador POST importar -> 403")
        s, _ = inst.json("POST", "/api/situacao/consultar",
                         {"identidade": BETA, "esfera": "FEDERAL"})
        igual(s, 403, "operador POST consultar -> 403")

        secao("10 · A tela é só federal")
        s, _, html = inst.pedir("GET", "/situacao.html")
        t = html.decode("utf-8", "replace")
        for trecho, desc in (
                ("/api/situacao/carteira", "chama a carteira"),
                ("/api/situacao/empresa", "chama o detalhe"),
                ("/api/situacao/classificar", "chama a classificação"),
                ("/api/situacao/consultar", "chama a consulta"),
                ("Página anterior", "visor: página anterior"),
                ("Próxima página", "visor: próxima página"),
                ("Zoom", "visor: zoom"),
                ("Baixar original", "visor: baixar original"),
                ("X-Paginas", "lê as páginas do cabeçalho"),
                ("Consultar situação fiscal", "botão do lote"),
                ("Relatório do Escritório", "o dossiê do escritório continua")):
            ok(trecho in t, desc)
        for trecho, desc in (
                ("recifeemdia", "nenhum atalho do portal do Recife"),
                ("/api/situacao/bandeja", "nenhuma chamada à bandeja"),
                ("/api/situacao/expectativa", "nenhuma expectativa declarada"),
                ("Inscrição Mercantil", "nenhuma Inscrição Mercantil"),
                ("clipboard", "nenhuma cópia para a área de transferência")):
            ok(trecho not in t, desc)

        # O MUNICIPAL VOLTOU AO ESCOPO — como canal ASSISTIDO, e só isso.
        #     Até 27/09/2026 este bloco exigia a AUSÊNCIA do cartão municipal:
        #     era o escopo retirado em 12/09. A decisão mudou, então o guarda
        #     mudou de lado — sem afrouxar. O que ele protege agora é que a
        #     esfera municipal nunca finja ter automação que não existe.
        ok("Municipal — Recife" in t, "a tela tem o cartão municipal")
        ok("ASSISTIDA_RECIFE" in t,
           "e guarda o PDF com a origem do canal assistido")
        ok("consultarMunicipal" not in t,
           "sem função de consulta municipal — o portal exige CAPTCHA")
        ok("consultarFederal(" in t,
           "a única consulta automática da tela continua sendo a federal")
    finally:
        inst.derrubar()

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
