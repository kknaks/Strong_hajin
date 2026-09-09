import { Drawer, Icon, ProgressBar } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/Drawer", component: Drawer };


/** 업무 상세 드로어 (840) — 편집·상세는 드로어 (v2 14). 뒤에 페이지가 남아 있다 */
export const TaskDetail = () => (
  <div style={{ minHeight: 700 }}>
    <div className="page-head" style={{ padding: "24px 40px 0" }}>
      <div><h1>내 업무</h1><p>기한이 오늘까지인 업무 3건</p></div>
      <div className="page-head-actions"><button className="btn primary" type="button"><Icon name="plus" /> 새 업무</button></div>
    </div>
    <Drawer
      label="업무 상세"
      kicker="개인 업무 · 진행 중"
      title="제품 소개서 내용 업데이트"
      onClose={() => {}}
      footer={
        <>
          <button className="btn ghost" type="button"><Icon name="ban" size={14} /> 취소</button>
          <span className="spacer" />
          <button className="btn" type="button">완료 확인 요청</button>
          <button className="btn primary" type="button">저장</button>
        </>
      }
    >
      <dl className="meta-grid">
        <div><dt>담당</dt><dd>이건학</dd></div>
        <div><dt>기간</dt><dd>09월 08일 (월) – 09월 12일 (금)</dd></div>
        <div><dt>요청</dt><dd>김지원 · 회의 「9월 영업 전략」에서</dd></div>
      </dl>
      <section className="drawer-section">
        <h4>진행</h4>
        <ProgressBar label="체크리스트" done={2} total={5} />
      </section>
      <section className="drawer-section">
        <h4>설명</h4>
        <p>2분기 실적과 신규 고객 사례 두 건을 반영해 소개서 5·6장을 다시 쓴다. 디자인 팀 검토는 목요일까지.</p>
      </section>
    </Drawer>
  </div>
);
