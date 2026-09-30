#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da ING 2 — checkpoint e motor de distribuição.

    python teste_ingestao2.py

REGRAS DESTE ARQUIVO
    1. ZERO rede. E não é promessa: o `socket` é bloqueado logo no início deste
       arquivo, então qualquer tentativa de falar com SEFAZ/ADN/Portal Nacional
       quebra o teste em vez de sair escondida.
    2. ZERO acesso à raiz real. Tudo dentro de `teste_apoio.raiz_temporaria()`,
       que ainda liga a trava do `fiscale_dados`.
    3. Nada de segredo impresso.
    4. Toda pasta temporária se apaga sozinha no fim do processo.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

# ── BLOQUEIO DE REDE (antes de qualquer import do projeto) ──────────────────
class RedeProibidaNoTeste(AssertionError):
    """Um teste tentou abrir conexão. Isso é falha, não é aviso."""


_tentativas_de_rede: list[str] = []
_socket_original = socket.socket


class _SocketBloqueado(socket.socket):
    def connect(self, address, *a, **kw):          # noqa: A003
        _tentativas_de_rede.append(str(address))
        raise RedeProibidaNoTeste(f"tentativa de conexão para {address}")

    def connect_ex(self, address, *a, **kw):
        _tentativas_de_rede.append(str(address))
        raise RedeProibidaNoTeste(f"tentativa de conexão para {address}")


socket.socket = _SocketBloqueado                    # type: ignore[misc]
socket.create_connection = lambda *a, **kw: (_ for _ in ()).throw(  # type: ignore[assignment]
    RedeProibidaNoTeste(f"tentativa de conexão para {a[0] if a else '?'}"))

import teste_apoio as ta                            # noqa: E402
import fiscale_dados as fd                          # noqa: E402
from ingestao import (                              # noqa: E402
    acervo as acv, ambiente as amb, checkpoint as cpm, contratos,
    credencial_estado as ce, distribuicao as dist, trava as trv,
)

_ok = _falhas = 0
_erros: list[str] = []


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
        print(f"  FALHA {desc}")


def igual(a, b, desc):
    ok(a == b, desc if a == b else f"{desc}  (obtido={a!r} esperado={b!r})")


def levanta(fn, excecao, desc):
    try:
        fn()
    except excecao:
        ok(True, desc)
    except Exception as exc:
        ok(False, f"{desc} (levantou {type(exc).__name__} em vez de {excecao.__name__})")
    else:
        ok(False, f"{desc} (não levantou nada)")


def _dv_cnpj(base12: str) -> str:
    c = base12
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        r = sum(int(d) * p for d, p in zip(c[:tam], pesos)) % 11
        c += str(0 if r < 2 else 11 - r)
    return c


def _dv_cpf(base9: str) -> str:
    c = base9
    for tam in (9, 10):
        r = sum(int(d) * (tam + 1 - i) for i, d in enumerate(c[:tam])) * 10 % 11
        c += str(0 if r == 10 else r)
    return c


EMP_A = _dv_cnpj("049990010001")
EMP_B = _dv_cnpj("278657570001")
PF_C = _dv_cpf("111444777")
CTE = cpm.CTE_DISTRIBUICAO
NFE = cpm.NFE_DISTRIBUICAO


# ── dublês ──────────────────────────────────────────────────────────────────
def doc(nsu: int, chave: str = "") -> dist.DocumentoBruto:
    return dist.DocumentoBruto(nsu=str(nsu), conteudo=f"<doc nsu={nsu}/>".encode(),
                               schema="procCTe_v2.00.xsd", chave=chave)


class FonteFake:
    """Serviço de mentira. Devolve lotes preparados; nunca abre conexão."""
    capacidades = frozenset({dist.CAP_DIST_NSU, dist.CAP_CONS_NSU})

    def __init__(self, servico=CTE, ambiente=amb.PRODUCAO, lotes=(), erro=None,
                 erro_no_lote=None):
        self.servico, self.ambiente = servico, ambiente
        self.lotes, self.erro, self.erro_no_lote = list(lotes), erro, erro_no_lote
        self.chamadas: list[str] = []

    def consultar(self, desde_nsu: str) -> dist.Lote:
        self.chamadas.append(desde_nsu)
        if self.erro is not None:
            raise self.erro
        if self.erro_no_lote is not None and len(self.chamadas) == self.erro_no_lote[0]:
            raise self.erro_no_lote[1]
        if not self.lotes:
            return dist.Lote(sem_documentos=True, motivo="nada novo")
        return self.lotes.pop(0)


class FonteNFe(FonteFake):
    """A NF-e TEM consulta por chave — e por isso declara a capacidade."""
    capacidades = frozenset({dist.CAP_DIST_NSU, dist.CAP_CONS_NSU, dist.CAP_CONS_CHAVE})

    def __init__(self, **kw):
        kw.setdefault("servico", NFE)
        super().__init__(**kw)

    def consultar_por_chave(self, chave: str) -> dist.Lote:
        return dist.Lote(documentos=(doc(1, chave=chave),), ult_nsu="1")


class FonteCTe(FonteFake):
    """O CT-e NÃO tem consulta por chave: nem capacidade, nem método."""
    capacidades = frozenset({dist.CAP_DIST_NSU, dist.CAP_CONS_NSU})


class AcervoFake:
    """Guarda em disco. Sabe falhar de propósito, nos dois pontos que importam."""

    def __init__(self, pasta: Path, falhar_ao_preservar_no=None,
                 confirmar=True, falhar_ao_confirmar=False):
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.falhar_ao_preservar_no = falhar_ao_preservar_no
        self.confirmar_resposta, self.falhar_ao_confirmar = confirmar, falhar_ao_confirmar
        self.gravados: list[str] = []
        self.confirmacoes = 0

    def preservar(self, d: dist.DocumentoBruto) -> None:
        if self.falhar_ao_preservar_no is not None and \
                len(self.gravados) == self.falhar_ao_preservar_no:
            raise OSError("disco cheio (simulado)")
        (self.pasta / f"{d.nsu}.xml").write_bytes(d.conteudo)
        self.gravados.append(d.nsu)

    def confirmar(self, documentos) -> bool:
        self.confirmacoes += 1
        if self.falhar_ao_confirmar:
            raise OSError("fsync falhou (simulado)")
        return self.confirmar_resposta


# ══ 1. Checkpoint: inexistente, criação, leitura/escrita ════════════════════
secao("1. Checkpoint inexistente, criação inicial, ida e volta")
with ta.raiz_temporaria("ing2_cp_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    ok(not repo.existe(EMP_A, CTE, amb.PRODUCAO), "não existe antes da primeira execução")
    cp = repo.carregar(EMP_A, CTE, amb.PRODUCAO)
    ok(cp.nunca_rodou, "carregar devolve um checkpoint novo, marcado como nunca executado")
    igual(cp.ult_nsu, cpm.NSU_ZERO, "com ultNSU zerado em 15 dígitos")
    igual(cp.status, cpm.NUNCA_EXECUTADO, "e status NUNCA_EXECUTADO")
    ok(not repo.existe(EMP_A, CTE, amb.PRODUCAO), "e NÃO grava arquivo só por ler")

    cp.ult_nsu, cp.max_nsu, cp.status = "42", "100", cpm.OK
    alvo = repo.salvar(cp)
    ok(alvo.is_file(), "salvar cria o arquivo")
    igual(alvo.name, f"{CTE}.producao.json", "com nome (serviço).(ambiente).json")
    ok(alvo.parent.parent.name == EMP_A, "dentro da pasta da empresa")

    lido = repo.carregar(EMP_A, CTE, amb.PRODUCAO)
    igual(lido.ult_nsu, "000000000000042", "ultNSU volta normalizado em 15 dígitos")
    igual(lido.max_nsu, "000000000000100", "maxNSU idem")
    igual(lido.status, cpm.OK, "status preservado")
    igual(lido.pendentes, 58, "calcula quantos NSU faltam")
    ok(not lido.em_dia, "e sabe que não está em dia")
    ok(lido.criado_em and lido.atualizado_em, "carimbos de auditoria gravados")
    igual(lido.versao, cpm.VERSAO_FORMATO, "versão do formato gravada")

    bruto = json.loads(alvo.read_text("utf-8"))
    proibidos = [k for k in bruto if "senha" in k.lower() or "cert" in k.lower()
                 or "token" in k.lower()]
    igual(proibidos, [], "o checkpoint NÃO guarda senha, certificado nem token")


# ══ 2. Separação: empresa, CPF/CNPJ, serviço, ambiente ══════════════════════
secao("2. A chave é (identidade, serviço, ambiente) — e separa de verdade")
with ta.raiz_temporaria("ing2_sep_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)

    def grava(ident, srv, ambiente, nsu):
        cp = repo.carregar(ident, srv, ambiente)
        cp.ult_nsu = str(nsu)
        repo.salvar(cp)

    grava(EMP_A, CTE, amb.PRODUCAO, 10)
    grava(EMP_B, CTE, amb.PRODUCAO, 20)
    grava(PF_C, CTE, amb.PRODUCAO, 30)
    grava(EMP_A, NFE, amb.PRODUCAO, 40)
    grava(EMP_A, CTE, amb.HOMOLOGACAO, 50)

    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 10, "empresa A isolada")
    igual(cpm.nsu_int(repo.carregar(EMP_B, CTE, amb.PRODUCAO).ult_nsu), 20, "empresa B isolada")
    igual(cpm.nsu_int(repo.carregar(PF_C, CTE, amb.PRODUCAO).ult_nsu), 30,
          "titular CPF tem checkpoint próprio (não é só CNPJ)")
    igual(cpm.nsu_int(repo.carregar(EMP_A, NFE, amb.PRODUCAO).ult_nsu), 40,
          "serviço diferente, checkpoint diferente")
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.HOMOLOGACAO).ult_nsu), 50,
          "*** homologação NÃO compartilha NSU com produção")

    ok(repo.caminho(EMP_A, CTE, amb.PRODUCAO) != repo.caminho(EMP_A, CTE, amb.HOMOLOGACAO),
       "os caminhos de produção e homologação são distintos")
    ok(str(repo.caminho(EMP_B, CTE, amb.PRODUCAO)).find(EMP_A) < 0,
       "o caminho de uma empresa não contém a identidade de outra")
    igual(len(repo.listar()), 5, "listar() enxerga todos os checkpoints")
    igual(len(repo.listar(EMP_A)), 3, "e sabe filtrar por empresa")
    levanta(lambda: repo.carregar("123", CTE, amb.PRODUCAO), ValueError,
            "identidade inválida é recusada")
    levanta(lambda: repo.carregar(EMP_A, "INVENTADO", amb.PRODUCAO), ValueError,
            "serviço desconhecido é recusado")

    # conteúdo com chave trocada = corrupção, nunca leitura cruzada
    p = repo.caminho(EMP_A, CTE, amb.PRODUCAO)
    d = json.loads(p.read_text("utf-8")); d["ambiente"] = "homologacao"
    p.write_text(json.dumps(d), "utf-8")
    levanta(lambda: repo.carregar(EMP_A, CTE, amb.PRODUCAO), cpm.CheckpointCorrompido,
            "conteúdo de outro ambiente no arquivo é tratado como corrupção")


# ══ 3. Escrita atômica ══════════════════════════════════════════════════════
secao("3. Escrita atômica — nunca sobrescrever o válido com lixo")
with ta.raiz_temporaria("ing2_atom_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    cp = repo.carregar(EMP_A, CTE, amb.PRODUCAO)
    cp.ult_nsu = "500"
    alvo = repo.salvar(cp)
    bom = alvo.read_text("utf-8")

    # o replace falha no meio: o arquivo anterior tem de sobreviver intacto
    original = os.replace
    def replace_quebrado(a, b):
        raise OSError("queda simulada durante a troca")
    os.replace = replace_quebrado
    try:
        cp.ult_nsu = "999"
        levanta(lambda: repo.salvar(cp), OSError, "falha na troca propaga o erro")
    finally:
        os.replace = original

    igual(alvo.read_text("utf-8"), bom, "*** o checkpoint anterior ficou INTACTO")
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 500,
          "e continua legível com o valor antigo")
    sobras = list(alvo.parent.glob(".cp-*.tmp"))
    igual(sobras, [], "nenhum temporário de escrita ficou para trás")

    # gravação normal não deixa temporário e é lida de volta
    cp.ult_nsu = "777"
    repo.salvar(cp)
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 777,
          "a gravação seguinte funciona normalmente")
    igual(list(alvo.parent.glob(".cp-*.tmp")), [], "e também não deixa temporário")


# ══ 4. Corrupção: evidência preservada, NUNCA zerar ═════════════════════════
secao("4. Corrupção — detecta, preserva evidência e exige decisão")
with ta.raiz_temporaria("ing2_corr_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    cp = repo.carregar(EMP_A, CTE, amb.PRODUCAO)
    cp.ult_nsu = "12345"
    alvo = repo.salvar(cp)

    for conteudo, rotulo in ((b"", "arquivo vazio"),
                             (b"{ isto nao e json", "JSON truncado"),
                             (b'{"foo": 1}', "JSON sem os campos obrigatórios")):
        alvo.write_bytes(conteudo)
        levanta(lambda: repo.carregar(EMP_A, CTE, amb.PRODUCAO),
                cpm.CheckpointCorrompido, f"detecta {rotulo}")

    ev = repo.isolar_corrompido(EMP_A, CTE, amb.PRODUCAO)
    ok(ev is not None and ev.is_file(), "a evidência é preservada com carimbo de tempo")
    ok("corrompido-" in ev.name, "com nome que diz o que é")
    ok(not alvo.exists(), "e o arquivo ilegível sai do lugar")

    novo = repo.carregar(EMP_A, CTE, amb.PRODUCAO)
    ok(novo.nunca_rodou, "depois de isolar, carregar devolve um checkpoint novo")
    ok(not repo.existe(EMP_A, CTE, amb.PRODUCAO),
       "*** mas NADA foi gravado com ultNSU=0 automaticamente")

    # versão futura também é corrupção, não "seguir em frente"
    cp2 = repo.carregar(EMP_A, NFE, amb.PRODUCAO); repo.salvar(cp2)
    p2 = repo.caminho(EMP_A, NFE, amb.PRODUCAO)
    d = json.loads(p2.read_text("utf-8")); d["versao"] = 99
    p2.write_text(json.dumps(d), "utf-8")
    levanta(lambda: repo.carregar(EMP_A, NFE, amb.PRODUCAO), cpm.CheckpointCorrompido,
            "formato mais novo que o suportado é recusado")

    # listar() não morre por causa de um arquivo ruim
    itens = repo.listar()
    ok(any(c.status == cpm.CORROMPIDO for c in itens),
       "listar() mostra o corrompido em vez de sumir com a empresa")


# ══ 5. Avanço do NSU: só depois de persistir ════════════════════════════════
secao("5. O NSU só avança DEPOIS de o documento estar salvo")
with ta.raiz_temporaria("ing2_avanco_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    runner = dist.DistribuicaoRunner(repo)

    # 5a. caminho feliz
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(1), doc(2)), ult_nsu="2", max_nsu="2")])
    acervo = AcervoFake(raiz / EMP_A / "cte")
    res = runner.executar(EMP_A, fonte, acervo)
    igual(res.status, cpm.EM_DIA, "lote gravado e em dia")
    igual(res.documentos, 2, "dois documentos contabilizados")
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 2, "checkpoint avançou")
    igual(sorted(acervo.gravados), ["000000000000001", "000000000000002"], "e ambos foram gravados")
    igual(acervo.confirmacoes, 1, "a persistência foi confirmada uma vez")

    # 5b. falha AO GRAVAR no meio do lote → checkpoint NÃO anda
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(3), doc(4)), ult_nsu="4", max_nsu="9")])
    acervo = AcervoFake(raiz / EMP_A / "cte", falhar_ao_preservar_no=1)
    res = runner.executar(EMP_A, fonte, acervo)
    igual(res.motivo_parada, dist.FIM_PERSISTENCIA, "a falha de gravação é reportada")
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 2,
          "*** o checkpoint continua em 2 — nenhum NSU foi pulado")
    ok(any("reprocessad" in n for n in res.notas), "e avisa que haverá reprocessamento")

    # 5c. gravou mas NÃO confirmou → checkpoint NÃO anda
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(3), doc(4)), ult_nsu="4", max_nsu="9")])
    acervo = AcervoFake(raiz / EMP_A / "cte", confirmar=False)
    res = runner.executar(EMP_A, fonte, acervo)
    igual(res.motivo_parada, dist.FIM_PERSISTENCIA, "confirmação negada interrompe o avanço")
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 2,
          "*** checkpoint ainda em 2, mesmo com os arquivos no disco")

    # 5d. exceção no confirmar (fsync) → idem
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(3),), ult_nsu="3", max_nsu="9")])
    acervo = AcervoFake(raiz / EMP_A / "cte", falhar_ao_confirmar=True)
    res = runner.executar(EMP_A, fonte, acervo)
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 2,
          "falha no fsync também mantém o checkpoint")

    # 5e. reprocessar o mesmo lote é seguro e faz o ponteiro andar
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(3), doc(4)), ult_nsu="4", max_nsu="4")])
    acervo = AcervoFake(raiz / EMP_A / "cte")
    res = runner.executar(EMP_A, fonte, acervo)
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 4,
          "reprocessado com sucesso, agora o checkpoint avança")
    igual(res.status, cpm.EM_DIA, "e fica em dia")


# ══ 6. Paradas e proteção contra laço infinito ══════════════════════════════
secao("6. Condições de parada explícitas")
with ta.raiz_temporaria("ing2_parada_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    runner = dist.DistribuicaoRunner(repo, limite_lotes=3)

    # ultNSU == maxNSU: CONSULTA MESMO ASSIM.
    # O maxNSU guardado é foto da última resposta; documento novo faz ele
    # crescer. Pular a consulta faria a empresa parar de buscar para sempre.
    cp = repo.carregar(EMP_A, CTE, amb.PRODUCAO)
    cp.ult_nsu = cp.max_nsu = "50"; repo.salvar(cp)
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(51),), ult_nsu="51", max_nsu="51")])
    res = runner.executar(EMP_A, fonte, AcervoFake(raiz / "x"))
    ok(len(fonte.chamadas) >= 1,
       "*** ultNSU == maxNSU ainda assim consulta (maxNSU pode ter crescido)")
    igual(cpm.nsu_int(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu), 51,
          "e o documento novo que apareceu é capturado")

    # nada novo de verdade: encerra sem mexer no ponteiro
    antes = repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu
    res = runner.executar(EMP_A, FonteCTe(lotes=[]), AcervoFake(raiz / "x"))
    # Com ult == max o motor reporta EM_DIA (mais informativo); sem maxNSU
    # conhecido, reporta SEM_DOCUMENTOS. Os dois são encerramento com sucesso.
    ok(res.motivo_parada in (dist.FIM_EM_DIA, dist.FIM_SEM_DOCUMENTOS),
       "sem novidade: encerra por EM_DIA ou SEM_DOCUMENTOS")
    ok(res.sucesso, "e é um encerramento de sucesso, não erro")
    igual(repo.carregar(EMP_A, CTE, amb.PRODUCAO).ult_nsu, antes, "e o NSU fica onde estava")

    # sem maxNSU conhecido, o rótulo é SEM_DOCUMENTOS
    r2 = runner.executar(PF_C, FonteCTe(lotes=[]), AcervoFake(raiz / "x2"))
    igual(r2.motivo_parada, dist.FIM_SEM_DOCUMENTOS,
          "empresa sem maxNSU conhecido encerra por SEM_DOCUMENTOS")

    # ultNSU < maxNSU: consulta e continua enquanto houver
    cp = repo.carregar(EMP_B, CTE, amb.PRODUCAO)
    cp.ult_nsu, cp.max_nsu = "10", "80"; repo.salvar(cp)
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(11),), ult_nsu="11", max_nsu="80")])
    res = runner.executar(EMP_B, fonte, AcervoFake(raiz / "y"))
    igual(fonte.chamadas[0], "000000000000010", "ultNSU < maxNSU: consulta a partir de onde parou")
    igual(cpm.nsu_int(repo.carregar(EMP_B, CTE, amb.PRODUCAO).ult_nsu), 11, "e avança")
    igual(res.status, cpm.OK, "seguindo pendente (11 de 80)")

    # resposta sem documentos
    fonte = FonteCTe(lotes=[])
    res = runner.executar(EMP_B, fonte, AcervoFake(raiz / "y"))
    igual(res.motivo_parada, dist.FIM_SEM_DOCUMENTOS, "resposta sem documentos encerra")

    # limite defensivo de lotes
    lotes = [dist.Lote(documentos=(doc(i),), ult_nsu=str(i), max_nsu="9999")
             for i in range(100, 120)]
    fonte = FonteCTe(lotes=lotes)
    res = runner.executar(PF_C, fonte, AcervoFake(raiz / "z"))
    igual(res.motivo_parada, dist.FIM_LIMITE_LOTES, "o limite de lotes interrompe a execução")
    igual(res.lotes, 3, "exatamente no limite configurado")
    ok(any("continua deste NSU" in n for n in res.notas), "avisando que a próxima continua")

    # serviço devolve documentos mas NÃO avança o NSU: seria laço infinito
    lotes = [dist.Lote(documentos=(doc(5),), ult_nsu="5", max_nsu="99")] * 5
    fonte = FonteCTe(lotes=list(lotes))
    cp = repo.carregar(EMP_A, NFE, amb.PRODUCAO); cp.ult_nsu = "5"; repo.salvar(cp)
    fonte.servico = NFE
    res = runner.executar(EMP_A, fonte, AcervoFake(raiz / "w"))
    igual(res.motivo_parada, dist.FIM_SEM_PROGRESSO, "*** NSU que não anda é detectado e para")
    igual(cpm.nsu_int(repo.carregar(EMP_A, NFE, amb.PRODUCAO).ult_nsu), 5,
          "e o checkpoint não recua nem repete para sempre")

    # erros: transitório, definitivo, resposta inválida, credencial
    for erro, status, motivo, rotulo in (
        (dist.ErroTransitorio("429"), cpm.ERRO_TRANSITORIO, dist.FIM_ERRO_TRANSITORIO, "transitório"),
        (dist.ErroDefinitivo("rejeitado"), cpm.ERRO_DEFINITIVO, dist.FIM_ERRO_DEFINITIVO, "definitivo"),
        (dist.RespostaInvalida("xml torto"), cpm.ERRO_DEFINITIVO, dist.FIM_RESPOSTA_INVALIDA, "resposta inválida"),
    ):
        r = dist.DistribuicaoRunner(repo).executar(
            EMP_B, FonteCTe(erro=erro), AcervoFake(raiz / "e"))
        igual(r.status, status, f"erro {rotulo} vira status próprio")
        igual(r.motivo_parada, motivo, f"e motivo de parada {motivo}")

    antes = repo.carregar(EMP_B, CTE, amb.PRODUCAO).ult_nsu
    r = dist.DistribuicaoRunner(repo).executar(
        EMP_B, FonteCTe(erro=dist.ErroTransitorio("timeout")), AcervoFake(raiz / "e"))
    igual(repo.carregar(EMP_B, CTE, amb.PRODUCAO).ult_nsu, antes,
          "nenhum erro faz o checkpoint andar")
    ok(repo.carregar(EMP_B, CTE, amb.PRODUCAO).falhas_consecutivas >= 1,
       "falhas consecutivas são contadas")

    # fonte devolvendo lixo em vez de Lote
    class FonteMaluca(FonteCTe):
        def consultar(self, desde_nsu):
            return {"nao": "sou um Lote"}
    r = dist.DistribuicaoRunner(repo).executar(EMP_B, FonteMaluca(), AcervoFake(raiz / "e"))
    igual(r.motivo_parada, dist.FIM_RESPOSTA_INVALIDA, "resposta que não é Lote é recusada")

    levanta(lambda: dist.DistribuicaoRunner(repo, limite_lotes=0), ValueError,
            "limite de lotes tem de ser >= 1")


# ══ 7. Concorrência ═════════════════════════════════════════════════════════
secao("7. Duas execuções não avançam o mesmo NSU")
with ta.raiz_temporaria("ing2_conc_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    with trv.travar(raiz, EMP_A, CTE, amb.PRODUCAO):
        levanta(lambda: trv.travar(raiz, EMP_A, CTE, amb.PRODUCAO).__enter__(),
                trv.TravaOcupada, "a segunda execução da MESMA chave é recusada")
        # chaves diferentes não se atrapalham
        with trv.travar(raiz, EMP_A, CTE, amb.HOMOLOGACAO):
            ok(True, "mesma empresa em ambiente diferente pode rodar em paralelo")
        with trv.travar(raiz, EMP_B, CTE, amb.PRODUCAO):
            ok(True, "outra empresa pode rodar em paralelo")
        with trv.travar(raiz, EMP_A, NFE, amb.PRODUCAO):
            ok(True, "outro serviço pode rodar em paralelo")
    ok(not trv.caminho_trava(raiz, EMP_A, CTE, amb.PRODUCAO).exists(),
       "a trava é liberada ao sair do bloco")

    # liberada mesmo quando dá exceção dentro
    try:
        with trv.travar(raiz, EMP_A, CTE, amb.PRODUCAO):
            raise RuntimeError("estouro dentro do bloco")
    except RuntimeError:
        pass
    ok(not trv.caminho_trava(raiz, EMP_A, CTE, amb.PRODUCAO).exists(),
       "e liberada também quando o bloco levanta")

    # trava órfã (processo que não existe mais) é assumida, com registro
    p = trv.caminho_trava(raiz, EMP_A, CTE, amb.PRODUCAO)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pid": 999999, "desde": "2020-01-01T00:00:00+00:00"}), "utf-8")
    with trv.travar(raiz, EMP_A, CTE, amb.PRODUCAO) as notas:
        ok(any("órfã" in n for n in notas), "trava órfã é assumida e o fato é registrado")

    # o runner respeita a trava e reporta
    with trv.travar(raiz, EMP_A, CTE, amb.PRODUCAO):
        r = dist.DistribuicaoRunner(repo).executar(
            EMP_A, FonteCTe(lotes=[dist.Lote(documentos=(doc(1),), ult_nsu="1")]),
            AcervoFake(raiz / "c"))
        igual(r.motivo_parada, dist.FIM_TRAVA, "o motor desiste quando a chave está travada")
        ok(not repo.existe(EMP_A, CTE, amb.PRODUCAO), "e não toca no checkpoint")

    # duas threads de verdade: só uma entra
    entraram, barreira = [], threading.Lock()
    def tentar():
        try:
            with trv.travar(raiz, PF_C, CTE, amb.PRODUCAO):
                with barreira:
                    entraram.append(1)
                time.sleep(0.15)
        except trv.TravaOcupada:
            pass
    ts = [threading.Thread(target=tentar) for _ in range(6)]
    for t in ts: t.start()
    for t in ts: t.join()
    igual(len(entraram), 1, "*** com 6 threads concorrentes, exatamente UMA entrou")


# ══ 8. CT-e: os sete cenários críticos ══════════════════════════════════════
secao("8. CT-e — os sete cenários críticos")
with ta.raiz_temporaria("ing2_cte_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    runner = dist.DistribuicaoRunner(repo)

    # (1) perda do arquivo de checkpoint
    cp = repo.carregar(EMP_A, CTE, amb.PRODUCAO); cp.ult_nsu = "900"; repo.salvar(cp)
    repo.caminho(EMP_A, CTE, amb.PRODUCAO).unlink()
    perdido = repo.carregar(EMP_A, CTE, amb.PRODUCAO)
    ok(perdido.nunca_rodou, "(1) checkpoint perdido: volta como novo…")
    ok(not repo.existe(EMP_A, CTE, amb.PRODUCAO),
       "     …e nada é gravado sozinho — a perda fica visível")

    # (2) checkpoint corrompido
    cp = repo.carregar(EMP_B, CTE, amb.PRODUCAO); cp.ult_nsu = "500"; repo.salvar(cp)
    repo.caminho(EMP_B, CTE, amb.PRODUCAO).write_bytes(b"\x00\x01lixo")
    r = runner.executar(EMP_B, FonteCTe(lotes=[]), AcervoFake(raiz / "a"))
    igual(r.status, cpm.CORROMPIDO, "(2) corrompido: o motor para com status CORROMPIDO")
    ok(cpm.NSU_ZERO not in (r.nsu_final or ""), "     e NÃO reseta o NSU para zero")
    conteudo = repo.caminho(EMP_B, CTE, amb.PRODUCAO).read_bytes()
    igual(conteudo, b"\x00\x01lixo", "     o arquivo ruim é preservado como evidência")

    # (3) ultNSU muito antigo (o AN só devolve 3 meses — o motor não inventa nada)
    cp = repo.carregar(PF_C, CTE, amb.PRODUCAO); cp.ult_nsu = "1"; cp.max_nsu = "999999"
    repo.salvar(cp)
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(900000),), ult_nsu="900000",
                                      max_nsu="999999",
                                      motivo="apenas os últimos 3 meses")])
    r = runner.executar(PF_C, fonte, AcervoFake(raiz / "b"))
    igual(cpm.nsu_int(repo.carregar(PF_C, CTE, amb.PRODUCAO).ult_nsu), 900000,
          "(3) NSU antigo: aceita o salto que o serviço informou")
    ok(repo.carregar(PF_C, CTE, amb.PRODUCAO).pendentes > 0,
       "     e continua sabendo que há pendência")

    # (4) ultNSU < maxNSU  e  (5) ultNSU == maxNSU
    cp = repo.carregar(EMP_A, CTE, amb.HOMOLOGACAO)
    cp.ult_nsu, cp.max_nsu = "10", "20"; repo.salvar(cp)
    ok(not cp.em_dia and cp.pendentes == 10, "(4) ultNSU < maxNSU: 10 pendentes")
    cp.ult_nsu = "20"; repo.salvar(cp)
    ok(repo.carregar(EMP_A, CTE, amb.HOMOLOGACAO).em_dia, "(5) ultNSU == maxNSU: em dia")

    # (6) lote parcial: o serviço devolve menos do que o máximo do lote
    cp = repo.carregar(EMP_A, NFE, amb.PRODUCAO); cp.ult_nsu = "0"; repo.salvar(cp)
    fonte = FonteFake(servico=NFE, lotes=[
        dist.Lote(documentos=tuple(doc(i) for i in range(1, 4)), ult_nsu="3", max_nsu="3")])
    ac = AcervoFake(raiz / "d")
    r = runner.executar(EMP_A, fonte, ac)
    igual(r.documentos, 3, "(6) lote parcial (3 de até 50) é aceito normalmente")
    igual(r.status, cpm.EM_DIA, "     e encerra em dia")

    # (7) falha ENTRE persistir o XML e avançar o NSU
    cp = repo.carregar(EMP_B, NFE, amb.PRODUCAO); cp.ult_nsu = "100"; repo.salvar(cp)
    fonte = FonteFake(servico=NFE, lotes=[
        dist.Lote(documentos=(doc(101), doc(102)), ult_nsu="102", max_nsu="500")])
    ac = AcervoFake(raiz / "f", confirmar=False)     # gravou, não confirmou
    r = runner.executar(EMP_B, fonte, ac)
    igual(len(ac.gravados), 2, "(7) os XML chegaram a ser gravados…")
    igual(cpm.nsu_int(repo.carregar(EMP_B, NFE, amb.PRODUCAO).ult_nsu), 100,
          "     *** …mas o NSU NÃO avançou — nada se perde, no máximo repete")

    # invariantes oficiais do CT-e
    c = contratos.contrato_do_checkpoint(CTE)
    ok("distNSU" in c.consultas and "consNSU" in c.consultas, "CT-e: distNSU e consNSU")
    ok(not c.consulta_por_chave, "CT-e: NÃO existe consulta por chave")
    igual(c.max_documentos_por_lote, 50, "CT-e: lote de até 50 docZip")
    igual(c.retencao_meses_do_zero, 3, "CT-e: do zero, só 3 meses")


# ══ 9. NF-e tem consChNFe; CT-e não — e a abstração respeita isso ═══════════
secao("9. Capacidade por serviço, não da abstração")
nfe_fonte, cte_fonte = FonteNFe(), FonteCTe()
ok(dist.CAP_CONS_CHAVE in nfe_fonte.capacidades, "NF-e declara consulta por chave")
ok(dist.CAP_CONS_CHAVE not in cte_fonte.capacidades, "*** CT-e NÃO declara consulta por chave")
ok(isinstance(nfe_fonte, dist.FontePorChave), "a fonte NF-e satisfaz FontePorChave")
ok(not isinstance(cte_fonte, dist.FontePorChave), "*** a fonte CT-e NÃO satisfaz FontePorChave")
ok(not hasattr(cte_fonte, "consultar_por_chave"),
   "e o CT-e sequer tem o método — não há como chamá-lo por engano")
ok(isinstance(cte_fonte, dist.Fonte) and isinstance(nfe_fonte, dist.Fonte),
   "as duas satisfazem a interface comum de distribuição")
lote = nfe_fonte.consultar_por_chave("35" + "0" * 42)
igual(len(lote.documentos), 1, "consChNFe funciona na NF-e")
ok("consChNFe" in contratos.obter("nfe55").consultas
   and "consChCTe" not in contratos.obter("cte57").consultas,
   "o contrato oficial confirma a assimetria")


# ══ 10. Estados da credencial ═══════════════════════════════════════════════
secao("10. Seis estados distinguíveis de credencial")
from ingestao import modelo as mod                     # noqa: E402
import seguranca                                        # noqa: E402

SENHA = "SENHA-TESTE-ing2"
with ta.raiz_temporaria("ing2_cred_") as raiz:
    def cred(nome, caminho, senha_blob):
        return mod.Credencial(id=nome, titular=__import__("ingestao").normalizar(EMP_A),
                              caminho=str(caminho), senha_protegida=senha_blob)

    blob = seguranca.proteger(SENHA, raiz)

    pfx_ok = ta.gerar_pfx(raiz / "certs" / "ok.pfx", SENHA, cn="EMPRESA OK")
    igual(ce.avaliar(cred("c1", pfx_ok, blob), raiz).estado, ce.VALIDO, "certificado válido")

    igual(ce.avaliar(cred("c2", raiz / "certs" / "nao_existe.pfx", blob), raiz).estado,
          ce.AUSENTE, "certificado ausente")
    igual(ce.avaliar(cred("c3", "", blob), raiz).estado, ce.AUSENTE, "caminho não cadastrado")

    igual(ce.avaliar(cred("c4", pfx_ok, ""), raiz).estado, ce.SENHA_NAO_CADASTRADA,
          "senha não cadastrada")

    blob_errado = seguranca.proteger("outra-senha", raiz)
    igual(ce.avaliar(cred("c5", pfx_ok, blob_errado), raiz).estado, ce.SENHA_INCORRETA,
          "senha incorreta (envelope íntegro, não abre)")

    quebrado = raiz / "certs" / "quebrado.pfx"
    quebrado.write_bytes(b"isto nao e um pkcs12 de jeito nenhum")
    igual(ce.avaliar(cred("c6", quebrado, blob), raiz).estado, ce.CORROMPIDO,
          "certificado corrompido (não é DER)")
    truncado = raiz / "certs" / "truncado.pfx"
    truncado.write_bytes(pfx_ok.read_bytes()[:200])
    igual(ce.avaliar(cred("c7", truncado, blob), raiz).estado, ce.CORROMPIDO,
          "certificado truncado")
    vazio = raiz / "certs" / "vazio.pfx"; vazio.write_bytes(b"")
    igual(ce.avaliar(cred("c8", vazio, blob), raiz).estado, ce.CORROMPIDO, "arquivo vazio")

    venc = ta.gerar_pfx(raiz / "certs" / "venc.pfx", SENHA, cn="VENCIDA", dias_validade=-30)
    est = ce.avaliar(cred("c9", venc, blob), raiz)
    igual(est.estado, ce.EXPIRADO, "certificado expirado")
    ok("venceu em" in est.detalhe, "e diz quando venceu")
    ok(est.acao_necessaria and "renove" in est.acao_necessaria.lower(),
       "com a ação necessária em português")

    from datetime import datetime, timedelta, timezone
    futuro = datetime.now(timezone.utc) - timedelta(days=200)
    est = ce.avaliar(cred("c10", pfx_ok, blob), raiz, agora=futuro)
    igual(est.estado, ce.AINDA_NAO_VALIDO, "certificado ainda não válido")
    ok("relógio" in est.acao_necessaria, "e lembra de conferir o relógio da máquina")

    ok(all(e in ce.IMPEDITIVOS for e in
           (ce.AUSENTE, ce.SENHA_INCORRETA, ce.CORROMPIDO, ce.EXPIRADO, ce.AINDA_NAO_VALIDO)),
       "todos os estados ruins são impeditivos")
    ok(ce.VALIDO not in ce.IMPEDITIVOS, "e o válido não é")
    ok(SENHA not in json.dumps(ce.avaliar(cred("c1", pfx_ok, blob), raiz).resumo()),
       "o resumo do estado não vaza a senha")

    # nunca levanta, nem com entrada absurda
    class CredExplosiva(mod.Credencial):
        def abrir_senha(self, dados_dir):
            raise RuntimeError("boom")
    e = ce.avaliar(CredExplosiva(id="cx", titular=__import__("ingestao").normalizar(EMP_A),
                                 caminho=str(pfx_ok), senha_protegida=blob), raiz)
    ok(e.estado in ce.IMPEDITIVOS, "avaliar() nunca levanta — devolve estado impeditivo")


# ══ 11. Uma empresa ruim não derruba as outras ══════════════════════════════
secao("11. Empresa com problema não interrompe a varredura das demais")
with ta.raiz_temporaria("ing2_multi_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    runner = dist.DistribuicaoRunner(repo)

    class FonteComCredencialRuim(FonteCTe):
        def consultar(self, desde_nsu):
            from ingestao.sessao import CertificadoInvalido
            raise CertificadoInvalido("credencial X: certificado vencido")

    itens = [
        (EMP_A, FonteCTe(lotes=[dist.Lote(documentos=(doc(1),), ult_nsu="1", max_nsu="1")]),
         AcervoFake(raiz / "a")),
        (EMP_B, FonteComCredencialRuim(), AcervoFake(raiz / "b")),
        (PF_C, FonteCTe(lotes=[dist.Lote(documentos=(doc(7),), ult_nsu="7", max_nsu="7")]),
         AcervoFake(raiz / "c")),
    ]
    res = dist.executar_para_empresas(runner, itens)
    igual(len(res), 3, "as três empresas foram processadas")
    igual(res[1].status, cpm.CREDENCIAL_INVALIDA, "a do meio reporta CREDENCIAL_INVALIDA")
    igual(res[1].motivo_parada, dist.FIM_CREDENCIAL, "com motivo de parada próprio")
    ok(res[0].sucesso and res[2].sucesso,
       "*** e as outras duas concluíram normalmente")
    igual(cpm.nsu_int(repo.carregar(PF_C, CTE, amb.PRODUCAO).ult_nsu), 7,
          "a última empresa avançou seu checkpoint")

    # identidade inválida também não derruba o conjunto
    res = dist.executar_para_empresas(runner, [("123", FonteCTe(), AcervoFake(raiz / "d"))])
    igual(res[0].status, cpm.ERRO_DEFINITIVO, "identidade inválida vira resultado, não exceção")


# ══ 12. Observabilidade ═════════════════════════════════════════════════════
secao("12. Logs úteis, sem dado sensível")
with ta.raiz_temporaria("ing2_log_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    fonte = FonteCTe(lotes=[dist.Lote(documentos=(doc(1), doc(2)), ult_nsu="2", max_nsu="2")])
    r = dist.DistribuicaoRunner(repo).executar(EMP_A, fonte, AcervoFake(raiz / "a"))
    d = r.para_log()
    for campo in ("empresa", "servico", "ambiente", "nsu_inicial", "nsu_final",
                  "max_nsu", "lotes", "documentos", "duracao_s", "status"):
        ok(campo in d, f"o log registra {campo}")
    ok(EMP_A not in json.dumps(d), "*** a identidade vai MASCARADA no log")
    ok("***" in d["empresa"], "com o miolo escondido")
    ok(r.duracao_s >= 0, "a duração é medida")
    ok(r.linha().startswith("["), "há uma linha pronta para o log")

    sujo = ("senha: MinhaSenha123 token=abc123 "
            "-----BEGIN PRIVATE KEY-----MIIEv...-----END PRIVATE KEY----- "
            "<?xml version='1.0'?><nfeProc>...</nfeProc>")
    limpo = dist.higienizar(sujo)
    for proibido in ("MinhaSenha123", "abc123", "MIIEv", "nfeProc"):
        ok(proibido not in limpo, f"higienizar() remove {proibido[:12]}")
    ok(len(dist.higienizar("x" * 5000)) <= dist.LIMITE_MENSAGEM + 1,
       "e trunca mensagem gigante")
    cp_log = repo.carregar(EMP_A, CTE, amb.PRODUCAO).resumo()
    ok(EMP_A not in json.dumps(cp_log), "o resumo do checkpoint também mascara a identidade")


# ══ 13. Backup: checkpoint entra; trava e evidência, não ════════════════════
secao("13. Integração com o backup .fbk")
import fiscale_backup as bk                            # noqa: E402
with ta.raiz_temporaria("ing2_bk_") as raiz:
    repo = cpm.RepositorioCheckpoint(raiz)
    cp = repo.carregar(EMP_A, CTE, amb.PRODUCAO); cp.ult_nsu = "123"
    alvo = repo.salvar(cp)
    repo.salvar(repo.carregar(EMP_A, CTE, amb.HOMOLOGACAO))
    trava = trv.caminho_trava(raiz, EMP_A, CTE, amb.PRODUCAO)
    trava.write_text("{}", "utf-8")
    evid = alvo.with_suffix(".json.corrompido-20260101000000")
    evid.write_text("{}", "utf-8")

    ok(bk._entra_no_backup(alvo, raiz), "o checkpoint ENTRA no backup")
    ok(bk._entra_no_backup(repo.caminho(EMP_A, CTE, amb.HOMOLOGACAO), raiz),
       "o de homologação também, em arquivo separado")
    ok(not bk._entra_no_backup(trava, raiz), "a trava .lock NÃO entra (PID desta máquina)")
    ok(not bk._entra_no_backup(evid, raiz), "a evidência .corrompido-* NÃO entra")
    ok(repo.caminho(EMP_A, CTE, amb.PRODUCAO) != repo.caminho(EMP_A, CTE, amb.HOMOLOGACAO),
       "produção e homologação continuam separadas dentro do pacote")
    conteudo = json.loads(alvo.read_text("utf-8"))
    ok(not any(k for k in conteudo if "senha" in k.lower()),
       "e o que entra no pacote não tem segredo")


# ══ 14. Provas finais ═══════════════════════════════════════════════════════
secao("14. Provas: sem rede, sem raiz real, sem temporários")
igual(_tentativas_de_rede, [], "*** ZERO tentativas de conexão durante toda a suíte")
ok(socket.socket is _SocketBloqueado, "o bloqueio de rede esteve ativo o tempo todo")
levanta(lambda: socket.create_connection(("adn.nfse.gov.br", 443)),
        RedeProibidaNoTeste, "e uma tentativa de conexão realmente falharia")

real = Path.home() / "Fiscale" / "dados"
if real.exists():
    ok((real / "certificados.json").exists(),
       "a pasta de dados REAL continua intacta ao fim da suíte")
    ok(not (real / "ingestao").exists(),
       "*** nenhum checkpoint foi criado na raiz real")
ok(os.environ.get(fd.VAR_PROIBIR_RAIZ_REAL) is None, "a trava de raiz foi desligada ao final")

tmp = Path(tempfile.gettempdir())
nossas = [p for p in tmp.glob("ing2_*") if p.is_dir()]
igual(nossas, [], "nenhuma pasta ing2_* sobrou (limpeza no fim de cada bloco)")


# ════════════════════════════════════════════════════════════════════════════
secao("A RESPOSTA BRUTA é preservada antes de o checkpoint andar (NF-e 7)")
# O motor da NF-e nunca guardou a resposta do serviço. Foi essa ausência que
# tornou irrecuperáveis os NSU reais dos 72 CT-e da CTE 4: os documentos
# estavam certos, a leitura de um campo estava errada, e não havia de onde
# reler. Aqui o motor passa a guardar, conferir e — se não conseguir — segurar
# o ponteiro.
from ingestao import resposta_bruta as bruta                    # noqa: E402


class FonteComBruto(FonteNFe):
    """Uma Fonte que expõe `ultima_resposta.bruto`, como a real."""

    class _Resp:
        def __init__(self, bruto):
            self.bruto = bruto

    def __init__(self, bruto=b"<retDistDFeInt>prova</retDistDFeInt>", **kw):
        super().__init__(**kw)
        self._bruto = bruto
        self.ultima_resposta = None

    def consultar(self, desde_nsu):
        lote = super().consultar(desde_nsu)
        self.ultima_resposta = self._Resp(self._bruto)
        return lote


with ta.raiz_temporaria("nfe7_") as raiz:
    EMP = "11222333000181"
    repo = cpm.RepositorioCheckpoint(raiz)
    ac = acv.abrir(raiz, EMP, servico=NFE, ambiente=amb.PRODUCAO)
    f = FonteComBruto(lotes=[dist.Lote(documentos=(doc(1), doc(2)),
                                       ult_nsu="2", max_nsu="9")])
    r = dist.DistribuicaoRunner(repo, limite_lotes=1).executar(EMP, f, ac)

    igual(r.documentos, 2, "os dois documentos entraram")
    cp = repo.carregar(EMP, NFE, amb.PRODUCAO)
    igual(cp.ult_nsu, "000000000000002", "e o checkpoint andou")

    guardadas = list((Path(raiz) / EMP / "respostas").rglob("*.xml"))
    igual(len(guardadas), 1, "UMA resposta bruta foi guardada")
    igual(guardadas[0].read_bytes(), b"<retDistDFeInt>prova</retDistDFeInt>",
          "com os bytes exatos que o transporte devolveu")
    ok(bruta.conferir(guardadas[0]),
       "e o SHA-256 do manifesto confere com o arquivo")

    manif = guardadas[0].with_suffix(".json")
    ok(manif.exists(), "há manifesto ao lado")
    import json as _j
    d = _j.loads(manif.read_text("utf-8"))
    igual(d["servico"], NFE, "com o serviço")
    igual(d["ambiente"], "producao", "e o ambiente")
    ok(d["quando"], "e a data/hora")
    ok(d["tentativa"], "e o identificador da tentativa")
    ok(d["sha256"], "e o SHA-256")
    ok("***" in d["empresa"], "com a empresa MASCARADA")
    ok(EMP not in _j.dumps(d), "e o CNPJ inteiro não vaza no manifesto")


secao("Não conseguir guardar a resposta SEGURA o checkpoint")
with ta.raiz_temporaria("nfe7_") as raiz:
    EMP = "11222333000181"
    repo = cpm.RepositorioCheckpoint(raiz)
    ac = acv.abrir(raiz, EMP, servico=NFE, ambiente=amb.PRODUCAO)
    # Conteúdo que a preservação recusa: chave privada não vai para o disco.
    f = FonteComBruto(bruto=b"-----BEGIN PRIVATE KEY-----",
                      lotes=[dist.Lote(documentos=(doc(1),), ult_nsu="1",
                                       max_nsu="9")])
    r = dist.DistribuicaoRunner(repo, limite_lotes=1).executar(EMP, f, ac)

    igual(r.motivo_parada, dist.FIM_RESPOSTA_BRUTA,
          "o motivo da parada é próprio: não guardou a PROVA")
    ok(r.motivo_parada != dist.FIM_PERSISTENCIA,
       "e não se confunde com falha ao gravar o DOCUMENTO")
    cp = repo.carregar(EMP, NFE, amb.PRODUCAO)
    igual(cp.ult_nsu, "000000000000000",
          "o checkpoint NÃO andou — sem prova, o ponteiro fica")
    igual(len(list((Path(raiz) / EMP / "respostas").rglob("*.xml"))
              if (Path(raiz) / EMP / "respostas").exists() else []), 0,
          "e nada de sensível foi escrito no disco")


secao("Fonte sem `bruto` continua funcionando (não quebra o que existe)")
with ta.raiz_temporaria("nfe7_") as raiz:
    EMP = "11222333000181"
    repo = cpm.RepositorioCheckpoint(raiz)
    ac = acv.abrir(raiz, EMP, servico=NFE, ambiente=amb.PRODUCAO)
    f = FonteNFe(lotes=[dist.Lote(documentos=(doc(1),), ult_nsu="1", max_nsu="9")])
    r = dist.DistribuicaoRunner(repo, limite_lotes=1).executar(EMP, f, ac)
    igual(r.documentos, 1, "o documento entra")
    igual(repo.carregar(EMP, NFE, amb.PRODUCAO).ult_nsu, "000000000000001",
          "e o checkpoint anda: a preservação só age quando há bruto")


print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")

if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Todos os testes da ING 2 passaram.")
