import asyncio
import json
import re
from typing import Literal
from urllib.parse import quote, urlencode

import httpx
from bs4 import BeautifulSoup
from agents import function_tool


GITHUB_TRENDING_BASE = "https://github.com/trending"
TIMEOUT_SECONDS = 15
MAX_RESULTS_LIMIT = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0 Safari/537.36"
)


def _clean_text(value: str) -> str:
    return " ".join(
        (value or "").split()
    ).strip()


def _number_from_text(
    value: str,
) -> int | None:
    text = (
        value
        or ""
    ).replace(",", "").strip()

    match = re.search(
        r"\d+",
        text,
    )

    if not match:
        return None

    try:
        return int(
            match.group(0)
        )
    except ValueError:
        return None


def _build_url(
    since: str,
    language: str,
    spoken_language_code: str,
) -> str:
    path = GITHUB_TRENDING_BASE

    language = (
        language
        or ""
    ).strip()

    if language:
        path += "/" + quote(
            language,
            safe="",
        )

    params = {
        "since": since,
    }

    spoken_language_code = (
        spoken_language_code
        or ""
    ).strip()

    if spoken_language_code:
        params[
            "spoken_language_code"
        ] = spoken_language_code

    return (
        path
        + "?"
        + urlencode(params)
    )


def _parse_repo(
    article,
    rank: int,
    since: str,
) -> dict:
    heading = article.find(
        "h2"
    )

    link = (
        heading.find("a")
        if heading
        else None
    )

    href = str(
        link.get("href")
        if link
        else ""
    ).strip()

    repo = (
        href.strip("/")
        if href
        else ""
    )

    url = (
        "https://github.com"
        + href
        if href.startswith("/")
        else href
    )

    description_tag = (
        article.find("p")
    )

    description = (
        _clean_text(
            description_tag.get_text(
                " ",
                strip=True,
            )
        )
        if description_tag
        else ""
    )

    language_tag = article.find(
        attrs={
            "itemprop":
                "programmingLanguage"
        }
    )

    language = (
        _clean_text(
            language_tag.get_text(
                " ",
                strip=True,
            )
        )
        if language_tag
        else ""
    )

    stars = None
    forks = None

    for anchor in article.find_all(
        "a",
        href=True,
    ):
        anchor_href = str(
            anchor.get("href")
            or ""
        )

        anchor_text = _clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if anchor_href.endswith(
            "/stargazers"
        ):
            stars = _number_from_text(
                anchor_text
            )

        elif anchor_href.endswith(
            "/forks"
        ):
            forks = _number_from_text(
                anchor_text
            )

    period_text = ""

    for span in article.find_all(
        "span"
    ):
        candidate = _clean_text(
            span.get_text(
                " ",
                strip=True,
            )
        )

        lowered = candidate.lower()

        if (
            "stars today"
            in lowered
            or "stars this week"
            in lowered
            or "stars this month"
            in lowered
        ):
            period_text = candidate
            break

    period_stars = (
        _number_from_text(
            period_text
        )
        if period_text
        else None
    )

    return {
        "rank": rank,
        "repository": repo,
        "url": url,
        "description": description,
        "language": language,
        "stars": stars,
        "forks": forks,
        "period": since,
        "period_stars": period_stars,
        "period_text": period_text,
    }


def _github_trending_sync(
    since: Literal[
        "daily",
        "weekly",
        "monthly",
    ] = "daily",
    language: str = "",
    spoken_language_code: str = "",
    max_results: int = 10,
) -> dict:
    since = (
        since
        if since
        in {
            "daily",
            "weekly",
            "monthly",
        }
        else "daily"
    )

    max_results = max(
        1,
        min(
            int(max_results),
            MAX_RESULTS_LIMIT,
        ),
    )

    url = _build_url(
        since,
        language,
        spoken_language_code,
    )

    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                TIMEOUT_SECONDS
            ),
            headers={
                "User-Agent":
                    USER_AGENT,
                "Accept":
                    "text/html,"
                    "application/xhtml+xml",
                "Accept-Language":
                    "en-US,en;q=0.9,"
                    "zh-CN;q=0.8",
            },
            follow_redirects=True,
        ) as client:

            response = client.get(
                url
            )

            response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        articles = soup.select(
            "article.Box-row"
        )

        results = []

        for article in articles[
            :max_results
        ]:
            item = _parse_repo(
                article,
                len(results) + 1,
                since,
            )

            if item.get(
                "repository"
            ):
                results.append(
                    item
                )

        return {
            "ok": True,
            "source":
                "GitHub Trending",
            "source_url": url,
            "since": since,
            "language":
                language or "",
            "spoken_language_code":
                spoken_language_code
                or "",
            "result_count":
                len(results),
            "results": results,
            "error": "",
        }

    except httpx.TimeoutException as error:
        return {
            "ok": False,
            "source":
                "GitHub Trending",
            "source_url": url,
            "results": [],
            "error": (
                "GITHUB_TRENDING_TIMEOUT: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }

    except httpx.HTTPStatusError as error:
        return {
            "ok": False,
            "source":
                "GitHub Trending",
            "source_url": url,
            "results": [],
            "error": (
                "GITHUB_TRENDING_HTTP_ERROR: "
                f"HTTP "
                f"{error.response.status_code}"
            ),
        }

    except Exception as error:
        return {
            "ok": False,
            "source":
                "GitHub Trending",
            "source_url": url,
            "results": [],
            "error": (
                "GITHUB_TRENDING_ERROR: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }


@function_tool
async def github_trending(
    since: Literal[
        "daily",
        "weekly",
        "monthly",
    ] = "daily",
    language: str = "",
    spoken_language_code: str = "",
    max_results: int = 10,
) -> str:
    """
    直接读取 GitHub 官方 Trending 页面，获取当前热门仓库。

    当用户询问以下内容时，优先使用此工具，而不是普通 web_search：
    - GitHub 当前热门项目
    - GitHub Trending
    - 今天 / 本周 / 本月最火的 GitHub 仓库
    - 某种编程语言当前热门 GitHub 项目

    数据直接来自 github.com/trending，
    比第三方“热门项目汇总文章”更适合回答实时趋势问题。

    Args:
        since:
            daily = 今日趋势
            weekly = 本周趋势
            monthly = 本月趋势
        language:
            可选的编程语言过滤，例如 python、typescript、rust。
            留空表示全部语言。
        spoken_language_code:
            可选的项目自然语言过滤，例如 zh、en。
            通常无需设置。
        max_results:
            返回项目数量，默认 10，最大 20。

    Returns:
        JSON 字符串，包括排名、仓库、GitHub URL、描述、语言、
        stars、forks 和本周期新增 stars。
    """

    result = await asyncio.to_thread(
        _github_trending_sync,
        since,
        language,
        spoken_language_code,
        max_results,
    )

    return json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )
