# APURAÇÃO 6B-0 — plano de entrada das NF-e de emissão própria

**Status:** planejamento. Nenhuma linha de código, cálculo, interface,
scheduler, checkpoint ou teste de silêncio foi alterada nesta fase.
**Baseline na abertura:** 3.059 asserções, 0 falhas, 24 suítes.

---

## 0. O problema, em uma frase

O acervo novo tem 8.059 NF-e e **nenhuma de emissão própria** (D50), porque a
única fonte que o alimenta é a Distribuição DF-e, que por definição não
devolve o que a empresa gerou (D2). A receita de venda por NF-e não tem
insumo — e `vendas.py`, pronto desde a APURAÇÃO 6, está sem consumidor por
falta de dado, não por falta de código.

Existe um canal capaz de trazer esse dado: `importar_xmls`. Ele grava numa
pasta própria, `nfe_importadas`, que **não é o acervo**. Este plano trata de
como ligar um ao outro sem criar dois repositórios concorrentes.

---

## 1. Fluxo proposto

```
   XML de NF-e (emissor da empresa, e-mail do contador, pasta vigiada)
        │
        ▼
   ┌────────────────────────────────────────────┐
   │ 1. PORTA DE ENTRADA  (staging, não acervo) │
   │    nfe_importadas/  ou  entrada/           │
   └────────────────────────────────────────────┘
        │  arquivo bruto, ainda não é fato fiscal
        ▼
   ┌────────────────────────────────────────────┐
   │ 2. VALIDAÇÃO — o portão                    │
   │    · é XML fiscal legível?                 │
   │    · emitente == empresa apurada?          │
   │    · modelo, chave, DV, protocolo          │
   │    REPROVOU → não entra, e diz por quê     │
   └────────────────────────────────────────────┘
        │  aprovado
        ▼
   ┌────────────────────────────────────────────┐
   │ 3. ACERVO NOVO  (única verdade)            │
   │    acervo.preservar(DocumentoBruto)        │
   │    · id_documento = sha256("NFE55|chave")  │
   │    · original.xml imutável                 │
   │    · captura.json + trilha de auditoria    │
   │    · dedup por identidade E por hash       │
   └────────────────────────────────────────────┘
        │
        ├─── eventos (cancelamento, CC-e) pelo MESMO portão
        │    id = sha256("EVENTO_NFE|chave|tp|seq|cOrgao")
        ▼
   ┌────────────────────────────────────────────┐
   │ 4. NORMALIZAÇÃO   normalizacao.py          │
   │    Operacao(sentido=SAIDA, natureza=       │
   │    MERCADORIA, situacao, id_documento…)    │
   └────────────────────────────────────────────┘
        ▼
   ┌────────────────────────────────────────────┐
   │ 5. CLASSIFICAÇÃO  vendas.py                │
   │    venda · transferência · devolução ·     │
   │    não-receita · INDETERMINADA             │
   └────────────────────────────────────────────┘
        ▼
   ┌────────────────────────────────────────────┐
   │ 6. RASTREABILIDADE  rastreio.py            │
   │    valor → id_documento → original.xml     │
   └────────────────────────────────────────────┘
        ▼
        7. APURAÇÃO (fase futura, ainda não autorizada)
```

**O ponto que sustenta o desenho:** `acervo.preservar()` **não depende de NSU
nem de checkpoint**. Conferido no código: `nsu` é só uma string de metadado em
`captura.json`, e quem avança checkpoint é o `DistribuicaoRunner`, não o
acervo. Portanto a importação pode gravar no MESMO acervo da distribuição sem
tocar em ponteiro nenhum — e é por isso que não precisamos de um segundo
repositório.

---

## 2. Arquivos e módulos envolvidos

### Novo — um só

| arquivo | papel |
|---|---|
| `ingestao/importacao.py` | **a porta única da importação.** Valida, monta `DocumentoBruto`, chama `acervo.preservar`, devolve relatório. Zero fórmula fiscal, zero rede. |
| `teste_apuracao6b.py` | suíte da fase |

### Reaproveitados sem alteração

| arquivo | o que já faz e serve |
|---|---|
| `ingestao/acervo.py` | `preservar()`, dedup, promoção resumo→completo→autorizado, quarentena, `original.xml` imutável |
| `ingestao/identificacao.py` | `identificar()`, `id_de_nfe()`, `id_de_evento()`, DV da chave |
| `ingestao/auditoria.py` | trilha: CAPTURA, PRESERVACAO, DEDUPLICACAO, COLISAO |
| `ingestao/parsers.py` | `ParserNFe55` |
| `ingestao/normalizacao.py` | `de_documento_nfe()` — já devolve SAIDA quando a empresa emitiu (D47) |
| `ingestao/conferencia.py` | `operacoes_nfe()` — a travessia do acervo, já extraída |
| `ingestao/vendas.py` | classificação por CFOP |
| `ingestao/rastreio.py` | caminho de volta ao XML |

### Tocados, e só na fase seguinte

| arquivo | mudança prevista |
|---|---|
| `nfe.py` → `importar_xmls` | passa a **delegar** para `ingestao/importacao.py`. A assinatura e o relatório da tela ficam iguais. |
| `main.py` `/api/nfe/importar` | nenhuma mudança de contrato; o relatório ganha campos novos |

**Não tocar:** `classificador.py`, `apuracao_federal.py`, `core.py`,
`painel.py`, `piloto_nfe.py`, scheduler, checkpoints, `servico_distribuicao.py`.

---

## 3. Validações obrigatórias

Em ordem. A primeira que reprova interrompe, e o motivo vai para o relatório e
para a trilha. **Nada reprovado é gravado no acervo.**

| # | validação | reprova quando | por quê é obrigatória |
|---|---|---|---|
| 1 | XML legível | não faz parse | lixo não vira documento |
| 2 | espécie fiscal | não é NF-e/NFC-e/evento | NFS-e e CT-e têm outro dono |
| 3 | chave presente e com **DV correto** | DV não confere | chave errada = `id_documento` errado = dedup quebrada |
| 4 | **emitente == empresa apurada** | `emit/CNPJ ≠ CNPJ` | é a validação central da fase |
| 5 | modelo declarado | mod fora de {55, 65} | ver risco R1 |
| 6 | chave × conteúdo | CNPJ dentro da chave (pos. 7–20) diferente de `emit/CNPJ`, ou modelo da chave diferente de `mod` | XML remontado ou adulterado |
| 7 | protocolo de autorização | sem `protNFe`/`cStat` 100 ou 150 | ver decisão D-b |
| 8 | não denegada | `cStat` 110, 301 ou 302 | nota denegada nunca produziu efeito |

**Sobre a validação 4, que é o coração desta fase.** Hoje `_classificar_papel()`
já compara `emit_cnpj == cnpj` e roteia para `venda`/`nfce`/`compra`/`frete`.
A regra existe e está certa. O que muda: em vez de rotular e guardar tudo na
mesma pasta, o importador **separa o destino** — emissão própria vai para o
acervo como saída; o resto continua sendo compra e já tem caminho.

Uma nota de terceiro chegando por engano **não pode** entrar como venda. O
código atual já protege o caso extremo (`papel == "outra"` não grava e reporta
o CNPJ do emitente), e essa proteção deve ser preservada, não reescrita.

---

## 4. Riscos

**R1 — NFC-e (modelo 65) é rotulada `NFE55`.**
`identificacao._RAIZ` mapeia pela tag raiz (`nfeProc`, `NFe`, `resNFe`), e a
NFC-e usa exatamente as mesmas tags. Uma NFC-e entraria no acervo com espécie
`NFE55` e id `sha256("NFE55|<chave>")`. **Não há colisão de identidade** — a
chave difere e carrega o modelo nas posições 21–22 —, mas o rótulo mente, e um
relatório que filtrar por espécie vai misturar os dois. Para uma varejista, a
NFC-e é a maior parte da receita. *Mitigação:* decidir D-a antes de importar.

**R2 — o evento de cancelamento da nota própria também não chega pela
distribuição.** É o mesmo motivo da D2. Se só a nota for importada e o
cancelamento não, uma venda cancelada **conta como receita para sempre**, em
silêncio. É o risco mais caro deste plano, porque erra para mais e não deixa
rastro. *Mitigação:* o importador aceita eventos pelo mesmo portão, e o
relatório de fechamento informa quantas notas estão **sem evento conhecido** —
que não é o mesmo que "não canceladas".

**R3 — dois repositórios.** Tratado na seção 5. Enquanto `nfe_importadas` e
acervo aceitarem escrita, existem duas verdades, e a apuração pode ler uma
enquanto a tela mostra a outra.

**R4 — importação incompleta parecendo completa.** O contador importa 3 meses,
a apuração roda, e o resultado tem cara de correto. Não existe `maxNSU` aqui:
**nada na importação manual diz "acabou"**. *Mitigação:* registrar por
competência a faixa de numeração (`nNF` mínimo e máximo por série) e apontar
os buracos. Buraco de numeração não prova omissão — pode ser inutilização —,
mas é o único sinal disponível, e é melhor que silêncio.

**R5 — inutilização e contingência.** Números inutilizados e notas emitidas em
contingência (EPEC/FS-DA) têm tratamento próprio e não estão previstos.
*Mitigação:* ficam `INDETERMINADA` com motivo, nunca receita presumida.

**R6 — importar sob a empresa errada.** Mitigado pela validação 4, que hoje já
existe e precisa continuar existindo depois da refatoração. Um teste deve
travar isso.

**R7 — a base de cálculo muda de tamanho.** Ligar a receita de venda altera o
RBT12 e pode mudar de faixa e de anexo. Não é risco da importação, é da fase
de apuração — mas nasce aqui, e por isso a etapa 7 do fluxo continua fechada.

---

## 5. Plano de migração de `nfe_importadas`

### O que existe hoje

17 XML, todos numa empresa (`01.***/0001-86`), **todos compras** de terceiros,
competência 06/2026 — e **os 17 já estão em `nfe/`** (interseção medida: 17 de
17). Não há nada de emissão própria para migrar. A migração é, portanto,
barata: o volume real é zero e o exercício é de arquitetura, não de dado.

### O princípio

`nfe_importadas` deixa de ser **repositório** e passa a ser **caixa de
entrada**. Repositório é um só: o acervo.

### Etapas

| etapa | ação | reversível? |
|---|---|---|
| M1 | `importacao.py` passa a gravar no acervo. `importar_xmls` continua gravando em `nfe_importadas` **também**. Dupla escrita, leitura ainda pela pasta antiga. | sim |
| M2 | Varredura dos XML já em `nfe_importadas` para o acervo, via o mesmo portão. Dedup e validação valem igual. **Nenhum arquivo é apagado.** | sim |
| M3 | Conferência: todo documento da pasta antiga tem `id_documento` no acervo, e os totais batem documento a documento. | — |
| M4 | Os leitores viram para o acervo: `carregar_notas`, `auditor_nfe.py`, `inscricoes.py`, `main.py:1734`. | sim |
| M5 | `importar_xmls` para de gravar em `nfe_importadas`. A pasta fica **congelada, não apagada** — como o `nfe/` legado. | sim |

**M0, antes de tudo:** um teste que prove que os 17 XML de hoje atravessam o
portão e chegam ao acervo com `id_documento` estável e sem duplicar o que a
distribuição já trouxe. Sem esse teste, M1 não começa.

**Nunca:** apagar `nfe_importadas`, `nfe/` ou qualquer XML original. O acervo
é aditivo; a origem se preserva. É a mesma regra da PORT 1.

---

## 6. O que ainda depende de decisão

**D-a · NFC-e (modelo 65) entra nesta fase?**
Se entrar, é preciso decidir se ganha espécie própria (`NFE65`) — o que muda
`identificacao.ESPECIES` e cria migração de rótulo — ou se convive sob `NFE55`
com o modelo lido da chave. *Recomendo:* espécie própria, e **fora da 6B**;
importar NF-e 55 primeiro, com o acervo pequeno e o erro barato.

**D-b · Nota sem protocolo de autorização: entra?**
*Recomendo:* não entra como receita, mas **é preservada** com situação
`INDETERMINADA` e motivo. Recusar a guardar é perder documento; contar como
receita é afirmar o que o XML não afirma.

**D-c · Por onde o contador importa?**
Três portas existem: `importar_xmls` (uma empresa), `distribuir_entrada`
(várias, pelo CNPJ) e `processar_pasta` (pasta vigiada). *Recomendo:* as três
continuam, e todas passam pelo mesmo portão — a porta é da interface, o portão
é um só. Foi assim que a ING 4D-3B resolveu a distribuição.

**D-d · Quem manda quando acervo e `nfe_importadas` divergirem, durante M1–M4?**
*Recomendo:* o acervo, sempre; a pasta antiga vira só leitura de conferência.

**D-e · O que fazer com nota emitida e depois cancelada, sem o evento?**
Ver R2. *Recomendo:* relatório de fechamento que diga quantas notas estão sem
evento conhecido, e que essa contagem apareça junto do total apurado — não
escondida num log.

**D-f · Devolução reduz a base?**
É regra fiscal, não é desta fase, e `vendas.py` de propósito não a aplica.
Precisa de decisão do contador antes da apuração.

**D-g · Até que competência importar?**
Afeta RBT12 e, portanto, a faixa do Simples. Decisão do contador.

---

## Encerramento

Planejamento apenas. Nada implementado, nada migrado, nada calculado.
A etapa 7 do fluxo — apuração — permanece fechada até autorização explícita.
