"""A resposta do fisco, guardada exatamente como chegou.

POR QUE ISTO EXISTE
    Na CTE 4 descobrimos que o NSU gravado nos documentos era a posição no
    lote, não o `docZip/@NSU`. O defeito era de uma linha. O que doeu foi
    outra coisa: **não havia como conferir**. A resposta que trouxe os 72
    documentos já capturados não existia em lugar nenhum, então o valor
    correto não era recuperável — nem para auditar, nem para corrigir.

    Um erro de leitura sem a resposta original é indetectável e irreversível.
    Com ela, é uma releitura.

O QUE SE GUARDA
    Os bytes que vieram do transporte, sem nenhuma interpretação, mais um
    manifesto em claro: empresa, serviço, ambiente, quando, identificador da
    tentativa e o SHA-256 do conteúdo.

O QUE NÃO SE GUARDA
    Certificado, chave privada, senha, cabeçalho de autenticação. A resposta
    do `CTeDistribuicaoDFe` não os contém — mas isso é conferido, não suposto:
    `_sem_segredo()` recusa a escrita se encontrar sinal de material sensível.

A ESCRITA É VERIFICADA
    `preservar()` grava, **relê do disco** e confere o SHA-256. Só devolve
    sucesso se o que está no disco for byte a byte o que se pediu para gravar.
    Quem chama usa esse sucesso como pré-condição para avançar o checkpoint:
    guardar depois de avançar seria guardar o que já não importa.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .ambiente import PRODUCAO, resolver as resolver_ambiente
from .identidade import normalizar

PASTA = "respostas"
VERSAO = 1

# Marcas de material que NUNCA pode ir para o disco junto da resposta. A
# resposta legítima do serviço não tem nenhuma delas.
_PROIBIDO = (
    b"-----BEGIN",              # PEM de qualquer espécie
    b"PRIVATE KEY",
    b"ENCRYPTED PRIVATE KEY",
    b"PKCS12", b"pkcs12",
    b"Authorization:",
)

# Nomes de CAMPO de segredo. O separador é montado na conferência, e não
# escrito aqui: um literal com a forma `campo="valor"` neste arquivo seria
# indistinguível de uma senha em código, e a guarda de segurança acusaria — com
# razão. Ela não deve aprender exceções para acomodar quem a testa.
_CAMPOS_DE_SEGREDO = (b"senha", b"password", b"passwd", b"pwd", b"secret",
                      b"token")


class PreservacaoFalhou(Exception):
    """Não foi possível garantir que a resposta está no disco, íntegra."""


@dataclass
class Preservacao:
    """O recibo. Sem bytes, sem segredo — serve para log e para o relatório."""
    tentativa: str = ""
    identidade_mascarada: str = ""
    servico: str = ""
    ambiente: str = ""
    quando: str = ""
    sha256: str = ""
    tamanho_bytes: int = 0
    arquivo: str = ""
    manifesto: str = ""
    verificada: bool = False
    motivo: str = ""

    def para_json(self) -> dict:
        return {"tentativa": self.tentativa,
                "empresa": self.identidade_mascarada,
                "servico": self.servico, "ambiente": self.ambiente,
                "quando": self.quando, "sha256": self.sha256,
                "tamanho_bytes": self.tamanho_bytes,
                # SÓ O NOME: o caminho completo carrega o CNPJ inteiro na pasta,
                # e este recibo vai para log e para relatório.
                "arquivo": Path(self.arquivo).name if self.arquivo else "",
                "verificada": self.verificada,
                "motivo": self.motivo or None}


def _sem_segredo(corpo: bytes) -> str:
    """Devolve o motivo da recusa, ou vazio quando está limpo."""
    for marca in _PROIBIDO:
        if marca in corpo:
            return (f"conteúdo contém {marca.decode('ascii', 'replace')!r} — "
                    f"material sensível não vai para o disco")
    baixo = corpo.lower()
    for campo in _CAMPOS_DE_SEGREDO:
        if campo + b"=" in baixo or campo + b'":' in baixo:
            return (f"conteúdo traz o campo "
                    f"{campo.decode('ascii', 'replace')!r} com valor — "
                    f"material sensível não vai para o disco")
    return ""


def _pasta(raiz: Path, identidade: str, servico: str, amb) -> Path:
    return Path(raiz) / identidade / PASTA / servico / amb.nome


def nova_tentativa() -> str:
    """Identificador da tentativa. Único, e sem nada do cliente dentro."""
    return uuid.uuid4().hex


def preservar(dados_dir, identidade, corpo: bytes, *, servico: str,
              ambiente=PRODUCAO, tentativa: str = "",
              quando: datetime | None = None) -> Preservacao:
    """Grava a resposta e **confere no disco**. Levanta se não conseguir.

    Levantar é deliberado: quem chama precisa poder tratar "não consegui
    guardar" como impedimento para avançar o checkpoint. Um retorno silencioso
    de falha viraria, na primeira pressa, um avanço sem rastro.
    """
    amb = resolver_ambiente(ambiente)
    ident = normalizar(identidade)
    q = quando or datetime.now(timezone.utc)
    p = Preservacao(
        tentativa=tentativa or nova_tentativa(),
        identidade_mascarada=ident.mascarado() if ident.valido else "<inválida>",
        servico=str(servico or ""), ambiente=amb.nome,
        quando=q.isoformat(), tamanho_bytes=len(corpo or b""))

    if not ident.valido:
        raise PreservacaoFalhou(f"identidade inválida: {ident.motivo}")
    if not corpo:
        raise PreservacaoFalhou("resposta vazia: não há o que preservar")

    motivo = _sem_segredo(corpo)
    if motivo:
        p.motivo = motivo
        raise PreservacaoFalhou(motivo)

    p.sha256 = hashlib.sha256(corpo).hexdigest()
    destino = _pasta(Path(dados_dir), ident.valor, p.servico, amb)
    destino.mkdir(parents=True, exist_ok=True)

    nome = f"{q.strftime('%Y%m%dT%H%M%S')}-{p.tentativa[:12]}"
    alvo = destino / f"{nome}.xml"
    manif = destino / f"{nome}.json"

    _gravar_atomico(alvo, corpo)

    # A CONFERÊNCIA. Relê do disco: só o que voltou íntegro conta como escrito.
    try:
        de_volta = alvo.read_bytes()
    except Exception as e:
        raise PreservacaoFalhou(f"gravou mas não releu: {e}") from e
    if hashlib.sha256(de_volta).hexdigest() != p.sha256:
        raise PreservacaoFalhou(
            "o conteúdo no disco não confere com o que foi enviado para gravar")

    p.arquivo = str(alvo)
    p.manifesto = str(manif)
    p.verificada = True
    _gravar_atomico(manif, json.dumps(
        {"versao": VERSAO, **p.para_json()},
        indent=1, ensure_ascii=False).encode("utf-8"))
    return p


def _gravar_atomico(alvo: Path, dados: bytes) -> None:
    """Arquivo pela metade é pior que arquivo ausente: um mente, o outro não."""
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=str(alvo.parent), suffix=".parcial")
        with os.fdopen(fd, "wb") as f:
            f.write(dados)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, alvo)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def listar(dados_dir, identidade, *, servico: str, ambiente=PRODUCAO) -> list:
    """Os manifestos já guardados, do mais novo para o mais antigo."""
    amb = resolver_ambiente(ambiente)
    ident = normalizar(identidade)
    if not ident.valido:
        return []
    base = _pasta(Path(dados_dir), ident.valor, servico, amb)
    if not base.exists():
        return []
    fora = []
    for m in sorted(base.glob("*.json"), reverse=True):
        try:
            fora.append(json.loads(m.read_text("utf-8")))
        except Exception:
            continue
    return fora


def conferir(caminho) -> bool:
    """O arquivo no disco ainda bate com o SHA-256 do manifesto ao lado?"""
    xml = Path(caminho)
    manif = xml.with_suffix(".json")
    if not xml.exists() or not manif.exists():
        return False
    try:
        d = json.loads(manif.read_text("utf-8"))
    except Exception:
        return False
    return hashlib.sha256(xml.read_bytes()).hexdigest() == d.get("sha256")
