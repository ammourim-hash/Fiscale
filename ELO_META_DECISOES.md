# ELO × Meta — decisões de arquitetura

**MVP 1.8.0. Nada implementado.** Base factual em
[ELO_META_AUDITORIA.md](ELO_META_AUDITORIA.md).

A pergunta que estas decisões respondem: **o que muda no ELO quando o
WhatsApp entrar?** A resposta curta, e o objetivo desta fase: **quase
nada** — e onde muda, muda em lugar isolado.

---

## M1. O WhatsApp é um CANAL, e o domínio não o conhece

`Conversation.channel` e `Message.channel` já são **texto livre**, com
`INTERNAL` e `DEV` em uso. O WhatsApp entra como mais um valor.

A regra escrita no MVP 1.5 (S34) continua valendo e vira critério de
revisão desta fase:

> Se o nome de um provedor aparecer no modelo de domínio, algo foi modelado
> errado.

O adaptador vive em `src/server/channels/whatsapp/` e é o **único** lugar
do projeto que sabe o que é `phone_number_id`, `wa_id` ou Graph API. Ele
converte payload da Meta em chamada aos serviços que já existem —
`registrarMensagemRecebida`, `criarAtendimento` — e nada mais.

**Consequência que vale dizer em voz alta:** as notificações do MVP 1.7 não
precisam de UMA linha nova. Mensagem do WhatsApp vira `Message`, `Message`
já dispara SSE e Web Push.

## M2. `Message` e `Conversation` não mudam de forma

Auditei campo a campo contra o que a Meta entrega:

| Precisa da Meta | Já existe no ELO |
|---|---|
| id da mensagem no canal | `Message.externalMessageId` + unique `(tenantId, externalMessageId)` |
| quem mandou, do lado de fora | `Message.externalSenderId` |
| canal | `Message.channel` |
| ordem | `Message.sequence` (nosso, não o da Meta) |
| status de transporte | `MessageDeliveryStatus` = QUEUED/SENT/DELIVERED/READ/FAILED |
| tipo | `MessageType` = TEXT/IMAGE/DOCUMENT/AUDIO/VOICE |
| anexo | `MessageAttachment` com sha256, mime, tamanho, duração |
| leitura interna | `MessageView` — outra coisa, e continua sendo |

**Nenhuma migration de alteração é necessária em `messages`.** O que falta
são campos ADITIVOS de correlação (M3).

A distinção `AUDIO` × `VOICE` (S48), criada quando ninguém a pedia, casa
exatamente com o `voice: true/false` do webhook. Foi a decisão certa.

## M3. O que precisa ser acrescentado — e só isso

Aditivo, sem tocar em coluna existente:

```
Channel                         o número, por tenant
  id · tenantId · type("WHATSAPP") · phoneNumberId · wabaId
  displayPhoneNumber · displayName · mode(CLOUD_API|COEXISTENCE)
  active · createdAt

ChannelCredential               segredo por canal, cifrado em repouso
  channelId · tokenCifrado · expiresAt · rotatedAt

Conversation
  + externalConversationId?     (o `wa_id` do cliente)

Message
  + externalTimestamp?          o horário da Meta, que não é o nosso
  + origin?                     CLOUD_API | BUSINESS_APP | HISTORY
```

**`origin` não é enfeite.** No Coexistence a mesma tabela vai guardar
mensagem que saiu do ELO, mensagem que saiu do celular e mensagem que veio
do histórico — e um dia alguém vai precisar responder "por que esta
resposta não tem autor?". A resposta é `origin = BUSINESS_APP`.

**`externalTimestamp` separado de `createdAt`** porque o histórico traz
mensagem de 170 dias atrás: `createdAt` é quando o ELO soube, e a linha do
tempo do cliente é o outro. Confundir os dois embaralharia a conversa
importada — foi exatamente o defeito corrigido em S43.

## M4. Idempotência: o índice decide, como sempre

A mesma mensagem pode chegar **três vezes**: pelo `history`, pelo
`messages` ao vivo e por reentrega da Meta. E `smb_message_echoes` **não
promete ordem nem deduplicação** — está escrito na referência.

A trava já existe desde o MVP 1.5: `@@unique([tenantId, externalMessageId])`.

A regra desta fase: **todo caminho de entrada grava `externalMessageId` e
trata P2002 como sucesso**, devolvendo a mensagem que já existe. Sem
`SELECT` antes do `INSERT` — entre os dois cabe a segunda entrega inteira
(S36).

Para o `history`, que chega **fora de ordem** e em três fases, o
`chunk_order` e o `progress` vão para uma tabela de execução
(`HistoryImport`), do mesmo jeito que `SyncRun` guarda a sincronização de
clientes no MVP 1.2. Importar duas vezes precisa dar o mesmo resultado.

## M5. Mensagem enviada pelo celular é cidadã de primeira classe

O pior defeito possível no Coexistence: **o cliente foi respondido pelo
celular e o ELO acha que ninguém respondeu.** O caso apareceria como
atendimento "não lido" eternamente, e alguém responderia de novo.

`smb_message_echoes` resolve — e vira `Message` com:

```
direction         = OUTBOUND
origin            = BUSINESS_APP
senderMembershipId= null
senderDisplayName = null          ← ninguém assina o que não escreveu aqui
externalMessageId = id do echo
```

E `firstResponseAt` é preenchido por ela também: do ponto de vista do
cliente, ele **foi** respondido.

**Na tela**, essa mensagem precisa se declarar — algo como "enviada pelo
WhatsApp no celular". Inventar um autor seria mentir no histórico, e o
histórico é o que o escritório usa para saber o que aconteceu.

⚠️ **Risco aberto, registrado:** áudio e voz **não constam** na lista de
tipos do `smb_message_echoes`, e mensagens de aparelhos não suportados
(**WhatsApp para Windows**) **não disparam webhook nenhum**. Ou seja: pode
haver resposta que o ELO nunca vê. Isso é **medido na fase 1.8.4**, e até
lá é limitação declarada — não problema resolvido.

## M6. Contato, cliente e ambiguidade — reusa o MVP 1.2

Nada de cadastro novo. O caminho é o que já existe:

```
telefone do WhatsApp
   ↓ normalização conservadora (S22: na dúvida, não normaliza)
ExternalCustomerPhone
   ↓ findCustomerByPhone
EXACT → vincula      NONE → contato não identificado      AMBIGUOUS → pessoa decide
```

`AMBIGUOUS` **nunca** escolhe sozinho. Matriz e filial com o mesmo telefone
é rotina em contabilidade, e a tela dirá:

> Encontramos mais de um cliente para este telefone. [Vincular manualmente]

Os contatos que vêm do `smb_app_state_sync` entram como **nome de exibição
do contato**, e não como cliente. Um contato do celular do escritório pode
ser o contador da empresa, o sócio, ou o fornecedor do café — não é o
cadastro fiscal. O vínculo com `ExternalCustomerReference` continua sendo o
telefone, com as três respostas acima.

## M7. A identificação do funcionário vai no CORPO da mensagem

Decisão **D1** do MVP 1.3, agora confirmada pela documentação: o perfil do
número é do escritório, e no Coexistence a personalização do perfil nem
sequer passa pela Cloud API.

Então:

```
Aline • Fiscal
Bom dia, Maria! Vou verificar para você.
```

A assinatura é montada pelo **adaptador**, na saída, a partir de
`senderDisplayName` e `senderDepartmentName` — que já estão **congelados**
na mensagem desde o MVP 1.5 (S37).

**Por que no adaptador, e não no serviço:** dentro do ELO a assinatura é
apresentação (a interface monta "Nome • Área" a partir dos dois campos).
Se o serviço concatenasse, o texto formatado iria para o banco e não
daria mais para mudar a apresentação sem reescrever o histórico. O
adaptador é a borda: é ali que o texto vira o que o cliente lê.

**Sem perfil de WhatsApp por funcionário.** Nunca esteve em discussão e
agora tem base: o perfil é do número, e o número é um só.

## M8. Status: o do transporte é do transporte

`MessageDeliveryStatus` já existe e mapeia direto:

| Meta | ELO |
|---|---|
| (POST aceito) | `SENT` |
| `sent` | `SENT` |
| `delivered` | `DELIVERED` |
| `read` | `READ` |
| `failed` | `FAILED` |
| `played` (voz, desde 17/03/2026) | `READ` — ou campo próprio, a decidir na 1.8.3 |

**A confusão que não pode acontecer**, e que S40 já separou: `READ` é "o
cliente leu no WhatsApp"; `MessageView` é "a Aline abriu no ELO". São
lados diferentes da conversa. Um dia aparecem juntos na mesma tela, e
precisam continuar distinguíveis.

Enquanto a mensagem não sai, ela é `QUEUED` — que é o estado em que toda
mensagem interna vive hoje.

## M9. Mídia: baixar, adotar, esquecer a Meta

O storage do MVP 1.6 **não é reconstruído**. O fluxo de entrada:

```
webhook com media id
   → GET /{media-id}  → URL (válida por 5 MINUTOS)
   → baixar os bytes
   → validar magic bytes (S51 — três checagens, e a terceira é a que vale)
   → StorageProvider.put()
   → MessageAttachment PENDING → adotado pela Message, na mesma transação
```

Os cinco minutos da URL mandam no desenho: **baixar é parte do
processamento do webhook**, não uma tarefa para depois. Guardar a URL para
usar mais tarde daria um anexo quebrado com hora marcada.

Na saída: upload para a Meta → `media id` → enviar. Os limites da Meta
(áudio 16 MB) são **mais apertados** que os nossos em alguns casos, e a
distinção está registrada desde S52: os limites do canal externo são dele,
e a validação de saída é do adaptador.

**Voz:** `.ogg`/OPUS **mono** é o que a Meta exige para virar mensagem de
voz. O gravador do ELO produz `audio/webm;codecs=opus` no Chrome (S56).
**Transcodificar ou não é decisão da fase 1.8.3** — e ela tem custo real
(ffmpeg no processo web, que S56 recusou uma vez). A alternativa honesta é
mandar como `audio` comum quando o formato não servir para voz.

## M10. Segurança: o segredo da Meta não encosta no navegador

| Segredo | Onde vive |
|---|---|
| App Secret | variável de ambiente do servidor. Só valida assinatura |
| Access token / system user token | `ChannelCredential`, **cifrado em repouso**, por tenant |
| Verify token do webhook | ambiente |

Nada disso vai para `NEXT_PUBLIC_`, nem para log, nem para auditoria. É a
mesma regra que o MVP 1.7 aplicou à chave VAPID — e o teste estrutural que
varre `src/` procurando quem conhece o segredo **será estendido** para
cobrir estes.

**Validação do webhook, na ordem:**

1. `X-Hub-Signature-256` confere com HMAC-SHA256 do **corpo cru** e o App
   Secret → senão, **descarta antes de interpretar**;
2. anti-replay por `id` de mensagem (a Meta reentrega por dias);
3. só então o payload entra no domínio.

Payload sem assinatura válida **não vira log de erro com o conteúdo
dentro** — vira contador. Log de payload inválido é como se guarda o que
um atacante mandou.

## M11. Retry: o que repete e o que não

| Situação | O que fazer |
|---|---|
| timeout / rede | repetir com backoff |
| `429` | respeitar e repetir com backoff |
| `5xx` | repetir com backoff |
| `4xx` de validação | **não repetir** — o defeito é nosso |
| template não aprovado | **não repetir** |
| telefone inválido | **não repetir**, marcar `FAILED` |
| fora da janela de 24 h | **não repetir** — precisa de template, e isso é decisão de gente |

Mesma lógica do push do MVP 1.7: um teto de falhas, e depois desistir com o
estado registrado. Insistir para sempre num erro permanente é o que
transforma fila em entulho.

**E vale a regra que atravessa o projeto:** falha no canal **não** pode
impedir a mensagem de ser gravada no ELO. Ela nasce `QUEUED` e o transporte
é efeito secundário — exatamente como o push (MVP 1.7).

## M12. O FISCALE não participa do WhatsApp

Confirmado e reforçado: o recebimento vive no VPS. **FISCALE desligado =
ELO e WhatsApp funcionando normalmente.**

O FISCALE continua com dois papéis, os dois de saída: emitir o token de
entrada e empurrar a projeção de clientes. Nenhum webhook da Meta chega
perto da máquina do escritório — onde estão os certificados.

## M13. Coexistence é escolha de PRODUÇÃO, não de desenvolvimento

Todo o desenvolvimento (fases 1.8.1 a 1.8.3) acontece no **número de
teste**, que é gratuito e fala com 5 destinatários. Nada disso encosta no
número do escritório.

O Coexistence só entra na 1.8.4, em ambiente controlado, e com **um número
que não seja o principal** — um chip novo, se necessário. Só depois, e com
autorização explícita, o número real (1.8.6).

Motivo: o onboarding do Coexistence tem **uma janela de 24 horas** para
sincronizar histórico e contatos, e **uma única tentativa**. Errar isso com
o número do escritório significa desconectar e refazer o fluxo inteiro —
com o atendimento no ar.

## M14. Duas coisas mudaram a conta, e precisam decisão sua

### 14.1 Ser Tech Provider deixou de ser opcional para o Coexistence

A auditoria mostrou que o Coexistence exige Embedded Signup, e o Embedded
Signup é para Tech Provider / Solution Partner / Tech Partner. **Não achei
caminho oficial para um negócio comum fazer Coexistence no próprio número
sem passar por isso.**

Como o Cenário B (vender o ELO a outros escritórios) exige o mesmo papel,
as duas necessidades apontam para o mesmo lugar. **A recomendação é
avaliar virar Tech Provider** — e não contratar BSP por reflexo. Mas é
decisão sua, e depende de quanto do Cenário B é real.

### 14.2 Responder cliente vai passar a ser cobrado

Hoje, mensagem de serviço dentro da janela de 24 h é **grátis**. A partir
de **01/10/2026** passa a ser cobrada por mensagem.

Isso não muda a arquitetura, mas muda o cálculo do produto: o ELO existe
para responder cliente, e responder cliente vira linha de custo. **Antes de
migrar o número real, medir o volume** — o próprio ELO já tem os dados
(mensagens por mês, por cliente) para essa projeção.

---

# Decisões da implementação — MVP 1.8.1

O que só apareceu quando o código foi escrito. Nada aqui derruba as
decisões acima; três delas ficaram **mais simples** do que o previsto.

## M16. `Channel` e `ChannelCredential` NÃO foram criadas

M3 previa as duas tabelas. Com **um** número de teste e **um** escritório,
elas teriam hoje uma linha copiada de variável de ambiente e nenhum leitor
de verdade — "tabela sem leitor é dívida, não progresso" (S6).

O que entrou no lugar: **uma coluna**, `conversations.external_contact_id`,
e a configuração em `ELO_WHATSAPP_NUMBERS`.

Elas entram quando houver vários números, ou seja, no Embedded Signup da
fase 1.8.4 — que é também quando o token deixa de ser um só e precisa
mesmo de linha por canal, cifrada em repouso.

## M17. O tenant vem da configuração, e há um motivo técnico além da segurança

O webhook chega com `phone_number_id` e **sem** tenant. Descobrir o dono
lendo uma tabela exigiria abrir o contexto de RLS — que precisa justamente
do tenant que se está tentando descobrir. **É a mesma circularidade de
S12**, e a saída é a mesma: a configuração é a autoridade, nunca o dado
que chegou pela rede.

`ELO_WHATSAPP_NUMBERS` mapeia `phone_number_id → tenantId`, exatamente como
`ELO_INTEGRATION_PUBLIC_KEYS` faz com o `kid` desde o MVP 1.2 (S19).

**Débito registrado:** com muitos números isso vira tabela, e a
circularidade volta a ser um problema real a resolver na 1.8.4.

## M18. `origin` e `externalTimestamp` ficaram para depois

M3 também previa esses dois campos em `Message`. Nenhum tem leitor nesta
fase: sem Coexistence não existe mensagem `BUSINESS_APP`, e sem importação
de histórico não existe mensagem de 170 dias atrás cuja data seja diferente
de "agora". Entram com quem os usa — 1.8.4 e 1.8.5.

## M19. Um atendimento por contato, garantido pelo ÍNDICE

`@@unique([tenantId, channel, externalContactId])`.

O caso real: o cliente manda três mensagens seguidas, e a Meta entrega três
webhooks quase simultâneos. Um `findFirst` seguido de `create` deixaria
passar os três — entre a consulta e a criação cabe a segunda mensagem
inteira. É a lição de S26 (assumir) e S36 (idempotência), agora aplicada à
criação do atendimento: **quem decide é o banco**, e o perdedor lê o
vencedor.

Vale também para atendimento **resolvido**: mensagem nova volta para o
mesmo caso, sobe na lista e aparece como não lida — sem reabrir sozinho
(S42). Confirmado por teste.

## M20. Status não anda para trás

A Meta **não garante ordem**: o `read` pode chegar antes do `delivered`.
Sem trava, o ✓✓ azul viraria ✓ cinza sozinho na tela e ninguém entenderia.

Há uma escala (`QUEUED < SENT < DELIVERED < READ`) e um status só sobe.
`FAILED` fica **fora** da escala, como terminal: uma entrega que falhou
depois de aceita continua sendo falha, e não um retrocesso a ignorar.

## M21. O webhook responde 200 quase sempre — e 403 na assinatura

A Meta **reentrega por dias** o que não recebeu 200, e desativa o webhook
depois de muita falha.

| Situação | Resposta |
|---|---|
| assinatura inválida | **403** — aqui queremos que PARE |
| número que não é nosso | 200, ignorado e contado |
| tipo não tratado (imagem, hoje) | 200, ignorado e contado |
| JSON quebrado | 200 |
| defeito nosso ao gravar | 200, registrado no log |

Devolver 500 num defeito nosso faria a Meta reenviar o mesmo evento por
dias. A idempotência absorveria, mas o barulho seria enorme.

E o que **não** acontece: o corpo de um payload recusado **não** vai para o
log nem para a auditoria. Guardar o que um atacante mandou é construir o
arquivo do ataque alheio. A auditoria registra o MOTIVO — confirmado por
teste.

## M22. Tipo não suportado é IGNORADO, nunca vira mensagem vazia

Uma imagem chegando hoje (mídia é a 1.8.3) não pode virar um balão em
branco no histórico do cliente. Ela é contada no log — `tipo_image:1` — e
descartada.

A interpretação do payload é **frouxa de propósito**: percorre o que
entende, ignora o que não entende, e nada lança. O webhook é contrato de
terceiro que muda sem avisar; um schema rígido transformaria uma adição da
Meta em queda de recebimento. Perder um tipo novo é ruim; perder todas as
mensagens porque um campo novo apareceu é muito pior.

## M23. A assinatura é conferida sobre o corpo CRU

`await req.text()` **antes** de qualquer `JSON.parse`. Ler como JSON e
reserializar muda espaço, ordem de chave e escape de acento — e a
assinatura deixa de bater por motivo nenhum, num defeito que custa uma
tarde para achar.

Comparação com `timingSafeEqual`, e falha fechada: sem segredo, sem
cabeçalho ou com formato estranho, a resposta é sempre **não é válida**.
Nunca "passa porque não deu para conferir".

## M24. O envio não bloqueia a mensagem — de novo

Mesma forma do Web Push (MVP 1.7): `despacharSemBloquear` depois do
COMMIT, sem `await` do lado de quem escreve. Meta fora do ar significa
`deliveryStatus = FAILED` e a mensagem **visível no Elo**, nunca um erro na
cara de quem acabou de escrever.

Retry só do que vale repetir — timeout, 429, 5xx —, três tentativas, e
nada de insistir em 4xx de validação, template exigido ou telefone
inválido. Insistir num erro permanente é o que transforma fila em entulho.

**Débito:** o retry vive no processo. Reinício durante a espera deixa a
mensagem `QUEUED`, visível, sem reenvio automático. Fila durável é
assunto da 1.8.2, se o volume pedir.

---

## M15. O que continua fora, e agora com base documental

- **Grupos**: não sincronizam, não existem na Cloud API. A tela dirá
  "Grupos continuam no aplicativo e não aparecem no ELO." Sem contorno.
- **Chamadas**: a Calling API exige número **fora** do Business App —
  incompatível com Coexistence. **VALIDAÇÃO FUTURA**, e só se o Coexistence
  for abandonado um dia.
- **Status/stories, catálogo, pedidos, etiquetas do app**: continuam no
  aplicativo.
- **E2EE**: nada muda. A decisão da seção 15 de ELO_DECISOES continua de pé
  — e o argumento que a encerrava (a Cloud API já descriptografa no
  servidor da Meta) segue valendo.

---

## M25. Chamadas: fora de escopo para o número atual (24/09/2026)

Auditoria da **WhatsApp Business Calling API** feita em 24/09/2026 sobre a
documentação oficial vigente. Esta nota **fecha** o "VALIDAÇÃO FUTURA" que
a M15 tinha deixado aberto, e registra a decisão:

1. **O número atual do escritório permanece no WhatsApp Business App, em
   Coexistence.** Nada muda no que já está em operação.
2. **A Calling API fica fora do escopo atual para esse número.** Não se
   implementa Calling API, WebRTC/SRTP, SIP, gravação, transcrição, evento
   de chamada, nem enum ou migration de chamada.
3. **Telefonia futura exige decisão separada**, e a primeira pergunta dela
   não é técnica: seria preciso um **número dedicado ao Cloud API**, fora
   do aplicativo. Só depois disso as outras perguntas fazem sentido.
4. **Isto não bloqueia nada do atendimento.** Mensagens, áudios (mensagem
   de voz), anexos, fila, transferência, SSE e Web Push seguem o roteiro
   que já existe. A decisão é sobre chamada de voz, e só.

### Por que — as duas frases da Meta que se somam

Nenhuma página da Meta diz "Calling API não funciona com Coexistence".
Essa frase **não existe**, e não é honesto citá-la. O que existe são dois
requisitos independentes que, juntos, não deixam brecha:

- Pré-requisito nº 1 da Calling API: *"Your business number is in use with
  Cloud API **(not the WhatsApp Business app)**"*.
- Tabela de funcionalidades do Coexistence, linha única sobre chamadas:
  *"Voice and video calls | No change. | **Not supported**."* — continuam
  no aplicativo, não existem no Cloud API daquele número.

O número em Coexistence **está** no aplicativo. Logo, não atende ao
pré-requisito. A incompatibilidade é **confirmada por requisito**, não por
uma declaração direta — e é assim que deve ser citada.

### Três coisas que a auditoria trouxe e vale não perder

- **O gargalo não é código.** Habilitar calling exige limite de **2.000
  destinatários únicos por dia**. Um escritório com ~20 clientes não chega
  lá por uso natural.
- **A Meta não entrega áudio pronto.** Ela entrega sinalização e uma sessão
  **WebRTC/SRTP ao vivo**. Gravação e transcrição **não são mencionadas em
  página nenhuma** — quem quiser gravar, grava o SRTP por conta própria,
  com o que isso implica de LGPD. Não confundir com mensagem de voz, que já
  funciona.
- **Roteamento por setor não existe do lado da Meta.** Sem URA, sem menu,
  sem fila. Qualquer escolha de setor é nossa — o que, aliás, vale também
  para o fluxo de entrada por mensagem.
- Chamada **de saída** é bloqueada para números de EUA, Canadá, Egito,
  Vietnã e Nigéria. **O Brasil não está nessa lista** — se um dia houver
  número dedicado, esse caminho está aberto.

Fontes: [Calling](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling),
[reference](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/reference),
[call-settings](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/call-settings),
[app-review](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/app-review-guidelines),
[Coexistence](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users).
