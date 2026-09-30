"""
cte_dfe.py — o adaptador do `CTeDistribuicaoDFe`. A ÚNICA porta do CT-e.

A REGRA QUE ESTE ARQUIVO EXISTE PARA SUSTENTAR
    Nenhuma tela, rota ou tarefa agendada fala com a SEFAZ de CT-e. Todas
    passam por aqui. É a mesma disciplina que o `nfe_dfe.py` já impõe à NF-e, e
    ela não é organização: é o que permite ter UM lugar onde o intervalo mínimo
    entre consultas, a espera progressiva e o bloqueio por consumo indevido são
    aplicados. Espalhados, cada chamador aplicaria os seus — e o serviço
    bloquearia o CNPJ do cliente por causa do chamador que esqueceu.

A DIFERENÇA QUE MUDA O DESENHO: NÃO EXISTE `consChCTe`
    A NF-e tem três formas de perguntar: varrer por NSU (`distNSU`), buscar um
    NSU (`consNSU`) e buscar por chave (`consChNFe`). **O CT-e tem duas.** O
    contrato oficial (`PL_CTeDistDFe_100`, já conferido em `contratos.py`) não
    oferece consulta por chave.

    A consequência é operacional e séria: se o checkpoint se perder, não há
    como pedir "aquele documento" de volta. A única recuperação é caminhar por
    NSU, e a retenção do Ambiente Nacional a partir do zero é de ~3 meses. Por
    isso o checkpoint do CT-e é tratado com mais cuidado do que o da NF-e, não
    com o mesmo.

    Este módulo **não** expõe função de consulta por chave. Não é esquecimento:
    é para que ninguém a escreva por analogia seis meses depois.

O QUE ELE FAZ E O QUE NÃO FAZ
    Faz: monta envelope, interpreta resposta, classifica `cStat`, decodifica o
    `docZip`, e diz ao motor o que fazer.

    Não faz: decidir quando consultar (é do serviço), gravar documento (é do
    portão), abrir `.pfx` ou ver senha (é da sessão). Recebe a sessão pronta.

QUEM RECEBE O QUE — E QUEM **NÃO** RECEBE
    A NT 2015.002 é sobre documentos **de interesse** do consulente. O emitente
    NÃO recebe de volta, por esta via, os documentos que ele mesmo emitiu.

    Para uma transportadora isso muda tudo: os CT-e que ela emite não vêm por
    aqui. O que vem é aquilo em que ela é **tomadora, remetente, destinatária,
    expedidora, recebedora** ou está no `autXML` — mais os eventos.

    A CTE 1 tratou os 59 CT-e emitidos da TRX como se fossem prova de captura.
    Não são: eles nunca chegariam pela distribuição. Emissão própria entra pela
    entrada canônica (ERP, pasta, e-mail, XML/ZIP).

REAPROVEITAMENTO, E ONDE ELE PARA
    A decodificação do `docZip` (base64 + gzip) e a classificação dos subtipos
    de 656 são do padrão DF-e e valem para os dois serviços. Reusá-las evita
    duas rotinas de base64+gzip que divergiriam no primeiro conserto.

    O que NÃO é reusado: envelope, endpoint, namespace, a ausência de consulta
    por chave e — desde a CTE 1.1 — **a tabela de `cStat`**.

    A tabela de `cStat` **é a do CT-e** (NT 2015.002 v1.05), e não mais a da
    NF-e. A CTE 1 reusava a da NF-e por analogia — errado nos dois sentidos: há
    códigos que só existem aqui (214, 239, 409-411, 472, 473, 489, 490, 593) e
    códigos da NF-e que não valem no CT-e (236, 578, 632).

    Código fora da tabela cai em `CATEGORIA_NAO_DOCUMENTADA` e é tratado como
    definitivo — não avança checkpoint, não repete sozinho, e mostra o texto
    literal do serviço.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ambiente import HOMOLOGACAO, PRODUCAO, Ambiente, resolver as resolver_ambiente
from ..distribuicao import normalizar_nsu
from ..identidade import normalizar
from . import nfe_dfe as _dfe

# ── o que é do padrão DF-e e vale para os dois serviços ─────────────────────
# Reexportado de propósito: quem usa este adaptador não precisa importar o da
# NF-e para ler um `cStat`.
Status = _dfe.Status
classificar_consumo_indevido = _dfe.classificar_consumo_indevido
ConsumoIndevido = _dfe.ConsumoIndevido
CertificadoRecusado = _dfe.CertificadoRecusado
Resposta = _dfe.Resposta
Transporte = _dfe.Transporte
TransporteHTTPS = _dfe.TransporteHTTPS

CAT_DOCUMENTOS = _dfe.CAT_DOCUMENTOS
CAT_SEM_DOCUMENTOS = _dfe.CAT_SEM_DOCUMENTOS
CAT_INDISPONIVEL = _dfe.CAT_INDISPONIVEL
CAT_CONSUMO_INDEVIDO = _dfe.CAT_CONSUMO_INDEVIDO
CAT_REJEICAO = _dfe.CAT_REJEICAO
CAT_AUTORIZACAO = _dfe.CAT_AUTORIZACAO
CATEGORIA_NAO_DOCUMENTADA = _dfe.CATEGORIA_NAO_DOCUMENTADA

# ── o que é do CT-e ─────────────────────────────────────────────────────────
NS_CTE = "http://www.portalfiscal.inf.br/cte"
NS_WSDL = "http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe"
NS_SOAP12 = _dfe.NS_SOAP12

# `distDFeInt` do CT-e — versão 1.00, conforme `PL_CTeDistDFe_100`. A da NF-e é
# 1.01, e mandar a versão errada é rejeição na cara.
VERSAO_DIST = "1.00"

ENDPOINT = {
    PRODUCAO.nome: ("https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/"
                    "CTeDistribuicaoDFe.asmx"),
    HOMOLOGACAO.nome: ("https://hom1.cte.fazenda.gov.br/CTeDistribuicaoDFe/"
                       "CTeDistribuicaoDFe.asmx"),
}
TP_AMB = _dfe.TP_AMB
CONTENT_TYPE = _dfe.CONTENT_TYPE

# Lote e retenção vêm do contrato conferido, não de suposição.
MAX_DOCUMENTOS_POR_LOTE = 50
RETENCAO_MESES_DO_ZERO = 3

# `consChCTe` NÃO EXISTE. A constante fica aqui, com o nome que alguém
# procuraria, para que a busca encontre a explicação em vez do silêncio.
CONSULTA_POR_CHAVE_DISPONIVEL = False


# ════════════════════════════════════════════════════════════════════════════
# 0. `cStat` — a tabela DO CT-e, não a herdada da NF-e
# ════════════════════════════════════════════════════════════════════════════
# POR QUE A HERANÇA SAIU
#     A CTE 1 reusou a tabela da NF-e "porque é a mesma família DF-e". Isso
#     estava errado por dois motivos: há códigos que só existem no CT-e (214,
#     239, 409-411, 472, 473, 489, 490, 593) e há códigos da NF-e que não
#     valem aqui (236, 578, 632). Uma tabela emprestada acerta por sorte e
#     erra em silêncio.
#
# FONTE
#     NT 2015.002 v1.05 — Distribuição de DF-e de Interesse (CT-e).
#
# O QUE NÃO MUDA
#     Código fora desta tabela cai em `CATEGORIA_NAO_DOCUMENTADA` e é tratado
#     como definitivo: **não avança checkpoint**, não repete sozinho, e mostra
#     o texto literal do serviço. Não há palpite por faixa numérica.
_NT_CTE = "NT 2015.002 v1.05 — Distribuição de DF-e de Interesse (CT-e)"


def _c(codigo, categoria, descricao):
    return _dfe.Status(codigo, categoria, descricao, True, _NT_CTE)


CSTAT_CTE: dict[str, _dfe.Status] = {
    # ── o serviço respondeu, e há ou não documento ──────────────────────
    "138": _c("138", CAT_DOCUMENTOS, "Documento localizado"),
    "137": _c("137", CAT_SEM_DOCUMENTOS, "Nenhum documento localizado"),

    # ── serviço indisponível: transitório, tenta depois ─────────────────
    "108": _c("108", CAT_INDISPONIVEL, "Serviço paralisado momentaneamente"),
    "109": _c("109", CAT_INDISPONIVEL, "Serviço paralisado sem previsão"),

    # ── consumo indevido: bloqueia, e NÃO repete sozinho ────────────────
    "656": _c("656", CAT_CONSUMO_INDEVIDO, "Rejeição: Consumo Indevido"),

    # ── rejeições da mensagem ───────────────────────────────────────────
    "214": _c("214", CAT_REJEICAO, "Rejeição: tamanho da mensagem excedeu o limite"),
    "215": _c("215", CAT_REJEICAO, "Rejeição: falha no schema XML"),
    "238": _c("238", CAT_REJEICAO,
              "Rejeição: cabeçalho — versão do arquivo XML superior à versão vigente"),
    "239": _c("239", CAT_REJEICAO,
              "Rejeição: cabeçalho — versão do arquivo XML não suportada"),
    "242": _c("242", CAT_REJEICAO, "Rejeição: elemento inválido na área de dados"),
    "252": _c("252", CAT_REJEICAO,
              "Rejeição: ambiente informado diverge do ambiente do webservice"),
    "402": _c("402", CAT_REJEICAO, "Rejeição: XML particionado"),
    "404": _c("404", CAT_REJEICAO,
              "Rejeição: uso de prefixo de namespace não permitido"),
    "409": _c("409", CAT_REJEICAO,
              "Rejeição: campo cUF inexistente no elemento de dados"),
    "410": _c("410", CAT_REJEICAO, "Rejeição: UF informada diverge da UF do webservice"),
    "411": _c("411", CAT_REJEICAO,
              "Rejeição: campo tpAmb inexistente no elemento de dados"),
    "472": _c("472", CAT_REJEICAO, "Rejeição: tipo de documento inválido"),
    "473": _c("473", CAT_REJEICAO, "Rejeição: documento inexistente"),
    "489": _c("489", CAT_REJEICAO, "Rejeição: consulta a NSU inexistente"),
    "490": _c("490", CAT_REJEICAO, "Rejeição: consulta a NSU muito antigo"),
    "589": _c("589", CAT_REJEICAO, "Rejeição: número do NSU informado superior ao maxNSU"),
    "593": _c("593", CAT_REJEICAO,
              "Rejeição: informado NSU e chave de acesso na mesma consulta"),

    # ── certificado e autorização: nunca é transitório ──────────────────
    "280": _c("280", CAT_AUTORIZACAO, "Rejeição: certificado transmissor inválido"),
    "281": _c("281", CAT_AUTORIZACAO,
              "Rejeição: certificado transmissor — data de validade"),
    "283": _c("283", CAT_AUTORIZACAO,
              "Rejeição: certificado transmissor sem CNPJ"),
    "284": _c("284", CAT_AUTORIZACAO, "Rejeição: certificado transmissor — erro na cadeia"),
    "285": _c("285", CAT_AUTORIZACAO, "Rejeição: certificado transmissor — CNPJ inválido"),
    "286": _c("286", CAT_AUTORIZACAO, "Rejeição: certificado transmissor revogado"),
}

# `489` e `490` merecem nome próprio: são as duas formas de "esse NSU não dá
# para buscar", e a segunda é a que revela a janela de retenção.
NSU_INEXISTENTE = "489"
NSU_MUITO_ANTIGO = "490"

# A janela do Ambiente Nacional a partir do zero. Não é escolha nossa: é o que
# a NT define, e é o que faz `ultNSU=0` NÃO significar "todo o histórico".
RETENCAO_MESES = 3

DESCONHECIDO_CTE = _dfe.Status(
    "", CATEGORIA_NAO_DOCUMENTADA,
    "código não consta da tabela oficial do CT-e conferida", False,
    "nenhuma — não inventar significado")


def status_de(cstat: str) -> _dfe.Status:
    """O `cStat` do CT-e como dado, pela tabela DELE.

    Substitui a consulta à tabela da NF-e, que a CTE 1 reusava por analogia.
    Código fora desta lista vira `NAO_DOCUMENTADO` — sem palpite por faixa.
    """
    c = str(cstat or "").strip()
    achado = CSTAT_CTE.get(c)
    if achado:
        return achado
    return _dfe.Status(c, CATEGORIA_NAO_DOCUMENTADA, DESCONHECIDO_CTE.descricao,
                       False, DESCONHECIDO_CTE.fonte)


class ConsultaNaoSuportada(NotImplementedError):
    """Pedida uma consulta que o serviço de CT-e não oferece."""


# ════════════════════════════════════════════════════════════════════════════
# 1. Envelope — função pura, sem rede
# ════════════════════════════════════════════════════════════════════════════
def _corpo(consulta: str, identidade: str, tp_amb: str, cuf: str) -> bytes:
    ident = normalizar(identidade)
    if not ident.valido:
        raise ValueError(f"identidade fiscal inválida: {ident.motivo}")
    marca = "CNPJ" if ident.tipo == "CNPJ" else "CPF"
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap12:Envelope xmlns:soap12="{NS_SOAP12}"><soap12:Body>'
        f'<cteDistDFeInteresse xmlns="{NS_WSDL}"><cteDadosMsg>'
        f'<distDFeInt xmlns="{NS_CTE}" versao="{VERSAO_DIST}">'
        f'<tpAmb>{tp_amb}</tpAmb><cUFAutor>{cuf}</cUFAutor>'
        f'<{marca}>{ident.valor}</{marca}>'
        f'{consulta}'
        '</distDFeInt></cteDadosMsg></cteDistDFeInteresse>'
        '</soap12:Body></soap12:Envelope>'
    ).encode("utf-8")


def montar_envelope_dist_nsu(identidade: str, ult_nsu: str, ambiente: Ambiente,
                             cuf: str) -> bytes:
    """Varredura sequencial a partir de um NSU — o modo normal de operação.

    `cuf` é o código da UF do AUTOR da consulta (o escritório ou o cliente que
    consulta), já resolvido por quem chama. Não há default: foi exatamente por
    default que o valor errado entrou na NF-e.
    """
    return _corpo(f"<distNSU><ultNSU>{normalizar_nsu(ult_nsu)}</ultNSU></distNSU>",
                  identidade, TP_AMB[ambiente.nome], _dfe._validar_cuf(cuf))


def montar_envelope_cons_nsu(identidade: str, nsu: str, ambiente: Ambiente,
                             cuf: str) -> bytes:
    """Busca UM NSU específico — para fechar lacuna, não para varrer.

    No CT-e esta é a **única** recuperação pontual que existe: sem
    `consChCTe`, quem perdeu um documento só o alcança sabendo o NSU dele.
    """
    return _corpo(f"<consNSU><NSU>{normalizar_nsu(nsu)}</NSU></consNSU>",
                  identidade, TP_AMB[ambiente.nome], _dfe._validar_cuf(cuf))


def montar_envelope_cons_chave(*_a, **_kw):
    """Não existe no CT-e. Levanta, e diz o que fazer no lugar.

    Esta função existe SÓ para falhar com explicação. Sem ela, quem escrevesse
    `montar_envelope_cons_chave` por analogia com a NF-e receberia um
    `AttributeError` seco e concluiria que faltou implementar — quando o que
    falta é no serviço, não aqui.
    """
    raise ConsultaNaoSuportada(
        "o CTeDistribuicaoDFe não oferece consulta por chave (`consChCTe` não "
        "existe no contrato PL_CTeDistDFe_100). A única recuperação pontual é "
        "`consNSU`, por NSU. Ver `contratos.CTE57`.")


# ════════════════════════════════════════════════════════════════════════════
# 2. Interpretação da resposta — função pura, sem rede
# ════════════════════════════════════════════════════════════════════════════
def interpretar_resposta(corpo: bytes) -> Resposta:
    """A resposta do CT-e tem a mesma FORMA da NF-e: `retDistDFeInt`.

    O envelope de ida difere (operação e namespace); o de volta, não — os dois
    serviços devolvem `retDistDFeInt` com `cStat`, `ultNSU`, `maxNSU` e
    `loteDistDFeInt` de `docZip`. Reusar a extração é correto: duplicá-la
    criaria duas rotinas de base64+gzip que divergiriam no primeiro conserto.

    **O que NÃO se reusa é o significado do `cStat`.** A `Resposta` volta com o
    status recalculado pela tabela do CT-e — senão um 489 (NSU inexistente)
    seria lido pela tabela da NF-e, onde ele não existe, e viraria
    "não documentado" à toa.
    """
    from dataclasses import replace
    r = _dfe.interpretar_resposta(corpo)
    try:
        return replace(r, status=status_de(r.cstat))
    except Exception:
        return r


def resposta_para_lote(r: Resposta):
    """`Resposta` → `Lote`, aplicando a categoria do `cStat`.

    É aqui que a taxonomia vira comportamento: consumo indevido levanta,
    serviço indisponível levanta como transitório, rejeição levanta como
    definitivo, e nenhum deles avança checkpoint.
    """
    return _dfe.resposta_para_lote(r)


# ════════════════════════════════════════════════════════════════════════════
# 3. Transporte — a única parte que fala TCP
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class ClienteCTe:
    """O adaptador completo: envelope + envio + interpretação.

    Recebe a sessão JÁ autenticada. Este objeto não abre `.pfx`, não vê senha e
    não sabe onde o certificado está — a senha existe apenas dentro de
    `sessao.criar_sessao`, e é apagada lá.
    """
    transporte: Transporte
    ambiente: Ambiente = PRODUCAO

    @property
    def url(self) -> str:
        return ENDPOINT[self.ambiente.nome]

    def distribuir(self, identidade: str, ult_nsu: str, cuf: str) -> Resposta:
        corpo = montar_envelope_dist_nsu(identidade, ult_nsu, self.ambiente, cuf)
        return interpretar_resposta(self.transporte.enviar(self.url, corpo))

    def consultar_nsu(self, identidade: str, nsu: str, cuf: str) -> Resposta:
        corpo = montar_envelope_cons_nsu(identidade, nsu, self.ambiente, cuf)
        return interpretar_resposta(self.transporte.enviar(self.url, corpo))

    def consultar_chave(self, *_a, **_kw):
        return montar_envelope_cons_chave()


TIMEOUT_LEITURA = _dfe.TIMEOUT_LEITURA


def criar(cad, identificador, dados_dir=None, ambiente=PRODUCAO,
          timeout_leitura: int = TIMEOUT_LEITURA) -> ClienteCTe:
    """Monta o cliente REAL do CT-e a partir do cadastro. Igual ao da NF-e.

    Confere ANTES de qualquer consulta que existe credencial utilizável para
    ESTA empresa. No CT-e isso pesa mais do que na NF-e: gastar a consulta e
    voltar com rejeição de certificado deixa a empresa em espera sem ter
    trazido nada — e o CT-e não tem consulta por chave para compensar depois.

    A senha do `.pfx` existe apenas dentro de `sessao.criar_sessao`, e é
    apagada lá. Nem esta função nem o `ClienteCTe` a veem.
    """
    from .. import credencial_estado as ce
    from .. import sessao as sess

    amb = resolver_ambiente(ambiente)
    ident = normalizar(identificador)
    if not ident.valido:
        raise ValueError(f"identidade fiscal inválida: {ident.motivo}")

    empresa = cad.obter_empresa(ident.valor)
    if empresa is None:
        raise ValueError(f"empresa {ident.mascarado()} não está no cadastro")

    cred = cad.obter_certificado_da_empresa(ident.valor)
    if cred is None:
        raise ValueError(
            f"empresa {ident.mascarado()} não tem certificado associado")

    raiz = dados_dir if dados_dir is not None else getattr(cad, "dados_dir", None)
    estado = ce.avaliar(cred, raiz)
    if not estado.utilizavel:
        raise ValueError(
            f"credencial de {ident.mascarado()} não está utilizável: "
            f"{estado.acao_necessaria}")

    sessao = sess.criar_sessao(cred, raiz, amb, timeout=timeout_leitura)
    # O `cUFAutor` NÃO entra aqui: ele vai por argumento em `distribuir()`,
    # porque é de quem CONSULTA — o escritório — e nunca da UF da empresa
    # consultada. Ver `autor_consulta.py`.
    return ClienteCTe(
        transporte=TransporteHTTPS(sessao=sessao,
                                   timeout_leitura=timeout_leitura),
        ambiente=amb)
