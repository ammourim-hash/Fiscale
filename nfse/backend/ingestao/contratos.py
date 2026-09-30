"""
contratos.py — o que os serviços oficiais oferecem, como DADO verificável.

POR QUE ISTO É CÓDIGO E NÃO SÓ DOCUMENTAÇÃO
    "O CT-e não tem consulta por chave" é o tipo de fato que se esquece. Daqui a
    seis meses alguém escreve `consChCTe` por analogia com a NF-e, o serviço
    recusa, e o erro volta como "falha de comunicação". Declarado aqui, o teste
    trava a suposição errada antes de ela virar código.

NADA AQUI FAZ CHAMADA
    Este módulo é uma tabela. Não abre conexão, não importa `requests`. Endpoint
    declarado não é endpoint chamado — a ING 1 não coleta nada.

PROCEDÊNCIA
    Tudo conferido em fonte oficial em 13/08/2026, não de memória:

    • NF-e — pacote de schemas `PL_NFeDistDFe_104` (Portal da NF-e).
    • CT-e — pacote `PL_CTeDistDFe_100` e a Relação de Serviços Web
      (Portal do CT-e), que dá o endereço do Ambiente Nacional.
    • NFS-e — página de APIs e o Manual de Contribuintes das APIs do ADN
      (gov.br/nfse); a exigência de mTLS foi confirmada na resposta dos próprios
      endpoints (HTTP 496 SSL Certificate Required, inclusive na documentação).

    Onde a fonte oficial não afirma, o campo fica `None` e o nome diz
    `nao_confirmado`. Não se preenche por analogia.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SERVICO_NFE55 = "nfe55"
SERVICO_CTE57 = "cte57"
SERVICO_NFSE_NACIONAL = "nfse_nacional"


@dataclass(frozen=True)
class ContratoServico:
    servico: str
    descricao: str
    protocolo: str                      # "SOAP" | "REST"
    namespace: str = ""
    endpoint_producao: str = ""
    endpoint_homologacao: str = ""
    pacote_schemas: str = ""
    consultas: tuple[str, ...] = ()     # formas de consulta oficialmente aceitas
    consulta_por_chave: bool = False    # existe recuperação pontual por chave?
    max_documentos_por_lote: int | None = None
    retencao_meses_do_zero: int | None = None   # o que volta quando se pede do NSU 0
    exige_mtls: bool = True
    nao_disponivel: tuple[str, ...] = ()
    nao_confirmado: tuple[str, ...] = ()
    fonte: str = ""
    observacoes: tuple[str, ...] = field(default_factory=tuple)


NFE55 = ContratoServico(
    servico=SERVICO_NFE55,
    descricao="NF-e modelo 55 — Distribuição de DF-e de Interesse (Ambiente Nacional)",
    protocolo="SOAP",
    namespace="http://www.portalfiscal.inf.br/nfe",
    endpoint_producao="https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    pacote_schemas="PL_NFeDistDFe_104",
    consultas=("distNSU", "consNSU", "consChNFe"),
    consulta_por_chave=True,
    max_documentos_por_lote=50,
    retencao_meses_do_zero=3,
    fonte="Portal da NF-e — pacote de schemas PL_NFeDistDFe_104 (distDFeInt_v1.01.xsd)",
    observacoes=(
        "Em produção no FISCALE desde antes da ING 1; hoje usa somente distNSU.",
        "consChNFe existe oficialmente e permite reprocessar um documento por chave.",
    ),
    nao_confirmado=(
        "endereço do ambiente de homologação (não levantado nesta pesquisa)",
    ),
)

CTE57 = ContratoServico(
    servico=SERVICO_CTE57,
    descricao="CT-e modelo 57 — Distribuição de DF-e de Interesse (Ambiente Nacional)",
    protocolo="SOAP",
    namespace="http://www.portalfiscal.inf.br/cte",
    endpoint_producao="https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx",
    pacote_schemas="PL_CTeDistDFe_100",
    consultas=("distNSU", "consNSU"),
    consulta_por_chave=False,           # 🔴 a diferença que importa
    max_documentos_por_lote=50,
    retencao_meses_do_zero=3,
    fonte="Portal do CT-e — Relação de Serviços Web + pacote PL_CTeDistDFe_100",
    nao_disponivel=(
        "consChCTe — não existe consulta por chave no contrato oficial analisado; "
        "a única recuperação pontual é consNSU, por NSU",
    ),
    observacoes=(
        "Versão do serviço: 1.00. NSU tem 15 dígitos (TNSU: [0-9]{15}).",
        "docZip vem em gZip codificado em base64; o atributo @schema identifica "
        "tipo e versão do documento (ex.: procCTe_v2.00.xsd, procEventoCTe_v2.00.xsd).",
        "Sem consulta por chave, perder o checkpoint pode tornar impossível "
        "reconstruir o histórico só pelo Ambiente Nacional — ver D30.",
        "Hoje o FISCALE só recebe CT-e por importação manual de XML; não há cliente "
        "deste serviço. Implementá-lo é a ING 4, não a ING 1.",
    ),
)

NFSE_NACIONAL = ContratoServico(
    servico=SERVICO_NFSE_NACIONAL,
    descricao="NFS-e Nacional — distribuição de DF-e ao contribuinte (ADN)",
    protocolo="REST",
    namespace="http://www.sped.fazenda.gov.br/nfse",
    endpoint_producao="https://adn.nfse.gov.br",
    endpoint_homologacao="https://adn.producaorestrita.nfse.gov.br",
    pacote_schemas="NFSe-ESQUEMAS_XSD-v1.01-20260209",
    consultas=("GET /contribuintes/DFe/{NSU}", "GET /NFSe/{ChaveAcesso}/Eventos"),
    consulta_por_chave=True,            # por chave, mas só para EVENTOS
    max_documentos_por_lote=None,
    retencao_meses_do_zero=None,
    fonte="gov.br/nfse — APIs Prod. Restrita e Produção + Manual de Contribuintes das APIs do ADN",
    nao_disponivel=(
        "consulta por intervalo de datas — a distribuição é sequencial por NSU",
    ),
    nao_confirmado=(
        "limite de retenção equivalente aos 3 meses da NF-e/CT-e",
        "tamanho máximo do LoteDFe — tratar o lote como variável é o seguro",
    ),
    observacoes=(
        "A rota em produção (core.py: /contribuintes/DFe/{nsu}) casa com a "
        "operação GET /DFe/{NSU} do manual oficial.",
        "Sem certificado cliente o ADN responde HTTP 496 até na documentação "
        "Swagger — mTLS é condição de acesso, não detalhe de implementação.",
        "GET /NFSe/{ChaveAcesso}/Eventos ainda não é usado; serve para conferir "
        "cancelamento/substituição de uma nota específica.",
    ),
)

TODOS = (NFE55, CTE57, NFSE_NACIONAL)
_POR_SERVICO = {c.servico: c for c in TODOS}

# Ponte entre o nome do contrato e o nome do serviço usado na chave de
# checkpoint (`checkpoint.SERVICOS`). São vocabulários diferentes de propósito:
# o contrato descreve o documento fiscal, o checkpoint descreve a operação de
# distribuição. Manter os dois amarrados aqui evita que divirjam.
SERVICO_CHECKPOINT = {
    SERVICO_NFE55: "NFE_DISTRIBUICAO",
    SERVICO_CTE57: "CTE_DISTRIBUICAO",
    SERVICO_NFSE_NACIONAL: "NFSE_DISTRIBUICAO",
}
CHECKPOINT_SERVICO = {v: k for k, v in SERVICO_CHECKPOINT.items()}


def contrato_do_checkpoint(servico_checkpoint: str) -> ContratoServico:
    """`'CTE_DISTRIBUICAO'` -> o contrato oficial do CT-e."""
    nome = CHECKPOINT_SERVICO.get(str(servico_checkpoint or "").strip().upper())
    if nome is None:
        raise ValueError(f"serviço de checkpoint desconhecido: {servico_checkpoint!r}")
    return obter(nome)


def obter(servico: str) -> ContratoServico:
    c = _POR_SERVICO.get(str(servico or "").strip().lower())
    if c is None:
        raise ValueError(f"serviço desconhecido: {servico!r}")
    return c


def suporta_consulta(servico: str, forma: str) -> bool:
    """`suporta_consulta('cte57', 'consChCTe')` é False — e o teste garante."""
    return forma in obter(servico).consultas
