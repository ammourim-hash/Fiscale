#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da ING 4C — controlador multiempresa da NF-e, sem rede.

    python teste_ingestao4c.py

O QUE ESTA SUÍTE PROVA
    Que o ciclo decide corretamente QUEM consultar, consulta só esses, persiste
    tudo antes de seguir, e que **uma empresa com problema nunca para as
    outras**. E que os estados não se misturam: "não consultei porque está em
    dia" e "não consultei porque o certificado venceu" pedem ações opostas de
    quem lê o relatório.

CNPJs FICTÍCIOS
    Os estados das quatro empresas reais já sincronizadas são REPRODUZIDOS com
    dados fictícios. Nenhum CNPJ real aparece aqui.

REDE BLOQUEADA, conferido no fim.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as apoio                        # noqa: E402
import teste_fixturas_nfe as T                     # noqa: E402
from ingestao import acervo as acv                 # noqa: E402
from ingestao import auditoria as aud              # noqa: E402
from ingestao import checkpoint as cpm             # noqa: E402
from ingestao import controlador as ctl            # noqa: E402
from ingestao import operacao as op                # noqa: E402
from ingestao.ambiente import PRODUCAO             # noqa: E402
from ingestao.checkpoint import RepositorioCheckpoint  # noqa: E402
from ingestao.conectores import nfe_dfe as N       # noqa: E402
from ingestao.distribuicao import ErroTransitorio  # noqa: E402

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
    ok(a == b, desc + ("" if a == b else f"  (obtive {a!r}, esperava {b!r})"))


# `AGORA` precisa ser um instante NO FUTURO em relação ao relógio da máquina.
# Vários cenários deixam o transporte falso carimbar a resposta com a hora real
# e o produto conta a janela do 656 a partir do MAIOR entre os dois
# (`servico_distribuicao`: `rejeitado_em = max(carimbo, q)` — bloquear de menos
# nunca). Uma data fixa cumpria isso quando a suíte foi escrita e deixou de
# cumprir quando o calendário a alcançou: em 20/08/2026 três verificações
# passaram a falhar sem que uma linha do produto mudasse. Derivar do relógio
# mantém a intenção original em qualquer data de execução.
#
# O `microsecond=0` não é enfeite: o estado grava com `timespec="seconds"`, e
# várias asserções comparam a string ISO campo a campo.
AGORA = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
SERV = cpm.NFE_DISTRIBUICAO
SENHA = "frase-ficticia-do-teste-4c"

# Empresas fictícias que reproduzem os estados reais já observados.
E_SINCRONIA = T.cnpj_ficticio("111111110001")     # como a 00.***-53
E_PENDENTE = T.cnpj_ficticio("222222220001")      # como a 01.***-86 no meio
E_OUTRA = T.cnpj_ficticio("333333330001")
E_DIVERGENTE = T.cnpj_ficticio("444444440001")    # como a MONTE
E_EXPIRADO = T.cnpj_ficticio("555555550001")
E_COOLDOWN = T.cnpj_ficticio("666666660001")
E_NOVA = T.cnpj_ficticio("777777770001")          # nunca sincronizada


# ── montagem de cenário ─────────────────────────────────────────────────────
def cadastrar(raiz, cnpj, dias_validade=365):
    """Cria .pfx + registro em certificados.json. Certificado GERADO na hora."""
    import json
    import seguranca
    certs = Path(raiz) / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    arq = certs / f"{cnpj}.pfx"
    apoio.gerar_pfx(arq, SENHA, cn=f"EMPRESA {cnpj[:4]}",
                    dias_validade=dias_validade)
    reg = Path(raiz) / "certificados.json"
    atual = json.loads(reg.read_text("utf-8")) if reg.exists() else []
    atual.append({"id": cnpj, "cnpj": cnpj, "nome": f"EMPRESA {cnpj[:4]}",
                  "caminho": f"certs/{cnpj}.pfx",
                  "senha_protegida": seguranca.proteger(SENHA, raiz)})
    reg.write_text(json.dumps(atual, ensure_ascii=False), encoding="utf-8")


def cp_de(raiz, cnpj, **kw):
    repo = RepositorioCheckpoint(raiz)
    cp = repo.carregar(cnpj, SERV, PRODUCAO)
    for k, v in kw.items():
        setattr(cp, k, v)
    repo.salvar(cp)
    return cp


def op_de(raiz, cnpj, **kw):
    repo = op.RepositorioOperacao(raiz)
    e = repo.carregar(cnpj, SERV, PRODUCAO)
    for k, v in kw.items():
        setattr(e, k, v)
    repo.salvar(e)
    return e


def cenario(raiz):
    """Sete empresas, uma de cada situação."""
    for c in (E_SINCRONIA, E_PENDENTE, E_OUTRA, E_DIVERGENTE, E_COOLDOWN, E_NOVA):
        cadastrar(raiz, c)
    cadastrar(raiz, E_EXPIRADO, dias_validade=-10)      # já vencido

    # elegíveis: têm posição herdada e nada as impede
    for c in (E_SINCRONIA, E_PENDENTE, E_OUTRA):
        cp_de(raiz, c, ult_nsu="000000000000100", max_nsu="000000000000150",
              status=cpm.OK, origem_ult_nsu=cpm.ORIGEM_LEGADO,
              cobertura_anterior=cpm.COBERTURA_ACERVO_LEGADO)
    # divergente: o caso da MONTE
    cp = RepositorioCheckpoint(raiz).carregar(E_DIVERGENTE, SERV, PRODUCAO)
    cp.registrar_observacao_sefaz("000000000001361", cstat="656")
    cp.marcar_possivel_consumidor_externo("teste")
    RepositorioCheckpoint(raiz).salvar(cp)
    # expirado: tem posição, mas o certificado venceu
    cp_de(raiz, E_EXPIRADO, ult_nsu="000000000000010",
          origem_ult_nsu=cpm.ORIGEM_LEGADO)
    # cooldown: consultada há pouco
    cp_de(raiz, E_COOLDOWN, ult_nsu="000000000000010",
          origem_ult_nsu=cpm.ORIGEM_LEGADO)
    op_de(raiz, E_COOLDOWN,
          proxima_consulta_permitida_em=(AGORA + timedelta(minutes=30)
                                         ).isoformat(timespec="seconds"),
          motivo_bloqueio="em dia — aguardando a próxima janela",
          ultima_consulta_em=(AGORA - timedelta(minutes=30)
                              ).isoformat(timespec="seconds"))
    # E_NOVA: nada — checkpoint zero, sem origem, nunca consultada
    return [E_SINCRONIA, E_PENDENTE, E_OUTRA, E_DIVERGENTE, E_EXPIRADO,
            E_COOLDOWN, E_NOVA]


# ── dublês ──────────────────────────────────────────────────────────────────
import base64                                      # noqa: E402
import gzip                                        # noqa: E402


def doczip(nsu, xml):
    return (f'<docZip NSU="{nsu}" schema="procNFe_v4.00.xsd">'
            f'{base64.b64encode(gzip.compress(xml)).decode()}</docZip>')


def resposta(cstat="138", motivo="Documento localizado", ult="000000000000150",
             maximo="000000000000150", docs=()):
    lote = f"<loteDistDFeInt>{''.join(docs)}</loteDistDFeInt>" if docs else ""
    return f"""<?xml version="1.0"?><soap:Envelope
 xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<retDistDFeInt xmlns="{N.NS_NFE}" versao="1.01"><tpAmb>1</tpAmb>
<cStat>{cstat}</cStat><xMotivo>{motivo}</xMotivo>
<ultNSU>{ult}</ultNSU><maxNSU>{maximo}</maxNSU>{lote}
</retDistDFeInt></soap:Body></soap:Envelope>""".encode("utf-8")


RESP_656 = resposta("656", "Rejeicao: Consumo Indevido", "000000000009999",
                    "000000000000000")


class Trans:
    def __init__(self, por_empresa=None, padrao=None):
        self.por_empresa = por_empresa or {}
        self.padrao = padrao
        self.chamadas = []

    def para(self, cnpj):
        t = self

        class _T:
            def enviar(_s, url, corpo):
                t.chamadas.append(cnpj)
                fila = t.por_empresa.get(cnpj)
                if fila:
                    item = fila.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return item
                return t.padrao or resposta(
                    "137", "Nenhum documento localizado",
                    "000000000000100", "000000000000100")
        return _T()


def fabrica(trans, acervo_quebrado=()):
    def _f(identidade, ambiente):
        return N.FonteNFeDistribuicaoDFe(identidade=identidade,
                                         transporte=trans.para(identidade),
                                         ambiente=ambiente, cuf="26")
    return _f


POL = ctl.Politica(usar_trava=False)


# ── ING 4C-1: nenhuma empresa cadastrada some do controlador ────────────────
from ingestao.identidade import normalizar as _norm     # noqa: E402


def cadastrar_so_em_clientes(raiz, cnpj, nome="EMPRESA SEM CREDENCIAL"):
    """Empresa que existe no cadastro de clientes e NÃO em certificados.json.

    É o caso real da 27.***.***/0001-64: conhecida, sem credencial vinculada,
    porque a senha do .pfx nunca foi informada.
    """
    import json
    reg = Path(raiz) / "state_clientes.json"
    atual = json.loads(reg.read_text("utf-8")) if reg.exists() else {"clientes": []}
    atual.setdefault("clientes", []).append(
        {"cnpj": cnpj, "nome": nome, "uf": "PE"})
    reg.write_text(json.dumps(atual, ensure_ascii=False), encoding="utf-8")


def pfx_orfao(raiz, cnpj):
    """Um .pfx no disco cujo NOME carrega o CNPJ, sem registro nenhum."""
    certs = Path(raiz) / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    arq = certs / f"CERTIFICADO {cnpj} 12345.pfx"
    apoio.gerar_pfx(arq, "senha-que-o-fiscale-nao-conhece", cn="ORFAO")
    return arq


E_SEM_CRED = T.cnpj_ficticio("888888880001")      # como a 27.***.***/0001-64

secao("O universo do ciclo é o CADASTRO, não as pastas em disco")
with apoio.raiz_temporaria("i4c_") as raiz:
    todas = cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    u = ctl.universo(raiz)
    igual(len(u), len(todas) + 1, "as 7 do cenário mais a sem credencial")
    ok(E_SEM_CRED in u,
       "empresa sem credencial ENTRA no universo — o motivo vem da avaliação")
    pastas = {p.name for p in Path(raiz).iterdir() if p.is_dir()}
    ok(E_SEM_CRED not in pastas,
       "e entra mesmo sem ter pasta em dados/ — foi assim que a real sumiu")

secao("Empresa cadastrada sem credencial: CREDENCIAL_INDISPONIVEL, com motivo")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    el = ctl.avaliar(raiz, E_SEM_CRED, politica=POL, agora=AGORA)
    ok(not el.pode, "não é elegível")
    igual(el.estado, op.CREDENCIAL_INDISPONIVEL, "e o estado diz por quê")
    igual(el.motivo, ctl.MOTIVO_CREDENCIAL, "com motivo legível")
    ok(bool(el.detalhe), "e detalhe preenchido, não vazio")

secao("PFX órfão conhecido: continua CREDENCIAL_INDISPONIVEL")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    arq = pfx_orfao(raiz, E_SEM_CRED)
    el = ctl.avaliar(raiz, E_SEM_CRED, politica=POL, agora=AGORA)
    ok(not el.pode, "existir um .pfx no disco NÃO torna a empresa elegível")
    igual(el.estado, op.CREDENCIAL_INDISPONIVEL, "o estado não muda")
    ok("precisa ser validada" in el.detalhe,
       "mas o detalhe muda: a ação é informar a senha, não caçar o arquivo")
    import json as _json
    regs = _json.loads((Path(raiz) / "certificados.json").read_text("utf-8"))
    ok(all(r.get("cnpj") != E_SEM_CRED for r in regs),
       "e NENHUM vínculo foi criado em certificados.json")
    ok(arq.exists(), "o .pfx órfão continua intacto no disco")

secao("A busca pelo .pfx órfão não abre o arquivo nem tenta senha")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    arq = pfx_orfao(raiz, E_SEM_CRED)
    from cryptography.hazmat.primitives.serialization import pkcs12 as _p12
    from ingestao import cadastro as _cad
    from ingestao import credencial_estado as _ce
    aberturas = []
    _orig_load = _p12.load_key_and_certificates

    def _espiao(*a, **k):
        aberturas.append(1)
        return _orig_load(*a, **k)

    _p12.load_key_and_certificates = _espiao
    try:
        achado = _ce.procurar_pfx_nao_vinculado(_cad.carregar(Path(raiz)),
                                                E_SEM_CRED, Path(raiz))
        el = ctl.avaliar(raiz, E_SEM_CRED, politica=POL, agora=AGORA)
    finally:
        _p12.load_key_and_certificates = _orig_load
    igual(Path(achado).name, arq.name, "achou o arquivo pelo nome")
    igual(aberturas, [], "e ninguém abriu o PKCS#12 — nem uma tentativa de senha")
    igual(el.estado, op.CREDENCIAL_INDISPONIVEL, "o diagnóstico saiu assim mesmo")

secao("O .pfx de OUTRA empresa não é adotado como sendo desta")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    pfx_orfao(raiz, T.cnpj_ficticio("999999990001"))     # órfão de outra
    el = ctl.avaliar(raiz, E_SEM_CRED, politica=POL, agora=AGORA)
    igual(el.detalhe, "nenhum certificado associado",
          "sem arquivo com o CNPJ dela, o diagnóstico volta a ser o honesto")

secao("Contagem: toda empresa cadastrada é avaliada e classificada")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    total = len(ctl.universo(raiz))
    fila = ctl.ordem_do_ciclo(raiz, agora=AGORA)
    igual(len(fila), total, "ordem_do_ciclo() sem lista avalia TODAS")
    igual(len({e.identidade_mascarada for e in fila}), total,
          "cada uma aparece exatamente uma vez")
    res = ctl.executar_ciclo(raiz, politica=POL, dry_run=True, agora=AGORA)
    igual(res.avaliadas, total, "o ciclo avalia o mesmo total")
    igual(len(res.empresas), total, "e relata uma linha por empresa")

secao("O relatório FECHA: soma das classificações == avaliadas")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    res = ctl.executar_ciclo(raiz, politica=POL, dry_run=True, agora=AGORA)
    r = res.resumo()
    soma = sum(r[c] for c in ("em_sincronia", "pendentes", "bloqueadas",
                              "divergencia_externa", "aguardando_onboarding",
                              "certificado_expirado", "credencial_indisponivel",
                              "desabilitadas", "em_execucao",
                              "erros_temporarios", "erros_permanentes"))
    igual(soma, r["avaliadas"], "nenhuma empresa fica fora de todas as caixas")
    igual(r["credencial_indisponivel"], 1, "a sem credencial tem caixa própria")
    igual(r["certificado_expirado"], 1, "e a vencida tem a dela, separada")
    texto = res.relatorio()
    ok("Total classificado" in texto, "o relatório mostra o total classificado")
    ok("ATENCAO" not in texto, "e não acusa ninguém sumido")

secao("Uma empresa sem credencial não impede as elegíveis do mesmo ciclo")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    pfx_orfao(raiz, E_SEM_CRED)
    trans = Trans()
    res = ctl.executar_ciclo(raiz, politica=POL, fabrica_fonte=fabrica(trans),
                             agora=AGORA)
    igual(res.consultadas, 3, "as três elegíveis foram consultadas assim mesmo")
    linha = [e for e in res.empresas
             if e.identidade_mascarada == _norm(E_SEM_CRED).mascarado()]
    igual(len(linha), 1, "e a sem credencial aparece no relatório")
    ok(not linha[0].consultada, "sem ter sido consultada")
    igual(linha[0].estado, op.CREDENCIAL_INDISPONIVEL, "com o estado certo")
    ok(E_SEM_CRED not in trans.chamadas, "e nenhuma chamada saiu por ela")

secao("A empresa sem credencial não gera rede nem cria pasta")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    pfx_orfao(raiz, E_SEM_CRED)
    antes = list(_TENTATIVAS)
    trans = Trans()
    ctl.executar_ciclo(raiz, [E_SEM_CRED], politica=POL,
                       fabrica_fonte=fabrica(trans), agora=AGORA)
    igual(list(_TENTATIVAS), antes, "nenhuma conexão de rede foi tentada")
    igual(trans.chamadas, [], "nem o transporte foi acionado")
    ok(not (Path(raiz) / E_SEM_CRED).exists(),
       "e nenhuma pasta de dados foi criada para quem não foi consultada")

secao("Lista explícita continua valendo — é a validação progressiva")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cadastrar_so_em_clientes(raiz, E_SEM_CRED)
    fila = ctl.ordem_do_ciclo(raiz, [E_SINCRONIA], agora=AGORA)
    igual(len(fila), 1, "quem passa uma lista recebe só aquela lista")
    igual(fila[0].identidade_mascarada, _norm(E_SINCRONIA).mascarado(),
          "e é a empresa pedida")

secao("O controlador não pré-filtra empresa por credencial")
_ast_ctl = __import__("ast").parse(
    (RAIZ / "nfse" / "backend" / "ingestao" / "controlador.py").read_text("utf-8"))
_chamadas_filtradas = [
    n for n in __import__("ast").walk(_ast_ctl)
    if isinstance(n, __import__("ast").Call)
    and getattr(n.func, "attr", "") == "listar_empresas"
    and any(k.arg == "com_credencial" for k in n.keywords)]
igual(_chamadas_filtradas, [],
      "nenhuma chamada a listar_empresas(com_credencial=...) — era esse o pré-filtro")


# ── ING 4D-3A: nem todo 656 e a mesma coisa ─────────────────────────────────
# Os dois xMotivo abaixo sao TEXTUALMENTE os que a SEFAZ devolveu em
# 17/08/2026, no mesmo ciclo, para duas empresas diferentes. Sao texto de
# servico publico, nao dado de contribuinte — nenhum CNPJ, chave ou protocolo.
MOT_SEQUENCIA = ("Rejeicao: Consumo Indevido (Deve ser utilizado o ultNSU nas "
                 "solicitacoes subsequentes. Tente apos 1 hora)")
MOT_JANELA = ("Rejeicao: Consumo Indevido (Deve ser aguardado 1 hora para "
              "efetuar nova solicitacao caso nao existam mais documentos a "
              "serem pesquisados. Tente apos 1 hora)")


def resp_656(motivo, ult="000000000000100"):
    """Rejeicao 656 como a real: o `ultNSU` devolvido e IGUAL a nossa posicao.

    O padrao importa. Devolver um ultNSU MAIOR que o nosso checkpoint e outro
    caso — divergencia externa (a licao da MONTE) —, e misturar os dois faria
    este teste medir a coisa errada. Nos dois 656 reais de 17/08/2026 o ultNSU
    devolvido era exatamente o que enviamos."""
    return f"""<?xml version="1.0"?><soap:Envelope
 xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<retDistDFeInt xmlns="{N.NS_NFE}" versao="1.01"><tpAmb>1</tpAmb>
<cStat>656</cStat><xMotivo>{motivo}</xMotivo>
<ultNSU>{ult}</ultNSU><maxNSU>000000000000000</maxNSU>
</retDistDFeInt></soap:Body></soap:Envelope>""".encode("utf-8")


secao("Classificacao do 656 pelo xMotivo, nao pelo numero")
igual(N.classificar_consumo_indevido(MOT_SEQUENCIA), N.SEQUENCIA_656,
      "a mensagem que fala do ultNSU e de SEQUENCIA")
igual(N.classificar_consumo_indevido(MOT_JANELA), N.JANELA_656,
      "a que fala de aguardar 1 hora e de JANELA")
igual(N.classificar_consumo_indevido("Rejeicao: Consumo Indevido"),
      N.OUTRO_656, "sem pista, nao se inventa subtipo")
igual(N.classificar_consumo_indevido(""), N.OUTRO_656, "vazio tambem")
ok(N.SEQUENCIA_656 != N.JANELA_656, "sao estados diferentes, nao rotulos")
igual(N.classificar_consumo_indevido(
    "Consumo Indevido: limite de consultas por hora excedido"), N.LIMITE_656,
    "e ha lugar para o subtipo de LIMITE, se a mensagem disser isso")

secao("Sequencia vence janela quando a mensagem tem as duas pistas")
ok("apos 1 hora" in MOT_SEQUENCIA,
   "a mensagem real de sequencia TAMBEM diz 'apos 1 hora'")
igual(N.classificar_consumo_indevido(MOT_SEQUENCIA), N.SEQUENCIA_656,
      "e ainda assim classifica como sequencia — o diagnostico mais grave")

secao("O subtipo chega na Resposta e so existe para consumo indevido")
r = N.interpretar_resposta(resp_656(MOT_JANELA))
igual(r.cstat, "656", "leu o cStat")
igual(r.subtipo_consumo, N.JANELA_656, "e o subtipo")
igual(r.para_log()["subtipo"], N.JANELA_656, "que aparece no log")
r137 = N.interpretar_resposta(resposta("137", "Nenhum documento localizado"))
igual(r137.subtipo_consumo, "", "137 nao tem subtipo de consumo")
ok("subtipo" not in r137.para_log(), "e o log nao inventa a chave")

secao("656 de JANELA: bloqueia por tempo, e o tempo resolve")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_JANELA)]})
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE], politica=POL,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(r.empresas[0].estado, op.BLOQUEADO_CONSUMO_INDEVIDO,
          "estado de bloqueio por consumo")
    igual(e.ultimo_656_subtipo, N.JANELA_656, "com o subtipo gravado")
    ok(not e.revisao_sequencia, "sem trava de revisao")
    igual(r.consultadas, 2, "e a empresa seguinte foi consultada")
    ok(not ctl.avaliar(raiz, E_SINCRONIA, politica=POL, agora=AGORA).pode,
       "agora nao consulta")
    ok(ctl.avaliar(raiz, E_SINCRONIA, politica=POL,
                   agora=AGORA + timedelta(hours=1, minutes=1)).pode,
       "passada a janela, VOLTA sozinha — porque a causa era frequencia")

secao("656 de SEQUENCIA: o tempo NAO resolve")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_SEQUENCIA)]})
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE], politica=POL,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(r.empresas[0].estado, op.REVISAO_DE_SEQUENCIA,
          "estado proprio, nao o de consumo generico")
    igual(e.ultimo_656_subtipo, N.SEQUENCIA_656, "subtipo gravado")
    ok(e.revisao_sequencia, "e a trava que o tempo nao abre esta ligada")
    el = ctl.avaliar(raiz, E_SINCRONIA, politica=POL,
                     agora=AGORA + timedelta(days=7))
    ok(not el.pode, "uma SEMANA depois, continua fora da automacao")
    igual(el.estado, op.REVISAO_DE_SEQUENCIA, "com o estado certo")
    igual(el.motivo, ctl.MOTIVO_REVISAO_SEQUENCIA,
          "e o motivo diz que exige revisao, nao 'em cooldown'")
    igual(r.consultadas, 2, "as outras empresas seguem normalmente")

secao("So decisao humana encerra a revisao de sequencia")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_SEQUENCIA)]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    repo = op.RepositorioOperacao(raiz)
    e = repo.carregar(E_SINCRONIA, SERV, PRODUCAO)
    e.liberar()
    repo.salvar(e)
    ok(not ctl.avaliar(raiz, E_SINCRONIA, politica=POL,
                       agora=AGORA + timedelta(hours=2)).pode,
       "liberar() solta o tempo mas NAO a revisao de sequencia")
    e = repo.carregar(E_SINCRONIA, SERV, PRODUCAO)
    e.encerrar_revisao_de_sequencia(quem="teste")
    repo.salvar(e)
    ok(ctl.avaliar(raiz, E_SINCRONIA, politica=POL,
                   agora=AGORA + timedelta(hours=2)).pode,
       "so encerrar_revisao_de_sequencia() devolve a empresa a automacao")

secao("O bloqueio conta do PROPRIO 656, nao do inicio do ciclo")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_JANELA)]})
    # Como na operacao real: o ciclo comeca alguns segundos ANTES da resposta.
    inicio_ciclo = datetime.now(timezone.utc) - timedelta(seconds=30)
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=inicio_ciclo)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    rej = op._de_iso(e.ultimo_656_em)
    ate = e.bloqueado_ate()
    ok(rej is not None, "o instante da rejeicao foi gravado")
    ok(rej > inicio_ciclo,
       "a rejeicao e posterior ao inicio do ciclo, como na operacao real")
    igual(int((ate - rej).total_seconds()), 3600,
          "e a janela e de 1 h contada DELA, nao do inicio do ciclo")
    ok(ate > inicio_ciclo + timedelta(hours=1),
       "por isso a liberacao cai mais tarde — errar para menos e o que causa 656")

secao("A janela nunca comeca antes da referencia do ciclo")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_JANELA)]})
    # AGORA e uma data no futuro: o carimbo real da resposta e ANTERIOR a ela.
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.segundos_para_liberar(AGORA), 3600,
          "vale a referencia do ciclo — bloquear de menos nunca")

secao("O 656 grava o diagnostico inteiro")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_SEQUENCIA, ult="000000000000100")]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.ultimo_656_nsu_enviado, "000000000000100", "NSU enviado")
    igual(e.ultimo_656_checkpoint_local, "000000000000100", "checkpoint local")
    igual(e.ultimo_656_ult_nsu, "000000000000100", "ultNSU devolvido")
    igual(e.ultimo_656_max_nsu, "000000000000000", "maxNSU devolvido (zero)")
    ok("ultNSU" in e.ultimo_656_motivo, "xMotivo preservado")
    ok(e.origem_bloqueio, "origem do bloqueio escrita")
    s = e.resumo()
    for campo in ("ultimo_656_em", "ultimo_656_subtipo", "revisao_sequencia",
                  "origem_bloqueio"):
        ok(campo in s, f"{campo} aparece no resumo da tela")

secao("Checkpoint NUNCA anda por causa de 656, e maxNSU=0 nao substitui")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    cp_de(raiz, E_SINCRONIA, ult_nsu="000000000000100",
          max_nsu="000000000000150", status=cpm.OK,
          origem_ult_nsu=cpm.ORIGEM_LEGADO)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_SEQUENCIA, ult="000000000009999")]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    cp = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(cp.ult_nsu, "000000000000100",
          "o ultNSU da rejeicao NAO vira posicao nossa")
    igual(cp.max_nsu, "000000000000150",
          "e o maxNSU=0 da rejeicao NAO apaga o maxNSU valido")

secao("Empresa em revisao de sequencia nao impede as demais")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_SEQUENCIA)]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    tr2 = Trans()
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE, E_OUTRA],
                           politica=POL, fabrica_fonte=fabrica(tr2),
                           agora=AGORA + timedelta(hours=3))
    igual(r.consultadas, 2, "as duas saudaveis foram consultadas")
    ok(E_SINCRONIA not in tr2.chamadas,
       "e nenhuma chamada saiu para a que esta em revisao")

secao("Reinicio preserva o bloqueio e a revisao")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_SEQUENCIA)]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    # Repositorio novo = processo novo: so o disco sobrevive.
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    ok(e.revisao_sequencia, "a trava sobreviveu ao 'reinicio'")
    ok(e.ultimo_656_subtipo, "o subtipo tambem")
    ok(e.bloqueado_ate() is not None, "e o bloqueio por tempo")

secao("Timezone nao altera o calculo da janela")
with apoio.raiz_temporaria("i4c_") as raiz:
    from datetime import timezone as _tz
    cenario(raiz)
    e = op_de(raiz, E_COOLDOWN)
    rej_utc = datetime(2026, 8, 20, 12, 0, tzinfo=_tz.utc)
    rej_local = rej_utc.astimezone(_tz(timedelta(hours=-3)))
    a = op.EstadoOperacao(identidade=E_COOLDOWN, servico=SERV, ambiente="producao")
    b = op.EstadoOperacao(identidade=E_COOLDOWN, servico=SERV, ambiente="producao")
    a.registrar_656("X", "m", "1", "1", quando=rej_utc)
    b.registrar_656("X", "m", "1", "1", quando=rej_local)
    a.bloquear(rej_utc + timedelta(hours=1), "x")
    b.bloquear(rej_local + timedelta(hours=1), "x")
    igual(a.segundos_para_liberar(rej_utc), b.segundos_para_liberar(rej_utc),
          "o mesmo instante em fusos diferentes da o mesmo bloqueio")
    igual(op._de_iso(a.ultimo_656_em), op._de_iso(b.ultimo_656_em),
          "e o carimbo aponta o mesmo instante")

secao("CONSULTA_RECONSTRUIDA nao vira 656 nem chamada real")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    aud.abrir(raiz, E_SINCRONIA).registrar(
        aud.CONSULTA_RECONSTRUIDA, servico=SERV, ambiente="producao",
        cstat="656", xmotivo=MOT_SEQUENCIA,
        inicio=AGORA.isoformat(timespec="seconds"))
    r = ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    igual(r["resultado"], ctl.BOOTSTRAP_SEM_HISTORICO,
          "reconstruida nao gera agenda")
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    ok(not e.revisao_sequencia,
       "e NAO liga a trava de revisao a partir de um fato do passado")
    igual(e.ultimo_656_subtipo, "", "nem grava subtipo")

secao("O relatorio mostra a revisao de sequencia separada do 656 comum")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_SEQUENCIA)],
                E_PENDENTE: [resp_656(MOT_JANELA)]})
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE], politica=POL,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    z = r.resumo()
    igual(z["revisao_sequencia"], 1, "uma em revisao de sequencia")
    igual(z["bloqueadas"], 1, "e uma em bloqueio comum")
    texto = r.relatorio()
    ok("Revisao de sequencia" in texto, "o relatorio nomeia a revisao")
    soma = sum(z[c] for c in ("em_sincronia", "pendentes", "bloqueadas",
                              "divergencia_externa", "aguardando_onboarding",
                              "certificado_expirado", "credencial_indisponivel",
                              "revisao_sequencia", "desabilitadas",
                              "em_execucao", "erros_temporarios",
                              "erros_permanentes"))
    igual(soma, z["avaliadas"], "e a classificacao continua fechando")

secao("Hipotese de consumidor externo nao e atribuida sozinha")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resp_656(MOT_SEQUENCIA)]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.hipotese_diagnostica, "",
          "um 656 sozinho NAO acusa consumidor externo")
_fonte_ctl2 = (RAIZ / "nfse" / "backend" / "ingestao" / "controlador.py").read_text("utf-8")
for suspeito in ("Dominio", "Domínio", "ERP", "Portal do Contador"):
    ok(suspeito not in _fonte_ctl2,
       f"o codigo nao nomeia '{suspeito}' como culpado")


# ══════════════════════════════════════════════════════════════════════════
secao("Elegibilidade — cada situação com o seu motivo")
with apoio.raiz_temporaria("i4c_") as raiz:
    todas = cenario(raiz)
    por = {}
    for c in todas:
        el = ctl.avaliar(raiz, c, politica=POL, agora=AGORA)
        por[c] = el

    igual(por[E_SINCRONIA].pode, True, "empresa com posição herdada é elegível")
    igual(por[E_PENDENTE].pode, True, "idem")
    igual(por[E_OUTRA].pode, True, "idem")

    igual(por[E_DIVERGENTE].pode, False, "divergência externa NÃO é consultada")
    igual(por[E_DIVERGENTE].estado, op.DIVERGENCIA_EXTERNA, "com estado próprio")
    ok("Ambiente Nacional informa" in (por[E_DIVERGENTE].detalhe or ""),
       "e o alerta administrativo no detalhe")

    igual(por[E_EXPIRADO].pode, False, "certificado vencido NÃO é consultado")
    igual(por[E_EXPIRADO].estado, op.CERTIFICADO_EXPIRADO,
          "com estado CERTIFICADO_EXPIRADO, não 'erro'")

    igual(por[E_COOLDOWN].pode, False, "em cooldown NÃO é consultada")
    igual(por[E_COOLDOWN].motivo, ctl.MOTIVO_COOLDOWN, "com o motivo certo")
    igual(por[E_COOLDOWN].segundos_para_liberar, 1800, "e quanto falta liberar")

    igual(por[E_NOVA].pode, False, "empresa nova NÃO entra sozinha")
    igual(por[E_NOVA].estado, op.AGUARDANDO_ONBOARDING,
          "estado AGUARDANDO_PRIMEIRA_SINCRONIZACAO")

    estados = {e.estado for e in por.values()}
    # Cinco, não seis: as três elegíveis compartilham PENDENTE — é o mesmo
    # estado operacional, e está certo que compartilhem.
    igual(len(estados), 5, "cinco situações distintas, nenhuma confundida com outra")
    igual(sorted(estados), sorted([op.PENDENTE, op.DIVERGENCIA_EXTERNA,
                                   op.CERTIFICADO_EXPIRADO, op.EM_SINCRONIA,
                                   op.AGUARDANDO_ONBOARDING]),
          "e são exatamente as esperadas")

secao("Empresa desabilitada à mão vence tudo")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    op_de(raiz, E_SINCRONIA, desabilitado=True, motivo_desabilitado="pedido do cliente")
    el = ctl.avaliar(raiz, E_SINCRONIA, politica=POL, agora=AGORA)
    igual(el.estado, op.DESABILITADO, "estado DESABILITADO")
    igual(el.detalhe, "pedido do cliente", "com o motivo humano preservado")

secao("Teto de consultas por hora")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    for i in range(POL.teto_consultas_por_hora):
        e.registrar_consulta(AGORA - timedelta(minutes=i))
    op.RepositorioOperacao(raiz).salvar(e)
    el = ctl.avaliar(raiz, E_SINCRONIA, politica=POL, agora=AGORA)
    igual(el.pode, False, "atingido o teto, não consulta")
    igual(el.motivo, ctl.MOTIVO_TETO_HORA, "com o motivo explícito")

# ── bootstrap: a agenda é derivada, nunca presumida ─────────────────────────
def plantar_consulta(raiz, cnpj, cstat="137", ult="000000000000100",
                     mx="000000000000100", resultado="NENHUM_DOCUMENTO",
                     quando=None, transporte_ok=True, erro="", servico=None,
                     ambiente="producao"):
    """Grava um ato CONSULTA real na trilha, como o conector grava."""
    aud.abrir(raiz, cnpj).registrar_consulta({
        "inicio": (quando or AGORA).isoformat(timespec="seconds"),
        "fim": (quando or AGORA).isoformat(timespec="seconds"),
        "servico": servico or SERV, "ambiente": ambiente,
        "tipo_consulta": "distNSU", "nsu_enviado": ult,
        "endpoint": "www1.nfe.fazenda.gov.br", "transporte_ok": transporte_ok,
        "cstat": cstat, "xmotivo": "motivo de teste",
        "ult_nsu": ult, "max_nsu": mx, "doczip": 0,
        "resultado": resultado, "erro_tecnico": erro,
    })


secao("Bootstrap sem histórico: não presume permissão NEM bloqueio")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    r = ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    igual(r["resultado"], ctl.BOOTSTRAP_SEM_HISTORICO,
          "sem CONSULTA auditada, não há de onde derivar")
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.proxima_consulta_permitida_em, "",
          "e nenhum bloqueio é inventado do nada")

secao("137 com ultNSU == maxNSU: a janela oficial de 1 h é materializada")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "137")
    r = ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    igual(r["resultado"], ctl.BOOTSTRAP_DERIVADO, "derivou da trilha")
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.ultimo_cstat, "137", "o cStat vem da última consulta real")
    igual(e.ultima_consulta_em, AGORA.isoformat(timespec="seconds"),
          "e o horário também")
    igual(e.segundos_para_liberar(AGORA), 3600,
          "1 h contada A PARTIR daquela consulta, não de agora")
    ok(not ctl.avaliar(raiz, E_SINCRONIA, politica=POL, agora=AGORA).pode,
       "e o controlador NÃO consultaria agora — era este o risco de 656")
    ok(ctl.avaliar(raiz, E_SINCRONIA, politica=POL,
                   agora=AGORA + timedelta(hours=1, seconds=1)).pode,
       "passada a hora, volta a ser elegível")

secao("138 que fecha em dia: mesma janela oficial")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "138", "000000000000150",
                     "000000000000150", "DOCUMENTOS_ENCONTRADOS")
    ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.segundos_para_liberar(AGORA), 3600, "1 h também quando veio lote")
    igual(e.ultima_sincronia_confirmada_em,
          AGORA.isoformat(timespec="seconds"),
          "e a sincronia confirmada fica carimbada")

secao("Paginação incompleta: NENHUMA espera entre páginas é inventada")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "138", "000000000000150",
                     "000000000009999", "DOCUMENTOS_ENCONTRADOS")
    r = ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.segundos_para_liberar(AGORA), 0,
          "ultNSU < maxNSU não gera espera — não existe regra oficial disso")
    igual(e.proxima_consulta_permitida_em, "", "nem bloqueio nenhum")
    ok("nenhuma espera inventada" in r["motivo"], "e o motivo diz exatamente isso")
    ok(ctl.avaliar(raiz, E_SINCRONIA, politica=POL, agora=AGORA).pode,
       "a varredura pode seguir da página onde parou")

secao("656: o bloqueio conhecido é preservado, não recalculado")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "656", "000000000009999",
                     "000000000000000", "CONSUMO_INDEVIDO")
    ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.ultimo_cstat, "656", "o cStat 656 fica registrado")
    igual(e.segundos_para_liberar(AGORA), 3600, "com a hora de bloqueio")
    ok("656" in e.motivo_bloqueio, "e o motivo é explícito para quem lê depois")

secao("Falha técnica: backoff da NOSSA política, não a hora oficial")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "", "000000000000100", "",
                     "FALHA_TRANSPORTE", transporte_ok=False, erro="timeout")
    ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.segundos_para_liberar(AGORA), POL.backoff_para(1) * 60,
          "backoff curto, que é escolha nossa — e não a hora da SEFAZ")

secao("CONSULTA_RECONSTRUIDA não é chamada de rede e não vira agenda")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    aud.abrir(raiz, E_SINCRONIA).registrar(
        aud.CONSULTA_RECONSTRUIDA, servico=SERV, ambiente="producao",
        cstat="656", ult_nsu="000000000009999", max_nsu="000000000000000",
        inicio=AGORA.isoformat(timespec="seconds"),
        fonte="documentação — proveniência explícita")
    r = ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    igual(r["resultado"], ctl.BOOTSTRAP_SEM_HISTORICO,
          "uma reconstruída não serve de base para a agenda")
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.proxima_consulta_permitida_em, "",
          "e não inventa bloqueio a partir de um fato do passado")
    igual(e.ultima_consulta_em, "", "nem carimba consulta que não houve")

secao("Havendo as duas, só a CONSULTA real conta")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    aud.abrir(raiz, E_SINCRONIA).registrar(
        aud.CONSULTA_RECONSTRUIDA, servico=SERV, ambiente="producao",
        cstat="656", inicio=AGORA.isoformat(timespec="seconds"))
    plantar_consulta(raiz, E_SINCRONIA, "137",
                     quando=AGORA - timedelta(minutes=50))
    ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.ultimo_cstat, "137", "o 656 reconstruído não sobrepõe a real")
    igual(e.segundos_para_liberar(AGORA), 600,
          "e a hora é contada da consulta real: faltam 10 min")

secao("Bootstrap é idempotente e não sobrescreve agenda existente")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "137")
    r1 = ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    arq = op.RepositorioOperacao(raiz).caminho(E_SINCRONIA, SERV, PRODUCAO)
    antes = arq.read_bytes()
    r2 = ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    igual(r1["resultado"], ctl.BOOTSTRAP_DERIVADO, "a primeira deriva")
    igual(r2["resultado"], ctl.BOOTSTRAP_JA_TEM_AGENDA,
          "a segunda respeita o que já existe")
    igual(arq.read_bytes(), antes, "e o arquivo fica byte a byte igual")

secao("O bootstrap não encurta um bloqueio maior já gravado")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    # A consulta real é antiga: a janela derivada dela venceria em 10 min,
    # antes do bloqueio de 30 min que já estava gravado.
    plantar_consulta(raiz, E_COOLDOWN, "137", quando=AGORA - timedelta(minutes=50))
    ctl.bootstrap_operacao(raiz, E_COOLDOWN, politica=POL, forcar=True)
    e = op.RepositorioOperacao(raiz).carregar(E_COOLDOWN, SERV, PRODUCAO)
    igual(e.segundos_para_liberar(AGORA), 30 * 60,
          "o bloqueio de 30 min prevalece — bloquear() adia, nunca antecipa")
    igual(e.ultimo_cstat, "137", "mas o histórico derivado é gravado do mesmo jeito")

secao("Bootstrap é por empresa: o 656 de uma não contamina a outra")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "656", "000000000009999",
                     "000000000000000", "CONSUMO_INDEVIDO")
    linhas = ctl.bootstrap_todas(raiz, [E_SINCRONIA, E_PENDENTE], politica=POL)
    igual(len(linhas), 2, "processou as duas")
    e2 = op.RepositorioOperacao(raiz).carregar(E_PENDENTE, SERV, PRODUCAO)
    igual(e2.proxima_consulta_permitida_em, "",
          "a empresa sem histórico segue sem bloqueio")

secao("Bootstrap é por (serviço, ambiente): trilha de outra chave não serve")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "137", ambiente="homologacao")
    r = ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    igual(r["resultado"], ctl.BOOTSTRAP_SEM_HISTORICO,
          "consulta de homologação não define a agenda de produção")

secao("A consulta derivada entra na janela do teto por hora")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    plantar_consulta(raiz, E_SINCRONIA, "138", "000000000000150",
                     "000000000009999", "DOCUMENTOS_ENCONTRADOS")
    ctl.bootstrap_operacao(raiz, E_SINCRONIA, politica=POL)
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.consultas_na_janela(60, AGORA), 1,
          "o teto por hora não recomeça do zero depois de uma rajada")


# ── a regra das 20 consultas/hora está documentada no serviço certo ─────────
secao("20/hora é regra das consultas PONTUAIS, não da paginação distNSU")
igual(ctl.REGRAS_OFICIAIS_CONSULTA_PONTUAL["servico"],
      "consChNFe (por chave) e consNSU (por NSU avulso)",
      "o teto de 20/h está documentado sob as consultas pontuais")
ok(any("20 consultas por hora" in x
       for x in ctl.REGRAS_OFICIAIS_CONSULTA_PONTUAL["confirmado"]),
   "e lá ele é declarado como confirmado")
ok(not any("20" in x for x in ctl.REGRAS_OFICIAIS_DIST_NSU["confirmado"]),
   "e NÃO aparece como regra confirmada do distNSU")
ok(any("teto hor" in x for x in ctl.REGRAS_OFICIAIS_DIST_NSU["nao_confirmado"]),
   "o distNSU declara explicitamente que não há teto horário confirmado")
igual(POL.teto_consultas_por_hora, 20,
      "e o valor da NOSSA política não mudou por causa da correção documental")
igual(ctl.Politica().teto_consultas_por_hora, 20,
      "nem o padrão de fábrica")


# ══════════════════════════════════════════════════════════════════════════
secao("Ciclo completo — 3 elegíveis, 4 puladas por motivos diferentes")
with apoio.raiz_temporaria("i4c_") as raiz:
    todas = cenario(raiz)
    tr = Trans({E_SINCRONIA: [resposta("137", "Nenhum documento localizado",
                                       "000000000000100", "000000000000100")],
                E_PENDENTE: [resposta(ult="000000000000150",
                                      maximo="000000000000150",
                                      docs=[doczip("000000000000150",
                                                   T.xml_nfe(T.CHAVE_1))])],
                E_OUTRA: [resposta("137", "Nenhum documento localizado",
                                   "000000000000100", "000000000000100")]})
    r = ctl.executar_ciclo(raiz, todas, politica=POL, fabrica_fonte=fabrica(tr),
                           agora=AGORA)
    igual(r.avaliadas, 7, "sete empresas avaliadas")
    igual(r.consultadas, 3, "três consultadas")
    igual(sorted(set(tr.chamadas)), sorted([E_SINCRONIA, E_PENDENTE, E_OUTRA]),
          "e exatamente as elegíveis foram à rede")
    ok(E_DIVERGENTE not in tr.chamadas, "a divergente NÃO foi consultada")
    ok(E_NOVA not in tr.chamadas, "a nova NÃO foi consultada")
    ok(E_EXPIRADO not in tr.chamadas, "a de certificado vencido NÃO foi consultada")
    ok(E_COOLDOWN not in tr.chamadas, "a em cooldown NÃO foi consultada")
    igual(r.documentos, 1, "um documento novo no ciclo")
    # Quatro: as três consultadas que fecharam em dia, mais a que já estava em
    # cooldown justamente por estar em dia.
    igual(r.por_estado(op.EM_SINCRONIA), 4, "quatro reportadas em sincronia")
    igual(sum(1 for e in r.empresas if e.consultada
              and e.estado == op.EM_SINCRONIA), 3,
          "das quais três foram de fato consultadas neste ciclo")
    igual(r.por_estado(op.DIVERGENCIA_EXTERNA), 1, "uma em divergência")
    igual(r.por_estado(op.AGUARDANDO_ONBOARDING), 1, "uma aguardando onboarding")

    txt = r.relatorio()
    ok("Ciclo NF-e" in txt, "o relatório tem cabeçalho")
    ok("Aguardando onboarding" in txt, "e as contagens por situação")
    for c in todas:
        ok(c not in txt, f"nenhum CNPJ inteiro no relatório ({c[:4]}...)")
    ok("nfeProc" not in txt and "PRODUTO" not in txt, "e nenhum XML")

secao("MONTE: em divergência, o ciclo ignora, mostra o motivo e segue")
with apoio.raiz_temporaria("i4c_") as raiz:
    todas = cenario(raiz)
    tr = Trans()
    r = ctl.executar_ciclo(raiz, [E_DIVERGENTE] + [E_SINCRONIA], politica=POL,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    div = [e for e in r.empresas if e.estado == op.DIVERGENCIA_EXTERNA][0]
    ok(not div.consultada, "não consultada")
    ok("divergência externa" in div.motivo, "com o motivo visível")
    igual(r.consultadas, 1, "e a empresa SEGUINTE foi consultada normalmente")
    ok(E_DIVERGENTE not in tr.chamadas, "nenhuma chamada para a divergente")
    cp = RepositorioCheckpoint(raiz).carregar(E_DIVERGENTE, SERV, PRODUCAO)
    igual(cp.ult_nsu, cpm.NSU_ZERO, "o checkpoint dela continua intocado")
    igual(cp.nsu_observado_sefaz, "000000000001361", "e o observado também")

secao("Empresas novas ficam fora do ciclo")
with apoio.raiz_temporaria("i4c_") as raiz:
    novas = [T.cnpj_ficticio(f"{n}0000000001") for n in range(1, 6)]
    for c in novas:
        cadastrar(raiz, c)
    tr = Trans()
    r = ctl.executar_ciclo(raiz, novas, politica=POL, fabrica_fonte=fabrica(tr),
                           agora=AGORA)
    igual(r.consultadas, 0, "nenhuma consultada")
    igual(tr.chamadas, [], "nenhuma chamada à rede")
    igual(r.por_estado(op.AGUARDANDO_ONBOARDING), 5,
          "as cinco ficam aguardando onboarding")
    ok(all(not e.consultada for e in r.empresas), "e nenhuma foi tocada")

# ══════════════════════════════════════════════════════════════════════════
secao("656 para a empresa, não o ciclo")
with apoio.raiz_temporaria("i4c_") as raiz:
    todas = cenario(raiz)
    tr = Trans({E_SINCRONIA: [RESP_656]})
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE, E_OUTRA],
                           politica=POL, fabrica_fonte=fabrica(tr), agora=AGORA)
    bloq = [e for e in r.empresas if e.identidade_mascarada.startswith("11")][0]
    igual(bloq.estado, op.BLOQUEADO_CONSUMO_INDEVIDO, "a do 656 fica bloqueada")
    igual(r.consultadas, 3, "mas as outras duas foram consultadas")

    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    ok(e.em_cooldown(AGORA), "com cooldown persistido")
    igual(e.segundos_para_liberar(AGORA), POL.cooldown_consumo_indevido_min * 60,
          "de 60 minutos, como a regra oficial")
    igual(e.ultimo_cstat, "656", "e o cStat registrado")

    cp = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(cp.ult_nsu, "000000000000100", "o checkpoint NÃO avançou")

    # e no ciclo seguinte ela é pulada
    r2 = ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                            fabrica_fonte=fabrica(tr), agora=AGORA)
    igual(r2.consultadas, 0, "no ciclo seguinte, não é consultada")
    # O 656 trouxe ultNSU à frente, então além do bloqueio ficou registrada uma
    # DIVERGENCIA_EXTERNA — e ela é o motivo mais fundamental, por isso aparece
    # antes do cooldown. É o caso da MONTE acontecendo de novo.
    igual(r2.empresas[0].estado, op.DIVERGENCIA_EXTERNA,
          "e passa a ser reportada como divergência, que é a causa de fundo")
    cp656 = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(cp656.nsu_observado_sefaz, "000000000009999",
          "com o NSU remoto observado registrado")
    igual(cp656.ult_nsu, "000000000000100", "e o checkpoint comprovado intacto")

secao("Erro técnico: backoff por empresa, sem contaminar as demais")
for rotulo, falha in (("timeout", ErroTransitorio("tempo esgotado ao LER")),
                      ("TLS", N.CertificadoRecusado("TLS recusado"))):
    with apoio.raiz_temporaria("i4c_") as raiz:
        cenario(raiz)
        tr = Trans({E_SINCRONIA: [falha]})
        r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE], politica=POL,
                               fabrica_fonte=fabrica(tr), agora=AGORA)
        primeiro = r.empresas[0]
        ok(primeiro.estado in (op.ERRO_TEMPORARIO, op.CREDENCIAL_INDISPONIVEL),
           f"{rotulo}: classificado como erro técnico")
        ok(not any(e.estado == op.DIVERGENCIA_EXTERNA for e in r.empresas),
           f"{rotulo}: NÃO é marcado como divergência externa")
        e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
        igual(e.falhas_consecutivas, 1, f"{rotulo}: falha contabilizada")
        ok(e.em_cooldown(AGORA), f"{rotulo}: backoff persistido")
        igual(r.consultadas, 2, f"{rotulo}: a empresa seguinte seguiu normalmente")

secao("Backoff cresce com falhas consecutivas")
igual([POL.backoff_para(n) for n in (1, 2, 3, 4)], [5, 15, 60, 60],
      "5 -> 15 -> 60, com teto")

secao("Falha de persistência: não avança checkpoint, para aquela empresa")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)

    class AcervoQuebrado(acv.AcervoArquivos):
        def preservar(self, doc):
            raise acv.ErroAcervo("disco cheio (simulado)")

    original = acv.AcervoArquivos
    tr = Trans({E_SINCRONIA: [resposta(ult="000000000000150",
                                       maximo="000000000000150",
                                       docs=[doczip("000000000000150",
                                                    T.xml_nfe(T.CHAVE_1))])]})
    import ingestao.pipeline as _pipe
    _pipe.acv.AcervoArquivos = AcervoQuebrado
    try:
        r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE], politica=POL,
                               fabrica_fonte=fabrica(tr), agora=AGORA)
    finally:
        _pipe.acv.AcervoArquivos = original
    cp = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(cp.ult_nsu, "000000000000100", "o checkpoint NÃO avançou")
    igual(r.empresas[0].estado, op.ERRO_TEMPORARIO, "a empresa fica em erro")
    igual(r.consultadas, 2, "e a seguinte continua")

secao("Falha de persistência: a sonda decide se é da empresa ou do sistema")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)

    class AcervoQuebrado(acv.AcervoArquivos):
        def preservar(self, doc):
            raise acv.ErroAcervo("colisao simulada nesta empresa")

    tr = Trans({E_SINCRONIA: [resposta(ult="000000000000150",
                                       maximo="000000000000150",
                                       docs=[doczip("000000000000150",
                                                    T.xml_nfe(T.CHAVE_1))])]})
    import ingestao.pipeline as _pipe
    original = _pipe.acv.AcervoArquivos
    _pipe.acv.AcervoArquivos = AcervoQuebrado
    try:
        r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE, E_OUTRA],
                               politica=POL, fabrica_fonte=fabrica(tr),
                               agora=AGORA)
    finally:
        _pipe.acv.AcervoArquivos = original
    cp = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(cp.ult_nsu, "000000000000100", "o checkpoint NÃO avançou")
    igual(r.interrompido_por, "", "a raiz de dados está sã: o ciclo NÃO aborta")
    igual(r.consultadas, 3, "e as outras duas empresas foram consultadas")
    ok("passou na sonda" in (r.empresas[0].erro or ""),
       "o relatório diz que a falha é daquela empresa, não do FISCALE")

secao("Falha de persistência COM armazenamento doente: aborta o ciclo")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)

    class AcervoQuebrado(acv.AcervoArquivos):
        def preservar(self, doc):
            raise acv.ErroAcervo("disco cheio (simulado)")

    tr = Trans({E_SINCRONIA: [resposta(ult="000000000000150",
                                       maximo="000000000000150",
                                       docs=[doczip("000000000000150",
                                                    T.xml_nfe(T.CHAVE_1))])]})
    import ingestao.pipeline as _pipe
    original = _pipe.acv.AcervoArquivos
    sonda_original = ctl.armazenamento_saudavel
    _pipe.acv.AcervoArquivos = AcervoQuebrado
    ctl.armazenamento_saudavel = lambda d: (False, "disco não aceita escrita")
    try:
        r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE, E_OUTRA],
                               politica=POL, fabrica_fonte=fabrica(tr),
                               agora=AGORA)
    finally:
        _pipe.acv.AcervoArquivos = original
        ctl.armazenamento_saudavel = sonda_original
    ok(ctl.MOTIVO_SISTEMICO in (r.interrompido_por or ""),
       "o ciclo aborta, e diz que foi falha sistêmica")
    igual(r.consultadas, 1, "nenhuma empresa seguinte foi consultada")
    igual(len(tr.chamadas), 1, "e nenhuma chamada extra saiu para a SEFAZ")
    cp = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(cp.ult_nsu, "000000000000100",
          "com armazenamento doente, o checkpoint continua onde estava")
    cp2 = RepositorioCheckpoint(raiz).carregar(E_PENDENTE, SERV, PRODUCAO)
    igual(cp2.ult_nsu, "000000000000100", "e o da empresa não visitada também")

secao("A sonda de armazenamento é honesta e não deixa lixo")
with apoio.raiz_temporaria("i4c_") as raiz:
    antes = sorted(p.name for p in Path(raiz).iterdir())
    saudavel, detalhe = ctl.armazenamento_saudavel(raiz)
    ok(saudavel, "raiz normal passa na sonda")
    igual(detalhe, "", "sem detalhe quando está tudo bem")
    igual(sorted(p.name for p in Path(raiz).iterdir()), antes,
          "e a sonda não deixa arquivo nenhum para trás")
    ruim, motivo = ctl.armazenamento_saudavel(Path(raiz) / "nao" / "existe" / "\x00")
    ok(not ruim, "caminho impossível reprova — e RESPONDE, em vez de levantar")
    ok(bool(motivo), "com motivo escrito")
    ok("\x00" not in motivo, "e o motivo é imprimível no console")

secao("Sucesso normal não chama a sonda — ela é só para falha de persistência")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    sondagens = []
    sonda_original = ctl.armazenamento_saudavel
    ctl.armazenamento_saudavel = lambda d: (sondagens.append(1), (True, ""))[1]
    try:
        ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                           fabrica_fonte=fabrica(Trans()), agora=AGORA)
    finally:
        ctl.armazenamento_saudavel = sonda_original
    igual(sondagens, [], "ciclo saudável não sonda disco à toa")


# ══════════════════════════════════════════════════════════════════════════
secao("Orçamento: máximo de empresas por ciclo")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    p = ctl.Politica(max_empresas_por_ciclo=2, usar_trava=False)
    tr = Trans()
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE, E_OUTRA],
                           politica=p, fabrica_fonte=fabrica(tr), agora=AGORA)
    igual(r.consultadas, 2, "só duas consultadas")
    igual(r.interrompido_por, ctl.MOTIVO_ORCAMENTO, "e o ciclo diz por que parou")
    igual(len(set(tr.chamadas)), 2, "a terceira nem foi à rede")

secao("Orçamento: máximo de lotes por empresa")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    p = ctl.Politica(max_lotes_por_empresa=2, usar_trava=False)
    lotes = [resposta(ult=f"{100+50*i:015d}", maximo="000000000009999",
                      docs=[doczip(f"{100+50*i:015d}",
                                   T.xml_nfe(T.chave_ficticia(T.FORNECEDOR, i + 1)))])
             for i in range(1, 6)]
    tr = Trans({E_SINCRONIA: lotes})
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=p,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    igual(len(tr.chamadas), 2, "só dois lotes foram pedidos")
    igual(r.empresas[0].estado, op.PENDENTE,
          "a empresa fica PENDENTE — há mais a buscar")
    e = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(e.segundos_para_liberar(AGORA), p.cooldown_pendente_min * 60,
          "com cooldown curto, porque ainda há pendência")

secao("Orçamento: duração máxima do ciclo")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    p = ctl.Politica(duracao_maxima_s=0, usar_trava=False)
    tr = Trans()
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE], politica=p,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    igual(r.consultadas, 0, "nenhuma consultada")
    igual(r.interrompido_por, ctl.MOTIVO_TEMPO, "o ciclo para por tempo")
    igual(tr.chamadas, [], "e nada foi à rede")

# ══════════════════════════════════════════════════════════════════════════
secao("Persistência antes de seguir, e reinício seguro")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resposta("137", "Nenhum documento localizado",
                                       "000000000000100", "000000000000100")],
                E_PENDENTE: [ErroTransitorio("queda no meio do ciclo")]})
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE], politica=POL,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    # o estado da PRIMEIRA já está no disco, mesmo com a segunda falhando
    e1 = op.RepositorioOperacao(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    ok(e1.ultima_consulta_em, "o estado da primeira foi persistido")
    ok(e1.em_cooldown(AGORA), "com o cooldown dela")
    e2 = op.RepositorioOperacao(raiz).carregar(E_PENDENTE, SERV, PRODUCAO)
    igual(e2.falhas_consecutivas, 1, "e o da segunda também, com a falha")

    # "reinício": objetos novos lendo o mesmo disco
    el1 = ctl.avaliar(raiz, E_SINCRONIA, politica=POL, agora=AGORA)
    ok(not el1.pode, "ao reiniciar, a primeira continua em cooldown")
    ok(el1.segundos_para_liberar > 0, "com o tempo restante correto")

secao("Reinício não retrocede checkpoint nem repete destrutivamente")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    doc = doczip("000000000000150", T.xml_nfe(T.CHAVE_1))
    tr = Trans({E_SINCRONIA: [resposta(ult="000000000000150",
                                       maximo="000000000000150", docs=[doc])]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    cp1 = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    n1 = len(acv.abrir(raiz, E_SINCRONIA).listar())

    # o mesmo lote de novo, depois do cooldown: duplicata idempotente
    tr2 = Trans({E_SINCRONIA: [resposta(ult="000000000000150",
                                        maximo="000000000000150", docs=[doc])]})
    depois = AGORA + timedelta(hours=2)
    ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                       fabrica_fonte=fabrica(tr2), agora=depois)
    cp2 = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(cp2.ult_nsu, cp1.ult_nsu, "o checkpoint não retrocedeu")
    igual(len(acv.abrir(raiz, E_SINCRONIA).listar()), n1,
          "e o documento repetido não duplicou o acervo")
    igual(acv.abrir(raiz, E_SINCRONIA).verificar(), [], "integridade preservada")

secao("Trava abandonada não bloqueia para sempre")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    from ingestao import trava as tv
    p = ctl.Politica(usar_trava=True)
    # trava de um processo que não existe mais
    alvo = tv.caminho(raiz, E_SINCRONIA, SERV, PRODUCAO) if hasattr(tv, "caminho") \
        else Path(raiz) / E_SINCRONIA / "ingestao" / f"{SERV}.producao.lock"
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text('{"pid": 999999, "em": "2020-01-01T00:00:00+00:00"}',
                    encoding="utf-8")
    el = ctl.avaliar(raiz, E_SINCRONIA, politica=p, agora=AGORA)
    ok(el.pode or el.estado == op.EM_EXECUCAO,
       "a trava é avaliada, e trava velha não impede para sempre")

secao("Ordem do ciclo: elegíveis primeiro, e quem espera há mais tempo antes")
with apoio.raiz_temporaria("i4c_") as raiz:
    todas = cenario(raiz)
    op_de(raiz, E_OUTRA, ultima_consulta_em="2026-08-01T00:00:00+00:00")
    op_de(raiz, E_PENDENTE, ultima_consulta_em="2026-08-19T00:00:00+00:00")
    fila = ctl.ordem_do_ciclo(raiz, todas, politica=POL, agora=AGORA)
    elegiveis = [e for e in fila if e.pode]
    igual(len(elegiveis), 3, "três elegíveis")
    ok(fila[0].pode and fila[-1].pode is False,
       "elegíveis vêm antes das não elegíveis")
    # Nunca consultada por nós vem primeiro (espera mais longa possível);
    # depois a mais antiga; por último a mais recente.
    igual([e.identidade_mascarada[:2] for e in elegiveis], ["11", "33", "22"],
          "nunca consultada primeiro, depois da mais antiga para a mais recente")

# ══════════════════════════════════════════════════════════════════════════
secao("O ciclo percorre na ORDEM decidida, nao na ordem recebida")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    op_de(raiz, E_SINCRONIA, ultima_consulta_em="2026-08-19T00:00:00+00:00")
    op_de(raiz, E_PENDENTE, ultima_consulta_em="2026-08-18T00:00:00+00:00")
    op_de(raiz, E_OUTRA, ultima_consulta_em="2026-08-01T00:00:00+00:00")
    # teto de 1: so a PRIMEIRA da ordem deve ser consultada
    p1 = ctl.Politica(max_empresas_por_ciclo=1, usar_trava=False)
    tr = Trans()
    # lista recebida na ordem INVERSA da prioridade
    ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE, E_OUTRA], politica=p1,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    igual(len(set(tr.chamadas)), 1, "so uma empresa foi consultada")
    igual(tr.chamadas[0], E_OUTRA,
          "e foi a que estava ha mais tempo sem consulta, nao a primeira da lista")

secao("O relatorio mostra o motivo de quem nao foi consultada")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans()
    r = ctl.executar_ciclo(raiz, [E_DIVERGENTE, E_EXPIRADO], politica=POL,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    txt = r.relatorio()
    ok(ctl.MOTIVO_DIVERGENCIA in txt, "o motivo da divergente aparece")
    ok(ctl.MOTIVO_CERT_EXPIRADO in txt, "e o da de certificado vencido")

secao("O relatorio de um ciclo com documentos e imprimivel no console")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resposta(ult="000000000000150",
                                       maximo="000000000000150",
                                       docs=[doczip("000000000000150",
                                                    T.xml_nfe(T.CHAVE_1))])]})
    r = ctl.executar_ciclo(raiz, [E_SINCRONIA], politica=POL,
                           fabrica_fonte=fabrica(tr), agora=AGORA)
    txt = r.relatorio()
    ok("checkpoint" in txt, "a linha da empresa consultada mostra o checkpoint")
    try:
        txt.encode("cp1252")
        ok(True, "e o relatorio inteiro imprime no console do Windows")
    except UnicodeEncodeError as exc:
        ok(False, f"o relatorio quebra no console: {exc}")

secao("Dry-run não vai à rede")
with apoio.raiz_temporaria("i4c_") as raiz:
    todas = cenario(raiz)
    tr = Trans()
    r = ctl.executar_ciclo(raiz, todas, politica=POL, fabrica_fonte=fabrica(tr),
                           dry_run=True, agora=AGORA)
    igual(tr.chamadas, [], "nenhuma chamada")
    igual(r.consultadas, 0, "nada consultado de fato")
    seriam = [e for e in r.empresas if "SERIA CONSULTADA" in (e.motivo or "")]
    igual(len(seriam), 3, "mas três apareceriam como consultáveis")
    cp = RepositorioCheckpoint(raiz).carregar(E_SINCRONIA, SERV, PRODUCAO)
    igual(cp.ult_nsu, "000000000000100", "e nenhum checkpoint mudou")

secao("Auditoria: cada consulta do ciclo deixa a sua linha")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resposta("137", "Nenhum documento localizado",
                                       "000000000000100", "000000000000100")]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA, E_DIVERGENTE], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    igual(len(aud.abrir(raiz, E_SINCRONIA).consultas()), 1,
          "a consultada tem uma linha CONSULTA")
    div_tr = aud.abrir(raiz, E_DIVERGENTE)
    igual(len(div_tr.consultas()) if (Path(raiz) / E_DIVERGENTE / "auditoria").exists()
          else 0, 0, "a ignorada não tem nenhuma")

secao("Isolamento: o ciclo nunca mistura empresas")
with apoio.raiz_temporaria("i4c_") as raiz:
    cenario(raiz)
    tr = Trans({E_SINCRONIA: [resposta(ult="000000000000150",
                                       maximo="000000000000150",
                                       docs=[doczip("000000000000150",
                                                    T.xml_nfe(T.CHAVE_1))])],
                E_PENDENTE: [resposta(ult="000000000000150",
                                      maximo="000000000000150",
                                      docs=[doczip("000000000000150",
                                                   T.xml_nfe(T.CHAVE_2))])]})
    ctl.executar_ciclo(raiz, [E_SINCRONIA, E_PENDENTE], politica=POL,
                       fabrica_fonte=fabrica(tr), agora=AGORA)
    for empresa, chave_outra in ((E_SINCRONIA, T.CHAVE_2), (E_PENDENTE, T.CHAVE_1)):
        vazou = [p for p in (Path(raiz) / empresa).rglob("*.xml")
                 if chave_outra.encode() in p.read_bytes()]
        igual(vazou, [], f"nenhum XML da outra empresa em {empresa[:4]}...")
    igual(len(acv.abrir(raiz, E_SINCRONIA).listar()), 1, "cada uma com o seu")
    igual(len(acv.abrir(raiz, E_PENDENTE).listar()), 1, "documento")

secao("Política declara o que é oficial e o que é escolha")
doc = ctl.Politica.__doc__
ok("REGRAS_OFICIAIS_DIST_NSU" in doc
   and "REGRAS_OFICIAIS_CONSULTA_PONTUAL" in doc,
   "a política aponta para onde mora o que é oficial, por tipo de consulta")
for _r in (ctl.REGRAS_OFICIAIS_DIST_NSU, ctl.REGRAS_OFICIAIS_CONSULTA_PONTUAL):
    ok("NT 2014.002" in _r["fonte"], f"a fonte oficial de {_r['servico'][:12]} é citada")
    ok(bool(_r["nao_confirmado"]),
       "o que NÃO foi confirmado está declarado, e não some por conveniência")
ok("escolha do fiscale" in doc.lower(),
   "e a escolha nossa é declarada como escolha")
fonte_ctl = (RAIZ / "nfse" / "backend" / "ingestao" / "controlador.py").read_text("utf-8")
ok("sleep(" not in fonte_ctl, "o controlador NÃO usa sleep como proteção")
ok("time.sleep" not in fonte_ctl, "nem indiretamente")

secao("O controlador não agenda nada sozinho")
import re as _re
# Palavra inteira: "cron" e substring de "sincronismo"/"sincronia", e casar por
# substring transformaria o vocabulario do dominio em falso positivo.
for proibido in ("schedule", "cron", "crontab", "Timer", "while True",
                 "threading", "APScheduler"):
    ok(not _re.search(rf"{_re.escape(proibido)}", fonte_ctl),
       f"sem '{proibido}' como agendador - isso e fase propria")

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
print("ING 4C: todos os testes passaram.")
