"""
cadastro.py — a fonte canônica de "quem é a empresa".

O PROBLEMA
    Existem dois cadastros que não conversam: `certificados.json` (21 contas com
    certificado) e `state_clientes.json` (14 clientes da tela). As listas não
    coincidem, e nada no código força a coincidir. Enquanto forem dois, a
    pergunta "quais empresas o motor deve varrer?" não tem resposta definida.

O QUE ESTA CAMADA FAZ — E O QUE NÃO FAZ
    FAZ: responde `obter_empresa`, `listar_empresas` e
    `obter_certificado_da_empresa` sem que o chamador saiba de qual JSON o dado
    veio, usando o identificador fiscal canônico como chave.

    NÃO FAZ: não escolhe um arquivo como "o verdadeiro", não funde registros,
    não apaga nada e não grava nada. Esta fase é de leitura. Divergência vira
    linha na tabela de reconciliação com estado explícito — a decisão de o que
    fazer com um `AMBIGUO` é do usuário, não minha.

IDENTIDADE É O CNPJ, NUNCA O NOME
    Dois registros só são a mesma empresa se o identificador fiscal confirmar.
    Nome parecido não vale: o nome ajuda a diagnosticar, e é tudo.

RAIZ DE DADOS INJETADA
    Toda função recebe `dados_dir`. Isso não é cerimônia: é o que permite o
    teste rodar contra uma pasta temporária sem chegar perto de
    `~/Fiscale/dados`. Mesmo padrão já usado no subpacote `saude`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import modelo as m
from .identidade import Identificador, normalizar

ARQ_CERTIFICADOS = "certificados.json"
ARQ_CLIENTES = "state_clientes.json"


def _ler_json(caminho: Path, padrao):
    """Lê JSON tolerando ausência e BOM. Arquivo corrompido não derruba o
    cadastro inteiro — vira ausência, e a reconciliação mostra o vazio."""
    try:
        return json.loads(caminho.read_text("utf-8-sig"))
    except Exception:
        return padrao


def _resolver_cert(caminho: str, dados_dir: Path) -> str:
    """Relativo (como está gravado) -> absoluto. Espelha `fiscale_dados.resolver_cert`,
    mas contra a raiz injetada, para o teste não depender da raiz real."""
    if not caminho:
        return ""
    p = Path(caminho)
    return str(p) if p.is_absolute() else str((Path(dados_dir) / p).resolve())


def _texto(v) -> str:
    return "" if v is None else str(v).strip()


def _arquivo_do_cliente(cert) -> str:
    """`state_clientes.json` grava o certificado ora como dict, ora como texto."""
    if isinstance(cert, dict):
        return _texto(cert.get("arquivo") or cert.get("caminho"))
    return _texto(cert)


@dataclass
class Cadastro:
    """Visão unificada e somente-leitura das duas fontes."""
    dados_dir: Path
    empresas: dict[str, m.Empresa] = field(default_factory=dict)
    reconciliacao: list[m.LinhaReconciliacao] = field(default_factory=list)
    invalidos: list[m.LinhaReconciliacao] = field(default_factory=list)

    # ── consultas públicas ─────────────────────────────────────────────────
    def obter_empresa(self, identificador) -> m.Empresa | None:
        """Empresa pelo CNPJ/CPF em qualquer formato (com ou sem máscara)."""
        ident = normalizar(identificador)
        return self.empresas.get(ident.valor) if ident.valido else None

    def listar_empresas(self, com_credencial: bool | None = None) -> list[m.Empresa]:
        """Todas as empresas conhecidas, ordenadas por nome de exibição.

        `com_credencial=True` devolve só as que têm certificado — é a lista que
        um motor de ingestão pode efetivamente varrer.
        """
        itens = list(self.empresas.values())
        if com_credencial is not None:
            itens = [e for e in itens if e.tem_credencial == com_credencial]
        return sorted(itens, key=lambda e: e.nome_exibicao().casefold())

    def obter_certificado_da_empresa(self, identificador) -> m.Credencial | None:
        """A credencial preferida da empresa, ou None se não houver."""
        emp = self.obter_empresa(identificador)
        return emp.credencial_preferida() if emp else None

    def obter_credenciais_da_empresa(self, identificador) -> list[m.Credencial]:
        emp = self.obter_empresa(identificador)
        return [v.credencial for v in emp.vinculos] if emp else []

    def tabela_reconciliacao(self) -> list[dict]:
        return [l.linha() for l in self.reconciliacao]

    def contagem_por_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for l in self.reconciliacao:
            out[l.status] = out.get(l.status, 0) + 1
        return out


def carregar(dados_dir) -> Cadastro:
    """Monta o cadastro canônico a partir dos dois JSON. Nunca grava."""
    dados_dir = Path(dados_dir)
    cad = Cadastro(dados_dir=dados_dir)

    brutos_cert = _ler_json(dados_dir / ARQ_CERTIFICADOS, [])
    if not isinstance(brutos_cert, list):
        brutos_cert = []
    dados_cli = _ler_json(dados_dir / ARQ_CLIENTES, {})
    brutos_cli = dados_cli.get("clientes") if isinstance(dados_cli, dict) else dados_cli
    if not isinstance(brutos_cli, list):
        brutos_cli = []

    # Passo 1 — normaliza cada registro e agrupa por identificador canônico.
    # Registro com identificador inválido NÃO é descartado: vira pendência.
    por_id: dict[str, dict[str, list]] = {}
    invalidos: list[m.LinhaReconciliacao] = []

    def indexar(reg: dict, origem: str, nome: str):
        ident = normalizar(reg.get("cnpj") or reg.get("id"))
        if not ident.valido:
            invalidos.append(m.LinhaReconciliacao(
                identificador_bruto=_texto(reg.get("cnpj") or reg.get("id")),
                identificador=ident, status=m.INVALIDO,
                nome_certificados=nome if origem == m.ORIGEM_CERTIFICADOS else "",
                nome_clientes=nome if origem == m.ORIGEM_CLIENTES else "",
                em_certificados=origem == m.ORIGEM_CERTIFICADOS,
                em_clientes=origem == m.ORIGEM_CLIENTES,
                detalhe=f"identificador não utilizável: {ident.motivo}",
            ))
            return
        alvo = por_id.setdefault(ident.valor, {"ident": ident, "cert": [], "cli": []})
        alvo["cert" if origem == m.ORIGEM_CERTIFICADOS else "cli"].append(reg)

    for reg in brutos_cert:
        if isinstance(reg, dict):
            indexar(reg, m.ORIGEM_CERTIFICADOS, _texto(reg.get("nome")))
    for reg in brutos_cli:
        if isinstance(reg, dict):
            indexar(reg, m.ORIGEM_CLIENTES, _texto(reg.get("nome")))

    # Passo 2 — monta Empresa + Credenciais para cada identificador.
    for id_canonico, grupo in por_id.items():
        ident: Identificador = grupo["ident"]
        regs_cert: list[dict] = grupo["cert"]
        regs_cli: list[dict] = grupo["cli"]

        emp = m.Empresa(identificador=ident)
        if regs_cert:
            emp.origens.add(m.ORIGEM_CERTIFICADOS)
        if regs_cli:
            emp.origens.add(m.ORIGEM_CLIENTES)

        # Dados cadastrais: o cadastro de clientes é mais rico (regime, UF, IE,
        # IM); o de certificados traz o nome que veio do próprio certificado.
        # Nenhum sobrescreve o outro com vazio.
        for reg in regs_cli:
            emp.nome = emp.nome or _texto(reg.get("nome"))
            emp.regime = emp.regime or _texto(reg.get("regime"))
            emp.uf = emp.uf or _texto(reg.get("uf"))
            emp.municipio = emp.municipio or _texto(reg.get("mun"))
            emp.ie = emp.ie or _texto(reg.get("ie"))
            emp.im = emp.im or _texto(reg.get("im"))
            emp.email = emp.email or _texto(reg.get("email"))
            emp.telefone = emp.telefone or _texto(reg.get("tel"))
        for reg in regs_cert:
            emp.nome = emp.nome or _texto(reg.get("nome"))
            emp.apelido = emp.apelido or _texto(reg.get("apelido"))

        for reg in regs_cert:
            procurador = bool(reg.get("procurador"))
            cred = m.Credencial(
                id=_texto(reg.get("id")) or id_canonico,
                titular=ident,
                nome_no_certificado=_texto(reg.get("nome")),
                caminho=_resolver_cert(_texto(reg.get("caminho")), dados_dir),
                origem=m.ORIGEM_CERTIFICADOS,
                senha_protegida=_texto(reg.get("senha_protegida")),
            )
            if procurador:
                motivo, obs = (m.MOTIVO_PROCURACAO,
                               "certificado de procurador; o titular do certificado "
                               "não está registrado separadamente no cadastro atual")
            else:
                motivo, obs = m.MOTIVO_TITULAR, ""
            emp.vinculos.append(m.Vinculo(credencial=cred, motivo=motivo, observacao=obs))

        # Validade do certificado só existe no cadastro de clientes.
        for reg in regs_cli:
            validade = _texto(reg.get("certValidade"))
            arquivo = _arquivo_do_cliente(reg.get("cert"))
            for v in emp.vinculos:
                if validade and not v.credencial.validade:
                    v.credencial.validade = validade
                if arquivo and not v.credencial.nome_no_certificado:
                    v.credencial.nome_no_certificado = arquivo

        # Pendências: relatadas, nunca corrigidas em silêncio.
        if ident.ajustes:
            emp.pendencias.append("identificador ajustado: " + "; ".join(ident.ajustes))
        if len(regs_cert) > 1:
            emp.pendencias.append(f"{len(regs_cert)} registros em {m.ORIGEM_CERTIFICADOS}")
        if len(regs_cli) > 1:
            emp.pendencias.append(f"{len(regs_cli)} registros em {m.ORIGEM_CLIENTES}")
        for v in emp.vinculos:
            if not v.credencial.tem_senha:
                emp.pendencias.append(f"credencial {v.credencial.id}: aguardando senha")
            elif not v.credencial.arquivo_existe:
                emp.pendencias.append(f"credencial {v.credencial.id}: arquivo .pfx não encontrado")

        cad.empresas[id_canonico] = emp

        duplicado = len(regs_cert) > 1 or len(regs_cli) > 1
        status = m.classificar(bool(regs_cert), bool(regs_cli), ident.valido, duplicado)
        cad.reconciliacao.append(m.LinhaReconciliacao(
            identificador_bruto=ident.bruto,
            identificador=ident,
            nome_certificados=_texto(regs_cert[0].get("nome")) if regs_cert else "",
            nome_clientes=_texto(regs_cli[0].get("nome")) if regs_cli else "",
            em_certificados=bool(regs_cert),
            em_clientes=bool(regs_cli),
            credenciais=len(emp.vinculos),
            status=status,
            detalhe="; ".join(emp.pendencias),
        ))

    cad.invalidos = invalidos
    cad.reconciliacao.extend(invalidos)
    cad.reconciliacao.sort(key=lambda l: (l.status, l.nome_certificados or l.nome_clientes))
    return cad
