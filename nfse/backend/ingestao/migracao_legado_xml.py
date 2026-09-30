"""
migracao_legado_xml.py — os XML das pastas antigas entram no acervo.

O QUE ESTA MIGRAÇÃO É
    Uma **cópia controlada**. Os XML que o `nfe.py` gravou soltos em
    `<cnpj>/nfe/` e `<cnpj>/nfe_importadas/` passam pelo mesmo portão de sempre
    (`importacao.py`) e viram documentos do acervo imutável, indexados.

    LEGADO → ACERVO IMUTÁVEL → ÍNDICE. Nunca legado → índice: o XML original é
    evidência fiscal, o índice é projeção reconstruível. Gravar direto no índice
    produziria um número sem documento por trás — e é o documento que vale
    numa fiscalização.

O QUE ELA NUNCA FAZ
    **Não apaga, não move, não renomeia e não altera o legado.** Termina a
    migração e as 23.450 pastas continuam exatamente onde estavam, byte a byte.
    Tirar o leitor legado é outra decisão, de outra fase.

    **Não fala com a SEFAZ.** Não consulta, não avança `ultNSU`, não encosta em
    checkpoint, em política de 656 nem em scheduler. É inteiramente offline —
    e há teste que prova cada uma dessas ausências.

    **Não enriquece nada.** Não separa CST de CSOSN, não acrescenta CEST, origem
    ou tributo por item. Isso é a NF-e 4. Aqui o objetivo é **cobertura**, e
    misturar as duas coisas tornaria impossível dizer qual mudança causou qual
    diferença de número.

POR QUE PASSA PELO PORTÃO, E NÃO GRAVA DIRETO
    `importacao.py` já sabe validar chave, conferir modelo, recusar denegada,
    decidir o papel da empresa, deduplicar por hash, promover resumo a completo
    e reconciliar o índice. Um segundo caminho de escrita teria de reimplementar
    tudo isso — e divergiria no primeiro caso difícil.

    A única coisa que este módulo acrescenta ao portão é a **procedência**:
    o documento entra marcado como `LEGADO_NFE`, e não como importação manual.

IDEMPOTÊNCIA NÃO É PROMESSA, É CONSEQUÊNCIA
    O acervo é endereçado por conteúdo e deduplica por hash. Rodar duas vezes
    devolve `DUPLICATA` na segunda e não escreve byte nenhum. Não há contador
    de "já migrei" para se perder ou dessincronizar.

UMA EMPRESA RUIM NÃO DERRUBA AS OUTRAS
    O laço é por empresa e cada uma tem seu relatório. Exceção numa vira erro
    registrado, não interrupção da carteira.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import importacao as imp
from . import indice as idx
from .ambiente import PRODUCAO
from .identidade import normalizar

# Procedência gravada no `captura.json`. Responde, meses depois, "de onde veio
# este documento?" sem depender da memória de ninguém.
FONTE_LEGADO = "LEGADO_NFE"

# As pastas que o `nfe.py` usava. Não há descoberta automática de propósito:
# uma varredura genérica por `*.xml` acabaria arrastando o acervo de volta para
# dentro de si mesmo.
PASTAS_LEGADAS = ("nfe", "nfe_importadas")

# ── veredito de cada arquivo ────────────────────────────────────────────────
MIGRARIA = "MIGRARIA"
JA_EXISTE = "JA_EXISTE"
PROMOVERIA_RESUMO = "PROMOVERIA_RESUMO"
COPIA_MENOR = "COPIA_MENOR"
EVENTO = "EVENTO"
DUPLICADO = "DUPLICADO"
XML_INVALIDO = "XML_INVALIDO"
TIPO_NAO_SUPORTADO = "TIPO_NAO_SUPORTADO"
EMPRESA_NAO_IDENTIFICADA = "EMPRESA_NAO_IDENTIFICADA"
CONFLITO = "CONFLITO"
CONFLITO_SO_FORMATACAO = "CONFLITO_SO_FORMATACAO"
ERRO = "ERRO"

VEREDITOS = (MIGRARIA, JA_EXISTE, PROMOVERIA_RESUMO, COPIA_MENOR, EVENTO,
             DUPLICADO, XML_INVALIDO, TIPO_NAO_SUPORTADO,
             EMPRESA_NAO_IDENTIFICADA, CONFLITO, CONFLITO_SO_FORMATACAO, ERRO)

# Veredito que EXIGE decisão humana antes de a migração real rodar.
# `CONFLITO_SO_FORMATACAO` fica FORA: é conflito diagnosticado, não conflito em
# aberto — ver `_so_muda_serializacao`.
BLOQUEANTES = (CONFLITO, ERRO)

# Como os motivos do portão viram veredito desta migração. O mapa é explícito
# para que nenhum motivo novo do portão caia em "DESCONHECIDO" por omissão.
_MOTIVO_PARA_VEREDITO = {
    imp.ILEGIVEL: XML_INVALIDO,
    imp.CHAVE_AUSENTE: XML_INVALIDO,
    imp.CHAVE_INVALIDA: XML_INVALIDO,
    imp.CHAVE_DIVERGENTE: XML_INVALIDO,
    imp.ESPECIE_NAO_SUPORTADA: TIPO_NAO_SUPORTADO,
    imp.MODELO_NAO_SUPORTADO: TIPO_NAO_SUPORTADO,
    imp.DENEGADA: TIPO_NAO_SUPORTADO,
    imp.EMPRESA_AUSENTE: EMPRESA_NAO_IDENTIFICADA,
    imp.FALHA_AO_PRESERVAR: ERRO,
}

# E como o resultado do acervo vira veredito, depois da gravação real.
from . import acervo as acv  # noqa: E402

_RESULTADO_PARA_VEREDITO = {
    acv.NOVO: MIGRARIA,
    acv.DUPLICATA: JA_EXISTE,
    acv.COPIA_PROMOVIDA: PROMOVERIA_RESUMO,
    acv.COPIA_MENOR: COPIA_MENOR,
    acv.COLISAO: CONFLITO,
}


def _so_muda_serializacao(a: bytes, b: bytes) -> bool:
    """Os dois XML dizem exatamente a mesma coisa, escrita de outro jeito?

    POR QUE ESTA PERGUNTA PRECISOU EXISTIR
        O ensaio da NF-e 3 acusou 17 `CONFLITO` — mesma chave, mesma
        prioridade, bytes diferentes. Investigados um a um, a diferença era
        `<Transform Algorithm="…" />` contra `<Transform Algorithm="…"/>`:
        **um espaço antes da barra**, dentro do bloco `<Signature>`. As árvores
        XML são idênticas, campo a campo.

        São a mesma nota chegando por dois caminhos — o `docZip` da
        distribuição e o arquivo que alguém importou do cliente — serializados
        por bibliotecas diferentes.

    O QUE ISTO **NÃO** MUDA
        A regra de colisão do acervo continua exatamente como está, e é boa:
        mesma identidade com bytes diferentes vira quarentena e espera gente.
        Aqui a migração apenas **classifica** antes de gravar, e pula o que já
        está preservado com conteúdo equivalente. Gravar a segunda cópia não
        acrescentaria informação fiscal nenhuma e criaria 17 quarentenas que
        alguém teria de revisar para concluir o que já se sabe.

    O QUE ELA NÃO FAZ
        Não normaliza, não reescreve, não escolhe um "vencedor" e não toca no
        acervo. Só responde sim ou não. Diferença que **não** for de
        serialização continua `CONFLITO`, continua bloqueante.
    """
    import xml.etree.ElementTree as ET

    def arvore(x: bytes):
        raiz = ET.fromstring(x)
        return [(e.tag, (e.text or "").strip(), tuple(sorted(e.attrib.items())))
                for e in raiz.iter()]

    try:
        return arvore(a) == arvore(b)
    except Exception:
        # XML que não abre não é "igual a nada": é conflito de verdade.
        return False


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ════════════════════════════════════════════════════════════════════════════
#  Inventário
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class InventarioEmpresa:
    identidade: str
    arquivos: int = 0
    bytes: int = 0
    por_pasta: dict = field(default_factory=dict)
    por_tipo: dict = field(default_factory=dict)
    documentos_no_acervo: int = 0
    documentos_no_indice: int = 0
    checkpoint: dict = field(default_factory=dict)

    def resumo(self) -> dict:
        return {"identidade": self.identidade[:8] + "***",
                "arquivos_legados": self.arquivos, "bytes": self.bytes,
                "por_pasta": self.por_pasta, "por_tipo": self.por_tipo,
                "documentos_no_acervo": self.documentos_no_acervo,
                "documentos_no_indice": self.documentos_no_indice,
                "checkpoint": self.checkpoint}


def _tipo_do_arquivo(nome: str) -> str:
    """Pelo sufixo que o `nfe.py` usava ao gravar. É rótulo, não decisão."""
    for marca in ("procNFe", "resNFe", "procEventoNFe", "resEvento"):
        if marca in nome:
            return marca
    return "outro"


def arquivos_legados(dados_dir, identidade) -> list[Path]:
    """Todos os XML das pastas legadas desta empresa, em ordem estável.

    A ordem importa: `procNFe` **depois** de `resNFe` faria o acervo receber o
    resumo primeiro e promover em seguida, gerando movimento desnecessário. Aqui
    o completo vai primeiro, e o resumo que chegar depois é reconhecido como
    cópia menor — que o acervo guarda sem rebaixar o canônico.
    """
    ident = normalizar(identidade)
    if not ident.valido:
        return []
    saida: list[Path] = []
    for pasta in PASTAS_LEGADAS:
        p = Path(dados_dir) / ident.valor / pasta
        if p.exists():
            saida.extend(sorted(x for x in p.rglob("*.xml") if x.is_file()))
    # completos primeiro, resumos depois, eventos por último
    ordem = {"procNFe": 0, "procEventoNFe": 1, "resNFe": 2, "resEvento": 3,
             "outro": 4}
    return sorted(saida, key=lambda x: (ordem[_tipo_do_arquivo(x.name)], x.name))


def inventariar(dados_dir, identidade) -> InventarioEmpresa:
    """Fotografia do estado ANTES de qualquer escrita. Só lê."""
    ident = normalizar(identidade)
    inv = InventarioEmpresa(identidade=ident.valor if ident.valido
                            else str(identidade))
    if not ident.valido:
        return inv

    raiz = Path(dados_dir) / ident.valor
    for pasta in PASTAS_LEGADAS:
        p = raiz / pasta
        if not p.exists():
            continue
        n = 0
        for x in p.rglob("*.xml"):
            if not x.is_file():
                continue
            n += 1
            inv.arquivos += 1
            inv.bytes += x.stat().st_size
            t = _tipo_do_arquivo(x.name)
            inv.por_tipo[t] = inv.por_tipo.get(t, 0) + 1
        inv.por_pasta[pasta] = n

    acervo = raiz / "acervo"
    if acervo.exists():
        inv.documentos_no_acervo = sum(1 for _ in acervo.rglob("original.xml"))

    banco = raiz / idx.PASTA / idx.ARQUIVO
    if banco.exists():
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{banco}?mode=ro", uri=True)
            inv.documentos_no_indice = int(
                con.execute("SELECT COUNT(*) FROM documentos").fetchone()[0])
            con.close()
        except Exception:                                    # pragma: no cover
            inv.documentos_no_indice = -1

    # O checkpoint entra no inventário para poder ser CONFERIDO depois. A
    # migração não o toca; o inventário é a prova de que não tocou.
    for arq in sorted((raiz / "ingestao").glob("*.json")) \
            if (raiz / "ingestao").exists() else []:
        try:
            inv.checkpoint[arq.name] = hashlib.sha256(arq.read_bytes()).hexdigest()
        except Exception:                                    # pragma: no cover
            inv.checkpoint[arq.name] = "ilegivel"
    return inv


def inventariar_carteira(dados_dir, identidades=None) -> dict:
    raiz = Path(dados_dir)
    if identidades is None:
        identidades = sorted(empresas_com_legado(raiz))
    invs = [inventariar(raiz, i) for i in identidades]
    return {
        "gerado_em": _agora(),
        "empresas": [i.resumo() for i in invs],
        "total_arquivos": sum(i.arquivos for i in invs),
        "total_bytes": sum(i.bytes for i in invs),
        "total_no_acervo": sum(i.documentos_no_acervo for i in invs),
        "total_no_indice": sum(i.documentos_no_indice for i in invs
                               if i.documentos_no_indice > 0),
        "inventarios": invs,
    }


def empresas_com_legado(dados_dir) -> list[str]:
    """Quem tem pasta legada. Derivado do disco, e é o certo aqui: a pergunta
    é literalmente 'quais pastas existem para migrar'."""
    raiz = Path(dados_dir)
    achadas = set()
    for pasta in PASTAS_LEGADAS:
        for p in raiz.glob(f"*/{pasta}"):
            if p.is_dir() and any(p.rglob("*.xml")):
                achadas.add(p.parent.name)
    return sorted(achadas)


# ════════════════════════════════════════════════════════════════════════════
#  Backup / manifesto pré-migração
# ════════════════════════════════════════════════════════════════════════════
def manifesto_pre_migracao(dados_dir, destino=None, identidades=None,
                           com_hash_por_arquivo: bool = True) -> dict:
    """Prova documental do estado ANTES da migração.

    Guarda o **hash de cada XML legado**, não os arquivos: os originais não
    saem do lugar (a migração é por cópia), então copiá-los duplicaria dezenas
    de milhares de arquivos sem acrescentar garantia. O que o manifesto precisa
    provar é que nenhum deles mudou — e hash prova isso.

    Não entra certificado, senha, `.pfx` nem nada de `certs/`: o manifesto
    descreve XML fiscal, e segredo que não viaja não vaza.
    """
    raiz = Path(dados_dir)
    destino = Path(destino) if destino else Path.home() / "Fiscale-backups"
    destino.mkdir(parents=True, exist_ok=True)

    inv = inventariar_carteira(raiz, identidades)
    corpo = {
        "marca": "fiscale-pre-nfe3-v1",
        "gerado_em": inv["gerado_em"],
        "origem": str(raiz),
        "motivo": "estado anterior à migração dos XML legados (NF-e 3)",
        "total_arquivos": inv["total_arquivos"],
        "total_bytes": inv["total_bytes"],
        "total_no_acervo": inv["total_no_acervo"],
        "total_no_indice": inv["total_no_indice"],
        "empresas": inv["empresas"],
    }
    if com_hash_por_arquivo:
        arquivos = {}
        for i in inv["inventarios"]:
            for x in arquivos_legados(raiz, i.identidade):
                rel = x.relative_to(raiz).as_posix()
                arquivos[rel] = {
                    "bytes": x.stat().st_size,
                    "sha256": hashlib.sha256(x.read_bytes()).hexdigest(),
                }
        corpo["arquivos"] = arquivos

    bruto = json.dumps(corpo, ensure_ascii=False, indent=1).encode("utf-8")
    sha = hashlib.sha256(bruto).hexdigest()
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = destino / f"pre_nfe3_{carimbo}.manifesto.json"
    caminho.write_bytes(bruto)
    (destino / f"pre_nfe3_{carimbo}.sha256").write_text(
        f"{sha}  {caminho.name}\n", encoding="utf-8")

    return {"arquivo": str(caminho), "sha256": sha, "bytes": len(bruto),
            "arquivos_catalogados": len(corpo.get("arquivos") or {}),
            "gerado_em": corpo["gerado_em"], "resumo": {
                k: corpo[k] for k in ("total_arquivos", "total_bytes",
                                      "total_no_acervo", "total_no_indice")}}


def conferir_manifesto(dados_dir, caminho) -> dict:
    """Nenhum XML legado mudou desde o manifesto? Só lê."""
    raiz = Path(dados_dir)
    corpo = json.loads(Path(caminho).read_text("utf-8"))
    esperados = corpo.get("arquivos") or {}
    alterados, sumidos = [], []
    for rel, info in esperados.items():
        p = raiz / rel
        if not p.exists():
            sumidos.append(rel)
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() != info["sha256"]:
            alterados.append(rel)
    return {"conferidos": len(esperados), "alterados": alterados,
            "sumidos": sumidos,
            "intacto": not alterados and not sumidos}


# ════════════════════════════════════════════════════════════════════════════
#  Migração (dry-run e real usam o MESMO caminho de decisão)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class ItemMigracao:
    arquivo: str
    veredito: str
    chave: str = ""
    papel: str = ""
    detalhe: str = ""
    id_documento: str = ""

    def para_json(self) -> dict:
        return {"arquivo": self.arquivo, "veredito": self.veredito,
                "chave": self.chave, "papel": self.papel,
                "detalhe": self.detalhe, "id_documento": self.id_documento}


@dataclass
class RelatorioMigracao:
    identidade: str
    ensaio: bool
    arquivos: int = 0
    itens: list[ItemMigracao] = field(default_factory=list)
    indexados: int = 0
    indice_em_dia: bool = False
    quarentena: int = 0
    erro: str = ""
    segundos: float = 0.0

    @property
    def por_veredito(self) -> dict:
        saida: dict[str, int] = {}
        for i in self.itens:
            saida[i.veredito] = saida.get(i.veredito, 0) + 1
        return dict(sorted(saida.items(), key=lambda kv: -kv[1]))

    @property
    def bloqueantes(self) -> list[ItemMigracao]:
        return [i for i in self.itens if i.veredito in BLOQUEANTES]

    @property
    def explicados(self) -> bool:
        """Todo arquivo tem um veredito nomeado. Nada cai em vala comum."""
        return all(i.veredito in VEREDITOS for i in self.itens)

    def resumo(self) -> dict:
        return {"identidade": self.identidade[:8] + "***",
                "ensaio": self.ensaio, "arquivos": self.arquivos,
                "por_veredito": self.por_veredito,
                "bloqueantes": len(self.bloqueantes),
                "indexados": self.indexados,
                "indice_em_dia": self.indice_em_dia,
                "quarentena": self.quarentena,
                "todos_explicados": self.explicados,
                "segundos": round(self.segundos, 1), "erro": self.erro}


def _veredito_do_ensaio(ac, conteudo: bytes, identidade: str,
                        simulado: dict | None = None) -> ItemMigracao:
    """O que ACONTECERIA, sem escrever byte nenhum.

    Usa exatamente as mesmas funções que a migração real usa para decidir
    (`importacao.avaliar` e a identificação do acervo). Um ensaio que decidisse
    por outro caminho não valeria como ensaio.

    `simulado` carrega o EFEITO ACUMULADO do próprio ensaio: `{id_documento:
    prioridade que já teria sido gravada}`.

    Sem ele o ensaio mente numa situação que é comum no legado: a mesma nota
    tem `procNFe` **e** `resNFe` na pasta. Consultando só o disco, os dois
    respondem "MIGRARIA", porque no início o acervo está vazio. Na execução
    real o completo entra primeiro e o resumo vira `COPIA_MENOR` — e o ensaio
    teria prometido duas migrações onde há uma. Foi um teste desta suíte que
    flagrou a diferença.
    """
    from . import identificacao as idf

    motivo, detalhe, papel, chave, _imp = imp.avaliar(conteudo, identidade)
    if motivo:
        return ItemMigracao("", _MOTIVO_PARA_VEREDITO.get(motivo, ERRO),
                            chave=chave, detalhe=detalhe or motivo)
    veredito_base = EVENTO if papel == imp.EVENTO else ""

    # A identificação decide a espécie sozinha, pelo modelo NA CHAVE — é ela a
    # autoridade, não quem chama. Passar espécie por fora seria a porta para
    # gravar um cupom sob o rótulo de NF-e (o defeito que a APURAÇÃO 6D
    # fechou), então o ensaio usa exatamente a mesma chamada que `preservar()`.
    ident = idf.identificar(conteudo, chave_informada=chave)

    # `simulado[id] = (prioridade já gravada, hashes já vistos)`. O hash entra
    # porque prioridade sozinha não distingue duplicata de colisão: dois
    # documentos com a mesma prioridade e conteúdos diferentes são conflito,
    # não repetição — e tratá-los como repetição faria o ensaio prometer
    # silêncio onde a execução real levantaria a mão.
    anterior = (simulado or {}).get(ident.id_documento)
    if anterior is None:
        resultado = ac.simular(ident)
    else:
        prio_ja, hashes_ja = anterior
        if ident.hash_conteudo in hashes_ja:
            resultado = acv.DUPLICATA
        elif ident.prioridade > prio_ja:
            resultado = acv.COPIA_PROMOVIDA
        elif ident.prioridade < prio_ja:
            resultado = acv.COPIA_MENOR
        else:
            resultado = acv.COLISAO
    if simulado is not None:
        prio_ja, hashes_ja = anterior or (0, set())
        simulado[ident.id_documento] = (max(ident.prioridade, prio_ja),
                                        hashes_ja | {ident.hash_conteudo})

    veredito = _RESULTADO_PARA_VEREDITO.get(resultado, ERRO)
    if veredito == CONFLITO and _conflito_e_so_formatacao(ac, ident, conteudo):
        veredito = CONFLITO_SO_FORMATACAO
    # Evento também é documento do acervo; o rótulo EVENTO só se sobrepõe
    # quando ele seria NOVO — para o relatório poder separar nota de evento
    # sem esconder duplicata nem conflito.
    if veredito_base == EVENTO and veredito == MIGRARIA:
        veredito = EVENTO
    return ItemMigracao("", veredito, chave=chave, papel=papel,
                        id_documento=ident.id_documento)


# Quantos arquivos por chamada do portão. `importar()` reconcilia o índice ao
# terminar, e reconciliar 20.585 vezes levaria horas — medido: 10% em 20 min.
# Em lote, a reconciliação acontece uma vez a cada `TAMANHO_DO_LOTE` e mais uma
# no fim da empresa. O estado final é o mesmo porque `indexar_ausentes` é
# idempotente; muda só quantas vezes se pergunta "falta indexar alguma coisa?".
TAMANHO_DO_LOTE = 500


def _despejar(dados_dir, identidade, lote, ambiente, rel) -> None:
    """Passa um lote pelo portão e traduz cada veredito."""
    r = imp.importar(dados_dir, identidade, lote, ambiente=ambiente,
                     fonte=FONTE_LEGADO)
    for v in r.vereditos:
        if not v.aceito:
            rel.itens.append(ItemMigracao(
                v.arquivo, _MOTIVO_PARA_VEREDITO.get(v.motivo, ERRO),
                chave=v.chave, detalhe=v.detalhe or v.motivo))
            continue
        veredito = _RESULTADO_PARA_VEREDITO.get(v.resultado_acervo, ERRO)
        if v.papel == imp.EVENTO and veredito == MIGRARIA:
            veredito = EVENTO
        rel.itens.append(ItemMigracao(
            v.arquivo, veredito, chave=v.chave, papel=v.papel,
            id_documento=v.id_documento,
            detalhe="quarentena" if v.quarentena else ""))
        if v.quarentena:
            rel.quarentena += 1


def _conflito_e_so_formatacao(ac, ident, conteudo: bytes) -> bool:
    """O que já está no acervo diz o mesmo que este arquivo?"""
    from .acervo import ORIGINAL
    try:
        canonico = ac.pasta_do(ident.especie, ident.id_documento) / ORIGINAL
        if not canonico.exists():
            return False
        return _so_muda_serializacao(conteudo, canonico.read_bytes())
    except Exception:                                        # pragma: no cover
        return False


# O que fazer quando o ensaio acusa CONFLITO de verdade.
PARAR = "parar"        # padrão: não migra a empresa. Alguém decide primeiro.
PULAR = "pular"        # migra o resto e deixa o conflitante FORA, registrado.


def migrar_empresa(dados_dir, identidade, *, ensaio: bool = True,
                   limite: int | None = None, conflitos: str = PARAR,
                   ambiente=PRODUCAO) -> RelatorioMigracao:
    """Uma empresa. `ensaio=True` decide e relata sem escrever nada.

    `conflitos=PULAR` **não resolve** conflito nenhum — faz o contrário: deixa
    o arquivo conflitante de fora, sem gravar, com o veredito registrado. O
    documento continua intacto na pasta legada e a decisão sobre qual das duas
    versões vale continua sendo de uma pessoa.

    Existe porque parar a migração inteira de 23 mil arquivos por causa de 3
    seria desproporcional — mas migrá-los às cegas seria pior. `PULAR` é a
    terceira opção honesta: seguir com o que não tem dúvida e deixar a dúvida
    visível.
    """
    import time
    inicio = time.perf_counter()

    ident = normalizar(identidade)
    rel = RelatorioMigracao(identidade=ident.valor if ident.valido
                            else str(identidade), ensaio=ensaio)
    if not ident.valido:
        rel.erro = f"identidade inválida: {ident.motivo}"
        return rel

    caminhos = arquivos_legados(dados_dir, ident.valor)
    if limite:
        caminhos = caminhos[:limite]
    rel.arquivos = len(caminhos)
    if not caminhos:
        rel.indice_em_dia = True
        rel.segundos = time.perf_counter() - inicio
        return rel

    vistos: dict[str, str] = {}          # hash do conteúdo -> primeiro arquivo
    lote: list[tuple[str, bytes]] = []
    # Só o ensaio precisa: a execução real vê o efeito no próprio acervo.
    simulado: dict[str, tuple[int, set]] = {}

    try:
        ac = acv.abrir(dados_dir, ident.valor, servico=FONTE_LEGADO,
                       ambiente=ambiente)
        for caminho in caminhos:
            nome = caminho.relative_to(Path(dados_dir) / ident.valor).as_posix()
            try:
                conteudo = caminho.read_bytes()
            except OSError as e:                             # pragma: no cover
                rel.itens.append(ItemMigracao(nome, ERRO, detalhe=str(e)[:120]))
                continue

            # Repetição DENTRO do próprio lote legado: dois arquivos com bytes
            # idênticos (o `nfe.py` gravava por NSU, e o mesmo documento podia
            # chegar em dois NSU). É diferente de "já está no acervo".
            h = hashlib.sha256(conteudo).hexdigest()
            if h in vistos:
                rel.itens.append(ItemMigracao(
                    nome, DUPLICADO,
                    detalhe=f"bytes idênticos a {vistos[h]}"))
                continue
            vistos[h] = nome

            if ensaio:
                item = _veredito_do_ensaio(ac, conteudo, ident.valor, simulado)
                item.arquivo = nome
                rel.itens.append(item)
                continue

            # Conflito real: não se grava sem decisão humana. Com `PULAR`, o
            # arquivo fica de fora e o motivo fica escrito; sem ele, a empresa
            # inteira para (é o `parar_em_bloqueante` de `migrar_carteira`).
            previa_c = _veredito_do_ensaio(ac, conteudo, ident.valor)
            if previa_c.veredito == CONFLITO and conflitos == PULAR:
                previa_c.arquivo = nome
                previa_c.detalhe = ("conflito de identidade NÃO resolvido: "
                                    "deixado fora da migração, e intacto no "
                                    "legado, aguardando decisão")
                rel.itens.append(previa_c)
                continue

            # ── migração real: o portão de sempre, com procedência própria ──
            # Antes de gravar, o mesmo diagnóstico do ensaio: se o acervo já
            # tem este documento e a única diferença é como o XML foi
            # serializado, não há o que preservar — e forçar a gravação criaria
            # uma quarentena para uma pergunta já respondida.
            previa = previa_c
            if previa.veredito == CONFLITO_SO_FORMATACAO:
                previa.arquivo = nome
                previa.detalhe = ("já preservado; difere apenas na serialização "
                                  "do XML (árvores idênticas)")
                rel.itens.append(previa)
                continue

            # UM arquivo por chamada seria correto e inutilizavelmente lento:
            # `importar()` reconcilia o índice inteiro ao terminar, e fazer isso
            # 20.585 vezes levaria horas. O lote preserva vários e a
            # reconciliação acontece **uma vez por empresa**, no fim — que é o
            # mesmo estado final, porque `indexar_ausentes` é idempotente.
            lote.append((nome, conteudo))
            if len(lote) >= TAMANHO_DO_LOTE:
                _despejar(dados_dir, ident.valor, lote, ambiente, rel)
                lote = []

        if lote:
            _despejar(dados_dir, ident.valor, lote, ambiente, rel)
            lote = []
    except Exception as e:                                   # pragma: no cover
        rel.erro = f"{type(e).__name__}: {e}"
        rel.segundos = time.perf_counter() - inicio
        return rel

    if not ensaio:
        try:
            r = idx.indexar_ausentes(dados_dir, ident.valor)
            rel.indexados = r.indexados
            rel.indice_em_dia = idx.abrir(dados_dir, ident.valor).em_dia_com(
                acv.abrir(dados_dir, ident.valor, servico=FONTE_LEGADO,
                          ambiente=ambiente))
        except Exception as e:                               # pragma: no cover
            rel.erro = f"indexação: {type(e).__name__}: {e}"

    rel.segundos = time.perf_counter() - inicio
    return rel


def migrar_carteira(dados_dir, identidades=None, *, ensaio: bool = True,
                    conflitos: str = PARAR,
                    parar_em_bloqueante: bool = True) -> dict:
    """A carteira, empresa por empresa.

    `parar_em_bloqueante` faz o que o nome diz: conflito de identidade ou erro
    interrompem antes da próxima empresa. Numa migração de documento fiscal,
    seguir em frente depois de um conflito é como decidir sozinho qual dos dois
    documentos vale.
    """
    raiz = Path(dados_dir)
    if identidades is None:
        identidades = empresas_com_legado(raiz)

    relatorios: list[RelatorioMigracao] = []
    parou_em = ""
    for i in identidades:
        r = migrar_empresa(raiz, i, ensaio=ensaio, conflitos=conflitos)
        relatorios.append(r)
        # Com `PULAR`, conflito já foi tratado (ficou de fora, registrado) e
        # não é motivo para interromper a carteira; erro continua sendo.
        travou = (r.erro or (r.bloqueantes and conflitos != PULAR))
        if parar_em_bloqueante and travou:
            parou_em = r.identidade
            break

    consolidado: dict = {"ensaio": ensaio, "conflitos": conflitos,
                         "empresas": len(relatorios),
                         "arquivos": sum(r.arquivos for r in relatorios),
                         "por_veredito": {}, "bloqueantes": 0,
                         "quarentena": sum(r.quarentena for r in relatorios),
                         "indexados": sum(r.indexados for r in relatorios),
                         "parou_em": parou_em[:8] + "***" if parou_em else "",
                         "segundos": round(sum(r.segundos for r in relatorios), 1)}
    for r in relatorios:
        consolidado["bloqueantes"] += len(r.bloqueantes)
        for v, n in r.por_veredito.items():
            consolidado["por_veredito"][v] = consolidado["por_veredito"].get(v, 0) + n
    consolidado["por_veredito"] = dict(
        sorted(consolidado["por_veredito"].items(), key=lambda kv: -kv[1]))
    consolidado["todos_explicados"] = all(r.explicados for r in relatorios)

    return {"consolidado": consolidado,
            "empresas": [r.resumo() for r in relatorios],
            "relatorios": relatorios}


# ── linha de comando ────────────────────────────────────────────────────────
#     python -m ingestao.migracao_legado_xml --inventario
#     python -m ingestao.migracao_legado_xml --manifesto
#     python -m ingestao.migracao_legado_xml --dry-run [cnpj...]
#     python -m ingestao.migracao_legado_xml --migrar  [cnpj...]
if __name__ == "__main__":                                    # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    import fiscale_dados as fd

    raiz = os.environ.get("FISCALE_DADOS") or str(fd.raiz())
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    modos = {a for a in sys.argv[1:] if a.startswith("--")}

    if "--inventario" in modos:
        inv = inventariar_carteira(raiz, args or None)
        inv.pop("inventarios", None)
        print(json.dumps(inv, ensure_ascii=False, indent=1))
    elif "--manifesto" in modos:
        print(json.dumps(manifesto_pre_migracao(raiz, identidades=args or None),
                         ensure_ascii=False, indent=1))
    elif "--migrar" in modos:
        r = migrar_carteira(raiz, args or None, ensaio=False)
        print(json.dumps({"consolidado": r["consolidado"],
                          "empresas": r["empresas"]}, ensure_ascii=False, indent=1))
    else:
        r = migrar_carteira(raiz, args or None, ensaio=True)
        print(json.dumps({"consolidado": r["consolidado"],
                          "empresas": r["empresas"]}, ensure_ascii=False, indent=1))
