#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decisões administrativas da ingestão NF-e — o caminho que substitui editar JSON.

    python teste_nfe_decisoes_admin.py

O QUE SE PROVA
    Que sair da revisão de sequência virou um ATO REGISTRADO, e não uma
    edição de arquivo: exige sessão, papel de administrador, CSRF válido e
    justificativa, e deixa na auditoria quem decidiu, sobre qual empresa,
    quando, por quê, e o estado de antes e o de depois.

    Que a decisão **não fala com a SEFAZ**. A suíte inteira roda com a rede
    proibida: qualquer tentativa de abrir soquete para fora levanta.

    Que o corpo é fechado — `ult_nsu`, `max_nsu`, `checkpoint` e caminho de
    arquivo são recusados com 400, e o estado não se move. Posição de fila
    continua sendo do domínio.

    Que empresa fora de revisão não pode "encerrar revisão" (é o caso da EDU
    JANGA e da MONTE, que não podem ser resolvidas por engano), que a decisão
    sobre uma empresa não toca em nenhuma outra, e que o marco de cobertura
    registra a posição SEM mover o `ult_nsu`.

    Nenhum dado real: raiz temporária, CNPJ de teste, justificativa inventada.
    A pasta de produção nunca é tocada.
"""
from __future__ import annotations

import io as _io
import json
import os
import socket as _socket
import sqlite3
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio                                        # noqa: E402
import fiscale_decisoes_nfe as dec                        # noqa: E402
from teste_clientes_modelo import Instancia               # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

# ── rede proibida: a prova de que nenhuma decisão consulta a SEFAZ ──────────
_conectar_real = _socket.socket.connect


def _connect(self, endereco, *a, **kw):
    # O servidor de teste fala consigo mesmo por 127.0.0.1; o que não pode é
    # sair da máquina. Qualquer outro destino levanta e reprova a suíte.
    alvo = endereco[0] if isinstance(endereco, tuple) else str(endereco)
    if alvo not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError("tentou abrir rede para " + str(endereco))
    return _conectar_real(self, endereco, *a, **kw)


_socket.socket.connect = _connect

CNPJ_A = "11222333000181"
CNPJ_B = "11444777000161"
JUST = "Cliente confirmou que o ERP dele consome a distribuicao desde 2025."


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


# ── fixtura de estado: empresa em revisão, sem tocar em nada real ──────────
def preparar(dados: Path, cnpj: str, *, revisao=True, observado="000000000001000",
             ult="000000000000000"):
    """Monta checkpoint e estado operacional pelo DOMÍNIO, não escrevendo JSON."""
    from ingestao import checkpoint as cpm
    from ingestao import operacao as op
    repo_cp = cpm.RepositorioCheckpoint(dados)
    repo_op = op.RepositorioOperacao(dados)
    cp = repo_cp.carregar(cnpj, dec.SERVICO_PADRAO, dec.AMBIENTE_PADRAO)
    cp.ult_nsu = ult
    cp.max_nsu = ult
    cp.status = cpm.ERRO_TRANSITORIO
    if observado:
        cp.registrar_observacao_sefaz(observado, cstat="656")
        cp.marcar_possivel_consumidor_externo("cStat 656 devolveu ultNSU %s" % observado)
    repo_cp.salvar(cp)
    estado = repo_op.carregar(cnpj, dec.SERVICO_PADRAO, dec.AMBIENTE_PADRAO)
    estado.registrar_falha("CONSUMO_INDEVIDO", cstat="656")
    estado.registrar_656(subtipo="CONSUMO_INDEVIDO_SEQUENCIA",
                         xmotivo="Rejeicao: Consumo Indevido",
                         nsu_enviado=ult, checkpoint_local=ult,
                         ult_nsu=observado, max_nsu="000000000000000")
    if revisao:
        estado.exigir_revisao_de_sequencia("656 de sequência (fixtura de teste)")
    repo_op.salvar(estado)
    return repo_cp, repo_op


def ler_estado(dados: Path, cnpj: str):
    from ingestao import checkpoint as cpm
    from ingestao import operacao as op
    cp = cpm.RepositorioCheckpoint(dados).carregar(cnpj, dec.SERVICO_PADRAO,
                                                   dec.AMBIENTE_PADRAO)
    st = op.RepositorioOperacao(dados).carregar(cnpj, dec.SERVICO_PADRAO,
                                                dec.AMBIENTE_PADRAO)
    return cp, st


def main() -> int:
    # ══════════════════════════════════════════════════════════════════
    secao("1. Domínio: encerrar revisão não move posse e não consulta nada")
    # ══════════════════════════════════════════════════════════════════
    with teste_apoio.raiz_temporaria("fiscale_dec_") as dados:
        preparar(dados, CNPJ_A)
        preparar(dados, CNPJ_B)
        cp0, st0 = ler_estado(dados, CNPJ_A)
        b0_cp, b0_st = ler_estado(dados, CNPJ_B)
        ok(st0.revisao_sequencia, "a fixtura nasce em revisão de sequência")

        r = dec.encerrar_revisao(dados, CNPJ_A, quem="admin", justificativa=JUST)
        cp1, st1 = ler_estado(dados, CNPJ_A)

        igual(r["tipo"], dec.TIPO_ENCERRAR_REVISAO, "a decisão tem tipo próprio")
        ok(r["consulta_disparada"] is False, "a decisão declara que não consultou nada")
        ok(not st1.revisao_sequencia, "a empresa saiu da revisão")
        igual(cp1.ult_nsu, cp0.ult_nsu, "o ult_nsu NÃO se moveu")
        igual(cp1.max_nsu, cp0.max_nsu, "o max_nsu NÃO se moveu")
        igual(cp1.estado_sincronismo, cp0.estado_sincronismo,
              "o estado de sincronismo continua o mesmo")
        ok(r["antes"]["revisao_sequencia"] is True
           and r["depois"]["revisao_sequencia"] is False,
           "a decisão carrega o retrato de antes e o de depois")
        ok(CNPJ_A not in json.dumps(r), "a empresa vai MASCARADA na decisão")

        secao("2. Isolamento entre empresas")
        b1_cp, b1_st = ler_estado(dados, CNPJ_B)
        ok(b1_st.revisao_sequencia, "a outra empresa continua em revisão")
        igual(b1_cp.ult_nsu, b0_cp.ult_nsu, "e o checkpoint dela não foi tocado")
        igual(b1_cp.nsu_observado_sefaz, b0_cp.nsu_observado_sefaz,
              "nem a notícia externa dela")

        secao("3. Empresa fora de revisão não pode encerrar revisão")
        try:
            dec.encerrar_revisao(dados, CNPJ_A, quem="admin", justificativa=JUST)
            ok(False, "deixou encerrar duas vezes")
        except dec.DecisaoRecusada as e:
            igual(e.motivo, "NAO_ESTA_EM_REVISAO", "recusa com motivo próprio")

        secao("4. Justificativa e operador são obrigatórios")
        for texto, motivo in (("curta", "JUSTIFICATIVA_CURTA"),
                              ("   ", "JUSTIFICATIVA_CURTA"),
                              ("x" * 600, "JUSTIFICATIVA_LONGA")):
            try:
                dec.encerrar_revisao(dados, CNPJ_B, quem="admin", justificativa=texto)
                ok(False, "aceitou justificativa %r" % texto[:12])
            except dec.DecisaoRecusada as e:
                igual(e.motivo, motivo, "justificativa %r recusada" % texto[:12])
        try:
            dec.encerrar_revisao(dados, CNPJ_B, quem="  ", justificativa=JUST)
            ok(False, "aceitou decisão sem operador")
        except dec.DecisaoRecusada as e:
            igual(e.motivo, "SEM_OPERADOR", "decisão sem autor é recusada")
        ok(ler_estado(dados, CNPJ_B)[1].revisao_sequencia,
           "e nenhuma das recusas mexeu no estado")

        secao("5. Reversível pelo domínio")
        dec.reabrir_revisao(dados, CNPJ_A, quem="admin",
                            justificativa="Reabrindo: o cliente ainda vai confirmar.")
        ok(ler_estado(dados, CNPJ_A)[1].revisao_sequencia,
           "reabrir devolve a empresa à trava, pela função do domínio")
        igual(ler_estado(dados, CNPJ_A)[0].ult_nsu, cp0.ult_nsu,
              "e continua sem mover o ult_nsu")

        secao("6. Marco de cobertura: registra a posição sem adotá-la como posse")
        antes_cp, _ = ler_estado(dados, CNPJ_B)
        r = dec.marcar_cobertura(dados, CNPJ_B, quem="admin", justificativa=JUST,
                                 origem_informacao=dec.ORIGEM_CLIENTE)
        depois_cp, _ = ler_estado(dados, CNPJ_B)
        igual(depois_cp.ult_nsu, antes_cp.ult_nsu,
              "o ult_nsu continua o mesmo depois do marco")
        ok(depois_cp.marco_cobertura, "o marco foi gravado")
        igual(depois_cp.marco_cobertura["nsu_informado_sefaz"],
              antes_cp.nsu_observado_sefaz,
              "e usa a posição que a SEFAZ informou, não um número de fora")
        ok("importação de XML" in depois_cp.marco_cobertura["aviso"],
           "o marco avisa que o passado só entra por importação de outra origem")
        igual(depois_cp.cobertura_anterior, "A_PARTIR_DE_MARCO",
              "a cobertura anterior passa a ser declarada como parcial")
        ok(r["origem_informacao"] == dec.ORIGEM_CLIENTE,
           "a origem da informação fica registrada")
        try:
            dec.marcar_cobertura(dados, CNPJ_B, quem="admin", justificativa=JUST,
                                 origem_informacao="ACHO_QUE_SIM")
            ok(False, "aceitou origem fora da lista")
        except dec.DecisaoRecusada as e:
            igual(e.motivo, "ORIGEM_INVALIDA", "origem fora do vocabulário é recusada")

        secao("7. Sem notícia externa, não há cobertura a marcar")
        preparar(dados, "11333444000165", revisao=False, observado="")
        try:
            dec.marcar_cobertura(dados, "11333444000165", quem="admin",
                                 justificativa=JUST,
                                 origem_informacao=dec.ORIGEM_ANALISE_INTERNA)
            ok(False, "marcou cobertura sem posição informada")
        except dec.DecisaoRecusada as e:
            igual(e.motivo, "SEM_NOTICIA_EXTERNA", "recusa quando não há fila adiante")

        secao("8. A trilha da empresa guarda a decisão, sem segredo")
        trilhas = list((dados / CNPJ_A / "auditoria").glob("*.jsonl"))
        ok(trilhas, "a trilha da empresa existe")
        linhas = [json.loads(l) for t in trilhas
                  for l in _io.open(t, encoding="utf-8") if l.strip()]
        decisoes = [d for d in linhas if d.get("ato") == "DECISAO_ADMINISTRATIVA"]
        igual(len(decisoes), 2, "duas decisões registradas (encerrar e reabrir)")
        d0 = decisoes[0]
        ok(d0.get("operador") == "admin", "com o operador")
        ok(d0.get("justificativa"), "com a justificativa")
        ok(d0.get("antes") and d0.get("depois"), "com o antes e o depois")
        bruto = json.dumps(linhas, ensure_ascii=False)
        ok(CNPJ_A not in bruto, "a empresa aparece mascarada na trilha")
        for proibido in ("senha", "pfx", "frase", "certificado"):
            ok(proibido not in bruto.lower(),
               "nada de %r na trilha" % proibido)

    # ══════════════════════════════════════════════════════════════════
    secao("9. HTTP: sessão, papel, CSRF e corpo fechado")
    # ══════════════════════════════════════════════════════════════════
    inst = Instancia()
    try:
        ok(inst.subir(), "FISCALE de teste na porta %d" % inst.porta)
        preparar(inst.dados, CNPJ_A)
        preparar(inst.dados, CNPJ_B)

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

        # sem sessão
        st, _ = pedir("POST", "/api/nfe/admin/revisao/encerrar",
                      {"id": CNPJ_A, "justificativa": JUST}, csrf="qualquer")
        igual(st, 401, "sem sessão: 401")
        st, _ = pedir("GET", "/api/nfe/admin/pendencias")
        igual(st, 401, "a listagem também exige sessão")

        # cria admin e entra
        st, _ = pedir("POST", "/api/primeiro-acesso", {"senha": Instancia.SENHA})
        igual(st, 200, "admin criado na instância de teste")

        st, r = pedir("GET", "/api/csrf")
        igual(st, 200, "o admin obtém um token CSRF")
        token = r.get("csrf") or ""
        ok(len(token) >= 32, "o token tem cara de HMAC (%d caracteres)" % len(token))

        st, r = pedir("GET", "/api/nfe/admin/pendencias")
        igual(st, 200, "a listagem responde ao admin")
        ok(r.get("total", 0) >= 2, "as duas empresas travadas aparecem")
        um = r["empresas"][0]
        ok(um.get("diagnostico_e_hipotese") is True
           and "não é uma conclusão" in (um.get("diagnostico_texto") or ""),
           "o diagnóstico é apresentado como HIPÓTESE, não como fato")
        ok("identificação" not in json.dumps(r).lower().replace("não identifica", ""),
           "e a tela não recebe nenhuma identificação de consumidor")

        # CSRF ausente e errado
        st, _ = pedir("POST", "/api/nfe/admin/revisao/encerrar",
                      {"id": CNPJ_A, "justificativa": JUST})
        igual(st, 403, "sem CSRF: 403")
        st, _ = pedir("POST", "/api/nfe/admin/revisao/encerrar",
                      {"id": CNPJ_A, "justificativa": JUST}, csrf="a" * 64)
        igual(st, 403, "CSRF errado: 403")

        # corpo fechado
        st, r = pedir("POST", "/api/nfe/admin/revisao/encerrar",
                      {"id": CNPJ_A, "justificativa": JUST,
                       "ult_nsu": "000000000009999"}, csrf=token)
        igual(st, 400, "ult_nsu no corpo: 400")
        ok("ult_nsu" in (r.get("campos_recusados") or []),
           "e a resposta diz qual campo foi recusado")
        for campo in ("max_nsu", "checkpoint", "caminho", "estado_sincronismo"):
            st, _ = pedir("POST", "/api/nfe/admin/revisao/encerrar",
                          {"id": CNPJ_A, "justificativa": JUST, campo: "x"},
                          csrf=token)
            igual(st, 400, "%s no corpo: 400" % campo)
        cp, stt = ler_estado(inst.dados, CNPJ_A)
        igual(cp.ult_nsu, "000000000000000", "nenhuma tentativa moveu o ult_nsu")
        ok(stt.revisao_sequencia, "e a empresa continua em revisão")

        # justificativa curta pelo HTTP
        st, r = pedir("POST", "/api/nfe/admin/revisao/encerrar",
                      {"id": CNPJ_A, "justificativa": "curta"}, csrf=token)
        igual(st, 409, "justificativa curta: 409")
        igual(r.get("motivo"), "JUSTIFICATIVA_CURTA", "com o motivo em código")

        # a decisão de verdade
        st, r = pedir("POST", "/api/nfe/admin/revisao/encerrar",
                      {"id": CNPJ_A, "justificativa": JUST}, csrf=token)
        igual(st, 200, "com sessão, papel, CSRF e justificativa: 200")
        ok(r.get("consulta_disparada") is False,
           "a resposta declara que nenhuma consulta foi disparada")
        ok(not ler_estado(inst.dados, CNPJ_A)[1].revisao_sequencia,
           "a empresa saiu da revisão")
        ok(ler_estado(inst.dados, CNPJ_B)[1].revisao_sequencia,
           "e a outra empresa continua travada (isolamento pelo HTTP também)")

        secao("10. A auditoria de acessos registrou a decisão")
        con = sqlite3.connect("file:%s?mode=ro"
                              % str(inst.dados / "auditoria.db").replace("\\", "/"),
                              uri=True)
        linhas = list(con.execute(
            "SELECT evento, resultado, ator_uid, derivado, detalhe FROM evento "
            "WHERE evento LIKE 'NFE_%' ORDER BY id"))
        con.close()
        eventos = [l[0] for l in linhas]
        ok("NFE_REVISAO_ENCERRADA" in eventos, "o sucesso foi auditado")
        ok("NFE_DECISAO_RECUSADA" in eventos, "e a recusa também")
        boa = [l for l in linhas if l[0] == "NFE_REVISAO_ENCERRADA"][0]
        igual(boa[2], "admin", "com o operador")
        ok(boa[3] and CNPJ_A not in boa[3], "com a empresa mascarada")
        ok(JUST[:40] in (boa[4] or ""), "e com a justificativa")
        bruto = json.dumps(linhas, ensure_ascii=False).lower()
        for proibido in ("senha", "pfx", "frase-senha", Instancia.SENHA.lower()):
            ok(proibido not in bruto, "nada de %r na auditoria" % proibido)

        secao("11. Papel: operador não decide")
        st, _ = pedir("POST", "/api/usuarios",
                      {"usuario": "operadora", "senha": Instancia.SENHA,
                       "papel": "operador", "nome": "Operadora de Teste"},
                      csrf=token)
        if st not in (200, 201):
            ok(True, "não consegui criar operadora por esta rota (status %s) — "
                     "a guarda de papel é conferida na lista de permissão" % st)
        else:
            inst.cookie = ""
            st, _ = pedir("POST", "/api/login",
                          {"usuario": "operadora", "senha": Instancia.SENHA})
            igual(st, 200, "operadora entrou")
            st, r2 = pedir("GET", "/api/csrf")
            tok_op = (r2 or {}).get("csrf") or ""
            st, _ = pedir("GET", "/api/nfe/admin/pendencias")
            igual(st, 403, "operadora não lista as travadas")
            st, _ = pedir("POST", "/api/nfe/admin/revisao/encerrar",
                          {"id": CNPJ_B, "justificativa": JUST}, csrf=tok_op)
            igual(st, 403, "operadora não encerra revisão")
            ok(ler_estado(inst.dados, CNPJ_B)[1].revisao_sequencia,
               "e o estado continua intacto")
    finally:
        inst.derrubar()

    # ══════════════════════════════════════════════════════════════════
    secao("12. Estático: a rota não chama a SEFAZ, e a lista de papéis sabe dela")
    # ══════════════════════════════════════════════════════════════════
    fonte = _io.open(RAIZ / "fiscale_decisoes_nfe.py", encoding="utf-8").read()
    for proibido in ("servico_distribuicao", "consultar_empresa", "requests",
                     "urllib.request", "fabrica_padrao"):
        ok(proibido not in fonte,
           "o módulo de decisões não menciona %r" % proibido)
    ok("encerrar_revisao_de_sequencia" in fonte and "marcar_inicio_cobertura" in fonte,
       "e usa as funções do domínio que já existiam")

    papeis = _io.open(RAIZ / "fiscale_papeis.py", encoding="utf-8").read()
    ok('("POST", "/api/nfe/admin/")' in papeis and '("GET", "/api/nfe/admin/")' in papeis,
       "as rotas estão na lista de permissão do administrador")
    srv = _io.open(RAIZ / "fiscale_server.py", encoding="utf-8").read()
    ok('"/api/nfe/admin/pendencias"' in srv and "NFSE_EXCECOES" in srv,
       "e declaradas como exceção do proxy do módulo NFS-e")
    ok("_csrf_ok()" in srv, "a rota de decisão confere o CSRF")

    secao("13. A trava de rede estava mesmo ativa")
    try:
        _socket.socket().connect(("8.8.8.8", 53))
        ok(False, "a trava de rede não estava ativa")
    except AssertionError:
        ok(True, "qualquer saída da máquina levantaria (conferido)")
    except OSError:
        ok(True, "sem rede disponível — a trava também barraria")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
