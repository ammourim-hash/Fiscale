# Fiscale — Roadmap

Documento curto de propósito. Cada item cabe em **uma sessão de trabalho**.
Se um item não couber, ele está mal recortado e precisa ser quebrado antes de começar.

Última revisão: **16/09/2026**

> **Acesso de fora do escritório (D72).** Decidido em 23/08/2026: será por
> **rede privada com HTTPS válido** — nunca porta aberta no roteador, nunca a
> 8777 exposta, nunca banco ou arquivo acessível direto. HTTPS vale também
> dentro da rede privada; certificados e segredos ficam no servidor; o acesso
> precisa ser revogável por usuário e por dispositivo, e registrado. **Fase
> própria** — nada foi instalado ainda.

> **Arquitetura oficial (D97, 14/09/2026).** O FISCALE é **Sistema Web + Auth**:
> `app.sistemafiscale.com.br` → Auth → Central de Aplicações → FISCALE / ELO /
> Administração, **somente pela rede privada** e com HTTPS. O `www` é o site
> institucional, o PWA é instalação opcional e a 8777 nunca é endereço público.
> D72 e D80 continuam valendo.

> **D-AMB-1 — ambiente atual (15/09/2026).** O **AMMOURIM** é atualmente o
> ambiente de **desenvolvimento, teste, homologação e produção provisória** do
> FISCALE. O **servidor definitivo será preparado posteriormente**. A produção
> provisória permanece em `C:\Users\nineq\Fiscale\dados`, na porta **8777**.
> A homologação é uma instância separada: porta **8898** e dados em
> `C:\Fiscale\homologacao\dados` (`homologar_fiscale.bat`), alimentada só por
> restauração de `.fbk` — nunca por cópia da pasta real. D72, D80 e D97
> continuam valendo.

> **Decisão de 23/08/2026 — boletim fiscal QUINZENAL.** O
> `FISCALE_AI0_DIAGNOSTICO.md` propôs o `FiscalUpdateMonitor` com coleta
> semanal (fases AI 5a/5b). Fica **substituído por quinzenal**: a cada 15 dias.
> Registrado aqui para que a AI 5 não seja implementada com a cadência antiga.
>
> **Implementado em 25/08/2026 como Central Fiscal (AI 5a, parte).** A
> cadência virou **configurável** (15 min a 1 dia, padrão 1 hora), a pedido
> do usuário — o quinzenal valia para o BOLETIM curado, que é outra coisa e
> continua na fila (AI 5b). Medição que importa: das 6 fontes do §7.2,
> **só a Receita Federal publica feed**. As outras 5 ficam cadastradas com
> estado `SEM_FEED` e o endereço, porque cobertura imaginada é pior que
> cobertura ausente. Raspagem de HTML (Onda B) continua não feita.

> **Antes da FISCAL AI 2 vem o módulo operacional NF-e / NFC-e.** Diagnóstico
> em `FISCALE_NFE1_DIAGNOSTICO.md`; fases NF-e 2 a NF-e 10 descritas lá.
> A **NF-e 2** está concluída (`FISCALE_NFE2_RELATORIO.md`): a consulta
> indexada existe e a equivalência com o leitor legado foi provada — os
> documentos que estão nos dois lados conferem com diferença de R$ 0,00.
> Ela também revelou que **300 documentos já capturados não aparecem na
> tela**, porque o leitor atual só varre as pastas antigas.
> A **NF-e 3** está concluída (`FISCALE_NFE3_RELATORIO.md`): os 23.463 XML
> legados foram migrados para o acervo, a cobertura foi de ~0,2% para **100% em
> todas as empresas** e a diferença financeira entre os dois leitores é de
> **R$ 0,00** sobre R$ 115,8 milhões. O legado continua intacto (conferido hash
> a hash). Restam 3 eventos com identidade colidente, em quarentena e com as
> duas versões preservadas, aguardando decisão.
> A **NF-e 4** está concluída (`FISCALE_NFE4_RELATORIO.md`): a situação de hoje
> passa a sair de documento + eventos (divergências de cancelamento **5 → 0**),
> e os cinco papéis vêm da estrutura oficial do XML — na maior transportadora,
> **R$ 85,0 mi de frete de terceiros** ficaram separados de **R$ 952 mil de
> compras**. O item ganhou CST separado de CSOSN, CEST, origem, tributos
> detalhados e os grupos **IBS/CBS/IS, que já chegam em 78% dos XML**. 24.023
> A **NF-e 5** está concluída (`FISCALE_NFE5_RELATORIO.md`): a tela
> (`web/nfe_documentos.html`) lê o índice, e os **300 documentos capturados que
> a interface não mostrava passaram a aparecer — provado documento a documento**.
> A listagem caiu de ~7 s para ~90 ms. O leitor legado ficou no código só como
> o outro lado do comparador de equivalência (D73). DANFE segue como fase
> própria (D74), e o saneamento dos eventos 510630 como **NF-e 5B**.

> A **NF-e 6** está concluída (`FISCALE_NFE6_RELATORIO.md`): o resultado da tela
> vira **pacote** — ZIP de XML originais do acervo com `RELATORIO.csv` que
> reconcilia o que entrou e o que não entrou, pacote plano para o Domínio, e
> **DANFE** de verdade (`fpdf2`, já no projeto), validado contra 400 XML reais.
> `dhSaiEnt` passou a ser lido e indexado — e a medição mostrou que ele está em
> **99,5%** do acervo, não na minoria (D76). Decisões novas: D75 (o lote não tem
> pesquisa própria), D76, D77 (DANFE e o CODE-128C escrito aqui), D78 (Domínio:
> os dois formatos, com a incerteza declarada).
> documentos reindexados com zero invariantes alteradas.

> A **NF-e 6B** está concluída (`FISCALE_NFE6B_RELATORIO.md`): a exportação
> deixou de nomear software de terceiro — `XMLs para o Domínio` virou
> **`Baixar XMLs em ZIP`**, e a rota `/exportar/dominio` virou
> `/exportar/xmls-planos`. O formato não mudou; o nome, sim: é um *formato*,
> não um *destino*. Entrou **`Salvar XMLs em pasta`**, que grava no disco do
> servidor (não é download) em `C:\FISCALE\EXPORTACOES\XML`, com
> organização `Empresa/Ano/Mês/Papel` ou pasta única, e cópia **byte a byte
> provada por SHA-256** — nenhum parser entre o original e a cópia. Nada é
> apagado. O documento **somente resumo** parou de sumir: selo próprio,
> filtro, card clicável e link na prévia levam a ele, e continua **proibido**
> transformar `resNFe` em NF-e completa (`Buscar XML completo` existe só como
> contrato visual, desligado). Duas falhas reais de segurança saíram daqui: a
> checagem de caminho absoluto rodava **depois** do `resolve()` (um caminho
> relativo cairia dentro da pasta de instalação), e a regra "a pasta pai
> precisa existir" impedia o próprio destino padrão de nascer — o que precisa
> existir é a **unidade**.
> **Ajuste de 24/08/2026:** a pasta de saída da NF-e passou a ser a mesma
> experiência do NFS-e. O seletor virou **um componente só**
> (`web/fiscale-pasta.js`), usado pelas duas telas — e no caminho apareceu que
> o botão *📁 Escolher pasta* do NFS-e **estava quebrado**: chamava
> `/api/escolher-pasta`, rota que saiu na PORT 4 junto com o tkinter. A
> mensagem de erro dizia *"Cole o caminho da pasta manualmente"*, e por isso
> o defeito passava por instrução de uso. A estrutura virou
> `<pasta escolhida>/<empresa>/` (`ORG_EMPRESA`, o novo padrão): os filtros
> dizem QUAIS XML saem, não mais ONDE eles ficam. E a exportação parou de
> sobrescrever às cegas — mesmo nome com conteúdo diferente **não** é
> reescrito (`ARQUIVO_DIFERENTE_JA_EXISTE`), e reexportar o mesmo período
> virou idempotente (`JA_EXISTIA_IGUAL`).
> **Ajuste de UX de 24/08/2026 (ainda 6B, não é NF-e 7):** o fluxo diário virou
> `empresa/período/filtros → escolher pasta → Baixar XMLs em lote`. A pasta de
> saída e o botão do lote saíram do menu e ficam junto dos filtros; organização,
> relatório e política de canceladas foram para **Configurações avançadas**, e o
> `RELATORIO.csv` deixou de ser obrigatório. O padrão passou a ser **gravar
> direto na pasta escolhida** (`ORG_UNICA`) — a subpasta por empresa virou
> opção. Novo `empresas_nfe.py`: o seletor da NF-e mostra só empresa com
> **Inscrição Estadual** no cadastro de Clientes (mesma fonte, sem lista
> paralela). **Custo medido:** sobram 3 empresas no seletor, e a TRX
> TRANSPORTES (20.705 documentos) fica de fora por não ter cadastro de cliente
> — nada dela foi apagado, e a IE está nos próprios XML (066540453), oferecida
> na tela de pendências. Dívida anotada: `pvEscolher`/`eiEscolher` em
> `web/nfe.html` continuam sendo cópias do seletor.
> **Reconciliação cadastral (24/08/2026):** antes de o filtro por IE valer no
> dia a dia, entrou `web/nfe_pendencias.html` — área do administrador que lista
> as empresas com acervo e cadastro incompleto, com a IE lida dos próprios XML
> como **sugestão**. A rota é só leitura (medido: três chamadas, nenhum arquivo
> alterado) e `Revisar cadastro` leva ao cadastro de Clientes já preenchido, que
> é onde o usuário confirma. **Não há segunda base de empresas.**

---

## Princípio que decide as prioridades

> Tudo o que fazemos aqui é para **automatizar rotina fiscal**.

O FISCALE tem **dois pilares complementares**: `CAPTURA DE DOCUMENTOS` +
`APURAÇÃO FISCAL`. A ingestão de NF-e, CT-e e NFS-e existe para alimentar e
melhorar a apuração que o sistema já faz — não para virar um painel de XML.
Decisão registrada em `FISCALE_DECISOES_FISCAIS.md` (**D42** a **D46**): o
fluxo até o resultado fiscal, a rastreabilidade obrigatória de todo valor
apurado até os documentos que o originaram, as segregações do Simples Nacional
e do Lucro Presumido, e a tela operacional de apuração.

Um recurso entra neste roadmap quando responde *sim* a estas três perguntas:

1. Elimina digitação ou conferência manual que o escritório faz hoje?
2. Continua valendo daqui a um ano?
3. Cabe numa sessão de trabalho?

Recurso que falha em qualquer uma delas vai para **Fora de escopo**, no fim
deste arquivo, com o motivo escrito. Isso não é rejeição: é registro de decisão,
para não reabrirmos a mesma discussão daqui a seis meses.

---

## O que já está pronto

Registrado aqui para não ser reconstruído por engano.

| Recurso | Onde |
|---|---|
| Captura NFS-e do Portal Nacional (certificado A1, mTLS) | `nfse/backend/core.py` |
| DANFSe oficial com cache em disco | `core.py` · `baixar_danfse()` |
| Download em lote de PDFs | `/api/exportar-pdfs` |
| Visualizador de nota (PDF + dados + retenções) | `nfse/frontend/index.html` · `verNota()` |
| XML individual da NFS-e | `/api/nfse/xml` |
| Histórico municipal do Recife | `recife.py` |
| Importador de XML das prefeituras | `prefeituras.py` · `importar_xml_nfse()` |
| Prévia do DAS com RBT12 automático | `classificador.py` |
| ISS fixo do escritório contábil (LC 123 art. 18 §22-A) | `classificador.py` |
| Fator R pela folha | `classificador.py` |
| Painel do mês — todas as empresas em paralelo | `painel.py` |
| NF-e: distribuição DFe, importador de XML, auditor | `nfe.py`, `auditor_nfe.py` |
| Conferidor EFD × XML (créditos PIS/COFINS) | `auditor_nfe.py` · `conferir_efd()` |
| CClassTrib (Reforma Tributária) | `web/cclasstrib.html` |
| Clientes: certificado, IE (NF-e) e IM (NFS-e) | `inscricoes.py` |
| Central Fiscal — o que mudou, com link para a fonte | `fiscal_atualizacoes.py`, `web/central_fiscal.html` |
| Documentos Fiscais — NF-e · NFC-e · CT-e em abas (Etapa A) | `web/nfe_documentos.html` |
| Primeira sincronização de empresa nova (onboarding) | `/api/nfe/onboarding`, `web/nfe_pendencias.html` |
| Atalho na Área de Trabalho no primeiro uso | `atalho_desktop.py` |

---

## Fila

### 1. Monitor de vencimento — certificados e procurações
**Por quê:** hoje o vencimento só aparece quando algo quebra. A procuração do
Fulano sustenta o acesso a Olinda e Paulista; se vencer sem aviso, o
histórico municipal para de entrar sem ninguém perceber.

**O que fazer:** ler a validade que já vem do certificado, guardar a data de
validade da procuração por empresa, e mostrar no Painel do mês um aviso a 60,
30 e 7 dias. Sem e-mail, sem agendador — aviso na tela basta.

**Pronto quando:** o Painel mostra o que vence nos próximos 60 dias, e uma
empresa com procuração vencida aparece marcada antes de a captura falhar.

---

### 2. Conferência PGDAS-D × prévia do Fiscale
**Por quê:** é literalmente a rotina que o escritório faz na mão todo mês. Foi
essa conferência que revelou o erro do ISS fixo da Monte.

**O que fazer:** campo para lançar o DAS realmente pago em cada competência
(um número por mês, junto do RBT12 que já existe) e uma coluna de diferença no
Painel. Diferença acima de R$ 1,00 fica destacada.

**Pronto quando:** o Painel mostra, por empresa, prévia × pago × diferença, e a
Monte fecha com diferença zero nos meses já conferidos.

---

### 3. Importador de XML para Paulista
**Por quê:** o web service de Paulista está barrado no **E171** — a prefeitura
precisa autorizar o certificado para o serviço, e isso não se resolve em código.
O importador de XML usa o acesso ao portal que já funciona.

**O que fazer:** `importa_xml=True` em `prefeituras.py` para o IBGE 2610707, do
mesmo jeito que Olinda.

**Pronto quando:** EDU NORTE, EDU SOLUÇÕES e EDU EDUCAÇÃO JANGA aceitam XML do
portal e o RBT12 delas fecha.

---

### 4. Status do PDF separado do status da nota
**Por quê:** hoje, quando o DANFSe não vem, a tela não distingue "a Receita
está sem responder" de "esta nota não tem PDF oficial". São coisas diferentes e
levam a ações diferentes.

**O que fazer:** um campo de estado do arquivo (`disponível`, `pendente`,
`indisponível na origem`, `limite atingido`, `só XML`) mostrado no visualizador
e como ícone na coluna PDF.

**Pronto quando:** uma nota do histórico municipal e uma nota barrada por
HTTP 429 exibem mensagens diferentes e corretas.

---

### 5. Folha de pagamento das empresas que faltam
**Por quê:** sem folha não há Fator R, e sem Fator R o anexo pode sair errado —
o que muda o valor da prévia.

**Pendentes:** PESSOA EXEMPLO, CLIENTE B, EMPRESA J.

**O que fazer:** é preenchimento de dado, não código. Entra aqui para não se
perder.

---

### 6. Histórico do Recife para LOCADORA EXEMPLO e TRANSPORTES BETA
**Por quê:** LOCADORA EXEMPLO tem RBT12 de ~R$ 826 mil com 5 meses vazios — a prévia
sai **menor** que o devido, que é o erro perigoso.

**O que fazer:** rodar o download do Recife (já existe em `recife.py`) com a
senha do certificado. Depende do usuário; não dá para automatizar.

---

## Fora de escopo (com o motivo)

Estes itens foram propostos e **recusados de propósito**. Não são esquecimento.

| Item | Por que não |
|---|---|
| Plano de Saúde | **Removido do produto em 22/08/2026** (FISCAL AI 1, D71). Fora do escopo "documento fiscal": não era captura nem apuração. Os dados foram arquivados cifrados antes da exclusão. |
| e-CAC | **Removido do produto em 22/08/2026** (FISCAL AI 1, D71). Era atalho para o portal da Receita, sem backend. O cálculo do MIT continua em `apuracao_federal.py`. |
| Multi-tenant (`tenantId` em tudo) | O Fiscale roda em `localhost` para um escritório. Não há segundo inquilino. Adiciona bug, não segurança. |
| JWT / OAuth / rate limit | Não há rede pública envolvida. O login local já existe. |
| Docker / Kubernetes / CI-CD | O obstáculo real de deploy é o **Smart App Control** do Windows bloqueando executável não assinado. Orquestração não resolve isso. |
| Migração para PostgreSQL | São 17 empresas em arquivos JSON/XML. O banco resolveria um problema de escala que não existe. |
| API pública / plugin para o Domínio | O Domínio importa por **arquivo** (XML, TXT, CSV, Excel), não por API. Exportar arquivo já cobre. |
| Conectores para Camaragibe, João Pessoa, Brasília | O Portal Nacional cobre todas as cidades desde 2026. Conector municipal só serve para histórico anterior — e esse buraco **fecha sozinho em 12/2026**. |
| Especificação de 350–500 páginas | Documento grande demais não é lido por ninguém, nem por mim: eu leria fragmentos e o código divergiria em silêncio. Este arquivo existe no lugar dela. |
| ~~Assistente Fiscal com IA~~ | **Voltou à fila em 22/08/2026.** A condição de reabertura escrita aqui — "quando houver uma pergunta concreta que ele responda" — foi cumprida: chegou a lista das 19 perguntas. Diagnóstico em `FISCALE_AI0_DIAGNOSTICO.md`; a fase de limpeza está em `FISCALE_AI1_RELATORIO.md`. |

---

## Como trabalhar neste arquivo

- Terminou um item? Move para **O que já está pronto**, com o caminho do arquivo.
- Recusou um item? Escreve na tabela **Fora de escopo** com o motivo em uma linha.
- A fila é ordenada. Item 1 é o próximo — não há prioridade paralela.
