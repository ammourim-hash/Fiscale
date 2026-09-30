#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teste_bind.py — em qual interface o FISCALE escuta.

    python teste_bind.py

POR QUE ESTE ARQUIVO EXISTE
    O FISCALE vai passar a viver atrás de um proxy HTTPS na rede privada. No
    dia dessa virada, a porta 8777 deixa de atender a rede e passa a atender
    só o proxy — e isso é uma linha de código com poder de derrubar o
    escritório inteiro se for trocada na hora errada.

    A variável `FISCALE_BIND` existe para que a virada seja um ajuste de
    ambiente, e não uma edição de código no dia. Enquanto ela não for usada,
    o comportamento tem de ser EXATAMENTE o de sempre.

    É isso que este arquivo mede: os dois lados. Que a ausência da variável
    continua dando `0.0.0.0` (hoje), e que, quando alguém a definir, o valor
    é respeitado como está (amanhã).

O QUE ELE NÃO FAZ
    Não sobe servidor. Não abre porta. Não encosta na instância em execução.
    A função é pura de propósito — justamente para poder ser testada sem
    nenhum efeito sobre quem está trabalhando.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_server as fs                              # noqa: E402

_ok = 0
_falhas = 0


def ok(condicao, texto):
    global _ok, _falhas
    if condicao:
        _ok += 1
        print(f"  ok   {texto}")
    else:
        _falhas += 1
        print(f"  FALHA {texto}")


def secao(titulo):
    print(f"\n{titulo}")


def main() -> int:
    secao("1 · Sem a variável, nada muda")
    ok(fs.BIND_PADRAO == "0.0.0.0", "o padrão declarado continua 0.0.0.0")
    ok(fs.endereco_de_escuta({}) == "0.0.0.0", "ambiente sem FISCALE_BIND → 0.0.0.0")
    ok(fs.endereco_de_escuta({"FISCALE_BIND": ""}) == "0.0.0.0",
       "FISCALE_BIND vazio → 0.0.0.0 (vazio não é endereço)")
    ok(fs.endereco_de_escuta({"FISCALE_BIND": "   "}) == "0.0.0.0",
       "só espaços → 0.0.0.0")

    secao("2 · Com a variável, o valor é respeitado")
    ok(fs.endereco_de_escuta({"FISCALE_BIND": "127.0.0.1"}) == "127.0.0.1",
       "FISCALE_BIND=127.0.0.1 → 127.0.0.1 (o arranjo com o Caddy na frente)")
    ok(fs.endereco_de_escuta({"FISCALE_BIND": "100.101.102.103"}) == "100.101.102.103",
       "FISCALE_BIND=100.x.y.z → o IP da rede privada, como veio")
    ok(fs.endereco_de_escuta({"FISCALE_BIND": " 127.0.0.1 "}) == "127.0.0.1",
       "espaços em volta são aparados (copiar e colar de arquivo de serviço)")
    ok(fs.endereco_de_escuta({"FISCALE_BIND": "0.0.0.0"}) == "0.0.0.0",
       "0.0.0.0 explícito continua valendo")

    secao("3 · O que a tela pode prometer")
    ok(fs.escuta_so_local("127.0.0.1") is True, "127.0.0.1 atende só esta máquina")
    ok(fs.escuta_so_local("::1") is True, "::1 idem")
    ok(fs.escuta_so_local("localhost") is True, "localhost idem")
    ok(fs.escuta_so_local("0.0.0.0") is False, "0.0.0.0 atende a rede")
    ok(fs.escuta_so_local("100.101.102.103") is False,
       "um IP de rede privada atende quem chega por ele")

    secao("4 · O servidor usa a função — e não um endereço escrito à mão")
    fonte = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8")
    ok("Servidor((bind, PORT), Handler)" in fonte,
       "o bind do servidor vem da variável")
    ok('Servidor(("0.0.0.0"' not in fonte,
       "nenhum 0.0.0.0 escrito direto na chamada do servidor")
    ok(re.search(r"bind = endereco_de_escuta\(\)", fonte) is not None,
       "e é a função quem decide, para o teste acima valer de verdade")

    print("\n" + "=" * 62)
    print(f"{_ok} ok · {_falhas} falha(s)")
    return 1 if _falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
