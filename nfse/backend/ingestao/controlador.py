"""
controlador.py — o ciclo multiempresa da NF-e. Sequencial, com orçamento.

O QUE ELE É
    Uma função que percorre uma lista de `(empresa, serviço, ambiente)`, decide
    para cada uma *se pode consultar agora*, consulta as que podem, e persiste
    tudo antes de passar adiante.

O QUE ELE NÃO É
    Não é agendador. Não cria tarefa do Windows, não roda sozinho, não tem laço
    infinito e não tem paralelismo. Alguém manda executar um ciclo; ele executa
    um ciclo e devolve o relatório.

────────────────────────────────────────────────────────────────────────────
REGRA OFICIAL × POLÍTICA DO FISCALE
────────────────────────────────────────────────────────────────────────────
    Esta separação é o ponto mais importante do arquivo, e está em `Politica`:
    o que a SEFAZ exige tem fonte citada; o que escolhemos é declarado como
    escolha, com o motivo, e pode ser mudado sem quebrar promessa nenhuma.

    **Nenhum número aqui é apresentado como exigência oficial sem fonte.**

────────────────────────────────────────────────────────────────────────────
UMA EMPRESA COM PROBLEMA NUNCA PARA O CICLO
────────────────────────────────────────────────────────────────────────────
    Certificado vencido, `656`, TLS recusado, disco cheio: tudo isso encerra
    **aquela** empresa e o ciclo segue. Foi a decisão da ING 2, e aqui ela vira
    política de operação — com o motivo registrado por empresa, para que
    "por que a empresa X não foi consultada hoje?" tenha resposta.

────────────────────────────────────────────────────────────────────────────
PERSISTIR ANTES DE SEGUIR
────────────────────────────────────────────────────────────────────────────
    checkpoint → estado operacional → auditoria → soltar trava → próxima.

    Nessa ordem. Uma queda de energia entre duas empresas não pode exigir
    descobrir onde o ciclo estava: o disco já sabe.
"""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import acervo as acv
from . import auditoria as aud
from . import checkpoint as cpm
from . import credencial_estado as ce
from . import operacao as op
from . import distribuicao as dist
from . import pipeline as pipe
from . import servico_distribuicao as svc
from .conectores import nfe_dfe as N
from .ambiente import PRODUCAO, Ambiente, resolver as resolver_ambiente
from .distribuicao import higienizar
from .identidade import normalizar
from .trava import TravaOcupada, travar

SERVICO_PADRAO = cpm.NFE_DISTRIBUICAO


# ════════════════════════════════════════════════════════════════════════════
# Política — configuração, não constante espalhada
# ════════════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════════════
# Regras oficiais, separadas POR TIPO DE CONSULTA
# ════════════════════════════════════════════════════════════════════════════
# Esta separação é uma correção: a documentação anterior tratava o teto de
# "20 consultas/hora" como se valesse para a paginação `distNSU`. **Não vale.**
# Na NT 2014.002 esse teto é das consultas PONTUAIS — `consChNFe` (por chave) e
# `consNSU` (por NSU avulso). Registrar um limite no lugar errado leva a
# arquitetar cadência contra uma regra que não existe, e a supor liberdade onde
# talvez haja restrição.
#
# O que fica valendo, dito com precisão:

REGRAS_OFICIAIS_DIST_NSU = {
    "servico": "distNSU — paginação sequencial da Distribuição DF-e",
    "confirmado": (
        "`656` (consumo indevido) bloqueia o CNPJ por cerca de 1 hora",
        "nova consulta dentro de 1 h após receber `137` é consumo indevido",
        "lote máximo de 50 documentos por resposta",
        "retenção de cerca de 3 meses a partir do NSU 0",
    ),
    "nao_confirmado": (
        "intervalo mínimo entre chamadas `distNSU` consecutivas bem-sucedidas "
        "durante a paginação",
        "existência de teto horário próprio para `distNSU`",
        "existência de limite diário",
    ),
    "fonte": "NT 2014.002 (conferido 13/08/2026)",
}

REGRAS_OFICIAIS_CONSULTA_PONTUAL = {
    "servico": "consChNFe (por chave) e consNSU (por NSU avulso)",
    "confirmado": (
        "limite de 20 consultas por hora — é DESTE tipo de consulta, "
        "não da paginação `distNSU`",
    ),
    "nao_confirmado": (
        "se o teto é por chave, por NSU, ou somado entre os dois",
    ),
    "fonte": "NT 2014.002 (conferido 13/08/2026)",
}


@dataclass(frozen=True)
class Politica:
    """Orçamento e cadência do ciclo.

    FONTE DE CADA NÚMERO
    ────────────────────
    O que é **oficial** está em `REGRAS_OFICIAIS_DIST_NSU` e
    `REGRAS_OFICIAIS_CONSULTA_PONTUAL`, separados por tipo de consulta e com a
    fonte citada. O que está aqui embaixo é **escolha do FISCALE**:
    conservadora, revisável, e sem pretensão de ser exigência da SEFAZ.

    Onde a escolha copia um número oficial, o comentário diz de onde veio —
    e onde ela é só prudência nossa, diz isso também.
    """
    # ── orçamento do ciclo ────────────────────────────────────────────────
    max_empresas_por_ciclo: int = 10
    max_lotes_por_empresa: int = 10          # 500 NSU; foi o usado na ING 4A
    duracao_maxima_s: int = 900              # 15 min: um ciclo que não prende ninguém

    # ── cadência por empresa ──────────────────────────────────────────────
    cooldown_em_dia_min: int = 60            # espelha a regra oficial do `137`
    cooldown_consumo_indevido_min: int = 60  # espelha a regra oficial do `656`
    cooldown_pendente_min: int = 5           # escolha nossa: há mais a buscar
    backoff_min: tuple = (5, 15, 60)         # escolha nossa
    # Teto de segurança POR EMPRESA, escolha NOSSA. O valor 20 veio do teto
    # oficial das consultas pontuais (`consChNFe`/`consNSU`) — que **não** é
    # regra da paginação `distNSU`. Mantemos o número como prudência: enquanto
    # não houver fonte sobre cadência de `distNSU`, um teto qualquer é melhor
    # do que nenhum. Não é exigência da SEFAZ, e pode ser revisto.
    teto_consultas_por_hora: int = 20

    # ── comportamento ─────────────────────────────────────────────────────
    consultar_novas_empresas: bool = False   # onboarding é manual — lição da MONTE
    consultar_em_divergencia: bool = False   # MONTE congelada
    usar_trava: bool = True

    def backoff_para(self, falhas: int) -> int:
        """Minutos de espera após N falhas consecutivas."""
        if falhas <= 0:
            return 0
        return self.backoff_min[min(falhas, len(self.backoff_min)) - 1]


POLITICA_PADRAO = Politica()

# ── motivos de não-consulta, para o relatório ───────────────────────────────
MOTIVO_ELEGIVEL = ""
MOTIVO_ONBOARDING = "empresa nunca sincronizada — exige onboarding manual"
MOTIVO_DIVERGENCIA = "divergência externa não resolvida"
MOTIVO_DESABILITADA = "desabilitada manualmente"
MOTIVO_CERT_EXPIRADO = "certificado expirado"
MOTIVO_CREDENCIAL = "credencial indisponível"
MOTIVO_COOLDOWN = "em cooldown"
MOTIVO_TETO_HORA = "teto de consultas por hora atingido"
MOTIVO_TRAVA = "já há uma sincronização em andamento"
MOTIVO_CHECKPOINT = "checkpoint corrompido"
MOTIVO_ORCAMENTO = "orçamento do ciclo esgotado"
MOTIVO_TEMPO = "duração máxima do ciclo atingida"
MOTIVO_HUB = ("escritório: consultado pela fonte autXML, não pelo ciclo por empresa")
MOTIVO_SISTEMICO = "falha sistêmica de armazenamento — ciclo abortado"
MOTIVO_REVISAO_SEQUENCIA = ("656 de sequência não resolvido — "
                            "fora da automação até revisão")


def armazenamento_saudavel(dados_dir) -> tuple:
    """A raiz de dados ainda aceita escrita e releitura? `(ok, detalhe)`.

    POR QUE UMA SONDA, E NÃO UM PALPITE
        Falha de persistência tem duas causas de consequências opostas. Se é
        **daquela empresa** — uma colisão, um arquivo travado por antivírus —,
        parar o ciclo inteiro puniria as outras três sem motivo. Se é do
        **armazenamento** — disco cheio, rede caída, pasta somem —, seguir para
        a próxima empresa é continuar consultando a SEFAZ e jogando fora o que
        vem, gastando cota para perder documento.

        Adivinhar qual das duas é, pelo texto da exceção, seria chute. A sonda
        responde com fato: escreve um arquivo temporário na raiz, relê e apaga.
        Barata, e conclusiva na direção que importa — se ela falha, o problema
        certamente não é de uma empresa só.

    O QUE ELA NÃO PROVA
        Sonda verde não garante acervo íntegro; garante que a raiz responde.
        Por isso a falha de persistência **nunca** avança checkpoint, verde ou
        não: quem decide isso é o `ingerir`, e ele já não avança.
    """
    raiz = Path(dados_dir)
    marca = b"fiscale-sonda"
    alvo = None
    try:
        raiz.mkdir(parents=True, exist_ok=True)
        fd_, tmp = tempfile.mkstemp(dir=str(raiz), prefix=".sonda-")
        alvo = Path(tmp)
        with os.fdopen(fd_, "wb") as f:
            f.write(marca)
            f.flush()
            os.fsync(f.fileno())
        if alvo.read_bytes() != marca:
            return False, "a raiz de dados releu conteúdo diferente do gravado"
        return True, ""
    except Exception as exc:
        # `except Exception`, e não `OSError`: uma sonda que LEVANTA é pior que
        # inútil — derrubaria o ciclo justamente no caminho de erro que ela
        # existe para diagnosticar. Caminho inválido, permissão, codificação:
        # tudo vira "não respondeu", que é a resposta honesta.
        return False, f"a raiz de dados não aceita escrita: {higienizar(exc)}"
    finally:
        if alvo is not None:
            try:
                alvo.unlink()
            except OSError:
                pass


# ════════════════════════════════════════════════════════════════════════════
# O universo do ciclo — quem entra na avaliação, antes de qualquer filtro
# ════════════════════════════════════════════════════════════════════════════
def universo(dados_dir, cad=None) -> list:
    """TODAS as empresas cadastradas, sem filtro nenhum.

    POR QUE O CONTROLADOR PRECISA SER O DONO DESTA LISTA
        Enquanto o universo vinha de fora, cada chamador inventava o seu. Um
        deles derivou a lista das PASTAS existentes em `dados/` — o que parece
        razoável e está errado: empresa que nunca foi consultada não tem pasta,
        então a lista excluía exatamente quem mais precisava aparecer. A
        `27.***.***/0001-64` sumiu assim, e sumiu **em silêncio**: o relatório
        fechava certo, com um total menor.

        A regra é: cadastrada entra; o motivo de não consultar é resultado da
        avaliação, nunca um pré-filtro. Empresa sem credencial utilizável sai
        do ciclo como `CREDENCIAL_INDISPONIVEL` **com motivo escrito**, não por
        omissão.

    NÃO FILTRA POR CREDENCIAL DE PROPÓSITO
        `cadastro.listar_empresas(com_credencial=True)` existe e é tentador
        aqui. Usá-lo faria a empresa desaparecer de novo, por outro caminho.
    """
    from . import cadastro as cad_mod

    c = cad if cad is not None else cad_mod.carregar(Path(dados_dir))
    return [e.identificador.valor for e in c.listar_empresas()]


def _universo_ou(dados_dir, identidades, cad=None) -> list:
    """`None` significa "o cadastro inteiro"; uma lista explícita é respeitada.

    A lista explícita continua existindo para a validação progressiva — uma
    empresa por vez, com aprovação — e é sempre uma decisão declarada de quem
    chama, não o resultado acidental de um `glob` em disco.
    """
    if identidades is None:
        return universo(dados_dir, cad=cad)
    return list(identidades)


# ════════════════════════════════════════════════════════════════════════════
# Bootstrap: derivar a agenda do histórico real, em vez de presumir "pode já"
# ════════════════════════════════════════════════════════════════════════════
BOOTSTRAP_SEM_HISTORICO = "SEM_HISTORICO"
BOOTSTRAP_JA_TEM_AGENDA = "JA_TEM_AGENDA"
BOOTSTRAP_DERIVADO = "DERIVADO_DA_TRILHA"


def bootstrap_operacao(dados_dir, identidade, servico: str = SERVICO_PADRAO,
                       ambiente=PRODUCAO, politica: Politica = POLITICA_PADRAO,
                       repo=None, forcar: bool = False) -> dict:
    """Deriva a agenda a partir das `CONSULTA` REAIS já auditadas.

    O PROBLEMA QUE ISTO RESOLVE
        Um `<servico>.<ambiente>.operacao.json` inexistente é lido como estado
        novo — sem `proxima_consulta_permitida_em`. Para as empresas que já
        foram consultadas antes de o controlador existir, isso significaria
        "pode consultar agora", **logo depois** de uma resposta que a regra
        oficial manda esperar uma hora.

        Presumir permissão é o erro caro aqui: é assim que se toma `656`.

    O QUE ENTRA COMO EVIDÊNCIA
        Apenas o ato `CONSULTA`. **`CONSULTA_RECONSTRUIDA` não conta** — ela
        descreve uma chamada que já era passado quando foi registrada, e
        tratá-la como chamada recente inventaria um bloqueio que a SEFAZ não
        impôs.

    A JANELA DERIVADA, POR DESFECHO
        `137`/`138` com `ultNSU == maxNSU` → 1 hora a partir daquela consulta.
            É a regra oficial: consultar de novo dentro de 1 h após `137` é
            consumo indevido.
        `656` → 1 hora a partir daquela consulta, com o bloqueio preservado.
        `ultNSU < maxNSU` (paginação incompleta) → **nenhuma espera inventada**.
            Não há regra oficial de intervalo entre páginas, e fabricar uma
            hora aqui pararia uma varredura legítima pela metade.
        falha técnica → backoff da nossa política.
    """
    dados = Path(dados_dir)
    ident = normalizar(identidade)
    if not ident.valido:
        return {"resultado": BOOTSTRAP_SEM_HISTORICO, "motivo": ident.motivo}
    amb = resolver_ambiente(ambiente)
    repo = repo or op.RepositorioOperacao(dados)
    estado = repo.carregar(ident.valor, servico, amb)

    if (estado.ultima_consulta_em or estado.proxima_consulta_permitida_em) \
            and not forcar:
        return {"resultado": BOOTSTRAP_JA_TEM_AGENDA,
                "empresa": ident.mascarado(),
                "proxima_consulta_permitida_em":
                    estado.proxima_consulta_permitida_em or None}

    # Só CONSULTA real — `consultas()` já exclui as reconstruídas por padrão.
    consultas = [c for c in aud.abrir(dados, ident.valor).consultas()
                 if c.get("servico") == servico and c.get("ambiente") == amb.nome]
    if not consultas:
        return {"resultado": BOOTSTRAP_SEM_HISTORICO, "empresa": ident.mascarado(),
                "motivo": "nenhuma CONSULTA real auditada"}

    u = consultas[-1]
    quando = op._de_iso(u.get("inicio") or "") or datetime.now(timezone.utc)
    cstat = str(u.get("cstat") or "")
    ult, mx = u.get("ult_nsu") or "", u.get("max_nsu") or ""
    limpa = (u.get("transporte_ok") and not u.get("erro_tecnico")
             and u.get("resultado") in ("DOCUMENTOS_ENCONTRADOS", "NENHUM_DOCUMENTO"))

    estado.ultima_consulta_em = op._iso(quando)
    estado.ultimo_cstat = cstat
    estado.ultimo_resultado = str(u.get("resultado") or "")
    # A janela de frequência recebe esta consulta; sem isso o teto por hora
    # começaria zerado logo depois de uma rajada.
    if op._iso(quando) not in estado.consultas_recentes:
        estado.consultas_recentes.append(op._iso(quando))

    motivo = ""
    if cstat == "656":
        estado.falhas_consecutivas = max(estado.falhas_consecutivas, 1)
        estado.bloquear(quando + timedelta(
            minutes=politica.cooldown_consumo_indevido_min),
            "cStat 656 — consumo indevido (derivado da trilha)")
        motivo = "656 — bloqueio de 1 h preservado"
    elif not limpa:
        estado.falhas_consecutivas = max(estado.falhas_consecutivas, 1)
        estado.bloquear(quando + timedelta(
            minutes=politica.backoff_para(estado.falhas_consecutivas)),
            "backoff derivado da última falha auditada")
        motivo = "falha técnica — backoff da nossa política"
    elif ult and mx and cpm.nsu_int(ult) == cpm.nsu_int(mx):
        estado.ultima_tentativa_ok_em = op._iso(quando)
        estado.ultima_sincronia_confirmada_em = op._iso(quando)
        estado.bloquear(quando + timedelta(minutes=politica.cooldown_em_dia_min),
                        "em dia — janela oficial de 1 h após a última resposta")
        motivo = "ultNSU == maxNSU — janela oficial de 1 h materializada"
    else:
        # Paginação incompleta: NÃO se inventa espera de uma hora entre páginas.
        estado.ultima_tentativa_ok_em = op._iso(quando)
        estado.liberar()
        motivo = "paginação incompleta — nenhuma espera inventada"

    repo.salvar(estado)
    return {"resultado": BOOTSTRAP_DERIVADO, "empresa": ident.mascarado(),
            "cstat": cstat, "ult_nsu": ult or None, "max_nsu": mx or None,
            "ultima_consulta_em": estado.ultima_consulta_em,
            "proxima_consulta_permitida_em":
                estado.proxima_consulta_permitida_em or None,
            "motivo": motivo}


def bootstrap_todas(dados_dir, identidades=None, servico: str = SERVICO_PADRAO,
                    ambiente=PRODUCAO, politica: Politica = POLITICA_PADRAO,
                    forcar: bool = False) -> list:
    """`identidades=None` significa o cadastro inteiro — veja `universo()`."""
    repo = op.RepositorioOperacao(Path(dados_dir))
    alvos = _universo_ou(dados_dir, identidades)
    return [bootstrap_operacao(dados_dir, i, servico, ambiente, politica,
                               repo=repo, forcar=forcar) for i in alvos]


@dataclass
class Elegibilidade:
    """`esta empresa pode ser consultada agora?` — e por que não."""
    identidade_mascarada: str
    estado: str
    pode: bool
    motivo: str = ""
    detalhe: str = ""
    segundos_para_liberar: int = 0

    def resumo(self) -> dict:
        return {"empresa": self.identidade_mascarada, "estado": self.estado,
                "pode_consultar": self.pode, "motivo": self.motivo or None,
                "detalhe": self.detalhe or None,
                "libera_em_s": self.segundos_para_liberar or None}


def avaliar(dados_dir, identidade, cad=None, servico: str = SERVICO_PADRAO,
            ambiente=PRODUCAO, politica: Politica = POLITICA_PADRAO,
            agora: datetime | None = None) -> Elegibilidade:
    """Decide, **sem tocar a rede**, se a empresa entra no ciclo.

    A ordem das checagens é a ordem da resposta: quem lê o relatório precisa do
    motivo mais fundamental, não do primeiro que o código encontrou. Certificado
    vencido vem antes de cooldown, porque renovar o certificado é o que resolve.
    """
    from . import cadastro as cad_mod

    q = agora or datetime.now(timezone.utc)
    dados = Path(dados_dir)
    ident = normalizar(identidade)
    m = ident.mascarado() if ident.valido else "<inválida>"
    if not ident.valido:
        return Elegibilidade(m, op.ERRO_PERMANENTE, False, MOTIVO_CREDENCIAL,
                             ident.motivo)

    amb = resolver_ambiente(ambiente)
    cad = cad if cad is not None else cad_mod.carregar(dados)
    repo_op = op.RepositorioOperacao(dados)
    estado_op = repo_op.carregar(ident.valor, servico, amb)

    # 0. o HUB do `autXML` não é cliente.
    #
    # Ele consulta com o certificado dele e o checkpoint dele, e o serviço é o
    # MESMO `NFE_DISTRIBUICAO`. Se o ciclo por empresa também o consultasse,
    # dois caminhos escreveriam o mesmo ponteiro — e o segundo pularia o que o
    # primeiro trouxe. É o descontrole que o CT-e levou quatro fases para
    # desfazer.
    #
    # A recusa é RESULTADO COM MOTIVO, não pré-filtro. `universo()` documenta
    # por que: uma empresa já sumiu em silêncio de um relatório que fechava
    # certo com o total menor. O hub aparece, e aparece dizendo por que não
    # entra.
    if servico == cpm.NFE_DISTRIBUICAO:
        from . import autxml as _autxml
        if _autxml.e_hub(dados, ident.valor):
            return Elegibilidade(m, op.DESABILITADO, False, MOTIVO_HUB,
                                 "a fila é do escritório; ver `autxml.json`")

    # 1. desabilitada à mão — decisão humana vence tudo
    if estado_op.desabilitado:
        return Elegibilidade(m, op.DESABILITADO, False, MOTIVO_DESABILITADA,
                             estado_op.motivo_desabilitado)

    # 2. credencial: sem ela não há o que discutir — mas a empresa NÃO some do
    #    relatório por isso. Sai do ciclo com o motivo escrito.
    cred = cad.obter_certificado_da_empresa(ident.valor)
    if cred is None:
        # Existe um .pfx no disco que parece ser dela? Dizer isso muda a ação
        # de quem lê: não é "arrume o certificado", é "informe a senha".
        # `procurar_pfx_nao_vinculado` não abre o arquivo nem cria vínculo.
        orfao = ce.procurar_pfx_nao_vinculado(cad, ident.valor, dados)
        detalhe = ("certificado encontrado no disco, mas a credencial ainda "
                   "precisa ser validada — informe a senha pela tela de "
                   "certificados" if orfao else "nenhum certificado associado")
        return Elegibilidade(m, op.CREDENCIAL_INDISPONIVEL, False,
                             MOTIVO_CREDENCIAL, detalhe)
    est_cred = ce.avaliar(cred, dados)
    if est_cred.estado == ce.EXPIRADO:
        return Elegibilidade(m, op.CERTIFICADO_EXPIRADO, False,
                             MOTIVO_CERT_EXPIRADO, est_cred.detalhe)
    if not est_cred.utilizavel:
        return Elegibilidade(m, op.CREDENCIAL_INDISPONIVEL, False,
                             MOTIVO_CREDENCIAL, est_cred.acao_necessaria)

    # 3. checkpoint legível
    repo_cp = cpm.RepositorioCheckpoint(dados)
    try:
        cp = repo_cp.carregar(ident.valor, servico, amb)
    except cpm.CheckpointCorrompido as exc:
        return Elegibilidade(m, op.ERRO_PERMANENTE, False, MOTIVO_CHECKPOINT,
                             higienizar(exc))

    # 3.5 revisão de sequência — a lição do 656 da TRX
    # Vem ANTES do cooldown de propósito: o tempo não resolve isto, e quem lê
    # o relatório precisa do motivo real, não de "em cooldown".
    if estado_op.revisao_sequencia:
        return Elegibilidade(m, op.REVISAO_DE_SEQUENCIA, False,
                             MOTIVO_REVISAO_SEQUENCIA,
                             estado_op.origem_bloqueio
                             or estado_op.ultimo_656_subtipo)

    # 4. divergência externa — a lição da MONTE
    if cp.estado_sincronismo == cpm.SINCRONISMO_DIVERGENCIA \
            and not politica.consultar_em_divergencia:
        return Elegibilidade(m, op.DIVERGENCIA_EXTERNA, False,
                             MOTIVO_DIVERGENCIA, cp.alerta_administrativo)

    # 5. empresa nunca sincronizada: `checkpoint = 0` NÃO significa "nunca houve
    #    consumo" — foi exatamente o que a MONTE ensinou. Onboarding é manual.
    nunca_nossa = (cpm.nsu_int(cp.ult_nsu) == 0
                   and not cp.origem_ult_nsu
                   and not estado_op.ultima_consulta_em)
    if nunca_nossa and not politica.consultar_novas_empresas:
        return Elegibilidade(m, op.AGUARDANDO_ONBOARDING, False,
                             MOTIVO_ONBOARDING,
                             "primeira consulta exige decisão explícita")

    # 6. bloqueio/cooldown persistido
    if estado_op.em_cooldown(q):
        segundos = estado_op.segundos_para_liberar(q)
        estado = (op.BLOQUEADO_CONSUMO_INDEVIDO
                  if estado_op.ultimo_cstat == "656" else
                  op.ERRO_TEMPORARIO if estado_op.falhas_consecutivas else
                  op.EM_SINCRONIA)
        return Elegibilidade(m, estado, False, MOTIVO_COOLDOWN,
                             estado_op.motivo_bloqueio, segundos)

    # 7. teto de frequência
    if estado_op.consultas_na_janela(60, q) >= politica.teto_consultas_por_hora:
        return Elegibilidade(m, op.ERRO_TEMPORARIO, False, MOTIVO_TETO_HORA,
                             f"{politica.teto_consultas_por_hora} consultas na "
                             f"última hora")

    # 8. trava — outra execução já está nesta unidade
    if politica.usar_trava:
        try:
            with travar(dados, ident.valor, servico, amb):
                pass
        except TravaOcupada as exc:
            return Elegibilidade(m, op.EM_EXECUCAO, False, MOTIVO_TRAVA,
                                 higienizar(exc))

    estado = (op.EM_SINCRONIA if cp.estado_sincronismo == cpm.SINCRONISMO_EM_DIA
              else op.PENDENTE)
    return Elegibilidade(m, estado, True, MOTIVO_ELEGIVEL,
                         f"pendentes: {cp.pendentes}" if cp.max_nsu else "")


# ════════════════════════════════════════════════════════════════════════════
# O ciclo
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class ResultadoEmpresa:
    identidade_mascarada: str
    estado: str
    consultada: bool = False
    motivo: str = ""
    nsu_antes: str = ""
    nsu_depois: str = ""
    documentos: int = 0
    chamadas: int = 0
    cstat: str = ""
    subtipo: str = ""              # subtipo do 656, quando houver
    erro: str = ""

    def linha(self) -> str:
        if not self.consultada:
            extra = f"\n  {self.motivo}" if self.motivo else ""
            return (f"{self.identidade_mascarada}\n  não consultada\n"
                    f"  {self.estado}{extra}")
        # `->` e não `→`: este texto vai para o console do Windows, que usa
        # cp1252 e derruba o programa em qualquer caractere fora dele. Um
        # relatório que quebra na hora de mostrar o resultado é pior do que
        # um relatório feio.
        return (f"{self.identidade_mascarada}\n"
                f"  checkpoint {self.nsu_antes.lstrip('0') or '0'} -> "
                f"{self.nsu_depois.lstrip('0') or '0'}\n"
                f"  {self.documentos} documento(s)\n  {self.estado}")


@dataclass
class ResultadoCiclo:
    inicio: str = ""
    fim: str = ""
    duracao_s: float = 0.0
    politica: dict = field(default_factory=dict)
    empresas: list = field(default_factory=list)
    interrompido_por: str = ""

    # ── contagens ─────────────────────────────────────────────────────────
    @property
    def avaliadas(self) -> int:
        return len(self.empresas)

    @property
    def consultadas(self) -> int:
        return sum(1 for e in self.empresas if e.consultada)

    @property
    def documentos(self) -> int:
        return sum(e.documentos for e in self.empresas)

    def por_estado(self, estado: str) -> int:
        return sum(1 for e in self.empresas if e.estado == estado)

    def resumo(self) -> dict:
        return {
            "inicio": self.inicio, "duracao_s": round(self.duracao_s, 2),
            "avaliadas": self.avaliadas, "consultadas": self.consultadas,
            "documentos": self.documentos,
            "em_sincronia": self.por_estado(op.EM_SINCRONIA),
            "pendentes": self.por_estado(op.PENDENTE),
            "bloqueadas": self.por_estado(op.BLOQUEADO_CONSUMO_INDEVIDO),
            "divergencia_externa": self.por_estado(op.DIVERGENCIA_EXTERNA),
            "aguardando_onboarding": self.por_estado(op.AGUARDANDO_ONBOARDING),
            "certificado_pendente": (self.por_estado(op.CERTIFICADO_EXPIRADO)
                                     + self.por_estado(op.CREDENCIAL_INDISPONIVEL)),
            # Separados também, porque a ação é outra: certificado expirado se
            # renova com o cliente; credencial indisponível se resolve
            # informando a senha na tela.
            "certificado_expirado": self.por_estado(op.CERTIFICADO_EXPIRADO),
            "credencial_indisponivel": self.por_estado(op.CREDENCIAL_INDISPONIVEL),
            "revisao_sequencia": self.por_estado(op.REVISAO_DE_SEQUENCIA),
            "desabilitadas": self.por_estado(op.DESABILITADO),
            "em_execucao": self.por_estado(op.EM_EXECUCAO),
            "erros_temporarios": self.por_estado(op.ERRO_TEMPORARIO),
            "erros_permanentes": self.por_estado(op.ERRO_PERMANENTE),
            "interrompido_por": self.interrompido_por or None,
        }

    def relatorio(self) -> str:
        """O texto que a tela mostra. Sem segredo, sem XML, sem CNPJ inteiro."""
        r = self.resumo()
        linhas = ["Ciclo NF-e", ""]
        linhas.append(f"{'Empresas avaliadas':26}{r['avaliadas']:>5}")
        for rotulo, chave in (("Consultadas", "consultadas"),
                              ("Novos documentos", "documentos")):
            linhas.append(f"{rotulo:26}{r[chave]:>5}")
        linhas.append("")

        # A classificação tem de FECHAR com o total avaliado. Se a soma não
        # bate, alguém sumiu do relatório — foi exatamente assim que a
        # 27.***.***/0001-64 passou despercebida — e a linha abaixo grita.
        classificacao = (("Em sincronia", "em_sincronia"),
                         ("Pendentes", "pendentes"),
                         ("Bloqueadas (656)", "bloqueadas"),
                         ("Divergência externa", "divergencia_externa"),
                         ("Revisao de sequencia", "revisao_sequencia"),
                         ("Aguardando onboarding", "aguardando_onboarding"),
                         ("Certificado expirado", "certificado_expirado"),
                         ("Credencial indisponível", "credencial_indisponivel"),
                         ("Desabilitadas", "desabilitadas"),
                         ("Em execução", "em_execucao"),
                         ("Erros temporários", "erros_temporarios"),
                         ("Erros permanentes", "erros_permanentes"))
        soma = 0
        for rotulo, chave in classificacao:
            soma += r[chave]
            if r[chave]:
                linhas.append(f"{rotulo:26}{r[chave]:>5}")
        linhas.append(f"{'':26}{'-' * 5:>5}")
        linhas.append(f"{'Total classificado':26}{soma:>5}")
        if soma != r["avaliadas"]:
            linhas.append(f"ATENCAO: {r['avaliadas'] - soma} empresa(s) "
                          f"avaliada(s) sem classificacao no relatorio")
        if self.interrompido_por:
            linhas += ["", f"Ciclo interrompido: {self.interrompido_por}"]
        linhas.append("")
        for e in self.empresas:
            linhas += [e.linha(), ""]
        return "\n".join(linhas)


def executar_ciclo(dados_dir, identidades=None, servico: str = SERVICO_PADRAO,
                   ambiente=PRODUCAO, politica: Politica = POLITICA_PADRAO,
                   fabrica_fonte=None, dry_run: bool = False,
                   agora: datetime | None = None) -> ResultadoCiclo:
    """Percorre as unidades de controle, uma por vez.

    `identidades=None` significa o cadastro inteiro — veja `universo()`.
    `fabrica_fonte(identidade, ambiente)` devolve a `Fonte` daquela empresa.
    Em `dry_run` ela nem é chamada: o ciclo só decide e relata.
    """
    from . import cadastro as cad_mod

    t0 = time.monotonic()
    q = agora or datetime.now(timezone.utc)
    dados = Path(dados_dir)
    amb = resolver_ambiente(ambiente)
    cad = cad_mod.carregar(dados)
    identidades = _universo_ou(dados, identidades, cad=cad)
    repo_op = op.RepositorioOperacao(dados)
    repo_cp = cpm.RepositorioCheckpoint(dados)

    res = ResultadoCiclo(inicio=q.isoformat(timespec="seconds"),
                         politica={"max_empresas": politica.max_empresas_por_ciclo,
                                   "max_lotes": politica.max_lotes_por_empresa,
                                   "duracao_maxima_s": politica.duracao_maxima_s,
                                   "dry_run": dry_run})
    consultadas = 0

    # O ciclo percorre na ORDEM decidida por `ordem_do_ciclo`: elegíveis
    # primeiro, quem espera há mais tempo antes. Sem isto o teto de empresas
    # por ciclo gastaria o orçamento com quem viesse primeiro na lista — que é
    # ordem de cadastro, não de necessidade.
    for identidade in _ordenar(dados, identidades, cad, servico, amb,
                               politica, q):
        # ── orçamento: tempo e quantidade ──────────────────────────────────
        if time.monotonic() - t0 >= politica.duracao_maxima_s:
            res.interrompido_por = MOTIVO_TEMPO
            break
        if consultadas >= politica.max_empresas_por_ciclo:
            res.interrompido_por = MOTIVO_ORCAMENTO
            break

        # ── a consulta passa pela PORTA ÚNICA ──────────────────────────────
        # Elegibilidade, trava, checkpoint, auditoria, estado operacional e
        # tratamento do 656 vivem em `servico_distribuicao`. O ciclo decide
        # ORDEM e ORÇAMENTO; não reimplementa política nenhuma.
        if dry_run:
            el = avaliar(dados, identidade, cad=cad, servico=servico,
                         ambiente=amb, politica=politica, agora=q)
            r = ResultadoEmpresa(identidade_mascarada=el.identidade_mascarada,
                                 estado=el.estado, motivo=el.motivo)
            ident = normalizar(identidade)
            if ident.valido:
                cp_antes = repo_cp.carregar(ident.valor, servico, amb)
                r.nsu_antes = r.nsu_depois = cp_antes.ult_nsu
            if not el.pode:
                r.motivo = f"{el.motivo}{'  · ' + el.detalhe if el.detalhe else ''}"
                res.empresas.append(r)
                continue
            r.motivo = "SERIA CONSULTADA (dry-run)"
            res.empresas.append(r)
            consultadas += 1
            continue

        rc = svc.consultar_empresa(
            dados, identidade, fabrica_fonte, servico=servico, ambiente=amb,
            politica=politica, cad=cad, agora=q, repo_op=repo_op,
            repo_cp=repo_cp, avaliar=avaliar)

        r = ResultadoEmpresa(
            identidade_mascarada=rc.identidade_mascarada, estado=rc.estado,
            motivo=rc.motivo, consultada=rc.consultada, cstat=rc.cstat,
            subtipo=rc.subtipo, chamadas=rc.chamadas, documentos=rc.documentos,
            nsu_antes=rc.nsu_antes, nsu_depois=rc.nsu_depois, erro=rc.erro)

        if not rc.autorizada:
            r.motivo = f"{rc.motivo}{'  · ' + rc.detalhe if rc.detalhe else ''}"
            res.empresas.append(r)
            continue

        res.empresas.append(r)
        consultadas += 1

        # ── falha de persistência: de quem é o problema? ───────────────────
        # O `ingerir` já garantiu o essencial — o checkpoint NÃO andou. Falta
        # decidir se as outras empresas podem seguir. A sonda responde com
        # fato, e só ela autoriza abortar o ciclo inteiro.
        if rc.falha_de_persistencia:
            saudavel, detalhe = armazenamento_saudavel(dados)
            if not saudavel:
                res.interrompido_por = f"{MOTIVO_SISTEMICO} ({detalhe})"
                r.erro = (f"{r.erro}  · ciclo abortado: o armazenamento não "
                          f"respondeu à sonda").strip()
                break
            r.erro = (f"{r.erro}  · falha desta empresa; a raiz de dados "
                      f"passou na sonda").strip()

    res.duracao_s = time.monotonic() - t0
    res.fim = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return res


def _ordenar(dados, identidades, cad, servico, amb, politica, agora) -> list:
    """A fila do ciclo, como lista de identidades. Critério em `ordem_do_ciclo`."""
    repo_op = op.RepositorioOperacao(dados)
    fila = []
    for i in identidades:
        el = avaliar(dados, i, cad=cad, servico=servico, ambiente=amb,
                     politica=politica, agora=agora)
        ident = normalizar(i)
        ultima = ""
        if ident.valido:
            ultima = repo_op.carregar(ident.valor, servico,
                                      amb).ultima_consulta_em or ""
        fila.append((el, i, 0 if not ultima else 1, ultima))
    fila.sort(key=lambda x: (not x[0].pode, x[2], x[3]))
    return [i for _, i, _, _ in fila]


def ordem_do_ciclo(dados_dir, identidades=None, servico: str = SERVICO_PADRAO,
                   ambiente=PRODUCAO, politica: Politica = POLITICA_PADRAO,
                   agora: datetime | None = None) -> list:
    """A ordem em que o ciclo visitaria as empresas, com o motivo de cada uma.

    `identidades=None` significa o cadastro inteiro — veja `universo()`. É esta
    a lista que o dry-run deve usar: se ela encolher, alguém desapareceu.

    Critério, nesta ordem:

    1. **elegíveis primeiro** — as não elegíveis vão ao fim, para o relatório;
    2. **quem nunca foi consultada por nós vem antes** de quem já foi. Não é
       artefato de ordenação: uma empresa elegível sem nenhuma consulta nossa é,
       por definição, a que está esperando há mais tempo;
    3. entre as já consultadas, a mais antiga primeiro — mais tempo parado,
       mais chance de documento acumulado.
    """
    from . import cadastro as cad_mod

    dados = Path(dados_dir)
    cad = cad_mod.carregar(dados)
    repo_op = op.RepositorioOperacao(dados)
    amb = resolver_ambiente(ambiente)
    fila = []
    for i in _universo_ou(dados, identidades, cad=cad):
        el = avaliar(dados, i, cad=cad, servico=servico, ambiente=amb,
                     politica=politica, agora=agora)
        ident = normalizar(i)
        e_op = (repo_op.carregar(ident.valor, servico, amb) if ident.valido
                else None)
        ultima = getattr(e_op, "ultima_consulta_em", "") or ""
        # `nunca` como chave separada deixa o critério 2 explícito, em vez de
        # depender de string vazia ordenar antes de uma data.
        fila.append((el, (0 if not ultima else 1), ultima))
    fila.sort(key=lambda x: (not x[0].pode, x[1], x[2]))
    return [el for el, _, _ in fila]
