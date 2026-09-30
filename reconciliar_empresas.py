#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reconciliar_empresas.py — relatório de `certificados.json` × `state_clientes.json`.

    python reconciliar_empresas.py
    python reconciliar_empresas.py --raiz "C:\\caminho\\para\\dados"

SOMENTE LEITURA. Não grava, não funde e não apaga registro nenhum: o objetivo é
mostrar onde os dois cadastros divergem para que a decisão seja sua.

Identificadores saem MASCARADOS — o relatório pode ser colado num e-mail sem
carregar CNPJ e CPF completos junto.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ_APP))
sys.path.insert(0, str(RAIZ_APP / "nfse" / "backend"))

import fiscale_dados as fdados          # noqa: E402
from ingestao import cadastro as cad    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconciliação dos cadastros de empresa")
    ap.add_argument("--raiz", default=None, help="pasta de dados (padrão: a do sistema)")
    ap.add_argument("--completo", action="store_true",
                    help="mostra o identificador sem máscara (evite em relatório compartilhado)")
    args = ap.parse_args()

    raiz = Path(args.raiz) if args.raiz else fdados.raiz()
    if not raiz.is_dir():
        print(f"Pasta de dados não encontrada: {raiz}")
        return 2

    c = cad.carregar(raiz)
    contagem = c.contagem_por_status()

    print(f"Pasta de dados: {raiz}")
    print(f"Empresas canônicas: {len(c.empresas)}   "
          f"Registros com identificador inutilizável: {len(c.invalidos)}")
    print("Por status: " + ("  ".join(f"{k}={v}" for k, v in sorted(contagem.items())) or "—"))
    print()

    cab = (f"{'CNPJ/CPF':<24} {'tipo':<5} {'cert':<5} {'cli':<4} "
           f"{'cred':<5} {'status':<20} nome")
    print(cab)
    print("-" * len(cab))
    for l in c.reconciliacao:
        d = l.linha()
        ident = l.identificador.valor if (args.completo and l.identificador.valido) else d["cnpj"]
        print(f"{ident:<24} {d['tipo'] or '-':<5} {d['certificados.json']:<5} "
              f"{d['state_clientes.json']:<4} {d['credenciais']:<5} "
              f"{d['status']:<20} {(d['nome'] or '')[:36]}")

    pend = [e for e in c.listar_empresas() if e.pendencias]
    print()
    print(f"Pendências: {len(pend)}")
    for e in pend:
        print(f"  {e.identificador.mascarado():<24} {e.nome_exibicao()[:30]:<32} "
              f"{'; '.join(e.pendencias)}")

    sem_cred = c.listar_empresas(com_credencial=False)
    if sem_cred:
        print()
        print(f"Sem certificado — não podem ser consultadas no fisco ({len(sem_cred)}):")
        for e in sem_cred:
            print(f"  {e.identificador.mascarado():<24} {e.nome_exibicao()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
