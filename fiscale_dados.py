#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fiscale_dados.py — a ÚNICA fonte de verdade sobre onde ficam os dados.

POR QUE ISTO EXISTE
    Até a PORT 1 havia duas raízes de dados, decididas em lugares diferentes:

        fiscale_server.py  ->  ~/Fiscale/dados        (respeitava FISCALE_DADOS)
        nfse/backend/main.py -> ~/SistemaNFSe/dados   (NÃO respeitava)

    A segunda vinha de `runner.py` fazendo `sys.frozen = True` na mão, só para
    o `main.py` cair no ramo "instalado". O efeito prático: login, clientes e
    plano de saúde num lugar; certificados, XMLs e NF-e em outro. Quem migrasse
    "a pasta de dados" levava metade do sistema — e foi exatamente o que
    aconteceu.

    Agora quem responde "onde ficam os dados?" é este módulo, e só ele.

A ORDEM DE DECISÃO
    1. FISCALE_DADOS         — se estiver definida, manda. Sempre.
    2. <app>/dados           — se existir a marca `.fiscale-portatil` ali dentro
                               (é assim que o FISCALE-Portable roda de pendrive
                               sem escrever no perfil do usuário)
    3. ~/Fiscale/dados       — o padrão, e o que o sistema já usa hoje

ESTRUTURA
    <DADOS>/
        certificados.json          cadastro das empresas
        certs/<arquivo>.pfx        os certificados, DENTRO dos dados
        usuarios.json              login
        state_*.json               estado das telas
        entrada_config.json        pasta vigiada
        sessoes.db  elo_sync.db    efêmeros — não vão em backup
        <cnpj>/nfe|xmls|acervo|... os documentos de cada empresa
        dados_versao.json          versão do formato (ver fiscale_migracao.py)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

MARCA_PORTATIL = ".fiscale-portatil"

# Onde ficavam os dados antes da PORT 1. Só serve para a migração achar o que
# precisa trazer — nada no sistema deve ler daqui em condições normais.
LEGADO_NFSE = Path.home() / "SistemaNFSe" / "dados"
LEGADO_FISCALE = Path.home() / "Fiscale" / "dados"

_raiz_cache: Path | None = None

# Trava de teste (ING 1). LIGADA SÓ pela variável de ambiente abaixo: sem ela,
# nada nesta função muda — a instalação normal não sabe que isto existe.
#
# Existe porque um teste que grava em `~/Fiscale/dados` corrompe dado real do
# usuário, e o estrago só aparece depois. Com a trava ligada, qualquer tentativa
# de resolver a raiz para a pasta de produção vira exceção imediata, em vez de
# uma escrita silenciosa. Ver `teste_apoio.py`.
VAR_PROIBIR_RAIZ_REAL = "FISCALE_TESTE_PROIBIR_RAIZ_REAL"


def _raizes_reais() -> set[Path]:
    """As pastas que contêm dado de produção do usuário nesta máquina.

    Calculadas direto de `Path.home()`, sem passar por `raiz_padrao()`: os
    testes de portabilidade trocam `raiz_padrao` por uma função própria, e uma
    trava que dependesse dela seria desligada justamente por quem ela protege.
    """
    casa = Path.home()
    return {(casa / "Fiscale" / "dados"), (casa / "SistemaNFSe" / "dados")}


def _guarda_raiz_real(p: Path) -> Path:
    if not os.environ.get(VAR_PROIBIR_RAIZ_REAL):
        return p
    try:
        alvo = p.resolve()
    except OSError:
        return p
    if alvo in {r.resolve() for r in _raizes_reais() if r.exists()} or alvo in _raizes_reais():
        raise RuntimeError(
            f"{VAR_PROIBIR_RAIZ_REAL} está ligada e algo tentou usar a pasta de "
            f"dados REAL ({alvo}). Teste deve rodar em raiz temporária — "
            "use teste_apoio.raiz_temporaria()."
        )
    return p


def pasta_do_app() -> Path:
    """A pasta onde o FISCALE está instalado (não a de dados)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _candidatas_portateis() -> list[Path]:
    """Onde a marca `.fiscale-portatil` pode estar.

    Duas, porque a distribuição portátil separa código de dados:

        FISCALE-Portable\\
            app\\dados      <- se alguém puser a marca junto do código
            dados           <- É AQUI, ao lado de app\\

    A segunda é a que vale na prática, e olhar só a primeira fez a versão
    portátil gravar em C:\\Users\\<voce>\\Fiscale — ou seja, deixou de ser
    portátil sem avisar. Manter os dados FORA de `app\\` é de propósito:
    atualizar o Fiscale é copiar `app\\` por cima, e isso não pode chegar
    perto dos dados."""
    base = pasta_do_app()
    return [base / "dados", base.parent / "dados"]


def raiz_padrao() -> Path:
    """Onde os dados ficam quando ninguém mandou o contrário."""
    for ao_lado in _candidatas_portateis():
        if (ao_lado / MARCA_PORTATIL).exists():
            return ao_lado.resolve()
    return (Path.home() / "Fiscale" / "dados").resolve()


def raiz_e_padrao() -> bool:
    """A raiz em uso é a natural desta instalação?

    Importa para a migração: quando o usuário aponta FISCALE_DADOS para uma
    pasta própria (um teste, um segundo escritório, um pendrive), essa pasta é
    uma instalação SEPARADA. Adotar nela os dados de ~/SistemaNFSe seria puxar
    o acervo de outra instalação para dentro — exatamente o que não se quer."""
    try:
        return raiz() == raiz_padrao()
    except Exception:
        return False


def raiz(forcar: str | os.PathLike | None = None) -> Path:
    """A pasta de dados. Cria se não existir.

    `forcar` existe para os testes: passar um caminho devolve aquele caminho e
    reinicia o cache, sem depender de variável de ambiente do processo."""
    global _raiz_cache
    if forcar is not None:
        alvo = _guarda_raiz_real(Path(forcar).expanduser().resolve())
        _raiz_cache = alvo
        _raiz_cache.mkdir(parents=True, exist_ok=True)
        return _raiz_cache
    if _raiz_cache is not None:
        return _raiz_cache

    env = os.environ.get("FISCALE_DADOS")
    if env and env.strip():
        p = Path(env).expanduser()
    else:
        p = raiz_padrao()

    _guarda_raiz_real(p)
    p.mkdir(parents=True, exist_ok=True)
    _raiz_cache = p.resolve()
    return _raiz_cache


def pasta_certs() -> Path:
    p = raiz() / "certs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def pasta_empresa(cnpj: str) -> Path:
    """Pasta dos documentos de uma empresa. O nome é só os dígitos do CNPJ —
    é assim que já está em disco, e mudar isso renomearia 30 mil arquivos."""
    import re
    d = re.sub(r"\D", "", str(cnpj or "")) or "sem-cnpj"
    p = raiz() / d
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── caminho do certificado: relativo no disco, absoluto na memória ──────────
# O JSON guarda "certs/EMPRESA.pfx". Quem usa recebe o caminho absoluto DESTA
# máquina. É o que faz o mesmo certificados.json funcionar em qualquer
# computador — e é por isso que a conversão fica aqui, num lugar só.

def resolver_cert(caminho: str) -> str:
    """Relativo (como está gravado) -> absoluto (como o código usa)."""
    if not caminho:
        return ""
    p = Path(caminho)
    if p.is_absolute():
        return str(p)                    # legado: cadastro antigo, ainda serve
    return str((raiz() / p).resolve())


def relativizar_cert(caminho: str) -> str:
    """Absoluto -> relativo, quando o arquivo está dentro da pasta de dados.

    Fora dela, devolve o absoluto: é melhor um cadastro que só funciona nesta
    máquina do que um cadastro que aponta para o lugar errado na outra."""
    if not caminho:
        return ""
    p = Path(caminho)
    if not p.is_absolute():
        return str(p).replace("\\", "/")
    try:
        return str(p.resolve().relative_to(raiz())).replace("\\", "/")
    except ValueError:
        return str(p)


def dentro_dos_dados(caminho: str) -> bool:
    if not caminho:
        return False
    p = Path(caminho)
    if not p.is_absolute():
        return True
    try:
        p.resolve().relative_to(raiz())
        return True
    except ValueError:
        return False
