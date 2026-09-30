# -*- coding: utf-8 -*-
"""credencial.py — quem assina o pedido ao SERPRO, e com qual certificado.

O PROBLEMA CONCRETO QUE ISTO RESOLVE
    As procurações eletrônicas das empresas não estão no e-CNPJ do escritório:
    estão no nome de uma PESSOA FÍSICA. Então o `autorPedidoDados` do Integra
    Contador não é o contratante — são dois documentos diferentes, e quem
    assume que são o mesmo escreve um conector que funciona no trial e é
    recusado em produção.

    E isso muda: procurador sai, procurador entra, certificado vence, uma
    segunda pessoa passa a assinar por parte da carteira. Por isso **nenhuma
    identidade mora no código**. O que mora aqui é a FORMA de descrevê-la.

    contratante        quem tem o contrato SERPRO   (CNPJ do escritório)
    procurador         quem assina o pedido          (CPF, com procuração)
    certificado_id     qual certificado usar         (aponta o cadastro)

SEGREDO NÃO MORA AQUI, E NEM EM CLARO
    `consumerKey` e `consumerSecret` ficam no arquivo **protegidos**, e este
    módulo não sabe protegê-los: ele recebe `proteger`/`desproteger` de fora
    (hoje, `nfse/backend/seguranca.py`). São duas razões:

      • o pacote não depende do backend do NFS-e para ser importado e testado;
      • existe UM cofre no projeto, e não dois com regras diferentes.

    Sem um `desproteger`, o segredo simplesmente não é devolvido. E `salvar()`
    recusa gravar segredo em claro — recusa em vez de avisar, porque aviso em
    log é coisa que ninguém lê antes do vazamento.

O ARQUIVO
    <FISCALE_DADOS>/situacao_credenciais.json

    {
      "contratante": "00000000000000",
      "ambiente": "trial",
      "padrao": "fulano",
      "procuradores": [
        {"apelido": "fulano", "documento": "...", "certificado_id": "...",
         "ativo": true, "observacao": "procurações das 19 empresas"}
      ],
      "segredos": {"consumer_key": {"protegido": "..."}}
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ARQUIVO = "situacao_credenciais.json"

TRIAL = "trial"
PRODUCAO = "producao"
AMBIENTES = (TRIAL, PRODUCAO)


class SemCredencial(Exception):
    """Não há credencial utilizável para o que se pediu."""


class SegredoEmClaro(Exception):
    """Alguém tentou gravar segredo sem cofre. Recusado."""


@dataclass
class Procurador:
    """Quem assina o pedido. Pode ser pessoa física — e normalmente é."""

    apelido: str
    documento: str = ""
    certificado_id: str = ""
    ativo: bool = True
    observacao: str = ""

    @property
    def digitos(self) -> str:
        return "".join(c for c in str(self.documento or "") if c.isdigit())

    def valido(self) -> bool:
        return bool(self.apelido) and len(self.digitos) in (11, 14)


@dataclass
class Credencial:
    """O conjunto pronto para uma chamada."""

    contratante: str
    procurador: Procurador
    ambiente: str = TRIAL
    certificado_id: str = ""
    segredos: dict = field(default_factory=dict)

    @property
    def autor(self) -> str:
        """`autorPedidoDados` — o procurador, NÃO o contratante.

        Devolver o contratante aqui seria o erro silencioso: o trial aceita
        (ele aceita o cenário fictício e mais nada), e a produção recusa por
        falta de procuração — num ponto do código longe daqui.
        """
        return self.procurador.digitos

    @property
    def eh_producao(self) -> bool:
        return self.ambiente == PRODUCAO


class Cofre:
    """Lê e escreve o arquivo de credenciais. Aceita VÁRIOS procuradores."""

    def __init__(self, caminho, desproteger=None, proteger=None):
        self.caminho = Path(caminho)
        self._desproteger = desproteger
        self._proteger = proteger
        self.dados = self._ler()

    def _ler(self) -> dict:
        try:
            d = json.loads(self.caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    # ── leitura ──────────────────────────────────────────────────────────
    def procuradores(self) -> list:
        saida = []
        for p in (self.dados.get("procuradores") or []):
            if isinstance(p, dict):
                saida.append(Procurador(
                    apelido=str(p.get("apelido") or ""),
                    documento=str(p.get("documento") or ""),
                    certificado_id=str(p.get("certificado_id") or ""),
                    ativo=bool(p.get("ativo", True)),
                    observacao=str(p.get("observacao") or "")))
        return saida

    def escolher(self, apelido: str = "") -> Credencial:
        """A credencial de `apelido`, ou a padrão. Levanta `SemCredencial`.

        NÃO ESCOLHE SOZINHO QUANDO HÁ AMBIGUIDADE. Com vários procuradores e
        sem padrão declarado, pegar "o primeiro" faria a assinatura mudar de
        pessoa quando alguém reordenasse o arquivo — e ninguém procuraria ali.
        """
        contratante = "".join(c for c in str(self.dados.get("contratante") or "")
                              if c.isdigit())
        if len(contratante) != 14:
            raise SemCredencial(
                "O contratante do Integra Contador não está configurado "
                "(esperado o CNPJ de quem tem o contrato).")

        disponiveis = [p for p in self.procuradores() if p.ativo and p.valido()]
        if not disponiveis:
            raise SemCredencial(
                "Nenhum procurador ativo configurado. As procurações ficam no "
                "nome de quem assina, e sem isso o pedido é recusado.")

        alvo = apelido or str(self.dados.get("padrao") or "")
        if alvo:
            for p in disponiveis:
                if p.apelido == alvo:
                    escolhido = p
                    break
            else:
                raise SemCredencial(
                    "Não há procurador ativo chamado %r." % alvo)
        elif len(disponiveis) == 1:
            escolhido = disponiveis[0]
        else:
            raise SemCredencial(
                "Há %d procuradores ativos e nenhum padrão declarado — diga "
                "qual deve assinar." % len(disponiveis))

        ambiente = str(self.dados.get("ambiente") or TRIAL).lower()
        if ambiente not in AMBIENTES:
            raise SemCredencial("Ambiente desconhecido: %r" % ambiente)

        return Credencial(contratante=contratante, procurador=escolhido,
                          ambiente=ambiente,
                          certificado_id=escolhido.certificado_id,
                          segredos=dict(self.dados.get("segredos") or {}))

    def segredo(self, nome: str) -> str:
        """O segredo em claro, se houver cofre para abri-lo.

        Sem `desproteger`, devolve vazio — e vazio faz a chamada falhar com
        "sem credencial", que é melhor que falhar com um segredo errado.
        """
        bruto = (self.dados.get("segredos") or {}).get(nome)
        if not isinstance(bruto, dict):
            return ""
        if "protegido" in bruto:
            if self._desproteger is None:
                return ""
            try:
                return self._desproteger(bruto["protegido"]) or ""
            except Exception:
                return ""
        return ""

    # ── escrita ──────────────────────────────────────────────────────────
    def guardar_segredo(self, nome: str, valor: str) -> None:
        if self._proteger is None:
            raise SegredoEmClaro(
                "Não há cofre configurado: gravar %r em claro seria pôr a "
                "credencial do SERPRO num JSON legível." % nome)
        self.dados.setdefault("segredos", {})[nome] = {
            "protegido": self._proteger(valor)}

    def salvar(self) -> None:
        """Grava o arquivo. Recusa se algum segredo estiver em claro."""
        for nome, v in (self.dados.get("segredos") or {}).items():
            if not isinstance(v, dict) or "protegido" not in v:
                raise SegredoEmClaro(
                    "O segredo %r não está protegido; nada foi gravado." % nome)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho.with_suffix(".parcial")
        tmp.write_text(json.dumps(self.dados, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.caminho)


def abrir(raiz, desproteger=None, proteger=None) -> Cofre:
    return Cofre(Path(raiz) / ARQUIVO, desproteger=desproteger,
                 proteger=proteger)
