import { CalendarRail } from "../../frontend/src/shell/CalendarRail";
import { calendarRailTasks } from "../../frontend/src/shell/fixtures/calendarRail";
import { seoulToday } from "../../frontend/src/lib/labels";

export default { title: "Work/CalendarRail", component: CalendarRail };

const noop = () => {};
function Preview({ state = "ready", empty = false }: { state?: "ready" | "loading" | "error"; empty?: boolean }) {
  return (
    <div style={{ width: 280, height: 720 }}>
      <CalendarRail state={state} tasks={empty ? [] : calendarRailTasks(seoulToday())} onOpen={noop} onRetry={noop} />
    </div>
  );
}

/** 오늘·주간·월간을 전환해 긴 제목/막힘/일정 dot을 확인한다. */
export const Populated = () => <Preview />;
/** 일정이 없어도 주간·월간의 오늘 dot은 유지된다. */
export const Empty = () => <Preview empty />;
export const Loading = () => <Preview state="loading" />;
export const Error = () => <Preview state="error" />;
