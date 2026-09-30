# -*- coding: utf-8 -*-
"""
regime.py — qual regra vale NESTA competência. Uma camada, uma pergunta.

O QUE ESTA CAMADA FAZ
    Responde "nesta competência, este tributo é devido por esta empresa, e por
    qual versão da regra?". Devolve a regra, a vigência e o ato normativo que
    a sustenta.

O QUE ELA NÃO FAZ, E NÃO PODE PASSAR A FAZER
    **Não calcula tributo.** Não tem alíquota, não tem base, não tem soma.
    Quem calcula são os motores que já existem (`classificador`,
    `apuracao_federal`, `iss`) — e é justamente para não duplicá-los que esta
    camada não os importa. Ela também não conhece parser, índice nem XML:
    depende só da biblioteca padrão.

    A dependência é de mão única: a apuração pergunta a `regime`; `regime`
    nunca pergunta à apuração. O contrário criaria ciclo e devolveria o
    problema que esta camada existe para resolver.

POR QUE ELA EXISTE
    Auditoria de 22/09/2026: **nenhuma** comparação de data contra ano fixo no
    código fiscal vivo. As tabelas do Simples, as alíquotas de PIS/COFINS e os
    limites são constantes de módulo — implicitamente "a lei atual, para
    sempre". Enquanto só existiu uma versão de cada regra, isso funcionou. Com
    a Reforma passam a existir duas, e sem uma camada de vigência a escolha
    viraria `if` de transição espalhado por cada motor.

AS TRÊS REGRAS QUE O TIPO IMPÕE
    1. **Contenção, nunca recência.** A regra escolhida é a cuja vigência
       CONTÉM a competência perguntada. Cadastrar uma regra futura não pode
       mudar a resposta de um mês já apurado.

       Quando mais de uma contém, vence a de **início mais recente** — que não
       é o mesmo que "a mais nova cadastrada". A distinção é o coração desta
       camada: uma regra que passa a valer em 2030 não é candidata para 2027,
       porque não contém 2027; entre uma que vale desde 2020 e outra desde
       2027, quem responde por 2029 é a de 2027, e quem responde por 2024
       continua sendo a de 2020 — hoje e para sempre.

    2. **Ausência não é permissão.** Sem regra vigente, a resposta é
       `SEM_REGRA_VIGENTE` com motivo — nunca a regra mais próxima, nunca a
       última conhecida, nunca um padrão. É o mesmo princípio de "ausente ≠
       zero" que o projeto aplica ao dado.

    3. **Coexistência, não substituição.** Cada tributo tem a sua vigência.
       Não existe aqui nenhuma regra dizendo "IBS substitui PIS" ou "IBS
       substitui ISS": perguntar por PIS e por CBS na mesma competência pode
       devolver os dois como devidos, e é assim que a transição funciona.

O QUE SIGNIFICA `fim` VAZIO
    "Sem data de extinção CADASTRADA" — **não** "nunca será extinto". É
    legítimo: uma regra em vigor hoje não tem fim, e vigência aberta responde
    normalmente. PIS, COFINS e ISS estão assim porque a data de extinção pela
    Reforma ainda não foi levantada aqui.

O QUE SIGNIFICA `inicio` VAZIO — E POR QUE ELE NÃO RESPONDE
    "Início não cadastrado". E **vigência sem início não cobre competência
    nenhuma**: `qual_regra` devolve `VIGENCIA_NAO_CADASTRADA`, nunca
    `APLICAVEL`.

    ISTO JÁ ESTEVE ERRADO AQUI, E O ERRO ERA GRAVE
        A primeira versão tratava `inicio` vazio como "vale desde sempre".
        O efeito medido: `qual_regra("PIS", "1500-01")` devolvia APLICÁVEL e
        `devido=True`. Uma lacuna de cadastro virava afirmação sobre o século
        XVI — e, pior, sobre 2099. "Não sei desde quando" não é "desde
        sempre", do mesmo jeito que ausente não é zero.

    A consequência honesta é que hoje NENHUMA regra do catálogo responde
    APLICÁVEL: as de PIS, COFINS e ISS estão sem início, e as da Reforma
    estão pendentes. Isso é o estado real do conhecimento do sistema, e
    aparece como pendência declarada em vez de responder por omissão.

NENHUMA ALÍQUOTA MORA AQUI
    Por dois motivos. O primeiro: alíquota é cálculo, e cálculo não é desta
    camada. O segundo: as alíquotas que existem já moram nos motores
    (`apuracao_federal.ALIQUOTAS`, `classificador.TABELAS`), e copiá-las para
    cá criaria duas verdades. Por isso o campo `aliquota` de uma `Regra`
    nunca é número: é `NO_MOTOR` quando o valor já existe no motor de sempre,
    ou `PENDENTE_DE_DEFINICAO` quando a fonte oficial ainda não o fixou.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

# ── identificadores de tributo ──────────────────────────────────────────────
# São chaves ESTÁVEIS, não rótulos de tela. O IBS é perguntado em duas partes
# porque ele se reparte entre UF e município, e os dois vêm separados no XML
# (`gIBSUF` e `gIBSMun`) — juntá-los aqui esconderia essa separação.
CBS = "CBS"
IBS_ESTADUAL = "IBS_ESTADUAL"
IBS_MUNICIPAL = "IBS_MUNICIPAL"
PIS = "PIS"
COFINS = "COFINS"
ISS = "ISS"

TRIBUTOS = (CBS, IBS_ESTADUAL, IBS_MUNICIPAL, PIS, COFINS, ISS)

# ── estado da resposta ──────────────────────────────────────────────────────
APLICAVEL = "APLICAVEL"
SEM_REGRA_VIGENTE = "SEM_REGRA_VIGENTE"
PENDENTE_DE_DEFINICAO = "PENDENTE_DE_DEFINICAO"
CONFLITO_DE_VIGENCIA = "CONFLITO_DE_VIGENCIA"
#: A regra existe e está completa, mas não se sabe DESDE QUANDO vale. Não é o
#: mesmo que não existir regra (SEM_REGRA_VIGENTE) nem que a regra inteira
#: esteja por definir (PENDENTE_DE_DEFINICAO): o que falta é só a data — e
#: enquanto faltar, a regra não responde por competência nenhuma.
VIGENCIA_NAO_CADASTRADA = "VIGENCIA_NAO_CADASTRADA"

#: Os únicos estados que NÃO autorizam decisão de cálculo. Só `APLICAVEL`
#: autoriza — e `Decisao.aplicavel` é a forma de perguntar isso.
NAO_DECIDEM = (SEM_REGRA_VIGENTE, PENDENTE_DE_DEFINICAO,
               CONFLITO_DE_VIGENCIA, VIGENCIA_NAO_CADASTRADA)

# ── tratamento de uma regra ─────────────────────────────────────────────────
DEVIDO = "DEVIDO"
DENTRO_DO_DAS = "DENTRO_DO_DAS"     # recolhido no documento único do Simples
NAO_DEVIDO = "NAO_DEVIDO"

# ── status do cadastro da regra ─────────────────────────────────────────────
VIGENTE = "VIGENTE"
PENDENTE = PENDENTE_DE_DEFINICAO    # mesmo valor: a regra existe, falta definir

# ── onde está a alíquota, quando existe ─────────────────────────────────────
NO_MOTOR = "NO_MOTOR"               # já existe no motor de sempre; não se copia

# ── regimes da empresa ──────────────────────────────────────────────────────
SIMPLES = "SIMPLES"
MEI = "MEI"
PRESUMIDO = "PRESUMIDO"
REAL = "REAL"
REGIMES = (SIMPLES, MEI, PRESUMIDO, REAL)

# ── tratamento de IBS/CBS para quem está no Simples ─────────────────────────
# Três estados, e o terceiro é o honesto: a lei prevê a opção pelo regime
# regular, mas as condições e o calendário ainda não estão cadastrados aqui.
IBS_CBS_DENTRO_DO_DAS = "DENTRO_DO_DAS"
IBS_CBS_REGIME_REGULAR = "REGIME_REGULAR"
IBS_CBS_PENDENTE = PENDENTE_DE_DEFINICAO


def competencia(valor) -> str:
    """Qualquer forma de competência → `'AAAA-MM'`. Vazio quando não dá.

    Aceita `date`/`datetime` (o que a normalização usa), `'AAAA-MM'` e
    `'AAAA-MM-DD'` (o que o classificador e o Contábil usam). Não inventa mês
    nem completa data pela metade: entrada ilegível devolve `''`, e quem
    perguntar recebe `SEM_REGRA_VIGENTE` com motivo, em vez de uma resposta
    montada sobre um palpite.
    """
    if valor is None:
        return ""
    if isinstance(valor, date):
        return f"{valor.year:04d}-{valor.month:02d}"
    t = str(valor).strip()
    if len(t) >= 7 and t[4] == "-" and t[:4].isdigit() and t[5:7].isdigit():
        mes = int(t[5:7])
        if 1 <= mes <= 12:
            return t[:7]
    return ""


@dataclass(frozen=True)
class Vigencia:
    """O intervalo de competências em que uma regra vale.

    `inicio` e `fim` são `'AAAA-MM'` inclusivos. Vazio tem significado
    declarado, e não é o mesmo que "sempre" — ver o cabeçalho do módulo.
    """
    inicio: str = ""    # "" = início não cadastrado — NÃO "desde sempre"
    fim: str = ""       # "" = sem data de extinção cadastrada (vigência aberta)

    @property
    def determinada(self) -> bool:
        """Dá para dizer a partir de quando esta regra vale?

        Sem início não dá, e sem isso ela não cobre competência alguma. Fim
        vazio é outra coisa: vigência aberta é determinada, e responde.
        """
        return bool(self.inicio)

    def contem(self, comp: str) -> bool:
        """A competência cai dentro desta vigência?

        Vigência INDETERMINADA não contém nada. É o ponto em que "não sei
        desde quando" deixa de virar "desde sempre".
        """
        if not comp or not self.determinada:
            return False
        if comp < self.inicio:
            return False
        if self.fim and comp > self.fim:
            return False
        return True

    @property
    def aberta(self) -> bool:
        return not self.fim

    @property
    def largura(self) -> int:
        """Quão específica é esta vigência, para desempate.

        Intervalo fechado é mais específico que aberto, e aberto dos dois
        lados é o menos específico de todos. Não é medida de tempo: é medida
        de compromisso — quem declarou início E fim disse mais.
        """
        return (1 if self.inicio else 0) + (1 if self.fim else 0)

    def para_json(self) -> dict:
        return {"inicio": self.inicio or None, "fim": self.fim or None,
                "aberta": self.aberta}


#: Limites usados só para comparar intervalos abertos. Como toda competência
#: tem a largura fixa `AAAA-MM`, comparação de texto já ordena certo — e estes
#: dois ficam, por construção, fora de qualquer competência real.
_ANTES_DE_TUDO = "0000-00"
_DEPOIS_DE_TUDO = "9999-99"


def _sobrepoe(a: Vigencia, b: Vigencia) -> bool:
    """Dois intervalos de competência têm algum mês em comum?"""
    ini = max(a.inicio or _ANTES_DE_TUDO, b.inicio or _ANTES_DE_TUDO)
    fim = min(a.fim or _DEPOIS_DE_TUDO, b.fim or _DEPOIS_DE_TUDO)
    return ini <= fim


@dataclass(frozen=True)
class Regra:
    """UMA versão de UMA regra de tributação, com a fonte que a sustenta.

    `fonte` e `ato` são obrigatórios na prática: `catalogo_valido()` recusa
    regra sem eles. Regra sem fonte é opinião, e opinião não entra no cadastro
    de um sistema fiscal.
    """
    tributo: str
    versao: str
    tratamento: str
    vigencia: Vigencia = field(default_factory=Vigencia)
    fonte: str = ""                  # o ato normativo, por extenso
    ato: str = ""                    # a chave curta do ato
    status: str = VIGENTE
    precedencia: int = 0
    # NUNCA um número. Ver o cabeçalho do módulo.
    aliquota: str = NO_MOTOR
    observacao: str = ""

    @property
    def pendente(self) -> bool:
        return (self.status == PENDENTE_DE_DEFINICAO
                or self.aliquota == PENDENTE_DE_DEFINICAO)

    def para_json(self) -> dict:
        return {"tributo": self.tributo, "versao": self.versao,
                "tratamento": self.tratamento, "vigencia": self.vigencia.para_json(),
                "fonte": self.fonte, "ato": self.ato, "status": self.status,
                "precedencia": self.precedencia, "aliquota": self.aliquota,
                "observacao": self.observacao or None}


@dataclass(frozen=True)
class Decisao:
    """A resposta de `qual_regra`. Retrato, sem efeito colateral."""
    tributo: str
    competencia: str
    estado: str
    regra: Regra | None = None
    motivo: str = ""
    candidatas: tuple[str, ...] = ()   # versões que disputaram, em conflito

    @property
    def aplicavel(self) -> bool:
        """Só `APLICAVEL` é aplicável. Pendente e ausente NÃO são."""
        return self.estado == APLICAVEL

    @property
    def devido(self) -> bool | None:
        """`None` quando não se sabe — nunca `False` por omissão."""
        if not self.aplicavel or self.regra is None:
            return None
        return self.regra.tratamento in (DEVIDO, DENTRO_DO_DAS)

    def para_json(self) -> dict:
        return {"tributo": self.tributo, "competencia": self.competencia or None,
                "estado": self.estado, "aplicavel": self.aplicavel,
                "devido": self.devido, "motivo": self.motivo or None,
                "regra": self.regra.para_json() if self.regra else None,
                "candidatas": list(self.candidatas)}


# ════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO
#
#  Só entra regra com FONTE, e só fonte que já está no projeto. Nenhuma data
#  de vigência da Reforma foi cadastrada porque nenhuma foi levantada de fonte
#  oficial aqui — ver `PENDENCIAS`, no fim do módulo.
# ════════════════════════════════════════════════════════════════════════════
CATALOGO: tuple[Regra, ...] = (
    # ── tributos de hoje ────────────────────────────────────────────────────
    # Início vazio: a data nunca foi levantada de fonte oficial neste projeto.
    # Enquanto estiver assim, estas três regras respondem
    # `VIGENCIA_NAO_CADASTRADA` — existem, estão completas no resto, e não
    # decidem competência nenhuma. Preencher o início é trabalho de cadastro
    # com fonte, não de quem escreve o módulo.
    #
    # Fim vazio: a data de extinção pela Reforma ainda não está cadastrada —
    # pendência declarada, não afirmação de perpetuidade.
    Regra(tributo=PIS, versao="PIS_ATUAL", tratamento=DEVIDO,
          vigencia=Vigencia(),
          fonte="Leis 9.715/1998 e 9.718/1998 (cumulativo); "
                "Lei 10.637/2002 (não cumulativo)",
          ato="LEI_9715_9718_10637", status=VIGENTE, aliquota=NO_MOTOR,
          observacao="Alíquota em apuracao_federal.ALIQUOTAS; não se copia."),
    Regra(tributo=COFINS, versao="COFINS_ATUAL", tratamento=DEVIDO,
          vigencia=Vigencia(),
          fonte="Lei 9.718/1998 (cumulativo); Lei 10.833/2003 (não cumulativo)",
          ato="LEI_9718_10833", status=VIGENTE, aliquota=NO_MOTOR,
          observacao="Alíquota em apuracao_federal.ALIQUOTAS; não se copia."),
    Regra(tributo=ISS, versao="ISS_ATUAL", tratamento=DEVIDO,
          vigencia=Vigencia(),
          fonte="LC 116", ato="LC_116", status=VIGENTE, aliquota=NO_MOTOR,
          observacao="Alíquota é municipal e vem do próprio documento; "
                     "o que o sistema apura está em iss.py."),

    # ── tributos da Reforma ─────────────────────────────────────────────────
    # EXISTEM e estão nomeados na legislação que o projeto já cita, mas nem a
    # competência de início nem a alíquota foram levantadas de fonte oficial
    # neste projeto. Ficam cadastrados como PENDENTES: perguntar por eles
    # devolve `PENDENTE_DE_DEFINICAO`, que é diferente de "não existe" e
    # diferente de "não é devido".
    Regra(tributo=CBS, versao="CBS_REFORMA", tratamento=PENDENTE_DE_DEFINICAO,
          vigencia=Vigencia(),
          fonte="EC 132; LC 214/2025", ato="EC_132_LC_214_2025",
          status=PENDENTE_DE_DEFINICAO, aliquota=PENDENTE_DE_DEFINICAO,
          observacao="Competência de início e alíquota não levantadas de "
                     "fonte oficial neste projeto."),
    Regra(tributo=IBS_ESTADUAL, versao="IBS_UF_REFORMA",
          tratamento=PENDENTE_DE_DEFINICAO, vigencia=Vigencia(),
          fonte="EC 132; LC 214/2025", ato="EC_132_LC_214_2025",
          status=PENDENTE_DE_DEFINICAO, aliquota=PENDENTE_DE_DEFINICAO,
          observacao="Competência de início e alíquota estadual não "
                     "levantadas de fonte oficial neste projeto."),
    Regra(tributo=IBS_MUNICIPAL, versao="IBS_MUN_REFORMA",
          tratamento=PENDENTE_DE_DEFINICAO, vigencia=Vigencia(),
          fonte="EC 132; LC 214/2025", ato="EC_132_LC_214_2025",
          status=PENDENTE_DE_DEFINICAO, aliquota=PENDENTE_DE_DEFINICAO,
          observacao="Competência de início e alíquota municipal não "
                     "levantadas de fonte oficial neste projeto."),
)


def catalogo_valido(catalogo=None) -> list[str]:
    """Problemas do cadastro. Lista vazia = cadastro íntegro.

    Roda sobre qualquer catálogo, inclusive os que os testes montam. Não
    corrige nada: aponta.
    """
    catalogo = CATALOGO if catalogo is None else catalogo
    problemas = []
    for r in catalogo:
        onde = f"{r.tributo}/{r.versao}"
        if r.tributo not in TRIBUTOS:
            problemas.append(f"{onde}: tributo desconhecido")
        if not r.fonte or not r.ato:
            problemas.append(f"{onde}: regra sem fonte normativa")
        if not r.versao:
            problemas.append(f"{onde}: regra sem versão")
        v = r.vigencia
        if v.inicio and v.fim and v.inicio > v.fim:
            problemas.append(f"{onde}: vigência começa depois de terminar")
        if isinstance(r.aliquota, (int, float)):
            problemas.append(f"{onde}: alíquota numérica não pertence a esta camada")
    return problemas


# ════════════════════════════════════════════════════════════════════════════
#  CONSULTA
# ════════════════════════════════════════════════════════════════════════════
def qual_regra(tributo: str, comp, *, catalogo=None) -> Decisao:
    """Qual regra vale para este tributo NESTA competência.

    A escolha é por CONTENÇÃO: entre as regras cadastradas para o tributo,
    vale a que contém a competência. Recência não é critério — cadastrar uma
    regra de 2030 não muda a resposta de 2026.
    """
    catalogo = CATALOGO if catalogo is None else catalogo
    c = competencia(comp)
    if not c:
        return Decisao(tributo=tributo, competencia="",
                       estado=SEM_REGRA_VIGENTE,
                       motivo="competência ilegível ou ausente")

    do_tributo = [r for r in catalogo if r.tributo == tributo]
    candidatas = [r for r in do_tributo if r.vigencia.contem(c)]
    if not candidatas:
        if not do_tributo:
            return Decisao(tributo=tributo, competencia=c,
                           estado=SEM_REGRA_VIGENTE,
                           motivo="tributo não cadastrado")
        # A regra existe. Ela não respondeu por um destes dois motivos, e são
        # diferentes: ou a definição inteira está pendente, ou só falta a data
        # de início. Colapsá-los em "sem regra" esconderia qual lacuna fechar.
        pendentes = [r for r in do_tributo if r.pendente]
        if pendentes:
            r = pendentes[0]
            return Decisao(tributo=tributo, competencia=c,
                           estado=PENDENTE_DE_DEFINICAO, regra=r,
                           motivo=r.observacao
                                  or "regra conhecida, definição pendente")
        sem_inicio = [r for r in do_tributo if not r.vigencia.determinada]
        if sem_inicio:
            r = sem_inicio[0]
            return Decisao(
                tributo=tributo, competencia=c,
                estado=VIGENCIA_NAO_CADASTRADA, regra=r,
                motivo="a regra existe, mas não se sabe desde qual "
                       "competência ela vale — e isso não é o mesmo que "
                       "valer desde sempre")
        return Decisao(tributo=tributo, competencia=c,
                       estado=SEM_REGRA_VIGENTE,
                       motivo="nenhuma regra vigente nesta competência")

    # DESEMPATE, entre as que já CONTÊM a competência:
    #   1. início mais recente. Isto NÃO é "a mais nova": é a que passou a
    #      valer por último ANTES desta competência. Uma regra cadastrada
    #      depois, com início posterior, não é candidata aqui — ela nem chega
    #      a esta lista, porque não contém a competência perguntada. É essa
    #      distinção que mantém o passado estável.
    #   2. vigência mais específica (quem declarou início E fim disse mais);
    #   3. precedência maior, para quando a lei mandar um caso especial
    #      vencer o geral.
    # Empate nos três é DEFEITO DE CADASTRO, e sai como conflito — escolher em
    # silêncio esconderia o erro.
    # `inicio` sem fallback: quem chegou aqui tem vigência determinada, senão
    # `contem()` o teria excluído.
    def _chave(r: Regra):
        return (r.vigencia.inicio, r.vigencia.largura, r.precedencia)

    ordenadas = sorted(candidatas, key=_chave, reverse=True)
    melhor = ordenadas[0]
    if len(ordenadas) > 1:
        seguinte = ordenadas[1]
        if _chave(seguinte) == _chave(melhor):
            return Decisao(
                tributo=tributo, competencia=c, estado=CONFLITO_DE_VIGENCIA,
                motivo="duas regras vigentes com a mesma especificidade "
                       "e a mesma precedência",
                candidatas=tuple(sorted(r.versao for r in ordenadas)))

    if melhor.pendente:
        return Decisao(tributo=tributo, competencia=c,
                       estado=PENDENTE_DE_DEFINICAO, regra=melhor,
                       motivo=melhor.observacao
                              or "regra conhecida, definição pendente")
    return Decisao(tributo=tributo, competencia=c, estado=APLICAVEL,
                   regra=melhor)


def regras_sem_vigencia(catalogo=None) -> tuple[str, ...]:
    """As regras cadastradas que ainda não dizem desde quando valem.

    Não é defeito de cadastro — é lacuna DECLARADA, e por isso fica fora de
    `catalogo_valido()`. Serve para a tela e para o relatório mostrarem o que
    falta levantar de fonte oficial, em vez de a falta aparecer como um
    "não aplicável" sem explicação.
    """
    catalogo = CATALOGO if catalogo is None else catalogo
    return tuple(f"{r.tributo}/{r.versao}" for r in catalogo
                 if not r.vigencia.determinada)


def tributos_da_competencia(comp, *, catalogo=None) -> dict:
    """O panorama de TODOS os tributos numa competência.

    Existe para tornar a coexistência visível: na transição, perguntar
    "o que é devido em 2027-01?" deve mostrar PIS e CBS lado a lado, cada um
    com a sua vigência. Nada aqui substitui nada.
    """
    return {t: qual_regra(t, comp, catalogo=catalogo) for t in TRIBUTOS}


# ════════════════════════════════════════════════════════════════════════════
#  REGIME DA EMPRESA — com histórico, não um valor solto
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class OpcaoRegimeRegular:
    """A opção do optante do Simples pelo regime regular de IBS/CBS.

    Estrutura separada do regime porque é OUTRA decisão: a empresa continua no
    Simples e passa a recolher IBS/CBS fora do DAS. Tem vigência própria,
    porque pode começar e terminar sem que o regime principal mude.

    Nasce vazia. As condições e o calendário desta opção não foram levantados
    de fonte oficial neste projeto — ver `PENDENCIAS`.
    """
    vigencia: Vigencia = field(default_factory=Vigencia)
    fonte: str = ""
    observacao: str = ""

    def vigente_em(self, comp: str) -> bool:
        return bool(self.fonte) and self.vigencia.contem(comp)


@dataclass(frozen=True)
class PeriodoRegime:
    """O regime da empresa durante um intervalo de competências."""
    regime: str
    vigencia: Vigencia = field(default_factory=Vigencia)
    # De onde veio a informação: cadastro do escritório, base pública, XML…
    # Importa porque "não optante" da base pública NÃO distingue Presumido de
    # Real, e quem consome precisa saber o peso do que está lendo.
    fonte: str = ""
    tratamento_ibs_cbs: str = IBS_CBS_PENDENTE
    opcao_regular: OpcaoRegimeRegular = field(default_factory=OpcaoRegimeRegular)
    observacao: str = ""

    @property
    def e_simples(self) -> bool:
        return self.regime in (SIMPLES, MEI)

    def para_json(self) -> dict:
        return {"regime": self.regime, "vigencia": self.vigencia.para_json(),
                "fonte": self.fonte or None,
                "tratamento_ibs_cbs": self.tratamento_ibs_cbs,
                "observacao": self.observacao or None}


@dataclass(frozen=True)
class HistoricoRegime:
    """Todos os regimes que a empresa já teve, cada um com a sua vigência.

    POR QUE HISTÓRICO, E NÃO UM CAMPO
        Hoje o regime sai de `cfg.get("regime")` — um escalar, sem data. Uma
        empresa que saia do Simples para o Presumido em julho não tem como ser
        representada, e a apuração de janeiro seria refeita com o regime de
        dezembro, em silêncio.

    NADA É APAGADO
        `com()` devolve um histórico NOVO com o período acrescentado. O
        anterior continua existindo — é `frozen`, e mudar o passado exigiria
        construir outro objeto de propósito.
    """
    identidade: str = ""
    periodos: tuple[PeriodoRegime, ...] = ()

    def em(self, comp) -> PeriodoRegime | None:
        """O regime vigente NAQUELA competência, ou `None`.

        Mesma regra do catálogo: contenção, nunca recência. Acrescentar o
        regime de 2028 não muda o que a empresa era em 2026.
        """
        c = competencia(comp)
        if not c:
            return None
        candidatos = [p for p in self.periodos if p.vigencia.contem(c)]
        if not candidatos:
            return None
        return sorted(candidatos, key=lambda p: p.vigencia.largura,
                      reverse=True)[0]

    def com(self, periodo: PeriodoRegime) -> "HistoricoRegime":
        """Um histórico novo, com este período acrescentado. Não apaga nada."""
        return replace(self, periodos=self.periodos + (periodo,))

    def conflitos(self) -> list[str]:
        """Períodos que se sobrepõem com a MESMA especificidade.

        Sobreposição com especificidades diferentes não é conflito: a mais
        específica vence, e é isso que permite um período fechado recortar um
        aberto. Empate de especificidade é defeito de cadastro — não há
        critério para escolher, e escolher em silêncio esconderia o erro.
        """
        achados = []
        for i, a in enumerate(self.periodos):
            for b in self.periodos[i + 1:]:
                if a.vigencia.largura != b.vigencia.largura:
                    continue
                if _sobrepoe(a.vigencia, b.vigencia):
                    achados.append(
                        f"{a.regime} e {b.regime} se sobrepõem com a mesma "
                        f"especificidade")
        return achados

    def para_json(self) -> dict:
        return {"identidade": self.identidade or None,
                "periodos": [p.para_json() for p in self.periodos]}


def tratamento_ibs_cbs(historico: HistoricoRegime, comp) -> Decisao:
    """Como IBS/CBS se aplicam a ESTA empresa NESTA competência.

    Combina duas perguntas que são mesmo duas: a regra geral do tributo
    (`qual_regra`) e a situação da empresa (regime + opção pelo regime
    regular). Não calcula nada e não decide alíquota.
    """
    c = competencia(comp)
    periodo = historico.em(c)
    if periodo is None:
        return Decisao(tributo=CBS, competencia=c, estado=SEM_REGRA_VIGENTE,
                       motivo="sem regime cadastrado para esta competência")
    geral = qual_regra(CBS, c)
    if geral.estado == PENDENTE_DE_DEFINICAO:
        return replace(geral, motivo=(
            f"regime {periodo.regime}: {geral.motivo}"))
    if not periodo.e_simples:
        return geral
    if periodo.opcao_regular.vigente_em(c):
        return replace(geral, motivo="Simples com opção pelo regime regular")
    if periodo.tratamento_ibs_cbs == IBS_CBS_PENDENTE:
        return Decisao(tributo=CBS, competencia=c,
                       estado=PENDENTE_DE_DEFINICAO,
                       motivo="tratamento de IBS/CBS no Simples não cadastrado")
    return replace(geral, motivo=f"Simples: {periodo.tratamento_ibs_cbs}")


# ════════════════════════════════════════════════════════════════════════════
#  PENDÊNCIAS — o que falta de fonte oficial, declarado em vez de inventado
# ════════════════════════════════════════════════════════════════════════════
PENDENCIAS = (
    "Alíquotas de IBS (estadual e municipal) e de CBS.",
    "Competência de início da cobrança de IBS e CBS.",
    "Competências de extinção de PIS, COFINS e ISS.",
    "Calendário da transição e regras de coexistência ano a ano.",
    "Tratamento de IBS/CBS para o optante do Simples: se e quando saem do DAS.",
    "Condições e vigência da opção do Simples pelo regime regular de IBS/CBS.",
    "Competência de início das tabelas do Simples hoje em uso "
    "(classificador.TABELAS), para que possam ser versionadas.",
    "Vigência do limiar de 28% do Fator R e dos limites de 3,6M e 4,8M.",
    "Vigência da tabela cClassTrib, hoje embutida em web/cclasstrib.html.",
)
