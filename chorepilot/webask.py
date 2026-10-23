""""Ask the house" from the dashboard: the same agent + policy path as the terminal and Telegram.

    POST /api/ask {"text"}            -> {"job"}         the request runs in a worker thread
    GET  /api/ask/<job>               -> progress: tool steps, a pending question, the answer
    POST /api/ask/<job>/answer {...}  -> the signed-in person answers that question

The agent (agent.run) calls House tools exactly as it does for `chorepilot ask`. When the policy
engine wants a human, the House calls back into the job: a tier-2 "confirm" becomes an inline
card with Yes / No, a strong confirmation asks for the typed phrase, a tier-3 security action
asks for the owner's PIN or TOTP code again. Only the person who asked, in the same signed-in
session, can answer; nothing the model writes counts as an answer, and silence is a no.
A kid's tier-2 request is not asked at all: the House queues it for a parent.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time

from .agent import brief, run as agent_run
from .chores import ChoreDB

WAIT_S = 120          # how long a question waits for the person; then it is a no
KEEP_S = 600          # finished jobs are forgotten after this
MAX_PER_PERSON = 2
_SECRET_KEYS = ("pin", "code", "passcode", "password", "totp", "otp")


def human(q):
    """'Water with the lawn_sprinkler ... 80 F?' -> 'Water with the lawn sprinkler ... 80°F?'"""
    q = re.sub(r"(?<=[a-z])_(?=[a-z])", " ", str(q))
    return re.sub(r"(\d) ([FC])\b", r"\1°\2", q)


def llm_target(cfg):
    from .env import getenv
    llm = cfg.get("llm") or {}
    url = llm.get("url") or getenv("LLM_URL")
    return (url.rstrip("/") if url else None), llm.get("model")


class Job:
    def __init__(self, sid, person, text):
        self.id = secrets.token_urlsafe(12)
        self.sid, self.person, self.text = sid, person, text
        self.created = time.time()
        self.state = "running"
        self.steps, self.answer, self.error = [], None, None
        self.pending = None
        self._event = threading.Event()
        self._value = None
        self._n = 0
        self.lock = threading.Lock()
        self.finished = None

    # -- the House calls these from the worker thread
    def _wait(self, kind, question, phrase=None):
        question = human(question)
        with self.lock:
            self._n += 1
            self.pending = {"id": self._n, "kind": kind, "question": question,
                            **({"phrase": phrase} if phrase else {}),
                            "expires": time.time() + WAIT_S}
            self.state = "waiting"
            self._event.clear()
            self._value = None
        got = self._event.wait(WAIT_S)
        with self.lock:
            value = self._value if got else None
            asked = self.pending
            self.pending = None
            self.state = "running"
        if asked:
            at = len(self.steps)
            if self.steps and self.steps[-1].get("state") == "running":
                at -= 1                       # the question belongs before the tool's result
            self.steps.insert(at, {"kind": "question", "question": question,
                               "answer": "timed out" if not got else
                               ("yes" if value not in (None, False, "") else "no")
                               if kind != "code" else ("code entered" if value else "no code")})
        return value

    def confirm(self, question):
        return self._wait("confirm", question) is True

    def strong(self, question, phrase):
        got = self._wait("phrase", question, phrase)
        return isinstance(got, str) and got.strip() == phrase

    def secret(self, question):
        got = self._wait("code", question)
        return got if isinstance(got, str) and got else None

    def give(self, qid, body):
        with self.lock:
            if not self.pending or self.pending["id"] != qid:
                return False
            kind = self.pending["kind"]
            if kind == "confirm":
                self._value = bool(body.get("yes"))
            elif kind == "phrase":
                self._value = str(body.get("phrase") or "") if body.get("yes", True) else None
            else:
                self._value = str(body.get("code") or "")[:64] if body.get("yes", True) else None
            self._event.set()
            return True

    def view(self):
        with self.lock:
            p = dict(self.pending) if self.pending else None
        return {"job": self.id, "text": self.text, "state": self.state, "steps": list(self.steps),
                "pending": p, "answer": self.answer, "error": self.error,
                "elapsed_s": round((self.finished or time.time()) - self.created, 1)}


class AskJobs:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def _gc(self):
        now = time.time()
        for k in [k for k, j in self.jobs.items()
                  if j.finished and now - j.finished > KEEP_S or now - j.created > 3 * KEEP_S]:
            self.jobs.pop(k, None)

    def get(self, jid, sid):
        with self.lock:
            j = self.jobs.get(jid)
        return j if j and secrets.compare_digest(j.sid, sid or "") else None

    def start(self, server, sid, person, text, make_house, url, model, chat_fn=None):
        with self.lock:
            self._gc()
            busy = [j for j in self.jobs.values() if j.person == person and not j.finished]
            if len(busy) >= MAX_PER_PERSON:
                raise ValueError("still working on your last request")
            job = Job(sid, person, text)
            self.jobs[job.id] = job
        t = threading.Thread(target=self._run, args=(server, job, make_house, url, model, chat_fn),
                             daemon=True, name=f"ask-{job.id[:6]}")
        t.start()
        return job

    def _run(self, server, job, make_house, url, model, chat_fn):
        db = ChoreDB(server.db_path)
        try:
            h = make_house(job, db)
            orig = h.call

            def call(name, args=None):
                a = {k: ("***" if str(k).lower() in _SECRET_KEYS else v)
                     for k, v in (args or {}).items()} if isinstance(args, dict) else {}
                step = {"kind": "tool", "tool": name, "args": a, "state": "running",
                        "t": round(time.time() - job.created, 2)}
                job.steps.append(step)
                res = orig(name, args)
                step.update(state="done" if res.get("ok") else (
                    "queued" if res.get("queued_for_approval") else
                    "declined" if res.get("declined") else "failed"),
                    result=json.loads(json.dumps(brief(res), default=str)),
                    error=None if res.get("ok") else res.get("error"))
                server.dev_cache.clear()
                return res
            h.call = call
            base_chat = chat_fn
            if base_chat is None:
                from .control import chat as base_chat

            def traced(url_, messages, *a, **k):
                step = {"kind": "model", "state": "running", "t": round(time.time() - job.created, 2)}
                job.steps.append(step)
                t0 = time.time()
                try:
                    out = base_chat(url_, messages, *a, **k)
                except Exception:
                    step["state"] = "failed"
                    raise
                tm = (out or {}).get("timings") or {}
                msg = ((out or {}).get("choices") or [{}])[0].get("message") or {}
                step.update(state="done", s=round(time.time() - t0, 2),
                            tok_s=round(tm["predicted_per_second"], 1)
                            if tm.get("predicted_per_second") else None,
                            ttft_s=round(tm["prompt_ms"] / 1000, 3) if tm.get("prompt_ms") else None,
                            tokens=tm.get("predicted_n"),
                            calls=len(msg.get("tool_calls") or []))
                return out
            kw = {"chat_fn": traced}
            answer, _trace = agent_run(job.text, h, url, model=model, log=lambda m: None, **kw)
            job.answer = answer or "(no answer)"
            job.state = "done"
        except Exception as e:  # noqa: BLE001 - shown to the person, never crashes the server
            job.error = _friendly(e, url)
            job.state = "error"
        finally:
            job.finished = time.time()
            db.close()


def _friendly(e, url):
    s = str(e)
    if "Connection refused" in s or "urlopen error" in s or "timed out" in s:
        return f"The model server at {url} did not answer. Start it, or pick another in Settings."
    return f"That failed on this side: {s[:200]}"
