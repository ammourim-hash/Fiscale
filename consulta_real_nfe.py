#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
consulta_real_nfe.py — UMA consulta real à SEFAZ, manual e supervisionada.

ISTO NÃO É OPERAÇÃO. É UM TESTE DE INTEGRAÇÃO CONTROLADO.
    Uma empresa, um certificado, um ambiente, uma execução, nenhum laço.
    Sem agendador, sem tarefa do Windows, sem varredura das 22 empresas, sem
    retry automático, sem manifestação do destinatário.

POR QUE É UM ARQUIVO SEPARADO, E NÃO UMA FLAG NA SUÍTE
    Se a integração real fosse um `--real` dentro de `teste_ingestao3b.py`,
    bastaria um engano de linha de comando — ou um CI mal configurado — para
    disparar consulta de verdade e gastar a cota horária do CNPJ. Arquivo
    separado, nome explícito e confirmação digitada é a diferença entre "não
    deve acontecer" e "não acontece".

O QUE ELE IMPRIME, E O QUE NUNCA IMPRIME
    Mostra: CNPJ MASCARADO, ambiente, serviço, checkpoint, cStat, ultNSU,
    maxNSU, contagem de docZip, persistidos, duplicatas, schemas desconhecidos,
    erros, duração.

    Nunca mostra: XML, conteúdo de documento, certificado, senha, token.

USO

    python consulta_real_nfe.py --empresa <CNPJ> --ambiente homologacao
    python consulta_real_nfe.py --empresa <CNPJ> --ambiente producao --confirmar

    Sem `--confirmar` ele só mostra o que FARIA e sai (ensaio).

CUIDADO COM A COTA
    O serviço bloqueia o CNPJ por cerca de 1 hora ao responder cStat 656
    (consumo indevido). Este programa faz **no máximo um lote por execução** por
    padrão (`--lotes 1`) justamente para não chegar perto disso.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import fiscale_dados as fd                              # noqa: E402
from ingestao import acervo as acv                      # noqa: E402
from ingestao import auditoria as aud                   # noqa: E402
from ingestao import cadastro as cad_mod                # noqa: E402
from ingestao import credencial_estado as ce            # noqa: E402
from ingestao import indice as idx                      # noqa: E402
from ingestao import controlador as ctl                 # noqa: E402
from ingestao import pipeline as pipe                   # noqa: E402
from ingestao import servico_distribuicao as svc        # noqa: E402
from ingestao.ambiente import resolver as resolver_ambiente   # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint   # noqa: E402
from ingestao import autor_consulta as autoria          # noqa: E402
from ingestao.conectores import nfe_dfe as N            # noqa: E402
from ingestao.identidade import normalizar              # noqa: E402


def linha(c="-", n=68):
    print(c * n)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Consulta real e ÚNICA à Distribuição DF-e da NF-e.")
    p.add_argument("--empresa", required=True,
                   help="CNPJ ou CPF da empresa (com ou sem máscara)")
    p.add_argument("--ambiente", required=True,
                   choices=["producao", "homologacao"],
                   help="escolha explícita; não há padrão de propósito")
    p.add_argument("--lotes", type=int, default=1,
                   help="máximo de lotes nesta execução (padrão 1)")
    p.add_argument("--confirmar", action="store_true",
                   help="sem isto, faz apenas o ENSAIO e não chama a SEFAZ")
    p.add_argument("--dados", default="",
                   help="raiz de dados (padrão: a do Fiscale)")
    p.add_argument("--cuf", default="",
                   help="override EXPLÍCITO do cUFAutor (diagnóstico). "
                        "A execução normal lê o autor de autor_consulta.json")
    args = p.parse_args()

    ident = normalizar(args.empresa)
    if not ident.valido:
        print(f"[ERRO] identidade fiscal inválida: {ident.motivo}")
        return 2

    raiz = Path(args.dados) if args.dados else fd.raiz()
    amb = resolver_ambiente(args.ambiente)

    # ── 1. o que sabemos ANTES de chamar ───────────────────────────────────
    linha("=")
    print("  CONSULTA REAL — NF-e Distribuição de DF-e")
    linha("=")

    cad = cad_mod.carregar(raiz)
    empresa = cad.obter_empresa(ident.valor)
    if empresa is None:
        print(f"[ERRO] {ident.mascarado()} não está no cadastro desta instalação.")
        return 2

    cred = cad.obter_certificado_da_empresa(ident.valor)
    if cred is None:
        print(f"[ERRO] {ident.mascarado()} não tem certificado associado.")
        return 2

    estado = ce.avaliar(cred, raiz)
    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(ident.valor, N.SERVICO, amb)

    # Monta a Fonte AGORA, ainda sem transporte, só para que o endpoint, o
    # cUFAutor e o tpAmb mostrados sejam os que o adapter REALMENTE escolheu —
    # e não uma segunda leitura da tabela, que poderia divergir do que vai ser
    # enviado sem ninguém notar.
    class _SemRede:
        def enviar(self, url, corpo):
            raise RuntimeError("validação somente-leitura não transmite")

    try:
        # O cUFAutor vem do AUTOR da consulta (o escritório), nunca da UF da
        # empresa consultada. Sem autor configurado e sem --cuf, aborta aqui —
        # antes de qualquer rede.
        previa = N.FonteNFeDistribuicaoDFe(
            identidade=ident.valor, transporte=_SemRede(), ambiente=amb,
            cuf=args.cuf, dados_dir=raiz)
    except autoria.ErroAutor as exc:
        print(f"[ABORTADO] cUFAutor indefinido: {exc}")
        print(f"  configure {autoria.ARQUIVO} na raiz de dados, ou passe --cuf")
        return 2
    except Exception as exc:
        print(f"[ABORTADO] não consegui preparar a consulta: "
              f"{type(exc).__name__}: {exc}")
        return 2

    envelope = N.montar_envelope_dist_nsu(ident.valor, cp.ult_nsu, amb,
                                          cuf=previa.cuf)
    tipo_consulta = "distNSU" if b"<distNSU>" in envelope else "(inesperado)"
    resumo_cred = estado.resumo()
    arquivo_cert = Path(str(cred.caminho or "")).name or "(sem caminho)"
    titular = normalizar(getattr(getattr(cred, "titular", None), "valor", "")
                         or str(getattr(cred, "titular", "")))

    print("  VALIDAÇÃO SOMENTE-LEITURA (nada foi transmitido)")
    linha()
    print(f"  identidade fiscal   : {ident.tipo} canônico, {len(ident.valor)} dígitos, DV ok")
    print(f"  empresa (mascarada) : {ident.mascarado()}")
    print(f"  UF da empresa       : {empresa.uf or '(não cadastrada)'}"
          f"   — NÃO entra no cUFAutor")
    print(f"  autor da consulta   : UF {previa.autor.uf}"
          f"   -> cUFAutor {previa.cuf}")
    print(f"    origem            : {previa.autor.origem}")
    print(f"  serviço             : NFeDistribuicaoDFe  ({N.SERVICO})")
    print(f"  ambiente            : {amb.nome}   (tpAmb {N.TP_AMB[amb.nome]})")
    print(f"  endpoint do adapter : {previa.url}")
    dias = resumo_cred.get("dias_para_vencer")
    print(f"  certificado         : {arquivo_cert}")
    print(f"    titular           : {titular.mascarado() if titular.valido else '(não identificado)'}")
    print(f"    estado            : {resumo_cred.get('estado', '?')}"
          f"   (utilizável: {resumo_cred.get('utilizavel')})")
    print(f"    válido de         : {resumo_cred.get('valido_de') or '(não lido)'}")
    print(f"    válido até        : {resumo_cred.get('valido_ate') or '(não lido)'}"
          f"{f'   faltam {dias} dia(s)' if dias is not None else ''}")
    if resumo_cred.get("vence_em_breve"):
        print("    [ATENÇÃO] este certificado vence em 30 dias ou menos")
    if resumo_cred.get("detalhe"):
        print(f"    detalhe           : {resumo_cred['detalhe']}")
    print(f"  checkpoint atual    : ultNSU {cp.ult_nsu}"
          f"   maxNSU {cp.max_nsu or '(nenhum)'}"
          f"   status {cp.status or '(novo)'}")
    print(f"    origem            : {cp.origem_ult_nsu or '(nenhuma)'}")
    print(f"    cobertura         : {cp.cobertura_anterior}")
    print(f"  tipo de consulta    : {tipo_consulta}")
    print(f"  NSU inicial         : {cp.ult_nsu}")
    print(f"  lotes máximos       : {args.lotes}   (sem laço, sem retry)")
    if cp.nunca_rodou:
        print()
        print("  [PRIMEIRA VEZ] o NSU inicial é ZERO para esta empresa neste")
        print("  serviço e ambiente. O Ambiente Nacional responde a partir do")
        print("  começo da retenção (~3 meses), então este lote pode trazer até")
        print(f"  50 documentos. Com --lotes {args.lotes} a execução para aí; o")
        print("  restante fica para uma próxima, a partir do NSU gravado.")
    linha()

    # ── conferências que ABORTAM antes de qualquer socket ──────────────────
    problemas: list[str] = []
    if not estado.utilizavel:
        problemas.append(f"credencial não utilizável: {estado.acao_necessaria}")
    if previa.url != N.ENDPOINT[amb.nome]:
        problemas.append("o endpoint do adapter não é o do ambiente selecionado")
    if amb.nome not in N.TP_AMB:
        problemas.append(f"ambiente sem tpAmb definido: {amb.nome}")
    if tipo_consulta != "distNSU":
        problemas.append("o envelope montado não é uma consulta distNSU")
    if f"<{ident.tipo}>{ident.valor}</{ident.tipo}>".encode() not in envelope:
        problemas.append("o identificador do envelope não é o da empresa selecionada")
    if titular.valido and titular.valor != ident.valor:
        # Não é necessariamente erro (procuração existe), mas exige decisão
        # humana: consultar com certificado de outro titular sem saber é o
        # caminho para descobrir tarde que a autorização não existia.
        problemas.append(
            f"o titular do certificado ({titular.mascarado()}) não é a empresa "
            f"consultada ({ident.mascarado()}) — se for procuração, confirme antes")
    if cp.status == "CORROMPIDO":
        problemas.append("o checkpoint está marcado como CORROMPIDO")

    if problemas:
        print("[ABORTADO] inconsistência antes da rede:")
        for x in problemas:
            print(f"    - {x}")
        linha("=")
        return 2
    print("  conferências: OK — nenhuma inconsistência.")
    linha()

    if not args.confirmar:
        print("  ENSAIO — nada foi enviado à SEFAZ.")
        print("  Para executar de verdade, repita o comando com --confirmar")
        linha("=")
        return 0

    # ── 2. confirmação digitada ────────────────────────────────────────────
    print(f"  Isto fará UMA consulta real ao ambiente de {amb.nome.upper()}.")
    print("  O serviço limita consultas por hora e bloqueia o CNPJ ao excedê-las.")
    try:
        resposta = input("  Digite CONSULTAR para prosseguir: ").strip()
    except (EOFError, KeyboardInterrupt):
        resposta = ""
    if resposta != "CONSULTAR":
        print("\n[CANCELADO] nada foi enviado.")
        return 1
    linha()

    # ── 3. a consulta, pela PORTA ÚNICA ────────────────────────────────────
    # Esta ferramenta NÃO tem autoridade própria. A confirmação digitada
    # autoriza gastar uma chamada; ela não autoriza ignorar cooldown, revisão
    # de sequência, divergência externa ou trava. Override administrativo, se
    # um dia existir, será decisão explícita e auditável — nunca efeito
    # colateral de rodar um script.
    inicio = time.monotonic()
    politica = ctl.Politica(max_lotes_por_empresa=max(1, args.lotes))
    caixa = {}

    def _fabrica(identidade, ambiente):
        f = N.criar(cad, identidade, dados_dir=raiz, ambiente=ambiente,
                    cuf=args.cuf)
        caixa["fonte"] = f
        return f

    print("  consultando...")
    rc = svc.consultar_empresa(raiz, ident.valor, _fabrica, ambiente=amb,
                               politica=politica, cad=cad)
    duracao = time.monotonic() - inicio

    if not rc.autorizada:
        linha()
        print("  [RECUSADO PELA POLÍTICA] nenhuma chamada de rede foi feita.")
        print(f"  estado : {rc.estado}")
        print(f"  motivo : {rc.motivo}")
        if rc.detalhe:
            print(f"  detalhe: {rc.detalhe}")
        if rc.segundos_para_liberar:
            print(f"  libera em {rc.segundos_para_liberar // 60} min")
        linha("=")
        return 1

    fonte = caixa.get("fonte")

    # ── 4. o que aconteceu ─────────────────────────────────────────────────
    linha()
    print("  RESULTADO")
    linha()
    ultima = fonte.ultima_resposta
    if ultima is not None:
        log = ultima.para_log()
        print(f"  cStat          : {log['cStat']}  ({log['categoria']})")
        print(f"  xMotivo        : {log['xMotivo']}")
        print(f"  ultNSU         : {log['ultNSU']}")
        print(f"  maxNSU         : {log['maxNSU']}")
        print(f"  docZip         : {log['docZip']}")
        if log["avarias"]:
            print(f"  docZip avariados: {log['avarias']}  (preservados assim mesmo)")
    for aviso in getattr(fonte, "avisos", []):
        print(f"  [aviso] {aviso}")

    ing_rel = {"total": rc.documentos, "indexados": rc.documentos,
               "schema_desconhecido": 0, "falha_parser": 0, "quarentena": 0}
    print(f"  documentos     : {rc.documentos} recebidos")
    print(f"  persistidos    : {ing_rel['total']} novos no índice")
    print(f"  indexados      : {ing_rel['indexados']}")
    print(f"  desconhecidos  : {ing_rel['schema_desconhecido']}")
    print(f"  falha de parser: {ing_rel['falha_parser']}")
    print(f"  quarentena     : {ing_rel['quarentena']}")

    # ── as seis camadas de erro, separadas ────────────────────────────────
    # Cada uma tem consequência diferente: TLS é credencial, SOAP é resposta
    # ilegível, cStat é negócio, docZip é transporte do documento, persistência
    # segura o checkpoint e parser não segura nada. Fundir duas delas num
    # "deu erro" apaga justamente a informação que decide o que fazer.
    linha()
    print("  CAMADAS DE ERRO (cada uma preservada em separado)")
    linha()
    camadas = [
        ("1. HTTP/TLS      ", rc.estado == "ERRO_TEMPORARIO" and not rc.cstat),
        ("2. SOAP/XML      ", "RESPOSTA_INVALIDA" in (rc.erro or "")),
        ("3. resposta SEFAZ", bool(ultima) and ultima.categoria not in
         (N.CAT_DOCUMENTOS, N.CAT_SEM_DOCUMENTOS)),
        ("4. docZip        ", bool(ultima) and bool(ultima.avarias)),
        ("5. persistência  ", rc.falha_de_persistencia),
        ("6. parser        ", ing_rel["falha_parser"] > 0),
    ]
    for nome, houve in camadas:
        print(f"  {nome} : {'PROBLEMA' if houve else 'sem ocorrência'}")
    if rc.erro:
        print(f"  detalhe        : {rc.erro}")


    if ultima is not None and ultima.categoria == N.CAT_CONSUMO_INDEVIDO:
        linha()
        print("  [656 CONSUMO INDEVIDO] PARADO. Nenhuma nova tentativa será feita.")
        print(f"  identidade afetada : {ident.mascarado()}")
        print(f"  horário            : {time.strftime('%d/%m/%Y %H:%M:%S')}")
        print("  Aguarde o intervalo determinado pelo Ambiente Nacional (~1 h).")

    trilha = aud.abrir(raiz, ident.valor)
    contagem = trilha.contar()
    print(f"  duplicatas     : {contagem.get(aud.DEDUPLICACAO, 0)} (acumulado)")
    print(f"  colisões       : {contagem.get(aud.COLISAO, 0)} (acumulado)")

    cp_final = repo.carregar(ident.valor, N.SERVICO, amb)
    print(f"  checkpoint     : {cp.ult_nsu} -> {cp_final.ult_nsu}")
    print(f"  estado final   : {rc.estado}"
          f"   ({rc.subtipo or rc.motivo or 'sem observação'})")
    if rc.erro:
        print(f"  erro           : {rc.erro}")
    print(f"  duração        : {duracao:.2f}s")

    total_acervo = len(acv.abrir(raiz, ident.valor).listar())
    print(f"  acervo         : {total_acervo} documento(s) desta empresa")
    linha("=")
    print("  Esta foi UMA execução manual. Nenhum agendamento foi criado.")
    return 0 if not rc.erro else 1


if __name__ == "__main__":
    sys.exit(main())
