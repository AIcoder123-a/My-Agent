from agents import Agent
from agents.agent import StopAtTools

from config import model

from agent_tools.calculator import calculator
from agent_tools.time_tools import get_current_time
from agent_tools.note_tools import (
    save_note,
    read_notes,
)

from agent_tools.file_tools import (
    list_files,
    read_file,
    write_file,
)

from agent_tools.finish_tools import (
    finish_task,
)


personal_agent = Agent(
    name="小智",

    instructions="""
    你是一个中文个人 AI Agent。
    始终使用中文回答用户。

    你的职责是理解用户最终目标，
    并使用提供的工具真正完成任务。


    文件任务：

    如果不知道 workspace 中有什么，
    使用 list_files。

    如果需要了解文件内容，
    使用 read_file。

    如果需要创建或修改文件，
    使用 write_file。


    多步骤任务：

    一个任务可以连续使用多个工具。

    根据每次工具返回的真实结果，
    判断下一步应该做什么。

    不要要求用户执行你自己可以通过工具完成的步骤。


    防止重复：

    不要重复读取已经读取且没有变化的文件。

    不要为了“再次确认”不断重复相同工具。

    文件成功写入后，
    如果用户目标已经满足，
    不要重复写入同一个文件。


    任务结束：

    当用户的所有要求都已经真正完成时，
    必须调用 finish_task。

    finish_task 每个任务只能调用一次。

    调用 finish_task 表示整个任务结束，
    此后不再执行任何其他工具。
    """,

    model=model,

    tools=[
        calculator,
        get_current_time,
        save_note,
        read_notes,
        list_files,
        read_file,
        write_file,
        finish_task,
    ],

    tool_use_behavior=StopAtTools(
        stop_at_tool_names=[
            "finish_task"
        ]
    ),

    reset_tool_choice=True,
)