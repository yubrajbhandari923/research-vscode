"""A local Telegram Bot API (HTTP): point `RESEARCH_TELEGRAM_API` at `.url`. Records every call; serves scripted updates.
Shared by test_chat_*.py and test_cli_e2e.py (the old in-file FakeTelegram moved here)."""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123456789:AAH-fake_token_for_tests_0123456789abc"


class FakeTelegram:
    def __init__(self, token=TOKEN):
        self.token = token
        self.sent, self.updates, self.calls, self.documents = [], [], [], []     # sent: texts (str); records in self.messages
        self.messages, self.edits, self.deleted, self.answered, self.commands = [], [], [], [], []
        self.fail_send = 0                  # the next N sendMessage calls answer HTTP 500
        self._mid, self._lock, outer = 100, threading.Lock(), self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                method = self.path.rsplit("/", 1)[-1]
                assert token in self.path, "wrong token in URL"
                ctype = self.headers.get("Content-Type", "")
                body = {} if ctype.startswith("multipart") else urllib.parse.parse_qs(raw.decode())
                body = {k: v[0] for k, v in body.items()}
                code, out = 200, outer.handle(method, body, raw, ctype)
                if out is None:
                    code, out = 500, {"ok": False, "description": "boom"}
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def handle(self, method, body, raw, ctype):
        with self._lock:
            self.calls.append(method)
            if method == "sendMessage":
                if self.fail_send:
                    self.fail_send -= 1
                    return None
                self._mid += 1
                self.sent.append(body["text"])
                self.messages.append({"chat": body["chat_id"], "text": body["text"], "id": self._mid, "markup": json.loads(body["reply_markup"]) if body.get("reply_markup") else None})
                return {"ok": True, "result": {"message_id": self._mid}}
            if method == "sendDocument":
                name = raw.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
                self.documents.append({"name": name, "size": len(raw)})
                return {"ok": True, "result": {"message_id": self._mid}}
            if method == "getUpdates":
                off = int(body.get("offset", "0"))
                return {"ok": True, "result": [u for u in self.updates if u["update_id"] >= off]}
            if method in ("editMessageText", "editMessageReplyMarkup"):
                self.edits.append({"method": method, **body})
            elif method == "deleteMessage":
                self.deleted.append(body)
            elif method == "answerCallbackQuery":
                self.answered.append(body)
            elif method == "setMyCommands":
                self.commands = json.loads(body["commands"])
            return {"ok": True, "result": True}

    # --- scripting
    def _next(self):
        return len(self.updates) + 1

    def say(self, text, chat=777, user=None, group=False, reply_to=None, bot=False):
        m = {"message_id": 5000 + self._next(), "chat": {"id": chat, "type": "supergroup" if group else "private"}, "text": text}
        if user is not None or group:
            m["from"] = {"id": user if user is not None else chat, "first_name": "U", "is_bot": bot}
        if reply_to:
            m["reply_to_message"] = {"message_id": reply_to}
        self.updates.append({"update_id": self._next(), "message": m})

    def click(self, data, chat=777, user=None, message_id=101):
        self.updates.append({"update_id": self._next(), "callback_query": {"id": f"cb{self._next()}", "data": data, "from": {"id": user if user is not None else chat, "first_name": "U"},
                                                                           "message": {"message_id": message_id, "chat": {"id": chat, "type": "private"}}}})

    def tokens(self, contains=None):
        """callback tokens of the buttons on the last (matching) message"""
        for m in reversed(self.messages):
            if m["markup"] and (contains is None or contains in m["text"]):
                return {b["text"]: b["callback_data"] for row in m["markup"]["inline_keyboard"] for b in row}
        return {}

    def wait_for(self, needle, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            hit = [m for m in self.sent if needle in m]
            if hit:
                return hit[-1]
            time.sleep(0.3)
        raise AssertionError(f"no Telegram message containing {needle!r}; got {self.sent}")

    def stop(self):
        self.srv.shutdown()
