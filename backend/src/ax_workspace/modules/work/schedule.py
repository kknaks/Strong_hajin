"""업무의 **기간을 읽는 법**과 그 기간 밖 판정 — 시간 배정이 서는 자리 (SPEC-004 §4 Validation).

이 파일은 **순수**하다. 저장소도 원장도 HTTP 도 모르고, 날짜 둘과 시각 둘만 받아 답한다.
답하는 것은 셋이다.

1. **기간을 읽는 법 — 네 경우** (DEC-003 증보 K7·K11). 업무의 `start_date`·`due_date` 는
   둘 다 있을 수도, 한쪽만 있을 수도, 둘 다 없을 수도, **뒤집혀 있을 수도** 있다.
   뒤집힌 기간은 가정이 아니라 **실재한다** — 시작 전이(`open → in_progress`)와 조건 변경 제안 동의가
   `validate_schedule` 을 지나지 않고 날짜를 넣기 때문이다. 그래서 「빈 구간」이 아니라
   **`[min, max]` 로 정규화**해서 읽는다.
2. **그 결과가 곧 응답의 `span_from`·`span_to`** 다 (증보 K14). 화면이 다시 계산하지 않는다 —
   서버가 적용한 결과 하나만 본다.
3. **닫기 사유는 둘뿐이다** (DEC-003 §J). 기간이 통째로 사라졌으면 `task_dates_cleared`,
   기간은 있는데 그 밖이면 `out_of_range`. **셋째가 생기는 경로가 없다** — 같은 날 재배정은
   같은 행을 갱신하고(K1·K10), 업무 종료는 쓰기가 아니라 읽기 필터다(§B).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time


#: 닫기 사유 둘. 배정의 `released_reason` 에 그대로 들어간다 — **외부에는 드러나지 않는다**(§4 Data).
RELEASE_OUT_OF_RANGE = "out_of_range"
RELEASE_TASK_DATES_CLEARED = "task_dates_cleared"


@dataclass(frozen=True, slots=True)
class TaskSpan:
    """배정을 놓을 수 있는 **정규화된 구간**. 양 끝을 포함한다."""

    span_from: date
    span_to: date

    def covers(self, on_date: date) -> bool:
        return self.span_from <= on_date <= self.span_to


def task_span(start_date: date | None, due_date: date | None) -> TaskSpan | None:
    """업무의 기간 — **네 경우**를 한 함수가 답한다 (증보 K7·K11).

    | 경우 | 구간 |
    |---|---|
    | 둘 다 있고 순서가 맞음 | `[start_date, due_date]` |
    | 한쪽만 있음 | 남은 한쪽의 **그 날 하루** |
    | 둘 다 없음 | `None` — 기간이 없다. 배정을 만들 수 없다 |
    | `start_date > due_date` | **`[min, max]`** — 빈 구간으로 보지 않는다 |

    한쪽만 있을 때 「그 날 하루」로 읽는 것은 프론트가 이미 쓰는 규칙과 같다 — **FE·BE 가 한 규칙**이다.
    """
    if start_date is None and due_date is None:
        return None
    if start_date is None:
        return TaskSpan(due_date, due_date)
    if due_date is None:
        return TaskSpan(start_date, start_date)
    if start_date > due_date:
        return TaskSpan(due_date, start_date)
    return TaskSpan(start_date, due_date)


def is_within_span(span: TaskSpan | None, on_date: date) -> bool:
    """기간이 없으면 **어떤 날도 안 된다** — 「제한이 없다」가 아니다 (원문 R2 · §E)."""
    return span is not None and span.covers(on_date)


def release_reason_for(span: TaskSpan | None) -> str:
    """새 기간을 기준으로 닫는 배정에 붙일 사유. **기간이 통째로 사라진 것만 다른 이름을 갖는다.**

    한쪽만 지운 경우는 남은 한쪽을 그 날 하루로 읽으므로 **여전히 기간이 있고**, 그 하루 밖의 배정은
    `out_of_range` 다 (증보 K7). 그래서 셋째 사유가 생기지 않는다.
    """
    return RELEASE_TASK_DATES_CLEARED if span is None else RELEASE_OUT_OF_RANGE


def is_valid_time_range(starts_at: time, ends_at: time) -> bool:
    """`starts_at < ends_at`. **자정을 넘길 수 없다** — 저장이 `Date` + `Time` + `Time` 이라 표현되지 않는다.

    최소 길이도 30분 눈금도 여기서 강제하지 않는다 (§4 Validation · DEC-003 §J).
    """
    return starts_at < ends_at
