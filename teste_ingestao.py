#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da ING 1 — identidade da empresa e infraestrutura mTLS.

    python teste_ingestao.py

REGRAS DESTE ARQUIVO
    1. NENHUM teste vai à rede. Não há chamada a SEFAZ, ADN ou Portal Nacional.
       O que precisa de rede é dublê; o que precisa de certificado é gerado na
       hora, autoassinado.
    2. NENHUM teste toca `~/Fiscale/dados`. Tudo roda dentro de
       `teste_apoio.raiz_temporaria()`, que ainda liga a trava que transforma
       qualquer tentativa de usar a pasta real em exceção.
    3. Segredo não é impresso. Quando falha, o teste diz onde, nunca qual.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "nfse" / "backend"))

import teste_apoio as ta                       # noqa: E402
import fiscale_dados as fd                     # noqa: E402
import seguranca                               # noqa: E402
from ingestao import (                          # noqa: E402
    ambiente as amb, cadastro as cad, contratos, identidade as ident,
    modelo as mod, sessao as ses,
)

_ok = _falhas = 0
_erros: list[str] = []

SENHA_CERT = "SENHA-DE-TESTE-nao-real-42"


def _com_dv_cnpj(base12: str) -> str:
    """Fecha os dígitos verificadores de uma raiz de 12 dígitos.

    Os identificadores do teste são SINTÉTICOS, calculados aqui: nenhum CNPJ de
    empresa real do usuário entra na suíte. Também evita o erro de digitar um
    número à mão e ele não fechar."""
    c = base12
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(c[:tam], pesos)) % 11
        c += str(0 if resto < 2 else 11 - resto)
    return c


def _com_dv_cpf(base9: str) -> str:
    c = base9
    for tam in (9, 10):
        resto = sum(int(d) * (tam + 1 - i) for i, d in enumerate(c[:tam])) * 10 % 11
        c += str(0 if resto == 10 else resto)
    return c


# Começa com zero de propósito: é o caso que planilha e JSON estragam.
CNPJ_A = _com_dv_cnpj("049990010001")
CNPJ_B = _com_dv_cnpj("278657570001")
CPF_C = _com_dv_cpf("111444777")


def _mascarar(c: str) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


CNPJ_A_MASC = _mascarar(CNPJ_A)      # a MESMA empresa, escrita com pontuação


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
        print(f"  FALHA {desc}")


def igual(a, b, desc):
    ok(a == b, f"{desc}" if a == b else f"{desc}  (obtido={a!r} esperado={b!r})")


def levanta(fn, excecao, desc):
    try:
        fn()
    except excecao:
        ok(True, desc)
    except Exception as exc:
        ok(False, f"{desc} (levantou {type(exc).__name__} em vez de {excecao.__name__})")
    else:
        ok(False, f"{desc} (não levantou nada)")


# ══ 1. Normalização de CNPJ ═════════════════════════════════════════════════
secao("1. Normalização de identificador — fonte única")
igual(ident.so_digitos(CNPJ_A_MASC), CNPJ_A, "máscara é removida")
i = ident.normalizar(CNPJ_A_MASC)
ok(i.valido and i.tipo == ident.TIPO_CNPJ, "CNPJ pontuado é reconhecido")
igual(i.valor, CNPJ_A, "e vira a forma canônica de 14 dígitos")
igual(ident.normalizar(CNPJ_A).valor, ident.normalizar(CNPJ_A_MASC).valor,
      "com e sem máscara dão a MESMA chave")
ok(ident.mesma_empresa(CNPJ_A_MASC, CNPJ_A), "mesma_empresa reconhece as duas formas")
ok(not ident.mesma_empresa(CNPJ_A, CNPJ_B), "e distingue empresas diferentes")

# zeros à esquerda: restaurados, mas nunca em silêncio
sem_zero = CNPJ_A.lstrip("0")
iz = ident.normalizar(sem_zero)
ok(iz.valido and iz.valor == CNPJ_A, "zeros à esquerda perdidos são restaurados")
ok(bool(iz.ajustes), "e o ajuste fica registrado (não é silencioso)")

# CPF existe no cadastro real — 11 dígitos não podem ser tratados como CNPJ
ic = ident.normalizar(CPF_C)
ok(ic.valido and ic.tipo == ident.TIPO_CPF, "identificador de 11 dígitos é CPF")

# máscara para log nunca mostra o número inteiro
m = ident.normalizar(CNPJ_A).mascarado()
ok(CNPJ_A not in m and "***" in m, "mascarado() não expõe o identificador completo")


# ══ 2. Rejeição de identificador inválido ═══════════════════════════════════
secao("2. Identificador inválido é rejeitado, não 'corrigido'")
for ruim, motivo in [("", "vazio"), ("00000000000000", "todos iguais"),
                     ("04999001000143", "dígito verificador errado"),
                     ("123", "curto demais"), ("abcdefgh", "sem dígitos")]:
    r = ident.normalizar(ruim)
    ok(not r.valido, f"rejeita {motivo}")
ok(ident.normalizar("04999001000143").motivo != "", "e diz o motivo da rejeição")
ok(not ident.cnpj_valido(CPF_C), "CPF não passa como CNPJ")
ok(not ident.cpf_valido(CNPJ_A), "CNPJ não passa como CPF")
ok(ident.normalizar("").valido is False and bool(ident.normalizar("")) is False,
   "Identificador inválido é falsy (não vira chave por engano)")


# ── monta uma instalação de teste ──────────────────────────────────────────
def montar(raiz: Path, *, com_cliente_extra=True, procurador=False,
           senha_ok=True, duplicar=False, cnpj_torto=False):
    """Escreve os dois cadastros numa raiz temporária."""
    certs_dir = raiz / "certs"
    pfx = ta.gerar_pfx(certs_dir / "empresa_a.pfx", SENHA_CERT, cn="EMPRESA A LTDA")

    reg_a = {
        "id": CNPJ_A, "cnpj": CNPJ_A, "nome": "EMPRESA A LTDA", "apelido": "",
        "caminho": "certs/empresa_a.pfx",
        "senha_protegida": seguranca.proteger(SENHA_CERT, raiz) if senha_ok else "",
        "procurador": procurador, "caminho_origem": "",
    }
    certificados = [reg_a]
    if duplicar:
        certificados.append(dict(reg_a, nome="EMPRESA A LTDA (2)"))
    if cnpj_torto:
        certificados.append({"id": "999", "cnpj": "999", "nome": "REGISTRO TORTO",
                             "caminho": "", "senha_protegida": "", "procurador": False})

    clientes = [{"id": 1, "cnpj": CNPJ_A, "nome": "Empresa A Comercio Ltda",
                 "regime": "Simples Nacional", "uf": "PE", "mun": "Recife",
                 "ie": "123", "im": "456", "email": "a@ex.com", "tel": "81",
                 "cert": {"arquivo": "empresa_a.pfx"}, "certValidade": "2027-01-01"}]
    if com_cliente_extra:
        clientes.append({"id": 2, "cnpj": CNPJ_B, "nome": "Empresa B Servicos Ltda",
                         "regime": "Lucro Presumido", "uf": "PE", "mun": "Olinda",
                         "cert": "", "certValidade": ""})

    (raiz / "certificados.json").write_text(json.dumps(certificados), "utf-8")
    (raiz / "state_clientes.json").write_text(
        json.dumps({"clientes": clientes, "seq": 3}), "utf-8")
    return pfx


# ══ 3–7. Cadastro canônico e reconciliação ══════════════════════════════════
secao("3. Empresa presente nas DUAS fontes")
with ta.raiz_temporaria("ing1_conf_") as raiz:
    montar(raiz)
    c = cad.carregar(raiz)
    emp = c.obter_empresa(CNPJ_A_MASC)
    ok(emp is not None, "acha a empresa mesmo consultando com máscara")
    igual(emp.id, CNPJ_A, "a chave é a forma canônica")
    igual(emp.origens, {mod.ORIGEM_CERTIFICADOS, mod.ORIGEM_CLIENTES},
          "registra que veio das duas fontes")
    ok(emp.regime == "Simples Nacional", "puxa o regime do cadastro de clientes")
    ok(emp.tem_credencial, "e tem credencial")
    linha = next(l for l in c.reconciliacao if l.identificador.valor == CNPJ_A)
    igual(linha.status, mod.CONFIRMADO, "status da reconciliação = CONFIRMADO")

secao("4. Empresa só em state_clientes.json")
with ta.raiz_temporaria("ing1_socli_") as raiz:
    montar(raiz)
    c = cad.carregar(raiz)
    emp = c.obter_empresa(CNPJ_B)
    ok(emp is not None, "a empresa sem certificado continua existindo")
    ok(not emp.tem_credencial, "e aparece sem credencial")
    linha = next(l for l in c.reconciliacao if l.identificador.valor == CNPJ_B)
    igual(linha.status, mod.SOMENTE_CLIENTES, "status = SOMENTE_CLIENTES")
    igual(c.obter_certificado_da_empresa(CNPJ_B), None, "não inventa certificado")

secao("5. Empresa só em certificados.json")
with ta.raiz_temporaria("ing1_socert_") as raiz:
    montar(raiz, com_cliente_extra=True)
    # tira a empresa A do cadastro de clientes, deixando-a só nos certificados
    d = json.loads((raiz / "state_clientes.json").read_text("utf-8"))
    d["clientes"] = [x for x in d["clientes"] if x["cnpj"] != CNPJ_A]
    (raiz / "state_clientes.json").write_text(json.dumps(d), "utf-8")
    c = cad.carregar(raiz)
    linha = next(l for l in c.reconciliacao if l.identificador.valor == CNPJ_A)
    igual(linha.status, mod.SOMENTE_CERTIFICADO, "status = SOMENTE_CERTIFICADO")
    ok(c.obter_empresa(CNPJ_A) is not None, "e a empresa continua consultável")

secao("6. Conflito: dois registros para o mesmo identificador")
with ta.raiz_temporaria("ing1_ambig_") as raiz:
    montar(raiz, duplicar=True)
    c = cad.carregar(raiz)
    linha = next(l for l in c.reconciliacao if l.identificador.valor == CNPJ_A)
    igual(linha.status, mod.AMBIGUO, "status = AMBIGUO")
    ok(any("2 registros" in p for p in c.obter_empresa(CNPJ_A).pendencias),
       "a duplicidade vira pendência declarada")
    igual(len(c.obter_credenciais_da_empresa(CNPJ_A)), 2,
          "nenhum registro é descartado (reconciliação não destrutiva)")

secao("7. Registro com identificador inválido não é apagado")
with ta.raiz_temporaria("ing1_torto_") as raiz:
    montar(raiz, cnpj_torto=True)
    c = cad.carregar(raiz)
    ok(len(c.invalidos) == 1, "o registro torto é relatado")
    igual(c.invalidos[0].status, mod.INVALIDO, "com status INVALIDO")
    ok(all(e.identificador.valido for e in c.listar_empresas()),
       "e não vira empresa com chave inválida")
    ok(any(l.status == mod.INVALIDO for l in c.reconciliacao),
       "aparece na tabela de reconciliação")


# ══ 8. Empresa -> Credencial, com motivo ═════════════════════════════════════
secao("8. Vínculo Empresa -> Credencial carrega o MOTIVO")
with ta.raiz_temporaria("ing1_tit_") as raiz:
    montar(raiz, procurador=False)
    c = cad.carregar(raiz)
    v = c.obter_empresa(CNPJ_A).vinculos[0]
    igual(v.motivo, mod.MOTIVO_TITULAR, "certificado próprio => TITULAR")
with ta.raiz_temporaria("ing1_proc_") as raiz:
    montar(raiz, procurador=True)
    c = cad.carregar(raiz)
    v = c.obter_empresa(CNPJ_A).vinculos[0]
    igual(v.motivo, mod.MOTIVO_PROCURACAO, "procurador=True => PROCURACAO")
    ok(v.observacao != "", "e diz por que a credencial pode ser usada")
ok(mod.Empresa(identificador=ident.normalizar(CNPJ_A)).credencial_preferida() is None,
   "empresa sem vínculo não tem credencial preferida")


# ══ 9–12. Sessão mTLS ═══════════════════════════════════════════════════════
secao("9. Senha protegida: guardada no cofre, nunca em claro")
with ta.raiz_temporaria("ing1_senha_") as raiz:
    montar(raiz)
    bruto = (raiz / "certificados.json").read_text("utf-8")
    ok(SENHA_CERT not in bruto, "a senha do certificado não está em claro no JSON")
    c = cad.carregar(raiz)
    cred = c.obter_certificado_da_empresa(CNPJ_A)
    ok(cred.tem_senha, "a credencial sabe que tem senha")
    ok(SENHA_CERT not in repr(cred), "repr() da credencial não vaza a senha")
    ok(SENHA_CERT not in json.dumps(cred.resumo()), "resumo() não vaza a senha")
    ok(SENHA_CERT not in json.dumps(c.obter_empresa(CNPJ_A).resumo()),
       "resumo() da empresa não vaza a senha")
    igual(cred.abrir_senha(raiz), SENHA_CERT, "e o cofre devolve a senha correta")

secao("10. Falha de abertura do certificado vira erro NOMEADO")
with ta.raiz_temporaria("ing1_falha_") as raiz:
    montar(raiz, senha_ok=False)
    c = cad.carregar(raiz)
    cred = c.obter_certificado_da_empresa(CNPJ_A)
    ok(not cred.tem_senha, "sem senha cadastrada => aguardando senha")
    levanta(lambda: ses.criar_sessao(cred, raiz), ses.SenhaAusente,
            "criar_sessao levanta SenhaAusente")

with ta.raiz_temporaria("ing1_semarq_") as raiz:
    montar(raiz)
    c = cad.carregar(raiz)
    cred = c.obter_certificado_da_empresa(CNPJ_A)
    Path(cred.caminho).unlink()
    levanta(lambda: ses.criar_sessao(cred, raiz), ses.CertificadoNaoEncontrado,
            "arquivo .pfx ausente levanta CertificadoNaoEncontrado")

with ta.raiz_temporaria("ing1_senhaerr_") as raiz:
    montar(raiz)
    # troca o blob por outra senha: o arquivo continua lá, a senha é que não abre
    regs = json.loads((raiz / "certificados.json").read_text("utf-8"))
    regs[0]["senha_protegida"] = seguranca.proteger("senha-errada", raiz)
    (raiz / "certificados.json").write_text(json.dumps(regs), "utf-8")
    c = cad.carregar(raiz)
    cred = c.obter_certificado_da_empresa(CNPJ_A)
    levanta(lambda: ses.criar_sessao(cred, raiz), ses.CertificadoInvalido,
            "senha errada levanta CertificadoInvalido (antes de qualquer rede)")
    try:
        ses.criar_sessao(cred, raiz)
    except ses.ErroCredencial as exc:
        ok("senha-errada" not in str(exc), "e a mensagem de erro não contém a senha")

with ta.raiz_temporaria("ing1_venc_") as raiz:
    montar(raiz)
    c = cad.carregar(raiz)
    cred = c.obter_certificado_da_empresa(CNPJ_A)
    ta.gerar_pfx(Path(cred.caminho), SENHA_CERT, cn="VENCIDA", dias_validade=-400)
    levanta(lambda: ses.criar_sessao(cred, raiz), ses.CertificadoInvalido,
            "certificado vencido é recusado sem ir à rede")

secao("11. Sessão mTLS criada, e criada num lugar só")
with ta.raiz_temporaria("ing1_sessao_") as raiz:
    montar(raiz)
    c = cad.carregar(raiz)
    cred = c.obter_certificado_da_empresa(CNPJ_A)
    s = ses.criar_sessao(cred, raiz, amb.PRODUCAO)
    ok(s.get_adapter("https://exemplo.gov.br") is not None, "a sessão tem adapter https")
    igual(s.fiscale_ambiente, "producao", "a sessão carrega o ambiente para rastreio")
    ok(SENHA_CERT not in repr(s.headers), "os cabeçalhos não carregam segredo")
    s.close()

    info = ses.inspecionar(cred, raiz)
    igual(info.titular, "EMPRESA A LTDA", "inspecionar() lê o titular do certificado")
    ok(not info.expirado, "e sabe que o certificado está válido")

    with ses.abrir_sessao(cred, raiz) as s2:
        ok(s2 is not None, "o context manager entrega a sessão")

    s3 = ses.criar_sessao_para_empresa(c, CNPJ_A_MASC)
    ok(s3 is not None, "criar_sessao_para_empresa resolve empresa -> credencial -> sessão")
    s3.close()
    levanta(lambda: ses.criar_sessao_para_empresa(c, CNPJ_B),
            ses.CertificadoNaoEncontrado, "empresa sem credencial é recusada com clareza")

secao("12. Arquivo temporário do PEM não sobra")
with ta.raiz_temporaria("ing1_tmp_") as raiz:
    montar(raiz)
    c = cad.carregar(raiz)
    cred = c.obter_certificado_da_empresa(CNPJ_A)
    tmp = Path(tempfile.gettempdir())
    antes = set(tmp.glob("tmp*"))
    for _ in range(3):
        ses.criar_sessao(cred, raiz).close()
    sobrou = set(tmp.glob("tmp*")) - antes
    ok(not sobrou, f"nenhum temporário ficou para trás ({len(sobrou)} encontrado(s))")
    # e o temporário nunca nasce dentro da pasta de dados => nunca entra no .fbk
    ok(not list(Path(raiz).rglob("*.pem")), "nenhum .pem foi criado dentro dos dados")
    ok(Path(tempfile.gettempdir()).resolve() != Path(raiz).resolve(),
       "o diretório temporário fica fora da raiz de dados")


# ══ 13. Ambientes ═══════════════════════════════════════════════════════════
secao("13. Produção e homologação são coisas distintas")
igual(amb.PRODUCAO.tp_amb, "1", "produção é tpAmb=1 na NF-e/CT-e")
igual(amb.HOMOLOGACAO.tp_amb, "2", "homologação é tpAmb=2")
igual(amb.HOMOLOGACAO.rotulo_adn, "restrita", "e 'restrita' na NFS-e Nacional")
ok(amb.PRODUCAO != amb.HOMOLOGACAO, "os dois ambientes não se confundem")
igual(amb.resolver("restrita"), amb.HOMOLOGACAO, "'restrita' resolve para homologação")
igual(amb.resolver("2"), amb.HOMOLOGACAO, "'2' também")
igual(amb.resolver("producao"), amb.PRODUCAO, "'producao' resolve para produção")
levanta(lambda: amb.resolver(""), ValueError,
        "ambiente vazio NÃO assume produção — levanta")
levanta(lambda: amb.resolver("qualquer"), ValueError, "ambiente desconhecido levanta")
ok(amb.PRODUCAO.e_producao and not amb.HOMOLOGACAO.e_producao, "e_producao distingue")
# a chave de checkpoint aprovada é (cnpj, serviço, ambiente): nomes distintos
chaves = {(CNPJ_A, "cte57", a.nome) for a in amb.TODOS}
igual(len(chaves), 2, "a mesma empresa+serviço gera chaves diferentes por ambiente")


# ══ 14. Contratos oficiais como invariantes ═════════════════════════════════
secao("14. Invariantes do CT-e (documentação oficial)")
cte = contratos.obter(contratos.SERVICO_CTE57)
ok("distNSU" in cte.consultas, "CT-e distribui por distNSU")
ok("consNSU" in cte.consultas, "e recupera pontualmente por consNSU")
ok(not cte.consulta_por_chave, "CT-e NÃO tem consulta por chave")
ok(not contratos.suporta_consulta("cte57", "consChCTe"), "consChCTe não é suportado")
ok(any("consChCTe" in x for x in cte.nao_disponivel), "e isso está registrado como NÃO DISPONÍVEL")
igual(cte.max_documentos_por_lote, 50, "lote de até 50 documentos")
igual(cte.retencao_meses_do_zero, 3, "do NSU zero, só os últimos 3 meses")
igual(cte.namespace, "http://www.portalfiscal.inf.br/cte", "namespace oficial do CT-e")
ok(cte.exige_mtls, "exige certificado")

nfe = contratos.obter(contratos.SERVICO_NFE55)
ok(nfe.consulta_por_chave and "consChNFe" in nfe.consultas,
   "a NF-e, ao contrário, TEM consulta por chave (consChNFe)")
ok(nfe.consultas != cte.consultas, "os contratos de NF-e e CT-e não são iguais")

nfse = contratos.obter(contratos.SERVICO_NFSE_NACIONAL)
igual(nfse.endpoint_producao, "https://adn.nfse.gov.br", "base de produção do ADN")
igual(nfse.endpoint_homologacao, "https://adn.producaorestrita.nfse.gov.br",
      "base de produção restrita do ADN")
ok(nfse.max_documentos_por_lote is None, "tamanho do LoteDFe fica como não confirmado")
ok(any("datas" in x for x in nfse.nao_disponivel),
   "consulta por intervalo de datas registrada como NÃO DISPONÍVEL")
levanta(lambda: contratos.obter("inexistente"), ValueError, "serviço desconhecido levanta")


# ══ 15. Isolamento: a pasta real é intocável ════════════════════════════════
secao("15. Isolamento absoluto da pasta de dados real")
reais = ta.raizes_reais()
ok(len(reais) >= 1, "as raízes reais são conhecidas")
ok(ta.e_raiz_real(Path.home() / "Fiscale" / "dados"), "~/Fiscale/dados é reconhecida como real")
with tempfile.TemporaryDirectory() as t:
    ok(not ta.e_raiz_real(t), "uma pasta temporária não é confundida com a real")
levanta(lambda: ta.exigir_raiz_temporaria(Path.home() / "Fiscale" / "dados"),
        RuntimeError, "exigir_raiz_temporaria barra a pasta real")

with ta.raiz_temporaria("ing1_iso_") as raiz:
    ok(os.environ.get(fd.VAR_PROIBIR_RAIZ_REAL) == "1", "a trava fica ligada dentro do teste")
    igual(fd.raiz(), raiz.resolve(), "a raiz em uso é a temporária")
    levanta(lambda: fd.raiz(Path.home() / "Fiscale" / "dados"), RuntimeError,
            "com a trava ligada, apontar para a raiz real levanta")
ok(os.environ.get(fd.VAR_PROIBIR_RAIZ_REAL) is None,
   "e a trava é desligada ao sair (não contamina o processo)")

# a instalação normal não muda: sem a variável, nada é barrado
os.environ.pop(fd.VAR_PROIBIR_RAIZ_REAL, None)
ok(fd._guarda_raiz_real(Path.home() / "Fiscale" / "dados") is not None,
   "sem a trava, o comportamento normal é preservado")

# a pasta real continua existindo e intacta ao fim da suíte
real = Path.home() / "Fiscale" / "dados"
if real.exists():
    ok((real / "certificados.json").exists(),
       "certificados.json real continua no lugar depois de toda a suíte")


print()
print("=" * 62)
print(f"{_ok} ok · {_falhas} falha(s)")
if _erros:
    print("\nFalhas:")
    for e in _erros:
        print("  -", e)
    sys.exit(1)
print("Todos os testes da ING 1 passaram.")
