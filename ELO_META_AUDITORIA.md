# ELO × Meta — auditoria da WhatsApp Business Platform

**MVP 1.8.0 — validação. Nada foi implementado, nada foi contratado, nada
foi tocado na Meta.** Nenhum número, real ou de teste, foi cadastrado.

Escrito em **11/08/2026**, a partir da documentação oficial vigente
(`developers.facebook.com` e `business.whatsapp.com`). O que a documentação
oficial não responde está marcado **NÃO CONFIRMADO** — e não foi preenchido
com blog, vídeo ou fórum.

> **Correção de uma fonte anterior.** A REVISÃO 1 de
> [ELO_DECISOES.md](ELO_DECISOES.md) descreveu o Coexistence a partir de
> textos de BSPs, com a ressalva de que a documentação oficial não tinha
> sido aberta. Agora foi. **O essencial se confirmou** — o número continua
> no aplicativo —, mas dois pontos daquela versão **não se sustentam na
> documentação oficial**: a lista de países excluídos e o requisito de
> "atividade recente". Ver §2.4 e §2.5.

---

## 1. O mapa: quem é o quê na plataforma

| Papel | O que é | Cobrança | Precisa disso para quê |
|---|---|---|---|
| **Negócio direto** ("self") | empresa que cria um app para o **próprio** número | paga a Meta direto | Cloud API comum |
| **Tech Provider** | oferece a plataforma a **outras** empresas, sem linha de crédito | a Meta cobra do **cliente**; o provider cobra o software à parte | Embedded Signup, Coexistence |
| **Solution Partner (BSP)** | idem, **com linha de crédito**; fatura o cliente | o BSP fatura o cliente | idem, mais revenda |

A documentação de parceiros diz, para quem constrói para si mesmo:
"refer to our Cloud API Get Started guide instead". Ou seja: **o escritório
pode usar a Cloud API no próprio número sem ser parceiro de nada.**

**Mas há uma exceção que decide esta fase:** o Coexistence exige Embedded
Signup, e o Embedded Signup exige **app do tipo Business com Advanced
Access aprovado em App Review**, disponível a "Tech Providers, Solution
Partners, or Tech Partners". Ver §4.

---

## 2. Coexistence — o número no aplicativo E na Cloud API

Nome oficial: **"Onboarding WhatsApp Business app users"**. "Coexistence" é
como aparece no suporte e na documentação de parceiros.

### 2.1 As 18 perguntas, respondidas

| # | Pergunta | Resposta oficial |
|---|---|---|
| 1 | O Business App continua funcionando no celular? | **Sim.** O negócio continua enviando 1:1 pelo aplicativo, e a Meta "keeps messaging history between both apps in sync" |
| 2 | A Cloud API funciona ao mesmo tempo? | **Sim.** É o ponto do recurso |
| 3 | Elegibilidade | App **2.24.17 ou superior**; onboarding por Embedded Signup customizado; o parceiro precisa ser Tech Provider ou Solution Partner |
| 4 | Países/regiões | **NÃO CONFIRMADO.** A documentação oficial do fluxo não traz lista de países. A lista de exclusões da REVISÃO 1 veio de BSP e **não foi confirmada** |
| 5 | Atividade recente | **NÃO CONFIRMADO.** Não há esse requisito na documentação oficial do fluxo |
| 6 | Limitações do número | **Throughput fixo de 20 mensagens por segundo** para números em uso nos dois |
| 7 | Conversas existentes | Sincronizam: **180 dias** de histórico, em três fases (§2.2) |
| 8 | Contatos | "All contacts with a WhatsApp number can be synchronized", via `smb_app_state_sync` |
| 9 | Mensagens futuras | Chegam nos dois lados. As enviadas pelo celular chegam ao parceiro por `smb_message_echoes` |
| 10 | Mídia | No histórico, só mensagens dos **últimos 14 dias** trazem o id do arquivo; o resto vem como `media_placeholder` sem id |
| 11 | Chamadas | Continuam no aplicativo, **não suportadas na Cloud API**. A Calling API é incompatível (§9) |
| 12 | Grupos | **Não sincronizam.** "messages that are part of a group chat will not be included" |
| 13 | Status/stories | **Não suportados** na Cloud API |
| 14 | Trocar de celular | "when a client onboarded via coexistence changes devices or re-registers their WhatsApp Business app, their Cloud API companion is automatically offboarded" |
| 15 | Reinstalar o Business App | Mesma coisa: re-registro = offboard automático |
| 16 | Reconectar | Automático: no novo aparelho aparece um opt-in **pré-marcado**; sem opt-out, "reonboarding runs automatically in the background and typically completes within a few minutes". **O histórico NÃO é ressincronizado** |
| 17 | Sair do Coexistence | Pelo próprio celular: **Configurações → Conta → Plataforma de Negócios → Desconectar conta.** A Deregister API **não** serve para número em coexistence |
| 18 | Rollback | É o item 17. Dispara `account_update` com evento `PARTNER_REMOVED` |

### 2.2 O histórico, em detalhe

- **180 dias**: "all messages sent or received within 180 days of the time
  when the business was onboarded onto Cloud API";
- **três fases** (`phase` 0/1/2): dia 0–1, dia 1–90, dia 90–180;
- os webhooks **chegam fora de ordem** — há `chunk_order` para reordenar e
  `progress` (0–100) para saber quando terminou;
- **uma única tentativa**: "Only one sync attempt allowed; must re-onboard
  if repeat needed";
- **grupos ficam de fora**;
- **mídia**: só os últimos 14 dias trazem asset id; o restante vem como
  `media_placeholder`;
- cada mensagem traz `history_context.status` com `DELIVERED`, `ERROR`,
  `PENDING`, `PLAYED`, `READ` ou `SENT`;
- se o negócio recusar compartilhar, vem o erro **2593109** — "History sync
  is turned off by the business from the WhatsApp Business App";
- **mensagens apagadas e de sistema: NÃO CONFIRMADO.** A referência não
  declara o comportamento.

### 2.3 O que muda no aplicativo depois do onboarding

**Desligado nos chats 1:1:** mensagens temporárias, ver uma vez,
localização em tempo real. **Listas de transmissão** viram somente leitura.

**Não existe na Cloud API** (continua só no aplicativo): grupos, chamadas,
catálogo, pedidos, status, mensagens de marketing do app, saudação,
ausência, respostas rápidas, **etiquetas**, personalização do perfil e
Canais.

**Aparelhos vinculados:** até 4 "companions"; **todos são desvinculados no
onboarding** e precisam ser religados. **WhatsApp para Windows e WearOS não
são suportados** — e mensagens enviadas por eles **não disparam webhook**.

> Este último ponto é um risco operacional real: se alguém do escritório
> responder pelo WhatsApp Desktop do Windows, **o ELO não fica sabendo.**

### 2.4 Sobre a lista de países da REVISÃO 1

A REVISÃO 1 listou Austrália, Japão, Nigéria, Filipinas, Rússia, Coreia do
Sul, África do Sul, Turquia, Suíça e EEE/UE/Reino Unido como excluídos.
**Não encontrei essa lista na documentação oficial do fluxo.** Ela pode
existir noutro lugar, pode ter mudado, ou pode nunca ter sido oficial.

Para o nosso caso (Brasil) não muda a decisão — mas **é preciso confirmar
com a Meta antes do onboarding**, e não tratar como resolvido.

### 2.5 Sobre "atividade recente"

Também não consta. **NÃO CONFIRMADO.**

---

## 3. Embedded Signup

- **App do tipo Business**, com **Advanced Access** aprovado em App Review
  antes de onboardar clientes;
- permissões: `whatsapp_business_management` e
  `whatsapp_business_messaging`; Solution Partners também precisam de
  `business_management` no system user;
- **versões**: a v2 **é descontinuada em 15/10/2026**; a documentação
  aponta a **v4** como a versão para onde migrar;
- **limites de onboarding**: 10 clientes por 7 dias, subindo para **200**
  depois de Business Verification + App Review + Access Verification;
- o fluxo devolve **WABA ID**, **business phone number ID** e um **code**
  trocável, servidor-a-servidor, por token de escopo de negócio;
- para Coexistence, o evento de sessão vem como
  `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING`, e **o registro do número é
  pulado** — ele já está registrado;
- "Business phone numbers already in use with the WhatsApp Business app are
  supported, but require you customize the flow".

---

## 4. Precisamos ser Tech Provider? — os dois cenários

### CENÁRIO A — o ELO só para o nosso escritório

| Caminho | Precisa ser parceiro? | Coexistence? |
|---|---|---|
| **A1. Cloud API direta, número novo** | **Não.** "Get Started" comum | ❌ não se aplica |
| **A2. Cloud API direta, migrando o número atual** | Não | ❌ o número **sai** do aplicativo |
| **A3. Coexistence** | **Sim** — o fluxo exige Embedded Signup, restrito a Tech Provider / Solution Partner / Tech Partner | ✅ |

**A conclusão que importa:** manter o número atual no celular **e** ter a
Cloud API exige o Coexistence, e o Coexistence exige Embedded Signup. Não
achei, na documentação oficial, caminho de Coexistence para um negócio
comum sobre o próprio número sem passar por esse fluxo.

Restam duas saídas honestas, e as duas são decisão sua:

- **virar Tech Provider** — a documentação descreve uma verificação "less
  extensive" que a de Solution Partner, e o Tech Provider **não** tem linha
  de crédito: a Meta cobra do cliente (que seria o próprio escritório);
- **ou contratar um BSP/Solution Partner**, que já tem o fluxo pronto.

### CENÁRIO B — o ELO vendido a outros escritórios

Aqui **não há dúvida**: cada escritório conecta o próprio número, e isso é
Embedded Signup por definição. Exige ser **Tech Provider** (ou Solution
Partner). Some-se:

- App Review com Advanced Access;
- Business Verification (e o limite de 10 → 200 clientes por 7 dias);
- multi-tenant de verdade — que o ELO **já tem** desde o MVP 1.0;
- guardar token **por WABA**, e não um token global.

**O ELO já está arquitetado para o Cenário B.** `tenantId` em toda tabela,
RLS forçado, projeção de clientes por tenant. O que falta é o papel na
Meta, não o software.

---

## 5. BSP — o que é, e quando é obrigatório

**BSP / Solution Partner** = Meta Business Partner certificado, com linha
de crédito, que fatura o cliente e oferece a plataforma pronta.

| | Obrigatório? |
|---|---|
| Cloud API no próprio número (número novo ou migrado) | **Não** |
| Coexistence | **Sim, ou ser Tech Provider** |
| Vender a outros escritórios | **Sim, ou ser Tech Provider** |

**Vantagens:** o fluxo de onboarding já existe, suporte, faturamento em
reais pelo parceiro, e alguém para acionar quando a Meta muda algo.

**Custos que aparecem:** mensalidade ou por número, markup sobre a tarifa
da Meta, cobrança por usuário/assento, e às vezes por conversa **além** do
que a Meta cobra.

**Lock-in, que é o risco real:** o número fica registrado na WABA sob o
parceiro. Trocar de parceiro é um procedimento — a documentação tem página
própria para migrar número entre Solution Partners — e a documentação
avisa que **linha de crédito compartilhada com um parceiro antigo pode dar
erro na troca**.

**Nenhum BSP foi escolhido, e nenhum foi contatado.**

---

## 6. Número de teste — o caminho seguro

Confirmado: ao começar com a Cloud API, a Meta cria **automaticamente** uma
WABA de teste e um **número de teste**, já registrado.

| | |
|---|---|
| Destinatários | **até 5**, cadastrados à mão |
| Custo | mensagens de teste **gratuitas** |
| Forma de pagamento | **não exigida** para enviar template com conta de teste |
| Limites | "relaxed messaging limits" |
| Token temporário | a documentação diz que "expires quickly"; **a duração exata é NÃO CONFIRMADO** |
| Token de produção | **system user token**, com `whatsapp_business_management`, `whatsapp_business_messaging` e `business_management` |

**É aqui que a implementação começa, e dá para ir longe:** webhook,
adapter, mídia, status, idempotência e as notificações do MVP 1.7 inteiras
— tudo sem encostar no número do escritório.

---

## 7. Webhooks — o que o ELO precisa assinar

| Campo | Quando dispara | Id para deduplicar |
|---|---|---|
| `messages` | mensagem recebida do cliente | `messages[].id` |
| `messages` (statuses) | `sent`, `delivered`, `read` — e `failed` | `statuses[].id` + `status` |
| `history` | após pedir a sincronização (Coexistence) | `history[].threads[].messages[].id` |
| `smb_app_state_sync` | contatos do aplicativo, e mudanças futuras | `phone_number` + `action` |
| `smb_message_echoes` | **mensagem enviada pelo celular** | `message_echoes[].id` |
| `account_update` | `PARTNER_REMOVED` e outros eventos de conta | evento |

### 7.1 `smb_message_echoes` — e a lacuna que ele tem

Tipos documentados: **`text`, `image`, `video`, `document`, `revoke`
(apagar, com `original_message_id`) e `edit` (editar)**.

**`audio` e `voice` NÃO aparecem nessa lista.** Se um funcionário mandar um
áudio pelo celular, **NÃO CONFIRMADO** se o ELO fica sabendo. É pergunta
para o teste em ambiente controlado (fase 1.8.4), não para adivinhar.

A referência também **não promete ordem nem deduplicação** — "No
deduplication or ordering guarantees are explicitly mentioned". Quem
garante somos nós.

### 7.2 Segurança do webhook

- **Verificação (GET):** `hub.mode=subscribe`, `hub.verify_token`
  (comparar com o que está no App Dashboard) e devolver `hub.challenge`;
- **Assinatura (POST):** cabeçalho **`X-Hub-Signature-256`**, no formato
  `sha256=<hex>`, que é **HMAC-SHA256 do payload com o App Secret**;
- a documentação diz "You don't have to validate the payload, but you
  should". **Para o ELO é obrigatório**: payload sem assinatura válida não
  entra no domínio.

---

## 8. Mídia

| Assunto | Oficial |
|---|---|
| URL de download | **válida por 5 minutos** |
| Áudio — MIME aceitos no envio | `audio/aac`, `audio/amr`, `audio/mpeg`, `audio/mp4`, `audio/ogg` (**só OPUS**) |
| Áudio — tamanho | **16 MB** |
| Voz (voice message) | `.ogg` com **OPUS**, **mono** |
| Recebido | o objeto `audio` traz `id`, `mime_type`, `sha256` e **`voice: true/false`** |
| `url` no webhook | campo `url` em implantação gradual desde **12/11/2025** — pode evitar o GET extra |
| Ícone de play | só aparece se o arquivo tiver **512 KB ou menos** |
| Status `played` | webhook de `played` para mensagens de voz **a partir de 17/03/2026** |
| Limites de imagem/documento/vídeo | **NÃO CONFIRMADO** nesta auditoria — a página consolidada não abriu |

O `voice: true/false` casa **exatamente** com a distinção `AUDIO` × `VOICE`
que o MVP 1.6 já criou (S48). O modelo não muda.

---

## 9. Chamadas — incompatível, e está escrito

A **WhatsApp Business Calling API** exige que "your business number is in
use with Cloud API (**not the WhatsApp Business app**)".

**Ou seja: Calling API e Coexistence não convivem no mesmo número.** Além
disso, exige limite diário de ao menos 2.000 destinatários únicos, e
chamadas iniciadas pelo negócio não estão disponíveis em EUA, Canadá,
Egito, Vietnã e Nigéria.

Para o ELO: chamadas continuam **fora de escopo** (já estavam), e agora com
um motivo estrutural, não só de prioridade.

---

## 10. Templates e a janela de 24 horas

- A janela de atendimento abre quando **o usuário** manda mensagem (ou
  liga) e dura **24 horas**; nova mensagem dele **reinicia** o contador;
- **dentro da janela**: qualquer mensagem livre, sem template;
- **fora da janela**: **só template**;
- categorias: **`authentication`, `marketing`, `utility`**;
- revisão automática, "can take up to 24 hours";
- status: `APPROVED`, `REJECTED`, `IN_REVIEW`, `PAUSED`, `DISABLED`;
- a Meta **não traduz** nada: o texto e os exemplos são nossos;
- há **janela de 72 h** de ponto de entrada gratuito para quem chega por
  anúncio Click-to-WhatsApp ou botão de Página.

**No Coexistence há uma sutileza importante:** mensagens enviadas pelo
**aplicativo** não estão sujeitas à janela de 24 h e **não afetam** a
tarifação nem as janelas da Cloud API.

---

## 11. Custos — e a mudança que muda o cálculo

Modelo vigente: **por mensagem**, desde **01/07/2025**, por categoria de
template e país do destinatário.

Hoje:

- **não-template (serviço) dentro da janela: grátis** desde 01/11/2024;
- **template `utility` dentro da janela: grátis** desde 01/07/2025;
- `marketing` sempre cobrado; `authentication` e `utility` fora da janela,
  cobrados.

### ⚠️ O que vem por aí, e é o dado mais relevante desta auditoria

| Data | O que muda |
|---|---|
| **01/08/2026** (já em vigor) | mensagens do **Meta Business Agent** passam a ser cobradas por token: **US$ 2,00 por 1M de tokens** |
| **01/10/2026** | **mensagens de serviço (não-template) passam a ser COBRADAS**, "consistent with how Meta charges for template messages" |
| **01/10/2026** | **templates `utility` dentro da janela** também voltam a ser cobrados |

A regra: "Any non-template message is charged as of October 1, 2026."

**Isto atinge o uso principal do ELO em cheio.** Responder cliente dentro da
janela — que é 99% do que um escritório de contabilidade faz — é
**gratuito hoje** e **passa a ser cobrado em menos de dois meses**.

> **Ressalva de fonte:** a página específica de mensagens não-template
> afirma isso com todas as letras; a página geral de *Pricing Updates* não
> repetia o item na leitura desta auditoria. **Confirmar o valor por
> mensagem para o Brasil antes de projetar custo** — a tabela de tarifas do
> Brasil é publicada à parte.

**Brasil:** desde **01/07/2026** dá para criar WABA faturada em **BRL**,
pela Facebook Brasil. Quem é elegível **precisa migrar todas as WABAs para
BRL até 30/06/2027**, sob pena de interrupção de entrega a partir de
01/07/2027.

---

## 12. Grupos — a resposta é não, e sem contorno

"Messages that are part of a group chat will not be included" no histórico,
e grupos constam como **não suportados** na Cloud API.

**Para escrever na tela, sem rodeio:**

> Grupos continuam no aplicativo e não aparecem no ELO.

Nenhum contorno não oficial será construído.

---

## 13. Perfil da empresa — o que o cliente vê

O que aparece para o cliente é o **perfil do número**: nome de exibição,
foto, descrição e demais campos do perfil empresarial — **do escritório**.

No Coexistence, a personalização do perfil **continua no aplicativo** (está
na lista de recursos não suportados na Cloud API).

Isso confirma a decisão **D1** do MVP 1.3: o contato é o escritório, e a
pessoa aparece **dentro** da mensagem ("Aline • Fiscal"), nunca como
remetente. Não haverá perfil de WhatsApp por funcionário.

---

## 14. O que ficou NÃO CONFIRMADO

1. Países/regiões onde o Coexistence está disponível;
2. requisito de "atividade recente" do número;
3. duração exata do token temporário de teste;
4. se `audio`/`voice` enviados pelo celular chegam em `smb_message_echoes`;
5. tratamento de mensagens **apagadas** e **de sistema** no `history`;
6. limites de tamanho de imagem, documento e vídeo;
7. tarifa por mensagem no Brasil, em reais, após 01/10/2026;
8. se um número já ligado a outro provedor pode entrar direto no
   Coexistence (a documentação só fala de erro por linha de crédito
   compartilhada).

Nenhum desses buracos foi preenchido com fonte não oficial.

---

## Fontes

Toda a auditoria saiu destas páginas oficiais:

- [Onboard WhatsApp Business app users (Coexistence)](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users/)
- [Onboarding WhatsApp Business app users — fluxo customizado](https://developers.facebook.com/docs/whatsapp/embedded-signup/custom-flows/onboarding-business-app-users/)
- [Reconnect offboarded coexistence clients](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/reconnect-offboarded-coexistence-clients/)
- [Embedded Signup — overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview/)
- [Solution Providers / Tech Providers — overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/overview)
- [Get Started — Cloud API](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started)
- [Webhook `history`](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/reference/history/)
- [Webhook `smb_message_echoes`](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/reference/smb_message_echoes/)
- [Webhook `smb_app_state_sync`](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/reference/smb_app_state_sync/)
- [Webhook `messages` — áudio](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/audio/)
- [Webhooks — componentes e statuses](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components/)
- [Graph API Webhooks — verificação e `X-Hub-Signature-256`](https://developers.facebook.com/docs/graph-api/webhooks/getting-started)
- [Mensagens de áudio](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/audio-messages)
- [Media API](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/media/media-api)
- [Templates — fundamentos](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview)
- [Pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [Pricing — mensagens não-template (mudança de 01/10/2026)](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages)
- [Pricing updates](https://developers.facebook.com/docs/whatsapp/pricing/updates-to-pricing/)
- [Calling API](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling)
