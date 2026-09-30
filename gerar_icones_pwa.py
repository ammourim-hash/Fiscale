#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gera os ícones PNG do aplicativo (PWA) a partir do símbolo do FISCALE.

    python gerar_icones_pwa.py

POR QUE EXISTE
    O `manifest.webmanifest` apontava para `icone.webp` declarado como 192 e
    512 ao mesmo tempo, num arquivo de 256×256. O Chrome e o Edge conferem o
    tamanho real, e é esse tipo de detalhe que faz o "Instalar" simplesmente
    não aparecer, sem mensagem nenhuma.

    A geometria é a MESMA de `web/fiscale-logo.svg` (viewBox 64×64): quem
    mudar o símbolo muda lá e roda isto de novo. Nada é baixado de fora.

O QUE SAI, em web/icones/
    fiscale-192.png, fiscale-512.png   "any": cantos arredondados, fundo transparente
    fiscale-maskable-512.png           "maskable": fundo cheio, símbolo dentro da
                                       zona segura (80%), para o Windows e o
                                       Android recortarem do jeito deles
    fiscale-180.png                    ícone de toque (apple-touch-icon)
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parent
DESTINO = RAIZ / "web" / "icones"

TOPO, BASE = (0x17, 0x61, 0x6C), (0x0E, 0x3D, 0x45)     # degradê petróleo
BRANCO, CIANO, TINTA = (255, 255, 255), (0x7F, 0xD1, 0xDE), (0x0C, 0x33, 0x3B)
AMOSTRA = 4                                              # superamostragem


def _degrade(lado: int) -> Image.Image:
    img = Image.new("RGBA", (lado, lado))
    px = ImageDraw.Draw(img)
    for y in range(lado):
        t = y / max(1, lado - 1)
        cor = tuple(round(TOPO[i] + (BASE[i] - TOPO[i]) * t) for i in range(3)) + (255,)
        px.line([(0, y), (lado, y)], fill=cor)
    return img


def _simbolo(d: ImageDraw.ImageDraw, escala: float, dx: float, dy: float):
    """O F, a barra ciano e o selo — coordenadas do viewBox 64×64."""
    def r(x, y, w, h, raio, cor):
        d.rounded_rectangle([dx + x * escala, dy + y * escala,
                             dx + (x + w) * escala, dy + (y + h) * escala],
                            radius=raio * escala, fill=cor)
    r(17, 15, 6.5, 34, 3.25, BRANCO)
    r(17, 15, 27, 6.5, 3.25, BRANCO)
    r(17, 28, 19, 6.5, 3.25, CIANO)
    cx, cy, raio = 45.5, 44.5, 10
    d.ellipse([dx + (cx - raio) * escala, dy + (cy - raio) * escala,
               dx + (cx + raio) * escala, dy + (cy + raio) * escala], fill=CIANO)
    visto = [(40.8, 44.6), (44.2, 48.0), (50.6, 40.6)]
    d.line([(dx + x * escala, dy + y * escala) for x, y in visto], fill=TINTA,
           width=max(1, round(3 * escala)), joint="curve")


def icone(lado: int, mascaravel: bool = False) -> Image.Image:
    grande = lado * AMOSTRA
    fundo = _degrade(grande)
    if mascaravel:
        img = fundo                                   # sangra até a borda
        area = grande * 0.80                          # zona segura
        escala, dx = area / 64, (grande - area) / 2
    else:
        img = Image.new("RGBA", (grande, grande), (0, 0, 0, 0))
        mascara = Image.new("L", (grande, grande), 0)
        ImageDraw.Draw(mascara).rounded_rectangle(
            [0, 0, grande - 1, grande - 1], radius=grande * 15 / 64, fill=255)
        img.paste(fundo, (0, 0), mascara)
        escala, dx = grande / 64, 0
    _simbolo(ImageDraw.Draw(img), escala, dx, dx)
    return img.resize((lado, lado), Image.LANCZOS)


def gerar(destino: Path = DESTINO) -> list[Path]:
    destino.mkdir(parents=True, exist_ok=True)
    saidas = []
    for nome, lado, masc in (("fiscale-192.png", 192, False),
                             ("fiscale-512.png", 512, False),
                             ("fiscale-maskable-512.png", 512, True),
                             ("fiscale-180.png", 180, True)):
        caminho = destino / nome
        icone(lado, masc).save(caminho, "PNG", optimize=True)
        saidas.append(caminho)
    return saidas


if __name__ == "__main__":
    for p in gerar():
        print("gerado", p.relative_to(RAIZ))
    sys.exit(0)
