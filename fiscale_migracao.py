#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fiscale_migracao.py — leva os dados antigos para a estrutura nova, sem perder nada.

QUANDO RODA
    No arranque, antes de qualquer módulo abrir arquivo. É idempotente: rodar
    dez vezes tem o mesmo efeito de rodar uma.

O QUE FAZ (formato 1 -> 2)
    1. Traz o conteúdo de ~/SistemaNFSe/dados para a raiz única.
       São ~30 mil arquivos e 150 MB, então usa `os.replace` (renomear no mesmo
       disco é instantâneo) e cai para cópia quando não dá.
    2. Traz as pastas <cnpj>/ e o certificados.json.
    3. Copia os .pfx para <DADOS>/certs/ e passa o cadastro a apontar para
       "certs/<arquivo>.pfx" — caminho relativo, que funciona em qualquer
       máquina.
    4. Grava dados_versao.json.

O QUE **NÃO** FAZ, DE PROPÓSITO
    • Não apaga a origem. O legado fica onde está, renomeado para
      `...-migrado-para-fiscale`, até você conferir e apagar à mão. Migração que
      apaga a origem no primeiro teste é migração que você não pode desfazer.
    • Não decifra nem transporta senha de certificado. DPAPI é da máquina; o que
      não abrir vira "aguardando senha" e o usuário informa de novo.
    • Não toca em sessoes.db nem em elo_sync.db — são efêmeros.

SE ALGO DER ERRADO
    Cada passo é registrado em `migracao.log` na pasta de dados. A migração para
    no primeiro erro e devolve o que já fez; nada fica pela metade sem registro.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import fiscale_dados as fd

VERSAO_ATUAL = 3
ARQ_VERSAO = "dados_versao.json"
ARQ_LOG = "migracao.log"


def _log(raiz: Path, msg: str) -> None:
    linha = f"{datetime.now():%d/%m/%Y %H:%M:%S}  {msg}"
    try:
        with open(raiz / ARQ_LOG, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass


def versao(raiz: Path) -> int:
    p = raiz / ARQ_VERSAO
    if not p.exists():
        # Raiz virgem (sem nada dentro) já nasce no formato novo; raiz com
        # dados antigos precisa passar pela migração.
        tem_coisa = any(raiz.glob("*.json")) or any(raiz.glob("*/"))
        return 1 if tem_coisa else VERSAO_ATUAL
    try:
        return int(json.loads(p.read_text("utf-8")).get("versao", 1))
    except Exception:
        return 1


def _legados_com_conteudo(raiz: Path) -> list[Path]:
    """Raízes antigas que ainda têm dados esperando para ser trazidos.

    Existe porque "a raiz nova está vazia" NÃO significa "não há o que migrar":
    numa máquina onde o Sistema NFS-e standalone rodou antes, ~/SistemaNFSe/dados
    está cheio e ~/Fiscale/dados nasce vazio. Decidir só pela raiz nova deixaria
    esses dados órfãos, em silêncio — que é o pior jeito de perder arquivo.

    DUAS TRAVAS, e as duas custaram um susto para existir:

    1. Só adota legado quando a raiz em uso é a NATURAL da instalação. Com
       FISCALE_DADOS apontando para uma pasta própria, aquilo é outra
       instalação; puxar ~/SistemaNFSe para dentro dela seria mexer no acervo
       de quem não pediu. (No primeiro teste, um servidor de ensaio numa pasta
       temporária tentou adotar — e renomear — os dados de produção.)

    2. ~/Fiscale/dados nunca é "legado". Ou é a própria raiz (nada a fazer) ou
       é outra instalação (mãos fora)."""
    if not fd.raiz_e_padrao():
        return []
    try:
        legado = fd.LEGADO_NFSE
        if legado.exists() and legado.resolve() != raiz.resolve() and any(legado.iterdir()):
            return [legado]
    except Exception:
        pass
    return []


def _gravar_versao(raiz: Path, v: int, extra: dict | None = None) -> None:
    d = {"versao": v, "em": datetime.now().strftime("%d/%m/%Y %H:%M:%S")}
    d.update(extra or {})
    (raiz / ARQ_VERSAO).write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                   encoding="utf-8")


def _mover(origem: Path, destino: Path) -> str:
    """Move preferindo renomear. Devolve 'renomeado' | 'copiado' | 'ja-existia'."""
    if destino.exists():
        return "ja-existia"
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(origem, destino)       # mesmo volume: instantâneo
        return "renomeado"
    except OSError:
        if origem.is_dir():
            shutil.copytree(origem, destino, dirs_exist_ok=True)
        else:
            shutil.copy2(origem, destino)
        return "copiado"


# ── passo 1: trazer o legado do módulo NFS-e ────────────────────────────────
def _trazer_legado(raiz: Path, legado: Path, rel: dict) -> None:
    if not legado.exists() or legado.resolve() == raiz.resolve():
        return
    itens = list(legado.iterdir())
    if not itens:
        return
    _log(raiz, f"legado encontrado em {legado} ({len(itens)} itens no topo)")
    for item in itens:
        # sessões e fila são efêmeras: não migram, se refazem sozinhas
        if item.name in ("sessoes.db", "elo_sync.db") or item.suffix in (".db-shm", ".db-wal"):
            continue
        destino = raiz / item.name
        if item.is_dir() and destino.exists():
            # pasta de empresa que existe dos dois lados: funde arquivo a arquivo,
            # SEM sobrescrever o que já está na raiz nova
            for sub in item.rglob("*"):
                if sub.is_dir():
                    continue
                alvo = destino / sub.relative_to(item)
                if alvo.exists():
                    rel["mantidos"] = rel.get("mantidos", 0) + 1
                    continue
                alvo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sub, alvo)
                rel["arquivos_trazidos"] = rel.get("arquivos_trazidos", 0) + 1
            continue
        if destino.exists():
            rel["mantidos"] = rel.get("mantidos", 0) + 1
            continue
        como = _mover(item, destino)
        rel[f"topo_{como}"] = rel.get(f"topo_{como}", 0) + 1
        if item.is_dir():
            rel["pastas_empresa"] = rel.get("pastas_empresa", 0) + 1
    # Marca a origem como consumida — RENOMEANDO, nunca apagando.
    #
    # Só renomeia depois de conferir, arquivo por arquivo, que tudo o que
    # sobrou na origem tem contraparte de mesmo tamanho no destino. Fusão de
    # pasta deixa a origem intacta (não apagamos nada), então contar "sobrou
    # algo?" nunca daria certo; o que vale é "está tudo lá do outro lado?".
    try:
        pendentes = []
        for p in legado.rglob("*"):
            if not p.is_file():
                continue
            if p.name in ("sessoes.db", "elo_sync.db") or p.suffix in (".db-shm", ".db-wal"):
                continue
            alvo = raiz / p.relative_to(legado)
            if not alvo.exists() or alvo.stat().st_size != p.stat().st_size:
                pendentes.append(str(p.relative_to(legado)))
        if pendentes:
            rel["nao_conferidos"] = pendentes[:20]
            rel["nao_conferidos_qtd"] = len(pendentes)
            _log(raiz, f"origem NAO renomeada: {len(pendentes)} arquivo(s) sem contraparte")
        else:
            marca = legado.parent / (legado.name + "-migrado-para-fiscale")
            if not marca.exists():
                os.replace(legado, marca)
                rel["origem_renomeada"] = str(marca)
                _log(raiz, f"origem conferida e renomeada para {marca}")
            else:
                rel["origem_ja_renomeada"] = str(marca)
    except Exception as e:
        _log(raiz, f"nao consegui renomear a origem (nao e problema): {e}")


# ── passo 2: certificados para dentro dos dados, com caminho relativo ───────
def _nome_seguro(nome: str) -> str:
    n = re.sub(r"[^0-9A-Za-zÀ-ÿ._\- ]", "_", nome or "certificado.pfx")
    return n[:150] or "certificado.pfx"


def _recolher_certificados(raiz: Path, rel: dict) -> None:
    reg = raiz / "certificados.json"
    if not reg.exists():
        return
    try:
        itens = json.loads(reg.read_text("utf-8"))
    except Exception as e:
        _log(raiz, f"certificados.json ilegivel, nao mexi: {e}")
        rel["erro_registro"] = str(e)
        return
    if not isinstance(itens, list):
        return

    certs = raiz / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    mudou = False
    for c in itens:
        bruto = (c.get("caminho") or "").strip()
        if not bruto:
            rel["sem_caminho"] = rel.get("sem_caminho", 0) + 1
            continue
        p = Path(bruto)
        if not p.is_absolute():
            rel["ja_relativo"] = rel.get("ja_relativo", 0) + 1
            continue
        alvo = certs / _nome_seguro(p.name)
        if not alvo.exists():
            if p.exists():
                try:
                    shutil.copy2(p, alvo)
                    rel["pfx_copiados"] = rel.get("pfx_copiados", 0) + 1
                except Exception as e:
                    _log(raiz, f"nao copiei {p.name}: {e}")
                    rel["pfx_falha"] = rel.get("pfx_falha", 0) + 1
                    continue
            else:
                # arquivo sumiu do caminho antigo: NÃO inventamos caminho novo,
                # o cadastro fica marcado e a tela mostra "arquivo ausente"
                rel["pfx_ausentes"] = rel.get("pfx_ausentes", 0) + 1
                _log(raiz, f"pfx ausente para {c.get('cnpj')}: {p}")
                continue
        else:
            rel["pfx_ja_estavam"] = rel.get("pfx_ja_estavam", 0) + 1
        c["caminho"] = "certs/" + alvo.name
        c["caminho_origem"] = bruto          # rastro do de onde veio
        mudou = True
        rel["cadastros_relativizados"] = rel.get("cadastros_relativizados", 0) + 1

    if mudou:
        bak = reg.with_suffix(".json.pre-port1")
        if not bak.exists():
            shutil.copy2(reg, bak)
        reg.write_text(json.dumps(itens, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(raiz, f"certificados.json atualizado ({rel.get('cadastros_relativizados',0)} cadastros)")


# ── passo 3: pasta vigiada com caminho portátil ─────────────────────────────
def _portabilizar_entrada(raiz: Path, rel: dict) -> None:
    p = raiz / "entrada_config.json"
    if not p.exists():
        return
    try:
        cfg = json.loads(p.read_text("utf-8"))
    except Exception:
        return
    pasta = str(cfg.get("pasta") or "")
    try:
        downloads = str(Path.home() / "Downloads")
    except Exception:
        return
    if pasta and pasta.rstrip("\\/").lower() == downloads.rstrip("\\/").lower():
        cfg["pasta"] = "%DOWNLOADS%"
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        rel["entrada_portatil"] = True


# ── passo 4 (formato 3): segredo de tela sai do texto claro ────────────────
def _proteger_segredos(raiz: Path, rel: dict) -> None:
    """Converte para DPAPI a credencial em claro de toda tela inscrita.

    Nunca apaga o texto claro antes de confirmar que a versão protegida abre e
    devolve o mesmo valor — a conferência é feita dentro de
    `fiscale_segredos.migrar_texto_claro`. O que falhar continua como está, e
    aparece no relatório."""
    import fiscale_segredos as fseg
    # O cofre nasce sem tela nenhuma inscrita; sem isto a migração rodaria
    # sobre um mapa vazio quando chamada pelo NFS-e ou pela restauração, e o
    # texto claro ficaria onde está sem ninguém notar.
    import fiscale_inscricoes  # noqa: F401
    for mod in fseg.MAPA:
        p = raiz / f"state_{mod}.json"
        if not p.exists():
            continue
        try:
            dados = json.loads(p.read_text("utf-8-sig")) or {}
        except Exception as e:
            _log(raiz, f"state_{mod}.json ilegivel, nao mexi: {e}")
            continue
        r = fseg.migrar_texto_claro(mod, dados, raiz)
        if not any(r.values()):
            continue
        bak = p.with_suffix(".json.pre-port3")
        if not bak.exists():
            shutil.copy2(p, bak)          # rede de segurança, fica só nesta máquina
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        rel[f"segredos_{mod}"] = r
        _log(raiz, f"state_{mod}.json: segredos protegidos {r}")


def caminho_entrada(valor: str) -> str:
    """Resolve o marcador %DOWNLOADS% para a pasta desta máquina."""
    v = (valor or "").strip()
    if v.upper().startswith("%DOWNLOADS%"):
        resto = v[len("%DOWNLOADS%"):].lstrip("\\/")
        return str(Path.home() / "Downloads" / resto) if resto else str(Path.home() / "Downloads")
    return v


# ── orquestração ────────────────────────────────────────────────────────────
def garantir(raiz: Path | None = None) -> dict:
    """Deixa a pasta de dados no formato atual. Seguro chamar sempre."""
    raiz = Path(raiz) if raiz else fd.raiz()
    raiz.mkdir(parents=True, exist_ok=True)
    v = versao(raiz)
    legados = _legados_com_conteudo(raiz)
    rel = {"versao_antes": v, "versao_depois": v, "raiz": str(raiz), "mudou": False}

    if v >= VERSAO_ATUAL and not legados:
        # Nada a fazer. Marca o formato para a próxima checagem sair de graça.
        if not (raiz / ARQ_VERSAO).exists():
            _gravar_versao(raiz, VERSAO_ATUAL, {"instalacao": "nova"})
        return rel

    if legados:
        rel["legados"] = [str(p) for p in legados]
    _log(raiz, f"=== migracao formato {v} -> {VERSAO_ATUAL} "
               f"(legados: {[str(p) for p in legados] or 'nenhum'}) ===")
    try:
        # Cada degrau roda só uma vez, na instalação que ainda não passou por
        # ele. Rodar tudo de novo a cada versão nova seria seguro (os passos são
        # idempotentes) mas varreria 30 mil arquivos à toa em cada atualização.
        if v < 2 or legados:
            for legado in legados:
                _trazer_legado(raiz, legado, rel)
            _recolher_certificados(raiz, rel)
            _portabilizar_entrada(raiz, rel)
        if v < 3:
            _proteger_segredos(raiz, rel)
    except Exception as e:
        rel["erro"] = f"{e.__class__.__name__}: {e}"
        _log(raiz, f"ERRO: {rel['erro']}")
        return rel                      # não grava versão: tenta de novo depois

    _gravar_versao(raiz, VERSAO_ATUAL, {"migracao": rel})
    rel["versao_depois"] = VERSAO_ATUAL
    rel["mudou"] = True
    _log(raiz, f"=== concluida: {rel} ===")
    return rel


if __name__ == "__main__":
    import sys
    r = garantir(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(r, ensure_ascii=False, indent=2))
