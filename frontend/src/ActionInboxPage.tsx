export function ActionInboxPage() {
  return (
    <section className="page-surface">
      <p className="kicker">ACTIONS</p>
      <h2>판단</h2>
      <p>
        업무 요청과 배정 수락·협의·거절은 별도 WorkRequest 및 ActionItem 원장에서 처리합니다.
      </p>

      <section className="surface-card decision-empty-state">
        <h3>확인이 필요한 요청</h3>
        <p>
          이 화면은 기존 기술 spike의 범용 실행 inbox를 표시하지 않습니다. 다음 application slice에서
          권한이 있는 WorkRequest 판단만 연결합니다.
        </p>
      </section>
    </section>
  );
}
