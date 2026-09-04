import type { ActionCommand, ActionItem } from "./viewModels";
import { formatDate } from "./labels";

/**
 * Shared presentation of an AX Action for the chat card and the decision inbox. Everything shown comes from the server
 * projection (subject, operation label, preview rows, commands); nothing is derived from action_type or the payload.
 */
export function actionSubject(action: ActionItem): string {
  return action.subject ?? action.title;
}

export function actionKicker(action: ActionItem): string {
  return action.operation_label ? `AX 제안 · ${action.operation_label}` : "AX 제안";
}

export function ActionPreviewDetails({ action, defaultOpen = false }: { action: ActionItem; defaultOpen?: boolean }) {
  const rows = action.preview ?? [];
  if (rows.length === 0) return null;
  return (
    <details className="ax-preview" data-action-preview={action.action_id} open={defaultOpen || undefined}>
      <summary>
        상세 보기 <small>· 승인 시 반영되는 {rows.length}개 항목</small>
      </summary>
      <dl className="ax-preview-list">
        {rows.map((row) => (
          <div className={`ax-preview-row ${row.kind}`} key={row.id}>
            <dt>{row.label}</dt>
            <dd>{row.kind === "date" ? formatDate(row.value) : row.value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function ActionCommandButtons({
  commands,
  disabled,
  onCommand,
}: {
  commands: ActionCommand[] | undefined;
  disabled?: boolean;
  onCommand: (commandId: string) => void;
}) {
  if (!commands || commands.length === 0) return null;
  return (
    <>
      {commands.map((command) => (
        <button
          className={`btn h30 ${command.tone === "primary" ? "primary" : command.tone === "danger" ? "danger" : ""}`}
          disabled={disabled}
          key={command.id}
          onClick={() => onCommand(command.id)}
          type="button"
        >
          {command.label}
        </button>
      ))}
    </>
  );
}
