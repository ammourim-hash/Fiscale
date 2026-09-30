"""
checkpoint.py — onde a varredura parou. Estado crítico, tratado como tal.

POR QUE ISTO É O ARQUIVO MAIS PERIGOSO DO MOTOR
    O Ambiente Nacional entrega documento por NSU, em sequência. O checkpoint é
    a única memória de até onde já se leu. Para o CT-e isso é decisivo: o
    contrato oficial (`PL_CTeDistDFe_100`) oferece apenas `distNSU` e `consNSU`
    — **não existe `consChCTe`**. Sem consulta por chave, um checkpoint perdido
    não pode ser reconstruído pedindo os documentos que faltam: só varrendo NSU.
    E pedindo do zero, o AN devolve somente os **últimos 3 meses**. Ou seja:
    perder o checkpoint do CT-e perde documento de verdade, para sempre.

    Daí as três regras que governam este módulo:

    1. **Nunca sobrescrever um checkpoint válido com escrita parcial.**
       Grava em temporário, força para o disco, e só então troca atomicamente.
    2. **Nunca avançar antes de o documento estar salvo.** Quem chama confirma
       a persistência; o checkpoint anda depois. Repetir um lote gera duplicata
       de transporte, que a deduplicação resolve; pular um NSU perde nota.
    3. **Nunca "consertar" corrupção zerando o NSU.** Zerar parece recuperação e
       é perda silenciosa de até três meses de histórico. Arquivo corrompido é
       preservado como evidência e exige decisão explícita.

A CHAVE
    `(identidade_fiscal, serviço, ambiente)` — nunca global, nunca só CNPJ.
    A ING 1 confirmou que existe titular **CPF** no cadastro real, então a
    identidade é o identificador canônico (14 ou 11 dígitos), não "o CNPJ".

    O ambiente entra na chave porque NSU de homologação e de produção são
    sequências diferentes e independentes. Misturar os dois faria a empresa
    pular documentos reais sem nenhum aviso.

ONDE FICA
    `<raiz>/<identidade>/ingestao/<servico>.<ambiente>.json`

    Dentro da pasta da empresa, de propósito: o isolamento passa a ser
    estrutural. Um erro de código que perca a identidade não tem como escrever
    no checkpoint de outra empresa, porque o caminho simplesmente não existe.
    E respeita a raiz portátil, porque a raiz vem por parâmetro.

SEM SEGREDO
    O checkpoint não guarda senha, certificado, caminho de `.pfx` nem sessão.
    Só posição de leitura e diagnóstico. É o que permite entrar no backup.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from .ambiente import Ambiente, resolver as resolver_ambiente
from .identidade import normalizar

VERSAO_FORMATO = 1
PASTA = "ingestao"

# ── serviços de distribuição ────────────────────────────────────────────────
NFE_DISTRIBUICAO = "NFE_DISTRIBUICAO"
CTE_DISTRIBUICAO = "CTE_DISTRIBUICAO"
NFSE_DISTRIBUICAO = "NFSE_DISTRIBUICAO"
SERVICOS = (NFE_DISTRIBUICAO, CTE_DISTRIBUICAO, NFSE_DISTRIBUICAO)

# ── situação da última execução ─────────────────────────────────────────────
NUNCA_EXECUTADO = "NUNCA_EXECUTADO"
OK = "OK"
EM_DIA = "EM_DIA"                 # ultNSU == maxNSU: não há o que buscar
ERRO_TRANSITORIO = "ERRO_TRANSITORIO"
ERRO_DEFINITIVO = "ERRO_DEFINITIVO"
CREDENCIAL_INVALIDA = "CREDENCIAL_INVALIDA"
CORROMPIDO = "CORROMPIDO"
INTERROMPIDO = "INTERROMPIDO"     # travou/caiu antes de confirmar

# ── sincronismo: o que NÓS temos × o que a SEFAZ diz (ING 3B-R2) ───────────
# A consulta real de 13/08/2026 mostrou por que estes dois números precisam de
# nomes diferentes: a SEFAZ devolveu `ultNSU=1361` numa REJEIÇÃO, para uma
# empresa cujo acervo local estava em zero. São afirmações distintas:
#
#   ult_nsu             o último NSU cujos documentos estão COMPROVADAMENTE no
#                       acervo. Só avança depois de persistência confirmada.
#   nsu_observado_sefaz a posição que o Ambiente Nacional informou. É notícia
#                       sobre o mundo, não prova de posse.
#
# Confundir os dois faria o FISCALE afirmar que tem 1.361 documentos que nunca
# viu — e, pior, pular todos eles em silêncio, sem chance de recuperação depois
# dos 3 meses de retenção.
SINCRONISMO_SEM_INFORMACAO = "SEM_INFORMACAO"    # a SEFAZ ainda não se manifestou
SINCRONISMO_EM_DIA = "EM_SINCRONIA"              # local e remoto batem
SINCRONISMO_DIVERGENCIA = "DIVERGENCIA_EXTERNA"  # remoto à FRENTE do local

# Diagnóstico (hipótese) é separado de estado (fato observado), de propósito:
# o fato é que os números divergem; a causa é suposição até alguém conferir.
DIAGNOSTICO_NENHUM = ""
POSSIVEL_CONSUMIDOR_EXTERNO = "POSSIVEL_CONSUMIDOR_EXTERNO"

# Cobertura do passado. "DESCONHECIDA" é resposta honesta, e vira o padrão assim
# que uma divergência aparece: o FISCALE não promete o que não baixou.
COBERTURA_COMPLETA_DESDE_ZERO = "COMPLETA_DESDE_ZERO"
COBERTURA_DESCONHECIDA = "DESCONHECIDA"
COBERTURA_A_PARTIR_DE_MARCO = "A_PARTIR_DE_MARCO"
# A distribuição foi consumida até `ult_nsu`, mas os documentos daquele período
# estão no ACERVO LEGADO (`<cnpj>/nfe/*.xml`), e não no acervo da ING 3A.
# Posição comprovada da distribuição ≠ documentos no acervo novo.
COBERTURA_ACERVO_LEGADO = "EM_ACERVO_LEGADO"

# De onde veio o `ult_nsu` atual. Migrado do código antigo não é a mesma coisa
# que obtido pelo conector novo: o primeiro é herança, o segundo é prova desta
# implementação.
ORIGEM_INDEFINIDA = ""
ORIGEM_CONECTOR = "CONECTOR"
ORIGEM_LEGADO = "LEGADO_NFE_PY"

NSU_ZERO = "0" * 15
_SO_DIGITOS = re.compile(r"\D")


class ErroCheckpoint(Exception):
    """Base."""


class CheckpointCorrompido(ErroCheckpoint):
    """O arquivo existe e não é utilizável.

    NÃO é tratado como "começar do zero". Zerar o NSU aqui seria perder
    silenciosamente até três meses de documentos — ver o cabeçalho.
    """
    def __init__(self, mensagem: str, evidencia: Path | None = None):
        super().__init__(mensagem)
        self.evidencia = evidencia


def normalizar_nsu(v) -> str:
    """NSU canônico: 15 dígitos com zeros à esquerda (TNSU: `[0-9]{15}`).

    Guardar como texto e não como número é deliberado: o zero à esquerda faz
    parte do formato oficial, e `int` o descartaria."""
    d = _SO_DIGITOS.sub("", str(v if v is not None else ""))
    if not d:
        return NSU_ZERO
    return d[-15:].zfill(15) if len(d) > 15 else d.zfill(15)


def nsu_int(v) -> int:
    return int(normalizar_nsu(v))


def validar_servico(servico: str) -> str:
    s = str(servico or "").strip().upper()
    if s not in SERVICOS:
        raise ValueError(f"serviço desconhecido: {servico!r} (esperado um de {SERVICOS})")
    return s


@dataclass
class Checkpoint:
    """A posição da varredura de UM (identidade, serviço, ambiente)."""
    identidade: str                       # CNPJ 14 díg. ou CPF 11 díg., canônico
    servico: str
    ambiente: str                         # "producao" | "homologacao"
    ult_nsu: str = NSU_ZERO
    max_nsu: str = ""                     # vazio = o serviço ainda não informou
    status: str = NUNCA_EXECUTADO
    ultima_consulta: str = ""             # ISO-8601 UTC
    ultimo_sucesso: str = ""
    ultimo_erro: str = ""                 # mensagem já higienizada
    falhas_consecutivas: int = 0
    documentos_recebidos: int = 0         # acumulado, para auditoria
    lotes_recebidos: int = 0
    criado_em: str = ""
    atualizado_em: str = ""
    versao: int = VERSAO_FORMATO
    observacoes: list[str] = field(default_factory=list)

    # ── ING 3B-R2: o que a SEFAZ diz, guardado SEPARADO do que temos ───────
    nsu_observado_sefaz: str = ""          # posição informada pelo Ambiente Nacional
    nsu_observado_em: str = ""             # quando ela foi informada
    nsu_observado_origem: str = ""         # o cStat que trouxe a informação
    sincronia_confirmada_em: str = ""      # quando uma resposta ATUAL provou 'em dia'
    estado_sincronismo: str = SINCRONISMO_SEM_INFORMACAO
    diagnostico: str = DIAGNOSTICO_NENHUM
    cobertura_anterior: str = COBERTURA_COMPLETA_DESDE_ZERO
    marco_cobertura: dict = field(default_factory=dict)   # INICIO_COBERTURA_DFE
    origem_ult_nsu: str = ORIGEM_INDEFINIDA
    migracao_legado: dict = field(default_factory=dict)
    # Estado de divergência reconstruído a partir de evidência histórica —
    # ver `migracao_r2.py`. Vazio quando o estado veio da consulta ao vivo.
    migracao_r2: dict = field(default_factory=dict)

    # ── consultas ──────────────────────────────────────────────────────────
    @property
    def chave(self) -> tuple[str, str, str]:
        return (self.identidade, self.servico, self.ambiente)

    @property
    def ha_divergencia_externa(self) -> bool:
        return self.estado_sincronismo == SINCRONISMO_DIVERGENCIA

    @property
    def distancia_para_sefaz(self) -> int:
        """Quantos NSU o Ambiente Nacional está à frente do nosso acervo.

        Zero quando não há informação — ausência de notícia não é notícia de
        que está tudo certo, e por isso o estado é consultado à parte."""
        if not self.nsu_observado_sefaz:
            return 0
        return max(0, nsu_int(self.nsu_observado_sefaz) - nsu_int(self.ult_nsu))

    @property
    def alerta_administrativo(self) -> str:
        """O texto que a tela mostra. Descreve o FATO, não acusa ninguém."""
        if not self.ha_divergencia_externa:
            return ""
        return (
            "O Ambiente Nacional informa uma posição de NSU superior ao "
            "histórico conhecido pelo FISCALE. Outro sistema pode estar "
            "consultando a Distribuição DF-e deste CNPJ."
        )

    # ── mutações que NÃO tocam o acervo ────────────────────────────────────
    def registrar_observacao_sefaz(self, ult_nsu_sefaz: str, cstat: str = "",
                                   agora: str = "") -> bool:
        """Guarda a posição informada pela SEFAZ. **Nunca mexe em `ult_nsu`.**

        Este método existe justamente para tornar impossível o atalho
        `checkpoint.ult_nsu = resposta.ultNSU`. Devolve `True` quando o valor
        observado está À FRENTE do que temos comprovado — que é a condição de
        divergência externa."""
        observado = normalizar_nsu(ult_nsu_sefaz)
        if nsu_int(observado) == 0:
            return False
        self.nsu_observado_sefaz = observado
        self.nsu_observado_em = agora or _agora()
        self.nsu_observado_origem = str(cstat or "")

        if nsu_int(observado) > nsu_int(self.ult_nsu):
            self.estado_sincronismo = SINCRONISMO_DIVERGENCIA
            # Enquanto houver divergência, o passado deixa de ser afirmável.
            if self.cobertura_anterior == COBERTURA_COMPLETA_DESDE_ZERO:
                self.cobertura_anterior = COBERTURA_DESCONHECIDA
            return True
        self.estado_sincronismo = SINCRONISMO_EM_DIA
        return False

    def registrar_sincronia_confirmada(self, ult_nsu_resposta: str,
                                       max_nsu_resposta: str,
                                       agora: str = "") -> bool:
        """Uma resposta ATUAL provou que estamos em dia. Grava `EM_SINCRONIA`.

        POR QUE ISTO EXISTE
            "O checkpoint não mudou" e "não sei em que pé estou" são coisas
            diferentes, e o sistema estava tratando as duas igual. Depois de um
            `137` que confirma `ultNSU == maxNSU`, o estado ficava
            `SEM_INFORMACAO` — como se nunca tivéssemos perguntado. Perguntamos,
            e a resposta foi "não há mais nada".

        AS TRÊS GUARDAS
            1. A resposta precisa fechar em si: `ultNSU == maxNSU`.
            2. **A nossa posição precisa ser essa posição.** Sem isto o sistema
               poderia declarar sincronia sobre um ponto que não alcançou —
               afirmar posse do que não tem, que é o erro que a R2 existe para
               impedir.
            3. Uma `DIVERGENCIA_EXTERNA` registrada **só é limpa** quando de
               fato a alcançamos (`ult_nsu >= nsu_observado_sefaz`). Ela nunca
               é apagada de lado, por um caminho que não a resolveu.

        Devolve `True` quando gravou. Quem chama só passa por aqui depois de
        confirmar que não houve erro, bloqueio nem rejeição.
        """
        ult = normalizar_nsu(ult_nsu_resposta) if ult_nsu_resposta else ""
        mx = normalizar_nsu(max_nsu_resposta) if max_nsu_resposta else ""
        if not ult or not mx:
            return False
        if nsu_int(ult) != nsu_int(mx):
            return False                      # guarda 1
        if nsu_int(self.ult_nsu) != nsu_int(ult):
            return False                      # guarda 2

        if (self.estado_sincronismo == SINCRONISMO_DIVERGENCIA
                and self.nsu_observado_sefaz
                and nsu_int(self.ult_nsu) < nsu_int(self.nsu_observado_sefaz)):
            return False                      # guarda 3

        self.estado_sincronismo = SINCRONISMO_EM_DIA
        self.sincronia_confirmada_em = agora or _agora()
        if (self.nsu_observado_sefaz
                and nsu_int(self.ult_nsu) >= nsu_int(self.nsu_observado_sefaz)):
            # Alcançamos o que a SEFAZ havia informado: a divergência acabou de
            # verdade, e o diagnóstico que a explicava deixa de valer.
            self.diagnostico = DIAGNOSTICO_NENHUM
        return True

    def marcar_possivel_consumidor_externo(self, motivo: str = "") -> None:
        """Hipótese, não conclusão. **Não diz qual sistema é** — só que os
        números indicam que alguém mais consome esta fila."""
        self.diagnostico = POSSIVEL_CONSUMIDOR_EXTERNO
        if motivo and motivo not in self.observacoes:
            self.observacoes.append(motivo)

    def marcar_inicio_cobertura(self, nsu_sefaz: str, cstat: str = "",
                                motivo: str = "", agora: str = "") -> dict:
        """Grava o marco `INICIO_COBERTURA_DFE`.

        A partir dele, o FISCALE passa a sincronizar da posição corrente **sem
        alegar que possui o passado anterior**. É o registro que impede alguém,
        daqui a dois anos, de olhar o acervo e concluir que não havia notas
        antes desta data — quando na verdade elas nunca foram baixadas."""
        ident = normalizar(self.identidade)
        marco = {
            "marco": "INICIO_COBERTURA_DFE",
            "empresa": ident.mascarado() if ident.valido else "<inválida>",
            "servico": self.servico,
            "ambiente": self.ambiente,
            "em": agora or _agora(),
            "checkpoint_local_anterior": self.ult_nsu,
            "nsu_informado_sefaz": normalizar_nsu(nsu_sefaz),
            "cstat_de_origem": str(cstat or ""),
            "motivo": motivo or ("sincronizar a partir da posição corrente "
                                 "informada pelo Ambiente Nacional"),
            "aviso": ("documentos anteriores a este marco podem NÃO existir no "
                      "acervo FISCALE; a cobertura do período anterior é "
                      "DESCONHECIDA e só pode ser preenchida por importação de "
                      "XML de outra origem"),
        }
        self.marco_cobertura = marco
        self.cobertura_anterior = COBERTURA_A_PARTIR_DE_MARCO
        return marco

    @property
    def nunca_rodou(self) -> bool:
        return self.status == NUNCA_EXECUTADO and nsu_int(self.ult_nsu) == 0

    @property
    def em_dia(self) -> bool:
        """Já leu tudo que o Ambiente Nacional tem para esta empresa?"""
        if not self.max_nsu:
            return False
        return nsu_int(self.ult_nsu) >= nsu_int(self.max_nsu)

    @property
    def pendentes(self) -> int:
        """Quantos NSU faltam, quando o serviço informou o máximo."""
        if not self.max_nsu:
            return 0
        return max(0, nsu_int(self.max_nsu) - nsu_int(self.ult_nsu))

    def resumo(self) -> dict:
        """Para tela e log. Identidade mascarada; não há segredo aqui."""
        ident = normalizar(self.identidade)
        return {
            "identidade": ident.mascarado() if ident.valido else "<inválida>",
            "servico": self.servico,
            "ambiente": self.ambiente,
            "ult_nsu": self.ult_nsu,
            "max_nsu": self.max_nsu or None,
            "pendentes": self.pendentes,
            "status": self.status,
            "em_dia": self.em_dia,
            "falhas_consecutivas": self.falhas_consecutivas,
            "ultima_consulta": self.ultima_consulta,
            "ultimo_sucesso": self.ultimo_sucesso,
            "documentos_recebidos": self.documentos_recebidos,
            # ING 3B-R2 — local × observado, sempre lado a lado para que
            # ninguém leia um pensando que é o outro.
            "checkpoint_acervo": self.ult_nsu,
            "checkpoint_sefaz_observado": self.nsu_observado_sefaz or None,
            "estado_sincronismo": self.estado_sincronismo,
            "sincronia_confirmada_em": self.sincronia_confirmada_em or None,
            "diagnostico": self.diagnostico or None,
            "distancia_para_sefaz": self.distancia_para_sefaz,
            "cobertura_anterior": self.cobertura_anterior,
            "marco_cobertura": self.marco_cobertura or None,
            "alerta": self.alerta_administrativo or None,
            "origem_ult_nsu": self.origem_ult_nsu or None,
            "migracao_legado": self.migracao_legado or None,
            "migracao_r2": self.migracao_r2 or None,
        }

    def para_json(self) -> dict:
        return asdict(self)

    @classmethod
    def de_json(cls, d: dict) -> "Checkpoint":
        if not isinstance(d, dict):
            raise CheckpointCorrompido("conteúdo não é um objeto JSON")
        faltando = [c for c in ("identidade", "servico", "ambiente") if not d.get(c)]
        if faltando:
            raise CheckpointCorrompido(f"campos obrigatórios ausentes: {faltando}")
        conhecidos = {f for f in cls.__dataclass_fields__}
        # `copy` nos mutáveis: sem isto o Checkpoint compartilha a MESMA lista
        # (e o mesmo dict) do JSON de origem, e qualquer `observacoes.append()`
        # depois altera o dicionário de quem chamou, à distância. Descoberto ao
        # simular o estado da MONTE: o dado "em disco" mudava sozinho na
        # memória do programa que só queria comparar antes e depois.
        limpo = {k: (list(v) if isinstance(v, list) else
                     dict(v) if isinstance(v, dict) else v)
                 for k, v in d.items() if k in conhecidos}
        cp = cls(**limpo)
        cp.ult_nsu = normalizar_nsu(cp.ult_nsu)
        cp.max_nsu = normalizar_nsu(cp.max_nsu) if cp.max_nsu else ""
        return cp


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── o repositório ───────────────────────────────────────────────────────────
class RepositorioCheckpoint:
    """Lê e grava checkpoints. Recebe a raiz por parâmetro (raiz portátil e
    isolamento de teste vêm de graça)."""

    def __init__(self, dados_dir):
        self.dados_dir = Path(dados_dir)

    # ── caminhos ───────────────────────────────────────────────────────────
    def pasta(self, identidade, criar: bool = False) -> Path:
        ident = normalizar(identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida: {identidade!r} ({ident.motivo})")
        p = self.dados_dir / ident.valor / PASTA
        if criar:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def caminho(self, identidade, servico: str, ambiente, criar: bool = False) -> Path:
        amb = resolver_ambiente(ambiente)
        srv = validar_servico(servico)
        return self.pasta(identidade, criar) / f"{srv}.{amb.nome}.json"

    # ── leitura ────────────────────────────────────────────────────────────
    def existe(self, identidade, servico: str, ambiente) -> bool:
        return self.caminho(identidade, servico, ambiente).is_file()

    def carregar(self, identidade, servico: str, ambiente) -> Checkpoint:
        """Devolve o checkpoint. Se não existir, devolve um NOVO zerado.

        Se existir e estiver corrompido, **levanta** `CheckpointCorrompido` —
        não devolve zerado. Ver `recuperar()` para o caminho de decisão.
        """
        amb = resolver_ambiente(ambiente)
        srv = validar_servico(servico)
        ident = normalizar(identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida: {identidade!r}")
        alvo = self.caminho(ident.valor, srv, amb)

        if not alvo.is_file():
            agora = _agora()
            return Checkpoint(identidade=ident.valor, servico=srv, ambiente=amb.nome,
                              criado_em=agora, atualizado_em=agora)

        try:
            bruto = alvo.read_text("utf-8-sig")
        except OSError as exc:
            raise CheckpointCorrompido(
                f"não foi possível ler o checkpoint ({type(exc).__name__})", alvo) from None
        if not bruto.strip():
            raise CheckpointCorrompido("checkpoint vazio (escrita interrompida?)", alvo)
        try:
            d = json.loads(bruto)
        except json.JSONDecodeError as exc:
            raise CheckpointCorrompido(f"JSON inválido: {exc.msg} (linha {exc.lineno})", alvo)

        cp = Checkpoint.de_json(d)

        # A chave gravada tem de bater com a pedida. Se não bate, o arquivo foi
        # parar no lugar errado — misturar produção com homologação, ou empresa
        # com empresa, é pior do que não ter checkpoint.
        if (cp.identidade, cp.servico, cp.ambiente) != (ident.valor, srv, amb.nome):
            raise CheckpointCorrompido(
                f"o conteúdo pertence a outra chave: gravado "
                f"({cp.servico}/{cp.ambiente}) e pedido ({srv}/{amb.nome})", alvo)
        if cp.versao > VERSAO_FORMATO:
            raise CheckpointCorrompido(
                f"formato versão {cp.versao}, mais novo que o suportado ({VERSAO_FORMATO})", alvo)
        return cp

    # ── escrita atômica ────────────────────────────────────────────────────
    def salvar(self, cp: Checkpoint) -> Path:
        """Grava de forma resistente a queda no meio.

        `tempfile` na MESMA pasta (para `os.replace` ser atômico — entre volumes
        diferentes não é), `flush` + `fsync` para o dado chegar ao disco, e só
        então `os.replace`, que no Windows e no POSIX troca o arquivo de uma vez.
        Se o processo morrer antes do replace, o checkpoint anterior continua
        inteiro; o que se perde é a última consulta, e repeti-la é barato.
        """
        amb = resolver_ambiente(cp.ambiente)
        srv = validar_servico(cp.servico)
        ident = normalizar(cp.identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida: {cp.identidade!r}")

        cp.identidade, cp.servico, cp.ambiente = ident.valor, srv, amb.nome
        cp.ult_nsu = normalizar_nsu(cp.ult_nsu)
        cp.max_nsu = normalizar_nsu(cp.max_nsu) if cp.max_nsu else ""
        cp.versao = VERSAO_FORMATO
        cp.atualizado_em = _agora()
        if not cp.criado_em:
            cp.criado_em = cp.atualizado_em

        alvo = self.caminho(ident.valor, srv, amb, criar=True)
        texto = json.dumps(cp.para_json(), ensure_ascii=False, indent=2)

        fd, tmp = tempfile.mkstemp(dir=str(alvo.parent), prefix=".cp-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(texto)
                fh.flush()
                os.fsync(fh.fileno())        # o dado precisa estar NO DISCO
            os.replace(tmp, alvo)            # troca atômica
            tmp = None
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)               # nunca deixa lixo se falhou
        return alvo

    # ── corrupção: evidência preservada, decisão do usuário ────────────────
    def isolar_corrompido(self, identidade, servico: str, ambiente) -> Path | None:
        """Move o arquivo ilegível para `.corrompido-<carimbo>` e **não** cria
        substituto. Preserva a evidência e obriga uma decisão explícita.

        Deliberadamente NÃO devolve um checkpoint zerado: para o CT-e isso
        equivaleria a descartar o histórico anterior a três meses sem avisar.
        """
        alvo = self.caminho(identidade, servico, ambiente)
        if not alvo.is_file():
            return None
        carimbo = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        destino = alvo.with_suffix(f".json.corrompido-{carimbo}")
        os.replace(alvo, destino)
        return destino

    # ── varredura ──────────────────────────────────────────────────────────
    def listar(self, identidade=None) -> list[Checkpoint]:
        """Todos os checkpoints (de uma empresa, ou de todas).

        Arquivo ilegível não derruba a listagem: vira um checkpoint com status
        CORROMPIDO, para a tela poder mostrar o problema em vez de sumir com a
        empresa."""
        out: list[Checkpoint] = []
        if identidade is not None:
            pastas = [self.pasta(identidade)]
        else:
            pastas = [p / PASTA for p in self.dados_dir.iterdir()
                      if p.is_dir() and (p / PASTA).is_dir()] if self.dados_dir.is_dir() else []
        for pasta in pastas:
            if not pasta.is_dir():
                continue
            for arq in sorted(pasta.glob("*.json")):
                nome = arq.stem
                if "." not in nome:
                    continue
                srv, _, ambn = nome.rpartition(".")
                try:
                    out.append(self.carregar(pasta.parent.name, srv, ambn))
                except (CheckpointCorrompido, ValueError) as exc:
                    out.append(Checkpoint(
                        identidade=pasta.parent.name, servico=srv.upper(),
                        ambiente=ambn, status=CORROMPIDO, ultimo_erro=str(exc)[:200]))
        return out
