import { Button, ButtonGroup } from "ax-workspace-frontend";

export default { title: "General/ButtonGroup", component: ButtonGroup };

/** 안의 단추들이 폭을 똑같이 나눠 갖는다 */
export const Pair = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <ButtonGroup>
      <Button variant="text" label="취소" />
      <Button variant="solid" tone="primary" label="업무로 승격" />
    </ButtonGroup>
  </div>
);

/** 셋 */
export const Triple = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <ButtonGroup>
      <Button variant="text" label="나중에" />
      <Button variant="outlined" label="초안 저장" />
      <Button variant="solid" tone="primary" label="공유" />
    </ButtonGroup>
  </div>
);
