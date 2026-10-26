"""Optional Telegram notifications: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable."""
from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("bot")


def notify(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    body = json.dumps({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # notifications must never break trading
        log.warning("telegram notify failed: %s", e)
