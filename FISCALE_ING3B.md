# ING 3B — conector NF-e Distribuição de DF-e

**Implementação offline concluída e verde. Nenhuma consulta real executada.**

- **Data:** 13/08/2026
- **Escopo:** migrar o cliente de Distribuição DF-e para `conectores/nfe_dfe.py`
  implementando a `Fonte` da ING 2, e preparar a primeira integração real.
- **Fora de escopo, e não iniciado:** CT-e, NFS-e, motor fiscal, IA, agenda de
  múltiplas empresas.

---

## 1. Levantamento do `nfe.py` atual

636 linhas. O que existe hoje, e o que acontece com cada parte.

| Item | Como está | Destino |
|---|---|---|
| `URL_DIST` | produção, chumbada; **sem homologação** | migrado para `ENDPOINT` por ambiente |
| `NS_WSDL`, `NFE` | namespaces corretos | migrados |
| `_sessao(pfx, senha)` | monta `Pkcs12Adapter` recebendo **caminho e senha em claro** | **substituído** pela ING 1 (`sessao.criar_sessao`), que apaga a senha na própria função |
| `_envelope(cnpj, ult_nsu, cuf="26")` | só `distNSU`; `tpAmb` fixo em `1`; `cUF` fixo em `26` | virou três funções puras com ambiente e UF explícitos |
| `sincronizar_nfe(...)` | laço, checkpoint, HTTP, parsing e gravação **num só lugar** | dividido: laço e checkpoint são da ING 2; HTTP é `TransporteHTTPS`; parsing é `interpretar_resposta`; gravação é o acervo da ING 3A |
| `r.raise_for_status()` | qualquer HTTP ≠ 2xx vira a mesma exceção | substituído por taxonomia (§7) |
| `timeout=90` | um número só, para conectar e ler | dois timeouts distintos |
| `cStat` | só `656` e `137` tratados; o resto ignorado | tabela `CSTAT` com categorias |
| `ultNSU` / `maxNSU` | lidos com `int()`, gravados em `estado.json` próprio | `Checkpoint` da ING 2, `(identidade, serviço, ambiente)` |
| `docZip` | `except: continue` — **documento descartado em silêncio** | 🔴 corrigido, §5 |
| descompactação | `gzip.decompress(base64.b64decode(...))` | mesma, com falhas separadas e preservação |
| gravação | `<nsu>-<schema>.xml` na pasta `nfe/` | acervo imutável da ING 3A |
| `time.sleep(0.6)` | cortesia contra consumo indevido | não migrado: nesta fase há **um lote por execução manual** |
| `ConsumoIndevido` | exceção própria | migrada, agora subclasse de `ErroTransitorio` |
| `_num()` → `float` | 🔴 valor fiscal em float | não migrado; `Decimal` é obrigatório desde a ING 3A |
| `carregar_nfe`, `carregar_cte`, `importar_xmls`, `distribuir_entrada`, `processar_pasta`, `_classificar_papel`, `_eventos_cancelamento` | leitura de disco e importação manual, **usadas pela tela hoje** | **não tocadas** |

**Código legado que poderá ser removido depois da primeira consulta real
validada:** `_sessao`, `_envelope`, `sincronizar_nfe`, `ConsumoIndevido`,
`URL_DIST`, `NS_WSDL`. **Nada foi removido nesta fase** — remover antes de a
integração nova rodar em produção deixaria o escritório sem NF-e se algo desse
errado.

**Dependências com outros módulos:** `sincronizar_nfe` é chamada por
`fiscale_server.py`. A rota atual continua apontando para ela; a troca é fase
posterior, depois da consulta real aprovada.

---

## 2. Documentação oficial — o que foi confirmado, e o que não foi

**Nota sobre método, e ela importa.** Os PDFs do Portal da NF-e não são
acessíveis por ferramenta automatizada neste ambiente: `exibirArquivo.aspx`
entra em laço de redirecionamento de cookie. Foi possível confirmar por busca
com resultados do domínio oficial, mas **não** transcrever a Nota Técnica
inteira.

O que isso significa na prática: **o que não foi confirmado está marcado como
tal no código**, em `CSTAT[...].confirmado = False`, com o motivo. Não há
descrição preenchida por analogia com blog ou biblioteca de terceiros.

### Confirmado

| Item | Valor | Fonte |
|---|---|---|
| Endpoint produção | `https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx` | `contratos.py` (pacote `PL_NFeDistDFe_104`, 13/08/2026) + portais oficiais |
| Endpoint homologação | `https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx` | portais oficiais estaduais (SEFAZ-PE, SPED-MG) |
| Namespace | `http://www.portalfiscal.inf.br/nfe` | pacote oficial |
| Namespace WSDL | `.../nfe/wsdl/NFeDistribuicaoDFe` | pacote oficial |
| Versão `distDFeInt` | `1.01` | `PL_NFeDistDFe_104` |
| Consultas | `distNSU`, `consNSU`, `consChNFe` | NT 2014.002 |
| NSU | 15 dígitos | NT 2014.002 |
| `docZip` | gzip + base64; atributos `NSU` e `schema` | pacote oficial |
| Lote máximo | 50 documentos | `PL_NFeDistDFe_104` |
| Retenção | ~3 meses a partir do NSU 0 | pacote oficial |
| `cStat 138` | "Documento localizado" | NT 2014.002 |
| `cStat 137` | "Nenhum documento localizado"; `ultNSU == maxNSU` | NT 2014.002 |
| `cStat 656` | "Rejeição: Consumo Indevido"; bloqueio do CNPJ por ~1 hora | NT 2014.002 |
| Consumo indevido | nova consulta dentro de 1 h após 137; e limite de 20 consultas/hora por chave ou NSU | NT 2014.002 |

### **Não** confirmado nesta pesquisa

- a **descrição exata** dos demais `cStat` (108, 109, 111, 215, 236, 242, 252,
  280, 281, 283, 286, 404, 578, 589, 632). Estão na tabela pela **categoria**,
  que é o que governa o comportamento, e todos marcados `confirmado=False`;
- o intervalo mínimo recomendado entre consultas consecutivas bem-sucedidas;
- se existe limite diário além do horário.

**Pendência registrada:** abrir a NT 2014.002 manualmente e completar as
descrições. O teste `cStat — categorias explícitas` falha se alguém marcar um
código como confirmado sem informar a fonte.

**Referências:**
[NT 2014.002 — Web Service de Distribuição de DF-e](https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo=wLVBlKchUb4%3D) ·
[Relação de Serviços Web — Portal da NF-e](https://hom.nfe.fazenda.gov.br/portal/webServices.aspx) ·
[Web Services — SPED MG](https://portalsped.fazenda.mg.gov.br/spedmg/nfe/webservices/) ·
[Webservices Produção e Homologação — SEFAZ-PE](https://www.sefaz.pe.gov.br/Servicos/nota-fiscal-eletronica/Paginas/url-web-services-prod-homolog.aspx)

---

## 3. Contrato `Fonte` — **não foi alterado**

O conector implementa `Fonte` como ela está desde a ING 2:

```python
servico: str
ambiente: Ambiente
capacidades: frozenset
def consultar(self, desde_nsu: str) -> Lote
```

E `FontePorChave` para `consChNFe`, que é capacidade **declarada** — o CT-e não
a terá, e o motor nunca chama o que não foi declarado.

**Nenhuma insuficiência apareceu.** O único ponto que exigiu pensar foi como
reportar "certificado recusado" de forma distinta de "serviço fora do ar":
resolvido **sem** tocar no contrato, com uma exceção cujo nome o
`DistribuicaoRunner` já classifica (`distribuicao.py:328-337`). Alterar a
interface para isso teria sido adaptar o domínio à SEFAZ à força.

---

## 4. Estrutura

```
conectores/
    __init__.py        (não importa nada — importação preguiçosa, ver o arquivo)
    nfe_dfe.py
        montar_envelope_dist_nsu / _cons_nsu / _cons_chave   funções PURAS
        interpretar_resposta(bytes) -> Resposta              função PURA
        CSTAT / status_de()                                  tabela
        resposta_para_lote()                                 categoria -> comportamento
        Transporte (protocolo) / TransporteHTTPS             a única parte com TCP
        FonteNFeDistribuicaoDFe                              junta tudo
        criar(cad, identificador, ...)                       caminho de produção
```

**Por que puro e transporte separados:** o que dá errado num integrador fiscal
quase nunca é o socket — é interpretar a resposta. Com `interpretar_resposta()`
pura, `docZip` corrompido, `cStat` inesperado e SOAP truncado viram teste
barato e determinístico, sem rede.

---

## 5. 🔴 O defeito que o levantamento encontrou

`nfe.py`, linhas 93-96:

```python
try:
    xml = gzip.decompress(base64.b64decode(dz.text)).decode("utf-8", "ignore")
except Exception:
    continue                      # ← o documento sumia
```

Um `docZip` que não descompactasse era **descartado em silêncio** — e logo
depois o `ultNSU` avançava por cima dele. Como o serviço só anda para frente e
retém ~3 meses, esse documento estava **perdido para sempre**, sem nenhum
registro de que existiu.

Não é hipótese remota: base64 truncado por conexão instável é exatamente o tipo
de coisa que acontece em rede de escritório.

**Como ficou:** `docZip` que não decodifica é preservado com os bytes que
chegaram, o motivo é registrado (`base64 inválido` / `gzip inválido` / `vazio`)
e o NSU fica anotado para poder ser pedido de novo por `consNSU`. O acervo o
guarda como `DESCONHECIDO`; um reprocessamento futuro pode reinterpretá-lo.

Demonstrado: 4 `docZip`, 3 avariados, **4 `DocumentoBruto` produzidos**.

---

## 6. Identidade e checkpoint

Toda chamada carrega, explicitamente:

| | |
|---|---|
| identidade fiscal | `normalizar()` da ING 1; inválida falha **na construção** da Fonte |
| CNPJ **ou CPF** | a tag do envelope muda conforme o tipo — autônomo usa `<CPF>` |
| serviço | `NFE_DISTRIBUICAO` |
| ambiente | governa endpoint **e** `tpAmb` |
| certificado | resolvido por `cadastro` + `sessao` da ING 1 |
| autorização | `credencial_estado.avaliar()` **antes** da consulta |
| checkpoint | `(identidade, serviço, ambiente)` — nunca global |

`criar()` recusa a montagem se a credencial não estiver utilizável. Gastar uma
consulta da cota horária para descobrir que o certificado venceu é desperdício
evitável.

---

## 7. HTTP e TLS — cada falha com o seu nome

| Situação | Vira | Por quê |
|---|---|---|
| `SSLError` (handshake) | `CertificadoRecusado` | repetir não resolve; é a credencial |
| `ConnectTimeout` (15 s) | `ErroTransitorio` "ao CONECTAR" | endereço fora do ar |
| `ReadTimeout` (90 s) | `ErroTransitorio` "ao LER" | serviço lento montando o lote — distinto do anterior |
| `ConnectionError` | `ErroTransitorio` (DNS ou recusa) | `requests` não os separa; o texto original preserva a causa |
| `ProxyError` | `ErroTransitorio` | rede do escritório |
| HTTP 429 / 5xx | `ErroTransitorio` | vale tentar mais tarde |
| HTTP 401/403/495/496 | `CertificadoRecusado` | 495/496 = certificado cliente ausente ou inválido |
| HTTP 4xx outros | `ErroDefinitivo` | repetir não muda |
| corpo não-XML | `RespostaInvalida` | portal de erro, captive portal, gateway |
| SOAP Fault | `RespostaInvalida` | resposta legível dizendo que falhou ≠ resposta ilegível |
| sem `retDistDFeInt` / sem `cStat` | `RespostaInvalida` | |

A mensagem preserva a causa e passa por `higienizar()` — sem senha, sem
certificado, sem XML.

---

## 8. `cStat` — categorias, sem inventar significado

```
DOCUMENTOS_ENCONTRADOS        -> Lote com documentos
NENHUM_DOCUMENTO              -> Lote vazio (não é erro)
SERVICO_INDISPONIVEL          -> ErroTransitorio
CONSUMO_INDEVIDO              -> ConsumoIndevido (transitório, espera ~1 h)
IDENTIFICACAO_OU_AUTORIZACAO  -> CertificadoRecusado
REJEICAO                      -> ErroDefinitivo
NAO_DOCUMENTADO               -> ErroDefinitivo + o xMotivo LITERAL do serviço
```

Não há palpite por faixa numérica ("2xx é rejeição"). Código fora da tabela cai
em `NAO_DOCUMENTADO`, é tratado do jeito seguro — **não avança o checkpoint,
não repete sozinho** — e mostra o texto do serviço para quem for investigar.

O custo assumido: um código transitório ainda não catalogado será reportado
como definitivo. A troca é deliberada — nunca perder documento vale mais do que
retentar automaticamente.

---

## 9. Baselines

| Suíte | Antes | Depois |
|---|---|---|
| `teste_seguranca.py` | 116 | **116** |
| `teste_backup.py` | 141 | **141** |
| `teste_portabilidade.py` | 102 | **102** |
| `teste_saude.py` | 144 | **144** |
| `teste_ingestao.py` (ING 1) | 102 | **102** |
| `teste_ingestao2.py` (ING 2) | 173 | **173** |
| `teste_ingestao3.py` (ING 3A) | 193 | **193** |
| **`teste_ingestao3b.py`** | — | **138** |
| `teste_fiscale_sessoes.py` / `elo_sync.py` | passou | **passou** |
| **Total** | 971 | **1109 · 0 falhas** |

ING 1, ING 2 e ING 3A **intactas** — mesmos números, nenhum módulo alterado.

---

## 10. Arquivos

### Criados

| Arquivo | |
|---|---|
| `ingestao/conectores/__init__.py` | fronteira da pasta; importação preguiçosa |
| `ingestao/conectores/nfe_dfe.py` | o conector |
| `teste_ingestao3b.py` | 138 verificações, sem rede |
| `teste_fixturas_nfe.py` | fixtures fictícias compartilhadas |
| `consulta_real_nfe.py` | a consulta real controlada (§12) |
| `FISCALE_ING3B.md` | este documento |

### Alterados

| Arquivo | O quê | Por quê |
|---|---|---|
| `teste_ingestao3.py` | passou a importar as fixtures de `teste_fixturas_nfe` | §11 |

**Nada fora de `ingestao/` foi alterado.** ELO, Plano de Saúde, backup, PORT 4,
HEALTH, motor fiscal, CT-e e NFS-e não foram tocados. `nfe.py` continua
exatamente como estava.

---

## 11. Dois defeitos que os testes encontraram

**1. Uma suíte importando a outra executava a outra.** `teste_ingestao3b` fez
`import teste_ingestao3` para reaproveitar fixtures. Como as suítes são
scripts, a ING 3A rodou inteira dentro da ING 3B — e o `finally` dela restaurou
`socket.connect`, **desligando a trava de rede da suíte que a importou**. Uma
consulta real poderia ter passado despercebida no meio de um teste.

Corrigido separando dado de programa: `teste_fixturas_nfe.py` é dado.

**2. Testes de arquitetura que faziam grep em prosa.** A primeira versão da
verificação "o conector não conhece o que não é dele" procurava palavras no
arquivo inteiro e falhava por causa de comentários. Comentário não é
dependência. Reescritos sobre a árvore sintática: agora conferem o que o módulo
**importa** e o que **nomeia** — e pegaram, de brinde, que `CST` é substring de
`cStat`.

---

## 12. A consulta real — comando exato, **não executado**

Primeiro o ensaio, que **não** chama a SEFAZ:

```bash
cd "/c/Users/nineq/TESTE DE SISTEMA INTEGRADO- novo/TESTE DE SISTEMA INTEGRADO/Fiscale/fiscale" && ./nfse/.venv/Scripts/python.exe consulta_real_nfe.py --empresa 04103256000185 --ambiente homologacao
```

Ele imprime empresa mascarada, UF, serviço, ambiente, endpoint, situação do
certificado e checkpoint atual — e sai sem enviar nada.

Para a consulta real, um único lote, com confirmação digitada (`CONSULTAR`):

```bash
cd "/c/Users/nineq/TESTE DE SISTEMA INTEGRADO- novo/TESTE DE SISTEMA INTEGRADO/Fiscale/fiscale" && ./nfse/.venv/Scripts/python.exe consulta_real_nfe.py --empresa 04103256000185 --ambiente producao --lotes 1 --confirmar
```

**Recomendo a MONTE (04103256000185)** por dois motivos: o certificado dela é o
de validade mais folgada entre os do cadastro, e é a empresa cujos números já
foram conferidos contra guia real — se algo vier estranho, há com o que
comparar.

**Sobre `--ambiente homologacao` — correção de uma afirmação forte demais.**
A versão anterior deste documento dizia que a homologação "retorna `cStat 137`".
**Isso não é garantia, e não deve ser lido como tal.** Não há afirmação em fonte
oficial de que homologação devolva 137 para um CNPJ real; o ambiente pode
responder rejeição, indisponibilidade, ou até documentos de teste. O valor da
homologação é provar o caminho (TLS, certificado, SOAP, parsing) sem consumir
cota de produção — **qualquer `cStat` que volte é informação legítima**, e o
conector precisa lidar com ela sem tratar 137 como o caso normal esperado.

**O que o programa NÃO faz:** laço, agendador, tarefa do Windows, varredura de
várias empresas, retry, manifestação do destinatário, polling. Uma execução,
um lote, e para.

**Riscos da primeira execução em produção:**

1. **Cota.** O serviço bloqueia o CNPJ por ~1 hora ao responder 656. Com
   `--lotes 1` isso é improvável, mas se acontecer é só esperar.
2. **NSU do zero.** Se o checkpoint desta empresa estiver zerado neste
   ambiente, a primeira varredura pede desde o começo e o serviço devolve ~3
   meses de documentos. Com `--lotes 1` vêm no máximo 50 — e o checkpoint
   avança corretamente para continuar depois.
3. **`cStat` não catalogado.** Vai aparecer como `NAO_DOCUMENTADO` com o
   `xMotivo` literal. Não é falha do conector; é a lacuna do §2 aparecendo, e
   com a informação necessária para fechá-la.

---

## 12-R1. A consulta real — executada uma vez, em 13/08/2026

**Resultado: `cStat 656` — Consumo Indevido. Parada imediata, sem retentativa.**

| | |
|---|---|
| Horário | 13/08/2026 21:26:55 → 21:26:57 (-03:00) |
| Duração | 0,86 s |
| Ambiente | produção (`tpAmb 1`) |
| Endpoint | `https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx` |
| Empresa | `04.***.***/0001-85` |
| Consulta | `distNSU`, NSU inicial `000000000000000` |
| `cStat` | **656** (`CONSUMO_INDEVIDO`) |
| `xMotivo` | *Rejeicao: Consumo Indevido (Deve ser utilizado o ultNSU nas solicitacoes subsequentes. Tente apos 1 hora)* |
| `ultNSU` retornado | `000000000001361` |
| `maxNSU` | `000000000000000` |
| `docZip` | 0 |
| Persistidos / duplicatas / cópias menores / colisões | 0 / 0 / 0 / 0 |
| Schemas conhecidos / desconhecidos / avariados | 0 / 0 / 0 |
| Checkpoint antes → depois | `000000000000000` → `000000000000000` |

### O que funcionou

O caminho inteiro até a SEFAZ está de pé: TLS com o certificado A1 da empresa,
SOAP aceito, resposta lida, `cStat` classificado, comportamento aplicado. As
seis camadas de erro ficaram separadas — só a **camada 3 (resposta SEFAZ)**
acusou problema; HTTP/TLS, SOAP, `docZip`, persistência e parser todas limpas.

O checkpoint **não avançou**, como manda a ING 2: rejeição não é entrega.

### 🔴 A descoberta que muda o próximo passo

A SEFAZ devolveu **`ultNSU = 1361`** para uma empresa cujo checkpoint local é
zero e que **nunca rodou** Distribuição DF-e neste sistema — confirmado: não
existe `04103256000185/nfe/estado.json`, enquanto quatro outras empresas têm
(`00612340…` 216, `05678005…` 45828, `67890007…` 2383, `64567004…` 455000).

O `ultimoNSU: 1543` que existe na raiz da pasta da MONTE é do **ADN da NFS-e** —
outro serviço, outra sequência de NSU. Não tem relação.

**Duas leituras possíveis, e não tenho fonte oficial para decidir entre elas:**

1. **Outro software já consome a DF-e desta empresa** (o Domínio, ou outra
   ferramenta do escritório) e a posição 1361 é dele;
2. o `ultNSU` devolvido numa **rejeição** tem outro significado — por exemplo,
   a posição atual do estoque do Ambiente Nacional — e não é um ponteiro de
   consumo.

**O que NÃO fiz, de propósito: adotar 1361 como checkpoint.** Se a leitura (2)
estiver certa, adotar esse número **pularia 1.361 documentos** que nunca
recebemos — e, sem `consChNFe` para cada chave, muitos seriam irrecuperáveis
depois dos 3 meses de retenção. O checkpoint ficou em zero, que é o único valor
que não perde nada.

**Isto precisa ser respondido antes da próxima tentativa**, e a resposta está na
NT 2014.002 §"Rejeição 656" — o documento que não abre por ferramenta neste
ambiente (§2). É leitura manual.

### Lacuna de observabilidade encontrada

A execução **não deixou linha na trilha de auditoria**: como nenhum documento
chegou, não houve `CAPTURA` para registrar. O fato ficou gravado só no
checkpoint (`ultimo_erro`, `falhas_consecutivas: 1`, `ultima_consulta`).

Para uma trilha fiscal, "consultamos às 21:26 e fomos rejeitados" é um fato que
merece registro próprio. **Proposta para a próxima fase** (não implementada
agora, para respeitar a regra de parada): um ato `CONSULTA` na trilha, gravado
antes da chamada e completado com o `cStat` depois.

### Efeito colateral menor

Foi criado um `indice/documentos.db` vazio (65 KB) para a MONTE, porque a
indexação roda mesmo quando a aquisição falha. É inofensivo — o índice é cache,
fica fora do `.fbk` e se reconstrói. Não vale código para evitar.

---

## 13. Próximo passo — aguardando aprovação

**Parado antes da primeira chamada real, como combinado.**

Depois dela, na ordem sugerida:

1. trocar a rota do `fiscale_server.py` para o conector novo;
2. remover o código legado listado no §1;
3. completar as descrições de `cStat` a partir da NT 2014.002;
4. só então discutir agenda de múltiplas empresas — que é fase própria, com
   respeito a cota e janela.

**CT-e continua parado.**
