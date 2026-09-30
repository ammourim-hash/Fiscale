"""
credencial_estado.py — em que situação está o certificado desta empresa.

POR QUE SEIS ESTADOS E NÃO "funciona / não funciona"
    O escritório tem 21 certificados. Quando um deles para, a pergunta que
    importa é o que fazer a respeito, e a resposta muda inteiramente conforme o
    motivo: certificado **expirado** significa ligar para o cliente e renovar;
    **senha incorreta** significa reinformar a senha na tela; **ausente**
    significa que o arquivo sumiu do disco; **corrompido** significa restaurar
    de backup. "Falha de comunicação" não distingue nada disso, e foi assim que
    a DI CAVALCANTI ficou 15 dias vencida sem o sistema dizer uma palavra.

    Há ainda o caso raro mas real de o certificado **ainda não ser válido** —
    A1 recém-emitido com início no dia seguinte, ou relógio da máquina errado.
    Sem esse estado o diagnóstico seria "expirado", e alguém tentaria renovar um
    certificado novo em folha.

UMA EMPRESA RUIM NÃO DERRUBA AS OUTRAS
    `avaliar()` **nunca levanta**. Devolve sempre um `EstadoCredencial`. É o que
    permite ao motor varrer as 21 empresas, pular as três que estão com problema
    e reportar quais foram — em vez de parar na primeira e deixar 18 empresas
    sem documento.

QUEM DECIDE SE O ARQUIVO PRESTA (corrigido em 13/08/2026)
    **A biblioteca criptográfica é a fonte da verdade.** O exame de bytes que
    existe aqui é *triagem*: serve para dar uma mensagem melhor quando a
    `cryptography` recusa, e nunca para recusar sozinho um arquivo que ela
    aceitaria.

    A versão anterior fazia o contrário, e errou feio. Ela exigia DER de
    **comprimento definido** e recusava, antes de tentar abrir, todo PKCS#12
    com envelope BER de **comprimento indefinido** (`30 80 … 00 00`) — que é
    codificação legítima e é o que várias ACs da ICP-Brasil emitem. Resultado:
    6 dos 21 certificados do escritório apareciam como "corrompidos" sem
    estarem. Um deles tinha 56 XML baixados com o próprio arquivo que a
    validação dizia estar danificado.

    Regra que ficou: só é `CORROMPIDO` o que a `cryptography` não conseguir
    interpretar. A triagem de bytes apenas ajuda a escolher entre "senha
    provavelmente incorreta" e "arquivo realmente inválido" quando a exceção
    da biblioteca não distingue os dois.

LIMITE DECLARADO DA HEURÍSTICA
    A `cryptography` levanta o mesmo `ValueError` para senha errada e para
    conteúdo corrompido internamente. Quando o envelope é reconhecível, a
    aposta é "senha"; quando nem o envelope se sustenta, a aposta é "arquivo".

    Isso é uma heurística, e está escrito aqui que é. Ela erra num caso:
    arquivo corrompido exatamente nos bytes internos, mantendo o envelope
    válido, é reportado como senha incorreta. O usuário reinformaria a senha,
    veria falhar de novo, e aí sim suspeitaria do arquivo — sem risco de perda
    de dado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import modelo as m
from .identidade import normalizar

# ── os seis estados ─────────────────────────────────────────────────────────
VALIDO = "VALIDO"
AUSENTE = "AUSENTE"                      # arquivo .pfx não está no caminho
SENHA_NAO_CADASTRADA = "SENHA_NAO_CADASTRADA"   # cofre vazio / não abre aqui
SENHA_INCORRETA = "SENHA_INCORRETA"      # a senha guardada não abre o certificado
CORROMPIDO = "CORROMPIDO"                # a cryptography não interpreta o arquivo
SEM_CHAVE_PRIVADA = "SEM_CHAVE_PRIVADA"  # abre, tem certificado, falta a chave
AINDA_NAO_VALIDO = "AINDA_NAO_VALIDO"    # notBefore no futuro
EXPIRADO = "EXPIRADO"                    # notAfter no passado

# Estados em que NÃO adianta tentar consultar o fisco.
IMPEDITIVOS = frozenset({AUSENTE, SENHA_NAO_CADASTRADA, SENHA_INCORRETA,
                         CORROMPIDO, SEM_CHAVE_PRIVADA, AINDA_NAO_VALIDO,
                         EXPIRADO})

# O que o usuário precisa fazer, por estado. É isto que a tela mostra.
ACAO = {
    VALIDO: "",
    AUSENTE: "o arquivo .pfx não está no caminho cadastrado — recadastre o certificado",
    SENHA_NAO_CADASTRADA: "informe a senha do certificado nesta máquina",
    SENHA_INCORRETA: "a senha guardada não abre o certificado — informe novamente",
    CORROMPIDO: "o arquivo do certificado está danificado — restaure de backup ou recadastre",
    SEM_CHAVE_PRIVADA: "o arquivo não traz a chave privada — reexporte o certificado "
                       "incluindo a chave",
    AINDA_NAO_VALIDO: "o certificado só passa a valer depois da data de início; "
                      "confira também o relógio do computador",
    EXPIRADO: "o certificado venceu — renove com a autoridade certificadora",
}


@dataclass(frozen=True)
class EstadoCredencial:
    """Diagnóstico completo de uma credencial. Nunca contém segredo."""
    credencial_id: str
    estado: str
    detalhe: str = ""
    titular: str = ""
    nao_antes: str = ""
    nao_depois: str = ""
    dias_para_vencer: int | None = None

    @property
    def utilizavel(self) -> bool:
        return self.estado == VALIDO

    @property
    def acao_necessaria(self) -> str:
        return ACAO.get(self.estado, "")

    @property
    def vence_em_breve(self) -> bool:
        """Avisar antes de parar vale mais do que explicar depois."""
        return (self.estado == VALIDO and self.dias_para_vencer is not None
                and self.dias_para_vencer <= 30)

    def resumo(self) -> dict:
        return {
            "credencial": self.credencial_id,
            "estado": self.estado,
            "utilizavel": self.utilizavel,
            "detalhe": self.detalhe,
            "titular": self.titular,
            "valido_de": self.nao_antes,
            "valido_ate": self.nao_depois,
            "dias_para_vencer": self.dias_para_vencer,
            "vence_em_breve": self.vence_em_breve,
            "acao": self.acao_necessaria,
        }


FORMATO_DEFINIDO = "DER_COMPRIMENTO_DEFINIDO"
FORMATO_INDEFINIDO = "BER_COMPRIMENTO_INDEFINIDO"
FORMATO_IRRECONHECIVEL = "IRRECONHECIVEL"


def _triagem_envelope(dados: bytes) -> tuple[str, str]:
    """Que cara tem este arquivo? Devolve `(formato, observação)`.

    **Isto NÃO recusa nada.** É triagem: o veredito é da `cryptography`. Aqui
    só se descreve o envelope, para que a mensagem final saiba distinguir
    "provavelmente a senha" de "provavelmente o arquivo".

    Um PKCS#12 é uma SEQUENCE ASN.1, então começa com `0x30`. O segundo byte diz
    o comprimento, e há duas codificações legítimas:

        30 82 0E 4B …      comprimento DEFINIDO (DER)   — 0x82 = 2 bytes de tamanho
        30 80 … 00 00      comprimento INDEFINIDO (BER) — fecha com dois zeros

    A segunda é a que a versão anterior recusava. Ela é válida, e é o que várias
    ACs da ICP-Brasil emitem.
    """
    if not dados:
        return FORMATO_IRRECONHECIVEL, "arquivo vazio"
    if len(dados) < 2:
        return FORMATO_IRRECONHECIVEL, "arquivo truncado (menos de 2 bytes)"
    if dados[0] != 0x30:
        return (FORMATO_IRRECONHECIVEL,
                "não começa com uma SEQUENCE ASN.1 (não parece um PKCS#12)")

    b = dados[1]
    if b == 0x80:
        # Comprimento indefinido: o conteúdo termina em 00 00 (End-of-Contents).
        if not dados.endswith(b"\x00\x00"):
            return (FORMATO_IRRECONHECIVEL,
                    "envelope BER de comprimento indefinido sem o marcador de fim")
        return FORMATO_INDEFINIDO, ""
    if b & 0x80:
        n = b & 0x7F
        if n > 4 or len(dados) < 2 + n:
            return FORMATO_IRRECONHECIVEL, "cabeçalho de comprimento fora do previsto"
        tamanho = int.from_bytes(dados[2:2 + n], "big")
        cabecalho = 2 + n
    else:
        tamanho, cabecalho = b, 2
    if cabecalho + tamanho > len(dados):
        return (FORMATO_IRRECONHECIVEL,
                f"arquivo truncado: o cabeçalho declara {cabecalho + tamanho} "
                f"bytes e há {len(dados)}")
    return FORMATO_DEFINIDO, ""


def _envelope_integro(dados: bytes) -> tuple[bool, str]:
    """Compatibilidade: `True` quando o envelope é reconhecível.

    Mantida porque outros pontos do projeto a chamam. **BER de comprimento
    indefinido agora conta como reconhecível** — era o falso negativo."""
    formato, obs = _triagem_envelope(dados)
    return formato != FORMATO_IRRECONHECIVEL, obs


def _classificar_falha_ao_abrir(dados: bytes, exc: Exception) -> tuple[str, str]:
    """A `cryptography` recusou. Foi a senha ou foi o arquivo?

    A biblioteca usa `ValueError` para os dois casos, então a distinção vem da
    triagem do envelope — declaradamente uma aposta, não uma certeza."""
    formato, obs = _triagem_envelope(dados)
    nome = type(exc).__name__
    if formato == FORMATO_IRRECONHECIVEL:
        return CORROMPIDO, f"o arquivo não é um PKCS#12 interpretável: {obs or nome}"
    texto = str(exc).lower()
    if any(t in texto for t in ("mac verify", "invalid password", "wrong password",
                                "decrypt", "authentication")):
        return SENHA_INCORRETA, f"a senha guardada não abre o certificado ({nome})"
    # Envelope reconhecível e mesmo assim não abriu: a aposta é senha, e o
    # detalhe diz que o arquivo parece íntegro — para o usuário não sair
    # restaurando backup à toa.
    return SENHA_INCORRETA, (f"o envelope está íntegro ({formato}) mas a senha "
                             f"não abre ({nome})")


def avaliar(cred: m.Credencial, dados_dir, agora: datetime | None = None) -> EstadoCredencial:
    """Diagnostica a credencial. **NUNCA levanta** — sempre devolve um estado.

    É o contrato que permite ao motor continuar nas outras empresas quando uma
    delas está com problema.
    """
    agora = agora or datetime.now(timezone.utc)
    cid = cred.id

    # 1. o arquivo existe?
    if not cred.caminho:
        return EstadoCredencial(cid, AUSENTE, "caminho do certificado não cadastrado")
    p = Path(cred.caminho)
    if not p.is_file():
        return EstadoCredencial(cid, AUSENTE, f"arquivo não encontrado: {p.name}")
    try:
        dados = p.read_bytes()
    except OSError as exc:
        return EstadoCredencial(cid, AUSENTE, f"não foi possível ler o arquivo ({type(exc).__name__})")

    # 2. triagem do envelope — NÃO recusa nada; só descreve o arquivo.
    #    A decisão de "corrompido" ficou com a `cryptography` (passo 4).
    formato, _obs = _triagem_envelope(dados)

    # 3. temos a senha? (cofre vazio ou que não abre nesta máquina)
    if not cred.tem_senha:
        return EstadoCredencial(cid, SENHA_NAO_CADASTRADA, "senha não cadastrada")
    try:
        senha = cred.abrir_senha(dados_dir)
    except Exception as exc:
        return EstadoCredencial(cid, SENHA_NAO_CADASTRADA,
                                f"a senha guardada não abre nesta máquina ({type(exc).__name__})")
    if not senha:
        return EstadoCredencial(cid, SENHA_NAO_CADASTRADA, "senha guardada está vazia")

    # 4. a senha abre o certificado?
    from cryptography.hazmat.primitives.serialization import pkcs12 as _pkcs12
    from cryptography.x509.oid import NameOID
    try:
        chave, cert, extras = _pkcs12.load_key_and_certificates(dados, senha.encode("utf-8"))
    except Exception as exc:
        # A senha NUNCA entra na mensagem — só o nome da exceção e o formato.
        estado, detalhe = _classificar_falha_ao_abrir(dados, exc)
        return EstadoCredencial(cid, estado, detalhe)
    finally:
        del senha

    # Sem chave privada não há mTLS. É diferente de corrompido: o arquivo está
    # bom, falta a metade que assina — acontece quando exportam só a parte
    # pública. E o formato varia: sem chave, a `cryptography` costuma devolver
    # `(None, None, [cert])`, porque sem par o certificado vai para o saco de
    # "adicionais" em vez do principal. Os dois arranjos significam a mesma
    # coisa para quem vai consultar o fisco.
    if chave is None and (cert is not None or extras):
        return EstadoCredencial(cid, SEM_CHAVE_PRIVADA,
                                "o arquivo contém certificado mas não a chave "
                                "privada — reexporte incluindo a chave")
    if cert is None:
        return EstadoCredencial(cid, CORROMPIDO,
                                "o arquivo abriu mas não contém certificado")

    try:
        titular = str(cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)
    except Exception:
        titular = ""
    inicio, fim = cert.not_valid_before_utc, cert.not_valid_after_utc
    base = dict(credencial_id=cid, titular=titular,
                nao_antes=inicio.isoformat(), nao_depois=fim.isoformat())

    # 5. está dentro da validade?
    if agora < inicio:
        return EstadoCredencial(estado=AINDA_NAO_VALIDO,
                                detalhe=f"só passa a valer em {inicio.date().isoformat()}",
                                dias_para_vencer=(fim - agora).days, **base)
    if agora > fim:
        return EstadoCredencial(estado=EXPIRADO,
                                detalhe=f"venceu em {fim.date().isoformat()} "
                                        f"({(agora - fim).days} dia(s) atrás)",
                                dias_para_vencer=-((agora - fim).days), **base)
    return EstadoCredencial(estado=VALIDO, dias_para_vencer=(fim - agora).days, **base)


def avaliar_empresas(cad, dados_dir=None, agora: datetime | None = None) -> dict[str, EstadoCredencial]:
    """Diagnostica TODAS as empresas do cadastro sem parar na primeira ruim.

    Devolve `{id_da_empresa: EstadoCredencial}`. Empresa sem credencial vira
    `AUSENTE` — que é a verdade: não há o que usar.
    """
    dados_dir = dados_dir if dados_dir is not None else cad.dados_dir
    out: dict[str, EstadoCredencial] = {}
    for emp in cad.listar_empresas():
        cred = emp.credencial_preferida()
        if cred is None:
            out[emp.id] = EstadoCredencial(emp.id, AUSENTE, "nenhuma credencial cadastrada")
            continue
        try:
            out[emp.id] = avaliar(cred, dados_dir, agora)
        except Exception as exc:      # cinto e suspensório: avaliar() não deve levantar
            out[emp.id] = EstadoCredencial(cred.id, CORROMPIDO,
                                           f"falha inesperada ao avaliar ({type(exc).__name__})")
    return out


def procurar_pfx_nao_vinculado(cad, identidade, dados_dir=None):
    """Existe em `certs/` um `.pfx` que PARECE ser desta empresa e não está
    vinculado a credencial nenhuma?

    POR QUE ISSO EXISTE
        Há empresa cadastrada em `state_clientes.json` cujo `.pfx` está no
        disco, mas que nunca entrou em `certificados.json` — falta a senha, que
        só o usuário informa pela tela. Sem esta função o diagnóstico seria
        "nenhum certificado associado", e alguém sairia procurando um arquivo
        que está ali do lado.

    O QUE ELA **NÃO** FAZ
        Não abre o arquivo, não tenta senha nenhuma, não cria vínculo e não
        escreve em lugar algum. Compara apenas os dígitos do NOME do arquivo
        com o CNPJ. Nome de arquivo não prova titularidade — por isso o
        resultado é pista para o relatório, nunca autorização para consultar.
    """
    ident = normalizar(identidade)
    if not ident.valido:
        return None
    emp = cad.obter_empresa(ident.valor)
    if emp is not None and emp.vinculos:
        return None                       # já tem vínculo: não é este o caso

    raiz = Path(dados_dir if dados_dir is not None else cad.dados_dir)
    pasta = raiz / "certs"
    if not pasta.is_dir():
        return None

    vinculados = set()
    for outra in cad.listar_empresas():
        for v in outra.vinculos:
            if v.credencial.caminho:
                vinculados.add(Path(v.credencial.caminho).name.casefold())

    for arq in sorted(pasta.glob("*.pfx")) + sorted(pasta.glob("*.p12")):
        if arq.name.casefold() in vinculados:
            continue
        if ident.valor in re.sub(r"\D", "", arq.stem):
            return arq
    return None
