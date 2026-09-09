import type { Preview } from "@storybook/react-vite";

// 토큰·컴포넌트 CSS 의 주인은 styles.css 하나다(:root 가 색·타이포를 다 들고 있다).
import "../../.design-sync/fonts/pretendard.css";
import "../src/styles.css";

const preview: Preview = {
  parameters: {
    layout: "padded",
    controls: { expanded: true },
  },
};

export default preview;
