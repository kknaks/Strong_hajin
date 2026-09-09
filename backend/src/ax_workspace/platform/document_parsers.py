"""Office/PDF document parser adapter.

OOXML packages go through a ZIP preflight (member count, per-member size, total uncompressed size, compression
ratio, encryption flags, path traversal) before any library opens them. python-docx / openpyxl / python-pptx /
pypdf then read the structure; formulas stay as text, macros and embedded objects are ignored, external links are
never followed, and failures become explainable statuses rather than library exceptions.
"""
from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import logging
from threading import get_ident
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
    ProjectionWriteError,
    SourceLocator,
    classify_document,
)

from ax_workspace.modules.work.table_parsing import TableCell, collect_table

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE_STREAM = "EncryptedPackage".encode("utf-16-le")
_ZIP_MAGIC = b"PK\x03\x04"


class _PdfReadWarnings(logging.Handler):
    """pypdf sometimes returns text after skipping a source unit. Keep reason codes, never raw warning text.

    Handlers are scoped to the extracting thread because workers may parse different documents concurrently.
    """

    def __init__(self, page) -> None:
        super().__init__(logging.WARNING)
        self.page = page
        self.thread_id = get_ident()
        self.skipped = False
        self.capacity_limit = False

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id:
            return
        self.skipped = True
        message = record.getMessage()
        if message.startswith("Exceeded ") and "form XObject invocations" in message:
            self.capacity_limit = True

    def __enter__(self):
        from pypdf.errors import LimitReachedError

        # pypdf catches Form decoder exceptions and only logs them. Observe the exception on this page instance
        # before that catch, so even a disabled logger cannot turn a capacity failure into completed text.
        self.previous_override = self.page.__dict__.get("extract_xform_text")
        original = self.page.extract_xform_text

        def read_form(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except (LimitReachedError, RecursionError):
                self.capacity_limit = True
                raise
            except Exception:
                self.skipped = True
                raise

        self.page.extract_xform_text = read_form
        logging.getLogger("pypdf").addHandler(self)
        return self

    def __exit__(self, *exc):
        logging.getLogger("pypdf").removeHandler(self)
        if self.previous_override is None:
            del self.page.extract_xform_text
        else:
            self.page.extract_xform_text = self.previous_override
        self.close()


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

    def parse(self, *, name: str, content_type: str, data: bytes, sink=None) -> ParsedDocument:
        fmt = classify_document(name, content_type)
        if fmt is None:
            return ParsedDocument(None, STATUS_UNSUPPORTED, detail="format is not DOCX/XLSX/PPTX/PDF")
        if fmt == FORMAT_PDF:
            return self._parse_pdf(data, sink=sink)
        status, detail = self._preflight.check(data)
        if status != STATUS_OK:
            return ParsedDocument(fmt, status, detail=detail)
        macro_warning = ("macro-enabled package: macros ignored",) if name.lower().endswith(("m",)) and name.lower().rsplit(".", 1)[-1] in {"docm", "xlsm", "pptm"} else ()
        try:
            if fmt == FORMAT_DOCX:
                document = self._parse_docx(data, sink=sink)
            elif fmt == FORMAT_XLSX:
                document = self._parse_xlsx(data, sink=sink)
            else:
                document = self._parse_pptx(data, sink=sink)
        except ProjectionWriteError:
            raise
        except Exception:  # noqa: BLE001 - the library's message may echo document content; only the class of failure leaves
            return ParsedDocument(fmt, STATUS_CORRUPT, detail="package opened but its parts are malformed")
        if macro_warning:
            document = replace(document, warnings=(*document.warnings, *macro_warning))
        return document

    # ---- DOCX -----------------------------------------------------------------------------------------------------

    def _parse_docx(self, data: bytes, *, sink=None) -> ParsedDocument:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(BytesIO(data))
        collector = BlockCollector(self._budget, sink=sink)
        paragraph_index = table_index = 0
        # Body children in document order: paragraphs and tables interleaved as authored.
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph_index += 1
                if not collector.add("paragraph", Paragraph(child, document).text, SourceLocator("paragraph", index=paragraph_index)):
                    break
            elif child.tag == qn("w:tbl"):
                table_index += 1
                if not collect_table(collector, self._docx_rows(Table(child, document)), SourceLocator("table", index=table_index), kind="table"):
                    break
        if not collector.truncated:
            for section_index, section in enumerate(document.sections, start=1):
                parts = [("header", section.header, None), ("footer", section.footer, None)]
                if section.different_first_page_header_footer:
                    parts.extend([("header", section.first_page_header, "first"), ("footer", section.first_page_footer, "first")])
                if document.settings.odd_and_even_pages_header_footer:
                    parts.extend([("header", section.even_page_header, "even"), ("footer", section.even_page_footer, "even")])
                for kind, part, variant in parts:
                    if part is None or part.is_linked_to_previous:
                        continue
                    part_table_index = 0
                    for item in part.iter_inner_content():
                        if isinstance(item, Table):
                            part_table_index += 1
                            if not collect_table(collector, self._docx_rows(item), SourceLocator(
                                "table", index=part_table_index, container=kind, section=section_index, variant=variant,
                            ), kind="table"):
                                break
                        elif not collector.add(kind, item.text, SourceLocator(kind, index=section_index, variant=variant)):
                            break
        status = STATUS_OK if collector.count else STATUS_EMPTY
        return ParsedDocument(FORMAT_DOCX, status, tuple(collector.blocks), tuple(collector.warnings), truncated=collector.truncated)

    @staticmethod
    def _docx_rows(table):
        from docx.table import Table

        for index, row in enumerate(table.rows, start=1):
            cells = []
            previous = None
            for column, cell in enumerate(row.cells, start=1 + row.grid_cols_before):
                if cell._tc is previous:
                    continue
                previous = cell._tc
                text = " ".join(
                    OfficeDocumentParser._docx_table_text(item) if isinstance(item, Table) else item.text
                    for item in cell.iter_inner_content()
                )
                cells.append(TableCell(column, text, column + cell.grid_span - 1))
            yield index, cells

    @staticmethod
    def _docx_table_text(table) -> str:
        return "\n".join(" | ".join(f"R{row}C{cell.column}={cell.text}" for cell in cells) for row, cells in OfficeDocumentParser._docx_rows(table))

    # ---- XLSX -----------------------------------------------------------------------------------------------------

    def _parse_xlsx(self, data: bytes, *, sink=None) -> ParsedDocument:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        # read_only streams rows; data_only=False keeps formulas as their text ("=SUM(A1:A3)") - nothing is evaluated;
        # keep_links=False prevents external workbook links from being resolved.
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
        collector = BlockCollector(self._budget, sink=sink)
        formulas = 0
        try:
            sheets = workbook.worksheets
            for sheet in sheets:
                if sheet.sheet_state != "visible":
                    collector.warn(f"hidden sheet included: {sheet.title}")
                sheet.reset_dimensions()  # Do not trust producer-supplied dimensions to hide later cells.
                def rows():
                    nonlocal formulas
                    rows_seen = 0
                    for row_index, row in enumerate(sheet.iter_rows(values_only=False), start=1):
                        if self._budget.max_rows_per_sheet is not None and rows_seen >= self._budget.max_rows_per_sheet:
                            collector.truncated = True
                            collector.warn(f"sheet rows truncated at {self._budget.max_rows_per_sheet}: {sheet.title}")
                            return
                        cells = []
                        for column, cell in enumerate(row[: self._budget.max_cells_per_row], start=1):
                            value = cell.value
                            if value is None or (isinstance(value, str) and not value.strip()):
                                continue
                            if isinstance(value, str) and value.startswith("="):
                                formulas += 1
                            cells.append(TableCell(column, f"{get_column_letter(column)}{row_index}={self._cell_text(value)}"))
                        if self._budget.max_cells_per_row is not None and len(row) > self._budget.max_cells_per_row:
                            collector.truncated = True
                            collector.warn(f"row cells truncated at {self._budget.max_cells_per_row}: {sheet.title}")
                            return
                        if cells:
                            if rows_seen == 0:
                                spans = self._xlsx_header_spans(data, sheet._worksheet_path, row_index)
                                cells = [replace(cell, end_column=spans.get(cell.column)) for cell in cells]
                            rows_seen += 1
                            yield row_index, cells
                collect_table(collector, rows(), SourceLocator("sheet_row", sheet=sheet.title), kind="sheet_row", rows_per_block=1)
                if collector.truncated:
                    break
            sheet_count = len(sheets)
        finally:
            workbook.close()
        if formulas:
            collector.warn(f"{formulas} formulas kept as text, not evaluated")
        status = STATUS_OK if collector.count else STATUS_EMPTY
        return ParsedDocument(FORMAT_XLSX, status, tuple(collector.blocks), tuple(collector.warnings), sheet_count=sheet_count, truncated=collector.truncated)

    @staticmethod
    def _xlsx_header_spans(data: bytes, worksheet_path: str, header_row: int) -> dict[int, int]:
        """Read merge references absent from read-only worksheets without retaining worksheet XML/cells.

        Only merges anchored in the context row are retained. A sheet can contain arbitrarily many later merges
        within the package admission budget; they do not grow this header map.
        """
        from xml.etree.ElementTree import iterparse
        from openpyxl.utils import range_boundaries

        spans = {}
        with zipfile.ZipFile(BytesIO(data)) as package, package.open(worksheet_path) as source:
            stack = []
            for event, element in iterparse(source, events=("start", "end")):
                if event == "start":
                    stack.append(element)
                    continue
                if element.tag == "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}mergeCell":
                    first, row, last, _ = range_boundaries(element.attrib["ref"])
                    if row == header_row:
                        spans[first] = last
                stack.pop()
                if stack:
                    stack[-1].remove(element)
                element.clear()
        return spans

    @staticmethod
    def _cell_text(value) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return " ".join(str(value).split())

    # ---- PPTX -----------------------------------------------------------------------------------------------------

    def _parse_pptx(self, data: bytes, *, sink=None) -> ParsedDocument:
        from pptx import Presentation

        presentation = Presentation(BytesIO(data))
        collector = BlockCollector(self._budget, sink=sink)
        slide_count = 0
        for slide_number, slide in enumerate(presentation.slides, start=1):
            slide_count += 1
            texts: list[str] = []
            tables: list = []
            self._collect_shapes(slide.shapes, texts, tables)
            locator = SourceLocator("slide", slide=slide_number)
            if not collector.add("slide_text", "\n".join(texts), locator):
                break
            for table_index, table in enumerate(tables, start=1):
                rows = ((row_index, [TableCell(column, cell.text, column + cell.span_width - 1) for column, cell in enumerate(row.cells, start=1)])
                        for row_index, row in enumerate(table.rows, start=1))
                if not collect_table(collector, rows, SourceLocator("table", index=table_index, slide=slide_number), kind="slide_table"):
                    break
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                if not collector.add("notes", slide.notes_slide.notes_text_frame.text, SourceLocator("notes", slide=slide_number)):
                    break
            if collector.truncated:
                break
        status = STATUS_OK if collector.count else STATUS_EMPTY
        return ParsedDocument(FORMAT_PPTX, status, tuple(collector.blocks), tuple(collector.warnings), slide_count=slide_count, truncated=collector.truncated)

    def _collect_shapes(self, shapes, texts: list[str], tables: list) -> None:
        for shape in shapes:
            if getattr(shape, "shape_type", None) is not None and shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
                self._collect_shapes(shape.shapes, texts, tables)
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                tables.append(shape.table)
                continue
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    texts.append(text)

    # ---- PDF ------------------------------------------------------------------------------------------------------

    def _parse_pdf(self, data: bytes, *, sink=None) -> ParsedDocument:
        from pypdf import PdfReader
        from pypdf.errors import LimitReachedError, PdfReadError

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
        collector = BlockCollector(self._budget, sink=sink)
        image_pages = 0
        decoded_bytes = 0
        missing: list[dict] = []
        for index, page in enumerate(reader.pages, start=1):
            if self._budget.max_pdf_pages is not None and index > self._budget.max_pdf_pages:
                collector.truncated = True
                collector.warn(f"pages truncated at {self._budget.max_pdf_pages}")
                break
            try:
                page_bytes = self._pdf_content_size(page)
                decoded_bytes += page_bytes
                if page_bytes > self._budget.max_pdf_content_bytes or decoded_bytes > self._budget.max_pdf_total_content_bytes:
                    return ParsedDocument(FORMAT_PDF, STATUS_BUDGET_EXCEEDED, page_count=page_count,
                                          detail="PDF decoded content exceeds admission ceiling", coverage={"complete": False})
                with _PdfReadWarnings(page) as notices:
                    text = page.extract_text() or ""
                if notices.capacity_limit:
                    return ParsedDocument(FORMAT_PDF, STATUS_BUDGET_EXCEEDED, page_count=page_count,
                                          detail="PDF Form invocation limit exceeded", coverage={"complete": False})
                if notices.skipped:
                    collector.warn(f"page {index} reported a parser warning; text coverage is uncertain")
                    missing.append({"page": index, "reason": "parser_warning"})
            except (LimitReachedError, RecursionError):
                return ParsedDocument(FORMAT_PDF, STATUS_BUDGET_EXCEEDED, page_count=page_count,
                                      detail="PDF decompression limit exceeded", coverage={"complete": False})
            except Exception:  # noqa: BLE001
                collector.warn(f"page {index} could not be decoded")
                missing.append({"page": index, "reason": "decode_failed"})
                continue
            if not text.strip() and self._page_has_images(page):
                image_pages += 1
                missing.append({"page": index, "reason": "needs_ocr"})
            if not collector.add("page", text, SourceLocator("page", page=index)):
                break
        coverage = {"unit": "page", "total_units": page_count, "processed_units": page_count - len({item["page"] for item in missing}), "missing_units": missing}
        if collector.count:
            if image_pages:
                collector.warn(f"{image_pages} pages have images but no text layer (possible partial scan)")
            return ParsedDocument(FORMAT_PDF, "partial" if missing else STATUS_OK, tuple(collector.blocks), tuple(collector.warnings), page_count=page_count, truncated=collector.truncated, coverage=coverage)
        if image_pages:
            return ParsedDocument(FORMAT_PDF, STATUS_NEEDS_OCR, (), tuple(collector.warnings), page_count=page_count, coverage=coverage, detail=f"{image_pages} of {page_count} pages are image-only; OCR is required")
        if missing:
            return ParsedDocument(FORMAT_PDF, STATUS_CORRUPT, warnings=tuple(collector.warnings), page_count=page_count, coverage=coverage, detail="no page could be decoded")
        return ParsedDocument(FORMAT_PDF, STATUS_EMPTY, (), tuple(collector.warnings), page_count=page_count, detail="no text and no images")

    def _pdf_content_size(self, page) -> int:
        """Count decoded text/Form streams before pypdf builds text operations; never decode image pixels.

        pypdf's own decompression limits apply while reading each stream. The smaller application budget applies
        before text extraction, including nested Form XObjects and cyclic resource graphs.
        """
        seen: set[int] = set()
        content = page.get_contents()
        total = len(content.get_data()) if content is not None else 0
        pending = [page.get("/Resources")]
        while pending and total <= self._budget.max_pdf_content_bytes:
            resources = pending.pop()
            if resources is None:
                continue
            resources = resources.get_object()
            if id(resources) in seen:
                continue
            seen.add(id(resources))
            objects = resources.get("/XObject")
            if objects is None:
                continue
            for reference in objects.get_object().values():
                obj = reference.get_object()
                if obj.get("/Subtype") != "/Form" or id(obj) in seen:
                    continue
                seen.add(id(obj))
                total += len(obj.get_data())
                if total > self._budget.max_pdf_content_bytes:
                    break
                pending.append(obj.get("/Resources"))
        if total > self._budget.max_pdf_content_bytes:
            return total
        return max(total, self._pdf_expansion_size(page))

    def _pdf_expansion_size(self, page) -> int:
        """Bound actual Form expansion independently of pypdf's logging configuration and silent skip limit."""
        from pypdf.errors import LimitReachedError
        from pypdf.generic import ContentStream

        content = page.get_contents()
        if content is None:
            return 0
        size = len(content.get_data())
        count = 0
        stack = [(iter(content.operations), page.get("/Resources"), frozenset())]
        cache = {}
        while stack:
            operations, resources, ancestors = stack[-1]
            item = next(operations, None)
            if item is None:
                stack.pop()
                continue
            operands, operator = item
            if operator != b"Do":
                continue
            resource = resources.get_object()
            form = resource["/XObject"].get_object()[operands[0]].get_object()
            if form.get("/Subtype") == "/Image":
                continue
            count += 1
            if count > self._budget.max_pdf_form_invocations or len(ancestors) >= self._budget.max_pdf_form_depth:
                raise LimitReachedError("PDF Form expansion exceeds admission ceiling")
            key = id(form)
            if key in ancestors:
                raise ValueError("cyclic PDF Form invocation")
            size += len(form.get_data())
            if size > self._budget.max_pdf_content_bytes:
                return size
            if key not in cache:
                cache[key] = ContentStream(form, page.pdf).operations
            nested_resources = form.get("/Resources") or resources
            stack.append((iter(cache[key]), nested_resources, ancestors | {key}))
        return size

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
