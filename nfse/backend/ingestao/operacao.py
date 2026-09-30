"""
operacao.py — o estado OPERACIONAL de `(empresa, serviço, ambiente)`.

POR QUE É UM ARQUIVO SEPARADO DO CHECKPOINT
    O checkpoint responde *onde a leitura parou* — é verdade fiscal, e é o que
    não pode retroceder nunca. Este arquivo responde *quando posso perguntar de
    novo, e por quê não agora* — é agenda.

    Misturar os dois faria uma decisão de cadência mexer no mesmo arquivo que
    guarda a posição dos documentos. Separados, o pior que um erro de política
    pode causar é consultar cedo demais; jamais perder NSU.

POR QUE PERSISTIDO, E NÃO `sleep`
    `time.sleep(0.6)` entre lotes — que era o mecanismo do código antigo — não é
    proteção: não sabe quantas consultas houve na última hora, não sobrevive a
    reinício do processo e não distingue empresa nenhuma. Se o FISCALE cair e
    voltar, o `sleep` não lembra de nada.

    Um carimbo em disco lembra. `proxima_consulta_permitida_em` sobrevive a
    queda de energia, e o controlador o respeita ao reiniciar.

ONDE FICA
    `<raiz>/<identidade>/ingestao/<servico>.<ambiente>.operacao.json`

    Ao lado do checkpoint, dentro da pasta da empresa — o mesmo isolamento por
    estrutura: não existe caminho que escreva a agenda de outra empresa.

    Entra no `.fbk` de propósito: o bloqueio de consumo indevido é do CNPJ no
    Ambiente Nacional, não desta máquina. Restaurar em outro computador e sair
    consultando ignoraria um bloqueio que continua valendo.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ambiente import Ambiente, resolver as resolver_ambiente
from .identidade import normalizar

VERSAO_FORMATO = 1
PASTA = "ingestao"
SUFIXO = ".operacao.json"

# ── estados operacionais ────────────────────────────────────────────────────
# O que importa não são os nomes, é não misturar situações diferentes: "não
# consultei porque está em dia" e "não consultei porque o certificado venceu"
# exigem ações opostas de quem lê.
EM_SINCRONIA = "EM_SINCRONIA"                     # a SEFAZ confirmou: nada novo
PENDENTE = "PENDENTE"                             # há o que buscar, e pode buscar
AGUARDANDO_ONBOARDING = "AGUARDANDO_PRIMEIRA_SINCRONIZACAO"
DIVERGENCIA_EXTERNA = "DIVERGENCIA_EXTERNA"
CREDENCIAL_INDISPONIVEL = "CREDENCIAL_INDISPONIVEL"
CERTIFICADO_EXPIRADO = "CERTIFICADO_EXPIRADO"
BLOQUEADO_CONSUMO_INDEVIDO = "BLOQUEADO_CONSUMO_INDEVIDO"
# `656` cuja mensagem fala da POSIÇÃO enviada, não da frequência. Esperar não
# conserta: se o Ambiente Nacional discorda da nossa posição, voltar depois do
# cooldown repete o consumo indevido e queima cota. A empresa sai da automação
# e só volta por decisão humana — é o mesmo princípio da MONTE.
REVISAO_DE_SEQUENCIA = "REVISAO_DE_SEQUENCIA"
ERRO_TEMPORARIO = "ERRO_TEMPORARIO"
ERRO_PERMANENTE = "ERRO_PERMANENTE"
DESABILITADO = "DESABILITADO"
EM_EXECUCAO = "EM_EXECUCAO"                       # há trava ativa agora

# Estados em que o controlador NÃO consulta. Cada um por um motivo diferente,
# e é isso que o relatório precisa dizer.
NAO_CONSULTAVEIS = frozenset({
    AGUARDANDO_ONBOARDING, DIVERGENCIA_EXTERNA, CREDENCIAL_INDISPONIVEL,
    CERTIFICADO_EXPIRADO, BLOQUEADO_CONSUMO_INDEVIDO, REVISAO_DE_SEQUENCIA,
    ERRO_PERMANENTE, DESABILITADO, EM_EXECUCAO,
})

# Estados que o TEMPO não resolve: passar o cooldown não os apaga, porque a
# causa não é frequência. Só saem por decisão humana registrada.
EXIGEM_DECISAO_HUMANA = frozenset({DIVERGENCIA_EXTERNA, REVISAO_DE_SEQUENCIA})


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def _de_iso(txt: str) -> datetime | None:
    if not txt:
        return None
    try:
        d = datetime.fromisoformat(txt)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class EstadoOperacao:
    """Agenda e histórico recente de UMA unidade de controle."""
    identidade: str
    servico: str
    ambiente: str

    desabilitado: bool = False
    motivo_desabilitado: str = ""

    ultima_consulta_em: str = ""
    ultima_tentativa_ok_em: str = ""
    ultima_sincronia_confirmada_em: str = ""
    proxima_consulta_permitida_em: str = ""
    motivo_bloqueio: str = ""

    ultimo_cstat: str = ""
    ultimo_resultado: str = ""
    falhas_consecutivas: int = 0

    # ── diagnóstico do último 656 ─────────────────────────────────────────
    # Guardado por inteiro porque a pergunta que vem depois é sempre "por que
    # esta empresa parou?", e responder isso relendo a trilha inteira é caro.
    # A trilha continua sendo a fonte da verdade; isto é o resumo à mão.
    revisao_sequencia: bool = False        # trava que o tempo não abre
    ultimo_656_em: str = ""
    ultimo_656_subtipo: str = ""
    ultimo_656_motivo: str = ""            # xMotivo já higienizado
    ultimo_656_nsu_enviado: str = ""
    ultimo_656_checkpoint_local: str = ""
    ultimo_656_ult_nsu: str = ""
    ultimo_656_max_nsu: str = ""
    origem_bloqueio: str = ""
    hipotese_diagnostica: str = ""
    ciclos_executados: int = 0
    documentos_acumulados: int = 0

    # ── decisão administrativa: bloqueio mantido por falta de evidência ───
    # O ESTADO QUE FALTAVA. Até aqui, "ninguém olhou esta empresa ainda" e
    # "alguém olhou, não achou evidência e decidiu mantê-la fora da automação"
    # eram o MESMO estado — a empresa apenas ficava na fila. Quem chegasse
    # depois não tinha como saber se a pendência era nova ou se já havia sido
    # analisada, e a análise não deixava rastro nenhum.
    #
    # Isto NÃO bloqueia: é o REGISTRO de uma decisão. Quem tira a empresa da
    # automação continua sendo `revisao_sequencia` ou a divergência do
    # checkpoint. Por isso, também, apagar este registro não liberaria
    # consulta nenhuma.
    bloqueio_mantido: dict = field(default_factory=dict)

    # Carimbos das consultas recentes, para controle de frequência. Guardamos
    # só o necessário para a janela — não é histórico de auditoria, que vive na
    # trilha e é append-only.
    consultas_recentes: list = field(default_factory=list)

    versao: int = VERSAO_FORMATO
    atualizado_em: str = ""

    # ── consultas ──────────────────────────────────────────────────────────
    def bloqueado_ate(self) -> datetime | None:
        return _de_iso(self.proxima_consulta_permitida_em)

    def em_cooldown(self, agora: datetime | None = None) -> bool:
        ate = self.bloqueado_ate()
        return bool(ate and (agora or _agora()) < ate)

    def segundos_para_liberar(self, agora: datetime | None = None) -> int:
        ate = self.bloqueado_ate()
        if not ate:
            return 0
        return max(0, int((ate - (agora or _agora())).total_seconds()))

    def consultas_na_janela(self, minutos: int = 60,
                            agora: datetime | None = None) -> int:
        limite = (agora or _agora()) - timedelta(minutes=minutos)
        return sum(1 for c in self.consultas_recentes
                   if (_de_iso(c) or datetime.min.replace(tzinfo=timezone.utc)) >= limite)

    # ── mutações ───────────────────────────────────────────────────────────
    def registrar_consulta(self, agora: datetime | None = None) -> None:
        """Uma chamada saiu. Conta para a janela de frequência."""
        q = agora or _agora()
        self.ultima_consulta_em = _iso(q)
        self.consultas_recentes.append(_iso(q))
        # A janela só precisa das últimas horas; guardar mais é lixo que cresce.
        corte = q - timedelta(hours=6)
        self.consultas_recentes = [
            c for c in self.consultas_recentes
            if (_de_iso(c) or datetime.min.replace(tzinfo=timezone.utc)) >= corte][-200:]

    def bloquear(self, ate: datetime, motivo: str) -> None:
        """Adia a próxima consulta. Nunca ANTECIPA um bloqueio já existente."""
        atual = self.bloqueado_ate()
        if atual and atual >= ate:
            return
        self.proxima_consulta_permitida_em = _iso(ate)
        self.motivo_bloqueio = motivo

    def liberar(self) -> None:
        """Solta o bloqueio POR TEMPO. Não mexe em `revisao_sequencia`.

        Separado de propósito: cooldown vencido nunca deve destravar uma
        empresa cuja rejeição falava da posição, não da frequência."""
        self.proxima_consulta_permitida_em = ""
        self.motivo_bloqueio = ""
        self.origem_bloqueio = ""

    def registrar_656(self, subtipo: str, xmotivo: str, nsu_enviado: str,
                      checkpoint_local: str, ult_nsu: str = "",
                      max_nsu: str = "", quando: datetime | None = None,
                      hipotese: str = "") -> None:
        """Grava o diagnóstico completo da rejeição.

        `quando` é o instante DA REJEIÇÃO, não o do início do ciclo: é dele que
        a janela deve ser contada. Num ciclo de várias empresas os dois podem
        diferir em segundos, e errar para menos é justamente o que provoca o
        próximo 656."""
        q = quando or _agora()
        self.ultimo_656_em = _iso(q)
        self.ultimo_656_subtipo = str(subtipo or "")
        self.ultimo_656_motivo = str(xmotivo or "")[:200]
        self.ultimo_656_nsu_enviado = str(nsu_enviado or "")
        self.ultimo_656_checkpoint_local = str(checkpoint_local or "")
        self.ultimo_656_ult_nsu = str(ult_nsu or "")
        self.ultimo_656_max_nsu = str(max_nsu or "")
        self.hipotese_diagnostica = str(hipotese or "")

    def exigir_revisao_de_sequencia(self, motivo: str) -> None:
        """Tira a empresa da automação até alguém olhar."""
        self.revisao_sequencia = True
        self.origem_bloqueio = motivo

    def encerrar_revisao_de_sequencia(self, quem: str = "") -> None:
        """Só existe para ser chamado por uma decisão humana explícita."""
        self.revisao_sequencia = False
        self.hipotese_diagnostica = ""
        self.origem_bloqueio = f"revisão encerrada por {quem}" if quem else ""

    # ── decisão administrativa ─────────────────────────────────────────────
    @property
    def decisao_pendente(self) -> bool:
        """Ninguém registrou ainda uma decisão sobre esta empresa travada."""
        return not bool(self.bloqueio_mantido)

    def manter_bloqueio_sem_evidencia(self, *, quem: str, justificativa: str,
                                      estado_no_momento: str = "",
                                      agora: datetime | None = None) -> str:
        """Alguém olhou, não achou evidência suficiente e decidiu esperar.

        **Não muda nada que decida consulta.** Não toca em `revisao_sequencia`,
        em cooldown, em checkpoint nem em posse: a empresa já estava fora da
        automação e continua fora pelo mesmo motivo de antes. O que muda é que
        a pendência deixa de ser anônima — passa a ter autor, data e porquê.

        IDEMPOTÊNCIA, E POR QUE ELA É PELA JUSTIFICATIVA
            Clicar duas vezes no mesmo botão não pode empilhar decisões
            iguais: um histórico com quinze linhas idênticas esconde a única
            que importa. Mas reafirmar com um motivo NOVO é informação de
            verdade ("conferi de novo em outubro, o cliente ainda não
            respondeu") — essa é registrada, e a data da PRIMEIRA decisão é
            preservada, porque é ela que diz há quanto tempo isto espera.

            Devolve ``"REGISTRADA"``, ``"REAFIRMADA"`` ou ``"REPETIDA"``. Só as
            duas primeiras mudam o estado; ``REPETIDA`` não grava nada, e quem
            chama decide o que responder.
        """
        q = agora or _agora()
        j = str(justificativa or "").strip()
        atual = dict(self.bloqueio_mantido or {})
        if atual and atual.get("justificativa", "") == j:
            return "REPETIDA"

        self.bloqueio_mantido = {
            "em": _iso(q),
            "operador": str(quem or ""),
            "justificativa": j,
            "estado_no_momento": str(estado_no_momento or ""),
            "primeira_em": atual.get("primeira_em") or _iso(q),
            "reafirmacoes": int(atual.get("reafirmacoes", 0)) + (1 if atual else 0),
        }
        return "REAFIRMADA" if atual else "REGISTRADA"

    def registrar_sucesso(self, cstat: str, resultado: str,
                          documentos: int = 0, agora: datetime | None = None) -> None:
        q = agora or _agora()
        self.ultimo_cstat = str(cstat or "")
        self.ultimo_resultado = str(resultado or "")
        self.ultima_tentativa_ok_em = _iso(q)
        self.falhas_consecutivas = 0
        self.documentos_acumulados += int(documentos or 0)

    def registrar_falha(self, resultado: str, cstat: str = "",
                        agora: datetime | None = None) -> None:
        self.ultimo_resultado = str(resultado or "")
        if cstat:
            self.ultimo_cstat = str(cstat)
        self.falhas_consecutivas += 1

    def resumo(self) -> dict:
        ident = normalizar(self.identidade)
        return {
            "empresa": ident.mascarado() if ident.valido else "<inválida>",
            "servico": self.servico, "ambiente": self.ambiente,
            "desabilitado": self.desabilitado,
            "ultima_consulta_em": self.ultima_consulta_em or None,
            "ultima_tentativa_ok_em": self.ultima_tentativa_ok_em or None,
            "proxima_consulta_permitida_em": self.proxima_consulta_permitida_em or None,
            "motivo_bloqueio": self.motivo_bloqueio or None,
            "revisao_sequencia": self.revisao_sequencia,
            "ultimo_656_em": self.ultimo_656_em or None,
            "ultimo_656_subtipo": self.ultimo_656_subtipo or None,
            "ultimo_656_nsu_enviado": self.ultimo_656_nsu_enviado or None,
            "origem_bloqueio": self.origem_bloqueio or None,
            "hipotese_diagnostica": self.hipotese_diagnostica or None,
            "ultimo_cstat": self.ultimo_cstat or None,
            "ultimo_resultado": self.ultimo_resultado or None,
            "falhas_consecutivas": self.falhas_consecutivas,
            "bloqueio_mantido_em": (self.bloqueio_mantido or {}).get("em") or None,
            "bloqueio_mantido_por": (self.bloqueio_mantido or {}).get("operador") or None,
            "decisao_pendente": self.decisao_pendente,
            "consultas_1h": self.consultas_na_janela(60),
            "documentos_acumulados": self.documentos_acumulados,
            "ciclos_executados": self.ciclos_executados,
        }

    def para_json(self) -> dict:
        return asdict(self)

    @classmethod
    def de_json(cls, d: dict) -> "EstadoOperacao":
        conhecidos = set(cls.__dataclass_fields__)
        limpo = {k: (list(v) if isinstance(v, list)
                     else dict(v) if isinstance(v, dict) else v)
                 for k, v in d.items() if k in conhecidos}
        return cls(**limpo)


class RepositorioOperacao:
    """Lê e grava o estado operacional. Escrita atômica, como o checkpoint."""

    def __init__(self, dados_dir):
        self.dados_dir = Path(dados_dir)

    def caminho(self, identidade, servico: str, ambiente,
                criar: bool = False) -> Path:
        ident = normalizar(identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida: {ident.motivo}")
        amb = resolver_ambiente(ambiente)
        pasta = self.dados_dir / ident.valor / PASTA
        if criar:
            pasta.mkdir(parents=True, exist_ok=True)
        return pasta / f"{servico}.{amb.nome}{SUFIXO}"

    def carregar(self, identidade, servico: str, ambiente) -> EstadoOperacao:
        """Nunca levanta por conteúdo: agenda ilegível vira agenda nova.

        É seguro — diferente do checkpoint, perder a agenda não perde documento.
        No pior caso o controlador consulta antes do ideal, e o `656` da SEFAZ
        continua sendo a rede de proteção final."""
        ident = normalizar(identidade)
        amb = resolver_ambiente(ambiente)
        p = self.caminho(ident.valor, servico, amb)
        base = EstadoOperacao(identidade=ident.valor, servico=servico,
                              ambiente=amb.nome)
        if not p.exists():
            return base
        try:
            d = json.loads(p.read_text("utf-8-sig"))
            if not isinstance(d, dict):
                return base
            e = EstadoOperacao.de_json(d)
            e.identidade, e.servico, e.ambiente = ident.valor, servico, amb.nome
            return e
        except (ValueError, OSError):
            return base

    def salvar(self, estado: EstadoOperacao) -> Path:
        estado.atualizado_em = _iso(_agora())
        p = self.caminho(estado.identidade, estado.servico, estado.ambiente,
                         criar=True)
        dados = json.dumps(estado.para_json(), ensure_ascii=False,
                           indent=1).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(dados)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return p
