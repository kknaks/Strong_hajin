import { Avatar, AvatarEmpty } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 센다.
 */
export default { title: "General/Avatar", component: Avatar };

const row: React.CSSProperties = { display: "flex", alignItems: "center", gap: 12 };

/** 크기 다섯 단 — 20 · 24 · 32 · 40 · 80 (구 `.avatar` 값 그대로) */
export const Sizes = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <div style={row}>
      <Avatar name="김도현" size="xs" />
      <Avatar name="김도현" size="sm" />
      <Avatar name="김도현" size="md" />
      <Avatar name="김도현" size="lg" />
      <Avatar name="김도현" size="xl" />
    </div>
  </div>
);

/** 이름 옆에 서는 자리 — 옆에 이름이 이미 있으므로 아바타는 `aria-hidden` 이다 */
export const WithName = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <div style={{ ...row, marginBottom: 12 }}>
      <Avatar name="박서진" size="md" />
      <div>
        <div className="t-item">박서진</div>
        <div className="t-meta">프로덕트 · 회의록 담당</div>
      </div>
    </div>
    <div style={row}>
      <Avatar name="이하늘" size="md" />
      <div>
        <div className="t-item">이하늘</div>
        <div className="t-meta">디자인 · 주간 업무보고</div>
      </div>
    </div>
  </div>
);

/** 혼자 서는 자리 — `label` 을 주면 읽히는 자리(role=img)가 된다 */
export const Labelled = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <div style={row}>
      <Avatar name="정민우" size="md" label="정민우 담당" />
      <Avatar name="최유리" size="md" label="최유리 담당" />
      <Avatar name="한지원" size="md" label="한지원 담당" />
    </div>
  </div>
);

/** 이름을 모르는 자리 — 빈 원만 선다. 글자를 지어내지 않는다 */
export const Empty = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <div style={row}>
      <AvatarEmpty size="sm" />
      <AvatarEmpty size="md" />
      <span className="t-meta">담당자 미지정</span>
    </div>
  </div>
);
