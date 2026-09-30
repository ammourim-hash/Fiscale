# -*- coding: utf-8 -*-
"""autenticacao.py — o Bearer do Integra Contador, gerado na hora.

NÃO EXISTE TOKEN FIXO, E ISSO JÁ CUSTOU UMA CHAMADA
    Em 08/09/2026 a primeira consulta real foi ao ar com o bearer PÚBLICO do
    ambiente de demonstração, porque o código tinha uma reserva silenciosa. O
    gateway respondeu `HTTP 403 code 900908 Resource forbidden` — que não é
    erro do SITFIS, é o gateway dizendo que aquele token não assina esta API.

    O bearer de produção é MINTADO: troca-se `consumerKey:consumerSecret` por
    um `access_token` que dura pouco e expira. Guardar um "token" no cofre é
    guardar algo que amanhã não vale.

O HANDSHAKE É DO TITULAR DO CONTRATO
    O `/authenticate` exige certificado no TLS, e é o do **contratante** —
    quem tem o contrato SERPRO. O procurador (pessoa física, com a procuração
    das empresas) vai no `autorPedidoDados` do PAYLOAD do SITFIS, e não no
    handshake. São dois papéis diferentes em duas camadas diferentes, e trocá-
    los produz um 403 que parece problema de procuração.

    Por isso o certificado é resolvido pelo CNPJ do CONTRATANTE por padrão.
    Não é chute: é a regra do gateway. O `certificado_id` do cofre existe para
    o dia em que não for.

DUAS FICHAS SAEM DA MESMA MÁQUINA
    A resposta do `/authenticate` traz `access_token` **e** `jwt_token`. Os
    dois nascem juntos e expiram juntos. Guardar um `jwt_token` fixo no cofre
    é guardar metade de um par que já venceu — o cofre continua aceitando um,
    mas o daqui vence, porque é o que combina com o bearer desta sessão.

O SEGREDO NÃO ATRAVESSA ESTE MÓDULO PARA LUGAR NENHUM
    `consumerKey` e `consumerSecret` viram um cabeçalho `Basic` e morrem na
    função. O `Bearer` tem `__repr__` próprio para não vazar em traceback, em
    log ou num `print` de depuração — que é como segredo escapa na prática.

A SESSÃO mTLS É INJETADA
    `abrir_sessao()` devolve uma `requests.Session` já com o certificado. Em
    produção ela vem do `ingestao/sessao.py`, que é a ÚNICA fábrica de sessão
    por certificado do projeto. Em teste vem um dublê — e é isso que permite
    exercitar renovação, expiração e falha sem certificado nenhum.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field

URL = "https://autenticacao.sapi.serpro.gov.br/authenticate"

# Renova ANTES de vencer. Um token que expira no voo vira 401 no meio de um
# ciclo de 19 empresas, e o relatório do dia fica pela metade sem motivo claro.
MARGEM_S = 60

# Quando o serviço não diz quanto dura, assume-se pouco. Errar para menos
# custa uma renovação; errar para mais custa o 401 acima.
DURACAO_PADRAO_S = 300


class ErroAutenticacao(Exception):
    """Não foi possível obter o token. A mensagem nunca contém segredo."""


class SemChaves(ErroAutenticacao):
    """Faltam consumerKey/consumerSecret, ou o certificado do contratante."""


@dataclass
class Bearer:
    """O par de fichas, com a hora em que deixam de valer."""

    access_token: str = field(repr=False, default="")
    jwt_token: str = field(repr=False, default="")
    expira_em: float = 0.0
    tipo: str = "Bearer"

    def __repr__(self) -> str:      # nunca o valor, nem por acidente
        return ("Bearer(access_token=****(%d), jwt_token=****(%d), "
                "expira_em=%.0f)" % (len(self.access_token),
                                     len(self.jwt_token), self.expira_em))

    __str__ = __repr__

    def valido(self, agora: float, margem: float = MARGEM_S) -> bool:
        return bool(self.access_token) and (agora + margem) < self.expira_em


class Autenticador:
    """Guarda o par vivo e o renova quando falta pouco.

    Uma instância por processo é o suficiente: o token vale para todas as
    empresas da carteira, e minta-lo 19 vezes seria 19 handshakes de TLS com
    certificado para nada.
    """

    def __init__(self, consumer_key: str, consumer_secret: str,
                 abrir_sessao=None, url: str = URL, agora=time.time,
                 tempo_limite: int = 60):
        if not consumer_key or not consumer_secret:
            raise SemChaves(
                "Faltam o consumerKey e o consumerSecret do Integra Contador. "
                "Eles vêm da área do cliente SERPRO e são gravados no cofre "
                "com `situacao.configurar --segredo consumer_key` e "
                "`--segredo consumer_secret`.")
        if abrir_sessao is None:
            raise SemChaves(
                "Falta a sessão com o certificado do contratante. O "
                "`/authenticate` exige o e-CNPJ de quem tem o contrato no "
                "handshake TLS.")
        self._basico = base64.b64encode(
            ("%s:%s" % (consumer_key, consumer_secret)).encode("utf-8")
        ).decode("ascii")
        self.abrir_sessao = abrir_sessao
        self.url = url
        self.agora = agora
        self.tempo_limite = tempo_limite
        self._ficha = Bearer()
        self.renovacoes = 0          # para o log dizer quantas vezes mintou

    # ── uso ──────────────────────────────────────────────────────────────
    def ficha(self) -> Bearer:
        """O par válido. Renova sozinho quando falta menos que a margem."""
        if not self._ficha.valido(self.agora()):
            self._ficha = self._mintar()
            self.renovacoes += 1
        return self._ficha

    def esquecer(self) -> None:
        """Joga a ficha fora. Serve para o 401: talvez ela tenha vencido antes
        do que o serviço disse, e insistir com a mesma não resolve."""
        self._ficha = Bearer()

    # ── a troca ──────────────────────────────────────────────────────────
    def _mintar(self) -> Bearer:
        cabecalhos = {
            "Authorization": "Basic " + self._basico,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "role-type": "TERCEIROS",
        }
        try:
            sessao = self.abrir_sessao()
        except Exception as exc:
            raise SemChaves(
                "Não foi possível abrir a sessão com o certificado do "
                "contratante (%s). Confira o certificado e a senha dele no "
                "cadastro." % type(exc).__name__) from None

        try:
            resposta = sessao.post(self.url, headers=cabecalhos,
                                   data={"grant_type": "client_credentials"},
                                   timeout=self.tempo_limite)
        except Exception as exc:
            raise ErroAutenticacao(
                "Não foi possível falar com o autenticador do SERPRO (%s)."
                % type(exc).__name__) from None
        finally:
            try:
                sessao.close()
            except Exception:
                pass

        status = getattr(resposta, "status_code", 0)
        try:
            corpo = resposta.json()
        except Exception:
            corpo = None

        if status != 200 or not isinstance(corpo, dict):
            # O TEXTO DO SERVIÇO VAI JUNTO, mas cortado: numa resposta de erro
            # ele é curto, e numa página de portal é HTML que não cabe em log.
            bruto = ""
            try:
                bruto = (resposta.text or "")[:200]
            except Exception:
                pass
            raise ErroAutenticacao(
                "O autenticador do SERPRO recusou (HTTP %s). %s"
                % (status, bruto))

        acesso = str(corpo.get("access_token") or "")
        if not acesso:
            raise ErroAutenticacao(
                "O autenticador respondeu sem `access_token`.")
        try:
            dura = float(corpo.get("expires_in") or DURACAO_PADRAO_S)
        except (TypeError, ValueError):
            dura = DURACAO_PADRAO_S

        return Bearer(access_token=acesso,
                      jwt_token=str(corpo.get("jwt_token") or ""),
                      expira_em=self.agora() + max(1.0, dura),
                      tipo=str(corpo.get("token_type") or "Bearer"))


# ── a sessão de verdade ─────────────────────────────────────────────────
def sessao_do_contratante(raiz, contratante: str, certificado_id: str = ""):
    """Uma função sem argumentos que abre a sessão mTLS do contratante.

    Devolve um *callable* porque a sessão precisa nascer na hora de cada
    renovação: certificado tem validade, e um objeto guardado por horas pode
    estar apoiado num arquivo que mudou.

    Resolve nesta ordem:
      1. o `certificado_id` do cofre, quando alguém o declarou;
      2. o certificado do CONTRATANTE — que é a regra do gateway, e por isso
         é padrão e não chute.
    """
    def abrir():
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                               / "nfse" / "backend"))
        from ingestao import cadastro as _cad          # type: ignore
        from ingestao import sessao as _ses            # type: ignore

        cad = _cad.carregar(raiz)
        cred = None
        if certificado_id:
            for empresa in cad.listar_empresas(com_credencial=True):
                for c in cad.obter_credenciais_da_empresa(empresa.identificador):
                    if c.id == certificado_id:
                        cred = c
                        break
                if cred:
                    break
            if cred is None:
                raise SemChaves(
                    "O certificado %r declarado no cofre não está no cadastro."
                    % certificado_id)
        else:
            cred = cad.obter_certificado_da_empresa(contratante)
            if cred is None:
                raise SemChaves(
                    "O contratante %s não tem certificado no cadastro, e o "
                    "handshake do /authenticate exige o e-CNPJ de quem tem o "
                    "contrato." % contratante)
        return _ses.criar_sessao(cred, raiz)

    return abrir


def de_credencial(raiz, cred, cofre, **kw) -> Autenticador:
    """Monta o autenticador a partir do cofre. Levanta `SemChaves` sem eles."""
    return Autenticador(
        consumer_key=cofre.segredo("consumer_key"),
        consumer_secret=cofre.segredo("consumer_secret"),
        abrir_sessao=sessao_do_contratante(raiz, cred.contratante,
                                           cred.certificado_id),
        **kw)
