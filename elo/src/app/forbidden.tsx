/**
 * A tela do 403.
 *
 * Existe porque `forbidden()` sem este arquivo mostra a página crua do
 * Next — e quem esbarra nela é gente do escritório, não um invasor. O
 * texto diz o que aconteceu e o que fazer, sem contar o que tem do outro
 * lado: "administrar a equipe" já é o suficiente para a pessoa saber a
 * quem pedir.
 */
import Link from "next/link";

export default function Proibido() {
  return (
    <div className="pagina pagina-estreita">
      <div className="estado">
        <p className="estado-titulo">Esta área é da administração.</p>
        <p className="estado-desc">
          Seu acesso não inclui administrar a equipe. Se precisar de uma alteração de cadastro,
          fale com quem responde pelo escritório.
        </p>
        <Link className="btn btn-acao" href="/">
          Voltar ao início
        </Link>
      </div>
    </div>
  );
}
