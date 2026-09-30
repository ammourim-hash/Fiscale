# -*- coding: utf-8 -*-
"""bancos.py — contas bancárias e os movimentos delas.

DO MOVIMENTO AO LANÇAMENTO, SEMPRE POR MÃO HUMANA
    `gerar_lancamento()` é o único caminho do extrato ao razão, e ele exige
    que uma pessoa escolha a conta de contrapartida. O lançamento nasce
    PENDENTE (é `lancamentos.criar`, que não sabe nascer de outro jeito), e a
    ligação com o movimento nasce como SUGESTÃO — vira CONCILIADO quando o
    lançamento for confirmado.

TRANSFERÊNCIA ENTRE CONTAS NÃO VIRA RESULTADO
    Se o movimento é transferência (pela sugestão ou pela confirmação), a
    contrapartida não pode ser conta de RECEITA, CUSTO ou DESPESA. É a regra
    que impede o dinheiro que só trocou de banco de aparecer como faturamento.
"""
from __future__ import annotations

from datetime import date

from . import conciliacao, lancamentos, modelo, plano
from .base import Base, agora, novo_id


def norm_numero(s) -> str:
    t = "".join(c for c in str(s or "").upper() if c.isalnum())
    return t.lstrip("0") or ("0" if t else "")


def obter_conta(b: Base, conta_id: str) -> dict | None:
    r = b.con.execute("SELECT * FROM conta_bancaria WHERE id=?",
                      (conta_id,)).fetchone()
    return dict(r) if r else None


def achar_conta(b: Base, banco, agencia, numero) -> dict | None:
    alvo = (norm_numero(banco), norm_numero(agencia), norm_numero(numero))
    for r in b.con.execute("SELECT * FROM conta_bancaria"):
        if (norm_numero(r["banco"]), norm_numero(r["agencia"]),
                norm_numero(r["numero"])) == alvo:
            return dict(r)
    return None


def criar_conta(b: Base, banco, agencia, numero, descricao="",
                conta_contabil_id="") -> dict:
    if not str(numero or "").strip():
        raise modelo.Invalido("informe o número da conta bancária")
    if achar_conta(b, banco, agencia, numero):
        raise modelo.Invalido("esta conta bancária já está cadastrada")
    if conta_contabil_id:
        c = plano.lancavel(b, conta_contabil_id)
        if c["grupo"] != modelo.ATIVO:
            raise modelo.Invalido("a conta contábil do banco é do ATIVO")
    cid = novo_id()
    b.con.execute(
        "INSERT INTO conta_bancaria (id, banco, agencia, numero, descricao, "
        "conta_contabil_id, criado_utc) VALUES (?,?,?,?,?,?,?)",
        (cid, str(banco or "").strip(), str(agencia or "").strip(),
         str(numero).strip(), str(descricao or "").strip()[:120],
         conta_contabil_id or "", agora()))
    return obter_conta(b, cid)


def alterar_conta(b: Base, conta_id: str, descricao=None,
                  conta_contabil_id=None) -> dict:
    atual = obter_conta(b, conta_id)
    if not atual:
        raise modelo.Invalido("conta bancária não encontrada")
    if conta_contabil_id:
        c = plano.lancavel(b, conta_contabil_id)
        if c["grupo"] != modelo.ATIVO:
            raise modelo.Invalido("a conta contábil do banco é do ATIVO")
    b.con.execute(
        "UPDATE conta_bancaria SET descricao=?, conta_contabil_id=? WHERE id=?",
        (atual["descricao"] if descricao is None else str(descricao)[:120],
         atual["conta_contabil_id"] if conta_contabil_id is None
         else conta_contabil_id, conta_id))
    return obter_conta(b, conta_id)


def listar_contas(b: Base) -> list:
    saida = []
    for r in b.con.execute("SELECT * FROM conta_bancaria ORDER BY banco, numero"):
        d = dict(r)
        d["movimentos"] = b.con.execute(
            "SELECT COUNT(*) FROM movimento WHERE conta_bancaria_id=?",
            (d["id"],)).fetchone()[0]
        cc = plano.obter(b, d["conta_contabil_id"]) if d["conta_contabil_id"] else None
        d["conta_contabil"] = ("%s %s" % (cc["codigo"], cc["descricao"])) if cc else ""
        saida.append(d)
    return saida


# ── Movimentos ────────────────────────────────────────────────────────────
def obter_movimento(b: Base, mov_id: str) -> dict | None:
    r = b.con.execute("SELECT * FROM movimento WHERE id=?", (mov_id,)).fetchone()
    return dict(r) if r else None


def categoria_efetiva(m: dict) -> str:
    return m.get("categoria_confirmada") or m.get("categoria") or \
        modelo.NAO_IDENTIFICADO


def buscar(b: Base, conta_bancaria_id="", de="", ate="", valor="", texto="",
           documento="", contraparte_doc="", contraparte="", conta_contabil_id="",
           estado="", categoria="", importacao_id="", limite: int = 1000) -> list:
    """A busca da conciliação: valor, data, descrição, documento, CNPJ/CPF,
    cliente/fornecedor e conta contábil. Tudo dentro do banco DESTA empresa."""
    sql = ["SELECT m.* FROM movimento m WHERE 1=1"]
    a = []
    if conta_bancaria_id:
        sql.append("AND m.conta_bancaria_id=?"); a.append(conta_bancaria_id)
    if importacao_id:
        sql.append("AND m.id IN (SELECT movimento_id FROM movimento_origem "
                   "WHERE importacao_id=?)"); a.append(importacao_id)
    if de:
        sql.append("AND m.data>=?"); a.append(modelo.data_iso(de))
    if ate:
        sql.append("AND m.data<=?"); a.append(modelo.data_iso(ate))
    if str(valor or "").strip():
        c = abs(modelo.centavos(valor))
        sql.append("AND ABS(m.valor)=?"); a.append(c)
    if texto:
        sql.append("AND m.descricao LIKE ?"); a.append("%" + texto + "%")
    if documento:
        sql.append("AND (m.documento LIKE ? OR m.id_transacao LIKE ?)")
        a += ["%" + documento + "%"] * 2
    if contraparte_doc:
        d = modelo.so_digitos(contraparte_doc)
        sql.append("AND (m.contraparte_doc=? OR m.descricao LIKE ?)")
        a += [d, "%" + d + "%"]
    if contraparte:
        sql.append("AND (m.contraparte_nome LIKE ? OR m.descricao LIKE ?)")
        a += ["%" + modelo.normalizar_texto(contraparte) + "%",
              "%" + contraparte + "%"]
    if conta_contabil_id:
        sql.append("AND m.id IN (SELECT c.movimento_id FROM conciliacao c "
                   "JOIN partida p ON p.lancamento_id=c.alvo_id WHERE "
                   "c.alvo_tipo='LANCAMENTO' AND c.desfeita_utc='' AND "
                   "p.conta_id=?)")
        a.append(conta_contabil_id)
    if estado:
        sql.append("AND m.estado_conciliacao=?")
        a.append(modelo.conferir(estado, modelo.ESTADOS_CONCILIACAO, "estado"))
    if categoria:
        cat = modelo.conferir(categoria, modelo.CATEGORIAS, "categoria")
        sql.append("AND COALESCE(NULLIF(m.categoria_confirmada,''), m.categoria)=?")
        a.append(cat)
    sql.append("ORDER BY m.data, m.importacao_id, m.linha LIMIT ?")
    a.append(int(limite))
    return [dict(r) for r in b.con.execute(" ".join(sql), a)]


def confirmar_categoria(b: Base, mov_id: str, categoria: str, usuario: str) -> dict:
    """Uma PESSOA diz o que o movimento é. A sugestão original fica."""
    if not (usuario or "").strip():
        raise modelo.Invalido("confirmar exige saber quem confirma")
    m = obter_movimento(b, mov_id)
    if not m:
        raise modelo.Invalido("movimento não encontrado")
    cat = modelo.conferir(categoria, modelo.CATEGORIAS, "categoria")
    b.con.execute("UPDATE movimento SET categoria_confirmada=?, confirmada_por=?"
                  " WHERE id=?", (cat, usuario, mov_id))
    b.evento("MOVIMENTO", mov_id, "CATEGORIA_CONFIRMADA", usuario, cat)
    return obter_movimento(b, mov_id)


def parear_transferencias(b: Base) -> int:
    """Liga as duas pontas de uma transferência entre contas da empresa.

    Conservador de propósito: só pareia quando UMA das pontas já foi
    reconhecida como transferência pelo texto. Um PIX de R$ 100 enviado a um
    fornecedor e um PIX de R$ 100 recebido de um cliente, no mesmo dia e em
    bancos diferentes, não são transferência — e parear só pelo valor diria
    que são.
    """
    soltos = [dict(r) for r in b.con.execute(
        "SELECT id, conta_bancaria_id, data, valor, categoria, "
        "categoria_confirmada FROM movimento WHERE par_id='' AND valor IS NOT "
        "NULL AND valor<>0 AND data<>''")]
    feitos, usados = 0, set()
    for a in soltos:
        if a["id"] in usados or categoria_efetiva(a) != modelo.TRANSFERENCIA:
            continue
        da = date.fromisoformat(a["data"])
        for c in soltos:
            if c["id"] in usados or c["id"] == a["id"] or \
                    c["conta_bancaria_id"] == a["conta_bancaria_id"] or \
                    c["valor"] != -a["valor"]:
                continue
            if abs((date.fromisoformat(c["data"]) - da).days) > 1:
                continue
            if c["categoria_confirmada"] and \
                    c["categoria_confirmada"] != modelo.TRANSFERENCIA:
                continue
            b.con.execute("UPDATE movimento SET par_id=? WHERE id=?",
                          (c["id"], a["id"]))
            b.con.execute(
                "UPDATE movimento SET par_id=?, categoria=?, sugestao=?, "
                "confianca=?, regra=? WHERE id=?",
                (a["id"], modelo.TRANSFERENCIA,
                 "NÃO É RECEITA NEM DESPESA — outra ponta na conta da empresa",
                 modelo.ALTA, "PAR_DE_TRANSFERENCIA", c["id"]))
            usados.update((a["id"], c["id"]))
            feitos += 1
            break
    return feitos


# Categoria do movimento → papel da contrapartida. É o que faz "recebimento
# bancário" sugerir `D Banco / C Clientes` e "pagamento de fornecedor" sugerir
# `D Fornecedores / C Banco` sem ninguém procurar a conta na lista. Só as
# categorias em que a contrapartida é ÓBVIA entram aqui: tarifa, tributo e
# salário dependem da conta que cada escritório usa, e chutar seria pior.
PAPEL_DA_CATEGORIA = {
    modelo.CLIENTE: "CLIENTES",
    modelo.FORNECEDOR: "FORNECEDORES",
}


def contrapartida_sugerida(b: Base, mov: dict) -> dict | None:
    """A conta que o Contábil sugere como contrapartida deste movimento."""
    papel = PAPEL_DA_CATEGORIA.get(categoria_efetiva(mov))
    return plano.conta_do_papel(b, papel) if papel else None


def gerar_lancamento(b: Base, mov_id: str, conta_contrapartida_id: str,
                     historico: str, usuario: str) -> dict:
    """Movimento → lançamento PENDENTE, com a contrapartida escolhida por gente.

    Sem contrapartida informada, usa a do papel mapeado para a categoria
    (cliente → Clientes, fornecedor → Fornecedores). Continua sendo sugestão:
    o lançamento nasce PENDENTE e a tela mostra qual conta entrou."""
    m = obter_movimento(b, mov_id)
    if not m:
        raise modelo.Invalido("movimento não encontrado")
    if not conta_contrapartida_id:
        c = contrapartida_sugerida(b, m)
        if c is None:
            raise modelo.Invalido(
                "escolha a conta de contrapartida (ou ligue o papel da "
                "categoria a uma conta em Plano de Contas → Contas padrão)")
        conta_contrapartida_id = c["id"]
    if not m["valor"]:
        raise modelo.Invalido("movimento sem valor não gera lançamento")
    if not m["data"]:
        raise modelo.Invalido("movimento sem data não gera lançamento")
    cb = obter_conta(b, m["conta_bancaria_id"])
    if not cb or not cb["conta_contabil_id"]:
        raise modelo.Invalido("associe esta conta bancária a uma conta contábil "
                              "do plano antes de lançar")
    banco_cc = plano.lancavel(b, cb["conta_contabil_id"])
    contra = plano.lancavel(b, conta_contrapartida_id)
    if contra["id"] == banco_cc["id"]:
        raise modelo.Invalido("a contrapartida não pode ser a própria conta do banco")
    cat = categoria_efetiva(m)
    if cat == modelo.TRANSFERENCIA and contra["grupo"] in modelo.GRUPOS_RESULTADO:
        raise modelo.Invalido(
            "transferência entre contas não é receita nem despesa: a "
            "contrapartida não pode ser do grupo %s" % contra["grupo"])
    if conciliacao.link_ativo(b, mov_id, modelo.ALVO_LANCAMENTO):
        raise modelo.Invalido("este movimento já está ligado a um lançamento")

    valor = abs(m["valor"])
    if m["valor"] > 0:
        partidas = [{"conta_id": banco_cc["id"], "tipo": "D", "valor": valor},
                    {"conta_id": contra["id"], "tipo": "C", "valor": valor}]
    else:
        partidas = [{"conta_id": contra["id"], "tipo": "D", "valor": valor},
                    {"conta_id": banco_cc["id"], "tipo": "C", "valor": valor}]
    lc = lancamentos.criar(
        b, m["data"], m["data"][:7],
        historico or m["descricao"] or "movimento bancário", partidas,
        modelo.BANCO, documento=m["documento"] or m["id_transacao"],
        lote="EXTRATO %s" % m["importacao_id"][:8], usuario=usuario)
    conciliacao.ligar(b, mov_id, modelo.ALVO_LANCAMENTO, lc["id"], usuario,
                      motivo="lançamento gerado do movimento")
    # A outra ponta da transferência, quando a contrapartida é o banco dela.
    if m["par_id"]:
        par = obter_movimento(b, m["par_id"])
        cb_par = obter_conta(b, par["conta_bancaria_id"]) if par else None
        if cb_par and cb_par["conta_contabil_id"] == contra["id"] and \
                not conciliacao.link_ativo(b, par["id"], modelo.ALVO_LANCAMENTO):
            conciliacao.ligar(b, par["id"], modelo.ALVO_LANCAMENTO, lc["id"],
                              usuario, motivo="outra ponta da transferência")
    b.evento("MOVIMENTO", mov_id, "LANCAMENTO_GERADO", usuario,
             "lançamento nº %s (PENDENTE)" % lc["numero"])
    return lancamentos.obter(b, lc["id"])
