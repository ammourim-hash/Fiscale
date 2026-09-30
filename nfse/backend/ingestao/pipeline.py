"""
pipeline.py — amarra aquisição → acervo → índice, sem misturar as etapas.

A CADEIA
    aquisição → DocumentoBruto → acervo imutável → identificação
              → indexação → parser → normalização

    Cada seta é uma fronteira real: a etapa seguinte pode falhar inteira sem
    desfazer a anterior. É isso que permite reprocessar sem rebaixar.

A SEPARAÇÃO QUE IMPORTA
    `preservar` e `interpretar` são chamadas DIFERENTES, e nesta ordem. O
    `DistribuicaoRunner` só conhece a primeira: para ele, o trabalho termina
    quando o byte está em disco e confirmado. A leitura vem depois, fora do
    laço de paginação, e **falhar nela não move o checkpoint um dígito**.

    Se as duas estivessem juntas, um parser com defeito faria o NSU parar de
    avançar — e o CT-e, que retém só 3 meses, perderia documento por causa de
    um bug nosso.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import acervo as acv
from . import auditoria as aud
from . import checkpoint as cpm
from . import indice as idx
from .ambiente import Ambiente, resolver as resolver_ambiente
from .distribuicao import DistribuicaoRunner, Fonte, ResultadoExecucao
from .identidade import normalizar


@dataclass
class ResultadoIngestao:
    """O que a ingestão completa de uma empresa produziu."""
    execucao: ResultadoExecucao | None = None
    indexacao: idx.RelatorioIndexacao = field(default_factory=idx.RelatorioIndexacao)
    identidade_mascarada: str = ""
    consultas_registradas: int = 0
    sincronismo: dict | None = None          # local × observado, quando houve

    @property
    def sucesso(self) -> bool:
        return bool(self.execucao and self.execucao.sucesso)

    @property
    def divergencia_externa(self) -> bool:
        return bool(self.sincronismo
                    and self.sincronismo.get("estado_sincronismo")
                    == cpm.SINCRONISMO_DIVERGENCIA)

    def resumo(self) -> dict:
        return {
            "empresa": self.identidade_mascarada,
            "aquisicao": self.execucao.para_log() if self.execucao else None,
            "indexacao": self.indexacao.resumo(),
            "consultas_registradas": self.consultas_registradas,
            "sincronismo": self.sincronismo,
        }

    def linha(self) -> str:
        base = self.execucao.linha() if self.execucao else "(sem aquisição)"
        r = self.indexacao.resumo()
        return (f"{base}\n    índice: {r['indexados']} ok · "
                f"{r['schema_desconhecido']} schema desconhecido · "
                f"{r['falha_parser']} falha de parser · "
                f"{r['quarentena']} em quarentena")


def montar(dados_dir, identidade, servico: str, ambiente="") -> tuple:
    """`(acervo, índice, trilha, repositório de checkpoint)` de uma empresa."""
    ident = normalizar(identidade)
    if not ident.valido:
        raise ValueError(f"identidade fiscal inválida: {ident.motivo}")
    dados = Path(dados_dir)
    trilha = aud.abrir(dados, ident.valor)
    ac = acv.AcervoArquivos(dados_dir=dados, identidade=ident.valor,
                            servico=servico, ambiente=ambiente, trilha=trilha)
    ind = idx.abrir(dados, ident.valor)
    repo = cpm.RepositorioCheckpoint(dados)
    return ac, ind, trilha, repo


def indexar_pendentes(dados_dir, identidade) -> idx.RelatorioIndexacao:
    """Passa pelo acervo e indexa o que ainda não está no índice.

    Chamado depois da aquisição — e também sozinho, quando alguém quer só
    reconciliar índice e acervo sem baixar nada."""
    # A varredura mora em `indice.indexar_ausentes`, onde tem nome e teste. Ter
    # duas cópias dela foi como o índice ficou para trás sem ninguém notar.
    return idx.indexar_ausentes(dados_dir, identidade,
                                trilha=aud.abrir(dados_dir, identidade))


def registrar_tentativas(trilha: aud.Trilha, fonte) -> int:
    """Passa as tentativas que a Fonte acumulou para a trilha de auditoria.

    A Fonte produz DADO; a trilha PERSISTE. Essa divisão é o que permite ao
    conector registrar tudo sem importar o módulo de auditoria — e é conferida
    por teste na ING 3B."""
    tentativas = list(getattr(fonte, "tentativas", []) or [])
    for t in tentativas:
        trilha.registrar_consulta(t)
    return len(tentativas)


def aplicar_sincronismo(repo: cpm.RepositorioCheckpoint, identidade: str,
                        servico: str, ambiente, fonte) -> dict | None:
    """Atualiza o ESTADO DE SINCRONISMO a partir das tentativas — nunca o acervo.

    Dois caminhos, e eles são excludentes:

    • **negativo** — a SEFAZ informou uma posição à frente numa rejeição:
      registra `DIVERGENCIA_EXTERNA` e a hipótese de consumidor externo.
    • **positivo** — uma resposta atual e limpa fechou `ultNSU == maxNSU` na
      nossa posição: registra `EM_SINCRONIA`.

    O positivo existe porque "o checkpoint não mudou" estava virando "estado
    desconhecido". Depois de um `137` que confirma que não há mais nada, o
    estado ficava `SEM_INFORMACAO` — como se ninguém tivesse perguntado.

    A regra que este trecho existe para impor: `ult_nsu` só anda com documento
    preservado. O `ultNSU` de uma rejeição atualiza apenas o estado OBSERVADO.
    Adotá-lo como posição do acervo faria o FISCALE afirmar que possui
    documentos que nunca viu, e pulá-los para sempre."""
    tentativas = list(getattr(fonte, "tentativas", []) or [])
    if not tentativas:
        return None
    divergentes = [t for t in tentativas if t.get("divergencia_externa")]

    cp = repo.carregar(identidade, servico, ambiente)
    antes = cp.ult_nsu

    # ── caminho POSITIVO ──────────────────────────────────────────────────
    # Só a ÚLTIMA tentativa vale: é ela que descreve o estado atual. E só
    # quando não houve erro nenhum — resposta de rejeição não prova sincronia.
    if not divergentes:
        u = tentativas[-1]
        limpa = (u.get("transporte_ok")
                 and not u.get("erro_tecnico")
                 and u.get("resultado") in ("DOCUMENTOS_ENCONTRADOS",
                                            "NENHUM_DOCUMENTO"))
        if limpa and cp.registrar_sincronia_confirmada(
                u.get("ult_nsu") or "", u.get("max_nsu") or ""):
            repo.salvar(cp)
            if cp.ult_nsu != antes:
                raise RuntimeError("sincronia confirmada alterou ult_nsu")
            return cp.resumo()
        return None

    # ── caminho NEGATIVO ──────────────────────────────────────────────────
    ultima = divergentes[-1]
    a_frente = cp.registrar_observacao_sefaz(
        ultima.get("nsu_observado_sefaz") or "", cstat=ultima.get("cstat") or "")

    if a_frente:
        # As quatro condições do diagnóstico: houve consulta, veio rejeição, a
        # mensagem manda usar outro ultNSU, e o valor devolvido está à frente do
        # que temos comprovado. Nenhuma delas nomeia um culpado — e o texto do
        # alerta também não.
        cp.marcar_possivel_consumidor_externo(
            f"cStat {ultima.get('cstat')} devolveu ultNSU "
            f"{ultima.get('nsu_observado_sefaz')} com acervo local em {antes}")
    repo.salvar(cp)

    if cp.ult_nsu != antes:                     # cinto e suspensório
        raise RuntimeError("o checkpoint do acervo foi alterado por uma "
                           "observação externa — isto nunca pode acontecer")
    return cp.resumo()


# Nome anterior, mantido: o caminho negativo continua sendo o principal uso.
aplicar_divergencia = aplicar_sincronismo


def ingerir(dados_dir, identidade, fonte: Fonte, ambiente="",
            limite_lotes: int = 0, usar_trava: bool = True) -> ResultadoIngestao:
    """Adquire, preserva e indexa — nesta ordem, com as fronteiras respeitadas.

    A indexação roda **mesmo quando a aquisição falha**: documentos preservados
    em execuções anteriores e ainda não lidos continuam pendentes, e não há
    motivo para deixá-los esperando o serviço voltar."""
    ident = normalizar(identidade)
    amb = resolver_ambiente(ambiente or getattr(fonte, "ambiente", ""))
    res = ResultadoIngestao(
        identidade_mascarada=ident.mascarado() if ident.valido else "<inválida>")
    if not ident.valido:
        return res

    servico = getattr(fonte, "servico", "")
    ac, _ind, trilha, repo = montar(dados_dir, ident.valor, servico, amb)
    runner = (DistribuicaoRunner(repo, limite_lotes=limite_lotes) if limite_lotes
              else DistribuicaoRunner(repo))

    res.execucao = runner.executar(ident.valor, fonte, ac, usar_trava=usar_trava)

    # A trilha da TENTATIVA vem antes da indexação e acontece sempre — mesmo
    # quando a aquisição falhou e não há um único documento. Foi a ausência
    # disto que deixou a rejeição 656 de 13/08/2026 sem registro na auditoria.
    res.consultas_registradas = registrar_tentativas(trilha, fonte)
    if servico:
        res.sincronismo = aplicar_sincronismo(repo, ident.valor, servico, amb, fonte)

    res.indexacao = indexar_pendentes(dados_dir, ident.valor)
    return res
