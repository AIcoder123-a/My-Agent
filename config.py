import os
from pathlib import Path

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
# ============================================================

api_key = (
    get_model_api_key()
)

base_url = (
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

model_name = (
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


if not api_key:

    raise RuntimeError(
        "未找到模型 API Key。"
        "请在设置中心配置，"
        "或保留 .env 中的 MODEL_API_KEY。"
    )


if not base_url:

    raise RuntimeError(
        "未找到 MODEL_BASE_URL。"
        "请在设置中心配置模型接口地址，"
        "或保留 .env 中的 MODEL_BASE_URL。"
    )


if not model_name:

    raise RuntimeError(
        "未找到 MODEL_NAME。"
        "请在设置中心配置模型名称，"
        "或保留 .env 中的 MODEL_NAME。"
    )


client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url,
)


model = (
    OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=client,
        buffer_streamed_tool_calls=True,
    )
)


# 第三方 OpenAI-compatible API：
# 不使用 OpenAI 官方 tracing 认证。
set_tracing_disabled(
    True
)
