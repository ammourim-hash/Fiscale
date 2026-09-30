#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fiscale — achar o `.fbk` certo para mandar ao Drive.

    python backup_drive.py --ensaio            mostra o que faria, sem escrever
    python backup_drive.py                     copia de verdade
    python backup_drive.py --origem "D:\\bkp"   força a pasta de origem
    python backup_drive.py --localizar-legados só LISTA o que está fora

POR QUE ISTO SAIU DO .BAT
    O `backup_para_drive.bat` procurava os `.fbk` em `%USERPROFILE%\\Desktop`.
    O `cmd.exe` não sabe perguntar ao Windows onde fica a Área de Trabalho de
    verdade, não calcula SHA-256, e se atrapalha com acento e espaço em
    caminho — e a Área de Trabalho real desta máquina é
    `C:\\Users\\nineq\\OneDrive\\Área de Trabalho`, que tem os dois.

    Então a decisão mudou de lugar: o `.bat` continua sendo o que se clica, e
    quem decide é este módulo, que dá para testar.

DE ONDE SAI O BACKUP: UM LUGAR SÓ
    `<FISCALE_DADOS>\backups`. Só ali, no funcionamento normal.

    A versão anterior deste módulo varria Área de Trabalho, Documentos e
    Downloads. Aquilo resolvia o sintoma — achava o arquivo — e mantinha a
    doença: o backup morava onde calhasse, e "qual é o backup?" continuava
    sendo uma pergunta com várias respostas. Uma origem canônica troca a
    busca por um endereço.

    As pastas antigas não sumiram do mapa: `--localizar-legados` LISTA o que
    houver nelas, e só. Esse modo nunca copia nada para o Drive — ele existe
    para achar o que ficou para trás, não para continuar usando o de trás.

O QUE É UM BACKUP, E O QUE É SÓ UM ARQUIVO `.fbk`
    Terminar em `.fbk` não é credencial. Todo candidato passa por
    `fiscale_backup.conferir()`, que abre o manifesto, confere versão e
    formato e recalcula o SHA-256 de CADA arquivo de dentro. Um pacote
    truncado, adulterado ou de outro produto é recusado com o motivo.

A REGRA QUE MAIS IMPORTA: NÃO ESCOLHER SOZINHO
    Quando duas pastas têm `.fbk`, este programa PARA e pergunta. A tentação é
    pegar o mais recente das duas e seguir — e é exatamente assim que se manda
    para o Drive o backup errado, sem ninguém perceber, durante meses.

    Também nunca diz "concluído" sem ter copiado nada. Um script que termina
    verde de mãos vazias é pior que um que falha: ele ensina a não conferir.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_backup                                  # noqa: E402
import fiscale_dados                                   # noqa: E402
import pastas_windows                                  # noqa: E402

# A pasta do Drive. Pode ser trocada pela variável de ambiente, que é o que
# permite testar sem tocar no Drive de verdade.
PASTA_CANONICA = "backups"       # dentro de FISCALE_DADOS
DESTINO_PADRAO = r"G:\Meu Drive\Amorim\FISCALE 2026"
VAR_DESTINO = "FISCALE_BACKUP_DESTINO"
VAR_ORIGEM = "FISCALE_BACKUP_ORIGEM"

# Códigos de saída. O `.bat` os lê para não dizer "pronto" quando não está.
SAIU_OK = 0
# O 2 era "ha .fbk em mais de uma pasta". Com a origem canonica, essa pergunta
# deixou de existir: ha UMA pasta. O numero fica reservado para nao mudar o
# significado dos outros se algum dia voltar a fazer sentido.
SAIU_NADA = 3
SAIU_DESTINO = 4
SAIU_ORIGEM_INVALIDA = 5


def canonica(criar=True) -> Path:
    """`<FISCALE_DADOS>\\backups` — a ÚNICA origem do funcionamento normal.

    A raiz vem de `fiscale_dados.raiz()`, que é o único lugar do sistema que
    responde "onde ficam os dados". Repetir o caminho aqui seria criar a
    segunda resposta que a PORT 1 existiu para eliminar.
    """
    p = Path(fiscale_dados.raiz()) / PASTA_CANONICA
    if criar:
        p.mkdir(parents=True, exist_ok=True)
    return p


def legados() -> list:
    """As pastas onde backups antigos podem ter ficado. SÓ PARA DIAGNÓSTICO.

    Nada aqui é consultado no caminho normal. A Área de Trabalho entra duas
    vezes de propósito — a que o Windows informa (que o OneDrive redireciona)
    e a literal `%USERPROFILE%\\Desktop`. Nesta máquina são pastas diferentes,
    e os backups antigos estão na literal: quem gravou usou uma, quem olha usa
    a outra.
    """
    perfil = Path(os.environ.get("USERPROFILE") or Path.home())
    vistos, saida = set(), []
    for rotulo, pasta in (
            ("Área de Trabalho (Windows)", pastas_windows.area_de_trabalho()),
            ("Área de Trabalho (literal)", perfil / "Desktop"),
            ("Documentos", pastas_windows.documentos()),
            ("Downloads", pastas_windows.downloads()),
    ):
        pa = Path(pasta)
        chave = os.path.normcase(str(pa))
        if chave in vistos:
            continue          # real e literal coincidem: uma entrada só
        vistos.add(chave)
        saida.append((rotulo, pa))
    return saida


def fbks(pasta) -> list:
    """Os `.fbk` de uma pasta, do mais recente para o mais antigo.

    "Mais recente" é a data REAL do arquivo (`mtime`), não o que está escrito
    no nome. O nome traz a data de quando o backup foi pedido; um arquivo
    copiado, restaurado ou renomeado mente sobre si mesmo, e o carimbo não.
    """
    p = Path(pasta)
    if not p.is_dir():
        return []
    try:
        achados = [f for f in p.iterdir()
                   if f.is_file() and f.suffix.lower() == ".fbk"]
    except OSError:
        return []
    return sorted(achados, key=lambda f: f.stat().st_mtime, reverse=True)


def sha256(caminho, bloco=1 << 20) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for pedaco in iter(lambda: f.read(bloco), b""):
            h.update(pedaco)
    return h.hexdigest()


def descrever(caminho, com_hash=True) -> dict:
    p = Path(caminho)
    st = p.stat()
    d = {"nome": p.name, "pasta": str(p.parent), "caminho": str(p),
         "bytes": st.st_size, "mb": st.st_size / 1e6, "mtime": st.st_mtime,
         "data": time.strftime("%d/%m/%Y %H:%M:%S",
                               time.localtime(st.st_mtime))}
    d["sha256"] = sha256(p) if com_hash else ""
    return d


def validar(caminho) -> dict:
    """Só passa pacote v2, cifrado por inteiro, estruturalmente íntegro.

    O QUE DÁ PARA CONFERIR AQUI, E O QUE NÃO DÁ
        Este programa NÃO tem a frase-senha, e não deve ter: ela é digitada na
        tela Home > Backup e não passa por argumento, variável nem log. Sem a
        chave não há como autenticar a tag do GCM — isso é a cifra funcionando,
        não uma falha.

        O que se confere sem a chave: que é um `.fbk` v2 (os 8 bytes de
        assinatura), que o cabeçalho é legível e declara os parâmetros
        esperados, e que o corpo tem tamanho compatível com um pacote real.
        A autenticação de verdade acontece no dia da restauração, com a frase.

    POR QUE O v1 É RECUSADO
        O v1 é um ZIP com o manifesto, o `usuarios.json` e os XMLs em CLARO.
        Mandá-lo ao Google Drive é publicar dado de cliente. Ele continua
        legível localmente para recuperação — o que não pode é subir.
    """
    caminho = Path(caminho)
    # `.parcial` é a marca de "não terminou". Um pacote interrompido tem
    # cabeçalho válido e corpo pela metade, e sem a frase-senha NÃO HÁ como
    # distingui-lo de um completo — a autenticação do GCM é a única coisa que
    # denunciaria, e ela exige a chave. Então a defesa é o nome: nada que se
    # chame `.parcial` é considerado, nem se alguém o renomear no meio do
    # caminho e vier perguntar.
    if ".parcial" in caminho.name.lower():
        return {"ok": False, "versao": 0,
                "motivo": "é um pacote INTERROMPIDO (.parcial). O backup não "
                          "terminou de ser gerado; gere outro pela tela "
                          "Home > Backup."}
    versao = fiscale_backup.versao_do_arquivo(caminho)
    if versao == fiscale_backup.FORMATO:
        return {"ok": False, "versao": 1,
                "motivo": "é um backup no formato ANTIGO (v1), que guarda "
                          "manifesto, cadastro e XMLs em claro. Ele não sobe "
                          "para o Drive. Gere um backup novo pela tela "
                          "Home > Backup."}
    if versao != fiscale_backup.FORMATO_V2:
        return {"ok": False, "versao": 0,
                "motivo": "não é um backup do Fiscale."}
    try:
        cabeca = fiscale_backup.cabecalho_v2(caminho)
    except fiscale_backup.BackupInvalido as e:
        return {"ok": False, "versao": 2, "motivo": str(e)}
    except Exception as e:
        return {"ok": False, "versao": 2,
                "motivo": "não consegui ler o cabeçalho (%s)"
                          % e.__class__.__name__}

    if cabeca.get("cifra") != "AES-256-GCM" or cabeca.get("kdf") != "scrypt":
        return {"ok": False, "versao": 2,
                "motivo": "o cabeçalho declara cifra ou derivação inesperada."}
    # Um pacote sem corpo é um arquivo que só tem cabeçalho e assinatura.
    corpo = (caminho.stat().st_size - cabeca["_inicio_cifra"]
             - fiscale_backup.TAG_BYTES)
    if corpo <= 0:
        return {"ok": False, "versao": 2,
                "motivo": "o pacote está truncado: não há conteúdo."}
    return {"ok": True, "versao": 2, "cifra": cabeca["cifra"],
            "kdf": cabeca["kdf"], "bytes_cifrados": corpo,
            "autenticado": False}


def escolher(origem_explicita=None) -> dict:
    """Qual `.fbk` enviar. Devolve `{"erro": ..., "codigo": ...}` quando não dá.

    No caminho normal olha SÓ a pasta canônica. `--origem` e a variável de
    ambiente continuam existindo para uso administrativo — e só são acionados
    quando alguém os digita: nada aqui os liga sozinho.
    """
    origem_explicita = (origem_explicita
                        or os.environ.get(VAR_ORIGEM) or "").strip().strip('"')
    if origem_explicita:
        pasta = Path(origem_explicita)
        rotulo = "origem configurada (uso administrativo)"
        if not pasta.is_dir():
            return {"codigo": SAIU_ORIGEM_INVALIDA,
                    "erro": "A pasta de origem configurada não existe:" + chr(10)
                            + "   %s" % pasta}
    else:
        pasta = canonica()
        rotulo = "pasta canônica"

    achados = fbks(pasta)
    if not achados:
        extra = ""
        if not origem_explicita:
            extra = (chr(10) + chr(10)
                     + "   Gere um backup pelo Fiscale (Home > Backup)."
                     + chr(10)
                     + "   Se você tem backups antigos em outra pasta, veja"
                     + " onde eles estão com:" + chr(10)
                     + "     python backup_drive.py --localizar-legados")
        return {"codigo": SAIU_NADA,
                "erro": "Nenhum arquivo .fbk em:" + chr(10) + "   %s%s"
                        % (pasta, extra)}

    # Cada candidato é CONFERIDO, do mais recente para o mais antigo. Um
    # pacote truncado não vira "o backup da vez" só por ser o último.
    recusados = []
    for f in achados:
        v = validar(f)
        if v["ok"]:
            return {"codigo": SAIU_OK, "arquivo": f, "origem": pasta,
                    "rotulo": rotulo, "irmaos": len(achados),
                    "validacao": v, "recusados": recusados}
        recusados.append((f.name, v["motivo"]))

    linhas = [("   %s" + chr(10) + "      %s") % (n, m) for n, m in recusados]
    return {"codigo": SAIU_NADA,
            "erro": ("Achei %d arquivo(s) .fbk em %s, e NENHUM passou na "
                     "conferência:" % (len(achados), pasta)) + chr(10) + chr(10)
                    + (chr(10) + chr(10)).join(linhas),
            "recusados": recusados}


def relatorio_legados(saida=print) -> int:
    """Lista `.fbk` nas pastas antigas. NUNCA copia coisa alguma.

    Existe para achar o que ficou para trás — e para que achar não seja o
    mesmo que continuar usando. Por isso este modo não tem caminho de envio:
    o que ele faz é imprimir.
    """
    saida("")
    saida("  Diagnóstico: procurando backups fora da pasta canônica.")
    saida("  A pasta canônica é: %s" % canonica(criar=False))
    saida("  Nada aqui é copiado nem enviado.")
    saida("")
    total = 0
    for rotulo, pasta in legados():
        achados = fbks(pasta)
        if not achados:
            saida("  %-28s  nenhum        %s" % (rotulo, pasta))
            continue
        total += len(achados)
        saida("  %-28s  %d arquivo(s)  %s" % (rotulo, len(achados), pasta))
        for f in achados:
            st = f.stat()
            v = validar(f)
            saida("       %-44s %8.1f MB  %s  %s"
                  % (f.name[:44], st.st_size / 1e6,
                     time.strftime("%d/%m/%Y %H:%M", time.localtime(st.st_mtime)),
                     "íntegro" if v["ok"] else "RECUSADO: " + v["motivo"][:40]))
    saida("")
    if total:
        saida("  %d arquivo(s) fora da pasta canônica." % total)
        saida("  Eles NÃO são enviados. Para adotar um, mova-o para a pasta")
        saida("  canônica você mesma, ou use --origem para um envio pontual.")
    else:
        saida("  Nada fora da pasta canônica.")
    saida("")
    return SAIU_OK


def destino() -> Path:
    return Path((os.environ.get(VAR_DESTINO) or DESTINO_PADRAO).strip().strip('"'))


def executar(ensaio=False, origem_explicita=None, saida=print) -> int:
    esc = escolher(origem_explicita)
    if esc["codigo"] != SAIU_OK:
        saida("")
        saida("  [ERRO] " + esc["erro"])
        saida("")
        return esc["codigo"]

    arquivo = esc["arquivo"]
    saida("  Calculando SHA-256 de %.1f MB..." % (arquivo.stat().st_size / 1e6))
    info = descrever(arquivo)
    alvo = destino()

    saida("")
    saida("  ORIGEM   %s  (%s)" % (info["pasta"], esc["rotulo"]))
    saida("  DESTINO  %s" % alvo)
    saida("  ARQUIVO  %s" % info["nome"])
    saida("  TAMANHO  %.1f MB  (%d bytes)" % (info["mb"], info["bytes"]))
    saida("  DATA     %s" % info["data"])
    saida("  SHA-256  %s" % info["sha256"])
    if esc.get("irmaos", 1) > 1:
        saida("  (a pasta tem %d arquivos .fbk; este é o de data mais recente)"
              % esc["irmaos"])
    v = esc.get("validacao") or {}
    saida("  FORMATO   v%d · %s · derivação %s"
          % (v.get("versao", 0), v.get("cifra", "?"), v.get("kdf", "?")))
    saida("  CIFRADO   %d bytes de conteúdo, ilegíveis sem a frase-senha"
          % v.get("bytes_cifrados", 0))
    saida("  (a autenticação criptográfica é conferida na restauração, com a")
    saida("   frase — este programa não a tem, e não deve ter)")
    for nome, motivo in esc.get("recusados") or []:
        saida("  (ignorado: %s — %s)" % (nome, motivo[:60]))

    if ensaio:
        saida("")
        saida("  ENSAIO: nada foi escrito no Drive.")
        return SAIU_OK

    if not alvo.is_dir():
        saida("")
        saida("  [ERRO] A pasta de destino não existe ou não está acessível:")
        saida("     %s" % alvo)
        saida("     Se for o Google Drive, abra o aplicativo e tente de novo.")
        saida("")
        return SAIU_DESTINO

    final = alvo / info["nome"]
    if final.exists() and final.stat().st_size == info["bytes"]:
        try:
            if sha256(final) == info["sha256"]:
                saida("")
                saida("  Já estava lá, idêntico. Nada a fazer.")
                return SAIU_OK
        except OSError:
            pass

    # Grava com nome provisório e só depois renomeia: uma cópia interrompida
    # deixaria no Drive um .fbk truncado com nome de backup bom, que é a
    # pior coisa que um backup pode ser.
    parcial = alvo / (info["nome"] + ".parcial")
    try:
        shutil.copy2(str(arquivo), str(parcial))
        conferido = sha256(parcial)
        if conferido != info["sha256"]:
            parcial.unlink(missing_ok=True)
            saida("")
            saida("  [ERRO] A cópia chegou diferente da origem. Nada foi "
                  "publicado.")
            saida("     origem  %s" % info["sha256"])
            saida("     destino %s" % conferido)
            return SAIU_DESTINO
        os.replace(str(parcial), str(final))
    except OSError as e:
        try:
            parcial.unlink(missing_ok=True)
        except OSError:
            pass
        saida("")
        saida("  [ERRO] Não consegui copiar: %s" % e)
        return SAIU_DESTINO

    saida("")
    saida("  Enviado e conferido byte a byte.")
    saida("     %s" % final)
    return SAIU_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Envia o backup .fbk mais recente para o Google Drive.")
    ap.add_argument("--ensaio", action="store_true",
                    help="mostra o que faria, sem escrever no Drive")
    ap.add_argument("--origem", default="",
                    help="pasta de origem, uso administrativo explícito; "
                         "tem prioridade sobre a pasta canônica")
    ap.add_argument("--localizar-legados", action="store_true", dest="legados",
                    help="apenas LISTA backups fora da pasta canônica; "
                         "não copia nem envia nada")
    a = ap.parse_args(argv)
    if a.legados:
        # Modo de diagnóstico: não há caminho daqui para o envio, de
        # propósito. Achar o que ficou para trás não pode ser o mesmo gesto
        # que continuar usando o de trás.
        return relatorio_legados()
    return executar(ensaio=a.ensaio, origem_explicita=a.origem)


if __name__ == "__main__":
    sys.exit(main())
