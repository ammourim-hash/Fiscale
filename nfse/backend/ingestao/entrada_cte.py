"""
entrada_cte.py — a ENTRADA CANÔNICA do CT-e. Toda escrita passa por aqui.

POR QUE UMA ENTRADA SÓ, E QUAL FONTE TRAZ O QUÊ
    O CT-e chega por dois caminhos, e eles não competem — **cobrem coisas
    diferentes**:

      • **distribuição oficial** (`CTeDistribuicaoDFe`, com certificado) traz
        o que é DE INTERESSE da empresa: aquilo em que ela é tomadora,
        remetente, destinatária, expedidora, recebedora ou está no `autXML`,
        mais os eventos;

      • **entrada canônica de XML** (ERP, pasta vigiada, e-mail, XML/ZIP) traz
        o que a distribuição **não devolve**: os CT-e que a própria empresa
        EMITIU — e também histórico anterior à captura e lacunas fora da
        janela de retenção.

    A NT 2015.002 é explícita: o emitente não recebe de volta o que emitiu.
    Esperar os próprios CT-e pela rede é esperar um documento que não vem, e
    foi exatamente esse o erro da CTE 1 ao apontar os 59 CT-e emitidos da TRX
    como prova de captura.

    O que as duas têm em comum é a deduplicação: a mesma chave nunca entra
    duas vezes, venha de onde vier. O que muda é só a procedência registrada.

AS QUATRO REGRAS DE IDENTIDADE
    1. **CT-e é único pela chave de acesso.** 44 dígitos com DV conferido. Sem
       chave válida o documento entra por hash, com identidade FRACA, e não
       deduplica contra ninguém — preservar sem saber é melhor que descartar.

    2. **Evento é único por chave + tipo + sequência (+ órgão).** O órgão entra
       porque dois estados podem registrar o mesmo tipo e sequência para a
       mesma chave — foi assim que 719 registros de passagem colidiram na NF-e.

    3. **SHA-256 decide repetição exata.** Bytes idênticos = `DUPLICATA`, e
       nada é escrito. É o que torna reimportar a mesma pasta inofensivo.

    4. **Mesma chave, conteúdo diferente, NUNCA sobrescreve.** O `original.xml`
       é imutável. A cópia nova é guardada ao lado e o documento vai para
       CONFERÊNCIA. Duas hipóteses e nenhuma delas justifica apagar: ou é a
       mesma nota em estágio diferente (resumo → autorizado), e aí a promoção
       resolve; ou são coisas diferentes com a mesma chave, e aí alguém precisa
       olhar.

ORIGENS SÃO REGISTRADAS, O DOCUMENTO NÃO É DUPLICADO
    Um CT-e que chega pela distribuição e depois é importado à mão é UM
    documento com DUAS procedências. O `captura.json` acumula as origens; o
    `original.xml` continua sendo um só. Guardar duas cópias faria a contagem
    do acervo mentir e a exportação entregar o mesmo frete duas vezes.

UMA CAPTURA POR VEZ
    A trava é por `(empresa, serviço, ambiente)` e vale para os dois caminhos.
    Duas capturas simultâneas da mesma empresa avançariam o checkpoint duas
    vezes a partir do mesmo ponto — e no CT-e, sem `consChCTe`, o que for
    pulado não volta.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from . import acervo as acv
from . import checkpoint as cpm
from . import identificacao as idf
from . import trava as trv
from .ambiente import PRODUCAO, resolver as resolver_ambiente
from .identidade import normalizar

SERVICO = cpm.CTE_DISTRIBUICAO

# ── procedências ────────────────────────────────────────────────────────────
# A fonte principal e as duas de apoio. O nome vai para o `captura.json` e é o
# que permite responder "de onde veio este documento?" meses depois.
ORIGEM_DISTRIBUICAO = "CTE_DISTRIBUICAO"
ORIGEM_IMPORTACAO = "IMPORTACAO_XML"
ORIGEM_LEGADO = "LEGADO_CTE"
ORIGENS = (ORIGEM_DISTRIBUICAO, ORIGEM_IMPORTACAO, ORIGEM_LEGADO)

# ── vereditos ───────────────────────────────────────────────────────────────
NOVO = "NOVO"
DUPLICATA = "DUPLICATA"                     # SHA-256 igual: nada foi escrito
PROMOVIDO = "PROMOVIDO"                     # cópia mais completa da mesma chave
COPIA_MENOR = "COPIA_MENOR"                 # guardada, não promovida
CONFLITO = "CONFLITO"                       # mesma chave, conteúdo divergente
CAPTURA_EM_CURSO = "CAPTURA_EM_CURSO"       # a trava estava ocupada; nada foi lido
ILEGIVEL = "ILEGIVEL"
NAO_E_CTE = "NAO_E_CTE"
CHAVE_INVALIDA = "CHAVE_INVALIDA"
EMPRESA_INVALIDA = "EMPRESA_INVALIDA"
ORIGEM_DESCONHECIDA = "ORIGEM_DESCONHECIDA"

ACEITOS = (NOVO, DUPLICATA, PROMOVIDO, COPIA_MENOR)

EXPLICACAO = {
    NOVO: "primeira vez que este documento aparece",
    DUPLICATA: "bytes idênticos aos que já estavam no acervo; nada foi escrito",
    PROMOVIDO: "cópia mais completa da mesma chave; o original anterior foi mantido",
    COPIA_MENOR: "cópia menos completa da mesma chave; guardada ao lado",
    CONFLITO: ("mesma chave com conteúdo divergente — vai para conferência, "
               "e o original NÃO é sobrescrito"),
    CAPTURA_EM_CURSO: ("outra captura desta empresa estava em curso; nada "
                       "foi lido nem gravado — não é um problema do "
                       "documento, e some quando a outra termina"),
    ILEGIVEL: "XML malformado",
    NAO_E_CTE: "o documento não é CT-e nem evento de CT-e",
    CHAVE_INVALIDA: "chave ausente ou com dígito verificador incorreto",
    EMPRESA_INVALIDA: "identidade fiscal da empresa inválida",
    ORIGEM_DESCONHECIDA: "procedência não declarada",
}


@dataclass
class Veredito:
    arquivo: str = ""
    resultado: str = ""
    aceito: bool = False
    chave: str = ""
    especie: str = ""
    id_documento: str = ""
    sha256: str = ""
    origem: str = ""
    quarentena: bool = False
    detalhe: str = ""

    def resumo(self) -> dict:
        return {"arquivo": self.arquivo, "resultado": self.resultado,
                "aceito": self.aceito, "chave": self.chave or None,
                "especie": self.especie or None,
                "sha256": self.sha256[:16] if self.sha256 else None,
                "origem": self.origem or None,
                "quarentena": self.quarentena,
                "detalhe": self.detalhe or EXPLICACAO.get(self.resultado, "")}


@dataclass
class Relatorio:
    identidade_mascarada: str = ""
    origem: str = ""
    vereditos: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.vereditos)

    @property
    def aceitos(self) -> int:
        return sum(1 for v in self.vereditos if v.aceito)

    @property
    def novos(self) -> int:
        return sum(1 for v in self.vereditos if v.resultado == NOVO)

    @property
    def conflitos(self) -> list:
        return [v for v in self.vereditos if v.resultado == CONFLITO]

    def por_resultado(self) -> dict:
        d: dict[str, int] = {}
        for v in self.vereditos:
            d[v.resultado] = d.get(v.resultado, 0) + 1
        return d

    def resumo(self) -> dict:
        return {"empresa": self.identidade_mascarada, "origem": self.origem,
                "total": self.total, "aceitos": self.aceitos,
                "novos": self.novos, "conflitos": len(self.conflitos),
                "por_resultado": self.por_resultado(),
                "confere": self.total == sum(self.por_resultado().values())}


SEM_NSU = "sem-nsu"
"""Rótulo de quem chegou sem NSU. É uma AUSÊNCIA declarada, não um valor.

`_nsu_do_nome` devolve string vazia para ele, e vazio é o que fica gravado —
o acervo passa a dizer "não sei" em vez de dizer um número que ninguém mediu.
"""


def rotulo_de_nsu(nsu) -> str:
    """NSU → rótulo do arquivo. Sem NSU, rótulo de ausência.

    Nunca há posição, contador ou sequência aqui. Um NSU que o serviço não
    mandou não vira 0 só porque foi o primeiro do lote: fabricar rastro é pior
    do que não ter rastro, porque um rastro falso parece verificado.
    """
    n = str(nsu or "").strip()
    return f"nsu-{n}.xml" if n else f"{SEM_NSU}.xml"


def _nsu_do_nome(nome: str) -> str:
    """`nsu-000000000000042.xml` → `000000000000042`. Vazio para importação.

    O NSU é o que liga o documento à resposta que o trouxe; para XML importado
    à mão ele não existe, e inventar um seria fabricar rastro."""
    base = (nome or "").rsplit("/", 1)[-1]
    if base.startswith("nsu-"):
        return base[4:].split(".")[0]
    return ""


def sha256(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def avaliar(conteudo: bytes) -> tuple[str, idf.Identificacao | None, str]:
    """Só as validações, sem gravar. `(resultado, identificacao, detalhe)`.

    Separada de propósito: a tela precisa poder dizer "este XML seria recusado,
    e por quê" antes de o usuário confirmar, e o teste precisa provar a regra
    sem tocar em disco.
    """
    if not conteudo:
        return ILEGIVEL, None, "conteúdo vazio"
    ident = idf.identificar(conteudo)
    if ident.especie == idf.DESCONHECIDO:
        return ILEGIVEL, ident, ident.motivo
    if ident.especie not in (idf.CTE57, idf.EVENTO_CTE,
                            idf.CTE_NAO_SUPORTADO):
        return NAO_E_CTE, ident, f"espécie {ident.especie}"
    if not ident.identidade_forte:
        # Sem chave utilizável o documento AINDA é preservado — mas por hash, e
        # sem deduplicar contra ninguém. Recusar aqui perderia justamente o
        # caso que o acervo existe para guardar.
        return CHAVE_INVALIDA, ident, ident.motivo
    return "", ident, ""


def registrar(dados_dir, identidade, arquivos, *, origem: str,
              ambiente=PRODUCAO, travar: bool = True) -> Relatorio:
    """A ÚNICA porta de escrita do CT-e. `arquivos` = `[(nome, bytes)]`.

    `origem` é obrigatória e fechada: um documento sem procedência declarada é
    um documento que ninguém consegue explicar depois.
    """
    ident = normalizar(identidade)
    amb = resolver_ambiente(ambiente)
    rel = Relatorio(
        identidade_mascarada=(ident.valor[:8] + "***") if ident.valido else "***",
        origem=origem)

    if origem not in ORIGENS:
        for nome, _ in arquivos or ():
            rel.vereditos.append(Veredito(arquivo=nome,
                                          resultado=ORIGEM_DESCONHECIDA,
                                          detalhe=f"origem {origem!r}"))
        return rel

    if not ident.valido:
        for nome, _ in arquivos or ():
            rel.vereditos.append(Veredito(arquivo=nome,
                                          resultado=EMPRESA_INVALIDA,
                                          origem=origem, detalhe=ident.motivo))
        return rel

    if not travar:
        return _registrar(dados_dir, ident, arquivos, origem, amb, rel)

    # UMA CAPTURA POR VEZ. Vale para os dois caminhos: uma importação manual
    # no meio de uma sincronização escreveria no mesmo acervo ao mesmo tempo.
    try:
        with trv.travar(Path(dados_dir), ident.valor, SERVICO, amb):
            return _registrar(dados_dir, ident, arquivos, origem, amb, rel)
    except trv.TravaOcupada as e:
        # NÃO é `CONFLITO`. Conflito é um fato sobre o DOCUMENTO — mesma chave,
        # conteúdo divergente — e manda alguém conferir. Trava ocupada é um
        # fato sobre a MÁQUINA, e some sozinho quando a outra captura termina.
        # Usar o mesmo veredito para os dois fazia uma colisão de trava contar
        # como documento tratado, e o checkpoint andava por cima de arquivos
        # que nunca foram gravados.
        for nome, _ in arquivos or ():
            rel.vereditos.append(Veredito(
                arquivo=nome, resultado=CAPTURA_EM_CURSO, origem=origem,
                detalhe=f"outra captura desta empresa está em curso: {e}"))
        return rel


def _registrar(dados_dir, ident, arquivos, origem, amb, rel) -> Relatorio:
    ac = acv.abrir(dados_dir, ident.valor, servico=origem, ambiente=amb)

    for nome, conteudo in arquivos or ():
        if not isinstance(conteudo, bytes):
            conteudo = str(conteudo).encode("utf-8")
        h = sha256(conteudo)
        resultado, identificacao, detalhe = avaliar(conteudo)

        if resultado and resultado != CHAVE_INVALIDA:
            rel.vereditos.append(Veredito(
                arquivo=nome, resultado=resultado, sha256=h, origem=origem,
                detalhe=detalhe,
                especie=identificacao.especie if identificacao else ""))
            continue

        # A PROCEDÊNCIA vai no `servico` do acervo (que a abertura já
        # recebeu), não num campo de `DocumentoBruto` — ele descreve o que o
        # serviço entregou, não de onde nós o tiramos.
        pres = ac.preservar(acv.DocumentoBruto(
            conteudo=conteudo, nsu=_nsu_do_nome(nome), schema=""))

        # O acervo já decide NOVO / DUPLICATA / promoção por completude. O que
        # esta camada acrescenta é o nome do veredito e a leitura do conflito.
        mapa = {acv.NOVO: NOVO, acv.DUPLICATA: DUPLICATA,
                acv.COPIA_PROMOVIDA: PROMOVIDO, acv.COPIA_MENOR: COPIA_MENOR}
        res = mapa.get(pres.resultado, pres.resultado)

        # Mesma chave, conteúdo diferente e SEM relação de completude: é o
        # caso que precisa de olho humano. O original continua onde está.
        if pres.quarentena or res not in mapa.values():
            res = CONFLITO

        rel.vereditos.append(Veredito(
            arquivo=nome, resultado=res, aceito=res in ACEITOS,
            chave=pres.identificacao.chave, especie=pres.identificacao.especie,
            id_documento=pres.id_documento, sha256=h, origem=origem,
            quarentena=bool(pres.quarentena), detalhe=pres.detalhe))
    return rel


def da_distribuicao(dados_dir, identidade, documentos, *, ambiente=PRODUCAO,
                    travar: bool = True) -> Relatorio:
    """Entrada da FONTE PRINCIPAL — o que veio do `CTeDistribuicaoDFe`.

    `documentos` são os itens do lote já decodificados pelo adaptador. Cada um
    vira `(rótulo, bytes)`; o rótulo carrega o NSU, que é o que permite
    rastrear o documento até a resposta que o trouxe.
    """
    arquivos = []
    for d in documentos or ():
        # DUAS FORMAS DE ENTRADA, e as duas carregam o NSU.
        #
        # `(rótulo, bytes)` — o rótulo JÁ é `nsu-<NSU>.xml`, montado por quem
        # leu a resposta. Ele é repassado inteiro. A versão anterior o
        # desempacotava e jogava fora, procurava `.nsu` numa tupla (que não
        # tem), e caía num `or i` — gravando a POSIÇÃO NO LOTE como se fosse
        # NSU. Foi assim que 50 documentos da TRX ficaram com NSU 0..49
        # enquanto o `ultNSU` da mesma resposta era 75.827.
        if isinstance(d, (tuple, list)) and len(d) == 2:
            nome, conteudo = d
            if conteudo is None:
                continue
            arquivos.append((nome, conteudo))
            continue

        # `DocumentoBruto` — o NSU está no atributo.
        conteudo = getattr(d, "conteudo", None)
        if conteudo is None:
            continue
        arquivos.append((rotulo_de_nsu(getattr(d, "nsu", "")), conteudo))

    return registrar(dados_dir, identidade, arquivos,
                     origem=ORIGEM_DISTRIBUICAO, ambiente=ambiente,
                     travar=travar)


def da_importacao(dados_dir, identidade, arquivos, *, ambiente=PRODUCAO,
                  legado: bool = False, travar: bool = True) -> Relatorio:
    """Entrada de HISTÓRICO E LACUNAS — XML que o usuário traz.

    Passa pela mesma deduplicação da distribuição. Um documento que já veio
    pela captura oficial devolve `DUPLICATA` e não escreve nada: importar por
    cima é inofensivo por construção, não por cuidado de quem clica.
    """
    return registrar(dados_dir, identidade, arquivos,
                     origem=ORIGEM_LEGADO if legado else ORIGEM_IMPORTACAO,
                     ambiente=ambiente, travar=travar)
