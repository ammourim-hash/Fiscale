# FISCALE — Plataforma Fiscal do Escritório

**Sistema Web FISCALE.** Uma aplicação web autenticada: quem usa abre o
navegador e entra. As estações não instalam nada.

> **Arquitetura oficial (D97, 14/09/2026):** Sistema Web + Auth, acessado
> **somente pela rede privada do escritório**, com HTTPS obrigatório. O sistema
> **não é público na internet**. Ver `FISCALE_DECISOES_FISCAIS.md` (D97, D72 e
> D80) e `FISCALE_HTTPS.md`.

---

## Como a plataforma é organizada

```
www.sistemafiscale.com.br  →  site institucional

app.sistemafiscale.com.br  →  Sistema Web FISCALE (só rede privada · HTTPS)
        ▼
      AUTH  →  CENTRAL DE APLICAÇÕES
                 ├── FISCALE        núcleo fiscal
                 ├── ELO            atendimento, sem segundo login
                 └── Administração  só para administrador
```

- **Auth** — uma autenticação só para toda a plataforma: login, sessão,
  "Manter conectado", papéis, auditoria e acesso às aplicações.
- **Central de Aplicações** — a porta de entrada depois do login. Mostra só o
  que o papel da pessoa permite.
- **FISCALE** — clientes, documentos fiscais (NF-e, NFC-e, NFS-e, CT-e),
  eventos, acervo, conferência, situação fiscal e apuração.
- **ELO** — aplicação irmã de atendimento. Abre a partir da Central com a
  mesma sessão; o ELO não pede senha.

---

## Onde fica e como se acessa

- O **servidor** guarda dados, certificados A1 e segredos — nada disso vai
  para o navegador.
- **Hoje (D-AMB-1):** a produção é **provisória**, no AMMOURIM, em
  `http://AMMOURIM:8777`. Use sempre o **nome** da máquina, não o IP, que
  muda. O AMMOURIM também é o ambiente de desenvolvimento, teste e
  homologação.
- **Depois:** a produção definitiva vai para um **servidor dedicado**,
  preparado quando o produto estiver maduro.
- **Destino:** `https://app.sistemafiscale.com.br`, **somente pela rede
  privada**, com HTTPS. **Ainda não implantado** — as pendências estão em
  `FISCALE_HTTPS.md`.
- A porta **8777 é interna**: nunca é endereço público de acesso.
- Se uma estação não abrir o sistema: `rede_diagnostico.bat`, no servidor.

---

## Entrar

- **Primeiro uso:** a senha do `admin` só pode ser criada no próprio servidor.
- **Login** por usuário ou e-mail. **Manter conectado** mantém a sessão por
  7 dias naquele navegador; sem marcar, a sessão dura 12 horas.
- **Esqueci minha senha:** o administrador redefine. Não há e-mail automático.
- **Papéis:** administrador (usuários, backup, auditoria) e operador (uso
  fiscal). Cada pessoa troca a própria senha.
- As senhas são guardadas com hash criptográfico (PBKDF2), nunca em texto.

---

## Instalação opcional como aplicativo (PWA)

**É opcional.** Tudo funciona pelo navegador.

- No **Edge** ou no **Chrome**, depois de entrar, aparece **Instalar FISCALE**
  quando o navegador permite. Ele só permite em contexto seguro: em
  `https://app.sistemafiscale.com.br` (depois da implantação) ou em
  `http://localhost:8777` no próprio servidor.
- Pela rede em `http://…:8777` o navegador **não** oferece a instalação — e o
  sistema continua funcionando normalmente.
- O aplicativo instalado usa a mesma sessão do navegador. Nenhuma senha ou
  token fica guardado no aparelho; só a tela "sem conexão".

---

## App Android

O FISCALE também roda no **Android** como aplicativo (`android/`). É o
**mesmo Sistema Web**, aberto num WebView: login, sessão, Central, ELO e
todas as telas vêm do servidor do escritório. Nada de regra fiscal, senha ou
certificado vai para o celular (D63, D72, D97).

- **Baixar o APK:** aba *Actions* do GitHub → workflow **Android APK** →
  artefato `FISCALE-android` (`FISCALE-debug.apk`). Em *Releases*, o APK
  também é anexado quando um release é publicado.
- **Instalar:** abrir o `.apk` no celular e permitir "instalar apps
  desconhecidos" para o navegador/gerenciador de arquivos.
- **Primeiro uso:** informar o endereço do servidor —
  `https://app.sistemafiscale.com.br` (destino, só pela rede privada) ou,
  hoje, `http://AMMOURIM:8777` na rede do escritório. O app testa a conexão e
  avisa quando o endereço é HTTP fora de rede privada.
- **O que o app acrescenta ao navegador:** escolher arquivo (importar XML),
  baixar arquivos para `Downloads/FISCALE`, botão voltar, tela "sem conexão"
  e atalho "Alterar servidor" (toque longo no ícone).

Detalhes técnicos e build local: `android/README.md`.

**Fora do escritório** (4G, casa, viagem): pela rede privada Tailscale
(D72/D97), sem abrir porta no roteador. No servidor, rode uma vez
`celular_configurar.bat`: ele instala e liga o Tailscale, libera a 8777
**só** para a rede privada (`100.64.0.0/10`) e mostra o endereço `100.x.x.x`
para o app. Prévia sem alterar nada: `celular_configurar.ps1 -Simular`;
desfazer: `celular_configurar.ps1 -Desfazer`. No celular: instalar o
Tailscale da Play Store, entrar com a mesma conta e usar esse endereço no app.

---

## Módulos

- **Clientes** — as empresas do escritório, com certificado, inscrições e
  regime.
- **Documentos Fiscais** — NFS-e, NF-e (modelo 55), NFC-e (modelo 65) e
  CT-e (modelo 57), capturados e guardados no acervo.
- **Auditor de XML** — créditos por regime e conferência de NCM. Inclui o
  **combustível como insumo**: separa as compras de combustível pelo grupo
  `<comb>`/código ANP do XML e mostra a base de crédito nota a nota. A caixa
  "Combustível é consumido como insumo" só soma o crédito quando marcada —
  quem sabe se é revenda ou insumo é o usuário.
- **Situação Fiscal** — certidões e extratos das esferas.
- **CClassTrib** — classificação tributária da Reforma.
- **Consulta Optantes** — situação no Simples Nacional pela base pública.
- **Central Fiscal** — o que mudou na legislação, com o link da fonte.

---

## Servidor (para quem mantém)

- **Pelo código (Windows):** `iniciar.bat` prepara o módulo NFS-e em
  `nfse/.venv` na primeira vez e sobe o servidor. Mac/Linux: `./iniciar.sh`.
- **Produção provisória:** sobe no logon pelo `Iniciar Fiscale.vbs` (pasta
  Inicializar do Windows), com `FISCALE_DADOS` apontando para a pasta real.
- **Dados:** `C:\Users\<usuário>\Fiscale\dados` — a regra única é
  `fiscale_dados.py`. Log em `fiscale.log`, na mesma pasta.
- **Testar sem derrubar a produção:** `testar_fiscale.bat` (porta 8899, pasta
  de dados própria).
- **Homologar:** `homologar_fiscale.bat` (porta 8898, dados em
  `C:\Fiscale\homologacao\dados`). Ele recusa subir sem pasta de dados
  definida, recusa a pasta real e recusa a porta da produção. A homologação
  recebe dados **só por restauração de `.fbk`** — nunca por cópia da pasta
  real, onde as senhas de certificado continuariam abrindo.
- **Pacote portátil:** `montar_portatil.py` gera o FISCALE-Portable (Python
  embutido, sem instalar nada no servidor).
- **Rede:** `rede_diagnostico.bat` (só leitura). O `rede_configurar.bat` está
  **em revisão (Infra 1A)**: até a aprovação, use apenas
  `rede_configurar.ps1 -Simular`, que não altera nada.

**D-AMB-1 (15/09/2026):** o AMMOURIM é hoje o ambiente de desenvolvimento,
teste, homologação **e produção provisória**. O servidor definitivo será
preparado posteriormente; a produção provisória segue em
`C:\Users\nineq\Fiscale\dados`, na porta 8777. Registro completo em
`FISCALE_DECISOES_FISCAIS.md` (D-AMB-1).

---

## Segurança

- Certificados A1 e segredos ficam **no servidor**, protegidos pelo DPAPI do
  Windows. O navegador recebe tela, nunca credencial.
- Acesso de fora do escritório **somente por rede privada com HTTPS** (D72). A
  senha sozinha não libera acesso externo (D80).
- O token de sessão fica só em cookie `HttpOnly`: nunca na URL, nunca no HTML.

---

## Histórico (não é mais a forma de uso)

Registrado para quem encontrar referências antigas no projeto:

- **Executável `dist\Fiscale.exe` com ícone na bandeja.** Foi a primeira forma
  de distribuição (gerado pelo PyInstaller com `fiscale.spec`). O Smart App
  Control do Windows passou a bloquear o executável sem assinatura, e o
  servidor passou a rodar pelo código e pelo pacote portátil.
- **"Instalar como aplicativo" por `http://IP:8777`.** Não funciona: o
  navegador só instala em contexto seguro (HTTPS ou localhost). A instalação
  opcional correta está descrita acima.
- **App Android (`.apk` com WebView).** Planejado e não seguido na época; foi
  retomado em 30/09/2026 — ver a seção "App Android" acima.
- **Módulos retirados do produto:** Plano de Saúde e atalho do e-CAC
  (22/08/2026, D71) e o canal assistido da CIM do Recife (removido no commit
  `b8f96ea`).

---

## Sobre este repositório

Código trazido da pasta **FISCALE 2026** do Google Drive (versão de
29/09/2026). Ficaram **de fora**, e continuam só no Drive: backups `.fbk`,
o pacote portátil `.zip`, cópias `backup_pre_*`/`checkpoint_*`, arquivos
`.bak`, o código Prisma gerado do ELO (`npx prisma generate` recria), as
fixturas SITFIS e amostras de extratos (podem conter dados reais) e os
relatórios operacionais por cliente (`FISCALE_*_RELATORIO.md`, diagnósticos,
`ING*`, `NFE*`, `CTE*`, `SAUDE_AUDITORIA.md`, `FISCALE_SEGREDOS.md`...).

Como o repositório é público, CPFs, CNPJs e nomes de clientes e pessoas reais
que apareciam no código e nos testes foram trocados por valores fictícios
**válidos** (dígitos verificadores preservados), sem mudar o resultado dos
testes.
