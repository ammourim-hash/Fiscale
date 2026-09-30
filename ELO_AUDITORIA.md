# Elo — auditoria e planejamento

Auditoria do Fiscale existente e plano do Elo como módulo do ecossistema.
**Nada foi implementado.** Documento para aprovação.

Escrito em **06/08/2026**. Complementa [FISCALE_ELO.md](FISCALE_ELO.md), que já
trazia nome, símbolo, telas e as três perguntas sobre migração de número.

---

## 1. O que já existe no Fiscale e dá para reaproveitar

Levantado lendo o código, não de memória.

| Peça | Onde | Reaproveitar? |
|---|---|---|
| **Cadastro de clientes** | `state_clientes.json` — 14 clientes com CNPJ, regime, IE, IM, município, e-mail, telefone | ✅ **fonte de verdade**; o Elo lê, nunca duplica |
| **Autenticação** | `fiscale_server.py` — PBKDF2-SHA256, 200k iterações, sal por usuário | ✅ o algoritmo; ❌ o armazenamento (ver item 7) |
| **Usuários** | `usuarios.json` | ⚠️ serve para 1–3 pessoas; precisa virar tabela |
| **Proxy de rotas** | `NFSE_PREFIXOS` em `fiscale_server.py` | ✅ o mesmo padrão serve para `/api/elo/*` |
| **Estado por módulo** | `/api/state/<mod>` + `Fiscale.salvar/carregar` | ✅ para preferências; ❌ para conversas |
| **Identidade visual** | `fiscale-nav.js` (barra superior, tema balão), `fiscale-logo.svg` | ✅ integralmente |
| **Dados fiscais** | `core.py`, `classificador.py`, `apuracao_federal.py` | ✅ leitura para o painel do cliente |
| **Documentos** | XMLs em `~/SistemaNFSe/dados/<cnpj>/xmls/` | ✅ destino do "enviar ao Fiscale" |
| **Vencimentos** | `vencimentos.py` | ✅ alimenta alerta proativo pelo Elo |

**Não existe hoje:** banco de dados, multitenancy, RBAC, WebSocket, storage de
mídia, fila, cache. Tudo isso é novo.

## 2. O que já definimos do Elo nesta conversa

- **Nome:** Fiscale Elo. **Símbolo:** dois elos entrelaçados cujo vão forma um balão.
- **Canal:** WhatsApp Cloud API oficial. Baileys e similares **descartados** — derrubam o número.
- **Apagadas:** arquivamento por webhook. A Meta avisa a exclusão; o conteúdo já está guardado.
- **Hospedagem:** VPS obrigatório. A Meta retenta por até 7 dias e depois descarta; máquina desligada = cliente sem resposta.
- **Migração:** você optou por migrar o número atual. Exportar as conversas **antes** é irreversível ([ELO_ANTES_DE_MIGRAR.txt](ELO_ANTES_DE_MIGRAR.txt)).
- **Schema inicial:** [elo/schema.sql](elo/schema.sql) — 7 tabelas, ainda não aplicado.
- **Telas:** três colunas; a da direita mostra dados do Fiscale — é o diferencial.

## 3. O que precisa ser criado

Banco PostgreSQL · receptor de webhook · API REST · WebSocket · storage de mídia ·
RBAC · sessões persistentes · camada de IA · fila de trabalho · painel de
pendências compartilhado · classificador de documentos recebidos.

## 4. O que muda no Fiscale

**Pouco, e nada que quebre:**

1. `fiscale_server.py` — acrescentar `/api/elo` aos prefixos de proxy (1 linha)
2. `fiscale-nav.js` — item "Elo" na barra e seletor de aplicativo
3. **Novo** `nfse/backend/elo_ponte.py` — expõe cliente, pendências e prévia do DAS ao Elo, **somente leitura**
4. Sessões saem da memória para arquivo/banco (hoje todo reinício desloga)

## 5. O que NÃO deve ser tocado

`core.py` · `classificador.py` · `apuracao_federal.py` · `auditor_nfe.py` ·
`prefeituras.py` · `recife.py` · `vencimentos.py` · `seguranca.py` (DPAPI) ·
o formato dos XMLs · `certificados.json` e a pasta `certs`.

**Regra:** o Elo **lê** o Fiscale. Nunca escreve, exceto ao depositar um
documento recebido — e mesmo aí, na pasta de importação, nunca no acervo direto.

---

## 6. Arquitetura

```
┌───────────────── VPS (sempre ligado) ─────────────────┐
│  elo-receptor   webhook da Meta → grava CRU primeiro  │
│  elo-api        REST + WebSocket                      │
│  PostgreSQL     conversas, mensagens, tarefas         │
│  Redis          presença, digitando, fila             │
│  storage/       mídias                                │
└────────────────────────┬──────────────────────────────┘
                         │ HTTPS (token assinado)
┌────────────────────────┴──────────────────────────────┐
│  AMMOURIM — Fiscale (como está hoje)                  │
│  fiscale_server.py :8777                              │
│  elo_ponte.py  → cliente, pendências, prévia do DAS   │
│  certificados, XMLs, DPAPI  ← NUNCA saem daqui        │
└───────────────────────────────────────────────────────┘
```

**Princípio:** o VPS vê **conversa**; a máquina do escritório guarda
**certificado e dado fiscal**. Nunca se cruzam no mesmo servidor.

## 7. Autenticação compartilhada

**O problema:** hoje `SESSOES` é um dicionário em memória e o cookie
`fiscale_sessao` é `SameSite=Lax`, válido só na origem do Fiscale. Num VPS
(outra origem), o cookie **não viaja**. E todo reinício desloga todo mundo —
inaceitável para 15 pessoas.

**Solução:** o Fiscale vira emissor de identidade.

```
1. usuário faz login no Fiscale (como hoje)
2. clica em "Elo"
3. Fiscale emite JWT curto (5 min), assinado com chave compartilhada
4. navegador leva ao Elo
5. Elo valida, cria sessão própria (cookie do domínio dele)
6. renova por refresh token
```

Um login, dois produtos. Mantém o PBKDF2 atual, que é adequado.

**Mudança obrigatória no Fiscale:** sessões em disco/banco. Sem isso, reiniciar
o Fiscale derruba a sessão de todos os atendentes.

## 8. Banco de dados

**PostgreSQL, e só para o Elo.** Os módulos fiscais continuam em JSON — funciona,
é auditável e tem um usuário. O Elo tem 15 escrevendo ao mesmo tempo: JSON
perderia conversa.

Base: [elo/schema.sql](elo/schema.sql) (7 tabelas). Acrescentar por fase:
`tarefa`, `lembrete`, `mensagem_agendada`, `aguardando_resposta`, `tag`,
`pasta`, `pendencia`, `documento_recebido`, `transcricao`, `sessao`,
`tenant`, `papel`, `permissao`.

**Fonte de verdade — sem duplicar:**

| Dado | Dono | O outro faz o quê |
|---|---|---|
| Cliente, CNPJ, regime | **Fiscale** | Elo guarda só o CNPJ como chave |
| Notas, DAS, apuração | **Fiscale** | Elo consulta ao abrir a conversa |
| Conversa, mensagem, mídia | **Elo** | Fiscale não sabe que existe |
| Pendência de documento | **compartilhado** | tabela no Elo, origem marcada |
| Usuário e senha | **Fiscale** | Elo recebe identidade por JWT |

## 9. Integração entre os módulos

`elo_ponte.py` no Fiscale expõe, **somente leitura**:

```
GET /api/elo/cliente/{cnpj}      nome, regime, município, IM
GET /api/elo/cliente/{cnpj}/resumo  prévia do DAS, notas do mês, vencimentos
GET /api/elo/pendencias/{cnpj}
POST /api/elo/documento          recebe arquivo → pasta de importação
```

Sentido inverso: o Fiscale gera **sugestão de mensagem** ("DAS disponível"), o
Elo abre já escrita, **o usuário revisa e envia**. Nunca automático.

## 10. Realtime

**Socket.IO** sobre WebSocket, com Redis como *adapter*. Motivo: reconexão e
*fallback* prontos, e a rede de escritório com Wi-Fi instável precisa disso.

Eventos: `mensagem:nova`, `mensagem:status`, `digitando`, `presenca`,
`conversa:atribuida`, `pendencia:atendida`.

## 11. Arquivos

Disco do VPS na Fase 1 (mais simples, mais barato); S3/R2 quando passar de
~50 GB. Nunca URL pública permanente: link assinado com validade curta.
Toda mídia com SHA-256, MIME validado e tamanho limitado.

## 12. IA

Camada `ProvedorIA` com uma interface e implementações trocáveis
(Claude, OpenAI, Whisper local). **Nenhuma chamada direta a provedor no código
de negócio.**

Ordem: transcrição de áudio → resumo de conversa → extração de pendências,
valores e prazos → busca semântica → sugestão de resposta.

**Regra que não se quebra:** a IA **nunca envia** mensagem sozinha. Ela redige;
uma pessoa aprova.

## 13. Permissões e multitenancy

Hoje o Fiscale é **mono-escritório**. O Elo já nasce com `tenant_id` em toda
tabela — o custo é baixo agora e proibitivo depois.

Papéis: `admin`, `fiscal`, `contabil`, `dp`, `atendimento`, `cliente`.
Autorização **sempre no backend**. Perfil `cliente` só enxerga a própria conversa.

## 14. Riscos

| Risco | Grav. | Mitigação |
|---|---|---|
| Migrar sem exportar histórico | 🔴 | Fase 0 — irreversível |
| Sessão em memória com 15 usuários | 🔴 | persistir antes da Fase 1 |
| Certificado no mesmo servidor da conversa | 🔴 | separação física; certificado nunca vai ao VPS |
| Vazamento entre tenants | 🔴 | `tenant_id` desde o primeiro dia + teste automatizado |
| VPS fora por mais de 7 dias | 🟡 | monitoramento; a Meta retenta nesse prazo |
| IA errar classificação de documento | 🟡 | nível de confiança; nunca importar com dúvida |
| Custo da Meta acima do previsto | 🟡 | medir um mês antes de abrir a todos |

## 15. O conflito E2EE × IA × histórico

**Os três não coexistem.** Se o servidor lê para transcrever, resumir e buscar,
não há criptografia ponta a ponta. Se há E2EE real, o servidor guarda bytes que
não consegue abrir — e a lixeira devolve conteúdo ilegível.

**Decisão proposta:** **sem E2EE**, com TLS em trânsito e cifra em repouso,
declarado nos termos com todas as letras. Motivo: o valor do Elo está na
inteligência sobre a conversa e no histórico — E2EE elimina os dois.

E há um dado que encerra a discussão: **o WhatsApp Cloud API já descriptografa
no servidor da Meta**. E2EE do nosso lado seria teatro.

**Nunca apresentar TLS como se fosse E2EE.**

Sobre "ver mensagem original": só é defensável como **política declarada da
plataforma**, avisada a quem escreve — nunca como poder discreto de alguns.

## 16. Estrutura de pastas

```
fiscale/                     ← intocado
  nfse/backend/…
  web/…
  nfse/backend/elo_ponte.py  ← ÚNICO arquivo novo aqui

elo/                         ← projeto novo, repositório próprio
  api/        rotas, websocket, autenticação
  dominio/    conversa, mensagem, tarefa, pendencia
  infra/      postgres, redis, storage, whatsapp, ia
  web/        interface (mesma linguagem visual do Fiscale)
  migrations/
  testes/
  docker-compose.yml
```

## 17 a 19. Tabelas, APIs e serviços

Tabelas: as 7 do schema + as listadas no item 8.
APIs: `/api/elo/conversas`, `/mensagens`, `/midia`, `/tarefas`, `/pendencias`,
`/ia/*`, `/webhook/whatsapp`, `/auth/*`.

**Serviços externos e custo mensal estimado:**

| Serviço | Para quê | Custo |
|---|---|---|
| WhatsApp Cloud API | canal | por conversa |
| VPS 2 GB | tudo | R$ 20–40 |
| Domínio + TLS | webhook precisa de HTTPS | R$ 40/ano |
| Provedor de IA | transcrição e resumo | por uso |
| TURN (só se houver chamadas) | WebRTC | R$ 0–50 |

---

## 20. Roadmap

### ELO MVP — o que resolve o problema de hoje
Banco · autenticação compartilhada · webhook · conversas · realtime · mídia ·
áudio com player · caixa compartilhada com atribuição · **histórico de apagadas** ·
painel do cliente com dados do Fiscale.

### ELO PRO — produtividade
Transcrição · resumo de conversa · busca semântica · tarefas · lembretes ·
aguardando resposta · agendamento · tags e pastas · "Enquanto você estava fora" ·
central de áudios · lixeira com retenção.

### ELO BUSINESS — atendimento
Departamentos · SLA · transferência · relatórios · grupos · status/stories ·
múltiplos dispositivos · PWA e offline.

### INTEGRAÇÃO AVANÇADA
Classificação de documento recebido com nível de confiança · pendências
compartilhadas nos dois sentidos · Fiscale sugerindo mensagem · perfil 360º do
cliente · chamadas WebRTC.

**Sequência obrigatória:** Fase 0 (exportar, VPS, conta Meta) → MVP → PRO →
BUSINESS. Não pular.

---

## O que eu recomendo mudar no seu pedido

**Apenas o MVP tem prazo previsível.** PRO, BUSINESS e integração avançada
somados são mais de um ano de trabalho. Prefiro entregar o MVP inteiro e
funcionando a começar as três frentes e não terminar nenhuma.

**E deixe as chamadas WebRTC para o fim.** É o item de maior custo e menor uso
num escritório de contabilidade — cliente liga no telefone.
