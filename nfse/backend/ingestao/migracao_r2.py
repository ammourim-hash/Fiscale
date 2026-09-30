"""
migracao_r2.py — transforma evidência histórica de `656` em estado estruturado.

O PROBLEMA
    A MONTE foi consultada em 13/08/2026 e recebeu `cStat 656` com
    `ultNSU = 1361` — uma divergência externa de 1.361 NSU. Só que a ING 3B-R2,
    que criou os campos para representar isso, foi escrita DEPOIS. O
    `aplicar_divergencia()` roda dentro do `pipeline.ingerir`, e a empresa não
    foi consultada de novo.

    Resultado: o checkpoint em disco tem o `656` no `ultimo_erro` e mais nada.
    Ao ser lido, os campos novos assumem o padrão — e a empresa aparece como
    `SEM_INFORMACAO` / `COMPLETA_DESDE_ZERO`, **indistinguível de uma empresa
    nova**. A divergência some da vista justamente em quem mais precisa dela.

O QUE ESTE MÓDULO FAZ, E O QUE ELE SE RECUSA A FAZER
    Ele promove evidência **já existente** a campo estruturado. Não vai à rede,
    não deduz e não completa lacuna.

    Em particular: **o `ultNSU` devolvido pela SEFAZ não está no disco.** O texto
    do erro guarda o `cStat` e o `xMotivo`, mas não o número — o `xMotivo` diz
    "deve ser utilizado o ultNSU", sem dizer qual.

    Por isso `nsu_observado` é PARÂMETRO OBRIGATÓRIO, acompanhado de
    `fonte_do_valor`. Quem chama declara de onde tirou o número, e a fonte fica
    gravada junto. Inferir 1361 de qualquer outra coisa seria inventar.

O QUE ISTO **NUNCA** TOCA
    • `ult_nsu` — a posição do acervo não muda; nada foi incorporado.
    • `INICIO_COBERTURA_DFE` — o marco pertence ao momento em que alguém decide
      assumir a cobertura, não a uma reconstrução de estado.
    • a rede.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import auditoria as aud
from . import checkpoint as cpm
from .ambiente import Ambiente, resolver as resolver_ambiente
from .identidade import normalizar

ORIGEM = "MIGRACAO_R2_DE_EVENTO_HISTORICO"

# ── desfechos ───────────────────────────────────────────────────────────────
MIGRADO = "MIGRADO"
JA_MIGRADO = "JA_MIGRADO"
SEM_CHECKPOINT = "SEM_CHECKPOINT"
SEM_EVIDENCIA_656 = "SEM_EVIDENCIA_656"
ACERVO_NAO_VAZIO = "ACERVO_NAO_VAZIO"
NSU_INVALIDO = "NSU_INVALIDO"
NAO_DIVERGE = "NAO_DIVERGE"
IDENTIDADE_INVALIDA = "IDENTIDADE_INVALIDA"

_CSTAT_656 = re.compile(r"cStat\s*:?\s*656\b", re.I)


@dataclass
class Resultado:
    identidade_mascarada: str = ""
    resultado: str = ""
    detalhe: str = ""
    nsu_observado: str = ""
    ult_nsu: str = ""
    estado_sincronismo: str = ""
    diagnostico: str = ""
    cobertura_anterior: str = ""

    @property
    def alterou(self) -> bool:
        return self.resultado == MIGRADO

    def resumo(self) -> dict:
        return {"empresa": self.identidade_mascarada, "resultado": self.resultado,
                "checkpoint_acervo": self.ult_nsu,
                "checkpoint_sefaz_observado": self.nsu_observado or None,
                "estado_sincronismo": self.estado_sincronismo or None,
                "diagnostico": self.diagnostico or None,
                "cobertura_anterior": self.cobertura_anterior or None,
                "detalhe": self.detalhe or None}

    def linha(self) -> str:
        return (f"[{self.resultado}] {self.identidade_mascarada} "
                f"acervo={self.ult_nsu} observado={self.nsu_observado or '—'} "
                f"{self.estado_sincronismo}"
                f"{'  · ' + self.detalhe if self.detalhe else ''}")


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conferir(dados_dir, identidade, servico=cpm.NFE_DISTRIBUICAO,
             ambiente="producao") -> dict:
    """O que a evidência em disco comprova. **Não grava nada.**"""
    dados = Path(dados_dir)
    ident = normalizar(identidade)
    if not ident.valido:
        return {"ok": False, "motivo": f"identidade inválida: {ident.motivo}"}
    amb = resolver_ambiente(ambiente)
    repo = cpm.RepositorioCheckpoint(dados)
    if not repo.existe(ident.valor, servico, amb):
        return {"ok": False, "motivo": "não há checkpoint em disco"}
    cp = repo.carregar(ident.valor, servico, amb)
    acervo = dados / ident.valor / "acervo"
    n_acervo = len(list(acervo.glob("*/*/*/original.xml"))) if acervo.exists() else 0
    return {
        "ok": True,
        "empresa": ident.mascarado(),
        "servico": cp.servico,
        "ambiente": cp.ambiente,
        "ult_nsu": cp.ult_nsu,
        "erro_e_656": bool(_CSTAT_656.search(cp.ultimo_erro or "")),
        "ultimo_erro": cp.ultimo_erro,
        "ultima_consulta": cp.ultima_consulta,
        "documentos_recebidos": cp.documentos_recebidos,
        "documentos_no_acervo": n_acervo,
        "nsu_no_texto_do_erro": bool(re.search(r"\b\d{4,15}\b",
                                               (cp.ultimo_erro or "").split("cStat")[-1]
                                               .replace("656", "", 1))),
        "estado_sincronismo_atual": cp.estado_sincronismo,
        "ja_migrado": bool(cp.migracao_r2),
    }


SINCRONIA_MATERIALIZADA = "SINCRONIA_MATERIALIZADA"
JA_EM_SINCRONIA = "JA_EM_SINCRONIA"
SEM_CONSULTA_UTIL = "SEM_CONSULTA_UTIL"
NAO_FECHA_EM_DIA = "NAO_FECHA_EM_DIA"

ORIGEM_SINCRONIA = "MATERIALIZADO_DE_CONSULTA_AUDITADA"


def materializar_sincronia_de_consulta(dados_dir, identidade,
                                       servico: str = cpm.NFE_DISTRIBUICAO,
                                       ambiente="producao", repo=None,
                                       agora: str = "") -> Resultado:
    """Aplica `EM_SINCRONIA` a partir da ÚLTIMA `CONSULTA` já auditada.

    POR QUE EXISTE
        A regra do `EM_SINCRONIA` foi implementada depois de a `00.***-53` ter
        sido consultada. A resposta dela — `137`, `ultNSU == maxNSU == 216` —
        está gravada na trilha, mas o estado ficou `SEM_INFORMACAO` porque o
        código que o grava ainda não existia.

        Isto **não é uma consulta nova** e **não inventa nada**: relê o registro
        que já está no disco e aplica a mesma regra
        (`registrar_sincronia_confirmada`), com as mesmas três guardas.

    O QUE NÃO FAZ
        Não cria ato `CONSULTA` (nem reconstruída — a consulta REAL já está
        registrada), não move `ult_nsu`, não muda cobertura, não toca no acervo.
    """
    dados = Path(dados_dir)
    ident = normalizar(identidade)
    res = Resultado(identidade_mascarada=ident.mascarado() if ident.valido
                    else "<inválida>")
    if not ident.valido:
        res.resultado, res.detalhe = IDENTIDADE_INVALIDA, ident.motivo
        return res

    amb = resolver_ambiente(ambiente)
    repo = repo or cpm.RepositorioCheckpoint(dados)
    if not repo.existe(ident.valor, servico, amb):
        res.resultado, res.detalhe = SEM_CHECKPOINT, "não há checkpoint em disco"
        return res
    cp = repo.carregar(ident.valor, servico, amb)
    res.ult_nsu = cp.ult_nsu
    res.estado_sincronismo = cp.estado_sincronismo
    res.cobertura_anterior = cp.cobertura_anterior

    if cp.estado_sincronismo == cpm.SINCRONISMO_EM_DIA:
        res.resultado = JA_EM_SINCRONIA
        res.detalhe = "o estado já está materializado"
        return res

    # A evidência é a última CONSULTA REAL da trilha — não reconstruída.
    consultas = [c for c in aud.abrir(dados, ident.valor).consultas()
                 if c.get("servico") == servico and c.get("ambiente") == amb.nome]
    if not consultas:
        res.resultado = SEM_CONSULTA_UTIL
        res.detalhe = "não há ato CONSULTA auditado para esta chave"
        return res
    u = consultas[-1]
    if not (u.get("transporte_ok") and not u.get("erro_tecnico")
            and u.get("resultado") in ("DOCUMENTOS_ENCONTRADOS", "NENHUM_DOCUMENTO")):
        res.resultado = SEM_CONSULTA_UTIL
        res.detalhe = (f"a última consulta não foi limpa "
                       f"(resultado={u.get('resultado')})")
        return res

    antes = cp.ult_nsu
    if not cp.registrar_sincronia_confirmada(u.get("ult_nsu") or "",
                                             u.get("max_nsu") or "",
                                             agora=agora):
        res.resultado = NAO_FECHA_EM_DIA
        res.detalhe = (f"a consulta auditada não prova sincronia "
                       f"(ultNSU={u.get('ult_nsu')} maxNSU={u.get('max_nsu')} "
                       f"acervo={cp.ult_nsu})")
        return res

    cp.migracao_r2 = dict(cp.migracao_r2 or {})
    cp.migracao_r2["sincronia"] = {
        "origem": ORIGEM_SINCRONIA,
        "materializado_em": agora or _agora(),
        "consulta_auditada_em": u.get("inicio"),
        "cstat": u.get("cstat"),
        "ult_nsu": u.get("ult_nsu"),
        "max_nsu": u.get("max_nsu"),
        "aviso": ("estado aplicado retroativamente a partir de uma CONSULTA "
                  "REAL já auditada; nenhuma chamada nova foi feita"),
    }
    if cp.ult_nsu != antes:
        raise RuntimeError("materializar sincronia alterou ult_nsu")
    repo.salvar(cp)

    res.resultado = SINCRONIA_MATERIALIZADA
    res.estado_sincronismo = cp.estado_sincronismo
    res.cobertura_anterior = cp.cobertura_anterior
    res.detalhe = f"cStat {u.get('cstat')} de {str(u.get('inicio'))[:19]}"
    return res


def migrar_estado_656(dados_dir, identidade, *, nsu_observado: str,
                      fonte_do_valor: str, servico: str = cpm.NFE_DISTRIBUICAO,
                      ambiente="producao", repo=None, trilha=None,
                      agora: str = "") -> Resultado:
    """Promove o `656` histórico a estado estruturado. **Idempotente.**

    `nsu_observado` e `fonte_do_valor` são obrigatórios: o número não está no
    disco, e gravá-lo sem dizer de onde veio seria transformar documentação em
    fato sem rastro.
    """
    dados = Path(dados_dir)
    ident = normalizar(identidade)
    res = Resultado(identidade_mascarada=ident.mascarado() if ident.valido
                    else "<inválida>")
    if not ident.valido:
        res.resultado, res.detalhe = IDENTIDADE_INVALIDA, ident.motivo
        return res
    if not str(fonte_do_valor or "").strip():
        res.resultado = NSU_INVALIDO
        res.detalhe = "fonte_do_valor é obrigatória — de onde veio o ultNSU?"
        return res

    amb = resolver_ambiente(ambiente)
    repo = repo or cpm.RepositorioCheckpoint(dados)

    # ── 1. o checkpoint existe? ───────────────────────────────────────────
    if not repo.existe(ident.valor, servico, amb):
        res.resultado = SEM_CHECKPOINT
        res.detalhe = "não há checkpoint em disco para esta chave"
        return res
    cp = repo.carregar(ident.valor, servico, amb)
    res.ult_nsu = cp.ult_nsu
    res.estado_sincronismo = cp.estado_sincronismo
    res.cobertura_anterior = cp.cobertura_anterior

    # ── 2. o erro persistido é MESMO o 656? ───────────────────────────────
    if not _CSTAT_656.search(cp.ultimo_erro or ""):
        res.resultado = SEM_EVIDENCIA_656
        res.detalhe = ("o `ultimo_erro` do checkpoint não registra cStat 656; "
                       "sem evidência, não se migra")
        return res

    # ── 3. a chave confere com o que foi pedido ───────────────────────────
    if (cp.identidade, cp.servico, cp.ambiente) != (ident.valor, servico, amb.nome):
        res.resultado = IDENTIDADE_INVALIDA
        res.detalhe = (f"o checkpoint é de ({cp.servico}, {cp.ambiente}), "
                       f"não de ({servico}, {amb.nome})")
        return res

    # ── 4. nada foi incorporado — a premissa do estado ────────────────────
    acervo = dados / ident.valor / "acervo"
    n_acervo = len(list(acervo.glob("*/*/*/original.xml"))) if acervo.exists() else 0
    if n_acervo or cp.documentos_recebidos or cpm.nsu_int(cp.ult_nsu):
        res.resultado = ACERVO_NAO_VAZIO
        res.detalhe = (f"há {n_acervo} documento(s) no acervo e ult_nsu="
                       f"{cp.ult_nsu}; este estado é só para a rejeição sem "
                       f"incorporação")
        return res

    # ── 5. o valor observado é utilizável e está à frente ─────────────────
    obs = cpm.normalizar_nsu(nsu_observado)
    if cpm.nsu_int(obs) == 0:
        res.resultado, res.detalhe = NSU_INVALIDO, "nsu_observado vazio ou zero"
        return res
    if cpm.nsu_int(obs) <= cpm.nsu_int(cp.ult_nsu):
        res.resultado = NAO_DIVERGE
        res.detalhe = f"{obs} não está à frente de {cp.ult_nsu}"
        return res

    # ── 6. idempotência ───────────────────────────────────────────────────
    if cp.migracao_r2 and cp.nsu_observado_sefaz == obs:
        res.resultado = JA_MIGRADO
        res.detalhe = "o estado já foi materializado antes"
        res.nsu_observado = cp.nsu_observado_sefaz
        res.estado_sincronismo = cp.estado_sincronismo
        res.diagnostico = cp.diagnostico
        res.cobertura_anterior = cp.cobertura_anterior
        return res

    # ── 7. grava ──────────────────────────────────────────────────────────
    quando = agora or _agora()
    antes = cp.ult_nsu
    cp.registrar_observacao_sefaz(obs, cstat="656", agora=quando)
    cp.marcar_possivel_consumidor_externo(
        f"cStat 656 histórico devolveu ultNSU {obs} com acervo local em {antes}")
    cp.migracao_r2 = {
        "origem": ORIGEM,
        "reconstruido_em": quando,
        "cstat_de_origem": "656",
        "nsu_observado": obs,
        "fonte_do_valor": str(fonte_do_valor).strip(),
        "evidencia_em_disco": {
            "ultimo_erro": cp.ultimo_erro,
            "ultima_consulta": cp.ultima_consulta,
            "ult_nsu": antes,
            "documentos_recebidos": cp.documentos_recebidos,
        },
        "aviso": ("estado reconstruído a partir de evidência persistida; o "
                  "ultNSU NÃO estava no disco e veio da fonte declarada acima"),
    }

    if cp.ult_nsu != antes:                    # cinto e suspensório
        raise RuntimeError("migracao_r2 alterou ult_nsu — isto nunca pode acontecer")
    if cp.marco_cobertura:
        raise RuntimeError("migracao_r2 não pode criar INICIO_COBERTURA_DFE")

    repo.salvar(cp)

    # ── 8. auditoria retroativa, marcada como tal ─────────────────────────
    trilha = trilha or aud.abrir(dados, ident.valor)
    trilha.registrar(
        aud.CONSULTA_RECONSTRUIDA,
        servico=servico, ambiente=amb.nome, tipo_consulta="distNSU",
        nsu_enviado=antes, cstat="656",
        xmotivo=cp.ultimo_erro, ult_nsu=obs,
        # o que NÃO se sabe fica None — não se falsifica precisão
        max_nsu=None, doczip=0, endpoint=None, duracao_s=None,
        inicio=cp.ultima_consulta or None, fim=None,
        transporte_ok=True,       # houve resposta de negócio, logo houve transporte
        resultado="CONSUMO_INDEVIDO",
        reconstruido=True,
        fonte_da_reconstrucao=str(fonte_do_valor).strip(),
        aviso=("registro NÃO produzido no momento da chamada; reconstruído "
               "depois, a partir do checkpoint e do erro persistidos. "
               "Horário de fim, duração e endpoint não foram preservados. "
               "Nenhum documento associado."))

    res.resultado = MIGRADO
    res.nsu_observado = cp.nsu_observado_sefaz
    res.estado_sincronismo = cp.estado_sincronismo
    res.diagnostico = cp.diagnostico
    res.cobertura_anterior = cp.cobertura_anterior
    res.detalhe = f"fonte do valor: {fonte_do_valor}"
    return res
