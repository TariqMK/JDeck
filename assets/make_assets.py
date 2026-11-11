"""Builds JDeck's brand images from the icon master, assets/icon/jdeck.svg.

Writes:
  assets/icon/jdeck.png   1024 x 1024 px (web page icon, Linux window, docs)
  assets/icon/jdeck.ico   16 to 256 px (Windows exe)
  assets/icon/jdeck.icns  16 to 1024 px (macOS app)
  assets/banner.png       1280 x 320 px at 2x (top of the README)
  assets/social-card.png  1280 x 640 px (GitHub social preview)
  docs/icon.svg, docs/apple-touch-icon.png (180 px), docs/social-card.png  copies for the website

Run it from the project folder after any change to the master:
  python -m pip install PySide6 Pillow
  python assets/make_assets.py

PySide6 is only needed here, not by the app.
"""

import io
import shutil
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QImage,
                           QPainter, QPainterPath, QPen)
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent
ICON_DIR = ASSETS / "icon"
MASTER = ICON_DIR / "jdeck.svg"

ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]

# Brand text and colours. Colours come from the dark theme in web/index.html.
NAME = "JDeck"
TAGLINE = "JDownloader from any device on your network."
SUBLINE = "Windows · macOS · Linux"
BG = "#161715"         # --bg (dark)
BORDER = "#333531"     # --line (dark)
TEXT = "#ecebe6"       # --text (dark)
SECONDARY = "#9c9b94"  # --muted (dark)
SUBTLE = "#77766f"     # Between --muted and --line, for the subline only.
SANS = ["Segoe UI", "Inter", "SF Pro Display", "Helvetica Neue", "Arial"]
MONO = ["Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono"]


def render_svg(svg: Path, size: int) -> Image.Image:
    """Render the SVG to a square, transparent Pillow image of the given size."""
    renderer = QSvgRenderer(QByteArray(svg.read_bytes()))
    if not renderer.isValid():
        sys.exit(f"Cannot read {svg}")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return qimage_to_pil(image)


def qimage_to_pil(image: QImage) -> Image.Image:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")


def make_icons() -> None:
    renders = {size: render_svg(MASTER, size) for size in sorted(set(ICO_SIZES + ICNS_SIZES))}

    png = ICON_DIR / "jdeck.png"
    renders[1024].save(png, optimize=True)

    ico = ICON_DIR / "jdeck.ico"
    big = renders[256]
    big.save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
             append_images=[renders[s] for s in ICO_SIZES if s != 256])

    icns = ICON_DIR / "jdeck.icns"
    renders[1024].save(icns, format="ICNS",
                       append_images=[renders[s] for s in ICNS_SIZES if s != 1024])

    for path in (png, ico, icns):
        print(f"Wrote {path.relative_to(ASSETS.parent)} ({path.stat().st_size // 1024} KB)")


def font(families: list[str], pixel_size: float, weight: QFont.Weight) -> QFont:
    available = set(QFontDatabase.families())
    result = QFont()
    result.setFamilies([f for f in families if f in available] or families)
    result.setPixelSize(round(pixel_size))
    result.setWeight(weight)
    return result


def fit(base: QFont, text: str, max_width: float) -> QFont:
    """Shrink the font 2 px at a time until the text fits max_width."""
    result = QFont(base)
    while QFontMetrics(result).horizontalAdvance(text) > max_width - 8 and result.pixelSize() > 12:
        result.setPixelSize(result.pixelSize() - 2)
    if result.pixelSize() < base.pixelSize():
        print(f'Warning: "{text}" shrank from {base.pixelSize()} to {result.pixelSize()} px to fit.')
    return result


def draw_svg(painter: QPainter, svg: Path, rect: QRectF) -> None:
    QSvgRenderer(QByteArray(svg.read_bytes())).render(painter, rect)


def make_banner() -> None:
    """1280 x 320 px at 2x: a rounded dark panel, icon left, name and tagline right, centred as a group."""
    scale = 2
    width, height = 1280 * scale, 320 * scale
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    panel = QPainterPath()
    half = scale / 2
    panel.addRoundedRect(QRectF(half, half, width - scale, height - scale), 24 * scale, 24 * scale)
    painter.fillPath(panel, QColor(BG))
    painter.setPen(QPen(QColor(BORDER), scale))
    painter.drawPath(panel)

    icon_size, gap = 176 * scale, 48 * scale
    name_font = font(SANS, 96 * scale, QFont.Weight.DemiBold)
    tag_font = font(MONO, 24 * scale, QFont.Weight.Normal)
    name_m, tag_m = QFontMetrics(name_font), QFontMetrics(tag_font)
    text_width = max(name_m.horizontalAdvance(NAME), tag_m.horizontalAdvance(TAGLINE))
    left = (width - (icon_size + gap + text_width)) / 2
    draw_svg(painter, MASTER, QRectF(left, (height - icon_size) / 2, icon_size, icon_size))

    # Name (cap height) and tagline (x-height) form one text block, centred on the icon.
    line_gap = 28 * scale
    block = name_m.capHeight() + line_gap + tag_m.xHeight()
    top = (height - block) / 2
    text_left = left + icon_size + gap
    painter.setFont(name_font)
    painter.setPen(QColor(TEXT))
    painter.drawText(QPointF(text_left - name_font.pixelSize() * 0.035, top + name_m.capHeight()), NAME)
    painter.setFont(tag_font)
    painter.setPen(QColor(SECONDARY))
    painter.drawText(QPointF(text_left, top + block), TAGLINE)
    painter.end()

    out = ASSETS / "banner.png"
    image.save(str(out))
    print(f"Wrote {out.relative_to(ASSETS.parent)} ({width} x {height} px)")


# Social card layout: GitHub's repo card template. Same values as the shared make_social_card.py.
CARD_W, CARD_H, SAFE = 1280, 640, 80
CARD_ICON, CARD_ICON_TOP, CARD_ICON_GAP = 168, 150, 40
CARD_NAME_PX, CARD_NAME_SPACING = 112, 98
CARD_TAGLINE_PX, CARD_TAGLINE_BASELINE = 34, 420
CARD_SUBLINE_PX, CARD_SUBLINE_BASELINE = 24, 476


def make_social_card() -> None:
    image = QImage(CARD_W, CARD_H, QImage.Format.Format_ARGB32)
    image.fill(QColor(BG))  # Full bleed: GitHub crops and rounds the card itself.
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    safe_width = CARD_W - 2 * SAFE

    name_font = font(SANS, CARD_NAME_PX, QFont.Weight.DemiBold)
    name_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, CARD_NAME_SPACING)
    icon_part = CARD_ICON + CARD_ICON_GAP
    name_font = fit(name_font, NAME, safe_width - icon_part)
    name_m = QFontMetrics(name_font)
    left = (CARD_W - (icon_part + name_m.horizontalAdvance(NAME))) / 2
    draw_svg(painter, MASTER, QRectF(left, CARD_ICON_TOP, CARD_ICON, CARD_ICON))
    baseline = CARD_ICON_TOP + CARD_ICON / 2 + name_m.capHeight() / 2
    painter.setFont(name_font)
    painter.setPen(QColor(TEXT))
    painter.drawText(QPointF(left + icon_part - name_font.pixelSize() * 0.035, baseline), NAME)

    for text, px, color, y in ((TAGLINE, CARD_TAGLINE_PX, SECONDARY, CARD_TAGLINE_BASELINE),
                               (SUBLINE, CARD_SUBLINE_PX, SUBTLE, CARD_SUBLINE_BASELINE)):
        text_font = fit(font(MONO, px, QFont.Weight.Normal), text, safe_width)
        painter.setFont(text_font)
        painter.setPen(QColor(color))
        painter.drawText(QPointF((CARD_W - QFontMetrics(text_font).horizontalAdvance(text)) / 2, y), text)
    painter.end()

    out = ASSETS / "social-card.png"
    image.save(str(out))
    print(f"Wrote {out.relative_to(ASSETS.parent)} ({CARD_W} x {CARD_H} px)")
    check_safe_area(image)


def check_safe_area(image: QImage) -> None:
    """Exit with an error if anything on the card is closer than SAFE px to an edge."""
    background = QColor(BG)
    xs, ys = [], []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y) != background:
                xs.append(x)
                ys.append(y)
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    if x0 < SAFE or y0 < SAFE or x1 > CARD_W - SAFE or y1 > CARD_H - SAFE:
        sys.exit(f"Social card content x {x0}-{x1}, y {y0}-{y1} is outside the {SAFE} px safe area.")
    print(f"Social card safe area check passed (content x {x0}-{x1}, y {y0}-{y1}).")


def main() -> None:
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - Qt needs an application object.
    make_icons()
    make_banner()
    make_social_card()
    copy_to_site()


def copy_to_site() -> None:
    """GitHub Pages serves only docs/, so the website keeps its own copies."""
    docs = ASSETS.parent / "docs"
    docs.mkdir(exist_ok=True)
    shutil.copyfile(MASTER, docs / "icon.svg")
    shutil.copyfile(ASSETS / "social-card.png", docs / "social-card.png")
    render_svg(MASTER, 180).save(docs / "apple-touch-icon.png", optimize=True)
    (docs / ".nojekyll").touch()
    print("Wrote docs/icon.svg, docs/apple-touch-icon.png, docs/social-card.png")


if __name__ == "__main__":
    main()
