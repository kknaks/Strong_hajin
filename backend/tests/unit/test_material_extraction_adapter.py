"""The document extraction adapter reports stable outcomes for supported and refused inputs."""

from io import BytesIO

from pypdf import PdfReader, PdfWriter

from ax_workspace.platform.material_extraction import PypdfTextExtractor


def _text_pdf(*lines: str, encrypt: str | None = None) -> bytes:
    """Build a minimal synthetic PDF without reading a real document."""
    content = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({line}) Tj 0 -16 Td" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(
        f"{offset:010d} 00000 n \n" for offset in offsets
    )
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    data = out.encode("latin-1")
    if encrypt is None:
        return data
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    writer.encrypt(encrypt)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_text_and_markdown_extract_utf8_only() -> None:
    extractor = PypdfTextExtractor()
    ok = extractor.extract(name="brief.md", content_type="text/markdown", data="# 견적\n납기일 9월 30일".encode())
    assert ok.status == "completed" and ok.extractor == "markdown" and ok.chunks[0].text.startswith("# 견적")
    latin = extractor.extract(name="legacy.txt", content_type="text/plain", data="caf\xe9".encode("cp1252") + b"\xff\xfe")
    assert (latin.status, latin.failure_reason) == ("failed", "not_utf8_text")
    empty = extractor.extract(name="empty.txt", content_type="text/plain", data=b"   \n")
    assert (empty.status, empty.failure_reason) == ("failed", "empty_content")
    image = extractor.extract(name="scan.png", content_type="image/png", data=b"\x89PNG")
    assert (image.status, image.failure_reason) == ("unsupported", "unsupported_format")


def test_pdf_extraction_reports_pages_encryption_corruption_and_empty_text() -> None:
    extractor = PypdfTextExtractor()
    good = extractor.extract(
        name="quote.pdf",
        content_type="application/pdf",
        data=_text_pdf("Quote total 1,200,000 KRW", "Delivery 2026-09-30"),
    )
    assert good.status == "completed" and good.page_count == 1 and good.chunks[0].page == 1
    assert "Delivery 2026-09-30" in good.chunks[0].text
    locked = extractor.extract(name="locked.pdf", content_type="application/pdf", data=_text_pdf("secret", encrypt="pw"))
    assert (locked.status, locked.failure_reason) == ("failed", "encrypted_document")
    corrupt = extractor.extract(name="broken.pdf", content_type="application/pdf", data=b"%PDF-1.4 garbage without xref")
    assert (corrupt.status, corrupt.failure_reason) == ("failed", "corrupt_document")
    blank = extractor.extract(name="blank.pdf", content_type="application/pdf", data=_text_pdf())
    assert (blank.status, blank.failure_reason) == ("failed", "empty_content")
