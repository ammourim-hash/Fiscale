#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Central de Atualizações Fiscais — a coleta.

    from fiscal_atualizacoes import Central
    c = Central(dados_dir)
    c.verificar()        # vai às fontes, arquiva o que é novo
    c.listar()           # o que está guardado, mais recente primeiro
    c.nao_lidos()        # quantos chegaram desde a última leitura

O QUE ESTE MÓDULO FAZ
    Lê feeds oficiais, guarda cada publicação com o link, e diz o que é novo.
    Nada além disso.

O QUE ELE NUNCA FAZ  (AI0 §7.1, risco R5)
    Não altera alíquota, anexo, regra de cálculo, cadastro ou documento.
    Uma publicação **nunca** muda um número sozinha. Entre a notícia e o
    cálculo existe revisão humana, e ela é uma fase própria (AI 6) que não
    está aqui. Este módulo é jornal, não motor.

DUAS REGRAS DA CASA, HERDADAS DO DIAGNÓSTICO
    1. **Item sem link não entra** (§7.4). Notícia que não se pode conferir na
       fonte é boato com aparência de informação.
    2. **Fonte que não responde vira "não verificado", nunca "nada mudou"**
       (§7.2, mesmo princípio de D53). Silêncio de fonte não é prova de
       ausência — e é justamente o dia em que a fonte cai que alguém precisa
       saber que não olhou.

SOBRE "TEMPO REAL", COM HONESTIDADE
    Não existe publicação em tempo real de legislação tributária no Brasil.
    Não há push oficial: o que existe é feed que o portal atualiza quando
    publica, e portal que só tem HTML. O mais próximo de tempo real que dá
    para prometer sem mentir é **verificar de hora em hora e avisar assim que
    aparecer** — e é isso que está implementado. O intervalo é configurável.

    Medido em 25/08/2026, das fontes candidatas do §7.2, **uma** entrega feed
    utilizável (Receita Federal). As demais devolvem 404, tempo esgotado ou
    HTML sem feed. Elas ficam cadastradas assim mesmo, com o estado real, para
    a tela dizer "não verificado" em vez de fingir cobertura que não há.
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

# ── estados de uma fonte ────────────────────────────────────────────────────
OK = "OK"
NAO_VERIFICADO = "NAO_VERIFICADO"          # tentou e não conseguiu
SEM_FEED = "SEM_FEED"                      # a fonte não publica feed nenhum

ROTULO_ESTADO = {
    OK: "verificada",
    NAO_VERIFICADO: "não verificada",
    SEM_FEED: "sem feed oficial",
}

# ── temas, para a tela filtrar ──────────────────────────────────────────────
# Palavra que aparece no título ou no resumo. É classificação grosseira e
# assumida como tal: serve para achar, não para decidir nada.
TEMAS = {
    "REFORMA": ("reforma tribut", "cbs", "ibs", "imposto seletivo",
                "split payment", "lc 214", "ec 132", "cclasstrib"),
    "SIMPLES": ("simples nacional", "mei", "pgdas", "defis", "cgsn"),
    "DOCUMENTOS": ("nf-e", "nfe", "nfc-e", "nfs-e", "ct-e", "mdf-e",
                   "nota técnica", "nota tecnica", "danfe", "sped"),
    "OBRIGACOES": ("dctf", "efd", "reinf", "ecd", "ecf", "dirf", "e-social",
                   "esocial", "declara"),
}

ROTULO_TEMA = {
    "REFORMA": "Reforma Tributária",
    "SIMPLES": "Simples Nacional",
    "DOCUMENTOS": "Documentos fiscais",
    "OBRIGACOES": "Obrigações acessórias",
    "GERAL": "Geral",
}


# ── as fontes ───────────────────────────────────────────────────────────────
# `feed` vazio = a fonte não tem feed oficial conhecido. Ela fica na lista de
# propósito: a tela precisa mostrar o que NÃO está sendo coberto. Cobertura
# imaginada é pior que cobertura ausente.
@dataclass
class Fonte:
    id: str
    nome: str
    feed: str
    site: str
    observacao: str = ""

    @property
    def tem_feed(self) -> bool:
        return bool(self.feed)


FONTES = (
    Fonte("rfb", "Receita Federal",
          "https://www.gov.br/receitafederal/pt-br/assuntos/noticias/RSS",
          "https://www.gov.br/receitafederal/pt-br/assuntos/noticias"),
    Fonte("confaz", "CONFAZ — convênios e ajustes SINIEF", "",
          "https://www.confaz.fazenda.gov.br/legislacao/atos",
          "O portal publica só em HTML; não há feed. Verificado em 25/08/2026."),
    Fonte("portal_nfe", "Portal Nacional da NF-e — notas técnicas", "",
          "https://www.nfe.fazenda.gov.br/portal/informe.aspx",
          "Área de avisos sem feed e com redirecionamento. "
          "Verificado em 25/08/2026."),
    Fonte("nfse_nacional", "Portal Nacional da NFS-e", "",
          "https://www.gov.br/nfse/pt-br",
          "Sem feed publicado. Verificado em 25/08/2026."),
    Fonte("reforma", "Reforma Tributária — Ministério da Fazenda", "",
          "https://www.gov.br/fazenda/pt-br/acesso-a-informacao/acoes-e-programas/reforma-tributaria",
          "A página existe, o feed não. Verificado em 25/08/2026."),
    Fonte("sefaz_pe", "SEFAZ-PE", "",
          "https://www.sefaz.pe.gov.br",
          "Sem feed publicado. Verificado em 25/08/2026."),
)

FONTES_POR_ID = {f.id: f for f in FONTES}

TEMPO_LIMITE = 20          # segundos por fonte
MAXIMO_GUARDADO = 500      # publicações no arquivo; as mais velhas saem
INTERVALO_PADRAO = 3600    # 1 hora


@dataclass
class Publicacao:
    id: str
    fonte: str
    titulo: str
    link: str
    resumo: str = ""
    publicado_em: str = ""
    visto_em: str = ""
    temas: list = field(default_factory=list)

    def dict(self) -> dict:
        return asdict(self)


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _limpar(texto: str) -> str:
    """Tira marcação e espaço sobrando. O resumo vem com HTML dentro."""
    sem_tag = re.sub(r"<[^>]+>", " ", texto or "")
    sem_ent = (sem_tag.replace("&nbsp;", " ").replace("&amp;", "&")
               .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
               .replace("&#39;", "'"))
    return re.sub(r"\s+", " ", sem_ent).strip()


def classificar(titulo: str, resumo: str) -> list:
    """Quais temas o texto toca. Pode ser mais de um; pode ser nenhum."""
    alvo = (titulo + " " + resumo).lower()
    achados = [t for t, palavras in TEMAS.items()
               if any(p in alvo for p in palavras)]
    return achados or ["GERAL"]


# ── o que NÃO é publicação ──────────────────────────────────────────────────
# O feed da Receita é gerado pelo Plone e lista a ESTRUTURA do portal junto
# com o conteúdo: pastas, coleções, atalhos e imagens entram como <item>.
# Medido em 25/08/2026, das 10 entradas do feed, 7 eram navegação — "Carrossel",
# "Coleção", "Todas as notícias", uma foto de Foz do Iguaçu.
#
# O que separa é o `dc:type`: notícia é `collective.nitf.content`; o resto se
# identifica. A lista abaixo é de EXCLUSÃO, não de inclusão — feed de outro
# portal que não use `dc:type` continua passando inteiro, em vez de sumir por
# não conhecermos o tipo dele.
TIPOS_ESTRUTURAIS = {
    "folder", "collection", "link", "image", "file", "topic",
    "plone site", "largefolder", "folderish",
}


def estrutural(tipo: str, link: str) -> bool:
    """É item de navegação do portal, e não uma publicação?"""
    if (tipo or "").strip().lower() in TIPOS_ESTRUTURAIS:
        return True
    # Imagem servida pela view do Plone: `/foto.png/view`.
    return bool(re.search(r"\.(png|jpe?g|gif|svg|webp)(/view)?/?$", link or "",
                          re.I))


def _sem_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _texto(elemento, *nomes) -> str:
    """O primeiro filho com um destes nomes, sem depender do namespace.

    O feed da Receita é RSS 1.0 (RDF), o de outros portais é RSS 2.0 ou Atom,
    e cada um põe o namespace no seu lugar. Comparar pelo nome local é o que
    faz o mesmo leitor servir aos três.
    """
    for filho in elemento:
        if _sem_ns(filho.tag) in nomes:
            if filho.text and filho.text.strip():
                return filho.text.strip()
            href = filho.attrib.get("href")      # Atom põe o link no atributo
            if href:
                return href.strip()
    return ""


def interpretar(bruto: bytes, fonte_id: str) -> list:
    """Feed → publicações. Aceita RSS 1.0, RSS 2.0 e Atom.

    Item sem link é DESCARTADO, não guardado sem link: notícia que não se
    pode conferir na fonte não é informação.
    """
    raiz = ET.fromstring(bruto)
    itens = [e for e in raiz.iter() if _sem_ns(e.tag) in ("item", "entry")]
    saida = []
    for it in itens:
        link = _texto(it, "link", "guid")
        titulo = _limpar(_texto(it, "title"))
        if not link or not titulo:
            continue
        if estrutural(_texto(it, "type"), link):
            continue
        resumo = _limpar(_texto(it, "description", "summary", "content"))[:600]
        quando = _texto(it, "pubDate", "date", "updated", "published", "created")
        saida.append(Publicacao(
            id=hashlib.sha256(link.encode("utf-8")).hexdigest()[:16],
            fonte=fonte_id,
            titulo=titulo[:300],
            link=link,
            resumo=resumo,
            publicado_em=quando[:40],
            visto_em=_agora(),
            temas=classificar(titulo, resumo),
        ))
    return saida


def _buscar(url: str, tempo_limite: int = TEMPO_LIMITE) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "FISCALE/1.0 (+leitor de feed oficial)"})
    with urllib.request.urlopen(
            req, timeout=tempo_limite,
            context=ssl.create_default_context()) as r:
        return r.read()


class Central:
    """O arquivo de publicações e o estado de cada fonte.

    Tudo em `<dados>/fiscal/atualizacoes/`. Nada aqui encosta em acervo,
    índice, checkpoint ou cadastro.
    """

    def __init__(self, dados_dir, buscador=None):
        self.raiz = Path(dados_dir) / "fiscal" / "atualizacoes"
        # `buscador` injetado é o que permite testar sem rede.
        self._buscar = buscador or _buscar

    # ── disco ───────────────────────────────────────────────────────────
    @property
    def arq_publicacoes(self) -> Path:
        return self.raiz / "publicacoes.json"

    @property
    def arq_estado(self) -> Path:
        return self.raiz / "estado.json"

    def _ler(self, arq: Path, padrao):
        try:
            if arq.exists():
                return json.loads(arq.read_text("utf-8-sig"))
        except Exception:
            pass          # arquivo corrompido não derruba a tela
        return padrao

    def _gravar(self, arq: Path, dado) -> None:
        self.raiz.mkdir(parents=True, exist_ok=True)
        tmp = arq.with_suffix(arq.suffix + ".tmp")
        tmp.write_text(json.dumps(dado, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(arq)

    def estado(self) -> dict:
        d = self._ler(self.arq_estado, {})
        d.setdefault("fontes", {})
        # `lidos` é uma lista de IDs, não uma data. Carimbo tem resolução de
        # segundo: publicação que chega no MESMO segundo em que se marcou tudo
        # como lido nasceria já lida, e ninguém a veria. Identidade não tem
        # esse problema.
        d.setdefault("lidos", [])
        d.setdefault("lido_ate", "")      # só informativo: quando foi a leitura
        d.setdefault("intervalo", INTERVALO_PADRAO)
        d.setdefault("ligado", True)
        d.setdefault("ultima_verificacao", "")
        return d

    def guardadas(self) -> list:
        return self._ler(self.arq_publicacoes, [])

    # ── coleta ──────────────────────────────────────────────────────────
    def verificar(self) -> dict:
        """Vai às fontes. Devolve o que aconteceu, fonte por fonte."""
        est = self.estado()
        antigas = self.guardadas()
        conhecidos = {p["id"] for p in antigas}
        novas, relatorio = [], []

        for f in FONTES:
            if not f.tem_feed:
                est["fontes"][f.id] = {
                    "estado": SEM_FEED, "quando": _agora(),
                    "detalhe": f.observacao, "novas": 0}
                relatorio.append({"fonte": f.id, "estado": SEM_FEED,
                                  "novas": 0, "detalhe": f.observacao})
                continue
            try:
                bruto = self._buscar(f.feed)
                itens = interpretar(bruto, f.id)
            except Exception as e:
                detalhe = "%s: %s" % (type(e).__name__, str(e)[:120])
                est["fontes"][f.id] = {
                    "estado": NAO_VERIFICADO, "quando": _agora(),
                    "detalhe": detalhe, "novas": 0}
                relatorio.append({"fonte": f.id, "estado": NAO_VERIFICADO,
                                  "novas": 0, "detalhe": detalhe})
                continue

            deste = [p for p in itens if p.id not in conhecidos]
            for p in deste:
                conhecidos.add(p.id)
            novas.extend(deste)
            est["fontes"][f.id] = {
                "estado": OK, "quando": _agora(),
                "detalhe": "%d publicações lidas" % len(itens),
                "novas": len(deste)}
            relatorio.append({"fonte": f.id, "estado": OK,
                              "novas": len(deste),
                              "detalhe": "%d lidas" % len(itens)})

        if novas:
            tudo = [p.dict() for p in novas] + antigas
            tudo.sort(key=lambda p: p.get("visto_em", ""), reverse=True)
            self._gravar(self.arq_publicacoes, tudo[:MAXIMO_GUARDADO])

        est["ultima_verificacao"] = _agora()
        self._gravar(self.arq_estado, est)
        return {
            "novas": len(novas),
            "fontes": relatorio,
            "verificado_em": est["ultima_verificacao"],
            # Quantas fontes NÃO foram verificadas. A tela mostra isso ao lado
            # do total: "12 novidades, 5 fontes não verificadas" é uma frase
            # honesta; "12 novidades" sozinha esconde metade do quadro.
            "nao_verificadas": sum(1 for r in relatorio
                                   if r["estado"] != OK),
        }

    # ── leitura ─────────────────────────────────────────────────────────
    def listar(self, tema: str = "", fonte: str = "", limite: int = 100) -> dict:
        itens = self.guardadas()
        if tema:
            itens = [p for p in itens if tema in (p.get("temas") or [])]
        if fonte:
            itens = [p for p in itens if p.get("fonte") == fonte]
        est = self.estado()
        lidos = set(est.get("lidos") or [])
        for p in itens:
            p["novo"] = p.get("id") not in lidos
        return {
            "publicacoes": itens[:limite],
            "total": len(itens),
            "nao_lidos": self.nao_lidos(),
            "estado": est,
            "fontes": [{**asdict(f),
                        "tem_feed": f.tem_feed,
                        **est["fontes"].get(f.id, {"estado": NAO_VERIFICADO,
                                                   "detalhe": "ainda não verificada",
                                                   "quando": ""})}
                       for f in FONTES],
            "temas": ROTULO_TEMA,
            "rotulos_estado": ROTULO_ESTADO,
        }

    def nao_lidos(self) -> int:
        lidos = set(self.estado().get("lidos") or [])
        return sum(1 for p in self.guardadas() if p.get("id") not in lidos)

    def marcar_lido(self) -> dict:
        est = self.estado()
        guardadas = self.guardadas()
        # Só os IDs que ainda estão no arquivo: publicação que já saiu pelo
        # teto não precisa continuar ocupando a lista de lidas.
        est["lidos"] = [p["id"] for p in guardadas if p.get("id")]
        est["lido_ate"] = _agora()
        self._gravar(self.arq_estado, est)
        return {"ok": True, "lido_ate": est["lido_ate"], "nao_lidos": 0}

    def configurar(self, intervalo=None, ligado=None) -> dict:
        est = self.estado()
        if intervalo is not None:
            # Menos de 15 minutos é bater na porta de portal do governo sem
            # motivo: eles publicam algumas vezes por dia, não por minuto.
            est["intervalo"] = max(900, min(int(intervalo), 86400))
        if ligado is not None:
            est["ligado"] = bool(ligado)
        self._gravar(self.arq_estado, est)
        return est

    def precisa_verificar(self) -> bool:
        """Já passou o intervalo desde a última verificação?"""
        est = self.estado()
        if not est.get("ligado"):
            return False
        ultima = est.get("ultima_verificacao") or ""
        if not ultima:
            return True
        try:
            quando = datetime.fromisoformat(ultima)
        except ValueError:
            return True
        return (datetime.now(timezone.utc).astimezone()
                - quando).total_seconds() >= est.get("intervalo",
                                                     INTERVALO_PADRAO)
