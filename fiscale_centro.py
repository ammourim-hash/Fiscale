#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fiscale_centro.py — a fila de trabalho do Centro de Operações.

O QUE ESTA CAMADA É
    Uma porta de entrada OPERACIONAL: o que precisa de mão humana hoje, com o
    caminho para resolver. Não é painel financeiro, não tem indicador de
    desempenho e não soma dinheiro.

O QUE ELA DELIBERADAMENTE NÃO FAZ
    **Não calcula nada e não escreve nada.** Só pergunta a quem já sabe: o
    cadastro, o índice da ingestão, o índice da situação fiscal e o estado da
    apuração. Nenhum módulo fiscal foi alterado para alimentar esta tela — e se
    algum dia for, é sinal de que a tela passou a mandar no sistema.

    **Não inventa número.** Cada bloco devolve `valor` OU `indisponivel` com o
    motivo em português. Zero e "não sei" são respostas diferentes, e confundi-las
    num painel é como um verde sem documento: parece informação e não é.

POR QUE UM AGREGADOR NO SERVIDOR, E NÃO N PEDIDOS NA TELA
    A tela já fazia cinco chamadas; somar mais oito faria a Home abrir em
    cascata, e cada falha isolada apareceria como um buraco diferente. Aqui a
    travessia é local (arquivos e SQLite do próprio escritório) e a resposta
    chega inteira, com o motivo de cada ausência.

CUSTO, QUE FOI MEDIDO ANTES DE ESCOLHER A FONTE
    Tudo o que este módulo lê é leitura local e barata: arquivos de estado e
    `COUNT` no SQLite do índice. O que exigiria abrir o acervo documento a
    documento — reprocessar parser, conferir XML — **não** entra aqui: viraria
    uma Home que demora, e uma Home que demora ninguém usa como porta.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent
_BACKEND = str(RAIZ_APP / "nfse" / "backend")
if _BACKEND not in sys.path:
    # O mesmo que `contabil/fiscal.py` faz, e pela mesma razão: o pacote
    # `ingestao` mora no backend do NFS-e e é a camada canônica. Depender do
    # `sys.path` do interpretador faria o módulo funcionar na instalação e
    # falhar no repositório — ou o contrário.
    sys.path.append(_BACKEND)

# Dias em que um certificado já conta como "exige atenção".
DIAS_CERTIFICADO = 30

# Os blocos, na ordem em que a tela os mostra.
BLOCOS = ("empresas_atencao", "documentos_pendentes", "falhas_captura",
          "inconsistencias", "apuracoes_pendentes", "situacao_fiscal",
          "avisos")


def _vazio(fonte: str, motivo: str) -> dict:
    """Um bloco que não pôde ser respondido — e diz por quê."""
    return {"valor": None, "itens": [], "fonte": fonte, "indisponivel": motivo}


def _bloco(valor: int, fonte: str, itens=None, detalhe: str = "") -> dict:
    return {"valor": int(valor), "itens": list(itens or []), "fonte": fonte,
            "indisponivel": "", "detalhe": detalhe}


def _digitos(v) -> str:
    return "".join(c for c in str(v or "") if c.isdigit())


def _mascarar(doc: str) -> str:
    d = _digitos(doc)
    if len(d) == 14:
        return "%s.***.***/%s-%s" % (d[:2], d[8:12], d[12:])
    if len(d) == 11:
        return "***.%s.%s-**" % (d[3:6], d[6:9])
    return d


# ════════════════════════════════════════════════════════════════════════════
#  Empresas que exigem atenção — certificado
# ════════════════════════════════════════════════════════════════════════════
def empresas_atencao(clientes: list, hoje: date | None = None) -> dict:
    """Certificado vencido ou vencendo, e empresa sem certificado.

    O certificado é o que permite capturar: sem ele a empresa para de receber
    documento, e isso não aparece em lugar nenhum até alguém procurar.
    """
    hoje = hoje or date.today()
    limite = hoje + timedelta(days=DIAS_CERTIFICADO)
    itens = []
    for c in clientes or []:
        doc = _digitos(c.get("cnpj") or c.get("documento") or c.get("cpf"))
        nome = (c.get("nome") or c.get("razao_social") or _mascarar(doc))
        # `cert` e `certValidade` são os nomes que o CADASTRO usa — os mesmos
        # que `ingestao/cadastro.py` lê e que a Home já usava no navegador.
        # Inventar `validade`/`cert_validade` aqui produziria uma segunda
        # leitura do mesmo dado, que discordaria da primeira em silêncio.
        val = str(c.get("certValidade") or "")[:10]
        if not doc:
            continue
        if not c.get("cert"):
            itens.append({"empresa": nome, "identidade": _mascarar(doc),
                          "motivo": "sem certificado — esta empresa não captura",
                          "grau": "aviso", "onde": "clientes.html"})
            continue
        if not val:
            # Sem validade lida não é "vencido": é desconhecido, e vai dito.
            itens.append({"empresa": nome, "identidade": _mascarar(doc),
                          "motivo": "certificado sem validade lida",
                          "grau": "aviso", "onde": "clientes.html"})
            continue
        try:
            d = date.fromisoformat(val)
        except ValueError:
            itens.append({"empresa": nome, "identidade": _mascarar(doc),
                          "motivo": "validade do certificado ilegível (%s)" % val,
                          "grau": "aviso", "onde": "clientes.html"})
            continue
        if d < hoje:
            itens.append({"empresa": nome, "identidade": _mascarar(doc),
                          "motivo": "certificado VENCIDO em %s"
                                    % d.strftime("%d/%m/%Y"),
                          "grau": "ruim", "onde": "clientes.html"})
        elif d <= limite:
            itens.append({"empresa": nome, "identidade": _mascarar(doc),
                          "motivo": "certificado vence em %s"
                                    % d.strftime("%d/%m/%Y"),
                          "grau": "aviso", "onde": "clientes.html"})
    return _bloco(len(itens), "cadastro de Clientes (validade do certificado)",
                  itens)


# ════════════════════════════════════════════════════════════════════════════
#  Falhas de captura — quem saiu da automação, e por quê
# ════════════════════════════════════════════════════════════════════════════
def falhas_captura(dados) -> dict:
    """Empresas travadas na ingestão NF-e, pelo domínio que já as conhece.

    A lista vem de `fiscale_decisoes_nfe.listar_pendentes`, que já traz o
    diagnóstico rotulado como HIPÓTESE. Nada aqui promove hipótese a conclusão.
    """
    try:
        import fiscale_decisoes_nfe as dec
        r = dec.listar_pendentes(dados)
    except Exception as e:
        return _vazio("fiscale_decisoes_nfe.listar_pendentes",
                      "não consegui ler o estado da ingestão (%s)"
                      % type(e).__name__)

    itens = []
    for e in r.get("empresas") or []:
        motivo = ("em revisão de sequência" if e.get("em_revisao")
                  else "divergência com a posição informada pela SEFAZ"
                  if e.get("divergencia_externa") else "fora da automação")
        if e.get("decisao_pendente"):
            motivo += " — sem decisão registrada"
        # `empresa` JÁ VEM MASCARADA do domínio; `identidade` é o CNPJ cru e
        # não sai daqui. Usar o cru como fallback espalharia o número pela tela
        # e pelos logs do navegador.
        itens.append({"empresa": e.get("empresa") or "—",
                      "identidade": e.get("empresa") or "",
                      "motivo": motivo, "grau": "ruim",
                      # `nfe_pendencias.html` é página de ADMIN
                      # (`_ADMIN_PAGINAS`). A tela precisa saber disso para não
                      # oferecer ao operador um link que responde 403.
                      "onde": "nfe_pendencias.html", "somente_admin": True})
    return _bloco(len(itens), "ingestão NF-e (checkpoint + operação)", itens)


# ════════════════════════════════════════════════════════════════════════════
#  Documentos pendentes e inconsistências — do índice, com COUNT
# ════════════════════════════════════════════════════════════════════════════
def _identidades(clientes: list) -> list:
    saida = []
    for c in clientes or []:
        d = _digitos(c.get("cnpj") or c.get("documento") or c.get("cpf"))
        if d:
            saida.append((d, c.get("nome") or c.get("razao_social") or _mascarar(d)))
    return saida


def inconsistencias(dados, clientes: list) -> dict:
    """Documentos em QUARENTENA no índice — existem e não foram entendidos.

    `COUNT` por empresa no SQLite local. Não abre XML nenhum: quarentena é
    estado do índice, gravado na entrada.
    """
    try:
        from ingestao import consulta as cq
    except Exception as e:
        return _vazio("índice da ingestão (quarentena)",
                      "pacote de ingestão indisponível (%s)" % type(e).__name__)

    itens, total = [], 0
    for doc, nome in _identidades(clientes):
        try:
            n = cq.contar(dados, doc, cq.Filtro(quarentena=True))
        except Exception:
            # Uma empresa ilegível não pode zerar o bloco das outras.
            continue
        if n:
            total += n
            itens.append({"empresa": nome, "identidade": _mascarar(doc),
                          "motivo": "%d documento(s) em quarentena — o índice "
                                    "não entendeu o conteúdo" % n,
                          "grau": "aviso",
                          "onde": "nfe_documentos.html"})
    return _bloco(total, "índice da ingestão (quarentena)", itens)


def documentos_pendentes(dados) -> dict:
    """A pasta vigiada: o que o último processamento encontrou e o que sobrou.

    Esta é a única fila de ENTRADA que existe hoje. Ela é lida, nunca acionada:
    processar é decisão de quem clica, no módulo.
    """
    # O nome é o do módulo NF-e (`ENTRADA_CONFIG` em `nfse/backend/main.py`).
    # Ele é lido, nunca escrito: a configuração é de lá.
    arq = Path(dados) / "entrada_config.json"
    if not arq.exists():
        return _vazio("pasta vigiada (configuração do módulo NF-e)",
                      "a pasta vigiada ainda não foi configurada nem processada")
    try:
        cfg = json.loads(arq.read_text("utf-8")) or {}
    except Exception as e:
        return _vazio("pasta vigiada",
                      "configuração ilegível (%s)" % type(e).__name__)

    u = cfg.get("ultimo") or {}
    if not u:
        return _bloco(0, "pasta vigiada (%s)" % arq.name, [],
                      detalhe="configurada, ainda sem processamento registrado")
    # A forma é a que `_rodar_pasta_entrada` grava: `{em, resumo}`, e o resumo é
    # o do portão — `rejeitados` é o que ele recusou.
    resumo = u.get("resumo") or {}
    pendentes = int(resumo.get("rejeitados") or 0)
    itens = []
    if pendentes:
        itens.append({"empresa": "—", "identidade": "",
                      "motivo": "%d arquivo(s) recusado(s) no último "
                                "processamento da pasta" % pendentes,
                      "grau": "aviso", "onde": "nfe_documentos.html"})
    quando = str(u.get("em") or "")[:19].replace("T", " ")
    return _bloco(pendentes, "pasta vigiada (%s)" % arq.name, itens,
                  detalhe="último processamento: %s" % (quando or "—"))


# ════════════════════════════════════════════════════════════════════════════
#  Apurações pendentes — do estado da própria apuração
# ════════════════════════════════════════════════════════════════════════════
def apuracoes_pendentes(dados, clientes: list) -> dict:
    """Empresas sem a configuração que a apuração exige para fechar o cálculo.

    Não roda apuração: rodar `classificar()` para a carteira inteira na abertura
    da Home custaria a leitura de todo o cache de NFS-e. O que se lê aqui é o
    ESTADO da apuração — e a ausência de `folha12m` é a pendência que mais
    aparece na prática, porque sem ela o DAS de quem é de Fator R não sai.
    """
    arq = Path(dados) / "state_classificador.json"
    if not arq.exists():
        return _vazio("estado da apuração (state_classificador.json)",
                      "nenhuma apuração foi configurada ainda")
    try:
        cfgs = (json.loads(arq.read_text("utf-8-sig")) or {}).get("cfgs") or {}
    except Exception as e:
        return _vazio("estado da apuração",
                      "estado ilegível (%s)" % type(e).__name__)

    itens = []
    for doc, nome in _identidades(clientes):
        cfg = None
        for chave, v in cfgs.items():
            if _digitos(chave) == doc or _digitos((v or {}).get("cnpj")) == doc:
                cfg = v or {}
                break
        if cfg is None:
            itens.append({"empresa": nome, "identidade": _mascarar(doc),
                          "motivo": "sem configuração de apuração",
                          "grau": "aviso", "onde": "classificador.html"})
        elif not cfg.get("folha12m") and not cfg.get("anexoServico"):
            itens.append({"empresa": nome, "identidade": _mascarar(doc),
                          "motivo": "sem Folha 12 meses nem Anexo fixo — o DAS "
                                    "de Fator R não fecha",
                          "grau": "aviso", "onde": "classificador.html"})
    return _bloco(len(itens), "estado da apuração (state_classificador.json)",
                  itens)


# ════════════════════════════════════════════════════════════════════════════
#  Situação fiscal — os contadores que a carteira já calcula
# ════════════════════════════════════════════════════════════════════════════
def situacao_fiscal(dados, clientes: list) -> dict:
    """Quantas empresas não estão com a regularidade federal resolvida.

    A conta é de `situacao.carteira.montar` — a mesma da tela de Situação
    Fiscal. Um segundo critério aqui faria a Home discordar dela.
    """
    try:
        import situacao.carteira as cart
        import situacao.ciclo as ciclo
        import situacao.indice as sind
    except Exception as e:
        return _vazio("carteira de regularidade",
                      "módulo de situação fiscal indisponível (%s)"
                      % type(e).__name__)

    empresas = [{"identidade": d, "nome": n} for d, n in _identidades(clientes)]
    try:
        with sind.abrir(dados) as ix:
            try:
                canal = cart.canal_federal(ciclo.cofre_do_projeto(dados))
            except Exception:
                canal = cart.canal_federal(None)
            r = cart.montar(ix, empresas, canal)
    except Exception as e:
        return _vazio("carteira de regularidade",
                      "não consegui ler o índice da situação fiscal (%s)"
                      % type(e).__name__)

    c = r.get("contadores") or {}
    pendentes = (int(c.get("com_pendencia") or 0)
                 + int(c.get("nao_consultadas") or 0)
                 + int(c.get("consulta_necessaria") or 0)
                 + int(c.get("erro") or 0))
    itens = []
    for l in r.get("empresas") or []:
        grupo = ((l.get("federal") or {}).get("grupo") or "")
        if grupo and grupo != "regular":
            itens.append({"empresa": l.get("nome") or "—",
                          "identidade": _mascarar(l.get("identidade") or ""),
                          "motivo": ((l.get("federal") or {}).get("rotulo")
                                     or grupo),
                          "grau": "aviso" if grupo == "nao_consultado" else "ruim",
                          "onde": "situacao.html"})
    return _bloco(pendentes, "situacao.carteira.montar", itens,
                  detalhe="%d empresa(s) na carteira" % int(c.get("empresas") or 0))


# ════════════════════════════════════════════════════════════════════════════
#  Avisos operacionais
# ════════════════════════════════════════════════════════════════════════════
def avisos(dados, clientes: list, nfse_no_ar: bool | None = None) -> dict:
    """O que não é fila de trabalho, mas atrapalha o trabalho.

    Backup sem cópia, módulo NFS-e fora do ar, cadastro vazio. Cada aviso diz o
    que fazer; nenhum é decorativo.
    """
    itens = []
    if not clientes:
        itens.append({"motivo": "nenhuma empresa cadastrada — comece pelo "
                                "Clientes", "grau": "aviso",
                      "onde": "clientes.html"})
    backups = Path(dados) / "backups"
    try:
        copias = sorted(backups.glob("*.fbk")) if backups.is_dir() else []
    except Exception:
        copias = []
    if not copias:
        itens.append({"motivo": "nenhuma cópia .fbk na pasta de backups",
                      "grau": "ruim", "onde": "backup.html"})
    else:
        mais_nova = max(p.stat().st_mtime for p in copias)
        dias = (datetime.now() - datetime.fromtimestamp(mais_nova)).days
        if dias > 30:
            itens.append({"motivo": "o backup mais recente tem %d dias" % dias,
                          "grau": "aviso", "onde": "backup.html"})
    if nfse_no_ar is False:
        itens.append({"motivo": "módulo NFS-e não está respondendo",
                      "grau": "ruim", "onde": "nfse.html"})
    return _bloco(len(itens), "pasta de dados + sonda do módulo", itens)


# ════════════════════════════════════════════════════════════════════════════
#  A fila inteira
# ════════════════════════════════════════════════════════════════════════════
def fila(dados, clientes: list, *, nfse_no_ar: bool | None = None,
         hoje: date | None = None) -> dict:
    """Todos os blocos. Um bloco que falha não derruba os outros."""
    def seguro(nome, fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            return _vazio(nome, "falhou ao montar (%s)" % type(e).__name__)

    blocos = {
        "empresas_atencao": seguro("cadastro", empresas_atencao, clientes, hoje),
        "documentos_pendentes": seguro("pasta vigiada", documentos_pendentes, dados),
        "falhas_captura": seguro("ingestão NF-e", falhas_captura, dados),
        "inconsistencias": seguro("índice", inconsistencias, dados, clientes),
        "apuracoes_pendentes": seguro("apuração", apuracoes_pendentes, dados, clientes),
        "situacao_fiscal": seguro("situação fiscal", situacao_fiscal, dados, clientes),
        "avisos": seguro("avisos", avisos, dados, clientes, nfse_no_ar),
    }
    # O total conta só o que foi possível apurar. Somar ausência como zero
    # produziria uma Home tranquila num sistema que ninguém conseguiu medir.
    conhecidos = [b["valor"] for b in blocos.values() if b["valor"] is not None]
    return {
        "blocos": blocos,
        "ordem": list(BLOCOS),
        "total_pendencias": sum(conhecidos),
        "blocos_indisponiveis": [k for k, b in blocos.items()
                                 if b["valor"] is None],
        "empresas_no_cadastro": len(_identidades(clientes)),
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "nada_inventado": ("cada bloco declara a fonte em `fonte`; o que não "
                           "pôde ser medido vem com `indisponivel`, nunca zero"),
    }
