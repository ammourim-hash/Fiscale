#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrar_login_email.py — grava os campos da Fase 1 no `usuarios.json`.

    python migrar_login_email.py            mostra o que faria (não escreve)
    python migrar_login_email.py --aplicar  faz o backup e migra

O QUE MUDA
    Cada registro ganha `uid`, `nome`, `email` e `ativo`. `uid` recebe a chave
    atual, `ativo` nasce `true`, e `nome`/`email` nascem vazios — o
    administrador os preenche em `web/usuarios.html`.

O QUE **NÃO** MUDA
    `sal`, `hash` e `admin`. Nenhuma senha é tocada, e ninguém precisa
    redefinir nada.

ISTO É OPCIONAL
    O Fiscale funciona sem rodar isto: `fiscale_usuarios.carregar()` preenche
    os campos em MEMÓRIA a cada leitura. A migração só torna o arquivo
    explícito — e é por isso que ela é segura de adiar, e segura de repetir.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fiscale_dados as fd
import fiscale_usuarios as us


def main(argv):
    raiz = Path(fd.raiz())
    arq = raiz / "usuarios.json"
    aplicar = "--aplicar" in argv

    print("=" * 66)
    print("  FISCALE — Fase 1 do login: e-mail profissional")
    print("=" * 66)
    print("  arquivo: %s" % arq)

    if not arq.exists():
        print("  Nada a fazer: o arquivo não existe (sistema sem usuários).")
        return 0

    atuais = us.carregar(arq)
    print("  usuários: %d" % len(atuais))
    for uid, reg in sorted(atuais.items()):
        print("     %-16s papel=%-9s ativo=%-5s email=%s"
              % (uid,
                 "admin" if reg.get("admin", uid == "admin") else "operador",
                 reg.get("ativo", True),
                 reg.get("email") or "(a cadastrar)"))

    if not aplicar:
        print()
        print("  ENSAIO — nada foi escrito.")
        print("  Para aplicar:  python migrar_login_email.py --aplicar")
        return 0

    r = us.migrar(arq)
    print()
    if r.get("ja_estava"):
        print("  O arquivo já tinha os campos. Nada mudou.")
    elif r.get("migrado"):
        print("  MIGRADO: %d usuário(s)." % r["usuarios"])
        print("  backup : %s" % r["backup"])
    else:
        print("  Não migrou: %s" % r.get("motivo", "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
