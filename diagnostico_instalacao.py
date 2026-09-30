#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnostico_instalacao.py — "esta instalação está sã?", respondido em português.

    python diagnostico_instalacao.py            texto, no terminal
    python diagnostico_instalacao.py --json     para a tela do Fiscale

CADA VERIFICAÇÃO DEVOLVE TRÊS COISAS
    situação  OK · ATENÇÃO · ERRO
    o que é   uma frase que um funcionário entende
    o que fazer   só quando há algo a fazer

REGRA QUE VALE PARA O ARQUIVO INTEIRO
    Nenhum traceback chega ao usuário. Verificação que quebra vira ATENÇÃO com
    o motivo em uma linha — um diagnóstico que estoura é pior do que não ter
    diagnóstico, porque assusta e não informa.

    E nenhum segredo é impresso. Senha, blob de DPAPI e frase-senha aparecem no
    máximo como "presente" ou "ausente".
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ_APP))
sys.path.insert(0, str(RAIZ_APP / "nfse" / "backend"))

VERSAO_FISCALE = "PORT 3 — 11/08/2026"

OK, ATENCAO, ERRO = "OK", "ATENÇÃO", "ERRO"
_ORDEM = {OK: 0, ATENCAO: 1, ERRO: 2}

# Pacotes que o Fiscale realmente importa (levantado por AST do próprio código).
PACOTES = ["fastapi", "uvicorn", "pydantic", "requests", "requests_pkcs12",
           "cryptography", "fpdf", "qrcode", "PIL", "pystray"]

# Arquivos sem os quais o sistema não abre.
OBRIGATORIOS = [
    "fiscale_server.py", "fiscale_dados.py", "fiscale_migracao.py",
    "fiscale_segredos.py", "fiscale_inscricoes.py",
    "fiscale_backup.py", "fiscale_sessoes.py",
    "nfse/runner.py", "nfse/backend/main.py", "nfse/backend/core.py",
    "nfse/backend/seguranca.py", "nfse/backend/municipios.json",
    "web/home_portal.html", "web/login.html", "web/backup.html",
    "web/fiscale.js",
]


class Diagnostico:
    def __init__(self):
        self.itens: list[dict] = []

    def add(self, grupo, titulo, situacao, detalhe, acao=""):
        self.itens.append({"grupo": grupo, "titulo": titulo, "situacao": situacao,
                           "detalhe": detalhe, "acao": acao})

    def seguro(self, grupo, titulo, funcao):
        """Roda uma verificação sem deixar exceção vazar para o usuário."""
        try:
            funcao()
        except Exception as e:
            self.add(grupo, titulo, ATENCAO,
                     f"Não consegui verificar ({e.__class__.__name__}).",
                     "Isto não impede o Fiscale de funcionar. Se persistir, avise o suporte.")

    @property
    def geral(self):
        if any(i["situacao"] == ERRO for i in self.itens):
            return ERRO
        if any(i["situacao"] == ATENCAO for i in self.itens):
            return ATENCAO
        return OK

    def contagem(self):
        c = {OK: 0, ATENCAO: 0, ERRO: 0}
        for i in self.itens:
            c[i["situacao"]] += 1
        return c


# ══════════════════════════════════════════════════════════════════════════
def executar(raiz_dados: str | Path | None = None) -> dict:
    d = Diagnostico()
    import fiscale_dados as fd
    if raiz_dados:
        fd._raiz_cache = None
        raiz = fd.raiz(raiz_dados)
    else:
        raiz = fd.raiz()

    _sistema(d, raiz, fd)
    _pastas(d, raiz)
    empresas, certs = _empresas_certificados(d, raiz)
    _bancos(d, raiz)
    _sincronismo(d, raiz)
    _dependencias(d)
    _arquivos_app(d)
    caminhos = _caminhos_presos(d, raiz)
    _segredos_em_claro(d, raiz)
    _backup(d, raiz)
    _rede(d)
    d.seguro("Escritório", "Acesso das outras máquinas", lambda: _escritorio(d))
    _escrita(d, raiz)

    pronto = _pronto_para_migrar(d, raiz, empresas, certs)
    return {
        "versao": VERSAO_FISCALE,
        "em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "raiz": str(raiz),
        "geral": d.geral,
        "contagem": d.contagem(),
        "itens": d.itens,
        "portabilidade": pronto,
        "caminhos_presos": caminhos,
    }


# ── 1-3, 22-23: sistema ─────────────────────────────────────────────────────
def _sistema(d, raiz, fd):
    v = sys.version_info
    if v >= (3, 10):
        d.add("Sistema", "Versão do Python", OK,
              f"Python {v.major}.{v.minor}.{v.micro} — atende o mínimo (3.10).")
    else:
        d.add("Sistema", "Versão do Python", ERRO,
              f"Python {v.major}.{v.minor} é antigo demais.",
              "Instale o Python 3.10 ou mais novo em python.org.")

    arq = platform.machine()
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    d.add("Sistema", "Arquitetura", OK if bits == 64 else ATENCAO,
          f"Windows {arq} · Python de {bits} bits.",
          "" if bits == 64 else "O Fiscale foi testado em 64 bits.")

    d.add("Sistema", "Versão do Fiscale", OK, VERSAO_FISCALE)
    d.add("Sistema", "Pasta de dados (raiz única)", OK,
          f"{raiz}",
          "" if fd.raiz_e_padrao() else
          "Está fora do padrão porque a variável FISCALE_DADOS aponta para cá.")


# ── 4-5, 20-21, 31: pastas, permissão, espaço ───────────────────────────────
def _pastas(d, raiz):
    if not raiz.exists():
        d.add("Pastas", "Pasta de dados", ERRO, f"{raiz} não existe.",
              "Abra o Fiscale uma vez: ele cria a pasta sozinho.")
        return
    try:
        teste = raiz / f".teste_escrita_{os.getpid()}"
        teste.write_text("ok", encoding="utf-8")
        teste.unlink()
        d.add("Pastas", "Permissão de escrita", OK, "Consigo gravar na pasta de dados.")
    except Exception:
        d.add("Pastas", "Permissão de escrita", ERRO,
              f"Não consigo gravar em {raiz}.",
              "O Fiscale não vai conseguir salvar nada. Verifique as permissões "
              "da pasta ou o antivírus.")

    try:
        with tempfile.NamedTemporaryFile(prefix="fiscale_", delete=True) as f:
            f.write(b"ok")
        d.add("Pastas", "Arquivo temporário", OK, "Consigo criar arquivo temporário.")
    except Exception:
        d.add("Pastas", "Arquivo temporário", ERRO,
              "Não consigo criar arquivo temporário nesta máquina.",
              "Exportar backup e gerar PDF vão falhar. Verifique a pasta TEMP.")

    try:
        livre = shutil.disk_usage(raiz).free / (1024 ** 3)
        usado = sum(p.stat().st_size for p in raiz.rglob("*") if p.is_file()) / (1024 ** 3)
        if livre < 1:
            s, acao = ERRO, "Libere espaço: abaixo de 1 GB o Fiscale pode falhar ao gravar."
        elif livre < 5:
            s, acao = ATENCAO, "Convém liberar espaço antes de baixar mais notas."
        else:
            s, acao = OK, ""
        d.add("Pastas", "Espaço em disco", s,
              f"{livre:.1f} GB livres · os dados do Fiscale ocupam {usado:.2f} GB.", acao)
    except Exception:
        d.add("Pastas", "Espaço em disco", ATENCAO, "Não consegui medir o espaço livre.")

    for sub, oque in (("certs", "certificados"), ("uploads", "arquivos enviados")):
        p = raiz / sub
        d.add("Pastas", f"Pasta de {oque}", OK if p.is_dir() else ATENCAO,
              f"{p}" if p.is_dir() else f"{sub}/ ainda não existe.",
              "" if p.is_dir() else "É criada sozinha no primeiro uso.")

    empresas = [p for p in raiz.iterdir() if p.is_dir() and re.fullmatch(r"\d{11,14}", p.name)]
    com_xml = sum(1 for p in empresas if any(p.rglob("*.xml")))
    d.add("Pastas", "Pastas de XML por empresa", OK if empresas else ATENCAO,
          f"{len(empresas)} pasta(s) de empresa · {com_xml} com XML guardado."
          if empresas else "Nenhuma pasta de empresa ainda.",
          "" if empresas else "Normal numa instalação nova.")


# ── 6-11: empresas e certificados ───────────────────────────────────────────
def _empresas_certificados(d, raiz):
    reg = raiz / "certificados.json"
    if not reg.exists():
        d.add("Empresas", "Empresas cadastradas", ATENCAO,
              "Nenhuma empresa cadastrada ainda.",
              "Cadastre em Clientes, ou restaure um backup.")
        return 0, 0
    try:
        itens = json.loads(reg.read_text("utf-8"))
    except Exception:
        d.add("Empresas", "Cadastro de empresas", ERRO,
              "O arquivo certificados.json não pôde ser lido.",
              "Restaure um backup, ou avise o suporte.")
        return 0, 0

    d.add("Empresas", "Empresas cadastradas", OK, f"{len(itens)} empresa(s) cadastrada(s).")

    import fiscale_dados as fd
    presentes = faltando = 0
    for c in itens:
        cam = c.get("caminho") or ""
        alvo = Path(fd.resolver_cert(cam))
        if cam and alvo.exists():
            presentes += 1
        else:
            faltando += 1
    d.add("Empresas", "Arquivos de certificado", OK if not faltando else ERRO,
          f"{presentes} de {len(itens)} certificado(s) encontrados na pasta.",
          "" if not faltando else
          f"{faltando} não estão no lugar indicado. Reenvie o .pfx dessas empresas em Clientes.")

    try:
        import seguranca
        com_senha = aguardando = 0
        for c in itens:
            sp = c.get("senha_protegida")
            if not sp:
                aguardando += 1
                continue
            try:
                seguranca.desproteger(sp, raiz)
                com_senha += 1
            except Exception:
                aguardando += 1
        d.add("Empresas", "Certificados com senha", OK, f"{com_senha} pronto(s) para usar.")
        d.add("Empresas", "Certificados aguardando senha",
              OK if not aguardando else ATENCAO,
              f"{aguardando} aguardam senha." if aguardando else "Nenhum aguardando senha.",
              "" if not aguardando else
              "Vá em Clientes e informe a senha de cada certificado que for usar. "
              "As empresas continuam cadastradas — não recadastre nada.")

        # a capacidade em si (independente de haver cadastro)
        try:
            ida = seguranca.proteger("teste-de-diagnostico", raiz)
            volta = seguranca.desproteger(ida, raiz)
            d.add("Empresas", "Proteção de senha (Windows)",
                  OK if volta == "teste-de-diagnostico" else ERRO,
                  "O Windows consegue guardar e reabrir senhas nesta conta."
                  if volta == "teste-de-diagnostico" else
                  "A proteção de senha não devolveu o mesmo valor.",
                  "" if volta == "teste-de-diagnostico" else
                  "As senhas salvas podem não funcionar. Avise o suporte.")
        except Exception:
            d.add("Empresas", "Proteção de senha (Windows)", ERRO,
                  "Não consigo usar a proteção de senha do Windows nesta conta.",
                  "As senhas de certificado terão de ser digitadas a cada uso.")
        return len(itens), presentes
    except Exception:
        d.add("Empresas", "Proteção de senha (Windows)", ATENCAO,
              "Não consegui verificar a proteção de senha.")
        return len(itens), presentes


# ── 12-13: bancos ───────────────────────────────────────────────────────────
def _bancos(d, raiz):
    achou = False
    for nome, oque in (("sessoes.db", "quem está logado"),
                       ("elo_sync.db", "fila de envio ao Elo")):
        p = raiz / nome
        if not p.exists():
            continue
        achou = True
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            r = con.execute("PRAGMA integrity_check").fetchone()[0]
            con.close()
            if r == "ok":
                d.add("Bancos", f"Banco de {oque}", OK,
                      f"{nome} íntegro ({p.stat().st_size // 1024} KB).")
            else:
                d.add("Bancos", f"Banco de {oque}", ATENCAO,
                      f"{nome} apresentou inconsistência.",
                      f"Feche o Fiscale e apague {nome}: ele se refaz sozinho.")
        except Exception:
            d.add("Bancos", f"Banco de {oque}", ATENCAO,
                  f"Não consegui abrir {nome}.",
                  f"Feche o Fiscale e apague {nome}: ele se refaz sozinho.")
    if not achou:
        d.add("Bancos", "Bancos internos", OK,
              "Ainda não existem — são criados no primeiro uso.")


# ── 16-18: estado de sincronismo ────────────────────────────────────────────
def _sincronismo(d, raiz):
    arquivos = sorted(raiz.rglob("estado.json"))
    if not arquivos:
        d.add("Sincronismo", "Estado de sincronismo", ATENCAO,
              "Nenhum arquivo de estado ainda.",
              "Normal numa instalação nova: aparece após a primeira sincronização.")
        return
    nfe, nfse, ruins = [], [], []
    for p in arquivos:
        try:
            v = json.loads(p.read_text("utf-8"))
        except Exception:
            ruins.append(p.name)
            continue
        if v.get("ultNSU") is not None:
            nfe.append(int(v["ultNSU"]))
        if v.get("ultimoNSU") is not None:
            nfse.append(int(v["ultimoNSU"]))
    d.add("Sincronismo", "Arquivos de estado", OK if not ruins else ATENCAO,
          f"{len(arquivos)} arquivo(s) de estado de sincronização.",
          "" if not ruins else f"{len(ruins)} não puderam ser lidos.")
    d.add("Sincronismo", "NF-e (ultNSU)", OK if nfe else ATENCAO,
          f"{len(nfe)} empresa(s) com posição guardada · maior NSU: {max(nfe):,}".replace(",", ".")
          if nfe else "Nenhuma empresa com NF-e sincronizada ainda.",
          "" if nfe else "Normal se você ainda não sincronizou NF-e.")
    d.add("Sincronismo", "NFS-e (ultimoNSU)", OK if nfse else ATENCAO,
          f"{len(nfse)} empresa(s) com posição guardada." if nfse
          else "Nenhuma empresa com NFS-e sincronizada ainda.",
          "" if nfse else "Normal se você ainda não sincronizou NFS-e.")


def runtime_portatil() -> tuple[bool, str]:
    """O Fiscale carrega o próprio Python, ou depende do que está instalado?

    A pergunta que importa NÃO é "o interpretador está dentro da pasta?" — uma
    `.venv` está, e mesmo assim não é portátil: o `pyvenv.cfg` aponta para a
    instalação base e é DE LÁ que vem a biblioteca padrão. Copiar a pasta para
    outra máquina produz um Python que não abre.

    O teste certo é onde mora a biblioteca padrão (`sys.base_prefix`)."""
    if getattr(sys, "frozen", False):
        return True, "Aplicativo empacotado — não depende de Python instalado."
    base = Path(sys.base_prefix).resolve()
    # A distribuição portátil separa código de runtime, lado a lado:
    #     FISCALE-Portable\ app\ ... \ runtime\ ... \ libs\
    # Então o runtime NÃO fica dentro de `app`, e olhar só ali fazia o
    # diagnóstico dizer "PENDENTE" rodando de dentro do próprio pacote.
    for candidata in (RAIZ_APP, RAIZ_APP.parent):
        try:
            base.relative_to(candidata)
            return True, ("O Python vem de dentro da própria pasta do Fiscale "
                          f"({base.name}\\) — não depende de instalação nenhuma.")
        except ValueError:
            continue
    if sys.prefix != sys.base_prefix:
        return False, (f"Ambiente virtual (.venv) que depende do Python instalado em "
                       f"{base}. A .venv não viaja: a biblioteca padrão fica lá fora.")
    return False, f"Usando o Python instalado no computador ({base})."


# ── 24: dependências ────────────────────────────────────────────────────────
def _dependencias(d):
    faltando = []
    for pacote in PACOTES:
        try:
            __import__(pacote)
        except Exception:
            faltando.append(pacote)
    if faltando:
        d.add("Dependências", "Bibliotecas do Python", ERRO,
              f"Faltam: {', '.join(faltando)}.",
              "Rode  pip install -r requirements.txt  na pasta do Fiscale.")
    else:
        d.add("Dependências", "Bibliotecas do Python", OK,
              f"As {len(PACOTES)} bibliotecas necessárias estão instaladas.")

    portatil, motivo = runtime_portatil()
    d.add("Dependências", "Como o Fiscale está rodando",
          OK if portatil else ATENCAO, motivo,
          "" if portatil else
          "Para levar o Fiscale a outra máquina sem instalar Python, "
          "falta a distribuição portátil (PORT 4).")


# ── 25: arquivos obrigatórios ───────────────────────────────────────────────
def _arquivos_app(d):
    faltando = [f for f in OBRIGATORIOS if not (RAIZ_APP / f).exists()]
    if faltando:
        d.add("Aplicação", "Arquivos do programa", ERRO,
              f"{len(faltando)} arquivo(s) essenciais não foram encontrados: "
              f"{', '.join(faltando[:4])}{'…' if len(faltando) > 4 else ''}",
              "A cópia do Fiscale está incompleta. Refaça a cópia da pasta.")
    else:
        d.add("Aplicação", "Arquivos do programa", OK,
              f"Os {len(OBRIGATORIOS)} arquivos essenciais estão no lugar.")


# ── 26-28: caminhos presos à máquina ────────────────────────────────────────
def _caminhos_presos(d, raiz):
    achados = []
    padrao = re.compile(r"[A-Za-z]:[\\/](?:Users|Usuários)[\\/]([^\\/\"']+)", re.I)
    usuario_atual = os.environ.get("USERNAME", "").lower()

    for p in sorted(raiz.glob("*.json")):
        if p.name in ("dados_versao.json",):
            continue
        try:
            texto = p.read_text("utf-8-sig")
        except Exception:
            continue
        for m in padrao.finditer(texto):
            dono = m.group(1)
            trecho = texto[m.start():m.start() + 120].split('"')[0]
            de_outro = dono.lower() != usuario_atual
            pasta_volatil = bool(re.search(r"Downloads|Desktop|AppData|Temp", trecho, re.I))
            achados.append({
                "arquivo": p.name, "usuario": dono, "trecho": trecho[:100],
                "de_outro_usuario": de_outro, "pasta_volatil": pasta_volatil,
                "gravidade": ERRO if de_outro else (ATENCAO if pasta_volatil else OK),
            })

    # certificados apontando para fora da raiz controlada — o caso que já doeu
    fora = []
    reg = raiz / "certificados.json"
    if reg.exists():
        try:
            for c in json.loads(reg.read_text("utf-8")):
                cam = c.get("caminho") or ""
                if cam and Path(cam).is_absolute():
                    fora.append({"cnpj": c.get("cnpj"), "nome": c.get("nome"), "caminho": cam})
        except Exception:
            pass

    if fora:
        d.add("Caminhos", "Certificados fora da pasta do Fiscale", ERRO,
              f"{len(fora)} certificado(s) apontam para fora da pasta controlada "
              f"(ex.: {Path(fora[0]['caminho']).parent}).",
              "Reenvie o .pfx dessas empresas em Clientes: o Fiscale passa a "
              "guardar o arquivo dentro da pasta de dados, e aí ele viaja junto no backup.")
    else:
        d.add("Caminhos", "Certificados fora da pasta do Fiscale", OK,
              "Todos os certificados estão dentro da pasta do Fiscale.")

    graves = [a for a in achados if a["gravidade"] == ERRO]
    medios = [a for a in achados if a["gravidade"] == ATENCAO]
    if graves:
        d.add("Caminhos", "Caminhos de outro usuário", ERRO,
              f"{len(graves)} referência(s) a pastas de outro usuário do Windows "
              f"(ex.: {graves[0]['usuario']}).",
              "Vieram de uma máquina anterior. Reconfigure o que apontar para lá.")
    elif medios:
        d.add("Caminhos", "Caminhos presos a este computador", ATENCAO,
              f"{len(medios)} referência(s) a Downloads/Desktop/AppData.",
              "Funcionam aqui, mas não em outro computador. O Fiscale já converte "
              "a pasta vigiada sozinho; o resto convém revisar.")
    else:
        d.add("Caminhos", "Caminhos presos a este computador", OK,
              "Nenhum caminho fixo problemático nos dados.")
    return {"total": len(achados), "graves": len(graves), "medios": len(medios),
            "certificados_fora": fora, "ocorrencias": achados[:40]}


# ── 29: segredo em texto claro ──────────────────────────────────────────────
def _segredos_em_claro(d, raiz):
    import fiscale_segredos as fs
    suspeitos = []
    for p in sorted(raiz.glob("*.json")):
        try:
            dados = json.loads(p.read_text("utf-8-sig"))
        except Exception:
            continue

        def anda(o, caminho=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield from anda(v, f"{caminho}.{k}" if caminho else str(k))
            elif isinstance(o, list):
                for i, v in enumerate(o[:200]):
                    yield from anda(v, f"{caminho}[{i}]")
            else:
                yield caminho, o

        for campo, valor in anda(dados):
            nome = campo.split(".")[-1].split("[")[0]
            if not fs.e_segredo(nome) or not isinstance(valor, str) or not valor:
                continue
            if valor.startswith(("dpapi:", "fernet:")):
                continue
            if "protegida" in campo or "protegido" in campo:
                continue
            if nome.lower() in ("hash", "sal"):        # login: hash, não é reversível
                continue
            suspeitos.append({"arquivo": p.name, "campo": campo})   # o VALOR nunca sai daqui

    if suspeitos:
        d.add("Segurança", "Segredo em texto claro", ERRO,
              f"{len(suspeitos)} campo(s) de senha estão legíveis em disco "
              f"(ex.: {suspeitos[0]['arquivo']} · {suspeitos[0]['campo']}).",
              "Feche e abra o Fiscale: ele protege automaticamente no arranque. "
              "Se continuar, avise o suporte.")
    else:
        d.add("Segurança", "Segredo em texto claro", OK,
              "Nenhuma senha legível nos arquivos de configuração.")

    for nome in ("elo_chave_ed25519.json", "elo_integracao_ed25519.json"):
        if (raiz / nome).exists():
            d.add("Segurança", "Chave privada do Elo", OK,
                  "Existe e fica só nesta máquina (não entra em backup).")
            break


# ── 30: mecanismo de backup ─────────────────────────────────────────────────
def _backup(d, raiz):
    try:
        import fiscale_backup as bk
    except Exception:
        d.add("Backup", "Mecanismo de backup", ERRO,
              "O módulo de backup não pôde ser carregado.",
              "A cópia do Fiscale está incompleta.")
        return
    d.add("Backup", "Mecanismo de backup", OK,
          "Backup oficial disponível em Home > Backup (arquivo .fbk).")

    lugares = [Path.home() / "Desktop", Path.home() / "Documents", raiz.parent]
    achados = []
    for lugar in lugares:
        try:
            achados += list(lugar.glob("FISCALE-backup-*.fbk"))
        except Exception:
            pass
    if not achados:
        d.add("Backup", "Backup já criado", ATENCAO,
              "Não encontrei nenhum arquivo .fbk nas pastas usuais.",
              "Crie um em Home > Backup > Criar backup completo.")
        return
    recente = max(achados, key=lambda p: p.stat().st_mtime)
    dias = (datetime.now() - datetime.fromtimestamp(recente.stat().st_mtime)).days
    situacao = OK if dias <= 30 else ATENCAO
    d.add("Backup", "Backup já criado", situacao,
          f"O mais recente é de {dias} dia(s) atrás: {recente.name} "
          f"({recente.stat().st_size / 1048576:.0f} MB).",
          "" if dias <= 30 else "Convém criar um backup novo.")


# ── 22-23, 32: rede e servidor ──────────────────────────────────────────────
def _rede(d):
    porta = int(os.environ.get("FISCALE_PORT") or os.environ.get("PORT") or 8777)
    livre = True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        livre = s.connect_ex(("127.0.0.1", porta)) != 0
    if livre:
        d.add("Rede", f"Porta {porta}", OK, "Está livre para o Fiscale usar.")
    else:
        d.add("Rede", f"Porta {porta}", OK,
              "Está em uso — provavelmente pelo próprio Fiscale, que está aberto agora.",
              "Se o Fiscale não abrir, algum outro programa pode estar usando esta porta.")

    interna = None
    for p in range(8790, 8831):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                interna = p
                break
    d.add("Rede", "Porta interna do módulo NFS-e", OK if interna else ERRO,
          f"Há porta livre na faixa 8790–8830 (ex.: {interna})." if interna
          else "Nenhuma porta livre entre 8790 e 8830.",
          "" if interna else "O módulo NFS-e não vai subir. Feche outros programas.")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
        d.add("Rede", "Servidor local", OK, "Consigo abrir um servidor nesta máquina.")
    except Exception:
        d.add("Rede", "Servidor local", ERRO,
              "Não consigo abrir servidor local — firewall ou antivírus bloqueando.",
              "Libere o Python/Fiscale no firewall do Windows.")


# ── Escritório: as outras pessoas conseguem entrar? ─────────────────────────
#
# O FISCALE NÃO PRECISA DE SERVIDOR — E ISTO PRECISA FICAR DITO
#     "Servidor" aqui nunca foi máquina comprada, nem nuvem, nem mensalidade.
#     É o PC onde o Fiscale está instalado. Num escritório de uma pessoa só,
#     instalar e abrir é tudo: nada nesta seção importa, e ela diz isso com
#     todas as letras em vez de encher a tela de ATENÇÃO sem motivo.
#
#     O que muda quando o escritório tem várias pessoas é só isto: as outras
#     máquinas abrem o navegador no endereço desta. Elas não instalam nada,
#     não copiam pasta e não sincronizam — existe um Fiscale só, e todo mundo
#     trabalha dentro dele.
#
# POR QUE PELO NOME, E NUNCA PELO IP
#     O IP vem do roteador por DHCP e troca sozinho. O nome não.

def endereco_do_escritorio() -> dict:
    """O endereço que as outras máquinas usam. Barato: só nome e porta."""
    porta = int(os.environ.get("FISCALE_PORT") or os.environ.get("PORT") or 8777)
    try:
        maquina = socket.gethostname()
    except Exception:
        maquina = ""
    ips = []
    try:
        for info in socket.getaddrinfo(maquina, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return {
        "maquina": maquina,
        "porta": porta,
        "url": f"http://{maquina}:{porta}" if maquina else "",
        "ips": ips,
        "windows": platform.system() == "Windows",
    }


def _powershell(comando: str, segundos: int = 8) -> str:
    """Roda uma consulta do PowerShell. Devolve "" em qualquer falha.

    Só leitura, e com prazo: um diagnóstico que trava é pior que um que não
    responde. Fora do Windows nem tenta.
    """
    if platform.system() != "Windows":
        return ""
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
            capture_output=True, text=True, timeout=segundos,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _escritorio(d):
    e = endereco_do_escritorio()

    d.add("Escritório", "Endereço para as outras máquinas", OK,
          f"{e['url'] or '(não consegui ler o nome desta máquina)'} — "
          "as outras abrem isso no navegador, sem instalar nada.",
          "Use o NOME, nunca o número de IP: o IP muda sozinho, o nome não.")

    if not e["windows"]:
        return

    # Perfil de rede. É o que mais derruba o acesso, e o menos óbvio: no perfil
    # Público o Windows bloqueia a descoberta e as outras máquinas não acham
    # esta. Fica como ATENÇÃO, nunca ERRO — quem usa sozinho não tem problema
    # nenhum, e chamar isso de erro seria mentira.
    perfis = _powershell(
        "(Get-NetConnectionProfile | ForEach-Object { \"$($_.Name)=$($_.NetworkCategory)\" }) -join ';'")
    if perfis:
        publicas = [p for p in perfis.split(";") if p.endswith("=Public")]
        if publicas:
            nomes = ", ".join(p.rsplit("=", 1)[0] for p in publicas)
            d.add("Escritório", "Perfil da rede", ATENCAO,
                  f"A rede {nomes} está como Pública, e nela o Windows bloqueia "
                  "o acesso vindo de outras máquinas.",
                  "Só importa se mais alguém for usar o Fiscale. Nesse caso: "
                  "Configurações > Rede e Internet > propriedades da rede > Rede privada "
                  "(ou rode o rede_configurar.bat como administrador).")
        else:
            d.add("Escritório", "Perfil da rede", OK,
                  "A rede está como Privada — é o que permite as outras máquinas entrarem.")

    # Regra de firewall nomeada.
    regra = _powershell(
        f"(Get-NetFirewallRule -DisplayName 'FISCALE {e['porta']}' "
        "-ErrorAction SilentlyContinue | Measure-Object).Count")
    if regra.isdigit():
        if int(regra) > 0:
            d.add("Escritório", "Firewall", OK,
                  f"Existe a regra 'FISCALE {e['porta']}', liberando o acesso da rede local.")
        else:
            d.add("Escritório", "Firewall", ATENCAO,
                  f"Não existe regra de firewall para a porta {e['porta']}.",
                  "Só importa se mais alguém for usar o Fiscale. Nesse caso, rode o "
                  "rede_configurar.bat como administrador — ele cria a regra e "
                  "ainda tira o Fiscale das redes públicas.")


def _escrita(d, raiz):
    try:
        alvo = raiz / f".diag_{os.getpid()}.tmp"
        alvo.write_bytes(b"x" * 1024)
        ok = alvo.stat().st_size == 1024
        alvo.unlink()
        d.add("Pastas", "Gravação de arquivo", OK if ok else ERRO,
              "Consigo gravar arquivos na pasta de dados." if ok
              else "A gravação não saiu do tamanho esperado.")
    except Exception:
        d.add("Pastas", "Gravação de arquivo", ERRO,
              "Não consigo gravar arquivos na pasta de dados.",
              "Verifique permissões e antivírus.")


# ── PARTE 4: pronto para outro computador? ──────────────────────────────────
def _pronto_para_migrar(d, raiz, empresas, certs) -> dict:
    linhas = []

    def linha(nome, situacao, detalhe):
        linhas.append({"item": nome, "situacao": situacao, "detalhe": detalhe})

    import fiscale_dados as fd
    import fiscale_migracao as fm
    linha("Dados centralizados",
          OK if fm.versao(raiz) >= 3 else ATENCAO,
          "Tudo numa pasta só." if fm.versao(raiz) >= 3
          else "A pasta de dados ainda não passou pela atualização de formato.")

    achados = list((Path.home() / "Desktop").glob("FISCALE-backup-*.fbk"))
    linha("Backup oficial disponível", OK if achados else ATENCAO,
          f"{len(achados)} arquivo(s) .fbk encontrados." if achados
          else "Nenhum .fbk criado ainda.")

    estados = list(raiz.rglob("estado.json"))
    linha("Estados NF-e/NFS-e", OK if estados else ATENCAO,
          f"{len(estados)} arquivo(s) de posição de sincronismo — vão no backup."
          if estados else "Ainda não há estado de sincronismo.")

    fora = 0
    reg = raiz / "certificados.json"
    if reg.exists():
        try:
            fora = sum(1 for c in json.loads(reg.read_text("utf-8"))
                       if (c.get("caminho") or "") and Path(c["caminho"]).is_absolute())
        except Exception:
            pass
    linha("Certificados controlados", OK if not fora else ERRO,
          "Todos dentro da pasta do Fiscale." if not fora
          else f"{fora} apontam para fora e não viajariam no backup.")

    claro = [i for i in d.itens
             if i["titulo"] in ("Segredo em texto claro", "Senha do portal")
             and i["situacao"] == ERRO]
    linha("Segredos locais", OK if not claro else ERRO,
          "Nenhuma senha legível em disco." if not claro
          else "Há senha em texto claro — corrija antes de migrar.")

    dep = next((i for i in d.itens if i["titulo"] == "Bibliotecas do Python"), None)
    linha("Dependências", dep["situacao"] if dep else ATENCAO,
          dep["detalhe"] if dep else "Não verificado.")

    portatil, motivo = runtime_portatil()
    linha("Runtime portátil", OK if portatil else "PENDENTE — PORT 4", motivo)

    tem_erro = any(l["situacao"] == ERRO for l in linhas)
    pendente = any(str(l["situacao"]).startswith("PENDENTE") for l in linhas)
    if tem_erro:
        veredito = ("Ainda NÃO dá para migrar com segurança. Resolva os itens em "
                    "ERRO acima e rode o diagnóstico de novo.")
        situacao = ERRO
    elif pendente:
        veredito = ("Os seus DADOS já estão prontos para migrar: crie o backup e "
                    "leve o arquivo .fbk. Mas esta instalação ainda não é portátil "
                    "sozinha — na outra máquina será preciso instalar o Python. "
                    "A PORT 4 vai embutir o runtime e resolver isso.")
        situacao = ATENCAO
    else:
        veredito = "Esta instalação está pronta para ir para outro computador."
        situacao = OK
    return {"situacao": situacao, "veredito": veredito, "linhas": linhas}


# ══════════════════════════════════════════════════════════════════════════
def _texto(r: dict) -> str:
    largura = 68
    out = ["=" * largura,
           "  FISCALE — DIAGNÓSTICO DA INSTALAÇÃO",
           "=" * largura,
           f"  {r['versao']}     {r['em']}",
           f"  Dados em: {r['raiz']}",
           ""]
    grupo = None
    for i in r["itens"]:
        if i["grupo"] != grupo:
            grupo = i["grupo"]
            out.append(f"  {grupo.upper()}")
        marca = {OK: "[ OK ]", ATENCAO: "[ ! ]", ERRO: "[ERRO]"}[i["situacao"]]
        out.append(f"    {marca}  {i['titulo']}: {i['detalhe']}")
        if i["acao"]:
            for linha in _quebrar(i["acao"], largura - 14):
                out.append(f"            -> {linha}")
    c = r["contagem"]
    out += ["", "-" * largura,
            f"  RESULTADO GERAL: {r['geral']}"
            f"    ({c[OK]} ok · {c[ATENCAO]} atenção · {c[ERRO]} erro)",
            "-" * largura, "",
            "  PRONTO PARA OUTRO COMPUTADOR?", ""]
    for l in r["portabilidade"]["linhas"]:
        pontos = "." * max(2, 34 - len(l["item"]))
        out.append(f"    {l['item']} {pontos} {l['situacao']}")
    out.append("")
    for linha in _quebrar(r["portabilidade"]["veredito"], largura - 6):
        out.append(f"    {linha}")
    out += ["", "=" * largura]
    return "\n".join(out)


def _quebrar(texto, largura):
    linhas, atual = [], ""
    for palavra in str(texto).split():
        if len(atual) + len(palavra) + 1 > largura:
            linhas.append(atual)
            atual = palavra
        else:
            atual = f"{atual} {palavra}".strip()
    if atual:
        linhas.append(atual)
    return linhas or [""]


def main(argv):
    raiz = next((a for a in argv if not a.startswith("--")), None)
    try:
        r = executar(raiz)
    except Exception as e:
        print(f"  Não consegui rodar o diagnóstico: {e.__class__.__name__}: {e}")
        return 2
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print(_texto(r))
    return {OK: 0, ATENCAO: 0, ERRO: 1}[r["geral"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
