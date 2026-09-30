package br.com.sistemafiscale.app

import android.content.Context
import android.net.Uri
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL

/**
 * Endereço do Sistema Web FISCALE e as regras em volta dele.
 *
 * O app não guarda senha, token nem cookie à parte: a sessão é a do próprio
 * FISCALE (cookie HttpOnly), mantida pelo WebView como em qualquer navegador.
 * Aqui só fica o endereço do servidor.
 */
object Servidor {
    private const val PREFS = "fiscale"
    private const val CHAVE = "servidor"

    /** Porta interna do FISCALE (D72: porta interna, nunca endereço público). */
    const val PORTA_PADRAO = 8777

    fun ler(ctx: Context): String? =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(CHAVE, null)

    fun gravar(ctx: Context, url: String) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(CHAVE, url).apply()
    }

    /**
     * Normaliza o que a pessoa digitou.
     *
     *  - sem esquema: `http://` e, sem porta, a 8777 (o jeito de hoje na rede do escritório);
     *  - com esquema: respeitado como veio;
     *  - barra final removida, para `base + "/api/..."` não virar `//api`.
     *
     * Devolve null quando não dá para entender como endereço.
     */
    fun normalizar(digitado: String): String? {
        var t = digitado.trim()
        if (t.isEmpty()) return null
        val temEsquema = t.startsWith("http://", true) || t.startsWith("https://", true)
        if (!temEsquema) {
            t = "http://$t"
        }
        val uri = Uri.parse(t)
        val host = uri.host ?: return null
        if (host.isBlank() || host.contains(' ')) return null
        var base = "${uri.scheme!!.lowercase()}://$host"
        val porta = uri.port
        if (porta > 0) {
            base += ":$porta"
        } else if (!temEsquema) {
            base += ":$PORTA_PADRAO"
        }
        val caminho = (uri.path ?: "").trimEnd('/')
        return base + caminho
    }

    fun ehHttps(url: String) = url.startsWith("https://", true)

    /**
     * O host parece estar dentro de uma rede privada?
     *
     * Serve só para AVISAR (D72: o FISCALE não vai para a internet). Não é
     * barreira de segurança — quem decide o que é alcançável é a rede.
     */
    fun hostPrivado(url: String): Boolean {
        val host = Uri.parse(url).host?.lowercase() ?: return false
        if (host == "localhost" || !host.contains('.')) return true // AMMOURIM, localhost
        if (host.endsWith(".local") || host.endsWith(".lan") || host.endsWith(".ts.net")) return true
        val partes = host.split('.')
        if (partes.size == 4 && partes.all { it.toIntOrNull() in 0..255 }) {
            val a = partes[0].toInt()
            val b = partes[1].toInt()
            return a == 10 || a == 127 ||
                (a == 192 && b == 168) ||
                (a == 172 && b in 16..31) ||
                (a == 100 && b in 64..127) || // CGNAT: faixa usada pelo Tailscale
                (a == 169 && b == 254)
        }
        return false
    }

    /** HTTP puro fora de rede privada: senha trafegaria aberta na internet. */
    fun precisaAviso(url: String) = !ehHttps(url) && !hostPrivado(url)

    sealed class Teste {
        data class Ok(val detalhe: String) : Teste()
        data class Falha(val motivo: String) : Teste()
    }

    /**
     * Pede a tela de login, que é rota livre no FISCALE (não exige sessão).
     * Rodar FORA da thread principal.
     */
    fun testar(url: String): Teste {
        return try {
            val host = Uri.parse(url).host ?: return Teste.Falha("Endereço inválido.")
            try {
                InetAddress.getByName(host)
            } catch (e: Exception) {
                return Teste.Falha("O nome \"$host\" não foi encontrado nesta rede. Confira se o celular está na rede do escritório ou, fora dela, se o Tailscale está conectado.")
            }
            val c = URL("$url/login.html").openConnection() as HttpURLConnection
            c.connectTimeout = 8000
            c.readTimeout = 8000
            c.instanceFollowRedirects = false
            c.requestMethod = "GET"
            c.setRequestProperty("User-Agent", "FISCALE-Android/${BuildConfig.VERSION_NAME}")
            val codigo = c.responseCode
            val corpo = if (codigo in 200..299) {
                c.inputStream.bufferedReader().use { it.readText().take(20000) }
            } else ""
            c.disconnect()
            when {
                codigo in 200..299 && corpo.contains("FISCALE", ignoreCase = true) ->
                    Teste.Ok("FISCALE respondeu (HTTP $codigo).")
                codigo in 200..399 ->
                    Teste.Ok("O servidor respondeu (HTTP $codigo), mas a página não se identificou como FISCALE. Confira o endereço.")
                else -> Teste.Falha("O servidor respondeu HTTP $codigo.")
            }
        } catch (e: javax.net.ssl.SSLException) {
            Teste.Falha("Falha no HTTPS: ${e.message}. O certificado precisa ser válido (Let's Encrypt), não improvisado.")
        } catch (e: java.net.SocketTimeoutException) {
            Teste.Falha("O servidor não respondeu em 8 segundos.")
        } catch (e: java.net.ConnectException) {
            Teste.Falha("Conexão recusada. O FISCALE está rodando e a porta está certa?")
        } catch (e: Exception) {
            Teste.Falha(e.message ?: e.javaClass.simpleName)
        }
    }
}
