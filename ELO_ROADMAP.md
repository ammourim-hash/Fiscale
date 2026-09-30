# Elo — roadmap e situação

Atualizado em **10/08/2026**.
Base congelada: [ELO_AUDITORIA.md](ELO_AUDITORIA.md) · [ELO_DECISOES.md](ELO_DECISOES.md)
Documentação da fundação: [elo/README.md](elo/README.md)

---

## ✅ MVP 1.0 — Fundação: CONCLUÍDO em 09/08/2026

O bloqueador de 06/08 (sem Node, sem Docker) foi resolvido por você. Ambiente
efetivamente usado:

```
Node.js  24.19.0      npm 11.17.0
Docker   29.6.2       Compose v5.3.1
Postgres 16.10-alpine (container, porta 5433)
```

Nada foi executado no PostgreSQL 9.6 da máquina, que continua na 5432,
intocado. **Nenhum arquivo do Fiscale foi alterado.**

### O que ficou pronto

| Item | Situação |
|---|---|
| Projeto Next.js 16 + React 19 + TypeScript 5.9 strict | ✅ |
| Docker Compose com Postgres 16, volume e health check | ✅ |
| Papéis separados: `elo_owner` (migrations) e `elo_app` (runtime) | ✅ |
| Prisma 7.9.1 com driver adapter e URLs separadas | ✅ |
| Schema mínimo: Tenant, User, Contact | ✅ |
| RLS + `FORCE` + políticas com `USING` e `WITH CHECK` | ✅ |
| Contexto de tenant transacional (`withTenant`) | ✅ |
| Health check `/api/health` (200 ok · 503 degraded) | ✅ |
| Log estruturado JSON com redação de segredos | ✅ |
| Tratamento de erros sem stack trace na resposta | ✅ |
| Validação de ambiente com zod | ✅ |
| 49 testes — cross-tenant, RLS em SQL cru, pool, guarda estrutural | ✅ |
| lint · typecheck · build | ✅ |

### Escopo deliberadamente menor que o previsto

- **Schema com 3 entidades, não 14.** As 14 de ELO_DECISOES seção D entram
  quando cada uma tiver tela e teste — MVP 1.1 a 1.5. Fundação prova o
  mecanismo; não adianta criar tabela que ninguém lê.
- **Sem Redis.** Está decidido para sessão, presença e Socket.IO, mas nada no
  MVP 1.0 o usa. Serviço que ninguém consome mascara dependência. Entra no 1.1.

### Verificação executada de verdade

Banco destruído (`db:reset`) e refeito do zero, migrations reaplicadas com
`prisma migrate deploy`, suíte inteira reexecutada: 49/49. `npm run verify`
(typecheck → lint → test → build) sai com código 0. Health check conferido no
servidor de produção, inclusive o caminho degradado com o banco parado.

Além disso, uma **verificação por mutação**: trocando `set_config(..., true)`
por `false` em `withTenant`, o teste de pool falha e mostra dado do tenant A
vazando para fora do contexto. A suíte detecta o problema real, não apenas a
si mesma.

### Débitos técnicos registrados

1. ~~Não há teste de carga concorrente entre tenants (só sequencial).~~
   Parcialmente resolvido no 1.1: o teste de `jti` usa duas conexões em
   paralelo real. Carga entre tenants continua sequencial.
2. ~~`provisionTenant` não tem rota nem autorização.~~ **Pago no 1.1**:
   virou CLI atrás de `ELO_ADMIN_TOOL=1`. Ver S10.
3. O `requestId` é gerado por rota; ainda não há middleware que o propague
   automaticamente. **Continua aberto.**
4. `prisma/legacy/` guarda os dois arquivos anteriores ao scaffold como
   referência; nenhum roda.

---

## ✅ MVP 1.1 — Identidade e sessão: CONCLUÍDO em 09/08/2026

### O que ficou pronto

| Item | Situação |
|---|---|
| Modelagem Identity + Membership (multi-tenant desde já) | ✅ |
| IdentityProvider (ligação com o login do Fiscale) | ✅ |
| Sessão do Elo persistente e revogável, com hash do token | ✅ |
| Encerrar todas as outras sessões | ✅ backend + rota |
| RBAC central por permissão (5 papéis, 8 permissões) | ✅ |
| Departamentos por tenant, com pertencimento múltiplo | ✅ |
| Token de troca Ed25519, 90 s, uso único, com `kid` | ✅ |
| Anti-replay atômico em Postgres | ✅ |
| Rotas `/api/auth/{exchange,me,logout,sessions}` e `/api/departments` | ✅ |
| Auditoria dos 5 eventos exigidos | ✅ |
| RLS de todas as tabelas novas, inclusive as globais | ✅ |
| Sessões do Fiscale em SQLite (sobrevivem ao reinício) | ✅ |
| Botão "🔗 Elo" no Fiscale | ✅ |
| Provisionamento como ferramenta, não rota | ✅ |

### Alterações no Fiscale

Duas novas e cinco pontuais, todas de autenticação:

| Arquivo | O quê |
|---|---|
| `fiscale_sessoes.py` | **novo** — sessões em SQLite, guarda hash do token |
| `fiscale_elo.py` | **novo** — chave Ed25519 e emissão do token de troca |
| `fiscale_server.py` | `SESSOES` passa a ser o store; `_usuario_sessao`; cookie com `Max-Age`; login/logout/primeiro-acesso; exclusão de usuário; rota `POST /api/elo/abrir` |
| `web/home_portal.html` | botão "🔗 Elo" e a função que envia o token por formulário POST |
| `teste_fiscale_sessoes.py` | **novo** — 33 verificações, sem rede |

Backup em `backup_pre_elo_mvp11_20260809/` com os hashes de antes.
**Nenhum módulo fiscal foi tocado**: `core.py`, `classificador.py`,
`apuracao_federal.py`, `auditor_nfe.py`, `prefeituras.py`, `recife.py`,
`vencimentos.py`, `seguranca.py`, certificados e XMLs estão intactos.

### Verificação executada

112 testes no Elo (8 arquivos) e 33 no Fiscale. `npm run verify` sai com 0.
O fluxo completo foi exercitado por HTTP real, com o Fiscale numa instância
isolada na porta 8899 (a do usuário, 8777, não foi tocada):

login → reinício do servidor → **sessão sobreviveu** → "Abrir ELO" →
exchange 303 com cookie → `/api/auth/me` → `/api/departments` →
**replay do mesmo token = 401** → logout → cookie antigo = 401 → cookie
com tenant trocado = 401 → sem cookie = 401.

### Débitos técnicos novos

1. Não há tela do Elo além da página de fundação — o MVP 1.3 é quem faz a
   interface. Hoje a prova é por API.
2. Derrubar a sessão de OUTRA pessoa não tem rota (`sessions.manage`
   existe, o endpoint não).
3. Rotação de chave é manual: editar o `.env` e reiniciar. Sem JWKS, o que
   é adequado para uma origem só.
4. `elo_config.json` é editado à mão no Fiscale. Só existe um escritório;
   vira tela quando houver dois.
5. O `tid` do token vem de `elo_config.json`, ou seja, um Fiscale atende um
   tenant. Suficiente hoje.

---

## ✅ MVP 1.2 — Projeção de clientes: CONCLUÍDO em 09/08/2026

### O que ficou pronto

| Item | Situação |
|---|---|
| `ExternalCustomerReference` + `ExternalCustomerPhone`, tenant-owned | ✅ |
| RLS, FORCE, WITH CHECK e rls-guard nas tabelas novas | ✅ |
| Autenticação de máquina por assinatura Ed25519 (chave própria) | ✅ |
| Replay: timestamp ±300 s + nonce atômico em Postgres | ✅ |
| Normalização de telefone conservadora (E.164) | ✅ |
| `findCustomerByPhone` com EXACT / NONE / **AMBIGUOUS** | ✅ |
| Busca por nome, documento e telefone | ✅ |
| Idempotência por `contentHash` calculado pelo Elo | ✅ |
| Full sync com `missingSince` (ausente ≠ inativado) | ✅ |
| Fila local em SQLite no Fiscale, com backoff e retry manual | ✅ |
| Gatilho incremental: só enfileira quem realmente mudou | ✅ |
| Auditoria dos 6 eventos e contadores em `sync_runs` | ✅ |
| Diagnóstico por endpoint nos dois lados | ✅ |

### Duas decisões que vieram dos dados reais, não da lista

Inspecionei o `state_clientes.json` antes de fechar o schema, como pedido:

1. **`nome fantasia` não existe no cadastro do Fiscale.** Estava na lista de
   campos mínimos; criar coluna que nunca teria valor seria peso morto.
   Fica de fora, e acrescentar depois é migration aditiva de uma linha.
2. **`tel` é UM campo de texto livre, sem validação** — e todos os 14
   clientes estão com ele vazio. Campo livre assim recebe "(81) 99999-1111
   / 3333-4444" na vida real. Por isso a tabela separada de telefones: com
   um campo só, casar a mensagem do WhatsApp obrigaria a escolher um dos
   números em silêncio.

Também ficaram de fora por minimização: `regime`, `ie`, `im`, `mun`, `uf`,
`cert` e `certValidade`.

### Verificação executada

187 testes no Elo (11 arquivos) e 66 no Fiscale (2 arquivos). `npm run
verify` sai com 0.

Fluxo ponta a ponta com os **22 passos** pedidos, por HTTP real, com o
Fiscale numa instância isolada na 8899: sincroniza → projeta → tenant certo
acha → tenant errado não acha (nome, documento e telefone) → telefone
normalizado acha → altera → atualiza → reenvia igual → nenhuma escrita →
**desliga o Elo** → altera cliente → Fiscale continua → item pendente →
religa → retry sincroniza → inativa → registro preservado com `active=false`.
**22/22.**

### Débitos técnicos novos

1. Sem tela: a projeção é consultada por API. Interface é o MVP 1.3.
2. O gatilho depende de `POST /api/state/clientes`. Alteração feita
   editando o JSON à mão não dispara nada — um `POST /api/elo/sync` resolve.
3. O `active` do cliente ainda não existe no cadastro do Fiscale; a
   projeção lê `ativo` se aparecer e assume ativo caso contrário.
4. Um Fiscale atende um tenant (herdado do 1.1).
5. A faxina de nonces é oportunista, sem tarefa agendada.

---

## ✅ MVP 1.3 — Interface: CONCLUÍDO em 09/08/2026

### Telas

| Tela | O que traz |
|---|---|
| **Início** | saudação, 3 cartões (atendimentos novos, aguardando, clientes ativos), rodapé com a última sincronização e a assinatura em uma linha |
| **Atendimentos** | duas colunas; filtros **Novos · Não lidos · Meus · Aguardando · Todos**; estado vazio honesto |
| **Clientes** | projeção real, busca por nome/CNPJ/telefone, status em ponto discreto |
| **Perfil do contato** | gaveta com documento, telefones, e-mail, origem e sincronização. Fecha no ✕, no fundo ou no Esc |
| **Configurações** | Meu perfil · Área principal · Outras áreas · Aparência · Sessão |
| **Entrar** | sem sessão, explica que a porta é o Fiscale |

### Identidade visual

Petróleo `#10444E` e ciano `#7FD1DE` do Fiscale, azul `#1668B3` como cor de
ação. Sem verde — o Elo não é o WhatsApp. Modo escuro com paleta própria
(azul-marinho `#0B1620`), não o claro invertido.

### Duas correções que só apareceram clicando

1. **O botão "Abrir ELO" caía em 405.** `target="_blank"` com POST degradava
   para GET quando a janela era bloqueada. Trocado por janela nomeada aberta
   dentro do clique, com fallback para a própria aba.
2. **O login voltava para a tela de entrada.** O redirect da troca era
   montado com `new URL("/", req.url)`, que usa o host de configuração — o
   cookie era gravado em `127.0.0.1` e a pessoa ia para `localhost`. Para o
   navegador são sites diferentes. Agora o destino vem do `Host` que o
   cliente usou (`src/server/http/same-host.ts`), o que também conserta o
   caso de produção atrás do proxy reverso.

Uma terceira, menor: a lista de clientes abria vazia sem termo de busca.

### Ajustes pedidos na revisão visual

Permissões técnicas fora da tela de quem atende (para admin, recolhidas e em
português) · Configurações em cinco seções · textos "Clientes sincronizados
com o FISCALE" e "Última sincronização com o FISCALE" · status em ponto
discreto · filtros de atendimento · home enxuta · assinatura em uma linha ·
navegação sem itens "em breve" · nome único **Atendimentos**.

### Verificação

245 testes no Elo (13 arquivos, sendo 53 de interface em jsdom) e 66 no
Fiscale. `npm run verify` sai com 0. Fluxo Fiscale → Elo reexercitado por
navegador real. Larguras 1280 / 768 / 375 sem rolagem horizontal e sem
elemento estourando.

### Débitos técnicos novos

1. Sem screenshot automatizado: a verificação visual é por leitura de DOM e
   medição de geometria. Regressão puramente estética não é detectada.
2. `TOTAL_ATENDIMENTOS` é uma constante 0 em `atendimentos/page.tsx` até
   `Conversation` existir (MVP 1.5) — uma linha vira a consulta real.
3. Avatar é inicial sobre cor derivada do nome; foto de verdade depende de
   storage (MVP 1.6).
4. A busca do topo leva para Clientes. Busca em mensagens não existe porque
   mensagens não existem.
5. Ao atravessar o ponto de quebra redimensionando, a barra lateral faz uma
   animação de saída visível. Só acontece redimensionando.

---

## ✅ MVP 1.4 — Modelo de atendimento: CONCLUÍDO em 10/08/2026

### Schema

| Tabela | Papel |
|---|---|
| `conversations` | o atendimento: cliente, canal, status, responsável, área, os quatro marcos de tempo e `version` |
| `conversation_views` | quem abriu, quando pela primeira e pela última vez, quantas |
| `conversation_events` | a linha do tempo única — de quem para quem, de qual status para qual |
| `tags` / `conversation_tags` | etiquetas por tenant |
| `internal_notes` | nota interna, tabela separada de mensagem |

Mais dois campos em `memberships`: `visibleToCustomers` (padrão **false**) e
`availableForAssignment` (padrão true).

Todas tenant-owned, todas com RLS + FORCE + WITH CHECK pela
`elo_apply_rls()`, todas cobertas pelo `rls-guard`.

### O teste que carrega a fase

Dois cliques em "Assumir" no mesmo segundo. A condição vive dentro do
UPDATE (`AND assigned_membership_id IS NULL`), então um vence e o outro lê:

> Este atendimento acabou de ser assumido por Aline • Fiscal.

**Verificado por mutação:** removida a condição, cinco tentativas
simultâneas "assumem" as cinco e o teste falha. Restaurada, 200 e 409 —
confirmado também por HTTP real no navegador.

Transferência e status usam trava otimista por `version`: tela velha
recebe 409 em vez de sobrescrever a decisão de um colega em silêncio.

### Verificação

285 testes no Elo (14 arquivos; 39 novos só de atendimento) e 66 no
Fiscale. `npm run verify` sai com 0.

Na tela, com dados reais do seed de desenvolvimento: assumir, transferir,
devolver à fila, mudar status, resolver, aplicar etiqueta e registrar nota
— com o histórico contando tudo em português ("Aline assumiu", "Aline
mudou o status de Em atendimento para Resolvido").

Larguras 1280 / 768 / 375 sem rolagem horizontal de página. Em 375 a faixa
de filtros rola dentro de si (479px de conteúdo em 375 de tela), que é o
comportamento pretendido.

### Débitos técnicos novos

1. A área de mensagens continua vazia — `Message` é o MVP 1.5. Nenhum
   balão falso foi criado.
2. Não há paginação na lista: teto de 200 por consulta. Suficiente para o
   volume de um escritório; vira cursor quando não for.
3. `firstResponseAt` existe no schema e ainda não é preenchido — quem o
   preenche é o envio de mensagem, na próxima fase.
4. Transferir com motivo tem o campo no serviço, mas a tela ainda não pede
   o texto.
5. Sem tela de administração de etiquetas: criam-se por API ou pelo seed.
6. Notificações ficaram registradas como decisão, não implementadas.

---

---

## ✅ MVP 1.5 — Motor de mensagens e realtime: CONCLUÍDO em 10/08/2026

### Schema

| Tabela | Papel |
|---|---|
| `messages` | a mensagem: direção, tipo, conteúdo, `sequence`, snapshot de quem escreveu, identidade externa de quem enviou, `clientMessageId` e o estado de transporte |
| `message_views` | quem, do escritório, visualizou cada mensagem |

Mais um campo em `conversations`: `message_seq`, o contador de onde sai a
ordem. Três enums novos: `MessageDirection`, `MessageType` (só `TEXT` é
produzido) e `MessageDeliveryStatus` (do transporte futuro).

As duas tabelas com RLS + FORCE + WITH CHECK pela `elo_apply_rls()`, e
cobertas pelo `rls-guard`.

### Os testes que carregam a fase

**Ordenação sob concorrência.** `sequence` sai de um `UPDATE … RETURNING`
que trava a linha da conversa. Dez envios simultâneos, dez números
distintos, sem buraco.

A mutação aqui ensinou mais do que o teste: trocando por
`SELECT MAX(sequence)+1`, o teste continuou passando — porque o pool dos
testes tem UMA conexão e uma conexão serializa transações. Um teste de
concorrência sem concorrência dá confiança sem dar garantia. Daí
`tests/messages-concurrency.test.ts`, com pool próprio e um teste que
confere o paralelismo antes de medir o resto.

**Idempotência.** Cinco tentativas simultâneas do mesmo `clientMessageId`
viram uma mensagem só. Confirmado também por HTTP real: dois POSTs, 200 nos
dois, o mesmo `id`, `duplicada: true` no segundo, um balão na tela.

**Isolamento do realtime.** Testado no barramento e por HTTP contra a rota
SSE com sessão real. Com o guard de tenant removido, os dois falham.

**Snapshot.** Trocando o campo gravado por uma consulta ao departamento
atual, o teste falha com `expected 'Contábil' to be 'Fiscal'`.

### Verificação

349 testes no Elo (17 arquivos; 64 novos entre mensagens, concorrência e
realtime) e 66 no Fiscale. `npm run verify` sai com 0.

No navegador, com dois funcionários e duas abas: mensagem de entrada
aparecendo sem refresh nas duas abas, resposta assinada
"Aline Exemplo • Fiscal", status indo para Em atendimento, `firstResponseAt`
gravado uma vez, "Visualizada por Aline • Fiscal" chegando em tempo real,
não lidas em 0 para a Aline e 1 para o Carlos no mesmo atendimento,
paginação de 63 mensagens sem duplicata nem buraco, "Reconectando…" ao
derrubar o servidor e revalidação sozinha ao voltar.

Em 375px, abrir uma conversa esconde a lista e mostra o botão de voltar;
sem rolagem horizontal de página.

### Débitos técnicos novos

1. **Barramento em processo.** Uma instância. Com duas, entra um adaptador
   (Redis) dentro de `bus.ts`; nada mais no projeto muda.
2. **Uma conexão SSE por aba.** Suficiente para quinze pessoas; vira
   assunto quando não for.
3. **Sem screenshot na validação desta fase.** O painel do navegador não
   estava sendo exibido nesta sessão, então a página não compunha quadros:
   a conferência visual foi por leitura de DOM, geometria e resolução de
   CSS por elemento-sonda. Regressão puramente estética não é detectada.
4. **Editar e apagar não existem.** As colunas existem; a política de
   retenção vem antes do botão.
5. **Busca em mensagens ficou de fora** — a fase pedia mensagem, leitura e
   realtime, e busca textual entraria como escopo novo. Fica para 1.5.1/1.6.
6. **Sem som e sem notificação fora da aba.** A arquitetura está registrada
   desde o MVP 1.4; os eventos de realtime já foram desenhados para
   alimentá-la.
7. **`MessageType` só produz `TEXT`.** Imagem, arquivo e áudio dependem de
   storage (MVP 1.6).

---

## ✅ MVP 1.6 — Mídia e storage: CONCLUÍDO em 10/08/2026

### Schema

| Tabela | Papel |
|---|---|
| `message_attachments` | metadado do arquivo: tipo, provedor, chave, nome original, MIME, tamanho, dimensão, duração, SHA-256, status e prazo |

`MessageType` ganhou `DOCUMENT` no lugar de `FILE` (por `ALTER TYPE ...
RENAME VALUE`, escrito à mão para não derrubar o enum). Dois enums novos:
`AttachmentStatus` (PENDING/ATTACHED) e `ScanStatus`
(NOT_SCANNED/CLEAN/BLOCKED). RLS + FORCE + WITH CHECK, coberto pelo
`rls-guard`.

Os bytes **não** vão para o Postgres: ficam no storage, atrás de
`StorageProvider`.

### Storage

Disco em desenvolvimento (`.storage/`, fora de `public/`), atrás da
interface que o S3 vai implementar: `put`, `get`, `getRange`, `delete`,
`metadata`, `signedUrl`. A chave é
`tenants/<tenantId>/messages/<ano>/<mês>/<32 hex>` — com o tenant no
prefixo e sem o nome do arquivo.

### Os testes que carregam a fase

**Conteúdo mentindo.** Extensão certa, MIME certo, bytes de executável: o
upload é recusado por magic bytes. Confirmado também por HTTP contra o
servidor rodando — HTTP 400, "O conteúdo do arquivo não corresponde à
extensão".

**Isolamento.** Anexo do tenant A é 404 para a sessão do tenant B, e a
`storageKey` não aparece em resposta nenhuma.

**Range.** `bytes=1000-1999` devolve 206 com `Content-Range: bytes
1000-1999/24044` e exatamente 1000 bytes. É o que faz o seek do áudio
existir.

**Faxina.** Upload que nunca virou mensagem é apagado — linha e arquivo —
depois do prazo; anexo adotado por uma mensagem não é tocado.

### Verificação

425 testes no Elo (20 arquivos; 76 novos entre mídia, HTTP e interface) e
66 no Fiscale. `npm run verify` sai com 0.

No navegador, contra o servidor rodando: imagem, PDF e WAV de 3 s enviados
por HTTP real; PDF baixando como `attachment` com `nosniff`, imagem
`inline`; áudio TOCANDO de verdade (posição avançando, duração real 3 s),
seek para 2 s, e velocidade 1x → 1.5x → 2x chegando ao elemento; imagem
como miniatura preguiçosa; documento como cartão com nome, tipo e tamanho;
mídia de entrada aparecendo na segunda aba em tempo real. Em 375px, nada
estoura e não há rolagem horizontal.

### Débitos técnicos novos

1. **Antivírus não existe.** O gancho existe (`scanStatus`, e o download
   recusa `BLOCKED`), o scanner não. **Dívida obrigatória antes de abrir a
   produção** — escritório recebe arquivo de desconhecido todo dia.
2. **Sem thumbnails.** A miniatura é a imagem original limitada por CSS: o
   custo é de rede. Derivativos entram como anexo derivado, sem
   sobrescrever o original.
3. **Arquivo sem linha é invisível para a faxina.** Se a compensação
   falhar (storage aceita gravar e depois recusa apagar), sobra um objeto
   que nenhuma varredura por tabela encontra. Resolve-se com uma varredura
   pelo próprio storage, que só faz sentido com S3.
4. **A faxina precisa do tenant.** Não há varredura global — seria preciso
   `BYPASSRLS`, recusado. Um tenant que nunca mais fizer upload só é
   limpo pelo script.
5. **Nota interna não aceita anexo.**
6. **Sem antivírus, sem OCR, sem conversão de Office.** Documento é
   servido como download, nunca interpretado no servidor.
7. **O provedor local não é configurável por ambiente** — caminho dinâmico
   fazia o Turbopack empacotar o projeto inteiro. Produção é S3.
8. **Gravação de voz não foi exercitada com microfone real:** o navegador
   de automação não concede microfone, e a página não compõe quadros nesta
   sessão. O gravador está coberto por teste de unidade (formato,
   cancelamento, permissão negada, envio); o hardware, não.

---

## ✅ MVP 1.7 — PWA, notificações e Web Push: CONCLUÍDO em 10/08/2026

O problema desta fase, na frase do pedido: **um funcionário precisa ser
avisado quando um cliente entra em contato, mesmo que não esteja olhando
para o Elo.**

### O roadmap mudou de ordem — e por quê

O 1.7 era "WhatsApp de teste". Virou notificações; o WhatsApp foi para o
1.8. O Elo já recebe mensagem (canal `DEV`), já tem realtime e já tem caixa
compartilhada — o que falta para ser usado de verdade é alguém FICAR
SABENDO que chegou algo. O SSE do MVP 1.5 depende de página aberta, e uma
equipe de quinze pessoas não fica com uma aba na frente o dia inteiro.

Ganho de arquitetura junto: quando a Meta entrar, ela grava em `messages` e
as notificações já estarão funcionando em cima de `Message` — nenhuma linha
de código de notificação vai conhecer a Meta. Ver S61 em ELO_DECISOES.

### Schema

| Tabela | Papel |
|---|---|
| `notification_preferences` | o que ESTA pessoa quer receber, neste escritório. Uma linha por membership, criada só quando alguém muda algo |
| `push_subscriptions` | um DISPOSITIVO: endpoint, chaves do navegador, rótulo, revogação e contador de falhas |

As duas tenant-owned, com RLS + FORCE + WITH CHECK pela `elo_apply_rls()` e
cobertas pelo `rls-guard`.

### Web Push sem dependência nova

`src/server/push/webpush.ts` implementa VAPID (RFC 8292) e a cifra de
payload (RFC 8291) com o `crypto` do próprio Node: ECDH P-256, HKDF-SHA256,
AES-128-GCM e assinatura ES256. Mesmo raciocínio do verificador de JWT do
MVP 1.1 — caminho único, algoritmo fixo, nada escolhido por dado de fora.

### Os testes que carregam a fase

**Ida e volta da cifra.** O teste cifra com o código do Elo e **decifra
como o navegador decifraria**. Sem ele, um erro de cifra seria invisível: o
provedor responde 201 e o navegador descarta em silêncio o que não abre — o
defeito apareceria como "às vezes não avisa". Duas armadilhas ficaram
travadas: `dsaEncoding: "ieee-p1363"` (o DER padrão do Node vira 401 no
provedor) e o delimitador `0x02` no fim do registro.

**O texto não vaza.** Com a prévia desligada, "Meu faturamento foi R$ 200
mil" não aparece em lugar nenhum do payload — e mídia vira descrição
("enviou um documento"), sem o nome do arquivo, porque
`Balancete_MARIA_SILVA.pdf` conta a mesma história.

**Quem enviou não é avisado.** Parece óbvio escrito assim, e é o defeito
mais comum desse tipo de sistema: o código que avisa costuma nascer sem
saber quem originou o evento.

**Membership desativado para de receber**, sem ninguém fazer nada — é o
mecanismo servidor-side pedido no item 36, e não depende de logout.

**O push não bloqueia a mensagem.** Com o provedor recusando conexão, a
mensagem é gravada e a conversa segue. Se este teste falhar, o produto
trocou "avisar" por "conseguir conversar".

**O service worker não cacheia `/api`.** Verificado no código e no
navegador: o cache tem cinco arquivos (offline, manifesto, três ícones) e
nenhuma resposta autenticada.

### Uma decisão que foi desfeita no meio, e por quê

A primeira versão tinha uma função `SECURITY DEFINER` no banco para revogar
a inscrição do mesmo aparelho em OUTRO escritório. **Ela não funcionava** —
`FORCE ROW LEVEL SECURITY` vale também para o dono das tabelas (S2), então
nem o `elo_owner` atravessa. Fazê-la funcionar exigiria `BYPASSRLS`, a
porta que o MVP 1.0 fechou com cuidado.

A saída ficou melhor: quem resolve é o NAVEGADOR, com `unsubscribe()`, que
mata o endpoint no provedor. Qualquer linha antiga, em qualquer escritório,
recebe 410 na primeira tentativa e é revogada sozinha. Ver S68.

### Verificação executada

**562 testes no Elo** (25 arquivos; 137 novos entre push, notificações,
HTTP, PWA e interface) e 66 no Fiscale. `npm run verify` sai com 0.

Contra o servidor rodando, com sessão real criada por token de troca:

- service worker **registrado e controlando**, manifesto servido como
  `application/manifest+json`, ícones 192/512/maskable conferidos byte a
  byte (assinatura PNG e largura do IHDR);
- o cache do worker contém **exatamente** `/offline`, o manifesto e três
  ícones — **zero** entradas de `/api`;
- a tela de sem-conexão renderiza e **não carrega nome de cliente nem
  conversa**;
- mensagem de entrada aparecendo na lista sem refresh (SSE), prévia
  atualizada;
- crachá de não lidos: 3 na navegação e 3 no servidor; depois de marcar uma
  como lida, 2 no servidor — pessoal, calculado, nunca decrementado no
  cliente;
- prévia da mensagem ligada e desligada, com o efeito **conferido no
  servidor** (`showPreview` persistido) e na amostra da tela;
- presença: a rota aceita a PRÓPRIA conexão (`aplicado: true`) e recusa um
  id inventado (`aplicado: false`);
- 375 / 768 / 1280 sem rolagem horizontal e sem elemento estourando; crachá
  em azul de ação (`rgb(22,104,179)`), sem verde.

### O que NÃO foi validado — e não foi simulado

O navegador automatizado **nega a permissão de notificação por padrão**
(`Notification.permission === "denied"`) e **não tem serviço de push**:
`pushManager.subscribe()` responde `AbortError: Registration failed - push
service not available`.

Portanto **não foram exercitados em hardware**:

1. instalar a PWA;
2. conceder a permissão pelo diálogo do navegador;
3. criar uma inscrição de push de verdade;
4. receber a notificação do Windows com o Elo FECHADO;
5. clicar nela e cair no atendimento certo;
6. o crachá no ícone do aplicativo instalado (`setAppBadge`).

Tudo o que está do lado do servidor desses seis passos tem teste
automatizado — inclusive a cifra conferida por decifragem. O que falta é a
ponta do navegador real, e ela precisa de uma sessão sua no Chrome ou no
Edge do escritório. O roteiro está em [elo/README.md](elo/README.md).

Também não houve screenshot: o painel do navegador não compunha quadros
nesta sessão, como no MVP 1.5 e no 1.6. A conferência visual foi por
leitura de DOM, geometria e resolução de CSS por elemento.

### Débitos técnicos novos

1. **A validação em hardware está pendente** — os seis passos acima. É o
   débito principal desta fase, e o roteiro para fechá-lo está no README.
2. **Presença e agrupamento vivem em UM processo**, como o barramento do
   MVP 1.5. Com duas instâncias, os três precisam sair para um lugar
   compartilhado (Redis) — e é o mesmo débito, no mesmo lugar.
3. **O último aviso de uma rajada pode não sair.** Consequência aceita do
   agrupamento sem temporizador (S67): lista, crachá e contador continuam
   certos; o que se perde é a segunda batida na tela.
4. **`systemNotices` existe na preferência e nada a produz.** Não há aviso
   de sistema ainda — a caixinha está lá para a preferência não precisar de
   migration quando houver.
5. **Sem "não perturbe" para o push do sistema.** O horário silencioso
   silencia só o som dentro do Elo; o resto é o Windows e o celular. Fazer
   o servidor segurar push por horário exigiria fuso por pessoa, e ninguém
   pediu.
6. **iOS não foi testado.** As limitações conhecidas estão documentadas no
   README, e são reais: exige adicionar à tela de início, e a permissão só
   pode ser pedida a partir daí.
7. **Sem tela de administração de aparelhos de terceiros.** Cada pessoa vê
   e revoga os próprios; um admin não desliga o aviso de um colega — e isso
   é decisão (S73), não falta.
8. **A trava do `dev/inbound` impede o teste ponta a ponta com `next
   start`.** `NODE_ENV=production` bloqueia a entrada de desenvolvimento,
   corretamente. A validação usou `next start` com `NODE_ENV=development`.

## Roadmap

### MVP 1.0 — Fundação ✅ concluído
Projeto, banco, Docker, saúde, logs, erros, multitenancy com RLS, testes de
isolamento.

### MVP 1.1 — Identidade e sessão ✅ concluído em 09/08/2026
Identity/Membership, sessão revogável, RBAC, departamentos, token de troca
Ed25519 de 90 s com uso único. Detalhes na seção própria abaixo.
**O Redis NÃO entrou** — ver S9 em ELO_DECISOES.md.
**Antes de tocar em `SESSOES` do Fiscale:** backup, mapear os pontos de uso,
criar testes, alterar o mínimo.

### MVP 1.2 — Projeção de clientes ✅ concluído em 09/08/2026
`ExternalCustomerReference`, sincronização **outbound** do Fiscale,
idempotência, versionamento por hash, retry, tolerância a Elo offline.
Detalhes na seção própria abaixo.

### MVP 1.3 — Interface ✅ concluído em 09/08/2026
Shell do Elo, barra lateral, Atendimentos, Clientes, perfil do contato,
Configurações, tema claro/escuro/sistema. Detalhes na seção própria abaixo.

### MVP 1.4 — Modelo de atendimento ✅ concluído em 10/08/2026
Conversation, visualização, atribuição, transferência, departamento, tags,
nota interna, histórico e auditoria. Detalhes na seção própria abaixo.

### MVP 1.5 — Mensagens ✅ concluído em 10/08/2026
Message, MessageView, composer, realtime por SSE, paginação por cursor,
idempotência, reconexão. Detalhes na seção própria abaixo.
`MessageAttachment` saiu do escopo: anexo depende de storage, que é 1.6.

### MVP 1.6 — Storage ✅ concluído em 10/08/2026
MessageAttachment, abstração de storage, imagem, documento, áudio, voz,
Range e faxina de órfãos. Detalhes na seção própria abaixo.
URL assinada ficou para o provedor de S3: no local, o download é pela rota
autenticada.

### MVP 1.7 — PWA, notificações e Web Push ✅ concluído em 10/08/2026
Manifesto, ícones próprios, service worker, Web Push com VAPID,
preferências por funcionário, privacidade da prévia, crachá de não lidos,
som, agrupamento e página de sem-conexão. Detalhes na seção própria acima.
**Trocou de lugar com o WhatsApp** — ver S61 em ELO_DECISOES.md.

### MVP 1.8 — WhatsApp de teste
Adapter isolado em `channels/whatsapp/`. **Só número de teste.** O domínio do
Elo não conhece a Meta. As notificações do 1.7 já funcionam em cima de
`Message`: o canal entra gravando nas mesmas tabelas.

### MVP 1.9 — Auditoria e segurança
RBAC, RLS, IDOR, cross-tenant, upload, webhook spoofing, replay, CSRF, CORS,
rate limit, secrets.

### MVP 1.10 — Aceitação
Cenário ponta a ponta + prova de isolamento entre Tenant A e Tenant B.

---

## Fora do MVP 1

Chamadas · WebRTC · Stories · grupos avançados · IA · E2EE · apps nativos.

## Em aberto (não bloqueiam a fundação)

1. **É WhatsApp Business App ou WhatsApp comum?** Decide se coexistence se aplica.
2. **Modelo oficial de integração** — validar na documentação da Meta na fase 1.8.
   Não tratar BSP pago como certeza.
3. **Domínio** para o webhook — só na fase 1.8. Ele também é o que
   destrava o Web Push fora do `localhost`: service worker e push exigem
   contexto seguro, e em producao isso e HTTPS com certificado valido.
