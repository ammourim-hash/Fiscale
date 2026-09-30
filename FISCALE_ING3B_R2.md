# ING 3B-R2 — checkpoint local × observado, e trilha de CONSULTA

**Implementado e verde. Nenhuma consulta real executada nesta fase.**

- **Data:** 13/08/2026
- **Origem:** a consulta real da R1 (`cStat 656`, `ultNSU` devolvido = 1361,
  acervo local = 0). Ver `FISCALE_ING3B.md` §12-R1.
- **Baseline:** 1.109 → **1.241 asserções, zero falhas**.

---

## 1. A distinção que a fase inteira existe para impor

```
checkpoint_acervo           o último NSU cujos documentos estão COMPROVADAMENTE
                            no acervo. Só anda com persistência confirmada.

checkpoint_sefaz_observado  a posição que o Ambiente Nacional informou.
                            É notícia sobre o mundo, não prova de posse.

estado_sincronismo          SEM_INFORMACAO | EM_SINCRONIA | DIVERGENCIA_EXTERNA
```

Se `ult_nsu` tivesse absorvido 1361, o FISCALE passaria a afirmar que possui
1.361 documentos que nunca viu — **e a pular todos eles para sempre**, porque a
Distribuição DF-e só anda para frente e retém cerca de 3 meses.

O estado é gravado no checkpoint, sobrevive à releitura, e entra no `.fbk`.

### O que impede o atalho voltar

`Checkpoint.registrar_observacao_sefaz()` é o **único** caminho para gravar a
posição remota, e ele não toca em `ult_nsu`. Além disso:

- `pipeline.aplicar_divergencia()` confere, depois de gravar, que `ult_nsu` não
  mudou — e levanta se mudou (cinto e suspensório);
- um teste varre a árvore sintática do pacote inteiro e falha se aparecer
  qualquer `checkpoint.ult_nsu = <algo vindo de resposta>`.

---

## 2. Diagnóstico é separado de estado

| | |
|---|---|
| **Estado** | `DIVERGENCIA_EXTERNA` — fato: os números divergem |
| **Diagnóstico** | `POSSIVEL_CONSUMIDOR_EXTERNO` — hipótese sobre a causa |

O diagnóstico só é marcado quando as quatro condições ocorrem juntas: consulta
enviada com NSU X · `cStat 656` · a mensagem manda usar outro `ultNSU` · o
`ultNSU` devolvido é **maior** que o checkpoint comprovado local.

**Alerta administrativo, texto exato:**

> O Ambiente Nacional informa uma posição de NSU superior ao histórico conhecido
> pelo FISCALE. Outro sistema pode estar consultando a Distribuição DF-e deste
> CNPJ.

Ele **não nomeia nenhum sistema**, e há teste conferindo que não aparecem
"Domínio", "ERP" nem nome de concorrente. Concluir qual aplicação é responsável
exige informação que o FISCALE não tem.

---

## 3. Cobertura do passado

```
COMPLETA_DESDE_ZERO   padrão de quem nunca divergiu
DESCONHECIDA          assim que uma divergência aparece
A_PARTIR_DE_MARCO     depois do INICIO_COBERTURA_DFE
```

Assim que a divergência é registrada, a cobertura cai automaticamente para
`DESCONHECIDA`. O FISCALE deixa de prometer o que não baixou — e isso é uma
mudança de *afirmação*, não de dado.

### Marco `INICIO_COBERTURA_DFE`

Gravado **antes** da primeira consulta que use o NSU indicado pela SEFAZ.
Contém empresa (mascarada), serviço, ambiente, data/hora, checkpoint local
anterior, NSU informado pela SEFAZ, `cStat` de origem, motivo, e o aviso:

> documentos anteriores a este marco podem NÃO existir no acervo FISCALE; a
> cobertura do período anterior é DESCONHECIDA e só pode ser preenchida por
> importação de XML de outra origem

É o registro que impede alguém, daqui a dois anos, de olhar o acervo e concluir
que não havia notas antes daquela data.

### Histórico 1–1361

**Não será reconstruído por `distNSU`.** Se aparecerem XMLs históricos — do
Domínio, de pasta local, de backup, do fornecedor ou entregues pelo cliente —
eles entram no mesmo acervo pela ING 3A, com `origem` diferente registrada em
`captura.json`, **sem alterar o histórico da Distribuição DF-e**. O acervo já
suporta isso: a identidade é a chave de acesso, e a proveniência é por cópia.

---

## 4. Trilha de `CONSULTA`

Um ato novo, gravado por **tentativa** de aquisição — com ou sem documento.

Campos: `inicio`, `fim`, `empresa` (mascarada), `servico`, `ambiente`,
`tipo_consulta`, `nsu_enviado`, `endpoint` (host lógico), `transporte_ok`,
`cstat`, `xmotivo` (sanitizado), `ult_nsu`, `max_nsu`, `doczip`, `resultado`,
e — quando houver — `erro_tecnico`, `erro_classe`, `avarias`,
`divergencia_externa`, `nsu_observado_sefaz`, `duracao_s`.

Testado para os sete desfechos: `137`, `138`, `656`, timeout, TLS, SOAP
inválido e falha HTTP. Cada chamada do laço vira **uma** linha, com o NSU que
*aquela* chamada enviou.

**Nunca guarda** senha, certificado, XML, CNPJ completo ou URL inteira — o
endpoint vai como host.

### Onde isso mora, e por quê

O conector **não importa** o módulo de auditoria — a fronteira da ING 3B
continua valendo. Ele acumula as tentativas como **dado puro** em
`fonte.tentativas`; quem persiste é o `pipeline`. Produzir e persistir são
responsabilidades diferentes.

---

## 5. Proteção contra dois consumidores

**Interna:** a trava da ING 2 já garante um fluxo de `distNSU` por vez para cada
`(CNPJ, serviço, ambiente)`. Conferido por teste, inclusive que empresas
distintas não se bloqueiam.

**Externa: não existe trava possível.** Nenhum lock nosso impede o Domínio ou
outro ERP de consultar a SEFAZ. Por isso a detecção é **comportamental** — pelo
NSU —, e não por exclusão mútua. É o melhor que dá para fazer, e reconhecer o
limite faz parte do desenho.

---

## 6. Estado da MONTE com os dados já obtidos (sem rede)

Simulação somente-leitura sobre o checkpoint gravado em 13/08. **Nada foi
gravado** — conferido relendo o arquivo depois.

| | |
|---|---|
| `checkpoint_acervo` | `000000000000000` |
| `checkpoint_sefaz_observado` | `000000000001361` |
| `distancia_para_sefaz` | **1361 NSU** |
| `estado_sincronismo` | `DIVERGENCIA_EXTERNA` |
| `diagnostico` | `POSSIVEL_CONSUMIDOR_EXTERNO` |
| `cobertura_anterior` | `DESCONHECIDA` |

E o marco que seria gravado antes da próxima consulta:

```
marco                     : INICIO_COBERTURA_DFE
empresa                   : 04.***.***/0001-85
servico                   : NFE_DISTRIBUICAO
ambiente                  : producao
checkpoint_local_anterior : 000000000000000
nsu_informado_sefaz       : 000000000001361
cstat_de_origem           : 656
cobertura_anterior depois : A_PARTIR_DE_MARCO
ult_nsu (ACERVO) depois   : 000000000000000   ← NÃO mudou
```

> **O checkpoint real em disco continua sem esses campos.** Eles só serão
> gravados numa execução autorizada — a simulação acima não persistiu nada.

---

## 7. Bug encontrado pela própria simulação

`Checkpoint.de_json()` entregava ao objeto a **mesma lista** `observacoes` do
dicionário de origem. Um `append` depois alterava o dicionário de quem chamou, à
distância: o dado "lido do disco" mudava sozinho em memória.

Apareceu porque a simulação comparava o dicionário antes e depois para provar
que nada tinha sido gravado — e a comparação falhou sem que o arquivo tivesse
mudado. Corrigido com cópia dos mutáveis, e coberto por teste.

Não era exploração hipotética: qualquer tela que carregue um checkpoint, exiba o
JSON e depois anexe uma observação mostraria dado inconsistente.

---

## 8. Baselines

| Suíte | Antes | Depois |
|---|---|---|
| segurança / backup / portabilidade / saúde | 116 · 141 · 102 · 144 | **iguais** |
| ING 1 / ING 2 / ING 3A / ING 3B | 102 · 173 · 193 · 138 | **iguais** |
| **`teste_ingestao3b_r2.py`** | — | **132** |
| **Total** | 1109 | **1241 · 0 falhas** |

---

## 9. Arquivos

**Criados:** `teste_ingestao3b_r2.py`, `FISCALE_ING3B_R2.md`.

**Alterados, todos dentro de `ingestao/`:**

| Arquivo | O quê |
|---|---|
| `checkpoint.py` | campos e estados de sincronismo; `registrar_observacao_sefaz`, `marcar_possivel_consumidor_externo`, `marcar_inicio_cobertura`; correção do aliasing em `de_json` |
| `auditoria.py` | ato `CONSULTA`, `CAMPOS_CONSULTA`, `registrar_consulta()`, `consultas()` |
| `conectores/nfe_dfe.py` | `tentativas` como dado puro; detecção de divergência |
| `pipeline.py` | `registrar_tentativas()`, `aplicar_divergencia()`; campos novos no resultado |

Compatibilidade: checkpoints antigos, sem os campos novos, continuam legíveis e
recebem os padrões — coberto por teste.

---

## 10. Pergunta operacional — para você responder, não para o código descobrir

**Existe no Domínio, ou em outro software usado hoje pelo escritório, uma rotina
ativa de captura automática de NF-e ou de Manifestação do Destinatário para o
CNPJ da MONTE?**

Se sim, registrar:

- [ ] qual sistema;
- [ ] usa Distribuição DF-e (`NFeDistribuicaoDFe`) ou outro caminho?
- [ ] com que frequência;
- [ ] pode ser desligado?
- [ ] atende outras empresas do escritório além da MONTE?
- [ ] precisamos coexistir com ele?

**Nenhuma configuração de sistema externo será alterada sem sua aprovação.**

A resposta muda o desenho da fase seguinte: se houver um consumidor externo
permanente, o FISCALE precisa conviver com ele — e o marco de cobertura passa a
ser a regra, não a exceção.

---

## 11. Próximo passo

**Parado, como combinado. Nenhuma consulta real.**

Quando for autorizado, a próxima consulta seria: gravar o
`INICIO_COBERTURA_DFE`, e então uma única `distNSU` a partir de
`000000000001361` — com a cobertura anterior declarada `DESCONHECIDA` desde o
início, sem alegar posse do passado.

**CT-e continua proibido.**
