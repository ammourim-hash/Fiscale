# -*- coding: utf-8 -*-
"""plano.py — o plano de contas de UMA empresa.

AS REGRAS, E POR QUE CADA UMA
    • O código é hierárquico por ponto ("1", "1.1", "1.1.01"). A conta-pai é
      DERIVADA do código, não digitada: dois campos que dizem a mesma coisa
      acabam dizendo coisas diferentes.
    • A pai precisa existir e ser SINTÉTICA. Uma analítica com filhas seria
      uma conta que recebe lançamento e ao mesmo tempo soma outras.
    • O grupo (ATIVO, PASSIVO, ..., DESPESA) é herdado da pai e não pode
      divergir dela. É o grupo que impede transferência entre contas de virar
      receita ou despesa — então ele não pode ser opinião de cada linha.
    • Só ANALÍTICA ATIVA recebe lançamento.
    • Conta nunca é apagada: é desativada. Um lançamento antigo apontando
      para uma conta que sumiu seria um razão que não fecha mais.
    • Natureza e tipo só mudam enquanto a conta não tem lançamento.

ECD/ECF
    `ref_ecd` e `ref_ecf` guardam o código referencial para o mapeamento que
    virá depois. Nesta fase eles são só guardados — ECD e ECF não são gerados.
"""
from __future__ import annotations

import csv
import io
import re

from . import modelo
from .base import Base, agora, novo_id

_CODIGO = re.compile(r"\d+(\.\d+)*")


def _codigo(texto) -> str:
    c = str(texto or "").strip()
    if not _CODIGO.fullmatch(c):
        raise modelo.Invalido(
            "código de conta fora do formato (use números separados por "
            "ponto, como 1.1.01): %r" % (texto,))
    return c


def pai_de(codigo: str) -> str:
    return codigo.rsplit(".", 1)[0] if "." in codigo else ""


def obter(b: Base, conta_id: str) -> dict | None:
    r = b.con.execute("SELECT * FROM conta WHERE id=?", (conta_id,)).fetchone()
    return dict(r) if r else None


def por_codigo(b: Base, codigo: str) -> dict | None:
    r = b.con.execute("SELECT * FROM conta WHERE codigo=?",
                      (str(codigo).strip(),)).fetchone()
    return dict(r) if r else None


def _tem_partida(b: Base, conta_id: str) -> bool:
    return b.con.execute("SELECT 1 FROM partida WHERE conta_id=? LIMIT 1",
                         (conta_id,)).fetchone() is not None


def _tem_filha(b: Base, codigo: str, so_ativas=False) -> bool:
    sql = "SELECT 1 FROM conta WHERE codigo_pai=?"
    if so_ativas:
        sql += " AND ativa=1"
    return b.con.execute(sql + " LIMIT 1", (codigo,)).fetchone() is not None


def criar(b: Base, codigo, descricao, tipo, natureza, grupo=None,
          ref_ecd="", ref_ecf="", usuario="") -> dict:
    cod = _codigo(codigo)
    desc = str(descricao or "").strip()
    if not desc:
        raise modelo.Invalido("a conta precisa de descrição")
    t = modelo.conferir(tipo, modelo.TIPOS_CONTA, "tipo")
    n = modelo.conferir(natureza, modelo.NATUREZAS, "natureza")
    if por_codigo(b, cod):
        raise modelo.Invalido("já existe conta com o código %s" % cod)

    pai = pai_de(cod)
    g = (grupo or "").strip().upper()
    if pai:
        rp = por_codigo(b, pai)
        if not rp:
            raise modelo.Invalido(
                "a conta %s precisa da conta sintética %s antes" % (cod, pai))
        if rp["tipo"] != modelo.SINTETICA:
            raise modelo.Invalido(
                "a conta %s é analítica e não pode ter subcontas" % pai)
        if not g:
            g = rp["grupo"]
        elif g != rp["grupo"]:
            raise modelo.Invalido(
                "o grupo da conta (%s) diverge do grupo da conta %s (%s)"
                % (g, pai, rp["grupo"]))
    g = modelo.conferir(g, modelo.GRUPOS, "grupo")

    cid = novo_id()
    b.con.execute(
        "INSERT INTO conta (id, codigo, descricao, tipo, natureza, grupo, "
        "codigo_pai, ativa, ref_ecd, ref_ecf, criado_utc, criado_por) "
        "VALUES (?,?,?,?,?,?,?,1,?,?,?,?)",
        (cid, cod, desc, t, n, g, pai, str(ref_ecd or "").strip(),
         str(ref_ecf or "").strip(), agora(), usuario or ""))
    b.evento("CONTA", cid, "CRIADA", usuario, cod)
    return obter(b, cid)


def alterar(b: Base, conta_id: str, usuario="", **campos) -> dict:
    atual = obter(b, conta_id)
    if not atual:
        raise modelo.Invalido("conta não encontrada")
    novo = {}
    if "descricao" in campos:
        d = str(campos["descricao"] or "").strip()
        if not d:
            raise modelo.Invalido("a conta precisa de descrição")
        novo["descricao"] = d
    for k in ("ref_ecd", "ref_ecf"):
        if k in campos:
            novo[k] = str(campos[k] or "").strip()
    if "natureza" in campos:
        n = modelo.conferir(campos["natureza"], modelo.NATUREZAS, "natureza")
        if n != atual["natureza"] and _tem_partida(b, conta_id):
            raise modelo.Invalido(
                "a natureza não muda depois que a conta tem lançamento")
        novo["natureza"] = n
    if "tipo" in campos:
        t = modelo.conferir(campos["tipo"], modelo.TIPOS_CONTA, "tipo")
        if t != atual["tipo"]:
            if _tem_partida(b, conta_id):
                raise modelo.Invalido(
                    "o tipo não muda depois que a conta tem lançamento")
            if t == modelo.ANALITICA and _tem_filha(b, atual["codigo"]):
                raise modelo.Invalido(
                    "a conta tem subcontas e não pode virar analítica")
        novo["tipo"] = t
    if "ativa" in campos:
        ativa = 1 if campos["ativa"] in (True, 1, "1", "true", "sim") else 0
        if not ativa and _tem_filha(b, atual["codigo"], so_ativas=True):
            raise modelo.Invalido(
                "desative as subcontas de %s antes" % atual["codigo"])
        if ativa and atual["codigo_pai"]:
            rp = por_codigo(b, atual["codigo_pai"])
            if rp and not rp["ativa"]:
                raise modelo.Invalido(
                    "reative a conta %s antes" % atual["codigo_pai"])
        novo["ativa"] = ativa
    if not novo:
        return atual
    novo["alterado_utc"] = agora()
    novo["alterado_por"] = usuario or ""
    sets = ", ".join("%s=?" % k for k in novo)
    b.con.execute("UPDATE conta SET %s WHERE id=?" % sets,
                  (*novo.values(), conta_id))
    b.evento("CONTA", conta_id, "ALTERADA", usuario,
             ", ".join(k for k in novo if not k.startswith("alterado")))
    return obter(b, conta_id)


def listar(b: Base, incluir_inativas: bool = True) -> list:
    sql = "SELECT * FROM conta"
    if not incluir_inativas:
        sql += " WHERE ativa=1"
    linhas = [dict(r) for r in b.con.execute(sql)]
    # Ordem natural do código: "1.10" depois de "1.9".
    linhas.sort(key=lambda c: [int(p) for p in c["codigo"].split(".")])
    return linhas


def lancavel(b: Base, conta_id: str) -> dict:
    """A conta, se ela pode receber lançamento. Senão, recusa dizendo por quê."""
    c = obter(b, conta_id)
    if not c:
        raise modelo.Invalido("conta não encontrada neste plano")
    if c["tipo"] != modelo.ANALITICA:
        raise modelo.Invalido("a conta %s é sintética e não recebe lançamento"
                              % c["codigo"])
    if not c["ativa"]:
        raise modelo.Invalido("a conta %s está inativa" % c["codigo"])
    return c


# ── Contas padrão: papel → conta desta empresa ────────────────────────────
#
# É o que permite ao Contábil sugerir "D Mercadorias / C Fornecedores" sem
# saber qual é a conta de mercadorias DESTA empresa. O papel é vocabulário
# fechado (`modelo.PAPEIS`); a conta é escolhida por uma pessoa, e tem de ser
# analítica, ativa e de um grupo que faça sentido para o papel — ligar
# "Clientes" a uma conta de RECEITA inverteria o razão inteiro em silêncio.
def mapear(b: Base, papel: str, conta_id: str, usuario: str = "") -> dict:
    p = (papel or "").strip().upper()
    if p not in modelo.PAPEIS:
        raise modelo.Invalido("papel desconhecido: %r" % (papel,))
    if not conta_id:
        b.con.execute("DELETE FROM mapa_conta WHERE papel=?", (p,))
        b.evento("MAPA", p, "DESLIGADO", usuario)
        return {"papel": p, "conta_id": ""}
    c = lancavel(b, conta_id)
    grupos, rotulo = modelo.PAPEIS[p]
    if c["grupo"] not in grupos:
        raise modelo.Invalido(
            "%s (%s) espera conta de %s, e a conta %s é de %s"
            % (p, rotulo, " ou ".join(grupos), c["codigo"], c["grupo"]))
    b.con.execute(
        "INSERT INTO mapa_conta (papel, conta_id, definido_utc, definido_por) "
        "VALUES (?,?,?,?) ON CONFLICT(papel) DO UPDATE SET "
        "conta_id=excluded.conta_id, definido_utc=excluded.definido_utc, "
        "definido_por=excluded.definido_por",
        (p, c["id"], agora(), usuario or ""))
    b.evento("MAPA", p, "LIGADO", usuario, c["codigo"])
    return {"papel": p, "conta_id": c["id"], "codigo": c["codigo"],
            "descricao": c["descricao"]}


def conta_do_papel(b: Base, papel: str) -> dict | None:
    """A conta ligada ao papel, se ela ainda puder receber lançamento."""
    r = b.con.execute("SELECT conta_id FROM mapa_conta WHERE papel=?",
                      ((papel or "").strip().upper(),)).fetchone()
    if not r:
        return None
    c = obter(b, r["conta_id"])
    if not c or c["tipo"] != modelo.ANALITICA or not c["ativa"]:
        return None
    return c


def mapa(b: Base) -> list:
    """Todos os papéis, ligados ou não — a tela precisa mostrar o que falta."""
    saida = []
    for p, (grupos, rotulo) in modelo.PAPEIS.items():
        c = conta_do_papel(b, p)
        saida.append({"papel": p, "rotulo": rotulo, "grupos": list(grupos),
                      "conta_id": c["id"] if c else "",
                      "codigo": c["codigo"] if c else "",
                      "descricao": c["descricao"] if c else ""})
    return saida


# Colunas aceitas na planilha do plano. A primeira linha tem de ser o
# cabeçalho; a ordem das colunas não importa.
COLUNAS_CSV = ("codigo", "descricao", "tipo", "natureza", "grupo",
               "ref_ecd", "ref_ecf")


def importar_csv(b: Base, texto: str, usuario="") -> dict:
    """Cria as contas de uma planilha CSV. TUDO OU NADA: se uma linha for
    recusada, nenhuma conta entra — um plano pela metade é pior que plano
    nenhum, porque parece pronto."""
    if not (texto or "").strip():
        raise modelo.Invalido("planilha vazia")
    amostra = texto[:4096]
    delim = ";" if amostra.count(";") >= amostra.count(",") else ","
    leitor = csv.reader(io.StringIO(texto), delimiter=delim)
    linhas = list(leitor)
    cab = [modelo.normalizar_texto(c).lower().replace(" ", "_")
           for c in linhas[0]]
    cab = ["descricao" if c in ("descricao", "descrição") else c for c in cab]
    for obrig in ("codigo", "descricao", "tipo", "natureza"):
        if obrig not in cab:
            raise modelo.Invalido("a planilha precisa da coluna %r" % obrig)
    pos = {c: cab.index(c) for c in COLUNAS_CSV if c in cab}
    criadas, erros = [], []
    # Pai antes de filha, qualquer que seja a ordem da planilha.
    corpo = [(i + 2, l) for i, l in enumerate(linhas[1:]) if any(
        x.strip() for x in l)]

    def _chave(item):
        cod = item[1][pos["codigo"]].strip() if len(item[1]) > pos["codigo"] else ""
        try:
            return [int(p) for p in _codigo(cod).split(".")]
        except modelo.Invalido:
            return [10 ** 9]
    corpo.sort(key=_chave)
    b.con.execute("SAVEPOINT plano_csv")
    for n, l in corpo:
        def campo(nome):
            i = pos.get(nome)
            return l[i].strip() if i is not None and i < len(l) else ""
        tipo = campo("tipo").upper()
        tipo = {"S": modelo.SINTETICA, "A": modelo.ANALITICA}.get(tipo, tipo)
        nat = campo("natureza").upper()
        nat = {"D": modelo.DEVEDORA, "C": modelo.CREDORA}.get(nat, nat)
        try:
            c = criar(b, campo("codigo"), campo("descricao"), tipo, nat,
                      campo("grupo") or None, campo("ref_ecd"),
                      campo("ref_ecf"), usuario)
            criadas.append(c["codigo"])
        except modelo.Invalido as e:
            erros.append({"linha": n, "erro": str(e)})
    if erros:
        b.con.execute("ROLLBACK TO plano_csv")
        b.con.execute("RELEASE plano_csv")
        return {"ok": False, "criadas": [], "erros": erros}
    b.con.execute("RELEASE plano_csv")
    return {"ok": True, "criadas": criadas, "erros": []}
