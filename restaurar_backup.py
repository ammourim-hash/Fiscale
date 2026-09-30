#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
restaurar_backup.py — traz de volta uma instalação a partir de um .fbk.

    python restaurar_backup.py caminho\\FISCALE-backup-2026-08-11-2025.fbk
    python restaurar_backup.py arquivo.fbk --so-conferir     não escreve nada

O que ele NÃO faz: decidir por você. Se a pasta de destino já tiver dados, ele
para, mostra o que existe dos dois lados e pergunta. Substituir move o que
estava ali para uma pasta ao lado — apagar dado de contabilidade não é algo que
um programa deva fazer sozinho.

Feche o Fiscale antes de restaurar: com ele aberto, arquivos em uso não podem
ser trocados.
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


def _mostrar_manifesto(m: dict) -> None:
    r = m.get("resumo") or {}
    print(f"  Criado em  : {m.get('criado_em')}")
    print(f"  Origem     : {(m.get('origem') or {}).get('maquina') or '(não informada)'}")
    print(f"  Arquivos   : {r.get('arquivos')}  ({_mb(r.get('bytes_originais') or 0)})")
    print(f"  Empresas   : {r.get('empresas_cadastradas')} cadastradas · "
          f"{r.get('empresas_com_documentos')} com documentos")
    print(f"  Certificados no cofre : {r.get('certificados_no_cofre')}")
    print(f"  Estado de sincronismo : {r.get('estado_sincronismo_qtd')} arquivo(s)")
    est = r.get("estado_sincronismo") or {}
    nfe = [v.get("ultNSU") for v in est.values() if v.get("ultNSU")]
    if nfe:
        print(f"      NF-e ultNSU: {sorted(nfe)}")


def _perguntar(destino: dict, opcoes: dict) -> str | None:
    print("\n  " + "!" * 62)
    print(f"  JÁ EXISTEM DADOS EM {destino['raiz']}")
    print(f"  {destino['arquivos']} arquivo(s) · {destino['empresas']} empresa(s) cadastrada(s)")
    print("  " + "!" * 62 + "\n")
    for i, (chave, texto) in enumerate((("cancelar", opcoes["cancelar"]),
                                        ("mesclar", opcoes["mesclar"]),
                                        ("substituir", opcoes["substituir"])), 1):
        print(f"  [{i}] {chave.upper()}")
        for linha in _quebrar(texto, 62):
            print(f"      {linha}")
        print()
    escolha = input("  Digite 1, 2 ou 3: ").strip()
    return {"1": None, "2": "mesclar", "3": "substituir"}.get(escolha, None)


def _quebrar(texto: str, largura: int) -> list[str]:
    linhas, atual = [], ""
    for palavra in texto.split():
        if len(atual) + len(palavra) + 1 > largura:
            linhas.append(atual)
            atual = palavra
        else:
            atual = f"{atual} {palavra}".strip()
    if atual:
        linhas.append(atual)
    return linhas


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    so_conferir = "--so-conferir" in argv
    if not args:
        print("  Informe o arquivo .fbk. Ex.:")
        print("     python restaurar_backup.py C:\\backups\\FISCALE-backup-....fbk")
        return 2

    fbk = Path(args[0])
    raiz = Path(args[1]) if len(args) > 1 else fd.raiz()

    print("=" * 66)
    print("  FISCALE — restaurar instalação anterior")
    print("=" * 66)
    print(f"  Pacote     : {fbk.name}")
    print(f"  Destino    : {raiz}\n")

    try:
        man = bk.inspecionar(fbk)
    except bk.BackupInvalido as e:
        print(f"  ERRO: {e}")
        return 2
    _mostrar_manifesto(man)

    print("\n  Conferindo a integridade do pacote...")
    conf = bk.conferir(fbk)
    if not conf["ok"]:
        print(f"  ERRO: o pacote não passou na conferência ({conf['problemas_qtd']} problema(s)):")
        for p in conf["problemas"][:8]:
            print(f"     - {p}")
        print("\n  NÃO restaure este arquivo. Use outro backup.")
        return 2
    print(f"  OK — {conf['conferidos']} arquivo(s) conferidos por hash.")

    frase = None
    if man["_precisa_frase"]:
        if so_conferir:
            print("\n  (o pacote tem cofre; a frase seria pedida na restauração)")
        else:
            print(f"\n  O pacote traz {man['cofre']['arquivos']} certificado(s) no cofre.")
            frase = getpass.getpass("  Frase-senha do backup: ")
            try:
                bk.conferir(fbk, frase)
            except bk.FraseIncorreta as e:
                print(f"\n  ERRO: {e}")
                return 2
            print("  Frase confere.")

    if so_conferir:
        print("\n  --so-conferir: nada foi escrito.")
        return 0

    r = bk.restaurar(fbk, frase, raiz=raiz)
    if r.get("precisa_decisao"):
        modo = _perguntar(r["destino"], r["opcoes"])
        if not modo:
            print("\n  Cancelado. Nada foi alterado.")
            return 0
        r = bk.restaurar(fbk, frase, raiz=raiz, modo=modo)

    if not r.get("ok"):
        print(f"\n  ERRO: {r.get('erro')}")
        for p in (r.get("problemas") or [])[:8]:
            print(f"     - {p}")
        return 2

    print("\n  PRONTO.\n")
    print(f"  Modo       : {r['modo']}")
    print(f"  Escritos   : {r['escritos']} arquivo(s)")
    if r.get("mantidos"):
        print(f"  Mantidos   : {r['mantidos']} (já existiam aqui)")
    if r.get("anterior_movido_para"):
        print(f"  O que estava aqui foi MOVIDO para:\n     {r['anterior_movido_para']}")
    print(f"  Certificados restaurados : {r['certificados']}")
    if r["aguardando_senha"]:
        print(f"\n  {r['aguardando_senha']} empresa(s) estão AGUARDANDO SENHA.")
        print("  Abra o Fiscale > Clientes e informe a senha de cada certificado")
        print("  que você for usar nesta máquina. As empresas já estão")
        print("  cadastradas — nada precisa ser recadastrado.")
    print("\n" + "=" * 66)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n  Cancelado.")
        sys.exit(1)
