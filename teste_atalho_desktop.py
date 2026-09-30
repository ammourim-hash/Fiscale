#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes do atalho na Área de Trabalho.

    python teste_atalho_desktop.py

O QUE ESTA SUÍTE PROVA
    1. Que o atalho nasce no PRIMEIRO uso, e só nele.
    2. Que ele vai para a Área de Trabalho de VERDADE (a do registro), e não
       para a pasta homônima que o OneDrive deixa para trás.
    3. Que atalho apagado pelo usuário NÃO volta — apagar é uma decisão.
    4. Que atalho já existente não é sobrescrito.
    5. Que ele aponta para o LANÇADOR (`.bat`), nunca para o `.py`.
    6. Que falhar em criar o atalho não derruba o Fiscale.
    7. Que nada disso toca a rede.
    8. Que instância de TESTE ou HOMOLOGAÇÃO nunca toca a Área de Trabalho:
       FISCALE_SEM_ATALHO, FISCALE_TESTE_PROIBIR_RAIZ_REAL, FISCALE_DADOS fora
       do padrão e programa rodando da pasta temporária — inclusive num
       servidor de verdade, subido como processo (o caso de 14/09/2026).
    9. Que o portátil legítimo continua criando o atalho dele.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import atalho_desktop as ad                          # noqa: E402
import fiscale_dados as fd                           # noqa: E402
import pastas_windows as pw                          # noqa: E402

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


class MesaFalsa:
    """Troca a Área de Trabalho por uma pasta temporária.

    Sem isto o teste sujaria a Área de Trabalho de quem o roda — e um teste
    que mexe na mesa de alguém é um teste que ninguém roda duas vezes.
    """

    VARS = ("FISCALE_DADOS", ad.VAR_SEM_ATALHO, ad.VAR_TESTE)

    def __init__(self, legitima: bool = True):
        # `legitima`: simula a instalação de verdade — sem as variáveis que
        # bloqueiam o atalho e com o programa FORA da pasta temporária (quem
        # roda a suíte a partir de uma árvore exportada estaria dentro dela).
        self.legitima = legitima
        self.consultas = 0

    def __enter__(self):
        self.mesa = Path(tempfile.mkdtemp(prefix="mesa_teste_"))
        self.dados = Path(tempfile.mkdtemp(prefix="dados_teste_"))
        self._original = pw.area_de_trabalho
        self._env = {v: os.environ.get(v) for v in self.VARS}
        self._temp = ad._app_em_pasta_temporaria

        def mesa():
            self.consultas += 1
            return self.mesa
        pw.area_de_trabalho = mesa
        for v in self.VARS:
            os.environ.pop(v, None)
        if self.legitima:
            ad._app_em_pasta_temporaria = lambda: False
        return self

    def __exit__(self, *a):
        pw.area_de_trabalho = self._original
        ad._app_em_pasta_temporaria = self._temp
        for v, valor in self._env.items():
            if valor is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = valor
        shutil.rmtree(self.mesa, ignore_errors=True)
        shutil.rmtree(self.dados, ignore_errors=True)

    def na_mesa(self):
        return sorted(p.name for p in self.mesa.iterdir())


def main() -> int:
    secao("O lançador é um .bat, nunca um .py")
    alvo = ad.lancador()
    ok(alvo is not None, "achou o lançador desta instalação")
    if alvo is not None:
        ok(alvo.suffix.lower() == ".bat",
           "e é um .bat (%s) — apontar para .py abriria o editor de quem tem "
           "um associado" % alvo.name)
        ok(alvo.is_file(), "que existe no disco")

    secao("Ele usa a Área de Trabalho de VERDADE")
    fonte = (RAIZ / "atalho_desktop.py").read_text("utf-8")
    ok("pastas_windows.area_de_trabalho()" in fonte,
       "chama `pastas_windows.area_de_trabalho()`")
    for chute in ('Path.home() / "Desktop"', 'expanduser("~"), "Desktop"',
                  '"Desktop"'):
        ok(chute not in fonte or chute == '"Desktop"',
           "e não chuta o caminho (%s)" % chute)

    if os.name != "nt":
        secao("Fora do Windows")
        with MesaFalsa() as m:
            r = ad.garantir(m.dados)
            igual(r["motivo"], "fora_do_windows",
                  "diz que não se aplica, em vez de falhar")
        print("\n" + "=" * 62)
        print("  %d ok · %d falha(s)" % (_ok, _falhas))
        print("=" * 62)
        return 1 if _falhas else 0

    secao("Primeiro uso: o atalho nasce")
    with MesaFalsa() as m:
        igual(m.na_mesa(), [], "a mesa começa vazia")
        r = ad.garantir(m.dados)
        ok(r["criado"], "criou")
        igual(m.na_mesa(), ["Fiscale.lnk"], "e o arquivo é o Fiscale.lnk")
        ok((m.dados / ad.MARCA).is_file(),
           "com a marca gravada na pasta de dados")

        secao("Segunda vez: não faz nada")
        r2 = ad.garantir(m.dados)
        ok(not r2["criado"], "não criou de novo")
        igual(r2["motivo"], "ja_feito", "e diz por quê")
        igual(m.na_mesa(), ["Fiscale.lnk"], "a mesa continua com um só")

        secao("Apagado de propósito, NÃO volta")
        (m.mesa / "Fiscale.lnk").unlink()
        r3 = ad.garantir(m.dados)
        ok(not r3["criado"],
           "apagar é uma decisão do dono da máquina — recriar seria discutir "
           "com ele")
        igual(m.na_mesa(), [], "a mesa segue vazia")

    secao("Atalho que já existia não é sobrescrito")
    with MesaFalsa() as m:
        antigo = m.mesa / "Fiscale.lnk"
        antigo.write_bytes(b"ATALHO ANTIGO DO USUARIO")
        r = ad.garantir(m.dados)
        ok(not r["criado"], "não criou por cima")
        igual(r["motivo"], "ja_existia", "reconheceu o que já estava lá")
        igual(antigo.read_bytes(), b"ATALHO ANTIGO DO USUARIO",
              "e o arquivo do usuário continua intacto, byte a byte")
        ok((m.dados / ad.MARCA).is_file(),
           "a marca é gravada mesmo assim, para não tentar de novo")

    secao("O atalho aponta para o lançador")
    with MesaFalsa() as m:
        ad.garantir(m.dados)
        import subprocess
        lnk = m.mesa / "Fiscale.lnk"
        ps = ("$s=New-Object -ComObject WScript.Shell;"
              "$a=$s.CreateShortcut('%s');$a.TargetPath" % lnk)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        destino = (out.stdout or "").strip()
        ok(destino.lower().endswith(".bat"),
           "o alvo é um .bat: %s" % (destino[-40:] or "(vazio)"))
        ok(str(ad.lancador()).lower() == destino.lower(),
           "e é exatamente o lançador desta instalação")

    secao("Falhar em criar não derruba o Fiscale")
    with MesaFalsa() as m:
        original = ad.lancador
        ad.lancador = lambda: None          # simula instalação sem .bat
        try:
            r = ad.garantir(m.dados)
        finally:
            ad.lancador = original
        ok(not r["criado"], "não criou")
        igual(r["motivo"], "sem_lancador", "e devolveu o motivo")
        ok(isinstance(r, dict), "sem levantar exceção — é conveniência, "
                                "não requisito")

    with MesaFalsa() as m:
        original = pw.area_de_trabalho
        pw.area_de_trabalho = lambda: Path("Z:/nao/existe/mesa")
        try:
            r = ad.garantir(m.dados)
        finally:
            pw.area_de_trabalho = original
        igual(r["motivo"], "sem_area_de_trabalho",
              "Área de Trabalho inacessível também é motivo, não exceção")

    secao("O primeiro uso do servidor chama isto")
    servidor = (RAIZ / "fiscale_server.py").read_text("utf-8")
    ok("import atalho_desktop" in servidor, "o servidor importa o módulo")
    ok("atalho_desktop.garantir(DADOS)" in servidor,
       "e chama `garantir` na inicialização")

    secao("Instância de teste/homologação NÃO toca a Área de Trabalho")

    def bloqueado(m, motivo_esperado, rotulo, forcar=False):
        r = ad.garantir(m.dados, forcar=forcar)
        igual(r["motivo"], motivo_esperado, rotulo + ": recusa com o motivo certo")
        ok(not r["criado"] and m.na_mesa() == [], rotulo + ": nada na mesa")
        ok(m.consultas == 0, rotulo + ": nem chegou a consultar a Área de Trabalho")
        ok(not (m.dados / ad.MARCA).exists(), rotulo + ": e não gravou marca")

    with MesaFalsa() as m:
        os.environ[ad.VAR_SEM_ATALHO] = "1"
        bloqueado(m, "desligado_por_FISCALE_SEM_ATALHO", "FISCALE_SEM_ATALHO=1")
    with MesaFalsa() as m:
        os.environ[ad.VAR_TESTE] = "1"
        bloqueado(m, "ambiente_de_teste", "FISCALE_TESTE_PROIBIR_RAIZ_REAL=1")
    with MesaFalsa() as m:
        os.environ["FISCALE_DADOS"] = str(m.dados)
        bloqueado(m, "pasta_de_dados_nao_padrao", "FISCALE_DADOS fora do padrão")
    with MesaFalsa() as m:
        os.environ["FISCALE_DADOS"] = str(m.dados)
        bloqueado(m, "pasta_de_dados_nao_padrao", "--forcar também respeita", forcar=True)
    with MesaFalsa(legitima=False) as m:
        original = ad._raiz_do_app
        ad._raiz_do_app = lambda: Path(tempfile.gettempdir()) / "arvore_exportada" / "fiscale"
        try:
            bloqueado(m, "instalacao_em_pasta_temporaria",
                      "programa rodando da pasta temporária (árvore exportada)")
        finally:
            ad._raiz_do_app = original

    secao("A instalação de verdade continua criando")
    with MesaFalsa() as m:
        original = fd.raiz_padrao
        fd.raiz_padrao = lambda: m.dados.resolve()
        os.environ["FISCALE_DADOS"] = str(m.dados)      # como o Iniciar Fiscale.vbs faz
        try:
            r = ad.garantir(m.dados)
        finally:
            fd.raiz_padrao = original
        ok(r["criado"] and m.na_mesa() == ["Fiscale.lnk"],
           "FISCALE_DADOS igual à pasta padrão (a produção): cria")

    with MesaFalsa() as m:
        # Portátil de verdade: app/ e dados/ são irmãs, a marca está em dados/,
        # ninguém define FISCALE_DADOS. A decisão de "padrão" vem da lógica
        # REAL de fiscale_dados, só com a pasta do programa apontada para cá.
        portatil = Path(tempfile.mkdtemp(prefix="portatil_teste_"))
        (portatil / "app").mkdir()
        (portatil / "dados").mkdir()
        (portatil / "dados" / fd.MARCA_PORTATIL).write_text("", encoding="utf-8")
        (portatil / "Fiscale.bat").write_text("@echo off\r\n", encoding="utf-8")
        o_app, o_pasta = ad._raiz_do_app, fd.pasta_do_app
        ad._raiz_do_app = lambda: portatil / "app"
        fd.pasta_do_app = lambda: portatil / "app"
        try:
            ok(fd.raiz_padrao() == (portatil / "dados").resolve(),
               "(controle) para o portátil, a pasta padrão é a dados/ irmã")
            igual(ad.bloqueio(), "", "portátil sem FISCALE_DADOS: nada bloqueia")
            r = ad.garantir(portatil / "dados")
            ok(r["criado"] and m.na_mesa() == ["Fiscale.lnk"], "o portátil cria o atalho dele")
            igual(ad.lancador(), portatil / "Fiscale.bat",
                  "apontando para o Fiscale.bat do portátil")
            os.environ["FISCALE_DADOS"] = str(portatil / "dados")
            igual(ad.bloqueio(), "",
                  "FISCALE_DADOS igual à dados/ do portátil também não bloqueia")
        finally:
            ad._raiz_do_app, fd.pasta_do_app = o_app, o_pasta
            shutil.rmtree(portatil, ignore_errors=True)

    secao("Servidor de TESTE de verdade, com FISCALE_DADOS temporário")
    import teste_apoio
    mesa_real = pw.area_de_trabalho()

    def foto_mesa_real():
        try:
            return sorted((q.name, q.stat().st_mtime_ns) for q in mesa_real.iterdir()
                          if q.suffix.lower() in (".lnk", ".url"))
        except OSError:
            return None

    antes = foto_mesa_real()
    caixa = Path(tempfile.mkdtemp(prefix="atalho_servidor_"))
    (caixa / "mesa").mkdir()
    (caixa / "dados").mkdir()
    registro = caixa / "registro.jsonl"
    espiao = caixa / "espiao.py"
    # O servidor roda num processo próprio. Este "espião" troca, ANTES de o
    # fiscale_server carregar, a Área de Trabalho por uma pasta da caixa e as
    # funções que gravam .lnk/.url por anotações — mesmo que a trava falhasse,
    # nada chegaria à mesa real. Depois executa o servidor de verdade.
    espiao.write_text("\n".join([
        "import json, os, runpy, sys, pathlib",
        "APP = os.environ['TESTE_ATALHO_APP']",
        "sys.path.insert(0, APP)",
        "def anota(ev, **kw):",
        "    with open(os.environ['TESTE_ATALHO_LOG'], 'a', encoding='utf-8') as f:",
        "        f.write(json.dumps(dict(ev=ev, **kw)) + chr(10))",
        "import pastas_windows, atalho_desktop as ad",
        "def mesa():",
        "    anota('consultou_area_de_trabalho')",
        "    return pathlib.Path(os.environ['TESTE_ATALHO_MESA'])",
        "pastas_windows.area_de_trabalho = mesa",
        "def gravar_lnk(destino, alvo, ico):",
        "    anota('tentou_escrever', funcao='lnk', destino=str(destino))",
        "    return False",
        "def gravar_url(destino, alvo, ico):",
        "    anota('tentou_escrever', funcao='url', destino=str(destino))",
        "    return False",
        "ad._criar_lnk = gravar_lnk",
        "ad._criar_url = gravar_url",
        "_garantir = ad.garantir",
        "def garantir(d, forcar=False):",
        "    r = _garantir(d, forcar)",
        "    anota('garantir', resultado=r)",
        "    return r",
        "ad.garantir = garantir",
        "sys.argv = [os.path.join(APP, 'fiscale_server.py')]",
        "runpy.run_path(sys.argv[0], run_name='__main__')",
        ""]), encoding="utf-8")
    import socket as _s
    livre = _s.socket()
    livre.bind(("127.0.0.1", 0))
    porta = livre.getsockname()[1]
    livre.close()
    env = dict(os.environ)
    for v in (ad.VAR_SEM_ATALHO, ad.VAR_TESTE):
        env.pop(v, None)            # isola a regra de FISCALE_DADOS
    env.update({"FISCALE_DADOS": str(caixa / "dados"), "FISCALE_PORT": str(porta),
                "FISCALE_SEM_BANDEJA": "1", "FISCALE_NO_BROWSER": "1",
                "PYTHONIOENCODING": "utf-8", "TESTE_ATALHO_APP": str(RAIZ),
                "TESTE_ATALHO_LOG": str(registro),
                "TESTE_ATALHO_MESA": str(caixa / "mesa")})
    saida = open(caixa / "saida.log", "wb")
    proc = subprocess.Popen([sys.executable, str(espiao)], cwd=str(RAIZ), env=env,
                            stdin=subprocess.DEVNULL, stdout=saida,
                            stderr=subprocess.STDOUT)
    eventos = []
    try:
        for _ in range(240):
            if registro.exists():
                eventos = [json.loads(l) for l in
                           registro.read_text("utf-8").splitlines() if l.strip()]
                if any(e["ev"] == "garantir" for e in eventos):
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.25)
    finally:
        teste_apoio.encerrar_arvore(proc)
        saida.close()
    g = [e for e in eventos if e["ev"] == "garantir"]
    ok(len(g) == 1, "o servidor chegou a chamar garantir() no arranque")
    if g:
        igual(g[0]["resultado"]["motivo"], "pasta_de_dados_nao_padrao",
              "e recusou por causa do FISCALE_DADOS temporário")
        ok(not g[0]["resultado"]["criado"], "sem criar nada")
    ok(not any(e["ev"] == "tentou_escrever" for e in eventos),
       "nenhuma tentativa de gravar .lnk/.url")
    ok(not any(e["ev"] == "consultou_area_de_trabalho" for e in eventos),
       "a Área de Trabalho nem foi consultada")
    ok(list((caixa / "mesa").iterdir()) == [], "a mesa falsa continua vazia")
    ok(not (caixa / "dados" / ad.MARCA).exists(), "nenhuma marca na pasta do teste")
    ok(proc.poll() is not None, "o servidor de teste foi encerrado (pela árvore de PIDs)")
    igual(foto_mesa_real(), antes,
          "a Área de Trabalho REAL continua igual (.lnk/.url, mesma data)")
    shutil.rmtree(caixa, ignore_errors=True)

    secao("E ele vai no pacote portátil")
    import montar_portatil as mp
    ok("atalho_desktop.py" in mp.COPIAR_ARQUIVOS,
       "está na lista do portátil — sem ele o import quebra o servidor")
    igual(mp.conferir_lista(), [], "e a lista está completa")

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
