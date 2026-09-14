import { Drawer, Button, IconButton, ProgressBar, Badge } from "ax-workspace-frontend";

export default { title: "General/Drawer", component: Drawer };

/**
 * 편집·상세는 드로어. 폭은 `size` 로 갈린다 (`DS-gaps` G-10) — `lg`(840)가 기본이다.
 * 바퀴 11 이후 `closeLabel` 을 **반드시** 받는다.
 */

/** 업무 상세 (lg · 840) — 뒤에 페이지가 남아 있다 */
export const TaskDetail = () => (
  <div style={{ minHeight: 700 }}>
    <div className="page-head" style={{ padding: "24px 40px 0" }}>
      <div><h1>내 업무</h1><p>기한이 오늘까지인 업무 3건</p></div>
      <div className="page-head-actions"><Button variant="solid" tone="primary" iconBefore="plus" label="새 업무" /></div>
    </div>
    <Drawer
      closeLabel="닫기"
      footer={
        <>
          <Button variant="text" iconBefore="ban" label="취소" />
          <span className="spacer" />
          <Button label="완료 확인 요청" />
          <Button variant="solid" tone="primary" label="저장" />
        </>
      }
      headerExtra={<IconButton name="link" label="링크 복사" size={16} />}
      kicker="개인 업무 · 진행 중"
      label="업무 상세"
      onClose={() => {}}
      title="제품 소개서 내용 업데이트"
    >
      <dl className="meta-grid">
        <div><dt>담당</dt><dd>이건학</dd></div>
        <div><dt>기간</dt><dd>09월 08일 (월) – 09월 12일 (금)</dd></div>
        <div><dt>요청</dt><dd>김지원 · 회의 「9월 영업 전략」에서</dd></div>
      </dl>
      <section className="drawer-section">
        <h4>진행</h4>
        <ProgressBar ariaLabel="체크리스트 진행률" label="체크리스트" done={2} total={5} />
      </section>
      <section className="drawer-section">
        <h4>설명</h4>
        <p>2분기 실적과 신규 고객 사례 두 건을 반영해 소개서 5·6장을 다시 쓴다. 디자인 팀 검토는 목요일까지.</p>
      </section>
    </Drawer>
  </div>
);

/** 좁은 자리 (sm · 520) */
export const Narrow = () => (
  <div style={{ minHeight: 700 }}>
    <Drawer
      closeLabel="닫기"
      footer={
        <>
          <span className="spacer" />
          <Button variant="solid" tone="primary" label="저장" />
        </>
      }
      kicker="회의 자료"
      label="자료 미리보기"
      onClose={() => {}}
      size="sm"
      title="회의실 예약 정책 초안.md"
    >
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <Badge tone="outline">초안 v3</Badge>
        <Badge tone="positive">추출 완료</Badge>
      </div>
      <p style={{ fontSize: 14, lineHeight: 1.7 }}>
        THE CONNECT 회의실은 층별로 예약 단위가 다르다. 3층은 30분, 5층은 1시간이 최소 단위다.
      </p>
    </Drawer>
  </div>
);
