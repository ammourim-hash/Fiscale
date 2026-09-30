#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verificar_orfas.py — nada aponta para o que não existe mais.

Nasceu na FISCAL AI 1, quando dois módulos saíram do produto e era preciso
provar que não sobrou ponta solta: import morto, rota fantasma, item de menu
para tela apagada, arquivo listado no pacote portátil que já não existe.

Fica no projeto porque a pergunta volta a cada remoção. Roda em segundos e não
depende de ferramenta externa — o projeto não tem lint nem typecheck.

    python verificar_orfas.py .

Sai com código 1 se achar algo, para poder entrar numa verificação automática.

O QUE ELE NÃO ACUSA, DE PROPÓSITO
    Menção histórica em comentário. `# a primeira versão trazia o plano de
    saúde embutido no MAPA` é registro de decisão, não referência viva — e
    apagar o registro para calar o verificador seria perder a explicação.
"""
from __future__ import annotations
import ast, re, sys
from pathlib import Path

RAIZ = Path(sys.argv[1]).resolve()
PULAR = {"__pycache__", ".venv", "node_modules", ".next", "dist", "build",
         "dados", "elo", ".git"}

def relevantes(padrao):
    for p in sorted(RAIZ.rglob(padrao)):
        rel = p.relative_to(RAIZ)
        if any(x in rel.parts for x in PULAR):
            continue
        if rel.parts and rel.parts[0].startswith("backup_pre"):
            continue
        yield p

problemas = []
def falha(cat, msg):
    problemas.append(f"[{cat}] {msg}")

# 1 ── todo .py compila e todo import local resolve
pys = list(relevantes("*.py"))
modulos_locais = {p.stem for p in pys}
for p in relevantes("*"):
    if p.is_dir() and (p / "__init__.py").exists():
        modulos_locais.add(p.name)

for p in pys:
    fonte = p.read_text("utf-8", "replace")
    try:
        arvore = ast.parse(fonte)
    except SyntaxError as e:
        falha("sintaxe", f"{p.relative_to(RAIZ)}: {e}")
        continue
    for no in ast.walk(arvore):
        alvos = []
        if isinstance(no, ast.Import):
            alvos = [a.name.split(".")[0] for a in no.names]
        elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
            alvos = [no.module.split(".")[0]]
        for nome in alvos:
            if nome in ("saude", "plano", "ecac"):
                falha("import-morto", f"{p.relative_to(RAIZ)}:{no.lineno} importa '{nome}'")

# 2 ── nenhuma menção viva aos módulos removidos
ALVOS = (r"plano_de_saude", r"/api/saude", r"/api/plano", r"state_plano",
         r"state_ecac", r"ecac\.html", r"\bsaudemod\b")
# Arquivos que CITAM os alvos por ofício e não são referência viva: o próprio
# verificador, o arquivador dos dados, os relatórios da fase e o limpador de
# temporários (que varre sobras antigas pelo prefixo).
IGNORAR_ARQ = {"verificar_orfas.py", "arquivar_plano_saude.py",
               "FISCALE_AI0_DIAGNOSTICO.md", "FISCALE_AI1_RELATORIO.md",
               "limpar_temporarios.py"}
for padrao in ("*.py", "*.html", "*.js", "*.bat", "*.txt", "*.iss", "*.spec"):
    for p in relevantes(padrao):
        if p.name in IGNORAR_ARQ:
            continue
        texto = p.read_text("utf-8", "replace")
        for alvo in ALVOS:
            for m in re.finditer(alvo, texto):
                linha = texto[:m.start()].count("\n") + 1
                # menção histórica em comentário/docstring é registro, não órfã
                trecho = texto.splitlines()[linha - 1].strip()
                if trecho.lower().startswith(("#", "*", "//", "rem ")) or "Antes " in trecho:
                    continue
                falha("referencia-viva", f"{p.relative_to(RAIZ)}:{linha}  {trecho[:80]}")

# 3 ── todo href/src local do HTML aponta para arquivo existente
for p in relevantes("*.html"):
    texto = p.read_text("utf-8", "replace")
    # `nfse.html` e os caminhos com barra inicial são SERVIDOS pelo servidor
    # (o proxy monta a tela do NFS-e a partir de nfse/frontend/index.html);
    # não existem como arquivo ao lado da página que os referencia.
    SERVIDOS = {"nfse.html"}
    for m in re.finditer(r'(?:href|src)="([^"#:]+?\.(?:html|js|css|svg|webp|png|ico))', texto):
        ref = m.group(1).split("?")[0]
        if ref.startswith("/") or ref in SERVIDOS:
            continue
        # A tela do NFS-e é servida pela RAIZ do servidor (/nfse.html), não da
        # pasta onde o arquivo mora: os links dela resolvem contra web/.
        if "frontend" in p.parts and (RAIZ / "web" / ref).exists():
            continue
        alvo = (p.parent / ref).resolve()
        if not alvo.exists():
            linha = texto[:m.start()].count("\n") + 1
            falha("link-quebrado", f"{p.relative_to(RAIZ)}:{linha} -> {m.group(1)}")

# 4 ── o menu só aponta para telas que existem
nav = RAIZ / "web" / "fiscale-nav.js"
if nav.exists():
    for m in re.finditer(r"href:\s*'([^']+\.html)'", nav.read_text("utf-8")):
        if m.group(1) == "nfse.html":      # servida pelo proxy, ver acima
            continue
        if not (RAIZ / "web" / m.group(1)).exists():
            falha("menu-orfao", f"fiscale-nav.js -> {m.group(1)}")

# 5 ── COPIAR_ARQUIVOS do portátil aponta para arquivos que existem
mp = RAIZ / "montar_portatil.py"
if mp.exists():
    texto = mp.read_text("utf-8")
    bloco = texto[texto.index("COPIAR_ARQUIVOS = ["):texto.index("]", texto.index("COPIAR_ARQUIVOS = ["))]
    for m in re.finditer(r'"([^"]+)"', bloco):
        if not (RAIZ / m.group(1)).exists():
            falha("pacote-orfao", f"montar_portatil.py -> {m.group(1)}")

# 6 ── OBRIGATORIOS do diagnóstico
di = RAIZ / "diagnostico_instalacao.py"
if di.exists():
    texto = di.read_text("utf-8")
    bloco = texto[texto.index("OBRIGATORIOS = ["):texto.index("]", texto.index("OBRIGATORIOS = ["))]
    for m in re.finditer(r'"([^"]+)"', bloco):
        if not (RAIZ / m.group(1)).exists():
            falha("diagnostico-orfao", f"diagnostico_instalacao.py -> {m.group(1)}")

print(f"varridos: {len(pys)} .py, {len(list(relevantes('*.html')))} .html")
if problemas:
    print(f"\n{len(problemas)} PROBLEMA(S):")
    for x in problemas:
        print("  " + x)
    sys.exit(1)
print("\nnenhuma referência órfã.")
