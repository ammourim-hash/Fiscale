#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Identidade canônica de eventos NF-e e migração do acervo (ING 4D-2B).

    python teste_ingestao4d2b.py

O QUE ORIGINOU ESTA SUÍTE
    No segundo ciclo real do controlador (16/08/2026) chegou um evento que o
    acervo classificou como `COLISAO`. Investigado: eram DOIS eventos legítimos
    da mesma NF-e — `tpEvento 610514` (Registro de Passagem propagado por
    MDF-e/CT-e), ambos com `nSeqEvento 1`, registrados por ÓRGÃOS diferentes.

    A identidade era `sha256("EVENTO_NFE|<chave>|<tpEvento>|<nSeq>")`, e por
    isso os dois recebiam o mesmo identificador. Os bytes não se perderam — o
    acervo preservou a segunda cópia —, mas o segundo evento ficou fora do
    índice, o que para a APURAÇÃO é grave: cancelamento e demais eventos
    decidem quais documentos entram numa competência.

O QUE ESTA SUÍTE GARANTE
    Que `cOrgao` distingue eventos distintos, que `resEvento` e
    `procEventoNFe` do mesmo evento lógico continuam sendo o MESMO documento,
    que a promoção resumo → completo sobrevive, e que a migração é idempotente
    e nunca junta o que está ambíguo.

FIXTURES fictícias. REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import acervo as acv                 # noqa: E402
from ingestao import auditoria as aud              # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import identificacao as idf          # noqa: E402
from ingestao import indice as idx                 # noqa: E402
from ingestao import migracao_eventos as mig       # noqa: E402
from ingestao import parsers as prs                # noqa: E402
from ingestao.ambiente import PRODUCAO             # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402
from ingestao.distribuicao import DocumentoBruto   # noqa: E402

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


EMP = T.EMPRESA_A
CH = T.CHAVE_1
PASSAGEM = "610514"


def guardar(raiz, conteudo, empresa=EMP, nsu="000000000000001"):
    """Preserva um documento pelo caminho normal do acervo."""
    ac = acv.abrir(raiz, empresa, servico=cpm.NFE_DISTRIBUICAO, ambiente=PRODUCAO)
    return ac.preservar(DocumentoBruto(conteudo=conteudo, nsu=nsu,
                                       schema="resEvento_v1.01.xsd"))


# ══════════════════════════════════════════════════════════════════════════
secao("A fórmula: o que entra na identidade do evento")
a = idf.identificar(T.xml_res_evento(CH, tp=PASSAGEM, orgao="33"))
b = idf.identificar(T.xml_res_evento(CH, tp=PASSAGEM, orgao="29"))
igual(a.orgao, "33", "o cOrgao é lido do documento")
ok(a.id_documento != b.id_documento,
   "mesma chave + tipo + sequência, ÓRGÃO diferente -> eventos DISTINTOS")
igual(idf.id_de_evento_legado(CH, PASSAGEM, "1"),
      idf.id_de_evento_legado(CH, PASSAGEM, "1"),
      "a fórmula legada continua existindo, para a migração saber a origem")
ok(idf.id_de_evento_legado(CH, PASSAGEM, "1") not in
   (a.id_documento, b.id_documento),
   "e nenhum dos dois herda o identificador antigo por acaso")

secao("Mesmo órgão: continua sendo o mesmo evento")
c = idf.identificar(T.xml_res_evento(CH, tp=PASSAGEM, orgao="33",
                                     protocolo="126000000000777"))
igual(a.id_documento, c.id_documento,
      "mesma chave + tipo + sequência + MESMO órgão -> mesmo evento")
ok(a.hash_conteudo != c.hash_conteudo,
   "mesmo com protocolo diferente, que muda os bytes")

secao("Sequência diferente e tipo diferente continuam separando")
ok(idf.identificar(T.xml_res_evento(CH, tp=PASSAGEM, seq="2", orgao="33")
                   ).id_documento != a.id_documento, "nSeqEvento distingue")
ok(idf.identificar(T.xml_res_evento(CH, tp="110111", orgao="33")
                   ).id_documento != a.id_documento, "tpEvento distingue")
ok(idf.identificar(T.xml_res_evento(T.CHAVE_2, tp=PASSAGEM, orgao="33")
                   ).id_documento != a.id_documento, "a chave distingue")

secao("`resEvento` e `procEventoNFe` do mesmo evento são o MESMO documento")
res = idf.identificar(T.xml_res_evento(CH, tp="110111", orgao="26"))
proc = idf.identificar(T.xml_evento(CH, tp="110111", orgao="26"))
igual(res.id_documento, proc.id_documento,
      "resumo e completo convergem para a mesma identidade")
ok(res.prioridade < proc.prioridade,
   "com prioridades diferentes — é isso que permite a promoção")
ok(res.hash_conteudo != proc.hash_conteudo,
   "e bytes diferentes: a identidade NÃO é o hash do XML")

secao("O cOrgao vem do bloco <evento>, não do primeiro que aparecer")
divergente = idf.identificar(T.xml_evento(CH, tp="110111", orgao="26",
                                          orgao_resposta="91"))
igual(divergente.orgao, "26",
      "retEvento com outro cOrgao não muda a identidade do evento")
igual(divergente.id_documento, res.id_documento,
      "e o resumo daquele evento continua convergindo")

secao("`nProt` fica FORA da identidade")
p1 = idf.identificar(T.xml_res_evento(CH, tp="110111", orgao="26",
                                      protocolo="126000000000009"))
p2 = idf.identificar(T.xml_res_evento(CH, tp="110111", orgao="26",
                                      protocolo="126000000000123"))
igual(p1.id_documento, p2.id_documento,
      "protocolo diferente não cria evento novo — no acervo real o mesmo "
      "evento tem dois nProt (o da NF-e e o do evento)")

secao("Ausência de cOrgao: não inventa identidade nem junta por otimismo")
sem = idf.identificar(T.xml_evento(CH, tp="110111", omitir_orgao=True))
igual(sem.id_documento, idf.id_de_evento_legado(CH, "110111", "1"),
      "sem cOrgao a identidade é a legada — nada é fabricado")
ok(any("sem cOrgao" in x for x in sem.avisos), "e o aviso fica registrado")
ok(sem.id_documento != res.id_documento,
   "um evento SEM órgão não é fundido com um COM órgão — separar é reversível")

# ══════════════════════════════════════════════════════════════════════════
secao("No acervo: dois órgãos viram dois documentos, sem colisão")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    r1 = guardar(raiz, T.xml_res_evento(CH, tp=PASSAGEM, orgao="33"))
    r2 = guardar(raiz, T.xml_res_evento(CH, tp=PASSAGEM, orgao="29"))
    igual(r1.resultado, acv.NOVO, "o primeiro entra como novo")
    igual(r2.resultado, acv.NOVO, "o segundo TAMBÉM — não é mais colisão")
    ok(r1.id_documento != r2.id_documento, "com identidades próprias")
    igual(len(acv.abrir(raiz, EMP).listar()), 2, "dois documentos no acervo")
    contagem = aud.abrir(raiz, EMP).contar()
    igual(contagem.get(aud.COLISAO, 0), 0, "e nenhuma COLISAO registrada")

secao("O mesmo evento recebido de novo é duplicata idempotente")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    xml = T.xml_res_evento(CH, tp=PASSAGEM, orgao="33")
    guardar(raiz, xml)
    antes = sorted(p.name for p in (Path(raiz) / EMP / "acervo").rglob("*"))
    r = guardar(raiz, xml)
    igual(r.resultado, acv.DUPLICATA, "segunda vez é duplicata")
    igual(sorted(p.name for p in (Path(raiz) / EMP / "acervo").rglob("*")), antes,
          "e nada foi escrito no acervo")

secao("Promoção resumo -> completo sobrevive à identidade nova")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    guardar(raiz, T.xml_res_evento(CH, tp="110111", orgao="26"))
    r = guardar(raiz, T.xml_evento(CH, tp="110111", orgao="26"))
    igual(r.resultado, acv.COPIA_PROMOVIDA, "o completo promove o resumo")
    igual(len(acv.abrir(raiz, EMP).listar()), 1, "e continua sendo UM documento")
    ac = acv.abrir(raiz, EMP)
    cap = ac.ler_captura(idf.EVENTO_NFE, r.id_documento)
    igual(cap["prioridade"], idf.PRIO_AUTORIZADO, "com a prioridade do completo")
    ok(cap["canonico"] != acv.ORIGINAL, "o canônico passou a ser a cópia nova")
    ok((ac.pasta_do(idf.EVENTO_NFE, r.id_documento) / acv.ORIGINAL).exists(),
       "e o original.xml continua no lugar, intacto")

secao("Índice: os dois eventos aparecem, com o órgão de cada um")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    guardar(raiz, T.xml_res_evento(CH, tp=PASSAGEM, orgao="33"))
    guardar(raiz, T.xml_res_evento(CH, tp=PASSAGEM, orgao="29"))
    idx.reconstruir(raiz, EMP)
    ind = idx.abrir(raiz, EMP)
    eventos = ind.eventos_da_chave(CH)
    igual(len(eventos), 2, "os DOIS eventos estão indexados")
    igual(sorted(e["orgao"] for e in eventos), ["29", "33"],
          "cada um com o seu cOrgao — consultável, não só preservado")

# ══════════════════════════════════════════════════════════════════════════
secao("Migração: dry-run não escreve nada")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    # Monta um acervo com a identidade ANTIGA, como estava em disco.
    xml_a = T.xml_res_evento(CH, tp=PASSAGEM, orgao="33")
    xml_b = T.xml_res_evento(CH, tp=PASSAGEM, orgao="29")
    id_velho = idf.id_de_evento_legado(CH, PASSAGEM, "1")
    pasta = Path(raiz) / EMP / "acervo" / idf.EVENTO_NFE / id_velho[:2] / id_velho
    (pasta / "copias").mkdir(parents=True, exist_ok=True)
    (pasta / acv.ORIGINAL).write_bytes(xml_a)
    (pasta / "copias" / "colisao.xml").write_bytes(xml_b)
    (pasta / acv.CAPTURA).write_text(json.dumps(
        {"id_documento": id_velho, "especie": idf.EVENTO_NFE, "chave": CH,
         "quarentena": True, "motivo_quarentena": "duas cópias resumo",
         "canonico": acv.ORIGINAL, "prioridade": idf.PRIO_RESUMO,
         "copias": [{"arquivo": acv.ORIGINAL,
                     "hash": idf.hash_conteudo(xml_a)}]}), encoding="utf-8")

    antes = {p: p.read_bytes() for p in pasta.rglob("*") if p.is_file()}
    plano = mig.planejar(raiz, EMP)
    r = plano.resumo()
    igual(r["eventos_no_acervo"], 1, "um grupo antigo")
    igual(r["arquivos_xml"], 2, "com dois XML dentro")
    igual(r["colisoes_desfeitas"], 1, "uma colisão seria desfeita")
    igual(r["documentos_novos"], 1, "gerando um documento novo")
    igual(r["revisar"], 0, "nada ambíguo")
    igual(r["erros"], 0, "nenhum erro")
    igual({p: p.read_bytes() for p in pasta.rglob("*") if p.is_file()}, antes,
          "e o dry-run NÃO escreveu um byte")

secao("Migração: aplica, separa e preserva tudo")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    xml_a = T.xml_res_evento(CH, tp=PASSAGEM, orgao="33")
    xml_b = T.xml_res_evento(CH, tp=PASSAGEM, orgao="29")
    id_velho = idf.id_de_evento_legado(CH, PASSAGEM, "1")
    id_a = idf.identificar(xml_a).id_documento
    id_b = idf.identificar(xml_b).id_documento
    pasta = Path(raiz) / EMP / "acervo" / idf.EVENTO_NFE / id_velho[:2] / id_velho
    (pasta / "copias").mkdir(parents=True, exist_ok=True)
    (pasta / acv.ORIGINAL).write_bytes(xml_a)
    (pasta / "copias" / "colisao.xml").write_bytes(xml_b)
    (pasta / acv.CAPTURA).write_text(json.dumps(
        {"id_documento": id_velho, "especie": idf.EVENTO_NFE, "chave": CH,
         "quarentena": True, "motivo_quarentena": "duas cópias resumo",
         "canonico": acv.ORIGINAL, "prioridade": idf.PRIO_RESUMO,
         "copias": [{"arquivo": acv.ORIGINAL,
                     "hash": idf.hash_conteudo(xml_a)}]}), encoding="utf-8")
    cp_antes = RepositorioCheckpoint(raiz).carregar(
        EMP, cpm.NFE_DISTRIBUICAO, PRODUCAO)
    cp_antes.ult_nsu = "000000000000123"
    RepositorioCheckpoint(raiz).salvar(cp_antes)

    feitos = mig.aplicar(raiz, EMP)
    base = Path(raiz) / EMP / "acervo" / idf.EVENTO_NFE
    pa = base / id_a[:2] / id_a
    pb = base / id_b[:2] / id_b

    igual(feitos["separados"], 1, "uma separação executada")
    igual(feitos["documentos_criados"], 1, "um documento criado")
    ok(pa.exists() and pb.exists(), "as duas pastas novas existem")
    igual((pa / acv.ORIGINAL).read_bytes(), xml_a,
          "o original do órgão 33 tem os MESMOS bytes de antes")
    igual((pb / acv.ORIGINAL).read_bytes(), xml_b,
          "e o do órgão 29 tem os bytes que estavam na cópia")
    ok(not (base / id_velho[:2] / id_velho).exists(),
       "a pasta com o nome antigo não ficou para trás")
    ok((pa / "copias" / "colisao.xml").exists(),
       "a cópia preservada continua onde estava — nada foi apagado")
    cap_a = json.loads((pa / acv.CAPTURA).read_text("utf-8"))
    cap_b = json.loads((pb / acv.CAPTURA).read_text("utf-8"))
    ok(not cap_a["quarentena"], "a quarentena da falsa colisão foi levantada")
    ok("motivo_quarentena" not in cap_a, "e o motivo dela saiu junto")
    igual(cap_a["separado_em"], [id_b], "o grupo A aponta para o B")
    igual(cap_b["separado_de"], id_velho, "e o B registra de onde veio")
    igual(cap_a["id_anterior"], id_velho, "o identificador antigo fica rastreável")
    igual(cap_a["orgao"], "33", "com o órgão gravado")
    igual(cap_b["orgao"], "29", "em cada um")

    cp_depois = RepositorioCheckpoint(raiz).carregar(
        EMP, cpm.NFE_DISTRIBUICAO, PRODUCAO)
    igual(cp_depois.ult_nsu, "000000000000123",
          "o checkpoint NÃO foi tocado pela migração")

    atos = aud.abrir(raiz, EMP).contar()
    igual(atos.get(aud.MIGRACAO_IDENTIDADE, 0), 2,
          "dois atos de migração auditados: o renomeado e o separado")

    ind = idx.abrir(raiz, EMP)
    eventos = ind.eventos_da_chave(CH)
    igual(len(eventos), 2, "AMBOS indexados depois da migração")
    igual(sorted(e["orgao"] for e in eventos), ["29", "33"],
          "o evento que estava preso como colisão voltou para o índice")

secao("Migração é idempotente")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    xml_a = T.xml_res_evento(CH, tp=PASSAGEM, orgao="33")
    xml_b = T.xml_res_evento(CH, tp=PASSAGEM, orgao="29")
    id_velho = idf.id_de_evento_legado(CH, PASSAGEM, "1")
    pasta = Path(raiz) / EMP / "acervo" / idf.EVENTO_NFE / id_velho[:2] / id_velho
    (pasta / "copias").mkdir(parents=True, exist_ok=True)
    (pasta / acv.ORIGINAL).write_bytes(xml_a)
    (pasta / "copias" / "colisao.xml").write_bytes(xml_b)
    mig.aplicar(raiz, EMP)

    base = Path(raiz) / EMP / "acervo"
    depois_1 = {str(p.relative_to(base)): p.read_bytes()
                for p in base.rglob("*.xml")}
    plano2 = mig.planejar(raiz, EMP)
    igual(plano2.resumo()["ids_alterados"], 0,
          "a segunda passada não encontra nada a alterar")
    igual(plano2.resumo()["ja_migrados"], len(plano2.grupos),
          "todos os grupos já estão migrados")
    feitos2 = mig.aplicar(raiz, EMP)
    igual(feitos2["renomeados"], 0, "nada é renomeado de novo")
    igual(feitos2["documentos_criados"], 0, "nada é criado de novo")
    igual({str(p.relative_to(base)): p.read_bytes()
           for p in base.rglob("*.xml")}, depois_1,
          "e os XML ficam byte a byte idênticos")

secao("Dados insuficientes: a migração NÃO decide, manda revisar")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    com = T.xml_res_evento(CH, tp=PASSAGEM, orgao="33")
    sem = T.xml_evento(CH, tp=PASSAGEM, omitir_orgao=True)
    id_velho = idf.id_de_evento_legado(CH, PASSAGEM, "1")
    pasta = Path(raiz) / EMP / "acervo" / idf.EVENTO_NFE / id_velho[:2] / id_velho
    (pasta / "copias").mkdir(parents=True, exist_ok=True)
    (pasta / acv.ORIGINAL).write_bytes(sem)
    (pasta / "copias" / "outro.xml").write_bytes(com)

    plano = mig.planejar(raiz, EMP)
    igual(plano.resumo()["revisar"], 1, "o grupo ambíguo vai para revisão")
    igual(plano.resumo()["ids_alterados"], 0, "e não é migrado")
    antes = {str(p): p.read_bytes() for p in pasta.rglob("*") if p.is_file()}
    mig.aplicar(raiz, EMP, reindexar=False)
    igual({str(p): p.read_bytes() for p in pasta.rglob("*") if p.is_file()}, antes,
          "aplicar não mexe em grupo marcado para revisão")

secao("Reconstrução completa do índice a partir do acervo migrado")
with apoio.raiz_temporaria("i4d2b_") as raiz:
    guardar(raiz, T.xml_res_evento(CH, tp=PASSAGEM, orgao="33"))
    guardar(raiz, T.xml_res_evento(CH, tp=PASSAGEM, orgao="29"))
    guardar(raiz, T.xml_evento(CH, tp="110111", orgao="26"))
    idx.reconstruir(raiz, EMP)
    hashes = {}
    ac = acv.abrir(raiz, EMP)
    for especie, ident in ac.listar():
        p = ac.pasta_do(especie, ident) / acv.ORIGINAL
        hashes[ident] = idf.hash_conteudo(p.read_bytes())
    rel = idx.reconstruir(raiz, EMP)
    igual(rel.resumo()["total"], 3, "os três documentos reindexados")
    igual(len(idx.abrir(raiz, EMP).eventos_da_chave(CH)), 3,
          "e os três aparecem no índice")
    for especie, ident in ac.listar():
        p = ac.pasta_do(especie, ident) / acv.ORIGINAL
        igual(idf.hash_conteudo(p.read_bytes()), hashes[ident],
              f"original de {ident[:8]} intacto após reconstrução")

secao("A versão do parser subiu — o índice sabe que precisa reprocessar")
igual(prs.VERSAO_EVENTO, 3, "VERSAO_EVENTO = 3")
igual(prs.ParserEventoNFe.VERSAO, prs.VERSAO_EVENTO, "e o parser usa essa versão")

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
print("ING 4D-2B: todos os testes passaram.")
