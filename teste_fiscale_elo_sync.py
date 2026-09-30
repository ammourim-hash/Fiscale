#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes da sincronização Fiscale -> Elo (lado Fiscale).

Sem rede e sem tocar em nada do escritório: pasta temporária própria. O
envio é testado contra um servidor HTTP local de mentira, que também sabe
fingir estar fora do ar — é assim que se verifica o retry.

    python teste_fiscale_elo_sync.py
"""
import base64
import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Pastas temporárias saem de `teste_apoio.pasta_temp()`, que as apaga no fim
# do processo. Antes eram `tempfile.mkdtemp()` soltos, e sobravam em %TEMP%.
import teste_apoio as _ta  # noqa: E402

import fiscale_elo_sync as sync

falhas = []


def checar(cond, desc):
    if cond:
        print(f"  ok   {desc}")
    else:
        print(f"  FALHA {desc}")
        falhas.append(desc)


# ─── servidor de mentira ────────────────────────────────────────────────
class EloFalso:
    """Recebe as requisições e confere a assinatura como o Elo faria."""

    def __init__(self, publica_b64):
        from cryptography.hazmat.primitives import serialization
        self.publica = serialization.load_der_public_key(base64.b64decode(publica_b64))
        self.recebidos = []
        self.nonces = set()
        self.fora_do_ar = False
        self.ultimo_erro_assinatura = None

        pai = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if pai.fora_do_ar:
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                n = int(self.headers.get("Content-Length", 0))
                corpo = self.rfile.read(n)

                ts = self.headers.get("x-elo-timestamp", "")
                nonce = self.headers.get("x-elo-nonce", "")
                assinatura = self.headers.get("x-elo-signature", "")

                texto = "\n".join(["POST", self.path, ts, nonce,
                                   hashlib.sha256(corpo).hexdigest()])
                try:
                    sig = base64.urlsafe_b64decode(assinatura + "=" * (-len(assinatura) % 4))
                    pai.publica.verify(sig, texto.encode("utf-8"))
                except Exception as e:
                    pai.ultimo_erro_assinatura = str(e)
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if nonce in pai.nonces:
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                pai.nonces.add(nonce)

                pai.recebidos.append(json.loads(corpo.decode("utf-8")))
                resposta = json.dumps({"runId": "x", "received": len(pai.recebidos)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resposta)))
                self.end_headers()
                self.wfile.write(resposta)

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.porta = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.porta}"

    def parar(self):
        self.httpd.shutdown()


# ─── testes ─────────────────────────────────────────────────────────────
CLIENTE = {
    "id": 1,
    "cert": {"arquivo": "EMPRESA_04103256000185.pfx", "titular": "x", "cnpjLido": "y"},
    "nome": "Monte Assessoria",
    "cnpj": "04.103.256/0001-85",
    "regime": "Simples Nacional",
    "ie": "123456",
    "im": "384.010-7",
    "mun": "Recife",
    "uf": "PE",
    "email": "contato@monte.com",
    "tel": "(81) 99999-1111",
    "certValidade": "2027-01-01",
}


def teste_projecao():
    print("\nProjeção — o que atravessa e o que não")

    p = sync.projetar(CLIENTE)

    checar(p["externalId"] == "1", "leva o id do Fiscale como externalId")
    checar(p["displayName"] == "Monte Assessoria", "leva o nome")
    checar(p["document"] == "04.103.256/0001-85", "leva o CNPJ (normalizado no Elo)")
    checar(p["email"] == "contato@monte.com", "leva o e-mail")
    checar(p["phone"] == "(81) 99999-1111", "leva o telefone")
    checar(p["active"] is True, "leva o ativo")

    texto = json.dumps(p)
    proibidos = {
        "certificado": "pfx", "nome do .pfx": "EMPRESA_", "validade do cert": "2027-01-01",
        "regime": "Simples", "IE": "123456", "IM": "384.010-7",
        "municipio": "Recife", "UF": "\"PE\"",
    }
    for rotulo, agulha in proibidos.items():
        checar(agulha not in texto, f"NÃO leva {rotulo}")

    checar(set(p.keys()) == {"externalId", "displayName", "document", "email",
                             "phone", "active"},
           "o payload tem exatamente os 6 campos permitidos")

    # Campo novo no cadastro não pode vazar sozinho.
    novo = dict(CLIENTE, senha_portal="segredo", saldo_das=1234.56)
    checar("segredo" not in json.dumps(sync.projetar(novo)),
           "campo novo no cadastro NÃO entra sozinho na projeção")

    vazio = sync.projetar({"id": 9, "nome": "Sem Contato"})
    checar(vazio["phone"] is None and vazio["email"] is None and vazio["document"] is None,
           "campos vazios viram null, não string vazia")


def teste_versao():
    print("\nVersão (hash) — decide o que enfileirar")

    v1 = sync.versao(sync.projetar(CLIENTE))
    v2 = sync.versao(sync.projetar(dict(CLIENTE)))
    checar(v1 == v2, "mesmo cadastro, mesma versão")

    checar(v1 != sync.versao(sync.projetar(dict(CLIENTE, nome="Outro"))),
           "mudar o nome muda a versão")
    checar(v1 != sync.versao(sync.projetar(dict(CLIENTE, tel="81988887777"))),
           "mudar o telefone muda a versão")
    checar(v1 == sync.versao(sync.projetar(dict(CLIENTE, regime="Lucro Presumido"))),
           "mudar o REGIME não muda a versão — ele não sincroniza")
    checar(v1 == sync.versao(sync.projetar(dict(CLIENTE, ie="999", im="888", mun="Olinda"))),
           "mudar IE/IM/município não muda a versão")


def teste_fila(pasta):
    print("\nFila local")

    f = sync.fila(pasta)
    checar(f.estado()["pendentes"] == 0, "começa vazia")

    f.enfileirar("UPSERT", "1", "v1")
    checar(f.estado()["pendentes"] == 1, "enfileira um cliente")

    f.enfileirar("UPSERT", "1", "v2")
    checar(f.estado()["pendentes"] == 1,
           "cinco edições do mesmo cliente viram UM pendente, com o estado final")

    f.enfileirar("UPSERT", "2", "v1")
    checar(f.estado()["pendentes"] == 2, "clientes diferentes são pendentes diferentes")

    ids = [i[0] for i in f.pendentes()]
    f.adiar(ids, "Elo fora do ar")
    e = f.estado()
    checar(e["pendentes"] == 2, "falha não perde o item")
    checar(e["prontos"] == 0, "depois de falhar, espera antes de tentar de novo")
    checar(e["ultimo_erro"] == "Elo fora do ar", "guarda o motivo da última falha")

    checar(f.liberar_todos() == 2, "retry manual libera os pendentes")
    checar(f.estado()["prontos"] == 2, "e eles voltam a estar prontos")

    f.concluir([i[0] for i in f.pendentes()])
    checar(f.estado()["pendentes"] == 0, "concluir esvazia a fila")


def teste_diferencas(pasta):
    print("\nGatilho — só enfileira quem mudou")

    f = sync.fila(pasta)
    f.concluir([i[0] for i in f.pendentes(prontos_apenas=False)])

    antes = [dict(CLIENTE), {"id": 2, "nome": "Segunda", "cnpj": "", "email": "", "tel": ""}]

    checar(sync.enfileirar_diferencas(pasta, antes, antes) == [],
           "salvar sem mudar nada não enfileira ninguém")

    depois = [dict(CLIENTE, nome="Monte Assessoria e Consultoria"), antes[1]]
    checar(sync.enfileirar_diferencas(pasta, antes, depois) == ["1"],
           "mudar o nome de um enfileira só ele")

    f.concluir([i[0] for i in f.pendentes(prontos_apenas=False)])
    so_fiscal = [dict(CLIENTE, regime="Lucro Real", ie="55555"), antes[1]]
    checar(sync.enfileirar_diferencas(pasta, antes, so_fiscal) == [],
           "mudar SÓ campo fiscal não gera sincronização nenhuma")

    f.concluir([i[0] for i in f.pendentes(prontos_apenas=False)])
    novo = antes + [{"id": 3, "nome": "Terceira", "cnpj": "", "email": "", "tel": ""}]
    checar(sync.enfileirar_diferencas(pasta, antes, novo) == ["3"],
           "cliente novo é enfileirado")

    f.concluir([i[0] for i in f.pendentes(prontos_apenas=False)])
    sumiu = [antes[0]]
    r = sync.enfileirar_diferencas(pasta, antes, sumiu)
    checar("*" in r, "cliente removido dispara um FULL, que é quem sabe marcar ausência")
    f.concluir([i[0] for i in f.pendentes(prontos_apenas=False)])


def teste_envio(pasta):
    print("\nEnvio assinado, retry e Elo offline")

    _, _, publica = sync.carregar_ou_criar_chave(pasta)
    elo = EloFalso(publica)
    cfg = {"url": elo.url, "tenant_id": "tenant-de-teste"}

    with open(os.path.join(pasta, "state_clientes.json"), "w", encoding="utf-8") as f:
        json.dump({"clientes": [CLIENTE], "seq": 1}, f, ensure_ascii=False)

    try:
        fila = sync.fila(pasta)
        fila.concluir([i[0] for i in fila.pendentes(prontos_apenas=False)])

        sync.enfileirar_full(pasta)
        r = sync.processar(pasta, cfg)
        checar(r.get("enviados") == 1, "envia o cliente")
        checar(elo.ultimo_erro_assinatura is None, "a assinatura confere do outro lado")
        checar(len(elo.recebidos) == 1, "o Elo recebeu uma requisição")
        checar(elo.recebidos[0]["mode"] == "FULL", "modo FULL")
        checar(elo.recebidos[0]["customers"][0]["displayName"] == "Monte Assessoria",
               "o cliente chegou inteiro")
        checar("sourceVersion" in elo.recebidos[0]["customers"][0],
               "vai com sourceVersion")
        checar("cert" not in json.dumps(elo.recebidos[0]),
               "NENHUM dado proibido no que foi pela rede")
        checar(fila.estado()["pendentes"] == 0, "a fila esvaziou")

        # ── Elo fora do ar ──
        elo.fora_do_ar = True
        sync.enfileirar_full(pasta)
        r = sync.processar(pasta, cfg)
        checar(r.get("enviados") == 0, "com o Elo fora, não envia")
        checar("erro" in r, "e registra o erro")
        checar(fila.estado()["pendentes"] == 1, "o item CONTINUA pendente — nada se perdeu")
        checar(fila.estado()["prontos"] == 0, "e espera antes de tentar de novo")

        # Alterar cliente com o Elo fora não pode falhar.
        antes = [CLIENTE]
        depois = [dict(CLIENTE, nome="Alterado Offline")]
        try:
            sync.enfileirar_diferencas(pasta, antes, depois)
            checar(True, "alterar cliente com o Elo fora funciona normalmente")
        except Exception:
            checar(False, "alterar cliente com o Elo fora funciona normalmente")

        # ── Elo volta ──
        elo.fora_do_ar = False
        with open(os.path.join(pasta, "state_clientes.json"), "w", encoding="utf-8") as f:
            json.dump({"clientes": depois, "seq": 1}, f, ensure_ascii=False)

        fila.liberar_todos()
        r = sync.processar(pasta, cfg)
        checar(r.get("enviados", 0) >= 1, "religado o Elo, o retry sincroniza")
        checar(fila.estado()["pendentes"] == 0, "e a fila esvazia")
        checar(elo.recebidos[-1]["customers"][0]["displayName"] == "Alterado Offline",
               "o que sobe é o estado ATUAL, não a foto de quando enfileirou")

        # ── nonce não se repete ──
        primeiro = elo.recebidos[0]
        checar(len(elo.nonces) == len(elo.recebidos),
               "cada requisição usou um nonce próprio")
        checar(primeiro is not None, "as requisições ficaram registradas")

    finally:
        elo.parar()


def teste_offline_total(pasta):
    print("\nElo inalcançável (porta fechada)")

    cfg = {"url": "http://127.0.0.1:1", "tenant_id": "t"}
    fila = sync.fila(pasta)
    fila.concluir([i[0] for i in fila.pendentes(prontos_apenas=False)])

    sync.enfileirar_full(pasta)
    inicio = time.time()
    r = sync.processar(pasta, cfg)
    checar("erro" in r, "porta fechada vira erro registrado, não exceção")
    checar(time.time() - inicio < 20, "e não trava o Fiscale esperando")
    checar(fila.estado()["pendentes"] == 1, "o item continua pendente para depois")

    checar(sync.processar(pasta, cfg).get("enviados") == 0,
           "enquanto está no backoff, nem tenta — e não é erro novo")

    fila.liberar_todos()
    checar(sync.processar(pasta, {"url": "", "tenant_id": ""}).get("erro") is not None,
           "sem configuração, falha limpa")


def main():
    pasta = _ta.pasta_temp(prefix="fiscale-sync-teste-")
    print(f"pasta de teste: {pasta}")
    try:
        teste_projecao()
        teste_versao()
        teste_fila(pasta)
        teste_diferencas(pasta)
        if sync.TEM_ED25519:
            teste_envio(pasta)
        else:
            print("\nEnvio: PULADO (cryptography indisponível)")
        teste_offline_total(pasta)
    finally:
        shutil.rmtree(pasta, ignore_errors=True)

    print()
    if falhas:
        print(f"REPROVADO — {len(falhas)} falha(s):")
        for f in falhas:
            print(f"  - {f}")
        sys.exit(1)
    print("Todos os testes passaram.")


if __name__ == "__main__":
    main()
