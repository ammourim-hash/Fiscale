"""
inscricoes.py — Varredura de Inscrição Estadual (IE) e Inscrição Municipal (IM)
a partir dos DOCUMENTOS da própria empresa (não da base pública, que não traz
esses dados).

  • IE  → dos XMLs de NF-e (pastas 'nfe' e 'nfe_importadas'): a IE da empresa
          aparece no bloco <emit> das notas que ela emitiu e no <dest> das que
          recebeu. Namespace da NF-e.
  • IM  → dos XMLs de NFS-e (pasta 'xmls'): as notas ali são as EMITIDAS pela
          empresa (ela é a prestadora), então a Inscrição Municipal do prestador
          é a da empresa. Tolerante a layout (nacional <IM> e ABRASF
          <InscricaoMunicipal>).

Retorna as inscrições encontradas ordenadas por frequência (a mais comum primeiro).
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

NFE = "{http://www.portalfiscal.inf.br/nfe}"


def _dig(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _ln(tag: str) -> str:
    """local-name de uma tag com namespace ({ns}nome -> nome)."""
    return tag.rsplit("}", 1)[-1]


def _varrer_ie(pasta: Path, cnpj: str, limite: int = 500) -> list[str]:
    cont: Counter = Counter()
    arquivos: list[Path] = []
    for sub in ("nfe_importadas", "nfe"):
        d = pasta / sub
        if d.exists():
            arquivos += list(d.glob("*.xml"))
    for f in arquivos[:limite]:
        try:
            root = ET.fromstring(f.read_text("utf-8", "ignore"))
        except Exception:
            continue
        for tag in ("emit", "dest"):
            for bloco in root.iter(f"{NFE}{tag}"):
                if _dig(bloco.findtext(f"{NFE}CNPJ")) == cnpj:
                    ie = (bloco.findtext(f"{NFE}IE") or "").strip()
                    if ie and ie.upper() != "ISENTO":
                        cont[ie] += 1
    return [ie for ie, _ in cont.most_common(5)]


_TAGS_CNPJ = ("CNPJ", "Cnpj", "Cpf", "CpfCnpj")
_TAGS_IM = ("IM", "InscricaoMunicipal")
# blocos que identificam SÓ o prestador (não envolvem o tomador)
_TAGS_PRESTADOR = ("prest", "IdentificacaoPrestador", "PrestadorServico",
                   "DadosPrestador", "Prestador")


def _im_de_bloco(bloco, cnpj, direto=False):
    """IM do bloco se o CNPJ dele for o da empresa. Com direto=True usa só os
    filhos imediatos (para <emit>, que aninha o tomador dentro dele)."""
    fonte = list(bloco) if direto else list(bloco.iter())
    cnpj_ok = any(_ln(x.tag) in _TAGS_CNPJ and _dig(x.text) == cnpj for x in fonte)
    if not cnpj_ok:
        return None
    for x in fonte:
        if _ln(x.tag) in _TAGS_IM and (x.text or "").strip():
            return x.text.strip()
    return None


def _varrer_im(pasta: Path, cnpj: str, limite: int = 500) -> list[str]:
    cont: Counter = Counter()
    d = pasta / "xmls"
    if not d.exists():
        return []
    for f in list(d.glob("nfse-*.xml"))[:limite]:
        try:
            root = ET.fromstring(f.read_text("utf-8", "ignore"))
        except Exception:
            continue
        for el in root.iter():
            ln = _ln(el.tag)
            im = None
            if ln in _TAGS_PRESTADOR:          # bloco só-prestador: busca em profundidade
                im = _im_de_bloco(el, cnpj, direto=False)
            elif ln == "emit":                 # layout nacional: emit aninha o tomador → só filhos diretos
                im = _im_de_bloco(el, cnpj, direto=True)
            if im:
                cont[im] += 1
    return [im for im, _ in cont.most_common(5)]


def varrer(pasta_cnpj, cnpj: str) -> dict:
    c = _dig(cnpj)
    pasta = Path(pasta_cnpj)
    ie = _varrer_ie(pasta, c)
    im = _varrer_im(pasta, c)
    return {
        "ok": True, "cnpj": c,
        "ie": ie, "im": im,
        "ie_sugerida": ie[0] if ie else "",
        "im_sugerida": im[0] if im else "",
    }
