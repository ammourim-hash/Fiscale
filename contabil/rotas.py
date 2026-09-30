# -*- coding: utf-8 -*-
"""rotas.py — as rotas /api/contabil/*, fora do `fiscale_server.py`.

O servidor faz o que só ele pode fazer — sessão, papel, CSRF, ler o corpo —
e entrega aqui um pedido já autenticado. Isto devolve `(status, objeto)`, ou
`Arquivo` quando a resposta é o extrato original.

A EMPRESA É CONFERIDA CONTRA O CADASTRO, SEMPRE
    Toda rota recebe `empresa`, e ela precisa estar no cadastro de Clientes
    (o universo do FISCALE vem do cadastro, não das pastas). Depois disso, o
    banco aberto é o DAQUELA empresa: um id de movimento da empresa B pedido
    com `empresa=A` simplesmente não existe no arquivo aberto, e a resposta é
    404 — não há `WHERE` para esquecer.

LEITURA NÃO CRIA NADA
    GET de empresa que nunca usou o Contábil devolve vazio sem criar pasta nem
    banco. Só a primeira ESCRITA cria `<empresa>/contabil/`.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import (armazenamento, bancos, base, competencias, conciliacao,
               extratos, fatos, fiscal, lancamentos, modelo, plano, visao)

PREFIXO = "/api/contabil/"

# Os módulos da área. `pronto=False` aparece na tela como "Em desenvolvimento"
# — sem tela falsa por trás.
MODULOS = [
    {"id": "visao", "nome": "Visão Geral", "pronto": True},
    {"id": "plano", "nome": "Plano de Contas", "pronto": True},
    {"id": "lancamentos", "nome": "Lançamentos", "pronto": True},
    {"id": "bancos", "nome": "Bancos", "pronto": True},
    {"id": "clientes", "nome": "Clientes", "pronto": False},
    {"id": "fornecedores", "nome": "Fornecedores", "pronto": False},
    {"id": "estoque", "nome": "Estoque", "pronto": False},
    {"id": "imobilizado", "nome": "Imobilizado", "pronto": False},
    {"id": "emprestimos", "nome": "Empréstimos", "pronto": False},
    {"id": "socios", "nome": "Sócios", "pronto": False},
    {"id": "fechamento", "nome": "Fechamento", "pronto": False},
    {"id": "demonstracoes", "nome": "Demonstrações", "pronto": False},
    {"id": "sped", "nome": "SPED", "pronto": False},
]


@dataclass
class Arquivo:
    bruto: bytes
    nome: str
    tipo: str


class NaoEncontrado(Exception):
    pass


def _um(q: dict, nome: str, padrao="") -> str:
    v = q.get(nome, padrao)
    if isinstance(v, list):
        v = v[0] if v else padrao
    return str(v if v is not None else padrao).strip()


def _empresa(q: dict, empresas: list) -> dict:
    ident = modelo.so_digitos(_um(q, "empresa"))
    if not ident:
        raise modelo.Invalido("informe a empresa")
    for e in empresas:
        if e["identidade"] == ident:
            return e
    raise NaoEncontrado("empresa não está no cadastro")


def _vazio(rota: str):
    """Resposta de leitura para empresa que ainda não tem banco contábil."""
    return {
        "visao": {"visao": {"situacao": visao.SEM_MOVIMENTO, "competencia": "",
                  "competencias": [], "lancamentos_pendentes": 0,
                  "movimentos_importados": 0, "nao_identificados": 0,
                  "divergencias": 0, "total_conciliado": 0,
                  "total_pendente": 0, "percentual_conciliado": None,
                  "percentual_conciliado_valor": None,
                  "base_do_percentual": "movimentos efetivamente importados",
                  "por_estado": {e: 0 for e in modelo.ESTADOS_CONCILIACAO},
                  "lancamentos": {s: 0 for s in modelo.STATUS},
                  "movimentos_com_problema": 0, "entradas": 0, "saidas": 0,
                  "importacoes": 0, "contas_bancarias": 0,
                  "contas_no_plano": 0, "total_divergente": 0}},
        "plano": {"contas": []},
        "lancamentos": {"lancamentos": []},
        "bancos": {"contas": [], "importacoes": []},
        "movimentos": {"movimentos": []},
    }.get(rota)


def vocabulario() -> dict:
    return {
        "modulos": MODULOS,
        "status": list(modelo.STATUS),
        "origens": list(modelo.ORIGENS),
        "tipos_conta": list(modelo.TIPOS_CONTA),
        "naturezas": list(modelo.NATUREZAS),
        "grupos": list(modelo.GRUPOS),
        "categorias": [{"id": c, "nome": modelo.ROTULO_CATEGORIA[c]}
                       for c in modelo.CATEGORIAS],
        "estados": list(modelo.ESTADOS_CONCILIACAO),
        "formatos_extrato": ["OFX", "CSV"],
        "formatos_em_preparo": ["PDF"],
        # Contábil 2
        "tipos_fato": [{"id": t, "nome": modelo.ROTULO_TIPO_FATO[t]}
                       for t in modelo.TIPOS_FATO],
        "estados_fato": list(modelo.ESTADOS_FATO),
        "estados_competencia": list(modelo.ESTADOS_COMPETENCIA),
        "papeis": [{"papel": p, "rotulo": r, "grupos": list(g)}
                   for p, (g, r) in modelo.PAPEIS.items()],
    }


def atender(metodo: str, rota: str, q: dict, corpo: dict, arquivo, usuario: str,
            dados, empresas: list):
    try:
        return _atender(metodo, rota, q or {}, corpo or {}, arquivo, usuario,
                        dados, empresas)
    except NaoEncontrado as e:
        return 404, {"erro": str(e)}
    except (modelo.Invalido, extratos.Recusado) as e:
        return 400, {"erro": str(e)}
    except armazenamento.ErroArmazenamento as e:
        return 409, {"erro": str(e)}


def _atender(metodo, rota, q, corpo, arquivo, usuario, dados, empresas):
    sub = rota[len(PREFIXO):]

    if metodo == "GET" and sub == "vocabulario":
        return 200, vocabulario()
    if metodo == "GET" and sub == "empresas":
        return 200, {"empresas": [{"identidade": e["identidade"],
                                   "nome": e["nome"]} for e in empresas]}

    fonte = q if metodo == "GET" else {**q, **corpo}
    emp = _empresa(fonte, empresas)
    ident = emp["identidade"]

    if metodo == "GET":
        if not base.existe(dados, ident):
            vazio = _vazio(sub)
            if vazio is not None:
                return 200, {**vazio, "empresa": emp}
            raise NaoEncontrado("não encontrado")
        with base.abrir(dados, ident) as b:
            return _get(b, sub, q, emp)

    if metodo == "POST":
        if not (usuario or "").strip():
            return 401, {"erro": "não autenticado"}
        with base.abrir(dados, ident) as b:
            return _post(b, sub, corpo, arquivo, usuario, emp)
    return 405, {"erro": "método não aceito"}


def _get(b, sub, q, emp):
    if sub == "visao":
        return 200, {"empresa": emp,
                     "visao": visao.resumo(b, _um(q, "competencia"))}
    if sub == "plano":
        return 200, {"empresa": emp, "contas": plano.listar(
            b, incluir_inativas=_um(q, "inativas", "1") != "0")}
    if sub == "lancamentos":
        return 200, {"empresa": emp, "lancamentos": lancamentos.listar(
            b, competencia=_um(q, "competencia"), status=_um(q, "status"),
            origem=_um(q, "origem"), texto=_um(q, "texto"),
            conta_id=_um(q, "conta"))}
    if sub == "lancamento":
        lc = lancamentos.obter(b, _um(q, "id"))
        if not lc:
            raise NaoEncontrado("lançamento não encontrado")
        lc["eventos"] = b.eventos("LANCAMENTO", lc["id"])
        lc["movimentos"] = [dict(r) for r in b.con.execute(
            "SELECT m.id, m.data, m.descricao, m.valor, c.estado FROM "
            "conciliacao c JOIN movimento m ON m.id=c.movimento_id WHERE "
            "c.alvo_tipo='LANCAMENTO' AND c.alvo_id=? AND c.desfeita_utc=''",
            (lc["id"],))]
        return 200, {"empresa": emp, "lancamento": lc}
    if sub == "bancos":
        return 200, {"empresa": emp, "contas": bancos.listar_contas(b),
                     "importacoes": extratos.listar_importacoes(b)}
    if sub == "movimentos":
        return 200, {"empresa": emp, "movimentos": bancos.buscar(
            b, conta_bancaria_id=_um(q, "conta"), de=_um(q, "de"),
            ate=_um(q, "ate"), valor=_um(q, "valor"), texto=_um(q, "texto"),
            documento=_um(q, "documento"), contraparte_doc=_um(q, "doc"),
            contraparte=_um(q, "contraparte"),
            conta_contabil_id=_um(q, "conta_contabil"),
            estado=_um(q, "estado"), categoria=_um(q, "categoria"),
            importacao_id=_um(q, "importacao"))}
    if sub == "movimento":
        m = bancos.obter_movimento(b, _um(q, "id"))
        if not m:
            raise NaoEncontrado("movimento não encontrado")
        return 200, {"empresa": emp, "movimento": m,
                     "candidatos": conciliacao.candidatos(b, m["id"]),
                     "ligacoes": conciliacao.ligacoes(b, m["id"]),
                     "rastreio": conciliacao.rastreio(b, m["id"]),
                     "eventos": b.eventos("MOVIMENTO", m["id"])}
    if sub == "importacao/ignoradas":
        return 200, {"empresa": emp, "linhas": extratos.linhas_ignoradas(
            b, _um(q, "id"))}
    if sub == "documentos":
        # Busca no acervo fiscal desta empresa, para conciliar à mão.
        docs = fiscal.documentos(b.dados, b.empresa)
        valor = _um(q, "valor")
        texto = modelo.normalizar_texto(_um(q, "texto"))
        doc = modelo.so_digitos(_um(q, "doc"))
        saida = []
        for d in docs:
            if valor and abs(modelo.centavos(valor)) not in [abs(v) for v in d["valores"]]:
                continue
            if doc and doc != d["contraparte_doc"]:
                continue
            if texto and texto not in modelo.normalizar_texto(
                    "%s %s" % (d["contraparte_nome"], d["numero"])):
                continue
            saida.append(d)
        saida.sort(key=lambda d: d["data"], reverse=True)
        return 200, {"empresa": emp, "documentos": saida[:200],
                     "total": len(saida)}
    # ── Contábil 2: Fiscal → Contábil ────────────────────────────────────
    if sub == "fiscal":
        comp = _um(q, "competencia")
        if not comp:
            raise modelo.Invalido("informe a competência")
        p = fatos.panorama(b, comp)
        lista = fatos.listar(b, competencia=comp, tipo=_um(q, "tipo"),
                             estado=_um(q, "estado"))
        for f in lista:
            f["rotulo_tipo"] = modelo.ROTULO_TIPO_FATO.get(f["tipo"], f["tipo"])
            f["sugestao"] = fatos.sugestao(b, f)
            lc = (lancamentos.obter(b, f["lancamento_id"])
                  if f["lancamento_id"] else None)
            f["lancamento"] = ({"id": lc["id"], "numero": lc["numero"],
                                "status": lc["status"], "total": lc["total"]}
                               if lc else None)
        return 200, {"empresa": emp, "panorama": p, "fatos": lista}
    if sub == "fiscal/fato":
        return 200, {"empresa": emp, "rastreio": fatos.rastreio(b, _um(q, "id"))}
    if sub == "impacto":
        # "Fiscal → documento → Ver impacto contábil": a pergunta na outra
        # direção, pelo `id_documento` que o módulo fiscal já conhece.
        id_doc = _um(q, "id_documento")
        f = fatos.obter(b, id_doc) if id_doc else None
        if not f:
            return 200, {"empresa": emp, "id_documento": id_doc, "fato": None,
                         "mensagem": "este documento ainda não foi trazido "
                                     "para o Contábil (sincronize a "
                                     "competência dele)"}
        return 200, {"empresa": emp, "id_documento": id_doc,
                     "rastreio": fatos.rastreio(b, f["id"])}
    if sub == "competencias":
        return 200, {"empresa": emp, "competencias": competencias.listar(b),
                     "competencia": competencias.registro(
                         b, _um(q, "competencia")) if _um(q, "competencia") else None}
    if sub == "mapa":
        return 200, {"empresa": emp, "mapa": plano.mapa(b)}
    if sub == "extrato/original":
        bruto, nome, fmt = extratos.original(b, _um(q, "importacao"))
        tipo = {"OFX": "application/x-ofx", "CSV": "text/csv",
                "PDF": "application/pdf"}.get(fmt, "application/octet-stream")
        return 200, Arquivo(bruto, nome, tipo)
    raise NaoEncontrado("rota do Contábil não existe")


def _post(b, sub, c, arquivo, usuario, emp):
    if sub == "plano/conta":
        conta = plano.criar(b, c.get("codigo"), c.get("descricao"), c.get("tipo"),
                            c.get("natureza"), c.get("grupo") or None,
                            c.get("ref_ecd", ""), c.get("ref_ecf", ""), usuario)
        return 200, {"ok": True, "conta": conta}
    if sub == "plano/alterar":
        campos = {k: c[k] for k in ("descricao", "natureza", "tipo", "ativa",
                                    "ref_ecd", "ref_ecf") if k in c}
        return 200, {"ok": True, "conta": plano.alterar(
            b, str(c.get("id") or ""), usuario, **campos)}
    if sub == "plano/importar":
        texto = str(c.get("texto") or "")
        if len(texto) > 2_000_000:
            raise modelo.Invalido("planilha grande demais")
        r = plano.importar_csv(b, texto, usuario)
        return (200 if r["ok"] else 400), r
    if sub == "lancamentos":
        lc = lancamentos.criar(
            b, c.get("data"), c.get("competencia"), c.get("historico"),
            c.get("partidas"), c.get("origem") or modelo.MANUAL,
            documento=c.get("documento", ""), lote=c.get("lote", ""),
            usuario=usuario)
        return 200, {"ok": True, "lancamento": lc}
    if sub == "lancamento/confirmar":
        return 200, {"ok": True, "lancamento": lancamentos.confirmar(
            b, str(c.get("id") or ""), usuario)}
    if sub == "lancamento/cancelar":
        return 200, {"ok": True, "lancamento": lancamentos.cancelar(
            b, str(c.get("id") or ""), usuario, str(c.get("motivo") or ""))}
    if sub == "lancamento/ajustar":
        lc = lancamentos.criar(
            b, c.get("data"), c.get("competencia"), c.get("historico"),
            c.get("partidas"), modelo.AJUSTE, documento=c.get("documento", ""),
            lote=c.get("lote", ""), usuario=usuario,
            ajusta_id=str(c.get("id") or ""))
        return 200, {"ok": True, "lancamento": lc}
    if sub == "bancos/conta":
        if c.get("id"):
            return 200, {"ok": True, "conta": bancos.alterar_conta(
                b, str(c["id"]), c.get("descricao"),
                c.get("conta_contabil_id"))}
        return 200, {"ok": True, "conta": bancos.criar_conta(
            b, c.get("banco"), c.get("agencia"), c.get("numero"),
            c.get("descricao", ""), c.get("conta_contabil_id", ""))}
    if sub in ("bancos/importar", "bancos/previa"):
        if not arquivo:
            raise modelo.Invalido("nenhum arquivo veio no envio")
        nome, bruto, _extras = arquivo
        if sub == "bancos/previa":
            lei = extratos.ler(bruto, nome)
            return 200, {"ok": True, "formato": lei.formato,
                         "linhas_total": lei.linhas_total,
                         "movimentos": len(lei.movimentos),
                         "ignoradas": len(lei.ignoradas),
                         "banco": lei.banco, "agencia": lei.agencia,
                         "conta": lei.conta}
        r = extratos.importar(b, bruto, nome, usuario,
                              str(c.get("conta_bancaria_id") or ""))
        return 200, {"ok": True, **r}
    if sub == "movimento/categoria":
        return 200, {"ok": True, "movimento": bancos.confirmar_categoria(
            b, str(c.get("id") or ""), str(c.get("categoria") or ""), usuario)}
    if sub == "movimento/lancar":
        lc = bancos.gerar_lancamento(b, str(c.get("id") or ""),
                                     str(c.get("conta_contrapartida_id") or ""),
                                     str(c.get("historico") or ""), usuario)
        return 200, {"ok": True, "lancamento": lc}
    if sub == "movimento/procurar":
        mid = str(c.get("id") or "")
        if not bancos.obter_movimento(b, mid):
            raise NaoEncontrado("movimento não encontrado")
        return 200, {"ok": True, "candidatos": conciliacao.procurar(b, mid)}
    # ── Contábil 2 ───────────────────────────────────────────────────────
    if sub == "fiscal/sincronizar":
        r = fatos.sincronizar(b, str(c.get("competencia") or ""), usuario=usuario)
        return 200, {"ok": True, **r, "panorama": fatos.panorama(b, r["competencia"])}
    if sub == "fiscal/lancar":
        if c.get("id"):
            lc = fatos.gerar_lancamento(b, str(c["id"]), usuario)
            return 200, {"ok": True, "lancamento": lc}
        r = fatos.gerar_lancamentos_da_competencia(
            b, str(c.get("competencia") or ""), usuario)
        return 200, {"ok": True, **r}
    if sub == "fiscal/ignorar":
        return 200, {"ok": True, "fato": fatos.ignorar(
            b, str(c.get("id") or ""), usuario, str(c.get("motivo") or ""))}
    if sub == "fiscal/reconsiderar":
        return 200, {"ok": True, "fato": fatos.reconsiderar(
            b, str(c.get("id") or ""), usuario)}
    if sub == "competencia/estado":
        return 200, {"ok": True, "competencia": competencias.definir(
            b, str(c.get("competencia") or ""), str(c.get("estado") or ""),
            usuario, str(c.get("motivo") or ""))}
    if sub == "mapa":
        return 200, {"ok": True, "mapeado": plano.mapear(
            b, str(c.get("papel") or ""), str(c.get("conta_id") or ""), usuario),
            "mapa": plano.mapa(b)}
    if sub == "conciliar":
        return 200, {"ok": True, "conciliacao": conciliacao.conciliar(
            b, str(c.get("movimento_id") or ""), str(c.get("alvo_tipo") or ""),
            str(c.get("alvo_id") or ""), usuario, str(c.get("motivo") or ""))}
    if sub == "conciliacao/desfazer":
        return 200, {"ok": True, "conciliacao": conciliacao.desfazer(
            b, str(c.get("id") or ""), usuario, str(c.get("motivo") or ""))}
    raise NaoEncontrado("rota do Contábil não existe")
