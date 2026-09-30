# -*- coding: utf-8 -*-
"""mensagens.py — o que a pessoa lê quando não veio documento.

O PROBLEMA
    O órgão responde `RECUSADA` com "Dados inválidos", ou `NAO_EMITIDA` com um
    parágrafo sobre exigibilidade. Nenhum dos dois diz a quem está na tela **o
    que fazer agora** — e é isso que separa um aviso útil de um vermelho que
    todo mundo aprende a ignorar.

A REGRA: MOLDURA NOSSA, TEXTO DELES
    A frase tem duas partes, e elas têm donos diferentes:

      • a MOLDURA vem do desfecho, que é vocabulário nosso e finito. Ela diz
        de quem é a próxima ação — do escritório, do cliente, ou de ninguém
        (esperar);
      • o TEXTO do órgão vai junto, **verbatim**. Ele é a única fonte que sabe
        o motivo real, e reescrevê-lo seria pôr palavra nossa na boca dele.

    O que NÃO se faz aqui é adivinhar código. Não há tabela de "se a mensagem
    contém X então significa Y" para mensagens que nunca observamos: essa
    tabela envelheceria errada e em silêncio, que é o pior tipo de errado.

    Quando aparecer amostra real de uma recusa específica — falta de
    procuração, pendência de ICMS —, ela entra em `CONHECIDAS` com a data da
    observação. Até lá, o texto do órgão fala por si.
"""
from __future__ import annotations

from . import modelo

# A moldura por desfecho: o que aconteceu, e de quem é a próxima ação.
MOLDURA = {
    modelo.OBTIDA: (
        "Documento obtido.",
        ""),
    modelo.NAO_EMITIDA: (
        "O órgão recusou emitir — há pendência no CNPJ.",
        "Resolver a pendência é trabalho de contabilidade; tentar de novo "
        "agora devolve a mesma resposta."),
    modelo.RECUSADA: (
        "O pedido foi recusado.",
        "Confira procuração, certificado e os dados do contribuinte antes de "
        "tentar de novo — repetir igual traz o mesmo resultado."),
    modelo.INDISPONIVEL: (
        "Não foi possível falar com o órgão agora.",
        "Isso costuma passar sozinho. A próxima tentativa pode dar certo sem "
        "que nada mude aqui."),
    modelo.ILEGIVEL: (
        "Veio uma resposta que não é o documento.",
        "Normalmente é a página de erro ou de login do portal chegando no "
        "lugar do PDF."),
    modelo.ERRO: (
        "Algo deu errado nesta consulta.",
        "O texto do órgão está abaixo; se ele não explicar, vale repetir mais "
        "tarde e, persistindo, olhar o registro de tentativas."),
}

# FRASES QUE JÁ FORAM OBSERVADAS DE VERDADE, com a data em que apareceram.
#     Cada entrada é `(trecho observado, o que dizer, quando se observou)`.
#     Entrar aqui exige ter visto a resposta — não basta supor que o órgão
#     usa esse texto.
CONHECIDAS = (
    ("dados inválidos",
     "O serviço não aceitou os dados enviados. No SITFIS isso costuma ser "
     "contribuinte fora da procuração, ou parâmetro fora do cenário.",
     "2026-09-07"),
)


def humanizar(desfecho: str, detalhe: str = "", esfera: str = "") -> dict:
    """`{titulo, orientacao, do_orgao}` — pronto para a tela.

    `do_orgao` sai separado de propósito: a tela pode exibi-lo com outra cor,
    e quem lê sabe qual parte é nossa e qual é do fisco.
    """
    titulo, orientacao = MOLDURA.get(
        desfecho, ("Resultado não classificado.", ""))

    texto = (detalhe or "").strip()
    baixo = texto.lower()
    for trecho, explicacao, _quando in CONHECIDAS:
        if trecho in baixo:
            orientacao = (explicacao + (" " + orientacao if orientacao else ""))
            break

    return {"desfecho": desfecho, "titulo": titulo,
            "orientacao": orientacao, "do_orgao": texto,
            "esfera": esfera}


def frase(desfecho: str, detalhe: str = "", esfera: str = "") -> str:
    """A versão de uma linha, para log e para lista."""
    h = humanizar(desfecho, detalhe, esfera)
    partes = [h["titulo"]]
    if h["do_orgao"]:
        partes.append("O órgão disse: “%s”" % h["do_orgao"])
    return " ".join(partes)
