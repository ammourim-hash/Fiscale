"""
servico_cte.py — a ÚNICA porta para o `CTeDistribuicaoDFe`.

POR QUE UMA PORTA SÓ, E POR QUE ELA É SEPARADA DA NF-e
    A política de cadência só protege se morar num lugar. Espalhada, basta o
    chamador que esqueceu — um botão de tela, uma tarefa agendada — para
    queimar a cota do CNPJ do cliente e bloqueá-lo por uma hora.

    Ser SEPARADA da NF-e não é simetria: os dois serviços contam cota por
    CNPJ e por serviço, e misturar as duas contagens faria a política do CT-e
    ser calculada com consultas de NF-e, ou o contrário.

O QUE ESTA VIA TRAZ — E O QUE ELA NUNCA VAI TRAZER
    A NT 2015.002 é sobre documentos **de interesse** do consulente. O emitente
    **não** recebe de volta, por esta via, o que ele mesmo emitiu.

    Para uma transportadora isso decide o desenho: os CT-e que ela emite NÃO
    virão daqui. Vem o que ela **toma, recebe ou em que participa** — como
    tomadora, remetente, destinatária, expedidora, recebedora, ou pelo
    `autXML` — mais os eventos.

    Emissão própria entra pela ENTRADA CANÔNICA (`entrada_cte.py`): ERP, pasta
    vigiada, e-mail, XML ou ZIP. São duas fontes com propósitos diferentes, e
    confundi-las faz esperar da rede um documento que nunca vem.

O QUE O CT-e TEM DE DIFERENTE, E O QUE ISSO MUDA AQUI
    **Não existe `consChCTe`.** A NF-e, se perder o checkpoint, ainda alcança
    um documento pela chave. O CT-e não: a única recuperação é caminhar por
    NSU, e o Ambiente Nacional retém cerca de 3 meses a partir do zero.

    Por isso a regra "só avança depois de gravar e conferir" é aqui mais
    crítica do que lá. Um avanço indevido de checkpoint na NF-e é um susto;
    no CT-e pode ser documento perdido para sempre.

A ORDEM, QUE NÃO MUDA
    1. elegibilidade — certificado, cooldown, teto de frequência, bloqueio
    2. trava por (empresa, serviço, ambiente): nunca duas ao mesmo tempo
    3. checkpoint do CT-e, nunca o da NF-e
    4. consulta pelo adaptador `conectores/cte_dfe.py` — jamais direto
    5. **persistir e conferir TODOS os documentos**
    6. só então avançar o checkpoint
    7. estado operacional gravado antes de devolver

O QUE ELA NUNCA FAZ
    Não agenda, não tem laço, não decide ordem nem orçamento — uma chamada é
    uma unidade de trabalho de uma empresa. Não abre `.pfx` nem vê senha:
    recebe a fábrica de fonte já pronta.

CLIQUE NÃO É AUTORIZAÇÃO
    Empresa em cooldown, bloqueada por consumo indevido ou sem certificado
    recebe **estado e motivo**, não uma chamada de rede.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import checkpoint as cpm
from . import entrada_cte as ent
from . import operacao as op
from . import resposta_bruta as bruta
from . import pipeline as pipe
from . import trava as trv
from .ambiente import PRODUCAO, resolver as resolver_ambiente
from .conectores import cte_dfe as C
from .distribuicao import higienizar, nsu_int, normalizar_nsu
from .identidade import normalizar

SERVICO = cpm.CTE_DISTRIBUICAO

# ── decisões ────────────────────────────────────────────────────────────────
AUTORIZADA = "AUTORIZADA"
RECUSADA = "RECUSADA_POR_POLITICA"
FALHA_PREPARO = "FALHA_AO_PREPARAR"

# ── estados devolvidos ──────────────────────────────────────────────────────
OK = "OK"
SEM_DOCUMENTOS = "SEM_DOCUMENTOS"
SEM_CADASTRO = "SEM_CADASTRO"          # nem empresa há: não é o mesmo
SEM_CERTIFICADO = "SEM_CERTIFICADO"    # empresa existe, credencial não
EM_COOLDOWN = "EM_COOLDOWN"
BLOQUEADA = "BLOQUEADA"
TETO_DE_FREQUENCIA = "TETO_DE_FREQUENCIA"
CONSUMO_INDEVIDO = "CONSUMO_INDEVIDO"
ERRO_DE_REDE = "ERRO_DE_REDE"
ERRO_DO_SERVICO = "ERRO_DO_SERVICO"
FALHA_DE_PERSISTENCIA = "FALHA_DE_PERSISTENCIA"
FILA_ESGOTADA = "FILA_ESGOTADA"        # ultNSU alcançou maxNSU
EXIGE_REVISAO = "EXIGE_REVISAO"        # 656: só volta com decisão humana

# A janela do Ambiente Nacional a partir do zero. `ultNSU=0` NÃO traz o
# histórico inteiro: traz o que está retido, e o resto não existe mais por
# esta via. Como não há `consChCTe`, o que ficou fora da janela só entra por
# importação.
RETENCAO_MESES_DO_ZERO = C.RETENCAO_MESES
AVISO_JANELA = (
    "`ultNSU=0` recupera apenas a janela retida pelo Ambiente Nacional — "
    f"cerca de {C.RETENCAO_MESES} meses. Documento anterior a isso não vem "
    "por esta via, e como o CT-e não tem consulta por chave, ele só entra "
    "por importação de XML.")


@dataclass(frozen=True)
class Politica:
    """Cadência. Números conservadores por escolha, não por medição.

    O teto documentado de 20 consultas/hora é da CONSULTA PONTUAL, não da
    paginação — a mesma distinção que a NF-e já registrou. Enquanto não houver
    medição própria do CT-e, o FISCALE anda abaixo do que o serviço permite:
    errar para menos custa uma sincronização mais lenta; errar para mais custa
    o CNPJ do cliente bloqueado.
    """
    intervalo_minimo_segundos: int = 60
    consultas_por_hora: int = 12
    # Espera progressiva depois de falha transitória. Cresce e para de crescer:
    # dobrar sem teto transformaria uma indisponibilidade de tarde inteira em
    # espera de dias.
    espera_progressiva: tuple[int, ...] = (60, 180, 600, 1800, 3600)
    # Consumo indevido: o serviço bloqueia por cerca de uma hora. Esperamos
    # mais do que isso de propósito — voltar no minuto exato é como se leva o
    # segundo bloqueio.
    cooldown_consumo_indevido_segundos: int = 4200
    # ESGOTADA A FILA, ESPERA UMA HORA. A NT é explícita: alcançado o
    # `maxNSU`, ou vindo `cStat 137`, consultar de novo antes de uma hora é
    # justamente o que caracteriza consumo indevido. Não é prudência nossa —
    # é a regra que gera o 656.
    cooldown_fila_esgotada_segundos: int = 3600
    # Paginação: o contrato entrega no máximo 50 documentos por resposta.
    max_paginas_por_chamada: int = 1


POLITICA_PADRAO = Politica()


def espera_para(tentativas: int, politica: Politica = POLITICA_PADRAO) -> int:
    """Segundos de espera depois de `tentativas` falhas seguidas."""
    if tentativas <= 0:
        return 0
    faixa = politica.espera_progressiva
    return faixa[min(tentativas, len(faixa)) - 1]


@dataclass
class ResultadoCTe:
    """O que aconteceu com UMA empresa. Sem XML, sem segredo, sem CNPJ inteiro."""
    identidade_mascarada: str = ""
    autorizada: bool = False
    consultada: bool = False
    decisao: str = RECUSADA
    estado: str = ""
    motivo: str = ""
    segundos_para_liberar: int = 0
    cstat: str = ""
    subtipo: str = ""
    documentos: int = 0
    persistidos: int = 0
    conflitos: int = 0        # foram para conferência: guardados, não perdidos
    indexados: int = 0        # entraram no índice — sem isto a captura é invisível
    tentativa: str = ""       # id da tentativa; liga o ciclo à resposta guardada
    resposta_preservada: bool = False
    nsu_antes: str = ""
    nsu_depois: str = ""
    max_nsu: str = ""
    erro: str = ""
    checkpoint_avancou: bool = False
    # 656 não volta sozinho: o tempo passa, mas a empresa só é consultada de
    # novo depois que alguém encerrar a revisão.
    exige_revisao: bool = False

    def resumo(self) -> dict:
        return {
            "empresa": self.identidade_mascarada,
            "servico": "CTE_DISTRIBUICAO",
            "autorizada": self.autorizada, "consultada": self.consultada,
            "decisao": self.decisao, "estado": self.estado,
            "motivo": self.motivo or None,
            "cstat": self.cstat or None, "subtipo": self.subtipo or None,
            "documentos": self.documentos, "persistidos": self.persistidos,
            "conflitos": self.conflitos,
            "indexados": self.indexados,
            "tentativa": self.tentativa or None,
            "resposta_preservada": self.resposta_preservada,
            "checkpoint_antes": self.nsu_antes or None,
            "checkpoint_depois": self.nsu_depois or None,
            "checkpoint_avancou": self.checkpoint_avancou,
            "max_nsu": self.max_nsu or None,
            "segundos_para_liberar": self.segundos_para_liberar,
            "exige_revisao": self.exige_revisao,
            "erro": self.erro or None,
        }


def _mascarar(d: str) -> str:
    d = normalizar(d).valor or str(d or "")
    return (d[:8] + "***") if len(d) >= 8 else "***"


PAUSA_ENTRE_PAGINAS_SEGUNDOS = 5
"""Respiro entre páginas de uma mesma varredura.

POR QUE 5, E POR QUE NÃO 60
    `POLITICA_PADRAO` pede 60 segundos entre consultas e no máximo 12 por
    hora. Esses números valem para consultar UMA VEZ e ir embora — e a própria
    docstring dela registra que o teto de 20/hora documentado é da consulta
    PONTUAL, não da paginação.

    Paginar é outra coisa. Cada página pede um `ultNSU` maior que a anterior e
    traz documentos que ainda não tínhamos: é a operação para a qual o
    `distNSU` existe. O que a NT chama de consumo indevido é o oposto disso —
    repetir a MESMA consulta sem que o checkpoint ande.

    Ainda assim, isto não é medição: é escolha conservadora dentro do que o
    serviço tolera. O `POLITICA_PADRAO` **não muda**; quem quiser drenar fila
    passa esta política de propósito, e a escolha fica visível no código.
"""

POLITICA_PAGINACAO = Politica(
    intervalo_minimo_segundos=PAUSA_ENTRE_PAGINAS_SEGUNDOS,
    # O teto horário some da paginação porque ele mede a coisa errada aqui:
    # 81 páginas a 12/hora seriam sete horas para drenar uma fila que o
    # serviço entrega em minutos. O que segura o laço é o `ultNSU` alcançar o
    # `maxNSU`, não um contador.
    consultas_por_hora=10 ** 6,
)

# Limites do laço. Nenhum deles é a regra fiscal — a regra é parar quando a
# fila acaba. São cintos de segurança contra laço infinito por defeito nosso.
MAX_PAGINAS_PADRAO = 500
LOTE = 50


@dataclass
class ResultadoDrenagem:
    """O que aconteceu com a varredura inteira. Uma linha por página."""
    identidade_mascarada: str = ""
    paginas: int = 0
    documentos: int = 0
    persistidos: int = 0
    indexados: int = 0
    nsu_inicial: str = ""
    nsu_final: str = ""
    max_nsu: str = ""
    motivo_da_parada: str = ""
    completa: bool = False
    ciclos: list = field(default_factory=list)

    def para_json(self) -> dict:
        return {"empresa": self.identidade_mascarada,
                "paginas": self.paginas, "documentos": self.documentos,
                "persistidos": self.persistidos, "indexados": self.indexados,
                "nsu_inicial": self.nsu_inicial, "nsu_final": self.nsu_final,
                "max_nsu": self.max_nsu, "completa": self.completa,
                "motivo_da_parada": self.motivo_da_parada}


def drenar_fila(dados_dir, identidade, fabrica_fonte, *, ambiente=PRODUCAO,
                politica: Politica = POLITICA_PAGINACAO, cad=None,
                cuf: str = "", max_paginas: int = MAX_PAGINAS_PADRAO,
                pausa=None, relogio=None,
                ao_terminar_pagina=None) -> ResultadoDrenagem:
    """Pagina até a fila acabar. **Nunca levanta.**

    Cada página é um `consultar_empresa` inteiro — com persistência, resposta
    preservada, indexação e só então o checkpoint. Este laço não conhece
    checkpoint: ele apenas repete o ciclo e olha o resultado. Continua a haver
    UM dono.

    PARA NA PRIMEIRA COISA QUE NÃO FOR SUCESSO
        Não insiste, não tenta de novo, não pula página. Um 656, uma rejeição,
        uma falha de persistência ou de índice encerram a varredura ali. Voltar
        a bater numa porta que respondeu "não" é como se conquista o segundo
        bloqueio — e no CT-e o que for pulado não volta, porque não existe
        consulta por chave.

    `pausa` e `relogio` existem para o teste rodar sem esperar de verdade: sem
    os dois juntos, a espera seria falsa mas o relógio da POLÍTICA seria real,
    e o intervalo mínimo barraria a segunda página. Em produção os dois são os
    de verdade.
    """
    import time

    dormir = pausa if pausa is not None else time.sleep
    agora_de = relogio if relogio is not None else (
        lambda: datetime.now(timezone.utc))
    ident = normalizar(identidade)
    r = ResultadoDrenagem(identidade_mascarada=_mascarar(identidade))

    repo_cp = cpm.RepositorioCheckpoint(Path(dados_dir))
    amb = resolver_ambiente(ambiente)
    cp0 = repo_cp.carregar(ident.valor, SERVICO, amb) if ident.valido else None
    r.nsu_inicial = r.nsu_final = (cp0.ult_nsu if cp0 else "")

    for n in range(max_paginas):
        if n:
            # A pausa vem ANTES da página seguinte, nunca depois da última:
            # esperar para não fazer nada é desperdício, e o operador que vê o
            # programa parado sem motivo aprende a desconfiar dele.
            dormir(politica.intervalo_minimo_segundos)

        c = consultar_empresa(dados_dir, identidade, fabrica_fonte,
                              ambiente=amb, politica=politica, cad=cad,
                              cuf=cuf, agora=agora_de())
        r.paginas += 1
        r.documentos += c.documentos
        r.persistidos += c.persistidos
        r.indexados += c.indexados
        r.nsu_final = c.nsu_depois or r.nsu_final
        r.max_nsu = c.max_nsu or r.max_nsu
        r.ciclos.append(c.para_json() if hasattr(c, "para_json") else {})
        if ao_terminar_pagina:
            ao_terminar_pagina(r.paginas, c)

        if c.estado in (FILA_ESGOTADA, SEM_DOCUMENTOS):
            r.motivo_da_parada = "fila esgotada — o serviço não tem mais nada"
            r.completa = True
            break
        if c.estado != OK:
            r.motivo_da_parada = (
                f"{c.estado}: {c.erro or c.motivo or 'sem detalhe'}")
            break
        if not c.checkpoint_avancou:
            # Sem avanço, a próxima página pediria o MESMO `ultNSU`. Isso é a
            # definição de consumo indevido, e o laço não vai lá.
            r.motivo_da_parada = ("o checkpoint não avançou; repetir o mesmo "
                                  "`ultNSU` é o que gera 656")
            break
    else:
        r.motivo_da_parada = (f"teto de {max_paginas} páginas alcançado — "
                              f"a fila continua")

    return r


def fabrica_padrao(cad, dados_dir):
    """A fonte REAL do CT-e, com mTLS. Único lugar que monta rede aqui.

    Espelha `servico_distribuicao.fabrica_padrao` de propósito: a rota HTTP, o
    ciclo e a ferramenta manual precisam usar a MESMA construção, senão cada
    caminho descobre um jeito diferente de abrir o certificado.

    O `cUFAutor` não entra na fábrica — ele viaja em `consultar_empresa(cuf=)`,
    porque é do autor da consulta e não da empresa consultada.
    """
    def _f(identidade, ambiente):
        return C.criar(cad, identidade, dados_dir=dados_dir, ambiente=ambiente)
    return _f


def elegivel(dados_dir, identidade, *, ambiente=PRODUCAO, cad=None,
             politica: Politica = POLITICA_PADRAO,
             agora: datetime | None = None, repo_op=None) -> ResultadoCTe:
    """Pode consultar agora? Devolve o motivo quando não.

    Separada da consulta de propósito: a tela precisa mostrar "por que não" sem
    disparar nada, e o piloto precisa poder ensaiar a decisão sem rede.
    """
    q = agora or datetime.now(timezone.utc)
    amb = resolver_ambiente(ambiente)
    ident = normalizar(identidade)
    r = ResultadoCTe(identidade_mascarada=_mascarar(identidade))

    if not ident.valido:
        r.estado, r.motivo = SEM_CERTIFICADO, f"identidade inválida: {ident.motivo}"
        return r

    if cad is not None:
        # A MESMA pergunta que o ciclo da NF-e faz, pelo MESMO método do
        # cadastro. A versão anterior chamava `cadastro.buscar()`, que não
        # existe, e o `except Exception` transformava o `AttributeError` em
        # "empresa sem certificado" — um erro de programação saindo como
        # veredito fiscal, idêntico para quem realmente não tem certificado.
        if cad.obter_empresa(ident.valor) is None:
            r.estado = SEM_CADASTRO
            r.motivo = "empresa não está no cadastro"
            return r
        if cad.obter_certificado_da_empresa(ident.valor) is None:
            r.estado = SEM_CERTIFICADO
            r.motivo = "nenhum certificado associado à empresa no cadastro"
            return r

    repo_op = repo_op or op.RepositorioOperacao(Path(dados_dir))
    estado = repo_op.carregar(ident.valor, SERVICO, amb)

    # REVISÃO PENDENTE barra antes do relógio. Sem isto, passada a hora do
    # bloqueio a empresa voltaria sozinha à fila — que é exatamente o que o
    # 656 pede para não acontecer.
    if getattr(estado, "revisao_sequencia", False):
        r.estado = EXIGE_REVISAO
        r.motivo = ("consulta suspensa após consumo indevido: exige revisão "
                    "humana antes de voltar à fila")
        return r

    if estado.em_cooldown(q):
        r.estado = EM_COOLDOWN
        r.segundos_para_liberar = estado.segundos_para_liberar(q)
        r.motivo = estado.motivo_bloqueio or "em espera"
        if "consumo" in (estado.motivo_bloqueio or "").lower():
            r.estado = BLOQUEADA
        return r

    if estado.consultas_na_janela(60, q) >= politica.consultas_por_hora:
        r.estado = TETO_DE_FREQUENCIA
        r.motivo = (f"{politica.consultas_por_hora} consultas na última hora — "
                    f"o teto desta política")
        r.segundos_para_liberar = politica.intervalo_minimo_segundos
        return r

    ultima = getattr(estado, "ultima_consulta_em", "")
    if ultima:
        try:
            delta = (q - op._de_iso(ultima)).total_seconds()
            if delta < politica.intervalo_minimo_segundos:
                r.estado = EM_COOLDOWN
                r.segundos_para_liberar = int(
                    politica.intervalo_minimo_segundos - delta)
                r.motivo = "intervalo mínimo entre consultas ainda não venceu"
                return r
        except Exception:
            pass

    r.autorizada = True
    r.decisao = AUTORIZADA
    r.estado = OK
    return r


def consultar_empresa(dados_dir, identidade, fabrica_fonte, *,
                      ambiente=PRODUCAO, politica: Politica = POLITICA_PADRAO,
                      cad=None, agora: datetime | None = None,
                      repo_op=None, repo_cp=None,
                      cuf: str = "") -> ResultadoCTe:
    """Consulta UMA empresa no CT-e, com toda a política. **Nunca levanta.**

    `fabrica_fonte(identidade, ambiente)` devolve um objeto com
    `distribuir(identidade, ult_nsu, cuf) -> Resposta`. Nos testes ele é um
    dublê: nenhuma linha daqui abre socket.
    """
    q = agora or datetime.now(timezone.utc)
    amb = resolver_ambiente(ambiente)
    ident = normalizar(identidade)
    dados = Path(dados_dir)

    repo_op = repo_op or op.RepositorioOperacao(dados)
    repo_cp = repo_cp or cpm.RepositorioCheckpoint(dados)

    r = elegivel(dados, identidade, ambiente=amb, cad=cad, politica=politica,
                 agora=q, repo_op=repo_op)
    if not r.autorizada:
        return r

    estado = repo_op.carregar(ident.valor, SERVICO, amb)
    cp = repo_cp.carregar(ident.valor, SERVICO, amb)
    r.nsu_antes = cp.ult_nsu

    # A trava impede duas consultas simultâneas da MESMA empresa e serviço.
    # Duas ao mesmo tempo avançariam o checkpoint duas vezes a partir do mesmo
    # ponto, e o segundo avanço pularia o que o primeiro trouxe.
    try:
        with trv.travar(dados, ident.valor, SERVICO, amb):
            return _consultar_travado(dados, ident, amb, fabrica_fonte, cuf,
                                      politica, q, repo_op, repo_cp, estado,
                                      cp, r)
    except trv.TravaOcupada as e:
        r.autorizada = False
        r.decisao = RECUSADA
        r.estado = EM_COOLDOWN
        r.motivo = f"outra consulta desta empresa está em curso: {e}"
        return r
    except Exception as e:                                   # pragma: no cover
        r.autorizada = False
        r.decisao = FALHA_PREPARO
        r.estado = ERRO_DE_REDE
        r.erro = f"{type(e).__name__}: {higienizar(str(e))}"
        return r


def _consultar_travado(dados, ident, amb, fabrica_fonte, cuf, politica, q,
                       repo_op, repo_cp, estado, cp, r) -> ResultadoCTe:
    estado.registrar_consulta(q)

    # ── 1. a chamada, pelo adaptador ────────────────────────────────────
    try:
        fonte = fabrica_fonte(ident.valor, amb)
        resposta = fonte.distribuir(ident.valor, cp.ult_nsu, cuf)
    except C.ConsumoIndevido as e:
        _bloquear_por_656(estado, repo_op, q, politica, r, str(e), "656")
        return r
    except Exception as e:
        # Rede, TLS, timeout, rejeição. **O checkpoint não anda.**
        estado.registrar_falha(ERRO_DE_REDE, "")
        repo_op.salvar(estado)
        r.consultada = True
        r.estado = ERRO_DE_REDE
        r.erro = f"{type(e).__name__}: {higienizar(str(e))}"
        r.nsu_depois = cp.ult_nsu
        return r

    r.consultada = True
    r.cstat = resposta.cstat
    r.max_nsu = resposta.max_nsu or ""
    if not r.tentativa:
        r.tentativa = bruta.nova_tentativa()

    # ── 2. a taxonomia do cStat vira comportamento ──────────────────────
    try:
        lote = C.resposta_para_lote(resposta)
    except C.ConsumoIndevido as e:
        _bloquear_por_656(estado, repo_op, q, politica, r, str(e),
                          resposta.subtipo_consumo or "656")
        return r
    except Exception as e:
        estado.registrar_falha(ERRO_DO_SERVICO, resposta.cstat)
        repo_op.salvar(estado)
        r.estado = ERRO_DO_SERVICO
        r.erro = f"{type(e).__name__}: {higienizar(str(e))}"
        r.nsu_depois = cp.ult_nsu
        return r

    # ── 3. resposta vazia: NÃO reinicia a sequência ─────────────────────
    if lote.sem_documentos:
        # `ultNSU` da resposta é a posição do serviço. Ele pode vir MENOR que o
        # nosso — e aceitar isso reiniciaria a varredura, repetindo meses de
        # documentos ou, pior, pulando os que vieram no intervalo. O checkpoint
        # só sobe; nunca desce.
        novo = _maior_nsu(cp.ult_nsu, lote.ult_nsu)
        avancou = nsu_int(novo) > nsu_int(cp.ult_nsu)
        if avancou:
            cp.ult_nsu = novo
        if lote.max_nsu:
            cp.max_nsu = lote.max_nsu
        repo_cp.salvar(cp)

        # ESGOTOU: bloqueia por uma hora, e isso NÃO é conservadorismo nosso.
        # Voltar antes é o comportamento que a NT descreve como consumo
        # indevido — o 656 é a consequência, não a causa.
        ate = q + timedelta(seconds=politica.cooldown_fila_esgotada_segundos)
        estado.bloquear(ate, "fila esgotada: sem documentos novos")
        estado.registrar_sucesso(resposta.cstat, SEM_DOCUMENTOS)
        repo_op.salvar(estado)
        r.estado = FILA_ESGOTADA if _alcancou_max(cp) else SEM_DOCUMENTOS
        r.motivo = lote.motivo or "nenhum documento novo"
        r.segundos_para_liberar = politica.cooldown_fila_esgotada_segundos
        r.nsu_depois = cp.ult_nsu
        r.checkpoint_avancou = avancou
        return r

    # ── 4. PERSISTIR ANTES DE AVANÇAR ───────────────────────────────────
    # A ordem é a regra inteira. Gravar depois de avançar significaria que uma
    # falha entre as duas coisas apaga documentos que o serviço não reentrega —
    # e no CT-e não há consulta por chave para buscá-los de volta.
    r.documentos = len(lote.documentos)
    try:
        # PELA ENTRADA CANÔNICA, não pelo `pipeline.ingerir`.
        #
        # A CTE 1 mandava o lote ao `pipeline.ingerir`, "para usar o mesmo
        # portão da NF-e". Dois problemas, e o primeiro escondia o segundo:
        #
        # 1. `ingerir()` não tem parâmetro `servico` — a chamada levantava
        #    `TypeError` e caía no `except` abaixo como falha de persistência.
        #    Nenhum teste pegou porque nenhum chegava aqui com documentos.
        # 2. Consertar o argumento seria pior. O `DistribuicaoRunner` que o
        #    `ingerir` usa **também é dono do checkpoint** (`repo.carregar`,
        #    `validar_servico(fonte.servico)`). Passar por ele daria DOIS
        #    donos ao mesmo ponteiro: ele avançaria no passo 4, e o passo 5
        #    avançaria de novo — exatamente o descontrole que esta função
        #    inteira existe para impedir.
        #
        # `entrada_cte` grava, deduplica pela chave e **não toca no
        # checkpoint**. O dono continua sendo um só: o passo 5.
        # `travar=False`: a trava desta empresa JÁ está na nossa mão, algumas
        # linhas acima. Pedi-la de novo aqui dentro não é reentrante — a
        # entrada via a trava ocupada, desistia de gravar, e devolvia um
        # veredito por documento como se cada um tivesse um problema.
        rel = ent.da_distribuicao(
            dados, ident.valor,
            [(ent.rotulo_de_nsu(d.nsu), d.conteudo)
             for d in lote.documentos],
            ambiente=amb, travar=False)
        # "Persistido" é SÓ o que a entrada aceitou e gravou: novo, duplicata,
        # promovido, cópia menor. Nada mais.
        #
        # Contar conflito como persistido parece generoso e é perigoso: o
        # conflito precisa de conferência humana, e declarar aquele NSU
        # resolvido antes disso é avançar por cima de um documento que ninguém
        # olhou. Sem `consChCTe`, não há como voltar para buscá-lo.
        r.persistidos = rel.aceitos
        r.conflitos = len(rel.conflitos)

        # 4b. A RESPOSTA BRUTA, guardada e CONFERIDA no disco.
        #
        # Antes do checkpoint, de propósito. Guardar depois de avançar seria
        # guardar o que já não importa: uma vez que o `ultNSU` andou, o
        # serviço não reentrega, e uma resposta mal lida vira dano permanente.
        # Foi exatamente isso que impediu de recuperar os NSU reais dos 72
        # documentos da CTE 2 e da CTE 4.
        #
        # `preservar()` levanta quando não consegue garantir o disco, e a
        # exceção cai no mesmo `except` da persistência: sem rastro, o
        # checkpoint não anda.
        if getattr(resposta, "bruto", b""):
            pres = bruta.preservar(dados, ident.valor, resposta.bruto,
                                   servico=SERVICO, ambiente=amb,
                                   tentativa=r.tentativa, quando=q)
            r.resposta_preservada = pres.verificada

        # 4c. O ÍNDICE, junto com o acervo.
        #
        # A D92 tirou a persistência do `pipeline.ingerir` e levou junto o
        # `indexar_pendentes()` que morava lá. O resultado foi 72 documentos
        # gravados e invisíveis: acervo cheio, índice vazio, tela vazia.
        #
        # A indexação NÃO toca no checkpoint — quem o move continua sendo só o
        # passo 5. Não há segundo dono; há um segundo consumidor do acervo.
        rel_idx = pipe.indexar_pendentes(dados, ident.valor)
        r.indexados = (getattr(rel_idx, "indexados", None)
                       if getattr(rel_idx, "indexados", None) is not None
                       else getattr(rel_idx, "total", 0))
    except Exception as e:
        estado.registrar_falha(FALHA_DE_PERSISTENCIA, resposta.cstat)
        repo_op.salvar(estado)
        r.estado = FALHA_DE_PERSISTENCIA
        r.erro = f"{type(e).__name__}: {higienizar(str(e))}"
        r.nsu_depois = cp.ult_nsu          # não andou
        return r

    if r.persistidos < r.documentos:
        # Falha PARCIAL. Avançar aqui perderia os que não gravaram.
        estado.registrar_falha(FALHA_DE_PERSISTENCIA, resposta.cstat)
        repo_op.salvar(estado)
        r.estado = FALHA_DE_PERSISTENCIA
        r.motivo = (f"{r.persistidos} de {r.documentos} documentos gravados; "
                    f"o checkpoint não avança com gravação parcial")
        r.nsu_depois = cp.ult_nsu
        return r

    # ── 5. só agora o checkpoint anda ───────────────────────────────────
    novo = _maior_nsu(cp.ult_nsu, lote.ult_nsu)
    r.checkpoint_avancou = nsu_int(novo) > nsu_int(cp.ult_nsu)
    cp.ult_nsu = novo
    if lote.max_nsu:
        cp.max_nsu = lote.max_nsu
    repo_cp.salvar(cp)

    # Alcançou o fim da fila nesta mesma resposta: mesma regra da resposta
    # vazia. O que decide não é ter vindo documento, é não haver mais.
    if _alcancou_max(cp):
        ate = q + timedelta(seconds=politica.cooldown_fila_esgotada_segundos)
        estado.bloquear(ate, "fila esgotada: ultNSU alcançou maxNSU")
        r.estado = FILA_ESGOTADA
        r.segundos_para_liberar = politica.cooldown_fila_esgotada_segundos
    else:
        r.estado = OK
    estado.registrar_sucesso(resposta.cstat, r.estado)
    repo_op.salvar(estado)

    r.nsu_depois = cp.ult_nsu
    return r


def _bloquear_por_656(estado, repo_op, q, politica, r, mensagem, subtipo):
    """656: bloqueia E marca para REVISÃO HUMANA. Não volta sozinho.

    A diferença entre bloquear e exigir revisão é o que separa "espere" de
    "alguém precisa olhar". O cooldown sozinho faria a empresa voltar à fila
    automaticamente daqui a 70 minutos — e se a causa do consumo indevido
    continuar de pé, o segundo 656 vem junto, agravando.

    Por isso os dois: o tempo passa, mas a empresa só volta a ser consultada
    depois que alguém encerrar a revisão.
    """
    ate = q + timedelta(seconds=politica.cooldown_consumo_indevido_segundos)
    estado.bloquear(ate, f"consumo indevido: {higienizar(mensagem)}")
    try:
        estado.exigir_revisao_de_sequencia(
            f"656 ({subtipo}): consulta automática suspensa até revisão")
    except Exception:
        pass
    repo_op.salvar(estado)
    r.consultada = True
    r.estado, r.subtipo = CONSUMO_INDEVIDO, subtipo
    r.motivo = higienizar(mensagem)
    r.exige_revisao = True
    r.segundos_para_liberar = politica.cooldown_consumo_indevido_segundos


def recuperar_nsu(dados_dir, identidade, nsu, fabrica_fonte, *,
                  ambiente=PRODUCAO, politica: Politica = POLITICA_PADRAO,
                  cuf: str = "", agora: datetime | None = None,
                  repo_op=None) -> ResultadoCTe:
    """`consNSU` — buscar UM NSU. **Só para lacuna identificada.**

    POR QUE NÃO É UMA VARREDURA
        `consNSU` existe na NT e é a única recuperação pontual do CT-e — não há
        `consChCTe`. Mas usá-la para caminhar NSU a NSU seria varrer pela porta
        errada: cada chamada gasta cota, e a varredura é o `distNSU`.

        Por isso esta função pede o NSU. Ela não descobre lacuna, não itera e
        não avança checkpoint: quem sabe qual documento falta é quem comparou o
        acervo com a sequência, e o resultado dessa comparação é o argumento.

    O CHECKPOINT NÃO ANDA AQUI
        Recuperar uma lacuna do passado não move a posição do presente. Mexer
        no `ultNSU` a partir de um `consNSU` faria a varredura pular ou repetir
        — e no CT-e o que for pulado não volta.
    """
    q = agora or datetime.now(timezone.utc)
    amb = resolver_ambiente(ambiente)
    ident = normalizar(identidade)
    dados = Path(dados_dir)
    repo_op = repo_op or op.RepositorioOperacao(dados)

    r = elegivel(dados, identidade, ambiente=amb, politica=politica, agora=q,
                 repo_op=repo_op)
    if not r.autorizada:
        return r

    alvo = normalizar_nsu(nsu)
    if nsu_int(alvo) <= 0:
        r.autorizada = False
        r.estado = ERRO_DO_SERVICO
        r.motivo = "informe o NSU da lacuna; `consNSU` não varre"
        return r

    estado = repo_op.carregar(ident.valor, SERVICO, amb)
    estado.registrar_consulta(q)
    try:
        fonte = fabrica_fonte(ident.valor, amb)
        resposta = fonte.consultar_nsu(ident.valor, alvo, cuf)
    except C.ConsumoIndevido as e:
        _bloquear_por_656(estado, repo_op, q, politica, r, str(e), "656")
        return r
    except Exception as e:
        estado.registrar_falha(ERRO_DE_REDE, "")
        repo_op.salvar(estado)
        r.consultada = True
        r.estado = ERRO_DE_REDE
        r.erro = f"{type(e).__name__}: {higienizar(str(e))}"
        return r

    r.consultada = True
    r.cstat = resposta.cstat
    try:
        lote = C.resposta_para_lote(resposta)
    except Exception as e:
        estado.registrar_falha(ERRO_DO_SERVICO, resposta.cstat)
        repo_op.salvar(estado)
        r.estado = ERRO_DO_SERVICO
        r.erro = f"{type(e).__name__}: {higienizar(str(e))}"
        return r

    if lote.sem_documentos:
        estado.registrar_sucesso(resposta.cstat, SEM_DOCUMENTOS)
        repo_op.salvar(estado)
        r.estado = SEM_DOCUMENTOS
        r.motivo = lote.motivo or f"NSU {alvo} não devolveu documento"
        return r

    rel = ent.da_distribuicao(dados, ident.valor, lote.documentos,
                              ambiente=amb, travar=False)
    r.documentos = len(lote.documentos)
    r.persistidos = rel.aceitos
    estado.registrar_sucesso(resposta.cstat, OK)
    repo_op.salvar(estado)
    r.estado = OK if rel.aceitos == r.documentos else FALHA_DE_PERSISTENCIA
    # O checkpoint NÃO anda: ver a docstring.
    r.checkpoint_avancou = False
    return r


def _alcancou_max(cp) -> bool:
    """O `ultNSU` chegou ao `maxNSU` que o serviço informou?

    É a pergunta "acabou a fila?". Sem `maxNSU` a resposta é não — não se
    conclui fim de fila a partir de informação ausente.
    """
    fim = getattr(cp, "max_nsu", "") or ""
    if not fim:
        return False
    return nsu_int(cp.ult_nsu or "0") >= nsu_int(fim)


def _maior_nsu(atual: str, novo: str) -> str:
    """O checkpoint **nunca diminui**.

    O serviço pode devolver um `ultNSU` menor que o nosso — por reprocessamento
    do lado dele, ou por resposta de outro contexto. Aceitá-lo faria a próxima
    varredura recomeçar de um ponto já percorrido, e no CT-e isso é caro: sem
    consulta por chave, cada volta ao passado é cota gasta sem trazer nada.
    """
    a, b = normalizar_nsu(atual or "0"), normalizar_nsu(novo or "0")
    return a if nsu_int(a) >= nsu_int(b) else b


class _FonteDeLote:
    """Adapta um `Lote` já obtido ao contrato de `Fonte` do pipeline.

    Existe para que a persistência do CT-e use **o mesmo portão** da NF-e, em
    vez de um segundo caminho de escrita no acervo.
    """

    def __init__(self, lote):
        self._lote = lote

    def buscar(self, *_a, **_kw):
        return self._lote

    def __iter__(self):
        return iter(self._lote.documentos)
