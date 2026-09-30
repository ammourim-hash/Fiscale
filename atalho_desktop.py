#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atalho do Fiscale na Área de Trabalho, criado no primeiro uso.

    from atalho_desktop import garantir
    garantir(raiz_de_dados)      # cria uma vez; nas próximas, não faz nada

POR QUE ISTO EXISTE
    Quem recebe o Fiscale portátil extrai um ZIP e fica com uma pasta. Se
    ninguém criar o atalho, o caminho de volta é lembrar onde a pasta ficou —
    e no dia seguinte a pessoa procura "Fiscale" no menu Iniciar e não acha,
    porque não foi instalado nada.

UMA VEZ, E SÓ UMA
    A marca fica em `<dados>/.atalho-criado`. Se o usuário apagar o atalho de
    propósito, ele NÃO volta no próximo início: apagar é uma decisão, e
    recriar seria discutir com o dono da máquina.

ONDE, DE VERDADE
    Na Área de Trabalho que a pessoa VÊ — `pastas_windows.area_de_trabalho()`.
    Com o OneDrive ligado, `~/Desktop` é outra pasta, e o atalho iria para um
    lugar invisível. Já aconteceu aqui com dois backups.

SÓ NA INSTALAÇÃO DE VERDADE (16/09/2026)
    Em 14/09 um servidor de TESTE, subido de uma árvore exportada para uma
    pasta temporária, achou a Área de Trabalho sem atalho e criou um
    `Fiscale.lnk` apontando para o `iniciar.bat` dessa árvore — código antigo
    que, clicado, abriria a pasta de dados REAL. Por isso `garantir()` não
    toca a Área de Trabalho quando:
      - `FISCALE_SEM_ATALHO=1`;
      - `FISCALE_TESTE_PROIBIR_RAIZ_REAL` está ligada (é teste);
      - `FISCALE_DADOS` aponta para outra pasta que não a padrão desta
        instalação (teste, homologação, instância paralela);
      - o próprio programa está dentro da pasta temporária do Windows
        (árvore exportada para teste, ZIP aberto sem extrair).
    O portátil de verdade continua criando: nele a pasta padrão é a `dados`
    ao lado do programa, e ninguém define `FISCALE_DADOS`.

COMO
    `.lnk` de verdade, pelo `WScript.Shell` via PowerShell — assim ele leva o
    ícone do Fiscale e abre o lançador certo. Sem dependência nova: não há
    `pywin32` no runtime portátil, e não vale acrescentar 10 MB por um atalho.
    Se o PowerShell não responder, cai para um `.url`, que qualquer Windows
    abre.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pastas_windows

NOME = "Fiscale"
MARCA = ".atalho-criado"
VAR_SEM_ATALHO = "FISCALE_SEM_ATALHO"
# Mesmo nome de `fiscale_dados.VAR_PROIBIR_RAIZ_REAL`; repetido aqui para a
# trava valer mesmo que aquele módulo não possa ser importado.
VAR_TESTE = "FISCALE_TESTE_PROIBIR_RAIZ_REAL"


def _raiz_do_app() -> Path:
    """A pasta onde o Fiscale está instalado (onde vive o `fiscale_server`)."""
    return Path(__file__).resolve().parent


def lancador() -> Path | None:
    """O arquivo que ABRE o Fiscale nesta instalação.

    No portátil é `Fiscale.bat`, uma pasta acima de `app/`. No projeto é o
    `iniciar.bat`, ao lado. Apontar o atalho para o `.py` abriria o editor
    de código de quem tem um associado — o `.bat` é o que sempre funciona.
    """
    app = _raiz_do_app()
    for cand in (app.parent / "Fiscale.bat",      # portátil: app/ é filha
                 app / "Fiscale.bat",
                 app / "iniciar.bat"):
        if cand.is_file():
            return cand
    return None


def icone() -> str:
    ico = _raiz_do_app() / "fiscale.ico"
    return str(ico) if ico.is_file() else ""


def _criar_lnk(destino: Path, alvo: Path, ico: str) -> bool:
    """`.lnk` pelo WScript.Shell. Devolve se conseguiu."""
    if os.name != "nt":
        return False
    partes = [
        "$s = New-Object -ComObject WScript.Shell",
        "$a = $s.CreateShortcut(%s)" % _ps(str(destino)),
        "$a.TargetPath = %s" % _ps(str(alvo)),
        "$a.WorkingDirectory = %s" % _ps(str(alvo.parent)),
        "$a.Description = 'Fiscale - sistema fiscal'",
        "$a.WindowStyle = 7",          # começa minimizado: o .bat só dispara
    ]
    if ico:
        partes.append("$a.IconLocation = %s" % _ps(ico))
    partes.append("$a.Save()")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", "; ".join(partes)],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and destino.is_file()
    except Exception:
        return False


def _ps(texto: str) -> str:
    """Literal de string do PowerShell — aspas simples, dobrando as internas."""
    return "'" + texto.replace("'", "''") + "'"


def _criar_url(destino: Path, alvo: Path, ico: str) -> bool:
    """Queda: um `.url`. Feio, mas abre em qualquer Windows."""
    try:
        linhas = ["[InternetShortcut]", "URL=file:///" +
                  str(alvo).replace("\\", "/")]
        if ico:
            linhas += ["IconFile=" + ico, "IconIndex=0"]
        destino.write_text("\r\n".join(linhas) + "\r\n", encoding="utf-8", newline="")
        return destino.is_file()
    except Exception:
        return False


def _normal(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _pastas_temporarias() -> list[str]:
    cands = [tempfile.gettempdir(), os.environ.get("TEMP"), os.environ.get("TMP")]
    saida = []
    for c in cands:
        if c:
            try:
                saida.append(_normal(Path(c).resolve()))
            except OSError:
                saida.append(_normal(c))
    return saida


def _app_em_pasta_temporaria() -> bool:
    """O programa está rodando de dentro da pasta temporária do Windows?"""
    try:
        app = _normal(_raiz_do_app().resolve())
    except OSError:
        app = _normal(_raiz_do_app())
    return any(app == t or app.startswith(t.rstrip(os.sep) + os.sep)
               for t in _pastas_temporarias())


def _dados_fora_do_padrao() -> bool:
    """`FISCALE_DADOS` definida para outra pasta que não a padrão desta instalação.

    Na dúvida (módulo de dados indisponível, caminho que não resolve), a
    resposta é SIM: é o lado seguro — no pior caso falta um atalho."""
    env = (os.environ.get("FISCALE_DADOS") or "").strip()
    if not env:
        return False
    try:
        import fiscale_dados
        return _normal(Path(env).expanduser().resolve()) != _normal(fiscale_dados.raiz_padrao())
    except Exception:
        return True


def bloqueio() -> str:
    """Motivo para NÃO tocar a Área de Trabalho, ou '' se esta é a instalação real."""
    if os.environ.get(VAR_SEM_ATALHO) == "1":
        return "desligado_por_FISCALE_SEM_ATALHO"
    if os.environ.get(VAR_TESTE):
        return "ambiente_de_teste"
    if _dados_fora_do_padrao():
        return "pasta_de_dados_nao_padrao"
    if _app_em_pasta_temporaria():
        return "instalacao_em_pasta_temporaria"
    return ""


def garantir(dados_dir, forcar: bool = False) -> dict:
    """Cria o atalho no primeiro uso. Nunca levanta exceção.

    Falhar em criar um atalho não pode impedir o Fiscale de abrir — é
    conveniência, não requisito. Por isso tudo aqui devolve estado em vez de
    estourar.
    """
    resultado = {"criado": False, "motivo": "", "caminho": ""}
    try:
        # PRIMEIRO de tudo, antes de olhar a Área de Trabalho ou gravar a
        # marca: instância de teste/homologação não mexe na mesa de ninguém.
        motivo = bloqueio()
        if motivo:
            resultado["motivo"] = motivo
            return resultado
        marca = Path(dados_dir) / MARCA
        if marca.exists() and not forcar:
            resultado["motivo"] = "ja_feito"
            return resultado
        if os.name != "nt":
            resultado["motivo"] = "fora_do_windows"
            return resultado

        alvo = lancador()
        if alvo is None:
            resultado["motivo"] = "sem_lancador"
            return resultado

        mesa = pastas_windows.area_de_trabalho()
        if not mesa.is_dir():
            resultado["motivo"] = "sem_area_de_trabalho"
            return resultado

        lnk = mesa / (NOME + ".lnk")
        url = mesa / (NOME + ".url")
        if (lnk.exists() or url.exists()) and not forcar:
            # Já existe um atalho — de uma instalação anterior, ou feito à
            # mão. Não sobrescreve: marca como resolvido e sai.
            marca.parent.mkdir(parents=True, exist_ok=True)
            marca.write_text("atalho ja existia\n", encoding="utf-8")
            resultado.update(motivo="ja_existia", caminho=str(
                lnk if lnk.exists() else url))
            return resultado

        ico = icone()
        if _criar_lnk(lnk, alvo, ico):
            feito = lnk
        elif _criar_url(url, alvo, ico):
            feito = url
        else:
            resultado["motivo"] = "falhou"
            return resultado

        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(str(feito) + "\n", encoding="utf-8")
        resultado.update(criado=True, motivo="criado", caminho=str(feito))
        return resultado
    except Exception as e:
        resultado["motivo"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        return resultado


# ── Atalho de REDE (o que serve nas outras máquinas) ────────────────────────
# O atalho da Área de Trabalho aponta para o `iniciar.bat` DESTA pasta, nesta
# máquina. Copiá-lo para o Drive não ajuda ninguém: no computador do lado esse
# caminho não existe, e o Fiscale não é para ser instalado lá — as outras
# máquinas do escritório abrem o servidor pelo NAVEGADOR.
#
# Por isso o atalho que vai para o Drive é outro: um `.url` para
# `http://<maquina>:<porta>`. Pelo NOME da máquina, nunca pelo IP — o IP vem de
# DHCP e muda sozinho, e no dia em que mudar o atalho quebra sem explicação.


def endereco_de_rede() -> str:
    """`http://<maquina>:<porta>` — o endereço que as outras máquinas usam."""
    try:
        import diagnostico_instalacao as diag
        return (diag.endereco_do_escritorio() or {}).get("url") or ""
    except Exception:
        import socket
        porta = os.environ.get("FISCALE_PORT") or "8777"
        try:
            maquina = socket.gethostname()
        except Exception:
            return ""
        return f"http://{maquina}:{porta}" if maquina else ""


def atalho_de_rede(pasta, nome: str = NOME, url: str = "") -> dict:
    """Grava em `pasta` um `.url` que abre o Fiscale pelo navegador.

    Serve para deixar no Drive (ou num pendrive): qualquer máquina do
    escritório copia para a própria Área de Trabalho e passa a abrir o
    Fiscale sem instalar nada. Não leva segredo nenhum — só o nome da máquina
    e a porta, que quem está na rede já sabe.
    """
    resultado = {"criado": False, "motivo": "", "caminho": "", "url": ""}
    try:
        endereco = url or endereco_de_rede()
        if not endereco:
            resultado["motivo"] = "sem_endereco"
            return resultado
        destino = Path(pasta)
        if not destino.is_dir():
            resultado["motivo"] = "pasta_inexistente"
            return resultado
        arq = destino / (nome + ".url")
        # SEM IconFile, de propósito. O `fiscale.ico` mora nesta máquina, e este
        # atalho existe justamente para ser copiado para outras: lá o caminho
        # não existe e o Windows desenha um quadrado branco, que é pior do que
        # o ícone padrão de atalho da internet. Um atalho que viaja não leva
        # caminho local nenhum.
        linhas = ["[InternetShortcut]", "URL=" + endereco]
        # CRLF: o .url é lido pelo shell do Windows, que espera o fim de linha
        # dele. Com LF puro o atalho abre, mas o ícone às vezes não pega.
        # `newline=""` desliga a tradução de fim de linha do Windows. Sem
        # ela o "\n" que eu escrevo vira "\r\n" de novo e o arquivo sai
        # com "\r\r\n" — o Windows tolera, mas é lixo gravado de graça.
        arq.write_text("\r\n".join(linhas) + "\r\n", encoding="utf-8", newline="")
        resultado.update(criado=True, motivo="criado",
                         caminho=str(arq), url=endereco)
        return resultado
    except Exception as e:
        resultado["motivo"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        return resultado


if __name__ == "__main__":
    import fiscale_dados as fd
    if "--rede" in sys.argv:
        # atalho_desktop.py --rede "G:\Meu Drive\Amorim\FISCALE 2026"
        alvos = [a for a in sys.argv[1:] if not a.startswith("--")]
        for pasta in (alvos or [str(pastas_windows.area_de_trabalho())]):
            print(atalho_de_rede(pasta))
    else:
        print(garantir(fd.raiz(), forcar="--forcar" in sys.argv))
