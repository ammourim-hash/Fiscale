"""
servico_distribuicao.py — a ÚNICA porta para o `NFeDistribuicaoDFe`.

POR QUE ESTE MÓDULO EXISTE
    Havia três caminhos capazes de abrir uma sessão real com a Distribuição
    DF-e: o controlador (governado), o utilitário manual (auditado, mas sem
    cadência) e a rota `POST /api/nfe/sincronizar`, que chamava o `nfe.py`
    legado — com checkpoint próprio, sem ato `CONSULTA` e sem cooldown.

    Nenhum deles causou os `656` observados em 17/08/2026, e isso foi
    conferido pelos carimbos de tempo. Mas enquanto três portas existirem, a
    frase "toda chamada real é governada e auditável" é falsa por construção —
    basta um clique numa tela para queimar cota do CNPJ sem deixar rastro.

    Aqui a política mora **uma vez**. Quem quiser consultar passa por
    `consultar_empresa()`; não há caminho curto.

O QUE ESTA CAMADA GARANTE, SEMPRE E NA MESMA ORDEM
    1. elegibilidade (certificado, checkpoint, divergência, revisão de
       sequência, cooldown, teto de frequência)
    2. trava por `(empresa, serviço, ambiente)` — nunca duas ao mesmo tempo
    3. checkpoint OFICIAL do motor ING; o `<cnpj>/nfe/estado.json` legado
       nunca é lido como posição operacional
    4. ato `CONSULTA` na trilha append-only, um por chamada
    5. estado operacional persistido antes de devolver
    6. tratamento do `656` com subtipo, e a janela contada do próprio `656`

O QUE ELA NÃO FAZ
    Não decide ORDEM nem ORÇAMENTO — isso é do ciclo, e continua no
    `controlador`. Não agenda. Não tem laço. Uma chamada a
    `consultar_empresa()` é uma unidade de trabalho de uma empresa.

SOBRE "O USUÁRIO CLICOU EM SINCRONIZAR"
    Clique não é autorização para ignorar política. Empresa em cooldown, em
    divergência externa ou em revisão de sequência recebe **estado e motivo**,
    não uma chamada de rede. Override administrativo, se um dia existir, será
    decisão explícita e auditável — não um efeito colateral de botão.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import checkpoint as cpm
from . import operacao as op
from . import pipeline as pipe
from .ambiente import PRODUCAO, resolver as resolver_ambiente
from .conectores import nfe_dfe as N
from .distribuicao import higienizar
from .identidade import normalizar

SERVICO_PADRAO = cpm.NFE_DISTRIBUICAO

# Motivo pelo qual a consulta não saiu. `AUTORIZADA` é o único que vira rede.
AUTORIZADA = "AUTORIZADA"
RECUSADA = "RECUSADA_POR_POLITICA"
FALHA_PREPARO = "FALHA_AO_PREPARAR"


@dataclass
class ResultadoConsulta:
    """O que aconteceu com UMA empresa. Sem XML, sem segredo, sem CNPJ inteiro."""
    identidade_mascarada: str = ""
    autorizada: bool = False
    consultada: bool = False
    decisao: str = RECUSADA
    estado: str = ""
    motivo: str = ""
    detalhe: str = ""
    segundos_para_liberar: int = 0

    cstat: str = ""
    subtipo: str = ""
    chamadas: int = 0
    documentos: int = 0
    nsu_antes: str = ""
    nsu_depois: str = ""
    erro: str = ""
    falha_de_persistencia: bool = False

    def resumo(self) -> dict:
        return {"empresa": self.identidade_mascarada, "autorizada": self.autorizada,
                "consultada": self.consultada, "decisao": self.decisao,
                "estado": self.estado, "motivo": self.motivo or None,
                "detalhe": self.detalhe or None, "cstat": self.cstat or None,
                "subtipo": self.subtipo or None, "documentos": self.documentos,
                "novos": self.documentos,
                "segundos_para_liberar": self.segundos_para_liberar,
                "checkpoint_antes": self.nsu_antes or None,
                "checkpoint_depois": self.nsu_depois or None,
                "erro": self.erro or None}


def fabrica_padrao(cad, dados_dir, cuf: str = ""):
    """A `Fonte` real, com mTLS. É o único lugar que monta transporte de rede.

    Recebe o cadastro já carregado para que a rota HTTP, o ciclo e a ferramenta
    manual usem exatamente a mesma construção — inclusive o `cUFAutor`, que vem
    do autor da consulta e nunca da UF da empresa."""
    def _f(identidade, ambiente):
        return N.criar(cad, identidade, dados_dir=dados_dir, ambiente=ambiente,
                       cuf=cuf)
    return _f


def consultar_empresa(dados_dir, identidade, fabrica_fonte, *,
                      servico: str = SERVICO_PADRAO, ambiente=PRODUCAO,
                      politica=None, cad=None, agora: datetime | None = None,
                      repo_op=None, repo_cp=None,
                      avaliar=None) -> ResultadoConsulta:
    """Consulta UMA empresa, com toda a política aplicada. Nunca levanta.

    `avaliar` é injetado pelo controlador para não criar import circular; sem
    ele, a função importa a avaliação canônica. Em nenhum caso existe uma
    segunda cópia da regra de elegibilidade."""
    from . import cadastro as cad_mod
    from . import controlador as ctl

    politica = politica if politica is not None else ctl.POLITICA_PADRAO
    avaliar = avaliar or ctl.avaliar
    q = agora or datetime.now(timezone.utc)
    dados = Path(dados_dir)
    amb = resolver_ambiente(ambiente)
    ident = normalizar(identidade)
    cad = cad if cad is not None else cad_mod.carregar(dados)
    repo_op = repo_op or op.RepositorioOperacao(dados)
    repo_cp = repo_cp or cpm.RepositorioCheckpoint(dados)

    el = avaliar(dados, identidade, cad=cad, servico=servico, ambiente=amb,
                 politica=politica, agora=q)
    r = ResultadoConsulta(identidade_mascarada=el.identidade_mascarada,
                          estado=el.estado, motivo=el.motivo,
                          detalhe=el.detalhe,
                          segundos_para_liberar=el.segundos_para_liberar)
    if not el.pode:
        # Nem transporte é construído. A recusa acontece ANTES de existir
        # qualquer objeto capaz de falar com a SEFAZ.
        return r

    r.autorizada = True
    r.decisao = AUTORIZADA
    cp_antes = repo_cp.carregar(ident.valor, servico, amb)
    r.nsu_antes = r.nsu_depois = cp_antes.ult_nsu
    estado_op = repo_op.carregar(ident.valor, servico, amb)

    try:
        fonte = fabrica_fonte(ident.valor, amb)
        estado_op.registrar_consulta(q)
        saida = pipe.ingerir(dados, ident.valor, fonte, ambiente=amb,
                             limite_lotes=politica.max_lotes_por_empresa,
                             usar_trava=politica.usar_trava)
    except Exception as exc:
        estado_op.registrar_falha(FALHA_PREPARO, agora=q)
        estado_op.bloquear(q + timedelta(
            minutes=politica.backoff_para(estado_op.falhas_consecutivas)),
            "falha ao preparar a consulta")
        repo_op.salvar(estado_op)
        r.decisao = FALHA_PREPARO
        r.estado, r.erro = op.ERRO_TEMPORARIO, higienizar(exc)
        r.motivo = "falha ao preparar a consulta"
        return r

    exe = saida.execucao
    cp = repo_cp.carregar(ident.valor, servico, amb)
    r.consultada = True
    r.chamadas = saida.consultas_registradas
    r.nsu_depois = cp.ult_nsu
    r.documentos = exe.documentos if exe else 0
    ultima = list(getattr(fonte, "tentativas", []) or [])[-1:] or [{}]
    t = ultima[0]
    r.cstat = str(t.get("cstat") or "")

    estado_op.ciclos_executados += 1
    if r.cstat == "656":
        # A janela conta do PRÓPRIO 656 — e nunca antes da referência do
        # ciclo. Janela que começa cedo termina cedo, e terminar cedo é
        # exatamente o que provoca a rejeição seguinte.
        carimbo = op._de_iso(t.get("rejeitado_em") or t.get("inicio") or "")
        rejeitado_em = max(carimbo, q) if carimbo else q
        subtipo = str(t.get("subtipo_consumo")
                      or N.classificar_consumo_indevido(t.get("xmotivo") or ""))
        r.subtipo = subtipo
        estado_op.registrar_falha("CONSUMO_INDEVIDO", cstat="656", agora=q)
        estado_op.registrar_656(
            subtipo=subtipo, xmotivo=t.get("xmotivo") or "",
            nsu_enviado=t.get("nsu_enviado") or "",
            checkpoint_local=cp_antes.ult_nsu,
            ult_nsu=t.get("ult_nsu") or "", max_nsu=t.get("max_nsu") or "",
            quando=rejeitado_em)
        estado_op.bloquear(
            rejeitado_em + timedelta(
                minutes=politica.cooldown_consumo_indevido_min),
            f"cStat 656 — {subtipo}")
        estado_op.origem_bloqueio = "rejeição 656 do Ambiente Nacional"

        if subtipo == N.SEQUENCIA_656:
            estado_op.exigir_revisao_de_sequencia(
                "656 de sequência: o Ambiente Nacional questionou o NSU "
                "enviado; a automação não retoma sozinha")
            r.estado = op.REVISAO_DE_SEQUENCIA
            r.motivo = ("656 de sequência — exige conferência humana antes "
                        "de nova consulta")
        else:
            r.estado = op.BLOQUEADO_CONSUMO_INDEVIDO
            r.motivo = f"656 — {subtipo}"
    elif exe and exe.sucesso:
        estado_op.registrar_sucesso(r.cstat, exe.status, r.documentos, q)
        if cp.estado_sincronismo == cpm.SINCRONISMO_DIVERGENCIA:
            r.estado = op.DIVERGENCIA_EXTERNA
            estado_op.bloquear(q + timedelta(
                minutes=politica.cooldown_consumo_indevido_min),
                "divergência externa — exige decisão")
        elif cp.em_dia:
            estado_op.ultima_sincronia_confirmada_em = q.isoformat(
                timespec="seconds")
            estado_op.bloquear(
                q + timedelta(minutes=politica.cooldown_em_dia_min),
                "em dia — aguardando a próxima janela")
            r.estado = op.EM_SINCRONIA
        else:
            estado_op.bloquear(
                q + timedelta(minutes=politica.cooldown_pendente_min),
                "ainda há documentos pendentes")
            r.estado = op.PENDENTE
    else:
        estado_op.registrar_falha(exe.motivo_parada if exe else "?",
                                  cstat=r.cstat, agora=q)
        estado_op.bloquear(q + timedelta(
            minutes=politica.backoff_para(estado_op.falhas_consecutivas)),
            f"backoff após {estado_op.falhas_consecutivas} falha(s)")
        r.estado = (op.CREDENCIAL_INDISPONIVEL
                    if exe and exe.status == cpm.CREDENCIAL_INVALIDA
                    else op.ERRO_TEMPORARIO)
        r.erro = exe.erro if exe else ""

    # ── persistir ANTES de devolver ───────────────────────────────────────
    # Checkpoint e auditoria já foram gravados dentro do `ingerir`; falta o
    # estado operacional. Quem chamou só recebe a resposta depois que o disco
    # já sabe de tudo.
    repo_op.salvar(estado_op)

    from . import distribuicao as dist
    r.falha_de_persistencia = bool(exe and exe.motivo_parada == dist.FIM_PERSISTENCIA)
    return r
