#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes de backup e restauração do Fiscale — PORT 2.

O teste que importa é o de IDA E VOLTA:

    dados originais -> .fbk -> pasta VAZIA -> restauração -> comparação

Criar o .fbk não prova nada. O que prova é a instalação vazia voltar a
funcionar, com os arquivos íntegros e com todo estado de sincronismo
(ultNSU / ultimoNSU) exatamente onde estava.

    python teste_backup.py

Nada toca os dados reais: tudo em pastas temporárias.

ATENÇÃO — ESTA SUÍTE É DO FORMATO v1
    O v1 deixou de ser gerado: `exportar()` produz v2, cifrado por
    inteiro. Mas o v1 continua sendo LIDO, para recuperar backups
    antigos, e é isso que se prova aqui — por `_exportar_v1`, chamado
    de propósito pelo nome interno.

    O formato atual tem suíte própria: `teste_backup_v2.py`.
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

import fiscale_backup as bk          # noqa: E402
import fiscale_dados as fd           # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []
FRASE = "frase de exportacao do escritorio 2026"


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
    return Path(_ta.pasta_temp(prefix=f"port2_{nome}_"))


# Tela fictícia com credencial: existe só nesta suíte. Ver `montar_instalacao`.
MOD_TELA = "portal_teste"
import fiscale_segredos as _fseg  # noqa: E402
_fseg.registrar(MOD_TELA, "itens", "acesso", "acesso_protegido")


def montar_instalacao(raiz: Path) -> dict:
    """Uma instalação com a cara da real: empresas, XMLs, PDFs, pastas de
    módulo, certificados, e os DOIS tipos de estado de sincronismo.

    A tela `portal_teste` não existe no produto: é uma tela fictícia inscrita
    no cofre só para esta suíte, do mesmo jeito que em `teste_seguranca`. O
    backup precisa provar que credencial de tela não viaja — e essa prova não
    pode depender de qual tela o produto tem no momento."""
    raiz.mkdir(parents=True, exist_ok=True)
    esperado = {}

    def escrever(rel, conteudo):
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        dados = conteudo if isinstance(conteudo, bytes) else conteudo.encode("utf-8")
        p.write_bytes(dados)
        esperado[rel.replace("\\", "/")] = dados

    escrever("usuarios.json", '{"admin":{"hash":"abc","sal":"123"}}')
    escrever("state_clientes.json", json.dumps(
        {"clientes": [{"id": 1, "cnpj": "11111111000111", "nome": "ACME LTDA",
                       "regime": "Simples Nacional", "ie": "123", "im": "456"}],
         "seq": 2}))
    escrever("state_classificador.json", json.dumps(
        {"11111111000111": {"anexo": "III", "issFixo": 250.0, "folha12m": 90000}}))
    escrever("state_painel.json", '{"seqDoc": 3}')

    # com credencial de portal em texto claro — NÃO pode sair daqui
    escrever(f"state_{MOD_TELA}.json", json.dumps({
        "catalogo": [{"nome": "Portal Fictício"}],
        "itens": [{"id": 1, "nome": "ACME",
                   "acesso": {"Código da empresa": "8PXXV",
                              "Usuário": "MASTER",
                              "Senha": "SEGREDO-DO-PORTAL-f4c1a9"}}],
        "outros": []}))

    reg = []
    for i, (cnpj, nome) in enumerate((("11111111000111", "ACME LTDA"),
                                      ("22222222000122", "BETA LTDA"),
                                      ("33333333000133", "GAMA ME"))):
        arq = f"{nome.replace(' ', '_')}.pfx"
        escrever(f"certs/{arq}", b"PFX-FALSO-" + cnpj.encode())
        reg.append({"id": cnpj, "cnpj": cnpj, "nome": nome,
                    "apelido": f"Apelido {i}", "caminho": f"certs/{arq}",
                    "procurador": i == 2,
                    "senha_protegida": f"dpapi:SEGREDO-DPAPI-{cnpj}"})
    escrever("certificados.json", json.dumps(reg, ensure_ascii=False, indent=2))
    escrever("entrada_config.json", json.dumps({"pasta": "%DOWNLOADS%", "auto": True}))

    # documentos por empresa + os dois estados de sincronismo
    for cnpj, nfse_nsu, nfe_nsu in (("11111111000111", 276, 454225),
                                    ("22222222000122", 77, 45828),
                                    ("33333333000133", 12, 0)):
        escrever(f"{cnpj}/estado.json",
                 json.dumps({"ultimoNSU": nfse_nsu, "atualizadoEm": "2026-07-24T17:58:49"}))
        if nfe_nsu:
            escrever(f"{cnpj}/nfe/estado.json",
                     json.dumps({"ultNSU": nfe_nsu, "maxNSU": nfe_nsu}))
            for j in range(4):
                escrever(f"{cnpj}/nfe/{nfe_nsu - j}-procNFe.xml", f"<nfe n='{j}'/>")
        for j in range(3):
            escrever(f"{cnpj}/xmls/nfse-{j}.xml", f"<nfse empresa='{cnpj}' n='{j}'/>")
        escrever(f"{cnpj}/pdfs/danfse-{cnpj}.pdf", b"%PDF-1.4 falso")
        escrever(f"{cnpj}/municipal/extratos.json",
                 json.dumps({"versao": 1, "extratos": [{"id": "abc", "competencia": "07/2026"}]}))
        escrever(f"{cnpj}/municipal/originais/abc-relatorio.pdf", b"%PDF-1.4 relatorio")
        escrever(f"{cnpj}/cte/imp-1.xml", "<cte/>")
        escrever(f"{cnpj}/nfe_importadas/imp-2.xml", "<nfe/>")

    # o que NÃO pode entrar no backup
    (raiz / "sessoes.db").write_bytes(b"sessao efemera")
    (raiz / "sessoes.db-wal").write_bytes(b"wal")
    (raiz / "elo_sync.db").write_bytes(b"fila do elo")
    (raiz / "fiscale.log").write_text("log", encoding="utf-8")
    (raiz / "migracao.log").write_text("log", encoding="utf-8")
    (raiz / "elo_chave_ed25519.json").write_text('{"privada":"NAO PODE SAIR"}', encoding="utf-8")
    (raiz / "elo_integracao_ed25519.json").write_text('{"privada":"NAO PODE SAIR"}', encoding="utf-8")
    (raiz / "uploads").mkdir(exist_ok=True)
    (raiz / "uploads" / "temporario.pdf").write_bytes(b"lixo")
    (raiz / "__pycache__").mkdir(exist_ok=True)
    (raiz / "__pycache__" / "x.pyc").write_bytes(b"cache")
    (raiz / "certificados.json.pre-port1").write_text("[]", encoding="utf-8")
    return esperado


# ══════════════════════════════════════════════════════════════════════════
secao("Exportar — o pacote nasce completo")

origem = tmp("origem")
esperado = montar_instalacao(origem)
fd._raiz_cache = None
fd.raiz(origem)

saida = tmp("saida")
r = bk._exportar_v1(saida, FRASE, raiz=origem)
fbk = Path(r["arquivo"])
ok(fbk.exists(), "o .fbk foi criado")
ok(fbk.name.startswith("FISCALE-backup-") and fbk.suffix == ".fbk",
   f"com o nome no padrão ({fbk.name})")
import re as _re
ok(bool(_re.fullmatch(r"FISCALE-backup-\d{4}-\d{2}-\d{2}-\d{4}\.fbk", fbk.name)),
   "FISCALE-backup-AAAA-MM-DD-HHMM.fbk")
ok(not list(saida.glob("*.parcial")), "e nenhum arquivo .parcial ficou para trás")

# Bug pego na tela: pasta que AINDA NÃO EXISTE não é `is_dir()`, e o pacote
# saía gravado com o nome da pasta, sem extensão e sem data.
nova = tmp("saida_nova") / "pasta_que_nao_existe"
r_nova = bk._exportar_v1(nova, FRASE, raiz=origem)
ok(Path(r_nova["arquivo"]).parent == nova,
   "pasta de destino que ainda não existe é criada, e o pacote vai DENTRO dela")
ok(Path(r_nova["arquivo"]).suffix == ".fbk", "com a extensão .fbk")
ok(nova.is_dir() and not nova.is_file(), "a pasta é pasta, não virou arquivo")
# e um caminho terminado em .fbk continua sendo o nome do arquivo
exato = tmp("saida_exata") / "meu-backup.fbk"
ok(Path(bk._exportar_v1(exato, FRASE, raiz=origem)["arquivo"]).name == "meu-backup.fbk",
   "caminho terminado em .fbk é respeitado como nome de arquivo")
igual(r["empresas_cadastradas"], 3, "3 empresas cadastradas no resumo")
igual(r["certificados_no_cofre"], 3, "3 certificados no cofre")
igual(r["estado_sincronismo_qtd"], 5, "5 arquivos de estado de sincronismo")

secao("Exportar — o que NÃO pode sair, não saiu")
with zipfile.ZipFile(fbk) as z:
    nomes = z.namelist()
    tudo = b"".join(z.read(n) for n in nomes if not n.endswith("/"))
    man = json.loads(z.read("manifesto.json"))
    cert_no_pacote = json.loads(z.read("dados/certificados.json"))
    tela_no_pacote = json.loads(z.read(f"dados/state_{MOD_TELA}.json"))

for proibido in ("sessoes.db", "elo_sync.db", "fiscale.log", "migracao.log",
                 "elo_chave_ed25519.json", "elo_integracao_ed25519.json",
                 "uploads/", "__pycache__", ".pre-port1", ".db-wal"):
    ok(not any(proibido in n for n in nomes), f"não entrou: {proibido}")
ok(not any(n.endswith((".pfx", ".p12")) for n in nomes),
   "nenhum .pfx em claro dentro do pacote")
ok(b"SEGREDO-DPAPI" not in tudo, "nenhuma senha DPAPI no pacote (nem em claro nem cifrada)")
ok(b"SEGREDO-DO-PORTAL" not in tudo, "a senha do portal NÃO saiu da máquina")
ok(all("senha_protegida" not in c for c in cert_no_pacote),
   "o cadastro no pacote não tem o campo senha_protegida")
igual(tela_no_pacote["itens"][0]["acesso"]["Senha"], "",
      "a credencial do portal foi zerada, e o resto do cadastro ficou")
igual(tela_no_pacote["itens"][0]["acesso"]["Usuário"], "MASTER",
      "usuário e código do portal continuam (não são segredo)")
igual(r["senhas_dpapi_removidas"], 3, "o relatório diz quantas senhas DPAPI saíram")
igual(r["senhas_portal_removidas"], 1, "e quantas senhas de portal foram removidas")
ok(b"PFX-FALSO" not in tudo,
   "o conteúdo dos .pfx não aparece em lugar nenhum do pacote (está cifrado)")

secao("Exportar — os dados que DEVEM ir, foram")
for critico in ("dados/certificados.json", "dados/usuarios.json",
                "dados/state_clientes.json", "dados/state_classificador.json",
                "dados/11111111000111/nfe/estado.json",
                "dados/11111111000111/estado.json",
                "dados/11111111000111/municipal/extratos.json",
                "dados/11111111000111/municipal/originais/abc-relatorio.pdf",
                "dados/22222222000122/xmls/nfse-0.xml",
                "dados/33333333000133/cte/imp-1.xml"):
    ok(critico in nomes, f"está no pacote: {critico.replace('dados/', '')}")
ok("cofre.fbkc" in nomes, "o cofre está no pacote")

secao("Manifesto — legível SEM a frase-senha")
info = bk.inspecionar(fbk)
igual(info["produto"], "Fiscale", "identifica o produto")
igual(info["formato"], bk.FORMATO, "traz a versão do formato")
ok(info["_precisa_frase"], "avisa que precisa de frase-senha")
# Os .pfx não entram no manifesto em claro: vão pelo cofre, e quem garante a
# integridade deles é a etiqueta de autenticação do AES-GCM — mais forte que um
# hash listado, porque também prova que ninguém trocou o cofre inteiro.
em_claro = {k for k in esperado if not k.startswith("certs/")}
igual(len(info["arquivos"]), len(em_claro),
      f"lista os {len(em_claro)} arquivos em claro")
igual(sorted(info["arquivos"]), sorted(em_claro), "exatamente os que estão no pacote")
igual(info["cofre"]["arquivos"], 3, "e conta os 3 certificados que foram para o cofre")
igual(sorted(info["cofre"]["nomes"]),
      sorted(k.split("/")[-1] for k in esperado if k.startswith("certs/")),
      "nomeando quais são, sem revelar o conteúdo")
ok(all("sha256" in v for v in info["arquivos"].values()), "com o hash de cada um")
igual(info["cofre"]["algoritmo"], "AES-256-GCM", "e diz como o cofre foi fechado")
ok("scrypt" in info["cofre"]["kdf"], "e qual derivação de chave")
ok(FRASE not in json.dumps(info), "a frase-senha NÃO está no manifesto")
est = info["resumo"]["estado_sincronismo"]
igual(est["11111111000111/nfe/estado.json"]["ultNSU"], 454225,
      "o manifesto registra o ultNSU de cada empresa")
igual(est["11111111000111/estado.json"]["ultimoNSU"], 276, "e o ultimoNSU também")

secao("Conferência — hash a hash")
c = bk.conferir(fbk, FRASE)
ok(c["ok"], "o backup passa na conferência")
igual(c["conferidos"], len(em_claro), f"conferiu os {len(em_claro)} arquivos em claro")
igual(c["problemas"], [], "sem problema nenhum")
ok(bk.conferir(fbk, FRASE)["ok"], "e o cofre abre com a frase certa")

secao("Frase-senha errada não abre — e não devolve lixo")
try:
    bk.conferir(fbk, "frase errada")
    ok(False, "deveria ter recusado")
except bk.FraseIncorreta as e:
    ok(True, "recusa com erro específico")
    ok("frase-senha" in str(e).lower(), "explicando em português")

secao("Arquivo adulterado é recusado")
adulterado = saida / "adulterado.fbk"
with zipfile.ZipFile(fbk) as z_in, zipfile.ZipFile(adulterado, "w") as z_out:
    for n in z_in.namelist():
        dados = z_in.read(n)
        if n == "dados/11111111000111/nfe/estado.json":
            dados = json.dumps({"ultNSU": 0, "maxNSU": 0}).encode()   # zerar o NSU!
        z_out.writestr(n, dados)
c2 = bk.conferir(adulterado)
ok(not c2["ok"], "a conferência reprova o pacote adulterado")
ok(any("estado.json" in p for p in c2["problemas"]),
   "e aponta exatamente o arquivo trocado")

nao_e_backup = saida / "qualquer.fbk"
nao_e_backup.write_bytes(b"isto nao e um zip")
try:
    bk.inspecionar(nao_e_backup)
    ok(False, "deveria recusar arquivo que não é backup")
except bk.BackupInvalido:
    ok(True, "arquivo que não é backup é recusado com explicação")

# ══════════════════════════════════════════════════════════════════════════
secao("IDA E VOLTA — restaurar numa instalação VAZIA")

destino = tmp("destino")
fd._raiz_cache = None
fd.raiz(destino)
rr = bk.restaurar(fbk, FRASE, raiz=destino)
ok(rr.get("ok"), "a restauração concluiu")
igual(rr["modo"], "novo", "reconheceu que o destino estava vazio")

print("\n  --- comparação arquivo a arquivo ---")
obtido = {}
for p in destino.rglob("*"):
    if not p.is_file():
        continue
    rel = p.relative_to(destino).as_posix()
    if rel in ("dados_versao.json", "migracao.log"):
        continue
    obtido[rel] = p.read_bytes()

faltando = sorted(set(esperado) - set(obtido))
sobrando = sorted(set(obtido) - set(esperado))
diferentes = sorted(k for k in esperado if k in obtido and obtido[k] != esperado[k])

igual(len(obtido), len(esperado),
      f"a instalação restaurada tem os mesmos {len(esperado)} arquivos")
ok(not faltando, "nenhum arquivo faltando" + (f" — {faltando[:6]}" if faltando else ""))
ok(not sobrando, "nenhum arquivo a mais" + (f" — {sobrando[:6]}" if sobrando else ""))
# certificados.json e o state da tela mudam de propósito (segredos removidos)
esperado_diferente = {"certificados.json", f"state_{MOD_TELA}.json"}
igual(set(diferentes), esperado_diferente,
      "só mudaram os dois arquivos de onde os segredos foram tirados")

print("\n  --- estado de sincronismo, um por um ---")
for rel in sorted(k for k in esperado if k.endswith("estado.json")):
    a = json.loads(esperado[rel])
    b = json.loads(obtido[rel])
    tipo = "NF-e  ultNSU   " if "/nfe/" in rel else "NFS-e ultimoNSU"
    campo = "ultNSU" if "/nfe/" in rel else "ultimoNSU"
    ok(a == b, f"{tipo} {rel.split('/')[0]} = {b.get(campo)} (idêntico)")
igual(sum(1 for k in obtido if k.endswith("estado.json")), 5,
      "os 5 arquivos de estado chegaram")

print("\n  --- certificados ---")
reg = json.loads(obtido["certificados.json"])
igual(len(reg), 3, "as 3 empresas continuam cadastradas")
igual([c["nome"] for c in reg], ["ACME LTDA", "BETA LTDA", "GAMA ME"], "com os nomes")
igual([c["apelido"] for c in reg], ["Apelido 0", "Apelido 1", "Apelido 2"], "e os apelidos")
ok(any(c["procurador"] for c in reg), "a marcação de procurador sobreviveu")
ok(all(not c.get("senha_protegida") for c in reg), "nenhuma com senha (é DPAPI)")
igual(rr["aguardando_senha"], 3, "a restauração informa: 3 aguardando senha")
igual(rr["certificados"], 3, "e que 3 .pfx saíram do cofre")
for c in reg:
    p = destino / c["caminho"]
    ok(p.exists(), f"o .pfx de {c['nome']} está em {c['caminho']}")
    igual(p.read_bytes(), esperado[c["caminho"]], f"  e é o arquivo original, byte a byte")

print("\n  --- o que o usuário vê ao abrir ---")
os.environ["FISCALE_DADOS"] = str(destino)
fd._raiz_cache = None
fd.raiz(destino)
sys.modules.pop("main", None)
import main as api  # noqa: E402
lista = api.listar_certificados()
igual(len(lista), 3, "a API lista as 3 empresas")
ok(all(x["existe"] for x in lista), "e acha o .pfx de todas")
ok(all(x["senha_estado"] == "aguardando" for x in lista),
   "as 3 aparecem como AGUARDANDO SENHA")
pend = api.certificados_pendentes()
igual(pend["aguardando_senha"], 3, "o resumo confirma 3 pendentes")
ok(all(p["arquivo_presente"] for p in pend["pendentes"]),
   "com o arquivo disponível para informar a senha")

secao("Informar a senha depois de restaurar não recadastra nada")
class _R:
    def __init__(s, i, se): s.id, s.senha = i, se

# core.documento_do_certificado abriria um .pfx de verdade; aqui o .pfx é falso,
# então trocamos por uma versão que devolve o CNPJ esperado — o que está sob
# teste é o fluxo do cadastro, não a leitura do certificado.
import core
_original = core.documento_do_certificado
core.documento_do_certificado = lambda caminho, senha: (
    "11111111000111" if senha == "senha-certa" else (_ for _ in ()).throw(ValueError("senha")))
try:
    try:
        api.informar_senha(_R("11111111000111", "senha-errada"))
        ok(False, "senha errada deveria ser recusada")
    except Exception as e:
        ok(getattr(e, "status_code", None) == 400, "senha errada é recusada")
    res = api.informar_senha(_R("11111111000111", "senha-certa"))
    igual(res["senha_estado"], "ok", "com a senha certa, a empresa fica pronta")
finally:
    core.documento_do_certificado = _original

depois = api.listar_certificados()
igual(len(depois), 3, "continuam sendo 3 empresas — nada foi recadastrado")
igual(sum(1 for x in depois if x["senha_estado"] == "ok"), 1, "uma pronta")
igual(sum(1 for x in depois if x["senha_estado"] == "aguardando"), 2, "duas aguardando")
ok("senha-certa" not in (destino / "certificados.json").read_text("utf-8"),
   "e a senha não foi gravada em texto claro")

# ══════════════════════════════════════════════════════════════════════════
secao("Destino com dados — o sistema NÃO decide sozinho")

ocupado = tmp("ocupado")
montar_instalacao(ocupado)
fd._raiz_cache = None
fd.raiz(ocupado)
r3 = bk.restaurar(fbk, FRASE, raiz=ocupado)
ok(not r3.get("ok"), "a restauração para")
ok(r3.get("precisa_decisao"), "e pede decisão")
igual(sorted(r3["opcoes"]), ["cancelar", "mesclar", "substituir"],
      "oferecendo Cancelar, Mesclar e Substituir")
ok(r3["destino"]["arquivos"] > 0 and r3["destino"]["empresas"] == 3,
   "mostrando o que já existe lá (arquivos e empresas)")
ok(all(v for v in r3["opcoes"].values()), "cada opção com a explicação do que faz")

secao("Instalação recém-criada conta como VAZIA")
# É o caminho principal: instalar, abrir, criar a senha do admin, restaurar.
# Sem esta distinção o sistema pedia mesclar/substituir logo no primeiro uso,
# quando não havia trabalho nenhum a perder.
nova = tmp("recem")
(nova / "usuarios.json").write_text('{"admin":{"hash":"h","sal":"s"}}', encoding="utf-8")
(nova / "dados_versao.json").write_text('{"versao":2}', encoding="utf-8")
(nova / "migracao.log").write_text("linha", encoding="utf-8")
est = bk.estado_do_destino(nova)
ok(est["vazio"], "instalação com só login e marcador de formato é 'vazia'")
igual(est["arquivos_de_trabalho"], 0, "porque não há arquivo de trabalho nenhum")
fd._raiz_cache = None
fd.raiz(nova)
r_nova2 = bk.restaurar(fbk, FRASE, raiz=nova)
ok(r_nova2.get("ok"), "e a restauração acontece direto, sem perguntar nada")
igual(len(json.loads((nova / "certificados.json").read_text("utf-8"))), 3,
      "trazendo as 3 empresas")

secao("Mas um único documento de empresa já pede decisão")
quase = tmp("quase")
(quase / "usuarios.json").write_text('{"admin":{}}', encoding="utf-8")
(quase / "11111111000111" / "xmls").mkdir(parents=True)
(quase / "11111111000111" / "xmls" / "uma-nota.xml").write_text("<x/>", encoding="utf-8")
ok(not bk.estado_do_destino(quase)["vazio"], "com uma nota fiscal, não é vazia")
fd._raiz_cache = None
fd.raiz(quase)
ok(bk.restaurar(fbk, FRASE, raiz=quase).get("precisa_decisao"),
   "e a restauração para para perguntar")

secao("Mesclar por CNPJ — o que já está aqui vence")
mesclado = tmp("mesclado")
montar_instalacao(mesclado)
# esta máquina já tem UMA senha informada e uma empresa que o backup não tem
reg_local = json.loads((mesclado / "certificados.json").read_text("utf-8"))
reg_local[0]["senha_protegida"] = "dpapi:SENHA-JA-INFORMADA-AQUI"
reg_local.append({"id": "99999999000199", "cnpj": "99999999000199",
                  "nome": "SO DESTA MAQUINA LTDA", "caminho": "certs/OUTRA.pfx"})
(mesclado / "certificados.json").write_text(json.dumps(reg_local, ensure_ascii=False), "utf-8")
(mesclado / "44444444000144" / "xmls").mkdir(parents=True)
(mesclado / "44444444000144" / "xmls" / "so-aqui.xml").write_text("<x/>", encoding="utf-8")
(mesclado / "11111111000111" / "xmls" / "nfse-0.xml").write_text("<LOCAL/>", encoding="utf-8")

fd._raiz_cache = None
fd.raiz(mesclado)
r4 = bk.restaurar(fbk, FRASE, raiz=mesclado, modo="mesclar")
ok(r4.get("ok"), "a mesclagem conclui")
reg_dep = json.loads((mesclado / "certificados.json").read_text("utf-8"))
igual(len(reg_dep), 4, "3 do backup + 1 que só existia aqui = 4 empresas")
achou = next(c for c in reg_dep if c["cnpj"] == "11111111000111")
igual(achou.get("senha_protegida"), "dpapi:SENHA-JA-INFORMADA-AQUI",
      "a senha já informada NESTA máquina foi mantida")
ok(any(c["cnpj"] == "99999999000199" for c in reg_dep),
   "a empresa que só existia aqui não sumiu")
igual((mesclado / "11111111000111" / "xmls" / "nfse-0.xml").read_text("utf-8"), "<LOCAL/>",
      "arquivo que existe dos dois lados fica como está aqui")
ok((mesclado / "44444444000144" / "xmls" / "so-aqui.xml").exists(),
   "e a empresa local intocada")
ok(r4["mantidos"] > 0, "o relatório diz quantos foram mantidos")

secao("Substituir — funciona mesmo com o Fiscale aberto")
# Defeito pego no teste do pacote portátil: o `sessoes.db` fica aberto enquanto
# o Fiscale roda, e no Windows um arquivo aberto trava a pasta inteira. Mover a
# raiz de uma vez falhava com "arquivo em uso", e a mensagem não dizia o porquê.
import fiscale_sessoes                                              # noqa: E402
aberto = tmp("com_banco")
montar_instalacao(aberto)
(aberto / "sessoes.db").unlink()               # o de mentira sai; entra um de verdade
sess = fiscale_sessoes.abrir(str(aberto))      # segura o banco, como o servidor
sess.criar("admin")
fd._raiz_cache = None
fd.raiz(aberto)
r_ab = bk.restaurar(fbk, FRASE, raiz=aberto, modo="substituir")
ok(r_ab.get("ok"), "restaura em Substituir com o banco de sessões aberto")
presos = r_ab.get("nao_puderam_ser_movidos") or []
ok(all("sessoes.db" in p for p in presos),
   f"só o banco de sessões fica preso, e ele é efêmero ({presos})")
guardado_ab = Path(r_ab["anterior_movido_para"])
ok((guardado_ab / "certificados.json").exists(),
   "e a instalação anterior foi preservada assim mesmo")
igual(len(json.loads((aberto / "certificados.json").read_text("utf-8"))), 3,
      "a raiz ficou com as 3 empresas do backup")
sess.fechar()

secao("Substituir NÃO pode desligar o modo portátil")
# Defeito encontrado no teste do pacote portátil: a marca `.fiscale-portatil`
# ia junto com o resto, e no reinício seguinte o Fiscale voltava a apontar para
# C:\Users\<voce>\Fiscale — a instalação de pendrive deixava de ser portátil em
# silêncio, logo depois de uma restauração. No teste, isso fez o servidor abrir
# a pasta de dados REAL do usuário.
port = tmp("portatil_subst")
montar_instalacao(port)
(port / fd.MARCA_PORTATIL).write_text("marca", encoding="utf-8")
fd._raiz_cache = None
fd.raiz(port)
r_p = bk.restaurar(fbk, FRASE, raiz=port, modo="substituir")
ok(r_p.get("ok"), "restaura em Substituir numa instalação portátil")
ok((port / fd.MARCA_PORTATIL).exists(),
   "e a marca .fiscale-portatil continua lá — a instalação segue portátil")
ok(r_p.get("marca_portatil_reposta"), "o relatório diz que a marca foi reposta")
# e numa instalação NÃO portátil, a marca não deve aparecer do nada
comum = tmp("comum_subst")
montar_instalacao(comum)
fd._raiz_cache = None
fd.raiz(comum)
r_c = bk.restaurar(fbk, FRASE, raiz=comum, modo="substituir")
ok(not (comum / fd.MARCA_PORTATIL).exists(),
   "numa instalação comum, a marca NÃO é inventada")

secao("Substituir — move o anterior, nunca apaga")
subst = tmp("subst")
montar_instalacao(subst)
(subst / "MARCA-DA-INSTALACAO-ANTIGA.txt").write_text("estava aqui", encoding="utf-8")
fd._raiz_cache = None
fd.raiz(subst)
r5 = bk.restaurar(fbk, FRASE, raiz=subst, modo="substituir")
ok(r5.get("ok"), "a substituição conclui")
guardado = Path(r5["anterior_movido_para"])
ok(guardado.exists(), "a instalação anterior foi MOVIDA para uma pasta ao lado")
ok((guardado / "MARCA-DA-INSTALACAO-ANTIGA.txt").exists(),
   "e continua inteira lá — nada foi apagado")
ok(not (subst / "MARCA-DA-INSTALACAO-ANTIGA.txt").exists(),
   "a raiz agora tem só o conteúdo do backup")
igual(len(json.loads((subst / "certificados.json").read_text("utf-8"))), 3,
      "com as 3 empresas do backup")

# ══════════════════════════════════════════════════════════════════════════
secao("Backup sem certificado nenhum não exige frase-senha")
sem_cert = tmp("semcert")
(sem_cert / "usuarios.json").parent.mkdir(parents=True, exist_ok=True)
(sem_cert / "usuarios.json").write_text('{"admin":{}}', encoding="utf-8")
saida2 = tmp("saida2")
r6 = bk._exportar_v1(saida2, None, raiz=sem_cert)
ok(r6["ok"], "exporta sem frase")
info6 = bk.inspecionar(r6["arquivo"])
ok(not info6["_precisa_frase"], "e o manifesto diz que não precisa de frase")
destino6 = tmp("destino6")
fd._raiz_cache = None
fd.raiz(destino6)
ok(bk.restaurar(r6["arquivo"], raiz=destino6).get("ok"), "e restaura sem frase")

secao("Com certificados, a frase é obrigatória na exportação")
try:
    bk._exportar_v1(tmp("saida3"), None, raiz=origem)
    ok(False, "deveria exigir a frase")
except ValueError as e:
    ok("frase-senha" in str(e), "exige a frase, explicando por quê")

secao("Restaurar backup com cofre sem informar a frase")
d7 = tmp("destino7")
fd._raiz_cache = None
fd.raiz(d7)
r7 = bk.restaurar(fbk, None, raiz=d7)
ok(not r7.get("ok") and r7.get("precisa_frase"), "para e pede a frase")
ok(not any(d7.iterdir()), "sem escrever nada no destino")

secao("Cada backup tem sal próprio")
a = bk._exportar_v1(tmp("s_a"), FRASE, raiz=origem)["arquivo"]
b = bk._exportar_v1(tmp("s_b"), FRASE, raiz=origem)["arquivo"]
with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
    ca, cb = za.read("cofre.fbkc"), zb.read("cofre.fbkc")
ok(ca[:16] != cb[:16], "sal diferente a cada exportação")
ok(ca != cb, "e o cofre nunca sai igual, mesmo com a mesma frase e os mesmos dados")
ok(bk._abrir_cofre(ca, FRASE).keys() == bk._abrir_cofre(cb, FRASE).keys(),
   "mas os dois abrem com a mesma frase")

secao("O ELO não participa disto")
fonte = (RAIZ / "fiscale_backup.py").read_text("utf-8")
import ast, io, tokenize
def so_codigo(f):
    arv = ast.parse(f)
    docs = {ast.get_docstring(n, clean=False) for n in ast.walk(arv)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    docs.discard(None)
    out = []
    for t in tokenize.generate_tokens(io.StringIO(f).readline):
        if t.type == tokenize.COMMENT:
            continue
        if t.type == tokenize.STRING and t.string.strip("\"'brf") in docs:
            continue
        out.append(t.string)
    return "\n".join(out)
codigo = so_codigo(fonte)
ok("import fiscale_elo" not in codigo, "o motor de backup não importa o ELO")
ok("elo_sync" not in codigo or "elo_sync.db" in codigo,
   "só cita o elo_sync.db para EXCLUIR do pacote")

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Todos os testes de backup passaram.")
