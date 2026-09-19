import asyncio
import ipaddress
import json
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from agents import function_tool

from agent_tools.web_search import (
    register_fetched_source,
)

from app_settings import (
    get_int_setting,
)


FETCH_TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2_000_000

DEFAULT_MAX_CHARS = 18_000
MAX_CHARS_LIMIT = 30_000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0 Safari/537.36 "
    "LocalAgentWebFetch/1.0"
)

ALLOWED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/xml",
)


class UnsafeURL(ValueError):
    pass


def _normalize_url(url: str) -> str:
    url = (url or "").strip()

    parts = urlsplit(url)

    if parts.scheme.lower() not in {"http", "https"}:
        raise UnsafeURL(
            "只允许访问 http:// 或 https:// 公网地址。"
        )

    if not parts.hostname:
        raise UnsafeURL(
            "URL 缺少有效主机名。"
        )

    # 去掉 fragment，避免同一页面重复抓取。
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc,
            parts.path or "/",
            parts.query,
            "",
        )
    )


def _ip_is_public(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False

    # is_global 会排除 loopback/private/link-local/
    # multicast/reserved/unspecified 等非公网地址。
    return bool(ip.is_global)


def _validate_public_host(hostname: str) -> None:
    host = (hostname or "").strip().lower().rstrip(".")

    if not host:
        raise UnsafeURL(
            "URL 主机名为空。"
        )

    blocked_names = {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
    }

    if (
        host in blocked_names
        or host.endswith(".localhost")
        or host.endswith(".local")
        or host.endswith(".internal")
    ):
        raise UnsafeURL(
            "禁止访问本机或局域网主机。"
        )

    # 如果本身就是 IP，直接校验。
    try:
        ipaddress.ip_address(host)

        if not _ip_is_public(host):
            raise UnsafeURL(
                "禁止访问私网、回环、链路本地或保留 IP。"
            )

        return

    except ValueError:
        pass

    # 域名先解析，所有解析结果都必须是公网 IP。
    try:
        infos = socket.getaddrinfo(
            host,
            None,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise UnsafeURL(
            f"域名解析失败：{error}"
        ) from error

    addresses = {
        info[4][0]
        for info in infos
        if info
        and len(info) >= 5
        and info[4]
    }

    if not addresses:
        raise UnsafeURL(
            "域名没有可用 IP 地址。"
        )

    unsafe = [
        address
        for address in addresses
        if not _ip_is_public(address)
    ]

    if unsafe:
        raise UnsafeURL(
            "域名解析到了非公网地址，"
            "为避免访问本机或内网资源，已阻止请求。"
        )


def _validate_public_url(url: str) -> str:
    normalized = _normalize_url(url)
    parts = urlsplit(normalized)

    _validate_public_host(
        parts.hostname or ""
    )

    # 显式阻止带用户名/密码的 URL。
    if parts.username or parts.password:
        raise UnsafeURL(
            "不允许在 URL 中携带用户名或密码。"
        )

    return normalized


def _content_type_only(value: str) -> str:
    return (
        (value or "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )


def _normalize_text(text: str) -> str:
    lines = []

    for raw_line in (text or "").splitlines():
        line = " ".join(
            raw_line.split()
        ).strip()

        if line:
            lines.append(line)

    return "\n".join(lines)


def _meta_content(
    soup: BeautifulSoup,
    *,
    name: str | None = None,
    property_name: str | None = None,
) -> str:
    tag = None

    if name:
        tag = soup.find(
            "meta",
            attrs={"name": name},
        )

    if tag is None and property_name:
        tag = soup.find(
            "meta",
            attrs={"property": property_name},
        )

    if tag is None:
        return ""

    return str(
        tag.get("content")
        or ""
    ).strip()


def _extract_html(
    html: str,
    final_url: str,
) -> dict[str, Any]:
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    title = ""

    if soup.title:
        title = _normalize_text(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

    description = (
        _meta_content(
            soup,
            name="description",
        )
        or _meta_content(
            soup,
            property_name="og:description",
        )
    )

    site_name = _meta_content(
        soup,
        property_name="og:site_name",
    )

    canonical_url = ""

    canonical = soup.find(
        "link",
        rel=lambda value: (
            value
            and "canonical"
            in (
                value
                if isinstance(value, list)
                else str(value).lower().split()
            )
        ),
    )

    if canonical:
        href = str(
            canonical.get("href")
            or ""
        ).strip()

        if href:
            canonical_url = urljoin(
                final_url,
                href,
            )

    # 清理明显不属于正文的节点。
    for tag_name in (
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "iframe",
        "form",
        "button",
        "nav",
        "footer",
        "header",
        "aside",
    ):
        for tag in soup.find_all(
            tag_name
        ):
            tag.decompose()

    # 优先 article / main，失败再退回 body。
    root = (
        soup.find("article")
        or soup.find("main")
        or soup.body
        or soup
    )

    content = _normalize_text(
        root.get_text(
            "\n",
            strip=True,
        )
    )

    return {
        "title": title,
        "description": description,
        "site_name": site_name,
        "canonical_url":
            canonical_url,
        "content": content,
    }


def _decode_response(
    response: httpx.Response,
    body: bytes,
) -> str:
    encoding = (
        response.encoding
        or "utf-8"
    )

    try:
        return body.decode(
            encoding,
            errors="replace",
        )
    except LookupError:
        return body.decode(
            "utf-8",
            errors="replace",
        )


def _fetch_sync(
    url: str,
    max_chars: int,
) -> dict:
    """
    抓取一个公网网页并提取可供 Agent 阅读的正文。

    不允许访问 localhost、私网、链路本地地址或 file://。
    """
    max_chars = max(
        1_000,
        min(
            int(max_chars),
            MAX_CHARS_LIMIT,
        ),
    )

    try:
        current_url = (
            _validate_public_url(
                url
            )
        )

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "text/plain,"
                "application/json,"
                "application/xml;q=0.9,"
                "*/*;q=0.5"
            ),
            "Accept-Language":
                "en-US,en;q=0.8,zh-CN;q=0.7",
        }

        redirect_chain = []

        with httpx.Client(
            timeout=httpx.Timeout(
                get_int_setting(
                    "web.fetch_timeout_seconds",
                    FETCH_TIMEOUT_SECONDS,
                    minimum=5,
                    maximum=60,
                )
            ),
            follow_redirects=False,
            headers=headers,
        ) as client:

            response = None

            for _ in range(
                MAX_REDIRECTS + 1
            ):
                # 每次请求前重新验证，
                # 避免跳转到 localhost / 私网。
                current_url = (
                    _validate_public_url(
                        current_url
                    )
                )

                response = client.get(
                    current_url
                )

                if response.status_code in {
                    301,
                    302,
                    303,
                    307,
                    308,
                }:
                    location = (
                        response.headers.get(
                            "location"
                        )
                        or ""
                    ).strip()

                    if not location:
                        break

                    next_url = urljoin(
                        current_url,
                        location,
                    )

                    redirect_chain.append(
                        next_url
                    )

                    current_url = next_url
                    continue

                break

            else:
                raise RuntimeError(
                    "重定向次数过多。"
                )

            if response is None:
                raise RuntimeError(
                    "没有获得 HTTP 响应。"
                )

            if response.status_code in {
                301,
                302,
                303,
                307,
                308,
            }:
                raise RuntimeError(
                    "重定向次数超过限制。"
                )

            response.raise_for_status()

            content_type = (
                _content_type_only(
                    response.headers.get(
                        "content-type",
                        ""
                    )
                )
            )

            if (
                content_type
                and not any(
                    content_type.startswith(
                        allowed
                    )
                    for allowed
                    in ALLOWED_CONTENT_TYPES
                )
            ):
                return {
                    "ok": False,
                    "url": url,
                    "final_url": current_url,
                    "content_type":
                        content_type,
                    "error": (
                        "WEB_FETCH_UNSUPPORTED_CONTENT_TYPE: "
                        f"{content_type or 'unknown'}。"
                        "当前 web_fetch 只处理 HTML、"
                        "纯文本、JSON 和 XML。"
                    ),
                }

            raw = response.content

            raw_truncated = False

            if len(raw) > MAX_RESPONSE_BYTES:
                raw = raw[
                    :MAX_RESPONSE_BYTES
                ]
                raw_truncated = True

            text = _decode_response(
                response,
                raw,
            )

            if content_type.startswith(
                "text/html"
            ):
                extracted = _extract_html(
                    text,
                    current_url,
                )

            else:
                extracted = {
                    "title": "",
                    "description": "",
                    "site_name": "",
                    "canonical_url": "",
                    "content":
                        _normalize_text(
                            text
                        ),
                }

            content = (
                extracted.get(
                    "content"
                )
                or ""
            )

            text_truncated = (
                len(content)
                > max_chars
            )

            if text_truncated:
                content = (
                    content[:max_chars]
                    + "\n...[正文已截断]"
                )

            hostname = (
                urlsplit(
                    current_url
                ).hostname
                or ""
            )

            result = {
                "ok": True,
                "url": url,
                "final_url":
                    current_url,

                "canonical_url":
                    extracted.get(
                        "canonical_url"
                    )
                    or "",

                "hostname":
                    hostname,

                "title":
                    extracted.get(
                        "title"
                    )
                    or "",

                "description":
                    extracted.get(
                        "description"
                    )
                    or "",

                "site_name":
                    extracted.get(
                        "site_name"
                    )
                    or "",

                "content_type":
                    content_type
                    or "unknown",

                "content":
                    content,

                "char_count":
                    len(content),

                "truncated": bool(
                    raw_truncated
                    or text_truncated
                ),

                "redirect_chain":
                    redirect_chain,

                "retrieved_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),

                "error": "",
            }

            # 成功读取正文后，把该来源升级为 fetched source。
            source_id = (
                register_fetched_source(
                    url=url,
                    final_url=current_url,
                    canonical_url=(
                        result.get(
                            "canonical_url"
                        )
                        or ""
                    ),
                    title=(
                        result.get("title")
                        or ""
                    ),
                    description=(
                        result.get(
                            "description"
                        )
                        or ""
                    ),
                    hostname=hostname,
                    content_type=(
                        result.get(
                            "content_type"
                        )
                        or ""
                    ),
                )
            )

            if source_id:
                result["source_id"] = (
                    source_id
                )

            return result

    except UnsafeURL as error:
        return {
            "ok": False,
            "url": url,
            "error": (
                "WEB_FETCH_BLOCKED: "
                f"{error}"
            ),
        }

    except httpx.TimeoutException as error:
        return {
            "ok": False,
            "url": url,
            "error": (
                "WEB_FETCH_TIMEOUT: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }

    except httpx.HTTPStatusError as error:
        return {
            "ok": False,
            "url": url,
            "final_url": str(
                error.request.url
            ),
            "status_code":
                error.response.status_code,
            "error": (
                "WEB_FETCH_HTTP_ERROR: "
                f"HTTP "
                f"{error.response.status_code}"
            ),
        }

    except httpx.HTTPError as error:
        return {
            "ok": False,
            "url": url,
            "error": (
                "WEB_FETCH_NETWORK_ERROR: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }

    except Exception as error:
        return {
            "ok": False,
            "url": url,
            "error": (
                "WEB_FETCH_UNEXPECTED_ERROR: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }


@function_tool
async def web_fetch(
    url: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """
    打开一个公开网页并提取正文，用于核实 web_search 找到的来源。

    典型流程：
    1. 先用 web_search 找候选来源；
    2. 优先选择官方/第一方/高可信来源；
    3. 用 web_fetch 读取正文；
    4. 再基于正文回答用户。

    成功结果会包含 source_id（例如 S1）。
    回答中引用该网页事实时，应在相关句子后标注 [S1]。

    安全限制：
    - 仅允许 http/https；
    - 禁止 localhost、私网、链路本地和保留 IP；
    - 禁止 file:// 等本地资源；
    - 自动限制响应大小与正文长度。

    Args:
        url: 要读取的公开网页 URL，应来自可靠搜索结果或用户提供的公开 URL。
        max_chars: 最多返回多少正文字符，默认 18000，最大 30000。

    Returns:
        JSON 字符串，包含标题、最终 URL、正文、内容类型、截断状态等。
    """
    result = await asyncio.to_thread(
        _fetch_sync,
        url,
        max_chars,
    )

    return json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )
