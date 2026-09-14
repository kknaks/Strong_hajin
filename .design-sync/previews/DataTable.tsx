import { DataTable, Td, Th, TrOpenable, Badge, EmptyValue, Avatar } from "ax-workspace-frontend";

export default { title: "General/DataTable", component: DataTable };

/**
 * 읽는 표 — 열 수에 안 묶인다 (`DS-gaps` G-15 · G-46).
 * 새 DS 의 `.scax-task-table` 은 업무 표 전용 6열 격자라 열이 제각각인 표가 쓸 수 없다.
 */
export const History = () => (
  <div className="surface-card" style={{ width: 760 }}>
    <DataTable label="변경 기록">
      <thead>
        <tr>
          <Th>시각</Th>
          <Th>바꾼 사람</Th>
          <Th>항목</Th>
          <Th>이전</Th>
          <Th>이후</Th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <Td>09-13 14:02</Td>
          <Td>김도현</Td>
          <Td title>마감일</Td>
          <Td><EmptyValue /></Td>
          <Td>2026-09-20</Td>
        </tr>
        <tr>
          <Td>09-13 11:40</Td>
          <Td>박서진</Td>
          <Td title>상태</Td>
          <Td>시작 전</Td>
          <Td>진행 중</Td>
        </tr>
        <tr>
          <Td>09-12 17:25</Td>
          <Td>이하늘</Td>
          <Td title>담당자</Td>
          <Td>박서진</Td>
          <Td>김도현</Td>
        </tr>
      </tbody>
    </DataTable>
  </div>
);

/** 3열 — 칸 정렬은 `align`, 「제목 칸」은 `title` */
export const ShareTargets = () => (
  <div className="surface-card" style={{ width: 560 }}>
    <DataTable label="공유 대상">
      <thead>
        <tr>
          <Th>받는 사람</Th>
          <Th>권한</Th>
          <Th align="end">보낸 시각</Th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <Td title><span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><Avatar name="정민우" size="xs" />정민우</span></Td>
          <Td><Badge tone="neutral">읽기</Badge></Td>
          <Td align="end">09-13 15:10</Td>
        </tr>
        <tr>
          <Td title><span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><Avatar name="최유리" size="xs" />최유리</span></Td>
          <Td><Badge tone="accent">편집</Badge></Td>
          <Td align="end">09-13 15:10</Td>
        </tr>
      </tbody>
    </DataTable>
  </div>
);

/** 누르면 열리는 줄 — 누르는 자리가 아니면 그냥 `<tr>` 을 쓴다 */
export const Openable = () => (
  <div className="surface-card" style={{ width: 660 }}>
    <DataTable label="보낸 업무">
      <thead>
        <tr>
          <Th>업무</Th>
          <Th>받는 사람</Th>
          <Th align="center">상태</Th>
          <Th align="end">마감</Th>
        </tr>
      </thead>
      <tbody>
        <TrOpenable onClick={() => {}}>
          <Td title>9월 회의록 정리 템플릿 확정</Td>
          <Td>한지원</Td>
          <Td align="center"><Badge tone="info">진행 중</Badge></Td>
          <Td align="end">09-20</Td>
        </TrOpenable>
        <TrOpenable onClick={() => {}}>
          <Td title>THE CONNECT 회의실 예약 정책 검토</Td>
          <Td>정민우</Td>
          <Td align="center"><Badge tone="positive">완료</Badge></Td>
          <Td align="end">09-15</Td>
        </TrOpenable>
      </tbody>
    </DataTable>
  </div>
);
