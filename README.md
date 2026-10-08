# UniiChat memory

One chat that never ends: a long-term memory for AI agents.

`memo` is one Python 3 file. It uses only the standard library. It follows
the UniiChat design ("UniiChat: one chat that never ends"):

- The log is append-only. Every message is stored word for word.
- A cheap model (Claude Haiku) writes a binary tree of one-line summaries.
  Each line has at most 512 bytes. The tool builds each line once.
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

| Setting | Value |
| --- | --- |
| Model | `claude-haiku-4-5-20251001` (`UNIICHAT_MODEL` changes it) |
| Effort | `xhigh` (`UNIICHAT_THINKING`: off, minimal, low, medium, high, xhigh, max) |
| Direct API | used when a key is found |
| Fallback | `pi --no-extensions -p` |

The key comes from `ANTHROPIC_API_KEY`. If it is not set, the tool runs
`pi auth print-api-key --provider anthropic` once per process and keeps the
key in memory. The tool never stores, logs or prints the key. If there is no
key, or `UNIICHAT_BACKEND=pi`, the tool calls `pi` instead. `UNIICHAT_BACKEND`
is `auto`, `api` or `pi`. `UNIICHAT_PI` is the `pi` command.

The API path marks the cache as the design says: the view goes in blocks of
4 lines, with one mark on the last whole block and one on the end of the
request. The `pi` path has no cache marks.

The system prompt is the design prompt, with Unii renamed to OptMem. The
paragraph about computers and the `zoom("Name")` line are removed.

## Files in the store

```
main/YYYY-MM-DD.jsonl   messages: {"i","kind","text","size","date"}
tree/YYYY-MM-DD.jsonl   tree nodes: {"l","i","text","size"}
view.json               {"n","epoch","view":[[l,i],...],"merging",
                         "compact":[[l,i],...],"compact_merging"}
queue.json              {"msgs":[i,...],"merges":[[l,i],...]}
.lock  .worker.lock     the writer lock, and the compactor lock
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
is a memory for sessions that other harnesses run. An agent can add `user`,
`unii`, `tool`, `echo` and `work` messages with `note --kind`, but nothing
does it by itself. There are no subagent chats (`zoom("Name")`), no image
messages, and no `recall`, `nap` or `forget` command.

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
  or something worth keeping happens.
- Never edit files in the store.
```

## Tests

```
python3 tests/test_memo.py       # prints the time of each test
python3 -m unittest discover -s tests -v
```

The tests use only the standard library and synthetic data. The model is a
stub, so they need no key and no network.
