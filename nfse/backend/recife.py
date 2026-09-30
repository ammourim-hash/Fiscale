"""
recife.py — Histórico municipal de NFS-e do Recife (padrão ABRASF).

Serve para completar o RBT12: as notas emitidas ANTES da migração para a
NFS-e Nacional não existem na API nacional (ADN) — só no sistema do município.

Web service (confirmado em teste com certificado A1 real):
  endpoint : https://nfse.recife.pe.gov.br/WSNacional/nfse_v01.asmx
  método   : ConsultarNfse (parâmetro `inputXML`)
  SOAPAction: http://nfse.recife.pe.gov.br/ConsultarNfse
  namespace: http://www.abrasf.org.br/ABRASF/arquivos/nfse.xsd   <-- NÃO é o
             "abrasf.org.br/nfse.xsd" padrão; o Recife usa este.
  auth     : certificado ICP-Brasil (mTLS). A consulta NÃO exige assinatura XML.

Decisões apuradas contra um PGDAS-D real (MARIA DAS VITORIAS, 06-11/2025):
  • Só notas VÁLIDAS: descarta as que têm <NfseCancelamento> (e <NfseSubstituicao>).
  • Agrupar por DATA DE EMISSÃO: bate 4/6 meses na casa do centavo. O campo
    <Competencia> do Recife é preenchido de forma irregular (muitas notas com o
    dia 1º do mês seguinte) e bateu 0/6 — por isso não é usado como competência.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

import requests
from requests_pkcs12 import Pkcs12Adapter

URL = "https://nfse.recife.pe.gov.br/WSNacional/nfse_v01.asmx"
NS_ABRASF = "http://www.abrasf.org.br/ABRASF/arquivos/nfse.xsd"
SOAP_ACTION = "http://nfse.recife.pe.gov.br/ConsultarNfse"
COD_MUNICIPIO_RECIFE = "2611606"


def _tag(el) -> str:
    return el.tag.split("}")[-1]


def _txt(el, nome) -> str:
    achado = next((e for e in el.iter() if _tag(e) == nome), None)
    return (achado.text or "").strip() if achado is not None and achado.text else ""


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def item_lc116_abrasf(v: str) -> str:
    """ItemListaServico do ABRASF -> 'II.SS'. Aceita '0902', '902', '9.02'."""
    d = re.sub(r"\D", "", v or "")
    if len(d) >= 3:
        d = d.zfill(4)[:4]
        return f"{int(d[:2])}.{d[2:4]}"
    return ""


def _sessao(pfx_path, senha):
    s = requests.Session()
    s.mount("https://", Pkcs12Adapter(pkcs12_filename=pfx_path, pkcs12_password=senha))
    return s


def consultar(pfx_path, senha, cnpj, data_ini, data_fim, im=None) -> tuple[list[dict], list[str]]:
    """Consulta as NFS-e emitidas no período. Devolve (notas_válidas, mensagens)."""
    prest = f"<Cnpj>{cnpj}</Cnpj>"
    if im:
        prest += f"<InscricaoMunicipal>{im}</InscricaoMunicipal>"
    abrasf = (f'<ConsultarNfseEnvio xmlns="{NS_ABRASF}">'
              f"<Prestador>{prest}</Prestador>"
              f"<PeriodoEmissao><DataInicial>{data_ini}</DataInicial>"
              f"<DataFinal>{data_fim}</DataFinal></PeriodoEmissao>"
              "</ConsultarNfseEnvio>")
    env = ('<?xml version="1.0" encoding="utf-8"?>'
           '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
           '<ConsultarNfseRequest xmlns="http://nfse.recife.pe.gov.br/">'
           f"<inputXML>{html.escape(abrasf)}</inputXML>"
           "</ConsultarNfseRequest></soap:Body></soap:Envelope>")

    sess = _sessao(pfx_path, senha)
    r = sess.post(URL, data=env.encode("utf-8"),
                  headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": SOAP_ACTION},
                  timeout=180)
    r.raise_for_status()
    texto = html.unescape(r.text)

    msgs = [f"[{c}] {m.strip()}" for c, m in
            re.findall(r"<Codigo>(.*?)</Codigo>\s*<Mensagem>(.*?)</Mensagem>", texto, re.S)]
    m = re.search(r"<ConsultarNfseResposta.*?</ConsultarNfseResposta>", texto, re.S)
    if not m:
        return [], msgs or ["Resposta inesperada do web service do Recife."]

    root = ET.fromstring(m.group(0))
    notas = []
    for cp in (e for e in root.iter() if _tag(e) == "CompNfse"):
        # SÓ VÁLIDAS: fora as canceladas e as substituídas
        if any(_tag(c) in ("NfseCancelamento", "NfseSubstituicao") for c in cp):
            continue
        nfse = next((c for c in cp if _tag(c) == "Nfse"), None)
        if nfse is None:
            continue
        inf = next((e for e in nfse.iter() if _tag(e) == "InfNfse"), None)
        if inf is None:
            continue
        emissao = _txt(inf, "DataEmissao")[:10]
        compet_xml = _txt(inf, "Competencia")[:10]
        prest_el = next((e for e in inf.iter() if _tag(e) == "PrestadorServico"), None)
        toma_el = next((e for e in inf.iter() if _tag(e) == "TomadorServico"), None)
        item = item_lc116_abrasf(_txt(inf, "ItemListaServico"))
        notas.append({
            "origem": "NFS-e (Recife)",
            "arquivo": f"recife-{_txt(inf, 'Numero')}",
            "chave": _txt(inf, "CodigoVerificacao"),
            "numero": _txt(inf, "Numero"),
            "cstat": "100",
            "data": emissao,
            # competência = mês de EMISSÃO (ver nota no topo do arquivo)
            "competencia": emissao[:7],
            "competencia_xml": compet_xml,
            "divergencia_competencia": bool(compet_xml and compet_xml[:7] != emissao[:7]),
            "tomador": _txt(toma_el, "RazaoSocial") if toma_el is not None else "",
            "ctribnac": "",
            "item_lc116": item,
            "descricao": (_txt(inf, "Discriminacao") or "")[:160],
            "valor": _num(_txt(inf, "ValorServicos")),
            "valor_liquido": _num(_txt(inf, "ValorLiquidoNfse")),
            "op_simp_nac": "3" if _txt(inf, "OptanteSimplesNacional") == "1" else "",
            "iss_retido": _txt(inf, "IssRetido") == "1",
            "valor_iss": _num(_txt(inf, "ValorIssRetido")) or _num(_txt(inf, "ValorIss")),
            "municipio": _txt(inf, "CodigoMunicipio") or COD_MUNICIPIO_RECIFE,
            "tipo": "Serviço",
            "cancelada": False,
        })
    return notas, msgs


def sincronizar(pfx_path, senha, pasta_cnpj, cnpj, data_ini, data_fim, im=None) -> dict:
    """Baixa o histórico do período e guarda em `pasta_cnpj/recife/notas.json`
    (mescla com o que já existe, sem duplicar pelo número da nota)."""
    novas, msgs = consultar(pfx_path, senha, cnpj, data_ini, data_fim, im)
    pasta = Path(pasta_cnpj) / "recife"
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / "notas.json"

    existentes = []
    if arq.exists():
        try:
            existentes = json.loads(arq.read_text("utf-8"))
        except Exception:
            existentes = []
    por_num = {n.get("numero"): n for n in existentes}
    antes = len(por_num)
    for n in novas:
        por_num[n["numero"]] = n
    todas = sorted(por_num.values(), key=lambda x: x.get("data") or "", reverse=True)
    arq.write_text(json.dumps(todas, ensure_ascii=False), encoding="utf-8")
    return {"baixadas": len(novas), "novas": len(por_num) - antes,
            "total": len(todas), "mensagens": msgs}


def ler(pasta_cnpj) -> list[dict]:
    arq = Path(pasta_cnpj) / "recife" / "notas.json"
    if not arq.exists():
        return []
    try:
        return json.loads(arq.read_text("utf-8"))
    except Exception:
        return []
