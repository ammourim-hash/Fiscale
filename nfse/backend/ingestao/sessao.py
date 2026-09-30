"""
sessao.py — a ÚNICA fábrica de sessão HTTPS autenticada por certificado.

O QUE SAI DE CIRCULAÇÃO
    Hoje `core.py`, `nfe.py` e `prefeituras.py` montam cada um a sua sessão
    mTLS, e mais dois deles abrem o PKCS#12 por conta própria para ler o nome
    do titular. São três lugares para consertar quando a biblioteca muda, três
    lugares por onde uma senha pode escapar, e três comportamentos que podem
    divergir sem ninguém notar. Daqui em diante, NF-e, CT-e e NFS-e pedem a
    sessão aqui e não sabem o que é um `.pfx`.

VALIDAÇÃO ACONTECE ANTES DA REDE
    O `Pkcs12Adapter` monta o `SSLContext` no próprio construtor. Então senha
    errada, arquivo corrompido e certificado vencido estouram **antes** de
    qualquer requisição — e viram exceção nomeada, não um erro de TLS
    incompreensível no meio de uma varredura.

ARQUIVO TEMPORÁRIO (auditado, não presumido)
    O `requests_pkcs12` precisa de um arquivo porque o `ssl` do CPython só sabe
    carregar cadeia de certificado a partir de um caminho — não há API em
    memória. Antes de escrever solução própria, li a biblioteca instalada:

      • grava um PEM temporário com a chave privada cifrada por uma senha
        aleatória de 128 bits gerada na hora (`secrets.token_bytes`);
      • carrega o contexto e apaga o arquivo em `finally` — some também quando
        dá exceção;
      • usa o diretório temporário do sistema, **fora da pasta de dados**, logo
        nunca entra no `.fbk` nem sobe para o Drive.

    Isso é melhor do que qualquer coisa que eu escreveria por cima. Portanto
    NÃO reimplementamos: reusamos, documentamos e travamos com teste.

SEGREDO
    A senha só existe como variável local dentro de `criar_sessao`. Não vira
    atributo de `Empresa`, de sessão, de checkpoint nem de log, e nenhuma
    exceção daqui carrega o valor na mensagem.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import requests
from requests_pkcs12 import Pkcs12Adapter

from . import modelo as m
from .ambiente import Ambiente, PRODUCAO

# `seguranca` (o cofre DPAPI da PORT 3) mora no backend, um nível acima.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

TIMEOUT_PADRAO = 60


# ── erros nomeados ──────────────────────────────────────────────────────────
class ErroCredencial(Exception):
    """Base de tudo que impede montar a sessão. Nunca carrega a senha."""


class SenhaAusente(ErroCredencial):
    """Cofre vazio ou que não abre nesta máquina — a conta fica 'aguardando senha'.

    É o estado esperado depois de restaurar um backup em outro computador: o
    DPAPI é da máquina + conta do Windows, e isso é proposital."""


class CertificadoNaoEncontrado(ErroCredencial):
    """O `.pfx` cadastrado não está no caminho registrado."""


class CertificadoInvalido(ErroCredencial):
    """Senha errada, arquivo corrompido ou certificado fora da validade."""


@dataclass(frozen=True)
class InfoCertificado:
    """O que se pode dizer de um certificado sem expor segredo algum."""
    titular: str            # Common Name do certificado
    nao_antes: str
    nao_depois: str
    expirado: bool


# ── leitura do PKCS#12 (ponto único) ────────────────────────────────────────
def _bytes_do_pfx(cred: m.Credencial) -> bytes:
    if not cred.caminho:
        raise CertificadoNaoEncontrado(f"credencial {cred.id}: caminho não cadastrado")
    p = Path(cred.caminho)
    if not p.is_file():
        raise CertificadoNaoEncontrado(f"credencial {cred.id}: arquivo não encontrado em {p}")
    try:
        return p.read_bytes()
    except OSError as exc:
        raise CertificadoNaoEncontrado(f"credencial {cred.id}: não foi possível ler o arquivo") from exc


def _senha(cred: m.Credencial, dados_dir) -> str:
    """Tira a senha do cofre. A mensagem de erro nunca inclui o valor."""
    if not cred.tem_senha:
        raise SenhaAusente(f"credencial {cred.id}: aguardando senha")
    try:
        senha = cred.abrir_senha(dados_dir)
    except Exception as exc:
        raise SenhaAusente(
            f"credencial {cred.id}: a senha guardada não abre nesta máquina "
            f"({type(exc).__name__})") from None
    if not senha:
        raise SenhaAusente(f"credencial {cred.id}: senha guardada está vazia")
    return senha


def inspecionar(cred: m.Credencial, dados_dir) -> InfoCertificado:
    """Abre o certificado só para descrever quem é e até quando vale.

    Substitui as três aberturas espalhadas de `load_key_and_certificates`.
    """
    from cryptography.hazmat.primitives.serialization import pkcs12 as _pkcs12
    from cryptography.x509.oid import NameOID
    from datetime import datetime, timezone

    dados = _bytes_do_pfx(cred)
    senha = _senha(cred, dados_dir)
    try:
        _chave, cert, _extras = _pkcs12.load_key_and_certificates(dados, senha.encode("utf-8"))
    except Exception as exc:
        raise CertificadoInvalido(
            f"credencial {cred.id}: não foi possível abrir o certificado "
            f"({type(exc).__name__})") from None
    finally:
        del senha
    if cert is None:
        raise CertificadoInvalido(f"credencial {cred.id}: arquivo sem certificado")

    try:
        titular = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except Exception:
        titular = ""
    fim = cert.not_valid_after_utc
    return InfoCertificado(
        titular=str(titular),
        nao_antes=cert.not_valid_before_utc.isoformat(),
        nao_depois=fim.isoformat(),
        expirado=fim < datetime.now(timezone.utc),
    )


# ── a fábrica ───────────────────────────────────────────────────────────────
def criar_sessao(cred: m.Credencial, dados_dir, ambiente: Ambiente = PRODUCAO,
                 timeout: int = TIMEOUT_PADRAO) -> requests.Session:
    """Devolve uma `requests.Session` já autenticada com o certificado.

    Quem chama não abre `.pfx`, não lê senha, não converte certificado, não cria
    nem limpa temporário. Erros vêm nomeados (`SenhaAusente`,
    `CertificadoNaoEncontrado`, `CertificadoInvalido`).

    O `ambiente` não muda a conexão — anda junto para que log e checkpoint
    saibam de onde a sessão veio, já que NSU não pode cruzar ambientes (D29).
    """
    dados = _bytes_do_pfx(cred)
    senha = _senha(cred, dados_dir)
    try:
        # O adapter valida senha e validade aqui dentro, sem tocar a rede, e
        # cuida sozinho do PEM temporário (ver cabeçalho).
        adaptador = Pkcs12Adapter(pkcs12_data=dados, pkcs12_password=senha)
    except ErroCredencial:
        raise
    except Exception as exc:
        raise CertificadoInvalido(
            f"credencial {cred.id}: certificado recusado ({type(exc).__name__}: {exc})"
        ) from None
    finally:
        del senha          # a senha não sobrevive a esta função

    s = requests.Session()
    s.mount("https://", adaptador)
    s.headers.update({"User-Agent": "Fiscale/1.0"})
    # Metadados de rastreio. NUNCA credencial, NUNCA senha.
    s.fiscale_ambiente = ambiente.nome          # type: ignore[attr-defined]
    s.fiscale_credencial = cred.id              # type: ignore[attr-defined]
    s.fiscale_timeout = timeout                 # type: ignore[attr-defined]
    return s


@contextmanager
def abrir_sessao(cred: m.Credencial, dados_dir, ambiente: Ambiente = PRODUCAO,
                 timeout: int = TIMEOUT_PADRAO):
    """`with abrir_sessao(cred, dados) as s:` — fecha os sockets ao sair,
    inclusive quando dá exceção.

    Não se chama `sessao` porque esse nome é o do próprio módulo: exportar os
    dois pelo pacote fazia `from ingestao import sessao` devolver a função em
    vez do módulo, silenciosamente."""
    s = criar_sessao(cred, dados_dir, ambiente, timeout)
    try:
        yield s
    finally:
        s.close()


def criar_sessao_para_empresa(cad, identificador, ambiente: Ambiente = PRODUCAO,
                              timeout: int = TIMEOUT_PADRAO) -> requests.Session:
    """Atalho: da empresa direto para a sessão, escolhendo a credencial preferida."""
    emp = cad.obter_empresa(identificador)
    if emp is None:
        raise ErroCredencial(f"empresa não encontrada no cadastro: {identificador!r}")
    cred = emp.credencial_preferida()
    if cred is None:
        raise CertificadoNaoEncontrado(
            f"empresa {emp.nome_exibicao()}: nenhuma credencial cadastrada")
    return criar_sessao(cred, cad.dados_dir, ambiente, timeout)
