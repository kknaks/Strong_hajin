import { Button } from "../../ds/Button";
import type { ActionCommand, ActionItem } from "../../lib/viewModels";
import { formatDate, formatDateTime } from "../../lib/labels";

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

export function ActionPreviewDetails({
  action,
  defaultOpen = false,
  excludeRowIds = [],
}: {
  action: ActionItem;
  defaultOpen?: boolean;
  excludeRowIds?: string[];
}) {
  const rows = (action.preview ?? []).filter((row) => !excludeRowIds.includes(row.id));
  if (rows.length === 0) return null;
  return (
    <details className="scax-preview" data-action-preview={action.action_id} open={defaultOpen || undefined}>
      <summary>
        상세 보기 <small>· {action.state === "pending" ? "승인 전 확인할" : "처리 결과"} {rows.length}개 항목</small>
      </summary>
      <dl className="scax-preview__list">
        {rows.map((row) => (
          <div className={`scax-preview__row scax-preview__row--${row.kind}`} key={row.id}>
            <dt>{row.label}</dt>
            <dd>{row.kind === "date" ? formatDate(row.value) : row.kind === "datetime" ? formatDateTime(row.value) : row.value}</dd>
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
        <Button
          disabled={disabled}
          key={command.id}
          onClick={() => onCommand(command.id)}
          size="sm"
          tone={command.tone === "primary" ? "primary" : command.tone === "danger" ? "danger" : "neutral"}
          type="button"
          variant={command.tone === "primary" || command.tone === "danger" ? "solid" : "outlined"}
        >
          {command.label}
        </Button>
      ))}
    </>
  );
}
