# Fiscale — portabilidade entre computadores

Fases aprovadas em 11/08/2026. Este documento cobre a **PORT 1**; as fases
seguintes entram aqui conforme forem aprovadas.

| Fase | O quê | Estado |
|---|---|---|
| **PORT 1** | Raiz única de dados + certificado controlado + "aguardando senha" | ✅ **CONCLUÍDA** — 11/08/2026 (migrada em produção às 15:17) |
| **PORT 2** | Backup e restauração `.fbk` + tela Backup e Migração | ✅ **CONCLUÍDA** — 11/08/2026 |
| **PORT 3** | Segurança, diagnóstico e preparação para a portabilidade | ✅ **CONCLUÍDA** — 11/08/2026 |
| **PORT 4** | Distribuição portátil com Python embutido | ✅ **CONCLUÍDA** — 12/08/2026 |

> **A PORT 4 é só portabilidade.** Nada de plano de saúde, REINF ou ELO entra
> nela. As decisões de 11/08/2026 sobre o módulo de saúde (enxugar a tela e
> automatizar a captura dos relatórios) estão registradas em
> `SAUDE_DECISOES.md` (D25 e D26) e viram as fases **HEALTH 2b** e
> **HEALTH 3** — trilha separada, sem cruzar com esta.

---

## O formato de dados — versão 2

```
<DADOS>/
    dados_versao.json          {"versao": 2}  — quem diz em que formato está
    certificados.json          cadastro; caminho do .pfx RELATIVO
    certs/<arquivo>.pfx        os certificados, dentro dos dados
    usuarios.json              login (PBKDF2, migra entre máquinas)
    state_*.json               estado das telas
    entrada_config.json        pasta vigiada, com marcador %DOWNLOADS%
    <cnpj>/                    documentos da empresa
        estado.json            NFS-e nacional  -> {"ultimoNSU": ...}
        nfe/estado.json        NF-e DFe        -> {"ultNSU":, "maxNSU":}
        nfe/ nfe_importadas/ cte/ xmls/ pdfs/ saude/
    sessoes.db  elo_sync.db    efêmeros — não entram em backup
    migracao.log               o que a migração fez, quando
```

**Quem decide a raiz** — `fiscale_dados.raiz()`, nesta ordem:

1. `FISCALE_DADOS`, se definida
2. `<app>/dados`, se existir a marca `.fiscale-portatil` (modo pendrive)
3. `~/Fiscale/dados` — o padrão

---

## Decisões da PORT 1

### P1 — Uma raiz só, decidida num lugar só

Antes: `fiscale_server.py` usava `~/Fiscale/dados` e o backend do NFS-e usava
`~/SistemaNFSe/dados`, porque `runner.py` fazia `sys.frozen = True` na mão. O
sistema ficava partido: login, clientes e plano de saúde de um lado;
certificados, XMLs e NF-e do outro. Quem copiasse "a pasta de dados" levava
metade — e foi isso que quebrou a migração.

Agora os dois importam `fiscale_dados` e o truque do `runner.py` saiu.

### P2 — O `.pfx` mora dentro dos dados, com caminho relativo

Os 21 cadastros apontavam para `C:/Users/nineq/Downloads/*.pfx`. Isso já era
frágil **nesta** máquina (limpar a pasta Downloads derruba 21 empresas) e não
sobrevivia a troca de computador.

Agora o cadastro copia o arquivo para `<DADOS>/certs/` e grava
`certs/EMPRESA.pfx`. A conversão relativo↔absoluto acontece só em
`_ler_registro`/`_salvar_registro`, então os 14 pontos que usam
`cert["caminho"]` continuam como estavam.

Cadastro antigo com caminho absoluto continua funcionando — não se quebra
instalação que ainda não migrou.

### P3 — "Aguardando senha" em vez de recadastrar

A senha do certificado é DPAPI: atrelada à conta do Windows, não viaja. Antes,
sem ela, a empresa era inútil e o jeito era recadastrar tudo.

Agora `estado_senha()` responde `ok` ou `aguardando`, e a empresa continua
cadastrada com apelido, marcação de procurador e histórico. `POST
/api/certificados/senha` recebe a senha, **confere abrindo o .pfx de verdade**,
confirma que o documento bate com o CNPJ do cadastro e reprotege com o DPAPI da
máquina nova.

Migração parcial funciona: quem usa 5 das 21 empresas digita 5 senhas.

### P4 — A migração nunca apaga a origem

`fiscale_migracao.garantir()` roda no arranque, é idempotente e:

- move o legado preferindo **renomear** (instantâneo mesmo com 30 mil arquivos);
- ao encontrar pasta que existe dos dois lados, **funde arquivo a arquivo sem
  sobrescrever** o que já está no destino;
- só marca a origem como consumida **depois de conferir, arquivo por arquivo**,
  que tudo tem contraparte de mesmo tamanho no destino — e mesmo aí **renomeia**
  para `...-migrado-para-fiscale` em vez de apagar;
- guarda `certificados.json.pre-port1` antes de reescrever o cadastro;
- **não decifra nenhuma senha**.

### P5 — Raiz customizada não adota dados de fora  🔴 *defeito encontrado em teste*

Um servidor de ensaio, com `FISCALE_DADOS` apontando para uma pasta temporária,
tentou trazer — e renomear — a pasta de dados de **produção** para dentro dela.
A produção sobreviveu porque as travas de P4 seguraram (arquivos já existiam no
destino; o rename falhou porque o Fiscale do usuário estava com a pasta aberta),
mas o comportamento estava errado.

Regra que passou a valer:

- adoção de legado **só** quando a raiz em uso é a natural da instalação
  (`raiz_e_padrao()`). `FISCALE_DADOS` apontando para outro lugar significa
  outra instalação, e outra instalação não se serve do acervo da primeira;
- **`~/Fiscale/dados` nunca é "legado"** — ou é a própria raiz, ou é outra
  instalação.

Há três testes travando isso.

### P6 — `%DOWNLOADS%` em vez do caminho de um usuário

`entrada_config.pasta` guardava `C:\Users\nineq\Downloads`. Agora guarda o
marcador e resolve para a Downloads de quem está usando.

---

---

# PORT 2 — Backup e restauração

## O arquivo `.fbk`

`FISCALE-backup-AAAA-MM-DD-HHMM.fbk` é um ZIP com três partes:

```
manifesto.json    EM CLARO — versão, data, contagens, e o SHA-256 de CADA
                  arquivo. Fica legível de propósito: dá para inspecionar o
                  conteúdo antes de restaurar e SEM a frase-senha.
dados/**          os dados, já limpos de segredo
cofre.fbkc        os .pfx, em AES-256-GCM
```

**O cofre.** Chave derivada da frase-senha por **scrypt** (n=2¹⁵, r=8, p=1),
**sal aleatório por backup**, **AES-256-GCM**. A cifra é autenticada: frase
errada, ou um byte trocado no arquivo, e a abertura falha — não devolve lixo.
A frase **não é gravada em lugar nenhum**, nem seu hash.

## Decisões da PORT 2

### P7 — O manifesto em claro, o conteúdo protegido

Só os `.pfx` vão no cofre. O resto fica legível porque precisa ser inspecionável
antes da restauração: quem recebe um `.fbk` tem de poder ver de onde veio,
quantas empresas traz e se está íntegro **antes** de mexer na instalação.

Os `.pfx` não têm hash no manifesto — quem garante a integridade deles é a
etiqueta de autenticação do GCM, que é mais forte: prova também que ninguém
trocou o cofre inteiro.

### P8 — Segredo não viaja

Saem do pacote na exportação, e o manifesto registra quantos:

| O quê | Por quê |
|---|---|
| `senha_protegida` (DPAPI) | é da máquina de origem; não abriria no destino |
| senhas de portal do `state_plano.json` | estão em texto claro; não podem sair assim |
| `sessoes.db`, `elo_sync.db` | efêmeros; sessão nova é o certo depois de migrar |
| logs, caches, `uploads/`, `.venv`, `build`, `dist`, `__pycache__` | recriáveis |
| chaves privadas do ELO | chave privada não sai da máquina que a gerou |

### P9 — Restaurar não decide sozinho

Com dados no destino, `restaurar()` devolve `precisa_decisao` e as três opções:

- **Cancelar** — não mexe em nada.
- **Mesclar por CNPJ** — o que já está aqui **vence**, inclusive a senha já
  informada. Do backup entram só as empresas e os arquivos que faltam.
- **Substituir** — o backup passa a valer. O que estava aqui é **movido** para
  `dados-substituido-AAAAMMDD-HHMMSS`. **Nada é apagado.**

### P10 — Instalação recém-criada conta como vazia

O caminho principal é: instalar → abrir → criar a senha do admin → restaurar. Só
que criar a senha já escreve `usuarios.json` e `dados_versao.json`, e a primeira
versão pedia decisão de mesclar/substituir bem aí, sem haver trabalho a perder.

Agora `vazio` significa "não há trabalho a perder": arquivos de sistema não
contam, **um único XML de empresa já conta**.

### P11 — A restauração derruba todas as sessões

`usuarios.json` foi trocado; seguir logado seria estar autenticado por um
cadastro que não existe mais. A tela avisa e manda para o login.

## Um defeito pego na tela

A primeira versão gravava o pacote **com o nome da pasta de destino** quando ela
ainda não existia — `Path.is_dir()` é falso para pasta inexistente, e o `.fbk`
saía sem extensão e sem data. Regra agora: **caminho sem `.fbk` é pasta**, e é
criada. Há dois testes travando isso.

## Onde fica na interface

**Home → 💾 Backup** (só para o administrador) → `web/backup.html`:
*Criar backup completo* e *Restaurar instalação anterior*.

As rotas ficam no `fiscale_server.py`, e não no módulo NFS-e: backup tem de
funcionar **mesmo quando o módulo não sobe** — é aí que ele mais importa.
Exigem administrador **e** acesso local: o `.fbk` carrega o escritório inteiro e
não sai por navegador da rede.

Pela linha de comando, para quem não quer abrir a tela:

```bash
python exportar_backup.py
```
```bash
python restaurar_backup.py caminho\FISCALE-backup-2026-08-11-2025.fbk
```

## O ELO não participa

Não é importado, não é consultado, não é requisito. Backup e restauração
funcionam com o ELO ligado, desligado ou inexistente. Há teste para isso.

---

---

# PORT 3 — segurança, diagnóstico e preparação

## Decisões

### P12 — Segredo de tela sai do texto claro

`fiscale_segredos.py`. Ao **gravar**, o servidor separa o que é segredo e
protege com DPAPI num campo irmão:

```
"acesso":           {"Código da empresa": "8PXXV", "Usuário": "MASTER"}
"acesso_protegido": {"Senha": "dpapi:AQAAANCM..."}
```

Ao **ler**, o segredo não vai para o navegador — vai só
`"acesso_estado": {"Senha": "ok"}` ou `"aguardando"`. A tela sabe mostrar
"Aguardando senha do portal" sem nunca ter tido a senha em mãos.

Três regras que custaram pensamento:

- **Campo vazio não apaga o guardado.** A tela manda o formulário inteiro a
  cada salvamento e nunca recebe a senha de volta; sem isso, mexer numa data
  apagaria o acesso ao portal.
- **Proteção que falha descarta o segredo**, nunca grava em claro. Perder a
  senha é recuperável; vazá-la, não.
- **A migração não apaga o claro antes de conferir** que a versão protegida
  abre e devolve o mesmo valor. O que falhar fica como está e aparece no
  relatório.

DPAPI é local: depois de restaurar noutra máquina a senha volta a "aguardando",
e é isso mesmo que se quer.

### P13 — Um mecanismo de backup só

`backup_para_drive.bat` copiava a pasta `dados` do projeto junto com o código.
Dois problemas, os dois medidos:

- **Falsa sensação de backup** — desde a PORT 1 os dados moram em
  `~/Fiscale/dados`, fora da pasta do projeto. O que subia era uma cópia de
  julho com 4 arquivos. Quem confiasse nisso perderia tudo.
- **Dado pessoal na nuvem** — aquele `state_plano.json` antigo levava CPF de 3
  pessoas físicas para o Google Drive.

Escolhida a combinação **A + B**: o `.bat` deixa de tocar em dados (`/XD dados`)
e passa a transportar os `.fbk` da Área de Trabalho. Backup é o `.fbk`; o `.bat`
distribui código e carrega o pacote. Descontinuar de vez não servia: o
`atualizar_do_drive.bat` depende dele para levar a versão nova à outra máquina.

### P14 — O diagnóstico não pode mentir sobre portabilidade

`diagnostico_instalacao.py`, 33 verificações em 9 grupos, cada uma com
`OK`/`ATENÇÃO`/`ERRO` e o que fazer em português. Nenhum traceback chega ao
usuário; nenhum segredo é impresso.

A primeira versão dizia **"Runtime portátil: OK"** porque o interpretador estava
dentro da pasta do Fiscale. Estava errado: uma `.venv` está dentro, e mesmo
assim não viaja — o `pyvenv.cfg` aponta para a instalação base, e é de lá que
vem a biblioteca padrão. O teste certo é onde mora `sys.base_prefix`.

---

---

# PORT 4 — distribuição portátil

## O pacote

```
FISCALE-Portable\           82,6 MB  ·  2.313 arquivos  ·  .zip com 32,5 MB
    Fiscale.bat             duplo clique — é só isto que o usuário abre
    Diagnostico.bat         quando algo não abrir
    runtime\        32,0 MB Python 3.12.10 oficial, assinado pela PSF
    libs\           49,2 MB os 11 pacotes, instalados pelo pip DESTE runtime
    app\             1,4 MB o código do Fiscale
    dados\                  os dados, com a marca .fiscale-portatil
    LEIA-ME.txt
```

`python montar_portatil.py [saída] [--zip] [--cache pasta]` monta tudo do zero.

## Decisões

### P15 — *embeddable* oficial, não PyInstaller

O Smart App Control desta máquina bloqueia `.exe` sem assinatura, e um build
nosso não é assinado. O `python.exe` do pacote oficial da python.org é
**assinado pela Python Software Foundation** — conferido: `Valid`. Atualizar o
Fiscale volta a ser copiar `.py`, sem rebuild.

As três armadilhas do *embeddable*, e o que foi feito:

1. **Vem sem pip** → baixa o `get-pip` e roda com o próprio runtime.
2. **O `._pth` limita o `sys.path` e desliga o `site`** → reescrito com `libs`,
   `app`, `app\nfse`, `app\nfse\backend` e `import site`.
3. **Código nativo precisa casar** (`cryptography`, `Pillow`) → instalado com o
   pip **deste** runtime, nunca copiado de outro lugar.

### P16 — `tkinter` eliminado, não empacotado

O inventário apontou `tkinter` como obstáculo. Antes de carregar ~10 MB de
Tcl/Tk, fui ver onde era usado:

| Rota | Quem chamava | Destino |
|---|---|---|
| `/api/escolher-arquivo` | só a interface antiga `nfse/frontend/`, que o Fiscale não serve | removida |
| `/api/escolher-arquivos` | idem | removida |
| `/api/escolher-pasta` | `web/nfe.html`, pasta vigiada | substituída |

No lugar entrou `GET /api/pastas`: o servidor **lista** subpastas e a tela
navega. Sem regressão — e corrige um defeito que já existia: o diálogo do
`tkinter` abria na máquina do **servidor**, então quem acessava o Fiscale pela
rede clicava e nada acontecia na tela dele.

## Três defeitos que só o teste em pacote revelou

**1. A marca portátil não pegava.** `fiscale_dados.py` mora em `app\`, e
procurava `.fiscale-portatil` em `app\dados` — mas ela fica ao lado de `app\`,
que é onde deve ficar (atualizar o Fiscale é copiar `app\` por cima, e isso não
pode chegar perto dos dados). Resultado: o pacote de pendrive gravava em
`C:\Users\<voce>\Fiscale`.

**2. O diagnóstico dizia "PENDENTE" de dentro do próprio pacote.** Mesma classe:
`runtime\` é **irmão** de `app\`, não filho.

**3. 🔴 Restaurar em "Substituir" desligava o modo portátil.** A marca ia junto
com o resto para a pasta guardada, e no reinício seguinte o Fiscale voltava a
apontar para o perfil do usuário — em silêncio, logo depois de uma restauração.
No teste, isso fez o servidor abrir a pasta de dados **real**. Agora a marca é
reposta, e só quando já existia.

**4. Restaurar em "Substituir" falhava com o Fiscale aberto.** O `sessoes.db`
segura a pasta no Windows. O servidor fecha o banco antes e reabre depois; e o
motor, não conseguindo mover a pasta inteira, move item a item. Os testes da
PORT 2 não pegaram porque rodavam sem servidor vivo.

Os quatro têm teste travando a regressão.

---

## O que ainda NÃO está resolvido

| # | Pendência | Fase |
|---|---|---|
| 1 | 12 caminhos a Downloads/Desktop/AppData nos dados — funcionam aqui, não em outra máquina | — |
| 2 | O pacote não é assinado como um todo; quem confia é o `python.exe` da PSF | — |
| 3 | Backup agendado / rotina automática | não planejado |
| 4 | `fiscale.spec` desatualizado | só se voltar ao PyInstaller |
| 5 | Ao encerrar pelo terminate, o processo-filho do NFS-e às vezes sobrevive | 🟢 baixo |

---

## Como a migração real vai acontecer

Não roda sozinha enquanto o Fiscale estiver aberto — ela acontece **no próximo
arranque**. Sequência:

1. Fechar o Fiscale pelo ícone ao lado do relógio.
2. Abrir de novo. No arranque, `garantir()` traz `~/SistemaNFSe/dados` (150 MB,
   ~30 mil arquivos) para `~/Fiscale/dados`. Ensaiado em escala real: **0,2 s**,
   porque é renomeação, não cópia.
3. Conferir: 21 empresas na tela de Clientes, notas e competências no lugar.
4. `~/SistemaNFSe/dados` vira `~/SistemaNFSe/dados-migrado-para-fiscale`.
   **Conferir antes de apagar** — o backup em
   `C:\Users\nineq\Fiscale-backups\pre-port1-20260811` também continua lá.

Se algo sair errado: fechar o Fiscale e restaurar as duas pastas a partir do
backup. Nada foi apagado.
