import { useEffect, useState } from "react";

import { decideWorkRequest, getActionInbox } from "./api";
import type { WorkRequest } from "./viewModels";

type ActionInboxPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

export function ActionInboxPage({ personaId, onError }: ActionInboxPageProps) {
  const [requests, setRequests] = useState<WorkRequest[]>([]);
  const [isWorking, setIsWorking] = useState(false);

  async function refresh() {
    try {
      setRequests(await getActionInbox(personaId));
    } catch (error) {
      onError(error instanceof Error ? error.message : "판단함을 불러오지 못했습니다.");
    }
  }

  useEffect(() => {
    void refresh();
  }, [personaId]);

  async function decide(request: WorkRequest, action: "accept" | "reject") {
    setIsWorking(true);
    onError(null);
    try {
      await decideWorkRequest(
        personaId,
        request.request_id,
        action,
        request.version,
        action === "reject" ? "현재 업무 우선순위와 맞지 않습니다." : undefined,
      );
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : "요청을 처리하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  return (
    <section className="page-surface">
      <p className="kicker">ACTIONS</p>
      <h2>판단</h2>
      <p>내게 온 업무 요청을 수락하거나 거절합니다. 수락하기 전에는 내 업무에 생성되지 않습니다.</p>

      <section className="surface-card decision-empty-state">
        <h3>확인이 필요한 요청</h3>
        {requests.length === 0 ? (
          <p className="empty-row">현재 판단할 업무 요청이 없습니다.</p>
        ) : (
          <ul className="evidence-list">
            {requests.map((request) => (
              <li key={request.request_id}>
                <div>
                  <b>{request.title}</b>
                  <span>{request.state}</span>
                </div>
                <div>
                  <button disabled={isWorking} onClick={() => void decide(request, "accept")} type="button">
                    수락
                  </button>
                  <button disabled={isWorking} onClick={() => void decide(request, "reject")} type="button">
                    거절
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
