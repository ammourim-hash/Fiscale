#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cadastro de clientes: a busca, o que o certificado entrega e o regime.

    python teste_clientes_busca.py

POR QUE ESTE TESTE EXISTE

    1. A caixa "Buscar por nome ou CNPJ" não filtrava NADA. O predicado era

           nome.includes(termo) || soDig(cnpj).includes(soDig(termo))

       e `soDig('monte')` é string vazia — `includes('')` é sempre verdadeiro.
       O segundo lado do OU aprovava a carteira inteira, qualquer que fosse o
       termo. O campo existia, respondia ao teclado e nunca escondia ninguém;
       por isso passou tanto tempo sem ser notado.

    2. O início de atividade NÃO pode sair do certificado digital. O e-CNPJ
       ICP-Brasil não guarda a abertura da empresa: o que ele tem no OID
       2.16.76.1.3.4 é a data de NASCIMENTO do responsável. Conferido nos
       certificados reais do escritório — a MONTE traz 01/01/1990 (o mesmo
       valor que aparece no e-CPF do responsável) contra abertura real em
       19/10/2000; a EDU NORTE traz 23/09/1975 contra 04/11/2014. Usar essa
       data jogaria o início de atividade décadas para trás e desligaria em
       silêncio a proporcionalização do RBT12.

    3. O que o certificado REALMENTE entrega tem uma armadilha de formato: o
       conteúdo vem em DER, com um byte de tamanho antes do texto. Quando o
       nome do responsável tem 33 letras esse byte é 0x21 — que é "!" — e o
       leitor devolvia "!ETIENE DA CONCEICAO SILVA DE MELO".

    4. O regime vindo da base pública só marcava "Simples Nacional" quando a
       empresa era optante. MEI nunca era marcado, e uma empresa cadastrada
       como Simples que a Receita diz NÃO ser optante continuava como Simples
       para sempre — com prévia de DAS e tudo.

O QUE ESTE TESTE **NÃO** FAZ
    Não vai à rede e não lê a pasta de dados real: o certificado é gerado aqui
    e a resposta da base pública é simulada. A parte de JavaScript roda no Node
    quando ele existe na máquina; sem Node, é pulada com aviso.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

CLIENTES_HTML = RAIZ / "web" / "clientes.html"
SERVIDOR_PY = RAIZ / "fiscale_server.py"

_ok = _falhas = 0
_erros: list[str] = []


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


def _node() -> str:
    return shutil.which("node") or ""


def _rodar_node(script: str):
    with tempfile.TemporaryDirectory(prefix="fiscale_teste_busca_") as d:
        arq = Path(d) / "trecho.mjs"
        arq.write_text(script, "utf-8")
        r = subprocess.run([_node(), str(arq)], capture_output=True, text=True,
                           encoding="utf-8")
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:400])
        return json.loads(r.stdout)


def _funcao_do_html(assinatura: str) -> str:
    """Recorta uma função do clientes.html para rodá-la isolada no Node."""
    txt = CLIENTES_HTML.read_text("utf-8")
    i = txt.index(assinatura)
    j = txt.index("\n}", i) + 2
    return txt[i:j]


def importados_no_modulo(arquivo: Path) -> set[str]:
    """Nomes importados no NÍVEL DO MÓDULO (não dentro de função)."""
    import ast
    nomes = set()
    for no in ast.parse(arquivo.read_text("utf-8")).body:
        if isinstance(no, ast.Import):
            for a in no.names:
                nomes.add((a.asname or a.name).split(".")[0])
        elif isinstance(no, ast.ImportFrom):
            for a in no.names:
                nomes.add(a.asname or a.name)
        elif isinstance(no, ast.Try):          # import protegido por try/except
            for corpo in (no.body, *(m.body for m in no.handlers), no.orelse):
                for sub in corpo:
                    if isinstance(sub, ast.Import):
                        for a in sub.names:
                            nomes.add((a.asname or a.name).split(".")[0])
                    elif isinstance(sub, ast.ImportFrom):
                        for a in sub.names:
                            nomes.add(a.asname or a.name)
    return nomes


def _leitor_de_certificado():
    """Carrega `ler_cnpj_certificado` do servidor sem subir o servidor.

    O namespace é montado a partir dos imports REAIS do `fiscale_server`, nunca
    escrito à mão. Isso importa: a versão anterior injetava `import re` de
    própria conta, e o servidor NÃO tinha esse import. A função passava aqui e
    estourava `NameError` em produção — engolido pelo `except`, virando "não
    consegui ler o CNPJ — confira a senha" com a senha CERTA. Nenhum
    certificado novo entrava no cadastro.

    Derivando do módulo, o dia em que um import sumir de lá ele some daqui
    também, e o teste cai junto. Um harness mais generoso que o real não testa
    o real.
    """
    import ast

    txt = SERVIDOR_PY.read_text("utf-8")
    trecho = txt[txt.index("OID_RESPONSAVEL_NOME"):txt.index("def parse_multipart")]
    usados = {m for m in __import__("re").findall(r"\b([a-z_][a-z0-9_]*)\.[a-z_]", trecho)}

    # só os imports do módulo que fornecem nomes que o trecho usa — assim o
    # teste não carrega o servidor inteiro nem dispara efeito colateral de
    # import de módulo do projeto.
    linhas = []
    for no in ast.parse(txt).body:
        alvos = []
        if isinstance(no, (ast.Import, ast.ImportFrom)):
            alvos = [no]
        elif isinstance(no, ast.Try):
            alvos = [s for s in no.body if isinstance(s, (ast.Import, ast.ImportFrom))]
        for imp in alvos:
            nomes = {(a.asname or a.name).split(".")[0] for a in imp.names}
            if nomes & usados:
                linhas.append(ast.unparse(imp))

    ns: dict = {"TEM_CRYPTO": True}
    exec("\n".join(linhas) + "\n" + trecho, ns)
    return ns["ler_cnpj_certificado"]


CARTEIRA = [
    {"nome": "MONTE ASSESSORIA E CONSULTORIA LTDA", "cnpj": "04.103.256/0001-85"},
    {"nome": "MARIA DAS VITÓRIAS DE TAL", "cnpj": "00.612.340/0001-73"},
    {"nome": "EDU NORTE CURSOS LTDA.", "cnpj": "62.345.002/0001-70"},
    {"nome": "SILVA E SOUZA SOCIEDADE INDIVIDUAL DE ADVOC",
     "cnpj": "63.456.003/0001-54"},
    {"nome": "Fulano Montenegro de Tal", "cnpj": "123.456.789-09"},
]


def buscar_no_node(termos: list[str]) -> dict:
    """Roda o filtro REAL do clientes.html no Node, termo a termo."""
    txt = CLIENTES_HTML.read_text("utf-8")
    ajudantes = txt[txt.index("const soDig ="):txt.index("async function api(")]
    filtro = txt[txt.index("  // A busca não filtrava NADA."):txt.index("  qtd.textContent")]
    linhas = [
        ajudantes,
        "const clientes = " + json.dumps(CARTEIRA, ensure_ascii=False) + ";",
        "function filtrar(valor){",
        "  const f_busca = {value: valor};",
        filtro,
        # O trecho REAL deixou de montar um array `filtrados`: ele agora
        # define o predicado `casa`, e quem agrupa por regime e a
        # `renderClientes`. A guarda passa a exercitar o predicado --
        # que e onde a regra de busca mora de verdade.
        "  return clientes.filter(casa).map(c => c.nome);",
        "}",
        "const termos = " + json.dumps(termos, ensure_ascii=False) + ";",
        "const saida = {};",
        "for (const t of termos) saida[t] = filtrar(t);",
        "console.log(JSON.stringify(saida));",
    ]
    return _rodar_node("\n".join(linhas))


def regime_no_node(casos):
    """Roda a função REAL `regimeDaBasePublica` do clientes.html no Node."""
    linhas = [
        _funcao_do_html("function regimeDaBasePublica(r, atual){"),
        "const casos = " + json.dumps(casos) + ";",
        "console.log(JSON.stringify(casos.map(([r, a]) => regimeDaBasePublica(r, a))));",
    ]
    return _rodar_node("\n".join(linhas))


def certificado_sintetico():
    """Um e-CNPJ parecido com os reais, com o responsável de 33 letras.

    33 letras é o caso que quebrava: o byte de tamanho do DER vira 0x21, que é
    o caractere "!", e o leitor antigo o entregava colado no nome.
    """
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    resp = "ETIENE DA CONCEICAO SILVA DE MELO"
    assert len(resp) == 33, "o caso do teste depende de ter 33 letras"

    def octet(texto: str) -> bytes:
        b = texto.encode("utf-8")
        return bytes([0x04, len(b)]) + b

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME,
                               "EMPRESA DE TESTE LTDA:12345678000199"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "RECIFE"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "PE"),
        ]))
        .issuer_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "AC DE TESTE v5")]))
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=1))
        .not_valid_after(agora + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([
            x509.RFC822Name("contato@empresa.com.br"),
            x509.OtherName(x509.ObjectIdentifier("2.16.76.1.3.2"), octet(resp)),
            x509.OtherName(x509.ObjectIdentifier("2.16.76.1.3.3"),
                           octet("12345678000199")),
            # nascimento(8) + CPF(11) + NIS(11) + RG(15) do RESPONSÁVEL
            x509.OtherName(x509.ObjectIdentifier("2.16.76.1.3.4"),
                           octet("01011990" + "93036663487" + "0" * 26)),
        ]), critical=False)
        .sign(chave, hashes.SHA256()))
    pfx = pkcs12.serialize_key_and_certificates(
        b"teste", chave, cert, None,
        serialization.BestAvailableEncryption(b"segredo"))
    return pfx, resp


def main():
    secao("O certificado digital NÃO tem o início de atividade da empresa")
    # Não é opinião: o OID 2.16.76.1.3.4 do e-CNPJ é "dados do responsável" —
    # nascimento + CPF + NIS + RG. O mesmo valor aparece no e-CPF da pessoa.
    oid_ecnpj_monte = "010119901234567890900000000000000000000000000"
    oid_ecpf_fulano = "010119901234567890900000000000000000000000000"
    igual(oid_ecnpj_monte, oid_ecpf_fulano,
          "o e-CNPJ da MONTE e o e-CPF do responsável trazem a MESMA data")
    igual(oid_ecnpj_monte[:8], "01011990",
          "e ela é 01/01/1990 — nascimento do responsável")
    ok("01011990" != "19102000",
       "a abertura real da MONTE é 19/10/2000: o certificado erraria por 22 anos")

    secao("O servidor importa tudo o que a leitura do certificado usa")
    # O defeito que este bloco existe para impedir: `re.sub` foi usado na
    # função e `import re` não estava no módulo. O `except Exception` engolia o
    # NameError e a tela dizia "não consegui ler o CNPJ — confira a senha" com
    # a senha CERTA. Nenhum certificado novo entrava no cadastro.
    disponiveis = importados_no_modulo(SERVIDOR_PY)
    corpo = SERVIDOR_PY.read_text("utf-8")
    trecho = corpo[corpo.index("OID_RESPONSAVEL_NOME"):corpo.index("def parse_multipart")]
    import re as _re
    usados = {m for m in _re.findall(r"([a-z_][a-z0-9_]*)\.[a-z_]", trecho)}
    # nomes locais da própria função não precisam de import
    locais = {"cert", "gn", "san", "attr", "b", "txt", "exp", "ini", "extras",
              "valor", "conteudo", "senha", "nome", "oid", "a"}
    faltando = sorted(n for n in (usados - locais)
                      if n not in disponiveis and n not in dir(__builtins__))
    igual(faltando, [],
          "todo módulo usado na leitura do certificado está importado no servidor")
    ok("re" in disponiveis, "`import re` está no nível do módulo (era o que faltava)")

    secao("O leitor entrega tudo o que o certificado tem, sem cabeçalho de DER")
    ler = _leitor_de_certificado()
    pfx, resp = certificado_sintetico()
    titular, cnpj, validade, extras = ler(pfx, "segredo")
    igual(titular, "EMPRESA DE TESTE LTDA", "razão social")
    igual(cnpj, "12345678000199", "CNPJ")
    ok(bool(validade), "validade")
    igual(extras["responsavel"], resp,
          "responsável de 33 letras SEM o byte de tamanho colado na frente")
    igual(extras["responsavel_cpf"], "93036663487",
          "CPF do responsável (os 8 primeiros dígitos são o nascimento, e saem)")
    igual(extras["email"], "contato@empresa.com.br", "e-mail")
    igual(extras["municipio"], "RECIFE", "município")
    igual(extras["uf"], "PE", "UF")
    igual(extras["tipo"], "e-CNPJ", "tipo do certificado")
    igual(extras["emissor"], "AC DE TESTE v5", "autoridade certificadora")
    ok("nascimento" not in json.dumps(extras),
       "e a data de nascimento NÃO é devolvida: não serve ao cadastro fiscal")
    igual(ler(pfx, "senha errada"), ("", "", "", {}),
          "senha errada não vaza nada nem levanta exceção")

    secao("A base pública devolve a abertura, e o Fiscale a expõe")
    import cnpj_publico
    fonte = Path(cnpj_publico.__file__).read_text("utf-8")
    ok('"inicio_atividade": d.get("data_inicio_atividade")' in fonte,
       "consultar() repassa data_inicio_atividade da base pública")
    ok("2.16.76.1.3.4" in fonte,
       "e o código registra por que NÃO se lê isso do certificado")
    html = CLIENTES_HTML.read_text("utf-8")
    ok("f_iniciativ.value = r.inicio_atividade.slice(0,7)" in html,
       "a tela preenche o campo com AAAA-MM (o input é type=month)")

    secao("O certificado alimenta o cadastro")
    for campo in ("f_resp", "f_respcpf"):
        ok(f'id="{campo}"' in html, f"o formulário tem o campo {campo}")
    ok("responsavel: f_resp.value.trim()" in html,
       "e o responsável é gravado no cadastro")
    ok("responsavelCpf: soDig(f_respcpf.value)" in html,
       "junto com o CPF dele")

    secao("Regime pela base pública: decide o que é inequívoco, e só isso")
    if not _node():
        ok(True, "Node ausente nesta máquina — parte JavaScript pulada")
    else:
        r = regime_no_node([
            ({"mei": True, "optante_simples": True}, "Simples Nacional"),
            ({"mei": False, "optante_simples": True}, "Lucro Presumido"),
            ({"mei": False, "optante_simples": False}, "Simples Nacional"),
            ({"mei": False, "optante_simples": False}, "MEI"),
            ({"mei": False, "optante_simples": False}, "Lucro Presumido"),
            ({"mei": False, "optante_simples": False}, "Lucro Real"),
        ])
        igual(r[0], {"valor": "MEI", "conflito": False},
              "MEI na base vira MEI no cadastro (antes nunca era marcado)")
        igual(r[1], {"valor": "Simples Nacional", "conflito": False},
              "optante corrige um cadastro que dizia Lucro Presumido")
        igual(r[2], {"valor": "Simples Nacional", "conflito": True},
              "não optante + cadastro em Simples = CONFLITO, sem escolher sozinho")
        igual(r[3], {"valor": "MEI", "conflito": True},
              "idem quando o cadastro diz MEI")
        igual(r[4], {"valor": "Lucro Presumido", "conflito": False},
              "não optante + já fora do Simples: nada a fazer")
        igual(r[5], {"valor": "Lucro Real", "conflito": False},
              "e Lucro Real não vira Presumido por chute")

    secao("A busca por nome ou CNPJ filtra de verdade")
    if not _node():
        ok(True, "Node ausente nesta máquina — parte JavaScript pulada")
    else:
        r = buscar_no_node(["", "monte", "vitorias", "VITÓRIAS", "62345",
                            "04.103.256", "123.456", "zzzz"])
        igual(len(r[""]), 5, "termo vazio mostra a carteira inteira")
        igual(len(r["monte"]), 2,
              "'monte' acha a MONTE e o sobrenome Montenegro — e só eles")
        ok("MONTE ASSESSORIA E CONSULTORIA LTDA" in r["monte"],
           "a empresa está entre eles")
        igual(r["vitorias"], ["MARIA DAS VITÓRIAS DE TAL"],
              "busca sem acento acha o nome acentuado")
        igual(r["VITÓRIAS"], ["MARIA DAS VITÓRIAS DE TAL"],
              "e com acento e caixa alta também")
        igual(r["62345"], ["EDU NORTE CURSOS LTDA."],
              "pedaço do CNPJ, sem pontuação")
        igual(r["04.103.256"], ["MONTE ASSESSORIA E CONSULTORIA LTDA"],
              "CNPJ digitado com pontuação")
        igual(r["123.456"], ["Fulano Montenegro de Tal"],
              "CPF também (nem todo cliente é empresa)")
        igual(r["zzzz"], [], "termo que não existe não devolve ninguém")
        ok(len(r["zzzz"]) != len(CARTEIRA),
           "e acima de tudo: a busca NÃO devolve a carteira inteira")

    print("\n" + "=" * 62)
    print(f"{_ok} ok · {_falhas} falha(s)")
    for e in _erros:
        print("  - " + e)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
