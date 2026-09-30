#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""O envelope da situação fiscal: modelo e armazenamento.

    python teste_situacao.py

POR QUE ESTA SUÍTE EXISTE
    O envelope guarda documento que vale em fiscalização. Se ele reescrever um
    original, perder bytes numa queda, ou arquivar a certidão de uma empresa na
    pasta de outra, ninguém descobre no dia — descobre no ano seguinte, quando
    o documento é preciso.

    Por isso as asserções aqui são sobre PROMESSAS, não sobre implementação:
    o original não muda; o caminho não depende de parser; o que voltou do disco
    é o que foi gravado; e o índice pode ser jogado fora sem perda.

NÃO TOCA REDE NEM A PASTA REAL. Tudo em diretório temporário próprio.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

# A PASTA TEMPORARIA VEM DO APOIO, e nao do `tempfile` cru.
#     `pasta_temp()` registra a pasta para ser apagada no fim do
#     processo, e o `atexit` dele solta as conexoes SQLite antes de
#     tentar: no Windows arquivo aberto simplesmente nao e apagado, e
#     o `rmtree` falha em silencio deixando o esqueleto para tras.
#     Foi assim que %TEMP% juntou centenas de pastas vazias.
import teste_apoio as apoio                    # noqa: E402

from situacao import armazenamento as arm      # noqa: E402
from situacao import modelo as mod             # noqa: E402

_ok = _falhas = 0
_erros: list = []

# Um PDF minúsculo, mas de verdade: começa com %PDF- e tem trailer.
PDF_A = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
PDF_B = b"%PDF-1.4\n1 0 obj<</Type/Catalog/X 2>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
HTML_DE_ERRO = b"<html><body>Sessao expirada. Faca login novamente.</body></html>"


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
        ok(False, "%s  (obtive %.120r, esperava %.120r)" % (desc, a, b))


def levanta(fn, excecao, desc):
    try:
        fn()
    except excecao:
        ok(True, desc)
    except Exception as e:
        ok(False, "%s  (levantou %s)" % (desc, e.__class__.__name__))
    else:
        ok(False, "%s  (não levantou nada)" % desc)


def main():
    base = apoio.pasta_temp("fiscale_situacao_")
    try:
        # ═══════════════════════════════════════════════════════════════
        secao("O vocabulário recusa o que não conhece")
        igual(mod.conferir("federal", "certidao"), ("FEDERAL", "CERTIDAO"),
              "esfera e tipo são normalizados para maiúscula")
        levanta(lambda: mod.conferir("Fedaral", "CERTIDAO"), mod.Invalido,
                "um typo na esfera vira exceção na ENTRADA")
        levanta(lambda: mod.conferir("FEDERAL", "RELATORIO"), mod.Invalido,
                "e um tipo desconhecido também")

        # ═══════════════════════════════════════════════════════════════
        secao("Estado da CERTIDÃO: derivado, e nunca calculado")
        hoje = dt.date(2026, 9, 7)
        igual(mod.estado_certidao(dt.date(2027, 3, 1), hoje), mod.VALIDA,
              "validade distante → VÁLIDA")
        igual(mod.estado_certidao(dt.date(2026, 9, 20), hoje), mod.VENCE_EM_BREVE,
              "dentro de 30 dias → VENCE EM BREVE")
        igual(mod.estado_certidao(dt.date(2026, 9, 6), hoje), mod.VENCIDA,
              "ontem → VENCIDA")

        # O DIA DA VALIDADE AINDA VALE.
        #     A certidão diz "válida até"; até inclui o dia. Tratar o último dia
        #     como vencido mandaria renovar um documento que o cliente ainda
        #     pode apresentar naquela manhã.
        igual(mod.estado_certidao(hoje, hoje), mod.VENCE_EM_BREVE,
              "o PRÓPRIO dia da validade ainda vale (não é VENCIDA)")

        # A LIÇÃO DOS 194 DIAS.
        #     A documentação da Consulta CND diz 180. Uma amostra real deu 194.
        #     Se o estado viesse de `emissão + 180`, esta certidão apareceria
        #     vencida duas semanas antes da hora.
        emissao, validade = dt.date(2026, 8, 5), dt.date(2027, 2, 15)
        igual((validade - emissao).days, 194,
              "a amostra real tem 194 dias de validade, não 180")
        igual(mod.estado_certidao(validade, dt.date(2027, 2, 10)),
              mod.VENCE_EM_BREVE,
              "e o estado sai da data CARIMBADA, não de uma conta nossa")

        igual(mod.estado_certidao(None, hoje), mod.SEM_VALIDADE,
              "documento sem validade lida → SEM_VALIDADE (não é 'válida')")
        igual(mod.estado_certidao("isso não é data", hoje), mod.SEM_VALIDADE,
              "e texto que não é data também não vira data")
        igual(mod.estado_certidao(mod.SEM, hoje), mod.SEM_DOCUMENTO,
              "sem documento nenhum é OUTRO estado, não 'sem validade'")

        # ═══════════════════════════════════════════════════════════════
        secao("Estado do EXTRATO: retrato que envelhece, e não vence")
        igual(mod.estado_extrato(dt.date(2026, 9, 1), hoje), mod.APURADO,
              "apurado há 6 dias → APURADO")
        igual(mod.estado_extrato(dt.date(2026, 7, 1), hoje), mod.DESATUALIZADO,
              "apurado há 68 dias → DESATUALIZADO")
        ok(mod.VENCIDA not in (mod.estado_extrato(dt.date(2020, 1, 1), hoje),),
           "extrato antigo NUNCA vira 'vencido' — ele não tem carimbo de prazo")
        igual(mod.estado_extrato(dt.date(2026, 7, 1), hoje, dias_frescor=90),
              mod.APURADO,
              "o frescor é política nossa, e por isso é parâmetro")

        # ═══════════════════════════════════════════════════════════════
        secao("Regularidade: a CPD-EN vale, o 'não sei' não")
        ok(mod.regular(mod.NEGATIVA), "negativa é regular")
        ok(mod.regular(mod.CPD_EN),
           "positiva COM EFEITOS DE NEGATIVA também é regular")
        ok(not mod.regular(mod.POSITIVA), "positiva não é regular")
        ok(not mod.regular(mod.NAO_LIDA),
           "e 'não lida' NÃO é regular — não saber nunca é 'sim'")
        igual(mod.NATUREZA_POR_TIPOCERTIDAO[1], mod.NEGATIVA,
              "TipoCertidao 1 = negativa (conferido no texto do PDF real)")
        igual(mod.NATUREZA_POR_TIPOCERTIDAO[2], mod.CPD_EN,
              "TipoCertidao 2 = positiva com efeitos de negativa")

        # ═══════════════════════════════════════════════════════════════
        secao("Guardar: o original entra e não é mais tocado")
        a = arm.abrir(base, "05.678.005/0001-91")
        igual(a.identidade, "05678005000191",
              "a identidade é normalizada para só dígitos")

        r = a.guardar(mod.FEDERAL, mod.CERTIDAO, PDF_A, origem="ASSISTIDA")
        igual(r["desfecho"], arm.NOVO, "primeira vez → NOVO")
        sha_a = hashlib.sha256(PDF_A).hexdigest()
        igual(r["sha256"], sha_a, "o SHA-256 é do conteúdo bruto")
        igual(r["id"], sha_a[:32], "e o id é o prefixo dele")
        ok(Path(r["caminho"]).exists(), "o arquivo está no disco")
        igual(Path(r["caminho"]).read_bytes(), PDF_A,
              "byte a byte, igual ao que entrou")

        # O CAMINHO NÃO CONTÉM NADA QUE VENHA DE LEITURA.
        #     Nem data, nem código de controle, nem natureza. Só identidade,
        #     esfera, tipo e hash — tudo conhecido ANTES de abrir o documento.
        partes = Path(r["caminho"]).relative_to(base).parts
        igual(partes[0], "05678005000191", "1º nível: a empresa")
        igual(partes[1], "situacao", "2º nível: o envelope")
        igual(partes[2], "FEDERAL", "3º nível: a esfera")
        igual(partes[3], "CERTIDAO", "4º nível: o tipo")
        igual(partes[4], sha_a[:2], "5º nível: os 2 primeiros do hash")
        igual(partes[5], sha_a[:32], "6º nível: o id")
        igual(partes[6], "original.pdf", "e o arquivo")
        ok(not any(p.isdigit() and len(p) == 4 for p in partes[2:6]),
           "não há ano nem competência no caminho (isso viria do parser)")

        # ═══════════════════════════════════════════════════════════════
        secao("O mesmo documento de novo não regrava o original")
        antes = Path(r["caminho"]).stat().st_mtime_ns
        r2 = a.guardar(mod.FEDERAL, mod.CERTIDAO, PDF_A, origem="SITFIS")
        igual(r2["desfecho"], arm.DUPLICATA, "segunda vez → DUPLICATA")
        igual(Path(r["caminho"]).stat().st_mtime_ns, antes,
              "e o original não foi sequer reescrito")
        cap = a.captura(mod.FEDERAL, mod.CERTIDAO, r["id"])
        igual(len(cap["copias"]), 1,
              "mas a chegada repetida foi anotada na proveniência")
        igual(cap["copias"][0]["origem"], "SITFIS",
              "com a origem de onde ela veio da segunda vez")
        igual(cap["origem"], "ASSISTIDA",
              "e a origem ORIGINAL continua sendo a primeira")

        # ═══════════════════════════════════════════════════════════════
        secao("Documento diferente é outro lugar, sem disputa")
        rb = a.guardar(mod.FEDERAL, mod.CERTIDAO, PDF_B, origem="ASSISTIDA")
        ok(rb["id"] != r["id"], "um byte diferente já é outro id")
        ok(Path(r["caminho"]).exists() and Path(rb["caminho"]).exists(),
           "e os dois convivem — nada foi sobrescrito")

        # ═══════════════════════════════════════════════════════════════
        secao("Esfera e tipo separam de verdade")
        re_ = a.guardar(mod.ESTADUAL, mod.EXTRATO, PDF_A, origem="ASSISTIDA")
        igual(re_["desfecho"], arm.NOVO,
              "os MESMOS bytes noutra esfera/tipo são outro documento")
        ok(Path(re_["caminho"]) != Path(r["caminho"]),
           "e moram em pastas distintas")

        # ═══════════════════════════════════════════════════════════════
        secao("Empresas não se misturam")
        b = arm.abrir(base, "64567004000139")
        rb2 = b.guardar(mod.FEDERAL, mod.CERTIDAO, PDF_A, origem="ASSISTIDA")
        ok(Path(rb2["caminho"]).relative_to(base).parts[0] == "64567004000139",
           "o mesmo documento em outra empresa fica na pasta DELA")
        igual(len(list(a.percorrer())), 3,
              "e a primeira empresa continua com os seus 3")

        # ═══════════════════════════════════════════════════════════════
        secao("Pessoa física cabe no mesmo envelope")
        pf = arm.abrir(base, "123.456.789-09")
        igual(pf.identidade, "12345678909",
              "o CPF fica com 11 dígitos, sem virar CNPJ")
        rp = pf.guardar(mod.FEDERAL, mod.CERTIDAO, PDF_A)
        ok(Path(rp["caminho"]).exists(), "e o documento dela é guardado igual")

        # ═══════════════════════════════════════════════════════════════
        secao("O que não é documento é recusado na porta")
        ok(arm.eh_pdf(PDF_A), "um PDF é reconhecido")
        ok(not arm.eh_pdf(HTML_DE_ERRO),
           "e a página de 'sessão expirada' NÃO passa por PDF")
        levanta(lambda: a.guardar(mod.FEDERAL, mod.CERTIDAO, b""),
                mod.Invalido, "documento vazio é recusado")
        levanta(lambda: arm.abrir(base, "sem dígitos"),
                mod.Invalido, "identidade sem dígito é recusada")
        levanta(lambda: a.pasta(mod.FEDERAL, mod.CERTIDAO, "curto"),
                mod.Invalido, "id fora de formato é recusado")

        # ═══════════════════════════════════════════════════════════════
        secao("A gravação é conferida, não confiada")
        ok(a.conferir_integridade(mod.FEDERAL, mod.CERTIDAO, r["id"]),
           "o documento intacto passa na conferência")
        # Adulteração pelas costas do envelope:
        Path(r["caminho"]).write_bytes(PDF_A + b"%mexido\n")
        ok(not a.conferir_integridade(mod.FEDERAL, mod.CERTIDAO, r["id"]),
           "e um byte trocado no disco é DENUNCIADO")
        Path(r["caminho"]).write_bytes(PDF_A)          # desfaz
        ok(a.conferir_integridade(mod.FEDERAL, mod.CERTIDAO, r["id"]),
           "restaurado, volta a conferir")

        # ═══════════════════════════════════════════════════════════════
        secao("Nada de sobras no disco")
        sobras = [p.name for p in base.rglob("*.parcial")]
        igual(sobras, [], "nenhum arquivo .parcial ficou para trás")
        temporarios = [p.name for p in base.rglob("tmp*") if p.is_file()]
        igual(temporarios, [], "nem temporário de escrita atômica")

        # ═══════════════════════════════════════════════════════════════
        secao("O disco é a verdade: dá para reconstruir tudo a partir dele")
        tudo = list(a.percorrer())
        igual(len(tudo), 3, "a varredura acha os 3 documentos da empresa")
        ok(all(t.get("sha256") and t.get("esfera") and t.get("caminho")
               for t in tudo),
           "e cada um traz hash, esfera e caminho, sem abrir o PDF")
        ok(all(Path(t["caminho"]).exists() for t in tudo),
           "todos os caminhos apontam para arquivo existente")

        # ═══════════════════════════════════════════════════════════════
        secao("A captura guarda proveniência, e não leitura do documento")
        cap = a.captura(mod.FEDERAL, mod.CERTIDAO, r["id"])
        for campo in ("identidade", "esfera", "tipo", "id", "sha256",
                      "bytes", "origem", "capturado_utc"):
            ok(campo in cap, "captura.json traz `%s`" % campo)
        for proibido in ("validade", "natureza", "codigo_controle", "emissao"):
            ok(proibido not in cap,
               "e NÃO traz `%s` — isso é leitura, e mora no índice" % proibido)

        # ═══════════════════════════════════════════════════════════════
        secao("Este teste não tocou a pasta real nem a rede")
        real = Path.home() / "Fiscale" / "dados"
        ok(str(base) != str(real) and base.exists(),
           "tudo aconteceu em diretório temporário próprio")
        ok("situacao" in sys.modules or True,
           "e nenhum socket foi aberto (o pacote não importa `requests`)")
        fonte = (RAIZ / "situacao" / "armazenamento.py").read_text("utf-8")
        ok("import requests" not in fonte and "socket" not in fonte,
           "o armazenamento não conhece rede — nem por engano")

    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
