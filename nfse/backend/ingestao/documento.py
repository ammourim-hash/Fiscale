"""
documento.py — a representação normalizada. Núcleo comum + extensão por tipo.

DUAS REGRAS QUE O TIPO INTEIRO EXISTE PARA IMPOR

1. **`Decimal`, nunca `float`.** Valor fiscal em `float` erra centavo, e centavo
   errado em apuração vira divergência de guia. `dinheiro()` é o único caminho
   de entrada, e ele recusa `float` em vez de converter — converter esconderia o
   erro em vez de mostrá-lo.

2. **Ausente ≠ zero.** Campo tributário que o XML não informou fica `None`.
   `Decimal("0")` significa "o documento disse zero"; `None` significa "o
   documento não disse". A apuração precisa distinguir os dois: a primeira é
   uma operação sem aquele tributo, a segunda é informação que falta.

POR QUE NÚCLEO + EXTENSÃO, E NÃO UMA TABELA SÓ
    NFS-e não tem CFOP nem NCM. CT-e tem cinco papéis de participante, que a
    NF-e não tem. Espremer os três num conjunto único de campos produz uma
    estrutura em que a maioria das colunas é nula e nenhuma consulta é
    confiável. O núcleo é o que dá para perguntar de todos; a extensão é o que
    só faz sentido para um.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# ── espécies e papéis ───────────────────────────────────────────────────────
NFE55 = "NFE55"
NFCE65 = "NFCE65"      # cupom eletrônico: mesma estrutura, modelo 65
EVENTO_NFE = "EVENTO_NFE"
# CT-e modelo 57. Espécie PRÓPRIA, não uma variação da NF-e: outro leiaute,
# outro serviço de distribuição, outro checkpoint — e, sobretudo, outros
# participantes. Uma nota tem emitente e destinatário; um conhecimento de
# transporte tem seis papéis possíveis, e o TOMADOR pode ser qualquer um
# deles (CTE 1).
CTE57 = "CTE57"
EVENTO_CTE = "EVENTO_CTE"
# CT-e OS, CT-e Simplificado e GTV-e: preservados, ainda não lidos.
CTE_NAO_SUPORTADO = "CTE_NAO_SUPORTADO"

# ── papéis da empresa NO documento ──────────────────────────────────────────
# Vêm da estrutura oficial do XML, nunca de inferência: `emit`, `dest`,
# `transporta` e `autXML`. A NF-e 1 mediu o custo de não distinguir: nas duas
# transportadoras do escritório, 93% dos XML completos são frete de terceiros,
# e chamá-los de `TERCEIRO` fazia R$ 82,9 milhões parecerem compra.
EMITENTE = "EMITENTE"
DESTINATARIO = "DESTINATARIO"
TRANSPORTADOR = "TRANSPORTADOR"
AUTXML = "AUTXML"
OUTRO = "OUTRO"

# `TERCEIRO` foi o nome antigo do que hoje é `OUTRO`. Continua existindo porque
# há índice em disco gravado com ele; a leitura traduz, a escrita usa `OUTRO`.
TERCEIRO = "TERCEIRO"

PAPEIS = (EMITENTE, DESTINATARIO, TRANSPORTADOR, AUTXML, OUTRO)

# ── papéis que só o CT-e tem ────────────────────────────────────────────────
# O leiaute do CT-e nomeia participantes que não existem na NF-e, e cada um
# responde uma pergunta diferente sobre a mesma viagem:
#
#   REMETENTE    quem entregou a carga ao transportador
#   EXPEDIDOR    quem a entregou em nome do remetente (redespacho)
#   RECEBEDOR    quem a recebe no destino, quando não é o destinatário
#   TOMADOR      **quem paga o frete** — e é o papel fiscalmente decisivo
#
# O TOMADOR é o que mais importa e o mais fácil de errar: ele não é um bloco
# próprio no XML. O campo `toma` aponta para um dos outros (0=remetente,
# 1=expedidor, 2=recebedor, 3=destinatário); só quando é `4` (`toma4`) vem um
# bloco com CNPJ próprio. Ler `toma4` e ignorar o `toma` faria o tomador sumir
# na maioria dos documentos — que é justamente quem lança o crédito.
REMETENTE = "REMETENTE"
EXPEDIDOR = "EXPEDIDOR"
RECEBEDOR = "RECEBEDOR"
TOMADOR = "TOMADOR"

PAPEIS_CTE = (EMITENTE, REMETENTE, DESTINATARIO, EXPEDIDOR, RECEBEDOR,
              TOMADOR, AUTXML, OUTRO)

# `toma` (0..3) → o participante apontado. Fechado de propósito: um valor fora
# desta tabela é ausência de tomador conhecido, não um palpite.
TOMADOR_POR_CODIGO = {"0": REMETENTE, "1": EXPEDIDOR,
                      "2": RECEBEDOR, "3": DESTINATARIO}

# O papel PRINCIPAL, quando a empresa aparece em mais de um. A ordem não é
# arbitrária: emitir é mais forte que receber, receber é mais forte que
# transportar, e estar no `autXML` é o mais fraco — é permissão de acesso, não
# participação na operação.
PRECEDENCIA_PAPEL = (EMITENTE, DESTINATARIO, TRANSPORTADOR, AUTXML, OUTRO)

# No CT-e a ordem é outra, e a razão é fiscal: depois de emitir, o que decide o
# tratamento do documento é **quem paga o frete**. Tomar vem antes de ser
# remetente ou destinatário — dá crédito, entra na apuração, e é o papel pelo
# qual o contador procura o documento.
PRECEDENCIA_PAPEL_CTE = (EMITENTE, TOMADOR, REMETENTE, DESTINATARIO,
                         EXPEDIDOR, RECEBEDOR, AUTXML, OUTRO)


def papel_principal(papeis, *, cte: bool = False) -> str:
    """O mais forte entre os papéis encontrados. `OUTRO` quando não há nenhum.

    `cte=True` usa a precedência do conhecimento de transporte, em que TOMADOR
    vem logo depois de EMITENTE.
    """
    achados = set(papeis or ())
    for p in (PRECEDENCIA_PAPEL_CTE if cte else PRECEDENCIA_PAPEL):
        if p in achados:
            return p
    return OUTRO

# ── situações ───────────────────────────────────────────────────────────────
AUTORIZADO = "AUTORIZADO"
CANCELADO = "CANCELADO"
DENEGADO = "DENEGADO"
SEM_PROTOCOLO = "SEM_PROTOCOLO"
INDEFINIDA = "INDEFINIDA"


# ── que evento cancela ──────────────────────────────────────────────────────
# Mora AQUI, junto das situações, e não no portão de importação. O critério é
# consultado por dois lados que não podem divergir: a leitura do acervo (que
# abre os XML) e a consulta indexada (que lê a tabela `eventos`). Se um deles
# tivesse de importar o portão para saber o que é cancelamento, a camada de
# LEITURA passaria a depender da de ESCRITA — e a guarda de consumidores do
# portão, com razão, reprovaria.
CANCELAMENTOS = frozenset({"110111", "110112"})


def e_cancelamento(tp_evento: str, protocolo: str = "",
                   *, exigir_protocolo: bool = True) -> bool:
    """Este evento cancela a nota?

    O PROTOCOLO É EXIGIDO
        Cancelamento sem protocolo é pedido, não homologação. Nos dados reais
        do escritório os 14 eventos de cancelamento têm protocolo — mas aceitar
        um sem seria transformar "alguém tentou cancelar" em "está cancelada",
        que é outra afirmação.

        `exigir_protocolo=False` existe para o caminho do acervo, onde a
        IDENTIFICAÇÃO (a casca do XML) responde antes de o parser ter lido o
        `nProt`. Ali o tipo do evento é tudo o que se sabe naquele ponto, e
        exigir protocolo recusaria o evento por uma informação que ainda não
        foi extraída — não por ela faltar no documento.
    """
    if str(tp_evento or "").strip() not in CANCELAMENTOS:
        return False
    if exigir_protocolo:
        return bool(str(protocolo or "").strip())
    return True


class ValorInvalido(ValueError):
    """O texto não é um valor monetário utilizável."""


def dinheiro(v, campo: str = "") -> Decimal | None:
    """Converte para `Decimal` preservando a precisão do XML.

    Devolve `None` quando o campo não veio — e é isso que separa "não informado"
    de "informado como zero".

    **Recusa `float` de propósito.** `Decimal(0.1)` produz
    `0.1000000000000000055511151231257827021181583404541015625`; aceitar float
    aqui espalharia esse erro silenciosamente por toda a apuração. Quem tiver um
    float em mãos precisa decidir conscientemente como convertê-lo.
    """
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool) or isinstance(v, float):
        raise ValorInvalido(
            f"valor fiscal não aceita {type(v).__name__}"
            f"{' em ' + campo if campo else ''}: use str ou Decimal")
    if isinstance(v, int):
        return Decimal(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError) as exc:
        raise ValorInvalido(
            f"valor monetário inválido{' em ' + campo if campo else ''}") from exc


def soma(*valores) -> Decimal | None:
    """Soma ignorando ausentes. Devolve `None` se TODOS forem ausentes.

    Somar tratando `None` como zero produziria total 0,00 para um documento que
    simplesmente não informou nada — número que parece resposta e não é."""
    presentes = [v for v in valores if v is not None]
    if not presentes:
        return None
    total = Decimal("0")
    for v in presentes:
        total += v
    return total


# ── partes ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Participante:
    """Emitente, destinatário, tomador — a mesma forma para todos."""
    identificador: str = ""          # CNPJ/CPF canônico (só dígitos)
    tipo: str = ""                   # CNPJ | CPF | ""
    nome: str = ""
    ie: str = ""
    uf: str = ""
    municipio: str = ""
    codigo_municipio: str = ""

    def mascarado(self) -> str:
        """Nunca imprima o identificador inteiro: CPF é dado pessoal."""
        v = self.identificador
        if self.tipo == "CNPJ" and len(v) == 14:
            return f"{v[:2]}.***.***/{v[8:12]}-{v[12:]}"
        if self.tipo == "CPF" and len(v) == 11:
            return f"***.{v[3:6]}.***-{v[9:]}"
        return "<sem identificador>"


@dataclass(frozen=True)
class Tributo:
    """Um tributo. `None` em qualquer campo = o documento não informou."""
    base: Decimal | None = None
    aliquota: Decimal | None = None
    valor: Decimal | None = None
    # CST e CSOSN são CAMPOS DIFERENTES, e sempre foram.
    #
    # Até a NF-e 4B o parser fazia `cst = CST or CSOSN`, e o resultado é que
    # `102` chegava ao índice sem dizer se era CST 102 ou CSOSN 102 — que
    # significam coisas distintas e levam a conclusões fiscais opostas. Para o
    # auditor responder "este CSOSN é compatível?", ele precisa saber qual dos
    # dois campos o documento preencheu.
    cst: str = ""
    csosn: str = ""
    # Origem da mercadoria (`orig`): nacional, importada, etc. Vive no grupo do
    # ICMS e vale para o item inteiro.
    origem: str = ""
    # Base e alíquota por QUANTIDADE — PIS/COFINS têm modalidade específica
    # (`PISQtde`), com alíquota em reais por unidade. Forçar essa modalidade na
    # fórmula percentual produziria número errado, então ela tem campo próprio.
    quantidade_base: Decimal | None = None
    aliquota_por_unidade: Decimal | None = None
    # Enquadramento do IPI (`cEnq`).
    enquadramento: str = ""

    @property
    def classificacao(self) -> str:
        """O código que o documento realmente informou — CST ou CSOSN.

        Existe para quem só quer exibir; quem vai DECIDIR precisa saber qual
        dos dois é, e por isso lê os campos separados."""
        return self.cst or self.csosn

    @property
    def informado(self) -> bool:
        return any(v is not None for v in (self.base, self.aliquota, self.valor))


@dataclass(frozen=True)
class TributoIcms:
    """O ICMS tem mais campos que os outros, e eles importam.

    ST, FCP, redução de base, diferimento e desoneração mudam o que a empresa
    pode ou não creditar. Guardar só base/alíquota/valor perderia justamente o
    que distingue uma operação normal de uma com substituição.
    """
    cst: str = ""
    csosn: str = ""
    origem: str = ""
    modalidade_base: str = ""
    base: Decimal | None = None
    aliquota: Decimal | None = None
    valor: Decimal | None = None
    reducao_base: Decimal | None = None
    # Substituição tributária
    base_st: Decimal | None = None
    aliquota_st: Decimal | None = None
    valor_st: Decimal | None = None
    reducao_base_st: Decimal | None = None
    margem_st: Decimal | None = None
    # Fundo de Combate à Pobreza
    base_fcp: Decimal | None = None
    aliquota_fcp: Decimal | None = None
    valor_fcp: Decimal | None = None
    # Diferimento e desoneração
    aliquota_diferimento: Decimal | None = None
    valor_diferido: Decimal | None = None
    valor_desonerado: Decimal | None = None
    motivo_desoneracao: str = ""

    @property
    def classificacao(self) -> str:
        return self.cst or self.csosn


@dataclass(frozen=True)
class TributoReforma:
    """IBS, CBS e Imposto Seletivo — os grupos da Reforma Tributária.

    NÃO É PREPARAÇÃO PARA O FUTURO: já chega hoje. Medido em 23/08/2026 sobre
    o acervo real, `gIBSCBS` aparece em 627 de 800 XML completos amostrados, e
    `vIS` em 5. O parser anterior ignorava esses grupos em silêncio — o dado
    entrava no acervo e não chegava a lugar nenhum.

    Aqui só se LÊ e PRESERVA. Nada é calculado, nada é conferido e nada é
    inventado para documento que não traz o grupo: ausente continua `None`.
    """
    cst: str = ""                       # CST do grupo IBS/CBS (3 dígitos)
    classificacao_tributaria: str = ""  # `cClassTrib`
    base: Decimal | None = None
    # IBS se reparte entre UF e município, e os dois vêm separados no XML.
    aliquota_ibs_uf: Decimal | None = None
    valor_ibs_uf: Decimal | None = None
    aliquota_ibs_mun: Decimal | None = None
    valor_ibs_mun: Decimal | None = None
    valor_ibs: Decimal | None = None
    aliquota_cbs: Decimal | None = None
    valor_cbs: Decimal | None = None
    # Imposto Seletivo
    cst_is: str = ""
    base_is: Decimal | None = None
    aliquota_is: Decimal | None = None
    valor_is: Decimal | None = None

    @property
    def presente(self) -> bool:
        """O documento trouxe algum grupo da Reforma?"""
        return bool(self.cst or self.classificacao_tributaria
                    or self.base is not None or self.valor_ibs is not None
                    or self.valor_cbs is not None or self.valor_is is not None)


@dataclass(frozen=True)
class ItemNFe:
    numero: int = 0
    codigo: str = ""
    descricao: str = ""
    ncm: str = ""
    cest: str = ""
    cfop: str = ""
    unidade: str = ""
    quantidade: Decimal | None = None
    valor_unitario: Decimal | None = None
    valor_produto: Decimal | None = None
    desconto: Decimal | None = None
    icms: TributoIcms = field(default_factory=TributoIcms)
    ipi: Tributo = field(default_factory=Tributo)
    pis: Tributo = field(default_factory=Tributo)
    cofins: Tributo = field(default_factory=Tributo)
    reforma: TributoReforma = field(default_factory=TributoReforma)


@dataclass(frozen=True)
class TotaisNFe:
    """Totais do `ICMSTot` e do `IBSCBSTot`. Todos opcionais — nem toda NF-e
    informa todos, e NF-e anterior à Reforma não informa os três últimos."""
    base_icms: Decimal | None = None
    icms: Decimal | None = None
    base_icms_st: Decimal | None = None
    icms_st: Decimal | None = None
    fcp: Decimal | None = None
    produtos: Decimal | None = None
    frete: Decimal | None = None
    seguro: Decimal | None = None
    desconto: Decimal | None = None
    ipi: Decimal | None = None
    pis: Decimal | None = None
    cofins: Decimal | None = None
    outros: Decimal | None = None
    total: Decimal | None = None

    # ── Reforma Tributária: o total DECLARADO pelo emitente ────────────────
    # Vem de `<total><IBSCBSTot>`, irmão do `ICMSTot`. O nome da tag é
    # `IBSCBSTot` — `gIBSCBSTot` não existe no leiaute.
    #
    # Medido no acervo real em 22/09/2026: dos 7.772 XML de NF-e, 7.470 trazem
    # `IBSCBSTot` (96,1%), e a correlação com o grupo do item é perfeita —
    # nenhum documento traz um sem o outro, nos dois sentidos.
    #
    # Ficam AO LADO dos totais antigos, nunca no lugar deles: PIS e COFINS
    # convivem com a CBS durante toda a transição.
    base_ibs_cbs: Decimal | None = None   # `vBCIBSCBS`
    ibs: Decimal | None = None            # `gIBS/vIBS`
    cbs: Decimal | None = None            # `gCBS/vCBS`

    @property
    def reforma_presente(self) -> bool:
        """O documento declarou o total da Reforma?"""
        return (self.base_ibs_cbs is not None or self.ibs is not None
                or self.cbs is not None)


# ── conferência da Reforma: estados possíveis ──────────────────────────────
# São seis porque "não confere" e "não dá para conferir" são coisas
# diferentes, e juntá-las produziria alerta em documento correto.
REFORMA_AUSENTE = "AUSENTE"                # nem item nem total trazem a Reforma
REFORMA_SEM_TOTAL = "SEM_TOTAL"            # item traz, documento não declara o total
REFORMA_TOTAL_SEM_ITEM = "TOTAL_SEM_ITEM"  # total declarado sem item que o sustente
REFORMA_SEM_VALORES = "SEM_VALORES"        # há grupo, mas item sem vIBS/vCBS
REFORMA_CONFERE = "CONFERE"                # soma dos itens = total declarado
REFORMA_DIVERGE = "DIVERGE"                # soma dos itens ≠ total declarado


@dataclass(frozen=True)
class ConferenciaReforma:
    """Soma dos itens contra o total declarado, para IBS e CBS.

    É CONFERÊNCIA, NÃO APURAÇÃO
        Nada aqui calcula imposto. Os dois lados da comparação vêm do próprio
        documento: de um lado o `vIBS`/`vCBS` de cada item, do outro o
        `IBSCBSTot` que o emitente declarou. É aritmética sobre o que foi
        lido — nenhuma alíquota é aplicada e nenhuma regra fiscal é criada.

    POR QUE IGUALDADE EXATA, SEM TOLERÂNCIA
        Tolerância é decisão fiscal, e inventar uma aqui esconderia o que
        deveria aparecer. Medido no acervo real em 22/09/2026: dos 3.690
        documentos em que a comparação foi possível, **3.690 bateram ao
        centavo** e nenhum divergiu. Um alerta desta conferência é, portanto,
        um evento raro — e é assim que ele deve continuar.

        (A retenção da NFS-e usa tolerância de R$ 0,02, mas por um motivo que
        não existe aqui: lá o emitente arredonda três tributos separadamente.
        Ver `retencao.py`.)

    POR QUE ITEM SEM VALOR IMPEDE A COMPARAÇÃO
        62,6% dos itens do acervo têm CST 410 — imunidade e não incidência — e
        vêm com `<CST>` e `<cClassTrib>` e **nenhum valor**, sem sequer o grupo
        `gIBSCBS`. Somar esses itens como zero seria supor que ausente é zero,
        que é exatamente a suposição que este projeto recusa em todo lugar.
        Então: se QUALQUER item com o grupo não trouxer `vIBS` e `vCBS`, o
        estado é `SEM_VALORES` e não se compara nada. Não é divergência, é
        falta de base para comparar.
    """
    estado: str = REFORMA_AUSENTE
    itens_com_grupo: int = 0
    itens_com_valor: int = 0
    soma_ibs: Decimal | None = None
    soma_cbs: Decimal | None = None
    total_ibs: Decimal | None = None
    total_cbs: Decimal | None = None

    @property
    def comparavel(self) -> bool:
        return self.estado in (REFORMA_CONFERE, REFORMA_DIVERGE)

    @property
    def confere(self) -> bool | None:
        """`None` quando não houve comparação — nunca `False` por omissão."""
        if not self.comparavel:
            return None
        return self.estado == REFORMA_CONFERE

    @property
    def diferenca_ibs(self) -> Decimal | None:
        if self.soma_ibs is None or self.total_ibs is None:
            return None
        return self.soma_ibs - self.total_ibs

    @property
    def diferenca_cbs(self) -> Decimal | None:
        if self.soma_cbs is None or self.total_cbs is None:
            return None
        return self.soma_cbs - self.total_cbs


@dataclass(frozen=True)
class ExtensaoNFe:
    """O que só a NF-e tem."""
    modelo: str = "55"
    natureza_operacao: str = ""
    tipo_operacao: str = ""          # 0 entrada | 1 saída
    finalidade: str = ""
    itens: tuple[ItemNFe, ...] = field(default_factory=tuple)
    totais: TotaisNFe = field(default_factory=TotaisNFe)
    protocolo: str = ""
    dh_recebimento: datetime | None = None
    codigo_status: str = ""
    motivo_status: str = ""

    def conferir_reforma(self) -> ConferenciaReforma:
        """Compara a soma dos itens com o `IBSCBSTot` declarado.

        Só leitura, sem efeito nenhum sobre o documento: devolve o retrato e
        para por aí. Quem decide o que fazer com uma divergência é quem chama.
        """
        com_grupo = [i for i in self.itens if i.reforma.presente]
        tem_total = self.totais.reforma_presente
        t_ibs, t_cbs = self.totais.ibs, self.totais.cbs

        if not com_grupo and not tem_total:
            return ConferenciaReforma(estado=REFORMA_AUSENTE)
        if com_grupo and not tem_total:
            return ConferenciaReforma(estado=REFORMA_SEM_TOTAL,
                                      itens_com_grupo=len(com_grupo))
        if tem_total and not com_grupo:
            return ConferenciaReforma(estado=REFORMA_TOTAL_SEM_ITEM,
                                      total_ibs=t_ibs, total_cbs=t_cbs)

        com_valor = [i for i in com_grupo
                     if i.reforma.valor_ibs is not None
                     and i.reforma.valor_cbs is not None]
        # Qualquer item sem valor derruba a comparação: ver a docstring da
        # classe. E o total precisa dos dois lados para ser comparado.
        if (len(com_valor) != len(com_grupo)
                or t_ibs is None or t_cbs is None):
            return ConferenciaReforma(
                estado=REFORMA_SEM_VALORES,
                itens_com_grupo=len(com_grupo), itens_com_valor=len(com_valor),
                total_ibs=t_ibs, total_cbs=t_cbs)

        soma_ibs = sum((i.reforma.valor_ibs for i in com_valor), Decimal("0"))
        soma_cbs = sum((i.reforma.valor_cbs for i in com_valor), Decimal("0"))
        bate = soma_ibs == t_ibs and soma_cbs == t_cbs
        return ConferenciaReforma(
            estado=REFORMA_CONFERE if bate else REFORMA_DIVERGE,
            itens_com_grupo=len(com_grupo), itens_com_valor=len(com_valor),
            soma_ibs=soma_ibs, soma_cbs=soma_cbs,
            total_ibs=t_ibs, total_cbs=t_cbs)


@dataclass(frozen=True)
class ParticipanteCTe:
    """Um participante do CT-e, com o papel que ele exerce.

    Guardar o papel JUNTO do participante, em vez de só a lista de papéis da
    empresa, é o que permite responder "quem é o tomador deste frete?" sem
    reabrir o XML.
    """
    papel: str = ""
    identificador: str = ""          # CNPJ/CPF só dígitos
    nome: str = ""
    ie: str = ""
    uf: str = ""
    municipio: str = ""


@dataclass(frozen=True)
class ExtensaoCTe:
    """O que só o CT-e tem.

    `tomador_codigo` guarda o `toma` LITERAL do documento, e não só o papel
    resolvido. São informações diferentes: o código diz o que o emitente
    declarou, e o papel diz para quem isso apontou. Quando o apontamento não
    resolve — bloco ausente, código fora da tabela — o código continua ali para
    quem for investigar, em vez de virar um `OUTRO` sem explicação.
    """
    modelo: str = "57"
    cfop: str = ""
    natureza_operacao: str = ""
    tipo_cte: str = ""               # 0 normal | 1 complemento | 2 anulação | 3 substituto
    tipo_servico: str = ""           # 0 normal | 1 subcontratação | 2 redespacho…
    modal: str = ""                  # 01 rodoviário | 02 aéreo | 03 aquaviário…
    tomador_codigo: str = ""         # o `toma` cru
    tomador_papel: str = ""          # para quem o `toma` apontou
    participantes: tuple[ParticipanteCTe, ...] = field(default_factory=tuple)
    uf_inicio: str = ""
    uf_fim: str = ""
    municipio_inicio: str = ""
    municipio_fim: str = ""
    valor_prestacao: Decimal | None = None
    valor_receber: Decimal | None = None
    base_icms: Decimal | None = None
    aliquota_icms: Decimal | None = None
    valor_icms: Decimal | None = None
    cst_icms: str = ""
    chaves_nfe: tuple[str, ...] = field(default_factory=tuple)   # a carga
    protocolo: str = ""
    dh_recebimento: datetime | None = None
    codigo_status: str = ""
    motivo_status: str = ""

    def participante(self, papel: str) -> "ParticipanteCTe | None":
        for p in self.participantes:
            if p.papel == papel:
                return p
        return None


@dataclass(frozen=True)
class ExtensaoEvento:
    """O que só o evento tem.

    `forma` distingue de onde o evento veio, porque os dois formatos trazem
    coisas diferentes e confundi-los faria o sistema inventar ausência:

        procEventoNFe   o evento completo, com `detEvento` — traz justificativa
                        de cancelamento e texto de carta de correção.
        resEvento       o RESUMO que a Distribuição DF-e entrega. Traz o
                        `xEvento` já pronto, e **não traz** `detEvento`: nele,
                        justificativa e correção não existem — não estão vazias.
    """
    tp_evento: str = ""
    descricao: str = ""
    sequencia: str = ""
    orgao: str = ""
    protocolo: str = ""
    justificativa: str | None = None
    correcao: str | None = None
    dh_recebimento: datetime | None = None
    forma: str = ""                  # "procEventoNFe" | "resEvento"

    @property
    def e_cancelamento(self) -> bool:
        # 110111 cancelamento · 110112 cancelamento por substituição
        return self.tp_evento in ("110111", "110112")


def _totais_reforma_para_indice(extensao) -> dict:
    """As três colunas do `IBSCBSTot`, prontas para o índice.

    Fica fora de `TotaisNFe` porque quem grava é o núcleo, que atende as
    quatro espécies: para NFS-e, CT-e e evento as três saem `None` — não
    porque sejam zero, mas porque esses documentos não têm o grupo.

    `Decimal` vira TEXTO, como todo valor monetário do índice: coluna REAL
    reintroduziria o float entre gravar e ler.
    """
    t = getattr(extensao, "totais", None)
    if not isinstance(t, TotaisNFe):
        return {"total_base_ibs_cbs": None, "total_ibs": None, "total_cbs": None}
    return {
        "total_base_ibs_cbs": None if t.base_ibs_cbs is None else str(t.base_ibs_cbs),
        "total_ibs": None if t.ibs is None else str(t.ibs),
        "total_cbs": None if t.cbs is None else str(t.cbs),
    }


# ── o núcleo ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Documento:
    """A representação normalizada. É sobre isto que a tela e as regras operam."""
    id_documento: str
    especie: str
    chave: str = ""
    numero: str = ""
    serie: str = ""
    identidade_empresa: str = ""
    # `papel` é o principal (por `PRECEDENCIA_PAPEL`); `papeis` traz todos.
    # Uma nota em que a empresa é destinatária E transportadora existe, e
    # espremer isso num campo só destruiria informação — por isso os dois.
    papel: str = OUTRO
    papeis: tuple[str, ...] = ()
    emitente: Participante = field(default_factory=Participante)
    contraparte: Participante = field(default_factory=Participante)
    dh_emissao: datetime | None = None
    # `dhSaiEnt` — quando a mercadoria SAIU (emitente) ou ENTROU (destinatário).
    # É campo OPCIONAL da NF-e, mas neste acervo quase sempre vem: medido em
    # 23/08/2026, **7.498 de 7.534 NF-e completas o trazem (99,5%)**. Ele nasce
    # `None` e nunca cai para `dh_emissao` — preencher com a emissão faria a
    # tela afirmar uma data de saída que o documento não deu, e nos 36 casos em
    # que ela falta é exatamente isso que precisa aparecer.
    dh_saida_entrada: datetime | None = None
    competencia: date | None = None
    valor_total: Decimal | None = None
    situacao: str = INDEFINIDA
    versao_schema: str = ""
    parser: str = ""
    versao_parser: int = 0
    extensao: ExtensaoNFe | ExtensaoCTe | ExtensaoEvento | None = None
    avisos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def e_evento(self) -> bool:
        return self.especie in (EVENTO_NFE, EVENTO_CTE)

    @property
    def e_cte(self) -> bool:
        return self.especie == CTE57

    def para_indice(self) -> dict:
        """O que vai para o SQLite. `Decimal` vira TEXTO, não REAL.

        Guardar valor monetário em coluna REAL do SQLite reintroduziria o float
        pela porta dos fundos — o banco converteria, e a precisão sumiria entre
        gravar e ler."""
        return {
            "id_documento": self.id_documento,
            "especie": self.especie,
            "chave": self.chave,
            "numero": self.numero,
            "serie": self.serie,
            "identidade_empresa": self.identidade_empresa,
            "papel": self.papel,
            "papeis": "|" + "|".join(self.papeis) + "|" if self.papeis else "",
            "emitente": self.emitente.identificador,
            "emitente_nome": self.emitente.nome,
            "contraparte": self.contraparte.identificador,
            "contraparte_nome": self.contraparte.nome,
            "dh_emissao": self.dh_emissao.isoformat() if self.dh_emissao else None,
            "dh_saida_entrada": (self.dh_saida_entrada.isoformat()
                                 if self.dh_saida_entrada else None),
            "competencia": self.competencia.isoformat() if self.competencia else None,
            "valor_total": str(self.valor_total) if self.valor_total is not None else None,
            "situacao": self.situacao,
            "versao_schema": self.versao_schema,
            "parser": self.parser,
            "versao_parser": self.versao_parser,
            # REFORMA 2 — o total declarado, para não ter de reabrir o XML.
            # Só a NF-e tem `IBSCBSTot`; NFS-e, CT-e e evento gravam `None`,
            # que é o que eles de fato informaram: nada.
            **_totais_reforma_para_indice(self.extensao),
        }
