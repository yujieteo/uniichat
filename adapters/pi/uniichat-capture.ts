// UniiChat capture for Pi: after each turn, tell `memo capture pi` which
// session file to read. memo reads what is new, redacts secrets and appends
// the messages. This file never reads or writes the chat itself.
//
// It stays silent in every Pi that is not the captain's interactive one
// (print, json and rpc modes, which subagents use), in Firstmate workers
// (FM_TASK_ID) and in no-mistakes pipeline agents (NO_MISTAKES_GATE).
// It never throws and never waits for memo.

import { spawn } from "node:child_process";
import { resolve } from "node:path";

export default function (pi: any) {
  const memo = process.env.UNIICHAT_MEMO || "memo";
  let running = false;
  let next: string | null = null;

  function run(payload: string) {
    running = true;
    try {
      const child = spawn(memo, ["capture", "pi"], {
        stdio: ["pipe", "ignore", "ignore"],
        detached: true,
      });
      const done = () => {
        running = false;
        if (next !== null) {
          const again = next;
          next = null;
          run(again);
        }
      };
      child.on("error", done);
      child.on("exit", done);
      child.stdin.on("error", () => {});
      child.stdin.end(payload);
      child.unref();
    } catch {
      running = false;
    }
  }

  function send(ctx: any) {
    try {
      if (ctx.mode !== "tui") return;
      if (process.env.FM_TASK_ID || process.env.NO_MISTAKES_GATE) return;
      const file = ctx.sessionManager.getSessionFile();
      if (!file) return;
      const payload = JSON.stringify({
        session_id: ctx.sessionManager.getSessionId(),
        session_file: resolve(file),
        mode: ctx.mode,
      });
      if (running) next = payload;
      else run(payload);
    } catch {
      // never into the session
    }
  }

  pi.on("turn_end", async (_event: any, ctx: any) => send(ctx));
  pi.on("agent_settled", async (_event: any, ctx: any) => send(ctx));
  pi.on("session_shutdown", async (_event: any, ctx: any) => send(ctx));
}
