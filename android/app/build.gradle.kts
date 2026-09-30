plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "br.com.sistemafiscale.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "br.com.sistemafiscale.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
    }

    // Assinatura de release opcional: só é usada quando as variáveis do
    // keystore existem (CI com secrets). Sem elas, o CI gera só o APK debug,
    // que já instala em qualquer Android.
    val ksPath = System.getenv("FISCALE_KEYSTORE")
    signingConfigs {
        if (ksPath != null && file(ksPath).exists()) {
            create("release") {
                storeFile = file(ksPath)
                storePassword = System.getenv("FISCALE_KEYSTORE_SENHA")
                keyAlias = System.getenv("FISCALE_KEY_ALIAS")
                keyPassword = System.getenv("FISCALE_KEY_SENHA")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
    }
}

// Sem dependências além da biblioteca padrão do Kotlin: o app é o WebView do
// sistema apontado para o Sistema Web FISCALE.
dependencies {
}
