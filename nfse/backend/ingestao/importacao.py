"""importacao.py — o PORTÃO ÚNICO por onde entra XML que não veio da SEFAZ.

POR QUE ELE EXISTE
    A Distribuição DF-e entrega o que a empresa **não** gerou (D2). A NF-e de
    emissão própria nasce no emissor dela e nunca volta pela distribuição —
    logo, a receita de venda por NF-e só tem uma fonte possível: importação
    manual de XML. O inventário da APURAÇÃO 6A mediu o tamanho do buraco:
    8.059 NF-e no acervo, **zero** de emissão própria (D50).

    Antes deste módulo, a importação gravava numa pasta própria,
    `nfe_importadas`, que não é o acervo. Duas pastas, duas verdades: a
    apuração podia ler uma enquanto a tela mostrava a outra.

O QUE ELE FAZ
    Uma coisa só: recebe bytes, decide se aquilo é um documento fiscal válido
    **desta** empresa, e — quando é — entrega ao `acervo.preservar()`. A partir
    daí o documento segue o mesmo caminho de tudo que veio da SEFAZ:
    `normalizacao` → `vendas` → `rastreio`.

O QUE ELE NÃO FAZ, E É DE PROPÓSITO
    - **Não toca em checkpoint.** `acervo.preservar()` não depende de NSU: o
      `nsu` é só um metadado em `captura.json`, e quem move ponteiro é o
      `DistribuicaoRunner`. Importar 10.000 XML deixa o checkpoint da
      distribuição byte a byte igual — e existe teste que prova isso.
    - Não fala com a rede. Nenhum import de transporte, nenhuma URL.
    - Não calcula imposto, não decide anexo, não segrega monofásico nem ST.
    - Não apaga nem move o que já está em `nfe_importadas`.

A REGRA QUE ORDENA TUDO: NUNCA DESCARTAR EM SILÊNCIO
    Todo arquivo sai deste módulo com um veredito escrito. Aceito, e diz onde
    foi parar; recusado, e diz por quê. Um XML que "sumiu" é pior que um XML
    recusado, porque o recusado o contador consegue corrigir.

AUSÊNCIA DE EVENTO NÃO É PROVA DE NADA
    Se a distribuição não devolve a NF-e própria, ela também não devolve o
    **cancelamento** dela. Uma venda cancelada cujo evento não foi importado
    contaria como receita para sempre, em silêncio — errando para mais. Por
    isso `situacao_das_operacoes()` responde em três estados, e o terceiro é
    "não sei": `sem_evento_conhecido` **não** quer dizer "nota ativa".
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import acervo as acv
from . import documento as dm
from . import identificacao as idf
from . import normalizacao as nz
from .ambiente import PRODUCAO
from .distribuicao import DocumentoBruto
from .identidade import normalizar

# Rótulo de fonte gravado em `captura.json`. Serve para responder, meses
# depois, "de onde veio este documento?" sem depender de memória de ninguém.
FONTE_IMPORTACAO = "IMPORTACAO_XML"

NS_NFE = "{http://www.portalfiscal.inf.br/nfe}"
MODELO_NFE = "55"
MODELO_NFCE = "65"          # reconhecido para poder RECUSAR com motivo claro

# ── papel da empresa no documento ───────────────────────────────────────────
PROPRIA = "EMISSAO_PROPRIA"       # a empresa emitiu: candidata a receita
TERCEIRO = "TERCEIRO"             # a empresa é destinatária ou transportadora
AUTORIZADO = "AUTORIZADO"         # consta em `autXML`: pode baixar, não é dela
EVENTO = "EVENTO"                 # evento vinculado a uma chave

# ── motivos de recusa ───────────────────────────────────────────────────────
ILEGIVEL = "ILEGIVEL"
ESPECIE_NAO_SUPORTADA = "ESPECIE_NAO_SUPORTADA"
CHAVE_AUSENTE = "CHAVE_AUSENTE"
CHAVE_INVALIDA = "CHAVE_INVALIDA"
CHAVE_DIVERGENTE = "CHAVE_DIVERGENTE"
MODELO_NAO_SUPORTADO = "MODELO_NAO_SUPORTADO"
EMPRESA_AUSENTE = "EMPRESA_AUSENTE"
DENEGADA = "DENEGADA"
FALHA_AO_PRESERVAR = "FALHA_AO_PRESERVAR"

TEXTO_DO_MOTIVO = {
    ILEGIVEL: "não é um XML legível",
    ESPECIE_NAO_SUPORTADA: "não é NF-e modelo 55 nem evento de NF-e",
    CHAVE_AUSENTE: "sem chave de acesso",
    CHAVE_INVALIDA: "chave de acesso com dígito verificador errado",
    CHAVE_DIVERGENTE: "a chave não confere com o conteúdo do XML",
    MODELO_NAO_SUPORTADO: "modelo fora de 55 (NF-e) e 65 (NFC-e)",
    EMPRESA_AUSENTE: "a empresa não participa deste documento",
    DENEGADA: "nota denegada — nunca produziu efeito fiscal",
    FALHA_AO_PRESERVAR: "não foi possível gravar no acervo",
}

# ── por que a nota não pode formar receita, mesmo preservada ────────────────
RECEITA_POSSIVEL = ""
NAO_E_EMISSAO_PROPRIA = "não foi emitida por esta empresa"
SOMENTE_AUTORIZADO = "consta apenas como autorizado a baixar o XML"
SEM_PROTOCOLO = "sem protocolo de autorização"

# cStat que autorizam. 100 = autorizado; 150 = autorizado fora do prazo.
CSTAT_AUTORIZA = frozenset({"100", "150"})
# cStat de denegação: o documento existe, mas nunca valeu.
CSTAT_DENEGA = frozenset({"110", "301", "302", "303"})

# O critério de cancelamento mora em `documento.py`, junto das situações — a
# consulta indexada também precisa dele, e ela não pode depender do portão de
# ESCRITA para saber o que é um cancelamento. Reexportado aqui porque este
# módulo é quem historicamente o expunha.
CANCELAMENTOS = dm.CANCELAMENTOS
e_cancelamento = dm.e_cancelamento

_SO_DIG = re.compile(r"\D")


def _local_sem_ns(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _dig(v) -> str:
    return _SO_DIG.sub("", str(v or ""))


def _texto(raiz, caminho: str) -> str:
    v = raiz.findtext(caminho)
    return (v or "").strip()


@dataclass(frozen=True)
class Veredito:
    """O que aconteceu com UM arquivo. Nunca há arquivo sem veredito."""
    arquivo: str
    aceito: bool = False
    motivo: str = ""                  # preenchido quando `aceito` é falso
    detalhe: str = ""
    papel: str = ""                   # PROPRIA | TERCEIRO | EVENTO
    id_documento: str = ""
    chave: str = ""
    especie: str = ""
    resultado_acervo: str = ""        # NOVO | DUPLICATA | COPIA_* | COLISAO
    impedimento_receita: str = ""     # vazio = pode ser receita
    quarentena: bool = False

    @property
    def preservado(self) -> bool:
        return self.aceito and self.resultado_acervo != ""

    @property
    def duplicado(self) -> bool:
        return self.resultado_acervo == acv.DUPLICATA

    @property
    def pode_ser_receita(self) -> bool:
        """Emissão própria, preservada e sem impedimento declarado.

        Não afirma que **é** receita — isso quem decide é `vendas.py`, pelo
        CFOP. Afirma só que nada aqui a impede."""
        return (self.aceito and self.papel == PROPRIA
                and not self.impedimento_receita)

    def para_json(self) -> dict:
        return {"arquivo": self.arquivo, "aceito": self.aceito,
                "motivo": self.motivo or None,
                "detalhe": self.detalhe or None,
                "papel": self.papel or None,
                "id_documento": self.id_documento or None,
                # A chave inteira identifica emitente e número da nota; no
                # relatório vai só o final, que basta para conferir.
                "chave_final": self.chave[-8:] if self.chave else None,
                "especie": self.especie or None,
                "resultado_acervo": self.resultado_acervo or None,
                "impedimento_receita": self.impedimento_receita or None,
                "quarentena": self.quarentena}


@dataclass
class RelatorioImportacao:
    """O fechamento de uma importação. Some nada, esconde nada."""
    identidade: str = ""
    vereditos: list = field(default_factory=list)
    # Preservar sem indexar deixa o documento existindo e invisível: ele está
    # no acervo, mas nenhuma consulta o encontra. A importação só termina
    # quando o índice alcança o acervo — e `indice_em_dia` diz se alcançou.
    indexados: int = 0
    falhas_indexacao: int = 0
    indice_em_dia: bool = False

    @property
    def preservados(self) -> int:
        return sum(1 for v in self.vereditos if v.preservado and not v.duplicado)

    @property
    def duplicados(self) -> int:
        return sum(1 for v in self.vereditos if v.duplicado)

    @property
    def rejeitados(self) -> int:
        return sum(1 for v in self.vereditos if not v.aceito)

    def por_motivo(self) -> dict:
        d: dict = {}
        for v in self.vereditos:
            if not v.aceito:
                d[v.motivo] = d.get(v.motivo, 0) + 1
        return dict(sorted(d.items()))

    def por_papel(self) -> dict:
        d: dict = {}
        for v in self.vereditos:
            if v.aceito:
                d[v.papel] = d.get(v.papel, 0) + 1
        return dict(sorted(d.items()))

    def resumo(self) -> dict:
        return {"empresa": normalizar(self.identidade).mascarado(),
                "arquivos": len(self.vereditos),
                "preservados": self.preservados,
                "duplicados": self.duplicados,
                "rejeitados": self.rejeitados,
                "indexados": self.indexados,
                "falhas_indexacao": self.falhas_indexacao,
                "indice_em_dia": self.indice_em_dia,
                "por_motivo": self.por_motivo(),
                "por_papel": self.por_papel(),
                "podem_ser_receita": sum(1 for v in self.vereditos
                                         if v.pode_ser_receita)}


# ════════════════════════════════════════════════════════════════════════════
#  As validações. Cada uma devolve `(motivo, detalhe)`; motivo vazio = passou.
# ════════════════════════════════════════════════════════════════════════════
def _ler_xml(conteudo: bytes):
    try:
        return ET.fromstring(conteudo.decode("utf-8", "ignore")
                             if isinstance(conteudo, bytes) else str(conteudo))
    except Exception:
        return None


def _autorizados(raiz) -> list:
    """Os CNPJ/CPF do grupo `autXML`. Lista vazia quando não há nenhum.

    O emitente pode autorizar até 10 terceiros a baixar o XML. Para o
    escritório contábil é este grupo que transforma a Distribuição DF-e numa
    fonte da nota que o CLIENTE emitiu — sem ele, a distribuição só traz o que
    a empresa não gerou (D2)."""
    saida = []
    for a in raiz.iter():
        if a.tag != f"{NS_NFE}autXML":
            continue
        v = _dig(_texto(a, f"{NS_NFE}CNPJ") or _texto(a, f"{NS_NFE}CPF"))
        if v:
            saida.append(v)
    return saida


def _dados_do_resumo(raiz) -> dict | None:
    """O `resNFe` — o RESUMO que a Distribuição DF-e entrega — também é
    documento preservável.

    POR QUE ELE PRECISOU ENTRAR AQUI
        O portão nasceu para importação manual, onde só faz sentido aceitar o
        documento completo. A migração dos XML legados (NF-e 3) mostrou o custo
        dessa restrição: **15 resumos não têm `procNFe` correspondente em lugar
        nenhum**. Recusá-los deixaria 15 documentos fiscais existindo só na
        pasta antiga — exatamente o buraco de cobertura que a fase existe para
        fechar.

    O QUE O RESUMO NÃO DIZ, E NÃO SE INVENTA
        Ele traz emitente, valor, data e situação. **Não traz destinatário,
        transportador nem `autXML`.** Por isso o papel da empresa sai daqui
        como indeterminado, e `_papel_da_empresa` o resolve como `TERCEIRO` —
        que é a forma honesta de dizer "chegou para ela, mas o documento não
        afirma em que qualidade".

        Nunca como emissão própria: resumo não forma receita, e o próprio
        `_impedimento_de_receita` já barra o `TERCEIRO`.
    """
    if _local_sem_ns(raiz.tag) != "resNFe":
        return None
    chave = _dig(_texto(raiz, f"{NS_NFE}chNFe"))
    if not chave:
        return None
    return {
        "chave": chave,
        # O resumo não traz `mod`; o modelo vem da própria chave, que é onde
        # ele é autoritativo (APURAÇÃO 6D).
        "modelo": chave[20:22] or MODELO_NFE,
        "emit": _dig(_texto(raiz, f"{NS_NFE}CNPJ") or _texto(raiz, f"{NS_NFE}CPF")),
        "dest": "", "transp": "", "autorizados": (),
        "cstat": "",
        "tem_protocolo": bool(_texto(raiz, f"{NS_NFE}nProt")),
        "resumo": True,
    }


def _dados_da_nfe(raiz) -> dict | None:
    """Os campos que as validações precisam. `None` se não for uma NF-e."""
    inf = raiz.find(f".//{NS_NFE}infNFe")
    if inf is None:
        return _dados_do_resumo(raiz)
    emit = inf.find(f"{NS_NFE}emit")
    dest = inf.find(f"{NS_NFE}dest")
    transp = inf.find(f".//{NS_NFE}transporta")
    prot = raiz.find(f".//{NS_NFE}infProt")
    return {
        "chave": _dig(inf.get("Id") or ""),
        "modelo": _texto(inf, f"{NS_NFE}ide/{NS_NFE}mod") or MODELO_NFE,
        "emit": _dig(_texto(emit, f"{NS_NFE}CNPJ") or _texto(emit, f"{NS_NFE}CPF"))
                if emit is not None else "",
        "dest": _dig(_texto(dest, f"{NS_NFE}CNPJ") or _texto(dest, f"{NS_NFE}CPF"))
                if dest is not None else "",
        "transp": _dig(_texto(transp, f"{NS_NFE}CNPJ")) if transp is not None else "",
        "autorizados": _autorizados(inf),
        "cstat": _texto(prot, f"{NS_NFE}cStat") if prot is not None else "",
        "tem_protocolo": prot is not None,
    }


def _validar_chave(chave: str) -> tuple[str, str]:
    if not chave:
        return CHAVE_AUSENTE, ""
    if len(chave) != 44:
        return CHAVE_INVALIDA, f"{len(chave)} dígitos, esperava 44"
    if not idf.chave_valida(chave):
        return CHAVE_INVALIDA, f"DV informado {chave[43]}, calculado {idf.dv_chave(chave)}"
    return "", ""


MODELOS_ACEITOS = frozenset({MODELO_NFE, MODELO_NFCE})


def _validar_modelo(modelo_chave: str, modelo_xml: str) -> tuple[str, str]:
    """Modelos 55 e 65. Qualquer outro é recusado com o número à vista.

    A NFC-e entrou na APURAÇÃO 6D **com espécie própria**, `NFCE65`. Até então
    ela era recusada aqui, e a recusa estava certa para o que existia: as duas
    usam as mesmas tags raiz (`nfeProc`, `NFe`, `resNFe`), e aceitá-la antes de
    a identificação saber separá-las teria gravado cupom sob o rótulo `NFE55`.
    Um id que existe e mente é pior que uma recusa que explica.

    Agora quem decide a espécie é o modelo **na chave**, não a tag raiz, e as
    duas convivem sem se confundir."""
    if modelo_chave and modelo_chave not in MODELOS_ACEITOS:
        return MODELO_NAO_SUPORTADO, f"modelo {modelo_chave} na chave"
    if modelo_xml and modelo_xml not in MODELOS_ACEITOS:
        return MODELO_NAO_SUPORTADO, f"modelo {modelo_xml} no XML"
    if modelo_chave and modelo_xml and modelo_chave != modelo_xml:
        return CHAVE_DIVERGENTE, (f"modelo {modelo_chave} na chave, "
                                  f"{modelo_xml} no XML")
    return "", ""


def _validar_chave_x_conteudo(chave: str, d: dict) -> tuple[str, str]:
    """A chave carrega CNPJ e modelo; os dois têm que bater com o corpo.

    Pega XML remontado, chave trocada e arquivo renomeado à mão — que é
    exatamente o tipo de coisa que aparece quando o dado vem por e-mail."""
    cnpj_na_chave = chave[6:20]
    if d["emit"] and cnpj_na_chave != d["emit"]:
        return CHAVE_DIVERGENTE, "o CNPJ dentro da chave não é o do emitente"
    if d["modelo"] and chave[20:22] != d["modelo"]:
        return CHAVE_DIVERGENTE, (f"modelo {chave[20:22]} na chave, "
                                  f"{d['modelo']} no XML")
    return "", ""


def _papel_da_empresa(d: dict, cnpj: str) -> str:
    """Emitiu, recebeu, transportou — ou não participa.

    Vale a mesma regra que `nfe._classificar_papel()` já aplicava e que estava
    certa: uma nota em que a empresa só aparece como transportadora não é
    compra dela, e uma em que ela não aparece de forma alguma não é dela."""
    if d["emit"] == cnpj:
        return PROPRIA
    if d["dest"] == cnpj or d["transp"] == cnpj:
        return TERCEIRO
    if d.get("resumo"):
        # O resumo não nomeia destinatário nem transportador. Ele só chega
        # porque o Ambiente Nacional o considerou de interesse desta empresa;
        # em que qualidade, o documento não diz — e adivinhar aqui produziria
        # exatamente a inferência que a NF-e 2 proibiu.
        return TERCEIRO
    if cnpj in (d.get("autorizados") or ()):
        # O escritório costuma ser SÓ isto: autorizado a baixar. O documento é
        # legítimo e precisa ser preservado, mas não é dele — nem entrada, nem
        # saída. Sem este papel, a nota do cliente chegaria pelo `autXML` e
        # seria recusada como EMPRESA_AUSENTE.
        return AUTORIZADO
    return ""


def _impedimento_de_receita(papel: str, d: dict) -> str:
    if papel == AUTORIZADO:
        return SOMENTE_AUTORIZADO
    if papel != PROPRIA:
        return NAO_E_EMISSAO_PROPRIA
    if not d["tem_protocolo"] or d["cstat"] not in CSTAT_AUTORIZA:
        # Preservar sim, contar como receita não. Recusar a guardar seria
        # perder documento; contar seria afirmar o que o XML não afirma.
        return SEM_PROTOCOLO
    return ""


# ════════════════════════════════════════════════════════════════════════════
#  O portão
# ════════════════════════════════════════════════════════════════════════════
def _preservar(ac, nome: str, conteudo: bytes, chave: str, papel: str,
               impedimento: str, especie: str) -> Veredito:
    try:
        p = ac.preservar(DocumentoBruto(nsu="", conteudo=conteudo, chave=chave))
    except Exception as exc:
        return Veredito(arquivo=nome, aceito=False, motivo=FALHA_AO_PRESERVAR,
                        detalhe=str(exc)[:200], chave=chave, papel=papel)
    return Veredito(arquivo=nome, aceito=True, papel=papel,
                    id_documento=p.id_documento, chave=chave,
                    especie=especie, resultado_acervo=p.resultado,
                    impedimento_receita=impedimento,
                    quarentena=p.quarentena, detalhe=p.detalhe)


def avaliar(conteudo: bytes, identidade: str) -> tuple[str, str, str, str, str]:
    """Só as validações, sem gravar nada. Devolve
    `(motivo, detalhe, papel, chave, impedimento)`.

    Existe separado de `importar()` para que a interface possa dizer "este XML
    vai ser recusado, e por quê" antes de o usuário confirmar — e para que o
    teste prove as regras sem tocar em disco."""
    cnpj = normalizar(identidade).valor
    raiz = _ler_xml(conteudo)
    if raiz is None:
        return ILEGIVEL, "", "", "", ""

    ident = idf.identificar(conteudo if isinstance(conteudo, bytes)
                            else str(conteudo).encode("utf-8"))

    # ── evento ────────────────────────────────────────────────────────────
    if ident.especie == idf.EVENTO_NFE:
        chave = _dig(ident.chave)
        motivo, detalhe = _validar_chave(chave)
        if motivo:
            return motivo, detalhe, "", chave, ""
        motivo, detalhe = _validar_modelo(idf.modelo_da_chave(chave), "")
        if motivo:
            return motivo, detalhe, "", chave, ""
        return "", "", EVENTO, chave, ""

    d = _dados_da_nfe(raiz)
    if ident.especie not in idf.ESPECIES_NOTA or d is None:
        return ESPECIE_NAO_SUPORTADA, ident.raiz or "", "", "", ""

    chave = d["chave"] or _dig(ident.chave)
    for motivo, detalhe in (_validar_chave(chave),
                            _validar_modelo(idf.modelo_da_chave(chave), d["modelo"]),
                            _validar_chave_x_conteudo(chave, d)):
        if motivo:
            return motivo, detalhe, "", chave, ""

    if d["cstat"] in CSTAT_DENEGA:
        return DENEGADA, f"cStat {d['cstat']}", "", chave, ""

    papel = _papel_da_empresa(d, cnpj)
    if not papel:
        return EMPRESA_AUSENTE, "nem emitente, nem destinatária, nem transportadora", \
               "", chave, ""

    return "", "", papel, chave, _impedimento_de_receita(papel, d)


def importar(dados_dir, identidade, arquivos, *,
             ambiente=PRODUCAO, fonte: str = "") -> RelatorioImportacao:
    """Valida e preserva. `arquivos` = lista de `(nome, bytes)`.

    Idempotente por construção: o acervo deduplica por identidade e por hash,
    então reimportar a mesma pasta não cria documento nenhum — devolve
    `DUPLICATA` e não escreve byte.

    `fonte` é a PROCEDÊNCIA gravada no `captura.json`. O padrão continua sendo
    `IMPORTACAO_XML`, que é o que a tela faz. A migração dos XML legados
    (NF-e 3) passa por este mesmo portão informando `LEGADO_NFE` — assim o
    documento entra com a origem certa sem que exista um segundo caminho de
    escrita no acervo.
    """
    ident = normalizar(identidade)
    rel = RelatorioImportacao(identidade=ident.valor if ident.valido
                              else str(identidade))
    if not ident.valido:
        for nome, _ in arquivos or ():
            rel.vereditos.append(Veredito(arquivo=nome, aceito=False,
                                          motivo=EMPRESA_AUSENTE,
                                          detalhe="identidade fiscal inválida"))
        return rel

    ac = acv.abrir(dados_dir, ident.valor, servico=fonte or FONTE_IMPORTACAO,
                   ambiente=ambiente)

    for nome, conteudo in arquivos or ():
        if not isinstance(conteudo, bytes):
            conteudo = str(conteudo).encode("utf-8")
        motivo, detalhe, papel, chave, impedimento = avaliar(conteudo, ident.valor)
        if motivo:
            rel.vereditos.append(Veredito(arquivo=nome, aceito=False,
                                          motivo=motivo, detalhe=detalhe,
                                          chave=chave))
            continue
        # A espécie é a que a identificação decidiu — `NFE55` ou `NFCE65` —,
        # nunca uma constante fixa aqui.
        especie = (idf.EVENTO_NFE if papel == EVENTO
                   else idf.especie_da_nota(chave))
        rel.vereditos.append(_preservar(ac, nome, conteudo, chave, papel,
                                        impedimento, especie))

    _fechar_indice(dados_dir, ident.valor, ac, rel)
    return rel


def _fechar_indice(dados_dir, identidade, ac, rel: RelatorioImportacao) -> None:
    """Reconcilia o índice com o acervo. Roda SEMPRE, custa nada quando em dia.

    Roda mesmo quando nada foi preservado — e isso é de propósito. O defeito
    achado no M1 não foi "esquecemos de indexar o que acabamos de gravar"; foi
    o índice ficar atrás sem que nada denunciasse. Uma importação que não muda
    nada é justamente a ocasião de descobrir que ele já estava atrás.

    Idempotente: com tudo em dia, `indexar_ausentes` devolve `total = 0` e não
    reescreve linha nenhuma. Zero reindexação desnecessária.
    """
    from . import indice as idx

    try:
        r = idx.indexar_ausentes(dados_dir, identidade, trilha=ac.trilha)
        rel.indexados = r.indexados
        rel.falhas_indexacao = r.total - r.indexados
        rel.indice_em_dia = idx.abrir(dados_dir, identidade).em_dia_com(ac)
    except Exception:
        # Índice é artefato derivado: falhar aqui não pode desfazer o que já
        # está preservado. Fica registrado como "não em dia" — visível.
        rel.indice_em_dia = False


def importar_pasta(dados_dir, identidade, pasta, *, padrao="*.xml",
                   ambiente=PRODUCAO) -> RelatorioImportacao:
    """Atravessa uma pasta inteira pelo portão. **Não move nem apaga nada.**

    É o que a migração M2 usa sobre `nfe_importadas`: a pasta antiga continua
    exatamente como está, e o acervo passa a ter os mesmos documentos."""
    p = Path(pasta)
    if not p.is_dir():
        return RelatorioImportacao(identidade=str(identidade))
    arquivos = [(a.name, a.read_bytes()) for a in sorted(p.rglob(padrao))]
    return importar(dados_dir, identidade, arquivos, ambiente=ambiente)


# ════════════════════════════════════════════════════════════════════════════
#  Eventos → situação. Três estados, e o terceiro é "não sei".
# ════════════════════════════════════════════════════════════════════════════
SEM_EVENTO_CONHECIDO = "SEM_EVENTO_CONHECIDO"


@dataclass
class RelatorioSituacao:
    """Quantas notas estão canceladas — e quantas simplesmente não se sabe."""
    canceladas: int = 0
    sem_evento_conhecido: int = 0
    chaves_canceladas: tuple = ()

    def resumo(self) -> dict:
        return {"canceladas": self.canceladas,
                "sem_evento_conhecido": self.sem_evento_conhecido,
                "advertencia": (
                    "ausência de evento NÃO confirma que a nota está ativa: a "
                    "Distribuição DF-e não devolve o cancelamento da NF-e "
                    "própria, e ele só existe aqui se tiver sido importado")}


def cancelamentos_no_acervo(dados_dir, identidade) -> dict:
    """`chave → dados do evento` para todo cancelamento preservado.

    Lê o acervo, não um índice: o acervo é a verdade, e o índice é derivado.
    """
    from . import parsers as prs

    ident = normalizar(identidade)
    alvo = ident.valor if ident.valido else str(identidade)
    ac = acv.abrir(dados_dir, alvo)
    achados: dict = {}
    for especie, id_doc in ac.listar(idf.EVENTO_NFE):
        try:
            bruto = ac.caminho_canonico(especie, id_doc).read_bytes()
        except OSError:
            continue
        i = idf.identificar(bruto)
        # A identificação lê a casca; o `nProt` só sai do parser. Por isso aqui
        # o critério roda sem exigir protocolo — ver `e_cancelamento`.
        if not e_cancelamento(i.tp_evento, exigir_protocolo=False):
            continue
        chave = _dig(i.chave)
        if chave:
            achados[chave] = {"id_documento": id_doc, "tp_evento": i.tp_evento,
                              "seq": i.seq_evento, "orgao": i.orgao}
    return achados


def situacao_das_operacoes(operacoes, cancelamentos: dict):
    """Aplica os cancelamentos conhecidos e CONTA o que ficou sem evento.

    O que este código deliberadamente **não** faz: marcar como `NORMAL` quem
    não tem evento. Não ter evento é não saber, e não saber tem que aparecer
    no relatório em vez de virar um "ativa" implícito que ninguém conferiu.
    """
    saida, rel = [], RelatorioSituacao()
    canceladas = []
    for op in operacoes or ():
        chave = _dig(op.origem.chave or "")
        if chave and chave in cancelamentos:
            saida.append(nz.com_situacao(op, nz.CANCELADA))
            rel.canceladas += 1
            canceladas.append(chave)
            continue
        rel.sem_evento_conhecido += 1
        saida.append(op)
    rel.chaves_canceladas = tuple(canceladas)
    return tuple(saida), rel


# ════════════════════════════════════════════════════════════════════════════
#  Painel operacional — o que o contador vê depois de soltar uma pasta
# ════════════════════════════════════════════════════════════════════════════
def painel(dados_dir, identidade, arquivos=None, *, pasta=None,
           recebidos: int | None = None, ambiente=PRODUCAO) -> dict:
    """Importa e devolve o quadro completo do lote, pronto para a tela.

    NÃO É UM SEGUNDO FLUXO. É `importar()` — o mesmo portão, o mesmo acervo, o
    mesmo índice — mais uma leitura do que acabou de entrar, feita pelos
    módulos que já existem: `conferencia.operacoes_nfe` normaliza e `vendas`
    classifica. Nenhuma regra nova mora aqui.

    O recorte é o LOTE, não a empresa inteira. Quem soltou uma pasta quer saber
    o que aquela pasta produziu; misturar com o histórico esconderia justamente
    o que ele precisa conferir.

    **Conferência, não apuração.** `receita_venda` sai daqui para ser olhada por
    um humano. Nada deste retorno alimenta DAS, PIS, COFINS, IRPJ ou CSLL.
    """
    from . import conferencia as cf
    from . import vendas as vd

    if pasta is not None:
        rel = importar_pasta(dados_dir, identidade, pasta, ambiente=ambiente)
    else:
        rel = importar(dados_dir, identidade, arquivos or [], ambiente=ambiente)

    ident = normalizar(identidade)
    alvo = ident.valor if ident.valido else str(identidade)

    # Só os documentos DESTE lote — inclusive os que já existiam e vieram como
    # duplicata: o contador acabou de entregá-los, e sumir com eles do quadro
    # faria parecer que o arquivo não foi lido.
    do_lote = {v.id_documento for v in rel.vereditos
               if v.aceito and v.papel != EVENTO}
    operacoes = [op for op, falha in cf.operacoes_nfe(dados_dir, alvo)
                 if op is not None and op.id_documento in do_lote]

    cancelados = cancelamentos_no_acervo(dados_dir, alvo)
    operacoes, _ = situacao_das_operacoes(operacoes, cancelados)
    receita = vd.receita_de_vendas(operacoes, alvo)
    z = receita.resumo()

    # As contagens de situação valem sobre O QUE A TELA MOSTRA — as saídas que
    # `vendas` classificou —, não sobre todas as NF-e do lote. Contar a compra
    # de terceiro junto fazia `canceladas + sem_evento` dar mais que o número
    # de linhas da tabela, e ninguém consegue conferir uma soma que não fecha.
    # (Uma compra sem evento de cancelamento também não diz nada sobre receita.)
    canceladas_por_evento = sum(1 for o in receita.operacoes
                                if _dig(o.chave) in cancelados)
    sem_evento = len(receita.operacoes) - canceladas_por_evento
    # `fora_da_receita` NÃO é sinônimo de cancelada: inclui denegada e sem
    # protocolo. Eram duas ideias com o mesmo nome na mesma tela.
    fora_da_receita = sum(1 for o in receita.operacoes if o.cancelada)

    def _n(nat):
        return z["por_natureza"].get(nat, {}).get("documentos", 0)

    quadro = {
        "empresa": ident.mascarado(),
        # ── o que entrou ────────────────────────────────────────────────
        # Quantos arquivos o usuário entregou — não quantos chegaram ao portão.
        # Na caixa de entrada mista, NFS-e e CT-e têm destino próprio e não
        # passam por aqui; contá-los como "não encontrados" seria mentir, e
        # omiti-los do total também.
        "xml_encontrados": len(rel.vereditos) if recebidos is None else recebidos,
        "xml_avaliados_pelo_portao": len(rel.vereditos),
        "xml_de_outro_destino": max(0, (recebidos or len(rel.vereditos))
                                    - len(rel.vereditos)),
        "nfe_proprias_validas": sum(1 for v in rel.vereditos if v.pode_ser_receita),
        "terceiros": sum(1 for v in rel.vereditos
                         if v.aceito and v.papel == TERCEIRO),
        "duplicados": rel.duplicados,
        "rejeitados": rel.rejeitados,
        "rejeitados_por_motivo": [
            {"motivo": m, "quantidade": q, "explicacao": TEXTO_DO_MOTIVO.get(m, m)}
            for m, q in rel.por_motivo().items()],
        "sem_protocolo": sum(1 for v in rel.vereditos
                             if v.impedimento_receita == SEM_PROTOCOLO),
        "eventos_importados": sum(1 for v in rel.vereditos
                                  if v.aceito and v.papel == EVENTO),
        # ── o que o acervo fez ──────────────────────────────────────────
        "preservados": rel.preservados,
        "indexados": rel.indexados,
        "indice_em_dia": rel.indice_em_dia,
        # ── como o lote se classifica ───────────────────────────────────
        "vendas": _n(vd.VENDA),
        "transferencias": _n(vd.TRANSFERENCIA),
        "devolucoes": _n(vd.DEVOLUCAO),
        "nao_receita": _n(vd.NAO_RECEITA),
        "indeterminadas": _n(vd.INDETERMINADA),
        "canceladas": canceladas_por_evento,
        "fora_da_receita": fora_da_receita,
        "sem_evento_conhecido": sem_evento,
        "advertencia_evento": RelatorioSituacao().resumo()["advertencia"],
        # ── só para conferência ─────────────────────────────────────────
        "receita_venda": z["receita_venda"],
        "receita_por_competencia": [
            {"competencia": c, "venda": str(v["venda"]),
             "transferencia": str(v["transferencia"]),
             "documentos": len(v["documentos_venda"])}
            for c, v in sorted(receita.por_competencia.items())],
        "apenas_conferencia": (
            "estes valores NÃO alimentam DAS, PIS, COFINS, IRPJ nem CSLL"),
        # ── rastreabilidade: do número ao XML ───────────────────────────
        "documentos": [
            {"id_documento": o.id_documento,
             "chave_final": (o.chave or "")[-8:],
             "natureza": o.natureza,
             "cfops": list(o.cfops),
             "valor": str(o.valor) if o.valor is not None else None,
             "cancelada": o.cancelada,
             "motivo": o.motivo or None,
             "caminho": o.caminho}
            for o in receita.operacoes],
        "arquivos": [v.para_json() for v in rel.vereditos],
    }
    return quadro
