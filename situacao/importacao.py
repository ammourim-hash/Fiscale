# -*- coding: utf-8 -*-
"""importacao.py — a ÚNICA porta por onde documento entra no envelope.

POR QUE UM PORTÃO, E NÃO CADA FONTE GRAVANDO
    É a lição da APURAÇÃO 6B, e ela custou caro: quando mais de um caminho
    escreve documento fiscal, as regras divergem em silêncio. Um valida, outro
    não; um deduplica, outro cria a segunda cópia; um indexa, outro esquece.
    E o defeito só aparece meses depois, num relatório que não fecha.

    Aqui há **uma** função de escrita. Toda fonte — a assistida, o SITFIS, um
    agregador amanhã — termina nela.

A ORDEM IMPORTA, E ELA É ESTA
    1. valida (é documento? é desta empresa? o vocabulário existe?)
    2. GRAVA e confere a gravação relendo do disco
    3. só então indexa
    4. e registra a tentativa, com desfecho, sempre

    Indexar antes de gravar deixaria o índice apontando para arquivo que não
    existe — e o índice é o que a tela lê. A pessoa veria a certidão listada e
    receberia erro ao abrir.

A TENTATIVA É REGISTRADA MESMO QUANDO DÁ CERTO
    Ela não é log de erro; é o histórico de que se foi buscar. Sem ela, "por
    que a certidão desta empresa é de agosto?" não tem resposta: pode ser que
    ninguém tentou, ou que se tentou e o órgão recusou. São coisas diferentes,
    e só uma delas é problema nosso.

O QUE ELE NÃO FAZ
    Não vai à rede (quem vai é a fonte), não lê PDF (quem lê é o leitor) e não
    decide política (quem decide se vale gastar chamada é o índice).
"""
from __future__ import annotations

from . import armazenamento, leitores, modelo
from .fontes import Colheita


class Recusado(Exception):
    """O documento não entra. A mensagem é escrita para quem está na tela."""


def _identidade_normalizada(valor) -> str:
    d = "".join(c for c in str(valor or "") if c.isdigit())
    if len(d) not in (11, 14):
        raise Recusado("A identidade não é CPF nem CNPJ (%d dígitos)." % len(d))
    return d


def _por_que_nao_leu(esfera: str, tipo: str, bruto: bytes) -> str:
    """Frase curta explicando por que o leitor nao extraiu campo nenhum.

    NUNCA levanta e nunca inventa: quando nem a medicao consegue dizer algo,
    devolve string vazia, e a tela volta a mostrar apenas "validade nao lida".
    """
    try:
        from .leitores import diagnostico as diag
        tem_leitor = (esfera, tipo) in leitores.registro()
    except Exception:
        return ""
    try:
        m = diag.analisar(bruto) or {}
    except Exception:
        return ""

    if not tem_leitor:
        base = "não há leitor calibrado para %s/%s" % (esfera.lower(), tipo.lower())
    else:
        base = "o leitor desta esfera não reconheceu o layout"

    if not m.get("abriu"):
        return (base + "; e o PDF não abriu para medição")[:300]

    # O caso concreto que o Recife produziu: PDF impresso, sem camada de texto.
    # Dizer isso poupa o dia de quem ia escrever parser para uma imagem.
    if m.get("provavel_imagem") or not m.get("tem_texto"):
        base += ("; e este PDF não tem camada de texto (%d página(s), %d "
                 "caractere(s)) — foi impresso, não gerado"
                 % (int(m.get("paginas") or 0), int(m.get("caracteres") or 0)))
        prod = str(m.get("produtor") or "")[:60]
        if prod:
            base += ", /Producer: %s" % prod
    return base[:300]


def importar(raiz, indice, identidade: str, esfera: str, tipo: str,
             bruto: bytes, origem: str = modelo.MANUAL, leitura: dict = None,
             extra: dict = None, auditar=None, usuario: str = "",
             metodo: str = "") -> dict:
    """Grava, indexa e registra. Devolve o que aconteceu.

    `leitura` são campos derivados que quem chamou já conhece — tipicamente
    porque a FONTE os entregou estruturados (a API devolve validade e natureza
    em JSON). Isso é proveniência, não leitura nossa, e por isso é aceito aqui
    sem que o portão abra o PDF.
    """
    ident = _identidade_normalizada(identidade)
    try:
        e, t = modelo.conferir(esfera, tipo)
        # A ORIGEM ENTRA NA MESMA PORTA QUE ESFERA E TIPO.
        #     Proveniência é dado, e dado sem vocabulário vira anotação.
        #     Um typo aqui não quebra nada hoje — ele aparece meses
        #     depois, como uma origem órfã no índice que ninguém explica.
        origem = modelo.conferir_origem(origem)
    except modelo.Invalido as erro:
        raise Recusado(str(erro))

    if not bruto:
        _tentativa(indice, ident, e, t, modelo.ILEGIVEL,
                   "veio vazio", origem, usuario=usuario, metodo=metodo)
        raise Recusado("O documento veio vazio.")

    # O CASO QUE MAIS ACONTECE NA PRÁTICA: o portal responde a página de login
    # ou de erro, com status de sucesso. Guardar isso como se fosse a certidão
    # é pior que não guardar — porque depois alguém confia nela.
    if not armazenamento.eh_pdf(bruto):
        _tentativa(indice, ident, e, t, modelo.ILEGIVEL,
                   "o conteúdo não é um PDF", origem, usuario=usuario,
                   metodo=metodo)
        raise Recusado("O arquivo enviado não é um PDF "
                       "(pode ser uma página de erro do portal).")

    # A LEITURA ACONTECE AQUI, PARA TODO MUNDO.
    #     `leitura=None` significa "leia voce"; um dicionario (mesmo vazio)
    #     significa "eu ja sei, nao leia". Deixar cada chamador decidir se
    #     chama o leitor faria metade dos caminhos indexar sem validade, e o
    #     defeito apareceria como "a certidao da empresa X nao tem data".
    #
    #     O leitor nunca levanta: quando nao entende, devolve {} e o estado
    #     derivado vira SEM_VALIDADE. Ver `leitores/__init__.py`.
    if leitura is None:
        leitura = leitores.ler(e, t, bruto)

    # POR QUE NAO FOI LIDO — medido uma vez, na entrada, e guardado.
    #     Sem isto, documento sem leitor e documento ilegivel chegam a tela
    #     exatamente iguais: um campo de validade vazio. Sao coisas diferentes,
    #     e pedem acoes opostas — "ainda nao escrevi o leitor desta esfera" e
    #     "este PDF e imagem, nenhum leitor por texto vai le-lo nunca".
    #
    #     O diagnostico ja existia como instrumento de medicao (`leitores/
    #     diagnostico.py`); o que faltava era alguem perguntar. Ele nao levanta,
    #     e o resultado e proveniencia: entra no `extra`, nao no documento.
    motivo_sem_leitura = ""
    if not leitura:
        motivo_sem_leitura = _por_que_nao_leu(e, t, bruto)

    armazem = armazenamento.abrir(raiz, ident)
    guardado = armazem.guardar(e, t, bruto, origem=origem,
                               extra=dict(extra or {}))
    captura = armazem.captura(e, t, guardado["id"])
    captura["caminho"] = guardado["caminho"]

    # A PROVENIENCIA DESTA CHEGADA VENCE A DA PRIMEIRA, PARA INDEXAR.
    #     Quando os bytes ja existem, `guardar()` devolve DUPLICATA e nao
    #     reescreve o `captura.json` -- correto, o original e imutavel. Mas o
    #     que se releu do disco e a proveniencia da PRIMEIRA vez, e o `extra`
    #     desta chamada some.
    #
    #     Doi num caso concreto: o mesmo relatorio do SITFIS baixado de novo
    #     traz "apurado_em = hoje", e sem esta linha o indice continuaria com
    #     a data antiga -- ou sem data nenhuma, se a primeira copia veio pelo
    #     canal assistido. O extrato pareceria velho no dia em que foi
    #     buscado.
    #
    #     O disco guarda as duas: a original em `origem`/`capturado_utc`, e
    #     esta em `copias`.
    if extra:
        captura["extra"] = dict(captura.get("extra") or {}, **extra)

    # O motivo vai para a PROVENIENCIA, ao lado de quem trouxe e quando —
    # nunca para dentro do documento, que e imutavel.
    if motivo_sem_leitura:
        captura["extra"] = dict(captura.get("extra") or {},
                                sem_leitura=motivo_sem_leitura)

    indice.registrar(captura, leitura)
    # O detalhe da tentativa e o que a tela mostra na coluna de erro/observacao.
    # "duplicata" continua vencendo: e informacao sobre ESTA chegada.
    detalhe = ("duplicata" if guardado["desfecho"] == armazenamento.DUPLICATA
               else motivo_sem_leitura)
    _tentativa(indice, ident, e, t, modelo.OBTIDA, detalhe,
               origem, guardado["id"], usuario=usuario,
               metodo=metodo, sha256=guardado["sha256"])

    if auditar is not None:
        # A auditoria registra QUE entrou, nunca o conteúdo. Guardar o
        # documento dentro do registro de segurança seria uma segunda cópia
        # dele, num lugar que ninguém trata como cofre.
        try:
            auditar(identidade=ident, esfera=e, tipo=t,
                    documento_id=guardado["id"], origem=origem,
                    desfecho=guardado["desfecho"])
        except Exception:
            pass        # auditoria indisponível não desfaz o que foi gravado

    # A ORIGEM VOLTA NA RESPOSTA, já normalizada. Quem chamou pode ter mandado
    # "assistida-recife" e precisa saber sob que nome aquilo foi gravado — é
    # esse nome que aparecerá no índice e no dossiê, não o que foi digitado.
    return {"desfecho": guardado["desfecho"], "id": guardado["id"],
            "sha256": guardado["sha256"], "bytes": guardado["bytes"],
            "caminho": guardado["caminho"], "identidade": ident,
            "esfera": e, "tipo": t, "origem": origem}


def de_colheita(raiz, indice, identidade: str, esfera: str, tipo: str,
                colheita: Colheita, leitura: dict = None,
                auditar=None, usuario: str = "",
                metodo: str = modelo.METODO_INTEGRACAO) -> dict:
    """O caminho das FONTES: recebe uma `Colheita` e resolve o que fazer.

    Colheita sem documento **não é erro daqui** — é desfecho de negócio ou do
    órgão. Ela vira tentativa registrada e ponto: nada é gravado, nada é
    indexado, e o motivo fica guardado para quem for olhar depois.
    """
    ident = _identidade_normalizada(identidade)
    e, t = modelo.conferir(esfera, tipo)

    if not colheita.tem_documento:
        _tentativa(indice, ident, e, t, colheita.desfecho, colheita.detalhe,
                   colheita.origem, usuario=usuario, metodo=metodo)
        return {"desfecho": colheita.desfecho, "id": "", "guardado": False,
                "detalhe": colheita.detalhe, "identidade": ident,
                "esfera": e, "tipo": t}

    extra = dict(colheita.extra or {})
    r = importar(raiz, indice, ident, e, t, colheita.documento,
                 origem=colheita.origem or modelo.MANUAL,
                 leitura=leitura, extra=extra, auditar=auditar,
                 usuario=usuario, metodo=metodo)
    r["guardado"] = True
    r["detalhe"] = colheita.detalhe
    return r


def _tentativa(indice, identidade, esfera, tipo, desfecho, detalhe, origem,
               documento_id="", usuario="", metodo="", sha256=""):
    """Registra sem nunca derrubar quem chamou.

    Perder o documento porque o histórico falhou seria trocar o que importa
    pelo que ajuda. O contrário — gravar sem registrar — é aceitável e fica
    visível: o documento aparece no índice sem tentativa correspondente.
    """
    try:
        indice.registrar_tentativa(identidade, esfera, tipo, desfecho,
                                   detalhe or "", origem or "", documento_id,
                                   usuario=usuario, metodo=metodo,
                                   sha256=sha256)
    except Exception:
        pass
