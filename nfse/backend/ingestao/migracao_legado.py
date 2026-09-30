"""
migracao_legado.py — traz a posição de NSU do `nfe.py` para o checkpoint da ING 2.

════════════════════════════════════════════════════════════════════════════
A PROVA, ANTES DA MIGRAÇÃO
════════════════════════════════════════════════════════════════════════════
Migrar um campo porque o nome parece certo é como se perde documento fiscal.
Existem DOIS arquivos chamados `estado.json` na pasta de uma empresa, e eles são
de serviços diferentes:

    <cnpj>/nfe/estado.json      {"ultNSU": …, "maxNSU": …}
        Escrito por `nfe.py:101`. Lido em `nfe.py:66` e injetado em
        `nfe.py:49` como `<distNSU><ultNSU>…</ultNSU></distNSU>` no envelope
        do **NFeDistribuicaoDFe**.
        → É a posição da Distribuição DF-e da NF-e. É ESTE que se migra.

    <cnpj>/estado.json          {"ultimoNSU": …, "atualizadoEm": …}
        Escrito por `core.py:200`. O `core.py` fala com
        `https://adn.nfse.gov.br` na rota `/contribuintes/DFe/{nsu}`.
        → É o **ADN da NFS-e Nacional**. Outro serviço, outra sequência de NSU.
        MIGRAR ISTO PARA O CHECKPOINT DA NF-e SERIA O ERRO.

O nome `ultimoNSU` pertence ao arquivo da NFS-e; o da NF-e chama `ultNSU`.
São parecidos e são coisas diferentes — por isso a leitura aqui é pela CHAVE
CERTA, no CAMINHO CERTO, e qualquer outra coisa é recusada.

════════════════════════════════════════════════════════════════════════════
AMBIENTE: o legado é PRODUÇÃO, e só
════════════════════════════════════════════════════════════════════════════
`nfe.py:48` emite `<tpAmb>1</tpAmb>` fixo no código — não havia opção de
homologação. Logo, o `ultNSU` legado é uma posição de PRODUÇÃO.

Migrá-lo para o checkpoint de homologação criaria uma posição falsa num
ambiente onde ela nunca existiu, e a varredura pularia documentos de teste sem
nunca os ter visto. **Esta função recusa qualquer ambiente que não seja
produção.**

════════════════════════════════════════════════════════════════════════════
POSIÇÃO DA DISTRIBUIÇÃO ≠ DOCUMENTOS NO ACERVO NOVO
════════════════════════════════════════════════════════════════════════════
Migrar o NSU afirma "a distribuição foi consumida até aqui". **Não** afirma que
os documentos estão no acervo da ING 3A — eles estão em `<cnpj>/nfe/*.xml`, no
formato do código antigo.

Por isso a migração grava `cobertura_anterior = EM_ACERVO_LEGADO` e registra
quantos XML existem no acervo legado e quantos no novo. A verdade sobre
cobertura é preservada em vez de ser presumida.

════════════════════════════════════════════════════════════════════════════
O QUE ESTA MIGRAÇÃO NUNCA FAZ
════════════════════════════════════════════════════════════════════════════
    • não vai à rede;
    • não reduz um checkpoint existente;
    • não sobrescreve um checkpoint da ING 2 que já esteja à frente;
    • não apaga nem move o arquivo legado;
    • não cria checkpoint a partir de arquivo corrompido ou NSU inválido;
    • não mistura produção com homologação;
    • rodar duas vezes tem o mesmo efeito de rodar uma.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import checkpoint as cpm
from .ambiente import PRODUCAO, Ambiente, resolver as resolver_ambiente
from .identidade import normalizar

ARQUIVO_LEGADO = "estado.json"
PASTA_LEGADA = "nfe"
CHAVE_NSU = "ultNSU"          # a da NF-e. `ultimoNSU` é da NFS-e — ver cabeçalho
CHAVE_MAX = "maxNSU"

# ── desfechos ───────────────────────────────────────────────────────────────
MIGRADO = "MIGRADO"                     # o checkpoint avançou
JA_MIGRADO = "JA_MIGRADO"               # idempotência: nada a fazer
NOVO_MAIOR = "NOVO_JA_ESTA_A_FRENTE"    # o checkpoint novo é maior; preservado
SEM_LEGADO = "SEM_ARQUIVO_LEGADO"       # não é erro
LEGADO_ILEGIVEL = "LEGADO_ILEGIVEL"     # JSON quebrado — não produz checkpoint
NSU_INVALIDO = "NSU_INVALIDO"           # campo ausente/negativo/não numérico
AMBIENTE_RECUSADO = "AMBIENTE_RECUSADO"  # legado é produção; pediram outro
IDENTIDADE_INVALIDA = "IDENTIDADE_INVALIDA"

SEM_EFEITO = frozenset({JA_MIGRADO, NOVO_MAIOR, SEM_LEGADO, LEGADO_ILEGIVEL,
                        NSU_INVALIDO, AMBIENTE_RECUSADO, IDENTIDADE_INVALIDA})


@dataclass
class Resultado:
    """O que a migração fez com UMA empresa. Nunca levanta; sempre explica."""
    identidade_mascarada: str = ""
    resultado: str = ""
    detalhe: str = ""
    nsu_legado: str = ""
    nsu_antes: str = ""
    nsu_depois: str = ""
    arquivo_legado: str = ""
    xml_no_acervo_legado: int = 0
    documentos_no_acervo_novo: int = 0

    @property
    def alterou(self) -> bool:
        return self.resultado == MIGRADO

    def resumo(self) -> dict:
        return {"empresa": self.identidade_mascarada, "resultado": self.resultado,
                "nsu_legado": self.nsu_legado or None,
                "nsu": f"{self.nsu_antes} -> {self.nsu_depois}",
                "xml_legado": self.xml_no_acervo_legado,
                "documentos_acervo_novo": self.documentos_no_acervo_novo,
                "detalhe": self.detalhe or None}

    def linha(self) -> str:
        return (f"[{self.resultado}] {self.identidade_mascarada} "
                f"NSU {self.nsu_antes} -> {self.nsu_depois}"
                f"{'  · ' + self.detalhe if self.detalhe else ''}")


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def caminho_legado(dados_dir, identidade: str) -> Path:
    return Path(dados_dir) / identidade / PASTA_LEGADA / ARQUIVO_LEGADO


def _ler_legado(p: Path) -> tuple[dict | None, str]:
    """`(conteúdo, motivo_da_recusa)`. Arquivo ausente devolve `(None, "")`."""
    if not p.exists():
        return None, ""
    try:
        bruto = p.read_text("utf-8-sig")
    except OSError as exc:
        return None, f"não foi possível ler ({type(exc).__name__})"
    try:
        d = json.loads(bruto)
    except (ValueError, TypeError) as exc:
        return None, f"JSON inválido ({type(exc).__name__})"
    if not isinstance(d, dict):
        return None, f"conteúdo não é um objeto JSON ({type(d).__name__})"
    return d, ""


def _nsu_valido(valor) -> tuple[str, str]:
    """`(nsu_canônico, motivo_da_recusa)`.

    Recusa o que `normalizar_nsu` aceitaria por engano: texto sem dígito vira
    zero lá, e zero não é uma posição migrável — é a ausência de posição."""
    if valor is None:
        return "", f"campo '{CHAVE_NSU}' ausente"
    if isinstance(valor, bool):
        return "", f"campo '{CHAVE_NSU}' é booleano"
    if isinstance(valor, float):
        return "", f"campo '{CHAVE_NSU}' é fracionário ({valor!r})"
    if isinstance(valor, int):
        if valor < 0:
            return "", f"campo '{CHAVE_NSU}' negativo ({valor})"
        n = valor
    elif isinstance(valor, str):
        t = valor.strip()
        if not t or not t.isdigit():
            return "", f"campo '{CHAVE_NSU}' não é numérico ({t[:20]!r})"
        n = int(t)
    else:
        return "", f"campo '{CHAVE_NSU}' de tipo inesperado ({type(valor).__name__})"
    if n == 0:
        return "", f"campo '{CHAVE_NSU}' é zero — não há posição a migrar"
    if n > 999_999_999_999_999:
        return "", f"campo '{CHAVE_NSU}' acima do máximo de 15 dígitos"
    return cpm.normalizar_nsu(n), ""


def _contar(pasta: Path, padrao: str) -> int:
    return len(list(pasta.glob(padrao))) if pasta.exists() else 0


def migrar_nfe(dados_dir, identidade, ambiente: Ambiente | str = PRODUCAO,
               repo: cpm.RepositorioCheckpoint | None = None,
               agora: str = "") -> Resultado:
    """Migra a posição da Distribuição DF-e da NF-e. **Idempotente.**

    Nunca levanta: devolve `Resultado` com o desfecho, para que uma empresa
    problemática não interrompa a migração das outras.
    """
    dados = Path(dados_dir)
    ident = normalizar(identidade)
    res = Resultado(identidade_mascarada=ident.mascarado() if ident.valido
                    else "<inválida>")
    if not ident.valido:
        res.resultado, res.detalhe = IDENTIDADE_INVALIDA, ident.motivo
        return res

    amb = resolver_ambiente(ambiente)
    if amb.nome != PRODUCAO.nome:
        # `nfe.py:48` mandava `<tpAmb>1</tpAmb>` fixo. Não existe posição legada
        # de homologação, e inventar uma faria a varredura pular documentos.
        res.resultado = AMBIENTE_RECUSADO
        res.detalhe = (f"o estado legado é de PRODUÇÃO (tpAmb 1 fixo no nfe.py); "
                       f"não há posição legada para '{amb.nome}'")
        return res

    p = caminho_legado(dados, ident.valor)
    res.arquivo_legado = str(p)
    legado, motivo = _ler_legado(p)
    if legado is None:
        if motivo:
            res.resultado, res.detalhe = LEGADO_ILEGIVEL, motivo
        else:
            res.resultado = SEM_LEGADO
            res.detalhe = "nenhum estado legado para esta empresa"
        return res

    nsu_legado, motivo = _nsu_valido(legado.get(CHAVE_NSU))
    if not nsu_legado:
        res.resultado, res.detalhe = NSU_INVALIDO, motivo
        return res
    res.nsu_legado = nsu_legado

    repo = repo or cpm.RepositorioCheckpoint(dados)
    try:
        cp = repo.carregar(ident.valor, cpm.NFE_DISTRIBUICAO, amb)
    except cpm.CheckpointCorrompido as exc:
        # Checkpoint ilegível é evidência preservada (ING 2). Não se sobrepõe.
        res.resultado, res.detalhe = LEGADO_ILEGIVEL, f"checkpoint atual: {exc}"
        return res

    res.nsu_antes = res.nsu_depois = cp.ult_nsu
    res.xml_no_acervo_legado = _contar(dados / ident.valor / PASTA_LEGADA, "*.xml")
    res.documentos_no_acervo_novo = _contar(
        dados / ident.valor / "acervo", "*/*/*/original.xml")

    # ── idempotência e proteção contra retrocesso ─────────────────────────
    if cpm.nsu_int(cp.ult_nsu) > cpm.nsu_int(nsu_legado):
        res.resultado = NOVO_MAIOR
        res.detalhe = (f"o checkpoint atual ({cp.ult_nsu}) está à frente do legado "
                       f"({nsu_legado}); preservado")
        return res
    if cpm.nsu_int(cp.ult_nsu) == cpm.nsu_int(nsu_legado):
        res.resultado = JA_MIGRADO
        res.detalhe = "o checkpoint já está na posição do legado"
        return res

    # ── migra ─────────────────────────────────────────────────────────────
    quando = agora or _agora()
    cp.ult_nsu = nsu_legado
    max_legado, _ = _nsu_valido(legado.get(CHAVE_MAX))
    if max_legado and cpm.nsu_int(max_legado) >= cpm.nsu_int(nsu_legado):
        cp.max_nsu = max_legado
    cp.origem_ult_nsu = cpm.ORIGEM_LEGADO
    # A distribuição foi consumida até aqui, mas os documentos estão no acervo
    # ANTIGO. Dizer o contrário seria prometer o que o acervo novo não tem.
    cp.cobertura_anterior = cpm.COBERTURA_ACERVO_LEGADO
    cp.migracao_legado = {
        "origem": "nfe.py",
        "arquivo": str(p),
        "chave_lida": CHAVE_NSU,
        "nsu_legado": nsu_legado,
        "max_nsu_legado": max_legado or None,
        "nsu_anterior_no_checkpoint": res.nsu_antes,
        "ambiente": amb.nome,
        "migrado_em": quando,
        "xml_no_acervo_legado": res.xml_no_acervo_legado,
        "documentos_no_acervo_ing3a": res.documentos_no_acervo_novo,
        "aviso": ("posição da distribuição migrada do código antigo; os "
                  "documentos desse período estão em <cnpj>/nfe/*.xml e NÃO "
                  "no acervo da ING 3A"),
    }
    if cp.status == cpm.NUNCA_EXECUTADO:
        cp.status = cpm.OK
    obs = f"NSU {nsu_legado} migrado do nfe.py em {quando}"
    if obs not in cp.observacoes:
        cp.observacoes.append(obs)

    repo.salvar(cp)
    res.resultado = MIGRADO
    res.nsu_depois = cp.ult_nsu
    res.detalhe = (f"{res.xml_no_acervo_legado} XML no acervo legado, "
                   f"{res.documentos_no_acervo_novo} no acervo novo")
    return res


def migrar_todas(dados_dir, identidades=None,
                 ambiente: Ambiente | str = PRODUCAO) -> list[Resultado]:
    """Migra várias empresas. Uma com problema não interrompe as outras."""
    dados = Path(dados_dir)
    if identidades is None:
        identidades = sorted(p.name for p in dados.iterdir()
                             if p.is_dir() and normalizar(p.name).valido)
    repo = cpm.RepositorioCheckpoint(dados)
    return [migrar_nfe(dados, i, ambiente, repo=repo) for i in identidades]


def prever(dados_dir, identidade, ambiente: Ambiente | str = PRODUCAO) -> dict:
    """O que a migração FARIA, sem gravar nada. Para conferência antes de agir."""
    dados = Path(dados_dir)
    ident = normalizar(identidade)
    if not ident.valido:
        return {"resultado": IDENTIDADE_INVALIDA, "detalhe": ident.motivo}
    amb = resolver_ambiente(ambiente)
    p = caminho_legado(dados, ident.valor)
    legado, motivo = _ler_legado(p)
    cp = cpm.RepositorioCheckpoint(dados).carregar(ident.valor,
                                                  cpm.NFE_DISTRIBUICAO, amb)
    nsu_legado = ""
    if legado is not None:
        nsu_legado, _ = _nsu_valido(legado.get(CHAVE_NSU))
    return {
        "empresa": ident.mascarado(),
        "ambiente": amb.nome,
        "arquivo_legado": str(p),
        "legado_existe": legado is not None,
        "motivo_recusa": motivo or None,
        "nsu_legado": nsu_legado or None,
        "checkpoint_atual": cp.ult_nsu,
        "origem_atual": cp.origem_ult_nsu or None,
        "avancaria_para": (nsu_legado if nsu_legado and
                           cpm.nsu_int(nsu_legado) > cpm.nsu_int(cp.ult_nsu)
                           else None),
        "xml_no_acervo_legado": _contar(dados / ident.valor / PASTA_LEGADA, "*.xml"),
        "documentos_no_acervo_ing3a": _contar(dados / ident.valor / "acervo",
                                              "*/*/*/original.xml"),
    }
