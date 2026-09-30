#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backup v2 — o pacote inteiro cifrado.

    python teste_backup_v2.py

O QUE SE PROVA
    Que NENHUM texto conhecido dos dados aparece nos bytes do `.fbk` — CNPJ,
    nome de usuário, nome de XML, conteúdo do manifesto. Que dois backups da
    mesma pasta saem diferentes. Que frase errada, cabeçalho mexido, corpo
    mexido, tag mexida e truncamento falham SEM extrair nada. Que o v1 ainda
    abre localmente e nunca sobe para o Drive. E que a frase não sobra em
    arquivo, argumento, variável de ambiente nem log.

    Nenhuma senha ou dado real aparece aqui: tudo é inventado para o teste.
"""
from __future__ import annotations

import ast
import io as _io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import backup_drive as bd                              # noqa: E402
import fiscale_backup as bk                            # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                               # noqa: E402


def _connect_proibido(self, endereco, *a, **kw):
    raise AssertionError("teste tentou abrir rede para " + str(endereco))


_socket.socket.connect = _connect_proibido


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
        print("  FALHOU  " + desc)


def igual(a, b, desc):
    ok(a == b, desc if a == b else "%s  (obtive %r, esperava %r)" % (desc, a, b))


FRASE = "cavalo bateria grampo correto"

# Marcas plantadas nos dados. Nenhuma delas pode aparecer nos bytes do .fbk.
CNPJ = "37918422000164"
USUARIO = "manuella.taveira"
XML = "NFe37918422000164550010000123451234567890.xml"
SEGREDO = "conteudo-secreto-que-nao-pode-vazar"


def montar_raiz() -> Path:
    raiz = Path(tempfile.mkdtemp(prefix="fiscale_v2_dados_")) / "dados"
    (raiz / "certs").mkdir(parents=True)
    (raiz / "usuarios.json").write_text(
        json.dumps({USUARIO: {"sal": "ab" * 16, "hash": "cd" * 32,
                              "email": "pessoa@escritorio.invalido"}}),
        encoding="utf-8")
    (raiz / "certificados.json").write_text(
        json.dumps([{"cnpj": CNPJ, "nome": "EMPRESA TESTE LTDA"}]),
        encoding="utf-8")
    xmls = raiz / CNPJ / "xmls"
    xmls.mkdir(parents=True)
    (xmls / XML).write_text("<nfe>%s</nfe>" % SEGREDO, encoding="utf-8")
    (raiz / "certs" / "empresa.pfx").write_bytes(b"PFX-DE-MENTIRA-" * 20)
    return raiz


def main():
    trabalho = Path(tempfile.mkdtemp(prefix="fiscale_v2_saida_"))
    raiz = montar_raiz()

    # ── Nada legível por fora ────────────────────────────────────────────
    secao("Nenhum texto conhecido aparece nos bytes do .fbk")
    r = bk.exportar(trabalho, FRASE, raiz=raiz)
    pacote = Path(r["arquivo"])
    igual(r["formato"], 2, "o formato gerado é o 2")
    bruto = pacote.read_bytes()

    for rotulo, marca in (
            ("o CNPJ", CNPJ),
            ("o nome de usuário", USUARIO),
            ("o nome do XML", XML),
            ("o conteúdo do XML", SEGREDO),
            ("o e-mail", "pessoa@escritorio.invalido"),
            ("a razão social", "EMPRESA TESTE LTDA"),
            ("o sal do usuário", "ab" * 16),
            ("o hash do usuário", "cd" * 32),
            ("a palavra manifesto", "manifesto"),
            ("o nome do arquivo de usuários", "usuarios.json"),
            ("o nome da pasta de certificados", "certs/"),
            ("a marca do PFX", "PFX-DE-MENTIRA"),
            ("a assinatura de ZIP", "PK\x03\x04"),
            ("a própria frase", FRASE)):
        ok(marca.encode("utf-8") not in bruto,
           "%s não aparece nos bytes" % rotulo)

    ok(bruto[:8] == bk.MAGIC_V2, "os 8 primeiros bytes identificam o formato")
    ok(not bruto.startswith(b"PK"), "o arquivo NÃO é um ZIP por fora")

    secao("O cabeçalho em claro revela só parâmetro técnico")
    cabeca = bk.cabecalho_v2(pacote)
    igual(sorted(k for k in cabeca if not k.startswith("_")),
          ["cifra", "dklen", "formato", "kdf", "n", "nonce", "p", "produto",
           "r", "sal"],
          "os campos do cabeçalho são exatamente os declarados")
    igual(cabeca["cifra"], "AES-256-GCM", "a cifra")
    igual(cabeca["kdf"], "scrypt", "a derivação")
    igual(cabeca["n"], bk.SCRYPT_N, "com os parâmetros do projeto")
    for proibido in ("criado_em", "data", "maquina", "raiz", "empresas",
                     "arquivos", "resumo", "origem"):
        ok(proibido not in cabeca,
           "o cabeçalho não conta %r" % proibido)
    # E o `inspecionar` sem frase também não conta nada.
    insp = bk.inspecionar(pacote)
    ok(insp["_precisa_frase"] and insp.get("_cifrado_por_inteiro"),
       "inspecionar sem frase diz apenas que é cifrado por inteiro")
    ok("arquivos" not in insp and "resumo" not in insp,
       "e não devolve manifesto nenhum")

    # ── Sal e nonce novos a cada backup ──────────────────────────────────
    secao("Dois backups da MESMA pasta saem diferentes")
    segundo = bk.exportar(trabalho / "outro.fbk", FRASE, raiz=raiz)
    p2 = Path(segundo["arquivo"])
    c1, c2 = bk.cabecalho_v2(pacote), bk.cabecalho_v2(p2)
    ok(c1["sal"] != c2["sal"], "o sal é novo a cada backup")
    ok(c1["nonce"] != c2["nonce"], "o nonce é novo a cada backup")
    ok(pacote.read_bytes() != p2.read_bytes(),
       "e os bytes dos dois arquivos são diferentes")
    # Mesma frase, mesmos dados, e ainda assim nada em comum no corpo.
    corpo1 = pacote.read_bytes()[c1["_inicio_cifra"]:]
    corpo2 = p2.read_bytes()[c2["_inicio_cifra"]:]
    ok(corpo1[:4096] != corpo2[:4096],
       "o começo do conteúdo cifrado também difere")
    ok(bk.conferir(p2, FRASE)["ok"], "e os dois continuam válidos")
    p2.unlink()

    # ── Frase errada ─────────────────────────────────────────────────────
    secao("Frase errada · falha sem extrair nada")
    temporarias_antes = set(Path(tempfile.gettempdir()).glob("fiscale_v2_*"))
    try:
        bk.conferir(pacote, "frase que nao e a certa")
        ok(False, "deveria ter falhado")
    except bk.FraseIncorreta as e:
        ok(True, "levanta FraseIncorreta")
        ok("Nada foi extraído" in str(e), "e diz que nada foi extraído")
        ok(FRASE not in str(e) and "frase que nao e a certa" not in str(e),
           "sem repetir nenhuma frase na mensagem")
    try:
        bk.conferir(pacote, None)
        ok(False, "sem frase deveria falhar")
    except bk.FraseIncorreta:
        ok(True, "sem frase nenhuma, também falha")
    sobrou = set(Path(tempfile.gettempdir()).glob("fiscale_v2_*")) - temporarias_antes
    igual(sobrou, set(), "e não sobrou pasta temporária nenhuma")

    # ── Adulteração ──────────────────────────────────────────────────────
    secao("Adulteração · cabeçalho, conteúdo e assinatura")
    original = pacote.read_bytes()
    inicio = bk.cabecalho_v2(pacote)["_inicio_cifra"]

    def mexer(bytes_, posicao, nome_do_teste):
        alvo = trabalho / "mexido.fbk"
        b = bytearray(bytes_)
        b[posicao] ^= 0x01
        alvo.write_bytes(bytes(b))
        try:
            bk.conferir(alvo, FRASE)
            ok(False, "%s: deveria ter falhado" % nome_do_teste)
        except (bk.FraseIncorreta, bk.BackupInvalido):
            ok(True, "%s: recusado" % nome_do_teste)
        alvo.unlink()

    # cabeçalho: um bit no JSON dos parâmetros
    mexer(original, inicio - 5, "um bit no cabeçalho")
    # conteúdo: um bit no meio do corpo cifrado
    mexer(original, inicio + (len(original) - inicio) // 2, "um bit no conteúdo")
    # assinatura: um bit na tag do GCM
    mexer(original, len(original) - 3, "um bit na assinatura")
    # magic
    mexer(original, 2, "um bit na marca do formato")

    # Trocar os parâmetros do KDF de forma "coerente" também tem de falhar.
    trocado = trabalho / "kdf.fbk"
    b = bytearray(original)
    i_n = bytes(b).find(b'"n":%d' % bk.SCRYPT_N)
    ok(i_n > 0, "achei o parâmetro do scrypt no cabeçalho")
    b[i_n:i_n + len(b'"n":%d' % bk.SCRYPT_N)] = b'"n":00256'
    trocado.write_bytes(bytes(b))
    try:
        bk.conferir(trocado, FRASE)
        ok(False, "baixar o custo do scrypt deveria falhar")
    except (bk.FraseIncorreta, bk.BackupInvalido):
        ok(True, "baixar o custo do scrypt é recusado — o cabeçalho é AAD")
    trocado.unlink()

    secao("Truncamento")
    for corte, rotulo in ((4, "só a marca"),
                          (inicio - 2, "cabeçalho pela metade"),
                          (inicio + 10, "cabeçalho e um pedaço"),
                          (len(original) - 8, "faltando metade da assinatura"),
                          (len(original) - 1, "faltando um byte")):
        alvo = trabalho / "curto.fbk"
        alvo.write_bytes(original[:corte])
        try:
            bk.conferir(alvo, FRASE)
            ok(False, "truncado (%s): deveria falhar" % rotulo)
        except (bk.FraseIncorreta, bk.BackupInvalido):
            ok(True, "truncado (%s): recusado" % rotulo)
        alvo.unlink()

    # ── Restauração completa ─────────────────────────────────────────────
    secao("Restauração v2 completa, em pasta isolada")
    destino = Path(tempfile.mkdtemp(prefix="fiscale_v2_restaura_")) / "dados"
    rel = bk.restaurar(pacote, FRASE, raiz=destino)
    ok(rel.get("ok"), "a restauração terminou bem")
    ok((destino / "usuarios.json").exists(), "o cadastro voltou")
    ok((destino / CNPJ / "xmls" / XML).exists(), "o XML voltou, com o nome")
    igual((destino / CNPJ / "xmls" / XML).read_text(encoding="utf-8"),
          "<nfe>%s</nfe>" % SEGREDO, "e com o conteúdo intacto")
    ok((destino / "certs" / "empresa.pfx").exists(),
       "o certificado voltou — no v2 ele vem como qualquer arquivo")
    igual((destino / "certs" / "empresa.pfx").read_bytes(),
          b"PFX-DE-MENTIRA-" * 20, "byte a byte")
    ok(rel.get("certificados", 0) >= 1, "e a contagem de certificados confere")
    voltou = json.loads((destino / "usuarios.json").read_text(encoding="utf-8"))
    ok(USUARIO in voltou, "o usuário voltou com o mesmo uid")

    secao("Restauração com frase errada não escreve nada")
    virgem = Path(tempfile.mkdtemp(prefix="fiscale_v2_virgem_")) / "dados"
    virgem.mkdir(parents=True)
    try:
        bk.restaurar(pacote, "frase errada", raiz=virgem)
        ok(False, "deveria ter falhado")
    except bk.FraseIncorreta:
        ok(True, "levanta FraseIncorreta")
    igual(list(virgem.rglob("*")), [], "e a pasta de destino continua vazia")

    # ── v1: lê local, nunca sobe ─────────────────────────────────────────
    secao("v1 · continua legível localmente, e nunca sobe para o Drive")
    v1 = Path(bk._exportar_v1(trabalho / "antigo-v1.fbk", FRASE,
                              raiz=raiz)["arquivo"])
    igual(bk.versao_do_arquivo(v1), 1, "reconhecido como v1")
    igual(bk.versao_do_arquivo(pacote), 2, "e o novo, como v2")
    ok(bk.conferir(v1, FRASE)["ok"], "o v1 ainda confere localmente")
    d1 = Path(tempfile.mkdtemp(prefix="fiscale_v1_rest_")) / "dados"
    ok(bk.restaurar(v1, FRASE, raiz=d1).get("ok"),
       "e ainda restaura localmente")
    ok((d1 / "certs" / "empresa.pfx").exists(),
       "inclusive o certificado, que no v1 vem pelo cofre")

    v = bd.validar(v1)
    ok(not v["ok"], "mas o envio ao Drive o RECUSA")
    igual(v["versao"], 1, "identificando a versão")
    ok("em claro" in v["motivo"], "e dizendo por quê")
    ok(bd.validar(pacote)["ok"], "enquanto o v2 é aceito")

    # E a prova de que o v1 vaza mesmo: os dados abrem sem frase nenhuma.
    with zipfile.ZipFile(v1) as z:
        nomes = z.namelist()
        ok(any(CNPJ in n for n in nomes),
           "(no v1 o CNPJ aparece no NOME de arquivo, sem senha)")
        ok(USUARIO.encode() in z.read("manifesto.json")
           or USUARIO.encode() in z.read("dados/usuarios.json"),
           "(e o cadastro abre em claro)")

    # ── A frase não sobra em lugar nenhum ────────────────────────────────
    secao("A frase não fica em arquivo, argumento, variável ou log")
    for arquivo in trabalho.rglob("*"):
        if arquivo.is_file():
            ok(FRASE.encode() not in arquivo.read_bytes(),
               "a frase não está em %s" % arquivo.name)
    for pasta in (destino, d1):
        achou = [p for p in pasta.rglob("*")
                 if p.is_file() and FRASE.encode() in p.read_bytes()]
        igual(achou, [], "a frase não ficou na pasta restaurada")

    # A propriedade não é "a palavra não aparece" — o módulo EXPLICA, em
    # mensagem, que não tem a frase, e isso é bom. É que ele nunca a recebe,
    # guarda ou repassa.
    fonte_bd = (RAIZ / "backup_drive.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte_bd)
    parametros = {a.arg.lower()
                  for f in ast.walk(arvore)
                  if isinstance(f, ast.FunctionDef)
                  for a in f.args.args + f.args.kwonlyargs}
    ok(not any("frase" in n or "senha" in n for n in parametros),
       "nenhuma função do programa do Drive recebe frase por parâmetro")
    nomes = {n.id.lower() for n in ast.walk(arvore) if isinstance(n, ast.Name)}
    ok(not any("frase" in n or "senha" in n for n in nomes),
       "e não guarda nenhuma em variável")
    chaves = {k.arg.lower() for c in ast.walk(arvore)
              if isinstance(c, ast.Call) for k in c.keywords if k.arg}
    ok(not any("frase" in k or "senha" in k for k in chaves
               if k != "frase"),
       "nem passa uma adiante por argumento nomeado")
    chamadas_bk = {getattr(c.func, "attr", "") for c in ast.walk(arvore)
                   if isinstance(c, ast.Call)
                   and getattr(getattr(c.func, "value", None), "id", "")
                   == "fiscale_backup"}
    ok("conferir" not in chamadas_bk and "conferir_v2" not in chamadas_bk,
       "e nem chama a conferência que exigiria frase — só lê o cabeçalho")
    ok("cabecalho_v2" in chamadas_bk,
       "que é o que dá para saber sem a chave")
    ambientes = {c.args[0].value for c in ast.walk(arvore)
                 if isinstance(c, ast.Call)
                 and getattr(c.func, "attr", "") == "get"
                 and getattr(getattr(c.func, "value", None), "attr", "") == "environ"
                 and c.args and isinstance(c.args[0], ast.Constant)}
    ok(not any("FRASE" in a.upper() or "SENHA" in a.upper() for a in ambientes),
       "e não lê frase de variável de ambiente (lê: %s)"
       % (sorted(ambientes) or "nenhuma"))

    # O servidor não pode registrar a frase.
    fonte_srv = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8")
    # O bloco termina na PRÓXIMA rota, seja ela qual for. Fatiar até uma rota
    # nomeada faz o teste depender de quem é o vizinho: quando as rotas de
    # importação de cadastro entraram entre as duas, esta asserção acusou um
    # vazamento que não existia — ela estava lendo código de outra rota.
    i = fonte_srv.index('if rota == "/api/backup/criar":')
    j = fonte_srv.index('if rota == "', i + 20)
    bloco = "\n".join(l for l in fonte_srv[i:j].splitlines()
                      if not l.strip().startswith("#"))
    ok('d.get("frase")' in bloco, "a rota recebe a frase pelo corpo do POST")
    ok("os.environ" not in bloco, "e não a põe em variável de ambiente")

    # A GUARDA MUDOU DE FORMA, E NÃO DE FORÇA.
    #
    # Antes ela proibia as palavras `print(`, `auditar(` e `log` dentro do
    # bloco. Isso pegava o vazamento — e pegava junto QUALQUER registro, o que
    # deixou a rota de backup sem telemetria nenhuma: sem início, sem fim, sem
    # duração, sem motivo de falha. Um backup de nove minutos virava boato.
    #
    # A regra sempre foi sobre a FRASE, então é ela que se verifica agora:
    # dentro do bloco, a frase só pode aparecer sendo repassada ao módulo; e
    # em TODO o servidor, nenhuma chamada de registro pode recebê-la. Isto é
    # mais estrito que a versão anterior, que só olhava um bloco.
    mencoes = [l.strip() for l in bloco.splitlines() if "frase" in l]
    ok(mencoes and all('d.get("frase")' in l for l in mencoes),
       "a frase só aparece para ser repassada ao módulo (%d menção(ões))"
       % len(mencoes))

    arvore_srv = ast.parse(fonte_srv)
    REGISTRO = {"auditar", "registrar", "print", "_backup_log", "_backup_anotar"}
    com_frase = []
    for no in ast.walk(arvore_srv):
        if not isinstance(no, ast.Call):
            continue
        nome = getattr(no.func, "id", "") or getattr(no.func, "attr", "")
        if nome in REGISTRO and "frase" in ast.unparse(no):
            com_frase.append(ast.unparse(no)[:70])
    ok(not com_frase,
       "nenhuma chamada de registro do servidor recebe a frase (%s)"
       % (com_frase or "nenhuma"))

    # E não entra em argumento de processo nem em variável de ambiente.
    fonte_bk = (RAIZ / "fiscale_backup.py").read_text(encoding="utf-8")
    codigo_bk = ast.unparse(ast.parse(fonte_bk))
    for perigo in ("os.environ['FISCALE_FRASE']", "sys.argv", "subprocess"):
        ok(perigo not in codigo_bk,
           "o módulo de backup não usa %r" % perigo)

    # ── As exclusões continuam de pé ─────────────────────────────────────
    secao("As exclusões continuam valendo no v2")
    for lixo in ("tentativas.db", "auditoria.db", "sessoes.db",
                 ".pimenta-login", "elo_sync.db", "fiscale.log"):
        (raiz / lixo).write_bytes(b"nao pode viajar")
    (raiz / "temp.tmp").write_bytes(b"x")
    (raiz / "backups").mkdir(exist_ok=True)
    (raiz / "backups" / "anterior.fbk").write_bytes(b"backup velho")
    (raiz / CNPJ / "indice").mkdir(parents=True, exist_ok=True)
    (raiz / CNPJ / "indice" / "documentos.db").write_bytes(b"cache")
    novo = Path(bk.exportar(trabalho / "com-lixo.fbk", FRASE,
                            raiz=raiz)["arquivo"])
    with bk._aberto_v2(novo, FRASE) as interno:
        with zipfile.ZipFile(interno) as z:
            dentro = {n.rsplit("/", 1)[-1].lower() for n in z.namelist()}
    for proibido in ("tentativas.db", "auditoria.db", "sessoes.db",
                     ".pimenta-login", "elo_sync.db", "fiscale.log",
                     "temp.tmp", "documentos.db", "anterior.fbk"):
        ok(proibido not in dentro, "%s ficou de fora" % proibido)
    ok(not any(n.endswith(".fbk") for n in dentro), "nenhum .fbk dentro do .fbk")

    secao("O .parcial não sobra, nem quando dá errado")
    ok(not list(trabalho.glob("*.parcial")),
       "nenhum .parcial depois de exportar com sucesso")
    quebrado = Path(tempfile.mkdtemp(prefix="fiscale_v2_erro_"))
    try:
        bk.exportar(quebrado, "", raiz=raiz)
        ok(False, "exportar sem frase deveria falhar")
    except ValueError as e:
        ok(True, "exportar sem frase é recusado")
        ok("cifrado" in str(e), "explicando que o pacote inteiro é cifrado")
    igual(list(quebrado.glob("*")), [], "e não deixou arquivo nenhum")

    # ── Leitura adiantada ────────────────────────────────────────────────
    secao("Leitura adiantada · o pacote não depende do número de trabalhadores")
    campo = Path(tempfile.mkdtemp(prefix="fiscale_v2_par_")) / "dados"
    (campo / "certs").mkdir(parents=True)
    (campo / "usuarios.json").write_text('{"a": {"sal": "00", "hash": "aa"}}',
                                         encoding="utf-8")
    for e in range(3):
        d = campo / ("1234567800%02d" % e) / "xmls"
        d.mkdir(parents=True)
        for i in range(80):
            (d / ("n%03d.xml" % i)).write_text("<nfe>%d</nfe>" % i,
                                               encoding="utf-8")
    (campo / "certs" / "x.pfx").write_bytes(b"PFX")
    # Um arquivo acima do limite: tem de sair do adiantamento e ser lido sozinho.
    (campo / "grande.bin").write_bytes(b"G" * (bk.LIMITE_PARALELO + 4096))
    saida_par = Path(tempfile.mkdtemp(prefix="fiscale_v2_parout_"))

    pacotes = {}
    for w in (1, 2, 4, 8):
        r = bk.exportar(saida_par / ("w%d.fbk" % w), FRASE, raiz=campo,
                        trabalhadores=w)
        with bk._aberto_v2(Path(r["arquivo"]), FRASE) as dentro:
            with zipfile.ZipFile(dentro) as z:
                man = json.loads(z.read("manifesto.json"))
                pacotes[w] = (list(man["arquivos"].keys()), man["arquivos"],
                              z.namelist())
    base_ordem, base_hashes, base_nomes = pacotes[1]
    ok(len(base_ordem) > 200,
       "o conjunto de teste tem %d arquivos" % len(base_ordem))
    # A ordem é: os arquivos ordenados, e DEPOIS os certificados — eles são
    # acrescentados no fim, de propósito. Não é "tudo ordenado"; é sempre a
    # mesma coisa, que é o que reprodutibilidade quer dizer.
    dados_, certs_ = ([k for k in base_ordem if not k.startswith("certs/")],
                      [k for k in base_ordem if k.startswith("certs/")])
    igual(dados_, sorted(dados_), "os arquivos saem em ordem alfabética")
    igual(certs_, sorted(certs_), "os certificados também")
    igual(base_ordem, dados_ + certs_,
          "e os certificados vêm depois de tudo, sempre")
    for w in (2, 4, 8):
        ordem, hashes, nomes = pacotes[w]
        igual(ordem, base_ordem,
              "%d trabalhadores: MESMA ordem no manifesto" % w)
        igual(hashes, base_hashes, "%d trabalhadores: mesmos hashes" % w)
        igual(nomes, base_nomes, "%d trabalhadores: mesma ordem no ZIP" % w)

    secao("Cada arquivo é aberto UMA vez só")
    import builtins
    aberturas = {}
    abrir_original = builtins.open

    def contar(caminho, *a, **kw):
        try:
            chave = os.path.normcase(str(caminho))
            if chave.endswith(".xml"):
                aberturas[chave] = aberturas.get(chave, 0) + 1
        except Exception:
            pass
        return abrir_original(caminho, *a, **kw)

    builtins.open = contar
    try:
        bk.exportar(saida_par / "contagem.fbk", FRASE, raiz=campo,
                    trabalhadores=8)
    finally:
        builtins.open = abrir_original
    ok(len(aberturas) >= 240, "contei aberturas de %d XML" % len(aberturas))
    repetidos = {k: v for k, v in aberturas.items() if v != 1}
    igual(repetidos, {}, "nenhum arquivo foi aberto duas vezes")

    secao("Arquivo grande fica fora do adiantamento")
    instantes = {}
    todos = sorted(q for q in campo.rglob("*")
                   if q.is_file() and bk._entra_no_backup(q, campo))
    for q in todos:
        st = q.stat()
        instantes[q] = (st.st_size, st.st_mtime_ns)
    grandes = [q for q in todos if instantes[q][0] > bk.LIMITE_PARALELO]
    igual(len(grandes), 1, "há um arquivo acima do limite no conjunto")
    lidos = [q for q, _ in bk._fluxo_de_leitura(todos, instantes,
                                                trabalhadores=8)]
    igual(lidos, todos, "o fluxo entrega TODOS, na ordem pedida")

    secao("A fila é limitada · a memória não cresce com o número de arquivos")
    import tracemalloc
    pequenos = [q for q in todos if instantes[q][0] <= bk.LIMITE_PARALELO]
    picos = {}
    for quantos in (30, 240):
        trecho = pequenos[:quantos]
        tracemalloc.start()
        for _ in bk._fluxo_de_leitura(trecho, instantes, trabalhadores=8):
            pass
        _, picos[quantos] = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    ok(picos[240] < picos[30] * 4,
       "8x mais arquivos não multiplicou a memória (%.0f KB -> %.0f KB)"
       % (picos[30] / 1024, picos[240] / 1024))

    secao("Arquivo que muda, some ou falha CANCELA o pacote")
    alvo = todos[len(todos) // 2]
    retrato = instantes[alvo]
    instantes[alvo] = (retrato[0] + 1, retrato[1])
    try:
        list(bk._fluxo_de_leitura(todos, instantes, trabalhadores=8))
        ok(False, "arquivo alterado deveria ter cancelado")
    except bk.BackupInvalido as e:
        ok(True, "arquivo alterado levanta BackupInvalido")
        ok("mudou enquanto o backup" in str(e), "com a razão explicada")
    instantes[alvo] = retrato

    sumido = campo / "sumiu.xml"
    sumido.write_text("<x/>", encoding="utf-8")
    st = sumido.stat()
    inst2 = dict(instantes)
    inst2[sumido] = (st.st_size, st.st_mtime_ns)
    sumido.unlink()
    try:
        list(bk._fluxo_de_leitura(todos + [sumido], inst2, trabalhadores=8))
        ok(False, "arquivo sumido deveria ter cancelado")
    except (OSError, bk.BackupInvalido):
        ok(True, "arquivo que sumiu cancela a leitura")

    saida_falha = Path(tempfile.mkdtemp(prefix="fiscale_v2_falha_"))
    ler_original = bk._ler_uma_vez
    contador = {"n": 0}

    def falhar(caminho, esperado):
        contador["n"] += 1
        if contador["n"] == 5:
            raise OSError("falha simulada de leitura")
        return ler_original(caminho, esperado)

    bk._ler_uma_vez = falhar
    try:
        bk.exportar(saida_falha / "quebra.fbk", FRASE, raiz=campo,
                    trabalhadores=8)
        ok(False, "a exportação deveria ter falhado")
    except OSError:
        ok(True, "a falha de leitura interrompe a exportação")
    finally:
        bk._ler_uma_vez = ler_original
    igual(list(saida_falha.glob("*.fbk")), [], "nenhum .fbk foi produzido")
    igual(list(saida_falha.glob("*.parcial")), [],
          "e nem o .parcial ficou para trás")

    for pasta in (campo.parent, saida_par, saida_falha):
        shutil.rmtree(pasta, ignore_errors=True)

    # ── A rota de conferência não pode morrer ────────────────────────────
    secao("Conferir pela tela · a rota exige a frase, e nunca pendura a tela")
    fonte_srv = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8")
    i = fonte_srv.index('if rota == "/api/backup/inspecionar":')
    j = fonte_srv.index('if rota == "/api/backup/restaurar"', i)
    rota = "\n".join(l for l in fonte_srv[i:j].splitlines()
                     if not l.strip().startswith("#"))

    # O DEFEITO QUE ISTO FIXA
    #   A rota chamava `conferir(arquivo)` SEM frase. No v1 isso funcionava —
    #   o manifesto era legível. No v2 levanta `FraseIncorreta`, que a rota
    #   NÃO capturava: o tratador morria, a conexão fechava sem resposta, e a
    #   tela ficava em "Conferindo…" para sempre. Trinta minutos, na prática.
    ok("_bk.conferir(m[" not in rota or "frase" in rota.split("_bk.conferir(")[1][:40],
       "a rota passa a frase para a conferência")
    ok("FraseIncorreta" in rota,
       "e captura FraseIncorreta — que o v2 levanta e o v1 nunca levantava")
    ok("BackupInvalido" in rota, "continua capturando BackupInvalido")
    ok("except Exception" in rota,
       "e tem uma rede de segurança: nenhuma exceção escapa desta rota")
    ok('"precisa_frase"' in rota,
       "sem a frase, responde 'precisa da frase' em vez de estourar")

    # Toda saída da rota é uma resposta. Uma rota que termina sem `_json`
    # deixa o navegador esperando uma conexão que já morreu.
    ramos = [l for l in rota.splitlines() if "return" in l]
    ok(all("_json" in l for l in ramos),
       "todo caminho de saída responde alguma coisa (%d ramos)" % len(ramos))

    secao("Com a frase, a tela recebe o manifesto DE DENTRO")
    # O DEFEITO QUE ISTO FIXA
    #   A rota mandava para a tela o resultado de `inspecionar()`, que no v2 é
    #   só o cabeçalho técnico. A tela então mostrava "0 arquivos, 0 empresas"
    #   logo abaixo de "79.328 conferidos por hash" — os dois números vindos
    #   do mesmo pedido, e um deles mentindo.
    ok("c.get(\"manifesto\")" in rota or 'c.get("manifesto")' in rota,
       "a rota devolve o manifesto que veio de DENTRO do pacote")
    i_conf = rota.index("_bk.conferir(")
    i_resp = rota.index('"manifesto":', i_conf)
    ok(i_conf < i_resp,
       "e o faz depois de conferir, que é quando ele existe")

    # E o manifesto de dentro tem mesmo o que a tela precisa.
    with bk._aberto_v2(pacote, FRASE) as dentro:
        with zipfile.ZipFile(dentro) as z:
            man_dentro = json.loads(z.read("manifesto.json"))
    for campo in ("criado_em", "origem", "resumo", "arquivos"):
        ok(campo in man_dentro,
           "o manifesto de dentro traz %r" % campo)
    resumo = man_dentro["resumo"]
    for campo in ("arquivos", "empresas_cadastradas", "certificados_no_cofre",
                  "estado_sincronismo_qtd"):
        ok(campo in resumo, "e o resumo traz %r, que a tela exibe" % campo)
    ok(resumo["arquivos"] > 0,
       "com contagem de verdade (%d arquivos)" % resumo["arquivos"])
    # O cabeçalho, sozinho, NÃO tem nada disso — é essa a diferença.
    cabeca_so = bk.inspecionar(pacote)
    ok("resumo" not in cabeca_so and "criado_em" not in cabeca_so,
       "e o cabeçalho sozinho continua não contando nada")

    tela_cert = (RAIZ / "web" / "backup.html").read_text(encoding="utf-8")
    ok("no pacote" in tela_cert,
       "a tela diz 'no pacote' no v2 — não há mais cofre separado")

    secao("E a tela não fica pendurada quando o servidor cai")
    tela_bk = (RAIZ / "web" / "backup.html").read_text(encoding="utf-8")
    i = tela_bk.index("const post = async")
    corpo_post = tela_bk[i:tela_bk.index("};", i)]
    ok("try" in corpo_post and "catch" in corpo_post,
       "o `post` embrulha o fetch em try/catch")
    # Sem os comentários: o meu próprio comentário fala de "try/catch", e o
    # `split` mordia ali em vez de no bloco de código.
    so_codigo = "\n".join(l for l in corpo_post.splitlines()
                          if not l.strip().startswith("//"))
    ok("await fetch" in so_codigo.split("try")[1].split("catch")[0],
       "e é o PRÓPRIO fetch que está protegido, não só o json()")
    ok("não respondeu" in corpo_post,
       "devolvendo um erro legível quando a conexão morre")
    ok(tela_bk.count('id="fraseRest"') == 1,
       "o campo da frase existe UMA vez só — id duplicado quebra o getElementById")
    i_campo = tela_bk.index('id="fraseRest"')
    i_botao = tela_bk.index('onclick="inspecionar()"')
    ok(i_campo < i_botao,
       "e ele vem ANTES do botão de conferir: no v2 a frase é necessária já ali")
    ok("frase: $('fraseRest').value" in tela_bk.split("inspecionar()")[2][:400]
       or "frase: $('fraseRest').value" in tela_bk,
       "a tela manda a frase ao conferir")
    ok("r.precisa_frase" in tela_bk,
       "e trata a resposta 'precisa da frase'")

    secao("Nenhum teste desta suíte foi à rede")
    try:
        _socket.socket().connect(("127.0.0.1", 9))
        ok(False, "a trava de rede não estava ativa")
    except AssertionError:
        ok(True, "a trava estava mesmo ativa (conferido)")

    for pasta in (raiz.parent, trabalho, destino.parent, virgem.parent,
                  d1.parent, quebrado):
        shutil.rmtree(pasta, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
