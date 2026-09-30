"""
main.py — API local (FastAPI) do Sistema NFS-e.

Serve a interface web e expõe as rotas para:
  - cadastrar/listar/remover certificados (caminho + nome + CNPJ; SEM senha);
  - sincronizar os DFe de um certificado (informando a senha na hora);
  - listar competências e notas válidas filtradas;
  - exportar os XMLs + um resumo CSV de uma competência.

Tudo roda localmente. As senhas nunca são gravadas em disco.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
import auditor_nfe as auditormod
import classificador as clsmod
import inscricoes as inscricoesmod
import prefeituras as prefmod
import core
import nfe as nfemod
import pdflocal
import recife as recifemod
import seguranca

# ─────────────────────────────────────────────────────────────────────────────
# Caminhos
# ─────────────────────────────────────────────────────────────────────────────
# Recursos empacotados (frontend): _MEIPASS quando rodando como .exe (PyInstaller).
_RES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
FRONTEND = _RES / "frontend" / "index.html"

# Pasta de dados — RAIZ ÚNICA, decidida por `fiscale_dados.raiz()`.
# Antes da PORT 1 este módulo escolhia sozinho (~/SistemaNFSe/dados quando
# "frozen") e ignorava FISCALE_DADOS, o que partia o sistema em duas metades.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import fiscale_cadastro as fcad
import fiscale_dados as fdados
# A porta única para o NFeDistribuicaoDFe. A rota de sincronização NÃO fala com
# a SEFAZ por conta própria: toda a política mora no serviço.
from ingestao import cadastro as _cad_ingestao
from ingestao import importacao as _importacao
from ingestao import servico_distribuicao as _svc_nfe
import fiscale_migracao as fmigra
from ingestao import consulta as _cq          # a leitura oficial (NF-e 5)
from ingestao import documento as _dm
from ingestao import emitidas as _emit        # a leitura da emissão própria
from ingestao import exportacao as _exp      # o lote (NF-e 6)
from ingestao import indice as _ix           # só para contar documentos
from ingestao import checkpoint as _cpm       # CT-e: checkpoint proprio
from ingestao import credencial_cte as _cred_cte
from ingestao import servico_cte as _svc_cte
from ingestao import distribuicao as _dist    # NSU: formato de 15 digitos
import ingestao as _ing                       # PRODUCAO/HOMOLOGACAO
import empresas_nfe as _emp                  # quem entra no seletor da NF-e
import fiscal_atualizacoes as _fatu          # a Central de Atualizações
import dataclasses as _dataclasses           # só para ajustar a política

DADOS_DIR = fdados.raiz()
fmigra.garantir(DADOS_DIR)          # idempotente; devolve rápido se já está no formato
REGISTRO = DADOS_DIR / "certificados.json"


def _ler_registro() -> list[dict]:
    """Cadastro dos certificados, com o caminho do .pfx já ABSOLUTO.

    No disco o caminho é relativo ("certs/EMPRESA.pfx") para o arquivo poder
    viajar de máquina; na memória ele é absoluto porque é isso que os 14 pontos
    que usam `cert["caminho"]` esperam. A conversão fica aqui, num lugar só."""
    if not REGISTRO.exists():
        return []
    try:
        itens = json.loads(REGISTRO.read_text("utf-8"))
    except Exception:
        return []
    if not isinstance(itens, list):
        return []
    for c in itens:
        if c.get("caminho"):
            c["caminho"] = fdados.resolver_cert(c["caminho"])
    return itens


def _salvar_registro(itens: list[dict]) -> None:
    """Grava relativizando de volta o que estiver dentro da pasta de dados."""
    saida = []
    for c in itens:
        c = dict(c)
        if c.get("caminho"):
            c["caminho"] = fdados.relativizar_cert(c["caminho"])
        saida.append(c)
    tmp = REGISTRO.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(REGISTRO)            # troca atômica: nunca um cadastro pela metade


def _cert_por_id(cid: str) -> dict:
    for c in _ler_registro():
        if c["id"] == cid:
            return c
    raise HTTPException(status_code=404, detail="Certificado não encontrado.")


def _conta_por_id(id: str) -> dict:
    """Resolve a conta (certificado) e devolve {"id", "cnpj", "nome"} para os
    endpoints de leitura/exportação (que só precisam do CNPJ e da pasta)."""
    for c in _ler_registro():
        if c["id"] == id:
            return {"id": c["id"], "cnpj": c["cnpj"], "nome": c.get("apelido") or c["nome"]}
    raise HTTPException(status_code=404, detail="Certificado não encontrado.")


def _pasta_cnpj(cnpj: str) -> Path:
    p = DADOS_DIR / cnpj
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Pasta vigiada (caixa de entrada automática) ──────────────────────────────
ENTRADA_CONFIG = DADOS_DIR / "entrada_config.json"


def _entrada_cfg() -> dict:
    """Config da pasta vigiada, com o caminho já resolvido para ESTA máquina.

    O arquivo pode guardar o marcador `%DOWNLOADS%`; assim a mesma configuração
    vale em qualquer computador, em vez de apontar para o perfil de quem
    configurou."""
    padrao = {"pasta": str(DADOS_DIR / "entrada"), "auto": False, "ultimo": None}
    if ENTRADA_CONFIG.exists():
        try:
            padrao = {**padrao, **json.loads(ENTRADA_CONFIG.read_text("utf-8"))}
        except Exception:
            pass
    padrao["pasta"] = fmigra.caminho_entrada(padrao.get("pasta") or "")
    return padrao


def _entrada_salvar(cfg: dict) -> None:
    """Grava guardando `%DOWNLOADS%` quando a pasta escolhida é a Downloads
    do usuário — é o caso mais comum e o que mais viaja mal entre máquinas."""
    try:
        cfg = dict(cfg)
        pasta = str(cfg.get("pasta") or "")
        alvo = str(Path.home() / "Downloads")
        if pasta and pasta.rstrip("\\/").lower() == alvo.rstrip("\\/").lower():
            cfg["pasta"] = "%DOWNLOADS%"
        ENTRADA_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _historico_municipal(pasta_cnpj, cnpj: str, ja_tem: list) -> list:
    """Notas do sistema MUNICIPAL (anteriores à adesão à NFS-e Nacional), no
    formato da tela de notas.

    Existe porque as duas telas divergiam: o Classificador somava o histórico e
    o módulo NFS-e não, então o mesmo mês aparecia com totais diferentes — o
    tipo de coisa que destrói a confiança na ferramenta.

    ANTI-DUPLICIDADE (mesma regra do classificador): o municipal só entra nos
    meses em que a base nacional NÃO tem nenhuma nota EMITIDA pela empresa. No
    mês da virada as duas fontes trazem as mesmas notas com numeração diferente,
    e não há como casar uma a uma.

    Só conta a nota EMITIDA: o histórico municipal é de emissão. Comparar com
    todas as notas faria uma única nota TOMADA no mês bloquear o histórico
    inteiro daquele mês — foi o que aconteceu na Monte, que recebe notas de
    fornecedores desde antes da adesão."""
    try:
        import recife as recifemod
        municipais = recifemod.ler(pasta_cnpj)
    except Exception:
        return []
    if not municipais:
        return []
    cnpj_d = re.sub(r"\D", "", cnpj or "")
    meses_nac = {(n.get("competencia") or "")[:7] for n in ja_tem
                 if re.sub(r"\D", "", n.get("emit_cnpj") or "") == cnpj_d}
    out = []
    for n in municipais:
        comp = (n.get("competencia") or "")[:7]
        if not comp or comp in meses_nac:
            continue
        valor = n.get("valor") or 0.0
        out.append({
            "chave": n.get("chave") or "", "numero": n.get("numero"),
            "competencia": comp,
            "emit_cnpj": cnpj, "emit_nome": "(emitida no sistema municipal)",
            "toma_doc": "", "toma_nome": n.get("tomador") or "",
            "valor_servico": valor, "valor_liquido": n.get("valor_liquido") or valor,
            "data_emissao": (n.get("data") or "") + "T00:00:00",
            "data_competencia": n.get("data") or "",
            "cstat": n.get("cstat") or "100",
            "iss_retido": bool(n.get("iss_retido")),
            "valor_iss_retido": n.get("valor_iss") or 0.0,
            "valor_retido": 0.0, "valor_irrf": 0.0, "valor_inss": 0.0,
            "inss_retido": False, "retido": bool(n.get("iss_retido")),
            "cancelada": bool(n.get("cancelada")),
            "arquivo": n.get("arquivo") or "", "situacao": "Normal",
            "origem_municipal": True,          # a tela marca estas linhas
            "item_lc116": n.get("item_lc116") or "",
        })
    return out


def _cnpjs_e_nomes() -> dict:
    """{cnpj_digitos: nome} de todas as empresas cadastradas."""
    return {re.sub(r"\D", "", c["cnpj"]): (c.get("apelido") or c["nome"])
            for c in _ler_registro()}


def _rodar_pasta_entrada() -> dict:
    """Processa a pasta vigiada agora e guarda o resultado no config."""
    cfg = _entrada_cfg()
    nomes = _cnpjs_e_nomes()
    rel = nfemod.processar_pasta(cfg["pasta"], nomes.keys(), _pasta_cnpj)
    if "por_empresa" in rel:
        rel["empresas"] = [{"cnpj": c, "nome": nomes.get(c, c), "notas": n}
                           for c, n in rel.pop("por_empresa").items()]
        rel["empresas"].sort(key=lambda e: e["nome"])
    cfg["ultimo"] = {"em": datetime.now().isoformat(timespec="seconds"), "resumo": rel}
    _entrada_salvar(cfg)
    return rel


def _parse_comps(texto: str | None) -> list[str]:
    """Normaliza uma entrada digitada de competências para uma lista de "AAAA-MM".

    Aceita "AAAA-MM" ou "MM/AAAA" (também "MM-AAAA"), separadas por vírgula,
    ponto-e-vírgula ou espaço. Ignora o que não reconhecer. Lista vazia = todas.
    """
    if not texto:
        return []
    comps: list[str] = []
    for it in re.split(r"[,;\s]+", texto.strip()):
        it = it.strip().strip("-/")   # tolera separador solto no início/fim
        if not it:
            continue
        m = re.match(r"^(\d{4})-(\d{2})$", it)            # AAAA-MM
        if m:
            comp = f"{m.group(1)}-{m.group(2)}"
        else:
            m = re.match(r"^(\d{1,2})[/-](\d{4})$", it)    # MM/AAAA ou MM-AAAA
            if not m:
                continue
            comp = f"{m.group(2)}-{int(m.group(1)):02d}"
        if comp not in comps:
            comps.append(comp)
    return comps


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Sistema NFS-e")


@app.get("/", response_class=HTMLResponse)
def index():
    # no-store: o navegador sempre busca a página atual (evita ver versão antiga
    # em cache depois de uma atualização do sistema).
    return HTMLResponse(
        FRONTEND.read_text("utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


_ICONE = _RES / "icone.ico"


_SEM_CACHE = {"Cache-Control": "no-store, max-age=0"}


@app.get("/favicon.ico")
def favicon():
    if _ICONE.exists():
        return FileResponse(_ICONE, headers=_SEM_CACHE)
    raise HTTPException(404, "sem ícone")


_LOGO = FRONTEND.parent / "nfse.webp"


@app.get("/logo")
def logo():
    if _LOGO.exists():
        return FileResponse(_LOGO, headers=_SEM_CACHE)
    return FileResponse(_ICONE, headers=_SEM_CACHE)


# ── Certificados ─────────────────────────────────────────────────────────────
class NovoCert(BaseModel):
    caminho: str
    senha: str
    apelido: str | None = None
    salvar_senha: bool = True


def estado_senha(cert: dict) -> tuple[str, str]:
    """Situação da senha deste certificado NESTA máquina.

    A senha é cifrada com DPAPI, que é atrelado à conta do Windows: um cadastro
    restaurado em outro computador traz a empresa inteira, mas não a senha. Em
    vez de sumir com a empresa (o que obrigaria a recadastrar tudo), ela fica
    "aguardando senha" e só as funções que precisam do certificado esperam.

    Devolve (estado, explicação):
        ok         — a senha está salva e abre aqui
        aguardando — falta informar a senha nesta máquina
    """
    sp = cert.get("senha_protegida")
    if not sp:
        return "aguardando", "Informe a senha do certificado nesta máquina."
    try:
        seguranca.desproteger(sp, DADOS_DIR)
        return "ok", ""
    except Exception:
        return ("aguardando",
                "A senha salva foi gravada em outro computador (ou noutra conta "
                "do Windows) e não pode ser lida aqui. Informe-a de novo.")


@app.get("/api/certificados")
def listar_certificados():
    # devolve sem nada sensível (a senha protegida nunca é exposta)
    saida = []
    for c in _ler_registro():
        est, motivo = estado_senha(c)
        saida.append(
        {"id": c["id"], "nome": c["nome"], "cnpj": c["cnpj"],
         "apelido": c.get("apelido"), "caminho": c["caminho"],
         "existe": Path(c["caminho"]).exists(),
         "senha_salva": bool(c.get("senha_protegida")),
         "senha_estado": est, "senha_motivo": motivo,
         # Procurador = certificado que acessa a prefeitura em nome de OUTRAS
         # empresas. Costuma ser o e-CPF do contador, mas também pode ser o
         # e-CNPJ do escritório — por isso a marca é explícita, não deduzida
         # só pelo tamanho do documento.
         "procurador": bool(c.get("procurador")) or len(re.sub(r"\D", "", c.get("cnpj") or "")) == 11})
    # Ordem central: a MESMA de Clientes, NF-e e NFS-e. Antes cada tela
    # ordenava do seu jeito, e a mesma empresa aparecia em posições
    # diferentes conforme onde se olhasse.
    return fcad.ordenar(saida)


def _guardar_pfx(origem: str, cnpj: str) -> str:
    """Copia o .pfx para `<DADOS>/certs/` e devolve o caminho de lá.

    Já está dentro dos dados? Devolve como está — reimportar o mesmo arquivo
    não pode gerar cópia atrás de cópia. Não deu para copiar (disco cheio,
    permissão)? Devolve a origem: um cadastro que só funciona nesta máquina
    ainda é melhor do que um cadastro que não funciona em nenhuma."""
    try:
        if fdados.dentro_dos_dados(origem):
            return origem
        destino = fdados.pasta_certs()
        nome = re.sub(r"[^0-9A-Za-zÀ-ÿ._\- ]", "_", Path(origem).name)[:150] or f"{cnpj}.pfx"
        alvo = destino / nome
        if alvo.exists() and alvo.read_bytes() == Path(origem).read_bytes():
            return str(alvo)             # mesmo arquivo, já guardado
        i = 1
        while alvo.exists():
            alvo = destino / f"{Path(nome).stem}({i}){Path(nome).suffix}"
            i += 1
        shutil.copy2(origem, alvo)
        return str(alvo)
    except Exception:
        return origem


def _cadastrar_um(caminho: str, senha: str, apelido: str | None,
                  salvar_senha: bool, itens: list[dict]) -> dict:
    """Valida e insere/atualiza um certificado em `itens` (lista mutada in-place).

    Não levanta exceção: devolve {"ok": True, ...} em caso de sucesso ou
    {"ok": False, "erro": "..."} em caso de falha — para o cadastro em lote
    conseguir reportar item a item sem abortar os demais.
    """
    caminho = (caminho or "").strip().strip('"')
    if not caminho:
        return {"ok": False, "erro": "Caminho vazio."}
    if not Path(caminho).exists():
        return {"ok": False, "erro": "Arquivo .pfx não encontrado nesse caminho."}
    try:
        # aceita e-CNPJ (empresa) e e-CPF (procurador — o contador com procuração
        # para acessar as notas das empresas na prefeitura)
        cnpj = core.documento_do_certificado(caminho, senha)
        nome = core.nome_do_certificado(caminho, senha) or "(sem nome)"
    except Exception:
        return {"ok": False, "erro": "Senha incorreta ou certificado inválido."}
    if not cnpj:
        return {"ok": False, "erro": "Não foi possível ler o CNPJ/CPF do certificado."}

    # O .pfx passa a viver DENTRO da pasta de dados. Antes o cadastro apontava
    # para onde o usuário tivesse o arquivo (quase sempre Downloads): bastava
    # limpar a pasta para 21 empresas pararem de funcionar — e nada disso
    # sobrevivia a uma troca de computador.
    caminho = _guardar_pfx(caminho, cnpj)

    eh_procurador = len(re.sub(r"\D", "", cnpj)) == 11
    anterior = next((c for c in itens if c["id"] == cnpj), {})
    novo = {"id": cnpj, "cnpj": cnpj, "nome": nome,
            "apelido": apelido or anterior.get("apelido") or None,
            "caminho": caminho,
            "procurador": eh_procurador or bool(anterior.get("procurador"))}
    if salvar_senha:
        try:
            novo["senha_protegida"] = seguranca.proteger(senha, DADOS_DIR)
        except Exception:
            pass  # se falhar a proteção, apenas não salva a senha
    # upsert por id (== cnpj): remove o antigo e adiciona o novo
    itens[:] = [c for c in itens if c["id"] != cnpj] + [novo]
    return {"ok": True, "id": cnpj, "nome": nome, "cnpj": cnpj,
            "apelido": novo["apelido"],
            "senha_salva": bool(novo.get("senha_protegida"))}


@app.post("/api/certificados")
def cadastrar_certificado(req: NovoCert):
    itens = _ler_registro()
    r = _cadastrar_um(req.caminho, req.senha, req.apelido, req.salvar_senha, itens)
    if not r.get("ok"):
        raise HTTPException(400, r["erro"])
    _salvar_registro(itens)
    return {"id": r["id"], "nome": r["nome"], "cnpj": r["cnpj"],
            "apelido": r["apelido"], "senha_salva": r["senha_salva"]}


class NovoCertLote(BaseModel):
    caminhos: list[str]
    senha: str
    salvar_senha: bool = True


@app.post("/api/certificados/lote")
def cadastrar_certificados_lote(req: NovoCertLote):
    """Cadastra vários certificados de uma vez usando a MESMA senha para todos.

    Cada item é processado independentemente; falhas (senha errada, arquivo
    inexistente) voltam com erro sem impedir o cadastro dos demais.
    """
    itens = _ler_registro()
    resultados = []
    for caminho in req.caminhos:
        r = _cadastrar_um(caminho, req.senha, None, req.salvar_senha, itens)
        resultados.append({"caminho": caminho, **r})
    _salvar_registro(itens)
    cadastrados = sum(1 for r in resultados if r.get("ok"))
    return {"cadastrados": cadastrados, "total": len(req.caminhos),
            "resultados": resultados}


class InformarSenha(BaseModel):
    id: str
    senha: str


@app.post("/api/certificados/senha")
def informar_senha(req: InformarSenha):
    """Informa a senha de um certificado JÁ CADASTRADO, sem recadastrar nada.

    É o caminho depois de restaurar um backup em outro computador: a empresa,
    o apelido, o regime e todo o histórico continuam onde estavam; só a senha
    — que é DPAPI e não viaja — precisa ser digitada de novo.

    A senha é conferida abrindo o .pfx de verdade antes de ser salva: senha
    errada aqui viraria uma falha inexplicável na primeira sincronização.
    """
    itens = _ler_registro()
    cert = next((c for c in itens if c["id"] == req.id), None)
    if not cert:
        raise HTTPException(404, "Certificado não encontrado.")
    caminho = cert.get("caminho") or ""
    if not caminho or not Path(caminho).exists():
        raise HTTPException(400,
            "O arquivo .pfx desta empresa não está na pasta de dados. "
            "Use 'Trocar certificado' para enviá-lo de novo.")
    try:
        doc = core.documento_do_certificado(caminho, req.senha)
    except Exception:
        raise HTTPException(400, "Senha incorreta para este certificado.")
    if re.sub(r"\D", "", doc or "") != re.sub(r"\D", "", cert.get("cnpj") or ""):
        raise HTTPException(400,
            f"Este certificado é do documento {doc}, e não de {cert.get('cnpj')}.")
    try:
        cert["senha_protegida"] = seguranca.proteger(req.senha, DADOS_DIR)
    except Exception as e:
        raise HTTPException(500, f"Não consegui proteger a senha nesta máquina: {e}")
    _salvar_registro(itens)
    est, motivo = estado_senha(cert)
    return {"ok": True, "id": cert["id"], "nome": cert["nome"],
            "senha_estado": est, "senha_motivo": motivo}


@app.get("/api/certificados/pendentes")
def certificados_pendentes():
    """Quem está aguardando senha. É o que a tela mostra depois de restaurar."""
    itens = _ler_registro()
    pend = []
    for c in itens:
        est, motivo = estado_senha(c)
        if est != "ok":
            pend.append({"id": c["id"], "cnpj": c["cnpj"], "nome": c["nome"],
                         "apelido": c.get("apelido"), "motivo": motivo,
                         "arquivo_presente": bool(c.get("caminho")) and Path(c["caminho"]).exists()})
    return {"total": len(itens), "aguardando_senha": len(pend), "pendentes": pend}


class MarcarProcurador(BaseModel):
    id: str
    procurador: bool = True


@app.post("/api/certificados/procurador")
def marcar_procurador(req: MarcarProcurador):
    """Marca um certificado como PROCURADOR — o que acessa a prefeitura em nome
    de outras empresas. Pode ser e-CPF do contador ou e-CNPJ do escritório."""
    itens = _ler_registro()
    achou = None
    for c in itens:
        if c["id"] == req.id:
            c["procurador"] = bool(req.procurador)
            achou = c
    if not achou:
        raise HTTPException(404, "Certificado não encontrado.")
    _salvar_registro(itens)
    return {"ok": True, "id": achou["id"], "nome": achou["nome"],
            "procurador": achou["procurador"]}


@app.delete("/api/certificados/{cid}")
def remover_certificado(cid: str):
    itens = [c for c in _ler_registro() if c["id"] != cid]
    _salvar_registro(itens)
    return {"ok": True}


# ── Escolher uma pasta, sem diálogo nativo ──────────────────────────────────
# Até a PORT 4 isto abria uma janela do `tkinter`. Saiu por dois motivos:
#
#   1. O `tkinter` não vem no Python embutido da distribuição portátil, e
#      carregar o Tcl/Tk junto custaria ~10 MB e um monte de arquivos para
#      servir a UM botão.
#   2. E ele já estava errado: a janela abria na máquina do SERVIDOR. Quem
#      acessasse o Fiscale pela rede clicava em "Escolher pasta" e não
#      acontecia nada na tela dele — a janela tinha aberto no outro computador.
#
# No lugar, o servidor apenas LISTA pastas e a tela navega. Funciona igual na
# máquina local e pela rede, e não depende de biblioteca gráfica nenhuma.
# ── Onde ficam, de verdade, as pastas do usuário ────────────────────────────
# A implementação mora em `pastas_windows.py`, na raiz do projeto, porque o
# `fiscale_server.py` precisa da MESMA resposta para decidir onde gravar o
# backup `.fbk`. Duas cópias desta regra já custaram caro uma vez: o seletor
# levava a pessoa para a pasta errada, e os backups iam para uma Área de
# Trabalho que ela não via.
import pastas_windows as _pw

# Reexportado com os nomes que a rota e os testes já usam.
_PASTAS_CONHECIDAS = _pw.PASTAS
_CHAVE_SHELL = _pw.CHAVE_SHELL
pastas_conhecidas = _pw.conhecidas


@app.get("/api/pastas")
def listar_pastas(caminho: str | None = None):
    """Subpastas de um caminho, para o seletor da tela.

    Sem `caminho`, devolve os pontos de partida: as unidades do computador e as
    pastas conhecidas do usuário. Só lista diretórios — nunca conteúdo de
    arquivo — e falha silenciosamente no que não puder ler (unidade de rede
    fora do ar, pasta sem permissão), em vez de derrubar a tela."""
    if not caminho:
        raizes = []
        for nome, p in pastas_conhecidas():
            if p.is_dir():
                raizes.append({"nome": nome, "caminho": str(p)})
        if os.name == "nt":
            import string
            for letra in string.ascii_uppercase:
                unidade = f"{letra}:\\"
                if os.path.isdir(unidade):
                    raizes.append({"nome": f"Disco {letra}:", "caminho": unidade})
        else:
            raizes.append({"nome": "Raiz", "caminho": "/"})
        return {"atual": None, "pai": None, "raizes": raizes, "pastas": []}

    p = Path(caminho).expanduser()
    if not p.is_dir():
        raise HTTPException(400, "Esse caminho não é uma pasta que eu consiga abrir.")
    filhas = []
    try:
        for f in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if not f.is_dir() or f.name.startswith("."):
                continue
            try:
                f.iterdir()          # dá para entrar? senão não oferece
            except Exception:
                continue
            filhas.append({"nome": f.name, "caminho": str(f)})
    except PermissionError:
        raise HTTPException(403, "Não tenho permissão para ler essa pasta.")
    except Exception:
        pass
    pai = str(p.parent) if p.parent != p else None
    return {"atual": str(p), "pai": pai, "raizes": [], "pastas": filhas}


# ── Sincronização e consulta ─────────────────────────────────────────────────
class Senha(BaseModel):
    id: str
    senha: str | None = None
    ambiente: str = "producao"


def _resolver_senha(cert: dict, senha_informada: str | None) -> str:
    """Usa a senha digitada; se vazia, tenta a senha salva do certificado.

    Quando a senha salva não abre nesta máquina (cadastro restaurado de outro
    computador), a mensagem diz isso com todas as letras — antes ela sugeria
    "digite de novo" sem explicar por que a senha que funcionava parou."""
    if senha_informada:
        return senha_informada
    est, motivo = estado_senha(cert)
    if est == "ok":
        return seguranca.desproteger(cert["senha_protegida"], DADOS_DIR)
    raise HTTPException(400, f"{cert.get('nome') or cert.get('cnpj')}: {motivo}")


@app.post("/api/sincronizar")
def sincronizar(req: Senha):
    cert = _cert_por_id(req.id)
    if not Path(cert["caminho"]).exists():
        raise HTTPException(400, "O arquivo do certificado não está mais nesse caminho.")
    senha = _resolver_senha(cert, req.senha)
    try:
        res = core.sincronizar(cert["caminho"], senha, _pasta_cnpj(cert["cnpj"]), req.ambiente)
    except core.LimiteRequisicoes as e:
        raise HTTPException(429, str(e))
    except Exception as e:
        msg = str(e)
        if "mac verify" in msg.lower() or "invalid password" in msg.lower():
            raise HTTPException(400, "Senha do certificado incorreta.")
        if "429" in msg:
            raise HTTPException(429, "Limite de requisições atingido. Aguarde alguns minutos.")
        raise HTTPException(500, f"Falha na sincronização: {msg}")

    notas = core.carregar_notas(_pasta_cnpj(cert["cnpj"]))
    comps = core.competencias_disponiveis(notas, cert["cnpj"], "emitidas")
    comps_rec = core.competencias_disponiveis(notas, cert["cnpj"], "recebidas")
    return {"novos": res["novos"], "ultimoNSU": res["ultimoNSU"],
            "competencias": comps, "competencias_recebidas": comps_rec}


@app.get("/api/competencias")
def competencias(id: str, papel: str = "emitidas"):
    conta = _conta_por_id(id)
    notas = core.carregar_notas(_pasta_cnpj(conta["cnpj"]))
    return {"competencias": core.competencias_disponiveis(notas, conta["cnpj"], papel)}


@app.get("/api/notas")
def notas(id: str, competencia: str | None = None,
          data_ini: str | None = None, data_fim: str | None = None,
          base_data: str = "emissao", papel: str = "emitidas",
          somente_validas: bool = True, somente_retidas: bool = False):
    conta = _conta_por_id(id)
    todas = core.carregar_notas(_pasta_cnpj(conta["cnpj"]))
    # soma o histórico do sistema municipal (meses anteriores à adesão nacional),
    # para esta tela mostrar o mesmo que o Classificador
    todas = todas + _historico_municipal(_pasta_cnpj(conta["cnpj"]), conta["cnpj"], todas)
    filtradas = core.filtrar(
        todas,
        competencias=_parse_comps(competencia),
        data_ini=data_ini or None,
        data_fim=data_fim or None,
        base_data=base_data,
        cnpj_empresa=conta["cnpj"],
        papel=papel,
        somente_validas=somente_validas,
        somente_retidas=somente_retidas,
    )
    # Ordena pela data gerada (data_emissao): as mais atuais (dias mais altos,
    # até 31) ficam no topo da listagem e as mais antigas (a partir do dia 01)
    # ficam mais abaixo. Em caso de empate na data, desempata pelo número da
    # nota (também decrescente).
    def _chave_ordenacao(x):
        data = x.get("data_emissao") or ""
        try:
            numero = int(x["numero"])
        except (TypeError, ValueError):
            numero = 0
        return (data, numero)
    filtradas.sort(key=_chave_ordenacao, reverse=True)
    return {"resumo": core.resumo(filtradas), "notas": filtradas}


@app.get("/api/danfse")
def danfse(id: str, chave: str, ambiente: str = "producao"):
    """Devolve o PDF (DANFSE) oficial de uma nota, para visualizar no navegador."""
    cert = _cert_por_id(id)
    if not Path(cert["caminho"]).exists():
        raise HTTPException(400, "O arquivo do certificado não está mais nesse caminho.")
    if not cert.get("senha_protegida"):
        raise HTTPException(
            400,
            "Para ver o PDF sem digitar a senha toda vez, recadastre o certificado "
            "marcando a opção 'salvar a senha'.",
        )
    senha = _resolver_senha(cert, None)
    try:
        pdf = core.baixar_danfse(cert["caminho"], senha, chave, ambiente, _pasta_cnpj(cert["cnpj"]))
    except core.LimiteRequisicoes as e:
        raise HTTPException(429, str(e))
    except core.ServicoIndisponivel as e:
        # 503 e não 500: o defeito é do servidor da Receita, não do Fiscale.
        raise HTTPException(503, str(e))
    except HTTPException:
        raise
    except Exception as e:
        # Nunca repassar a mensagem crua: ela traz a URL com a chave de acesso
        # inteira, que aparecia na tela do usuário.
        raise HTTPException(
            500, "Não foi possível obter o PDF desta nota. O XML dela continua "
                 f"disponível no Fiscale. (detalhe técnico: {e.__class__.__name__})")
    nome_arq = "NFSe-" + re.sub(r"\D", "", chave)[:20] + ".pdf"
    return FileResponse(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nome_arq}"',
                 "Cache-Control": "no-store"},
    )


@app.get("/api/apuracao-federal")
def api_apuracao_federal(competencia: str):
    """PIS/COFINS do mês das empresas de Lucro Presumido e Real, com as
    retenções abatidas — os números que vão para o MIT."""
    import apuracao_federal

    if not re.fullmatch(r"\d{4}-\d{2}", competencia or ""):
        raise HTTPException(400, "Competência inválida. Use AAAA-MM.")
    cfgs = {}
    try:
        arq = Path(caminho_estado_classificador())
        if arq.exists():
            cfgs = (json.loads(arq.read_text("utf-8-sig")) or {}).get("cfgs", {}) or {}
    except Exception:
        cfgs = {}
    return apuracao_federal.apurar_carteira(
        DADOS_DIR, _ler_registro(), cfgs, competencia,
        lambda cnpj: _pasta_cnpj(cnpj))



@app.get("/api/iss-recolher")
def api_iss_recolher(id: str, data_ini: str | None = None, data_fim: str | None = None,
                     base_data: str = "emissao"):
    """ISS a recolher em guia municipal no período: próprio + retido de terceiros.

    A regra mora em `iss.py` (inclusive por que o ISS de nota do Simples NÃO
    entra). A rota só escolhe as notas do período — saídas e entradas juntas,
    porque as duas parcelas vêm de papéis diferentes."""
    import iss as _iss

    conta = _conta_por_id(id)
    cnpj = conta["cnpj"]
    notas = core.carregar_notas(_pasta_cnpj(cnpj))
    do_periodo = core.filtrar(
        notas, competencias=None, data_ini=data_ini or None,
        data_fim=data_fim or None, base_data=base_data or "emissao",
        cnpj_empresa=None, papel="emitidas",
        somente_validas=True, somente_retidas=False)
    return _iss.iss_a_recolher(do_periodo, cnpj)


@app.get("/api/federal-empresa")
def api_federal_empresa(id: str, data_ini: str | None = None, data_fim: str | None = None,
                        papel: str = "emitidas", base_data: str = "emissao"):
    """Tributos federais de UMA empresa no período pesquisado — é o que a tela
    das notas mostra ao lado (ou no lugar) da prévia do DAS.

    O PAPEL MANDA ANTES DO REGIME.
      recebidas (serviço tomado) -> NUNCA apuração: só as retenções na fonte
                                    das notas tomadas, ou nada;
      emitidas                   -> a apuração do regime, como abaixo.

    O conteúdo muda com o regime:
      Simples   -> só as RETENÇÕES SOFRIDAS (o tributo em si está dentro do DAS);
      Presumido -> PIS/COFINS do mês + IRPJ/CSLL do trimestre;
      Real      -> PIS/COFINS do mês (débito bruto) + retenções.
    """
    import apuracao_federal as af

    conta = _conta_por_id(id)
    cnpj = conta["cnpj"]
    cfgs = {}
    try:
        arq = Path(caminho_estado_classificador())
        if arq.exists():
            cfgs = (json.loads(arq.read_text("utf-8-sig")) or {}).get("cfgs", {}) or {}
    except Exception:
        cfgs = {}
    cfg = cfgs.get(id) or cfgs.get(cnpj) or {}
    regime = regime_da_empresa(cnpj, cfgs, id)

    notas = core.carregar_notas(_pasta_cnpj(cnpj))

    # ── NOTA DE ENTRADA: nada de DAS, PIS/COFINS, IRPJ ou CSLL sobre faturamento.
    #     Serviço tomado é despesa. Antes esta rota ignorava o papel e a tela
    #     desenhava a apuração da RECEITA em cima da aba de notas recebidas.
    if (papel or "").lower() == "recebidas":
        tomadas = core.filtrar(
            notas, competencias=None, data_ini=data_ini or None,
            data_fim=data_fim or None, base_data=base_data or "emissao",
            cnpj_empresa=cnpj, papel="recebidas",
            somente_validas=True, somente_retidas=False)
        return {"regime": regime, **af.retencoes_de_entrada(tomadas)}

    # Competências que aparecem no período pesquisado (mesma regra da prévia do DAS)
    comps = sorted({
        (n.get("competencia") or "")[:7] for n in core.filtrar(
            notas, competencias=None, data_ini=data_ini or None, data_fim=data_fim or None,
            base_data="emissao", cnpj_empresa=cnpj, papel="emitidas",
            somente_validas=True, somente_retidas=False)
        if n.get("competencia")})

    meses, ret_tot = [], {"pis": 0.0, "cofins": 0.0, "csll": 0.0, "irrf": 0.0}
    for c in comps:
        emit = af._emitidas_do_mes(notas, cnpj, c)
        r = {"competencia": c,
             "receita": round(sum(n.get("valor_servico", 0.0) for n in emit), 2),
             "retido_pis": round(sum(n.get("valor_pis", 0.0) for n in emit), 2),
             "retido_cofins": round(sum(n.get("valor_cofins", 0.0) for n in emit), 2),
             "retido_csll": round(sum(n.get("valor_csll", 0.0) for n in emit), 2),
             "retido_irrf": round(sum(n.get("valor_irrf", 0.0) for n in emit), 2)}
        if regime in af.ALIQUOTAS:
            a = af.apurar(notas, cnpj, c, regime)
            r.update({"pis_devido": a["pis_devido"], "cofins_devido": a["cofins_devido"],
                      "pis_a_pagar": a["pis_a_pagar"], "cofins_a_pagar": a["cofins_a_pagar"],
                      "pis_saldo_credor": a["pis_saldo_credor"],
                      "cofins_saldo_credor": a["cofins_saldo_credor"],
                      "aliquota_pis": a["aliquota_pis"], "aliquota_cofins": a["aliquota_cofins"],
                      "avisos": a["avisos"]})
        meses.append(r)
        for k in ret_tot:
            ret_tot[k] += r["retido_" + k]

    # IRPJ/CSLL são TRIMESTRAIS no Presumido: mostrar o trimestre que contém as
    # competências pesquisadas, não um valor mensal que não existe.
    trimestres = []
    if regime == "presumido" and comps:
        vistos = sorted({(int(c[:4]), (int(c[5:7]) - 1) // 3 + 1) for c in comps})
        for ano, tri in vistos:
            t = af.apurar_trimestre(notas, cnpj, ano, tri,
                                    cfg.get("presuncaoIRPJ"), cfg.get("presuncaoCSLL"))
            trimestres.append(t)

    return {
        "regime": regime,
        "papel": "emitidas",
        "apuracao": True,
        "competencias": comps,
        "meses": meses,
        "trimestres": trimestres,
        "retencoes": {k: round(v, 2) for k, v in ret_tot.items()},
        "totais": {
            "pis_a_pagar": round(sum(m.get("pis_a_pagar", 0.0) for m in meses), 2),
            "cofins_a_pagar": round(sum(m.get("cofins_a_pagar", 0.0) for m in meses), 2),
            "irpj_a_pagar": round(sum(t.get("irpj_a_pagar", 0.0) for t in trimestres), 2),
            "csll_a_pagar": round(sum(t.get("csll_a_pagar", 0.0) for t in trimestres), 2),
        },
    }


@app.get("/api/apuracao-trimestral")
def api_apuracao_trimestral(ano: int, trimestre: int):
    """IRPJ e CSLL do trimestre — Lucro Presumido. No Presumido esses tributos
    são trimestrais; só aparecem no MIT do mês que fecha o trimestre."""
    import apuracao_federal

    if trimestre not in (1, 2, 3, 4):
        raise HTTPException(400, "Trimestre deve ser 1, 2, 3 ou 4.")
    if not (2000 <= ano <= 2100):
        raise HTTPException(400, "Ano inválido.")
    cfgs = {}
    try:
        arq = Path(caminho_estado_classificador())
        if arq.exists():
            cfgs = (json.loads(arq.read_text("utf-8-sig")) or {}).get("cfgs", {}) or {}
    except Exception:
        cfgs = {}
    return apuracao_federal.apurar_carteira_trimestre(
        _ler_registro(), cfgs, ano, trimestre, lambda cnpj: _pasta_cnpj(cnpj))


@app.get("/api/vencimentos")
def api_vencimentos():
    """O que está prestes a derrubar o acesso: certificado vencendo, procuração
    vencendo, ou arquivo .pfx que saiu do lugar. Só leitura."""
    import vencimentos

    def _senha(cert):
        # Sem senha salva não dá para abrir o .pfx — devolve None em vez de
        # estourar, para uma empresa não derrubar o relatório inteiro.
        if not cert.get("senha_protegida"):
            return None
        try:
            return seguranca.desproteger(cert["senha_protegida"], DADOS_DIR)
        except Exception:
            return None

    cfgs = {}
    try:
        arq = Path(caminho_estado_classificador())
        if arq.exists():
            cfgs = (json.loads(arq.read_text("utf-8-sig")) or {}).get("cfgs", {}) or {}
    except Exception:
        cfgs = {}

    return vencimentos.verificar(_ler_registro(), _senha, cfgs)


@app.get("/api/nfse/xml")
def nfse_xml(id: str, chave: str):
    """Serve o XML guardado de uma NFS-e, para ver/salvar a nota individual.

    O XML é a fonte oficial: o PDF (DANFSE) é só a representação dela. Por isso
    o visualizador oferece os dois, e o Conferidor sempre lê daqui.
    """
    conta = _conta_por_id(id)
    ch = re.sub(r"[^0-9A-Za-z]", "", chave or "")
    if not ch:
        raise HTTPException(400, "Chave da nota não informada.")
    xml_dir = _pasta_cnpj(conta["cnpj"]) / "xmls"
    # Só monta o nome a partir da chave já higienizada — nada que venha do
    # navegador entra no caminho sem passar por esse filtro (path traversal).
    arq = xml_dir / f"nfse-{ch}.xml"
    if not arq.exists():
        raise HTTPException(404, "XML desta nota não encontrado no cache local.")
    return Response(content=arq.read_text("utf-8", "ignore"), media_type="application/xml")


@app.get("/api/danfse-local")
def danfse_local(id: str, chave: str):
    """Monta o DANFSe a partir do XML guardado aqui, sem falar com a Receita.

    Existe porque o /danfse do ADN cai sozinho (503) e leva junto a única forma
    de ver a nota. O XML é o documento oficial e traz tudo o que o DANFSe
    mostra, então o PDF sai igual ao do portal — a diferença é que este foi
    desenhado aqui, e não veio do servidor da Receita.
    """
    conta = _conta_por_id(id)
    ch = re.sub(r"[^0-9A-Za-z]", "", chave or "")
    if not ch:
        raise HTTPException(400, "Chave da nota não informada.")
    # Mesmo cuidado do /api/nfse/xml: nada vindo do navegador entra no caminho
    # sem passar por esse filtro (path traversal).
    arq = _pasta_cnpj(conta["cnpj"]) / "xmls" / f"nfse-{ch}.xml"
    if not arq.exists():
        raise HTTPException(404, "XML desta nota não encontrado no cache local.")
    try:
        pdf = pdflocal.gerar(arq.read_text("utf-8"))
    except Exception as e:
        raise HTTPException(
            500, "Não foi possível montar o PDF a partir do XML desta nota. "
                 f"(detalhe técnico: {e.__class__.__name__})")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="NFSe-{ch[:20]}.pdf"',
                 "Cache-Control": "no-store"},
    )


# ── Exportação ───────────────────────────────────────────────────────────────
class Exportar(BaseModel):
    id: str
    competencia: str | None = None   # opcional (lista "AAAA-MM"); filtro principal é o período
    data_ini: str | None = None      # período "AAAA-MM-DD" (inclusivo)
    data_fim: str | None = None
    base_data: str = "emissao"       # "emissao" ou "competencia"
    papel: str = "emitidas"          # "emitidas" ou "recebidas"
    somente_validas: bool = True
    somente_retidas: bool = False
    pasta_saida: str | None = None   # None = pasta padrão (dentro do sistema)


def _sanitizar_nome(s: str) -> str:
    """Deixa um texto seguro para nome de pasta."""
    limpo = re.sub(r'[<>:"/\\|?*]', "_", (s or "").strip())
    return limpo.rstrip(". ") or "empresa"


@app.post("/api/exportar")
def exportar(req: Exportar):
    cert = _conta_por_id(req.id)
    pasta = _pasta_cnpj(cert["cnpj"])
    comps = _parse_comps(req.competencia)
    di, df = (req.data_ini or None), (req.data_fim or None)
    todas = core.carregar_notas(pasta)
    filtradas = core.filtrar(
        todas,
        competencias=comps,
        data_ini=di,
        data_fim=df,
        base_data=req.base_data,
        cnpj_empresa=cert["cnpj"],
        papel=req.papel,
        somente_validas=req.somente_validas,
        somente_retidas=req.somente_retidas,
    )
    if not filtradas:
        raise HTTPException(400, "Nenhuma nota para exportar nesse período/filtro.")

    if di or df:
        sufixo = f"{(di or 'inicio')}_a_{(df or 'fim')}".replace("-", "_")
    elif comps:
        sufixo = "-".join(c.replace("-", "_") for c in comps)
    else:
        sufixo = "todas"
    comp = sufixo
    if req.pasta_saida and req.pasta_saida.strip():
        base_saida = Path(req.pasta_saida.strip().strip('"'))
        # XMLs vão direto na pasta da empresa (sem subpasta de competência)
        nome_emp = _sanitizar_nome(cert.get("nome") or cert["cnpj"])
        destino = base_saida / nome_emp
    else:
        destino = pasta / "exportados" / comp
    try:
        # cria a pasta (e subpastas); não apaga nada, então não trava se estiver aberta
        destino.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(400, f"Não consegui criar a pasta de saída ({e}). Verifique o caminho.")

    avisos: list[str] = []

    # Copia (sobrescrevendo) os XMLs da competência.
    origem = pasta / "xmls"
    nomes_atuais = set()
    for n in filtradas:
        arq = origem / n["arquivo"]
        if arq.exists():
            try:
                shutil.copy2(arq, destino / n["arquivo"])
                nomes_atuais.add(n["arquivo"])
            except Exception as e:
                avisos.append(f'Não consegui gravar {n["arquivo"]} ({e}).')

    # Remove apenas XMLs DAS competências exportadas que deixaram de ser válidos
    # (ex.: nota cancelada após uma exportação anterior). Não toca em outras
    # competências que possam estar na mesma pasta da empresa.
    for n in todas:
        if comps and n.get("competencia") not in comps:
            continue
        if not core.dentro_intervalo(n, di, df, req.base_data):
            continue
        if not core._papel_ok(n, cert["cnpj"], req.papel):
            continue
        if n["arquivo"] not in nomes_atuais:
            alvo = destino / n["arquivo"]
            if alvo.exists():
                try:
                    alvo.unlink()
                except Exception:
                    pass

    # Resumo CSV. Se o arquivo estiver aberto (ex.: Excel), grava com outro nome.
    recebidas = req.papel == "recebidas"

    def _escrever_csv(caminho: Path) -> None:
        with open(caminho, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.writer(fp, delimiter=";")
            contraparte = "Prestador" if recebidas else "Tomador"
            w.writerow(["Competencia", "Numero", "Situacao", "Chave", contraparte, "Valor Servico",
                        "Valor Liquido", "Contrib. Sociais Retidas", "IRRF Retido",
                        "INSS Retido", "ISS Retido", "ISS Retido?", "Emissao"])
            for n in filtradas:
                nome = (n["emit_nome"] if recebidas else (n["toma_nome"] or n["toma_doc"])) or ""
                w.writerow([n.get("competencia") or "", n["numero"], n.get("situacao") or "", n["chave"], nome,
                            f'{n["valor_servico"]:.2f}', f'{n["valor_liquido"]:.2f}',
                            f'{n.get("valor_retido", 0.0):.2f}', f'{n.get("valor_irrf", 0.0):.2f}',
                            f'{n.get("valor_inss", 0.0):.2f}',
                            f'{n.get("valor_iss_retido", 0.0):.2f}',
                            "SIM" if n.get("iss_retido") else "",
                            n["data_emissao"] or ""])

    csv_path = destino / f"resumo-{sufixo}.csv"
    try:
        _escrever_csv(csv_path)
    except PermissionError:
        csv_path = destino / f"resumo-{sufixo}-{datetime.now():%H%M%S}.csv"
        _escrever_csv(csv_path)
        avisos.append("O CSV anterior estava aberto; salvei o resumo com um nome novo.")

    r = core.resumo(filtradas)
    return {"pasta": str(destino), "csv": str(csv_path), "avisos": avisos,
            "quantidade": r["quantidade"], "total_servico": r["total_servico"],
            "total_liquido": r["total_liquido"], "total_retido": r["total_retido"],
            "total_irrf": r["total_irrf"],
            "total_iss_retido": r["total_iss_retido"], "qtd_iss_retido": r["qtd_iss_retido"],
            "total_inss": r["total_inss"], "qtd_inss_retido": r["qtd_inss_retido"]}


@app.post("/api/exportar-pdfs")
def exportar_pdfs(req: Exportar):
    """Baixa em lote os PDFs (DANFSE) das notas do período para uma pasta.

    Reaproveita o cache em disco; se a Receita limitar o ritmo (429), salva o que
    conseguiu e informa quantos faltaram — basta clicar de novo para continuar.
    """
    cert = _cert_por_id(req.id)
    if not Path(cert["caminho"]).exists():
        raise HTTPException(400, "O arquivo do certificado não está mais nesse caminho.")
    if not cert.get("senha_protegida"):
        raise HTTPException(
            400,
            "Para baixar os PDFs em lote, recadastre o certificado marcando a opção "
            "'salvar a senha'.",
        )
    senha = _resolver_senha(cert, None)

    pasta = _pasta_cnpj(cert["cnpj"])
    comps = _parse_comps(req.competencia)
    di, df = (req.data_ini or None), (req.data_fim or None)
    todas = core.carregar_notas(pasta)
    filtradas = core.filtrar(
        todas, competencias=comps, data_ini=di, data_fim=df, base_data=req.base_data,
        cnpj_empresa=cert["cnpj"], papel=req.papel,
        somente_validas=req.somente_validas, somente_retidas=req.somente_retidas,
    )
    if not filtradas:
        raise HTTPException(400, "Nenhuma nota nesse período/filtro.")

    if di or df:
        sufixo = f"{(di or 'inicio')}_a_{(df or 'fim')}".replace("-", "_")
    elif comps:
        sufixo = "-".join(c.replace("-", "_") for c in comps)
    else:
        sufixo = "todas"

    if req.pasta_saida and req.pasta_saida.strip():
        base_saida = Path(req.pasta_saida.strip().strip('"'))
        nome_emp = _sanitizar_nome(cert.get("apelido") or cert["nome"] or cert["cnpj"])
        destino = base_saida / nome_emp / "PDFs"
    else:
        destino = pasta / "exportados" / sufixo / "PDFs"
    try:
        destino.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(400, f"Não consegui criar a pasta de saída ({e}). Verifique o caminho.")

    cache_pdf = pasta / "danfse"
    baixados = 0
    avisos: list[str] = []
    limite = False
    for n in filtradas:
        chave = re.sub(r"\D", "", n.get("chave") or "")
        if len(chave) != 50:
            continue
        era_cache = (cache_pdf / f"{chave}.pdf").exists()
        try:
            # Medição empírica (jul/2026): o ADN sustenta ~1 PDF a cada 5s por
            # certificado; abaixo disso chove 429. 502 é instabilidade aleatória
            # e resolve com poucas retentativas curtas.
            pdf = core.baixar_danfse(cert["caminho"], senha, chave, "producao", pasta,
                                     tentativas=3, espera_max=10)
        except core.LimiteRequisicoes:
            limite = True
            break
        except Exception:
            # falha temporária (ex.: 502 do gateway da Receita) ou nota sem
            # DANFSE; conta como "restante" e segue para a próxima.
            continue
        nome = f'NFSe-{n.get("numero") or "s-n"}-{chave[-8:]}.pdf'
        try:
            shutil.copy2(pdf, destino / nome)
            baixados += 1
        except Exception as e:
            avisos.append(f'Não consegui gravar o PDF da nota {n.get("numero") or chave[:8]} ({e}).')
        if not era_cache:
            time.sleep(5.0)  # ritmo sustentável medido do ADN (~1 PDF/5s por certificado)

    restantes = len(filtradas) - baixados
    if limite:
        avisos.append(
            "A Receita limitou o ritmo de download. Os já baixados foram salvos "
            "(e ficam em cache); clique em 'Baixar PDFs' de novo em instantes para continuar."
        )
    elif restantes > 0:
        avisos.append(
            f"{restantes} nota(s) não baixaram agora (serviço da Receita instável). "
            "Clique em 'Baixar PDFs' de novo em instantes para pegar as que faltaram."
        )
    return {"pasta": str(destino), "baixados": baixados, "total": len(filtradas),
            "restantes": restantes, "limite": limite, "avisos": avisos}


@app.post("/api/exportar-pdfs-local")
def exportar_pdfs_local(req: Exportar):
    """Gera, a partir do XML, um PDF-resumo de cada nota do período (offline,
    sem limite). Não é o DANFSE oficial — é um resumo para conferência."""
    cert = _conta_por_id(req.id)
    pasta = _pasta_cnpj(cert["cnpj"])
    comps = _parse_comps(req.competencia)
    di, df = (req.data_ini or None), (req.data_fim or None)
    todas = core.carregar_notas(pasta)
    filtradas = core.filtrar(
        todas, competencias=comps, data_ini=di, data_fim=df, base_data=req.base_data,
        cnpj_empresa=cert["cnpj"], papel=req.papel,
        somente_validas=req.somente_validas, somente_retidas=req.somente_retidas,
    )
    if not filtradas:
        raise HTTPException(400, "Nenhuma nota nesse período/filtro.")

    if di or df:
        sufixo = f"{(di or 'inicio')}_a_{(df or 'fim')}".replace("-", "_")
    elif comps:
        sufixo = "-".join(c.replace("-", "_") for c in comps)
    else:
        sufixo = "todas"

    if req.pasta_saida and req.pasta_saida.strip():
        base_saida = Path(req.pasta_saida.strip().strip('"'))
        nome_emp = _sanitizar_nome(cert.get("nome") or cert["cnpj"])
        destino = base_saida / nome_emp / "PDFs-resumo"
    else:
        destino = pasta / "exportados" / sufixo / "PDFs-resumo"
    try:
        destino.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(400, f"Não consegui criar a pasta de saída ({e}). Verifique o caminho.")

    origem = pasta / "xmls"
    gerados = 0
    avisos: list[str] = []
    for n in filtradas:
        arq = origem / n.get("arquivo", "")
        if not arq.exists():
            continue
        try:
            pdf_bytes = pdflocal.gerar(arq.read_text("utf-8"))
        except Exception as e:
            avisos.append(f'Nota {n.get("numero") or "?"}: {e}')
            continue
        chave = re.sub(r"\D", "", n.get("chave") or "")
        nome = f'NFSe-{n.get("numero") or "s-n"}-{chave[-8:] or "nota"}.pdf'
        try:
            (destino / nome).write_bytes(pdf_bytes)
            gerados += 1
        except Exception as e:
            avisos.append(f'Não consegui gravar o PDF da nota {n.get("numero") or "?"} ({e}).')

    return {"pasta": str(destino), "gerados": gerados, "total": len(filtradas), "avisos": avisos}


# ── Histórico municipal do Recife (ABRASF) — completa o RBT12 ────────────────
class SincRecife(BaseModel):
    id: str
    senha: str | None = None
    data_ini: str
    data_fim: str
    inscricao_municipal: str | None = None


@app.post("/api/recife/sincronizar")
def recife_sincronizar(req: SincRecife):
    cert = _cert_por_id(req.id)
    if not Path(cert["caminho"]).exists():
        raise HTTPException(400, "O arquivo do certificado não está mais nesse caminho.")
    senha = _resolver_senha(cert, req.senha)
    try:
        return recifemod.sincronizar(cert["caminho"], senha, _pasta_cnpj(cert["cnpj"]),
                                     cert["cnpj"], req.data_ini, req.data_fim,
                                     req.inscricao_municipal)
    except Exception as e:
        msg = str(e)
        if "mac verify" in msg.lower() or "invalid password" in msg.lower():
            raise HTTPException(400, "Senha do certificado incorreta.")
        raise HTTPException(400, f"Falha ao consultar o portal do Recife: {msg}")


# ── Outras prefeituras (além do Recife) ─────────────────────────────────────
@app.get("/api/prefeituras")
def prefeituras_lista():
    """Cidades atendidas pelo módulo, com provedor, layout e se já dá para
    consultar. A tela usa isto para montar a sub-tela de outras cidades."""
    return {"cidades": prefmod.cidades_disponiveis()}


# O regime é cadastrado UMA VEZ, no módulo Clientes. Pedir de novo no
# Classificador criava duas fontes para o mesmo dado — e o dia em que
# divergissem, o imposto sairia errado sem ninguém saber qual valia.
_REGIME_POR_ROTULO = {
    "simples nacional": "simples", "simples": "simples", "mei": "mei",
    "lucro presumido": "presumido", "presumido": "presumido",
    "lucro real": "real", "real": "real",
}


def regime_da_empresa(cnpj: str, cfgs: dict | None = None, cid: str | None = None) -> str:
    """Regime tributário da empresa: Clientes manda; o Classificador é reserva.

    A reserva existe só para não quebrar quem já marcou pelo Classificador
    antes desta mudança. Quando o Clientes tem o dado, ele vence.
    """
    alvo = re.sub(r"\D", "", cnpj or "")
    try:
        arq = DADOS_DIR / "state_clientes.json"
        if arq.exists():
            dados = json.loads(arq.read_text("utf-8-sig")) or {}
            for c in (dados.get("clientes") or []):
                if re.sub(r"\D", "", c.get("cnpj") or "") == alvo:
                    r = _REGIME_POR_ROTULO.get((c.get("regime") or "").strip().lower())
                    if r:
                        return r
                    break
    except Exception:
        pass
    cfg = (cfgs or {}).get(cid or "") or (cfgs or {}).get(cnpj) or {}
    return _REGIME_POR_ROTULO.get(
        str(cfg.get("regime") or "").strip().lower(), "simples")


def _cliente_do_cadastro(cnpj: str) -> dict:
    """O registro do módulo Clientes desta empresa (ou {} se não houver)."""
    alvo = re.sub(r"\D", "", cnpj or "")
    try:
        arq = DADOS_DIR / "state_clientes.json"
        if not arq.exists():
            return {}
        dados = json.loads(arq.read_text("utf-8-sig")) or {}
        for c in (dados.get("clientes") or []):
            if re.sub(r"\D", "", c.get("cnpj") or "") == alvo:
                return c
    except Exception:
        pass
    return {}


def inicio_atividade_da_empresa(cnpj: str, cfgs: dict | None = None,
                                cid: str | None = None) -> str | None:
    """Início de atividade ('AAAA-MM'): Clientes manda, Classificador é reserva.

    É um dado do CADASTRO, como o regime — não uma configuração de tela. Ele
    muda o RBT12 nos 12 primeiros meses (CGSN 140/2018, art. 21, §2º), e
    digitá-lo em cada tela abria a mesma porta que o regime já tinha fechado:
    duas fontes para o mesmo dado, divergindo em silêncio.
    """
    ini = (_cliente_do_cadastro(cnpj).get("inicioAtividade") or "").strip()
    if ini:
        return ini[:7]
    cfg = (cfgs or {}).get(cid or "") or (cfgs or {}).get(cnpj) or {}
    return (str(cfg.get("inicioAtividade") or "")[:7] or None)


def caminho_estado_classificador() -> str:
    """Onde a tela guarda a configuração por empresa (Anexo, folha, ISS fixo,
    receitas informadas). É o mesmo arquivo que o Classificador e o NFS-e usam."""
    return str(DADOS_DIR / "state_classificador.json")


# O corpo desta rota. Em 07/09/2026 (commit 4bb89f7) a remoção do bloco
# `/api/painel/baixar-historico`, logo acima, levou esta classe junto. Com
# `from __future__ import annotations` o módulo continuou importando — a
# anotação vira texto e só é resolvida depois — e o defeito apareceu em dois
# lugares: a importação de XML municipal parou, e `/openapi.json` passou a
# devolver 500, porque o FastAPI não consegue montar o esquema de um tipo que
# não existe. `teste_nfse_importar_xml.py` segura as duas coisas.
class ImportarXmlMunicipal(BaseModel):
    id: str
    arquivos: list[dict] = []          # [{nome, xml}] ou [{nome, zip: base64}]


@app.post("/api/prefeituras/importar-xml")
def prefeituras_importar_xml(req: ImportarXmlMunicipal):
    """Importa NFS-e a partir dos XML que o PORTAL da prefeitura exporta.
    Caminho para as cidades que não liberam web service (Olinda/Tinus)."""
    import base64 as _b64
    conta = _conta_por_id(req.id)
    pares = []
    for a in req.arquivos:
        nome = a.get("nome") or "nota.xml"
        if a.get("zip"):
            try:
                pares.append((nome, _b64.b64decode((a["zip"] or "").split(",")[-1])))
            except Exception:
                pass
        elif a.get("xml") is not None:
            pares.append((nome, a["xml"].encode("utf-8")))
    if not pares:
        raise HTTPException(400, "Nenhum arquivo recebido.")
    return prefmod.importar_xml_nfse(_pasta_cnpj(conta["cnpj"]), conta["cnpj"], pares)


@app.get("/api/prefeituras/diagnostico")
def prefeituras_diagnostico():
    """Última conversa com o web service da prefeitura (envelope enviado e
    resposta recebida). É o que permite acertar o formato de um município novo
    sem ficar no escuro — cada provedor tem sua peculiaridade."""
    return prefmod.ULTIMA_RESPOSTA


@app.get("/api/prefeituras/pendencias")
def prefeituras_pendencias(id: str, pa: str | None = None):
    """Meses vazios no RBT12 da empresa + em que cidade ela emite + se dá para
    buscar lá. A tela usa isto logo após sincronizar, para oferecer a busca do
    histórico sem o usuário ter que descobrir nada."""
    conta = _conta_por_id(id)
    return prefmod.pendencias(_pasta_cnpj(conta["cnpj"]), conta["cnpj"], pa)


class SincPrefeitura(BaseModel):
    id: str
    ibge: str
    data_ini: str
    data_fim: str
    senha: str | None = None
    inscricao_municipal: str | None = None
    # Certificado do PROCURADOR (e-CPF do contador). Várias prefeituras só
    # liberam as notas a quem tem procuração — nesses casos quem autentica e
    # assina é este certificado, mas as notas consultadas continuam sendo as
    # da empresa (o CNPJ dela vai no XML).
    cert_procurador: str | None = None
    senha_procurador: str | None = None


@app.post("/api/prefeituras/sincronizar")
def prefeituras_sincronizar(req: SincPrefeitura):
    cert = _cert_por_id(req.id)                    # empresa dona das notas
    assina = cert                                  # quem autentica na prefeitura
    if req.cert_procurador and req.cert_procurador != req.id:
        assina = _cert_por_id(req.cert_procurador)
    if not Path(assina["caminho"]).exists():
        raise HTTPException(400, "O arquivo do certificado não está mais nesse caminho.")
    senha = _resolver_senha(assina, req.senha_procurador or req.senha)
    try:
        return prefmod.sincronizar(assina["caminho"], senha, _pasta_cnpj(cert["cnpj"]),
                                   cert["cnpj"], req.ibge, req.inscricao_municipal,
                                   req.data_ini, req.data_fim)
    except Exception as e:
        msg = str(e)
        if "mac verify" in msg.lower() or "invalid password" in msg.lower():
            raise HTTPException(400, "Senha do certificado incorreta.")
        raise HTTPException(400, f"Falha ao consultar a prefeitura: {msg}")


# ── Classificador: lê os XMLs já baixados e sugere o Anexo do Simples ────────
class CfgClassificador(BaseModel):
    id: str
    data_ini: str | None = None
    data_fim: str | None = None
    # "competencia" (padrão) ou "emissao". A MESMA base vale para a lista de
    # notas e para o DAS: antes a tela filtrava por emissão e o DAS somava por
    # competência, e o quadro não fechava com a lista logo acima dele.
    base_data: str = "competencia"
    folha12m: float | None = None
    fator_r: bool = False
    anexo_servico: str | None = None
    # ISS recolhido em valor FIXO à prefeitura (escritório de serviços contábeis
    # ou sociedade de profissionais): a fatia do ISS sai do DAS.
    iss_fixo: bool = False
    # 'AAAA-MM' do início de atividade. Nos 12 primeiros meses o RBT12 é
    # PROPORCIONAL (CGSN 140/2018, art. 21, §2º) — somar direto deixa o DAS menor
    # que o devido. Também evita pedir receita de meses anteriores à abertura.
    inicio_atividade: str | None = None
    # Regime tributário: "simples" (padrão), "mei", "presumido" ou "real".
    # Fora do Simples a prévia do DAS não existe e não deve ser calculada.
    regime: str | None = None
    # Percentuais de presunção do Lucro Presumido (IRPJ e CSLL), em %.
    # Variam por atividade: IRPJ 8% comércio/indústria, 32% serviços, 16%
    # transporte de passageiros, 1,6% combustíveis. CSLL 12% ou 32%.
    presuncao_irpj: float | None = None
    presuncao_csll: float | None = None
    # {'AAAA-MM': valor} — SUBSTITUI o mês. Para o histórico anterior à migração,
    # que não existe na API nacional. Só alimenta o RBT12.
    receitas_manuais: dict[str, float] = {}
    # Receita sem nota fiscal (aluguel etc.) — SOMA ao mês e entra na apuração.
    # [{competencia, valor, tipo, anexo, descricao, iss_retido}]
    lancamentos: list[dict] = []


def _cfg_de(req_ou_dict, manuais=None, lancs=None):
    """Monta o cfg do classificador. Existe para a tela e a exportação usarem
    exatamente a mesma configuração (a exportação ignorava os manuais e por
    isso fechava um RBT12 diferente do que aparecia na tela)."""
    g = (req_ou_dict.get if isinstance(req_ou_dict, dict)
         else lambda k, d=None: getattr(req_ou_dict, k, d))
    return {"folha12m": g("folha12m"), "fatorR": g("fator_r", False) or False,
            "anexoServico": (g("anexo_servico") or "").strip(),
            # ISS em valor fixo (escritório contábil / soc. de profissionais):
            # tira a fatia do ISS do DAS — LC 123, art. 18, §22-A.
            "issFixo": bool(g("iss_fixo", False)),
            "inicioAtividade": (g("inicio_atividade") or "") or None,
            # Se a tela não mandar regime, resolve pelo Clientes (fonte oficial).
            "regime": (g("regime") or None),
            "presuncaoIRPJ": g("presuncao_irpj"),
            "presuncaoCSLL": g("presuncao_csll"),
            "receitas_manuais": (manuais if manuais is not None else g("receitas_manuais")) or {},
            "lancamentos": (lancs if lancs is not None else g("lancamentos")) or []}


@app.get("/api/classificador/tipos")
def classificador_tipos():
    """Tipos de receita sem nota (para o combo da tela)."""
    return clsmod.TIPOS_RECEITA


def _cfg_com_regime(req, cfg: dict) -> dict:
    """O regime vem do Clientes, SEMPRE — a tela não decide isso.

    Antes só consultava quando o campo chegava vazio; como as telas mandam
    'simples' por padrão, a consulta nunca acontecia e uma empresa do Lucro
    Presumido aparecia com prévia do DAS, que não existe para ela.
    `regime_da_empresa` já cai na configuração da tela se o Clientes não tiver
    a empresa, então chamar sempre é seguro.
    """
    try:
        conta = _conta_por_id(getattr(req, "id", "") or "")
        cfg["regime"] = regime_da_empresa(conta["cnpj"],
                                          {conta.get("id"): cfg}, conta.get("id"))
        # Início de atividade: mesma regra do regime — o cadastro manda.
        cfg["inicioAtividade"] = inicio_atividade_da_empresa(
            conta["cnpj"], {conta.get("id"): cfg}, conta.get("id"))
    except Exception:
        cfg["regime"] = cfg.get("regime") or "simples"
    return cfg


@app.post("/api/classificador")
def classificar_empresa(req: CfgClassificador):
    conta = _conta_por_id(req.id)
    r = clsmod.classificar(_pasta_cnpj(conta["cnpj"]), conta["cnpj"],
                           _cfg_com_regime(req, _cfg_de(req)),
                           req.data_ini, req.data_fim, req.base_data)
    r["empresa"] = {"nome": conta["nome"], "cnpj": conta["cnpj"]}
    return r


class PedidoMemoria(CfgClassificador):
    """A mesma configuração da apuração, mais a competência do rastro.

    Herda de `CfgClassificador` de propósito: a memória tem de ser apurada com
    EXATAMENTE os parâmetros da tela (base, folha, ISS fixo, início de
    atividade). Um segundo conjunto de campos produziria uma memória que não
    corresponde ao número que está na tela ao lado.
    """
    competencia: str | None = None


@app.post("/api/classificador/memoria")
def classificador_memoria(req: PedidoMemoria):
    """A MEMÓRIA DE CÁLCULO: de onde veio cada valor da apuração.

    Esta rota não calcula nada. Ela chama os motores que já existem — o mesmo
    `classificar()` da tela, `apurar()` para PIS/COFINS e `apurar_trimestre()`
    para IRPJ/CSLL — e entrega o rastro de cada número até os documentos.
    """
    import apuracao_federal as _af
    import apuracao_memoria as _mem

    conta = _conta_por_id(req.id)
    cnpj = conta["cnpj"]
    cfg = _cfg_com_regime(req, _cfg_de(req))
    r = clsmod.classificar(_pasta_cnpj(cnpj), cnpj, cfg,
                           req.data_ini, req.data_fim, req.base_data)

    comp = (req.competencia or "").strip()
    if comp and not re.fullmatch(r"\d{4}-\d{2}", comp):
        raise HTTPException(400, "Competência inválida. Use AAAA-MM.")

    regime = (r.get("regime") or "").lower()
    federal = trimestre = None
    # Fora do Simples, os motores federais fazem sentido — e SÓ com competência,
    # porque PIS/COFINS são mensais e IRPJ/CSLL do Presumido, trimestrais.
    if regime in ("presumido", "real") and comp:
        notas = core.carregar_notas(_pasta_cnpj(cnpj))
        federal = _af.apurar(notas, cnpj, comp, regime)
        if regime == "presumido":
            ano, mes = int(comp[:4]), int(comp[5:7])
            trimestre = _af.apurar_trimestre(
                notas, cnpj, ano, (mes - 1) // 3 + 1,
                presuncao_irpj=cfg.get("presuncaoIRPJ"),
                presuncao_csll=cfg.get("presuncaoCSLL"))

    m = _mem.memoria(r, dados_dir=DADOS_DIR, identidade=cnpj, competencia=comp,
                     resultado_federal=federal, resultado_trimestre=trimestre)
    m["empresa"] = {"id": conta["id"], "nome": conta["nome"],
                    "cnpj_mascarado": _mascarar_doc(cnpj)}
    return m


@app.post("/api/classificador/exportar")
def classificar_exportar(payload: dict):
    conta = _conta_por_id(payload.get("id", ""))
    r = clsmod.classificar(_pasta_cnpj(conta["cnpj"]), conta["cnpj"], _cfg_de(payload),
                           payload.get("data_ini"), payload.get("data_fim"),
                           payload.get("base_data") or "competencia")
    destino = Path(payload.get("pasta_saida") or (DADOS_DIR / "exportados"))
    destino = destino / (conta["nome"] or conta["cnpj"])
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino / f'classificacao-{conta["cnpj"]}.csv'
    with open(arq, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Origem", "Numero", "Data", "Competencia", "Tomador", "cTribNac",
                    "Item LC116", "Descricao", "Valor", "Anexo sugerido", "Base", "Situacao"])
        for n in r["notas"]:
            w.writerow([n.get("origem"), n.get("numero"), n.get("data"), n.get("competencia"),
                        n.get("tomador"), n.get("ctribnac"), n.get("item_lc116"),
                        n.get("descricao"), f'{n.get("valor", 0):.2f}'.replace(".", ","),
                        n.get("anexo"), n.get("base_anexo"),
                        "Cancelada" if n.get("cancelada") else "Normal"])
    return {"arquivo": str(arq), "pasta": str(destino), "quantidade": len(r["notas"])}


# ── NF-e (modelo 55) — distribuição de DFe: puxa as COMPRAS (recebidas) ──────
@app.post("/api/nfe/sincronizar")
def nfe_sincronizar(req: Senha):
    """Sincroniza pela PORTA ÚNICA — `ingestao.servico_distribuicao`.

    O botão da tela não é autorização para ignorar política. Empresa em
    cooldown, em divergência externa, em revisão de sequência, com certificado
    vencido ou sem credencial recebe **estado e motivo**, e nenhuma chamada de
    rede acontece. O caminho legado (`nfe.py:sincronizar_nfe`) usava um
    checkpoint paralelo e não registrava auditoria — foi desativado.
    """
    cert = _cert_por_id(req.id)
    if not Path(cert["caminho"]).exists():
        raise HTTPException(400, "O arquivo do certificado não está mais nesse caminho.")
    # A senha continua sendo resolvida aqui para que erro de senha continue
    # sendo erro de senha, com a mensagem que a tela já conhece. Ela NÃO é
    # repassada adiante: o serviço lê a credencial do cadastro.
    try:
        _resolver_senha(cert, req.senha)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Senha do certificado: {e}")

    raiz = fdados.raiz()
    cad = _cad_ingestao.carregar(raiz)
    try:
        r = _svc_nfe.consultar_empresa(
            raiz, cert["cnpj"], _svc_nfe.fabrica_padrao(cad, raiz),
            ambiente=(req.ambiente or "producao"), cad=cad)
    except ValueError as e:
        raise HTTPException(400, f"Empresa não utilizável: {e}")

    if not r.autorizada:
        # 409: o pedido é válido; o ESTADO é que não permite agora. A tela
        # mostra o motivo em vez de "erro" — são coisas diferentes, e foi por
        # confundir as duas que "sincronizar de novo" virava hábito.
        raise HTTPException(409, {"estado": r.estado, "motivo": r.motivo,
                                  "detalhe": r.detalhe or None,
                                  "segundos_para_liberar": r.segundos_para_liberar})
    if r.erro:
        raise HTTPException(400, f"Falha na sincronização de NF-e: {r.erro}")
    return r.resumo()


# ════════════════════════════════════════════════════════════════════════════
#  NF-e — a tela lê o ÍNDICE, não as pastas (NF-e 5)
# ════════════════════════════════════════════════════════════════════════════
#
# Até a NF-e 5 estas rotas chamavam `nfe.carregar_nfe()`, que varre
# `<cnpj>/nfe/*.xml` a cada requisição. Duas consequências, ambas medidas:
#
#   • ~7 s por pergunta na maior empresa, porque a varredura é sempre integral;
#   • 300 documentos capturados pelo motor novo NUNCA apareciam, porque eles
#     vivem no acervo e o leitor antigo só conhece a pasta.
#
# O caminho agora é ACERVO → ÍNDICE → `ingestao.consulta` → API → tela. O leitor
# legado **continua no código** (`nfe.carregar_nfe`) e é usado por
# `ingestao.equivalencia` para conferência — mas nenhuma tela passa por ele.

# O vocabulário antigo da tela (`compra`, `venda`, `frete`) e o do índice
# (`DESTINATARIO`, `EMITENTE`, `TRANSPORTADOR`) convivem: a tela nova usa os
# nomes oficiais, e os antigos continuam aceitos para não quebrar quem chamar
# a rota do jeito de ontem.
_PAPEL_DA_TELA = {
    "compra": _cq.DESTINATARIO, "recebida": _cq.DESTINATARIO,
    "venda": _cq.EMITENTE, "emitida": _cq.EMITENTE,
    "frete": _cq.TRANSPORTADOR, "transportador": _cq.TRANSPORTADOR,
    "autxml": _cq.AUTXML, "outro": _cq.OUTRO, "outra": _cq.OUTRO,
}


def _data(v: str | None):
    """`'2026-08-01'` → `date`. Vazio é ausência de limite, não erro."""
    from datetime import date as _date
    t = (v or "").strip()[:10]
    if not t:
        return None
    try:
        a, m, d = t.split("-")
        return _date(int(a), int(m), int(d))
    except Exception:
        raise HTTPException(400, f"data inválida: {v!r} (esperado AAAA-MM-DD)")


def _filtro_da_query(q: dict) -> "_cq.Filtro":
    """Traduz a querystring em `Filtro`. Nada aqui vira consulta à SEFAZ."""
    papel = (q.get("papel") or "").strip()
    if papel and papel.lower() in _PAPEL_DA_TELA:
        papel = _PAPEL_DA_TELA[papel.lower()]
    if papel in ("todas", "todos", "TODAS", "TODOS"):
        papel = ""

    especies = (_dm.NFE55, _dm.NFCE65)
    if (q.get("especie") or "").upper() == "NFCE65" or \
            (q.get("papel") or "").lower() == "nfce":
        especies = (_dm.NFCE65,)
    elif (q.get("especie") or "").upper() == "NFE55":
        especies = (_dm.NFE55,)

    def bool3(nome):
        v = q.get(nome)
        if v in (None, "", "todos"):
            return None
        return str(v).lower() in ("1", "true", "sim", "s")

    situacoes = tuple(x for x in (q.get("situacao_atual") or "").split(",") if x)

    # Qual data o período filtra. EMISSAO é o padrão e continua sendo: ENTRADA
    # (`dhSaiEnt`) só existe em parte dos documentos, e usá-la sem o usuário
    # pedir esconderia todos os que não a informaram.
    campo_data = (q.get("campo_data") or "").upper() or _cq.CAMPO_DATA_PADRAO
    if campo_data not in _cq.CAMPOS_DATA:
        raise HTTPException(400, f"campo de data desconhecido: {campo_data!r}")

    return _cq.Filtro(
        especies=especies, campo_data=campo_data,
        data_de=_data(q.get("data_ini")), data_ate=_data(q.get("data_fim")),
        papel=papel, situacoes_atuais=situacoes,
        cancelada=bool3("cancelada"),
        conteudo=(q.get("conteudo") or "").upper() or "",
        numero=q.get("numero") or "", serie=q.get("serie") or "",
        chave=q.get("chave") or "", emitente=q.get("emitente") or "",
        destinatario=q.get("destinatario") or "",
        documento=q.get("documento") or "", uf=q.get("uf") or "",
        cfop=q.get("cfop") or "", ncm=q.get("ncm") or "",
        cest=q.get("cest") or "", icms_cst=q.get("icms_cst") or "",
        icms_csosn=q.get("icms_csosn") or "", origem=q.get("origem") or "",
        pis_cst=q.get("pis_cst") or "", cofins_cst=q.get("cofins_cst") or "",
        ipi_cst=q.get("ipi_cst") or "",
        com_reforma=bool3("com_reforma"), com_evento=bool3("com_evento"),
        texto=q.get("texto") or "")


@app.get("/api/nfe/notas")
def nfe_notas(request: Request, id: str,
              pagina: int = 1, tamanho: int = 50,
              ordenar_por: str = "emissao", direcao: str = "desc"):
    """A lista de documentos, do índice, paginada no banco.

    Contrato: as chaves `notas`, `resumo`, `por_papel` e `sincronizado` que a
    tela antiga já usava continuam existindo — `teste_nfe5` as cobra uma a uma.
    O que é novo (`pagina`, `situacao_atual`, `papeis`, `tem_reforma`) foi
    acrescentado ao lado, sem trocar o significado do que existia.
    """
    conta = _conta_por_id(id)
    try:
        filtro = _filtro_da_query(dict(request.query_params))
        pg = _cq.consultar(DADOS_DIR, conta["cnpj"], filtro, pagina=pagina,
                           tamanho=tamanho, ordenar_por=ordenar_por,
                           direcao=direcao)
    except _cq.ErroConsulta as e:
        # Filtro que a camada ainda não sabe responder é 400 com o motivo —
        # nunca 500, e nunca uma lista vazia fingindo que não há documento.
        raise HTTPException(400, str(e))

    notas = [_nota_para_tela(d) for d in pg.itens]
    total_valor = sum((d.valor_total or 0) for d in pg.itens)
    completas = sum(1 for d in pg.itens if d.conteudo == _cq.COMPLETO)

    # A contagem por papel é do PERÍODO inteiro, não da página: é o que permite
    # à tela dizer "há 6.105 como transportador" enquanto mostra 50.
    por_papel = {}
    for p in _cq.PAPEIS:
        n = _cq.contar(DADOS_DIR, conta["cnpj"], filtro.com(papel=p))
        if n:
            por_papel[p] = n

    return {
        "notas": notas,
        "por_papel": por_papel,
        # `sincronizado` responde "esta empresa já tem acervo?", que é a
        # pergunta que a tela faz para distinguir "nunca capturou" de "período
        # sem documento". Antes era a existência de um arquivo de estado.
        "sincronizado": _cq.contar(DADOS_DIR, conta["cnpj"], _cq.Filtro()) > 0,
        "resumo": {"quantidade": pg.total,
                   "total": round(float(total_valor), 2),
                   "completas": completas,
                   "resumos": len(pg.itens) - completas},
        "pagina": pg.resumo(),
        "empresa": {"id": conta["id"], "nome": conta["nome"],
                    "cnpj_mascarado": _mascarar_doc(conta["cnpj"])},
    }


def _mascarar_doc(v: str) -> str:
    """`12345678000199` → `12.***.***/0001-99`. A tela mostra o suficiente para
    a pessoa reconhecer a empresa, sem espalhar o número inteiro por logs e
    prints de tela."""
    d = re.sub(r"\D", "", v or "")
    if len(d) == 14:
        return f"{d[:2]}.***.***/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"***.{d[3:6]}.{d[6:9]}-**"
    return d


def _nota_para_tela(d) -> dict:
    """Um documento do índice no formato que a tela consome.

    Mantém `valor`, `data`, `completa` e `cancelada` com os nomes de antes —
    a tela e os testes contratuais dependem deles — e acrescenta o que a NF-e 4
    passou a saber.
    """
    return {
        "chave": d.chave, "numero": d.numero, "serie": d.serie,
        "modelo": "65" if d.especie == _dm.NFCE65 else "55",
        "especie": d.especie,
        "data": (d.dh_emissao or "")[:10],
        "dh_emissao": d.dh_emissao,
        # A data de saída/entrada é DIFERENTE da emissão e vai num campo
        # próprio. Vazio quer dizer que o documento não informou — a tela
        # mostra `—`, e em lugar nenhum a emissão ocupa o lugar dela.
        "dh_saida_entrada": d.dh_saida_entrada,
        "data_saida_entrada": (d.dh_saida_entrada or "")[:10],
        "competencia": d.competencia,
        "emit_cnpj": d.emitente, "emit_nome": d.emitente_nome,
        "dest_cnpj": d.contraparte, "dest_nome": d.contraparte_nome,
        "uf": d.chave[:2] if d.chave else "",
        "valor": float(d.valor_total or 0),
        # As duas verdades, lado a lado (NF-e 4A). A tela mostra a atual e
        # revela a original no detalhe.
        "situacao": d.situacao,
        "situacao_atual": d.situacao_atual,
        "cancelada": d.cancelada,
        "papel": d.papel, "papeis": list(d.papeis),
        "conteudo": d.conteudo, "completa": d.conteudo == _cq.COMPLETO,
        "eventos": d.eventos,
        "id_documento": d.id_documento,
        "quarentena": d.quarentena,
    }


@app.get("/api/nfe/resumo")
def nfe_resumo(request: Request, id: str):
    """Os cards. Cada número diz exatamente o que conta — e o que não conta.

    Nenhum card soma o acervo inteiro e chama de "entradas": nas
    transportadoras isso erraria por duas ordens de grandeza (NF-e 4, §2).
    """
    conta = _conta_por_id(id)
    try:
        base = _filtro_da_query(dict(request.query_params))
    except _cq.ErroConsulta as e:
        raise HTTPException(400, str(e))

    def bloco(filtro):
        # `COUNT` + `SUM` no banco. A primeira versão paginava o conjunto todo
        # em Python e levava 1,2 s para montar os seis cards numa empresa de
        # 6.513 documentos.
        return _cq.somar(DADOS_DIR, conta["cnpj"], filtro)

    recebidas = bloco(base.com(papel=_cq.DESTINATARIO))
    emitidas = bloco(base.com(papel=_cq.EMITENTE))
    transportes = bloco(base.com(papel=_cq.TRANSPORTADOR))
    autorizadas = bloco(base.com(papel=_cq.AUTXML))
    canceladas = _cq.contar(DADOS_DIR, conta["cnpj"], base.com(cancelada=True))
    reforma = _cq.contar(DADOS_DIR, conta["cnpj"], base.com(com_reforma=True))
    resumos = _cq.contar(DADOS_DIR, conta["cnpj"], base.com(conteudo=_cq.RESUMO))

    return {
        "recebidas": recebidas,
        "emitidas": emitidas,
        "transportes": transportes,
        "autorizadas": autorizadas,
        "canceladas": {"quantidade": canceladas},
        "com_reforma": {"quantidade": reforma},
        "somente_resumo": {"quantidade": resumos},
        "total_documentos": _cq.contar(DADOS_DIR, conta["cnpj"], base),
        # Escrito no próprio JSON para que a tela não precise lembrar, e para
        # que ninguém leia estes números como apuração.
        "advertencias": {
            "recebidas": "documentos em que a empresa é DESTINATÁRIA. Não é "
                         "'compra tributável': isso depende de CFOP e de "
                         "classificação fiscal, que esta fase não faz.",
            "transportes": "a empresa aparece apenas como TRANSPORTADORA. "
                           "Frete de terceiro NÃO é compra nem receita dela.",
            "com_reforma": "IBS/CBS/IS INFORMADOS no XML. O FISCALE não "
                           "calcula esses tributos.",
            "periodo": "por padrão o período filtra pela DATA DE EMISSÃO. "
                       "Desde a NF-e 6 dá para filtrar pela SAÍDA/ENTRADA "
                       "(`dhSaiEnt`) — mas ela é campo opcional, e quem não a "
                       "informou fica de fora desse filtro.",
        },
    }


@app.get("/api/nfe/emitidas")
def nfe_emitidas(request: Request, id: str):
    """NF-e/NFC-e que a EMPRESA emitiu — lidas do acervo que já existe.

    NÃO é uma segunda fonte: não captura, não importa e não consulta a SEFAZ.
    É o `consulta` de sempre com o `papel` fixado em EMITENTE, mais o estado
    das fontes que já existem — para a tela poder dizer *por onde* esses
    documentos entram, sem ir buscar nada.

    A listagem continua sendo `/api/nfe/notas?papel=EMITENTE`: uma rota que
    devolvesse documento aqui seria uma segunda leitura do mesmo acervo, com
    paginação e ordenação próprias para divergir da primeira.
    """
    conta = _conta_por_id(id)
    try:
        base = _filtro_da_query(dict(request.query_params))
        resumo = _emit.resumo(DADOS_DIR, conta["cnpj"], base)
    except _cq.ErroConsulta as e:
        raise HTTPException(400, str(e))

    # As fontes: o que existe hoje é a pasta vigiada. Lida, nunca acionada —
    # processar é `POST /api/nfe/entrada/processar`, e é decisão de quem clica.
    cfg = _entrada_cfg()
    ultimo = cfg.get("ultimo") or None
    return {
        "empresa": {"id": conta["id"], "nome": conta["nome"],
                    "cnpj_mascarado": _mascarar_doc(conta["cnpj"])},
        "resumo": resumo,
        "entrada": {
            "pasta": cfg.get("pasta") or "",
            "automatica": bool(cfg.get("auto")),
            "ultimo": ultimo,
            # Declarado para a tela não precisar adivinhar de onde vem a
            # emissão própria — e para ficar explícito que não há outra via.
            "fontes": ["PASTA_VIGIADA"],
            "observacao": ("a emissão própria não chega pela Distribuição DF-e; "
                           "ela entra por XML, sempre pelo portão único"),
        },
    }


@app.get("/api/nfe/documento")
def nfe_documento(id: str, chave: str = "", id_documento: str = ""):
    """O detalhe de UM documento: dados, itens, tributos, eventos e Reforma."""
    conta = _conta_por_id(id)
    if not chave and not id_documento:
        raise HTTPException(400, "informe a chave ou o id do documento.")
    try:
        d = _cq.detalhe(DADOS_DIR, conta["cnpj"], chave=chave,
                        id_documento=id_documento)
    except _cq.ErroConsulta as e:
        raise HTTPException(400, str(e))
    if d is None:
        raise HTTPException(404, "documento não encontrado nesta empresa.")
    return d


@app.get("/api/nfe/capacidades")
def nfe_capacidades():
    """O que a camada de consulta sabe e o que ainda não sabe.

    A tela desabilita o filtro em vez de oferecê-lo e devolver vazio."""
    return _cq.capacidades()


@app.get("/api/nfe/cte")
def nfe_cte(id: str, data_ini: str | None = None, data_fim: str | None = None,
            papel: str = "todos"):
    """CT-e (mod 57) da empresa. papel: 'emitido' (ela transportou), 'tomado'
    (ela pagou o frete) ou 'todos'."""
    conta = _conta_por_id(id)
    itens = nfemod.carregar_cte(_pasta_cnpj(conta["cnpj"]), conta["cnpj"])
    por_papel: dict = {}
    out = []
    for c in itens:
        d = (c.get("data") or "")[:10]
        if data_ini and (not d or d < data_ini):
            continue
        if data_fim and (not d or d > data_fim):
            continue
        por_papel[c["papel"]] = por_papel.get(c["papel"], 0) + 1
        if papel != "todos" and c["papel"] != papel:
            continue
        out.append(c)
    return {"cte": out, "por_papel": por_papel,
            "resumo": {"quantidade": len(out),
                       "total": round(sum(c["valor"] for c in out), 2),
                       "icms": round(sum(c["vicms"] for c in out), 2)}}


@app.get("/api/nfe/auditoria")
def nfe_auditoria(id: str, data_ini: str | None = None, data_fim: str | None = None,
                  regime: str = "real", validar_ncm: bool = False,
                  combustivel_insumo: bool = False, base_efd: float = 0.0):
    """Auditor de XML: cruza entradas/saídas já importadas e devolve créditos de
    ICMS/PIS/COFINS aproveitáveis conforme o regime + inconsistências de NCM.
    regime: 'simples' | 'presumido' | 'real'. validar_ncm=1 confere os NCM na
    tabela oficial (online). combustivel_insumo=1 soma o crédito do combustível
    consumido como insumo (só tem efeito no Lucro Real). base_efd = base de
    crédito declarada na EFD-Contribuições, para o conferidor EFD × XML."""
    conta = _conta_por_id(id)
    return auditormod.auditar(_pasta_cnpj(conta["cnpj"]), conta["cnpj"],
                              data_ini or None, data_fim or None, regime, validar_ncm,
                              combustivel_insumo, base_efd)


@app.get("/api/clientes/inscricoes")
def clientes_inscricoes(cnpj: str):
    """Varre os XMLs da empresa e devolve a Inscrição Estadual (das NF-e) e a
    Inscrição Municipal (das NFS-e) encontradas. A base pública não traz esses
    dados — vêm dos próprios documentos da empresa."""
    c = fcad.normalizar_documento(cnpj)
    if not fcad.documento_valido(c):
        raise HTTPException(400, "Documento inválido (nem CPF, nem CNPJ).")

    r = dict(inscricoesmod.varrer(_pasta_cnpj(c), c))
    r["tipo_documento"] = fcad.tipo_documento(c)

    # PESSOA FÍSICA NÃO É EXCLUÍDA — É INFORMADA.
    #
    # A rota exigia 14 dígitos e devolvia 400. Uma cliente pessoa física, com
    # CPF válido, era recusada como se o documento estivesse errado — e a tela
    # de cadastro em lote engolia o erro em silêncio.
    #
    # Inscrição MUNICIPAL se aplica a pessoa física: autônomo prestador de
    # serviço tem IM, e ela aparece nas NFS-e dele. Inscrição ESTADUAL, não:
    # é de quem circula mercadoria, e pessoa física fora do produtor rural não
    # tem. O certo não é esconder a cliente; é dizer que aquele campo não se
    # aplica a ela.
    if fcad.eh_pessoa_fisica(c):
        r["ie_situacao"] = "NÃO APLICÁVEL A CPF"
        r["ie_explicacao"] = (
            "Inscrição Estadual é de contribuinte de ICMS. Pessoa física "
            "só tem quando é produtor rural — se for o caso, informe à mão.")
        if not r.get("ie"):
            r["ie_sugerida"] = ""
    else:
        r["ie_situacao"] = "APLICAVEL"
    return r


class ImportarNFe(BaseModel):
    id: str
    arquivos: list[dict] = []          # [{nome, xml}]


@app.post("/api/nfe/importar")
def nfe_importar(req: ImportarNFe):
    """Recebe XMLs de NF-e/NFCe (texto) e guarda para entrar na apuração —
    é o caminho das VENDAS e das NFCe, que a distribuição nacional não traz."""
    conta = _conta_por_id(req.id)
    pares = [(a.get("nome") or "sem-nome.xml", (a.get("xml") or "").encode("utf-8"))
             for a in req.arquivos]
    if not pares:
        raise HTTPException(400, "Nenhum XML recebido.")
    res = nfemod.importar_xmls(_pasta_cnpj(conta["cnpj"]), conta["cnpj"], pares)
    # `importar_xmls` já passou pelo portão e devolve o quadro do lote em
    # `res["painel"]`. Não há segunda leitura aqui — seria outro caminho.
    return res


# ── Importacao manual de pasta: APOSENTADA em 07/09/2026 ──────────────
#     Saíram daqui `POST /api/nfe/importar-pasta` (o contador apontava a
#     pasta do emissor) e `POST /api/nfe/entrada` (caixa de entrada
#     agregadora, XML de várias empresas de uma vez).
#
#     A captura passou a ser automática — autXML e conectores —, e rota
#     manual que ninguém alcança vira código morto que confunde quem lê a
#     arquitetura depois. As telas delas já haviam saído na Etapa B.
#
#     NÃO saíram, e não são a mesma coisa: `/api/nfe/entrada/config` e
#     `/api/nfe/entrada/processar`. Aquilo é a PASTA VIGIADA, que é
#     automação — a pasta se enche sozinha e termina no portão.
#
#     O portão (`ingestao/importacao.py`) continua intacto: ele nunca foi
#     destas rotas, e sim delas para ele.
@app.get("/api/nfe/entrada/config")
def nfe_entrada_config():
    """Config da pasta vigiada (caminho, ligado/desligado, último resultado).
    Devolve também o caminho da pasta Downloads DESTE computador, para o botão
    'Usar Downloads' da tela — o servidor é quem enxerga o disco, não o navegador."""
    cfg = dict(_entrada_cfg())
    downloads = Path.home() / "Downloads"
    cfg["downloads"] = str(downloads) if downloads.is_dir() else ""
    return cfg


class EntradaCfg(BaseModel):
    pasta: str | None = None
    auto: bool | None = None


@app.post("/api/nfe/entrada/config")
def nfe_entrada_config_set(req: EntradaCfg):
    cfg = _entrada_cfg()
    if req.pasta is not None:
        cfg["pasta"] = req.pasta.strip() or cfg["pasta"]
    if req.auto is not None:
        cfg["auto"] = bool(req.auto)
    _entrada_salvar(cfg)
    return cfg


@app.post("/api/nfe/entrada/processar")
def nfe_entrada_processar():
    """Processa a pasta vigiada agora (botão 'Processar agora')."""
    if not _ler_registro():
        raise HTTPException(400, "Nenhuma empresa cadastrada (cadastre certificados no NFS-e).")
    return _rodar_pasta_entrada()


@app.get("/api/nfe/xml")
def nfe_xml(id: str, chave: str = "", id_documento: str = "",
            baixar: bool = False):
    """O XML ORIGINAL do acervo — os bytes exatos que chegaram.

    NUNCA reconstruído a partir do índice: o índice é projeção, e o que vale
    numa fiscalização é o arquivo assinado. Se o acervo não tem, a resposta é
    404, não uma reconstrução parecida.

    SEGURANÇA — por que não existe parâmetro de caminho
        A versão anterior varria `<cnpj>/nfe/*.xml` e devolvia o primeiro
        arquivo que CONTIVESSE a chave como substring. Além de lento, era
        frágil: uma chave citada dentro de outra nota (uma referenciada, por
        exemplo) devolvia o documento errado.

        Agora o caminho nasce da identidade: `chave` → `id_documento` →
        `acervo.caminho_canonico`. O navegador diz QUAL documento quer, nunca
        ONDE ele está. Não há como um `../..` chegar ao sistema de arquivos,
        porque nenhum trecho do que o cliente manda entra na montagem do
        caminho — só dígitos de chave e um id hexadecimal que precisa existir
        no índice desta empresa.
    """
    conta = _conta_por_id(id)
    if not chave and not id_documento:
        raise HTTPException(400, "informe a chave ou o id do documento.")

    d = _cq.detalhe(DADOS_DIR, conta["cnpj"], chave=chave,
                    id_documento=id_documento)
    if d is None:
        raise HTTPException(404, "documento não encontrado nesta empresa.")

    doc = d["documento"]
    from ingestao import acervo as _acv
    ac = _acv.abrir(DADOS_DIR, conta["cnpj"])
    try:
        caminho = ac.caminho_canonico(doc["especie"], doc["id_documento"])
        conteudo = caminho.read_bytes()
    except (OSError, ValueError):
        raise HTTPException(404, "o XML original não está no acervo.")

    cabecalhos = {}
    if baixar:
        # O nome do arquivo sai da CHAVE, não do caminho em disco: o nome
        # interno do acervo é um hash e não diz nada a quem baixa, e expor a
        # estrutura de pastas num `Content-Disposition` seria vazar o layout.
        nome = f"NFe{doc['chave'] or doc['id_documento']}.xml"
        cabecalhos["Content-Disposition"] = f'attachment; filename="{nome}"'
    return Response(content=conteudo, media_type="application/xml",
                    headers=cabecalhos)


# ════════════════════════════════════════════════════════════════════════════
#  NF-e 6 — o resultado da tela vira pacote
# ════════════════════════════════════════════════════════════════════════════
#
# TODAS estas rotas usam `_filtro_da_query`, o MESMO tradutor que
# `/api/nfe/notas` usa. Não existe segunda lógica de pesquisa: o que a tela
# mostra é o que o ZIP contém, e a igualdade é conferida por teste.
#
# NENHUMA delas fala com a SEFAZ. Baixar é operação local sobre o acervo —
# `teste_nfe6` varre o bloco cobrando a ausência dos nomes da distribuição.

def _plano_da_query(conta: dict, q: dict) -> "_exp.Plano":
    """Filtro da tela + política de canceladas → plano de exportação."""
    politica = (q.get("canceladas") or _exp.POLITICA_PADRAO).upper()
    if politica not in _exp.POLITICAS:
        raise HTTPException(
            400, f"política de canceladas desconhecida: {politica!r}; "
                 f"use uma de {list(_exp.POLITICAS)}")
    try:
        filtro = _filtro_da_query(q)
        return _exp.planejar(DADOS_DIR, conta["cnpj"], filtro,
                             politica=politica)
    except _exp.LimiteExcedido as e:
        raise HTTPException(413, str(e))
    except (_cq.ErroConsulta, _exp.ErroExportacao) as e:
        raise HTTPException(400, str(e))


def _zip_temporario():
    """Arquivo temporário para o pacote. O ZIP é montado NO DISCO.

    Montar em memória funcionaria com dez notas e faria o processo inchar com
    seis mil. O arquivo é apagado depois que a resposta termina de ser enviada
    — por `BackgroundTask`, não por `finally`: o `finally` roda antes de o
    corpo sair pela rede.
    """
    import tempfile
    fd, caminho = tempfile.mkstemp(prefix="fiscale_nfe_", suffix=".zip")
    os.close(fd)
    return Path(caminho)


def _entregar_zip(caminho: Path, nome: str):
    from starlette.background import BackgroundTask

    def limpar():
        try:
            caminho.unlink()
        except OSError:
            pass

    return FileResponse(
        str(caminho), media_type="application/zip", filename=nome,
        background=BackgroundTask(limpar))


@app.get("/api/nfe/exportar/previa")
def nfe_exportar_previa(request: Request, id: str):
    """O que o pacote vai conter — ANTES de baixar.

    É aqui que aparecem as três frases que evitam o erro caro: quantos XML
    completos existem, quantos são só resumo, e qual a composição por papel
    quando nenhum papel foi filtrado.
    """
    conta = _conta_por_id(id)
    plano = _plano_da_query(conta, dict(request.query_params))
    r = plano.resumo()
    r["nome_sugerido"] = _exp.nome_do_pacote(conta["cnpj"], plano)
    r["limite_por_pacote"] = _exp.LIMITE_DOCUMENTOS
    r["limite_danfe_em_lote"] = LIMITE_DANFE_LOTE
    r["empresa"] = {"id": conta["id"], "nome": conta["nome"],
                    "cnpj_mascarado": _mascarar_doc(conta["cnpj"])}
    return r


@app.get("/api/nfe/exportar/xmls")
def nfe_exportar_xmls(request: Request, id: str, relatorio: bool = True):
    """ZIP com `XML/<chave>.xml` e o `RELATORIO.csv`."""
    conta = _conta_por_id(id)
    plano = _plano_da_query(conta, dict(request.query_params))
    caminho = _zip_temporario()
    try:
        _exp.escrever_zip(caminho, DADOS_DIR, conta["cnpj"], plano,
                          com_relatorio=relatorio)
    except Exception:
        caminho.unlink(missing_ok=True)
        raise
    return _entregar_zip(caminho, _exp.nome_do_pacote(conta["cnpj"], plano))


@app.get("/api/nfe/exportar/xmls-planos")
def nfe_exportar_xmls_planos(request: Request, id: str):
    """ZIP plano: XML na raiz, sem mais nada dentro.

    Nenhum software de contabilidade é nomeado aqui. É um formato, não um
    destino — quem exporta decide para onde leva.
    """
    conta = _conta_por_id(id)
    plano = _plano_da_query(conta, dict(request.query_params))
    caminho = _zip_temporario()
    try:
        _exp.pacote_xmls_planos(caminho, DADOS_DIR, conta["cnpj"], plano)
    except Exception:
        caminho.unlink(missing_ok=True)
        raise
    return _entregar_zip(caminho,
                         _exp.nome_do_pacote(conta["cnpj"], plano,
                                             sufixo="XML"))


@app.get("/api/nfe/exportar/relatorio")
def nfe_exportar_relatorio(request: Request, id: str):
    """Só o `RELATORIO.csv` do resultado, sem baixar XML nenhum."""
    conta = _conta_por_id(id)
    plano = _plano_da_query(conta, dict(request.query_params))
    nome = _exp.nome_do_pacote(conta["cnpj"], plano)[:-4] + ".csv"
    return Response(
        content=_exp.relatorio_csv(plano),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'})


# ── DANFE ───────────────────────────────────────────────────────────────────
#
# O DANFE é DERIVADO. O documento fiscal é o XML do acervo, e é ele que fica
# imutável — o PDF pode ser apagado e refeito a qualquer momento sem perda.
#
# CACHE
#     Gerar custa ~70 ms (medido em 400 XML reais do acervo). Refazer a cada
#     clique é desperdício visível ao abrir o mesmo documento duas vezes. O
#     PDF fica em `<empresa>/derivados/danfe/`, e a chave do arquivo carrega
#     **o hash do XML e a versão do gerador**: XML novo ou gerador novo produz
#     nome novo, e o arquivo velho nunca é servido no lugar.
#
#     `derivados/` fica FORA do acervo de propósito. Nada ali é evidência.
LIMITE_DANFE_LOTE = 300     # ~21 s a 70 ms por documento (medido)


def _pasta_danfe(cnpj: str) -> Path:
    return DADOS_DIR / re.sub(r"\D", "", cnpj) / "derivados" / "danfe"


def _danfe_bytes(conta: dict, doc: dict, xml: bytes) -> bytes:
    """PDF do documento, do cache quando ele serve."""
    import danfe as _danfe
    marca = hashlib.sha256(xml).hexdigest()[:16]
    pasta = _pasta_danfe(conta["cnpj"])
    alvo = pasta / f"{_exp.sanitizar(doc['id_documento'])}__{marca}__v{_danfe.VERSAO}.pdf"
    if alvo.exists():
        try:
            return alvo.read_bytes()
        except OSError:
            pass
    pdf = _danfe.gerar(xml)
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        temporario = alvo.with_suffix(".parcial")
        temporario.write_bytes(pdf)
        temporario.replace(alvo)
    except OSError:
        pass          # cache é conveniência; falhar nele não pode falhar o PDF
    return pdf


def _xml_do_documento(conta: dict, chave: str = "", id_documento: str = "") -> tuple:
    """`(documento, bytes)` do acervo — o mesmo caminho seguro de `/api/nfe/xml`."""
    d = _cq.detalhe(DADOS_DIR, conta["cnpj"], chave=chave,
                    id_documento=id_documento)
    if d is None:
        raise HTTPException(404, "documento não encontrado nesta empresa.")
    doc = d["documento"]
    from ingestao import acervo as _acv
    ac = _acv.abrir(DADOS_DIR, conta["cnpj"])
    try:
        return doc, ac.caminho_canonico(doc["especie"],
                                        doc["id_documento"]).read_bytes()
    except (OSError, ValueError):
        raise HTTPException(404, "o XML original não está no acervo.")


@app.get("/api/nfe/danfe")
def nfe_danfe(id: str, chave: str = "", id_documento: str = "",
              baixar: bool = False):
    """O DANFE em PDF, gerado do XML autorizado do acervo.

    Documento que só existe como RESUMO é recusado com o motivo, em vez de
    virar um PDF com metade dos campos em branco.
    """
    import danfe as _danfe
    conta = _conta_por_id(id)
    if not chave and not id_documento:
        raise HTTPException(400, "informe a chave ou o id do documento.")
    doc, xml = _xml_do_documento(conta, chave, id_documento)
    try:
        pdf = _danfe_bytes(conta, doc, xml)
    except _danfe.DanfeIndisponivel as e:
        raise HTTPException(409, str(e))

    nome = f"DANFE-{doc['chave'] or doc['id_documento']}.pdf"
    disposicao = "attachment" if baixar else "inline"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'{disposicao}; filename="{nome}"'})


@app.get("/api/nfe/exportar/danfes")
def nfe_exportar_danfes(request: Request, id: str):
    """ZIP de DANFE do resultado. Separado do pacote de XML, sempre.

    POR QUE O LIMITE
        Gerar custa ~70 ms por documento. Seis mil documentos seriam sete
        minutos com o navegador esperando — não é lentidão, é uma requisição
        que morre no caminho. O teto de 300 mantém o pior caso em cerca de
        21 s, e acima dele a resposta diz o que fazer em vez de travar.

    O PDF NUNCA entra no pacote do Domínio: aquele lote é de XML, e um PDF no
    meio é o tipo de arquivo estranho que faz importador recusar tudo.
    """
    import danfe as _danfe
    conta = _conta_por_id(id)
    plano = _plano_da_query(conta, dict(request.query_params))
    alvos = plano.incluidos
    if len(alvos) > LIMITE_DANFE_LOTE:
        raise HTTPException(
            413, f"o resultado tem {len(alvos)} documentos com XML completo e "
                 f"o teto de DANFE em lote é {LIMITE_DANFE_LOTE} "
                 f"(cerca de {LIMITE_DANFE_LOTE * 70 // 1000} s de geração). "
                 f"Estreite o período ou o filtro.")

    import zipfile
    from ingestao import acervo as _acv
    ac = _acv.abrir(DADOS_DIR, conta["cnpj"])
    raiz_acervo = ac.raiz().resolve()
    caminho = _zip_temporario()
    gerados, recusados = 0, []
    try:
        with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=3) as z:
            for linha in alvos:
                arq = _exp._caminho_seguro(ac, raiz_acervo, linha)
                if arq is None:
                    recusados.append((linha.chave, _exp.ARQUIVO_AUSENTE_NO_ACERVO))
                    continue
                try:
                    pdf = _danfe_bytes(
                        conta, {"id_documento": linha.id_documento},
                        arq.read_bytes())
                except _danfe.DanfeIndisponivel as e:
                    recusados.append((linha.chave, str(e)[:120]))
                    continue
                z.writestr(f"DANFE/{_exp.sanitizar(linha.chave)}.pdf", pdf)
                gerados += 1
            if recusados:
                # O que não virou PDF sai escrito, como no pacote de XML.
                z.writestr("NAO_GERADOS.csv",
                           b"\xef\xbb\xbfchave;motivo\r\n" + "\r\n".join(
                               f"{c};{m}" for c, m in recusados
                           ).encode("utf-8"))
    except Exception:
        caminho.unlink(missing_ok=True)
        raise
    return _entregar_zip(caminho,
                         _exp.nome_do_pacote(conta["cnpj"], plano,
                                             prefixo="DANFE")[:-4] + ".zip")


@app.post("/api/nfe/exportar")
def nfe_exportar(payload: dict):
    """Escreve os XML numa PASTA — o fluxo que a tela antiga já usa.

    Reescrita na NF-e 6. Antes ela varria `<cnpj>/nfe/*.xml` pelo leitor
    legado, o que trazia dois defeitos junto: os 300 documentos que só existem
    no acervo nunca saíam, e a cancelada era descartada em silêncio pelo
    padrão `somente_validas=True`.

    Agora a seleção é a mesma da tela (`consulta`), os bytes são os do acervo,
    e o `RELATORIO.csv` escrito ao lado explica o que não foi. O padrão de
    canceladas passou a ser TODOS: quem quiser filtrar, escolhe.
    """
    conta = _conta_por_id(payload.get("id", ""))
    politica = (payload.get("canceladas")
                or (_exp.SOMENTE_VALIDAS if payload.get("somente_validas")
                    else _exp.POLITICA_PADRAO))
    q = {"data_ini": payload.get("data_ini") or "",
         "data_fim": payload.get("data_fim") or "",
         "papel": payload.get("papel") or "",
         "canceladas": politica}
    plano = _plano_da_query(conta, q)

    destino_base = payload.get("pasta_saida") or pasta_exportacao_padrao()
    # O nome da pasta usa a razão social — e é justamente por isso que passa
    # por `sanitizar`: razão social traz barra, acento e ponto final, e um
    # deles vira travessia de diretório no dia errado.
    destino = (Path(destino_base) / _exp.sanitizar(conta["nome"] or conta["cnpj"])
               / "NFe")
    r = _exp.escrever_em_pasta(destino, DADOS_DIR, conta["cnpj"], plano,
                               organizacao=_exp.ORG_UNICA)
    # `quantidade` mantém o nome antigo: a tela antiga o consome.
    r["quantidade"] = r["xmls_escritos"]
    r["aviso_de_papel"] = plano.aviso_de_papel()
    return r



# ════════════════════════════════════════════════════════════════════════════
#  Pasta de exportação — preferência operacional, não segredo
# ════════════════════════════════════════════════════════════════════════════
# POR QUE FORA DA PASTA DE DADOS
#     Até a NF-e 6B o padrão era `<dados>/exportados`. Exportar para dentro da
#     própria área do Fiscale mistura o que é acervo com o que é entrega: o
#     backup passa a carregar cópias, e um caminho digitado errado escreve
#     onde mora o original. A partir daqui a saída é uma pasta de trabalho, e
#     o acervo é recusado por `validar_pasta_destino`.
ARQ_PREF_EXPORTACAO = "exportacao.json"


def pasta_exportacao_padrao() -> str:
    """`C:\\FISCALE\\EXPORTACOES\\XML` no Windows, `~/FISCALE/...` fora dele.

    Não leva nome de software contábil: o Fiscale exporta XML fiscal, e quem
    consome isso é problema de quem importa.
    """
    # `FISCALE_EXPORTACOES` existe por dois motivos concretos: a versão
    # portátil precisa exportar ao lado de si mesma, e os testes não podem
    # criar pasta na raiz do disco do usuário.
    forcado = (os.environ.get("FISCALE_EXPORTACOES") or "").strip()
    if forcado:
        return str(Path(forcado))
    if os.name == "nt":
        raiz = Path(os.environ.get("SystemDrive", "C:") + os.sep)
    else:
        raiz = Path.home()
    return str(raiz / "FISCALE" / "EXPORTACOES" / "XML")


def _arq_pref_exportacao() -> Path:
    return Path(DADOS_DIR) / ARQ_PREF_EXPORTACAO


def ler_pref_exportacao() -> dict:
    # `com_relatorio` nasce DESLIGADO: o objetivo de "Baixar XMLs em lote" é
    # pôr os XML na pasta. Um CSV que ninguém pediu no meio do lote é
    # exatamente o arquivo estranho que faz importador recusar a pasta.
    padrao = {"pasta": pasta_exportacao_padrao(),
              "organizacao": _exp.ORGANIZACAO_PADRAO,
              "com_relatorio": False}
    arq = _arq_pref_exportacao()
    if not arq.exists():
        return padrao
    try:
        d = json.loads(arq.read_text("utf-8-sig")) or {}
    except Exception:
        return padrao          # preferência corrompida não derruba a tela
    padrao.update({k: v for k, v in d.items() if k in padrao})
    if padrao["organizacao"] not in _exp.ORGANIZACOES:
        padrao["organizacao"] = _exp.ORGANIZACAO_PADRAO
    return padrao


@app.get("/api/nfe/exportar/pasta-config")
def nfe_pasta_config():
    """A preferência atual + o padrão, para a tela poder oferecer 'restaurar'."""
    pref = ler_pref_exportacao()
    pref["padrao"] = pasta_exportacao_padrao()
    # A tela não repete os rótulos: quem nomeia as organizações é o módulo,
    # senão o nome muda num lugar e fica velho no outro.
    pref["organizacoes"] = [{"valor": o,
                             "rotulo": _exp.ROTULO_ORGANIZACAO.get(o, o)}
                            for o in _exp.ORGANIZACOES]
    return pref


@app.post("/api/nfe/exportar/pasta-config")
def nfe_pasta_config_salvar(payload: dict):
    """Valida ANTES de guardar. Preferência que aponta para pasta inválida é
    pior que preferência nenhuma: o erro só aparece na hora de exportar."""
    pasta = (payload.get("pasta") or "").strip() or pasta_exportacao_padrao()
    try:
        alvo = _exp.validar_pasta_destino(pasta, DADOS_DIR)
        _exp.conferir_escrita(alvo)
    except _exp.PastaInsegura as e:
        raise HTTPException(400, str(e))
    org = payload.get("organizacao") or _exp.ORGANIZACAO_PADRAO
    if org not in _exp.ORGANIZACOES:
        raise HTTPException(400, "Organização de pastas desconhecida.")
    pref = {"pasta": str(alvo), "organizacao": org,
            "com_relatorio": bool(payload.get("com_relatorio", False))}
    _arq_pref_exportacao().write_text(
        json.dumps(pref, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, **pref}


@app.post("/api/nfe/exportar/pasta")
def nfe_exportar_pasta(payload: dict):
    """Grava os XML do resultado numa pasta DO SERVIDOR.

    Não é download. Quando o Fiscale for aberto do celular, isto continua
    gravando na máquina onde ele roda — a tela precisa dizer isso, e o
    resultado devolve `destino: SERVIDOR` para ela não ter como esquecer.
    """
    conta = _conta_por_id(payload.get("id", ""))
    pref = ler_pref_exportacao()
    pasta = (payload.get("pasta") or "").strip() or pref["pasta"]
    org = payload.get("organizacao") or pref["organizacao"]
    com_rel = payload.get("com_relatorio")
    com_rel = pref["com_relatorio"] if com_rel is None else bool(com_rel)

    plano = _plano_da_query(conta, payload.get("filtro") or payload)
    try:
        r = _exp.escrever_em_pasta(
            pasta, DADOS_DIR, conta["cnpj"], plano,
            organizacao=org, com_relatorio=com_rel,
            empresa=conta.get("nome") or conta["cnpj"])
    except _exp.PastaInsegura as e:
        raise HTTPException(400, str(e))
    except _exp.ErroExportacao as e:
        raise HTTPException(400, str(e))
    r["aviso_de_papel"] = plano.aviso_de_papel()
    return r


# ── Central de Atualizações Fiscais ─────────────────────────────────────────
# Jornal, não motor. Nenhuma destas rotas altera alíquota, anexo, regra de
# cálculo, cadastro ou documento — a revisão legislativa que MUDA número é
# outra fase (AI 6) e não existe ainda.
def _central() -> "_fatu.Central":
    return _fatu.Central(DADOS_DIR)


@app.get("/api/fiscal/atualizacoes")
def fiscal_atualizacoes(tema: str = "", fonte: str = "", limite: int = 100):
    """O que está guardado, com o estado de cada fonte."""
    return _central().listar(tema=tema, fonte=fonte, limite=max(1, min(limite, 300)))


@app.get("/api/fiscal/atualizacoes/resumo")
def fiscal_atualizacoes_resumo():
    """Só o contador — é o que a barra de módulos consulta em todas as telas.

    Barato de propósito: lê dois arquivos e não toca a rede. Quem decide ir
    às fontes é a tela, olhando `precisa_verificar`, para uma consulta lenta
    nunca segurar o desenho do cabeçalho.
    """
    c = _central()
    est = c.estado()
    return {"nao_lidos": c.nao_lidos(),
            "precisa_verificar": c.precisa_verificar(),
            "ligado": est.get("ligado", True),
            "ultima_verificacao": est.get("ultima_verificacao", "")}


@app.post("/api/fiscal/atualizacoes/verificar")
def fiscal_atualizacoes_verificar():
    """Vai às fontes agora. Devolve o que aconteceu, fonte por fonte."""
    return _central().verificar()


@app.post("/api/fiscal/atualizacoes/lido")
def fiscal_atualizacoes_lido():
    return _central().marcar_lido()


@app.post("/api/fiscal/atualizacoes/config")
def fiscal_atualizacoes_config(payload: dict):
    return _central().configurar(intervalo=payload.get("intervalo"),
                                 ligado=payload.get("ligado"))


# ── Quais empresas o módulo NF-e mostra ─────────────────────────────────────
@app.get("/api/nfe/empresas")
def nfe_empresas(contar: bool = True):
    """O seletor da NF-e: só empresa com Inscrição Estadual cadastrada.

    NF-e é circulação de mercadoria; quem participa dela tem IE. A fonte é o
    MESMO cadastro do módulo Clientes — não há lista paralela, então IE
    cadastrada depois faz a empresa voltar sozinha.

    As que ficam de fora saem em `pendencias`, com o motivo e com quantos
    documentos elas têm no acervo. Elas continuam com tudo: acervo, índice,
    XML e checkpoint. Some da lista de trabalho, não do sistema.
    """
    certs = _ler_registro()
    clientes = []
    try:
        arq = DADOS_DIR / "state_clientes.json"
        if arq.exists():
            clientes = (json.loads(arq.read_text("utf-8-sig")) or {}).get(
                "clientes") or []
    except Exception:
        clientes = []          # cadastro ilegível: tudo vira pendência, e a
                               # tela diz por quê — melhor que sumir calado

    docs = {}
    if contar:
        for c in certs:
            try:
                docs[c["cnpj"]] = sum(
                    _ix.abrir(DADOS_DIR, c["cnpj"]).contar().values())
            except Exception:
                docs[c["cnpj"]] = 0
    return _emp.separar(certs, clientes, docs_por_cnpj=docs)


@app.get("/api/nfe/pendencias-cadastrais")
def nfe_pendencias_cadastrais():
    """As empresas que TÊM acervo NF-e e não atendem ao cadastro exigido.

    É a fila de reconciliação antes de o filtro por IE valer no dia a dia.
    Só entram as que têm documento parado: cadastro incompleto de empresa sem
    movimento é arrumação, não pendência.

    ESTA ROTA NÃO ESCREVE NADA
        Nem no cadastro, nem no acervo, nem no índice, nem no checkpoint. A
        IE que vem dos XML é **sugestão para conferência** — quem decide é o
        usuário, no cadastro de Clientes, com o botão de salvar dele.

        A tentação de gravar sozinho é grande e é justamente o erro: uma IE
        lida de um XML de terceiro, ou de um documento antigo, entraria no
        cadastro sem ninguém ter olhado — e passaria a valer para apuração.
    """
    base = nfe_empresas()
    fila = []
    for p in base["pendencias"]:
        if p["documentos_no_acervo"] <= 0:
            continue
        sugerida, vistas = "", []
        try:
            achado = inscricoesmod.varrer(_pasta_cnpj(p["cnpj"]), p["cnpj"]) or {}
            sugerida = achado.get("ie_sugerida") or ""
            vistas = achado.get("ie") or []
        except Exception:
            pass                      # sem sugestão a pendência ainda vale
        fila.append({
            **p,
            "situacao": p["motivo"],
            "situacao_rotulo": _emp.ROTULO_SITUACAO.get(p["motivo"], p["motivo"]),
            "ie_sugerida": sugerida,
            "ie_vistas_nos_xml": vistas,
            "rotulo_sugestao": _emp.ROTULO_SUGESTAO if sugerida else "",
            # O nome vem do certificado — é onde a razão social está escrita
            # por quem a emitiu, e não digitada por nós.
            "origem_do_nome": "certificado digital",
        })
    return {
        "pendencias": fila,
        "total": len(fila),
        "documentos_parados": sum(p["documentos_no_acervo"] for p in fila),
        "grava_alguma_coisa": False,
        # A frase inteira, com a lista do que não é tocado, mora na tela
        # (`nfe_pendencias.html`) — que é onde o usuário lê. Aqui fica o
        # essencial: a guarda de `teste_nfe6.py` proíbe as palavras da
        # captura no CÓDIGO deste bloco, e literal de texto é código.
        "observacao": (
            "A Inscrição Estadual encontrada nos XML é SUGESTÃO para "
            "conferência. Nada é gravado por esta tela: revise em Clientes e "
            "salve por lá."),
    }


@app.get("/api/nfe/empresas/ie-sugerida")
def nfe_ie_sugerida(cnpj: str):
    """A IE que os XML da própria empresa revelam.

    Serve para a pendência ser resolvível com um clique em vez de virar
    trabalho de digitação. Lê o acervo; não fala com a SEFAZ.
    """
    return inscricoesmod.varrer(_pasta_cnpj(cnpj), cnpj)




# ════════════════════════════════════════════════════════════════════════════
#  CT-e — leitura do acervo (CTE 1)
# ════════════════════════════════════════════════════════════════════════════
#
# NENHUMA destas rotas fala com a SEFAZ. A única que pode um dia falar é
# `POST /api/cte/sincronizar`, e ela passa obrigatoriamente por
# `ingestao.servico_cte`, que é a porta única. `teste_cte1` varre este bloco
# cobrando a ausência de qualquer chamada direta ao conector.


def _filtro_cte(q: dict) -> "_cq.Filtro":
    """A querystring vira `Filtro` de CT-e. Mesma camada de consulta da NF-e.

    A diferença é `especies`: aqui é sempre CT-e. Deixar a espécie vir da URL
    permitiria à tela de CT-e listar notas fiscais, que é confusão garantida.
    """
    papel = (q.get("papel") or "").strip().upper()
    if papel in ("TODAS", "TODOS", ""):
        papel = ""
    if papel and papel not in _cq.PAPEIS_TODOS:
        raise HTTPException(400, f"papel desconhecido: {papel!r}; "
                                 f"use um de {list(_cq.PAPEIS_CTE)}")

    especies = (_dm.CTE57,)
    if (q.get("incluir_eventos") or "") in ("1", "true", "sim"):
        especies = (_dm.CTE57, _dm.EVENTO_CTE)

    campo_data = (q.get("campo_data") or "").upper() or _cq.CAMPO_DATA_PADRAO
    if campo_data not in _cq.CAMPOS_DATA:
        raise HTTPException(400, f"campo de data desconhecido: {campo_data!r}")

    def bool3(nome):
        v = q.get(nome)
        if v in (None, "", "todos"):
            return None
        return str(v).lower() in ("1", "true", "sim", "s")

    situacoes = tuple(x for x in (q.get("situacao_atual") or "").split(",") if x)
    return _cq.Filtro(
        especies=especies, campo_data=campo_data,
        data_de=_data(q.get("data_ini")), data_ate=_data(q.get("data_fim")),
        papel=papel, situacoes_atuais=situacoes, cancelada=bool3("cancelada"),
        conteudo=(q.get("conteudo") or "").upper() or "",
        numero=q.get("numero") or "", serie=q.get("serie") or "",
        chave=q.get("chave") or "", emitente=q.get("emitente") or "",
        destinatario=q.get("destinatario") or "",
        documento=q.get("documento") or "", uf=q.get("uf") or "",
        com_evento=bool3("com_evento"), texto=q.get("texto") or "")


@app.get("/api/cte/empresas")
def cte_empresas():
    """O seletor de clientes do CT-e, em ORDEM ALFABÉTICA.

    Cada empresa vem com a situação da captura, e as três situações que a tela
    precisa distinguir jamais aparecem como o mesmo `0`:

      • `SEM_CERTIFICADO` — falta arquivo, não falta consulta
      • `AGUARDANDO_PRIMEIRA_CONSULTA` — pronta, nunca consultada
      • `SINCRONIZADA` — já consultou; o zero aqui significa "não tem CT-e"

    Mostrar `0 CT-e` para quem nunca foi consultado seria afirmar ausência a
    partir de silêncio.
    """
    pan = _cred_cte.panorama(DADOS_DIR)
    repo = _cpm.RepositorioCheckpoint(DADOS_DIR)
    contas = {re.sub(r"\D", "", c.get("cnpj") or ""): c for c in _ler_registro()}

    saida = []
    for linha in pan["empresas"]:
        marca = linha["empresa"].replace("***", "")
        conta = next((c for d, c in contas.items() if d.startswith(marca)), None)
        cnpj = re.sub(r"\D", "", (conta or {}).get("cnpj") or "")
        cp = repo.carregar(cnpj, _svc_cte.SERVICO, _ing.PRODUCAO) if cnpj else None
        nunca = (cp is None) or _dist.nsu_int(cp.ult_nsu or "0") == 0

        if not linha["pode_consultar"]:
            situacao = linha["estado"]
        elif nunca:
            situacao = "AGUARDANDO_PRIMEIRA_CONSULTA"
        else:
            situacao = "SINCRONIZADA"

        total = _cq.contar(DADOS_DIR, cnpj, _cq.Filtro(especies=(_dm.CTE57,))) \
            if cnpj else 0
        saida.append({
            "id": (conta or {}).get("id") or "",
            "nome": linha["nome"] or (conta or {}).get("nome") or "",
            "cnpj_mascarado": _mascarar_doc(cnpj) if cnpj else linha["empresa"],
            "situacao": situacao,
            "pode_consultar": linha["pode_consultar"],
            "motivo": linha["motivo"],
            "ultimo_nsu": (cp.ult_nsu if cp else "") or "",
            # `None` — e não `0` — quando nunca foi consultada. A tela mostra
            # "—", que é a verdade; `0` seria uma afirmação sem base.
            "documentos": None if nunca else total,
        })
    # A ordenação central: mesma ordem do resto do FISCALE.
    saida.sort(key=lambda e: (e["nome"] or "").casefold())
    return {"empresas": saida, "por_estado": pan["por_estado"],
            "arquivos_orfaos": pan["arquivos_orfaos"],
            "observacao": pan["observacao"]}


@app.get("/api/cte/notas")
def cte_notas(request: Request, id: str, pagina: int = 1, tamanho: int = 50,
              ordenar_por: str = "emissao", direcao: str = "desc"):
    """A lista de CT-e da empresa, do índice, paginada no banco."""
    conta = _conta_por_id(id)
    try:
        filtro = _filtro_cte(dict(request.query_params))
        pg = _cq.consultar(DADOS_DIR, conta["cnpj"], filtro, pagina=pagina,
                           tamanho=tamanho, ordenar_por=ordenar_por,
                           direcao=direcao)
    except _cq.ErroConsulta as e:
        raise HTTPException(400, str(e))

    por_papel = {}
    for p in _cq.PAPEIS_CTE:
        n = _cq.contar(DADOS_DIR, conta["cnpj"], filtro.com(papel=p))
        if n:
            por_papel[p] = n

    return {
        "documentos": [_cte_para_tela(d) for d in pg.itens],
        "por_papel": por_papel,
        "pagina": pg.resumo(),
        "sincronizado": _cq.contar(DADOS_DIR, conta["cnpj"],
                                   _cq.Filtro(especies=(_dm.CTE57,))) > 0,
        "empresa": {"id": conta["id"], "nome": conta["nome"],
                    "cnpj_mascarado": _mascarar_doc(conta["cnpj"])},
    }


def _cte_para_tela(d) -> dict:
    return {
        "chave": d.chave, "numero": d.numero, "serie": d.serie,
        "especie": d.especie, "modelo": "57",
        "data": (d.dh_emissao or "")[:10], "dh_emissao": d.dh_emissao,
        "competencia": d.competencia,
        "emit_cnpj": d.emitente, "emit_nome": d.emitente_nome,
        "dest_cnpj": d.contraparte, "dest_nome": d.contraparte_nome,
        "uf": d.chave[:2] if d.chave else "",
        "valor": float(d.valor_total or 0),
        "situacao": d.situacao, "situacao_atual": d.situacao_atual,
        "cancelada": d.cancelada,
        "papel": d.papel, "papeis": list(d.papeis),
        "conteudo": d.conteudo, "completo": d.conteudo == _cq.COMPLETO,
        "eventos": d.eventos, "id_documento": d.id_documento,
    }


@app.get("/api/cte/resumo")
def cte_resumo(request: Request, id: str):
    """Os cards do CT-e. Cada número diz o que conta.

    `emitidos` e `tomados` são perguntas diferentes e não se somam: uma
    transportadora emite; um embarcador toma. A mesma empresa pode fazer as
    duas coisas, e nesse caso o documento aparece nos dois — de propósito.
    """
    conta = _conta_por_id(id)
    try:
        base = _filtro_cte(dict(request.query_params))
    except _cq.ErroConsulta as e:
        raise HTTPException(400, str(e))

    def bloco(papel):
        return _cq.somar(DADOS_DIR, conta["cnpj"], base.com(papel=papel))

    return {
        "emitidos": bloco(_cq.EMITENTE),
        "tomados": bloco(_cq.TOMADOR),
        "remetente": bloco(_cq.REMETENTE),
        "destinatario": bloco(_cq.DESTINATARIO),
        "expedidor": bloco(_cq.EXPEDIDOR),
        "recebedor": bloco(_cq.RECEBEDOR),
        "canceladas": {"quantidade": _cq.contar(
            DADOS_DIR, conta["cnpj"], base.com(cancelada=True))},
        "eventos": {"quantidade": _cq.contar(
            DADOS_DIR, conta["cnpj"],
            _cq.Filtro(especies=(_dm.EVENTO_CTE,)))},
        "total": _cq.contar(DADOS_DIR, conta["cnpj"], base),
        "advertencias": {
            "emitidos": "a distribuição oficial NÃO devolve ao emitente os "
                        "documentos que ele mesmo emitiu (NT 2015.002 v1.05). "
                        "Estes vieram por IMPORTAÇÃO de XML — ERP, pasta, "
                        "e-mail ou ZIP —, e sincronizar não traz mais nenhum.",
            "tomados": "TOMADOR é quem paga o frete. Na maioria dos CT-e ele "
                       "não tem bloco próprio: o campo `toma` aponta para o "
                       "remetente, expedidor, recebedor ou destinatário.",
            "papeis": "um mesmo CT-e pode contar em mais de um card — a "
                      "empresa pode ser emitente e tomadora do mesmo frete.",
        },
    }


@app.get("/api/cte/documento")
def cte_documento(id: str, chave: str = "", id_documento: str = ""):
    """O detalhe de UM CT-e: participantes, papéis, valores e eventos."""
    conta = _conta_por_id(id)
    if not chave and not id_documento:
        raise HTTPException(400, "informe a chave ou o id do documento.")
    d = _cq.detalhe(DADOS_DIR, conta["cnpj"], chave=chave,
                    id_documento=id_documento)
    if d is None:
        raise HTTPException(404, "documento não encontrado nesta empresa.")
    return d


@app.get("/api/cte/xml")
def cte_xml(id: str, chave: str = "", id_documento: str = "",
            baixar: bool = False):
    """O XML ORIGINAL do acervo — os bytes exatos que chegaram.

    Mesmo modelo seguro da NF-e: o navegador diz QUAL documento quer, nunca
    ONDE ele está. O caminho nasce de `especie` + `id_documento`.
    """
    conta = _conta_por_id(id)
    if not chave and not id_documento:
        raise HTTPException(400, "informe a chave ou o id do documento.")
    d = _cq.detalhe(DADOS_DIR, conta["cnpj"], chave=chave,
                    id_documento=id_documento)
    if d is None:
        raise HTTPException(404, "documento não encontrado nesta empresa.")
    doc = d["documento"]
    from ingestao import acervo as _acv
    ac = _acv.abrir(DADOS_DIR, conta["cnpj"])
    try:
        conteudo = ac.caminho_canonico(doc["especie"],
                                       doc["id_documento"]).read_bytes()
    except (OSError, ValueError):
        raise HTTPException(404, "o XML original não está no acervo.")
    cab = {}
    if baixar:
        nome = f"CTe{doc['chave'] or doc['id_documento']}.xml"
        cab["Content-Disposition"] = f'attachment; filename="{nome}"'
    return Response(content=conteudo, media_type="application/xml", headers=cab)


@app.get("/api/cte/situacao")
def cte_situacao(id: str):
    """Diagnóstico da captura de UMA empresa. Não consulta nada.

    Existe para a etapa CTE 2 poder ensaiar a decisão — "seria autorizada
    agora?" — sem que nenhuma chamada saia.
    """
    conta = _conta_por_id(id)
    r = _svc_cte.elegivel(DADOS_DIR, conta["cnpj"], ambiente=_ing.PRODUCAO,
                          cad=_cad_ingestao.carregar(DADOS_DIR))
    pront = _cred_cte.avaliar(DADOS_DIR, conta["cnpj"])
    repo = _cpm.RepositorioCheckpoint(DADOS_DIR)
    cp = repo.carregar(conta["cnpj"], _svc_cte.SERVICO, _ing.PRODUCAO)
    nunca = _dist.nsu_int(cp.ult_nsu or "0") == 0
    return {"elegibilidade": r.resumo(), "certificado": pront.resumo(),
            "checkpoint": {"servico": _svc_cte.SERVICO,
                           "ult_nsu": cp.ult_nsu, "max_nsu": cp.max_nsu,
                           "nunca_consultada": nunca},
            "politica": {
                "intervalo_minimo_segundos":
                    _svc_cte.POLITICA_PADRAO.intervalo_minimo_segundos,
                "consultas_por_hora":
                    _svc_cte.POLITICA_PADRAO.consultas_por_hora,
                "espera_progressiva":
                    list(_svc_cte.POLITICA_PADRAO.espera_progressiva),
                "cooldown_fila_esgotada_segundos":
                    _svc_cte.POLITICA_PADRAO.cooldown_fila_esgotada_segundos,
            },
            # As duas coisas que a tela precisa dizer ANTES da primeira
            # consulta, para ninguém esperar o que não vem.
            "limites": {
                "janela_retencao_meses": _svc_cte.RETENCAO_MESES_DO_ZERO,
                "aviso_janela": _svc_cte.AVISO_JANELA,
                "emitidos_nao_vem_pela_captura":
                    "a distribuição não devolve ao emitente os próprios "
                    "documentos (NT 2015.002 v1.05): CT-e emitidos entram por "
                    "importação de XML",
                "consulta_por_chave": False,
            }}


@app.post("/api/abrir-pasta")
def abrir_pasta(payload: dict):
    caminho = payload.get("caminho", "")
    if not caminho or not Path(caminho).exists():
        raise HTTPException(400, "Pasta inexistente.")
    try:
        sistema = platform.system()
        if sistema == "Windows":
            os.startfile(caminho)  # type: ignore[attr-defined]
        elif sistema == "Darwin":
            subprocess.Popen(["open", caminho])
        else:
            subprocess.Popen(["xdg-open", caminho])
    except Exception as e:
        raise HTTPException(500, f"Não foi possível abrir a pasta: {e}")
    return {"ok": True}


# NOTA DE LUGAR: este bloco fica DEPOIS de `/api/abrir-pasta` de propósito.
# `teste_nfe6.py` varre o trecho das rotas de exportação e falha se ele
# mencionar `_svc_nfe`, `checkpoint`, `distNSU` ou `sincronizar` — é assim
# que se prova que exportar um lote não fala com a SEFAZ nem move contador.
# O onboarding faz exatamente as duas coisas, e por isso mora fora daquele
# trecho. A guarda está certa; o lugar é que precisava ser outro.
# ── Primeira sincronização de uma empresa (onboarding) ──────────────────────
# A política recusa empresa nunca sincronizada: `consultar_novas_empresas` é
# `False` por padrão, e o estado dela é `AGUARDANDO_PRIMEIRA_SINCRONIZACAO`.
# Isso é proposital e a lição tem nome — a MONTE. A primeira consulta puxa a
# fila inteira da SEFAZ e MOVE o checkpoint, e o NSU não volta: se ela sair
# sozinha, no ambiente errado ou na empresa errada, não há desfazer.
#
# O portão existia; a decisão explícita não tinha onde ser tomada. Era por
# isso que empresa nova nunca capturava nada, sem nenhum erro aparecer.
def _empresas_sem_checkpoint():
    """Quem tem certificado e nunca consultou a SEFAZ.

    Quem responde é o REPOSITÓRIO de checkpoint, não um caminho adivinhado.
    Existem dois arquivos parecidos no disco e eles não querem dizer a mesma
    coisa: `<cnpj>/nfe/estado.json` é do leitor legado, e
    `<cnpj>/ingestao/NFE_DISTRIBUICAO.<ambiente>.json` é o checkpoint de
    verdade. Olhar o primeiro faz empresa já sincronizada parecer nova — e
    convidaria a refazer um onboarding que não precisa ser refeito.
    """
    from ingestao import checkpoint as _cpm
    repo = _cpm.RepositorioCheckpoint(DADOS_DIR)
    saida = []
    for c in _ler_registro():
        try:
            tem = repo.existe(c["cnpj"], _svc_nfe.SERVICO_PADRAO, "producao")
        except Exception:
            tem = False          # identidade inválida entra na fila, com o
                                 # certificado e a senha à mostra para revisão
        if not tem:
            saida.append(c)
    return saida


@app.get("/api/nfe/onboarding")
def nfe_onboarding_lista():
    """Empresas que nunca sincronizaram, e por isso não têm documento nenhum.

    Só leitura. Não consulta a SEFAZ nem move nada.
    """
    fila = []
    for c in _empresas_sem_checkpoint():
        fila.append({
            "id": c.get("id"),
            "cnpj": c.get("cnpj"),
            "nome": c.get("nome") or c.get("apelido") or c.get("cnpj"),
            "tem_senha": bool(c.get("senha_protegida") or c.get("senha_salva")),
            "certificado_no_lugar": Path(c.get("caminho", "")).exists(),
        })
    fila.sort(key=lambda x: x["nome"])
    return {
        "empresas": fila,
        "total": len(fila),
        "observacao": (
            "A primeira consulta traz a fila inteira que a SEFAZ guarda para "
            "a empresa e avança o contador (NSU), que não retrocede. Por isso "
            "ela exige um clique consciente, uma empresa por vez."),
    }


@app.post("/api/nfe/onboarding")
def nfe_onboarding_executar(payload: dict):
    """Faz a PRIMEIRA sincronização de UMA empresa, por decisão explícita.

    Uma por chamada, de propósito: onboarding em lote é como se descobre,
    tarde, que o ambiente estava errado em vinte empresas de uma vez.

    Passa pela MESMA porta (`servico_distribuicao.consultar_empresa`) — o que
    muda é só `consultar_novas_empresas=True` para esta chamada. Não há motor
    paralelo, e o resto da política (cooldown, teto, auditoria) continua
    valendo.
    """
    from ingestao import controlador as _ctl

    cert = _cert_por_id(payload.get("id", ""))
    if not Path(cert["caminho"]).exists():
        raise HTTPException(400, "O arquivo do certificado não está mais nesse caminho.")
    try:
        _resolver_senha(cert, payload.get("senha"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Senha do certificado: {e}")

    raiz = fdados.raiz()
    cad = _cad_ingestao.carregar(raiz)
    politica = _dataclasses.replace(_ctl.POLITICA_PADRAO,
                                    consultar_novas_empresas=True)
    try:
        r = _svc_nfe.consultar_empresa(
            raiz, cert["cnpj"], _svc_nfe.fabrica_padrao(cad, raiz),
            ambiente=(payload.get("ambiente") or "producao"),
            politica=politica, cad=cad)
    except ValueError as e:
        raise HTTPException(400, f"Empresa não utilizável: {e}")

    if not r.autorizada:
        raise HTTPException(409, {"estado": r.estado, "motivo": r.motivo,
                                  "detalhe": r.detalhe or None,
                                  "segundos_para_liberar": r.segundos_para_liberar})
    if r.erro:
        raise HTTPException(400, f"Falha na primeira sincronização: {r.erro}")
    resumo = r.resumo()
    resumo["primeira_sincronizacao"] = True
    return resumo


# ── Vigia da pasta de entrada (roda em segundo plano quando ligado) ──────────
def _vigia_pasta_entrada():
    import time
    while True:
        try:
            cfg = _entrada_cfg()
            if cfg.get("auto") and _ler_registro():
                _rodar_pasta_entrada()
        except Exception:
            pass
        time.sleep(60)   # varre a cada 1 minuto


threading.Thread(target=_vigia_pasta_entrada, daemon=True).start()
