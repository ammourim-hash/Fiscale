# Plano de Saúde — decisões

As decisões de projeto do módulo `saude/`. As decisões fiscais que atravessam o
Fiscale inteiro estão em `FISCALE_DECISOES_FISCAIS.md` (D1–D24); aqui ficam as
que só dizem respeito a este módulo, com a evidência que as sustenta.

---

## Onde o módulo vive

```
nfse/backend/saude/
    __init__.py        fachada
    modelo.py          Beneficiario · Cobranca · Familia · Extrato · Conferencia
    texto.py           PDF/CSV/TXT → texto
    deteccao.py        qual operadora é esta
    armazenamento.py   índice JSON + arquivos originais
    servico.py         detectar → parsear → conferir → guardar
    parsers/
        base.py        o contrato
        sulamerica.py  amostra real
        bradesco.py    amostra real
    amostras/          fixtures SANITIZADAS
```

Segue o padrão que já funciona no Fiscale: `.py` no backend + rota FastAPI no
`main.py` + prefixo em `NFSE_PREFIXOS` (`fiscale_server.py`) + tela em `web/`.
**Nenhum framework novo.** Sem ORM, sem camada de repositório, sem injeção de
dependência — o projeto é stdlib + FastAPI e continua assim.

`healthplans/models/parsers/importers/validation/storage/services` foi
descartado: seis pastas para um módulo que cabe em sete arquivos é estrutura
sem trabalho para fazer.

---

## Persistência

```
<DADOS>/<cnpj>/saude/
    extratos.json                  índice
    originais/<hash8>-<nome>.pdf   como veio, nunca alterado
```

`<DADOS>` é `~\SistemaNFSe\dados` — o mesmo do módulo NFS-e. Gravação do índice
por arquivo temporário + `replace()` (troca atômica): nunca fica um índice pela
metade se faltar energia no meio.

---

## Estados

`IMPORTADO` → `LIDO` → `CONFERIDO` | `CONFERIR` | `ERRO`

- **CONFERIDO** — a soma das vidas bate com os totais que o relatório declara
- **CONFERIR** — há diferença acima de R$ 0,02, **ou** o CNPJ do relatório
  diverge da empresa em que foi importado
- **ERRO** — não deu para ler. O original fica guardado e o extrato entra no
  índice mesmo assim, para poder ser reprocessado depois

Não existe `VALIDATED` separado de `CONFERIDO`: no domínio de fatura, validar
**é** conferir contra os totais declarados. Dois estados para a mesma coisa só
gerariam dúvida sobre qual olhar.

---

## Por que não reaproveitar o `state_plano.json`

Ver D7. Em resumo: é estado de tela, guarda `float`, não tem noção de empresa,
competência nem procedência de arquivo, e mistura dado real com dado de
demonstração. As abas antigas continuam usando — intocadas.

---

## Identidade

`Beneficiario.chave_tecnica()`, nesta ordem:

| Origem | Chave | Quando |
|---|---|---|
| CPF válido | `cpf:<11 díg.>` | SulAmérica |
| Código/certificado | `cod:<código>` | Bradesco (não traz CPF) |
| Matrícula + nome | `mat:<mat>\|<nome>` | fallback |
| CPF com DV inválido | `cpf?:<díg.>` | ainda identifica, mas marcado |
| Nome | `nome:<canônico>` | último recurso |

`origem_chave` guarda qual foi usada, para a tela poder avisar quando a
identidade é frágil.

---

## Conferência

A regra é **do parser** (D12) e vai em texto para a tela. Cada divergência
carrega quatro coisas — `declarado`, `identificado`, `diferenca`, `contexto` —
porque é isso que a tela precisa mostrar quando não fecha:

```
VALOR DO RELATÓRIO     R$ 2.714,16
VALOR IDENTIFICADO     R$ 1.809,44
DIFERENÇA              R$   904,72     CONFERIR
```

Divergência com diferença dentro da tolerância entra na lista assim mesmo,
marcada com ✓. É de propósito: ver que o número **foi conferido e bate** vale
mais do que não ver nada.

---

## Privacidade

O modelo tem: nome, CPF, código, matrícula, plano, parentesco, nascimento,
início de vigência e as parcelas de cobrança.

Não tem — e não vai ter — diagnóstico, CID, procedimento, guia, carência ou
internação. **Nem sexo, nem estado civil**, que os dois relatórios trazem e a
cobrança não usa. Há teste que falha se um campo desses aparecer.

Nascimento e início de vigência ficam porque a operadora reajusta por faixa
etária e por tempo de contrato — sem eles não dá para conferir reajuste.

---

## D25 — a tela mostra o que se confere, e esconde o resto

*Decidido em 11/08/2026. Ainda não implementado — ver HEALTH 2b no roadmap.*

A tela chegou a 10 abas e a listagens com uma dúzia de colunas. Para quem só
precisa conferir a fatura do mês, isso atrapalha: informação demais na frente
esconde a informação que importa.

**Fica visível na tela principal** — é o que responde "esta fatura fecha?":

```
competência · empresa · operadora
titular · dependentes
valor individual · mensalidade/prêmio · coparticipação · outros ajustes
total da família · total da fatura
diferença · status da conferência
```

**Vai para "Detalhes do beneficiário"** — continua sendo **guardado**, porque
é o que dá identidade e histórico, mas não ocupa a tela principal:

```
nascimento · idade · código de identificação · matrícula · ID funcional
início de vigência · plano (código) · avisos técnicos · origem da chave
```

Isto **não** é "capturar menos". O parser continua extraindo tudo — a chave
técnica depende do código/matrícula (D10) e o histórico depende deles. O que
muda é só onde aparece.

**Consequência para as abas:** ficam as 5 do importador (Competências ·
Importar · Beneficiários · Conferências · Histórico). Operadoras vira um bloco
dentro de Importar, com o roteiro do portal — que é a parte com valor
operacional real. Clientes, Lançamentos, Faturas e e-CAC saem: as três
primeiras faziam à mão o que o importador passou a fazer sozinho, e a última
era um `alert()`. Os dados que existem hoje nelas são preservados antes.

---

## D26 — automatizar a captura, sem burlar nada

*Decidido em 11/08/2026. Ainda não implementado — ver HEALTH 3 no roadmap.*

A importação manual é a **primeira fase**, não o destino. O objetivo é abrir
uma competência e encontrar os relatórios já lá.

**Três níveis, nesta ordem:**

1. **Importação manual em lote** — entregue. Continua existindo para sempre,
   como caminho normal e como reserva de quando um conector falhar.
2. **Conectores por operadora**, preferindo nesta ordem: **API oficial** →
   **endpoint oficial** → **exportação autorizada do portal**. Scraping de tela
   **não é a primeira opção**, e só entra com decisão explícita, por operadora.
3. **Captura automática por competência**, com painel de situação.

**O que o Fiscale não vai fazer, e isto não é negociável:**

- não burlar CAPTCHA;
- não burlar MFA ou segundo fator;
- não contornar mecanismo de segurança de portal.

**Quando o portal exigir uma pessoa, ele para e pede.** "Ação necessária para
autenticar", o usuário conclui o acesso, e o conector segue de onde parou.
Fluxo assistido, não fluxo furtivo.

**Por que a ordem de preferência importa mais do que parece:** um scraper
quebra em silêncio. O portal muda um rótulo, o robô lê a coluna errada, e o
número entra na conferência com cara de certo. Numa conferência de fatura, isso
é pior do que não ter automação — porque o erro só aparece depois de o valor
ter sido usado. API oficial quebra com erro; tela muda sem avisar.

**Pré-requisito já resolvido:** guardar credencial de portal com segurança era
condição para qualquer conector. Foi feito na PORT 3 (D12) — e é por isso que
precisou vir antes.

---

## O que a tela ganhou

Cinco abas novas, ligadas ao backend real:

**Competências** · **Importar relatórios** · **Beneficiários** · **Conferências** · **Histórico**

As cinco antigas (Clientes, Operadoras, Faturas, Lançamentos, e-CAC) continuam
exatamente como estavam, no estado local de tela. Não foram tocadas — inclusive
a aba e-CAC, que segue inerte até o REINF existir.

Sem dashboard: a tela responde "esta competência fecha?" e "quanto é de cada
um?". Gráfico não responde nenhuma das duas.
