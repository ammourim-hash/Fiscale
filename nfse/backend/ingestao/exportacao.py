"""
exportacao.py — o resultado da tela vira um pacote de XML originais.

A PERGUNTA QUE ESTE MÓDULO RESPONDE
    "Quero os XML de julho desta empresa, só as recebidas." Hoje isso é feito
    à mão, arquivo por arquivo, e é a operação que mais consome tempo do
    escritório. Aqui ela vira um ZIP.

A REGRA QUE GOVERNA TUDO: NÃO EXISTE SEGUNDA PESQUISA
    O lote **não** tem lógica de seleção própria. Ele recebe o mesmo `Filtro`
    que a tela usou e chama `consulta.consultar`. Se a tela diz 245, o pacote
    fala de 245 — não de 244 nem de 250.

    Isto não é elegância: é a única forma de o número do ZIP poder ser
    conferido contra o número da tela. Uma segunda implementação de "quais
    documentos" divergiria em algum canto (papel, cancelamento, resumo) e a
    divergência apareceria como arquivo faltando num lote entregue ao cliente.

O QUE VAI DENTRO É O ORIGINAL, E SÓ ELE
    Cada XML sai do acervo, byte a byte, pelo caminho canônico do documento.
    Nada é reconstruído a partir do SQLite, nada é reformatado, nada é
    reassinado. O índice é projeção; o que vale numa fiscalização é o arquivo
    que chegou assinado.

O QUE NÃO ENTRA NÃO SOME
    Documento que existe no resultado mas não pode ir para o ZIP — resumo sem
    XML completo, arquivo ausente, excluído por situação — aparece no
    `RELATORIO.csv` com o motivo. **A soma sempre fecha**:

        documentos do resultado = incluídos + não incluídos explicados

    Um lote que entrega 238 de 245 e não diz onde foram os 7 é pior do que um
    lote que falha: o usuário só descobre no mês seguinte.

O QUE ELE NUNCA FAZ
    Não vai à rede. Não completa resumo consultando a SEFAZ. Não move
    checkpoint. Não escreve no acervo. Não aceita caminho vindo do navegador.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import acervo as acv
from . import consulta as cq
from . import documento as dm

# ── política de canceladas ──────────────────────────────────────────────────
# O comportamento antigo descartava cancelada em silêncio (`somente_validas`
# com padrão `True`). O silêncio é o problema: quem exporta precisa saber que
# escolheu.
TODOS = "TODOS"
SOMENTE_VALIDAS = "SOMENTE_VALIDAS"
SOMENTE_CANCELADAS = "SOMENTE_CANCELADAS"
POLITICAS = (TODOS, SOMENTE_VALIDAS, SOMENTE_CANCELADAS)
POLITICA_PADRAO = TODOS

# Situações que tiram o documento de "válido hoje". Denegada entra junto:
# documento denegado nunca chegou a valer, e mandá-lo como se valesse é lançar
# o que não existe.
_INVALIDAS = (dm.CANCELADO, dm.DENEGADO)

# ── motivos de não inclusão ─────────────────────────────────────────────────
INCLUIDO = ""
XML_COMPLETO_INDISPONIVEL = "XML_COMPLETO_INDISPONIVEL"
ARQUIVO_AUSENTE_NO_ACERVO = "ARQUIVO_AUSENTE_NO_ACERVO"
EXCLUIDO_POR_SITUACAO = "EXCLUIDO_POR_SITUACAO"
JA_EXISTIA_IGUAL = "JA_EXISTIA_IGUAL"
ARQUIVO_DIFERENTE_JA_EXISTE = "ARQUIVO_DIFERENTE_JA_EXISTE"

_EXPLICACAO = {
    XML_COMPLETO_INDISPONIVEL:
        "o documento está no acervo apenas como RESUMO (`resNFe`). O XML "
        "completo nunca chegou. Nenhum XML foi inventado para preencher a "
        "lacuna, e a SEFAZ não foi consultada.",
    ARQUIVO_AUSENTE_NO_ACERVO:
        "o índice conhece o documento, mas o arquivo não está no acervo. É "
        "defeito de integridade e merece conferência.",
    EXCLUIDO_POR_SITUACAO:
        "fora pela política de canceladas escolhida na exportação.",
    JA_EXISTIA_IGUAL:
        "o arquivo já estava na pasta com o conteúdo idêntico (conferido por "
        "SHA-256). Não foi reescrito, e nada se perdeu — exportar de novo é "
        "seguro.",
    ARQUIVO_DIFERENTE_JA_EXISTE:
        "já existe na pasta um arquivo com este nome e conteúdo DIFERENTE. "
        "Ele NÃO foi sobrescrito. Confira antes: mesmo nome com conteúdo "
        "diferente é sinal de que um dos dois não é o que se pensa.",
}

# ── limites defensivos ──────────────────────────────────────────────────────
# A maior empresa do escritório tem 6.513 documentos; o acervo inteiro, 23.463.
# O teto existe para o caso de alguém pedir "tudo" sem perceber, não para
# barrar uso normal.
LIMITE_DOCUMENTOS = 25000
# De quantos em quantos a consulta é paginada ao montar o plano. Trazer 25 mil
# linhas numa tacada só é o tipo de coisa que funciona no teste e engasga na
# máquina do escritório.
LOTE_DA_CONSULTA = cq.TAMANHO_MAXIMO
# Pedaço usado ao copiar cada XML para dentro do ZIP. Nenhum arquivo é lido
# inteiro na memória.
PEDACO = 256 * 1024

PASTA_XML = "XML"
NOME_RELATORIO = "RELATORIO.csv"


class ErroExportacao(Exception):
    pass


class LimiteExcedido(ErroExportacao):
    pass


# ════════════════════════════════════════════════════════════════════════════
#  O plano — o que vai, o que não vai, e por quê
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Linha:
    """Um documento do resultado, já decidido."""
    id_documento: str
    especie: str
    chave: str
    numero: str
    serie: str
    emissao: str
    saida_entrada: str
    emitente: str
    emitente_nome: str
    contraparte: str
    contraparte_nome: str
    papel: str
    papeis: tuple[str, ...]
    situacao: str
    situacao_atual: str
    valor: str
    conteudo: str
    motivo: str = INCLUIDO

    @property
    def incluido(self) -> bool:
        return self.motivo == INCLUIDO

    @property
    def nome_no_zip(self) -> str:
        """`<chave>.xml`. Sem chave (não deveria acontecer numa nota), cai no
        `id_documento`, que é hexadecimal e igualmente inofensivo."""
        return f"{sanitizar(self.chave or self.id_documento)}.xml"


@dataclass
class Plano:
    identidade: str
    politica: str
    linhas: list[Linha] = field(default_factory=list)
    papel_filtrado: str = ""
    periodo: tuple[str, str] = ("", "")
    campo_data: str = cq.EMISSAO

    @property
    def total(self) -> int:
        return len(self.linhas)

    @property
    def incluidos(self) -> list[Linha]:
        return [x for x in self.linhas if x.incluido]

    def por_motivo(self) -> dict[str, int]:
        saida: dict[str, int] = {}
        for x in self.linhas:
            if not x.incluido:
                saida[x.motivo] = saida.get(x.motivo, 0) + 1
        return saida

    @property
    def composicao(self) -> dict[str, int]:
        """Quantos documentos de cada papel. É o que a tela mostra antes de
        exportar quando NENHUM papel foi filtrado — porque um ZIP que mistura
        frete de terceiro com compra, sem avisar, vira lançamento errado."""
        saida: dict[str, int] = {}
        for x in self.linhas:
            for p in (x.papeis or (x.papel,)):
                saida[p] = saida.get(p, 0) + 1
        return saida

    def reconciliacao(self) -> dict:
        """A soma que precisa fechar, escrita como dado.

        `confere` é a asserção: resultado da consulta = incluídos +
        explicados. Se ela for falsa, o pacote não deve ser entregue.
        """
        motivos = self.por_motivo()
        explicados = sum(motivos.values())
        return {
            "documentos_no_resultado": self.total,
            "xmls_no_zip": len(self.incluidos),
            "nao_incluidos": explicados,
            "por_motivo": motivos,
            "explicacoes": {k: _EXPLICACAO[k] for k in motivos},
            "confere": self.total == len(self.incluidos) + explicados,
        }

    def aviso_de_papel(self) -> str:
        """A frase que a tela mostra antes de exportar.

        Papel filtrado: o pacote é daquele papel e ponto. Sem filtro: o pacote
        mistura, e isso precisa estar escrito — não descoberto depois.
        """
        if self.papel_filtrado:
            return f"Somente documentos em que a empresa é {self.papel_filtrado}."
        partes = ", ".join(f"{p}: {n}" for p, n in sorted(
            self.composicao.items(), key=lambda kv: -kv[1]))
        return ("Todos os papéis selecionados — " + (partes or "nenhum documento")
                + ". TRANSPORTADOR e AUTXML não são compras da empresa.")

    def resumo(self) -> dict:
        r = self.reconciliacao()
        r.update({
            "identidade": self.identidade[:8] + "***",
            "politica_canceladas": self.politica,
            "papel": self.papel_filtrado or "TODOS",
            "aviso_de_papel": self.aviso_de_papel(),
            "composicao_por_papel": self.composicao,
            "periodo": {"de": self.periodo[0], "ate": self.periodo[1],
                        "campo": self.campo_data},
        })
        return r


# ════════════════════════════════════════════════════════════════════════════
#  Planejar — uma passada pela MESMA consulta da tela
# ════════════════════════════════════════════════════════════════════════════
def planejar(dados_dir, identidade, filtro: cq.Filtro | None = None, *,
             politica: str = POLITICA_PADRAO,
             limite: int = LIMITE_DOCUMENTOS) -> Plano:
    """Percorre o resultado e decide, documento a documento.

    Nenhuma decisão aqui abre arquivo: saber se o XML completo existe é
    pergunta ao índice (`conteudo`), não ao disco. O disco só é tocado na
    escrita do pacote, e o que ele contradisser vira
    `ARQUIVO_AUSENTE_NO_ACERVO` — com o documento ainda listado no CSV.
    """
    if politica not in POLITICAS:
        raise ErroExportacao(
            f"política de canceladas desconhecida: {politica!r}; "
            f"use uma de {list(POLITICAS)}")
    filtro = filtro or cq.Filtro()

    plano = Plano(identidade=str(identidade), politica=politica,
                  papel_filtrado=filtro.papel or "",
                  campo_data=filtro.campo_data,
                  periodo=(_iso(filtro.data_de), _iso(filtro.data_ate)))

    total = cq.contar(dados_dir, identidade, filtro)
    if total > limite:
        raise LimiteExcedido(
            f"o resultado tem {total} documentos e o teto por pacote é "
            f"{limite}. Estreite o período ou o filtro.")

    pagina = 1
    while True:
        pg = cq.consultar(dados_dir, identidade, filtro, pagina=pagina,
                          tamanho=LOTE_DA_CONSULTA, ordenar_por="emissao",
                          direcao="asc")
        for d in pg.itens:
            plano.linhas.append(_decidir(d, politica))
        if pagina >= pg.paginas or not pg.itens:
            break
        pagina += 1
    return plano


def _decidir(d: cq.Documento, politica: str) -> Linha:
    motivo = INCLUIDO
    # A ORDEM IMPORTA. A situação é decisão do usuário e vem primeiro: um
    # documento cancelado que ele mandou excluir sai por isso, não por
    # "resumo" — e o CSV precisa dizer a razão que ele reconhece.
    if politica == SOMENTE_VALIDAS and d.situacao_atual in _INVALIDAS:
        motivo = EXCLUIDO_POR_SITUACAO
    elif politica == SOMENTE_CANCELADAS and d.situacao_atual != dm.CANCELADO:
        motivo = EXCLUIDO_POR_SITUACAO
    elif d.conteudo != cq.COMPLETO:
        motivo = XML_COMPLETO_INDISPONIVEL

    return Linha(
        id_documento=d.id_documento, especie=d.especie, chave=d.chave,
        numero=d.numero, serie=d.serie,
        emissao=d.dh_emissao, saida_entrada=d.dh_saida_entrada,
        emitente=d.emitente, emitente_nome=d.emitente_nome,
        contraparte=d.contraparte, contraparte_nome=d.contraparte_nome,
        papel=d.papel, papeis=tuple(d.papeis),
        situacao=d.situacao, situacao_atual=d.situacao_atual,
        valor="" if d.valor_total is None else str(d.valor_total),
        conteudo=d.conteudo, motivo=motivo)


def _iso(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.isoformat()[:10]
    return str(d or "")


# ════════════════════════════════════════════════════════════════════════════
#  Nomes de arquivo — sanitizados, sempre
# ════════════════════════════════════════════════════════════════════════════
# Razão social do cliente vai para o nome do arquivo? Não. Ela traz acento,
# barra, aspas, `&`, ponto final e às vezes `..`. Nome de pacote é feito de
# CNPJ, período e papel — dados curtos, previsíveis e sem surpresa.
_PROIBIDO = re.compile(r"[^A-Za-z0-9._-]+")


def sanitizar(texto: str, *, tamanho: int = 60) -> str:
    """Só `[A-Za-z0-9._-]`, sem `..`, sem começar por ponto ou hífen.

    Não é decoração: o nome de arquivo é o que sai da nossa mão e entra no
    sistema de arquivos de quem baixa. `..` e separador de caminho não passam
    daqui.
    """
    limpo = _PROIBIDO.sub("_", str(texto or "")).strip("._-")
    while ".." in limpo:
        limpo = limpo.replace("..", "_")
    return limpo[:tamanho] or "SEM_NOME"


def nome_do_pacote(identidade: str, plano: Plano, *, prefixo: str = "NFE",
                   sufixo: str = "") -> str:
    """`NFE_12345678000190_2026-07_DESTINATARIO.zip`.

    Tudo o que entra aqui passa por `sanitizar`, inclusive o que veio do
    cadastro. O nome é amigável porque é composto de campos curtos e
    previsíveis, não porque alguém digitou um texto bonito.
    """
    partes = [sanitizar(prefixo), sanitizar(re.sub(r"\D", "", identidade))]
    de, ate = plano.periodo
    if de and ate and de[:7] == ate[:7]:
        partes.append(sanitizar(de[:7]))
    elif de or ate:
        partes.append(sanitizar(f"{de or 'inicio'}_a_{ate or 'hoje'}"))
    else:
        partes.append("TUDO")
    partes.append(sanitizar(plano.papel_filtrado or "TODOS_OS_PAPEIS"))
    if plano.politica != TODOS:
        partes.append(sanitizar(plano.politica))
    if sufixo:
        partes.append(sanitizar(sufixo))
    return "_".join(p for p in partes if p) + ".zip"


# ════════════════════════════════════════════════════════════════════════════
#  RELATORIO.csv — a reconciliação em forma de planilha
# ════════════════════════════════════════════════════════════════════════════
COLUNAS_CSV = (
    "chave", "numero", "serie", "emissao", "saida_entrada",
    "emitente_cnpj_cpf", "emitente_nome",
    "destinatario_cnpj_cpf", "destinatario_nome",
    "papel_da_empresa", "papeis_da_empresa",
    "situacao_original", "situacao_atual", "valor",
    # `conteudo` é o valor de máquina (COMPLETO/RESUMO); `disponibilidade_xml`
    # é a mesma verdade escrita para quem abre a planilha. As duas convivem
    # porque a primeira é filtrável e a segunda é legível.
    "conteudo", "disponibilidade_xml",
    # `exportado`/`arquivo` não dizem "zip": desde a NF-e 6B o mesmo relatório
    # acompanha a exportação para pasta, onde não há ZIP nenhum.
    "exportado", "motivo", "arquivo",
)

ROTULO_DISPONIBILIDADE = {
    cq.COMPLETO: "XML completo",
    cq.RESUMO: "Somente resumo",
}


def relatorio_csv(plano: Plano, *, pasta_xml: str = PASTA_XML) -> bytes:
    """UTF-8 **com BOM** e separador `;`.

    Não é preferência: é o que o Excel em português abre com as colunas
    separadas e os acentos certos ao dar duplo clique. Sem o BOM ele lê como
    Latin-1 e a razão social vem quebrada; com vírgula ele joga tudo numa
    coluna só.
    """
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                   lineterminator="\r\n")
    w.writerow(COLUNAS_CSV)
    for x in plano.linhas:
        dentro = (f"{pasta_xml}/{x.nome_no_zip}" if pasta_xml else x.nome_no_zip)
        w.writerow([
            x.chave, x.numero, x.serie, x.emissao, x.saida_entrada,
            x.emitente, x.emitente_nome, x.contraparte, x.contraparte_nome,
            x.papel, "|".join(x.papeis), x.situacao, x.situacao_atual,
            x.valor, x.conteudo,
            ROTULO_DISPONIBILIDADE.get(x.conteudo, x.conteudo or ""),
            "SIM" if x.incluido else "NAO",
            x.motivo,
            dentro if (x.incluido or x.motivo == JA_EXISTIA_IGUAL) else "",
        ])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


# ════════════════════════════════════════════════════════════════════════════
#  Escrever o ZIP — um arquivo por vez, direto no disco
# ════════════════════════════════════════════════════════════════════════════
def escrever_zip(destino, dados_dir, identidade, plano: Plano, *,
                 pasta_xml: str = PASTA_XML,
                 com_relatorio: bool = True) -> dict:
    """Monta o pacote em `destino` (caminho ou arquivo aberto em binário).

    MEMÓRIA
        Cada XML é copiado em pedaços de 256 KB de dentro do acervo para
        dentro do ZIP. Em nenhum momento o pacote inteiro — nem um arquivo
        inteiro — fica na memória. É o que permite exportar milhares de
        documentos na máquina do escritório sem o processo inchar.

    SEGURANÇA
        O caminho de cada XML nasce de `especie` + `id_documento`, ambos vindos
        do índice desta empresa, e é resolvido por `acervo.caminho_canonico`.
        Antes de ler, o caminho resolvido é conferido contra a raiz do acervo
        da empresa: qualquer coisa que caia fora vira
        `ARQUIVO_AUSENTE_NO_ACERVO` em vez de ser lida. Não há como um valor de
        URL escolher arquivo.
    """
    ac = acv.abrir(dados_dir, identidade)
    raiz = ac.raiz().resolve()
    escritos, ausentes = 0, []
    bytes_totais = 0

    # `plano.linhas` é reescrito porque um arquivo que sumiu entre planejar e
    # escrever muda o motivo — e o CSV tem de contar a verdade da hora em que o
    # pacote foi feito, não a da hora do plano.
    finais: list[Linha] = []

    fechar = False
    if hasattr(destino, "write"):
        saida = destino
    else:
        saida = open(destino, "wb")
        fechar = True
    try:
        with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            for linha in plano.linhas:
                if not linha.incluido:
                    finais.append(linha)
                    continue
                caminho = _caminho_seguro(ac, raiz, linha)
                if caminho is None:
                    ausentes.append(linha.chave or linha.id_documento)
                    finais.append(_com_motivo(linha, ARQUIVO_AUSENTE_NO_ACERVO))
                    continue
                nome = (f"{pasta_xml}/{linha.nome_no_zip}" if pasta_xml
                        else linha.nome_no_zip)
                tamanho = caminho.stat().st_size
                with open(caminho, "rb") as origem, z.open(nome, "w") as alvo:
                    shutil.copyfileobj(origem, alvo, PEDACO)
                bytes_totais += tamanho
                escritos += 1
                finais.append(linha)

            plano.linhas = finais
            if com_relatorio:
                z.writestr(NOME_RELATORIO,
                           relatorio_csv(plano, pasta_xml=pasta_xml))
    finally:
        if fechar:
            saida.close()

    r = plano.reconciliacao()
    r.update({"xmls_escritos": escritos, "bytes_de_xml": bytes_totais,
              "arquivos_ausentes": ausentes[:20],
              "com_relatorio": com_relatorio, "pasta_xml": pasta_xml})
    return r


def _sha(caminho) -> str:
    """SHA-256 lido em pedaços — o arquivo não precisa caber na memória."""
    import hashlib
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for pedaco in iter(lambda: f.read(PEDACO), b""):
            h.update(pedaco)
    return h.hexdigest()


def _com_motivo(linha: Linha, motivo: str) -> Linha:
    from dataclasses import replace
    return replace(linha, motivo=motivo)


def _caminho_seguro(ac, raiz: Path, linha: Linha) -> Path | None:
    """O arquivo do documento, ou `None`.

    A conferência contra a raiz é redundante hoje — `caminho_canonico` monta o
    caminho a partir de `especie`/`id_documento` e nunca sai da pasta. Ela
    existe porque essa garantia mora em outro arquivo, e uma exportação em
    lote é justamente onde um escape passaria despercebido: seriam milhares de
    arquivos, e ninguém confere um a um.
    """
    try:
        caminho = ac.caminho_canonico(linha.especie, linha.id_documento).resolve()
    except (OSError, ValueError):
        return None
    try:
        caminho.relative_to(raiz)
    except ValueError:
        return None
    return caminho if caminho.is_file() else None



# ════════════════════════════════════════════════════════════════════════════
#  Pasta de destino — onde é seguro escrever
# ════════════════════════════════════════════════════════════════════════════
# POR QUE ISTO EXISTE
#     "Salvar em pasta" é a única operação do módulo que recebe um caminho de
#     fora. Sem guarda, um caminho digitado (ou vindo do navegador) escreveria
#     dentro do acervo, na pasta de certificados ou em C:\Windows. Nenhuma
#     dessas escritas seria percebida na hora — só quando algo parasse de
#     funcionar, semanas depois.
#
# A REGRA
#     Recusar é o padrão. Só passa caminho absoluto, existente ou criável,
#     gravável, e que NÃO esteja dentro de nenhuma área do próprio Fiscale.

ORG_UNICA = "UNICA"
ORG_EMPRESA = "EMPRESA"
ORG_EMPRESA_ANO_MES_PAPEL = "EMPRESA_ANO_MES_PAPEL"

# `UNICA` é o padrão desde o ajuste de UX da NF-e 6B: **a pasta escolhida é o
# destino**. Quem seleciona `C:\Users\ana\Desktop\XML JULHO` espera os XML ali
# dentro — não em `XML JULHO\EMPRESA\2026\07\DESTINATARIO\`.
#
# A troca não foi de gosto. O usuário já tinha separado a pasta por período na
# mão; o sistema separava de novo, por outro critério, e o lote que era para
# ser importado de uma vez virava uma caça a quatro níveis.
#
# Os filtros da tela dizem QUAIS documentos saem. Eles não dizem mais ONDE os
# documentos ficam — eram duas perguntas coladas numa.
ORGANIZACAO_PADRAO = ORG_UNICA

ROTULO_ORGANIZACAO = {
    ORG_UNICA: "Direto na pasta escolhida (padrão)",
    ORG_EMPRESA: "Uma subpasta por empresa",
    ORG_EMPRESA_ANO_MES_PAPEL: "Empresa / Ano / Mês / Papel",
}

# A ordem em que a tela oferece: o padrão primeiro.
ORGANIZACOES = (ORG_UNICA, ORG_EMPRESA, ORG_EMPRESA_ANO_MES_PAPEL)


class PastaInsegura(ErroExportacao):
    """A pasta pedida não pode receber a exportação."""


# Nomes de diretório de sistema em que nunca se escreve. Comparados sobre o
# caminho resolvido, em minúsculas, por segmento — não por "contém", que
# recusaria uma pasta legítima chamada "C:\Trabalho\windows-xmls".
_SEGMENTOS_PROIBIDOS = {
    "windows", "system32", "syswow64", "program files", "program files (x86)",
    "programdata", "$recycle.bin", "system volume information",
}


def _resolver(p) -> Path:
    return Path(p).expanduser().resolve()


def _dentro_de(alvo: Path, raiz: Path) -> bool:
    try:
        alvo.relative_to(raiz)
        return True
    except ValueError:
        return False


def validar_pasta_destino(pasta, dados_dir, identidade=None) -> Path:
    """Devolve o caminho aprovado, ou levanta `PastaInsegura` com o motivo.

    O motivo é escrito para o usuário, não para o log: quem escolheu a pasta
    precisa entender por que ela foi recusada, senão tenta de novo igual.
    """
    bruto = str(pasta or "").strip()
    if not bruto:
        raise PastaInsegura("Informe a pasta de destino.")

    # A checagem de "é absoluto?" vem ANTES de resolver, e é por isso que
    # ela existe: `resolve()` completa um caminho relativo contra o
    # diretório de trabalho do servidor — a pasta de instalação do Fiscale.
    # Testar depois sempre passaria, e "exportacoes" acabaria dentro do
    # próprio programa, calado.
    try:
        pedido = Path(bruto).expanduser()
    except (OSError, ValueError, RuntimeError) as e:
        raise PastaInsegura(f"Caminho inválido: {e}") from None
    if not pedido.is_absolute():
        raise PastaInsegura(
            "Use um caminho completo, começando pela unidade "
            "(ex.: C:\\FISCALE\\EXPORTACOES\\XML).")

    # Path traversal nunca chega ao disco: o caminho é resolvido antes de
    # qualquer teste, e é o resolvido que responde por tudo daqui em diante.
    try:
        alvo = _resolver(pedido)
    except (OSError, ValueError, RuntimeError) as e:
        raise PastaInsegura(f"Caminho inválido: {e}") from None

    partes = [x.lower() for x in alvo.parts]
    for seg in partes:
        if seg.strip("\\/").lower() in _SEGMENTOS_PROIBIDOS:
            raise PastaInsegura(
                f"'{seg}' é uma pasta do sistema. Escolha uma pasta de "
                "trabalho, como C:\\FISCALE\\EXPORTACOES\\XML.")

    # A raiz de dados do Fiscale inteira está fora: acervo, índice, certs,
    # sessões, backups. Exportar para dentro de si mesmo é como o sistema
    # perde dado sem ninguém notar.
    try:
        raiz_dados = _resolver(dados_dir)
    except (OSError, ValueError):
        raiz_dados = None
    if raiz_dados is not None and (_dentro_de(alvo, raiz_dados) or alvo == raiz_dados):
        raise PastaInsegura(
            "Esta pasta está dentro da área de dados do Fiscale. A exportação "
            "nunca escreve no acervo — escolha uma pasta fora dele.")

    if identidade:
        try:
            ac = acv.abrir(dados_dir, identidade)
            raiz_acervo = ac.raiz().resolve()
            if _dentro_de(alvo, raiz_acervo) or alvo == raiz_acervo:
                raise PastaInsegura(
                    "Esta é a pasta do acervo da empresa. O acervo é imutável "
                    "e nunca recebe exportação.")
        except PastaInsegura:
            raise
        except Exception:
            pass  # não conseguir abrir o acervo não é motivo para recusar

    # Existe? tem de ser pasta.
    if alvo.exists():
        if not alvo.is_dir():
            raise PastaInsegura("Esse caminho é um arquivo, não uma pasta.")
        return alvo

    # Não existe: a árvore será criada. Isso é o caso NORMAL da primeira
    # exportação — exigir que o pai já exista impediria o próprio destino
    # padrão de nascer.
    #
    # O que precisa existir é a ÂNCORA: a unidade. É ela que distingue "pasta
    # nova em disco bom" de "unidade de rede caída" e de "letra de drive que
    # não existe" — e este último é o caso que se manifesta como exportação
    # que some sem erro.
    ancora = Path(alvo.anchor) if alvo.anchor else None
    if ancora is None or not str(ancora).strip():
        raise PastaInsegura("Não consegui identificar a unidade do caminho.")
    if not ancora.exists():
        raise PastaInsegura(
            f"A unidade {ancora} não está acessível. Se for pasta de rede ou "
            "disco externo, conecte antes de exportar.")

    # O ancestral mais próximo que já existe é quem responde pela permissão.
    existente = alvo.parent
    while not existente.exists() and existente != existente.parent:
        existente = existente.parent
    if not existente.is_dir():
        raise PastaInsegura(f"{existente} não é uma pasta.")

    return alvo


def conferir_escrita(pasta: Path) -> None:
    """Prova que dá para escrever, escrevendo. `os.access` mente em rede e em
    pasta com ACL — a única resposta confiável é tentar."""
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PastaInsegura(
            f"Não foi possível criar a pasta: {e.strerror or e}") from None
    teste = pasta / f".fiscale-escrita-{os.getpid()}.tmp"
    try:
        teste.write_bytes(b"ok")
    except OSError as e:
        raise PastaInsegura(
            "Sem permissão de escrita nesta pasta"
            + (f" ({e.strerror})" if e.strerror else "")
            + ". Se for pasta de rede, confira se a unidade está conectada."
        ) from None
    finally:
        try:
            teste.unlink()
        except OSError:
            pass


def subpasta_de(linha: Linha, organizacao: str, *, empresa: str = "") -> Path:
    """Onde o XML fica dentro da pasta de destino.

    `EMPRESA` (o padrão) devolve UM nível: `<empresa>/`, com os XML soltos
    dentro. `UNICA` devolve a raiz, para quem já separou a pasta por empresa
    na mão. A árvore por ano/mês/papel existe para quem arquiva.
    """
    if organizacao == ORG_UNICA:
        return Path()
    if organizacao == ORG_EMPRESA:
        return Path(sanitizar(empresa or "EMPRESA", tamanho=80) or "EMPRESA")
    emissao = (linha.emissao or "")[:10]
    ano = emissao[:4] or "SEM_DATA"
    mes = emissao[5:7] or "00"
    papel = sanitizar(linha.papel or "SEM_PAPEL", tamanho=30) or "SEM_PAPEL"
    emp = sanitizar(empresa or "EMPRESA", tamanho=40) or "EMPRESA"
    return Path(emp) / ano / mes / papel

# ════════════════════════════════════════════════════════════════════════════
#  O ZIP plano — os XML soltos, sem mais nada
# ════════════════════════════════════════════════════════════════════════════
# O QUE SE SABE E O QUE NÃO SE SABE
#     Não há, neste projeto, evidência de como cada importador de contabilidade
#     consome os arquivos — se aceita ZIP ou se exige uma pasta com os XML
#     soltos. Procurado e não encontrado (NF-e 6, §9). Presumir seria entregar
#     um pacote que o escritório só descobriria não servir na hora de usar.
#
#     O que EXISTE de evidência é do lado do FISCALE: a exportação anterior
#     escrevia XML soltos numa pasta e a abria no Explorer. É o fluxo de hoje.
#
# A DECISÃO
#     Os dois caminhos ficam disponíveis, e nenhum é chamado de "o certo":
#       • ZIP com os XML na RAIZ (sem subpasta, sem CSV) — descompactar produz
#         exatamente uma pasta de XML soltos, e serve para levar embora;
#       • escrita direta numa pasta do servidor, que é o fluxo de sempre.
#     O CSV fica FORA do ZIP plano, entregue à parte, porque um arquivo
#     estranho no meio dos XML é o tipo de coisa que faz importador recusar o
#     lote inteiro.
def pacote_xmls_planos(destino, dados_dir, identidade, plano: Plano) -> dict:
    """ZIP plano: os XML na raiz, sem mais nada dentro.

    É o formato que todo importador de contabilidade aceita — nenhum deles é
    nomeado aqui de propósito. O Fiscale entrega arquivo padrão; a quem o
    arquivo serve é escolha de quem exporta, não do sistema.

    O relatório sai à parte, por `/api/nfe/exportar/relatorio`: importador que
    encontra arquivo estranho no lote costuma recusar o lote inteiro, e essa
    é uma falha cara de diagnosticar.
    """
    r = escrever_zip(destino, dados_dir, identidade, plano,
                     pasta_xml="", com_relatorio=False)
    r["formato"] = "ZIP_PLANO"
    r["observacao"] = (
        "XML originais na raiz do ZIP, sem nenhum outro arquivo. O "
        "RELATORIO.csv é entregue à parte.")
    return r


def escrever_em_pasta(pasta, dados_dir, identidade, plano: Plano, *,
                      organizacao: str = ORGANIZACAO_PADRAO,
                      com_relatorio: bool = False,
                      empresa: str = "",
                      validar: bool = True) -> dict:
    """Os XML soltos numa pasta do computador onde o Fiscale roda.

    IMPORTANTE — ISTO NÃO É DOWNLOAD
        O destino é o disco do SERVIDOR. Quando o Fiscale for acessado do
        celular ou de outra máquina, "salvar em pasta" continua gravando aqui,
        não no aparelho de quem clicou. Para levar os arquivos consigo, o
        caminho é o ZIP.

    NADA É APAGADO, E NADA É SOBRESCRITO ÀS CEGAS
        A pasta é criada se faltar; arquivos que já existem lá e não fazem
        parte deste lote não são tocados.

        Quando o nome JÁ EXISTE, o conteúdo decide, por SHA-256:
          • idêntico  → não reescreve, conta como `JA_EXISTIA_IGUAL`. É o caso
            normal de reexportar o mesmo período, e é barato dizer isso.
          • diferente → **não sobrescreve**, e sai no relatório como
            `ARQUIVO_DIFERENTE_JA_EXISTE`. O nome é a chave de acesso, então
            dois conteúdos diferentes sob a mesma chave significam que um dos
            dois não é o que se pensa — e descobrir isso depois de o original
            ter sido apagado é tarde demais.
    """
    if organizacao not in ORGANIZACOES:
        raise ErroExportacao(
            f"organização desconhecida: {organizacao!r}; "
            f"use uma de {list(ORGANIZACOES)}")

    alvo = (validar_pasta_destino(pasta, dados_dir, identidade) if validar
            else Path(pasta).expanduser().resolve())
    conferir_escrita(alvo)

    ac = acv.abrir(dados_dir, identidade)
    raiz = ac.raiz().resolve()
    escritos, ja_iguais, conflitos = 0, 0, 0
    finais, pastas = [], set()
    for linha in plano.linhas:
        if not linha.incluido:
            finais.append(linha)
            continue
        caminho = _caminho_seguro(ac, raiz, linha)
        if caminho is None:
            finais.append(_com_motivo(linha, ARQUIVO_AUSENTE_NO_ACERVO))
            continue
        sub = subpasta_de(linha, organizacao, empresa=empresa)
        destino_dir = alvo / sub if str(sub) != "." else alvo
        # Cinto e suspensório: `subpasta_de` já sanitiza cada segmento, mas o
        # caminho montado é conferido contra a raiz antes de qualquer escrita.
        destino_dir = destino_dir.resolve()
        if not (_dentro_de(destino_dir, alvo) or destino_dir == alvo):
            finais.append(_com_motivo(linha, ARQUIVO_AUSENTE_NO_ACERVO))
            continue
        if destino_dir not in pastas:
            destino_dir.mkdir(parents=True, exist_ok=True)
            pastas.add(destino_dir)
        destino_arq = destino_dir / linha.nome_no_zip
        if destino_arq.exists():
            # `_sha` lê os dois em pedaços: um lote de 6 mil XML não cabe na
            # memória de uma vez, e não precisa caber.
            if _sha(destino_arq) == _sha(caminho):
                ja_iguais += 1
                finais.append(_com_motivo(linha, JA_EXISTIA_IGUAL))
            else:
                conflitos += 1
                finais.append(_com_motivo(linha, ARQUIVO_DIFERENTE_JA_EXISTE))
            continue
        try:
            shutil.copyfile(caminho, destino_arq)
        except OSError as e:
            raise PastaInsegura(
                f"Falha ao gravar em {destino_dir}: {e.strerror or e}. "
                "Se for pasta de rede, confira a conexão; se for disco, o "
                "espaço livre."
            ) from None
        escritos += 1
        finais.append(linha)
    plano.linhas = finais

    caminho_relatorio = ""
    if com_relatorio:
        nome_rel = NOME_RELATORIO
        if organizacao != ORG_UNICA:
            marca = sanitizar(empresa or identidade, tamanho=60)
            if marca:
                nome_rel = f"{Path(NOME_RELATORIO).stem}-{marca}.csv"
        rel = alvo / nome_rel
        rel.write_bytes(relatorio_csv(plano, pasta_xml=""))
        caminho_relatorio = str(rel)

    r = plano.reconciliacao()
    r.update({
        "pasta": str(alvo),
        "xmls_escritos": escritos,
        "ja_existiam_iguais": ja_iguais,
        "conflitos": conflitos,
        "organizacao": organizacao,
        "subpastas": len(pastas),
        "relatorio": caminho_relatorio,
        "destino": "SERVIDOR",
    })
    return r
