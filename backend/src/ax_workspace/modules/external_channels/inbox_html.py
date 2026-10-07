"""메일 본문 — Gmail 원문 풀기와 **렌더용 안전본** 소독 (SPEC-008 §2.1 F-3 · BE-3).

외부 누구나 보낸 HTML 을 **세션이 사는 같은 origin** 의 iframe 에 그린다. 그래서 서버가 원문(`raw`, D-28 — 그대로
보관)과 별개로 안전본을 만든다:

1. `<script>`·`on*` 이벤트 속성·`<form>`·`<iframe>`·`<object>`·`<embed>`·`<style>`·`<link>`·`<meta>` 를 지운다 — 허용
   목록(nh3/ammonia)만 남긴다. 링크는 `http(s)`·`mailto`·`tel` 만, `data:` 는 `<img>` 의 이미지 MIME 만.
2. **원격 이미지는 원래 주소로 직접 붙지 않는다**(추적 픽셀이 우리 사용자 IP·시각을 못 본다) — `src` 를 걷고 원래 주소를
   `data-ax-remote-src` 에 남긴다. 화면이 본문을 그릴 때 **자동으로** `GET /api/inbox/mail/{id}/remote-image?u=`(서버 프록시 ·
   SSRF 규칙 · 래스터와 SVG)로 바꿔 단다(SPEC-008 §4.4 v0.6.0 — 옛 「기본 차단 · 이미지 보기」 단추는 없다).
3. 인라인 `cid:` 이미지는 첨부 중계 경로(`/api/inbox/mail/{id}/attachments/{aid}`)로 바꾼다.
4. **인용은 `<details>` 로 감싼다** — 스크립트 없이 접힌다(N-4). Gmail `div.gmail_quote` · Outlook `#divRplyFwdMsg` 뒤 ·
   맨 바깥 `<blockquote>`.
5. 맨 앞에 CSP `<meta>` 를 박는다 — `default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'`. iframe 은
   `sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"`(`allow-scripts` 금지)로 프론트가 연다.

이 파일은 `fastapi`·`sqlalchemy` 를 모른다. 연동 워커(BE-2)가 수집 때 `sanitize_mail_html` 을 불러 `safe_html` 을 미리
채워도 되고, 비어 있으면 메시지함이 처음 열 때 만들어 채운다.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import html as html_lib
import re
from collections.abc import Callable
from typing import Any, Iterator
from urllib import parse as urllib_parse
from urllib.parse import urlsplit

import nh3
from lxml import etree, html as lxml_html

#: 판 2(BE 수정 판 5) — `<style>` 블록 보존 · CSS `url()`·`background` 를 프록시로 · body 배경을 감싸개로.
SAFE_HTML_VERSION = 2
CSP_META = (
    '<meta http-equiv="Content-Security-Policy" '
    "content=\"default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'\">"
)
#: 저장된 안전본의 머리 — 판이 바뀌면 옛 안전본을 버리고 다시 만든다.
SAFE_HTML_PREFIX = f'{CSP_META}<meta name="ax-safe-html" content="{SAFE_HTML_VERSION}">'
REMOTE_SRC_ATTRIBUTE = "data-ax-remote-src"
QUOTE_SUMMARY = "이전 내용 보기"

_ALLOWED_TAGS = {
    "a", "abbr", "address", "b", "bdi", "bdo", "big", "blockquote", "br", "caption", "center", "cite", "code", "col",
    "colgroup", "dd", "del", "details", "dfn", "div", "dl", "dt", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "pre", "q", "s", "samp", "small", "span", "strike",
    "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "time", "tr", "tt", "u", "ul",
    "var", "wbr", "article", "section", "header", "footer", "main", "aside", "figure", "figcaption",
}
_CLEAN_CONTENT = {"script", "style", "title", "noscript", "template", "iframe", "object", "embed", "form", "textarea",
                  "select", "button", "svg", "math", "head"}
_GENERIC_ATTRIBUTES = {"style", "align", "valign", "dir", "lang", "title", "width", "height", "bgcolor", "color",
                       "border", "cellpadding", "cellspacing", "class"}
_ATTRIBUTES = {
    "*": _GENERIC_ATTRIBUTES,
    "a": {"href", "name", "target"},
    "img": {"src", "alt", REMOTE_SRC_ATTRIBUTE},
    "td": {"colspan", "rowspan", "nowrap", "background"},
    "th": {"colspan", "rowspan", "nowrap", "scope", "background"},
    "table": {"background"},
    "tr": {"background"},
    "col": {"span"},
    "colgroup": {"span"},
    "font": {"face", "size"},
    "ol": {"start", "type"},
    "li": {"value"},
    "time": {"datetime"},
    "details": {"open"},
}
_RASTER_DATA_PREFIXES = ("data:image/png", "data:image/jpeg", "data:image/jpg", "data:image/gif", "data:image/webp")


# ── Gmail 원문(messages.get format=full) 풀기 ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MailParts:
    headers: dict[str, str]
    html: str | None
    text: str | None
    #: Content-ID(꺾쇠 없이, 소문자) → partId. 인라인 `cid:` 이미지를 첨부 중계 경로로 바꿀 때 쓴다.
    inline_parts: dict[str, str] = field(default_factory=dict)


def iter_parts(part: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield part
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            yield from iter_parts(child)


def _decode_body(data: str | None) -> str | None:
    if not data:
        return None
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return None
    return raw.decode("utf-8", errors="replace")


def part_headers(part: dict[str, Any]) -> dict[str, str]:
    return {
        str(row.get("name", "")).lower(): str(row.get("value", ""))
        for row in part.get("headers") or []
        if isinstance(row, dict)
    }


def parse_gmail_message(raw: dict[str, Any]) -> MailParts:
    payload = raw.get("payload") or {}
    html_body: str | None = None
    text_body: str | None = None
    inline: dict[str, str] = {}
    for part in iter_parts(payload):
        mime = str(part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        headers = part_headers(part)
        content_id = headers.get("content-id", "").strip().strip("<>").lower()
        if content_id and body.get("attachmentId"):
            inline[content_id] = str(part.get("partId") or "")
        if part.get("filename"):
            continue
        if mime == "text/html" and html_body is None:
            html_body = _decode_body(body.get("data"))
        elif mime == "text/plain" and text_body is None:
            text_body = _decode_body(body.get("data"))
    return MailParts(part_headers(payload), html_body, text_body, inline)


def gmail_attachment_aid(integration_id: str, message_id: str, part_id: str) -> str:
    """BE-2 수집(`sync_messages.normalize_gmail_message`)과 같은 식 — partId 가 안정 키다."""
    return hashlib.sha256(f"{integration_id}:{message_id}:{part_id}".encode("utf-8")).hexdigest()


def split_addresses(value: str | None) -> list[str]:
    """머리 값 `A <a@x>, "B, C" <b@x>` → 각 주소 문자열. 쉼표가 따옴표 안에 있으면 가르지 않는다."""
    if not value:
        return []
    from email.utils import getaddresses

    return [
        (f"{name} <{address}>" if name else address)
        for name, address in getaddresses([value])
        if address
    ]


def bare_address(value: str) -> str:
    from email.utils import parseaddr

    return parseaddr(value)[1].strip().lower()


# ── 소독 ──────────────────────────────────────────────────────────────────────────────────


def _is_remote(url: str) -> bool:
    return urlsplit(url.strip()).scheme.lower() in {"http", "https"}


def _wrap_quotes(root: Any) -> None:
    targets: list[Any] = []
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        classes = set((element.get("class") or "").split())
        if element.tag == "div" and classes & {"gmail_quote", "gmail_extra", "yahoo_quoted", "moz-cite-prefix"}:
            targets.append(element)
        elif element.tag == "blockquote" and not any(
            ancestor.tag == "blockquote" or ancestor in targets for ancestor in element.iterancestors()
        ):
            targets.append(element)
    for element in targets:
        if any(ancestor in targets for ancestor in element.iterancestors()):
            continue
        parent = element.getparent()
        if parent is None:
            continue
        details = etree.Element("details")
        summary = etree.SubElement(details, "summary")
        summary.text = QUOTE_SUMMARY
        parent.replace(element, details)
        details.append(element)


def _rewrite_images(root: Any, cid_urls: dict[str, str]) -> None:
    for image in root.iter("img"):
        src = (image.get("src") or "").strip()
        for name in list(image.attrib):
            if name.lower() == REMOTE_SRC_ATTRIBUTE:
                del image.attrib[name]
        if src.lower().startswith("cid:"):
            target = cid_urls.get(src[4:].strip().strip("<>").lower())
            if target:
                image.set("src", target)
            else:
                image.attrib.pop("src", None)
        elif _is_remote(src):
            image.attrib.pop("src", None)
            image.set(REMOTE_SRC_ATTRIBUTE, src)
        elif not src.lower().startswith(_RASTER_DATA_PREFIXES):
            image.attrib.pop("src", None)


_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
_CSS_FORBIDDEN = ("@import", "expression(", "javascript:", "behavior:", "-moz-binding", "vbscript:")
_PROXIED = "/api/inbox/mail/"


def _css_url(target: str, remote_url: Callable[[str], str] | None) -> str | None:
    target = html_lib.unescape(target.strip())
    lowered = target.lower()
    if lowered.startswith(_RASTER_DATA_PREFIXES) or lowered.startswith(_PROXIED):
        return target
    if remote_url is not None and _is_remote(target):
        return remote_url(target)
    return None


def sanitize_css(css: str, remote_url: Callable[[str], str] | None) -> str:
    """CSS 한 덩이 — 원격을 부르는 길을 닫되 **지우지 않고 바꾼다**(BE 수정 판 5). `url()` 은 원격이면 우리 이미지 프록시로,
    래스터 data: 는 그대로, 그 밖은 `none`. `@import`·`expression()`·`javascript:` 가 든 선언은 그 줄만 걷는다.
    `<` 는 남기지 않는다 — `<style>` 밖으로 빠져나가지 못하게."""
    css = css.replace("<", " ").replace("\\", "")
    kept = []
    for chunk in re.split(r"(;|\{|\})", css):
        if any(token in chunk.lower() for token in _CSS_FORBIDDEN):
            continue
        kept.append(_CSS_URL.sub(lambda match: (f"url('{proxied}')" if (proxied := _css_url(match.group(2), remote_url)) else "none"), chunk))
    return "".join(kept)


def _rewrite_backgrounds(root: Any, remote_url: Callable[[str], str] | None) -> None:
    """`<td background=…>`·`<table background>` — 배너를 그리는 옛 방식. 지우지 않고 프록시 주소로 바꾼다."""
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        value = element.get("background")
        if value is not None:
            proxied = _css_url(value, remote_url)
            if proxied:
                element.set("background", proxied)
            else:
                del element.attrib["background"]
        style = element.get("style")
        if style and "url(" in style.lower():
            element.set("style", sanitize_css(style, remote_url))


def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    lowered = value.strip().lower()
    if attribute == "background":
        return value if lowered.startswith(_PROXIED) or lowered.startswith(_RASTER_DATA_PREFIXES) else None
    if attribute == "href":
        return value if lowered.startswith(("http://", "https://", "mailto:", "tel:", "#")) else None
    if attribute == "src":
        if tag != "img":
            return None
        if lowered.startswith(_RASTER_DATA_PREFIXES) or lowered.startswith("/api/inbox/mail/"):
            return value
        return None
    if attribute == REMOTE_SRC_ATTRIBUTE:
        return value if _is_remote(value) else None
    if attribute == "style":
        # 원격 `url()` 은 앞에서 프록시로 바뀌었다. 남은 위험한 것(@import·expression·javascript:)은 그 선언만 걷고
        # 나머지 레이아웃(폭·정렬·색)은 남긴다 — 통째로 지우면 메일이 좁아지고 색이 빠진다(BE 수정 판 5). CSP 가 한 겹 더.
        return sanitize_css(value, None)
    if attribute == "target":
        return "_blank"
    return value


def sanitize_mail_html(
    document: str, *, cid_urls: dict[str, str] | None = None, remote_url: Callable[[str], str] | None = None
) -> str:
    """원문 HTML → 렌더용 안전본(CSP meta + 소독한 `<style>` + 소독한 body). 같은 입력이면 같은 출력이다.

    `remote_url` 은 원격 주소 → 우리 이미지 프록시 주소(그 메일의 `remote-image?u=`). 있으면 CSS `url()`·`background`
    속성을 지우지 않고 그 주소로 바꾼다. `<img>` 는 종전대로 `data-ax-remote-src` 로 둔다(화면이 프록시로 그린다).
    """
    fragment = ""
    styles: list[str] = []
    wrapper_style = ""
    if document and document.strip():
        try:
            root = lxml_html.document_fromstring(document)
        except (etree.ParserError, ValueError):
            root = None
        if root is not None:
            # 뉴스레터는 `<style>`(글꼴·링크 색·모바일 @media)에 기댄다 — 위험한 것만 걷고 남긴다(BE 수정 판 5).
            styles = [sanitize_css(element.text or "", remote_url) for element in root.iter("style")]
            body = root.find("body")
            container = body if body is not None else root
            if body is not None:
                # body 의 배경색·글꼴은 감싸개 div 로 옮긴다 — body 는 안전본에 없다.
                bgcolor = (body.get("bgcolor") or "").strip()
                wrapper_style = sanitize_css(
                    ((f"background-color:{bgcolor};" if re.fullmatch(r"#?[0-9A-Za-z]{1,20}", bgcolor) else "") + (body.get("style") or "")),
                    remote_url,
                )
            _rewrite_images(container, cid_urls or {})
            _rewrite_backgrounds(container, remote_url)
            _wrap_quotes(container)
            fragment = "".join(
                [html_lib.escape(container.text or "")]
                + [lxml_html.tostring(child, encoding="unicode") for child in container]
            )
    cleaned = nh3.clean(
        fragment,
        tags=_ALLOWED_TAGS,
        clean_content_tags=_CLEAN_CONTENT,
        attributes=_ATTRIBUTES,
        attribute_filter=_attribute_filter,
        url_schemes={"http", "https", "mailto", "tel", "data"},
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    style_block = "".join(f"<style>{css}</style>" for css in styles if css.strip())
    if wrapper_style.strip():
        cleaned = f'<div style="{html_lib.escape(wrapper_style, quote=True)}">{cleaned}</div>'
    return f"{SAFE_HTML_PREFIX}{style_block}{cleaned}"


def text_to_safe_html(text: str) -> str:
    """HTML 이 없는 메일 — 글자를 그대로 이스케이프해 `<pre>` 로. 인용(`>` 줄)은 접는다."""
    lines = (text or "").splitlines()
    head: list[str] = []
    quote: list[str] = []
    for line in lines:
        (quote if quote or line.startswith(">") else head).append(line)
    body = f'<pre style="white-space:pre-wrap;font-family:inherit">{html_lib.escape(chr(10).join(head))}</pre>'
    if quote:
        body += (
            f"<details><summary>{QUOTE_SUMMARY}</summary>"
            f'<pre style="white-space:pre-wrap;font-family:inherit">{html_lib.escape(chr(10).join(quote))}</pre></details>'
        )
    return f"{SAFE_HTML_PREFIX}{body}"


def remote_image_urls(safe_html: str) -> set[str]:
    """안전본에 «실제로 있는» 원격 이미지 주소 — 이미지 프록시는 이 집합만 받는다(N-5)."""
    if not safe_html:
        return set()
    try:
        root = lxml_html.document_fromstring(safe_html)
    except (etree.ParserError, ValueError):
        return set()
    proxied = {
        urllib_parse.unquote(match)
        for match in re.findall(r"/api/inbox/mail/[0-9a-f-]+/remote-image\?u=([^\"')\s&]+)", html_lib.unescape(safe_html))
    }
    return proxied | {
        html_lib.unescape(value).strip()
        for value in (image.get(REMOTE_SRC_ATTRIBUTE) for image in root.iter("img"))
        if value
    }


#: 맥락 조합(SPEC-008 §4.8 ②)이 싣는 메일 글자의 상한 — 긴 뉴스레터 한 통이 대화 프롬프트를 먹지 않게.
SAFE_TEXT_LIMIT = 20_000


def safe_html_text(safe_html: str, *, limit: int = SAFE_TEXT_LIMIT) -> str:
    """**소독한 안전본에서 글자만** 뽑는다 — HTML 아님 · 인용 접기(`<details>`)는 뺀다 (SPEC-008 §4.8 ② · OQ-819).

    `<style>` 은 글자가 아니라 버린다. 문단·줄바꿈 자리는 줄로 남기고 빈 줄은 하나로 줄인다.
    """
    body = (safe_html or "").removeprefix(SAFE_HTML_PREFIX)
    if not body.strip():
        return ""
    try:
        root = lxml_html.fragment_fromstring(body, create_parent="div")
    except (etree.ParserError, ValueError):
        return ""
    for element in list(root.iter("details", "style", "script", "head", "title")):
        if element.getparent() is not None:
            # `drop_tree` 는 꼬리 글자(인용 뒤에 이어진 본문)를 남기고 요소만 걷는다.
            element.drop_tree()
    for element in root.iter("br"):
        element.tail = "\n" + (element.tail or "")
    for element in root.iter("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"):
        element.tail = "\n" + (element.tail or "")
    text = root.text_content()
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return collapsed[:limit]
