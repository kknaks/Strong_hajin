import { useEffect, useState } from "react";

import {
  decideAction,
  decideWorkRequest,
  getActionInbox,
  getActions,
  negotiateWorkRequest,
} from "./api";
import type { ActionItem, WorkRequest } from "./viewModels";

type ActionInboxPageProps = {
  personaId: string;
  canDecideWorkRequests: boolean;
  onError: (message: string | null) => void;
};

export function ActionInboxPage({
  personaId,
  canDecideWorkRequests,
  onError,
}: ActionInboxPageProps) {
  const [requests, setRequests] = useState<WorkRequest[]>([]);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [isWorking, setIsWorking] = useState(false);
  const [negotiatingRequestId, setNegotiatingRequestId] = useState<string | null>(null);
  const [conditions, setConditions] = useState("");

  async function refresh() {
    try {
      const [nextRequests, nextActions] = await Promise.all([
        canDecideWorkRequests ? getActionInbox(personaId) : Promise.resolve([]),
        getActions(personaId),
      ]);
      setRequests(nextRequests);
      setActions(nextActions);
    } catch (error) {
      onError(error instanceof Error ? error.message : "판단함을 불러오지 못했습니다.");
    }
  }

  useEffect(() => {
    void refresh();
  }, [canDecideWorkRequests, personaId]);

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

  async function negotiate(request: WorkRequest) {
    const note = conditions.trim();
    if (!note) {
      onError("협의 조건을 입력해 주세요.");
      return;
    }

    setIsWorking(true);
    onError(null);
    try {
      await negotiateWorkRequest(personaId, request.request_id, request.version, { note });
      setConditions("");
      setNegotiatingRequestId(null);
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : "협의 요청을 처리하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  async function decideActionItem(action: ActionItem, decision: "approve" | "reject") {
    setIsWorking(true);
    onError(null);
    try {
      await decideAction(personaId, action.action_id, action.version, decision);
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : "확인 항목을 처리하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  return (
    <section className="page-surface">
      <p className="kicker">ACTIONS</p>
      <h2>판단</h2>
      <p>확인이 필요한 업무 요청과 AX가 제안한 변경을 검토합니다.</p>

      <section className="surface-card decision-empty-state">
        <h3>확인이 필요한 변경</h3>
        {actions.filter((action) => action.state === "pending").length === 0 ? (
          <p className="empty-row">현재 확인할 변경이 없습니다.</p>
        ) : (
          <ul className="evidence-list">
            {actions
              .filter((action) => action.state === "pending")
              .map((action) => (
                <li key={action.action_id}>
                  <div>
                    <b>{action.title}</b>
                    <span>{action.payload_summary}</span>
                    <small>AX 제안 · 버전 {action.version}</small>
                  </div>
                  <div>
                    <button
                      disabled={isWorking}
                      onClick={() => void decideActionItem(action, "approve")}
                      type="button"
                    >
                      승인
                    </button>
                    <button
                      disabled={isWorking}
                      onClick={() => void decideActionItem(action, "reject")}
                      type="button"
                    >
                      거절
                    </button>
                  </div>
                </li>
              ))}
          </ul>
        )}
      </section>

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
                  {request.conditions && <small>협의 조건: {JSON.stringify(request.conditions)}</small>}
                </div>
                <div>
                  <button disabled={isWorking} onClick={() => void decide(request, "accept")} type="button">
                    수락
                  </button>
                  <button disabled={isWorking} onClick={() => void decide(request, "reject")} type="button">
                    거절
                  </button>
                  <button
                    disabled={isWorking}
                    onClick={() => setNegotiatingRequestId(request.request_id)}
                    type="button"
                  >
                    협의
                  </button>
                </div>
                {negotiatingRequestId === request.request_id && (
                  <div className="request-negotiation">
                    <label htmlFor={`conditions-${request.request_id}`}>협의 조건</label>
                    <input
                      id={`conditions-${request.request_id}`}
                      onChange={(event) => setConditions(event.target.value)}
                      placeholder="예: 9월 5일까지 완료 가능"
                      value={conditions}
                    />
                    <button disabled={isWorking} onClick={() => void negotiate(request)} type="button">
                      조건 보내기
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
