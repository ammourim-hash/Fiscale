"""
cnpj_publico.py — consulta os DADOS PÚBLICOS de um CNPJ (só biblioteca padrão).

Para que serve:
  1. Consulta Optantes: responder "é optante do Simples?" sem CAPTCHA.
  2. Descobrir o MUNICÍPIO (código IBGE) da empresa — é ele que decide em qual
     prefeitura buscar as NFS-e municipais, sem precisar cadastrar cidade a cidade.

Fonte:
  São os Dados Abertos do CNPJ, publicados pela própria Receita Federal e
  espelhados por serviços públicos gratuitos (BrasilAPI e Minha Receita).
  Tentamos um e caímos no outro se falhar — os dois devolvem os mesmos campos.

IMPORTANTE (limite conhecido):
  Os dados abertos são atualizados em lote (mensalmente). Uma opção pelo Simples
  feita há poucos dias pode ainda não constar. Por isso a tela mantém o link da
  Consulta Optantes oficial para conferência em caso de dúvida — o valor aqui é
  responder de imediato para as dezenas de empresas do escritório.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request

FONTES = (
    ("BrasilAPI", "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"),
    ("Minha Receita", "https://minhareceita.org/{cnpj}"),
)

# A MESMA lista, invertida, para o RELATÓRIO de Optantes.
#
# Medido em 12/09/2026: nenhuma das duas fontes informa a data de referência
# DENTRO do registro do CNPJ. Só a Minha Receita a publica, e num endpoint
# separado (`/updated`). Então o relatório precisa que a Minha Receita seja a
# primária — senão a linha vem de uma fonte e a data de outra, que é
# emprestar procedência e o tipo de erro que ninguém percebe.
FONTES_COM_REFERENCIA = tuple(reversed(FONTES))

URL_REFERENCIA = "https://minhareceita.org/updated"

_CACHE: dict[str, tuple[float, dict]] = {}
_VALIDADE = 24 * 3600  # 1 dia: o dado de origem muda no máximo uma vez por mês


def so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def cnpj_valido(cnpj: str) -> bool:
    """Valida os 2 dígitos verificadores (evita gastar rede com digitação errada)."""
    c = so_digitos(cnpj)
    if len(c) != 14 or c == c[0] * 14:
        return False
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(d) * p for d, p in zip(c[:tam], pesos))
        resto = soma % 11
        if int(c[tam]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def _buscar(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Fiscale/1.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


def referencia_da_base() -> dict:
    """A data de referência dos Dados Abertos, publicada pela Minha Receita.

    Devolve `{"ok": True, "referencia": "AAAA-MM"}` ou `{"ok": False, "erro": ...}`.
    Nunca levanta e nunca inventa: se a fonte não responder, quem chama tem de
    dizer "não informada pela fonte" — jamais a data de hoje disfarçada de
    referência, que é o erro que este campo existe para evitar.

    A base é atualizada em lote, mensalmente. Em 12/09/2026 ela respondia
    `2026-08` — um mês atrás. É exatamente o risco que o carimbo cobre: uma
    exclusão do Simples feita em agosto ainda aparece como optante hoje.
    """
    try:
        d = _buscar(URL_REFERENCIA)
    except Exception as e:
        return {"ok": False, "referencia": "", "erro": f"{type(e).__name__}: {e}"}
    ref = str((d or {}).get("message") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", ref):
        return {"ok": False, "referencia": "",
                "erro": f"a fonte respondeu algo que não é data: {ref[:40]!r}"}
    return {"ok": True, "referencia": ref}


def consultar(cnpj: str, usar_cache=True, fontes=None) -> dict:
    """Devolve os dados públicos do CNPJ. Nunca levanta exceção: em caso de
    falha devolve {'ok': False, 'erro': ...} para a tela mostrar o motivo.

    `fontes` troca a ordem de tentativa (ver `FONTES_COM_REFERENCIA`). Quando
    ela é informada, o cache só vale se a resposta guardada tiver vindo da
    fonte PREFERIDA — servir um registro da retaguarda para quem pediu a
    primária carimbaria a data da Minha Receita num dado da BrasilAPI.
    """
    fontes = tuple(fontes or FONTES)
    c = so_digitos(cnpj)
    if len(c) == 11:
        return {"ok": False, "cnpj": c, "erro": "Isso é um CPF. A Consulta Optantes "
                "do Simples Nacional é só para CNPJ.", "tipo": "cpf"}
    if not cnpj_valido(c):
        return {"ok": False, "cnpj": c, "erro": "CNPJ inválido (dígito verificador não confere)."}

    if usar_cache:
        achado = _CACHE.get(c)
        if achado and (time.time() - achado[0]) < _VALIDADE:
            if achado[1].get("fonte") == fontes[0][0]:
                return {**achado[1], "do_cache": True}

    erros = []
    for nome, molde in fontes:
        try:
            d = _buscar(molde.format(cnpj=c))
        except urllib.error.HTTPError as e:
            erros.append(f"{nome}: HTTP {e.code}" + (" (CNPJ não encontrado)" if e.code == 404 else ""))
            continue
        except Exception as e:
            erros.append(f"{nome}: {e}")
            continue

        exclusao = d.get("data_exclusao_do_simples")
        optante = bool(d.get("opcao_pelo_simples")) and not exclusao
        out = {
            "ok": True,
            "cnpj": c,
            "fonte": nome,
            "razao_social": d.get("razao_social") or "",
            "nome_fantasia": d.get("nome_fantasia") or "",
            "situacao": d.get("descricao_situacao_cadastral") or "",
            "optante_simples": optante,
            "data_opcao_simples": d.get("data_opcao_pelo_simples"),
            # Início de atividade da EMPRESA ('AAAA-MM-DD'). É o dado que o
            # Simples Nacional usa para proporcionalizar o RBT12 nos 12
            # primeiros meses. NÃO existe dentro do certificado digital: o
            # e-CNPJ só carrega a data de NASCIMENTO do responsável (OID
            # 2.16.76.1.3.4) — na MONTE, 01/01/1990 contra a abertura real em
            # 19/10/2000. Aqui vem da base pública da própria Receita.
            "inicio_atividade": d.get("data_inicio_atividade") or "",
            "data_exclusao_simples": exclusao,
            "mei": bool(d.get("opcao_pelo_mei")) and not d.get("data_exclusao_do_mei"),
            "municipio": d.get("municipio") or "",
            "uf": d.get("uf") or "",
            "cod_municipio": str(d.get("codigo_municipio_ibge") or ""),
            "cnae": str(d.get("cnae_fiscal") or ""),
            "cnae_desc": d.get("cnae_fiscal_descricao") or "",
            "porte": d.get("porte") or d.get("descricao_porte") or "",
        }
        _CACHE[c] = (time.time(), out)
        return out

    return {"ok": False, "cnpj": c,
            "erro": "Não foi possível consultar agora. " + " | ".join(erros)}


def municipio_de(cnpj: str) -> str:
    """Código IBGE do município da empresa ('' se não descobrir)."""
    d = consultar(cnpj)
    return d.get("cod_municipio", "") if d.get("ok") else ""
