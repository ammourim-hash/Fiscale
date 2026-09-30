#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes do ajuste de UX da NF-e 6B.

    python teste_nfe6b_ux.py

O QUE ESTA SUÍTE PROVA

    1. Que a pasta escolhida É o destino: nenhuma subpasta nasce no fluxo
       padrão.
    2. Que o lote respeita os filtros da tela, e só eles.
    3. Que o relatório deixou de ser obrigatório.
    4. Que o seletor da NF-e mostra só empresa com Inscrição Estadual — e que
       IE vazia, com espaços, `ISENTO`, zeros e afins contam como sem IE.
    5. Que empresa sem IE **não perde nada**: acervo, índice, XML e checkpoint
       continuam onde estavam, e ela sai como pendência, não em silêncio.
    6. Que IE cadastrada depois faz a empresa voltar sozinha, e IE apagada a
       faz sair sozinha — porque não há lista paralela.
    7. Que o XML continua sendo o original, byte a byte.
    8. Que nada disso toca rede.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                          # noqa: E402
import empresas_nfe as emp                           # noqa: E402
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


# De `teste_apoio_nfe6`, NÃO de `teste_nfe6`: aquele tem as asserções no
# nível do módulo, e importá-lo reexecutava as 312 dele antes destas.
from teste_apoio_nfe6 import montar, carregar_main, CONTA   # noqa: E402
from teste_fixturas_nfe import EMPRESA_A              # noqa: E402


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def cert(cnpj, nome, cid="c1"):
    return {"id": cid, "cnpj": cnpj, "nome": nome, "caminho": "certs/E.pfx"}


def main() -> int:
    # ═══════════════════════════════════════════════════════════════════
    secao("Inscrição Estadual — o que conta e o que não conta")
    for valido in ("038067749", "023008776", "12.345.678-9", "ISENTO123",
                   "PE 066540453", "0123"):
        ok(emp.ie_valida(valido), "%r é uma IE" % valido)
    for invalido in (None, "", "   ", "\t\n", "0", "00", "000000000",
                     "ISENTO", "isento", " Isenta ", "N/A", "NULL", "-",
                     "SEM IE", "Não possui", "X", "IE", "??"):
        ok(not emp.ie_valida(invalido), "%r NÃO é uma IE" % invalido)

    secao("Empresa ativa por omissão")
    ok(emp.esta_ativa({}), "cliente sem o campo é ativo")
    ok(emp.esta_ativa({"ie": "1"}), "e continua ativo com outros campos")
    ok(not emp.esta_ativa({"ativo": False}), "`ativo: false` é inativa")
    ok(not emp.esta_ativa({"inativo": True}), "`inativo: true` também")
    ok(emp.esta_ativa({"ativo": True}), "`ativo: true` é ativa")

    # ═══════════════════════════════════════════════════════════════════
    secao("Quem entra no seletor da NF-e")
    certs = [cert("11111111000191", "COM IE LTDA", "a"),
             cert("22222222000172", "IE VAZIA LTDA", "b"),
             cert("33333333000153", "IE SO ESPACOS LTDA", "c"),
             cert("44444444000134", "SEM CADASTRO LTDA", "d"),
             cert("55555555000115", "INATIVA LTDA", "e")]
    clientes = [
        {"cnpj": "11.111.111/0001-91", "nome": "COM IE LTDA", "ie": "038067749"},
        {"cnpj": "22.222.222/0001-72", "nome": "IE VAZIA LTDA", "ie": ""},
        {"cnpj": "33.333.333/0001-53", "nome": "IE SO ESPACOS", "ie": "    "},
        {"cnpj": "55.555.555/0001-15", "nome": "INATIVA", "ie": "123456789",
         "ativo": False},
    ]
    r = emp.separar(certs, clientes)
    igual([e["nome"] for e in r["operacionais"]], ["COM IE LTDA"],
          "só a empresa com IE aparece")
    motivos = {p["nome"]: p["motivo"] for p in r["pendencias"]}
    igual(motivos.get("IE VAZIA LTDA"), emp.MOTIVO_SEM_IE, "IE vazia fica fora")
    igual(motivos.get("IE SO ESPACOS LTDA"), emp.MOTIVO_SEM_IE,
          "IE só com espaços fica fora")
    igual(motivos.get("SEM CADASTRO LTDA"), emp.MOTIVO_SEM_CADASTRO,
          "sem cadastro em Clientes fica fora, com motivo próprio")
    igual(motivos.get("INATIVA LTDA"), emp.MOTIVO_INATIVA,
          "inativa fica fora mesmo tendo IE")
    igual(len(r["pendencias"]) + len(r["operacionais"]), len(certs),
          "ninguém some: operacionais + pendências = todos os certificados")
    ok(all(p["explicacao"] for p in r["pendencias"]),
       "toda pendência sai com o motivo escrito para o usuário")

    secao("A IE cadastrada depois traz a empresa de volta sozinha")
    depois = [dict(c) for c in clientes]
    depois[1]["ie"] = "023008776"          # a que estava vazia
    r2 = emp.separar(certs, depois)
    ok("IE VAZIA LTDA" in [e["nome"] for e in r2["operacionais"]],
       "cadastrou a IE, a empresa apareceu — sem sincronizar nada")
    apagada = [dict(c) for c in clientes]
    apagada[0]["ie"] = ""
    r3 = emp.separar(certs, apagada)
    ok("COM IE LTDA" not in [e["nome"] for e in r3["operacionais"]],
       "apagou a IE, a empresa saiu — pela mesma leitura")
    ok(not any("empresas_nfe" in Path(RAIZ / "nfse" / "backend" / m).read_text(
                   "utf-8", errors="ignore")
               for m in ("classificador.py",)),
       "nenhum módulo fiscal consome o portão de IE (ele é só da tela)")

    # ═══════════════════════════════════════════════════════════════════
    with apoio.raiz_temporaria("fiscale_nfe6bux_") as raiz:
        ch = montar(raiz)
        fora = apoio.registrar_para_apagar(apoio.pasta_temp("nfe6bux_saida_"))

        def plano_de(**kw):
            return exp.planejar(raiz, EMPRESA_A, cq.Filtro(**kw))

        secao("A pasta escolhida É o destino")
        igual(exp.ORGANIZACAO_PADRAO, exp.ORG_UNICA,
              "o padrão é gravar direto na pasta escolhida")
        destino = fora / "XML JULHO"
        r = exp.escrever_em_pasta(destino, raiz, EMPRESA_A, plano_de(),
                                  empresa="TRANSPORTADORA TRX LOGISTICAS LTDA")
        igual(r["xmls_escritos"], 4, "gravou os 4 XML completos")
        igual(r["subpastas"], 1, "usou UMA pasta: a escolhida")
        igual([x.name for x in destino.iterdir() if x.is_dir()], [],
              "nenhuma subpasta foi criada")
        igual(len(list(destino.glob("*.xml"))), 4,
              "os XML estão soltos na pasta escolhida")
        ok(all(len(x.relative_to(destino).parts) == 1
               for x in destino.glob("*.xml")),
           "um nível: <pasta escolhida>/<chave>.xml")

        secao("O relatório deixou de ser obrigatório")
        ok(not (destino / exp.NOME_RELATORIO).exists(),
           "sem pedir, nenhum CSV aparece no meio dos XML")
        igual([x.suffix for x in destino.iterdir()], [".xml"] * 4,
              "nada além de .xml na pasta")
        com_rel = fora / "com relatorio"
        exp.escrever_em_pasta(com_rel, raiz, EMPRESA_A, plano_de(),
                              com_relatorio=True)
        ok((com_rel / exp.NOME_RELATORIO).is_file(),
           "quem pede o relatório continua recebendo")

        secao("O lote respeita os filtros da tela, e só eles")
        so_dest = exp.planejar(raiz, EMPRESA_A, cq.Filtro(papel="DESTINATARIO"))
        pasta_f = fora / "filtrado"
        rf = exp.escrever_em_pasta(pasta_f, raiz, EMPRESA_A, so_dest)
        gravados = set(x.stem for x in pasta_f.glob("*.xml"))
        esperados = set(l.chave for l in
                        exp.planejar(raiz, EMPRESA_A,
                                     cq.Filtro(papel="DESTINATARIO")).incluidos)
        igual(gravados, esperados,
              "gravou exatamente o que o filtro selecionou")
        ok(ch["emitida"] not in gravados,
           "e o que o filtro excluiu não foi gravado")
        ok(rf["xmls_escritos"] < r["xmls_escritos"],
           "filtro mais estreito grava menos — não há segunda seleção")

        secao("O XML é o original, byte a byte")
        ac = acv.abrir(raiz, EMPRESA_A)
        iguais = 0
        for linha in plano_de().incluidos:
            origem = ac.caminho_canonico(linha.especie, linha.id_documento)
            copia = destino / linha.nome_no_zip
            if copia.is_file() and sha(origem) == sha(copia):
                iguais += 1
        igual(iguais, 4, "os 4 batem por SHA-256 com o acervo")

        secao("Reexportar é idempotente; conflito não sobrescreve")
        r_id = exp.escrever_em_pasta(destino, raiz, EMPRESA_A, plano_de())
        igual(r_id["xmls_escritos"], 0, "nada foi reescrito")
        igual(r_id["ja_existiam_iguais"], 4, "os 4 são idênticos")
        vitima = sorted(destino.glob("*.xml"))[0]
        vitima.write_bytes(b"<outro/>")
        marca = sha(vitima)
        r_cf = exp.escrever_em_pasta(destino, raiz, EMPRESA_A, plano_de())
        igual(sha(vitima), marca, "o arquivo diferente NÃO foi sobrescrito")
        igual(r_cf["conflitos"], 1, "e o conflito foi contado")

        secao("Somente resumo continua visível e achável")
        resumo = plano_de(conteudo=cq.RESUMO)
        igual(resumo.total, 1, "o filtro Somente resumo acha exatamente 1")
        igual(resumo.linhas[0].chave, ch["resumo"], "e é o documento certo")
        completos = plano_de(conteudo=cq.COMPLETO)
        igual(resumo.total + completos.total, plano_de().total,
              "resumo + completo = total: ninguém some da listagem")
        ok(ch["resumo"] + ".xml" not in [x.name for x in destino.glob("*.xml")],
           "e nenhum XML foi inventado para o resumo")

        # ═══════════════════════════════════════════════════════════════
        secao("A rota do seletor, e o que ela NÃO faz com os dados")
        main = carregar_main(raiz)
        globals()["main"] = main

        antes_acervo = {str(p): sha(p) for p in Path(ac.raiz()).rglob("*")
                        if p.is_file()}
        estado_nfe = Path(raiz) / EMPRESA_A / "nfe" / "estado.json"
        antes_checkpoint = (estado_nfe.read_bytes() if estado_nfe.is_file()
                            else None)

        # A empresa do cenário tem acervo e NÃO tem cadastro de cliente.
        saida = main.nfe_empresas()
        igual(saida["operacionais"], [],
              "sem IE cadastrada, nenhuma empresa no seletor")
        igual(len(saida["pendencias"]), 1, "ela sai como pendência")
        pend = saida["pendencias"][0]
        ok(pend["documentos_no_acervo"] > 0,
           "a pendência diz quantos documentos estão parados: %d"
           % pend["documentos_no_acervo"])
        ok("não tem Inscrição Estadual" in saida["aviso"],
           "e o aviso explica, em vez de a empresa sumir calada")

        depois_acervo = {str(p): sha(p) for p in Path(ac.raiz()).rglob("*")
                         if p.is_file()}
        igual(depois_acervo, antes_acervo,
              "NADA foi apagado do acervo da empresa fora do seletor")
        ok(exp.planejar(raiz, EMPRESA_A, cq.Filtro()).total == 5,
           "o índice continua respondendo pelos 5 documentos")
        depois_checkpoint = (estado_nfe.read_bytes() if estado_nfe.is_file()
                             else None)
        igual(depois_checkpoint, antes_checkpoint, "o checkpoint não se moveu")

        secao("Cadastrada a IE, a empresa aparece na rota")
        (Path(raiz) / "state_clientes.json").write_text(json.dumps(
            {"clientes": [{"cnpj": EMPRESA_A, "nome": "EMPRESA TESTE",
                           "ie": "066540453"}], "seq": 1},
            ensure_ascii=False), encoding="utf-8")
        saida2 = main.nfe_empresas()
        igual([e["id"] for e in saida2["operacionais"]], [CONTA],
              "a empresa entrou no seletor sem nenhuma sincronização")
        igual(saida2["pendencias"], [], "e saiu das pendências")
        igual(saida2["aviso"], "", "sem pendência, sem aviso")

        secao("Apagada a IE, ela sai de novo")
        (Path(raiz) / "state_clientes.json").write_text(json.dumps(
            {"clientes": [{"cnpj": EMPRESA_A, "nome": "EMPRESA TESTE",
                           "ie": "   "}], "seq": 1},
            ensure_ascii=False), encoding="utf-8")
        igual(main.nfe_empresas()["operacionais"], [],
              "IE só com espaços tira a empresa do seletor")
        ok(exp.planejar(raiz, EMPRESA_A, cq.Filtro()).total == 5,
           "e os documentos continuam lá")

        # ═══════════════════════════════════════════════════════════════
        secao("Pendências cadastrais — a fila da reconciliação")
        # O cenário: a empresa tem acervo e NÃO tem cadastro em Clientes.
        (Path(raiz) / "state_clientes.json").unlink(missing_ok=True)
        fila = main.nfe_pendencias_cadastrais()
        igual(fila["total"], 1, "a empresa com acervo entra na fila")
        p0 = fila["pendencias"][0]
        igual(p0["situacao"], emp.MOTIVO_SEM_CADASTRO,
              "situação: sem cadastro em Clientes")
        igual(p0["situacao_rotulo"], "SEM CADASTRO EM CLIENTES",
              "com o rótulo que a tela mostra")
        ok(p0["cnpj"], "traz o CNPJ")
        ok(p0["nome"], "traz a razão social encontrada: %r" % p0["nome"])
        igual(p0["origem_do_nome"], "certificado digital",
              "e diz de onde o nome veio")
        ok(p0["documentos_no_acervo"] > 0,
           "traz a quantidade de documentos: %d" % p0["documentos_no_acervo"])
        igual(p0["ie_cadastrada"], "", "IE cadastrada hoje: nenhuma")
        igual(fila["documentos_parados"], p0["documentos_no_acervo"],
              "e o total de documentos parados fecha")
        igual(fila["grava_alguma_coisa"], False,
              "a rota declara, no próprio retorno, que não grava nada")

        secao("Empresa cadastrada SEM IE também entra na fila")
        (Path(raiz) / "state_clientes.json").write_text(json.dumps(
            {"clientes": [{"cnpj": EMPRESA_A, "nome": "EMPRESA TESTE",
                           "ie": "   "}], "seq": 1}, ensure_ascii=False),
            encoding="utf-8")
        fila2 = main.nfe_pendencias_cadastrais()
        igual(fila2["total"], 1, "continua na fila")
        igual(fila2["pendencias"][0]["situacao"], emp.MOTIVO_SEM_IE,
              "mas agora a situação é: sem Inscrição Estadual")
        igual(fila2["pendencias"][0]["situacao_rotulo"],
              "SEM INSCRIÇÃO ESTADUAL", "com o rótulo certo")

        secao("Empresa SEM acervo não vira pendência de reconciliação")
        # Ela continua fora do seletor, mas cadastro incompleto de empresa sem
        # movimento é arrumação, não fila de correção.
        todas = main.nfe_empresas()
        ok(len(todas["pendencias"]) >= 1, "ela está entre as pendências gerais")
        ok(all(p["documentos_no_acervo"] > 0
               for p in main.nfe_pendencias_cadastrais()["pendencias"]),
           "mas a fila de reconciliação só tem quem tem documento parado")

        secao("A IE sugerida é sugestão — e NADA é gravado")
        antes_cadastro = (Path(raiz) / "state_clientes.json").read_bytes()
        antes_ac = {str(p): sha(p) for p in Path(ac.raiz()).rglob("*")
                    if p.is_file()}
        antes_cp = (estado_nfe.read_bytes() if estado_nfe.is_file() else None)

        main.nfe_pendencias_cadastrais()
        main.nfe_pendencias_cadastrais()      # duas vezes: nada acumula

        igual((Path(raiz) / "state_clientes.json").read_bytes(), antes_cadastro,
              "o cadastro de Clientes NÃO foi tocado")
        igual({str(p): sha(p) for p in Path(ac.raiz()).rglob("*")
               if p.is_file()}, antes_ac,
              "o acervo continua byte a byte igual")
        igual((estado_nfe.read_bytes() if estado_nfe.is_file() else None),
              antes_cp, "o checkpoint não se moveu")
        ok(exp.planejar(raiz, EMPRESA_A, cq.Filtro()).total == 5,
           "e o índice continua respondendo pelos 5 documentos")

        secao("A confirmação da IE é que faz a empresa voltar")
        # É o que aconteceria DEPOIS de o usuário salvar em Clientes.
        ainda_fora = main.nfe_empresas()["operacionais"]
        igual(ainda_fora, [], "antes de confirmar, segue fora do seletor")
        (Path(raiz) / "state_clientes.json").write_text(json.dumps(
            {"clientes": [{"cnpj": EMPRESA_A, "nome": "EMPRESA TESTE",
                           "ie": "066540453"}], "seq": 1}, ensure_ascii=False),
            encoding="utf-8")
        igual([e["id"] for e in main.nfe_empresas()["operacionais"]], [CONTA],
              "confirmada a IE em Clientes, a empresa entra no seletor")
        igual(main.nfe_pendencias_cadastrais()["total"], 0,
              "e sai da fila de pendências, sozinha")

        secao("Depois da correção, o acervo continua o mesmo")
        igual({str(p): sha(p) for p in Path(ac.raiz()).rglob("*")
               if p.is_file()}, antes_ac,
              "nenhum arquivo do acervo mudou em todo o percurso")
        igual((estado_nfe.read_bytes() if estado_nfe.is_file() else None),
              antes_cp, "nem o checkpoint")

        secao("A preferência da pasta nasce simples")
        pref = main.nfe_pasta_config()
        igual(pref["organizacao"], exp.ORG_UNICA,
              "direto na pasta escolhida")
        igual(pref["com_relatorio"], False,
              "sem relatório, a menos que peçam")
        igual([o["valor"] for o in pref["organizacoes"]][0], exp.ORG_UNICA,
              "e o padrão é a primeira opção oferecida")

    # ═══════════════════════════════════════════════════════════════════
    secao("Uma implementação só para 'onde fica a Área de Trabalho'")
    # O mesmo engano aconteceu duas vezes com o OneDrive: o seletor levava a
    # pessoa para a pasta errada, e o backup .fbk era gravado numa Área de
    # Trabalho que ela não via. Agora a regra mora num lugar só.
    import pastas_windows as pw
    ok(hasattr(pw, "area_de_trabalho") and hasattr(pw, "conhecidas"),
       "existe `pastas_windows` com a regra")
    ok(main.pastas_conhecidas is pw.conhecidas,
       "o backend do NFS-e usa a MESMA função, não uma cópia")
    igual(main._CHAVE_SHELL, pw.CHAVE_SHELL, "e a mesma chave do registro")

    servidor = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8")
    ok("import pastas_windows" in servidor,
       "o `fiscale_server` também importa a regra")
    ok("pastas_windows.area_de_trabalho()" in servidor,
       "e a usa para dizer onde a Area de Trabalho fica de verdade")
    # ANTES ERAM DOIS PONTOS; HOJE E UM SO, E ISSO E MELHORA, NAO PERDA.
    #     O backup gravava na Area de Trabalho, e por isso precisava saber
    #     onde ela estava. Desde a pasta canonica ele grava em
    #     <FISCALE_DADOS>\backups e nao pergunta mais. O que o teste tem de
    #     proteger nao e a CONTAGEM de chamadas - e que ninguem volte a
    #     adivinhar a mesa por conta propria.
    ok('Path.home() / "Desktop"' not in servidor
       and "Path.home()/\"Desktop\"" not in servidor,
       "e ninguem remonta a mesa a mao no servidor")
    ok("backup_drive.canonica()" in servidor,
       "o backup grava na pasta canonica, nao na Area de Trabalho")
    # A forma antiga não pode voltar: era ela que escondia o .fbk.
    for velho in ('os.path.expanduser("~"), "Desktop"',
                  'Path.home() / "Desktop"'):
        ok(velho not in servidor,
           "o `fiscale_server` não volta a chutar o caminho (%s)" % velho)

    if os.name == "nt":
        alvo = pw.area_de_trabalho()
        ok(alvo.is_dir(), "a Área de Trabalho resolvida existe: %s" % alvo)
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, pw.CHAVE_SHELL) as k:
                anotado = os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
        except OSError:
            anotado = ""
        if anotado and Path(anotado).is_dir():
            igual(str(alvo), anotado,
                  "e é a que o Windows anotou, não `~/Desktop`")
    else:
        ok(True, "fora do Windows não há registro a consultar")

    secao("A tela: fluxo simples, junto dos filtros")
    html = (RAIZ / "web" / "nfe_documentos.html").read_text(encoding="utf-8")
    ok('id="barraSaida"' in html, "existe a barra de saída")
    ok("Pasta de saída dos arquivos" in html, "com o rótulo do NFS-e")
    ok('id="btnEscolherPasta"' in html and "Escolher pasta" in html,
       "e o botão 📁 Escolher pasta")
    ok('id="btnBaixarLote"' in html and "Baixar XMLs em lote" in html,
       "o botão do lote fica ao lado")
    i_barra = html.index('id="barraSaida"')
    i_lista = html.index('<section class="quadro"')
    ok(i_barra < i_lista, "a barra vem ANTES da listagem, junto dos filtros")
    ok("FiscalePasta.escolher" in html, "usa o componente compartilhado")
    ok("fiscale-pasta.js" in html, "e o carrega")

    secao("As opções de organização saíram do fluxo principal")
    i_av = html.index('id="avancadoSaida"')
    ok(html[i_av - 400:i_av].count("<details") >= 1
       or "<details" in html[max(0, i_av - 200):i_av + 60],
       "elas moraram num <details> recolhido")
    ok("Configurações avançadas" in html, "chamado de Configurações avançadas")
    ok('id="pastaOrg"' in html and 'id="pastaRelatorio"' in html,
       "e trazem organização e relatório")
    ok("Empresa (recomendado)" not in html,
       "nenhum rótulo de organização repetido na tela — vêm do backend")
    ok("Usar o padrão" not in html, "o botão 'Usar o padrão' saiu")
    ok('data-exportar="pasta"' not in html,
       "'Salvar XMLs em pasta' não é mais item de menu: virou o botão")

    secao("A área administrativa de pendências")
    pend = (RAIZ / "web" / "nfe_pendencias.html").read_text(encoding="utf-8")
    ok("Pendências cadastrais da NF-e" in pend, "a tela existe e se identifica")
    for campo in ("CNPJ", "Razão social encontrada", "Documentos no acervo",
                  "IE cadastrada hoje", "IE encontrada nos XMLs",
                  "Por que ficou de fora"):
        ok(campo in pend, "mostra %r" % campo)
    ok("Revisar cadastro" in pend, "e oferece a ação Revisar cadastro")
    ok("clientes.html?" in pend,
       "que leva ao cadastro existente, não a uma segunda base")
    ok("Nada é salvo até você confirmar" in pend,
       "deixando claro que ela não grava")
    for escrita in ("method:'POST'", 'method: "POST"', "fetch(rota,{method"):
        ok(escrita not in pend, "a tela não faz POST nenhum (%r)" % escrita)
    ok("SEFAZ" in pend, "e diz que não consulta a SEFAZ")

    secao("A página é do administrador")
    import fiscale_papeis as papeis
    ok("/nfe_pendencias.html" in papeis._ADMIN_PAGINAS,
       "a página está na lista do admin")
    ok(not papeis.pode("operador", "GET", "/nfe_pendencias.html"),
       "o operador não abre a página")
    ok(papeis.pode("admin", "GET", "/nfe_pendencias.html"),
       "o admin abre")
    ok(not papeis.pode("operador", "GET", "/api/nfe/pendencias-cadastrais"),
       "e a rota também é do admin — esconder botão nunca foi a proteção")
    ok(papeis.pode("admin", "GET", "/api/nfe/pendencias-cadastrais"),
       "que o admin alcança")
    ok(not papeis.pode("operador", "GET", "/api/nfe/empresas/ie-sugerida"),
       "a IE sugerida também não é do operador")

    secao("Clientes recebe a sugestão sem gravar")
    cli = (RAIZ / "web" / "clientes.html").read_text(encoding="utf-8")
    ok("function revisarDaNFe" in cli, "a chegada da revisão existe")
    ok("revisarDaNFe();" in cli, "e é chamada depois de carregar o cadastro")
    ok("ie_sugerida" in cli, "lê a IE sugerida da querystring")
    ok("editarCliente(jaExiste.id)" in cli,
       "CNPJ que já existe abre em EDIÇÃO, para não duplicar a empresa")
    ok("lida dos XMLs" in cli and "confira antes de salvar" in cli,
       "e avisa que o número é sugestão")
    ok("Nada foi gravado ainda" in cli, "dizendo que nada foi salvo")
    i_rev = cli.index("function revisarDaNFe")
    corpo_rev = cli[i_rev:cli.index("async function salvarCliente")]
    for escrita in ("persistir()", "salvarCliente()", "clientes.push"):
        ok(escrita not in corpo_rev,
           "a revisão não chama %r — quem salva é o usuário" % escrita)

    secao("Nomenclatura neutra")
    for nome in ("Domínio", "Dominio", "DOMINIO", "dominio", "Alterdata",
                 "Fortes", "Contmatic"):
        ok(nome not in html, "a tela não escreve %r" % nome)
    for m in ("empresas_nfe.py", "ingestao/exportacao.py"):
        fonte = (RAIZ / "nfse" / "backend" / m).read_text("utf-8")
        for nome in ("Domínio", "Dominio", "Alterdata", "Fortes"):
            ok(nome not in fonte, "%s não escreve %r" % (m, nome))

    secao("O somente resumo na tela")
    ok("XML: somente resumo" in html and "XML: completo" in html,
       "o indicador aparece na listagem")
    ok("Distribuição DF-e" in html, "com a explicação de o que isso significa")
    ok('id="verResumosLote"' in html and "verSomenteResumo" in html,
       "'Ver N documentos' está na prévia do lote e aplica o filtro")
    ok("cardResumo.onclick" in html, "e o card também é clicável")
    ok('id="btnBuscarCompleto"' in html,
       "'Buscar XML completo' segue como contrato visual")
    ok("disabled" in html.split("btnBuscarCompleto")[1][:160],
       "desabilitado")
    for consulta in ("NFeDistribuicaoDFe", "distNSU", "consChNFe", "consNSU"):
        ok(consulta not in html, "a tela não chama a SEFAZ (%s)" % consulta)

    secao("O portão de IE não fala com a SEFAZ nem apaga nada")
    fonte_bruta = (RAIZ / "nfse" / "backend"
                   / "empresas_nfe.py").read_text("utf-8")
    # A guarda é sobre o que o módulo CHAMA, não sobre o que ele explica: a
    # docstring diz que o checkpoint fica intacto, e isso é documentação, não
    # uso. Sem tirar comentário e docstring, o teste puniria a explicação.
    import ast as _ast
    arvore = _ast.parse(fonte_bruta)
    for no in _ast.walk(arvore):
        if isinstance(no, (_ast.Module, _ast.FunctionDef, _ast.ClassDef)):
            d = _ast.get_docstring(no, clean=False)
            if d and no.body and isinstance(no.body[0], _ast.Expr):
                no.body.pop(0)
    fonte = _ast.unparse(arvore)
    for proibido in ("requests", "urllib", "socket", "unlink", "rmtree",
                     "remove(", "checkpoint", "distNSU", "shutil", "open("):
        ok(proibido not in fonte,
           "empresas_nfe não usa %r no código" % proibido)
    ok("checkpoint" in fonte_bruta,
       "mas a documentação diz, sim, que o checkpoint fica intacto")
    ok("def separar" in fonte and "return {" in fonte,
       "ele só lê e devolve — não guarda estado")

    # ═══════════════════════════════════════════════════════════════════
    secao("A barra de módulos é uma coluna à esquerda")
    nav = (RAIZ / "web" / "fiscale-nav.js").read_text("utf-8")

    ok("flex-direction:column" in nav and "bottom:' + MARGEM" in nav,
       "a barra é uma coluna que vai do topo ao pé da janela")
    ok("padding-left:' + FOLGA" in nav,
       "e o corpo reserva o espaço à ESQUERDA")
    ok("'body{padding-top:' + FOLGA" not in nav,
       "e não reserva mais espaço no topo")

    # A CAIXA TEM DE SER A PEGADA, NAO O MIOLO.
    #     Sem `border-box`, `width:236` vira 258 na tela (padding 20 + borda 2)
    #     e a folga calculada encosta o conteúdo na barra.
    ok("box-sizing:border-box" in nav,
       "a largura declarada é a largura real da barra")

    # A FOLGA ESTREITA VEM DEPOIS DA BASE.
    #     Mesma especificidade: quem vem por último vence. Dentro da media
    #     query lá de cima, esta regra perdia, e o corpo reservava 258px para
    #     uma barra de 72.
    base = nav.index("'body{padding-left:' + FOLGA + 'px")
    estreita = nav.index("body{padding-left:' + FOLGA_ESTREITA")
    ok(estreita > base,
       "a folga do modo estreito vem depois da folga base, e por isso vence")

    # A BARRA NAO HERDA A FOLHA DE ESTILO DA TELA.
    #     `central_fiscal.html` declara `.item` como cartão BRANCO para as
    #     notícias. A barra usa `a.item`: sem declarar fundo e borda, o branco
    #     da página entrava e os módulos viravam pílulas brancas dentro da
    #     barra escura.
    i = nav.index("'#fscNav a.item{")
    regra = nav[i:nav.index("}'", i)]
    ok("background:transparent" in regra and "border:0" in regra,
       "o item da barra declara fundo e borda, e não deixa a tela decidir")
    ok(".item{" in (RAIZ / "web" / "central_fiscal.html").read_text("utf-8"),
       "e a Central Fiscal de fato declara um `.item` próprio (a colisão é real)")

    # O CABECALHO USA A MESMA PINTURA DA BARRA.
    #     Ângulos diferentes faziam o cartão de título ler mais escuro que a
    #     barra ao lado, na mesma tela.
    import re as _re
    pinturas = _re.findall(r"linear-gradient\((\d+)deg,#12454F 0%,#0C3038 68%,#0A2A31 100%\)", nav)
    ok(len(pinturas) >= 2 and len(set(pinturas)) == 1,
       "barra e cabeçalho de módulo usam o MESMO gradiente (%s)"
       % (", ".join(p + "deg" for p in sorted(set(pinturas))) or "nenhum"))

    # O SUBMENU ABRE AO LADO, NAO EMBAIXO.
    ok("r.right + 8" in nav,
       "o submenu abre à direita do item, não cobrindo os módulos seguintes")

    # ═══════════════════════════════════════════════════════════════════
    secao("O painel de situação lê o nome como o cadastro o guarda")
    # DEFEITO ENCONTRADO SÓ AO ENCOSTAR NO DADO REAL (07/09/2026).
    #     A rota lia `razao_social`, que é o nome da coluna na PLANILHA de
    #     importação. O registro em disco usa `nome` — então a tela caía no
    #     CNPJ para as 19 empresas do escritório. Dado fictício não revelaria:
    #     ele foi escrito já no formato da planilha.
    srv = (RAIZ / "fiscale_server.py").read_text("utf-8")
    i = srv.index('if rota == "/api/situacao/panorama":')
    bloco = srv[i:srv.index('if rota == "/api/situacao/tentativas":', i)]
    ok('c.get("nome")' in bloco,
       "o panorama lê `nome`, que é como o cadastro vivo guarda")
    ok(bloco.index('c.get("nome")') < bloco.index('c.get("razao_social")'),
       "e antes de `razao_social`, que é o nome da coluna da planilha")

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
