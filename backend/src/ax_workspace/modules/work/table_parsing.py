"""Stream table regions without using a row/column batch size to discard source content."""
from collections.abc import Iterable
from dataclasses import dataclass, replace

from ax_workspace.modules.work.document_parsing import BlockCollector, SourceLocator

TABLE_ROWS_PER_BLOCK = 16
TABLE_COLUMNS_PER_BLOCK = 32
CONTEXT_PREVIEW_CHARS = 512


@dataclass(frozen=True, slots=True)
class TableCell:
    column: int
    text: str
    end_column: int | None = None

    @property
    def last_column(self) -> int:
        return self.end_column if self.end_column is not None else self.column


def column_label(column: int) -> str:
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(65 + remainder) + result
    return result


def collect_table(
    collector: BlockCollector,
    rows: Iterable[tuple[int, list[TableCell]]],
    locator: SourceLocator,
    *,
    kind: str,
    rows_per_block: int = TABLE_ROWS_PER_BLOCK,
) -> bool:
    """The first non-empty row is context, not a claim that the author designated a header.

    Its complete source is indexed in its own region. Later regions carry a bounded preview and coordinates
    for the corresponding columns; a long header is never removed from the source projection.
    """
    header: list[TableCell] = []
    header_row = None
    pending: list[tuple[int, list[TableCell]]] = []

    def render_cell(row: int, cell: TableCell) -> str:
        if kind == "sheet_row":
            return cell.text  # XLSX values already carry their original A1 address.
        position = f"R{row}C{cell.column}"
        if cell.last_column != cell.column:
            position += f":C{cell.last_column}"
        return f"{position}={cell.text}"

    def flush() -> bool:
        regions: dict[int, list] = {}
        for row, cells in pending:
            buckets: dict[int, list] = {}
            for cell in cells:
                for bucket in range((cell.column - 1) // TABLE_COLUMNS_PER_BLOCK, (cell.last_column - 1) // TABLE_COLUMNS_PER_BLOCK + 1):
                    first = max(cell.column, bucket * TABLE_COLUMNS_PER_BLOCK + 1)
                    last = min(cell.last_column, (bucket + 1) * TABLE_COLUMNS_PER_BLOCK)
                    buckets.setdefault(bucket, []).append(replace(cell, column=first, end_column=last))
            for bucket, values in buckets.items():
                regions.setdefault(bucket, []).append((row, values))
        for bucket in sorted(regions):
            region = regions[bucket]
            columns = [column for _, cells in region for cell in cells for column in (cell.column, cell.last_column)]
            first, last = min(columns), max(columns)
            start, end = region[0][0], region[-1][0]
            cells = f"{column_label(first)}{start}:{column_label(last)}{end}" if locator.sheet else None
            source = replace(locator, row_start=start, row_end=end, column_start=first, column_end=last,
                             cell_range=cells, index=start if kind == "sheet_row" else locator.index)
            preview = " | ".join(render_cell(header_row, cell) for cell in header if cell.column <= last and cell.last_column >= first)
            context = {
                "basis": "first_non_empty_row", "row": header_row,
                "column_start": first, "column_end": last,
                "text": preview[:CONTEXT_PREVIEW_CHARS], "preview_truncated": len(preview) > CONTEXT_PREVIEW_CHARS,
            }
            text = "\n".join(" | ".join(render_cell(row, cell) for cell in row_cells) for row, row_cells in region)
            if not collector.add(kind, text, source, header_context=context):
                return False
        pending.clear()
        return True

    for row, cells in rows:
        cells = [cell for cell in cells if cell.text.strip()]
        if not cells:
            continue
        if header_row is None:
            header_row, header = row, cells
        pending.append((row, cells))
        if len(pending) == rows_per_block and not flush():
            return False
    return flush() if pending else True
