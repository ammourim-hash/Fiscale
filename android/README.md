# FISCALE para Android

App Android do **Sistema Web FISCALE**. É um WebView apontado para o servidor
do escritório, e não uma segunda implementação: login, sessão (cookie
`HttpOnly`), papéis, Central de Aplicações, ELO e telas fiscais são as mesmas
que o navegador do PC recebe. O app não guarda senha, token nem certificado;
guarda só o endereço do servidor.

## O que o app faz

| Recurso | Como |
|---|---|
| Endereço do servidor | Tela própria, com **Testar conexão** (pede `/login.html`, rota livre) e aviso quando o endereço é HTTP fora de rede privada (D72/D80) |
| Sessão | Cookie do próprio FISCALE, gravado no disco ao sair do app: com "Manter conectado" (7 dias) ele reabre já dentro |
| Importar XML/planilha | `<input type=file>` abre o seletor do Android, sem filtro de tipo, porque XML costuma vir como `octet-stream` |
| Baixar arquivos | Downloads comuns (com o cookie da sessão), `blob:` e `data:` vão para `Downloads/FISCALE`, com a opção **Abrir** |
| ELO | Abre na mesma tela, e o **voltar** retorna ao FISCALE |
| Sem conexão | Tela local "O FISCALE não respondeu", com **Tentar de novo** e **Alterar servidor** |
| HTTPS | Só certificados do sistema (Let's Encrypt real). Certificado inválido é recusado, nunca aceito |
| Trocar servidor | Toque longo no ícone → **Alterar servidor**, ou **voltar** na tela inicial |

Requer Android 8.0 ou mais novo (API 26).

## Endereços

- Destino (D97): `https://app.sistemafiscale.com.br`, **somente pela rede privada**.
- Hoje (D-AMB-1): `http://AMMOURIM:8777` na rede do escritório.
- Digitado sem `http(s)://` e sem porta, o app completa com `http://` e `:8777`.

## Build

O CI (`.github/workflows/android.yml`) gera `FISCALE-debug.apk` a cada push em
`android/` e o publica como artefato `FISCALE-android`. Quando um release é
publicado, o APK também é anexado a ele.

Para um APK **release assinado**, cadastre nos *secrets* do repositório:
`FISCALE_KEYSTORE_B64` (o `.jks` em base64), `FISCALE_KEYSTORE_SENHA`,
`FISCALE_KEY_ALIAS` e `FISCALE_KEY_SENHA`.

Build local (Android SDK + JDK 17):

```bash
cd android
./gradlew assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

## Estrutura

```
android/app/src/main/
  java/br/com/sistemafiscale/app/
    MainActivity.kt      WebView, arquivos, downloads, voltar, ponte JS
    ServidorActivity.kt  tela do endereço do servidor
    Servidor.kt          normalização, teste de conexão, aviso de rede
  assets/erro.html       tela "sem conexão"
  res/xml/network_security_config.xml
```

A ponte JS (`FiscaleApp`) só atende a tela local de erro e as páginas do
servidor configurado. Qualquer outra origem é ignorada.
