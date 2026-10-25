"""Render a .docx to .pdf using LibreOffice in headless mode.

Install LibreOffice: https://www.libreoffice.org/download/download/
Windows default install path: C:\\Program Files\\LibreOffice\\program\\soffice.exe
If it is not on your PATH, set SOFFICE_PATH in .env.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from config import CONFIG

_WINDOWS_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def _find_soffice() -> str:
    if CONFIG.soffice_path and Path(CONFIG.soffice_path).exists():
        return CONFIG.soffice_path
    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _WINDOWS_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "LibreOffice not found. Install it from libreoffice.org, or set "
        "SOFFICE_PATH in .env to the full path of soffice.exe."
    )


def docx_to_pdf(docx_path, out_dir=None) -> str:
    docx_path = Path(docx_path)
    out_dir = Path(out_dir) if out_dir else docx_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    soffice = _find_soffice()
    result = subprocess.run(
        [
            soffice,
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(docx_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    pdf_path = out_dir / (docx_path.stem + ".pdf")
    if result.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(
            f"LibreOffice conversion failed (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return str(pdf_path)


def pdf_page_count(pdf_path) -> int:
    """Number of pages in a PDF (pypdf if installed, else a robust byte-level count)."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except ImportError:
        import re

        data = Path(pdf_path).read_bytes()
        counts = [int(n) for n in re.findall(rb"/Count\s+(\d+)", data)]
        if counts:
            return max(counts)
        return len(re.findall(rb"/Type\s*/Page[^s]", data))
