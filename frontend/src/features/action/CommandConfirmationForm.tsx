import { useId, useState } from 'react';
import { Button } from "../../ds/Button";
import { useActionDraft } from '../work/useActionDraft';
import type { ActionCommand, ActionEditContract, ActionEditField } from '../../lib/viewModels';

function localDateTime(value: unknown): string {
  if (!value) return '';
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return '';
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Render only the fields the owning operation offers; preserve its bound identities. */
export function CommandConfirmationForm({ actionId, principalId, contract, commands, onCommand }: {
  actionId: string;
  principalId: string;
  contract: ActionEditContract;
  commands: ActionCommand[];
  onCommand: (command: string, payload: Record<string, unknown>) => Promise<unknown>;
}) {
  const prefix = useId();
  const sanitize = (values: Record<string, unknown>) => ({
    ...contract.values,
    ...Object.fromEntries(contract.fields.filter(field => field.editable && Object.hasOwn(values, field.id)).map(field => [field.id, values[field.id]])),
  });
  const recovered = useActionDraft({ principalId, actionId, baseSubmissionVersion: contract.base_submission_version, baseDraft: contract.values, pending: commands.length > 0, sanitize });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = commands.find(command => command.id === 'confirm');
  const set = (field: string, value: unknown) => recovered.setDraft({ ...recovered.draft, [field]: value });

  function finalDraft() {
    const draft = sanitize(recovered.draft);
    for (const field of contract.fields) {
      if (field.empty_policy === 'omit' && (draft[field.id] === null || draft[field.id] === '')) delete draft[field.id];
    }
    return draft;
  }

  async function run(command: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await onCommand(command, command === 'confirm' ? { base_submission_version: contract.base_submission_version, draft: finalDraft() } : {});
      if (result !== false) recovered.clear();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '변경을 반영하지 못했습니다. 입력한 내용은 유지했습니다.');
    } finally {
      setBusy(false);
    }
  }

  function control(field: ActionEditField) {
    const value = recovered.draft[field.id];
    const id = `${prefix}-${field.id}`;
    const required = field.empty_policy === 'forbid' || field.required;
    const empty = field.empty_policy === 'forbid' || field.empty_policy === 'empty_string' ? '' : null;
    if (!field.editable) return <output id={id}>{field.label_value ?? field.value ?? '—'}</output>;
    if (field.type === 'boolean') return <input id={id} type="checkbox" checked={Boolean(value)} onChange={event => set(field.id, event.target.checked)} />;
    if (field.type === 'select') return <select id={id} required={required} value={String(value ?? '')} onChange={event => set(field.id, event.target.value || empty)}>
      <option value="" disabled={required}>{field.empty_policy === 'omit' ? '변경 안 함' : required ? '선택해 주세요' : '선택 안 함'}</option>
      {(field.options ?? []).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>;
    if (field.type === 'ordered_select') {
      const selected = Array.isArray(value) ? value.map(String) : [];
      const labels = new Map((field.options ?? []).map(option => [option.value, option.label]));
      const move = (index: number, offset: number) => {
        const ordered = [...selected];
        [ordered[index], ordered[index + offset]] = [ordered[index + offset], ordered[index]];
        set(field.id, ordered);
      };
      return <ol id={id} aria-label={field.label} className="action-command-order">{selected.map((key, index) => <li key={key}>
        <span>{labels.get(key) ?? '변경되었거나 볼 수 없는 단계'}</span>
        <button type="button" disabled={index === 0} aria-label={`${labels.get(key) ?? '단계'} 위로`} onClick={() => move(index, -1)}>위로</button>
        <button type="button" disabled={index === selected.length - 1} aria-label={`${labels.get(key) ?? '단계'} 아래로`} onClick={() => move(index, 1)}>아래로</button>
      </li>)}</ol>;
    }
    if (field.type === 'multi_select') {
      const selected = Array.isArray(value) ? value.map(String) : [];
      return <div id={id} role="group" aria-label={field.label} className="action-task-options">{(field.options ?? []).map(option => <label key={option.value}>
        <input type="checkbox" checked={selected.includes(option.value)} onChange={event => set(field.id, event.target.checked ? [...selected, option.value] : selected.filter(item => item !== option.value))} />{option.label}
      </label>)}</div>;
    }
    if (field.type === 'source_select') {
      const selected = (Array.isArray(value) ? value : []) as Record<string, unknown>[];
      const sourceKey = (source: Record<string, unknown>) => JSON.stringify([source.task_id, source.task_version, source.occurred_at]);
      return <div id={id} role="group" aria-label={field.label} className="action-task-options">{(field.options ?? []).map(option => {
        const source = JSON.parse(option.value) as Record<string, unknown>;
        const key = sourceKey(source);
        return <label key={key}><input type="checkbox" checked={selected.some(item => sourceKey(item) === key)} onChange={event => set(field.id, event.target.checked ? [...selected, source] : selected.filter(item => sourceKey(item) !== key))} />{option.label}</label>;
      })}</div>;
    }
    if (field.type === 'string_list') return <textarea id={id} required={field.required} value={Array.isArray(value) ? value.join('\n') : ''} onChange={event => set(field.id, event.target.value.split('\n').filter(Boolean))} />;
    if (field.type === 'textarea') return <textarea id={id} required={required} value={String(value ?? '')} onChange={event => set(field.id, event.target.value || empty)} />;
    if (field.type === 'datetime') return <input id={id} type="datetime-local" required={required} value={localDateTime(value)} onChange={event => set(field.id, event.target.value ? new Date(event.target.value).toISOString() : empty)} />;
    return <input id={id} type={field.type === 'date' ? 'date' : 'text'} required={required} value={String(value ?? '')} onChange={event => set(field.id, event.target.value || empty)} />;
  }

  return <form className="action-command-editor" aria-label="변경 내용 확인" onSubmit={event => { event.preventDefault(); if (confirm && !recovered.stale) void run(confirm.id); }}>
    {recovered.stale && <div role="alert">승인 내용이 갱신되었습니다. 저장한 수정값을 확인해 주세요.
      <button type="button" onClick={recovered.startFromLatest}>최신 내용 사용</button>
      <button type="button" onClick={recovered.continueWithLocal}>내 수정값으로 계속</button>
    </div>}
    <fieldset disabled={busy || Boolean(recovered.stale)} className="form-grid">
      {/* 바퀴 12(M-5): main 이 구 `.field` 로 들여온 것을 새 DS 폼 어휘로 바꿨다 (바퀴 8-A 방식) */}
      {contract.fields.map(field => (
        <div className="scax-field" key={field.id}>
          <label className="scax-field__label" htmlFor={`${prefix}-${field.id}`}>{field.label}</label>
          {control(field)}
        </div>
      ))}
    </fieldset>
    {error && <p role="alert">{error}</p>}
    <div className="action-task-buttons">
      {/* 바퀴 12(M-5): 구 `.btn h36` 은 CSS 가 이미 없어 벗겨진 채로 떴다. DS Button 으로 바꾼다 —
          h36 은 새 램프의 sm(32)·md(39) 중 md 에 가까워 기본 크기를 쓰고, 구 `ghost` 는 `variant="text"` 다. */}
      {commands.map(command => (
        <Button
          disabled={busy || (command.id === 'confirm' && Boolean(recovered.stale))}
          key={command.id}
          onClick={command.id === 'confirm' ? undefined : () => void run(command.id)}
          tone={command.tone === 'primary' ? 'primary' : 'neutral'}
          type={command.id === 'confirm' ? 'submit' : 'button'}
          variant={command.tone === 'primary' ? 'solid' : 'text'}
        >
          {command.label}
        </Button>
      ))}
      {recovered.restored && (
        <Button disabled={busy} onClick={recovered.reset} type="button" variant="text">
          수정 취소
        </Button>
      )}
    </div>
  </form>;
}
