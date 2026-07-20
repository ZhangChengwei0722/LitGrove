from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen.canvas import Canvas


def write_synthetic_pdf(
    path: Path,
    pages: list[str],
    *,
    password: str | None = None,
) -> Path:
    encryption = (
        StandardEncryption(password, canPrint=0, strength=128)
        if password is not None
        else None
    )
    document = Canvas(
        str(path),
        pagesize=letter,
        pageCompression=0,
        invariant=1,
        encrypt=encryption,
    )
    for page_text in pages:
        text = document.beginText(72, 720)
        for line in page_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if line:
                text.textLine(line)
        document.drawText(text)
        document.showPage()
    document.save()
    return path
