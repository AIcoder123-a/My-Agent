import json
import os
import uuid
from copy import deepcopy
from importlib.metadata import (
    PackageNotFoundError,
    version,
)
from pathlib import Path
from typing import Any


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

DATA_DIR = (
    PROJECT_ROOT
    / "data"
)

MCP_CONFIG_PATH = (
    DATA_DIR
    / "mcp_servers.json"
)

KEYRING_SERVICE = (
    "xiaozhi-agent-mcp"
)


DEFAULT_DOCUMENT = {
    "version": 1,
    "servers": [],
}


def _load_keyring():

    try:
        import keyring

        return keyring

    except ImportError as error:

        raise RuntimeError(
            "尚未安装 keyring。"
            "请运行：pip install keyring"
        ) from error


def _secret_account(
    server_id: str,
    secret_kind: str,
) -> str:

    return (
        f"{server_id}:{secret_kind}"
    )


def _read_secret(
    server_id: str,
    secret_kind: str,
) -> dict[str, str]:

    if not server_id:
        return {}

    try:

        keyring = _load_keyring()

        raw = (
            keyring.get_password(
                KEYRING_SERVICE,
                _secret_account(
                    server_id,
                    secret_kind,
                ),
            )
            or ""
        ).strip()

        if not raw:
            return {}

        value = json.loads(
            raw
        )

        if not isinstance(
            value,
            dict,
        ):
            return {}

        return {
            str(key): str(item)
            for key, item
            in value.items()
        }

    except Exception:
        return {}


def _write_secret(
    server_id: str,
    secret_kind: str,
    value: dict[str, str],
) -> None:

    keyring = _load_keyring()

    account = _secret_account(
        server_id,
        secret_kind,
    )

    if not value:

        try:

            keyring.delete_password(
                KEYRING_SERVICE,
                account,
            )

        except Exception:
            pass

        return

    keyring.set_password(
        KEYRING_SERVICE,
        account,
        json.dumps(
            value,
            ensure_ascii=False,
        ),
    )


def _delete_all_secrets(
    server_id: str,
) -> None:

    for kind in (
        "headers",
        "env",
    ):

        _write_secret(
            server_id,
            kind,
            {},
        )


def _atomic_write(
    document: dict,
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = (
        MCP_CONFIG_PATH
        .with_suffix(
            ".json.tmp"
        )
    )

    temp.write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(
        MCP_CONFIG_PATH
    )


def read_mcp_document() -> dict:

    if not MCP_CONFIG_PATH.exists():

        return deepcopy(
            DEFAULT_DOCUMENT
        )

    try:

        value = json.loads(
            MCP_CONFIG_PATH.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return deepcopy(
            DEFAULT_DOCUMENT
        )

    if not isinstance(
        value,
        dict,
    ):

        return deepcopy(
            DEFAULT_DOCUMENT
        )

    servers = value.get(
        "servers",
        [],
    )

    if not isinstance(
        servers,
        list,
    ):
        servers = []

    return {
        "version": int(
            value.get(
                "version",
                1,
            )
            or 1
        ),
        "servers": [
            item
            for item in servers
            if isinstance(
                item,
                dict,
            )
        ],
    }


def write_mcp_document(
    document: dict,
) -> dict:

    clean = {
        "version": 1,
        "servers": (
            document.get(
                "servers",
                [],
            )
            if isinstance(
                document,
                dict,
            )
            else []
        ),
    }

    _atomic_write(
        clean
    )

    return clean


def list_mcp_servers() -> list[dict]:

    servers = (
        read_mcp_document()
        .get(
            "servers",
            [],
        )
    )

    result = []

    for server in servers:

        item = deepcopy(
            server
        )

        server_id = str(
            item.get(
                "id",
                "",
            )
        )

        item[
            "has_secret_headers"
        ] = bool(
            _read_secret(
                server_id,
                "headers",
            )
        )

        item[
            "has_secret_env"
        ] = bool(
            _read_secret(
                server_id,
                "env",
            )
        )

        result.append(
            item
        )

    return result


def get_mcp_server(
    server_id: str,
) -> dict | None:

    server_id = (
        server_id
        or ""
    ).strip()

    for server in list_mcp_servers():

        if str(
            server.get(
                "id",
                "",
            )
        ) == server_id:

            return server

    return None


def _normalize_string_dict(
    value: Any,
) -> dict[str, str]:

    if not isinstance(
        value,
        dict,
    ):
        return {}

    return {
        str(key): str(item)
        for key, item
        in value.items()
    }


def _normalize_args(
    value: Any,
) -> list[str]:

    if not isinstance(
        value,
        list,
    ):
        return []

    return [
        str(item)
        for item in value
    ]


def _clean_server(
    server: dict,
) -> dict:

    transport = str(
        server.get(
            "transport",
            "stdio",
        )
    ).strip()

    if transport not in {
        "stdio",
        "streamable_http",
    }:
        transport = "stdio"

    approval = str(
        server.get(
            "require_approval",
            "always",
        )
    ).strip()

    if approval not in {
        "always",
        "never",
    }:
        approval = "always"

    timeout = int(
        server.get(
            "timeout_seconds",
            10,
        )
        or 10
    )

    timeout = max(
        3,
        min(
            120,
            timeout,
        ),
    )

    return {
        "id": str(
            server.get(
                "id",
                "",
            )
            or uuid.uuid4()
        ),
        "name": str(
            server.get(
                "name",
                "",
            )
        ).strip()
        or "未命名 MCP",
        "transport":
            transport,
        "enabled": bool(
            server.get(
                "enabled",
                False,
            )
        ),
        "require_approval":
            approval,
        "cache_tools_list": bool(
            server.get(
                "cache_tools_list",
                True,
            )
        ),
        "timeout_seconds":
            timeout,

        "stdio": {
            "command": str(
                (
                    server.get(
                        "stdio",
                        {},
                    )
                    or {}
                ).get(
                    "command",
                    "",
                )
            ).strip(),
            "args": _normalize_args(
                (
                    server.get(
                        "stdio",
                        {},
                    )
                    or {}
                ).get(
                    "args",
                    [],
                )
            ),
            "cwd": str(
                (
                    server.get(
                        "stdio",
                        {},
                    )
                    or {}
                ).get(
                    "cwd",
                    "",
                )
            ).strip(),
            "env": _normalize_string_dict(
                (
                    server.get(
                        "stdio",
                        {},
                    )
                    or {}
                ).get(
                    "env",
                    {},
                )
            ),
        },

        "http": {
            "url": str(
                (
                    server.get(
                        "http",
                        {},
                    )
                    or {}
                ).get(
                    "url",
                    "",
                )
            ).strip(),
            "headers":
                _normalize_string_dict(
                    (
                        server.get(
                            "http",
                            {},
                        )
                        or {}
                    ).get(
                        "headers",
                        {},
                    )
                ),
        },
    }


def save_mcp_server(
    server: dict,
    *,
    secret_headers: dict[str, str] | None = None,
    secret_env: dict[str, str] | None = None,
    replace_secret_headers: bool = False,
    replace_secret_env: bool = False,
) -> dict:

    clean = _clean_server(
        server
    )

    document = (
        read_mcp_document()
    )

    servers = (
        document.get(
            "servers",
            []
        )
    )

    found = False

    for index, current in enumerate(
        servers
    ):

        if str(
            current.get(
                "id",
                "",
            )
        ) == clean["id"]:

            servers[index] = clean
            found = True
            break

    if not found:

        servers.append(
            clean
        )

    document[
        "servers"
    ] = servers

    write_mcp_document(
        document
    )

    if replace_secret_headers:

        _write_secret(
            clean["id"],
            "headers",
            secret_headers
            or {},
        )

    if replace_secret_env:

        _write_secret(
            clean["id"],
            "env",
            secret_env
            or {},
        )

    return get_mcp_server(
        clean["id"]
    ) or clean


def delete_mcp_server(
    server_id: str,
) -> bool:

    server_id = (
        server_id
        or ""
    ).strip()

    if not server_id:
        return False

    document = (
        read_mcp_document()
    )

    servers = (
        document.get(
            "servers",
            [],
        )
    )

    remaining = [
        server
        for server in servers
        if str(
            server.get(
                "id",
                "",
            )
        ) != server_id
    ]

    changed = (
        len(remaining)
        != len(servers)
    )

    if changed:

        document[
            "servers"
        ] = remaining

        write_mcp_document(
            document
        )

        _delete_all_secrets(
            server_id
        )

    return changed


def set_mcp_server_enabled(
    server_id: str,
    enabled: bool,
) -> bool:

    server = get_mcp_server(
        server_id
    )

    if not server:
        return False

    server["enabled"] = bool(
        enabled
    )

    save_mcp_server(
        server
    )

    return True


def parse_json_object_text(
    text: str,
    *,
    field_name: str,
) -> dict[str, str]:

    text = (
        text
        or ""
    ).strip()

    if not text:
        return {}

    try:

        value = json.loads(
            text
        )

    except json.JSONDecodeError as error:

        raise ValueError(
            f"{field_name} 必须是合法 JSON 对象："
            f"{error.msg}"
        ) from error

    if not isinstance(
        value,
        dict,
    ):

        raise ValueError(
            f"{field_name} 必须使用 JSON 对象格式。"
        )

    return {
        str(key): str(item)
        for key, item
        in value.items()
    }


def parse_args_text(
    text: str,
) -> list[str]:
    """
    一行一个参数。

    这样在 Windows 路径场景下比 shell 字符串更稳定，
    不会因为引号规则不同而意外拆分。
    """

    return [
        line.strip()
        for line in (
            text
            or ""
        ).splitlines()
        if line.strip()
    ]


def format_args_text(
    args: list[str] | None,
) -> str:

    return "\n".join(
        str(item)
        for item in (
            args
            or []
        )
    )


def mcp_server_choices():

    choices = []

    for server in list_mcp_servers():

        prefix = (
            "●"
            if server.get(
                "enabled"
            )
            else "○"
        )

        transport = (
            "stdio"
            if server.get(
                "transport"
            ) == "stdio"
            else "HTTP"
        )

        label = (
            f"{prefix} "
            f"{server.get('name', '未命名 MCP')} "
            f"· {transport}"
        )

        choices.append(
            (
                label,
                server.get(
                    "id",
                    "",
                ),
            )
        )

    return choices


def mcp_server_rows():

    rows = []

    for server in list_mcp_servers():

        transport = (
            "本地 stdio"
            if server.get(
                "transport"
            ) == "stdio"
            else "Streamable HTTP"
        )

        target = ""

        if (
            server.get(
                "transport"
            )
            == "stdio"
        ):

            target = (
                (
                    server.get(
                        "stdio",
                        {},
                    )
                    or {}
                ).get(
                    "command",
                    "",
                )
            )

        else:

            target = (
                (
                    server.get(
                        "http",
                        {},
                    )
                    or {}
                ).get(
                    "url",
                    "",
                )
            )

        secret_status = []

        if server.get(
            "has_secret_headers"
        ):
            secret_status.append(
                "安全请求头"
            )

        if server.get(
            "has_secret_env"
        ):
            secret_status.append(
                "安全环境变量"
            )

        rows.append(
            [
                server.get(
                    "name",
                    "",
                ),
                transport,
                (
                    "已启用"
                    if server.get(
                        "enabled"
                    )
                    else "已停用"
                ),
                (
                    "每次批准"
                    if server.get(
                        "require_approval"
                    )
                    == "always"
                    else "自动执行"
                ),
                "、".join(
                    secret_status
                )
                or "无",
                target,
            ]
        )

    return rows


def mcp_dependency_status() -> dict:

    result = {
        "openai_agents":
            "未知",
        "mcp":
            "未安装",
        "ready":
            False,
    }

    try:

        result[
            "openai_agents"
        ] = version(
            "openai-agents"
        )

    except PackageNotFoundError:
        pass

    try:

        result[
            "mcp"
        ] = version(
            "mcp"
        )

        result[
            "ready"
        ] = True

    except PackageNotFoundError:
        pass

    return result


def _merged_stdio_env(
    server: dict,
) -> dict[str, str]:

    server_id = str(
        server.get(
            "id",
            "",
        )
    )

    normal_env = (
        (
            server.get(
                "stdio",
                {},
            )
            or {}
        ).get(
            "env",
            {},
        )
        or {}
    )

    secret_env = (
        _read_secret(
            server_id,
            "env",
        )
    )

    result = {
        str(key): str(value)
        for key, value
        in os.environ.items()
    }

    result.update(
        _normalize_string_dict(
            normal_env
        )
    )

    result.update(
        secret_env
    )

    return result


def _merged_http_headers(
    server: dict,
) -> dict[str, str]:

    server_id = str(
        server.get(
            "id",
            "",
        )
    )

    normal_headers = (
        (
            server.get(
                "http",
                {},
            )
            or {}
        ).get(
            "headers",
            {},
        )
        or {}
    )

    secret_headers = (
        _read_secret(
            server_id,
            "headers",
        )
    )

    result = (
        _normalize_string_dict(
            normal_headers
        )
    )

    result.update(
        secret_headers
    )

    return result


def build_mcp_server(
    server_config: dict,
):
    """
    根据已保存配置构造 OpenAI Agents SDK MCP server 实例。

    注意：这里只构造对象，不自动 connect。
    """

    try:

        from agents.mcp import (
            MCPServerStdio,
            MCPServerStreamableHttp,
        )

    except Exception as error:

        raise RuntimeError(
            "MCP 运行依赖不可用。"
            "请先安装：pip install \"mcp>=1.19.0,<3\""
        ) from error

    server = _clean_server(
        server_config
    )

    approval = (
        "always"
        if server.get(
            "require_approval"
        )
        == "always"
        else "never"
    )

    common = {
        "name":
            server["name"],
        "cache_tools_list":
            bool(
                server.get(
                    "cache_tools_list",
                    True,
                )
            ),
        "client_session_timeout_seconds":
            float(
                server.get(
                    "timeout_seconds",
                    10,
                )
            ),
        "require_approval":
            approval,
    }

    if (
        server.get(
            "transport"
        )
        == "stdio"
    ):

        stdio = (
            server.get(
                "stdio",
                {},
            )
            or {}
        )

        command = str(
            stdio.get(
                "command",
                "",
            )
        ).strip()

        if not command:

            raise ValueError(
                "stdio MCP 缺少启动命令。"
            )

        params: dict[str, Any] = {
            "command":
                command,
            "args":
                _normalize_args(
                    stdio.get(
                        "args",
                        [],
                    )
                ),
            "env":
                _merged_stdio_env(
                    server
                ),
        }

        cwd = str(
            stdio.get(
                "cwd",
                "",
            )
        ).strip()

        if cwd:

            params["cwd"] = cwd

        return MCPServerStdio(
            params=params,
            **common,
        )

    http = (
        server.get(
            "http",
            {},
        )
        or {}
    )

    url = str(
        http.get(
            "url",
            "",
        )
    ).strip()

    if not (
        url.startswith(
            "http://"
        )
        or url.startswith(
            "https://"
        )
    ):

        raise ValueError(
            "Streamable HTTP MCP 需要有效的 "
            "http:// 或 https:// URL。"
        )

    params = {
        "url": url,
        "headers":
            _merged_http_headers(
                server
            ),
        "timeout":
            float(
                server.get(
                    "timeout_seconds",
                    10,
                )
            ),
    }

    return MCPServerStreamableHttp(
        params=params,
        **common,
    )


def _tool_description(
    tool: Any,
) -> str:

    value = getattr(
        tool,
        "description",
        "",
    )

    return str(
        value
        or ""
    ).strip()


def _tool_name(
    tool: Any,
) -> str:

    value = getattr(
        tool,
        "name",
        "",
    )

    return str(
        value
        or ""
    ).strip()


async def test_mcp_server(
    server_id: str,
) -> dict:

    server_config = (
        get_mcp_server(
            server_id
        )
    )

    if not server_config:

        return {
            "ok": False,
            "error":
                "没有找到该 MCP 服务器配置。",
            "tools": [],
        }

    dependency = (
        mcp_dependency_status()
    )

    if not dependency.get(
        "ready"
    ):

        return {
            "ok": False,
            "error": (
                "尚未安装 MCP Python SDK。"
                "请运行：pip install \"mcp>=1.19.0,<3\""
            ),
            "tools": [],
            "dependency":
                dependency,
        }

    try:

        server = build_mcp_server(
            server_config
        )

        async with server:

            tools = await (
                server.list_tools()
            )

        clean_tools = [
            {
                "name":
                    _tool_name(
                        tool
                    ),
                "description":
                    _tool_description(
                        tool
                    ),
            }
            for tool in (
                tools
                or []
            )
        ]

        return {
            "ok": True,
            "server_id":
                server_id,
            "server_name":
                server_config.get(
                    "name",
                    "",
                ),
            "transport":
                server_config.get(
                    "transport",
                    "",
                ),
            "tool_count":
                len(clean_tools),
            "tools":
                clean_tools,
            "dependency":
                dependency,
            "error": "",
        }

    except Exception as error:

        return {
            "ok": False,
            "server_id":
                server_id,
            "server_name":
                server_config.get(
                    "name",
                    "",
                ),
            "tools": [],
            "dependency":
                dependency,
            "error": (
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }
