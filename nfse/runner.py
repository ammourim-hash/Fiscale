"""Runner do módulo NFS-e dentro do Fiscale.

Sobe o backend FastAPI do NFS-e numa porta interna (padrão 8768). O servidor do
Fiscale (fiscale_server.py) faz proxy das rotas do módulo para cá, atrás do
login.

A pasta de dados é a MESMA do resto do Fiscale — quem decide é
`fiscale_dados.raiz()`, que respeita FISCALE_DADOS.

    Antes da PORT 1 esta linha era `sys.frozen = True`, um truque para o
    main.py cair no ramo "instalado" e usar ~/SistemaNFSe/dados. O efeito
    colateral era o sistema ter DUAS pastas de dados: uma para login, clientes
    e plano de saúde, outra para certificados, XMLs e NF-e. Quem migrasse de
    máquina levava metade. O truque saiu; a decisão agora é de um módulo só.
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "backend"))
sys.path.insert(0, str(BASE.parent))       # onde vivem fiscale_dados/fiscale_migracao

import main
import uvicorn

porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8768
uvicorn.run(main.app, host="127.0.0.1", port=porta, log_level="warning")
