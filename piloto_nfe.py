#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
piloto_nfe.py — a execução periódica do ciclo NF-e, restrita a uma allowlist.

O QUE ESTE ARQUIVO É
    O ponto de entrada que a Tarefa Agendada do Windows chama. Ele monta a
    política, lê a allowlist do arquivo de configuração e manda o controlador
    executar **um** ciclo. Nada mais.

O QUE ELE NÃO É
    Não é um segundo motor. Não fala com a SEFAZ: quem fala é
    `servico_distribuicao`, a porta única, através do controlador. Não tem
    laço infinito — quem repete é o agendador do sistema operacional, e é ele
    que sobrevive a reinício de máquina.

AS DUAS HORAS SÃO ESCOLHA NOSSA
    `intervalo_horas: 2` e `cooldown_em_dia_min: 120` são **política
    conservadora do FISCALE**, não exigência da SEFAZ. A regra oficial
    confirmada é 1 hora (NT 2014.002); dobramos por prudência depois dos dois
    `656` de 17/08/2026, cuja causa **não** está provada. Está em arquivo de
    configuração justamente para poder mudar sem tocar em código.

DUAS EXECUÇÕES AO MESMO TEMPO SERIAM O PIOR CASO
    Duas instâncias do piloto consultariam a mesma empresa em paralelo e
    queimariam cota por conta própria. Por isso há uma trava de processo além
    das travas por empresa: a primeira instância cria o arquivo com `O_EXCL`,
    e a segunda encerra em silêncio, com código 0 — porque "já está rodando"
    não é erro, é a proteção funcionando.

USO
    python piloto_nfe.py            executa um ciclo
    python piloto_nfe.py --dry-run  decide e relata, sem tocar a rede
    python piloto_nfe.py --config   mostra a configuração vigente
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import fiscale_dados as fd                                  # noqa: E402
from ingestao import cadastro as cad_mod                    # noqa: E402
from ingestao import checkpoint as cpm                      # noqa: E402
from ingestao import controlador as ctl                     # noqa: E402
from ingestao import servico_distribuicao as svc            # noqa: E402
from ingestao.ambiente import resolver as resolver_ambiente  # noqa: E402
from ingestao.identidade import normalizar                  # noqa: E402

ARQ_CONFIG = RAIZ / "piloto_nfe.json"
PASTA_LOG = "piloto"
ARQ_LOG = "nfe.jsonl"
ARQ_TRAVA = "piloto-nfe.lock"
IDADE_MAXIMA_TRAVA_S = 3600      # ciclo de 2 empresas leva segundos; 1 h é folga


PADRAO = {
    "allowlist": [],
    "ambiente": "producao",
    "intervalo_horas": 2,
    "max_lotes_por_empresa": 1,
    "max_empresas_por_ciclo": 2,
    "cooldown_em_dia_min": 120,
    "observacao": ("intervalo_horas e cooldown_em_dia_min sao POLITICA "
                   "CONSERVADORA DO FISCALE, nao regra da SEFAZ. A regra "
                   "oficial confirmada e de 1 hora (NT 2014.002)."),
}


def carregar_config() -> dict:
    cfg = dict(PADRAO)
    if ARQ_CONFIG.exists():
        try:
            d = json.loads(ARQ_CONFIG.read_text("utf-8-sig"))
            if isinstance(d, dict):
                cfg.update({k: v for k, v in d.items() if k in PADRAO})
        except ValueError:
            pass
    cfg["allowlist"] = [normalizar(c).valor for c in (cfg.get("allowlist") or [])
                        if normalizar(c).valido]
    return cfg


def politica_de(cfg: dict) -> ctl.Politica:
    """A política do piloto. Mais conservadora que a padrão, de propósito."""
    return ctl.Politica(
        max_empresas_por_ciclo=int(cfg["max_empresas_por_ciclo"]),
        max_lotes_por_empresa=int(cfg["max_lotes_por_empresa"]),
        cooldown_em_dia_min=int(cfg["cooldown_em_dia_min"]),
        usar_trava=True,
        consultar_novas_empresas=False,     # onboarding continua manual
        consultar_em_divergencia=False,     # MONTE segue congelada
    )


# ── trava de processo ───────────────────────────────────────────────────────
def _processo_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return True          # na dúvida, assume vivo: não roubar a trava


class TravaDoPiloto:
    """Uma execução por vez. `O_EXCL` decide, não uma checagem prévia.

    Verificar "existe?" e depois criar deixa uma janela entre as duas
    operações — e é exatamente nessa janela que duas tarefas agendadas
    disparadas juntas passariam as duas."""

    def __init__(self, dados_dir):
        self.caminho = Path(dados_dir) / PASTA_LOG / ARQ_TRAVA
        self.tomada = False

    def __enter__(self):
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        for tentativa in (1, 2):
            try:
                fd_ = os.open(str(self.caminho),
                              os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd_, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(),
                               "desde": datetime.now(timezone.utc).isoformat(
                                   timespec="seconds")}, f)
                self.tomada = True
                return self
            except FileExistsError:
                if tentativa == 2:
                    return self
                if self._orfa():
                    try:
                        self.caminho.unlink()
                    except OSError:
                        return self
                else:
                    return self
        return self

    def _orfa(self) -> bool:
        """Trava de processo morto, ou velha demais para ser real."""
        try:
            d = json.loads(self.caminho.read_text("utf-8"))
            pid = int(d.get("pid") or 0)
            desde = datetime.fromisoformat(d.get("desde") or "")
        except (ValueError, OSError):
            return True
        if _processo_vivo(pid):
            return False
        idade = (datetime.now(timezone.utc) - desde).total_seconds()
        return idade >= 0     # processo morto: órfã, independente da idade

    def dono(self) -> dict:
        try:
            return json.loads(self.caminho.read_text("utf-8"))
        except (ValueError, OSError):
            return {}

    def __exit__(self, *exc):
        if self.tomada:
            try:
                self.caminho.unlink()
            except OSError:
                pass
        return False


def registrar(dados_dir, linha: dict) -> None:
    """Log append-only do piloto. NÃO substitui a trilha de auditoria."""
    p = Path(dados_dir) / PASTA_LOG / ARQ_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    linha = dict(linha)
    linha["em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")


def executar(dry_run: bool = False, dados_dir=None) -> int:
    cfg = carregar_config()
    raiz = Path(dados_dir) if dados_dir else fd.raiz()
    amb = resolver_ambiente(cfg["ambiente"])
    politica = politica_de(cfg)
    alvos = cfg["allowlist"]

    if not alvos:
        registrar(raiz, {"evento": "SEM_ALLOWLIST", "dry_run": dry_run})
        print("[PARADO] allowlist vazia — o piloto não consulta ninguém.")
        return 2

    with TravaDoPiloto(raiz) as trava:
        if not trava.tomada:
            dono = trava.dono()
            registrar(raiz, {"evento": "JA_EM_EXECUCAO", "dono": dono})
            print(f"[IGNORADO] já há um ciclo do piloto em andamento {dono}")
            return 0        # não é erro: é a proteção funcionando

        t0 = time.monotonic()
        cad = cad_mod.carregar(raiz)
        res = ctl.executar_ciclo(
            raiz, alvos, servico=cpm.NFE_DISTRIBUICAO, ambiente=amb,
            politica=politica, dry_run=dry_run,
            fabrica_fonte=None if dry_run else svc.fabrica_padrao(cad, raiz))
        z = res.resumo()
        registrar(raiz, {
            "evento": "CICLO", "dry_run": dry_run,
            "allowlist": [normalizar(c).mascarado() for c in alvos],
            "avaliadas": z["avaliadas"], "consultadas": z["consultadas"],
            "documentos": z["documentos"],
            "bloqueadas": z["bloqueadas"],
            "revisao_sequencia": z["revisao_sequencia"],
            "erros": z["erros_temporarios"] + z["erros_permanentes"],
            "interrompido_por": res.interrompido_por or None,
            "duracao_s": round(time.monotonic() - t0, 2),
            "empresas": [{"empresa": e.identidade_mascarada,
                          "consultada": e.consultada, "cstat": e.cstat or None,
                          "subtipo": e.subtipo or None,
                          "documentos": e.documentos, "estado": e.estado,
                          "motivo": e.motivo or None} for e in res.empresas],
        })
        print(res.relatorio())
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Piloto periódico do ciclo NF-e.")
    p.add_argument("--dry-run", action="store_true",
                   help="decide e relata, sem tocar a rede")
    p.add_argument("--config", action="store_true",
                   help="mostra a configuração vigente e sai")
    p.add_argument("--dados", default="", help="raiz de dados (padrão: a do Fiscale)")
    a = p.parse_args()

    if a.config:
        cfg = carregar_config()
        print(json.dumps({**cfg, "allowlist": [normalizar(c).mascarado()
                                               for c in cfg["allowlist"]]},
                         ensure_ascii=False, indent=1))
        return 0
    return executar(dry_run=a.dry_run, dados_dir=a.dados or None)


if __name__ == "__main__":
    sys.exit(main())
