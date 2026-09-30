"""
ambiente.py — produção e homologação como conceito explícito.

POR QUE ISTO EXISTE ANTES DE QUALQUER COLETA
    Os checkpoints de hoje (`<cnpj>/estado.json` e `<cnpj>/nfe/estado.json`)
    guardam o NSU e **não guardam o ambiente**. Uma varredura em homologação
    moveria o ponteiro de produção, e a empresa passaria a pular documentos
    reais — silenciosamente, porque o sistema continuaria dizendo que está em
    dia. O ambiente precisa existir como conceito antes de existir coleta.

    Decisão D29: a chave de estado de distribuição é sempre
    `(cnpj, serviço, ambiente)`. NSU jamais é reaproveitado entre ambientes.

CADA FISCO CHAMA DE UM JEITO
    NF-e e CT-e usam `tpAmb`: 1 = Produção, 2 = Homologação.
    A NFS-e Nacional usa dois hosts: produção e "produção restrita".
    São o mesmo conceito com nomes diferentes; a tradução mora aqui, e não
    espalhada por cada conector.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ambiente:
    nome: str          # "producao" | "homologacao" — usado em nome de arquivo
    tp_amb: str        # "1" | "2"  — NF-e / CT-e (campo tpAmb do XML)
    rotulo_adn: str    # "producao" | "restrita" — chave de BASES_ADN da NFS-e
    descricao: str

    def __str__(self) -> str:
        return self.nome

    @property
    def e_producao(self) -> bool:
        return self.nome == "producao"


PRODUCAO = Ambiente(
    nome="producao", tp_amb="1", rotulo_adn="producao",
    descricao="Produção — documentos fiscais reais",
)

HOMOLOGACAO = Ambiente(
    nome="homologacao", tp_amb="2", rotulo_adn="restrita",
    descricao="Homologação / produção restrita — testes, sem valor fiscal",
)

TODOS = (PRODUCAO, HOMOLOGACAO)
_POR_NOME = {a.nome: a for a in TODOS}

# Apelidos que aparecem no código e na documentação oficial. "restrita" é como
# a NFS-e Nacional chama o ambiente de teste; "2" é como o XML da NF-e chama.
_APELIDOS = {
    "producao": PRODUCAO, "produção": PRODUCAO, "prod": PRODUCAO, "1": PRODUCAO,
    "homologacao": HOMOLOGACAO, "homologação": HOMOLOGACAO, "homolog": HOMOLOGACAO,
    "restrita": HOMOLOGACAO, "producaorestrita": HOMOLOGACAO, "2": HOMOLOGACAO,
}


def resolver(valor, padrao: Ambiente | None = None) -> Ambiente:
    """Aceita 'producao', 'restrita', '1', '2'… e devolve o Ambiente.

    Sem padrão explícito, **não** assume produção: levanta. Assumir produção
    por omissão é exatamente como um teste acabaria mexendo em dado real.
    """
    if isinstance(valor, Ambiente):
        return valor
    chave = str(valor or "").strip().lower()
    if not chave:
        if padrao is not None:
            return padrao
        raise ValueError("ambiente não informado (esperado 'producao' ou 'homologacao')")
    amb = _APELIDOS.get(chave)
    if amb is None:
        raise ValueError(f"ambiente desconhecido: {valor!r}")
    return amb
