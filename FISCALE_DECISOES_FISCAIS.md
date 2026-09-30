# Fiscale — decisões fiscais

Registro do que foi **decidido** e **por quê**, para não ser redecidido por
engano. Cada item traz a evidência que sustenta a decisão.

Aberto em 11/08/2026.

---

## D1 — O projeto de verdade fica fora do diretório de trabalho

**Decisão:** todo trabalho fiscal acontece em
`C:\Users\nineq\TESTE DE SISTEMA INTEGRADO- novo\TESTE DE SISTEMA INTEGRADO\Fiscale\fiscale`.

**Por quê:** `Downloads\sistema_nfse_projeto_3` é uma cópia antiga do módulo
NFS-e — não tem `auditor_nfe.py`, `prefeituras.py`, `apuracao_federal.py` nem
`web/`. Trabalhar nela produziria código que não chega ao sistema em uso.

---

## D2 — O `NFeDistribuicaoDFe` não é fonte das NF-e geradas pela própria empresa

> **Redação corrigida em 17/08/2026 (APURAÇÃO 6A).** A versão anterior dizia
> que a distribuição "só traz compras". Isso é impreciso e induzia a erro: o
> serviço entrega **todo documento de interesse que a empresa não gerou** —
> compras, sim, mas também fretes de terceiros (CT-e), notas em que ela é
> apenas transportadora e eventos. O que ele **não** entrega é a NF-e de
> emissão própria. É essa a afirmação que vale.

**Decisão:** não "consertar" o `nfe.py` para trazer as notas de emissão
própria. Não é limitação da nossa implementação.

**Por quê:** a NF-e emitida pela empresa nasce no emissor dela — a SEFAZ já a
devolveu autorizada no momento da emissão, e não a redistribui como "documento
de interesse". A fonte dessas notas é a **importação de XML**
(`importar_xmls` → pasta `nfe_importadas`, `distribuir_entrada`,
`processar_pasta`), não a distribuição.

**Consequência prática (D49, medida em 17/08/2026):** enquanto essa importação
não for alimentada, a receita de venda por NF-e não tem fonte. O inventário da
APURAÇÃO 6A encontrou **8.059 NF-e e nenhuma de emissão própria**.

---

## D3 — A assinatura do ABRASF continua em SHA-1

**Decisão:** não migrar `prefeituras.assinar_xml()` para SHA-256. Quando o
REINF precisar, a função ganha o algoritmo como **parâmetro** e cada chamador
escolhe.

**Por quê:** o GissOnline valida contra o padrão ABRASF (RSA-SHA1 + C14N 2001).
Trocar para "unificar" quebra a NFS-e municipal, que está em produção. Não é
descuido histórico — é outro contexto normativo.

---

## D4 — A assinatura do ABRASF **serve** de base para a EFD-Reinf

**Decisão:** o assinador REINF nasce da mesma função, parametrizada — não de uma
implementação nova nem de uma dependência (`xmlsec`/`signxml`).

**Por quê — evidência oficial:** o Manual de Orientação ao Desenvolvedor da
EFD-Reinf **v2.7 (01/10/2025), §3.7** exige, textualmente:

- formato **Enveloped**
- transformações **enveloped-signature** + **C14N `REC-xml-c14n-20010315`**
- cadeia **EndCertOnly**, KeyInfo com **apenas** `X509Certificate`

Tudo isso é exatamente o que `prefeituras.assinar_xml()` já faz. A **única**
diferença é o algoritmo: RSA-SHA256 / SHA-256 em vez de SHA-1.

**Correção de suposição:** a canonicalização exigida é a **inclusiva de 2001**,
**não** a exclusiva (`exc-c14n`). A palavra "exclusive" não aparece no manual.
Quem partir do pressuposto contrário vai reescrever o que já funciona.

---

## D5 — HEALTH 1 antes de REINF 1

**Decisão:** construir o importador de plano de saúde primeiro.

**Por quê:** o REINF depende de valores **individualizados por beneficiário**,
que não existiam em lugar nenhum. Gerador de XML sem o dado é construção sobre
vazio. O importador entrega valor sozinho — a conferência de fatura que hoje é
manual — e produz exatamente o insumo que o REINF vai precisar.

---

## D6 — HealthPlan não conhece REINF

**Decisão:** o módulo `saude/` não menciona evento, XSD nem assinatura. A saída
é um modelo normalizado; a tradução para evento fica numa camada futura.

**Por quê:** acoplar parser de PDF a builder de XML significa que toda mudança
de leiaute da Receita mexe no parser da operadora, e vice-versa. Há um teste que
falha se as palavras `evtRet`, `R-4010`, `R-4020`, `envioLoteEventos` ou
`xmldsig` aparecerem no módulo — a decisão está no código, não só aqui.

---

## D7 — `state_plano.json` não vira banco de dados

**Decisão:** o módulo `saude/` tem armazenamento próprio
(`<DADOS>/<cnpj>/saude/extratos.json` + `originais/`). O `state_plano.json`
continua servindo às abas antigas, intocado.

**Por quê:** é estado de tela, gravado pela rota genérica `/api/state/<mod>`,
sem noção de empresa, competência ou procedência de arquivo. Guarda valores em
**float** e mistura dado real com dado de demonstração ("Ana Beatriz Ferreira
Lima, CPF 111.111.111-11" ao lado de um CPF de pessoa real).

---

## D8 — Dinheiro em `Decimal`, nunca em `float`

**Decisão:** todo valor monetário do módulo de saúde passa por `dec()`.

**Por quê:** a conferência é uma comparação na casa do centavo. Com float,
`0,1 + 0,2` não dá `0,3`, e três parcelas de R$ 904,72 não fecham exatamente em
R$ 2.714,16. O erro apareceria como divergência falsa — ou, pior, esconderia uma
verdadeira. Há teste para os dois casos.

---

## D9 — Tolerância de R$ 0,02, e só

**Decisão:** diferença acima de dois centavos é divergência relevante.

**Por quê:** existe arredondamento legítimo quando a operadora rateia um prêmio
por N vidas. Tolerância maior que isso deixa de cobrir arredondamento e passa a
esconder erro.

---

## D10 — Nome não é chave técnica

**Decisão:** a identidade de um beneficiário sai de CPF → código/certificado →
matrícula+nome → nome (último recurso, marcado em `origem_chave`).

**Por quê:** homônimo existe, e a mesma pessoa muda de grafia entre
competências ("MARIA E SILVA" numa, "MARIA EDUARDA SILVA" noutra). Sem chave
estável, o histórico por competência não funciona.

---

## D11 — CPF sem os zeros à esquerda é recuperado, não recusado

**Decisão:** CPF com menos de 11 dígitos é completado à esquerda, o DV é
conferido e o resultado fica em `cpf_ok` + aviso.

**Por quê — evidência:** o relatório real da SulAmérica traz o CPF com 10
dígitos quando ele começa por zero. Completar recupera o dado; não é
adivinhação. Mas CPF com DV inválido **não** vira chave técnica.

---

## D12 — Cada operadora declara a sua regra de conferência

**Decisão:** `Parser.conferir()` é obrigação do parser, e a regra vai em texto
para a tela.

**Por quê — evidência medida em faturas reais:**

- **Bradesco:** soma das vidas = `(TS) TOTAIS DA SUBFATURA` (12.970,52);
  total técnico + IOF = valor cobrado (12.970,52 + 308,69 = **13.279,21**)
- **SulAmérica:** soma das vidas = `Total da Família`; soma das famílias =
  `Total` do RESUMO PRÊMIO; Total + Acertos + IOF = Total Geral
  (2.714,16 + 0,00 + 64,60 = **2.778,76**)

Forçar uma fórmula única produziria divergência onde não há.

---

## D13 — No Bradesco, quem manda é a coluna de parentesco, não o sufixo `/00`

**Decisão:** titular é a vida cuja coluna de parentesco vem **vazia**. O sufixo
`/00` do certificado é conferência; divergindo, vale o parentesco e o extrato vai
para CONFERIR com aviso.

**Por quê:** o parentesco é dado explícito do relatório (`FILH`, `CONJ`, vazio
para o titular); o sufixo é convenção de numeração. Na fatura real os dois
concordam — o que valida a regra sem torná-la dependente da convenção.

---

## D14 — Parser só com amostra real

**Decisão:** Hapvida, Unimed, Amil e NotreDame aparecem na tela como
"aguardando amostra". Nenhum parser é escrito por dedução.

**Por quê:** parser deduzido produz número errado com cara de certo. Numa
conferência de fatura, isso é pior do que não ter parser nenhum — o usuário
confia num total que ninguém verificou.

---

## D15 — O arquivo original é a prova

**Decisão:** o arquivo importado é gravado byte a byte antes de qualquer parse,
com hash SHA-256, **inclusive quando a importação falha**.

**Por quê:** é o que vale diante da operadora, e é o que permite reprocessar
quando um parser é corrigido, sem pedir o PDF de novo. Guardar só nos casos de
sucesso deixaria justamente os casos que mais precisam de reprocessamento sem
como reprocessar.

---

## D16 — Reprocessar não apaga

**Decisão:** reprocessar move a leitura anterior para `historico[]` com o
`parserVersao` que a produziu.

**Por quê:** se um número muda depois de uma correção de parser, é preciso saber
o que ele era antes e por qual versão foi lido. Sobrescrever em silêncio destrói
a trilha.

---

## D17 — Detecção de operadora: decide ou pergunta, nunca chuta

**Decisão:** confiança ≥ 0,6 **e** margem ≥ 0,25 sobre o segundo colocado →
decide sozinho. Caso contrário, devolve `precisa_confirmar` com os candidatos e
o motivo.

**Por quê:** classificar errado em silêncio faz o extrato inteiro ser lido com
os totais no lugar errado. Perguntar custa um clique; errar custa uma
competência.

---

## D18 — Um arquivo ruim não derruba o lote

**Decisão:** `importar_arquivos()` isola cada item; o resultado é por arquivo.

**Por quê:** o escritório importa a pasta do mês de uma vez. Se o terceiro PDF
está corrompido, os outros nove precisam entrar — e o terceiro precisa aparecer
dizendo o que houve, não sumir.

---

## D19 — CNPJ divergente é aviso, não sobrescrita

**Decisão:** se o relatório traz CNPJ e ele difere da empresa da pasta, o valor
lido é **mantido**, um aviso é emitido e o status cai para CONFERIR.

**Por quê:** divergência aí quase sempre significa PDF importado na empresa
errada — o erro que estraga uma competência inteira. Sobrescrever esconderia
exatamente o sinal.

---

## D20 — `pypdf` como dependência

**Decisão:** adicionado a `requirements.txt` e `nfse/requirements.txt`.

**Por quê:** os relatórios são PDF com camada de texto. Extração artesanal foi
tentada e falhou (fonte Type0 com ToUnicode na SulAmérica, imagens embutidas
poluindo o fluxo). `pypdf` é Python puro, sem dependência nativa — não complica
o empacotamento com PyInstaller. Se faltar, o módulo diz o que instalar em vez
de quebrar.

---

## D21 — Só o necessário para a conferência

**Decisão:** o modelo de beneficiário não tem campo clínico. Nem sexo, nem
estado civil — que os relatórios trazem e a cobrança não usa.

**Por quê:** dado de plano de saúde é sensível. Capturar o que não se usa é
risco sem contrapartida. Há teste que falha se um campo desses aparecer.

---

## D22 — CNPJ do Bradesco: exigir a pontuação

**Decisão:** o CNPJ do estipulante é extraído só do formato pontuado, e as
raízes da Bradesco Saúde, do Banco Bradesco e da Bradesco Dental são descartadas.

**Por quê — erro real cometido e corrigido:** a primeira versão pegava 14
dígitos corridos e arquivou a fatura sob `00.203.207/2337-53`, que é o **nosso
número do boleto**. A versão anterior a essa pegava `92.693.118/0001-60`, o CNPJ
da **seguradora**. O correto é `15.862.792/0001-80`, confirmado contra o nome do
certificado A1 do cliente.

---

## D23 — Vencimento não é início de cobertura

**Decisão:** o vencimento do Bradesco sai da linha isolada abaixo da linha
digitável do boleto, não do `DE dd.mm.aaaa A dd.mm.aaaa`.

**Por quê:** na fatura auditada os dois davam 29/07/2026 — por coincidência. O
`DE … A …` é o período de **cobertura**. Usar ele daria a data certa em alguns
meses e errada nos outros, que é o tipo de defeito que só aparece em produção.

---

## D24 — Incerteza declarada, não escondida

**Decisão:** quando a diferença contra o total técnico do Bradesco é
**exatamente** a coparticipação, o sistema diz isso no contexto da divergência e
emite aviso.

**Por quê:** a amostra real que temos tem a coluna "Part. Seg." **zerada em
todas as vidas**, então não dá para afirmar se o `TOTAIS DA SUBFATURA` inclui ou
não a coparticipação. Inventar a resposta seria chute; esconder deixaria o
contador caçando um erro que talvez não exista. Fica como débito até aparecer
uma fatura com coparticipação. Ver `SAUDE_ROADMAP.md`.

---

## D27 — Motor de ingestão é pacote interno, não serviço separado

**Decisão:** NF-e 55, CT-e 57 e NFS-e Nacional passam a compartilhar um pacote
`nfse/backend/ingestao/`, com um contrato `Conector` único. Mesmo processo,
mesma distribuição portátil. Nada de microserviço, fila ou banco novo.

**Por quê:** os três fluxos são a mesma forma — autentica com o certificado da
empresa, pede a partir de uma posição, recebe um lote, grava o bruto, avança a
posição. Hoje ela está escrita duas vezes (`nfe.py` e `core.py`), com nomes
diferentes e checkpoints incompatíveis. Escrever o CT-e do zero seria a terceira
cópia. E o FISCALE roda numa máquina de escritório distribuída como pacote
portátil (PORT 4): processo separado seria custo sem retorno.

---

## D28 — Cadastro único de empresa, CNPJ normalizado como identidade

**Decisão:** `certificados.json` e `state_clientes.json` convergem para um
cadastro só, com o CNPJ em dígitos como chave, e a normalização vindo de um
único módulo.

**Por quê — medido, não suposto:** são 21 contas com certificado e 14 clientes
na tela, e as listas não coincidem. Enquanto forem dois cadastros, "varrer todas
as empresas" não tem resposta definida — e um motor de ingestão precisa dessa
resposta. Além disso a normalização de CNPJ está duplicada em cinco lugares
(`cnpj_publico`, `apuracao_federal`, `inscricoes`, `nfe`, `saude/modelo`): cinco
chances de divergir na hora de casar documento com empresa.

---

## D29 — Checkpoint por (empresa, serviço, ambiente); nunca global

**Decisão:** a posição da varredura passa a viver em
`dados/<cnpj>/ingestao/<servico>.<ambiente>.json`. O ambiente entra na chave.

**Por quê:** hoje há dois formatos — `<cnpj>/estado.json` (`ultimoNSU`) para a
NFS-e e `<cnpj>/nfe/estado.json` (`ultNSU`/`maxNSU`) para a NF-e — e **nenhum
dos dois registra o ambiente**. Uma varredura em produção restrita moveria o
ponteiro de produção, e a empresa deixaria de baixar documentos reais sem
ninguém perceber. Guardar dentro da pasta do CNPJ também torna o isolamento
estrutural: um erro que perca o CNPJ não tem caminho para escrever no checkpoint
de outra empresa.

---

## D30 — CT-e não tem consulta por chave: o checkpoint é dado crítico

**Decisão:** o checkpoint do CT-e é gravado com escrita atômica e conferido após
gravar; `consNSU` é o mecanismo de recuperação de lacuna.

**Por quê — confirmado no schema oficial `PL_CTeDistDFe_100`:** o `distDFeInt`
do CT-e aceita **apenas** `distNSU` e `consNSU`. O equivalente da NF-e
(`PL_NFeDistDFe_104`) aceita também `consChNFe`. Ou seja: **`consChCTe` não
existe**. Se o ponteiro do CT-e se perder, não há como pedir "devolva a chave X"
— só varrer NSU. E com `ultNSU=0` o Ambiente Nacional devolve apenas os últimos
3 meses, também conforme o schema. Perder o checkpoint do CT-e perde documento
de verdade.

---

## D31 — IA só depois do parser determinístico

**Decisão:** a sequência é XML → parser determinístico → normalização → regras →
IA. Campo que está estruturado no XML (`vNF`, `CNPJ`, `chave`, `CST`, `NCM`,
`dhEmi`) é extraído por caminho de elemento, nunca por IA. A IA fica para o que
é interpretação — texto livre de descrição de serviço, conciliação de nomes
grafados de formas diferentes, explicação de uma classificação — e sempre como
sugestão revisável, nunca gravação automática.

**Por quê:** não há ganho em usar IA onde o dado já vem estruturado e assinado
digitalmente, e há risco: um valor alucinado num campo monetário vira erro
fiscal silencioso, num documento que a empresa vai declarar.

---

## D32 — Identificador fiscal canônico aceita CPF, não só CNPJ

**Decisão:** a chave de identidade da empresa é o identificador fiscal em
dígitos — CNPJ com 14 ou **CPF com 11** —, validado pelos dígitos verificadores.
Zeros à esquerda são restaurados, mas só quando o resultado fecha, e o ajuste
fica registrado em `Identificador.ajustes`.

**Por quê — medido nos dados reais:** o cadastro tem um titular de 11 dígitos
(profissional autônomo com e-CPF). Modelar só PJ o deixaria de fora do motor de
ingestão. E os zeros à esquerda importam porque planilha e JSON mal exportados
transformam `04999001000143` em `4999001000143`; restaurar é necessário,
restaurar em silêncio é como um erro entra sem ninguém ver.

**Nome não é identidade.** `mesma_empresa()` só confirma por identificador
válido. Razão social parecida ajuda a diagnosticar e não decide nada.

---

## D33 — O PEM temporário do mTLS: reusar a biblioteca, não reimplementar

**Decisão:** a criação de sessão mTLS continua usando `requests_pkcs12`, sem
solução própria para o arquivo temporário.

**Por quê — auditado antes de decidir:** o `ssl` do CPython só carrega cadeia de
certificado a partir de um caminho; **não existe API em memória**. Então algum
arquivo vai existir. Lendo a biblioteca instalada: ela grava um PEM com a chave
privada cifrada por senha aleatória de 128 bits gerada na hora, carrega o
contexto, e apaga o arquivo em `finally` — some também quando dá exceção. E usa
o diretório temporário do sistema, fora da pasta de dados, logo nunca entra no
`.fbk` nem sobe para o Drive.

Escrever algo por cima disso seria pior e mais arriscado. O que a ING 1 fez foi
travar o comportamento com teste: cria três sessões e exige zero temporários
remanescentes, e confere que nenhum `.pem` nasce dentro da raiz de dados.

**Bônus do desenho:** o `Pkcs12Adapter` monta o `SSLContext` no construtor, então
senha errada e certificado vencido falham **antes de qualquer requisição** — e
viram exceção nomeada em vez de erro de TLS no meio de uma varredura.

---

## D34 — Trava de teste contra a pasta de dados real, opt-in

**Decisão:** `fiscale_dados.raiz()` passa a levantar se a variável
`FISCALE_TESTE_PROIBIR_RAIZ_REAL` estiver ligada e algo tentar resolver a raiz
para `~/Fiscale/dados` ou `~/SistemaNFSe/dados`. Sem a variável, o comportamento
é exatamente o de antes — a instalação normal não muda.

**Por quê:** as suítes já usavam `tempfile`, mas isso dependia de cada teste
lembrar de fazer certo. Um esquecimento grava sobre 30 mil arquivos fiscais de
21 empresas, e o estrago só aparece dias depois, quando alguém procura uma nota
que sumiu. Disciplina não é proteção; trava é.

A trava calcula as raízes reais direto de `Path.home()`, **sem passar por
`raiz_padrao()`**: os testes de portabilidade substituem `raiz_padrao` por uma
função própria, e uma trava que dependesse dela seria desligada justamente por
quem ela deveria proteger.

---

## D35 — Checkpoint por (identidade, serviço, ambiente), com escrita atômica

**Decisão:** o estado da distribuição vive em
`<raiz>/<identidade>/ingestao/<SERVICO>.<ambiente>.json`, gravado por
`mkstemp` na mesma pasta → `flush` → `fsync` → `os.replace`.

**Por quê:** o arquivo dentro da pasta da empresa torna o isolamento
estrutural, não disciplinar — um erro que perca a identidade não tem caminho
para escrever no checkpoint de outra empresa. O ambiente entra na chave porque
NSU de homologação e de produção são sequências independentes; misturá-los faria
a empresa pular documentos reais sem aviso. E o `fsync` está lá porque `flush`
entrega ao sistema operacional, não ao disco: queda de energia entre um e outro
deixaria um checkpoint truncado.

A identidade é o identificador canônico da ING 1 — CNPJ **ou CPF**. "O CNPJ"
seria uma chave que deixa de fora o titular pessoa física que existe no cadastro
real.

---

## D36 — O NSU só avança depois da persistência confirmada

**Decisão:** `consultar → lote → validar → preservar → CONFIRMAR → avançar`. Se
qualquer etapa antes da confirmação falhar, o checkpoint fica onde estava.

**Por quê:** repetir um lote gera duplicata de transporte, que a deduplicação
absorve. Pular um NSU não tem conserto — no CT-e não existe `consChCTe`, então o
documento perdido não pode ser pedido por chave depois. **É melhor reprocessar
um lote do que perder documentos.**

O NSU novo é o que o serviço informou; na falta dele, o maior efetivamente
recebido. Nunca um palpite.

---

## D37 — Corrupção de checkpoint nunca vira reset para zero

**Decisão:** checkpoint ilegível levanta `CheckpointCorrompido`, é movido para
`.json.corrompido-<carimbo>` como evidência, e **nenhum substituto é criado**.

**Por quê:** zerar o `ultNSU` parece recuperação e é perda silenciosa. Pedindo do
zero, o Ambiente Nacional devolve apenas os **últimos 3 meses** — o resto do
histórico simplesmente não volta. A ausência do arquivo é o sinal de que houve
perda e de que alguém precisa decidir; um zero gravado automaticamente pareceria
normalidade.

---

## D38 — Consulta por chave é capacidade do serviço, não da abstração

**Decisão:** `consChNFe` é declarada via `CAP_CONS_CHAVE` e um protocolo
separado (`FontePorChave`). A fonte do CT-e não declara a capacidade **e não tem
o método**.

**Por quê:** se a interface comum tivesse `consultar_por_chave`, o CT-e
precisaria implementá-lo só para levantar — e um dia alguém chamaria supondo que
existe, porque a NF-e tem. Não ter o método é a única forma de a impossibilidade
ficar evidente na hora de escrever o código, e não em produção.

---

## D39 — Checkpoints entram no `.fbk`; travas e evidências, não

**Decisão:** `<id>/ingestao/<SERVICO>.<ambiente>.json` vai no backup. Ficam de
fora `*.lock` e `*.corrompido-<carimbo>`.

**Por quê:** no CT-e o checkpoint é parte do estado operacional necessário à
continuidade — restaurar sem ele faria a empresa recomeçar do zero e perder tudo
anterior a três meses. Já a trava guarda o PID desta máquina: restaurada em
outra, bloquearia a primeira varredura por até duas horas em nome de um processo
que nunca existiu. E a evidência de corrupção é um fato local, para decisão
local.

Os checkpoints podem viajar porque **não contêm segredo algum** — há teste que
falha se algum campo de senha, certificado ou token aparecer neles.

---

## D40 — "Já está em dia" nunca dispensa a consulta

**Decisão:** o motor sempre consulta ao menos uma vez. `ultNSU == maxNSU` é
condição de parada **dentro do laço**, jamais motivo para não começar.

**Por quê — defeito real, pego pelo teste antes de ir para produção:** a primeira
versão pulava a execução quando `ultNSU == maxNSU`. Mas o `maxNSU` guardado é uma
*fotografia da última resposta*: quando chegam documentos novos, o maior NSU do
Ambiente Nacional cresce e o checkpoint não sabe disso até perguntar. Depois de
ficar em dia uma única vez, a empresa **nunca mais buscaria nada** — e a tela
continuaria dizendo `EM_DIA`. O custo da correção é uma consulta barata que o
serviço responde com "não há documentos".

---

## D41 — Seis estados de credencial, e nenhum deles derruba as outras empresas

**Decisão:** `avaliar()` distingue `VALIDO`, `AUSENTE`, `SENHA_NAO_CADASTRADA`,
`SENHA_INCORRETA`, `CORROMPIDO`, `AINDA_NAO_VALIDO` e `EXPIRADO`, **nunca
levanta**, e cada estado traz a ação necessária em português.

**Por quê:** o escritório tem 21 certificados, e o que fazer quando um para
depende inteiramente do motivo — renovar, reinformar a senha, recadastrar o
arquivo, restaurar de backup. "Falha de comunicação" não distingue nada disso, e
foi assim que um certificado ficou 15 dias vencido sem o sistema dizer uma
palavra. `AINDA_NAO_VALIDO` existe separado de `EXPIRADO` porque A1 recém-emitido
(ou relógio da máquina errado) levaria alguém a tentar renovar certificado novo.

Separar "senha errada" de "arquivo corrompido" é **heurística declarada**: a
`cryptography` levanta o mesmo `ValueError` para os dois, então olhamos a
estrutura DER — envelope íntegro que não abre é senha, envelope que não fecha é
corrupção.

---

## D42 — O FISCALE tem dois pilares: captura **e** apuração

**Decisão:** a ingestão de NF-e, CT-e e NFS-e existe para **alimentar a
apuração fiscal que o FISCALE já faz** — não para virar um painel de XML.

```
CAPTURA DE DOCUMENTOS  +  APURAÇÃO FISCAL
```

O produto continua tendo de: ler notas de venda e de serviço, organizar e
conferir os documentos, formar bases de apuração, dizer quais documentos entram
(e quais não entram) em cada apuração, apontar divergências, e **evoluir** a
apuração já existente de Simples Nacional, PIS, COFINS, CSLL e IRPJ.

**Por quê:** um capturador entrega arquivo; o escritório precisa de valor
apurado com origem conferível. O que o FISCALE já resolve hoje — a prévia do
DAS com RBT12, o ISS fixo do §22-A, o Fator R, o conferidor EFD × XML — é
justamente a parte que um "dashboard de XML" jogaria fora. A captura tem valor
porque termina em apuração; sem isso, ela é custo de banda.

**Consequência operacional:** quando chegarmos à fase do motor fiscal, o
primeiro passo é **levantar o código de apuração que já existe**
(`classificador.py`, `apuracao_federal.py`, `auditor_nfe.py`) antes de propor
qualquer substituição. Reescrever por hábito é como se perde a conferência
contra a guia real da Monte.

---

## D43 — Fluxo conceitual da ingestão até o resultado fiscal

**Decisão:** a arquitetura futura tem esta forma, e a ingestão **não duplica
nem descarta** a lógica de apuração existente:

```
NF-e / NFS-e / outros documentos
            |
      INGESTÃO FISCALE
            |
      ACERVO DOCUMENTAL          (imutável, endereçado por conteúdo)
            |
       NORMALIZAÇÃO
            |
    DOCUMENTOS DE VENDAS
    DOCUMENTOS DE SERVIÇOS
            |
     MOTOR DE APURAÇÃO
            |
   +--------+--------+
   |        |        |
SIMPLES  PIS/COFINS  CSLL/IRPJ
            |
       CONFERÊNCIA
            |
      RESULTADO FISCAL
```

**Por quê:** o acervo já é imutável e endereçado por conteúdo (D-ING 3A), e a
normalização é o ponto onde "documento capturado" vira "operação apurável".
Separar as duas camadas permite reprocessar apuração sem rebaixar o acervo —
e reprocessar é o que acontece quando uma regra muda retroativamente.

---

## D44 — Nenhum valor apurado sem caminho de volta aos documentos

**Decisão:** toda apuração futura tem de responder **"quais documentos formaram
este valor?"**, em todos os níveis:

```
PIS apurado -> base utilizada -> receitas/operações -> notas fiscais
```

Vale igualmente para COFINS, IRPJ, CSLL, Simples Nacional, segregações de
receita, exclusões, devoluções e cancelamentos. **Nunca produzir um total sem
permitir chegar à origem documental.**

**Por quê:** total sem origem não é conferível, e o que o escritório faz na
frente do fisco é justamente conferir. Foi assim que dois erros reais
apareceram: o ISS fixo que não saía do DAS e a competência de fora do período
puxando o mês inteiro. Nos dois casos, a resposta veio de olhar **quais notas**
formaram o número — não do número.

---

## D45 — Segregações do Simples Nacional: arquitetura preparada, regras depois

**Decisão:** a estrutura de normalização deve **poder** distinguir comércio,
indústria, serviços, receitas de anexos distintos, monofásicas, com ICMS-ST,
sujeitas a ISS, devoluções, cancelamentos e demais segregações. As **regras não
são implementadas agora** — só o lugar onde elas caberão.

Para Lucro Presumido, os mesmos documentos formam e conferem PIS, COFINS, IRPJ
e CSLL, separando receitas e atividades quando os percentuais, incidências ou
tratamentos diferirem.

**Por quê:** registrar a decisão agora evita que a normalização nasça achatada —
um campo único de "valor" que depois não se consegue segregar sem reprocessar
tudo. Mas escrever as regras antes de ter documento normalizado seria adivinhar.

---

## D46 — A tela de apuração é operacional, não um painel de leitura

**Decisão (registro para fase futura, sem implementar agora):** a tela mostra,
por competência, documentos lidos (vendas, serviços, cancelados, pendentes),
receitas por natureza e tributos apurados, com ações de **conferir documentos**,
**ver composição da base**, **ver divergências** e **reprocessar apuração**.
Para o Simples Nacional, mostra a composição da receita e as segregações usadas
para chegar ao DAS estimado.

**Por quê:** é a diferença entre "o sistema informa" e "o sistema resolve". As
quatro ações são as que o escritório executa hoje na mão; sem elas a tela vira
relatório, e relatório não elimina digitação — que é o critério de entrada do
roadmap.

---

## D47 — `tpNF` é a perspectiva de QUEM EMITIU, nunca a da empresa

**Data:** 17/08/2026 · **Fase:** APURAÇÃO 6

O campo `tpNF` da NF-e (0 = entrada, 1 = saída) descreve a operação **do
emitente**. A Distribuição DF-e entrega justamente os documentos que a empresa
**não** gerou (D2), então quase toda nota do acervo chega com `tpNF=1` — saída
do fornecedor, entrada nossa.

`normalizacao._sentido_da_nfe` lia `tpNF` primeiro e classificou **301 compras
como saídas**. O relatório da APURAÇÃO 3 afirmou "300 saídas" onde havia zero.

**Regra:** quem decide o sentido é o **papel da empresa** no documento.
`tpNF` só é consultado quando a própria empresa emitiu — aí as perspectivas
coincidem e ele ainda distingue devolução de saída. Papel indeterminado com
emitente terceiro é ENTRADA; sem emitente identificável, `INDEFINIDO` declarado.

## D48 — Transferência não é receita, e a regra atual não a separa

**Data:** 17/08/2026 · **Fase:** APURAÇÃO 6

`classificador.tipo_por_cfop()` trata o grupo `5.1xx`/`6.1xx` inteiro como
venda. Os CFOP `5151`/`5152`/`6152` são **transferência entre estabelecimentos
da mesma empresa** — não são receita.

No acervo real os dois CFOP mais numerosos são exatamente `6152` (1.531 itens)
e `5152` (362). Somá-los como receita inflaria a base de forma grosseira.

`ingestao/vendas.py` tem tabela própria com a categoria TRANSFERENCIA;
`tipo_por_cfop()` fica **intocado**, e `comparar_com_regra_atual()` mostra onde
as duas discordam. Unificar é decisão do contador, com a divergência na mesa.

## D49 — Nenhuma NF-e de venda existe no acervo hoje

**Data:** 17/08/2026 · **Fase:** APURAÇÃO 6

As 301 NF-e capturadas foram **todas emitidas por terceiros**: 0 saídas,
301 entradas. É a confirmação numérica de D2. A receita de venda por NF-e
ainda não tem fonte — só entrará quando houver importação de XML de emissão
própria. `vendas.py` fica pronto e **sem consumidor**.

## D50 — Inventário 6A: nenhuma NF-e de emissão própria existe em nenhuma fonte

**Data:** 17/08/2026 · **Fase:** APURAÇÃO 6A · **Método:** leitura offline,
SEFAZ não consultada, nada escrito em `dados/`.

Varridas todas as fontes já usadas pelo sistema, dentro e fora da raiz de
dados: `nfe/`, `nfe_importadas/`, `acervo/`, `xmls/`, `cte/`, a raiz de dados
legada em `Downloads/`, o pacote `FISCALE-backup-2026-08-11.fbk` e o OneDrive.

**8.059 NF-e · 0 de emissão própria.** Inclusive as 17 de `nfe_importadas`,
que são compras importadas à mão (todas com `tpNF=1` — o campo do emitente,
D47).

Fontes de documento **de emissão própria** que existem hoje:
- **NFS-e** — 5.156 documentos, 18 empresas, 45 competências (12/2022 a
  08/2026). É o que já alimenta a apuração.
- **CT-e** — 59 conhecimentos da `24.***/0001-34`, transportadora, modelo 57,
  competência 06/2026, importados à mão. Receita de serviço de transporte,
  **fora do escopo desta fase**.

**Conclusão:** a única fonte possível para a receita de venda por NF-e é o
canal de **importação manual de XML** (`importar_xmls` → `nfe_importadas`).
Ele existe, funciona e está vazio de vendas. Enquanto não for alimentado com
os XML do emissor de cada empresa, `vendas.py` continua sem insumo — e essa é
a razão de ele seguir sem consumidor.

## D51 — O 656 daquelas empresas era de JANELA: 24h de silêncio bastaram

**Data:** 18/08/2026 · **Fase:** teste de silêncio (pós ING 4D-4C)

Após 24h14min sem nenhuma consulta, uma única chamada de `distNSU` para a
`00.***-53` — a mesma empresa rejeitada com 656 em 17/08 — devolveu **cStat
137**, "Nenhum documento localizado". Falhas consecutivas voltaram a zero;
checkpoint intocado.

Confirma o subtipo **JANELA** (o tempo resolve) e afasta **SEQUÊNCIA** (que
exigiria revisão humana). Ver D-nota sobre os dois 656 distintos.

**O que continua sem resposta:** o intervalo mínimo seguro. 24h bastaram;
2h52min não bastaram; a faixa entre os dois não foi medida, e medi-la exigiria
justamente a repetição que causou o problema. A cadência de 2h do piloto
**segue sem validação**, e o piloto segue desligado com allowlist vazia.

## D52 — O portão único de importação, e o que ele nunca faz

**Data:** 18/08/2026 · **Fase:** APURAÇÃO 6B

`ingestao/importacao.py` é a única entrada de XML que não veio da SEFAZ. Valida
antes de gravar (legibilidade, DV da chave, modelo 55, chave × conteúdo,
denegação, participação da empresa) e só então chama `acervo.preservar()`.

**Nunca move o checkpoint da distribuição.** `preservar()` não depende de NSU —
o `nsu` é metadado em `captura.json`, e quem move ponteiro é o
`DistribuicaoRunner`. Provado: 10 notas importadas, checkpoint byte a byte
igual.

**Nunca chama de venda própria uma nota de terceiro.** Emitente ≠ empresa vira
`TERCEIRO` com impedimento escrito; empresa que não participa do documento é
recusada e nada é gravado.

**Nota sem protocolo é preservada, mas não forma receita.** Recusar guardaria
menos documento sem produzir mais verdade; contar afirmaria o que o XML não
afirma.

**NFC-e (modelo 65) é recusada com motivo**, não aceita sob rótulo `NFE55`
errado. Entra em fase própria, com espécie própria.

`nfe.importar_xmls` delega ao portão e mantém todas as chaves antigas do
relatório da tela, somando `acervo_*`. `nfe_importadas` continua sendo escrita
e **não foi apagada**: virou caixa de entrada, não fonte da verdade.

## D53 — Ausência de evento de cancelamento não é prova de nota ativa

**Data:** 18/08/2026 · **Fase:** APURAÇÃO 6B

Como a distribuição não devolve a NF-e própria (D2), ela também não devolve o
**cancelamento** dela. `situacao_das_operacoes()` responde em três estados, e o
terceiro é "não sei": `sem_evento_conhecido` aparece no relatório em vez de
virar um "ativa" implícito.

Sem isso, uma venda cancelada cujo evento não foi importado contaria como
receita para sempre, em silêncio — e erraria para mais.

## D54 — M1 executado: os 17 XML migraram para o acervo (18/08/2026)

**Fase:** APURAÇÃO 6B-1 · **Empresa:** `01.***.***/0001-86` · **Zero SEFAZ.**

Acervo da empresa 493 → 510 (+17). Todos `TERCEIRO`, nenhum candidato a venda
própria, 0 rejeitados. Segunda execução: 17/17 `DUPLICATA`, acervo inalterado.
Checkpoint: 31 arquivos hasheados antes e depois, **0 alterados**. Os 17 XML
originais continuam em `nfe_importadas`, byte a byte iguais.

**Achado do M1 — o índice não denuncia a própria defasagem.** Depois da
migração o acervo tinha 510 documentos e o índice 493, e mesmo assim
`indice.desatualizados()` devolvia **0**: aquele método compara o que já está
indexado, não o que falta indexar. Documento novo no acervo é invisível para
ele.

Corrigido com `pipeline.indexar_pendentes()` (17 indexados, índice 510 = acervo
510, checkpoint intocado). O índice é artefato derivado e reconstruível, então
o dado nunca esteve em risco — mas **quem migrar pelo portão precisa indexar
depois**, porque nada avisa. Enquanto `desatualizados()` não enxergar essa
diferença, a indexação é passo obrigatório da migração, não opcional.

## D55 — A importação só termina quando o índice alcança o acervo

**Data:** 18/08/2026 · **Fase:** APURAÇÃO 6B-2

Preservar sem indexar deixa o documento **existindo e invisível**: está no
acervo, e nenhuma consulta o encontra. Por isso `importacao.importar()` fecha o
ciclo — preserva, deduplica, reconcilia o índice — e o relatório informa
`indexados`, `falhas_indexacao` e `indice_em_dia`.

A reconciliação **roda sempre**, mesmo quando nada foi preservado. O defeito do
M1 não foi "esquecemos de indexar o que gravamos"; foi o índice ficar atrás sem
nada denunciar. Uma importação que não muda nada é justamente a ocasião de
descobrir que ele já estava atrás — e existe teste para esse caso.

**O ponto cego, nomeado:** `indice.ausentes(acervo)` responde "o que está no
acervo e falta no índice", e `em_dia_com()` responde sim ou não.
`desatualizados()` continua respondendo outra pergunta — quem foi lido por
parser velho — e não enxerga quem nunca entrou no índice. As duas coexistem com
os nomes certos.

A varredura passou a ter **uma implementação só**: `indice.indexar_ausentes`.
`pipeline.indexar_pendentes` delega. Eram duas cópias da mesma consulta, e foi
assim que divergiram sem ninguém notar.

Idempotência: com tudo em dia, `total = 0` e nenhuma linha é reescrita.
Reindexar por precaução apagaria a diferença entre "estava em dia" e "foi
consertado agora".

## D56 — A situação do documento é da normalização, não do portão

**Data:** 18/08/2026 · **Fase:** APURAÇÃO 6C

O portão já recusava contar como receita uma NF-e sem protocolo
(`SEM_PROTOCOLO`), mas `vendas.py` lia a operação normalizada e a somava assim
mesmo: **a barreira estava num lugar e a soma no outro**. Descoberto ao montar
o painel, com uma nota sem protocolo aparecendo na receita.

`normalizacao` ganhou `DENEGADA` e `NAO_AUTORIZADA`, ambas em
`FORA_DA_RECEITA`, e passou a mapear `doc.situacao` — que o parser já
distinguia — em vez de jogar tudo que não fosse cancelamento em `NORMAL`.
`vendas` herda a regra sem conhecê-la, e `MOTIVO_FORA_DA_RECEITA` faz o motivo
nomear a situação real: dizer "documento cancelado" para uma nota sem
protocolo mandaria o contador procurar um evento que não existe.

## D57 — Contagem que não fecha com a tabela não é contagem

**Data:** 18/08/2026 · **Fase:** APURAÇÃO 6C

O painel mostrava 7 documentos e `canceladas + sem_evento = 8`: as contagens
de situação varriam todas as NF-e do lote, inclusive a compra de terceiro, e a
tabela mostrava só as saídas. Corrigido para o mesmo recorte da tabela — uma
compra sem evento de cancelamento também não diz nada sobre receita.

E `canceladas` deixou de ser sinônimo de `fora_da_receita`: a primeira exige
evento importado; a segunda inclui denegada e sem protocolo. Eram duas ideias
com o mesmo nome na mesma tela.

## D58 — NFC-e tem espécie própria, e a espécie vem do modelo NA CHAVE

**Data:** 18/08/2026 · **Fase:** APURAÇÃO 6D

NF-e e NFC-e usam as MESMAS tags raiz (`nfeProc`, `NFe`, `resNFe`). Decidir a
espécie pela tag raiz chamava cupom de `NFE55`. Agora quem decide é o **modelo
nas posições 21-22 da chave**, via `identificacao.especie_da_nota()`.

`NFCE65` tem espaço de nomes próprio no `id_documento` — o prefixo entra no
hash —, pasta própria no acervo e filtro próprio no índice. O mesmo parser lê
as duas (a estrutura é idêntica) e elas **convergem só em `vendas.py`**, porque
lá as duas são venda de mercadoria.

Até a 6B o portão recusava modelo 65, e a recusa estava certa **para o que
existia**: aceitar antes de a identificação saber separá-las teria gravado
cupom sob rótulo errado. Um id que existe e mente é pior que uma recusa que
explica. A reversão está registrada na própria suíte 6B.

Migração: **nenhuma**. O acervo real não tinha nenhuma NFC-e (D50), então
nenhum `id_documento` mudou de valor.

## D59 — Fonte de emissão entrega bytes; validar é do portão

**Data:** 18/08/2026 · **Fase:** APURAÇÃO 6D

`ingestao/fontes_emissao.py` é a camada de origens. O contrato tem dois
métodos: `descrever()` e `documentos()`. Uma fonte **não** valida, não decide
identidade, não deduplica e não grava — se soubesse validar, seria uma segunda
opinião sobre o que é documento válido, e duas opiniões divergem.

`coletar()` tem quatro linhas úteis: pega da fonte, entrega ao portão. Se
crescer, é sinal de que alguma regra escorregou de lugar.

**Nenhuma memória do que já foi lido.** Um "já processei este arquivo" seria
uma segunda memória do que entrou, competindo com o acervo. A idempotência vem
do portão: recoletar devolve duplicatas e não escreve byte.

`PastaEmissor` é a única fonte implementada. Não há conector de ERP, e a
ausência é proposital — escrever um sem saber qual ERP cada cliente usa seria
inventar um protocolo e chamá-lo de integração.

## D60 — Inventário 6D: a automação já existia e termina no portão

**Data:** 18/08/2026 · **Fase:** APURAÇÃO 6D

Nesta máquina **não há emissor de NF-e/NFC-e**: Alterdata (Contábil/Fiscal/DP)
instalado com **zero XML** em disco, Domínio presente só como acesso remoto e
plugin de navegador, nenhuma API configurada, nenhuma tarefa agendada de
terceiro ligada a documento fiscal.

O que existe é a **pasta vigiada** (`entrada_config.json`, hoje
`%DOWNLOADS%` com `auto: false`) e uma thread de 60s em `main.py` que chama
`processar_pasta` → `importar_xmls` → **portão**. A automação está pronta
ponta a ponta desde a 6B; falta apontá-la para uma pasta que receba emissão
própria e ligá-la.

O Google Drive está sincronizado em `G:` e já carrega documento de emissão
própria (NFS-e). É a candidata mais próxima de automação real sem integração
nova — mas hoje não há NF-e/NFC-e própria em lugar nenhum (D50, reconfirmado).

## D61 — Não existe webservice para a NF-e de emissão própria

**Data:** 18/08/2026 · **Fase:** 7 (análise) · **Corrige a premissa da 6D.**

Investigação dos meios oficiais:

- `NFeDownloadNF` — **desativado em 01/06/2017**, substituído pelo
  `NFeDistribuicaoDFe`.
- `NFeDistribuicaoDFe` — entrega o que a empresa **não** gerou (D2).
- `NFeConsultaProtocolo` (`consChNFe`) — para o **emitente** devolve situação e
  protocolo, **não** o XML completo.
- e-Fisco PE — download de NF-e e de NFC-e existe, autentica por certificado
  digital ou conta gov.br, e **nenhuma página oficial menciona API, webservice
  ou integração automatizada**.

A razão é de premissa, não técnica: **o Fisco presume que o emitente já tem o
próprio XML**. Quem apura sem ter emitido está fora do desenho original.

**Consequência:** a aquisição de emissão própria fica no nível **assistido**.
Não haverá automação de navegador, e endpoint observado no portal não será
tratado como API — documentação oficial vem antes de qualquer código.

## D62 — A premissa da 6D era o ERP; a fonte real é o portal

**Data:** 18/08/2026 · **Fase:** 7 (análise)

A 6D assumiu que a origem natural seria a pasta do emissor do cliente. No
escritório não é: o cliente não manda XML, e a fonte é o **e-Fisco**.

A camada de fontes continua certa — muda o significado de `PastaEmissor`, que
passa a vigiar a **pasta de destino do download**, não a saída de um ERP.

A fonte `EFISCO_PE` prepara (o que falta, IE, período, pasta de destino) e
**não fala com a SEFAZ**. O humano faz as três ações irredutíveis — autenticar,
consultar, baixar —; o resto já é automático desde a 6B.

O `NFeDistribuicaoDFe` **não é tocado**: continua sendo o caminho das notas de
terceiros, sob a governança da ING.

## D63 — Modelo comercial: núcleo local gratuito, assinatura só sobre custo recorrente

**Data:** 18/08/2026 · **Fase:** 7 (proposta, aguardando aprovação)

O FISCALE desktop é **gratuito para sempre e completo** — cadastro, acervo,
importação, conferência, apuração, auditoria, backup `.fbk`, atualizações.
Não é versão reduzida.

Cobra-se apenas o que tem **custo recorrente real ou trabalho humano**: nuvem,
backup gerenciado, acesso remoto/multiusuário, automação contínua, alertas,
ELO/WhatsApp, IA fiscal, implantação, treinamento, suporte com SLA.

Regra que separa os planos: *roda na máquina do usuário* é grátis; *roda em
máquina nossa ou consome serviço de terceiro* é pago.

Nunca: bloquear dados, cobrar exportação, exigir nuvem para função local,
anúncio, venda de dado fiscal.

Decisões arquitetônicas a tomar agora (A1–A8), sem implementar cobrança:
fronteira local×serviço travada por teste; identidade de instalação (não
licença); capacidade em vez de fork; nuvem como réplica, nunca original;
certificado nunca sai da máquina; telemetria opt-in; multiempresa no que
nascer para nuvem; exportação como contrato testado.

## D80 — Senha de 8 caracteres NÃO libera acesso externo

**Data:** 27/08/2026 · **Fase:** login 2

A política de senha passou a exigir **mínimo de 8 e máximo de 128
caracteres**, recomendando (sem obrigar) uma frase de 12 ou mais. Não se
exige combinação artificial de maiúscula, número e símbolo: essa regra tem
trinta anos e produz `Fiscal@2026`, que é o primeiro palpite de quem tenta.
O eixo é o comprimento; o que se bloqueia é o adivinhável — senha comum,
sequência de teclado, repetição, e a própria pessoa (uid, nome, e-mail,
inclusive escritos em *leetspeak*).

**A CONDIÇÃO, e ela é vinculante:**

> **Oito caracteres bastam para a rede do escritório e não bastam para a
> internet.** O acesso remoto **não poderá ser liberado apenas com senha de 8
> caracteres.** Antes de qualquer liberação externa é obrigatório existir uma
> **camada adicional de autenticação** — rede privada (D72) ou **MFA** — além
> de **HTTPS**.

O motivo é aritmético, não de gosto: na rede local o atacante precisa estar
dentro do escritório, e o limite de tentativas (3 livres, atraso progressivo,
bloqueio de 5 min por conta e 10 min por IP) torna a força bruta inviável na
prática. Exposta à internet, a mesma conta recebe tentativas de todo lugar,
o tempo todo, e senha de 8 vira questão de tempo — não de sorte.

Registrado aqui para que a fase de acesso externo **não comece** tratando
"já temos política de senha" como se fosse suficiente.


## D64 — `autXML` é a via oficial da emissão própria, e o NSU é de quem consulta

**Data:** 18/08/2026 · **Fase:** 7B

O leiaute da NF-e define quatro atores para a distribuição: emitente (C01),
destinatário (E01), transportador (X03) e os **autorizados a baixar o XML** —
grupo `autXML` (GA01), até dez CNPJ/CPF escolhidos pelo emitente. Confirmado na
documentação oficial antes de codificar.

Quando o cliente põe o CNPJ do escritório em `autXML`, a nota que **ele emitiu**
passa a ser distribuível ao escritório pelo `NFeDistribuicaoDFe`. É a única via
oficial encontrada para adquirir emissão própria sem portal e sem ERP (D61) —
e corrige o rumo da 6D sem invalidá-la.

**O NSU pertence a quem consulta.** Um checkpoint por
`(identidade consultante, serviço, ambiente)`. Consultar uma vez por cliente com
o mesmo certificado repetiria a mesma fila N vezes e queimaria a cota do
escritório sem trazer nada novo. Provado: uma consulta trouxe notas de dois
clientes, e nenhum cliente ganhou checkpoint.

O portão aprendeu o papel `AUTORIZADO`: quem consta só em `autXML` é
participante legítimo do documento — preservado, nunca receita dele.

Documento que não dá para rotear **não é descartado**: fica sob o escritório
com a chave preservada. Descartar perderia o NSU, que não volta.

**Situação por evidência, não por declaração:** `NAO_CONFIGURADA` →
`AGUARDANDO` (o cliente declarou) → `ATIVA` (chegou documento) ou
`SEM_EVIDENCIA` (silêncio repetido). `ATIVA` não regride por lote vazio.

**Desativada por padrão.** NFC-e não entra por esta via sem evidência oficial
de distribuição equivalente.

## D65 — A documentação interna não viaja no pacote distribuível

**Data:** 18/08/2026 · **Fase:** 7B

A primeira montagem do ZIP levou os `.md` de engenharia junto: 9 ocorrências de
CNPJ de clientes reais em 8 arquivos. Não é conteúdo de usuário — é o diário do
projeto, com decisões, diagnósticos e relatórios de fase.

`montar_portatil.py` deixou de copiá-los. O que o usuário lê é o `LEIA-ME.txt`.
Já havia precedente de `.md` levando segredo para fora; a lição é a mesma: não
distribuir o que descreve a operação.

## D66 — Duas instalações do Fiscale disputavam a porta interna do NFS-e

**Data:** 20/08/2026 · **Origem:** o módulo NFS-e não abria na cópia portátil.

`_porta_livre(8790, 8830)` é calculada no **pai**, na importação, mas quem
**binda** é o processo-filho, depois. Entre o palpite e o bind, outra instalação
do Fiscale pode tomar a porta — e foi o que aconteceu: a instalação do projeto
segurava a 8790, e o filho do portátil morria ao bindar.

**O que escondia o problema:** `NFSE_ATIVO = True` era gravado logo após o
`Popen`. `Popen` devolver um processo não quer dizer que ele subiu. O Fiscale
anunciava o módulo como ATIVO e o usuário só descobria ao clicar em NFS-e e
receber "o módulo não respondeu", sem nenhuma pista do motivo — o filho morria
sem console, e a mensagem de erro ia para lugar nenhum.

**Correção:** `iniciar_modulo_nfse()` confirma que o filho ficou **escutando**
(`_escutando`, até 12 s), e em caso negativo encerra o órfão e tenta a próxima
porta livre, até três vezes, dizendo no console qual porta falhou e que outra
instalação pode estar usando. `NFSE_ATIVO` só é verdadeiro com escuta
confirmada.

**Verificado** com 8790 e 8791 ocupadas: o filho subiu na 8792, `/login.html`
devolveu 200 e `/nfse.html` passou a ser roteado.

Duas instalações na mesma máquina é situação normal aqui — a do projeto e a
portátil — e o pacote precisa conviver com isso.

## D67 — A causa real: o filho do NFS-e herdava handles inválidos do `pythonw`

**Data:** 20/08/2026 · **Corrige o diagnóstico da D66.**

A D66 atribuiu a falha à disputa de porta com outra instalação. **Estava
incompleta.** Com as portas 8791 a 8794 comprovadamente livres, o filho
continuava morrendo.

**A causa é outra.** O Fiscale é aberto por `pythonw.exe`, que roda sem
console: `sys.stdout` e `sys.stderr` do pai não são handles válidos. O
`subprocess.Popen` não redirecionava nada, então o filho **herdava** esses
handles e morria na primeira linha que tentasse escrever — sem rastro, porque
o lugar de escrever o rastro era justamente o que não existia.

Correção: o filho recebe `stdout` próprio (`dados/nfse-modulo.log`),
`stderr=STDOUT` e `stdin=DEVNULL`. Vive, e quando falhar por outro motivo o
motivo fica gravado.

**Por que demorei a achar:** meu primeiro teste lançou o `runner.py` com
`pythonw` e **redirecionou a saída para um arquivo** — o que mascarava
exatamente o defeito. O teste passou e eu descartei a hipótese certa. Só a
execução real, pelo `Fiscale.bat`, com as portas livres, isolou a causa.

O da D66 continua valendo e foi mantido: a confirmação de escuta e a troca de
porta são corretas por si, e foram elas que transformaram um silêncio em
`módulo indisponível (porta interna 8794)` — a mensagem que permitiu ver que
porta não era o problema.

**Verificado** pelo caminho real (`pythonw`, como o `.bat` abre):
`NFS-e: módulo ATIVO (porta interna 8791)`, filho vivo como processo-filho do
Fiscale, `/login.html` 200 e `/nfse.html` roteado.

## D68 — O pacote portátil não levava a interface do módulo NFS-e

**Data:** 20/08/2026 · **Terceira e última causa do "NFS-e não abre".**

`montar_portatil.py` copiava `nfse/runner.py`, `nfse/requirements.txt` e a
árvore `nfse/backend/` — **e mais nada**. Ficava de fora `nfse/frontend/`
(260 KB: `index.html`, `danfse_logo.png`, `nfse.webp`), que é a **tela própria
do módulo**, servida pelo `main.py` dele.

Resultado: o módulo subia, respondia, e devolvia `Internal Server Error` —
`FileNotFoundError` no `read_text` do index, dentro de um traceback de quarenta
linhas de FastAPI que em nenhum momento dizia que faltava uma PASTA.

Corrigido no construtor; `.bak` também passou a ficar de fora (a interface
tinha um `index.html.bak` de 104 KB que é histórico de desenvolvimento).

**As três causas eram independentes e se escondiam em fila:**
1. (D66) disputa de porta entre duas instalações;
2. (D67) o filho herdava handles inválidos do `pythonw` e morria calado;
3. (D68) faltava a interface do módulo no pacote.

Cada correção só revelava a seguinte. O que encurtou a terceira foi o log
próprio criado na D67 — sem ele, o `Internal Server Error` seria outra caçada
às cegas.

**Lição de empacotamento:** o construtor listava arquivos por nome
(`COPIAR_NFSE`). Lista escrita à mão envelhece em silêncio. Um teste de
inventário do pacote — abrir o ZIP e conferir que todo caminho que o código lê
em tempo de execução existe — teria pego isto antes de o usuário ver.

---

## D69 — O cofre de segredos não pertence a módulo funcional nenhum

**Decisão (FISCAL AI 1a, 22/08/2026):** `fiscale_segredos` é infraestrutura e
nasce **sem conhecer tela alguma**. O `MAPA` começa vazio; quem guarda
credencial se **inscreve**, e as inscrições vigentes moram num arquivo à parte
(`fiscale_inscricoes.py`), importado por todo ponto de entrada que grava, lê ou
migra `state_<mod>.json`.

A sanitização do `.fbk` passou a ser dirigida pela **mesma inscrição**:
`_limpar_state(mod, ...)` vale para qualquer `state_*.json`, e não mais para um
nome de arquivo conhecido. Inscrever-se no cofre protege o backup pelo mesmo
gesto.

**Por quê:** o `MAPA` tinha uma única entrada — `plano` — e sobre ela estava
construída a proteção de segredo do sistema inteiro: DPAPI, mascaramento,
pendências, migração do texto claro e sanitização de backup. Remover o módulo
Plano de Saúde teria arrastado tudo isso junto, sem que nada acusasse. Pior: a
`teste_seguranca` usava `state_plano.json` como **veículo** de 32 asserções que
não são sobre plano de saúde — são sobre criptografia e vazamento. Cobertura de
segurança não pode sair de carona com uma tela de negócio.

**Consequência que vale declarar:** o backup só remove credencial de tela
**inscrita**. Uma tela nova que guarde senha sem se inscrever viajaria com o
segredo dentro — por isso existe a rede de segurança que apaga qualquer campo
`*_protegido` em qualquer state, inscrito ou não. Ela toca só nos campos irmãos
que o próprio cofre cria: uma heurística que saísse apagando todo campo chamado
"chave" apagaria a chave de acesso das notas, que é o oposto do que se quer.

---

## D70 — Teste com data fixa é bomba-relógio, e ela explodiu

**Decisão (FISCAL AI 1a, 22/08/2026):** instante de referência em teste que
interage com o relógio real **deriva de `datetime.now()`**, nunca de uma data
escrita à mão.

**O que aconteceu:** `teste_ingestao4c.py` fixava
`AGORA = datetime(2026, 8, 20, 12, 0, UTC)`. O produto conta a janela do 656 a
partir do **maior** entre o carimbo real da resposta e o `agora` informado
(`servico_distribuicao`: `rejeitado_em = max(carimbo, q)` — "bloquear de menos
nunca", D51). Enquanto 20/08 estava no futuro, `max` escolhia `AGORA` e a conta
dava 3.600 s exatos. Passado o dia 20, `max` passou a escolher o relógio real e
a mesma verificação passou a esperar 3.600 onde a resposta certa era a
diferença real — 222.647 s no dia em que foi diagnosticado.

**Três asserções falharam sem que uma linha do produto mudasse.** O baseline
registrado em 18/08 dizia "0 falhas" e continuava verdadeiro para aquele dia; o
arquivo é que datou.

**O produto não foi tocado.** `max(carimbo, q)` está certo e é deliberado —
errar a janela para menos é o que provoca o 656 seguinte. Quem estava errado
era o teste.

Mesma correção aplicada preventivamente em `teste_ingestao4d3b.py` e
`teste_piloto_nfe.py`, que carregavam a mesma constante e ainda passavam por
sorte.

---

## D71 — Plano de Saúde e e-CAC saíram do produto

**Decisão (FISCAL AI 1, 22/08/2026):** os dois módulos foram removidos. O
FISCALE passa a ter um escopo só: **documento fiscal — captura, conferência e
apuração** (D42), agora com a inteligência fiscal como próxima camada.

**Dado pessoal não some porque o software mudou de escopo.** O que o Plano de
Saúde acumulou — 2 pessoas físicas com CPF, data de nascimento e 4 dependentes,
1 credencial de portal, 4 operadoras — foi arquivado cifrado
(`arquivar_plano_saude.py`, AES-256-GCM, o mesmo cofre do `.fbk`) **antes** da
exclusão, e o arquivo foi aberto e conferido byte a byte contra a origem. O
módulo não tinha movimento nenhum: zero faturas, zero lançamentos, zero
arquivos nas pastas `saude/`.

**O e-CAC não tinha backend.** Nenhuma rota `/api/ecac` jamais existiu: era uma
tela que guardava a referência de um certificado e abria o portal da Receita. O
`.pfx` referenciado é **compartilhado** (5 registros em `certificados.json`) e
foi conferido por SHA-256 antes e depois da remoção.

**Perda funcional declarada:** o e-CAC era o único ponto da interface que
tratava DCTFWeb/MIT e PGDAS-D, ainda que só como atalho para o portal. O
cálculo continua em `apuracao_federal.py`; o que saiu foi o atalho, não o
número.

**`pypdf` saiu junto** — entrou (D20) só para ler PDF de fatura de operadora, e
não sobrou nenhum importador.

---

## D72 — Acesso de fora do escritório: rede privada com HTTPS, nunca porta aberta

**Decisão (23/08/2026, registrada na NF-e 3 — implementação em fase própria):**
o FISCALE poderá ser usado de casa, do celular e de um notebook externo, e o
caminho será **rede privada com HTTPS válido**. Nada disso foi instalado nesta
fase; aqui só se registra a decisão para que ela não seja reinventada de outro
jeito daqui a três meses.

**O que fica proibido, e é a parte que importa:**

- publicar o FISCALE diretamente na internet;
- abrir porta no roteador ou fazer *port forwarding*;
- deixar a **8777 acessível publicamente**;
- expor banco ou arquivos por caminho direto.

**O que a solução precisa ter:**

- **HTTPS também dentro da rede privada.** "É rede interna" não é motivo para
  tráfego em claro: a rede privada protege quem entra, não o que trafega.
- **Certificado A1 e segredos permanecem no servidor.** O que viaja é tela, não
  credencial — a mesma regra que já vale para o acesso pela rede do escritório.
- **Revogação por usuário e por dispositivo.** Perder um celular precisa ser
  resolvível sem trocar o acesso de todo mundo.
- **Registro dos acessos remotos**, separável do acesso local.

**Por quê assim.** O obstáculo real nunca foi de rede: já existe login, já
existem papéis (admin/operador) e já existe servidor único (D-servidor-único).
O risco de abrir uma porta é que o portão de autenticação do FISCALE passa a ser
a única barreira entre a internet e 23 CNPJs com certificado A1 na mesma
máquina. Rede privada põe uma barreira **antes** da aplicação, e isso é
diferente em natureza, não em grau.

**Não decidido aqui:** qual tecnologia (VPN ponto-a-ponto, túnel gerenciado,
proxy autenticado). Isso é a fase de implementação, com comparação escrita.

---

## D73 — A interface lê o índice; o leitor legado fica, mas só para conferir

**Decisão (NF-e 5, 23/08/2026):** `GET /api/nfe/notas` passou a servir de
`ingestao/consulta.py`. A varredura de `<cnpj>/nfe/*.xml` saiu do caminho da
tela.

**O que isso resolveu, em número:** 300 documentos capturados pelo motor novo
eram invisíveis na interface, porque viviam no acervo e o leitor antigo só
conhecia a pasta. Depois da troca: **zero** — provado documento a documento por
`ingestao/prova_visibilidade.py`, que pergunta pela mesma camada que a tela usa.

**O leitor legado NÃO foi removido**, e isso é deliberado: ele é o outro lado do
comparador de equivalência. Enquanto existir, dá para perguntar "os dois
caminhos dizem a mesma coisa?" sem depender do índice para responder sobre si
mesmo. Tirá-lo é decisão de outra fase, e custa essa garantia.

**O que a tela nunca faz:** abrir banco, conhecer o acervo, montar caminho de
arquivo. Ela diz *qual* documento quer; o servidor resolve *onde* ele está. A
rota antiga de XML devolvia o primeiro arquivo que contivesse a chave como
substring — além de lenta, podia entregar o documento errado quando uma nota
referenciasse outra.

**Honestidade dos cards.** Nenhum card soma o acervo e chama de "entradas":
`TRANSPORTADOR` e `AUTXML` ficam fora de recebidas, e a advertência de que
`DESTINATARIO` ainda não é "compra tributável" viaja no próprio JSON, não só na
tela. Nas transportadoras a diferença é de 89× — somar tudo erraria por duas
ordens de grandeza.

---

## D74 — DANFE: espaço reservado, não improviso

**Decisão (NF-e 5):** a tela mostra `DANFE — em preparação` e não gera nada.

Investigado antes de decidir: `pdflocal.py` existe e gera **DANFSe** — o
documento da NFS-e, com outro layout, outros campos e outra norma. Não há
representação de NF-e no projeto.

Transformar HTML arbitrário em algo chamado "DANFE" seria dar aparência oficial
a um documento que não é. O espaço arquitetural está reservado na tela; a
geração é fase própria, e começa por decidir a fonte (representação recebida do
cliente × geração a partir do XML autorizado, sempre carimbada como auxiliar).

---

## D75 — O lote não tem pesquisa própria

**Decisão (NF-e 6, 23/08/2026):** a exportação em lote recebe o **mesmo
`Filtro`** que a tela usou e chama `ingestao/consulta.py`. Não existe uma
segunda implementação de "quais documentos".

**Por que isso é regra e não estilo.** Se o lote tivesse a própria seleção, ela
divergiria em algum canto — papel, cancelamento, resumo — e a divergência
apareceria como arquivo faltando num pacote já entregue ao cliente. Com uma
fonte só, o número do ZIP pode ser conferido contra o número da tela, e há
teste que faz exatamente essa conferência.

**A soma que precisa fechar:**

```
documentos do resultado = XMLs no ZIP + não incluídos, cada um com motivo
```

Um lote que entrega 238 de 245 sem dizer onde foram os 7 é pior do que um lote
que falha: o erro só aparece no mês seguinte. Os motivos são
`XML_COMPLETO_INDISPONIVEL`, `ARQUIVO_AUSENTE_NO_ACERVO` e
`EXCLUIDO_POR_SITUACAO`, e todos vão para o `RELATORIO.csv`.

**Canceladas deixaram de sumir em silêncio.** A exportação anterior tinha
`somente_validas=True` por padrão e descartava a cancelada sem avisar. Agora a
escolha é explícita — Todos / Somente válidas / Somente as canceladas — e usa a
**situação atual**, não a do documento.

**Papel continua sendo a linha que não se cruza.** Filtrou recebidas, o ZIP tem
recebidas; `TRANSPORTADOR` e `AUTXML` não entram. Sem filtro de papel, a tela
diz a composição **antes** de baixar. Nas transportadoras a diferença entre um
e outro é de 89×.

---

## D76 — `dhSaiEnt` estava em 99,5% do acervo, e não era lido

**Decisão (NF-e 6):** o parser passou a ler `dhSaiEnt`/`dSaiEnt` e o índice a
guardá-la em coluna própria, separada de `dh_emissao`.

**A medição que mudou o texto do código.** Escrevi, antes de medir, que o campo
é opcional e que "a maior parte dos documentos não o traz". Varri o acervo:
**7.498 de 7.534 NF-e completas o trazem — 99,5%.** As 36 exceções estão em
três empresas. O comentário estava errado e foi corrigido para o número medido.

**O que ela NÃO faz.** Não vira competência: a competência continua saindo da
emissão. Mudar isso mexeria em apuração, e esta fase é documental.

**Ausência é ausência.** Documento sem `dhSaiEnt` mostra `—` e a frase "o
documento não informou". Cair para `dhEmi` seria inventar exatamente o dado que
o campo existe para registrar. E filtrar o período por saída/entrada **exclui**
quem não a informou — inclusive todo resumo, que não tem `ide`. A tela diz isso
onde a escolha é feita.

**Índice velho recusa com o motivo.** Um índice gravado antes da NF-e 6 não tem
a coluna, e a consulta abre em `mode=ro` — ela não pode criar coluna. Em vez de
`no such column` chegando à tela como "consulta falhou", a resposta é
`CampoDeDataIndisponivel` dizendo para reindexar.

---

## D77 — O DANFE é `fpdf2`, e o código de barras é nosso

**Decisão (NF-e 6):** DANFE da NF-e modelo 55 gerado por **`fpdf2` 2.8.7**, em
`nfse/backend/danfe.py`.

**Por quê.** Ela **já está no projeto** — gera o DANFSe da NFS-e — e já está no
`PACOTES` do portátil. Custo de distribuição: **zero**. O FISCALE é entregue
como pacote de 82 MB sem instalador e sem rede; biblioteca nova é problema de
distribuição, não detalhe de implementação.

`brazilfiscalreport` resolveria o DANFE pronto e seria a escolha óbvia num
projeto com `pip install` à mão. Aqui custaria a biblioteca, as dependências
dela e uma revisão de licença de algo que produz documento fiscal. Registrada
como alternativa; não adotada.

**CODE-128C escrito aqui.** O padrão da NF-e manda esse código para a chave de
44 dígitos, o `fpdf2` só traz Code39, e não há biblioteca de código de barras
no projeto. A tabela dos 107 padrões e o dígito verificador estão em
`danfe.py`, com teste que confere a integridade da tabela (107 símbolos únicos,
todos somando 11 módulos; o STOP, 13).

**Validado contra XML reais:** 400 documentos do acervo, sorteados —
**399 gerados, 1 recusado (resumo), zero falhas**. Mediana **70 ms**, p95 158 ms.

**Reforma fora da grade oficial, e dito na página.** O XML já traz IBS/CBS/IS
(390 dos 400 da amostra). Onde esses campos entram no DANFE depende da Nota
Técnica vigente, que não dá para conferir offline. Inventar posição dentro da
grade produziria um documento que **parece** o padrão novo e não é. Eles saem
num bloco próprio, rotulado "informados no XML", com a frase de que o FISCALE
não os calcula.

**O PDF nunca é fonte.** Rodapé em toda página dizendo que é representação
auxiliar do XML autorizado e onde conferir a validade. O cache fica em
`<empresa>/derivados/`, com o hash do XML e a versão do gerador no nome — fora
do acervo, fora do `.fbk`, e apagado por `limpar_derivados`.

---

## D78 — Pacote para o Domínio: os dois formatos, e a incerteza declarada

**Decisão (NF-e 6, §9):** entregar **ZIP plano** (XML na raiz, sem CSV dentro) e
manter a **escrita direta em pasta**. Nenhum dos dois é chamado de "o certo".

**O que foi procurado e não achado.** Não há, em lugar nenhum deste projeto,
evidência de como o importador do Domínio consome os arquivos — se aceita ZIP
ou exige pasta com XML soltos. O nome do software não aparece em nenhum `.py`,
`.md`, `.html` ou `.json`. **Não presumi.**

**O que existe de evidência é do nosso lado:** a exportação anterior escrevia
XML soltos numa pasta e a abria no Explorer. É o fluxo que o escritório usa
hoje — e por isso ele continua funcionando, agora lendo do acervo.

**O CSV fica fora do pacote do Domínio.** Um arquivo estranho no meio dos XML é
o tipo de coisa que faz importador recusar o lote inteiro. Ele sai por rota
própria.

**Pendência para o usuário:** conferir num lote pequeno se o Domínio aceita o
ZIP ou se prefere a pasta, e dizer qual. Aí um dos dois vira o padrão do botão.

---

## D79 — Mexeu no backend, reinicie: o frontend é lido do disco, o backend não

**Fato (23/08/2026):** logo depois da NF-e 6, a tela na instância real
respondia *"Não consegui carregar os documentos — Not Found"*. A URL era
`GET /api/nfe/resumo` devolvendo **404**.

**A causa não era código.** O processo estava de pé desde as 11:11 daquele dia
e carregara o `main.py` daquele momento — anterior à NF-e 5. Python importa o
módulo uma vez; editar o arquivo não muda um processo vivo, e o `uvicorn` de
produção roda sem `--reload`, de propósito.

**O que torna isso difícil de enxergar:** `web/` é servido do disco a cada
requisição, então **a tela fica nova enquanto o backend continua velho**. O
sintoma é um 404 genérico numa interface que parece atual — e a primeira
suspeita vai para a rota, para o CNPJ, para o filtro; nunca para o relógio.

**A pergunta que resolve em dez segundos**, perguntando ao processo o que ele
mesmo tem:

```bash
curl -s http://127.0.0.1:8790/openapi.json | grep -o "/api/nfe/[a-z/]*" | sort -u
```

Rota nova ausente = processo velho. Um segundo sinal, se o `openapi` não estiver
à mão: a assinatura de `/api/nfe/notas`. Se ela ainda aceita `somente_validas`,
é pré-NF-e 5.

**Como reiniciar sem quebrar nada:**

1. registre o SHA-256 dos `*/ingestao/*.json` antes — é o que prova depois que
   nenhum checkpoint andou;
2. confira que não há importação em curso e que a Tarefa Agendada segue
   desativada;
3. pare **pela árvore do PID raiz** (`taskkill /PID <raiz> /T`) — nunca por
   nome de processo, que derrubaria qualquer outro Python da máquina;
4. religue com `nfse\.venv\Scripts\pythonw.exe fiscale_server.py`.

**Observação sobre a árvore de processos:** o FISCALE sobe como **quatro**
processos encadeados (pythonw da venv → pythonw do sistema → runner da venv →
runner do sistema). Isso é o funcionamento normal desta instalação, e não duas
instâncias em disputa — confirmado por a estrutura se reproduzir idêntica após
o reinício. A raiz é o único PID que precisa ser parado.

---

## D81 — CT-e: espécie própria, e o namespace como desempate

**Decisão (CTE 1, 30/08/2026):** o CT-e não é uma variação da NF-e. Espécies
`CTE57` e `EVENTO_CTE`, parsers próprios, checkpoint próprio, adaptador próprio.

**O desempate que não é detalhe.** `resEvento` e `evento` são tags dos **dois**
documentos. Um resumo de evento de CT-e tem exatamente a mesma tag raiz que um
de NF-e; o que os separa é o namespace do portal — `/cte` contra `/nfe`.

Sem isso, todo evento de CT-e seria identificado como `EVENTO_NFE`, iria para a
fila da NF-e e ficaria pendurado numa chave que aquele acervo não conhece. Não
seria erro visível: seria **um evento a menos, em silêncio**.

---

## D82 — O TOMADOR não tem bloco próprio, e é ele que importa

**O campo `toma` é um PONTEIRO.** Ele guarda 0..3 e aponta para outro
participante: 0 remetente, 1 expedidor, 2 recebedor, 3 destinatário. Só quando
vale 4 existe `<toma4>` com CNPJ próprio.

**Medido nos 59 CT-e reais do acervo: TODOS os 59 usam `toma=0`.** Nenhum tem
`toma4`. Ler apenas o bloco `toma4` — que é o caminho óbvio, por ser o único
visível no XML — deixaria o tomador **ausente em 100% dos documentos reais**.

E o tomador é quem paga o frete: é ele que lança o crédito, entra na apuração e
é a pergunta pela qual o contador procura o documento. Por isso a precedência
do CT-e coloca `TOMADOR` logo depois de `EMITENTE`, antes de remetente e
destinatário.

Um mesmo documento carrega **todos** os papéis aplicáveis. Uma transportadora
que emite e também toma tem os dois, e guardar só o mais forte faria o
documento sumir de uma das duas perguntas.

---

## D83 — Sem `consChCTe`, o checkpoint do CT-e vale mais

**O contrato oficial (`PL_CTeDistDFe_100`) não tem consulta por chave.** A NF-e
tem três formas de perguntar (`distNSU`, `consNSU`, `consChNFe`); o CT-e tem
duas.

A consequência é operacional: se o checkpoint se perder, **não há como pedir
"aquele documento" de volta**. A única recuperação é caminhar por NSU, e a
retenção do Ambiente Nacional a partir do zero é de ~3 meses.

Por isso a regra "só avança depois de gravar e conferir todos os documentos" é
aqui mais crítica do que na NF-e. Um avanço indevido lá é um susto; aqui pode
ser documento perdido para sempre.

`montar_envelope_cons_chave` **existe no adaptador só para recusar com
explicação** — para que ninguém a escreva por analogia com a NF-e daqui a seis
meses e receba um `AttributeError` seco.

---

## D84 — Entrada canônica: uma porta, duas procedências

**A distribuição oficial é a fonte principal; a importação serve histórico e
lacunas.** As duas passam pela MESMA deduplicação (`ingestao/entrada_cte.py`).

| regra | como |
|---|---|
| CT-e único | chave de acesso, 44 dígitos com DV |
| evento único | chave + tipo + sequência + órgão |
| repetição exata | SHA-256: `DUPLICATA`, nada é escrito |
| **conflito** | mesma chave, conteúdo diferente → **quarentena**, e o `original.xml` **nunca** é sobrescrito |
| origens | acumuladas no `captura.json`; **o documento não é duplicado** |
| concorrência | trava por (empresa, serviço, ambiente) |

Provado com CT-e reais: 5 `NOVO` → 5 `DUPLICATA` (mesmos bytes por outro
caminho) → `CONFLITO` em quarentena, com **5 originais no acervo** ao final —
nada sobrescrito, nada duplicado.

---

## D85 — O que NÃO foi verificado, e está declarado

Três limites desta etapa, ditos em vez de escondidos:

1. **A tabela de `cStat` foi conferida contra o manual da NF-e**, não do CT-e.
   É o ponto de partida, não uma verificação. Código fora dela cai em
   `CATEGORIA_NAO_DOCUMENTADA` e é tratado como definitivo — não avança
   checkpoint, não repete sozinho.

2. **O leiaute do evento de CT-e não foi conferido contra amostra real** — não
   há nenhum evento de CT-e no acervo. Na NF-e o `xJust` fica direto sob
   `detEvento` (conferido em documento real); no CT-e o leiaute prevê um
   invólucro `evCancCTe`. O parser busca **em profundidade**, o que acerta nas
   duas formas.

3. **A validade do certificado não é aferível** sem abrir o `.pfx` com a senha,
   o que esta etapa não faz. Os estados `VENCIDO` e `AINDA_NAO_VALIDO` existem
   no vocabulário e só são devolvidos quando alguém informa a data de fora.
   Prometer "válido" sem medir seria pior do que não ter o estado.

**Modelo 67 (CT-e OS) não é suportado.** Sem esquema ou amostra para conferir,
declarar suporte seria afirmar o que não foi verificado. Um modelo 67 é
identificado e preservado, e o parser registra o aviso.

---

# CTE 1.1 — Correção pela NT 2015.002 v1.05

A CTE 1 foi construída por analogia com a NF-e. A NT oficial do CT-e desfaz
cinco dessas analogias. As decisões abaixo substituem o que a analogia supunha.

## D86 — A distribuição não devolve ao emitente o que ele emitiu

O `CTeDistribuicaoDFe` entrega **documentos de interesse**: aqueles em que a
empresa figura como tomador, remetente, expedidor, recebedor ou destinatário.
O papel de **emitente não está nessa lista**.

Consequência medida: os **59 CT-e da 64567004\*\*\*** são de emissão própria
(todos com `emit` igual ao CNPJ da pasta). **Nenhum deles voltará pela
captura.** Esperá-los seria interpretar uma resposta correta como falha, e a
reação natural a essa falha imaginária é reconsultar — que é exatamente o
caminho para o 656.

A emissão própria entra pela **entrada canônica**, por importação de XML. É a
mesma porta, com procedência diferente: `ORIGEM_DISTRIBUICAO` e
`ORIGEM_IMPORTACAO` são registradas separadamente e deduplicam entre si pela
chave de acesso.

O limite está dito em três lugares onde a decisão é tomada: no serviço, no
adaptador e **na tela** — `emitidos_nao_vem_pela_captura` também vai no
diagnóstico, como dado, não como texto.

## D87 — A tabela de `cStat` é a do CT-e, não a herdada da NF-e

Substitui o ponto 1 da D85, que declarava a herança como limite conhecido.

A tabela agora tem exatamente os **28 códigos da NT 2015.002 v1.05**: 108, 109,
137, 138, 214, 215, 238, 239, 242, 252, 280, 281, 283–286, 402, 404, 409–411,
472, 473, 489, 490, 589, 593 e 656.

Dois erros que a herança cometia, e que a analogia esconderia:

- **Códigos que só existem no CT-e** (214, 239, 409–411, 472, 473, 489, 490,
  593) caíam em "não documentado" — inclusive o **490**, que é justamente quem
  revela a janela de retenção.
- **Códigos da NF-e que não valem aqui** (236, 578, 632, 111) seriam
  interpretados com significado emprestado. Foram removidos e agora caem em
  `CATEGORIA_NAO_DOCUMENTADA`.

O que não muda: **código fora da tabela não avança checkpoint**, não repete
sozinho, e mostra o texto literal do serviço. Não há palpite por faixa numérica.

## D88 — Fila esgotada espera uma hora; 656 espera uma pessoa

São dois bloqueios diferentes, e tratá-los como um só é o que produz consumo
indevido.

**Fila esgotada** (`ultNSU = maxNSU`, ou nenhum documento): não há o que buscar.
Nova consulta fica bloqueada por **no mínimo uma hora**
(`cooldown_fila_esgotada_segundos = 3600`). É o relógio que resolve, e ele
resolve sozinho.

**656 — consumo indevido**: não basta esperar. A empresa sai da automação e
fica com `revisao_sequencia` até revisão humana. O teste prova o ponto: **dois
dias** depois, ela continua barrada, e o estado devolvido é `EXIGE_REVISAO` —
não `BLOQUEADA`. O que a segura é a revisão, não o tempo.

Isso corrige a CTE 1, onde o 656 voltava à fila assim que o relógio passasse.

## D89 — `consNSU` recupera lacuna; não varre. E `consChCTe` não existe.

A NT prevê três formas de consulta, e o CT-e tem **duas**: `distNSU` (a
paginação) e `consNSU` (um NSU específico). **Não existe consulta por chave de
acesso** — `CONSULTA_POR_CHAVE_DISPONIVEL = False`, e pedir isso levanta
`ConsultaNaoSuportada` em vez de montar um envelope que a SEFAZ rejeitaria.

`recuperar_nsu()` serve **só a lacuna já identificada**. Duas travas:

- **O checkpoint não anda por ela.** Recuperar um buraco no meio não autoriza
  declarar em dia o que vem depois.
- **NSU zero é recusado.** Usar `consNSU` para varrer é `distNSU` disfarçado,
  com o custo de uma chamada por documento.

## D90 — `ultNSU = 0` não significa "todo o histórico"

São **três meses** de retenção no Ambiente Nacional (`RETENCAO_MESES = 3`). O
que é mais antigo que isso não existe para a distribuição, e nenhuma quantidade
de paginação o traz de volta.

Isso é dito ao operador, não só ao código: `AVISO_JANELA` aponta a **importação
de XML** como o caminho do que ficou fora, e o diagnóstico expõe
`janela_retencao_meses` junto do resto.

Sem essa declaração, o histórico anterior à janela pareceria uma captura
incompleta — e a reação a uma captura aparentemente incompleta é reconsultar.

## D91 — CT-e OS, CT-e Simplificado e GTV-e: preservar sem fingir que se lê

A distribuição entrega outras espécies pela mesma porta. Elas são
**identificadas** (`CTE_NAO_SUPORTADO`), gravadas com os **bytes originais
intactos** e recebem **identidade forte** — deduplicam contra si mesmas na
consulta seguinte, em vez de reentrar a cada página.

O que não fazem é atravessar o parser. O aviso diz que o leiaute não foi
implementado.

A alternativa — descartar o que não se sabe ler — perderia o documento em
silêncio e, pior, sem registro de que ele existiu. Preservar é o que permite
implementar o leiaute depois **sem reconsultar a SEFAZ**.

Substitui a nota final da D85, que tratava só do modelo 67.

---

# CTE 2 — Primeiro ciclo real

A primeira consulta real ao `CTeDistribuicaoDFe` foi feita em **02/09/2026**
para a Cliente L, com `ultNSU=000000000000000`. O serviço respondeu **138 com 22
documentos**. Nenhum foi gravado: o caminho de persistência tinha três defeitos
que só existem quando a consulta dá certo — e por isso nenhuma fase anterior os
viu.

## D92 — A persistência do CT-e passa pela entrada canônica, não pelo pipeline

A CTE 1 mandava o lote a `pipeline.ingerir()`, "para usar o mesmo portão da
NF-e". Estava errado por dois motivos, e o primeiro escondia o segundo:

1. `ingerir()` **não tem parâmetro `servico`**. A chamada levantava `TypeError`
   e caía no `except` como falha de persistência — um erro de digitação
   vestido de problema fiscal.
2. Consertar o argumento seria pior. O `DistribuicaoRunner` que o `ingerir`
   usa **também é dono do checkpoint**. Passar por ele daria **dois donos ao
   mesmo ponteiro**: ele avançaria no passo 4 e o passo 5 avançaria de novo.

`entrada_cte.da_distribuicao` grava, deduplica pela chave e **não toca no
checkpoint**. O dono continua sendo um só.

## D93 — Trava ocupada não é conflito de documento

`entrada_cte` devolvia `CONFLITO` quando a trava da empresa estava ocupada.
Mas `CONFLITO` é um fato sobre o **documento** — mesma chave, conteúdo
divergente — e manda alguém conferir. Trava ocupada é um fato sobre a
**máquina**, e some sozinho quando a outra captura termina.

O estrago não era só de vocabulário. `consultar_empresa` já segura a trava
quando chama a entrada; a entrada pedia a mesma trava, não conseguia, e
devolvia um veredito por documento. Com a contagem errada da D94, isso fazia o
checkpoint **avançar sobre 22 documentos que nunca foram gravados**.

Veredito novo: `CAPTURA_EM_CURSO`. E `consultar_empresa` passa `travar=False`,
porque a trava já está na mão dele.

## D94 — Persistido é só o que foi gravado

`persistidos` conta **apenas** o que a entrada aceitou: novo, duplicata,
promovido, cópia menor.

Conflito **não** conta. Parece generoso contar — o arquivo está lá, afinal —
mas o conflito espera conferência humana, e declarar aquele NSU resolvido antes
disso é avançar por cima de um documento que ninguém olhou. Sem `consChCTe`,
não há como voltar para buscá-lo.

Escrevi a regra errada primeiro (conflito contava) e o teste do ciclo completo
a derrubou na primeira execução, com o acervo vazio e o checkpoint avançado.

## D95 — Sem cadastro e sem certificado são respostas diferentes

`elegivel()` chamava `cadastro.buscar()`, **que não existe**, e o
`except Exception` transformava o `AttributeError` em "empresa sem certificado
no cadastro". Toda empresa passada com `cad=` era recusada, **inclusive as que
tinham certificado válido** — a Cliente L entre elas.

Um erro de programação saindo como veredito fiscal, indistinguível do caso
real. Agora são dois estados: `SEM_CADASTRO` (nem empresa há) e
`SEM_CERTIFICADO` (empresa existe, credencial não).

## D96 — O teste que faltava era o do caminho que dá certo

Os quatro defeitos acima moram todos **depois** do `cStat 138` com documentos.
A CTE 1 e a CTE 1.1 testaram fartamente 137, 656, 490, espécies não suportadas
e bloqueios — e nenhuma vez o ciclo inteiro com documentos no lote.

O caminho de sucesso é o único que roda todo dia. Ele agora tem teste: dois
documentos entram, gravam, o checkpoint anda **depois**, a segunda passada
deduplica em vez de duplicar, e um documento ilegível **segura** o checkpoint.


## D97 — Arquitetura oficial: Sistema Web + Auth, somente pela rede privada

**Data:** 14/09/2026 · **Fase:** portal · **Decisão vinculante**

O FISCALE **é um Sistema Web autenticado**, e não um programa instalado na
máquina de quem usa. O navegador é o cliente; o servidor fica no escritório e
guarda dados, certificados A1 e segredos.

```
navegador
   ↓
HTTPS
   ↓
app.sistemafiscale.com.br
   ↓
REDE PRIVADA
   ↓
AUTH
   ↓
CENTRAL
   ├── FISCALE
   ├── ELO
   └── Administração
```

Proteção de acesso externo: **REDE PRIVADA + HTTPS + AUTH**. MFA não está
implementado e não faz parte da arquitetura atual.

> Nota futura: MFA poderá ser considerado futuramente caso a política de
> acesso externo seja alterada.

**O que fica decidido:**

1. **Sistema Web + Auth.** O Auth é a camada única de autenticação: login,
   logout, sessão, expiração, "Manter conectado", redefinição de senha feita
   pelo administrador, papéis, identificação do usuário, auditoria e controle
   de acesso às aplicações. FISCALE, ELO e Administração **não têm
   autenticação própria**. O ELO recebe o bilhete de 90 segundos emitido pelo
   Auth e cria a sessão dele a partir desse bilhete — nunca pede senha.
   Hoje o Auth são os módulos `fiscale_usuarios`, `fiscale_senhas`,
   `fiscale_pimenta`, `fiscale_tentativas`, `fiscale_sessoes`,
   `fiscale_papeis`, `fiscale_auditoria`, `fiscale_elo` e `fiscale_proxy`,
   servidos pelo `fiscale_server.py`. Esta decisão dá nome ao que já existe;
   não pede reescrita.
2. **Endereço do sistema:** `app.sistemafiscale.com.br`. Resolve a D-HTTPS-1
   do `FISCALE_HTTPS.md`.
3. **Site institucional:** `www.sistemafiscale.com.br` continua sendo o site, e
   **não** é substituído pelo sistema.
4. **Somente pela rede privada.** O `app.` só é alcançável de dentro da rede
   privada do escritório. **Não** é publicado na internet. O certificado HTTPS
   é emitido por desafio de DNS justamente para não precisar expor a máquina.
   A rede privada é **pré-requisito** do `app.`, não alternativa.
5. **HTTPS obrigatório**, também dentro da rede privada.
6. **A 8777 é porta interna.** Nunca é endereço público de acesso: o proxy
   HTTPS fala com ela por 127.0.0.1. Enquanto o HTTPS não é implantado, a rede
   local do escritório continua usando a 8777 em HTTP, como hoje — isso é uso
   interno, não acesso externo.
7. **PWA é instalação opcional** do Sistema Web (Edge/Chrome). O sistema
   funciona inteiro pelo navegador e **nada depende do PWA**. O código da
   Fase 3 (manifest, `sw.js`, `offline.html`, `fiscale-pwa.js`) permanece.
8. **Central por papel.** Operador vê FISCALE e ELO; administrador vê
   FISCALE, ELO e Administração. Domínio não aparece enquanto não existir
   integração oficial.

**Decisões preservadas — e por que esta não conflita com elas:**

- **D72 continua valendo integralmente.** Nada é publicado na internet,
  nenhuma porta é aberta no roteador, a 8777 nunca é pública, e o acesso de
  fora do escritório é só por rede privada com HTTPS. A D97 é a D72 com nome e
  endereço.
- **D80 continua valendo integralmente.** Senha de 8 caracteres não libera
  acesso externo. A camada adicional que ela exige, aqui, é a **rede
  privada** — por isso **não se implementa MFA agora**. Se algum dia o `app.`
  deixar de ser só rede privada, a D80 volta a exigir MFA antes, e isso será
  decisão nova, não releitura desta.

**Linguagem do produto.** "FISCALE — Plataforma Fiscal do Escritório"; quando
for preciso distinguir, "Sistema Web FISCALE". Evitar "programa instalado",
"executável" ou "abrir o atalho" como descrição do produto.

**Não decidido aqui (pendências de implantação, em `FISCALE_HTTPS.md`):** DNS
com API (D-HTTPS-2), qual rede privada (D-HTTPS-3), instalação do proxy,
firewall do servidor, confiança explícita no proxy e o ELO atrás do HTTPS.
Nada disso foi instalado ou configurado por esta decisão.


## D-AMB-1 — Ambientes: AMMOURIM como desenvolvimento, homologação e produção provisória

**Data:** 15/09/2026 · **Fase:** infraestrutura · **Decisão vinculante**

O **AMMOURIM** é, por ora, o ambiente de **desenvolvimento, teste, homologação
e produção provisória** do FISCALE. A **produção definitiva** vai para um
**servidor dedicado**, preparado depois, quando o produto estiver maduro. A
migração segue o roteiro: `.fbk` final → servidor definitivo → restauração →
recadastro das senhas protegidas por DPAPI → validação → rede privada → HTTPS.

**O que fica decidido:**

1. **Produção provisória:** dados em `C:\Users\nineq\Fiscale\dados`, porta
   **8777**, iniciada no logon pelo `Iniciar Fiscale.vbs`. Teste destrutivo
   contra essa pasta, só com aprovação explícita.
2. **Homologação:** instância separada, porta **8898**, dados em
   `C:\Fiscale\homologacao\dados`, subida por `homologar_fiscale.bat` — que
   recusa pasta não definida, a pasta real e a porta da produção. Ela é
   criada vazia e recebe dados **somente por restauração de `.fbk`**, nunca por
   cópia da pasta real: numa cópia crua as senhas de certificado (DPAPI da
   mesma conta do Windows) continuariam abrindo.
3. **Teste:** `testar_fiscale.bat` (8899) e as suítes com pasta temporária e a
   trava `FISCALE_TESTE_PROIBIR_RAIZ_REAL`. Dado de teste é fictício: CNPJ,
   certificado e senha inventados.
4. **Servidor definitivo:** usado **posteriormente** para a produção
   definitiva. Rede privada, proxy HTTPS e firewall definitivo ficam para essa
   implantação.

**Decisões preservadas:** D63, D72, D80 e D97 continuam valendo. A rede à qual o
AMMOURIM está conectado hoje **não** é tratada como rede do escritório nem como
base de confiança do FISCALE: a rede privada da D97 será montada no servidor
definitivo.
