import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from agents import function_tool
from ddgs import DDGS
from ddgs.exceptions import (
    DDGSException,
    RatelimitException,
    TimeoutException,
)

from app_settings import (
    get_int_setting,
)


DEFAULT_REGION = "us-en"
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 8
SEARCH_TIMEOUT_SECONDS = 12

# 普通联网任务最多 4 次搜索。
NORMAL_TASK_SEARCH_BUDGET = 4

# 明显属于深入研究/全面比较的任务最多 6 次搜索。
RESEARCH_TASK_SEARCH_BUDGET = 6


@dataclass
class SearchTaskState:
    task_id: str
    max_calls: int
    calls_used: int = 0
    seen_queries: set[str] = field(default_factory=set)
    seen_urls: set[str] = field(default_factory=set)

    # 当前 Task 中发现的来源。
    # key 使用 canonical URL，value 是统一来源记录。
    sources: dict[str, dict] = field(default_factory=dict)

    # 为当前 Task 分配稳定的 S1 / S2 / S3...
    next_source_number: int = 1


# 按 task_id 保存搜索状态。
# 这样 HITL 暂停后恢复时也能继续沿用同一任务的搜索预算。
_SEARCH_TASKS: dict[str, SearchTaskState] = {}

# 注册表容量上限。异常 / 取消路径可能漏掉 end_search_task，
# 长期运行时按插入顺序淘汰最旧的非活跃任务，防止缓慢累积。
MAX_SEARCH_TASKS = 200

# 当前正在执行的 task_id。
# AgentService 会在 Runner 前激活它。
_CURRENT_TASK_ID: str | None = None


def _looks_like_research_task(user_input: str) -> bool:
    """
    用非常保守的关键词判断是否属于明显的“深度研究”任务。

    普通“帮我搜一下 / 查一下最新消息”仍使用 4 次预算；
    只有明确要求深入、全面、多来源比较等时才提高到 6 次。
    """
    text = (user_input or "").strip().lower()

    hints = (
        "深入研究",
        "深入分析",
        "全面研究",
        "全面分析",
        "详细调研",
        "系统调研",
        "多来源",
        "综合比较",
        "全面比较",
        "深度研究",
        "deep research",
        "in-depth research",
        "comprehensive research",
        "comprehensive analysis",
        "multi-source",
        "multiple sources",
        "systematic review",
    )

    return any(
        hint in text
        for hint in hints
    )


def begin_search_task(
    task_id: str,
    user_input: str = "",
) -> dict:
    """
    为一个新的 Agent Task 创建搜索预算。

    普通任务：4 次
    明显深度研究任务：6 次
    """
    global _CURRENT_TASK_ID

    normal_budget = (
        get_int_setting(
            "web.normal_search_budget",
            NORMAL_TASK_SEARCH_BUDGET,
            minimum=1,
            maximum=12,
        )
    )

    research_budget = (
        get_int_setting(
            "web.research_search_budget",
            RESEARCH_TASK_SEARCH_BUDGET,
            minimum=normal_budget,
            maximum=20,
        )
    )

    max_calls = (
        research_budget
        if _looks_like_research_task(
            user_input
        )
        else normal_budget
    )

    state = SearchTaskState(
        task_id=str(task_id),
        max_calls=max_calls,
    )

    _SEARCH_TASKS[str(task_id)] = state
    _CURRENT_TASK_ID = str(task_id)

    while len(_SEARCH_TASKS) > MAX_SEARCH_TASKS:
        oldest = next(
            (
                key
                for key in _SEARCH_TASKS
                if key != _CURRENT_TASK_ID
            ),
            None,
        )
        if oldest is None:
            break
        _SEARCH_TASKS.pop(
            oldest,
            None,
        )

    return get_search_task_status(
        task_id
    )


def activate_search_task(
    task_id: str,
) -> dict:
    """
    恢复/激活一个已经存在的 Task 搜索状态。

    主要用于 HITL 暂停后的 Runner 恢复。
    """
    global _CURRENT_TASK_ID

    task_id = str(task_id)

    if task_id not in _SEARCH_TASKS:
        # 极端情况下状态不存在时，用普通预算恢复，
        # 但不主动增加已用次数。
        _SEARCH_TASKS[task_id] = SearchTaskState(
            task_id=task_id,
            max_calls=(
                get_int_setting(
                    "web.normal_search_budget",
                    NORMAL_TASK_SEARCH_BUDGET,
                    minimum=1,
                    maximum=12,
                )
            ),
        )

    _CURRENT_TASK_ID = task_id

    return get_search_task_status(
        task_id
    )


def end_search_task(
    task_id: str | None = None,
) -> None:
    """
    Task 真正结束后清理搜索状态。
    """
    global _CURRENT_TASK_ID

    target = (
        str(task_id)
        if task_id is not None
        else _CURRENT_TASK_ID
    )

    if target:
        _SEARCH_TASKS.pop(
            target,
            None,
        )

    if (
        _CURRENT_TASK_ID
        == target
    ):
        _CURRENT_TASK_ID = None


def get_search_task_status(
    task_id: str | None = None,
) -> dict:
    """
    返回任务级搜索预算状态。
    """
    target = (
        str(task_id)
        if task_id is not None
        else _CURRENT_TASK_ID
    )

    state = (
        _SEARCH_TASKS.get(target)
        if target
        else None
    )

    if state is None:
        return {
            "task_id": target or "",
            "active": False,
            "calls_used": 0,
            "calls_remaining": 0,
            "max_calls": 0,
            "unique_queries": 0,
            "unique_urls": 0,
        }

    return {
        "task_id": state.task_id,
        "active": True,
        "calls_used": state.calls_used,
        "calls_remaining": max(
            0,
            state.max_calls
            - state.calls_used,
        ),
        "max_calls": state.max_calls,
        "unique_queries": len(
            state.seen_queries
        ),
        "unique_urls": len(
            state.seen_urls
        ),
        "source_count": len(
            state.sources
        ),
        "fetched_source_count": sum(
            1
            for source in state.sources.values()
            if source.get("fetched")
        ),
    }


def _current_state() -> SearchTaskState | None:
    if not _CURRENT_TASK_ID:
        return None

    return _SEARCH_TASKS.get(
        _CURRENT_TASK_ID
    )


def _normalize_query_key(
    query: str,
    search_type: str,
    region: str,
    freshness: str,
) -> str:
    """
    生成重复搜索判定键。

    只拦截“本质上完全相同”的查询，
    不阻止模型用不同关键词进行合理补充搜索。
    """
    normalized_query = re.sub(
        r"\s+",
        " ",
        (query or "").strip().lower(),
    )

    return "|".join(
        (
            search_type.strip().lower(),
            region.strip().lower(),
            freshness.strip().lower(),
            normalized_query,
        )
    )


def _canonical_url(
    url: str,
) -> str:
    """
    URL 去重键。

    去掉 fragment 和末尾多余 /，
    保留 query string，因为有些站点的 query 本身代表不同页面。
    """
    url = (url or "").strip()

    if not url:
        return ""

    try:
        parts = urlsplit(url)

        path = (
            parts.path.rstrip("/")
            or "/"
        )

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                path,
                parts.query,
                "",
            )
        )

    except Exception:
        return url.rstrip("/")


def _hostname_from_url(
    url: str,
) -> str:
    try:
        return (
            urlsplit(url).hostname
            or ""
        ).lower()
    except Exception:
        return ""


def _register_search_source(
    item: dict,
) -> str:
    """
    把 web_search 返回的结果登记到当前 Task 的 Sources。

    返回稳定 source_id，例如 S1。
    这里只代表“搜索发现”，不代表已经读取过正文。
    """
    state = _current_state()

    if state is None:
        return ""

    url = str(
        item.get("url")
        or ""
    ).strip()

    key = _canonical_url(url)

    if not key:
        return ""

    existing = state.sources.get(
        key,
        {},
    )

    source_id = str(
        existing.get("source_id")
        or ""
    ).strip()

    if not source_id:
        source_id = (
            f"S{state.next_source_number}"
        )
        state.next_source_number += 1

    title = str(
        item.get("title")
        or existing.get("title")
        or ""
    ).strip()

    snippet = str(
        item.get("snippet")
        or existing.get("snippet")
        or ""
    ).strip()

    publisher = str(
        item.get("source")
        or existing.get("publisher")
        or ""
    ).strip()

    published_at = str(
        item.get("date")
        or existing.get("published_at")
        or ""
    ).strip()

    state.sources[key] = {
        "source_id": source_id,
        "title": title,
        "url": url,
        "hostname":
            _hostname_from_url(url),
        "snippet": snippet,
        "publisher": publisher,
        "published_at": published_at,

        # discovered = 搜索发现
        # fetched = 已真正读取正文
        "discovered": True,
        "fetched": bool(
            existing.get("fetched")
        ),

        "fetch_title": str(
            existing.get("fetch_title")
            or ""
        ),
        "fetch_description": str(
            existing.get("fetch_description")
            or ""
        ),
        "content_type": str(
            existing.get("content_type")
            or ""
        ),
    }

    return source_id


def register_fetched_source(
    *,
    url: str,
    final_url: str = "",
    canonical_url: str = "",
    title: str = "",
    description: str = "",
    hostname: str = "",
    content_type: str = "",
) -> str:
    """
    由 web_fetch 在成功读取正文后调用。

    如果这个 URL 之前来自 web_search，则升级同一条来源记录；
    如果用户直接给了 URL，也允许创建一条 fetched-only 来源。
    """
    state = _current_state()

    if state is None:
        return ""

    preferred_url = (
        canonical_url
        or final_url
        or url
        or ""
    ).strip()

    key = _canonical_url(
        preferred_url
    )

    if not key:
        return ""

    # 尝试把 search 阶段的原 URL 记录迁移到最终 URL，
    # 避免一次 redirect 产生两条来源。
    candidate_keys = [
        _canonical_url(url),
        _canonical_url(final_url),
        _canonical_url(canonical_url),
    ]

    existing = {}

    for candidate in candidate_keys:
        if (
            candidate
            and candidate in state.sources
        ):
            existing = state.sources.pop(
                candidate
            )
            break

    source_id = str(
        existing.get("source_id")
        or ""
    ).strip()

    if not source_id:
        source_id = (
            f"S{state.next_source_number}"
        )
        state.next_source_number += 1

    state.sources[key] = {
        "source_id": source_id,
        "title": (
            str(title).strip()
            or str(
                existing.get("title")
                or ""
            ).strip()
        ),
        "url": preferred_url,
        "hostname": (
            str(hostname).strip().lower()
            or _hostname_from_url(
                preferred_url
            )
        ),
        "snippet": str(
            existing.get("snippet")
            or ""
        ),
        "publisher": str(
            existing.get("publisher")
            or ""
        ),
        "published_at": str(
            existing.get("published_at")
            or ""
        ),

        "discovered": bool(
            existing.get("discovered")
        ),
        "fetched": True,

        "fetch_title": str(
            title
            or ""
        ).strip(),
        "fetch_description": str(
            description
            or ""
        ).strip(),
        "content_type": str(
            content_type
            or ""
        ).strip(),
    }

    return source_id


def get_search_task_sources(
    task_id: str | None = None,
) -> list[dict]:
    """
    返回当前任务的统一 Sources 列表。

    source_id 仅在当前 Task 内稳定，例如 S1 / S2 / S3。
    """
    target = (
        str(task_id)
        if task_id is not None
        else _CURRENT_TASK_ID
    )

    state = (
        _SEARCH_TASKS.get(target)
        if target
        else None
    )

    if state is None:
        return []

    result = []

    for source in (
        state.sources.values()
    ):
        result.append(
            dict(source)
        )

    return result


def _normalize_text_result(
    item: dict,
    index: int,
) -> dict:
    """把 DDGS text() 的不同后端结果统一成稳定结构。"""
    return {
        "index": index,
        "title": str(
            item.get("title")
            or ""
        ).strip(),
        "url": str(
            item.get("href")
            or item.get("url")
            or ""
        ).strip(),
        "snippet": str(
            item.get("body")
            or item.get("description")
            or item.get("content")
            or ""
        ).strip(),
    }


def _normalize_news_result(
    item: dict,
    index: int,
) -> dict:
    """把 DDGS news() 的结果统一成稳定结构。"""
    return {
        "index": index,
        "title": str(
            item.get("title")
            or ""
        ).strip(),
        "url": str(
            item.get("url")
            or item.get("href")
            or ""
        ).strip(),
        "snippet": str(
            item.get("body")
            or item.get("description")
            or item.get("content")
            or ""
        ).strip(),
        "date": str(
            item.get("date")
            or ""
        ).strip(),
        "source": str(
            item.get("source")
            or ""
        ).strip(),
    }


def _dedupe_results(
    results: list[dict],
    state: SearchTaskState | None,
) -> tuple[list[dict], int]:
    """
    去掉：
    1. 单次搜索内部重复 URL
    2. 当前 Task 之前已经返回过的 URL
    """
    clean_results = []
    removed = 0
    local_seen = set()

    for item in results:
        url = str(
            item.get("url")
            or ""
        ).strip()

        key = _canonical_url(
            url
        )

        if not key:
            continue

        if key in local_seen:
            removed += 1
            continue

        if (
            state is not None
            and key in state.seen_urls
        ):
            removed += 1
            continue

        local_seen.add(key)

        if state is not None:
            state.seen_urls.add(key)

        clean_results.append(
            item
        )

        # 同时登记为当前 Task 的候选来源，
        # 并把稳定 source_id 返回给模型。
        source_id = (
            _register_search_source(
                item
            )
        )

        if source_id:
            item["source_id"] = (
                source_id
            )

    # 重新编号，方便模型引用“结果 1 / 2 / 3”。
    for index, item in enumerate(
        clean_results,
        start=1,
    ):
        item["index"] = index

    return clean_results, removed


def _budget_payload(
    state: SearchTaskState | None,
) -> dict:
    if state is None:
        return {
            "calls_used": 0,
            "calls_remaining": None,
            "max_calls": None,
        }

    return {
        "calls_used":
            state.calls_used,

        "calls_remaining":
            max(
                0,
                state.max_calls
                - state.calls_used,
            ),

        "max_calls":
            state.max_calls,
    }


def _run_search_sync(
    query: str,
    search_type: Literal["web", "news"],
    max_results: int,
    region: str,
    freshness: str,
) -> dict:
    """
    实际执行阻塞式网络搜索。

    单独保留这个普通 Python 函数，
    方便在 Agent Tool 之外独立测试。
    """
    query = (
        query
        or ""
    ).strip()

    if not query:
        return {
            "ok": False,
            "query": "",
            "search_type": search_type,
            "results": [],
            "error":
                "搜索关键词不能为空。",
        }

    max_results = max(
        1,
        min(
            int(max_results),
            MAX_RESULTS_LIMIT,
        ),
    )

    region = (
        region
        or DEFAULT_REGION
    ).strip()

    freshness = (
        freshness
        or ""
    ).strip().lower()

    if freshness not in {
        "",
        "d",
        "w",
        "m",
        "y",
    }:
        freshness = ""

    timelimit = (
        freshness
        or None
    )

    try:
        searcher = DDGS(
            timeout=SEARCH_TIMEOUT_SECONDS,
        )

        if search_type == "news":

            raw_results = searcher.news(
                query=query,
                region=region,
                safesearch="moderate",
                timelimit=timelimit,
                max_results=max_results,
                backend="auto",
            )

            results = [
                _normalize_news_result(
                    item,
                    index,
                )
                for index, item
                in enumerate(
                    raw_results,
                    start=1,
                )
                if isinstance(
                    item,
                    dict,
                )
            ]

        else:

            raw_results = searcher.text(
                query=query,
                region=region,
                safesearch="moderate",
                timelimit=timelimit,
                max_results=max_results,
                backend="auto",
            )

            results = [
                _normalize_text_result(
                    item,
                    index,
                )
                for index, item
                in enumerate(
                    raw_results,
                    start=1,
                )
                if isinstance(
                    item,
                    dict,
                )
            ]

        results = [
            item
            for item in results
            if item.get("url")
        ]

        return {
            "ok": True,
            "query": query,
            "search_type": search_type,
            "region": region,
            "freshness": freshness,
            "result_count":
                len(results),
            "results": results,
            "error": "",
        }

    except RatelimitException as error:

        return {
            "ok": False,
            "query": query,
            "search_type": search_type,
            "results": [],
            "error": (
                "WEB_SEARCH_RATE_LIMIT: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }

    except TimeoutException as error:

        return {
            "ok": False,
            "query": query,
            "search_type": search_type,
            "results": [],
            "error": (
                "WEB_SEARCH_TIMEOUT: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }

    except DDGSException as error:

        return {
            "ok": False,
            "query": query,
            "search_type": search_type,
            "results": [],
            "error": (
                "WEB_SEARCH_ERROR: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }

    except Exception as error:

        return {
            "ok": False,
            "query": query,
            "search_type": search_type,
            "results": [],
            "error": (
                "WEB_SEARCH_UNEXPECTED_ERROR: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }


@function_tool
async def web_search(
    query: str,
    search_type: Literal["web", "news"] = "web",
    max_results: int = DEFAULT_MAX_RESULTS,
    region: str = DEFAULT_REGION,
    freshness: str = "",
) -> str:
    """
    搜索互联网中的最新公开信息，并返回带来源 URL 的结果。

    当用户的问题依赖最新信息、新闻、当前版本、近期事件、
    在线资料、实时变化的信息，或者明确要求联网搜索时使用。

    搜索预算由系统按“当前用户任务”统一控制：
    - 普通联网任务最多 4 次搜索；
    - 明确要求深入/全面/多来源研究的任务最多 6 次；
    - 相同查询不会重复执行；
    - 当前任务已经返回过的 URL 会自动去重。

    搜索结果中的每个来源会包含稳定的 source_id，
    例如 "S1"、"S2"。回答用户时，凡是基于联网来源得出的
    具体事实，应在相关句子后使用 [S1]、[S2] 这种格式标注来源。

    当返回 budget_exceeded=true 时，不要继续尝试 web_search，
    应使用已经获得的来源完成回答。
    当返回 duplicate_query=true 时，应使用已有结果，
    或仅在确有必要时换成明显不同的新查询。

    Args:
        query: 搜索关键词。应尽量具体，必要时可包含年份、产品名或限定词。
        search_type: "web" 为普通网页搜索，"news" 为新闻搜索。
        max_results: 返回结果数量，建议 3~5，最大 8。
        region: 搜索地区与语言，例如 "us-en"、"cn-zh"。
        freshness: 时间范围；"" 表示不限，"d"=一天，"w"=一周，
            "m"=一月，"y"=一年。

    Returns:
        JSON 字符串，包含搜索结果标题、摘要、URL 和当前搜索预算。
        回答用户时应依据返回结果，不要伪造来源。
    """
    query = (
        query
        or ""
    ).strip()

    search_type = (
        search_type
        if search_type in {
            "web",
            "news",
        }
        else "web"
    )

    region = (
        region
        or DEFAULT_REGION
    ).strip()

    freshness = (
        freshness
        or ""
    ).strip().lower()

    if freshness not in {
        "",
        "d",
        "w",
        "m",
        "y",
    }:
        freshness = ""

    state = _current_state()

    query_key = _normalize_query_key(
        query,
        search_type,
        region,
        freshness,
    )

    # --------------------------------------------------------
    # 重复 Query：不再消耗预算，也不再访问网络。
    # --------------------------------------------------------

    if (
        state is not None
        and query_key
        in state.seen_queries
    ):
        result = {
            "ok": True,
            "query": query,
            "search_type": search_type,
            "region": region,
            "freshness": freshness,
            "result_count": 0,
            "results": [],
            "duplicate_query": True,
            "budget_exceeded": False,
            "message": (
                "该搜索查询在当前任务中已经执行过，"
                "为避免重复搜索已跳过。"
            ),
            "error": "",
            "budget":
                _budget_payload(
                    state
                ),
        }

        return json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )

    # --------------------------------------------------------
    # 搜索预算耗尽：不给模型继续无限搜索。
    # --------------------------------------------------------

    if (
        state is not None
        and state.calls_used
        >= state.max_calls
    ):
        result = {
            "ok": False,
            "query": query,
            "search_type": search_type,
            "results": [],
            "duplicate_query": False,
            "budget_exceeded": True,
            "error": (
                "WEB_SEARCH_BUDGET_EXCEEDED: "
                "当前任务的联网搜索预算已经用完。"
                "请基于已有搜索结果完成回答。"
            ),
            "budget":
                _budget_payload(
                    state
                ),
        }

        return json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )

    # --------------------------------------------------------
    # 真正占用一次预算。
    # --------------------------------------------------------

    if state is not None:
        state.seen_queries.add(
            query_key
        )
        state.calls_used += 1

    result = await asyncio.to_thread(
        _run_search_sync,
        query,
        search_type,
        max_results,
        region,
        freshness,
    )

    # --------------------------------------------------------
    # 成功搜索后执行 URL 去重。
    # --------------------------------------------------------

    duplicate_urls_removed = 0

    if (
        result.get("ok") is True
        and isinstance(
            result.get("results"),
            list,
        )
    ):
        (
            clean_results,
            duplicate_urls_removed,
        ) = _dedupe_results(
            result["results"],
            state,
        )

        result["results"] = (
            clean_results
        )

        result["result_count"] = (
            len(clean_results)
        )

    result["duplicate_query"] = False
    result["budget_exceeded"] = False
    result[
        "duplicate_urls_removed"
    ] = duplicate_urls_removed
    result["budget"] = (
        _budget_payload(
            state
        )
    )

    return json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )
