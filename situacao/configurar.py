# -*- coding: utf-8 -*-
"""configurar.py — escreve o `situacao_credenciais.json` sem vazar segredo.

    python -m situacao.configurar --ver
    python -m situacao.configurar --contratante 00000000000000 \\
                                  --procurador  00000000000 \\
                                  --apelido fulano \\
                                  --certificado <id do certificado> \\
                                  --ambiente producao
    python -m situacao.configurar --segredo jwt_token
    python -m situacao.configurar --segredo consumer_key
    python -m situacao.configurar --segredo consumer_secret

POR QUE ISTO EXISTE, EM VEZ DE "edite o JSON"
    Editar o arquivo à mão funciona — e é justamente por isso que o segredo
    acaba em claro nele. Aqui os dois tipos de informação entram por portas
    diferentes, porque têm riscos diferentes:

      • IDENTIDADE (contratante, procurador, certificado, ambiente) vem por
        argumento: é identificador, aparece na tela e no histórico, e tudo bem;

      • SEGREDO (`--segredo NOME`) é pedido por `getpass`, digitado sem eco, e
        gravado **protegido** pelo cofre do projeto. Ele nunca vira argumento
        de linha de comando: argumento fica no histórico do shell, na lista de
        processos e em qualquer log de auditoria da máquina.

    `--ver` mostra o que está configurado e **nunca** o conteúdo dos segredos —
    só se cada um existe e se abre com o cofre desta máquina.

O COFRE É O DO PROJETO, E NÃO UM NOVO
    `nfse/backend/seguranca.py` já protege as senhas de certificado com DPAPI.
    Ter um segundo cofre seria ter duas regras de proteção divergindo — e a que
    divergisse para menos seria descoberta tarde.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import credencial as _credencial


def _cofre_do_projeto(dados: Path):
    """`(proteger, desproteger)` do `seguranca.py`, ou `(None, None)`.

    Importado aqui dentro, e com falha tolerada: o pacote `situacao` não pode
    depender do backend do NFS-e para ser importado nem testado.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                               / "nfse" / "backend"))
        import seguranca                                   # type: ignore
        return (lambda t: seguranca.proteger(t, dados),
                lambda b: seguranca.desproteger(b, dados))
    except Exception:
        return None, None


def _certificado_do_contratante(raiz, cred) -> bool:
    """Existe certificado utilizável para o handshake? Nunca levanta.

    Só a EXISTÊNCIA, e nem tenta abrir: pedir a senha do certificado para
    responder "está configurado?" transformaria uma conferência em uma
    operação com segredo.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                                / "nfse" / "backend"))
        from ingestao import cadastro as _cad          # type: ignore
        cad = _cad.carregar(raiz)
        if cred.certificado_id:
            for e in cad.listar_empresas(com_credencial=True):
                for c in cad.obter_credenciais_da_empresa(e.identificador):
                    if c.id == cred.certificado_id:
                        return True
            return False
        return cad.obter_certificado_da_empresa(cred.contratante) is not None
    except Exception:
        return False


def _raiz() -> Path:
    import fiscale_dados
    return Path(fiscale_dados.raiz())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Configura a credencial do Integra Contador (SITFIS)")
    ap.add_argument("--ver", action="store_true",
                    help="mostra o que está configurado (sem revelar segredo)")
    ap.add_argument("--contratante", help="CNPJ de quem tem o contrato SERPRO")
    ap.add_argument("--procurador", help="CPF/CNPJ de quem assina (procuração)")
    ap.add_argument("--apelido", default="", help="nome curto do procurador")
    ap.add_argument("--certificado", default="", help="id do certificado a usar")
    ap.add_argument("--ambiente", choices=_credencial.AMBIENTES,
                    help="trial ou producao")
    ap.add_argument("--segredo", metavar="NOME",
                    help="grava um segredo, pedido sem eco (jwt_token, "
                         "consumer_key, consumer_secret)")
    ap.add_argument("--dados", default=None, help="raiz de dados (para teste)")
    args = ap.parse_args(argv)

    raiz = Path(args.dados) if args.dados else _raiz()
    proteger, desproteger = _cofre_do_projeto(raiz)
    cofre = _credencial.abrir(raiz, desproteger=desproteger, proteger=proteger)

    # ── ver ──────────────────────────────────────────────────────────────
    if args.ver or not any((args.contratante, args.procurador, args.ambiente,
                            args.segredo)):
        print("Arquivo: %s" % cofre.caminho)
        print("  existe        : %s" % cofre.caminho.exists())
        print("  contratante   : %s" % (cofre.dados.get("contratante") or "— falta"))
        print("  ambiente      : %s" % (cofre.dados.get("ambiente") or "— falta"))
        print("  padrão        : %s" % (cofre.dados.get("padrao") or "—"))
        procs = cofre.procuradores()
        print("  procuradores  : %d" % len(procs))
        for p in procs:
            print("      %-16s %s  cert=%-14s %s"
                  % (p.apelido, p.digitos or "— sem documento",
                     p.certificado_id or "—",
                     "ativo" if p.ativo else "INATIVO"))
        print("  segredos      :")
        nomes = sorted((cofre.dados.get("segredos") or {}))
        if not nomes:
            print("      — nenhum")
        for n in nomes:
            # NUNCA o valor. Só se existe e se ABRE nesta máquina — que é a
            # pergunta útil: DPAPI é por usuário e por máquina, e um segredo
            # copiado de outro computador não abre aqui.
            abre = bool(cofre.segredo(n)) if desproteger else None
            print("      %-16s guardado, %s" % (
                n, "abre nesta máquina" if abre
                else ("NÃO abre aqui" if abre is False else "cofre indisponível")))
        try:
            c = cofre.escolher()
        except _credencial.SemCredencial as e:
            print("\n  AINDA NÃO DÁ PARA USAR: %s" % e)
            return 0

        # EM PRODUÇÃO, "PRONTO" EXIGE A CREDENCIAL DE ACESSO INTEIRA.
        #     Este relatório já disse PRONTO uma vez sobre uma configuração
        #     que não conseguia autenticar: ele olhava só contratante,
        #     procurador e ambiente. A chamada real saiu, o gateway devolveu
        #     403/900908, e o "pronto" tinha mandado procurar no lugar errado.
        #
        #     Falta que impede a chamada é falta, e tem de aparecer AQUI —
        #     antes de alguém gastar uma consulta bilhetada para descobrir.
        faltando = []
        if c.eh_producao:
            for nome in ("consumer_key", "consumer_secret"):
                if not cofre.segredo(nome):
                    faltando.append(nome)
            if not desproteger:
                faltando.append("o cofre desta máquina (nenhum segredo abre)")
            if not _certificado_do_contratante(raiz, c):
                faltando.append(
                    "certificado do contratante %s (o /authenticate exige o "
                    "e-CNPJ de quem tem o contrato no handshake)"
                    % c.contratante)

        if faltando:
            print("\n  AINDA NÃO DÁ PARA USAR em produção. Falta:")
            for f in faltando:
                print("      - %s" % f)
            print("\n  O contratante e o procurador estão certos; o que falta é"
                  " a credencial de ACESSO.")
            return 0

        print("\n  PRONTO: contratante %s, autor %s, ambiente %s"
              % (c.contratante, c.autor, c.ambiente))
        return 0

    # ── segredo ──────────────────────────────────────────────────────────
    if args.segredo:
        if proteger is None:
            print("Cofre indisponível nesta máquina — nada foi gravado.")
            return 2
        valor = getpass.getpass("Valor de %r (não aparece na tela): "
                                % args.segredo)
        if not valor.strip():
            print("Vazio. Nada foi gravado.")
            return 1
        cofre.guardar_segredo(args.segredo, valor)
        cofre.salvar()
        # O COMPRIMENTO CONFIRMA QUE A COLAGEM PEGOU, e nao revela nada.
        #     Sem ele, uma colagem que veio vazia ou pela metade so apareceria
        #     muito depois, como recusa do gateway.
        print("Segredo %r gravado protegido em %s  (%d caracteres)"
              % (args.segredo, cofre.caminho.name, len(valor)))
        if valor != valor.strip():
            print("  ATENCAO: o valor colado tinha espaco ou quebra de linha "
                  "nas pontas, e foi gravado COMO VEIO. Se o gateway recusar, "
                  "grave de novo sem a sobra.")
        return 0

    # ── identidade ───────────────────────────────────────────────────────
    if args.contratante:
        cofre.dados["contratante"] = "".join(c for c in args.contratante
                                             if c.isdigit())
    if args.ambiente:
        cofre.dados["ambiente"] = args.ambiente
    if args.procurador:
        apelido = args.apelido or "procurador"
        doc = "".join(c for c in args.procurador if c.isdigit())
        lista = cofre.dados.setdefault("procuradores", [])
        for p in lista:
            if p.get("apelido") == apelido:
                p.update({"documento": doc, "ativo": True})
                if args.certificado:
                    p["certificado_id"] = args.certificado
                break
        else:
            lista.append({"apelido": apelido, "documento": doc,
                          "certificado_id": args.certificado, "ativo": True})
        cofre.dados.setdefault("padrao", apelido)

    cofre.salvar()
    print("Gravado em %s" % cofre.caminho)
    print("Confira com:  python -m situacao.configurar --ver")
    return 0


if __name__ == "__main__":
    sys.exit(main())
