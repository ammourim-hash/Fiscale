"""autxml.py — aquisição pelo `autXML`: o escritório como ator autorizado.

O QUE ESTA FONTE RESOLVE
    A Distribuição DF-e entrega documentos aos ATORES da NF-e. O leiaute define
    quatro: emitente (C01), destinatário (E01), transportador (X03) e os
    **autorizados a baixar o XML** — grupo `autXML`, GA01, até dez CNPJ/CPF que
    o emitente escolhe.

    É o quarto que muda tudo para o escritório contábil. Quando o cliente põe o
    CNPJ do escritório em `autXML`, a nota que **ele emitiu** passa a ser
    distribuível para o escritório. É a única via oficial encontrada para
    adquirir emissão própria sem portal e sem ERP (D61).

    Não é exceção nem brecha: é o mecanismo previsto para exatamente este caso.

O QUE MUDA E O QUE NÃO MUDA
    Muda **quem pergunta**. O certificado é o do escritório, e o CNPJ que vai
    no SOAP é o dele. O serviço é o mesmo `NFeDistribuicaoDFe`, o conector é o
    mesmo `conectores/nfe_dfe.py`, o parser é o mesmo, o acervo é o mesmo.

    **O NSU pertence a quem consulta.** Isso não é preferência de projeto: é o
    desenho do serviço. Um NSU é a posição do escritório na fila de documentos
    de interesse *dele*. Por isso existe **um checkpoint por (identidade
    consultante, serviço, ambiente)** — e é por isso que consultar uma vez por
    cliente com o mesmo certificado seria errado duas vezes: repetiria a mesma
    fila N vezes e queimaria a cota do escritório sem trazer nada novo.

O QUE ESTE MÓDULO É
    Uma FONTE. Ele busca, roteia e entrega ao portão. Não interpreta documento,
    não normaliza, não classifica, não calcula. `importacao.importar()` faz o
    resto — o mesmo caminho testado desde a 6B.

ROTEAMENTO, E A REGRA QUE O ORDENA
    O lote vem misturado: notas de vários clientes, eventos, e documentos de
    empresas que não estão no cadastro. Cada documento vai para a empresa a que
    pertence — e **nunca para uma empresa errada**.

    Documento que não deu para rotear **não é descartado**. Ele é preservado
    sob a identidade do escritório, com o papel `AUTORIZADO`, e fica esperando
    o cadastro aparecer. Descartar seria perder o NSU: o serviço não reentrega,
    e o documento simplesmente deixaria de existir para nós.

EVIDÊNCIA, NÃO DECLARAÇÃO
    Marcar uma caixa dizendo "configurei o autXML" não prova nada — nem má-fé,
    só que a configuração pode ter sido feita errado, ou depois. A situação só
    vira `ATIVA` quando **chega documento daquele cliente por esta via**. Até
    lá é `AGUARDANDO`, e depois de várias sincronizações sem nada é
    `SEM_EVIDENCIA`. A diferença entre as três é o que evita o escritório achar
    que está capturando quando não está.

O QUE FICA DE FORA, DE PROPÓSITO
    - **NFC-e (65).** Não há evidência oficial de distribuição equivalente por
      `autXML`. A espécie `NFCE65` existe e o caminho está pronto desde a 6D,
      mas não será forçada aqui sem fonte que a sustente.
    - **Notas anteriores à inclusão do `autXML`.** A autorização vale dali para
      frente. O histórico continua dependendo do e-Fisco e da importação manual.
    - **A ING.** `servico_distribuicao.consultar_empresa` continua sendo a
      porta das consultas por empresa, com o checkpoint dela. Este módulo tem
      identidade e checkpoint próprios e não toca naquilo.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .ambiente import PRODUCAO, resolver as resolver_ambiente
from . import resposta_bruta as bruta
from .distribuicao import normalizar_nsu
from .identidade import normalizar

ARQUIVO_CONFIG = "autxml.json"
SERVICO = "NFE_DISTRIBUICAO"

# ── situação da autorização de cada cliente ─────────────────────────────────
NAO_CONFIGURADA = "NAO_CONFIGURADA"   # o cliente nem declarou
AGUARDANDO = "AGUARDANDO"             # declarou; ainda não chegou documento
ATIVA = "ATIVA"                       # chegou documento por esta via
SEM_EVIDENCIA = "SEM_EVIDENCIA"       # declarou há tempo e nada chegou
SITUACOES = (NAO_CONFIGURADA, AGUARDANDO, ATIVA, SEM_EVIDENCIA)

# Depois de quantas sincronizações sem nada a declaração perde credibilidade.
# Escolha nossa, conservadora e revisável — não é regra de lugar nenhum.
SINCRONIZACOES_PARA_DUVIDAR = 3

# ── por que um documento não foi roteado ────────────────────────────────────
EMPRESA_NAO_CADASTRADA = "empresa não cadastrada"
SEM_PARTICIPANTE_LEGIVEL = "não foi possível ler emitente nem destinatário"

_SO_DIG = re.compile(r"\D")
_NS = re.compile(r"^\{[^}]*\}")


def _dig(v) -> str:
    return _SO_DIG.sub("", str(v or ""))


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ════════════════════════════════════════════════════════════════════════════
#  Configuração do escritório
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class ClienteAutorizado:
    """Um cliente que declarou ter posto o escritório no `autXML`."""
    identidade: str
    declarada_em: str = ""
    situacao: str = NAO_CONFIGURADA
    primeira_evidencia_em: str = ""
    ultima_sincronizacao: str = ""
    documentos_roteados: int = 0
    sincronizacoes_sem_documento: int = 0

    def para_json(self) -> dict:
        return {"identidade": self.identidade,
                "mascarado": normalizar(self.identidade).mascarado(),
                "declarada_em": self.declarada_em or None,
                "situacao": self.situacao,
                "primeira_evidencia_em": self.primeira_evidencia_em or None,
                "ultima_sincronizacao": self.ultima_sincronizacao or None,
                "documentos_roteados": self.documentos_roteados}


@dataclass
class Configuracao:
    """O que o escritório configurou. **Desativada por padrão.**

    `ativa` começa `False` de propósito: uma fonte que fala com a SEFAZ não
    deve entrar em operação por existir no disco. Ligar é decisão explícita.
    """
    identidade: str = ""            # CNPJ/CPF do escritório — quem consulta
    certificado: str = ""           # apelido; o .pfx continua onde sempre esteve
    ambiente: str = "producao"
    ativa: bool = False
    clientes: list = field(default_factory=list)

    @property
    def valida(self) -> bool:
        return normalizar(self.identidade).valido

    def cliente(self, identidade) -> ClienteAutorizado | None:
        alvo = normalizar(identidade).valor
        for c in self.clientes:
            if normalizar(c.identidade).valor == alvo:
                return c
        return None

    def declarar(self, identidade) -> ClienteAutorizado:
        """O cliente diz que configurou. Vira `AGUARDANDO`, nunca `ATIVA`."""
        c = self.cliente(identidade)
        if c is None:
            c = ClienteAutorizado(identidade=normalizar(identidade).valor
                                  or str(identidade))
            self.clientes.append(c)
        if c.situacao in (NAO_CONFIGURADA, SEM_EVIDENCIA):
            c.situacao = AGUARDANDO
            c.declarada_em = c.declarada_em or _agora()
        return c

    def para_json(self) -> dict:
        return {"identidade": normalizar(self.identidade).mascarado(),
                "certificado": self.certificado or None,
                "ambiente": self.ambiente, "ativa": self.ativa,
                "clientes": [c.para_json() for c in self.clientes]}


def caminho_config(dados_dir) -> Path:
    return Path(dados_dir) / ARQUIVO_CONFIG


def carregar_config(dados_dir) -> Configuracao:
    try:
        d = json.loads(caminho_config(dados_dir).read_text("utf-8"))
    except (OSError, ValueError):
        return Configuracao()
    cfg = Configuracao(identidade=d.get("identidade") or "",
                       certificado=d.get("certificado") or "",
                       ambiente=d.get("ambiente") or "producao",
                       ativa=bool(d.get("ativa")))
    for c in d.get("clientes") or []:
        if not isinstance(c, dict) or not c.get("identidade"):
            continue
        cfg.clientes.append(ClienteAutorizado(
            identidade=c["identidade"],
            declarada_em=c.get("declarada_em") or "",
            situacao=c.get("situacao") if c.get("situacao") in SITUACOES
            else NAO_CONFIGURADA,
            primeira_evidencia_em=c.get("primeira_evidencia_em") or "",
            ultima_sincronizacao=c.get("ultima_sincronizacao") or "",
            documentos_roteados=int(c.get("documentos_roteados") or 0),
            sincronizacoes_sem_documento=int(
                c.get("sincronizacoes_sem_documento") or 0)))
    return cfg


def salvar_config(dados_dir, cfg: Configuracao) -> Path:
    p = caminho_config(dados_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    # A identidade vai INTEIRA no arquivo (é preciso para consultar); o
    # mascaramento é só do relatório.
    p.write_text(json.dumps({
        "identidade": cfg.identidade, "certificado": cfg.certificado,
        "ambiente": cfg.ambiente, "ativa": cfg.ativa,
        "clientes": [{"identidade": c.identidade,
                      "declarada_em": c.declarada_em,
                      "situacao": c.situacao,
                      "primeira_evidencia_em": c.primeira_evidencia_em,
                      "ultima_sincronizacao": c.ultima_sincronizacao,
                      "documentos_roteados": c.documentos_roteados,
                      "sincronizacoes_sem_documento":
                          c.sincronizacoes_sem_documento}
                     for c in cfg.clientes]},
        indent=1, ensure_ascii=False), encoding="utf-8")
    return p


def identidade_do_hub(dados_dir) -> str:
    """O CNPJ do escritório que consulta pelo `autXML`, ou vazio.

    NÃO HÁ FLAG NOVA, E ISSO É DE PROPÓSITO
        Quem é o hub já está escrito em `autxml.json`. Criar um segundo lugar
        para dizer a mesma coisa é criar a chance de os dois discordarem — e o
        dia em que discordassem, o escritório apareceria como cliente numa tela
        e não noutra, sem ninguém saber qual está certa.
    """
    try:
        return normalizar(carregar_config(dados_dir).identidade).valor
    except Exception:
        return ""


def e_hub(dados_dir, identidade) -> bool:
    """Esta identidade é o escritório, e não um cliente?"""
    alvo = normalizar(identidade).valor
    hub = identidade_do_hub(dados_dir)
    return bool(alvo and hub and alvo == hub)


# ════════════════════════════════════════════════════════════════════════════
#  Roteamento
# ════════════════════════════════════════════════════════════════════════════
EMISSAO_PROPRIA = "EMISSAO_PROPRIA"
RECEBIDO = "RECEBIDO"
EVENTO = "EVENTO"
NAO_ROTEADO = "NAO_ROTEADO"


@dataclass(frozen=True)
class Destino:
    """Para quem vai este documento, e por quê."""
    identidade: str
    papel: str
    motivo: str = ""


def _local(tag: str) -> str:
    return _NS.sub("", tag or "")


def _achar(raiz, *nomes) -> str:
    alvo = set(nomes)
    for e in raiz.iter():
        if _local(e.tag) in alvo:
            return (e.text or "").strip()
    return ""


def _bloco(raiz, nome: str):
    for e in raiz.iter():
        if _local(e.tag) == nome:
            return e
    return None


def destino_do_documento(conteudo: bytes, cadastradas) -> Destino:
    """A quem pertence este documento, entre as empresas cadastradas.

    A ordem importa: **emitente antes de destinatário**. Se o cliente emitiu, é
    emissão própria dele, e é isso que estamos buscando com o `autXML`. Só
    depois se pergunta se ele recebeu.

    Evento não traz emitente nem destinatário — traz `chNFe`. O CNPJ do
    emitente está nas posições 7 a 20 da chave, e é por lá que ele encontra
    dono. Um evento sem chave legível não tem como ser roteado, e é dito.
    """
    import xml.etree.ElementTree as ET

    conhecidas = {normalizar(c).valor for c in (cadastradas or ())
                  if normalizar(c).valido}
    try:
        raiz = ET.fromstring(conteudo)
    except Exception:
        return Destino("", NAO_ROTEADO, SEM_PARTICIPANTE_LEGIVEL)

    chave = _dig(_achar(raiz, "chNFe", "chCTe"))
    e_evento = bool(_achar(raiz, "tpEvento"))

    if e_evento:
        dono = chave[6:20] if len(chave) == 44 else ""
        if dono and dono in conhecidas:
            return Destino(dono, EVENTO)
        return Destino("", NAO_ROTEADO,
                       EMPRESA_NAO_CADASTRADA if dono else SEM_PARTICIPANTE_LEGIVEL)

    emit = _bloco(raiz, "emit")
    dest = _bloco(raiz, "dest")
    cnpj_emit = _dig(_achar(emit, "CNPJ", "CPF")) if emit is not None else ""
    cnpj_dest = _dig(_achar(dest, "CNPJ", "CPF")) if dest is not None else ""
    if not cnpj_emit and len(chave) == 44:
        cnpj_emit = chave[6:20]      # o resumo `resNFe` não traz `<emit>`

    if cnpj_emit and cnpj_emit in conhecidas:
        return Destino(cnpj_emit, EMISSAO_PROPRIA)
    if cnpj_dest and cnpj_dest in conhecidas:
        return Destino(cnpj_dest, RECEBIDO)
    if not cnpj_emit and not cnpj_dest:
        return Destino("", NAO_ROTEADO, SEM_PARTICIPANTE_LEGIVEL)
    return Destino("", NAO_ROTEADO, EMPRESA_NAO_CADASTRADA)


# ════════════════════════════════════════════════════════════════════════════
#  Relatórios
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class RelatorioRoteamento:
    """O que veio no lote e para onde foi."""
    documentos: int = 0
    por_empresa: dict = field(default_factory=dict)   # cnpj → contagem
    por_papel: dict = field(default_factory=dict)
    nao_roteados: int = 0
    motivos_nao_roteado: dict = field(default_factory=dict)
    preservados: int = 0
    duplicados: int = 0
    rejeitados: int = 0
    indice_em_dia: bool = True

    def resumo(self) -> dict:
        return {"documentos": self.documentos,
                "por_empresa": {normalizar(k).mascarado(): v
                                for k, v in sorted(self.por_empresa.items())},
                "por_papel": dict(sorted(self.por_papel.items())),
                "nao_roteados": self.nao_roteados,
                "motivos_nao_roteado": dict(sorted(
                    self.motivos_nao_roteado.items())),
                "preservados": self.preservados,
                "duplicados": self.duplicados,
                "rejeitados": self.rejeitados,
                "indice_em_dia": self.indice_em_dia}


@dataclass
class RelatorioSincronizacao:
    identidade: str = ""
    executou: bool = False
    motivo: str = ""
    nsu_antes: str = ""
    nsu_depois: str = ""
    lotes: int = 0
    roteamento: RelatorioRoteamento = field(default_factory=RelatorioRoteamento)

    def resumo(self) -> dict:
        return {"escritorio": normalizar(self.identidade).mascarado(),
                "executou": self.executou, "motivo": self.motivo or None,
                "nsu_antes": self.nsu_antes, "nsu_depois": self.nsu_depois,
                "lotes": self.lotes, "roteamento": self.roteamento.resumo()}


# ════════════════════════════════════════════════════════════════════════════
#  Entrega ao portão
# ════════════════════════════════════════════════════════════════════════════
def rotear_documentos(dados_dir, cfg: Configuracao, documentos,
                      cadastradas, *, ambiente=PRODUCAO) -> RelatorioRoteamento:
    """Distribui os documentos do lote e entrega cada grupo ao PORTÃO.

    `documentos` = iterável de `(nome, bytes)`.

    Nada é interpretado aqui além do necessário para saber o dono. Validação,
    identidade, dedup, índice e classificação continuam sendo do portão — e é
    ele que resolve, por identidade e hash, o documento que já tinha vindo por
    outra fonte: vira `DUPLICATA`, nunca uma segunda nota.
    """
    from . import importacao as imp

    rel = RelatorioRoteamento()
    grupos: dict = {}
    escritorio = normalizar(cfg.identidade).valor

    for nome, conteudo in documentos or ():
        rel.documentos += 1
        d = destino_do_documento(conteudo, cadastradas)
        if d.papel == NAO_ROTEADO:
            rel.nao_roteados += 1
            rel.motivos_nao_roteado[d.motivo] = \
                rel.motivos_nao_roteado.get(d.motivo, 0) + 1
            # NUNCA descartar: preserva sob o escritório, que consta no
            # `autXML` e portanto é participante legítimo do documento.
            if escritorio:
                grupos.setdefault(escritorio, []).append((nome, conteudo))
            continue
        rel.por_papel[d.papel] = rel.por_papel.get(d.papel, 0) + 1
        rel.por_empresa[d.identidade] = rel.por_empresa.get(d.identidade, 0) + 1
        grupos.setdefault(d.identidade, []).append((nome, conteudo))

    for identidade, arquivos in grupos.items():
        r = imp.importar(dados_dir, identidade, arquivos, ambiente=ambiente)
        rel.preservados += r.preservados
        rel.duplicados += r.duplicados
        rel.rejeitados += r.rejeitados
        rel.indice_em_dia = rel.indice_em_dia and r.indice_em_dia
    return rel


def _atualizar_situacoes(cfg: Configuracao, rel: RelatorioRoteamento) -> None:
    """Evidência documental manda; declaração não.

    Quem recebeu documento vira `ATIVA` e não volta atrás — a autorização já
    foi comprovada uma vez. Quem declarou e continua sem nada acumula
    silêncio até virar `SEM_EVIDENCIA`, que é um convite a conferir a
    configuração, não uma acusação."""
    agora = _agora()
    for c in cfg.clientes:
        alvo = normalizar(c.identidade).valor
        vieram = rel.por_empresa.get(alvo, 0)
        c.ultima_sincronizacao = agora
        if vieram:
            c.documentos_roteados += vieram
            c.sincronizacoes_sem_documento = 0
            if c.situacao != ATIVA:
                c.situacao = ATIVA
                c.primeira_evidencia_em = c.primeira_evidencia_em or agora
            continue
        if c.situacao == AGUARDANDO:
            c.sincronizacoes_sem_documento += 1
            if c.sincronizacoes_sem_documento >= SINCRONIZACOES_PARA_DUVIDAR:
                c.situacao = SEM_EVIDENCIA


def sincronizar(dados_dir, cfg: Configuracao, fabrica_fonte, cadastradas, *,
                max_lotes: int = 1, repo_cp=None,
                ambiente=None) -> RelatorioSincronizacao:
    """Um ciclo de aquisição pelo `autXML`. **Uma consulta serve todos.**

    O checkpoint é do ESCRITÓRIO — `(identidade consultante, serviço,
    ambiente)`. Não existe um por cliente, e é por isso que não se consulta uma
    vez por cliente: seria a mesma fila, repetida.

    O ponteiro só avança **depois** de o portão confirmar a persistência do
    lote. Um lote perdido com NSU avançado é documento que não volta.
    """
    from . import checkpoint as cpm

    amb = resolver_ambiente(ambiente or cfg.ambiente or "producao")
    rel = RelatorioSincronizacao(identidade=cfg.identidade)

    if not cfg.ativa:
        rel.motivo = "fonte autXML desativada"
        return rel
    if not cfg.valida:
        rel.motivo = "identidade do escritório inválida"
        return rel

    repo = repo_cp or cpm.RepositorioCheckpoint(dados_dir)
    ident = normalizar(cfg.identidade).valor
    cp = repo.carregar(ident, SERVICO, amb)
    rel.nsu_antes = rel.nsu_depois = cp.ult_nsu

    fonte = fabrica_fonte(ident, amb)
    for _ in range(max(1, max_lotes)):
        lote = fonte.consultar(rel.nsu_depois)
        if lote is None or lote.vazio:
            break
        arquivos = [(f"nsu-{d.nsu}.xml", d.conteudo) for d in lote.documentos]
        r = rotear_documentos(dados_dir, cfg, arquivos, cadastradas,
                              ambiente=amb)
        for campo in ("documentos", "nao_roteados", "preservados",
                      "duplicados", "rejeitados"):
            setattr(rel.roteamento, campo,
                    getattr(rel.roteamento, campo) + getattr(r, campo))
        for alvo, origem in ((rel.roteamento.por_empresa, r.por_empresa),
                             (rel.roteamento.por_papel, r.por_papel),
                             (rel.roteamento.motivos_nao_roteado,
                              r.motivos_nao_roteado)):
            for k, v in origem.items():
                alvo[k] = alvo.get(k, 0) + v
        rel.roteamento.indice_em_dia = (rel.roteamento.indice_em_dia
                                        and r.indice_em_dia)
        rel.lotes += 1

        # A RESPOSTA BRUTA, antes de o ponteiro andar.
        #
        # Este módulo é o ÚNICO caminho de captura que não passa pelo
        # `DistribuicaoRunner` — ele carrega o próprio checkpoint, algumas
        # linhas abaixo. Por isso não herdou a preservação que o runner ganhou
        # na NF-e 7, e ficaria sendo a única via que avança o NSU sem deixar
        # prova no disco do que o serviço respondeu.
        #
        # Foi a falta dessa prova que tornou irrecuperáveis os NSU reais dos 72
        # CT-e da CTE 4: os documentos estavam certos, a leitura de um campo
        # estava errada, e não havia de onde reler.
        #
        # `preservar()` grava, relê e confere o SHA-256. Não conseguir guardar
        # impede o avanço — a mesma promessa do portão, estendida à prova.
        bruto = getattr(getattr(fonte, "ultima_resposta", None), "bruto", b"")
        prova_ok = True
        if bruto:
            try:
                bruta.preservar(dados_dir, ident, bruto, servico=SERVICO,
                                ambiente=amb)
            except Exception as exc:
                prova_ok = False
                rel.motivo = (f"resposta bruta não preservada: "
                              f"{type(exc).__name__}; o checkpoint não avança")

        # Só agora o ponteiro anda — e só depois de o portão ter absorvido o
        # lote. Avançar antes é como se perde documento: o NSU não volta.
        absorvido = (r.preservados + r.duplicados) > 0 or r.documentos == 0
        if not prova_ok:
            break
        if absorvido and lote.ult_nsu:
            cp.ult_nsu = normalizar_nsu(lote.ult_nsu)
            if lote.max_nsu:
                cp.registrar_observacao_sefaz(lote.max_nsu)
                cp.registrar_sincronia_confirmada(lote.ult_nsu, lote.max_nsu)
            repo.salvar(cp)
            rel.nsu_depois = cp.ult_nsu
        if lote.ult_nsu and lote.max_nsu and lote.ult_nsu >= lote.max_nsu:
            break

    rel.executou = True
    _atualizar_situacoes(cfg, rel.roteamento)
    salvar_config(dados_dir, cfg)
    return rel
