import json
import re
from typing import Literal

from agents.decorators import tool
from pydantic import BaseModel
from paths import DATA_DIR
from tool_errors import tool_error_to_model
from tool_logging import current_session_id, current_task_id


class PlanStep(BaseModel):
    title: str
    status: Literal['pending', 'in_progress', 'completed']


def plan_path(session_id: str):
    if not re.fullmatch(r'[\w-]{1,150}', session_id, flags=re.ASCII):
        raise ValueError('无效的会话标识。')
    return DATA_DIR / 'plans' / f'{session_id}.json'


@tool(failure_error_function=tool_error_to_model)
def update_plan(steps: list[PlanStep]) -> str:
    """创建或更新当前任务计划。2—12 步，最多一步进行中；每完成阶段更新状态。"""
    if not 2 <= len(steps) <= 12 or sum(s.status == 'in_progress' for s in steps) > 1:
        raise ValueError('计划需为 2—12 步，最多一步进行中。')
    if any(not s.title.strip() or len(s.title) > 160 for s in steps):
        raise ValueError('每步标题为 1—160 字。')
    session = current_session_id.get()
    if not session:
        raise ValueError('当前没有活动会话。')
    path = plan_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'task_id': current_task_id.get(), 'steps': [s.model_dump() for s in steps]}
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)
    return json.dumps(data, ensure_ascii=False)
