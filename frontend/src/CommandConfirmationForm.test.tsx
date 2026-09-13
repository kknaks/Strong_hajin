import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CommandConfirmationForm } from './CommandConfirmationForm';
import type { ActionEditContract } from './viewModels';

const contract: ActionEditContract = {
  editor: 'command', base_submission_version: 1,
  values: { project_id: 'bound-project', expected_version: 2, name: '제안 원안', description: null },
  fields: [
    { id: 'name', label: '프로젝트 명', type: 'text', required: true, editable: true },
    { id: 'description', label: '설명', type: 'textarea', required: false, editable: true },
  ],
};
const commands = [{ id: 'confirm', label: '이 내용으로 반영', tone: 'primary' }, { id: 'reject', label: '거절', tone: 'neutral' }];
afterEach(() => { cleanup(); localStorage.clear(); });

it('submits edited values with the original target and base submission', async () => {
  const onCommand = vi.fn().mockResolvedValue(undefined);
  render(<CommandConfirmationForm actionId="a1" principalId="mina" contract={contract} commands={commands} onCommand={onCommand} />);
  fireEvent.change(screen.getByLabelText('프로젝트 명'), { target: { value: '최종 프로젝트' } });
  fireEvent.click(screen.getByRole('button', { name: '이 내용으로 반영' }));
  await waitFor(() => expect(onCommand).toHaveBeenCalledWith('confirm', { base_submission_version: 1, draft: { ...contract.values, name: '최종 프로젝트' } }));
  expect(screen.queryByDisplayValue('bound-project')).toBeNull();
});

it('keeps failed edits and restores them only for the same principal and action', async () => {
  const onCommand = vi.fn().mockRejectedValue(new Error('최신 권한 확인 실패'));
  const view = render(<CommandConfirmationForm actionId="a1" principalId="mina" contract={contract} commands={commands} onCommand={onCommand} />);
  fireEvent.change(screen.getByLabelText('프로젝트 명'), { target: { value: '유지할 수정' } });
  fireEvent.click(screen.getByRole('button', { name: '이 내용으로 반영' }));
  await screen.findByRole('alert');
  view.unmount();
  const recovered = render(<CommandConfirmationForm actionId="a1" principalId="mina" contract={contract} commands={commands} onCommand={onCommand} />);
  expect((screen.getByLabelText('프로젝트 명') as HTMLInputElement).value).toBe('유지할 수정');
  recovered.unmount();
  render(<CommandConfirmationForm actionId="a1" principalId="jiho" contract={contract} commands={commands} onCommand={onCommand} />);
  expect((screen.getByLabelText('프로젝트 명') as HTMLInputElement).value).toBe('제안 원안');
});

it('requires defaulted non-null fields and omits unchanged edits while preserving explicit clears after recovery', async () => {
  const editContract: ActionEditContract = {
    editor: 'command', base_submission_version: 1,
    values: { meeting_id: 'bound', title: '원안', description: '삭제할 내용', kind: 'member' },
    fields: [
      { id: 'title', label: '회의 명', type: 'text', required: false, editable: true, empty_policy: 'omit' },
      { id: 'description', label: '내용', type: 'textarea', required: false, editable: true, empty_policy: 'null' },
      { id: 'kind', label: '역할', type: 'select', required: false, editable: true, empty_policy: 'forbid', options: [{ value: 'member', label: '참여' }] },
    ],
  };
  const onCommand = vi.fn().mockResolvedValue(false);
  const first = render(<CommandConfirmationForm actionId="empty" principalId="jiho" contract={editContract} commands={commands} onCommand={onCommand} />);
  expect((screen.getByLabelText('역할') as HTMLSelectElement).required).toBe(true);
  expect((screen.getByRole('option', { name: '선택해 주세요' }) as HTMLOptionElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('회의 명'), { target: { value: '' } });
  fireEvent.change(screen.getByLabelText('내용'), { target: { value: '' } });
  first.unmount();
  render(<CommandConfirmationForm actionId="empty" principalId="jiho" contract={editContract} commands={commands} onCommand={onCommand} />);
  expect((screen.getByLabelText('회의 명') as HTMLInputElement).value).toBe('');
  fireEvent.click(screen.getByRole('button', { name: '이 내용으로 반영' }));
  await waitFor(() => expect(onCommand).toHaveBeenCalledWith('confirm', { base_submission_version: 1, draft: { meeting_id: 'bound', description: null, kind: 'member' } }));
});

it('blocks an empty non-null title even when the command supplies a default', () => {
  const onCommand = vi.fn();
  const editContract: ActionEditContract = { editor: 'command', base_submission_version: 1, values: { title: '새 대화' }, fields: [{ id: 'title', label: '대화 제목', type: 'text', required: false, editable: true, empty_policy: 'forbid' }] };
  render(<CommandConfirmationForm actionId="title" principalId="jiho" contract={editContract} commands={commands} onCommand={onCommand} />);
  const input = screen.getByLabelText('대화 제목') as HTMLInputElement;
  fireEvent.change(input, { target: { value: '' } });
  expect(input.checkValidity()).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '이 내용으로 반영' }));
  expect(onCommand).not.toHaveBeenCalled();
});

it('preserves the explicit empty description that clears a request revision after recovery', async () => {
  const onCommand = vi.fn().mockResolvedValue(false);
  const editContract: ActionEditContract = { editor: 'command', base_submission_version: 1, values: { request_id: 'bound', description: '원래 설명' }, fields: [{ id: 'description', label: '설명', type: 'textarea', required: false, editable: true, empty_policy: 'empty_string' }] };
  const first = render(<CommandConfirmationForm actionId="revision" principalId="mina" contract={editContract} commands={commands} onCommand={onCommand} />);
  fireEvent.change(screen.getByLabelText('설명'), { target: { value: '' } });
  first.unmount();
  render(<CommandConfirmationForm actionId="revision" principalId="mina" contract={editContract} commands={commands} onCommand={onCommand} />);
  fireEvent.click(screen.getByRole('button', { name: '이 내용으로 반영' }));
  await waitFor(() => expect(onCommand).toHaveBeenCalledWith('confirm', { base_submission_version: 1, draft: { request_id: 'bound', description: '' } }));
});

it('edits the full checklist order using step names and submits the bound identifiers', async () => {
  const onCommand = vi.fn().mockResolvedValue(true);
  const editContract: ActionEditContract = { editor: 'command', base_submission_version: 1, values: { task_id: 'bound', item_ids: ['first', 'second'] }, fields: [{ id: 'item_ids', label: '단계 순서', type: 'ordered_select', required: true, editable: true, options: [{ value: 'first', label: '자료 모으기' }, { value: 'second', label: '검토하기' }] }] };
  render(<CommandConfirmationForm actionId="order" principalId="jiho" contract={editContract} commands={commands} onCommand={onCommand} />);
  fireEvent.click(screen.getByRole('button', { name: '검토하기 위로' }));
  expect(screen.getAllByRole('listitem').map(item => item.textContent)).toEqual(['검토하기위로아래로', '자료 모으기위로아래로']);
  fireEvent.click(screen.getByRole('button', { name: '이 내용으로 반영' }));
  await waitFor(() => expect(onCommand).toHaveBeenCalledWith('confirm', { base_submission_version: 1, draft: { task_id: 'bound', item_ids: ['second', 'first'] } }));
});

it('selects report occurrences by labels and keeps their complete references after recovery', async () => {
  const source = { task_id: 'task', task_version: 3, occurred_at: '2026-09-11T10:00:00Z', state: 'done', captured_note: '보존할 출처' };
  const onCommand = vi.fn().mockResolvedValue(true);
  const editContract: ActionEditContract = { editor: 'command', base_submission_version: 1, values: { report_id: 'bound', exclude_source_refs: [] }, fields: [{ id: 'exclude_source_refs', label: '제외할 근거', type: 'source_select', required: false, editable: true, options: [{ value: JSON.stringify(source), label: '검토한 업무 · 완료 기록' }] }] };
  const first = render(<CommandConfirmationForm actionId="report" principalId="mina" contract={editContract} commands={commands} onCommand={onCommand} />);
  fireEvent.click(screen.getByLabelText('검토한 업무 · 완료 기록'));
  first.unmount();
  render(<CommandConfirmationForm actionId="report" principalId="mina" contract={editContract} commands={commands} onCommand={onCommand} />);
  fireEvent.click(screen.getByRole('button', { name: '이 내용으로 반영' }));
  await waitFor(() => expect(onCommand).toHaveBeenCalledWith('confirm', { base_submission_version: 1, draft: { report_id: 'bound', exclude_source_refs: [source] } }));
});
