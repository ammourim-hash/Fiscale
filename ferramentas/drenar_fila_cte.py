"""Drena a fila de CT-e de UMA empresa, página a página, até acabar.

O QUE ELE É
    Uma casca fina sobre `servico_cte.drenar_fila`. Toda a regra — persistir
    antes de avançar, preservar a resposta, indexar, parar no primeiro
    problema — mora no serviço. Aqui só há o que mostrar na tela.

A CADÊNCIA
    `POLITICA_PAGINACAO`: 5 segundos entre páginas. É política separada da
    `POLITICA_PADRAO` (60s, 12/hora), que continua valendo para consulta
    avulsa. A distinção não é conveniência: paginar pede um `ultNSU` maior a
    cada vez e traz documento novo, que é a operação para a qual o `distNSU`
    existe. Consumo indevido é o contrário — repetir a mesma consulta sem que
    o checkpoint ande.

    Uso:  python ferramentas/drenar_fila_cte.py --empresa <CNPJ> [--max N]
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROJETO / "nfse" / "backend"))

from ingestao import autor_consulta as ac          # noqa: E402
from ingestao import cadastro as cad_mod           # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import servico_cte as svc            # noqa: E402
from ingestao.distribuicao import nsu_int          # noqa: E402


def raiz_dados() -> Path:
    return Path(os.environ.get("FISCALE_DADOS",
                               Path.home() / "Fiscale" / "dados"))


def mascarar(c: str) -> str:
    return (c[:8] + "***") if len(c) >= 8 else "***"


def main() -> int:
    if "--empresa" not in sys.argv:
        print("informe --empresa <CNPJ>")
        return 2
    emp = "".join(ch for ch in sys.argv[sys.argv.index("--empresa") + 1]
                  if ch.isdigit())
    teto = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv \
        else svc.MAX_PAGINAS_PADRAO

    raiz = raiz_dados()
    cad = cad_mod.carregar(raiz)
    autor = ac.carregar(raiz)
    repo = cpm.RepositorioCheckpoint(raiz)
    cp0 = repo.carregar(emp, svc.SERVICO, "PRODUCAO")

    u, m = nsu_int(cp0.ult_nsu or "0"), nsu_int(getattr(cp0, "max_nsu", "") or "0")
    print("═" * 74)
    print(f"  DRENAGEM DE FILA — CT-e   ·   {mascarar(emp)}")
    print("═" * 74)
    print(f"  ultNSU atual ..... {cp0.ult_nsu}")
    print(f"  maxNSU conhecido . {getattr(cp0, 'max_nsu', '') or '(nenhum)'}")
    print(f"  fila estimada .... {max(0, m - u)}")
    print(f"  pausa ............ {svc.POLITICA_PAGINACAO.intervalo_minimo_segundos}s "
          f"entre páginas")
    print(f"  teto ............. {teto} páginas")
    print(f"  cUFAutor ......... {autor.cuf} ({autor.uf})")
    print()

    t0 = time.monotonic()

    def mostrar(n, c):
        falta = max(0, nsu_int(c.max_nsu or "0") - nsu_int(c.nsu_depois or "0"))
        print(f"  pág {n:>3}  cStat {c.cstat:<4} {c.estado:<22} "
              f"docs {c.documentos:>3}  grav {c.persistidos:>3}  "
              f"idx {c.indexados:>4}  ultNSU {c.nsu_depois}  falta {falta}",
              flush=True)
        if c.erro:
            print(f"           ERRO: {c.erro}", flush=True)

    d = svc.drenar_fila(raiz, emp, svc.fabrica_padrao(cad, raiz),
                        cad=cad, cuf=autor.cuf, max_paginas=teto,
                        ao_terminar_pagina=mostrar)

    cp1 = repo.carregar(emp, svc.SERVICO, "PRODUCAO")
    u1 = nsu_int(cp1.ult_nsu or "0")
    m1 = nsu_int(getattr(cp1, "max_nsu", "") or "0")

    print()
    print("═" * 74)
    print(f"  páginas .......... {d.paginas}")
    print(f"  documentos ....... {d.documentos}   gravados: {d.persistidos}")
    print(f"  ultNSU ........... {d.nsu_inicial}  →  {cp1.ult_nsu}")
    print(f"  maxNSU ........... {getattr(cp1, 'max_nsu', '')}")
    print(f"  fila restante .... {max(0, m1 - u1)}")
    print(f"  sincronizada? .... {'SIM — ultNSU == maxNSU' if u1 >= m1 and m1 else 'não'}")
    print(f"  parou porque ..... {d.motivo_da_parada}")
    print(f"  tempo ............ {round(time.monotonic() - t0)}s")
    print("═" * 74)
    return 0 if d.completa else 1


if __name__ == "__main__":
    raise SystemExit(main())
