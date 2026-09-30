"use client";

/**
 * O botão da tela de sem-conexão, e o aviso de volta.
 *
 * Fica em componente próprio porque a página offline é estática de
 * propósito: ela precisa ser guardada pelo service worker e servida sem
 * servidor, então nada nela pode depender de dado.
 */
import { useEffect, useState } from "react";

export function BotaoTentarDeNovo() {
  const [online, setOnline] = useState(true);

  useEffect(() => {
    const atualizar = () => setOnline(navigator.onLine);
    atualizar();
    window.addEventListener("online", atualizar);
    window.addEventListener("offline", atualizar);
    return () => {
      window.removeEventListener("online", atualizar);
      window.removeEventListener("offline", atualizar);
    };
  }, []);

  return (
    <>
      {online ? (
        // `navigator.onLine` só sabe que existe uma rede — não que o
        // servidor responde. Por isso o texto diz "parece", e o botão
        // continua sendo um botão em vez de recarregar sozinho.
        <p className="entrada-nota" role="status">
          A conexão parece ter voltado.
        </p>
      ) : null}

      <button type="button" className="btn btn-acao" onClick={() => window.location.reload()}>
        Tentar de novo
      </button>
    </>
  );
}
