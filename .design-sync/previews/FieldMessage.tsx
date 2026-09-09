import { FieldMessage } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/FieldMessage", component: FieldMessage };


const field: React.CSSProperties = { width: 320 };

/** 헬퍼 — 필드 아래 한 줄 */
export const Help = () => (
  <div className="field" style={field}>
    <label htmlFor="fm-title">제목</label>
    <input id="fm-title" defaultValue="제품 소개서 내용 업데이트" aria-describedby="fm-title-help" />
    <FieldMessage id="fm-title-help" help="회의록·보고서에서 이 이름으로 찾습니다." />
  </div>
);

/** 에러 — 보더만 갈아입고 헬퍼를 교체한다 (v2 09) */
export const Error = () => (
  <div className="field" style={field}>
    <label htmlFor="fm-due">종료일</label>
    <input id="fm-due" defaultValue="2026/09/05" aria-invalid="true" aria-describedby="fm-due-msg" />
    <FieldMessage id="fm-due-msg" help="시작일 이후여야 합니다." error="종료일이 시작일보다 앞섭니다." />
  </div>
);

/** 둘 다 없으면 아무것도 그리지 않는다 */
export const Nothing = () => (
  <div className="field" style={field}>
    <label htmlFor="fm-owner">담당</label>
    <input id="fm-owner" placeholder="이름으로 검색" />
    <FieldMessage />
  </div>
);
