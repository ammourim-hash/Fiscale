#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da NF-e 6B — salvar em pasta, e o documento que só existe como resumo.

    python teste_nfe6b.py

O QUE ESTA SUÍTE PROVA
    1. Que "salvar em pasta" grava o XML **original, byte a byte** — o mesmo
       arquivo que o ZIP entrega, provado por SHA-256 dos dois lados.
    2. Que a pasta de destino é **recusada** quando é o acervo, a área de
       dados do Fiscale, uma pasta de sistema, ou um caminho que escapa.
    3. Que **nada é apagado**: arquivo alheio na pasta continua lá depois.
    4. Que o documento sem XML completo **não some** — sai no relatório com
       `XML_COMPLETO_INDISPONIVEL`, e é achável pelo filtro.
    5. Que ZIP e pasta selecionam **exatamente** os mesmos documentos.
    6. Que nenhum XML é inventado a partir de um resumo.
    7. Que nada disso toca rede ou acervo.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402
from ingestao import acervo as acv                   # noqa: E402
from ingestao import consulta as cq                  # noqa: E402
from ingestao import exportacao as exp               # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                             # noqa: E402


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
    ok(a == b, desc if a == b else desc + "  (obtive %r, esperava %r)" % (a, b))


def recusa(f, desc, trecho=""):
    """A recusa precisa vir com motivo legível — erro mudo é erro que o
    usuário repete."""
    try:
        f()
    except exp.PastaInsegura as e:
        texto = str(e)
        bom = (not trecho) or (trecho.lower() in texto.lower())
        ok(bom, desc if bom else desc + " — motivo foi %r" % texto)
    except Exception as e:
        ok(False, desc + " — levantou %s: %s" % (type(e).__name__, e))
    else:
        ok(False, desc + " — NÃO recusou")


# De `teste_apoio_nfe6`, NÃO de `teste_nfe6`: aquele tem as asserções no
# nível do módulo, e importá-lo reexecutava as 312 dele antes destas.
from teste_apoio_nfe6 import montar, carregar_main, CONTA, Req  # noqa: E402
from teste_fixturas_nfe import EMPRESA_A             # noqa: E402


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main() -> int:
    with apoio.raiz_temporaria("fiscale_nfe6b_") as raiz:
        ch = montar(raiz)
        fora = apoio.registrar_para_apagar(
            apoio.pasta_temp("fiscale_nfe6b_saida_"))

        def plano_de(**kw):
            return exp.planejar(raiz, EMPRESA_A, cq.Filtro(**kw))

        secao("O cenário")
        plano = plano_de()
        igual(plano.total, 5, "o resultado tem os 5 documentos")
        igual(len(plano.incluidos), 4, "4 têm XML completo")
        igual(plano.por_motivo().get(exp.XML_COMPLETO_INDISPONIVEL), 1,
              "1 é somente resumo, e o motivo é esse")
        ok(plano.reconciliacao()["confere"], "a soma fecha: 5 = 4 + 1")

        secao("Salvar XMLs em pasta — pasta única")
        destino = fora / "unica"
        r = exp.escrever_em_pasta(destino, raiz, EMPRESA_A, plano_de(),
                                  organizacao=exp.ORG_UNICA,
                                  com_relatorio=True)
        igual(r["xmls_escritos"], 4, "gravou os 4 XML completos")
        igual(r["destino"], "SERVIDOR",
              "o resultado diz que gravou no SERVIDOR, não no dispositivo")
        xmls = sorted(destino.glob("*.xml"))
        igual(len(xmls), 4, "os 4 arquivos estão soltos na raiz da pasta")
        ok(all(len(x.stem) == 44 for x in xmls),
           "o nome de cada arquivo é a chave de acesso de 44 dígitos")
        ok((destino / exp.NOME_RELATORIO).is_file(),
           "o RELATORIO.csv acompanha quando pedido")

        secao("O arquivo gravado é o original")
        ac = acv.abrir(raiz, EMPRESA_A)
        iguais = 0
        for linha in plano_de().incluidos:
            origem = ac.caminho_canonico(linha.especie, linha.id_documento)
            copia = destino / linha.nome_no_zip
            if copia.is_file() and sha(origem) == sha(copia):
                iguais += 1
        igual(iguais, 4, "os 4 batem por SHA-256 com o acervo")

        secao("ZIP x pasta")
        buf = io.BytesIO()
        exp.escrever_zip(buf, raiz, EMPRESA_A, plano_de())
        buf.seek(0)
        with zipfile.ZipFile(buf) as z:
            no_zip = set(Path(n).name for n in z.namelist()
                         if n.endswith(".xml"))
        na_pasta = set(x.name for x in destino.glob("*.xml"))
        igual(no_zip, na_pasta, "os dois lotes têm exatamente os mesmos XML")

        secao("Organização em árvore")
        arv = fora / "arvore"
        r2 = exp.escrever_em_pasta(arv, raiz, EMPRESA_A, plano_de(),
                                   organizacao=exp.ORG_EMPRESA_ANO_MES_PAPEL,
                                   empresa="TRANSPORTADORA TRX / LTDA")
        igual(r2["xmls_escritos"], 4, "gravou os mesmos 4")
        profundos = list(arv.rglob("*.xml"))
        igual(len(profundos), 4, "os 4 estão na árvore")
        ok(all(len(x.relative_to(arv).parts) == 5 for x in profundos),
           "cada um em empresa/ano/mes/papel/arquivo.xml")
        pastas_topo = [x for x in arv.iterdir() if x.is_dir()]
        ok(len(pastas_topo) == 1,
           "o nome da empresa virou UMA pasta (a barra foi sanitizada)")

        secao("Nada é apagado")
        alheio = destino / "NAO_MEXER.txt"
        alheio.write_text("arquivo do usuario", encoding="utf-8")
        exp.escrever_em_pasta(destino, raiz, EMPRESA_A, plano_de(),
                              organizacao=exp.ORG_UNICA)
        ok(alheio.is_file()
           and alheio.read_text(encoding="utf-8") == "arquivo do usuario",
           "arquivo alheio continua intacto")
        antes = dict((x.name, sha(x)) for x in destino.glob("*.xml"))
        exp.escrever_em_pasta(destino, raiz, EMPRESA_A, plano_de(),
                              organizacao=exp.ORG_UNICA)
        depois = dict((x.name, sha(x)) for x in destino.glob("*.xml"))
        igual(depois, antes, "reexportar não altera conteúdo nenhum")

        secao("Relatório opcional")
        so_xml = fora / "so_xml"
        exp.escrever_em_pasta(so_xml, raiz, EMPRESA_A, plano_de(),
                              organizacao=exp.ORG_UNICA, com_relatorio=False)
        ok(not (so_xml / exp.NOME_RELATORIO).exists(),
           "sem CSV quando o usuário pede só os XML")
        igual(len(list(so_xml.glob("*.xml"))), 4, "e os XML estão lá")

        secao("Pastas que precisam ser recusadas")
        recusa(lambda: exp.validar_pasta_destino("", raiz, EMPRESA_A),
               "vazia é recusada", "informe")
        recusa(lambda: exp.validar_pasta_destino("exportacoes", raiz,
                                                 EMPRESA_A),
               "caminho relativo é recusado", "caminho completo")
        recusa(lambda: exp.validar_pasta_destino(raiz, raiz, EMPRESA_A),
               "a raiz de dados do Fiscale é recusada", "dados do Fiscale")
        recusa(lambda: exp.validar_pasta_destino(Path(raiz) / "certs", raiz,
                                                 EMPRESA_A),
               "a pasta de certificados é recusada", "dados do Fiscale")
        recusa(lambda: exp.validar_pasta_destino(ac.raiz(), raiz, EMPRESA_A),
               "o acervo da empresa é recusado")
        recusa(lambda: exp.validar_pasta_destino("C:" + chr(92) + "Windows"
                                                 + chr(92) + "System32",
                                                 raiz, EMPRESA_A),
               "pasta de sistema é recusada", "sistema")
        # Pasta nova em disco bom é o caso NORMAL da primeira exportação:
        # a árvore nasce na hora. O que não pode nascer é numa unidade que
        # não está lá — esse é o erro que se manifesta como "exportei e não
        # achei nada".
        nova = exp.validar_pasta_destino(
            str(fora / "nao" / "existe" / "ainda"), raiz, EMPRESA_A)
        ok(not Path(nova).exists(),
           "pasta ainda inexistente em disco bom é ACEITA (será criada)")
        recusa(lambda: exp.validar_pasta_destino(
                   chr(92) * 2 + "servidor-que-nao-existe" + chr(92)
                   + "compartilhamento" + chr(92) + "xml", raiz, EMPRESA_A)
               if os.name == "nt" else
               exp.validar_pasta_destino("/nao/existe/unidade", raiz,
                                         EMPRESA_A),
               "unidade/servidor inacessível é recusado")
        arq = fora / "um_arquivo.txt"
        arq.write_text("x", encoding="utf-8")
        recusa(lambda: exp.validar_pasta_destino(arq, raiz, EMPRESA_A),
               "caminho que é arquivo é recusado", "é um arquivo")

        secao("Path traversal")
        aprovada = exp.validar_pasta_destino(
            str(fora / "sub" / ".." / "resolvida"), raiz, EMPRESA_A)
        ok(".." not in str(aprovada), "o '..' foi resolvido, não repassado")
        recusa(lambda: exp.validar_pasta_destino(
                   str(Path(raiz) / "x" / ".." / "certs"), raiz, EMPRESA_A),
               "'..' que volta para dentro dos dados é recusado")

        secao("O documento que só existe como resumo")
        so_resumo = plano_de(conteudo=cq.RESUMO)
        igual(so_resumo.total, 1, "o filtro Somente resumo acha exatamente 1")
        igual(so_resumo.linhas[0].chave, ch["resumo"],
              "e é o documento certo")
        igual(len(so_resumo.incluidos), 0, "ele não entra em pacote nenhum")
        completos = plano_de(conteudo=cq.COMPLETO)
        igual(completos.total, 4, "o filtro XML completo acha os outros 4")
        igual(so_resumo.total + completos.total, plano.total,
              "resumo + completo = total: ninguém some da listagem")
        csv = exp.relatorio_csv(plano_de()).decode("utf-8-sig")
        ok(ch["resumo"] in csv, "o resumo APARECE no relatório")
        ok(exp.XML_COMPLETO_INDISPONIVEL in csv,
           "com o motivo XML_COMPLETO_INDISPONIVEL")

        secao("Nenhum XML inventado")
        nomes = set(x.name for x in destino.glob("*.xml"))
        ok(ch["resumo"] + ".xml" not in nomes,
           "não existe arquivo com a chave do documento-resumo")

        secao("O acervo continua intacto")
        assinaturas = dict((str(p), sha(p))
                           for p in Path(ac.raiz()).rglob("*") if p.is_file())
        exp.escrever_em_pasta(fora / "outra", raiz, EMPRESA_A, plano_de(),
                              organizacao=exp.ORG_UNICA)
        depois_ac = dict((str(p), sha(p))
                         for p in Path(ac.raiz()).rglob("*") if p.is_file())
        igual(depois_ac, assinaturas, "nenhum arquivo do acervo mudou")


        # ── as rotas ────────────────────────────────────────────────────

        secao("Organização por empresa — agora é opção, não padrão")
        # Desde o ajuste de UX da 6B o padrão é gravar DIRETO na pasta
        # escolhida (`ORG_UNICA`). A subpasta por empresa continua existindo
        # para quem arquiva, mas só quando pedida.
        emp = fora / "por_empresa"
        NOME = "TRANSPORTADORA TRX / LOGISTICAS LTDA"
        igual(exp.ORGANIZACAO_PADRAO, exp.ORG_UNICA,
              "o padrão é a pasta escolhida, sem subpasta")
        re_ = exp.escrever_em_pasta(emp, raiz, EMPRESA_A, plano_de(),
                                    organizacao=exp.ORG_EMPRESA, empresa=NOME,
                                    com_relatorio=True)
        igual(re_["organizacao"], exp.ORG_EMPRESA,
              "pedida explicitamente, a organização é por empresa")
        pastas = [x for x in emp.iterdir() if x.is_dir()]
        igual(len(pastas), 1, "nasceu UMA pasta (a barra do nome foi sanitizada)")
        xs = list(pastas[0].glob("*.xml"))
        igual(len(xs), 4, "e os 4 XML estão soltos DENTRO dela")
        ok(all(len(x.relative_to(emp).parts) == 2 for x in xs),
           "um nível só: <base>/<empresa>/<chave>.xml — sem ano/mês/papel")
        ok(all(f.suffix == ".xml" for f in pastas[0].iterdir()),
           "nada além dos XML dentro da pasta da empresa")
        ok(any(f.name.startswith("RELATORIO-") for f in emp.iterdir()),
           "o relatório fica na pasta-base e leva o nome da empresa")

        secao("Nunca sobrescrever em silêncio")
        # reexportar o MESMO lote: nada é reescrito, e o motivo diz isso
        r2x = exp.escrever_em_pasta(emp, raiz, EMPRESA_A, plano_de(),
                                    organizacao=exp.ORG_EMPRESA, empresa=NOME)
        igual(r2x["xmls_escritos"], 0, "reexportar não reescreve nada")
        igual(r2x["ja_existiam_iguais"], 4,
              "os 4 são reconhecidos como idênticos, por SHA-256")
        igual(r2x["conflitos"], 0, "e não há conflito nenhum")
        ok(r2x["confere"], "a soma continua fechando")

        # agora o caso perigoso: MESMO nome, conteúdo DIFERENTE
        vitima = sorted(pastas[0].glob("*.xml"))[0]
        antes_sha = sha(vitima)
        vitima.write_bytes(b"<outro>arquivo do usuario</outro>")
        adulterado = sha(vitima)
        plano3 = plano_de()
        r3x = exp.escrever_em_pasta(emp, raiz, EMPRESA_A, plano3,
                                    organizacao=exp.ORG_EMPRESA, empresa=NOME)
        igual(sha(vitima), adulterado,
              "o arquivo diferente NÃO foi sobrescrito")
        ok(sha(vitima) != antes_sha, "e continua sendo o que o usuário deixou")
        igual(r3x["conflitos"], 1, "o conflito foi contado")
        igual(r3x["ja_existiam_iguais"], 3, "e os outros 3 seguem idênticos")
        igual(r3x["por_motivo"].get(exp.ARQUIVO_DIFERENTE_JA_EXISTE), 1,
              "com o motivo ARQUIVO_DIFERENTE_JA_EXISTE")
        # `escrever_em_pasta` reescreve `plano.linhas` com os motivos reais,
        # então é ESTE plano que sabe o que aconteceu — um `planejar()` novo
        # já não saberia.
        csv3 = exp.relatorio_csv(plano3, pasta_xml="").decode("utf-8-sig")
        ok(exp.ARQUIVO_DIFERENTE_JA_EXISTE in csv3,
           "o relatório registra o arquivo que não foi sobrescrito")
        ok(exp.JA_EXISTIA_IGUAL in csv3,
           "e registra os que já estavam lá idênticos")
        linha_conflito = [l for l in csv3.splitlines()
                          if exp.ARQUIVO_DIFERENTE_JA_EXISTE in l]
        igual(len(linha_conflito), 1, "uma linha para o conflito")
        ok("NAO" in linha_conflito[0].split(";"),
           "e ela diz `exportado = NAO`, porque nada foi escrito")

        secao("As rotas da exportação em pasta")
        main = carregar_main(raiz)
        globals()['main'] = main   # a seção da tela usa depois
        pref = main.nfe_pasta_config()
        ok(pref["padrao"] == os.environ["FISCALE_EXPORTACOES"],
           "o padrão respeita FISCALE_EXPORTACOES — é o que segura o teste "
           "fora da raiz do disco do usuário")
        # E, sem o override, o padrão é a pasta de trabalho do FISCALE.
        guardado = os.environ.pop("FISCALE_EXPORTACOES")
        try:
            nativo = main.pasta_exportacao_padrao()
        finally:
            os.environ["FISCALE_EXPORTACOES"] = guardado
        ok(nativo.upper().replace(chr(92), "/").endswith(
               "/FISCALE/EXPORTACOES/XML"),
           "sem override, o padrão é <unidade>/FISCALE/EXPORTACOES/XML")
        for proibido in ("dominio", "Domínio", "Dominio"):
            ok(proibido not in nativo,
               "o padrão nativo não nomeia software de terceiro: %r" % proibido)
        for proibido in ("dominio", "Domínio", "Dominio"):
            ok(proibido not in pref["padrao"],
               "o padrão não nomeia software de terceiro: %r" % proibido)
        igual(sorted(o["valor"] for o in pref["organizacoes"]),
              sorted(exp.ORGANIZACOES),
              "a tela recebe as organizações que o módulo aceita")
        ok(all(o["rotulo"] for o in pref["organizacoes"]),
           "e cada uma vem com o rótulo escrito pelo módulo, não pela tela")
        igual(pref["organizacao"], exp.ORG_UNICA,
              "a preferência nasce gravando direto na pasta escolhida")

        alvo_rota = fora / "por_rota"
        r = main.nfe_exportar_pasta({"id": CONTA, "pasta": str(alvo_rota),
                                     "organizacao": exp.ORG_UNICA})
        igual(r["xmls_escritos"], 4, "a rota gravou os 4 XML completos")
        igual(r["destino"], "SERVIDOR", "e declarou que gravou no servidor")
        igual(len(list(alvo_rota.glob("*.xml"))), 4, "os arquivos estão lá")

        # A preferência é validada ANTES de virar preferência: pasta ruim
        # guardada é erro que só aparece na próxima exportação.
        try:
            main.nfe_pasta_config_salvar({"pasta": str(raiz)})
            ok(False, "a rota deveria recusar a raiz de dados")
        except Exception as e:
            ok(getattr(e, "status_code", None) == 400,
               "salvar preferência apontando para os dados devolve 400")
        try:
            main.nfe_pasta_config_salvar({"pasta": str(fora / "boa"),
                                          "organizacao": "INVENTADA"})
            ok(False, "organização inventada deveria ser recusada")
        except Exception as e:
            ok(getattr(e, "status_code", None) == 400,
               "organização inventada devolve 400")
        try:
            main.nfe_exportar_pasta({"id": CONTA, "pasta": "exportacoes"})
            ok(False, "caminho relativo deveria ser recusado pela rota")
        except Exception as e:
            ok(getattr(e, "status_code", None) == 400,
               "caminho relativo na rota devolve 400")

        secao("A rota do ZIP plano não nomeia software nenhum")
        ok(not hasattr(main, "nfe_exportar_dominio"),
           "a rota /exportar/dominio não existe mais")
        ok(hasattr(main, "nfe_exportar_xmls_planos"),
           "e virou /exportar/xmls-planos")

        secao("O relatório diz disponibilidade, exportado e motivo")
        cab = exp.relatorio_csv(plano_de()).decode("utf-8-sig").splitlines()[0]
        for col in ("disponibilidade_xml", "exportado", "motivo"):
            ok(col in cab.split(";"), "a coluna %r existe" % col)
        for velha in ("incluido_no_zip", "arquivo_no_zip"):
            ok(velha not in cab, "a coluna %r, que dizia 'zip', saiu" % velha)
        linhas_csv = exp.relatorio_csv(plano_de()).decode("utf-8-sig")
        ok("Somente resumo" in linhas_csv,
           "e a disponibilidade sai legível, não só COMPLETO/RESUMO")

        secao("Entradas inválidas")
        try:
            exp.escrever_em_pasta(fora / "z", raiz, EMPRESA_A, plano_de(),
                                  organizacao="ARVORE_MALUCA")
            ok(False, "organização desconhecida deveria ser recusada")
        except exp.ErroExportacao as e:
            ok("organização desconhecida" in str(e),
               "organização desconhecida é recusada com o motivo")

    # ── a tela ──────────────────────────────────────────────────────────
    secao("A tela não depende de software contábil nenhum")
    html = (RAIZ / "web" / "nfe_documentos.html").read_text(encoding="utf-8")
    for nome in ("Domínio", "Dominio", "DOMINIO", "dominio"):
        ok(nome not in html, "a tela não escreve %r em lugar nenhum" % nome)
    for oferta in ("Baixar XMLs em ZIP", "XMLs + relatório", "Baixar DANFEs"):
        ok(oferta in html, "o menu oferece %r" % oferta)
    # Salvar em pasta saiu do menu no ajuste de UX: virou o botão principal,
    # junto dos filtros. Quem cobre isso é `teste_nfe6b_ux.py`.
    ok("Baixar XMLs em lote" in html and 'id="btnBaixarLote"' in html,
       "e salvar em pasta virou o botão 'Baixar XMLs em lote'")
    ok('data-exportar="pasta"' not in html,
       "deixando de ser item de menu")
    ok("computador que roda o Fiscale" in html,
       "a tela avisa que salvar em pasta grava no SERVIDOR, não no aparelho")
    ok("verSomenteResumo" in html and "cardResumo.onclick" in html,
       "o card 'Só resumo' é clicável e aplica o filtro")
    ok("data-ver-resumo" in html,
       "e a prévia leva aos documentos que ficam de fora")
    ok("btnBuscarCompleto" in html,
       "'Buscar XML completo' existe como contrato visual")
    ok("disabled" in html.split("btnBuscarCompleto")[1][:160],
       "e o botão está desligado (`disabled`)")
    ok("não constrói" in html,
       "e a tela diz que o FISCALE não fabrica XML a partir do resumo")
    for consulta in ("NFeDistribuicaoDFe", "distNSU", "consChNFe"):
        ok(consulta not in html,
           "a tela não chama a SEFAZ (%s ausente)" % consulta)

    secao("O seletor abre nas pastas de VERDADE do usuário")
    # `Path.home()/"Desktop"` mente quando o OneDrive assume as pastas
    # conhecidas. Quem sabe é o registro — e a pasta antiga continua existindo,
    # então "existe" nunca foi prova de "é a certa".
    conhecidas = main.pastas_conhecidas()
    rotulos = [r for r, _ in conhecidas]
    igual(rotulos, ["Downloads", "Área de Trabalho", "Documentos",
                    "Pasta do usuário"],
          "os quatro pontos de partida, nessa ordem")
    ok(all(isinstance(c, Path) for _, c in conhecidas),
       "cada um vem como caminho, não como texto")

    if os.name == "nt":
        import winreg
        anotado = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                main._CHAVE_SHELL) as k:
                for _r, chave, _q in main._PASTAS_CONHECIDAS:
                    try:
                        v, _t = winreg.QueryValueEx(k, chave)
                        anotado[chave] = os.path.expandvars(v)
                    except OSError:
                        pass
        except Exception:
            anotado = {}
        achadas = dict(conhecidas)
        for rotulo, chave, _queda in main._PASTAS_CONHECIDAS:
            esperado = anotado.get(chave, "")
            if not esperado or not Path(esperado).is_dir():
                continue          # sem anotação utilizável, nada a comparar
            igual(str(achadas[rotulo]), esperado,
                  "%s é a que o Windows anotou, não o palpite" % rotulo)
        ok(bool(anotado), "o registro respondeu nesta máquina")
    else:
        ok(True, "fora do Windows não há registro a consultar")

    # A queda tem de continuar existindo: máquina sem OneDrive, ou chave
    # ausente, não pode ficar sem pontos de partida.
    # A regra saiu do `main.py` e virou `pastas_windows.py`, na raiz: o
    # `fiscale_server` precisa da MESMA resposta para gravar o backup, e duas
    # cópias dela já enganaram duas vezes.
    fonte_pw = (RAIZ / "pastas_windows.py").read_text("utf-8")
    ok("Path.home() / queda" in fonte_pw,
       "e há queda para o palpite quando o registro não responde")
    ok('"Personal"' in fonte_pw and '"Desktop"' in fonte_pw,
       "as chaves são as do Windows (`Desktop`/`Personal`), não os nomes "
       "traduzidos")

    secao("NF-e e NFS-e escolhem pasta do MESMO jeito")
    comp = RAIZ / "web" / "fiscale-pasta.js"
    ok(comp.is_file(), "existe UM seletor de pasta compartilhado")
    fonte = comp.read_text(encoding="utf-8")
    ok("/api/pastas" in fonte,
       "ele usa a rota que existe (`/api/pastas`), não a que morreu")
    ok("tkinter" in fonte,
       "e o comentário registra por que a janela nativa saiu")
    nfse_html = (RAIZ / "nfse" / "frontend" / "index.html").read_text("utf-8")
    for tela, fonte_tela in (("NF-e", html), ("NFS-e", nfse_html)):
        ok("fiscale-pasta.js" in fonte_tela,
           "a tela %s carrega o seletor compartilhado" % tela)
        ok("Pasta de saída dos arquivos" in fonte_tela,
           "a tela %s usa o mesmo rótulo" % tela)
        ok("Escolher pasta" in fonte_tela,
           "a tela %s tem o botão 📁 Escolher pasta" % tela)
        ok("FiscalePasta.escolher" in fonte_tela,
           "a tela %s chama o componente, e não uma cópia sua" % tela)
    # A rota que sumiu na PORT 4 não pode voltar a ser chamada por ninguém.
    for tela, fonte_tela in (("NF-e", html), ("NFS-e", nfse_html)):
        ok("api/escolher-pasta" not in fonte_tela.replace("`/api/escolher-pasta`", ""),
           "a tela %s não chama mais /api/escolher-pasta (rota inexistente)" % tela)
    backend = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8")
    ok('"/api/escolher-pasta"' not in backend,
       "e a rota realmente não existe no backend — a chamada era órfã")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
