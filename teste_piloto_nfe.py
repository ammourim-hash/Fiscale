#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Piloto periódico do ciclo NF-e (ING 4D-4).

    python teste_piloto_nfe.py

O QUE ESTA SUÍTE GARANTE
    Que a automação só alcança quem está na allowlist, que duas execuções
    simultâneas não acontecem, que a política conservadora do piloto está
    declarada como escolha nossa — e que o arquivo de configuração real do
    projeto aponta exatamente para as duas empresas aprovadas.

REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import piloto_nfe as piloto                        # noqa: E402
import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import controlador as ctl            # noqa: E402
from ingestao import operacao as op                # noqa: E402
from ingestao.ambiente import PRODUCAO             # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402
from ingestao.identidade import normalizar         # noqa: E402

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
    ok(a == b, desc if a == b else f"{desc}  (obtive {a!r}, esperava {b!r})")


SERV = cpm.NFE_DISTRIBUICAO
SENHA = "frase-ficticia-piloto"
# Instante de referência NO FUTURO em relação ao relógio da máquina — mesma
# razão explicada em `teste_ingestao4c.py`: o produto conta a janela do 656 a
# partir do MAIOR entre o carimbo real da resposta e este `agora`. Data fixa
# vira bomba-relógio no dia em que o calendário a alcança.
AGORA = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)

E_A = T.cnpj_ficticio("111111110001")      # no piloto
E_B = T.cnpj_ficticio("222222220001")      # no piloto
E_FORA = T.cnpj_ficticio("333333330001")   # elegível, mas FORA da allowlist
E_REVISAO = T.cnpj_ficticio("444444440001")
E_DIVERG = T.cnpj_ficticio("555555550001")


def cadastrar(raiz, cnpj):
    import seguranca
    certs = Path(raiz) / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    arq = certs / f"{cnpj}.pfx"
    apoio.gerar_pfx(arq, SENHA, cn=f"EMPRESA {cnpj[:4]}")
    reg = Path(raiz) / "certificados.json"
    atual = json.loads(reg.read_text("utf-8")) if reg.exists() else []
    atual.append({"id": cnpj, "cnpj": cnpj, "nome": f"EMPRESA {cnpj[:4]}",
                  "caminho": f"certs/{cnpj}.pfx",
                  "senha_protegida": seguranca.proteger(SENHA, raiz)})
    reg.write_text(json.dumps(atual, ensure_ascii=False), encoding="utf-8")


def cenario(raiz):
    repo = RepositorioCheckpoint(raiz)
    for c in (E_A, E_B, E_FORA, E_REVISAO, E_DIVERG):
        cadastrar(raiz, c)
        cp = repo.carregar(c, SERV, PRODUCAO)
        cp.ult_nsu = "000000000000100"
        cp.max_nsu = "000000000000150"
        cp.status = cpm.OK
        cp.origem_ult_nsu = cpm.ORIGEM_LEGADO
        cp.cobertura_anterior = cpm.COBERTURA_ACERVO_LEGADO
        repo.salvar(cp)
    rop = op.RepositorioOperacao(raiz)
    e = rop.carregar(E_REVISAO, SERV, PRODUCAO)
    e.exigir_revisao_de_sequencia("656 de sequência")
    rop.salvar(e)
    cp = repo.carregar(E_DIVERG, SERV, PRODUCAO)
    cp.registrar_observacao_sefaz("000000000001361", cstat="656")
    cp.marcar_possivel_consumidor_externo("teste")
    repo.salvar(cp)


def escrever_config(raiz, **kw):
    cfg = {"allowlist": [E_A, E_B], "ambiente": "producao",
           "intervalo_horas": 2, "max_lotes_por_empresa": 1,
           "max_empresas_por_ciclo": 2, "cooldown_em_dia_min": 120}
    cfg.update(kw)
    piloto.ARQ_CONFIG = Path(raiz) / "piloto_nfe.json"
    piloto.ARQ_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False),
                                 encoding="utf-8")
    return cfg


_CONFIG_REAL = piloto.ARQ_CONFIG

# ══════════════════════════════════════════════════════════════════════════
secao("A configuração REAL do projeto: piloto SUSPENSO desde 17/08/2026")
cfg_real = json.loads(_CONFIG_REAL.read_text("utf-8-sig"))
APROVADAS = ["00612340000173", "67890007000105"]
# A allowlist foi ESVAZIADA de propósito quando o piloto foi suspenso: as duas
# empresas receberam 656 depois de 1h40 e 2h52, e a cadência de 2 h deixou de
# estar validada. Vazia = o piloto não consulta ninguém nem se a tarefa do
# Windows for reabilitada por engano. A lista aprovada fica preservada ao lado.
igual(cfg_real["allowlist"], [],
      "allowlist vazia — o piloto está suspenso, e isso é o estado pretendido")
igual(sorted(cfg_real.get("allowlist_suspensa_em_17_08_2026", [])),
      sorted(APROVADAS),
      "a lista aprovada continua registrada, para a retomada não ser de memória")
ok("656" in cfg_real.get("motivo_suspensao", ""),
   "com o motivo da suspensão escrito no próprio arquivo")
for proibida in ("64567004000139", "05678005000191", "04"):
    ok(not any(c.startswith(proibida) for c in cfg_real["allowlist"]),
       f"nenhuma empresa começando por {proibida} está na allowlist")
    ok(not any(c.startswith(proibida)
               for c in cfg_real.get("allowlist_suspensa_em_17_08_2026", [])),
       f"nem na lista suspensa — {proibida} nunca esteve no piloto")
ok(set(cfg_real["allowlist"]) <= set(APROVADAS),
   "e se um dia voltar a ter empresa, só pode ser uma das duas aprovadas")
igual(cfg_real["intervalo_horas"], 2, "intervalo de 2 horas")
igual(cfg_real["max_lotes_por_empresa"], 1, "um lote por empresa por ciclo")
igual(cfg_real["cooldown_em_dia_min"], 120, "cooldown de 120 min")
ok("NAO" in cfg_real["observacao"] and "SEFAZ" in cfg_real["observacao"],
   "e está escrito no arquivo que 2 h NÃO é regra da SEFAZ")
ok("NT 2014.002" in cfg_real["observacao"],
   "com a fonte da regra oficial de 1 hora citada")

secao("A política do piloto é mais conservadora que a padrão")
pol = piloto.politica_de(piloto.PADRAO | {"allowlist": [], "cooldown_em_dia_min": 120,
                                          "max_lotes_por_empresa": 1,
                                          "max_empresas_por_ciclo": 2})
igual(pol.cooldown_em_dia_min, 120, "120 min contra os 60 do padrão")
ok(pol.cooldown_em_dia_min > ctl.Politica().cooldown_em_dia_min,
   "é estritamente mais conservadora que a política padrão")
igual(pol.max_lotes_por_empresa, 1, "um lote")
ok(not pol.consultar_novas_empresas, "onboarding continua manual")
ok(not pol.consultar_em_divergencia, "e a MONTE segue congelada")
ok(pol.usar_trava, "com trava por empresa ligada")

secao("O piloto NÃO documenta as 2 h como exigência oficial")
_fonte = (RAIZ / "piloto_nfe.py").read_text("utf-8")
ok("não é exigência da SEFAZ" in _fonte or "não exigência" in _fonte
   or "não é regra" in _fonte or "escolha nossa" in _fonte.lower(),
   "o módulo declara as 2 h como escolha do FISCALE")
ok("NT 2014.002" in _fonte, "e cita a regra oficial de 1 hora")
for regra in ctl.REGRAS_OFICIAIS_DIST_NSU["confirmado"]:
    ok("2 h" not in regra and "duas horas" not in regra,
       "nenhuma regra OFICIAL passou a falar em 2 horas")

# ══════════════════════════════════════════════════════════════════════════
secao("Dry-run: só as duas da allowlist entram")
with apoio.raiz_temporaria("pil_") as raiz:
    cenario(raiz)
    escrever_config(raiz)
    codigo = piloto.executar(dry_run=True, dados_dir=raiz)
    igual(codigo, 0, "o piloto termina bem")
    log = [json.loads(l) for l in
           (Path(raiz) / piloto.PASTA_LOG / piloto.ARQ_LOG).read_text("utf-8").splitlines()]
    ciclo = [x for x in log if x["evento"] == "CICLO"][-1]
    igual(ciclo["avaliadas"], 2, "duas empresas avaliadas")
    igual(sorted(ciclo["allowlist"]),
          sorted([normalizar(E_A).mascarado(), normalizar(E_B).mascarado()]),
          "e são exatamente as da allowlist")
    vistas = {e["empresa"] for e in ciclo["empresas"]}
    for fora in (E_FORA, E_REVISAO, E_DIVERG):
        ok(normalizar(fora).mascarado() not in vistas,
           f"{normalizar(fora).mascarado()} ficou de fora do ciclo")
    ok(ciclo["dry_run"], "marcado como dry-run no log")
    igual(_TENTATIVAS, [], "e zero rede")

secao("Empresa elegível fora da allowlist NÃO é consultada")
with apoio.raiz_temporaria("pil_") as raiz:
    cenario(raiz)
    escrever_config(raiz)
    el = ctl.avaliar(raiz, E_FORA, agora=AGORA)
    ok(el.pode, "a empresa de fora É elegível pela política geral")
    piloto.executar(dry_run=True, dados_dir=raiz)
    log = [json.loads(l) for l in
           (Path(raiz) / piloto.PASTA_LOG / piloto.ARQ_LOG).read_text("utf-8").splitlines()]
    vistas = {e["empresa"] for e in log[-1]["empresas"]}
    ok(normalizar(E_FORA).mascarado() not in vistas,
       "mas o piloto não a alcança — a allowlist é o limite")

secao("Allowlist vazia: o piloto não consulta ninguém")
with apoio.raiz_temporaria("pil_") as raiz:
    cenario(raiz)
    escrever_config(raiz, allowlist=[])
    igual(piloto.executar(dry_run=True, dados_dir=raiz), 2,
          "sai com código de parada")
    log = (Path(raiz) / piloto.PASTA_LOG / piloto.ARQ_LOG).read_text("utf-8")
    ok("SEM_ALLOWLIST" in log, "e registra o motivo")

secao("Identificador inválido na configuração é descartado, não consultado")
with apoio.raiz_temporaria("pil_") as raiz:
    cenario(raiz)
    escrever_config(raiz, allowlist=[E_A, "nao-e-cnpj", "00000000000000"])
    cfg = piloto.carregar_config()
    igual(cfg["allowlist"], [E_A], "só o identificador válido sobrevive")

# ══════════════════════════════════════════════════════════════════════════
secao("Trava de processo: a segunda execução não roda")
with apoio.raiz_temporaria("pil_") as raiz:
    cenario(raiz)
    escrever_config(raiz)
    with piloto.TravaDoPiloto(raiz) as t1:
        ok(t1.tomada, "a primeira toma a trava")
        with piloto.TravaDoPiloto(raiz) as t2:
            ok(not t2.tomada, "a segunda NÃO toma")
            igual(t2.dono().get("pid"), os.getpid(), "e sabe de quem é")
        codigo = piloto.executar(dry_run=True, dados_dir=raiz)
        igual(codigo, 0, "executar() com trava tomada sai com 0, não erro")
        log = (Path(raiz) / piloto.PASTA_LOG / piloto.ARQ_LOG).read_text("utf-8")
        ok("JA_EM_EXECUCAO" in log, "e registra que já havia ciclo em andamento")
        ok("CICLO" not in log.split("JA_EM_EXECUCAO")[-1],
           "sem executar um segundo ciclo")

secao("A trava é liberada ao sair, e o ciclo seguinte roda")
with apoio.raiz_temporaria("pil_") as raiz:
    cenario(raiz)
    escrever_config(raiz)
    with piloto.TravaDoPiloto(raiz) as t:
        caminho = t.caminho
        ok(caminho.exists(), "o arquivo de trava existe durante a execução")
    ok(not caminho.exists(), "e some ao terminar")
    igual(piloto.executar(dry_run=True, dados_dir=raiz), 0,
          "o ciclo seguinte roda normalmente")

secao("Trava órfã de processo morto não bloqueia para sempre")
with apoio.raiz_temporaria("pil_") as raiz:
    cenario(raiz)
    escrever_config(raiz)
    p = Path(raiz) / piloto.PASTA_LOG / piloto.ARQ_TRAVA
    p.parent.mkdir(parents=True, exist_ok=True)
    # PID que não existe: processo morto sem soltar a trava (queda de energia).
    p.write_text(json.dumps({"pid": 999999,
                             "desde": (datetime.now(timezone.utc)
                                       - timedelta(hours=5)).isoformat()}),
                 encoding="utf-8")
    with piloto.TravaDoPiloto(raiz) as t:
        ok(t.tomada, "a trava órfã é retomada")
    ok(not p.exists(), "e liberada ao final")

secao("Trava de processo VIVO é respeitada mesmo se antiga")
with apoio.raiz_temporaria("pil_") as raiz:
    cenario(raiz)
    escrever_config(raiz)
    p = Path(raiz) / piloto.PASTA_LOG / piloto.ARQ_TRAVA
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pid": os.getpid(),
                             "desde": (datetime.now(timezone.utc)
                                       - timedelta(days=2)).isoformat()}),
                 encoding="utf-8")
    with piloto.TravaDoPiloto(raiz) as t:
        ok(not t.tomada,
           "processo vivo dono da trava não é atropelado por idade")
    ok(p.exists(), "e a trava dele continua lá")
    p.unlink()

secao("O piloto não tem laço próprio — quem repete é o agendador")
for proibido in ("while True", "schedule", "APScheduler", "Timer(", "sleep("):
    ok(proibido not in _fonte, f"sem '{proibido}' dentro do piloto")

secao("O piloto usa a porta única, e não fala com a SEFAZ por conta própria")
import ast                                          # noqa: E402
arv = ast.parse(_fonte)
diretas = [n.lineno for n in ast.walk(arv) if isinstance(n, ast.Call)
           and getattr(n.func, "attr", "") in ("ingerir", "FonteNFeDistribuicaoDFe")]
igual(diretas, [], "não chama pipeline.ingerir nem monta Fonte")
ok("svc.fabrica_padrao" in _fonte, "usa a fábrica da porta única")
ok("executar_ciclo" in _fonte, "e delega o ciclo ao controlador")

secao("O log do piloto não substitui a trilha de auditoria")
ok("NÃO substitui a trilha" in _fonte,
   "está escrito que o log é operacional, não auditoria")

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
    piloto.ARQ_CONFIG = _CONFIG_REAL

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Piloto NF-e: todos os testes passaram.")
