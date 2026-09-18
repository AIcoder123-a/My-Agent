import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    OpenAIChatCompletionsModel,
    set_tracing_disabled,
)

from paths import BASE_DIR


# 加载项目根目录中的 .env
load_dotenv(BASE_DIR / ".env")


api_key = os.getenv("MODEL_API_KEY")
base_url = os.getenv("MODEL_BASE_URL")
model_name = os.getenv("MODEL_NAME")


if not api_key:
    raise ValueError(
        "缺少 MODEL_API_KEY，请检查 .env"
    )

if not base_url:
    raise ValueError(
        "缺少 MODEL_BASE_URL，请检查 .env"
    )

if not model_name:
    raise ValueError(
        "缺少 MODEL_NAME，请检查 .env"
    )


client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url,
)


model = OpenAIChatCompletionsModel(
    model=model_name,
    openai_client=client,
)


# 第三方模型暂时关闭 OpenAI tracing
set_tracing_disabled(True)