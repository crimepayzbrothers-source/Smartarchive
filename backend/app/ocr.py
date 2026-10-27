"""PDF-Erzeugung, Texterkennung und Seitenvorschau.

Reihenfolge der Texterkennung:
1. Digitales PDF mit Textebene -> Text direkt übernehmen (pdftotext).
2. Sonst OCRmyPDF (falls installiert) -> durchsuchbares PDF + Text.
3. Sonst Tesseract direkt über pytesseract -> durchsuchbares PDF + Text.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

log = logging.getLogger("smartarchive.ocr")

MIN_CHARS_PER_PAGE = 30


@lru_cache(maxsize=1)
def ocr_languages() -> str:
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return "eng"
    langs = {line.strip() for line in out.splitlines()[1:] if line.strip()}
    chosen = [lang for lang in ("deu", "eng") if lang in langs]
    return "+".join(chosen) if chosen else "eng"


def images_to_pdf(images: list[bytes]) -> bytes:
    """Fasst Fotos (z. B. Kamera-Aufnahmen) zu einem PDF zusammen.

    Die Ausrichtung aus den EXIF-Daten wird übernommen, Bilder werden auf eine
    sinnvolle Größe begrenzt und als JPEG eingebettet.
    """
    import img2pdf

    pages: list[bytes] = []
    for raw in images:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((3000, 3000))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=88, dpi=(300, 300))
            pages.append(buf.getvalue())
    return img2pdf.convert(pages)


def page_count(pdf: Path) -> int:
    from pypdf import PdfReader

    try:
        return len(PdfReader(str(pdf)).pages)
    except Exception:  # beschädigte PDFs: wenigstens nicht abstürzen
        return 0


def extract_text_pages(pdf: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"],
            capture_output=True, timeout=120,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return []
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return [p.strip("\n") for p in pages]


def has_text_layer(pages: list[str], n_pages: int) -> bool:
    if not pages:
        return False
    total = sum(len("".join(p.split())) for p in pages)
    return total >= MIN_CHARS_PER_PAGE * max(1, n_pages) * 0.6


def _ocrmypdf(src: Path, dst: Path, sidecar: Path) -> bool:
    if not shutil.which("ocrmypdf"):
        return False
    cmd = [
        "ocrmypdf", "--skip-text", "--rotate-pages", "--jobs", "1",
        "-l", ocr_languages(), "--output-type", "pdfa", "--optimize", "1",
        "--sidecar", str(sidecar), str(src), str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("OCRmyPDF nicht ausführbar: %s", exc)
        return False
    if proc.returncode not in (0, 6):  # 6 = enthielt bereits Text
        log.warning("OCRmyPDF Fehler %s: %s", proc.returncode, proc.stderr[-800:])
        return False
    return dst.exists()


def _tesseract_direct(src: Path, dst: Path) -> list[str]:
    import pytesseract
    from pypdf import PdfReader, PdfWriter

    texts: list[str] = []
    writer = PdfWriter()
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "300", "-png", str(src), f"{tmp}/p"], check=True, timeout=1800)
        for img_path in sorted(Path(tmp).glob("p*.png")):
            with Image.open(img_path) as im:
                lang = ocr_languages()
                texts.append(pytesseract.image_to_string(im, lang=lang))
                pdf_bytes = pytesseract.image_to_pdf_or_hocr(im, extension="pdf", lang=lang)
            for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
                writer.add_page(page)
    with open(dst, "wb") as fh:
        writer.write(fh)
    return texts


def ocr_pdf(src: Path, workdir: Path) -> tuple[Path | None, list[str]]:
    """Erzeugt ein durchsuchbares PDF. Liefert (Pfad oder None, Seitentexte)."""
    dst = workdir / "searchable.pdf"
    sidecar = workdir / "sidecar.txt"
    if _ocrmypdf(src, dst, sidecar):
        pages = extract_text_pages(dst)
        if not has_text_layer(pages, len(pages) or 1) and sidecar.exists():
            pages = sidecar.read_text("utf-8", "replace").split("\f")
        return dst, pages
    try:
        texts = _tesseract_direct(src, dst)
        return dst, texts
    except Exception as exc:  # noqa: BLE001
        log.error("Texterkennung fehlgeschlagen: %s", exc)
        return None, []


def render_page(pdf: Path, page: int, dpi: int, out: Path) -> Path:
    """Rendert eine Seite als PNG (für die Vorschau auf dem Handy)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    base = out.with_suffix("")
    subprocess.run(
        ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile", str(pdf), str(base)],
        check=True, capture_output=True, timeout=120,
    )
    return out
