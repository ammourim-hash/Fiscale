# -*- coding: utf-8 -*-
"""armazenamento.py — onde a certidão e o extrato ficam guardados, intactos.

A PROMESSA, IGUAL À DO ACERVO
    Os bytes que o órgão entregou são gravados como vieram e **jamais
    reescritos**. Parser novo, regra nova, correção de bug: tudo isso produz
    dado derivado em outro lugar. O `original.pdf` de hoje precisa ser idêntico
    ao de daqui a cinco anos, porque é ele que vale numa fiscalização — não a
    nossa leitura dele.

LAYOUT

    <raiz>/<identidade>/situacao/<esfera>/<tipo>/<id[:2]>/<id>/
        original.pdf     os bytes exatos, como chegaram
        captura.json     proveniência: fonte, quando, sha256, tentativa

O CAMINHO É O HASH, E SÓ O HASH
    O código de controle da certidão é a chave natural — o órgão o emite
    justamente para identificar aquele documento. Mesmo assim ele NÃO entra no
    caminho, e a razão é a lição que o `acervo.py` já pagou caro:

        caminho que depende do parser faz o documento MUDAR DE LUGAR quando o
        parser evolui — e isso quebra a imutabilidade que este módulo existe
        para garantir.

    O hash existe antes de qualquer leitura, inclusive para PDF corrompido e
    para formato que ninguém reconhece. Quem responde por código de controle,
    validade e natureza é o índice, que é reconstruível.

POR QUE 32 CARACTERES E NÃO OS 64
    O nome da pasta usa os 32 primeiros dígitos do SHA-256. São 128 bits: para
    o volume de um escritório (dezenas de documentos por empresa por ano),
    colisão não acontece. O que acontece, e já mordeu este projeto, é caminho
    longo demais no Windows. O hash INTEIRO fica no `captura.json` e no índice,
    e é ele que a conferência usa.

A ESCRITA É VERIFICADA, NÃO CONFIADA
    `guardar()` grava, **relê do disco** e confere o SHA-256. Só devolve
    sucesso se o que está no disco for byte a byte o que se pediu para gravar.
    Sem isso, "gravei" é uma suposição — e quem chama usa esse sucesso como
    pré-condição para indexar e para dizer à pessoa que está tudo salvo.

O QUE ESTE MÓDULO **NÃO** FAZ
    Não lê PDF, não decide natureza, não sabe o que é validade, não fala com
    órgão nenhum e não escolhe empresa. Ele recebe bytes e identidade, e
    guarda. Um armazenamento que soubesse ler seria uma segunda opinião sobre o
    que o documento diz — e duas opiniões divergem.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import modelo

PASTA = "situacao"
ORIGINAL = "original.pdf"
CAPTURA = "captura.json"

# Prefixo do hash usado no caminho. Ver o cabeçalho: 128 bits bastam, e o
# Windows tem orçamento de caminho.
TAMANHO_ID = 32

# Desfechos de `guardar()`
NOVO = "NOVO"                   # primeira vez que estes bytes aparecem
DUPLICATA = "DUPLICATA"         # já tínhamos, byte a byte


class ErroArmazenamento(Exception):
    """Falhou ao gravar, ou o disco não devolveu o que se gravou."""


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def identificar(bruto: bytes) -> tuple:
    """`(id_curto, sha256_completo)` — a única forma de nomear um documento."""
    completo = hashlib.sha256(bruto).hexdigest()
    return completo[:TAMANHO_ID], completo


def eh_pdf(bruto: bytes) -> bool:
    """Só o cabeçalho. Não valida estrutura — isso é trabalho de quem lê.

    Serve para pegar o caso que mais acontece na prática: o portal responde uma
    página HTML de erro ou de login, com status 200, e sem esta conferência ela
    seria guardada como se fosse a certidão.
    """
    return bruto[:5] == b"%PDF-"


class Armazem:
    """A pasta de situação fiscal de UMA empresa."""

    def __init__(self, raiz, identidade: str):
        ident = "".join(c for c in str(identidade) if c.isdigit())
        if not ident:
            raise modelo.Invalido("identidade sem dígitos: %r" % (identidade,))
        self.identidade = ident
        self.base = Path(raiz) / ident / PASTA

    # ── caminhos ─────────────────────────────────────────────────────────
    def pasta(self, esfera: str, tipo: str, id_curto: str) -> Path:
        e, t = modelo.conferir(esfera, tipo)
        if not id_curto or len(id_curto) != TAMANHO_ID or \
                any(c not in "0123456789abcdef" for c in id_curto):
            raise modelo.Invalido("id fora de formato: %r" % (id_curto,))
        return self.base / e / t / id_curto[:2] / id_curto

    def existe(self, esfera: str, tipo: str, id_curto: str) -> bool:
        return (self.pasta(esfera, tipo, id_curto) / ORIGINAL).exists()

    # ── escrita ──────────────────────────────────────────────────────────
    def guardar(self, esfera: str, tipo: str, bruto: bytes,
                origem: str = "", tentativa: str = "",
                extra: dict = None) -> dict:
        """Guarda os bytes e devolve o que aconteceu.

        `extra` entra no `captura.json` como proveniência adicional — jamais
        como verdade sobre o documento. Nada aqui é lido do PDF.
        """
        e, t = modelo.conferir(esfera, tipo)
        if not bruto:
            raise modelo.Invalido("documento vazio")
        id_curto, sha = identificar(bruto)
        destino = self.pasta(e, t, id_curto)
        alvo = destino / ORIGINAL

        if alvo.exists():
            # MESMOS BYTES: não regrava. Regravar seria reescrever o original,
            # que é justamente o que este módulo promete nunca fazer.
            atual = alvo.read_bytes()
            if hashlib.sha256(atual).hexdigest() == sha:
                self._anotar_copia(destino, origem, tentativa, extra)
                return {"desfecho": DUPLICATA, "id": id_curto, "sha256": sha,
                        "caminho": str(alvo), "bytes": len(bruto)}
            # Mesmo hash de caminho, conteúdo diferente: colisão de SHA-256.
            # Não é para acontecer nesta vida; se acontecer, é para parar tudo.
            raise ErroArmazenamento(
                "colisão de hash em %s — o disco tem conteúdo diferente com o "
                "mesmo SHA-256" % destino)

        destino.mkdir(parents=True, exist_ok=True)
        _escrever_atomico(alvo, bruto)

        # RELÊ E CONFERE. Só depois disto é que "gravei" vira verdade.
        de_volta = alvo.read_bytes()
        if hashlib.sha256(de_volta).hexdigest() != sha:
            try:
                alvo.unlink()
            except OSError:
                pass
            raise ErroArmazenamento(
                "o que voltou do disco não é o que foi gravado (%s)" % alvo)

        captura = {
            "identidade": self.identidade,
            "esfera": e, "tipo": t,
            "id": id_curto, "sha256": sha, "bytes": len(bruto),
            "origem": origem or "DESCONHECIDA",
            "tentativa": tentativa or "",
            "capturado_utc": _agora(),
            "copias": [],
            "extra": dict(extra or {}),
        }
        _escrever_atomico(destino / CAPTURA,
                          json.dumps(captura, ensure_ascii=False,
                                     indent=2).encode("utf-8"))
        return {"desfecho": NOVO, "id": id_curto, "sha256": sha,
                "caminho": str(alvo), "bytes": len(bruto)}

    def _anotar_copia(self, destino: Path, origem: str, tentativa: str,
                      extra: dict = None) -> None:
        """Registra que o MESMO documento chegou de novo.

        O documento não muda; a proveniência sim. Saber que a mesma certidão
        veio duas vezes, por caminhos diferentes, é informação — e apagá-la
        seria perder a única pista de que houve retrabalho.
        """
        arq = destino / CAPTURA
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
        except Exception:
            return          # metadado ilegível não invalida o original
        d.setdefault("copias", []).append(
            {"quando_utc": _agora(), "origem": origem or "DESCONHECIDA",
             "tentativa": tentativa or "", "extra": dict(extra or {})})
        try:
            _escrever_atomico(arq, json.dumps(d, ensure_ascii=False,
                                              indent=2).encode("utf-8"))
        except OSError:
            pass

    # ── leitura ──────────────────────────────────────────────────────────
    def ler(self, esfera: str, tipo: str, id_curto: str) -> bytes:
        return (self.pasta(esfera, tipo, id_curto) / ORIGINAL).read_bytes()

    def captura(self, esfera: str, tipo: str, id_curto: str) -> dict:
        arq = self.pasta(esfera, tipo, id_curto) / CAPTURA
        return json.loads(arq.read_text(encoding="utf-8"))

    def conferir_integridade(self, esfera: str, tipo: str, id_curto: str) -> bool:
        """O documento no disco ainda é o que dizemos que é?

        Confere contra o hash INTEIRO do `captura.json`, não contra o nome da
        pasta — o nome é só o prefixo, e conferir prefixo é conferir menos.
        """
        try:
            bruto = self.ler(esfera, tipo, id_curto)
            esperado = self.captura(esfera, tipo, id_curto).get("sha256")
        except (OSError, ValueError):
            return False
        return bool(esperado) and hashlib.sha256(bruto).hexdigest() == esperado

    def percorrer(self):
        """Todos os documentos desta empresa, lidos do DISCO.

        É por aqui que o índice se reconstrói: o disco é a verdade, o banco é
        conveniência descartável.
        """
        if not self.base.exists():
            return
        for cap in sorted(self.base.rglob(CAPTURA)):
            try:
                d = json.loads(cap.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            d["caminho"] = str(cap.parent / ORIGINAL)
            yield d


def _escrever_atomico(destino: Path, dados: bytes) -> None:
    """Grava inteiro ou não grava.

    Uma queda no meio da escrita deixaria um `original.pdf` truncado — e um
    original truncado é pior que original nenhum, porque parece existir.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(destino.parent), suffix=".parcial")
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


def abrir(raiz, identidade: str) -> Armazem:
    return Armazem(raiz, identidade)
