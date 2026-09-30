#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fiscale_papeis.py — quem alcança qual rota.

POR QUE ISTO EXISTE
    O FISCALE deixou de ser instalação pessoal: cada escritório tem UMA base,
    e todo mundo ali entra nela pelo navegador. Até aqui o cadastro de usuários
    era binário — `admin` ou não — e "não admin" significava, na prática, quase
    tudo liberado: quem entrasse podia cadastrar certificado, informar a senha
    de um `.pfx`, apontar a pasta vigiada e mexer na integração com o Elo.

    A decisão: quem trabalha no escritório **consulta e apura livremente, e não
    mexe em certificado nem em usuário**.

ONDE O PORTÃO MORA — E POR QUE SÓ PODE SER AQUI
    O módulo NFS-e é um processo filho FastAPI que escuta em `127.0.0.1` e
    **não tem autenticação nenhuma**: ele confia em quem o chama. Quem chama é
    o `_proxy_nfse` do `fiscale_server.py`, que até agora repassava tudo sem
    olhar quem era. Por isso o portão é aplicado no proxy, antes do repasse, e
    `nfse/backend/main.py` não precisa ser tocado.

    O corolário é uma regra de operação, não de código: **a porta interna do
    módulo NFS-e nunca pode ser exposta na rede**. Se ela for, o portão é
    contornável e nada aqui vale.

LISTA DE PERMISSÃO, NÃO DE NEGAÇÃO
    Rota que ninguém classificou **nasce restrita ao admin**. Uma lista de
    negação falharia aberto: bastaria alguém acrescentar uma rota nova e
    esquecer de negá-la.

    O risco conhecido dessa escolha é apodrecer — a rota nova é esquecida, o
    operador leva 403 sem motivo e alguém "conserta" afrouxando a regra. O
    antídoto é `teste_papeis.py`: ele varre os decoradores `@app.*` de
    `main.py` e as rotas do `fiscale_server.py` e **falha** quando encontra
    rota que não passou por aqui. Classificar deixa de ser lembrança e vira
    parte de acrescentar rota.

O QUE ESTE MÓDULO NÃO FAZ
    Não autentica (isso é `fiscale_sessoes`), não decide se a requisição veio
    da própria máquina (isso é `_e_local`, e continua valendo por cima: o
    backup exige admin **e** estar no PC), e não conhece o conteúdo das rotas.
    Só responde "este papel alcança este método+rota?".
"""
from __future__ import annotations

ADMIN = "admin"
OPERADOR = "operador"


def papel_do_registro(reg: dict | None, nome: str) -> str:
    """O papel gravado em `usuarios.json`.

    O cadastro guarda `{"admin": true|false}`. Quem não tem a marca é
    operador — e o usuário chamado `admin` é admin mesmo sem ela, que é a
    regra que já valia em `eh_admin()` e não pode mudar sob os pés de quem já
    usa o sistema.
    """
    reg = reg or {}
    return ADMIN if bool(reg.get("admin", nome == "admin")) else OPERADOR


# ── O que o OPERADOR alcança ────────────────────────────────────────────────
#
# Trabalho fiscal inteiro: consultar, apurar, capturar, importar, exportar.
# O que ele NÃO alcança está na lista de admin mais abaixo, que é consultada
# primeiro.

_OPERADOR_EXATAS = {
    # A FILA DO CENTRO DE OPERAÇÕES é do operador: ela É o trabalho do dia.
    # Só leitura, sem segredo e com o CNPJ mascarado. O que exige
    # administrador (backup, diagnóstico) continua nas rotas próprias.
    ("GET", "/api/centro"),
    # A ficha da empresa é leitura do cadastro: trabalho de quem opera. Não traz
    # segredo — nem senha, nem caminho de .pfx, nem conteúdo fiscal.
    ("GET", "/api/clientes/ficha"),
    # Situação fiscal: LER é trabalho de quem opera. Saber que a certidão da
    # empresa vence semana que vem é o dia a dia do escritório; guardar
    # documento novo no acervo é que exige administrador.
    ("GET", "/api/situacao/panorama"),
    ("GET", "/api/situacao/documento"),
    ("GET", "/api/situacao/tentativas"),
    # CLASSIFICAR É DO OPERADOR (Fase H, decisão de 12/09/2026). Ler o extrato
    # do Recife e registrar o que ele diz é o trabalho diário de quem opera; o
    # documento original não muda, a conclusão é gravada à parte com o nome
    # de quem a fez, e reclassificar sucede sem apagar a anterior. Guardar
    # documento novo e consultar o órgão continuam com o administrador.
    ("POST", "/api/situacao/classificar"),
    # Sessão e identidade
    ("GET", "/api/quem"),
    # Token anti-CSRF da PRÓPRIA sessão. É de qualquer papel porque é o que
    # prova que o pedido veio da nossa tela; quem pode agir continua sendo
    # decidido rota a rota, aqui embaixo.
    ("GET", "/api/csrf"),
    ("POST", "/api/logout"),
    ("POST", "/api/senha"),            # troca a PRÓPRIA senha
    # Encerrar o Fiscale já é protegido por `_e_local()`: pela rede derrubaria
    # o servidor do escritório inteiro. Quem está no PC pode.
    ("POST", "/api/encerrar"),
    # A regra de senha: mínimo, máximo e a frase da tela. Não diz nada sobre
    # pessoa nenhuma, e a tela de primeiro acesso precisa dela antes de haver
    # usuário — por isso também está em `ROTAS_LIVRES`.
    ("GET", "/api/politica-senha"),
    # Consultas públicas de apoio
    ("GET", "/api/optante"),
    ("POST", "/api/optantes"),
    # As exportações da varredura de optantes: LEITURA de base pública e
    # tabulação do que a consulta acima já devolve. Barrar o operador aqui
    # seria deixá-lo consultar na tela e não conseguir levar o resultado.
    ("GET", "/api/optantes/universo"),
    ("GET", "/api/optantes/relatorio"),
    ("GET", "/api/optantes/exportar"),
    ("GET", "/api/ncm"),
    ("POST", "/api/xlsx-codigos"),
    ("POST", "/api/upload"),
    # Entrar no Elo é login do próprio usuário naquele sistema; configurar e
    # sincronizar a base de clientes com ele é outra coisa, e é do admin.
    ("POST", "/api/elo/abrir"),
    # Empresas: ver a lista para poder trabalhar. `GET /api/certificados`
    # nunca devolve `senha_protegida` — só `senha_salva` e `senha_estado`.
    ("GET", "/api/certificados"),
    ("GET", "/api/certificados/pendentes"),
    # Navegar pastas do servidor: necessário para importar XML de uma pasta.
    ("GET", "/api/pastas"),
    # O endereço que as outras máquinas do escritório usam. Não é segredo —
    # quem está na rede já sabe o nome da máquina — e serve para o colega
    # passar o endereço adiante sem depender do administrador.
    ("GET", "/api/escritorio"),
}

_OPERADOR_PREFIXOS = (
    # Situação fiscal: o servidor decide o bloco inteiro por `startswith`, e é
    # o prefixo que precisa de papel. Ele é do OPERADOR e não do admin porque
    # o bloco só LÊ — e porque a ordem de `classificar()` faz admin específico
    # vencer prefixo largo: se um dia surgir um GET aqui que só o admin possa
    # ver, basta declará-lo em `_ADMIN_EXATAS`, que ele passa na frente.
    # A ESCRITA (`POST /api/situacao/importar`) está na lista de admin.
    ("GET", "/api/situacao/"),
    # Módulo NFS-e — o trabalho fiscal. As exceções estão na lista de admin.
    ("GET", "/api/notas"),
    ("GET", "/api/competencias"),
    ("GET", "/api/danfse"),
    ("GET", "/api/apuracao-federal"),
    ("GET", "/api/federal-empresa"),
    # ISS a recolher: LEITURA da apuração municipal das notas, igual ao quadro
    # federal ao lado — trabalho de quem opera.
    ("GET", "/api/iss-recolher"),
    ("GET", "/api/apuracao-trimestral"),
    ("GET", "/api/vencimentos"),
    ("GET", "/api/nfse/xml"),
    ("GET", "/api/painel"),
    ("GET", "/api/prefeituras"),
    ("GET", "/api/classificador/"),
    ("GET", "/api/clientes/inscricoes"),
    # O modelo da planilha não tem dado de ninguém: são os nomes das colunas
    # e uma linha inventada. Barrá-lo seria zelo sem objeto.
    ("GET", "/api/clientes/modelo"),
    ("GET", "/api/nfe/"),
    # CT-e: LEITURA do acervo e o seletor de empresas. O diagnostico
    # (`/api/cte/situacao`) e a sincronizacao ficam com o admin, e por
    # isso NAO estao aqui — a lista do admin e consultada antes desta.
    ("GET", "/api/cte/"),
    ("POST", "/api/sincronizar"),
    ("POST", "/api/exportar"),
    ("POST", "/api/exportar-pdfs"),
    ("POST", "/api/exportar-pdfs-local"),
    ("POST", "/api/recife/sincronizar"),
    ("POST", "/api/painel/baixar-historico"),
    ("POST", "/api/prefeituras/"),
    ("POST", "/api/classificador"),
    ("POST", "/api/nfe/"),
    # CONTÁBIL (Fase Contábil 1, 19/09/2026): plano de contas, lançamentos,
    # extratos e conciliação são o trabalho diário de quem opera — o mesmo
    # raciocínio de "consulta e apura livremente". Não há certificado, senha
    # nem usuário aqui dentro. Toda ESCRITA exige o CSRF da sessão (conferido
    # no servidor), e todo lançamento nasce PENDENTE e registra quem o criou
    # e quem o confirmou.
    ("GET", "/api/contabil/"),
    ("POST", "/api/contabil/"),
    # Central de Atualizações Fiscais: é jornal, e todo mundo lê o jornal.
    # Ela não altera cálculo nenhum, e o operador precisa saber o que mudou
    # tanto quanto o administrador. Configurar a cadência é do admin.
    ("GET", "/api/fiscal/atualizacoes"),
    ("POST", "/api/fiscal/atualizacoes/verificar"),
    ("POST", "/api/fiscal/atualizacoes/lido"),
    # Arquivos que a própria tela subiu (logo do DANFSE, planilhas).
    ("GET", "/dados/uploads/"),
)


# ── O que só o ADMIN alcança ────────────────────────────────────────────────
#
# Consultado ANTES da lista do operador: é como `POST /api/nfe/entrada/config`
# fica restrito sem tirar do operador o resto de `/api/nfe/`.

_ADMIN_EXATAS = {
    # Usuários
    ("GET", "/api/usuarios"),
    ("POST", "/api/usuarios"),
    ("POST", "/api/usuarios/excluir"),
    # Diagnóstico expõe caminhos, versões e o estado do cofre da máquina.
    ("GET", "/api/diagnostico"),
    # Registro de acessos (Fase 3). Diz quem entrou, quando e de qual IP —
    # é o hábito de entrada das pessoas do escritório, e nem o operador
    # consulta, nem exporta, nem apaga. Não existe rota de exportação nem de
    # remoção: a única remoção é a retenção por idade, no servidor.
    ("GET", "/api/auditoria"),
    ("GET", "/api/auditoria/saude"),
    # Situação fiscal: certidões e extratos das três esferas. Guardar
    # documento oficial no acervo do escritório e apagar dúvida sobre
    # regularidade é trabalho de quem administra. A LEITURA fica com o
    # operador — ver a lista dele mais abaixo.
    ("POST", "/api/situacao/importar"),
    # Consultar é falar com a Receita em nome do cliente (e pode gastar
    # chamada bilhetada): fica com quem administra. CLASSIFICAR não está aqui —
    # ver a lista do operador.
    ("POST", "/api/situacao/consultar"),
    # Cadastro 1: importar planilha de empresas. Escreve o cadastro inteiro
    # do escritório — é trabalho de quem administra, não de quem opera.
    ("POST", "/api/clientes/importar/previa"),
    ("POST", "/api/clientes/importar/aplicar"),
    # E o prefixo inteiro, porque o servidor decide por ele: qualquer caminho
    # novo em `/api/clientes/importar/` nasce restrito, sem depender de alguém
    # lembrar de vir aqui declará-lo.
    ("POST", "/api/clientes/importar/"),
    # Certificados: cadastrar, excluir, informar senha, marcar procurador.
    ("POST", "/api/certificados"),
    ("POST", "/api/certificados/lote"),
    ("POST", "/api/certificados/senha"),
    ("POST", "/api/certificados/procurador"),
    # Pasta vigiada: define de onde o sistema passa a ler XML sozinho.
    ("POST", "/api/nfe/entrada/config"),
    ("GET", "/api/nfe/entrada/config"),
    # Abre uma pasta no Explorer DO SERVIDOR — não faz sentido pela rede e
    # não é do operador.
    ("POST", "/api/abrir-pasta"),
    # Reconciliação cadastral: expõe quais empresas estão fora do seletor e
    # sugere IE lida dos XML. É trabalho de cadastro, não de operação.
    ("GET", "/api/nfe/pendencias-cadastrais"),
    ("GET", "/api/nfe/empresas/ie-sugerida"),
    # Cadência da Central: define de quanto em quanto tempo a máquina bate
    # nos portais do governo. É configuração do servidor.
    ("POST", "/api/fiscal/atualizacoes/config"),
    # Primeira sincronização: puxa a fila inteira da SEFAZ e move o
    # checkpoint, que não retrocede. É decisão de quem administra.
    ("GET", "/api/nfe/onboarding"),
    ("POST", "/api/nfe/onboarding"),
}

_ADMIN_PREFIXOS = (
    # Backup e restauração. Exigem também `_e_local()` no servidor: o `.fbk`
    # carrega o escritório inteiro e não sai por navegador remoto.
    # CT-e: DIAGNOSTICO e SINCRONIZACAO sao do administrador.
    # `/api/cte/situacao` mostra estado de certificado e checkpoint, e
    # `/api/cte/sincronizar` e a unica rota capaz de gastar cota do CNPJ
    # do cliente. Leitura do acervo fica com o operador, logo abaixo.
    ("GET", "/api/cte/situacao"),
    ("POST", "/api/cte/sincronizar"),
    ("POST", "/api/cte/"),
    ("GET", "/api/backup/"),
    ("POST", "/api/backup/"),
    # Decisões administrativas da ingestão NF-e: encerrar/reabrir revisão de
    # sequência e marcar início de cobertura. Não consultam a SEFAZ, mas
    # mudam o estado que decide se um documento será buscado — é decisão de
    # quem administra, e o operador não entra aqui.
    ("GET", "/api/nfe/admin/"),
    ("POST", "/api/nfe/admin/"),
    # Integração com o Elo: configuração e envio da base de clientes.
    ("GET", "/api/elo/sync"),
    ("POST", "/api/elo/sync"),
    # Excluir certificado — `DELETE /api/certificados/{id}`.
    ("DELETE", "/api/certificados"),
)

# Páginas que só o admin abre. As rotas por trás delas já recusam sozinhas;
# gatear a página evita a tela abrir vazia e cheia de erro.
_ADMIN_PAGINAS = ("/usuarios.html", "/backup.html", "/nfe_pendencias.html",
                  "/seguranca_acessos.html")


# ── Estado de tela: classificado por módulo, não em bloco ───────────────────
#
# `GET /api/state/<mod>` é leitura e o operador precisa de toda ela.
#
# A ESCRITA É LISTA DE PERMISSÃO, PELO MESMO MOTIVO DAS ROTAS
#     A primeira versão disto negava só duas telas conhecidas e liberava o
#     resto — e um módulo que ninguém tinha classificado (`nfe_vendas`) passou
#     despercebido. Numa lista de negação isso é o comportamento normal:
#     esquecer libera. Aqui, esquecer restringe.
#
#     `clientes` é cadastro de empresa (caminha junto do certificado) e não
#     entraria aqui de qualquer forma. O mesmo vale para qualquer tela que
#     venha a guardar credencial: escrita de credencial é do admin.

_ESTADO_ESCRITA_OPERADOR = {
    "classificador",    # configuração de blocos do classificador
    "nfe_vendas",       # o mapa de vendas da tela de NF-e, que é trabalho dele
}


def _casa(regra: tuple[str, str], metodo: str, rota: str, exato: bool) -> bool:
    m, p = regra
    if m != metodo:
        return False
    return rota == p if exato else (rota == p or rota.startswith(p))


def classificar(metodo: str, rota: str) -> str | None:
    """O papel mínimo para esta rota, ou None se ninguém a classificou.

    None é o sinal que `teste_papeis.py` procura. Em produção `pode()` trata
    None como admin — falhar fechado é o comportamento certo para rota que
    ninguém pensou a respeito.
    """
    metodo = (metodo or "").upper()
    rota = rota or ""

    # Estado de tela, antes das listas gerais: o papel depende do módulo.
    if rota.startswith("/api/state/"):
        if metodo == "GET":
            return OPERADOR
        mod = rota.split("/api/state/", 1)[1]
        return OPERADOR if mod in _ESTADO_ESCRITA_OPERADOR else ADMIN

    # Admin primeiro: é o que torna a exceção mais específica vencer o
    # prefixo largo do operador.
    if (metodo, rota) in _ADMIN_EXATAS:
        return ADMIN
    if any(_casa(r, metodo, rota, False) for r in _ADMIN_PREFIXOS):
        return ADMIN
    if metodo == "GET" and rota in _ADMIN_PAGINAS:
        return ADMIN

    if (metodo, rota) in _OPERADOR_EXATAS:
        return OPERADOR
    if any(_casa(r, metodo, rota, False) for r in _OPERADOR_PREFIXOS):
        return OPERADOR

    # Tela e arquivo estático: o operador usa o sistema inteiro pelo
    # navegador, então tudo que não é `/api/` e não é página de admin é dele.
    if metodo == "GET" and not rota.startswith("/api/"):
        return OPERADOR

    return None


def pode(papel: str, metodo: str, rota: str) -> bool:
    """Este papel alcança esta rota? Rota não classificada é só do admin."""
    if papel == ADMIN:
        return True
    return classificar(metodo, rota) == OPERADOR
