import type { ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AnswerResource } from "../viewModels";

/**
 * Assistant bodies are provider text, never trusted HTML. Raw HTML is skipped (not parsed), only a small set of
 * elements is rendered, and `javascript:`/`data:` link targets are dropped by react-markdown's default URL
 * transform. Disallowed elements are unwrapped so their text still shows.
 */
const ALLOWED_ELEMENTS = ["p", "strong", "em", "del", "ul", "ol", "li", "a", "code", "pre", "blockquote", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td"];
const RESOURCE_LINK_PREFIX = "#scax-resource-";
const INTERNAL_RESOURCE_PATH = /^\/api\/(tasks|meetings|work-requests)\/([^/]+)\/?$/;
const CONTENT_PATH = /^\/api\/(?:tasks\/[^/]+\/materials\/[^/]+|meetings\/[^/]+\/materials\/[^/]+|work-requests\/[^/]+\/attachments\/[^/]+|daily-reports\/[^/]+\/materials\/[^/]+|material-folders\/[^/]+\/materials\/[^/]+)\/content\/?$/;

type InternalLinkResolution =
  | { kind: "external" }
  | { kind: "blocked" }
  | { kind: "resource"; resource: AnswerResource };

function resolveInternalLink(href: string, resources: AnswerResource[]): InternalLinkResolution {
  const base = typeof window === "undefined" ? "http://scax.local" : window.location.origin;
  let url: URL;
  try {
    url = new URL(href, `${base}/`);
  } catch {
    return { kind: "blocked" };
  }
  const hasExplicitOrigin = /^[a-z][a-z\d+.-]*:/i.test(href) || href.startsWith("//");
  if (hasExplicitOrigin && url.origin !== base) return { kind: "external" };
  if (!url.pathname.startsWith("/api/")) return { kind: "external" };
  // These are authorized file responses, not JSON resource projections. Preserve the original URL, query, and fragment.
  if (CONTENT_PATH.test(url.pathname)) return { kind: "external" };

  const match = url.pathname.match(INTERNAL_RESOURCE_PATH);
  if (!match) return { kind: "blocked" };
  const resourceType = match[1] === "tasks" ? "task" : match[1] === "meetings" ? "meeting" : "work_request";
  let resourceId: string;
  try {
    resourceId = decodeURIComponent(match[2]);
  } catch {
    return { kind: "blocked" };
  }
  const resource = resources.find((item) => item.resource_type === resourceType && item.resource_id === resourceId);
  return resource ? { kind: "resource", resource } : { kind: "blocked" };
}

type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
};

function resourceTitleLinkPlugin(resources: AnswerResource[]) {
  const byTitle = new Map<string, AnswerResource>();
  for (const resource of resources) {
    const title = resource.title.trim();
    if (title.length >= 2 && !byTitle.has(title)) byTitle.set(title, resource);
  }
  const entries = [...byTitle.entries()].sort(([left], [right]) => right.length - left.length);

  return () => (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode, protectedText = false) => {
      if (!node.children) return;
      const protectedChildren = protectedText || node.type === "link" || node.type === "inlineCode" || node.type === "code";
      node.children = node.children.flatMap((child) => {
        if (protectedChildren || child.type !== "text" || !child.value) {
          visit(child, protectedChildren);
          return [child];
        }
        const result: MarkdownNode[] = [];
        let cursor = 0;
        while (cursor < child.value.length) {
          let match: { index: number; title: string; resource: AnswerResource } | null = null;
          for (const [title, resource] of entries) {
            const index = child.value.indexOf(title, cursor);
            if (index < 0 || (match && index > match.index)) continue;
            if (!match || index < match.index || title.length > match.title.length) match = { index, title, resource };
          }
          if (!match) {
            result.push({ type: "text", value: child.value.slice(cursor) });
            break;
          }
          if (match.index > cursor) result.push({ type: "text", value: child.value.slice(cursor, match.index) });
          result.push({
            type: "link",
            url: `${RESOURCE_LINK_PREFIX}${encodeURIComponent(match.resource.reference_id)}`,
            children: [{ type: "text", value: match.title }],
          });
          cursor = match.index + match.title.length;
        }
        return result;
      });
    };
    visit(tree);
  };
}

function Heading({ children }: { children?: ReactNode }) {
  // Chat bodies do not carry document headings; keep the emphasis without inflating the type scale.
  return <p className="ax-md-heading">{children}</p>;
}

export function AssistantMarkdown({
  body,
  resources = [],
  onOpenResource,
}: {
  body: string;
  resources?: AnswerResource[];
  onOpenResource?: (resource: AnswerResource) => void;
}) {
  const byReference = new Map(resources.map((resource) => [resource.reference_id, resource]));
  return (
    <div className="ax-md">
      <Markdown
        allowedElements={ALLOWED_ELEMENTS}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith(RESOURCE_LINK_PREFIX)) {
              let referenceId = "";
              try {
                referenceId = decodeURIComponent(href.slice(RESOURCE_LINK_PREFIX.length));
              } catch {
                return <span>{children}</span>;
              }
              const resource = byReference.get(referenceId);
              if (resource && onOpenResource) {
                return <button className="ax-inline-resource-link" onClick={() => onOpenResource(resource)} type="button">{children}</button>;
              }
              return <span>{children}</span>;
            }
            if (href) {
              const resolution = resolveInternalLink(href, resources);
              if (resolution.kind === "resource") {
                return onOpenResource
                  ? <button className="ax-inline-resource-link" onClick={() => onOpenResource(resolution.resource)} type="button">{children}</button>
                  : <span>{children}</span>;
              }
              if (resolution.kind === "blocked") return <span>{children}</span>;
            }
            return <a href={href} rel="noopener noreferrer" target="_blank">{children}</a>;
          },
          h1: Heading,
          h2: Heading,
          h3: Heading,
          h4: Heading,
          h5: Heading,
          h6: Heading,
        }}
        remarkPlugins={[remarkGfm, resourceTitleLinkPlugin(resources)]}
        skipHtml
        unwrapDisallowed
      >
        {body}
      </Markdown>
    </div>
  );
}
