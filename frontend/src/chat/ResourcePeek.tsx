import { useEffect, useState } from "react";

import { getMeeting, getTask, getTaskMaterials, getWorkRequestTimeline } from "../api";
import { formatDate, taskStateLabel, workRequestStateLabel } from "../labels";
import { Skeleton } from "../Skeleton";
import type { AnswerResource } from "../viewModels";

/**
 * 근거를 대화 안에서 열어 본다.
 *
 * 답을 읽다가 근거 하나를 확인하려고 화면을 떠나면 돌아왔을 때 스크롤도, 쓰다 만 문장도, 접어 둔 실행 영수증도
 * 그대로가 아니다. 그래서 여기서는 대화를 떠나지 않고 그 자리에서 정본을 다시 읽어 보여 준다.
 *
 * 이것은 요약이 아니라 지금 그 원장이 답하는 것이다 — 이 사람의 권한으로 다시 물어보고, 열 수 없게 되었으면
 * 열 수 없다고 말한다. 깊은 이력과 편집은 이 자리의 일이 아니므로 정식 화면으로 가는 길을 따로 둔다.
 */
export function ResourcePeek({
  resource,
  onClose,
  onOpenFully,
}: {
  resource: AnswerResource;
  onClose: () => void;
  onOpenFully: (resource: AnswerResource) => void;
}) {
  const [rows, setRows] = useState<Array<[string, string]> | null>(null);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setGone(false);
    void read(resource)
      .then((found) => {
        if (cancelled) return;
        if (found === null) setGone(true);
        else setRows(found);
      })
      .catch(() => !cancelled && setGone(true));
    return () => {
      cancelled = true;
    };
  }, [resource]);

  return (
    <aside aria-label="근거 상세" className="ax-peek" data-resource={`${resource.resource_type}:${resource.resource_id}`}>
      <header>
        <b>{resource.title}</b>
        <button aria-label="근거 상세 닫기" className="btn link" onClick={onClose} type="button">
          닫기
        </button>
      </header>
      {resource.changed_since && (
        <p className="ax-peek-note">답변 뒤에 바뀌었습니다. 아래는 지금의 내용입니다.</p>
      )}
      {gone ? (
        // 사라졌는지 권한을 잃었는지 구별해 말하지 않는다. 구별하면 그것이 곧 존재를 알리는 말이 된다.
        <p className="ax-peek-note">지금은 열 수 없습니다.</p>
      ) : rows === null ? (
        <Skeleton label="자료를 불러오는 중" rows={2} />
      ) : (
        <dl className="ax-peek-list">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <button className="btn h30" onClick={() => onOpenFully(resource)} type="button">
        정식 화면에서 열기
      </button>
    </aside>
  );
}

/** 정본을 지금 다시 읽는다. 열 수 없으면 `null`이고, 그것이 곧 이 자리에서 할 수 있는 전부다. */
async function read(resource: AnswerResource): Promise<Array<[string, string]> | null> {
  try {
    if (resource.resource_type === "task") {
      const task = await getTask(resource.resource_id);
      return [
        ["상태", taskStateLabel[task.state as keyof typeof taskStateLabel] ?? task.state],
        ["담당", task.assignee?.display_name ?? "미정"],
        ["기한", task.due_date ? formatDate(task.due_date) : "없음"],
        ...(task.description ? ([["내용", task.description]] as Array<[string, string]>) : []),
      ];
    }
    if (resource.resource_type === "meeting") {
      const meeting = await getMeeting(resource.resource_id);
      return [
        ["시작", formatDate(meeting.starts_at.slice(0, 10))],
        ["공개", meeting.visibility === "private" ? "비공개" : "조직 공개"],
        ["참석", `${(meeting.attendees ?? []).length}명`],
      ];
    }
    if (resource.resource_type === "work_request") {
      const timeline = await getWorkRequestTimeline(resource.resource_id);
      const request = timeline.request;
      return [
        ["상태", workRequestStateLabel[request.state as keyof typeof workRequestStateLabel] ?? request.state],
        ["보낸 사람", request.requester_id ?? "알 수 없음"],
        ["기한", request.due_date ? formatDate(request.due_date) : "없음"],
      ];
    }
    if (resource.resource_type === "material" && resource.parent_resource_id) {
      const materials = await getTaskMaterials(resource.parent_resource_id);
      const found = materials.find((row) => row.material_id === resource.resource_id);
      if (!found) return null;
      const where = resource.source_locator?.page ? `${resource.source_locator.page}쪽` : "";
      return [
        ["종류", found.kind === "input" ? "참고 자료" : "산출물"],
        ...(where ? ([["읽은 자리", where]] as Array<[string, string]>) : []),
        ["올린 때", formatDate(found.created_at.slice(0, 10))],
      ];
    }
  } catch {
    return null;
  }
  return null;
}
