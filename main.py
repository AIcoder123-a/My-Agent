from agents import Runner

from app_agents.personal_agent import (
    personal_agent,
)

from memory import (
    load_or_create_session,
    create_new_session,
)


def main():

    # 启动时继续上一次 Session
    session, session_id = (
        load_or_create_session()
    )

    print("\n小智 Agent 已启动。")

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
        f"\n当前 Session：{session_id}"
    )

    def read_user_input() -> str:
        """
        支持多行用户输入。

        输入 /send 表示发送整段消息。
        """

        print(
            "\n你："
            "\n（支持多行输入，完成后输入 /send）"
        )

        lines = []

        while True:

            line = input()

            if line.strip().lower() == "/send":
                break

            lines.append(line)

        return "\n".join(lines).strip()
    while True:

        user_input = read_user_input()

        if not user_input:
            continue

        if user_input.lower() in [
            "exit",
            "quit",
            "退出",
        ]:
            print("程序结束")
            break

        if user_input.lower() in [
            "/new",
            "新对话",
        ]:
            session, session_id = (
                create_new_session()
            )

            print(
                "\n已创建新的对话。"
            )

            print(
                f"Session：{session_id}"
            )

            continue

        try:

            result = Runner.run_sync(
                personal_agent,
                user_input,
                session=session,
                max_turns=10,
            )

            print(
                "\n小智：",
                result.final_output,
            )

        except Exception as e:

            print(
                "\n[程序发生错误]",
                e,
            )

if __name__ == "__main__":
    main()