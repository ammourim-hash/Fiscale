#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fiscale — sincronização da projeção de clientes para o Elo.

O SENTIDO DA CONEXÃO
    Sempre FISCALE → ELO. O Elo nunca abre conexão para cá. Isso atravessa
    NAT e firewall sem abrir porta nenhuma no escritório, e mantém o
    princípio da arquitetura: a máquina que guarda certificado não aceita
    conexão de fora.

O QUE ATRAVESSA
    Cinco campos por cliente: id, nome, CNPJ, e-mail e telefone. Mais o
    ativo/inativo. É o mínimo para o Elo reconhecer quem escreveu e
    responder.

O QUE NÃO ATRAVESSA — e a lista é a parte importante deste arquivo
    Certificado, nome do arquivo .pfx, validade do certificado, senha,
    DPAPI, regime tributário, IE, IM, município, UF, XML, DAS, apuração.
    `projetar()` monta o payload campo a campo, em vez de copiar o
    dicionário e remover o que não pode ir: assim, campo novo no cadastro
    do Fiscale NÃO vaza sozinho na próxima versão.

POR QUE O PAYLOAD É MONTADO NA HORA DO ENVIO
    A fila guarda "o cliente X mudou", não o conteúdo dele. Se o Elo ficar
    fora do ar por duas horas e o cadastro mudar mais três vezes nesse
    intervalo, o que sobe é o estado ATUAL — não uma sequência de fotos
    velhas.

O FISCALE NUNCA PARA POR CAUSA DISTO
    Enfileirar é gravação local em SQLite, rápida e sem rede. O envio
    acontece numa thread separada. Elo fora do ar vira item pendente com
    nova tentativa marcada; o cadastro já foi salvo e a rotina fiscal
    segue como se o Elo não existisse.
"""
import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    TEM_ED25519 = True
except Exception:
    TEM_ED25519 = False

CAMINHO_SYNC = "/api/integrations/fiscale/customers/sync"
ARQUIVO_CHAVE = "elo_integracao_ed25519.json"
FONTE = "FISCALE"

# Espera entre tentativas, por número de falhas. Depois da última, repete
# a de uma hora. Nada de tentar de segundo em segundo: o Elo pode estar
# fora por manutenção, e martelar não adianta.
BACKOFF_S = [30, 120, 300, 900, 3600]
MAX_TENTATIVAS = 20

# Teto por requisição. O Elo recusa lote maior.
LOTE_MAXIMO = 500

TIMEOUT_S = 30


class SyncIndisponivel(Exception):
    """Não dá para sincronizar agora. Não é erro do Fiscale."""


# ─── chave da integração ────────────────────────────────────────────────
# Par PRÓPRIO, separado do par que assina o token de login. Mesma máquina
# e mesmo dono, mas propósitos diferentes: uma assina "esta pessoa entrou",
# a outra assina "este cadastro mudou". Separadas, cada uma rotaciona no
# seu tempo e comprometer uma não entrega a outra.
def carregar_ou_criar_chave(pasta_dados):
    """Devolve (kid, privada, publica_b64)."""
    if not TEM_ED25519:
        raise SyncIndisponivel("a biblioteca cryptography não está disponível")

    caminho = os.path.join(pasta_dados, ARQUIVO_CHAVE)

    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8-sig") as f:
                d = json.load(f)
            privada = serialization.load_pem_private_key(
                d["privada_pem"].encode("utf-8"), password=None)
            return d["kid"], privada, d["publica_b64"]
        except Exception as e:
            raise SyncIndisponivel(
                f"a chave de integração não pôde ser lida ({e.__class__.__name__})")

    privada = Ed25519PrivateKey.generate()
    kid = "sync-" + uuid.uuid4().hex[:8]
    privada_pem = privada.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    publica_b64 = base64.b64encode(privada.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )).decode("ascii")

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"kid": kid, "privada_pem": privada_pem,
                   "publica_b64": publica_b64}, f, indent=2)
    try:
        os.chmod(caminho, 0o600)
    except Exception:
        pass

    print("=" * 66)
    print("  Chave de SINCRONIZAÇÃO Fiscale -> Elo criada.")
    print("  Acrescente ao .env do Elo (o tenant é o mesmo do elo_config.json):")
    print(f'  ELO_INTEGRATION_PUBLIC_KEYS={{"{kid}":{{"tenantId":"<TENANT>",'
          f'"source":"FISCALE","publicKey":"{publica_b64}"}}}}')
    print("=" * 66)

    return kid, privada, publica_b64


# ─── projeção ───────────────────────────────────────────────────────────
def projetar(cliente):
    """Cliente do `state_clientes.json` -> payload permitido.

    Lista branca explícita. Campo novo no cadastro não entra sozinho.
    """
    return {
        "externalId": str(cliente.get("id")),
        "displayName": (cliente.get("nome") or "").strip(),
        "document": (cliente.get("cnpj") or "").strip() or None,
        "email": (cliente.get("email") or "").strip() or None,
        "phone": (cliente.get("tel") or "").strip() or None,
        # O cadastro não tem "inativo" hoje; todo cliente presente está
        # ativo. Quando o Fiscale ganhar o campo, é aqui que ele entra.
        "active": bool(cliente.get("ativo", True)),
    }


def versao(projecao):
    """Hash estável dos campos permitidos.

    Serve para o Fiscale decidir o que enfileirar. Quem decide se a
    projeção mudou, do lado do Elo, é o hash que o Elo calcula sozinho —
    assim os dois não precisam produzir o mesmo digest, e um erro aqui
    causa, no pior caso, um envio a mais.
    """
    partes = [
        projecao["externalId"],
        projecao["displayName"],
        projecao["document"] or "",
        projecao["email"] or "",
        projecao["phone"] or "",
        "1" if projecao["active"] else "0",
    ]
    # Junta com quebra de linha, e não com espaço: os campos podem conter
    # espaço, e aí "A B" + "" e "A" + "B" dariam o mesmo hash.
    return hashlib.sha256("\n".join(partes).encode("utf-8")).hexdigest()


def ler_clientes(pasta_dados):
    caminho = os.path.join(pasta_dados, "state_clientes.json")
    if not os.path.exists(caminho):
        return []
    try:
        with open(caminho, encoding="utf-8-sig") as f:
            d = json.load(f)
        return d.get("clientes") or []
    except Exception:
        return []


# ─── fila local ─────────────────────────────────────────────────────────
class Fila:
    """Fila de sincronização em SQLite.

    Nada de Kafka nem RabbitMQ: é uma máquina só, algumas dezenas de
    clientes, e o que precisamos é não perder uma alteração porque a
    internet caiu no meio do salvamento. Um arquivo resolve.
    """

    def __init__(self, caminho):
        self._trava = threading.Lock()
        self._con = sqlite3.connect(caminho, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        with self._trava:
            self._con.execute("""
                CREATE TABLE IF NOT EXISTS elo_sync_queue (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo          TEXT    NOT NULL,      -- FULL | UPSERT
                    external_id   TEXT,                  -- NULL quando FULL
                    versao        TEXT,
                    criado_em     INTEGER NOT NULL,
                    tentativas    INTEGER NOT NULL DEFAULT 0,
                    proxima_em    INTEGER NOT NULL DEFAULT 0,
                    ultimo_erro   TEXT,
                    concluido_em  INTEGER
                )
            """)
            self._con.execute("""
                CREATE INDEX IF NOT EXISTS ix_fila_pendente
                    ON elo_sync_queue (concluido_em, proxima_em)
            """)
            # Um pendente por cliente. Cinco edições seguidas viram um
            # envio, com o estado final — não cinco envios.
            self._con.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ix_fila_um_por_cliente
                    ON elo_sync_queue (tipo, external_id)
                 WHERE concluido_em IS NULL
            """)
            self._con.commit()

    def enfileirar(self, tipo, external_id=None, versao_reg=None):
        agora = int(time.time())
        with self._trava:
            existente = self._con.execute(
                "SELECT id FROM elo_sync_queue"
                " WHERE tipo=? AND external_id IS ? AND concluido_em IS NULL",
                (tipo, external_id)).fetchone()
            if existente:
                # Já há pendente para este cliente: atualiza a versão e
                # devolve a tentativa para agora — a alteração é nova.
                self._con.execute(
                    "UPDATE elo_sync_queue SET versao=?, proxima_em=0, ultimo_erro=NULL"
                    " WHERE id=?", (versao_reg, existente[0]))
            else:
                self._con.execute(
                    "INSERT INTO elo_sync_queue (tipo, external_id, versao, criado_em)"
                    " VALUES (?,?,?,?)", (tipo, external_id, versao_reg, agora))
            self._con.commit()

    def pendentes(self, limite=LOTE_MAXIMO, prontos_apenas=True):
        agora = int(time.time())
        with self._trava:
            if prontos_apenas:
                return self._con.execute(
                    "SELECT id, tipo, external_id, versao, tentativas FROM elo_sync_queue"
                    " WHERE concluido_em IS NULL AND proxima_em <= ? AND tentativas < ?"
                    " ORDER BY id LIMIT ?", (agora, MAX_TENTATIVAS, limite)).fetchall()
            return self._con.execute(
                "SELECT id, tipo, external_id, versao, tentativas FROM elo_sync_queue"
                " WHERE concluido_em IS NULL ORDER BY id LIMIT ?", (limite,)).fetchall()

    def concluir(self, ids):
        if not ids:
            return
        agora = int(time.time())
        with self._trava:
            self._con.executemany(
                "UPDATE elo_sync_queue SET concluido_em=? WHERE id=?",
                [(agora, i) for i in ids])
            self._con.commit()

    def adiar(self, ids, erro):
        """Marca falha e agenda a próxima tentativa com espera crescente."""
        if not ids:
            return
        agora = int(time.time())
        with self._trava:
            for i in ids:
                linha = self._con.execute(
                    "SELECT tentativas FROM elo_sync_queue WHERE id=?", (i,)).fetchone()
                n = (linha[0] if linha else 0) + 1
                espera = BACKOFF_S[min(n - 1, len(BACKOFF_S) - 1)]
                self._con.execute(
                    "UPDATE elo_sync_queue SET tentativas=?, proxima_em=?, ultimo_erro=?"
                    " WHERE id=?", (n, agora + espera, (erro or "")[:500], i))
            self._con.commit()

    def liberar_todos(self):
        """Retry manual: zera a espera de tudo que está pendente."""
        with self._trava:
            c = self._con.execute(
                "UPDATE elo_sync_queue SET proxima_em=0, tentativas=0"
                " WHERE concluido_em IS NULL")
            self._con.commit()
            return c.rowcount

    def estado(self):
        agora = int(time.time())
        with self._trava:
            pend = self._con.execute(
                "SELECT count(*) FROM elo_sync_queue WHERE concluido_em IS NULL"
            ).fetchone()[0]
            prontos = self._con.execute(
                "SELECT count(*) FROM elo_sync_queue"
                " WHERE concluido_em IS NULL AND proxima_em <= ? AND tentativas < ?",
                (agora, MAX_TENTATIVAS)).fetchone()[0]
            desistidos = self._con.execute(
                "SELECT count(*) FROM elo_sync_queue"
                " WHERE concluido_em IS NULL AND tentativas >= ?",
                (MAX_TENTATIVAS,)).fetchone()[0]
            erro = self._con.execute(
                "SELECT ultimo_erro FROM elo_sync_queue"
                " WHERE concluido_em IS NULL AND ultimo_erro IS NOT NULL"
                " ORDER BY id DESC LIMIT 1").fetchone()
            concluidos = self._con.execute(
                "SELECT count(*) FROM elo_sync_queue WHERE concluido_em IS NOT NULL"
            ).fetchone()[0]
        return {"pendentes": pend, "prontos": prontos, "desistidos": desistidos,
                "concluidos": concluidos, "ultimo_erro": erro[0] if erro else None}


_filas = {}
_trava_filas = threading.Lock()


def fila(pasta_dados):
    with _trava_filas:
        f = _filas.get(pasta_dados)
        if f is None:
            f = Fila(os.path.join(pasta_dados, "elo_sync.db"))
            _filas[pasta_dados] = f
        return f


# ─── gatilhos ───────────────────────────────────────────────────────────
def enfileirar_full(pasta_dados):
    fila(pasta_dados).enfileirar("FULL")


def enfileirar_diferencas(pasta_dados, antes, depois):
    """Enfileira só quem realmente mudou entre dois estados do cadastro.

    Varrer os 14 clientes a cada salvamento funcionaria, mas com 300 seria
    tráfego e escrita à toa. Comparar o hash dos campos permitidos é
    barato e enfileira exatamente o que mexeu — inclusive nada, quando a
    alteração foi num campo que não sincroniza (regime, IE, certificado).
    """
    def por_id(lista):
        return {str(c.get("id")): c for c in (lista or []) if c.get("id") is not None}

    a, d = por_id(antes), por_id(depois)
    f = fila(pasta_dados)
    mexidos = []

    for cid, cliente in d.items():
        nova = versao(projetar(cliente))
        antiga = versao(projetar(a[cid])) if cid in a else None
        if nova != antiga:
            f.enfileirar("UPSERT", cid, nova)
            mexidos.append(cid)

    # Cliente que sumiu do cadastro: um FULL resolve, porque é o full sync
    # que sabe marcar ausência do lado do Elo.
    if set(a) - set(d):
        f.enfileirar("FULL")
        mexidos.append("*")

    return mexidos


# ─── envio ──────────────────────────────────────────────────────────────
def _assinar(privada, kid, metodo, caminho, corpo_bytes):
    ts = str(int(time.time()))
    nonce = str(uuid.uuid4())
    hash_corpo = hashlib.sha256(corpo_bytes).hexdigest()
    texto = "\n".join([metodo.upper(), caminho, ts, nonce, hash_corpo])
    assinatura = privada.sign(texto.encode("utf-8"))
    return {
        "x-elo-key-id": kid,
        "x-elo-timestamp": ts,
        "x-elo-nonce": nonce,
        "x-elo-signature": base64.urlsafe_b64encode(assinatura).rstrip(b"=").decode("ascii"),
    }


def enviar(pasta_dados, config, clientes, mode):
    """Envia um lote. Devolve o resumo do Elo; levanta em caso de falha."""
    if not config.get("url"):
        raise SyncIndisponivel("a URL do Elo não está configurada")

    kid, privada, _ = carregar_ou_criar_chave(pasta_dados)

    corpo = json.dumps({
        "source": FONTE,
        "mode": mode,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "customers": clientes,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    url = config["url"].rstrip("/") + CAMINHO_SYNC
    cabecalhos = _assinar(privada, kid, "POST", CAMINHO_SYNC, corpo)
    cabecalhos["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=corpo, headers=cabecalhos, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "ignore")[:300]
        raise SyncIndisponivel(f"HTTP {e.code}: {detalhe}")
    except Exception as e:
        raise SyncIndisponivel(f"{e.__class__.__name__}: {e}")


def processar(pasta_dados, config):
    """Drena o que está pronto na fila. Devolve um resumo do que aconteceu.

    Nunca levanta: é chamada de thread de fundo e de rota HTTP, e falhar
    aqui não pode derrubar nem uma nem outra.
    """
    f = fila(pasta_dados)
    itens = f.pendentes()
    if not itens:
        return {"enviados": 0, "pendentes": f.estado()["pendentes"]}

    tem_full = any(i[1] == "FULL" for i in itens)
    ids = [i[0] for i in itens]
    todos = ler_clientes(pasta_dados)

    if tem_full:
        # Um FULL cobre qualquer UPSERT pendente: ele leva o cadastro
        # inteiro e ainda marca ausências. Concluímos os dois juntos.
        payload = [projetar(c) for c in todos]
        mode = "FULL"
    else:
        alvos = {i[2] for i in itens if i[2]}
        payload = [projetar(c) for c in todos if str(c.get("id")) in alvos]
        mode = "INCREMENTAL"
        if not payload:
            # Cliente enfileirado que não existe mais: um FULL resolve.
            f.concluir(ids)
            f.enfileirar("FULL")
            return {"enviados": 0, "observacao": "clientes sumiram; FULL enfileirado"}

    for p in payload:
        p["sourceVersion"] = versao(p)

    try:
        resumo = enviar(pasta_dados, config, payload[:LOTE_MAXIMO], mode)
    except SyncIndisponivel as e:
        f.adiar(ids, str(e))
        return {"enviados": 0, "erro": str(e), "pendentes": f.estado()["pendentes"]}
    except Exception as e:                       # rede, DNS, o que for
        f.adiar(ids, f"{e.__class__.__name__}: {e}")
        return {"enviados": 0, "erro": str(e), "pendentes": f.estado()["pendentes"]}

    f.concluir(ids)
    return {"enviados": len(payload), "mode": mode, "elo": resumo,
            "pendentes": f.estado()["pendentes"]}


# ─── thread de fundo ────────────────────────────────────────────────────
_worker = None
_parar = threading.Event()


def iniciar_worker(pasta_dados, obter_config, intervalo_s=60):
    """Sobe a thread que drena a fila.

    Daemon: não segura o encerramento do Fiscale. Silenciosa: qualquer
    erro aqui vira espera até a próxima volta, nunca ruído na tela de quem
    está trabalhando.
    """
    global _worker
    if _worker is not None:
        return _worker

    def laco():
        while not _parar.wait(5):
            try:
                cfg = obter_config()
                if cfg.get("url") and cfg.get("tenant_id"):
                    processar(pasta_dados, cfg)
            except Exception:
                pass
            if _parar.wait(intervalo_s):
                break

    _worker = threading.Thread(target=laco, daemon=True, name="elo-sync")
    _worker.start()
    return _worker


def parar_worker():
    _parar.set()
