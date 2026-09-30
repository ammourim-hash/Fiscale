"""
parsers.py — leitura DETERMINÍSTICA do XML. Sem rede, sem relógio, sem IA.

O CONTRATO
    Mesma entrada, mesma saída. Sempre. Um parser que consulte a rede, olhe o
    relógio ou peça opinião a um modelo não é reprocessável — e reprocessar é a
    razão de o acervo existir.

    Campo que o XML traz estruturado — CNPJ, chave, número, série, data, CFOP,
    NCM, CST, base, alíquota, valor — sai daqui e de lugar nenhum mais. A IA não
    é consultada sobre eles, e se um dia opinar, a opinião é descartada.

VERSÃO DO PARSER
    `VERSAO` sobe quando a LEITURA muda: campo novo, correção de interpretação,
    tag que passou a ser considerada. O índice guarda com que versão cada
    documento foi lido, e o reprocessamento usa isso para saber o que reler —
    **do disco, nunca do portal**.

    Correção de estilo ou refatoração não sobem a versão: se a saída não muda,
    não há o que reprocessar.

AUSENTE ≠ ZERO, EM TODA PARTE
    `_texto()` devolve `""` para tag ausente e `dinheiro()` transforma `""` em
    `None`. Em nenhum ponto deste arquivo um campo ausente vira `Decimal("0")`.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from xml.etree import ElementTree as ET

from . import documento as dm
from . import identificacao as idf
from .identidade import normalizar

VERSAO_NFE55 = 4
#   2 — papéis reais: `transporta` e `autXML` passaram a ser lidos (NF-e 4A)
#   3 — itens enriquecidos: CST separado de CSOSN, origem, desconto,
#       ICMS completo (ST, FCP, diferimento, desoneração) e os grupos
#       IBS/CBS/IS, que já chegam nos XML de hoje (NF-e 4B)
#   4 — `dhSaiEnt`: a data de SAÍDA/ENTRADA, que é campo próprio do `ide` e
#       vinha sendo ignorada por três fases. Ela NÃO substitui `dhEmi` em lugar
#       nenhum — são perguntas diferentes, e o acervo responde as duas em 99,5%
#       dos casos (NF-e 6)
#
# Subir esta versão é o que faz `indice.desatualizados()` marcar os documentos
# para reprocessamento: o acervo não muda, o índice se refaz.
VERSAO_EVENTO = 3   # 2: leu `resEvento` (15/08); 3: cOrgao na identidade (16/08)

# CT-e (CTE 1). Comecam em 1: nao ha indice em disco com CT-e para
# reprocessar, entao nao ha versao anterior de onde subir.
VERSAO_CTE57 = 3   # 2: ExtensaoCTe no índice · 3: carga só com DV válido
VERSAO_EVENTO_CTE = 1

_NS = re.compile(r"^\{[^}]*\}")

# tpEvento → descrição. Só os que a NF-e usa de fato.
DESCRICAO_EVENTO = {
    "110110": "Carta de Correção",
    "110111": "Cancelamento",
    "110112": "Cancelamento por substituição",
    "210200": "Confirmação da Operação",
    "210210": "Ciência da Operação",
    "210220": "Desconhecimento da Operação",
    "210240": "Operação não Realizada",
}


class ErroParser(Exception):
    """O documento não pôde ser lido. O original permanece intocado."""


def _local(tag: str) -> str:
    return _NS.sub("", tag or "")


def _filhos(el, nome: str):
    return [f for f in el if _local(f.tag) == nome]


def _achar(el, *caminho: str):
    """Desce pelo caminho de tags, ignorando namespace. `None` se não achar."""
    atual = el
    for nome in caminho:
        achou = None
        for f in atual:
            if _local(f.tag) == nome:
                achou = f
                break
        if achou is None:
            return None
        atual = achou
    return atual


def _fundo(el, nome: str):
    """O texto de `nome` em qualquer profundidade sob `el`, ou `None`.

    `None` é "não existe"; `""` seria "existe e veio vazio". No evento de
    cancelamento essa diferença é a que separa "sem justificativa" de
    "justificativa em branco"."""
    if el is None:
        return None
    for filho in el.iter():
        if _local(filho.tag) == nome:
            t = (filho.text or "").strip()
            return t or None
    return None


def _primeiro(raiz, nome: str):
    for el in raiz.iter():
        if _local(el.tag) == nome:
            return el
    return None


def _texto(el, *caminho: str) -> str:
    """Texto da tag, ou `""` se ela não existir. Nunca inventa valor."""
    alvo = _achar(el, *caminho) if caminho else el
    if alvo is None:
        return ""
    return (alvo.text or "").strip()


def _valor(el, *caminho: str):
    """Valor monetário: `Decimal` quando informado, `None` quando ausente."""
    return dm.dinheiro(_texto(el, *caminho), campo=caminho[-1] if caminho else "")


def _data_hora(txt: str) -> datetime | None:
    """`dhEmi` no formato da NF-e 4.00 (ISO com fuso) ou `dEmi` (só data).

    Devolve `None` em vez de levantar: data ilegível não pode impedir a
    preservação do resto do documento."""
    s = (txt or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for formato in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if "T" in s else s[:10], formato)
        except ValueError:
            continue
    return None


def _participante(el) -> dm.Participante:
    """Emitente ou destinatário. Aceita CNPJ ou CPF — autônomo emite com CPF."""
    if el is None:
        return dm.Participante()
    bruto = _texto(el, "CNPJ") or _texto(el, "CPF")
    ident = normalizar(bruto)
    ender = _achar(el, "enderEmit") or _achar(el, "enderDest")
    return dm.Participante(
        identificador=ident.valor if ident.valido else idf.digitos_da_chave(bruto),
        tipo=ident.tipo,
        nome=_texto(el, "xNome"),
        ie=_texto(el, "IE"),
        uf=_texto(ender, "UF") if ender is not None else "",
        municipio=_texto(ender, "xMun") if ender is not None else "",
        codigo_municipio=_texto(ender, "cMun") if ender is not None else "",
    )


def _subgrupo(imposto, grupo: str):
    """O subgrupo real de um tributo do item.

    O ICMS vem dentro de um subgrupo cujo nome varia com a tributação
    (`ICMS00`, `ICMS60`, `ICMSSN101`…). Em vez de listar todos — e esquecer um
    quando a legislação mudar —, procura-se o primeiro filho do grupo."""
    if imposto is None:
        return None
    bloco = _achar(imposto, grupo)
    if bloco is None:
        return None
    # O PRIMEIRO FILHO nem sempre é o subgrupo tributado: o grupo `IPI` começa
    # com `<cEnq>`, e só depois vem `<IPINT>`/`<IPITrib>`. Pegar `bloco[0]` às
    # cegas fazia o CST do IPI sair vazio em todo documento que declara
    # enquadramento — que são praticamente todos.
    for filho in bloco:
        if _local(filho.tag).startswith(grupo):
            return filho
    return bloco


def _tributo_do_item(imposto, grupo: str) -> dm.Tributo:
    """IPI, PIS e COFINS de um item. O ICMS tem função própria.

    CST e CSOSN vão para campos SEPARADOS. Colapsá-los num só era o que fazia
    `102` chegar ao índice sem dizer se era CST ou CSOSN — mesmo número, dois
    significados, conclusões fiscais opostas.

    PIS e COFINS têm modalidade por QUANTIDADE (`PISQtde`), com alíquota em
    reais por unidade em vez de percentual. Os dois convivem em campos
    próprios: espremer a modalidade específica na fórmula percentual daria
    número errado, calado.
    """
    alvo = _subgrupo(imposto, grupo)
    if alvo is None:
        return dm.Tributo()
    grupo_pai = _achar(imposto, grupo)
    return dm.Tributo(
        base=_valor(alvo, "vBC"),
        aliquota=(_valor(alvo, "pIPI") or _valor(alvo, "pPIS")
                  or _valor(alvo, "pCOFINS")),
        valor=(_valor(alvo, "vIPI") or _valor(alvo, "vPIS")
               or _valor(alvo, "vCOFINS")),
        cst=_texto(alvo, "CST"),
        csosn=_texto(alvo, "CSOSN"),
        origem=_texto(alvo, "orig"),
        quantidade_base=_valor(alvo, "qBCProd"),
        aliquota_por_unidade=_valor(alvo, "vAliqProd"),
        # `cEnq` é do grupo IPI, não do subgrupo tributado.
        enquadramento=(_texto(grupo_pai, "cEnq")
                       if grupo == "IPI" and grupo_pai is not None else ""),
    )


def _icms_do_item(imposto) -> dm.TributoIcms:
    """O ICMS inteiro: próprio, ST, FCP, diferimento e desoneração.

    Ler só base/alíquota/valor perderia o que distingue uma operação normal de
    uma com substituição — e é essa distinção que decide se há crédito. Nada é
    calculado: lê-se o que o documento traz, e o que ele não traz fica `None`.
    """
    alvo = _subgrupo(imposto, "ICMS")
    if alvo is None:
        return dm.TributoIcms()
    return dm.TributoIcms(
        cst=_texto(alvo, "CST"), csosn=_texto(alvo, "CSOSN"),
        origem=_texto(alvo, "orig"), modalidade_base=_texto(alvo, "modBC"),
        base=_valor(alvo, "vBC"), aliquota=_valor(alvo, "pICMS"),
        valor=_valor(alvo, "vICMS"), reducao_base=_valor(alvo, "pRedBC"),
        base_st=_valor(alvo, "vBCST"), aliquota_st=_valor(alvo, "pICMSST"),
        valor_st=_valor(alvo, "vICMSST"),
        reducao_base_st=_valor(alvo, "pRedBCST"),
        margem_st=_valor(alvo, "pMVAST"),
        base_fcp=_valor(alvo, "vBCFCP"), aliquota_fcp=_valor(alvo, "pFCP"),
        valor_fcp=_valor(alvo, "vFCP"),
        aliquota_diferimento=_valor(alvo, "pDif"),
        valor_diferido=_valor(alvo, "vICMSDif"),
        valor_desonerado=_valor(alvo, "vICMSDeson"),
        motivo_desoneracao=_texto(alvo, "motDesICMS"),
    )


def _reforma_do_item(imposto) -> dm.TributoReforma:
    """IBS, CBS e Imposto Seletivo, quando o documento os traz.

    A estrutura oficial, conferida no acervo real em 23/08/2026:

        <IBSCBS><CST>000</CST><cClassTrib>000001</cClassTrib>
          <gIBSCBS><vBC>1062.92</vBC>
            <gIBSUF><pIBSUF>0.10</pIBSUF><vIBSUF>1.06</vIBSUF></gIBSUF>
            <gIBSMun><pIBSMun>0.00</pIBSMun><vIBSMun>0.00</vIBSMun></gIBSMun>
            <vIBS>1.06</vIBS>
            <gCBS><pCBS>0.90</pCBS><vCBS>9.57</vCBS></gCBS>
          </gIBSCBS></IBSCBS>

    Documento sem esses grupos devolve tudo `None`, e é assim que tem de ser:
    ausente não é zero, e inventar zero para XML antigo faria parecer que a
    Reforma foi apurada e deu nada.
    """
    if imposto is None:
        return dm.TributoReforma()
    bloco = _achar(imposto, "IBSCBS")
    sel = _achar(imposto, "IS")
    if bloco is None and sel is None:
        return dm.TributoReforma()

    g = _achar(bloco, "gIBSCBS") if bloco is not None else None
    uf = _achar(g, "gIBSUF") if g is not None else None
    mun = _achar(g, "gIBSMun") if g is not None else None
    cbs = _achar(g, "gCBS") if g is not None else None
    return dm.TributoReforma(
        cst=_texto(bloco, "CST") if bloco is not None else "",
        classificacao_tributaria=(_texto(bloco, "cClassTrib")
                                  if bloco is not None else ""),
        base=_valor(g, "vBC") if g is not None else None,
        aliquota_ibs_uf=_valor(uf, "pIBSUF") if uf is not None else None,
        valor_ibs_uf=_valor(uf, "vIBSUF") if uf is not None else None,
        aliquota_ibs_mun=_valor(mun, "pIBSMun") if mun is not None else None,
        valor_ibs_mun=_valor(mun, "vIBSMun") if mun is not None else None,
        valor_ibs=_valor(g, "vIBS") if g is not None else None,
        aliquota_cbs=_valor(cbs, "pCBS") if cbs is not None else None,
        valor_cbs=_valor(cbs, "vCBS") if cbs is not None else None,
        cst_is=_texto(sel, "CSTIS") if sel is not None else "",
        base_is=_valor(sel, "vBCIS") if sel is not None else None,
        aliquota_is=_valor(sel, "pIS") if sel is not None else None,
        valor_is=_valor(sel, "vIS") if sel is not None else None,
    )


# ── NF-e modelo 55 ──────────────────────────────────────────────────────────
class ParserNFe55:
    """Lê `nfeProc`, `NFe` e `resNFe`.

    `resNFe` é o RESUMO da Distribuição DF-e: traz emitente, valor e situação,
    e não traz itens. É documento legítimo e precisa ser lido — a maior parte do
    que chega pela distribuição começa assim."""

    tipo = dm.NFE55
    nome = "nfe55"
    VERSAO = VERSAO_NFE55

    def suporta(self, ident: idf.Identificacao) -> bool:
        # NF-e e NFC-e têm a MESMA estrutura — `infNFe`, `det`, `prod`,
        # `imposto`. Um parser lê as duas; o que muda é o modelo, e isso a
        # identificação já resolveu antes de chegar aqui.
        return ident.especie in idf.ESPECIES_NOTA

    def interpretar(self, conteudo: bytes, ident: idf.Identificacao,
                    identidade_empresa: str = "") -> dm.Documento:
        try:
            raiz = ET.fromstring(conteudo)
        except ET.ParseError as exc:
            raise ErroParser(f"XML malformado: {type(exc).__name__}") from exc

        if _local(raiz.tag) == "resNFe":
            return self._resumo(raiz, ident, identidade_empresa)
        return self._completo(raiz, ident, identidade_empresa)

    # ------------------------------------------------------------------
    def _resumo(self, raiz, ident, identidade_empresa) -> dm.Documento:
        emit = dm.Participante(
            identificador=normalizar(_texto(raiz, "CNPJ")).valor
            or idf.digitos_da_chave(_texto(raiz, "CNPJ")),
            tipo="CNPJ", nome=_texto(raiz, "xNome"), ie=_texto(raiz, "IE"))
        dh = _data_hora(_texto(raiz, "dhEmi"))
        situacao = {"1": dm.AUTORIZADO, "2": dm.DENEGADO,
                    "3": dm.CANCELADO}.get(_texto(raiz, "cSitNFe"), dm.INDEFINIDA)
        return dm.Documento(
            id_documento=ident.id_documento, especie=ident.especie,
            chave=ident.chave,
            identidade_empresa=identidade_empresa,
            # O `resNFe` traz emitente, valor, data e situação — e mais nada.
            # Não nomeia destinatário, transportador nem `autXML`. Se a empresa
            # não é a emitente, o papel dela é INDETERMINADO, e dizer `OUTRO`
            # aqui é dizer "não sei", não "não participa". Enriquece sozinho
            # quando o documento completo chegar e promover o canônico.
            papeis=self._papeis(identidade_empresa,
                                emitente=emit.identificador),
            papel=dm.papel_principal(
                self._papeis(identidade_empresa, emitente=emit.identificador)),
            emitente=emit, dh_emissao=dh,
            competencia=date(dh.year, dh.month, 1) if dh else None,
            valor_total=_valor(raiz, "vNF"), situacao=situacao,
            versao_schema="resNFe", parser=self.nome, versao_parser=self.VERSAO,
            extensao=dm.ExtensaoNFe(protocolo=_texto(raiz, "nProt")),
            avisos=("resumo: sem itens nem totais detalhados",))

    # ------------------------------------------------------------------
    def _completo(self, raiz, ident, identidade_empresa) -> dm.Documento:
        inf = _primeiro(raiz, "infNFe")
        if inf is None:
            raise ErroParser("sem bloco infNFe")

        ide = _achar(inf, "ide")
        emit = _participante(_achar(inf, "emit"))
        dest = _participante(_achar(inf, "dest"))
        dh = _data_hora(_texto(ide, "dhEmi") or _texto(ide, "dEmi"))
        # `dhSaiEnt` (4.00) ou `dSaiEnt` (3.10). O schema o marca como
        # opcional, mas na prática ele vem: 99,5% das NF-e completas deste
        # acervo o trazem (7.498 de 7.534, medido em 23/08/2026). `None` quer
        # dizer "o documento não informou" — nunca "saiu no dia da emissão".
        # Cair para `dhEmi` seria inventar justamente o dado que este campo
        # existe para registrar.
        saida = _data_hora(_texto(ide, "dhSaiEnt") or _texto(ide, "dSaiEnt"))

        itens = []
        for det in _filhos(inf, "det"):
            prod = _achar(det, "prod")
            imposto = _achar(det, "imposto")
            if prod is None:
                continue
            try:
                numero = int(det.get("nItem") or 0)
            except ValueError:
                numero = 0
            itens.append(dm.ItemNFe(
                numero=numero,
                codigo=_texto(prod, "cProd"), descricao=_texto(prod, "xProd"),
                ncm=_texto(prod, "NCM"), cest=_texto(prod, "CEST"),
                cfop=_texto(prod, "CFOP"), unidade=_texto(prod, "uCom"),
                quantidade=_valor(prod, "qCom"),
                valor_unitario=_valor(prod, "vUnCom"),
                valor_produto=_valor(prod, "vProd"),
                desconto=_valor(prod, "vDesc"),
                icms=_icms_do_item(imposto),
                ipi=_tributo_do_item(imposto, "IPI"),
                pis=_tributo_do_item(imposto, "PIS"),
                cofins=_tributo_do_item(imposto, "COFINS"),
                reforma=_reforma_do_item(imposto),
            ))

        bloco_total = _achar(inf, "total") or inf
        tot = _achar(bloco_total, "ICMSTot")
        # O total da Reforma é IRMÃO do `ICMSTot`, não filho dele, e existe
        # sem depender dele: por isso os dois são buscados separadamente e o
        # `TotaisNFe` é montado mesmo quando só um dos dois veio. Ler o
        # `IBSCBSTot` de dentro do `ICMSTot` devolveria `None` sempre.
        reforma_tot = _achar(bloco_total, "IBSCBSTot")
        gibs = _achar(reforma_tot, "gIBS") if reforma_tot is not None else None
        gcbs = _achar(reforma_tot, "gCBS") if reforma_tot is not None else None

        # `_valor` não aceita elemento nulo, e agora o `TotaisNFe` pode nascer
        # de um bloco sem o outro: um XML só com `IBSCBSTot` é possível no
        # leiaute, e sem esta guarda o parser morreria nele.
        def _v(el, tag):
            return _valor(el, tag) if el is not None else None

        totais = dm.TotaisNFe(
            base_icms=_v(tot, "vBC"), icms=_v(tot, "vICMS"),
            base_icms_st=_v(tot, "vBCST"), icms_st=_v(tot, "vST"),
            fcp=_v(tot, "vFCP"), produtos=_v(tot, "vProd"),
            frete=_v(tot, "vFrete"), seguro=_v(tot, "vSeg"),
            desconto=_v(tot, "vDesc"), ipi=_v(tot, "vIPI"),
            pis=_v(tot, "vPIS"), cofins=_v(tot, "vCOFINS"),
            outros=_v(tot, "vOutro"), total=_v(tot, "vNF"),
            base_ibs_cbs=_v(reforma_tot, "vBCIBSCBS"),
            ibs=_v(gibs, "vIBS"), cbs=_v(gcbs, "vCBS"),
        ) if (tot is not None or reforma_tot is not None) else dm.TotaisNFe()

        # `transporta` fica em `transp`, e `autXML` é uma lista — os dois
        # blocos existem no schema e não eram lidos até a NF-e 4.
        transp = _achar(_achar(inf, "transp") or inf, "transporta")
        transportador = normalizar(
            _texto(transp, "CNPJ") or _texto(transp, "CPF")).valor if transp is not None else ""
        autorizados = tuple(
            filter(None, (normalizar(_texto(a, "CNPJ") or _texto(a, "CPF")).valor
                          for a in _filhos(inf, "autXML"))))
        papeis = self._papeis(identidade_empresa,
                              emitente=emit.identificador,
                              destinatario=dest.identificador,
                              transportador=transportador,
                              autorizados=autorizados)

        prot = _primeiro(raiz, "infProt")
        situacao = dm.SEM_PROTOCOLO
        if prot is not None:
            cstat = _texto(prot, "cStat")
            situacao = dm.AUTORIZADO if cstat in ("100", "150") else (
                dm.DENEGADO if cstat in ("110", "301", "302", "303") else dm.INDEFINIDA)

        return dm.Documento(
            id_documento=ident.id_documento, especie=ident.especie,
            chave=ident.chave,
            numero=_texto(ide, "nNF"), serie=_texto(ide, "serie"),
            identidade_empresa=identidade_empresa,
            papel=dm.papel_principal(papeis), papeis=papeis,
            emitente=emit, contraparte=dest, dh_emissao=dh,
            dh_saida_entrada=saida,
            # A competência continua vindo da EMISSÃO. `dhSaiEnt` não a
            # desloca: mudar isso mexeria em apuração, e esta fase é
            # documental.
            competencia=date(dh.year, dh.month, 1) if dh else None,
            valor_total=totais.total, situacao=situacao,
            versao_schema=(inf.get("versao") or ""),
            parser=self.nome, versao_parser=self.VERSAO,
            extensao=dm.ExtensaoNFe(
                natureza_operacao=_texto(ide, "natOp"),
                tipo_operacao=_texto(ide, "tpNF"),
                finalidade=_texto(ide, "finNFe"),
                itens=tuple(itens), totais=totais,
                protocolo=_texto(prot, "nProt") if prot is not None else "",
                dh_recebimento=_data_hora(_texto(prot, "dhRecbto")) if prot is not None else None,
                codigo_status=_texto(prot, "cStat") if prot is not None else "",
                motivo_status=_texto(prot, "xMotivo") if prot is not None else "",
            ))

    # ------------------------------------------------------------------
    @staticmethod
    def _papeis(empresa: str, *, emitente: str = "", destinatario: str = "",
                transportador: str = "", autorizados=()) -> tuple[str, ...]:
        """Em que qualidade(s) a empresa aparece NESTE documento.

        Cada papel sai de um bloco oficial do XML — `emit`, `dest`,
        `transporta`, `autXML` — e de nenhum outro lugar. Não se infere papel
        pelo valor, pela pasta em que o arquivo estava, nem por o documento ser
        completo: essa última é a inferência que a NF-e 2 proibiu por escrito,
        porque o transportador recebe o XML completo sem manifestar.

        Devolve TODOS os papéis encontrados, e não apenas um. Uma empresa pode
        legitimamente ser destinatária e transportadora da mesma nota (compra
        com frete próprio), e forçar um valor único apagaria metade do fato.

        Vazio quando a empresa não aparece em bloco nenhum — que é diferente de
        "aparece como outra coisa". Quem traduz vazio em `OUTRO` é
        `dm.papel_principal`.
        """
        alvo = normalizar(empresa)
        if not alvo.valido:
            return ()
        eu = alvo.valor
        achados = []
        if eu and eu == emitente:
            achados.append(dm.EMITENTE)
        if eu and eu == destinatario:
            achados.append(dm.DESTINATARIO)
        if eu and eu == transportador:
            achados.append(dm.TRANSPORTADOR)
        if eu and eu in {a for a in (autorizados or ()) if a}:
            achados.append(dm.AUTXML)
        return tuple(achados)


# ── eventos ─────────────────────────────────────────────────────────────────
class ParserEventoNFe:
    """Lê `procEventoNFe`, `evento` e `resEvento`.

    Evento não é documento: ele MODIFICA a situação de um documento. Por isso
    tem parser próprio, id próprio e tabela própria no índice — juntar os dois
    faria um cancelamento apagar a nota que ele cancela."""

    tipo = dm.EVENTO_NFE
    nome = "evento_nfe"
    VERSAO = VERSAO_EVENTO

    def suporta(self, ident: idf.Identificacao) -> bool:
        return ident.especie == idf.EVENTO_NFE

    def interpretar(self, conteudo: bytes, ident: idf.Identificacao,
                    identidade_empresa: str = "") -> dm.Documento:
        try:
            raiz = ET.fromstring(conteudo)
        except ET.ParseError as exc:
            raise ErroParser(f"XML malformado: {type(exc).__name__}") from exc

        if _local(raiz.tag) == "resEvento":
            return self._resumo(raiz, ident, identidade_empresa)
        return self._completo(raiz, ident, identidade_empresa)

    # ------------------------------------------------------------------
    def _resumo(self, raiz, ident, identidade_empresa) -> dm.Documento:
        """`resEvento` — o resumo que a Distribuição DF-e entrega.

        Descoberto na primeira consulta real (15/08/2026): 49 dos 50 documentos
        vieram assim, e o parser recusava todos com "sem bloco infEvento".

        A diferença é estrutural: no `resEvento` **os campos ficam direto sob a
        raiz** — não há `infEvento`, não há `detEvento`. Tratar um como o outro
        era o defeito; por isso são dois caminhos, e não um `if` no meio do
        outro.

        O que o resumo traz e o completo não: `xEvento` já pronto pelo serviço.
        O que ele NÃO traz: justificativa de cancelamento e texto de carta de
        correção — que ficam `None`, porque não existem aqui. `None` é "o
        documento não informou"; `""` seria "informou vazio", e são coisas
        diferentes na hora de conferir um cancelamento.
        """
        chave = ident.chave or idf.digitos_da_chave(_texto(raiz, "chNFe"))
        tp = _texto(raiz, "tpEvento") or ident.tp_evento
        dh = _data_hora(_texto(raiz, "dhEvento"))
        cnpj = _texto(raiz, "CNPJ") or _texto(raiz, "CPF")
        autor = normalizar(cnpj)

        return dm.Documento(
            id_documento=ident.id_documento, especie=dm.EVENTO_NFE,
            chave=chave, identidade_empresa=identidade_empresa,
            numero=_texto(raiz, "nSeqEvento") or ident.seq_evento,
            emitente=dm.Participante(
                identificador=autor.valor if autor.valido
                else idf.digitos_da_chave(cnpj),
                tipo=autor.tipo),
            dh_emissao=dh,
            competencia=date(dh.year, dh.month, 1) if dh else None,
            situacao=dm.INDEFINIDA,
            versao_schema=(raiz.get("versao") or ""),
            parser=self.nome, versao_parser=self.VERSAO,
            extensao=dm.ExtensaoEvento(
                tp_evento=tp,
                # O serviço já manda a descrição pronta em `xEvento`. Usar a
                # tabela local aqui seria substituir dado do documento por
                # palpite nosso.
                descricao=_texto(raiz, "xEvento") or DESCRICAO_EVENTO.get(tp, ""),
                sequencia=_texto(raiz, "nSeqEvento") or ident.seq_evento,
                orgao=_texto(raiz, "cOrgao"),
                protocolo=_texto(raiz, "nProt"),
                justificativa=None,      # não existe no resumo — ver docstring
                correcao=None,
                dh_recebimento=_data_hora(_texto(raiz, "dhRecbto")),
                forma="resEvento",
            ),
            avisos=("resumo de evento: sem detEvento (justificativa e correção "
                    "não vêm neste formato)",))

    # ------------------------------------------------------------------
    def _completo(self, raiz, ident, identidade_empresa) -> dm.Documento:
        inf = _primeiro(raiz, "infEvento")
        if inf is None:
            raise ErroParser("sem bloco infEvento")

        det = _achar(inf, "detEvento")
        tp = _texto(inf, "tpEvento") or ident.tp_evento
        dh = _data_hora(_texto(inf, "dhEvento"))
        cnpj = _texto(inf, "CNPJ") or _texto(inf, "CPF")
        autor = normalizar(cnpj)

        # O protocolo do evento está em `retEvento/infEvento/nProt` — dois
        # níveis abaixo, e não como filho direto. Busca em profundidade dentro
        # do `retEvento` para não depender da forma exata do envelope.
        ret = _primeiro(raiz, "retEvento")
        el_prot = _primeiro(ret, "nProt") if ret is not None else None
        protocolo = (el_prot.text or "").strip() if el_prot is not None else ""

        return dm.Documento(
            id_documento=ident.id_documento, especie=dm.EVENTO_NFE,
            chave=ident.chave, identidade_empresa=identidade_empresa,
            numero=_texto(inf, "nSeqEvento") or ident.seq_evento,
            emitente=dm.Participante(
                identificador=autor.valor if autor.valido else idf.digitos_da_chave(cnpj),
                tipo=autor.tipo),
            dh_emissao=dh,
            competencia=date(dh.year, dh.month, 1) if dh else None,
            situacao=dm.INDEFINIDA,
            versao_schema=(inf.get("versao") or ""),
            parser=self.nome, versao_parser=self.VERSAO,
            extensao=dm.ExtensaoEvento(
                tp_evento=tp,
                descricao=_texto(det, "descEvento") if det is not None
                else DESCRICAO_EVENTO.get(tp, ""),
                sequencia=_texto(inf, "nSeqEvento") or ident.seq_evento,
                orgao=_texto(inf, "cOrgao"),
                protocolo=protocolo,
                justificativa=(_texto(det, "xJust") or None) if det is not None else None,
                correcao=(_texto(det, "xCorrecao") or None) if det is not None else None,
                dh_recebimento=_data_hora(_texto(_primeiro(raiz, "retEvento") or inf,
                                                 "dhRegEvento")) if ret is not None else None,
                forma="procEventoNFe",
            ))


# ════════════════════════════════════════════════════════════════════════════
#  CT-e modelo 57
# ════════════════════════════════════════════════════════════════════════════
class ParserCTe57:
    """Lê `cteProc`, `CTe` e `resCTe`.

    O QUE MUDA EM RELAÇÃO À NOTA
        Uma NF-e tem dois participantes que importam: quem emitiu e quem
        recebeu. Um CT-e tem seis — e a pergunta fiscal, "de quem é este
        frete?", é respondida por um deles que **não tem bloco próprio**.

    O TOMADOR, E POR QUE ELE É O PONTO
        `<toma>` guarda um código de 0 a 3 que APONTA para outro participante:
        0 remetente, 1 expedidor, 2 recebedor, 3 destinatário. Só quando vale 4
        existe `<toma4>`, com CNPJ próprio.

        Ler apenas `toma4` — que é o caminho óbvio, porque é o único bloco
        visível — faria o tomador sumir na maioria dos documentos. E o tomador
        é quem paga o frete e lança o crédito: perdê-lo em silêncio é perder
        exatamente a informação pela qual o contador procura o documento.

    MODELO 67 (CT-e OS) FICA DE FORA, DECLARADAMENTE
        O CT-e OS tem `infCTeNorm` diferente e participantes próprios. Sem
        esquema ou amostra oficial para conferir, declarar suporte seria
        afirmar o que não foi verificado. Um modelo 67 é identificado e
        preservado, e o parser registra o aviso em vez de fingir que leu.
    """

    tipo = dm.CTE57
    nome = "cte57"
    VERSAO = VERSAO_CTE57

    def suporta(self, ident: idf.Identificacao) -> bool:
        return ident.especie == idf.CTE57

    def interpretar(self, conteudo: bytes, ident: idf.Identificacao,
                    identidade_empresa: str = "") -> dm.Documento:
        try:
            raiz = ET.fromstring(conteudo)
        except ET.ParseError as exc:
            raise ErroParser(f"XML malformado: {type(exc).__name__}") from exc
        if _local(raiz.tag) == "resCTe":
            return self._resumo(raiz, ident, identidade_empresa)
        return self._completo(raiz, ident, identidade_empresa)

    # ------------------------------------------------------------------
    def _resumo(self, raiz, ident, identidade_empresa) -> dm.Documento:
        """`resCTe` — o resumo da Distribuição DF-e.

        Traz emitente, valor e situação. **Não traz os outros participantes** —
        e é por isso que o papel da empresa aqui só pode ser EMITENTE ou
        indeterminado. Dizer `OUTRO` afirmaria que ela não participa; o certo é
        deixar vazio até o documento completo chegar.
        """
        cnpj = _texto(raiz, "CNPJ") or _texto(raiz, "CPF")
        emit = dm.Participante(
            identificador=normalizar(cnpj).valor or idf.digitos_da_chave(cnpj),
            tipo="CNPJ" if len(idf.digitos_da_chave(cnpj)) == 14 else "CPF",
            nome=_texto(raiz, "xNome"), ie=_texto(raiz, "IE"))
        dh = _data_hora(_texto(raiz, "dhEmi"))
        situacao = {"1": dm.AUTORIZADO, "2": dm.DENEGADO,
                    "3": dm.CANCELADO}.get(_texto(raiz, "cSitCTe"), dm.INDEFINIDA)
        alvo = normalizar(identidade_empresa).valor if identidade_empresa else ""
        papeis = (dm.EMITENTE,) if alvo and alvo == emit.identificador else ()
        return dm.Documento(
            id_documento=ident.id_documento, especie=dm.CTE57, chave=ident.chave,
            identidade_empresa=identidade_empresa,
            papeis=papeis, papel=dm.papel_principal(papeis, cte=True),
            emitente=emit, dh_emissao=dh,
            competencia=date(dh.year, dh.month, 1) if dh else None,
            valor_total=_valor(raiz, "vTPrest"), situacao=situacao,
            versao_schema="resCTe", parser=self.nome, versao_parser=self.VERSAO,
            extensao=dm.ExtensaoCTe(protocolo=_texto(raiz, "nProt")),
            avisos=("resumo: sem participantes além do emitente",))

    # ------------------------------------------------------------------
    def _completo(self, raiz, ident, identidade_empresa) -> dm.Documento:
        inf = _primeiro(raiz, "infCte")
        if inf is None:
            raise ErroParser("sem bloco infCte")

        ide = _achar(inf, "ide")
        avisos = []
        modelo = _texto(ide, "mod") or (ident.chave[20:22] if ident.chave else "")
        if modelo == "67":
            avisos.append("CT-e OS (modelo 67): leiaute não conferido nesta "
                          "versão; participantes podem ficar incompletos")

        # ── os participantes que têm bloco próprio ──────────────────────
        blocos = ((dm.EMITENTE, "emit"), (dm.REMETENTE, "rem"),
                  (dm.DESTINATARIO, "dest"), (dm.EXPEDIDOR, "exped"),
                  (dm.RECEBEDOR, "receb"))
        participantes = []
        por_papel = {}
        for papel, tag in blocos:
            el = _achar(inf, tag)
            if el is None:
                continue
            doc = _texto(el, "CNPJ") or _texto(el, "CPF")
            ender = None
            for nome_ender in ("enderEmit", "enderReme", "enderDest",
                               "enderExped", "enderReceb"):
                ender = _achar(el, nome_ender)
                if ender is not None:
                    break
            p = dm.ParticipanteCTe(
                papel=papel,
                identificador=normalizar(doc).valor or idf.digitos_da_chave(doc),
                nome=_texto(el, "xNome"), ie=_texto(el, "IE"),
                uf=_texto(ender, "UF") if ender is not None else "",
                municipio=_texto(ender, "xMun") if ender is not None else "")
            participantes.append(p)
            por_papel[papel] = p

        # ── o tomador ───────────────────────────────────────────────────
        bloco4 = _achar(inf, "ide", "toma4")
        bloco3 = _achar(inf, "ide", "toma3")
        codigo = _texto(ide, "toma")
        if not codigo and bloco3 is not None:
            codigo = _texto(bloco3, "toma")
        if not codigo and bloco4 is not None:
            codigo = _texto(bloco4, "toma") or "4"
        tomador_papel = ""

        doc4 = ""
        if bloco4 is not None:
            doc4 = _texto(bloco4, "CNPJ") or _texto(bloco4, "CPF")

        if doc4:
            p = dm.ParticipanteCTe(
                papel=dm.TOMADOR,
                identificador=normalizar(doc4).valor or idf.digitos_da_chave(doc4),
                nome=_texto(bloco4, "xNome"), ie=_texto(bloco4, "IE"))
            participantes.append(p)
            por_papel[dm.TOMADOR] = p
            tomador_papel = dm.TOMADOR
        elif codigo in dm.TOMADOR_POR_CODIGO:
            alvo_papel = dm.TOMADOR_POR_CODIGO[codigo]
            tomador_papel = alvo_papel
            origem = por_papel.get(alvo_papel)
            if origem is not None:
                # O tomador é o MESMO participante, sob outro papel. Ele entra
                # duas vezes de propósito: quem procura "tomador" o acha, e
                # quem procura "remetente" também.
                participantes.append(dm.ParticipanteCTe(
                    papel=dm.TOMADOR, identificador=origem.identificador,
                    nome=origem.nome, ie=origem.ie, uf=origem.uf,
                    municipio=origem.municipio))
                por_papel[dm.TOMADOR] = origem
            else:
                avisos.append(f"`toma`={codigo} aponta para {alvo_papel}, que "
                              f"não veio no documento: tomador indeterminado")
        elif codigo:
            avisos.append(f"`toma`={codigo!r} fora da tabela 0..4")

        autorizados = tuple(filter(None, (
            normalizar(_texto(a, "CNPJ") or _texto(a, "CPF")).valor
            for a in _filhos(inf, "autXML"))))

        papeis = self._papeis(identidade_empresa, por_papel, autorizados)

        # ── valores e imposto ───────────────────────────────────────────
        vprest = _achar(inf, "vPrest")
        icms = None
        imp = _achar(inf, "imp")
        if imp is not None:
            bloco = _achar(imp, "ICMS")
            if bloco is not None:
                for filho in bloco:
                    icms = filho
                    break

        # ── a carga: as NF-e que este CT-e transporta ───────────────────
        #
        # SÓ CHAVE QUE FECHA O DV.
        #
        # O leiaute manda a chave em `infDoc/infNFe/chave`, e o emitente
        # preenche o que quiser. Medido nos 4.243 CT-e da TRX: **1.204 traziam
        # 44 noves** — um preenchimento fictício, não uma nota. Guardá-los
        # criava um vínculo falso, e 1.204 fretes apareciam carregando a mesma
        # NF-e inexistente.
        #
        # `chave_valida` já recusa as duas formas do problema: DV que não fecha
        # e dígito repetido 44 vezes. Uma carga que não sabemos qual é fica
        # AUSENTE, que é o que ela é — inventar o elo é pior do que não tê-lo.
        vistas, chaves, descartadas = set(), [], 0
        for e in inf.iter():
            if _local(e.tag) != "chave" or not (e.text or "").strip():
                continue
            c = idf.digitos_da_chave(e.text)
            if not idf.chave_valida(c):
                descartadas += 1
                continue
            if c not in vistas:
                vistas.add(c)
                chaves.append(c)
        chaves_nfe = tuple(chaves)
        if descartadas:
            avisos.append(f"{descartadas} chave(s) de carga sem DV válido "
                          f"foram ignoradas: o documento não diz o que levou")

        prot = _primeiro(raiz, "infProt")
        situacao = dm.SEM_PROTOCOLO
        if prot is not None:
            cstat = _texto(prot, "cStat")
            situacao = dm.AUTORIZADO if cstat in ("100", "150") else (
                dm.DENEGADO if cstat in ("110", "301", "302", "303")
                else dm.INDEFINIDA)

        dh = _data_hora(_texto(ide, "dhEmi"))
        emit = por_papel.get(dm.EMITENTE)
        dest = por_papel.get(dm.DESTINATARIO)
        total = _valor(vprest, "vTPrest") if vprest is not None else None

        return dm.Documento(
            id_documento=ident.id_documento, especie=dm.CTE57, chave=ident.chave,
            numero=_texto(ide, "nCT"), serie=_texto(ide, "serie"),
            identidade_empresa=identidade_empresa,
            papeis=papeis, papel=dm.papel_principal(papeis, cte=True),
            emitente=dm.Participante(
                identificador=emit.identificador if emit else "",
                nome=emit.nome if emit else "", ie=emit.ie if emit else ""),
            contraparte=dm.Participante(
                identificador=dest.identificador if dest else "",
                nome=dest.nome if dest else "", ie=dest.ie if dest else ""),
            dh_emissao=dh,
            competencia=date(dh.year, dh.month, 1) if dh else None,
            valor_total=total, situacao=situacao,
            versao_schema=(inf.get("versao") or ""),
            parser=self.nome, versao_parser=self.VERSAO,
            extensao=dm.ExtensaoCTe(
                modelo=modelo or "57",
                cfop=_texto(ide, "CFOP"),
                natureza_operacao=_texto(ide, "natOp"),
                tipo_cte=_texto(ide, "tpCTe"),
                tipo_servico=_texto(ide, "tpServ"),
                modal=_texto(ide, "modal"),
                tomador_codigo=codigo, tomador_papel=tomador_papel,
                participantes=tuple(participantes),
                uf_inicio=_texto(ide, "UFIni"), uf_fim=_texto(ide, "UFFim"),
                municipio_inicio=_texto(ide, "xMunIni"),
                municipio_fim=_texto(ide, "xMunFim"),
                valor_prestacao=total,
                valor_receber=_valor(vprest, "vRec") if vprest is not None else None,
                base_icms=_valor(icms, "vBC") if icms is not None else None,
                aliquota_icms=_valor(icms, "pICMS") if icms is not None else None,
                valor_icms=_valor(icms, "vICMS") if icms is not None else None,
                cst_icms=_texto(icms, "CST") if icms is not None else "",
                chaves_nfe=chaves_nfe,
                protocolo=_texto(prot, "nProt") if prot is not None else "",
                dh_recebimento=(_data_hora(_texto(prot, "dhRecbto"))
                                if prot is not None else None),
                codigo_status=_texto(prot, "cStat") if prot is not None else "",
                motivo_status=_texto(prot, "xMotivo") if prot is not None else "",
            ),
            avisos=tuple(avisos))

    @staticmethod
    def _papeis(empresa, por_papel, autorizados) -> tuple:
        """TODOS os papéis da empresa neste CT-e, nunca só o mais forte.

        Uma transportadora que emite o CT-e e também é a tomadora tem os dois
        papéis. Guardar um só faria o documento sumir de uma das duas
        perguntas — e as duas são feitas.
        """
        alvo = normalizar(empresa).valor if empresa else ""
        if not alvo:
            return ()
        achados = {papel for papel, p in por_papel.items()
                   if p.identificador == alvo}
        if alvo in (autorizados or ()):
            achados.add(dm.AUTXML)
        return tuple(sorted(achados))


class ParserEventoCTe:
    """Lê `procEventoCTe`, `eventoCTe` e o `resEvento` do CT-e.

    Mesma disciplina do evento de NF-e: o evento não substitui o documento, ele
    registra o que aconteceu com ele. O XML original do CT-e continua intocado
    quando um cancelamento chega — o que muda é a situação derivada na leitura.
    """

    tipo = dm.EVENTO_CTE
    nome = "evento_cte"
    VERSAO = VERSAO_EVENTO_CTE

    def suporta(self, ident: idf.Identificacao) -> bool:
        return ident.especie == idf.EVENTO_CTE

    def interpretar(self, conteudo: bytes, ident: idf.Identificacao,
                    identidade_empresa: str = "") -> dm.Documento:
        try:
            raiz = ET.fromstring(conteudo)
        except ET.ParseError as exc:
            raise ErroParser(f"XML malformado: {type(exc).__name__}") from exc

        resumo = _local(raiz.tag) == "resEvento"
        base = raiz if resumo else (_primeiro(raiz, "infEvento") or raiz)
        chave = ident.chave or idf.digitos_da_chave(_texto(base, "chCTe"))
        tp = _texto(base, "tpEvento") or ident.tp_evento
        dh = _data_hora(_texto(base, "dhEvento"))
        doc = _texto(base, "CNPJ") or _texto(base, "CPF")
        autor = normalizar(doc)

        det = None if resumo else _primeiro(raiz, "detEvento")
        ret = None if resumo else _primeiro(raiz, "retEventoCTe")

        return dm.Documento(
            id_documento=ident.id_documento, especie=dm.EVENTO_CTE,
            chave=chave, identidade_empresa=identidade_empresa,
            numero=_texto(base, "nSeqEvento") or ident.seq_evento,
            emitente=dm.Participante(
                identificador=(autor.valor if autor.valido
                               else idf.digitos_da_chave(doc)),
                nome=_texto(base, "xNome")),
            dh_emissao=dh,
            competencia=date(dh.year, dh.month, 1) if dh else None,
            situacao=dm.INDEFINIDA,
            versao_schema="resEvento" if resumo else "procEventoCTe",
            parser=self.nome, versao_parser=self.VERSAO,
            extensao=dm.ExtensaoEvento(
                tp_evento=tp,
                descricao=_texto(base, "xEvento") or DESCRICAO_EVENTO.get(tp, ""),
                sequencia=_texto(base, "nSeqEvento") or ident.seq_evento,
                orgao=_texto(base, "cOrgao") or ident.orgao,
                protocolo=_texto(ret, "nProt") if ret is not None else "",
                # `None` é "o documento não informou". No resumo esses campos
                # NÃO existem — e `""` diria que existem e vieram vazios.
                # BUSCA PROFUNDA, e não filho direto. Na NF-e o `xJust` fica
                # sob `detEvento` sem invólucro — conferido em evento real do
                # acervo. No CT-e o leiaute prevê `evCancCTe`/`evCCeCTe` entre
                # os dois, e **não há nenhum evento de CT-e real aqui para
                # conferir**. Procurar em profundidade acerta nas duas formas;
                # ler só o filho direto perderia a justificativa em silêncio,
                # que é exatamente o campo que explica um cancelamento.
                justificativa=_fundo(det, "xJust"),
                correcao=_fundo(det, "xCorrecao"),
                dh_recebimento=(_data_hora(_texto(ret, "dhRegEvento"))
                                if ret is not None else None),
                forma="resEvento" if resumo else "procEventoCTe",
            ))


# ── registro ────────────────────────────────────────────────────────────────
_REGISTRO = [ParserNFe55(), ParserEventoNFe(), ParserCTe57(), ParserEventoCTe()]


def para(ident: idf.Identificacao):
    """O parser que sabe ler isto, ou `None`.

    `None` **não** é erro: é schema desconhecido, e o documento continua
    preservado esperando um parser que o entenda."""
    for p in _REGISTRO:
        if p.suporta(ident):
            return p
    return None


def versoes() -> dict[str, int]:
    """Versão atual de cada parser — o que o índice guarda para reprocessar."""
    return {p.nome: p.VERSAO for p in _REGISTRO}
