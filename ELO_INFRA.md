# ELO — infraestrutura para homologação

**Análise e plano. NADA foi contratado, comprado, registrado ou
configurado.** Nenhuma conta criada, nenhum provedor acionado.

Escrito em **11/08/2026**. Decisão sua já registrada: **VPS + domínio +
HTTPS**, e não túnel de desenvolvimento.

---

## 1. O que o projeto consome HOJE — medido, não estimado

Medido nesta máquina, com o build de produção rodando:

| O quê | Medida |
|---|---|
| ELO em produção (`next start`), ocioso | **128 MB** de RSS |
| ELO depois de exercitar rotas | **116 MB** (o Node devolveu memória) |
| PostgreSQL 16 (contêiner), ocioso | **44 MB** |
| Banco `elo` inteiro | **17 MB** — e quase tudo é *bloat* dos 615 testes |
| Linhas reais de negócio | **0** (os testes limpam o que criam) |
| `.next` (build + cache) | 523 MB |
| `.next/static` (o que vai ao navegador) | **711 KB** |
| `node_modules` | 823 MB |
| `.storage` (anexos) | 31 KB |

**A leitura que importa:** o ELO em si é pequeno. Ocioso, a pilha inteira
cabe em **menos de 400 MB**. O que dimensiona o servidor não é rodar — é
**compilar**.

### O gargalo real: `next build`

O build com Turbopack é o pico de memória do projeto. Com 2 GB de RAM ele
pode falhar por falta de memória num servidor que também roda Postgres.

**Duas saídas, e recomendo a primeira:**

1. **Compilar fora do servidor** — build na máquina do escritório (ou em
   CI, um dia), e sobe só o resultado. O VPS só executa;
2. compilar no VPS, e aí **4 GB é o piso**, não o conforto.

Isso muda a conta de RAM, e é por isso que aparece antes dela.

### Uma mudança pequena que vale a pena antes de subir

`next.config.ts` não tem `output: "standalone"`. Com ele, o Next monta uma
pasta com **só o necessário** para rodar — normalmente algumas dezenas de
MB, em vez de arrastar os 823 MB de `node_modules` para o servidor.

Não mexi nisso agora (esta fase é análise). Fica no checklist.

---

## 2. Arquitetura recomendada

Simples de propósito. **Sem Kubernetes, sem cluster, sem orquestrador** —
são quinze pessoas e um número de WhatsApp.

```
                    INTERNET
                       │
                       │  443/tcp  (e 80 só para redirecionar)
                       ▼
        ┌──────────────────────────────┐
        │  Caddy                       │  TLS automático (Let's Encrypt)
        │  elo.seudominio.com.br       │  renovação sozinha
        └───────────────┬──────────────┘
                        │  rede interna do Docker
                        ▼
        ┌──────────────────────────────┐
        │  ELO (Next.js)               │  ÚNICO serviço web
        │  :3000 — só na rede interna  │  não publica porta no host
        └───────┬──────────────┬───────┘
                │              │
                ▼              ▼
     ┌────────────────┐  ┌──────────────────┐
     │ PostgreSQL 16  │  │ volume /storage  │  anexos
     │ SEM porta      │  │ (disco do VPS)   │
     │ publicada      │  └──────────────────┘
     └────────────────┘
```

**Três regras que a arquitetura aplica:**

1. **O Postgres não existe para a internet.** Sem `ports:` no compose —
   só `expose`. Quem o alcança é o contêiner do ELO, pela rede interna.
   Nem com senha vazada alguém conecta de fora, porque não há porta.
2. **O ELO é o único serviço web público.** Não há painel, phpMyAdmin,
   Adminer nem porta de banco.
3. **O TLS termina no Caddy**, e o ELO nunca vê certificado.

### Caddy ou Nginx?

**Caddy.** A configuração inteira desta arquitetura cabe em cinco linhas,
e o certificado é obtido e renovado sozinho — sem certbot, sem cron, sem
o incidente clássico de "o site caiu porque o certificado venceu num
domingo".

Nginx é excelente e eu o escolheria se houvesse necessidade específica
(regras complexas de cache, WAF, muitos vhosts). Não há. Trocar cinco
linhas por trinta e um certbot para manter seria pagar complexidade sem
comprar nada.

**Um detalhe que importa aqui:** o ELO usa **SSE** (o realtime do MVP
1.5). Proxy que bufferiza quebra streaming. O Caddy não bufferiza por
padrão — e a rota já manda `X-Accel-Buffering: no` por precaução.

---

## 3. Requisitos MÍNIMOS do VPS

Para homologação, com build feito fora:

| Recurso | Mínimo | Por quê |
|---|---|---|
| vCPU | **1** | medido: ocioso quase não usa CPU |
| RAM | **2 GB** | ~400 MB da pilha + folga de SO e picos |
| SSD/NVMe | **40 GB** | sistema + imagens + banco + anexos do primeiro ano |
| Tráfego | **1 TB/mês** | folgadíssimo (ver §12) |
| SO | **Ubuntu 24.04 LTS** | suporte até 2029, é o que a documentação do projeto já assume |

⚠️ Com 2 GB, **não compile no servidor**.

## 4. Requisitos RECOMENDADOS

| Recurso | Recomendado | Por quê |
|---|---|---|
| vCPU | **2** | um build eventual no servidor, e o Postgres sem disputar |
| RAM | **4 GB** | permite compilar no VPS e dá espaço para o `shared_buffers` crescer |
| SSD/NVMe | **80–100 GB** | anexos por vários anos sem pensar no assunto |
| Tráfego | **2 TB+** | idem |
| Backup do provedor | **sim** | como rede extra, **nunca** como estratégia única (§13) |

**É o que eu recomendaria contratar:** 2 vCPU / 4 GB / ~100 GB.

---

## 5. Três opções de hospedagem — preços verificados em 11/08/2026

### A. Hostinger — VPS KVM 2 · **recomendada para começar**

| | |
|---|---|
| Configuração | 2 vCPU · **8 GB** RAM · 100 GB NVMe · 8 TB tráfego |
| Preço | **R$ 42,99/mês** no primeiro ciclo · **R$ 77,99/mês** na renovação |
| Datacenter | América do Sul (a página não confirma "São Paulo" com todas as letras — **confirmar antes de fechar**) |
| Backup | **semanal incluído**, mais snapshots manuais |
| Cobrança | **em real** |

**Prós:** melhor RAM por real da lista, backup incluso, suporte e cobrança
em português. **Contra:** o preço promocional é do primeiro ciclo; a conta
real é a da renovação, e é ela que deve entrar no orçamento.

### B. Magalu Cloud — BV2-4-100

| | |
|---|---|
| Configuração | 2 vCPU · 4 GB RAM · 100 GB NVMe |
| Preço | **R$ 152,99/mês** |
| Datacenter | **Brasil** |
| Cobrança | **em real**, nota fiscal brasileira, sem variação cambial |

**Prós:** empresa brasileira, datacenter no país, previsibilidade total de
custo e nota fiscal sem complicação. **Contra:** **três a quatro vezes mais
caro** pela mesma coisa. Faz sentido se houver exigência de fornecedor
nacional; para este porte, é caro.

### C. Hetzner — CX23

| | |
|---|---|
| Configuração | 2 vCPU · 4 GB RAM · 40 GB SSD · 20 TB tráfego |
| Preço | a partir de **€ 3,49/mês** (~R$ 22) |
| Datacenter | **Alemanha** |
| Cobrança | **em euro** |

**Prós:** o mais barato com folga, e reputação técnica excelente.
**Contra decisivo:** a latência Recife↔Alemanha fica na casa das **centenas
de milissegundos**. Para o webhook da Meta, tudo bem. Para quinze pessoas
digitando e vendo o SSE responder, **cada clique parece lento** — e é o
tipo de coisa que faz a equipe achar que "o sistema é ruim".

> **Também existe:** a Vultr tem datacenter em **São Paulo** (confirmado),
> com cobrança em dólar por hora. Não consegui abrir a tabela de preços
> oficial (o site recusou a leitura automatizada), então **não afirmo
> valores**. Fica como quarta opção a conferir manualmente, se quiser
> comparar.

### Recomendação

**Hostinger KVM 2**, orçando pela **renovação (R$ 77,99)** e não pela
promoção. Confirme o datacenter de São Paulo antes de fechar — se não
houver, a Magalu passa a ser a escolha certa apesar do preço, porque
latência ruim é permanente e o preço é mensal.

---

## 6. Domínio: exclusivo ou subdomínio?

**Subdomínio, sem dúvida:** `elo.seudominio.com.br`.

**Por quê:**

- **um domínio a menos para manter** — vencimento, DNS, titularidade;
- **custo zero adicional**: subdomínio não se registra, só se aponta;
- **o nome conta a verdade**: o ELO é parte do ecossistema FISCALE do
  escritório, não um produto de outra empresa;
- **reputação**: um domínio recém-registrado tem histórico zero. Um
  subdomínio de um domínio antigo herda o que já existe.

Se o escritório ainda **não tiver** um domínio próprio, aí sim registre um
`.com.br` no **Registro.br** — **R$ 40,00/ano** (≈ R$ 3,33/mês).

**Um subdomínio a mais, para depois:** `elo-hml.seudominio.com.br` para
homologação, quando produção existir.

---

## 7. DNS

Um registro. Só isso.

```
elo.seudominio.com.br.      A      <IP do VPS>       TTL 300
```

- **`A`, não `CNAME`** — CNAME em subdomínio funciona, mas o `A` é direto
  e não depende de outro nome resolver;
- **TTL 300 (5 min)** durante a implantação: se precisar trocar o IP, a
  mudança pega rápido. Depois de estável, pode subir para 3600;
- **IPv6 (`AAAA`)**: opcional; se o provedor der IPv6, acrescente — a Meta
  alcança pelos dois;
- **Nada de wildcard.** `*.seudominio.com.br` apontando para o VPS
  transformaria qualquer nome esquecido num endereço válido.

**Antes de emitir o certificado**, o DNS precisa estar propagado — o
Let's Encrypt confirma que o domínio aponta mesmo para aquele servidor.

---

## 8. HTTPS

O `Caddyfile` inteiro:

```caddy
elo.seudominio.com.br {
    encode zstd gzip
    reverse_proxy elo:3000
}
```

O Caddy resolve sozinho: pede o certificado ao Let's Encrypt, redireciona
80 → 443, renova antes de vencer, e serve TLS 1.2/1.3 com configuração
moderna.

**O que isso destrava de uma vez só:**

| Pendência | Por que precisava de HTTPS |
|---|---|
| **Webhook da Meta** (MVP 1.8.1) | a Meta exige URL pública com certificado válido |
| **Web Push / PWA** (MVP 1.7) | service worker e push só funcionam em contexto seguro |

São as duas homologações pendentes, e **uma única configuração resolve as
duas**.

**Ajustar ao subir:** `ELO_COOKIE_SECURE=true` no `.env`. Hoje é `false`
porque em desenvolvimento o ELO serve HTTP; com HTTPS, o cookie de sessão
precisa do `Secure`.

---

## 9. Firewall — só o indispensável

```
ENTRADA
  22/tcp    SSH      só com CHAVE; senha desligada
  80/tcp    HTTP     só para o redirecionamento e o desafio do certificado
  443/tcp   HTTPS    o ELO
  todo o resto: NEGADO

SAÍDA
  liberada (o ELO precisa falar com a Meta e com o provedor de push)
```

**O que NÃO fica aberto, e é o ponto:** `5432` (Postgres) e `3000` (Next).
Os dois vivem na rede interna do Docker e **não publicam porta no host**.

**SSH, com cuidado real:**

- **autenticação só por chave** — `PasswordAuthentication no`;
- **sem root direto** — `PermitRootLogin no`, e um usuário com `sudo`;
- **`fail2ban`** para cortar tentativa em massa;
- opcional, e bom: mover o SSH para uma porta alta corta 99% do ruído de
  varredura — não é segurança de verdade, é menos barulho no log.

⚠️ **Atenção com o Docker e o UFW:** o Docker escreve regras direto no
`iptables` e **passa por cima do UFW** quando um contêiner publica porta.
Por isso a arquitetura **não publica** as portas de banco e aplicação — é
o que torna o firewall confiável em vez de decorativo.

---

## 10. Docker e contêineres

Três contêineres, um arquivo:

```yaml
services:
  caddy:      # único com portas 80/443 no host
  elo:        # expose 3000, sem ports
  postgres:   # expose 5432, sem ports
volumes:
  pgdata      # banco
  storage     # anexos
  caddy_data  # certificados
```

**Decisões:**

- **imagem própria do ELO**, multi-stage, com `output: "standalone"` — sai
  de ~800 MB para algumas dezenas;
- **`restart: unless-stopped`** em tudo: o VPS reinicia e a pilha volta
  sozinha. É metade do "24/7";
- **volumes nomeados** para banco e anexos — o dado sobrevive a `docker
  compose down` e a troca de imagem;
- **healthcheck do Postgres** (já existe no compose atual) e do ELO
  apontando para `/api/health`, que já devolve 200/503.

**Não entra:** Kubernetes, Swarm, Portainer, Traefik. Um servidor, três
contêineres. Cada peça a mais é uma peça a mais para atualizar e entender
às 22h de uma sexta.

---

## 11. Banco de dados

- **PostgreSQL 16**, contêiner, **sem porta publicada**;
- os três papéis do MVP 1.0 continuam: `postgres` (bootstrap),
  `elo_owner` (migrations), `elo_app` (runtime, só DML). **O RLS forçado
  continua sendo a segunda camada** — e o `rls-guard` continua no `verify`;
- `shared_buffers` ~25% da RAM disponível ao contêiner (com 4 GB: ~512 MB
  a 1 GB);
- **autovacuum ligado** (padrão): o banco de teste chegou a 17 MB de
  *bloat* com 0 linhas vivas — em produção, com apagamento e atualização,
  isso importa;
- **acesso administrativo**: por túnel SSH (`ssh -L`), nunca abrindo a
  porta. Quem administra chega pela porta da frente, com chave.

**Homologação e produção NUNCA no mesmo banco** — §17.

---

## 12. Armazenamento — quanto de mídia, e quando migrar

Estimativa para o porte do escritório (≈14 clientes, 15 pessoas):

| Tipo | Tamanho típico | Volume/mês estimado | Espaço/mês |
|---|---|---|---|
| Texto | ~200 B | 8.000 mensagens | **~2 MB** |
| Imagem (foto de documento pelo celular) | ~2 MB | 200 | **~400 MB** |
| PDF (guia, balancete) | ~200 KB | 300 | **~60 MB** |
| Áudio anexado | ~1 MB/min | 50 | **~100 MB** |
| Mensagem de voz (Opus, ~30 s) | ~250 KB | 300 | **~75 MB** |

**Total: ~640 MB/mês → ~7,7 GB/ano.**

Mais a importação inicial de histórico (180 dias, quando a fase 1.8.5
chegar): **alguns GB, uma vez**.

### Quando o SSD do VPS basta — e quando não

| Situação | Onde guardar |
|---|---|
| **Até ~50 GB** de mídia | **SSD do VPS.** Simples, rápido, sem custo extra |
| **Acima de ~50 GB** | migrar para **object storage** (S3/R2) |

Com 100 GB de disco e ~8 GB/ano, isso dá **cinco anos ou mais** antes de o
assunto voltar. E o MVP 1.6 já preparou a troca: existe a interface
`StorageProvider`, e o S3 é uma classe nova mais uma variável de ambiente
(S49).

**Os dois sinais de que chegou a hora**, e nenhum é o disco encher:

1. **o backup dos anexos começar a demorar demais** — é o primeiro a doer;
2. o disco passar de ~60% e obrigar a pensar em espaço a cada mês.

---

## 13. Backups — e o snapshot do provedor **não** é a estratégia

Snapshot do VPS é ótimo para "o servidor quebrou". É inútil para:

- "alguém apagou o atendimento errado na terça";
- "o provedor teve um problema na conta";
- "preciso do banco de três semanas atrás".

**Backup de verdade sai do VPS.**

### O que é copiado

| O quê | Como | Quando |
|---|---|---|
| **PostgreSQL** | `pg_dump -Fc` (formato custom, comprimido) | diário |
| **Anexos** | incremental (`restic` ou `rsync`) | diário |
| **`.env` e Caddyfile** | cifrado, à parte | a cada mudança |

**Cifrar antes de sair do servidor.** O dump tem conversa de cliente; ele
não pode ficar legível num armazenamento de terceiro.

### Para onde

**Duas cópias, em lugares diferentes** — e a segunda é a que resolve o
"problema na conta do provedor":

1. **object storage de outro fornecedor** (~R$ 5–10/mês por 10–20 GB);
2. **o computador do escritório PUXA a cópia** — via SSH, uma vez por dia.

> A segunda é elegante e barata: **o escritório busca, o VPS não empurra**.
> Nenhuma porta é aberta no escritório, e a cópia fica numa máquina que
> ninguém de fora alcança. É a mesma direção de conexão da integração
> FISCALE → ELO.

### Retenção

```
7 diários  ·  4 semanais  ·  6 mensais
```

Cobre "ontem", "semana passada" e "antes do fechamento do mês", sem
guardar tudo para sempre.

### Restauração — documentada e TESTADA

A regra do MVP 1.0 vale aqui: **backup que nunca foi restaurado não é
backup, é esperança.**

O procedimento precisa estar escrito (subir Postgres limpo, `pg_restore`,
apontar o ELO, conferir contagens) e **executado uma vez por mês** contra
um banco descartável — com o resultado anotado. Dez minutos por mês.

---

## 14. Monitoramento

Nada pesado. Três coisas:

1. **Sonda externa** batendo em `https://elo.../api/health` a cada 5 min.
   A rota **já existe** e já distingue 200 (ok) de 503 (banco fora).
   UptimeRobot ou BetterStack no plano gratuito resolvem, e avisam por
   e-mail. **Externa de propósito:** um monitor dentro do VPS não avisa
   quando o VPS cai.
2. **Logs.** O ELO já emite JSON estruturado com redação de segredos
   (comprovado no teste de ontem: nenhum token apareceu). No servidor,
   `docker compose logs` com **rotação configurada** — sem isso, o log
   enche o disco e derruba tudo por um motivo bobo.
3. **Disco.** Um alerta simples em 80%.

O que **não** entra agora: Prometheus, Grafana, Loki. São ótimos e são
mais três serviços para manter, atualizar e entender. Quando houver
pergunta que os logs não respondam, aí sim.

---

## 15. Segurança — o resumo

| Camada | Medida |
|---|---|
| Rede | só 22/80/443; banco e app sem porta pública |
| SSH | só chave, sem root, `fail2ban` |
| TLS | Caddy, renovação automática, redirect 80→443 |
| Sessão | `ELO_COOKIE_SECURE=true` (mudança obrigatória ao subir) |
| Webhook | `X-Hub-Signature-256` sobre o corpo cru, falha fechada (1.8.1) |
| Banco | RLS + FORCE + `WITH CHECK`; `elo_app` só DML |
| Segredos | `.env` com permissão `600`, dono do serviço; nunca em repositório |
| Atualização | `unattended-upgrades` para segurança do SO |
| Imagens | atualizar Postgres e Node por versão fixada, não `latest` |

**Segredos que passam a existir no VPS:** senhas do Postgres, chaves VAPID,
chave pública Ed25519 do FISCALE, App Secret e access token da Meta.
Todos em `.env`, no servidor, com permissão restrita.

**Um lembrete que vale repetir:** trocar o par VAPID invalida todas as
inscrições de push. Ao migrar para o VPS, **leve o par existente** ou
avise que todos precisarão ativar as notificações de novo.

---

## 16. Segregação FISCALE ↔ ELO — inalterada, e reforçada

**O VPS NÃO recebe, em nenhuma hipótese:**

- certificados `.pfx` dos clientes;
- senhas de certificado;
- blob DPAPI;
- XMLs fiscais;
- credenciais de prefeitura;
- o banco fiscal do FISCALE.

Isso não é promessa: é o desenho do MVP 1.2. A projeção que sobe tem
**lista branca aplicada nos dois lados**, e campo fora do contrato faz o
payload ser **recusado** — não descartado em silêncio (S18).

O que sobe: **nome, CNPJ, e-mail, telefone, ativo**. Só.

### O fluxo, e a direção da conexão

```
   ESCRITÓRIO (AMMOURIM)                      VPS
   ┌────────────────────┐                ┌──────────────┐
   │ FISCALE :8777      │                │ ELO :443     │
   │ certificados       │  ──── HTTPS ──►│ (público)    │
   │ XMLs, DPAPI        │   SAÍDA apenas │              │
   │                    │                │              │
   │ NENHUMA porta      │ ◄─── nunca ────│              │
   │ de entrada aberta  │                │              │
   └────────────────────┘                └──────────────┘
```

**O FISCALE só faz conexão de SAÍDA.** Nenhuma porta é aberta no
computador do escritório, nenhum encaminhamento no roteador, nada de DDNS.

Como já funciona hoje (MVP 1.2):

- o FISCALE assina cada requisição com **Ed25519** (par próprio, separado
  do par de login);
- assina método, caminho, timestamp, nonce e hash do corpo — método e
  caminho entram para que uma assinatura válida num endpoint não sirva em
  outro (S19);
- **anti-replay**: timestamp ±300 s + nonce atômico no Postgres;
- **o tenant vem da chave**, nunca do corpo;
- se o ELO estiver fora, a fila local em SQLite segura e reenvia (S23).

**O que muda ao subir para o VPS:** uma linha de configuração no FISCALE —
a URL passa de `http://localhost:3000` para
`https://elo.seudominio.com.br`. Mais nada.

**E o inverso continua valendo:** com o FISCALE desligado, o ELO e o
WhatsApp funcionam normalmente. A última projeção de clientes já está no
VPS. É o requisito 12 da sua lista, e ele já está atendido por desenho.

---

## 17. Ambientes

### DEV — esta máquina

Como está: Docker Compose local, Postgres na 5433, ELO em `localhost`,
`.env` de desenvolvimento. **Não muda nada.**

### HOMOLOGAÇÃO — o VPS

- `elo.seudominio.com.br` (ou `elo-hml.` se já houver produção);
- **número de TESTE da Meta**, com até 5 destinatários;
- banco `elo_hml`, **separado**;
- `ELO_ALLOW_DEV_INBOUND=0` — o número de teste é real, a porta de
  desenvolvimento não precisa existir aqui;
- é onde as **duas pendências** são finalmente testadas (§19).

### PRODUÇÃO — só depois

Duas formas, e recomendo a primeira:

| | Como | Custo |
|---|---|---|
| **A. Promover** | quando homologação for aprovada, ela vira produção (banco novo, dados de teste apagados) e a homologação volta para a máquina local | **um VPS** |
| **B. Separar** | um VPS para cada | dois VPS |

Para quinze pessoas, **A**. A separação física vale quando houver alguém
mexendo em homologação enquanto outra pessoa depende da produção — que não
é o caso hoje.

**O que NUNCA se mistura, em qualquer das formas:** o banco. Homologação e
produção com bancos separados, sempre. Um `pg_dump` restaurado no lugar
errado é o tipo de acidente que não tem desfazer.

---

## 18. Como os 615 testes ficam preservados

**A suíte não roda no VPS.** Ela precisa de um Postgres onde possa criar e
apagar tenants à vontade — fazer isso no banco de homologação é pedir para
apagar dado de teste real da Meta.

O fluxo que preserva:

```
1. na máquina local:  npm run verify   → 615/615 e build ok
2. só então:          empacotar e subir
3. no VPS:            prisma migrate deploy
4. no VPS:            fumaça (§19) — não a suíte inteira
```

**Antes e depois da implantação, a suíte é a mesma e roda no mesmo lugar:
aqui.** O que muda no VPS é apenas *onde o resultado roda*.

**Débito honesto:** hoje isso depende de alguém lembrar de rodar o
`verify` antes de subir. Um CI (GitHub Actions ou equivalente) faria
disso uma regra em vez de um hábito. Não é necessário agora, e vale
quando houver mais de uma pessoa publicando.

**Teste de fumaça no VPS** (rápido, não destrutivo):

- `GET /api/health` → 200;
- `GET /api/webhooks/whatsapp?hub.mode=subscribe&...` → devolve o desafio;
- POST no webhook com **assinatura errada** → **403**;
- abrir o ELO pelo navegador e entrar pelo FISCALE.

---

## 19. Ordem de implantação

Nada aqui foi feito. É a sequência proposta, cada passo dependendo do
anterior.

**Fase 0 — decisões suas (sem custo)**
1. confirmar o datacenter São Paulo da Hostinger — ou trocar de opção;
2. definir o domínio: já tem um `.com.br`? Se não, registrar (R$ 40/ano);
3. escolher o subdomínio: `elo.` ou `elo-hml.`.

**Fase 1 — contratar e abrir (você)**
4. contratar o VPS, Ubuntu 24.04 LTS;
5. criar a chave SSH e subir a pública **na criação** do servidor;
6. apontar o DNS `A` para o IP, TTL 300.

**Fase 2 — endurecer o servidor**
7. usuário não-root com `sudo`; desligar senha e root no SSH;
8. `ufw`: 22, 80, 443; `fail2ban`; `unattended-upgrades`;
9. Docker + Compose.

**Fase 3 — subir a pilha**
10. `docker-compose.prod.yml` (Caddy + ELO + Postgres, sem portas de
    banco/app);
11. `.env` de homologação, permissão 600 — **incluindo o par VAPID atual**;
12. `prisma migrate deploy`;
13. certificado emitido pelo Caddy; conferir o cadeado.

**Fase 4 — ligar o que estava esperando**
14. **Meta**: criar o app, pegar o número de teste, apontar o webhook
    para `https://elo.../api/webhooks/whatsapp`, cadastrar os 5
    destinatários;
15. **FISCALE**: trocar a URL do ELO para a pública;
16. backup diário + primeira restauração de teste;
17. sonda externa em `/api/health`.

**Fase 5 — as duas homologações, juntas**
18. **A — MVP 1.7**: instalar a PWA no Windows, ativar notificações,
    **fechar o Elo**, gerar mensagem, receber a notificação, clicar,
    cair no atendimento certo;
19. **B — MVP 1.8.1**: mandar mensagem de um dos 5 números para o número
    de teste, ver o atendimento nascer, responder pelo ELO, ver chegar no
    WhatsApp e o status voltar.

**Sem tocar no número real. Sem Coexistence. Sem iniciar a 1.8.2.**

---

## 20. Custo mensal estimado

### Cenário recomendado (Hostinger KVM 2)

| Item | 1º ciclo | Depois |
|---|---|---|
| VPS 2 vCPU / 8 GB / 100 GB | R$ 42,99 | **R$ 77,99** |
| Domínio `.com.br` (R$ 40/ano) | R$ 3,33 | R$ 3,33 |
| Object storage do backup (~10 GB) | R$ 0–10 | R$ 0–10 |
| Monitoramento (plano gratuito) | R$ 0 | R$ 0 |
| **Total** | **≈ R$ 46–56** | **≈ R$ 81–91** |

> Se o escritório já tiver domínio, tire os R$ 3,33.
> Se a segunda cópia do backup for a máquina do escritório puxando, tire
> também o object storage — e aí fica **R$ 78/mês**.

### Comparação honesta

| Provedor | Mensal (estável) | Observação |
|---|---|---|
| Hostinger KVM 2 | **~R$ 78** | melhor custo/benefício; confirmar SP |
| Magalu BV2-4-100 | **~R$ 153** | metade da RAM, o dobro do preço; datacenter BR garantido |
| Hetzner CX23 | **~R$ 22** | mais barato, mas latência da Alemanha |

### O que ainda NÃO está nesta conta

- **WhatsApp**: zero durante a homologação (número de teste é gratuito).
  Em produção entra a tarifa por mensagem — e lembre-se de **01/10/2026**,
  quando mensagem de serviço passa a ser cobrada (ELO_META_AUDITORIA §11);
- **Web Push**: zero, sempre. É protocolo aberto, sem intermediário pago.

---

## Resumo em cinco linhas

1. Um VPS de **2 vCPU / 4–8 GB / 100 GB**, Ubuntu 24.04, em São Paulo.
2. **Caddy** na frente, **ELO** no meio, **Postgres sem porta pública**.
3. `elo.seudominio.com.br`, um registro `A`, HTTPS automático.
4. Backup **para fora do VPS**, com restauração testada todo mês.
5. **~R$ 78–90/mês**, e destrava as duas homologações pendentes de uma vez.
