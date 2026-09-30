#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes de segurança e de máquina limpa — PORT 3.

    python teste_seguranca.py

Estes testes existem para FALHAR quando um segredo escapar. Eles montam uma
instalação com segredos plantados e conferem que nada disso sai — nem para o
navegador, nem para o backup, nem para o log, nem para o manifesto.

REGRA DO ARQUIVO: nenhum segredo é impresso. Quando um teste falha, ele diz
ONDE o segredo apareceu, nunca QUAL era.

A TELA FICTÍCIA
    O cofre é infraestrutura e não pertence a módulo funcional nenhum. Para
    provar isso, esta suíte não usa nenhuma tela real: ela inscreve uma tela
    que **só existe aqui** (`cofre_teste`) e exercita o cofre inteiro sobre
    ela. Antes o veículo era o `state_plano.json`, e a consequência era que
    remover o Plano de Saúde levaria junto a prova de DPAPI, de sanitização de
    backup e de resistência a arquivo corrompido — cobertura de segurança
    saindo de carona com uma tela de negócio.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

# Pastas temporárias saem de `teste_apoio.pasta_temp()`, que as apaga no fim
# do processo. Antes eram `tempfile.mkdtemp()` soltos, e sobravam em %TEMP%.
import teste_apoio as _ta  # noqa: E402

import fiscale_backup as bk        # noqa: E402
import fiscale_dados as fd         # noqa: E402
import fiscale_migracao as fm      # noqa: E402
import fiscale_segredos as fs      # noqa: E402
import seguranca                   # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

# Segredos plantados. Se qualquer um destes textos aparecer onde não deve, o
# teste falha — e reporta só o rótulo, nunca o valor.
SEGREDOS = {
    "senha de portal": "PORTAL-SEGREDO-a1b2c3",
    "senha de certificado": "CERT-SEGREDO-d4e5f6",
    "app secret": "APPSECRET-SEGREDO-g7h8i9",
    "chave privada do Elo": "ELOPRIV-SEGREDO-j0k1l2",
    "token do WhatsApp": "WHATSAPP-SEGREDO-m3n4o5",
    "token de sessão": "SESSAO-SEGREDO-p6q7r8",
}
FRASE = "frase-de-exportacao-do-teste-de-seguranca"

# ── A TELA FICTÍCIA ─────────────────────────────────────────────────────────
# `MOD` não existe no produto: é uma tela inventada só para esta suíte. O
# cofre a trata como trataria qualquer outra, porque ele não conhece nenhuma —
# quem tem credencial se INSCREVE (`fiscale_segredos.registrar`). É essa
# indiferença que o arquivo inteiro está aqui para provar.
MOD = "cofre_teste"
LISTA, CLARO, PROTEGIDO = "itens", "acesso", "acesso_protegido"
ARQ = f"state_{MOD}.json"
fs.registrar(MOD, LISTA, CLARO, PROTEGIDO)


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


def tmp(nome):
    return Path(_ta.pasta_temp(prefix=f"port3_{nome}_"))


def onde_vazou(texto: str) -> list[str]:
    """Rótulos dos segredos encontrados. O valor nunca é devolvido."""
    return [rotulo for rotulo, valor in SEGREDOS.items() if valor in texto]


def montar(raiz: Path) -> dict:
    """Instalação com segredo em todo canto onde ele poderia se esconder."""
    raiz.mkdir(parents=True, exist_ok=True)
    (raiz / "certs").mkdir(exist_ok=True)
    (raiz / "certs" / "EMPRESA.pfx").write_bytes(b"PFX-FALSO")

    (raiz / "certificados.json").write_text(json.dumps([{
        "id": "11111111000111", "cnpj": "11111111000111", "nome": "ACME LTDA",
        "caminho": "certs/EMPRESA.pfx",
        "senha_protegida": seguranca.proteger(SEGREDOS["senha de certificado"], raiz),
    }], ensure_ascii=False), encoding="utf-8")

    # o formato ANTIGO: credencial de portal em texto claro
    (raiz / ARQ).write_text(json.dumps({
        "catalogo": [{"nome": "Portal Fictício"}],
        LISTA: [{"id": 1, "nome": "ACME", "rotulo": "Portal Fictício",
                 CLARO: {"Código da empresa": "8PXXV", "Usuário": "MASTER",
                         "Senha": SEGREDOS["senha de portal"]}}],
    }, ensure_ascii=False), encoding="utf-8")

    (raiz / "usuarios.json").write_text(
        '{"admin":{"hash":"abc","sal":"123","admin":true}}', encoding="utf-8")
    (raiz / "elo_chave_ed25519.json").write_text(
        json.dumps({"privada": SEGREDOS["chave privada do Elo"]}), encoding="utf-8")
    (raiz / "elo_integracao_ed25519.json").write_text(
        json.dumps({"privada": SEGREDOS["chave privada do Elo"]}), encoding="utf-8")
    (raiz / "elo_config.json").write_text(
        json.dumps({"tenant_id": "t1", "app_secret": SEGREDOS["app secret"],
                    "whatsapp_token": SEGREDOS["token do WhatsApp"]}), encoding="utf-8")
    (raiz / "sessoes.db").write_bytes(SEGREDOS["token de sessão"].encode())
    (raiz / "elo_sync.db").write_bytes(b"fila")
    (raiz / "fiscale.log").write_text(
        f"login ok {SEGREDOS['token de sessão']}", encoding="utf-8")

    for cnpj, nfse_nsu, nfe_nsu in (("11111111000111", 276, 454225),):
        (raiz / cnpj / "nfe").mkdir(parents=True, exist_ok=True)
        (raiz / cnpj / "estado.json").write_text(
            json.dumps({"ultimoNSU": nfse_nsu}), encoding="utf-8")
        (raiz / cnpj / "nfe" / "estado.json").write_text(
            json.dumps({"ultNSU": nfe_nsu, "maxNSU": nfe_nsu}), encoding="utf-8")
        (raiz / cnpj / "xmls").mkdir(exist_ok=True)
        (raiz / cnpj / "xmls" / "n.xml").write_text("<nfse/>", encoding="utf-8")
    return {"nfe": 454225, "nfse": 276}


def apontar(raiz: Path):
    fd.LEGADO_NFSE = raiz / "__nada__"
    fd.LEGADO_FISCALE = raiz / "__nada__"
    fd._raiz_cache = None
    fd.raiz(raiz)
    fd.raiz_padrao = lambda: raiz.resolve()


# ══════════════════════════════════════════════════════════════════════════
secao("A credencial de portal sai do texto claro sozinha, no arranque")

origem = tmp("origem")
esperado = montar(origem)
apontar(origem)

bruto_antes = (origem / ARQ).read_text("utf-8")
ok("senha de portal" in onde_vazou(bruto_antes),
   "antes da migração, a senha ESTÁ legível (é o defeito que se corrige)")

fm.garantir(origem)
bruto = (origem / ARQ).read_text("utf-8")
igual(onde_vazou(bruto), [], f"depois do arranque, sumiu do {ARQ}")
emp = json.loads(bruto)[LISTA][0]
ok("Senha" not in (emp.get(CLARO) or {}), "saiu do bloco em claro")
ok((emp.get(PROTEGIDO) or {}).get("Senha", "").startswith("dpapi:"),
   "e está protegida pelo Windows")
igual(seguranca.desproteger(emp[PROTEGIDO]["Senha"], origem),
      SEGREDOS["senha de portal"], "abre e devolve o valor certo")
ok(emp[CLARO]["Usuário"] == "MASTER", "o que não é segredo continua lá")
# A migração é dirigida pela INSCRIÇÃO, não por um nome de arquivo conhecido:
# esta tela não existe no produto e mesmo assim foi protegida.
ok(MOD in fs.MAPA, "e quem decidiu isso foi a inscrição da tela, não o cofre")

secao("Nenhum segredo vai para o navegador")
enviado = json.dumps(fs.mascarar_para_enviar(
    MOD, json.loads((origem / ARQ).read_text("utf-8")), origem),
    ensure_ascii=False)
igual(onde_vazou(enviado), [], "o JSON que a tela recebe não tem segredo")
ok("dpapi:" not in enviado, "nem a forma protegida dele")
ok(CLARO + "_estado" in enviado, "vai só a situação (ok / aguardando)")

secao("Nenhum segredo em log")
for arq in origem.glob("*.log"):
    vaz = onde_vazou(arq.read_text("utf-8", "ignore"))
    # o fiscale.log foi plantado com segredo de propósito: o que se exige é que
    # ele NUNCA entre no backup (testado adiante), e que a migração não escreva
    # segredo em log novo
    if arq.name == "fiscale.log":
        continue
    igual(vaz, [], f"{arq.name} sem segredo")
if (origem / "migracao.log").exists():
    igual(onde_vazou((origem / "migracao.log").read_text("utf-8", "ignore")), [],
          "migracao.log não registra o valor de nenhum segredo")

# ══════════════════════════════════════════════════════════════════════════
secao("O backup .fbk não carrega segredo nenhum")

saida = tmp("saida")
r = bk.exportar(saida, FRASE, raiz=origem)
fbk = Path(r["arquivo"])

with bk.pacote_aberto(fbk, FRASE) as _dentro, zipfile.ZipFile(_dentro) as z:
    nomes = z.namelist()
    manifesto_bruto = z.read("manifesto.json").decode("utf-8")
    tudo = b"".join(z.read(n) for n in nomes if not n.endswith("/"))

texto_tudo = tudo.decode("latin-1", "ignore")
vazou = onde_vazou(texto_tudo)
igual(vazou, [], "nenhum dos segredos plantados aparece no pacote")
ok(FRASE not in texto_tudo, "a frase-senha de exportação não está no pacote")
igual(onde_vazou(manifesto_bruto), [], "o manifesto não tem segredo")
ok(FRASE not in manifesto_bruto, "nem a frase-senha")

for proibido, oque in (("sessoes.db", "sessões"), ("elo_sync.db", "fila do Elo"),
                       ("elo_chave_ed25519", "chave privada do Elo"),
                       ("elo_integracao_ed25519", "chave de integração do Elo"),
                       ("elo_config.json", "config do Elo com app secret e token"),
                       ("fiscale.log", "log")):
    ok(not any(proibido in n for n in nomes), f"não entrou no pacote: {oque}")
# No v1 "nenhum .pfx em claro" queria dizer "não está na lista de nomes do
# ZIP" — ele viajava dentro do cofre. No v2 o pacote INTEIRO é cifrado, então
# o .pfx está sim na lista de dentro (e precisa estar: backup sem certificado
# não é backup). A propriedade que importa mudou de lugar, e ficou mais forte:
# nada dele pode aparecer nos BYTES do arquivo.
ok(any(n.endswith(".pfx") for n in nomes),
   "o .pfx está dentro do pacote — backup sem certificado não é backup")
_bytes_fbk = Path(fbk).read_bytes()
ok(b"PFX-FALSO" not in _bytes_fbk,
   "e o conteúdo dele NÃO aparece nos bytes do .fbk")
ok(b"EMPRESA.pfx" not in _bytes_fbk,
   "nem o nome do arquivo de certificado")
ok(b"certs/" not in _bytes_fbk,
   "nem o nome da pasta de certificados")

with bk.pacote_aberto(fbk, FRASE) as _d:
    with zipfile.ZipFile(_d) as _z:
        cert_pacote = json.loads(_z.read("dados/certificados.json"))
        state_pacote = json.loads(_z.read(f"dados/{ARQ}"))
ok(all("senha_protegida" not in c for c in cert_pacote),
   "o cadastro no pacote não leva o campo de senha DPAPI")
emp_p = state_pacote[LISTA][0]
ok(PROTEGIDO not in emp_p, "nem o cofre da credencial de portal")
ok(not (emp_p.get(CLARO) or {}).get("Senha"), "nem a senha em claro")
igual(emp_p[CLARO]["Usuário"], "MASTER", "mas a configuração do portal veio")

secao("Caminho absoluto de certificado não entra no backup")
for c in cert_pacote:
    cam = c.get("caminho") or ""
    ok(not Path(cam).is_absolute(), f"caminho relativo: {cam}")
    ok("Downloads" not in cam, "e nada apontando para Downloads")
ok("Downloads" not in manifesto_bruto or "%DOWNLOADS%" in manifesto_bruto,
   "o manifesto não fixa a pasta Downloads de ninguém")

# ══════════════════════════════════════════════════════════════════════════
secao("MÁQUINA LIMPA — partindo de uma pasta vazia")

nova = tmp("maquina_limpa")
ok(not any(nova.iterdir()), "a pasta começa vazia")
apontar(nova)

est = bk.estado_do_destino(nova)
ok(est["vazio"], "o Fiscale reconhece que não há nada aqui")
igual(est["empresas"], 0, "nenhuma empresa")

fm.garantir(nova)
ok((nova / "dados_versao.json").exists(), "a estrutura é criada no arranque")
igual(fm.versao(nova), fm.VERSAO_ATUAL, "já no formato atual")

rr = bk.restaurar(fbk, FRASE, raiz=nova)
ok(rr.get("ok"), "a restauração conclui numa instalação nova")

secao("Máquina limpa — o que o usuário encontra")
reg = json.loads((nova / "certificados.json").read_text("utf-8"))
igual(len(reg), 1, "a empresa está cadastrada")
igual(reg[0]["nome"], "ACME LTDA", "com o nome")
ok(not reg[0].get("senha_protegida"), "sem a senha (é da outra máquina)")
igual(rr["aguardando_senha"], 1, "e a restauração avisa: 1 aguardando senha")

os.environ["FISCALE_DADOS"] = str(nova)
fd._raiz_cache = None
fd.raiz(nova)
sys.modules.pop("main", None)
import main as api  # noqa: E402
lista = api.listar_certificados()
igual(len(lista), 1, "a API lista a empresa")
igual(lista[0]["senha_estado"], "aguardando", "como AGUARDANDO SENHA")
ok(lista[0]["existe"], "e o .pfx está no lugar (veio do cofre)")

state_novo = json.loads((nova / ARQ).read_text("utf-8"))
pend = fs.pendencias(MOD, state_novo, nova)
igual(len(pend), 1, "a tela tem 1 pendência de credencial de portal")
igual(pend[0]["campos"], ["Senha"], "e diz qual campo falta")
igual(pend[0]["nome"], "ACME", "identificando o item")
igual(pend[0]["rotulo"], "Portal Fictício",
      "com um rótulo livre — o contrato não conhece 'operadora'")

secao("Máquina limpa — estado de sincronismo preservado")
nfe = json.loads((nova / "11111111000111" / "nfe" / "estado.json").read_text("utf-8"))
nfse = json.loads((nova / "11111111000111" / "estado.json").read_text("utf-8"))
igual(nfe["ultNSU"], esperado["nfe"], "ultNSU do NF-e intacto")
igual(nfse["ultimoNSU"], esperado["nfse"], "ultimoNSU do NFS-e intacto")
ok((nova / "11111111000111" / "xmls" / "n.xml").exists(), "e os XMLs vieram")

secao("Máquina limpa — nada preso à máquina antiga")
todo_texto = ""
for p in nova.rglob("*.json"):
    todo_texto += p.read_text("utf-8", "ignore")
igual(onde_vazou(todo_texto), [], "nenhum segredo da máquina de origem")
ok("Downloads" not in todo_texto or "%DOWNLOADS%" in todo_texto,
   "nenhuma dependência da pasta Downloads de alguém")
import re as _re
usuarios = set(_re.findall(r"[A-Za-z]:[\\/]Users[\\/]([^\\/\"']+)", todo_texto))
igual(usuarios, set(), "nenhum caminho de usuário do Windows nos dados")

secao("Máquina limpa — o diagnóstico enxerga o que falta")
import diagnostico_instalacao as diag
r_diag = diag.executar(nova)
por_titulo = {i["titulo"]: i for i in r_diag["itens"]}
igual(por_titulo["Certificados aguardando senha"]["situacao"], "ATENÇÃO",
      "aponta os certificados aguardando senha")
igual(por_titulo["Segredo em texto claro"]["situacao"], "OK",
      "e confirma que não há segredo legível")
igual(por_titulo["Certificados fora da pasta do Fiscale"]["situacao"], "OK",
      "nem certificado fora da pasta")
ok(por_titulo["NF-e (ultNSU)"]["situacao"] == "OK", "vê o estado do NF-e")
port = r_diag["portabilidade"]
runtime = next(l for l in port["linhas"] if l["item"] == "Runtime portátil")
ok(str(runtime["situacao"]).startswith("PENDENTE"),
   "e NÃO diz que a instalação é portátil enquanto depender do Python instalado")
ok("PORT 4" in port["veredito"], "o veredito aponta a fase que resolve isso")

secao("O diagnóstico não imprime segredo")
texto_diag = json.dumps(r_diag, ensure_ascii=False)
igual(onde_vazou(texto_diag), [], "o relatório do diagnóstico não tem segredo")
ok("dpapi:" not in texto_diag, "nem blob de proteção")

# ══════════════════════════════════════════════════════════════════════════
# CAMINHOS DEGRADADOS
#
# Os testes acima cobrem o dia bom: DPAPI responde, o arquivo existe e é JSON
# válido. Os de baixo cobrem o dia ruim, porque é nele que segredo vaza ou some
# calado. A regra que estes testes fixam, e que vale para todos eles:
#
#     falhar é aceitável; falhar em claro, ou falhar em silêncio, não é.
#
# Quando a proteção não funciona, o segredo é DESCARTADO na gravação (nunca
# gravado legível) e MANTIDO como estava na migração (nunca perdido sem aviso).
# ══════════════════════════════════════════════════════════════════════════
import contextlib as _ctx


@_ctx.contextmanager
def _dpapi_quebrado(quebrar_proteger=True, quebrar_desproteger=False):
    """Simula DPAPI indisponível — perfil de outra máquina, chave revogada,
    conta do Windows recriada. Não dá para provocar isso de verdade num teste,
    então trocamos a função pela que falha e conferimos a reação do sistema."""
    p_orig, d_orig = seguranca.proteger, seguranca.desproteger
    if quebrar_proteger:
        def _falha(*_a, **_k):
            raise OSError("CryptProtectData falhou")
        seguranca.proteger = _falha
    if quebrar_desproteger:
        def _falha_d(*_a, **_k):
            raise OSError("CryptUnprotectData falhou")
        seguranca.desproteger = _falha_d
    try:
        yield
    finally:
        seguranca.proteger, seguranca.desproteger = p_orig, d_orig


def _state_com_senha(valor):
    return {LISTA: [{"id": 1, "nome": "ACME",
                     CLARO: {"Usuário": "MASTER", "Senha": valor}}]}


secao("Chave indisponível — gravar descarta o segredo, nunca grava em claro")
degradado = tmp("degradado")
d = _state_com_senha(SEGREDOS["senha de portal"])
with _dpapi_quebrado():
    d, rel_g = fs.proteger_para_gravar(MOD, d, degradado)
emp_d = d[LISTA][0]
igual(onde_vazou(json.dumps(d, ensure_ascii=False)), [],
      "o que iria para o disco não tem o segredo")
ok("Senha" not in emp_d[CLARO], "o campo em claro não sobrou")
ok(not emp_d.get(PROTEGIDO), "e nada foi gravado no cofre")
igual(rel_g["descartados"], 1, "o relatório conta 1 descarte (não é silencioso)")
igual(emp_d[CLARO]["Usuário"], "MASTER", "o que não é segredo continua lá")

secao("Chave indisponível — a migração NÃO apaga o legado que não conseguiu proteger")
d = _state_com_senha(SEGREDOS["senha de portal"])
with _dpapi_quebrado():
    rel_m = fs.migrar_texto_claro(MOD, d, degradado)
emp_d = d[LISTA][0]
igual(rel_m["falharam"], 1, "a migração reporta a falha")
igual(rel_m["migrados"], 0, "e não conta como migrado")
igual(emp_d[CLARO].get("Senha"), SEGREDOS["senha de portal"],
      "o valor legado continua onde estava — some depois, não agora")
ok(not emp_d.get(PROTEGIDO), "e o cofre não ficou com meia proteção")

secao("Segredo protegido que não abre nesta máquina vira 'aguardando'")
d = _state_com_senha(SEGREDOS["senha de portal"])
d, _ = fs.proteger_para_gravar(MOD, d, degradado)
with _dpapi_quebrado(quebrar_proteger=False, quebrar_desproteger=True):
    enviado_d = json.dumps(fs.mascarar_para_enviar(MOD, json.loads(json.dumps(d)),
                                                   degradado), ensure_ascii=False)
    pend_d = fs.pendencias(MOD, json.loads(json.dumps(d)), degradado)
ok("aguardando" in enviado_d, "a tela recebe a situação 'aguardando'")
ok("dpapi:" not in enviado_d, "e não recebe o blob que não abriu")
igual(onde_vazou(enviado_d), [], "nem o segredo")
igual([p["campos"] for p in pend_d], [["Senha"]], "a pendência diz qual campo falta")

secao("Blob corrompido é tratado como ausente, não como texto")
d = _state_com_senha(SEGREDOS["senha de portal"])
d, _ = fs.proteger_para_gravar(MOD, d, degradado)
blob = d[LISTA][0][PROTEGIDO]["Senha"]
d[LISTA][0][PROTEGIDO]["Senha"] = blob[:-8] + "AAAAAAAA"
igual([p["campos"] for p in fs.pendencias(MOD, json.loads(json.dumps(d)), degradado)],
      [["Senha"]], "cofre corrompido aparece como pendência")
enviado_c = json.dumps(fs.mascarar_para_enviar(MOD, d, degradado), ensure_ascii=False)
ok("dpapi:" not in enviado_c, "e o blob corrompido não vai para a tela")

secao("Senha vazia não apaga a que já estava guardada")
guardado = _state_com_senha(SEGREDOS["senha de portal"])
guardado, _ = fs.proteger_para_gravar(MOD, guardado, degradado)
# a tela devolve o formulário inteiro, com a senha em branco (ela nunca a recebeu)
vindo_da_tela = _state_com_senha("")
vindo_da_tela = fs.preservar_protegidos(MOD, vindo_da_tela, guardado)
vindo_da_tela, rel_v = fs.proteger_para_gravar(MOD, vindo_da_tela, degradado)
emp_v = vindo_da_tela[LISTA][0]
igual(rel_v["mantidos"], 1, "o campo vazio conta como 'mantido'")
igual(seguranca.desproteger(emp_v[PROTEGIDO]["Senha"], degradado),
      SEGREDOS["senha de portal"], "e a senha guardada continua abrindo")
ok(not fs.informar(MOD, vindo_da_tela, 1, "Senha", "", degradado),
   "informar com valor vazio é recusado")
igual(seguranca.desproteger(
    vindo_da_tela[LISTA][0]["acesso_protegido"]["Senha"], degradado),
    SEGREDOS["senha de portal"], "e não destrói o cofre existente")

secao("Senha que não sobreviveria à ida e volta não é gravada")
# Cofre que abre, mas devolve OUTRA coisa. É o pior caso silencioso: o sistema
# diria "ok" e o portal recusaria o login sem ninguém entender por quê.
frag = {LISTA: [{"id": 1, "nome": "ACME", CLARO: {"Usuário": "MASTER"}}]}
_orig_desp = seguranca.desproteger
seguranca.desproteger = lambda *_a, **_k: "outra-coisa-qualquer"
try:
    fs.informar(MOD, frag, 1, "Senha", SEGREDOS["senha de portal"], degradado)
    ok(False, "informar deveria ter recusado uma senha que não confere na volta")
except Exception:
    ok(True, "informar recusa quando a ida e volta não bate")
finally:
    seguranca.desproteger = _orig_desp
ok(not frag[LISTA][0].get(PROTEGIDO),
   "e não deixa o cofre meio gravado")
igual(onde_vazou(json.dumps(frag, ensure_ascii=False)), [],
      "nem o segredo escapa para o item recusado")

# e o caminho feliz do mesmo método continua de pé
bom = {LISTA: [{"id": 1, "nome": "ACME", CLARO: {"Usuário": "MASTER"}}]}
ok(fs.informar(MOD, bom, 1, "Senha", SEGREDOS["senha de portal"], degradado),
   "informar aceita a senha quando a conferência bate")
igual(seguranca.desproteger(bom[LISTA][0][PROTEGIDO]["Senha"], degradado),
      SEGREDOS["senha de portal"], "e ela abre depois")
igual(onde_vazou(json.dumps(bom[LISTA][0][CLARO], ensure_ascii=False)), [],
      "sem sobrar nada em claro no bloco de acesso")

secao("Arquivo ausente — a migração não inventa nem estoura")
vazia = tmp("sem_estado")
rel_a: dict = {}
fm._proteger_segredos(vazia, rel_a)
igual(rel_a, {}, f"sem {ARQ}, nada a relatar")
ok(not (vazia / ARQ).exists(), "e nenhum arquivo é criado do nada")
igual(fs.pendencias(MOD, {}, vazia), [], "sem dados, sem pendência")
igual(fs.mascarar_para_enviar(MOD, {}, vazia), {}, "e a tela recebe o vazio")
igual(fs.migrar_texto_claro(MOD, {}, vazia),
      {"migrados": 0, "falharam": 0, "ja_protegidos": 0}, "migrar o vazio é inócuo")

secao("Arquivo corrompido — não é sobrescrito nem truncado")
corrompida = tmp("corrompido")
lixo = '{"' + LISTA + '": [{"id": 1, "' + CLARO + '": {"Senha": '   # JSON cortado ao meio
(corrompida / ARQ).write_text(lixo, encoding="utf-8")
antes_bytes = (corrompida / ARQ).read_bytes()
rel_c: dict = {}
fm._proteger_segredos(corrompida, rel_c)          # não pode levantar
igual((corrompida / ARQ).read_bytes(), antes_bytes,
      "o arquivo ilegível fica exatamente como estava")
ok(f"segredos_{MOD}" not in rel_c, "e a migração não afirma ter migrado nada")
ok(not (corrompida / (ARQ + ".tmp")).exists(), "sem .tmp pela metade")
ok(not (corrompida / (ARQ + ".pre-port3")).exists(),
   "e sem backup de um arquivo que ela não conseguiu ler")
log_c = (corrompida / "migracao.log")
ok(log_c.exists() and "ilegivel" in log_c.read_text("utf-8", "ignore"),
   "o problema fica registrado no migracao.log")

secao("Tipos inesperados no lugar do dicionário não derrubam nada")
for esquisito in ({LISTA: "não é lista"},
                  {LISTA: [None, 7, "x"]},
                  {LISTA: [{"id": 1, CLARO: "não é dicionário"}]},
                  []):
    try:
        copia = json.loads(json.dumps(esquisito))
        fs.proteger_para_gravar(MOD, copia, degradado)
        fs.mascarar_para_enviar(MOD, copia, degradado)
        fs.migrar_texto_claro(MOD, copia, degradado)
        fs.pendencias(MOD, copia, degradado)
        ok(True, f"aguenta {type(esquisito).__name__} malformado sem quebrar")
    except Exception as e:
        ok(False, f"quebrou com entrada malformada: {type(e).__name__}")

secao("O .fbk sanitiza mesmo quando o state da tela está corrompido")
bruto_ok, n_ok = bk._limpar_state(MOD, json.dumps(
    _state_com_senha(SEGREDOS["senha de portal"])).encode("utf-8"))
igual(onde_vazou(bruto_ok.decode("utf-8")), [], "senha em claro sai do pacote")
ok(n_ok >= 1, "e o manifesto registra quantas saíram")
bruto_lixo, n_lixo = bk._limpar_state(MOD, lixo.encode("utf-8"))
igual(bruto_lixo, lixo.encode("utf-8"), "arquivo ilegível volta inalterado")
igual(n_lixo, 0, "sem alegar remoção que não houve")

# ── COBERTURA NOVA: o cofre é infraestrutura, não parte de uma tela ─────────
secao("O cofre continua íntegro sem NENHUMA tela inscrita")
sem_tela = tmp("sem_tela")
guardado_mapa = dict(fs.MAPA)
try:
    fs.MAPA.clear()
    vazio = _state_com_senha(SEGREDOS["senha de portal"])
    copia = json.loads(json.dumps(vazio))
    d_v, rel_vz = fs.proteger_para_gravar(MOD, copia, sem_tela)
    igual(rel_vz, {"protegidos": 0, "mantidos": 0, "descartados": 0},
          "sem inscrição não há o que proteger — e isso não é erro")
    igual(fs.pendencias(MOD, copia, sem_tela), [], "nem pendência a relatar")
    igual(fs.migrar_texto_claro(MOD, copia, sem_tela),
          {"migrados": 0, "falharam": 0, "ja_protegidos": 0},
          "nem migração a fazer")
    ok(seguranca.desproteger(
        seguranca.proteger(SEGREDOS["senha de portal"], sem_tela), sem_tela)
        == SEGREDOS["senha de portal"],
       "e o cofre em si continua cifrando e decifrando normalmente")
    rel_vazio: dict = {}
    bruto_vz = bk._tratar("dados/" + ARQ,
                          json.dumps(vazio).encode("utf-8"), rel_vazio)
    ok("Senha" in bruto_vz.decode("utf-8"),
       "o backup NÃO adivinha segredo de tela que não se inscreveu")
finally:
    fs.MAPA.clear()
    fs.MAPA.update(guardado_mapa)
ok(MOD in fs.MAPA, "e a inscrição volta intacta para o resto da suíte")

secao("Inscrever é o gesto único: protege o cofre E o backup")
outra = "outra_tela_ficticia"
fs.registrar(outra, "linhas", "credencial", "credencial_protegida")
try:
    d_o = {"linhas": [{"id": 7, "nome": "Z",
                       "credencial": {"Login": "z", "Token": SEGREDOS["senha de portal"]}}]}
    bruto_o, n_o = bk._limpar_state(outra, json.dumps(d_o).encode("utf-8"))
    igual(onde_vazou(bruto_o.decode("utf-8")), [],
          "uma tela nova, inscrita hoje, já sai limpa do backup sem tocar no backup")
    ok(n_o >= 1, "e a remoção é contada")
    igual(json.loads(bruto_o)["linhas"][0]["credencial"]["Login"], "z",
          "o que não é segredo continua no pacote")
finally:
    fs.esquecer(outra)
ok(outra not in fs.MAPA, "e desinscrever é uma linha, sem tocar no cofre")

secao("Blob de DPAPI nunca viaja, mesmo de tela NÃO inscrita")
solto = {"qualquer": [{"id": 1, "acesso_protegido": {"Senha": "dpapi:AAAA"},
                       "acesso_estado": {"Senha": "ok"},
                       "acesso": {"Usuário": "MASTER"}}]}
bruto_s, n_s = bk._limpar_state("tela_que_ninguem_inscreveu",
                                json.dumps(solto).encode("utf-8"))
texto_s = bruto_s.decode("utf-8")
ok("dpapi:" not in texto_s, "o blob protegido foi removido pela rede de segurança")
ok("acesso_estado" not in texto_s, "e a situação junto (é derivada, não viaja)")
ok(n_s >= 1, "e a remoção foi contada")
igual(json.loads(bruto_s)["qualquer"][0]["acesso"]["Usuário"], "MASTER",
      "sem levar embora o que não era segredo")

secao("A rede de segurança não confunde chave de acesso com segredo")
notas = {"notas": [{"chave": "35240712345678000199550010000000011000000017",
                    "chaveAcesso": "43240712345678000199550010000000021000000023",
                    "senha": "isto-sim-e-segredo"}]}
bruto_n, _ = bk._limpar_state("nfe_vendas", json.dumps(notas).encode("utf-8"))
saida_n = json.loads(bruto_n)["notas"][0]
igual(saida_n["chave"], "35240712345678000199550010000000011000000017",
      "a chave de acesso da nota sobrevive — apagá-la seria pior que o vazamento")
igual(saida_n["chaveAcesso"], "43240712345678000199550010000000021000000023",
      "inclusive escrita de outro jeito")

# ══════════════════════════════════════════════════════════════════════════
secao("O código não guarda segredo escrito")
import ast as _ast, io as _io, tokenize as _tok


def so_codigo(fonte: str) -> str:
    arv = _ast.parse(fonte)
    docs = {_ast.get_docstring(n, clean=False) for n in _ast.walk(arv)
            if isinstance(n, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))}
    docs.discard(None)
    saida = []
    for t in _tok.generate_tokens(_io.StringIO(fonte).readline):
        if t.type == _tok.COMMENT:
            continue
        if t.type == _tok.STRING and t.string.strip("\"'brf") in docs:
            continue
        saida.append(t.string)
    return "\n".join(saida)


suspeito = _re.compile(r"(senha|password|token|secret|api[_\-]?key)\s*=\s*[\"'][^\"']{6,}[\"']", _re.I)
achados = []
for p in RAIZ.rglob("*.py"):
    if any(x in p.parts for x in (".venv", "build", "dist", "__pycache__", "elo")) \
            or p.parts[len(RAIZ.parts):][0].startswith(("backup_", "teste_")):
        continue
    try:
        codigo = so_codigo(p.read_text("utf-8", "ignore"))
    except Exception:
        continue
    for m in suspeito.finditer(codigo):
        trecho = m.group(0)
        if any(x in trecho.lower() for x in ("informada", "protegida", "or none",
                                             "d.get", "req.", "self.", "kw", "= senha")):
            continue
        achados.append(f"{p.name}: {m.group(1)}")
igual(achados, [], "nenhuma senha escrita direto no código")

secao("A documentação não transcreve credencial")
# Achado em 13/08/2026: a senha REAL do portal estava escrita, em claro, dentro
# de FISCALE_AUDITORIA_FISCAL.md e FISCALE_ROADMAP_FISCAL.md — documentos que o
# backup_para_drive.bat SOBE para o Google Drive (ele exclui `dados`, .env e
# .pfx, mas .md vai). Corrigir o programa e deixar o segredo escrito no relatório
# que descreve a correção anula a correção.
#
# O teste não pode conter o valor procurado (seria reintroduzi-lo). Procura a
# FORMA: um campo de segredo recebendo um literal concreto em prosa ou em código.
_ATRIBUICAO = _re.compile(
    r'["\']?(senha|password|passwd|pwd|secret|token|api[_\- ]?key)["\']?'
    r'\s*[:=]\s*["\']([^"\']{4,80})["\']', _re.I)
# O que é legítimo aparecer: marcador de redação, exemplo declarado, blob
# protegido, nome de variável, placeholder.
_INOCENTE = _re.compile(
    r'^(<[^>]+>|\.{3}|x{3,}|\*{3,}|dpapi:.*|fernet:.*|SEGREDO-[\w\-]*|'
    r'[A-Z_]{4,}|\$\{.*\}|%[A-Z_]+%|sua[_\- ].*|troque.*|informe.*|'
    r'senha[_\- ].*|.*_(protegida|informada|nova)|null|None|true|false)$', _re.I)

docs_sujos = []
for p in list(RAIZ.glob("*.md")) + list(RAIZ.glob("*.txt")):
    if p.name.startswith("backup_"):
        continue
    for m in _ATRIBUICAO.finditer(p.read_text("utf-8", "ignore")):
        valor = m.group(2).strip()
        if _INOCENTE.match(valor) or valor.startswith(("<", "«")):
            continue
        docs_sujos.append(f"{p.name}: campo '{m.group(1)}'")   # o valor NUNCA entra aqui
igual(docs_sujos, [], "nenhum documento transcreve o valor de uma credencial")

ok(_ATRIBUICAO.search('"Senha":"000000"') is not None,
   "(o detector reconhece a forma que vazou)")
ok(_INOCENTE.match("<REDIGIDO>") is not None,
   "e aceita o marcador de redação sem alarme falso")

secao("O .bat do Drive não leva mais dados nem segredo")
bat = (RAIZ / "backup_para_drive.bat").read_text("utf-8", "ignore")
ok('"dados"' in bat, "a pasta de dados é excluída explicitamente")
for x in ("*.pfx", "*.db", "elo_chave_ed25519.json", "elo_integracao_ed25519.json",
          "elo_config.json", "*.log", ".env"):
    ok(x in bat, f"e também {x}")
# O .bat não procura mais o .fbk sozinho: quem decide é `backup_drive.py`,
# porque o cmd.exe não sabe perguntar ao Windows onde fica a Área de Trabalho
# real (que aqui está no OneDrive, com espaço e acento no nome). A propriedade
# continua sendo "o backup viaja"; mudou o lugar onde ela é garantida.
ok("backup_drive.py" in bat, "e ele passou a transportar o backup oficial .fbk")
ok(".fbk" in (RAIZ / "backup_drive.py").read_text("utf-8", "ignore"),
   "pelo módulo que sabe achar a Área de Trabalho de verdade")
ok("MESA=" not in bat,
   "e o .bat não adivinha mais a pasta por conta própria")

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Todos os testes de segurança passaram.")
