#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""homologacao_sitfis.py — a credencial de PRODUÇÃO do Integra Contador funciona?

    python homologacao_sitfis.py                       só autentica
    python homologacao_sitfis.py --consultar 00000000000000
                                                       autentica + UM pedido de
                                                       protocolo SITFIS (/Apoiar)

POR QUE ESTE SCRIPT EXISTE
    A Situação Fiscal passou a depender só do SITFIS. Antes de soltar o lote da
    carteira, é preciso saber — isoladamente — se a credencial de produção
    abre a porta: consumerKey/consumerSecret do cofre, e-CNPJ do contratante no
    handshake, `access_token` e `jwt_token` devolvidos. Descobrir isso no meio
    de um lote de 20 empresas é descobrir tarde e com 20 recusas no histórico.

O QUE ELE FAZ
    1. abre o cofre DPAPI do projeto (`situacao.ciclo.cofre_do_projeto`) — o
       MESMO que o sistema usa; não há segundo armazenamento de credencial;
    2. confere contratante, procurador, ambiente e a PRESENÇA das chaves;
    3. gera o token no `/authenticate` (`situacao.autenticacao`);
    4. com `--consultar`, faz UM `SOLICITARPROTOCOLO91` para um CNPJ — o passo
       mais barato que prova que o token e a procuração valem no SITFIS. Não
       emite relatório, não grava no envelope e não roda a carteira.

O QUE ELE **NUNCA** FAZ
    • Não imprime token, chave nem segredo. Mostra TAMANHO, VALIDADE e uma
      IMPRESSÃO DIGITAL (SHA-256 truncado): isso prova que o token existe e
      permite comparar duas execuções, sem deixar credencial viva no terminal,
      no histórico do console ou num log colado em chamado.
    • Não cai no token público do trial quando falta credencial de produção.
      Essa reserva silenciosa já custou uma chamada real em 08/09/2026
      (`HTTP 403 code 900908`) — ver `situacao/ciclo.py`.
    • Não pede nem grava segredo. Para gravar as chaves: `configurar_serpro.bat`.

CÓDIGOS DE SAÍDA
    0 ok · 2 cofre/procurador/contratante · 3 ambiente não é produção
    4 faltam consumerKey/consumerSecret · 5 autenticação recusada
    6 CNPJ inválido · 7 o SITFIS recusou ou não respondeu o protocolo

RASTRO
    Cada execução acrescenta UMA linha em `<dados>/situacao_homologacao.log`
    (JSON): quando, quem (usuário do Windows), contratante, contribuinte,
    etapa e resultado. Nenhum token entra nela.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import hashlib
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

ARQ_LOG = "situacao_homologacao.log"

OK, E_COFRE, E_AMBIENTE, E_CHAVES, E_AUTH, E_CNPJ, E_SITFIS = 0, 2, 3, 4, 5, 6, 7


def impressao_digital(valor: str) -> str:
    """SHA-256 truncado. Identifica o token sem revelá-lo."""
    if not valor:
        return "—"
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:16]


def _cnpj_valido(c: str) -> bool:
    c = "".join(ch for ch in str(c or "") if ch.isdigit())
    if len(c) != 14 or c == c[0] * 14:
        return False
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(c[:tam], pesos)) % 11
        if int(c[tam]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def _registrar(raiz, linha: dict) -> None:
    """Uma linha de rastro. Falhar aqui não derruba a homologação."""
    try:
        linha = dict(linha, quando=_dt.datetime.now().isoformat(timespec="seconds"))
        try:
            linha["usuario_windows"] = getpass.getuser()
        except Exception:
            linha["usuario_windows"] = ""
        with open(Path(raiz) / ARQ_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    except OSError:
        pass


def homologar(raiz, consultar: str = "", cofre=None, fabrica_autenticador=None,
              transporte=None, saida=print, agora=time.time) -> int:
    """A homologação inteira. Tudo que toca rede ou cofre é injetável."""
    from situacao import autenticacao as _auth
    from situacao import ciclo as _ciclo
    from situacao import credencial as _cred
    from situacao.fontes import sitfis as _sitfis

    rastro = {"etapa": "", "resultado": "", "contribuinte": consultar or ""}

    def fim(codigo, etapa, resultado):
        rastro.update(etapa=etapa, resultado=resultado, codigo=codigo)
        _registrar(raiz, rastro)
        return codigo

    saida("== Homologação do Integra Contador (SITFIS) ==")
    saida("dados: %s" % raiz)

    # ── 1 · cofre ────────────────────────────────────────────────────────
    cofre = cofre or _ciclo.cofre_do_projeto(raiz)
    dados = getattr(cofre, "dados", {}) or {}
    presentes = sorted((dados.get("segredos") or {}).keys())
    saida("ambiente: %s" % (dados.get("ambiente") or "—"))
    saida("segredos presentes no cofre: %s" % (", ".join(presentes) or "nenhum"))
    try:
        cred = cofre.escolher()
    except _cred.SemCredencial as e:
        saida("FALHOU  cofre: %s" % e)
        return fim(E_COFRE, "cofre", str(e))
    rastro["contratante"] = cred.contratante
    saida("contratante: %s · procurador: %s (%d dígitos)" % (
        cred.contratante, cred.procurador.apelido, len(cred.autor)))

    if not cred.eh_producao:
        msg = ("o cofre está no ambiente de demonstração; a homologação é da "
               "credencial de PRODUÇÃO")
        saida("PARADO  %s." % msg)
        return fim(E_AMBIENTE, "ambiente", msg)

    # ── 2 · chaves (presença, sem imprimir) ──────────────────────────────
    faltam = [n for n in ("consumer_key", "consumer_secret")
              if not cofre.segredo(n)]
    if faltam:
        msg = ("faltam %s no cofre (ou o cofre desta máquina não consegue "
               "abri-los). Grave com configurar_serpro.bat" % " e ".join(faltam))
        saida("PARADO  %s. Nada foi enviado ao SERPRO." % msg)
        return fim(E_CHAVES, "chaves", msg)

    # ── 3 · autenticação ─────────────────────────────────────────────────
    saida("autenticando no %s ..." % _auth.URL)
    try:
        if fabrica_autenticador is not None:
            autenticador = fabrica_autenticador(raiz, cred, cofre)
        else:
            autenticador = _auth.de_credencial(raiz, cred, cofre, agora=agora)
        ficha = autenticador.ficha()
    except _auth.ErroAutenticacao as e:
        # A mensagem do módulo nunca contém segredo (ver autenticacao.py).
        saida("FALHOU  autenticação: %s" % e)
        return fim(E_AUTH, "autenticacao", str(e))

    restam = max(0, int(ficha.expira_em - agora()))
    expira = _dt.datetime.fromtimestamp(ficha.expira_em).strftime("%d/%m/%Y %H:%M:%S")
    saida("OK      token gerado (%s)" % ficha.tipo)
    saida("        access_token: %d caracteres · impressão digital %s"
          % (len(ficha.access_token), impressao_digital(ficha.access_token)))
    saida("        jwt_token:    %s" % (
        "%d caracteres · impressão digital %s" % (
            len(ficha.jwt_token), impressao_digital(ficha.jwt_token))
        if ficha.jwt_token else "NÃO VEIO — produção exige; o SITFIS vai recusar"))
    saida("        válido até %s (%d s)" % (expira, restam))
    rastro.update(access_token_sha16=impressao_digital(ficha.access_token),
                  jwt_presente=bool(ficha.jwt_token), expira_em=expira)

    if not consultar:
        saida("Homologação da autenticação concluída. Nenhuma consulta foi feita.")
        return fim(OK, "autenticacao", "token gerado")

    # ── 4 · um pedido de protocolo, e só ─────────────────────────────────
    cnpj = "".join(ch for ch in consultar if ch.isdigit())
    if not _cnpj_valido(cnpj):
        saida("PARADO  CNPJ inválido para consulta: %r" % consultar)
        return fim(E_CNPJ, "consulta", "cnpj invalido")
    if not ficha.jwt_token:
        msg = "sem jwt_token o SITFIS de produção recusa; consulta não enviada"
        saida("PARADO  %s." % msg)
        return fim(E_SITFIS, "consulta", msg)

    conector = _sitfis.de_credencial(
        cred, transporte=transporte or _sitfis.transporte_requests(),
        token=ficha.access_token, jwt=ficha.jwt_token)
    pedido = _sitfis.montar_pedido(cred.contratante, cred.autor, cnpj,
                                   _sitfis.SERVICO_PROTOCOLO)
    saida("SITFIS  %s/Apoiar para %s ..." % (conector.base, cnpj))
    try:
        st, corpo, _bruto = conector._chamar("/Apoiar", pedido)
    except Exception as e:
        msg = "sem resposta do SITFIS (%s)" % e.__class__.__name__
        saida("FALHOU  %s" % msg)
        return fim(E_SITFIS, "consulta", msg)

    # O estado real vem do CORPO, não do HTTP (memória: o erro vem com 200).
    if corpo is None:
        msg = "resposta não é JSON (HTTP %s)" % st
        saida("FALHOU  %s" % msg)
        return fim(E_SITFIS, "consulta", msg)
    texto = _sitfis.mensagem_de(corpo)
    protocolo = _sitfis.ler_dados(corpo).get("protocoloRelatorio") or ""
    if corpo.get("status") != 200 or not protocolo:
        msg = "status %s: %s" % (corpo.get("status"), texto or "sem mensagem")
        saida("FALHOU  SITFIS recusou o protocolo — %s" % msg)
        return fim(E_SITFIS, "consulta", msg)
    saida("OK      protocolo recebido (%d caracteres) · %s" % (len(protocolo), texto))
    saida("Homologação concluída: token e procuração aceitos pelo SITFIS.")
    return fim(OK, "consulta", "protocolo recebido")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--consultar", metavar="CNPJ", default="",
                    help="faz UM pedido de protocolo SITFIS para este CNPJ")
    ap.add_argument("--dados", default=None,
                    help="raiz de dados (padrão: a do Fiscale desta máquina)")
    a = ap.parse_args(argv)
    if a.dados:
        raiz = Path(a.dados)
    else:
        import fiscale_dados
        raiz = Path(fiscale_dados.raiz())
    return homologar(raiz, consultar=a.consultar)


if __name__ == "__main__":
    sys.exit(main())
