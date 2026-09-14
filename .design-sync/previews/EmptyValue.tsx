import { DataTable, EmptyValue, Td, Th } from "ax-workspace-frontend";

export default { title: "General/EmptyValue", component: EmptyValue };

/**
 * 값이 없는 칸에 남기는 것 — 공백이 아니라 대시다.
 *
 * 바퀴 11: 대시는 **말이 아니라 활자**다(어느 언어에서도 「—」다). 그래서 prop 으로 받지 않고
 * 이 부품이 들고 있는다 — `lib/labels` 의 `emptyValue` 는 「문자열을 만드는」 자리가 따로 쓴다.
 */

/** 표 안의 빈 칸 */
export const InTable = () => (
  <div className="surface-card" style={{ width: 560 }}>
    <DataTable label="업무 목록">
      <thead>
        <tr>
          <Th>업무</Th>
          <Th>담당</Th>
          <Th align="center">종료일</Th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <Td title>제품 소개서 내용 업데이트</Td>
          <Td>김지원</Td>
          <Td align="center">09월 12일</Td>
        </tr>
        <tr>
          <Td title>주간 회의록 정리</Td>
          <Td><EmptyValue /></Td>
          <Td align="center"><EmptyValue /></Td>
        </tr>
      </tbody>
    </DataTable>
  </div>
);

/** 메타 목록 안의 빈 값 */
export const InMeta = () => (
  <dl className="meta-grid" style={{ width: 400 }}>
    <div><dt>담당</dt><dd>이건학</dd></div>
    <div><dt>프로젝트</dt><dd><EmptyValue /></dd></div>
    <div><dt>요청</dt><dd><EmptyValue /></dd></div>
  </dl>
);
