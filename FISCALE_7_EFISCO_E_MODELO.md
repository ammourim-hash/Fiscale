# FASE 7 — aquisição pelo e-Fisco PE e modelo de negócio

**Status:** análise. Nada implementado, nada cobrado, nada bloqueado.
**Baseline na abertura:** 26 suítes · 3.488 asserções · 0 falhas.

---

# PARTE I — A FONTE REAL DOS DOCUMENTOS

## 1. Mapa real de como buscamos NF-e/NFC-e hoje

O fluxo operacional do escritório, como ele é:

```
e-Fisco (ARE Virtual) → consultar por IE e período → baixar XML
        → pasta local → FISCALE → (depois) Domínio
```

Passo a passo, com o que já está no sistema:

| # | passo | quem faz hoje | onde no FISCALE |
|---|---|---|---|
| 1 | escolher a empresa e o período | humano | aba **NF-e Emitente** |
| 2 | obter a **Inscrição Estadual** do cliente | **automático** | módulo Clientes → `v_dados`, IE copiada |
| 3 | abrir o e-Fisco | **semiautomático** | botão que abre o portal com a IE na área de transferência |
| 4 | autenticar (certificado da empresa mãe ou gov.br) | humano | — |
| 5 | navegar até *Documentos Fiscais Eletrônicos → Download de NF-e / NFC-e* | humano | — |
| 6 | preencher filtros e consultar | humano | — |
| 7 | baixar os XML | humano | — |
| 8 | levar os XML ao FISCALE | **automático desde a 6B/6D** | pasta vigiada → `importar_xmls` → portão |
| 9 | acervo, índice, normalização, `vendas.py`, rastreabilidade | **automático** | 6B/6C/6D |

**Onde a premissa da 6D estava errada.** Eu tratei "o ERP do cliente" como a
fonte natural e a pasta do emissor como o caminho normal. No escritório não é
assim: o cliente não manda XML, e a fonte é o **portal estadual**. A camada de
fontes da 6D não se perde com isso — ela continua sendo o encaixe certo —, mas
a fonte que importa se chama `EFISCO_PE`, não `PastaEmissor` do ERP.

Corrigido: `PastaEmissor` passa a ser o **destino do download**, não a saída do
ERP. É a mesma classe; muda o que ela significa no fluxo.

---

## 2. O que pode ser automatizado por meio oficial

Investiguei os mecanismos oficiais na ordem de prioridade que você definiu.

### 2.1 Webservice/API oficial — **não existe para emissão própria**

| serviço | situação | serve para emitidas? |
|---|---|---|
| `NFeDownloadNF` | **desativado em 01/06/2017** | não existe mais |
| `NFeDistribuicaoDFe` | ativo — é o nosso ING | **não**: entrega o que a empresa não gerou (D2) |
| `NFeConsultaProtocolo` (`consChNFe`) | ativo | **não**: para o emitente devolve situação e protocolo, não o XML completo |
| API do e-Fisco para DF-e | **não documentada** | — |

A regra por trás disso é coerente, e vale entendê-la para não procurar o que
não há: **o Fisco presume que o emitente já tem o próprio XML**, porque foi ele
quem o gerou e a legislação o obriga a guardá-lo. Nenhum serviço nacional
existe para devolvê-lo. O buraco não é técnico, é de premissa: quem apura sem
ser quem emitiu está fora do desenho original.

O e-Fisco passou a autenticar por **certificado digital ou conta gov.br**, e
nenhuma página oficial de download de NF-e ou NFC-e menciona API, webservice
ou integração automatizada.

### 2.2 Endpoint oficial autorizado — **não localizado**

Não encontrei publicação da SEFAZ-PE oferecendo endpoint de download de DF-e
para sistemas. As APIs que aparecem em busca são de **outros estados** (DARE
em SP), de **serviços privados** ou do **portal nacional de NFS-e** — nenhuma
resolve NF-e/NFC-e emitidas em PE.

**Não vou tratar como API algo observado no navegador.** Um XHR interno do
portal não é contrato público: pode mudar sem aviso, costuma depender de sessão
e, usado por fora, é uso não previsto de sistema de governo.

### 2.3 Integração suportada pelo portal — **o que dá para fazer**

O portal **suporta download de arquivo** e é aí que a automação legítima
começa: **tudo depois do download já é nosso e já é automático**.

### 2.4 Fluxo assistido — **o nível realmente disponível hoje**

É o quarto nível da sua prioridade, e é onde estamos. A meta então muda de
"automatizar a aquisição" para **"reduzir a intervenção humana ao mínimo
irredutível"**.

**O mínimo irredutível são três ações:** autenticar, consultar, baixar.
Todo o resto pode sair do humano.

---

## 3. O que ainda exige participação do usuário

| exige humano | por quê | dá para reduzir? |
|---|---|---|
| autenticação (certificado / gov.br / MFA) | é o mecanismo de segurança | **não** — e não se deve tentar |
| navegar até o serviço de download | portal sem API | parcialmente: link direto quando existir |
| preencher filtros e consultar | idem | parcialmente: dados prontos para colar |
| clicar em baixar | idem | não |
| **conferir o resultado** | — | **já é automático** |
| **arquivar, deduplicar, indexar, classificar** | — | **já é automático** |

O que o FISCALE pode assumir sem cruzar nenhuma linha:

1. **Dizer o que falta.** Por empresa e competência: quais períodos nunca foram
   baixados, quais têm buraco de numeração. Hoje o contador decide de memória.
2. **Preparar a consulta.** IE, CNPJ, período, tudo pronto — já faz para a IE.
3. **Vigiar o destino do download.** A pasta de Downloads é a fonte real; o
   vigia da 6D já existe e já termina no portão. Falta ligá-lo.
4. **Fechar o ciclo sozinho.** Do arquivo salvo até `vendas.py`, sem clique.
5. **Confirmar.** "Baixou 84 notas de 07/2026; faltam os números 41 e 42."

Isso tira o humano de **seis** dos nove passos do mapa e o deixa nos três que
são, por desenho, dele.

---

## 4. Arquitetura da fonte `EFISCO_PE`

Ela **não** é um robô de navegador. É uma fonte que sabe três coisas: o que
falta buscar, como preparar a consulta, e onde o arquivo vai cair.

```
                        ┌─────────────────────────────┐
  o que falta ────────→ │  EFISCO_PE                  │
  (índice + acervo)     │  · lacunas por competência  │
                        │  · IE, CNPJ, período        │
                        │  · pasta de destino vigiada │
                        └──────────────┬──────────────┘
                                       │ prepara
                        ┌──────────────▼──────────────┐
                        │  HUMANO no e-Fisco          │
                        │  autentica · consulta · baixa│
                        └──────────────┬──────────────┘
                                       │ arquivo cai na pasta
                        ┌──────────────▼──────────────┐
                        │  PastaEmissor (6D)          │
                        └──────────────┬──────────────┘
                                       ▼
                    importacao.importar()  ← O PORTÃO
                                       ▼
                    acervo → índice → normalizacao → vendas → rastreio
```

**Três componentes, e nenhum deles fala com a SEFAZ:**

- **`LacunasDeAquisicao`** — lê acervo e índice e responde "o que falta". Não
  baixa nada. É a peça que hoje não existe e que mais reduz trabalho humano.
- **`RoteiroEfisco`** — monta o roteiro da consulta: empresa, IE, período,
  caminho no portal, pasta de destino. Dado puro, sem efeito colateral.
- **`PastaEmissor`** (já existe) — vigia a pasta de destino.

**O que essa fonte deliberadamente não tem:** navegador embutido, sessão,
cookie, credencial, resolução de CAPTCHA, retentativa contra o portal. Se um
dia aparecer endpoint oficial documentado, ele entra como um quarto componente
e o resto não muda — é exatamente para isso que a camada de fontes existe.

---

## 5. Como conversa com o portão único

Não conversa por caminho novo. Conversa pelo mesmo de sempre:

```
EFISCO_PE (prepara) → humano baixa → PastaEmissor → importacao.importar()
```

`fontes_emissao.coletar()` continua com quatro linhas úteis. Tudo que a fonte
nova acrescenta acontece **antes** do download; depois dele, o caminho é o que
já está testado com 3.488 asserções.

Preservado sem exceção: `NFE55` e `NFCE65` distintas, XML original imutável,
dedup por identidade e hash, eventos pelo mesmo portão, acervo = índice,
checkpoint da distribuição **independente e intocado**, importação manual da 6C
como fallback.

**O ING não é tocado.** `NFeDistribuicaoDFe` continua sendo o caminho das notas
de terceiros, com porta única, checkpoint, cooldown e piloto sob a governança
da ING. Emissão própria é outro problema e ganha outra fonte — misturar os dois
foi o erro que a D2 já custou caro para desfazer.

---

## 6. Riscos técnicos e de segurança

**R1 — Tentação de automatizar o navegador.** É o caminho que parece resolver
tudo. Ele quebra a cada mudança de layout, esbarra em CAPTCHA e MFA por
desenho, e usa credencial fiscal de terceiro fora do previsto. **Não faremos**,
e a arquitetura acima existe em parte para tornar essa tentação desnecessária.

**R2 — Endpoint observado tratado como API.** Um XHR do portal não é contrato.
Só entra se houver publicação oficial, com autenticação, limites e condições
documentados — e a documentação vem **antes** do código.

**R3 — Certificado da empresa mãe.** O acesso usa um certificado que vê várias
empresas. Ele continua onde está (DPAPI, `certs/`), o FISCALE não o usa para
falar com o e-Fisco, e nada disso muda nesta fase.

**R4 — Lacuna silenciosa.** Baixar "o mês" e não perceber que faltaram notas é
o erro mais provável e o mais caro: a apuração fica plausível e errada. Por
isso `LacunasDeAquisicao` vem **antes** de qualquer automação — e por isso
buraco de numeração precisa aparecer, mesmo sem provar omissão.

**R5 — NFC-e em volume.** Para varejo, cupom é o grosso da receita e o download
pode vir em milhares. A espécie `NFCE65` já existe e o acervo aguenta, mas
volume real ainda não foi medido.

**R6 — Evento de cancelamento.** A própria SEFAZ-PE avisa: para nota cancelada
é preciso baixar **os dois** arquivos, autorização e cancelamento. Se só o
primeiro vier, `sem_evento_conhecido` cobre — mas o roteiro precisa lembrar
disso, senão a receita fica inflada em silêncio.

**R7 — Mudança do portal.** Caminho de menu e rótulos mudam. Como não
dependemos deles em código, o custo é atualizar um texto de roteiro.

---

# PARTE II — MODELO DE NEGÓCIO

## 7. FISCALE Gratuito

**O programa completo, para sempre, sem assinatura.** Não é demonstração nem
versão reduzida: é o produto.

Inclui **tudo que já existe e tudo que a apuração vier a ser**: cadastro de
empresas, certificados, acervo local, importação (manual e por pasta),
conferência, apuração, auditoria, rastreabilidade, backup `.fbk`, portabilidade
e atualização do programa.

Três compromissos que valem mais escritos do que ditos:

1. **Os dados são do usuário.** XML, acervo e certificados ficam no computador
   dele. Nada exige nuvem para funcionar.
2. **Exportar é grátis, sempre.** O `.fbk` já existe e já leva tudo. Cobrar
   para levar os próprios dados embora é a definição de refém.
3. **Sem anúncio e sem venda de dados.** Dado fiscal de terceiro não é ativo
   nosso.

A razão comercial é simples: **o desktop não tem custo recorrente.** Cobrar
assinatura por algo que roda na máquina do usuário é cobrar por nada — e cria
a pressão de piorar o gratuito para justificar o pago. É assim que produto bom
apodrece.

---

## 8. Monetização sem cobrar pelo programa

O critério: **só cobrar o que tem custo recorrente real, ou trabalho humano.**

### Custo de infraestrutura (assinatura se justifica sozinha)

| serviço | por que custa | por que o usuário paga |
|---|---|---|
| **FISCALE Cloud** — acervo replicado | armazenamento e banda | acesso de qualquer máquina, sem VPN |
| **Backup e sincronização** | armazenamento versionado | perder o acervo é perder anos de XML |
| **Acesso remoto / multiusuário** | servidor gerenciado | equipe trabalhando junta |
| **Automações contínuas** | máquina rodando 24×7 | ciclo de aquisição sem ninguém no escritório |
| **Alertas e acompanhamento** | notificação, canal | prazo perdido custa multa |
| **ELO / WhatsApp** | API de mensagem por conversa | o cliente responde onde já está |
| **IA fiscal** | inferência por token | classificar CFOP e achar divergência em escala |
| **Hospedagem gerenciada** | tudo acima somado | escritório que não quer TI |

### Trabalho humano (cobra-se a hora, não o software)

Implantação e migração, treinamento, suporte prioritário com SLA, conectores
sob medida.

### Escala (quem ganha mais, paga mais)

Planos por volume de empresas. Uma empresa é grátis com todos os recursos; 300
empresas em nuvem consomem infraestrutura de verdade.

### O que **não** entra, nunca

Bloquear dados, cobrar exportação, exigir nuvem para função local, anúncio,
venda de dado fiscal, tornar XML inacessível.

---

## 9. Planos sugeridos

| | **Gratuito** | **Pro / Cloud** | **Escritório** |
|---|---|---|---|
| preço | R$ 0, para sempre | por empresa/mês | por faixa de empresas |
| instalação | local | local + nuvem | gerenciada |
| empresas | **ilimitadas** | ilimitadas | ilimitadas |
| acervo, XML, certificados | **local, do usuário** | local + réplica | idem |
| importação, conferência, apuração, auditoria | **tudo** | tudo | tudo |
| exportar/backup `.fbk` | **sim** | sim | sim |
| atualizações | **sim** | sim | sim |
| backup em nuvem | — | sim | sim |
| acesso remoto / multiusuário | — | sim | sim + perfis |
| automação contínua | — | sim | sim |
| alertas, ELO/WhatsApp | — | sim | sim |
| IA fiscal | — | cota | cota maior |
| relatórios avançados | — | sim | sim + consolidado |
| equipes e permissões | — | — | sim |
| suporte | comunidade | prioritário | SLA + gerente |
| implantação e treinamento | — | opcional | incluso |

**A linha que separa os planos é uma só:** *roda na máquina do usuário* fica no
Gratuito; *roda em máquina nossa ou consome serviço de terceiro* é pago.
Fácil de explicar, difícil de corromper.

**White-label:** só faz sentido depois de haver nuvem multiempresa estável.
Antes disso, é distração.

---

## 10. Decisões arquitetônicas a tomar agora

Nenhuma delas implementa cobrança. Todas evitam reescrita depois.

**A1 — Fronteira explícita entre núcleo local e serviço.** Já existe de fato: o
pacote `ingestao/` não conhece rede além da SEFAZ, e o ELO é outro processo.
Falta **declarar** a fronteira e travá-la com teste, como já se faz com o
portão. Sem isso, uma função de nuvem vaza para dentro do núcleo e o gratuito
começa a depender de servidor.

**A2 — Identidade de instalação, não de licença.** Um id anônimo por instalação
serve para suporte e para vincular assinatura de serviço. **Não** é chave de
ativação e nada no desktop deve consultá-lo para liberar função.

**A3 — Capacidade, não fork.** Serviço opcional se habilita por capacidade
declarada (como os `capabilities` do ELO), nunca por build separado. Duas bases
de código viram duas qualidades de produto.

**A4 — Nuvem é réplica, nunca original.** O acervo local continua sendo a
verdade. Nuvem que vira original transforma cancelamento de assinatura em perda
de dado — exatamente o que não queremos.

**A5 — Certificado nunca sai da máquina.** Vale para qualquer plano. É o
compromisso mais fácil de quebrar por conveniência e o mais caro de recuperar.

**A6 — Telemetria opcional e explícita.** Se existir, é opt-in, anônima e
documentada. Nunca conteúdo fiscal.

**A7 — Multiempresa desde já no que for para a nuvem.** O ELO já tem sessões;
o FISCALE desktop é monousuário por natureza. O que for nascer para a nuvem
nasce com locatário.

**A8 — Exportação como contrato.** O `.fbk` é a garantia de saída. Deve ter
teste que prove que leva tudo — hoje já tem (PORT 2, 141 asserções).

---

## 11. O que fica local e o que justifica pagamento

| **100% local, gratuito** | **justifica serviço pago** |
|---|---|
| acervo, XML, certificados | réplica em nuvem, versionamento |
| importação, dedup, índice | aquisição contínua sem operador |
| normalização, `vendas.py` | IA para CFOP duvidoso e divergência |
| apuração e rastreabilidade | consolidado entre empresas |
| auditoria de XML | monitoramento contínuo com alerta |
| backup `.fbk` local | backup gerenciado fora do escritório |
| interface e relatórios básicos | acesso remoto, equipe, permissões |
| atualização do programa | implantação, migração, treinamento, SLA |

**O teste para decidir:** *se o servidor sair do ar, o contador consegue
apurar?* Se sim, é gratuito. Se não, ou é serviço pago, ou está no lugar
errado.

---

## 12. Recomendação final

**Núcleo aberto e gratuito para sempre; assinatura só sobre serviço com custo
recorrente.**

Por quatro razões:

1. **O desktop não custa nada para operar.** Assinatura sobre ele seria cobrar
   por nada, e criaria a pressão de piorar o gratuito para justificar o pago.
2. **Distribuição gratuita é o canal.** Contabilidade se escolhe por indicação.
   Um FISCALE bom e sem barreira circula; um com trava de licença fica no
   escritório de origem.
3. **A receita cresce com o uso, não com o download.** Quem tem 3 empresas
   nunca vai pagar nuvem; quem tem 300 paga sem hesitar — e é justo, porque
   consome 100 vezes mais infraestrutura.
4. **Protege o produto de si mesmo.** A regra "só cobro o que me custa" é a
   única que não degrada sozinha com o tempo.

**Ordem sugerida:** primeiro o gratuito ficar completo — a apuração ligada e
confiável, que é onde estamos. Depois backup em nuvem, que é o serviço mais
simples de operar e o de dor mais óbvia. Depois automação contínua e ELO.
IA fiscal por último, quando houver acervo grande o bastante para ela ter o que
aprender.

**Vender nada disso antes de a apuração estar fechada.** Produto que cobra
antes de resolver o problema principal queima o canal que a gratuidade abriu.

---

## Encerramento

Análise. Nenhuma linha de código, nenhuma cobrança, nenhuma licença, nenhum
bloqueio. NF-e/NFC-e próprias continuam desligadas do cálculo; o piloto NF-e
continua `Disabled`; as regras tributárias não foram tocadas.

**Aguardando aprovação** para: (a) implementar `LacunasDeAquisicao` e o roteiro
`EFISCO_PE`, e (b) fixar o modelo comercial e as decisões A1–A8.
