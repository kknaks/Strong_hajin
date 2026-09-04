import type { ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Assistant bodies are provider text, never trusted HTML. Raw HTML is skipped (not parsed), only a small set of
 * elements is rendered, and `javascript:`/`data:` link targets are dropped by react-markdown's default URL
 * transform. Disallowed elements are unwrapped so their text still shows.
 */
const ALLOWED_ELEMENTS = ["p", "strong", "em", "del", "ul", "ol", "li", "a", "code", "pre", "blockquote", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td"];

function Heading({ children }: { children?: ReactNode }) {
  // Chat bodies do not carry document headings; keep the emphasis without inflating the type scale.
  return <p className="ax-md-heading">{children}</p>;
}

export function AssistantMarkdown({ body }: { body: string }) {
  return (
    <div className="ax-md">
      <Markdown
        allowedElements={ALLOWED_ELEMENTS}
        components={{
          a: ({ href, children }) => (
            <a href={href} rel="noopener noreferrer" target="_blank">
              {children}
            </a>
          ),
          h1: Heading,
          h2: Heading,
          h3: Heading,
          h4: Heading,
          h5: Heading,
          h6: Heading,
        }}
        remarkPlugins={[remarkGfm]}
        skipHtml
        unwrapDisallowed
      >
        {body}
      </Markdown>
    </div>
  );
}
