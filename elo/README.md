# Fiscale Elo — notificações e PWA (MVP 1.7)

Base, identidade, clientes, interface, atendimento, mensagens, mídia — e
agora **o Elo avisa**: aplicativo instalável, notificação do sistema com o
navegador fechado, crachá de não lidos e preferências por funcionário.

**Não** tem WhatsApp nem IA: isso é MVP 1.8 em diante
([ELO_ROADMAP.md](../ELO_ROADMAP.md)).

O que existe hoje:

- **MVP 1.0 — fundação.** Next.js, Postgres 16 em Docker, migrations,
  isolamento entre tenants com Row Level Security, health check, log
  estruturado, tratamento de erros.
- **MVP 1.1 — identidade.** Identity/Membership, sessão revogável, RBAC,
  departamentos, e a entrada Fiscale → token de troca Ed25519 → sessão
  própria do Elo.
- **MVP 1.2 — projeção de clientes.** Sincronização outbound assinada,
  normalização de telefone, casamento telefone → cliente, fila local com
  retry no Fiscale.
- **MVP 1.3 — interface.** Início, Atendimentos, Clientes, perfil do
  contato, Configurações; tema claro/escuro/sistema; responsivo.
- **MVP 1.4 — atendimento.** Conversation, visualização, responsável,
  transferência, status, tags, notas internas, histórico e auditoria.
- **MVP 1.5 — mensagens.** Message e MessageView, ordem por `sequence`,
  idempotência, paginação por cursor, realtime por SSE e não lidas por
  funcionário.
- **MVP 1.6 — mídia.** MessageAttachment, abstração de storage, validação
  por magic bytes, download autenticado com `Range`, player de áudio e
  gravação de voz.
- **MVP 1.7 — notificações e PWA.** Manifesto e ícones próprios, service
  worker, Web Push com VAPID escrito sobre o `crypto` do Node,
  preferências por pessoa, prévia desligada por padrão, crachá de não
  lidos, som discreto, agrupamento e tela de sem-conexão.

No Fiscale, cinco arquivos: três novos (`fiscale_sessoes.py`,
`fiscale_elo.py`, `fiscale_elo_sync.py`) e alterações pontuais em
`fiscale_server.py` e `web/home_portal.html`. Nenhum módulo fiscal foi
tocado.

---

## Versões

| Peça | Versão | Observação |
|---|---|---|
| Node.js | **24.19.0** | LTS desde 10/2025. Prisma 7 pede `^20.19 \|\| ^22.12 \|\| >=24`; Next 16, `>=20.9`. Sem warning de engine |
| Next.js | **16.3.0** | App Router, Turbopack |
| React | 19.2.8 | |
| TypeScript | **5.9.3** | Não o 7.0. O `typescript-eslint` que o `eslint-config-next` 16 usa ainda tem como alvo o 5.x — subir agora trocaria uma versão por um lint quebrado |
| ESLint | **9.39.5** | Não o 10. O `eslint-plugin-react` do `eslint-config-next` declara peer `^9.7`; com o 10 o npm emitia `ERESOLVE` |
| Prisma | **7.9.1** | Query Compiler + driver adapter `@prisma/adapter-pg` |
| PostgreSQL | **16.10-alpine** | Em container. O 9.6 da máquina fica intocado |
| Vitest | 4.1.10 | |

---

## Como subir

```bash
cp .env.example .env           # troque TODAS as senhas
cp .env.test.example .env.test # mesmas senhas, banco elo_test
cp .env.dev.example .env.dev   # mesmas senhas, banco elo_dev
npm install
npm run db:up                  # Postgres 16 no Docker, porta 5433
npm run prisma:deploy          # migrations no banco de trabalho
npm run db:migrate:test        # migrations no banco da suite
npm run db:migrate:dev         # migrations no banco de dev manual
npm run prisma:generate        # gera o cliente
npm run dev                    # http://localhost:3000
```

**Três bancos, no mesmo container**, com as mesmas migrations e as mesmas
permissões:

| Banco | Para quê | O que vive nele |
|---|---|---|
| `elo` | dia a dia; é o que a integração com o Fiscale alimenta | a projeção real do escritório |
| `elo_test` | só `npm test` | o que a suíte cria e apaga o tempo todo |
| `elo_dev` | mexer à mão: seed, simulação, conferir tela | só dado sintético |

A separação não é organização: `npm test` cria e apaga tenants sem parar, e
fazer isso ao lado do dado real deixava sobra em cima dele. `tests/setup.ts`
recusa rodar fora do `elo_test`.

O `elo_dev` **nasce vazio e continua sem nada do escritório**. Para ter o que
olhar, crie escritório e pessoas com `npm run admin` usando nomes inventados;
o `seed:dev` só acrescenta etiquetas e atendimentos sobre clientes que já
existam ali.

Num volume criado antes deste bootstrap, `elo_test` e `elo_dev` não existem —
crie-os à mão com as mesmas permissões do
[`00-roles.sh`](docker/postgres/initdb/00-roles.sh) e rode `npm run
db:migrate:test` e `npm run db:migrate:dev`.

Conferência rápida:

```bash
curl http://localhost:3000/api/health
```

Para ter alguém para entrar (o Elo não cria tenant nem pessoa sozinho):

```bash
npm run admin -- tenant:criar alfa "Alfa Assessoria"
```

Depois `pessoa:criar` com o `tenantId` devolvido, usando como último
argumento **o login do Fiscale** daquela pessoa — é por ele que a troca
encontra quem entrou.

### Ligando o Fiscale

1. No Fiscale, crie `dados/elo_config.json`:
   `{"url":"http://127.0.0.1:3000","tenant_id":"<id do tenant>"}`
2. Clique em **🔗 Elo** na barra do Fiscale. No primeiro uso ele gera o par
   Ed25519, guarda a privada em `dados/elo_chave_ed25519.json` e imprime no
   console a linha `ELO_EXCHANGE_PUBLIC_KEYS=…` já pronta.
3. Cole essa linha no `.env` do Elo e reinicie o Elo.
4. Clique em **🔗 Elo** de novo. Abre em aba nova, já autenticado.

> Em desenvolvimento use `127.0.0.1` nos dois lados. `localhost` e
> `127.0.0.1` são hosts diferentes para o navegador, e o cookie `SameSite=Lax`
> se comportaria como cross-site na volta.

Se o Elo estiver fora do ar, o botão avisa e nada mais acontece — login e
uso fiscal do Fiscale não dependem dele em ponto nenhum.

`{"status":"ok", ...}` com HTTP 200 = aplicação e banco no ar.
`{"status":"degraded", ...}` com HTTP 503 = aplicação viva, banco fora.

## Comandos

| Comando | O que faz |
|---|---|
| `npm run dev` | servidor de desenvolvimento |
| `npm run build` | build de produção |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | suíte completa (precisa do `elo_test` no ar) |
| `npm run verify` | typecheck + lint + test + build, na ordem |
| `npm run db:up` / `db:down` | sobe/derruba o Postgres |
| `npm run db:reset` | **apaga o volume** — leva `elo`, `elo_test` e `elo_dev` junto, inclusive o dado real do escritório — e recria os três já migrados |
| `npm run db:espera` | espera o Postgres do container aceitar conexão |
| `npm run prisma:migrate` | cria e aplica migration a partir do schema |
| `npm run prisma:deploy` | aplica migrations existentes (produção) |
| `npm run db:migrate:test` | aplica as mesmas migrations no `elo_test` |
| `npm run db:migrate:dev` | aplica as mesmas migrations no `elo_dev` |
| `npm run admin -- <cmd>` | provisionamento (tenant, pessoa, departamento) |
| `npm run chaves:troca` | gera par Ed25519 para teste ou rotação |

---

## Banco: três papéis, três níveis de poder

| Papel | Quem usa | Pode |
|---|---|---|
| `postgres` | só o script de bootstrap, uma vez | tudo — por isso nunca mais aparece |
| `elo_owner` | `prisma migrate` | DDL, `CREATEDB` (shadow database). **Sem** SUPERUSER, **sem** BYPASSRLS |
| `elo_app` | a aplicação em execução | só `SELECT/INSERT/UPDATE/DELETE` nas tabelas com RLS ligado |

A separação está nas URLs: `DATABASE_MIGRATION_URL` (owner, usada só pela CLI
do Prisma via `prisma.config.ts`) e `DATABASE_URL` (app, usada pelo driver
adapter em `src/server/db/client.ts`).

Superusuário **ignora RLS em silêncio** — é por isso que nem o owner é um.

## Isolamento entre tenants

Quatro camadas, e nenhuma delas confia na anterior:

1. **`tenant_id` em toda tabela de negócio**, mesmo quando derivável do pai.
2. **Row Level Security ligado e `FORCE`** em todas elas. `FORCE` faz a
   política valer também para o dono da tabela — sem ele, `elo_owner` leria
   tudo. É o erro mais comum em RLS.
3. **Papel runtime sem `BYPASSRLS`**, sem DDL, sem acesso a
   `_prisma_migrations`.
4. **Testes automatizados** que quebram o build se algo atravessar.

A política é sempre a mesma:

```sql
USING (tenant_id = app_tenant_id())
WITH CHECK (tenant_id = app_tenant_id())
```

`WITH CHECK` importa tanto quanto o `USING`: sem ele, o tenant A não *leria*
dado de B, mas conseguiria *gravar* linha carimbada com o id de B.

### Falha fechada

Sem `app.tenant_id` definido, `app_tenant_id()` devolve `NULL`, a comparação
vira `NULL` e nenhuma linha satisfaz a política. Esquecer de definir o tenant
dá tela vazia — nunca vazamento. Valor inválido (`'lixo'`) também devolve
`NULL`: a função captura a exceção do cast em vez de deixá-la abrir alguma
coisa.

### Contexto do tenant e o pool de conexões

Este é o ponto delicado, e o motivo de existir `src/server/tenancy/`.

O RLS lê `app.tenant_id` da **sessão**, e sessão no Postgres é a **conexão** —
que vive num pool, emprestada e devolvida o tempo todo. Com `SET` comum:

```sql
SET app.tenant_id = '<A>'     -- fica colado na conexão
```

o valor sobreviveria ao fim da requisição de A. A requisição seguinte, do
tenant B, poderia pegar aquela conexão e, se por qualquer motivo não
redefinisse o valor, leria os dados de A. Sem erro, sem log, sem sintoma.

A saída é nunca sair do escopo da transação:

```sql
BEGIN;
SELECT set_config('app.tenant_id', '<A>', true);   -- `true` = LOCAL
-- consultas
COMMIT;                                            -- o Postgres desfaz sozinho
```

O terceiro argumento `true` é a linha mais importante do projeto. Com ele, o
banco reverte o ajuste no `COMMIT` **e** no `ROLLBACK`. Não há caminho —
exceção, timeout, `return` no meio — que devolva a conexão suja ao pool.

Isso vive num lugar só, `withTenant()`. `SET LOCAL` não se escreve em nenhum
outro arquivo:

```ts
import { withTenant } from "@/server/tenancy";

const contatos = await withTenant(tenantId, async (tx) => {
  // `tx` já está preso ao tenant. Consulta sem `where` não vaza:
  // quem filtra é o banco.
  return tx.contact.findMany();
});
```

Criar tenant não precisa de conexão privilegiada: `provisionTenant()` gera o
uuid antes e abre a transação já com ele fixado, então o `WITH CHECK` aprova
(`id = app_tenant_id()`).

### Migration nova? Chame `elo_apply_rls()`

Toda migration que criar tabela com `tenant_id` precisa terminar com:

```sql
SELECT elo_apply_rls();
```

A função liga RLS, força, cria a política e **só então** concede acesso ao
`elo_app`. É deliberado não haver `ALTER DEFAULT PRIVILEGES`: se tabela nova
ganhasse `SELECT` automático, uma migration distraída abriria um vazamento
silencioso. Esquecer a chamada quebra `tests/rls-guard.test.ts`, não a
produção.

---

---

## Identidade: por que Identity + Membership, e não User

No MVP 1.0 existia `User` com `tenantId`. Isso impede a mesma pessoa de
participar de dois escritórios: seriam duas linhas sem parentesco, sem
forma de dizer que é a mesma Aline. Corrigir depois exigiria migrar dados
vivos. O banco estava vazio, então a separação foi feita agora:

```
Identity ──< IdentityProvider        a PESSOA (global) e de onde ela vem
    │
    └──< Membership >── Tenant       a pessoa DENTRO de um escritório
              │
              ├──< Session           sessão é sempre de um membership
              └──< DepartmentMembership >── Department
```

`Membership` é o que o 1.0 chamava de `User`, com o nome certo. Papel
(`role`) mora nele — é o vínculo que tem poder, não a pessoa.

**Papel ≠ departamento.** Papel é nível de permissão (`OWNER`, `ADMIN`,
`MANAGER`, `AGENT`, `VIEWER`); departamento é área operacional (Fiscal,
Contábil, DP…) e é **dado**, criado por tenant, não constante no código.
Aline pode ser ADMIN no Fiscal; outra pessoa, AGENT no DP. E uma pessoa
pode estar em vários departamentos.

### RLS nas tabelas globais

`identities` e `identity_providers` não têm `tenant_id` — a pessoa é uma
só. O que as isola é a **associação**: só são visíveis as identidades com
membership no tenant corrente.

```sql
USING (EXISTS (SELECT 1 FROM memberships m
                WHERE m.identity_id = identities.id
                  AND m.tenant_id = app_tenant_id()))
```

Quem não tem vínculo com o tenant não existe para ele — nem pelo id, nem
pelo e-mail, que é único no sistema inteiro. Há teste para os dois.

O `INSERT` é liberado às cegas (`WITH CHECK (true)`), o que não é brecha:
inserir não lê, e a linha continua invisível até haver membership. A
consequência prática aparece em `provisionMembership()`, que cria a
identidade com SQL sem `RETURNING` — Prisma sempre usa `RETURNING`, e ler
de volta ali ainda seria proibido.

---

## RBAC

Pergunta-se por permissão, nunca por papel. `if (role === "ADMIN")` não
existe no projeto: a tradução papel → permissão mora só em
`src/server/auth/permissions.ts`.

| Permissão | OWNER | ADMIN | MANAGER | AGENT | VIEWER |
|---|:-:|:-:|:-:|:-:|:-:|
| `tenant.manage` | ✅ | | | | |
| `users.manage` | ✅ | ✅ | | | |
| `users.read` | ✅ | ✅ | ✅ | ✅ | |
| `departments.manage` | ✅ | ✅ | ✅ | | |
| `departments.read` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `sessions.manage` | ✅ | ✅ | | | |
| `sessions.read` | ✅ | ✅ | ✅ | | |
| `audit.read` | ✅ | ✅ | | | |

Encerrar as **próprias** sessões não passa por permissão — é a pessoa
agindo sobre si. `sessions.manage` existe para derrubar sessão de outra
pessoa, que ainda não tem rota.

---

## Entrada: Fiscale → Elo

Login compartilhado **não** é cookie compartilhado. Cada sistema tem a sua
sessão; o que atravessa é um bilhete de 90 segundos.

```
1. pessoa logada no Fiscale clica em "Elo"
2. Fiscale assina um token Ed25519  (chave privada só lá)
3. a tela envia por formulário POST — nunca na URL
4. Elo confere assinatura, iss, aud, exp, iat, jti, tid, sub
5. Elo queima o jti  (atômico; segunda tentativa morre aqui)
6. Elo confere tenant ativo, pessoa ativa, vínculo ativo
7. Elo cria sessão PRÓPRIA e devolve o cookie dele
8. o token de troca não serve para mais nada
```

**Assimétrico de propósito.** A privada fica no escritório; o Elo tem só a
pública. Quando o Elo for para um VPS, invadir aquele servidor dará poder
de *conferir* token, nunca de *emitir* — que é o que aconteceria com
segredo compartilhado.

**Sem confusão de algoritmo.** O verificador é escrito à mão justamente
para não haver o `switch (header.alg)` que a família de ataques do JWT
explora. Há um caminho só, Ed25519. `alg` é conferido, não obedecido:
`none` e `HS256` são recusados no cabeçalho, antes de qualquer conta.

**Token no corpo, nunca na query string.** URL fica no histórico do
navegador, viaja no `Referer` e entra no log de acesso de qualquer proxy.

### Anti-replay sem Redis

A decisão original previa `jti` no Redis. Reavaliada: são alguns tokens por
dia, o Postgres já está no ar, e subir um segundo banco para guardar
dezenas de linhas custaria operação sem ganho. Mas o argumento decisivo é
técnico — a atomicidade vem de graça:

```sql
INSERT INTO consumed_exchange_tokens (jti, expires_at)
VALUES ($1, $2) ON CONFLICT DO NOTHING
```

Um comando. Quem grava é o primeiro; o segundo colide com a chave primária
e afeta zero linhas. Não existe o intervalo entre "consultar" e "gravar"
onde duas requisições simultâneas passariam as duas.

A tabela é **somente-escrita**: o `elo_app` não tem `SELECT` nela. Detectar
replay nunca precisou ler, e sem leitura ninguém enumera token alheio. A
única exceção é o grant de coluna em `expires_at`, que a faxina precisa
para avaliar o próprio `WHERE`.

### Rotação de chave

O `kid` vai no cabeçalho do token. Para rotacionar sem parada: gere o par
novo, acrescente a pública ao JSON **mantendo a antiga**, troque a privada
no Fiscale, e só então remova a antiga. Com as duas configuradas, nenhum
token em voo se perde. Há teste dos dois estados.

---

## Sessão do Elo

Tabela, não JWT — JWT de sessão não se revoga, e "encerrar sessão" viraria
promessa vazia. O banco guarda **SHA-256 do token**, nunca o token: quem
levar um backup leva hashes, que não servem para entrar.

### O cookie é `<tenantId>.<token>`

Há um problema de ordem: para ler a tabela de sessões é preciso o contexto
de RLS aberto, e o contexto exige o tenant, que só se descobre lendo a
sessão. As saídas usuais seriam uma função `SECURITY DEFINER` ou um papel
com `BYPASSRLS` — as duas abrem no código a porta que o MVP 1.0 fechou.

A saída escolhida é o cookie carregar o tenant junto. E é preciso ser
explícito sobre o que isso significa:

> o tenant do cookie **não é autoridade**. É chave de busca.

A consulta exige que o par (tenant, hash do token) case com uma linha real.
Trocar o tenant no cookie não dá acesso ao outro escritório — dá 401,
porque lá não existe sessão com aquele hash. O segredo continua sendo só o
token. Testado, inclusive por HTTP.

| Atributo | Valor | Por quê |
|---|---|---|
| `HttpOnly` | sempre | XSS não leva a sessão embora |
| `Secure` | produção | em dev o Elo é http; com Secure o cookie não existiria |
| `SameSite` | `Lax` | a entrada vem de navegação iniciada pelo Fiscale; com `Strict` o cookie não acompanharia esse primeiro salto |
| `Path` | `/` | |
| expiração | 12 h | e a sessão no servidor expira junto |

---

## Provisionamento — ferramenta, não rota

O débito do MVP 1.0 (`provisionTenant` sem rota nem autorização) foi pago
**não** criando um endpoint. Escritório novo nasce algumas vezes por ano,
por decisão humana; expor isso na web seria superfície de ataque para uma
operação que não precisa dela.

Virou CLI, com trava de ambiente: sem `ELO_ADMIN_TOOL=1` nada roda. Se
alguém importar essas funções dentro de uma rota, o processo web não tem a
variável e a chamada falha alto, na hora.

```bash
npm run admin -- tenant:criar alfa "Alfa Assessoria"
npm run admin -- pessoa:criar <tenantId> OWNER ana@escritorio.com "Ana" ana
npm run admin -- pessoa:vincular <tenantId> VIEWER <identityId>
npm run admin -- depto:criar <tenantId> fiscal "Fiscal"
```

Tudo roda como `elo_app` — nenhum papel privilegiado. `pessoa:vincular` é o
caminho de quem já existe entrando num segundo escritório, e exige o
`identityId`: o tenant novo não consegue procurar por e-mail, porque
descobrir "esse e-mail existe no sistema" de um tenant que não conhece a
pessoa seria vazamento.

---

## Testes

`npm test` — **615 testes, 27 arquivos**. Rodam contra o banco `elo_test`,
nunca contra o `elo`.

A configuração vem do `.env.test`, que `tests/setup.ts` carrega por cima do
`.env` — só as duas URLs; chave de troca, storage e VAPID continuam vindo do
`.env`. E há uma trava: se a `DATABASE_URL` não apontar para `elo_test`, a
suíte **inteira** para antes do primeiro teste, dizendo em qual banco ela ia
mexer. Sem isso, um `.env.test` ausente ou mal copiado devolveria os testes
ao banco de trabalho em silêncio, e o estrago só apareceria depois, como
sobra.

A tabela abaixo cobre os arquivos do MVP 1.1; os demais seguiram o mesmo
padrão nas fases seguintes.
| Arquivo | O que prova |
|---|---|
| `cross-tenant.test.ts` | isolamento pelo caminho da aplicação: A lê A; A não lê, altera nem apaga B; busca por id conhecido do outro tenant volta vazia; sem tenant válido, nada |
| `rls-sql.test.ts` | o mesmo em SQL cru, conexão `pg` direta, sem Prisma. Mais o **controle negativo** |
| `pool-reuse.test.ts` | vazamento por reutilização de conexão |
| `rls-guard.test.ts` | guarda estrutural contra regressão em migration futura |
| `foundation.test.ts` | sonda, erros e log (inclusive a redação de segredos) |
| `exchange.test.ts` | token de troca: assinatura adulterada, corpo adulterado, chave errada, `kid` desconhecido, `alg=none`, `alg=HS256`, issuer, audience, expirado, emitido no futuro, vida longa demais, sem `jti`, pessoa/tenant/vínculo inexistente ou inativo, rotação de chave |
| `replay.test.ts` | uso único, inclusive **concorrente** |
| `session-rbac.test.ts` | sessão, revogação, expiração, cookie adulterado, RBAC, departamentos, pessoa em dois tenants |

No Fiscale, `python teste_fiscale_sessoes.py` — 33 verificações de sessão
persistente e emissão de token, sem rede e em pasta temporária.

Três escolhas que fazem esses testes valerem alguma coisa:

- **O pool roda com uma única conexão** (`tests/setup.ts`). Toda operação
  reaproveita fisicamente a conexão que a anterior devolveu, então a suíte
  inteira — não só um caso preparado — exercita o cenário de vazamento.
- **Controle negativo.** Um teste conecta como superusuário e confirma que
  as linhas dos *dois* tenants existem mesmo. Sem ele, "não vazou" e "a
  tabela está vazia" dariam o mesmo verde. Outro conecta como `elo_owner`
  e confirma que nem o dono passa — é para isso que serve o `FORCE`.
- **Concorrência de verdade no teste de replay.** Além de `Promise.all`
  sobre o serviço, há duas conexões `pg` distintas inserindo o mesmo `jti`
  ao mesmo tempo, 15 vezes. Se a proteção dependesse de ordem no event loop
  do Node, sumiria ali.

Três escolhas que fazem esses testes valerem alguma coisa:

- **O pool roda com uma única conexão** (`tests/setup.ts`). Toda operação
  reaproveita fisicamente a conexão que a anterior devolveu, então a suíte
  inteira — não só um caso preparado — exercita o cenário de vazamento.
- **Controle negativo.** Um teste conecta como superusuário e confirma que as
  linhas dos *dois* tenants existem mesmo. Sem ele, "não vazou" e "a tabela
  está vazia" dariam o mesmo resultado verde. Outro conecta como `elo_owner`
  e confirma que nem o dono passa — é para isso que serve o `FORCE`.
- **Verificação por mutação.** Trocando o `set_config(..., true)` por
  `false` em `withTenant`, `pool-reuse.test.ts` falha em dois pontos, sendo
  um deles uma consulta *fora* de `withTenant` devolvendo o contato do tenant
  anterior. O teste detecta o vazamento real, não só a si mesmo.

---

## Estrutura

```
elo/
  docker/postgres/initdb/00-roles.sh   bootstrap dos papéis (roda uma vez)
  prisma/
    schema.prisma                      Tenant, Identity, Membership,
                                       Department, Session, AuditLog…
    migrations/                        5 migrations, ver abaixo
    legacy/                            SQL anterior ao scaffold
  scripts/
    admin.ts                           CLI de provisionamento
    gerar-chaves-troca.mjs             par Ed25519 (teste e rotação)
  src/
    app/
      page.tsx                         página de fundação
      api/health                       sonda
      api/auth/{exchange,me,logout,sessions}
      api/departments                  primeira rota com RBAC
    generated/prisma/                  cliente Prisma (não versionado)
    server/
      admin/       provisioning.ts — atrás da trava ELO_ADMIN_TOOL
      auth/        exchange-token, exchange, replay, session,
                   permissions, context, request, audit
      db/          client.ts (adapter + pool), health.ts (sonda)
      tenancy/     withTenant — o único caminho para dado de tenant
      logging/     logger JSON com redação de segredos
      errors/      AppError + resposta HTTP sem stack trace
    env.ts                             validação do ambiente com zod
  tests/
  docker-compose.yml
```

### Migrations

| Migration | O que faz |
|---|---|
| `init_foundation` | tabelas do MVP 1.0 |
| `rls_multitenancy` | `app_tenant_id()`, `elo_apply_rls()`, políticas, grants |
| `identity_session_rbac` | Identity/Membership/Session/Department/AuditLog; remove `users` |
| `rls_identity` | RLS das tabelas novas, inclusive as globais |
| `purge_column_grant` + `purge_select_policy` | as duas permissões mínimas que a faxina de `jti` precisava |

`prisma/legacy/` guarda os dois arquivos escritos antes do scaffold:

- `schema.sql.pre-prisma` — as 7 tabelas em `SERIAL`, sem `tenant_id`, de
  quando o Elo seria um serviço isolado. Substituído pelo schema Prisma.
- `0001_rls_multitenancy.sql.pre-scaffold` — a migration de RLS original. A
  lógica foi mantida quase inteira; o que mudou está registrado em
  [ELO_DECISOES.md](../ELO_DECISOES.md), revisão 2.

Ficam como referência histórica. Nenhum dos dois roda.

---

## Segurança operacional

- O container publica em `127.0.0.1:5433`. O banco **não** aparece para a
  rede do escritório.
- `.env` está no `.gitignore`; só o `.env.example` é versionado, e com
  senhas de mentira.
- O health check devolve alcance e latência. Não devolve connection string,
  host, usuário, versão do Postgres nem stack trace.
- O logger corta `password`, `senha`, `token`, `authorization`, `cookie`,
  `DATABASE_URL`, `pfx`, `dpapi` e afins, em qualquer nível do objeto.
- Erro de aplicação tem duas mensagens: a interna, que vai para o log, e a
  pública, genérica. Em produção a resposta não tem o campo `debug`.
- **Toda recusa de autenticação sai igual**: 401, "Autenticação
  necessária.", sem dizer se foi assinatura, expiração, replay, pessoa
  inexistente ou vínculo inativo. O código estruturado (`REPLAY`,
  `IDENTITY_NOT_FOUND`, …) fica no log e na auditoria. Distinguir isso na
  resposta entregaria ao atacante um oráculo de quem trabalha no
  escritório.
- A chave privada de troca **nunca** sai da máquina do Fiscale e não está
  em repositório nenhum. `.env.example` traz só nomes e um `{}` vazio.
- A auditoria registra `AUTH_EXCHANGE_SUCCESS`, `AUTH_EXCHANGE_FAILED`,
  `LOGIN_SESSION_CREATED`, `LOGOUT` e `SESSION_REVOKED` — sem token, sem
  cookie, sem hash de sessão. Há teste que confere isso.
- IP é guardado como hash com sal, não em claro.

---

# Projeção de clientes (MVP 1.2)

O Fiscale é a **fonte da verdade** do cadastro. O que existe aqui é uma
**projeção de leitura**: a cópia mínima para o Elo reconhecer quem está do
outro lado e continuar funcionando às 23h com o escritório fechado. Editar
a projeção não altera o Fiscale, e nada daqui volta para lá.

## O que atravessa — e o que não

O cadastro real (`state_clientes.json`) tem doze campos. O Elo recebe
**cinco**, mais o ativo/inativo:

| Vai | Fica no Fiscale | Por quê |
|---|---|---|
| `id` → `externalId` | `cert`, `certValidade` | certificado nunca sai da máquina do escritório — nem o **nome** do arquivo, que carrega razão social e CNPJ |
| `nome` | `regime` | dado de apuração, não de atendimento |
| `cnpj` | `ie`, `im` | idem |
| `email` | `mun`, `uf` | não ajuda a reconhecer nem a responder; entraria "porque um dia talvez" |
| `tel` | | |

**`nome fantasia` não existe no cadastro do Fiscale.** Estava na lista
pedida, mas coluna que nunca teria valor é peso morto; quando a origem
ganhar o campo, acrescentar é uma migration aditiva de uma linha.

A lista branca é aplicada nos **dois lados**. No Fiscale, `projetar()`
monta o payload campo a campo em vez de copiar o dicionário e remover o
proibido — assim campo novo no cadastro não vaza sozinho numa versão
futura. No Elo, campo fora do contrato faz o payload ser **recusado**, não
descartado em silêncio: se o Fiscale começar a mandar `regime`, alguém
precisa descobrir no mesmo dia.

## Autenticação da integração

Máquina falando com máquina não usa cookie de usuário nem token de login.
Cada requisição é **assinada** com Ed25519, com par próprio — separado do
par que assina o login, para que cada um rotacione no seu tempo.

```
x-elo-key-id      qual chave assinou
x-elo-timestamp   unix seconds, janela de ±300 s
x-elo-nonce       uuid, usado uma vez
x-elo-signature   Ed25519 sobre:

    POST
    /api/integrations/fiscale/customers/sync
    <timestamp>
    <nonce>
    <sha256 do corpo>
```

Método e caminho entram no texto assinado: sem eles, uma assinatura válida
para este endpoint serviria em qualquer outro que aceitasse o mesmo corpo.

**O tenant vem da chave.** Cada `kid` registrado aponta para um tenant, e o
`tenantId` do corpo — se vier — precisa concordar. Divergência é recusa
explícita, não descarte: erro de configuração e tentativa se parecem, e as
duas precisam aparecer.

Anti-replay pelo mesmo desenho do token de troca: `INSERT ... ON CONFLICT
DO NOTHING` na tabela de nonces, que é somente-escrita. Assinatura inválida
**não** queima o nonce — senão qualquer um inutilizaria nonces alheios
mandando lixo.

## Telefone

O ponto mais delicado da fase, porque amanhã isto casa mensagem de WhatsApp
com cliente. Número normalizado errado não dá erro: entrega a conversa de
um cliente na tela de outro.

**Na dúvida, não normaliza.** Nunca inventa DDD, nunca acrescenta o nono
dígito, nunca "conserta" número curto. O que não dá para normalizar com
certeza fica guardado como veio, marcado, e não participa do casamento
automático.

| Entrada | Resultado |
|---|---|
| `(81) 99999-1111`, `81999991111`, `+5581999991111` | `+5581999991111` |
| `(81) 3333-4444` | `+558133334444` |
| `99999-1111` (sem DDD) | `NO_AREA_CODE` — guardado, não casa |
| `8199991111` (10 dígitos começando em 9) | `INVALID` — completar seria inventar |
| `02699999999` (CPF no campo errado) | `INVALID` — DDD 02 não existe |
| vazio, `abc` | `INVALID` |

O campo `tel` do Fiscale é texto livre, então pode conter mais de um
número. A separação é conservadora: só em `/ ; , |` e a palavra "e".
Espaço **não** separa, senão `(81) 99999-1111` viraria dois pedaços. Daí a
tabela `external_customer_phones` — comprimir num campo só obrigaria a
escolher um número na hora de casar a mensagem.

### findCustomerByPhone

Três respostas, e a terceira é a que importa:

- `EXACT` — um cliente
- `NONE` — nenhum, ou o número não normalizou
- `AMBIGUOUS` — **dois ou mais**. Acontece de verdade: matriz e filial,
  contador que atende as duas, celular do sócio. Escolher em silêncio
  entregaria a conversa errada; quem decide é a pessoa no atendimento.

A função mora sozinha em `src/server/customers/lookup.ts` porque o adapter
do WhatsApp (MVP 1.7) vai chamar exatamente ela. Lógica de telefone
espalhada por rotas vira "pega o primeiro" em algum lugar.

## Idempotência

O Fiscale manda o `sourceVersion` dele e usa esse valor para decidir o que
enfileirar. Mas quem decide se a projeção mudou é o `contentHash` que o
**Elo** calcula sobre o que chegou — assim os dois não precisam produzir o
mesmo digest, e um bug no hash do Fiscale causa, no pior caso, um envio a
mais; nunca uma atualização perdida.

Payload igual duas vezes: a segunda conta como `unchanged` e o `updatedAt`
não se mexe. O `syncedAt` sim — ele é "visto por último", não "alterado".

## Full sync, ausência e inativação

`FULL` marca com `missingSince` quem não veio. **Não apaga e não inativa.**
Ausência pode ser exclusão na origem, mas também filtro novo ou exportação
pela metade — e as três se parecem daqui. Inativar de verdade só quando a
origem disser `active: false`, e mesmo aí o registro permanece: conversa
futura vai apontar para ele.

`INCREMENTAL` não marca ninguém como ausente.

## Fila local e retry (lado Fiscale)

Nada de Kafka nem RabbitMQ: é uma máquina, algumas dezenas de clientes, e o
que precisamos é não perder uma alteração porque a internet caiu. Um
SQLite (`elo_sync.db`) resolve.

- Salvar cliente compara o hash dos campos permitidos e enfileira **só quem
  mudou**. Mexer em regime, IE ou certificado não gera sincronização nenhuma.
- **Um pendente por cliente**: cinco edições seguidas viram um envio.
- A fila guarda "o cliente X mudou", **não o conteúdo**. O payload é montado
  na hora do envio, então o que sobe é o estado atual — não uma sequência
  de fotos velhas.
- Backoff 30 s → 2 min → 5 min → 15 min → 1 h. Máximo 20 tentativas, e
  depois disso `POST /api/elo/sync/tentar` libera tudo de novo.
- Uma thread daemon drena a fila a cada minuto. Silenciosa: Elo fora do ar
  não aparece na tela de quem está trabalhando.

### Elo offline

O cadastro é salvo primeiro; enfileirar vem depois e é gravação local. Se o
Elo estiver fora, o item fica pendente e a rotina fiscal segue como se o
Elo não existisse. **Nada no Fiscale espera pela rede.**

### Fiscale offline

O Elo opera com a última projeção. Toda resposta traz `syncedAt`, para a
tela poder dizer "sincronizado em" em vez de apresentar cópia antiga como
se fosse tempo real.

## Rotas

| Rota | Quem chama |
|---|---|
| `POST /api/integrations/fiscale/customers/sync` | o Fiscale, com assinatura |
| `GET /api/customers?q=…` | sessão de usuário — nome, documento ou telefone |
| `GET /api/customers?phone=…` | sessão — casamento telefone → cliente |
| `GET /api/customers?status=1` | sessão — diagnóstico da projeção |

No Fiscale: `POST /api/elo/sync` (full sob demanda), `POST
/api/elo/sync/tentar` (retry manual), `GET /api/elo/sync/estado`
(diagnóstico). Sem painel — endpoint que responde JSON basta nesta fase.

---

# Interface (MVP 1.3)

## Identidade visual

Vem do Fiscale e evolui a partir dele: petróleo `#10444E` e ciano `#7FD1DE`
— a mesma dupla do símbolo — com **azul `#1668B3` como cor de ação**. Verde
não entra: seria imitar o WhatsApp, e o Elo não é o WhatsApp; é a mesa de
trabalho do escritório, que por acaso fala WhatsApp.

O **modo escuro não é o claro invertido**. Tem paleta própria, azul-marinho
esverdeado (`#0B1620` de fundo), porque quem atende passa o dia na tela e
preto puro com branco puro cansa em duas horas.

Três opções: **Claro · Escuro · Sistema**. A escolha vive no `localStorage`
e é aplicada por um script no `<head>` **antes da primeira pintura** — sem
isso, quem usa o modo escuro leva um flash branco a cada carregamento.
"Sistema" acompanha o SO inclusive quando ele muda no meio do expediente.

A leitura da preferência passa por `useSyncExternalStore`, e não por
`useState` + `useEffect`: com efeito, o primeiro render mostraria o tema
errado e o segundo corrigiria — exatamente o flash que o script evita.

## Telas

| Tela | Conteúdo |
|---|---|
| **Início** | saudação, 3 cartões, rodapé com última sincronização e assinatura |
| **Atendimentos** | lista + área de conversa, filtros por prioridade |
| **Clientes** | projeção real, busca, perfil em gaveta |
| **Configurações** | Meu perfil · Área principal · Outras áreas · Aparência · Sessão |
| **Entrar** | sem sessão — explica que a porta é o Fiscale |

Todas as páginas autenticadas passam por `src/app/(app)/layout.tsx`, que
resolve a sessão **no servidor** antes de qualquer coisa renderizar.
Esconder item de menu nunca foi autorização: as rotas de API continuam
exigindo sessão por conta própria.

## Regras de UX que o código aplica

**A conversa é a protagonista.** CNPJ, e-mail e telefone **não** ficam
permanentemente na tela. O cabeçalho é limpo; os dados aparecem quando
alguém clica no cliente. Dado complementar sempre aberto vira ruído que
ninguém mais lê.

**Nada de dado inventado.** "0 atendimentos novos" é a verdade enquanto o
canal não existe. Uma lista de exemplo é a maneira mais rápida de o
escritório perder a confiança no que vê.

**A navegação mostra só o que existe.** Nenhum item desabilitado com "em
breve" — barra lateral de quem trabalha não é lugar de exibir roadmap.
"Não lidas", "Aguardando" e "Arquivadas" vivem como filtros dentro de
Atendimentos.

**Filtros por prioridade:** `Novos · Não lidos · Meus · Aguardando · Todos`.
A tela abre em *Novos* — quem chega de manhã vê o que está parado, não uma
lista geral onde o urgente se esconde.

**Texto didático não compete com trabalho real.** A explicação dos quatro
estados do atendimento aparece só enquanto não existe nenhum atendimento;
no primeiro, some sozinha.

**O nome é "Atendimentos"**, em toda parte. Não "caixa de entrada": o Elo
trata de atendimento, e caixa de mensagens é só o que se vê de fora.

## Identidade do funcionário: `Nome • Área`

Toda mensagem sairá assinada como **`Aline • Fiscal`** — automático, a
partir da identidade mais o departamento principal. Ninguém digita.

Sem área principal escolhida, sai só o nome. O sistema **não deduz** qual é
a principal: eleger "a primeira da lista" faria a assinatura que o cliente
lê mudar sozinha quando alguém reordenasse um cadastro.

O cliente vê, no canal, a **foto e o nome do escritório** como contato; a
pessoa aparece dentro da mensagem. Ver as decisões D1–D14 em
[ELO_DECISOES.md](../ELO_DECISOES.md), revisão 5.

## Acessibilidade

Quem age é `<button>`, quem navega é `<a>` — nenhuma `<div onClick>`, que
não recebe foco, não responde ao Enter e não é anunciada como controle.
Foco visível com `:focus-visible`, link "Pular para o conteúdo", `Esc`
fecha gaveta e menu, `aria-pressed` nos alternadores, `role="status"` no
carregando e `role="alert"` no erro. Status de cliente tem texto para
leitor de tela — cor sozinha não é informação.

## Responsividade

| Largura | Comportamento |
|---|---|
| ≥ 900px | navegação fixa à esquerda, duas colunas em Atendimentos |
| < 900px | navegação vira gaveta com ☰; colunas empilham |
| 375px | cartões em coluna única, gaveta do perfil ocupa a tela |

Três colunas espremidas num celular não é responsividade — é desistir de
ler.

## Componentes

`AppShell` · `EloLogo`/`EloWordmark` · `ThemeProvider`/`ThemeToggle` ·
`ConversationList` · `EmptyConversation` · `OutgoingPreview` ·
`CustomerBrowser` · `CustomerProfileDrawer` · `Avatar` · `EmployeeBadge` ·
`DepartmentBadge` · `StatusBadge` · `StatusDot` · `LoadingState` ·
`EmptyState` · `ErrorState`.

## Dados

A interface consome as APIs reais: `/api/customers` (lista, busca, telefone
e diagnóstico) pelo navegador, e a sessão pelo servidor via `pageSession()`.
Mock só em teste.

---

# Modelo de atendimento (MVP 1.4)

Mensagens ainda não existem. O que existe é o **caso**: quem é o cliente,
quem está responsável, em que pé está, quem já olhou, o que foi combinado
internamente e tudo o que aconteceu até aqui.

## A regra

**A conversa pertence ao tenant.** O funcionário é o responsável *atual*.
Transferir troca o responsável; não cria outro atendimento, não esconde o
anterior de ninguém, não reescreve o histórico. Sem isso, sair de férias
viraria conversa inacessível.

## Status

`NEW` · `IN_PROGRESS` · `WAITING_CUSTOMER` · `WAITING_OFFICE` · `RESOLVED` —
na tela: Novo, Em atendimento, Aguardando cliente, Aguardando escritório,
Resolvido.

Um atendimento nasce `NEW` **mesmo quando o cliente escolheu alguém**: ser
escolhido não é ter sido atendido. Vira `IN_PROGRESS` quando a pessoa
assume.

## Os quatro momentos, que são coisas diferentes

```
RECEBIDA     10:20   chegou              → conversations.createdAt
VISUALIZADA  10:22   alguém abriu        → firstViewedAt + conversation_views
ASSUMIDA     10:24   alguém é responsável→ firstAssignedAt + assignedMembershipId
RESPONDIDA   10:30   o cliente teve resposta → firstResponseAt (MVP 1.5)
```

Cada marco grava **uma vez**. "Tempo até a primeira resposta" não pode
mudar quando a segunda chega.

### Visualizar não é assumir

Abrir um atendimento registra visualização e nada mais. Assumir é um botão,
com nome. Se abrir assumisse, ninguém olharia a caixa por medo de virar
dono do problema — o oposto de uma caixa compartilhada.

`conversation_views` guarda uma linha por (atendimento, pessoa) com
`firstViewedAt`, `lastViewedAt` e `viewCount`. O evento `VIEWED` do
histórico entra só na primeira vez de cada pessoa: quem abre um caso o abre
dez vezes por dia, e registrar todas encheria a linha do tempo de ruído.

O painel "Quem visualizou" é **interno**. O cliente não vê.

## Concorrência

Duas pessoas clicando "Assumir" no mesmo segundo é o caso que quebra
implementação ingênua. A condição vive dentro do `UPDATE`:

```sql
UPDATE conversations SET assigned_membership_id = :eu, ...
 WHERE id = :id AND assigned_membership_id IS NULL
```

Uma linha afetada: assumi. Zero: alguém chegou antes, e a resposta diz
**quem** — "Este atendimento acabou de ser assumido por Aline • Fiscal".
Sem o nome, a pessoa clica de novo achando que travou.

Transferência e status usam **trava otimista** por `version`. Tela velha
recebe 409 em vez de sobrescrever a decisão de um colega em silêncio.

## Histórico

Uma linha do tempo só, em `conversation_events`. Duas tabelas paralelas
divergiriam, e "o que aconteceu com este atendimento?" passaria a ter duas
respostas.

Os campos que se consulta o tempo todo — de quem para quem, de qual status
para qual — são colunas, não chaves dentro de um JSON. Na tela sai em
português: *"Aline assumiu"*, *"Aline mudou o status de Em atendimento para
Resolvido"*.

## Nota interna

**Nunca vai para o cliente**, e isso é estrutural: nota é uma *tabela
separada* de mensagem. Um campo `interna: boolean` dentro de mensagem
deixaria a nota do escritório a um `false` de distância do WhatsApp do
cliente — e esse `false` chega um dia, num merge apressado. O histórico
registra que houve nota, nunca o texto.

## Departamento e fila

`assignedDepartmentId` é independente do responsável. "Fiscal, sem
responsável" é um estado válido — é a fila departamental.

## Quem o cliente pode escolher

`getCustomerVisibleAssignees()` devolve **nome, área e um id**. Não devolve
e-mail, papel, permissão nem dado administrativo — e a regra não é filtrar
na tela, é não buscar.

| Campo | Para quê |
|---|---|
| `visibleToCustomers` | aparece na lista do cliente. Padrão **false**: ninguém é exposto por ter sido cadastrado |
| `availableForAssignment` | pode receber atendimento novo. Padrão true |

São coisas diferentes: um gerente pode ser visível para consulta sem entrar
na distribuição; quem está de férias sai dos dois. E nenhum é "online" —
presença em tempo real é de outra fase.

A visibilidade **não** é derivada do papel. Nada de `role === MANAGER →
aparece`.

**Atendimento Geral** existe sempre. Obrigar o cliente a escolher um nome
para falar com o escritório é criar obstáculo onde deveria haver porta.

## Filtros

| Filtro | Significa |
|---|---|
| Novos | `status = NEW` |
| Não lidos | **eu** nunca abri — é pessoal, não do escritório |
| Meus | sou o responsável atual |
| Aguardando | esperando cliente ou escritório |
| Todos | tudo, inclusive resolvido |

## Permissões

`conversations.read` · `conversations.assign` · `conversations.transfer` ·
`conversations.status` · `notes.read` · `notes.write` · `tags.read` ·
`tags.manage`

AGENT transfere de propósito: passar um caso ao colega antes das férias é
rotina, não ato administrativo. `tags.manage` (criar a lista do escritório)
fica no MANAGER para cima — senão a carteira acaba com trinta variações de
"urgente".

VIEWER lê e não age.

## Dados de desenvolvimento

```bash
ELO_ALLOW_DEV_SEED=1 npm run seed:dev -- <tenantId>
npm run seed:dev:limpar -- <tenantId>
```

Três travas: recusa `NODE_ENV=production`, exige `ELO_ALLOW_DEV_SEED=1`, e
marca tudo com o canal `DEV` mais a etiqueta "Dados de teste". A limpeza
apaga pelo canal — nunca por data nem por "os últimos N".

Cria atendimentos sobre clientes que **já existem** na projeção. Continua
não havendo mensagem falsa.

## Rotas

| Rota | O que faz |
|---|---|
| `GET /api/conversations?filtro=…` | lista + contagens |
| `GET /api/conversations/:id` | detalhe, histórico, quem viu — e registra visualização |
| `PATCH /api/conversations/:id` | `assume` · `transfer` · `status` (com `expectedVersion`) |
| `POST /api/conversations/:id/notes` | nota interna |
| `POST /api/conversations/:id/tags` | aplica/remove etiqueta |
| `GET /api/tags` · `POST /api/tags` | etiquetas do escritório |
| `GET /api/assignees` | destinos de transferência |
| `GET /api/assignees?menu=cliente` | a lista que o cliente verá (MVP 1.7) |

As três ações ficam num `PATCH` só porque alteram a mesma linha e disputam
a mesma trava. Separadas, dariam a impressão de serem independentes — e a
primeira consequência seria alguém esquecer o `expectedVersion` numa delas.

---

# Mensagens e realtime (MVP 1.5)

Agora a conversa tem conversa. Ainda **não** tem WhatsApp: o canal externo
é o MVP 1.7, e o motor foi desenhado para não saber disso.

## O modelo

`Message` aponta para a `Conversation` que já existia. Não há um segundo
modelo de conversa, e não há nome de provedor em lugar nenhum do modelo —
a Meta entrará como um adaptador gravando nestas mesmas tabelas.

| Direção | Quem |
|---|---|
| `INBOUND` | cliente → escritório. Identidade **externa**; nenhum Membership envolvido |
| `OUTBOUND` | escritório → cliente. Com o snapshot de quem escreveu |
| `SYSTEM` | declarado e **não usado** — evento já vive em `ConversationEvent` |

`MessageType` só produz `TEXT`. `IMAGE`, `FILE`, `AUDIO` e `VOICE` estão no
enum porque acrescentar valor depois é migration, e não há uma linha de
código morto atrás deles.

## Ordem: um contador, não o relógio

```sql
UPDATE conversations SET message_seq = message_seq + 1 … RETURNING
```

O UPDATE trava a linha da conversa, então dois envios simultâneos recebem
números diferentes. `SELECT MAX(sequence)+1` é a versão que parece certa e
perde a corrida: as duas transações leem o mesmo máximo antes de qualquer
uma gravar.

Na tela, a ordem das mensagens é a do `sequence` e nada mais. As notas
internas são encaixadas por horário entre elas.

## Idempotência

O navegador gera `clientMessageId` **antes** de enviar. Duplo clique,
retry e reconexão mandam o mesmo valor; quem decide é o índice único, não
um `SELECT` antes do `INSERT`. Quem perde a corrida lê a mensagem que já
existe e devolve ELA — de fora, o retry parece ter funcionado na primeira
tentativa.

Para a entrada vale o mesmo com `externalMessageId`: canal reentrega, e
reentrega não é mensagem nova.

## Assinatura congelada

Cada mensagem guarda `senderDisplayName` e `senderDepartmentName` de
**quando foi escrita**. Se a Aline mudar do Fiscal para o Contábil em
março, a mensagem de janeiro continua dizendo "Aline • Fiscal".

São dois campos, e não um texto pronto: quem monta `Nome • Área` é a
interface.

A assinatura aparece **sempre**, inclusive para quem escreveu. Nada de
"Você": num atendimento compartilhado, quem lê o histórico amanhã não é
quem digitou hoje.

## Leitura

Duas perguntas diferentes, duas tabelas:

| | Responde |
|---|---|
| `ConversationView` | quem abriu o atendimento |
| `MessageView` | quem leu **esta** mensagem |

**Nenhum GET marca leitura.** É rota própria, chamada quando valem três
coisas ao mesmo tempo: conversa aberta, janela visível e a mensagem dentro
da área visível. Renderizar não basta — prefetch e aba em segundo plano
zerariam o contador de quem não olhou nada.

É informação **interna**. Não é o "visto" do cliente e nunca sai para ele.
O status do transporte — `QUEUED · SENT · DELIVERED · READ · FAILED` — é
outro conceito, do outro lado da conversa, e chega com o canal.

## Não lidas

Por pessoa, calculado, nunca guardado: contador materializado diverge, e
número errado de não lidas é pior que nenhum.

> Tem mensagem do cliente que **eu** não visualizei. Atendimento sem
> nenhuma mensagem de entrada conta como não lido se eu nunca o abri.

Se a Aline leu e o Carlos não, some da lista dela e continua na dele.

## Status ao enviar e ao receber

```
Enviar     NEW              → IN_PROGRESS
           WAITING_OFFICE   → IN_PROGRESS
           WAITING_CUSTOMER → inalterado   cobrar não é ser respondido
           RESOLVED         → inalterado   reabrir é decisão de gente

Receber    WAITING_CUSTOMER → WAITING_OFFICE
           os demais        → inalterado
```

Mensagem em atendimento resolvido não reabre sozinha: sobe na lista e
aparece como não lida. `firstResponseAt` é gravado na primeira saída e não
muda mais.

## Realtime: SSE

Escolhido depois de olhar a pilha. Socket.IO exigiria servidor HTTP próprio
e o abandono de `next dev`/`next start`; o que a fase pede é fluxo servidor
→ cliente, e enviar já é um POST idempotente.

O que decide é segurança: **SSE é uma rota como as outras**, com o mesmo
`requireAuth`, o mesmo cookie e a mesma sessão revogável. Um servidor de
WebSocket ao lado teria o seu próprio caminho de autenticação — e a segunda
porta é sempre a que fica aberta. De quebra, o `EventSource` reconecta
sozinho.

Isolamento é estrutura: os inscritos vivem num `Map<tenantId, Set<…>>` e
`publish` entrega só àquela chave. Não existe "todos os inscritos" para
alguém varrer com um `if` errado. O tenant vem da sessão; o cliente só pode
**reduzir** o que recebe pedindo uma conversa — e essa conversa é conferida
contra o banco.

| Evento | Para quê |
|---|---|
| `message.created` | mensagem nova (só para quem assinou a conversa) |
| `message.viewed` | alguém do escritório leu |
| `conversation.updated` | status, responsável, nota, etiqueta — move a lista |

O evento é **aviso**, não transporte: quem tem a verdade é o banco. Se a
conexão cair, a próxima leitura conserta — por isso a reconexão dispara
revalidação em vez de tentar recuperar o que passou. A tela mostra
"Reconectando…" discretamente enquanto isso.

## Nota interna, agora que Message existe

Continua sendo tabela separada. Um `interna: boolean` deixaria a observação
do escritório a um `false` de distância do WhatsApp do cliente.

Na tela as duas convivem na linha do tempo — mas a nota não tem forma de
balão, tem faixa própria e diz por extenso que não será enviada. O campo de
escrita muda de cor junto.

## Editar e apagar

Não existem. Mensagem enviada é histórico. As colunas `editedAt` e
`deletedAt` existem para que a política de retenção não precise de
migration sobre dado vivo — e a política vem antes do botão.

## Teclado

`Enter` envia, `Shift+Enter` quebra linha. Mensagem nova não rouba o foco
de quem está escrevendo.

## Entrada de desenvolvimento

```bash
ELO_ALLOW_DEV_INBOUND=1 npm run dev:inbound -- <tenantId> <conversationId> "texto"
```

Enquanto não há WhatsApp, é assim que se faz uma mensagem chegar. Três
travas, as mesmas do seed: recusa `NODE_ENV=production`, exige
`ELO_ALLOW_DEV_INBOUND=1` e marca tudo com o canal `DEV`. A rota
`/api/dev/inbound` responde **404** quando desligada — e exige sessão
mesmo ligada.

## Rotas

| Rota | O que faz |
|---|---|
| `GET /api/conversations/:id/messages?antesDe=&limite=` | página de mensagens (as mais recentes primeiro; `antesDe` sobe no histórico) |
| `POST /api/conversations/:id/messages` | envia (idempotente por `clientMessageId`) |
| `POST /api/conversations/:id/messages/read` | marca como visualizadas por mim |
| `GET /api/realtime?conversation=` | fluxo SSE do tenant da sessão |
| `POST /api/dev/inbound` | só em desenvolvimento |

---

# Mídia: arquivos, imagens e voz (MVP 1.6)

Anexo pendura na `Message` que já existe. Não há um segundo caminho para
mídia, e o binário não entra no Postgres: a tabela guarda metadado e uma
chave, e os bytes ficam no storage.

## Tipos

| Tipo | O que é |
|---|---|
| `IMAGE` | jpg, jpeg, png, webp |
| `DOCUMENT` | pdf, doc, docx, xls, xlsx |
| `AUDIO` | mp3, m4a, aac, ogg, opus, wav — arquivo anexado |
| `VOICE` | gravado na hora pelo navegador |

`AUDIO` e `VOICE` são separados de propósito: não são a mesma coisa para
quem lê a conversa, aparecem diferente na tela, e o WhatsApp também os
distingue. Juntá-los agora seria ter de adivinhar depois, pelo mimetype.

## Limites

| Categoria | Padrão | Variável |
|---|---|---|
| Imagem | 10 MB | `ELO_UPLOAD_MAX_IMAGE_MB` |
| Documento | 25 MB | `ELO_UPLOAD_MAX_DOCUMENT_MB` |
| Áudio | 25 MB | `ELO_UPLOAD_MAX_AUDIO_MB` |
| Voz | 15 MB | `ELO_UPLOAD_MAX_VOICE_MB` |

Dimensionados para escritório de contabilidade. O canal externo terá os
SEUS limites, mais apertados — não são estes.

## Validação: três checagens

1. **Extensão** — a ÚLTIMA. `foto.jpg.exe` tem extensão `exe`.
2. **MIME declarado** — trivial de forjar, nunca decide sozinho.
3. **Magic bytes** — o que o arquivo é. Um `.png` que começa com `MZ` é um
   executável renomeado.

As três precisam concordar.

## Storage

```
tenants/<tenantId>/messages/<ano>/<mês>/<32 hex aleatórios>
```

O tenant no prefixo é a segunda camada de isolamento. O nome original é só
metadado — nunca a chave. O sufixo é aleatório: sequencial se enumera, e
hash do conteúdo faria dois clientes com o mesmo PDF dividirem caminho.

`StorageProvider` tem cinco operações: `put`, `get`, `getRange`, `delete`,
`metadata`, `signedUrl`. Em desenvolvimento, disco (`.storage/`, fora de
`public/`); em produção, S3 ou R2 — uma classe nova e uma variável.

`getRange` está no contrato porque áudio precisa de seek.

## Upload em dois estágios

```
POST /api/uploads          → valida, guarda, devolve um id (PENDING, 2 h)
POST …/messages            → a mensagem nasce e ADOTA o anexo
```

Banco e storage não compartilham transação. O desenho é para que a falha
deixe LIXO, nunca buraco: bytes primeiro, linha `PENDING` depois, adoção
na mesma transação da mensagem, e faxina do que sobrar.

O retry reenvia o MESMO `uploadId` e o MESMO `clientMessageId`: nem o
arquivo sobe duas vezes, nem a mensagem duplica.

```bash
npm run storage:limpar -- <tenantId>
```

A faxina precisa do tenant: `message_attachments` tem RLS forçado, e sem
contexto a consulta não vê linha nenhuma. Um papel com `BYPASSRLS`
resolveria e abriria para sempre a porta que o MVP 1.0 fechou.

## Download

Não existe URL pública. Tudo passa por `/api/attachments/:id` — sessão,
permissão, RLS. A `storageKey` nunca sai.

- Anexo de outro tenant: **404**, não 403 (403 confirmaria que existe).
- `inline` só para imagem e áudio; documento vai como `attachment`.
- `X-Content-Type-Options: nosniff` sempre.
- `Range` atendido com `206` — é o que faz o seek existir.
- Nada de OCR, conversão de Office ou macro: documento é servido, nunca
  interpretado.

## Áudio e voz

Player próprio: play/pause, barra com seek, tempo, e velocidade **1x /
1.5x / 2x** — ouvir recado em 1,5× é o que mais economiza tempo num
escritório.

O gravador escolhe o formato com `MediaRecorder.isTypeSupported`:
Chrome/Edge dão `audio/webm;codecs=opus`, o Safari dá `audio/mp4`. Nenhum
navegador grava MP3, e não há transcodificação no servidor. Dá para ouvir
antes de enviar, cancelar, e o microfone é solto em todos os caminhos.

Permissão negada mostra *"Não foi possível acessar o microfone. Verifique
a permissão do navegador."* e a conversa continua funcionando.

## Desempenho

Histórico traz metadado. Imagem é `loading="lazy"`; áudio é
`preload="none"` e mostra "0:37" a partir da duração gravada, sem baixar
nada. Os bytes só são buscados quando alguém abre ou toca.

## Antivírus — dívida registrada

`scanStatus` nasce `NOT_SCANNED`, não `CLEAN`: dizer "limpo" sobre o que
ninguém examinou seria mentira gravada no banco. O download recusa
`BLOCKED`. **Ligar um scanner é obrigatório antes de abrir a produção.**

## Rotas

| Rota | O que faz |
|---|---|
| `POST /api/uploads` | multipart; valida e guarda; devolve o id do anexo |
| `GET /api/attachments/:id` | os bytes, autenticado, com `Range` |

---

# PWA, notificações e Web Push (MVP 1.7)

O problema desta fase, na frase do pedido: **um funcionário precisa ser
avisado quando um cliente entra em contato, mesmo que não esteja olhando
para o Elo.**

O SSE do MVP 1.5 resolve metade disso — ele atualiza a tela de quem está
com a página aberta. A outra metade é o Web Push, que funciona com o
navegador fechado.

## As duas metades, e onde uma termina

```
Message criada
     │
     ├─► SSE ──────────► quem está com o Elo aberto (tela, crachá, som)
     │
     └─► decisão de notificação
              │
              └─► Web Push ─► quem NÃO está olhando (Windows, celular)
```

A fronteira entre as duas é a **presença**, e a regra está escrita:

| Estado da pessoa | O que acontece |
|---|---|
| conversa aberta + janela visível | só o SSE. Nem push, nem som |
| Elo aberto, em outra conversa | som (se ligado) e crachá. Sem push |
| Elo aberto, janela escondida | push |
| Elo fechado | push |

O limite honesto: a presença sabe o que a aba CONTOU. Uma aba que morre sem
avisar fica registrada até a conexão SSE cair — alguns segundos —, e nesse
intervalo o push pode ser suprimido para alguém que já saiu. A escolha é
errar para o lado de um aviso a menos numa janela de segundos, em vez do
aviso duplicado o tempo todo. A lista, o crachá e o contador de não lidas
continuam certos; o que se perde é o alerta imediato.

## Privacidade: a prévia vem desligada

A notificação aparece na **tela bloqueada** do Windows e do celular, e o
escritório trata de assunto de terceiro. Por padrão o aviso diz QUEM falou,
nunca O QUE falou:

```
ELO
Empresa ABC entrou em contato.
```

Com a prévia ligada — escolha explícita de cada pessoa, em Configurações —
entra um trecho de até 120 caracteres. E mídia vira DESCRIÇÃO mesmo assim:

```
Empresa ABC enviou uma foto.
Empresa ABC enviou um documento.
Empresa ABC enviou uma mensagem de voz.
```

O nome do arquivo fica de fora inclusive com a prévia ligada:
`Balancete_MARIA_SILVA_2026.pdf` conta a mesma história que a prévia existe
para esconder.

## Quem é notificado

Notificar todo mundo de tudo é o caminho mais rápido para ninguém ler nada.

| Evento | Quem recebe |
|---|---|
| mensagem em atendimento **assumido** | o responsável, e só ele |
| mensagem em atendimento **sem dono** | a fila elegível |
| atendimento **novo** sem responsável | a fila elegível |
| **atribuição** / **transferência** | quem recebeu |

**Fila elegível** = vínculo ativo · pessoa ativa · `availableForAssignment`
· permissão `conversations.read` · e, quando o atendimento tem ÁREA,
pertencer àquela área. Sem área, é o escritório (o "Atendimento Geral").

`visibleToCustomers` NÃO entra: aparecer na lista que o cliente vê é outra
coisa, e quem está fora dela continua trabalhando.

**Quem fez a ação nunca é avisado dela.** A Aline manda a mensagem e a
Aline não recebe "nova mensagem no ELO".

**Membership desativado para de receber**, sem ninguém fazer nada. É este o
mecanismo do lado do servidor: sair do Elo não apaga a inscrição do
navegador — perder o vínculo corta a entrega na origem.

## Preferências, por pessoa

Uma linha por membership, criada só quando alguém muda algo. Quem nunca
abriu a tela acompanha o padrão para sempre — e mudar o padrão alcança
todos eles.

```
[✓] Novas mensagens
[✓] Novos atendimentos
[✓] Atendimento atribuído a mim
[✓] Transferência para mim
[✓] Notificações do sistema
[✓] Som quando o ELO estiver aberto
[ ] Mostrar prévia da mensagem     ← o único desligado por padrão
```

Não há configuração de tenant para isso: quem escuta o som e lê a prévia é
a pessoa. O escritório decide QUEM recebe; a pessoa decide COMO.

## Agrupamento

Vinte mensagens seguidas não viram vinte notificações. Duas camadas:

1. **`tag` / `Topic`** — o sistema operacional substitui o aviso anterior
   daquele atendimento;
2. **janela de 30 s no servidor** — resolve vibração, som e bateria, que a
   substituição não resolve.

O primeiro da rajada sai na hora; os seguintes ficam contados; o próximo
depois da janela leva o total ("Empresa ABC — 5 novas mensagens"). Ler a
conversa zera a contagem, senão o aviso seguinte incluiria o que já foi
lido.

**O custo:** se a rajada parar dentro da janela, as últimas mensagens não
geram um segundo aviso do sistema. A pessoa já foi avisada pelo primeiro.

## Crachá de não lidos

O número na navegação, ao lado de "Atendimentos", e — onde o sistema
suporta — no ícone do aplicativo instalado (`setAppBadge`).

É **pessoal e calculado**: usa exatamente o `whereNaoLidos` do filtro "Não
lidos". A Aline e o Carlos, no mesmo atendimento, têm números diferentes.

Nunca é decrementado no cliente. Qualquer evento de realtime faz a tela
perguntar de novo ao servidor — decrementar localmente diverge na segunda
aba, e um contador em que a equipe não confia é pior que nenhum.

## Som

Duas notas curtas, sintetizadas com `AudioContext` — sem arquivo de áudio
para baixar, versionar e cachear. Nunca toca duas vezes em menos de um
segundo: cinco mensagens juntas fariam cinco bipes sobrepostos, que é como
se ensina uma equipe a desligar o som.

Não toca quando: a mensagem é minha, a conversa está aberta na frente, a
janela está escondida (aí quem avisa é o push) ou a preferência está
desligada.

O horário silencioso silencia **só o som dentro do Elo**. O silêncio das
notificações do sistema é do Windows e do celular; duplicar esse controle
aqui daria dois lugares para desligar a mesma coisa.

## Service worker: o que ele guarda

**Nada de `/api`.** Um worker que cacheia resposta autenticada deixa
conversa, cliente e anexo em disco, fora do banco, sem RLS e sem
expiração — numa máquina compartilhada, à disposição da pessoa seguinte.

O que ele guarda são cinco arquivos:

```
/offline
/manifest.webmanifest
/icones/icone-192.png
/icones/icone-512.png
/icones/badge-96.png
```

Navegação é **rede primeiro, sempre**. Sem conexão aparece a tela que diz
que está sem conexão — nunca uma cópia de ontem apresentada como atual.

## Atualização da PWA

`VERSAO` no `sw.js` muda a cada alteração. O navegador instala o novo em
segundo plano e a aba mostra:

```
Uma nova versão do ELO está disponível.   [Atualizar]
```

Nada de `skipWaiting()` automático: trocar o worker debaixo de quem está
digitando a resposta de um cliente recarrega a página e perde o texto.

## Chaves VAPID

```bash
npm run vapid
```

Cola as três linhas no `.env`. A **pública** pode chegar ao navegador (é o
`applicationServerKey`), e ainda assim sai por rota autenticada, nunca por
variável `NEXT_PUBLIC_`. A **privada** nunca sai do servidor: quem a tem
assina avisos em nome do Elo para qualquer aparelho inscrito.

> **Trocar o par invalida TODAS as inscrições.** O navegador amarra a
> inscrição à chave pública com que ela foi criada. Não há rotação suave
> aqui, ao contrário do token de troca. Gere uma vez e guarde.

Sem chaves configuradas, o push simplesmente não acontece — nada quebra, e
a tela de Configurações explica o motivo.

## Ícones

```bash
npm run icones
```

Gera os quatro PNGs a partir do mesmo desenho do `EloLogo`, sem dependência
de imagem (PNG é simples e o `zlib` já vem no Node). Um PNG exportado à mão
envelhece: alguém ajusta o SVG e o ícone instalado continua sendo o desenho
de três meses atrás.

O `icone-maskable-512` tem o símbolo menor, dentro da zona segura, porque o
Android RECORTA o ícone na forma do sistema. O `badge-96` é branco sobre
transparente: o Android o pinta, e um ícone colorido vira uma mancha preta.

## Limitações por navegador — o que é real

| | Instalar | Notificação com app fechado | Crachá no ícone |
|---|---|---|---|
| **Chrome/Edge no Windows** | ✅ | ✅ | ✅ `setAppBadge` |
| **Chrome no Android** | ✅ | ✅ | ✅ |
| **Firefox** | parcial | ✅ | ❌ |
| **Safari / iOS** | só "Adicionar à Tela de Início" | ⚠️ ver abaixo | ❌ |

**iOS — sem promessa de paridade.** A partir do iOS 16.4 há Web Push, e ele
funciona; o que muda é o caminho até lá:

- a PWA **precisa ser adicionada à Tela de Início**. No Safari comum, sem
  instalar, não há push nenhum;
- a permissão só pode ser pedida de dentro do aplicativo instalado, a
  partir de um gesto;
- não há `setAppBadge` — o crachá do Elo, dentro da tela, continua
  funcionando;
- o iOS descarta o service worker de PWAs pouco usadas, e a inscrição pode
  precisar ser refeita.

**Nada disso foi testado em hardware iOS.** Está aqui como o que a
documentação da plataforma descreve, e não como algo verificado.

**Windows:** confira também o Foco/Assistente de foco. Ele silencia
notificações sem que o navegador saiba, e é a explicação mais comum para
"ativei e não recebo".

## Contexto seguro: por que `localhost` funciona e o IP não

Service worker e Web Push exigem contexto seguro. `localhost` conta; um
`http://192.168.1.8:3000` **não** conta — o navegador nem registra o
worker.

Ou seja: as outras máquinas do escritório só terão notificação quando o Elo
estiver atrás de HTTPS com certificado válido, o que chega junto com o
domínio do MVP 1.8.

## Como validar em hardware — o roteiro

O que falta desta fase, e precisa de uma pessoa num navegador de verdade.
O navegador automatizado **nega a permissão por padrão** e **não tem
serviço de push** (`Registration failed - push service not available`), por
isso os passos abaixo não foram exercitados aqui.

```bash
npm run vapid            # se ainda não houver chaves no .env
npm run build && npm start
```

1. Abra o Elo no Chrome ou no Edge, entrando pelo Fiscale.
2. **Instalar:** o ícone de instalação aparece na barra de endereço. A
   janela deve abrir sem barra de navegação, com o ícone do Elo.
3. **Configurações → Notificações → Ativar notificações.** Conceda no
   diálogo do navegador. O estado deve virar "Ativas".
4. **Feche o Elo por completo** — a janela e todas as abas.
5. Gere uma mensagem de entrada:
   ```bash
   npm run dev:inbound -- <conversationId> "Bom dia, preciso da guia"
   ```
6. **A notificação do Windows deve aparecer** com o Elo fechado, dizendo
   "Empresa ABC entrou em contato." (sem o texto, porque a prévia vem
   desligada).
7. **Clique nela.** O Elo abre no atendimento certo.
8. Repita com a prévia LIGADA e confira que o trecho aparece.
9. Repita com o Elo ABERTO na conversa: **não deve** aparecer notificação
   do sistema — só o balão e o crachá.

Se algum passo falhar, o primeiro lugar a olhar é Configurações →
Notificações: ela distingue "não configurada", "bloqueada pelo navegador" e
"sem chaves no servidor", que são três problemas diferentes com três
soluções diferentes.

## Rotas

| Método | Rota | Para quê |
|---|---|---|
| GET | `/api/notifications` | preferências, meus aparelhos e a chave pública VAPID |
| PATCH | `/api/notifications/preferences` | salva as MINHAS preferências |
| POST | `/api/notifications/subscription` | inscreve ESTE dispositivo |
| DELETE | `/api/notifications/subscription` | desativa ESTE dispositivo |
| GET | `/api/notifications/badge` | meu contador de não lidos |
| POST | `/api/presence` | "esta aba foi para o fundo" / "voltou" |
| GET | `/api/auth/destino?para=` | guarda o destino antes do login |

Nenhuma delas devolve o endpoint de push nem as chaves do navegador: o que
sai é o `endpointHash`. O endpoint é um segredo de capacidade — quem o tem
entrega notificação naquele aparelho.
