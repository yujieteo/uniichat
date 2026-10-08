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

    def test_codex_exec_runs_and_subagent_threads_are_skipped(self):
        for name in ("codex-exec-rollout.jsonl", "codex-subagent-rollout.jsonl"):
            f = self.fixture(name)
            self.hook("codex", self.payload("codex-stop.json", f))
        self.assertEqual(self.messages(), [])
        again = self.fixture("codex-exec-rollout.jsonl", "again.jsonl")
        self.hook("codex", self.payload("codex-user-prompt-submit.json", again))
        self.assertEqual(self.messages(), [])

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

    def test_pi_outside_the_interactive_mode_is_skipped(self):
        f = self.fixture("pi-session.jsonl")
        self.hook("pi", self.payload("pi-print-mode.json", f))
        self.assertEqual(self.messages(), [])

    def test_claude_subagent_hooks_are_skipped(self):
        f = self.fixture("claude-transcript.jsonl")
        self.hook("claude", self.payload("claude-subagent-post-tool-use.json", f))
        self.assertEqual(self.messages(), [])

    def test_firstmate_workers_and_pipeline_agents_are_skipped(self):
        f = self.fixture("pi-session.jsonl")
        text = self.payload("pi-turn-end.json", f)
        for var in SKIPPED:
            with mock.patch.dict(os.environ, {var: "1"}):
                self.hook("pi", text)
            self.assertEqual(self.messages(), [])
            self.assertFalse(os.path.exists(self.store.p("capture")))
        self.hook("pi", text)  # the main session has none of them
        self.assertEqual(len(self.messages()), 5)

    def test_message_date_is_the_time_of_the_event_and_has_its_source_key(self):
        f = self.fixture("pi-session.jsonl")
        self.hook("pi", self.payload("pi-turn-end.json", f))
        first = self.messages()[0]
        self.assertEqual(first["date"], memo.local_date("2026-03-01T12:02:00.000Z"))
        self.assertTrue(first["k"].startswith("e") or first["k"].startswith("p"))
        self.assertEqual(set(first), {"i", "kind", "text", "size", "date", "k"})

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

    def test_capture_lands_in_the_view_as_normal_messages(self):
        f = self.fixture("claude-transcript.jsonl")
        self.hook("claude", self.payload("claude-stop.json", f))
        s = self.store.load()
        self.assertEqual(s.n, 8)
        self.assertIn("user: [claude ", self.store.tree.get(0, 0))


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

    def test_a_lost_cursor_does_not_duplicate_messages(self):
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
            self.assertEqual(len(self.messages()), n, name)

    def test_a_copy_of_a_session_file_under_a_new_id_adds_nothing(self):
        """A resumed or forked session carries its old messages along."""
        for name, harness, payload in (
                ("claude-transcript.jsonl", "claude", "claude-stop.json"),
                ("codex-rollout.jsonl", "codex", "codex-stop.json"),
                ("pi-session.jsonl", "pi", "pi-turn-end.json")):
            f = self.fixture(name)
            self.hook(harness, self.payload(payload, f, "old-id"))
            n = len(self.messages())
            copy = self.fixture(name, "fork-" + name)
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

    def test_a_bad_line_is_logged_and_the_rest_is_kept(self):
        f = self.fixture("pi-session.jsonl")
        self.append(f, "{not json", json.dumps(pi_entry(50, "user", "after it")))
        self.hook("pi", self.payload("pi-turn-end.json", f))
        self.assertEqual(self.bodies()[-1], ("user", "after it"))
        self.assertIn("bad line in pi-session.jsonl", self.errors())

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

    def test_a_queued_hook_retries_until_the_lock_is_free(self):
        f = self.fixture("pi-session.jsonl")
        self.hook("pi", self.payload("pi-turn-end.json", f))  # creates capture/
        self.append(f, pi_entry(60, "user", "while the lock is taken"))
        fd = self.hold_lock()
        with mock.patch.object(memo, "LOCK_WAIT", 0.05), \
                mock.patch.object(memo, "spawn_drain"):
            self.hook("pi", self.payload("pi-turn-end.json", f))
        threading.Timer(0.2, lambda: fcntl.flock(fd, fcntl.LOCK_UN)).start()
        memo.drain(self.store, tries=100, pause=0.02)
        self.assertEqual(self.bodies()[-1], ("user", "while the lock is taken"))
        self.assertEqual(len(self.messages()), 6)

    def test_a_job_that_never_gets_the_lock_is_dropped_and_logged(self):
        f = self.fixture("pi-session.jsonl")
        self.hold_lock()
        with mock.patch.object(memo, "LOCK_WAIT", 0.01), \
                mock.patch.object(memo, "DRAIN_WAIT", 0.01), \
                mock.patch.object(memo, "spawn_drain"):
            self.hook("pi", self.payload("pi-turn-end.json", f))
            memo.drain(self.store, tries=2, pause=0.01)
        self.assertEqual(os.listdir(self.store.p("capture/queue")), [])
        self.assertIn("gave up", self.errors())

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

    def test_one_hooks_messages_are_contiguous(self):
        f = self.fixture("pi-session.jsonl")
        other = os.path.join(self.tmp, "other.jsonl")
        self.append(other, pi_entry(1, "user", "other-1"),
                    pi_entry(2, "user", "other-2"))
        self.hook("pi", self.payload("pi-turn-end.json", f, "one"))
        self.hook("pi", json.dumps({"session_id": "two", "session_file": other,
                                    "mode": "tui"}))
        bodies = [b for _, b in self.bodies()]
        self.assertEqual(bodies[-2:], ["other-1", "other-2"])
        self.assertEqual(len(bodies), 7)


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

    def test_a_huge_tool_input_is_clipped_the_same_way(self):
        f = os.path.join(self.tmp, "hugein.jsonl")
        call = {"type": "toolCall", "id": "c", "name": "write",
                "arguments": {"path": "/a", "content": "y" * 500_000}}
        self.append(f, pi_entry(1, "assistant", [call]))
        self.hook("pi", json.dumps({"session_id": "h", "session_file": f,
                                    "mode": "tui"}))
        msgs = self.messages()
        self.assertTrue(all(m["kind"] == "tool" for m in msgs))
        self.assertEqual(len("".join(m["text"] for m in msgs)), memo.ECHO_MAX)

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

    def test_an_empty_tool_output_is_logged_as_such(self):
        f = os.path.join(self.tmp, "empty.jsonl")
        self.append(f, pi_entry(1, "toolResult", []),
                    pi_entry(2, "user", "   "))
        self.hook("pi", json.dumps({"session_id": "h", "session_file": f,
                                    "mode": "tui"}))
        self.assertEqual(self.bodies(), [("echo", "(no output)")])


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

    def test_token_formats(self):
        for name, secret in self.CASES:
            out = memo.redact("before %s after" % secret)
            self.assertEqual(out, "before [REDACTED] after", name)

    def test_private_key_blocks(self):
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

    def test_a_missing_store_is_not_an_error_for_the_harness(self):
        gone = memo.Store(os.path.join(self.tmp, "nowhere"))
        f = self.fixture("pi-session.jsonl")
        err = self.hook("pi", self.payload("pi-turn-end.json", f), store=gone)
        self.assertIn("no chat at", err)
        self.assertFalse(os.path.exists(gone.path))

    def test_a_store_that_cannot_be_written_still_exits_zero(self):
        f = self.fixture("pi-session.jsonl")
        os.chmod(self.store.path, 0o500)
        self.addCleanup(os.chmod, self.store.path, 0o700)
        if os.access(self.store.path, os.W_OK):
            self.skipTest("the user can write anywhere")
        self.hook("pi", self.payload("pi-turn-end.json", f))

    def test_unknown_harness_is_a_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = memo.main(["--store", self.store.path, "capture", "vim"],
                             out=lambda m: None)
        self.assertEqual(code, 1)
        self.assertIn("usage:", err.getvalue())

    def test_capture_output_is_empty_so_hooks_add_no_context(self):
        f = self.fixture("claude-transcript.jsonl")
        shown = []
        with mock.patch.object(sys, "stdin",
                               io.StringIO(self.payload("claude-stop.json", f))):
            memo.main(["--store", self.store.path, "capture", "claude"],
                      out=shown.append)
        self.assertEqual(shown, [])

    def test_status_shows_the_capture_state(self):
        f = self.fixture("pi-session.jsonl")
        self.hook("pi", self.payload("pi-turn-end.json", f))
        self.hook("pi", "not json")
        lines = []
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k"}):
            memo.cmd_status(self.store, [], lines.append)
        self.assertIn("capture    1 session files followed, 0 queued, 1 error lines",
                      lines)

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


class Adapters_in_the_repository(unittest.TestCase):
    """The files the README tells the captain to install."""

    def root(self, *p):
        return os.path.join(HERE, "..", "adapters", *p)

    def test_hook_files_name_memo_capture_and_run_in_the_background(self):
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
                        if event != "SessionEnd":
                            self.assertTrue(h["async"], event)

    def test_pi_extension_calls_memo_capture_and_guards_the_session(self):
        with open(self.root("pi", "uniichat-capture.ts"), encoding="utf-8") as f:
            src = f.read()
        for needle in ('"capture", "pi"', 'ctx.mode !== "tui"', "FM_TASK_ID",
                       "NO_MISTAKES_GATE", "turn_end", "agent_settled",
                       "session_shutdown", "detached: true", "unref()"):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main(testRunner=unittest.TextTestRunner(resultclass=Timed,
                                                     verbosity=1))
