#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exportar_backup.py — cria o FISCALE-backup-AAAA-MM-DD-HHMM.fbk pela linha de comando.

    python exportar_backup.py                     salva na Área de Trabalho
    python exportar_backup.py D:\\pendrive         salva na pasta indicada
    python exportar_backup.py --sem-certificados   pacote sem cofre, sem frase

A frase-senha é pedida na hora e digitada às escondidas. Ela NÃO é guardada em
lugar nenhum: é ela que abre os certificados na outra máquina, e perdê-la
significa perder os certificados DO PACOTE — as empresas continuam lá.

Existe porque nem toda máquina vai ter a tela aberta na hora de migrar, e
porque um backup que só funciona clicando é um backup que não entra em rotina.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fiscale_backup as bk
import fiscale_dados as fd


def _mb(n):
    return f"{n / 1024 / 1024:.1f} MB"


def _pedir_frase() -> str:
    print("\n  A frase-senha protege os certificados dentro do pacote.")
    print("  Use algo que você consiga lembrar e ninguém consiga adivinhar.")
    print("  Ela NÃO fica guardada: sem ela, os certificados do backup não abrem.\n")
    while True:
        a = getpass.getpass("  Frase-senha: ")
        if len(a) < 8:
            print("  Curta demais — use ao menos 8 caracteres.\n")
            continue
        b = getpass.getpass("  Repita: ")
        if a != b:
            print("  As duas não bateram. Vamos de novo.\n")
            continue
        return a


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    sem_certs = "--sem-certificados" in argv

    raiz = fd.raiz()
    destino = Path(args[0]) if args else (Path.home() / "Desktop")
    if not destino.exists() and not destino.suffix:
        destino.mkdir(parents=True, exist_ok=True)

    print("=" * 66)
    print("  FISCALE — exportar backup")
    print("=" * 66)
    print(f"  Dados em : {raiz}")
    print(f"  Salvando : {destino}")

    tem_pfx = any((raiz / "certs").glob("*.pfx")) if (raiz / "certs").is_dir() else False
    frase = None
    if tem_pfx and not sem_certs:
        frase = _pedir_frase()
    elif tem_pfx:
        print("\n  [--sem-certificados] Os certificados NÃO vão no pacote.")
        print("  As empresas continuam cadastradas; na outra máquina você")
        print("  precisará enviar os .pfx de novo.")

    def progresso(i, total):
        pct = i * 100 // max(total, 1)
        print(f"\r  empacotando... {pct:3d}%  ({i}/{total})", end="", flush=True)

    try:
        if sem_certs:
            import shutil, tempfile
            espelho = Path(tempfile.mkdtemp(prefix="fbk_sem_certs_"))
            for p in raiz.rglob("*"):
                if p.is_file() and bk._entra_no_backup(p, raiz):
                    alvo = espelho / p.relative_to(raiz)
                    alvo.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, alvo)
            r = bk.exportar(destino, None, raiz=espelho, progresso=progresso)
            shutil.rmtree(espelho, ignore_errors=True)
        else:
            r = bk.exportar(destino, frase, raiz=raiz, progresso=progresso)
    except ValueError as e:
        print(f"\n\n  ERRO: {e}")
        return 2
    except Exception as e:
        print(f"\n\n  ERRO inesperado: {e.__class__.__name__}: {e}")
        return 2

    print("\r" + " " * 50, end="\r")
    print("\n  PRONTO.\n")
    print(f"  Arquivo    : {r['arquivo']}")
    print(f"  Tamanho    : {_mb(r['bytes'])}  (de {_mb(r['bytes_originais'])} em disco)")
    print(f"  Arquivos   : {r['arquivos']}")
    print(f"  Empresas   : {r['empresas_cadastradas']} cadastradas · "
          f"{r['empresas_com_documentos']} com documentos")
    print(f"  Certificados no cofre : {r['certificados_no_cofre']}")
    print(f"  Estado de sincronismo : {r['estado_sincronismo_qtd']} arquivo(s) "
          f"(ultNSU/ultimoNSU preservados)")
    if r["senhas_dpapi_removidas"]:
        print(f"\n  {r['senhas_dpapi_removidas']} senha(s) de certificado NÃO foram para o pacote")
        print("  (são do Windows desta máquina). Na outra máquina as empresas")
        print("  aparecem como 'Aguardando senha' — basta informar a senha de")
        print("  cada certificado que você for usar. Nada precisa ser recadastrado.")
    if r["senhas_portal_removidas"]:
        print(f"\n  {r['senhas_portal_removidas']} senha(s) de portal de plano de saúde foram")
        print("  removidas do pacote por segurança. Reinforme-as na outra máquina.")
    print("\n" + "=" * 66)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n  Cancelado.")
        sys.exit(1)
