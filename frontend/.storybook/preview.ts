import type { Preview } from "@storybook/react-vite";

// 제품과 같은 로컬 글꼴·토큰·컴포넌트 스타일을 사용한다.
import "../src/styles/index.css";

const preview: Preview = {
  parameters: {
    layout: "padded",
    controls: { expanded: true },
  },
};

export default preview;
