#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arquivar_plano_saude.py — guarda o que existe do Plano de Saúde, cifrado,
ANTES de o módulo ser removido do FISCALE.

O QUE ISTO É
    Um arquivamento de saída, não um backup de rotina. O módulo Plano de Saúde
    sai do produto (FISCAL AI 1); o que ele acumulou não pode simplesmente
    sumir, porque parte disso é **dado pessoal**: nome, CPF, data de
    nascimento e dependentes de pessoas físicas. Some quando o titular
    decidir, não porque o software mudou de escopo.

O QUE ENTRA
    • `state_plano.json` — INTEIRO e sem sanitizar. É a diferença deste
      arquivamento para o `.fbk`: o backup normal remove credencial de propósito
      (ela é da máquina e não deve viajar); aqui o objetivo é o oposto — não
      perder nada, porque depois disto não haverá de onde reler.
    • Tudo que houver em `<raiz>/<cnpj>/saude/` (faturas, extratos, originais).

COMO É CIFRADO
    AES-256-GCM com chave derivada por scrypt, exatamente o cofre que o `.fbk`
    já usa (`fiscale_backup._fechar_cofre`) — não há segundo mecanismo de
    criptografia no FISCALE, e não é aqui que ele vai nascer.

    A frase é SORTEADA, não escolhida, e fica guardada de duas formas:

      1. protegida por DPAPI, ao lado do pacote — é o que permite reabrir nesta
         máquina sem digitar nada;
      2. em claro, num arquivo `.chave.txt` separado — que é o que salva se
         esta máquina morrer.

    O `.chave.txt` ao lado do pacote não é proteção nenhuma: ele existe para
    ser MOVIDO para fora do computador (gerenciador de senhas, cofre físico).
    Enquanto os dois estiverem na mesma pasta, considere o pacote como não
    cifrado. O programa avisa isso ao terminar.

O QUE ELE NUNCA FAZ
    Não apaga nada. Não imprime CPF, nome, data de nascimento nem dependente —
    nem no console, nem no recibo. O recibo conta QUANTOS registros havia, não
    QUEM eram.

Uso:
    python arquivar_plano_saude.py                 # arquiva e valida
    python arquivar_plano_saude.py --conferir X    # só reabre e confere o pacote X
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import zipfile
from datetime import datetime
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ_APP))
sys.path.insert(0, str(RAIZ_APP / "nfse" / "backend"))

import fiscale_backup as bk      # noqa: E402  — o cofre AES-256-GCM já existe
import fiscale_dados as fd       # noqa: E402
import seguranca                 # noqa: E402

PASTA_SAIDA = Path.home() / "Fiscale-backups"
MARCA = "fiscale-arquivo-plano-saude-v1"


# ── coleta ──────────────────────────────────────────────────────────────────
def coletar(raiz: Path) -> tuple[list[tuple[str, bytes]], dict]:
    """Devolve (arquivos, inventário). O inventário conta; nunca identifica."""
    arquivos: list[tuple[str, bytes]] = []
    inv = {"state_plano.json": None, "pastas_saude": {}, "total_bytes": 0}

    estado = raiz / "state_plano.json"
    if estado.exists():
        bruto = estado.read_bytes()
        arquivos.append(("state_plano.json", bruto))
        inv["total_bytes"] += len(bruto)
        try:
            d = json.loads(bruto.decode("utf-8-sig"))
        except Exception:
            inv["state_plano.json"] = {"ilegivel": True, "bytes": len(bruto)}
        else:
            # Contagens. Nenhum valor identificável entra no inventário.
            clientes = d.get("clientes") or []
            inv["state_plano.json"] = {
                "bytes": len(bruto),
                "operadoras": len(d.get("operadoras") or []),
                "clientes_pessoa_fisica": len(clientes),
                "dependentes": sum(len(c.get("deps") or []) for c in clientes
                                   if isinstance(c, dict)),
                "empresas_com_plano": len(d.get("empresasPlano") or []),
                "procuradores": len(d.get("procuradores") or []),
                "faturas": len(d.get("faturas") or []),
                "lancamentos": len(d.get("lancamentos") or []),
                "reinf_envios": len(d.get("reinfEnvios") or []),
                "credenciais_protegidas": sum(
                    len(e.get("acesso_protegido") or {})
                    for e in (d.get("empresasPlano") or []) if isinstance(e, dict)),
            }

    for pasta in sorted(raiz.glob("*/saude")):
        cnpj = pasta.parent.name
        n = 0
        for arq in sorted(pasta.rglob("*")):
            if not arq.is_file():
                continue
            bruto = arq.read_bytes()
            arquivos.append((f"{cnpj}/saude/{arq.relative_to(pasta).as_posix()}", bruto))
            inv["total_bytes"] += len(bruto)
            n += 1
        inv["pastas_saude"][cnpj] = n

    return arquivos, inv


# ── arquivar ────────────────────────────────────────────────────────────────
def arquivar(raiz: Path | None = None, saida: Path | None = None) -> dict:
    raiz = Path(raiz) if raiz else fd.raiz()
    saida = Path(saida) if saida else PASTA_SAIDA
    saida.mkdir(parents=True, exist_ok=True)

    arquivos, inv = coletar(raiz)
    if not arquivos:
        return {"ok": False, "motivo": "não há nada do Plano de Saúde para arquivar"}

    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = saida / f"plano_saude_{carimbo}"
    pacote, chave_dpapi, chave_clara = (
        base.with_suffix(".cofre"), base.with_suffix(".chave.dpapi"),
        base.with_suffix(".chave.txt"))

    # Frase sorteada: 256 bits. Ninguém escolhe, ninguém lembra, ninguém
    # reaproveita de outro sistema.
    frase = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")

    manifesto = {
        "marca": MARCA,
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "origem": str(raiz),
        "motivo": "remoção do módulo Plano de Saúde do FISCALE (FISCAL AI 1)",
        "inventario": inv,
        "arquivos": [nome for nome, _ in arquivos],
        "cifra": "AES-256-GCM, chave por scrypt (mesmo cofre do .fbk)",
    }
    conteudo = [("manifesto.json",
                 json.dumps(manifesto, ensure_ascii=False, indent=2).encode("utf-8"))]
    conteudo += arquivos

    pacote.write_bytes(bk._fechar_cofre(conteudo, frase))
    sha = hashlib.sha256(pacote.read_bytes()).hexdigest()

    # A frase, de duas formas — ver o cabeçalho do arquivo.
    try:
        chave_dpapi.write_text(seguranca.proteger(frase, raiz), encoding="utf-8")
        dpapi_ok = True
    except Exception:
        dpapi_ok = False
    chave_clara.write_text(
        "FISCALE — chave de recuperação do arquivo do Plano de Saúde\n"
        f"pacote : {pacote.name}\n"
        f"sha256 : {sha}\n"
        f"gerado : {manifesto['gerado_em']}\n\n"
        "MOVA ESTE ARQUIVO PARA FORA DESTE COMPUTADOR.\n"
        "Enquanto ele estiver na mesma pasta do pacote, o pacote não está protegido.\n\n"
        f"{frase}\n", encoding="utf-8")

    recibo = {
        "ok": True,
        "pacote": str(pacote),
        "sha256": sha,
        "bytes": pacote.stat().st_size,
        "gerado_em": manifesto["gerado_em"],
        "chave_dpapi": str(chave_dpapi) if dpapi_ok else None,
        "chave_recuperacao": str(chave_clara),
        "inventario": inv,
    }
    recibo["validacao"] = conferir(pacote, frase)
    (base.with_suffix(".recibo.json")).write_text(
        json.dumps(recibo, ensure_ascii=False, indent=2), encoding="utf-8")
    recibo["recibo"] = str(base.with_suffix(".recibo.json"))
    return recibo


# ── conferir ────────────────────────────────────────────────────────────────
def _frase_de(pacote: Path, raiz: Path | None = None) -> str:
    """Recupera a frase: DPAPI primeiro, arquivo de recuperação depois."""
    dp = pacote.with_suffix(".chave.dpapi")
    if dp.exists():
        try:
            return seguranca.desproteger(dp.read_text("utf-8").strip(),
                                         raiz or fd.raiz())
        except Exception:
            pass
    txt = pacote.with_suffix(".chave.txt")
    if txt.exists():
        return txt.read_text("utf-8").strip().splitlines()[-1].strip()
    raise RuntimeError("não encontrei a chave deste pacote")


def conferir(pacote: Path, frase: str | None = None,
             raiz: Path | None = None) -> dict:
    """Reabre o pacote e confere que TUDO que entrou pode sair.

    Validar backup é abrir, não é olhar o tamanho do arquivo. Um cofre que
    fecha e não abre é pior que backup nenhum: dá a sensação de estar coberto.
    """
    pacote = Path(pacote)
    frase = frase or _frase_de(pacote, raiz)
    try:
        dentro = bk._abrir_cofre(pacote.read_bytes(), frase)
    except Exception as e:
        return {"ok": False, "erro": f"{type(e).__name__}: não abriu"}

    if "manifesto.json" not in dentro:
        return {"ok": False, "erro": "pacote sem manifesto"}
    man = json.loads(dentro["manifesto.json"].decode("utf-8"))
    if man.get("marca") != MARCA:
        return {"ok": False, "erro": "não é um arquivo do Plano de Saúde"}

    esperados = list(man.get("arquivos") or [])
    faltando = [n for n in esperados if n not in dentro]
    r = {
        "ok": not faltando,
        "abriu": True,
        "arquivos_conferidos": len(esperados) - len(faltando),
        "faltando": faltando,
        "gerado_em": man.get("gerado_em"),
        "inventario": man.get("inventario"),
    }
    # Prova de conteúdo, sem imprimir conteúdo: o state precisa ser JSON válido
    # e trazer as mesmas contagens que o manifesto registrou.
    if "state_plano.json" in dentro:
        try:
            d = json.loads(dentro["state_plano.json"].decode("utf-8-sig"))
            inv = (man.get("inventario") or {}).get("state_plano.json") or {}
            r["state_legivel"] = True
            r["contagens_batem"] = (
                len(d.get("clientes") or []) == inv.get("clientes_pessoa_fisica")
                and len(d.get("empresasPlano") or []) == inv.get("empresas_com_plano"))
        except Exception:
            r["state_legivel"] = False
            r["ok"] = False
    return r


# ── linha de comando ────────────────────────────────────────────────────────
def _imprimir(r: dict) -> None:
    if not r.get("ok"):
        print("NÃO ARQUIVADO:", r.get("motivo") or r.get("erro"))
        return
    inv = r["inventario"]
    st = inv.get("state_plano.json") or {}
    print("\nArquivo do Plano de Saúde criado.\n")
    print(f"  pacote  : {r['pacote']}")
    print(f"  tamanho : {r['bytes']} bytes")
    print(f"  sha256  : {r['sha256']}")
    print(f"  gerado  : {r['gerado_em']}")
    print(f"  recibo  : {r['recibo']}")
    print("\n  Conteúdo (contagens; nenhum dado pessoal é impresso):")
    for rotulo, chave in (("operadoras", "operadoras"),
                          ("pessoas físicas", "clientes_pessoa_fisica"),
                          ("dependentes", "dependentes"),
                          ("empresas com plano", "empresas_com_plano"),
                          ("procuradores", "procuradores"),
                          ("faturas", "faturas"),
                          ("lançamentos", "lancamentos"),
                          ("credenciais protegidas", "credenciais_protegidas")):
        print(f"    {rotulo:<24} {st.get(chave)}")
    for cnpj, n in (inv.get("pastas_saude") or {}).items():
        print(f"    pasta saude/{cnpj[:5]}***     {n} arquivo(s)")
    v = r.get("validacao") or {}
    print(f"\n  Validação: {'ABRIU e confere' if v.get('ok') else 'FALHOU'} "
          f"({v.get('arquivos_conferidos')} arquivo(s) conferido(s))")
    print(f"\n  ⚠ MOVA {Path(r['chave_recuperacao']).name} para fora deste computador.")
    print("    Enquanto ele estiver ao lado do pacote, o pacote não está protegido.\n")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--conferir":
        print(json.dumps(conferir(Path(sys.argv[2])), ensure_ascii=False, indent=2))
    else:
        _imprimir(arquivar())
