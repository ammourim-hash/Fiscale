"""
credencial_cte.py — a empresa está pronta para consultar CT-e? E por quê não?

A PERGUNTA QUE ESTE MÓDULO RESPONDE
    Antes de qualquer chamada real, alguém precisa dizer, para cada cliente:
    "dá para consultar agora?" — e, quando não dá, **qual dos motivos** é.

    Sem isso a tela mostra `0 CT-e` para uma empresa sem certificado e para
    uma empresa que nunca foi consultada, como se fossem a mesma coisa. Não
    são: uma precisa de um arquivo, a outra precisa de um clique.

O QUE ELE NUNCA FAZ
    **Não abre `.pfx` e não vê senha.** Nenhuma linha daqui pede frase-senha,
    tenta adivinhar, ou guarda o que quer que seja. A prontidão é aferida pelo
    que o cadastro e o sistema de arquivos já sabem.

    A consequência é honesta e precisa ser dita: **validade não é aferível
    aqui**. Saber se um certificado venceu exige abri-lo, e abri-lo exige a
    senha. Por isso `VENCIDO`, `AINDA_NAO_VALIDO` e `SENHA_PENDENTE` existem como
    estados do vocabulário — para quem tiver a informação preenchê-los — mas
    esta camada só os devolve quando alguém os informa de fora.

    Prometer um estado que não se pode medir seria pior do que não ter o
    estado: a tela diria "válido" sobre um certificado vencido.

A ASSOCIAÇÃO É PELO DOCUMENTO, NUNCA PELO NOME DO ARQUIVO
    Um `.pfx` chamado `MONTE_04103256000185.pfx` pode conter qualquer coisa. O
    vínculo canônico é o CPF/CNPJ **normalizado** do cadastro, que a
    `ingestao/cadastro.py` já reconcilia entre `certificados.json` e
    `state_clientes.json`. O nome do arquivo é rótulo, não evidência.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import cadastro as cad_mod
from .identidade import normalizar

# ── estados, e o que cada um quer dizer ─────────────────────────────────────
PRONTA = "PRONTA"                       # cadastro completo e arquivo no lugar
SEM_CADASTRO = "SEM_CADASTRO"           # nem em certificados nem em clientes
SEM_CERTIFICADO = "SEM_CERTIFICADO"     # empresa existe; credencial não
ARQUIVO_AUSENTE = "ARQUIVO_AUSENTE"     # cadastro aponta para arquivo que sumiu
ARQUIVO_ORFAO = "ARQUIVO_ORFAO"         # `.pfx` em disco sem dono no cadastro
DOCUMENTO_DIVERGENTE = "DOCUMENTO_DIVERGENTE"   # cadastro × certificado
SENHA_PENDENTE = "SENHA_PENDENTE"                 # senha pendente / cofre não abre
VENCIDO = "VENCIDO"
AINDA_NAO_VALIDO = "AINDA_NAO_VALIDO"
VALIDADE_DESCONHECIDA = "VALIDADE_DESCONHECIDA"

ESTADOS = (PRONTA, SEM_CADASTRO, SEM_CERTIFICADO, ARQUIVO_AUSENTE,
           ARQUIVO_ORFAO, DOCUMENTO_DIVERGENTE, SENHA_PENDENTE, VENCIDO,
           AINDA_NAO_VALIDO, VALIDADE_DESCONHECIDA)

# Estados que impedem a consulta. `VALIDADE_DESCONHECIDA` **não** impede: ela é
# o normal enquanto ninguém abriu o certificado, e barrar por ela travaria
# todas as empresas por uma informação que esta camada escolheu não buscar.
IMPEDEM = (SEM_CADASTRO, SEM_CERTIFICADO, ARQUIVO_AUSENTE,
           DOCUMENTO_DIVERGENTE, SENHA_PENDENTE, VENCIDO, AINDA_NAO_VALIDO)

EXPLICACAO = {
    PRONTA: "cadastro completo e arquivo de certificado no lugar",
    SEM_CADASTRO: "não há ficha desta empresa em Clientes nem em Certificados",
    SEM_CERTIFICADO: "a empresa está cadastrada, mas sem certificado vinculado",
    ARQUIVO_AUSENTE: "o cadastro aponta para um arquivo que não está no disco",
    ARQUIVO_ORFAO: "há um .pfx no disco sem empresa correspondente no cadastro",
    DOCUMENTO_DIVERGENTE: "o documento do cadastro não confere com o do certificado",
    SENHA_PENDENTE: "senha pendente ou cofre indisponível: o certificado não abre",
    VENCIDO: "o certificado está fora do prazo de validade",
    AINDA_NAO_VALIDO: "o certificado começa a valer numa data futura",
    VALIDADE_DESCONHECIDA: ("a validade não foi aferida — ela exige abrir o "
                            ".pfx com a senha, o que esta camada não faz"),
}


def _mascarar(d: str) -> str:
    d = normalizar(d).valor or str(d or "")
    return (d[:8] + "***") if len(d) >= 8 else "***"


@dataclass
class Prontidao:
    """A situação de UMA empresa. **Nunca carrega CNPJ inteiro nem caminho.**"""
    identidade_mascarada: str = ""
    nome: str = ""
    estado: str = SEM_CADASTRO
    pode_consultar: bool = False
    motivo: str = ""
    tem_arquivo: bool = False
    tem_senha_guardada: bool = False
    validade_ate: str = ""
    avisos: tuple[str, ...] = field(default_factory=tuple)

    def resumo(self) -> dict:
        return {"empresa": self.identidade_mascarada, "nome": self.nome,
                "estado": self.estado, "pode_consultar": self.pode_consultar,
                "motivo": self.motivo,
                "tem_arquivo": self.tem_arquivo,
                "tem_senha_guardada": self.tem_senha_guardada,
                "validade_ate": self.validade_ate or None,
                "avisos": list(self.avisos)}


def avaliar(dados_dir, identidade, *, cad=None,
            validade_ate: date | None = None,
            documento_do_certificado: str = "",
            senha_disponivel: bool | None = None,
            hoje: date | None = None) -> Prontidao:
    """A prontidão de uma empresa para consultar CT-e.

    Os três parâmetros opcionais existem para quem **já abriu** o certificado
    (uma rotina autorizada, um teste) poder informar o que apurou. Vazios, a
    função não inventa: devolve `VALIDADE_DESCONHECIDA` e segue.
    """
    dados = Path(dados_dir)
    cad = cad if cad is not None else cad_mod.carregar(dados)
    q = hoje or datetime.now(timezone.utc).date()
    ident = normalizar(identidade)

    p = Prontidao(identidade_mascarada=_mascarar(identidade))
    if not ident.valido:
        p.estado, p.motivo = SEM_CADASTRO, f"identidade inválida: {ident.motivo}"
        return p

    emp = cad.obter_empresa(ident.valor)
    if emp is None:
        p.estado, p.motivo = SEM_CADASTRO, EXPLICACAO[SEM_CADASTRO]
        return p
    p.nome = emp.nome_exibicao()

    cred = emp.credencial_preferida()
    if cred is None:
        p.estado, p.motivo = SEM_CERTIFICADO, EXPLICACAO[SEM_CERTIFICADO]
        return p

    caminho = getattr(cred, "caminho", "") or ""
    alvo = Path(caminho)
    if not alvo.is_absolute():
        alvo = dados / caminho
    p.tem_arquivo = bool(caminho) and alvo.exists()
    if not p.tem_arquivo:
        p.estado, p.motivo = ARQUIVO_AUSENTE, EXPLICACAO[ARQUIVO_AUSENTE]
        return p

    p.tem_senha_guardada = bool(getattr(cred, "senha_protegida", "") or
                                getattr(cred, "tem_senha", False))
    if senha_disponivel is False or (senha_disponivel is None
                                     and not p.tem_senha_guardada):
        p.estado, p.motivo = SENHA_PENDENTE, EXPLICACAO[SENHA_PENDENTE]
        return p

    # ── documento: cadastro × certificado ───────────────────────────────
    # Só compara quando alguém INFORMOU o que leu do certificado. Sem isso não
    # há divergência a declarar — e inventar uma seria pior que não checar.
    if documento_do_certificado:
        lido = normalizar(documento_do_certificado)
        if not lido.valido or lido.valor != ident.valor:
            p.estado = DOCUMENTO_DIVERGENTE
            p.motivo = (EXPLICACAO[DOCUMENTO_DIVERGENTE] +
                        f" (cadastro {_mascarar(ident.valor)}, "
                        f"certificado {_mascarar(documento_do_certificado)})")
            return p

    # ── validade ────────────────────────────────────────────────────────
    if validade_ate is None:
        p.estado = PRONTA
        p.pode_consultar = True
        p.motivo = EXPLICACAO[PRONTA]
        p.avisos = (EXPLICACAO[VALIDADE_DESCONHECIDA],)
        return p

    p.validade_ate = validade_ate.isoformat()
    if validade_ate < q:
        p.estado, p.motivo = VENCIDO, EXPLICACAO[VENCIDO]
        return p

    p.estado = PRONTA
    p.pode_consultar = True
    p.motivo = EXPLICACAO[PRONTA]
    return p


def orfaos(dados_dir, *, cad=None) -> list[str]:
    """Arquivos `.pfx` em `certs/` sem empresa correspondente no cadastro.

    Devolve só o NOME do arquivo, e mesmo assim mascarado no que parece
    documento: um `.pfx` órfão é um problema de organização, e o relatório dele
    não precisa espalhar CNPJ.
    """
    dados = Path(dados_dir)
    cad = cad if cad is not None else cad_mod.carregar(dados)
    conhecidos = set()
    for emp in cad.listar_empresas():
        for c in cad.obter_credenciais_da_empresa(
                getattr(emp, "identificador", "") or emp.nome_exibicao()):
            nome = Path(getattr(c, "caminho", "") or "").name
            if nome:
                conhecidos.add(nome.casefold())

    pasta = dados / "certs"
    if not pasta.exists():
        return []
    fora = []
    for p in sorted(pasta.glob("*.pfx")) + sorted(pasta.glob("*.p12")):
        if p.name.casefold() not in conhecidos:
            fora.append(_higienizar_nome(p.name))
    return fora


def _higienizar_nome(nome: str) -> str:
    """Troca sequências de 11+ dígitos por `***`. Nome de arquivo de
    certificado costuma trazer o CNPJ inteiro, e o relatório não precisa dele."""
    import re
    return re.sub(r"\d{11,}", "***", nome)


def panorama(dados_dir, *, cad=None, hoje: date | None = None) -> dict:
    """A prontidão de TODAS as empresas, em ordem alfabética.

    A ordem é a mesma da seleção de clientes do resto do FISCALE — quem lê a
    tela e quem lê este relatório veem a mesma sequência.
    """
    dados = Path(dados_dir)
    cad = cad if cad is not None else cad_mod.carregar(dados)
    linhas = []
    for emp in cad.listar_empresas():
        ident = getattr(emp, "identificador", "") or ""
        linhas.append(avaliar(dados, ident, cad=cad, hoje=hoje).resumo())

    contagem: dict[str, int] = {}
    for l in linhas:
        contagem[l["estado"]] = contagem.get(l["estado"], 0) + 1
    return {
        "empresas": linhas,
        "por_estado": contagem,
        "prontas": sum(1 for l in linhas if l["pode_consultar"]),
        "arquivos_orfaos": orfaos(dados, cad=cad),
        "observacao": EXPLICACAO[VALIDADE_DESCONHECIDA],
    }
