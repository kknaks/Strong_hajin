"""겹침을 **읽는 문 하나** — 두 표를 조회한다 (SPEC-004 §2.9 · DEC-003 증보 8 K22).

**표는 둘이어도 문은 하나다.** `task_schedules` 와 `meetings` 는 각자 사는 표이고
(통합 시간 표로 가지 않았다 — **회의 시각의 정본이 둘이 되는 것이 겹침 경합보다 나쁘다**),
그래서 「이 사람의 그 시간이 비었나」를 묻는 자리가 **여기 하나**다. 배정 쪽과 회의 쪽이 각자
조회를 쓰면 **두 규칙이 된다** — 반열림 판정도 자정 분할도 이 문 안에서 한 번만 일어난다
(`modules/time_blocks.py` 가 그 규칙을 갖는다).

**제약으로 올릴 수 없다.** 겹침은 두 표에 걸쳐 있어 `EXCLUDE USING gist` 같은 것을 걸 자리가 없다 —
「하루 한 칸」은 부분 unique 가 **데이터베이스에서** 답하지만 겹침은 **application 이** 답한다.
**검사와 저장을 한 트랜잭션에 두지만 틈이 남는다**: 동시 요청 둘이 각각 검사를 통과해 둘 다 설 수
있다. **이 차이를 감추지 않는다** (SPEC §2.9 한계 표).

**블록이 되는 것 — 그 사람의 캘린더에 실제로 서는 것만.** K22 가 「참석 회의는 이미 그 사람
캘린더에 선다」를 참석자를 막는 근거로 들었으므로, 근거와 구현이 같은 집합을 본다.

| 표 | 블록이 되는 행 | 왜 |
|---|---|---|
| `task_schedules` | 살아 있고(`released_at IS NULL`), **활성 담당이 그 사람**이며, 업무가 `DONE`·`CANCELLED` 가 **아닌** 것 | 닫힌 배정과 끝난 업무는 합본 조회에도 없다 (§2.5) |
| `meetings` | **주최자이거나 활성 참석자**이고 `cancelled` 가 **아닌** 것 | 참석 관계가 곧 「그 사람의 시간」이다 (§D `board` 축) |

- **조직 범위로 보이는 남의 회의·공유받은 회의는 블록이 아니다.** `meetings_visible_to` 는 열람을
  묻는 자리라 그 둘을 함께 내지만, 여기서 묻는 것은 **그 사람의 시간이 찼는가**다 — 옆 팀 회의가
  보인다는 이유로 내 배정이 막히면 안 된다.
- **취소된 회의는 블록이 아니다.** 자동 취소 판정(`_settle_auto_cancel`)이 기록 없이 지난 「예정」을
  `cancelled` 로 옮기므로, 세면 **되살릴 수 없는 이유로 그 시간이 영구히 막힌다.**
- **제목·내용을 읽지 않는다** (K22). `TimeBlock` 이 싣는 것은 busy 라는 사실과 출처 식별자까지다.
"""

from __future__ import annotations

from datetime import UTC
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ax_workspace.modules.meetings.domain import MeetingStatus
from ax_workspace.modules.time_blocks import (
    MEETING_BLOCK,
    TASK_SCHEDULE_BLOCK,
    TimeBlock,
    TimeWindow,
    aware,
    office_dates_in,
    office_span,
    overlaps,
    split_across_office_dates,
)
from ax_workspace.modules.work.lifecycle import TaskState
from ax_workspace.platform.persistence import (
    MeetingAttendeeRecord,
    MeetingRecord,
    TaskAssignmentRecord,
    TaskRecord,
    TaskScheduleRecord,
)


_CLOSED_TASK_STATES = (TaskState.DONE, TaskState.CANCELLED)


class SqlAlchemyTimeBlockRepository:
    """`overlapping_blocks(member_ids, 구간)` 한 문. 이 클래스에 다른 공개 메서드를 더하지 않는다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def overlapping_blocks(
        self,
        member_ids: frozenset[str],
        window: TimeWindow,
        *,
        ignore_schedule_id: UUID | None = None,
        ignore_meeting_id: UUID | None = None,
    ) -> list[TimeBlock]:
        """그 사람들의 시간 중 **그 구간과 겹치는 칸 전부** — 반열림 `[시작, 끝)` 이다.

        `ignore_schedule_id`·`ignore_meeting_id` 는 **고치는 중인 자기 자신**을 뺀다. 없으면
        10:00–11:00 을 10:30–11:30 으로 옮기는 것이 **자기와 겹쳐** 거절된다.

        **`member_ids` 는 처음부터 복수다** — 배정 쪽은 「나의 시간만」이라 하나를 넣지만, 회의 쪽은
        **주최자 + 참석자 전원**을 묻는다 (Phase BE-4). 문이 갈리지 않게 여기서 이미 복수를 받는다.
        """
        if not member_ids:
            return []
        blocks = [
            *self._schedule_blocks(member_ids, window, ignore_schedule_id=ignore_schedule_id),
            *self._meeting_blocks(member_ids, window, ignore_meeting_id=ignore_meeting_id),
        ]
        blocks.sort(key=lambda block: (block.starts_at, block.source, block.source_id, block.member_id))
        return blocks

    # ---- 표 하나: 시간 배정 -------------------------------------------------------

    def _schedule_blocks(
        self, member_ids: frozenset[str], window: TimeWindow, *, ignore_schedule_id: UUID | None
    ) -> list[TimeBlock]:
        """`Date` + `Time` 로 사는 칸. **자정을 넘지 않으므로 쪼갤 것이 없다.**

        날짜로 먼저 좁힌다 — 이 표에는 순간 열이 없다. 그 뒤 **같은 반열림 판정**으로 가른다.
        """
        first_date, last_date = office_dates_in(window)
        statement = (
            select(TaskScheduleRecord, TaskAssignmentRecord.assignee_id)
            .join(TaskAssignmentRecord, TaskAssignmentRecord.task_id == TaskScheduleRecord.task_id)
            .join(TaskRecord, TaskRecord.id == TaskScheduleRecord.task_id)
            .where(
                TaskScheduleRecord.released_at.is_(None),
                TaskScheduleRecord.on_date >= first_date,
                TaskScheduleRecord.on_date <= last_date,
                TaskAssignmentRecord.status == "active",
                TaskAssignmentRecord.assignee_id.in_(member_ids),
                TaskRecord.state.not_in(_CLOSED_TASK_STATES),
            )
        )
        if ignore_schedule_id is not None:
            statement = statement.where(TaskScheduleRecord.id != ignore_schedule_id)
        blocks: list[TimeBlock] = []
        for schedule, assignee_id in self.session.execute(statement).all():
            span = office_span(schedule.on_date, schedule.starts_at, schedule.ends_at)
            if not overlaps(span, window):
                continue
            blocks.append(
                TimeBlock(
                    member_id=assignee_id,
                    on_date=schedule.on_date,
                    starts_at=span[0],
                    ends_at=span[1],
                    source=TASK_SCHEDULE_BLOCK,
                    source_id=str(schedule.id),
                )
            )
        return blocks

    # ---- 표 둘: 회의 ---------------------------------------------------------------

    def _meeting_blocks(
        self, member_ids: frozenset[str], window: TimeWindow, *, ignore_meeting_id: UUID | None
    ) -> list[TimeBlock]:
        """`timestamptz` 로 사는 칸. **자정을 넘을 수 있으므로 날짜별로 쪼갠다** (K22).

        한 회의에 `member_ids` 의 여러 사람이 걸려 있으면 **사람마다 한 칸**이 된다 — 거절 문구가
        「누구의 일정과」를 말할 수 있어야 하기 때문이다 (BE-4). 제목은 읽지 않는다.
        """
        # **UTC 로 묶어 넘긴다.** `timestamptz` 열이라 PostgreSQL 은 순간으로 비교하지만, SQLite 의
        # `DATETIME` bind 는 **tzinfo 를 버리고 벽시계만** 넘긴다 — 사무실 시각을 그대로 실으면
        # 저장된 UTC 값과 아홉 시간 어긋난 문자열 비교가 된다. 두 엔진에서 같은 답이 나오게 맞춘다.
        window_from = aware(window[0]).astimezone(UTC)
        window_to = aware(window[1]).astimezone(UTC)
        attends = select(MeetingAttendeeRecord.meeting_id).where(
            MeetingAttendeeRecord.member_id.in_(member_ids),
            MeetingAttendeeRecord.removed_at.is_(None),
        )
        statement = select(MeetingRecord).where(
            or_(MeetingRecord.owner_id.in_(member_ids), MeetingRecord.id.in_(attends)),
            MeetingRecord.status != MeetingStatus.CANCELLED.value,
            MeetingRecord.starts_at < window_to,
            MeetingRecord.ends_at > window_from,
        )
        if ignore_meeting_id is not None:
            statement = statement.where(MeetingRecord.id != ignore_meeting_id)
        meetings = list(self.session.scalars(statement))
        attendees = self._attendees_of(meetings, member_ids)
        blocks: list[TimeBlock] = []
        for meeting in meetings:
            held_by = sorted(({meeting.owner_id} & member_ids) | attendees.get(meeting.id, set()))
            if not held_by:
                continue
            for segment in split_across_office_dates(meeting.starts_at, meeting.ends_at, within=window):
                for member_id in held_by:
                    blocks.append(
                        TimeBlock(
                            member_id=member_id,
                            on_date=segment.on_date,
                            starts_at=segment.starts_at,
                            ends_at=segment.ends_at,
                            source=MEETING_BLOCK,
                            source_id=str(meeting.id),
                        )
                    )
        return blocks

    def _attendees_of(
        self, meetings: list[MeetingRecord], member_ids: frozenset[str]
    ) -> dict[UUID, set[str]]:
        """그 회의들이 **누구의 시간을 차지하는가** — 활성 참석 관계를 **한 질의로** 읽어 나눈다.

        회의마다 따로 물으면 **회의 수 × 1 질의**가 되고, 그것이 회의 생성·시각 변경마다 돈다.
        `member_ids` 가 하나였을 때(배정 쪽)는 무해했지만 **주최자 + 참석자 전원으로 넓히면 아프다.**

        **공유는 참석이 아니다** (증보 K25) — 공유받은 회의는 「참고하라」고 온 것이지 내가 그 시간에
        잡혀 있다는 뜻이 아니므로 여기서 읽지 않는다.
        """
        if not meetings:
            return {}
        rows = self.session.execute(
            select(MeetingAttendeeRecord.meeting_id, MeetingAttendeeRecord.member_id).where(
                MeetingAttendeeRecord.meeting_id.in_([meeting.id for meeting in meetings]),
                MeetingAttendeeRecord.removed_at.is_(None),
                MeetingAttendeeRecord.member_id.in_(member_ids),
            )
        ).all()
        grouped: dict[UUID, set[str]] = {}
        for meeting_id, member_id in rows:
            grouped.setdefault(meeting_id, set()).add(member_id)
        return grouped
