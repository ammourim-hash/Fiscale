# -*- coding: utf-8 -*-
"""situacao — certidões e extratos de débito das três esferas, num envelope só.

Certidão e extrato têm a mesma forma: documento oficial + esfera + instante +
hash + proveniência. Dois pacotes separados dariam dois armazenamentos, dois
índices, dois portões e duas telas — dois lugares para consertar cada defeito.

    modelo         o vocabulário e a derivação de estado (não toca disco)
    armazenamento  o original imutável, endereçado pelo SHA-256
    indice         o consultável, reconstruível a partir do disco
    importacao     o portão único de entrada
    fontes/        de onde vêm os bytes; elas SÓ entregam bytes

O diagnóstico que originou este pacote está em FISCALE_SITUACAO_FISCAL.md.
"""
