"""fonte_api_emissao.py — emissão própria por API de terceiro.

O QUE ESTA CAMADA É
    Mais uma `FonteEmissao`, ao lado da `PastaEmissor`. Ela busca num serviço
    de mercado (Focus NFe, Arquivei, NFE.io, Espião…) as NF-e que a empresa
    **emitiu** e devolve `(nome, bytes, origem)`. Nada além disso.

    Ela NÃO valida, NÃO decide identidade, NÃO deduplica, NÃO grava e NÃO
    indexa. Quem faz isso é `importacao.importar()`, o portão único, desde a
    APURAÇÃO 6B. Uma fonte que gravasse seria um segundo caminho de escrita no
    acervo — o problema que aquela fase existiu para resolver.

O CONTRATO DO FORNECEDOR NÃO ESTÁ AQUI, E ISSO É DE PROPÓSITO
    Endpoint, cabeçalho de autenticação e nomes de campo mudam de fornecedor
    para fornecedor, e nenhum deles foi conferido contra documentação oficial.
    Inventá-los e chamar de integração é o que a D61 proíbe: "documentação
    oficial vem antes de qualquer código".

    Por isso existe `ContratoAPI`: um objeto de CONFIGURAÇÃO, preenchido a
    partir do manual do fornecedor. O código abaixo é o mesmo para todos; o
    que muda é o contrato. Enquanto ele não for preenchido com dado real, a
    fonte se declara indisponível e não chama ninguém.

O CERTIFICADO NÃO SOBE POR PADRÃO
    `enviar_certificado` nasce `False`. Mandar o A1 da empresa para um
    terceiro dá a ele a capacidade de EMITIR em nome dela — é decisão de
    negócio com consequência jurídica, não detalhe de integração. Quando for
    tomada, que seja escrita e explícita.

O QUE ELA NUNCA FAZ
    Não escreve segredo em log, não guarda token em disco, não repete consulta
    em laço automático e não decide sozinha o que é venda.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .fontes_emissao import DocumentoDeOrigem
from .identidade import normalizar

TIPO = "API_EMISSAO"

# Formatos em que um XML costuma chegar numa resposta JSON.
CRU = "xml"
BASE64 = "base64"
BASE64_GZIP = "base64+gzip"
FORMATOS = (CRU, BASE64, BASE64_GZIP)

_SO_DIG = re.compile(r"\D")


class ContratoNaoPreenchido(Exception):
    """Falta o que só o manual do fornecedor pode dizer."""


class RespostaInesperada(Exception):
    """O serviço respondeu algo que o contrato não descreve."""


@dataclass(frozen=True)
class ContratoAPI:
    """O que muda de um fornecedor para outro. Vem do manual, não daqui.

    `campo_*` são caminhos dentro do JSON, com pontos: `"dados.itens"`,
    `"nfe.chave_acesso"`. Nenhum default é chute — o que não for informado
    impede a fonte de rodar, e é assim que se descobre o que falta antes de
    gastar uma chamada.
    """
    nome: str = ""                       # "focus", "arquivei", "nfeio"…
    url_listagem: str = ""               # aceita {cnpj} {ano} {mes}
    metodo: str = "GET"
    cabecalho_auth: str = "Authorization"
    prefixo_token: str = "Bearer "
    # onde as coisas moram na resposta
    campo_lista: str = ""                # ex.: "data" ou "documentos"
    campo_chave: str = ""                # ex.: "chave" ou "chave_acesso"
    campo_xml: str = ""                  # ex.: "xml" — vazio = buscar à parte
    formato_xml: str = CRU
    url_xml: str = ""                    # quando o XML vem noutra chamada
    # paginação, quando houver
    campo_pagina: str = ""
    campo_proxima: str = ""
    enviar_certificado: bool = False     # ver docstring do módulo
    fonte_documentacao: str = ""         # de onde tudo acima foi lido

    @property
    def preenchido(self) -> bool:
        return bool(self.url_listagem and self.campo_lista and self.campo_chave
                    and (self.campo_xml or self.url_xml)
                    and self.formato_xml in FORMATOS
                    and self.fonte_documentacao)

    def faltando(self) -> list[str]:
        """O que impede esta fonte de rodar. Dito, não adivinhado."""
        falta = []
        for campo in ("url_listagem", "campo_lista", "campo_chave",
                      "fonte_documentacao"):
            if not getattr(self, campo):
                falta.append(campo)
        if not self.campo_xml and not self.url_xml:
            falta.append("campo_xml ou url_xml")
        if self.formato_xml not in FORMATOS:
            falta.append(f"formato_xml deve ser um de {FORMATOS}")
        return falta


def _caminho(dado, caminho: str):
    """`"a.b.c"` sobre dicionários aninhados. Ausente devolve `None`."""
    atual = dado
    for parte in (caminho or "").split("."):
        if not parte:
            continue
        if isinstance(atual, dict):
            atual = atual.get(parte)
        else:
            return None
    return atual


def decodificar_xml(valor, formato: str) -> bytes:
    """O XML como o fornecedor mandou → os bytes originais.

    Falha ALTO. Um XML que não decodifica é documento perdido, e devolver
    bytes truncados faria o portão gravar lixo com cara de nota.
    """
    if valor is None:
        raise RespostaInesperada("o campo do XML veio vazio")
    if isinstance(valor, bytes):
        bruto = valor
    else:
        bruto = str(valor).encode("utf-8")

    if formato == CRU:
        return bruto
    try:
        dados = base64.b64decode(bruto, validate=True)
    except (binascii.Error, ValueError) as e:
        raise RespostaInesperada(f"não é base64 válido: {e}") from e
    if formato == BASE64:
        return dados
    try:
        return gzip.decompress(dados)
    except OSError as e:
        raise RespostaInesperada(f"não descompacta como gzip: {e}") from e


@dataclass
class FonteApiEmissao:
    """A fonte. Cumpre `FonteEmissao` e nada além.

    `transporte` é injetado: um objeto com `.get(url, headers)` e
    `.post(url, headers, json)` devolvendo `(status, corpo_bytes)`. Assim o
    módulo é testável sem rede — mesma técnica do conector da NF-e.
    """
    identidade: str
    contrato: ContratoAPI
    token: str = ""
    ano: int = 0
    mes: int = 0
    transporte: object = None
    caminho_certificado: str = ""
    tipo: str = field(default=TIPO, init=False)
    avisos: list = field(default_factory=list, init=False)

    def __post_init__(self):
        ident = normalizar(self.identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida: {ident.motivo}")
        self.identidade = ident.valor

    # ── contrato FonteEmissao ────────────────────────────────────────────
    @property
    def disponivel(self) -> bool:
        return bool(self.contrato.preenchido and self.token
                    and self.transporte is not None
                    and self.ano and self.mes)

    def descrever(self) -> dict:
        """Como a fonte se apresenta. NUNCA com o token dentro."""
        return {
            "tipo": self.tipo,
            "fornecedor": self.contrato.nome or "(não informado)",
            "empresa": normalizar(self.identidade).mascarado(),
            "periodo": f"{self.ano:04d}-{self.mes:02d}" if self.ano else "",
            "disponivel": self.disponivel,
            "token_configurado": bool(self.token),
            "envia_certificado": self.contrato.enviar_certificado,
            "faltando": self.contrato.faltando(),
            "documentacao": self.contrato.fonte_documentacao or None,
        }

    def documentos(self) -> Iterable[DocumentoDeOrigem]:
        """As NF-e emitidas no mês. Gerador: não carrega tudo na memória."""
        if not self.contrato.preenchido:
            raise ContratoNaoPreenchido(
                "faltam do manual do fornecedor: "
                + ", ".join(self.contrato.faltando()))
        if not self.disponivel:
            return

        for item in self._listar():
            chave = _SO_DIG.sub("", str(_caminho(item, self.contrato.campo_chave)
                                        or ""))
            if len(chave) != 44:
                # Sem chave de 44 dígitos não há identidade. O portão recusaria
                # de qualquer forma; dizer aqui poupa a viagem.
                self.avisos.append("item sem chave de 44 dígitos, ignorado")
                continue

            bruto = self._xml_do_item(item, chave)
            if bruto is None:
                continue
            yield DocumentoDeOrigem(
                nome=f"{chave}.xml",
                conteudo=bruto,
                origem={
                    "fonte": self.tipo,
                    "fornecedor": self.contrato.nome,
                    "chave": chave,
                    "periodo": f"{self.ano:04d}-{self.mes:02d}",
                    "obtido_em": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"),
                    "bytes": len(bruto),
                })

    # ── miolo ────────────────────────────────────────────────────────────
    def _cabecalhos(self) -> dict:
        return {self.contrato.cabecalho_auth:
                f"{self.contrato.prefixo_token}{self.token}",
                "Accept": "application/json"}

    def _pedir(self, url: str) -> dict:
        status, corpo = self.transporte.get(url, headers=self._cabecalhos())
        if status != 200:
            # O corpo NÃO entra na mensagem: pode trazer o token de volta.
            raise RespostaInesperada(f"HTTP {status} em {_host(url)}")
        try:
            return json.loads(corpo.decode("utf-8", "replace"))
        except ValueError as e:
            raise RespostaInesperada(f"resposta não é JSON: {e}") from e

    def _listar(self):
        """Percorre as páginas do mês. Para quando o serviço não indica outra."""
        url = self.contrato.url_listagem.format(
            cnpj=self.identidade, ano=f"{self.ano:04d}", mes=f"{self.mes:02d}")
        visitadas = set()
        while url and url not in visitadas:
            visitadas.add(url)
            dado = self._pedir(url)
            itens = _caminho(dado, self.contrato.campo_lista)
            if itens is None:
                raise RespostaInesperada(
                    f"campo `{self.contrato.campo_lista}` não existe na resposta")
            if not isinstance(itens, list):
                raise RespostaInesperada(
                    f"campo `{self.contrato.campo_lista}` não é lista")
            for item in itens:
                yield item
            url = (str(_caminho(dado, self.contrato.campo_proxima) or "")
                   if self.contrato.campo_proxima else "")

    def _xml_do_item(self, item, chave: str) -> bytes | None:
        """O XML do item — no próprio item, ou numa segunda chamada."""
        if self.contrato.campo_xml:
            valor = _caminho(item, self.contrato.campo_xml)
            if valor is None:
                self.avisos.append(f"{chave[:8]}…: item sem o campo do XML")
                return None
            return decodificar_xml(valor, self.contrato.formato_xml)

        url = self.contrato.url_xml.format(cnpj=self.identidade, chave=chave)
        status, corpo = self.transporte.get(url, headers=self._cabecalhos())
        if status != 200:
            self.avisos.append(f"{chave[:8]}…: HTTP {status} ao buscar o XML")
            return None
        if self.contrato.formato_xml == CRU:
            return corpo
        return decodificar_xml(corpo, self.contrato.formato_xml)


def _host(url: str) -> str:
    """Só o host, para mensagem de erro. Caminho e query podem levar segredo."""
    m = re.match(r"^https?://([^/?#]+)", str(url or ""))
    return m.group(1) if m else "(url inválida)"
