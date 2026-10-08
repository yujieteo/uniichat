"""Tests for `memo capture`: the adapters' common core. Standard library only;
synthetic transcripts and recorded synthetic hook payloads (tests/fixtures).

Run:  python3 tests/test_capture.py      (prints each test's time)
      python3 -m unittest discover -s tests -v
"""

import contextlib
import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import test_memo
from test_memo import Clock, Timed

memo = test_memo.memo
HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
MEMO = os.path.join(HERE, "..", "memo")
SKIPPED = ("FM_TASK_ID", "NO_MISTAKES_GATE")


def fake(prefix, n=24, alphabet="a1"):
    """A string shaped like a secret, built at run time so no fixture holds one."""
    return prefix + (alphabet * n)[:n]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="capture-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = memo.Store(os.path.join(self.tmp, "chat"), Clock())
        self.store.init()
        env = {k: v for k, v in os.environ.items() if k not in SKIPPED}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ["UNIICHAT_NO_WORKER"] = "1"

    # -- files
    def fixture(self, name, as_name=None):
        dst = os.path.join(self.tmp, as_name or name)
        shutil.copy(os.path.join(FIX, name), dst)
        return dst

    def payload(self, name, file, sid="sess-1"):
        with open(os.path.join(FIX, name), encoding="utf-8") as f:
            text = f.read().replace("@FILE@", file).replace("@SID@", sid)
        return text

    # -- run `memo capture HARNESS` in this process
    def hook(self, harness, text, store=None):
        err = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(text)), \
                contextlib.redirect_stderr(err):
            code = memo.main(["--store", (store or self.store).path, "capture",
                              harness], out=lambda m: None)
        self.assertEqual(code, 0, err.getvalue())
        return err.getvalue()

    def messages(self):
        s = memo.Store(self.store.path, Clock())
        return [s.main.get(i) for i in range(s.main.count())]

    def texts(self):
        return [(m["kind"], m["text"]) for m in self.messages()]

    def bodies(self):
        """(kind, text without the source prefix) of every message."""
        return [(k, re.sub(r"^\[\w+ [0-9a-f]{4}\] ", "", t))
                for k, t in self.texts()]

    def errors(self):
        try:
            with open(self.store.p("capture/errors.log"), encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def append(self, file, *records):
        with open(file, "a", encoding="utf-8") as f:
            for r in records:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def pi_entry(i, role, content, sid_ts="2026-03-02T09:%02d:00.000Z", tag=0, **kw):
    msg = {"role": role, "content": content}
    msg.update(kw)
    return {"type": "message", "id": "e%x%06x" % (tag, i), "parentId": None,
            "timestamp": sid_ts % (i % 60), "message": msg}


class Adapters(Case):
    def test_claude_code_kinds_and_what_is_left_out(self):
        f = self.fixture("claude-transcript.jsonl")
        self.hook("claude", self.payload("claude-stop.json", f))
        body = self.bodies()
        self.assertEqual([k for k, _ in body],
                         ["user", "unii", "tool", "echo", "tool", "echo", "unii",
                          "user"])
        self.assertEqual(body[0][1], "Please add a retry to the upload function.")
        self.assertEqual(body[1][1], "I will read the file first.")
        self.assertEqual(body[2][1], 'Read {"file_path":"/work/app/upload.py"}')
        self.assertEqual(body[3][1], "def upload(path):\n    return put(path)\n")
        self.assertEqual(body[4][1],
                         'Bash {"command":"pytest -q","description":"Run tests"}')
        self.assertEqual(body[5][1], "3 passed in 0.10s")
        self.assertEqual(body[6][1], "Done: upload() now retries 3 times.\nTests pass.")
        self.assertEqual(body[7][1], "/review the upload change")
        everything = "\n".join(t for _, t in self.texts())
        for left_out in ("SECRET THOUGHT", "subagent chatter", "print mode",
                         "Skill text", "task-notification", "local-command",
                         "No response requested"):
            self.assertNotIn(left_out, everything)

    def test_codex_kinds_and_what_is_left_out(self):
        f = self.fixture("codex-rollout.jsonl")
        self.hook("codex", self.payload("codex-stop.json", f))
        body = self.bodies()
        self.assertEqual([k for k, _ in body],
                         ["user", "tool", "echo", "tool", "echo", "unii"])
        self.assertEqual(body[0][1], "Please rename the helper to upload_file.")
        self.assertEqual(body[1][1],
                         'shell {"command":["grep","-rn","helper","."]}')
        self.assertEqual(body[2][1], "./upload.py:3:def helper():")
        self.assertEqual(body[3][1], 'apply_patch {"input":"*** Begin Patch\\n'
                                     '*** End Patch"}')
        self.assertEqual(body[4][1], "Success. Updated 1 file.")
        self.assertEqual(body[5][1], "Renamed helper to upload_file in 1 file.")
        everything = "\n".join(t for _, t in self.texts())
        for left_out in ("AGENTS.md", "developer rules", "SECRET THOUGHT"):
            self.assertNotIn(left_out, everything)

    def test_pi_kinds_and_what_is_left_out(self):
        f = self.fixture("pi-session.jsonl")
        self.hook("pi", self.payload("pi-turn-end.json", f))
        body = self.bodies()
        self.assertEqual([k for k, _ in body],
                         ["user", "unii", "tool", "echo", "unii"])
        self.assertEqual(body[0][1], "Please list the failing tests.")
        self.assertEqual(body[2][1], 'bash {"command":"pytest -q"}')
        self.assertEqual(body[3][1], "1 failed, 2 passed")
        everything = "\n".join(t for _, t in self.texts())
        for left_out in ("SECRET THOUGHT", "user shell noise", "system prompt"):
            self.assertNotIn(left_out, everything)

    def test_workers_subagents_and_print_runs_are_skipped(self):
        pi = self.fixture("pi-session.jsonl")
        claude = self.fixture("claude-transcript.jsonl")
        for name in ("codex-exec-rollout.jsonl", "codex-subagent-rollout.jsonl"):
            self.hook("codex", self.payload("codex-stop.json", self.fixture(name)))
        self.hook("pi", self.payload("pi-print-mode.json", pi))
        self.hook("claude", self.payload("claude-subagent-post-tool-use.json", claude))
        for var in SKIPPED:
            with mock.patch.dict(os.environ, {var: "1"}):
                self.hook("pi", self.payload("pi-turn-end.json", pi))
        self.assertEqual(self.messages(), [])
        self.hook("pi", self.payload("pi-turn-end.json", pi))  # the main session
        self.assertEqual(len(self.messages()), 5)

    def test_source_prefix_is_short_and_tells_sessions_apart(self):
        a = self.fixture("pi-session.jsonl", "a.jsonl")
        b = self.fixture("claude-transcript.jsonl", "b.jsonl")
        self.hook("pi", self.payload("pi-turn-end.json", a, "pi-session-1"))
        self.hook("claude", self.payload("claude-stop.json", b, "claude-session-1"))
        heads = {re.match(r"\[(\w+ [0-9a-f]{4})\] ", t).group(1)
                 for _, t in self.texts()}
        self.assertEqual(len(heads), 2)
        self.assertTrue(all(h.startswith(("pi ", "claude ")) for h in heads))
        # the same session always gets the same prefix
        self.hook("pi", self.payload("pi-turn-end.json", a, "pi-session-1"))
        self.assertEqual(len(self.messages()), 13)


class Idempotence(Case):
    def test_a_repeated_hook_adds_nothing(self):
        f = self.fixture("claude-transcript.jsonl")
        text = self.payload("claude-post-tool-use.json", f)
        self.hook("claude", text)
        before = self.texts()
        for name in ("claude-post-tool-use.json", "claude-stop.json",
                     "claude-session-end.json", "claude-user-prompt-submit.json"):
            self.hook("claude", self.payload(name, f))
        self.assertEqual(self.texts(), before)

    def test_only_what_is_new_is_added(self):
        f = self.fixture("pi-session.jsonl")
        text = self.payload("pi-turn-end.json", f)
        self.hook("pi", text)
        self.append(f, pi_entry(30, "user", "one more question"),
                    pi_entry(31, "assistant", [{"type": "text", "text": "answer"}]))
        self.hook("pi", text)
        self.hook("pi", text)
        self.assertEqual([b for _, b in self.bodies()][-2:],
                         ["one more question", "answer"])
        self.assertEqual(len(self.messages()), 7)

    def test_a_lost_cursor_or_a_copied_session_file_adds_nothing(self):
        for name, harness, payload in (
                ("claude-transcript.jsonl", "claude", "claude-stop.json"),
                ("codex-rollout.jsonl", "codex", "codex-stop.json"),
                ("pi-session.jsonl", "pi", "pi-turn-end.json")):
            f = self.fixture(name)
            text = self.payload(payload, f)
            self.hook(harness, text)
            n = len(self.messages())
            shutil.rmtree(self.store.p("capture/cursors"))
            self.hook(harness, text)
            copy = self.fixture(name, "fork-" + name)  # a resumed or forked session
            self.hook(harness, self.payload(payload, copy, "new-id"))
            self.assertEqual(len(self.messages()), n, name)

    def test_a_half_written_last_line_waits_for_its_newline(self):
        f = self.fixture("pi-session.jsonl")
        text = self.payload("pi-turn-end.json", f)
        line = json.dumps(pi_entry(40, "user", "half written"))
        with open(f, "a", encoding="utf-8") as h:
            h.write(line[:30])
        self.hook("pi", text)
        self.assertEqual(len(self.messages()), 5)
        with open(f, "a", encoding="utf-8") as h:
            h.write(line[30:] + "\n")
        self.hook("pi", text)
        self.assertEqual(self.bodies()[-1], ("user", "half written"))
        self.assertEqual(len(self.messages()), 6)

    def test_a_crash_between_the_log_and_the_cursor_adds_no_duplicate(self):
        f = self.fixture("pi-session.jsonl")
        text = self.payload("pi-turn-end.json", f)
        real_save, calls = memo.Cursors.save, []

        def flaky(self_, harness, file, cur):
            calls.append(cur)
            if len(calls) == 2:  # the save after the append
                raise OSError("disk went away")
            return real_save(self_, harness, file, cur)

        with mock.patch.object(memo.Cursors, "save", flaky):
            self.hook("pi", text)
        self.assertEqual(len(self.messages()), 5)
        self.assertIn("disk went away", self.errors())
        self.hook("pi", text)
        self.assertEqual(len(self.messages()), 5)
        self.hook("pi", text)
        self.assertEqual(len(self.messages()), 5)

    def test_stop_waits_for_the_transcript_to_hold_the_last_reply(self):
        src = os.path.join(FIX, "claude-transcript.jsonl")
        with open(src, encoding="utf-8") as h:
            lines = h.read().splitlines(keepends=True)
        late = next(i for i, l in enumerate(lines) if "Done: upload()" in l)
        f = os.path.join(self.tmp, "late.jsonl")
        with open(f, "w", encoding="utf-8") as h:
            h.writelines(lines[:late])

        def finish():
            time.sleep(0.3)
            with open(f, "a", encoding="utf-8") as h:
                h.writelines(lines[late:])

        t = threading.Thread(target=finish)
        t.start()
        began = time.time()
        self.hook("claude", self.payload("claude-stop.json", f))
        t.join()
        self.assertLess(time.time() - began, 2.5)
        self.assertIn(("unii", "Done: upload() now retries 3 times.\nTests pass."),
                      self.bodies())


class Contention(Case):
    def hold_lock(self):
        fd = open(self.store.p(".lock"), "a")
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.addCleanup(fd.close)
        return fd

    def test_a_busy_lock_queues_the_hook_and_it_returns_at_once(self):
        f = self.fixture("pi-session.jsonl")
        text = self.payload("pi-turn-end.json", f)
        fd = self.hold_lock()
        with mock.patch.object(memo, "LOCK_WAIT", 0.05), \
                mock.patch.object(memo, "spawn_drain") as spawn:
            began = time.time()
            self.hook("pi", text)
            self.assertLess(time.time() - began, 1.0)
        spawn.assert_called_once()
        queued = os.listdir(self.store.p("capture/queue"))
        self.assertEqual(len(queued), 1)
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual(self.messages(), [])
        memo.drain(self.store, tries=3, pause=0.01)
        self.assertEqual(len(self.messages()), 5)
        self.assertEqual(os.listdir(self.store.p("capture/queue")), [])

    def test_real_processes_queue_retry_and_finish(self):
        """A real hook finds the lock taken, queues, and a real detached
        drain adds the messages once the lock is free."""
        f = self.fixture("pi-session.jsonl")
        text = self.payload("pi-turn-end.json", f)
        fd = self.hold_lock()
        env = dict(os.environ, UNIICHAT_CAPTURE_WAIT="0.1")
        began = time.time()
        subprocess.run([sys.executable, MEMO, "--store", self.store.path,
                        "capture", "pi"], input=text, text=True, env=env,
                       check=True, timeout=30)
        self.assertLess(time.time() - began, 5)
        self.assertEqual(self.messages(), [])
        fcntl.flock(fd, fcntl.LOCK_UN)
        end = time.time() + 20
        while time.time() < end and len(self.messages()) < 5:
            time.sleep(0.1)
        self.assertEqual(len(self.messages()), 5)

    def test_parallel_sessions_stay_whole_and_in_order(self):
        """Six hooks at once, two per session: each message once, each
        session in its own order, each record whole."""
        sessions = {}
        for n in range(3):
            f = os.path.join(self.tmp, "s%d.jsonl" % n)
            self.append(f, *[pi_entry(k, "user" if k % 2 else "assistant",
                                      "s%d-m%02d" % (n, k) if k % 2
                                      else [{"type": "text",
                                             "text": "s%d-m%02d" % (n, k)}],
                                      tag=n + 1)
                             for k in range(1, 61)])
            sessions[n] = f
        procs = []
        for rep in range(2):
            for n, f in sessions.items():
                text = json.dumps({"session_id": "par-%d" % n,
                                   "session_file": f, "mode": "tui"})
                p = subprocess.Popen([sys.executable, MEMO, "--store",
                                      self.store.path, "capture", "pi"],
                                     stdin=subprocess.PIPE, text=True)
                p.stdin.write(text)
                p.stdin.close()
                procs.append(p)
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        by_session = {}
        for m in self.messages():
            head = re.match(r"\[pi [0-9a-f]{4}\] (s(\d)-m(\d\d))$", m["text"])
            self.assertIsNotNone(head, m["text"])
            by_session.setdefault(head.group(2), []).append(int(head.group(3)))
        self.assertEqual(len(self.messages()), 180)
        for n in "012":
            self.assertEqual(by_session[n], list(range(1, 61)))
        for i, m in enumerate(self.messages()):
            self.assertEqual(m["i"], i)  # ids run on, no gap, no repeat
        with open(self.store.p("capture/errors.log"), "a"):
            pass
        self.assertEqual(self.errors(), "")


class Size(Case):
    def test_a_huge_tool_output_keeps_head_and_tail_30000_characters(self):
        f = os.path.join(self.tmp, "huge.jsonl")
        out = "HEAD" + "x" * 2_000_000 + "TAIL"
        self.append(f, pi_entry(1, "toolResult", [{"type": "text", "text": out}]))
        began = time.time()
        self.hook("pi", json.dumps({"session_id": "h", "session_file": f,
                                    "mode": "tui"}))
        self.assertLess(time.time() - began, 5)
        msgs = self.messages()
        self.assertTrue(all(m["kind"] == "echo" for m in msgs))
        self.assertTrue(all(m["size"] <= memo.MESSAGE_MAX for m in msgs))
        joined = "".join(m["text"] for m in msgs)
        self.assertEqual(len(joined), memo.ECHO_MAX)
        self.assertRegex(joined, r"^\[pi [0-9a-f]{4}\] HEAD")
        self.assertTrue(joined.endswith("xxxTAIL"))
        self.assertIn("characters clipped", joined)

    def test_a_long_user_message_is_never_clipped_only_split(self):
        f = os.path.join(self.tmp, "long.jsonl")
        text = "".join("line %d é\n" % k for k in range(12000))
        self.append(f, pi_entry(1, "user", text))
        self.hook("pi", json.dumps({"session_id": "h", "session_file": f,
                                    "mode": "tui"}))
        msgs = self.messages()
        self.assertGreater(len(msgs), 3)
        self.assertTrue(all(m["size"] <= memo.MESSAGE_MAX for m in msgs))
        joined = "".join(m["text"] for m in msgs)
        self.assertEqual(re.sub(r"^\[pi [0-9a-f]{4}\] ", "", joined), text)


class Redaction(Case):
    CASES = [
        ("anthropic key", fake("sk-ant-api03-", 40)),
        ("openai key", fake("sk-proj-", 40, "Ab9_")),
        ("deepseek key", fake("sk-", 32, "0f")),
        ("github token", fake("ghp_", 36, "aB3")),
        ("github fine token", fake("github_pat_", 40, "aB3_")),
        ("gitlab token", fake("glpat-", 24, "aB3_")),
        ("aws key", fake("AKIA", 16, "A1")),
        ("slack token", fake("xoxb-", 24, "12ab-")),
        ("google key", fake("AIza", 35, "aB3_")),
        ("npm token", fake("npm_", 36, "aB3")),
        ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"),
    ]

    def test_token_formats_and_private_key_blocks(self):
        for name, secret in self.CASES:
            out = memo.redact("before %s after" % secret)
            self.assertEqual(out, "before [REDACTED] after", name)
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\ndef==\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(memo.redact("k:\n%s\nok" % pem), "k:\n[REDACTED]\nok")
        cut = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3Nza\nmore"
        self.assertEqual(memo.redact("k:\n" + cut), "k:\n[REDACTED]")

    def test_named_values_and_bearer_headers_and_url_passwords(self):
        cases = {
            "export ANTHROPIC_API_KEY=abcd1234efgh": "export ANTHROPIC_API_KEY=[REDACTED]",
            'password: "hunter2hunter2"': 'password: "[REDACTED]"',
            '{"api_key": "abcdefgh12345678"}': '{"api_key": "[REDACTED]"}',
            "client_secret = s3cr3tvalue99": "client_secret = [REDACTED]",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123": "Authorization: Bearer [REDACTED]",
            "git clone https://bob:pa55w0rd@host.example/r.git": "git clone https://[REDACTED]@host.example/r.git",
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY": "AWS_SECRET_ACCESS_KEY=[REDACTED]",
        }
        for text, want in cases.items():
            self.assertEqual(memo.redact(text), want)

    def test_ordinary_text_is_left_alone(self):
        for text in ("max_tokens: 1024", "the token count is 5", "tokens=12",
                     "sk-short", "password: $PASSWORD_FROM_ENV_FILE",
                     "api_key = <your key here>", "see https://host.example/a@b",
                     "ssh-rsa AAAA user@host", "BEGIN PRIVATE KEY is a header",
                     "Bearer short", "def secret(x): return x * 2",
                     "[REDACTED]"):
            self.assertEqual(memo.redact(text), text)

    def test_no_secret_reaches_the_store(self):
        secrets = [s for _, s in self.CASES]
        f = os.path.join(self.tmp, "sec.jsonl")
        self.append(
            f, pi_entry(1, "user", "my key is %s, use it" % secrets[0]),
            pi_entry(2, "assistant", [
                {"type": "text", "text": "got %s" % secrets[1]},
                {"type": "toolCall", "id": "c", "name": "bash",
                 "arguments": {"command": "curl -H 'Authorization: Bearer %s' x"
                               % secrets[2] + " && echo TOKEN=%s" % secrets[3]}}]),
            pi_entry(3, "toolResult", [{"type": "text", "text": "\n".join(secrets)}]))
        self.hook("pi", json.dumps({"session_id": "s", "session_file": f,
                                    "mode": "tui"}))
        data = ""
        for root, _, names in os.walk(self.store.path):
            for n in names:
                with open(os.path.join(root, n), encoding="utf-8",
                          errors="replace") as h:
                    data += h.read()
        for secret in secrets:
            self.assertNotIn(secret, data)
        self.assertIn("[REDACTED]", data)
        self.assertEqual(self.bodies()[0], ("user", "my key is [REDACTED], use it"))


class Safety(Case):
    def test_it_never_raises_into_the_harness(self):
        f = self.fixture("pi-session.jsonl")
        text = self.payload("pi-turn-end.json", f)
        with mock.patch.object(memo, "capture", side_effect=RuntimeError("boom")):
            self.hook("pi", text)
        self.assertIn("RuntimeError: boom", self.errors())
        self.hook("pi", "this is not json")
        self.assertIn("JSONDecodeError", self.errors())
        self.hook("pi", "[1, 2]")
        self.hook("pi", "")
        self.hook("pi", json.dumps({"session_file": "/no/such/file"}))
        self.hook("pi", json.dumps({}))
        self.assertEqual(self.messages(), [])
        gone = memo.Store(os.path.join(self.tmp, "nowhere"))
        self.assertIn("no chat at", self.hook("pi", text, store=gone))
        self.assertFalse(os.path.exists(gone.path))
        f = self.fixture("pi-session.jsonl", "bad.jsonl")
        self.append(f, "{not json", json.dumps(pi_entry(50, "user", "after it")))
        self.hook("pi", self.payload("pi-turn-end.json", f))
        self.assertEqual(self.bodies()[-1], ("user", "after it"))
        self.assertIn("bad line in bad.jsonl", self.errors())

    def test_a_real_hook_process_is_fast(self):
        f = self.fixture("claude-transcript.jsonl")
        text = self.payload("claude-post-tool-use.json", f)
        run = lambda: subprocess.run([sys.executable, MEMO, "--store",
                                      self.store.path, "capture", "claude"],
                                     input=text, text=True, check=True,
                                     capture_output=True, timeout=30)
        run()
        began = time.time()
        r = run()
        self.assertLess(time.time() - began, 1.5)
        self.assertEqual((r.stdout, r.stderr), ("", ""))
        self.assertEqual(len(self.messages()), 8)


class AgentAndHooks(Case):
    """A session with both the AGENTS.md block (the agent logs) and a hook."""

    def test_a_prompt_hook_asks_for_the_compaction_in_the_context(self):
        with self.store.writer():
            self.store.add_messages([("note", "long " + "x" * 600, None)])
        f = self.fixture("claude-transcript.jsonl")

        def run(harness, name):
            shown = []
            with mock.patch.object(sys, "stdin", io.StringIO(self.payload(name, f))):
                memo.main(["--store", self.store.path, "capture", harness],
                          out=shown.append)
            return "\n".join(shown)

        out = run("claude", "claude-user-prompt-submit.json")
        self.assertIn(memo.ASK, out)
        self.assertIn("memo line 0+1", out)
        self.assertEqual(run("claude", "claude-post-tool-use.json"), "")
        self.assertEqual(run("claude", "claude-stop.json"), "")
        self.assertIn(memo.ASK, run("codex", "codex-user-prompt-submit.json"))

    def test_the_agent_does_not_log_what_a_hook_of_its_session_logs(self):
        def say(kind, text, env):
            out = []
            with mock.patch.dict(os.environ, env):
                memo.main(["--store", self.store.path, kind, text],
                          out=out.append)
            return "\n".join(out)

        f = self.fixture("pi-session.jsonl")
        mine = {"PI_SESSION_ID": "hooked"}
        self.assertIn("Saved", say("user", "before the hook", mine))  # no hook yet
        self.hook("pi", self.payload("pi-turn-end.json", f, "hooked"))
        n = len(self.messages())
        self.assertIn("Not saved", say("unii", "my reply", mine))
        self.assertIn("Not saved", say("user", "my prompt", mine))
        self.assertEqual(len(self.messages()), n)
        # another session, a session of another harness, and a plain note
        say("unii", "other " + fake("sk-", 30), {"PI_SESSION_ID": "other"})
        say("user", "claude one", {"CLAUDE_CODE_SESSION_ID": "c1"})
        say("note", "kept", mine)
        self.assertEqual([k for k, _ in self.texts()[n:]], ["unii", "user", "note"])
        self.assertRegex(self.texts()[n][1], r"^\[pi [0-9a-f]{4}\] other \[REDACTED\]$")
        self.assertRegex(self.texts()[n + 1][1], r"^\[claude [0-9a-f]{4}\] claude one$")
        # a worker logs nothing, not even by itself
        self.assertIn("Not saved", say("user", "w", {"FM_TASK_ID": "t"}))
        self.assertEqual(len(self.messages()), n + 3)


class Adapters_in_the_repository(unittest.TestCase):
    """The files the README tells the captain to install."""

    def root(self, *p):
        return os.path.join(HERE, "..", "adapters", *p)

    def test_hook_files_name_memo_capture_and_never_wait_but_the_prompt_hook(self):
        for name, harness, events in (
                ("claude-code-hooks.json", "claude",
                 ("UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")),
                ("codex-hooks.json", "codex",
                 ("UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"))):
            with open(self.root(name), encoding="utf-8") as f:
                hooks = json.load(f)["hooks"]
            self.assertEqual(set(hooks), set(events))
            for event, groups in hooks.items():
                for group in groups:
                    for h in group["hooks"]:
                        self.assertEqual(h["type"], "command")
                        self.assertEqual(h["command"], "memo capture " + harness)
                        # a prompt hook waits: its output is context for the prompt
                        if event in ("PostToolUse", "Stop"):
                            self.assertTrue(h["async"], event)
                        else:
                            self.assertFalse(h.get("async"), event)


if __name__ == "__main__":
    unittest.main(testRunner=unittest.TextTestRunner(resultclass=Timed,
                                                     verbosity=1))
