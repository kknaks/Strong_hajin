/**
 * HTML5 calendar drag diagnostics. This is deliberately observability only:
 * it never supplies a task id and never changes drop acceptance.
 *
 * Vite removes the call sites from production behavior through the DEV guard.
 * The same record is kept on `window` so a Tauri Web Inspector session can
 * inspect the last events even when console output is not attached to stdout.
 */
export type CalendarDndStage = "dragstart" | "dragover" | "drop";

export type CalendarDndRecord = {
  stage: CalendarDndStage;
  types: string[];
  plain: string;
  text: string;
  at: number;
};

type DebugWindow = Window & { __SCAX_CALENDAR_DND_LOG__?: CalendarDndRecord[] };

function read(transfer: DataTransfer | null, type: string): string {
  if (!transfer) return "";
  try {
    return transfer.getData(type);
  } catch {
    return "<getData threw>";
  }
}

export function debugCalendarDnd(stage: CalendarDndStage, transfer: DataTransfer | null): void {
  if (!import.meta.env.DEV) return;
  const record: CalendarDndRecord = {
    stage,
    types: transfer?.types ? Array.from(transfer.types) : [],
    plain: read(transfer, "text/plain"),
    text: read(transfer, "text"),
    at: Date.now(),
  };
  const scope = window as DebugWindow;
  const log = scope.__SCAX_CALENDAR_DND_LOG__ ?? [];
  log.push(record);
  scope.__SCAX_CALENDAR_DND_LOG__ = log.slice(-30);
  console.info("[calendar-dnd]", record);
}
