"""
modelo.py — Empresa, Credencial e o vínculo entre as duas.

A SEPARAÇÃO QUE IMPORTA
    Empresa é quem tem obrigação fiscal. Credencial é um certificado digital
    que prova identidade perante o fisco. **Não são a mesma coisa e não são
    1:1.** Hoje o cadastro real tem `procurador: true/false` em
    `certificados.json` — ou seja, a possibilidade de uma credencial servir a
    uma empresa que não é a titular dela já existe nos dados. Modelar como 1:1
    seria contrariar o que está em disco.

    Por isso o `Vinculo` carrega um **motivo**: não basta saber que a credencial
    pode ser usada, é preciso saber por quê. Quando alguém for auditar uma
    consulta feita em nome de terceiro, a resposta tem que estar no modelo.

SEGREDO
    A senha do certificado **não é atributo de Empresa nem de DocumentoFiscal
    nem de checkpoint**. `Credencial` guarda apenas o blob já protegido pelo
    DPAPI (o mesmo cofre da PORT 3), marcado `repr=False` para não escapar em
    traceback, log ou `print`. O texto claro só existe dentro de
    `abrir_senha()`, no momento de montar a sessão, e não é devolvido a
    ninguém que não tenha pedido explicitamente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .identidade import Identificador, normalizar

# ── por que esta credencial pode ser usada por esta empresa ─────────────────
MOTIVO_TITULAR = "TITULAR"          # o certificado é da própria empresa
MOTIVO_PROCURACAO = "PROCURACAO"    # representação autorizada (campo `procurador`)
MOTIVO_INDEFINIDO = "INDEFINIDO"    # há vínculo no cadastro, mas não sabemos a razão

# ── estados da reconciliação (não destrutiva) ───────────────────────────────
CONFIRMADO = "CONFIRMADO"                    # identificador válido nas duas fontes
SOMENTE_CERTIFICADO = "SOMENTE_CERTIFICADO"  # só em certificados.json
SOMENTE_CLIENTES = "SOMENTE_CLIENTES"        # só em state_clientes.json
AMBIGUO = "AMBIGUO"                          # mais de um registro para o mesmo id
INVALIDO = "INVALIDO"                        # identificador ausente ou não confere

ORIGEM_CERTIFICADOS = "certificados.json"
ORIGEM_CLIENTES = "state_clientes.json"


@dataclass
class Credencial:
    """Um certificado digital A1 (.pfx) e o que se sabe sobre ele.

    `senha_protegida` é o blob do cofre, nunca a senha. `tem_senha` responde a
    pergunta que a tela faz sem precisar abrir nada.
    """
    id: str
    titular: Identificador                 # de quem é o certificado
    nome_no_certificado: str = ""
    caminho: str = ""
    validade: str = ""
    origem: str = ORIGEM_CERTIFICADOS
    senha_protegida: str = field(default="", repr=False)

    @property
    def tem_senha(self) -> bool:
        return bool(self.senha_protegida)

    @property
    def arquivo_existe(self) -> bool:
        return bool(self.caminho) and Path(self.caminho).is_file()

    def abrir_senha(self, dados_dir) -> str:
        """Devolve a senha em claro. Único caminho para ela sair do cofre.

        Levanta se o cofre não abre — o que acontece, por desenho, num backup
        restaurado em outra máquina (DPAPI é da máquina + conta do Windows).
        Nesse caso a conta fica "aguardando senha", que é o comportamento certo.
        """
        if not self.senha_protegida:
            raise ValueError(f"credencial {self.id}: senha não cadastrada")
        import seguranca
        return seguranca.desproteger(self.senha_protegida, dados_dir)

    def resumo(self) -> dict:
        """Versão segura para tela, API e log. Sem blob, sem senha."""
        return {
            "id": self.id,
            "titular": self.titular.mascarado(),
            "tipo_titular": self.titular.tipo,
            "nome_no_certificado": self.nome_no_certificado,
            "tem_senha": self.tem_senha,
            "arquivo_existe": self.arquivo_existe,
            "validade": self.validade,
            "origem": self.origem,
        }


@dataclass
class Vinculo:
    """Liga uma empresa a uma credencial, dizendo por quê."""
    credencial: Credencial
    motivo: str = MOTIVO_INDEFINIDO
    observacao: str = ""

    @property
    def e_titular(self) -> bool:
        return self.motivo == MOTIVO_TITULAR

    def resumo(self) -> dict:
        return {"credencial": self.credencial.resumo(),
                "motivo": self.motivo, "observacao": self.observacao}


@dataclass
class Empresa:
    """A empresa, montada a partir de todas as fontes que falam dela.

    `origens` registra de quais arquivos ela veio — é o que permite responder
    "esta empresa está só no cadastro de clientes, sem certificado" sem que o
    chamador precise saber que existem dois JSON.
    """
    identificador: Identificador
    nome: str = ""
    apelido: str = ""
    regime: str = ""
    uf: str = ""
    municipio: str = ""
    ie: str = ""
    im: str = ""
    email: str = ""
    telefone: str = ""
    origens: set[str] = field(default_factory=set)
    vinculos: list[Vinculo] = field(default_factory=list)
    pendencias: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        """Chave canônica: os dígitos do CNPJ/CPF. É o nome da pasta de dados."""
        return self.identificador.valor

    @property
    def e_pessoa_juridica(self) -> bool:
        return self.identificador.tipo == "CNPJ"

    @property
    def tem_credencial(self) -> bool:
        return bool(self.vinculos)

    def credencial_preferida(self) -> Credencial | None:
        """A credencial a usar quando ninguém especificou qual.

        Ordem: titular com senha > titular > qualquer uma com senha > a primeira.
        O certificado da própria empresa vem antes do de procurador porque é o
        que não depende de autorização vigente.
        """
        if not self.vinculos:
            return None
        ordem = sorted(
            self.vinculos,
            key=lambda v: (not v.e_titular, not v.credencial.tem_senha),
        )
        return ordem[0].credencial

    def nome_exibicao(self) -> str:
        return self.apelido or self.nome or self.identificador.mascarado()

    def resumo(self) -> dict:
        return {
            "id": self.id,
            "identificador": self.identificador.mascarado(),
            "tipo": self.identificador.tipo,
            "nome": self.nome,
            "apelido": self.apelido,
            "regime": self.regime,
            "uf": self.uf,
            "municipio": self.municipio,
            "origens": sorted(self.origens),
            "credenciais": [v.resumo() for v in self.vinculos],
            "pendencias": list(self.pendencias),
        }


@dataclass
class LinhaReconciliacao:
    """Uma linha da tabela de reconciliação `certificados.json` × `state_clientes.json`.

    Existe para **relatar**, não para consertar. Nenhum registro é eliminado ou
    fundido nesta fase — a decisão de o que fazer com um `AMBIGUO` é do usuário.
    """
    identificador_bruto: str
    identificador: Identificador
    nome_certificados: str = ""
    nome_clientes: str = ""
    em_certificados: bool = False
    em_clientes: bool = False
    credenciais: int = 0
    status: str = INVALIDO
    detalhe: str = ""

    def linha(self) -> dict:
        return {
            "cnpj": self.identificador.mascarado() if self.identificador.valido
                    else f"<bruto:{len(self.identificador_bruto)} car.>",
            "id_canonico": self.identificador.valor,
            "tipo": self.identificador.tipo,
            "nome": self.nome_certificados or self.nome_clientes,
            "certificados.json": "sim" if self.em_certificados else "-",
            "state_clientes.json": "sim" if self.em_clientes else "-",
            "credenciais": self.credenciais,
            "status": self.status,
            "detalhe": self.detalhe,
        }


def classificar(em_cert: bool, em_cli: bool, valido: bool, duplicado: bool) -> str:
    """Regra única de classificação da reconciliação."""
    if not valido:
        return INVALIDO
    if duplicado:
        return AMBIGUO
    if em_cert and em_cli:
        return CONFIRMADO
    if em_cert:
        return SOMENTE_CERTIFICADO
    if em_cli:
        return SOMENTE_CLIENTES
    return INVALIDO
