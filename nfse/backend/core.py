"""
core.py — Núcleo do sistema NFS-e Nacional.

Responsável por:
  - sincronizar os DFe via API oficial ADN (distribuição por NSU, mTLS com A1);
  - decodificar o XML (GZip + Base64);
  - interpretar cada NFS-e (competência, emitente, tomador, valores) e cada
    evento (cancelamentos);
  - filtrar apenas as notas VÁLIDAS e EMITIDAS de uma competência escolhida.

Não depende de nada do FastAPI — é uma camada pura, fácil de testar.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests
from requests_pkcs12 import Pkcs12Adapter
from cryptography.hazmat.primitives.serialization import pkcs12 as _pkcs12

# Namespace padrão dos XMLs da NFS-e Nacional
NS = "{http://www.sped.fazenda.gov.br/nfse}"

import xml.etree.ElementTree as ET

BASES_ADN = {
    "producao": "https://adn.nfse.gov.br",
    "restrita": "https://adn.producaorestrita.nfse.gov.br",
}

ROTA_DFE = "/contribuintes/DFe/{nsu}"


# ─────────────────────────────────────────────────────────────────────────────
# Certificado
# ─────────────────────────────────────────────────────────────────────────────
def cnpj_do_certificado(pfx_path: str, senha: str) -> Optional[str]:
    """Lê o CNPJ (14 dígitos) do titular do certificado A1."""
    dados = Path(pfx_path).read_bytes()
    _key, cert, _add = _pkcs12.load_key_and_certificates(dados, senha.encode("utf-8"))
    if cert is None:
        return None
    subject = cert.subject.rfc4514_string()
    m = re.search(r"(\d{14})", subject)
    return m.group(1) if m else None


def documento_do_certificado(pfx_path: str, senha: str) -> Optional[str]:
    """CNPJ (14) ou CPF (11) do titular.

    O e-CPF é necessário porque várias prefeituras só liberam as notas ao
    PROCURADOR — o contador acessa com o certificado dele, não com o da empresa.
    Sem aceitar CPF aqui, esse certificado nem chega a ser cadastrado.
    """
    dados = Path(pfx_path).read_bytes()
    _key, cert, _add = _pkcs12.load_key_and_certificates(dados, senha.encode("utf-8"))
    if cert is None:
        return None
    # O documento do TITULAR está no CN, no formato "NOME:DOCUMENTO". Procurar
    # no subject inteiro é armadilha: o e-CPF do Fulano traz um CNPJ em
    # outro campo (OU) e o sistema cadastrou o certificado como se fosse PJ.
    for attr in cert.subject:
        nome = getattr(attr.oid, "_name", "")
        if nome in ("commonName", "common_name") or attr.oid.dotted_string == "2.5.4.3":
            cn = attr.value or ""
            m = re.search(r":(\d{14})\b", cn) or re.search(r":(\d{11})\b", cn)
            if m:
                return m.group(1)
            m = re.search(r"(\d{14})", cn) or re.search(r"(?<!\d)(\d{11})(?!\d)", cn)
            if m:
                return m.group(1)
    # sem CN utilizável: cai para o subject inteiro (CNPJ antes de CPF)
    subject = cert.subject.rfc4514_string()
    m = re.search(r"(\d{14})", subject) or re.search(r"(?<!\d)(\d{11})(?!\d)", subject)
    return m.group(1) if m else None


def nome_do_certificado(pfx_path: str, senha: str) -> Optional[str]:
    """Razão social / nome do titular (parte antes do ':' no CN)."""
    dados = Path(pfx_path).read_bytes()
    _key, cert, _add = _pkcs12.load_key_and_certificates(dados, senha.encode("utf-8"))
    if cert is None:
        return None
    for attr in cert.subject:
        if attr.oid._name in ("commonName", "common_name") or attr.oid.dotted_string == "2.5.4.3":
            return attr.value.split(":")[0].strip()
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Sincronização (download incremental dos DFe)
# ─────────────────────────────────────────────────────────────────────────────
def _sessao_mtls(pfx_path: str, senha: str) -> requests.Session:
    s = requests.Session()
    s.mount("https://", Pkcs12Adapter(pkcs12_filename=pfx_path, pkcs12_password=senha))
    return s


# CSRF — Contribuição Social Retida na Fonte (IN RFB 459/2004, art. 2º).
# 4,65% = PIS 0,65% + COFINS 3,00% + CSLL 1,00%.
# A regra da retenção social mora em retencao.py (uma só para tela, apuração,
# classificador e DANFSe). Os nomes ficam aqui por compatibilidade.
import retencao as _ret                                     # noqa: E402
CSRF_PIS, CSRF_COFINS, CSRF_CSLL = _ret.CSRF_PIS, _ret.CSRF_COFINS, _ret.CSRF_CSLL
CSRF_TOTAL = _ret.CSRF_TOTAL


class LimiteRequisicoes(Exception):
    """Estourou o limite de requisições da API (HTTP 429) mesmo após retentar."""


class ServicoIndisponivel(Exception):
    """O serviço da Receita respondeu 502/503/504 — está fora do ar. Nada a
    corrigir do nosso lado; a tela precisa dizer isso sem parecer erro nosso."""


def _get_com_retry(sess: requests.Session, url: str, tentativas: int = 6) -> requests.Response:
    """GET com backoff em caso de 429 (respeita Retry-After quando presente)."""
    espera = 10
    r = None
    for _ in range(tentativas):
        r = sess.get(url, headers={"Accept": "application/json"}, timeout=60)
        if r.status_code != 429:
            return r
        ra = r.headers.get("Retry-After", "")
        pausa = int(ra) if ra.isdigit() else espera
        time.sleep(min(pausa, 60))
        espera = min(espera * 2, 60)
    raise LimiteRequisicoes(
        "Limite de requisições da Receita atingido. Aguarde alguns minutos e tente novamente "
        "(o progresso já baixado foi salvo)."
    )


def _decodificar_arquivo(b64: str) -> str:
    """Base64 -> (se GZip) descompacta -> texto XML UTF-8."""
    bruto = base64.b64decode(re.sub(r"\s", "", b64))
    if len(bruto) >= 2 and bruto[0] == 0x1F and bruto[1] == 0x8B:
        return gzip.decompress(bruto).decode("utf-8")
    return bruto.decode("utf-8")


def sincronizar(
    pfx_path: str,
    senha: str,
    pasta_cnpj: str | Path,
    ambiente: str = "producao",
    progresso: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """
    Baixa todos os DFe novos (a partir do último NSU salvo) para
    `pasta_cnpj/xmls`, salvando o estado do NSU. Retorna um resumo.
    """
    base = BASES_ADN[ambiente]
    pasta = Path(pasta_cnpj)
    xml_dir = pasta / "xmls"
    xml_dir.mkdir(parents=True, exist_ok=True)
    estado_arq = pasta / "estado.json"

    nsu = 0
    if estado_arq.exists():
        try:
            # utf-8-sig tolera BOM caso o arquivo tenha sido gravado por outra ferramenta
            nsu = int(json.loads(estado_arq.read_text("utf-8-sig")).get("ultimoNSU", 0))
        except Exception:
            nsu = 0

    sess = _sessao_mtls(pfx_path, senha)
    novos = 0

    while True:
        url = base + ROTA_DFE.format(nsu=nsu)
        r = _get_com_retry(sess, url)

        # 404 + NENHUM_DOCUMENTO_LOCALIZADO = fim normal da distribuição
        if r.status_code == 404 and "NENHUM_DOCUMENTO_LOCALIZADO" in r.text:
            break
        r.raise_for_status()

        data = r.json()
        lote = data.get("LoteDFe") or []
        if not lote:
            break

        for d in lote:
            xml = _decodificar_arquivo(d["ArquivoXml"])
            chave = d.get("ChaveAcesso") or f"nsu{d['NSU']}"
            prefixo = "evento" if d.get("TipoDocumento") == "EVENTO" else "nfse"
            (xml_dir / f"{prefixo}-{chave}.xml").write_text(xml, encoding="utf-8")
            novos += 1

        max_nsu = max(int(d["NSU"]) for d in lote)
        estado_arq.write_text(
            json.dumps({"ultimoNSU": max_nsu, "atualizadoEm": datetime.now().isoformat()}),
            encoding="utf-8",
        )
        if progresso:
            progresso(max_nsu, novos)
        if max_nsu <= nsu:
            break
        nsu = max_nsu
        time.sleep(0.5)  # cortesia contra rate-limit (429)

    return {"novos": novos, "ultimoNSU": nsu}


# ─────────────────────────────────────────────────────────────────────────────
# DANFSE (PDF oficial da nota, via ADN)
# ─────────────────────────────────────────────────────────────────────────────
def baixar_danfse(
    pfx_path: str,
    senha: str,
    chave: str,
    ambiente: str = "producao",
    cache_dir: str | Path | None = None,
    tentativas: int = 5,
    espera_max: int = 15,
) -> Path:
    """Baixa (com cache em disco) o PDF do DANFSE de uma NFS-e pela chave.

    Endpoint oficial: GET {ADN}/danfse/{chaveAcesso} (mTLS com o A1), que
    devolve application/pdf. Respeita o limite de requisições (HTTP 429).
    """
    chave = re.sub(r"\D", "", chave or "")
    if len(chave) != 50:
        raise ValueError("Chave de acesso inválida.")

    arq = None
    if cache_dir:
        pasta = Path(cache_dir) / "danfse"
        pasta.mkdir(parents=True, exist_ok=True)
        arq = pasta / f"{chave}.pdf"
        if arq.exists() and arq.stat().st_size > 0:
            return arq

    sess = _sessao_mtls(pfx_path, senha)
    url = BASES_ADN[ambiente] + "/danfse/" + chave
    # O gateway do ADN às vezes responde 502/503/504 sob carga (além do 429 de
    # limite); em todos esses casos vale esperar e repetir.
    retryaveis = {429, 502, 503, 504}
    espera = min(6, espera_max)
    r = None
    for _ in range(max(1, tentativas)):
        r = sess.get(url, headers={"Accept": "application/pdf"}, timeout=60)
        if r.status_code not in retryaveis:
            break
        ra = r.headers.get("Retry-After", "")
        time.sleep(min(int(ra) if ra.isdigit() else espera, espera_max))
        espera = min(espera * 2, espera_max)

    if r is None:
        raise RuntimeError("Falha ao contatar o serviço de DANFSE.")
    if r.status_code == 429:
        raise LimiteRequisicoes(
            "Limite de requisições da Receita atingido ao gerar o PDF. "
            "Aguarde alguns instantes e tente novamente."
        )
    # 502/503/504 = o serviço da Receita está fora do ar ou sobrecarregado. Não é
    # problema desta nota nem do certificado, e repetir agora não adianta. Vira
    # exceção própria para a tela dizer isso em vez de mostrar a URL crua (que
    # ainda por cima expõe a chave de acesso inteira).
    if r.status_code in (502, 503, 504):
        raise ServicoIndisponivel(
            "O serviço de DANFSe da Receita Federal está indisponível no momento "
            f"(erro {r.status_code}). Não é problema do seu certificado nem desta "
            "nota — é o servidor do governo. Tente mais tarde; o XML da nota "
            "continua disponível aqui no Fiscale."
        )
    r.raise_for_status()
    if r.content[:4] != b"%PDF":
        raise RuntimeError("O serviço não retornou um PDF válido para esta nota.")

    destino = arq if arq is not None else Path(cache_dir or ".") / f"danfse-{chave}.pdf"
    destino.write_bytes(r.content)
    return destino


# ─────────────────────────────────────────────────────────────────────────────
# Interpretação dos XMLs
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Nota:
    chave: str
    numero: Optional[str]
    competencia: Optional[str]   # "AAAA-MM"
    emit_cnpj: Optional[str]
    emit_nome: Optional[str]
    toma_doc: Optional[str]
    toma_nome: Optional[str]
    valor_servico: float
    valor_liquido: float
    data_emissao: Optional[str]
    cstat: Optional[str]
    data_competencia: Optional[str] = None  # dCompet completo "AAAA-MM-DD"
    tp_ret_piscofins: Optional[str] = None  # 1/3/4 = retido (contrib. sociais)
    valor_retido: float = 0.0               # contribuições sociais retidas (PIS+COFINS+CSLL)
    valor_pis: float = 0.0                  # PIS retido, quando informado à parte
    valor_cofins: float = 0.0               # COFINS retido, quando informado à parte
    valor_csll: float = 0.0                 # CSLL retido (vRetCSLL)
    retencao_detalhada: bool = False        # True: emitente discriminou os três
    retencao_rateada: bool = False          # True: veio consolidada e foi dividida
                                            # pela alíquota legal da CSRF (4,65%)
    retencao_consolidada: bool = False      # True: vRetCSLL já era o total
                                            # (PIS/COFINS destacados NÃO somados)
    alerta_csrf: bool = False               # retenção social ≠ 4,65% do serviço
    alerta_csrf_msg: str = ""               # o porquê, para a tela
    csrf_esperado: float = 0.0              # 4,65% do serviço, arredondado
    valor_irrf: float = 0.0                 # IRRF retido (vRetIRRF) — separado
    tp_ret_iss: Optional[str] = None        # 2=retido p/ tomador, 3=retido p/ intermediário
    valor_iss_retido: float = 0.0           # ISS retido (vISSQN quando há retenção)
    valor_iss: float = 0.0                  # vISSQN da nota, retida OU não (ISS a recolher)
    op_simp_nac: Optional[str] = None       # 1=não optante, 2=MEI, 3=ME/EPP (ISS no DAS)
    municipio_incidencia: Optional[str] = None  # cLocIncid: a prefeitura da guia
    iss_retido: bool = False                # True quando o ISS é retido na nota
    valor_inss: float = 0.0                 # INSS/Contrib. Previdenciária retida (vRetCP)
    inss_retido: bool = False               # True quando há INSS retido na nota
    # O XML COMO VEIO (sem composição): o detalhe da nota espelha as linhas do
    # DANFSe — vRetCSLL cru, e vPis/vCofins que no tpRetPisCofins=2 são débito
    # de apuração própria, não retenção.
    xml_v_pis: float = 0.0
    xml_v_cofins: float = 0.0
    xml_v_ret_csll: float = 0.0
    retido: bool = False
    cancelada: bool = False
    arquivo: str = ""


def _txt(el, path: str) -> Optional[str]:
    if el is None:
        return None
    achado = el.find(path)
    return achado.text if achado is not None else None


def _num(v: Optional[str]) -> float:
    if not v:
        return 0.0
    try:
        return float(v)
    except ValueError:
        return 0.0


def parse_nfse(xml_text: str) -> Optional[dict]:
    root = ET.fromstring(xml_text)
    inf = root.find(f"{NS}infNFSe")
    if inf is None:
        return None

    chave = (inf.get("Id") or "").replace("NFS", "", 1)
    numero = _txt(inf, f"{NS}nNFSe")
    cstat = _txt(inf, f"{NS}cStat")
    dh_proc = _txt(inf, f"{NS}dhProc")
    emit_cnpj = _txt(inf, f"{NS}emit/{NS}CNPJ") or _txt(inf, f"{NS}emit/{NS}CPF")
    emit_nome = _txt(inf, f"{NS}emit/{NS}xNome")
    valor_liq = _num(_txt(inf, f"{NS}valores/{NS}vLiq"))

    competencia = None
    data_competencia = None
    valor_serv = 0.0
    toma_doc = None
    toma_nome = None
    dps = inf.find(f"{NS}DPS/{NS}infDPS")

    # CORREÇÃO: algumas notas chegam com o DPS/infDPS em outra posição da árvore
    # (ex.: NFS-e transcrita de uma Sefin municipal para o layout nacional) e o
    # find() acima não acha nada. Isso fazia a nota ficar com competencia=None
    # e, como o filtro por competência exige correspondência exata, ela
    # simplesmente sumia da listagem sem nenhum erro — mesmo estando dentro do
    # período certo. Por isso os campos abaixo têm um fallback de busca em todo
    # o documento (".//") quando não encontrados no caminho aninhado esperado.
    if dps is not None:
        dcompet = _txt(dps, f"{NS}dCompet")           # ex.: 2025-12-02
        valor_serv = _num(_txt(dps, f".//{NS}vServ"))
        toma = dps.find(f"{NS}toma")
        if toma is not None:
            toma_doc = _txt(toma, f"{NS}CNPJ") or _txt(toma, f"{NS}CPF")
            toma_nome = _txt(toma, f"{NS}xNome")
    else:
        dcompet = None

    if not dcompet:
        dcompet = _txt(inf, f".//{NS}dCompet")
    if dcompet and len(dcompet) >= 7:
        competencia = dcompet[:7]                      # AAAA-MM
        data_competencia = dcompet[:10]                # AAAA-MM-DD

    if valor_serv == 0.0:
        valor_serv = _num(_txt(inf, f".//{NS}vServ"))

    if toma_doc is None:
        toma_el = inf.find(f".//{NS}toma")
        if toma_el is not None:
            toma_doc = _txt(toma_el, f"{NS}CNPJ") or _txt(toma_el, f"{NS}CPF")
            toma_nome = _txt(toma_el, f"{NS}xNome")

    # Última rede de segurança: se AINDA assim não veio nenhuma competência
    # (documento muito fora do padrão), deriva do mês/ano da data de emissão —
    # melhor mostrar a nota no mês da emissão do que escondê-la do usuário.
    if not competencia and dh_proc:
        competencia = dh_proc[:7]
        data_competencia = data_competencia or dh_proc[:10]

    # Contribuições sociais retidas (PIS/COFINS/CSLL) — ver NT 007/2026.
    # tpRetPisCofins: 1=PIS/COFINS retido, 3=só PIS, 4=só COFINS (2/0=não retido).
    # vRetCSLL consolida o total retido (PIS+COFINS+CSLL) quando há retenção.
    tp_ret = None
    v_pis = v_cofins = 0.0
    pc = inf.find(f".//{NS}piscofins")
    if pc is not None:
        tp_ret = _txt(pc, f"{NS}tpRetPisCofins")
        v_pis = _num(_txt(pc, f"{NS}vPis"))
        v_cofins = _num(_txt(pc, f"{NS}vCofins"))
    vret_el = inf.find(f".//{NS}vRetCSLL")
    v_ret_csll = _num(vret_el.text) if vret_el is not None else 0.0

    # A COMPOSIÇÃO É DE retencao.compor — ver o cabeçalho de lá.
    #     vRetCSLL tem dois significados no acervo real: às vezes é só a CSLL
    #     (e o total é a soma dos três), às vezes já é o consolidado (e somar
    #     PIS/COFINS por cima DUPLICA). A regra decide pela estrutura da nota.
    comp = _ret.compor(tp_ret, v_pis, v_cofins, v_ret_csll, valor_serv)
    pis_ret, cofins_ret = comp["pis"], comp["cofins"]
    v_ret_csll = comp["csll"]
    rateado = comp["rateada"]
    valor_retido = comp["total"]
    retencao_detalhada = comp["detalhada"]
    # CONFERÊNCIA: a retenção social bate com os 4,65% da Lei 10.833/2003?
    alerta = _ret.alerta_csrf(valor_retido, valor_serv)

    # IRRF retido (campo próprio, independente das contribuições sociais)
    irrf_el = inf.find(f".//{NS}vRetIRRF")
    valor_irrf = _num(irrf_el.text) if irrf_el is not None else 0.0

    # ISS retido — tpRetISSQN (DPS/trib/tribMun): 1=não retido, 2=retido pelo
    # tomador, 3=retido pelo intermediário. O valor é o vISSQN consolidado em
    # infNFSe/valores (a Receita já calcula o ISS da nota ali).
    tp_ret_iss = _txt(inf, f".//{NS}tribMun/{NS}tpRetISSQN")
    iss_retido = tp_ret_iss in ("2", "3")
    valor_iss = _num(_txt(inf, f"{NS}valores/{NS}vISSQN"))
    valor_iss_retido = valor_iss if iss_retido else 0.0
    op_simp_nac = _txt(inf, f".//{NS}opSimpNac")
    municipio_incidencia = _txt(inf, f".//{NS}cLocIncid")

    # INSS / Contribuição Previdenciária retida — campo vRetCP (DPS/trib/tribFed).
    # É um valor direto; presença > 0 indica retenção (comum em notas recebidas).
    inss_el = inf.find(f".//{NS}vRetCP")
    valor_inss = _num(inss_el.text) if inss_el is not None else 0.0
    inss_retido = valor_inss > 0

    retido = (valor_retido > 0 or valor_irrf > 0 or valor_inss > 0
              or tp_ret in ("1", "3", "4"))

    return asdict(
        Nota(
            chave=chave,
            numero=numero,
            competencia=competencia,
            emit_cnpj=emit_cnpj,
            emit_nome=emit_nome,
            toma_doc=toma_doc,
            toma_nome=toma_nome,
            valor_servico=valor_serv,
            valor_liquido=valor_liq,
            data_emissao=dh_proc,
            cstat=cstat,
            data_competencia=data_competencia,
            tp_ret_piscofins=tp_ret,
            valor_retido=round(valor_retido, 2),
            valor_pis=round(pis_ret, 2),
            valor_cofins=round(cofins_ret, 2),
            valor_csll=round(v_ret_csll, 2),
            retencao_detalhada=retencao_detalhada or rateado,
            retencao_rateada=rateado,
            retencao_consolidada=comp["consolidada_no_campo"],
            alerta_csrf=alerta["alerta"],
            alerta_csrf_msg=alerta["mensagem"],
            csrf_esperado=alerta["esperado"],
            valor_irrf=round(valor_irrf, 2),
            tp_ret_iss=tp_ret_iss,
            valor_iss_retido=round(valor_iss_retido, 2),
            valor_iss=round(valor_iss, 2),
            op_simp_nac=op_simp_nac,
            municipio_incidencia=municipio_incidencia,
            iss_retido=iss_retido,
            valor_inss=round(valor_inss, 2),
            inss_retido=inss_retido,
            xml_v_pis=round(v_pis, 2),
            xml_v_cofins=round(v_cofins, 2),
            xml_v_ret_csll=round(_num(vret_el.text) if vret_el is not None else 0.0, 2),
            retido=retido,
        )
    )


def parse_evento(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    ch = root.find(f".//{NS}chNFSe")
    desc = root.find(f".//{NS}xDesc")
    chsub = root.find(f".//{NS}chSubstituta")   # presente no cancelamento por substituição
    return {
        "chNFSe": ch.text if ch is not None else None,
        "desc": desc.text if desc is not None else "",
        "chSubstituta": chsub.text if chsub is not None else None,
    }


def _eh_cancelamento(desc: str) -> bool:
    return "cancel" in (desc or "").lower()


def carregar_notas(pasta_cnpj: str | Path) -> list[dict]:
    """Lê todos os XMLs do cache e devolve as notas com flag de cancelada."""
    xml_dir = Path(pasta_cnpj) / "xmls"
    if not xml_dir.exists():
        return []

    # Classifica cada chave pelos eventos:
    #   canceladas   -> "Cancelamento de NFS-e" (cancelamento simples)
    #   substituidas -> "Cancelamento por Substituição" (a original, agora inválida)
    #   substitutas  -> a chave nova (chSubstituta) que passou a valer no lugar
    canceladas: set[str] = set()
    substituidas: set[str] = set()
    substitutas: set[str] = set()
    for f in xml_dir.glob("evento-*.xml"):
        try:
            ev = parse_evento(f.read_text("utf-8"))
        except Exception:
            continue
        ch = ev["chNFSe"]
        if not ch:
            continue
        desc = (ev["desc"] or "").lower()
        if "substitu" in desc:
            substituidas.add(ch)
            if ev.get("chSubstituta"):
                substitutas.add(ev["chSubstituta"])
        elif "cancel" in desc:
            canceladas.add(ch)

    notas: list[dict] = []
    for f in xml_dir.glob("nfse-*.xml"):
        try:
            n = parse_nfse(f.read_text("utf-8"))
        except Exception:
            continue
        if not n:
            continue
        n["arquivo"] = f.name
        if not n["chave"]:
            n["chave"] = f.name[len("nfse-"):-4]
        ch = n["chave"]
        # Situação da nota (precedência: cancelada > substituída > substituta).
        # ATENÇÃO: cStat=101 é o status da NFS-e *de substituição* (a nota VÁLIDA
        # do par); a invalidação vem do evento na chave original.
        if ch in canceladas:
            n["situacao"] = "Cancelada"
        elif ch in substituidas:
            n["situacao"] = "Substituída"
        elif ch in substitutas:
            n["situacao"] = "Substituta"
        else:
            n["situacao"] = "Normal"
        # Para os filtros de "somente válidas": canceladas e substituídas saem;
        # a substituta é a nota válida do par e permanece.
        n["cancelada"] = ch in canceladas or ch in substituidas
        notas.append(n)
    return notas


# ─────────────────────────────────────────────────────────────────────────────
# Filtros e resumo
# ─────────────────────────────────────────────────────────────────────────────
def _papel_ok(n: dict, cnpj_empresa: Optional[str], papel: str) -> bool:
    """emitidas: empresa é o emitente; recebidas: empresa NÃO é o emitente."""
    if not cnpj_empresa:
        return True
    if papel == "recebidas":
        return n.get("emit_cnpj") != cnpj_empresa
    return n.get("emit_cnpj") == cnpj_empresa


def dentro_intervalo(
    n: dict,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    base_data: str = "emissao",
) -> bool:
    """A nota cai no período [data_ini, data_fim] (AAAA-MM-DD, inclusivo)?

    base_data = "emissao"    -> compara pela data de emissão (dhProc);
    base_data = "competencia"-> compara pela data de competência (dCompet).
    Sem datas informadas = sempre True (não filtra por período).
    """
    if not (data_ini or data_fim):
        return True
    bruto = n.get("data_competencia") if base_data == "competencia" else n.get("data_emissao")
    dt = (bruto or "")[:10]
    if not dt:
        return False
    if data_ini and dt < data_ini:
        return False
    if data_fim and dt > data_fim:
        return False
    return True


def competencias_disponiveis(
    notas: Iterable[dict],
    cnpj_empresa: Optional[str] = None,
    papel: str = "emitidas",
) -> list[str]:
    comps = set()
    for n in notas:
        if not _papel_ok(n, cnpj_empresa, papel):
            continue
        if n.get("competencia"):
            comps.add(n["competencia"])
    return sorted(comps, reverse=True)


def filtrar(
    notas: Iterable[dict],
    competencias: Optional[list[str]] = None,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    base_data: str = "emissao",
    cnpj_empresa: Optional[str] = None,
    papel: str = "emitidas",
    somente_validas: bool = True,
    somente_retidas: bool = False,
) -> list[dict]:
    """
    competencias     -> lista de "AAAA-MM" (None/vazia = ignora este filtro)
    data_ini/data_fim-> período "AAAA-MM-DD" (inclusivo); base_data escolhe a data
    base_data        -> "emissao" ou "competencia"
    cnpj_empresa     -> CNPJ da empresa (para distinguir emitidas/recebidas)
    papel            -> "emitidas" ou "recebidas"
    somente_validas  -> exclui canceladas/substituídas (por evento)
    somente_retidas  -> mantém apenas notas com algum tipo de retenção
    """
    out = []
    for n in notas:
        if not _papel_ok(n, cnpj_empresa, papel):
            continue
        if competencias and n.get("competencia") not in competencias:
            continue
        if not dentro_intervalo(n, data_ini, data_fim, base_data):
            continue
        if somente_validas:
            # O cStat do infNFSe é o TIPO da nota gerada, não um veredito de
            # validade (tabela oficial do leiaute ADN v1.01: 100=Gerada,
            # 101=Substituição, 102=Decisão Judicial/Administrativa, 103=Avulsa,
            # 107=MEI — todos são notas válidas). A invalidação vem SOMENTE dos
            # eventos de cancelamento/substituição (flag "cancelada", acima).
            # Filtrar por lista branca de cStat escondia silenciosamente notas
            # legítimas (ex.: todas as emitidas por MEI, cStat 107).
            if n.get("cancelada"):
                continue
        if somente_retidas and not n.get("retido"):
            continue
        out.append(n)
    return out


def resumo(notas: Iterable[dict]) -> dict:
    notas = list(notas)
    return {
        "quantidade": len(notas),
        "total_servico": round(sum(n["valor_servico"] for n in notas), 2),
        "total_liquido": round(sum(n["valor_liquido"] for n in notas), 2),
        "total_retido": round(sum(n.get("valor_retido", 0.0) for n in notas), 2),
        "total_irrf": round(sum(n.get("valor_irrf", 0.0) for n in notas), 2),
        "total_iss_retido": round(sum(n.get("valor_iss_retido", 0.0) for n in notas), 2),
        "qtd_iss_retido": sum(1 for n in notas if n.get("iss_retido")),
        "total_inss": round(sum(n.get("valor_inss", 0.0) for n in notas), 2),
        "qtd_inss_retido": sum(1 for n in notas if n.get("inss_retido")),
        # Notas cuja retenção social não bate com os 4,65% — pendência de
        # conferência, contada para a tela avisar antes de alguém somar.
        "qtd_alerta_csrf": sum(1 for n in notas if n.get("alerta_csrf")),
    }
