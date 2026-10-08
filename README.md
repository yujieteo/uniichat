# UniiChat memory

One chat that never ends: a long-term memory for AI agents.

`memo` is one Python 3 file. It uses only the standard library. It follows
the UniiChat design ("UniiChat: one chat that never ends"):

- The log is append-only. Every message is stored word for word.
- A cheap model (DeepSeek v4 Flash) writes a binary tree of one-line
  summaries. Each line has at most 512 bytes. The tool builds each line once.
- The view is a saved list of tree lines, 64-128 KB, oldest first. Recent
  lines are fine and old lines are coarse. Agents read the view with `wake`.
- An agent opens a vague line with `zoom`, down to the message itself.

Sizes are UTF-8 bytes. The tool has no memory data in it. The store is
outside the repository.

## Install

Copy `memo` to a directory in your `PATH`, or call it by its path.
The agent session needs Python 3.9 or later.

## Store

The store is a directory. The tool uses the first of these:

1. `memo --store PATH ...` (the option goes before the command)
2. the environment variable `UNIICHAT_DIR`
3. `~/.uniichat/chat`

Run `memo init` once to create it. Other commands refuse a missing store.

## Commands

```
memo init                       create the store; safe to repeat
memo wake [FROM EPOCH]          print the view
memo note [--kind K] [TEXT|-]   append a message (kind K, default note)
memo zoom ID N                  open line ID+N (N=1: message ID whole)
memo date ID                    print the date and time of message ID
memo compact                    build the pending summaries now
memo status                     print sizes, queues and model
memo import [LOG.txt]           append old OptMem memories as notes
memo capture HARNESS            log the new messages of a claude|codex|pi
                                session (a hook payload on stdin)
```

`wake` prints a short guide, then `<chat>`, the view lines, and `</chat>`.
Each line is `id+n|text`: the n messages from `id` on. An unbuilt line shows
`(not summarized yet: zoom it)`. A call prints at most 20,000 bytes, because
agent harnesses cut long output. If the view is longer, the last line is
`Not awake yet. Run: memo wake FROM EPOCH`. Run that command, with the same
numbers, until the output ends with `</chat>`. If the view merged between two
calls, the tool refuses the old EPOCH. Then run `memo wake` again.

`note` appends one message. `TEXT` is the text. `-` reads the text from
standard input. `K` is one of `user unii tool echo work note`. The tool prints
`Saved as #ID.` A text of more than 16,000 bytes becomes several messages in a
row. A tool output (`echo`) keeps its head and tail, 30,000 characters in all.
A message of 512 bytes or less (`kind: text`) is its own tree line. Other
messages get a summary in the background.

`zoom ID N` needs N as a power of 2 and ID as a multiple of N. With N=1 it
prints `kind: text` of message ID. With N=2 or more it prints the two lines
that make line ID+N, as `id+n|text`.

`capture` is for hooks, not for people. See "Capture" below.

`date ID` prints the stored date. Messages from `import` have a day only.

`compact` builds every ready summary and waits. Each `wake`, `note`, `zoom`,
`date` and `import` also starts one background `memo compact`, if summaries
are pending and none runs. The output goes to `worker.log` in the store. If a
model call fails, the node stays queued. The next call tries it again.

`import` reads an old OptMem `LOG.txt` (default: `LOG.txt` in the store) and
appends every memory, in order and word for word, as a `note` with its old
date. It needs an empty chat. After a stop it can run again: it checks that
the messages already there equal the first memories, then appends the rest.
It never changes the source file. Old `TREE` files are not read: the new tree
is built again.

## Compaction model

The default is DeepSeek v4 Flash, through the direct DeepSeek API. It costs
about $0.30 per million input tokens ($0.006 if cached) and $1.20 per million
output tokens. Claude Haiku 4.5 costs $1, $0.10 and $5.

| Setting | Default | Other values |
| --- | --- | --- |
| `UNIICHAT_MODEL` | `deepseek-flash` | `claude-haiku-4-5-20251001` (a name that starts with `claude` selects Anthropic) |
| `UNIICHAT_PROVIDER` | from the model name, else `deepseek` | `deepseek`, `anthropic` |
| `UNIICHAT_THINKING` | `cut` (DeepSeek), `xhigh` (Anthropic) | `off minimal low medium high xhigh max`; DeepSeek also `cut` |
| `UNIICHAT_BACKEND` | `auto`: the API if a key is found, else `pi` | `api`, `pi` |
| `UNIICHAT_PI` | `pi` | the `pi` command |

The key comes from `DEEPSEEK_API_KEY` (or `ANTHROPIC_API_KEY` for Anthropic).
If the variable is not set, the tool runs
`pi auth print-api-key --provider deepseek` once per process and keeps the key
in memory. The tool never stores, logs or prints the key. If there is no key,
or `UNIICHAT_BACKEND=pi`, the tool calls `pi --no-extensions -p` with the same
provider and model. `DEEPSEEK_BASE_URL` and `ANTHROPIC_BASE_URL` change the
API address. The route is always the provider's own API, never a router.

To use Haiku as before: `UNIICHAT_MODEL=claude-haiku-4-5-20251001`.

The DeepSeek request is the system prompt, then the view and the task as one
user message. The prefix is the same in each call, so DeepSeek caches it by
itself. This path has no cache marks. The Anthropic path marks the cache as
the design says: the view goes in blocks of 4 lines, with one mark on the last
whole block and one on the end of the request.

Thinking on DeepSeek. With thinking on, DeepSeek can use all of `max_tokens`
to think and return no text. The setting `cut` avoids this: the first line
is written with thinking off (`max_tokens` 1024), and thinking is on only when
the model must cut a line that is too long. If a call with thinking returns no
text, the tool asks again with thinking off. `off` never thinks. A level
(`high`, `xhigh`, `max`) thinks on every call, with room of 8,192 to 32,000
tokens for it. The 512-byte ruler, the cut and the 5 tries are the same for
all models.

The system prompt is the design prompt, with Unii renamed to OptMem. The
paragraph about computers and the `zoom("Name")` line are removed.

### Measured

20 synthetic compactions (16 messages, 4 merges of two 480-byte lines), 8
calls at once, 2026-10-08. "Cut asks" is the number of times the model had to
cut a line that was over 512 bytes.

| Model, thinking | Failed | Over 512 after 5 tries | Cut asks | Mean line | Output tokens | Cost of 20 |
| --- | --- | --- | --- | --- | --- | --- |
| DeepSeek, `cut` (default) | 0 | 0 | 23 | 453 bytes | 40,962 | $0.054 |
| DeepSeek, `off` | 0 | 4 | 33 | 465 bytes | 7,850 | $0.022 |
| DeepSeek, `high` | 0 | 1 | 20 | 457 bytes | 73,051 | $0.100 |
| Haiku 4.5, `xhigh` | 0 | 0 | 3 | 398 bytes | 22,685 | $0.175 |

No call returned empty text. Costs use the prices above and the token counts
of the API. Haiku's cache writes cost extra, so $0.175 is a floor.

## Capture

Nothing logs a session by itself, so each harness runs `memo capture` from a
hook or an extension. The hook only says that the session file changed.
`memo` reads what is new in that file and appends it as messages. All the
rules are in `memo`, so the three adapters are small.

| Kind | What it holds |
| --- | --- |
| `user` | the words the user typed (a slash command is `/name args`) |
| `unii` | the text of each assistant reply |
| `tool` | a tool call: the name and its JSON input |
| `echo` | a tool result |

The tool never logs thoughts (reasoning), system prompts, injected context
(AGENTS.md, skills, reminders, hook output), background notifications or
compaction summaries.

A message starts with a short source mark: `[pi 3f2a] `, `[claude 3f2a] ` or
`[codex 3f2a] `. The four hex digits come from the session id, so two sessions
differ. The mark is part of the text, so the summaries keep it.
`tool` and `echo` messages are clipped to the head and tail, 30,000
characters in all (the mark counts). A long `user` or `unii` text is only split in
messages of at most 16,000 bytes, never clipped. An empty tool result is
`(no output)`. The `date` of a message is the time of the event in the
session file.

**Whose sessions.** Only interactive sessions of the captain are logged.
`memo capture` does nothing when `FM_TASK_ID` or `NO_MISTAKES_GATE` is set in
its environment (Firstmate workers and scouts, no-mistakes pipeline agents).
It also skips each subagent and each print, exec or SDK run:

- Claude Code: a hook payload with `agent_id`; a line with `isSidechain`; a
  line with an `entrypoint` that starts with `sdk` (`claude -p`).
- Codex: a rollout whose `session_meta` has `source` `exec`, or a
  `thread_source` of `subagent`, or a source that is not a string.
- Pi: the extension runs only if `ctx.mode` is `tui`. Print, json and rpc
  modes (which subagents use) are skipped.

The Firstmate main session is an interactive session, so it is logged.

**Safe to repeat.** Per session file, `capture/cursors/` holds a byte offset.
A hook reads only the new complete lines. A half-written last line waits for
its newline. Each message also has a key (the line id of the harness, or a
hash of the line) in the field `k` of its record. The keys of all captured
messages are in `capture/seen/`. A repeated hook, a restart, a lost cursor, or
a resumed or forked session that copies old messages adds nothing. If a crash
comes between the append and the cursor, the next hook finds the messages by
their keys.

**Never in the way.** A hook takes about 0.06 s. It prints nothing, and it
exits with 0 on any error; the error goes to `capture/errors.log` in the
store (256 KB at most, then `errors.log.1`). A hook waits at most 2 s for the
writer lock (`UNIICHAT_CAPTURE_WAIT` changes it). Then it puts the job in
`capture/queue/` and starts one background `memo capture --drain`, which
retries 40 times, 0.5 s apart. The cursor does not move before the append, so
a job that never succeeds is lost only until the next hook of that session.
Messages of one hook are appended in one write, under the writer lock, in the
order the hooks arrive. Messages of parallel sessions never mix inside one
hook's batch.

A session file seen for the first time is read from its start. If you add the
hooks to an old session that you then resume, its old messages are logged
once.

**Redaction.** Before it writes, `memo` replaces each of these with
`[REDACTED]` (in `user`, `unii`, `tool` and `echo` text, after the clip of `tool` and `echo`):

1. A private key block: from `-----BEGIN ... PRIVATE KEY-----` to the
   matching `END` line, or to the end of the text if the block is cut.
2. The `user:password` part of a URL, in `scheme://user:password@host`.
3. These token formats: `sk-` followed by 20 or more letters, digits, `_` or
   `-` (OpenAI, Anthropic, DeepSeek and others); `ghp_`, `gho_`, `ghu_`,
   `ghs_`, `ghr_` with 30 or more letters or digits; `github_pat_` with 20 or
   more; `glpat-` with 20 or more; `AKIA` or `ASIA` with 16 capitals or
   digits; `xoxa-`, `xoxb-`, `xoxp-`, `xoxr-`, `xoxs-` with 10 or more;
   `AIza` with 35; `npm_` with 30 or more; a JSON web token (`eyJ...` `.`
   `eyJ...` `.` signature).
4. The token after `Bearer ` if it has 20 or more token characters.
5. The value of a name that contains `api_key`, `apikey`, `api-key`, `secret`,
   `token`, `password`, `passwd`, `private_key`, `access_key` or `credential`
   (any case), written as `name=value`, `name: value` or `"name": "value"`,
   if the value has 8 or more characters with no space, quote, comma or
   semicolon and does not start with `$`, `<` or `{`.

Nothing else is redacted. Personal data, file contents and passwords in plain
sentences stay. The redaction is a net, not a promise: do not type a secret
in a session on purpose.

### Install

`memo` must be on the `PATH` of the harness (see Install). The store must
exist (`memo init`). Set `UNIICHAT_DIR` if the store is not the default. Each
hook below runs `memo capture HARNESS`.

**Claude Code.** Merge `adapters/claude-code-hooks.json` into the `hooks` of
`~/.claude/settings.json`. It adds `UserPromptSubmit`, `PostToolUse` and
`Stop` (each with `"async": true`, so Claude never waits) and `SessionEnd`.
Tested with Claude Code 2.1.292. The `Stop` hook waits up to 3 s for the
transcript to hold the last reply, because Claude writes the transcript late.

**Codex.** Merge `adapters/codex-hooks.json` into `~/.codex/hooks.json`. It adds
`UserPromptSubmit`, `PostToolUse` and `Stop` (async) and `SessionEnd`. The hook
feature must be on (`[features] hooks = true` in `~/.codex/config.toml`).
Codex runs a new or changed hook only after you trust it: open `/hooks` in
the CLI once and trust the four hooks. Tested against rollout files of Codex
0.160.1. A Codex rollout file is not a stable interface, so run
`python3 tests/test_capture.py` after a Codex update.

**Pi.** Copy or link `adapters/pi/uniichat-capture.ts` to
`~/.pi/agent/extensions/uniichat-capture.ts`. It runs `memo capture pi` (in the
background, never awaited) after each turn, at the end of a run and at
shutdown. Set `UNIICHAT_MEMO` if the command is not `memo`. Tested with
Pi 1.0.4.

To turn capture off for one session, set `FM_TASK_ID` (any value) in its
environment. To turn it off for all, remove the hooks.

## Files in the store

```
main/YYYY-MM-DD.jsonl   messages: {"i","kind","text","size","date"}
                        (a captured message also has "k", its source key)
tree/YYYY-MM-DD.jsonl   tree nodes: {"l","i","text","size"}
view.json               {"n","epoch","view":[[l,i],...],"merging",
                         "compact":[[l,i],...],"compact_merging"}
queue.json              {"msgs":[i,...],"merges":[[l,i],...]}
.lock  .worker.lock     the writer lock, and the compactor lock
capture/cursors/        one small file per session file: byte offset
capture/seen/           256 files of hashed message keys
capture/queue/          hooks that found the writer lock taken
capture/errors.log      what capture could not do (it never stops a hook)
```

- Message `i` is permanent. The file is the day of its `date`. A clock that
  steps back stays in the last file. `date` is local time with an offset
  (`2026-10-08T14:26:05+08:00`), or a day only for imported notes.
- Node `(l, i)` has the 2^l messages from `i*2^l` on. Its name is `id+n` with
  `id = i*2^l` and `n = 2^l`. A node is in the tree file of the day of its
  first message.
- `view.json` and `queue.json` are state. The tool saves and loads them. It
  never rebuilds the view from the log.
- Do not edit or delete any file in the store.

## Limits

The log and tree are append-only. Only one process writes at a time (the
writer lock). A reader may run at any time. The view merges only when it is
over 128,000 bytes, then down to 64,000. Compactions use up to 8 model calls
at once.

## Left out

Section 6 of the design (a turn: a fresh model call for each user message,
and a log of every reply and tool call) belongs to a chat front end. This tool
is a memory for sessions that other harnesses run. `capture` logs the messages
of those sessions, but a harness other than Claude Code, Codex and Pi needs its
own adapter. Queued prompts that a user types while Claude Code works are
logged only when Claude writes them to the transcript as user lines. There are
no subagent chats (`zoom("Name")`), no image messages (an image in a message
is `[image]`), and no `recall`, `nap` or `forget` command.

## Privacy

The store holds every word of every logged session: your prompts, the replies
and the tool output, as far as the redaction list above does not remove them.
It stays on your machine, in `~/.uniichat/chat`, unless you copy it. The
summaries are written by a model, so the text of each long message goes to the
model provider (DeepSeek by default) under its terms. The repository has no
memory data. Never commit a store or a session file.

## Agent instructions

Put this in the `AGENTS.md` of each agent:

```
## Memory

Your memory is UniiChat: one chat that never ends.
- At the start of every session, before any other tool call, run `memo wake`.
  If it says "Not awake yet", run the command it prints, until it ends with `</chat>`.
- When you need old information, find its latest line in the view and run
  `memo zoom ID N` until you have it whole. Do not search the store.
- Run `memo note "<what you learned or decided>"` when you learn something new
  or something worth keeping happens. (With the capture hooks installed the
  session messages are logged by themselves; `note` is for what the summaries
  may lose.)
- Never edit files in the store.
```

## Tests

```
python3 tests/test_memo.py       # the store, the tree and the model backends
python3 tests/test_capture.py    # capture, with recorded synthetic hook payloads
python3 -m unittest discover -s tests -v
```

Each script prints the time of each test. The tests use only the standard
library and synthetic data (`tests/fixtures`). The model is a stub, so they
need no key and no network. The tests clear `FM_TASK_ID` and
`NO_MISTAKES_GATE` from their environment.
