# Plano de Saúde — roadmap

Atualizado em **11/08/2026**.

| Fase | O quê | Estado |
|---|---|---|
| **HEALTH 1** | importação e normalização | ✅ entregue em 11/08/2026 |
| **HEALTH 2** | movimentação e comparação entre competências | ⬜ |
| **HEALTH 2b** | enxugar a tela | ⬜ decidido, não implementado |
| **HEALTH 3** | **conectores e captura automática de operadoras** | ⬜ registrado, não implementado |
| **HEALTH 4** | qualidade do cadastro | ⬜ |

> A **PORT 4** (distribuição portátil) é fase de outra trilha e segue
> **exclusivamente** focada na portabilidade do Fiscale. Nada de plano de saúde
> entra nela.

---

## HEALTH 1 — importação e normalização ✅ ENTREGUE

| Entrega | Estado |
|---|---|
| Módulo backend `nfse/backend/saude/` | ✅ |
| Modelo com `Decimal` e cobrança decomposta | ✅ |
| Parser SulAmérica (amostra real) | ✅ |
| Parser Bradesco (amostra real) | ✅ |
| Detecção de operadora por conteúdo | ✅ |
| Conferência declarada pelo parser | ✅ |
| Arquivo original preservado + hash | ✅ |
| Anti-duplicidade | ✅ |
| Reprocessamento com histórico e `parserVersao` | ✅ |
| Importação em lote tolerante a falha | ✅ |
| Titular × dependentes com parentesco | ✅ |
| Histórico por competência | ✅ |
| 9 rotas `/api/saude/*` | ✅ |
| Tela: Competências · Importar · Beneficiários · Conferências · Histórico | ✅ |
| 144 asserções em `teste_saude.py` | ✅ |

**Fora do escopo, de propósito:** REINF, XML, assinatura, transmissão,
automação de portal, Selenium/Playwright, scraping, parser sem amostra real.

---

## HEALTH 2 — movimentação e comparação (próxima)

1. **Comparar duas competências** — entrada, saída, reajuste, dependente novo,
   troca de plano, mudança de titular. A base existe (chave técnica estável +
   histórico); falta a camada de diferença e a tela.
2. **Fechar a dúvida da coparticipação do Bradesco** — depende de uma fatura com
   `Part. Seg.` preenchida. É a única incerteza conhecida do HEALTH 1.
3. **Exportar a competência conferida** (CSV/XLSX) — o que o contador pede hoje;
   o que a camada REINF consome amanhã.
4. **Terceira operadora** — a que aparecer amostra primeiro. Hapvida, Unimed,
   Amil e NotreDame já aparecem na tela como "aguardando amostra".
5. **Avaliar SQLite** — só se o volume real pressionar. O JSON por empresa dá
   conta do que existe hoje.

---

## HEALTH 2b — enxugar a tela  *(decidido em 11/08/2026, não implementado)*

A tela ficou com 10 abas e informação demais para quem só quer conferir a
fatura do mês. Ver **D25** em `SAUDE_DECISOES.md` para o que fica visível e o
que vai para "Detalhes do beneficiário".

Em uma linha: **5 abas** (Competências · Importar · Beneficiários ·
Conferências · Histórico); Operadoras vira um bloco dentro de Importar, só com
o roteiro do portal; Clientes, Lançamentos, Faturas e e-CAC saem, com os dados
existentes preservados antes.

---

## HEALTH 3 — conectores e captura automática  *(registrado, não implementado)*

O objetivo final é **não precisar baixar e importar relatório todo mês**. Isso
se constrói em três níveis, e a ordem importa: cada nível só existe porque o
anterior já funciona e serve de rede.

### Nível 1 — importação manual em lote  ✅ *entregue no HEALTH 1*

Arrastar os PDF, detecção da operadora por conteúdo, conferência, histórico.
**Continua existindo para sempre**, como caminho normal e como reserva de
quando um conector falhar.

### Nível 2 — conectores por operadora

Um conector por operadora, em ordem de preferência **inegociável**:

| Ordem | Caminho | Por quê |
|---|---|---|
| 1º | **API oficial** | contrato estável, autenticação prevista, quebra com aviso |
| 2º | **Endpoint oficial** de exportação | idem, ainda que sem documentação pública |
| 3º | **Exportação autorizada do portal** | o próprio portal oferece "baixar relatório"; automatiza-se aquilo, não a navegação |
| — | ~~scraping de tela~~ | **não é a primeira opção**, e só entra com decisão explícita e por operadora |

Operadoras previstas: **Bradesco**, **SulAmérica**, **Hapvida**, **Unimed**, e
as demais conforme aparecerem.

**Autenticação — o que o Fiscale não faz:**

- não burla CAPTCHA;
- não burla MFA / segundo fator;
- não contorna mecanismo de segurança nenhum.

Quando o portal exigir uma pessoa, o conector **para e pede**:

> **Ação necessária para autenticar** — entre no portal e conclua o acesso.
> O Fiscale continua daqui quando você terminar.

E continua de onde parou. Fluxo assistido, não fluxo furtivo.

### Nível 3 — captura automática por competência

```
operadora → autenticação autorizada → relatório → parser
          → normalização → conferência → histórico
```

Do "relatório" em diante é o HEALTH 1, que já existe e já é testado. O nível 3
só acrescenta o gatilho por competência e o painel de situação.

### A experiência que se quer

Abrir **Planos de Saúde → 08/2026** e ver:

```
Bradesco ......... Capturado automaticamente
SulAmérica ....... Capturado automaticamente
Hapvida .......... Aguardando autenticação
Unimed ........... Conferir
```

Sem baixar e importar um por um, todo mês.

### Pré-requisitos

- HEALTH 2 fechado (movimentação entre competências);
- parser com amostra real de cada operadora que ganhar conector (D14);
- credencial de portal protegida — já resolvido na PORT 3 (D12), e é por isso
  que ela precisou vir antes.

**Não implementar agora. Não misturar com a PORT 4**, que é exclusivamente a
portabilidade do Fiscale.

---

## HEALTH 4 — qualidade do cadastro

- Beneficiário sem CPF (todo o Bradesco): casar com o cadastro de Clientes por
  nome + nascimento, **com confirmação humana** — nunca automático.
- Painel de pendências: CPF ausente, CPF com DV inválido, família sem titular.
- Conferência entre operadoras para a mesma pessoa.

---

## Depois — a ponte para o REINF

O HealthPlan **não** conhece REINF (D6). Quando chegar a hora:

```
saude/  →  competência conferida  →  camada REINF  →  evento aplicável
```

A camada REINF lê o modelo normalizado. Se o leiaute da Receita mudar, muda a
camada — não o parser da operadora. Se a operadora mudar o relatório, muda o
parser — não o gerador de evento.

**Pré-requisito não negociável:** só entra na camada REINF competência com
status `CONFERIDO`. Enviar evento a partir de número que não fecha é
transformar um erro de leitura em obrigação acessória entregue errada.

---

## Débitos do HEALTH 1

| # | Débito | Grav. |
|---|---|---|
| 1 | Coparticipação do Bradesco: falta amostra que decida se entra no total técnico | 🟡 |
| 2 | Só PDF com camada de texto. Relatório digitalizado dá erro explicativo, sem OCR | 🟢 |
| 3 | Sem parser para XLSX/CSV — nenhuma das duas operadoras exporta assim | 🟢 |
| 4 | Bradesco sem CPF: a identidade depende do certificado. Se a operadora renumerar, o histórico quebra | 🟡 |
| 5 | A tela não deixa corrigir manualmente um valor lido errado (por opção: o original é a prova; o caminho é corrigir o parser e reprocessar) | 🟢 |
| 6 | `listar_competencias` soma extratos de operadoras diferentes na mesma competência sem avisar de sobreposição | 🟢 |
