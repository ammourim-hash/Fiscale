"""
nfe.py — Distribuição de DF-e (NF-e modelo 55) via web service nacional da SEFAZ.

Puxa as notas RECEBIDAS (compras) de um CNPJ usando o certificado A1, de forma
incremental por NSU — exatamente como o módulo NFS-e faz com a API ADN.

Observação importante (confirmada em teste): o serviço NFeDistribuicaoDFe entrega
os documentos "de interesse que a empresa NÃO gerou" — ou seja, as COMPRAS
(recebidas), em resumo (resNFe) e/ou XML completo (procNFe), além de eventos.
As notas EMITIDAS (vendas) da própria empresa NÃO são distribuídas por aqui;
para essas usa-se o portal estadual (eFisco), no modo manual.
"""
from __future__ import annotations

import base64
import gzip
import json
import re
import time
from pathlib import Path
import xml.etree.ElementTree as ET

import requests
from requests_pkcs12 import Pkcs12Adapter

NFE = "{http://www.portalfiscal.inf.br/nfe}"
CTE = "{http://www.portalfiscal.inf.br/cte}"   # Conhecimento de Transporte-e (mod 57)
URL_DIST = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
NS_WSDL = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"


class ConsumoIndevido(Exception):
    """A Receita rejeitou por consulta excessiva (cStat 656) — aguardar ~1 hora."""


def _sessao(pfx_path: str, senha: str) -> requests.Session:
    s = requests.Session()
    s.mount("https://", Pkcs12Adapter(pkcs12_filename=pfx_path, pkcs12_password=senha))
    return s


def _envelope(cnpj: str, ult_nsu: int, cuf: str = "26") -> bytes:
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body>'
        f'<nfeDistDFeInteresse xmlns="{NS_WSDL}"><nfeDadosMsg>'
        '<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
        f'<tpAmb>1</tpAmb><cUFAutor>{cuf}</cUFAutor><CNPJ>{cnpj}</CNPJ>'
        f'<distNSU><ultNSU>{int(ult_nsu):015d}</ultNSU></distNSU>'
        '</distDFeInt></nfeDadosMsg></nfeDistDFeInteresse>'
        '</soap12:Body></soap12:Envelope>'
    )
    return xml.encode("utf-8")


class CaminhoDesativado(RuntimeError):
    """Chamada de rede legada bloqueada: existe uma porta única para isto."""


def sincronizar_nfe(*args, **kwargs) -> dict:
    """**DESATIVADO em 17/08/2026.** Use `ingestao.servico_distribuicao`.

    POR QUE FOI BLOQUEADO, E NÃO APAGADO
        Esta função abria uma sessão real com o `NFeDistribuicaoDFe` por fora
        de toda a governança: lia `<cnpj>/nfe/estado.json` como posição
        operacional — um SEGUNDO checkpoint no mesmo espaço de NSU —, não
        gravava o ato `CONSULTA` na trilha e não conhecia cooldown nem estado
        operacional. Um clique em "Sincronizar" na tela podia queimar cota do
        CNPJ sem deixar rastro, e depois aparecer como `656` inexplicável.

        O corpo antigo fica logo abaixo, comentado, porque ele é a
        documentação viva de como o estado legado foi produzido — e esse
        estado continua no disco como evidência histórica.

        O resto deste módulo (leitura de notas, importação de XML, distribuição
        de pasta de entrada, classificação de papel) **continua em uso** e não
        toca a rede. Só o caminho de rede foi fechado.
    """
    raise CaminhoDesativado(
        "A sincronização de NF-e passou a ter porta única: "
        "ingestao.servico_distribuicao.consultar_empresa(). Este caminho legado "
        "não aplica cooldown, não registra auditoria e usa um checkpoint "
        "paralelo, por isso foi desativado.")


def _sincronizar_nfe_legado_desativado(pfx_path, senha, pasta_cnpj, cnpj,
                                       cuf="26", progresso=None,
                                       max_lotes=300) -> dict:
    """CÓDIGO HISTÓRICO — não chamado por ninguém. Ver `sincronizar_nfe`."""
    raise CaminhoDesativado("código histórico; não deve ser executado")
    pasta = Path(pasta_cnpj) / "nfe"
    pasta.mkdir(parents=True, exist_ok=True)
    estado_arq = pasta / "estado.json"

    ult = 0
    if estado_arq.exists():
        try:
            ult = int(json.loads(estado_arq.read_text("utf-8")).get("ultNSU", 0))
        except Exception:
            ult = 0

    sess = _sessao(pfx_path, senha)
    novos = 0
    for _ in range(max_lotes):
        r = sess.post(URL_DIST, data=_envelope(cnpj, ult, cuf),
                      headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                      timeout=90)
        r.raise_for_status()
        ret = ET.fromstring(r.text).find(f".//{NFE}retDistDFeInt")
        if ret is None:
            break
        cstat = ret.findtext(f"{NFE}cStat") or ""
        if cstat == "656":
            raise ConsumoIndevido(
                "A Receita pediu para aguardar (consulta em excesso). Tente novamente "
                "em cerca de 1 hora — o que já foi baixado está salvo.")
        max_nsu = int(ret.findtext(f"{NFE}maxNSU") or 0)
        ult_ret = int(ret.findtext(f"{NFE}ultNSU") or ult)

        lote = ret.find(f"{NFE}loteDistDFeInt")
        if lote is not None:
            for dz in lote.findall(f"{NFE}docZip"):
                nsu = dz.get("NSU") or "0"
                schema = (dz.get("schema") or "doc").split("_")[0]
                try:
                    xml = gzip.decompress(base64.b64decode(dz.text)).decode("utf-8", "ignore")
                except Exception:
                    continue
                (pasta / f"{nsu}-{schema}.xml").write_text(xml, encoding="utf-8")
                novos += 1

        ult = ult_ret
        estado_arq.write_text(json.dumps({"ultNSU": ult, "maxNSU": max_nsu}), encoding="utf-8")
        if progresso:
            progresso(ult, max_nsu, novos)
        if cstat == "137" or ult >= max_nsu:   # 137 = nenhum documento; ou chegou ao fim
            break
        time.sleep(0.6)   # cortesia contra rejeição por consumo indevido

    return {"novos": novos, "ultNSU": ult}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _nota_de_infnfe(inf, cnpj, arquivo, origem) -> dict:
    """Extrai uma nota de um <infNFe> (serve para NF-e mod 55 e NFCe mod 65)."""
    ch = (inf.get("Id") or "").replace("NFe", "")
    emit = inf.find(f"{NFE}emit")
    dest = inf.find(f"{NFE}dest")
    transp = inf.find(f".//{NFE}transporta")
    emit_cnpj = emit.findtext(f"{NFE}CNPJ") if emit is not None else None
    dest_cnpj = dest.findtext(f"{NFE}CNPJ") if dest is not None else None
    transp_cnpj = transp.findtext(f"{NFE}CNPJ") if transp is not None else None
    modelo = inf.findtext(f"{NFE}ide/{NFE}mod") or "55"
    dest_nome = dest.findtext(f"{NFE}xNome") if dest is not None else None
    return {
        "chave": ch,
        "modelo": modelo,                                  # 55 = NF-e, 65 = NFCe
        "emit_cnpj": emit_cnpj,
        "emit_nome": emit.findtext(f"{NFE}xNome") if emit is not None else None,
        "dest_cnpj": dest_cnpj,
        "dest_nome": dest_nome,
        "transp_cnpj": transp_cnpj,
        "valor": _num(inf.findtext(f".//{NFE}ICMSTot/{NFE}vNF")),
        "data": (inf.findtext(f"{NFE}ide/{NFE}dhEmi") or "")[:10],
        "numero": inf.findtext(f"{NFE}ide/{NFE}nNF"),
        "completa": True, "origem": origem, "arquivo": arquivo,
    }


def _dig(v) -> str:
    """Só os dígitos — compara CNPJ sem depender de pontuação/zeros à esquerda perdidos."""
    return re.sub(r"\D", "", str(v or ""))


def _classificar_papel(n: dict, cnpj: str) -> str:
    """De que a empresa participa nesta nota?
      compra   – é a destinatária (comprou)                → entra na apuração de compras
      venda    – é a emitente de uma NF-e 55 (vendeu)
      nfce     – é a emitente de uma NFCe 65 (venda ao consumidor)
      frete    – aparece só como transportadora (nota de TERCEIROS) → NÃO é compra dela
      outra    – não identificada
    Sem isto, as notas de frete entram como 'compra' e inflam o total (uma
    transportadora recebe milhares de notas em que é só a transportadora)."""
    cnpj = _dig(cnpj)
    if not n.get("completa"):
        return "compra"                                    # resumo (resNFe) = sempre recebida
    if _dig(n.get("emit_cnpj")) == cnpj:
        return "nfce" if n.get("modelo") == "65" else "venda"
    if _dig(n.get("dest_cnpj")) == cnpj:
        return "compra"
    if _dig(n.get("transp_cnpj")) == cnpj:
        return "frete"
    return "outra"


def _eventos_cancelamento(pasta) -> set:
    canceladas = set()
    for padrao in ("*-resEvento.xml", "*-procEventoNFe.xml"):
        for f in pasta.glob(padrao):
            try:
                d = ET.fromstring(f.read_text("utf-8"))
                if (d.findtext(f".//{NFE}tpEvento") or "") == "110111":  # cancelamento
                    ch = d.findtext(f".//{NFE}chNFe")
                    if ch:
                        canceladas.add(ch)
            except Exception:
                pass
    return canceladas


def carregar_nfe(pasta_cnpj, cnpj) -> list[dict]:
    """Lê NF-e/NFCe da empresa: as RECEBIDAS (distribuição nacional, pasta 'nfe')
    e as IMPORTADAS pelo usuário (vendas e NFCe, pasta 'nfe_importadas')."""
    pasta = Path(pasta_cnpj) / "nfe"
    importadas = Path(pasta_cnpj) / "nfe_importadas"
    notas: dict[str, dict] = {}
    canceladas = set()

    if pasta.exists():
        canceladas |= _eventos_cancelamento(pasta)
        # resumos primeiro; a procNFe (completa) sobrescreve
        for f in pasta.glob("*-resNFe.xml"):
            try:
                d = ET.fromstring(f.read_text("utf-8"))
            except Exception:
                continue
            ch = d.findtext(f".//{NFE}chNFe")
            if not ch:
                continue
            notas.setdefault(ch, {
                "chave": ch, "modelo": "55",
                "emit_cnpj": d.findtext(f".//{NFE}CNPJ"),
                "emit_nome": d.findtext(f".//{NFE}xNome"),
                "valor": _num(d.findtext(f".//{NFE}vNF")),
                "data": (d.findtext(f".//{NFE}dhEmi") or "")[:10],
                "numero": None, "completa": False, "origem": "distribuida", "arquivo": f.name,
            })
        for f in pasta.glob("*-procNFe.xml"):
            try:
                inf = ET.fromstring(f.read_text("utf-8")).find(f".//{NFE}infNFe")
            except Exception:
                continue
            if inf is not None:
                n = _nota_de_infnfe(inf, cnpj, f.name, "distribuida")
                notas[n["chave"]] = n

    if importadas.exists():
        canceladas |= _eventos_cancelamento(importadas)
        for f in importadas.glob("*.xml"):
            try:
                inf = ET.fromstring(f.read_text("utf-8", "ignore")).find(f".//{NFE}infNFe")
            except Exception:
                continue
            if inf is not None:
                n = _nota_de_infnfe(inf, cnpj, f.name, "importada")
                notas.setdefault(n["chave"], n)   # não sobrescreve a distribuída

    out = []
    for ch, n in notas.items():
        n["cancelada"] = ch in canceladas
        n["papel"] = _classificar_papel(n, cnpj)
        out.append(n)
    out.sort(key=lambda x: x.get("data") or "", reverse=True)
    return out


def carregar_cte(pasta_cnpj, cnpj) -> list[dict]:
    """Lê os CT-e (mod 57) guardados na pasta 'cte' da empresa.
    papel: 'emitido' (a empresa é a transportadora) ou 'tomado' (pagou o frete)."""
    pasta = Path(pasta_cnpj) / "cte"
    if not pasta.exists():
        return []
    cnpj_d = _dig(cnpj)
    out = []
    for f in pasta.glob("*.xml"):
        try:
            inf = ET.fromstring(f.read_text("utf-8", "ignore")).find(f".//{CTE}infCte")
        except Exception:
            continue
        if inf is None:
            continue
        emit = _dig(inf.findtext(f"{CTE}emit/{CTE}CNPJ"))
        toma = _dig(inf.findtext(f".//{CTE}toma4/{CTE}CNPJ"))
        # CST do ICMS: fica dentro do grupo escolhido (ICMS00/ICMS45/ICMS60/...).
        # Sem isso, um CT-e isento (ICMS45/CST 40) aparece com "R$ 0,00" sem explicar.
        grupo_icms = inf.find(f".//{CTE}imp/{CTE}ICMS")
        cst_cte = ""
        if grupo_icms is not None:
            for filho in grupo_icms:
                cst_cte = (filho.findtext(f"{CTE}CST") or "").strip()
                if cst_cte:
                    break
        rem = _dig(inf.findtext(f"{CTE}rem/{CTE}CNPJ"))
        dest = _dig(inf.findtext(f"{CTE}dest/{CTE}CNPJ"))
        out.append({
            "chave": (inf.get("Id") or "").replace("CTe", ""),
            "numero": inf.findtext(f"{CTE}ide/{CTE}nCT"),
            "data": (inf.findtext(f"{CTE}ide/{CTE}dhEmi") or "")[:10],
            "emit_cnpj": emit,
            "emit_nome": inf.findtext(f"{CTE}emit/{CTE}xNome") or "",
            "dest_nome": inf.findtext(f"{CTE}dest/{CTE}xNome") or "",
            "rem_nome": inf.findtext(f"{CTE}rem/{CTE}xNome") or "",
            "cfop": inf.findtext(f"{CTE}ide/{CTE}CFOP") or "",
            "valor": _num(inf.findtext(f".//{CTE}vPrest/{CTE}vTPrest")),
            "vicms": _num(inf.findtext(f".//{CTE}ICMS//{CTE}vICMS")),
            "cst": cst_cte,
            "papel": "emitido" if emit == cnpj_d else "tomado",
            "toma_cnpj": toma, "rem_cnpj": rem, "dest_cnpj": dest,
            "arquivo": f.name,
        })
    out.sort(key=lambda x: x.get("data") or "", reverse=True)
    return out


def importar_xmls(pasta_cnpj, cnpj, arquivos) -> dict:
    """Guarda XMLs de NF-e/NFCe informados pelo usuário e alimenta o ACERVO.

    `arquivos` = lista de (nome, conteudo_bytes). Devolve um relatório detalhado
    (quantos de cada tipo, e o PORQUÊ dos ignorados — para o usuário entender
    quando 'não reconheceu').

    QUEM MANDA, DESDE A APURAÇÃO 6B
        O acervo. A pasta `nfe_importadas` continua sendo escrita — ninguém
        perde arquivo numa virada de fase —, mas ela passou a ser **caixa de
        entrada**, não fonte da verdade. A validação de verdade e a gravação
        que a apuração enxerga acontecem em `ingestao/importacao.py`, o portão
        único, e este relatório ganhou as chaves `acervo_*` para mostrar isso.

        As chaves antigas continuam todas aqui, com o mesmo significado: a tela
        não precisa saber que o miolo mudou.
    """
    from ingestao import importacao as _imp

    destino = Path(pasta_cnpj) / "nfe_importadas"
    destino.mkdir(parents=True, exist_ok=True)
    r = {"venda": 0, "nfce": 0, "compra": 0, "frete": 0, "outra": 0,
         "cte_emitido": 0, "cte_tomado": 0,
         "repetidos": 0, "nfse": 0, "invalidos": 0, "outros_emitentes": []}
    outros = set()
    # O que segue para o portão: NF-e e eventos de NF-e. CT-e e NFS-e têm
    # destino próprio e não são desta porta.
    para_o_acervo: list = []
    for nome, conteudo in arquivos:
        try:
            texto = conteudo.decode("utf-8", "ignore") if isinstance(conteudo, bytes) else str(conteudo)
            raiz = ET.fromstring(texto)
        except Exception:
            r["invalidos"] += 1
            continue
        inf = raiz.find(f".//{NFE}infNFe")
        if inf is None:
            # CT-e (mod 57)? guarda na pasta 'cte' (emitido pela empresa × frete tomado)
            infcte = raiz.find(f".//{CTE}infCte")
            if infcte is not None:
                ch = (infcte.get("Id") or "").replace("CTe", "") or nome
                emit_cte = _dig(infcte.findtext(f"{CTE}emit/{CTE}CNPJ"))
                pasta_cte = Path(pasta_cnpj) / "cte"
                pasta_cte.mkdir(parents=True, exist_ok=True)
                arqc = pasta_cte / f"imp-{ch}.xml"
                if arqc.exists():
                    r["repetidos"] += 1
                    continue
                arqc.write_text(texto, encoding="utf-8")
                papel_cte = "cte_emitido" if emit_cte == _dig(cnpj) else "cte_tomado"
                r[papel_cte] += 1
                continue
            # evento de cancelamento de NF-e?
            if raiz.find(f".//{NFE}tpEvento") is not None:
                ch = raiz.findtext(f".//{NFE}chNFe") or "evento"
                (destino / f"imp-{ch}-procEventoNFe.xml").write_text(texto, encoding="utf-8")
                # O evento importa tanto quanto a nota: sem ele, uma venda
                # cancelada conta como receita para sempre, em silêncio.
                para_o_acervo.append((nome, texto.encode("utf-8")))
                continue
            # é NFS-e (serviço)? — nome de tag revela; ajuda o usuário a não confundir módulo
            tag = raiz.tag.lower()
            corpo = texto[:800].lower()
            if ("nfse" in tag or "abrasf" in corpo or "sped.fazenda.gov.br/nfse" in corpo
                    or "consultarnfseresposta" in tag):
                r["nfse"] += 1
            else:
                r["invalidos"] += 1
            continue
        n = _nota_de_infnfe(inf, cnpj, "", "importada")
        arq = destino / f"imp-{n['chave']}.xml"
        if arq.exists():
            r["repetidos"] += 1
            continue
        papel = _classificar_papel(n, cnpj)
        # nota de OUTRO emitente (a empresa não é emitente, nem destinatária, nem
        # transportadora): NÃO grava sob esta empresa — seria dado errado. Só reporta,
        # para o usuário ver que escolheu a empresa errada ou o XML é de outra.
        if papel == "outra":
            if _dig(n.get("emit_cnpj")) and _dig(n.get("emit_cnpj")) != _dig(cnpj):
                outros.add(n.get("emit_cnpj"))
            r["outra"] += 1
            continue
        arq.write_text(texto, encoding="utf-8")
        r[papel] = r.get(papel, 0) + 1
        para_o_acervo.append((nome, texto.encode("utf-8")))

    # ── o portão único ────────────────────────────────────────────────────
    # `pasta_cnpj` é `<dados>/<cnpj>`; o acervo trabalha a partir da raiz.
    # `painel()` É o portão — ele chama `importar()` por dentro e ainda lê o
    # que entrou. Uma passagem só: chamar os dois faria o segundo ver tudo
    # como duplicata e mentir no relatório.
    q = _imp.painel(Path(pasta_cnpj).parent, cnpj, para_o_acervo,
                    recebidos=len(arquivos or []))
    r["acervo_novos"] = q["preservados"]
    r["acervo_duplicados"] = q["duplicados"]
    r["acervo_rejeitados"] = q["rejeitados"]
    r["acervo_por_motivo"] = {m["motivo"]: m["quantidade"]
                              for m in q["rejeitados_por_motivo"]}
    r["acervo_emissao_propria"] = q["nfe_proprias_validas"]
    r["painel"] = q
    r["outros_emitentes"] = sorted(outros)
    return r


def _cnpj_da_chave(chave) -> str:
    """A chave de acesso de 44 dígitos traz o CNPJ do emitente nas posições 7–20."""
    d = _dig(chave)
    return d[6:20] if len(d) == 44 else ""


def _peek(texto):
    """Lê o mínimo de um XML para ROTEAR: emit/dest/modelo, ou evento (pela chave)."""
    try:
        raiz = ET.fromstring(texto)
    except Exception:
        return None
    inf = raiz.find(f".//{NFE}infNFe")
    if inf is not None:
        return {"tipo": "nota",
                "emit": inf.findtext(f"{NFE}emit/{NFE}CNPJ"),
                "dest": inf.findtext(f"{NFE}dest/{NFE}CNPJ"),
                "modelo": inf.findtext(f"{NFE}ide/{NFE}mod") or "55"}
    infcte = raiz.find(f".//{CTE}infCte")               # CT-e (frete, mod 57)
    if infcte is not None:
        return {"tipo": "cte",
                "emit": infcte.findtext(f"{CTE}emit/{CTE}CNPJ"),
                "rem": infcte.findtext(f"{CTE}rem/{CTE}CNPJ"),
                "dest": infcte.findtext(f"{CTE}dest/{CTE}CNPJ"),
                "toma": infcte.findtext(f".//{CTE}toma4/{CTE}CNPJ"),
                "modelo": "57"}
    if raiz.find(f".//{NFE}tpEvento") is not None:      # cancelamento etc.
        return {"tipo": "evento", "chave": raiz.findtext(f".//{NFE}chNFe")}
    low = texto[:800].lower()
    if "nfse" in low or "abrasf" in low or "sped.fazenda.gov.br/nfse" in low:
        # NFS-e: descobre o CNPJ do PRESTADOR para arquivar na empresa certa.
        # No layout nacional o <emit> aninha o tomador, então usa filhos DIRETOS
        # (mesmo cuidado do módulo de inscrições).
        prest = ""
        def _ln(t):
            return t.rsplit("}", 1)[-1]
        for el in raiz.iter():
            nome_tag = _ln(el.tag)
            if nome_tag in ("prest", "IdentificacaoPrestador", "PrestadorServico", "DadosPrestador", "Prestador"):
                for x in el.iter():
                    if _ln(x.tag) in ("CNPJ", "Cnpj") and _dig(x.text):
                        prest = _dig(x.text)
                        break
            elif nome_tag == "emit" and not prest:
                for x in list(el):          # só filhos diretos
                    if _ln(x.tag) in ("CNPJ", "Cnpj") and _dig(x.text):
                        prest = _dig(x.text)
                        break
            if prest:
                break
        return {"tipo": "nfse", "prest": prest}
    return None


def distribuir_entrada(cnpjs_conhecidos, pasta_de, arquivos) -> dict:
    """AGREGADOR: recebe XMLs de VÁRIAS empresas (misturados) e arquiva cada um
    na empresa dona — pelo CNPJ do EMITENTE (se for cliente nosso); senão tenta o
    destinatário. Assim o escritório tem UM só ponto de entrada.
      cnpjs_conhecidos : CNPJs (qualquer formato) que temos cadastrados
      pasta_de(cnpj)   : função que devolve a pasta da empresa
      arquivos         : lista de (nome, bytes)"""
    conhecidos = {_dig(c) for c in cnpjs_conhecidos if _dig(c)}
    por_empresa: dict[str, list] = {}
    rel = {"total": len(arquivos), "nfse": 0, "invalidos": 0,
           "nao_identificados": [], "por_empresa": {}}
    nao_id = set()
    for nome, conteudo in arquivos:
        texto = conteudo.decode("utf-8", "ignore") if isinstance(conteudo, bytes) else str(conteudo)
        info = _peek(texto)
        if not info:
            rel["invalidos"] += 1
            continue
        if info["tipo"] == "nfse":
            rel["nfse"] += 1
            continue
        if info["tipo"] == "evento":
            candidatos = [_cnpj_da_chave(info.get("chave"))]
        elif info["tipo"] == "cte":                     # CT-e: emitente, tomador, dest, remetente
            candidatos = [_dig(info.get(k)) for k in ("emit", "toma", "dest", "rem")]
        else:
            candidatos = [_dig(info.get("emit")), _dig(info.get("dest"))]
        alvo = next((c for c in candidatos if c and c in conhecidos), None)
        if not alvo:
            primeiro = next((c for c in candidatos if c), "")
            if primeiro:
                nao_id.add(primeiro)
            else:
                rel["invalidos"] += 1
            continue
        por_empresa.setdefault(alvo, []).append((nome, conteudo))
    for cnpj, arqs in por_empresa.items():
        r = importar_xmls(pasta_de(cnpj), cnpj, arqs)
        rel["por_empresa"][cnpj] = {k: r.get(k, 0) for k in
                                    ("venda", "nfce", "compra", "frete",
                                     "cte_emitido", "cte_tomado", "repetidos")}
    rel["nao_identificados"] = sorted(nao_id)
    return rel


def _mover(f: Path, destino_dir: Path):
    """Move o arquivo processado para uma subpasta (não reprocessar + rastro)."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    alvo = destino_dir / f.name
    i = 1
    while alvo.exists():
        alvo = destino_dir / f"{f.stem}({i}){f.suffix}"
        i += 1
    try:
        f.replace(alvo)
    except Exception:
        pass


# subpastas onde os XML vão parar depois de processados
_SUBS = ("_processados", "_nfse", "_nao_identificados", "_invalidos", "_pdf")


def _candidatos_do_info(info) -> list:
    """CNPJs que podem ser o dono do documento, na ordem de preferência."""
    if info["tipo"] == "evento":
        return [_cnpj_da_chave(info.get("chave"))]
    if info["tipo"] == "cte":
        return [_dig(info.get(k)) for k in ("emit", "toma", "dest", "rem")]
    if info["tipo"] == "nfse":
        return [_dig(info.get("prest"))]
    return [_dig(info.get("emit")), _dig(info.get("dest"))]


def _guardar_nfse(pasta_cnpj, nome, texto) -> bool:
    """Salva uma NFS-e na pasta 'xmls' da empresa — é onde o módulo Serviços lê."""
    try:
        destino = Path(pasta_cnpj) / "xmls"
        destino.mkdir(parents=True, exist_ok=True)
        base = re.sub(r"[^0-9A-Za-z_.-]", "_", nome)
        if not base.startswith("nfse-"):
            base = "nfse-" + base
        if not base.endswith(".xml"):
            base += ".xml"
        alvo = destino / base
        if alvo.exists():
            return False
        alvo.write_text(texto, encoding="utf-8")
        return True
    except Exception:
        return False


def _processar_documento(nome, dados: bytes, conhecidos, pasta_de, rel, nao_id) -> str:
    """Roteia UM documento (bytes). Devolve o resultado:
    'ok' | 'nfse' | 'nao_identificado' | 'invalido'."""
    try:
        texto = dados.decode("utf-8", "ignore")
    except Exception:
        return "invalido"
    info = _peek(texto)
    if not info:
        return "invalido"
    alvo = next((c for c in _candidatos_do_info(info) if c and c in conhecidos), None)
    if not alvo:
        primeiro = next((c for c in _candidatos_do_info(info) if c), "")
        if primeiro:
            nao_id.add(primeiro)
        return "nao_identificado"
    if info["tipo"] == "nfse":
        if _guardar_nfse(pasta_de(alvo), nome, texto):
            rel["nfse_importadas"] = rel.get("nfse_importadas", 0) + 1
        rel["por_empresa"][alvo] = rel["por_empresa"].get(alvo, 0) + 1
        return "ok"
    importar_xmls(pasta_de(alvo), alvo, [(nome, dados)])
    rel["por_empresa"][alvo] = rel["por_empresa"].get(alvo, 0) + 1
    return "ok"


def processar_pasta(pasta, cnpjs_conhecidos, pasta_de, mover_desconhecidos=False) -> dict:
    """PASTA VIGIADA: lê o que estiver solto em `pasta` — .xml, .zip (pasta
    compactada) e .pdf — arquiva cada documento na empresa dona e MOVE o arquivo
    para uma subpasta conforme o resultado. Serve para apontar direto para a
    pasta Downloads: NF-e, NFC-e, CT-e e NFS-e vão cada um para o seu lugar.

    PDF: é só a representação gráfica (DANFE/DACTE) — NÃO tem os dados fiscais
    estruturados. Quando dá para identificar a empresa pela chave de 44 dígitos
    no nome do arquivo, o PDF é guardado na pasta 'pdfs' dela; ele NÃO entra na
    apuração (isso só o XML faz).

    `mover_desconhecidos=False` (padrão): o que NÃO for reconhecido como documento
    fiscal fica exatamente onde está. É o que torna seguro apontar para a pasta
    Downloads — um zip de fotos ou um boleto em PDF não é tocado."""
    import zipfile as _zip

    pasta = Path(pasta)
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except Exception:
        return {"erro": f"Não foi possível acessar a pasta: {pasta}"}
    conhecidos = {_dig(c) for c in cnpjs_conhecidos if _dig(c)}
    rel = {"total": 0, "nfse": 0, "nfse_importadas": 0, "invalidos": 0,
           "zips": 0, "pdfs": 0, "pdfs_sem_dono": 0,
           "nao_identificados": [], "por_empresa": {}}
    nao_id = set()

    def _conta(res):
        if res == "invalido":
            rel["invalidos"] += 1
        elif res == "nao_identificado":
            rel["nao_identificados_qtd"] = rel.get("nao_identificados_qtd", 0) + 1

    for f in sorted(pasta.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in (".xml", ".zip", ".pdf"):
            continue
        rel["total"] += 1

        if ext == ".pdf":
            # identifica pela chave de 44 dígitos no NOME (padrão dos downloads)
            m = re.search(r"\d{44}", f.name)
            dono = _cnpj_da_chave(m.group()) if m else ""
            if dono and dono in conhecidos:
                try:
                    destino = Path(pasta_de(dono)) / "pdfs"
                    destino.mkdir(parents=True, exist_ok=True)
                    _mover(f, destino)
                    rel["pdfs"] += 1
                except Exception:
                    rel["pdfs_sem_dono"] += 1
            else:
                # PDF sem chave reconhecida pode ser um documento pessoal: não mexe
                rel["pdfs_sem_dono"] += 1
                if mover_desconhecidos:
                    _mover(f, pasta / "_pdf")
            continue

        if ext == ".zip":
            achou = False
            try:
                with _zip.ZipFile(f) as z:
                    for n in z.namelist():
                        if not n.lower().endswith(".xml") or n.endswith("/"):
                            continue
                        achou = True
                        res = _processar_documento(n.split("/")[-1], z.read(n),
                                                   conhecidos, pasta_de, rel, nao_id)
                        _conta(res)
            except Exception:
                rel["invalidos"] += 1
                if mover_desconhecidos:
                    _mover(f, pasta / "_invalidos")
                continue
            if achou:
                rel["zips"] += 1
                _mover(f, pasta / "_processados")
            else:
                rel["invalidos"] += 1        # zip sem XML: provavelmente não é fiscal
                if mover_desconhecidos:
                    _mover(f, pasta / "_invalidos")
            continue

        # .xml solto
        try:
            dados = f.read_bytes()
        except Exception:
            rel["invalidos"] += 1
            continue
        res = _processar_documento(f.name, dados, conhecidos, pasta_de, rel, nao_id)
        _conta(res)
        if res == "ok":
            _mover(f, pasta / "_processados")
        elif res == "nfse":
            rel["nfse"] += 1
            _mover(f, pasta / "_nfse")
        elif res == "nao_identificado":
            # é nota fiscal, mas de empresa não cadastrada: move só se autorizado
            if mover_desconhecidos:
                _mover(f, pasta / "_nao_identificados")
        elif mover_desconhecidos:
            _mover(f, pasta / "_invalidos")
    rel["nao_identificados"] = sorted(nao_id)
    return rel
