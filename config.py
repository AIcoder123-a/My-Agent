import os
from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    OpenAIProvider,
    set_tracing_disabled,
)

load_dotenv()

api_key = os.getenv("MODEL_API_KEY")
base_url = os.getenv("MODEL_BASE_URL")
model_name = os.getenv("MODEL_NAME")

if not api_key:
    raise ValueError("缺少 MODEL_API_KEY")

if not base_url:
    raise ValueError("缺少 MODEL_BASE_URL")

if not model_name:
    raise ValueError("缺少 MODEL_NAME")


client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url,
)


model_provider = OpenAIProvider(
    openai_client=client,
    use_responses=False,
    buffer_streamed_tool_calls=True,
)


# personal_agent.py 仍然可以：
# from config import model
model = OpenAIChatCompletionsModel(
    model=model_name,
    openai_client=client,
    buffer_streamed_tool_calls=True,
)


set_tracing_disabled(True)