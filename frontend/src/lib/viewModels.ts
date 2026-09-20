export type ProductSurface = "today" | "calendar" | "meetings" | "work" | "report" | "project" | "org" | "graph";

/** One thing in the relation graph. Nodes are canonical resources, never a graph-only record. */
export type GraphNodeKind = "person" | "team" | "project" | "work_request" | "task" | "material" | "meeting" | "report";

export type GraphNode = {
  kind: GraphNodeKind;
  id: string;
  title: string;
  state?: string | null;
  /** Where the ledger plans this in time (deadline, meeting day, report date). Absent when it plans none. */
  date?: string | null;
  /** 어느 프로젝트의 일인가. 업무 node에만 있고, 비어 있는 것이 정상이다. */
  project_id?: string | null;
  /** 접어서 이 안에 담은 개수. 묶어 보는 표현 수준에서만 실리며, 권한 밖은 여기 세지 않는다. */
  folded?: number | null;
};

/**
 * How two things are connected, as the ledgers already record it.
 *
 * `label`/`inverse_label` are the same edge read from either end — wording, not a second connection — and
 * `provenance` names the ledger that states it. `count` appears only when an answer was grouped one level up.
 */
export type GraphEdge = {
  kind: string;
  from: string;
  to: string;
  label?: string;
  inverse_label?: string;
  provenance?: string;
  count?: number;
};

/** One observed step of a turn's walk: something it found, or a connection it followed. */
export type GraphReceipt = {
  receipt_id: string;
  turn_id: string;
  sequence: number;
  kind: "node" | "edge";
  node_ref?: string | null;
  node_title?: string | null;
  edge_kind?: string | null;
  from_ref?: string | null;
  from_title?: string | null;
  to_ref?: string | null;
  to_title?: string | null;
  observed_at: string;
};

export type GraphNeighborhood = { center: GraphNode; nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean };

/** The first screen: what this person is already connected to, at the chosen level of grouping. */
export type GraphView = "member" | "team" | "project";
export type GraphOverview = GraphNeighborhood & { view: GraphView; available_views: GraphView[] };

/** 부서를 가로질러 묶이는 일 하나. 소유 조직은 없고, 붙은 사람이 곧 그 프로젝트다. */
export type Project = {
  project_id: string;
  name: string;
  description: string | null;
  state: string;
  /** 기간은 없을 수 있다 — 시작만 정해지고 끝은 아직 없는 일이 흔하다. */
  starts_on: string | null;
  ends_on: string | null;
  external_key: string | null;
  version: number;
};

export type ProjectMember = {
  assignment_id?: string;
  member_id: string;
  display_name: string;
  assignment_kind: "lead" | "member";
  valid_from: string | null;
  valid_until: string | null;
};

/** 현재 구성원과 섞지 않고 읽는 한 번의 프로젝트 참여 회차. */
export type ProjectParticipation = ProjectMember & {
  assignment_id: string;
  assigned_by_member_id: string | null;
  assigned_by_display_name: string | null;
  created_at: string;
  ended_at: string | null;
  ended_by_member_id: string | null;
  ended_by_display_name: string | null;
  end_reason: string | null;
};

export type ProjectDetail = Project & {
  /** 이 프로젝트의 담당자를 붙이고 뗄 수 있는가. 서버가 판정하고 화면은 그대로 따른다. */
  may_manage: boolean;
  members: ProjectMember[];
  tasks: Array<{
    task_id: string;
    title: string;
    state: string;
    start_date: string | null;
    due_date: string | null;
    parent_task_id: string | null;
  }>;
};

export type Persona = {
  id: string;
  display_name: string;
};

/**
 * 수행 상태 — **넷이 계약이다** (SPEC-003 §4 Data Contract).
 *
 * `completion_submitted` 는 **외부 계약에 없다.** 요청 업무의 완료 보고가 들어가면 밖으로는
 * `state=done` + `derived.approval=awaiting_review` 로 읽힌다(SPEC-003 §4 Request/Response ·
 * SPEC-001 §4 State). 내부 enum 은 백엔드에 남아 있지만 **그 값을 화면 타입으로 들이지 않는다** —
 * 들이면 「승인 전 done」을 상태값으로 판단하는 코드가 다시 생긴다.
 *
 * `blocked` 는 **이 판이 없애지 않는다** — 계약에는 없고(M-6 미정) 코드에는 남아 있어서, 기존
 * 막힘 흐름을 그대로 보존한다. 필터 칩만 그리지 않는다(WORK-002 Phase 7-B).
 */
export type TaskState = "open" | "in_progress" | "blocked" | "done" | "cancelled";

/** 수락·담당 변경을 기다리는 자리. **상태가 아니라 파생 표시다** (SPEC-003 §2.2 · DEC-002 D-4). */
export type DerivedAssignment = "awaiting_acceptance" | "awaiting_handover";

/** 완료 확인의 자리 (SPEC-002 계승). 승인 전 `done` 과 최종 완료를 가르는 **유일한** 값이다. */
export type DerivedApproval = "awaiting_review" | "awaiting_revision" | "approved";

/** 응답을 기다리는 제안 (SPEC-003 §4 Data). 담당 변경은 여기 없다 — `task_assignments` 표면이 갖는다. */
export type DerivedProposal = "cancellation_pending" | "terms_change_pending";

/** 상위의 최종 완료를 막고 있는 하위 하나. `why` 가 「아직 안 끝났다」와 「승인 전이다」를 가른다. */
export type BlockingChild = {
  task_id: string;
  title: string;
  why: "unfinished" | "awaiting_approval" | string;
};

/**
 * 서버가 낸 파생 표시 묶음 (SPEC-003 §4 `GET /api/tasks/{id}` · §2.11).
 *
 * **화면이 상태 문자열로 권한·완결을 추론하지 않는다.** 무엇을 기다리는지도, 무엇이 막고 있는지도
 * 여기서만 읽는다. 값이 통째로 없는 응답(구 서버)에서는 기존 동작이 그대로 선다.
 */
export type TaskDerived = {
  assignment?: DerivedAssignment | null;
  approval?: DerivedApproval | null;
  proposal?: DerivedProposal | null;
  blocking_children?: BlockingChild[];
  /** 기한이 지난 날수. **표시만 바꾼다** — 상태·담당·기한은 그대로다 (SPEC-003 §2.6 · U-14). */
  overdue_days?: number | null;
  reply?: unknown;
  status_note?: unknown;
};

/** 왜 취소됐나 (SPEC-003 §4 Data). 상위 목록에서 「취소됨 — 요청 거절」로 읽히는 근거다 (F-3). */
export type TaskCancelReason = "direct" | "request_rejected" | "request_withdrawn" | "cancellation_agreed";

/** One part of a larger Task: a Task of its own, with its own holder, dates and state. */
export type TaskChild = {
  task_id: string;
  title: string;
  state: TaskState;
  due_date?: string | null;
  assignee?: { member_id: string; display_name: string } | null;
  /** 하위의 파생 표시 — 「완결」 판정이 `state` 만 보지 않게 하는 자리다 (SPEC-003 §4 Data · V-15·V-16). */
  derived?: TaskDerived | null;
  /** 취소된 하위가 왜 취소됐나. 상위에서 「취소됨 — 요청 거절」로 읽힌다 (F-3). */
  cancel_reason?: TaskCancelReason | string | null;
};

/**
 * Where requested work stands with the person who asked for it. Present only on Tasks whose completion they confirm.
 * `awaiting_review` means reported and waiting; `awaiting_revision` means they said what is still missing.
 */
export type TaskDelivery = {
  action_item_id: string;
  status: "awaiting_review" | "awaiting_revision" | "resolved";
  rounds: number;
  reported_by: string;
  reported_at: string | null;
  summary: string | null;
  last_reason: string | null;
};

export type DirectTask = {
  task_id: string;
  title: string;
  state: TaskState;
  version: number;
  block_reason: string | null;
  description?: string | null;
  start_date?: string | null;
  due_date?: string | null;
  created_at?: string;
  updated_at?: string;
  organization_unit_id?: string | null;
  origin_kind?: string;
  visibility?: string;
  assignment?: TaskAssignmentSummary | null;
  lineage?: TaskLineage;
  /** Where this Task came from, decided and authorized by the server. Never derived on the client. */
  origin?: TaskOrigin | null;
  /** "owner" holds the task; "read_only" may look at it through a relationship they are allowed to read. */
  access?: "owner" | "read_only";
  /** Who holds the work right now, projected from the active assignment by the server. */
  assignee?: { member_id: string; display_name: string } | null;
  /** 어느 프로젝트의 일인가. 서버의 업무 투영이 이미 내던 값이고, 타입에만 빠져 있었다. */
  project_id?: string | null;
  /**
   * 이 응답이 **날짜를 바꾼 것이라면** 그때 함께 닫힌 시간 배정의 건수와 사유 (SPEC-004 증보 K3).
   *
   * **날짜를 바꾸는 표면에만 실린다** — `PATCH /api/tasks/{id}` · `POST /api/tasks/{id}/start` ·
   * `POST …/proposals/{pid}/respond` 셋뿐이다. `/block`·`/resume`·`/complete`·`/cancel` 에는
   * **없고**, 조회(`GET /api/tasks`)에도 없다 — 그래서 `undefined` 가 기본이고 그때는 **아무 말도 하지 않는다.**
   */
  schedule_release?: ScheduleRelease | null;
  /**
   * 선행업무 — **활성인 것만** 실린다 (SPEC-001 §4). 상위(`parent`)·참고(`reference`)와
   * **다른 세 번째 관계**이고 서로 대체하지 않는다. 이것이 끝나야 이 업무를 시작할 수 있다.
   */
  preceding_task_ids?: string[];
  /**
   * 선행 각각의 요약 — `preceding_task_ids` 와 **같은 순서·같은 길이**다 (상세 조회에만 실린다).
   *
   * **볼 수 없는 선행은 `title`·`state` 가 비고 자리만 남는다** (SPEC-001 §4). 자료 구획과 다르다:
   * 자료는 건수도 내지 않지만 선행은 **시작을 막는 이유**라, 이유를 숨기면 사람이 다음 걸음을
   * 고를 수 없다. 제목은 감추고 건수는 낸다 — **배열 안의 빈 자리가 그 건수다.**
   */
  predecessors?: Array<{ task_id: string; title: string | null; state: TaskState | null }>;
  /** 승인자 0..1. 화면 라벨은 「결재자」다 (SPEC-001 §4 · OQ-N). */
  approver_id?: string | null;
  /** Steps inside this Task. Present on the detail read, not on list projections. */
  checklist?: ChecklistItem[];
  /** Earlier work this Task points at. Present on the detail read. */
  references?: TaskReference[];
  /** The result question this Task's completion opens, when someone else asked for the work. */
  delivery?: TaskDelivery | null;
  /** The work this one is a part of, named just enough to know what it belongs to. */
  parent?: { task_id: string; title: string; state: TaskState } | null;
  /** The parts of this work this reader may see. A subtask is a Task, not a checklist line. */
  children?: TaskChild[];
  /**
   * 직속 하위의 셈 (SPEC-003 §4). `blocking` 이 0 이어야 최종 완료가 통과한다.
   * **읽을 수 없는 하위는 여기에도 들어가지 않는다** (U-15).
   */
  child_progress?: { done: number; total: number; blocking?: number; cancelled?: number };
  checklist_progress?: { done: number; total: number };
  /** 서버가 낸 파생 표시. 화면은 기다림·완결·막는 하위를 **여기서만** 읽는다. */
  derived?: TaskDerived | null;
  /** 취소 사유 구분 (SPEC-003 §4 Data). */
  cancel_reason?: TaskCancelReason | string | null;
  /** `open → in_progress` 시각 — 계획 시작일(`start_date`)과 다른 값이다 (V-13 · L-10). */
  started_at?: string | null;
  /*
   * **수락 시각은 여기 없다.** 서버는 그것을 담당 관계에 싣는다 — `assignment.accepted_at`
   * (`TaskAssignmentSummary`) 이고, 「누가 언제 이 일을 맡았나」는 업무가 아니라 그 관계의 사실이기
   * 때문이다. 최상위에 `accepted_at` 을 두면 **서버가 내지 않는 키**를 타입이 약속하게 된다.
   * U-3 의 「수락 시각과 시작 시각을 각각 읽는다」는 `assignment.accepted_at` ↔ `started_at` 이다.
   */
  completed_at?: string | null;
  reopened_at?: string | null;
};

/**
 * 한 업무의 담당 관계 — **현재와 대기를 각각** 낸다 (SPEC-003 §4 `GET /api/tasks/{id}/assignments`).
 *
 * 담당 변경 대기 동안 `current`(active)와 `pending` 이 **함께 산다**(V-18). 하나로 합쳐 읽으면
 * 「책임 공백」이 화면에서 생긴다.
 */
export type TaskAssignmentsView = {
  task_id: string;
  current: TaskAssignment | null;
  pending: TaskAssignment | null;
  /** 지난 담당 관계 — 교체·거절·철회가 그대로 남는다. 담당 변경 «이력» 이 읽히는 자리다 (F-5). */
  history: TaskAssignment[];
};

/** 수락 후의 제안 — 취소 합의와 조건 변경 (SPEC-003 §4). 담당 변경은 이 enum 에 없다. */
export type TaskProposalKind = "cancellation" | "terms_change";

export type TaskProposalState = "pending" | "agreed" | "declined" | "withdrawn";

/** 한 업무의 제안 — **대기와 지난 것을 각각** 낸다 (`GET /api/tasks/{id}/proposals`). */
export type TaskProposalsView = {
  task_id: string;
  pending: TaskProposal[];
  history: TaskProposal[];
};

export type TaskProposal = {
  proposal_id: string;
  kind: TaskProposalKind | string;
  state: TaskProposalState | string;
  /** 제안한 사람. 철회는 이 사람만 부른다. */
  proposed_by: string;
  /** 답해야 하는 사람 — 담당자다. 서버가 정하고 화면은 「내 차례인가」만 읽는다. */
  responder_id?: string | null;
  /**
   * 조건 변경이 **실제로 바꾸자고 하는 값** (`kind=terms_change`). 취소 제안에서는 비어 있다.
   * 무엇을 바꾸는지가 없는 조건 변경은 답할 수 없는 제안이라, 화면이 이 값을 반드시 실어 보낸다.
   */
  payload?: Record<string, unknown> | null;
  reason: string | null;
  created_at: string;
  responded_at?: string | null;
};

/** 제안 명령의 응답 봉투 — 제안 하나와 그 명령이 업무를 옮긴 회차를 함께 낸다. */
export type TaskProposalMutation = {
  task_id: string;
  proposal: TaskProposal;
  task_version: number;
};

/**
 * Earlier work a Task points at. One meaning only — `참고` — and the pointer never grants access: `task` is absent
 * when the reader may not open what it points at.
 */
export type TaskReference = {
  reference_id: string;
  created_by: string;
  created_at?: string;
  task: { task_id: string; title: string; state: TaskState; due_date?: string | null; assignee?: { member_id: string; display_name: string } | null } | null;
  /** The Task version this connection or release moved the Task to. Only mutation answers carry it. */
  task_version?: number;
};

/** How a Task got to where it is: one frozen picture per version, and the ledger lines that produced them. */
export type TaskHistory = {
  task_id: string;
  versions: Array<{
    version: number;
    change_kind: string;
    actor_id: string;
    reason: string | null;
    captured_at: string;
    snapshot: Record<string, unknown>;
  }>;
  activity: Array<{
    event_kind: string;
    actor: { member_id: string; display_name: string } | null;
    actor_kind: string;
    summary: string;
    reason: string | null;
    /** The version this line produced, so a reader can open exactly that snapshot. */
    version: number | null;
    /** What carried this change here — an approved AX confirmation, say. Never a claim that AX acted. */
    causation?: { kind: string; id: string } | null;
    occurred_at: string;
  }>;
};

/** What actually moved between two versions. Only the fields the server says changed are present. */
export type TaskHistoryDiff = {
  task_id: string;
  from: number;
  to: number;
  changes: Record<string, { before?: unknown; after?: unknown; added?: string[]; removed?: string[] }>;
};

/**
 * The Task's provenance as the server resolved it. `actor_role` already says which role the actor played, so no
 * surface has to decide whether a member id means requester, assigner or assignee. `source` is absent when the
 * caller may not read the resource behind it.
 */
export type TaskOrigin = {
  kind: "self_created" | "work_request" | "direct_assignment" | string;
  actor_role: string;
  actor: { member_id: string; display_name: string } | null;
  source: { type: string; id: string; title: string | null } | null;
};

/** One step inside a Task: no assignment, no lineage, no judgement. */
export type ChecklistItem = {
  /** The Task version this step's change moved the Task to. Only mutation answers carry it. */
  task_version?: number;
  item_id: string;
  text: string;
  position: number;
  done: boolean;
  /** `active` while it is on the list; `archived` once someone took it off. */
  state?: string;
  /** The step's own concurrency counter: answer it and two people editing two steps never collide. */
  version?: number;
  created_by?: string;
  completed_by: string | null;
  completed_at: string | null;
};

export type TaskAssignmentSummary = {
  assignment_id: string;
  kind: "self" | "request_effect" | "direct";
  status: "pending" | "active" | "declined" | "superseded" | "cancelled";
  /** Who put this person on the work. Empty when nobody did — a self assignment has no assigner. */
  assigned_by: string | null;
  accepted_at: string | null;
};

export type TaskAssignment = {
  assignment_id: string;
  assignment_kind: "self" | "request_effect" | "direct";
  status: "pending" | "active" | "declined" | "superseded" | "cancelled";
  assignee_id: string;
  /** Who put this person on the work. Empty when nobody did — a self assignment has no assigner. */
  assigned_by: string | null;
  decline_reason: string | null;
  created_at: string;
  accepted_at: string | null;
  declined_at: string | null;
  /** 다른 담당이 수락해서 이 행이 닫힌 시각. 「중간에 담당 없는 구간」이 없다는 증거다 (V-18). */
  superseded_at?: string | null;
  /** 이 제안이 물러나게 한 이전 담당 행. */
  supersedes_assignment_id?: string | null;
  /**
   * 담당 관계가 가리키는 업무. **관계 조회(`/assignments`)의 행에는 실리지 않는다** —
   * 그 응답은 한 업무의 담당들이라 업무를 되풀이하지 않는다. 보낸 목록(`/task-assignments/sent`)만 싣는다.
   */
  task: DirectTask;
};

export type TaskLineage = {
  request_thread_id: string | null;
  source_work_request_id: string | null;
  source_decision_item_id: string | null;
  source_submission_id: string | null;
  source_review_decision_id: string | null;
  source_action_item_id: string | null;
  source_task_id: string | null;
};

export type TaskMaterialKind = "input" | "output";

export type TaskMaterial = {
  /** Where the artifact lives when it is not a file SCAX holds. */
  url?: string | null;
  /** Nothing was fetched and no revision was pinned, so it may change under the reader. */
  mutable_source?: boolean;
  /** Another thing inside SCAX, resolved for this reader. Absent when they may not open it. */
  resource?: { type: string; id: string; title: string } | null;
  /** `file` (SCAX holds the bytes), `external_link` (a URL), `resource_ref` (something inside the product). */
  source_kind?: string;
  material_id: string;
  binding_id: string;
  task_id: string;
  kind: TaskMaterialKind;
  name: string;
  content_type: string;
  size_bytes: number;
  uploaded_by: string;
  created_at: string;
  removed_at: string | null;
  extraction?: MaterialExtraction | null;
  /** The Task version this attach or detach moved the Task to. Only mutation answers carry it. */
  task_version?: number;
};

/**
 * 발송한 요청에 붙은 자료 한 건 (`WorkRequestMaterialView`).
 *
 * **업무 자료와 같은 모양이고 자리가 하나 더 붙는다** — `request_id` 는 자료가 매달린 요청이고,
 * `task_id` 는 그 요청이 세운 업무다. 아직 수락되지 않은 요청에는 업무가 없으므로 업무 자료와
 * 달리 **비어 있을 수 있다.**
 */
export type WorkRequestMaterial = Omit<TaskMaterial, "task_id" | "task_version"> & {
  request_id: string;
  task_id: string | null;
  /** 붙이고 뗀 답만 싣는다 — 그 조작이 요청을 옮긴 회차다 (업무 자료의 `task_version` 자리). */
  request_version?: number;
};

export type MaterialExtraction = {
  extraction_id: string;
  /** `needs_ocr`: a scan with no text layer — unreadable, not empty. `purged`: the file itself was destroyed. */
  status: "queued" | "running" | "completed" | "failed" | "unsupported" | "needs_ocr" | "purged";
  extractor: string | null;
  failure_reason: string | null;
  failure_text: string | null;
  chunk_count: number;
  char_count: number;
  page_count: number | null;
  attempt_count: number;
  requested_at: string | null;
  completed_at: string | null;
};

export type MaterialEvidence = {
  evidence_id: string;
  turn_id: string;
  source_contexts?: Array<{ resource_type: string; resource_id: string; title: string; binding_id: string; origin: string }>;
  source_locator?: Record<string, unknown> | null;
  material_id: string;
  attachment_id: string;
  chunk_id: string;
  name: string;
  integrity_ref: string;
  page: number | null;
  excerpt: string;
  query: string;
  rank: number;
  origin: string;
  recorded_at: string;
};

export type TaskPatch = {
  title?: string;
  description?: string;
  start_date?: string | null;
  due_date?: string | null;
  /** 어느 프로젝트의 일로 둘 것인가. `null`이면 프로젝트에서 뗀다. */
  project_id?: string | null;
};


export type AccessGrant = {
  grant_id: string;
  role_id: string | null;
  role_label: string | null;
  capability_id: string | null;
  role_capability_version: number | null;
  scope_kind: string;
  scope_ref: string | null;
  scope_name: string | null;
  include_descendants: boolean;
  origin_rule_id: string | null;
  granted_by: string | null;
  valid_from: string;
  valid_until: string | null;
};

/** A role as this organization actually has it — not the product's recommendation. */
export type InstalledAccessRole = {
  role_id: string;
  label: string;
  version: number;
  template_key: string | null;
  customized: boolean;
  capabilities: string[];
};

/** What one person may do and where, read by someone who administers access for them. */
export type MemberAccess = {
  member_id: string;
  display_name: string;
  roles: string[];
  capabilities: string[];
  grants: AccessGrant[];
};

export type OrganizationProfile = {
  member_id: string;
  display_name: string;
  organizations: Array<{ id: string; name: string }>;
  roles?: string[];
  grants?: AccessGrant[];
  capabilities: string[];
  assistant_character?: {
    character_key: string;
    version: number;
  };
};

export type DailyReportDraft = {
  report_id: string;
  draft_id: string;
  draft_version: number;
  body: string;
  source_refs: Array<{
    task_id: string;
    task_version: number;
    state: string;
    occurred_at: string;
  }>;
  status: string;
  workflow_run_id?: string;
  definition_version_id?: string;
  workflow_state?: string;
  submission_status?: string;
};

export type DailyReportHistory = {
  report_id: string;
  report_date: string;
  status: string;
  drafts: Array<{
    draft_id: string;
    version: number;
    body: string;
    source_refs: DailyReportDraft["source_refs"];
    workflow_run_id: string;
    definition_version_id: string;
  }>;
  submissions: Array<{
    submission_id: string;
    version: number;
    body: string;
    source_refs: DailyReportDraft["source_refs"];
    reason: string | null;
    submitted_at: string;
  }>;
};

export type DailyReportStatus = {
  report_date: string;
  status: "not_started" | "draft" | "submitted";
  report_id: string | null;
  generation_id: string | null;
  generation_status: "queued" | "running" | "completed" | "failed" | "needs_verification" | null;
  generation_error_code: string | null;
};

export type WorkRequest = {
  /** 수신함 응답에 포함될 경우 서버 분류를 우선한다. */
  category?: "work" | "reference";
  request_id: string;
  request_thread_id?: string | null;
  submission_version?: number | null;
  title: string;
  description?: string | null;
  due_date?: string | null;
  /** Steps requested before acceptance; they become the accepted Task's own checklist. */
  checklist?: string[];
  /** Permission-safe earlier work, populated by the WorkRequest detail read. */
  references?: TaskReference[];
  requester_id?: string;
  assignee_id?: string;
  cc_member_ids?: string[];
  /**
   * 출처 상태다 — 업무의 수행 상태(`open`…)와 다른 축이다.
   *
   * `assigned` 는 **W1 의 신규 경로**가 세우는 값이다: 판단 없이 업무와 활성 담당이 섰다는 사실만 말한다.
   * 나머지 다섯은 **과거 판단 경로의 행**이 그대로 갖는 값이고 뜻이 바뀌지 않았다 — `assigned` 를
   * 「수락됨」과 섞지 않는다(WORK-001 Phase 4).
   */
  state: "pending" | "negotiating" | "assigned" | "accepted" | "rejected" | "withdrawn" | "cancelled_by_agreement";
  version: number;
  task_id: string | null;
  assignment_state: string | null;
  conditions: Record<string, unknown> | null;
  /**
   * **누가 눌러 이 요청이 섰나** — 회의 승격이면 회의(시스템)가 요청자 자리에 서고 이 값이 «사람» 이다
   * (BASE-002 O-31 · `requests.py:953`). 요청자 전용 조작(수정·재상신·철회·제안)의 판정이
   * `requester_id` **또는** 이 값이므로, 화면도 둘을 함께 읽어야 한다.
   */
  promoted_by_member_id?: string | null;
  /** 요청자를 사람으로 그릴지 「회의 · {이름}」으로 그릴지 — 서버가 정한다 (D40). */
  requester_kind?: "member" | "system" | string | null;
  source_meeting_id?: string | null;
  /** 하위 요청의 상위 업무 (V-9). 발송 단계부터 연결된다. */
  parent_task_id?: string | null;
  /** 재요청이면 이전 요청 (V-12). 「다시 요청」이 남기는 연결이다. */
  supersedes_request_id?: string | null;
  /** 이 요청이 물러난 자리에 선 새 요청. 이전 요청 쪽에서 읽는 같은 연결이다. */
  superseded_by_request_id?: string | null;
  /**
   * 요청자 목록에서 정리(숨김)됐나 (L-6 · F-6). **이력은 남는다** — 목록에서만 빠진다.
   * 값이 없는 응답에서는 「숨기지 않음」으로 읽는다.
   */
  list_entry_hidden?: boolean | null;
};

export type ActionItem = {
  action_id: string;
  conversation_id: string;
  turn_id: string;
  action_type: string;
  title: string;
  state: "pending" | "approved" | "rejected";
  version: number;
  payload_summary: string;
  result: Record<string, unknown> | null;
  audit_ref: string | null;
  /** Server-provided approval controls; rendered verbatim, never inferred from state on the client. */
  commands?: ActionCommand[];
  /** The target this confirmation answers has moved, so it can only be cleared away. */
  obsolete?: boolean;
  /** Server-side presentation: the real work title, the operation kicker, and permission-safe preview rows. */
  subject?: string;
  operation_label?: string;
  preview?: ActionPreviewField[];
  /** Server-authored outcome for effects that can complete only partially. */
  result_summary?: string;
  /** Closed, server-authored editor contract for an editable AX proposal. */
  edit_contract?: ActionEditContract;
  /** Pre-create link/file identities owned by this Action; confirmation alone claims them into a Task. */
  material_drafts?: ActionMaterialDraft[];
  material_results?: Array<Record<string, unknown>>;
};

/** One independent judgement question, whatever raised it. The server decides everything the client renders. */
export type ActionItemEnvelope = {
  action_item_id: string;
  kind: string;
  status: "awaiting_review" | "awaiting_revision" | "resolved";
  subject: string;
  operation_label: string;
  current_question: string;
  preview: ActionPreviewField[];
  allowed_commands: ActionCommand[];
  submission_version: number;
  waiting_on: { member_id: string; display_name: string } | null;
  resource: { type: string; id: string };
  expected_version: number | null;
  /** The Task this proposal produced, when it produced one. */
  derived_task_id?: string | null;
  /** The Meeting this proposal produced, when it produced one. */
  derived_meeting_id?: string | null;
  /** Fields the last reviewer asked to have changed. A proposal, never an edit: the round still holds what was sent. */
  suggested_changes?: Record<string, string>;
  edit_contract?: ActionEditContract;
  material_drafts?: ActionMaterialDraft[];
  material_results?: Array<Record<string, unknown>>;
  /** Server-authored outcome for effects that can complete only partially. */
  result_summary?: string;
  /** Per-effect receipt for a server-executed batch. */
  execution_result?: Record<string, unknown>;
};

export type ActionMaterialDraft = {
  material_draft_id: string;
  action_item_id: string;
  source_kind: "external_link" | "file";
  name: string;
  content_type: string;
  size_bytes: number;
  url: string | null;
  integrity_ref: string;
  state: "staged" | "claimed";
  expires_at: string;
  claimed_task_id: string | null;
  claimed_meeting_id?: string | null;
};

/** One immutable round: the frozen content that was judged, what changed since the previous round, and the answers. */
export type ActionRound = {
  submission_id: string;
  submission_version: number;
  submitted_by: string;
  submitted_at: string;
  content_hash: string;
  snapshot: Record<string, unknown>;
  diff: Record<string, { before: unknown; after: unknown }> | null;
  /** What this round currently stands on, in the canonical manifest form the hash is computed from. */
  evidence?: EvidenceManifestEntry[];
  evidence_hash?: string | null;
  decisions: Array<{
    review_decision_id: string;
    actor_member_id: string;
    decision: string;
    reason: string | null;
    decided_at: string;
    suggested_changes?: Record<string, string>;
    /** The basis the answer was actually given on, frozen when it was given. */
    evidence_hash?: string | null;
  }>;
};

/** One comment on the judgement. The discussion travels with the ActionItem and never moves it. */
export type ActionDiscussionEntry = {
  comment_id: string;
  author_member_id: string;
  body: string;
  created_at: string;
  edited_at: string | null;
  attachments: Array<{ attachment_id: string; name: string }>;
};

export type ActionItemDetail = ActionItemEnvelope & {
  rounds: ActionRound[];
  derived_task_id?: string | null;
  discussion?: ActionDiscussionEntry[];
};

export type ActionPreviewField = { id: string; label: string; value: string; kind: "text" | "date" | "person" | "people" | "state" | string };

export type ActionCommand = { id: string; label: string; tone: "primary" | "neutral" | "danger" | string; requires_reason?: boolean };

export type ActionEditOption = { value: string; label: string; organization_ids?: string[] };

export type ActionEditField = {
  id: string;
  label: string;
  type: "text" | "textarea" | "number" | "date" | "datetime" | "boolean" | "person" | "select" | "multi_select" | "string_list" | "object_list" | "ordered_select" | "source_select";
  required: boolean;
  editable: boolean;
  empty_policy?: 'forbid' | 'null' | 'omit' | 'empty_string';
  value?: string | null;
  label_value?: string;
  options?: ActionEditOption[];
};

export type ActionEditContract = {
  editor: "task" | "meeting" | "task_progress_batch" | "command";
  base_submission_version: number;
  values: Record<string, unknown>;
  fields: ActionEditField[];
  warnings?: string[];
};

export type TurnProgressState = "queued" | "preparing" | "tool_running" | "composing" | "retrying" | "completed" | "failed" | "cancelled";

export type FollowUpCandidate = {
  candidate_id: string;
  source_turn_id: string;
  label: string;
  user_text: string;
  selected_message_id: string | null;
};

export type ConversationTurn = {
  turn_id: string;
  state: string;
  progress_state?: TurnProgressState;
  current_tool_display_name?: string | null;
  attempt?: number;
  queued_at?: string;
  execution_started_at?: string | null;
  execution_completed_at?: string | null;
  queue_wait_ms?: number | null;
  run_ms?: number | null;
  retry_of_turn_id?: string | null;
  usage?: Record<string, number> | null;
  provider_run_ref: string | null;
  provider_session_ref: string | null;
  error: string | null;
  follow_up_candidates?: FollowUpCandidate[];
};

export type Conversation = {
  conversation_id: string;
  title: string;
  version: number;
  messages: Array<{
    message_id: string;
    turn_id: string | null;
    role: "user" | "assistant";
    body: string;
    answer_document?: AnswerDocument | null;
    sequence: number;
    state: "accepted" | "queued";
    body_state?: "final" | "streaming" | "failed" | "cancelled";
    idempotency_key?: string | null;
    follow_up_candidate_id?: string | null;
    created_at?: string;
  }>;
  turns: ConversationTurn[];
  context_references: ConversationContextReference[];
  tool_invocations: Array<{
    turn_id: string;
    sequence: number;
    provider_call_id: string | null;
    tool_name: string;
    display_name: string;
    input_summary: string;
    state: string;
    result_summary: string | null;
    error_summary: string | null;
    latency_ms: number | null;
    started_at?: string | null;
    completed_at?: string | null;
    target_resource_id: string | null;
    target_resource_version: string | null;
    audit_ref: string | null;
  }>;
  actions?: ActionItem[];
  material_evidence?: MaterialEvidence[];
  /** Where each turn actually walked in the relation graph, in the order the tools returned it. */
  graph_receipts?: GraphReceipt[];
  /** The canonical things a turn read and named, so each item in an answer opens its own detail. */
  answer_resources?: AnswerResource[];
  /** `messages` is windowed to the most recent ~50 — true while an older batch is still one scroll-up away. */
  has_more_messages: boolean;
  /** Preview/search/count stay accurate for the whole conversation even while `messages` is windowed. */
  first_user_message_excerpt: string | null;
  user_message_count: number;
  has_final_answer: boolean;
  /** A list row's summary needs only these two — never the full `messages`/`turns` arrays a list row omits. */
  queued_message_count: number;
  latest_turn_state: string | null;
};

/** Saved placement; inaccessible receipts become null without their old metadata. */
export type AnswerElement =
  | { key: string; type: "resource_reference"; ref: string | null }
  | { key: string; type: "resource_list"; ordered: boolean; items: Array<{ ref: string | null; description: string }> };

export type AnswerDocument = { version: 1; elements: AnswerElement[] };

/** Authorized current resource metadata. Inaccessible resources are absent from this projection. */
export type AnswerResource = {
  reference_id: string;
  turn_id: string;
  sequence: number;
  resource_type: "task" | "meeting" | "work_request" | "material" | "report";
  resource_id: string;
  resource_version: number | null;
  title: string;
  source_contexts?: MaterialEvidence["source_contexts"];
  origin?: string;
  state: string | null;
  /** 원문의 어디였는지 — 쪽·절처럼 그 자료가 스스로 부르는 자리. 없는 것이 정상이다. */
  source_locator?: { page?: number; section?: string; sheet?: string; cell?: string; anchor?: string } | null;
  /** 지금의 회차. 회차를 갖지 않는 것에는 없다. */
  current_version?: number | null;
  /** 답이 딛고 선 뒤로 바뀌었는가. 숨기지 않고 말한다. */
  changed_since?: boolean;
};

export type Notification = {
  notification_id: string;
  kind: "meeting.shared" | "work_request.received" | "work_request.accepted" | string;
  summary: string;
  actor_id: string;
  resource: {
    type: "meeting" | "work_request";
    id: string;
    version: number | null;
    title: string;
  };
  created_at: string;
  read_at: string | null;
};

export type ConversationContextReference = {
  resource_type: "task" | "work_request";
  resource_id: string;
  resource_version: number;
  included: boolean;
};

export type ConversationMessageAcceptance = {
  conversation_id: string;
  message_id: string;
  turn_id: string | null;
  queued: boolean;
  queue_size: number;
};

export type OrganizationUnitNode = {
  id: string;
  name: string;
  parent_id: string | null;
  unit_type: string | null;
  lifecycle: string;
  display_order: number;
  member_count: number;
  direct_member_count: number;
  leaders: Array<{ display_name: string; position: string; kind: string }>;
};

export type OrganizationMember = {
  member_id: string;
  display_name: string;
  memberships: Array<{ organization_id: string; organization_name: string; kind: string }>;
  positions: Array<{ position: string; organization_name: string; kind: string }>;
  grade: string | null;
  jobs: string[];
};

export type RequestAttachment = {
  attachment_id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  uploaded_by: string;
  created_at: string;
};

export type RequestComment = {
  comment_id: string;
  author_member_id: string;
  body: string;
  created_at: string;
  edited_at: string | null;
  attachments?: RequestAttachment[];
};

export type RequestEvidence = {
  evidence_id: string;
  submission_id: string;
  submission_version: number;
  attachment_id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  evidence_role: "supporting" | "decision_basis" | string;
  fixed_snapshot_ref: string;
  adopted_by: string;
  adopted_at: string;
  /** The request version adopting this produced: the basis moved, so an open reviewer's version is now stale. */
  request_version?: number;
};

/** One line of a round's basis: the attachment and its integrity, never the row that adopted it. */
export type EvidenceManifestEntry = { attachment_id: string; evidence_role: string; fixed_snapshot_ref: string };

export type RequestTimeline = {
  request: WorkRequest;
  request_thread_id: string | null;
  comments: RequestComment[];
  evidence?: RequestEvidence[];
  decision_item: { decision_item_id: string; kind: string; status: string; due_at: string | null } | null;
  submissions: Array<{
    submission_id: string;
    submission_version: number;
    revises_id: string | null;
    submitted_by: string;
    submitted_at: string;
    snapshot: Record<string, unknown>;
    subject_version: number | null;
    diff: Record<string, { before: unknown; after: unknown }> | null;
    /** What this round currently stands on, and the identity of that set. */
    evidence: EvidenceManifestEntry[];
    evidence_hash: string | null;
  }>;
  review_assignments: Array<{ review_assignment_id: string; submission_id: string; reviewer_member_id: string; status: string; assigned_at: string }>;
  review_decisions: Array<{ review_decision_id: string; submission_id: string; actor_member_id: string; decision: string; reason: string | null; conditions: Record<string, unknown> | null; evidence_hash: string | null; decided_at: string }>;
  activity: Array<{ event_kind: string; actor_id: string; safe_summary: string; reason: string | null; occurred_at: string }>;
};

/* ---- Meeting screens (SCR-105 · SCR-106) — the contract WP-001 owns ---- */

/**
 * 회의 여섯 상태 (SPEC-004 §5.1). 화면은 이 여섯만 안다 — 「정리됨」은 없고 「완료」다.
 * 무엇을 낼 수 있는지는 상태와 `viewer_relation`·`can_edit_*` 가 함께 정한다. 역할(kind)로 추론하지 않는다.
 */
export type MeetingStatus = "scheduled" | "in_progress" | "summarizing" | "done" | "failed" | "cancelled";

/** 이 사람이 회의에 닿은 방식. 「열람」 꼬리표와 조작 버튼의 유무가 이 값 하나로 갈린다. */
export type MeetingViewerRelation = "attendee" | "shared";

export type MeetingRow = {
  meeting_id: string;
  /** 주제를 안 채운 회의는 null 로 온다 — 화면이 「제목 없는 회의」로 읽는다. */
  title: string | null;
  starts_at: string;
  ends_at: string;
  location: string | null;
  status: MeetingStatus;
  viewer_relation: MeetingViewerRelation;
  created_by: string;
  attendee_count: number;
};

export type MeetingListPayload = {
  upcoming: MeetingRow[];
  past: { items: MeetingRow[]; next_cursor: string | null };
};

/** 한 줄이 딛는 원문 구간. 화면은 이것을 타임칩으로 펴고 스크립트의 그 자리로 간다. */
export type MeetingEvidence = { start_ms: number; end_ms: number };

/**
 * 회의록이 선 **벌** — 한 회의에 회의록이 셋이고 벌마다 자기 안건 목록과 줄을 갖는다 (SPEC §4.0-1).
 *
 * ⚠ **표기는 `memo` 다.** SPEC 본문(§4.0-2)은 사람 벌을 `human` 으로 적었지만 그 개명은
 * 사용자 결정으로 되돌아갔고 **서버가 내는 값은 `memo`** 다
 * (`backend/.../meetings/domain.py:80 TRACK_MEMO = "memo"`). 화면은 서버가 내는 값을 쓴다.
 */
export type MeetingTrack = "memo" | "ai" | "final";

/**
 * 벌별 판정 셋. 불리언 하나로는 「종료에서 최종 벌은 열리고 사람 벌은 닫힌다」를 낼 수 없다 (§4.1-6).
 * **화면이 상태로 다시 추론하지 않는다** — 「진행 중」·「정리 중」까지 서버가 이 값에 이미 반영했다.
 */
export type MeetingTrackGates = { memo: boolean; ai: boolean; final: boolean };

/** 회의록 한 줄. `track` 은 어느 트랙에서 온 줄인지이고, 화면은 종류 배지를 두지 않는다. */
export type MeetingLine = {
  line_id: string;
  track: MeetingTrack;
  order: number;
  text: string;
  /** 그 줄을 적은 사람의 `member_id`. **AI 가 낸 줄과 합쳐진 최종 줄에는 작성자가 없다** — 그럴 때 `null` 이다. */
  author: string | null;
  /** 메모 줄에만 값이 있다 — 회의 시작에서 흐른 밀리초. AI·합성 줄은 시각이 아니라 구간에 걸린다. */
  at_ms: number | null;
  evidence: MeetingEvidence[];
  /**
   * 계보 — 이 최종 줄이 딛는 **원본 줄 id** 목록 (§4.2-10 · D54). 최종 벌 줄만 값을 든다.
   * **이 바퀴는 이 값을 화면에 그리지 않는다** — 어디에 어떻게 낼지를 스펙이 아직 정하지 않았다
   * (`OQ-308`). 응답에 오는 것을 타입으로만 받아 둔다.
   */
  from_lines: string[];
};

/**
 * 후속업무 후보 한 줄 (결정 D19-3).
 *
 * **담당 후보를 두지 않는다** — 담당은 [업무 생성]이 여는 업무 요청 모달에서 사람이 고른다. AI 가
 * 사람을 지목하지 않는다. `linked` 가 차 있으면 이미 요청으로 선 것이고, 그 요청이 수락되면
 * `task_id` 가 붙는다.
 */
export type MeetingTodo = {
  todo_id: string;
  agenda_id: string;
  title: string;
  description: string;
  due_candidate: string | null;
  /** 요청 모달의 체크리스트를 미리 채우는 줄들. */
  checklist_candidate: string[];
  /** 이 후보가 딛는 자리 — 어느 회의의 어느 안건의 어느 줄에서 나왔나. 화면은 그리지 않는다. */
  reference: { meeting_id: string; agenda_id: string; line_ids: string[] };
  /**
   * 회의 «중» 배치가 낸 잠정 후보인가 (D46). 참이면 읽기만 한다 — 승격도 삭제도 종료 뒤 최종에서만이다.
   * 최종 합성이 잠정 후보를 통째로 지우므로 완료·실패 화면에는 참인 것이 오지 않는다.
   */
  provisional: boolean;
  linked: { work_request_id: string; task_id: string | null } | null;
};

export type MeetingAgenda = {
  agenda_id: string;
  /**
   * 이 안건이 선 **벌**. 화면이 세 벌을 가르는 **유일한 축**이다 (§4.0-1).
   * 안건 id 는 같은 벌 안에서만 유효하고, `order` 도 벌 «안에서» 1 부터 다시 매겨진다.
   */
  track: MeetingTrack;
  /** 이 안건의 마지막 저장 시각. 다음 저장이 이 값을 함께 보내 「그 사이에 누가 저장했나」를 가른다. */
  last_saved_at: string | null;
  order: number;
  title: string;
  /** 제목이 아직 «자리표시» 인가 (D6). 빠른 시작이 세운 안건은 제목이 빈 값으로 온다 (§12 R-50). */
  title_placeholder: boolean;
  /**
   * 어디서 온 안건인가 (D38). **사람 벌만 값을 갖는다** — AI 벌·최종 벌은 `null` 이다 (§4.1-2).
   * 구 `"ai"` 값은 은퇴했다: AI 가 세운 안건은 출처가 아니라 **벌**로 갈린다.
   */
  source: "manual" | "set" | "carried" | "derived" | null;
  concluded: boolean;
  lines: MeetingLine[];
  todos: MeetingTodo[];
  /**
   * 계보 — 이 최종 안건이 묶은 **원본 안건 id** 목록 (§4.1-3 · D54). 최종 벌만 값을 든다.
   * `from_lines` 와 같은 이유로 **이 바퀴는 그리지 않는다** (`OQ-308`).
   */
  merged_from: string[];
};

export type MeetingInfo = {
  meeting_id: string;
  title: string | null;
  purpose: string | null;
  starts_at: string;
  ends_at: string;
  location: string | null;
  status: MeetingStatus;
  created_by: string;
  attendees: Array<{ member_id: string; display_name: string }>;
  external_attendees: string[];
  viewer_relation: MeetingViewerRelation;
  /** 예약값(제목·일시·장소·참석자)을 고칠 수 있는가 — 서버가 정한다. */
  can_edit_info: boolean;
  /** 회의록 **줄**을 고칠 수 있는가 — 서버가 정한다 (완료·실패·취소됨). */
  can_edit_note: boolean;
  /**
   * 안건을 **고치고 지울** 수 있는가 — **벌마다 다르다** (§4.1-6).
   * 사람 벌은 예정·취소, AI 벌은 언제나 거짓, 최종 벌은 종료·실패다.
   * 상태 게이트(「진행 중」·「정리 중」)도 서버가 이미 반영했으므로 화면이 다시 추론하지 않는다.
   */
  can_edit_agendas: MeetingTrackGates;
  /**
   * 안건을 **더할** 수 있는가 — 고치는 것과 **다른 게이트**다 (§4.1-6).
   * 사람 벌은 예정·**진행 중**·취소에 열린다: 「진행 중」에 더할 수는 있어도 고칠 수는 없다.
   */
  can_add_agenda: MeetingTrackGates;
  /** 메모 입력 칸이 서는가 — 만든 사람 × 「진행 중」. 화면이 `created_by` 로 추론하지 않는다. */
  can_write_memo: boolean;
  last_saved_at: string | null;
  /** `at_ms` 의 기준점. 예정 시각이 아니라 「진행 중」으로 옮긴 실제 시각이다. */
  started_at: string | null;
  carried_from_meeting_id: string | null;
  /** 제목이 비었을 때 합성이 낸 후보. **사람이 머리 편집에서 저장해야 제목이 된다.** */
  title_candidate: string | null;
  /** 「실패」일 때 무엇이 어긋났나. 안내 문구 옆에 그대로 붙인다. */
  failure_reason: string | null;
  /** 회의실을 안 잡은 회의는 `null` 이다. */
  room_reservation: MeetingRoomReservation | null;
};

/** 「스크립트」 탭이 읽는 원문 — 확정 발화와 회의 중 메모가 같은 시각 축에 선다 (SPEC §5.4-7·8). */
export type MeetingTranscript = {
  items: Array<{ id: string; speakerLabel: string; atMs: number; endMs: number; content: string }>;
  memos: Array<{ line_id: string; agenda_id: string; text: string; author: string | null; atMs: number | null }>;
};

export type MeetingRecord = { meeting: MeetingInfo; agendas: MeetingAgenda[] };

/* ---- 회의 자료와 공유 (SCAX-WP-005) ---- */

/** 회의를 보며 열어 놓는 파일 한 줄. **저장 위치는 나오지 않는다.** */
export type MeetingMaterial = {
  material_id: string;
  name: string;
  content_type: string;
  size: number;
  uploaded_by: string;
  uploaded_at: string;
  /**
   * 이 자료를 뗄 수 있는가 — **서버가 말한다.** 「올린 사람이 나인가」를 화면이 맞춰 보면
   * 상태 조건(「진행 중」에는 자료를 다루지 않는다)이 빠지고 규칙이 두 곳에 살게 된다.
   */
  can_detach: boolean;
};

/** 붙지 못한 파일과 그 사유 — 하나가 막혔다고 나머지를 버리지 않는다 (SPEC §10). */
export type MeetingMaterialFailure = { name: string; reason: "too_large" | "unsupported_type" };

export type MeetingMaterialUpload = { attached: MeetingMaterial[]; failed: MeetingMaterialFailure[] };

/** 고를 수 있는 사옥 회의실 (SCAX-WP-007). 예약 시스템이 없거나 닿지 않으면 목록이 **빈다**. */
export type MeetingRoom = { room_id: number; name: string; capacity: number };

/**
 * 회의실 예약이 어떻게 됐나. `booked` 면 회의의 `location` 이 그 방 이름으로 차 있다.
 * **실패해도 회의는 남는다** — 장소만 비어 있다.
 */
export type MeetingRoomReservation = {
  status: "booked" | "failed" | "cancelled" | "needs_verification";
  room_name: string | null;
  reason: string | null;
  /** 고른 방이 안 돼 **다른 방으로 잡혔다**. 그 사실을 사람에게 말해야 한다. */
  replaced?: boolean;
  /** 원래 고른 방 이름 — `replaced` 일 때만 온다. */
  requested_room_name?: string;
};

/** 「볼 수 있는 사람」 — 참석과 공유가 한 목록에 서고 `basis` 가 둘을 가른다 (SPEC §3.2-3). */
export type MeetingViewer = { member_id: string; name: string; basis: "attendee" | "share" };

/**
 * 참고 항목 읽음의 영수증 (SPEC-001 §4).
 *
 * **요청 행이 아니라 영수증이다** — `version` 이 없는 것이 그 사실이다. 읽음은 그 사람에게만
 * 있는 사실이라 요청의 회차를 움직이지 않는다.
 */
export type WorkRequestReadReceipt = {
  request_id: string;
  read: boolean;
  read_at: string;
};

/* ---- 캘린더 시간 배정 (SPEC-004) ----
   합본 조회 `GET /api/calendar?from=&to=` 한 배열이 업무와 회의를 함께 싣고 `kind` 로 가른다.
   **화면은 기간을 계산하지 않는다** — 서버가 정규화한 `span_from`·`span_to` 를 받아서 그린다 (K14). */

/**
 * 업무 하루치 시간 배정 한 줄.
 *
 * **`task_id` 가 없다** — 이 배열은 언제나 자기 업무 행 안에 실려 오므로 행이 곧 소속이다.
 * 요청한 기간과 겹치는 **살아 있는** 것만 온다(닫힌 배정·기간 밖 배정은 안 실린다).
 * `version` 은 **그 배정 자신의 회차**다 — 업무 회차와 다른 값이고, 시각 변경이 이것을 쓴다 (K8·K10).
 */
export type TaskScheduleRow = {
  schedule_id: string;
  /** 어느 날 (`YYYY-MM-DD`). */
  on_date: string;
  /** 사무실 시간대의 벽시계 (`HH:MM`). 회의의 UTC ISO 와 **타입이 다르다**. */
  starts_at: string;
  ends_at: string;
  version: number;
};

/**
 * 합본 조회의 업무 행.
 *
 * ⚠ **`start_date`·`due_date` 로 띠를 그리지 않는다.** 둘은 원본 그대로라 뒤집힌 업무에서
 * `start_date > due_date` 가 실제로 온다. 그리기도 드롭 가드도 `span_from`·`span_to` 를 쓴다 (K14).
 * 기간이 없는 업무는 `span_from`·`span_to` 가 **둘 다 `null`** 이고, 그래도 행으로는 온다 —
 * 좌측 레일이 「날짜부터」(R2)를 하려면 필요하다.
 *
 * `state` 는 화면 어휘 5종 그대로다. 다만 내부 `completion_submitted` 가 여기서 **`"done"` 으로
 * 투영**되고 행에 `derived` 가 없어 승인 대기인지 **가려지지 않는다** — 그래서 좌측 카드는
 * 상태를 내지 않는다 (K15).
 */
export type CalendarTaskRow = {
  kind: "task";
  task_id: string;
  title: string;
  state: TaskState;
  start_date: string | null;
  due_date: string | null;
  span_from: string | null;
  span_to: string | null;
  /** **업무** 회차. 날짜 조정(`PATCH /api/tasks`)이 쓴다 — 배정 회차가 아니다. */
  version: number;
  schedules: TaskScheduleRow[];
};

/**
 * 합본 조회의 회의 행.
 *
 * `starts_at`·`ends_at` 은 **UTC ISO**(`+00:00`)다 — 업무의 `on_date`+`HH:MM` 과 타입이 다르므로
 * 격자에 놓기 전에 사무실 시간대(Asia/Seoul)로 옮긴다.
 * `created_by_display_name` 은 **이 합본 조회에만** 있다 — `GET /api/meetings` 기간 갈래에는 없다 (K4).
 */
export type CalendarMeetingRow = {
  kind: "meeting";
  meeting_id: string;
  title: string | null;
  starts_at: string;
  ends_at: string;
  location: string | null;
  status: MeetingStatus;
  viewer_relation: MeetingViewerRelation;
  /** member id. **화면에 내지 않는다** — 이름은 아래 `created_by_display_name` 이다. */
  created_by: string;
  attendee_count: number;
  created_by_display_name: string;
};

export type CalendarEntry = CalendarTaskRow | CalendarMeetingRow;

/** 배정 생성·시각 변경의 응답. 여기에는 `task_id` 가 있다 — 합본 조회의 `schedules[]` 와 다르다. */
export type TaskScheduleMutation = {
  schedule_id: string;
  task_id: string;
  on_date: string;
  starts_at: string;
  ends_at: string;
  version: number;
};

/**
 * 업무 날짜가 바뀔 때 **함께 닫힌 배정의 건수와 사유** (K3).
 *
 * 날짜가 바뀌는 세 자리(업무 수정 · 시작 전이 · 조건 변경 제안 동의)의 응답에만 실린다 —
 * 날짜를 안 바꾸는 전이 넷(`/block`·`/resume`·`/complete`·`/cancel`)에는 **없다**.
 * `released_count` 가 0 이면 화면은 **아무 말도 하지 않는다.** 문구는 서버가 아니라 화면이 만든다.
 */
export type ScheduleRelease = {
  released_count: number;
  reason: "out_of_range" | "task_dates_cleared" | null;
};
