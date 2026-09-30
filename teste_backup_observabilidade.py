#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backup v2 — progresso, telemetria e o retrabalho que saiu do `_resumir`.

    python teste_backup_observabilidade.py

O QUE SE PROVA
    Que o backup deixou de ser uma caixa preta: o progresso anda, as fases
    aparecem na ordem, o resultado traz duração e tempo por fase, e a falha
    diz ONDE parou, QUAL arquivo, por QUANTO tempo e com QUANTOS arquivos já
    processados.

    Que o `_resumir` não faz mais uma terceira varredura de `stat()` sobre os
    mesmos arquivos — provado com o `stat` proibido, não por leitura de código.

    Que o backup continua em FLUXO: o `.parcial` cresce em disco enquanto o
    empacotamento corre, e a memória não guarda o pacote inteiro.

    E — o que mais importa — que NADA de segredo aparece em lugar nenhum:
    nem a frase-senha, nem conteúdo de arquivo, no progresso, no resultado,
    na exceção ou nas linhas que o servidor escreve no log.

    Nenhum dado real é usado: raiz temporária, conteúdo inventado, frase de
    mentira. A pasta real nunca é tocada.
"""
from __future__ import annotations

import ast
import io as _io
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import teste_apoio                                     # noqa: E402
import fiscale_backup as bk                            # noqa: E402

FRASE = "frase-de-teste-que-nao-e-de-ninguem-2026"
MARCA = "CONTEUDO-FISCAL-QUE-NAO-PODE-VAZAR"

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
        print("  FALHOU   " + desc)


def povoar(raiz: Path, quantos: int = 60) -> int:
    """Uma instalação de mentira: empresas, XML e JSON inventados."""
    total = 0
    for i in range(quantos):
        emp = "%014d" % (11111111000100 + i % 4)
        pasta = raiz / emp / ("acervo" if i % 2 else "nfe")
        pasta.mkdir(parents=True, exist_ok=True)
        corpo = ("<?xml version='1.0'?><NFe><marca>%s</marca><n>%d</n>%s</NFe>"
                 % (MARCA, i, "<item>PRODUTO DE TESTE</item>" * 40)).encode("utf-8")
        (pasta / ("doc%04d.xml" % i)).write_bytes(corpo)
        total += len(corpo)
    (raiz / "dados_versao.json").write_text(json.dumps({"versao": 7}), encoding="utf-8")
    (raiz / "certificados.json").write_text(json.dumps([]), encoding="utf-8")
    return total


def main() -> int:
    # ══════════════════════════════════════════════════════════════════
    secao("1. Progresso: cresce, nomeia a fase e chega ao fim")
    # ══════════════════════════════════════════════════════════════════
    with teste_apoio.raiz_temporaria("fiscale_obs_") as raiz:
        povoar(raiz, 120)
        destino = raiz.parent / "pacote1.fbk"
        chamadas: list[tuple] = []

        def progresso(feitos, total, info=None):
            chamadas.append((feitos, total, dict(info or {})))

        r = bk.exportar_v2(destino, FRASE, raiz=raiz, progresso=progresso)

        ok(len(chamadas) >= 4, "o progresso foi chamado várias vezes (%d)" % len(chamadas))
        feitos = [c[0] for c in chamadas]
        ok(feitos == sorted(feitos), "a contagem de arquivos nunca anda para trás")
        ok(feitos[-1] > 0 and feitos[-1] == chamadas[-1][1],
           "termina com feitos == total (%d)" % feitos[-1])

        fases = [c[2].get("fase") for c in chamadas]
        for f in (bk.FASE_DESCOBERTA, bk.FASE_EMPACOTAMENTO, bk.FASE_CIFRA,
                  bk.FASE_ESCRITA, bk.FASE_CONFERENCIA, bk.FASE_CONCLUSAO):
            ok(f in fases, "a fase '%s' foi anunciada" % f)
        ok(fases.index(bk.FASE_DESCOBERTA) < fases.index(bk.FASE_EMPACOTAMENTO)
           < fases.index(bk.FASE_CONFERENCIA) < fases.index(bk.FASE_CONCLUSAO),
           "as fases vêm na ordem certa")
        ok(bk.FASE_FALHA not in fases, "nenhuma fase de falha num backup que deu certo")

        bytes_vistos = [c[2].get("bytes", 0) for c in chamadas]
        ok(bytes_vistos == sorted(bytes_vistos), "os bytes lidos também só crescem")
        ok(chamadas[-1][2].get("bytes_total", 0) > 0,
           "o total de bytes é informado desde o começo")

        secao("2. Telemetria do resultado")
        ok(r.get("ok") is True, "o backup terminou bem")
        ok(isinstance(r.get("duracao_s"), float) and r["duracao_s"] >= 0,
           "o resultado traz a duração em segundos (%.3f)" % r.get("duracao_s", -1))
        ok(isinstance(r.get("fases"), dict), "o resultado traz o tempo por fase")
        for f in (bk.FASE_DESCOBERTA, bk.FASE_EMPACOTAMENTO, bk.FASE_CONFERENCIA,
                  bk.FASE_CONCLUSAO):
            ok(f in r["fases"], "o tempo da fase '%s' foi medido" % f)
        ok(sum(r["fases"].values()) <= r["duracao_s"] + 0.5,
           "a soma das fases não passa da duração total")
        ok(r.get("arquivos") == 122, "contou os 122 arquivos (%s)" % r.get("arquivos"))
        ok(r.get("bytes_lidos", 0) > 0, "informa quantos bytes foram lidos")
        ok(r.get("bytes", 0) > 0, "informa o tamanho do pacote")

        secao("3. Nenhum segredo no que é medido ou devolvido")
        tudo = json.dumps({"resultado": r, "chamadas": chamadas},
                          ensure_ascii=False, default=str)
        ok(FRASE not in tudo, "a frase-senha não aparece no progresso nem no resultado")
        ok(MARCA not in tudo, "conteúdo de arquivo não aparece no progresso nem no resultado")
        ok("frase" not in tudo.lower().replace("frase-senha", ""),
           "nem o campo 'frase' é ecoado")

        secao("4. Compatibilidade: o callback antigo de dois argumentos continua valendo")
        antigos: list[tuple] = []
        r2 = bk.exportar_v2(raiz.parent / "pacote2.fbk", FRASE, raiz=raiz,
                            progresso=lambda feitos, total: antigos.append((feitos, total)))
        ok(r2["ok"] and antigos, "exportou e chamou o callback de 2 argumentos (%d vezes)"
           % len(antigos))
        ok(all(len(c) == 2 for c in antigos), "nenhum terceiro argumento foi forçado nele")
        ok(bk._avisador(None) is not None, "sem callback, o avisador não quebra")

    # ══════════════════════════════════════════════════════════════════
    secao("5. Falha: diz a fase, o arquivo, a duração e quanto já tinha andado")
    # ══════════════════════════════════════════════════════════════════
    with teste_apoio.raiz_temporaria("fiscale_obs_erro_") as raiz:
        povoar(raiz, 400)
        destino = raiz.parent / "falha.fbk"
        parcial = destino.with_suffix(destino.suffix + ".parcial")
        alvos = sorted(raiz.rglob("*.xml"))[100:260]
        parar = threading.Event()

        def escritor():
            while not parar.is_set():
                for p in alvos:
                    if parar.is_set():
                        return
                    try:
                        with _io.open(p, "ab") as f:
                            f.write(b" ")
                    except OSError:
                        pass

        avisos: list[dict] = []
        th = threading.Thread(target=escritor, daemon=True)
        th.start()
        erro = None
        try:
            bk.exportar_v2(destino, FRASE, raiz=raiz,
                           progresso=lambda f, t, i=None: avisos.append(dict(i or {})))
        except Exception as e:      # noqa: BLE001 — é o que estamos medindo
            erro = e
        finally:
            parar.set()
            th.join(timeout=5)

        if erro is None:
            ok(False, "o escritor concorrente não colidiu — teste inconclusivo")
        else:
            ok(isinstance(erro, bk.BackupInvalido),
               "um arquivo alterado no meio aborta o backup (%s)" % erro.__class__.__name__)
            ok(getattr(erro, "fase", "") == bk.FASE_EMPACOTAMENTO,
               "a exceção diz a fase: %s" % getattr(erro, "fase", "(nenhuma)"))
            ok(bool(getattr(erro, "arquivo", "")),
               "a exceção diz o arquivo: %s" % getattr(erro, "arquivo", "(nenhum)"))
            ok(os.sep not in getattr(erro, "arquivo", ""),
               "e diz só o NOME, sem caminho")
            ok(isinstance(getattr(erro, "duracao_s", None), float),
               "a exceção diz há quanto tempo o backup corria")
            ok(getattr(erro, "arquivos_processados", -1) >= 0,
               "a exceção diz quantos arquivos já tinham entrado (%s)"
               % getattr(erro, "arquivos_processados", None))
            ok(MARCA not in str(erro) and FRASE not in str(erro),
               "a mensagem de erro não carrega conteúdo nem frase")
            ok(avisos and avisos[-1].get("fase") == bk.FASE_FALHA,
               "o último aviso de progresso é a fase de falha")
            ok(not destino.exists(), "nenhum .fbk foi deixado para trás")
            ok(not parcial.exists(), "e o .parcial foi removido")

    # ══════════════════════════════════════════════════════════════════
    secao("6. `_resumir` não faz a terceira varredura de stat()")
    # ══════════════════════════════════════════════════════════════════
    # A prova certa NÃO é "zero stat": o `_resumir` ainda pergunta se o
    # `certificados.json` existe, e isso é uma chamada só, sempre. O que se
    # exige é que o custo PARE DE CRESCER com a quantidade de arquivos — era
    # exatamente isso que fazia 91 mil chamadas no fim de cada backup.
    def medir(quantos):
        with teste_apoio.raiz_temporaria("fiscale_obs_stat_") as raiz:
            povoar(raiz, quantos)
            arquivos = sorted(p for p in raiz.rglob("*")
                              if p.is_file() and bk._entra_no_backup(p, raiz))
            instantes = {}
            for p in arquivos:
                st = p.stat()
                instantes[p] = (st.st_size, st.st_mtime_ns)
            esperado = sum(v[0] for v in instantes.values())

            original = Path.stat
            contador = {"n": 0, "dos_arquivos": 0}
            listados = set(arquivos)

            def stat_contado(self, *a, **kw):
                contador["n"] += 1
                if self in listados and self.name != "certificados.json":
                    contador["dos_arquivos"] += 1
                return original(self, *a, **kw)

            Path.stat = stat_contado
            try:
                resumo = bk._resumir(raiz, arquivos, [], {}, instantes)
                antigo = None
                Path.stat = original
                antigo = bk._resumir(raiz, arquivos, [], {})
            finally:
                Path.stat = original
            return len(arquivos), contador, resumo, antigo, esperado

        return None

    n1, c1, r1, a1, e1 = medir(30)
    n2, c2, r2, a2, e2 = medir(90)
    ok(c1["dos_arquivos"] == 0 and c2["dos_arquivos"] == 0,
       "nenhum arquivo da lista é consultado de novo com stat()")
    ok(c1["n"] == c2["n"],
       "o número de stat() não cresce com a quantidade de arquivos (%d com %d arquivos, %d com %d)"
       % (c1["n"], n1, c2["n"], n2))
    ok(c2["n"] <= 2, "e é constante e pequeno (%d chamada(s))" % c2["n"])
    ok(r1["bytes_originais"] == e1 and r2["bytes_originais"] == e2,
       "a soma de bytes continua exata (%d e %d)"
       % (r1["bytes_originais"], r2["bytes_originais"]))
    ok(a1["bytes_originais"] == e1 and a2["bytes_originais"] == e2,
       "sem instantes, o comportamento antigo (v1) é preservado")

    # ══════════════════════════════════════════════════════════════════
    secao("7. Continua em fluxo: o pacote não é montado na memória")
    # ══════════════════════════════════════════════════════════════════
    with teste_apoio.raiz_temporaria("fiscale_obs_fluxo_") as raiz:
        povoar(raiz, 400)
        destino = raiz.parent / "fluxo.fbk"
        parcial = destino.with_suffix(destino.suffix + ".parcial")
        crescimento: list[int] = []

        def espiar(feitos, total, info=None):
            if (info or {}).get("fase") == bk.FASE_EMPACOTAMENTO:
                try:
                    crescimento.append(parcial.stat().st_size)
                except OSError:
                    crescimento.append(-1)

        r = bk.exportar_v2(destino, FRASE, raiz=raiz, progresso=espiar)
        ok(any(n > 0 for n in crescimento),
           "o .parcial já tinha bytes em disco durante o empacotamento")
        ok(crescimento == sorted(crescimento),
           "e só cresce — nada é reescrito do começo no fim")
        ok(r["ok"] and destino.exists(), "o pacote final foi gerado")

        fonte = _io.open(RAIZ / "fiscale_backup.py", encoding="utf-8").read()
        arvore = ast.parse(fonte)
        alvo = next(n for n in ast.walk(arvore)
                    if isinstance(n, ast.FunctionDef) and n.name == "exportar_v2")
        corpo = ast.dump(alvo)
        ok("_FluxoCifrado" in corpo, "exportar_v2 continua escrevendo pelo fluxo cifrado")
        ok(".read_bytes" not in ast.unparse(alvo),
           "e não lê a árvore inteira de uma vez")

    # ══════════════════════════════════════════════════════════════════
    secao("8. Servidor: telemetria registrada, e sem segredo no log")
    # ══════════════════════════════════════════════════════════════════
    fonte_srv = _io.open(RAIZ / "fiscale_server.py", encoding="utf-8").read()
    trecho = fonte_srv[fonte_srv.index('if rota == "/api/backup/criar"'):]
    trecho = trecho[:trecho.index('if rota == "/api/backup/inspecionar"')]

    ok("progresso=_andou" in trecho, "a rota de criar passa o progresso ao módulo")
    ok("_backup_log(\"início" in trecho, "registra o início no log")
    ok("_backup_log(\"fim" in trecho, "registra o fim, com duração e contagem")
    ok("FALHOU" in trecho, "registra a falha")
    ok("fiscale_auditoria.BACKUP_CRIADO" in trecho, "registra o sucesso na auditoria")
    ok("fiscale_auditoria.BACKUP_FALHOU" in trecho, "registra a falha na auditoria")
    ok('getattr(e, "fase"' in trecho and 'getattr(e, "arquivo"' in trecho,
       "aproveita fase e arquivo da exceção")

    # nenhuma linha que vá para log/auditoria pode conter a frase
    linhas_log = re.findall(r'_backup_log\((.*?)\)\n', trecho, re.S)
    ok(linhas_log, "há linhas de log para conferir (%d)" % len(linhas_log))
    ok(all("frase" not in l for l in linhas_log),
       "nenhuma linha de log menciona a frase")
    auditorias = re.findall(r'auditar\((.*?)\*\*self\._ctx\(\)', trecho, re.S)
    ok(auditorias, "há chamadas de auditoria para conferir (%d)" % len(auditorias))
    ok(all("frase" not in a for a in auditorias),
       "nenhuma chamada de auditoria carrega a frase")
    ok("BACKUP_ANDAMENTO" in fonte_srv and "_BACKUP_TRAVA" in fonte_srv,
       "o andamento é guardado sob trava")
    ok('"/api/backup/progresso"' in fonte_srv, "existe a rota de andamento")
    bloco_prog = fonte_srv[fonte_srv.index('if rota == "/api/backup/progresso"'):]
    bloco_prog = bloco_prog[:bloco_prog.index('if rota == "/api/backup/situacao"')]
    ok("eh_admin" in bloco_prog, "e ela é só do administrador")
    ok("frase" not in bloco_prog, "e não devolve nada sobre a frase")

    import fiscale_auditoria as aud
    ok(aud.BACKUP_CRIADO in aud.EVENTOS and aud.BACKUP_FALHOU in aud.EVENTOS,
       "os dois eventos entraram no vocabulário da auditoria")
    ok(all(e in aud.ROTULO_EVENTO for e in aud.EVENTOS),
       "todo evento do vocabulário tem rótulo de tela")

    secao("9. Tela: progresso real, com queda limpa para o aviso de antes")
    tela = _io.open(RAIZ / "web" / "backup.html", encoding="utf-8").read()
    ok("/api/backup/progresso" in tela, "a tela consulta o andamento")
    ok("1500" in tela, "de 1,5 em 1,5 segundo — não é polling agressivo")
    ok("acompanhar(true)" in tela and "acompanhar(false)" in tela,
       "só acompanha enquanto o backup corre")
    ok("clearInterval" in tela, "e desliga o relógio ao terminar")
    ok("if(!r.ok) return" in tela.replace(" ", "").replace("if(!r.ok)return;",
                                                           "if(!r.ok) return;")
       or "if(!r.ok) return" in tela,
       "se a rota falhar, a tela não quebra")
    ok("Não feche esta janela" in tela, "a mensagem original foi preservada")

    print("\n" + "=" * 62)
    print("  %d ok · %d falha(s)" % (_ok, _falhas))
    for e in _erros:
        print("   - " + e)
    print("=" * 62)
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
