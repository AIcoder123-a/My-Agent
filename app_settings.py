import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = (
    PROJECT_ROOT
    / "data"
)

SETTINGS_PATH = (
    DATA_DIR
    / "settings.json"
)

KEYRING_SERVICE = (
    "xiaozhi-agent"
)

MODEL_API_KEY_ACCOUNT = (
    "MODEL_API_KEY"
)


DEFAULT_SETTINGS = {
    "general": {
        "language": "zh-CN",
        "open_browser": True,
    },

    "model": {
        "provider_name":
            "OpenAI 兼容接口",
        "base_url": "",
        "model_name": "",
    },

    "agent": {
        "max_turns": 10,
    },

    "web": {
        "normal_search_budget": 4,
        "research_search_budget": 6,
        "fetch_timeout_seconds": 15,
    },

    "mcp": {
        "enabled": True,
    },
}


def _merge_dict(
    base: dict,
    override: dict,
) -> dict:

    result = deepcopy(
        base
    )

    for key, value in (
        override or {}
    ).items():

        if (
            isinstance(
                value,
                dict,
            )
            and isinstance(
                result.get(key),
                dict,
            )
        ):

            result[key] = (
                _merge_dict(
                    result[key],
                    value,
                )
            )

        else:

            result[key] = value

    return result


def read_settings() -> dict:
    """
    读取非敏感设置。

    API Key 不存放在 settings.json。
    """

    if not SETTINGS_PATH.exists():

        return deepcopy(
            DEFAULT_SETTINGS
        )

    try:

        data = json.loads(
            SETTINGS_PATH.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(
            data,
            dict,
        ):
            data = {}

    except Exception:
        data = {}

    return _merge_dict(
        DEFAULT_SETTINGS,
        data,
    )


def write_settings(
    settings: dict,
) -> dict:
    """
    原子写入非敏感设置。
    """

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    clean = _merge_dict(
        DEFAULT_SETTINGS,
        settings or {},
    )

    temp_path = (
        SETTINGS_PATH
        .with_suffix(
            ".json.tmp"
        )
    )

    temp_path.write_text(
        json.dumps(
            clean,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp_path.replace(
        SETTINGS_PATH
    )

    return clean


def update_settings(
    patch: dict,
) -> dict:

    current = read_settings()

    merged = _merge_dict(
        current,
        patch or {},
    )

    return write_settings(
        merged
    )


def get_setting(
    dotted_key: str,
    default: Any = None,
) -> Any:

    value: Any = (
        read_settings()
    )

    for part in (
        dotted_key
        or ""
    ).split("."):

        if not part:
            continue

        if not isinstance(
            value,
            dict,
        ):
            return default

        if part not in value:
            return default

        value = value[
            part
        ]

    return value


def get_int_setting(
    dotted_key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:

    try:

        value = int(
            get_setting(
                dotted_key,
                default,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        value = int(
            default
        )

    if minimum is not None:
        value = max(
            minimum,
            value,
        )

    if maximum is not None:
        value = min(
            maximum,
            value,
        )

    return value


def get_bool_setting(
    dotted_key: str,
    default: bool,
) -> bool:

    value = get_setting(
        dotted_key,
        default,
    )

    if isinstance(
        value,
        bool,
    ):
        return value

    if isinstance(
        value,
        str,
    ):
        return (
            value.strip().lower()
            in {
                "1",
                "true",
                "yes",
                "on",
            }
        )

    return bool(value)


def get_str_setting(
    dotted_key: str,
    default: str = "",
) -> str:

    value = get_setting(
        dotted_key,
        default,
    )

    if value is None:
        return default

    return str(value)


def _load_keyring():
    """
    延迟导入 keyring。

    这样即使依赖还没安装，
    GUI 仍可以启动并显示明确提示。
    """

    try:
        import keyring

        return keyring

    except ImportError as error:

        raise RuntimeError(
            "尚未安装安全密钥存储依赖 keyring。"
            "请运行：pip install keyring"
        ) from error


def set_model_api_key(
    api_key: str,
) -> None:

    value = (
        api_key
        or ""
    ).strip()

    if not value:

        raise ValueError(
            "API Key 不能为空。"
        )

    keyring = _load_keyring()

    keyring.set_password(
        KEYRING_SERVICE,
        MODEL_API_KEY_ACCOUNT,
        value,
    )


def get_keyring_model_api_key() -> str:

    try:

        keyring = _load_keyring()

        value = (
            keyring.get_password(
                KEYRING_SERVICE,
                MODEL_API_KEY_ACCOUNT,
            )
            or ""
        )

        return value.strip()

    except Exception:
        return ""


def delete_model_api_key() -> None:

    keyring = _load_keyring()

    try:

        keyring.delete_password(
            KEYRING_SERVICE,
            MODEL_API_KEY_ACCOUNT,
        )

    except Exception as error:

        # 某些 backend 在条目不存在时会抛异常。
        if not get_keyring_model_api_key():
            return

        raise error


def get_model_api_key() -> str:
    """
    运行时优先级：

    1. Windows Credential Manager / keyring
    2. 旧 .env / 环境变量 MODEL_API_KEY

    这样可以平滑迁移旧项目。
    """

    secure_value = (
        get_keyring_model_api_key()
    )

    if secure_value:
        return secure_value

    return (
        os.getenv(
            "MODEL_API_KEY",
            "",
        )
        or ""
    ).strip()


def _masked(
    value: str,
) -> str:

    value = (
        value
        or ""
    ).strip()

    if not value:
        return "未配置"

    if len(value) <= 8:
        return "••••••••"

    return (
        value[:3]
        + "••••••••"
        + value[-4:]
    )


def model_api_key_status() -> dict:
    """
    仅返回脱敏状态，不返回完整密钥。
    """

    secure = (
        get_keyring_model_api_key()
    )

    env_value = (
        os.getenv(
            "MODEL_API_KEY",
            "",
        )
        or ""
    ).strip()

    if secure:

        return {
            "configured": True,
            "source":
                "Windows 安全存储",
            "masked":
                _masked(secure),
            "legacy_env_present":
                bool(env_value),
        }

    if env_value:

        return {
            "configured": True,
            "source":
                ".env / 环境变量",
            "masked":
                _masked(env_value),
            "legacy_env_present":
                True,
        }

    return {
        "configured": False,
        "source": "未配置",
        "masked": "未配置",
        "legacy_env_present": False,
    }


def migrate_env_key_to_keyring() -> dict:
    """
    将旧 MODEL_API_KEY 复制进系统安全存储。

    不自动修改 .env，避免误删用户配置。
    """

    env_value = (
        os.getenv(
            "MODEL_API_KEY",
            "",
        )
        or ""
    ).strip()

    if not env_value:

        return {
            "ok": False,
            "message":
                "当前环境中没有可迁移的 MODEL_API_KEY。",
        }

    set_model_api_key(
        env_value
    )

    return {
        "ok": True,
        "message":
            "已复制到 Windows 安全存储。"
            "确认程序使用正常后，可以再手动移除 .env 中的 MODEL_API_KEY。",
    }
