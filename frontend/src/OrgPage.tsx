export function OrgPage() {
  return (
    <section className="page-surface">
      <p className="kicker">ORGANIZATION</p>
      <h2>조직</h2>
      <p>조직도·구성원·권한은 Organization &amp; Access 원장의 authorized query로 표시합니다.</p>

      <section className="surface-card organization-seam">
        <h3>내 접근 범위</h3>
        <p>
          이 화면은 fixture 구성원을 제품 데이터처럼 표시하지 않습니다. 다음 Organization &amp; Access slice가
          실제 조직·멤버십·권한 projection을 제공합니다.
        </p>
      </section>
    </section>
  );
}
