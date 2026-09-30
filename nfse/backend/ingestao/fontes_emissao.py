"""fontes_emissao.py — de onde vêm as NF-e/NFC-e que a EMPRESA emitiu.

O PROBLEMA QUE ESTA CAMADA RESOLVE
    A Distribuição DF-e entrega o que a empresa **não** gerou (D2). A emissão
    própria nasce no emissor do cliente e só chega ao escritório se alguém a
    trouxer. Hoje isso é manual: alguém copia XML para uma pasta.

    Existem várias origens possíveis — a pasta em que o emissor já grava, uma
    pasta sincronizada (Drive, rede), no futuro uma API de ERP — e cada uma
    fala uma língua. Sem uma camada como esta, cada origem viraria um caminho
    próprio até o disco, e o FISCALE teria de novo o problema que a APURAÇÃO 6B
    acabou de resolver: mais de um lugar gravando documento fiscal.

O CONTRATO, E O QUE ELE DELIBERADAMENTE NÃO INCLUI
    Uma fonte faz **uma coisa**: entrega `(nome, bytes, metadados de origem)`.

    Ela NÃO valida, NÃO decide identidade, NÃO deduplica, NÃO grava, NÃO sabe
    o que é emitente, protocolo, CFOP ou competência. Tudo isso já existe e
    pertence ao portão (`importacao.py`), ao acervo e à normalização. Uma fonte
    que soubesse validar seria uma segunda opinião sobre o que é documento
    válido — e duas opiniões divergem.

    Por isso `coletar()` tem quatro linhas úteis: pega os documentos da fonte e
    entrega ao portão. Se um dia ela crescer, é sinal de que alguma regra
    escorregou para o lugar errado.

    fonte → bytes → importacao.importar() → acervo → índice → normalização →
    vendas → rastreabilidade

O QUE EXISTE HOJE, DE VERDADE
    `PastaEmissor` — uma pasta que se enche sozinha. É a única fonte
    implementada, porque é a única que o ambiente atual comporta: o inventário
    da APURAÇÃO 6D não encontrou nenhum emissor de NF-e/NFC-e instalado nesta
    máquina, nenhuma API configurada e nenhuma exportação automática.

    Não há conector de ERP aqui, e a ausência é proposital: escrever um sem
    saber qual ERP cada cliente usa seria inventar um protocolo e chamá-lo de
    integração.

NUNCA MOVE, NUNCA APAGA
    A pasta de origem pode ser a do emissor do cliente, uma pasta de rede ou
    uma pasta sincronizada. Ela não é nossa. A fonte **lê** e pronto — quem
    guarda é o acervo, e o original fica lá, imutável, com hash.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from .ambiente import PRODUCAO
from .identidade import normalizar

# ── tipos de fonte ──────────────────────────────────────────────────────────
PASTA = "pasta"                 # implementada
API_ERP = "api_erp"             # contrato previsto, sem implementação
TIPOS_PREVISTOS = (PASTA, API_ERP)

ARQUIVO_CONFIG = "fontes_emissao.json"


@dataclass(frozen=True)
class DocumentoDeOrigem:
    """Um arquivo como a fonte o encontrou. Bytes crus e de onde vieram.

    `origem` é livre de propósito: cada fonte sabe o que é relevante guardar —
    caminho e data de modificação numa pasta, id externo numa API. Isso vai
    para o relatório, não para o acervo: o acervo já registra a sua própria
    procedência em `captura.json`.
    """
    nome: str
    conteudo: bytes
    origem: dict = field(default_factory=dict)


@runtime_checkable
class FonteEmissao(Protocol):
    """O contrato que qualquer origem precisa cumprir. Só isto."""
    tipo: str
    identidade: str

    def descrever(self) -> dict:
        """Como esta fonte se apresenta no relatório e na tela."""

    def documentos(self) -> Iterable[DocumentoDeOrigem]:
        """Os documentos disponíveis agora. Pode ser um gerador."""


@dataclass
class PastaEmissor:
    """Uma pasta que se enche sozinha: o emissor grava, o Drive sincroniza.

    É a fonte automática que o ambiente de hoje permite. Não precisa de API,
    de credencial nova nem de acordo com fabricante de ERP: quase todo emissor
    de NF-e/NFC-e grava o XML autorizado em algum diretório, e basta apontar
    para ele.

    `recursiva` porque emissor costuma separar por ano/mês, e `padrao` porque
    alguns gravam `.XML` maiúsculo — detalhes que, se não forem tratados aqui,
    viram "o sistema não achou minha nota".
    """
    identidade: str
    pasta: Path | str
    recursiva: bool = True
    padrao: str = "*.xml"
    tipo: str = field(default=PASTA, init=False)

    def __post_init__(self):
        self.pasta = Path(self.pasta).expanduser()

    @property
    def disponivel(self) -> bool:
        try:
            return Path(self.pasta).is_dir()
        except OSError:
            # Pasta de rede fora do ar não é erro de programa: é indisponível.
            return False

    def descrever(self) -> dict:
        return {"tipo": self.tipo, "pasta": str(self.pasta),
                "recursiva": self.recursiva, "padrao": self.padrao,
                "disponivel": self.disponivel,
                "empresa": normalizar(self.identidade).mascarado()}

    def documentos(self) -> Iterable[DocumentoDeOrigem]:
        if not self.disponivel:
            return
        base = Path(self.pasta)
        achar = base.rglob if self.recursiva else base.glob
        for arq in sorted(achar(self.padrao)):
            try:
                conteudo = arq.read_bytes()
                mtime = arq.stat().st_mtime
            except OSError:
                # Arquivo sendo escrito pelo emissor neste instante, ou sem
                # permissão. Pular é certo: ele continua na pasta e entra na
                # próxima coleta. Apagar ou travar aqui, não.
                continue
            yield DocumentoDeOrigem(
                nome=arq.name, conteudo=conteudo,
                origem={"caminho": str(arq),
                        "modificado_em": datetime.fromtimestamp(
                            mtime, timezone.utc).isoformat(timespec="seconds"),
                        "bytes": len(conteudo)})


def de_config(identidade: str, cfg: dict) -> FonteEmissao | None:
    """Constrói a fonte descrita por um dicionário de configuração.

    Devolve `None` para tipo previsto mas não implementado — `API_ERP` é o
    caso. Não levanta: uma configuração de ERP salva antes de existir conector
    não pode impedir a pasta da empresa ao lado de funcionar."""
    tipo = (cfg or {}).get("tipo") or PASTA
    if tipo == PASTA and cfg.get("pasta"):
        return PastaEmissor(identidade=identidade, pasta=cfg["pasta"],
                            recursiva=bool(cfg.get("recursiva", True)),
                            padrao=cfg.get("padrao") or "*.xml")
    return None


# ── configuração por empresa ────────────────────────────────────────────────
def caminho_config(dados_dir, identidade) -> Path:
    ident = normalizar(identidade)
    alvo = ident.valor if ident.valido else str(identidade)
    return Path(dados_dir) / alvo / ARQUIVO_CONFIG


def carregar_config(dados_dir, identidade) -> list[dict]:
    p = caminho_config(dados_dir, identidade)
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    fontes = d.get("fontes") if isinstance(d, dict) else d
    return [f for f in (fontes or []) if isinstance(f, dict)]


def salvar_config(dados_dir, identidade, fontes: list[dict]) -> Path:
    p = caminho_config(dados_dir, identidade)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fontes": list(fontes or [])}, indent=1,
                            ensure_ascii=False), encoding="utf-8")
    return p


def fontes_da_empresa(dados_dir, identidade) -> list:
    """As fontes configuradas que dá para construir hoje."""
    saida = []
    for cfg in carregar_config(dados_dir, identidade):
        f = de_config(identidade, cfg)
        if f is not None:
            saida.append(f)
    return saida


# ── a coleta ────────────────────────────────────────────────────────────────
@dataclass
class RelatorioColeta:
    """O que a fonte trouxe e o que o portão fez com aquilo."""
    identidade: str = ""
    fonte: dict = field(default_factory=dict)
    encontrados: int = 0
    indisponivel: bool = False
    painel: dict = field(default_factory=dict)

    def resumo(self) -> dict:
        q = self.painel or {}
        return {"empresa": normalizar(self.identidade).mascarado(),
                "fonte": self.fonte,
                "indisponivel": self.indisponivel,
                "encontrados_na_fonte": self.encontrados,
                "preservados": q.get("preservados", 0),
                "duplicados": q.get("duplicados", 0),
                "rejeitados": q.get("rejeitados", 0),
                "indexados": q.get("indexados", 0),
                "indice_em_dia": q.get("indice_em_dia", False),
                "proprias_validas": q.get("nfe_proprias_validas", 0)}


def coletar(dados_dir, identidade, fonte: FonteEmissao, *,
            ambiente=PRODUCAO) -> RelatorioColeta:
    """Traz o que a fonte tem e entrega ao PORTÃO. É só isso, e é de propósito.

    Idempotente porque o portão é: reexecutar sobre a mesma pasta devolve
    duplicatas e não escreve byte. Nenhum estado de "já li este arquivo" é
    guardado aqui — esse estado existiria para evitar releitura e passaria a
    ser uma segunda memória do que entrou, competindo com o acervo.
    """
    from . import importacao as imp

    rel = RelatorioColeta(identidade=str(identidade),
                          fonte=fonte.descrever() if fonte else {})
    if fonte is None or not rel.fonte.get("disponivel", True):
        rel.indisponivel = True
        return rel

    arquivos = [(d.nome, d.conteudo) for d in fonte.documentos()]
    rel.encontrados = len(arquivos)
    rel.painel = imp.painel(dados_dir, identidade, arquivos, ambiente=ambiente)
    return rel


def coletar_todas(dados_dir, identidade, *, ambiente=PRODUCAO) -> list:
    """Percorre as fontes configuradas da empresa. Uma falha não derruba as outras."""
    return [coletar(dados_dir, identidade, f, ambiente=ambiente)
            for f in fontes_da_empresa(dados_dir, identidade)]
