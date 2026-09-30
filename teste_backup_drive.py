#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A origem canônica dos backups, e o que vai (ou não) para o Drive.

    python teste_backup_drive.py

TRÊS DEFEITOS FECHADOS AQUI
    1. O `backup_para_drive.bat` procurava em `%USERPROFILE%\\Desktop` e tentava
       adivinhar a pasta do OneDrive escrevendo "Area de Trabalho" SEM ACENTO.
       A pasta real tem acento, o palpite nunca casava — e o arquivo ainda
       dizia "Concluido" mesmo sem ter achado `.fbk` nenhum.
    2. O backup morava onde calhasse. Agora mora em `<FISCALE_DADOS>\\backups`,
       e só. As pastas antigas viraram diagnóstico (`--localizar-legados`),
       que lista e nunca copia.
    3. A tela Home > Backup salvava na Área de Trabalho por padrão. O backup
       de 28/08 saiu com 157,7 MB para lá, e o envio ao Drive — que só olha a
       pasta canônica — não o enxergava.

    Nenhum teste daqui escreve no Drive nem toca em backup de verdade: tudo
    acontece em pastas temporárias com pacotes gerados na hora.
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import backup_drive as bd                              # noqa: E402
import fiscale_backup as fbk                           # noqa: E402

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


FRASE = "frase longa de teste do fiscale"


class Cenario:
    """Um Windows de mentira: raiz de dados, perfil e pastas conhecidas.

    Trocar as pastas conhecidas por monkeypatch em vez de mexer no registro é
    o que permite testar o caso do OneDrive numa máquina onde ele está ligado
    — e o contrário também.
    """

    def __init__(self, com_onedrive=True, nome_da_mesa="Área de Trabalho"):
        self.raiz = Path(tempfile.mkdtemp(prefix="fiscale_bkdrive_"))
        self.dados = self.raiz / "Fiscale" / "dados"
        (self.dados / "certs").mkdir(parents=True)
        self.perfil = self.raiz / "Users" / "fulana"
        self.literal = self.perfil / "Desktop"
        self.literal.mkdir(parents=True)
        self.downloads = self.perfil / "Downloads"
        self.downloads.mkdir()
        self.documentos = self.perfil / "Documents"
        self.documentos.mkdir()
        if com_onedrive:
            self.mesa = self.perfil / "OneDrive" / nome_da_mesa
            self.mesa.mkdir(parents=True)
        else:
            self.mesa = self.literal
        self.destino = self.raiz / "G" / "Meu Drive" / "FISCALE 2026"

    @property
    def canonica(self):
        return self.dados / bd.PASTA_CANONICA

    def ligar(self):
        self._guardado = {k: os.environ.get(k) for k in
                          ("USERPROFILE", "FISCALE_DADOS",
                           bd.VAR_ORIGEM, bd.VAR_DESTINO)}
        os.environ["USERPROFILE"] = str(self.perfil)
        os.environ["FISCALE_DADOS"] = str(self.dados)
        os.environ.pop(bd.VAR_ORIGEM, None)
        os.environ[bd.VAR_DESTINO] = str(self.destino)
        bd.fiscale_dados._raiz_cache = None
        self._pw = (bd.pastas_windows.area_de_trabalho,
                    bd.pastas_windows.documentos,
                    bd.pastas_windows.downloads)
        bd.pastas_windows.area_de_trabalho = lambda: self.mesa
        bd.pastas_windows.documentos = lambda: self.documentos
        bd.pastas_windows.downloads = lambda: self.downloads
        return self

    def desligar(self):
        (bd.pastas_windows.area_de_trabalho,
         bd.pastas_windows.documentos,
         bd.pastas_windows.downloads) = self._pw
        for chave, valor in self._guardado.items():
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor
        bd.fiscale_dados._raiz_cache = None
        shutil.rmtree(self.raiz, ignore_errors=True)

    def __enter__(self):
        return self.ligar()

    def __exit__(self, *a):
        self.desligar()

    def semear_dados(self):
        """Alguns arquivos para o backup ter o que levar."""
        (self.dados / "usuarios.json").write_text(
            '{"admin": {"sal": "00", "hash": "aa"}}', encoding="utf-8")
        (self.dados / "certificados.json").write_text("[]", encoding="utf-8")
        emp = self.dados / "12345678000199" / "xmls"
        emp.mkdir(parents=True)
        (emp / "nota.xml").write_text("<nfe/>", encoding="utf-8")


def gerar_fbk(cenario, pasta, idade_s=0):
    """Um backup v2 DE VERDADE, feito pelo gerador do Fiscale."""
    r = fbk.exportar(Path(pasta), frase=FRASE, raiz=cenario.dados)
    p = Path(r["arquivo"])
    if idade_s:
        quando = time.time() - idade_s
        os.utime(p, (quando, quando))
    return p


def gerar_v1(cenario, pasta, nome="FISCALE-backup-2020-01-01-0000.fbk"):
    """Um backup no formato ANTIGO, para provar que o Drive o recusa.

    Nome EXPLÍCITO: `nome_sugerido()` tem resolução de minuto, e dois backups
    gerados no mesmo minuto recebem o mesmo nome — um sobrescreve o outro.
    Foi o que aconteceu na primeira versão deste teste.
    """
    r = fbk._exportar_v1(Path(pasta) / nome, frase=FRASE, raiz=cenario.dados)
    return Path(r["arquivo"])


def falas(f, *a, **kw):
    linhas = []
    codigo = f(*a, saida=linhas.append, **kw)
    return codigo, "\n".join(linhas)


def main():
    # ── A pasta canônica ─────────────────────────────────────────────────
    secao("A origem canônica é <FISCALE_DADOS>\\backups")
    fonte_bd = (RAIZ / "backup_drive.py").read_text(encoding="utf-8")
    arv_bd = ast.parse(fonte_bd)
    with Cenario() as c:
        alvo = bd.canonica()
        igual(alvo, c.canonica, "a canônica sai de fiscale_dados.raiz()")
        ok(alvo.is_dir(), "e é criada quando não existe")
        igual(bd.canonica(criar=False), c.canonica,
              "criar=False devolve o mesmo caminho")
        chamadas_bd = {ast.unparse(n.func) for n in ast.walk(arv_bd)
                       if isinstance(n, ast.Call)}
        ok("fiscale_dados.raiz" in chamadas_bd,
           "a raiz vem do módulo que é a fonte única de verdade")
        embutidos = [n.value for n in ast.walk(arv_bd)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and "Fiscale" in n.value and "dados" in n.value]
        igual(embutidos, [], "e nenhum caminho de dados está escrito à mão")

    # ── O modo normal olha SÓ a canônica ─────────────────────────────────
    secao("Modo normal · não encosta nas pastas antigas")
    with Cenario() as c:
        c.semear_dados()
        gerar_fbk(c, c.literal)
        gerar_fbk(c, c.downloads)
        gerar_fbk(c, c.mesa)
        r = bd.escolher()
        igual(r["codigo"], bd.SAIU_NADA,
              "com a canônica vazia, não encontra nada")
        ok(str(c.canonica) in r["erro"], "e a mensagem nomeia a pasta canônica")
        ok(str(c.literal) not in r["erro"] and str(c.downloads) not in r["erro"],
           "sem oferecer as pastas antigas como alternativa")
        ok("--localizar-legados" in r["erro"], "mas ensinando como encontrá-las")

        bom = gerar_fbk(c, c.canonica)
        r = bd.escolher()
        igual(r["codigo"], bd.SAIU_OK, "com backup na canônica, escolhe")
        igual(Path(r["arquivo"]).parent, c.canonica, "e é o da canônica")
        igual(r["rotulo"], "pasta canônica", "dizendo de onde veio")
        igual(Path(r["arquivo"]).name, bom.name, "o arquivo certo")

    # ── Diagnóstico dos legados ──────────────────────────────────────────
    secao("--localizar-legados · lista e nunca copia")
    with Cenario() as c:
        c.semear_dados()
        gerar_fbk(c, c.literal)
        gerar_fbk(c, c.downloads)
        codigo, texto = falas(bd.relatorio_legados)
        igual(codigo, bd.SAIU_OK, "o diagnóstico termina bem")
        ok(str(c.literal) in texto and str(c.downloads) in texto,
           "listando as pastas onde achou")
        ok("2 arquivo(s) fora da pasta canônica" in texto, "com a contagem")
        ok("NÃO são enviados" in texto, "dizendo que não serão enviados")
        ok("íntegro" in texto, "e conferindo cada um")
        ok(not list(c.canonica.glob("*.fbk")),
           "e NÃO copiou nada para a canônica")
        ok(not c.destino.exists(), "nem criou o destino")
        fn = [n for n in ast.walk(arv_bd) if isinstance(n, ast.FunctionDef)
              and n.name == "relatorio_legados"][0]
        chamadas = {getattr(n.func, "id", "") or getattr(n.func, "attr", "")
                    for n in ast.walk(fn) if isinstance(n, ast.Call)}
        ok("executar" not in chamadas and "copy2" not in chamadas
           and "replace" not in chamadas,
           "o modo de diagnóstico não chama nada que copie")

    # ── Origem explícita ─────────────────────────────────────────────────
    secao("--origem e FISCALE_BACKUP_ORIGEM · só quando alguém digita")
    with Cenario() as c:
        c.semear_dados()
        gerar_fbk(c, c.canonica)
        antigo = gerar_fbk(c, c.literal)

        r = bd.escolher()
        igual(Path(r["arquivo"]).parent, c.canonica,
              "sem pedir nada, usa a canônica")

        r = bd.escolher(origem_explicita=str(c.literal))
        igual(r["codigo"], bd.SAIU_OK, "--origem funciona")
        igual(Path(r["arquivo"]).name, antigo.name, "e usa a pasta pedida")
        ok("administrativo" in r["rotulo"], "marcada como uso administrativo")

        os.environ[bd.VAR_ORIGEM] = str(c.literal)
        r = bd.escolher()
        igual(Path(r["arquivo"]).parent, c.literal,
              "a variável de ambiente também vence a canônica")
        os.environ.pop(bd.VAR_ORIGEM, None)

        r = bd.escolher(origem_explicita=str(c.raiz / "nao existe"))
        igual(r["codigo"], bd.SAIU_ORIGEM_INVALIDA, "pasta inexistente é erro")
        r = bd.escolher(origem_explicita=str(c.documentos))
        igual(r["codigo"], bd.SAIU_NADA, "pasta sem .fbk é erro")
        ok("arquivo" not in r,
           "e a origem explícita NÃO cai de volta na canônica")

    # ── Validar de verdade ───────────────────────────────────────────────
    secao("Um .fbk não é um backup só por terminar em .fbk")
    with Cenario() as c:
        c.semear_dados()
        bom = gerar_fbk(c, c.canonica, idade_s=600)

        v = bd.validar(bom)
        ok(v["ok"], "o backup v2 passa")
        igual(v["versao"], 2, "reconhecido como formato 2")
        igual(v["cifra"], "AES-256-GCM", "com a cifra esperada")
        ok(v["bytes_cifrados"] > 0, "e com conteúdo cifrado de verdade")
        ok(v["autenticado"] is False,
           "sem afirmar autenticação — isso exige a frase, que aqui não há")

        antigo = gerar_v1(c, c.canonica)
        v = bd.validar(antigo)
        ok(not v["ok"], "o backup v1 é RECUSADO para envio")
        igual(v["versao"], 1, "identificado como v1")
        ok("em claro" in v["motivo"], "porque guarda dados em claro")
        ok(fbk.conferir(antigo, FRASE)["ok"],
           "e mesmo assim continua conferível localmente")
        r = bd.escolher()
        igual(Path(r["arquivo"]).name, bom.name,
              "o programa escolhe o v2 e ignora o v1")
        antigo.unlink()

        lixo = c.canonica / "FISCALE-backup-2026-01-01-0000.fbk"
        lixo.write_bytes(b"isto nao e um pacote")
        v = bd.validar(lixo)
        ok(not v["ok"], "arquivo que não é pacote é recusado")
        igual(v["versao"], 0, "e nem versão tem")
        r = bd.escolher()
        igual(Path(r["arquivo"]).name, bom.name,
              "e o programa cai no backup bom, mais antigo")
        ok(any(n == lixo.name for n, _ in r["recusados"]),
           "dizendo qual foi ignorado e por quê")
        lixo.unlink()

        mexido = c.canonica / "FISCALE-backup-2026-01-03-0000.fbk"
        bruto = bytearray(bom.read_bytes())
        i_n = bruto.find(b'"n":32768')
        ok(i_n > 0, "achei o parâmetro do scrypt no cabeçalho")
        bruto[i_n:i_n + 9] = b'"n":16384'
        mexido.write_bytes(bytes(bruto))
        try:
            fbk.conferir(mexido, FRASE)
            ok(False, "a abertura do cabeçalho mexido deveria falhar")
        except (fbk.FraseIncorreta, fbk.BackupInvalido):
            ok(True, "cabeçalho mexido não abre — ele é AAD")
        mexido.unlink()

        interrompido = c.canonica / "FISCALE-backup-2026-01-06-0000.fbk.parcial"
        interrompido.write_bytes(bom.read_bytes())
        v = bd.validar(interrompido)
        ok(not v["ok"], "pacote .parcial é recusado, mesmo sendo v2 válido")
        ok("INTERROMPIDO" in v["motivo"], "dizendo que não terminou")
        ok(not any(f.name == interrompido.name for f in bd.fbks(c.canonica)),
           "e ele nem entra na lista de candidatos")
        interrompido.unlink()

        curto = c.canonica / "FISCALE-backup-2026-01-05-0000.fbk"
        curto.write_bytes(bom.read_bytes()[:200])
        ok(not bd.validar(curto)["ok"], "pacote truncado é recusado")
        curto.unlink()

        guardado = bom.read_bytes()
        bom.unlink()
        so_lixo = c.canonica / "FISCALE-backup-2026-01-04-0000.fbk"
        so_lixo.write_bytes(b"nada")
        r = bd.escolher()
        igual(r["codigo"], bd.SAIU_NADA, "sem nenhum válido, para")
        ok("NENHUM passou na conferência" in r["erro"],
           "dizendo que nenhum passou")
        codigo, texto = falas(bd.executar, ensaio=True)
        ok("ENSAIO" not in texto and "Enviado" not in texto,
           "e não finge ter ensaiado nem enviado")
        so_lixo.unlink()
        bom.write_bytes(guardado)

    # ── OneDrive ─────────────────────────────────────────────────────────
    secao("OneDrive ligado · o diagnóstico enxerga as duas Áreas de Trabalho")
    with Cenario(com_onedrive=True) as c:
        ok(" " in c.mesa.name and "Á" in c.mesa.name,
           "a pasta de teste tem espaço E acento, como a de verdade")
        rotulos = [r for r, _ in bd.legados()]
        ok("Área de Trabalho (Windows)" in rotulos
           and "Área de Trabalho (literal)" in rotulos,
           "as duas entram na lista de legados")
        c.semear_dados()
        gerar_fbk(c, c.mesa)
        codigo, texto = falas(bd.relatorio_legados)
        ok(str(c.mesa) in texto, "e o backup dentro do OneDrive é listado")

    secao("OneDrive desligado · as duas Áreas de Trabalho são a mesma")
    with Cenario(com_onedrive=False) as c:
        igual(c.mesa, c.literal, "as duas pastas coincidem")
        pastas = [os.path.normcase(str(p)) for _, p in bd.legados()]
        igual(len(pastas), len(set(pastas)), "e ela entra UMA vez só na lista")

    # ── Nome com espaço e acento ─────────────────────────────────────────
    secao("Nome de arquivo com espaço e acento")
    with Cenario() as c:
        c.semear_dados()
        gerado = gerar_fbk(c, c.canonica)
        estranho = c.canonica / "Backup do Escritório — Março 2026.fbk"
        gerado.rename(estranho)
        r = bd.escolher()
        igual(r["codigo"], bd.SAIU_OK, "acha arquivo com espaço e acento")
        igual(Path(r["arquivo"]).name, estranho.name, "com o nome intacto")
        c.destino.mkdir(parents=True)
        codigo, texto = falas(bd.executar, ensaio=False)
        igual(codigo, bd.SAIU_OK, "e copia sem se perder")
        ok((c.destino / estranho.name).exists(),
           "chegando com o nome preservado")

    # ── Mais recente pela data REAL ──────────────────────────────────────
    secao("O mais recente é pela data do arquivo, não pelo nome")
    with Cenario() as c:
        c.semear_dados()
        velho = gerar_fbk(c, c.canonica, idade_s=90 * 86400)
        velho.rename(velho.with_name("FISCALE-backup-2099-12-31-2359.fbk"))
        novo = gerar_fbk(c, c.canonica, idade_s=10)
        novo.rename(novo.with_name("FISCALE-backup-2000-01-01-0000.fbk"))
        r = bd.escolher()
        igual(Path(r["arquivo"]).name, "FISCALE-backup-2000-01-01-0000.fbk",
              "escolhe pelo mtime, mesmo com o nome dizendo outra coisa")
        igual(r["irmaos"], 2, "e conta quantos havia na pasta")

    # ── Nenhum arquivo ───────────────────────────────────────────────────
    secao("Nenhum .fbk · falha clara, e nunca 'concluído'")
    with Cenario() as c:
        codigo, texto = falas(bd.executar, ensaio=False)
        igual(codigo, bd.SAIU_NADA, "código de saída 'nada'")
        ok("[ERRO]" in texto, "a saída é um erro")
        ok("Concluido" not in texto and "Concluído" not in texto
           and "Enviado" not in texto,
           "NUNCA diz concluído nem enviado quando não achou nada")

    # ── Falha de destino ─────────────────────────────────────────────────
    secao("Destino indisponível · o Drive não está montado")
    with Cenario() as c:
        c.semear_dados()
        gerar_fbk(c, c.canonica)
        codigo, texto = falas(bd.executar, ensaio=False)
        igual(codigo, bd.SAIU_DESTINO, "devolve o código de destino")
        ok("[ERRO]" in texto and "destino" in texto.lower(),
           "e diz que o problema é o destino")
        ok("Enviado" not in texto, "sem dizer que enviou")

    # ── Ensaio e cópia real ──────────────────────────────────────────────
    secao("Ensaio não escreve; cópia real confere byte a byte")
    with Cenario() as c:
        c.semear_dados()
        origem = gerar_fbk(c, c.canonica)
        codigo, texto = falas(bd.executar, ensaio=True)
        igual(codigo, bd.SAIU_OK, "o ensaio termina bem")
        ok("ENSAIO" in texto and "nada foi escrito" in texto,
           "e diz em voz alta que não escreveu")
        for campo in ("ORIGEM", "DESTINO", "ARQUIVO", "TAMANHO", "DATA",
                      "SHA-256", "FORMATO", "CIFRADO"):
            ok(campo in texto, "o relatório traz %s" % campo)
        ok(not c.destino.exists(), "o ensaio não criou nem o destino")

        c.destino.mkdir(parents=True)
        codigo, texto = falas(bd.executar, ensaio=False)
        igual(codigo, bd.SAIU_OK, "a cópia real termina bem")
        final = c.destino / origem.name
        ok(final.exists(), "o arquivo chegou")
        igual(bd.sha256(final), bd.sha256(origem), "o SHA-256 confere")
        ok(not list(c.destino.glob("*.parcial")), "sem .parcial sobrando")
        ok("Enviado e conferido" in texto, "e só aí diz que enviou")
        codigo2, texto2 = falas(bd.executar, ensaio=False)
        ok("Já estava lá" in texto2, "rodar de novo reconhece que já está lá")

    # ── O que o backup não pode levar ────────────────────────────────────
    secao("O .fbk não leva o que não deve")
    with Cenario() as c:
        c.semear_dados()
        for lixo in ("tentativas.db", "auditoria.db", "sessoes.db",
                     ".pimenta-login", "elo_sync.db", "fiscale.log"):
            (c.dados / lixo).write_bytes(b"nao pode viajar")
        (c.dados / "temporario.tmp").write_bytes(b"x")
        (c.dados / "backups").mkdir(exist_ok=True)
        (c.dados / "backups" / "antigo.fbk").write_bytes(b"backup anterior")
        pacote = gerar_fbk(c, c.canonica)
        # O v2 não é um ZIP por fora: só se olha dentro depois de decifrar.
        with fbk._aberto_v2(pacote, FRASE) as interno:
            with zipfile.ZipFile(interno) as z:
                dentro = {n.rsplit("/", 1)[-1].lower() for n in z.namelist()}
        for proibido in ("tentativas.db", "auditoria.db", "sessoes.db",
                         ".pimenta-login", "elo_sync.db", "fiscale.log",
                         "temporario.tmp"):
            ok(proibido not in dentro, "%s ficou de fora" % proibido)
        ok("antigo.fbk" not in dentro,
           "backup anterior não entra dentro do backup novo")
        ok(not any(n.endswith(".fbk") for n in dentro),
           "nenhum .fbk dentro do .fbk")

    # ── O destino padrão da tela ─────────────────────────────────────────
    secao("Home > Backup salva na pasta canônica por padrão")
    servidor = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8")
    sem_comentario = "\n".join(l for l in servidor.splitlines()
                               if not l.strip().startswith("#"))

    i = sem_comentario.index('if rota == "/api/backup/criar":')
    criar = sem_comentario[i:sem_comentario.index(
        'if rota == "/api/backup/inspecionar"', i)]
    ok("backup_drive.canonica()" in criar,
       "sem pasta escolhida, o backup vai para a canônica")
    ok("pastas_windows.area_de_trabalho()" not in criar,
       "e NÃO mais para a Área de Trabalho")
    ok('d.get("pasta")' in criar, "mas a pasta escolhida continua sendo lida")
    ok(criar.index('d.get("pasta")') < criar.index("backup_drive.canonica()"),
       "e é lida ANTES de o padrão entrar — a escolha vence")

    j = sem_comentario.index('if rota == "/api/backup/situacao":')
    situacao = sem_comentario[j:sem_comentario.index(
        'if rota == "/api/diagnostico"', j)]
    ok('"pasta_padrao"' in situacao and "backup_drive.canonica()" in situacao,
       "a tela recebe a pasta padrão do servidor")
    ok('"area_de_trabalho"' in situacao,
       "e continua recebendo a Área de Trabalho, como alternativa")

    tela = (RAIZ / "web" / "backup.html").read_text(encoding="utf-8")
    ok("SIT.pasta_padrao || SIT.area_de_trabalho" in tela,
       "a tela preenche com a pasta padrão, caindo na Área de Trabalho se faltar")
    ok("btnMesa" in tela and "btnPadrao" in tela,
       "e oferece os dois caminhos por botão")
    depois_do_campo = tela.split('id="pasta"')[1][:160]
    ok("readonly" not in depois_do_campo and "disabled" not in depois_do_campo,
       "o campo continua editável — qualquer outra pasta é aceita")
    ok("procura <b>só aqui</b>" in tela,
       "a tela explica que o envio ao Drive só olha essa pasta")
    for classe in (".dica{", ".btn-mini{"):
        ok(classe in tela, "a classe %s existe no CSS desta tela" % classe)

    arv_srv = ast.parse(servidor)
    chamadas_srv = {ast.unparse(n.func) for n in ast.walk(arv_srv)
                    if isinstance(n, ast.Call)}
    ok("backup_drive.canonica" in chamadas_srv,
       "o servidor obtém a pasta canônica pela função, não por texto")
    embutidos = [n.value for n in ast.walk(arv_srv)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and "backups" in n.value
                 and ("/" in n.value or chr(92) in n.value)]
    igual(embutidos, [],
          "e nenhum caminho de backup está escrito à mão no servidor")

    # ── O .bat delega ────────────────────────────────────────────────────
    secao("O .bat delega e respeita o código de saída")
    bat = (RAIZ / "backup_para_drive.bat").read_text(encoding="utf-8")
    codigo_bat = "\n".join(l for l in bat.splitlines()
                           if not l.strip().lower().startswith("rem"))
    ok("backup_drive.py" in codigo_bat, "o .bat chama o módulo")
    ok("ERRORLEVEL" in codigo_bat and "RESULTADO" in codigo_bat,
       "e guarda o código de saída")
    i_ok = codigo_bat.index(":fimok")
    ok("Concluido" in codigo_bat[i_ok:] and "Concluido" not in codigo_bat[:i_ok],
       "o 'Concluido' só existe depois do desvio de sucesso")
    ok('if "%RESULTADO%"=="0" goto fimok' in codigo_bat,
       "o desvio depende do código de saída")
    for esperado in ('"3"', '"4"', '"5"'):
        ok(esperado in codigo_bat, "o .bat explica o código %s" % esperado)
    ok('"2"' not in codigo_bat,
       "e não fala mais do código 2, que deixou de existir")
    ok("MESA=" not in codigo_bat,
       "a variável MESA sumiu — o .bat não adivinha mais pasta nenhuma")
    ok("--localizar-legados" in bat,
       "e aponta o modo de diagnóstico para quem tem backup antigo")

    secao("Exclusões do pacote, na fonte")
    fonte_bk = (RAIZ / "fiscale_backup.py").read_text(encoding="utf-8")
    trecho = fonte_bk[fonte_bk.index("ARQUIVOS_FORA"):
                      fonte_bk.index("SUFIXOS_FORA")]
    for arquivo in ('"tentativas.db"', '".pimenta-login"', '"auditoria.db"',
                    '"sessoes.db"'):
        ok(arquivo in trecho, "%s está fora do pacote" % arquivo)

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
