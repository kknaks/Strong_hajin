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

/**
 * 범용 명령 단추로 그리지 않는 명령 — 자기 편집 창이 부르는 것들이다.
 *
 * `save_draft`(AX 초안 저장, WORK-009 2a-1)는 「수정」 창의 「저장」 단추만 부른다. 봉투가 `allowed_commands` 에
 * 함께 싣기 때문에 명령을 전부 단추로 그리는 자리(결과 카드 · 판단 상세 footer 등)로 떨어지면 「저장」 단추가 따로 서게 된다.
 */
export const EDITOR_ONLY_COMMANDS: ReadonlySet<string> = new Set(["save_draft"]);

export function withoutEditorOnlyCommands<T extends { id: string }>(commands: ReadonlyArray<T>): T[] {
  return commands.filter((command) => !EDITOR_ONLY_COMMANDS.has(command.id));
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
  const shown = withoutEditorOnlyCommands(commands ?? []);
  if (shown.length === 0) return null;
  return (
    <>
      {shown.map((command) => (
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
