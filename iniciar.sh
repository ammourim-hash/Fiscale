#!/usr/bin/env bash
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  # Módulo NFS-e: prepara as dependências uma única vez
  if [ -f "nfse/runner.py" ] && [ ! -d "nfse/.venv" ]; then
    echo "Preparando o módulo NFS-e (só na primeira vez, 1-2 minutos)..."
    python3 -m venv nfse/.venv
    nfse/.venv/bin/python -m pip install --upgrade pip -q
    nfse/.venv/bin/pip install -r nfse/requirements.txt -q
  fi
  python3 fiscale_server.py
else
  echo "Python 3 não encontrado. Instale em https://www.python.org/downloads/"
  read -p "Pressione Enter para sair..."
fi
