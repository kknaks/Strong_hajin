import { useState } from "react";

import { Badge } from "../../ds/Badge";
import { Button, IconButton } from "../../ds/Button";
import { Icon } from "../../ds/icons/Icon";
import { Modal } from "../../ds/Modal";
import { formatDate, personName, projectScreen } from "../../lib/labels";
import type { Persona, ProjectDetail, ProjectParticipation } from "../../lib/viewModels";

/**
 * 프로젝트 관리 — 참여자 붙이기/떼기 · 참여 이력 · 새 프로젝트 (WORK-005 FE-3).
 *
 * ⚠ **시안에 이 모달이 없다** (M-1). 시안의 본문은 관리 기능을 하나도 그리지 않고, 우리 화면의 그 기능은
 * 없앨 것이 아니라 **자리를 옮기는 것**이다. 그래서 **새로 발명하지 않고 어휘만 빌렸다** —
 * 시안 `handoff/shell/js/work-modal.jsx` 의 `Modal`(`scax-modal*`) · `Field`(`scax-field*`) ·
 * `TextField`(`scax-textfield*`) · `AutoComplete`(`scax-autocomplete*`). **필드가 늘지도 줄지도 않았다:**
 * 옮기기 전 `ProjectPage` 가 들고 있던 것과 같은 다섯이다(이름 · 붙일 구성원 · 참여/담당 · 종료 사유).
 *
 * 구성원 고르기는 옮기기 전에 `ds/Select` 였는데 시안 어휘의 그 자리는 `AutoComplete` 다.
 * **`ds/Select` 를 고치지 않고** 화면 안에서 조립한다 — CSS `.scax-autocomplete*` 는 저장소에 이미 있다.
 *
 * **`may_manage` 가 거짓이면 이 모달을 여는 손잡이가 렌더되지 않는다** — 화면이 권한을 추측하지 않고,
 * 눌러야 아는 거절도 만들지 않는다. 그 판정은 `ProjectPage` 한 자리에서만 일어난다.
 */
export function ProjectManageModal({
  project,
  history,
  directory,
  busy,
  onClose,
  onCreate,
  onJoin,
  onRelease,
}: {
  project: ProjectDetail;
  history: ProjectParticipation[] | null;
  directory: Persona[];
  busy: boolean;
  onClose: () => void;
  onCreate: (name: string) => void;
  onJoin: (memberId: string, kind: "lead" | "member") => void;
  onRelease: (memberId: string, assignmentId: string | undefined, reason: string) => void;
}) {
  const [name, setName] = useState("");
  const [joining, setJoining] = useState("");
  const [releasing, setReleasing] = useState<{ assignmentId?: string; memberId: string; displayName: string } | null>(null);
  const [releaseReason, setReleaseReason] = useState("");

  const joined = new Set(project.members.map((row) => row.member_id));
  const candidates = directory.filter((person) => !joined.has(person.id));

  return (
    <Modal
      closeLabel={projectScreen.manageClose}
      footer={
        <Button onClick={onClose} size="sm" type="button" variant="outlined">
          {projectScreen.manageDone}
        </Button>
      }
      label={projectScreen.manage}
      onClose={onClose}
      size="md"
      title={projectScreen.manage}
    >
      <section aria-label={projectScreen.members} className="scax-pj-manage__block">
        <h4 className="scax-pj-manage__title">
          {projectScreen.members}
          <span className="scax-pj-side__count">{project.members.length}</span>
          <small className="scax-pj-manage__hint">{projectScreen.membersHint}</small>
        </h4>
        <ul className="scax-pj-manage__list">
          {project.members.map((row) => (
            <li data-member-id={row.member_id} key={row.member_id}>
              <Badge tone={row.assignment_kind === "lead" ? "accent" : "neutral"}>
                {row.assignment_kind === "lead" ? projectScreen.memberLead : projectScreen.memberMember}
              </Badge>
              <span className="scax-pj-manage__name">{personName(row.display_name)}</span>
              {row.valid_until && <span className="t-meta">~ {formatDate(row.valid_until.slice(0, 10))}</span>}
              <Button
                ariaLabel={projectScreen.release(personName(row.display_name))}
                disabled={busy}
                onClick={() => {
                  setReleasing({
                    assignmentId: row.assignment_id,
                    memberId: row.member_id,
                    displayName: personName(row.display_name),
                  });
                  setReleaseReason("");
                }}
                size="sm"
                type="button"
                variant="text"
              >
                {projectScreen.release(personName(row.display_name))}
              </Button>
            </li>
          ))}
          {project.members.length === 0 && <li className="t-meta">{projectScreen.membersNone}</li>}
        </ul>

        {releasing && (
          <div className="scax-field">
            <label className="scax-field__label" htmlFor="project-release-reason">
              {projectScreen.releaseReason}
            </label>
            <div className="scax-textfield">
              <input
                className="scax-textfield__input"
                id="project-release-reason"
                maxLength={4000}
                onChange={(event) => setReleaseReason(event.target.value)}
                placeholder={projectScreen.releaseReasonPlaceholder(releasing.displayName)}
                value={releaseReason}
              />
            </div>
            <div className="scax-pj-manage__row">
              <Button
                disabled={busy}
                onClick={() => {
                  onRelease(releasing.memberId, releasing.assignmentId, releaseReason);
                  setReleasing(null);
                  setReleaseReason("");
                }}
                size="sm"
                type="button"
              >
                {projectScreen.releaseConfirm}
              </Button>
              <Button
                disabled={busy}
                onClick={() => {
                  setReleasing(null);
                  setReleaseReason("");
                }}
                size="sm"
                type="button"
                variant="text"
              >
                {projectScreen.releaseCancel}
              </Button>
            </div>
          </div>
        )}

        <div className="scax-field">
          <span className="scax-field__label" id="project-join-label">
            {projectScreen.joinField}
          </span>
          <MemberAutoComplete
            label={projectScreen.joinField}
            onPick={setJoining}
            options={candidates}
            value={joining}
          />
          <div className="scax-pj-manage__row">
            <Button disabled={busy || !joining} onClick={() => onJoin(joining, "member")} size="sm" type="button">
              {projectScreen.joinAsMember}
            </Button>
            <Button disabled={busy || !joining} onClick={() => onJoin(joining, "lead")} size="sm" type="button">
              {projectScreen.joinAsLead}
            </Button>
          </div>
        </div>
      </section>

      <section aria-label={projectScreen.historyTitle} className="scax-pj-manage__block">
        <h4 className="scax-pj-manage__title">
          {projectScreen.historyTitle}
          {history !== null && <span className="scax-pj-side__count">{history.length}</span>}
        </h4>
        <ul className="scax-pj-manage__list">
          {(history ?? []).map((row) => (
            <li data-assignment-id={row.assignment_id} key={row.assignment_id}>
              <Badge tone={row.assignment_kind === "lead" ? "accent" : "neutral"}>
                {row.assignment_kind === "lead" ? projectScreen.memberLead : projectScreen.memberMember}
              </Badge>
              <span className="scax-pj-manage__name">{personName(row.display_name)}</span>
              <span className="t-meta">{row.ended_at ? projectScreen.historyEnded : projectScreen.historyActive}</span>
              {row.ended_by_display_name && (
                <span className="t-meta">{projectScreen.historyEndedBy(personName(row.ended_by_display_name))}</span>
              )}
              {row.end_reason && <span>{row.end_reason}</span>}
            </li>
          ))}
          {history !== null && history.length === 0 && <li className="t-meta">{projectScreen.historyNone}</li>}
        </ul>
      </section>

      <section aria-label={projectScreen.newProject} className="scax-pj-manage__block">
        <h4 className="scax-pj-manage__title">{projectScreen.newProject}</h4>
        <div className="scax-field">
          <label className="scax-field__label" htmlFor="project-name">
            {projectScreen.newProjectName}
          </label>
          <div className="scax-textfield">
            <input
              className="scax-textfield__input"
              id="project-name"
              onChange={(event) => setName(event.target.value)}
              value={name}
            />
          </div>
          <div className="scax-pj-manage__row">
            <Button
              disabled={busy || !name.trim()}
              onClick={() => {
                onCreate(name.trim());
                setName("");
              }}
              size="sm"
              tone="primary"
              type="button"
              variant="solid"
            >
              {projectScreen.newProjectOpen}
            </Button>
          </div>
        </div>
      </section>
    </Modal>
  );
}

/**
 * 이름으로 찾는 콤보박스 — 시안 `work-modal.jsx:83-116` 의 `AutoComplete` 어휘 그대로다.
 *
 * **`ds/Select` 를 고치지 않는다** — 그 부품은 12자리가 함께 쓴다. 여기서 필요한 것은 폼 필드꼴
 * 트리거가 아니라 **글자로 좁히는 목록** 하나뿐이라, 이 화면 안에서 조립한다.
 * 고른 값은 **구성원 id** 이고 입력창에 보이는 것은 이름이다 — 붙이는 명령이 id 를 받기 때문이다.
 */
function MemberAutoComplete({
  value,
  onPick,
  options,
  label,
}: {
  /** 고른 구성원 id. 빈 문자열이면 아직 아무도 안 골랐다. */
  value: string;
  onPick: (memberId: string) => void;
  options: Persona[];
  label: string;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const picked = options.find((person) => person.id === value) ?? null;
  const text = picked ? personName(picked.display_name) : query;
  const matches = query
    ? options.filter((person) => personName(person.display_name).includes(query))
    : options;
  return (
    <div className="scax-autocomplete">
      <div className="scax-textfield scax-textfield--search">
        <input
          aria-expanded={open}
          aria-label={label}
          className="scax-textfield__input"
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onChange={(event) => {
            setQuery(event.target.value);
            onPick("");
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder={projectScreen.joinSearch}
          role="combobox"
          value={text}
        />
        {picked || query ? (
          <IconButton
            label={projectScreen.releaseCancel}
            name="close"
            onClick={() => {
              setQuery("");
              onPick("");
            }}
            size={16}
          />
        ) : null}
      </div>
      {open && matches.length > 0 && (
        <ul className="scax-autocomplete__list" role="listbox">
          {matches.map((person) => (
            <li key={person.id}>
              <button
                aria-selected={person.id === value}
                className={`scax-autocomplete__option${person.id === value ? " scax-autocomplete__option--selected" : ""}`}
                onMouseDown={() => {
                  onPick(person.id);
                  setQuery("");
                  setOpen(false);
                }}
                role="option"
                type="button"
              >
                {personName(person.display_name)}
                {person.id === value ? <Icon name="check" size={16} /> : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
