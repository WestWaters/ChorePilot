#!/usr/bin/env python3
"""A fake Telegram Bot API on 127.0.0.1, for trying `chorepilot telegram` without a real bot.

    python3 scripts/fake_telegram.py --port 8099 &
    CHOREPILOT_TELEGRAM_API=http://127.0.0.1:8099 CHOREPILOT_TELEGRAM_TOKEN=1:FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE \
        chorepilot telegram run

The bot's side is the real Bot API surface it uses (getMe, getUpdates with long polling,
sendMessage, editMessageText, deleteMessage, answerCallbackQuery). The "user" side is two
extra endpoints:
    POST /_say    {"user": 11, "text": "hi", "chat": 11, "private": true}   a message
    POST /_press  {"user": 11, "data": "m:3:y"}                              a button press
    GET  /_sent                                                              what the bot sent
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"updates": [], "sent": [], "calls": [], "uid": 0, "mid": 1000}
LOCK = threading.Condition()


def _next():
    STATE["uid"] += 1
    return STATE["uid"]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/_sent":
            with LOCK:
                return self._json(200, STATE["sent"])
        self._json(404, {"ok": False})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path in ("/_say", "/_press"):
            with LOCK:
                uid = _next()
                user = body["user"]
                chat = {"id": body.get("chat", user),
                        "type": "private" if body.get("private", True) else "group"}
                if self.path == "/_say":
                    up = {"update_id": uid, "message": {"message_id": uid, "from": {"id": user},
                                                        "chat": chat, "text": body["text"]}}
                else:
                    up = {"update_id": uid, "callback_query": {
                        "id": str(uid), "from": {"id": user}, "data": body["data"],
                        "message": {"message_id": body.get("message_id", 1), "chat": chat}}}
                STATE["updates"].append(up)
                LOCK.notify_all()
            return self._json(200, {"ok": True, "update_id": uid})
        parts = self.path.split("/")
        if len(parts) != 3 or not parts[1].startswith("bot"):
            return self._json(404, {"ok": False, "description": "Not Found"})
        method = parts[2]
        with LOCK:
            STATE["calls"].append(method)
        if method == "getMe":
            return self._json(200, {"ok": True, "result": {"id": 1, "is_bot": True,
                                                           "username": "fake_chorepilot_bot"}})
        if method == "getUpdates":
            off = body.get("offset") or 0
            end = time.time() + min(float(body.get("timeout") or 0), 5)
            with LOCK:
                while True:
                    ups = [u for u in STATE["updates"] if u["update_id"] >= off]
                    if ups or time.time() >= end:
                        break
                    LOCK.wait(max(0.05, end - time.time()))
            return self._json(200, {"ok": True, "result": ups})
        if method == "sendMessage":
            with LOCK:
                STATE["mid"] += 1
                rec = {"message_id": STATE["mid"], "chat_id": body["chat_id"], "text": body["text"],
                       "buttons": [b["callback_data"] for row in (body.get("reply_markup") or {})
                                   .get("inline_keyboard", []) for b in row]}
                STATE["sent"].append(rec)
            return self._json(200, {"ok": True, "result": {"message_id": rec["message_id"],
                                                           "chat": {"id": body["chat_id"]}}})
        if method in ("editMessageText", "deleteMessage", "answerCallbackQuery"):
            with LOCK:
                STATE["sent"].append({"method": method, **{k: v for k, v in body.items()
                                                           if k != "text" or method != "deleteMessage"}})
            return self._json(200, {"ok": True, "result": True})
        return self._json(400, {"ok": False, "description": f"unsupported method {method}"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"fake Telegram Bot API on http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()
