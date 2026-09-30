"""
auditoria.py — trilha append-only. Responde "de onde veio isto?".

FORMATO: JSONL, um arquivo por empresa e mês de captura

    <raiz>/<identidade>/auditoria/<AAAA-MM>.jsonl

POR QUE JSONL E NÃO SQLITE
    Três motivos, nesta ordem:

    1. **Sobrevive a truncamento.** Se a máquina desligar no meio de uma
       escrita, perde-se a última linha — não a trilha. Um SQLite interrompido
       na hora errada pode ficar ilegível inteiro.
    2. **É legível sem ferramenta.** Auditoria que precisa de programa para ser
       lida é auditoria que ninguém confere.
    3. **Append é a operação mais barata e mais atômica que o sistema de
       arquivos oferece.** Não há leitura-modificação-escrita para dar errado.

    O índice (SQLite) é o oposto: consultável e descartável. São papéis
    diferentes, e é por isso que existem os dois.

O QUE NUNCA ENTRA AQUI
    XML completo, conteúdo de documento, senha, token, certificado, CPF sem
    máscara. Tudo passa por `_limpar()`, e a identidade da empresa é gravada
    **mascarada**. A trilha diz o que aconteceu com qual documento — não repete
    o documento.

APPEND-ONLY DE VERDADE
    Não existe função de apagar nem de editar neste módulo. Corrigir um registro
    errado se faz acrescentando outro que o corrige, nunca reescrevendo — é o
    que separa trilha de auditoria de arquivo de log comum.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .distribuicao import higienizar
from .identidade import normalizar

# ── atos ────────────────────────────────────────────────────────────────────
CONSULTA = "CONSULTA"                   # TENTATIVA de aquisição — ver §CONSULTA
# Consulta que ACONTECEU antes de o ato `CONSULTA` existir, reconstruída depois
# a partir de evidência persistida. Ato SEPARADO de propósito: misturá-la com as
# de verdade faria a trilha afirmar uma precisão de horário e duração que não
# existe. Quem lê precisa poder distinguir registro de reconstrução.
CONSULTA_RECONSTRUIDA = "CONSULTA_RECONSTRUIDA"
CAPTURA = "CAPTURA"                     # documento chegou da fonte
PRESERVACAO = "PRESERVACAO"             # gravado no acervo, com hash
DEDUPLICACAO = "DEDUPLICACAO"           # já tínhamos, conteúdo idêntico
COPIA_NOVA = "COPIA_NOVA"               # mesma chave, cópia mais completa
COLISAO = "COLISAO"                     # mesma chave, conteúdo incompatível
SCHEMA_DESCONHECIDO = "SCHEMA_DESCONHECIDO"
PARSE = "PARSE"                         # parser leu (ou tentou)
NORMALIZACAO = "NORMALIZACAO"
INDEXACAO = "INDEXACAO"
REPROCESSAMENTO = "REPROCESSAMENTO"
# Reidentificação de documento já preservado: a fórmula do `id_documento` mudou
# e a pasta foi renomeada, ou uma falsa colisão foi separada em dois documentos.
# Ato próprio porque não é captura nem parse — é o acervo se corrigindo, e quem
# auditar precisa distinguir "chegou agora" de "sempre esteve aqui, com outro
# nome".
MIGRACAO_IDENTIDADE = "MIGRACAO_IDENTIDADE_EVENTO"
# Decisão HUMANA sobre o estado da ingestão — encerrar revisão de sequência,
# reabri-la, marcar início de cobertura. Ato próprio porque não é aquisição
# nem processamento: é alguém assumindo uma escolha, com nome e justificativa.
# Fica na trilha da empresa, ao lado da evidência que a motivou, para que daqui
# a dois anos a pergunta "por que esta empresa voltou à automação?" tenha
# resposta no mesmo arquivo em que está o `656` que a tirou dela.
DECISAO_ADMINISTRATIVA = "DECISAO_ADMINISTRATIVA"
ERRO = "ERRO"

ATOS = (CONSULTA, CONSULTA_RECONSTRUIDA, CAPTURA, PRESERVACAO, DEDUPLICACAO, COPIA_NOVA, COLISAO,
        SCHEMA_DESCONHECIDO, PARSE, NORMALIZACAO, INDEXACAO,
        REPROCESSAMENTO, MIGRACAO_IDENTIDADE, DECISAO_ADMINISTRATIVA, ERRO)

# Campos que uma linha `CONSULTA` deve trazer. Existe como constante para que o
# teste possa exigir que nenhum se perca numa refatoração.
CAMPOS_CONSULTA = (
    "inicio", "fim", "empresa", "servico", "ambiente", "tipo_consulta",
    "nsu_enviado", "endpoint", "transporte_ok", "cstat", "xmotivo",
    "ult_nsu", "max_nsu", "doczip", "resultado",
)

PASTA = "auditoria"

# Chaves cujo valor nunca é gravado, mesmo que alguém as passe em `detalhe`.
_PROIBIDAS = {"conteudo", "xml", "senha", "password", "token", "secret",
              "certificado", "pfx", "chave_privada", "authorization"}

_LIMITE_TEXTO = 300


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _limpar(valor, _nivel: int = 0):
    """Deixa passar só o que pode ser gravado.

    `bytes` some sempre: se um dia alguém passar o XML por engano em `detalhe`,
    ele não chega ao disco."""
    if _nivel > 4:
        return "<profundo demais>"
    if isinstance(valor, bytes):
        return f"<{len(valor)} bytes omitidos>"
    if isinstance(valor, dict):
        out = {}
        for k, v in valor.items():
            if str(k).lower() in _PROIBIDAS:
                out[str(k)] = "<omitido>"
            else:
                out[str(k)] = _limpar(v, _nivel + 1)
        return out
    if isinstance(valor, (list, tuple)):
        return [_limpar(v, _nivel + 1) for v in valor[:50]]
    if isinstance(valor, str):
        s = higienizar(valor)
        return s[:_LIMITE_TEXTO]
    if isinstance(valor, (int, float, bool)) or valor is None:
        return valor
    return higienizar(str(valor))


@dataclass
class Trilha:
    """A trilha de UMA empresa. Escreve; não apaga e não edita."""
    dados_dir: Path
    identidade: str
    mascarada: str = ""

    def __post_init__(self):
        self.dados_dir = Path(self.dados_dir)
        ident = normalizar(self.identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida para trilha: {ident.motivo}")
        self.identidade = ident.valor
        self.mascarada = ident.mascarado()

    # ------------------------------------------------------------------
    def pasta(self, criar: bool = False) -> Path:
        p = self.dados_dir / self.identidade / PASTA
        if criar:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def caminho(self, quando: datetime | None = None, criar: bool = False) -> Path:
        q = quando or datetime.now(timezone.utc).astimezone()
        return self.pasta(criar=criar) / f"{q:%Y-%m}.jsonl"

    # ------------------------------------------------------------------
    def registrar(self, ato: str, **campos) -> dict:
        """Acrescenta uma linha. Devolve o registro gravado.

        Nunca levanta por causa do disco: perder a trilha é ruim, mas
        interromper a ingestão porque a trilha falhou é pior — o documento
        fiscal é o ativo, a trilha é o relato. A falha vira `ERRO` na próxima
        linha que conseguir ser gravada.
        """
        reg = {"em": _agora(), "ato": str(ato), "empresa": self.mascarada}
        for k, v in campos.items():
            if v is None:
                continue
            reg[k] = _limpar(v)

        linha = json.dumps(reg, ensure_ascii=False, sort_keys=False)
        try:
            caminho = self.caminho(criar=True)
            # "a" + uma única escrita: no POSIX e no Windows, escrita curta em
            # modo append não se intercala com a de outro processo.
            with open(caminho, "a", encoding="utf-8", newline="\n") as f:
                if self._termina_sem_quebra(caminho):
                    # Uma queda no meio da escrita anterior deixa a última linha
                    # truncada e SEM `\n`. Sem esta cura, o próximo registro
                    # gruda nela e os DOIS viram uma linha ilegível — o defeito
                    # se propagaria para frente em vez de ficar contido.
                    f.write("\n")
                f.write(linha + "\n")
                f.flush()
                os.fsync(f.fileno())     # a trilha precisa sobreviver a queda
        except OSError:
            pass
        return reg

    @staticmethod
    def _termina_sem_quebra(caminho: Path) -> bool:
        """O arquivo existe, tem conteúdo e não termina em `\\n`?"""
        try:
            tam = caminho.stat().st_size
            if tam == 0:
                return False
            with open(caminho, "rb") as f:
                f.seek(-1, os.SEEK_END)
                return f.read(1) != b"\n"
        except OSError:
            return False

    # ── §CONSULTA — a tentativa de aquisição, com ou sem documento ────────
    def registrar_consulta(self, tentativa: dict) -> dict:
        """Registra UMA tentativa de aquisição. Documento é opcional.

        POR QUE ISTO EXISTE
            A consulta real de 13/08/2026 foi rejeitada com `cStat 656` e não
            deixou linha nenhuma na trilha — porque nenhum documento chegou, e
            até então só documento gerava registro. Para auditoria, "consultamos
            às 21:26 e fomos rejeitados" é um fato tão relevante quanto "baixamos
            50 notas": é o que responde *por que* falta documento num período.

            Vale para 137, 138, 656, timeout, TLS, SOAP inválido e falha HTTP —
            todos deixam linha, porque em todos eles houve uma tentativa.

        A `tentativa` é DADO PURO, produzido pela Fonte sem tocar disco. Quem
        persiste é esta função. É o que permite ao conector registrar tudo isso
        sem importar o módulo de auditoria.
        """
        campos = {c: tentativa.get(c) for c in CAMPOS_CONSULTA}
        for extra in ("erro_tecnico", "erro_classe", "avarias",
                      "divergencia_externa", "nsu_observado_sefaz",
                      "duracao_s"):
            if tentativa.get(extra) is not None:
                campos[extra] = tentativa[extra]
        return self.registrar(CONSULTA, **campos)

    def consultas(self, mes: str = "", incluir_reconstruidas: bool = False
                  ) -> list[dict]:
        """Só as tentativas de aquisição, em ordem.

        As reconstruídas ficam de FORA por padrão: quem conta chamadas reais
        não deve somar uma reconstrução. Quem quer o histórico completo pede."""
        alvos = ({CONSULTA, CONSULTA_RECONSTRUIDA} if incluir_reconstruidas
                 else {CONSULTA})
        return [r for r in self.ler(mes) if r.get("ato") in alvos]

    # ------------------------------------------------------------------
    def ler(self, mes: str = "") -> list[dict]:
        """Lê a trilha. Linha corrompida é PULADA, não derruba a leitura.

        Uma linha truncada por queda de energia não pode impedir a auditoria de
        ler as outras mil que estão íntegras."""
        arquivos = ([self.pasta() / f"{mes}.jsonl"] if mes
                    else sorted(self.pasta().glob("*.jsonl")))
        out: list[dict] = []
        for arq in arquivos:
            if not arq.exists():
                continue
            for linha in arq.read_text("utf-8", "ignore").splitlines():
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    reg = json.loads(linha)
                except (ValueError, TypeError):
                    out.append({"ato": ERRO, "em": "", "empresa": self.mascarada,
                                "detalhe": {"linha_ilegivel": True,
                                            "arquivo": arq.name}})
                    continue
                if isinstance(reg, dict):
                    out.append(reg)
        return out

    def contar(self, mes: str = "") -> dict[str, int]:
        """Quantas vezes cada ato aconteceu. É o resumo que a tela mostra."""
        contagem: dict[str, int] = {}
        for reg in self.ler(mes):
            ato = str(reg.get("ato") or "?")
            contagem[ato] = contagem.get(ato, 0) + 1
        return contagem

    def historico(self, id_documento: str, mes: str = "") -> list[dict]:
        """Tudo que aconteceu com um documento, em ordem."""
        alvo = str(id_documento or "")
        return [r for r in self.ler(mes) if r.get("id_documento") == alvo]


def abrir(dados_dir, identidade) -> Trilha:
    return Trilha(dados_dir=Path(dados_dir), identidade=str(identidade))
