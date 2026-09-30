#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inventario_port4.py — o que a distribuição portátil vai precisar carregar.

    python inventario_port4.py            relatório em texto
    python inventario_port4.py --json     o mesmo, para consumo de máquina
    python inventario_port4.py --md       grava INVENTARIO_PORT4.md

NÃO instala nada e NÃO empacota nada. Só olha a instalação atual e responde:
"para rodar isto sem Python instalado, o que tem de ir junto?"

**A .venv de hoje NÃO serve de base.** Ela é um atalho para a instalação do
Python que existe nesta máquina — a biblioteca padrão vem de fora dela. A PORT 4
parte do *embeddable package* oficial da python.org e reinstala os pacotes ali
dentro; este inventário é a lista de compras.
"""
from __future__ import annotations

import ast
import json
import platform
import sys
import sysconfig
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PULAR = {".venv", "build", "dist", "__pycache__", "node_modules", ".next", "elo"}

# Pacotes com código nativo (.pyd/.dll). São os que dão trabalho num pacote
# portátil: precisam casar com a versão E a arquitetura do Python embutido.
NATIVOS = {"cryptography", "PIL", "pillow", "cffi", "pydantic_core", "pydantic-core",
           "httptools", "watchfiles", "websockets", "fonttools"}


def _fontes() -> list[Path]:
    return [p for p in RAIZ.rglob("*.py")
            if not any(x in p.parts for x in PULAR)
            and not p.parts[len(RAIZ.parts):][0].startswith("backup_")]


def _imports(arquivos):
    locais = {p.stem for p in arquivos}
    std, ext = set(), {}
    for p in arquivos:
        try:
            arvore = ast.parse(p.read_text("utf-8", "ignore"))
        except Exception:
            continue
        nomes = []
        for n in ast.walk(arvore):
            if isinstance(n, ast.Import):
                nomes += [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                nomes.append(n.module.split(".")[0])
        for nome in nomes:
            if nome in locais:
                continue
            if nome in sys.stdlib_module_names:
                std.add(nome)
            else:
                ext.setdefault(nome, set()).add(str(p.relative_to(RAIZ)))
    return sorted(std), {k: sorted(v) for k, v in sorted(ext.items())}


def _instalados() -> dict:
    try:
        from importlib.metadata import distributions
    except Exception:
        return {}
    fora = {}
    for dist in distributions():
        try:
            nome = dist.metadata["Name"]
            if nome:
                fora[nome.lower().replace("_", "-")] = dist.version
        except Exception:
            continue
    return dict(sorted(fora.items()))


def _binarios_do_pacote(modulo: str) -> list[str]:
    """.pyd/.dll que o pacote carrega — o que a PORT 4 precisa ver casar."""
    try:
        m = __import__(modulo)
    except Exception:
        return []
    base = Path(getattr(m, "__file__", "") or "").parent
    if not base.is_dir():
        return []
    fora = []
    for p in base.rglob("*"):
        if p.suffix.lower() in (".pyd", ".dll"):
            try:
                fora.append(f"{p.relative_to(base.parent).as_posix()}  "
                            f"({p.stat().st_size // 1024} KB)")
            except Exception:
                fora.append(p.name)
    return sorted(fora)[:40]


def _arquivos_do_app() -> dict:
    grupos = {"web": [], "python": [], "assets": [], "dados_de_apoio": [],
              "scripts": [], "documentacao": []}
    for p in sorted(RAIZ.rglob("*")):
        if not p.is_file() or any(x in p.parts for x in PULAR):
            continue
        rel = p.relative_to(RAIZ)
        if rel.parts and rel.parts[0].startswith("backup_"):
            continue
        alvo = None
        if rel.parts[0] == "web" or p.suffix in (".html", ".js", ".webmanifest"):
            alvo = "web"
        elif p.suffix == ".py":
            alvo = "python"
        elif p.suffix in (".png", ".webp", ".svg", ".ico"):
            alvo = "assets"
        elif p.suffix == ".json":
            alvo = "dados_de_apoio"
        elif p.suffix in (".bat", ".sh", ".spec", ".iss"):
            alvo = "scripts"
        elif p.suffix in (".md", ".txt"):
            alvo = "documentacao"
        if alvo:
            grupos[alvo].append({"arquivo": rel.as_posix(), "bytes": p.stat().st_size})
    return grupos


def levantar() -> dict:
    arquivos = _fontes()
    std, ext = _imports(arquivos)
    instalados = _instalados()

    # de nome de import para nome de pacote no pip
    apelidos = {"PIL": "pillow", "fpdf": "fpdf2", "requests_pkcs12": "requests-pkcs12",
                "qrcode": "qrcode", "pystray": "pystray"}
    externos = []
    for nome in ext:
        pip = apelidos.get(nome, nome).lower().replace("_", "-")
        externos.append({
            "import": nome,
            "pacote_pip": pip,
            "versao_instalada": instalados.get(pip, "(não identificada)"),
            "tem_codigo_nativo": nome in NATIVOS or pip in NATIVOS,
            "usado_em": ext[nome][:5],
        })

    grupos = _arquivos_do_app()
    nativos = {}
    for e in externos:
        if e["tem_codigo_nativo"]:
            bins = _binarios_do_pacote(e["import"])
            if bins:
                nativos[e["import"]] = bins

    v = sys.version_info
    return {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "runtime": {
            "python": f"{v.major}.{v.minor}.{v.micro}",
            "arquitetura": platform.machine(),
            "bits": 64 if sys.maxsize > 2 ** 32 else 32,
            "implementacao": platform.python_implementation(),
            "tag_abi": sysconfig.get_config_var("SOABI") or "",
            "prefixo_atual": sys.prefix,
            "prefixo_base": sys.base_prefix,
            "e_venv": sys.prefix != sys.base_prefix,
            "embeddable_sugerido":
                f"python-{v.major}.{v.minor}.{v.micro}-embed-"
                f"{'amd64' if sys.maxsize > 2**32 else 'win32'}.zip",
        },
        "stdlib_usada": std,
        "pacotes_externos": externos,
        "binarios_nativos": nativos,
        "arquivos_do_app": {k: {"quantidade": len(v_), "bytes": sum(a["bytes"] for a in v_),
                                "itens": v_}
                            for k, v_ in grupos.items()},
        "nao_levar": [
            "nfse/.venv/  — atalho para o Python desta máquina; a PORT 4 monta o seu",
            "build/ e dist/  — saída do PyInstaller, 109 MB recriáveis",
            "__pycache__/  — cache",
            "elo/  — projeto separado, 1,4 GB; não é requisito do Fiscale",
            "dados/  — dados do usuário; vão pelo backup .fbk, nunca no instalador",
            "*.pfx, *.db, *.log  — segredo, estado e cache",
        ],
        "avisos": _avisos(externos),
    }


def _avisos(externos) -> list[str]:
    fora = []
    nativos = [e["import"] for e in externos if e["tem_codigo_nativo"]]
    if nativos:
        fora.append(
            f"{len(nativos)} pacote(s) têm código nativo ({', '.join(nativos)}). "
            f"Os binários precisam casar com a versão e a arquitetura exatas do "
            f"Python embutido — instalar com o pip do PRÓPRIO runtime portátil "
            f"resolve isso; copiar de outro lugar, não.")
    fora.append(
        "O pacote embeddable oficial vem sem pip e com um arquivo ._pth que "
        "limita o sys.path. A PORT 4 precisa habilitar 'import site' e apontar "
        "a pasta das bibliotecas, senão nada importa.")
    fora.append(
        "tkinter NÃO vem no embeddable. O Fiscale usa (diálogos de escolher "
        "arquivo/pasta no módulo NFS-e). Ou se leva o tcl/tk junto, ou esses "
        "botões passam a usar um seletor da própria tela.")
    fora.append(
        "Smart App Control está ligado nesta máquina e bloqueia .exe sem "
        "assinatura. O python.exe do pacote oficial é assinado pela PSF — é por "
        "isso que o embeddable passa e um PyInstaller não assinado, não.")
    return fora


# ── saída ───────────────────────────────────────────────────────────────────
def _mb(n):
    return f"{n / 1048576:.1f} MB"


def texto(inv: dict) -> str:
    r = inv["runtime"]
    L = ["=" * 70, "  FISCALE — INVENTÁRIO PARA A DISTRIBUIÇÃO PORTÁTIL (PORT 4)",
         "=" * 70, f"  gerado em {inv['gerado_em']}", "",
         "  RUNTIME",
         f"    Python {r['python']} · {r['implementacao']} · {r['arquitetura']} "
         f"({r['bits']} bits)",
         f"    hoje roda de : {r['prefixo_atual']}",
         f"    biblioteca padrão vem de: {r['prefixo_base']}",
         f"    é ambiente virtual? {'SIM — e por isso não é portátil' if r['e_venv'] else 'não'}",
         f"    pacote a baixar na PORT 4: {r['embeddable_sugerido']}", "",
         f"  BIBLIOTECA PADRÃO — {len(inv['stdlib_usada'])} módulos usados",
         "    " + ", ".join(inv["stdlib_usada"]), "",
         f"  PACOTES EXTERNOS — {len(inv['pacotes_externos'])}"]
    L.append(f"    {'import':<18} {'pacote pip':<20} {'versão':<12} nativo")
    for e in inv["pacotes_externos"]:
        L.append(f"    {e['import']:<18} {e['pacote_pip']:<20} "
                 f"{e['versao_instalada']:<12} {'SIM' if e['tem_codigo_nativo'] else '-'}")
    if inv["binarios_nativos"]:
        L += ["", "  BINÁRIOS NATIVOS (precisam casar com o Python embutido)"]
        for pacote, bins in inv["binarios_nativos"].items():
            L.append(f"    {pacote}: {len(bins)} arquivo(s)")
            for b in bins[:4]:
                L.append(f"        {b}")
            if len(bins) > 4:
                L.append(f"        … e mais {len(bins) - 4}")
    L += ["", "  ARQUIVOS DO PROGRAMA"]
    for grupo, info in inv["arquivos_do_app"].items():
        L.append(f"    {grupo:<18} {info['quantidade']:>4} arquivo(s)  {_mb(info['bytes']):>10}")
    L += ["", "  NÃO LEVAR"]
    for x in inv["nao_levar"]:
        L.append(f"    - {x}")
    L += ["", "  O QUE VAI DAR TRABALHO NA PORT 4"]
    for i, a in enumerate(inv["avisos"], 1):
        L.append(f"    {i}. " + a)
    L += ["", "=" * 70]
    return "\n".join(L)


def markdown(inv: dict) -> str:
    r = inv["runtime"]
    L = ["# Inventário para a PORT 4 — distribuição portátil", "",
         f"Gerado automaticamente por `inventario_port4.py` em {inv['gerado_em']}.", "",
         "## Runtime", "", "| | |", "|---|---|",
         f"| Python | **{r['python']}** ({r['implementacao']}) |",
         f"| Arquitetura | {r['arquitetura']} · {r['bits']} bits |",
         f"| Hoje roda de | `{r['prefixo_atual']}` |",
         f"| Biblioteca padrão vem de | `{r['prefixo_base']}` |",
         f"| É ambiente virtual? | {'**sim** — e por isso não é portátil' if r['e_venv'] else 'não'} |",
         f"| Pacote a baixar | `{r['embeddable_sugerido']}` |", "",
         f"## Pacotes externos ({len(inv['pacotes_externos'])})", "",
         "| import | pacote pip | versão | código nativo |", "|---|---|---|---|"]
    for e in inv["pacotes_externos"]:
        L.append(f"| `{e['import']}` | `{e['pacote_pip']}` | {e['versao_instalada']} | "
                 f"{'**sim**' if e['tem_codigo_nativo'] else 'não'} |")
    L += ["", f"## Biblioteca padrão ({len(inv['stdlib_usada'])} módulos)", "",
          "`" + "` · `".join(inv["stdlib_usada"]) + "`", "",
          "## Arquivos do programa", "", "| grupo | arquivos | tamanho |", "|---|---:|---:|"]
    for grupo, info in inv["arquivos_do_app"].items():
        L.append(f"| {grupo} | {info['quantidade']} | {_mb(info['bytes'])} |")
    L += ["", "## Não levar", ""]
    for x in inv["nao_levar"]:
        L.append(f"- {x}")
    L += ["", "## O que vai dar trabalho", ""]
    for i, a in enumerate(inv["avisos"], 1):
        L.append(f"{i}. {a}")
    L.append("")
    return "\n".join(L)


def main(argv):
    inv = levantar()
    if "--json" in argv:
        print(json.dumps(inv, ensure_ascii=False, indent=1))
        return 0
    if "--md" in argv:
        destino = RAIZ / "INVENTARIO_PORT4.md"
        destino.write_text(markdown(inv), encoding="utf-8")
        print(f"gravado: {destino}")
        return 0
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(texto(inv))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
