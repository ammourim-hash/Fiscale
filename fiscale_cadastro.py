#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fiscale — o cadastro de empresas: importação e ordenação (Cadastro 1).

    import fiscale_cadastro as cad
    linhas, avisos = cad.ler_planilha(bytes, "empresas.xlsx")
    previa = cad.conciliar(atuais, linhas)      # nada é gravado aqui
    novos  = cad.aplicar(atuais, previa)        # só depois de alguém confirmar

DUAS COISAS, E POR QUE MORAM JUNTAS
    Importar planilha e ordenar empresa parecem assuntos diferentes. São o
    mesmo: os dois dependem de responder "que empresa é esta?" e "como se
    chama?", e essas duas perguntas tinham resposta espalhada — cada tela
    ordenava do seu jeito, e o CNPJ era comparado ora com pontuação, ora sem.

A CHAVE É O DOCUMENTO, E SÓ ELE
    Conciliar por nome é conciliar por digitação: "LTDA" e "LTDA." viram duas
    empresas, e um acento fora do lugar vira uma terceira. O documento tem
    dígito verificador — dá para saber se está certo antes de gravar.

    DOCUMENTO, e não "CNPJ": um dos 23 cadastros desta instalação é pessoa
    física, com CPF. O ensaio sobre o cadastro real mostrou isso, e a versão
    anterior desta função o teria corrompido. As telas continuam dizendo
    "CNPJ" porque é como o escritório fala; o código sabe a diferença.

    E o documento NÃO substitui o identificador interno de ninguém. O `id` que
    cada cadastro já tem continua sendo dele: quem aponta para um cliente hoje
    continua apontando amanhã.

NADA É SOBRESCRITO EM SILÊNCIO
    Quando a planilha traz um valor diferente do que já está no cadastro, isso
    não é uma atualização: é um CONFLITO, e ele aparece na prévia sem ser
    aplicado. Preencher campo vazio é atualização; trocar o que alguém já
    escreveu é decisão de gente.

A PLANILHA É DADO, NUNCA PROGRAMA
    `.xlsx` é um ZIP de XMLs. Este módulo lê os XMLs das células e mais nada:
    não avalia fórmula, não abre vínculo externo, e recusa arquivo com macro.
    Fórmula sem valor calculado vira erro visível — importar o vazio que ela
    deixou seria pior que recusar.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

# ── limites da planilha ─────────────────────────────────────────────────────
# Existem para que um arquivo enorme (ou uma bomba de descompressão) não vire
# memória do servidor. Os números são folgados para um escritório: 5 MB e
# 5.000 linhas cobrem um cadastro inteiro com sobra.
TAMANHO_MAX = 5 * 1024 * 1024
LINHAS_MAX = 5000
CELULAS_MAX = 200_000
EXTENSOES = (".xlsx", ".csv")

# BOMBA DE DESCOMPRESSÃO
#     Um .xlsx de 200 KB pode declarar um XML de vários gigabytes: o ZIP
#     comprime bem demais quando o conteúdo é repetitivo, e descompactar sem
#     olhar é entregar a memória da máquina a quem mandou o arquivo.
#
#     Duas travas, porque uma só não basta: um TETO absoluto por membro, e uma
#     RAZÃO de expansão. O teto pega o arquivo declaradamente enorme; a razão
#     pega o pequeno que só cresce ao abrir.
MEMBRO_MAX = 40 * 1024 * 1024
RAZAO_MAX = 120

# ── os campos do cadastro ───────────────────────────────────────────────────
CAMPOS = ("cnpj", "razao_social", "nome_fantasia", "cnae_principal",
          "cnaes_secundarios", "ie", "im", "municipio", "uf", "ibge",
          "regime", "situacao", "email", "contato")

# Obrigatórios para um cadastro NOVO. O CNAE só vale para pessoa jurídica:
# pessoa física não tem CNAE, e exigi-lo seria impedir o cadastro de uma
# cliente que já existe no escritório.
OBRIGATORIOS_NOVO = ("cnpj", "razao_social", "cnae_principal")
OBRIGATORIOS_NOVO_PF = ("cnpj", "razao_social")


def obrigatorios_de(documento) -> tuple:
    return (OBRIGATORIOS_NOVO_PF if eh_pessoa_fisica(documento)
            else OBRIGATORIOS_NOVO)

ROTULOS = {
    "cnpj": "CPF/CNPJ", "razao_social": "Razão Social",
    "nome_fantasia": "Nome Fantasia", "cnae_principal": "CNAE Principal",
    "cnaes_secundarios": "CNAEs Secundários", "ie": "Inscrição Estadual",
    "im": "Inscrição Municipal", "municipio": "Município", "uf": "UF",
    "ibge": "Código IBGE", "regime": "Regime Tributário",
    # "Situação" sozinho colidia com a SITUAÇÃO FISCAL (regularidade perante a
    # Receita), que é outro assunto e tem módulo próprio (`situacao/`). Este
    # campo é a situação CADASTRAL do CNPJ — ativa, baixada, suspensa. O
    # sinônimo "situacao cadastral" já existia em `_SINONIMOS`, então a planilha
    # continua indo e voltando; o que muda é o rótulo deixar de ser ambíguo.
    "situacao": "Situação Cadastral",
    "email": "E-mail", "contato": "Contato",
}


# ════════════════════════════════════════════════════════════════════════════
#  QUEM MANDA EM CADA DADO — a declaração que faltava
# ════════════════════════════════════════════════════════════════════════════
#  O FISCALE tinha o princípio ("o cadastro é a fonte") aplicado em alguns
#  lugares e não declarado em nenhum: `regime_da_empresa` já dá a palavra final
#  ao Clientes, `ingestao/cadastro.py` unifica os dois JSON sem eleger um, e a
#  validade do certificado é COPIADA para o cadastro pela tela.
#
#  O mapa abaixo é a declaração. Ele não move dado nenhum: diz, campo por
#  campo, quem é o dono e onde o valor vive. Serve para três coisas concretas:
#
#    1. a tela poder mostrar "isto vem de aqui" em vez de um campo mudo;
#    2. um campo derivado nunca ser editado no cadastro como se fosse dele;
#    3. divergência entre a cópia e a origem aparecer, em vez de ser resolvida
#       em silêncio por quem lê por último.
#
#  MESTRE      o cadastro é o dono; ninguém sobrescreve
#  DERIVADO    o valor nasce em outra fonte; o cadastro no máximo guarda cópia
#  SUGERIDO    outra fonte propõe, o cadastro decide (e pode discordar)
MESTRE = "MESTRE"
DERIVADO = "DERIVADO"
SUGERIDO = "SUGERIDO"

FONTE_DO_CAMPO = {
    # Identidade e dados de contato: do cadastro, e de mais ninguém.
    "cnpj": (MESTRE, "cadastro (Clientes)"),
    "razao_social": (MESTRE, "cadastro (Clientes)"),
    "nome_fantasia": (MESTRE, "cadastro (Clientes)"),
    "email": (MESTRE, "cadastro (Clientes)"),
    "contato": (MESTRE, "cadastro (Clientes)"),
    # CNAE, município e IBGE chegam da base pública e o cadastro fica com eles.
    "cnae_principal": (SUGERIDO, "base pública do CNPJ → cadastro"),
    "cnaes_secundarios": (SUGERIDO, "base pública do CNPJ → cadastro"),
    "municipio": (SUGERIDO, "base pública do CNPJ → cadastro"),
    "uf": (SUGERIDO, "base pública do CNPJ → cadastro"),
    "ibge": (SUGERIDO, "base pública do CNPJ → cadastro"),
    "situacao": (SUGERIDO, "base pública do CNPJ (situação CADASTRAL)"),
    # Regime: o Clientes tem a palavra final (`regime_da_empresa`), e a base
    # pública é sugestão — ela não separa Presumido de Real, então "não
    # optante" nunca pode virar um regime por conta própria.
    "regime": (MESTRE, "cadastro (Clientes); base pública apenas sugere"),
    # IE e IM nascem nos XML: a base pública não as traz. O cadastro guarda o
    # que foi confirmado, e o filtro de NF-e por IE lê daqui.
    "ie": (DERIVADO, "XML de NF-e (`inscricoes`) → cadastro"),
    "im": (DERIVADO, "XML de NFS-e (`inscricoes`) → cadastro"),
}

# Campos que NÃO são do cadastro, e por isso não estão em `CAMPOS`. Ficam
# declarados para que ninguém os acrescente ali por engano: o dia em que a
# validade do certificado virar campo de planilha, ela passa a ter duas
# verdades.
FORA_DO_CADASTRO = {
    "certificado": (DERIVADO, "`certificados.json` — arquivo .pfx e validade "
                              "lidos do próprio certificado"),
    "regularidade": (DERIVADO, "pacote `situacao/` — certidões e extratos; "
                               "NÃO é o campo `situacao`"),
    "acervo": (DERIVADO, "índice da ingestão — documentos capturados"),
    "apuracao": (DERIVADO, "`state_classificador.json` — configuração de "
                           "apuração da empresa"),
}

# Sinônimos de cabeçalho. A planilha vem de onde vier — do contador, do
# sistema antigo, do próprio modelo — e exigir a grafia exata seria transformar
# um trabalho de cinco minutos numa tarde de tentativa e erro.
_SINONIMOS = {
    "cnpj": "cnpj", "c n p j": "cnpj", "cpf": "cnpj",
    "cpf cnpj": "cnpj", "documento": "cnpj", "doc": "cnpj",
    "cnpj cpf": "cnpj",
    "razao social": "razao_social", "razaosocial": "razao_social",
    "razao": "razao_social", "empresa": "razao_social", "nome": "razao_social",
    "nome empresarial": "razao_social",
    "nome fantasia": "nome_fantasia", "fantasia": "nome_fantasia",
    "apelido": "nome_fantasia",
    "cnae principal": "cnae_principal", "cnae": "cnae_principal",
    "cnae fiscal": "cnae_principal", "atividade principal": "cnae_principal",
    "cnaes secundarios": "cnaes_secundarios",
    "cnae secundario": "cnaes_secundarios",
    "cnaes secundarias": "cnaes_secundarios",
    "atividades secundarias": "cnaes_secundarios",
    "inscricao estadual": "ie", "ie": "ie", "insc estadual": "ie",
    "inscricao municipal": "im", "im": "im", "insc municipal": "im",
    "municipio": "municipio", "cidade": "municipio",
    "uf": "uf", "estado": "uf",
    "codigo ibge": "ibge", "ibge": "ibge", "cod ibge": "ibge",
    "codigo do municipio": "ibge",
    "regime tributario": "regime", "regime": "regime", "tributacao": "regime",
    "situacao": "situacao", "situacao cadastral": "situacao",
    "status": "situacao",
    "email": "email", "e mail": "email", "correio": "email",
    "contato": "contato", "telefone": "contato", "fone": "contato",
    "celular": "contato",
}


def sem_acento(texto) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto or ""))
                   if not unicodedata.combining(c))


def _cabecalho(bruto) -> str:
    """O nome de coluna, reduzido ao que importa para reconhecê-lo."""
    t = sem_acento(bruto).lower().strip()
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return re.sub(r"\s+", " ", t)


def campo_do_cabecalho(bruto) -> str:
    return _SINONIMOS.get(_cabecalho(bruto), "")


# ── CNPJ ────────────────────────────────────────────────────────────────────
def so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _cnpj_dv_ok(c: str) -> bool:
    if len(c) != 14 or len(set(c)) == 1:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(c[i]) * pesos[i] for i in range(tamanho))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if int(c[tamanho]) != digito:
            return False
    return True


def _cpf_dv_ok(c: str) -> bool:
    if len(c) != 11 or len(set(c)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(c[i]) * ((tamanho + 1) - i) for i in range(tamanho))
        digito = (soma * 10) % 11 % 10
        if int(c[tamanho]) != digito:
            return False
    return True


def normalizar_documento(v) -> str:
    """CNPJ de 14 dígitos ou CPF de 11. Sem pontuação.

    NEM TODO CLIENTE É EMPRESA
        Dos 23 cadastros desta instalação, um é PESSOA FÍSICA — CPF válido de
        11 dígitos. Tratar tudo como CNPJ não o rejeitaria apenas: o
        preenchimento de zeros à esquerda transformaria `12345678909` em
        `00012345678909`, um número que não é de ninguém. Foi o que a primeira
        versão desta função fez, e o ensaio sobre o cadastro real pegou.

    O ZERO À ESQUERDA SOME EM PLANILHA
        Guardado como número, `05678005000191` vira `5678005000191`. Por isso
        um documento curto é completado — mas só até o tamanho em que o dígito
        verificador FECHA. Completar às cegas inventaria documento.
    """
    d = so_digitos(v)
    if not d:
        return ""
    if len(d) == 14:
        return d
    if len(d) == 11:
        # Onze dígitos é CPF quase sempre; só é CNPJ se o DV disser que sim.
        if _cpf_dv_ok(d):
            return d
        completo = d.zfill(14)
        return completo if _cnpj_dv_ok(completo) else d
    if 12 <= len(d) <= 13:
        return d.zfill(14)
    if 9 <= len(d) <= 10:
        curto = d.zfill(11)
        return curto if _cpf_dv_ok(curto) else d.zfill(14)
    return d[:14] if len(d) > 14 else d


def documento_valido(v) -> bool:
    """O dígito verificador fecha — como CNPJ ou como CPF."""
    d = normalizar_documento(v)
    return _cnpj_dv_ok(d) if len(d) == 14 else _cpf_dv_ok(d)


def eh_pessoa_fisica(v) -> bool:
    return len(normalizar_documento(v)) == 11


CPF, CNPJ = "CPF", "CNPJ"


def tipo_documento(v) -> str:
    """`"CPF"`, `"CNPJ"` ou `""`. É o que a tela mostra e a regra consulta.

    Adivinhar o tipo pelo tamanho em cada lugar que precisa dele foi como o
    CPF acabou tratado como CNPJ curto. Perguntar aqui é uma linha; espalhar
    `len(x) == 14` pelo sistema é um defeito por lugar.
    """
    d = normalizar_documento(v)
    if len(d) == 11:
        return CPF
    if len(d) == 14:
        return CNPJ
    return ""


def documento_de(registro) -> str:
    """O documento de um cadastro, venha ele do campo que vier.

    O cadastro tem história: `cnpj` no formato novo, `id` nos certificados,
    `documento` no que vier depois. Quem precisa do documento pergunta aqui —
    e não fica sabendo em qual campo ele estava guardado.
    """
    if not isinstance(registro, dict):
        return normalizar_documento(registro)
    for nome in ("documento", "cnpj", "cpf", "cpf_cnpj", "id"):
        v = registro.get(nome)
        if v not in (None, ""):
            d = normalizar_documento(v)
            if d:
                return d
    return ""


def com_tipo(registro) -> dict:
    """O cadastro acrescido de `documento` e `tipo_documento`.

    Não substitui campo nenhum: acrescenta. Quem lê `cnpj` continua lendo
    `cnpj`; quem quer saber se é gente ou empresa pergunta a `tipo_documento`.
    """
    if not isinstance(registro, dict):
        return registro
    d = documento_de(registro)
    return {**registro, "documento": d, "tipo_documento": tipo_documento(d),
            "documento_formatado": formatar_cnpj(d) if d else ""}


# Nomes antigos, mantidos porque "CNPJ" é como o resto do sistema chama a
# coluna. Eles agora aceitam CPF também — o cadastro tem um.
def normalizar_cnpj(v) -> str:
    return normalizar_documento(v)


def cnpj_valido(v) -> bool:
    return documento_valido(v)


def formatar_cnpj(v) -> str:
    c = normalizar_documento(v)
    if len(c) == 14:
        return "%s.%s.%s/%s-%s" % (c[:2], c[2:5], c[5:8], c[8:12], c[12:])
    if len(c) == 11:
        return "%s.%s.%s-%s" % (c[:3], c[3:6], c[6:9], c[9:])
    return str(v or "")


# ── CNAE ────────────────────────────────────────────────────────────────────
def normalizar_cnae(v) -> str:
    """Sete dígitos, sem pontuação. Devolve "" quando não dá.

    A CNAE tem sete dígitos (`6920601`), e aparece escrita de todo jeito:
    `69.20-6-01`, `6920-6/01`, `6920601`. O que muda é a pontuação; o código é
    o mesmo, e comparar com pontuação faz a mesma atividade parecer duas.
    """
    d = so_digitos(v)
    if not d:
        return ""
    if len(d) < 7:
        return ""
    return d[:7]


def normalizar_cnaes(v, principal="") -> list:
    """A lista de secundárias, sem repetição e sem repetir a principal.

    Separador: ponto e vírgula. Vírgula NÃO serve — em planilha brasileira a
    vírgula é decimal, e um CSV separado por vírgula partiria a própria célula.
    """
    if isinstance(v, (list, tuple)):
        pedacos = list(v)
    else:
        pedacos = re.split(r"[;\n]", str(v or ""))
    saida, vistos = [], set()
    if principal:
        vistos.add(normalizar_cnae(principal))
    for p in pedacos:
        c = normalizar_cnae(p)
        if c and c not in vistos:
            vistos.add(c)
            saida.append(c)
    return saida


# ── ordenação central ───────────────────────────────────────────────────────
def chave_ordenacao(empresa) -> tuple:
    """Razão Social, depois Nome Fantasia, e o CNPJ como desempate.

    Sem acento e sem caixa: `Água` e `AGUA` são a mesma palavra para quem
    procura numa lista, e ordenar por código de caractere jogaria todos os
    acentuados para o fim. O CNPJ entra por último porque duas empresas podem
    ter o mesmo nome, e a lista não pode mudar de ordem a cada carregamento.
    """
    if not isinstance(empresa, dict):
        empresa = {}
    def texto(*nomes):
        for n in nomes:
            v = empresa.get(n)
            if v:
                return sem_acento(v).casefold().strip()
        return ""
    return (texto("razao_social", "nome", "razaoSocial"),
            texto("nome_fantasia", "apelido", "fantasia"),
            documento_de(empresa))


def ordenar(empresas) -> list:
    """A ordem que TODA tela e TODO seletor usa. Uma só, no servidor."""
    return sorted(list(empresas or []), key=chave_ordenacao)


# ── leitura da planilha ─────────────────────────────────────────────────────
class PlanilhaInvalida(Exception):
    """O arquivo não serve, e o motivo está na mensagem."""


_COLUNA = re.compile(r"([A-Z]+)")


def _indice_da_coluna(ref: str) -> int:
    letras = _COLUNA.match((ref or "").upper())
    if not letras:
        return 0
    n = 0
    for c in letras.group(1):
        n = n * 26 + (ord(c) - 64)
    return n - 1


def _sem_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ler_membro(z, nome: str) -> bytes:
    """Um membro do ZIP, com teto de tamanho e de expansão. Nunca vai a disco.

    O tamanho é conferido no ÍNDICE antes de descompactar. Conferir depois
    seria conferir com a memória já ocupada — que é exatamente o que a bomba
    de descompressão quer.
    """
    info = z.getinfo(nome)
    if info.file_size > MEMBRO_MAX:
        raise PlanilhaInvalida(
            "A planilha tem uma parte interna grande demais (%.0f MB)."
            % (info.file_size / 1e6))
    if info.compress_size and (info.file_size / info.compress_size) > RAZAO_MAX:
        raise PlanilhaInvalida(
            "A planilha expande %dx ao ser aberta, o que não é normal. "
            "Recusei por segurança." % (info.file_size / info.compress_size))
    return z.read(nome)          # em memória; nada é extraído para o disco


def _ler_xlsx(bruto: bytes) -> list:
    """As linhas da PRIMEIRA planilha, como listas de texto.

    Só biblioteca padrão: o `.xlsx` é um ZIP de XMLs, e o que interessa são
    `sharedStrings.xml` e a primeira `worksheets/sheet*.xml`. Nada mais é
    aberto — vínculo externo, gráfico e macro ficam onde estão.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(bruto))
    except zipfile.BadZipFile:
        raise PlanilhaInvalida(
            "Este arquivo não é uma planilha .xlsx do Excel.")
    with z:
        nomes = z.namelist()
        # Macro: recusa. Não é o formato pedido, e um .xlsm com macro é
        # exatamente o que não se abre por hábito.
        if any(n.lower().endswith("vbaproject.bin") for n in nomes):
            raise PlanilhaInvalida(
                "Esta planilha contém macros. Salve como .xlsx comum "
                "(sem macros) e importe de novo.")

        compartilhadas = []
        if "xl/sharedStrings.xml" in nomes:
            raiz = ET.fromstring(_ler_membro(z, "xl/sharedStrings.xml"))
            for si in raiz:
                compartilhadas.append("".join(
                    t.text or "" for t in si.iter()
                    if _sem_ns(t.tag) == "t"))

        folhas = sorted(n for n in nomes
                        if n.startswith("xl/worksheets/sheet")
                        and n.endswith(".xml"))
        if not folhas:
            raise PlanilhaInvalida("A planilha não tem nenhuma aba com dados.")

        linhas, celulas = [], 0
        raiz = ET.fromstring(_ler_membro(z, folhas[0]))
        for elem in raiz.iter():
            if _sem_ns(elem.tag) != "row":
                continue
            atual = []
            for c in elem:
                if _sem_ns(c.tag) != "c":
                    continue
                celulas += 1
                if celulas > CELULAS_MAX:
                    raise PlanilhaInvalida(
                        "A planilha tem células demais (limite: %d)."
                        % CELULAS_MAX)
                tipo = c.get("t") or ""
                valor, formula = "", False
                for f in c:
                    nome = _sem_ns(f.tag)
                    if nome == "f":
                        formula = True
                    elif nome == "v":
                        valor = f.text or ""
                    elif nome == "is":
                        valor = "".join(t.text or "" for t in f.iter()
                                        if _sem_ns(t.tag) == "t")
                        tipo = "inline"
                if formula:
                    # FÓRMULA É RECUSADA, TENHA OU NÃO RESULTADO GRAVADO.
                    #
                    # O resultado guardado no arquivo é o da última vez que
                    # alguém abriu a planilha — pode ser de antes da última
                    # edição, e não há como saber. Um cadastro fiscal não pode
                    # depender de "provavelmente está atualizado". Quem quiser
                    # importar o resultado de uma fórmula cola como valor.
                    valor = "#FORMULA"
                elif tipo == "s" and valor.isdigit():
                    i = int(valor)
                    valor = compartilhadas[i] if i < len(compartilhadas) else ""
                pos = _indice_da_coluna(c.get("r") or "")
                while len(atual) <= pos:
                    atual.append("")
                atual[pos] = str(valor).strip()
            linhas.append(atual)
            if len(linhas) > LINHAS_MAX + 1:
                raise PlanilhaInvalida(
                    "A planilha tem mais de %d linhas." % LINHAS_MAX)
    return linhas


def _ler_csv(bruto: bytes) -> list:
    """As linhas do CSV. Separador descoberto pelo cabeçalho.

    Ponto e vírgula primeiro: é o que o Excel brasileiro grava, porque a
    vírgula aqui é decimal.
    """
    for codificacao in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            texto = bruto.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise PlanilhaInvalida("Não consegui ler o texto deste arquivo .csv.")
    primeira = texto.splitlines()[0] if texto.splitlines() else ""
    sep = ";" if primeira.count(";") >= primeira.count(",") else ","
    linhas = []
    for i, linha in enumerate(csv.reader(io.StringIO(texto), delimiter=sep)):
        linhas.append([str(c).strip() for c in linha])
        if i > LINHAS_MAX + 1:
            raise PlanilhaInvalida(
                "O arquivo tem mais de %d linhas." % LINHAS_MAX)
    return linhas


def ler_planilha(bruto: bytes, nome: str = "") -> tuple:
    """`(linhas, avisos)` — cada linha é um dicionário com os campos do cadastro.

    Não grava nada e não decide nada: só traduz a planilha para o vocabulário
    do cadastro. A conciliação é outro passo, de propósito.
    """
    avisos = []
    if not bruto:
        raise PlanilhaInvalida("O arquivo está vazio.")
    if len(bruto) > TAMANHO_MAX:
        raise PlanilhaInvalida(
            "O arquivo tem %.1f MB e o limite é %.0f MB."
            % (len(bruto) / 1e6, TAMANHO_MAX / 1e6))
    ext = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in (nome or "") else ""
    if ext not in EXTENSOES:
        raise PlanilhaInvalida(
            "Formato não aceito (%s). Envie um arquivo .xlsx ou .csv."
            % (ext or "sem extensão"))

    cruas = _ler_xlsx(bruto) if ext == ".xlsx" else _ler_csv(bruto)
    cruas = [l for l in cruas if any(str(c).strip() for c in l)]
    if not cruas:
        raise PlanilhaInvalida("A planilha não tem nenhuma linha preenchida.")

    cabecalho = cruas[0]
    mapa = {}
    for i, bruto_col in enumerate(cabecalho):
        campo = campo_do_cabecalho(bruto_col)
        if campo and campo not in mapa:
            mapa[campo] = i
    if "cnpj" not in mapa:
        raise PlanilhaInvalida(
            "Não achei a coluna CNPJ. A primeira linha tem de ser o "
            "cabeçalho, com os nomes das colunas.")
    desconhecidas = [c for c in cabecalho
                     if str(c).strip() and not campo_do_cabecalho(c)]
    if desconhecidas:
        avisos.append("Colunas ignoradas (não fazem parte do cadastro): "
                      + ", ".join(str(c)[:30] for c in desconhecidas[:8]))

    linhas = []
    for n, crua in enumerate(cruas[1:], start=2):
        item = {"_linha": n}
        for campo, i in mapa.items():
            item[campo] = str(crua[i]).strip() if i < len(crua) else ""
        linhas.append(item)
    return linhas, avisos


# ── conciliação ─────────────────────────────────────────────────────────────
def _indexar(atuais) -> dict:
    """Cadastro atual por CNPJ normalizado."""
    indice = {}
    for e in (atuais or []):
        c = documento_de(e)
        if c:
            indice.setdefault(c, e)
    return indice


def _valor_atual(empresa, campo) -> str:
    equivalentes = {"razao_social": ("razao_social", "nome"),
                    "nome_fantasia": ("nome_fantasia", "apelido"),
                    "municipio": ("municipio", "mun"),
                    "regime": ("regime",), "ie": ("ie",), "im": ("im",),
                    "uf": ("uf",), "email": ("email",),
                    "contato": ("contato", "tel"),
                    "ibge": ("ibge",), "situacao": ("situacao",),
                    "cnae_principal": ("cnae_principal",),
                    "cnaes_secundarios": ("cnaes_secundarios",)}
    for nome in equivalentes.get(campo, (campo,)):
        v = empresa.get(nome)
        if v not in (None, "", [], {}):
            return v
    return ""


def _mesmo(a, b, campo) -> bool:
    if campo == "cnaes_secundarios":
        return sorted(normalizar_cnaes(a)) == sorted(normalizar_cnaes(b))
    if campo == "cnae_principal":
        return normalizar_cnae(a) == normalizar_cnae(b)
    if campo in ("ie", "im", "ibge"):
        return so_digitos(a) == so_digitos(b)
    return sem_acento(a).casefold().strip() == sem_acento(b).casefold().strip()


def conciliar(atuais, linhas) -> dict:
    """A PRÉVIA. Não grava, não decide, não altera nada.

    Devolve cinco listas, e a diferença entre elas é o coração desta fase:

    novos       CNPJ que não existe no cadastro
    atualizados campo VAZIO no cadastro que a planilha preenche
    inalterados a planilha diz o mesmo que já está lá
    conflitos   a planilha diz algo DIFERENTE do que já está lá
    erros       linha que não dá para usar (CNPJ inválido, falta obrigatório)

    Conflito não é atualização. Trocar o que alguém escreveu é decisão de
    gente, e por isso ele aparece e não é aplicado.
    """
    indice = _indexar(atuais)
    novos, atualizados, inalterados, conflitos, erros = [], [], [], [], []
    vistos = {}

    for linha in linhas:
        n = linha.get("_linha")
        cnpj_bruto = linha.get("cnpj", "")
        cnpj = normalizar_cnpj(cnpj_bruto)
        if not cnpj:
            erros.append({"linha": n, "cnpj": str(cnpj_bruto)[:20],
                          "motivo": "CNPJ vazio ou ilegível"})
            continue
        if not cnpj_valido(cnpj):
            erros.append({"linha": n, "cnpj": formatar_cnpj(cnpj),
                          "motivo": "CNPJ com dígito verificador inválido"})
            continue
        if any("#FORMULA" == linha.get(c) for c in CAMPOS):
            erros.append({"linha": n, "cnpj": formatar_cnpj(cnpj),
                          "motivo": "há célula com fórmula; copie e cole como "
                                    "valor antes de importar"})
            continue
        if cnpj in vistos:
            erros.append({"linha": n, "cnpj": formatar_cnpj(cnpj),
                          "motivo": "CNPJ repetido na planilha (linha %d)"
                                    % vistos[cnpj]})
            continue
        vistos[cnpj] = n

        principal = normalizar_cnae(linha.get("cnae_principal", ""))
        if linha.get("cnae_principal") and not principal:
            erros.append({"linha": n, "cnpj": formatar_cnpj(cnpj),
                          "motivo": "CNAE principal inválido (%s)"
                                    % str(linha.get("cnae_principal"))[:20]})
            continue
        secundarias = normalizar_cnaes(linha.get("cnaes_secundarios", ""),
                                       principal)

        existente = indice.get(cnpj)
        proposto = {"cnpj": cnpj, "cnae_principal": principal,
                    "cnaes_secundarios": secundarias}
        for campo in CAMPOS:
            if campo in ("cnpj", "cnae_principal", "cnaes_secundarios"):
                continue
            proposto[campo] = str(linha.get(campo, "") or "").strip()
        proposto["uf"] = proposto["uf"].upper()[:2]
        proposto["ibge"] = so_digitos(proposto["ibge"])[:7]

        if existente is None:
            faltando = [ROTULOS[c] for c in obrigatorios_de(cnpj)
                        if not proposto.get(c)]
            if faltando:
                erros.append({"linha": n, "cnpj": formatar_cnpj(cnpj),
                              "motivo": "cadastro novo sem " + ", ".join(faltando)})
                continue
            novos.append({"linha": n, "cnpj": cnpj, "dados": proposto,
                          "razao_social": proposto["razao_social"]})
            continue

        preencher, choca = {}, []
        for campo in CAMPOS:
            if campo == "cnpj":
                continue
            novo = proposto.get(campo)
            if novo in ("", [], None):
                continue
            atual = _valor_atual(existente, campo)
            if atual in ("", [], None):
                preencher[campo] = novo
            elif not _mesmo(atual, novo, campo):
                choca.append({"campo": campo, "rotulo": ROTULOS[campo],
                              "atual": atual, "planilha": novo})
        alvo = {"linha": n, "cnpj": cnpj,
                "razao_social": _valor_atual(existente, "razao_social")
                                or proposto["razao_social"],
                "id": existente.get("id")}
        if choca:
            conflitos.append({**alvo, "campos": choca, "preencher": preencher})
        elif preencher:
            atualizados.append({**alvo, "preencher": preencher})
        else:
            inalterados.append(alvo)

    return {"novos": novos, "atualizados": atualizados,
            "inalterados": inalterados, "conflitos": conflitos,
            "erros": erros,
            "resumo": {"novos": len(novos), "atualizados": len(atualizados),
                       "inalterados": len(inalterados),
                       "conflitos": len(conflitos), "erros": len(erros),
                       "linhas": len(linhas)}}


def proximo_id(atuais) -> int:
    maior = 0
    for e in (atuais or []):
        try:
            maior = max(maior, int(e.get("id") or 0))
        except (TypeError, ValueError):
            continue
    return maior + 1


def aplicar(atuais, previa, aceitar_conflitos=False) -> tuple:
    """`(nova_lista, aplicado)`. Só o que a prévia mostrou.

    `aceitar_conflitos` existe para o dia em que alguém OLHAR os conflitos e
    decidir. O padrão é não tocar neles: essa é a diferença entre importar e
    atropelar.
    """
    atuais = list(atuais or [])
    por_cnpj = {}
    for i, e in enumerate(atuais):
        c = documento_de(e)
        if c:
            por_cnpj.setdefault(c, i)

    aplicado = {"criados": 0, "atualizados": 0, "campos": 0,
                "conflitos_aplicados": 0}
    seq = proximo_id(atuais)

    for item in previa.get("novos", []):
        d = dict(item["dados"])
        d["id"] = seq
        seq += 1
        atuais.append(d)
        aplicado["criados"] += 1

    def preencher_em(item, conta_conflito=False):
        i = por_cnpj.get(item["cnpj"])
        if i is None:
            return
        alvo = dict(atuais[i])
        mudou = 0
        for campo, valor in (item.get("preencher") or {}).items():
            alvo[campo] = valor
            mudou += 1
        if conta_conflito:
            for c in item.get("campos", []):
                alvo[c["campo"]] = c["planilha"]
                mudou += 1
        if mudou:
            atuais[i] = alvo
            aplicado["atualizados"] += 1
            aplicado["campos"] += mudou

    for item in previa.get("atualizados", []):
        preencher_em(item)
    if aceitar_conflitos:
        for item in previa.get("conflitos", []):
            preencher_em(item, conta_conflito=True)
            aplicado["conflitos_aplicados"] += 1

    return ordenar(atuais), aplicado


# ── modelo para download ────────────────────────────────────────────────────
def modelo_csv() -> bytes:
    """O modelo, em CSV. Uma linha de exemplo, com dado inventado.

    Ponto e vírgula, e BOM no começo: é o que faz o Excel brasileiro abrir o
    arquivo com as colunas separadas em vez de tudo numa célula só.
    """
    cabecalho = [ROTULOS[c] for c in CAMPOS]
    exemplo = ["00.000.000/0001-91", "EMPRESA EXEMPLO LTDA", "Exemplo",
               "6920-6/01", "6201-5/01;6209-1/00", "ISENTO", "123456-7",
               "Recife", "PE", "2611606", "Simples Nacional", "Ativa",
               "contato@exemplo.com.br", "(81) 99999-0000"]
    # A segunda linha é PESSOA FÍSICA, e está aqui de propósito: sem ela,
    # ninguém descobriria que dá para cadastrar CPF — e que ele não precisa
    # de CNAE.
    exemplo_pf = ["111.444.777-35", "FULANO DE TAL", "", "", "", "", "98765-4",
                  "Recife", "PE", "2611606", "Simples Nacional", "Ativa",
                  "fulano@exemplo.com.br", "(81) 98888-0000"]
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    w.writerow(cabecalho)
    w.writerow(exemplo)
    w.writerow(exemplo_pf)
    return "﻿".encode("utf-8") + saida.getvalue().encode("utf-8")


# ════════════════════════════════════════════════════════════════════════════
#  A FICHA — o que o sistema sabe da empresa, e de onde
# ════════════════════════════════════════════════════════════════════════════
def ficha(registro, *, certificado=None, inscricoes=None, regularidade=None,
          acervo=None, apuracao=None) -> dict:
    """Consolida o que se sabe de UMA empresa, dizendo a fonte de cada dado.

    FUNÇÃO PURA, de propósito: recebe o que o chamador já leu e não abre arquivo
    nenhum. É o que mantém este módulo testável sem raiz de dados, e é o que
    impede que ele se torne um sexto leitor de `state_clientes.json`.

    A divergência é RELATADA, nunca resolvida aqui. Quando o cadastro guarda uma
    cópia (a validade do certificado é o caso real) e a origem diz outra coisa,
    as duas aparecem com os nomes das fontes. Escolher uma em silêncio é o que
    produz o campo que ninguém entende seis meses depois.
    """
    reg = dict(registro or {})
    doc = documento_de(reg)
    campos = {}
    for campo in CAMPOS:
        dono, onde = FONTE_DO_CAMPO.get(campo, (MESTRE, "cadastro (Clientes)"))
        campos[campo] = {
            "rotulo": ROTULOS.get(campo, campo),
            "valor": reg.get(campo) if campo != "cnpj" else formatar_cnpj(doc),
            "dono": dono,
            "fonte": onde,
            # Só o que o cadastro possui pode ser editado nele. Um campo
            # DERIVADO editado à mão vira a segunda verdade que este mapa
            # existe para impedir.
            "editavel_no_cadastro": dono != DERIVADO,
        }

    vinculos, divergencias = {}, []

    # ── certificado: a origem é `certificados.json`, nunca o cadastro ──────
    cert = dict(certificado or {})
    val_origem = str(cert.get("validade") or "")[:10]
    val_copia = str(reg.get("certValidade") or "")[:10]
    vinculos["certificado"] = {
        "tem": bool(cert.get("arquivo") or cert.get("caminho") or reg.get("cert")),
        "validade": val_origem or val_copia or "",
        "dono": DERIVADO,
        "fonte": FORA_DO_CADASTRO["certificado"][1],
        "onde": "clientes.html",
    }
    if val_origem and val_copia and val_origem != val_copia:
        divergencias.append({
            "assunto": "validade do certificado",
            "no_cadastro": val_copia, "na_origem": val_origem,
            "fonte_que_manda": FORA_DO_CADASTRO["certificado"][1],
            "o_que_fazer": "a origem é o certificado; a cópia do cadastro está "
                           "velha e será reescrita na próxima leitura do .pfx",
        })

    # ── IE e IM: nascem no XML ────────────────────────────────────────────
    insc = dict(inscricoes or {})
    for campo, especie in (("ie", "NF-e"), ("im", "NFS-e")):
        lido = str(insc.get(campo) or "").strip()
        guardado = str(reg.get(campo) or "").strip()
        vinculos[campo] = {"no_cadastro": guardado, "no_xml": lido,
                           "dono": DERIVADO,
                           "fonte": FONTE_DO_CAMPO[campo][1], "especie": especie}
        if lido and guardado and so_digitos(lido) != so_digitos(guardado):
            divergencias.append({
                "assunto": ROTULOS[campo],
                "no_cadastro": guardado, "na_origem": lido,
                "fonte_que_manda": FONTE_DO_CAMPO[campo][1],
                "o_que_fazer": "confira no XML: a inscrição do documento é a "
                               "que os módulos fiscais usam",
            })

    # ── regularidade: é do pacote `situacao/`, e NÃO é o campo `situacao` ──
    vinculos["regularidade"] = {
        "status": (regularidade or {}).get("status") or "",
        "rotulo": (regularidade or {}).get("rotulo") or "",
        "dono": DERIVADO,
        "fonte": FORA_DO_CADASTRO["regularidade"][1],
        "onde": "situacao.html",
        "nao_confundir_com": "o campo «%s», que é do CNPJ"
                             % ROTULOS["situacao"],
    }
    vinculos["acervo"] = {
        "documentos": (acervo or {}).get("documentos"),
        "dono": DERIVADO, "fonte": FORA_DO_CADASTRO["acervo"][1],
        "onde": "nfe_documentos.html",
    }
    vinculos["apuracao"] = {
        "configurada": bool(apuracao),
        "dono": DERIVADO, "fonte": FORA_DO_CADASTRO["apuracao"][1],
        "onde": "classificador.html",
    }

    return {
        "identidade": formatar_cnpj(doc),
        "tipo": tipo_documento(doc),
        "nome": (reg.get("nome") or reg.get("razao_social") or "").strip(),
        "campos": campos,
        "vinculos": vinculos,
        "divergencias": divergencias,
        "mestre": [c for c in CAMPOS
                   if FONTE_DO_CAMPO.get(c, (MESTRE,))[0] == MESTRE],
        "derivado": [c for c in CAMPOS
                     if FONTE_DO_CAMPO.get(c, (MESTRE,))[0] == DERIVADO],
        "regra": ("o cadastro é a fonte do que é dele; o que nasce em outra "
                  "fonte aparece aqui como derivado, com o nome dela"),
    }
