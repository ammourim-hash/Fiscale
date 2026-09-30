# Situação fiscal do contribuinte — diagnóstico técnico

*Levantado em 07/09/2026. Cobre certidões (CND) e extratos de débito nas três
esferas: Federal (RFB/PGFN), Estadual (SEFAZ-PE) e Municipal (Recife).*

Este documento existe para que a próxima pessoa — inclusive nós, daqui a seis
meses — não repita as três investigações que deram origem a ele. Ele registra o
que **existe**, o que **não existe**, e o que **foi medido** em vez de suposto.

---

## 1. Três premissas que o código não sustenta

### 1.1 Não há camada e-CAC para reaproveitar

A tela de e-CAC **foi removida** na FISCAL AI 1. Não é resíduo: o
`verificar_orfas.py` reprova a build se qualquer arquivo voltar a importar
`ecac`. O `apuracao_federal.py` apura PIS/COFINS "para alimentar o MIT
(DCTFWeb)" — ele produz números que uma pessoa digita, e não conversa com
ninguém.

O `ingestao/sessao.py` — a única fábrica de sessão mTLS por certificado — fala
**SOAP com a SEFAZ**. O e-CAC não é isso: é sessão de navegador autenticada
pelo gov.br, sem API documentada. Ele não serve aqui.

### 1.2 O Integra Contador não emite CND

Catálogo conferido em 07/09/2026 (44 serviços): Integra-SN, Integra-MEI,
Integra-DCTFWeb, Procurações, Sicalc, Caixa Postal, Pagamento, Parcelamentos,
Redesim, e-Processo, **Integra-SITFIS**. **Não há serviço de CND.**

O mais próximo é o **SITFIS** (`idSistema: SITFIS`, serviços
`SOLICITARPROTOCOLO91` e `RELATORIOSITFIS92`) — Relatório de Situação Fiscal,
que é o extrato de pendências. É relatório, **não é certidão**, e não substitui
CND para licitação, empréstimo ou contrato.

### 1.3 A CND federal é outro produto, e outro contrato

A **API Consulta CND** é produto SERPRO separado, com credencial e
**bilhetagem por chamada** próprias.

    POST https://gateway.apiserpro.serpro.gov.br/consulta-cnd/v1/certidao
    Auth: OAuth2 Bearer (consumerKey:consumerSecret -> /token)
          NÃO é mTLS com .pfx.

**O escritório tem só o Integra Contador.** Portanto **não existe canal
automático para a CND hoje** — a aquisição da certidão é assistida até que se
contrate a Consulta CND, o que é decisão comercial.

Ordem de grandeza para essa decisão: a certidão vale ~180 dias, então são
~2 chamadas por empresa por ano. Com 19 empresas, ~40 chamadas anuais na esfera
federal. O preço por chamada não foi levantado.

---

## 2. O que cada esfera entrega

| Esfera | Automatizável hoje | Fora de alcance |
|---|---|---|
| **Federal** | **SITFIS** via Integra Contador — o extrato de pendências RFB/PGFN | e-CAC direto (sem API). CND (outro contrato) |
| **Estadual (SEFAZ-PE)** | **Certidão de Regularidade Fiscal**: consulta pública, **sem login** | O extrato **valorado** de ICMS/multas, que exige sessão do e-Fisco com certificado. Sem API |
| **Municipal (Recife)** | Nada oficial | CND e extrato ficam no *Recife em Dia*, por CNPJ+CIM, só web |

**A distinção que muda o produto:** na esfera estadual, o que se obtém sem
credencial é **regularidade** (sim/não), não a lista valorada de débitos. Um
painel que prometa "valor total em aberto" nas três esferas promete algo que só
o Federal e o canal assistido entregam.

**Atenção a um engano fácil:** o `nfse/backend/recife.py` é web service de
**NFS-e** (ABRASF, consulta de notas emitidas, mTLS). Ele **não** serve para
débito nem para certidão municipal. Reaproveitá-lo para isso é caminho morto.

### Sobre scraping

Não se constrói contorno de CAPTCHA. Automatizar sessão autenticada em portal
de governo é tecnicamente possível e o dado é do próprio escritório, mas quebra
em silêncio a cada mudança de layout e exige conferir os termos de uso de cada
portal antes de virar código. Não é base para o módulo.

Existe um terceiro caminho: agregadores comerciais publicam API para as três
consultas. O preço é que **CNPJ de clientes e certidões passam por um
terceiro** — decisão do escritório, registrada aqui como opção, não como
recomendação.

---

## 3. O que a captura real ensinou (e a documentação não)

O ambiente de demonstração da Consulta CND é gratuito, tem token público e só
aceita contribuintes fictícios. Foram colhidas 16 respostas e 9 PDFs.

**O swagger mente em dois campos:**

| Documentado | Real |
|---|---|
| `Messagem` | `Mensagem` |
| `TipoCertidão` (com acento) | `TipoCertidao` (sem) |

Quem escrever o parser pelo papel recebe `None` nos dois, em silêncio, sempre.

**`Status` e `TipoCertidao` são eixos independentes**, e a amostra esconde isso:
em 8 dos 9 casos com certidão eles coincidem. Só um caso quebra a correlação
(`Status=2` "Emitida" com `TipoCertidao=1` "Negativa"). Um parser escrito a
partir dos dois primeiros contribuintes trataria os dois como o mesmo campo, e
funcionaria até a primeira certidão **negativa emitida na hora** — que é o caso
mais comum de todos.

    Status  1 Encontrada · 2 Emitida · 3 Não emitida (HTTP 200, SEM bloco Certidao)
            8 não cadastrado (404) · 10 tipo inválido (400) · 12 código inválido (400)
            + HTTP 429 por limite de taxa

O `9` que a documentação lista nunca apareceu; `10`, `12` e o `429` não estão
documentados. O 429 bateu em 6 das 16 primeiras chamadas e sumiu com 12s de
intervalo — **o conector precisa nascer com recuo**.

    TipoCertidao  1 = NEGATIVA
                  2 = POSITIVA COM EFEITOS DE NEGATIVA (CPD-EN)

Confirmado pelo texto dos PDFs, não pelo número.

**Três regras que os dados impuseram:**

1. **Nunca calcular a validade.** Uma amostra deu exatamente 180 dias; outra
   deu **194**. Calcular `emissão + 180` mostraria a certidão vencida duas
   semanas antes da hora. Grava-se o `DataValidade` que veio.
2. **Nunca arquivar pelo campo devolvido.** Consulta a `A0000177000110`
   devolveu `ContribuinteCertidao = "00000000000002"`. Comparar com o que se
   pediu e **recusar quando divergir** — senão a certidão de uma empresa vai
   parar na pasta de outra.
3. **`GerarCertidaoPdf: false` devolve `DocumentoPdf: ""`** — string vazia, não
   campo ausente. Todo o resto vem idêntico.

---

## 4. A arquitetura: um envelope, não dois

Certidão e extrato têm a mesma forma — documento oficial + esfera + instante +
hash + proveniência. Construir `certidoes/` e depois `extratos/` produziria dois
armazenamentos, dois índices, dois portões e duas telas: dois lugares para
consertar cada defeito.

    situacao/
      modelo.py         esferas, tipos (CERTIDAO | EXTRATO), derivação de estado
      armazenamento.py  <raiz>/<identidade>/situacao/<esfera>/<tipo>/<id[:2]>/<id>/
                            original.pdf   bytes exatos, jamais reescritos
                            captura.json   origem, quando, sha256, resposta bruta
      indice.py         SQLite do escritório, reconstruível do disco
      importacao.py     portão único: valida, deduplica, grava, indexa, audita
      fontes/           assistida.py · sitfis.py — só entregam bytes

**Endereçado pelo hash, nunca pelo parser.** Lição que o `acervo.py` já pagou:
caminho que depende de leitura faz o documento mudar de lugar quando o parser
evolui, e quebra a imutabilidade que o armazenamento existe para garantir. O
código de controle é a chave natural, mas ele só existe depois de ler — então
ele mora no índice, não no caminho.

**A diferença que não pode ser achatada:** certidão tem **validade**; extrato é
**retrato de um instante**. Certidão deriva `válida / vencida`; extrato deriva
`apurado em <data>` e envelhece por política, não por vencimento. Forçar um
modelo no outro produz tela que mente em uma das duas metades.

**Estado é derivado, nunca gravado.** "Vencida" é função da validade e da data
de hoje; uma linha gravada como "Válida" passa a mentir na virada do dia.

**Certidão e tentativa são tabelas separadas.** "Não emitida" (Status 3) é
desfecho legítimo de negócio, com HTTP 200 e sem documento. Se `ERRO` morasse
na mesma tabela, toda falha criaria uma certidão fantasma. A tentativa nasce
sempre; a certidão só nasce com documento no disco.

**Guardar antes de ler.** O painel mostra `REGULAR / COM PENDÊNCIA / NÃO SEI`
desde o primeiro dia; o **valor total** só aparece na esfera cujo parser foi
calibrado contra documento real. Total que o parser chutou é pior que total
ausente.

---

## 5. Testes: dublê alimentado por captura, nunca por documentação

O caso `Messagem`/`Mensagem` acima é a razão. Um dublê montado a partir do
swagger teria passado verde e o conector devolveria mensagem vazia em produção,
para sempre, sem ninguém notar.

Por esfera, o dublê cobre: o caso regular (zerado), o caso com débito, e os
feios — HTML de erro no lugar do PDF, portal fora do ar, PDF truncado,
documento já vencido na origem, e o limite de taxa.

---

## 6. Pendências que travam as próximas fases

1. **Confirmar que o contrato do Integra Contador inclui o módulo SITFIS.** O
   catálogo ter o serviço não significa que o plano contratado o inclua.
2. **Confirmar as procurações eletrônicas** das empresas para o escritório
   (`AUTENTICAPROCURADOR`). Sem elas o SITFIS não roda para ninguém.
3. **Preço da Consulta CND** para ~40 chamadas/ano, se a emissão automática de
   certidão federal for desejada.
4. **Capturar resposta real do SITFIS** antes de escrever o parser dele. A
   regra da seção 5 vale para ele igual.

---

## Fontes

- Catálogo Integra Contador — <https://apicenter.estaleiro.serpro.gov.br/documentacao/api-integra-contador/pt/catalogo_de_servicos/>
- API Consulta CND — <https://apicenter.estaleiro.serpro.gov.br/documentacao/consulta-cnd/pt/introducao/>
- Portal de Atendimento SEFAZ-PE — <https://www.sefaz.pe.gov.br/Servicos/Paginas/portal_atendimento.aspx>
- Certidões, Recife em Dia — <https://recifeemdia.recife.pe.gov.br/certidoes>


---

## 7. Central de regularidade — Federal + Recife (12/09/2026)

> **Parcialmente superada pela seção 8:** o escopo municipal (Recife) foi
> cancelado no mesmo dia. O que segue sobre o Recife é registro da
> investigação, não descrição do sistema atual.

Checkpoint de retorno: tag git `checkpoint-pre-central-regularidade`. A
migração do índice é só aditiva: o código anterior lê o banco novo.

### 7.1 Portais investigados, e o que cada um permite

| Serviço | Canal oficial | Automatizável hoje? | Por quê |
|---|---|---|---|
| **SITFIS** — Relatório de Situação Fiscal (RFB/PGFN) | Integra Contador `SOLICITARPROTOCOLO91` + `RELATORIOSITFIS92` | **Sim, com contrato** | API oficial. Exige contrato SERPRO (consumerKey/secret), e-CNPJ do contratante no `/authenticate` e **procuração eletrônica** do contribuinte para quem assina. Devolve **só PDF em base64** — nenhum dado estruturado de pendência |
| Diagnóstico Fiscal / "Consulta Pendência – Situação Fiscal" (e-CAC) | e-CAC, login gov.br ou certificado | **Não** | Sessão de navegador, sem API. Fluxo assistido: abrir e-CAC, baixar o PDF, guardar |
| Certidão RFB/PGFN (CND/CPD-EN) | API **Consulta CND** (SERPRO, bilhetada) ou portal `servicos.receitafederal.gov.br/servico/certidoes/` | **Só com outro contrato** | O escritório não tem a Consulta CND. O portal é SPA — CAPTCHA não pôde ser conferido por ferramenta; tratado como assistido |
| Extrato de Débitos Mercantis (Recife) | `recifeemdia.recife.pe.gov.br/extratoDebitos/2` | **Não** | **CAPTCHA de imagem próprio** (`/captcha/showImage?namespace=login`). Nenhuma API pública |
| CND PJ com cadastro (Recife) | `/emissaoCertidao/4` (CIM + CNPJ) | **Não** | CAPTCHA (`namespace=formulario_emissao_pdf`) — o nome indica que a saída é PDF |
| CND PJ sem cadastro (Recife) | `/emissaoCertidao/29` | **Não** | mesmo portal, mesma forma |
| Extrato de Vínculos Tributários (Recife) | `/extratoInscricoes` | **Não** | Redireciona para login OpenID (`login.recife.pe.gov.br`, Keycloak). Exige conta pessoal — o Fiscale não guarda senha de portal |
| Débito Protestado / CDA (Recife) | `/relatorioCda` | **Não** | CAPTCHA (`namespace=gerar_relatorio_cda_pdf`); pede o nº do título, não o CNPJ |
| API de certidão do Recife | Emprel "SFCN" | **Não é para terceiros** | Catálogo da Emprel: público-alvo "Secretarias / Órgãos da Prefeitura do Recife" |

**As quatro perguntas sobre o Recife, respondidas:** (1) API oficial — não
encontrada; (2) endpoint sem CAPTCHA para uso autorizado — não; (3) acesso
institucional por certificado/procuração — não encontrado (a área logada é por
conta pessoal); (4) outra rota oficial — o **DTe** (domicílio tributário
eletrônico) existe em domínio próprio, sem API documentada. Agregadores
comerciais vendem essa consulta; isso poria CNPJ de cliente num terceiro e não
foi adotado (decisão do escritório, ver §2).

**CAPTCHA não é contornado.** A tela manda resolvê-lo no próprio portal.

### 7.2 Estado real desta máquina, medido

- `situacao.db`: **0 documentos**. Duas tentativas SITFIS em 09/09 para a
  ANDRADE: `ERRO` e `RECUSADA` ("falta o token… obtido do
  consumerKey/consumerSecret").
- `situacao_credenciais.json`: ambiente `producao`, contratante MONTE,
  procurador pessoa física, **só `jwt_token`** no cofre — **faltam
  consumerKey e consumerSecret**. É por isso que nenhuma consulta federal
  automática funciona hoje, e a tela diz isso em vez de mostrar verde.
- Cadastro: 20 clientes; **12 do Recife**, 9 com inscrição no campo `im`;
  sem inscrição: TRANSPORTADORA TRX, JOAO RICARDO e o CPF do Fulano.

### 7.3 Arquitetura — o que foi reaproveitado e o que nasceu

Reaproveitado sem mudança de contrato: envelope imutável (`armazenamento`),
portão único (`importacao`), conector SITFIS com OAuth mintado (`ciclo`,
`autenticacao`), cofre DPAPI (`credencial` via `seguranca.py`), bandeja com
expectativa, dossiê white-label, papéis no proxy.

Novo, e só isto:

    situacao/regularidade.py   A REGRA ÚNICA: status por esfera + consolidado. Puro.
    situacao/interpretacao.py  conclusão humana, em arquivo ao lado do original
    situacao/carteira.py       uma linha por empresa, contadores, histórico
    situacao/indice.py         v2: +sem_pendencias, +usuario/metodo/sha256, +interpretacao
    situacao/fontes/sitfis.py  202/204 do /Emitir com repetição limitada (documentado, não capturado)
    fiscale_server.py          GET carteira/empresa · POST classificar/consultar · documento com X-Paginas/X-Sha256/baixar
    web/situacao.html          carteira + detalhe + visualizador (bandeja e guarda preservadas)

### 7.4 Três camadas, separadas como pedido

| Camada | Onde mora | Muda? |
|---|---|---|
| **Documento original** | `<ident>/situacao/<esfera>/<tipo>/<id>/original.pdf` + `captura.json` (SHA-256) | **nunca** |
| **Resultado da consulta** | `situacao.db → tentativa` (empresa, esfera, tipo, quando, desfecho, detalhe, origem, **usuário, método, hash**) | só acrescenta |
| **Interpretação** | `<pasta do documento>/interpretacoes/*.json`, espelhada em `situacao.db → interpretacao` | só acrescenta; reclassificar **sucede** |

Os campos pedidos (`situacao_fiscal`) existem, distribuídos pela camada a que
pertencem: `empresa_id/fonte/tipo_consulta/data_consulta/origem/usuario/
mensagem` → tentativa; `documento_pdf/hash_documento/validade/codigo_externo/
data_referencia (apurado_em)` → documento; `status/tem_pendencia/
quantidade_pendencias/valor_total` → interpretação (ou leitor). Uma tabela
plana única misturaria exatamente o que se pediu para separar.

### 7.5 A regra de status

Por esfera: `REGULAR · PENDENCIA · CERTIDAO_POSITIVA_COM_EFEITOS_DE_NEGATIVA ·
CERTIDAO_POSITIVA · NAO_CONSULTADO · CONSULTA_EXPIRADA · ERRO_DE_ACESSO ·
ACESSO_REQUER_REPRESENTACAO · ACESSO_REQUER_INTERACAO_DO_USUARIO ·
NAO_APLICAVEL`.

1. **Falha nunca é regular.** RECUSADA/INDISPONIVEL/ERRO/ILEGIVEL sem
   documento → erro de acesso (ou "requer representação" se não há procurador).
2. `NAO_EMITIDA` (o órgão recusou emitir por pendência) → PENDÊNCIA.
3. **O documento mais recente responde**, não o mais favorável.
4. Conclusão: interpretação humana daquele documento > leitor. O leitor só
   afirma o observado: certidão com natureza lida; SITFIS com a frase "não
   foram detectadas pendências". **Sem a frase, não deduz pendência** (não há
   amostra real de relatório com pendência) → REQUER INTERAÇÃO.
5. Certidão: vale a validade carimbada/lida. **Sem validade lida, envelhece
   como extrato (30 dias)** — nunca `emissão + 180`.
6. Consolidado: pendência conhecida vence; REGULAR só se **todas** as esferas
   aplicáveis forem regulares; nada consultado → NÃO CONSULTADO; o resto →
   CONSULTA NECESSÁRIA. Municipal fora do Recife → NÃO SE APLICA.

### 7.6 Fluxos

**Federal automático** (quando o cofre tiver consumerKey/secret e as
procurações existirem): botão "Consultar agora" ou lote → `ciclo.uma_empresa`
(freio de bilhetagem antes da rede) → SITFIS → portão → leitor → carteira.
Trava de servidor: **uma consulta oficial por vez** (429 para a segunda).
Lote: sequencial, 8 s entre empresas, **para sozinho na primeira recusa de
acesso** e **nem começa** se o canal não estiver configurado.

**Federal assistido** (hoje): "Abrir e-CAC" / "Emitir certidão RFB/PGFN" →
baixar → "Guardar o PDF baixado" (origem `ASSISTIDA_ECAC`) → o leitor SITFIS
tenta ler; se não concluir, classificar.

**Recife assistido**: "Extrato de Débitos" / "Certidão Negativa PJ" copiam a
Inscrição Mercantil, **declaram a expectativa na bandeja** e abrem o portal →
a pessoa resolve o CAPTCHA → o PDF chega pela pasta vigiada ou pelo "Guardar"
(origem `ASSISTIDA_RECIFE`, método e usuário registrados) → **classificar**
(status, quantidade, valor, validade, lista de pendências com tributo,
competência, vencimento, principal, encargos, total, situação, CDA/parcel.).

### 7.7 Riscos e limitações

- **Nenhum verde federal automático até existirem consumerKey/secret** e as
  procurações das empresas. É contrato/configuração, não código.
- **A leitura de pendência federal é humana** até haver amostra real de SITFIS
  com pendência; o leitor atual só reconhece o relatório limpo.
- **O extrato do Recife não tem leitor** (a única amostra era imagem — §7.4 de
  FISCALE_EXTRATOS_REAIS.md). Toda classificação municipal é humana.
- O tratamento 202/204 do SITFIS segue a documentação oficial e **não foi
  capturado**; é limitado a 3 repetições.
- O visualizador usa o motor de PDF do próprio navegador (sem biblioteca nova,
  offline). Página e zoom vão por fragmento `#page=&zoom=` — conferido no
  Chromium (Edge/Chrome); outro navegador pode ignorar o zoom.
- **Classificar é do operador** (decisão da Fase H): a conclusão é gravada à
  parte, com o nome dele, e não altera o PDF. **Consultar o órgão e guardar
  documento continuam de administrador.**
- O nome do usuário passa a viajar no backup (`situacao.db`), junto com os
  documentos que ele classificou. IP e hábito de entrada continuam só no
  `auditoria.db`, que não viaja.

### 7.8 Próximos passos

1. Cadastrar **consumerKey/consumerSecret** no cofre e confirmar as
   procurações → rodar o lote federal real e **capturar um SITFIS com
   pendência** para calibrar o leitor.
2. Preencher a Inscrição Mercantil das 3 empresas do Recife sem `im`.
3. Salvar um extrato do Recife **pelo botão de download do portal** e rodar
   `python -m situacao.leitores.diagnostico` nele (Fase E).
5. Consulta CND (SERPRO) só se a emissão automática de certidão federal for
   desejada — decisão comercial.


---

## 8. Escopo municipal CANCELADO — a central é só federal (12/09/2026)

**Decisão:** a Situação Fiscal opera exclusivamente na esfera federal, pelo
**SITFIS via Integra Contador**. O fluxo assistido do Recife foi cancelado.

**Motivo, técnico e de produto:** o Recife em Dia não oferece API e exige
CAPTCHA resolvido por pessoa em todas as consultas relevantes (§7.1). Não há
automação silenciosa possível sem contornar o CAPTCHA — o que este projeto não
faz —, e o processo manual contraria a premissa do sistema.

### Fases canceladas por inviabilidade técnica

| Fase | O que era | Situação |
|---|---|---|
| **D** | Botões do Recife + expectativa na bandeja | **CANCELADA** — entregue em `eda270e`, retirada da Situação Fiscal |
| **E** | `leitores/recife.py` (leitor do extrato municipal) | **CANCELADA** — nunca escrita; bloqueio intransponível (CAPTCHA, sem API) |

### O que saiu

- `web/situacao.html`: bandeja de captura assistida, alertas de pasta vigiada,
  atalhos do Recife (extrato, CND, vínculos, CDA), cópia da Inscrição
  Mercantil, cartão da esfera municipal, coluna "Recife", filtro "Municipal",
  busca por inscrição. O formulário de guarda ficou só com a esfera federal.
- `situacao/carteira.py`: esfera municipal, `eh_recife`, Inscrição Mercantil.
  A linha da carteira tem só `federal` e `geral`; histórico e documentos do
  detalhe são só federais.
- `situacao/regularidade.py`: `consolidar(federal)` — sem peso cruzado.
  Saíram `PENDENCIA_MUNICIPAL`, `SITUACAO_CRITICA` e `NAO_APLICAVEL`.
- `fiscale_server.py`: consulta de esfera não federal responde 400 ("opera
  somente na esfera federal"); upload aceita só as origens e-CAC e manual.

### O que continua no repositório — e por quê

- **Envelope, índice e portão** seguem aceitando as três esferas: são o
  armazenamento imutável, e apagar vocabulário quebraria `captura.json` já
  gravado. Documento municipal antigo **não pesa** na carteira (há teste).
- **Motor da bandeja e tela CIM Recife: REMOVIDOS** no commit seguinte
  ("Ferramental: ..."). Saíram `situacao/bandeja.py`, `web/cim_recife.html`,
  `teste_situacao_bandeja.py`, as rotas `/api/situacao/bandeja*` e
  `/api/situacao/expectativa` (servidor e `fiscale_papeis.py`), o item do menu
  e o atalho "🏛 CIM" em Clientes. O `teste_situacao_http.py` prova que as
  rotas não respondem mais. Os prefixos `bandeja` em `limpar_temporarios.py`
  ficam: servem para limpar sobras de execuções antigas de teste.
- Os botões **"Abrir e-CAC"** e **"Emitir certidão RFB/PGFN"** e o formulário
  de guarda federal continuam: são da esfera federal.

### O que isso muda nos riscos da seção 7

A central passa a depender inteiramente do SITFIS. Enquanto o cofre não tiver
**consumerKey/consumerSecret** — medido hoje: só `jwt_token` —, nenhuma empresa
fica regular por consulta automática. Validar essa credencial contra o
SERPRO é o passo antes do primeiro lote.
