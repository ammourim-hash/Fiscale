# -*- coding: utf-8 -*-
"""teste_rede_configurar.py — ponte para o rodar_testes.py.

A suíte de verdade é o `teste_rede_configurar.ps1`: a política de firewall
mora num script PowerShell e só pode ser provada em PowerShell. Este arquivo
só o executa e repassa a saída e o código de saída.

O .ps1 não toca no Windows: recusa rodar como administrador, troca todo
comando que altera algo por função de mentira e confere pela árvore
sintática que ninguém escapou. Detalhes no cabeçalho dele.
"""
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def main() -> int:
    if sys.platform != "win32":
        print("  ok   não se aplica fora do Windows")
        print("1 ok · 0 falha(s)")
        return 0
    ps = shutil.which("powershell") or shutil.which("powershell.exe")
    if not ps:
        print("  FALHOU  powershell não encontrado")
        print("0 ok · 1 falha(s)")
        return 1
    r = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(RAIZ / "teste_rede_configurar.ps1")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    sys.stdout.write(r.stdout)
    if r.stderr.strip():
        sys.stdout.write("\n[stderr do PowerShell]\n" + r.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
