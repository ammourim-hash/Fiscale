#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teste_fixturas_fiscal.py — NFS-e e eventos FICTÍCIOS no layout nacional.

POR QUE FICTÍCIOS, E POR QUE UM ARQUIVO SÓ
    Nenhum documento real do escritório entra em teste: o formato foi copiado,
    o conteúdo não. CNPJ, chave, número, protocolo e razão social são
    inventados — os CNPJ com DV calculado, para passarem nas validações.

    Um arquivo separado porque `import` de uma suíte dentro de outra EXECUTA a
    suíte importada (e desfaz travas de rede). Foi o que aconteceu na ING 3B.

O LAYOUT
    Segue o que os dois leitores realmente procuram: `core.parse_nfse` exige
    `infNFSe` com `emit/CNPJ`, `valores/vLiq` e `DPS/infDPS`; o
    `classificador.ler_nfse` busca em profundidade. As duas formas convivem
    aqui de propósito — é assim que o acervo real é.
"""
from __future__ import annotations

# Lido pelo runner por AST, sem importar: declara que este arquivo é
# APOIO, não suíte. Rodá-lo não executa asserção nenhuma, e contá-lo
# como suíte verde seria contar um arquivo que nunca testou nada.
TESTE_APOIO = True

NS = "http://www.sped.fazenda.gov.br/nfse"


def cnpj_ficticio(base12: str) -> str:
    """Completa os dois dígitos verificadores de um CNPJ inventado."""
    c = base12[:12]
    for _ in range(2):
        pesos = list(range(len(c) - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(c, pesos)) % 11
        c += str(0 if resto < 2 else 11 - resto)
    return c


PRESTADOR = cnpj_ficticio("111111110001")     # a empresa apurada
TOMADOR = cnpj_ficticio("222222220001")
OUTRO = cnpj_ficticio("333333330001")


def chave_nfse(numero: int) -> str:
    """Chave de 50 posições da NFS-e nacional. Formato, não conteúdo real."""
    return f"26{numero:011d}" + "0" * 37


def xml_nfse(numero: int, competencia: str, valor: float, *,
             emitente: str = PRESTADOR, tomador: str = TOMADOR,
             ctribnac: str = "170601", descricao: str = "SERVICO FICTICIO",
             iss_retido: bool = False, valor_iss: float = 0.0,
             op_simp_nac: str = "2", cstat: str = "100",
             ret_pis: float = 0.0, ret_cofins: float = 0.0,
             ret_csll: float = 0.0, ret_irrf: float = 0.0,
             tp_ret_piscofins: str = "", com_bloco_piscofins: bool = True,
             chave: str = "") -> tuple[str, bytes]:
    """`(nome_do_arquivo, bytes)` de uma NFS-e emitida.

    `com_bloco_piscofins=False` reproduz a nota que traz `tpRetPisCofins` solto,
    fora do bloco `<piscofins>` — variação que existe no acervo e que separa os
    dois leitores. Não é defeito da fixture: é o caso que revela a divergência.
    """
    ch = chave or chave_nfse(numero)
    dia = f"{competencia}-10"
    if com_bloco_piscofins:
        bloco_pc = (f"<piscofins><tpRetPisCofins>{tp_ret_piscofins}</tpRetPisCofins>"
                    f"<vPis>{ret_pis:.2f}</vPis>"
                    f"<vCofins>{ret_cofins:.2f}</vCofins></piscofins>")
    else:
        bloco_pc = (f"<tpRetPisCofins>{tp_ret_piscofins}</tpRetPisCofins>"
                    f"<vPis>{ret_pis:.2f}</vPis><vCofins>{ret_cofins:.2f}</vCofins>")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="{NS}" versao="1.00">
  <infNFSe Id="NFS{ch}">
    <nNFSe>{numero}</nNFSe>
    <cStat>{cstat}</cStat>
    <dhProc>{dia}T10:00:00-03:00</dhProc>
    <emit><CNPJ>{emitente}</CNPJ><xNome>EMPRESA FICTICIA LTDA</xNome></emit>
    <valores><vLiq>{valor:.2f}</vLiq><vISSQN>{valor_iss:.2f}</vISSQN></valores>
    <opSimpNac>{op_simp_nac}</opSimpNac>
    <cLocIncid>2611606</cLocIncid>
    <tribMun><tpRetISSQN>{'2' if iss_retido else '1'}</tpRetISSQN></tribMun>
    {bloco_pc}
    <vRetCSLL>{ret_csll:.2f}</vRetCSLL>
    <vRetIRRF>{ret_irrf:.2f}</vRetIRRF>
    <DPS><infDPS>
      <dCompet>{dia}</dCompet>
      <toma><CNPJ>{tomador}</CNPJ><xNome>TOMADOR FICTICIO SA</xNome></toma>
      <serv><vServ>{valor:.2f}</vServ>
        <cTribNac>{ctribnac}</cTribNac>
        <xTribNac>{descricao}</xTribNac>
        <xDescServ>{descricao}</xDescServ>
      </serv>
    </infDPS></DPS>
  </infNFSe>
</NFSe>"""
    return f"nfse-{ch}.xml", xml.encode("utf-8")


def xml_evento_nfse(chave: str, descricao: str = "Cancelamento de NFS-e",
                    chave_substituta: str = "") -> tuple[str, bytes]:
    """Evento de cancelamento — ou de cancelamento por SUBSTITUIÇÃO."""
    sub = f"<chSubstituta>{chave_substituta}</chSubstituta>" if chave_substituta else ""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<evento xmlns="{NS}" versao="1.00">
  <infEvento>
    <chNFSe>{chave}</chNFSe>
    <xDesc>{descricao}</xDesc>
    {sub}
  </infEvento>
</evento>"""
    return f"evento-{chave}-1.xml", xml.encode("utf-8")


def gravar(pasta, arquivos) -> None:
    """Grava `[(nome, bytes)]` em `<pasta>/xmls`."""
    from pathlib import Path
    d = Path(pasta) / "xmls"
    d.mkdir(parents=True, exist_ok=True)
    for nome, dados in arquivos:
        (d / nome).write_bytes(dados)
