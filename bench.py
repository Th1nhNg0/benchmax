#!/usr/bin/env python3
"""benchmax: a personal benchmark runner for AI models, using the pi, Codex or Claude Code agents.

Usage:
  python3 bench.py list
  python3 bench.py login pi|codex|claude
  python3 bench.py run -m openai-codex/gpt-6-luna [-a pi] [-t high] [-k 3] [-j 2] [--only 'euler-*'] [--category algorithms]
  python3 bench.py regrade results/<run>
  python3 bench.py report [results/<run> ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS_DIR = ROOT / "tasks"
RESULTS_DIR = ROOT / "results"
SANDBOX_DIR = ROOT / "sandbox"

AUTH_VOLUME = "benchmax-auth"      # the agents' logins, separate from your everyday ones


def load_config() -> dict:
    path = ROOT / "benchmax.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


CONFIG = load_config()


# ---------------------------------------------------------------- tasks

def load_tasks() -> list[dict]:
    """Tasks live in tasks/<category>/<task-id>/; the folder names are the category and id."""
    tasks, seen = [], {}
    for meta_path in sorted(TASKS_DIR.glob("*/*/task.toml")):
        task_dir = meta_path.parent
        meta = tomllib.loads(meta_path.read_text())
        meta["id"] = task_dir.name
        meta["category"] = task_dir.parent.name
        if meta["id"] in seen:
            sys.exit(f"duplicate task id {meta['id']!r} in {seen[meta['id']]} and {task_dir}")
        seen[meta["id"]] = task_dir
        meta.setdefault("timeout_s", 900)
        meta["dir"] = task_dir
        meta["prompt"] = (task_dir / "prompt.md").read_text()
        tasks.append(meta)
    return tasks


DEFAULT_RULES = ("Solve this task yourself. Do not search the web or look up the answer or existing "
                 "solutions online, and do not cheat in any other way.")


def task_prompt(task: dict) -> str:
    """The task's prompt plus the rules every task shares (`[prompt] rules` in benchmax.toml)."""
    rules = CONFIG.get("prompt", {}).get("rules", DEFAULT_RULES).strip()
    return f"{task['prompt'].rstrip()}\n\n{rules}\n" if rules else task["prompt"]


def missing_answer(task: dict) -> bool:
    return (task["grader"] == "exact" and not task.get("expected")) or \
           (task["grader"] == "regex" and not task.get("pattern"))


def select_tasks(tasks, only=None, category=None):
    for t in [t for t in tasks if missing_answer(t)]:
        print(f"skip {t['id']}: no expected answer in task.toml", file=sys.stderr)
    tasks = [t for t in tasks if not missing_answer(t)]
    if only:
        tasks = [t for t in tasks if any(fnmatch.fnmatch(t["id"], p) for p in only.split(","))]
    if category:
        cats = set(category.split(","))
        tasks = [t for t in tasks if t["category"] in cats]
    return tasks


# ---------------------------------------------------------------- agents
#
# Each agent CLI runs inside the sandbox container with its own built-in tools, stripped of user
# configuration (instructions files, skills, MCP servers, plugins) and web search, so results
# depend on the model and the harness, not on personal setup.

def agent_cfg(agent: str) -> dict:
    return CONFIG.get("agents", {}).get(agent, {})


def agent_command(agent: str, model: str, thinking: str | None, prompt: str, tools=None) -> list[str]:
    """The command line for one non-interactive agent run. tools=[] means no tools at all."""
    cfg = agent_cfg(agent)
    if agent == "pi":
        cmd = ["pi", "-p", "--mode", "json", "--no-session", "--no-extensions", "--no-skills",
               "--no-context-files", "--no-prompt-templates", "--no-themes", "--model", model]
        for ext in cfg.get("extensions", []):
            cmd += ["-e", ext]  # provider extensions, e.g. antigravity
        if thinking:
            cmd += ["--thinking", thinking]
        # Extensions can register extra tools (e.g. web search), so always pin the tool set.
        tools = cfg.get("tools", ["read", "bash", "edit", "write"]) if tools is None else tools
        cmd += ["--tools", ",".join(tools)] if tools else ["--no-tools"]
        return cmd + ["--", prompt]
    if agent == "codex":
        cmd = ["codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
               "--ignore-rules", "--dangerously-bypass-approvals-and-sandbox", "-C", "/workspace", "-m", model]
        # ChatGPT account connectors (GitHub, Gmail, Drive, ...), plugins, browser and computer use run
        # on OpenAI's side or act on the user's accounts: never available to a benchmark attempt.
        for feature in CODEX_DISABLED_FEATURES:
            cmd += ["--disable", feature]
        if thinking:
            cmd += ["-c", f'model_reasoning_effort="{thinking}"']
        for kv in cfg.get("config", []):
            cmd += ["-c", kv]
        return cmd + ["--", prompt]
    if agent == "claude":
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
               "--dangerously-skip-permissions", "--setting-sources", "", "--strict-mcp-config",
               "--disable-slash-commands", "--model", model]
        if thinking:
            cmd += ["--effort", thinking]
        if tools == []:
            cmd += ["--tools", ""]
        elif tools or cfg.get("tools"):
            cmd += ["--tools", ",".join(tools or cfg["tools"])]
        # Tools that only work across sessions (wake-ups, schedules) can never fire in a one-shot run:
        # the model would end its turn waiting for a wake-up that never comes.
        cmd += ["--disallowed-tools", ",".join(CLAUDE_ONE_SHOT_BLOCKED + cfg.get("disallowed_tools", []))]
        return cmd + ["--", prompt]
    sys.exit(f"unknown agent {agent!r} (use pi, codex or claude)")


CODEX_DISABLED_FEATURES = [
    "apps", "plugins", "remote_plugin", "browser_use", "browser_use_external", "computer_use",
    "image_generation", "tool_suggest", "skill_search", "skill_mcp_dependency_install",
]

CLAUDE_ONE_SHOT_BLOCKED = ["ScheduleWakeup", "CronCreate", "CronDelete", "CronList", "RemoteTrigger"]

LOGIN_COMMANDS = {
    "pi": lambda: ["pi", *[a for ext in agent_cfg("pi").get("extensions", []) for a in ("-e", ext)]],
    "codex": lambda: ["codex", "login"],
    "claude": lambda: ["claude", "auth", "login"],
}


def empty_stats() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cost": None, "turns": 0, "tool_calls": 0,
            "final_text": "", "error": None, "model": None, "model_seconds": None}


def parse_events(agent: str, path: Path) -> dict:
    """Summarise an agent's JSON event stream: tokens, cost, tool calls, final reply, errors."""
    events = []
    times_path = path.with_suffix(".times")
    times = times_path.read_text().split() if times_path.exists() else []
    for i, line in enumerate(path.read_text(errors="replace").splitlines() if path.exists() else []):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            ev["_t"] = float(times[i]) if i < len(times) else None  # seconds since the agent started
            events.append(ev)
    return {"pi": parse_pi, "codex": parse_codex, "claude": parse_claude}[agent](events)


def model_time(events, tool_spans: list[tuple[float, float]]) -> float | None:
    """Time not spent running tools: from the first to the last event, minus the tool spans.
    Covers the model's generation and the API round trips, not the container start-up."""
    stamps = [ev["_t"] for ev in events if ev.get("_t") is not None]
    if len(stamps) < 2:
        return None
    return round(max(stamps[-1] - stamps[0] - sum(b - a for a, b in tool_spans), 0), 1)


def parse_pi(events) -> dict:
    stats = empty_stats()
    stats["cost"] = 0.0
    stop_reason = None
    for ev in events:
        if ev.get("type") != "message_end" or ev["message"].get("role") != "assistant":
            continue
        msg = ev["message"]
        usage = msg.get("usage", {})
        stats["input"] += usage.get("input", 0)
        stats["output"] += usage.get("output", 0)
        stats["cache_read"] += usage.get("cacheRead", 0)
        stats["cost"] += usage.get("cost", {}).get("total", 0) or 0
        stats["turns"] += 1
        stats["model"] = msg.get("model")
        stop_reason = msg.get("stopReason")
        content = msg.get("content") or []
        stats["tool_calls"] += sum(1 for c in content if c.get("type") == "toolCall")
        text = "".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
        if text:
            stats["final_text"] = text
        if stop_reason in ("error", "aborted"):
            stats["error"] = msg.get("errorMessage") or stop_reason
    if stats["turns"] == 0:
        stats["error"] = stats["error"] or "no reply"
    starts, spans = {}, []
    for ev in events:
        if ev.get("_t") is None:
            continue
        if ev.get("type") == "tool_execution_start":
            starts[ev.get("toolCallId")] = ev["_t"]
        elif ev.get("type") == "tool_execution_end" and ev.get("toolCallId") in starts:
            spans.append((starts.pop(ev["toolCallId"]), ev["_t"]))
    stats["model_seconds"] = model_time(events, spans)
    return stats


def parse_codex(events) -> dict:
    stats = empty_stats()
    starts, spans = {}, []
    for ev in events:
        kind = ev.get("type")
        item = ev.get("item", {})
        if ev.get("_t") is not None and item.get("type") not in (None, "reasoning", "agent_message", "todo_list", "error"):
            if kind == "item.started":
                starts[item.get("id")] = ev["_t"]
            elif kind == "item.completed" and item.get("id") in starts:
                spans.append((starts.pop(item["id"]), ev["_t"]))
        if kind == "turn.completed":
            usage = ev.get("usage", {})
            stats["input"] += usage.get("input_tokens", 0)
            stats["cache_read"] += usage.get("cached_input_tokens", 0)
            stats["output"] += usage.get("output_tokens", 0)
            stats["turns"] += 1
        elif kind == "item.completed":
            item = ev.get("item", {})
            if item.get("type") == "agent_message" and item.get("text", "").strip():
                stats["final_text"] = item["text"].strip()
            elif item.get("type") not in ("reasoning", "agent_message", "todo_list", "error"):
                stats["tool_calls"] += 1
        elif kind in ("turn.failed", "error"):
            stats["error"] = (ev.get("error") or {}).get("message") or ev.get("message") or kind
    if stats["turns"] == 0 and not stats["error"]:
        stats["error"] = "no reply"
    stats["model_seconds"] = model_time(events, spans)
    return stats


def parse_claude(events) -> dict:
    stats = empty_stats()
    tool_ids, result = set(), None
    for ev in events:
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            stats["model"] = ev.get("model")
        elif kind == "assistant":
            for c in ev.get("message", {}).get("content") or []:
                if c.get("type") == "tool_use":
                    tool_ids.add(c.get("id"))
        elif kind == "result":
            result = ev
    stats["tool_calls"] = len(tool_ids)
    if result is None:
        stats["error"] = "no result"
        return stats
    usage = result.get("usage", {})
    stats["input"] = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
    stats["cache_read"] = usage.get("cache_read_input_tokens", 0)
    stats["output"] = usage.get("output_tokens", 0)
    stats["cost"] = result.get("total_cost_usd")
    stats["turns"] = result.get("num_turns", 0)
    if result.get("duration_api_ms") is not None:
        stats["model_seconds"] = round(result["duration_api_ms"] / 1000, 1)
    stats["final_text"] = (result.get("result") or "").strip()
    if result.get("is_error") or result.get("subtype") != "success":
        stats["error"] = stats["final_text"][:500] or result.get("subtype") or "error"
    return stats


# ---------------------------------------------------------------- sandbox
#
# Every attempt runs in a fresh container: the agent CLI and its tools inside, the workspace
# mounted at /workspace, and normal internet access.

def sandbox_cfg() -> dict:
    return {"cpus": 4, "memory": "8g", "pids": 1024, **CONFIG.get("sandbox", {})}


def docker(*args, check=True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True, **kw)


_image_lock = threading.Lock()


def build_image(tag: str, context: Path, build_args: dict | None = None) -> str:
    with _image_lock:
        if docker("image", "inspect", tag, check=False).returncode != 0:
            print(f"building {tag} ...", flush=True)
            extra = [a for k, v in (build_args or {}).items() for a in ("--build-arg", f"{k}={v}")]
            subprocess.run(["docker", "build", "-q", *extra, "-t", tag, str(context)],
                           check=True, stdout=subprocess.DEVNULL)
    return tag


def dir_hash(path: Path, salt: str = "") -> str:
    h = hashlib.sha256(salt.encode())
    for f in sorted(path.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(path)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:12]


def sandbox_image() -> str:
    """Image tagged by the Dockerfile's hash, built on first use."""
    if sandbox_cfg().get("image"):
        return sandbox_cfg()["image"]
    digest = hashlib.sha256((SANDBOX_DIR / "Dockerfile").read_bytes()).hexdigest()[:12]
    return build_image(f"benchmax-sandbox:{digest}", SANDBOX_DIR)


def task_image(task: dict) -> str:
    """The sandbox image for a task: `image` from task.toml, an image built from the task's
    image/Dockerfile on top of the default sandbox, or the default sandbox itself."""
    if task.get("image"):
        return task["image"]
    ctx = task["dir"] / "image"
    base = sandbox_image()
    if not (ctx / "Dockerfile").exists():
        return base
    return build_image(f"benchmax-task-{task['id']}:{dir_hash(ctx, base)}", ctx, {"BASE": base})


def ensure_auth_volume() -> None:
    """The volume holding the agents' logins, owned by the current user."""
    if docker("volume", "inspect", AUTH_VOLUME, check=False).returncode == 0:
        return
    docker("volume", "create", AUTH_VOLUME)
    settings = json.dumps({"quietStartup": True})
    docker("run", "--rm", "-v", f"{AUTH_VOLUME}:/auth", sandbox_image(), "sh", "-c",
           f"mkdir -p /auth/pi /auth/codex /auth/claude && echo '{settings}' > /auth/pi/settings.json"
           f" && chown -R {os.getuid()}:{os.getgid()} /auth && chmod 700 /auth")


AGENT_ENV = {
    # Logins live in the auth volume, outside HOME, so an agent cleaning up HOME can't delete them.
    "PI_CODING_AGENT_DIR": "/auth/pi", "CODEX_HOME": "/auth/codex", "CLAUDE_CONFIG_DIR": "/auth/claude",
    "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
    # Claude Code: no background commands, which a one-shot (-p) run would abandon when it ends.
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
}


def container_args(image: str, mounts: list[tuple[Path, str, str]], workdir: str, network: str,
                   env: dict | None = None) -> list[str]:
    cfg = sandbox_cfg()
    args = ["--init", "--network", network, "--user", f"{os.getuid()}:{os.getgid()}", "-w", workdir,
            "--cpus", str(cfg["cpus"]), "--memory", cfg["memory"], "--pids-limit", str(cfg["pids"])]
    for src, dst, mode in mounts:
        args += ["-v", f"{src}:{dst}:{mode}"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    return args + [image]


def stop_container(name: str) -> None:
    docker("rm", "-f", name, check=False)


def remove_stale_containers() -> None:
    """Remove containers left behind by runs that were killed (named benchmax-<pid>-...)."""
    for name in docker("ps", "-a", "--filter", "name=^benchmax-", "--format", "{{.Names}}").stdout.split():
        m = re.match(r"benchmax-(\d+)-", name)
        if not m:
            continue
        try:
            os.kill(int(m.group(1)), 0)
        except ProcessLookupError:
            stop_container(name)
        except PermissionError:
            pass


def pump_events(pipe, stdout_path: Path, start: float) -> None:
    """Copy the agent's stdout to stdout_path, noting when each line arrived (seconds since start) in
    a sibling .times file; the agents' event streams carry no usable timestamps of their own."""
    with open(stdout_path, "wb") as out, open(stdout_path.with_suffix(".times"), "w") as times:
        for line in pipe:
            out.write(line)
            out.flush()
            times.write(f"{time.monotonic() - start:.3f}\n")
            times.flush()


def run_process(cmd, timeout_s, stdout_path, stderr_path) -> tuple[int | None, float]:
    """Run cmd in its own process group; returns (exit code or None on timeout, seconds)."""
    start = time.monotonic()
    with open(stderr_path, "w") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        pump = threading.Thread(target=pump_events, args=(proc.stdout, Path(stdout_path), start))
        pump.start()
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code = None
        pump.join(timeout=10)
    return code, time.monotonic() - start


def run_agent(agent: str, cmd: list[str], work: Path, image: str, name: str, timeout_s: int,
              out: Path, err: Path) -> tuple[int | None, float]:
    """Run an agent command in a fresh container with `work` mounted at /workspace."""
    args = ["docker", "run", "--rm", "--name", name,
            *container_args(image, [(work, "/workspace", "rw"), (AUTH_VOLUME, "/auth", "rw")], "/workspace",
                            "bridge", AGENT_ENV), *cmd]
    try:
        return run_process(args, timeout_s, out, err)
    finally:
        stop_container(name)  # killing `docker run` on timeout does not stop the container


# ---------------------------------------------------------------- graders

def extract_answer(text: str) -> str:
    matches = re.findall(r"^\s*\**ANSWER:?\**\s*:?\s*(.+?)\s*$", text, re.MULTILINE | re.IGNORECASE)
    return matches[-1] if matches else text.strip()


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().strip("`*").strip()).casefold()


def grade(task: dict, attempt_dir: Path, final_text: str) -> dict:
    kind = task["grader"]
    if kind == "exact":
        answer = extract_answer(final_text)
        expected = task["expected"] if isinstance(task["expected"], list) else [task["expected"]]
        ok = normalize(answer) in {normalize(e) for e in expected}
        return {"score": float(ok), "passed": ok, "detail": f"answer={answer!r}"}
    if kind == "regex":
        answer = extract_answer(final_text)
        ok = re.search(task["pattern"], answer, re.IGNORECASE | re.DOTALL) is not None
        return {"score": float(ok), "passed": ok, "detail": f"answer={answer!r}"}
    if kind == "script":
        return grade_script(task, attempt_dir)
    if kind == "judge":
        return grade_judge(task, attempt_dir, final_text)
    raise ValueError(f"unknown grader {kind!r} in task {task['id']}")


def grade_script(task: dict, attempt_dir: Path) -> dict:
    """verify.sh runs in a network-less container, in the workspace the agent left behind.
    Exit 0 = pass; an optional 'SCORE=<0..1>' line gives partial credit."""
    task_env = {"TASK_DIR": "/task", "ANSWER_FILE": "/attempt/answer.txt"}
    verify = ["docker", "run", "--rm",
              *container_args(task_image(task), [(attempt_dir, "/attempt", "rw"), (task["dir"], "/task", "ro")],
                              "/attempt/workspace", "none", task_env),
              "bash", "/task/verify.sh"]
    try:
        proc = subprocess.run(verify, capture_output=True, text=True, timeout=task.get("verify_timeout_s", 300))
    except subprocess.TimeoutExpired:
        return {"score": 0.0, "passed": False, "detail": "verify.sh timed out"}
    output = (proc.stdout + proc.stderr)[-4000:]
    (attempt_dir / "verify.log").write_text(output)
    scores = re.findall(r"^SCORE=([0-9.]+)\s*$", proc.stdout, re.MULTILINE)
    passed = proc.returncode == 0
    score = min(1.0, float(scores[-1])) if scores else float(passed)
    return {"score": score, "passed": passed, "detail": output.strip().splitlines()[-1] if output.strip() else ""}


JUDGE_TEMPLATE = """You are a strict evaluator. Grade the RESPONSE to the TASK using the RUBRIC.
Reply with a single JSON object and nothing else: {{"score": <integer 0-10>, "reason": "<one or two sentences>"}}

<task>
{task}
</task>

<rubric>
{rubric}
</rubric>

<response>
{response}
</response>
"""


def judge_cfg() -> dict:
    return {"agent": "pi", "model": "openai-codex/gpt-6.1-sol", "thinking": "medium", **CONFIG.get("judge", {})}


def grade_judge(task: dict, attempt_dir: Path, final_text: str) -> dict:
    judge = judge_cfg()
    response = final_text
    if task.get("judge_file"):
        f = attempt_dir / "workspace" / task["judge_file"]
        response = f.read_text() if f.exists() else f"(file {task['judge_file']} was not created)"
    prompt = JUDGE_TEMPLATE.format(task=task["prompt"], rubric=task["rubric"], response=response)
    cmd = agent_command(judge["agent"], judge["model"], judge["thinking"], prompt, tools=[])
    with tempfile.TemporaryDirectory(prefix="benchmax-judge-") as tmp:
        run_agent(judge["agent"], cmd, Path(tmp), sandbox_image(),
                  f"benchmax-{os.getpid()}-judge-{task['id']}-{attempt_dir.name}", 600,
                  attempt_dir / "judge.jsonl", attempt_dir / "judge.stderr")
    stats = parse_events(judge["agent"], attempt_dir / "judge.jsonl")
    m = re.search(r"\{.*\}", stats["final_text"], re.DOTALL)
    try:
        verdict = json.loads(m.group(0)) if m else None
        score10 = float(verdict["score"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"score": None, "passed": None, "detail": f"judge failed: {stats['error'] or stats['final_text'][:200]}",
                "grading_error": True}
    return {"score": score10 / 10, "passed": score10 >= task.get("pass_threshold", 7),
            "detail": f"{score10:g}/10 {verdict.get('reason', '')}", "judge_cost": stats["cost"]}


# ---------------------------------------------------------------- run

def run_attempt(task: dict, rep: int, run_dir: Path, agent: str, model: str, thinking: str | None) -> dict:
    attempt_dir = run_dir / task["id"] / f"r{rep}"
    if attempt_dir.exists():
        shutil.rmtree(attempt_dir)
    attempt_dir.mkdir(parents=True)

    # The agent works on a copy of the task's workspace; hidden tests are never mounted.
    with tempfile.TemporaryDirectory(prefix=f"benchmax-{task['id']}-") as tmp:
        work = Path(tmp) / "workspace"
        src = task["dir"] / "workspace"
        shutil.copytree(src, work) if src.exists() else work.mkdir()
        cmd = agent_command(agent, model, task.get("thinking", thinking), task_prompt(task), task.get("tools"))
        code, seconds = run_agent(agent, cmd, work, task_image(task), f"benchmax-{os.getpid()}-{task['id']}-r{rep}",
                                  task["timeout_s"], attempt_dir / "events.jsonl", attempt_dir / "stderr.txt")
        shutil.copytree(work, attempt_dir / "workspace", symlinks=True)

    stats = parse_events(agent, attempt_dir / "events.jsonl")
    (attempt_dir / "answer.txt").write_text(stats["final_text"])
    result = {"task": task["id"], "category": task["category"], "rep": rep, "seconds": round(seconds, 1),
              **{k: v for k, v in stats.items() if k != "final_text"}}

    if code is None:
        result.update(status="timeout", score=0.0, passed=False)
    elif stats["error"] or code != 0:
        # Provider/harness failure (quota, network, crash): not the model's fault, excluded from scores.
        result.update(status="error", score=None, passed=None,
                      error=stats["error"] or (attempt_dir / "stderr.txt").read_text()[-500:])
    else:
        g = grade(task, attempt_dir, stats["final_text"])
        result.update(status="error" if g.pop("grading_error", False) else "done", **g)
    (attempt_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def preflight(agent: str, model: str, thinking: str | None, run_dir: Path) -> None:
    """One tiny request before the parallel attempts: catches a missing login or a wrong model name
    early, and refreshes the login once instead of in every attempt at the same time."""
    out_dir = run_dir / "preflight"
    out_dir.mkdir()
    cmd = agent_command(agent, model, thinking, "Reply with the single word OK.", tools=[])
    with tempfile.TemporaryDirectory(prefix="benchmax-preflight-") as tmp:
        code, _ = run_agent(agent, cmd, Path(tmp), sandbox_image(), f"benchmax-{os.getpid()}-preflight", 300,
                            out_dir / "events.jsonl", out_dir / "stderr.txt")
    stats = parse_events(agent, out_dir / "events.jsonl")
    if code is None or stats["error"] or code != 0:
        detail = stats["error"] or (out_dir / "stderr.txt").read_text()[-1000:] or f"exit code {code}"
        sys.exit(f"preflight failed for {agent} / {model}: {detail.strip()}\n"
                 f"If you are not logged in: python3 bench.py login {agent}")


def cmd_run(args):
    tasks = select_tasks(load_tasks(), args.only, args.category)
    if not tasks:
        sys.exit("no tasks selected")
    judge = judge_cfg()
    if (judge["agent"], judge["model"]) == (args.agent, args.model) and any(t["grader"] == "judge" for t in tasks):
        print("warning: judge model == model under test; judge scores will be biased", file=sys.stderr)

    remove_stale_containers()
    ensure_auth_volume()
    image = sandbox_image()
    # Build task images up front, and record the ones that differ from the default sandbox.
    task_images = {t["id"]: task_image(t) for t in tasks}

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    label = args.label or re.sub(r"[^\w.-]+", "_", f"{args.agent}_{args.model}_{args.thinking or 'default'}")
    run_dir = RESULTS_DIR / f"{stamp}_{label}"
    run_dir.mkdir(parents=True)
    meta = {"rules": CONFIG.get("prompt", {}).get("rules", DEFAULT_RULES), "agent": args.agent, "model": args.model, "thinking": args.thinking, "repeats": args.repeats,
            "started": stamp, "sandbox": image, "tasks": [t["id"] for t in tasks], "judge": judge}
    if any(v != image for v in task_images.values()):
        meta["task_images"] = {k: v for k, v in task_images.items() if v != image}
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))

    preflight(args.agent, args.model, args.thinking, run_dir)

    jobs = [(t, r) for t in tasks for r in range(1, args.repeats + 1)]
    print(f"{len(jobs)} attempts -> {run_dir.relative_to(ROOT)}")

    def work(job):
        task, rep = job
        res = run_attempt(task, rep, run_dir, args.agent, args.model, args.thinking)
        mark = {True: "PASS", False: "FAIL", None: res["status"].upper()}[res["passed"]]
        cost = "" if res["cost"] is None else f"${res['cost']:.4f}"
        print(f"  {mark:7} {task['id']} r{rep}  {res['seconds']}s  {cost}  {res.get('detail') or res.get('error') or ''}"[:200],
              flush=True)
        return res

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(work, jobs))
    write_results(run_dir, results)
    report([run_dir])


def cmd_login(args):
    """Interactive login inside the sandbox image, saved to the auth volume. Uses the host network so
    the browser's OAuth redirect to localhost reaches the CLI."""
    ensure_auth_volume()
    cmd = LOGIN_COMMANDS[args.agent]() + args.extra
    os.execvp("docker", ["docker", "run", "--rm", "-it", "--network", "host",
                         "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{AUTH_VOLUME}:/auth",
                         *[a for k, v in AGENT_ENV.items() for a in ("-e", f"{k}={v}")],
                         sandbox_image(), *cmd])


def write_results(run_dir: Path, results: list[dict]):
    with open(run_dir / "results.jsonl", "w") as f:
        for r in sorted(results, key=lambda r: (r["task"], r["rep"])):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def cmd_regrade(args):
    tasks = {t["id"]: t for t in load_tasks()}
    run_dir = Path(args.run).resolve()
    results = []
    for path in sorted(run_dir.glob("*/r*/result.json")):
        res = json.loads(path.read_text())
        if res["status"] == "done" or res.get("grading_error"):
            g = grade(tasks[res["task"]], path.parent, (path.parent / "answer.txt").read_text())
            res.update(status="error" if g.pop("grading_error", False) else "done", **g)
            path.write_text(json.dumps(res, indent=2, ensure_ascii=False))
        results.append(res)
    write_results(run_dir, results)
    report([run_dir])


# ---------------------------------------------------------------- report

def derive_prices(runs: list[dict]) -> dict[str, tuple]:
    """Per-token prices (input, cache read, output) of each model, fitted by least squares to the costs
    pi reported for it, so agents that report no cost (Codex) can be priced from the same model's pi runs."""
    rows: dict[str, list] = {}
    for run in runs:
        if run["agent"] != "pi":
            continue
        for a in run["attempts"]:
            if a.get("cost") is not None and a.get("input") is not None:
                rows.setdefault(run["model"].split("/")[-1], []).append(
                    ([a["input"], a.get("cache_read", 0) or 0, a.get("output", 0)], a["cost"]))
    prices = {}
    for model, data in rows.items():
        # Normal equations X'X p = X'y, solved by Gaussian elimination.
        m = [[sum(x[i] * x[j] for x, _ in data) for j in range(3)] + [sum(x[i] * y for x, y in data)] for i in range(3)]
        try:
            for i in range(3):
                piv = max(range(i, 3), key=lambda r: abs(m[r][i]))
                m[i], m[piv] = m[piv], m[i]
                m[i] = [v / m[i][i] for v in m[i]]
                for r in range(3):
                    if r != i:
                        m[r] = [v - m[r][i] * w for v, w in zip(m[r], m[i])]
        except ZeroDivisionError:
            continue
        p = tuple(m[i][3] for i in range(3))
        if min(p) >= 0:
            prices[model] = p
    return prices


def estimate_costs(runs: list[dict]):
    """Fill in missing costs from prices derived from pi runs. Codex's input tokens include the cached
    ones, so those are billed at the cache-read rate instead."""
    prices = derive_prices(runs)
    for run in runs:
        price = prices.get(run["model"].split("/")[-1])
        if not price:
            continue
        for a in run["attempts"]:
            if a.get("cost") is None and a["status"] in ("done", "timeout"):
                cached = a.get("cache_read", 0) or 0
                fresh = max(a.get("input", 0) - cached, 0) if run["agent"] == "codex" else a.get("input", 0)
                a["cost"] = fresh * price[0] + cached * price[1] + a.get("output", 0) * price[2]


def collect_run(run_dir: Path) -> dict:
    """A run's metadata and every attempt, read from the per-attempt result files, so runs that are
    still going can be reported too (attempts without a result are 'running' or 'pending')."""
    meta = json.loads((run_dir / "run.json").read_text())
    categories = {t["id"]: t["category"] for t in load_tasks()}
    attempts, last = [], 0.0
    for task_id in meta.get("tasks", []):
        if task_id not in categories:  # task since removed: don't let old runs count it
            continue
        for rep in range(1, meta.get("repeats", 1) + 1):
            adir = run_dir / task_id / f"r{rep}"
            path = adir / "result.json"
            if path.exists():
                res = json.loads(path.read_text())
                last = max(last, path.stat().st_mtime)
            else:
                res = {"task": task_id, "rep": rep, "status": "running" if adir.exists() else "pending",
                       "score": None, "passed": None}
            res.setdefault("category", categories.get(task_id, "?"))
            res["path"] = str(adir.relative_to(RESULTS_DIR)) if adir.is_relative_to(RESULTS_DIR) else str(adir)
            attempts.append(res)
    started = dt.datetime.strptime(meta["started"], "%Y%m%d-%H%M%S")
    done = all(a["status"] not in ("running", "pending") for a in attempts)
    return {
        "id": run_dir.name, "agent": meta.get("agent", "pi"), "model": meta.get("model", "?"),
        "thinking": meta.get("thinking"), "repeats": meta.get("repeats", 1), "started": started.isoformat(),
        "wall_seconds": round(last - started.timestamp()) if last else None,
        "complete": done, "sandbox": meta.get("sandbox"), "attempts": attempts,
    }


def summarize(attempts: list[dict]) -> dict:
    graded = [a for a in attempts if a.get("score") is not None]
    by_task: dict[str, list] = {}
    for a in graded:
        by_task.setdefault(a["task"], []).append(a)
    costs = [a["cost"] for a in attempts if a.get("cost") is not None]
    times = sorted(a["seconds"] for a in attempts if a.get("seconds") is not None)
    model_times = sorted(a["model_seconds"] for a in attempts if a.get("model_seconds") is not None)
    return {
        "score": sum(a["score"] for a in graded) / len(graded) if graded else float("nan"),
        "pass": sum(bool(a["passed"]) for a in graded) / len(graded) if graded else float("nan"),
        # Tasks passed on every attempt: a measure of reliability, not just capability.
        "stable": sum(all(a["passed"] for a in rs) for rs in by_task.values()),
        "tasks": len(by_task),
        "timeouts": sum(a["status"] == "timeout" for a in attempts),
        "errors": sum(a["status"] == "error" for a in attempts),
        "unfinished": sum(a["status"] in ("running", "pending") for a in attempts),
        "cost": sum(costs) if costs else None,  # Codex reports tokens but no cost
        "input": sum(a.get("input", 0) for a in attempts),
        "output": sum(a.get("output", 0) for a in attempts),
        "median_s": times[len(times) // 2] if times else None,
        "median_model_s": model_times[len(model_times) // 2] if model_times else None,
    }


def pct(x: float, width: int) -> str:
    return f"{'-':>{width}}" if x != x else f"{x:>{width}.0%}"


def run_name(run: dict) -> str:
    return f"{run['agent']}:{run['model'].split('/')[-1]}:{run['thinking'] or '-'}"


def fmt_time(seconds) -> str:
    if seconds is None:
        return "-"
    return f"{seconds:.0f}s" if seconds < 60 else f"{seconds / 60:.1f}m" if seconds < 3600 else f"{seconds / 3600:.1f}h"


def ranked_runs(run_dirs: list[Path]) -> tuple[list[dict], dict]:
    """Runs with costs filled in, best first, and their summaries by run id."""
    runs = [collect_run(d) for d in run_dirs if (d / "run.json").exists()]
    if not runs:
        sys.exit("no runs found")
    estimate_costs(runs)
    summaries = {r["id"]: summarize(r["attempts"]) for r in runs}
    # Best first: score, then pass rate, then faster, then fewer tokens, then cheaper. Runs with nothing
    # graded yet go last, and missing times or costs rank after real ones.
    def rank(r):
        s = summaries[r["id"]]
        graded = s["score"] == s["score"]
        inf = float("inf")
        return (not graded, -s["score"] if graded else 0, -s["pass"] if graded else 0,
                s["median_s"] if s["median_s"] is not None else inf, s["input"] + s["output"],
                s["cost"] if s["cost"] is not None else inf)
    runs.sort(key=rank)
    return runs, summaries


def report(run_dirs: list[Path]):
    runs, summaries = ranked_runs(run_dirs)
    width = max(len(run_name(r)) for r in runs) + 2

    print(f"\n{'#':>2}  {'agent:model:thinking':{width}} {'score':>6} {'pass':>6} {'stable':>7} {'t/o':>4} {'err':>4}"
          f" {'cost$':>8} {'$/pass':>7} {'input':>11} {'output':>10} {'median':>7} {'model':>7} {'wall':>6}  started")
    for i, run in enumerate(runs, 1):
        s = summaries[run["id"]]
        passed = sum(bool(a["passed"]) for a in run["attempts"])
        cost = f"{'-':>8}" if s["cost"] is None else f"{s['cost']:8.3f}"
        per_pass = f"{'-':>7}" if s["cost"] is None or not passed else f"{s['cost'] / passed:7.3f}"
        started = run["started"][:16].replace("T", " ")
        if not run["complete"]:
            started += f"  ({s['unfinished']} unfinished)"
        print(f"{i:>2}  {run_name(run):{width}} {pct(s['score'], 6)} {pct(s['pass'], 6)} {s['stable']:>3}/{s['tasks']:<3}"
              f" {s['timeouts']:>4} {s['errors']:>4} {cost} {per_pass} {s['input']:>11,} {s['output']:>10,}"
              f" {fmt_time(s['median_s']):>7} {fmt_time(s['median_model_s']):>7} {fmt_time(run['wall_seconds']):>6}  {started}")

    cols = "".join(f"{'#' + str(i):>11}" for i in range(1, len(runs) + 1))
    categories = sorted({a.get("category") or "?" for r in runs for a in r["attempts"]})
    if len(categories) > 1:
        print(f"\n{'category (avg score)':34}{cols}")
        for cat in categories:
            cells = "".join(pct(summarize([a for a in r["attempts"] if (a.get("category") or "?") == cat])["score"], 11)
                            for r in runs)
            print(f"{cat:34}{cells}")

    # Per task: passed/graded per run, plus what went wrong; the last column is how hard the task is
    # across all runs shown.
    print(f"\n{'task (passed/graded)':34}{cols}{'all runs':>11}")
    task_ids = sorted({a["task"] for r in runs for a in r["attempts"]})
    for tid in task_ids:
        cells, total_pass, total_graded = "", 0, 0
        for run in runs:
            rs = [a for a in run["attempts"] if a["task"] == tid]
            graded = [a for a in rs if a["passed"] is not None]
            npass = sum(bool(a["passed"]) for a in graded)
            total_pass, total_graded = total_pass + npass, total_graded + len(graded)
            mark = "".join(sorted({{"timeout": "T", "error": "E", "running": "~", "pending": "~"}.get(a["status"], "")
                                   for a in rs}))
            cell = (f"{npass}/{len(graded)}" if graded else "-") + (f" {mark}" if mark else "")
            cells += f"{cell if rs else '':>11}"
        print(f"{tid[:34]:34}{cells}{pct(total_pass / total_graded if total_graded else float('nan'), 11)}")

    print("\nT = timed out, E = infrastructure error (not scored), ~ = still running."
          " median = median time per attempt, model = median of that spent in the model (not tools),"
          " wall = run duration.")
    for i, run in enumerate(runs, 1):
        print(f"#{i}: {run['id']}")


def cmd_report(args):
    dirs = [Path(p).resolve() for p in args.runs] or sorted(p for p in RESULTS_DIR.glob("*") if p.is_dir())
    report(dirs)
    if args.task:
        attempt_details(dirs, args.task)


TSV_FILE = ROOT / "results.tsv"
TABLE_MARKERS = ("<!-- results:start -->", "<!-- results:end -->")


TSV_COLUMNS = ["run", "agent", "model", "thinking", "started", "attempts", "score", "pass", "stable", "tasks",
               "timeouts", "errors", "cost_usd", "input_tokens", "output_tokens", "median_s", "median_model_s",
               "wall_s"]


def cmd_export(args):
    """Merge a summary of every finished local run into results.tsv (kept in git, unlike results/) and refresh
    the README table. Rows already in the file, e.g. from other machines, are kept; a local run replaces
    its own row."""
    rows: dict[str, dict] = {}
    if TSV_FILE.exists():
        lines = TSV_FILE.read_text().splitlines()
        for line in lines[1:]:
            row = dict(zip(lines[0].split("\t"), line.split("\t")))
            if row.get("run"):
                rows[row["run"]] = row
    kept = len(rows)
    dirs = sorted(p for p in RESULTS_DIR.glob("*") if p.is_dir() and (p / "run.json").exists())
    if dirs:
        runs, summaries = ranked_runs(dirs)
        for r in runs:
            if not r["complete"]:
                continue
            s = summaries[r["id"]]
            if s["score"] != s["score"]:
                continue
            cells = [r["id"], r["agent"], r["model"], r["thinking"] or "", r["started"], len(r["attempts"]),
                     f"{s['score']:.4f}", f"{s['pass']:.4f}", s["stable"], s["tasks"], s["timeouts"], s["errors"],
                     "" if s["cost"] is None else f"{s['cost']:.4f}", s["input"], s["output"],
                     "" if s["median_s"] is None else s["median_s"],
                     "" if s["median_model_s"] is None else s["median_model_s"],
                     "" if r["wall_seconds"] is None else r["wall_seconds"]]
            rows[r["id"]] = dict(zip(TSV_COLUMNS, map(str, cells)))
    if not rows:
        sys.exit("no runs to export")

    def num(row, key, default=float("inf")):
        return float(row[key]) if row.get(key) else default
    # Same order as the report: score, pass rate, faster, fewer tokens, cheaper.
    ordered = sorted(rows.values(), key=lambda x: (-num(x, "score"), -num(x, "pass"), num(x, "median_s"),
                                                   num(x, "input_tokens", 0) + num(x, "output_tokens", 0),
                                                   num(x, "cost_usd"), x["run"]))
    TSV_FILE.write_text("\n".join(["\t".join(TSV_COLUMNS)] + ["\t".join(x.get(c, "") for c in TSV_COLUMNS)
                                                              for x in ordered]) + "\n")
    print(f"wrote {len(ordered)} runs to {TSV_FILE.relative_to(ROOT)} ({kept} were already there)")

    table = ["| # | Agent : model : thinking | Score | Pass | Stable | Timeouts | Cost | Median time |",
             "|--:|---|--:|--:|--:|--:|--:|--:|"]
    for i, x in enumerate(ordered, 1):
        name = f"{x['agent']}:{x['model'].split('/')[-1]}:{x['thinking'] or '-'}"
        cost = f"${float(x['cost_usd']):.2f}" if x["cost_usd"] else "-"
        median = fmt_time(float(x["median_s"])) if x["median_s"] else "-"
        table.append(f"| {i} | {name} | {float(x['score']):.0%} | {float(x['pass']):.0%} | {x['stable']}/{x['tasks']} |"
                     f" {x['timeouts']} | {cost} | {median} |")
    readme = ROOT / "README.md"
    text = readme.read_text()
    start, end = (text.find(m) for m in TABLE_MARKERS)
    if start == -1 or end == -1:
        print(f"README.md has no {TABLE_MARKERS[0]} ... {TABLE_MARKERS[1]} block; table not updated")
        return
    readme.write_text(text[:start + len(TABLE_MARKERS[0])] + "\n" + "\n".join(table) + "\n" + text[end:])
    print("updated the comparison table in README.md")


def attempt_details(run_dirs: list[Path], pattern: str):
    """Every attempt of the matching tasks: status, answer or error, time, cost, tokens, tool calls."""
    for d in run_dirs:
        run = collect_run(d)
        for a in run["attempts"]:
            if not fnmatch.fnmatch(a["task"], pattern):
                continue
            mark = {True: "PASS", False: "FAIL", None: a["status"].upper()}[a.get("passed")]
            cost = "" if a.get("cost") is None else f"${a['cost']:.3f}"
            what = a.get("detail") or (a.get("error") or "").strip().splitlines()[-1:] or [""]
            what = what if isinstance(what, str) else what[0]
            print(f"  {mark:7} {run_name(run)[:30]:30} {a['task'][:28]:28} r{a['rep']}"
                  f" {fmt_time(a.get('seconds')):>6} {fmt_time(a.get('model_seconds')):>6} {cost:>7} {a.get('input', 0) + a.get('output', 0):>9,} tok"
                  f" {a.get('tool_calls', 0):>4} tools  {what[:70]}")


def cmd_list(_args):
    for t in load_tasks():
        note = "  (no answer yet)" if missing_answer(t) else ""
        print(f"{t['category']:14} {t['id']:32} {t['grader']:8} {t.get('difficulty', '')}{note}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    lg = sub.add_parser("login", help="log an agent in, inside the sandbox image")
    lg.add_argument("agent", choices=sorted(LOGIN_COMMANDS))
    lg.add_argument("extra", nargs=argparse.REMAINDER, help="extra arguments for the login command")
    lg.set_defaults(fn=cmd_login)
    r = sub.add_parser("run")
    r.add_argument("-a", "--agent", default="pi", choices=["pi", "codex", "claude"], help="agent harness (default pi)")
    r.add_argument("-m", "--model", required=True,
                   help="model as the agent names it: provider/model for pi, e.g. gpt-6.1-sol for codex")
    r.add_argument("-t", "--thinking", help="thinking/effort level, passed to the agent as is")
    r.add_argument("-k", "--repeats", type=int, default=1)
    r.add_argument("-j", "--jobs", type=int, default=1)
    r.add_argument("--only", help="comma-separated task id globs")
    r.add_argument("--category", help="comma-separated categories")
    r.add_argument("--label", help="name for the results folder")
    r.set_defaults(fn=cmd_run)
    g = sub.add_parser("regrade")
    g.add_argument("run")
    g.set_defaults(fn=cmd_regrade)
    sub.add_parser("export", help="save run summaries to results.tsv and the README table").set_defaults(fn=cmd_export)
    rep = sub.add_parser("report")
    rep.add_argument("runs", nargs="*")
    rep.add_argument("--task", help="also list every attempt of tasks matching this glob")
    rep.set_defaults(fn=cmd_report)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
