#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes do lançador de HOMOLOGAÇÃO (D-AMB-1).

    python teste_homologacao.py

O QUE ESTA SUÍTE PROVA
    1. Que o lançador está configurado para a porta 8898 e para
       C:\\Fiscale\\homologacao\\dados — e que a 8777 e a pasta real nunca são
       alvo dele.
    2. Que ele RECUSA subir sem pasta de dados definida, em vez de cair na
       pasta real (é o acidente que a separação de ambientes existe para evitar).
    3. Que ele RECUSA a pasta de dados real, e também qualquer pasta DENTRO dela.
    4. Que ele RECUSA a porta da produção e uma porta já ocupada.
    5. Que ele liga a trava `FISCALE_TESTE_PROIBIR_RAIZ_REAL` e desliga a bandeja.
    6. Que o modo `conferir` não sobe nada: nenhum processo, nenhuma porta.
    7. Que rodar o lançador não altera nada na pasta de dados da PRODUÇÃO nem
       muda o estado da produção na 8777.
    8. Que a porta interna do módulo NFS-e é escolhida entre as livres da faixa
       8790-8830 e vai para o log — é o que permite duas instâncias conviverem.

NADA AQUI SOBE O SERVIDOR. Todos os testes usam o modo `conferir` e pastas
temporárias; a produção é apenas observada.

A 8898 PODE ESTAR EM USO — pela própria homologação.
    A conferência normal roda numa porta livre sorteada na hora, e a porta
    padrão ganhou um caso próprio: com a homologação no ar ela tem de ser
    RECUSADA (código 4); com a 8898 livre, aceita. Assim a suíte fica verde
    nos dois estados sem afrouxar a recusa de porta ocupada.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
LANCADOR = RAIZ / "homologar_fiscale.bat"
PORTA_HOMOLOGACAO = "8898"
PORTA_PRODUCAO = "8777"
PASTA_PADRAO = "C:\\Fiscale\\homologacao\\dados"
PERFIL = Path(os.environ.get("USERPROFILE") or Path.home())
REAL = PERFIL / "Fiscale" / "dados"

_ok = _falhas = 0
_erros: list[str] = []


def secao(t: str) -> None:
    print("\n" + t)


def ok(cond, desc: str) -> None:
    global _ok, _falhas
    if cond:
        _ok += 1
        print("  ok   " + desc)
    else:
        _falhas += 1
        _erros.append(desc)
        print("  FALHOU  " + desc)


def conferir(dados=None, porta=None, sem_dados=False):
    """Roda o lançador no modo que NÃO sobe nada."""
    env = dict(os.environ)
    env.pop("FISCALE_HOMOLOGACAO_DADOS", None)
    env.pop("FISCALE_HOMOLOGACAO_PORTA", None)
    if dados is not None:
        env["FISCALE_HOMOLOGACAO_DADOS"] = str(dados)
    if porta is not None:
        env["FISCALE_HOMOLOGACAO_PORTA"] = str(porta)
    args = ["cmd", "/c", str(LANCADOR), "conferir"]
    if sem_dados:
        args.append("_teste_sem_dados")
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(RAIZ), env=env, timeout=120)


def porta_livre() -> int:
    """Uma porta que ninguém escuta agora (o sistema operacional escolhe)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def escutando(porta: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def foto_da_producao():
    """Nome, tamanho e mtime do primeiro nível da pasta real. Só leitura."""
    if not REAL.exists():
        return []
    saida = []
    for p in sorted(REAL.iterdir()):
        try:
            st = p.stat()
            saida.append((p.name, st.st_size if p.is_file() else -1, int(st.st_mtime)))
        except OSError:
            saida.append((p.name, -2, -2))
    return saida


def main() -> int:
    if sys.platform != "win32":
        print("  ok   não se aplica fora do Windows")
        print("  1 ok · 0 falha(s)")
        return 0

    secao("O lançador existe e está configurado para a homologação")
    ok(LANCADOR.exists(), "homologar_fiscale.bat existe")
    texto = LANCADOR.read_text(encoding="utf-8", errors="replace")
    ok('set "PORTA=%s"' % PORTA_HOMOLOGACAO in texto, "porta padrão é %s" % PORTA_HOMOLOGACAO)
    ok('set "DADOS=%s"' % PASTA_PADRAO in texto, "pasta padrão é %s" % PASTA_PADRAO)
    ok('set "FISCALE_SEM_BANDEJA=1"' in texto, "sobe sem ícone na bandeja")
    ok('set "FISCALE_TESTE_PROIBIR_RAIZ_REAL=1"' in texto,
       "liga a trava contra a pasta de dados real")
    ok('set "FISCALE_DADOS=%DADOS%"' in texto, "exporta FISCALE_DADOS explicitamente")
    ok('set "PORTA_PRODUCAO=%s"' % PORTA_PRODUCAO in texto and ":portaproducao" in texto,
       "conhece a porta da produção e tem recusa própria para ela")
    ok("%USERPROFILE%\\Fiscale\\dados" in texto and 'set "DADOS=%REAL%"' not in texto,
       "a pasta real aparece só como alvo PROIBIDO, nunca como destino")
    ok("nfse\\.venv\\Scripts\\python.exe" in texto, "usa o Python do projeto")
    ok("Iniciar Fiscale.vbs" not in texto, "não menciona nem toca o lançador da produção")

    secao("Conferência normal, em pasta temporária")
    foto_antes = foto_da_producao()
    producao_de_pe = escutando(int(PORTA_PRODUCAO))
    with tempfile.TemporaryDirectory(prefix="fiscale_homolog_") as tmp:
        alvo = str(Path(tmp) / "dados")
        livre = porta_livre()
        r = conferir(dados=alvo, porta=livre)
        saida = (r.stdout or "") + (r.stderr or "")
        ok(r.returncode == 0, "conferir termina com 0 (obtive %s)" % r.returncode)
        ok("AMBIENTE: HOMOLOGACAO" in saida, "a saída identifica o ambiente como HOMOLOGACAO")
        ok("Porta          : %s" % livre in saida, "mostra a porta conferida")
        ok(alvo in saida, "mostra a pasta de dados da homologação")
        ok("porta %s" % PORTA_PRODUCAO in saida and "INTOCADA" in saida,
           "diz que a produção fica intocada")
        ok("NADA foi iniciado" in saida, "declara que não iniciou nada")
        ok(Path(alvo).is_dir(), "cria a pasta de dados da homologação quando falta")
        ok("restaure um .fbk" in saida, "pede restauração de .fbk quando a pasta está vazia")
        ok(not escutando(livre), "ninguém passou a escutar na porta conferida (%s)" % livre)

    secao("Porta padrão %s" % PORTA_HOMOLOGACAO)
    homologacao_no_ar = escutando(int(PORTA_HOMOLOGACAO))
    with tempfile.TemporaryDirectory(prefix="fiscale_homolog_") as tmp:
        r = conferir(dados=Path(tmp) / "dados")        # porta NÃO informada
        saida = (r.stdout or "") + (r.stderr or "")
        ok("Porta          : %s" % PORTA_HOMOLOGACAO in saida,
           "sem porta informada, o lançador usa a %s" % PORTA_HOMOLOGACAO)
        if homologacao_no_ar:
            ok(r.returncode == 4 and "ja esta em uso" in saida,
               "com a homologação no ar, a %s é RECUSADA (código 4; obtive %s)"
               % (PORTA_HOMOLOGACAO, r.returncode))
            ok("NADA foi iniciado" not in saida,
               "e a recusa acontece antes de qualquer outra coisa")
        else:
            ok(r.returncode == 0 and "NADA foi iniciado" in saida,
               "com a %s livre, a conferência passa (obtive %s)"
               % (PORTA_HOMOLOGACAO, r.returncode))
            ok(not escutando(int(PORTA_HOMOLOGACAO)),
               "e continua ninguém escutando na %s" % PORTA_HOMOLOGACAO)

    secao("Recusas")
    r = conferir(dados=Path(tempfile.gettempdir()) / "nunca_usada", sem_dados=True)
    ok(r.returncode == 2 and "nao esta definida" in (r.stdout or ""),
       "recusa sem pasta de dados definida, com código 2 (obtive %s)" % r.returncode)
    ok("pasta de dados REAL" in (r.stdout or ""),
       "e explica que sem ela cairia na pasta real")

    r = conferir(dados=REAL)
    ok(r.returncode == 3, "recusa a pasta de dados REAL, com código 3 (obtive %s)" % r.returncode)
    r = conferir(dados=REAL / "homologacao")
    ok(r.returncode == 3, "recusa também uma pasta DENTRO da pasta real")
    r = conferir(dados=PERFIL / "SistemaNFSe" / "dados")
    ok(r.returncode == 3, "recusa a raiz legada ~\\SistemaNFSe\\dados")

    with tempfile.TemporaryDirectory(prefix="fiscale_homolog_") as tmp:
        r = conferir(dados=Path(tmp) / "dados", porta=PORTA_PRODUCAO)
        ok(r.returncode == 5, "recusa a porta da produção, com código 5 (obtive %s)" % r.returncode)

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            ocupada = s.getsockname()[1]
            r = conferir(dados=Path(tmp) / "dados", porta=ocupada)
        ok(r.returncode == 4, "recusa porta ocupada, com código 4 (obtive %s)" % r.returncode)
        ok("ja esta em uso" in (r.stdout or ""), "e diz que a porta está em uso")

    secao("A produção continua exatamente como estava")
    ok(foto_da_producao() == foto_antes,
       "nada mudou no primeiro nível da pasta de dados da produção")
    ok(escutando(int(PORTA_PRODUCAO)) == producao_de_pe,
       "a produção na 8777 continua no mesmo estado de antes")

    secao("Porta interna do módulo NFS-e (convivência de instâncias)")
    servidor = (RAIZ / "fiscale_server.py").read_text(encoding="utf-8", errors="replace")
    ok("def _porta_livre(inicio=8790, fim=8830)" in servidor,
       "a porta interna sai da faixa 8790-8830, escolhida entre as livres")
    ok("porta interna" in servidor, "a porta interna escolhida é registrada na saída/log")
    ok(re.search(r"NFSE_PORT\s*=\s*_porta_livre\(\)", servidor) is not None,
       "cada instância calcula a sua porta interna no arranque")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
