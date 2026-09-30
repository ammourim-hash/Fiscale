# ELO × Meta — roadmap do MVP 1.8

Base: [ELO_META_AUDITORIA.md](ELO_META_AUDITORIA.md) ·
[ELO_META_DECISOES.md](ELO_META_DECISOES.md)

**Nenhuma subfase abaixo foi executada.** A 1.8.0 (esta) termina em
documento; as demais só começam com aprovação explícita, uma por vez.

---

## Duas travas que valem para o roadmap inteiro

**1. O número real não é tocado** até a 1.8.6, e só com autorização
escrita. Não cadastrar, não migrar, não remover, não re-registrar, não
verificar, não conectar, não desconectar.

**2. A pendência do MVP 1.7 continua aberta e obrigatória:**

> **Web Push precisa ser validado em hardware real** — instalar a PWA,
> conceder a permissão, fechar o Elo, receber a notificação do Windows,
> clicar. O roteiro está em `elo/README.md`. **Produção não é considerada
> pronta enquanto isso não for feito**, e isso independe do WhatsApp.

---

## ✅ 1.8.0 — Validação Meta (esta fase)

Pesquisa oficial, validação de arquitetura e checklist. **Concluída.**

Entregou: as três páginas deste conjunto, a confirmação de que
`Message`/`Conversation` **não mudam de forma**, e duas descobertas que
mudam decisão — o Coexistence exigir Tech Provider, e a cobrança de
mensagem de serviço a partir de 01/10/2026.

**Nenhuma ação foi feita na Meta.**

---

## ⚠️ 1.8.1 — Webhook e adapter: CÓDIGO PRONTO, homologação BLOQUEADA

**Concluído em 11/08/2026** do lado do software. O teste com a Meta **não
foi feito**, e o motivo não é falta de código — ver "O bloqueio" abaixo.

### O que ficou pronto

| Item | Situação |
|---|---|
| `conversations.external_contact_id` + índice único por contato | ✅ |
| `GET` de verificação (`hub.challenge` em texto puro) | ✅ |
| `POST` com `X-Hub-Signature-256` sobre o **corpo cru** | ✅ |
| Falha fechada: sem segredo, sem cabeçalho ou formato ruim = recusa | ✅ |
| Interpretação tolerante do payload (nada lança) | ✅ |
| Tenant pela **configuração**, nunca pelo payload | ✅ |
| Recebimento de TEXT → `Conversation` + `Message` já existentes | ✅ |
| Identificação do cliente por telefone (EXACT/NONE/**AMBIGUOUS**) | ✅ |
| Deduplicação por `externalMessageId` | ✅ |
| Envio de TEXT com assinatura "Nome • Área" no corpo | ✅ |
| `externalMessageId` gravado a partir do `wamid` da resposta | ✅ |
| Status `sent`/`delivered`/`read`/`failed`, sem andar para trás | ✅ |
| Retry de timeout/429/5xx; nada de repetir erro permanente | ✅ |
| Logs sem token, App Secret, telefone ou conteúdo desnecessário | ✅ |
| **53 testes novos** (615 no total) · `npm run verify` sai com 0 | ✅ |

### O que NÃO foi feito, de propósito

Coexistence, histórico, contatos, mídia, áudio, documentos, templates,
chamadas, grupos e **o número real**. Nada foi criado, contratado ou
alterado na Meta.

### Validação executada — e o que ela prova

Contra o servidor rodando, por HTTP real:

- handshake com token certo → **200** com o desafio em texto puro;
  com token errado → **403**;
- payload assinado → **200**, e no banco: atendimento `WHATSAPP`,
  contato `558199123456`, uma mensagem `INBOUND` com o texto certo;
- **o mesmo payload com assinatura forjada → 403, e nada gravado**;
- reentrega do payload válido → 200 e **continua uma mensagem só**;
- auditoria com `CONVERSATION_CREATED`, `MESSAGE_RECEIVED` e
  `WHATSAPP_WEBHOOK_REJECTED` — e **sem o corpo recusado**;
- nenhum segredo apareceu no log.

Isso prova **a nossa metade do contrato**. Não prova a metade da Meta.

### 🔴 O bloqueio: HTTPS público

O webhook da Meta exige uma **URL pública com HTTPS e certificado
válido**. O Elo hoje roda em `localhost`. Não há domínio nem VPS.

Conforme combinado, **não improvisei túnel nem serviço de terceiro**.

**Para destravar, é preciso decidir uma destas — e é decisão sua:**

| Caminho | O que envolve |
|---|---|
| **VPS + domínio** | o que a auditoria já previa (2 vCPU/4 GB, Caddy com TLS automático, ~R$ 40–60/mês). Resolve também o Web Push, que precisa do mesmo HTTPS |
| **Túnel de desenvolvimento** | mais rápido para testar, expõe a máquina do escritório à internet enquanto estiver ligado. **Só com sua autorização explícita** |

Enquanto isso não existir, o item fica:

> **PENDENTE DE HOMOLOGAÇÃO** — a mesma marca do Web Push do MVP 1.7.

### Débitos técnicos novos

1. **Retry vive no processo.** Reinício durante a espera deixa a mensagem
   `QUEUED`, visível, sem reenvio automático. Fila durável fica para a
   1.8.2, se o volume pedir.
2. **Um número por tenant no envio.** `despachar` pega o primeiro número
   configurado do tenant; com dois números no mesmo escritório seria
   preciso guardar na conversa por qual número ela entrou.
3. **`AMBIGUOUS` só vai para o log.** O atendimento nasce sem cliente e
   ninguém é avisado na tela. A tela de vínculo manual é da 1.8.2.
4. **Nome do perfil do WhatsApp não é gravado.** Chega no payload e é
   descartado; o atendimento mostra "Contato não identificado" quando o
   telefone não casa. Entra junto com a tela do item 3.
5. **Payload cru não é arquivado.** A seção D de ELO_DECISOES previa
   gravar antes de interpretar. Não foi feito: guardar o corpo de tudo o
   que chega inclui o que chega forjado, e a idempotência por
   `externalMessageId` já cobre o caso de reentrega. Reavaliar na 1.8.5,
   quando o histórico chegar em lote.

---

## 1.8.2 — Adapter inbound e outbound

**Objetivo:** conversa completa nos dois sentidos, pelo número de teste.

- `src/server/channels/whatsapp/` — o único lugar que conhece a Meta;
- entrada: payload → `Contact`/`ExternalCustomerPhone` → `findCustomerByPhone`
  (EXACT / NONE / **AMBIGUOUS**) → `Conversation` → `registrarMensagemRecebida`;
- saída: `enviarMensagem` → adapter → Cloud API, com a assinatura
  "Aline • Fiscal" montada **na borda** (M7);
- `Channel` e `ChannelCredential` (token cifrado em repouso);
- idempotência por `externalMessageId`, com P2002 tratado como sucesso;
- retry conforme M11 — e **falha de envio não impede a mensagem de existir
  no ELO**.

**Pronto quando:** cliente manda, aparece no ELO, funcionário responde pelo
ELO, cliente recebe assinado — e a mesma mensagem reentregue pela Meta
**não** vira duas.

**Bônus que não custa nada:** as notificações do MVP 1.7 passam a funcionar
com WhatsApp de verdade, sem uma linha nova.

---

## 1.8.3 — Mídia e status

- entrada: media id → URL (**5 minutos**) → download → magic bytes →
  storage → `MessageAttachment`, tudo dentro do processamento do webhook;
- saída: upload → media id → envio, respeitando os limites da Meta;
- `voice: true/false` → `VOICE` × `AUDIO` (o modelo já distingue);
- **decidir**: transcodificar voz para `.ogg`/OPUS mono, ou enviar como
  áudio comum quando o formato não servir. Com o custo do ffmpeg na mesa;
- statuses `sent`/`delivered`/`read`/`failed` → `MessageDeliveryStatus`,
  sem encostar em `MessageView`;
- `played` (voz, desde 17/03/2026): mapear ou criar campo próprio.

**Pronto quando:** foto, PDF e áudio atravessam nos dois sentidos, e a tela
mostra ✓✓ do cliente **sem** confundir com "visualizada por Aline".

---

## 1.8.4 — Coexistence em ambiente controlado

**Aqui entra o Embedded Signup — e o papel na Meta.** Antes desta subfase é
preciso ter decidido §14.1 de ELO_META_DECISOES (Tech Provider ou BSP).

- **número que NÃO seja o do escritório** — chip novo, com o WhatsApp
  Business instalado e algumas conversas de teste criadas de propósito;
- Embedded Signup com fluxo customizado, evento
  `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING`, **sem registrar o número**;
- assinar `history`, `smb_app_state_sync`, `smb_message_echoes`;
- **a janela de 24 h**: pedir contatos e histórico pela SMB App Data API
  imediatamente. **Uma tentativa só.** Ensaiar o roteiro antes;
- medir de verdade o que a documentação não responde:
  - áudio enviado pelo celular **chega** em `smb_message_echoes`?
  - mensagem apagada e mensagem de sistema aparecem no `history`?
  - quanto do histórico chega, na prática, e em quanto tempo?
- exercitar o **offboard** pelo celular e o **reconnect** trocando de
  aparelho.

**Pronto quando:** responder pelo celular aparece no ELO marcado como
`BUSINESS_APP`, o histórico importa **sem duplicar** o que já chegou ao
vivo, e as três perguntas acima têm resposta medida.

**Risco ao número real:** nenhum — é outro número.

---

## 1.8.5 — Importação inicial de contatos e histórico

**Objetivo:** transformar a importação em algo que se possa fazer uma vez,
com calma, e conferir depois.

- `HistoryImport` com `phase`, `chunk_order`, `progress` e contadores — o
  mesmo desenho de `SyncRun` do MVP 1.2;
- reconciliação: `history` e webhook ao vivo se sobrepõem, e a trava é
  `(tenantId, externalMessageId)`;
- contatos do `smb_app_state_sync` → nome de exibição; o vínculo com
  cliente continua sendo por telefone, com **AMBIGUOUS decidido por
  gente**;
- tela de conferência: quantas conversas, quantas mensagens, quantos
  contatos, quantos ambíguos aguardando decisão;
- **mídia antiga**: só os últimos 14 dias vêm com arquivo. O resto é
  `media_placeholder` — e a tela precisa dizer isso, não fingir que o
  arquivo sumiu.

**Pronto quando:** importar duas vezes dá o mesmo resultado, e nenhuma
mensagem vira duas.

---

## 1.8.6 — Número real

**Só com autorização escrita, e depois de tudo acima.**

Pré-requisitos, todos obrigatórios:

1. 1.8.1 a 1.8.5 concluídas e aprovadas;
2. **Web Push validado em hardware** (a pendência do 1.7);
3. papel na Meta definido e ativo (Tech Provider ou BSP);
4. **projeção de custo feita** com a tarifa vigente após 01/10/2026 — o
   ELO já tem o volume de mensagens para calcular;
5. decisão sobre faturamento em **BRL** (disponível desde 01/07/2026;
   migração obrigatória até 30/06/2027);
6. roteiro do onboarding ensaiado, com a janela de 24 h cronometrada;
7. plano de rollback escrito: desconectar pelo celular
   (Configurações → Conta → Plataforma de Negócios → Desconectar).

**E uma escolha que ainda é sua:** número novo em produção (mais seguro) ou
Coexistence no número atual (mantém o histórico e o número conhecido). A
auditoria mostra que o Coexistence funciona; ela também mostra que ele custa
um papel na Meta e tem uma janela de 24 h sem segunda chance.

---

## Riscos vivos, para não sumirem

| Risco | Grav. | Onde é medido |
|---|---|---|
| Resposta pelo WhatsApp Desktop (Windows) **não** gera webhook | 🔴 | 1.8.4 — pode virar regra de operação: "responda pelo celular ou pelo ELO" |
| Áudio do celular pode não gerar echo | 🟡 | 1.8.4 |
| Janela de 24 h do onboarding, com uma tentativa | 🔴 | ensaiar na 1.8.4 antes de fazer na 1.8.6 |
| Mensagem de serviço passa a ser cobrada em 01/10/2026 | 🟡 | projeção antes da 1.8.6 |
| Ser Tech Provider é pré-requisito não previsto | 🟡 | decisão antes da 1.8.4 |
| Lock-in de BSP, se for esse o caminho | 🟡 | decisão antes da 1.8.4 |
| Trocar de celular derruba a Cloud API por minutos | 🟢 | documentado; reconecta sozinho |
| Web Push sem validação em hardware | 🔴 | **pendência aberta do MVP 1.7** |

---

## O que NÃO está neste roadmap, de propósito

IA, transcrição, chamadas/WebRTC, grupos, status/stories, catálogo,
marketing em massa e template de campanha. Nada disso foi pedido, e a
auditoria mostrou que grupos e chamadas **não são possíveis** no modelo
escolhido — o que é diferente de "ficaram para depois".
