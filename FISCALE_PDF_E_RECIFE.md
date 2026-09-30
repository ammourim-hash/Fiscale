# Dossiê de situação fiscal e o módulo do Recife

*Decidido em 11/09/2026.*

Duas entregas com uma regra em comum, e um diagnóstico que fecha uma porta de
propósito.

---

## 1 · O que NÃO se constrói: renderizador de documento oficial

O pedido original era gerar PDFs **visualmente idênticos aos dos órgãos**.
Isso foi descartado, e a razão precisa ficar registrada porque ela vai voltar:

**Um documento que imita a aparência de uma certidão da Receita, mas foi
desenhado por nós, é uma falsificação** — mesmo feito de boa-fé, mesmo para
uso interno. Basta um deles chegar a um banco, a uma licitação ou a um cliente
para virar documento falso, e isso não exige má intenção: basta alguém
encaminhar um anexo.

No caso federal seria, além de tudo, **desnecessário**: o SITFIS entrega o PDF
verdadeiro da RFB, que o envelope guarda byte a byte. Redesenhá-lo seria trocar
o documento que vale por uma cópia que não vale.

### A distinção com o `pdflocal.py`, que é legítimo

O `pdflocal.py` reproduz o layout do DANFSe, e isso **não** é o mesmo caso:

| | NFS-e | Certidão / extrato |
|---|---|---|
| O documento legal é | o **XML** | o **próprio PDF** |
| O que o PDF é | documento **auxiliar** (impressão) | o original |
| Existe original nosso para representar? | sim, o XML | não |

O DANFSe é uma representação de algo que possuímos legitimamente, e carrega no
rodapé a marca de que foi gerado localmente. Uma certidão não tem representação
— tem original, ou não tem nada.

---

## 2 · O que se constrói: compilação, não redesenho

**Anexar** o PDF oficial dentro de uma compilação é juntada de documento — o
que qualquer processo faz. **Redesenhar** é outra coisa. O motor compila.

    situacao/relatorio.py

      dossie(...)   1 PDF por empresa:
                    capa + índice + os PDFs ORIGINAIS como páginas

      lote(...)     1 PDF do escritório:
                    capa + quadro consolidado + um bloco por empresa

Ferramentas: `pypdf` faz o merge, `fpdf2` desenha capa e índice. Ambos já
estavam instalados; nenhuma dependência nova.

### REGRA ESTRITA: sem marca do sistema, em lugar nenhum

O nome do sistema **não aparece** no PDF gerado. Não na capa, não no rodapé,
não no título, e **não nos metadados** — que é onde ele escaparia sem ninguém
ver: o `fpdf2` grava `Producer` por padrão, e o `pypdf` propaga o que encontra.

Os metadados do documento final são limpos explicitamente, e há teste que
varre os **bytes inteiros** do PDF procurando a marca. Regra que não é
testada é intenção, não regra.

O que aparece na capa:

    Relatório de Situação Fiscal
    <razão social>
    CNPJ/CPF <documento>
    Emitido em <data>

    <quadro da situação por esfera e tipo>
    <índice dos documentos anexados>

Títulos genéricos, layout neutro, e só dado da contabilidade e do cliente.

### O que a capa mantém, e por quê

Cada documento anexado leva **procedência**: origem, data de captura e
SHA-256. Isso não é assinatura do sistema — é dado sobre o documento do
cliente, e é justamente o que torna a compilação defensável se alguém
questionar de onde veio aquele PDF. Sem isso, o dossiê é um amontoado de
páginas sem cadeia de custódia.

### Duas recusas que o motor pratica

**Documento que não confere não é anexado.** O envelope promete que o original
não muda; anexar um arquivo cujo hash divergiu seria quebrar essa promessa no
lugar mais visível. Ele é omitido, e a capa diz que foi omitido.

**PDF que o `pypdf` não abre não derruba o dossiê.** Ele é registrado como não
anexável e o resto segue. Relatório que falha inteiro por causa de um arquivo
ruim é relatório que ninguém consegue emitir no dia em que mais precisa.

---

## 2.1 · Os endpoints, e por que eles devolvem cabeçalho

    GET /api/situacao/dossie?identidade=<CNPJ/CPF>   → application/pdf
    GET /api/situacao/lote                           → application/pdf

Os dois são **GET**, servidos pelo próprio Fiscale (não pelo proxy do NFS-e), e
exigem sessão — sem cookie respondem **401**, porque o corpo é documento de
cliente. `dossie` sem `identidade` responde **400**. O `lote` varre o cadastro
inteiro, ordenado por nome.

Além do PDF, as duas respostas trazem três cabeçalhos:

| Cabeçalho | Para que serve |
|---|---|
| `X-Anexados` | quantos originais entraram no PDF |
| `X-Omitidos` | quantos existiam mas ficaram de fora (hash divergente ou PDF ilegível) |
| `X-Empresas` | quantas empresas o documento cobre (1 no dossiê, N no lote) |

**`X-Anexados` existe por uma razão específica:** o navegador não conta páginas
de um PDF. Sem esse número, um dossiê de **capa sozinha** — empresa em *Sem
documento* nas seis células — desceria calado, e a pessoa só descobriria ao
abrir, possivelmente na frente do cliente. Com ele, a tela avisa antes.

### O nome do arquivo também é superfície white-label

    situacao-fiscal-<empresa>-<aaaammdd>.pdf

Ele é a primeira coisa que a pessoa vê, vai para o e-mail que ela encaminha e
**sobrevive ao PDF**. De nada serviria limpar os metadados e chamar o arquivo de
`fiscale-dossie.pdf`. É ASCII de propósito: `Content-Disposition` é latin-1 no
protocolo, e acento ali sai como lixo no Windows.

---

## 2.2 · O comportamento da tela

**Botão por linha — “Baixar dossiê”.** Última coluna do painel cruzado.

**Botão no topo — “Relatório do Escritório”.** Na barra acima da tabela, junto
da frase que explica que o dossiê *junta* os originais e não redesenha nada —
alguém vai perguntar por que o PDF não “parece” uma certidão, e a resposta é
que ele **contém** a certidão.

**O download passa por `fetch`, e não por um link.** Um link baixaria o arquivo e
pronto; só o `fetch` permite ler `X-Anexados` antes de entregar o arquivo.

Três retornos possíveis, todos na mesma faixa acima da tabela:

| Situação | O que a tela diz |
|---|---|
| `X-Anexados > 0` | “Pronto: N documentos anexados” — e, se houver, quantos ficaram de fora e que a capa diz o motivo |
| `X-Anexados == 0` | aviso âmbar: **“O dossiê baixou sem nenhum anexo”**, com a instrução de guardar os PDFs pelo painel abaixo e gerar de novo |
| erro | a mensagem do servidor, que nunca carrega caminho de disco — só o nome da exceção |

**A lentidão do merge tem dois estágios de aviso.** Juntar dezenove empresas é
trabalho de disco, e tela muda por oito segundos é tela que a pessoa clica de
novo. Então: o botão desabilita e vira “Montando…” imediatamente, e aos
**2,5 s** a mensagem cresce para explicar o que está acontecendo — no lote, com
a frase “pode levar de dezenas de segundos a alguns minutos. Não clique de
novo.”

**Só os dígitos viajam no `onclick`.** O nome da empresa vem do cadastro e pode
ter apóstrofo (D’AVILA): num `onclick="…'nome'…"` o HTML decodifica `&#39;`
antes do JS ler, e o botão quebraria justamente na empresa de nome incomum. O
nome é buscado no panorama já carregado, na hora do clique.

### A suíte que segura isso

`teste_situacao_dossie_http.py` sobe um Fiscale de verdade, numa porta própria,
com pasta própria, e pede pela rede. As rotas de importação de cadastro já
foram escritas, classificadas e cobertas por asserções **e não funcionaram, duas
vezes** — nas duas o defeito estava no caminho até o handler, que nenhum teste
de módulo percorre. Aqui se confere também que os **bytes servidos pelo socket**
não carregam marca: é o único lugar onde uma reescrita no caminho apareceria.

---

## 3 · Consulta Optantes

Já existia (`web/consulta_optantes.html`, `/api/optantes`, `cnpj_publico.py`).
O que entra é a exportação e o PDF consolidado. Duas decisões:

**Varre as 19 empresas do cadastro, sem filtrar por certificado.** A consulta
usa a base pública e não precisa de certificado nenhum; filtrar por quem tem
`.pfx` excluiria empresas consultáveis sem ganho algum. O certificado vira
**coluna informativa** na tela, e quem quiser filtra ali.

**A data de referência da base vai no CABEÇALHO do PDF, não no rodapé.** A base
é atualizada mensalmente: uma exclusão do Simples de ontem ainda aparece como
optante hoje. Sem a data estampada onde se lê primeiro, o relatório é lido como
verdade de hoje — e essa leitura causa erro de apuração.

---

## 4 · CIM Recife — o diagnóstico

As três ações pedidas **não são da mesma natureza**, e tratá-las igual é o erro
a evitar:

| Ação | O que é | Risco |
|---|---|---|
| Ver o débito | leitura | quebra a cada mudança de layout |
| Gerar o DAM | o portal **emite** documento de arrecadação | robô pedindo instrumento financeiro |
| **Parcelar** | **confissão de dívida e assinatura de termo** | ato jurídico vinculante do contribuinte |

### Parcelamento não se automatiza

Não é limitação técnica. Um erro de leitura devolve número errado na tela; um
erro de parcelamento devolve **dívida confessada**, com renúncia à discussão
administrativa, em nome de um cliente que não clicou. Nenhuma economia de tempo
paga isso, e não há suíte de testes que torne aceitável.

### As opções levantadas

A Prefeitura do Recife **não publica API oficial**. O que existe são
agregadores comerciais que raspam o portal e revendem por API.

**(a) Assistido — ESCOLHIDO.** A tela tem os três botões; eles levam ao portal
com o CIM em mãos. Você faz o ato, e o Fiscale faz a **memória**: guarda o PDF
baixado pelo portão do envelope, com hash e procedência. Zero dívida técnica, e
nenhuma arquitetura nova — o canal assistido já existe e já funciona.

**(b) Raspagem própria.** Quebra a cada mudança do portal, exige guardar senha
de portal municipal, e esbarra em CAPTCHA. Descartada.

**(c) Agregador de terceiro.** Entrega extrato e DAM por API. O preço é que
CNPJ e débitos dos clientes passam por uma empresa de fora — decisão comercial,
não técnica. Se um dia for por aqui, entra como mais uma `FonteSituacao`, sem
tocar em nada acima: foi por essa razão que o contrato das fontes só entrega
bytes.

### O que fica registrado para quem vier depois

Ter os três botões e um deles apenas **levar ao portal** é escolha de produto
deliberada, não limitação a esconder. A tela diz isso em palavras, para
ninguém "consertar" o que está certo.
