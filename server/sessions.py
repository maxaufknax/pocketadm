"""Live, device-independent chat sessions for Vibe Code.

A `Session` is scoped to a chat_id (not a WebSocket). The agent loop runs as a
background task owned by the session, so:

  * closing the app / locking the phone does NOT kill the agent — work keeps
    running server-side and you can re-attach later and see the result;
  * the same chat can be open on several devices at once — every device gets
    the same live token stream (open it on your laptop and watch it type);
  * a reconnecting device replays the finished turns (from persisted history)
    plus the in-flight turn (from a live buffer) and keeps streaming;
  * you can send a message *while the agent is working* — it is queued and
    handled after the current turn, or injected mid-turn as steering.

Each connected device is a `Client` (one WebSocket). Clients attach/detach; the
Session outlives them. `manager` keeps one Session per active chat_id and drops
it once it is idle with no listeners (history is on disk, so nothing is lost).

Event protocol (server -> client) — superset of the old one:
    {"type":"chat", id,title,events,config,running,live}   snapshot on attach
    {"type":"user_echo","text","queued"}                   a message was sent
    {"type":"text"|"thinking"|"thinking_block", ...}       stream chunks
    {"type":"tool_request"|"tool_start"|"tool_result", ...}
    {"type":"run_state","running":bool}                    turn started/ended
    {"type":"services","items":[...]}                       new services found
    {"type":"usage",...} {"type":"stopped"} {"type":"done"} {"type":"error",...}
"""
import asyncio
import json
import os
import time

from . import agents, ai, audit, chats, config, discovery, permissions

# events that make up the replayable in-flight turn (see Session.live_events)
_LIVE_KINDS = {"text", "thinking", "thinking_block", "tool_request",
               "tool_start", "tool_result", "usage", "services"}
_LIVE_CAP = 600


class Client:
    """One connected device (a WebSocket). Sending is best-effort: a dead
    socket just gets dropped from the session, never crashes a broadcast."""

    def __init__(self, ws):
        self.ws = ws
        self.alive = True

    async def send(self, **event) -> bool:
        if not self.alive:
            return False
        try:
            await self.ws.send_text(json.dumps(event))
            return True
        except Exception:
            self.alive = False
            return False


class Session:
    def __init__(self, chat: dict):
        self.chat = chat
        self.chat_id = chat["id"]
        self.messages: list = chat["messages"]
        self.session_usage = chat.get("usage") or \
            {"input": 0, "output": 0, "cost": 0.0, "turns": 0}
        # runtime config (shared by every device on this chat)
        default = config.get_ai_default()
        self.mode = "agent"
        self.provider = default["provider"]
        self.model = default["model"]
        self.workdir = config.get_default_workspace() or ai.DEFAULT_WORKDIR
        self.thinking = "off"   # "off" | "low" | "medium" | "high" (effort tier)

        self.plan: list[dict] = chat.get("plan") or []   # visible to-do panel

        self.subscribers: set[Client] = set()
        self.inbox: list[str] = []            # queued user messages / steering
        self.pending: dict[str, asyncio.Future] = {}
        self.run_task: asyncio.Task | None = None
        self.running = False
        self.paused = False                   # awaiting a "continue" at a checkpoint
        self.pause_info: dict = {}
        self.continue_fut: asyncio.Future | None = None
        self.live_events: list[dict] = []     # in-flight turn, for late joiners
        self._mutated = False                 # did a state-changing tool run?
        self._steps = 0                       # tool runs since last checkpoint
        self._total_steps = 0                 # tool runs this prompt (for status)
        self._ckpt_start = 0.0                # wall-clock of last checkpoint

    # ---------------------------------------------------------- attach/replay

    def attach(self, client: Client) -> None:
        self.subscribers.add(client)

    def detach(self, client: Client) -> None:
        self.subscribers.discard(client)

    def config_dict(self) -> dict:
        return {"mode": self.mode, "provider": self.provider, "model": self.model,
                "workdir": self.workdir, "thinking": self.thinking}

    async def send_snapshot(self, client: Client) -> None:
        """Bring a freshly-attached device fully up to date."""
        await client.send(
            type="chat", id=self.chat_id, title=self.chat["title"],
            events=chats.display_events(self.messages),
            config=self.config_dict(), running=self.running,
            plan=self.plan, paused=self.paused, pause=self.pause_info,
            live=self.live_events if self.running else [])

    async def broadcast(self, live: bool = True, **event) -> None:
        if live and event.get("type") in _LIVE_KINDS:
            self._buffer_live(event)
        dead = [c for c in self.subscribers if not await c.send(**event)]
        for c in dead:
            self.subscribers.discard(c)

    def _buffer_live(self, event: dict) -> None:
        # coalesce consecutive text/thinking deltas so the buffer stays small
        if event["type"] in ("text", "thinking") and self.live_events:
            last = self.live_events[-1]
            if last["type"] == event["type"]:
                last["delta"] += event["delta"]
                return
        self.live_events.append(dict(event))
        if len(self.live_events) > _LIVE_CAP:
            del self.live_events[0:len(self.live_events) - _LIVE_CAP]

    # ------------------------------------------------------------- controls

    def set_config(self, msg: dict) -> bool:
        changed = False
        if msg.get("mode") in ai.MODE_TOOLS and msg["mode"] != self.mode:
            self.mode, changed = msg["mode"], True
        if msg.get("provider") in ai.CHAT_PROVIDERS and msg["provider"] != self.provider:
            self.provider, changed = msg["provider"], True
        if "model" in msg:
            model = msg["model"] or config.DEFAULT_MODELS.get(self.provider, "")
            if model != self.model:
                self.model, changed = model, True
        if msg.get("workdir"):
            wd = self._safe_workdir(msg["workdir"])
            if wd != self.workdir:
                self.workdir, changed = wd, True
        if "thinking" in msg:
            tier = ai.normalize_thinking(msg["thinking"])
            if tier != self.thinking:
                self.thinking, changed = tier, True
        return changed

    def _safe_workdir(self, path: str) -> str:
        allowed = config.get_workspaces() + [ai.DEFAULT_WORKDIR]
        resolved = os.path.realpath(path)
        for root in allowed:
            r = os.path.realpath(root)
            if resolved == r or resolved.startswith(r + os.sep):
                return resolved if os.path.isdir(resolved) else self.workdir
        return self.workdir

    def resolve_approval(self, msg: dict) -> None:
        fut = self.pending.pop(msg.get("id", ""), None)
        if fut and not fut.done():
            fut.set_result(bool(msg.get("approved")))

    def stop(self) -> None:
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result(False)
        self.pending.clear()
        if self.continue_fut and not self.continue_fut.done():
            self.continue_fut.set_result(False)
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()

    def resume_continue(self) -> None:
        """User tapped Continue on a checkpoint pause."""
        if self.continue_fut and not self.continue_fut.done():
            self.continue_fut.set_result(True)

    def rewind(self, ordinal: int) -> str | None:
        """Drop history from the Nth user message on (0-based, counting only
        visible user messages) — the Claude-Code-style 'edit / retract' flow.
        Returns the removed message's text, or None if it can't rewind."""
        if self.running:
            return None
        count = -1
        for i, m in enumerate(self.messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str) \
                    and m["content"].strip():
                count += 1
                if count == ordinal:
                    removed = m["content"]
                    del self.messages[i:]
                    self._persist()
                    return removed
        return None

    async def submit_user(self, text: str, context: str = "") -> None:
        text = (text or "").strip()
        if not text and not context:
            return
        # `context` (attached services/apps/files) is fed to the model as a
        # preamble but not shown in the transcript — the chip UI shows it instead.
        payload = (context.strip() + "\n\n" + text).strip() if context.strip() else text
        self.inbox.append(payload)
        # every device shows the (clean) message immediately (single source of truth)
        await self.broadcast(type="user_echo", text=text, queued=self.running, live=False)
        if self.paused:
            # a message sent while paused both steers and resumes the agent
            self.resume_continue()
        elif not self.running:
            self.run_task = asyncio.ensure_future(self._runner())

    # --------------------------------------------------------------- runner

    async def _runner(self) -> None:
        """Owns the agent while there is user input to answer. Survives client
        disconnects; only a `stop` or completion ends it."""
        if config.DEMO:
            await self.broadcast(type="error", live=False,
                                 message="This is a read-only demo. Open the sample chat to see "
                                         "the AI agent in action — or install PocketADM on your "
                                         "own server and add an API key to chat live.")
            self._drain_inbox_into_history()
            return
        if not self.provider:
            await self.broadcast(type="error", live=False,
                                 message="No AI provider configured. Add a key under More → AI, "
                                         "or set up a local model under More → Local AI.")
            self._drain_inbox_into_history()
            return
        self.running = True
        await self.broadcast(type="run_state", running=True, live=False)
        try:
            while self.inbox:
                self._drain_inbox_into_history()
                await self._agent_cycle()
        except asyncio.CancelledError:
            await self._safe_broadcast(type="stopped", live=False)
        except Exception as e:  # noqa: BLE001 — surface any provider error
            self._persist()
            await self._safe_broadcast(type="error", message=str(e), live=False)
            self._push("crit", "Agent stopped on an error", str(e)[:300])
        finally:
            self.running = False
            self.paused = False
            self.pause_info = {}
            self.continue_fut = None
            self.live_events = []
            await self._safe_broadcast(type="run_state", running=False, live=False)
            await self._safe_broadcast(type="done", live=False)

    def _drain_inbox_into_history(self) -> None:
        while self.inbox:
            self.messages.append({"role": "user", "content": self.inbox.pop(0)})

    async def _agent_cycle(self) -> None:
        """One user turn: model + tools until the model stops calling tools.
        Mirrors the old ChatSession.run_turn but streams via broadcast and can
        pick up steering messages between tool iterations."""
        cfg = ai._cfg_for(self.provider, self.model)
        sysprompt = ai.system_prompt(self.workdir, self.mode)
        tool_names = ai.allowed_tools(self.mode)
        turn_usage = {"input": 0, "output": 0}
        self._mutated = False
        self._total_steps = 0
        self._reset_checkpoint()
        before_ids = await discovery.snapshot_ids() if tool_names else set()
        text_parts: list[str] = []
        try:
            iteration = 0
            while True:
                iteration += 1
                self.live_events = []           # commit boundary for reconnects
                text_parts = []
                tool_calls: list[dict] = []
                thinking_blocks: list[dict] = []
                async for kind, payload in ai.get_stream(
                        cfg, self.messages, sysprompt, tool_names, self.thinking):
                    if kind == "text":
                        text_parts.append(payload)
                        await self.broadcast(type="text", delta=payload)
                    elif kind == "thinking":
                        await self.broadcast(type="thinking", delta=payload)
                    elif kind == "thinking_block":
                        thinking_blocks.append(payload)
                    elif kind == "tool_call":
                        tool_calls.append(payload)
                    elif kind == "usage":
                        turn_usage["input"] += payload["input"]
                        turn_usage["output"] += payload["output"]
                msg: dict = {"role": "assistant", "content": "".join(text_parts),
                             "tool_calls": tool_calls}
                if thinking_blocks:
                    msg["thinking_blocks"] = thinking_blocks
                self.messages.append(msg)
                if not tool_calls:
                    self._persist()
                    break
                for tc in tool_calls:
                    output = await self._run_tool_with_approval(tc)
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                          "content": output})
                self._persist()
                self.live_events = []
                # steering: a message sent mid-turn is answered on the next model call
                self._drain_inbox_into_history()
                # watchdog: instead of silently stopping at a turn cap, checkpoint —
                # keep going autonomously, or pause and ask the user to continue.
                if not await self._checkpoint_gate(iteration, turn_usage):
                    break
            await self._account(cfg, turn_usage)
            if self._mutated and tool_names:
                await self._report_new_services(before_ids)
        except asyncio.CancelledError:
            if text_parts and (not self.messages or self.messages[-1]["role"] != "assistant"):
                self.messages.append({"role": "assistant",
                                      "content": "".join(text_parts), "tool_calls": []})
            self._persist()
            try:
                await self._account(cfg, turn_usage)
            except Exception:
                pass
            raise

    async def _run_tool_with_approval(self, tc: dict) -> str:
        if tc["name"] == "update_plan":
            return await self._apply_plan(tc)
        auto = (self.mode == "auto"
                or (self.mode in ("agent", "plan") and tc["name"] in ai.SAFE_TOOLS)) \
            and not ai._sensitive_call(tc)
        if not auto:
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self.pending[tc["id"]] = fut
            await self.broadcast(type="tool_request", id=tc["id"], name=tc["name"], args=tc["args"])
            try:
                approved = await asyncio.wait_for(fut, timeout=1800)
            except asyncio.TimeoutError:
                approved = False
            finally:
                self.pending.pop(tc["id"], None)
            if not approved:
                await self.broadcast(type="tool_result", id=tc["id"], output="[denied by user]")
                return "The user declined this action. Ask how they want to proceed."
        await self.broadcast(type="tool_start", id=tc["id"], name=tc["name"], args=tc["args"])
        before = ai.capture_before(tc["name"], tc["args"], self.workdir)
        output = await ai.execute_tool(tc["name"], tc["args"], self.workdir)
        diff = ai.build_file_diff(tc["name"], tc["args"], self.workdir, before)
        self._steps += 1
        self._total_steps += 1
        if tc["name"] not in ai.SAFE_TOOLS:
            self._mutated = True
            audit.record("agent_tool", target=tc["name"],
                         source="auto" if self.mode == "auto" else "agent",
                         detail=ai._tool_audit_detail(tc["name"], tc["args"]))
        output = await self._check_permission(tc, output)
        await self.broadcast(type="tool_result", id=tc["id"], output=output, diff=diff)
        return output

    async def _apply_plan(self, tc: dict) -> str:
        """update_plan is display-only: store the plan on the session, push it to
        every device, and return a short confirmation to the model. No tool card."""
        raw = tc.get("args", {}).get("steps") or []
        plan = []
        for s in raw:
            title = str((s or {}).get("title", "")).strip()[:200]
            if not title:
                continue
            status = s.get("status") if s.get("status") in ("pending", "in_progress", "done") \
                else "pending"
            plan.append({"title": title, "status": status})
        self.plan = plan[:12]
        self._persist()
        await self.broadcast(type="plan", items=self.plan, live=False)
        done = sum(1 for s in self.plan if s["status"] == "done")
        return f"Plan updated ({done}/{len(self.plan)} done)."

    async def _check_permission(self, tc: dict, output: str) -> str:
        """If a tool failed for lack of a permission, surface an actionable
        request to the user and nudge the model not to retry blindly."""
        issue = ai.detect_permission_issue(tc["name"], tc.get("args", {}), output)
        if not issue:
            return output
        req = permissions.add(**issue, chat_id=self.chat_id)
        if req.get("_new"):
            await self.broadcast(type="permission", request={k: req[k] for k in
                                 ("id", "kind", "title", "detail", "explanation", "risk", "fix")},
                                 live=False)
            self._push("warn", "Permission needed: " + issue["title"],
                       issue["explanation"][:200])
        return (output + "\n\n[Helmsman: this failed because of a missing permission "
                f"('{issue['title']}'). The user has been shown a permission request — do "
                "not retry blindly. Continue with what you can do without it, or tell the "
                "user what access you need and why.]")

    # --------------------------------------------------------- watchdog

    def _reset_checkpoint(self) -> None:
        self._steps = 0
        self._ckpt_start = time.time()

    async def _checkpoint_gate(self, iteration: int, turn_usage: dict) -> bool:
        """Called at each tool-iteration boundary. Returns True to keep working,
        False to end the turn. Decides between running on autonomously and
        pausing to ask the user, and always pauses at the hard safety cap."""
        au = config.get_autonomy()
        hard = iteration >= ai.HARD_MAX_ITERATIONS
        due = ""
        if au["steps"] and self._steps >= au["steps"]:
            due = "steps"
        elif au["minutes"] and (time.time() - self._ckpt_start) >= au["minutes"] * 60:
            due = "time"
        if not hard and not due:
            return True
        # autonomous mode keeps going through soft checkpoints (only the hard cap stops it)
        if au["pause_mode"] == "autonomous" and not hard:
            self._reset_checkpoint()
            return True
        return await self._pause_and_wait("limit" if hard else due, turn_usage)

    def _status_summary(self, reason: str, turn_usage: dict) -> dict:
        task = next((m["content"] for m in self.messages
                     if m["role"] == "user" and isinstance(m["content"], str)), "")
        last = ""
        for m in reversed(self.messages):
            if m["role"] == "assistant" and m.get("content"):
                last = m["content"]
                break
        why = {"steps": f"{self._total_steps} steps in — checking in.",
               "time": "Been working a while — checking in.",
               "limit": "Reached the safety limit for one prompt."}.get(reason, "Checking in.")
        return {"reason": reason, "title": "Agent paused — continue?", "why": why,
                "task": task[:160], "last": (last[:400] or "Working…"),
                "steps": self._total_steps,
                "tokens": turn_usage["input"] + turn_usage["output"]}

    async def _pause_and_wait(self, reason: str, turn_usage: dict) -> bool:
        info = self._status_summary(reason, turn_usage)
        self.paused = True
        self.pause_info = info
        self._persist()
        await self._safe_broadcast(type="paused", live=False, **info)
        await self._safe_broadcast(type="run_state", running=True, paused=True, live=False)
        self._push("warn", info["title"], f"{info['why']} {info['last'][:160]}")
        self.continue_fut = asyncio.get_running_loop().create_future()
        try:
            cont = await asyncio.wait_for(self.continue_fut, timeout=6 * 3600)
        except asyncio.TimeoutError:
            cont = False
        finally:
            self.continue_fut = None
            self.paused = False
            self.pause_info = {}
        await self._safe_broadcast(type="run_state", running=True, paused=False, live=False)
        if cont:
            self._reset_checkpoint()
            # a message sent while paused steers the very next model call
            self._drain_inbox_into_history()
        return cont

    def _push(self, status: str, title: str, body: str) -> None:
        """Fire-and-forget agent push to the global ntfy topic (if configured)."""
        au = config.get_autonomy()
        url = config.get_ntfy_url()
        if not (au["push"] and url):
            return
        name = config.get_server_name() or "Helmsman"
        asyncio.ensure_future(agents._push_ntfy(url, status, f"{name}: {title}", body))

    async def _report_new_services(self, before_ids: set) -> None:
        try:
            items = await discovery.new_services(before_ids)
        except Exception:
            items = []
        if items:
            await self.broadcast(type="services", items=items, live=False)

    async def _account(self, cfg: dict, turn_usage: dict) -> None:
        cost = ai.estimate_cost(cfg["provider"], cfg["model"],
                                turn_usage["input"], turn_usage["output"])
        self.session_usage["input"] += turn_usage["input"]
        self.session_usage["output"] += turn_usage["output"]
        self.session_usage["turns"] += 1
        if cost is not None:
            self.session_usage["cost"] += cost
        ai._persist_usage(cfg, turn_usage, cost)
        self._persist()
        await self._safe_broadcast(type="chat_meta", id=self.chat_id,
                                   title=self.chat["title"], live=False)
        await self._safe_broadcast(
            type="usage", live=False,
            turn={**turn_usage, "cost": cost, "model": cfg["model"]},
            session=self.session_usage)

    def _persist(self) -> None:
        if self.chat["title"] == chats.DEFAULT_TITLE:
            first = next((m["content"] for m in self.messages
                          if m["role"] == "user" and isinstance(m["content"], str)), "")
            if first:
                self.chat["title"] = chats.title_from(first)
        self.chat["messages"] = self.messages
        self.chat["usage"] = self.session_usage
        self.chat["plan"] = self.plan
        try:
            chats.save(self.chat)
        except Exception:
            pass

    async def _safe_broadcast(self, **event) -> None:
        try:
            await self.broadcast(**event)
        except Exception:
            pass

    @property
    def idle(self) -> bool:
        return not self.running and not self.subscribers


class SessionManager:
    """Keeps live sessions keyed by chat_id and reaps idle ones."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def get(self, chat_id: str) -> Session | None:
        """A live session for this chat, if one is currently open."""
        return self._sessions.get(chat_id)

    def open(self, chat_id: str = "") -> Session:
        if chat_id and chat_id in self._sessions:
            return self._sessions[chat_id]
        chat = chats.load(chat_id) if chat_id else None
        if not chat:
            chat = chats.create()
        sess = self._sessions.get(chat["id"])
        if sess is None:
            sess = Session(chat)
            self._sessions[chat["id"]] = sess
        return sess

    def reap(self, session: Session) -> None:
        """Drop a session that no device is watching and no work is running.
        Everything is on disk, so re-opening later reloads it cleanly."""
        if session.idle and self._sessions.get(session.chat_id) is session:
            self._sessions.pop(session.chat_id, None)


manager = SessionManager()


async def ws_chat(ws) -> None:
    """WebSocket endpoint body — routes one device to the shared sessions."""
    client = Client(ws)
    session: Session | None = None
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t in ("open", "reset"):
                if session:
                    session.detach(client)
                    manager.reap(session)
                session = manager.open("" if t == "reset" else msg.get("id", ""))
                session.attach(client)
                await session.send_snapshot(client)
            elif t == "config":
                if session and session.set_config(msg):
                    await session.broadcast(type="config", live=False, **session.config_dict())
            elif t == "user":
                if session is None:
                    session = manager.open(msg.get("chat_id", ""))
                    session.attach(client)
                    await session.send_snapshot(client)
                await session.submit_user(msg.get("text", ""), msg.get("context", ""))
            elif t == "rewind":
                # edit/retract a sent message: truncate history at that user
                # message; with "text" set, immediately resend the edited version
                if session:
                    if session.running:
                        await client.send(type="error",
                                          message="Stop the agent before editing a message.")
                    else:
                        removed = session.rewind(int(msg.get("ordinal", -1)))
                        if removed is not None:
                            for c in list(session.subscribers):
                                await session.send_snapshot(c)
                            new_text = (msg.get("text") or "").strip()
                            if new_text:
                                await session.submit_user(new_text)
                            else:
                                await session.broadcast(type="rewound", text=removed,
                                                        live=False)
            elif t == "approve":
                if session:
                    session.resolve_approval(msg)
            elif t == "continue":
                if session:
                    session.resume_continue()
            elif t == "stop":
                if session:
                    session.stop()
    except Exception:
        pass
    finally:
        if session:
            session.detach(client)
            manager.reap(session)
