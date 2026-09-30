"""Um ciclo de distribuição de CT-e, com as evidências materiais na tela.

POR QUE ESTE SCRIPT EXISTE
    A CTE 4 gravou 50 documentos com NSU 0..49 — a posição no lote — enquanto
    o `ultNSU` da mesma resposta era 75.827. O defeito era de uma linha. O que
    doeu foi não haver como conferir: a resposta original não existia em lugar
    nenhum, e o valor certo não era recuperável.

    Este script roda UMA página e mostra, lado a lado, o que a SEFAZ mandou e
    o que ficou no disco. É a auditoria que antes era impossível.

O QUE ELE NÃO FAZ
    Não pagina, não repete, não tenta de novo em caso de erro, não consulta
    outra empresa e não mexe em checkpoint por fora do serviço. Uma página, e
    o relatório do que aconteceu com ela.

    Uso:  python ferramentas/auditoria_ciclo_unico.py [--empresa <CNPJ>]
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROJETO / "nfse" / "backend"))

from ingestao import autor_consulta as ac          # noqa: E402
from ingestao import cadastro as cad_mod           # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import resposta_bruta as bruta       # noqa: E402
from ingestao import servico_cte as svc            # noqa: E402

TRX = "64567004000139"
NS_CTE = "{http://www.portalfiscal.inf.br/cte}"


def raiz_dados() -> Path:
    return Path(os.environ.get("FISCALE_DADOS",
                               Path.home() / "Fiscale" / "dados"))


def titulo(t: str) -> None:
    print()
    print("═" * 78)
    print(f"  {t}")
    print("═" * 78)


def mascarar(cnpj: str) -> str:
    return (cnpj[:8] + "***") if len(cnpj) >= 8 else "***"


class FonteEspiada:
    """Passa a chamada adiante e guarda a `Resposta` para a auditoria.

    Não interfere: só observa. Sem isto, o que a SEFAZ devolveu por documento
    ficaria só dentro do serviço, e a comparação com o disco seria impossível.
    """

    def __init__(self, real):
        self._real = real
        self.resposta = None

    def distribuir(self, *a, **kw):
        self.resposta = self._real.distribuir(*a, **kw)
        return self.resposta

    def consultar_nsu(self, *a, **kw):
        return self._real.consultar_nsu(*a, **kw)


def cte_no_acervo(raiz: Path, emp: str) -> dict:
    """`id_documento` → metadados de captura, só das espécies de CT-e."""
    base = raiz / emp / "acervo"
    fora = {}
    if not base.exists():
        return fora
    for c in base.rglob("captura.json"):
        if c.parts[-4] not in ("CTE57", "EVENTO_CTE"):
            continue
        try:
            d = json.loads(c.read_text("utf-8"))
        except Exception:
            continue
        d["_pasta"] = c.parent
        fora[d["id_documento"]] = d
    return fora


def nsus_do_bruto(caminho: Path) -> list:
    """Lê o `docZip/@NSU` direto da resposta preservada — a fonte da verdade."""
    try:
        r = ET.fromstring(caminho.read_bytes())
    except Exception as e:
        print(f"    (não foi possível reler o bruto: {e})")
        return []
    fora = []
    for el in r.iter():
        if el.tag.rsplit("}", 1)[-1] == "docZip":
            fora.append(el.get("NSU") or "(sem atributo)")
    return fora


def main() -> int:
    emp = TRX
    if "--empresa" in sys.argv:
        emp = "".join(ch for ch in sys.argv[sys.argv.index("--empresa") + 1]
                      if ch.isdigit())

    raiz = raiz_dados()
    autor = ac.carregar(raiz)
    cad = cad_mod.carregar(raiz)
    repo_cp = cpm.RepositorioCheckpoint(raiz)

    titulo("CICLO ÚNICO — distribuição de CT-e")
    print(f"  empresa .......... {mascarar(emp)}")
    print(f"  serviço .......... cteDistDFeInteresse")
    print(f"  ambiente ......... produção")
    print(f"  cUFAutor ......... {autor.cuf} ({autor.uf})")
    print(f"  raiz de dados .... {raiz}")

    cp_antes = repo_cp.carregar(emp, svc.SERVICO, "PRODUCAO")
    arq_cp = raiz / emp / "ingestao" / "CTE_DISTRIBUICAO.producao.json"
    docs_antes = cte_no_acervo(raiz, emp)
    brutos_antes = {p for p in (raiz / emp).rglob("respostas/**/*.xml")}

    print(f"  ultNSU de partida  {cp_antes.ult_nsu}")

    el = svc.elegivel(raiz, emp, cad=cad)
    if not el.autorizada:
        titulo("NÃO EXECUTADO")
        print(f"  estado ........... {el.estado}")
        print(f"  motivo ........... {el.motivo}")
        if el.segundos_para_liberar:
            print(f"  libera em ........ {el.segundos_para_liberar}s "
                  f"({round(el.segundos_para_liberar / 60, 1)} min)")
        print("\n  Nenhuma consulta foi feita. Nada mudou.")
        return 1

    espia = {}

    def fabrica(identidade, ambiente):
        f = FonteEspiada(svc.fabrica_padrao(cad, raiz)(identidade, ambiente))
        espia["fonte"] = f
        return f

    print("\n  consultando ...", flush=True)
    r = svc.consultar_empresa(raiz, emp, fabrica, cad=cad, cuf=autor.cuf)

    resposta = espia.get("fonte").resposta if espia.get("fonte") else None
    cp_depois = repo_cp.carregar(emp, svc.SERVICO, "PRODUCAO")
    docs_depois = cte_no_acervo(raiz, emp)
    novos = {k: v for k, v in docs_depois.items() if k not in docs_antes}

    # ── o retorno ──────────────────────────────────────────────────────────
    titulo("RETORNO DO SERVIÇO")
    print(f"  cStat ............ {r.cstat}  ({r.estado})")
    if r.motivo:
        print(f"  motivo ........... {r.motivo}")
    if r.erro:
        print(f"  ERRO ............. {r.erro}")
    print(f"  documentos ....... {r.documentos}")
    print(f"  persistidos ...... {r.persistidos}   conflitos: {r.conflitos}")
    print(f"  indexados ........ {r.indexados}")
    print(f"  tentativa ........ {r.tentativa}")

    # ── 1. NSU ─────────────────────────────────────────────────────────────
    titulo("1. AUDITORIA DE NSU")
    da_resposta = [d.nsu for d in (resposta.documentos if resposta else ())]
    print(f"  {'extraído da resposta':<24} {'gravado no acervo':<24} confere")
    print(f"  {'-' * 24} {'-' * 24} -------")
    gravados = sorted((v["nsu"], k) for k, v in novos.items())
    for i, (nsu_disco, _id) in enumerate(gravados):
        origem = da_resposta[i] if i < len(da_resposta) else "?"
        marca = "sim" if origem == nsu_disco else "NÃO"
        print(f"  {origem or '(vazio)':<24} {nsu_disco or '(vazio)':<24} {marca}")
    if not gravados:
        print("  (nenhum documento novo neste lote)")

    if gravados:
        so_disco = [n for n, _ in gravados]
        posicional = [str(i).zfill(15) for i in range(len(so_disco))]
        print()
        print(f"  são a POSIÇÃO no lote (0,1,2…)?  "
              f"{'SIM — DEFEITO' if so_disco == posicional else 'não'}")
        print(f"  contíguos a partir de zero?      "
              f"{'SIM — suspeito' if so_disco == posicional else 'não'}")
        print(f"  faixa ...........................  "
              f"{so_disco[0]} … {so_disco[-1]}")

    # ── 2. índice ──────────────────────────────────────────────────────────
    titulo("2. AUDITORIA DE ÍNDICE")
    import sqlite3
    db = raiz / emp / "indice" / "documentos.db"
    print(f"  base ............. {db}")
    if db.exists():
        con = sqlite3.connect(db)
        por_especie = list(con.execute(
            "select especie, count(*) from documentos "
            "where especie in ('CTE57','EVENTO_CTE') group by 1"))
        total = sum(n for _, n in por_especie)
        print(f"  CT-e no ÍNDICE ... {total}   {dict(por_especie)}")
        print(f"  CT-e no ACERVO ... {len(docs_depois)}")
        print(f"  batem? ........... "
              f"{'sim' if total == len(docs_depois) else 'NÃO — ficou defasado'}")
        if novos:
            print("\n  os que entraram agora:")
            chaves = tuple(v["chave"] for v in novos.values() if v.get("chave"))
            if chaves:
                marcas = ",".join("?" * len(chaves))
                for esp, ch, num in con.execute(
                        f"select especie, chave, numero from documentos "
                        f"where chave in ({marcas}) order by especie, chave",
                        chaves):
                    print(f"    {esp:<11} {ch}  nº {num or '-'}")
    else:
        print("  a base de índice NÃO existe")

    # ── 3. resposta bruta ──────────────────────────────────────────────────
    titulo("3. AUDITORIA DE PRESERVAÇÃO")
    brutos_depois = {p for p in (raiz / emp).rglob("respostas/**/*.xml")}
    novos_brutos = sorted(brutos_depois - brutos_antes)
    print(f"  o serviço declarou preservada? {r.resposta_preservada}")
    if not novos_brutos:
        print("  NENHUM arquivo bruto novo no disco")
    for b in novos_brutos:
        manif = b.with_suffix(".json")
        d = json.loads(manif.read_text("utf-8")) if manif.exists() else {}
        no_disco = hashlib.sha256(b.read_bytes()).hexdigest()
        print(f"  arquivo .......... {b}")
        print(f"  manifesto ........ {manif}")
        print(f"  SHA-256 (rotina) . {d.get('sha256', '(sem manifesto)')}")
        print(f"  SHA-256 (do disco) {no_disco}")
        print(f"  confere? ......... "
              f"{'sim' if d.get('sha256') == no_disco else 'NÃO'}")
        print(f"  conferir() ....... {bruta.conferir(b)}")
        print(f"  tamanho .......... {b.stat().st_size} bytes")
        print(f"  empresa no recibo  {d.get('empresa')}")
        print(f"  quando ........... {d.get('quando')}")
        print(f"  tentativa ........ {d.get('tentativa')}")

        # A PROVA QUE ANTES ERA IMPOSSÍVEL: os NSU lidos do próprio bruto.
        do_bruto = nsus_do_bruto(b)
        if do_bruto:
            print(f"\n  docZip/@NSU lidos do arquivo bruto ({len(do_bruto)}):")
            print(f"    {', '.join(do_bruto[:8])}"
                  f"{' …' if len(do_bruto) > 8 else ''}")
            no_acervo = [n for n, _ in gravados]
            iguais = sorted(do_bruto) == sorted(no_acervo)
            print(f"  batem com o que foi gravado? "
                  f"{'SIM' if iguais else 'NÃO — divergência'}")

    # ── 4. checkpoint ──────────────────────────────────────────────────────
    titulo("4. AUDITORIA DE CHECKPOINT")
    print(f"  arquivo .......... {arq_cp}")
    print(f"  {'':<18} {'ANTES':<20} {'DEPOIS':<20}")
    print(f"  {'-' * 18} {'-' * 20} {'-' * 20}")
    print(f"  {'ult_nsu':<18} {cp_antes.ult_nsu:<20} {cp_depois.ult_nsu:<20}")
    print(f"  {'max_nsu':<18} {getattr(cp_antes, 'max_nsu', '') or '(vazio)':<20} "
          f"{getattr(cp_depois, 'max_nsu', '') or '(vazio)':<20}")
    print(f"  {'existe em disco':<18} {str(bool(cp_antes.ult_nsu != '' and arq_cp.exists())):<20} "
          f"{str(arq_cp.exists()):<20}")
    print(f"\n  avançou? ......... {r.checkpoint_avancou}")

    from ingestao.distribuicao import nsu_int
    u = nsu_int(cp_depois.ult_nsu or "0")
    m = nsu_int(getattr(cp_depois, "max_nsu", "") or "0")
    print(f"  fila restante .... {max(0, m - u)}")
    print(f"  leitura .......... "
          f"{'há mais páginas' if u < m else 'fila esgotada'}")

    titulo("VEREDITO")
    problemas = []
    if r.erro:
        problemas.append(f"o ciclo terminou com erro: {r.erro}")
    if r.documentos and r.persistidos < r.documentos:
        problemas.append("persistência parcial")
    if gravados and [n for n, _ in gravados] == [
            str(i).zfill(15) for i in range(len(gravados))]:
        problemas.append("os NSU gravados são a posição no lote")
    if novos and not novos_brutos:
        problemas.append("houve documento novo e nenhuma resposta preservada")
    if r.documentos and r.checkpoint_avancou and r.persistidos < r.documentos:
        problemas.append("o checkpoint avançou com gravação incompleta")

    if problemas:
        for p in problemas:
            print(f"  ✗ {p}")
        return 2
    print("  Nenhum dos defeitos conhecidos apareceu neste ciclo.")
    print(f"  Uma página consultada. Restam {max(0, m - u)} na fila; este")
    print("  script não as busca — é decisão de quem opera.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
