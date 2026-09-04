"""Office/PDF document parser adapter.

OOXML packages go through a ZIP preflight (member count, per-member size, total uncompressed size, compression
ratio, encryption flags, path traversal) before any library opens them. python-docx / openpyxl / python-pptx /
pypdf then read the structure; formulas stay as text, macros and embedded objects are ignored, external links are
never followed, and failures become explainable statuses rather than library exceptions.
"""
from __future__ import annotations

from io import BytesIO
import zipfile

from ax_workspace.modules.work.document_parsing import (
    FORMAT_DOCX,
    FORMAT_PDF,
    FORMAT_PPTX,
    FORMAT_XLSX,
    STATUS_BUDGET_EXCEEDED,
    STATUS_CORRUPT,
    STATUS_EMPTY,
    STATUS_ENCRYPTED,
    STATUS_NEEDS_OCR,
    STATUS_OK,
    STATUS_UNSUPPORTED,
    BlockCollector,
    ParseBudget,
    ParsedDocument,
    SourceLocator,
    classify_document,
)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE_STREAM = "EncryptedPackage".encode("utf-16-le")
_ZIP_MAGIC = b"PK\x03\x04"


class OoxmlPreflight:
    """Refuse an OOXML archive before decompressing anything beyond the central directory."""

    def __init__(self, budget: ParseBudget) -> None:
        self._budget = budget

    def check(self, data: bytes) -> tuple[str, str | None]:
        """Returns (status, detail). status is STATUS_OK when the archive may be opened."""
        if data.startswith(_OLE_MAGIC):
            if _ENCRYPTED_PACKAGE_STREAM in data:
                return STATUS_ENCRYPTED, "password-protected Office package"
            return STATUS_UNSUPPORTED, "legacy binary Office format (DOC/XLS/PPT) is not supported"
        if not data.startswith(_ZIP_MAGIC):
            return STATUS_CORRUPT, "not an OOXML (zip) package"
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > self._budget.max_archive_members:
                    return STATUS_BUDGET_EXCEEDED, f"archive has {len(infos)} members (limit {self._budget.max_archive_members})"
                total = 0
                for info in infos:
                    if info.flag_bits & 0x1:
                        return STATUS_ENCRYPTED, "archive member is encrypted"
                    name = info.filename
                    if name.startswith(("/", "\\")) or ".." in name.split("/"):
                        return STATUS_CORRUPT, "archive member path escapes the package"
                    if info.file_size > self._budget.max_member_bytes:
                        return STATUS_BUDGET_EXCEEDED, f"archive member exceeds {self._budget.max_member_bytes} bytes uncompressed"
                    if info.compress_size and info.file_size // max(1, info.compress_size) > self._budget.max_compression_ratio:
                        return STATUS_BUDGET_EXCEEDED, "archive member compression ratio is implausibly high"
                    total += info.file_size
                    if total > self._budget.max_total_uncompressed_bytes:
                        return STATUS_BUDGET_EXCEEDED, f"archive exceeds {self._budget.max_total_uncompressed_bytes} bytes uncompressed"
                if archive.testzip() is not None:
                    return STATUS_CORRUPT, "archive member failed its CRC check"
        except (zipfile.BadZipFile, RuntimeError, ValueError, OSError):
            return STATUS_CORRUPT, "archive could not be read"
        return STATUS_OK, None


class OfficeDocumentParser:
    def __init__(self, budget: ParseBudget | None = None) -> None:
        self._budget = budget or ParseBudget()
        self._preflight = OoxmlPreflight(self._budget)

    def supports(self, *, name: str, content_type: str) -> str | None:
        return classify_document(name, content_type)

    def parse(self, *, name: str, content_type: str, data: bytes) -> ParsedDocument:
        fmt = classify_document(name, content_type)
        if fmt is None:
            return ParsedDocument(None, STATUS_UNSUPPORTED, detail="format is not DOCX/XLSX/PPTX/PDF")
        if fmt == FORMAT_PDF:
            return self._parse_pdf(data)
        status, detail = self._preflight.check(data)
        if status != STATUS_OK:
            return ParsedDocument(fmt, status, detail=detail)
        macro_warning = ("macro-enabled package: macros ignored",) if name.lower().endswith(("m",)) and name.lower().rsplit(".", 1)[-1] in {"docm", "xlsm", "pptm"} else ()
        try:
            if fmt == FORMAT_DOCX:
                document = self._parse_docx(data)
            elif fmt == FORMAT_XLSX:
                document = self._parse_xlsx(data)
            else:
                document = self._parse_pptx(data)
        except Exception:  # noqa: BLE001 - the library's message may echo document content; only the class of failure leaves
            return ParsedDocument(fmt, STATUS_CORRUPT, detail="package opened but its parts are malformed")
        if macro_warning:
            document = ParsedDocument(document.format, document.status, document.blocks, (*document.warnings, *macro_warning), document.page_count, document.sheet_count, document.slide_count, document.truncated, document.detail)
        return document

    # ---- DOCX -----------------------------------------------------------------------------------------------------

    def _parse_docx(self, data: bytes) -> ParsedDocument:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(BytesIO(data))
        collector = BlockCollector(self._budget)
        paragraph_index = table_index = 0
        # Body children in document order: paragraphs and tables interleaved as authored.
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph_index += 1
                if not collector.add("paragraph", Paragraph(child, document).text, SourceLocator("paragraph", index=paragraph_index)):
                    break
            elif child.tag == qn("w:tbl"):
                table_index += 1
                if not collector.add("table", self._docx_table_text(Table(child, document)), SourceLocator("table", index=table_index)):
                    break
        if not collector.truncated:
            for section_index, section in enumerate(document.sections, start=1):
                for kind, part in (("header", section.header), ("footer", section.footer)):
                    if part is None or part.is_linked_to_previous:
                        continue
                    text = "\n".join(paragraph.text for paragraph in part.paragraphs)
                    if not collector.add(kind, text, SourceLocator(kind, index=section_index)):
                        break
        status = STATUS_OK if collector.blocks else STATUS_EMPTY
        return ParsedDocument(FORMAT_DOCX, status, tuple(collector.blocks), tuple(collector.warnings), truncated=collector.truncated)

    @staticmethod
    def _docx_table_text(table) -> str:
        rows: list[str] = []
        for row in table.rows:
            cells: list[str] = []
            previous = None
            for cell in row.cells:
                if cell._tc is previous:  # merged cells repeat the same element
                    continue
                previous = cell._tc
                cells.append(" ".join(cell.text.split()))
            rows.append(" | ".join(cells))
        return "\n".join(rows)

    # ---- XLSX -----------------------------------------------------------------------------------------------------

    def _parse_xlsx(self, data: bytes) -> ParsedDocument:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        # read_only streams rows; data_only=False keeps formulas as their text ("=SUM(A1:A3)") - nothing is evaluated;
        # keep_links=False prevents external workbook links from being resolved.
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
        collector = BlockCollector(self._budget)
        formulas = 0
        try:
            sheets = workbook.worksheets
            for sheet in sheets:
                if sheet.sheet_state != "visible":
                    collector.warn(f"hidden sheet included: {sheet.title}")
                rows_seen = 0
                for row_index, row in enumerate(sheet.iter_rows(values_only=False), start=1):
                    if rows_seen >= self._budget.max_rows_per_sheet:
                        collector.warn(f"sheet rows truncated at {self._budget.max_rows_per_sheet}: {sheet.title}")
                        break
                    cells: list[str] = []
                    first_col = last_col = None
                    for cell in row[: self._budget.max_cells_per_row]:
                        value = cell.value
                        if value is None or (isinstance(value, str) and not value.strip()):
                            continue
                        column = getattr(cell, "column", None) or (cells.__len__() + 1)
                        first_col = column if first_col is None else first_col
                        last_col = column
                        if isinstance(value, str) and value.startswith("="):
                            formulas += 1
                        cells.append(f"{get_column_letter(column)}{row_index}={self._cell_text(value)}")
                    if len(row) > self._budget.max_cells_per_row:
                        collector.warn(f"row cells truncated at {self._budget.max_cells_per_row}: {sheet.title}")
                    if not cells:
                        continue
                    rows_seen += 1
                    cell_range = f"{get_column_letter(first_col)}{row_index}:{get_column_letter(last_col)}{row_index}"
                    if not collector.add("sheet_row", " | ".join(cells), SourceLocator("sheet_row", sheet=sheet.title, cell_range=cell_range)):
                        break
                if collector.truncated:
                    break
            sheet_count = len(sheets)
        finally:
            workbook.close()
        if formulas:
            collector.warn(f"{formulas} formulas kept as text, not evaluated")
        status = STATUS_OK if collector.blocks else STATUS_EMPTY
        return ParsedDocument(FORMAT_XLSX, status, tuple(collector.blocks), tuple(collector.warnings), sheet_count=sheet_count, truncated=collector.truncated)

    @staticmethod
    def _cell_text(value) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return " ".join(str(value).split())

    # ---- PPTX -----------------------------------------------------------------------------------------------------

    def _parse_pptx(self, data: bytes) -> ParsedDocument:
        from pptx import Presentation

        presentation = Presentation(BytesIO(data))
        collector = BlockCollector(self._budget)
        slide_count = 0
        for slide_number, slide in enumerate(presentation.slides, start=1):
            slide_count += 1
            texts: list[str] = []
            tables: list[str] = []
            self._collect_shapes(slide.shapes, texts, tables)
            locator = SourceLocator("slide", slide=slide_number)
            if not collector.add("slide_text", "\n".join(texts), locator):
                break
            for table_text in tables:
                if not collector.add("slide_table", table_text, locator):
                    break
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                if not collector.add("notes", slide.notes_slide.notes_text_frame.text, SourceLocator("notes", slide=slide_number)):
                    break
            if collector.truncated:
                break
        status = STATUS_OK if collector.blocks else STATUS_EMPTY
        return ParsedDocument(FORMAT_PPTX, status, tuple(collector.blocks), tuple(collector.warnings), slide_count=slide_count, truncated=collector.truncated)

    def _collect_shapes(self, shapes, texts: list[str], tables: list[str]) -> None:
        for shape in shapes:
            if getattr(shape, "shape_type", None) is not None and shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
                self._collect_shapes(shape.shapes, texts, tables)
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [" | ".join(" ".join(cell.text.split()) for cell in row.cells) for row in shape.table.rows]
                tables.append("\n".join(rows))
                continue
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    texts.append(text)

    # ---- PDF ------------------------------------------------------------------------------------------------------

    def _parse_pdf(self, data: bytes) -> ParsedDocument:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted:
                try:
                    if reader.decrypt("") == 0:
                        return ParsedDocument(FORMAT_PDF, STATUS_ENCRYPTED, detail="password-protected PDF")
                except Exception:  # noqa: BLE001
                    return ParsedDocument(FORMAT_PDF, STATUS_ENCRYPTED, detail="password-protected PDF")
            page_count = len(reader.pages)
        except PdfReadError:
            return ParsedDocument(FORMAT_PDF, STATUS_CORRUPT, detail="PDF structure could not be read")
        except Exception:  # noqa: BLE001
            return ParsedDocument(FORMAT_PDF, STATUS_CORRUPT, detail="PDF structure could not be read")
        collector = BlockCollector(self._budget)
        image_pages = 0
        for index, page in enumerate(reader.pages, start=1):
            if index > self._budget.max_pdf_pages:
                collector.warn(f"pages truncated at {self._budget.max_pdf_pages}")
                break
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                collector.warn(f"page {index} could not be decoded")
                text = ""
            if not text.strip() and self._page_has_images(page):
                image_pages += 1
            if not collector.add("page", text, SourceLocator("page", page=index)):
                break
        if collector.blocks:
            if image_pages:
                collector.warn(f"{image_pages} pages have images but no text layer (possible partial scan)")
            return ParsedDocument(FORMAT_PDF, STATUS_OK, tuple(collector.blocks), tuple(collector.warnings), page_count=page_count, truncated=collector.truncated)
        if image_pages:
            return ParsedDocument(FORMAT_PDF, STATUS_NEEDS_OCR, (), tuple(collector.warnings), page_count=page_count, detail=f"{image_pages} of {page_count} pages are image-only; OCR is required")
        return ParsedDocument(FORMAT_PDF, STATUS_EMPTY, (), tuple(collector.warnings), page_count=page_count, detail="no text and no images")

    @staticmethod
    def _page_has_images(page) -> bool:
        try:
            resources = page.get("/Resources") or {}
            xobjects = resources.get("/XObject") if hasattr(resources, "get") else None
            if not xobjects:
                return False
            for key in list(xobjects.keys()):
                obj = xobjects[key].get_object()
                if obj.get("/Subtype") == "/Image":
                    return True
        except Exception:  # noqa: BLE001
            return False
        return False
