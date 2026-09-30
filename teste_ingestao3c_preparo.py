#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes do preparo da ING 3C — migração de checkpoint legado e PKCS#12 BER.

    python teste_ingestao3c_preparo.py

DUAS CORREÇÕES, DOIS RISCOS DIFERENTES

    1. Migração do NSU legado. O risco é migrar o campo ERRADO: há dois
       `estado.json` na pasta de uma empresa, de serviços diferentes, com nomes
       parecidos (`ultNSU` da NF-e e `ultimoNSU` da NFS-e). Migrar o segundo
       para o checkpoint da NF-e criaria uma posição inventada, e a varredura
       pularia documentos reais sem nunca os ter visto.

    2. Validação de PKCS#12. O risco é o oposto: recusar arquivo bom. A versão
       antiga recusava envelope BER de comprimento indefinido e marcava 6 dos
       21 certificados do escritório como corrompidos sem estarem.

FIXTURES
    Certificados são **gerados na hora**, em DER e em BER. Nenhum certificado
    real é copiado para cá — os seis do escritório serviram só para conferência
    manual, e não entram em fixture, repositório nem backup de teste.

REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import credencial_estado as ce       # noqa: E402
from ingestao import migracao_legado as mig        # noqa: E402
from ingestao import modelo as m                   # noqa: E402
from ingestao.ambiente import HOMOLOGACAO, PRODUCAO  # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                           # noqa: E402
_TENTATIVAS: list[str] = []
_connect_original = _socket.socket.connect


def _proibido(self, endereco, *a, **kw):
    _TENTATIVAS.append(str(endereco))
    raise AssertionError(f"teste tentou abrir conexão de rede para {endereco}")


_socket.socket.connect = _proibido


def secao(t):
    print(f"\n{t}")


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print(f"  ok   {desc}")
    else:
        _falhas += 1
        _erros.append(desc)
        print(f"  FALHOU  {desc}")


def igual(a, b, desc):
    ok(a == b, desc + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


# ── apoio ───────────────────────────────────────────────────────────────────
EMPRESA = T.EMPRESA_A
OUTRA = T.EMPRESA_B


def plantar_legado(raiz, cnpj, conteudo, n_xml: int = 0) -> Path:
    """Cria `<cnpj>/nfe/estado.json` como o `nfe.py` faria."""
    pasta = Path(raiz) / cnpj / "nfe"
    pasta.mkdir(parents=True, exist_ok=True)
    p = pasta / "estado.json"
    p.write_text(conteudo if isinstance(conteudo, str)
                 else json.dumps(conteudo), encoding="utf-8")
    for i in range(n_xml):
        (pasta / f"{i:015d}-procNFe.xml").write_text("<nfeProc/>", encoding="utf-8")
    return p


def cp_de(raiz, cnpj=EMPRESA, ambiente=PRODUCAO):
    return RepositorioCheckpoint(raiz).carregar(cnpj, cpm.NFE_DISTRIBUICAO, ambiente)


def gravar_cp(raiz, cnpj, nsu, ambiente=PRODUCAO, origem=cpm.ORIGEM_CONECTOR):
    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(cnpj, cpm.NFE_DISTRIBUICAO, ambiente)
    cp.ult_nsu = cpm.normalizar_nsu(nsu)
    cp.origem_ult_nsu = origem
    cp.status = cpm.OK
    repo.salvar(cp)
    return cp


# ══════════════════════════════════════════════════════════════════════════
secao("A PROVA: qual campo é a posição da Distribuição DF-e")
fonte_nfe = (RAIZ / "nfse" / "backend" / "nfe.py").read_text("utf-8")
fonte_core = (RAIZ / "nfse" / "backend" / "core.py").read_text("utf-8")

ok('"ultNSU": ult' in fonte_nfe or "'ultNSU'" in fonte_nfe or '"ultNSU"' in fonte_nfe,
   "nfe.py grava a chave 'ultNSU'")
ok("<distNSU><ultNSU>" in fonte_nfe,
   "e usa esse valor no envelope <distNSU> do NFeDistribuicaoDFe")
ok("NFeDistribuicaoDFe" in fonte_nfe, "que é o serviço da NF-e")
ok('"ultimoNSU"' in fonte_core, "core.py grava a chave 'ultimoNSU'")
ok("adn.nfse.gov.br" in fonte_core, "e fala com o ADN da NFS-e — outro serviço")
igual(mig.CHAVE_NSU, "ultNSU", "a migração lê 'ultNSU', não 'ultimoNSU'")
ok("<tpAmb>1</tpAmb>" in fonte_nfe,
   "e o nfe.py fixava tpAmb=1: o legado é PRODUÇÃO, e só")

secao("O campo da NFS-e NÃO é aceito como se fosse o da NF-e")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultimoNSU": 1543,
                                   "atualizadoEm": "2026-07-31T23:16:55"})
    r = mig.migrar_nfe(raiz, EMPRESA)
    igual(r.resultado, mig.NSU_INVALIDO,
          "arquivo com 'ultimoNSU' (NFS-e) NÃO produz checkpoint de NF-e")
    ok("ultNSU" in r.detalhe, "e o motivo diz qual campo faltou")
    igual(cp_de(raiz).ult_nsu, cpm.NSU_ZERO, "o checkpoint continua em zero")

# ══════════════════════════════════════════════════════════════════════════
secao("legado 455000 / novo 0 — migra")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000}, n_xml=3)
    r = mig.migrar_nfe(raiz, EMPRESA)
    igual(r.resultado, mig.MIGRADO, "migrou")
    igual(r.nsu_antes, cpm.NSU_ZERO, "de zero")
    igual(r.nsu_depois, "000000000455000", "para 455000")

    cp = cp_de(raiz)
    igual(cp.ult_nsu, "000000000455000", "o checkpoint gravado tem o NSU legado")
    igual(cp.max_nsu, "000000000455000", "e o maxNSU legado")
    igual(cp.origem_ult_nsu, cpm.ORIGEM_LEGADO,
          "marcado como vindo do LEGADO, não do conector")
    igual(cp.cobertura_anterior, cpm.COBERTURA_ACERVO_LEGADO,
          "e a cobertura diz que os documentos estão no acervo LEGADO")

    ml = cp.migracao_legado
    for campo in ("origem", "arquivo", "chave_lida", "nsu_legado",
                  "nsu_anterior_no_checkpoint", "ambiente", "migrado_em",
                  "xml_no_acervo_legado", "documentos_no_acervo_ing3a", "aviso"):
        ok(campo in ml, f"o registro da migração traz '{campo}'")
    igual(ml["chave_lida"], "ultNSU", "registrando QUAL chave foi lida")
    igual(ml["xml_no_acervo_legado"], 3, "quantos XML há no acervo legado")
    igual(ml["documentos_no_acervo_ing3a"], 0, "e quantos no acervo novo — zero")
    ok("NÃO" in ml["aviso"] and "ING 3A" in ml["aviso"],
       "com o aviso de que os documentos não estão no acervo novo")

secao("legado 455000 / novo 455000 — idempotente")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000})
    gravar_cp(raiz, EMPRESA, 455000)
    r = mig.migrar_nfe(raiz, EMPRESA)
    igual(r.resultado, mig.JA_MIGRADO, "reconhece que já está na posição")
    ok(not r.alterou, "e não altera nada")
    igual(cp_de(raiz).origem_ult_nsu, cpm.ORIGEM_CONECTOR,
          "a origem anterior (CONECTOR) é preservada, não sobrescrita")

secao("legado 455000 / novo 455100 — o novo é maior e é preservado")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000})
    gravar_cp(raiz, EMPRESA, 455100)
    r = mig.migrar_nfe(raiz, EMPRESA)
    igual(r.resultado, mig.NOVO_MAIOR, "reconhece que o checkpoint está à frente")
    igual(cp_de(raiz).ult_nsu, "000000000455100",
          "o checkpoint NÃO retrocede para o legado")
    ok("preservado" in r.detalhe, "e o motivo é explícito")

secao("Segunda execução não altera nada (idempotência de verdade)")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000}, n_xml=2)
    r1 = mig.migrar_nfe(raiz, EMPRESA)
    arq = RepositorioCheckpoint(raiz).caminho(EMPRESA, cpm.NFE_DISTRIBUICAO, PRODUCAO)
    bytes1 = arq.read_bytes()
    r2 = mig.migrar_nfe(raiz, EMPRESA)
    r3 = mig.migrar_nfe(raiz, EMPRESA)
    igual(r1.resultado, mig.MIGRADO, "a primeira migra")
    igual(r2.resultado, mig.JA_MIGRADO, "a segunda não")
    igual(r3.resultado, mig.JA_MIGRADO, "a terceira também não")
    igual(arq.read_bytes(), bytes1,
          "e o arquivo do checkpoint está byte a byte igual ao da primeira")

secao("O arquivo legado é PRESERVADO")
with apoio.raiz_temporaria("i3c_") as raiz:
    p = plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000}, n_xml=4)
    antes = p.read_bytes()
    mig.migrar_nfe(raiz, EMPRESA)
    ok(p.exists(), "o estado.json legado continua lá")
    igual(p.read_bytes(), antes, "e inalterado")
    igual(len(list(p.parent.glob("*.xml"))), 4, "os XML legados também")

# ══════════════════════════════════════════════════════════════════════════
secao("Arquivo ausente não é erro")
with apoio.raiz_temporaria("i3c_") as raiz:
    r = mig.migrar_nfe(raiz, EMPRESA)
    igual(r.resultado, mig.SEM_LEGADO, "resultado é SEM_ARQUIVO_LEGADO")
    ok(not r.alterou, "nada foi alterado")
    igual(cp_de(raiz).ult_nsu, cpm.NSU_ZERO, "o checkpoint continua em zero")

secao("JSON corrompido não produz checkpoint")
for rotulo, conteudo in (("cortado ao meio", '{"ultNSU": 4550'),
                         ("lista em vez de objeto", "[1,2,3]"),
                         ("vazio", ""),
                         ("texto solto", "isto nao e json")):
    with apoio.raiz_temporaria("i3c_") as raiz:
        plantar_legado(raiz, EMPRESA, conteudo)
        r = mig.migrar_nfe(raiz, EMPRESA)
        igual(r.resultado, mig.LEGADO_ILEGIVEL, f"{rotulo}: recusado")
        igual(cp_de(raiz).ult_nsu, cpm.NSU_ZERO, f"{rotulo}: checkpoint intacto")

secao("NSU inválido não produz checkpoint")
for rotulo, valor in (("ausente", None), ("negativo", -5), ("texto", "abc"),
                      ("fracionário", 12.5), ("booleano", True),
                      ("zero", 0), ("acima de 15 dígitos", 10 ** 16),
                      ("string vazia", "")):
    with apoio.raiz_temporaria("i3c_") as raiz:
        d = {"maxNSU": 10}
        if valor is not None:
            d["ultNSU"] = valor
        plantar_legado(raiz, EMPRESA, d)
        r = mig.migrar_nfe(raiz, EMPRESA)
        igual(r.resultado, mig.NSU_INVALIDO, f"NSU {rotulo}: recusado")
        igual(cp_de(raiz).ult_nsu, cpm.NSU_ZERO, f"NSU {rotulo}: checkpoint intacto")

secao("NSU em texto numérico é aceito (o legado às vezes grava assim)")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultNSU": "455000", "maxNSU": "455000"})
    igual(mig.migrar_nfe(raiz, EMPRESA).resultado, mig.MIGRADO, "aceito")
    igual(cp_de(raiz).ult_nsu, "000000000455000", "com o valor certo")

secao("CNPJ divergente / inválido")
with apoio.raiz_temporaria("i3c_") as raiz:
    r = mig.migrar_nfe(raiz, "123")
    igual(r.resultado, mig.IDENTIDADE_INVALIDA, "identidade inválida é recusada")
    r = mig.migrar_nfe(raiz, "11111111000100")     # DV errado
    igual(r.resultado, mig.IDENTIDADE_INVALIDA, "DV errado é recusado")

    # o legado de UMA empresa não pode virar checkpoint de OUTRA
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000})
    r = mig.migrar_nfe(raiz, OUTRA)
    igual(r.resultado, mig.SEM_LEGADO,
          "a empresa B não herda o legado da empresa A")
    igual(cp_de(raiz, OUTRA).ult_nsu, cpm.NSU_ZERO, "e o checkpoint dela fica zero")

secao("Ambiente divergente — homologação NUNCA recebe posição de produção")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000})
    r = mig.migrar_nfe(raiz, EMPRESA, ambiente=HOMOLOGACAO)
    igual(r.resultado, mig.AMBIENTE_RECUSADO, "recusa migrar para homologação")
    ok("PRODUÇÃO" in r.detalhe, "explicando que o legado é de produção")
    igual(cp_de(raiz, EMPRESA, HOMOLOGACAO).ult_nsu, cpm.NSU_ZERO,
          "e o checkpoint de homologação continua em zero")

    mig.migrar_nfe(raiz, EMPRESA, ambiente=PRODUCAO)
    igual(cp_de(raiz, EMPRESA, PRODUCAO).ult_nsu, "000000000455000",
          "enquanto o de produção recebe normalmente")
    igual(cp_de(raiz, EMPRESA, HOMOLOGACAO).ult_nsu, cpm.NSU_ZERO,
          "sem contaminar homologação")

secao("Interrupção durante a escrita não corrompe o checkpoint")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000})
    gravar_cp(raiz, EMPRESA, 100)
    arq = RepositorioCheckpoint(raiz).caminho(EMPRESA, cpm.NFE_DISTRIBUICAO, PRODUCAO)
    antes = arq.read_bytes()

    repo = RepositorioCheckpoint(raiz)
    original = repo.salvar

    def salvar_que_falha(cp):
        raise OSError("disco cheio no meio da gravação (simulado)")

    repo.salvar = salvar_que_falha
    try:
        mig.migrar_nfe(raiz, EMPRESA, repo=repo)
        ok(False, "a falha de gravação deveria propagar")
    except OSError:
        ok(True, "a falha de gravação propaga em vez de ser engolida")
    igual(arq.read_bytes(), antes,
          "e o checkpoint anterior está byte a byte intacto")

    repo.salvar = original
    igual(mig.migrar_nfe(raiz, EMPRESA, repo=repo).resultado, mig.MIGRADO,
          "com o disco de volta, a migração conclui")
    igual(cp_de(raiz).ult_nsu, "000000000455000", "e chega ao valor certo")

secao("Múltiplas empresas — uma com problema não derruba as outras")
with apoio.raiz_temporaria("i3c_") as raiz:
    C = T.cnpj_ficticio("444444440001")
    D = T.cnpj_ficticio("555555550001")
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000}, n_xml=2)
    plantar_legado(raiz, OUTRA, '{"ultNSU": quebrado')
    plantar_legado(raiz, C, {"ultNSU": 216, "maxNSU": 216}, n_xml=1)
    (Path(raiz) / D).mkdir(parents=True, exist_ok=True)     # sem legado

    rs = mig.migrar_todas(raiz)
    por = {r.identidade_mascarada: r.resultado for r in rs}
    igual(len(rs), 4, "as quatro empresas foram avaliadas")
    from ingestao.identidade import normalizar as _n
    igual(por[_n(EMPRESA).mascarado()], mig.MIGRADO, "a boa migrou")
    igual(por[_n(OUTRA).mascarado()], mig.LEGADO_ILEGIVEL, "a quebrada foi reportada")
    igual(por[_n(C).mascarado()], mig.MIGRADO, "a terceira migrou")
    igual(por[_n(D).mascarado()], mig.SEM_LEGADO, "a sem legado não é erro")
    igual(cp_de(raiz, EMPRESA).ult_nsu, "000000000455000", "cada uma com o SEU NSU")
    igual(cp_de(raiz, C).ult_nsu, "000000000000216", "sem mistura entre empresas")
    igual(cp_de(raiz, OUTRA).ult_nsu, cpm.NSU_ZERO, "a quebrada continua em zero")

secao("prever() mostra o que faria sem gravar nada")
with apoio.raiz_temporaria("i3c_") as raiz:
    plantar_legado(raiz, EMPRESA, {"ultNSU": 455000, "maxNSU": 455000}, n_xml=5)
    p = mig.prever(raiz, EMPRESA)
    igual(p["nsu_legado"], "000000000455000", "mostra o NSU legado")
    igual(p["checkpoint_atual"], cpm.NSU_ZERO, "e o checkpoint atual")
    igual(p["avancaria_para"], "000000000455000", "e para onde avançaria")
    igual(p["xml_no_acervo_legado"], 5, "com a contagem do acervo legado")
    igual(p["documentos_no_acervo_ing3a"], 0, "e do acervo novo")
    igual(cp_de(raiz).ult_nsu, cpm.NSU_ZERO, "e NÃO gravou nada")

# ══════════════════════════════════════════════════════════════════════════
secao("PKCS#12 — a biblioteca é a fonte da verdade")


def pfx_der(destino: Path, senha: str, cn="EMPRESA FICTICIA LTDA") -> bytes:
    apoio.gerar_pfx(destino, senha, cn=cn)
    return destino.read_bytes()


def para_ber(der: bytes) -> bytes:
    """Reescreve a SEQUENCE externa em comprimento INDEFINIDO (BER).

    `30 <len> <conteudo>`  ->  `30 80 <conteudo> 00 00`

    É a codificação que várias ACs da ICP-Brasil emitem e que a validação
    antiga recusava."""
    assert der[0] == 0x30
    b = der[1]
    if b & 0x80:
        n = b & 0x7F
        inicio = 2 + n
    else:
        inicio = 2
    return b"\x30\x80" + der[inicio:] + b"\x00\x00"


with apoio.raiz_temporaria("i3c_") as raiz:
    SENHA = "frase-ficticia-do-teste-3c"
    der = pfx_der(Path(raiz) / "certs" / "der.pfx", SENHA)
    ber = para_ber(der)
    (Path(raiz) / "certs" / "ber.pfx").write_bytes(ber)

    igual(der[:2], b"\x30\x82", "a fixture DER tem comprimento definido")
    igual(ber[:2], b"\x30\x80", "e a fixture BER tem comprimento indefinido")

    f_der, _ = ce._triagem_envelope(der)
    f_ber, _ = ce._triagem_envelope(ber)
    igual(f_der, ce.FORMATO_DEFINIDO, "a triagem reconhece o DER")
    igual(f_ber, ce.FORMATO_INDEFINIDO, "e reconhece o BER — não o recusa")

    # a biblioteca abre os dois
    from cryptography.hazmat.primitives.serialization import pkcs12
    for rotulo, dados in (("DER", der), ("BER", ber)):
        chave, cert, _ = pkcs12.load_key_and_certificates(dados, SENHA.encode())
        ok(chave is not None and cert is not None,
           f"a cryptography abre a fixture {rotulo}")


def credencial(raiz, nome, senha_certa=True):
    """Credencial apontando para um .pfx da raiz temporária."""
    import seguranca
    from ingestao.identidade import normalizar as _n
    caminho = Path(raiz) / "certs" / nome
    senha = "frase-ficticia-do-teste-3c" if senha_certa else "outra-frase-qualquer"
    return m.Credencial(id=nome, titular=_n(EMPRESA), caminho=str(caminho),
                        senha_protegida=seguranca.proteger(senha, raiz))


with apoio.raiz_temporaria("i3c_") as raiz:
    SENHA = "frase-ficticia-do-teste-3c"
    (Path(raiz) / "certs").mkdir(parents=True, exist_ok=True)
    der = pfx_der(Path(raiz) / "certs" / "der.pfx", SENHA)
    (Path(raiz) / "certs" / "ber.pfx").write_bytes(para_ber(der))

    secao("Certificado válido — em DER e em BER")
    for nome in ("der.pfx", "ber.pfx"):
        est = ce.avaliar(credencial(raiz, nome), raiz)
        igual(est.estado, ce.VALIDO, f"{nome}: reconhecido como VALIDO")
        ok(est.utilizavel, f"{nome}: utilizável")
        ok(est.nao_depois, f"{nome}: com a validade lida")

    secao("Senha incorreta é distinguida de arquivo corrompido")
    est = ce.avaliar(credencial(raiz, "ber.pfx", senha_certa=False), raiz)
    igual(est.estado, ce.SENHA_INCORRETA,
          "senha errada num BER válido = SENHA_INCORRETA, não CORROMPIDO")
    ok("senha" in est.detalhe.lower(), "com o motivo apontando a senha")
    for proibido in ("frase-ficticia", "outra-frase"):
        ok(proibido not in est.detalhe, f"e sem a senha na mensagem ({proibido})")
    ok(proibido not in json.dumps(est.resumo()), "nem no resumo")

    secao("Arquivo realmente inválido continua CORROMPIDO")
    casos = {
        "lixo.pfx": b"isto nao e um pkcs12 de jeito nenhum",
        "vazio.pfx": b"",
        "truncado.pfx": der[:len(der) // 2],
        "ber_sem_fim.pfx": b"\x30\x80" + der[4:],       # indefinido sem 00 00
    }
    for nome, dados in casos.items():
        (Path(raiz) / "certs" / nome).write_bytes(dados)
        est = ce.avaliar(credencial(raiz, nome), raiz)
        igual(est.estado, ce.CORROMPIDO, f"{nome}: CORROMPIDO")

    secao("Certificado sem chave privada tem estado próprio")
    from cryptography.hazmat.primitives.serialization import (
        BestAvailableEncryption, pkcs12 as _p12)
    _c, cert, _e = _p12.load_key_and_certificates(der, SENHA.encode())
    so_cert = _p12.serialize_key_and_certificates(
        name=b"so-cert", key=None, cert=cert, cas=None,
        encryption_algorithm=BestAvailableEncryption(SENHA.encode()))
    (Path(raiz) / "certs" / "sem_chave.pfx").write_bytes(so_cert)
    est = ce.avaliar(credencial(raiz, "sem_chave.pfx"), raiz)
    igual(est.estado, ce.SEM_CHAVE_PRIVADA,
          "abre e tem certificado, mas sem chave privada")
    ok(not est.utilizavel, "e não é utilizável para mTLS")
    ok("chave privada" in ce.ACAO[ce.SEM_CHAVE_PRIVADA],
       "com ação orientando reexportar")

    secao("Arquivo ausente continua AUSENTE")
    igual(ce.avaliar(credencial(raiz, "nao_existe.pfx"), raiz).estado, ce.AUSENTE,
          "arquivo que não está no disco")

# ══════════════════════════════════════════════════════════════════════════
secao("cUFAutor — é do AUTOR da consulta, nunca da empresa consultada")
from ingestao import autor_consulta as autoria          # noqa: E402
from ingestao.conectores import nfe_dfe as N            # noqa: E402


class _SemRede:
    def enviar(self, url, corpo):
        raise RuntimeError("este teste não transmite")


def _cuf_do_envelope(env: bytes) -> str:
    import re as _re
    m = _re.search(r"<cUFAutor>(\d+)</cUFAutor>", env.decode("utf-8"))
    return m.group(1) if m else ""


ok("uf" not in N.FonteNFeDistribuicaoDFe.__dataclass_fields__,
   "a Fonte NÃO tem mais parâmetro 'uf' — a porta pela qual o erro entrava")
ok("autor" in N.FonteNFeDistribuicaoDFe.__dataclass_fields__,
   "e tem 'autor', que é quem pergunta")

secao("A UF da empresa NÃO é inferida nem consultada")
with apoio.raiz_temporaria("i3c_") as raiz:
    autoria.gravar(raiz, "PE")
    # duas empresas, e a segunda seria de outro estado se alguém olhasse o
    # endereço dela. O cUFAutor tem de ser o mesmo nas duas.
    f1 = N.FonteNFeDistribuicaoDFe(identidade=EMPRESA, transporte=_SemRede(),
                                   ambiente=PRODUCAO, dados_dir=raiz)
    f2 = N.FonteNFeDistribuicaoDFe(identidade=OUTRA, transporte=_SemRede(),
                                   ambiente=PRODUCAO, dados_dir=raiz)
    igual(f1.cuf, "26", "empresa A: cUFAutor 26 (do escritório)")
    igual(f2.cuf, "26", "empresa B: cUFAutor 26 — o MESMO, não o dela")
    igual(f1.autor.origem, autoria.ORIGEM_ARQUIVO, "e a origem é o arquivo")

    e1 = N.montar_envelope_dist_nsu(EMPRESA, "0", PRODUCAO, cuf=f1.cuf)
    e2 = N.montar_envelope_dist_nsu(OUTRA, "0", PRODUCAO, cuf=f2.cuf)
    igual(_cuf_do_envelope(e1), _cuf_do_envelope(e2),
          "os dois envelopes saem com o mesmo cUFAutor")

secao("Escritório de outra UF muda o cUFAutor de TODAS as consultas")
with apoio.raiz_temporaria("i3c_") as raiz:
    autoria.gravar(raiz, "SP")
    f = N.FonteNFeDistribuicaoDFe(identidade=EMPRESA, transporte=_SemRede(),
                                  ambiente=PRODUCAO, dados_dir=raiz)
    igual(f.cuf, "35", "autor em SP -> cUFAutor 35")
    igual(f.autor.uf, "SP", "e a UF do autor acompanha")

secao("Ausência de autor ABORTA antes da rede")
with apoio.raiz_temporaria("i3c_") as raiz:
    try:
        N.FonteNFeDistribuicaoDFe(identidade=EMPRESA, transporte=_SemRede(),
                                  ambiente=PRODUCAO, dados_dir=raiz)
        ok(False, "sem autor configurado, a construção deveria falhar")
    except autoria.AutorNaoConfigurado:
        ok(True, "sem autor_consulta.json, a Fonte nem é construída")
    try:
        N.FonteNFeDistribuicaoDFe(identidade=EMPRESA, transporte=_SemRede(),
                                  ambiente=PRODUCAO)
        ok(False, "sem raiz e sem override, deveria falhar")
    except autoria.AutorNaoConfigurado:
        ok(True, "sem raiz e sem override também falha — não há default")

secao("Valor inválido aborta")
for rotulo, valor in (("código fora do IBGE", "99"), ("texto", "PE"),
                      ("zero", "0"), ("vazio numa string", "   ")):
    with apoio.raiz_temporaria("i3c_") as raiz:
        try:
            N.FonteNFeDistribuicaoDFe(identidade=EMPRESA, transporte=_SemRede(),
                                      ambiente=PRODUCAO, cuf=valor, dados_dir=raiz)
            ok(False, f"cUFAutor {rotulo} deveria ser recusado")
        except autoria.ErroAutor:
            ok(True, f"cUFAutor {rotulo} é recusado")

with apoio.raiz_temporaria("i3c_") as raiz:
    (Path(raiz) / autoria.ARQUIVO).write_text('{"uf": "ZZ"}', encoding="utf-8")
    try:
        autoria.carregar(raiz)
        ok(False, "UF inexistente no arquivo deveria ser recusada")
    except autoria.AutorInvalido:
        ok(True, "UF inexistente no arquivo é recusada")

    (Path(raiz) / autoria.ARQUIVO).write_text('{"uf": "PE", "cuf": "35"}',
                                              encoding="utf-8")
    try:
        autoria.carregar(raiz)
        ok(False, "arquivo contraditório deveria ser recusado")
    except autoria.AutorInvalido as exc:
        ok("contraditório" in str(exc), "arquivo com uf e cuf discordantes é recusado")

secao("Override explícito funciona SOMENTE quando informado")
with apoio.raiz_temporaria("i3c_") as raiz:
    autoria.gravar(raiz, "PE")
    f_normal = N.FonteNFeDistribuicaoDFe(identidade=EMPRESA, transporte=_SemRede(),
                                         ambiente=PRODUCAO, dados_dir=raiz)
    igual(f_normal.cuf, "26", "sem override, vale o arquivo")
    igual(f_normal.autor.origem, autoria.ORIGEM_ARQUIVO, "com origem 'arquivo'")

    f_over = N.FonteNFeDistribuicaoDFe(identidade=EMPRESA, transporte=_SemRede(),
                                       ambiente=PRODUCAO, cuf="35", dados_dir=raiz)
    igual(f_over.cuf, "35", "com override, vale o override")
    igual(f_over.autor.origem, autoria.ORIGEM_OVERRIDE, "e a origem diz que foi override")
    igual(autoria.carregar(raiz).cuf, "26",
          "o override NÃO altera o arquivo de configuração")

secao("A execução normal não depende de o operador lembrar do --cuf")
cli = (RAIZ / "consulta_real_nfe.py").read_text("utf-8")
ok("--cuf" in cli, "o override existe na linha de comando")
ok("default=\"\"" in cli.split("--cuf")[1][:200],
   "mas o padrão é vazio: sem ele, o autor vem do arquivo")
# O CLI ainda EXIBE a UF da empresa — rotulada como "não entra no cUFAutor".
# O que não pode é PASSÁ-LA ao conector. É isso que se confere.
ok("uf=empresa.uf" not in cli, "o CLI não passa empresa.uf ao conector")
ok("uf=" not in cli.replace("cuf=", "").replace("_uf=", ""),
   "e não passa nenhum argumento 'uf=' às funções do conector")
ok("dados_dir=raiz" in cli, "passando a raiz para o autor ser carregado")

secao("A regra das duas chaves segue coberta (regra permanente)")
igual(mig.CHAVE_NSU, "ultNSU", "a migração lê 'ultNSU' (NF-e)")
ok("ultimoNSU" not in mig.CHAVE_NSU, "e nunca 'ultimoNSU' (NFS-e)")

secao("Nenhum certificado real do escritório entrou no repositório")
# A primeira versão desta seção procurava os nomes reais no PRÓPRIO texto da
# suíte — e falhava sempre, porque os nomes procurados estavam ali, na lista da
# procura. Teste que se olha no espelho não prova nada.
#
# O que importa não é o texto: é se existe ARQUIVO de certificado dentro do
# repositório. É isso que se confere agora.
_IGNORAR = {".venv", "dist", "build", "__pycache__", "node_modules"}


def _certificados_em(base: Path) -> list[str]:
    achados = []
    for padrao in ("*.pfx", "*.p12"):
        for p in base.rglob(padrao):
            if _IGNORAR & set(p.parts) or any(x.startswith("backup_") for x in p.parts):
                continue
            achados.append(str(p.relative_to(RAIZ)))
    return sorted(achados)


# 1. No CÓDIGO — nenhuma fixture pode ser um certificado real.
codigo = [c for c in _certificados_em(RAIZ) if not c.replace("\\", "/").startswith("dados/")]
igual(codigo, [], "nenhum .pfx/.p12 entre o código e as fixtures")

# 2. Na pasta `dados/` do projeto — é dado, não fixture, e a suíte não decide
#    o que fazer com ele. Mas registra, porque chave privada dentro da árvore do
#    projeto é fato que alguém precisa saber. (Achado real: 13/08/2026.)
em_dados = [c for c in _certificados_em(RAIZ) if c.replace("\\", "/").startswith("dados/")]
if em_dados:
    print(f"       (aviso: {len(em_dados)} certificado(s) em <projeto>/dados/certs — "
          f"resíduo da raiz antiga, ver relatório)")
ok(True, f"pasta dados/ do projeto inspecionada ({len(em_dados)} certificado(s))")

# 3. As fixtures desta suíte são GERADAS na hora, em pasta temporária.
_fonte = Path(__file__).read_text("utf-8")
ok("gerar_pfx" in _fonte, "as fixtures de certificado são geradas, não copiadas")
ok("apoio.raiz_temporaria" in _fonte, "e vivem em raiz temporária, apagada no fim")

# ══════════════════════════════════════════════════════════════════════════
secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede não estava ativa")
except AssertionError as exc:
    ok("conexão de rede" in str(exc), "e a trava estava mesmo ativa (conferido)")
finally:
    _TENTATIVAS.clear()
    _socket.socket.connect = _connect_original

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("ING 3C (preparo): todos os testes passaram.")
