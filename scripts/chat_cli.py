"""终端聊天客户端 — 手测真实后端 /chat SSE 流。

用法:
    uv run python scripts/chat_cli.py
    uv run python scripts/chat_cli.py --user demo_user_2
    uv run python scripts/chat_cli.py --url http://localhost:8000

交互命令:
    /new        开新 session(清上下文,同用户)
    /user <id>  切用户(会同时开新 session)
    /quit       退出
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Iterator

import httpx


def parse_sse_stream(response: httpx.Response) -> Iterator[tuple[str, dict]]:
    event_name = ""
    data_buf: list[str] = []
    for raw_line in response.iter_lines():
        line = raw_line.rstrip("\r")
        if line == "":
            if event_name and data_buf:
                try:
                    payload = json.loads("\n".join(data_buf))
                except json.JSONDecodeError:
                    payload = {"_raw": "\n".join(data_buf)}
                yield event_name, payload
            event_name = ""
            data_buf = []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_buf.append(line[5:].lstrip())


def render_event(ev: str, data: dict) -> None:
    if ev == "meta":
        print(
            f"\n[meta] session={data.get('session_id')} turn={data.get('turn_id')}",
            flush=True,
        )
    elif ev == "thinking":
        delta = data.get("delta", "")
        if delta.strip():
            print(f"[thinking] {delta[:120]}", flush=True)
    elif ev == "tool_call":
        name = data.get("name", "?")
        args = data.get("args") or {}
        args_preview = json.dumps(args, ensure_ascii=False)
        if len(args_preview) > 200:
            args_preview = args_preview[:200] + "..."
        print(f"\n[tool] {name} args={args_preview}", flush=True)
    elif ev == "card":
        kind = data.get("kind") or data.get("type") or "card"
        title = (
            data.get("title")
            or data.get("product_id")
            or data.get("order_id")
            or data.get("sku_id")
            or ""
        )
        print(f"\n[card:{kind}] {title}", flush=True)
        for k, v in data.items():
            if k in ("kind", "type"):
                continue
            preview = str(v).replace("\n", " ")
            if len(preview) > 100:
                preview = preview[:100] + "..."
            print(f"    {k}: {preview}", flush=True)
    elif ev == "text":
        print(data.get("delta", ""), end="", flush=True)
    elif ev == "suggestions":
        items = data.get("items") or []
        print("\n[suggestions]", flush=True)
        for i, s in enumerate(items, 1):
            label = s.get("label") or s.get("text") or json.dumps(s, ensure_ascii=False)
            print(f"    {i}. {label}", flush=True)
    elif ev == "done":
        print(f"\n[done] finish_reason={data.get('finish_reason')}", flush=True)
    elif ev == "error":
        print(f"\n[error:{data.get('code')}] {data.get('msg')}", flush=True)
    else:
        print(f"\n[{ev}] {data}", flush=True)


def chat_turn(
    client: httpx.Client, url: str, user_id: str, session_id: str, query: str
) -> None:
    headers = {
        "X-User-Id": user_id,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {"query": query, "session_id": session_id}
    with client.stream(
        "POST", f"{url}/chat", headers=headers, json=body, timeout=None
    ) as response:
        if response.status_code != 200:
            err_body = response.read().decode("utf-8", "ignore")
            print(f"\n[HTTP {response.status_code}] {err_body}")
            return
        for ev, data in parse_sse_stream(response):
            render_event(ev, data)
            if ev == "done":
                break


def main() -> None:
    parser = argparse.ArgumentParser(description="ShopMind 终端聊天客户端")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--user", default="demo_user_1")
    args = parser.parse_args()

    user_id = args.user
    session_id = f"cli-{uuid.uuid4().hex[:8]}"
    print(f"ShopMind CLI — server={args.url} user={user_id} session={session_id}")
    print("命令: /new  /user <id>  /quit")

    with httpx.Client() as client:
        while True:
            try:
                query = input(f"\n[{user_id}] > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not query:
                continue
            if query in ("/quit", "/exit"):
                break
            if query == "/new":
                session_id = f"cli-{uuid.uuid4().hex[:8]}"
                print(f"new session: {session_id}")
                continue
            if query.startswith("/user "):
                user_id = query.split(maxsplit=1)[1].strip()
                session_id = f"cli-{uuid.uuid4().hex[:8]}"
                print(f"切换用户: {user_id} (new session: {session_id})")
                continue
            chat_turn(client, args.url, user_id, session_id, query)


if __name__ == "__main__":
    main()
