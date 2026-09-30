# ING 3A — acervo imutável, identidade, índice e auditoria

**Entregue e verde. Parado antes do CT-e, como combinado.**

- **Data:** 13/08/2026
- **Desenho:** `FISCALE_ING3_ACERVO_E_MODELO.md`
- **Baseline preservada:** ING 1 e ING 2 não foram reimplementadas. Os 11 módulos
  anteriores continuam como estavam — a única alteração em código existente está
  registrada em §5, com o motivo.

---

## 1. O que a fase prova

A cadeia pedida, ponta a ponta, com cada elo falhando sozinho:

```
NF-e adquirida → preservada → identificada → deduplicada
               → indexada → normalizada → auditável
```

Sem IA. Sem `DominioAdapter`. Sem PostgreSQL. Sem fila, sem Redis, sem
microserviço. Sem rede nos testes — e isso é **verificado**, não declarado: a
suíte substitui `socket.socket.connect` por uma função que levanta, e confere no
fim que nenhuma tentativa houve.

---

## 2. Baselines

| Suíte | Antes da ING 3A | Depois |
|---|---|---|
| `teste_seguranca.py` | 116 ok | **116 ok** |
| `teste_backup.py` | 141 ok | **141 ok** |
| `teste_portabilidade.py` | 102 ok | **102 ok** |
| `teste_saude.py` | 144 ok | **144 ok** |
| `teste_ingestao.py` (ING 1) | 102 ok | **102 ok** |
| `teste_ingestao2.py` (ING 2) | 173 ok | **173 ok** |
| `teste_fiscale_sessoes.py` | passou | **passou** |
| `teste_fiscale_elo_sync.py` | passou | **passou** |
| **`teste_ingestao3.py` (novo)** | — | **193 ok** |
| **Total** | 778 | **971 · 0 falhas** |

ING 1 e ING 2 **não regrediram**: mesmos números, sem alteração nos módulos.

---

## 3. Arquivos

### Criados — `nfse/backend/ingestao/`

| Arquivo | Papel |
|---|---|
| `identificacao.py` | espécie, chave + DV, `id_documento`, hash, prioridade da cópia |
| `acervo.py` | preservação imutável, `captura.json`, dedup, promoção, quarentena, verificação de integridade |
| `auditoria.py` | trilha JSONL append-only, higienizada e mascarada |
| `documento.py` | núcleo + extensões; `Decimal` obrigatório; ausente ≠ zero |
| `parsers.py` | `ParserNFe55` (nfeProc / NFe / resNFe) e `ParserEventoNFe`, versionados |
| `indice.py` | SQLite reconstruível, reconstrução e reprocessamento |
| `pipeline.py` | amarra aquisição → acervo → índice sem misturar as etapas |

### Criado — raiz

| Arquivo | |
|---|---|
| `teste_ingestao3.py` | 193 verificações |
| `FISCALE_ING3A.md` | este documento |

### Alterados

| Arquivo | O quê | Por quê |
|---|---|---|
| `ingestao/__init__.py` | exporta a ING 3 | aditivo; nada removido |
| `fiscale_backup.py` | `"indice"` em `PASTAS_FORA` | §5 |
| `FISCALE_ING3_ACERVO_E_MODELO.md` | §C.2 corrigida | §4 |

---

## 4. Correção de desenho feita na implementação

O desenho previa `acervo/<tipo>/<AAAA-MM>/<id>/`. **Não funciona**, e o motivo é
estrutural: a competência só existe depois do parser, e o acervo grava antes —
inclusive XML malformado e schema desconhecido, que não têm data. Um caminho
dependente do parser faria o documento **mudar de lugar** quando o parser
evoluísse, quebrando a imutabilidade que o acervo existe para garantir.

O acervo passou a ser endereçado por conteúdo:

```
dados/<identidade>/acervo/<especie>/<id[:2]>/<id_documento>/
```

Quem responde por período é o índice, que é reconstruível.

---

## 5. Única alteração em código existente

`fiscale_backup.py` — `"indice"` entrou em `PASTAS_FORA`.

**Por quê:** o índice é cache reconstruível. Sem essa linha ele viajaria no
`.fbk` e, pior que inflar o pacote, poderia **restaurar um índice velho por cima
de um acervo novo** — divergência silenciosa entre o que o sistema mostra e o
que ele tem.

O que **vai** no pacote: `acervo/` (a verdade), `auditoria/` (o relato),
`ingestao/` (o checkpoint — reiniciar do zero rebaixaria tudo de novo).

Coberto por teste: *Backup: acervo e auditoria viajam; o índice fica*, que
restaura numa instalação limpa, confere a trilha registro a registro e
reconstrói o índice do acervo restaurado, comparando com o de antes.

---

## 6. Decisões que a implementação fixou

### D-ING3A-1 — Evento e documento nunca compartilham identidade

```
NF-e    → sha256("NFE55|<chave>")
evento  → sha256("EVENTO_NFE|<chave>|<tpEvento>|<nSeq>")
```

Compartilham a **chave**, não o **id**. Se compartilhassem, um cancelamento
sobrescreveria a nota cancelada, ou seria descartado como duplicata dela.

### D-ING3A-2 — Três resultados distintos para "já tenho este documento"

| Situação | Resultado | O que acontece |
|---|---|---|
| bytes idênticos | `DUPLICATA` | nada é escrito. Idempotente |
| cópia **mais** completa (resumo → autorizado) | `COPIA_PROMOVIDA` | guarda ao lado, promove o canônico |
| cópia **menos** completa | `COPIA_MENOR` | guarda ao lado, não rebaixa |
| mesma prioridade, conteúdo diferente | `COLISÃO` | guarda ao lado, **quarentena** |

Em **nenhum** dos quatro o `original.xml` é reescrito. A Distribuição DF-e
entrega o resumo antes do XML completo — isso é fluxo normal, não divergência, e
tratar os dois casos igual geraria alarme falso em todo documento.

### D-ING3A-3 — Identidade fraca não deduplica

Sem chave com DV válido, o id cai no hash do conteúdo e o documento **nunca**
funde com outro. Dois documentos diferentes tratados como um apagam receita em
silêncio; o mesmo documento listado duas vezes é visível e corrigível.

### D-ING3A-4 — `dinheiro()` recusa `float` em vez de converter

`Decimal(0.1)` é `0.1000000000000000055511151231257827…`. Aceitar float aqui
espalharia o erro por toda a apuração. Quem tiver um float precisa decidir
conscientemente como convertê-lo.

No índice, valor monetário é coluna **TEXT**. `REAL` reintroduziria o float
entre gravar e ler — e `total_por_competencia()` soma em Python com `Decimal`,
não com o `SUM()` do SQLite, pelo mesmo motivo.

### D-ING3A-5 — A trilha nunca recebe bytes de documento

`bytes` em qualquer campo vira `<N bytes omitidos>`; nomes de campo com cara de
segredo viram `<omitido>`; texto passa por `higienizar()`; a identidade da
empresa é gravada **mascarada**. Verificado por teste, inclusive contra o CNPJ
completo.

---

## 7. Dois defeitos que os testes encontraram

**1. Chave de evento lida do lugar errado.** O `Id` do `infEvento` é
`ID + tpEvento(6) + chave(44) + nSeqEvento(2)`: a chave está no **meio**. Pegar
os últimos 44 dígitos devolvia um número que parecia chave, tinha o comprimento
certo e estava errado. Agora `infEvento` é excluído dessa leitura e a chave do
evento vem de `<chNFe>`, que é inequívoco.

**2. Linha truncada na trilha contaminava a seguinte.** Uma queda no meio de uma
escrita deixa a última linha sem `\n`; o próximo registro grudava nela e os
**dois** viravam uma linha ilegível — o defeito se propagava para frente. A
escrita agora fecha a linha pendente antes de acrescentar. O dano ficou contido
a um registro, que é o máximo que JSONL promete.

---

## 8. Cobertura de teste — as 18 exigências

| Exigência | Seção da suíte |
|---|---|
| mesma NF-e duas vezes | *A mesma NF-e duas vezes — idempotente* |
| mesma chave + mesmo hash | idem |
| mesma chave + hash diferente | *Mesma chave, conteúdo diferente — nunca sobrescreve* |
| documento sem chave reconhecível | *Documento sem chave reconhecível* |
| schema desconhecido | *Schema desconhecido — preservado, nunca descartado* |
| XML malformado | *XML malformado — também é preservado* |
| evento de NF-e | *Evento e cancelamento* |
| cancelamento | idem |
| `docZip` repetido | *A mesma NF-e duas vezes* (mesmo conteúdo, NSU diferente) |
| falha após aquisição, antes da persistência | *Falha DEPOIS da aquisição…* + *Falha na CONFIRMAÇÃO* |
| falha após persistência, antes do parser | *Falha DEPOIS da persistência…* |
| falha durante normalização | *Falha durante a normalização* |
| reinício do processo | *Reinício do processo — retoma de onde parou* |
| reprocessamento após parser novo | *Reprocessamento após versão nova do parser* |
| precisão monetária | *Parser — Decimal, precisão* + *O índice guarda dinheiro como TEXTO* |
| tributário ausente = `None` | *Parser — ausente != zero* |
| isolamento entre empresas | *Isolamento entre duas empresas* |
| backup com auditoria e índice reconstruído | *Backup: acervo e auditoria viajam* |

Extras que a implementação pediu: integridade (corrupção silenciosa de disco),
trilha sem vazamento, linha corrompida na trilha, fluxo completo pelo pipeline,
trava de rede conferida.

---

## 9. O que a ING 3A **não** faz

- **Não altera a situação da nota a partir do evento.** Um cancelamento é
  registrado como evento vinculado à chave; consolidar isso em "a nota está
  cancelada" é regra de apuração, e entra com o motor fiscal.
- **Não tem tela.** A validação do fluxo foi por teste; nenhuma interface nova.
- **Não lê NFC-e, CT-e nem NFS-e.** `parsers.py` cobre NF-e 55 e eventos de
  NF-e. Qualquer outra coisa é preservada como `DESCONHECIDO` e espera parser.
- **Não substitui `nfe.py` nem `core.py`.** A migração dos conectores é fase
  futura; nada existente foi tocado.

---

## 10. Estado do acervo na prática

```
dados/<identidade>/
    acervo/NFE55/d9/d9f136fe7c9256ccee54f5a4af134740/
        original.xml      ← imutável
        captura.json      ← proveniência, cópias, quarentena
        copias/<hash>.xml ← quando houve cópia divergente
    acervo/EVENTO_NFE/…
    acervo/DESCONHECIDO/… ← o que ainda não sabemos ler, guardado
    auditoria/2026-08.jsonl
    ingestao/nfe_distribuicao.producao.json    ← checkpoint (ING 2)
    indice/documentos.db                       ← cache, fora do .fbk
```

---

## 11. Próximo passo — **ING 3B, aguardando aprovação**

Sugestão de escopo, para você aprovar ou reordenar:

1. migrar o conector NF-e de `nfe.py` para `conectores/nfe_dfe.py` sobre a
   `Fonte` da ING 2 — é o primeiro contato real com a rede, e merece fase só dele;
2. consolidação de situação a partir de eventos (cancelamento → nota cancelada);
3. consulta por período na tela de diagnóstico.

**O CT-e continua parado**, como combinado.
