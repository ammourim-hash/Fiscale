"""
distribuicao.py — o motor de paginação. Não sabe o que é uma NF-e.

A REGRA QUE JUSTIFICA O MÓDULO INTEIRO
    **Receber uma resposta não autoriza avançar o checkpoint.** O `ultNSU` só
    anda depois que os documentos daquele lote estão gravados e confirmados em
    disco. A ordem é:

        consultar → receber lote → validar envelope → preservar bruto
                  → CONFIRMAR persistência → avançar checkpoint

    Se qualquer etapa antes da confirmação falhar, o checkpoint anterior fica
    como está. Repetir a consulta pode trazer o mesmo lote de novo — duplicata
    de transporte, que a camada de documentos resolve por deduplicação. O
    contrário não tem conserto: para o CT-e não existe `consChCTe`, então um NSU
    pulado não pode ser pedido por chave depois.

    Em uma frase: **é melhor reprocessar um lote do que perder documentos.**

SEPARAÇÃO DE RESPONSABILIDADES
    Este módulo conhece: checkpoint, trava, laço, condições de parada, limite de
    iterações e o relatório da execução.

    Este módulo NÃO conhece: SOAP, REST, `docZip`, `LoteDFe`, gzip, base64,
    namespace, `cStat`, nem uma única tag de NF-e ou CT-e. Tudo isso vive na
    `Fonte`, que é a peça específica de cada serviço — e que a ING 2 ainda não
    implementa para valer.

CAPACIDADES SÃO POR SERVIÇO, NÃO DA ABSTRAÇÃO
    A NF-e tem `consChNFe`; o CT-e **não tem** equivalente. Se a interface comum
    tivesse um método `consultar_por_chave`, o CT-e precisaria implementá-lo
    para levantar — e alguém, um dia, chamaria assumindo que existe. Por isso a
    consulta por chave é uma capacidade **declarada** (`CAP_CONS_CHAVE`) e um
    protocolo separado (`FontePorChave`): quem não a declara não a tem, e o
    motor nunca a chama.

NADA AQUI CHAMA A REDE
    O motor recebe uma `Fonte` já pronta. Na ING 2 as fontes são dublês de
    teste. Nenhuma chamada a SEFAZ, ADN ou Portal Nacional acontece.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from . import checkpoint as cpm
from .ambiente import Ambiente, resolver as resolver_ambiente
from .checkpoint import Checkpoint, RepositorioCheckpoint, normalizar_nsu, nsu_int
from . import resposta_bruta as bruta
from .identidade import normalizar
from .trava import TravaOcupada, travar

# ── capacidades declaradas por cada serviço ─────────────────────────────────
CAP_DIST_NSU = "distNSU"        # varrer a partir de um NSU
CAP_CONS_NSU = "consNSU"        # buscar UM NSU específico (fechar lacuna)
CAP_CONS_CHAVE = "consChave"    # buscar por chave de acesso — NF-e sim, CT-e NÃO

# ── por que a paginação parou ───────────────────────────────────────────────
FIM_EM_DIA = "EM_DIA"                       # ultNSU alcançou maxNSU
FIM_SEM_DOCUMENTOS = "SEM_DOCUMENTOS"       # o serviço não tem nada novo agora
FIM_LIMITE_LOTES = "LIMITE_LOTES"           # trava defensiva contra laço infinito
FIM_SEM_PROGRESSO = "SEM_PROGRESSO"         # o NSU não andou: serviço inconsistente
FIM_ERRO_TRANSITORIO = "ERRO_TRANSITORIO"   # rede, 429, indisponibilidade
FIM_ERRO_DEFINITIVO = "ERRO_DEFINITIVO"     # rejeição que repetir não resolve
FIM_CREDENCIAL = "CREDENCIAL_INVALIDA"
FIM_RESPOSTA_INVALIDA = "RESPOSTA_INVALIDA"
FIM_PERSISTENCIA = "FALHA_DE_PERSISTENCIA"  # não gravou: o checkpoint NÃO anda
FIM_RESPOSTA_BRUTA = "FALHA_RESPOSTA_BRUTA"   # não guardou a prova: idem
FIM_TRAVA = "TRAVA_OCUPADA"

LIMITE_LOTES_PADRAO = 200      # 200 × 50 documentos = 10.000 por execução


# ── erros que a Fonte usa para se explicar ──────────────────────────────────
class ErroFonte(Exception):
    """Base dos erros que uma Fonte reporta ao motor."""


class ErroTransitorio(ErroFonte):
    """Vale tentar de novo mais tarde (rede, 429, serviço fora do ar)."""


class ErroDefinitivo(ErroFonte):
    """Repetir não resolve (rejeição de schema, CNPJ não autorizado)."""


class RespostaInvalida(ErroFonte):
    """Veio resposta, mas não dá para confiar nela."""


class FalhaDePersistencia(Exception):
    """O acervo não confirmou a gravação. O checkpoint NÃO pode avançar."""


# ── o que trafega ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DocumentoBruto:
    """Um documento como veio do serviço, ainda sem interpretação.

    O motor não olha dentro de `conteudo`. Quem sabe o que é um `procCTe` é o
    acervo e, mais tarde, o parser."""
    nsu: str
    conteudo: bytes
    schema: str = ""             # ex.: "procCTe_v2.00.xsd" — vem do serviço
    tipo: str = ""               # rótulo do serviço, quando houver
    chave: str = ""              # chave de acesso, quando o serviço informar

    def __post_init__(self):
        # NSU AUSENTE FICA AUSENTE.
        #
        # `normalizar_nsu("")` devolve quinze zeros — e zero é um NSU válido,
        # o primeiro da fila. Gravar isso transformava "não sei de onde veio"
        # em "veio do NSU 0": uma afirmação sobre a procedência que ninguém
        # mediu. Vale para o XML importado à mão, que nunca tem NSU.
        bruto = str(self.nsu or "").strip()
        object.__setattr__(self, "nsu", normalizar_nsu(bruto) if bruto else "")


@dataclass(frozen=True)
class Lote:
    """A resposta de uma consulta, já normalizada pela Fonte."""
    documentos: tuple[DocumentoBruto, ...] = ()
    ult_nsu: str = ""            # último NSU pesquisado, como o serviço informou
    max_nsu: str = ""            # maior NSU existente, quando informado
    sem_documentos: bool = False  # o serviço disse explicitamente "não há nada"
    motivo: str = ""             # texto do serviço, para diagnóstico

    @property
    def vazio(self) -> bool:
        return not self.documentos


# ── os contratos que o motor exige ──────────────────────────────────────────
@runtime_checkable
class Fonte(Protocol):
    """A parte que conhece o serviço. Uma por (serviço, ambiente)."""
    servico: str
    ambiente: Ambiente
    capacidades: frozenset

    def consultar(self, desde_nsu: str) -> Lote:
        """Pede o próximo lote a partir de `desde_nsu`.

        Levanta `ErroTransitorio`, `ErroDefinitivo` ou `RespostaInvalida`."""
        ...


@runtime_checkable
class FontePorChave(Protocol):
    """SÓ a NF-e implementa. O CT-e não tem `consChCTe` no contrato oficial."""
    def consultar_por_chave(self, chave: str) -> Lote: ...


@runtime_checkable
class Acervo(Protocol):
    """Onde o documento bruto é preservado."""

    def preservar(self, doc: DocumentoBruto) -> None:
        """Grava. Levanta se não conseguir."""
        ...

    def confirmar(self, documentos) -> bool:
        """Os documentos estão **duráveis** em disco?

        Só um `True` daqui autoriza o checkpoint a avançar. Devolver `True` sem
        ter certeza é o único jeito de este motor perder documento."""
        ...


# ── higiene de log ──────────────────────────────────────────────────────────
_SUSPEITO = re.compile(
    r"(senha|password|token|secret|api[_\- ]?key|authorization|private[_\- ]?key)"
    r"\s*[:=]\s*\S+", re.I)
_PEM = re.compile(r"-----BEGIN[^-]+-----.*?-----END[^-]+-----", re.S)
LIMITE_MENSAGEM = 300


def higienizar(texto) -> str:
    """Tira do texto o que não pode ir para log, e encurta.

    Mensagem de erro de biblioteca às vezes ecoa o que recebeu. XML fiscal
    inteiro em log também não serve — é volumoso e contém dado de terceiros."""
    s = str(texto or "")
    s = _PEM.sub("<certificado omitido>", s)
    s = _SUSPEITO.sub(lambda m: m.group(0).split(":")[0].split("=")[0] + ": <omitido>", s)
    s = re.sub(r"<\?xml.*", "<xml omitido>", s, flags=re.S)
    s = " ".join(s.split())
    return s[:LIMITE_MENSAGEM] + ("…" if len(s) > LIMITE_MENSAGEM else "")


# ── relatório da execução ───────────────────────────────────────────────────
@dataclass
class ResultadoExecucao:
    """O que aconteceu. É isto que vai para o log e para a tela."""
    identidade_mascarada: str
    servico: str
    ambiente: str
    nsu_inicial: str
    nsu_final: str
    max_nsu: str = ""
    lotes: int = 0
    documentos: int = 0
    duracao_s: float = 0.0
    status: str = ""
    motivo_parada: str = ""
    erro: str = ""
    avancou: bool = False
    notas: list[str] = field(default_factory=list)

    @property
    def sucesso(self) -> bool:
        return self.status in (cpm.OK, cpm.EM_DIA)

    def para_log(self) -> dict:
        """Sem senha, sem certificado, sem chave privada, sem XML."""
        return {
            "empresa": self.identidade_mascarada,
            "servico": self.servico,
            "ambiente": self.ambiente,
            "nsu_inicial": self.nsu_inicial,
            "nsu_final": self.nsu_final,
            "max_nsu": self.max_nsu or None,
            "lotes": self.lotes,
            "documentos": self.documentos,
            "duracao_s": round(self.duracao_s, 3),
            "status": self.status,
            "motivo_parada": self.motivo_parada,
            "erro": self.erro or None,
        }

    def linha(self) -> str:
        d = self.para_log()
        de_max = f" de {d['max_nsu']}" if d["max_nsu"] else ""
        erro = f" · {d['erro']}" if d["erro"] else ""
        return (f"[{d['status']}] {d['empresa']} {d['servico']}/{d['ambiente']} "
                f"NSU {d['nsu_inicial']}->{d['nsu_final']}{de_max} "
                f"· {d['lotes']} lote(s) · {d['documentos']} doc(s) "
                f"· {d['duracao_s']}s · {d['motivo_parada']}{erro}")


# ── o coordenador ───────────────────────────────────────────────────────────
class DistribuicaoRunner:
    """Roda a paginação de UM (empresa, serviço, ambiente).

    Não conhece XML. Só conhece: onde parou, pedir o próximo, mandar guardar,
    conferir que guardou, e então anotar onde chegou.
    """

    def __init__(self, repositorio: RepositorioCheckpoint,
                 limite_lotes: int = LIMITE_LOTES_PADRAO):
        if limite_lotes < 1:
            raise ValueError("limite_lotes deve ser >= 1")
        self.repo = repositorio
        self.limite_lotes = limite_lotes

    # ------------------------------------------------------------------
    def executar(self, identidade, fonte: Fonte, acervo: Acervo,
                 usar_trava: bool = True) -> ResultadoExecucao:
        """Varre até acabar, dar erro ou bater o limite. **Nunca levanta** por
        erro de serviço — devolve o resultado com o status, para que uma empresa
        com problema não interrompa as demais."""
        inicio = time.monotonic()
        ident = normalizar(identidade)
        amb = resolver_ambiente(fonte.ambiente)
        srv = cpm.validar_servico(fonte.servico)
        mascarada = ident.mascarado() if ident.valido else "<identidade inválida>"

        res = ResultadoExecucao(identidade_mascarada=mascarada, servico=srv,
                                ambiente=amb.nome, nsu_inicial="", nsu_final="")
        if not ident.valido:
            res.status = cpm.ERRO_DEFINITIVO
            res.motivo_parada = FIM_ERRO_DEFINITIVO
            res.erro = f"identidade fiscal inválida: {ident.motivo}"
            return res

        try:
            if usar_trava:
                with travar(self.repo.dados_dir, ident.valor, srv, amb) as notas:
                    res.notas.extend(notas)
                    self._laco(ident.valor, srv, amb, fonte, acervo, res)
            else:
                self._laco(ident.valor, srv, amb, fonte, acervo, res)
        except TravaOcupada as exc:
            res.status = cpm.INTERROMPIDO
            res.motivo_parada = FIM_TRAVA
            res.erro = higienizar(exc)
        except cpm.CheckpointCorrompido as exc:
            # NUNCA zerar aqui. Evidência preservada, decisão do usuário.
            res.status = cpm.CORROMPIDO
            res.motivo_parada = cpm.CORROMPIDO
            res.erro = higienizar(exc)
        finally:
            res.duracao_s = time.monotonic() - inicio
        return res

    # ------------------------------------------------------------------
    def _laco(self, identidade: str, servico: str, amb: Ambiente,
              fonte: Fonte, acervo: Acervo, res: ResultadoExecucao) -> None:
        cp = self.repo.carregar(identidade, servico, amb)     # pode levantar Corrompido
        res.nsu_inicial = res.nsu_final = cp.ult_nsu
        res.max_nsu = cp.max_nsu

        cp.ultima_consulta = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # ATENÇÃO: NÃO pular a execução quando `ult_nsu == max_nsu`.
        #
        # O `max_nsu` guardado é uma FOTOGRAFIA da última resposta. Quando a
        # empresa emite ou recebe documentos novos, o maior NSU do Ambiente
        # Nacional cresce — e o nosso checkpoint não sabe disso até perguntar.
        # Um atalho aqui faria a empresa parar de buscar para sempre depois de
        # ficar em dia uma vez, e o sistema continuaria dizendo "EM_DIA".
        # (Este teste pegou exatamente isso.)
        #
        # `em_dia` é condição de parada DENTRO do laço, não motivo para não
        # começar. O custo é uma consulta barata que o serviço responde com
        # "não há documentos".
        vistos: set[str] = set()

        for _ in range(self.limite_lotes):
            desde = cp.ult_nsu

            # ---- 1. consultar -------------------------------------------------
            try:
                lote = fonte.consultar(desde)
            except ErroTransitorio as exc:
                return self._encerrar_com_erro(cp, res, cpm.ERRO_TRANSITORIO,
                                               FIM_ERRO_TRANSITORIO, exc)
            except RespostaInvalida as exc:
                return self._encerrar_com_erro(cp, res, cpm.ERRO_DEFINITIVO,
                                               FIM_RESPOSTA_INVALIDA, exc)
            except ErroDefinitivo as exc:
                return self._encerrar_com_erro(cp, res, cpm.ERRO_DEFINITIVO,
                                               FIM_ERRO_DEFINITIVO, exc)
            except Exception as exc:
                # Credencial ruim chega aqui (SenhaAusente, CertificadoInvalido…).
                # Vira status próprio para o relatório separar "empresa com
                # certificado vencido" de "serviço fora do ar".
                nome = type(exc).__name__
                if "Credencial" in nome or "Certificado" in nome or "Senha" in nome:
                    return self._encerrar_com_erro(cp, res, cpm.CREDENCIAL_INVALIDA,
                                                   FIM_CREDENCIAL, exc)
                return self._encerrar_com_erro(cp, res, cpm.ERRO_DEFINITIVO,
                                               FIM_ERRO_DEFINITIVO, exc)

            # ---- 2. validar o envelope ---------------------------------------
            if not isinstance(lote, Lote):
                return self._encerrar_com_erro(
                    cp, res, cpm.ERRO_DEFINITIVO, FIM_RESPOSTA_INVALIDA,
                    f"a fonte devolveu {type(lote).__name__} em vez de Lote")
            if lote.max_nsu:
                cp.max_nsu = normalizar_nsu(lote.max_nsu)
                res.max_nsu = cp.max_nsu

            # ---- 3. o serviço disse que não há nada --------------------------
            if lote.sem_documentos or lote.vazio:
                cp.status = cpm.EM_DIA if cp.em_dia else cpm.OK
                cp.ultimo_sucesso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                cp.falhas_consecutivas = 0
                cp.ultimo_erro = ""
                self.repo.salvar(cp)
                res.status = cp.status
                res.motivo_parada = FIM_EM_DIA if cp.em_dia else FIM_SEM_DOCUMENTOS
                if lote.motivo:
                    res.notas.append(higienizar(lote.motivo))
                return

            # ---- 4. preservar o bruto ----------------------------------------
            preservados: list[DocumentoBruto] = []
            try:
                for doc in lote.documentos:
                    acervo.preservar(doc)
                    preservados.append(doc)
                # ---- 5. CONFIRMAR a persistência ------------------------------
                confirmado = bool(acervo.confirmar(tuple(preservados)))
            except Exception as exc:
                return self._encerrar_com_erro(cp, res, cpm.ERRO_TRANSITORIO,
                                               FIM_PERSISTENCIA, exc,
                                               parciais=len(preservados))
            if not confirmado:
                return self._encerrar_com_erro(
                    cp, res, cpm.ERRO_TRANSITORIO, FIM_PERSISTENCIA,
                    "o acervo não confirmou a gravação do lote",
                    parciais=len(preservados))

            # ---- 5b. a RESPOSTA BRUTA, antes de o ponteiro andar --------------
            #
            # POR QUE AQUI, E NÃO NO CONECTOR
            #     O conector declara que não toca disco, e a separação é
            #     deliberada: ele produz DADO, quem persiste é esta camada.
            #     Ele expõe `ultima_resposta`, e `Resposta.bruto` carrega os
            #     bytes exatos do transporte — é por aí que eles chegam aqui,
            #     sem que o conector precise saber onde fica o acervo.
            #
            # POR QUE ANTES DO PASSO 6
            #     Depois de o `ultNSU` andar, o serviço não reentrega. Uma
            #     resposta mal lida vira dano permanente e sem prova. Foi
            #     exatamente isso que impediu de recuperar os NSU reais dos 72
            #     CT-e da CTE 4: os documentos estavam certos, a leitura de um
            #     campo estava errada, e não havia de onde reler.
            #
            # FALHAR AQUI SEGURA O CHECKPOINT
            #     `preservar()` levanta quando não consegue garantir o disco —
            #     ela grava, relê e confere o SHA-256. Sem rastro guardado, o
            #     ponteiro não anda: é a mesma promessa do passo 5, estendida à
            #     prova do que o serviço respondeu.
            bruto = getattr(getattr(fonte, "ultima_resposta", None), "bruto", b"")
            if bruto:
                try:
                    bruta.preservar(acervo.dados_dir, identidade, bruto,
                                    servico=servico, ambiente=amb)
                except Exception as exc:
                    return self._encerrar_com_erro(
                        cp, res, cpm.ERRO_TRANSITORIO, FIM_RESPOSTA_BRUTA, exc,
                        parciais=len(preservados))

            # ---- 6. só AGORA o checkpoint avança ------------------------------
            # O NSU que vale é o do serviço, quando informado; senão, o maior
            # NSU efetivamente recebido. Nunca um palpite.
            novo = normalizar_nsu(lote.ult_nsu) if lote.ult_nsu else \
                max((d.nsu for d in lote.documentos), default=desde)

            if nsu_int(novo) <= nsu_int(desde) or novo in vistos:
                # O serviço não andou. Continuar seria laço infinito; recuar o
                # ponteiro seria pior. Para e reporta o estado inconsistente.
                cp.status = cpm.ERRO_DEFINITIVO
                cp.ultimo_erro = (f"o serviço devolveu documentos mas não avançou o NSU "
                                  f"(pedido a partir de {desde}, devolveu {novo})")
                cp.falhas_consecutivas += 1
                self.repo.salvar(cp)
                res.status, res.motivo_parada = cpm.ERRO_DEFINITIVO, FIM_SEM_PROGRESSO
                res.erro = cp.ultimo_erro
                res.lotes += 1
                res.documentos += len(lote.documentos)
                return
            vistos.add(novo)

            cp.ult_nsu = novo
            cp.lotes_recebidos += 1
            cp.documentos_recebidos += len(lote.documentos)
            cp.ultimo_sucesso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cp.ultimo_erro = ""
            cp.falhas_consecutivas = 0
            cp.status = cpm.EM_DIA if cp.em_dia else cpm.OK
            self.repo.salvar(cp)                     # grava a cada lote, atomicamente

            res.nsu_final = cp.ult_nsu
            res.lotes += 1
            res.documentos += len(lote.documentos)

            if cp.em_dia:
                res.status, res.motivo_parada = cpm.EM_DIA, FIM_EM_DIA
                return

        # ---- limite defensivo -------------------------------------------------
        # Não é erro: é a execução respeitando um teto. A próxima continua de onde
        # esta parou, porque o checkpoint foi gravado a cada lote.
        res.status = cpm.OK
        res.motivo_parada = FIM_LIMITE_LOTES
        res.notas.append(f"limite de {self.limite_lotes} lote(s) por execução atingido; "
                         "a próxima execução continua deste NSU")

    # ------------------------------------------------------------------
    def _encerrar_com_erro(self, cp: Checkpoint, res: ResultadoExecucao,
                           status: str, motivo: str, erro, parciais: int = 0) -> None:
        """Registra a falha SEM avançar o `ultNSU`.

        É aqui que a promessa se cumpre: qualquer coisa que dê errado antes da
        confirmação deixa o checkpoint exatamente onde estava."""
        cp.status = status
        cp.ultimo_erro = higienizar(erro)
        cp.falhas_consecutivas += 1
        self.repo.salvar(cp)                 # o ult_nsu gravado é o de antes
        res.status = status
        res.motivo_parada = motivo
        res.erro = cp.ultimo_erro
        res.nsu_final = cp.ult_nsu
        if parciais:
            res.notas.append(f"{parciais} documento(s) já gravado(s) serão reprocessados "
                             "na próxima execução (duplicata de transporte é esperada)")


# ── varredura de várias empresas ────────────────────────────────────────────
def executar_para_empresas(runner: DistribuicaoRunner, itens) -> list[ResultadoExecucao]:
    """Roda várias `(identidade, fonte, acervo)` em sequência.

    **Uma empresa com problema não interrompe as outras.** Certificado vencido,
    serviço fora do ar ou checkpoint corrompido viram um resultado com status —
    e a varredura segue para a próxima empresa. Foi por falta disso que a
    DI CAVALCANTI, vencida, poderia derrubar a coleta das outras 20.
    """
    out: list[ResultadoExecucao] = []
    for identidade, fonte, acervo in itens:
        try:
            out.append(runner.executar(identidade, fonte, acervo))
        except Exception as exc:      # cinto e suspensório: executar() não deve levantar
            ident = normalizar(identidade)
            out.append(ResultadoExecucao(
                identidade_mascarada=ident.mascarado() if ident.valido else "<inválida>",
                servico=getattr(fonte, "servico", "?"),
                ambiente=getattr(getattr(fonte, "ambiente", None), "nome", "?"),
                nsu_inicial="", nsu_final="",
                status=cpm.ERRO_DEFINITIVO, motivo_parada=FIM_ERRO_DEFINITIVO,
                erro=higienizar(f"falha inesperada: {type(exc).__name__}")))
    return out
