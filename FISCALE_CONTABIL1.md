# FASE CONTÁBIL 1 — o módulo Contábil, primeira versão

*19/09/2026.*

## O que existe

A área **Contábil** (`web/contabil.html`) tem 13 itens de navegação. Quatro
funcionam; nove dizem "Em desenvolvimento" e não têm tela por trás.

*(Onde ela é acessada mudou na CONTÁBIL 1B, no fim deste arquivo: deixou de ser
item da barra do FISCALE e virou aplicação em `Aplicações → Contábil`.)*

| Área | Estado |
|---|---|
| Visão Geral | pronta — pendências, importados, não identificados, divergências, totais, % conciliado, situação da competência |
| Plano de Contas | pronto — por empresa, sintética/analítica, natureza, grupo, ativa/inativa, ref. ECD/ECF guardada, importação CSV tudo-ou-nada |
| Lançamentos | prontos — partida dobrada ao centavo, PENDENTE → CONFIRMADO / CANCELADO, correção por AJUSTE (o original vira AJUSTADO) |
| Bancos | pronto — contas bancárias, Importar Extrato (OFX/CSV), movimentos item a item, classificação sugerida, conciliação, rastreio |
| Clientes, Fornecedores, Estoque, Imobilizado, Empréstimos, Sócios, Fechamento, Demonstrações, SPED | em desenvolvimento |

## Onde mora

```
contabil/                    pacote novo (stdlib + pypdf já existente)
  base.py                    <dados>/<empresa>/contabil/contabil.db — UM banco por empresa
  armazenamento.py           <dados>/<empresa>/contabil/extratos/<id>/original.* (byte a byte)
  leitores/ofx.py            OFX 1.x (SGML) e 2.x (XML)
  leitores/csv_extrato.py    CSV de qualquer banco, pelo nome das colunas
  leitores/pdf.py            só diagnóstico: tem texto? (sem leitor de layout)
  classificacao.py           sugestão; não escreve em banco
  fiscal.py                  leitura do acervo fiscal (índice em mode=ro + core.carregar_notas)
  conciliacao.py, bancos.py, extratos.py, lancamentos.py, plano.py, visao.py, rotas.py
fiscale_server.py            +_contabil(): login, papel, CSRF, multipart → contabil.rotas
fiscale_papeis.py            GET e POST /api/contabil/ para o operador
montar_portatil.py           contabil/ no pacote portátil (só isto; ver abaixo)
web/fiscale-nav.js           (1B: revertido — o Contábil saiu da barra)
web/central.html             (1B) cartão "Contábil" e a vista de Administração
```

## Decisões desta fase

- **Um banco por empresa, não uma coluna `empresa`.** Não há pergunta contábil
  que misture o razão de duas empresas; com arquivo separado não existe `WHERE`
  para esquecer. O banco guarda a própria empresa no `meta` e recusa abrir se
  for copiado para a pasta de outra.
- **O banco é dado primário.** Vai no `.fbk` (a pasta `contabil/` não está em
  `PASTAS_FORA`), e o journal é o padrão (não WAL), porque o backup deixa
  `.db-wal` de fora.
- **Nada nasce definitivo.** `lancamentos.criar()` não tem parâmetro de status.
  Movimento → lançamento só por `gerar_lancamento()`, com a contrapartida
  escolhida por uma pessoa; a ligação fica SUGESTÃO até o lançamento ser
  confirmado.
- **Nenhuma linha some.** CSV: movimentos + linhas ignoradas (com motivo) =
  total de linhas. OFX: nº de `<STMTTRN>` no texto cru = nº de movimentos.
  Valor ou data ilegível vira movimento com `problema`, fora das somas.
- **Idempotência por impressão, não por FITID.** Há banco que muda o FITID a
  cada exportação. O movimento é conta + data + valor + descrição + documento +
  posição entre os iguais do mesmo arquivo. O mesmo arquivo é DUPLICATA pelo
  hash.
- **Transferência não vira resultado.** A contrapartida de um movimento de
  transferência não pode ser RECEITA, CUSTO ou DESPESA. As duas pontas são
  pareadas quando uma delas foi reconhecida pelo texto (nunca só pelo valor).
- **Acervo fiscal só leitura.** O índice da ingestão é aberto em `mode=ro`
  (o `conectar()` dela grava o `meta`); NFS-e pela mesma `core.carregar_notas`
  da apuração. Nenhum parser novo, nenhum segundo acervo.
- **Rotas do operador, escrita com CSRF.** Classificado em `fiscale_papeis`;
  toda escrita confere `X-Fiscale-Csrf`.

## Formatos de extrato

| Formato | Situação |
|---|---|
| OFX 1.x / 2.x | lido item a item (data, data de lançamento, descrição, documento, valor, sentido, tipo, FITID, banco/agência/conta do arquivo, saldo final) |
| CSV | lido item a item quando há cabeçalho com DATA e VALOR (ou CRÉDITO/DÉBITO); saldo por linha, D/C, documento e agência/conta do preâmbulo quando existem |
| PDF | **não importa.** Diz se tem texto (sem OCR) e recusa: não há leitor de layout. Arquitetura pronta em `leitores/pdf.py` (`LAYOUTS`) |

## Limitações conhecidas

- **Nenhum extrato real foi usado.** Não havia OFX/CSV/PDF de banco no projeto
  nem no disco. OFX segue a especificação; o CSV é genérico. O primeiro extrato
  real de cada banco precisa ser conferido — em especial CSV com histórico
  quebrado em duas linhas (a segunda vira linha "SEM VALOR", visível, não é
  juntada).
- OFX com mais de uma conta no mesmo arquivo é recusado.
- Extrato de cartão de crédito (CCSTMTRS) é lido pela mesma rotina, mas não foi
  exercitado.
- Classificação por palavras-chave: sugestão com a regra e a confiança à mostra.
  Aporte/retirada de sócio não usa o CPF do sócio (Sócios não existe ainda).
- A busca de documento fiscal lê todas as NFS-e da empresa a cada busca
  (`core.carregar_notas`). Com milhares de notas, a importação de um extrato
  grande leva alguns segundos a mais.
- Visão Geral não fecha competência — fechamento não existe nesta fase.

## Correção fora do escopo, registrada

`teste_atalho_desktop` já falhava ANTES desta fase (baseline de 19/09):
`fiscale_decisoes_nfe.py` é importado pelo servidor e não estava na lista do
pacote portátil. Essa correção **não entrou nesta fase**: foi tratada em commit
próprio da frente NF-e (`e273709`, "Corrige pacote portátil da NF-e"). O
Contábil mexe em `montar_portatil.py` apenas para levar `contabil/`.

---

# CONTÁBIL 1B — o Contábil vira aplicação, a administração sai de dentro

*19/09/2026. Só arquitetura de navegação: nenhum módulo funcional novo.*

## O que mudou

| Antes (Contábil 1) | Agora (1B) |
|---|---|
| Contábil era item da **barra de módulos do FISCALE** | Contábil é **aplicação**, em `Aplicações → Contábil`, como o ELO |
| A empresa era um `<select>` no topo, guardado no navegador | A empresa é **contexto da aplicação**, na URL (`?empresa=`), escolhida na entrada |
| A tela carregava a barra do FISCALE | A aplicação tem **casca própria** (lateral, trilha, sair) e não carrega a barra do FISCALE |
| Administração espalhada em `Configurações` da Central | Administração só em **`Aplicações → Administração`** |

## O caminho

```
Central de Aplicações → Contábil → Abrir empresa → módulo → módulo → Trocar empresa
                      → Administração  (só admin)
```

- A **empresa mora na URL**. Sobrevive a recarregar e ao voltar do navegador, e
  não depende de `localStorage` — trocar de empresa é navegar de novo, e a
  página recomeça do zero: nada da empresa anterior fica na memória da tela
  (movimento aberto, formulário pela metade, plano carregado).
- Empresa que não está no cadastro de Clientes **não abre**, nem digitada à mão
  na URL: a entrada volta com o motivo.
- Dentro da aplicação: 4 módulos funcionais (Visão Geral, Plano de Contas,
  Lançamentos, Bancos) e 9 com `Em desenvolvimento`, separados por um traço na
  lateral. Nenhum deles tem tela provisória.

## Administração da plataforma

`Aplicações → Administração` (cartão só para quem o servidor diz que é admin)
reúne: **Usuários e permissões**, **Segurança e auditoria** (registro de
acessos), **Backups administrativos**, e — declarados `Em desenvolvimento` —
**Gestão de aplicações** e **Configurações administrativas**.

A lista é montada **pelo script, sob `if(q.admin)`**: para o operador os links
não existem nem no documento. Quem protege continua sendo o servidor
(`fiscale_papeis._ADMIN_PAGINAS` e as rotas), e isso não mudou.

`Configurações` da Central voltou a ser só da própria conta (trocar senha).

## Barra superior do FISCALE

Continua sendo a área da plataforma (`Aplicações · ELO · Backup · Usuários ·
Segurança`). O que mudou é a etiqueta de quem entrou: em vez de `admin (admin)`,
agora diz **`fulano · administração da plataforma`** (ou `· operador`), com a
explicação no `title` de que esse perfil é da plataforma, não contábil.

## O que NÃO mudou

Nenhuma regra fiscal, NFS-e, NF-e, ELO, ING, checkpoint, acervo, backup ou
segurança. No servidor, **nada**: as rotas `/api/contabil/*`, os papéis e o
CSRF são os mesmos da Contábil 1. A mudança é de navegação, em três arquivos de
tela.

---

# CONTÁBIL 2 — o Fiscal entra no Contábil por competência

*19/09/2026.*

## A cadeia, e de onde vem cada elo

```
documento fiscal → operação fiscal → FATO CONTÁBIL → sugestão → lançamento
   acervo/índice     normalizacao        contabil/      contabil/   PENDENTE
   (ingestão)        + vendas (CFOP)     fatos.py       fatos.py
```

Os dois primeiros elos **já existiam e não foram reescritos**: a operação vem
de `ingestao.normalizacao` (a mesma que a apuração usa) e a natureza da
operação, de `ingestao.vendas` (a tabela de CFOP continua só dela). Não há
parser novo, tabela de CFOP nova nem segundo acervo.

## O que o fato guarda

`empresa` (é o banco) · `competencia` · `id_documento` · `origem` · `tipo` ·
`valor` · `situacao` · `caminho` até o XML — mais chave, número, contraparte,
CFOP, retenções e o hash do conteúdo. Com isso o Contábil responde sozinho:

- **"quais documentos formaram os lançamentos desta competência?"**
  `fatos.listar(competencia=..., estado=LANCADO)`
- **"qual lançamento nasceu desta nota?"** `fatos.rastreio(id_documento)`, e
  pela rede `GET /api/contabil/impacto?id_documento=…` — que é o que a tela de
  Documentos Fiscais chama em **"Ver impacto contábil"**.

## Regras desta fase

- **Um fato por documento.** A chave é o `id_documento`. Sincronizar de novo
  atualiza; nunca duplica. Documento que mudou de situação no acervo (foi
  cancelado depois) é atualizado — e, se já tinha lançamento, isso vira
  **divergência à vista**, não conserto silencioso.
- **A competência é explícita** e sai do documento, nunca da data em que se
  clicou em sincronizar. Não existe "todas as competências": a rota recusa.
- **A sugestão fala em PAPEL**, não em conta. `CLIENTES`, `FORNECEDORES`,
  `MERCADORIAS`… são ligados às contas desta empresa em *Plano de Contas →
  Contas padrão*. Sem o papel ligado, o fato fica `SEM_LANCAMENTO` dizendo
  qual falta — e nenhuma conta é inventada.
- **Nada nasce definitivo.** O lançamento gerado do Fiscal é `PENDENTE`, com
  origem `FISCAL` e lote `FISCAL <competência>`.
- **Transferência entre estabelecimentos não vira receita nem despesa** — fica
  sem sugestão, com o motivo escrito.
- Documento **fora da receita** (cancelado, denegado, sem protocolo) entra como
  `SEM_EFEITO`: não pede lançamento e não entra no valor fiscal.

| tipo do fato | sugestão |
|---|---|
| Receita de serviço (NFS-e/CT-e emitido) | D Clientes (+ D Retenções) / C Receita de serviços |
| Receita de venda (NF-e/NFC-e emitida) | D Clientes / C Receita de vendas |
| Compra de mercadoria | D Mercadorias / C Fornecedores |
| Compra de imobilizado (CFOP 551/552) | D Imobilizado / C Fornecedores |
| Serviço tomado · Frete tomado | D Serviços tomados / Fretes / C Fornecedores |
| Devolução de venda · de compra | D Devoluções de vendas / C Clientes · D Fornecedores / C Devoluções de compras |
| Transferência, remessa, indeterminado, sem efeito | **sem sugestão**, com o motivo |

No banco, `D Banco / C Clientes` e `D Fornecedores / C Banco` passaram a sair
sozinhos quando a categoria do movimento é cliente ou fornecedor e o papel
está ligado.

## Fechamento por competência

`ABERTA → EM CONFERÊNCIA → FECHADA`. Competência sem linha é ABERTA (não há
migração). **EM CONFERÊNCIA não barra nada** — é recado; barrar faria fechar
antes da hora. **FECHADA** barra criar, confirmar e cancelar lançamento e
gerar lançamento do Fiscal. **Não barra sincronizar**: o acervo continua
andando, e uma nota cancelada depois do fechamento tem de aparecer como
divergência em vez de ser escondida pela tranca. Reabrir exige motivo e fica
no histórico, com quem reabriu.

## Divergências Fiscal × Contábil

`DOCUMENTO_SEM_EFEITO_COM_LANCAMENTO` · `VALOR_DIVERGENTE` ·
`COMPETENCIA_DIVERGENTE` · `LANCAMENTO_FISCAL_SEM_FATO`. Fato sem lançamento
**não** é divergência: é cobertura, e tem contador próprio.

## Medição contra o acervo REAL (leitura)

Duas empresas do escritório, competências 07 e 08/2026, com o banco contábil
numa pasta temporária e o acervo real aberto só para leitura
(`sincronizar(..., dados_fiscais=...)`):

| | documentos | fatos | lançamentos sugeridos | cobertura | divergências |
|---|---:|---:|---:|---:|---:|
| empresa 1 · 07/2026 | 138 | 138 | 136 | 99,3% | 0 |
| empresa 1 · 08/2026 | 171 | 171 | 163 | 98,2% | 0 |
| empresa 2 · 07/2026 | 33 | 33 | 32 | 97,0% | 0 |
| empresa 2 · 08/2026 | 42 | 42 | 39 | 100% | 0 |

**384 documentos, 370 lançamentos — todos PENDENTE.** O hash da pasta de cada
empresa na produção ficou idêntico antes e depois: nada foi escrito lá.
Os recusados são remessas/retornos e um CFOP fora da tabela conhecida: sem
sugestão, com o motivo, à espera de decisão humana.

## Limitações

- **CT-e vem do índice**, não da normalização de notas (ele não tem item nem
  CFOP). Tipo e valor saem do índice; o teste com CT-e real ainda não foi
  feito — o acervo do escritório tem CT-e de terceiros (papel AUTXML), que
  não é frete tomado pela empresa.
- A lista de competências oferecida na tela vem do índice fiscal e do que já
  existe no Contábil. Um mês que só tenha NFS-e não aparece sozinho: para
  esse caso a tela tem o campo "Abrir outra" (digitar o mês).
- Sincronizar lê todas as NFS-e da empresa (o leitor é o da apuração) — em
  empresas com milhares de notas, a primeira sincronização leva alguns
  segundos.
- Não foram implementados, de propósito: ECD, ECF, estoque/CMV, depreciação,
  folha contábil, cálculo tributário novo e qualquer automatismo que confirme
  lançamento sozinho.
