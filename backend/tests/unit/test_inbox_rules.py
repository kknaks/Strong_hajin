"""메시지함의 규칙 — HTML 소독 · 이미지 프록시 SSRF · 커서 · `Re:` · 카톡 첨부 상태 · 프로필 이미지 판정 (SPEC-008 §2.1·§4.4·§4.6·§4.7)."""
from __future__ import annotations

from datetime import UTC, datetime
import socket

import pytest

from ax_workspace.modules.external_channels.inbox import (
    InvalidInboxRequest,
    RemoteImageRejected,
    decode_cursor,
    encode_cursor,
    reply_subject,
)
from ax_workspace.modules.external_channels.inbox_html import (
    CSP_META,
    SAFE_HTML_PREFIX,
    sanitize_css,
    parse_gmail_message,
    remote_image_urls,
    sanitize_mail_html,
    split_addresses,
    text_to_safe_html,
)
from ax_workspace.modules.external_channels.kakao_ingest import KakaoAttachmentInput, _initial_state
from ax_workspace.modules.organization_access.profile_settings import sniff_image
from ax_workspace.platform.external_inbox_upstream import SafeImageFetcher, address_is_public


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)</script>",
        '<img src=x onerror="alert(1)">',
        '<a href="javascript:alert(1)">x</a>',
        '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        '<iframe src="https://evil"></iframe>',
        '<object data="x"></object><embed src="x">',
        '<form action="https://evil"><button>go</button></form>',
        '<svg onload="alert(1)"><script>1</script></svg>',
        '<div style="background:url(https://track.example/p)">x</div>',
        '<style>@import "https://evil/x.css";</style>',
        '<meta http-equiv="refresh" content="0;url=https://evil">',
        '<base href="https://evil/">',
        '<link rel="stylesheet" href="https://evil/x.css">',
    ],
)
def test_sanitizer_drops_every_active_or_tracking_construct(hostile: str) -> None:
    safe = sanitize_mail_html(f"<html><body><p>본문</p>{hostile}</body></html>")
    body = safe[len(SAFE_HTML_PREFIX):]
    for token in ("<script", "onerror", "onload", "javascript:", "<iframe", "<object", "<embed", "<form", "<svg",
                  "url(", "@import", "<meta", "<base", "<link", "data:text"):
        assert token not in body.lower(), (hostile, body)
    assert "본문" in body


def test_sanitizer_blocks_remote_images_maps_cid_and_folds_quotes() -> None:
    safe = sanitize_mail_html(
        '<p>hi</p><img src="https://img.example/a.png" alt="a"><img src="cid:Part1@x">'
        '<img src="data:image/png;base64,AAAA"><blockquote>quoted</blockquote>',
        cid_urls={"part1@x": "/api/inbox/mail/m1/attachments/abc"},
    )
    assert safe.startswith(CSP_META) and "default-src 'none'" in CSP_META
    assert 'data-ax-remote-src="https://img.example/a.png"' in safe and '<img src="https://' not in safe
    assert 'src="/api/inbox/mail/m1/attachments/abc"' in safe
    assert 'src="data:image/png;base64,AAAA"' in safe
    assert "<details><summary>" in safe and "quoted" in safe
    assert remote_image_urls(safe) == {"https://img.example/a.png"}


def test_plain_text_mail_is_escaped_and_quotes_fold() -> None:
    safe = text_to_safe_html("안녕 <b>\n> 예전")
    assert "&lt;b&gt;" in safe and "<details>" in safe and "<b>" not in safe


def test_gmail_payload_parts_and_addresses() -> None:
    import base64

    raw = {"payload": {"headers": [{"name": "To", "value": '"Kim, A" <a@x.com>, b@x.com'}], "parts": [
        {"partId": "0", "mimeType": "text/plain", "filename": "", "body": {"data": base64.urlsafe_b64encode("평문".encode()).decode()}},
        {"partId": "1", "mimeType": "image/png", "filename": "", "headers": [{"name": "Content-ID", "value": "<IMG@1>"}],
         "body": {"attachmentId": "A"}},
    ]}}
    parts = parse_gmail_message(raw)
    assert parts.text == "평문" and parts.html is None and parts.inline_parts == {"img@1": "1"}
    assert split_addresses(parts.headers["to"]) == ["Kim, A <a@x.com>", "b@x.com"]


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.3.4", False),
        ("192.168.0.1", False),
        ("169.254.169.254", False),  # 클라우드 메타데이터
        ("100.64.0.1", False),  # CGNAT
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_ssrf_address_rule(address: str, public: bool) -> None:
    assert address_is_public(address) is public


def _resolver(*addresses: str):
    def resolve(host, port, type=0):  # noqa: A002 — getaddrinfo 모양
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]

    return resolve


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://x.example/a.png", "gopher://x", "https://user:pw@x.example/a.png", "http://x.example:22/a.png"],
)
def test_image_fetcher_refuses_non_http_urls_before_any_lookup(url: str) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("must not resolve")

    with pytest.raises(RemoteImageRejected):
        SafeImageFetcher(resolver=explode).fetch(url)


def test_image_fetcher_refuses_a_name_that_resolves_to_any_private_address() -> None:
    with pytest.raises(RemoteImageRejected):
        SafeImageFetcher(resolver=_resolver("127.0.0.1")).fetch("http://localhost/a.png")
    with pytest.raises(RemoteImageRejected):
        SafeImageFetcher(resolver=_resolver("93.184.216.34", "169.254.169.254")).fetch("https://rebind.example/a.png")


def test_cursor_round_trip_and_garbage() -> None:
    at = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert decode_cursor(encode_cursor(at, "abc")) == (at, "abc")
    assert decode_cursor(None) is None
    with pytest.raises(InvalidInboxRequest):
        decode_cursor("!!!")


def test_reply_subject_is_re_once() -> None:
    assert reply_subject("견적") == "Re: 견적"
    assert reply_subject("RE: 견적") == "RE: 견적"
    assert reply_subject(None) == "Re:"


@pytest.mark.parametrize(
    ("attachment", "state"),
    [
        ({"kind": "image", "seq": 0, "size": 10}, "pending"),
        ({"kind": "album", "seq": 0}, "pending"),
        ({"kind": "file", "seq": 0, "size": 50 * 1024 * 1024 + 1}, "too_large"),
        ({"kind": "image", "seq": 0, "expired": True}, "expired"),
        ({"kind": "video", "seq": 0}, "not_stored"),
        ({"kind": "audio", "seq": 0}, "not_stored"),
        ({"kind": "sticker", "seq": 0}, "not_stored"),
    ],
)
def test_kakao_attachment_initial_state(attachment: dict, state: str) -> None:
    assert _initial_state(KakaoAttachmentInput(**attachment)) == state


def test_profile_image_is_judged_by_its_bytes() -> None:
    assert sniff_image(b"\x89PNG\r\n\x1a\n...") == "image/png"
    assert sniff_image(b"\xff\xd8\xff\xe0...") == "image/jpeg"
    assert sniff_image(b"GIF89a") is None
    assert sniff_image(b"<svg/>") is None



# ── BE 수정 판 5 — 배경 이미지·레이아웃을 지우지 않고 바꾼다 ───────────────────────────────────


def _proxy(url: str) -> str:
    from urllib.parse import quote

    return f"/api/inbox/mail/11111111-1111-1111-1111-111111111111/remote-image?u={quote(url, safe='')}"


def test_backgrounds_and_css_urls_are_proxied_not_dropped() -> None:
    document = (
        '<html><head><style>.hero{background-image:url("https://img.example/hero.png");color:#123}'
        "@import url(https://evil/x.css); .x{width:expression(alert(1))} @media (max-width:600px){.w{width:100%!important}}"
        "</style></head>"
        '<body bgcolor="#F2F6F9" style="font-family:Noto Sans">'
        '<table background="https://img.example/bg.jpg" width="756" align="center" cellpadding="0"><tr>'
        '<td style="width:600px;background:url(https://img.example/td.png) no-repeat;color:#000;text-align:center">배너</td>'
        '<td style="background:url(javascript:alert(1))">x</td>'
        "</tr></table></body></html>"
    )
    safe = sanitize_mail_html(document, remote_url=_proxy)
    assert safe.startswith(SAFE_HTML_PREFIX)
    assert 'background="/api/inbox/mail/11111111-1111-1111-1111-111111111111/remote-image?u=https%3A%2F%2Fimg.example%2Fbg.jpg"' in safe
    assert "width:600px" in safe and "text-align:center" in safe  # 레이아웃 선언은 남는다
    assert 'width="756"' in safe and 'align="center"' in safe and 'cellpadding="0"' in safe
    assert "@media" in safe and "color:#123" in safe  # <style> 블록이 남는다
    assert "@import" not in safe and "expression(" not in safe and "javascript:" not in safe
    assert "background-color:#F2F6F9" in safe  # body 배경은 감싸개로
    assert remote_image_urls(safe) >= {"https://img.example/hero.png", "https://img.example/bg.jpg", "https://img.example/td.png"}
    assert "https://img.example/" not in safe.replace("u=https%3A%2F%2Fimg.example", "")  # 원격 주소가 직접 남지 않는다


def test_css_cannot_escape_the_style_element() -> None:
    css = sanitize_css('a{color:red}</style><script>alert(1)</script>', None)
    assert "<" not in css


def test_redirects_are_followed_with_an_ssrf_check_on_every_hop(monkeypatch) -> None:
    from ax_workspace.platform import external_inbox_upstream as upstream

    class Response:
        def __init__(self, status, headers, body=b""):
            self.status, self._headers, self._body = status, headers, body

        def getheader(self, name, default=None):
            return self._headers.get(name, default)

        def read(self, limit):
            return self._body[:limit]

    plan = {
        "cdn.example": Response(302, {"Location": "https://img2.example/real.png"}),
        "img2.example": Response(200, {"Content-Type": "application/octet-stream"}, b"\x89PNG\r\n\x1a\n" + b"x" * 10),
        "hop.example": Response(302, {"Location": "http://internal.example/admin"}),
    }

    class Connection:
        def __init__(self, host, address, port, *, timeout):
            self.host = host

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return plan[self.host]

        def close(self):
            pass

    monkeypatch.setattr(upstream, "_PinnedHTTPSConnection", Connection)
    monkeypatch.setattr(upstream, "_PinnedHTTPConnection", Connection)
    addresses = {"cdn.example": "93.184.216.34", "img2.example": "93.184.216.35", "hop.example": "93.184.216.36", "internal.example": "10.0.0.5"}
    fetcher = SafeImageFetcher(resolver=lambda host, port, type: [(0, 0, 0, "", (addresses[host], port))])
    data, content_type = fetcher.fetch("https://cdn.example/track?id=1")
    assert content_type == "image/png" and data.startswith(b"\x89PNG")  # 이름이 틀린 type 도 바이트로 판별
    with pytest.raises(RemoteImageRejected):
        fetcher.fetch("https://hop.example/x")  # 리다이렉트가 사설 주소로 가면 그 홉에서 거절
