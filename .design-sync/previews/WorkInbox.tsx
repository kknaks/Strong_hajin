import { InboxRail } from "../../frontend/src/shell/InboxRail";
import { requestInboxFixture } from "../../frontend/src/shell/fixtures/requestInbox";

export default { title: "Work/InboxRail", component: InboxRail };

const noop = () => {};
function Preview({ state = "ready", empty = false }: { state?: "ready" | "loading" | "error"; empty?: boolean }) {
  return <div style={{ width: 380, height: 720 }}><InboxRail items={empty ? [] : requestInboxFixture} state={state} personas={[{ id: "colleague", display_name: "동료" }]} canDecide onAccept={noop} onReject={noop} onOpen={noop} onRetry={noop} /></div>;
}

export const Populated = () => <Preview />;
export const Empty = () => <Preview empty />;
export const Loading = () => <Preview state="loading" />;
export const Error = () => <Preview state="error" />;
