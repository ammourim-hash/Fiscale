#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fiscale_decisoes_nfe.py — decisões humanas sobre a ingestão NF-e, sem edição de JSON.

O PROBLEMA QUE ISTO RESOLVE
    Oito empresas estão em `revisao_sequencia` desde 06/09/2026 — a trava que
    o tempo não abre. O domínio SEMPRE teve as duas funções certas para sair
    dela (`operacao.encerrar_revisao_de_sequencia` e
    `checkpoint.marcar_inicio_cobertura`), mas nenhuma rota, nenhuma tela e
    nenhum comando as chamava: elas existiam só para os testes. Na prática, a
    única "saída" era abrir o `.operacao.json` num editor e mexer à mão — que
    é exatamente o que não pode acontecer com o estado que decide se um
    documento fiscal será buscado ou pulado para sempre.

    Este módulo é o caminho administrativo que faltava. Ele não inventa regra
    nova: chama as funções do domínio, na ordem do domínio, com a trava do
    domínio.

O QUE ELE **NÃO** FAZ, E É O MAIS IMPORTANTE
    • **Não fala com a SEFAZ.** Nenhuma função aqui abre soquete. Encerrar a
      revisão devolve a empresa à fila de elegibilidade — a consulta seguinte
      continua sendo uma decisão separada, sujeita a cooldown, teto e trava.
    • **Não recebe `ult_nsu`, `max_nsu`, caminho de arquivo nem estado.** O
      número usado no marco de cobertura vem do PRÓPRIO checkpoint
      (`nsu_observado_sefaz`), nunca de quem chama. Não há parâmetro por onde
      passar posição — e há uma conferência final que levanta se o `ult_nsu`
      mudar.
    • **Não transforma hipótese em fato.** `POSSIVEL_CONSUMIDOR_EXTERNO` é
      hipótese registrada pelo sistema; não existe campo para o operador
      "confirmar" quem consome, porque o FISCALE não tem como saber.
    • **Não decide sozinho.** Justificativa é obrigatória, o operador é
      registrado, e o estado de antes e o de depois vão para a trilha.

REVERSIBILIDADE
    Encerrar a revisão é reversível pelo domínio: `exigir_revisao_de_sequencia`
    põe a empresa de volta na trava, e `reabrir_revisao()` abaixo é isso e nada
    mais. O marco de cobertura **não tem** função oficial de desfazer, e este
    módulo não inventa uma: ele é um registro histórico do que se decidiu, e
    apagar registro histórico seria pior que o problema.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent
_CAMINHO_MODULO = str(RAIZ_APP / "nfse" / "backend")
if _CAMINHO_MODULO not in sys.path:
    # Mesmo padrão do `piloto_nfe.py` e do `consulta_real_nfe.py`: o pacote de
    # ingestão mora no módulo NFS-e e é importado por caminho, não copiado.
    sys.path.insert(0, _CAMINHO_MODULO)

SERVICO_PADRAO = "NFE_DISTRIBUICAO"
AMBIENTE_PADRAO = "producao"

# Tipos de decisão — vocabulário fechado. Quem inventar um terceiro tem de
# vir aqui, e isso é de propósito.
TIPO_ENCERRAR_REVISAO = "REVISAO_SEQUENCIA_ENCERRADA"
TIPO_REABRIR_REVISAO = "REVISAO_SEQUENCIA_REABERTA"
TIPO_MARCO_COBERTURA = "INICIO_COBERTURA_MARCADO"
# "Olhei, não há evidência suficiente, e decido manter fora da automação."
# É a única decisão que não muda nada além do próprio registro — e existe
# justamente para que "ninguém olhou" pare de se parecer com "já foi olhado".
TIPO_MANTER_BLOQUEIO = "BLOQUEIO_MANTIDO_SEM_EVIDENCIA"
TIPOS = (TIPO_ENCERRAR_REVISAO, TIPO_REABRIR_REVISAO, TIPO_MARCO_COBERTURA,
         TIPO_MANTER_BLOQUEIO)

# Justificativa: curta demais não é justificativa, é formalidade. Longa demais
# vira despejo de texto num campo que a auditoria vai carregar para sempre.
JUSTIFICATIVA_MINIMA = 15
JUSTIFICATIVA_MAXIMA = 500

# De onde veio a informação que embasa o marco de cobertura. Lista fechada:
# "o cliente disse" e "eu deduzi" são coisas diferentes, e daqui a um ano
# ninguém vai lembrar qual das duas foi.
ORIGEM_CLIENTE = "INFORMADO_PELO_CLIENTE"
ORIGEM_CONTADOR_ANTERIOR = "INFORMADO_PELO_CONTADOR_ANTERIOR"
ORIGEM_DOCUMENTO_RECEBIDO = "DOCUMENTO_RECEBIDO_DE_OUTRA_FONTE"
ORIGEM_ANALISE_INTERNA = "ANALISE_INTERNA_DO_ESCRITORIO"
ORIGENS = (ORIGEM_CLIENTE, ORIGEM_CONTADOR_ANTERIOR,
           ORIGEM_DOCUMENTO_RECEBIDO, ORIGEM_ANALISE_INTERNA)


class DecisaoRecusada(Exception):
    """A decisão não se aplica ao estado atual. `motivo` é código, não frase."""

    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(detalhe or motivo)
        self.motivo = motivo
        self.detalhe = detalhe


# ── higiene de entrada ─────────────────────────────────────────────────────
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def limpar_texto(t) -> str:
    """Texto de operador: sem caractere de controle, sem espaço sobrando."""
    t = unicodedata.normalize("NFC", str(t or ""))
    return _CONTROLE.sub("", t).strip()


def validar_justificativa(t) -> str:
    j = limpar_texto(t)
    if len(j) < JUSTIFICATIVA_MINIMA:
        raise DecisaoRecusada(
            "JUSTIFICATIVA_CURTA",
            "escreva ao menos %d caracteres dizendo por que esta decisão está "
            "sendo tomada" % JUSTIFICATIVA_MINIMA)
    if len(j) > JUSTIFICATIVA_MAXIMA:
        raise DecisaoRecusada("JUSTIFICATIVA_LONGA",
                              "limite de %d caracteres" % JUSTIFICATIVA_MAXIMA)
    return j


def _agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dominio():
    """Importa o domínio na hora do uso — o servidor sobe sem pagar por isto."""
    from ingestao import auditoria as aud
    from ingestao import checkpoint as cpm
    from ingestao import operacao as op
    from ingestao import trava as tv
    from ingestao.ambiente import resolver as resolver_ambiente
    from ingestao.identidade import normalizar
    return aud, cpm, op, tv, resolver_ambiente, normalizar


def _retrato(cp, estado) -> dict:
    """O estado que importa, em números — sem conteúdo fiscal, sem segredo."""
    return {
        "ult_nsu": cp.ult_nsu,
        "max_nsu": cp.max_nsu,
        "nsu_observado_sefaz": cp.nsu_observado_sefaz or "",
        "status": cp.status,
        "estado_sincronismo": cp.estado_sincronismo,
        "diagnostico": cp.diagnostico or "",
        "cobertura_anterior": cp.cobertura_anterior,
        "tem_marco_cobertura": bool(getattr(cp, "marco_cobertura", None)),
        "revisao_sequencia": bool(estado.revisao_sequencia),
        "decisao_pendente": bool(estado.decisao_pendente),
        "bloqueio_mantido_em": (estado.bloqueio_mantido or {}).get("em", ""),
        "falhas_consecutivas": estado.falhas_consecutivas,
        "ultimo_cstat": estado.ultimo_cstat or "",
        "ultimo_656_subtipo": estado.ultimo_656_subtipo or "",
    }


def _registrar_na_trilha(aud, dados, ident, decisao: dict) -> None:
    """A decisão fica ao lado da evidência que a motivou, na trilha da empresa.

    Vai o retrato de antes e depois, o tipo, quem decidiu e a justificativa —
    nada de senha, certificado, frase-senha ou conteúdo de documento.
    """
    try:
        aud.abrir(dados, ident.valor).registrar(
            aud.DECISAO_ADMINISTRATIVA,
            decisao=decisao["tipo"], operador=decisao["operador"],
            justificativa=decisao["justificativa"],
            servico=decisao["servico"], ambiente=decisao["ambiente"],
            antes=decisao["antes"], depois=decisao["depois"],
            origem_informacao=decisao.get("origem_informacao") or None)
    except Exception:
        # A trilha nunca derruba a decisão — mesma regra do resto do pacote.
        pass


def _abrir(dados, identidade, servico, ambiente):
    aud, cpm, op, tv, resolver_ambiente, normalizar = _dominio()
    ident = normalizar(identidade)
    if not ident.valido:
        raise DecisaoRecusada("IDENTIDADE_INVALIDA", ident.motivo)
    amb = resolver_ambiente(ambiente)
    repo_cp = cpm.RepositorioCheckpoint(Path(dados))
    repo_op = op.RepositorioOperacao(Path(dados))
    return aud, cpm, op, tv, ident, amb, repo_cp, repo_op


# ── as decisões ────────────────────────────────────────────────────────────
def encerrar_revisao(dados, identidade, *, quem: str, justificativa: str,
                     servico: str = SERVICO_PADRAO,
                     ambiente=AMBIENTE_PADRAO) -> dict:
    """Tira a empresa da revisão de sequência. **Não consulta nada.**

    Depois disto a empresa volta a ser ELEGÍVEL — e só. A próxima consulta
    continua dependendo de cooldown, teto por hora, trava e, se houver
    divergência externa, da política que barra consulta em divergência.
    """
    quem = limpar_texto(quem)
    if not quem:
        raise DecisaoRecusada("SEM_OPERADOR", "a decisão precisa de um autor")
    j = validar_justificativa(justificativa)
    aud, cpm, op, tv, ident, amb, repo_cp, repo_op = _abrir(
        dados, identidade, servico, ambiente)

    with tv.travar(Path(dados), ident.valor, servico, amb):
        cp = repo_cp.carregar(ident.valor, servico, amb)
        estado = repo_op.carregar(ident.valor, servico, amb)
        if not estado.revisao_sequencia:
            raise DecisaoRecusada(
                "NAO_ESTA_EM_REVISAO",
                "esta empresa não está em revisão de sequência; nada a encerrar")
        antes = _retrato(cp, estado)
        nsu_antes = cp.ult_nsu

        estado.encerrar_revisao_de_sequencia(quem=quem)   # função do domínio
        repo_op.salvar(estado)

        cp_depois = repo_cp.carregar(ident.valor, servico, amb)
        if cp_depois.ult_nsu != nsu_antes:
            # Cinto e suspensório, como no pipeline: decisão administrativa
            # não move ponteiro de posse. Se moveu, algo está muito errado.
            raise RuntimeError("a decisão administrativa alterou o ult_nsu — "
                               "isto nunca pode acontecer")
        depois = _retrato(cp_depois, repo_op.carregar(ident.valor, servico, amb))

    decisao = {"tipo": TIPO_ENCERRAR_REVISAO, "empresa": ident.mascarado(),
               "operador": quem, "justificativa": j,
               "servico": servico, "ambiente": amb.nome, "em": _agora(),
               "antes": antes, "depois": depois,
               "consulta_disparada": False}
    _registrar_na_trilha(aud, dados, ident, decisao)
    return decisao


def reabrir_revisao(dados, identidade, *, quem: str, justificativa: str,
                    servico: str = SERVICO_PADRAO,
                    ambiente=AMBIENTE_PADRAO) -> dict:
    """Desfaz o encerramento, pelo domínio (`exigir_revisao_de_sequencia`)."""
    quem = limpar_texto(quem)
    if not quem:
        raise DecisaoRecusada("SEM_OPERADOR", "a decisão precisa de um autor")
    j = validar_justificativa(justificativa)
    aud, cpm, op, tv, ident, amb, repo_cp, repo_op = _abrir(
        dados, identidade, servico, ambiente)

    with tv.travar(Path(dados), ident.valor, servico, amb):
        cp = repo_cp.carregar(ident.valor, servico, amb)
        estado = repo_op.carregar(ident.valor, servico, amb)
        if estado.revisao_sequencia:
            raise DecisaoRecusada("JA_ESTA_EM_REVISAO",
                                  "esta empresa já está fora da automação")
        antes = _retrato(cp, estado)
        nsu_antes = cp.ult_nsu

        estado.exigir_revisao_de_sequencia(
            "revisão reaberta por decisão administrativa de %s" % quem)
        repo_op.salvar(estado)

        cp_depois = repo_cp.carregar(ident.valor, servico, amb)
        if cp_depois.ult_nsu != nsu_antes:
            raise RuntimeError("a decisão administrativa alterou o ult_nsu — "
                               "isto nunca pode acontecer")
        depois = _retrato(cp_depois, repo_op.carregar(ident.valor, servico, amb))

    decisao = {"tipo": TIPO_REABRIR_REVISAO, "empresa": ident.mascarado(),
               "operador": quem, "justificativa": j,
               "servico": servico, "ambiente": amb.nome, "em": _agora(),
               "antes": antes, "depois": depois, "consulta_disparada": False}
    _registrar_na_trilha(aud, dados, ident, decisao)
    return decisao


def marcar_cobertura(dados, identidade, *, quem: str, justificativa: str,
                     origem_informacao: str,
                     servico: str = SERVICO_PADRAO,
                     ambiente=AMBIENTE_PADRAO) -> dict:
    """Registra o marco `INICIO_COBERTURA_DFE`, pelo domínio. **Sem rede.**

    A POSIÇÃO NÃO VEM DE QUEM CHAMA. Ela é o `nsu_observado_sefaz` que a
    própria resposta do Ambiente Nacional deixou gravado no checkpoint. Não há
    parâmetro de NSU nesta função, e isso é o ponto: o operador decide
    *reconhecer* a posição, não *escolher* um número.

    O marco diz, para sempre, que o que veio antes dele pode não estar no
    acervo — e que só entra por importação de XML de outra origem.
    """
    quem = limpar_texto(quem)
    if not quem:
        raise DecisaoRecusada("SEM_OPERADOR", "a decisão precisa de um autor")
    j = validar_justificativa(justificativa)
    origem = limpar_texto(origem_informacao).upper()
    if origem not in ORIGENS:
        raise DecisaoRecusada(
            "ORIGEM_INVALIDA",
            "informe de onde veio a informação: %s" % ", ".join(ORIGENS))
    aud, cpm, op, tv, ident, amb, repo_cp, repo_op = _abrir(
        dados, identidade, servico, ambiente)

    with tv.travar(Path(dados), ident.valor, servico, amb):
        cp = repo_cp.carregar(ident.valor, servico, amb)
        estado = repo_op.carregar(ident.valor, servico, amb)
        observado = cp.nsu_observado_sefaz or ""
        if cpm.nsu_int(observado) <= cpm.nsu_int(cp.ult_nsu):
            raise DecisaoRecusada(
                "SEM_NOTICIA_EXTERNA",
                "não há posição informada pelo Ambiente Nacional à frente do "
                "acervo; não há cobertura a marcar")
        if getattr(cp, "marco_cobertura", None):
            raise DecisaoRecusada("MARCO_JA_EXISTE",
                                  "esta empresa já tem um marco de cobertura")
        antes = _retrato(cp, estado)
        nsu_antes = cp.ult_nsu

        marco = cp.marcar_inicio_cobertura(          # função do domínio
            observado, cstat=cp.nsu_observado_origem or "",
            motivo="decisão administrativa de %s · origem da informação: %s"
                   % (quem, origem))
        repo_cp.salvar(cp)

        cp_depois = repo_cp.carregar(ident.valor, servico, amb)
        if cp_depois.ult_nsu != nsu_antes:
            raise RuntimeError("o marco de cobertura alterou o ult_nsu — "
                               "isto nunca pode acontecer")
        depois = _retrato(cp_depois, repo_op.carregar(ident.valor, servico, amb))

    decisao = {"tipo": TIPO_MARCO_COBERTURA, "empresa": ident.mascarado(),
               "operador": quem, "justificativa": j,
               "origem_informacao": origem, "servico": servico,
               "ambiente": amb.nome, "em": _agora(),
               "antes": antes, "depois": depois, "consulta_disparada": False,
               "marco": {k: v for k, v in marco.items() if k != "empresa"}}
    _registrar_na_trilha(aud, dados, ident, decisao)
    return decisao


def manter_bloqueio(dados, identidade, *, quem: str, justificativa: str,
                    servico: str = SERVICO_PADRAO,
                    ambiente=AMBIENTE_PADRAO) -> dict:
    """Registra "analisei e mantenho fora da automação por falta de evidência".

    É a decisão de NÃO decidir — e ela precisa existir, porque sem ela uma
    empresa analisada ontem e uma que ninguém abriu há três meses são o mesmo
    item na fila. Depois desta chamada a empresa continua **exatamente** onde
    estava: mesma posse, mesmo checkpoint, mesma divergência, mesma revisão,
    mesmo cooldown. O que ela ganha é autor, data e motivo.

    NÃO SUBSTITUI A REVISÃO DE SEQUÊNCIA. Empresa em revisão continua em
    revisão; registrar aqui não encerra nada e não é alternativa a
    `encerrar_revisao` — apenas diz que a revisão foi olhada e mantida.

    REPETIR NÃO EMPILHA. Com a MESMA justificativa, recusa (`DECISAO_REPETIDA`)
    e nada é gravado. Com uma justificativa NOVA, reafirma: conta a reafirmação
    e preserva a data da primeira decisão.
    """
    quem = limpar_texto(quem)
    if not quem:
        raise DecisaoRecusada("SEM_OPERADOR", "a decisão precisa de um autor")
    j = validar_justificativa(justificativa)
    aud, cpm, op, tv, ident, amb, repo_cp, repo_op = _abrir(
        dados, identidade, servico, ambiente)

    with tv.travar(Path(dados), ident.valor, servico, amb):
        cp = repo_cp.carregar(ident.valor, servico, amb)
        estado = repo_op.carregar(ident.valor, servico, amb)

        # Só faz sentido sobre quem ESTÁ fora da automação por decisão humana
        # pendente. Em empresa saudável isto seria um carimbo sem objeto.
        em_revisao = bool(estado.revisao_sequencia)
        divergente = cp.estado_sincronismo == cpm.SINCRONISMO_DIVERGENCIA
        if not (em_revisao or divergente):
            raise DecisaoRecusada(
                "NAO_ESTA_BLOQUEADA",
                "esta empresa não está em revisão de sequência nem em "
                "divergência externa; não há bloqueio a manter")

        antes = _retrato(cp, estado)
        nsu_antes = cp.ult_nsu
        estado_no_momento = (op.REVISAO_DE_SEQUENCIA if em_revisao
                             else op.DIVERGENCIA_EXTERNA)

        resultado = estado.manter_bloqueio_sem_evidencia(   # função do domínio
            quem=quem, justificativa=j, estado_no_momento=estado_no_momento)
        if resultado == "REPETIDA":
            anterior = estado.bloqueio_mantido or {}
            raise DecisaoRecusada(
                "DECISAO_REPETIDA",
                "esta empresa já está mantida bloqueada com esta mesma "
                "justificativa, desde %s. Para reafirmar, escreva o que mudou "
                "desde então." % (anterior.get("em") or "a decisão anterior"))
        repo_op.salvar(estado)

        cp_depois = repo_cp.carregar(ident.valor, servico, amb)
        if cp_depois.ult_nsu != nsu_antes:
            raise RuntimeError("a decisão administrativa alterou o ult_nsu — "
                               "isto nunca pode acontecer")
        estado_depois = repo_op.carregar(ident.valor, servico, amb)
        if bool(estado_depois.revisao_sequencia) != em_revisao:
            raise RuntimeError("manter o bloqueio alterou a revisão de "
                               "sequência — isto nunca pode acontecer")
        depois = _retrato(cp_depois, estado_depois)

    decisao = {"tipo": TIPO_MANTER_BLOQUEIO, "empresa": ident.mascarado(),
               "operador": quem, "justificativa": j,
               "servico": servico, "ambiente": amb.nome, "em": _agora(),
               "antes": antes, "depois": depois, "consulta_disparada": False,
               "resultado": resultado,
               "estado_no_momento": estado_no_momento,
               "bloqueio_mantido": dict(estado_depois.bloqueio_mantido or {})}
    _registrar_na_trilha(aud, dados, ident, decisao)
    return decisao


# ── leitura para a tela ────────────────────────────────────────────────────
def listar_pendentes(dados, servico: str = SERVICO_PADRAO,
                     ambiente=AMBIENTE_PADRAO) -> dict:
    """Quem está travado, e por quê. Só leitura — não decide nada.

    Traz o diagnóstico SEMPRE rotulado como hipótese: `POSSIVEL_CONSUMIDOR_
    EXTERNO` é o que os números sugerem, não a identificação de um sistema.
    """
    aud, cpm, op, tv, resolver_ambiente, normalizar = _dominio()
    dados = Path(dados)
    amb = resolver_ambiente(ambiente)
    repo_cp = cpm.RepositorioCheckpoint(dados)
    repo_op = op.RepositorioOperacao(dados)

    fila = []
    for pasta in sorted(p for p in dados.iterdir() if p.is_dir()):
        ident = normalizar(pasta.name)
        if not ident.valido:
            continue
        if not repo_cp.existe(ident.valor, servico, amb):
            continue
        try:
            cp = repo_cp.carregar(ident.valor, servico, amb)
        except Exception:
            continue
        estado = repo_op.carregar(ident.valor, servico, amb)
        em_revisao = bool(estado.revisao_sequencia)
        divergente = cp.estado_sincronismo == cpm.SINCRONISMO_DIVERGENCIA
        if not (em_revisao or divergente):
            continue

        avisos = []
        if not estado.ultima_consulta_em:
            avisos.append("nenhuma consulta real registrada para esta empresa — "
                          "a evidência é histórica (reconstruída)")
        if divergente and not estado.ultimo_656_subtipo:
            avisos.append("divergência sem rejeição 656: a posição foi informada "
                          "numa resposta normal")
        if cpm.nsu_int(cp.ult_nsu) == 0:
            avisos.append("o FISCALE nunca recebeu documento desta empresa")

        fila.append({
            "identidade": ident.valor,
            "empresa": ident.mascarado(),
            "em_revisao": em_revisao,
            "divergencia_externa": divergente,
            "distancia": cp.distancia_para_sefaz,
            "ult_nsu": cp.ult_nsu,
            "nsu_observado_sefaz": cp.nsu_observado_sefaz or "",
            "ultimo_656_subtipo": estado.ultimo_656_subtipo or "",
            "ultimo_656_em": estado.ultimo_656_em or "",
            "falhas_consecutivas": estado.falhas_consecutivas,
            "tem_marco_cobertura": bool(getattr(cp, "marco_cobertura", None)),
            "cobertura_anterior": cp.cobertura_anterior,
            # Decisão administrativa: "ninguém olhou ainda" × "olhado e mantido".
            # A tela precisa dos dois separados — era isso que faltava.
            "decisao_pendente": bool(estado.decisao_pendente),
            "bloqueio_mantido": dict(estado.bloqueio_mantido or {}),
            # Hipótese, e dito com todas as letras.
            "diagnostico": cp.diagnostico or "",
            "diagnostico_e_hipotese": bool(cp.diagnostico),
            "diagnostico_texto": (
                "Hipótese registrada pelo sistema a partir dos números: alguém "
                "mais parece consumir esta fila. O FISCALE NÃO identifica qual "
                "sistema é, e isto não é uma conclusão."
                if cp.diagnostico else ""),
            "avisos": avisos,
            "pode_encerrar_revisao": em_revisao,
            # Cabe sempre que a empresa está travada: é a decisão de esperar,
            # e reafirmá-la com motivo novo continua valendo.
            "pode_manter_bloqueio": True,
            "pode_marcar_cobertura": (
                cpm.nsu_int(cp.nsu_observado_sefaz) > cpm.nsu_int(cp.ult_nsu)
                and not getattr(cp, "marco_cobertura", None)),
        })
    # Quem ainda não foi analisado vem primeiro: a fila é de trabalho, e o que
    # já tem decisão registrada não precisa ser reaberto toda semana.
    fila.sort(key=lambda x: (not x["decisao_pendente"], not x["em_revisao"],
                             -x["distancia"]))
    return {
        "empresas": fila, "total": len(fila),
        "sem_decisao": sum(1 for e in fila if e["decisao_pendente"]),
        "mantidas_por_decisao": sum(1 for e in fila if not e["decisao_pendente"]),
        "servico": servico, "ambiente": amb.nome,
        "observacao": (
            "Encerrar a revisão NÃO consulta a SEFAZ: devolve a empresa à fila "
            "de elegibilidade, e a consulta seguinte continua sendo uma decisão "
            "separada, sujeita a cooldown, teto e trava."),
        "origens_de_informacao": list(ORIGENS),
    }
