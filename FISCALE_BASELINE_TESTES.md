# Baseline de testes do FISCALE

Este arquivo existe porque o baseline vinha sendo reportado só no chat, e um
salto de 2.849 para 3.059 asserções ficou sem origem escrita. Aqui ele passa a
ter. **Regra:** todo salto de baseline se explica linha a linha, ou não é
baseline — é número solto.

Como reproduzir (a soma é de todas as suítes `teste_*.py`, exceto `teste_apoio`
e `teste_fixturas_*`, que são apoio e não têm asserção própria):

```bash
for f in teste_*.py; do ./nfse/.venv/Scripts/python.exe -X utf8 "$f"; done
```

---

## Reconciliação 2.849 → 3.059 (17/08/2026, APURAÇÃO 6/6A)

O baseline de **2.849** era a soma de **21** suítes. As duas suítes mais
antigas — `teste_fiscale_sessoes` e `teste_fiscale_elo_sync` — sempre rodaram
verdes, mas **nunca entraram na soma**: elas imprimem "Todos os testes
passaram" em vez de `N ok · M falha(s)`, e o script que totalizava só
reconhecia o segundo formato. Não eram testes novos; eram testes invisíveis
para a conta.

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (21 suítes) | **2.849** | |
| `teste_apuracao6.py` | **+116** | suíte nova da APURAÇÃO 6 |
| `teste_apuracao2.py` 151 → 152 | **+1** | a asserção "tpNF=1 é saída" virou **duas**: "terceiro emitiu ⇒ ENTRADA" e "a empresa emitiu ⇒ SAÍDA" (D47) |
| `teste_fiscale_sessoes.py` | **+32** | já existia e já passava; entrou na conta |
| `teste_fiscale_elo_sync.py` | **+60** | idem |
| `teste_ingestao4d3b.py` 107 → 108 | **+1** | ver nota abaixo |
| **total** | **3.059** | **24 suítes, 0 falhas** |

**Nota sobre o +1 do `teste_ingestao4d3b`.** O arquivo não foi tocado desde
`16/08/2026 22:26` — mesma janela da própria ING 4D-3B. O 107 registrado veio
de uma execução feita **minutos antes** dessa última edição do arquivo; a
suíte já valia 108 quando a fase foi aprovada. Não houve asserção acrescentada
depois, e não houve mudança de comportamento: foi erro de instantâneo meu ao
anotar o número, não evolução do código.

---

## Composição atual — 24 suítes · 3.059 asserções · 0 falhas

| suíte | asserções | | suíte | asserções |
|---|---:|---|---|---:|
| `teste_apuracao1` | 265 | | `teste_ingestao3b` | 139 |
| `teste_apuracao2` | 152 | | `teste_ingestao3b_r2` | 157 |
| `teste_apuracao3` | 86 | | `teste_ingestao3c_eventos` | 71 |
| `teste_apuracao4` | 116 | | `teste_ingestao3c_preparo` | 159 |
| `teste_apuracao5` | 114 | | `teste_ingestao4b` | 92 |
| `teste_apuracao6` | 116 | | `teste_ingestao4c` | 283 |
| `teste_backup` | 141 | | `teste_ingestao4d2b` | 74 |
| `teste_fiscale_elo_sync` | 60 | | `teste_ingestao4d3b` | 108 |
| `teste_fiscale_sessoes` | 32 | | `teste_piloto_nfe` | 64 |
| `teste_ingestao` | 102 | | `teste_portabilidade` | 102 |
| `teste_ingestao2` | 173 | | `teste_saude` | 144 |
| `teste_ingestao3` | 193 | | `teste_seguranca` | 116 |

---

## Reconciliação 3.059 → 3.184 (18/08/2026, APURAÇÃO 6B)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (24 suítes) | **3.059** | |
| `teste_apuracao6b.py` | **+124** | suíte nova do portão de importação |
| `teste_ingestao4d3b.py` 108 → 109 | **+1** | a suíte varre **todos** os módulos procurando acesso ao estado legado; o `importacao.py` entrou na varredura sozinho |
| **total** | **3.184** | **25 suítes, 0 falhas** |

O `teste_apuracao2` não mudou de contagem: a lista de consumidores da
normalização ganhou `importacao.py`, mas continua sendo **uma** asserção.

---

## Reconciliação 3.184 → 3.228 (18/08/2026, APURAÇÃO 6B-2)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (25 suítes) | **3.184** | |
| `teste_apuracao6b.py` 124 → 168 | **+44** | testes do defeito do índice: detecção, conserto, idempotência e as duas guardas novas |
| **total** | **3.228** | **25 suítes, 0 falhas** |

---

## Reconciliação 3.228 → 3.328 (18/08/2026, APURAÇÃO 6C)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (25 suítes) | **3.228** | |
| `teste_apuracao6b.py` 168 → 268 | **+100** | painel operacional, situação do documento, e as contagens que precisam fechar com a tabela |
| **total** | **3.328** | **25 suítes, 0 falhas** |

Nenhuma suíte perdeu asserção. Três guardas existentes tiveram a **expectativa**
atualizada, sem mudar de contagem: `teste_apuracao3` e `teste_apuracao6`
passaram a aceitar `importacao.py` como leitor de `conferencia`/`vendas` (é
conferência, não apuração), e a guarda de "a tela não faz conta de imposto"
passou a olhar **só o que a 6C acrescentou** — varrer a página inteira acusava
a aba do Auditor, que fala de alíquota desde antes.

---

## Reconciliação 3.328 → 3.488 (18/08/2026, APURAÇÃO 6D)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (25 suítes) | **3.328** | |
| `teste_apuracao6d.py` | **+154** | fontes de emissão e espécie NFCE65 |
| `teste_apuracao6b.py` 268 → 273 | **+5** | a seção que recusava NFC-e virou a que a aceita como NFCE65, mais a recusa de modelo 57 |
| `teste_ingestao4d3b.py` 109 → 110 | **+1** | a suíte varre todos os módulos; `fontes_emissao.py` entrou sozinho |
| **total** | **3.488** | **26 suítes, 0 falhas** |

---

## Reconciliação 3.488 → 3.597 (18/08/2026, FASE 7B)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (26 suítes) | **3.488** | |
| `teste_autxml.py` | **+108** | fonte autXML: roteamento, posse do checkpoint, evidência |
| `teste_apuracao6b.py` | **+1** | guarda de consumidores do portão passou a incluir `autxml.py` |
| **total** | **3.597** | **27 suítes, 0 falhas** |

---

## Reconciliação 3.597 → 3.586 (22/08/2026, FISCAL AI 1)

Primeiro é preciso corrigir o próprio ponto de partida. O 3.597 registrado em
18/08 é de **27 suítes**; depois disso entraram `teste_papeis` (115) e
`teste_estado_concorrente` (11) sem passar por aqui. E, mais grave, o baseline
deixou de ser verdadeiro sem que ninguém mexesse no código:

**Medição real de 22/08, antes de qualquer alteração desta fase:**

| | |
|---|---:|
| 26 suítes com contagem, somadas | **3.628** |
| `teste_fiscale_sessoes` + `teste_fiscale_elo_sync` (imprimem sem número) | **92** |
| **total** | **3.720** |
| **falhas** | **3** |

As 3 falhas eram todas de `teste_ingestao4c` (280 ok · 3 falhas) — data fixa
vencida, não regressão. Ver **D70**.

### O que mudou

| origem | asserções | o que é |
|---|---:|---|
| medição de partida (28 suítes) | **3.720** | com 3 falhas |
| `teste_ingestao4c` 280 → 283 | **+3** | as três que falhavam voltaram a passar; o `AGORA` passou a derivar do relógio (D70) |
| `teste_saude` | **−144** | suíte inteira removida com o módulo (D71) |
| `teste_seguranca` 116 → 133 | **+17** | **−1** (o item "Senha do portal" do diagnóstico, que era do módulo removido) e **+18** de cobertura nova: cofre com `MAPA` vazio, inscrição de tela protegendo backup pelo mesmo gesto, blob DPAPI removido mesmo de tela não inscrita, e a garantia de que a rede de segurança não confunde chave de acesso de nota com segredo (D69) |
| `teste_papeis` 115 → 105 | **−10** | **−11** asserções de rotas e módulos de estado que deixaram de existir (`/api/saude/*`, `/api/plano/senha`, `state/plano`, `state/ecac`) e **+1** nova: a lista de módulos de estado passou a ser conferida por igualdade, em vez de um piso numérico que envelhecia sozinho |
| **total** | **3.586** | **26 suítes com contagem + 2 sem, 0 falhas** |

`teste_backup` e `teste_portabilidade` mantiveram a contagem: os fixtures que
usavam `state_plano.json` e a pasta `saude/` foram trocados por uma tela
fictícia (`portal_teste`) e por `municipal/`. O que eles provam não mudou — só
deixou de depender de qual tela o produto tem no momento.

---

## Composição atual — 28 suítes · 3.586 asserções · 0 falhas

| suíte | asserções | | suíte | asserções |
|---|---:|---|---|---:|
| `teste_apuracao1` | 265 | | `teste_ingestao3b` | 139 |
| `teste_apuracao2` | 152 | | `teste_ingestao3b_r2` | 157 |
| `teste_apuracao3` | 86 | | `teste_ingestao3c_eventos` | 71 |
| `teste_apuracao4` | 116 | | `teste_ingestao3c_preparo` | 159 |
| `teste_apuracao5` | 114 | | `teste_ingestao4b` | 92 |
| `teste_apuracao6` | 116 | | `teste_ingestao4c` | 283 |
| `teste_apuracao6b` | 273 | | `teste_ingestao4d2b` | 74 |
| `teste_apuracao6d` | 154 | | `teste_ingestao4d3b` | 111 |
| `teste_autxml` | 108 | | `teste_papeis` | 105 |
| `teste_backup` | 141 | | `teste_piloto_nfe` | 64 |
| `teste_estado_concorrente` | 11 | | `teste_portabilidade` | 102 |
| `teste_fiscale_elo_sync` | 60 | | `teste_seguranca` | 133 |
| `teste_fiscale_sessoes` | 32 | | `teste_ingestao` | 102 |
| `teste_ingestao2` | 173 | | `teste_ingestao3` | 193 |

---

## Reconciliação 3.586 → 3.754 (23/08/2026, NF-e 2)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (28 suítes) | **3.586** | |
| `teste_nfe2.py` | **+166** | suíte nova: consulta indexada, filtros, paginação, ordenação, equivalência com o leitor legado, e a guarda de que a consulta local nunca fala com a SEFAZ |
| `teste_ingestao4d3b.py` 111 → 113 | **+2** | a suíte varre **todos** os módulos do pacote cobrando que nenhum abra o estado legado; `consulta.py` e `equivalencia.py` entraram sozinhos na varredura |
| **total** | **3.754** | **29 suítes, 0 falhas** |

Nenhuma outra suíte mudou de contagem — a NF-e 2 não alterou comportamento de
produção: `GET /api/nfe/notas` continua servindo o leitor legado.

---

## Reconciliação 3.754 → 3.871 (23/08/2026, NF-e 3)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (29 suítes) | **3.754** | |
| `teste_nfe3.py` | **+116** | suíte nova: inventário, manifesto, dry-run que não escreve, migração, idempotência, conflito de serialização × conflito real, checkpoint intocado, equivalência pós-migração |
| `teste_ingestao4d3b.py` 113 → 114 | **+1** | a varredura de módulos pegou `migracao_legado_xml.py` sozinha — mesma guarda que na NF-e 2 pegou `consulta.py` e `equivalencia.py` |
| **total** | **3.871** | **30 suítes, 0 falhas** |

`teste_apuracao6b` manteve 273: a lista de consumidores do portão ganhou
`migracao_legado_xml.py`, mas continua sendo **uma** asserção — e foi ela que
cobrou a decisão de a migração passar pelo portão em vez de abrir um segundo
caminho de escrita no acervo.

---

## Reconciliação 3.871 → 4.013 (23/08/2026, NF-e 4)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (30 suítes) | **3.871** | |
| `teste_nfe4.py` | **+141** | suíte nova: cancelamento por evento (as duas verdades), os cinco papéis pela estrutura oficial, múltiplos papéis, CST × CSOSN separados, CEST, origem, PIS/COFINS/IPI/ICMS por item, IBS/CBS/IS, reindexação com prova de invariantes |
| `teste_ingestao4d3b.py` 114 → 115 | **+1** | a varredura de módulos pegou `reindexacao.py` sozinha — terceira vez que essa guarda registra um módulo novo |
| **total** | **4.013** | **31 suítes, 0 falhas** |

Três suítes tiveram expectativa atualizada **sem mudar de contagem**, porque
documentavam defeitos que esta fase corrigiu:

- `teste_apuracao6b` (273): a guarda de consumidores do portão reprovou quando
  `consulta.py` passou a importá-lo. Em vez de afrouxar a guarda, o critério de
  cancelamento mudou de casa (`importacao` → `documento`) e a guarda voltou ao
  verde sozinha — leitura não depende mais de escrita.
- `teste_nfe2` (166): a asserção "sem divergência" passava pelo motivo errado —
  os dois leitores concordavam porque *nenhum* aplicava o evento de
  cancelamento. Agora ela cobra a divergência e verifica que o índice é o lado
  certo.
- `teste_nfe3` (116): esperava 1 divergência de `CANCELAMENTO`; agora cobra 0,
  com a explicação de que a NF-e 4A a fechou.

---

## Reconciliação 4.013 → 4.167 (23/08/2026, NF-e 5)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (31 suítes) | **4.013** | |
| `teste_nfe5.py` | **+153** | contrato da API, paginação, 16 filtros, cards honestos, detalhe, XML pelo acervo, path traversal, os 300 documentos, empresa sem índice, a tela |
| `teste_ingestao4d3b.py` 115 → 116 | **+1** | a varredura pegou `prova_visibilidade.py` — quarta vez que essa guarda registra um módulo novo sozinha |
| **total** | **4.167** | **32 suítes, 0 falhas** |

---

## Reconciliação 4.167 → 4.482 (23/08/2026, NF-e 6)

| origem | asserções | o que é |
|---|---:|---|
| baseline anterior (32 suítes) | **4.167** | |
| `teste_nfe6.py` | **+311** | lote pelo mesmo filtro da tela, ZIP byte a byte, reconciliação, CSV, canceladas, papel, nomes sanitizados, pacote Domínio, lote vazio, lote grande, arquivo ausente, path traversal, `dhSaiEnt`, reindexação, DANFE, CODE-128C, cache |
| `teste_nfe2.py` 166 → 168 | **+2** | a asserção "filtrar por ENTRADA é recusado" virou "o filtro funciona, e `CAPTURA` continua recusada com motivo" |
| `teste_nfe5.py` 153 → 154 | **+1** | "declara o DANFE como pendente" virou "o DANFE existe e continua se declarando auxiliar" |
| `teste_ingestao4d3b.py` 116 → 117 | **+1** | a varredura de módulos pegou `exportacao.py` sozinha — quinta vez que essa guarda se atualiza |
| **total** | **4.482** | **33 suítes, 0 falhas** |

---

## Reconciliação da contagem (29/08/2026) — **45 suítes · 5.922 asserções · 0 falhas**

Investigação pedida antes do reinício, porque dois números conflitavam:
47 suítes/6.053 e 48 suítes/5.830. **Nenhum dos dois estava certo**, e as duas
causas são diferentes.

### A contagem de suítes: 48 são ARQUIVOS, 45 são suítes

Existem 48 arquivos `teste_*.py`. Três deles são **módulos de apoio**,
importados pelas outras — `teste_apoio.py`, `teste_fixturas_fiscal.py` e
`teste_fixturas_nfe.py`. Executados isoladamente saem com **código 0 e zero
asserções**: foram contados como suítes verdes sem executar nada.

A convenção deste documento sempre contou suítes executáveis (33 na NF-e 6,
36 na NF-e 6B). **São 45.**

### De onde saiu 5.830

```
5.922  (total correto)
  − 32  teste_fiscale_sessoes.py
  − 60  teste_fiscale_elo_sync.py
──────
5.830
```

São exatamente as duas suítes que imprimem o resumo num formato que a regex do
runner não casava — somadas como zero. Elas já eram conhecidas: a reconciliação
da NF-e 5 registrava "+92 das duas suítes que imprimem sem número". O runner
regrediu para o problema que aquela nota descrevia.

### De onde saiu 6.053 — não sei, e digo por quê

6.053 fica **131 acima** do total real; considerando que a suíte HTTP (51) foi
criada depois, a diferença sobe para **182**. Nenhuma das quatro regras de
contagem aplicadas à árvore de hoje produz 6.053:

| regra | total |
|---|---:|
| A — resumo ancorado em `^`, zero quando indentado | 5.026 |
| **B — o resumo PRÓPRIO de cada arquivo (correto)** | **5.922** |
| C — `grep -c ok` frouxo | 6.594 |
| D — contagem de linhas `  ok ` | 6.546 |

**Não existe registro por arquivo daquela execução.** O baseline deixou de ser
alimentado depois da NF-e 6B (36 suítes · 4.787), e sem a decomposição não há
como demonstrar a origem — só constatar que o número não se reproduz.

A hipótese que a aritmética admite é perda de cobertura num arquivo
reconstruído. **Não é verificável:** `teste_backup_drive.py` foi criado depois
do último commit (27/08 22:27), não está versionado, e não há cópia dele em
git, em `backup_pre_*` nem em `.bak`. Fica como hipótese, não como achado.

### O defeito que a investigação encontrou no PRÓPRIO conjunto de testes

`teste_nfe6b.py:84` e `teste_nfe6b_ux.py:72` fazem
`from teste_nfe6 import montar, carregar_main, CONTA, Req`. Como `teste_nfe6.py`
tem as asserções no nível do módulo, **importar executa as 312 dele de novo**.
A saída dessas suítes traz duas linhas-resumo:

| arquivo | resumos na saída | linhas `  ok ` |
|---|---|---:|
| `teste_nfe6.py` | 312 | 312 |
| `teste_nfe6b.py` | 312 **e** 125 | 437 |
| `teste_nfe6b_ux.py` | 312 **e** 190 | 502 |

Consequências:

- somar essas três varia entre **627** (o resumo próprio de cada uma, correto),
  **936** (primeira linha-resumo) e **1.251** (contagem de linhas) — uma
  oscilação de até 624 asserções em três arquivos, que é a ordem de grandeza
  de todo o desacordo;
- numa rodada completa, as 312 asserções do `teste_nfe6` rodam **três vezes**,
  e ela é das mais lentas (gera DANFE em PDF).

**Regra de contagem que este documento passa a exigir:** a linha-resumo
**própria** do arquivo — a última da saída. Foi assim que 5.922 foi medido.

### A execução, com prova

| | |
|---|---:|
| arquivos `teste_*.py` | 48 |
| suítes executáveis | **45** |
| módulos de apoio (0 asserções) | 3 |
| **asserções** | **5.922** |
| **falhas** | **0** |
| códigos de saída ≠ 0 | **nenhum** |
| suítes que estouraram tempo | **nenhuma** |
| suítes sem contagem apurável | **nenhuma** |
| tempo total das 48 execuções | 710 s |

Cada arquivo foi executado como processo próprio, com o código de saída e a
saída capturados — nada foi dado como verde por omissão. Os seis arquivos que
antes suspeitei não propagarem falha (`sys.exit(1)` ausente) na verdade fazem
`sys.exit(main())` com `return 1 if _falhas else 0`: **os códigos de saída estão
corretos**; o problema deles era só a linha-resumo indentada.

### Nada foi removido

Por git, desde o commit de 27/08 22:27: **nenhum arquivo de teste apagado**, e
os seis versionados que mudaram **todos cresceram** — `teste_papeis` +42/−0,
`teste_seguranca` +29/−6, `teste_backup` +15/−7, `teste_nfe6b_ux` +13/−2,
`teste_login_tempo` +4/−1, `teste_ingestao3` +2/−1. Cinco suítes são posteriores
ao commit e não têm histórico: `teste_auditoria`, `teste_backup_drive`,
`teste_backup_v2`, `teste_cadastro1`, `teste_importar_http`.

As suítes que já existiam na NF-e 6 devolvem hoje **exatamente** o mesmo número
de então (apuracao1 265, apuracao6b 273, ingestao3 193, ingestao4c 283,
ingestao3b_r2 157, nfe2 168, nfe3 116, nfe4 141, nfe5 154…). Nenhuma regrediu.

---

## Baseline oficial e pendências técnicas (29/08/2026)

**Baseline oficial: 45 suítes executáveis · 5.922 asserções únicas · 0 falhas.**

"Únicas" é a palavra que importa: a contagem soma o resumo **próprio** de cada
arquivo. As 312 asserções do `teste_nfe6` executam três vezes numa rodada
completa, e contá-las três vezes seria inflar o número sem cobrir nada a mais.

### Pendências do conjunto de testes — não mexer junto com produção

| # | Pendência | Origem |
|---|---|---|
| T1 | Separar os utilitários importados de `teste_nfe6.py` (`montar`, `carregar_main`, `CONTA`, `Req`) para um módulo de apoio | `teste_nfe6b.py:84` e `teste_nfe6b_ux.py:72` importam a suíte inteira |
| T2 | Impedir a reexecução aninhada das 312 asserções | consequência de T1; hoje elas rodam 3× por rodada, e `teste_nfe6` é das mais lentas (gera DANFE) |
| T3 | O runner precisa distinguir módulo de apoio de suíte | `teste_apoio.py`, `teste_fixturas_fiscal.py` e `teste_fixturas_nfe.py` saem com código 0 e zero asserções, e vinham sendo contados como suítes verdes |
| T4 | Uma única linha final estruturada por suíte | hoje há dois formatos (`N ok · M falha(s)` em coluna 0 e indentado), e é disso que nasceram 5.830 e 6.053 |

Enquanto T4 não existir, a regra de contagem é: **a última linha-resumo da
saída** — a própria do arquivo.

### Achado de produção, durante a verificação do reinício

**`POST /api/backup/*` é tratado ANTES do portão de login.** No `do_POST`, o
bloco de backup está na linha relativa 19; `_exigir_login` só aparece na 248 e
`_barrar_por_papel` na 250.

É a **mesma classe de defeito** que acabou de ser corrigida em
`/api/clientes/importar/*`. Consequências, medidas:

- sem sessão, `POST /api/backup/criar` responde **403** (`somente o
  administrador`) em vez de **401** — quem recusa é a autodefesa do bloco, não
  o portão central;
- `fiscale_papeis` **não governa** essas rotas, então a lista de permissão não
  as cobre.

Não há vazamento: o bloco se defende sozinho e recusa. Mas a defesa está no
lugar errado, e foi exatamente esse arranjo que produziu o 404 do cadastro.

**Não corrigido neste reinício, por não estar no escopo autorizado.** Registrado
como **T5**.

### Falso alarme meu, para não voltar

Cheguei a apontar que `/seguranca_acessos.html` respondia 200 sem sessão. Era o
`urllib` seguindo o 302: **todas** as páginas redirecionam para `/login.html`
igualmente. Conferido página a página. Não há vazamento.

---

## Reconciliação 5.922 → 5.959 (30/08/2026, botão «Baixar modelo»)

| origem | asserções | o que é |
|---|---:|---|
| baseline oficial (45 suítes) | **5.922** | |
| `teste_clientes_modelo.py` | **+36** | suíte nova: sintaxe de todo JS embutido pelo parser real, estrutura do botão, os estados exigidos, e o modelo servido numa instância isolada (BOM, `;`, cabeçalho, exemplos) |
| `teste_cadastro1.py` 198 → 199 | **+1** | a asserção que cobrava `alert(` virou duas: uma cobra `_statusModelo(`, a outra cobra a **ausência** de `alert` |
| **total** | **5.959** | **46 suítes, 0 falhas** |

Nenhuma outra suíte mudou de contagem — conferido arquivo a arquivo.

### O defeito: um erro de sintaxe matou a tela inteira

`web/clientes.html` tinha, dentro de `aplicarImportacao()`, um literal de aspas
simples partido em duas linhas:

```js
if (!confirm('Gravar as alterações no cadastro de clientes?
                                                            ← quebra REAL
'
    + 'Uma cópia do cadastro atual é guardada antes.')) return;
```

O `<script>` inteiro deixou de compilar. **Nenhuma função da tela passou a
existir** — nem `baixarModelo`, nem `iniciar`, nem nenhum outro `onclick`. A
tela abria, os botões apareciam, e clicar não fazia nada e não dizia nada.

O sintoma reportado foi só o botão «Baixar modelo». O alcance era a tela toda.

**Como ficou provado que era parse, e não execução:** `baixarModelo`, `iniciar`
e `revisarDaNFe` são todas declarações `function`, que sofrem *hoisting*. Se um
erro de execução tivesse interrompido o script, elas existiriam mesmo assim.
Todas `undefined` só acontece quando o bloco nunca chega a ser compilado.

**Por que nenhum teste pegou:** `teste_importar_http.py` já provava que
`GET /api/clientes/modelo` devolve 200 com o CSV certo — e devolvia mesmo. O
servidor esteve certo o tempo todo. Nada exercitava o navegador.

### A guarda que passa a existir

`teste_clientes_modelo.py` roda `node --check` sobre **todo** JavaScript
embutido em `web/*.html`. E, para não ser uma guarda que só confirma o que já
está certo, ela alimenta o parser com **o defeito real** — a string partida em
duas linhas — e exige que ele **reprove**.

Quando o Node não existe na máquina, a conferência de sintaxe é pulada e as
asserções estruturais continuam valendo. Nenhuma dependência nova foi
acrescentada ao pacote portátil.

### O que a prova de navegador mostrou

Feita num FISCALE isolado (porta e pasta próprias, admin descartável criado ali;
**nem a sessão nem a senha do usuário foram usadas**), com o `onclick` real:

| | |
|---|---|
| blob | 1 criado · `text/csv` · 488 bytes |
| nome do arquivo | `modelo-empresas-fiscale.csv` |
| link temporário | clicado com `href` `blob:`, ainda no DOM |
| revogação | **5.007 ms depois do clique** |
| BOM | `EF BB BF` |
| separador | `;` · 14 colunas · CRLF |
| linhas | cabeçalho + **2 exemplos** (um CNPJ, um CPF) |
| erros no console | **nenhum** |

Estados do botão: `⬇ Baixar modelo` → `Preparando modelo…` → `Modelo baixado`
→ volta ao rótulo em 2,5 s. Os quatro modos de falha (401, 500, rede caída,
modelo vazio) mostram frase própria **na tela** e reabilitam o botão.

O `alert` saiu de propósito: ele some ao clicar em OK, e quem estava lendo o
motivo do erro perde a frase. O recado agora fica num elemento com `aria-live`.

**A revogação é adiada de propósito.** `URL.revokeObjectURL` na mesma volta do
laço cancela o download em alguns navegadores: o `click()` é atendido depois, e
a URL precisa existir nessa hora.

### Correção do meu próprio runner

O runner de contagem ainda usava a regex ancorada em `^` — a mesma que, na
reconciliação de 29/08, me fez reportar 6.231 em vez de 5.922. Passou a usar a
busca sem âncora e a **última** linha-resumo: o resumo próprio do arquivo, não
o de uma suíte que ele tenha importado. Continua sendo pendência **T4** dar às
suítes uma linha final única e estruturada, para o runner não precisar adivinhar.

---

## Higiene Técnica 1 (30/08/2026) — **46 suítes · 5.959 asserções · 0 falhas**

Fase de higiene: **nenhuma funcionalidade fiscal foi alterada**, e a contagem
saiu igual à de entrada — 5.959 antes, 5.959 depois. É o resultado que se
espera de uma limpeza: o mesmo número, obtido de forma confiável.

### T5 — o portão central passou a governar `POST /api/backup/*`

O bloco de backup vivia **antes** de `_exigir_login` e `_barrar_por_papel`, e
se defendia sozinho com um `eh_admin`. Duas consequências:

- sem sessão a resposta era **403** ("somente o administrador") em vez de
  **401** — quem recusava era a autodefesa, não o portão;
- `fiscale_papeis` **não governava** essas rotas: uma rota nova sob
  `/api/backup/` nascia fora da lista de permissão.

O bloco foi movido para depois dos portões e a autodefesa de **papel** saiu —
`_ADMIN_PREFIXOS` já declara `("POST", "/api/backup/")`, e duas autoridades
sobre a mesma decisão é como uma delas fica velha sem ninguém perceber. A
conferência de **máquina local** ficou: ela responde outra pergunta — "é neste
PC?" —, que papel nenhum responde.

Provado em instância isolada, com usuários de teste:

| rota | sem sessão | operador | administrador |
|---|---|---|---|
| `/api/backup/criar` | **401** | **403** | 400 "informe a frase-senha" |
| `/api/backup/inspecionar` | **401** | **403** | 400 "arquivo não encontrado" |
| `/api/backup/restaurar` | **401** | **403** | 400 "arquivo não encontrado" |
| `/api/backup/rota-que-nao-existe` | **401** | **403** | 404 |
| `GET /api/backup/situacao` | **401** | **403** | 200 |

A quarta linha é a que prova o ponto: **rota inexistente sob o prefixo já nasce
restrita** — o operador leva 403 antes de qualquer código do bloco rodar.

### Processos órfãos — a causa era genealógica

Não era o `fiscale_server` que sobrava: eram os **`runner.py`**, que ele lança
como processo próprio. `proc.terminate()` mata o filho direto, e no Windows o
**neto sobrevive**, segurando a porta interna.

| suíte | processos deixados por execução |
|---|---:|
| `teste_importar_http.py` | 2 |
| `teste_clientes_modelo.py` | 2 |

Acumulavam-se: catorze órfãos ocupando 8791, 8792, 8793, 8794.

`teste_apoio.encerrar_arvore()` encerra **os descendentes daquele PID**, do mais
fundo para o mais raso. Nunca por nome — uma varredura por "python que roda
fiscale_server" derrubaria o FISCALE do usuário, que fica o dia inteiro na
8777 — e nunca por porta, que tem a mesma armadilha no dia em que a porta
coincidir. A única identificação segura é a genealogia.

Chamada em `finally`, idempotente, e **nunca levanta**: um `finally` que estoura
esconde o erro de verdade.

Medido depois: **4 → 4 → 4** processos, e nenhuma porta 8791–8899 ocupada.

### Reexecução aninhada — resolvida na raiz

`teste_nfe6b.py` e `teste_nfe6b_ux.py` importavam `montar`, `carregar_main`,
`CONTA` e `Req` de `teste_nfe6.py` — que tem as asserções no **nível do
módulo**. Importar era executar: as 312 asserções rodavam **três vezes** por
rodada completa.

Os utilitários foram para `teste_apoio_nfe6.py`, sem asserção e sem
`__main__`. Cobertura preservada exatamente:

| suíte | antes | depois | linhas-resumo na saída |
|---|---:|---:|---:|
| `teste_nfe6.py` | 312 | **312** | 1 |
| `teste_nfe6b.py` | 125 | **125** | 1 (eram 2) |
| `teste_nfe6b_ux.py` | 190 | **190** | 1 (eram 2) |

Cada asserção executa **uma única vez**.

### `rodar_testes.py` — o runner do projeto

A contagem passou meses sendo feita por laço de shell com `grep`, e cada laço
contava diferente. Agora há um runner só, no repositório.

| ele distingue | como |
|---|---|
| **APOIO** | o arquivo declara `TESTE_APOIO = True`, lido por **AST, sem importar** |
| **SUITE** | o resto, executado como processo próprio |
| **FALHA** | código de saída ≠ 0, ou a linha-resumo acusa falha |
| **TIMEOUT** | estourou `--tempo` (padrão 900 s) |
| **SEM_RESULTADO** | rodou, saiu 0, e não imprimiu nada — não é sucesso |
| **ANINHADA** | imprimiu **mais de uma** linha-resumo; **não é somada** |

A classificação é declarada, não adivinhada: a versão anterior deduzia pelo
conteúdo ("tem `__main__`?"), e adivinhação em contagem de teste é como um
módulo de apoio vira suíte verde sem ninguém notar.

`ANINHADA` fecha a porta por onde entraram 6.053 e 5.830: em vez de escolher
uma das duas linhas-resumo e devolver um número plausível, o runner **recusa
somar** e aponta o arquivo.

Uma linha estruturada por suíte, sempre com as mesmas colunas, e um resumo
global reproduzível. Isso encerra as pendências **T1**, **T2**, **T3** e **T4**.

### Reconciliação

| | suítes | asserções |
|---|---:|---:|
| referência de entrada | 46 | 5.959 |
| **saída** | **46** | **5.959** |

`teste_apoio_nfe6.py` entrou como **módulo de apoio** (são 4 agora), não como
suíte: ele não tem asserção nenhuma, e contá-lo seria repetir o erro que esta
fase veio corrigir.

---

## Reconciliação 5.959 → 6.149 (30/08/2026, CTE 1)

| origem | asserções | o que é |
|---|---:|---|
| baseline oficial (46 suítes) | **5.959** | |
| `teste_cte1.py` | **+186** | identificação com desempate por namespace, os oito papéis, tomador pelo `toma`, entrada canônica (dedup por chave e SHA-256, conflito em quarentena), checkpoint isolado e crescente, adaptador sem consulta por chave, `cStat`, certificado nos sete estados, rotas e papéis, tela |
| `teste_ingestao4d3b.py` 117 → 121 | **+4** | a varredura de módulos pegou `cte_dfe.py`, `servico_cte.py`, `entrada_cte.py` e `credencial_cte.py` sozinha — sexta vez que essa guarda se atualiza |
| **total** | **6.149** | **47 suítes, 0 falhas** |

Módulos de apoio passaram de 4 para 5 com `teste_fixturas_cte.py`. Ele **não**
é contado como suíte: não tem asserção nenhuma, e contá-lo repetiria o erro que
a reconciliação de 29/08 corrigiu.

### Três falhas que apareceram, e o que cada uma era

Nenhuma foi afrouxamento de guarda — as três foram corrigidas **na causa**.

| suíte | o que acusou | a causa |
|---|---|---|
| `teste_senha_politica` | "o número não colide com outra decisão" | **reusei o D80**, que já era a política de senha. Renumerei as minhas para D81–D85 |
| `teste_seguranca` | "nenhuma senha escrita direto no código" | criei `SEM_SENHA = "SEM_SENHA"`, e o `SENHA` colado no `=` casa com a busca por segredo. **Renomeei a constante para `SENHA_PENDENTE`** em vez de mexer na guarda |
| `teste_apuracao2` | "`main.py` NÃO consome a normalização" | escrevi um comentário `# normalizacao de NSU`, e a guarda procura a palavra no texto. O `main.py` não importa aquele módulo; a redação é que disparou |

### E uma fragilidade de teste, consertada

`teste_nfe6` fatiava o bloco de rotas da NF-e 6 até `@app.post("/api/abrir-pasta")`.
Quando o bloco do CT-e entrou entre os dois, o código do CT-e passou a ser lido
como se fosse da NF-e 6 — e a asserção "não menciona `checkpoint`" reprovou por
um `checkpoint` que não era dela.

É o **mesmo defeito** que derrubou `teste_cadastro1` em 29/08: delimitar por
vizinho. O fim da fatia passou a ser o cabeçalho da própria seção seguinte, com
fallback — a próxima seção inserida ali não quebra o teste.

---

## CTE 1.1 — 01/09/2026

```
47 suítes executáveis · 6.206 asserções únicas · 0 falha(s) · 5 módulos de apoio · 519s
```

### Reconciliação contra as 6.149 da CTE 1

Comparação **arquivo a arquivo** dos dois `--json`, não só do total:

| arquivo | CTE 1 | CTE 1.1 | delta |
|---|---:|---:|---:|
| `teste_cte1.py` | 186 | 243 | **+57** |
| *todos os outros 46* | — | — | **0** |

`6.149 + 57 = 6.206`. **Um único arquivo mudou**, e é o da fase. Nenhuma suíte
perdeu asserção, nenhuma sumiu, o número de suítes executáveis continua **47**.

### As 57 novas, por objeto

| asserções | o que provam |
|---:|---|
| 12 | a tabela de `cStat` é **exatamente** a da NT — nada faltando, nada inventado, e a fonte declarada em todos |
| 8 | os 4 códigos herdados da NF-e (236, 578, 632, 111) **saíram**, e caem em não-documentado sem palpite |
| 11 | cada categoria vira o comportamento certo; código fora da tabela não é adivinhado |
| 10 | a distribuição **não devolve ao emitente**: dito no serviço, no adaptador, na entrada, **na tela** e no diagnóstico |
| 9 | CT-e OS, CT-e Simplificado e GTV-e são identificados, **preservados byte a byte** e deduplicam |
| 4 | fila esgotada bloqueia ≥ 1 hora, e meia hora depois ainda não libera |
| 4 | **656 exige revisão humana** — dois dias depois continua barrada, com estado `EXIGE_REVISAO` |
| 5 | `consNSU` existe só para lacuna; NSU zero é recusado; o checkpoint não anda por ela |
| 3 | a janela de 3 meses é declarada, e aponta a importação como caminho do resto |

### Uma asserção antiga mudou de expectativa (e não é regressão)

`teste_cte1.py:734` esperava `svc.BLOQUEADA` depois de um 656. Agora recebe
`svc.EXIGE_REVISAO`.

Isso é a **D88 em vigor**: na CTE 1 o 656 voltava à fila quando o relógio
passasse; pela NT ele não pode voltar sozinho. A expectativa do teste foi
atualizada porque o **comportamento correto mudou** — não para alcançar um
número.

### Guardas estruturais

- `verificar_orfas.py` — 155 `.py`, 17 `.html`, **nenhuma referência órfã**
- `montar_portatil.conferir_lista()` — **`[]`**, nada de fora da lista do portátil

---

## FASE CONTÁBIL 1 — 19/09/2026

Antes de tocar em código:

```
74 suítes executáveis · 8.153 asserções únicas · 1 falha(s) · 5 módulos de apoio
```

A falha já existia: `teste_atalho_desktop` acusava `fiscale_decisoes_nfe.py`
fora da lista do pacote portátil (commit de 18/09). Ela foi corrigida **pela
frente NF-e, em commit separado** (`e273709`) — o Contábil não incorporou essa
alteração.

Depois da fase:

```
76 suítes executáveis · 8.355 asserções únicas · 0 falha(s) · 5 módulos de apoio · 578s
```

| arquivo | antes | depois | delta |
|---|---:|---:|---:|
| `teste_contabil1.py` | — | 164 | **+164** (suíte nova) |
| `teste_contabil_http.py` | — | 37 | **+37** (suíte nova) |
| `teste_atalho_desktop.py` | 65 · 1 falha | 66 · 0 | **+1** (a asserção que falhava passou) |
| *todos os outros 73* | — | — | **0** |

`8.153 + 164 + 37 + 1 = 8.355`. Nenhuma suíte perdeu asserção.

---

## CONTÁBIL 1B — 19/09/2026

```
76 suítes executáveis · 8.387 asserções únicas · 0 falha(s) · 5 módulos de apoio · 1143s
```

| arquivo | antes | depois | delta |
|---|---:|---:|---:|
| `teste_contabil_http.py` | 69 | 69 | **+32** sobre as 37 da Contábil 1 |
| *todos os outros 75* | — | — | **0** |

`8.355 + 32 = 8.387`. Nenhuma suíte perdeu asserção, nenhuma suíte nova.

### Uma medição descartada, e por quê

A bateria "antes" desta fase foi disparada e eu **editei `web/` enquanto ela
rodava**: o `teste_contabil_http` leu a barra de módulos já sem o Contábil e
acusou 2 falhas (8.353 · 2). O número não vale — não é estado nenhum da árvore,
é a mistura de dois. O "antes" legítimo é o fechamento da CONTÁBIL 1, medido na
árvore idêntica: **76 suítes · 8.355 · 0 falhas**.

**Regra que fica:** bateria de baseline e edição de arquivo não convivem. Quem
disparar a bateria espera ela terminar antes de tocar no código.

---

## CONTÁBIL 2 — 19/09/2026

Antes: `76 suítes · 8.387 asserções · 0 falhas` (reconfirmado com a árvore
parada, sem edição durante a corrida).

```
77 suítes executáveis · 8.498 asserções únicas · 0 falha(s) · 5 módulos de apoio · 608s
```

| arquivo | antes | depois | delta |
|---|---:|---:|---:|
| `teste_contabil2.py` | — | 100 | **+100** (suíte nova) |
| `teste_contabil_http.py` | 69 | 80 | **+11** (Fiscal → Contábil pela rede) |
| *todos os outros 75* | — | — | **0** |

`8.387 + 100 + 11 = 8.498`. Nenhuma suíte perdeu asserção.
