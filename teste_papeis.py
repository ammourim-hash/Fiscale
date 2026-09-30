#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes dos papéis `admin` e `operador`.

    python teste_papeis.py

O TESTE QUE IMPORTA MAIS
    A cobertura. `fiscale_papeis` é lista de PERMISSÃO: rota que ninguém
    classificou nasce restrita ao admin. Isso falha fechado, que é o certo —
    mas apodrece em silêncio, porque a rota nova só dá 403 para o operador, e
    a tentação é "consertar" afrouxando a regra.

    Por isso este arquivo varre o código de verdade — os decoradores `@app.*`
    de `nfse/backend/main.py` e as comparações de rota do `fiscale_server.py`
    — e FALHA quando encontra rota que `classificar()` não reconhece. Assim
    classificar deixa de ser lembrança e passa a ser parte de acrescentar
    rota.

    Se este teste falhar depois de você criar uma rota, ele não está errado:
    decida se ela é do operador ou do admin e declare em `fiscale_papeis.py`.
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import fiscale_papeis as pp  # noqa: E402

_ok = _falhas = 0
_erros: list[str] = []


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


# ── Descoberta de rotas no código ───────────────────────────────────────────

def rotas_do_modulo_nfse() -> set[tuple[str, str]]:
    """`@app.get("/api/notas")` -> ("GET", "/api/notas").

    O caminho com parâmetro (`/api/certificados/{cid}`) é cortado antes da
    chave, porque é assim que ele chega ao servidor: `/api/certificados/7`.
    """
    txt = (RAIZ / "nfse" / "backend" / "main.py").read_text("utf-8", "ignore")
    achadas = set()
    for metodo, caminho in re.findall(r'@app\.(get|post|put|delete)\(\s*["\']([^"\']+)["\']', txt):
        caminho = caminho.split("{", 1)[0].rstrip("/") or "/"
        achadas.add((metodo.upper(), caminho))
    return achadas


def rotas_do_servidor() -> set[tuple[str, str]]:
    """As comparações de rota do `fiscale_server.py`, por bloco `do_*`.

    O método vem do `def do_GET/do_POST/do_DELETE` em que a linha aparece —
    é o que permite dizer que `POST /api/usuarios` e `GET /api/usuarios` são
    rotas diferentes, como de fato são.

    O `\\s*` depois de `rota` não é enfeite: sem ele o padrão casa
    `rota == "..."` (que tem espaço) e perde `rota.startswith("...")` (que
    não tem). Na primeira versão deste teste foi exatamente o que aconteceu —
    `/api/state/`, `/dados/uploads/` e `/api/backup/` passaram despercebidos,
    e a cobertura deu verde sem cobrir.
    """
    txt = (RAIZ / "fiscale_server.py").read_text("utf-8", "ignore")
    metodo = None
    achadas = set()
    for linha in txt.splitlines():
        m = re.match(r"\s*def do_(GET|POST|DELETE|PUT)\b", linha)
        if m:
            metodo = m.group(1)
            continue
        if not metodo:
            continue
        for cam in re.findall(r'rota\s*(?:==|\.startswith\()\s*\(?["\']([^"\']+)["\']', linha):
            if cam.startswith("/"):
                achadas.add((metodo, cam))
    return achadas


def prefixos_proxy() -> list[str]:
    """`NFSE_PREFIXOS` — tudo sob eles é repassado ao módulo NFS-e.

    Não viram rotas para a cobertura: cruzar cada prefixo com GET/POST/DELETE
    inventaria coisas que não existem (`DELETE /api/notas`) e encheria a
    varredura de ruído. O que interessa neles é outra pergunta — se algum
    prefixo é um ponto cego, isto é, se abre caminho para o módulo NFS-e sem
    ter nenhuma rota classificada embaixo.
    """
    return list(prefixos_por_ast((RAIZ / "fiscale_server.py").read_text("utf-8")))


# LER A TUPLA PELO AST, NÃO PELO TEXTO
#     Até 13/09/2026 a tupla era recortada "até o primeiro `)`". Um comentário
#     com parênteses dentro dela (`# ISS a recolher (próprio + retido)`) cortou
#     a lista ao meio: /api/recife, /api/clientes, /api/prefeituras e /api/cte
#     deixaram de ser conferidos, e o teste seguiu verde com 3 asserções a
#     menos. O AST não enxerga comentário, e a contagem por `tokenize` abaixo
#     é a testemunha independente de que nada ficou para trás.
def prefixos_por_ast(fonte: str) -> tuple:
    for no in ast.parse(fonte).body:
        if (isinstance(no, ast.Assign)
                and any(isinstance(a, ast.Name) and a.id == "NFSE_PREFIXOS"
                        for a in no.targets)):
            return tuple(ast.literal_eval(no.value))
    return ()


def contar_strings_por_tokenize(fonte: str, nome: str) -> int:
    """Quantos literais de texto há entre `nome = (` e o `)` que o fecha."""
    toks = list(tokenize.generate_tokens(io.StringIO(fonte).readline))
    for i, t in enumerate(toks):
        if t.type == tokenize.NAME and t.string == nome and toks[i + 1].string == "=":
            nivel, qtd = 0, 0
            for u in toks[i + 2:]:
                if u.type == tokenize.OP and u.string in "([{":
                    nivel += 1
                elif u.type == tokenize.OP and u.string in ")]}":
                    nivel -= 1
                    if nivel == 0:
                        return qtd
                elif u.type == tokenize.STRING:
                    qtd += 1
    return -1


# Rotas que existem no código mas não passam pelo portão de papéis, porque são
# resolvidas ANTES do login. Cada uma com o motivo — a lista é curta de
# propósito, e crescer nela deve doer.
FORA_DO_PORTAO = {
    ("GET", "/api/primeiro-acesso"),    # a tela de login pergunta se há usuário
    ("POST", "/api/primeiro-acesso"),   # cria o admin; exige estar no PC
    ("POST", "/api/login"),             # é o próprio login
    ("GET", "/"),                       # redireciona para a Home
    # `rota.startswith("/api/")` dentro do próprio portão de login: é o teste
    # que decide entre 401 e redirecionar para a tela. Não é rota.
    ("GET", "/api/"),
}


secao("Cobertura: toda rota do código está classificada")

descobertas = rotas_do_modulo_nfse() | rotas_do_servidor()
ok(len(descobertas) > 50, f"a varredura achou rotas de verdade ({len(descobertas)})")
ok(("POST", "/api/state/") in descobertas and ("GET", "/dados/uploads/") in descobertas,
   "e enxerga também os `rota.startswith(...)`, não só os `rota == ...`")

nao_classificadas = sorted(
    (m, r) for (m, r) in descobertas
    if (m, r) not in FORA_DO_PORTAO and pp.classificar(m, r) is None
)
ok(not nao_classificadas,
   "nenhuma rota ficou sem papel declarado"
   + ("" if not nao_classificadas else
      "\n         declare em fiscale_papeis.py: "
      + ", ".join(f"{m} {r}" for m, r in nao_classificadas)))


secao("Todo módulo de estado usado pelas telas está classificado")

# `/api/state/<mod>` não aparece na varredura de rotas com o nome do módulo —
# no servidor é um `startswith` só. Quem sabe os nomes são as telas, e é nelas
# que este teste procura. Foi assim que `nfe_vendas` apareceu: existia desde
# sempre e ninguém o tinha classificado.
web = RAIZ / "web"
modulos = set()
for arq in list(web.glob("*.html")) + list(web.glob("*.js")):
    txt = arq.read_text("utf-8", "ignore")
    modulos |= set(re.findall(r"""Fiscale\.(?:salvar|carregar)\(\s*["']([A-Za-z0-9_-]+)["']""", txt))

# O piso era 5 quando existiam as telas de Plano de Saúde e e-CAC. Elas saíram
# na FISCAL AI 1, e o piso desceu com elas. O número não é o que importa aqui —
# o que importa é a varredura NÃO voltar vazia: vazia, o teste abaixo passaria
# sem conferir nada, que é o defeito que ele existe para impedir.
# O PISO DESCEU DE NOVO, e a razão está registrada: a aba de Vendas saiu do
# `nfe.html` em 07/09/2026, junto com a captura manual, e ela era a única tela
# que gravava `nfe_vendas`. O módulo continua DECLARADO em `fiscale_papeis` —
# declaração de rota que ninguém chama é inofensiva, e retirá-la faria o módulo
# renascer restrito ao admin sem que ninguém tivesse decidido isso.
#
# O numero nao e o que importa: o que importa e a varredura NAO voltar vazia.
# Vazia, o teste abaixo passaria sem conferir nada.
ok(len(modulos) >= 2, f"a varredura achou os módulos de estado das telas ({len(modulos)})")
igual(sorted(modulos), ["classificador", "clientes"],
      "e são exatamente os que as telas de hoje usam")
for mod in sorted(modulos):
    declarado = mod in pp._ESTADO_ESCRITA_OPERADOR
    papel = pp.classificar("POST", f"/api/state/{mod}")
    ok(declarado or papel == pp.ADMIN,
       f"state/{mod}: escrita declarada ({'operador' if declarado else 'admin'})")


secao("Módulo de estado novo não nasce liberado")
igual(pp.classificar("POST", "/api/state/inventado_ontem"), pp.ADMIN,
      "módulo que ninguém declarou fica restrito ao admin (falha fechado)")
ok(pp.pode(pp.OPERADOR, "GET", "/api/state/inventado_ontem"),
   "mas continua legível: esconder dado do operador não é o objetivo")


secao("Nenhum prefixo do proxy é ponto cego")
reais = rotas_do_modulo_nfse()
for pref in prefixos_proxy():
    sob = [(m, r) for (m, r) in reais if r == pref or r.startswith(pref + "/")]
    if not sob:
        # Prefixo que não tem rota nenhuma embaixo: o repasse cai em 404 no
        # módulo NFS-e. Não é furo de segurança, mas é lixo — e o teste diz.
        ok(pp.classificar("GET", pref) is not None,
           f"{pref} não tem rota no módulo (prefixo morto), mas tem papel declarado")
        continue
    ok(all(pp.classificar(m, r) is not None for m, r in sob),
       f"{pref}: as {len(sob)} rotas sob ele estão classificadas")


secao("O operador faz o trabalho fiscal")
for metodo, rota in [
    ("GET", "/api/notas"), ("GET", "/api/apuracao-federal"),
    ("GET", "/api/apuracao-trimestral"), ("GET", "/api/vencimentos"),
    ("GET", "/api/danfse"), ("GET", "/api/nfse/xml"),
    ("POST", "/api/sincronizar"), ("POST", "/api/nfe/sincronizar"),
    #  saiu em 07/09/2026: a importacao manual de
    # pasta foi aposentada quando a captura virou automatica.
    ("POST", "/api/nfe/importar"),
    ("POST", "/api/prefeituras/sincronizar"), ("POST", "/api/exportar"),
    ("POST", "/api/classificador"), ("GET", "/api/certificados"),
    ("GET", "/api/certificados/pendentes"), ("GET", "/api/pastas"),
    ("GET", "/api/quem"), ("POST", "/api/senha"),
    ("GET", "/home_portal.html"), ("GET", "/fiscale.js"),
]:
    ok(pp.pode(pp.OPERADOR, metodo, rota), f"operador alcança {metodo} {rota}")


secao("O operador não mexe em certificado, usuário nem configuração")
for metodo, rota in [
    ("POST", "/api/certificados"), ("POST", "/api/certificados/lote"),
    ("POST", "/api/certificados/senha"), ("POST", "/api/certificados/procurador"),
    ("DELETE", "/api/certificados/7"),
    ("GET", "/api/usuarios"), ("POST", "/api/usuarios"),
    ("POST", "/api/usuarios/excluir"),
    ("GET", "/api/backup/situacao"), ("POST", "/api/backup/criar"),
    ("POST", "/api/backup/restaurar"), ("GET", "/api/backup/baixar"),
    ("GET", "/api/diagnostico"),
    ("POST", "/api/elo/sync"), ("POST", "/api/elo/sync/tentar"),
    ("POST", "/api/nfe/entrada/config"), ("POST", "/api/abrir-pasta"),
    ("GET", "/usuarios.html"), ("GET", "/backup.html"),
]:
    ok(not pp.pode(pp.OPERADOR, metodo, rota), f"operador NÃO alcança {metodo} {rota}")
    ok(pp.pode(pp.ADMIN, metodo, rota), f"  mas o admin alcança")


secao("A exceção específica vence o prefixo largo")
ok(pp.pode(pp.OPERADOR, "POST", "/api/nfe/importar"),
   "POST /api/nfe/ em geral é do operador")
ok(not pp.pode(pp.OPERADOR, "POST", "/api/nfe/entrada/config"),
   "mas a pasta vigiada, dentro do mesmo prefixo, não é")
ok(pp.pode(pp.OPERADOR, "GET", "/api/certificados"),
   "listar empresas é do operador")
ok(not pp.pode(pp.OPERADOR, "POST", "/api/certificados"),
   "cadastrar empresa não é")


secao("Estado de tela: leitura para todos, escrita conforme o módulo")
for mod in ("clientes", "classificador"):
    ok(pp.pode(pp.OPERADOR, "GET", f"/api/state/{mod}"),
       f"operador lê state/{mod}")
for mod in ("classificador", "nfe_vendas"):
    ok(pp.pode(pp.OPERADOR, "POST", f"/api/state/{mod}"),
       f"operador grava state/{mod} (trabalho e preferência de tela)")
for mod in ("clientes",):
    ok(not pp.pode(pp.OPERADOR, "POST", f"/api/state/{mod}"),
       f"operador NÃO grava state/{mod} (cadastro/credencial)")


secao("Rota desconhecida nasce restrita")
igual(pp.classificar("POST", "/api/inventada-ontem"), None,
      "classificar() acusa que ninguém a declarou")
ok(not pp.pode(pp.OPERADOR, "POST", "/api/inventada-ontem"),
   "e o operador não a alcança")
ok(pp.pode(pp.ADMIN, "POST", "/api/inventada-ontem"),
   "o admin alcança (falha fechado, não travada)")


secao("O papel sai do cadastro como ele já é hoje")
igual(pp.papel_do_registro({"admin": True}, "joao"), pp.ADMIN, "marca explícita manda")
igual(pp.papel_do_registro({"admin": False}, "admin"), pp.OPERADOR,
      "e a marca explícita também manda quando é False")
igual(pp.papel_do_registro({}, "admin"), pp.ADMIN,
      "o usuário 'admin' segue admin sem marca (regra que já valia)")
igual(pp.papel_do_registro({}, "maria"), pp.OPERADOR,
      "quem não tem marca vira operador")
igual(pp.papel_do_registro(None, "maria"), pp.OPERADOR, "cadastro ausente também")



# ── Rota que nasce aqui sob um prefixo repassado ao NFS-e ──────────────────
#
# O DEFEITO QUE ISTO FIXA
#     `/api/clientes` está entre os prefixos repassados ao módulo NFS-e. Três
#     rotas novas — `/api/clientes/modelo` e as duas de importação de cadastro —
#     foram escritas no `fiscale_server.py` e NUNCA eram alcançadas: o proxy
#     respondia primeiro, o módulo não conhecia o caminho, e o navegador levava
#     404 sem nenhuma pista de que a rota existia deste lado.
#
#     Um 404 é indistinguível de "rota não escrita". Por isso a checagem tem de
#     morar aqui: quem acrescentar rota sob prefixo repassado descobre na suíte,
#     e não na tela do escritório.
secao("Rota local sob prefixo do NFS-e precisa estar na lista de exceções")
_fonte = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8")

_desviados = prefixos_por_ast(_fonte)
ok(len(_desviados) > 5, "achei %d prefixo(s) repassado(s)" % len(_desviados))
# Importar o servidor para contar de verdade não serve: no topo ele cria pastas
# de dados, migra e abre sessões. A testemunha é o tokenize, que separa
# comentário de literal — e prefixos conhecidos, do meio e do fim da tupla.
igual(len(_desviados), contar_strings_por_tokenize(_fonte, "NFSE_PREFIXOS"),
      "o AST leu todos os literais da tupla (contagem conferida por tokenize)")
_conhecidos = ["/api/recife", "/api/clientes", "/api/prefeituras", "/api/cte",
               "/api/iss-recolher", "/favicon.ico"]
igual([p for p in _conhecidos if p not in _desviados], [],
      "prefixos conhecidos estão todos na leitura (inclusive o último)")

_i = _fonte.index("NFSE_EXCECOES = frozenset({")
_excecoes = set(re.findall(r'"(/[^"]+)"', _fonte[_i:_fonte.index("})", _i)]))
ok(_excecoes, "a lista de exceções existe e tem %d rota(s)" % len(_excecoes))

_locais = set(re.findall(r'if rota == "(/api/[^"]+)"', _fonte))
ok(len(_locais) > 20, "achei %d rotas declaradas no servidor" % len(_locais))

_engolidas = sorted(r for r in _locais
                    if r.startswith(_desviados) and r not in _excecoes)
igual(_engolidas, [], "nenhuma rota local é engolida pelo proxy do NFS-e")

# E o contrário: exceção que não corresponde a rota nenhuma é lixo que engana
# quem for ler a lista depois.
_sobrando = sorted(r for r in _excecoes if r not in _locais)
igual(_sobrando, [], "e nenhuma exceção aponta para rota que não existe")

# A exceção tem de ser consultada ANTES do teste de prefixo — depois não serve.
_i = _fonte.index("def _rota_nfse")
_corpo = _fonte[_i:_fonte.index("def _proxy_nfse", _i)]
ok(_corpo.index("NFSE_EXCECOES") < _corpo.index("startswith(NFSE_PREFIXOS)"),
   "e é consultada antes do teste de prefixo")

print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Todos os testes de papéis passaram.")
