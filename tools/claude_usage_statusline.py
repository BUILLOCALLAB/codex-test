"""Claude Code のステータスラインから Claude の利用枠(5時間枠・週間枠)を取り出してファイルに保存する。

Claude Code はステータスライン用のコマンドに、標準入力で JSON を渡してくる。
その中の rate_limits に 5時間枠(five_hour)と週間枠(seven_day)の「使用済み%」が入っている。
(claude.ai の Pro / Max 契約で、そのセッションで最初の応答が返ったあとだけ入る)
https://code.claude.com/docs/en/statusline

このスクリプトは
  1. 受け取った JSON から「残り%」を計算して USAGE_FILE に保存する
  2. ステータスラインに短い表示を出す
ダッシュボード側は load_usage() でファイルを読めば「Claude週間枠 残り」「Claude 5時間枠 残り」を表示できる。

使い方(Windows の %USERPROFILE%\\.claude\\settings.json):
  "statusLine": {"type": "command", "command": "python C:/path/to/tools/claude_usage_statusline.py"}
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# 保存先。環境変数 CLAUDE_USAGE_FILE があればそちらを使う
USAGE_FILE = Path(
    os.environ.get("CLAUDE_USAGE_FILE")
    or Path.home() / ".claude" / "claude_usage.json"
)

WINDOWS = {"five_hour": "5時間枠", "seven_day": "週間枠"}
# ダッシュボードの既存表示に合わせた見出し
DASHBOARD_LABELS = {"five_hour": "Claude 5時間枠 残り", "seven_day": "Claude週間枠 残り"}


def extract_usage(status, now=None):
    """ステータスラインの JSON から枠ごとの残り%を取り出す。取れなかった枠は入れない。"""
    now = time.time() if now is None else now
    rate_limits = status.get("rate_limits") or {}
    usage = {"updated_at": int(now)}
    for key in WINDOWS:
        window = rate_limits.get(key) or {}
        used = window.get("used_percentage")
        if used is None:
            continue
        used = min(max(float(used), 0.0), 100.0)
        usage[key] = {
            "used_percentage": round(used, 1),
            "remaining_percentage": round(100.0 - used, 1),
            "resets_at": window.get("resets_at"),
        }
    return usage


def save_usage(usage, path=None):
    """前回の値に今回取れた枠だけ上書きして保存する(片方だけ届いたときに、もう片方を消さないため)。"""
    path = Path(path or USAGE_FILE)
    merged = load_usage(path, now=0) or {}
    merged.update(usage)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 書きかけのファイルをダッシュボードが読まないよう、別名で書いてから置き換える
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False)
    os.replace(tmp, path)
    return merged


def load_usage(path=None, now=None):
    """ダッシュボード用。保存された残り%を読む。ファイルが無い・壊れているときは None。

    resets_at を過ぎた枠はもうリセットされているので、残り100%として返す。
    """
    path = Path(path or USAGE_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    now = time.time() if now is None else now
    for key in WINDOWS:
        window = data.get(key)
        if not isinstance(window, dict):
            data.pop(key, None)
            continue
        resets_at = window.get("resets_at")
        if now and resets_at and now >= resets_at:
            window.update(used_percentage=0.0, remaining_percentage=100.0, reset=True)
    return data


def format_remaining(usage, key):
    """ダッシュボードの表示用文字列。例: 'Claude週間枠 残り 73%' / 'Claude週間枠 残り 未取得'"""
    label = DASHBOARD_LABELS[key]
    window = (usage or {}).get(key)
    if not window:
        return f"{label} 未取得"
    return f"{label} {window['remaining_percentage']:.0f}%"


def statusline_text(usage):
    parts = [
        f"{WINDOWS[key]} 残り{usage[key]['remaining_percentage']:.0f}%"
        for key in WINDOWS
        if key in usage
    ]
    return " | ".join(parts) if parts else "Claude 残り 未取得"


def main():
    try:
        status = json.load(sys.stdin)
    except ValueError:
        status = {}
    usage = extract_usage(status)
    if any(key in usage for key in WINDOWS):
        try:
            save_usage(usage)
        except OSError:
            pass  # 保存に失敗してもステータスラインは止めない
    print(statusline_text(usage))


if __name__ == "__main__":
    main()
