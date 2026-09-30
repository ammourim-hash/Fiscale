"""
acervo.py — onde o documento original é preservado, e nunca alterado.

A PROMESSA
    O que a fonte entregou é gravado byte a byte e **jamais reescrito**. Parser
    novo, regra nova, correção de bug: tudo isso produz dado derivado em outro
    lugar. O `original.xml` de hoje precisa ser idêntico ao de daqui a cinco
    anos, porque é ele que vale numa fiscalização — não a nossa interpretação
    dele.

LAYOUT

    <raiz>/<identidade>/acervo/<especie>/<id[:2]>/<id_documento>/
        original.xml        os bytes exatos da PRIMEIRA cópia recebida
        captura.json        proveniência: fonte, NSU, hash, quando, cópias
        copias/<hash8>.xml  cópias posteriores com conteúdo diferente

    **Por que o caminho não tem competência (AAAA-MM).** O desenho da ING 3
    previa pasta por competência. Implementando, apareceu o furo: a competência
    só existe DEPOIS do parser, e o acervo grava ANTES — inclusive documento
    malformado e schema desconhecido, que não têm data nenhuma. Um caminho que
    depende do parser faria o documento mudar de lugar quando o parser
    evoluísse, quebrando a imutabilidade que este módulo existe para garantir.

    O acervo é endereçado por conteúdo; **quem responde por período é o índice**,
    que é reconstruível. Os dois primeiros caracteres do id espalham as pastas
    para não juntar dezenas de milhares de entradas num diretório só.

DEDUPLICAÇÃO E COLISÃO — a distinção que evita perder nota
    Mesma chave, mesmo hash        → duplicata pura. Idempotente, nada muda.
    Mesma chave, cópia mais completa (resumo → autorizado)
                                   → cópia legítima. Guarda ao lado e PROMOVE
                                     o canônico. O original nunca é tocado.
    Mesma chave, mesma prioridade, conteúdo diferente
                                   → COLISÃO. Guarda ao lado, marca quarentena
                                     e **não decide sozinho**.

    Sobrescrever no terceiro caso seria apagar um documento fiscal com base num
    palpite. Guardar os dois e pedir conferência humana é a única saída honesta.

ISOLAMENTO ENTRE EMPRESAS
    Todo caminho nasce de `identidade`, validada e canônica. Não existe função
    aqui que escreva sem passar por ela — um erro de código que perca o CNPJ não
    tem onde gravar, em vez de gravar no lugar errado.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import auditoria as aud
from . import identificacao as idf
from .ambiente import Ambiente, resolver as resolver_ambiente
from .distribuicao import DocumentoBruto, higienizar
from .identidade import normalizar

PASTA = "acervo"
ORIGINAL = "original.xml"
CAPTURA = "captura.json"
COPIAS = "copias"

# ── o que aconteceu ao preservar ────────────────────────────────────────────
NOVO = "NOVO"                   # primeira vez que este documento aparece
DUPLICATA = "DUPLICATA"         # bytes idênticos aos que já tínhamos
COPIA_PROMOVIDA = "COPIA_PROMOVIDA"   # cópia mais completa da mesma chave
COPIA_MENOR = "COPIA_MENOR"     # cópia menos completa: guardada, não promovida
COLISAO = "COLISAO"             # mesma identidade, conteúdo incompatível


class ErroAcervo(Exception):
    """Não deu para preservar. O checkpoint NÃO pode avançar."""


@dataclass(frozen=True)
class Preservacao:
    """O que o acervo fez com um documento."""
    resultado: str
    id_documento: str
    pasta: Path
    identificacao: idf.Identificacao
    quarentena: bool = False
    gravou_bytes: bool = False
    detalhe: str = ""

    @property
    def novo(self) -> bool:
        return self.resultado == NOVO


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _escrever_atomico(destino: Path, dados: bytes) -> None:
    """Grava por arquivo temporário + `os.replace`.

    Sem isso, uma queda no meio da escrita deixa um `original.xml` truncado —
    que é pior do que nenhum arquivo, porque parece válido."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(destino.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dados)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destino)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class AcervoArquivos:
    """Implementa o protocolo `Acervo` que o `DistribuicaoRunner` exige.

    Uma instância por (empresa, serviço, ambiente) — o mesmo recorte do
    checkpoint, pelo mesmo motivo: não existe operação global."""
    dados_dir: Path
    identidade: str
    servico: str = ""
    ambiente: Ambiente | str = ""
    trilha: aud.Trilha | None = None
    mascarada: str = field(default="", init=False)

    def __post_init__(self):
        self.dados_dir = Path(self.dados_dir)
        ident = normalizar(self.identidade)
        if not ident.valido:
            raise ValueError(f"identidade fiscal inválida para acervo: {ident.motivo}")
        self.identidade = ident.valor
        self.mascarada = ident.mascarado()
        self.ambiente = resolver_ambiente(self.ambiente) if self.ambiente else None
        if self.trilha is None:
            self.trilha = aud.abrir(self.dados_dir, self.identidade)

    # ── caminhos ──────────────────────────────────────────────────────────
    def raiz(self, criar: bool = False) -> Path:
        p = self.dados_dir / self.identidade / PASTA
        if criar:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def pasta_do(self, especie: str, id_documento: str, criar: bool = False) -> Path:
        if not id_documento:
            raise ValueError("id_documento vazio")
        p = self.raiz() / especie / id_documento[:2] / id_documento
        if criar:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def existe(self, especie: str, id_documento: str) -> bool:
        return (self.pasta_do(especie, id_documento) / ORIGINAL).exists()

    # ── leitura ───────────────────────────────────────────────────────────
    def ler_captura(self, especie: str, id_documento: str) -> dict:
        p = self.pasta_do(especie, id_documento) / CAPTURA
        if not p.exists():
            return {}
        try:
            d = json.loads(p.read_text("utf-8-sig"))
            return d if isinstance(d, dict) else {}
        except (ValueError, OSError):
            # Metadado ilegível não invalida o documento: o `original.xml`
            # continua lá, e a captura pode ser reconstruída a partir dele.
            return {}

    def ler_original(self, especie: str, id_documento: str) -> bytes:
        return (self.pasta_do(especie, id_documento) / ORIGINAL).read_bytes()

    def caminho_canonico(self, especie: str, id_documento: str) -> Path:
        """O arquivo que representa o documento hoje.

        Pode não ser o `original.xml`: se depois chegou uma cópia mais completa
        (resumo → autorizado), o canônico é ela. O original continua guardado."""
        pasta = self.pasta_do(especie, id_documento)
        nome = (self.ler_captura(especie, id_documento).get("canonico") or ORIGINAL)
        alvo = pasta / nome
        return alvo if alvo.exists() else pasta / ORIGINAL

    def listar(self, especie: str = "") -> list[tuple[str, str]]:
        """`(especie, id_documento)` de tudo que está no acervo desta empresa."""
        raiz = self.raiz()
        if not raiz.exists():
            return []
        especies = [especie] if especie else sorted(
            p.name for p in raiz.iterdir() if p.is_dir())
        out: list[tuple[str, str]] = []
        for esp in especies:
            base = raiz / esp
            if not base.is_dir():
                continue
            for shard in sorted(base.iterdir()):
                if not shard.is_dir():
                    continue
                for pasta in sorted(shard.iterdir()):
                    if (pasta / ORIGINAL).exists():
                        out.append((esp, pasta.name))
        return out

    # ── o protocolo Acervo ────────────────────────────────────────────────
    def preservar(self, doc: DocumentoBruto) -> Preservacao:
        """Grava o documento. Levanta `ErroAcervo` se não conseguir.

        Levantar é o comportamento certo: o `DistribuicaoRunner` trata qualquer
        exceção daqui como falha de persistência e **não avança o checkpoint**.
        Documento não gravado precisa vir de novo."""
        ident = idf.identificar(doc.conteudo, chave_informada=doc.chave,
                                schema=doc.schema)
        self.trilha.registrar(
            aud.CAPTURA, id_documento=ident.id_documento,
            chave=ident.chave or None, especie=ident.especie,
            servico=self.servico or None,
            ambiente=getattr(self.ambiente, "nome", None),
            nsu=doc.nsu or None, hash=ident.hash_conteudo,
            schema=doc.schema or None)

        if not ident.reconhecido:
            # NUNCA descartar. O NSU não volta, e o serviço retém por pouco tempo.
            self.trilha.registrar(
                aud.SCHEMA_DESCONHECIDO, id_documento=ident.id_documento,
                hash=ident.hash_conteudo, schema=doc.schema or None,
                raiz=ident.raiz or None, motivo=ident.motivo)

        try:
            return self._gravar(doc, ident)
        except ErroAcervo:
            raise
        except Exception as exc:
            self.trilha.registrar(aud.ERRO, id_documento=ident.id_documento,
                                  etapa="preservacao", motivo=higienizar(exc))
            raise ErroAcervo(f"não consegui preservar: {higienizar(exc)}") from exc

    def confirmar(self, documentos) -> bool:
        """Os documentos estão duráveis em disco?

        Confere de verdade — abre o arquivo e compara o hash. Devolver `True`
        sem conferir é o único jeito de este motor perder documento, e a
        conferência custa uma leitura por documento."""
        for doc in documentos or ():
            ident = idf.identificar(doc.conteudo, chave_informada=doc.chave,
                                    schema=doc.schema)
            pasta = self.pasta_do(ident.especie, ident.id_documento)
            if not (pasta / ORIGINAL).exists():
                return False
            cap = self.ler_captura(ident.especie, ident.id_documento)
            hashes = {cap.get("hash_conteudo")}
            hashes.update(c.get("hash") for c in (cap.get("copias") or []))
            if ident.hash_conteudo not in hashes:
                return False
        return True

    def simular(self, ident: idf.Identificacao) -> str:
        """O que `preservar()` FARIA com este documento, sem escrever nada.

        Existe para o `--dry-run` da migração dos XML legados poder dizer
        "este arquivo migraria / já existe / promoveria o resumo / colidiria"
        **antes** de a primeira escrita acontecer.

        Espelha, na mesma ordem, as decisões de `_gravar()`: existe original?
        o hash já é conhecido? a identidade é forte? a prioridade sobe, desce
        ou empata? Um ensaio que decidisse por outro caminho não valeria como
        ensaio — por isso a comparação é feita sobre os mesmos campos, e
        qualquer mudança em `_gravar` que não seja refletida aqui aparece como
        divergência entre o ensaio e a execução real.

        Não cria pasta, não abre arquivo para escrita e não registra trilha.
        """
        pasta = self.pasta_do(ident.especie, ident.id_documento)
        if not (pasta / ORIGINAL).exists():
            return NOVO

        cap = self.ler_captura(ident.especie, ident.id_documento)
        conhecidos = {c.get("hash") for c in (cap.get("copias") or [])}
        conhecidos.add(cap.get("hash_conteudo"))
        if ident.hash_conteudo in conhecidos:
            return DUPLICATA

        if not ident.identidade_forte:
            return COLISAO

        prio_atual = int(cap.get("prioridade") or idf.PRIO_RESUMO)
        if ident.prioridade > prio_atual:
            return COPIA_PROMOVIDA
        if ident.prioridade < prio_atual:
            return COPIA_MENOR
        return COLISAO

    # ── o miolo ───────────────────────────────────────────────────────────
    def _gravar(self, doc: DocumentoBruto, ident: idf.Identificacao) -> Preservacao:
        pasta = self.pasta_do(ident.especie, ident.id_documento)
        original = pasta / ORIGINAL

        registro_copia = {
            "arquivo": ORIGINAL, "hash": ident.hash_conteudo,
            "prioridade": ident.prioridade, "rotulo": ident.rotulo_prioridade,
            "fonte": self.servico or "", "nsu": doc.nsu or "",
            "schema": doc.schema or "", "capturado_em": _agora(),
        }

        # ---- primeira vez ------------------------------------------------
        if not original.exists():
            _escrever_atomico(original, doc.conteudo)
            cap = {
                "id_documento": ident.id_documento,
                "especie": ident.especie,
                "chave": ident.chave,
                "identidade_forte": ident.identidade_forte,
                "identidade_empresa": self.identidade,
                "hash_conteudo": ident.hash_conteudo,
                "tamanho_bytes": len(doc.conteudo or b""),
                "canonico": ORIGINAL,
                "prioridade": ident.prioridade,
                "fonte": self.servico or "",
                "ambiente": getattr(self.ambiente, "nome", ""),
                "nsu": doc.nsu or "",
                "schema": doc.schema or "",
                "raiz": ident.raiz,
                "reconhecido": ident.reconhecido,
                "motivo": ident.motivo,
                "avisos": list(ident.avisos),
                "capturado_em": registro_copia["capturado_em"],
                "quarentena": False,
                "copias": [registro_copia],
            }
            if ident.e_evento:
                cap["tp_evento"] = ident.tp_evento
                cap["seq_evento"] = ident.seq_evento
            self._salvar_captura(pasta, cap)
            self.trilha.registrar(aud.PRESERVACAO, id_documento=ident.id_documento,
                                  chave=ident.chave or None, especie=ident.especie,
                                  hash=ident.hash_conteudo, resultado=NOVO,
                                  bytes=len(doc.conteudo or b""))
            return Preservacao(NOVO, ident.id_documento, pasta, ident,
                               gravou_bytes=True)

        # ---- já existe: decidir sem nunca sobrescrever --------------------
        cap = self.ler_captura(ident.especie, ident.id_documento)
        copias = list(cap.get("copias") or [])
        conhecidos = {c.get("hash") for c in copias} | {cap.get("hash_conteudo")}

        if ident.hash_conteudo in conhecidos:
            # Duplicata pura. Idempotente: nada é escrito, nada muda.
            self.trilha.registrar(aud.DEDUPLICACAO, id_documento=ident.id_documento,
                                  chave=ident.chave or None, hash=ident.hash_conteudo,
                                  nsu=doc.nsu or None,
                                  motivo="conteúdo idêntico ao já preservado")
            return Preservacao(DUPLICATA, ident.id_documento, pasta, ident,
                               quarentena=bool(cap.get("quarentena")),
                               detalhe="conteúdo idêntico")

        # Conteúdo diferente para a mesma identidade.
        prio_atual = int(cap.get("prioridade") or idf.PRIO_RESUMO)
        nome_copia = f"{ident.hash_conteudo.split(':')[-1][:16]}.xml"
        destino = pasta / COPIAS / nome_copia
        _escrever_atomico(destino, doc.conteudo)
        registro_copia["arquivo"] = f"{COPIAS}/{nome_copia}"
        copias.append(registro_copia)
        cap["copias"] = copias

        if not ident.identidade_forte:
            # Identidade fraca nunca deduplica nem promove: dois documentos
            # distintos podem ter caído no mesmo balde do hash da espécie.
            cap["quarentena"] = True
            cap["motivo_quarentena"] = "identidade fraca com conteúdo divergente"
            self._salvar_captura(pasta, cap)
            self.trilha.registrar(aud.COLISAO, id_documento=ident.id_documento,
                                  hash=ident.hash_conteudo, nsu=doc.nsu or None,
                                  motivo=cap["motivo_quarentena"])
            return Preservacao(COLISAO, ident.id_documento, pasta, ident,
                               quarentena=True, gravou_bytes=True,
                               detalhe=cap["motivo_quarentena"])

        if ident.prioridade > prio_atual:
            # Cópia mais completa: resumo → completo → autorizado. É o caminho
            # normal da Distribuição DF-e, não uma divergência.
            cap["canonico"] = registro_copia["arquivo"]
            cap["prioridade"] = ident.prioridade
            cap["promovido_em"] = _agora()
            self._salvar_captura(pasta, cap)
            self.trilha.registrar(
                aud.COPIA_NOVA, id_documento=ident.id_documento,
                chave=ident.chave or None, hash=ident.hash_conteudo,
                nsu=doc.nsu or None, resultado=COPIA_PROMOVIDA,
                motivo=f"cópia {ident.rotulo_prioridade} substitui "
                       f"{idf._ROTULO_PRIO.get(prio_atual, prio_atual)} como canônica")
            return Preservacao(COPIA_PROMOVIDA, ident.id_documento, pasta, ident,
                               gravou_bytes=True, detalhe="cópia mais completa")

        if ident.prioridade < prio_atual:
            # Chegou o resumo depois do completo. Guarda, não rebaixa.
            self._salvar_captura(pasta, cap)
            self.trilha.registrar(aud.COPIA_NOVA, id_documento=ident.id_documento,
                                  chave=ident.chave or None, hash=ident.hash_conteudo,
                                  nsu=doc.nsu or None, resultado=COPIA_MENOR,
                                  motivo="cópia menos completa preservada ao lado")
            return Preservacao(COPIA_MENOR, ident.id_documento, pasta, ident,
                               gravou_bytes=True, detalhe="cópia menos completa")

        # Mesma prioridade, conteúdo incompatível. Ninguém decide isto sozinho.
        cap["quarentena"] = True
        cap["motivo_quarentena"] = (
            f"duas cópias {ident.rotulo_prioridade} da mesma chave com conteúdo "
            f"diferente")
        self._salvar_captura(pasta, cap)
        self.trilha.registrar(aud.COLISAO, id_documento=ident.id_documento,
                              chave=ident.chave or None, hash=ident.hash_conteudo,
                              nsu=doc.nsu or None, motivo=cap["motivo_quarentena"])
        return Preservacao(COLISAO, ident.id_documento, pasta, ident,
                           quarentena=True, gravou_bytes=True,
                           detalhe=cap["motivo_quarentena"])

    def _salvar_captura(self, pasta: Path, cap: dict) -> None:
        _escrever_atomico(pasta / CAPTURA,
                          json.dumps(cap, ensure_ascii=False, indent=1).encode("utf-8"))

    # ── conferência de integridade ────────────────────────────────────────
    def verificar(self) -> list[dict]:
        """Relê o acervo e compara cada arquivo com o hash registrado.

        Detecta corrupção silenciosa de disco. Não corrige nada — corrigir
        exigiria rebaixar o documento, e essa é uma decisão do usuário."""
        problemas: list[dict] = []
        for especie, id_doc in self.listar():
            pasta = self.pasta_do(especie, id_doc)
            cap = self.ler_captura(especie, id_doc)
            if not cap:
                problemas.append({"id_documento": id_doc, "especie": especie,
                                  "problema": "captura.json ausente ou ilegível"})
            for copia in (cap.get("copias") or [{"arquivo": ORIGINAL,
                                                 "hash": cap.get("hash_conteudo")}]):
                arq = pasta / str(copia.get("arquivo") or ORIGINAL)
                if not arq.exists():
                    problemas.append({"id_documento": id_doc, "especie": especie,
                                      "problema": f"arquivo ausente: {arq.name}"})
                    continue
                atual = idf.hash_conteudo(arq.read_bytes())
                if copia.get("hash") and atual != copia["hash"]:
                    problemas.append({"id_documento": id_doc, "especie": especie,
                                      "problema": f"hash não confere: {arq.name}"})
        return problemas


def abrir(dados_dir, identidade, servico: str = "", ambiente="") -> AcervoArquivos:
    return AcervoArquivos(dados_dir=Path(dados_dir), identidade=str(identidade),
                          servico=servico, ambiente=ambiente)


# Pastas que existem só porque dá para refazê-las a partir do acervo. Apagar
# qualquer uma delas não perde nada — e é o que o teste de reconstrução usa
# para provar que o acervo basta sozinho.
DERIVADOS = ("indice", "derivados")


def limpar_derivados(dados_dir, identidade) -> int:
    """Apaga só o que é reconstruível. **Nunca toca no acervo.**

    `indice/`     — a projeção SQLite; `indice.reconstruir()` a refaz.
    `derivados/`  — o cache de DANFE (NF-e 6). PDF é leitura do XML em papel:
                    some, e o próximo clique o gera de novo.

    Existe para o teste de reconstrução poder provar que o acervo basta. Se um
    dia apagar mais do que isto, o teste falha."""
    ident = normalizar(identidade)
    if not ident.valido:
        raise ValueError("identidade inválida")
    n = 0
    for pasta in DERIVADOS:
        alvo = Path(dados_dir) / ident.valor / pasta
        if alvo.exists():
            n += sum(1 for _ in alvo.rglob("*") if _.is_file())
            shutil.rmtree(alvo, ignore_errors=True)
    return n
