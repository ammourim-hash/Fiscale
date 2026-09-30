# -*- coding: utf-8 -*-
"""extratos.py — importar um extrato: do arquivo a cada movimento, rastreável.

O CAMINHO, NA ORDEM, E POR QUE NESSA ORDEM
    1. hash dos bytes. Já importado? Devolve a importação que existe e não
       toca em nada (DUPLICATA). Reimportar o mesmo arquivo cem vezes dá o
       mesmo banco que importar uma.
    2. leitura, e a conferência de completude. Se o leitor não prestou contas
       de TODAS as linhas, nada é gravado — nem o original.
    3. a conta bancária: a do OFX (lida do arquivo) ou a escolhida (CSV).
       OFX de uma conta importado "para" outra é recusado.
    4. o original guardado byte a byte, e RELIDO do disco.
    5. numa transação só: a importação, cada movimento, a origem de cada
       linha, as linhas ignoradas com o motivo.
    6. depois da gravação: classificação (sugestão), pareamento de
       transferências e busca de correspondências. Nenhum desses passos cria
       lançamento.

IDEMPOTÊNCIA ENTRE ARQUIVOS DIFERENTES
    Extrato de 1 a 31 e extrato de 15 a 15 repetem a segunda quinzena. O
    movimento é identificado pela sua impressão — conta, data, valor,
    descrição e documento — mais a POSIÇÃO entre os iguais do mesmo arquivo
    (duas tarifas idênticas no mesmo dia são dois movimentos, e continuam
    sendo dois no arquivo seguinte). O FITID do OFX é guardado, mas não é a
    chave: há banco que muda o FITID a cada exportação, e aí a chave
    duplicaria o extrato inteiro.
"""
from __future__ import annotations

import hashlib

from . import armazenamento, bancos, classificacao, conciliacao, fiscal, modelo
from .base import Base, agora, novo_id
from .leitores import Leitura, SemLeitor, conferir_completude, formato_de
from .leitores import ler as ler_arquivo

DUPLICATA = "DUPLICATA"
IMPORTADO = "IMPORTADO"

_EXT = {"OFX": "ofx", "CSV": "csv", "PDF": "pdf"}


class Recusado(ValueError):
    pass


def impressao(conta_id: str, m) -> str:
    return "|".join((conta_id, m.data or "", str(m.valor),
                     modelo.normalizar_texto(m.descricao),
                     (m.documento or "").strip()))


def identificadores(conta_id: str, movimentos) -> list:
    vistos: dict = {}
    saida = []
    for m in movimentos:
        imp = impressao(conta_id, m)
        vistos[imp] = vistos.get(imp, 0) + 1
        saida.append(hashlib.sha256(("%s#%d" % (imp, vistos[imp])).encode(
            "utf-8")).hexdigest()[:32])
    return saida


def ler(bruto: bytes, nome: str = "") -> Leitura:
    """Lê e confere. Não grava nada. É também a prévia da tela."""
    try:
        lei = ler_arquivo(bruto, nome)
        conferir_completude(lei)
    except SemLeitor as e:
        raise Recusado(str(e))
    return lei


def importar(b: Base, bruto: bytes, nome_arquivo: str = "", usuario: str = "",
             conta_bancaria_id: str = "", documentos_fiscais=None) -> dict:
    if not bruto:
        raise Recusado("arquivo vazio")
    sha = hashlib.sha256(bruto).hexdigest()
    ja = b.con.execute("SELECT * FROM importacao WHERE sha256=?", (sha,)).fetchone()
    if ja:
        return {"desfecho": DUPLICATA, "importacao": dict(ja),
                "mensagem": "este arquivo já foi importado em %s; nada mudou"
                            % ja["quando_utc"][:10]}

    lei = ler(bruto, nome_arquivo)

    # A conta bancária.
    if lei.formato == "OFX" and lei.conta:
        cb = bancos.achar_conta(b, lei.banco, lei.agencia, lei.conta)
        if conta_bancaria_id and cb and cb["id"] != conta_bancaria_id:
            raise Recusado("o OFX é da conta %s/%s/%s, e não da conta escolhida"
                           % (lei.banco, lei.agencia, lei.conta))
        if conta_bancaria_id and not cb:
            escolhida = bancos.obter_conta(b, conta_bancaria_id)
            if not escolhida:
                raise Recusado("conta bancária não encontrada")
            if escolhida["numero"] and bancos.norm_numero(escolhida["numero"]) \
                    != bancos.norm_numero(lei.conta):
                raise Recusado(
                    "o OFX é da conta %s, e a conta escolhida é %s"
                    % (lei.conta, escolhida["numero"]))
            cb = escolhida
        if not cb:
            cb = bancos.criar_conta(b, lei.banco, lei.agencia, lei.conta,
                                    "criada pelo OFX %s" % (nome_arquivo or "")[:60])
    else:
        if not conta_bancaria_id:
            raise Recusado("escolha a conta bancária deste extrato: o %s não "
                           "diz de qual conta ele é" % lei.formato)
        cb = bancos.obter_conta(b, conta_bancaria_id)
        if not cb:
            raise Recusado("conta bancária não encontrada")

    guardado = armazenamento.guardar(b.dados, b.empresa, bruto,
                                     _EXT.get(lei.formato, "txt"),
                                     nome_arquivo, usuario)

    imp_id = novo_id()
    ids = identificadores(cb["id"], lei.movimentos)
    datas = sorted(m.data for m in lei.movimentos if m.data)
    novos, existentes = [], 0
    b.con.execute(
        "INSERT INTO importacao (id, conta_bancaria_id, sha256, arquivo_id, "
        "nome_arquivo, formato, bytes, quando_utc, usuario, linhas_total, "
        "movimentos_lidos, linhas_ignoradas, periodo_ini, periodo_fim, "
        "saldo_final, saldo_final_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (imp_id, cb["id"], sha, guardado["arquivo_id"],
         (nome_arquivo or "")[:200], lei.formato, len(bruto), agora(),
         usuario or "", lei.linhas_total, len(lei.movimentos),
         len(lei.ignoradas), datas[0] if datas else "",
         datas[-1] if datas else "", lei.saldo_final, lei.saldo_final_data))
    for m, ident in zip(lei.movimentos, ids):
        r = b.con.execute("SELECT id FROM movimento WHERE conta_bancaria_id=? "
                          "AND identificador=?", (cb["id"], ident)).fetchone()
        if r:
            mov_id = r["id"]
            existentes += 1
        else:
            mov_id = novo_id()
            sentido = "" if m.valor is None else (
                modelo.ENTRADA if m.valor > 0 else modelo.SAIDA if m.valor < 0
                else "")
            b.con.execute(
                "INSERT INTO movimento (id, conta_bancaria_id, identificador, "
                "data, data_lancamento, descricao, documento, valor, sentido, "
                "saldo, tipo_operacao, id_transacao, banco, agencia, conta, "
                "importacao_id, linha, pagina, problema) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mov_id, cb["id"], ident, m.data, m.data_lancamento,
                 m.descricao, m.documento, m.valor, sentido, m.saldo,
                 m.tipo_operacao, m.id_transacao, cb["banco"], cb["agencia"],
                 cb["numero"], imp_id, m.linha, m.pagina, m.problema))
            novos.append(mov_id)
        b.con.execute(
            "INSERT INTO movimento_origem (movimento_id, importacao_id, ordem, "
            "linha, pagina) VALUES (?,?,?,?,?)",
            (mov_id, imp_id, m.ordem, m.linha, m.pagina))
    b.con.executemany(
        "INSERT INTO linha_ignorada (importacao_id, linha, conteudo, motivo) "
        "VALUES (?,?,?,?)",
        [(imp_id, ln, (txt or "")[:500], motivo) for ln, txt, motivo in lei.ignoradas])
    b.con.execute("UPDATE importacao SET movimentos_novos=?, "
                  "movimentos_existentes=? WHERE id=?",
                  (len(novos), existentes, imp_id))
    b.evento("IMPORTACAO", imp_id, "IMPORTADA", usuario,
             "%s · %d movimentos (%d novos)" % (lei.formato,
                                                len(lei.movimentos), len(novos)))

    # Sugestões. Nada daqui cria lançamento.
    docs = documentos_fiscais
    if docs is None:
        docs = fiscal.documentos(b.dados, b.empresa)
    ctx = fiscal.contexto_classificacao(docs, b.empresa)
    for mov_id in novos:
        reclassificar(b, mov_id, ctx)
    bancos.parear_transferencias(b)
    for mov_id in novos:
        conciliacao.procurar(b, mov_id, docs)

    imp = dict(b.con.execute("SELECT * FROM importacao WHERE id=?",
                             (imp_id,)).fetchone())
    return {"desfecho": IMPORTADO, "importacao": imp,
            "conta_bancaria": cb, "avisos": lei.avisos,
            "mensagem": "%d movimentos lidos: %d novos, %d já existiam; "
                        "%d linhas não eram movimento"
                        % (len(lei.movimentos), len(novos), existentes,
                           len(lei.ignoradas))}


def reclassificar(b: Base, mov_id: str, contexto: dict) -> dict:
    """Refaz a SUGESTÃO. Não toca em `categoria_confirmada`."""
    m = b.con.execute("SELECT * FROM movimento WHERE id=?", (mov_id,)).fetchone()
    if not m:
        raise modelo.Invalido("movimento não encontrado")
    s = classificacao.classificar(m["descricao"], m["valor"],
                                  m["tipo_operacao"], contexto)
    b.con.execute(
        "UPDATE movimento SET categoria=?, sugestao=?, confianca=?, regra=?, "
        "contraparte_doc=?, contraparte_nome=? WHERE id=?",
        (s["categoria"], s["sugestao"], s["confianca"], s["regra"],
         s["contraparte_doc"], s["contraparte_nome"], mov_id))
    return s


def listar_importacoes(b: Base) -> list:
    return [dict(r) for r in b.con.execute(
        "SELECT i.*, c.banco, c.agencia, c.numero, c.descricao AS conta_desc "
        "FROM importacao i LEFT JOIN conta_bancaria c ON c.id=i.conta_bancaria_id"
        " ORDER BY i.quando_utc DESC")]


def linhas_ignoradas(b: Base, importacao_id: str) -> list:
    return [dict(r) for r in b.con.execute(
        "SELECT linha, conteudo, motivo FROM linha_ignorada WHERE "
        "importacao_id=? ORDER BY linha", (importacao_id,))]


def original(b: Base, importacao_id: str) -> tuple:
    """`(bytes, nome, formato)` do arquivo original, conferido pelo hash."""
    r = b.con.execute("SELECT * FROM importacao WHERE id=?",
                      (importacao_id,)).fetchone()
    if not r:
        raise modelo.Invalido("importação não encontrada")
    if not armazenamento.conferir_integridade(b.dados, b.empresa,
                                              r["arquivo_id"], r["sha256"]):
        raise armazenamento.ErroArmazenamento(
            "o arquivo no disco não confere com o hash registrado")
    return (armazenamento.ler(b.dados, b.empresa, r["arquivo_id"]),
            r["nome_arquivo"] or ("extrato." + _EXT.get(r["formato"], "txt")),
            r["formato"])


__all__ = ["importar", "ler", "Recusado", "DUPLICATA", "IMPORTADO",
           "formato_de"]
