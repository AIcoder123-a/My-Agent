import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    OpenAIChatCompletionsModel,
    set_tracing_disabled,
)

from app_settings import (
    get_model_api_key,
    get_str_setting,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

load_dotenv(
    PROJECT_ROOT
    / ".env"
)


# ============================================================
# 模型 / Provider 配置
#
# 这些值在进程内可以被 reload_runtime() 重新读取并替换，
# 所以「设置 → 模型」保存后无需重启即可生效。
# ============================================================

DEFAULT_BASE_URL = (
    "https://api.openai.com/v1"
)

PLACEHOLDER_API_KEY = (
    "not-configured"
)

PLACEHOLDER_MODEL = (
    "not-configured"
)


# ============================================================
# 网络：超时与重试
#
# openai-python 默认超时长达 600 秒：打到无响应的第三方
# 端点时，界面会挂住十分钟没有任何反馈。这里收紧为
# 「连接 30 秒、读 120 秒」。读超时按流式响应的字节间隔
# 计算，不会打断正常的长回答；连接超时给跨境网络留足
# 余量。超时错误由 SDK 内建重试（默认 2 次）自动兜底，
# 全部失败时任务快速报错，而不是无限挂起。
# 可用环境变量覆盖。
# ============================================================

def _env_number(
    name: str,
    default: float,
) -> float:

    raw = (
        os.getenv(
            name,
            "",
        ).strip()
    )

    if not raw:

        return default

    try:

        value = float(raw)

    except ValueError:

        return default

    return value if value > 0 else default


MODEL_CONNECT_TIMEOUT_SECONDS = (
    _env_number(
        "MODEL_CONNECT_TIMEOUT_SECONDS",
        30.0,
    )
)

MODEL_READ_TIMEOUT_SECONDS = (
    _env_number(
        "MODEL_READ_TIMEOUT_SECONDS",
        120.0,
    )
)

MODEL_MAX_RETRIES = int(
    _env_number(
        "MODEL_MAX_RETRIES",
        2,
    )
)


def _read_api_key() -> str:

    return (
        get_model_api_key()
        or (
            os.getenv(
                "MODEL_API_KEY",
                "",
            )
            or ""
        ).strip()
    )


def _read_base_url() -> str:

    return (
        get_str_setting(
            "model.base_url",
            "",
        ).strip()
        or (
            os.getenv(
                "MODEL_BASE_URL",
                "",
            )
            or ""
        ).strip()
    )


def _read_model_name() -> str:

    return (
        get_str_setting(
            "model.model_name",
            "",
        ).strip()
        or (
            os.getenv(
                "MODEL_NAME",
                "",
            )
            or ""
        ).strip()
    )


def is_configured() -> bool:
    """
    判断是否已经具备「可发起请求」的最小配置。

    API Key 缺失或模型名仍是占位符，都视为未配置。
    """

    return bool(
        _read_api_key()
    ) and (
        _read_model_name()
        not in {
            "",
            PLACEHOLDER_MODEL,
        }
    )


def build_runtime():
    """
    按当前设置构建 (client, model)。

    返回一个元组，调用方决定是否替换模块级变量。
    """

    key = (
        _read_api_key()
        or PLACEHOLDER_API_KEY
    )

    url = (
        _read_base_url()
        or DEFAULT_BASE_URL
    )

    name = (
        _read_model_name()
        or PLACEHOLDER_MODEL
    )

    new_client = AsyncOpenAI(
        api_key=key,
        base_url=url,
        timeout=httpx.Timeout(
            MODEL_READ_TIMEOUT_SECONDS,
            connect=MODEL_CONNECT_TIMEOUT_SECONDS,
        ),
        max_retries=MODEL_MAX_RETRIES,
    )

    new_model = OpenAIChatCompletionsModel(
        model=name,
        openai_client=new_client,
        buffer_streamed_tool_calls=True,
    )

    return new_client, new_model


def reload_runtime() -> dict:
    """
    重新读取设置并原地替换模块级 client / model。

    同时把新模型挂到已创建的 Agent 上，
    这样「设置 → 模型」保存后立刻生效，不必重启。

    返回一份脱敏摘要，供界面显示。
    """

    global client, model
    global api_key, base_url, model_name

    new_client, new_model = (
        build_runtime()
    )

    client = new_client
    model = new_model

    api_key = (
        _read_api_key()
        or PLACEHOLDER_API_KEY
    )

    base_url = (
        _read_base_url()
        or DEFAULT_BASE_URL
    )

    model_name = (
        _read_model_name()
        or PLACEHOLDER_MODEL
    )

    # 已存在的 Agent 实例换模型：
    # openai-agents 的 Agent.model 是可写属性，
    # 下一次 Runner.run 就会用新模型。
    try:

        from app_agents.personal_agent import (
            personal_agent,
        )

        personal_agent.model = (
            new_model
        )

    except Exception:
        # Agent 还没构建或结构变化时不阻断配置保存。
        pass

    return {
        "configured": (
            is_configured()
        ),
        "base_url": base_url,
        "model_name": model_name,
    }


api_key = (
    _read_api_key()
    or PLACEHOLDER_API_KEY
)

base_url = (
    _read_base_url()
    or DEFAULT_BASE_URL
)

model_name = (
    _read_model_name()
    or PLACEHOLDER_MODEL
)


client, model = build_runtime()


# 第三方 OpenAI-compatible API：
# 不使用 OpenAI 官方 tracing 认证。
set_tracing_disabled(
    True
)
