#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
limpar_temporarios.py — sobras de teste antigas em %TEMP%.

    python limpar_temporarios.py            # só LISTA (padrão, não apaga nada)
    python limpar_temporarios.py --apagar   # apaga o que foi confirmado

POR QUE ISTO EXISTE
    Antes da ING 2 as suítes chamavam `tempfile.mkdtemp()` e nunca apagavam.
    Sobrou uma centena de pastas em %TEMP%, várias com segredos **fictícios**
    plantados pelos testes. Não é vazamento — mas lixo com cara de segredo faz
    alguém perder tempo, ou concluir que vazou de verdade. As suítes já não
    deixam mais nada; isto é para o passivo.

REGRA DE SEGURANÇA: SÓ APAGA O QUE FOR PROVADO SER NOSSO
    Prefixo não basta. `port2_` poderia ser de qualquer programa. Uma pasta só
    é considerada do Fiscale se **tiver o prefixo E o conteúdo bater** com o de
    uma instalação de teste (`dados_versao.json`, `certificados.json`,
    `state_*.json`, `elo_sync.db`…).

    Pasta com o prefixo mas sem o conteúdo esperado é listada como DUVIDOSA e
    **não é apagada**, nem com `--apagar`. Diretório genérico nunca é tocado.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

# "saude_" é sobra histórica: o módulo Plano de Saúde saiu na FISCAL AI 1,
# mas pastas antigas com esse prefixo podem estar em %TEMP% até hoje.
PREFIXOS = ("port1_", "port2_", "port3_", "port4_", "saude_",
            "ing1_", "ing2_", "fiscale_teste_", "fiscale-teste-",
            "fiscale-sync-teste-", "exp-",
            # Acrescentados em 07/09/2026. Esta lista tinha ficado para tras
            # das suites novas: havia ~160 sobras em %TEMP% que o limpador nao
            # enxergava, porque ele so reconhecia os prefixos da epoca em que
            # foi escrito. Lista que envelhece em silencio e pior que lista
            # nenhuma -- esta parece limpar, e nao limpa.
            "fiscale_ip_", "fiscale_ip2_", "fiscale_http_",
            "fiscale_modelo_", "fiscale_v2_", "fiscale_t5_",
            "fiscale_sandbox_",
            # E os das suites do envelope de situacao fiscal.
            "fiscale_situacao_", "fiscale_indice_", "fiscale_ciclo_",
            "fiscale_relat_", "fiscale_bandeja_",
            "fiscale_demo_situacao_")

# Marcas de uma instalação de teste do Fiscale. Basta uma bater.
MARCAS = ("dados_versao.json", "certificados.json", "usuarios.json",
          "state_clientes.json", "state_plano.json", "elo_sync.db",
          "sessoes.db", ".fiscale-portatil", "entrada_config.json",
          # Bancos que so o Fiscale cria. Sem eles, as sobras de
          # `fiscale_ip_*` -- que contem APENAS `tentativas.db` -- ficavam
          # eternamente como DUVIDOSAS: prefixo reconhecido, conteudo nao,
          # e portanto nunca apagadas.
          "tentativas.db", "auditoria.db", "situacao.db", "bandeja.db", "situacao_bandeja.json",
          ".pimenta-login", "situacao_credenciais.json")

# Sufixos que a restauração "Substituir" e a migração criam AO LADO da raiz.
SUFIXOS_IRMAOS = ("-substituido-", "-migrado-para-fiscale")


def _do_fiscale(p: Path) -> tuple[bool, str]:
    """A pasta é comprovadamente de um teste do Fiscale?"""
    nome = p.name
    if not any(nome.startswith(x) for x in PREFIXOS):
        return False, "prefixo não é do projeto"
    try:
        nomes = {f.name for f in p.rglob("*") if f.is_file()}
        nomes |= {f.name for f in p.iterdir()}
    except OSError as exc:
        return False, f"não foi possível inspecionar ({type(exc).__name__})"
    achadas = sorted(nomes & set(MARCAS))
    if achadas:
        return True, "contém " + ", ".join(achadas[:3])
    if any(s in nome for s in SUFIXOS_IRMAOS):
        return True, "cópia deixada por restauração/migração de teste"
    return False, "prefixo do projeto, mas sem conteúdo reconhecível"


def _esta_vazia(p: Path) -> bool:
    """Nenhum ARQUIVO dentro, em nível nenhum. Diretório não conta."""
    try:
        return not any(f.is_file() for f in p.rglob("*"))
    except OSError:
        return False


def _tamanho(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Sobras de teste do Fiscale em %TEMP%")
    ap.add_argument("--apagar", action="store_true",
                    help="apaga as pastas CONFIRMADAS (sem isto, apenas lista)")
    ap.add_argument("--apagar-vazias", action="store_true", dest="apagar_vazias",
                    help="apaga também as pastas VAZIAS (zero arquivos)")
    ap.add_argument("--temp", default=None, help="diretório temporário a varrer")
    args = ap.parse_args()

    raiz = Path(args.temp) if args.temp else Path(tempfile.gettempdir())
    print(f"Varrendo: {raiz}\n")

    confirmadas: list[tuple[Path, str, int]] = []
    vazias: list[Path] = []
    duvidosas: list[tuple[Path, str]] = []
    try:
        itens = sorted(p for p in raiz.iterdir() if p.is_dir())
    except OSError as exc:
        print(f"Não foi possível ler {raiz}: {type(exc).__name__}")
        return 2

    for p in itens:
        if not any(p.name.startswith(x) for x in PREFIXOS):
            continue                      # nem olha: não é candidata
        # PASTA VAZIA É CATEGORIA PRÓPRIA, e não "duvidosa".
        #     O `rmtree` das suítes levava os arquivos e falhava nos
        #     diretórios, deixando esqueletos de zero byte. Eles não provam
        #     nada pelo conteúdo — não há conteúdo —, então caíam como
        #     DUVIDOSOS e nunca eram apagados: 105 pastas presas para sempre
        #     entre "não posso provar" e "não posso apagar".
        #
        #     Pasta com prefixo do projeto e ZERO arquivos não tem o que
        #     perder: apagar uma que por acaso não fosse nossa custaria um
        #     diretório vazio. Ainda assim ela NÃO entra no `--apagar`:
        #     precisa de `--apagar-vazias`, dito de própria boca.
        if _esta_vazia(p):
            vazias.append(p)
            continue
        ok, motivo = _do_fiscale(p)
        (confirmadas.append((p, motivo, _tamanho(p))) if ok
         else duvidosas.append((p, motivo)))

    total = sum(t for _, _, t in confirmadas)
    print(f"CONFIRMADAS como teste do Fiscale: {len(confirmadas)}  "
          f"({total / 1_048_576:.1f} MB)")
    for p, motivo, tam in confirmadas:
        print(f"  {tam / 1024:>9.0f} KB  {p.name}   [{motivo}]")

    print(f"\nVAZIAS — prefixo do projeto e ZERO arquivos: {len(vazias)}")
    for p in vazias[:8]:
        print(f"             {p.name}")
    if len(vazias) > 8:
        print(f"             ... e mais {len(vazias) - 8}")

    print(f"\nDUVIDOSAS — NÃO serão apagadas: {len(duvidosas)}")
    for p, motivo in duvidosas:
        print(f"             {p.name}   [{motivo}]")

    if not args.apagar and not args.apagar_vazias:
        print("\nNada foi apagado. Para apagar as CONFIRMADAS:")
        print("    python limpar_temporarios.py --apagar")
        if vazias:
            print("Para apagar também os esqueletos vazios:")
            print("    python limpar_temporarios.py --apagar --apagar-vazias")
        return 0

    alvos = [p for p, _, _ in confirmadas] if args.apagar else []
    if args.apagar_vazias:
        alvos += vazias

    apagadas = falhas = 0
    for p in alvos:
        shutil.rmtree(p, ignore_errors=True)
        if p.exists():
            falhas += 1
            print(f"  não foi possível apagar (em uso?): {p.name}")
        else:
            apagadas += 1
    print(f"\nApagadas: {apagadas}   Falharam: {falhas}   "
          f"Duvidosas preservadas: {len(duvidosas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
