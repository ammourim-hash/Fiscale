# Fiscale Elo — central de conversas

Desenho do módulo de atendimento por WhatsApp. **Nada foi construído ainda** —
este documento existe para você decidir antes de escrever a primeira linha.

Escrito em **06/08/2026**.

---

## Nome e símbolo

**Fiscale Elo** — *central de conversas*.

"Elo" é o anel que fecha a corrente: curto, corporativo, e diz o que o módulo
faz — liga o escritório ao cliente sem passar pelo número pessoal de ninguém.

**Símbolo:** dois elos de corrente entrelaçados, cujo espaço negativo forma um
balão de conversa. Petróleo `#10444E` atrás, ciano `#7FD1DE` na frente — a mesma
dupla do F do Fiscale, para os dois ícones se reconhecerem como família.

---

## As três perguntas, respondidas

### 1. Migrando o número atual, dá para resgatar o histórico?

**Não automaticamente.** Quando o número migra para a Cloud API, a conversa do
aplicativo **não vai junto**: a API começa vazia. O histórico do app fica no
celular e no backup do Google Drive, que é cifrado e não se lê por programa.

**O que dá para fazer:** o WhatsApp exporta conversa por conversa
(*Conversa → Mais → Exportar conversa*), gerando `.txt` com as mensagens e uma
pasta de mídias. O Elo pode **importar esses arquivos** como arquivo
somente-leitura, ficando na mesma linha do tempo das mensagens novas.

É manual, uma conversa por vez. Para 15 ou 20 clientes importantes, vale. Para
centenas, não.

> **Faça isso ANTES de migrar.** Depois que o número vai para a API, o app
> daquele número para de funcionar e a exportação deixa de ser possível.

### 2. Se a máquina estiver desligada, a mensagem se perde?

**Não de imediato — mas o atendimento se perde.**

A Meta reenvia o webhook com espera crescente por **até 7 dias**; depois
descarta. Não há API de replay nem fila de reprocessamento
([Meta for Developers](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview)).

Na prática isso significa:

- mensagem de sábado com a máquina desligada chega **segunda**, em rajada;
- o cliente esperou o fim de semana inteiro sem resposta;
- feriado prolongado + férias pode passar dos 7 dias — e aí some de vez.

O dado sobrevive; o atendimento não. Para um canal de atendimento, **isso é a
falha**, mesmo sem perda de dado.

### 3. Alternativa para máquina que não fica ligada

Quatro caminhos, do melhor ao pior para o seu caso:

| Caminho | Custo/mês | Fica ligado | Observação |
|---|---|---|---|
| **VPS pequeno** | R$ 20–40 | sempre | 1 vCPU / 2 GB dá conta de 15 atendentes |
| **Raspberry Pi no escritório** | ~R$ 5 de luz | sempre | 5 W, cabe atrás do monitor; depende da luz e da internet do escritório |
| **BSP (360dialog, Twilio)** | R$ 100+ | sempre | eles guardam e você consulta; some a preocupação, sobe o custo |
| Máquina do escritório | zero | **não** | só serve se alguém garantir que fica ligada |

**Recomendo o VPS.** É o único que resolve sem depender de disciplina humana, e
R$ 30/mês some perto do custo de perder um cliente por demora.

**O desenho híbrido que eu faria:**

```
   Cliente  →  Meta Cloud API  →  VPS (recebe e guarda, 24h)
                                        ↓
                          Fiscale local lê e responde
                          (quando a máquina liga)
```

O VPS é só **caixa postal**: recebe o webhook, grava, nunca perde. Os dados
fiscais e os certificados **continuam na máquina do escritório** — o VPS só vê
mensagem, nunca certificado.

---

## Arquitetura

### Componentes

```
┌─────────────────────────────────────────────────────────┐
│  VPS (sempre ligado)                                    │
│                                                         │
│  receptor.py    ← webhook da Meta, grava CRU primeiro   │
│  PostgreSQL     ← conversas, mensagens, mídias          │
│  api.py         ← REST + WebSocket para os atendentes   │
│  storage/       ← áudios, imagens, documentos           │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTPS + WebSocket
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   atendente 1     atendente 2  …  atendente 15
   (navegador)     (navegador)      (navegador)
```

**Regra de ouro do receptor:** gravar o payload cru **antes** de qualquer
processamento. Se o código de interpretação falhar, a mensagem já está salva e
dá para reprocessar. Sem isso, um bug no parser vira mensagem perdida.

### Por que PostgreSQL aqui, se eu disse que não no Fiscale

Porque o problema é outro. Nos módulos fiscais há **um usuário** e arquivos JSON
bastam. Aqui há **15 pessoas escrevendo ao mesmo tempo** — dois atendentes
salvando juntos num JSON e uma conversa desaparece. Não é preferência: é
integridade.

O PostgreSQL já está instalado nesta máquina, e no VPS é um `apt install`.

### Banco de dados

```
conversa
  id, cliente_id (liga ao Fiscale), telefone, nome_exibicao,
  atendente_id, status (aberta|aguardando|resolvida),
  ultima_mensagem_em, nao_lidas, fixada

mensagem
  id, conversa_id, wa_id (id da Meta), direcao (entrada|saida),
  autor_id, tipo (texto|audio|imagem|documento|sistema),
  texto, midia_id, respondendo_a,
  enviada_em, entregue_em, lida_em,
  apagada_em, apagada_por        ← o conteúdo PERMANECE
  payload_cru (jsonb)            ← rede de segurança

midia
  id, mensagem_id, tipo_mime, tamanho, duracao_seg,
  caminho, hash_sha256, transcricao (opcional)

atendente
  id, nome, email, senha_hash, papel (admin|atendente), ativo

nota_interna
  id, conversa_id, autor_id, texto, criada_em
  (visível só para a equipe — o cliente nunca vê)
```

**Mensagem apagada:** a linha ganha `apagada_em` e `apagada_por`; o texto e a
mídia **ficam**. A tela mostra riscado, com etiqueta *"apagada pelo remetente"*.
É arquivamento, não interceptação.

---

## Telas

**Três colunas**, o desenho que atendimento usa há década — e funciona:

```
┌──────────────┬────────────────────────┬──────────────────┐
│ CONVERSAS    │  MENSAGENS             │  O CLIENTE       │
│              │                        │                  │
│ ⬤ Monte      │  ┌──────────────┐      │ MONTE ASSESSORIA │
│   há 2 min   │  │ cliente      │      │ Simples Nacional │
│              │  └──────────────┘      │                  │
│ ⬤ Transportes Beta   │       ┌────────────┐   │ Prévia do DAS    │
│   há 1 h     │       │ atendente  │   │ R$ 8.972,55      │
│              │       └────────────┘   │                  │
│ ○ Ribas      │  ┌──────────────┐      │ ⚠ certificado    │
│   ontem      │  │ ~~apagada~~  │      │   vence em 43d   │
│              │  └──────────────┘      │                  │
│ [filtros]    │  [ escrever… ] [🎤]    │ [ver no Fiscale] │
└──────────────┴────────────────────────┴──────────────────┘
```

A **terceira coluna é o diferencial**. Nenhum concorrente tem: enquanto você
conversa, vê o regime, a prévia do DAS, as pendências e o vencimento do
certificado daquele cliente. Sem isso, o Elo seria só mais um chat.

Visual: a mesma linguagem de balão e sobreposição do Fiscale — cantos de 24px,
barra superior flutuante, cores petróleo e ciano.

---

## Restrições que mudam a rotina

**1. O número sai do celular.** Migrado para a API, aquele número **não abre
mais no app**. Não há volta fácil.

**2. Janela de 24 horas.** Passadas 24h da última mensagem do cliente, só dá
para iniciar conversa com **modelo aprovado** pela Meta. Cobrança e aviso de
vencimento precisam de modelo cadastrado com antecedência.

**3. Custo por conversa.** A Meta cobra por conversa/mensagem. Some ao VPS.

**4. Verificação da empresa.** A Meta exige verificar o negócio — CNPJ,
comprovante, prazo de dias.

---

## Fases

### Fase 1 — Receber e responder
Webhook, banco, caixa compartilhada, atribuir conversa, histórico com marcação
de apagadas, áudio e imagem. **É 80% do valor.**

### Fase 2 — Ligar ao Fiscale
A terceira coluna: cliente, regime, prévia do DAS, pendências, vencimentos.
É o que faz valer ser um módulo do Fiscale e não um app avulso.

### Fase 3 — Interno e automações
Conversa entre a equipe, nota interna na conversa do cliente, aviso automático
de vencimento de certificado, lembrete de documento.

### Fase 0 — Antes de tudo (você, não eu)
1. Exportar as conversas que interessam **enquanto o número ainda está no app**
2. Decidir: migrar o número atual ou usar um novo
3. Contratar o VPS
4. Abrir conta na Meta e verificar a empresa
5. Avisar nos termos de atendimento que as conversas são gravadas

---

## Riscos

| Risco | Gravidade | O que fazer |
|---|---|---|
| Migrar sem exportar o histórico | 🔴 alta | Fase 0, passo 1 — **irreversível** |
| VPS cai e passa dos 7 dias | 🟡 média | monitorar; a Meta reenvia nesse prazo |
| Bug no parser perder mensagem | 🟡 média | gravar payload cru antes de processar |
| Custo da Meta acima do previsto | 🟡 média | medir um mês antes de abrir para todos |
| Biblioteca não oficial (Baileys) | 🔴 alta | **não usar** — derruba o número |
| Dado de cliente em servidor | 🟡 média | VPS só vê mensagem; certificado fica local |

---

## O que eu recomendo

**Fase 1, com VPS, número novo.**

Número novo em vez de migrar porque: você não perde o app no número atual, não
corre o risco de perder o histórico, e dá para rodar os dois em paralelo até
confiar no Elo. Migrar o principal depois, com calma, se quiser.

Se ainda assim quiser migrar o atual, **exporte tudo antes** — não tem segunda
chance.
