# FISCALE — Fase 3: HTTPS + instalação opcional (PWA)

**Data:** 13/09/2026 · **Status:** lado do FISCALE pronto e testado; **HTTPS NÃO está no ar** — parado antes da configuração definitiva, como pedido.
**Checkpoint:** `checkpoint_pre_fase3_20260913_200526/` (cópias + `diff_nao_commitado.patch` + `HEAD.txt`).

> **Arquitetura oficial (D97, 14/09/2026).** O FISCALE é **Sistema Web + Auth**. O endereço do sistema é
> `app.sistemafiscale.com.br`, **somente pela rede privada do escritório**, com HTTPS obrigatório — o HTTPS
> deste documento é usado **dentro** dessa arquitetura de rede privada, nunca como publicação na internet.
> `www.sistemafiscale.com.br` continua sendo o site institucional. O PWA desta fase é **instalação opcional**
> do Sistema Web: nada depende dele. D72 e D80 seguem valendo. As pendências de implantação continuam na seção 5.

---

## 1. Auditoria (antes de mexer)

> **Registro histórico de 13/09/2026.** A tabela descreve o que foi encontrado **naquele dia**. Onde o
> estado mudou, a própria linha diz qual é o estado atual. Nada aqui confirma a segurança da rede de hoje.

| Item | Encontrado |
|---|---|
| Servidor | `pythonw.exe` do Python 3.12 do sistema rodando `fiscale_server.py`, PID 12112, escutando **0.0.0.0:8777**. Módulo NFS-e em 127.0.0.1:8790. |
| Portas 80/443 | **livres** — nada escuta. |
| Proxy / túnel / VPN | **nenhum instalado**: sem Caddy, nginx, cloudflared, Tailscale, ZeroTier, WireGuard, win-acme. |
| Certificados | **nenhum** para `sistemafiscale.com.br` no repositório do Windows. |
| Domínio | `www.sistemafiscale.com.br` é **CNAME para `custom-domains.chatgpt.site`** (Cloudflare) e já serve um site: *"FISCALE — Gestão fiscal orientada por documentos"*. O domínio raiz também. **DNS no Registro.br** (`d/e.sec.dns.br`). |
| Firewall | Não há a regra `FISCALE 8777` do `rede_configurar.ps1`. Há regras **por programa** permitindo o `pythonw.exe`/`python.exe` do Python 3.12 **inclusive no perfil Público**. Em 13/09/2026 a rede Wi-Fi então usada aparecia com perfil Privado. **Estado atual (17/09/2026, medido):** o perfil da rede é **Público** (`WiFi-Casa 3 = Public`); essa rede é **particular do administrador**, **não** é a rede do escritório nem base de confiança do FISCALE (D-AMB-1), e **não deve ser tornada Privada**. A **Infra 1A ainda não foi aplicada** — as regras do perfil Público continuam abertas (seção 5). |
| Manifest | nome antigo ("Plano de Saúde"), um `.webp` 256×256 declarado como 192 **e** 512 — o Chrome/Edge não aceitaria. |
| Service worker | `sw.js` só de **desativação** (o anterior prendia telas em versão velha). |
| Telas | lidas do disco a cada pedido; o backend só muda com reinício. |

### O que a auditoria revelou que ninguém tinha visto

Atrás de um proxy na mesma máquina, **toda** requisição chega de 127.0.0.1. O `_eh_local()` tratava isso como "está no PC". Quem viesse de fora pelo HTTPS poderia:
- criar o primeiro admin;
- restaurar backup;
- **encerrar o servidor**.

Além disso, o limite de tentativas por IP juntaria o escritório inteiro num contador só.

**Corrigido nesta fase e provado por mutação.** Com a regra antiga, o teste criou o admin pelo proxy (HTTP 200).

---

## 2. Arquitetura HTTPS proposta

```
 dispositivo na REDE PRIVADA ──HTTPS 443──► Caddy (bind só no IP da rede privada)
                                               │  X-Forwarded-For = IP real (sobrescrito)
                                               │  X-Forwarded-Proto = https
                                               ▼
                                   FISCALE 127.0.0.1:8777  (nunca publicado)
```

- **Proxy:** Caddy. É um binário só e emite e renova o certificado sozinho. O modelo está em `proxy/Caddyfile.exemplo`, **não validado**: rodar `caddy validate` na hora de instalar.
- **Certificado:** Let's Encrypt real, pelo **desafio de DNS**. É o único que funciona sem expor a máquina à internet. Nada de certificado improvisado.
- **HTTP → HTTPS:** feito pelo Caddy, também só na rede privada. A 8777 **não redireciona nada**. Hoje existe **produção provisória no AMMOURIM** (D-AMB-1), acessível em `http://AMMOURIM:8777`.
- **8777:** continua em 0.0.0.0 para a rede local, que é o funcionamento atual. Não é publicada para a internet. O proxy fala com ela só por 127.0.0.1.
- **Nome (D97):** `app.sistemafiscale.com.br`, resolvido **só** dentro da rede privada. A rede privada é pré-requisito, não alternativa.

### O que o FISCALE passou a fazer atrás do proxy (`fiscale_proxy.py`)

Todas as regras abaixo valem **somente** quando o soquete é loopback **e** vem cabeçalho de encaminhamento.

| Regra | Efeito |
|---|---|
| Loopback com encaminhamento **não é local** | criar admin, restaurar backup e encerrar ficam só no PC |
| IP = último `X-Forwarded-For`, validado | auditoria e limite de tentativas por pessoa real |
| `X-Forwarded-Proto: https` | cookie **Secure** e **HSTS** (`max-age=86400`, curto de propósito) |
| De outra máquina | cabeçalho **ignorado**: ninguém da rede se declara HTTPS nem troca de IP |

Na 8777 direta (HTTP) nada muda: cookie sem Secure, sem HSTS e local igual a antes.

---

## 3. PWA — instalação opcional do Sistema Web

| Arquivo | O quê |
|---|---|
| `web/manifest.webmanifest` | `FISCALE` / `FISCALE` / "Plataforma Fiscal do Escritório", `standalone`, `id` estável, `start_url` `/home_portal.html`, atalhos (Central, Documentos, Clientes) |
| `web/icones/*.png` | 192, 512, maskable 512 e 180, gerados por `gerar_icones_pwa.py` a partir do símbolo (mesma geometria do `fiscale-logo.svg`) |
| `web/sw.js` | não guarda página, script nem `/api`. Só guarda `offline.html`. Só navegações GET da própria origem. Apaga caches antigos |
| `web/offline.html` | "O FISCALE não respondeu", com o símbolo embutido |
| `web/fiscale-pwa.js` | registra o SW só em contexto seguro; "Instalar FISCALE" só aparece quando o navegador oferece |
| `web/central.html`, `web/home_portal.html` | manifest, ícone de toque e botão discreto "Instalar FISCALE" (nasce escondido) |

**Sessão no app:** é a mesma de sempre (12 h, ou 7 dias com "Manter conectado"). Com a sessão válida, o ícone abre o FISCALE. Vencida ou ausente, `/home_portal.html` responde 302 para o login. Nenhuma senha, token ou cookie passa pelo SW ou pelo `localStorage`.

---

## 4. Testes

| Teste | Resultado |
|---|---|
| `teste_https_pwa.py` (novo) | **138 ok** |
| `teste_portal`, `teste_papeis`, `teste_login_tempo`, `teste_login_email`, `teste_auditoria`, `teste_senha_politica`, `teste_fiscale_sessoes`, `teste_clientes_modelo`, `teste_fiscale_elo_sync`, `teste_situacao_http`, `teste_importar_http` | todos verdes |
| Mutação: `_eh_local` com a regra antiga | **pegou**: admin criado pelo proxy (200) e mais 9 falhas |

O `teste_https_pwa.py` cobre, ponta a ponta, com um proxy TLS de teste (certificado temporário) na frente de um FISCALE isolado:
- HTTPS e HSTS;
- login e cookie Secure;
- o proxy não vira "local";
- IP real na auditoria;
- operador e admin;
- Central, FISCALE e ELO sem segunda senha;
- sessão vencida volta ao login;
- logout;
- manifest, ícones, SW e `offline.html` abrem sem sessão;
- rotas livres exatas.

**Edge 154 real, no Windows**, numa cópia estática em `localhost` (sem dado nenhum):
- SW `activated` e controlando a página;
- cache só com `offline.html`;
- o navegador disparou `beforeinstallprompt`, e o botão **Instalar FISCALE apareceu**;
- com o servidor derrubado, abriu a tela "sem conexão".

Esse teste achou um defeito: o ícone quebrava sem servidor. Foi corrigido e reverificado. O SW e o cache de teste foram removidos do Edge depois.

**Não verificado, e por quê:**
- **Instalar de fato, fechar e abrir pelo ícone:** o diálogo de instalação é do navegador, e só a própria pessoa pode clicar nele.
- **`https://app.sistemafiscale.com.br`:** não existe HTTPS ainda (seção 5).
- **ELO pela Central atrás do proxy:** a autorização passa sem segunda senha. A emissão do bilhete não foi exercitada porque a instância isolada não tem ELO (respondeu 503).
- **Caddy:** não instalado. O redirecionamento e o `bind` foram conferidos só no texto.

---

## 5. O que falta para o HTTPS — decisões e ações suas

**D-HTTPS-1 — Qual nome? DECIDIDO (D97, 14/09/2026): `app.sistemafiscale.com.br`**, somente pela rede privada. O `www` continua servindo o site institucional. A tabela abaixo fica como registro da escolha.

| Opção | Consequência |
|---|---|
| **a) `app.sistemafiscale.com.br`** para o sistema — **escolhida** | o site continua no `www`, e o sistema ganha nome próprio |
| b) `www` para o sistema | **o site atual sai do ar**, e quem estiver fora da rede privada não vê nada no endereço |

**D-HTTPS-2 — Onde fica o DNS?** O Registro.br não tem API, então não dá para renovar o certificado sozinho.

| Opção | Consequência |
|---|---|
| **a) Mover os servidores DNS para a Cloudflare** (grátis, com API; recomendado) | o Caddy emite e renova sozinho. Os registros do site precisam ser recriados lá antes da troca |
| b) Continuar no Registro.br | a renovação vira **manual a cada 60–90 dias**: frágil, e o certificado vence em silêncio |

**D-HTTPS-3 — Rede privada.** Pela D97 ela é **pré-requisito** do `app.`: o sistema só é alcançável por ela. Falta decidir qual. O Caddyfile escuta só no IP dela, então ela precisa existir primeiro. Com Tailscale, cada aparelho que usar o app instala o cliente. Isso é exceção à regra "estações só abrem o navegador" e só vale para quem usa o app pelo HTTPS.

**Risco em aberto (configuração de segurança, é sua):** o Python 3.12 que roda a 8777 está liberado no firewall **também no perfil Público**. Numa rede pública, a 8777 ficaria aberta para aquela rede. O tratamento está na **Infra 1A**: o `rede_configurar.ps1` revisado (identifica o Python do FISCALE, restringe origem e perfil, grava backup e oferece `-Desfazer`). Até a aprovação, ele só deve ser usado em modo de prévia:

```powershell
.\rede_configurar.ps1 -Simular
```

**Nada foi aplicado** no firewall, e o risco **permanece aberto**. A rede à qual o AMMOURIM está conectado hoje não deve ser confirmada como rede do escritório (D-AMB-1).

---

## 6. Reinício da 8777 — **feito em 16/09/2026**

**Situação em 16/09/2026:**

- A **8777 foi reiniciada em 16/09/2026 às 12:24** (PID **20192**). O código desta fase está no ar.
- O **NFS-e da 8790 foi relançado em 16/09/2026** (21:20, PID 1756), já com a correção **`5af8afd`** (importação municipal e `/openapi.json`).
- A correção do **ciclo de vida do NFS-e** (**`9390aac`**) **só será efetiva no próximo reinício da 8777**. Antes desse reinício, encerrar pelos PIDs o NFS-e atual — **1756** e **26436** —, que ficou fora da árvore do servidor depois do relançamento manual.

**Plano original (13/09/2026), mantido como registro:**

- **O que:** o processo `pythonw.exe fiscale_server.py` (PID 12112) na **8777**.
- **Por quê:** o backend lê `fiscale_proxy`, as rotas livres dos ícones, Secure/HSTS e o login com `manter`/`destino` das Fases 1 e 2 só depois de reiniciado.
- **Impacto:** 10 a 30 s fora do ar para todas as máquinas. As sessões sobrevivem (SQLite), ninguém precisa entrar de novo.
- **Até reiniciar:** as telas novas já aparecem. O "Instalar" pode não ser oferecido, porque os ícones ainda pedem login. Pela rede em HTTP nada muda, porque o SW não registra fora de contexto seguro.
- **Como voltar:** copiar de volta os arquivos de `checkpoint_pre_fase3_20260913_200526/`, apagar os arquivos novos da seção 7 e reiniciar de novo.

---

## 7. Arquivos

- **Novos:**
  - `fiscale_proxy.py`
  - `gerar_icones_pwa.py`
  - `teste_https_pwa.py`
  - `proxy/Caddyfile.exemplo`
  - `web/offline.html`
  - `web/fiscale-pwa.js`
  - `web/icones/` (4 PNG)
  - este documento
- **Alterados:**
  - `fiscale_server.py`: origem, `_eh_local`, `_via_https`, HSTS, cookie Secure, rotas livres
  - `web/manifest.webmanifest`
  - `web/sw.js`
  - `web/central.html`
  - `web/home_portal.html`
  - `teste_login_tempo.py`: 15b, a regra nova de origem
- **Intocados:** NF-e, NFS-e, CT-e, Situação Fiscal, Apuração, Domínio.

---

## 8. Instalação opcional no Windows (quando o HTTPS existir — ou já, no próprio PC)

**Opcional (D97):** o Sistema Web funciona inteiro pelo navegador; instalar só dá janela própria e ícone.

Pelo `http://localhost:8777` no AMMOURIM já dá, porque localhost conta como seguro:

1. Abrir no **Edge** ou no **Chrome** e entrar.
2. Na Central (ou na Home do FISCALE), clicar em **Instalar FISCALE**, no topo. Se não aparecer, use o ícone de instalar na barra de endereço (Edge: "Aplicativos → Instalar este site como aplicativo").
3. Confirmar. O FISCALE abre em janela própria e ganha atalho no Menu Iniciar. Para fixar na barra de tarefas, clique com o botão direito no ícone.
4. Roteiro de validação:
   1. Fechar o navegador.
   2. Abrir pelo ícone **FISCALE**: deve entrar direto.
   3. **Sair**, abrir de novo: deve cair no login.
   4. Abrir o **ELO** pela Central.

## 9. Próximo passo exato

1. ~~Responder **D-HTTPS-1** (nome)~~ — decidido pela D97: `app.sistemafiscale.com.br`. Falta responder **D-HTTPS-2** (DNS).
2. Montar a rede privada **no servidor definitivo, depois da migração** (**D-HTTPS-3**, D-AMB-1) e informar o IP dele nela.
3. ~~Autorizar o reinício da 8777 (seção 6)~~ — **concluído** em 16/09/2026 (seção 6).
4. Então, e só então: instalar o Caddy (download oficial, com sua confirmação), preencher as variáveis, rodar `caddy validate`, emitir o certificado e fazer o teste real da seção 8 em `https://app.sistemafiscale.com.br`, de dentro da rede privada.
