from agent_service import AgentService


def read_user_input() -> str:
    """
    支持多行输入。

    输入 /send 发送消息。
    """

    print(
        "\n你："
        "\n（支持多行输入，"
        "完成后单独输入 /send）"
    )

    lines = []

    while True:

        line = input()

        if (
            line.strip().lower()
            == "/send"
        ):
            break

        lines.append(line)

    return "\n".join(lines).strip()


def handle_approval(
    service: AgentService,
    result: dict,
) -> dict:
    """
    CLI 模式下处理审批。
    """

    while (
        result.get("status")
        == "approval_required"
    ):

        print(
            "\n[需要人工批准]"
        )

        print(
            f"工具："
            f"{result.get('tool')}"
        )

        print(
            f"参数："
            f"{result.get('arguments')}"
        )

        answer = input(
            "\n是否允许执行？(y/n)："
        ).strip().lower()

        if answer in {
            "y",
            "yes",
            "是",
        }:

            print(
                "已批准。"
            )

            result = (
                service.approve_current()
            )

        else:

            print(
                "已拒绝。"
            )

            result = (
                service.reject_current()
            )

    return result


def main():

    service = AgentService()

    print(
        "\n小智 Agent 已启动。"
    )

    print(
        "输入 /new 创建新对话。"
    )

    print(
        "输入 /session 查看当前 Session。"
    )

    print(
        "输入 exit / quit / 退出 结束程序。"
    )

    print(
        f"\n当前 Session："
        f"{service.get_session_id()}"
    )

    while True:

        user_input = read_user_input()

        if not user_input:
            continue

        command = (
            user_input
            .strip()
            .lower()
        )

        # =====================
        # 退出
        # =====================

        if command in {
            "exit",
            "quit",
            "退出",
        }:

            print(
                "程序结束"
            )

            break

        # =====================
        # 新 Session
        # =====================

        if command in {
            "/new",
            "新对话",
        }:

            session_id = (
                service.new_session()
            )

            print(
                "\n已创建新的对话。"
            )

            print(
                f"Session：{session_id}"
            )

            continue

        # =====================
        # 当前 Session
        # =====================

        if command == "/session":

            print(
                f"\n当前 Session："
                f"{service.get_session_id()}"
            )

            continue

        # =====================
        # Agent
        # =====================

        result = service.start_task(
            user_input
        )

        result = handle_approval(
            service,
            result,
        )

        if (
            result.get("status")
            == "completed"
        ):

            print(
                "\n小智：",
                result.get("message"),
            )

        elif (
            result.get("status")
            == "error"
        ):

            print(
                "\n[程序发生错误]",
                result.get("message"),
            )


if __name__ == "__main__":
    main()