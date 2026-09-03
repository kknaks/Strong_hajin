import { useCallback, useEffect, useState } from "react";

import { decideAction, decideWorkRequest, getActionInbox, getActions, negotiateWorkRequest } from "./api";
import { workRequestStateLabel } from "./labels";
import type { ActionItem, Persona, WorkRequest } from "./viewModels";
import { StatusText, WorkRequestDetailDrawer, conditionText, displayNameOf } from "./WorkModals";
import { WorkRow, WorkTable } from "./WorkTable";

type ActionInboxPageProps = {
  personaId: string;
  personas: Persona[];
  canReadActions: boolean;
  canDecideActions: boolean;
  canDecideWorkRequests: boolean;
  onError: (message: string | null) => void;
};

type RequestDialog = { requestId: string; mode: "negotiate" | "reject" } | null;

export function ActionInboxPage({
  personaId,
  personas,
  canReadActions,
  canDecideActions,
  canDecideWorkRequests,
  onError,
}: ActionInboxPageProps) {
  const [requests, setRequests] = useState<WorkRequest[]>([]);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [isWorking, setIsWorking] = useState(false);
  const [dialog, setDialog] = useState<RequestDialog>(null);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<WorkRequest | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextRequests, nextActions] = await Promise.all([
        canDecideWorkRequests ? getActionInbox() : Promise.resolve([]),
        canReadActions ? getActions() : Promise.resolve([]),
      ]);
      setRequests(nextRequests);
      setActions(nextActions);
      setSelectedRequest((current) =>
        current ? nextRequests.find((request) => request.request_id === current.request_id) ?? null : null,
      );
    } catch (error) {
      onError(error instanceof Error ? error.message : "판단함을 불러오지 못했습니다.");
    }
  }, [canDecideWorkRequests, canReadActions, onError, personaId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  function openDialog(request: WorkRequest, mode: "negotiate" | "reject") {
    setDialog({ requestId: request.request_id, mode });
    setNote("");
  }

  async function accept(request: WorkRequest) {
    setIsWorking(true);
    onError(null);
    try {
      await decideWorkRequest(request.request_id, "accept", request.version);
      await refresh();
      setNotice(`'${request.title}' 요청을 수락했습니다. 내 업무에 생성되었습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "요청을 처리하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  async function submitDialog(request: WorkRequest) {
    const trimmed = note.trim();
    if (!trimmed) {
      onError(dialog?.mode === "reject" ? "거절 사유를 입력해 주세요." : "협의 조건을 입력해 주세요.");
      return;
    }
    setIsWorking(true);
    onError(null);
    try {
      if (dialog?.mode === "reject") {
        await decideWorkRequest(request.request_id, "reject", request.version, trimmed);
        setNotice(`'${request.title}' 요청을 거절했습니다. 업무는 생성되지 않습니다.`);
      } else {
        await negotiateWorkRequest(request.request_id, request.version, { note: trimmed });
        setNotice(`'${request.title}' 요청에 협의 조건을 보냈습니다.`);
      }
      setNote("");
      setDialog(null);
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : "요청을 처리하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  async function decideActionItem(action: ActionItem, decision: "approve" | "reject") {
    setIsWorking(true);
    onError(null);
    try {
      await decideAction(action.action_id, action.version, decision);
      await refresh();
      setNotice(decision === "approve" ? `'${action.title}' 제안을 승인해 반영했습니다.` : `'${action.title}' 제안을 거절했습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "확인 항목을 처리하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  const pendingActions = actions.filter((action) => action.state === "pending");

  return (
    <section className="page-surface">
      <div className="page-head">
        <div>
          <h1>판단</h1>
          <p>나에게 온 업무 요청과 AX가 제안한 변경을 검토합니다. 수락한 요청만 내 업무가 되고, 승인한 제안만 실제로 반영됩니다.</p>
        </div>
      </div>

      {notice && (
        <p className="report-notice" role="status" style={{ marginBottom: 16 }}>
          {notice}
        </p>
      )}

      <section className="surface-card decision-empty-state">
        <div className="card-title">
          <h3>확인이 필요한 변경</h3>
          <span className="badge ai">AI 제안 {pendingActions.length}건</span>
        </div>
        <WorkTable as="ul" empty={pendingActions.length === 0 ? "현재 확인할 변경이 없습니다." : null}>
          {pendingActions.map((action) => (
            <WorkRow
              actions={
                canDecideActions && (
                  <>
                    <button className="btn h30 primary" disabled={isWorking} onClick={() => void decideActionItem(action, "approve")} type="button">
                      승인
                    </button>
                    <button className="btn h30" disabled={isWorking} onClick={() => void decideActionItem(action, "reject")} type="button">
                      거절
                    </button>
                  </>
                )
              }
              as="li"
              data-action-id={action.action_id}
              key={action.action_id}
              status={<span className="badge ai">AI · v{action.version}</span>}
              subtitle={`${action.payload_summary} · 승인하면 이 변경이 실제로 반영됩니다.`}
              title={action.title}
            />
          ))}
        </WorkTable>
      </section>

      <section className="surface-card decision-empty-state">
        <div className="card-title">
          <h3>확인이 필요한 요청</h3>
          <span className="badge progress">판단 대기 {requests.length}건</span>
        </div>
        <WorkTable
          as="ul"
          columns={[{ label: "내용" }, { label: "상태", width: "104px" }, { label: "액션", width: "300px", align: "end" }]}
          empty={requests.length === 0 ? "현재 판단할 업무 요청이 없습니다." : null}
        >
          {requests.map((request) => {
            const activeDialog = dialog?.requestId === request.request_id ? dialog : null;
            const condition = conditionText(request.conditions);
            return (
              <WorkRow
                actions={
                  <>
                    <button className="btn h30 primary" disabled={isWorking} onClick={() => void accept(request)} type="button">
                      수락
                    </button>
                    <button className="btn h30" disabled={isWorking} onClick={() => openDialog(request, "negotiate")} type="button">
                      협의
                    </button>
                    <button className="btn h30" disabled={isWorking} onClick={() => openDialog(request, "reject")} type="button">
                      거절
                    </button>
                  </>
                }
                as="li"
                extra={
                  activeDialog && (
                    <div className="inline-reason">
                      <label className="sr-only" htmlFor={`note-${request.request_id}`}>
                        {activeDialog.mode === "reject" ? "거절 사유" : "협의 조건"}
                      </label>
                      <input
                        autoFocus
                        id={`note-${request.request_id}`}
                        onChange={(event) => setNote(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") void submitDialog(request);
                          if (event.key === "Escape") setDialog(null);
                        }}
                        placeholder={activeDialog.mode === "reject" ? "예: 현재 업무 우선순위와 맞지 않습니다" : "예: 9월 5일까지 완료 가능"}
                        value={note}
                      />
                      <button
                        className={activeDialog.mode === "reject" ? "btn h30 danger" : "btn h30 primary"}
                        disabled={isWorking}
                        onClick={() => void submitDialog(request)}
                        type="button"
                      >
                        {activeDialog.mode === "reject" ? "거절 확정" : "조건 보내기"}
                      </button>
                      <button className="btn h30 ghost" onClick={() => setDialog(null)} type="button">
                        입력 취소
                      </button>
                    </div>
                  )
                }
                key={request.request_id}
                onOpen={() => setSelectedRequest(request)}
                status={<StatusText label={workRequestStateLabel[request.state]} state={request.state} />}
                subtitle={
                  condition
                    ? `요청자 ${displayNameOf(personas, request.requester_id, "동료")} · 보낸 협의 조건: ${condition}`
                    : `요청자 ${displayNameOf(personas, request.requester_id, "동료")}`
                }
                title={request.title}
              />
            );
          })}
        </WorkTable>
      </section>

      {selectedRequest && (
        <WorkRequestDetailDrawer
          canDecide={canDecideWorkRequests}
          onChanged={refresh}
          onClose={() => setSelectedRequest(null)}
          onError={onError}
          personaId={personaId}
          personas={personas}
          request={selectedRequest}
        />
      )}
    </section>
  );
}
