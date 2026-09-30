#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Porta única para o NFeDistribuicaoDFe (ING 4D-3B).

    python teste_ingestao4d3b.py

O QUE ORIGINOU ESTA SUÍTE
    Existiam TRÊS caminhos capazes de abrir uma sessão real com a Distribuição
    DF-e: o controlador (governado), o utilitário manual (auditado, sem
    cadência) e a rota `POST /api/nfe/sincronizar`, que chamava o `nfe.py`
    legado — checkpoint próprio, sem ato `CONSULTA`, sem cooldown.

    Nenhum causou os `656` de 17/08/2026 (conferido pelos carimbos), mas
    enquanto três portas existirem a frase "toda chamada real é governada e
    auditável" é falsa por construção.

O QUE ESTA SUÍTE GARANTE
    Que a política mora em UM lugar, que nenhum consumidor consegue
    contorná-la, e que uma rota nova escrita amanhã por engano **quebra o
    teste** em vez de quebrar a produção.

REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import auditoria as aud              # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import controlador as ctl            # noqa: E402
from ingestao import operacao as op                # noqa: E402
from ingestao import servico_distribuicao as svc   # noqa: E402
from ingestao import trava as tv                   # noqa: E402
from ingestao.ambiente import PRODUCAO             # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402
from ingestao.conectores import nfe_dfe as N       # noqa: E402
from ingestao.identidade import normalizar         # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []

import socket as _socket                           # noqa: E402
_TENTATIVAS: list[str] = []
_connect_original = _socket.socket.connect


def _proibido(self, endereco, *a, **kw):
    _TENTATIVAS.append(str(endereco))
    raise AssertionError(f"teste tentou abrir conexão de rede para {endereco}")


_socket.socket.connect = _proibido


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
    ok(a == b, desc if a == b else f"{desc}  (obtive {a!r}, esperava {b!r})")


# Instante de referência NO FUTURO em relação ao relógio da máquina — mesma
# razão explicada em `teste_ingestao4c.py`: o produto conta a janela do 656 a
# partir do MAIOR entre o carimbo real da resposta e este `agora`. Data fixa
# vira bomba-relógio no dia em que o calendário a alcança.
AGORA = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
SERV = cpm.NFE_DISTRIBUICAO
SENHA = "frase-ficticia-4d3b"
BACKEND = RAIZ / "nfse" / "backend"

E_OK = T.cnpj_ficticio("111111110001")
E_OUTRA = T.cnpj_ficticio("222222220001")
E_DIVERGENTE = T.cnpj_ficticio("444444440001")
E_EXPIRADO = T.cnpj_ficticio("555555550001")
E_SEM_CRED = T.cnpj_ficticio("888888880001")

POL = ctl.Politica(usar_trava=True, max_lotes_por_empresa=1)


def cadastrar(raiz, cnpj, dias=365):
    import seguranca
    certs = Path(raiz) / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    arq = certs / f"{cnpj}.pfx"
    apoio.gerar_pfx(arq, SENHA, cn=f"EMPRESA {cnpj[:4]}", dias_validade=dias)
    reg = Path(raiz) / "certificados.json"
    atual = json.loads(reg.read_text("utf-8")) if reg.exists() else []
    atual.append({"id": cnpj, "cnpj": cnpj, "nome": f"EMPRESA {cnpj[:4]}",
                  "caminho": f"certs/{cnpj}.pfx",
                  "senha_protegida": seguranca.proteger(SENHA, raiz)})
    reg.write_text(json.dumps(atual, ensure_ascii=False), encoding="utf-8")


def cenario(raiz):
    for c in (E_OK, E_OUTRA, E_DIVERGENTE):
        cadastrar(raiz, c)
    cadastrar(raiz, E_EXPIRADO, dias=-10)
    repo = RepositorioCheckpoint(raiz)
    for c in (E_OK, E_OUTRA, E_EXPIRADO):
        cp = repo.carregar(c, SERV, PRODUCAO)
        cp.ult_nsu = "000000000000100"
        cp.max_nsu = "000000000000150"
        cp.status = cpm.OK
        cp.origem_ult_nsu = cpm.ORIGEM_LEGADO
        cp.cobertura_anterior = cpm.COBERTURA_ACERVO_LEGADO
        repo.salvar(cp)
    cp = repo.carregar(E_DIVERGENTE, SERV, PRODUCAO)
    cp.registrar_observacao_sefaz("000000000001361", cstat="656")
    cp.marcar_possivel_consumidor_externo("teste")
    repo.salvar(cp)
    # state_clientes: empresa conhecida sem credencial vinculada
    (Path(raiz) / "state_clientes.json").write_text(
        json.dumps({"clientes": [{"cnpj": E_SEM_CRED, "nome": "SEM CREDENCIAL"}]}),
        encoding="utf-8")


def resposta(cstat="137", motivo="Nenhum documento localizado",
             ult="000000000000100", maximo="000000000000100"):
    return f"""<?xml version="1.0"?><soap:Envelope
 xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<retDistDFeInt xmlns="{N.NS_NFE}" versao="1.01"><tpAmb>1</tpAmb>
<cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo>
<ultNSU>{ult}</ultNSU><maxNSU>{maximo}</maxNSU>
</retDistDFeInt></soap:Body></soap:Envelope>""".encode("utf-8")


class Transporte:
    """Dublê que CONTA chamadas. Zero chamada é a prova que interessa aqui."""

    def __init__(self, corpo=None):
        self.corpo = corpo or resposta()
        self.chamadas = []

    def para(self, cnpj):
        t = self

        class _T:
            def enviar(_s, url, corpo):
                t.chamadas.append(cnpj)
                return t.corpo
        return _T()


def fabrica(trans):
    def _f(identidade, ambiente):
        return N.FonteNFeDistribuicaoDFe(identidade=identidade,
                                         transporte=trans.para(identidade),
                                         ambiente=ambiente, cuf="26")
    return _f


# ══════════════════════════════════════════════════════════════════════════
secao("A porta única aplica a política antes de existir transporte")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    r = svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL, agora=AGORA)
    ok(r.autorizada, "empresa elegível é autorizada")
    ok(r.consultada, "e a consulta acontece")
    igual(tr.chamadas, [E_OK], "com exatamente uma chamada de transporte")
    igual(r.decisao, svc.AUTORIZADA, "decisão registrada")
    e = op.RepositorioOperacao(raiz).carregar(E_OK, SERV, PRODUCAO)
    ok(e.proxima_consulta_permitida_em, "o cooldown foi gravado")
    trilha = [x for x in aud.abrir(raiz, E_OK).consultas()
              if x.get("servico") == SERV]
    igual(len(trilha), 1, "e a trilha tem UMA CONSULTA — o ato canônico")

secao("Empresa em cooldown: zero transporte, motivo explícito")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL, agora=AGORA)
    r = svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL,
                              agora=AGORA + timedelta(minutes=5))
    ok(not r.autorizada, "a segunda chamada é recusada")
    igual(r.motivo, ctl.MOTIVO_COOLDOWN, "com motivo de cooldown")
    ok(r.segundos_para_liberar > 0, "e o tempo que falta")
    igual(len(tr.chamadas), 1, "nenhuma chamada extra saiu")

secao("Empresa em REVISAO_DE_SEQUENCIA: zero transporte, mesmo por comando")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    repo = op.RepositorioOperacao(raiz)
    e = repo.carregar(E_OK, SERV, PRODUCAO)
    e.exigir_revisao_de_sequencia("656 de sequência")
    repo.salvar(e)
    tr = Transporte()
    r = svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL,
                              agora=AGORA + timedelta(days=30))
    ok(not r.autorizada, "recusada mesmo 30 dias depois")
    igual(r.estado, op.REVISAO_DE_SEQUENCIA, "com o estado certo")
    igual(tr.chamadas, [], "e ZERO transporte")

secao("MONTE (divergência externa): zero transporte")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    r = svc.consultar_empresa(raiz, E_DIVERGENTE, fabrica(tr), politica=POL,
                              agora=AGORA)
    ok(not r.autorizada, "recusada")
    igual(r.estado, op.DIVERGENCIA_EXTERNA, "por divergência externa")
    igual(tr.chamadas, [], "e ZERO transporte")

secao("Certificado expirado e credencial ausente: zero transporte")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    r1 = svc.consultar_empresa(raiz, E_EXPIRADO, fabrica(tr), politica=POL,
                               agora=AGORA)
    r2 = svc.consultar_empresa(raiz, E_SEM_CRED, fabrica(tr), politica=POL,
                               agora=AGORA)
    igual(r1.estado, op.CERTIFICADO_EXPIRADO, "certificado vencido barra")
    igual(r2.estado, op.CREDENCIAL_INDISPONIVEL, "credencial ausente barra")
    igual(tr.chamadas, [], "nenhum transporte nos dois casos")

secao("Empresa inexistente: erro claro, sem rede")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    r = svc.consultar_empresa(raiz, T.cnpj_ficticio("777777770001"),
                              fabrica(tr), politica=POL, agora=AGORA)
    ok(not r.autorizada, "recusada")
    igual(tr.chamadas, [], "sem transporte")
    r2 = svc.consultar_empresa(raiz, "nao-e-cnpj", fabrica(tr), politica=POL,
                               agora=AGORA)
    ok(not r2.autorizada, "identificador inválido também é recusado")
    igual(tr.chamadas, [], "e continua sem transporte")

# ══════════════════════════════════════════════════════════════════════════
secao("Concorrência: a trava impede duas consultas na mesma empresa")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    # Simula o controlador já dentro da unidade: a trava está tomada.
    with tv.travar(raiz, E_OK, SERV, PRODUCAO):
        r = svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL,
                                  agora=AGORA)
    ok(not r.autorizada, "o segundo consumidor é recusado")
    igual(r.estado, op.EM_EXECUCAO, "com o estado de execução em andamento")
    igual(r.motivo, ctl.MOTIVO_TRAVA, "e o motivo da trava")
    igual(tr.chamadas, [], "nenhuma segunda chamada de rede")

secao("Solta a trava, a mesma empresa volta a ser consultável")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    with tv.travar(raiz, E_OK, SERV, PRODUCAO):
        pass
    r = svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL, agora=AGORA)
    ok(r.autorizada, "depois de solta, autoriza")
    igual(len(tr.chamadas), 1, "uma chamada")

secao("A trava de uma empresa não bloqueia outra")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    with tv.travar(raiz, E_OK, SERV, PRODUCAO):
        r = svc.consultar_empresa(raiz, E_OUTRA, fabrica(tr), politica=POL,
                                  agora=AGORA)
    ok(r.autorizada, "a outra empresa segue normalmente")
    igual(tr.chamadas, [E_OUTRA], "e só ela chamou")

# ══════════════════════════════════════════════════════════════════════════
secao("O ciclo usa a MESMA porta — não reimplementa política")
_fonte_ctl = (BACKEND / "ingestao" / "controlador.py").read_text("utf-8")
arvore = ast.parse(_fonte_ctl)
chama_ingerir = [n for n in ast.walk(arvore) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", "") == "ingerir"]
igual(chama_ingerir, [],
      "o controlador não chama pipeline.ingerir por conta própria")
chama_servico = [n for n in ast.walk(arvore) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", "") == "consultar_empresa"]
ok(len(chama_servico) >= 1, "ele chama consultar_empresa da porta única")
ok("registrar_656" not in _fonte_ctl,
   "e o tratamento do 656 não está duplicado nele")

secao("A porta única é o único módulo que monta transporte real")
_AUTORIZADOS = {
    "ingestao/servico_distribuicao.py",   # a porta
    "ingestao/conectores/nfe_dfe.py",     # o conector, que ela usa
}
proibidos = []
for arq in sorted(BACKEND.rglob("*.py")):
    rel = arq.relative_to(BACKEND).as_posix()
    if rel in _AUTORIZADOS or "__pycache__" in rel:
        continue
    fonte = arq.read_text("utf-8", errors="replace")
    try:
        arv = ast.parse(fonte)
    except SyntaxError:
        continue
    for nodo in ast.walk(arv):
        if not isinstance(nodo, ast.Call):
            continue
        nome = getattr(nodo.func, "attr", "") or getattr(nodo.func, "id", "")
        # `N.criar` e `FonteNFeDistribuicaoDFe(...)` constroem a Fonte com
        # transporte mTLS — é aí que uma chamada real nasce.
        if nome in ("FonteNFeDistribuicaoDFe", "criar") and "nfe" in fonte[:2000].lower():
            if nome == "criar" and "N.criar" not in fonte:
                continue
            proigual = f"{rel}:{nodo.lineno}"
            proibidos.append(proigual)
igual(proibidos, [],
      "nenhum módulo do backend monta a Fonte NF-e fora da porta única")

secao("A rota HTTP não fala com a SEFAZ por conta própria")
_fonte_main = (BACKEND / "main.py").read_text("utf-8")
ok("nfemod.sincronizar_nfe" not in _fonte_main,
   "main.py não chama mais o sincronizar_nfe legado")
ok("_svc_nfe.consultar_empresa" in _fonte_main,
   "e chama a porta única")
arv_main = ast.parse(_fonte_main)
usos_legado = [n.lineno for n in ast.walk(arv_main) if isinstance(n, ast.Call)
               and getattr(n.func, "attr", "") == "sincronizar_nfe"]
igual(usos_legado, [], "nenhuma chamada ao caminho de rede legado sobrou")

secao("O caminho legado está bloqueado, não apenas desligado")
import nfe as _nfe                                  # noqa: E402
try:
    _nfe.sincronizar_nfe("x.pfx", "senha", "/tmp", "00000000000000")
    ok(False, "sincronizar_nfe deveria ter levantado")
except _nfe.CaminhoDesativado as exc:
    ok("porta única" in str(exc), "levanta CaminhoDesativado com a razão")
except Exception as exc:
    ok(False, f"levantou o erro errado: {type(exc).__name__}")
igual(_TENTATIVAS, [], "e não tentou rede ao ser chamado")

secao("O legado continua útil para o que NÃO é rede")
for funcao in ("carregar_nfe", "importar_xmls", "processar_pasta",
               "distribuir_entrada", "carregar_cte"):
    ok(callable(getattr(_nfe, funcao, None)),
       f"{funcao}() continua disponível — só o caminho de rede foi fechado")

secao("A ferramenta manual também passa pela porta")
_fonte_manual = (RAIZ / "consulta_real_nfe.py").read_text("utf-8")
ok("svc.consultar_empresa" in _fonte_manual,
   "consulta_real_nfe.py chama a porta única")
arv_manual = ast.parse(_fonte_manual)
ingerir_manual = [n.lineno for n in ast.walk(arv_manual) if isinstance(n, ast.Call)
                  and getattr(n.func, "attr", "") == "ingerir"]
igual(ingerir_manual, [],
      "e não chama pipeline.ingerir direto, que pularia a política")
ok("RECUSADO PELA POLÍTICA" in _fonte_manual,
   "ela sabe informar quando a política recusa")
ok("override" in _fonte_manual.lower(),
   "e diz explicitamente que confirmação humana não é override")

# ══════════════════════════════════════════════════════════════════════════
secao("Checkpoint: o legado nunca governa a distribuição")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    # Um estado legado propositalmente divergente, como existe em disco hoje.
    legado = Path(raiz) / E_OK / "nfe"
    legado.mkdir(parents=True, exist_ok=True)
    (legado / "estado.json").write_text(
        json.dumps({"ultNSU": 999999, "maxNSU": 999999}), encoding="utf-8")
    antes = (legado / "estado.json").read_bytes()
    tr = Transporte()
    svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL, agora=AGORA)
    trilha = [x for x in aud.abrir(raiz, E_OK).consultas()
              if x.get("servico") == SERV]
    igual(trilha[-1]["nsu_enviado"], "000000000000100",
          "o NSU enviado veio do checkpoint NOVO, não do legado (999999)")
    igual((legado / "estado.json").read_bytes(), antes,
          "e o estado legado ficou intacto — evidência histórica preservada")

secao("Nenhum módulo do motor lê o ARQUIVO de estado legado")
# Cuidado: `ultNSU` também é o nome da TAG XML da resposta da SEFAZ, e o
# conector precisa dela. O que não pode existir é leitura do ARQUIVO
# `<cnpj>/nfe/estado.json`, que é o checkpoint paralelo.
_LEGADO_OK = {"migracao_legado.py"}    # migra o legado de propósito, uma vez só


def _literais_de_codigo(fonte: str) -> list:
    """Strings que o código USA — docstrings e comentários ficam de fora.

    A distinção importa: vários módulos EXPLICAM o estado legado na docstring,
    e explicar não é ler. Procurar no texto puro acusaria a documentação."""
    arv = ast.parse(fonte)
    docs = set()
    for nodo in ast.walk(arv):
        corpo = getattr(nodo, "body", None)
        if isinstance(corpo, list) and corpo and isinstance(corpo[0], ast.Expr) \
                and isinstance(getattr(corpo[0], "value", None), ast.Constant) \
                and isinstance(corpo[0].value.value, str):
            docs.add(id(corpo[0].value))
    return [n.value for n in ast.walk(arv)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


for arq in sorted((BACKEND / "ingestao").rglob("*.py")):
    if arq.name in _LEGADO_OK or "__pycache__" in arq.parts:
        continue
    lits = _literais_de_codigo(arq.read_text("utf-8", errors="replace"))
    ok(not any("estado.json" in s for s in lits),
       f"{arq.name} não ABRE o arquivo de estado legado")
_mig = _literais_de_codigo(
    (BACKEND / "ingestao" / "migracao_legado.py").read_text("utf-8"))
ok(any("estado.json" in s for s in _mig),
   "só a migração conhece o arquivo legado — e ela existe para isso")
ok("ultNSU" in (BACKEND / "ingestao" / "conectores" / "nfe_dfe.py").read_text("utf-8"),
   "o conector usa 'ultNSU' como TAG da resposta, o que é outra coisa")

secao("Auditoria: um formato só, o ato canônico CONSULTA")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL, agora=AGORA)
    linha = [x for x in aud.abrir(raiz, E_OK).consultas()
             if x.get("servico") == SERV][-1]
    for campo in aud.CAMPOS_CONSULTA:
        ok(campo in linha, f"a linha traz o campo canônico '{campo}'")
    igual(linha["ambiente"], "producao", "com o ambiente correto")

secao("Consulta recusada NÃO polui a trilha com ato de consulta")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte()
    svc.consultar_empresa(raiz, E_DIVERGENTE, fabrica(tr), politica=POL,
                          agora=AGORA)
    consultas = [x for x in aud.abrir(raiz, E_DIVERGENTE).consultas()
                 if x.get("servico") == SERV]
    igual(consultas, [], "nada foi consultado, nada foi registrado como consulta")

secao("656 pela porta única mantém o modelo da 4D-3A")
MOT_SEQ = ("Rejeicao: Consumo Indevido (Deve ser utilizado o ultNSU nas "
           "solicitacoes subsequentes. Tente apos 1 hora)")
with apoio.raiz_temporaria("i4d3b_") as raiz:
    cenario(raiz)
    tr = Transporte(resposta("656", MOT_SEQ, "000000000000100",
                             "000000000000000"))
    r = svc.consultar_empresa(raiz, E_OK, fabrica(tr), politica=POL, agora=AGORA)
    igual(r.subtipo, N.SEQUENCIA_656, "o subtipo continua sendo classificado")
    igual(r.estado, op.REVISAO_DE_SEQUENCIA, "e vira revisão de sequência")
    e = op.RepositorioOperacao(raiz).carregar(E_OK, SERV, PRODUCAO)
    ok(e.revisao_sequencia, "com a trava que o tempo não abre")
    cp = RepositorioCheckpoint(raiz).carregar(E_OK, SERV, PRODUCAO)
    igual(cp.ult_nsu, "000000000000100", "checkpoint não andou")
    igual(cp.max_nsu, "000000000000150", "e o maxNSU válido não foi apagado")

# ══════════════════════════════════════════════════════════════════════════
secao("Nenhum teste desta suíte foi à rede")
igual(_TENTATIVAS, [], "nenhuma conexão de rede foi tentada")
try:
    _socket.socket().connect(("127.0.0.1", 9))
    ok(False, "a trava de rede não estava ativa")
except AssertionError as exc:
    ok("conexão de rede" in str(exc), "e a trava estava mesmo ativa (conferido)")
finally:
    _TENTATIVAS.clear()
    _socket.socket.connect = _connect_original

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("ING 4D-3B: todos os testes passaram.")
