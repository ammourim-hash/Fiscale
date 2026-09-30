"""
migracao_eventos.py — reidentificar `EVENTO_NFE` com `cOrgao` na identidade.

O QUE MUDOU, E POR QUE PRECISA DE MIGRAÇÃO
    A identidade do evento passou de
    `sha256("EVENTO_NFE|<chave>|<tpEvento>|<nSeq>")` para
    `sha256("EVENTO_NFE|<chave>|<tpEvento>|<nSeq>|<cOrgao>")`.

    Fórmula de identidade não é configuração: ela decide o NOME DA PASTA onde o
    documento mora. Deixar a nova valer só para documentos novos criaria duas
    convenções no mesmo acervo — e o evento antigo que hoje está preso como
    `COLISAO` continuaria preso.

O QUE ESTA MIGRAÇÃO **NUNCA** FAZ
    Não altera um único byte de `original.xml`. Não apaga cópia preservada. Não
    toca em checkpoint. Não fala com a SEFAZ. Não junta documentos: ela só
    **separa** o que estava indevidamente junto, e renomeia o que continua
    sozinho.

    Separar demais é reversível — sobra uma pasta a mais, com os bytes certos.
    Juntar errado apaga um fato fiscal. A migração só anda na direção segura.

A SEPARAÇÃO, EM CONCRETO
    Um grupo antigo com `original.xml` (órgão A) e `copias/x.xml` (órgão B) vira
    dois documentos lógicos:

        <id_novo_A>/original.xml    os MESMOS bytes de antes
        <id_novo_B>/original.xml    os bytes que estavam na cópia

    A cópia original **continua onde está**, e o grupo A registra
    `separado_em` apontando para B. Nada é removido; o que havia continua
    havendo, e passa a haver mais.

IDEMPOTÊNCIA
    Rodar duas vezes não muda nada na segunda: um grupo cujo nome de pasta já é
    o identificador novo é reconhecido como `JA_MIGRADO`.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from . import acervo as acv
from . import auditoria as aud
from . import identificacao as idf
from . import indice as idx
from .identidade import normalizar

# ── desfechos por grupo ─────────────────────────────────────────────────────
JA_MIGRADO = "JA_MIGRADO"                 # a pasta já tem o nome novo
RENOMEAR = "RENOMEAR"                     # um evento só, id mudou
SEPARAR = "SEPARAR"                       # colisão falsa: vira N documentos
INALTERADO = "INALTERADO"                 # id novo == id antigo (sem cOrgao)
REVISAR = "REVISAR"                       # ambíguo: a migração não decide

ATO = aud.MIGRACAO_IDENTIDADE


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Copia:
    """Um arquivo XML dentro de um grupo, já reidentificado."""
    arquivo: Path
    e_original: bool
    id_novo: str
    orgao: str
    hash_conteudo: str
    prioridade: int
    raiz: str
    # A cópia já virou documento próprio numa migração anterior. Ela CONTINUA
    # no lugar — não apagamos cópia preservada —, mas não conta mais para
    # decidir o que fazer com o grupo. Sem isto o plano nunca estabilizaria:
    # toda releitura veria a mesma cópia e proporia separar de novo.
    ja_separada: bool = False


@dataclass
class Grupo:
    """Um `id_documento` antigo, com tudo o que mora nele."""
    id_antigo: str
    pasta: Path
    copias: list = field(default_factory=list)
    desfecho: str = ""
    motivo: str = ""

    @property
    def ids_novos(self) -> list:
        """Identidades que este grupo ainda precisa resolver, sem repetir."""
        vistos, saida = set(), []
        for c in self.copias:
            if c.ja_separada or c.id_novo in vistos:
                continue
            vistos.add(c.id_novo)
            saida.append(c.id_novo)
        return saida


@dataclass
class Plano:
    """O que a migração FARIA. É o dry-run, e é o mesmo objeto que ela executa."""
    identidade: str = ""
    grupos: list = field(default_factory=list)
    erros: list = field(default_factory=list)

    def por_desfecho(self, d: str) -> list:
        return [g for g in self.grupos if g.desfecho == d]

    def resumo(self) -> dict:
        arquivos = sum(len(g.copias) for g in self.grupos)
        novos = sum(max(0, len(g.ids_novos) - 1) for g in self.por_desfecho(SEPARAR))
        return {
            "empresa": normalizar(self.identidade).mascarado(),
            "eventos_no_acervo": len(self.grupos),
            "arquivos_xml": arquivos,
            "ids_alterados": len(self.por_desfecho(RENOMEAR)) + len(self.por_desfecho(SEPARAR)),
            "colisoes_desfeitas": len(self.por_desfecho(SEPARAR)),
            "documentos_novos": novos,
            "inalterados": len(self.por_desfecho(INALTERADO)),
            "ja_migrados": len(self.por_desfecho(JA_MIGRADO)),
            "revisar": len(self.por_desfecho(REVISAR)),
            "erros": len(self.erros),
        }


def _identificar_arquivo(p: Path) -> tuple:
    """`(Identificacao, erro)` — nunca levanta."""
    try:
        dados = p.read_bytes()
    except OSError as exc:
        return None, f"não consegui ler {p.name}: {type(exc).__name__}"
    ident = idf.identificar(dados)
    if ident.especie != idf.EVENTO_NFE:
        return None, f"{p.name} não é EVENTO_NFE (raiz {ident.raiz or '?'})"
    return ident, ""


def planejar(dados_dir, identidade) -> Plano:
    """Lê o acervo e monta o plano. **Somente leitura.**"""
    dados = Path(dados_dir)
    ident_emp = normalizar(identidade)
    plano = Plano(identidade=ident_emp.valor if ident_emp.valido else str(identidade))
    base = dados / plano.identidade / acv.PASTA / idf.EVENTO_NFE
    if not base.is_dir():
        return plano

    for pasta in sorted(p for p in base.rglob("*") if p.is_dir()):
        original = pasta / acv.ORIGINAL
        if not original.exists():
            continue                       # não é pasta de documento
        g = Grupo(id_antigo=pasta.name, pasta=pasta)

        arquivos = [(original, True)]
        arquivos += [(x, False) for x in
                     sorted((pasta / acv.COPIAS).glob("*.xml"))
                     if (pasta / acv.COPIAS).is_dir()]

        for arq, e_orig in arquivos:
            ident, erro = _identificar_arquivo(arq)
            if erro:
                plano.erros.append(f"{pasta.name}: {erro}")
                g.desfecho, g.motivo = REVISAR, erro
                continue
            id_novo = ident.id_documento
            # Já existe documento próprio com esta identidade, e não é este
            # grupo? Então a separação já aconteceu numa passada anterior.
            ja = (not e_orig
                  and id_novo != pasta.name
                  and (base / id_novo[:2] / id_novo / acv.ORIGINAL).exists())
            g.copias.append(Copia(
                arquivo=arq, e_original=e_orig, id_novo=id_novo,
                orgao=ident.orgao, hash_conteudo=ident.hash_conteudo,
                prioridade=ident.prioridade, raiz=ident.raiz,
                ja_separada=bool(ja)))

        if g.desfecho == REVISAR:
            plano.grupos.append(g)
            continue
        pendentes = [c for c in g.copias if not c.ja_separada]
        if not g.copias:
            g.desfecho, g.motivo = REVISAR, "grupo sem arquivo identificável"
        elif any(not c.orgao for c in pendentes) and len(g.ids_novos) > 1:
            # Parte com órgão, parte sem: não dá para afirmar se são o mesmo
            # evento. A migração NÃO decide isso.
            g.desfecho, g.motivo = REVISAR, "evento sem cOrgao junto de evento com cOrgao"
        elif len(g.ids_novos) > 1:
            g.desfecho = SEPARAR
            g.motivo = (f"{len(g.ids_novos)} órgãos distintos no mesmo id antigo: "
                        f"{', '.join(sorted({c.orgao for c in pendentes}))}")
        elif g.ids_novos[0] == g.id_antigo:
            g.desfecho = JA_MIGRADO if pendentes[0].orgao else INALTERADO
            g.motivo = ("pasta já nomeada pela identidade nova" if pendentes[0].orgao
                        else "sem cOrgao: identidade nova é igual à antiga")
        else:
            g.desfecho = RENOMEAR
            g.motivo = f"cOrgao {pendentes[0].orgao} entra na identidade"
        plano.grupos.append(g)

    return plano


def _gravar_captura(pasta: Path, cap: dict) -> None:
    (pasta / acv.CAPTURA).write_text(
        json.dumps(cap, ensure_ascii=False, indent=1), encoding="utf-8")


def _captura_de(pasta: Path) -> dict:
    p = pasta / acv.CAPTURA
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text("utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}


def aplicar(dados_dir, identidade, plano: Plano | None = None,
            reindexar: bool = True) -> dict:
    """Executa o plano. Nunca sobrescreve `original.xml`, nunca apaga cópia."""
    dados = Path(dados_dir)
    ident_emp = normalizar(identidade)
    plano = plano or planejar(dados, identidade)
    base = dados / plano.identidade / acv.PASTA / idf.EVENTO_NFE
    trilha = aud.abrir(dados, plano.identidade)
    feitos = {"renomeados": 0, "separados": 0, "documentos_criados": 0,
              "ignorados": 0, "erros": []}

    for g in plano.grupos:
        if g.desfecho in (JA_MIGRADO, INALTERADO, REVISAR):
            feitos["ignorados"] += 1
            continue

        cap = _captura_de(g.pasta)
        principal = next(c for c in g.copias if c.e_original)

        # ── 1. os órgãos que NÃO são o do original viram documentos próprios ──
        criados = []
        for c in g.copias:
            if c.id_novo == principal.id_novo:
                continue
            destino = base / c.id_novo[:2] / c.id_novo
            if (destino / acv.ORIGINAL).exists():
                continue                    # idempotência: já separado antes
            destino.mkdir(parents=True, exist_ok=True)
            # `copy2`, não `move`: a cópia preservada continua onde estava.
            shutil.copy2(c.arquivo, destino / acv.ORIGINAL)
            novo_cap = dict(cap)
            novo_cap.update({
                "id_documento": c.id_novo, "especie": idf.EVENTO_NFE,
                "hash_conteudo": c.hash_conteudo, "prioridade": c.prioridade,
                "canonico": acv.ORIGINAL, "raiz": c.raiz, "quarentena": False,
                "orgao": c.orgao, "tamanho_bytes": c.arquivo.stat().st_size,
                "copias": [{"arquivo": acv.ORIGINAL, "hash": c.hash_conteudo,
                            "prioridade": c.prioridade,
                            "capturado_em": _agora()}],
                "separado_de": g.id_antigo,
                "migrado_em": _agora(),
                "motivo_migracao": g.motivo,
            })
            novo_cap.pop("motivo_quarentena", None)
            _gravar_captura(destino, novo_cap)
            criados.append(c.id_novo)
            trilha.registrar(ATO, id_documento=c.id_novo,
                             id_anterior=g.id_antigo, orgao=c.orgao,
                             hash=c.hash_conteudo, resultado="DOCUMENTO_SEPARADO",
                             motivo=g.motivo)

        # ── 2. o grupo original é renomeado para a identidade nova ───────────
        alvo = base / principal.id_novo[:2] / principal.id_novo
        if alvo != g.pasta:
            if alvo.exists():
                feitos["erros"].append(
                    f"{g.id_antigo}: destino {principal.id_novo[:12]} já existe")
                continue
            alvo.parent.mkdir(parents=True, exist_ok=True)
            g.pasta.rename(alvo)
            feitos["renomeados"] += 1

        cap = _captura_de(alvo)
        cap["id_documento"] = principal.id_novo
        cap["orgao"] = principal.orgao
        cap["id_anterior"] = g.id_antigo
        cap["migrado_em"] = _agora()
        if criados:
            # A quarentena era consequência da falsa colisão. Ela some porque o
            # motivo sumiu — e o rastro fica escrito em `separado_em`.
            cap["quarentena"] = False
            cap.pop("motivo_quarentena", None)
            cap["separado_em"] = criados
            feitos["separados"] += 1
            feitos["documentos_criados"] += len(criados)
        _gravar_captura(alvo, cap)
        trilha.registrar(ATO, id_documento=principal.id_novo,
                         id_anterior=g.id_antigo, orgao=principal.orgao,
                         resultado="IDENTIDADE_ATUALIZADA", motivo=g.motivo)

    if reindexar:
        rel = idx.reconstruir(dados, plano.identidade)
        feitos["indice"] = rel.resumo()
    return feitos
