# -*- coding: utf-8 -*-
"""ciclo.py — a passagem do dublê para a chamada real.

O QUE ESTE MÓDULO É
    A costura entre as peças que já existem: a credencial diz QUEM assina, o
    índice diz SE vale consultar, a fonte busca, o portão guarda. Ele não tem
    regra própria — se um dia tiver, é sinal de que alguma regra escorregou
    para o lugar errado.

O FREIO VEM ANTES DA REDE, SEMPRE
    `precisa_consultar()` é consultado **antes** de montar a chamada. A API do
    SERPRO é bilhetada, e uma rotina que consultasse as 19 empresas por dia
    para reconfirmar o que já está no disco gastaria dinheiro para não
    descobrir nada.

    `forcar=True` existe para o caso legítimo — "quero agora, mesmo válida" —
    e é explícito justamente para não ser o padrão.

NADA ACONTECE SEM CREDENCIAL COMPLETA
    Em produção o `jwt_token` é obrigatório e o certificado precisa existir.
    Faltando qualquer coisa, o ciclo **não chama**: registra tentativa
    `RECUSADA` com o motivo e segue para a próxima empresa. Sair pela metade
    com meia credencial produz erro do lado do órgão, que é mais caro de
    entender do que uma recusa nossa, dita com todas as letras.

O TRANSPORTE É INJETADO ATÉ AQUI
    `transporte=None` monta o de verdade (`requests`). Em teste passa-se o
    dublê. É o que permite exercitar o ciclo inteiro — freio, credencial,
    portão, tradução — sem tocar a rede e sem credencial nenhuma.
"""
from __future__ import annotations

from . import autenticacao as _auth
from . import credencial as _credencial
from . import importacao as _portao
from . import indice as _indice
from . import mensagens, modelo
from .fontes import Colheita
from .fontes import sitfis as _sitfis

# O SITFIS é EXTRATO de pendências, não certidão. Ver o cabeçalho do conector.
ESFERA = modelo.FEDERAL
TIPO = modelo.EXTRATO


def cofre_do_projeto(raiz):
    """O cofre de credenciais JÁ ABERTO com o protetor da máquina.

    ISTO AQUI NÃO É DETALHE, E QUASE CUSTOU A PRIMEIRA CHAMADA REAL.
        `credencial.abrir(raiz)` sem `desproteger` devolve um cofre que LÊ o
        arquivo mas não abre segredo nenhum: `segredo("jwt_token")` volta
        vazio, e o ciclo recusa com "falta o jwt_token" — mesmo com o segredo
        gravado e íntegro no disco.

        O sintoma seria o pior tipo: uma recusa correta, com mensagem
        plausível, apontando para o lugar errado. Alguém iria reconfigurar a
        credencial que já estava certa.

    O protetor é o do projeto (`nfse/backend/seguranca.py`, DPAPI). Importado
    aqui dentro e com falha tolerada: o pacote `situacao` não pode depender do
    backend do NFS-e para ser importado nem testado.
    """
    proteger = desproteger = None
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent
                                / "nfse" / "backend"))
        import seguranca                                   # type: ignore
        proteger = lambda t: seguranca.proteger(t, raiz)   # noqa: E731
        desproteger = lambda b: seguranca.desproteger(b, raiz)  # noqa: E731
    except Exception:
        pass
    return _credencial.abrir(raiz, desproteger=desproteger, proteger=proteger)


def _sem_credencial(motivo: str) -> Colheita:
    return Colheita(modelo.RECUSADA, detalhe=motivo, origem=_sitfis.ORIGEM)


def uma_empresa(raiz, identidade: str, cofre=None, transporte=None,
                indice=None, forcar: bool = False, token: str = "",
                jwt: str = "", auditar=None, autenticador=None,
                usuario: str = "") -> dict:
    """Roda o ciclo para UMA empresa e devolve o que aconteceu, já traduzido."""
    fechar_indice = indice is None
    ix = indice or _indice.abrir(raiz)
    try:
        if not forcar:
            precisa, motivo = ix.precisa_consultar(identidade, ESFERA, TIPO)
            if not precisa:
                return _resposta(identidade, "PULADO", motivo, guardado=False)

        cofre = cofre or cofre_do_projeto(raiz)
        try:
            cred = cofre.escolher()
        except _credencial.SemCredencial as e:
            col = _sem_credencial(str(e))
            _portao.de_colheita(raiz, ix, identidade, ESFERA, TIPO, col,
                                usuario=usuario)
            return _resposta(identidade, col.desfecho, str(e), guardado=False)

        # EM PRODUÇÃO, MEIA CREDENCIAL NÃO SERVE.
        chave = token or cofre.segredo("token")
        assinatura = jwt or cofre.segredo("jwt_token")

        # O TOKEN DO TRIAL NUNCA É RESERVA EM PRODUÇÃO.
        #     Custou a primeira chamada real, em 08/09/2026: sem `token` no
        #     cofre, o código caía em `or _sitfis.TOKEN_TRIAL` e mandava o
        #     bearer PÚBLICO de demonstração para o gateway de produção. Veio
        #     `HTTP 403 code 900908 Resource forbidden` — que não é erro do
        #     SITFIS, é o gateway dizendo que aquele token não assina esta API.
        #
        #     A mensagem certa é a nossa, ANTES de sair: reserva silenciosa
        #     transforma "faltou credencial" em "o órgão recusou", e manda
        #     investigar o lugar errado.
        # EM PRODUÇÃO O BEARER É MINTADO NA HORA.
        #     Não existe token fixo no Integra Contador: troca-se
        #     consumerKey/consumerSecret por um `access_token` de vida curta,
        #     com o certificado do CONTRATANTE no handshake. O `jwt_token`
        #     nasce na mesma resposta e vale pelo mesmo tempo — por isso o par
        #     recém-mintado tem preferência sobre o que estiver guardado.
        if cred.eh_producao and not chave:
            try:
                autenticador = autenticador or _auth.de_credencial(
                    raiz, cred, cofre)
                ficha = autenticador.ficha()
            except _auth.ErroAutenticacao as e:
                col = _sem_credencial(str(e))
                _portao.de_colheita(raiz, ix, identidade, ESFERA, TIPO, col,
                                    usuario=usuario)
                return _resposta(identidade, col.desfecho, str(e),
                                 guardado=False)
            chave = ficha.access_token
            assinatura = ficha.jwt_token or assinatura
        if not chave:
            chave = _sitfis.TOKEN_TRIAL       # só no trial, e de propósito

        if cred.eh_producao and not assinatura:
            motivo = ("Falta o jwt_token, que é obrigatório em produção. "
                      "Nada foi consultado.")
            col = _sem_credencial(motivo)
            _portao.de_colheita(raiz, ix, identidade, ESFERA, TIPO, col,
                                usuario=usuario)
            return _resposta(identidade, col.desfecho, motivo, guardado=False)

        conector = _sitfis.de_credencial(
            cred, transporte=transporte or _sitfis.transporte_requests(),
            token=chave, jwt=assinatura)
        colheita = conector.obter(identidade)
        r = _portao.de_colheita(raiz, ix, identidade, ESFERA, TIPO, colheita,
                                auditar=auditar, usuario=usuario)
        return _resposta(identidade, colheita.desfecho, colheita.detalhe,
                         guardado=bool(r.get("guardado")),
                         documento_id=r.get("id", ""))
    finally:
        if fechar_indice:
            ix.fechar()


def todas(raiz, identidades, **kw) -> list:
    """O ciclo para uma carteira. Uma empresa que falha NÃO para as outras.

    Sem isso, a primeira empresa sem procuração deixaria as dezoito seguintes
    sem consulta nenhuma — e o relatório do dia diria "nada aconteceu" em vez
    de "faltou procuração em uma".
    """
    ix = kw.pop("indice", None) or _indice.abrir(raiz)
    try:
        saida = []
        for ident in identidades:
            try:
                saida.append(uma_empresa(raiz, ident, indice=ix, **kw))
            except Exception as e:
                saida.append(_resposta(
                    ident, modelo.ERRO,
                    "falha inesperada (%s)" % e.__class__.__name__,
                    guardado=False))
        return saida
    finally:
        ix.fechar()


def _resposta(identidade, desfecho, detalhe, guardado, documento_id=""):
    if desfecho == "PULADO":
        return {"identidade": identidade, "desfecho": "PULADO",
                "guardado": False, "documento_id": "",
                "titulo": "Não precisou consultar.",
                "orientacao": detalhe, "do_orgao": "",
                "frase": "Não precisou consultar: %s" % detalhe}
    h = mensagens.humanizar(desfecho, detalhe, ESFERA)
    h.update({"identidade": identidade, "guardado": guardado,
              "documento_id": documento_id,
              "frase": mensagens.frase(desfecho, detalhe, ESFERA)})
    return h
