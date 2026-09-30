# -*- coding: utf-8 -*-
"""fatos.py — do documento fiscal apurado ao fato contábil, por competência.

A CADEIA, E ONDE CADA ELO JÁ MORAVA
    documento fiscal   acervo da ingestão (XML imutável, `id_documento`)
    operação fiscal    `ingestao.normalizacao.Operacao` + `ingestao.vendas`
    FATO CONTÁBIL      aqui
    sugestão           aqui (em PAPEL de conta, nunca em conta adivinhada)
    lançamento         `contabil.lancamentos`, sempre PENDENTE

    Os dois primeiros elos são do Fiscal e **não são reescritos**: este módulo
    lê a operação já normalizada e a natureza já classificada por CFOP. Não há
    parser novo, não há tabela de CFOP nova, não há segundo acervo.

O QUE O FATO GUARDA, E POR QUÊ
    empresa (é o banco), competência, `id_documento`, origem, tipo de
    operação, valor, situação e o CAMINHO até o XML original. Com isso o
    Contábil responde as duas perguntas da fase — "quais documentos formaram
    os lançamentos desta competência?" e "qual lançamento nasceu desta
    nota?" — sem pedir nada de volta ao Fiscal.

UM FATO POR DOCUMENTO
    A chave é o `id_documento`. Sincronizar duas vezes ATUALIZA o mesmo fato;
    não cria o segundo. E se o documento mudou de situação no acervo (foi
    cancelado depois), o fato acompanha — e, se já havia lançamento, isso vira
    DIVERGÊNCIA à vista, nunca uma correção silenciosa.

A COMPETÊNCIA É EXPLÍCITA
    Cada sincronização é de UMA competência, e o fato carrega a dela. A
    competência sai do documento (a mesma que a apuração usa), nunca da data
    em que alguém rodou a sincronização.

O QUE ESTE MÓDULO NÃO FAZ
    Não calcula tributo, não decide anexo, não mexe no acervo nem no índice,
    não cria conta no plano e não confirma lançamento. A sugestão sai em
    PAPEL; sem o papel mapeado, o fato fica SEM_LANCAMENTO dizendo qual falta.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import competencias, fiscal, lancamentos, modelo, plano
from .base import Base, agora

ORIGEM = modelo.FISCAL

# CFOP de ativo imobilizado / uso e consumo. São dois códigos, não uma tabela:
# a tabela de CFOP é de `ingestao.vendas` e continua sendo só dela. Aqui eles
# são separados porque a CONTA é outra — imobilizado não é mercadoria.
_GRUPOS_IMOBILIZADO = ("551", "552")

# tipo do fato → (papel do débito, papel do crédito). É a única "regra
# contábil" desta fase, e está escrita num lugar só. Tipo fora daqui não tem
# sugestão — e não ter sugestão é uma resposta legítima.
SUGESTAO = {
    modelo.RECEITA_SERVICO: ("CLIENTES", "RECEITA_SERVICOS"),
    modelo.RECEITA_VENDA: ("CLIENTES", "RECEITA_VENDAS"),
    modelo.COMPRA_MERCADORIA: ("MERCADORIAS", "FORNECEDORES"),
    modelo.COMPRA_IMOBILIZADO: ("IMOBILIZADO", "FORNECEDORES"),
    modelo.SERVICO_TOMADO: ("SERVICOS_TOMADOS", "FORNECEDORES"),
    modelo.FRETE_TOMADO: ("FRETES", "FORNECEDORES"),
    modelo.DEVOLUCAO_VENDA: ("DEVOLUCOES_VENDAS", "CLIENTES"),
    modelo.DEVOLUCAO_COMPRA: ("FORNECEDORES", "DEVOLUCOES_COMPRAS"),
}

# Por que um tipo não tem sugestão. A frase vai para a tela: "sem sugestão" e
# nada mais deixaria a pessoa procurando um defeito que não existe.
SEM_SUGESTAO = {
    modelo.TRANSFERENCIA_ESTABELECIMENTO:
        "transferência entre estabelecimentos não é receita nem despesa — "
        "lance à mão se houver movimentação de estoque",
    modelo.OUTRA_ENTRADA: "remessa, retorno ou operação sem efeito de compra",
    modelo.OUTRA_SAIDA: "remessa, retorno ou operação sem efeito de venda",
    modelo.SEM_EFEITO: "documento fora da receita",
    modelo.INDETERMINADO: "natureza não reconhecida pelo CFOP",
}


def _modulo(nome: str):
    return fiscal._modulo_backend(nome)


def _centavos(v) -> int | None:
    if v is None:
        return None
    try:
        return modelo.centavos(Decimal(str(v)))
    except Exception:
        return None


def _id_fato(id_documento: str) -> str:
    return hashlib.sha256(("fato:" + id_documento).encode("utf-8")).hexdigest()[:24]


# ── Classificação: operação fiscal → tipo contábil ────────────────────────
def tipo_de_mercadoria(sentido: str, natureza_fiscal: str, cfops) -> str:
    """NF-e/NFC-e: o que ela é para a contabilidade.

    O CFOP do XML é sempre o do EMITENTE. Numa compra, quem emitiu foi o
    fornecedor: o código diz "venda" (5.102) e o que ele significa para nós é
    "compra". É por isso que o sentido entra na decisão — sem ele, toda nota de
    entrada viraria receita.
    """
    grupos = {c[1:4] for c in cfops if len(c) == 4}
    if sentido == modelo.ENTRADA:
        if grupos & set(_GRUPOS_IMOBILIZADO):
            return modelo.COMPRA_IMOBILIZADO
        if natureza_fiscal == "VENDA":
            return modelo.COMPRA_MERCADORIA
        if natureza_fiscal == "DEVOLUCAO":
            # O cliente devolveu o que compramos dele? Não: quem emite
            # devolução de venda é quem está devolvendo a mercadoria que
            # comprou. Entrando aqui, é a nossa venda que voltou.
            return modelo.DEVOLUCAO_VENDA
        if natureza_fiscal == "TRANSFERENCIA":
            return modelo.TRANSFERENCIA_ESTABELECIMENTO
        if natureza_fiscal == "NAO_RECEITA":
            return modelo.OUTRA_ENTRADA
        return modelo.INDETERMINADO
    if natureza_fiscal == "VENDA":
        # Saída de imobilizado ou de material de uso e consumo não é receita
        # de venda: é baixa de ativo, e o lançamento depende do valor contábil
        # do bem — conta que este módulo não faz.
        if grupos & set(_GRUPOS_IMOBILIZADO):
            return modelo.OUTRA_SAIDA
        return modelo.RECEITA_VENDA
    if natureza_fiscal == "DEVOLUCAO":
        return modelo.DEVOLUCAO_COMPRA
    if natureza_fiscal == "TRANSFERENCIA":
        return modelo.TRANSFERENCIA_ESTABELECIMENTO
    if natureza_fiscal == "NAO_RECEITA":
        return modelo.OUTRA_SAIDA
    return modelo.INDETERMINADO


def _papeis(tipo: str) -> tuple:
    return SUGESTAO.get(tipo, ("", ""))


# ── Leitura do Fiscal ──────────────────────────────────────────────────────
def _ids_da_competencia(dados, empresa: str, comp: str) -> dict:
    """`{id_documento: linha do índice}` da competência. Só leitura (mode=ro).

    O índice já sabe de qual competência cada documento é: perguntar a ele
    evita abrir e interpretar o acervo inteiro para descobrir o que ele já
    tinha registrado.
    """
    arq = fiscal.caminho_indice(dados, empresa)
    if not arq or not arq.exists():
        return {}
    con = sqlite3.connect(arq.as_uri() + "?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        return {r["id_documento"]: dict(r) for r in con.execute(
            "SELECT * FROM documentos WHERE estado='INDEXADO' AND "
            "substr(competencia,1,7)=? AND especie IN ('NFE55','NFCE65','CTE57')",
            (comp,))}
    finally:
        con.close()


def _caminho_acervo(dados, empresa: str, especie: str, id_doc: str) -> str:
    """O caminho do XML no acervo, pela regra do próprio acervo — nunca
    montado à mão aqui. Vazio quando o acervo não sabe dizer."""
    try:
        acv = _modulo("ingestao.acervo")
        p = acv.abrir(dados, empresa).caminho_canonico(especie, id_doc)
        return str(Path(p).relative_to(Path(dados)))
    except Exception:
        return ""


def competencias_no_fiscal(dados, empresa: str) -> list:
    """Os meses que o ÍNDICE fiscal conhece para esta empresa.

    É o que permite a tela oferecer uma competência que o Contábil ainda não
    tem — sem isso, um mês novo nunca apareceria na lista e ninguém
    conseguiria sincronizá-lo pela primeira vez.

    Só o índice: varrer os XML de NFS-e para descobrir meses custaria abrir
    milhares de arquivos a cada abertura de tela. Para um mês que só tenha
    NFS-e, a tela tem o campo de digitar a competência.
    """
    arq = fiscal.caminho_indice(dados, empresa)
    if not arq or not arq.exists():
        return []
    con = sqlite3.connect(arq.as_uri() + "?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT substr(competencia,1,7) c FROM documentos WHERE "
            "estado='INDEXADO' AND competencia<>'' AND especie IN "
            "('NFE55','NFCE65','CTE57') ORDER BY c DESC") if r[0]]
    finally:
        con.close()


def _de_operacao(op, natureza_fiscal: str, cfops: tuple) -> dict:
    """`Operacao` normalizada → os campos do fato. Nada é recalculado."""
    ret = op.retencoes
    detalhe = {k: str(getattr(ret, k)) for k in
               ("pis", "cofins", "csll", "irrf", "iss")
               if getattr(ret, k, None) is not None}
    total_ret = sum((_centavos(v) or 0) for v in
                    (ret.pis, ret.cofins, ret.csll, ret.irrf, ret.iss))
    return {
        "id_documento": op.id_documento,
        "competencia": op.competencia.strftime("%Y-%m") if op.competencia else "",
        "especie": op.especie,
        "sentido": op.sentido,
        "situacao": op.situacao,
        "entra_na_receita": 1 if op.entra_na_receita else 0,
        "valor": _centavos(op.valor_bruto),
        "retencoes": total_ret,
        "retencoes_detalhe": json.dumps(detalhe, ensure_ascii=False),
        "numero": op.numero or "", "serie": op.serie or "",
        "chave": op.origem.chave or "",
        "data_emissao": op.data_emissao.isoformat() if op.data_emissao else "",
        "contraparte_doc": modelo.so_digitos(op.contraparte),
        "contraparte_nome": "",
        "cfops": ",".join(cfops),
        "arquivo": op.origem.arquivo or "",
        "caminho": op.origem.caminho or "",
        "hash_conteudo": op.origem.hash_conteudo or "",
        "fonte_leitor": op.origem.fonte_leitor or "",
        "natureza_fiscal": natureza_fiscal,
    }


def ler_do_fiscal(dados, empresa: str, competencia: str) -> list:
    """Todos os documentos fiscais da competência, já como campos de fato.

    Três fontes, as mesmas que a apuração usa — nenhuma leitura nova:
      NF-e/NFC-e  acervo, via `conferencia.operacoes_nfe` (parser + normalização)
      NFS-e       `core.carregar_notas`, via `normalizacao.de_nota_core`
      CT-e        o índice, porque o CT-e não passa pela normalização de notas
    """
    comp = modelo.competencia(competencia)
    empresa = modelo.empresa_valida(empresa)
    nz = _modulo("ingestao.normalizacao")
    conf = _modulo("ingestao.conferencia")
    vendas = _modulo("ingestao.vendas")
    saida, vistos = [], set()

    # 1 · NF-e e NFC-e do acervo, só as da competência (lista vinda do índice).
    linhas = _ids_da_competencia(dados, empresa, comp)
    ids_nota = {i for i, r in linhas.items()
                if r["especie"] in ("NFE55", "NFCE65")}
    if ids_nota:
        for op, falha in conf.operacoes_nfe(dados, empresa, ids=ids_nota):
            if op is None or op.id_documento in vistos:
                continue
            cfops = tuple(sorted({i.cfop for i in op.itens if i.cfop}))
            natureza = ""
            v = vendas.classificar(op, empresa)
            if v is not None:
                natureza = v.natureza                 # saída: quem classifica é o Fiscal
            elif cfops:
                # Entrada: o CFOP é do emitente. `classificar_cfop` devolve
                # NAO_RECEITA para 1/2/3 (não é venda NOSSA), então o que vale
                # é o GRUPO do código — lido pela mesma função, com o dígito
                # de origem trocado. A tabela continua sendo a de `vendas.py`.
                naturezas = {vendas.classificar_cfop("5" + c[1:]) for c in cfops}
                natureza = naturezas.pop() if len(naturezas) == 1 else "INDETERMINADA"
            d = _de_operacao(op, natureza, cfops)
            d["tipo"] = (modelo.SEM_EFEITO if not op.entra_na_receita
                         else tipo_de_mercadoria(op.sentido, natureza, cfops))
            linha = linhas.get(op.id_documento) or {}
            d["contraparte_nome"] = linha.get("contraparte_nome") or ""
            if d["competencia"] == comp:
                saida.append(d)
                vistos.add(op.id_documento)

    # 2 · NFS-e, pelo leitor que a apuração já usa.
    pasta = Path(dados) / empresa
    if (pasta / "xmls").exists():
        try:
            core = _modulo("core")
            notas = core.carregar_notas(pasta)
        except Exception:
            notas = []
        for n in notas:
            op = nz.de_nota_core(n, empresa)
            if not op.id_documento or op.id_documento in vistos:
                continue
            d = _de_operacao(op, "", ())
            if d["competencia"] != comp:
                continue
            emitida = op.sentido == nz.SAIDA
            d["tipo"] = (modelo.SEM_EFEITO if not op.entra_na_receita else
                         modelo.RECEITA_SERVICO if emitida else modelo.SERVICO_TOMADO)
            d["contraparte_nome"] = (n.get("toma_nome") if emitida
                                     else n.get("emit_nome")) or ""
            d["arquivo"] = n.get("arquivo") or d["arquivo"]
            d["caminho"] = d["caminho"] or str(
                Path(empresa) / "xmls" / (n.get("arquivo") or ""))
            saida.append(d)
            vistos.add(op.id_documento)

    # 3 · CT-e, pelo índice. Ele não passa pela normalização de notas — não
    #     tem item nem CFOP —, e é por isso que vem do índice e não do acervo.
    for id_doc, r in linhas.items():
        if r["especie"] != "CTE57" or id_doc in vistos:
            continue
        papel = (r["papel"] or "").upper()
        emitido = papel == "EMITENTE"
        cancelado = "CANCEL" in (r["situacao"] or "").upper()
        saida.append({
            "id_documento": id_doc, "competencia": comp, "especie": "CTE57",
            "sentido": modelo.SAIDA if emitido else modelo.ENTRADA,
            "situacao": r["situacao"] or "", "entra_na_receita": 0 if cancelado else 1,
            "valor": _centavos(r["valor_total"]), "retencoes": 0,
            "retencoes_detalhe": "{}", "numero": r["numero"] or "",
            "serie": r["serie"] or "", "chave": r["chave"] or "",
            "data_emissao": (r["dh_emissao"] or "")[:10],
            "contraparte_doc": modelo.so_digitos(r["contraparte"]),
            "contraparte_nome": r["contraparte_nome"] or "", "cfops": "",
            "arquivo": "", "caminho": _caminho_acervo(dados, empresa,
                                                      "CTE57", id_doc),
            "hash_conteudo": r["hash_conteudo"] or "", "fonte_leitor": "indice",
            "natureza_fiscal": "",
            "tipo": (modelo.SEM_EFEITO if cancelado else
                     modelo.RECEITA_SERVICO if emitido else modelo.FRETE_TOMADO),
        })
        vistos.add(id_doc)
    return saida


# ── Sincronização ─────────────────────────────────────────────────────────
def sincronizar(b: Base, competencia: str, dados_fiscais=None,
                usuario: str = "") -> dict:
    """Traz os documentos da competência para dentro do Contábil.

    Idempotente: um fato por documento. O que já existe é ATUALIZADO (situação,
    valor, tipo) e o que sumiu do Fiscal é dito — nunca apagado às escondidas.

    `dados_fiscais` permite ler um acervo que não é o do banco aberto. Serve
    ao ensaio: ler o acervo real e gravar num banco temporário, sem escrever
    uma linha na produção.
    """
    comp = modelo.competencia(competencia)
    fonte = Path(dados_fiscais) if dados_fiscais else b.dados
    documentos = ler_do_fiscal(fonte, b.empresa, comp)
    novos = atualizados = inalterados = 0
    quando = agora()
    vistos = set()

    for d in documentos:
        vistos.add(d["id_documento"])
        deb, cred = _papeis(d["tipo"])
        motivo = SEM_SUGESTAO.get(d["tipo"], "")
        atual = b.con.execute("SELECT * FROM fato WHERE id_documento=?",
                              (d["id_documento"],)).fetchone()
        campos = dict(d, origem=ORIGEM, papel_debito=deb, papel_credito=cred,
                      motivo=motivo, sincronizado_utc=quando)
        if atual is None:
            campos["id"] = _id_fato(d["id_documento"])
            campos["estado"] = (modelo.SEM_EFEITO if d["tipo"] == modelo.SEM_EFEITO
                                else modelo.SEM_LANCAMENTO)
            colunas = ", ".join(campos)
            b.con.execute("INSERT INTO fato (%s) VALUES (%s)"
                          % (colunas, ",".join("?" * len(campos))),
                          tuple(campos.values()))
            novos += 1
            continue
        # Já existia: o estado é de quem o gerou (LANCADO, IGNORADO) e não
        # se perde numa ressincronização.
        mudou = any(atual[k] != v for k, v in campos.items()
                    if k != "sincronizado_utc")
        campos["estado"] = atual["estado"]
        if atual["estado"] == modelo.IGNORADO:
            # O motivo de ignorar é de quem ignorou. Ressincronizar não
            # apaga a frase que explica por que aquele documento não lança.
            campos["motivo"] = atual["motivo"]
        if atual["estado"] == modelo.SEM_EFEITO and d["tipo"] != modelo.SEM_EFEITO:
            campos["estado"] = (modelo.LANCADO if atual["lancamento_id"]
                                else modelo.SEM_LANCAMENTO)
        if (d["tipo"] == modelo.SEM_EFEITO and not atual["lancamento_id"]
                and atual["estado"] != modelo.IGNORADO):
            campos["estado"] = modelo.SEM_EFEITO
        sets = ", ".join("%s=?" % k for k in campos)
        b.con.execute("UPDATE fato SET %s WHERE id_documento=?" % sets,
                      (*campos.values(), d["id_documento"]))
        if mudou:
            atualizados += 1
            b.evento("FATO", atual["id"], "ATUALIZADO", usuario,
                     "situação %s → %s" % (atual["situacao"], d["situacao"]))
        else:
            inalterados += 1

    sumidos = [r["id_documento"] for r in b.con.execute(
        "SELECT id_documento FROM fato WHERE competencia=?", (comp,))
        if r["id_documento"] not in vistos]
    b.evento("COMPETENCIA", comp, "SINCRONIZADA", usuario,
             "%d documentos (%d novos)" % (len(documentos), novos))
    return {"competencia": comp, "documentos": len(documentos), "novos": novos,
            "atualizados": atualizados, "inalterados": inalterados,
            "nao_encontrados_no_fiscal": sumidos}


# ── Consulta ──────────────────────────────────────────────────────────────
def listar(b: Base, competencia: str = "", tipo: str = "", estado: str = "",
           id_documento: str = "") -> list:
    sql = ["SELECT * FROM fato WHERE 1=1"]
    a = []
    if competencia:
        sql.append("AND competencia=?"); a.append(modelo.competencia(competencia))
    if tipo:
        sql.append("AND tipo=?")
        a.append(modelo.conferir(tipo, modelo.TIPOS_FATO, "tipo"))
    if estado:
        sql.append("AND estado=?")
        a.append(modelo.conferir(estado, modelo.ESTADOS_FATO, "estado"))
    if id_documento:
        sql.append("AND id_documento=?"); a.append(id_documento)
    sql.append("ORDER BY data_emissao, numero")
    return [dict(r) for r in b.con.execute(" ".join(sql), a)]


def obter(b: Base, fato_id: str) -> dict | None:
    r = b.con.execute("SELECT * FROM fato WHERE id=? OR id_documento=?",
                      (fato_id, fato_id)).fetchone()
    return dict(r) if r else None


def sugestao(b: Base, fato: dict) -> dict:
    """A sugestão de lançamento deste fato, com as contas JÁ resolvidas —
    ou o motivo pelo qual não há sugestão. Não grava nada."""
    deb, cred = fato["papel_debito"], fato["papel_credito"]
    if not deb or not cred:
        return {"tem": False, "motivo": fato["motivo"] or
                SEM_SUGESTAO.get(fato["tipo"], "sem sugestão para este tipo")}
    if not fato["valor"]:
        return {"tem": False, "motivo": "documento sem valor lido"}
    faltando = [p for p in (deb, cred) if plano.conta_do_papel(b, p) is None]
    if faltando:
        return {"tem": False, "papeis": [deb, cred], "faltando": faltando,
                "motivo": "falta ligar %s a uma conta do plano (Plano de "
                          "Contas → Contas padrão)" % " e ".join(faltando)}
    c_deb = plano.conta_do_papel(b, deb)
    c_cred = plano.conta_do_papel(b, cred)
    valor = abs(fato["valor"])
    partidas = [{"conta_id": c_deb["id"], "codigo": c_deb["codigo"],
                 "descricao": c_deb["descricao"], "papel": deb, "tipo": "D",
                 "valor": valor},
                {"conta_id": c_cred["id"], "codigo": c_cred["codigo"],
                 "descricao": c_cred["descricao"], "papel": cred, "tipo": "C",
                 "valor": valor}]
    aviso = ""
    # Retenção: só entra se a conta do papel existir. Separar retenção sem ter
    # onde pôr daria um lançamento que não fecha.
    ret = fato["retencoes"] or 0
    if ret and fato["tipo"] == modelo.RECEITA_SERVICO:
        c_ret = plano.conta_do_papel(b, "RETENCOES_A_RECUPERAR")
        if c_ret and ret < valor:
            partidas[0]["valor"] = valor - ret
            partidas.insert(1, {"conta_id": c_ret["id"], "codigo": c_ret["codigo"],
                                "descricao": c_ret["descricao"],
                                "papel": "RETENCOES_A_RECUPERAR", "tipo": "D",
                                "valor": ret})
        else:
            aviso = ("há R$ %s de retenção neste documento e ela NÃO foi "
                     "separada: ligue RETENCOES_A_RECUPERAR a uma conta."
                     % modelo.reais(ret))
    if fato["especie"] == "NFCE65":
        aviso = ((aviso + " · ") if aviso else "") + (
            "venda a consumidor: se o recebimento foi à vista, troque a "
            "contrapartida de Clientes pela conta do caixa ou do banco")
    return {"tem": True, "partidas": partidas, "aviso": aviso,
            "historico": historico_de(fato), "valor": valor}


def historico_de(fato: dict) -> str:
    esp = {"NFE55": "NF-e", "NFCE65": "NFC-e", "NFSE": "NFS-e",
           "CTE57": "CT-e"}.get(fato["especie"], fato["especie"])
    quem = fato["contraparte_nome"] or fato["contraparte_doc"] or ""
    numero = (" nº %s" % fato["numero"]) if fato["numero"] else ""
    rotulo = modelo.ROTULO_TIPO_FATO.get(fato["tipo"], fato["tipo"])
    return ("%s%s — %s%s" % (esp, numero, rotulo,
                             (" — " + quem) if quem else ""))[:500]


# ── Do fato ao lançamento ─────────────────────────────────────────────────
def gerar_lancamento(b: Base, fato_id: str, usuario: str) -> dict:
    """Cria o lançamento PENDENTE deste fato. Nunca confirma nada."""
    f = obter(b, fato_id)
    if not f:
        raise modelo.Invalido("fato não encontrado nesta empresa")
    competencias.exigir_aberta(b, f["competencia"], "lançamento novo")
    if f["lancamento_id"]:
        lc = lancamentos.obter(b, f["lancamento_id"])
        if lc and lc["status"] != modelo.CANCELADO:
            raise modelo.Invalido(
                "este documento já tem o lançamento nº %s" % lc["numero"])
    s = sugestao(b, f)
    if not s["tem"]:
        raise modelo.Invalido(s["motivo"])
    lc = lancamentos.criar(
        b, f["data_emissao"] or (f["competencia"] + "-01"), f["competencia"],
        s["historico"], [{"conta_id": p["conta_id"], "tipo": p["tipo"],
                          "valor": p["valor"]} for p in s["partidas"]],
        modelo.FISCAL,
        documento=("%s %s" % (f["especie"], f["numero"])).strip(),
        lote="FISCAL %s" % f["competencia"], usuario=usuario)
    b.con.execute("UPDATE fato SET lancamento_id=?, estado=? WHERE id=?",
                  (lc["id"], modelo.LANCADO, f["id"]))
    b.evento("FATO", f["id"], "LANCAMENTO_GERADO", usuario,
             "lançamento nº %s (PENDENTE)" % lc["numero"])
    return lancamentos.obter(b, lc["id"])


def gerar_lancamentos_da_competencia(b: Base, competencia: str,
                                     usuario: str) -> dict:
    """Gera, de uma vez, o lançamento PENDENTE de cada fato que tem sugestão.
    O que não tem entra na lista de recusados, com o motivo."""
    comp = modelo.competencia(competencia)
    competencias.exigir_aberta(b, comp, "lançamento novo")
    feitos, recusados = [], []
    for f in listar(b, competencia=comp):
        if f["estado"] in (modelo.LANCADO, modelo.IGNORADO, modelo.SEM_EFEITO):
            continue
        try:
            lc = gerar_lancamento(b, f["id"], usuario)
            feitos.append({"id_documento": f["id_documento"],
                           "numero": lc["numero"], "total": lc["total"]})
        except modelo.Invalido as e:
            recusados.append({"id_documento": f["id_documento"],
                              "motivo": str(e)})
    return {"competencia": comp, "gerados": feitos, "recusados": recusados}


def ignorar(b: Base, fato_id: str, usuario: str, motivo: str) -> dict:
    """Marca o fato como "não lança" — com motivo, e reversível."""
    if not (motivo or "").strip():
        raise modelo.Invalido("ignorar um fato exige motivo")
    f = obter(b, fato_id)
    if not f:
        raise modelo.Invalido("fato não encontrado")
    if f["lancamento_id"]:
        raise modelo.Invalido("este fato já tem lançamento; cancele-o antes")
    b.con.execute("UPDATE fato SET estado=?, motivo=? WHERE id=?",
                  (modelo.IGNORADO, motivo.strip()[:300], f["id"]))
    b.evento("FATO", f["id"], "IGNORADO", usuario, motivo)
    return obter(b, f["id"])


def reconsiderar(b: Base, fato_id: str, usuario: str) -> dict:
    f = obter(b, fato_id)
    if not f:
        raise modelo.Invalido("fato não encontrado")
    estado = (modelo.SEM_EFEITO if f["tipo"] == modelo.SEM_EFEITO
              else modelo.LANCADO if f["lancamento_id"] else modelo.SEM_LANCAMENTO)
    b.con.execute("UPDATE fato SET estado=?, motivo=? WHERE id=?",
                  (estado, SEM_SUGESTAO.get(f["tipo"], ""), f["id"]))
    b.evento("FATO", f["id"], "RECONSIDERADO", usuario)
    return obter(b, f["id"])


def lancamento_cancelado(b: Base, lancamento_id: str) -> None:
    """O lançamento caiu: o fato volta a esperar um. Chamado por
    `lancamentos.cancelar` — o fato não pode ficar dizendo LANCADO."""
    for r in b.con.execute("SELECT id, tipo FROM fato WHERE lancamento_id=?",
                           (lancamento_id,)).fetchall():
        estado = (modelo.SEM_EFEITO if r["tipo"] == modelo.SEM_EFEITO
                  else modelo.SEM_LANCAMENTO)
        b.con.execute("UPDATE fato SET lancamento_id='', estado=? WHERE id=?",
                      (estado, r["id"]))


# ── Divergências e panorama ───────────────────────────────────────────────
def divergencias(b: Base, competencia: str) -> list:
    """Onde o Fiscal e o Contábil discordam. Só o que EXIGE olho humano —
    fato sem lançamento não entra aqui: isso é cobertura, e tem contador
    próprio."""
    comp = modelo.competencia(competencia)
    fora = []
    for f in listar(b, competencia=comp):
        lc = lancamentos.obter(b, f["lancamento_id"]) if f["lancamento_id"] else None
        if lc and lc["status"] == modelo.CANCELADO:
            lc = None
        if f["tipo"] == modelo.SEM_EFEITO and lc:
            fora.append({"tipo": "DOCUMENTO_SEM_EFEITO_COM_LANCAMENTO",
                         "id_documento": f["id_documento"],
                         "lancamento": lc["numero"],
                         "detalhe": "o documento está %s no Fiscal e o "
                                    "lançamento nº %s continua no razão"
                                    % (f["situacao"], lc["numero"])})
            continue
        if lc and f["valor"] is not None and lc["total"] != abs(f["valor"]):
            fora.append({"tipo": "VALOR_DIVERGENTE",
                         "id_documento": f["id_documento"],
                         "lancamento": lc["numero"],
                         "detalhe": "documento R$ %s × lançamento R$ %s"
                                    % (modelo.reais(abs(f["valor"])),
                                       modelo.reais(lc["total"]))})
        if lc and lc["competencia"] != f["competencia"]:
            fora.append({"tipo": "COMPETENCIA_DIVERGENTE",
                         "id_documento": f["id_documento"],
                         "lancamento": lc["numero"],
                         "detalhe": "documento na competência %s e lançamento "
                                    "em %s" % (f["competencia"], lc["competencia"])})
    # Lançamento de origem FISCAL sem fato: nasceu de um documento que o
    # Contábil não conhece mais (ou foi criado à mão com essa origem).
    ligados = {r["lancamento_id"] for r in b.con.execute(
        "SELECT lancamento_id FROM fato WHERE lancamento_id<>''")}
    for lc in lancamentos.listar(b, competencia=comp, origem=modelo.FISCAL):
        if lc["id"] not in ligados and lc["status"] != modelo.CANCELADO:
            fora.append({"tipo": "LANCAMENTO_FISCAL_SEM_FATO",
                         "id_documento": "", "lancamento": lc["numero"],
                         "detalhe": "lançamento nº %s tem origem FISCAL e não "
                                    "aponta para documento nenhum" % lc["numero"]})
    return fora


def panorama(b: Base, competencia: str) -> dict:
    """O quadro da competência: documentos, fatos, sugestões, cobertura."""
    comp = modelo.competencia(competencia)
    fatos = listar(b, competencia=comp)
    por_tipo, por_estado, por_especie = {}, {}, {}
    valor_fiscal = valor_lancado = 0
    exigem = lancados = 0
    pendentes = confirmados = 0
    for f in fatos:
        por_tipo[f["tipo"]] = por_tipo.get(f["tipo"], 0) + 1
        por_estado[f["estado"]] = por_estado.get(f["estado"], 0) + 1
        por_especie[f["especie"]] = por_especie.get(f["especie"], 0) + 1
        if f["tipo"] != modelo.SEM_EFEITO:
            valor_fiscal += abs(f["valor"] or 0)
        lc = lancamentos.obter(b, f["lancamento_id"]) if f["lancamento_id"] else None
        if lc and lc["status"] == modelo.CANCELADO:
            lc = None
        if f["estado"] not in (modelo.SEM_EFEITO, modelo.IGNORADO):
            exigem += 1
            if lc:
                lancados += 1
        if lc:
            valor_lancado += lc["total"]
            if lc["status"] == modelo.PENDENTE:
                pendentes += 1
            else:
                confirmados += 1
    div = divergencias(b, comp)
    return {
        "competencia": comp,
        "estado": competencias.registro(b, comp),
        "documentos": len(fatos),
        "por_especie": por_especie, "por_tipo": por_tipo, "por_estado": por_estado,
        "fatos": len(fatos),
        "exigem_lancamento": exigem,
        "com_lancamento": lancados,
        "sem_lancamento": exigem - lancados,
        "sugeridos_pendentes": pendentes,
        "confirmados": confirmados,
        "valor_fiscal": valor_fiscal,
        "valor_contabilizado": valor_lancado,
        "diferenca_valor": valor_fiscal - valor_lancado,
        "divergencias": div,
        "cobertura": (round(100.0 * lancados / exigem, 1) if exigem else None),
        "ultima_sincronizacao": max([f["sincronizado_utc"] for f in fatos] or [""]),
    }


def rastreio(b: Base, fato_id: str) -> dict:
    """Nota → operação → fato → lançamento, com o caminho até o XML."""
    f = obter(b, fato_id)
    if not f:
        raise modelo.Invalido("fato não encontrado")
    lc = lancamentos.obter(b, f["lancamento_id"]) if f["lancamento_id"] else None
    return {
        "documento": {k: f[k] for k in (
            "id_documento", "especie", "numero", "serie", "chave",
            "data_emissao", "situacao", "contraparte_doc", "contraparte_nome",
            "arquivo", "caminho", "hash_conteudo", "fonte_leitor")},
        "operacao": {"sentido": f["sentido"], "natureza_fiscal": f["natureza_fiscal"],
                     "cfops": f["cfops"], "entra_na_receita": bool(f["entra_na_receita"]),
                     "valor": f["valor"], "retencoes": f["retencoes"],
                     "retencoes_detalhe": json.loads(f["retencoes_detalhe"] or "{}")},
        "fato": {"id": f["id"], "competencia": f["competencia"], "tipo": f["tipo"],
                 "rotulo": modelo.ROTULO_TIPO_FATO.get(f["tipo"], f["tipo"]),
                 "origem": f["origem"], "estado": f["estado"],
                 "motivo": f["motivo"]},
        "sugestao": sugestao(b, f),
        "lancamento": ({k: lc[k] for k in ("id", "numero", "data", "competencia",
                                           "historico", "status", "origem",
                                           "documento", "lote", "total")}
                       | {"partidas": lc["partidas"]}) if lc else None,
        "eventos": b.eventos("FATO", f["id"]),
    }
