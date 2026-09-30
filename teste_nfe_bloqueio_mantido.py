#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NF-e — "mantida bloqueada por falta de evidência": a decisão de NÃO decidir.

    python teste_nfe_bloqueio_mantido.py

O QUE SE PROVA
    Que "ninguém olhou esta empresa ainda" deixou de ser igual a "alguém
    olhou, não achou evidência e decidiu manter fora da automação". Até aqui
    os dois eram o mesmo item na fila, e a análise não deixava rastro nenhum.

    A decisão nova **não muda nada que decida consulta**: nem `ult_nsu`, nem
    `max_nsu`, nem checkpoint, nem `estado_sincronismo`, nem diagnóstico, nem
    cobertura, nem `revisao_sequencia`. Ela acrescenta autor, data e motivo —
    e é por isso que a empresa continua NÃO ELEGÍVEL depois dela, o que este
    teste confere chamando o próprio controlador.

    Exige sessão, papel de administrador, CSRF e justificativa de 15 a 500
    caracteres, com o corpo fechado: `ult_nsu`, `max_nsu`, `checkpoint`,
    `caminho` e `estado_sincronismo` são recusados com 400.

    Repetir é definido, não silencioso: a MESMA justificativa é recusada com
    409 `DECISAO_REPETIDA` e não grava nada; uma justificativa NOVA reafirma,
    conta a reafirmação e preserva a data da primeira decisão.

    Nenhum dado real: raízes temporárias, CNPJ de teste, justificativa
    inventada. A pasta de produção nunca é tocada, e a suíte inteira roda com
    a rede para fora proibida.
"""
from __future__ import annotations

import io as _io
import json
import socket as _socket
import sqlite3
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import fiscale_decisoes_nfe as dec                        # noqa: E402
import fiscale_papeis as pp                               # noqa: E402
from teste_clientes_modelo import Instancia               # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

# ── rede proibida: a prova de que a decisão não consulta a SEFAZ ────────────
_conectar_real = _socket.socket.connect


def _connect(self, endereco, *a, **kw):
    alvo = endereco[0] if isinstance(endereco, tuple) else str(endereco)
    if alvo not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError("tentou abrir rede para " + str(endereco))
    return _conectar_real(self, endereco, *a, **kw)


_socket.socket.connect = _connect

CNPJ_A = "11222333000181"          # divergência externa, sem revisão (caso EDU)
CNPJ_B = "11444777000161"          # em revisão de sequência
CNPJ_C = "66789006000106"          # saudável: nada a manter
JUST = "Cliente ainda nao respondeu sobre os NSU faltantes; sem evidencia."
JUST2 = "Conferido de novo em outubro: contador anterior nao enviou nada."
ROTA = "/api/nfe/admin/bloqueio/manter"


def secao(t):
    print("\n" + t)


def ok(cond, desc):
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        _erros.append(desc)
        print("  FALHOU   " + desc)


def igual(a, b, desc):
    ok(a == b, "%s (%r == %r)" % (desc, a, b))


# ── fixturas: montadas pelo DOMÍNIO, nunca escrevendo JSON ─────────────────
def preparar(dados: Path, cnpj: str, *, revisao: bool, divergencia: bool,
             ult="000000000000000", observado="000000000000002"):
    from ingestao import checkpoint as cpm
    from ingestao import operacao as op
    repo_cp = cpm.RepositorioCheckpoint(dados)
    repo_op = op.RepositorioOperacao(dados)
    cp = repo_cp.carregar(cnpj, dec.SERVICO_PADRAO, dec.AMBIENTE_PADRAO)
    cp.ult_nsu = ult
    cp.max_nsu = observado if divergencia else ult
    cp.status = cpm.OK
    if divergencia:
        # É o caminho real da EDU JANGA: um 137 informou posição à frente.
        cp.registrar_observacao_sefaz(observado, cstat="137")
        cp.marcar_possivel_consumidor_externo(
            "cStat 137 devolveu ultNSU %s com acervo local em %s" % (observado, ult))
    repo_cp.salvar(cp)
    estado = repo_op.carregar(cnpj, dec.SERVICO_PADRAO, dec.AMBIENTE_PADRAO)
    if revisao:
        estado.exigir_revisao_de_sequencia("656 de sequência (fixtura de teste)")
    repo_op.salvar(estado)
    return repo_cp, repo_op


def ler(dados: Path, cnpj: str):
    from ingestao import checkpoint as cpm
    from ingestao import operacao as op
    cp = cpm.RepositorioCheckpoint(dados).carregar(
        cnpj, dec.SERVICO_PADRAO, dec.AMBIENTE_PADRAO)
    st = op.RepositorioOperacao(dados).carregar(
        cnpj, dec.SERVICO_PADRAO, dec.AMBIENTE_PADRAO)
    return cp, st


def retrato_fiscal(cp) -> dict:
    """Tudo que a decisão NÃO pode encostar."""
    return {"ult_nsu": cp.ult_nsu, "max_nsu": cp.max_nsu,
            "estado_sincronismo": cp.estado_sincronismo,
            "nsu_observado_sefaz": cp.nsu_observado_sefaz,
            "diagnostico": cp.diagnostico,
            "cobertura_anterior": cp.cobertura_anterior,
            "marco_cobertura": dict(cp.marco_cobertura or {}),
            "status": cp.status}


def main() -> int:
    # ══════════════════════════════════════════════════════════════════
    secao("1. Domínio: a empresa passa de 'sem decisão' para 'bloqueada por decisão'")
    # ══════════════════════════════════════════════════════════════════
    with tempfile.TemporaryDirectory(prefix="fiscale_bloq_") as tmp:
        dados = Path(tmp)
        preparar(dados, CNPJ_A, revisao=False, divergencia=True)
        preparar(dados, CNPJ_B, revisao=True, divergencia=False)

        cp0, st0 = ler(dados, CNPJ_A)
        fiscal_antes = retrato_fiscal(cp0)
        ok(st0.decisao_pendente, "antes: a decisão está pendente")
        igual(st0.bloqueio_mantido, {}, "e não há registro nenhum")

        r = dec.manter_bloqueio(dados, CNPJ_A, quem="admin", justificativa=JUST)
        igual(r["tipo"], "BLOQUEIO_MANTIDO_SEM_EVIDENCIA", "o tipo da decisão")
        igual(r["resultado"], "REGISTRADA", "primeira decisão: REGISTRADA")
        igual(r["estado_no_momento"], "DIVERGENCIA_EXTERNA", "com o estado do momento")
        ok(r["consulta_disparada"] is False, "e nenhuma consulta foi disparada")
        ok(CNPJ_A not in json.dumps(r), "a empresa sai mascarada na resposta")

        cp1, st1 = ler(dados, CNPJ_A)
        ok(not st1.decisao_pendente, "depois: a decisão deixou de ser pendente")
        igual(st1.bloqueio_mantido.get("operador"), "admin", "com o operador")
        igual(st1.bloqueio_mantido.get("justificativa"), JUST, "e a justificativa")
        ok(st1.bloqueio_mantido.get("em"), "com data e hora")
        igual(st1.bloqueio_mantido.get("reafirmacoes"), 0, "sem reafirmação ainda")

        secao("2. Nenhum dado fiscal foi alterado")
        igual(retrato_fiscal(cp1), fiscal_antes,
              "checkpoint idêntico: posse, max, sincronismo, diagnóstico, cobertura")
        igual(st1.revisao_sequencia, False, "a revisão de sequência não mudou")
        igual(st1.proxima_consulta_permitida_em, st0.proxima_consulta_permitida_em,
              "o cooldown não mudou")
        igual(st1.ultimo_cstat, st0.ultimo_cstat, "o último cStat não mudou")
        igual(r["antes"]["ult_nsu"], r["depois"]["ult_nsu"],
              "o retrato antes/depois mostra a mesma posse")
        ok(r["antes"]["decisao_pendente"] is True
           and r["depois"]["decisao_pendente"] is False,
           "e o que mudou foi só o estado de decisão")

        secao("3. A empresa continua FORA da elegibilidade")
        from ingestao import controlador as ctrl
        from ingestao import operacao as op
        el = ctrl.avaliar(dados, CNPJ_A)
        ok(not el.pode, "o controlador continua recusando a consulta")
        # Na raiz de teste não há certificado, e essa checagem vem ANTES da
        # divergência: o que se prova aqui é que a decisão não tornou ninguém
        # elegível. A divergência em si continua no checkpoint, conferida acima.
        ok(el.estado in op.NAO_CONSULTAVEIS,
           "e o estado continua entre os não consultáveis (%s)" % el.estado)
        igual(ler(dados, CNPJ_A)[0].estado_sincronismo, "DIVERGENCIA_EXTERNA",
              "com a divergência intacta no checkpoint")
        el_b = ctrl.avaliar(dados, CNPJ_B)
        ok(not el_b.pode, "a que está em revisão também segue fora")

        secao("4. Repetir é definido: mesma justificativa não empilha")
        try:
            dec.manter_bloqueio(dados, CNPJ_A, quem="admin", justificativa=JUST)
            ok(False, "a repetição deveria ter sido recusada")
        except dec.DecisaoRecusada as e:
            igual(e.motivo, "DECISAO_REPETIDA", "recusa com motivo em código")
            ok("reafirmar" in (e.detalhe or "").lower(),
               "e explica como reafirmar")
        _, st_rep = ler(dados, CNPJ_A)
        igual(st_rep.bloqueio_mantido, st1.bloqueio_mantido,
              "o estado ficou byte a byte igual (nada empilhou)")

        secao("5. Reafirmar com motivo novo preserva a primeira data")
        r2 = dec.manter_bloqueio(dados, CNPJ_A, quem="maria", justificativa=JUST2)
        igual(r2["resultado"], "REAFIRMADA", "resultado REAFIRMADA")
        _, st2 = ler(dados, CNPJ_A)
        igual(st2.bloqueio_mantido.get("reafirmacoes"), 1, "conta a reafirmação")
        igual(st2.bloqueio_mantido.get("primeira_em"),
              st1.bloqueio_mantido.get("em"), "e preserva a data da primeira")
        igual(st2.bloqueio_mantido.get("operador"), "maria", "com o novo autor")
        igual(retrato_fiscal(ler(dados, CNPJ_A)[0]), fiscal_antes,
              "e o checkpoint continua intocado")

        secao("6. Revisão de sequência: respeita o estado do domínio")
        r3 = dec.manter_bloqueio(dados, CNPJ_B, quem="admin", justificativa=JUST)
        igual(r3["estado_no_momento"], "REVISAO_DE_SEQUENCIA",
              "registra que o bloqueio era a revisão")
        _, st_b = ler(dados, CNPJ_B)
        ok(st_b.revisao_sequencia,
           "a empresa CONTINUA em revisão — a decisão não substitui encerrar")
        ok(not st_b.decisao_pendente, "mas agora consta que foi analisada")

        secao("7. Empresa saudável não tem bloqueio a manter")
        preparar(dados, CNPJ_C, revisao=False, divergencia=False, observado="")
        try:
            dec.manter_bloqueio(dados, CNPJ_C, quem="admin", justificativa=JUST)
            ok(False, "deveria recusar")
        except dec.DecisaoRecusada as e:
            igual(e.motivo, "NAO_ESTA_BLOQUEADA", "recusa: NAO_ESTA_BLOQUEADA")

        secao("8. Justificativa e operador")
        for texto, motivo in ((" ", "JUSTIFICATIVA_CURTA"),
                              ("curta demais", "JUSTIFICATIVA_CURTA"),
                              ("x" * 501, "JUSTIFICATIVA_LONGA")):
            try:
                dec.manter_bloqueio(dados, CNPJ_B, quem="admin", justificativa=texto)
                ok(False, "deveria recusar %r" % texto[:12])
            except dec.DecisaoRecusada as e:
                igual(e.motivo, motivo, "justificativa %r → %s" % (texto[:12], motivo))
        try:
            dec.manter_bloqueio(dados, CNPJ_B, quem="  ", justificativa=JUST2)
            ok(False, "deveria exigir operador")
        except dec.DecisaoRecusada as e:
            igual(e.motivo, "SEM_OPERADOR", "sem operador: SEM_OPERADOR")

        secao("9. Isolamento entre empresas")
        with tempfile.TemporaryDirectory(prefix="fiscale_bloq2_") as tmp2:
            d2 = Path(tmp2)
            preparar(d2, CNPJ_A, revisao=False, divergencia=True)
            preparar(d2, CNPJ_B, revisao=False, divergencia=True)
            dec.manter_bloqueio(d2, CNPJ_A, quem="admin", justificativa=JUST)
            ok(not ler(d2, CNPJ_A)[1].decisao_pendente, "a decidida tem decisão")
            ok(ler(d2, CNPJ_B)[1].decisao_pendente,
               "e a outra continua sem decisão nenhuma")

        secao("10. A trilha da empresa guarda a decisão, sem segredo")
        trilhas = sorted((dados / CNPJ_A / "auditoria").glob("*.jsonl"))
        ok(trilhas, "a trilha da empresa existe")
        linhas = [json.loads(l) for t in trilhas
                  for l in t.read_text("utf-8").splitlines() if l.strip()]
        atos = [l for l in linhas if l.get("ato") == "DECISAO_ADMINISTRATIVA"]
        igual(len(atos), 2, "duas decisões registradas (a primeira e a reafirmação)")
        a = atos[0]
        igual(a.get("decisao"), "BLOQUEIO_MANTIDO_SEM_EVIDENCIA", "com o tipo")
        igual(a.get("operador"), "admin", "com o operador")
        igual(a.get("justificativa"), JUST, "com a justificativa")
        igual(a.get("servico"), dec.SERVICO_PADRAO, "com o serviço")
        igual(a.get("ambiente"), "producao", "com o ambiente")
        ok(a.get("em"), "com data e hora")
        ok(a.get("antes") and a.get("depois"), "com o estado antes e depois")
        ok(CNPJ_A not in json.dumps(linhas), "e a empresa aparece mascarada")
        bruto = json.dumps(linhas, ensure_ascii=False).lower()
        for proibido in ("senha", "pfx", "certificado", "token", "frase-senha"):
            ok(proibido not in bruto, "nada de %r na trilha" % proibido)

        secao("11. A listagem separa 'sem decisão' de 'mantida por decisão'")
        fila = dec.listar_pendentes(dados)
        por_id = {e["identidade"]: e for e in fila["empresas"]}
        ok(not por_id[CNPJ_A]["decisao_pendente"], "A aparece como já decidida")
        igual(por_id[CNPJ_A]["bloqueio_mantido"].get("operador"), "maria",
              "com o operador da última decisão")
        igual(fila["mantidas_por_decisao"], 2, "o resumo conta as decididas")
        igual(fila["sem_decisao"], 0, "e as pendentes")
        ok(all(e.get("pode_manter_bloqueio") for e in fila["empresas"]),
           "a ação está disponível para as empresas travadas")

    # ══════════════════════════════════════════════════════════════════
    secao("12. HTTP: sessão, papel, CSRF, corpo fechado e idempotência")
    # ══════════════════════════════════════════════════════════════════
    inst = Instancia()
    try:
        ok(inst.subir(), "FISCALE de teste na porta %d" % inst.porta)
        preparar(inst.dados, CNPJ_A, revisao=False, divergencia=True)
        preparar(inst.dados, CNPJ_B, revisao=True, divergencia=False)

        def pedir(metodo, caminho, corpo=None, csrf=None):
            dados_corpo = json.dumps(corpo).encode() if corpo is not None else None
            c = __import__("http.client", fromlist=["x"]).HTTPConnection(
                "127.0.0.1", inst.porta, timeout=120)
            cab = {}
            if inst.cookie:
                cab["Cookie"] = inst.cookie
            if dados_corpo is not None:
                cab["Content-Type"] = "application/json"
                cab["Content-Length"] = str(len(dados_corpo))
            if csrf is not None:
                cab["X-Fiscale-Csrf"] = csrf
            c.request(metodo, caminho, body=dados_corpo, headers=cab)
            r = c.getresponse()
            corpo_r = r.read()
            h = {k.lower(): v for k, v in r.getheaders()}
            if "set-cookie" in h:
                inst.cookie = h["set-cookie"].split(";")[0]
            c.close()
            try:
                return r.status, json.loads(corpo_r or b"{}")
            except Exception:
                return r.status, {}

        st, _ = pedir("POST", ROTA, {"id": CNPJ_A, "justificativa": JUST},
                      csrf="qualquer")
        igual(st, 401, "sem sessão: 401")

        st, _ = pedir("POST", "/api/primeiro-acesso", {"senha": Instancia.SENHA})
        igual(st, 200, "admin criado na instância de teste")
        st, r = pedir("GET", "/api/csrf")
        token = r.get("csrf") or ""
        ok(st == 200 and len(token) >= 32, "o admin obtém CSRF")

        st, _ = pedir("POST", ROTA, {"id": CNPJ_A, "justificativa": JUST})
        igual(st, 403, "sem CSRF: 403")
        st, _ = pedir("POST", ROTA, {"id": CNPJ_A, "justificativa": JUST},
                      csrf="a" * 64)
        igual(st, 403, "CSRF errado: 403")

        st, r = pedir("POST", ROTA, {"id": CNPJ_A, "justificativa": "curta"},
                      csrf=token)
        igual(st, 409, "justificativa curta: 409")
        igual(r.get("motivo"), "JUSTIFICATIVA_CURTA", "com o motivo em código")

        for campo in ("ult_nsu", "max_nsu", "checkpoint", "caminho",
                      "estado_sincronismo", "origem_informacao"):
            st, r = pedir("POST", ROTA,
                          {"id": CNPJ_A, "justificativa": JUST, campo: "x"},
                          csrf=token)
            igual(st, 400, "%s no corpo: 400" % campo)
            ok(campo in (r.get("campos_recusados") or []),
               "  e a resposta diz qual campo foi recusado")
        cp_http, st_http = ler(inst.dados, CNPJ_A)
        igual(cp_http.ult_nsu, "000000000000000", "nenhuma tentativa moveu o ult_nsu")
        ok(st_http.decisao_pendente, "e nenhuma decisão foi gravada")

        st, r = pedir("POST", ROTA, {"id": CNPJ_A, "justificativa": JUST},
                      csrf=token)
        igual(st, 200, "com sessão, papel, CSRF e justificativa: 200")
        igual(r.get("resultado"), "REGISTRADA", "a primeira decisão é registrada")
        ok(not ler(inst.dados, CNPJ_A)[1].decisao_pendente, "a empresa foi decidida")
        ok(ler(inst.dados, CNPJ_B)[1].decisao_pendente,
           "e a outra continua sem decisão (isolamento pelo HTTP também)")

        st, r = pedir("POST", ROTA, {"id": CNPJ_A, "justificativa": JUST},
                      csrf=token)
        igual(st, 409, "repetir a MESMA justificativa: 409")
        igual(r.get("motivo"), "DECISAO_REPETIDA", "com motivo DECISAO_REPETIDA")
        st, r = pedir("POST", ROTA, {"id": CNPJ_A, "justificativa": JUST2},
                      csrf=token)
        igual(st, 200, "motivo novo: 200")
        igual(r.get("resultado"), "REAFIRMADA", "e é uma reafirmação")

        st, r = pedir("GET", "/api/nfe/admin/pendencias")
        ok(st == 200 and r.get("mantidas_por_decisao") == 1
           and r.get("sem_decisao") == 1,
           "a listagem separa decidida de pendente (%s/%s)"
           % (r.get("mantidas_por_decisao"), r.get("sem_decisao")))

        secao("13. A auditoria de acessos registrou a decisão")
        con = sqlite3.connect("file:%s?mode=ro"
                              % str(inst.dados / "auditoria.db").replace("\\", "/"),
                              uri=True)
        linhas = list(con.execute(
            "SELECT evento, resultado, ator_uid, derivado, detalhe, ip FROM evento "
            "WHERE evento LIKE 'NFE_%' ORDER BY id"))
        con.close()
        eventos = [l[0] for l in linhas]
        ok("NFE_BLOQUEIO_MANTIDO" in eventos, "o evento próprio foi auditado")
        ok("NFE_DECISAO_RECUSADA" in eventos, "e as recusas também")
        boa = [l for l in linhas if l[0] == "NFE_BLOQUEIO_MANTIDO"][0]
        igual(boa[1], "OK", "com resultado OK")
        igual(boa[2], "admin", "com o operador")
        ok(boa[3] and CNPJ_A not in boa[3], "com a empresa mascarada")
        ok(JUST[:40] in (boa[4] or ""), "com a justificativa")
        ok(boa[5], "e com o IP de origem")
        bruto = json.dumps(linhas, ensure_ascii=False).lower()
        for proibido in ("senha", "pfx", "frase-senha", Instancia.SENHA.lower()):
            ok(proibido not in bruto, "nada de %r na auditoria" % proibido)

        secao("14. Nenhum dado fiscal mudou pelo caminho HTTP")
        cp_fim, st_fim = ler(inst.dados, CNPJ_A)
        igual(cp_fim.ult_nsu, "000000000000000", "posse intacta")
        igual(cp_fim.max_nsu, "000000000000002", "max_nsu intacto")
        igual(cp_fim.estado_sincronismo, "DIVERGENCIA_EXTERNA", "divergência intacta")
        igual(cp_fim.diagnostico, "POSSIVEL_CONSUMIDOR_EXTERNO", "diagnóstico intacto")
        igual(dict(cp_fim.marco_cobertura or {}), {}, "nenhum marco foi criado")
        igual(cp_fim.cobertura_anterior, "DESCONHECIDA", "cobertura intacta")
        ok(ler(inst.dados, CNPJ_B)[1].revisao_sequencia,
           "e a empresa em revisão continua em revisão")
    finally:
        inst.derrubar()

    # ══════════════════════════════════════════════════════════════════
    secao("15. Estático: papéis, proxy, ausência de rede e texto da tela")
    # ══════════════════════════════════════════════════════════════════
    ok(not pp.pode(pp.OPERADOR, "POST", ROTA), "operador NÃO alcança a rota")
    ok(pp.pode(pp.ADMIN, "POST", ROTA), "o admin alcança")
    igual(pp.classificar("POST", ROTA), pp.ADMIN, "e ela é classificada como admin")

    servidor = _io.open(RAIZ / "fiscale_server.py", encoding="utf-8").read()
    ok('"%s",' % ROTA in servidor,
       "a rota está na lista de exceções do proxy (senão viraria 404 do NFS-e)")
    ok('if rota == "%s"' % ROTA in servidor, "e é atendida aqui, com os portões")

    fonte = _io.open(RAIZ / "fiscale_decisoes_nfe.py", encoding="utf-8").read()
    for proibido in ("servico_distribuicao", "consultar_empresa", "requests",
                     "urllib.request", "fabrica_padrao"):
        ok(proibido not in fonte, "o módulo não menciona %r" % proibido)
    ok("manter_bloqueio_sem_evidencia" in fonte,
       "e usa a função do domínio, não edição de JSON")

    dominio = _io.open(RAIZ / "nfse" / "backend" / "ingestao" / "operacao.py",
                       encoding="utf-8").read()
    i = dominio.index("def manter_bloqueio_sem_evidencia")
    corpo = dominio[i:dominio.index("def registrar_sucesso", i)]
    # A docstring CITA os campos que a função promete não tocar; o que vale é
    # não haver ATRIBUIÇÃO a nenhum deles. Procurar a palavra solta reprovaria
    # justamente o comentário que explica a promessa.
    import re as _re
    escritos = set(_re.findall(r"self\.([A-Za-z_]+)\s*=(?!=)", corpo))
    igual(sorted(escritos), ["bloqueio_mantido"],
          "a função do domínio só escreve em `bloqueio_mantido`")
    for proibido in ("ult_nsu", "max_nsu", "revisao_sequencia",
                     "proxima_consulta_permitida_em", "estado_sincronismo"):
        ok(proibido not in escritos,
           "não escreve em %r" % proibido)

    tela = _io.open(RAIZ / "web" / "nfe_pendencias.html", encoding="utf-8").read()
    for trecho in ("Bloqueada por decisão administrativa".lower(),
                   "mantida bloqueada por falta de evidência",
                   "manter bloqueada por falta de evidência",
                   "continua fora da",
                   "reafirmar o bloqueio"):
        ok(trecho in tela.lower(), "a tela traz “%s”" % trecho)
    ok(ROTA in tela, "e chama a rota nova")
    ok("não consulta a SEFAZ, não libera consulta" in tela,
       "dizendo que a decisão não consulta nem libera")
    ok("NÃO TEM DESFAZER" in tela,
       "e a confirmação da cobertura passou a avisar que não tem desfazer")

    print()
    print("=" * 62)
    print("%d ok · %d falha(s)" % (_ok, _falhas))
    if _erros:
        print("\nFalhas:")
        for e in _erros:
            print("  -", e)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
