# Elo — decisões técnicas antes do MVP 1

Resposta aos itens A–I. **Nada implementado.** Aguardando aprovação.
Escrito em **06/08/2026**.

---

## A. "Exportar conversas (irreversível)" — correção minha

**Eu escrevi errado e criei alarme onde não havia.** A exportação **não é
irreversível**. Quem é irreversível é a **migração do número**, que é outro ato.

| Pergunta | Resposta |
|---|---|
| Quais conversas | As do celular do escritório, no aplicativo WhatsApp |
| De qual sistema | **Do aplicativo WhatsApp.** Não do Fiscale, não de nada que eu toque |
| Para onde | Google Drive ou e-mail, pela função de exportar do próprio app |
| Procedimento | Manual, **feito por você no celular**. Eu não executo nem tenho acesso |
| Por que eu disse irreversível | **Erro meu de redação.** A exportação é cópia: não altera, não apaga, não move nada |
| Altera algo existente | **Não.** É leitura pura |
| Envolve WhatsApp | Sim, o aplicativo — nada além disso |
| Envolve o histórico atual | Lê. Não modifica |
| **É necessária para o MVP** | **Não.** Nenhuma etapa do MVP depende dela |

**O que é de fato irreversível:** migrar o número para a Cloud API. Depois disso
o número **para de funcionar no aplicativo** e a exportação deixa de existir.

**Conclusão:** dá para construir e testar o MVP inteiro **sem tocar no número do
escritório** (item F). A decisão de migrar fica para o fim, e é opcional.

**Nada será exportado, migrado ou alterado sem sua autorização explícita.**

---

## B. Ponte Fiscale ↔ Elo — especificação

`nfse/backend/elo_ponte.py` — **somente leitura no MVP**.

### Endpoints

| Método | Rota | Devolve |
|---|---|---|
| GET | `/api/elo/cliente?cnpj=` | nome, CNPJ, regime, município, IM, e-mail, telefone |
| GET | `/api/elo/cliente/resumo?cnpj=` | prévia do DAS, notas do mês, competência, vencimento do certificado |
| GET | `/api/elo/pendencias?cnpj=` | meses faltando no RBT12, anexo por definir |
| GET | `/api/elo/saude` | `{ok: true, versao}` — sonda |

### Dados permitidos
Razão social · CNPJ · regime · município · IE/IM · e-mail/telefone de contato ·
valores agregados (prévia do DAS, contagem de notas) · datas de vencimento.

### Dados proibidos — nunca saem
Certificado `.pfx` · senha de certificado · blob DPAPI · caminho de arquivo local ·
XML de nota · conteúdo de documento fiscal · credencial de prefeitura ·
`usuarios.json` · qualquer segredo.

### Autenticação e proteção
- **Escuta só em `127.0.0.1`** — não aceita conexão da rede, nem do VPS
- Token estático longo em `elo_ponte.token` (fora do repositório), comparado com `secrets.compare_digest`
- O VPS **não chama a ponte**: quem chama é o navegador do usuário, que está na rede do escritório, e o resultado vai para a tela — nunca ao banco do Elo
- Rate limit 60 req/min por token
- Timeout de 5 s

### Erros e Fiscale offline
| Situação | Resposta |
|---|---|
| CNPJ não cadastrado | `404` + `{erro: "cliente não encontrado"}` |
| Token inválido | `401`, sem detalhe |
| Fiscale desligado | O Elo mostra *"dados fiscais indisponíveis — Fiscale offline"* e **continua funcionando**. A conversa nunca depende do Fiscale estar no ar |

### Logs
Registra `cnpj`, `endpoint`, `hora`, `resultado`. **Nunca** o conteúdo devolvido.

---

## C. Sessão e login compartilhado

### Comparação pedida

| | Prós | Contras | Veredito |
|---|---|---|---|
| **A. SQLite** | zero infra nova, arquivo único, atômico, sobrevive a reinício | não serve a dois servidores | ✅ **para o Fiscale** |
| **B. PostgreSQL** | robusto, auditável | instalar banco na máquina local só para sessão é exagero | ❌ no Fiscale · ✅ no Elo (auditoria) |
| **C. Redis** | rápido, TTL nativo, já necessário para presença e Socket.IO | mais um serviço | ✅ **para o Elo** |
| **D. Stateless assinada** | nada para guardar | **não dá para revogar** — "encerrar sessão" vira mentira | ❌ como sessão |
| **E. Cookie assinado + servidor** | — | é a A ou a C com outro nome | — |

**Decisão: contextos diferentes, respostas diferentes.**
- **Fiscale (local, 1–3 pessoas):** SQLite. Menos invasivo, resolve o reinício.
- **Elo (VPS, 15 pessoas):** Redis para sessão ativa + Postgres para auditoria.

### Token de transição

**Só para atravessar do Fiscale ao Elo. Nunca é a sessão do Elo.**

```
alg  EdDSA (Ed25519); RS256 aceitável
     Fiscale guarda a chave PRIVADA; Elo só a PÚBLICA.
     O VPS, mais exposto, não consegue emitir token.

iss  fiscale.local
aud  elo
sub  <id do usuário>
iat  emissão
exp  iat + 90 s
jti  uuid v4
tid  <tenant_id>
scp  papéis (ex.: ["atendimento"])
```

| Requisito | Como |
|---|---|
| Replay | `jti` gravado no Redis com TTL = exp; segundo uso é rejeitado |
| Uso único | O token vale **uma troca**; o Elo devolve cookie próprio e o queima |
| Rotação de chave | Par novo a cada 90 dias; `kid` no cabeçalho; duas chaves válidas na virada |
| Logout | Apaga a sessão do Redis; o token já expirou há muito |
| Revogação | Sessão no Redis — apagou, morreu. Por isso não usamos stateless |
| Relógio | Tolerância de 30 s (`leeway`); NTP nos dois lados |
| Expirada | `401` → volta ao login do Fiscale |
| HTTPS | Obrigatório. Sem TLS o token não é emitido |

**Vida do token: 90 segundos.** É bilhete de entrada, não crachá.

---

## D. Schema Prisma inicial — MVP

Só as 14 entidades pedidas. Nada de fases futuras.

```prisma
model Tenant {
  id        String   @id @default(uuid())
  nome      String
  criadoEm  DateTime @default(now())
  usuarios  User[]
  conversas Conversation[]
}

model User {
  id         String   @id @default(uuid())
  tenantId   String
  tenant     Tenant   @relation(fields: [tenantId], references: [id])
  nome       String
  email      String
  papel      String   @default("atendimento")
  ativo      Boolean  @default(true)
  criadoEm   DateTime @default(now())
  identidades UserIdentity[]
  @@unique([tenantId, email])
  @@index([tenantId])
}

// Liga o usuário do Elo ao usuário do Fiscale. Senha NÃO mora aqui:
// quem autentica é o Fiscale.
model UserIdentity {
  id          String @id @default(uuid())
  userId      String
  user        User   @relation(fields: [userId], references: [id])
  provedor    String            // "fiscale"
  provedorUid String
  @@unique([provedor, provedorUid])
}

model Department {
  id       String @id @default(uuid())
  tenantId String
  nome     String
  @@unique([tenantId, nome])
  @@index([tenantId])
}

// Canal = número de WhatsApp. Já plural: amanhã entra outro número.
model Channel {
  id         String @id @default(uuid())
  tenantId   String
  tipo       String            // "whatsapp"
  identidade String            // telefone E.164
  wabaId     String?
  ativo      Boolean @default(true)
  @@unique([tenantId, tipo, identidade])
  @@index([tenantId])
}

// A PESSOA do outro lado. fiscaleCnpj é só referência —
// o cadastro fiscal continua no Fiscale.
model Contact {
  id          String  @id @default(uuid())
  tenantId    String
  telefone    String
  nome        String?
  fiscaleCnpj String?
  bloqueado   Boolean @default(false)
  @@unique([tenantId, telefone])
  @@index([tenantId, fiscaleCnpj])
}

model Conversation {
  id              String   @id @default(uuid())
  tenantId        String
  tenant          Tenant   @relation(fields: [tenantId], references: [id])
  channelId       String
  contactId       String
  status          String   @default("NOVO")
  // NOVO | EM_ATENDIMENTO | AGUARDANDO_CLIENTE | AGUARDANDO_ESCRITORIO | RESOLVIDO
  departmentId    String?
  responsavelId   String?
  ultimaEntradaEm DateTime?   // controla a janela de 24 h da Meta
  ultimaMsgEm     DateTime?
  naoLidas        Int      @default(0)
  criadaEm        DateTime @default(now())
  mensagens       Message[]
  @@unique([tenantId, channelId, contactId])
  @@index([tenantId, status, ultimaMsgEm])
}

model ConversationParticipant {
  id             String @id @default(uuid())
  tenantId       String
  conversationId String
  userId         String
  papel          String @default("observador")
  @@unique([conversationId, userId])
  @@index([tenantId])
}

model Message {
  id             String   @id @default(uuid())
  tenantId       String
  conversationId String
  conversation   Conversation @relation(fields: [conversationId], references: [id])
  waId           String?  @unique     // idempotência: a Meta reenvia por 7 dias
  direcao        String              // entrada | saida
  autorId        String?
  tipo           String   @default("texto")
  texto          String?
  respondendoA   String?
  enviadaEm      DateTime
  entregueEm     DateTime?
  lidaEm         DateTime?
  apagadaEm      DateTime?           // marca; o conteúdo PERMANECE
  apagadaPor     String?
  payloadCru     Json?               // rede de segurança: grava antes de interpretar
  anexos         MessageAttachment[]
  @@index([tenantId, conversationId, enviadaEm])
}

model MessageAttachment {
  id         String  @id @default(uuid())
  tenantId   String
  messageId  String
  message    Message @relation(fields: [messageId], references: [id])
  tipoMime   String
  nome       String?
  tamanho    Int?
  duracaoSeg Int?
  caminho    String
  sha256     String?
  @@index([tenantId, messageId])
}

model ConversationAssignment {
  id             String   @id @default(uuid())
  tenantId       String
  conversationId String
  deUserId       String?
  paraUserId     String
  motivo         String?
  em             DateTime @default(now())
  @@index([tenantId, conversationId, em])
}

model ConversationTag {
  id             String @id @default(uuid())
  tenantId       String
  conversationId String
  nome           String
  cor            String?
  @@unique([conversationId, nome])
  @@index([tenantId])
}

model InternalNote {
  id             String   @id @default(uuid())
  tenantId       String
  conversationId String
  autorId        String
  texto          String
  criadaEm       DateTime @default(now())
  @@index([tenantId, conversationId])
}

model AuditLog {
  id        String   @id @default(uuid())
  tenantId  String
  userId    String?
  acao      String
  alvoTipo  String?
  alvoId    String?
  metadados Json?     // NUNCA o conteúdo da mensagem
  ip        String?
  em        DateTime @default(now())
  @@index([tenantId, em])
}
```

---

## E. Multitenancy

**Desde a primeira migration, sem exceção.**

1. **`tenantId` em toda tabela de negócio** — inclusive nas filhas, mesmo sendo
   derivável pelo pai. Redundância proposital: permite filtrar sem `join`.
2. **Row Level Security do PostgreSQL** como rede de segurança:
   ```sql
   ALTER TABLE "Conversation" ENABLE ROW LEVEL SECURITY;
   CREATE POLICY tenant_iso ON "Conversation"
     USING (tenant_id = current_setting('app.tenant_id')::uuid);
   ```
   A cada requisição, `SET LOCAL app.tenant_id`. Assim, **mesmo com bug na
   consulta**, o banco não devolve dado de outro tenant.
3. **Contra IDOR:** id é UUID (não sequencial) e toda busca por id filtra também
   por `tenantId`. Nunca `findUnique({id})` sozinho.
4. **Teste automatizado obrigatório:** dois tenants, tentar ler o do outro por
   todos os endpoints. Falhou o teste, quebrou o build.

---

## F. Meta — o que dá para fazer **sem tocar no número atual**

Esta é a parte que resolve sua preocupação.

### ✅ Seguro agora — não interrompe nada

| Passo | Interrompe o WhatsApp atual? |
|---|---|
| 1. Conta Meta for Developers | Não |
| 2. Business Portfolio (Meta Business) | Não |
| 3. Verificação da empresa — CNPJ, comprovante | Não · **leva dias, comece já** |
| 4. Criar WABA (WhatsApp Business Account) | Não |
| 5. Criar aplicativo com produto WhatsApp | Não |
| 6. **Usar o número de TESTE que a Meta dá de graça** | **Não** |
| 7. Configurar webhook apontando para o ambiente de dev | Não |
| 8. Gerar token temporário (24 h) para testar | Não |

> A Meta fornece um **número de teste gratuito** que envia e recebe de até 5
> destinatários cadastrados. **O MVP inteiro se constrói e se testa nele**, sem
> encostar no número do escritório.

### ⛔ Só no fim, com sua autorização

| Passo | Efeito |
|---|---|
| Adicionar o número real à WABA | **O número para de funcionar no aplicativo** |
| Migrar o número do escritório | Irreversível na prática |
| Token permanente de sistema | Depois de tudo validado |

**Recomendação:** número de teste no MVP → número novo em produção → migrar o
principal só se quiser, no futuro, e com exportação feita antes.

---

## G. VPS — requisitos

| | Desenvolvimento | Homologação | Produção |
|---|---|---|---|
| Onde | sua máquina, Docker | VPS pequeno | VPS |
| CPU | — | 1 vCPU | **2 vCPU** |
| RAM | — | 2 GB | **4 GB** |
| Disco | — | 20 GB | **40 GB SSD** + storage de mídia |
| SO | — | Ubuntu 24.04 LTS | Ubuntu 24.04 LTS |
| Banco | Postgres em container | Postgres 16 | **Postgres 16** |
| Redis | container | sim | **sim** |
| Proxy | — | Caddy | **Caddy** (TLS automático) |
| SSL | — | Let's Encrypt | Let's Encrypt |
| Object storage | disco local | disco local | disco → S3/R2 acima de 50 GB |
| Backup | — | diário | **diário + teste mensal de restauração** |
| Custo/mês | R$ 0 | R$ 20 | **R$ 40–60** |

O webhook da Meta **exige HTTPS com certificado válido** — daí o domínio e o Caddy.

---

## H. Ações seguras de fazer agora

1. **Verificar a empresa na Meta** — leva dias, não interrompe nada, é o
   caminho crítico
2. Criar Business Portfolio, WABA e aplicativo
3. Ativar o número de teste gratuito
4. Subir o ambiente de desenvolvimento local com Docker
5. Aplicar o schema Prisma **num banco vazio de desenvolvimento**
6. Escrever os testes de isolamento entre tenants
7. Registrar um domínio para o webhook
8. Definir a política de retenção e o aviso de gravação nos termos

## I. O que evitar por enquanto

1. ❌ **Exportar conversas** — desnecessário para o MVP
2. ❌ **Adicionar o número real à WABA** — para o aplicativo
3. ❌ **Contratar VPS de produção** — só depois do MVP rodando local
4. ❌ **Mexer em `SESSOES`** do Fiscale — decidido, mas não urgente
5. ❌ **Criar `elo_ponte.py`** — aguardando aprovação da especificação
6. ❌ **Token permanente da Meta** — o temporário basta para desenvolver
7. ❌ **Alterar qualquer arquivo fiscal** existente
8. ❌ **Object storage externo** — disco local resolve por muito tempo

---

---

# REVISÃO 1 — 06/08/2026

Duas premissas minhas foram derrubadas pela sua revisão. Ambas mudam arquitetura.

## R1. Coexistence existe — o número NÃO precisa sair do aplicativo

Eu afirmei que adicionar o número à plataforma o tiraria do app. **Isso deixou de
ser verdade.** A Meta lançou o **WhatsApp Business App Coexistence** em maio/2025,
e ele virou o caminho padrão de entrada para quem já usa o app.

**Ressalva de fonte:** não consegui abrir a documentação oficial da Meta; o que
encontrei foram provedores oficiais (BSPs) descrevendo o fluxo. Os pontos abaixo
estão consistentes entre eles, mas **precisam ser confirmados com o BSP escolhido
antes de qualquer ação.**

### Elegibilidade

| Requisito | Nosso caso |
|---|---|
| Número no **WhatsApp Business App** (não o WhatsApp comum) | ⚠️ **confirmar** — se for o app pessoal, não serve |
| Número com atividade recente | ✅ uso diário |
| Não estar ligado a outro provedor de API | ✅ |
| País suportado | ✅ Brasil não está na lista de exclusão |
| Entrada via **BSP com Embedded Signup** | ⚠️ **muda o plano** — ver abaixo |

Países excluídos: Austrália, Japão, Nigéria, Filipinas, Rússia, Coreia do Sul,
África do Sul, Turquia, Suíça e todo o EEE/UE/Reino Unido.

### O que muda no plano

**Coexistence exige um BSP.** Não dá pelo autoatendimento direto da Cloud API.
Isso acrescenta um intermediário e um custo mensal que eu não tinha previsto.

### Limitações conhecidas

- **Grupos não sincronizam** para a API — continuam só no aplicativo
- A primeira mensagem de um contato novo pode não chegar pelo webhook
- O histórico sincroniza (é o argumento de venda do recurso), mas o alcance
  varia por provedor — **confirmar antes de contar com isso**
- Reverter não é um botão: mexe no registro do número na Meta

### Comparação

| | A · Número de teste | B · Coexistence | C · Migração completa |
|---|---|---|---|
| App continua funcionando | — | ✅ | ❌ |
| Risco ao número do escritório | nenhum | baixo | alto |
| Precisa de BSP | não | **sim** | não |
| Custo | zero | BSP + Meta | Meta |
| Histórico | — | sincroniza (confirmar) | perde |
| Serve para desenvolver | ✅ | — | — |

**Decisão: A no desenvolvimento, B na produção, C nunca — salvo motivo real.**
É exatamente sua preferência, e agora com fundamento.

**Consequência boa:** a exportação de conversas fica ainda menos necessária.

## R2. A ponte por localhost está descartada

Você está certo. HTTPS na internet chamando `127.0.0.1` esbarra em **Private
Network Access**, mixed content, CORS e CSRF contra serviço local — e o Chrome
vem apertando isso. Construir sobre esse comportamento é construir sobre areia.

### Comparação

| | Segurança | Instalação | Firewall/NAT | PC desligado | Veredito |
|---|---|---|---|---|---|
| A · Browser → localhost | frágil | zero | ok | quebra | ❌ **descartada** |
| B · Agente local com porta aberta | exige abrir porta | média | ruim | quebra | ❌ |
| C · **Fiscale conecta SAINDO** | ✅ sem entrada | baixa | ✅ atravessa NAT | degrada bem | ✅ |
| D · **Sincronizar projeção** | ✅ mínimo necessário | baixa | ✅ | ✅ **funciona** | ✅ |
| E · API intermediária | ok | alta | ok | depende | ⚠️ |
| F · **C + D** | ✅ | baixa | ✅ | ✅ | ✅ **recomendada** |

### Arquitetura escolhida — C + D

```
Fiscale (escritório)                     Elo (VPS)
   │                                         │
   │  1. sobe → autentica com chave própria  │
   │  2. envia PROJEÇÃO de clientes ──────►  │  grava ExternalCustomerReference
   │  3. mantém canal aberto (opcional) ───► │  responde consulta ao vivo
   │                                         │
   └── NUNCA aceita conexão de entrada ──────┘
```

**Só sai conexão do Fiscale.** Nada de porta aberta, nada de NAT, nada de
depender do navegador. Se o Fiscale estiver fechado, o Elo usa a última projeção.

## R3. Você tem razão sobre "não duplicar"

Fui rígido demais. **Fonte da verdade** e **projeção de leitura** são coisas
diferentes — e sem projeção o Elo não funciona às 23h com o escritório fechado.

Tabela nova, exatamente como você propôs:

```prisma
model ExternalCustomerReference {
  id               String   @id @default(uuid())
  tenantId         String
  fiscaleCustomerId String
  displayName      String
  tradeName        String?
  documentNumber   String            // CNPJ, só dígitos
  email            String?
  phone            String?
  active           Boolean  @default(true)
  syncedAt         DateTime
  sourceVersion    String            // hash do registro na origem
  @@unique([tenantId, fiscaleCustomerId])
  @@index([tenantId, documentNumber])
  @@index([tenantId, phone])
}
```

**O que sincroniza:** nome, nome fantasia, CNPJ, e-mail, telefone, ativo.
**O que NUNCA sincroniza:** regime, apuração, DAS, XML, IE/IM, certificado,
senha, pendência fiscal — isso continua só no Fiscale e só aparece na tela
quando ele está no ar.

**Como:** o Fiscale envia a projeção ao subir e a cada alteração no cadastro de
clientes. `sourceVersion` é o hash do registro — só manda o que mudou.

## R4. O Elo não depende do Fiscale para existir

Requisito aceito e incorporado. O núcleo de comunicação — webhook, mensagem,
mídia, conversa, atribuição — **não chama o Fiscale em nenhum ponto**.

| Fiscale ligado | Fiscale desligado |
|---|---|
| Painel do cliente completo: regime, prévia do DAS, pendências | Nome e CNPJ pela projeção; o painel fiscal mostra *"Fiscale offline"* |

Receber, salvar, identificar, atribuir e responder **funcionam sempre**.

## R5. Identificação do contato — ordem

1. `Contact` do Elo
2. `ExternalCustomerReference` por telefone
3. `ExternalCustomerReference` por CNPJ, quando o contato informa
4. Nada → **"Contato não identificado"** + `[Vincular a cliente existente]`

Criar cliente no Fiscale fica fora do MVP — a ponte é somente leitura.

---

## Resumo das decisões

| Tema | Decisão |
|---|---|
| Exportação | **Não é necessária.** Erro meu ter dito irreversível |
| Sessão Fiscale | SQLite |
| Sessão Elo | Redis + auditoria em Postgres |
| Token de troca | EdDSA, 90 s, uso único, `jti` no Redis |
| Ponte | ~~`127.0.0.1`, somente leitura~~ → substituída pela arquitetura C+D (revisão 1, R2) |
| Multitenancy | `tenantId` + Row Level Security + teste no build |
| Meta | Número de **teste** no MVP; número real só no fim |
| Criptografia | TLS + repouso. **Sem E2EE, e sem chamar de E2EE** |

---

---

# REVISÃO 2 — 09/08/2026 · decisões surgidas na implementação do MVP 1.0

Nada aqui derruba decisão anterior. São escolhas que só apareceram quando o
código foi escrito e executado. A fundação está documentada em
[elo/README.md](elo/README.md).

## S1. Contexto do tenant é transacional, não de sessão

A seção E dizia "a cada requisição, `SET LOCAL app.tenant_id`". Na
implementação isso precisou virar regra explícita, porque `SET LOCAL` só
existe **dentro de transação** — fora dela o Postgres avisa e ignora.

Decisão: todo acesso a dado de tenant passa por `withTenant()`, que abre
transação e executa `set_config('app.tenant_id', $1, true)`. O terceiro
argumento `true` faz o banco reverter o valor no `COMMIT` **e** no
`ROLLBACK`. Sem isso, uma conexão devolvida ao pool carregaria o tenant
anterior — o vazamento clássico de RLS com pool, que não dá erro nem log.

`SET LOCAL` não se escreve em nenhum outro arquivo do projeto.

## S2. Três papéis de banco, não dois

A seção E previa `elo_app` não-superusuário. Faltava o papel das migrations:
Prisma precisa de DDL, e rodar migration com o papel da aplicação obrigaria a
aplicação a ter DDL.

| Papel | Uso | Poderes |
|---|---|---|
| `postgres` | bootstrap, uma vez | tudo |
| `elo_owner` | `prisma migrate` | DDL + `CREATEDB` (shadow db). Sem SUPERUSER, sem BYPASSRLS |
| `elo_app` | runtime | só DML, e só onde o RLS já está ligado |

`FORCE ROW LEVEL SECURITY` vale também para o `elo_owner` — há teste que
confirma que nem o dono das tabelas atravessa.

## S3. Sem `ALTER DEFAULT PRIVILEGES`

Tentação óbvia: conceder acesso automático ao `elo_app` em tabela nova.
Recusada. Uma migration futura que criasse tabela e esquecesse de ligar RLS
teria a tabela **legível e desprotegida** no mesmo instante.

Em vez disso, `elo_apply_rls()` — reexecutável — liga RLS, força, cria a
política e **só então** concede. Toda migration nova termina chamando-a.
Esquecer quebra `tests/rls-guard.test.ts`, não a produção.

## S4. Criar tenant não precisa de conexão privilegiada

A política de `tenants` é `id = app_tenant_id()`, o que à primeira vista
impediria a criação do primeiro registro. A saída não foi abrir exceção para
o `elo_owner`: `provisionTenant()` gera o uuid **antes**, abre a transação já
com ele fixado, e o `WITH CHECK` aprova. O `elo_owner` continua sendo apenas
o papel das migrations.

## S5. Redis fora do MVP 1.0

Decidido na seção C que o Elo usa Redis para sessão, presença e Socket.IO —
e isso continua valendo. Mas nada no MVP 1.0 o consome. Subir um serviço que
ninguém usa mascara dependência e consome memória. Entra no MVP 1.1, junto
com a sessão.

## S6. Schema com 3 entidades no MVP 1.0

A seção D define 14 entidades. A fundação implementou **Tenant, User e
Contact** — o suficiente para provar isolamento com uma entidade de negócio
real. As outras 11 entram com as fases que as usam (1.1 a 1.5). Tabela sem
leitor é dívida, não progresso.

## S7. Versões: TypeScript 5.9 e ESLint 9

Node 24.19 é LTS e atende Prisma 7 (`^20.19 || ^22.12 || >=24`) e Next 16
(`>=20.9`). Duas versões foram conscientemente **não** atualizadas para a
mais recente:

- **TypeScript 5.9.3**, não 7.0.2 — o `typescript-eslint` que vem no
  `eslint-config-next` 16 ainda tem o 5.x como alvo.
- **ESLint 9.39.5**, não 10.8.1 — o `eslint-plugin-react` do
  `eslint-config-next` declara peer `^9.7`; com o 10, o npm emitia
  `ERESOLVE`. Warning de peer é aviso de combinação não testada, não ruído.

Nenhum warning de engine ou de peer permaneceu na instalação final.

## S8b. O `elo/schema.sql` original foi aposentado

O arquivo de 7 tabelas com `SERIAL` e sem `tenant_id` é anterior à decisão de
multitenancy e ao Prisma. Foi movido para `elo/prisma/legacy/` junto com a
migration `0001_rls_multitenancy.sql` pré-scaffold, cuja lógica foi
preservada quase inteira — o que mudou foi: nomes em snake_case (o Prisma
gera `tenant_id`, e o SQL fica sem aspas), o loop virou função reexecutável
`elo_apply_rls()`, e os `GRANT` passaram a acontecer só junto com o RLS (S3).
Os dois ficam como referência histórica. Nenhum roda.

---

---

# REVISÃO 3 — 09/08/2026 · identidade, sessão e RBAC (MVP 1.1)

Uma decisão aqui **derruba** a modelagem da seção D. As outras são novas.

## S9. Identity + Membership no lugar de User — MUDANÇA DE ARQUITETURA

A seção D definia `User` com `tenantId`. Isso torna impossível a mesma
pessoa participar de dois escritórios sem virar duas linhas sem parentesco:
nenhuma forma de dizer que é a mesma Aline, e cada uma com o seu cadastro.

Enquanto o banco está vazio, isso custa uma migration. Com conversa e
mensagem em produção, custaria migração de dados vivos com o sistema no ar.
O momento de corrigir era agora.

```
Identity ──< IdentityProvider     a PESSOA (global) e de onde ela vem
    │
    └──< Membership >── Tenant    a pessoa DENTRO de um escritório
              │
              ├──< Session
              └──< DepartmentMembership >── Department
```

`Membership` é o `User` da seção D com o nome certo — e é nele que mora o
papel, porque quem tem poder é o vínculo, não a pessoa. `UserIdentity` da
seção D virou `IdentityProvider`, com a mesma função.

**O que isso obriga:** sessão, departamento e auditoria apontam para
Membership, nunca para Identity. E as duas tabelas globais precisaram de
uma forma de RLS diferente — ver S10.

Testado: a mesma pessoa em dois tenants, com papéis diferentes, duas sessões
vivas ao mesmo tempo, e nenhuma enxergando a outra.

## S10. RLS por associação nas tabelas globais

`identities` e `identity_providers` não têm `tenant_id` — a pessoa é uma só.
Não dava para aplicar a política de sempre.

A política passou a ser o vínculo:

```sql
USING (EXISTS (SELECT 1 FROM memberships m
                WHERE m.identity_id = identities.id
                  AND m.tenant_id = app_tenant_id()))
```

Quem não tem membership no tenant corrente não existe para ele — nem pelo
id, nem pelo e-mail, que é único no sistema inteiro. O `INSERT` é liberado
às cegas, o que não abre nada: inserir não lê, e a linha continua invisível
até haver vínculo.

Consequência prática: criar identidade usa SQL sem `RETURNING`, porque o
Prisma sempre usa `RETURNING` e ler de volta ali ainda seria proibido. E
colocar alguém que já existe num segundo tenant exige informar o
`identityId` — procurar por e-mail de um tenant que não conhece a pessoa
seria exatamente o vazamento que a política evita.

## S11. Anti-replay no Postgres, sem Redis

A seção C previa `jti` no Redis com TTL. Reavaliado e trocado, por três
motivos:

1. São alguns tokens por dia. Subir, monitorar e fazer backup de um segundo
   banco para guardar dezenas de linhas é custo sem ganho.
2. O Postgres já está no ar com garantia transacional.
3. **O argumento decisivo é técnico:** a atomicidade vem de graça.

```sql
INSERT INTO consumed_exchange_tokens (jti, expires_at)
VALUES ($1, $2) ON CONFLICT DO NOTHING
```

Um comando. A implementação ingênua — `SELECT` e depois `INSERT` — deixa
duas requisições simultâneas passarem as duas, porque ambas consultam antes
de qualquer uma gravar. Aqui quem decide é o índice, dentro do banco.

A tabela é **somente-escrita**: o `elo_app` não tem `SELECT`. Detectar
replay nunca precisou ler, e sem leitura ninguém enumera token alheio.

Duas descobertas do Postgres, ambas viraram migration com o motivo escrito:
`DELETE ... WHERE` exige privilégio de leitura das colunas do filtro; e
quando o `WHERE` referencia colunas, as políticas de SELECT também valem
num DELETE. A faxina ganhou o mínimo para funcionar — leitura só de
`expires_at`, e só de linhas já expiradas.

**O Redis continua previsto** para presença e Socket.IO (MVP 1.5+). O que
foi descartado é usá-lo *para isto*.

## S12. O cookie do Elo carrega o tenant — e por que não é contradição

Para ler a tabela de sessões é preciso o contexto de RLS aberto; o contexto
exige o tenant; o tenant só se descobre lendo a sessão. Circular.

As saídas usuais seriam uma função `SECURITY DEFINER` ou um papel com
`BYPASSRLS`. As duas abrem no código a porta que o MVP 1.0 fechou com
cuidado, e abrem para sempre.

A escolhida: o cookie é `<tenantId>.<token>`. Parece violar "nunca aceite
tenantId do navegador", e por isso a distinção precisa estar escrita:

> o tenant do cookie **não é autoridade**. É chave de busca.

A consulta exige que o par (tenant, hash do token) case com uma linha real.
Trocar o tenant no cookie não dá acesso ao outro escritório — dá 401,
porque lá não existe sessão com aquele hash. O segredo continua sendo só o
token. Há teste, inclusive por HTTP.

## S13. Verificador de JWT escrito à mão

A família de ataques clássica do JWT é a confusão de algoritmo: a biblioteca
lê `alg` do cabeçalho — que o atacante controla — e escolhe o que fazer.
Trocando para `none`, ou para `HS256` com a chave pública como segredo, a
verificação passa.

Aqui `alg` não escolhe nada. Há um caminho só, Ed25519, sempre. O campo é
apenas conferido, e qualquer coisa diferente de `EdDSA` morre antes de
qualquer conta. São ~60 linhas usando o `crypto` do próprio Node, sem
dependência nova. `alg=none` e `alg=HS256` têm teste.

## S14. Sessão do Fiscale: SQLite confirmado

Reavaliei olhando o código real, como pedido, e a decisão da seção C se
sustenta:

- `sqlite3` já vem na biblioteca padrão e o PyInstaller já o empacota. Zero
  instalação, zero serviço novo — que é o valor do Fiscale.
- O servidor é `ThreadingMixIn`: várias threads no mesmo arquivo. JSON
  perderia escrita; SQLite tem transação.
- Postgres ou Redis exigiriam um serviço na máquina do escritório para
  guardar quinze linhas.

Duas coisas que não estavam decididas e agora estão: guarda-se o **SHA-256**
do token, não o token (o arquivo vai para backup e para o Drive, e ler
tokens ali seria entrar como qualquer um); e o `Secure` **não** entra no
cookie, porque o Fiscale serve HTTP na rede do escritório e o navegador
descartaria o cookie — ninguém conseguiria entrar. `HttpOnly` e
`SameSite=Lax` já estavam certos e permanecem.

## S15. Provisionamento é ferramenta, não rota

O débito do MVP 1.0 foi pago **não** criando o endpoint. Escritório novo
nasce algumas vezes por ano, por decisão humana; expor isso na web seria
superfície de ataque para uma operação que não precisa dela.

Virou CLI com trava de ambiente: sem `ELO_ADMIN_TOOL=1`, nada roda. Se
alguém importar essas funções de dentro de uma rota, o processo web não tem
a variável e a chamada falha alto, na hora — em vez de funcionar em
silêncio. Tudo roda como `elo_app`; nenhum papel privilegiado foi criado.

## S16. Toda recusa de autenticação sai igual

Assinatura inválida, expirado, replay, pessoa inexistente, pessoa inativa,
vínculo inativo: para fora, 401 e "Autenticação necessária." O código
estruturado vai para o log e para a auditoria.

Distinguir na resposta entregaria um oráculo: bastaria variar o `sub` para
descobrir quem trabalha no escritório.

---

---

# REVISÃO 4 — 09/08/2026 · projeção de clientes (MVP 1.2)

A revisão 1 (R3) já tinha aprovado a `ExternalCustomerReference`. O que
segue são as decisões que só apareceram ao olhar o cadastro real e ao
escrever o código.

## S17. O cadastro real derrubou dois pontos do schema previsto

Inspecionei `state_clientes.json` antes de fechar o modelo. Campos que
existem: `id · cert{arquivo,titular,cnpjLido} · nome · cnpj · regime · ie ·
im · mun · uf · email · tel · certValidade`. São 14 clientes.

**`nome fantasia` (tradeName) não existe.** Estava na lista de campos
mínimos da fase, e na R3. Coluna que nunca teria valor é peso morto;
acrescentar quando a origem tiver o campo é migration aditiva de uma linha.
Fica de fora.

**`tel` é um campo só, de texto livre, sem máscara e sem validação** — e
está vazio nos 14. Campo assim recebe "(81) 99999-1111 / 3333-4444" na
vida real, porque é como escritório preenche cadastro. Daí a decisão de
criar `ExternalCustomerPhone` em vez de uma coluna: com uma coluna só,
casar a mensagem do WhatsApp obrigaria a escolher um dos números em
silêncio — a decisão que o MVP 1.2 existe para tornar impossível.

Minimização aplicada: `regime`, `ie`, `im`, `mun`, `uf`, `cert` e
`certValidade` **não** sincronizam. Os três primeiros são dado de apuração;
`mun`/`uf` entrariam só "porque um dia talvez"; e o nome do arquivo `.pfx`
carrega razão social e CNPJ, então nem ele sai da máquina.

## S18. A lista branca é aplicada nos dois lados, e recusa em vez de descartar

No Fiscale, `projetar()` monta o payload campo a campo. Copiar o dicionário
e remover o proibido funcionaria hoje e vazaria amanhã, quando alguém
acrescentasse um campo ao cadastro.

No Elo, campo fora do contrato faz o payload ser **recusado**. Descartar em
silêncio deixaria o dado sensível chegar até a borda sem ninguém saber; se
o Fiscale começar a mandar `regime`, isso precisa aparecer no mesmo dia.

## S19. Autenticação de máquina: assinatura, não API key

Chave estática pura viaja em toda requisição e, capturada, pode ser
reenviada para sempre. A escolha foi assinar cada requisição com Ed25519 —
assimétrica pelo mesmo motivo do token de troca: o Elo vai para um VPS, e
lá não pode existir nada capaz de FORJAR requisição do Fiscale.

Par **separado** do par de login. Mesma máquina e mesmo dono, mas
propósitos distintos: uma assina "esta pessoa entrou", a outra "este
cadastro mudou". Separadas, cada uma rotaciona no seu tempo.

Texto assinado: método, caminho, timestamp, nonce e hash do corpo. Método e
caminho entram porque, sem eles, uma assinatura válida para o endpoint de
sincronização serviria em qualquer outro que aceitasse o mesmo corpo.

**O tenant vem da chave.** Cada `kid` registrado aponta para um tenant, e
`tenantId` no corpo — se vier — precisa concordar. Divergência é recusa
explícita: erro de configuração e tentativa se parecem daqui, e as duas
precisam aparecer.

## S20. Idempotência decidida pelo Elo, não pelo Fiscale

O Fiscale manda o `sourceVersion` dele e o usa para decidir o que
enfileirar. Mas quem decide se a projeção mudou é o `contentHash` que o Elo
calcula sobre o que chegou.

O motivo é prático: os dois lados são Python e TypeScript, e depender de
que produzam o mesmo digest é frágil. Do jeito escolhido, um erro no hash
do Fiscale causa, no pior caso, um envio a mais — nunca uma atualização
perdida.

## S21. Ausência não é inativação

Registro que não veio num full sync ganha `missingSince`. Não é apagado nem
inativado, porque ausência pode ser exclusão na origem, mas também filtro
novo ou exportação pela metade — e as três se parecem daqui. Inativar só
quando a origem disser `active: false`, e mesmo aí o registro permanece:
conversa futura vai apontar para ele.

## S22. Telefone: na dúvida, não normaliza

A regra que governa o módulo. Nunca inventar DDD, nunca acrescentar o nono
dígito, nunca "consertar" número curto. Número normalizado errado não dá
erro — entrega a conversa de um cliente na tela de outro.

Há uma lista de DDDs válidos, e ela não é purismo: sem ela, um CPF digitado
no campo de telefone (11 dígitos) viraria número válido e entraria no
índice de busca.

E `findCustomerByPhone` tem três respostas, sendo `AMBIGUOUS` a que
importa. Dois clientes com o mesmo telefone acontece — matriz e filial,
contador que atende as duas, celular do sócio. Quem decide é a pessoa.

## S23. Fila local em SQLite, guardando o evento e não o conteúdo

Sem Kafka nem RabbitMQ: uma máquina, dezenas de clientes, e o problema real
é não perder alteração quando a internet cai.

A decisão menos óbvia: a fila guarda "o cliente X mudou", e o payload é
montado na **hora do envio**. Se o Elo ficar duas horas fora e o cadastro
mudar três vezes, o que sobe é o estado atual — não uma sequência de fotos
velhas.

O gatilho compara o hash dos campos permitidos, então mexer em regime, IE
ou certificado não gera sincronização nenhuma.

## S24. Duas descobertas do PostgreSQL que viraram migration

Registradas porque custaram tempo e vão se repetir:

1. `DELETE ... WHERE coluna < x` exige privilégio de **SELECT** sobre a
   coluna do filtro — o banco precisa ler para avaliar a condição.
2. Quando o `WHERE` de um DELETE ou UPDATE referencia colunas da tabela, as
   políticas de **SELECT** do RLS também se aplicam. Sem política de
   leitura, o comando não encontra nada e apaga zero linhas, em silêncio.

As duas apareceram na faxina de tokens consumidos. A tabela de nonces da
integração já nasceu com as duas permissões mínimas por causa disso.

---

---

# REVISÃO 5 — 09/08/2026 · interface e atendimento (MVP 1.3)

A interface obrigou a decidir coisas que só apareceriam mais tarde, quando
já custariam migração. Ficam registradas agora, antes de existir a primeira
mensagem.

## D1. A identidade externa é fixa: funcionário + área principal

Toda mensagem enviada pelo Elo sai assinada como **`Nome • Área`** —
"Aline • Fiscal". A assinatura é automática: ninguém digita, ninguém
escolhe na hora, ninguém esquece.

O cliente continua vendo, no canal, a **foto e o nome do escritório** como
contato principal. A pessoa aparece dentro da mensagem, não como remetente:

```
Aline • Fiscal
Bom dia, Maria! Vou verificar para você.
10:32 ✓✓
```

A foto pessoal do funcionário não é exposta ao cliente.

## D2. Cada mensagem guarda o nome e a área de QUANDO foi escrita

A mensagem precisará de campos próprios — `senderMembershipId`,
`senderDisplayName`, `senderDepartmentName` ou equivalente — e não apenas
de uma referência ao membership.

O motivo é simples e só aparece depois: se a Aline mudar do Fiscal para o
Contábil em março, a mensagem que ela escreveu em janeiro **não pode**
passar a dizer "Aline • Contábil". O histórico registra o que aconteceu,
não o organograma de hoje. Referência viva reescreveria o passado a cada
mudança de setor.

## D3. Visualização de mensagem é registrada por pessoa

O Elo guardará quem abriu cada mensagem recebida, mesmo sem responder:

```
Visualizada por:
  Aline • Fiscal — 10:31
  Carlos • Gerência — 10:34
```

Informação **interna** ao escritório. Não vai para o cliente, e não vira
"visto" no WhatsApp dele.

## D4. Visualizar não é assumir

`VIEWED` e `ASSIGNED_TO` são coisas separadas, e nenhuma implica a outra.
Abrir uma conversa registra visualização; não muda o responsável.

Se abrir assumisse, ninguém olharia a caixa por medo de virar dono do
problema — e a caixa compartilhada perderia a graça.

Os quatro estados do atendimento são distintos e vão alimentar as métricas:

```
RECEBIDA    10:20   chegou
VISUALIZADA 10:22   alguém abriu
ASSUMIDA    10:24   alguém é responsável
RESPONDIDA  10:30   o cliente teve resposta
```

## D5. O cliente poderá escolher com quem falar

Quando um cliente iniciar contato sem atendimento ativo, poderá receber
uma lista:

```
Com quem você deseja falar?
  Aline — Fiscal
  Carlos — Contábil
  Mariana — Departamento Pessoal
  João — Gerência
  Atendimento Geral
```

Sempre **nome + área**, nunca só o nome: "Alan" não diz nada ao cliente;
"Alan • Gerência" diz.

## D6. `visibleToCustomers` no membership

A lista acima **não** é "todos os usuários do tenant". Quem administra o
escritório marca quem aparece. Sem esse campo, um estagiário recém-criado
apareceria para o cliente escolher no dia seguinte ao cadastro.

## D7. `availableForAssignment` no membership

Separado do anterior, e de propósito: aparecer na lista e poder receber
atendimento são coisas diferentes. Alguém de férias continua existindo no
sistema e não deve entrar na fila; um gerente pode ser visível para
consulta sem estar na distribuição.

## D8. Departamento principal — implementado nesta fase

`Membership.primaryDepartmentId` já existe. Uma pessoa pode participar de
várias áreas, mas só uma acompanha o nome dela para fora.

O sistema **não deduz** qual é a principal. Sem escolha explícita, a
assinatura sai só com o nome. Eleger "o primeiro da lista" faria a
assinatura que o cliente lê mudar sozinha quando alguém reordenasse um
cadastro.

## D9. A conversa pertence ao tenant, não ao atendente

Escolher a Aline a torna **responsável**, não dona. A conversa é do
escritório: outra pessoa autorizada pode ler, e a transferência não pede
permissão de quem estava com ela.

Sem isso, sair de férias viraria conversa inacessível.

## D10. Transferência preserva histórico

`Aline • Fiscal → Carlos • Fiscal` fica registrado: quem transferiu, para
quem, quando e por quê. A transferência muda o responsável **a partir dali**
— não reescreve as mensagens anteriores, que continuam com a assinatura de
quem as escreveu (ver D2).

---

## Decisões de UX definidas no fechamento do MVP 1.3

### D11. Texto didático não compete com trabalho real

A explicação dos quatro estados aparece **apenas enquanto não existe
nenhum atendimento**. No instante em que houver o primeiro, ela some
sozinha e a área principal passa a ser da conversa selecionada — ou do
estado "Selecione um atendimento."

Implementado: `EmptyConversation` recebe o total de atendimentos e decide.
Hoje o total é 0 por não existir `Conversation`; no MVP 1.5 basta uma linha
virar a consulta real, e a interface se ajusta sem redesenho.

### D12. A navegação mostra só o que existe

Nada de item desabilitado com "em breve". A barra lateral de quem trabalha
não é lugar de exibir roadmap: cria expectativa, ocupa espaço e ensina a
pessoa a ignorar itens.

"Não lidas", "Aguardando resposta" e "Arquivadas" saíram da navegação e
vivem como **filtros dentro de Atendimentos**, que é onde fazem sentido.
Voltam à navegação se e quando forem telas próprias.

### D13. O nome é "Atendimentos"

Em toda parte: navegação, título da tela, filtros e textos. Não "caixa de
entrada" — o Elo trata de **atendimento**, e caixa de mensagens é apenas o
que se vê de fora. O nome que a equipe usa molda como ela pensa o trabalho.

### D14. Filtros ordenados por prioridade, não por conveniência

```
Novos · Não lidos · Meus · Aguardando · Todos
```

Primeiro o que exige ação; "Todos" por último. A tela abre em **Novos**:
quem chega de manhã vê o que está parado, não uma lista geral onde o
urgente se esconde no meio.

E a tela principal do funcionário **não vira painel de métricas**. Conversa
e pendência são o assunto; número agregado é consequência, e cabe em outro
lugar.

---

---

# REVISÃO 6 — 10/08/2026 · modelo de atendimento (MVP 1.4)

## S25. O nome é `Conversation`, e a tela chama de "Atendimento"

Escolhido entre `Conversation`, `Ticket` e `Atendimento`. Vence
`Conversation` por dois motivos: é o nome que a seção D já usava — e a
regra desta fase era não reinventar estrutura —, e é para ele que as
mensagens do MVP 1.5 vão apontar.

Na interface aparece "Atendimento", que é a palavra do escritório. Modelo
e rótulo não precisam ser a mesma palavra; forçar isso costuma estragar
um dos dois.

## S26. Concorrência ao assumir: a condição vai dentro do UPDATE

O caso: Aline e Carlos clicam "Assumir" no mesmo segundo.

A implementação ingênua lê, decide no código e grava. Entre o `SELECT` que
vê "sem responsável" e o `UPDATE` que grava, o outro faz o mesmo — e os
dois gravam. O último vence, o primeiro acha que assumiu, e os dois
respondem o mesmo cliente.

```sql
UPDATE conversations SET assigned_membership_id = :eu, ...
 WHERE id = :id AND assigned_membership_id IS NULL
```

Uma linha afetada: assumi. Zero: alguém chegou antes — e a resposta diz
**quem**:

> Este atendimento acabou de ser assumido por Aline • Fiscal.

Dizer o nome não é enfeite: sem ele a pessoa clica de novo achando que
travou. É a única exceção documentada à regra de não devolver detalhe
interno ao cliente — e não há segredo em dizer qual colega pegou o caso.

**Verificado por mutação:** removida a condição do `WHERE`, cinco cliques
simultâneos "assumem" os cinco, e o teste falha. Com ela, 200 e 409.

Para transferir e mudar status, trava otimista por `version`: quem envia a
partir de uma tela velha recebe 409 em vez de sobrescrever a decisão de
outro em silêncio.

## S27. Uma linha do tempo, não duas

A seção D previa `ConversationAssignment` separada. Ficou tudo em
`ConversationEvent`: duas linhas do tempo paralelas divergem, e a pergunta
"o que aconteceu com este atendimento?" passaria a ter duas respostas.

Os campos que se consulta o tempo todo — de quem para quem, de qual status
para qual — são COLUNAS, não chaves dentro de um JSON. Cavar JSON para
responder isso envelhece mal. `metadata` fica para o resto.

## S28. Visualização acumula; o evento é uma vez por pessoa

`ConversationView` guarda uma linha por (atendimento, pessoa), com
`firstViewedAt`, `lastViewedAt` e `viewCount`. Um evento por abertura
encheria o histórico de ruído: quem abre um caso o abre dez vezes por dia.

O `VIEWED` do histórico entra apenas na primeira vez de cada pessoa. E o
`firstViewedAt` do atendimento é do ESCRITÓRIO — grava uma vez só, porque
"tempo até a primeira visualização" não pode mudar quando a segunda pessoa
abre.

## S29. Nota interna é tabela, não marcação

`InternalNote` é uma tabela separada de mensagem — e vai continuar sendo
quando `Message` existir.

Um campo `interna: boolean` dentro de mensagem deixaria a nota do
escritório a um `false` de distância do WhatsApp do cliente. Esse `false`
chega um dia, num merge apressado, e ninguém percebe até o cliente
responder à observação interna sobre ele. Separadas, não existe caminho de
código que envie uma nota.

O histórico registra QUE houve nota, nunca o texto.

## S30. `visibleToCustomers` tem padrão `false`

Ninguém é exposto ao cliente por ter sido cadastrado. Com padrão `true`, o
estagiário criado hoje estaria na lista de escolha amanhã.

`availableForAssignment` tem padrão `true` e é outra coisa: um gerente
pode ser visível para consulta sem entrar na distribuição; quem está de
férias sai dos dois. E nenhum dos dois é "online" — presença em tempo real
é do MVP 1.5+.

A visibilidade externa **não** é derivada do papel. Nada de
`role === MANAGER → aparece`: regra por papel engessa num lugar que o
escritório não controla.

## S31. `getCustomerVisibleAssignees` devolve o mínimo

Nome, área e um id. Não devolve e-mail, papel, permissão, sessão nem dado
administrativo — e a regra não é "filtrar na tela", é **não buscar**: o que
não sai de lá não vaza numa serialização distraída no MVP 1.7.

"Atendimento Geral" existe sempre. Obrigar o cliente a escolher um nome
para falar com o escritório é criar obstáculo onde deveria haver porta.

## S32. O significado de cada filtro, escrito

"Não lidos" pode querer dizer três coisas, e escolher errado faz a equipe
parar de confiar no número:

| Filtro | Significa |
|---|---|
| Novos | `status = NEW` — chegou e ninguém assumiu |
| Não lidos | **EU** nunca abri. É pessoal: o que já vi sai da minha lista mesmo que o colega não tenha visto |
| Meus | sou o responsável atual |
| Aguardando | parado esperando cliente ou escritório |
| Todos | tudo, inclusive resolvido — ordenado por atividade, o resolvido afunda sozinho |

## S33. Dados de desenvolvimento com três travas

`npm run seed:dev` recusa `NODE_ENV=production`, exige
`ELO_ALLOW_DEV_SEED=1` e marca tudo com o canal `DEV` mais a etiqueta
"Dados de teste". `seed:dev:limpar` apaga pelo canal — nunca por data nem
por "os últimos N".

E continua não havendo mensagem falsa: atendimento é registro de trabalho;
balão de conversa inventado é outra coisa, e essa não existe.

---

## Decisão registrada para o futuro: notificações

Não implementado nesta fase. Registrado para não se perder.

O Elo precisará avisar o funcionário **mesmo com a interface fora do
primeiro plano** — a caixa compartilhada só funciona se alguém souber que
chegou algo. A arquitetura futura deve contemplar:

1. **Aviso dentro do Elo** — contador e destaque na lista, sem depender do
   sistema operacional.
2. **Som configurável**, por pessoa e desligável. Som que não se desliga é
   som que faz a pessoa desligar o sistema.
3. **Web Notifications** com permissão pedida no momento certo — depois de
   a pessoa entender o produto, nunca no primeiro segundo.
4. **PWA / Web Push** para quando a aba estiver fechada. Exige service
   worker e chaves VAPID; é a parte cara.
5. **Contador de não lidas** coerente com o filtro "Não lidos": pessoal,
   não do escritório.
6. **Roteamento**: avisar o responsável; sem responsável, a área; sem área,
   quem estiver na fila geral. Notificar todo mundo de tudo é o caminho
   mais rápido para ninguém ler nada.
7. **Escalonamento de visualizado sem resposta** — o dado para isso já
   existe (`firstViewedAt`, `firstResponseAt`, `ConversationView`), que é
   metade do motivo de terem sido modelados agora.
8. **Privacidade da prévia**: poder mostrar "Nova mensagem de Padaria do
   João" sem o conteúdo. Notificação aparece em tela bloqueada, e o
   escritório trata de assunto de terceiro.

---

---

# REVISÃO 7 — 10/08/2026 · motor de mensagens e realtime (MVP 1.5)

## S34. `Message` aponta para a `Conversation` que já existe

Não há um segundo modelo de conversa, e não há nada de WhatsApp dentro de
`Message`. O canal é um campo de texto (`INTERNAL` hoje, `DEV` no que
entra pela porta de teste), e a Meta entrará como ADAPTADOR gravando
nestas mesmas tabelas. A regra prática para revisão: se o nome de um
provedor aparecer no modelo, algo foi modelado errado.

`MessageDirection` tem `SYSTEM` declarado e **nada o produz**.
Transferência, status e atribuição já vivem em `ConversationEvent`, e
duplicá-los como mensagem daria duas fontes de verdade para o mesmo fato.
A interface compõe as duas listas quando precisa; o banco não.

## S35. A ordem vem de um contador, não do relógio

`sequence` sai de `UPDATE conversations SET message_seq = message_seq + 1
… RETURNING`. O próprio UPDATE trava a linha da conversa, então dois
envios simultâneos recebem números diferentes, sempre.

A alternativa que parece certa — `SELECT MAX(sequence) + 1` e depois
inserir — perde a corrida: as duas transações leem o mesmo máximo antes de
qualquer uma gravar.

**Verificado por mutação, e a mutação ensinou mais do que o teste:** com o
`SELECT MAX`, o teste de "dez envios simultâneos" continuou PASSANDO no
arquivo comum de testes. O motivo é que `tests/setup.ts` fixa o pool em uma
conexão — de propósito, para forçar o reuso e expor vazamento de tenant —,
e uma conexão serializa as transações. Um teste de concorrência sem
concorrência dá confiança sem dar garantia, que é pior do que não ter
teste.

Daí `tests/messages-concurrency.test.ts`, com pool próprio e um teste que
confere que o paralelismo existe antes de medir qualquer outra coisa. Com
ele, a mutação falha como devia.

## S36. Idempotência decidida pelo índice, não pelo código

`clientMessageId` nasce no navegador ANTES do envio. Duplo clique, retry e
reconexão mandam o mesmo valor, e quem decide é o índice único
`(tenant_id, sender_membership_id, client_message_id)`.

O perdedor recebe P2002, lê a mensagem que já existe e devolve ELA. Do
lado de fora o retry parece ter dado certo na primeira tentativa — que é
exatamente o que deveria parecer. `NULL` não colide com `NULL` no
Postgres, então mensagem de entrada não é afetada; para ela a trava é
`(tenant_id, external_message_id)`, porque canal reentrega e reentrega não
é mensagem nova.

## S37. Nome e área ficam CONGELADOS na mensagem

`senderDisplayName` e `senderDepartmentName` são copiados no envio. Se a
Aline mudar do Fiscal para o Contábil em março, a mensagem de janeiro
continua dizendo "Aline • Fiscal".

Ficam em dois campos, e não numa string pronta: quem monta "Nome • Área" é
a interface. Guardar o texto formatado impediria mudar a apresentação sem
reescrever o histórico.

**Verificado por mutação:** trocando a leitura do snapshot por uma consulta
ao departamento atual, o teste falha com `expected 'Contábil' to be
'Fiscal'` — o passado reescrito, visível.

## S38. Realtime por SSE, e o motivo é de segurança

Socket.IO exigiria um servidor HTTP próprio, e com ele o abandono de `next
dev`/`next start` por um servidor customizado. Mudança de arquitetura para
um problema que não temos: o que esta fase pede é fluxo servidor →
cliente. Enviar mensagem é um POST, que já existe e já é idempotente.

O argumento que decide é outro: **SSE é uma rota como as outras.**
Autentica com o mesmo `requireAuth(req)`, pelo mesmo cookie, com a mesma
sessão revogável. Um servidor de WebSocket ao lado teria o seu próprio
caminho de autenticação — uma segunda porta para manter fechada, e a
segunda porta é sempre a que fica aberta.

O `EventSource` reconecta sozinho, que era o principal motivo para querer
Socket.IO.

Custo aceito e registrado como débito: uma conexão HTTP por aba, e um
barramento em processo — ou seja, uma instância. Quando houver a segunda,
o adaptador entra dentro de `bus.ts` e nada mais no projeto muda.

## S39. O isolamento do realtime é estrutura, não verificação

Os inscritos vivem num `Map<tenantId, Set<handler>>`. `publish(tenantId,
…)` entrega ao conjunto daquela chave e a mais nenhum: não existe "todos
os inscritos" para alguém varrer com um `if` errado. Vazar exigiria
reescrever a função, não esquecer uma condição.

E o tenant nunca vem do navegador: quem se inscreve é a rota, com o tenant
da sessão. O cliente pode pedir para acompanhar UMA conversa, e esse
parâmetro só REDUZ o que ele recebe — ainda assim conferido contra o
banco, com RLS aberto no tenant da sessão. Conversa de outro escritório não
existe ali: a inscrição é recusada, e a tela é avisada da recusa em vez de
receber silêncio.

**Verificado por mutação nos dois níveis:** com `publish` entregando a
todos, falham o teste do barramento e o teste por HTTP com sessão real.

## S40. Ler o atendimento e ler a mensagem são registros diferentes

`ConversationView` (MVP 1.4) responde "quem abriu o caso". `MessageView`
responde "quem leu esta mensagem". Não são a mesma pergunta, e o filtro de
não lidas depende da segunda.

**Nenhum GET marca leitura.** É rota própria, chamada pela interface quando
três coisas valem ao mesmo tempo: conversa aberta, janela visível e a
mensagem de fato dentro da área visível. Renderizar não basta — se
bastasse, prefetch e aba em segundo plano zerariam o contador de quem não
olhou nada, e contador em que a equipe não confia é pior do que contador
nenhum.

Isso é interno. Não é o "visto" do cliente e nunca sai para ele: o status
de leitura do canal externo (`QUEUED · SENT · DELIVERED · READ · FAILED`,
já declarado no enum) é do TRANSPORTE, do outro lado da conversa, e um dia
os dois vão aparecer juntos na mesma tela sem se confundir.

## S41. "Não lidos" passou a olhar mensagem

Antes de existir mensagem, a única leitura possível era "abri o
atendimento". Agora o que conta é a mensagem do cliente: um caso que eu
abri ontem e que recebeu mensagem hoje de manhã está NÃO LIDO — e a regra
antiga o esconderia, que é o pior erro que este filtro pode cometer.

Sobra um caso: atendimento sem nenhuma mensagem de entrada. Para esse vale
a regra antiga (nunca abri), senão ele nunca apareceria em lugar nenhum
justamente por ser silencioso.

O contador é **calculado**, nunca guardado. Contador materializado
diverge, e um número errado de não lidas é pior que nenhum. São duas
consultas para a lista inteira — não duas por linha.

## S42. A máquina de estados do envio, escrita

```
Enviar     NEW              → IN_PROGRESS
           WAITING_OFFICE   → IN_PROGRESS   a bola estava conosco e saiu
           IN_PROGRESS      → IN_PROGRESS
           WAITING_CUSTOMER → inalterado    cobrar não é ser respondido
           RESOLVED         → inalterado    reabrir é decisão de gente

Receber    WAITING_CUSTOMER → WAITING_OFFICE
           os demais        → inalterado
```

Mensagem em atendimento RESOLVIDO **não reabre sozinha**: o caso sobe na
lista e aparece como não lido, e quem reabre é uma pessoa. Status foi
decisão de alguém; sobrescrevê-lo em silêncio faz a equipe parar de usá-lo.

`firstResponseAt` é gravado na primeira mensagem de saída e não muda mais.
"Tempo até a primeira resposta" não pode melhorar nem piorar quando a
segunda sai.

## S43. Paginação por cursor, e a ordem da tela é o `sequence`

Conversa cresce pela ponta. Com `OFFSET`, cada mensagem nova empurra tudo:
a pessoa rola para cima e revê o que já leu, ou pula uma. O cursor é o
`sequence`, que não se move. Pede-se um item a mais do que cabe para saber
se há página anterior, em vez de contar a conversa inteira.

Uma descoberta da validação no navegador: a interface montava a linha do
tempo ordenando mensagens e notas **por horário**. Com dados de teste
gravados com data retroativa, a conversa apareceu embaralhada. Corrigido:
as MENSAGENS seguem o `sequence` e nada mais; as notas são encaixadas por
horário entre elas. Em produção os dois critérios concordam — o que não é
motivo para depender do que empata.

## S44. Nota interna continua sendo outra tabela — agora que Message existe

Era uma decisão fácil de manter enquanto não havia mensagem. Agora que há,
vale repetir por que não virou `interna: boolean`: seria um `false` de
distância entre a observação do escritório e o WhatsApp do cliente, e esse
`false` chega um dia num merge apressado.

Separadas, não existe caminho de código que envie uma nota. Há teste de
conteúdo (nota não aparece entre as mensagens) e teste estrutural (o módulo
de notas não importa o de mensagens) — o segundo existe para que criar esse
caminho exija apagar um teste, que é uma decisão visível numa revisão.

Na tela as duas convivem na mesma linha do tempo, porque é onde o
escritório lê o caso — mas a nota não tem forma de balão, tem faixa
própria, e diz por extenso que não será enviada ao cliente. O campo de
escrita muda de cor junto.

## S45. Editar e apagar ficaram de fora, e as colunas ficaram

Não há caminho de código que escreva em `editedAt` ou `deletedAt`. Mensagem
enviada é histórico; edição silenciosa transforma registro em rascunho.

As colunas existem para que a política de retenção não precise de migration
sobre dado vivo. E a política vem antes do botão.

## S46. Entrada de desenvolvimento com as travas do seed

`/api/dev/inbound` recusa `NODE_ENV=production`, exige
`ELO_ALLOW_DEV_INBOUND=1` e marca tudo com o canal `DEV`. Desligada,
responde **404** — não confirma nem desmente a existência da rota. E exige
sessão mesmo assim: "só existe em desenvolvimento" não é motivo para
deixar aberta, porque banco de desenvolvimento também tem dado de cliente.

A trava mora num módulo, e não na rota, porque já há um segundo chamador
(o script) e a proteção não pode depender de quem chama lembrar dela.

---

## Decisão registrada para o futuro: mensagens apagadas e retenção

Não implementado. Registrado para não se perder, e porque a diferença entre
os itens abaixo é o que separa produto de armadilha.

1. **O Elo pode manter histórico de mensagens removidas**, desde que a
   política de retenção seja **declarada com todas as letras** a quem
   escreve. Guardar o que foi apagado sem avisar é vigilância; guardar com
   aviso é registro.
2. **Não haverá mecanismo oculto** para recuperar mensagem apagada em
   plataforma externa. O que o Elo mostra é o que o Elo recebeu.
3. **Não prometer o que nunca chegou.** Mensagem apagada antes de o canal
   entregar não existe para nós, e dizer o contrário é vender adivinhação.
4. **Exclusão lógica, quando existir**, segue política explícita: quem pode
   apagar, o que some da tela, o que permanece para auditoria e por quanto
   tempo — decidido antes de haver botão.
5. O aviso de gravação nos termos é pré-requisito da integração externa,
   não item de checklist posterior.

---

---

# REVISÃO 8 — 10/08/2026 · mídia e storage (MVP 1.6)

## S47. `MessageAttachment` pendura na `Message` que já existe

Nada de um segundo caminho para mídia. A mensagem continua sendo a
entidade; o anexo é uma linha que aponta para ela, e o TIPO da mensagem
sai do anexo (uma imagem faz a mensagem ser `IMAGE`).

O binário **não** entra no Postgres. A tabela guarda metadado e uma
chave; os bytes ficam no storage. Blob em banco transforma backup,
replicação e migration num problema de disco, e não se ganha nada — nem
transação, porque o arquivo continua tendo que subir antes.

## S48. `AUDIO` e `VOICE` são tipos diferentes

Arquivo de áudio anexado e recado gravado na hora não são a mesma coisa
para quem lê a conversa, aparecem diferente na tela, e o WhatsApp também
os distingue (`audio` com `voice: true`). Juntá-los agora significaria,
mais tarde, adivinhar qual era qual a partir do mimetype.

O enum antigo tinha `FILE`; virou `DOCUMENT` por `ALTER TYPE ... RENAME
VALUE`, escrito à mão. O `prisma migrate dev` queria derrubar e recriar o
enum, o que falharia com qualquer linha usando o valor antigo — hoje não
há nenhuma, e um dia haverá.

## S49. Disco em desenvolvimento, atrás da interface que o S3 vai usar

MinIO daria fidelidade com S3 e custaria mais um contêiner para todo mundo
que só quer rodar o projeto. A fidelidade que importa está na INTERFACE:
`put`, `get`, `getRange`, `delete`, `metadata`, `signedUrl`. Se o contrato
estiver certo, S3 é uma classe nova e uma variável de ambiente.

`getRange` está no contrato, e não é luxo: sem leitura por faixa o
navegador não deixa arrastar a barra do áudio.

`signedUrl` devolve `null` no provedor local — resposta legítima, que
obriga quem chama a usar a rota autenticada em vez de supor que existe
link. Supor é como se acaba servindo arquivo por URL pública sem perceber.

**A raiz local é constante (`.storage/` no `cwd`), e não configurável.**
Com o caminho vindo de `process.env`, o Turbopack avisa — e está certo —
que a leitura de disco com caminho dinâmico faz o build rastrear o projeto
inteiro e empacotar todo o código-fonte junto do servidor. Trocar um risco
de deploy por uma variável que só serve em desenvolvimento seria mau
negócio. Em produção o storage é S3, onde "onde fica" é configuração de
bucket.

## S50. A chave carrega o tenant e não carrega o nome do arquivo

```
tenants/<tenantId>/messages/<ano>/<mês>/<32 hex aleatórios>
```

O tenant vem primeiro: é a segunda camada de isolamento, e a rota de
download só chega à chave depois de o banco (com RLS) dizer que o anexo é
seu. O nome original é só metadado — com ele fora da chave, `../`, `CON`,
emoji, 300 caracteres e dois arquivos chamados `foto.jpg` deixam de ser
problema. E o sufixo é aleatório, não sequencial (enumerável) nem derivado
do conteúdo (dois clientes que mandaram o mesmo PDF compartilhariam
caminho).

## S51. Três checagens, e a terceira é a que importa

1. **Extensão** — a ÚLTIMA. `foto.jpg.exe` tem extensão `exe`. Olhar "se
   contém .jpg" é o que transforma a lista de permitidos em decoração.
2. **MIME declarado** — barato de conferir, trivial de forjar. Nunca
   decide sozinho.
3. **Magic bytes** — o que o arquivo É. Um `.png` que começa com `MZ` é um
   executável renomeado, e só a terceira checagem percebe.

As três precisam concordar. Concordar duas em três passa em muitos
sistemas, e é exatamente o que se explora.

`conteudoBate` pode devolver `null` — "não sei" — para formatos sem
assinatura confiável (`.aac` cru é indistinguível de MP3). `null` não é
"pode passar": é uma decisão que fica escrita.

## S52. Limites por categoria, num lugar só

Imagem 10 MB · documento 25 MB · áudio 25 MB · voz 15 MB, todos por
variável de ambiente. Dimensionados para escritório de contabilidade: foto
de documento pelo celular, PDF de balancete, recado de cliente. Cinco
minutos de voz em Opus não passam de 4 MB — 15 MB é folga, não convite.

O canal externo terá os SEUS limites, mais apertados. Não são estes, e a
distinção precisa sobreviver à integração.

## S53. Banco e storage não compartilham transação

Não existe `BEGIN` que cubra o Postgres e o S3. O fluxo é desenhado para
que a falha em qualquer ponto deixe LIXO, nunca buraco:

1. gravam-se os BYTES;
2. grava-se a LINHA como `PENDING`, com prazo de 2 h;
3. a mensagem nasce e ADOTA o anexo (`PENDING` → `ATTACHED`) **na mesma
   transação**;
4. o que não foi adotado até o prazo é apagado — linha e bytes.

A ordem importa: gravar a linha antes dos bytes daria um anexo quebrado na
tela, que é pior que um arquivo esquecido no disco. E se o passo 2 falhar
depois do 1, o arquivo é apagado ali mesmo, na compensação.

O `where` da adoção exige `status = PENDING AND message_id IS NULL`:
anexo já usado não é reaproveitado por outra mensagem, nem por um retry
malicioso.

## S54. A faxina roda POR TENANT — e o teste é que mostrou por quê

A primeira versão varria a tabela inteira com o cliente cru, sem contexto
de tenant. Não apagava nada, e dizia que estava tudo limpo: com RLS
forçado e sem `app.tenant_id`, a consulta não devolve linha nenhuma.

A saída fácil seria um papel com `BYPASSRLS` para a manutenção. Recusada:
abriria no projeto, para sempre e por causa de uma tarefa de limpeza, a
porta que o MVP 1.0 fechou com cuidado.

Então a faxina recebe o tenant. Roda de forma oportunista a cada upload
(no máximo uma vez a cada 15 min) e à mão por `npm run storage:limpar`.
Sem cron externo: volume de escritório não justifica agendador, e um
`setInterval` no processo web morre no primeiro deploy sem ninguém notar.

## S55. Não existe URL pública

Guia de cliente, contrato, foto de documento. Todo download passa por
`/api/attachments/:id`: sessão válida, permissão, RLS. A `storageKey`
nunca sai na resposta.

Anexo de outro tenant é **404**, não 403 — 403 confirmaria que existe.

`Content-Disposition: inline` só para imagem e áudio, que o navegador
exibe sem executar nada. Documento vai como `attachment`: PDF aberto no
visualizador interno é script rodando na NOSSA origem, e não há motivo
para conceder isso a um arquivo que chegou de fora. Junto vai
`X-Content-Type-Options: nosniff`, sem o qual o navegador pode "corrigir"
o tipo declarado.

O `Range` é atendido com `206` e `Content-Range` — é o que faz o seek do
áudio existir.

## S56. Nenhum navegador grava MP3

Chrome e Edge produzem `audio/webm;codecs=opus`; o Safari, `audio/mp4`
(AAC). O formato é escolhido com `MediaRecorder.isTypeSupported`, na ordem
de preferência, e o servidor aceita os dois. Assumir um formato daria um
gravador que funciona numa máquina e falha na outra sem explicação.

Não há transcodificação no servidor: converter para MP3 exigiria ffmpeg no
processo web, e o ganho seria estético.

O microfone é solto SEMPRE — no envio, no cancelamento e ao desmontar.
Luz de microfone acesa depois de a pessoa cancelar é a diferença entre um
sistema em que se confia e um em que não se confia. E permissão negada
mostra uma frase clara sem derrubar a conversa: o gravador é recurso a
mais, nunca pré-requisito.

## S57. O gancho de antivírus existe; o antivírus, não

`scanStatus` nasce `NOT_SCANNED`, e **não** `CLEAN`: dizer "limpo" sobre
um arquivo que ninguém examinou seria mentira gravada no banco. O download
recusa `BLOCKED`. Ligar um scanner depois é preencher a coluna — não mexer
no caminho de download.

**Isto é dívida obrigatória antes de abrir a produção**, e está registrada
como tal no README. Escritório de contabilidade recebe arquivo de
desconhecido todo dia.

## S58. Mídia não carrega até alguém querer

O histórico traz METADADO. Imagem usa `loading="lazy"`; áudio usa
`preload="none"` e mostra "0:37" a partir da duração gravada no anexo —
sem baixar um byte. Uma conversa com quarenta áudios abre sem buscar
quarenta arquivos.

A miniatura é a imagem original limitada por CSS. Não há pipeline de
thumbnail: quando houver, entra como anexo DERIVADO, sem sobrescrever o
original. O custo hoje é de rede, e está registrado.

## S59. Prévia de mídia na lista — achado ao clicar

Uma mensagem só com foto aparecia como linha em branco na lista de
atendimentos, porque a prévia usava o `content` da mensagem, que é vazio.
Corrigido no servidor: sem texto, a prévia vira "Foto", "Áudio",
"Mensagem de voz" — ou o NOME do arquivo, no caso de documento, porque ali
o nome é a informação ("DAS_08-2026.pdf"). Para foto, o nome
(`IMG_20260810_193045.jpg`) não diria nada.

## S60. Nota interna continua sem anexo

Ficou fora desta fase de propósito. A prioridade era mensagem, e anexo em
nota abriria uma segunda superfície de upload — com as mesmas perguntas de
validação, download e retenção — para um caso que ninguém pediu ainda.

---

---

# REVISÃO 9 — 10/08/2026 · PWA, notificações e Web Push (MVP 1.7)

O problema que esta fase resolve, escrito como o pedido chegou: **um
funcionário precisa ser avisado quando um cliente entra em contato, mesmo
que não esteja olhando para o Elo.**

Isso muda o roadmap. O MVP 1.7 era "WhatsApp de teste" e passou a ser
notificações; o WhatsApp foi para o 1.8. Ver o motivo em S61.

## S61. Notificação vem ANTES do canal — e a troca tem razão

A ordem antiga colocava o WhatsApp em 1.7. A nova coloca notificação.

O argumento: o Elo já recebe mensagem (canal `DEV`), já tem realtime, já
tem caixa compartilhada. O que falta para ele ser usado de verdade é
alguém FICAR SABENDO que chegou algo — hoje o SSE só funciona com a página
aberta, e uma equipe de quinze pessoas não fica com uma aba na frente o dia
inteiro.

E há um ganho que não é de agenda: quando o WhatsApp chegar, ele entra
como adaptador gravando em `messages`, e as notificações já vão estar
funcionando em cima de `Message` — sem uma linha de código de notificação
que conheça a Meta. Na ordem antiga, a tentação seria acoplar as duas
coisas.

## S62. Web Push escrito à mão, com o `crypto` do Node

A biblioteca usual (`web-push`) faz exatamente o que `push/webpush.ts` faz,
e traz junto um cliente HTTP e a árvore de dependências dele. O que o
arquivo precisa é ECDH P-256, HKDF-SHA256, AES-128-GCM e uma assinatura
ES256 — as quatro nativas no Node, e as quatro definidas por RFC (8291 e
8292).

É o mesmo raciocínio de S13, o verificador de JWT: **caminho único,
algoritmo fixo, nada escolhido por dado de fora.** Não há negociação de
cifra — `aes128gcm` sempre, `ES256` sempre.

O que tornou isso defensável foi o teste. `tests/webpush.test.ts` cifra com
o código do Elo e **decifra como o navegador decifraria**, com a chave
privada do "navegador" de teste. Sem essa ida e volta, um erro de cifra
seria INVISÍVEL: o provedor aceita o POST, responde 201, e o navegador
descarta em silêncio o que não consegue abrir. O defeito só apareceria
como "às vezes não avisa" — a pior forma de bug possível num sistema de
alerta.

Duas armadilhas que o teste trava, e que custam horas quando escapam:

- **`dsaEncoding: "ieee-p1363"`.** O padrão do Node assina em DER; o
  provedor recusa com 401, que parece problema de chave.
- **O delimitador `0x02`** no fim do registro. Sem ele o navegador espera
  mais dados e descarta a mensagem.

## S63. A prévia vem DESLIGADA, e é o único padrão restritivo

Todas as preferências nascem ligadas, menos `showPreview`.

A assimetria é deliberada. Receber o aviso é o que a pessoa espera do
produto; ver o texto do cliente na tela bloqueada do Windows não é — isso
ela precisa pedir.

O caso concreto: a notificação cruza o canto da tela enquanto alguém passa
pela mesa, e aparece no celular trancado em cima da mesa do almoço. O
escritório trata de assunto de terceiro. Com a prévia desligada o aviso diz
QUEM falou, nunca O QUE falou:

```
ELO
Empresa ABC entrou em contato.
```

E mídia vira DESCRIÇÃO mesmo com a prévia ligada — inclusive o nome do
arquivo fica de fora. "Balancete_MARIA_SILVA_2026.pdf" conta a mesma
história que a prévia existe para esconder.

## S64. A permissão só é pedida depois de um clique

Nada no código chama `Notification.requestPermission()` sozinho. O registro
do service worker acontece na abertura (é invisível e não pede nada); a
permissão, só no botão "Ativar notificações", numa tela que explica antes o
que vai acontecer.

O motivo não é etiqueta: um diálogo no primeiro segundo recebe "Bloquear",
e "Bloquear" no Chrome é uma decisão que a pessoa comum não sabe desfazer.
Um único diálogo mal colocado tira o recurso do escritório inteiro, para
sempre, sem que ninguém entenda por quê.

Por isso também a tela distingue os quatro estados — permitida, bloqueada,
não configurada, sem suporte — e, no caso bloqueado, explica que a mudança
é no navegador em vez de oferecer um botão que não vai funcionar.

## S65. A fronteira entre SSE e Push é a PRESENÇA

Os dois são complementares, e a regra está escrita em código
(`realtime/presence.ts`):

```
conversa aberta + janela visível  → só o SSE. Nem push, nem som.
Elo aberto, outra conversa        → som (se ligado) e crachá. Sem push.
Elo aberto, janela escondida      → push.
Elo fechado                       → push.
```

A presença em processo sabe o que a aba CONTOU. Uma aba que morre sem
avisar fica registrada até a conexão SSE cair, o que leva alguns segundos —
e nesse intervalo o push pode ser suprimido para alguém que já não está lá.

**A escolha é deliberada:** errar para o lado de UM aviso a menos numa
janela de segundos, em vez de errar para o lado do aviso duplicado o tempo
todo. O atendimento continua na lista, continua não lido e continua
contando no crachá — o que se perde é o alerta imediato, nunca a
informação.

Consequência de arquitetura: `useRealtime` virou **conexão única por aba**
(singleton do módulo). No MVP 1.5 havia um chamador só e isso era
consequência feliz; agora há dois — a tela de Atendimentos e a casca — e
duas conexões dobrariam também a presença registrada, que é o que decide se
o push sai.

## S66. `decidir` é pergunta, `avisar` é ação

A primeira versão consumia a janela de agrupamento dentro de `decidir`. O
defeito apareceu num teste que chamava `decidir` e depois `avisar`: a
segunda chamada respondia "não notificar" porque a primeira já tinha gasto
a janela.

Numa tela de diagnóstico isso teria virado "o sistema diz que vai avisar e
não avisa", sem nada no log. Perguntar duas vezes tem que dar a mesma
resposta; o efeito colateral mora em `avisar`, na hora de entregar.

## S67. Agrupamento sem temporizador, e o que isso custa

Duas camadas:

1. **`tag` / `Topic`** — o sistema operacional substitui o aviso anterior
   do mesmo atendimento. Resolve a bagunça visual.
2. **A janela de 30 s no servidor** — resolve vibração, som do sistema e
   bateria, que a substituição não resolve: cada aviso substituído ainda
   chegou e ainda acordou a tela.

A versão com `setTimeout` mandaria um aviso final com o total exato ao fim
da rajada. Ela também colocaria um temporizador vivo num processo web que
pode ser reciclado — e um aviso que depende de o processo continuar de pé é
um aviso que às vezes não sai, sem ninguém descobrir por quê.

**O custo, escrito para não virar surpresa:** se a rajada parar dentro da
janela, as últimas mensagens não geram um segundo aviso do sistema. A
pessoa já foi avisada pelo primeiro; lista, crachá e contador continuam
certos, porque nenhum deles depende disto.

## S68. O aparelho que troca de dono — resolvido no navegador

O endpoint de push pertence ao NAVEGADOR, não à pessoa. Um computador
compartilhado com um perfil do Chrome só produz o mesmo endpoint para quem
quer que entre nele.

**Dentro do tenant** a UNIQUE de `(tenant_id, endpoint_hash)` resolve: a
reinscrição vira UPDATE e o `membership_id` passa a ser de quem entrou
agora. Uma linha, um dono.

**Entre tenants** a primeira tentativa foi uma função `SECURITY DEFINER`
para revogar a linha do outro escritório. **Ela não funciona**, e a razão é
S2: `FORCE ROW LEVEL SECURITY` vale também para o dono das tabelas, então
nem o `elo_owner` atravessa. Fazê-la funcionar exigiria um papel com
`BYPASSRLS` — a porta que o MVP 1.0 fechou com cuidado, aberta para sempre
por causa de um caso de borda.

A saída escolhida é melhor, e não um consolo: ao ativar as notificações, se
já existe inscrição neste navegador que não pertence a quem está entrando,
o cliente chama `unsubscribe()` — o que mata o endpoint **no provedor**. A
partir daí qualquer linha antiga apontando para ele, em qualquer
escritório, recebe 410 na primeira tentativa e é revogada sozinha.

O problema é resolvido na única camada que enxerga os dois lados, sem que o
banco precise enxergar além do próprio tenant.

## S69. O service worker guarda a casca, e só a casca

Um service worker que guarda respostas por padrão acaba guardando conversa,
cliente, anexo e documento fiscal — em disco, fora do banco, sem RLS, sem
expiração e sem ninguém saber. Numa máquina compartilhada do escritório
isso é o histórico de atendimento de um cliente à disposição da pessoa
seguinte, mesmo depois do logout.

A regra virou código: **nada sob `/api` é tocado.** O que se guarda são
cinco arquivos — a página de sem-conexão, o manifesto e três ícones — que
não contam nada sobre ninguém.

E navegação é **rede primeiro, sempre**. Offline, aparece uma tela que diz
que está offline; nunca uma cópia de ontem apresentada como atual. Num
escritório de contabilidade, responder a partir de uma conversa
desatualizada é responder errado ao cliente.

## S70. A atualização não acontece sozinha

Nada de `skipWaiting()` no `install`. Trocar o worker debaixo de uma aba
que está com a conversa aberta recarrega a página — e recarregar enquanto a
pessoa digita a resposta de um cliente perde o texto.

A faixa aparece ("Uma nova versão do ELO está disponível") e quem escolhe a
hora é quem está usando.

## S71. O crachá vem do banco, sempre

A tentação é diminuir o contador no cliente quando a pessoa abre uma
conversa. É mais rápido e está errado na segunda aba: ela abre num lugar, o
outro continua com o número velho, e a partir daí os dois divergem para
sempre.

Qualquer evento de realtime faz a tela PERGUNTAR de novo. E a condição é
exatamente a mesma do filtro "Não lidos" (`whereNaoLidos`), num lugar só —
se o crachá dissesse cinco e o filtro mostrasse três, a equipe pararia de
confiar nos dois.

O `setAppBadge` do ícone da PWA é o EXTRA; o crachá dentro do Elo é o
principal, porque funciona em qualquer navegador.

## S72. O destino sobrevive ao login

Sexta-feira, 22h. Chega a notificação, a pessoa clica, e a sessão do Elo
venceu ao meio-dia. Sem tratamento ela faz login, volta para a home, e o
atendimento que a notificação prometia sumiu — a interrupção aconteceu e o
serviço não.

O caminho pedido é guardado num cookie curto (15 min, `HttpOnly`,
`SameSite=Lax`) antes de mandar para a entrada, e o `/api/auth/exchange` o
consome e o queima junto com o token. `Lax` é o que permite o cookie viajar
na volta do Fiscale; `Strict` o bloquearia exatamente quando ele precisa
existir.

Duas coisas que não se abrem mão:

- **O caminho é validado** (`caminhoSeguro`). Sem isso, `?para=//evil.com`
  transformaria o Elo num redirecionador aberto — phishing usando o domínio
  do escritório como trampolim.
- **O destino não é autoridade.** O `conversationId` que vem do push decide
  qual URL abrir, e nada mais: sessão, tenant, RBAC e RLS continuam
  obrigatórios do outro lado, e atendimento de outro escritório não é
  encontrado.

Isso exigiu um `middleware.ts` — um Server Component não sabe a própria
URL. Ele tem uma responsabilidade só: contar o caminho pedido para a
página. **Não autentica, não autoriza, não lê banco.** Colocar autorização
ali criaria uma segunda porta de decisão, mais fraca que a primeira e fácil
de esquecer quando uma rota nova aparecer.

## S73. `notifications.manage_self`, e nada além disso

Uma permissão nova, concedida a **todos** os papéis, inclusive VIEWER. Ela
é só isto: as MINHAS preferências e os MEUS aparelhos.

Não existe permissão para mexer no aviso de outra pessoa, e a rota é a
prova: não há parâmetro de membership, o vínculo vem da sessão. Desligar o
alerta de um colega é desligar o trabalho dele sem que ele saiba.

Um VIEWER sem essa permissão não conseguiria nem desligar o som que ele
mesmo ouve — e som que não se desliga é som que faz a pessoa desligar o
sistema.

## S74. O que a auditoria registra, e o que ela nunca vê

`PUSH_SUBSCRIBED`, `PUSH_UNSUBSCRIBED`, `NOTIFICATION_PREFERENCE_CHANGED`.

Fora: o endpoint, o `p256dh` e o `auth`. O endpoint é um **segredo de
capacidade** — quem o tem entrega notificação naquele aparelho —, e a
trilha de auditoria é lida por gente e sobrevive a backups. Do aparelho
fica o RÓTULO ("Chrome no Windows"), que é o que alguém lendo a trilha
precisa para reconhecer o que aconteceu.

Pela mesma razão, nenhuma rota devolve o endpoint: a tela recebe o
`endpointHash`, que serve para o navegador reconhecer "este dispositivo sou
eu" e para mais nada.

## S75. Sem push crítico, e sem inventar prioridade

Todo aviso sai com `Urgency: normal` e sem `requireInteraction`. Não existe
alerta que fique preso na tela até alguém dispensar, e não existe nível de
emergência.

Marcar tudo como urgente é o caminho mais curto para o sistema operacional
parar de dar atenção ao aplicativo — e para a pessoa desligar tudo. O
"não perturbe" silencia apenas o SOM dentro do Elo; o silêncio das
notificações do sistema é do Windows e do celular, e duplicar esse controle
aqui daria dois lugares para desligar a mesma coisa.

## S76. Trocar o par VAPID invalida todas as inscrições

Não há rotação suave, ao contrário do token de troca (que aceita duas
chaves na virada). O navegador amarra a inscrição à chave pública com que
ela foi criada; com um par novo, cada pessoa precisa ativar as notificações
de novo.

Está escrito no `.env.example` e no script `npm run vapid`, em voz alta,
porque é o tipo de coisa que se descobre no dia em que o escritório inteiro
para de receber aviso e ninguém liga uma coisa à outra.
