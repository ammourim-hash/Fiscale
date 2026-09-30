package br.com.sistemafiscale.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.util.Base64
import android.view.View
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.MimeTypeMap
import android.webkit.SslErrorHandler
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.Toast
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * O FISCALE no Android: o Sistema Web do escritório (D97) num WebView.
 *
 * Nada da regra de negócio mora aqui. Login, sessão (cookie HttpOnly), papéis,
 * Central, ELO e as telas fiscais são as do servidor — o mesmo código que o
 * navegador do PC recebe. O app acrescenta só o que um navegador daria:
 * escolher arquivo (importar XML), baixar arquivo, voltar, e uma tela clara
 * quando o servidor não está ao alcance.
 */
class MainActivity : Activity() {

    companion object {
        const val EXTRA_RECARREGAR = "recarregar"
        private const val PEDIDO_ARQUIVO = 10
        private const val PEDIDO_PERMISSAO = 11
        private const val PAGINA_ERRO = "file:///android_asset/erro.html"
    }

    private lateinit var web: WebView
    private lateinit var progresso: ProgressBar
    private var base: String = ""
    private var escolhaPendente: ValueCallback<Array<Uri>>? = null
    private var limparHistorico = false

    /** URL da página em exibição, lida pela ponte JS fora da thread de UI. */
    @Volatile private var paginaAtual: String = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val salvo = Servidor.ler(this)
        if (salvo == null) {
            startActivity(Intent(this, ServidorActivity::class.java))
            finish()
            return
        }
        base = salvo
        setContentView(R.layout.activity_main)
        web = findViewById(R.id.web)
        progresso = findViewById(R.id.progresso)
        configurarWebView()

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState)
        } else {
            abrirInicio()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val novo = Servidor.ler(this) ?: return
        if (novo != base || intent.getBooleanExtra(EXTRA_RECARREGAR, false)) {
            base = novo
            abrirInicio()
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::web.isInitialized) web.saveState(outState)
    }

    override fun onPause() {
        super.onPause()
        // Grava o cookie de sessão em disco: com "Manter conectado" (7 dias) o
        // app reabre já dentro, igual ao PWA.
        CookieManager.getInstance().flush()
    }

    private fun abrirInicio() {
        limparHistorico = true
        // "/" leva à Central de Aplicações; sem sessão, o servidor manda ao login.
        web.loadUrl("$base/")
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configurarWebView() {
        val s = web.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.loadWithOverviewMode = true
        s.useWideViewPort = true
        s.builtInZoomControls = true
        s.displayZoomControls = false
        s.setSupportZoom(true)
        s.allowFileAccess = false
        s.allowContentAccess = false
        s.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        s.javaScriptCanOpenWindowsAutomatically = true
        // Sem janelas extras: links e formulários com alvo novo (ex.: o ELO)
        // abrem nesta mesma tela, e o "voltar" retorna ao FISCALE.
        s.setSupportMultipleWindows(false)
        s.userAgentString = s.userAgentString + " FISCALE-Android/" + BuildConfig.VERSION_NAME

        val cookies = CookieManager.getInstance()
        cookies.setAcceptCookie(true)
        // O ELO embutido (iframe) é outra origem e grava o cookie dele.
        cookies.setAcceptThirdPartyCookies(web, true)

        web.addJavascriptInterface(Ponte(), "FiscaleApp")
        web.webViewClient = Cliente()
        web.webChromeClient = Cromo()
        web.setDownloadListener { url, _, disposicao, mime, _ ->
            baixar(url, disposicao, mime)
        }
    }

    // ---------------------------------------------------------------- navegação

    private inner class Cliente : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            return when (uri.scheme?.lowercase()) {
                "http", "https", "about", "blob", "data" -> false
                else -> {
                    // mailto:, tel:, whatsapp:, intent: … vão para o app certo.
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, uri))
                    } catch (e: ActivityNotFoundException) {
                        Toast.makeText(this@MainActivity, "Nenhum app abre este link.", Toast.LENGTH_SHORT).show()
                    }
                    true
                }
            }
        }

        override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
            paginaAtual = url
            progresso.visibility = View.VISIBLE
        }

        override fun doUpdateVisitedHistory(view: WebView, url: String, isReload: Boolean) {
            paginaAtual = url
        }

        override fun onPageFinished(view: WebView, url: String) {
            progresso.visibility = View.GONE
            CookieManager.getInstance().flush()
            if (limparHistorico && !url.startsWith(PAGINA_ERRO)) {
                limparHistorico = false
                view.clearHistory()
            }
            // Guarda o nome dos links com atributo download: um blob: chega ao
            // app sem nome, e "relatorio.csv" é melhor que "download.bin".
            view.evaluateJavascript(
                """
                (function(){
                  if (window.__fiscaleApp) return; window.__fiscaleApp = true;
                  document.addEventListener('click', function(e){
                    var a = e.target && e.target.closest ? e.target.closest('a[download]') : null;
                    if (a) window.__fiscaleNome = a.getAttribute('download') || '';
                  }, true);
                })();
                """.trimIndent(), null
            )
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) {
                mostrarErro("${error.description}", request.url.toString())
            }
        }

        @SuppressLint("WebViewClientOnReceivedSslError")
        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            // Nunca aceitar certificado inválido (FISCALE_HTTPS.md: só Let's Encrypt real).
            handler.cancel()
            mostrarErro("Certificado HTTPS inválido (código ${error.primaryError}).", error.url)
        }
    }

    private fun mostrarErro(motivo: String, url: String) {
        progresso.visibility = View.GONE
        val q = "motivo=" + Uri.encode(motivo) + "&url=" + Uri.encode(url) + "&servidor=" + Uri.encode(base)
        web.loadUrl("$PAGINA_ERRO#$q")
    }

    private inner class Cromo : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) {
            progresso.progress = newProgress
            progresso.visibility = if (newProgress < 100) View.VISIBLE else View.GONE
        }

        // Importar XML/planilha: o <input type=file> das telas do FISCALE.
        override fun onShowFileChooser(
            webView: WebView,
            callback: ValueCallback<Array<Uri>>,
            params: FileChooserParams
        ): Boolean {
            escolhaPendente?.onReceiveValue(null)
            escolhaPendente = callback
            val intent = params.createIntent().apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                if (params.mode == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }
                // XML costuma vir como application/octet-stream no Android:
                // filtrar por tipo esconderia justamente as notas.
                type = "*/*"
            }
            return try {
                @Suppress("DEPRECATION")
                startActivityForResult(Intent.createChooser(intent, "Escolher arquivo"), PEDIDO_ARQUIVO)
                true
            } catch (e: ActivityNotFoundException) {
                escolhaPendente = null
                false
            }
        }
    }

    @Deprecated("API de Activity simples, sem AndroidX")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == PEDIDO_ARQUIVO) {
            val cb = escolhaPendente
            escolhaPendente = null
            if (cb == null) return
            if (resultCode != RESULT_OK || data == null) {
                cb.onReceiveValue(null)
                return
            }
            val clip = data.clipData
            val uris = if (clip != null) {
                Array(clip.itemCount) { clip.getItemAt(it).uri }
            } else {
                data.data?.let { arrayOf(it) }
            }
            cb.onReceiveValue(uris)
            return
        }
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
    }

    @Deprecated("API de Activity simples, sem AndroidX")
    override fun onBackPressed() {
        if (::web.isInitialized && web.canGoBack()) {
            web.goBack()
            return
        }
        AlertDialog.Builder(this)
            .setTitle("FISCALE")
            .setMessage("Servidor: $base")
            .setNegativeButton("Alterar servidor") { _, _ ->
                startActivity(Intent(this, ServidorActivity::class.java))
            }
            .setNeutralButton("Cancelar", null)
            .setPositiveButton("Sair") { _, _ -> finish() }
            .show()
    }

    // ---------------------------------------------------------------- ponte JS

    /**
     * Métodos que as páginas podem chamar como `FiscaleApp.xxx()`.
     * Só atendem a própria tela de erro do app e as páginas do servidor
     * configurado — qualquer outra origem é ignorada.
     */
    private inner class Ponte {
        private fun confiavel(): Boolean {
            val p = paginaAtual
            return p.startsWith(PAGINA_ERRO) || p == base || p.startsWith("$base/")
        }

        @JavascriptInterface
        fun tentarNovamente() {
            if (!confiavel()) return
            runOnUiThread { abrirInicio() }
        }

        @JavascriptInterface
        fun alterarServidor() {
            if (!confiavel()) return
            runOnUiThread { startActivity(Intent(this@MainActivity, ServidorActivity::class.java)) }
        }

        @JavascriptInterface
        fun versao(): String = BuildConfig.VERSION_NAME

        @JavascriptInterface
        fun salvarArquivo(base64: String, mime: String?, nome: String?) {
            if (!confiavel()) return
            val bytes = try {
                Base64.decode(base64, Base64.DEFAULT)
            } catch (e: IllegalArgumentException) {
                return
            }
            val tipo = if (mime.isNullOrBlank()) "application/octet-stream" else mime
            val arquivo = nomeSeguro(nome, tipo)
            runOnUiThread { gravarNosDownloads(arquivo, tipo) { it.write(bytes) } }
        }
    }

    // ---------------------------------------------------------------- downloads

    private fun baixar(url: String, disposicao: String?, mime: String?) {
        if (!podeGravarDownloads()) return
        when {
            url.startsWith("blob:") -> {
                // O conteúdo só existe dentro da página: ela mesma lê e entrega.
                val js = """
                    (function(){
                      fetch(${jsTexto(url)}).then(function(r){ return r.blob(); }).then(function(b){
                        var fr = new FileReader();
                        fr.onloadend = function(){
                          var s = String(fr.result); var i = s.indexOf(',');
                          FiscaleApp.salvarArquivo(s.substring(i+1), b.type || ${jsTexto(mime ?: "")}, window.__fiscaleNome || '');
                          window.__fiscaleNome = '';
                        };
                        fr.readAsDataURL(b);
                      });
                    })();
                """.trimIndent()
                web.evaluateJavascript(js, null)
            }
            url.startsWith("data:") -> {
                val virgula = url.indexOf(',')
                val cabecalho = url.substring(5, virgula.coerceAtLeast(5))
                val tipo = cabecalho.substringBefore(';').ifBlank { mime ?: "application/octet-stream" }
                val dados = url.substring(virgula + 1)
                val bytes = if (cabecalho.endsWith(";base64")) Base64.decode(dados, Base64.DEFAULT)
                else Uri.decode(dados).toByteArray()
                gravarNosDownloads(nomeSeguro(null, tipo), tipo) { it.write(bytes) }
            }
            else -> {
                val nome = URLUtil.guessFileName(url, disposicao, mime)
                val cookie = CookieManager.getInstance().getCookie(url)
                val agente = web.settings.userAgentString
                Toast.makeText(this, "Baixando $nome…", Toast.LENGTH_SHORT).show()
                // Baixa com o cookie da sessão: os arquivos do FISCALE exigem login.
                thread {
                    try {
                        val c = URL(url).openConnection() as HttpURLConnection
                        c.connectTimeout = 15000
                        c.readTimeout = 60000
                        if (cookie != null) c.setRequestProperty("Cookie", cookie)
                        c.setRequestProperty("User-Agent", agente)
                        val codigo = c.responseCode
                        if (codigo !in 200..299) {
                            c.disconnect()
                            runOnUiThread { Toast.makeText(this, "Falha ao baixar (HTTP $codigo).", Toast.LENGTH_LONG).show() }
                            return@thread
                        }
                        val tipo = mime ?: c.contentType ?: "application/octet-stream"
                        val bytes = c.inputStream.use { it.readBytes() }
                        c.disconnect()
                        runOnUiThread { gravarNosDownloads(nome, tipo) { it.write(bytes) } }
                    } catch (e: Exception) {
                        runOnUiThread { Toast.makeText(this, "Falha ao baixar: ${e.message}", Toast.LENGTH_LONG).show() }
                    }
                }
            }
        }
    }

    private fun jsTexto(s: String) = "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

    private fun nomeSeguro(nome: String?, mime: String): String {
        val limpo = (nome ?: "").replace(Regex("[\\\\/:*?\"<>|]"), "_").trim()
        if (limpo.isNotEmpty()) return limpo
        val ext = MimeTypeMap.getSingleton().getExtensionFromMimeType(mime.substringBefore(';')) ?: "bin"
        return "fiscale_${System.currentTimeMillis()}.$ext"
    }

    private fun podeGravarDownloads(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) return true
        if (checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED) return true
        requestPermissions(arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), PEDIDO_PERMISSAO)
        Toast.makeText(this, "Permita o acesso ao armazenamento e toque em baixar de novo.", Toast.LENGTH_LONG).show()
        return false
    }

    /** Salva em Downloads/FISCALE e oferece abrir. */
    private fun gravarNosDownloads(nome: String, mime: String, escrever: (OutputStream) -> Unit) {
        try {
            val tipo = mime.substringBefore(';').trim()
            val abrir: Uri?
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val valores = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, nome)
                    put(MediaStore.Downloads.MIME_TYPE, tipo)
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/FISCALE")
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val r = contentResolver
                val uri = r.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, valores)
                    ?: throw IllegalStateException("não foi possível criar o arquivo")
                r.openOutputStream(uri)!!.use(escrever)
                valores.clear()
                valores.put(MediaStore.Downloads.IS_PENDING, 0)
                r.update(uri, valores, null, null)
                abrir = uri
            } else {
                @Suppress("DEPRECATION")
                val pasta = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "FISCALE")
                pasta.mkdirs()
                FileOutputStream(File(pasta, nome)).use(escrever)
                abrir = null
            }
            if (abrir == null) {
                Toast.makeText(this, "Salvo em Downloads/FISCALE/$nome", Toast.LENGTH_LONG).show()
                return
            }
            AlertDialog.Builder(this)
                .setTitle("Arquivo salvo")
                .setMessage("Downloads/FISCALE/$nome")
                .setNegativeButton("Fechar", null)
                .setPositiveButton("Abrir") { _, _ ->
                    try {
                        startActivity(
                            Intent(Intent.ACTION_VIEW).setDataAndType(abrir, tipo)
                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        )
                    } catch (e: ActivityNotFoundException) {
                        Toast.makeText(this, "Nenhum app abre este tipo de arquivo.", Toast.LENGTH_SHORT).show()
                    }
                }
                .show()
        } catch (e: Exception) {
            Toast.makeText(this, "Não foi possível salvar: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }
}
