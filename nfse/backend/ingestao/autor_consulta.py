"""
autor_consulta.py — quem PERGUNTA ao fisco. Fonte única do `cUFAutor`.

O ERRO QUE ESTE MÓDULO EXISTE PARA IMPEDIR
    `cUFAutor` é a UF do **autor da consulta** — o escritório que pergunta. Não
    é a UF da empresa consultada.

    A primeira versão do conector derivava `cUFAutor` de `empresa.uf`. Funciona
    por acidente enquanto todos os clientes são do mesmo estado do escritório, e
    passa a mandar o valor errado no dia em que um cliente de outra UF for
    consultado — sem nenhum sintoma local, porque quem recusa é a SEFAZ, com uma
    rejeição genérica.

    Foi flagrado antes da primeira consulta da ING 3C: a empresa candidata não
    tinha UF cadastrada, e o sistema abortaria — expondo que o valor vinha do
    lugar errado.

DE ONDE VEM A VERDADE

        <raiz de dados>/autor_consulta.json
        {"uf": "PE"}

    Um arquivo, uma linha, um lugar. `cUFAutor` é derivado da UF pela tabela do
    IBGE, que também mora aqui — para não haver um `26` solto no meio do código
    de conector, sem semântica e impossível de achar quando o escritório mudar
    de estado ou abrir filial.

AUSÊNCIA NÃO TEM PADRÃO
    Sem o arquivo, `carregar()` levanta `AutorNaoConfigurado`. **Não existe
    fallback para "26"**: um padrão silencioso é como o valor errado sobrevive a
    uma mudança de escritório. Falhar antes da rede é barato; descobrir depois
    de uma rejeição inexplicada, não.

OVERRIDE
    Existe, e é explícito: `cuf=` nas funções do conector, alimentado pelo
    `--cuf` da linha de comando. Serve para diagnóstico e teste. A execução
    normal **não** depende de o operador lembrar do parâmetro.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

ARQUIVO = "autor_consulta.json"

# Código de UF do IBGE. Fonte única no projeto — ver o cabeçalho sobre por que
# não pode haver um "26" solto em módulo de conector.
CUF_POR_UF = {
    "RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16",
    "TO": "17", "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25",
    "PE": "26", "AL": "27", "SE": "28", "BA": "29", "MG": "31", "ES": "32",
    "RJ": "33", "SP": "35", "PR": "41", "SC": "42", "RS": "43", "MS": "50",
    "MT": "51", "GO": "52", "DF": "53",
}
UF_POR_CUF = {v: k for k, v in CUF_POR_UF.items()}

ORIGEM_ARQUIVO = "autor_consulta.json"
ORIGEM_OVERRIDE = "override explícito"

_SO_DIGITOS = re.compile(r"\D")


class ErroAutor(Exception):
    """Base."""


class AutorNaoConfigurado(ErroAutor):
    """Não há UF de autor definida. A consulta **não pode** sair assim."""


class AutorInvalido(ErroAutor):
    """A UF configurada não existe, ou o cUFAutor não é um código válido."""


@dataclass(frozen=True)
class AutorConsulta:
    """Quem assina a pergunta ao fisco."""
    uf: str
    cuf: str
    origem: str = ORIGEM_ARQUIVO

    def resumo(self) -> dict:
        return {"uf_do_autor": self.uf, "cUFAutor": self.cuf,
                "origem": self.origem}


def _validar_uf(uf) -> str:
    u = str(uf or "").strip().upper()
    if not u:
        raise AutorNaoConfigurado("UF do autor da consulta não informada")
    if u not in CUF_POR_UF:
        raise AutorInvalido(f"UF do autor desconhecida: {u!r}")
    return u


def de_uf(uf, origem: str = ORIGEM_ARQUIVO) -> AutorConsulta:
    """Monta o autor a partir da sigla da UF."""
    u = _validar_uf(uf)
    return AutorConsulta(uf=u, cuf=CUF_POR_UF[u], origem=origem)


def de_cuf(cuf, origem: str = ORIGEM_OVERRIDE) -> AutorConsulta:
    """Monta o autor a partir do código numérico — é o caminho do `--cuf`."""
    c = _SO_DIGITOS.sub("", str(cuf or ""))
    if not c:
        raise AutorInvalido(f"cUFAutor vazio ou não numérico: {cuf!r}")
    c = c.zfill(2)
    if c not in UF_POR_CUF:
        raise AutorInvalido(f"cUFAutor não é um código de UF do IBGE: {c!r}")
    return AutorConsulta(uf=UF_POR_CUF[c], cuf=c, origem=origem)


def caminho(dados_dir) -> Path:
    return Path(dados_dir) / ARQUIVO


def carregar(dados_dir) -> AutorConsulta:
    """Lê o autor configurado. **Levanta** se não houver — não há padrão."""
    p = caminho(dados_dir)
    if not p.exists():
        raise AutorNaoConfigurado(
            f"{ARQUIVO} não existe em {Path(dados_dir)}; configure a UF do "
            f"autor das consultas antes de falar com o fisco")
    try:
        d = json.loads(p.read_text("utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise AutorInvalido(f"{ARQUIVO} ilegível ({type(exc).__name__})") from None
    if not isinstance(d, dict):
        raise AutorInvalido(f"{ARQUIVO} não contém um objeto JSON")

    if d.get("uf"):
        autor = de_uf(d["uf"])
    elif d.get("cuf") or d.get("cUFAutor"):
        autor = de_cuf(d.get("cuf") or d.get("cUFAutor"))
    else:
        raise AutorNaoConfigurado(f"{ARQUIVO} não define 'uf' nem 'cuf'")

    # Se o arquivo trouxer os dois, precisam concordar: divergência silenciosa
    # aqui manda o código de um estado com o nome de outro.
    if d.get("uf") and (d.get("cuf") or d.get("cUFAutor")):
        pedido = _SO_DIGITOS.sub("", str(d.get("cuf") or d.get("cUFAutor"))).zfill(2)
        if pedido != autor.cuf:
            raise AutorInvalido(
                f"{ARQUIVO} é contraditório: uf={autor.uf} corresponde a "
                f"cUFAutor {autor.cuf}, mas o arquivo diz {pedido}")
    return autor


def resolver(dados_dir=None, cuf_override: str = "",
             autor: AutorConsulta | None = None) -> AutorConsulta:
    """O autor efetivo desta consulta, na ordem de precedência:

    1. `cuf_override` — o `--cuf` explícito, para diagnóstico;
    2. `autor` já montado, quando quem chama tem um;
    3. o arquivo de configuração.

    **A UF da empresa consultada não entra nesta lista**, e é esse o ponto.
    """
    if cuf_override:
        return de_cuf(cuf_override)
    if autor is not None:
        return autor
    if dados_dir is None:
        raise AutorNaoConfigurado(
            "sem raiz de dados e sem override: não há de onde tirar o cUFAutor")
    return carregar(dados_dir)


def gravar(dados_dir, uf) -> AutorConsulta:
    """Escreve a configuração. Usado uma vez, na instalação."""
    autor = de_uf(uf)
    p = caminho(dados_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "uf": autor.uf,
        "cuf": autor.cuf,
        "_comentario": ("UF do AUTOR das consultas ao fisco — o escritório, "
                        "não a empresa consultada. Usado como cUFAutor."),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return autor
