# FISCALE — ING 3: acervo, modelo comum e pipeline

**FASE 2, Parte II — desenho. Nenhum coletor implementado.**

- **Data:** 13/08/2026
- **Continua:** `FISCALE_INGESTAO_ARQUITETURA.md` (visão geral, fluxos NF-e/CT-e/NFS-e),
  `FISCALE_ING1.md` (identidade e mTLS), `FISCALE_ING2.md` (checkpoint e motor)
- **Cobre:** os itens **A–P** do pedido de 13/08/2026 que ainda não tinham desenho

## Onde estamos

`nfse/backend/ingestao/` já existe e já resolve a **aquisição**:

| Módulo | Resolve |
|---|---|
| `identidade.py` | CNPJ/CPF normalizado — fonte única |
| `cadastro.py` | empresa unificada (`certificados.json` + `state_clientes.json`) |
| `modelo.py` | `Empresa`, `Credencial`, `Vinculo` |
| `ambiente.py` | produção / restrita, na chave de tudo |
| `credencial_estado.py` | validade, vencimento, senha ausente |
| `sessao.py` | único ponto que abre `.pfx` e monta mTLS |
| `checkpoint.py` | `(empresa, serviço, ambiente)`, avança só após persistência |
| `contratos.py` | o que cada serviço oficialmente suporta |
| `trava.py` | uma varredura por vez |
| `distribuicao.py` | `Fonte`, `Acervo`, `Lote`, `DocumentoBruto`, motor |

**Este documento desenha o que vem depois do `DocumentoBruto`:** onde ele é
preservado, como vira dado, como não duplica, como se audita e como se
reprocessa.

> A fronteira é deliberada e é o pedido principal: **captura não conhece regra
> tributária, e regra tributária não conhece portal.** `distribuicao.py` já não
> olha dentro de `conteudo`; nada abaixo pode quebrar isso.

---

## A. Modelo comum de documento fiscal

### A.1 O erro que não vamos cometer

Forçar NF-e, CT-e e NFS-e no mesmo conjunto de campos produz uma tabela com 200
colunas onde 140 são nulas, e nenhuma consulta confiável. NFS-e Nacional não tem
CFOP nem NCM; CT-e tem tomador, que NF-e não tem; NF-e tem itens com NCM/CST,
que NFS-e não tem.

### A.2 Núcleo + extensão

**Núcleo** — o que os três têm de verdade, e sobre o que toda listagem, filtro e
totalização opera:

```
Documento
  id_documento      identidade interna (ver B)
  tipo              NFE55 | NFCE65 | CTE57 | NFSE_NAC
  chave             44 dígitos, quando existir (NF-e/NFC-e/CT-e)
  numero, serie
  cnpj_empresa      a empresa do escritório a que este documento pertence
  papel             EMITENTE | DESTINATARIO | TOMADOR | REMETENTE | TERCEIRO
  emitente          Participante
  contraparte       Participante   (destinatário, tomador — conforme o tipo)
  dh_emissao        datetime com fuso
  competencia       date            (ver G — nem sempre igual à emissão)
  valor_total       Decimal
  situacao          ver D
  origem_captura    Proveniencia
  versao_schema     "4.00" | "2.00" | "1.00"
  extensao          NFe | CTe | NFSe   ← dado específico, sem perda
```

**Participante** (mesma forma para todos, e é onde mora o histórico do item):

```
Participante(identificador: Identificador,   # reusa ingestao/identidade.py
             nome, ie, im, uf, municipio, cep)
```

**Extensões** — cada uma carrega o que só ela tem, **sem inventar equivalência**:

```
NFe   itens[]: (n, cProd, xProd, NCM, CEST, CFOP, CST/CSOSN, unidade,
                quantidade, vUnit, vProd, tributos: Tributos)
      totais: ICMS, ICMSST, IPI, PIS, COFINS, FCP, DIFAL, frete, desconto
      transporte, cobranca, infAdic

CTe   modal, tipo_servico, tomador_papel, CFOP, natureza
      remetente, destinatario, expedidor, recebedor    # CT-e tem cinco papéis
      valores: vTPrest, vRec
      tributos: ICMS (CST próprio do CT-e), vBC, pICMS
      chaves_referenciadas[]        # as NF-e transportadas — ver §A.4
      documentos_anteriores[]

NFSe  codigo_servico_nacional, codigo_municipal, item_LC116, cnae
      municipio_incidencia, municipio_prestacao
      discriminacao (texto livre — é aqui, e só aqui, que a IA pode entrar)
      valores: servico, deducoes, desconto_incondicionado, base
      tributacao: ISS (retido/não), aliquota, exigibilidade, regime_especial
      retencoes: Retencoes        # ver H
      dps: (serie, numero, dh_competencia)
```

**Regra:** o núcleo é para consultar; a extensão é para conferir. Nenhum campo da
extensão é copiado para o núcleo "por conveniência" — copiar é criar duas
verdades.

### A.3 Tributos e Retenções — sempre `Decimal`, sempre com o bruto

```
Tributos(  base: Decimal, aliquota: Decimal, valor: Decimal,
           cst: str, origem_calculo: "XML" | "DERIVADO" )
Retencoes( iss, inss, irrf, pis, cofins, csll, outras: Decimal,
           bruto: dict[str, str] )   # o texto exato do XML, sem conversão
```

`bruto` existe por causa de um caso real deste escritório: `vRetCSLL` nem sempre
vem consolidado, e o código antigo descartava PIS e COFINS — R$ 989,95 em 19
notas. **Guardar o bruto ao lado do interpretado é o que permite descobrir isso
sem rebaixar tudo de novo do portal.**

### A.4 Vínculo CT-e ↔ NF-e

O CT-e traz as chaves das NF-e transportadas em `infNFe`/`infDoc`. O vínculo é
**derivado e assimétrico**: guardamos `CTe.chaves_referenciadas[]` e o índice
monta o reverso. Nunca gravamos no documento NF-e que "existe um CT-e" — isso
alteraria um documento fiscal para registrar um fato externo a ele.

Motivo prático já conhecido: frete de terceiros inflava as compras 88×. O
vínculo existe para **explicar** esse número na conferência, não para corrigi-lo
sozinho.

---

## B. Identidade e deduplicação

### B.1 Identidade por tipo

| Tipo | Identidade natural | Confiança |
|---|---|---|
| NF-e 55 / NFC-e 65 | `chave` (44 díg.) | total — é única por definição |
| CT-e 57 | `chave` (44 díg.) | total |
| **NFS-e Nacional** | **`chave` de 50 caracteres** do ADN | alta, quando presente |
| NFS-e municipal (futuro) | **não há chave nacional** | ver B.3 |

A NFS-e Nacional **tem** identificador próprio no Ambiente de Dados Nacional. O
que ela não tem é a chave de 44 dígitos da NF-e — são coisas diferentes, e tratar
como igual é o erro que este item previne.

### B.2 `id_documento` — a identidade interna

```
id_documento = sha256( tipo + "|" + identidade_natural )   [32 hex]
```

Onde `identidade_natural` é a chave quando existe. Isso dá:

- **estabilidade** — o mesmo documento gera o mesmo id em qualquer máquina, hoje
  ou daqui a três anos;
- **convergência** — API, XML importado à mão e pasta monitorada chegam ao mesmo
  id sem combinar nada entre si;
- **independência do NSU** — o mesmo documento tem NSU diferente para o emitente
  e para o destinatário, e NSU não é identidade.

### B.3 Documento sem chave — não force

Para NFS-e municipal (Recife, Olinda, Paulista, João Pessoa, Camaragibe,
Brasília) **não existe** identificador nacional. Não invente um que pareça chave.

```
id_documento = sha256( "NFSE_MUN|" + municipio_ibge + "|" + cnpj_prestador
                       + "|" + numero + "|" + serie )
```

E marque `identidade_forte = False`. Documento com identidade fraca:

- **nunca** é deduplicado silenciosamente contra outro;
- quando dois colidem, vira **alerta de possível duplicidade** para conferência
  humana, com os dois documentos lado a lado.

**Por quê:** dois municípios podem repetir número/série para o mesmo prestador
em anos diferentes. Deduplicar por engano **apaga receita** — e apagar receita
por engano é pior do que listar a mesma nota duas vezes, porque o primeiro erro
é invisível e o segundo é gritante.

### B.4 Hash de conteúdo — papel diferente de identidade

```
hash_conteudo = sha256(bytes exatos do arquivo recebido)
```

O hash **não** identifica o documento fiscal: o mesmo documento chega com bytes
diferentes por fontes diferentes (resumo vs. XML completo, com e sem `procNFe`,
espaçamento distinto). O hash serve para:

1. detectar corrupção silenciosa em disco;
2. evitar regravar bytes idênticos;
3. provar, na auditoria, que o original não mudou.

### B.5 Convergência de fontes — qual cópia prevalece

Quando o mesmo `id_documento` chega por fontes diferentes, o acervo **guarda
todas as cópias** e elege uma como canônica:

| Prioridade | Origem | Por quê |
|---|---|---|
| 1 | XML completo autorizado (`procNFe`/`procCTe`/NFS-e do ADN) | tem protocolo |
| 2 | XML completo sem protocolo | íntegro, sem prova de autorização |
| 3 | Resumo (`resNFe`/`resCTe`) | parcial por natureza |

Cópia de menor prioridade **nunca sobrescreve** a de maior. Uma cópia nova de
prioridade igual é preservada ao lado, e a divergência vira alerta — nunca
sobrescrita automática.

Este caso é real e frequente: a Distribuição DF-e entrega `resNFe` (resumo) e,
depois da manifestação, o `procNFe` completo. São dois eventos legítimos do mesmo
documento, não uma duplicata.

---

## C. Documento bruto imutável

### C.1 A regra

**O arquivo original recebido nunca é reescrito, renomeado por conteúdo, nem
"normalizado" no disco.** Tudo que é derivado mora em outro lugar e pode ser
apagado e reconstruído.

### C.2 Layout

```
dados/<identidade>/acervo/<especie>/<id[:2]>/<id_documento>/
    original.xml            bytes exatos da PRIMEIRA cópia recebida
    captura.json            proveniência — ver C.3
    copias/<hash16>.xml     cópias posteriores com conteúdo diferente
```

`<id_documento>` como nome de pasta, e não a chave: é o único identificador que
existe para os três tipos, inclusive para NFS-e municipal sem chave.

> **Correção do desenho, feita na implementação (ING 3A).** Esta seção previa
> pasta por competência (`AAAA-MM`). Implementando, o furo apareceu: **a
> competência só existe depois do parser, e o acervo grava antes** — inclusive
> XML malformado e schema desconhecido, que não têm data nenhuma. Um caminho
> que dependa do parser faz o documento **mudar de lugar** quando o parser
> evolui, quebrando exatamente a imutabilidade que este capítulo existe para
> garantir.
>
> O acervo passou a ser endereçado por conteúdo, com os dois primeiros
> caracteres do id espalhando as pastas. **Quem responde por período é o
> índice** (§G), que é reconstruível e pode ser reorganizado à vontade.

### C.3 `captura.json` — a proveniência

```json
{ "id_documento": "a3f9...",
  "hash_conteudo": "sha256:...",
  "tamanho_bytes": 12043,
  "capturado_em":  "2026-08-13T14:22:31-03:00",
  "fonte":         "nfe_dfe",
  "ambiente":      "producao",
  "cnpj_consulta": "04103256000185",
  "nsu":           "000000000012345",
  "schema":        "procNFe_v4.00.xsd",
  "prioridade":    1,
  "copias": [ { "arquivo": "original.xml", "hash": "sha256:...",
                "fonte": "nfe_dfe", "capturado_em": "..." } ] }
```

`captura.json` **é** metadado, e por isso pode ser reescrito quando uma cópia
nova chega. `original.xml` não.

### C.4 Versão do parser fica no derivado, não no original

```
dados/<cnpj>/indice/           ← 100% reconstruível, pode ser apagado
    documentos.db              SQLite: núcleo + extensão serializada
    parser_versao.json         { "nfe55": 3, "cte57": 1, "nfse_nac": 2 }
```

Quando um parser sobe de versão, o índice sabe quais documentos foram lidos por
versão antiga e reprocessa **a partir do disco**, sem tocar no portal (§L).

**Decisão:** SQLite para o índice, não JSON. Já há `sessoes.db` e o padrão está
estabelecido; a consulta por período sobre milhares de documentos precisa de
índice, e um JSON de 200 MB carregado em memória a cada tela não escala para
milhares de empresas. **O SQLite é cache: se sumir, reconstrói do XML.** É por
isso que ele pode ficar fora do `.fbk` (§M).

---

## D. Pipeline e estados

```
DESCOBERTO → BAIXADO → PRESERVADO → VALIDADO → NORMALIZADO
                                                    ↓
                        EXPORTADO ← CONFERIDO ← CLASSIFICADO
```

| Estado | Significa | Quem avança |
|---|---|---|
| `DESCOBERTO` | a fonte anunciou (resumo/NSU); ainda não temos o conteúdo | motor |
| `BAIXADO` | conteúdo em memória | motor |
| `PRESERVADO` | **em disco e confirmado durável** — só aqui o checkpoint avança | acervo |
| `VALIDADO` | é XML bem-formado, do schema declarado, chave com DV correto | validador |
| `NORMALIZADO` | núcleo + extensão preenchidos por parser determinístico | normalizador |
| `CLASSIFICADO` | operação fiscal atribuída por regra | classificador |
| `CONFERIDO` | passou pela conferência (automática e/ou humana) | conferência |
| `EXPORTADO` | entregue ao destino (Domínio, planilha, SPED) | exportador |

Estados de exceção, **paralelos e não terminais**:

| Estado | Significa |
|---|---|
| `FALHA_CAPTURA` | a fonte respondeu erro. Checkpoint **não** avança |
| `FALHA_VALIDACAO` | chegou, está preservado, mas não passa no schema |
| `FALHA_PARSER` | preservado e válido; o parser não deu conta |
| `QUARENTENA` | identidade fraca em colisão (B.3), ou divergência entre cópias |
| `IGNORADO` | fora de escopo por decisão registrada (ex.: outra empresa) |

**Duas invariantes:**

1. **`PRESERVADO` é irreversível.** Nenhum estado posterior pode apagar o
   original. `FALHA_PARSER` é problema nosso, não do documento.
2. **O estado vive no índice, não no acervo.** Reprocessar = recalcular estado a
   partir do disco. Estado gravado junto do original seria estado imutável — e
   estado precisa mudar.

---

## E. Adaptadores

```
                     ┌──────────────────────────┐
                     │   núcleo da ingestão     │
                     │  (motor, acervo, índice) │
                     └───────────▲──────────────┘
                                 │ depende só do contrato
        ┌────────────────┬───────┴────────┬──────────────────┐
     Fonte            Acervo          ParserFiscal      Autenticador
        │
   ┌────┴─────┬──────────────┬───────────────┬─────────────────┐
 nfe_dfe   cte_dfe      nfse_adn      importador_xml    pasta_monitorada
                             │
                    (futuro) ├── recife · olinda · paulista
                             ├── joao_pessoa · camaragibe · brasilia
                             └── ...
```

**O núcleo não importa Selenium, Playwright, `requests` para portal específico,
nem nome de prefeitura.** Um conector municipal que precise de navegador carrega
essa dependência **dentro dele**; se a dependência não estiver instalada, aquele
conector não carrega e os outros seguem funcionando.

**Regra de dependência, em uma linha:** `conectores/*` → `contratos` ← `núcleo`.
Ninguém no núcleo tem `import` de conector concreto; o registro é por
descoberta, e um conector quebrado é um conector ausente, não um sistema fora do ar.

**Ordem de preferência (sua regra, aplicada):** API oficial → webservice oficial
→ distribuição DF-e → exportação autorizada → terceiro isolado por adapter.
**Scraping não é opção enquanto existir serviço oficial**, e nenhum conector
deste desenho contorna CAPTCHA, MFA ou autenticação.

---

## F. Autenticação — contrato separado

```python
class Autenticador(Protocol):
    """Entrega uma sessão pronta. Nunca devolve segredo, nunca o registra."""
    def preparar(self, empresa: Empresa, ambiente: Ambiente) -> Sessao: ...
    def estado(self, empresa: Empresa) -> EstadoCredencial: ...
```

Implementações previstas:

| Implementação | Situação |
|---|---|
| `CertificadoA1` | **existe** — `sessao.py` + `credencial_estado.py` |
| `Procuracao` | interface pronta; usa o A1 do procurador com CNPJ do outorgante |
| `UsuarioSenhaPortal` | **contrato apenas.** Segredo via `fiscale_segredos` (DPAPI) |
| `TokenAPI` / `OAuth` | contrato apenas; quando alguma prefeitura oferecer |
| `A3` | **método `suporta_a3()` devolvendo `False`.** Ver decisão D-ING3-4 |

**Integração com o cofre da FASE 1, e é aqui que as duas fases se encontram:**

- senha de `.pfx` → `certificados.json.senha_protegida`, DPAPI;
- senha de portal → `fiscale_segredos.MAPA`, mesmo mecanismo, mesmo cofre;
- **nenhum segredo entra no modelo do documento fiscal**, em nenhum campo, em
  nenhuma serialização;
- log passa por `distribuicao.higienizar()`, que já corta `senha|password|token|
  secret|api_key|authorization|private_key` e blocos PEM.

O `Autenticador` recebe `Empresa` e devolve `Sessao`. **A senha não passa pela
assinatura de nenhuma função do motor.**

---

## G. Competência e período

Quatro datas, e confundi-las é erro de apuração — já aconteceu neste escritório:
nota emitida num mês com competência de outro puxou o mês inteiro para o total.

| Campo | É |
|---|---|
| `dh_emissao` | quando o documento foi emitido |
| `competencia` | a que mês pertence fiscalmente |
| `dh_entrada_saida` | circulação da mercadoria (NF-e) |
| `dh_captura` | quando **nós** obtivemos — nunca entra em apuração |

**Regra por tipo:**

- NF-e/CT-e: `competencia = mês(dh_emissao)`;
- **NFS-e Nacional: `competencia = dhCompetencia` da DPS**, que **pode diferir**
  do mês de emissão. Nota substituta é o caso clássico.

Índice:

```sql
CREATE INDEX ix_doc_comp   ON documentos(cnpj_empresa, competencia, tipo);
CREATE INDEX ix_doc_emis   ON documentos(cnpj_empresa, dh_emissao);
CREATE INDEX ix_doc_chave  ON documentos(chave);
```

Os períodos da tela (Mês Atual, Mês Passado, Ano Atual, Ano Passado,
personalizado) resolvem-se em faixa sobre `competencia` — **e a tela deve dizer
qual data está usando.** "Setembro" sem dizer se é emissão ou competência é a
origem do bug que já custou uma investigação.

---

## H. Retenções

Estruturadas **e** com o bruto ao lado (§A.3). Fontes por documento:

| Retenção | NFS-e Nacional | NF-e |
|---|---|---|
| ISS | `valores/vISSRet`, `tpRetISSQN` | — |
| INSS | `vRetINSS` | — |
| IRRF | `vRetIRRF` | `retTrib/vIRRF` |
| PIS | `vRetPIS` | `retTrib/vRetPIS` |
| COFINS | `vRetCOFINS` | `retTrib/vRetCOFINS` |
| CSLL | `vRetCSLL` | `retTrib/vRetCSLL` |
| outras | `vOutrasRet` | — |

**Nunca derivar uma retenção da outra.** `vRetCSLL` já foi tratado como
consolidado de PIS+COFINS+CSLL, e não é sempre — foi assim que R$ 989,95 sumiram
de 19 notas. Cada campo vem do seu, e o que não vier fica `None`, **não zero**.
`None` significa "o XML não informou"; `0` significa "o XML informou zero". A
apuração precisa distinguir.

---

## I. Prévia fiscal

Só o **contrato**. Nada de cálculo de DAS nesta fase.

```python
class PreviaFiscal(Protocol):
    def resumo(self, cnpj, competencia) -> ResumoCompetencia: ...

ResumoCompetencia(
    receita_bruta, por_tipo: dict, quantidade_documentos,
    cancelados, substituidos, retencoes: Retencoes,
    segregacao: dict,             # por município / atividade / anexo
    divergencias: list[Divergencia],
    lacunas: list[Lacuna],        # numeração faltando, NSU pulado
    duplicidades: list[Duplicidade],
)
```

Duas coisas que a prévia **precisa** enxergar, e que hoje ninguém enxerga:

- **documento ausente** — série com número faltando; NSU que o serviço pulou;
- **competência fora do período** — a nota emitida em agosto com competência de
  julho aparece nos dois lugares, marcada, em vez de inflar um deles em silêncio.

O motivo de a prévia existir antes do cálculo: o cálculo do DAS **já está certo**
neste sistema (conferido contra guia real, diferença zero). O que falta não é
conta — é **saber se os documentos que entraram na conta são todos os documentos.**

---

## J. Visualização individual

```python
class VisaoDocumento(Protocol):
    def detalhes(self, id_documento) -> Documento: ...
    def xml_original(self, id_documento) -> bytes: ...
    def pdf(self, id_documento) -> tuple[bytes, Literal["OFICIAL", "GERADO"]]: ...
    def historico(self, id_documento) -> list[EventoAuditoria]: ...
```

**PDF:** quando a fonte oferece a representação oficial (DANFE/DACTE/DANFSe),
usa-se ela. Quando não há, o sistema pode **gerar** a partir do XML — e o
resultado carrega, visível no próprio documento, `"Representação gerada pelo
FISCALE — não é o documento fiscal"`.

O PDF gerado é **derivado**: vai em `indice/`, não no acervo, e nunca ao lado do
`original.xml`. O XML não é tocado para gerar PDF nenhum.

---

## K. Auditoria

Trilha **append-only**, um arquivo por empresa e mês, em JSONL:

```
dados/<cnpj>/auditoria/<AAAA-MM>.jsonl
```

```json
{"em":"2026-08-13T14:22:31-03:00","ato":"CAPTURA","id_documento":"a3f9...",
 "fonte":"nfe_dfe","ambiente":"producao","nsu":"...","por":"varredura",
 "detalhe":{"hash":"sha256:..."}}
```

Atos: `CAPTURA` · `PRESERVACAO` · `VALIDACAO` · `PARSE` · `CLASSIFICACAO` ·
`SUGESTAO_IA` · `DECISAO_HUMANA` · `ALTERACAO_MANUAL` · `EXPORTACAO` · `ERRO` ·
`REPROCESSAMENTO`.

Responde às oito perguntas do pedido:

| Pergunta | Onde |
|---|---|
| de onde veio? | `CAPTURA.fonte` + `captura.json` |
| quando foi obtido? | `CAPTURA.em` |
| qual arquivo originou? | `PARSE.detalhe.arquivo` + `hash` |
| qual parser interpretou? | `PARSE.detalhe.parser` + `versao` |
| qual regra classificou? | `CLASSIFICACAO.detalhe.regra` + `versao_regra` |
| houve alteração manual? | existe `ALTERACAO_MANUAL`? |
| quem alterou? | `.por` |
| valor anterior? | `ALTERACAO_MANUAL.detalhe.antes` / `.depois` |

**JSONL append-only e não SQLite**, de propósito: append é atômico o bastante
para o caso, sobrevive a arquivo truncado (perde-se a última linha, não a
trilha), e é legível sem ferramenta — em auditoria isso vale mais que consulta
rápida. A trilha **vai** no `.fbk`; o índice não.

### K.1 Sugestão de IA — o que a trilha guarda

```json
{"ato":"SUGESTAO_IA","id_documento":"a3f9...",
 "detalhe":{"provedor":"claude","modelo":"claude-...","versao_prompt":"nfse-classif-v3",
            "entrada_hash":"sha256:...","resposta":"...","evidencias":[...],
            "confianca":{"metodo":"regras","valor":0.82,"regras_satisfeitas":9,
                         "regras_totais":11,"ambiguidades":["CNAE genérico"]},
            "decisao_humana":null}}
```

**A IA nunca escreve no documento.** Ela produz `SUGESTAO_IA`, que fica pendente
até `DECISAO_HUMANA`. Campo que existe estruturado no XML — CNPJ, chave, número,
série, data, CFOP, NCM, CST, CSOSN, base, alíquota, valor — **é lido por parser
determinístico e ponto final**; a IA não é consultada e sua resposta sobre esses
campos é descartada se vier.

**Confiança não é o modelo dizendo "95%".** O `valor` é calculado a partir de
regras satisfeitas / regras aplicáveis, penalizado por ambiguidade detectada e
por distância do padrão histórico daquele fornecedor. `metodo` registra como foi
obtido, para que um número nunca seja comparado com outro de origem diferente.

---

## L. Erros e reprocessamento

**Falha isolada não derruba lote.** O motor já separa `ErroTransitorio` de
`ErroDefinitivo`; o pipeline estende:

| Falha | Efeito |
|---|---|
| um documento não parseia | `FALHA_PARSER`, os outros seguem, checkpoint avança |
| a fonte cai no meio | lote parcial preservado, checkpoint **não** avança |
| índice corrompido | apaga e reconstrói do acervo |
| schema desconhecido | `PRESERVADO` + `QUARENTENA` — **guarda-se assim mesmo** |

O último é importante: um `procCTeOS` ou um schema novo que ainda não sabemos ler
**deve ser baixado e guardado**. O NSU não volta. Recusar o que não se entende é
perder o documento para sempre.

**Reprocessar sem rebaixar:**

```
reprocessar(cnpj, tipo=None, competencia=None, desde_versao_parser=None)
    → varre dados/<cnpj>/acervo/, relê original.xml, regrava só o índice
```

Nunca toca no portal, nunca move checkpoint, nunca escreve no acervo. É por isso
que o parser pode evoluir sem medo: **o original está guardado, e a interpretação
é descartável.**

---

## M. Portabilidade (PORT 4)

| O que | Onde | Vai no `.fbk`? |
|---|---|---|
| código do motor | `nfse/backend/ingestao/` | não — é código |
| `original.xml`, `captura.json`, eventos | `dados/<cnpj>/acervo/` | **sim** |
| trilha de auditoria | `dados/<cnpj>/auditoria/` | **sim** |
| checkpoints | `dados/<cnpj>/ingestao/` | **sim** — reiniciar do zero rebaixaria tudo |
| índice SQLite, PDF gerado | `dados/<cnpj>/indice/` | **não** — reconstrói |
| `.pfx` | `dados/certs/` | sim, **dentro do cofre AES-256-GCM** |
| senha de `.pfx` / de portal | DPAPI | **nunca** — vira "aguardando" no destino |

**Caminhos absolutos:** todo caminho nasce de `fiscale_dados.raiz()`; nenhum
módulo de ingestão guarda caminho de máquina, e o certificado é referenciado como
`certs/<arquivo>.pfx`. Isto já é conferido pelo `teste_portabilidade.py`, que
falha se aparecer `C:\Users\<alguém>` dentro dos dados.

---

## N. Segurança e LGPD

| Classe | Dado | Política |
|---|---|---|
| **Crítico** | `.pfx`, senhas, tokens | DPAPI ou cofre AES-256-GCM. Nunca em log, exceção, temporário, `.fbk` em claro ou nuvem |
| **Pessoal (LGPD)** | CPF, nome de PF, endereço, e-mail de tomador | fica no XML (não pode ser removido — é documento fiscal). **Não replicar** para índice, log ou relatório sem necessidade |
| **Sigiloso** | valores, faturamento, clientes | dado do cliente do escritório; só sai por exportação explícita |
| **Público** | CNPJ, NCM, CFOP, município | sem restrição |

Políticas:

- **Log** — `higienizar()` já corta segredo; **CPF passa a ser mascarado**
  (`***.456.789-**`). Log com CPF completo é dado pessoal fora de controle.
- **Temporários** — `limpar_temporarios.py` já existe e é o caminho.
- **Retenção** — documento fiscal: **guardar 5 anos** (prazo decadencial,
  CTN art. 173). Índice e PDF gerado: descartáveis. Trilha: acompanha o
  documento. **Nada é apagado automaticamente** — regra da casa desde a PORT 1.
- **Exclusão a pedido do titular** — não se aplica a documento fiscal
  (obrigação legal, LGPD art. 16, I). Registrar a recusa fundamentada, não apagar.

---

## O. Contratos técnicos e direção das dependências

```
        identidade · modelo · ambiente          (não dependem de ninguém)
                        ▲
        ┌───────────────┼────────────────┬──────────────┐
     Cadastro     Autenticador       Contratos      Checkpoint
                        ▲                                ▲
                     Sessao                              │
                        ▲                                │
                   ┌────┴────┐                           │
                 Fonte    FontePorChave                  │
                        ▲                                │
                   ═════╪════════ MOTOR ═════════════════╯
                        ▼
                     Acervo  →  DocumentoBruto (imutável)
                        ▼
                  ValidadorFiscal
                        ▼
                   ParserFiscal   →  Documento (núcleo + extensão)
                        ▼
                  Normalizador
                        ▼
                RepositorioFiscal  (índice; reconstruível)
                        ▼
                ClassificadorFiscal ← HistoricoFiscal
                        ▼
                    AIProvider  (só sugere; nunca escreve)
                        ▼
                     Auditoria  (transversal — todos escrevem, ninguém lê para decidir)
```

**Seta = "conhece". Ninguém conhece quem está acima.** Em particular:
`ParserFiscal` não conhece `Fonte`; `ClassificadorFiscal` não conhece portal
nem certificado; `AIProvider` não conhece acervo.

```python
class ValidadorFiscal(Protocol):
    def validar(self, bruto: DocumentoBruto) -> Validacao: ...
    # bem-formado · schema declarado · DV da chave · CNPJ da empresa confere

class ParserFiscal(Protocol):
    tipo: str
    versao: int                       # sobe quando a leitura muda (§C.4)
    def suporta(self, schema: str) -> bool: ...
    def interpretar(self, bruto: DocumentoBruto) -> Documento: ...
    # DETERMINÍSTICO. Sem rede, sem IA, sem relógio. Mesma entrada, mesma saída.

class Normalizador(Protocol):
    def normalizar(self, doc: Documento) -> Documento: ...
    # CNPJ canônico · Decimal (nunca float) · datas com fuso · competência

class RepositorioFiscal(Protocol):
    def salvar(self, doc: Documento) -> None: ...
    def obter(self, id_documento: str) -> Documento | None: ...
    def listar(self, cnpj, periodo: Periodo, tipo=None,
               situacao=None) -> Iterator[Documento]: ...
    def existe(self, id_documento: str) -> bool: ...
    def reconstruir(self, cnpj) -> Relatorio: ...

class ClassificadorFiscal(Protocol):
    versao_regras: int
    def classificar(self, doc: Documento,
                    historico: HistoricoFiscal) -> Classificacao: ...
    # Classificacao(operacao, regra_aplicada, versao_regras, evidencias,
    #               confianca: Confianca, precisa_revisao: bool)

class AIProvider(Protocol):
    nome: str; modelo: str
    def sugerir(self, pedido: PedidoIA) -> SugestaoIA: ...
    # PedidoIA leva SÓ o necessário — nunca o XML inteiro, nunca CPF,
    # nunca segredo. SugestaoIA nunca é aplicada sem DECISAO_HUMANA.
```

**`float` é proibido em valor monetário** em todo este desenho. Já existe registro
de `947.25` como float no sistema, e é semente de erro de arredondamento em
apuração.

### O.1 Histórico inteligente

```
dados/<cnpj>/historico/padroes.db
    (fornecedor, ncm, cfop, operacao) → contagem, primeira_vez, ultima_vez
```

Serve para **alertar**: "este fornecedor sempre emitiu CFOP 5102; este veio
6102". Nunca para corrigir. Documento fiscal não se altera por estatística — o
alerta vai para a conferência humana com as duas evidências lado a lado.

---

## P. Compatibilidade — reaproveitar e criar

| Existe hoje | Situação |
|---|---|
| `ingestao/` (11 módulos) | **reaproveitar integralmente** — é a camada canônica |
| `nfe.py` — Distribuição DF-e NF-e | reaproveitar a lógica; migrar para `conectores/nfe_dfe.py` |
| `core.py` — ADN NFS-e | idem, para `conectores/nfse_adn.py` |
| `auditor_nfe.py` | reaproveitar como primeiro `ValidadorFiscal` (já é read-only) |
| `classificador.py` | base do `ClassificadorFiscal` |
| `recife.py` | **não reconstruir** — vira `conectores/municipais/recife.py` |
| `pdflocal.py` | base do PDF gerado (§J) |
| `seguranca.py` / `fiscale_segredos.py` | reaproveitar — é o cofre do §F |
| `fiscale_backup.py` | ajustar só a lista de inclusão/exclusão (§M) |

| Criar | |
|---|---|
| `acervo.py` | preservação imutável + `captura.json` + convergência de cópias |
| `parsers/{nfe55,cte57,nfse_nac,eventos}.py` | determinísticos, versionados |
| `indice.py` | SQLite reconstruível |
| `auditoria.py` | JSONL append-only |
| `conectores/cte_dfe.py` | **o único conector realmente novo** |

**Nenhuma refatoração nesta fase.** O mapa acima é destino, não tarefa.

---

## Matriz fonte × tipo × autenticação

| Fonte | Tipos | Autenticação | Incremental | Situação |
|---|---|---|---|---|
| NF-e Distribuição DF-e | NFE55, eventos | A1 mTLS | NSU | **implementado** |
| NFS-e ADN (`/contribuintes/DFe`) | NFSE_NAC, eventos | A1 mTLS | NSU | **implementado** |
| CT-e Distribuição DF-e | CTE57, eventos | A1 mTLS | NSU | **a implementar** |
| Importador de XML | todos | nenhuma | — | existe; converge por `id_documento` |
| Pasta monitorada | todos | nenhuma | mtime | existe (`entrada_config.json`) |
| Recife | NFSE_MUN | portal | data | existe (`recife.py`) |
| Olinda · Paulista · J.Pessoa · Camaragibe · Brasília | NFSE_MUN | a levantar | a levantar | **não investigado** |

**Limites oficiais, e não são contornáveis:**

- **CT-e não tem consulta por chave.** Só `distNSU`/`consNSU`, lote de 50,
  retenção de 3 meses no serviço. Quem perder a janela **não recupera pelo
  serviço** — outro motivo para nunca recusar documento que não se entende (§L).
- **NF-e Distribuição DF-e traz o que a empresa **recebeu**, não o que emitiu.**
  Vendas e NFC-e entram por importador de XML. Já verificado: 0 casos de emitidas
  pela distribuição nacional.
- **NFS-e Nacional:** o ADN cobre os municípios aderentes. O que não for
  obtenível oficialmente fica marcado como **limitação**, não como endpoint a
  descobrir.

---

## Riscos

| # | Risco | Mitigação |
|---|---|---|
| R1 | Índice divergir do acervo | XML é a verdade; `reconstruir()` a qualquer momento |
| R2 | Dedup errado apagar receita (NFS-e municipal) | identidade fraca → `QUARENTENA`, nunca merge silencioso |
| R3 | Schema novo não reconhecido | preserva mesmo assim; NSU não volta |
| R4 | CT-e: janela de 3 meses | varredura precisa ser regular; alerta de checkpoint parado |
| R5 | Vazamento entre empresas | três barreiras do ING 2 + teste de duas empresas simultâneas |
| R6 | IA "corrigir" campo do XML | proibida por contrato; parser determinístico é a única fonte |
| R7 | `float` em valor | `Decimal` obrigatório; teste que rejeita `float` em serialização |
| R8 | Acervo crescer sem limite | ~50 KB/doc; 1.000 docs/mês/empresa × 22 = ~1 GB/ano. Aceitável |
| R9 | Competência confundida com emissão | campos separados; a tela declara qual usa |

---

## Decisões que precisam de você

| # | Decisão | Recomendo |
|---|---|---|
| **D-ING3-1** | Índice em **SQLite** reconstruível, fora do `.fbk` | **sim** — JSON não escala; e é cache, não verdade |
| **D-ING3-2** | Acervo por `<cnpj>/acervo/<tipo>/<AAAA-MM>/<id_documento>/` | **sim** — casa com a consulta por período |
| **D-ING3-3** | NFS-e municipal com identidade **fraca** → quarentena em vez de merge | **sim** — apagar receita é pior que duplicar |
| **D-ING3-4** | **A3 fora agora**, com `suporta_a3() → False` na interface | **sim** — A3 exige PIN interativo e sessão presa a leitor; forçar agora contamina o desenho |
| **D-ING3-5** | Auditoria em **JSONL append-only**, dentro do `.fbk` | **sim** — legível sem ferramenta, sobrevive a truncamento |
| **D-ING3-6** | PDF gerado é derivado (fica no índice), nunca ao lado do original | **sim** |
| **D-ING3-7** | Próxima fase: **ING 4 = acervo + parser NF-e 55**, antes do CT-e | **sim** — o CT-e é conector novo *e* parser novo; fazer o acervo com o fluxo já implementado reduz o que pode dar errado de duas coisas para uma |

**Fora do meu alcance, e precisa de levantamento seu:** quais mecanismos de
importação o **Domínio Sistemas** realmente aceita no seu contrato. Sem isso o
`DominioAdapter` seria chute. O desenho acima não depende dessa resposta — o
exportador é o último elo e não afeta nada acima dele.

---

**Parado aqui, como combinado.** Nenhum coletor implementado, nenhuma
funcionalidade fiscal existente alterada.
