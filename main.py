import os
from datetime import datetime

from openai import AsyncOpenAI
from dotenv import load_dotenv

from agents import (
    Agent,
    Runner,
    OpenAIChatCompletionsModel,
    set_tracing_disabled,
    SQLiteSession,
)

from agents.decorators import tool


# =========================================================
# 1. 加载 .env 配置
# =========================================================

load_dotenv()

api_key = os.getenv("MODEL_API_KEY")
base_url = os.getenv("MODEL_BASE_URL")
model_name = os.getenv("MODEL_NAME")


# 检查配置是否成功读取
if not api_key:
    raise ValueError("缺少 MODEL_API_KEY，请检查 .env")

if not base_url:
    raise ValueError("缺少 MODEL_BASE_URL，请检查 .env")

if not model_name:
    raise ValueError("缺少 MODEL_NAME，请检查 .env")


# =========================================================
# 2. Tool 1：计算器
# =========================================================

@tool
def calculator(
    a: float,
    b: float,
    operation: str
) -> float:
    """
    执行基础数学运算。

    Args:
        a: 第一个数字。
        b: 第二个数字。
        operation: 运算类型，只能是
                   add、subtract、multiply、divide。
    """

    print(
        f"\n[工具调用] calculator"
        f"(a={a}, b={b}, operation={operation})"
    )

    if operation == "add":
        return a + b

    elif operation == "subtract":
        return a - b

    elif operation == "multiply":
        return a * b

    elif operation == "divide":

        if b == 0:
            raise ValueError("除数不能为 0")

        return a / b

    else:
        raise ValueError(
            f"不支持的 operation：{operation}"
        )


# =========================================================
# 3. Tool 2：获取当前时间
# =========================================================

@tool
def get_current_time() -> str:
    """
    获取当前电脑所在时区的本地日期和时间。
    当用户询问现在几点、当前时间、今天日期等问题时使用。
    """

    print("\n[工具调用] get_current_time()")

    now = datetime.now().astimezone()

    return now.strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )

@tool
def save_note(content: str) -> str:
    """
    把用户希望记住的内容保存到本地 notes.txt 文件中。

    Args:
        content: 用户希望保存的具体内容。
    """

    print(f"\n[工具调用] save_note(content={content})")

    with open(
        "notes.txt",
        "a",
        encoding="utf-8"
    ) as file:

        file.write(content + "\n")

    return "笔记已成功保存。"


@tool
def read_notes() -> str:
    """
    读取用户之前保存到本地 notes.txt 中的所有笔记。
    当用户询问之前记录过什么、查看笔记等问题时使用。
    """

    print("\n[工具调用] read_notes()")

    if not os.path.exists("notes.txt"):
        return "目前还没有保存任何笔记。"

    with open(
        "notes.txt",
        "r",
        encoding="utf-8"
    ) as file:

        content = file.read()

    if not content.strip():
        return "目前还没有保存任何笔记。"

    return content


# =========================================================
# 4. 创建第三方模型客户端
# =========================================================

client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url,
)


# =========================================================
# 5. 创建模型
# =========================================================

model = OpenAIChatCompletionsModel(
    model=model_name,
    openai_client=client,
)


# 使用第三方 API，关闭 OpenAI tracing
set_tracing_disabled(True)


# =========================================================
# 6. 创建 Agent
# =========================================================

agent = Agent(
    name="小智",

    instructions="""
    你是一个中文 AI Agent。
    请始终使用中文回答用户。

    你拥有以下工具：

    1. calculator
    用于执行数学计算。

    2. get_current_time
    用于获取当前真实日期和时间。

    3. save_note
    当用户明确要求记录、保存、记住某件事情时使用。

    4. read_notes
    当用户询问之前保存过什么内容、查看笔记时使用。

    如果用户要求记录某件事情，
    必须使用 save_note，
    不要只口头说“记住了”。

    如果用户询问保存过的笔记，
    必须使用 read_notes。

    数学计算优先使用 calculator。

    当前日期或时间必须使用 get_current_time。

    如果问题不需要工具，
    可以直接回答。
 """,
    model=model,

    tools=[
        calculator,
        get_current_time,
        save_note,
        read_notes,
    ]
)

# =========================================================
# 7. 创建会话 Session
# =========================================================

project_dir = os.path.dirname(
    os.path.abspath(__file__)
)

database_path = os.path.join(
    project_dir,
    "conversation_history.db"
)

session = SQLiteSession(
    "main_conversation",
    database_path,
)


# =========================================================
# 7. Agent 主循环
# =========================================================

while True:

    user_input = input("\n你：")

    if user_input.lower() in [
        "exit",
        "quit",
        "退出",
    ]:
        print("程序结束")
        break

    try:

        result = Runner.run_sync(
            agent,
            user_input,
            session=session,

        )

        print(
            "\n小智：",
            result.final_output
        )

    except Exception as e:

        print(
            "\n[程序发生错误]",
            e
        )