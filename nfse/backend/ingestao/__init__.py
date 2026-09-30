"""
Fiscale — Motor de Ingestão Fiscal (ING 1: identidade da empresa + mTLS).

O QUE ESTA FASE ENTREGA
    1. Uma resposta única para "quem é a empresa", com o identificador fiscal
       canônico como chave — em vez de dois cadastros que não conversam.
    2. Um único lugar que abre certificado e monta sessão HTTPS autenticada —
       em vez de três módulos fazendo isso cada um do seu jeito.
    3. O ambiente (produção × homologação) como conceito explícito, antes de
       existir qualquer coleta que possa confundir os dois.
    4. Os contratos oficiais de NF-e, CT-e e NFS-e como dado verificável.

O QUE ESTA FASE **NÃO** FAZ
    Não coleta nada. Sem laço de distNSU, sem avanço de NSU, sem download de
    NF-e, CT-e ou NFS-e, sem checkpoint gravado, sem parser novo, sem
    classificação tributária e sem IA. Nada aqui chama SEFAZ, ADN ou Portal
    Nacional. `contratos.py` declara endereços; declarar não é chamar.

COMPATIBILIDADE
    Nenhum módulo existente foi reescrito. `core.py`, `nfe.py` e `prefeituras.py`
    continuam como estavam. Esta camada é aditiva e passa a ser adotada aos
    poucos — a ideia é não mexer em NFS-e, Plano de Saúde ou ELO sem
    necessidade direta.

RAIZ DE DADOS
    Toda função que precisa de disco recebe `dados_dir` por parâmetro. É o que
    permite testar contra pasta temporária sem chegar perto de `~/Fiscale/dados`.
"""
from .ambiente import PRODUCAO, HOMOLOGACAO, Ambiente, resolver as resolver_ambiente
from .identidade import (
    Identificador, normalizar, so_digitos, cnpj_valido, cpf_valido, mesma_empresa,
    TIPO_CNPJ, TIPO_CPF,
)
from .modelo import (
    Empresa, Credencial, Vinculo, LinhaReconciliacao,
    MOTIVO_TITULAR, MOTIVO_PROCURACAO, MOTIVO_INDEFINIDO,
    CONFIRMADO, SOMENTE_CERTIFICADO, SOMENTE_CLIENTES, AMBIGUO, INVALIDO,
)
from .cadastro import Cadastro, carregar
from .sessao import (
    criar_sessao, abrir_sessao, criar_sessao_para_empresa, inspecionar,
    ErroCredencial, SenhaAusente, CertificadoNaoEncontrado, CertificadoInvalido,
)
from .credencial_estado import (
    EstadoCredencial, avaliar as avaliar_credencial, avaliar_empresas,
    VALIDO, AUSENTE, SENHA_NAO_CADASTRADA, SENHA_INCORRETA, CORROMPIDO,
    AINDA_NAO_VALIDO, EXPIRADO, IMPEDITIVOS,
)
from .checkpoint import (
    Checkpoint, RepositorioCheckpoint, CheckpointCorrompido, ErroCheckpoint,
    NFE_DISTRIBUICAO, CTE_DISTRIBUICAO, NFSE_DISTRIBUICAO, SERVICOS,
    normalizar_nsu, nsu_int,
)
from .trava import travar, TravaOcupada
from .distribuicao import (
    DistribuicaoRunner, ResultadoExecucao, Lote, DocumentoBruto,
    Fonte, FontePorChave, Acervo, executar_para_empresas,
    ErroFonte, ErroTransitorio, ErroDefinitivo, RespostaInvalida,
    CAP_DIST_NSU, CAP_CONS_NSU, CAP_CONS_CHAVE,
)
from .identificacao import (
    Identificacao, identificar, chave_valida, dv_chave, hash_conteudo,
    id_de_nfe, id_de_evento, digitos_da_chave,
    NFE55 as ESPECIE_NFE55, EVENTO_NFE as ESPECIE_EVENTO_NFE,
    DESCONHECIDO as ESPECIE_DESCONHECIDA,
    PRIO_RESUMO, PRIO_COMPLETO, PRIO_AUTORIZADO,
)
from .documento import (
    Documento, Participante, Tributo, ItemNFe, TotaisNFe,
    ExtensaoNFe, ExtensaoEvento, ValorInvalido, dinheiro, soma,
    AUTORIZADO, CANCELADO, DENEGADO, SEM_PROTOCOLO, INDEFINIDA,
    EMITENTE, DESTINATARIO, TERCEIRO,
)
from .acervo import (
    AcervoArquivos, Preservacao, ErroAcervo, abrir as abrir_acervo,
    NOVO, DUPLICATA, COPIA_PROMOVIDA, COPIA_MENOR, COLISAO,
)
from .auditoria import Trilha, abrir as abrir_trilha
from . import auditoria, parsers, indice, pipeline
from .indice import (
    Indice, RelatorioIndexacao, abrir as abrir_indice,
    reconstruir, reprocessar,
    INDEXADO, SCHEMA_DESCONHECIDO, FALHA_PARSER, QUARENTENA,
)
from .pipeline import ingerir, indexar_pendentes, ResultadoIngestao
from . import migracao_legado, migracao_r2, operacao, controlador
from .controlador import (
    Politica, POLITICA_PADRAO, Elegibilidade, ResultadoCiclo,
    ResultadoEmpresa, avaliar as avaliar_elegibilidade,
    executar_ciclo, ordem_do_ciclo, bootstrap_operacao, bootstrap_todas, universo,
    REGRAS_OFICIAIS_DIST_NSU, REGRAS_OFICIAIS_CONSULTA_PONTUAL,
)
from .migracao_legado import migrar_nfe, migrar_todas, prever as prever_migracao
from . import contratos

__all__ = [
    # ING 3 — identificação, acervo, índice, auditoria
    "Identificacao", "identificar", "chave_valida", "dv_chave", "hash_conteudo",
    "id_de_nfe", "id_de_evento", "digitos_da_chave",
    "ESPECIE_NFE55", "ESPECIE_EVENTO_NFE", "ESPECIE_DESCONHECIDA",
    "PRIO_RESUMO", "PRIO_COMPLETO", "PRIO_AUTORIZADO",
    "Documento", "Participante", "Tributo", "ItemNFe", "TotaisNFe",
    "ExtensaoNFe", "ExtensaoEvento", "ValorInvalido", "dinheiro", "soma",
    "AUTORIZADO", "CANCELADO", "DENEGADO", "SEM_PROTOCOLO", "INDEFINIDA",
    "EMITENTE", "DESTINATARIO", "TERCEIRO",
    "AcervoArquivos", "Preservacao", "ErroAcervo", "abrir_acervo",
    "NOVO", "DUPLICATA", "COPIA_PROMOVIDA", "COPIA_MENOR", "COLISAO",
    "Trilha", "abrir_trilha", "auditoria", "parsers", "indice", "pipeline",
    "Indice", "RelatorioIndexacao", "abrir_indice", "reconstruir", "reprocessar",
    "INDEXADO", "SCHEMA_DESCONHECIDO", "FALHA_PARSER", "QUARENTENA",
    "ingerir", "indexar_pendentes", "ResultadoIngestao",
    "migracao_legado", "migrar_nfe", "migrar_todas", "prever_migracao",
    "migracao_r2", "operacao", "controlador",
    "Politica", "POLITICA_PADRAO", "Elegibilidade", "ResultadoCiclo",
    "ResultadoEmpresa", "avaliar_elegibilidade", "executar_ciclo",
    "ordem_do_ciclo", "bootstrap_operacao", "bootstrap_todas", "universo",
    "REGRAS_OFICIAIS_DIST_NSU", "REGRAS_OFICIAIS_CONSULTA_PONTUAL",
    "PRODUCAO", "HOMOLOGACAO", "Ambiente", "resolver_ambiente",
    "Identificador", "normalizar", "so_digitos", "cnpj_valido", "cpf_valido",
    "mesma_empresa", "TIPO_CNPJ", "TIPO_CPF",
    "Empresa", "Credencial", "Vinculo", "LinhaReconciliacao",
    "MOTIVO_TITULAR", "MOTIVO_PROCURACAO", "MOTIVO_INDEFINIDO",
    "CONFIRMADO", "SOMENTE_CERTIFICADO", "SOMENTE_CLIENTES", "AMBIGUO", "INVALIDO",
    "Cadastro", "carregar",
    "criar_sessao", "abrir_sessao", "criar_sessao_para_empresa", "inspecionar",
    "ErroCredencial", "SenhaAusente", "CertificadoNaoEncontrado", "CertificadoInvalido",
    # ING 2 — estado da credencial
    "EstadoCredencial", "avaliar_credencial", "avaliar_empresas",
    "VALIDO", "AUSENTE", "SENHA_NAO_CADASTRADA", "SENHA_INCORRETA", "CORROMPIDO",
    "AINDA_NAO_VALIDO", "EXPIRADO", "IMPEDITIVOS",
    # ING 2 — checkpoint e motor
    "Checkpoint", "RepositorioCheckpoint", "CheckpointCorrompido", "ErroCheckpoint",
    "NFE_DISTRIBUICAO", "CTE_DISTRIBUICAO", "NFSE_DISTRIBUICAO", "SERVICOS",
    "normalizar_nsu", "nsu_int", "travar", "TravaOcupada",
    "DistribuicaoRunner", "ResultadoExecucao", "Lote", "DocumentoBruto",
    "Fonte", "FontePorChave", "Acervo", "executar_para_empresas",
    "ErroFonte", "ErroTransitorio", "ErroDefinitivo", "RespostaInvalida",
    "CAP_DIST_NSU", "CAP_CONS_NSU", "CAP_CONS_CHAVE",
    "contratos",
]
