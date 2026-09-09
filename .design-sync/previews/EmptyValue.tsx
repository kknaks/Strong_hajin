import { EmptyValue } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/EmptyValue", component: EmptyValue };


/** 값이 없는 칸은 공백이 아니라 대시 (v2 12) — 테이블 안에서 */
export const InTable = () => (
  <table className="plain-table" style={{ width: 560 }}>
    <thead>
      <tr><th>업무</th><th>담당</th><th className="center">종료일</th></tr>
    </thead>
    <tbody>
      <tr><td className="title-cell">제품 소개서 내용 업데이트</td><td>김지원</td><td className="center">09월 12일</td></tr>
      <tr><td className="title-cell">주간 회의록 정리</td><td><EmptyValue /></td><td className="center"><EmptyValue /></td></tr>
    </tbody>
  </table>
);

/** 메타 그리드 안에서 */
export const InMetaGrid = () => (
  <dl className="meta-grid" style={{ width: 400 }}>
    <div><dt>담당</dt><dd>이건학</dd></div>
    <div><dt>기간</dt><dd><EmptyValue /></dd></div>
    <div><dt>참조</dt><dd><EmptyValue /></dd></div>
  </dl>
);
