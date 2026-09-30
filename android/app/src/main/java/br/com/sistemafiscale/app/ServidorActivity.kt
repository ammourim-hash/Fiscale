package br.com.sistemafiscale.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import kotlin.concurrent.thread

/** Onde a pessoa informa (ou troca) o endereço do Sistema Web FISCALE. */
class ServidorActivity : Activity() {

    private lateinit var campo: EditText
    private lateinit var resultado: TextView
    private lateinit var testar: Button
    private lateinit var salvar: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_servidor)
        campo = findViewById(R.id.endereco)
        resultado = findViewById(R.id.resultado)
        testar = findViewById(R.id.testar)
        salvar = findViewById(R.id.salvar)

        Servidor.ler(this)?.let { campo.setText(it) }

        testar.setOnClickListener { executarTeste() }
        salvar.setOnClickListener { confirmarESalvar() }
        campo.setOnEditorActionListener { _, acao, _ ->
            if (acao == EditorInfo.IME_ACTION_GO) { confirmarESalvar(); true } else false
        }
    }

    private fun lerCampo(): String? {
        val url = Servidor.normalizar(campo.text.toString())
        if (url == null) mostrar(false, "Digite um endereço válido, por exemplo https://app.sistemafiscale.com.br")
        return url
    }

    private fun mostrar(ok: Boolean, texto: String) {
        resultado.visibility = View.VISIBLE
        resultado.setTextColor(getColor(if (ok) R.color.ok else R.color.erro))
        resultado.text = texto
    }

    private fun executarTeste(depois: ((Boolean) -> Unit)? = null) {
        val url = lerCampo() ?: return
        campo.setText(url)
        testar.isEnabled = false
        salvar.isEnabled = false
        resultado.visibility = View.VISIBLE
        resultado.setTextColor(getColor(R.color.texto_suave))
        resultado.text = getString(R.string.testando)
        thread {
            val r = Servidor.testar(url)
            runOnUiThread {
                testar.isEnabled = true
                salvar.isEnabled = true
                when (r) {
                    is Servidor.Teste.Ok -> mostrar(true, "✓ " + r.detalhe)
                    is Servidor.Teste.Falha -> mostrar(false, "✗ " + r.motivo)
                }
                depois?.invoke(r is Servidor.Teste.Ok)
            }
        }
    }

    private fun confirmarESalvar() {
        val url = lerCampo() ?: return
        if (Servidor.precisaAviso(url)) {
            // D72/D80: o FISCALE não é publicado na internet e senha não trafega
            // aberta fora da rede privada. Avisamos; a rede é quem decide.
            AlertDialog.Builder(this)
                .setTitle("Conexão sem HTTPS")
                .setMessage(
                    "\"$url\" usa HTTP e não parece estar numa rede privada.\n\n" +
                        "Pelas decisões D72/D80 o FISCALE só deve ser acessado pela rede privada " +
                        "do escritório, com HTTPS (app.sistemafiscale.com.br). Sua senha trafegaria sem criptografia.\n\n" +
                        "Usar mesmo assim?"
                )
                .setNegativeButton("Corrigir", null)
                .setPositiveButton("Usar mesmo assim") { _, _ -> testarEAbrir(url) }
                .show()
        } else {
            testarEAbrir(url)
        }
    }

    private fun testarEAbrir(url: String) {
        campo.setText(url)
        executarTeste { ok ->
            if (ok) {
                abrir(url)
            } else {
                AlertDialog.Builder(this)
                    .setTitle("Servidor não respondeu")
                    .setMessage("Salvar este endereço mesmo assim? Você poderá tentar de novo quando estiver na rede do escritório.")
                    .setNegativeButton("Cancelar", null)
                    .setPositiveButton("Salvar") { _, _ -> abrir(url) }
                    .show()
            }
        }
    }

    private fun abrir(url: String) {
        val trocou = Servidor.ler(this) != url
        Servidor.gravar(this, url)
        startActivity(
            Intent(this, MainActivity::class.java)
                .putExtra(MainActivity.EXTRA_RECARREGAR, trocou)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
        )
        finish()
    }
}
