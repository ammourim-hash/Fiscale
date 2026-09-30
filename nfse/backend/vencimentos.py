"""
vencimentos.py — O que está prestes a quebrar o acesso do escritório.

Três coisas derrubam a captura sem aviso nenhum, e as três só aparecem quando
já é tarde — no meio de uma apuração:

  1. o certificado A1 vence (validade dentro do próprio .pfx);
  2. a procuração vence (data que o usuário informa; não está em lugar nenhum
     do sistema, e é ela que sustenta o acesso às prefeituras);
  3. o arquivo .pfx sai do lugar (vários apontam para a pasta Downloads, que é
     justamente onde se apaga arquivo).

Este módulo só LÊ. Não baixa nada, não altera cadastro, não escreve arquivo.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

try:
    from cryptography.hazmat.primitives.serialization import pkcs12
    TEM_CRYPTO = True
except Exception:                                    # pragma: no cover
    TEM_CRYPTO = False

# Faixas de aviso. 60 dias dá tempo de renovar sem correria; 7 é urgência.
PRAZO_ATENCAO = 60
PRAZO_URGENTE = 30
PRAZO_CRITICO = 7

# Pastas que o usuário (ou o Windows) limpa sem pensar duas vezes. Certificado
# guardado aqui é acidente esperando acontecer.
_PASTAS_FRAGEIS = ("downloads", "temp", "tmp", "área de trabalho", "area de trabalho",
                   "desktop", "lixeira", "recycle")


def _hoje() -> _dt.date:
    return _dt.date.today()


def _dias_ate(data: str | None) -> int | None:
    """Dias de hoje até 'AAAA-MM-DD'. Negativo = já venceu."""
    if not data:
        return None
    try:
        d = _dt.date.fromisoformat(str(data)[:10])
    except ValueError:
        return None
    return (d - _hoje()).days


def _nivel(dias: int | None) -> str:
    if dias is None:
        return "sem_data"
    if dias < 0:
        return "vencido"
    if dias <= PRAZO_CRITICO:
        return "critico"
    if dias <= PRAZO_URGENTE:
        return "urgente"
    if dias <= PRAZO_ATENCAO:
        return "atencao"
    return "ok"


def _pasta_fragil(caminho: str) -> bool:
    p = (caminho or "").replace("/", "\\").lower()
    return any(f"\\{f}\\" in p for f in _PASTAS_FRAGEIS)


def validade_do_pfx(caminho: str, senha: str) -> str | None:
    """Data de expiração ('AAAA-MM-DD') lida de dentro do .pfx, ou None."""
    if not TEM_CRYPTO:
        return None
    try:
        dados = Path(caminho).read_bytes()
        _, cert, _ = pkcs12.load_key_and_certificates(dados, (senha or "").encode())
        if cert is None:
            return None
        exp = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
        return exp.strftime("%Y-%m-%d") if exp else None
    except Exception:
        return None


def verificar(registro, abrir_senha, cfgs=None) -> dict:
    """Confere todos os certificados cadastrados.

    `registro`    lista do certificados.json
    `abrir_senha` função(cert) -> senha em texto (o chamador decide como; aqui
                  não se mexe em DPAPI para não duplicar a regra de segurança)
    `cfgs`        configuração por empresa, de onde sai a validade da procuração
    """
    cfgs = cfgs or {}
    itens, resumo = [], {"vencido": 0, "critico": 0, "urgente": 0,
                         "atencao": 0, "sem_arquivo": 0, "fragil": 0, "ok": 0}

    for c in registro or []:
        cid = c.get("id") or ""
        caminho = c.get("caminho") or ""
        existe = bool(caminho) and Path(caminho).exists()

        item = {
            "id": cid,
            "nome": c.get("apelido") or c.get("nome") or cid,
            "documento": re.sub(r"\D", "", c.get("cnpj") or ""),
            "procurador": bool(c.get("procurador")),
            "caminho": caminho,
            "arquivo_existe": existe,
            "pasta_fragil": _pasta_fragil(caminho),
            "validade": None, "dias": None, "nivel": "sem_data",
            "procuracao_validade": None, "procuracao_dias": None,
            "procuracao_nivel": None,
            "problemas": [],
        }

        if not existe:
            # Pior caso: some sem avisar e o erro só aparece na apuração.
            item["nivel"] = "sem_arquivo"
            item["problemas"].append(
                "O arquivo do certificado não está mais neste caminho. "
                "A captura desta empresa vai falhar.")
            resumo["sem_arquivo"] += 1
        else:
            if item["pasta_fragil"]:
                item["problemas"].append(
                    "O certificado está numa pasta que costuma ser limpa "
                    "(Downloads/Temp/Área de Trabalho). Mova para a pasta de "
                    "dados do Fiscale para não perdê-lo.")
                resumo["fragil"] += 1
            try:
                senha = abrir_senha(c)
            except Exception:
                senha = None
            if senha is not None:
                val = validade_do_pfx(caminho, senha)
                item["validade"] = val
                item["dias"] = _dias_ate(val)
                item["nivel"] = _nivel(item["dias"])
                if item["nivel"] == "vencido":
                    item["problemas"].append("Certificado VENCIDO.")
                elif item["nivel"] in ("critico", "urgente", "atencao"):
                    item["problemas"].append(
                        f"Certificado vence em {item['dias']} dia(s).")
            else:
                item["problemas"].append(
                    "Senha do certificado não está salva — não dá para conferir "
                    "a validade. Recadastre marcando 'salvar a senha'.")

        # Procuração: vale por empresa e é informada à mão (não existe no .pfx).
        cfg = cfgs.get(cid) or cfgs.get(item["documento"]) or {}
        pval = cfg.get("procuracaoValidade") or cfg.get("procuracao_validade")
        if pval:
            item["procuracao_validade"] = str(pval)[:10]
            item["procuracao_dias"] = _dias_ate(pval)
            item["procuracao_nivel"] = _nivel(item["procuracao_dias"])
            if item["procuracao_nivel"] == "vencido":
                item["problemas"].append(
                    "PROCURAÇÃO VENCIDA — o acesso à prefeitura para de funcionar.")
            elif item["procuracao_nivel"] in ("critico", "urgente", "atencao"):
                item["problemas"].append(
                    f"Procuração vence em {item['procuracao_dias']} dia(s).")

        # O resumo conta pelo pior dos dois prazos.
        piores = [n for n in (item["nivel"], item["procuracao_nivel"]) if n]
        for n in ("vencido", "critico", "urgente", "atencao"):
            if n in piores:
                resumo[n] += 1
                break
        else:
            if item["nivel"] not in ("sem_arquivo", "sem_data"):
                resumo["ok"] += 1

        itens.append(item)

    # Ordem de leitura: o que dói primeiro aparece primeiro.
    peso = {"vencido": 0, "sem_arquivo": 1, "critico": 2, "urgente": 3,
            "atencao": 4, "sem_data": 5, "ok": 6}
    itens.sort(key=lambda x: (peso.get(x["nivel"], 9),
                              x["dias"] if x["dias"] is not None else 9999))

    return {"itens": itens, "resumo": resumo,
            "alertas": sum(resumo[k] for k in
                           ("vencido", "critico", "urgente", "atencao",
                            "sem_arquivo"))}
