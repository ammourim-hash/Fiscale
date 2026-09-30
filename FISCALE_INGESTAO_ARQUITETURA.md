# FISCALE — Arquitetura do Motor de Ingestão Fiscal (FASE 2)

**Etapa de projeto. Nenhum código de produção foi alterado.**
Data: 13/08/2026 · Baseline: 549 testes verdes (PORT 4 aprovada).

Este documento responde à inspeção pedida, registra o que foi **confirmado na
documentação oficial vigente**, e propõe a arquitetura para unificar a captura
de **NF-e (mod. 55)**, **CT-e (mod. 57)** e **NFS-e Nacional**.

Regra que governou a pesquisa: nenhum endpoint, namespace ou regra técnica foi
inventado. O que não pôde ser confirmado em fonte oficial está marcado
`NÃO CONFIRMADO`; o que oficialmente não existe está marcado `NÃO DISPONÍVEL`.

> Nota de honestidade: o enunciado literal das 10 perguntas da inspeção foi
> perdido na compactação do contexto. As respostas abaixo cobrem o escopo da
> inspeção conforme registrado (módulos, representação de empresa, normalização
> de CNPJ, carga de certificado, armazenamento, banco, acoplamento, checkpoints,
> situação do CT-e, uso de IA). Se alguma pergunta ficou de fora, me diga qual.

---

## 1. Inspeção — o que existe hoje

### 1.1 Módulos do backend (linhas)

| Módulo | Linhas | Papel |
|---|---:|---|
| `main.py` | 1896 | rotas FastAPI; importa 13 módulos locais |
| `classificador.py` | 811 | classificação fiscal |
| `auditor_nfe.py` | 770 | auditoria de créditos e NCM |
| `core.py` | 676 | **NFS-e Nacional (ADN)** |
| `nfe.py` | 636 | **NF-e / CT-e** |
| `prefeituras.py` | 581 | integrações municipais |
| `pdflocal.py` | 548 | geração de PDF |
| `apuracao_federal.py` | 318 | apuração |
| `vencimentos.py` | 187 | — |
| `recife.py` | 173 | download Recife |
| `painel.py` | 140 | — |
| `inscricoes.py` | 108 | — |
| `seguranca.py` | 90 | DPAPI |

**Acoplamento.** `main.py` → 13 módulos. `prefeituras` → `classificador`, `core`,
`recife`; `painel` → `classificador`, `prefeituras`; `classificador` →
`prefeituras`, `recife`; `auditor_nfe` → `nfe`; `apuracao_federal` → `core`.
Módulos-folha (sem dependência local): `core`, `nfe`, `pdflocal`, `recife`,
`seguranca`, `inscricoes`, `vencimentos`.

Isso é importante para a proposta: **`core` e `nfe` já são folhas**. O motor de
ingestão pode ser extraído sem desmontar o resto.

### 1.2 Representação de empresa — hoje são DOIS cadastros

| Arquivo | Registros | Campos |
|---|---:|---|
| `certificados.json` | 21 | `id, cnpj, nome, apelido, caminho, senha_protegida, procurador, caminho_origem` (`id == cnpj`) |
| `state_clientes.json` | 14 | `cert, certValidade, cnpj, email, id, ie, im, mun, nome, regime, tel, uf` |

São 21 contas com certificado e 14 clientes cadastrados na tela — as listas não
coincidem, e nada no código força a coincidência. **Este é o principal débito
estrutural para a ingestão multiempresa**: não há uma resposta única para "quais
empresas o motor deve varrer".

### 1.3 Normalização de CNPJ — duplicada em 5 lugares

`cnpj_publico.so_digitos`, `apuracao_federal._so_digitos`, `inscricoes._dig`,
`nfe._dig`, `saude/modelo.so_digitos`. Cinco implementações da mesma regra.

### 1.4 Certificado — PKCS#12 carregado em 3 módulos

`core.py`, `nfe.py` e `prefeituras.py`, cada um montando seu próprio
`Pkcs12Adapter` para mTLS. Três lugares para consertar quando algo muda.

### 1.5 Armazenamento por empresa

| Pasta | Arquivos | Origem |
|---|---:|---|
| `nfe/` | 23.450 | distribuição NF-e |
| `xmls/` | 6.838 | distribuição NFS-e Nacional |
| `cte/` | 59 | **importação manual de XML** |
| `nfe_importadas/` | 17 | importação manual |
| `recife/` | 10 | download prefeitura |
| `municipal/` | 3 | — |
| `danfse/` | 1 | PDF baixado do ADN |

Nomenclatura: `{NSU}-procNFe.xml` e `{NSU}-resEvento.xml` (NF-e);
`nfse-{chave}.xml` e `evento-{chave}.xml` (NFS-e); `imp-{chave}.xml` (importados).

**O sistema de arquivos é o banco de dados dos documentos.** Não há índice.

### 1.6 Banco de dados

Um único SQLite: `sessoes.db`, tabela `sessao(token_hash PK, usuario, criada_em,
expira_em, vista_em)`. Nada fiscal está em banco. (`elo_sync.db` aparece no
código mas não existe nesta instalação.)

### 1.7 Checkpoints — dois formatos incompatíveis

| Arquivo | Conteúdo | Serviço |
|---|---|---|
| `<cnpj>/estado.json` | `{"ultimoNSU", "atualizadoEm"}` | NFS-e ADN |
| `<cnpj>/nfe/estado.json` | `{"ultNSU", "maxNSU"}` | NF-e DFe |

Ambos já são **por empresa** (moram dentro da pasta do CNPJ) — isso está certo e
deve ser preservado. Mas **nenhum dos dois registra o ambiente** (produção ×
restrita). Rodar em restrita hoje contaminaria o checkpoint de produção.
Ver decisão **D29**.

### 1.8 CT-e hoje: só entra à mão

`nfe.py:241 carregar_cte()` lê `<cnpj>/cte/*.xml`, e `nfe.py:309-316` grava
`imp-{chave}.xml` quando um XML importado tem `infCte`. A rota
`GET /api/nfe/cte` (`main.py:1548`) apenas lista o que já está em disco.

**Não existe nenhum cliente de `CTeDistribuicaoDFe`.** Os 59 CT-e vieram de
importação manual. Este é o buraco que a FASE 2 fecha.

---

## 2. Fontes oficiais consultadas

Tudo abaixo foi obtido dos portais oficiais nesta sessão, não de memória.

| # | Fonte | O que confirmou |
|---|---|---|
| F1 | [Portal CT-e → Relação de Serviços Web](https://www.cte.fazenda.gov.br/portal/webServices.aspx?tipoConteudo=Wgd9RMDGh1Y=) | endpoint do AN do CT-e |
| F2 | Pacote oficial `PL_CTeDistDFe_100` (ZIP de schemas do Portal CT-e) | leiaute completo do CT-e |
| F3 | Pacote oficial `PL_NFeDistDFe_104` (ZIP de schemas do Portal NF-e) | leiaute completo da NF-e |
| F4 | [gov.br/nfse → APIs Prod. Restrita e Produção](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao) | bases de URL do ADN/SEFIN |
| F5 | [gov.br/nfse → Documentação atual](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual) | manuais e schemas XSD v1.01 |
| F6 | Manual de Contribuintes — Guia das APIs do ADN (PDF oficial) | operações disponíveis ao contribuinte |
| F7 | Resposta HTTP dos próprios endpoints ADN | exigência de mTLS |

---

## 3. Fluxo 1 — NF-e modelo 55 (JÁ IMPLEMENTADO)

Confirmado contra o pacote oficial **`PL_NFeDistDFe_104`** (F3).

- **Endpoint** (em produção no `nfe.py:28`):
  `https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx`
- **Namespace**: `http://www.portalfiscal.inf.br/nfe`
- **Consultas aceitas** (confirmadas no `distDFeInt_v1.01.xsd`):
  `distNSU/ultNSU` · `consNSU/NSU` · **`consChNFe/chNFe`**
- **Autenticação**: mTLS com o certificado A1 da empresa.

O FISCALE usa hoje apenas `distNSU`. As outras duas consultas existem
oficialmente e estão disponíveis para fechar lacunas de NSU e para reprocessar
um documento por chave.

---

## 4. Fluxo 2 — CT-e modelo 57 (A IMPLEMENTAR)

Confirmado contra o pacote oficial **`PL_CTeDistDFe_100`** (F2) e o portal (F1).

- **Endpoint (Ambiente Nacional, versão 1.00)** — F1:
  `https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx`
- **Namespace**: `http://www.portalfiscal.inf.br/cte`
- **Pacote de schemas**: `PL_CTeDistDFe_100`
- **Autenticação**: mTLS com o certificado A1 da empresa.

### 4.1 Pedido — `distDFeInt` (versão `1.00`)

```
distDFeInt versao="1.00"
├── tpAmb          1=Produção  2=Homologação
├── cUFAutor       código IBGE da UF do autor
├── CNPJ | CPF     (escolha) o interessado no DF-e
└── (escolha)
    ├── distNSU → ultNSU   (15 dígitos)
    └── consNSU → NSU      (15 dígitos)
```

### 4.2 Retorno — `retDistDFeInt`

```
retDistDFeInt
├── tpAmb, verAplic, cStat, xMotivo
├── dhResp         AAAA-MM-DDTHH:MM:SS
├── ultNSU         último NSU pesquisado
├── maxNSU         maior NSU existente no AN para o CNPJ/CPF
└── loteDistDFeInt (0..1)
    └── docZip (até 50)   base64 de conteúdo gZip
        ├── @NSU
        └── @schema       ex.: procCTe_v2.00.xsd, procEventoCTe_v2.00.xsd
```

### 4.3 Regras confirmadas (citação do schema oficial)

- **Máximo 50 documentos por lote** — `<xs:sequence maxOccurs="50">`.
- **`ultNSU = 0` não traz o histórico completo.** O schema é explícito: com zero
  ou NSU muito antigo, *"a consulta retornará unicamente as informações
  resumidas e documentos fiscais eletrônicos que tenham sido recepcionados pelo
  Ambiente Nacional nos últimos 3 meses"*.
- **`consNSU`** serve para fechar lacuna de NSU faltante.
- **NSU tem 15 dígitos** (`TNSU`: `[0-9]{15}`).
- **`docZip` é gZip + base64**, e o atributo `@schema` identifica tipo e versão
  do documento — é por ele que se decide o parser.

### 4.4 🔴 Diferença oficial entre CT-e e NF-e

**O CT-e NÃO tem consulta por chave.** O `distDFeInt` do CT-e oferece apenas
`distNSU` e `consNSU`; o equivalente da NF-e oferece também `consChNFe`.

→ `consChCTe`: **NÃO DISPONÍVEL**.

Consequência de projeto: para CT-e, a única forma de recuperar um documento é
pelo NSU. Se o checkpoint for perdido ou corrompido, não há como pedir "me
devolva a chave X" — só varrer NSU. Isso torna a **integridade do checkpoint do
CT-e mais crítica que a da NF-e**, e é a razão da decisão **D30**.

---

## 5. Fluxo 3 — NFS-e Nacional (JÁ IMPLEMENTADO, com limites)

- **Bases oficiais** (F4):
  - Produção: `https://adn.nfse.gov.br`
  - Produção restrita: `https://adn.producaorestrita.nfse.gov.br`
- **Autenticação**: mTLS. Confirmado empiricamente (F7): sem certificado, os
  endpoints — inclusive a **documentação Swagger** — respondem
  `HTTP 496 SSL Certificate Required`.
- **Schemas oficiais**: pacote `NFSe-ESQUEMAS_XSD-v1.01-20260209` (F5), com
  `DPS_v1.01.xsd`, `NFSe_v1.01.xsd`, `evento_v1.01.xsd`, `pedRegEvento_v1.01.xsd`.

### 5.1 Operações do contribuinte

O Manual de Contribuintes das APIs do ADN (F6) documenta duas operações:

| Operação | Situação no FISCALE |
|---|---|
| `GET /DFe/{NSU}` | **em uso** — `core.py:40` usa `/contribuintes/DFe/{nsu}` |
| `GET /NFSe/{ChaveAcesso}/Eventos` | **não usado** |

A rota em produção casa exatamente com a operação oficial. O retorno traz
`LoteDFe`, com `NSU`, `ChaveAcesso`, `TipoDocumento` e `ArquivoXml` (base64).

Além dessas, o FISCALE usa `GET /danfse/{chave}` (`core.py:243`), coerente com a
API DANFSE listada em F4.

### 5.2 Oportunidade identificada

`GET /NFSe/{ChaveAcesso}/Eventos` permite consultar os eventos de uma nota
específica — hoje o FISCALE só descobre eventos se eles passarem pelo fluxo de
NSU. Vale usar para conferir cancelamento/substituição de uma nota pontual.

### 5.3 NÃO DISPONÍVEL / NÃO CONFIRMADO

| Item | Situação |
|---|---|
| Consulta por intervalo de datas | **NÃO DISPONÍVEL** — a distribuição é sequencial por NSU |
| Limite de retenção equivalente aos 3 meses do CT-e/NF-e | **NÃO CONFIRMADO** — não achei enunciado oficial; não assumir |
| Tamanho máximo do `LoteDFe` | **NÃO CONFIRMADO** — o código já trata o lote como variável, o que é o comportamento seguro |
| Documentação Swagger sem certificado | **indisponível por desenho** (496) |

Nada disso deve ser contornado por portal ou scraping — vale a **D26** já
registrada.

---

## 6. Arquitetura proposta — monólito modular

Não proponho microserviços. O FISCALE roda numa máquina de escritório, distribuído
como pacote portátil (PORT 4); processo separado seria um custo sem retorno.
A proposta é **isolar o motor de ingestão como um pacote com fronteira explícita**,
mantendo tudo no mesmo processo.

```
nfse/backend/ingestao/
├── conector.py       # o contrato (ABC) que todo serviço implementa
├── checkpoint.py     # posição da varredura, isolada por empresa+serviço+ambiente
├── cofre.py          # ÚNICO ponto que abre .pfx e monta sessão mTLS
├── acervo.py         # onde o documento bruto é gravado, e como se acha depois
├── identidade.py     # normalização de CNPJ/CPF/chave — fonte única
├── conectores/
│   ├── nfe_dfe.py    # NFeDistribuicaoDFe   (migra de nfe.py)
│   ├── cte_dfe.py    # CTeDistribuicaoDFe   (NOVO)
│   └── nfse_adn.py   # ADN /contribuintes/DFe (migra de core.py)
└── parsers/
    ├── nfe55.py, cte57.py, nfse_nac.py
    └── eventos.py
```

**Por que agrupar assim:** os três fluxos têm a mesma forma — *autentica com o
certificado da empresa, pede a partir de uma posição, recebe um lote, grava o
bruto, avança a posição*. Hoje essa forma está escrita duas vezes (`nfe.py` e
`core.py`) com nomes diferentes e formatos de checkpoint incompatíveis. Escrever
o CT-e seria a terceira cópia.

### 6.1 Contrato do conector

```python
class Conector(ABC):
    servico: str      # "nfe55" | "cte57" | "nfse_nacional"
    ambiente: str     # "producao" | "restrita"

    def posicao_inicial(self) -> Posicao: ...
    def buscar(self, sessao, empresa, desde: Posicao) -> Lote: ...
    # Lote = (documentos: list[DocumentoBruto], proxima: Posicao, fim: bool)
```

O conector **não grava nada e não decide nada**. Quem grava é o `acervo`; quem
avança o checkpoint é o orquestrador, e **só depois** de o documento estar em
disco. Essa ordem é o que garante que uma queda no meio da varredura não pule
documento.

### 6.2 O que NÃO muda

Nada de portabilidade, backup, ELO ou Plano de Saúde é tocado. O acervo continua
gravando nas mesmas pastas, com os mesmos nomes — o `.fbk` continua funcionando
sem alteração.

---

## 7. Checkpoint isolado — empresa + serviço + ambiente

**Requisito seu, e ele já está parcialmente violado hoje** (§1.7: o ambiente não
entra na chave).

Chave proposta: **`(cnpj, servico, ambiente)`** — nunca global.

```
dados/<cnpj>/ingestao/<servico>.<ambiente>.json
    { "ultimo_nsu": "000000000012345",
      "max_nsu":    "000000000012400",
      "atualizado_em": "...", "versao": 1 }
```

Propriedades:

1. **Fisicamente dentro da pasta da empresa.** Um erro de código que perca o
   CNPJ não tem como escrever no checkpoint de outra empresa — o caminho não
   existe. Isolamento por estrutura, não por disciplina.
2. **Ambiente na chave.** Testar em produção restrita nunca move o ponteiro de
   produção.
3. **Migração dos dois formatos atuais** preservando o valor, e sem apagar o
   arquivo antigo antes de conferir — mesma regra da PORT 1/PORT 3.

### 7.1 Isolamento entre empresas — as três barreiras

| Barreira | Como |
|---|---|
| Credencial | cada requisição usa o `.pfx` **daquela** empresa; o AN só devolve o que é dela |
| Caminho | todo caminho nasce de `pasta_cnpj(cnpj)`; não há caminho de acervo que não passe pelo CNPJ |
| Checkpoint | por `(cnpj, servico, ambiente)`; não existe ponteiro global |

Teste obrigatório antes de aceitar a implementação: varrer duas empresas na
mesma execução e provar que nenhum arquivo de uma apareceu na pasta da outra, e
que os checkpoints avançaram independentemente.

---

## 8. Modelo de dados conceitual

Sem ORM e sem banco relacional para os documentos — o acervo em arquivos já
funciona e o `.fbk` depende dele. O que falta é um **índice**, que pode ser
reconstruído do zero a qualquer momento a partir dos XML.

```
Empresa      (cnpj*, nome, regime, uf, municipio, ie, im)
   │ 1:N
Documento    (chave*, modelo, servico, cnpj_empresa, papel,
              emitente, destinatario, dh_emissao, valor, situacao,
              nsu, arquivo, hash_sha256)
   │ 1:N
Evento       (chave_documento, tp_evento, sequencia, dh_evento, arquivo)

Checkpoint   (cnpj*, servico*, ambiente*, ultimo_nsu, max_nsu, atualizado_em)
```

- **`chave`** é a chave de acesso de 44 dígitos — identidade natural dos três
  documentos.
- **`papel`** (compra/venda/frete/NFC-e/outra) é **derivado**, não armazenado
  como verdade: já existe `_classificar_papel()` em `nfe.py`, e a regra pode
  mudar. Fica no índice, que é descartável.
- **`situacao`** vem dos eventos (cancelamento, substituição), nunca do
  documento isolado.
- **`hash_sha256`** do XML bruto: detecta corrupção silenciosa e evita
  regravação desnecessária.

**Regra de ouro:** o XML original é a verdade e é imutável. O índice é cache.
Se o índice e o XML divergirem, o XML ganha e o índice se reconstrói.

### 8.1 Empresa: unificar os dois cadastros

`certificados.json` (21) e `state_clientes.json` (14) precisam virar uma coisa
só, com o CNPJ normalizado como identidade. Enquanto forem dois, "varrer todas
as empresas" não tem resposta definida. Ver **D28**.

---

## 9. Pipeline de processamento — onde a IA entra e onde não entra

Sua regra, aplicada literalmente:

```
XML bruto
   ↓  parser determinístico      ← ElementTree; sem IA. NUNCA.
campos estruturados
   ↓  normalização               ← CNPJ, Decimal, datas; fonte única
documento normalizado
   ↓  regras                     ← classificação, créditos, papel; código explícito
resultado auditável
   ↓  IA — SOMENTE aqui
interpretação (texto livre, casos ambíguos)
```

**Proibido usar IA para extrair campo que está estruturado no XML.** `vNF`,
`CNPJ`, `chave`, `CST`, `NCM`, `dhEmi` saem do XML por caminho de elemento. Não
há ganho e há risco: um valor alucinado num campo monetário é um erro fiscal
silencioso.

Onde a IA é legítima: descrição de serviço em texto livre para sugerir código de
tributação; conciliação de nome de fornecedor grafado de formas diferentes;
explicar em português por que uma nota foi classificada de tal jeito. Sempre
**sugestão revisável**, nunca gravação automática.

---

## 10. Riscos e limites

| # | Risco | Mitigação |
|---|---|---|
| R1 | CT-e sem consulta por chave (§4.4) | checkpoint com escrita atômica e verificação; `consNSU` para lacunas |
| R2 | `ultNSU=0` só traz 3 meses | documentar; empresa nova não recupera histórico — igual à decisão já tomada para NFS-e (lançamento à mão) |
| R3 | Dois cadastros de empresa | unificar antes de varrer todas (D28) |
| R4 | Ambiente fora do checkpoint | D29, antes de qualquer teste em restrita |
| R5 | 23.450 arquivos numa pasta, sem índice | índice reconstruível; não bloqueia a FASE 2 |
| R6 | Rate limit (429) do ADN | já tratado em `core.py`; replicar no contrato do conector |
| R7 | Migrar `nfe.py`/`core.py` quebrar produção | mover sem reescrever; os 549 testes são o portão |

---

## 11. Decisões a registrar (D27–D31)

Detalhadas em `FISCALE_DECISOES_FISCAIS.md`.

| # | Decisão |
|---|---|
| D27 | Motor de ingestão como pacote interno; monólito modular, não microserviço |
| D28 | Cadastro único de empresa, CNPJ normalizado como identidade |
| D29 | Checkpoint por `(cnpj, servico, ambiente)`; ambiente obrigatório na chave |
| D30 | CT-e não tem consulta por chave — checkpoint tratado como dado crítico |
| D31 | IA só depois do parser determinístico; nunca para extrair campo do XML |

---

## 12. Fases propostas (para aprovação)

Nada começa sem sua palavra.

| Fase | Escopo | Risco |
|---|---|---|
| **ING 1** | `identidade.py` + `cofre.py`: fonte única de CNPJ e de sessão mTLS. Elimina 5 e 3 duplicações. Sem mudança de comportamento. | baixo |
| **ING 2** | `checkpoint.py` com ambiente na chave + migração dos dois formatos atuais | médio — mexe em estado de produção |
| **ING 3** | Contrato `Conector` + migração de NF-e e NFS-e para ele, **sem reescrever a lógica** | médio |
| **ING 4** | **`cte_dfe.py`** — o conector novo de CT-e | médio |
| **ING 5** | Índice reconstruível e tela unificada de documentos | baixo |

Sugiro começar pela **ING 1**: é a única sem risco de estado, e já paga sozinha
(oito duplicações removidas).

---

## 13. Pendência para sua decisão — resíduo `.pre-port3`

Verificado por busca no código, conforme pedido. **Nada em produção lê esses
arquivos.** As únicas referências:

| Local | O que faz |
|---|---|
| `fiscale_migracao.py:291` | **escreve** a cópia (`shutil.copy2`) |
| `fiscale_backup.py:88` | **exclui** do `.fbk` (`_COPIAS_DE_MIGRACAO`) |
| `backup_para_drive.bat:64` | **exclui** do envio ao Drive |
| `teste_seguranca.py:452` | **testa** que não vaza no pacote |
| `FISCALE_SEGREDOS.md` | documenta |

Nenhuma rotina de rollback existe. Nenhum `open`/`read_text` aponta para eles.

Arquivos existentes hoje (**contêm a senha do portal em texto claro**):

```
C:\Users\nineq\Fiscale\dados\state_plano.json.pre-port3                        (3.205 bytes)
C:\Users\nineq\Fiscale-backups\pre-port4-20260812\dados\state_plano.json.pre-port3  (3.205 bytes)
```

**Podem ser apagados sem quebrar nada.** Não apaguei — aguardo sua palavra.
Lembrando a ressalva já registrada: apagar não desfaz vazamento anterior; se a
senha chegou a subir ao Drive, o que resolve é **trocar a senha**, não apagar o
arquivo.

---

**FASE 2 encerrada. Nenhum código de produção foi alterado. Aguardo revisão.**
