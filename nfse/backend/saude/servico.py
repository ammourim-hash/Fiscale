"""servico.py — orquestra a importação: detectar → parsear → conferir → guardar.

Regra que atravessa o módulo inteiro: **um arquivo ruim não derruba os outros**.
O escritório importa a pasta de faturas do mês de uma vez; se o terceiro PDF
estiver corrompido, os outros nove precisam entrar assim mesmo, e o terceiro
precisa aparecer na tela dizendo o que houve — não sumir.
"""
from __future__ import annotations

from decimal import Decimal

from . import armazenamento as arm
from .deteccao import detectar
from .modelo import Extrato, dec
from .parsers import PARSERS, por_nome
from .texto import texto_de


def _extrato_com_erro(nome, dados, mensagem, pasta_cnpj) -> dict:
    """Mesmo falhando, o arquivo original fica guardado E o extrato entra no
    índice com status ERRO. São as duas condições para reprocessar depois de
    corrigir o parser sem ter de pedir o PDF de novo — e para o arquivo com
    problema aparecer na tela em vez de sumir do lote."""
    h, caminho = arm.guardar_original(pasta_cnpj, nome, dados)
    ex = Extrato(arquivo_nome=nome, arquivo_hash=h, arquivo_caminho=caminho,
                 status="ERRO", erro=mensagem, importado_em=arm.agora())
    d = ex.dict()
    arm.gravar_extrato(pasta_cnpj, d, substituir=True)
    return d


def _casar_cnpj(ex, cnpj_empresa) -> None:
    """O CNPJ da empresa dona da pasta é o contexto da importação.

    Se o relatório não traz CNPJ (é o caso da SulAmérica), completamos com o da
    empresa. Se traz e diverge, NÃO sobrescrevemos: avisamos — divergência aqui
    quase sempre significa que o PDF é de outra empresa e foi importado no lugar
    errado, que é exatamente o erro que estraga uma competência inteira."""
    from .modelo import so_digitos
    alvo = so_digitos(cnpj_empresa)
    if not alvo:
        return
    lido = so_digitos(ex.empresa_cnpj)
    if not lido:
        ex.empresa_cnpj = cnpj_empresa
    elif lido != alvo:
        ex.avisos.append(
            f"O relatório é do CNPJ {ex.empresa_cnpj}, mas está sendo importado "
            f"na empresa {cnpj_empresa}. Confira se escolheu a empresa certa.")
        ex.status = "CONFERIR"


def importar_um(pasta_cnpj, nome: str, dados: bytes,
                operadora: str | None = None, substituir=False,
                cnpj_empresa: str | None = None) -> dict:
    """Importa UM arquivo. Nunca levanta exceção — devolve o resultado, com
    `resultado` em {novo, duplicado, reprocessado, erro, confirmar}."""
    h, caminho = arm.guardar_original(pasta_cnpj, nome, dados)

    try:
        texto = texto_de(dados, nome)
    except Exception as e:
        d = _extrato_com_erro(nome, dados, str(e), pasta_cnpj)
        return {"resultado": "erro", "arquivo": nome, "extrato": d}

    if operadora:
        parser = por_nome(operadora)
        if parser is None:
            return {"resultado": "erro", "arquivo": nome,
                    "extrato": _extrato_com_erro(
                        nome, dados, f"Não tenho parser para '{operadora}'.", pasta_cnpj)}
        confianca, precisa = parser.confianca(texto), False
    else:
        det = detectar(texto)
        if det.precisa_confirmar:
            return {"resultado": "confirmar", "arquivo": nome,
                    "deteccao": det.dict(), "hash": h,
                    "mensagem": det.motivo}
        parser, confianca, precisa = det.parser, det.confianca, False

    ex = Extrato(arquivo_nome=nome, arquivo_hash=h, arquivo_caminho=caminho,
                 operadora=parser.nome, parser=parser.__class__.__name__,
                 parser_versao=parser.versao, deteccao_confianca=confianca,
                 importado_em=arm.agora())
    try:
        parser.ler(texto, ex)
    except Exception as e:
        ex.status = "ERRO"
        ex.erro = f"Não consegui ler o relatório ({e.__class__.__name__}: {e})."
        d = ex.dict()
        arm.gravar_extrato(pasta_cnpj, d, substituir=True)
        return {"resultado": "erro", "arquivo": nome, "extrato": d}

    if not ex.familias:
        ex.status = "ERRO"
        ex.erro = ("Reconheci a operadora, mas não encontrei nenhuma vida no "
                   "relatório. Confira se é o relatório de beneficiários e não "
                   "outro documento da mesma operadora.")
        d = ex.dict()
        arm.gravar_extrato(pasta_cnpj, d, substituir=True)
        return {"resultado": "erro", "arquivo": nome, "extrato": d}

    ex.status = "LIDO"
    ex.conferencia = parser.conferir(ex)
    ex.status = "CONFERIDO" if ex.conferencia.fecha else "CONFERIR"
    _casar_cnpj(ex, cnpj_empresa)      # pode rebaixar para CONFERIR — de propósito

    d = ex.dict()
    resultado = arm.gravar_extrato(pasta_cnpj, d, substituir=substituir)
    return {"resultado": resultado, "arquivo": nome, "extrato": d}


def importar_arquivos(pasta_cnpj, arquivos, operadora=None, substituir=False,
                      cnpj_empresa=None) -> dict:
    """Lote. `arquivos` = [(nome, bytes), ...]."""
    itens = []
    for nome, dados in arquivos:
        try:
            itens.append(importar_um(pasta_cnpj, nome, dados,
                                     operadora=operadora, substituir=substituir,
                                     cnpj_empresa=cnpj_empresa))
        except Exception as e:      # rede de segurança: o lote continua
            itens.append({"resultado": "erro", "arquivo": nome,
                          "extrato": {"arquivo_nome": nome, "status": "ERRO",
                                      "erro": f"Falha inesperada: {e}"}})
    resumo = {"total": len(itens)}
    for r in ("novo", "duplicado", "reprocessado", "erro", "confirmar"):
        resumo[r] = sum(1 for i in itens if i["resultado"] == r)
    return {"resumo": resumo, "itens": itens}


# ── consulta ─────────────────────────────────────────────────────────────
def _chave_comp(c: str):
    """Ordena MM/AAAA cronologicamente (e não alfabeticamente)."""
    try:
        mm, aa = c.split("/")
        return (int(aa), int(mm))
    except Exception:
        return (0, 0)


def listar_competencias(pasta_cnpj) -> list:
    """Uma linha por competência × operadora, com os números da tela."""
    base = arm.carregar(pasta_cnpj)
    agrupado: dict = {}
    for e in base["extratos"]:
        if e.get("status") == "ERRO":
            continue
        k = (e.get("competencia") or "", e.get("operadora") or "")
        g = agrupado.setdefault(k, {
            "competencia": k[0], "operadora": k[1], "empresa": e.get("empresa_nome") or "",
            "titulares": 0, "dependentes": 0, "vidas": 0,
            "individualizado": Decimal("0.00"), "iof": Decimal("0.00"),
            "acertos": Decimal("0.00"), "total": Decimal("0.00"),
            "extratos": [], "status": "CONFERIDO"})
        g["titulares"] += e.get("titulares") or 0
        g["dependentes"] += e.get("dependentes") or 0
        g["vidas"] += e.get("vidas") or 0
        g["individualizado"] += dec(e.get("total_individualizado"))
        g["iof"] += dec(e.get("iof_declarado"))
        g["acertos"] += dec(e.get("acertos_declarados"))
        g["total"] += dec(e.get("total_fatura_declarado"))
        g["extratos"].append(e.get("id"))
        if e.get("status") != "CONFERIDO":
            g["status"] = "CONFERIR"
    saida = []
    for g in agrupado.values():
        for k in ("individualizado", "iof", "acertos", "total"):
            g[k] = str(g[k])
        saida.append(g)
    saida.sort(key=lambda g: _chave_comp(g["competencia"]), reverse=True)
    return saida


def competencia(pasta_cnpj, comp: str, operadora: str | None = None) -> list:
    base = arm.carregar(pasta_cnpj)
    return [e for e in base["extratos"]
            if e.get("competencia") == comp
            and (not operadora or e.get("operadora") == operadora)]


def extratos(pasta_cnpj, incluir_erros=True) -> list:
    base = arm.carregar(pasta_cnpj)
    itens = base["extratos"]
    if not incluir_erros:
        itens = [e for e in itens if e.get("status") != "ERRO"]
    return sorted(itens, key=lambda e: _chave_comp(e.get("competencia") or ""),
                  reverse=True)


def historico_beneficiario(pasta_cnpj, chave: str) -> list:
    """Todas as competências em que esta pessoa aparece, em ordem cronológica.

    É o que permite ver entrada, saída, aumento e troca de plano — e por isso a
    identidade tem de vir de CPF/código, nunca do nome."""
    base = arm.carregar(pasta_cnpj)
    linha = []
    for e in base["extratos"]:
        if e.get("status") == "ERRO":
            continue
        for f in e.get("familias") or []:
            vidas = ([f["titular"]] if f.get("titular") else []) + (f.get("dependentes") or [])
            for b in vidas:
                if b.get("chave") != chave:
                    continue
                linha.append({
                    "competencia": e.get("competencia"),
                    "operadora": e.get("operadora"),
                    "nome": b.get("nome"), "plano": b.get("plano"),
                    "parentesco": b.get("parentesco"),
                    "titular": (f.get("titular") or {}).get("nome"),
                    "valor": (b.get("cobranca") or {}).get("total"),
                    "extrato": e.get("id"),
                })
    linha.sort(key=lambda x: _chave_comp(x["competencia"] or ""))
    return linha


def beneficiarios(pasta_cnpj, comp: str | None = None) -> list:
    """Lista de vidas distintas (pela chave técnica), com a última posição."""
    base = arm.carregar(pasta_cnpj)
    por_chave: dict = {}
    for e in sorted(base["extratos"], key=lambda e: _chave_comp(e.get("competencia") or "")):
        if e.get("status") == "ERRO":
            continue
        if comp and e.get("competencia") != comp:
            continue
        for f in e.get("familias") or []:
            tit = (f.get("titular") or {}).get("nome") or ""
            vidas = ([f["titular"]] if f.get("titular") else []) + (f.get("dependentes") or [])
            for b in vidas:
                por_chave[b.get("chave")] = {
                    "chave": b.get("chave"), "nome": b.get("nome"),
                    "cpf": b.get("cpf"), "cpf_ok": b.get("cpf_ok"),
                    "codigo": b.get("codigo"), "matricula": b.get("matricula"),
                    "plano": b.get("plano"), "parentesco": b.get("parentesco"),
                    "nascimento": b.get("nascimento"),
                    "inicio_vigencia": b.get("inicio_vigencia"),
                    "titular": tit, "operadora": e.get("operadora"),
                    "ultima_competencia": e.get("competencia"),
                    "ultimo_valor": (b.get("cobranca") or {}).get("total"),
                    "avisos": b.get("avisos") or [],
                }
    return sorted(por_chave.values(), key=lambda b: (b["titular"] or "", b["nome"] or ""))


def reprocessar(pasta_cnpj, extrato_id: str) -> dict:
    """Relê o arquivo original com o parser ATUAL, guardando o resultado
    anterior no histórico. É o caminho quando um parser é corrigido."""
    from pathlib import Path
    base = arm.carregar(pasta_cnpj)
    e = arm.achar(base, extrato_id)
    if not e:
        return {"ok": False, "erro": "Extrato não encontrado."}
    caminho = Path(e.get("arquivo_caminho") or "")
    if not caminho.exists():
        return {"ok": False, "erro": f"O arquivo original sumiu: {caminho}"}
    dados = caminho.read_bytes()
    r = importar_um(pasta_cnpj, e.get("arquivo_nome") or caminho.name, dados,
                    operadora=e.get("operadora") or None, substituir=True,
                    cnpj_empresa=e.get("empresa_cnpj"))
    return {"ok": r["resultado"] != "erro", **r}
