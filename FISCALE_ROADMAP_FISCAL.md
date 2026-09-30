# Fiscale — roadmap fiscal

Atualizado em **11/08/2026**, depois da auditoria completa e do HEALTH 1.

---

## Situação

| Fase | O quê | Estado |
|---|---|---|
| **F0** | Auditoria integrada (DF-e, saúde, REINF, assinatura, baseline) | ✅ **COMPLETA** — 11/08/2026 |
| **HEALTH 1** | Importador + parsers Bradesco e SulAmérica + conferência | ✅ **ENTREGUE** — 11/08/2026 |
| **HEALTH 2** | Movimentação entre competências, mais operadoras | ⬜ |
| **DFE 1** | Robustez da captura: `cStat`, retry, backoff, manifestação | ⬜ |
| **DFE 2** | Documento normalizado + itens persistidos | ⬜ |
| **REINF 1** | Modelo, XSD oficial, builders — **sem transmitir** | ⬜ |
| **REINF 2** | Assinatura (parametrizar `assinar_xml` para SHA-256) | ⬜ |
| **REINF 3** | Transmissão em **produção restrita** | ⬜ |
| **REINF 4** | Retornos, recibos, histórico | ⬜ |
| **REINF 5+** | Fechamento, retificação, exclusão | ⬜ |

---

## Fora de fase — dois itens que não deveriam esperar

### S1 · Senha de portal em texto claro 🔴

`~\Fiscale\dados\state_plano.json` guarda
`empresasPlano[].acesso = {"Usuário":"MASTER","Senha":"<REDIGIDO>"}` sem proteção, e
o `backup_para_drive.bat` copia esse arquivo para o Google Drive.

`seguranca.py` já faz DPAPI para senha de certificado. É aplicar o mesmo
caminho — trabalho pequeno, exposição grande.

### S2 · A área fiscal não tem teste 🔴

Antes do HEALTH 1 havia **2 suítes**, ambas sobre sessão e ELO. Zero sobre
NFS-e, NF-e, DF-e, classificador, apuração federal ou assinatura. O
`teste_saude.py` (144 asserções) é o primeiro teste fiscal do projeto.

Mexer em `nfe.py` ou `classificador.py` hoje é no escuro. Um teste de
caracterização do `classificador` — congelando o DAS já conferido contra a guia
real da Monte (R$ 8.972,55) — travaria a regressão mais cara que existe aqui.

---

## Próxima fase recomendada: **HEALTH 2**

**Por quê, e não DFE 1 ou REINF 1:**

O HEALTH 1 entregou a base, mas com **uma competência de cada empresa**. O valor
que o escritório sente aparece quando há **duas competências seguidas**: entrada
e saída de vida, reajuste, dependente novo, troca de plano. A estrutura para isso
já está no lugar (chave técnica estável, histórico por competência); falta a
camada de comparação e a tela que a mostra.

E há um motivo prático: o REINF 1 precisa de dados de **vários meses** para ser
exercitado com honestidade. O HEALTH 2 produz esses meses.

### Escopo do HEALTH 2

1. **Movimentação entre competências** — entrou, saiu, reajustou, mudou de
   plano, mudou de titular. Comparação de duas competências, com o motivo.
2. **Fechar a dúvida da coparticipação do Bradesco** (D24) — depende de uma
   fatura com "Part. Seg." preenchida.
3. **Parser de mais uma operadora** — a que aparecer amostra primeiro.
4. **Exportação da competência conferida** (CSV/XLSX) — é o que o contador pede
   hoje e o que a camada REINF vai consumir amanhã.
5. **Avaliar SQLite** — só se o volume real pressionar. Hoje o JSON por empresa
   dá conta; trocar antes da dor é custo sem retorno.

---

## Quando chegar no REINF 1 — o que já está resolvido

Anotado aqui para não ser pesquisado de novo (fonte: Manual do Desenvolvedor
**2.7**, 01/10/2025; leiaute **2.1.2b**; Manual do Usuário **2.1.2.1**):

- **Série R-4000 é lote assíncrono via API REST.** A v2.7 removeu o modelo
  síncrono.
- Envio `POST https://reinf.receita.economia.gov.br/recepcao/lotes`
  · restrita `https://pre-reinf.receita.economia.gov.br/recepcao/lotes`
- Consulta `GET …/consulta/lotes/{numeroProtocolo}`
- `application/xml`, UTF-8, **máx. 54 MB**; certificado de cliente obrigatório; **TLS 1.2+**
- Namespace do lote:
  `http://www.reinf.esocial.gov.br/schemas/envioLoteEventosAssincrono/v1_00_00`
- HTTP **201** ok · **413** tamanho · **415** media type · **422** inconsistência
  · **429** excesso · **495/496** certificado · **500** interno
- **Assinatura:** enveloped · **RSA-SHA256** · digest **SHA-256** · transformações
  enveloped-signature + **C14N inclusiva `REC-xml-c14n-20010315`** · KeyInfo só
  com `X509Certificate` · cadeia **EndCertOnly** · certificado ICP-Brasil série A
  (A1/A3), e-CNPJ da **matriz** ou procurador habilitado no e-CAC
- ⚠️ **Resolução ICP-Brasil 179**: certificado de raiz v10 sem "não repúdio" serve
  só para autenticar — não assina. Passar no mTLS não garante poder assinar.
- Situação do evento: `1-Ativo` · `2-Retificado` · `3-Excluído`
- Regra do manual: respeitar a precedência dos eventos e **não enviar durante o
  processamento do fechamento**

**Regra que não se negocia:** fechamento é evento transmitido e confirmado pela
Receita. **Nunca** marcar competência como fechada por decisão local.

---

## Débitos técnicos abertos

| # | Débito | Onde | Grav. |
|---|---|---|---|
| 1 | Senha de portal sem DPAPI | `state_plano.json` | 🔴 |
| 2 | `cStat` não tratado vira "0 notas novas" | `nfe.py:80-105` | 🔴 |
| 3 | Sem teste na área fiscal (fora do `saude/`) | projeto | 🔴 |
| 4 | Sem retry nem backoff exponencial na distribuição | `nfe.py:72-106` | 🟡 |
| 5 | `except Exception: pass` engolindo XML corrompido | `nfe.py` (6 pontos) | 🟡 |
| 6 | `tpAmb=1` fixo, sem homologação | `nfe.py:48` | 🟡 |
| 7 | `cUF="26"` como padrão | `nfe.py:56` | 🟢 |
| 8 | Manifestação do destinatário não existe | DF-e | 🟡 |
| 9 | Lista de legalidade cortada em 300 itens | `auditor_nfe.py:352` | 🟡 |
| 10 | Divergência do auditor não é persistida (não dá para marcar "revisado") | `auditor_nfe.py` | 🟡 |
| 11 | Coparticipação do Bradesco: falta amostra que decida se entra no total técnico | `saude/parsers/bradesco.py` | 🟡 |
| 12 | NT 01/2026 e 02/2026 não confirmadas na fonte oficial | REINF | 🟢 |
| 13 | Duas cópias divergentes de `state_plano.json` | `dados/` | 🟢 |

---

# FASE ING — Motor de Ingestão Fiscal (proposta, aguardando aprovação)

Projeto em `FISCALE_INGESTAO_ARQUITETURA.md`; decisões D27–D31 em
`FISCALE_DECISOES_FISCAIS.md`. Nada começa sem aprovação explícita.

| Fase | Escopo | Risco | Situação |
|---|---|---|---|
| ING 1 | `identidade.py` + `cofre.py` — fonte única de CNPJ (hoje em 5 lugares) e de sessão mTLS (hoje em 3). Sem mudança de comportamento. | baixo | proposta |
| ING 2 | `checkpoint.py` com ambiente na chave + migração dos dois formatos atuais | médio — mexe em estado de produção | proposta |
| ING 3 | Contrato `Conector`; NF-e e NFS-e migram para ele **sem reescrita de lógica** | médio | proposta |
| ING 4 | `cte_dfe.py` — conector novo de CT-e (`CTeDistribuicaoDFe`) | médio | proposta |
| ING 5 | Índice reconstruível dos documentos + tela unificada | baixo | proposta |

Sugestão: começar pela **ING 1** — é a única sem risco de estado e já paga
sozinha, removendo oito duplicações.

**Lacuna que a fase fecha:** hoje o CT-e só entra por importação manual de XML
(`imp-{chave}.xml`); não existe cliente de distribuição. São 59 CT-e em disco
contra 23.450 NF-e.

**Limite oficial a comunicar ao usuário:** com `ultNSU=0` o Ambiente Nacional
devolve apenas os últimos 3 meses (schema `PL_CTeDistDFe_100`). Empresa nova não
recupera histórico completo por distribuição — mesma situação já aceita para a
NFS-e, resolvida por lançamento à mão.

## ING 1 — CONCLUÍDA em 13/08/2026

Relatório completo em `FISCALE_ING1.md`. Decisões D32–D34.

Entregue: pacote `nfse/backend/ingestao/` (identidade canônica de empresa,
modelo Empresa ↔ Credencial com motivo do vínculo, cadastro unificado sobre os
dois JSON, fábrica única de sessão mTLS, ambiente explícito, contratos oficiais
como dado verificável), `reconciliar_empresas.py`, `teste_apoio.py` e 102 testes
novos. Todas as suítes verdes.

Reconciliação dos dados reais: **22 empresas canônicas** — 13 CONFIRMADO,
8 SOMENTE_CERTIFICADO, 1 SOMENTE_CLIENTES, **0 ambíguo, 0 inválido**.

Pendências levantadas e NÃO corrigidas (decisão do usuário):

- 8 empresas com certificado e sem cadastro de cliente (sem regime, UF, IE, IM);
- 1 cliente sem certificado (não pode ser consultado no fisco);
- 🔴 1 certificado **vencido em 29/07/2026** (DI CAVALCANTI ADVOGADOS
  ASSOCIADOS) — enquanto não renovar, essa empresa não consulta nada.

Dívida declarada: as cópias antigas de normalização de CNPJ (5) e de sessão mTLS
(3) continuam no código. Trocá-las é a ING 3 — fazer agora significaria mexer em
NF-e e NFS-e em produção sem necessidade direta.

## ING 2 — CONCLUÍDA em 13/08/2026

Relatório completo em `FISCALE_ING2.md`. Decisões D35–D41.

Entregue: `checkpoint.py` (estado por identidade+serviço+ambiente, escrita
atômica, corrupção sem reset a zero), `distribuicao.py` (`DistribuicaoRunner`
genérico, paradas explícitas, limite de lotes), `trava.py` (concorrência),
`credencial_estado.py` (seis estados), `limpar_temporarios.py`, e 173 testes
novos. **775 asserções verdes, 0 falhas.**

🔴 **Defeito real pego pelo teste:** o atalho "já está em dia" faria a empresa
parar de buscar documentos para sempre, porque o `maxNSU` guardado é foto da
última resposta. Corrigido (D40).

Higiene: as suítes não deixam mais pastas em `%TEMP%` (zero numa rodada
completa). Restam **106 pastas antigas** identificadas — `limpar_temporarios.py`
lista; apagar é decisão do usuário.

Pendência operacional: **DI CAVALCANTI ADVOGADOS, certificado vencido em
29/07/2026.** Nada foi alterado.

**Próxima: ING 3** — migrar NF-e e NFS-e para o contrato `Conector`, sem
reescrever a lógica. Depois a ING 4 implementa a fonte real do CT-e.
