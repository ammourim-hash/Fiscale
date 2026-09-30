# -*- coding: utf-8 -*-
"""fontes — de onde vêm os bytes da certidão ou do extrato.

O CONTRATO, E O QUE ELE DELIBERADAMENTE NÃO INCLUI
    Uma fonte faz **uma coisa**: entrega `Colheita(desfecho, documento, ...)`.

    Ela NÃO valida o documento, NÃO decide identidade, NÃO deduplica, NÃO
    grava, NÃO indexa e NÃO sabe o que é validade, natureza ou código de
    controle. Tudo isso pertence ao portão, ao armazenamento e ao índice.

    É o mesmo contrato do `ingestao/fontes_emissao.py`, e pela mesma razão: sem
    ele, cada origem — o SITFIS, o PDF que a pessoa baixou à mão, um agregador
    amanhã — viraria um caminho próprio até o disco, e voltaríamos a ter mais
    de um lugar gravando documento oficial.

    Uma fonte que soubesse validar seria uma segunda opinião sobre o que é
    documento válido. Duas opiniões divergem, e a divergência aparece tarde.

O DESFECHO É PARTE DA ENTREGA
    Fonte que devolve `None` para tudo que não deu certo joga fora a única
    informação que a tentativa tinha: POR QUE não deu. "Há pendência, o órgão
    recusou emitir" e "o órgão está fora do ar" pedem ações opostas — uma é
    trabalho de contabilidade, a outra é tentar de novo mais tarde.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Colheita:
    """O que uma fonte devolve. Bytes, ou o motivo de não haver bytes."""

    desfecho: str                      # ver `situacao.modelo.DESFECHOS`
    documento: bytes = b""             # os bytes crus, quando houver
    detalhe: str = ""                  # legível por gente; NUNCA credencial
    origem: str = ""                   # quem colheu: "SITFIS", "ASSISTIDA"...
    respostas: list = field(default_factory=list)   # as respostas cruas
    extra: dict = field(default_factory=dict)       # proveniência adicional

    @property
    def tem_documento(self) -> bool:
        return bool(self.documento)
