# FISCALE — Portal, App e Integração Domínio: auditoria e plano mínimo

**Data:** 13/09/2026 · **Status:** auditoria + proposta. Fases 1 e 2 **implementadas no código**, e o
PWA da Fase 3 **implementado no código como instalação opcional** (ver abaixo e `FISCALE_HTTPS.md`).
**HTTPS, Caddy e rede privada continuam NÃO implantados** — implementação em código não é implantação.

## Decisões do usuário (13/09/2026)

1. **Acesso externo:** **rede privada** (C1 resolvido nessa direção; nada publicado) — confirmado e tornado vinculante pela **D97**.
2. **`sistemafiscale.com.br`:** registrado em nome do usuário.
3. **Reescopo dos testes da 6B:** autorizado — mas **não foi necessário**, porque:
4. **Domínio fora do escopo por ora.** As seções 2–3 e as Fases 5–10 abaixo ficam **congeladas**
   como registro da auditoria; nenhum código de integração foi escrito.
5. Pergunta sobre o papel de quem envia ao Domínio: **cancelada**.

## Arquitetura oficial (14/09/2026 — D97)

O FISCALE é **Sistema Web + Auth**, e não programa instalado:

```
www.sistemafiscale.com.br  →  site institucional
app.sistemafiscale.com.br  →  Sistema Web FISCALE  (só rede privada · HTTPS obrigatório)
        ▼
      AUTH  →  CENTRAL DE APLICAÇÕES  →  FISCALE / ELO / Administração
```

- **Auth** é a autenticação única da Central, do FISCALE, do ELO e da Administração (conforme papel).
- **`app.`** é o endereço do sistema; **`www`** fica exclusivamente para o site institucional.
- **PWA** é instalação opcional do Sistema Web. **8777** é porta interna, nunca endereço público.
- **D72 e D80 preservadas:** nada publicado na internet; a camada adicional exigida pela D80 é a rede privada, por isso não há MFA agora.

## O que foi entregue — Fases 1 e 2

| Arquivo | O quê |
|---|---|
| `web/login.html` | Tela nova (painel da marca + paisagem SVG local + cartão). "Manter conectado", "Esqueci minha senha" (orienta a pedir ao admin; não há e-mail). O rodapé só diz "SSL" quando a página veio por HTTPS. Primeiro acesso preservado. |
| `web/central.html` | Central de Aplicações: lateral (Início, Aplicações, Suporte, Configurações), conta no topo, cartões **FISCALE** e **ELO** para todos, **Administração** só para admin; usuário, perfil, ambiente e último acesso. |
| `web/fiscale-elo.js` | Abertura do ELO (bilhete Ed25519 por POST) extraída da Home — um caminho só para Home e Central. |
| `web/home_portal.html` | Usa `fiscale-elo.js`; botão "▦ Aplicações" de volta à Central. |
| `fiscale_sessoes.py` | `criar(uid, validade_s=None)` e `VALIDADE_MANTER_S` = 7 dias corridos, não renovável. Padrão continua 12 h. |
| `fiscale_server.py` | Login lê `manter` (só `true` booleano) e devolve `destino: /central.html`; cookie com a mesma validade da sessão; `/` → Central; `/api/quem?completo=1` traz nome e login anterior. |
| `teste_portal.py` | 86 asserções: estáticas, papéis, validade, e HTTP ponta a ponta numa instância isolada. |
| `teste_login_email.py`, `teste_login_tempo.py`, `teste_auditoria.py` | A guarda "sessão nasce do uid" passou de texto exato para regex do primeiro argumento. |

**Próximo:** implantação do HTTPS pela rede privada (pendências em `FISCALE_HTTPS.md`). O PWA da Fase 3 já está no código como instalação opcional.

---

## 0. Antes de tudo: três conflitos com decisões já tomadas

O pedido esbarra em decisões registradas. Nenhuma delas deve ser contornada
em silêncio — cada uma precisa de uma resposta explícita antes da Fase 1.

| # | Pedido | Decisão vigente | O conflito |
|---|---|---|---|
| C1 | Login em `app.sistemafiscale.com.br` (pedido original citava o `www`, que é o site institucional) | **D72**: proibido publicar o FISCALE na internet, abrir porta ou expor a 8777; acesso externo só por rede privada com HTTPS | Um formulário de login público na internet é exatamente o que a D72 proíbe |
| C2 | Login único com senha | **D80**: senha de 8 caracteres não libera acesso externo; exige MFA ou rede privada + HTTPS. Pendente: **Fase 2.1** (oráculo de tempo revela e-mails válidos) | Expor o login atual entrega a lista de e-mails e força bruta de toda a internet |
| C3 | Botão "Enviar ao Domínio" | **NF-e 6B**: a palavra "Domínio" foi retirada do produto; `teste_nfe6b.py` e `teste_ingestao3b_r2.py` **falham** se ela voltar à tela | A 6B tratava de *formato de exportação*, não de integração — mas o teste não sabe a diferença |

### Proposta para C1 + C2 (recomendação)

> **Resolvido pela D97 (14/09/2026): rede privada.** O túnel público com MFA descrito abaixo fica
> registrado como alternativa **não escolhida**.

O servidor continua no escritório (D63: "certificado nunca sai da máquina").
O domínio público passa a ser **só a porta**, com uma barreira **antes** da
aplicação:

```
navegador ──HTTPS──► sistemafiscale.com.br
                         │  túnel de saída (nenhuma porta aberta no roteador)
                         │  + camada de identidade com MFA  ◄── barreira D72/D80
                         ▼
                   PC do escritório: FISCALE (8777) ── ELO
```

Duas tecnologias atendem a isso sem abrir porta. A escolha é sua:

- **Túnel gerenciado com proxy de identidade** (ex.: Cloudflare Tunnel + Access):
  o domínio fica público, mas só quem passa pelo MFA chega ao login do FISCALE.
  É o que dá a experiência "entro no site do escritório". É serviço de
  terceiro, o que pela D63 cai do lado pago.
- **Rede privada** (Tailscale, já citada na decisão de servidor único): é mais
  fechada, mas cada máquina precisa do cliente instalado. Isso contraria a
  regra "nas estações só se abre o navegador"; o sistema usa `app.` e o `www` segue como site institucional.

**Sem uma das duas, a Fase 1 não vai para a internet.** Ela pode ser construída
e testada na rede do escritório — HTTPS local incluso.

### Proposta para C3

A 6B continua certa para o *exportador*. O teste passa a proibir "Domínio"
apenas nas telas e rotas de exportação, e libera o módulo de integração com
nome próprio. A mudança de escopo do teste precisa estar escrita, não
simplesmente apagada.

---

## 1. O que já existe e será aproveitado (auditado no código)

| Pedido | Já existe | Onde | Falta |
|---|---|---|---|
| Cadastro único de usuários | Sim | `fiscale_usuarios.py`, login por e-mail | nada |
| Papéis | `admin` / `operador`, lista de permissão travada por teste | `fiscale_papeis.py`, `teste_papeis.py` | papéis por **aplicação** (quem vê ELO, Domínio) |
| Sessão segura | SQLite, só o SHA-256 do token, 12 h, revogação por token/usuário/todas | `fiscale_sessoes.py` | "manter conectado" com validade maior; renovação |
| Cookie | `HttpOnly; SameSite=Lax`, token fora do HTML e do localStorage; **`Secure` e HSTS já estão no código** (`fiscale_proxy.py`), aplicados quando a requisição chega por HTTPS | `fiscale_server.py`, `fiscale_proxy.py` | nada no código — falta só a **implantação do HTTPS**, sem a qual o `Secure` não tem efeito prático |
| Limite de tentativas | 3 livres, atraso progressivo, bloqueio por conta e por IP | `fiscale_tentativas.py` | Fase 2.1 (tempo igual; contar pelo identificador digitado) |
| Auditoria | Eventos com resultado e campos | `fiscale_auditoria.py` | eventos `dominio.*` |
| Cofre de segredos | DPAPI; o segredo nunca vai ao navegador, só o estado | `fiscale_segredos.py` + `nfse/backend/seguranca.py` | inscrever as credenciais do Domínio |
| **SSO FISCALE → ELO** | **Já pronto.** Bilhete Ed25519 de 90 s, uso único; o ELO cria sessão própria | `fiscale_elo.py`, `/api/elo/abrir` | levar a empresa selecionada no bilhete |
| Tela inicial e menu | Centro de Operação Fiscal + menu com Clientes, Documentos Fiscais, Situação Fiscal, CClassTrib, Consulta Optantes, Central Fiscal | `web/home_portal.html`, `web/fiscale-nav.js` | nada — preservar |
| PWA | **Feito na Fase 3** (13/09/2026): manifest renomeado para FISCALE, ícones PNG 192/512 e maskable, `sw.js` sem cache de página nem de `/api` (guarda só o `offline.html`), `fiscale-pwa.js` e o botão "Instalar FISCALE" | `web/manifest.webmanifest`, `web/sw.js`, `web/icones/`, `web/fiscale-pwa.js` | nada no código — falta só o contexto seguro da implantação (`https://app.…`); é **instalação opcional** e nada depende dela |
| Acervo imutável e deduplicação | Sim, endereçado por conteúdo | `ingestao/` | status de integração **ao lado** do acervo, nunca dentro |

**Leitura:** a autenticação única *já é* a arquitetura do sistema. O que falta
é: (a) a camada externa, (b) a Central como tela, (c) o conector Domínio.

**Observação sobre o menu pedido:** "CIM Recife" não está no menu atual. O
escopo assistido do Recife foi removido no commit `b8f96ea`, e pela sua regra
ele **não volta**, a não ser que você diga o contrário.

---

## 2. Domínio — o que a documentação oficial diz (lida em 13/09/2026)

Fontes: Thomson Reuters Developer Portal (*Onvio BR Accounting API*), páginas
8476 e 12917 do Portal do Cliente Domínio e a landing page da Central do
Desenvolvedor.

### Confirmado na fonte

- **É API oficial, sem custo adicional**, para todos os pacotes. Documentos:
  NF-e e NFC-e (XML 4.0), CT-e (XML 3.0), CF-e (0.07/0.08), NFS-e
  (leiaute próprio 1.00 / ABRASF / Nacional — **as duas fontes divergem**)
  e baixas de parcela.
- **Autenticação:** OAuth 2.0 no `auth.thomsonreuters.com`, token em
  `POST https://auth.thomsonreuters.com/oauth/token` com
  `Authorization: Basic base64(client_id:client_secret)`. Resposta com
  `access_token` (JWT), `refresh_token` e `expires_in`. A página 8476 diz que
  o token "deve ser gerado apenas uma vez ao dia pelo ERP".
- **Credenciais do sistema (client_id/secret):** pedidas à Thomson Reuters.
  O cadastro como parceiro fica em `dominiosistemas.com.br/solucoes/integracao-com-erp/#Parceiro`
  e o e-mail citado é `api.dominio@thomsonreuters.com`. As chaves "são de uso
  EXCLUSIVO do ERP".
- **Identificação da empresa:** o **contador gera uma chave por CNPJ**, sem
  vencimento. A partir dela gera-se a **Integration Key**, que acompanha o
  token em cada envio.
- **Fluxo oficial:** criar token → conferir a key do cliente (contabilidade,
  CNPJ, nome) → gerar Integration Key → enviar XML → **consultar o envio**.
- **Recursos da API:** `ClientInfoResource` (clientes que o usuário pode
  integrar), `IntegrationResource` (informação da integração — recomendado
  para testar a conexão) e `InvoiceIntegrationResource` ("cria um lote de
  arquivos a processar"). O envio é por `multipart/form-data`.
- **TLS 1.3** preferencial e TLS 1.2 com as suítes ECDHE-GCM/ChaCha20.

### Não confirmado — e por isso não será escrito

- URL e path exatos do envio e da consulta, nomes dos campos multipart, nome
  do header da Integration Key, status possíveis do lote, códigos de erro,
  limites e tratamento de duplicidade.
- Esses dados estão em `Download Spec` (atrás de login do portal), na
  collection Postman e no PDF "Retorno de Protocolos API". **Não foram abertos.**
- **Não existe ambiente de homologação documentado.** Se isso se confirmar, o
  "teste real" será um envio verdadeiro para uma empresa-teste criada no
  Domínio do escritório. Isso precisa de sua autorização explícita.

Projetos da comunidade citam hosts e headers específicos. Isso **não é fonte**
e não entra no código.

### Agente local

Nada na documentação exige instalação local: token, chave e envio são todos
remotos. **Não se cria FISCALE Agent** agora.

---

## 3. Arquitetura do conector

Fica fora dos módulos NF-e/NFS-e/CT-e e segue o padrão de `conectores/`,
que já existe:

```
integracoes/dominio/
    configuracao.py   # ambiente, ligado/desligado, tipos — sem segredo
    credenciais.py    # client_id/secret e Integration Key por CNPJ → cofre DPAPI
    autenticacao.py   # token OAuth, cache em memória, renovação 1x/dia
    cliente.py        # HTTP puro (injetável: mock nos testes)
    mapeamento.py     # documento do acervo → arquivo aceito (espécie × versão)
    envio.py          # prepara → valida → envia → consulta
    respostas.py      # resposta bruta → status canônico
    registro.py       # tabela de envios (SQLite) + idempotência
    fila.py           # fila e reprocessamento (Fase 9)
teste_dominio_*.py
```

**Status canônico** (independente do acervo):
`nao_enviado · aguardando · processando · enviado · aceito · recusado ·
erro_temporario · erro_permanente · requer_configuracao`.

**Idempotência:** a chave única é `(cnpj_empresa, chave_documento,
sha256_do_xml, destino="dominio")`. Com `aceito` gravado, o botão responde
"Documento já enviado ao Domínio" e não chama a API. Reenvio só por ação de
admin, com motivo, e gera uma nova tentativa ligada à anterior.

**Registro por tentativa:** empresa, documento, chave, fonte, data, usuário,
número da tentativa, resultado, protocolo, mensagem, hash e resposta técnica
bruta — sem token nem key.

**Auditoria:** evento `dominio.envio` no `fiscale_auditoria` existente.

**Papel:** configurar credencial é de admin. Enviar documento: **decisão sua**
(operador ou só admin).

---

## 4. Fases, redesenhadas a partir do que existe

Cada fase termina com testes verdes e demonstração. Só se avança com sua aprovação.

A coluna **Situação** separa o que está **no código** do que é **implantação
futura**. Estar no código não significa estar implantado: HTTPS, Caddy e rede
privada **não** estão instalados (pendências em `FISCALE_HTTPS.md`).

| Fase | Entrega | Situação | Depende de |
|---|---|---|---|
| **0** | Decisões C1–C3 · Fase 2.1 do login · HTTPS local | **parcial.** C1 decidido pela **D97** (rede privada) e C2 endereçado por ela; **C3 em aberto**; **Fase 2.1 em aberto**; HTTPS **não implantado** | você |
| **1** | Tela de login nova (visual de referência, DNA `#10444E`) sobre a autenticação **atual**; "Manter conectado" = sessão de validade maior, revogável; "Esqueci minha senha" = redefinição pelo admin (não há e-mail configurado) | **implementada no código** (13/09/2026) | 0 |
| **2** | `central.html`: cartões por papel (FISCALE, ELO via `/api/elo/abrir`, Administração, Domínio) | **implementada no código** (13/09/2026), sem o cartão Domínio — ele não aparece enquanto não houver integração oficial (D97, item 8) | 1 |
| **3** | PWA de verdade: manifest renomeado, ícones PNG 192/512, `sw.js` sem cache de `/api` e sem prender versão (mantém a lição do SW de desativação), botão "Instalar FISCALE" | **implementado no código** como **instalação opcional** (D97, item 7). O navegador só oferece instalar em contexto seguro: `http://localhost:8777` no próprio servidor hoje, e `https://app.sistemafiscale.com.br` **só depois da implantação** | HTTPS |
| **4** | Empresa selecionada como contexto (pesquisa, cabeçalho com CNPJ/IE/IM/competência/certificado/capturas) e repassada ao ELO no bilhete | **não iniciada** | 2 |
| **5** | Conector Domínio **com mock**: token, key, envio, aceito, recusado, timeout, indisponível, duplicidade, nenhum segredo em log/HTML | credenciais TR |
| **6** | **Um** envio manual de **uma** NF-e real, com sua autorização, para uma empresa definida | 5 + spec oficial aberta |
| **7** | Retorno, protocolo e rastreabilidade na tela | 6 |
| **8** | NFC-e, CT-e e NFS-e, cada uma só depois de um envio real aceito (resolver antes a divergência de leiaute da NFS-e) | 7 |
| **9** | Fila, reprocessamento com recuo (erro temporário) e bloqueio (permanente) | 8 |
| **10** | Envio automático **desligado por padrão** | 9 |

---

## 5. O que preciso de você para começar

1. **C1 — decidido, não é mais pergunta.** A arquitetura aprovada é a da **D97**: **rede privada + HTTPS →
   Auth → Central → FISCALE / ELO / Administração**, com o sistema em `app.sistemafiscale.com.br`,
   alcançável **somente** de dentro da rede privada. **MFA não faz parte da arquitetura atual** — a camada
   adicional exigida pela D80 é a própria rede privada. A 8777 segue como porta interna (D72).
2. **Domínio `sistemafiscale.com.br`: registrado em nome do usuário** (13/09/2026) — fato registrado, não
   pendência. O `www` é o site institucional e o `app.` é o sistema (D97).
3. **C3:** posso reescopar os testes da 6B para liberar o nome no módulo de integração?
4. **As imagens de referência não chegaram nesta conversa.** Sem elas eu
   trabalho pela descrição textual.
5. **Thomson Reuters:** o cadastro de parceiro e o pedido de client_id/secret
   são feitos pelo escritório. O acesso ao Download Spec e à collection
   depende disso.
6. Enviar ao Domínio é tarefa de **operador** ou só de **admin**?
