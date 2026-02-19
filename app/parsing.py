from PyPDF2 import PdfReader
from pathlib import Path


def pdf_to_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    parts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        parts.append(t)
    return "\n".join(parts).strip()


def safe_delete(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        # Do not crash prototype on delete issues
        pass
