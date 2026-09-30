"""
prefeituras.py — Histórico municipal de NFS-e nas cidades ALÉM do Recife.

Serve ao mesmo fim do recife.py: completar o RBT12 com as notas emitidas ANTES
da adesão do município à NFS-e Nacional, que não existem na API nacional (ADN).

O Recife tem módulo próprio (recife.py) porque usa um web service caseiro, com
namespace fora do padrão. As cidades daqui usam provedores de mercado, então dá
para tratar por FAMÍLIA de provedor em vez de uma implementação por cidade.

── GissOnline / Eicon (Paulista-PE) ────────────────────────────────────────
Confirmado no portal do contador (paulista.giss.com.br) e no manual técnico
publicado pela própria prefeitura:
  provedor : GissOnline (Eicon)  ·  layout ABRASF 2.04
  WSDL     : https://ws-paulista.giss.com.br/service-ws/nf/nfse-ws?wsdl
             (URLs novas obrigatórias desde 31/10/2024)
  método   : ConsultarNfseServicoPrestado
  auth     : certificado ICP-Brasil (mTLS) — o MESMO A1 que o Fiscale já usa;
             NÃO depende do login/senha do contador no portal
  manual   : https://paulista.giss.com.br/giss-ajuda/manuais/
             Manual_Tecnico_-_Servicos_Prestados_-_V1.3.pdf

⚠️ ESTE MÓDULO AINDA NÃO FOI TESTADO CONTRA O SERVIÇO REAL. Foi escrito a partir
do padrão ABRASF 2.04 e da documentação do provedor. Cada prefeitura costuma ter
um detalhe próprio (namespace, exigência de assinatura, formato de data) que só
aparece no primeiro teste com certificado — no Recife foram DOIS. Enquanto o
teste real não passar, trate o resultado como não confirmado.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

import requests
from requests_pkcs12 import Pkcs12Adapter

NS_ABRASF_204 = "http://www.abrasf.org.br/nfse.xsd"
# Namespaces PRÓPRIOS do GissOnline — descobertos na resposta real do servidor de
# Paulista (ele devolve V999/E183 quando recebe o namespace genérico da ABRASF).
NS_GISS_CABEC = "http://www.giss.com.br/cabecalho-v2_04.xsd"
NS_GISS_ENVIO = "http://www.giss.com.br/consultar-nfse-servico-prestado-envio-v2_04.xsd"
NS_GISS_TIPOS = "http://www.giss.com.br/tipos-v2_04.xsd"

# Cidades atendidas por este módulo. `im_obrigatoria` = o provedor exige a
# Inscrição Municipal na consulta (o GissOnline exige).
CIDADES = {
    # O Recife tem implementação própria (recife.py), testada contra o serviço
    # real. Fica listado aqui só para a tela ter UM lugar de download; o
    # sincronizar() roteia para o módulo dele.
    "2611606": {
        "nome": "Recife", "uf": "PE", "provedor": "sistema próprio (Prefeitura)",
        "layout": "ABRASF (WSNacional)", "im_obrigatoria": False,
        "wsdl": "__recife__",          # marcador: usa recife.py
        "portal": "https://nfse.recife.pe.gov.br",
        "aderiu_nacional": "2025-11",
        "testado": True,
    },
    "2610707": {
        "nome": "Paulista", "uf": "PE", "provedor": "GissOnline (Eicon)",
        "layout": "ABRASF 2.04", "im_obrigatoria": True,
        "wsdl": "https://ws-paulista.giss.com.br/service-ws/nf/nfse-ws",
        "portal": "https://paulista.giss.com.br",
        "aderiu_nacional": "2026-01",   # a partir daqui a API nacional já traz
    },
    # As demais ficam declaradas para a tela listar, mas sem endpoint confirmado.
    # Olinda: conferido no portal real (01/08/2026). É Tinus, não sistema
    # próprio. O portal separa "NFS-e Antiga (até 21/12/2025)" da "NFS-e
    # Nacional (a partir de 22/12/2025)" — a antiga é justamente o histórico que
    # falta no RBT12 — e oferece **XML Notas**, que exporta o lote de XML.
    # Por isso o caminho aqui é IMPORTAR o XML exportado, não o web service:
    # o Tinus exige credenciamento prévio (mesmo caso do Jaboatão) e a
    # exportação já resolve, sem depender de autorização.
    "2609600": {
        "nome": "Olinda", "uf": "PE", "provedor": "Tinus Informática",
        "layout": "ABRASF (portal exporta XML)", "im_obrigatoria": False,
        "wsdl": "", "portal": "https://tinus.com.br/csp/OLINDA/portal/",
        "aderiu_nacional": "2025-12",
        "importa_xml": True,
        "observacao": "O portal exporta os XML em lote: menu «NFS-e Antiga (serv. "
                      "prestado até 21/12/2025) → XML Notas». Baixe e solte aqui — "
                      "não depende de credenciar web service.",
    },
    "2600054": {
        "nome": "Abreu e Lima", "uf": "PE", "provedor": "tributosmunicipais.com.br",
        "layout": "ABRASF 2.04", "im_obrigatoria": True,
        "wsdl": "", "portal": "https://www.tributosmunicipais.com.br/NFE-abreuelima/",
        "aderiu_nacional": "2025-12",
    },
    "2607901": {
        "nome": "Jaboatão dos Guararapes", "uf": "PE", "provedor": "Tinus",
        "layout": "ABRASF 1.00", "im_obrigatoria": True,
        "wsdl": "", "portal": "",
        "aderiu_nacional": "",
        "observacao": "A prefeitura exige autorização prévia para usar o web "
                      "service (produção e homologação) e o envio de um RPS de teste.",
    },
}


def cidades_disponiveis() -> list[dict]:
    """Lista para a tela: o que já dá para consultar e o que ainda não."""
    out = []
    for cod, c in CIDADES.items():
        out.append({
            "ibge": cod, "nome": c["nome"], "uf": c["uf"],
            "provedor": c["provedor"], "layout": c["layout"],
            "portal": c.get("portal") or "",
            "aderiu_nacional": c.get("aderiu_nacional") or "",
            "im_obrigatoria": c["im_obrigatoria"],
            "pronto": bool(c.get("wsdl")),
            "importa_xml": bool(c.get("importa_xml")),   # portal exporta XML
            "tem_caminho": bool(c.get("wsdl") or c.get("importa_xml")),
            "testado": bool(c.get("testado")),   # já rodou contra o serviço real
            "observacao": c.get("observacao") or "",
        })
    # testadas primeiro, depois as que já têm caminho, depois o resto
    return sorted(out, key=lambda x: (not x["testado"], not x["tem_caminho"], x["nome"]))


def _sessao(pfx_path: str, senha: str) -> requests.Session:
    s = requests.Session()
    s.mount("https://", Pkcs12Adapter(pkcs12_filename=pfx_path, pkcs12_password=senha))
    return s


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _txt(el, nome) -> str:
    for x in el.iter():
        if _tag(x) == nome and (x.text or "").strip():
            return x.text.strip()
    return ""


def _num(v):
    try:
        return round(float(str(v).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return 0.0


def _erro_do_soap(texto: str) -> str:
    """Extrai a mensagem real de uma resposta de erro. Um HTTP 500 quase sempre
    traz um SOAP Fault com o motivo — sem isto o usuário só vê 'Internal Server
    Error', que não diz nada e não dá para agir."""
    if not texto:
        return ""
    for padrao in (r"<faultstring[^>]*>(.*?)</faultstring>",
                   r"<Mensagem>(.*?)</Mensagem>",
                   r"<Descricao>(.*?)</Descricao>",
                   r"<faultcode[^>]*>(.*?)</faultcode>",
                   r"<title>(.*?)</title>"):
        m = re.search(padrao, texto, re.S | re.I)
        if m and m.group(1).strip():
            return re.sub(r"\s+", " ", m.group(1)).strip()[:300]
    limpo = re.sub(r"<[^>]+>", " ", texto)
    limpo = re.sub(r"\s+", " ", limpo).strip()
    return limpo[:300]


def _envelope_abrasf204(corpo_dados: str, metodo: str, ns_servico: str) -> str:
    """Envelope do padrão ABRASF 2.04: o XML de negócio vai DENTRO de
    <nfseCabecMsg> e <nfseDadosMsg>, como texto (CDATA) — não solto no Body.
    Era este o erro da primeira versão.

    O cabeçalho usa o namespace PRÓPRIO do GissOnline
    (www.giss.com.br/cabecalho-v2_04.xsd), revelado pela resposta do servidor —
    o namespace genérico da ABRASF é recusado com o código E183."""
    cabec = (f'<cabecalho versao="2.04" xmlns="{NS_GISS_CABEC}">'
             "<versaoDados>2.04</versaoDados></cabecalho>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            f'xmlns:ws="{ns_servico}"><soapenv:Header/><soapenv:Body>'
            f"<ws:{metodo}>"
            f"<nfseCabecMsg><![CDATA[{cabec}]]></nfseCabecMsg>"
            f"<nfseDadosMsg><![CDATA[{corpo_dados}]]></nfseDadosMsg>"
            f"</ws:{metodo}>"
            "</soapenv:Body></soapenv:Envelope>")


ULTIMA_RESPOSTA = {"envelope": "", "resposta": "", "http": 0}   # p/ diagnóstico


def assinar_xml(xml: str, pfx_path: str, senha: str, id_ref: str = "") -> str:
    """Assinatura XMLDSig *enveloped* no padrão que a ABRASF exige (RSA-SHA1 +
    C14N 2001), usando só a stdlib + cryptography — sem lxml/xmlsec.

    O GissOnline recusa a consulta sem assinatura com o código E174 ("arquivo
    enviado com erro na assinatura"). O Recife não exige; por isso a assinatura
    fica aqui, por provedor, e não no caminho comum.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import pkcs12

    chave, cert, _ = pkcs12.load_key_and_certificates(
        open(pfx_path, "rb").read(), (senha or "").encode())

    # 1) digest do documento canonicalizado (sem assinatura ainda).
    #    O documento final PRECISA ser o próprio c14n — se assinarmos o XML
    #    original e inserirmos a assinatura nele, o verificador (que remove a
    #    assinatura e canonicaliza de novo) chega a outro texto e o digest não
    #    bate. Foi o que aconteceu no 1º teste: assinatura inválida.
    xml = ET.canonicalize(xml_data=xml, strip_text=False)
    dig = hashlib.sha1(xml.encode("utf-8")).digest()
    digest_b64 = base64.b64encode(dig).decode()

    uri = f"#{id_ref}" if id_ref else ""
    signed_info = (
        '<SignedInfo xmlns="http://www.w3.org/2000/09/xmldsig#">'
        '<CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
        '<SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>'
        f'<Reference URI="{uri}"><Transforms>'
        '<Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>'
        '<Transform Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
        '</Transforms>'
        '<DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>'
        f"<DigestValue>{digest_b64}</DigestValue></Reference></SignedInfo>")

    # 2) assina o SignedInfo canonicalizado
    c14n_si = ET.canonicalize(xml_data=signed_info, strip_text=False)
    assinatura = chave.sign(c14n_si.encode("utf-8"), padding.PKCS1v15(), hashes.SHA1())
    sig_b64 = base64.b64encode(assinatura).decode()
    cert_b64 = base64.b64encode(
        cert.public_bytes(serialization.Encoding.DER)).decode()

    bloco = ('<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">'
             + signed_info.replace(' xmlns="http://www.w3.org/2000/09/xmldsig#"', "", 1)
             + f"<SignatureValue>{sig_b64}</SignatureValue>"
             f"<KeyInfo><X509Data><X509Certificate>{cert_b64}</X509Certificate>"
             "</X509Data></KeyInfo></Signature>")
    # 3) insere a assinatura como último filho do elemento raiz
    corte = xml.rstrip().rfind("</")
    return xml[:corte] + bloco + xml[corte:]


def consultar_gissonline(pfx_path, senha, cnpj, im, data_ini, data_fim,
                         wsdl: str, pagina: int = 1) -> tuple[list, list]:
    """ConsultarNfseServicoPrestado (ABRASF 2.04) do GissOnline.
    Devolve (notas, mensagens). Não levanta exceção de negócio — devolve avisos."""
    cnpj = re.sub(r"\D", "", cnpj or "")
    prest = f"<t:Cnpj>{cnpj}</t:Cnpj>"
    # Namespaces do GissOnline (confirmado pelo ContextoTecnico do próprio
    # servidor): os elementos ESTRUTURAIS (Prestador, PeriodoEmissao, Pagina)
    # ficam no ns de ENVIO — que aqui é o default —, e os campos de dados
    # dentro deles no ns de TIPOS (prefixo t).
    dados = (
        f'<ConsultarNfseServicoPrestadoEnvio xmlns="{NS_GISS_ENVIO}" '
        f'xmlns:t="{NS_GISS_TIPOS}">'
        f"<Prestador><t:CpfCnpj>{prest}</t:CpfCnpj>"
        + (f"<t:InscricaoMunicipal>{re.sub(r'[^0-9A-Za-z]', '', str(im))}</t:InscricaoMunicipal>" if im else "")
        + "</Prestador>"
        f"<PeriodoEmissao><DataInicial>{data_ini}</DataInicial>"
        f"<DataFinal>{data_fim}</DataFinal></PeriodoEmissao>"
        f"<Pagina>{pagina}</Pagina>"
        "</ConsultarNfseServicoPrestadoEnvio>")

    # O GissOnline devolve E174 ("erro na assinatura") se a consulta vier sem
    # assinatura digital. Falhar aqui não pode derrubar a consulta inteira —
    # mandamos sem assinar e o próprio serviço dirá o que faltou.
    try:
        dados = assinar_xml(dados, pfx_path, senha)
    except Exception as e:
        ULTIMA_RESPOSTA["assinatura"] = f"falhou: {e.__class__.__name__}: {e}"
    else:
        ULTIMA_RESPOSTA["assinatura"] = "ok"

    sess = _sessao(pfx_path, senha)
    # O namespace do serviço varia entre instalações do GissOnline; tenta os
    # conhecidos e guarda o motivo de cada recusa para o usuário ver.
    # Descoberto no teste real contra o servidor de Paulista:
    #   1) o elemento é "ConsultarNfseServicoPrestadoRequest" no namespace
    #      "http://nfse.abrasf.org.br" (SEM barra no fim) — o próprio Fault disse;
    #   2) o SOAPAction NÃO é o nome do elemento ("does not match an operation").
    # Então o elemento fica fixo e variamos só o SOAPAction, que é o que resta.
    NS = "http://nfse.abrasf.org.br"
    METODO = "ConsultarNfseServicoPrestadoRequest"
    acoes = ["", "ConsultarNfseServicoPrestado",
             f"{NS}/ConsultarNfseServicoPrestado",
             "consultarNfseServicoPrestado"]
    texto, erros = "", []
    env = _envelope_abrasf204(dados, METODO, NS)
    for acao in acoes:
        cab = {"Content-Type": "text/xml; charset=utf-8"}
        if acao:
            cab["SOAPAction"] = f'"{acao}"'
        try:
            r = sess.post(wsdl, data=env.encode("utf-8"), headers=cab, timeout=180)
        except Exception as e:
            erros.append(f"SOAPAction '{acao}': {e.__class__.__name__}")
            continue
        ULTIMA_RESPOSTA.update({"envelope": env[:1200], "resposta": r.text[:4000],
                                "http": r.status_code, "soapaction": acao or "(vazio)"})
        if r.status_code == 200 and "Fault" not in r.text[:2000]:
            texto = r.text
            break
        erros.append(f"SOAPAction '{acao or '(vazio)'}' → HTTP {r.status_code}: {_erro_do_soap(r.text)}")
    if not texto:
        return [], ["O web service recusou a consulta. Respostas recebidas:"] + erros[:3]

    # a resposta traz o XML de negócio dentro de <outputXML>/<return> (CDATA)
    m_out = re.search(r"<(?:outputXML|return|.*?Resposta)>(.*?)</(?:outputXML|return|.*?Resposta)>",
                      texto, re.S)
    if m_out:
        import html as _html
        texto = _html.unescape(m_out.group(1))

    # as mensagens vêm com prefixo de namespace (<ns3:Codigo>) — o padrão precisa
    # aceitar prefixo, senão o retorno do provedor passa despercebido
    # as mensagens vêm com prefixo de namespace (<ns3:Codigo>) — o padrão precisa
    # aceitar prefixo, senão o retorno do provedor passa despercebido
    msgs = [f"[{c.strip()}] {m.strip()}" for c, m in
            re.findall(r"<(?:\w+:)?Codigo>(.*?)</(?:\w+:)?Codigo>\s*"
                       r"<(?:\w+:)?Mensagem>(.*?)</(?:\w+:)?Mensagem>", texto, re.S)]
    # o ContextoTecnico diz EXATAMENTE o que o schema recusou — é o que permite
    # acertar o formato de um município novo sem tentativa e erro às cegas
    msgs += ["↳ " + re.sub(r"\s+", " ", ct).strip()[:300] for ct in
             re.findall(r"<(?:\w+:)?ContextoTecnico>(.*?)</(?:\w+:)?ContextoTecnico>", texto, re.S)]
    try:
        root = ET.fromstring(texto)
    except ET.ParseError:
        return [], msgs or ["Resposta do web service não é um XML válido."]

    notas = []
    for cp in (e for e in root.iter() if _tag(e) in ("CompNfse", "Nfse")):
        # nota cancelada/substituída não entra na receita (mesma regra do Recife)
        if any(_tag(x) in ("NfseCancelamento", "NfseSubstituicao") for x in cp.iter()):
            continue
        numero = _txt(cp, "Numero")
        data = (_txt(cp, "DataEmissao") or "")[:10]
        if not numero or not data:
            continue
        valor = _num(_txt(cp, "ValorServicos"))
        notas.append({
            "origem": f"NFS-e ({CIDADES.get('2610707', {}).get('nome', 'município')})",
            "arquivo": f"mun-{numero}", "chave": _txt(cp, "CodigoVerificacao") or "",
            "numero": numero, "cstat": "100",
            "data": data, "competencia": data[:7], "competencia_xml": data,
            "divergencia_competencia": False,
            "tomador": _txt(cp, "RazaoSocial"),
            "item_lc116": (_txt(cp, "ItemListaServico") or "").strip(),
            "descricao": _txt(cp, "Discriminacao")[:200],
            "valor": valor, "valor_liquido": _num(_txt(cp, "ValorLiquidoNfse")) or valor,
            "op_simp_nac": "", "iss_retido": _txt(cp, "IssRetido") == "1",
            "valor_iss": _num(_txt(cp, "ValorIss")),
            "tipo": "Serviço", "cancelada": False,
        })
    return notas, msgs


def sincronizar(pfx_path, senha, pasta_cnpj, cnpj, ibge, im, data_ini, data_fim) -> dict:
    """Baixa o histórico da cidade e guarda em 'municipal/<ibge>.json'.
    Pagina até acabar (o ABRASF 2.04 devolve no máximo 50 por página)."""
    cid = CIDADES.get(str(ibge))
    if not cid:
        return {"ok": False, "erro": "Cidade não atendida por este módulo."}
    if not cid.get("wsdl"):
        return {"ok": False, "erro": f"{cid['nome']}: endpoint do web service ainda "
                                     "não confirmado — não dá para consultar."}
    # Recife: implementação própria e já testada — só repassa
    if cid["wsdl"] == "__recife__":
        import recife as recifemod
        r = recifemod.sincronizar(pfx_path, senha, pasta_cnpj, cnpj,
                                  data_ini, data_fim, im)
        return {"ok": True, "cidade": cid["nome"],
                "baixadas": r.get("baixadas", r.get("total", 0)),
                "novas": r.get("novas", 0), "total": r.get("total", 0),
                "mensagens": r.get("mensagens", [])[:10]}
    if cid["im_obrigatoria"] and not im:
        return {"ok": False, "erro": f"{cid['nome']} exige a Inscrição Municipal na consulta."}

    todas, msgs, pagina = [], [], 1
    while pagina <= 200:                      # trava de segurança
        notas, m = consultar_gissonline(pfx_path, senha, cnpj, im,
                                        data_ini, data_fim, cid["wsdl"], pagina)
        msgs += [x for x in m if x not in msgs]
        if not notas:
            break
        todas += notas
        if len(notas) < 50:                   # última página
            break
        pagina += 1

    # Nada veio E o serviço reclamou: é falha, não "período sem notas".
    if not todas and any("recusou" in x or "HTTP" in x for x in msgs):
        return {"ok": False, "cidade": cid["nome"],
                "erro": f"{cid['nome']} não aceitou a consulta.",
                "detalhes": msgs[:4],
                "dica": "Este município ainda não foi testado de verdade. Me mande estas "
                        "mensagens que eu ajusto o formato — foi assim que o Recife ficou de pé."}

    destino = Path(pasta_cnpj) / "municipal"
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino / f"{ibge}.json"
    # mescla com o que já existe, sem duplicar (chave = número da nota)
    antigas = []
    if arq.exists():
        try:
            antigas = json.loads(arq.read_text("utf-8"))
        except Exception:
            antigas = []
    por_num = {n["numero"]: n for n in antigas}
    novas = 0
    for n in todas:
        if n["numero"] not in por_num:
            novas += 1
        por_num[n["numero"]] = n
    final = sorted(por_num.values(), key=lambda x: x.get("data") or "", reverse=True)
    arq.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "cidade": cid["nome"], "baixadas": len(todas), "novas": novas,
            "total": len(final), "mensagens": msgs[:10]}


def importar_xml_nfse(pasta_cnpj, cnpj: str, arquivos) -> dict:
    """Importa NFS-e a partir dos XML exportados pelo PORTAL da prefeitura.

    Existe porque nem toda cidade libera web service: o Tinus (Olinda, Jaboatão)
    exige credenciamento, mas o portal exporta o lote de XML. Baixar de lá e
    soltar aqui resolve o histórico sem depender de autorização de ninguém.

    Aceita .xml solto e .zip. As notas caem em 'xmls/', a mesma pasta da NFS-e
    Nacional — então entram no RBT12 e no DAS pelo caminho de sempre.

    `arquivos` = lista de (nome, bytes).
    """
    import io
    import zipfile as _zip
    import core

    destino = Path(pasta_cnpj) / "xmls"
    destino.mkdir(parents=True, exist_ok=True)
    cnpj_d = re.sub(r"\D", "", cnpj or "")
    r = {"lidos": 0, "importadas": 0, "repetidas": 0, "de_outra_empresa": 0,
         "invalidos": 0, "competencias": {}}

    def _um(nome: str, dados: bytes):
        r["lidos"] += 1
        try:
            texto = dados.decode("utf-8", "ignore")
            raiz = ET.fromstring(texto)
        except Exception:
            r["invalidos"] += 1
            return
        # precisa parecer NFS-e
        corpo = texto[:1500].lower()
        if not ("nfse" in raiz.tag.lower() or "nfse" in corpo or "abrasf" in corpo):
            r["invalidos"] += 1
            return
        # a nota é DESTA empresa? (evita misturar carteira)
        cnpjs = {re.sub(r"\D", "", (x.text or "")) for x in raiz.iter()
                 if _tag(x) in ("Cnpj", "CNPJ", "CpfCnpj")}
        if cnpj_d not in cnpjs:
            r["de_outra_empresa"] += 1
            return
        base = re.sub(r"[^0-9A-Za-z_.-]", "_", nome)
        if not base.lower().endswith(".xml"):
            base += ".xml"
        if not base.startswith("nfse-"):
            base = "nfse-" + base
        alvo = destino / base
        if alvo.exists():
            r["repetidas"] += 1
            return
        alvo.write_text(texto, encoding="utf-8")
        r["importadas"] += 1
        # conta por competência, para a tela mostrar o que foi coberto
        try:
            n = core.parse_nfse(texto)
            comp = (n or {}).get("competencia") or ""
        except Exception:
            comp = ""
        if comp:
            r["competencias"][comp] = r["competencias"].get(comp, 0) + 1

    for nome, dados in arquivos:
        if nome.lower().endswith(".zip"):
            try:
                with _zip.ZipFile(io.BytesIO(dados)) as z:
                    for interno in z.namelist():
                        if interno.lower().endswith(".xml") and not interno.endswith("/"):
                            _um(interno.split("/")[-1], z.read(interno))
            except Exception:
                r["invalidos"] += 1
        else:
            _um(nome, dados)

    r["competencias"] = dict(sorted(r["competencias"].items()))
    return r


def cidade_da_empresa(pasta_cnpj, cnpj: str) -> str:
    """Descobre em que município a empresa EMITE, pelos 7 primeiros dígitos da
    chave da NFS-e (que são o código IBGE). Devolve o código mais frequente."""
    import core
    from collections import Counter
    c = re.sub(r"\D", "", cnpj or "")
    cont: Counter = Counter()
    try:
        for n in core.carregar_notas(pasta_cnpj):
            if re.sub(r"\D", "", n.get("emit_cnpj") or "") != c:
                continue
            ch = re.sub(r"\D", "", n.get("chave") or "")
            if len(ch) >= 7:
                cont[ch[:7]] += 1
    except Exception:
        return ""
    return cont.most_common(1)[0][0] if cont else ""


def pendencias(pasta_cnpj, cnpj: str, pa: str | None = None) -> dict:
    """O que falta para o RBT12 fechar e se dá para buscar na prefeitura.

    É o que permite a busca automática: ao consultar uma empresa, o sistema já
    sabe em que cidade ela emite, quais meses do RBT12 estão vazios e se aquele
    município tem download disponível."""
    import classificador as clsmod
    from datetime import date

    pa = pa or date.today().strftime("%Y-%m")
    try:
        notas = clsmod.ler_nfse(pasta_cnpj, cnpj)
        # o que já veio das prefeituras também conta
        try:
            import recife as recifemod
            notas = notas + recifemod.ler(pasta_cnpj)
        except Exception:
            pass
        notas = notas + ler(pasta_cnpj)
        r12 = clsmod.rbt12_de(notas, pa, None)
    except Exception as e:
        return {"ok": False, "erro": f"Não consegui ler as notas ({e.__class__.__name__})."}

    vazios = [m["comp"] for m in r12["meses"] if m["valor"] == 0]
    ibge = cidade_da_empresa(pasta_cnpj, cnpj)
    cid = CIDADES.get(ibge)
    # só vale buscar o que é ANTERIOR à adesão do município ao nacional
    limite = (cid or {}).get("aderiu_nacional") or ""
    buscaveis = [m for m in vazios if not limite or m < limite]

    return {
        "ok": True, "pa": pa, "rbt12": r12["total"],
        "meses_vazios": vazios, "buscaveis": buscaveis,
        "cidade": ({"ibge": ibge, "nome": cid["nome"], "uf": cid["uf"],
                    "provedor": cid["provedor"], "pronto": bool(cid.get("wsdl")),
                    "testado": bool(cid.get("testado")),
                    "im_obrigatoria": cid["im_obrigatoria"],
                    "aderiu_nacional": limite} if cid else
                   ({"ibge": ibge, "nome": "(município não atendido)", "pronto": False}
                    if ibge else None)),
        "periodo_sugerido": ({"ini": buscaveis[0] + "-01",
                              "fim": _fim_do_mes(buscaveis[-1])} if buscaveis else None),
    }


def _fim_do_mes(comp: str) -> str:
    import calendar
    a, m = int(comp[:4]), int(comp[5:7])
    return f"{comp}-{calendar.monthrange(a, m)[1]:02d}"


def ler(pasta_cnpj, ibge=None) -> list[dict]:
    """Notas municipais já baixadas (de uma cidade ou de todas)."""
    base = Path(pasta_cnpj) / "municipal"
    if not base.exists():
        return []
    arqs = [base / f"{ibge}.json"] if ibge else sorted(base.glob("*.json"))
    out = []
    for a in arqs:
        if not a.exists():
            continue
        try:
            out += json.loads(a.read_text("utf-8"))
        except Exception:
            continue
    return out
