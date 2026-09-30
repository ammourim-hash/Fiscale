# -*- coding: utf-8 -*-
"""contabil — o módulo Contábil do FISCALE (FASE CONTÁBIL 1).

O que existe nesta fase, e só isto:

    modelo.py          vocabulário fechado, dinheiro em centavos, datas
    base.py            o banco da empresa (um arquivo POR EMPRESA)
    plano.py           plano de contas: sintética/analítica, natureza, ECD/ECF
    lancamentos.py     partida dobrada; nada nasce definitivo
    armazenamento.py   o extrato original, byte a byte
    leitores/          OFX e CSV; PDF só diagnostica (sem leitor de layout)
    extratos.py        a importação: arquivo → movimento, linha a linha
    bancos.py          contas bancárias, busca, movimento → lançamento
    classificacao.py   SUGESTÃO de categoria; nunca lançamento
    fiscal.py          leitura (só leitura) do acervo fiscal que já existe
    conciliacao.py     banco → documento/lançamento
    visao.py           a Visão Geral
    rotas.py           as rotas /api/contabil/*

O QUE ESTE PACOTE NÃO FAZ, DE PROPÓSITO
    Não calcula imposto, não mexe em regra tributária, não escreve no acervo
    fiscal nem no índice dele, não fala com a rede, e não transforma sugestão
    em lançamento definitivo. Quem confirma é uma pessoa.
"""
