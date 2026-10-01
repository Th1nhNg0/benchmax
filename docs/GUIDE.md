# benchmax guide

Details for [benchmax](../README.md): logging in, commands, writing tasks, the sandbox, configuration and the results layout.

## Logging in

`login` opens the agent's own login inside the sandbox image and saves it to the `benchmax-auth`
Docker volume:

| Agent | Command | What to do |
|---|---|---|
| pi | `python3 bench.py login pi` | Type `/login`, pick the provider, then `/quit` |
| Codex | `python3 bench.py login codex` | Open the printed link. Add `--device-auth` for a code-based login |
| Claude Code | `python3 bench.py login claude` | Open the printed link and paste the code back |

These are separate sessions from your everyday logins. Copying your existing credentials instead
is not recommended: some providers rotate refresh tokens, so two copies of one login can log each
other out.

## Usage

```bash
python3 bench.py list                                   # list tasks
python3 bench.py login pi|codex|claude                  # log an agent in (once)
python3 bench.py run -m <model> [options]               # run a model on the tasks
python3 bench.py report [results/<run> ...] [--task G]  # compare runs (default: all runs)
python3 bench.py regrade results/<run>                  # re-grade after fixing a verifier or rubric
```

`run` options:

| Option | Description |
|---|---|
| `-a, --agent` | `pi` (default), `codex`, or `claude` |
| `-m, --model` | Model as the agent names it: `antigravity/gemini-3.8-flash` for pi, `gpt-6.1-sol` for Codex, `claude-opus-5-5` for Claude Code |
| `-t, --thinking` | Thinking level, passed to the agent as is: pi `--thinking`, Codex `model_reasoning_effort`, Claude Code `--effort` |
| `-k, --repeats` | Attempts per task (default 1) |
| `-j, --jobs` | Attempts to run in parallel (default 1) |
| `--only` | Comma-separated task id globs, e.g. `euler-9*` |
| `--category` | Comma-separated categories |
| `--label` | Name for the results folder |

`report` ranks runs by score, then pass rate, median time, tokens and cost, works on runs that are still going, and adds a task matrix
(passed/graded per run, with `T` for timeouts, `E` for errors, and an "all runs" column showing
how hard each task is). `report --task 'euler-9*'` also lists every attempt of those tasks.

| Column | Meaning |
|---|---|
| `score` | Mean score across graded attempts, including partial credit |
| `pass` | Fraction of graded attempts that passed |
| `stable` | Tasks that passed on every attempt, a measure of reliability |
| `t/o` | Attempts that hit the task's time limit (scored as failures) |
| `err` | Attempts that failed for infrastructure reasons, excluded from scores |
| `cost$`, `$/pass` | Total cost and cost per passed attempt. Codex reports no cost, so it is estimated from the prices pi reported for the same model (`-` if there is no pi run of that model) |
| `input`, `output` | Total input and output tokens |
| `median`, `model`, `wall` | Median time per attempt, the median part of it spent in the model rather than running tools, and how long the whole run took |

## Tasks

Tasks live in `tasks/<category>/<task-id>/`. The folder names are the category and the task id, and
task ids must be unique across the whole benchmark.

```
tasks/<category>/<task-id>/
  task.toml      metadata and grading
  prompt.md      the prompt given to the agent
  workspace/     (optional) initial files, copied into a fresh directory for each attempt
  verify.sh      (grader = "script") hidden checks; never visible to the agent
  image/         (optional) Dockerfile for a task-specific sandbox image, plus its build files
```

`task.toml`:

```toml
difficulty = "medium"
timeout_s = 900
thinking = "high"     # optional: override the thinking level for this task
tools = []            # optional: [] disables all tools; ["read", "bash"] allows only these (agent's tool names)
image = "..."         # optional: custom sandbox image for this task
verify_timeout_s = 300  # optional: time limit for verify.sh

grader = "script"     # script | exact | regex | judge
# exact:  expected = "401"   or   expected = ["401", "four hundred and one"]
# regex:  pattern = "^4\\d\\d$"
# judge:  rubric = """..."""   judge_file = "OUT.md"   pass_threshold = 7
```

## Graders

| Grader | How it works |
|---|---|
| `script` | `verify.sh` runs in the workspace after the agent finishes. Exit code 0 means pass. Print `SCORE=<0..1>` for partial credit. `$TASK_DIR` points to the task folder, for reading original data. |
| `exact` | Compares the last `ANSWER: ...` line of the reply with `expected`, ignoring case and surrounding whitespace. The prompt must ask for that line. |
| `regex` | Matches the last `ANSWER: ...` line against `pattern`. |
| `judge` | The judge model from `benchmax.toml` scores the reply (or `judge_file`) from 0 to 10 against `rubric`. Use it only when automated checks are impossible. |

## Task images

A task that needs extra tools (a game referee, a database, another language) can ship an
`image/Dockerfile`. It is built on top of the default sandbox image, which is passed in as the
`BASE` build argument:

```dockerfile
ARG BASE
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless
```

The image is tagged by the hash of the `image/` folder and the base image, built on first use, and
used both for the agent's container and for `verify.sh`. Run metadata records it under `task_images`.

Tasks with an `exact` or `regex` grader but no answer yet are listed as `(no answer yet)` and
skipped.

## Writing good tasks

- Take tasks from your real work: bugs you fixed, features you shipped, analyses you ran.
- Prefer automated checks (`script`, `exact`) over `judge`.
- Keep some tasks that no current model can solve, so new models have room to show progress.
- Aim for at least 5 to 10 tasks per category, so category averages aren't dominated by one task.
- Changing a task or the sandbox image (which pins the agent versions) makes older runs not
  directly comparable.

## Sandbox

- Each attempt runs the agent CLI in a fresh container from [`sandbox/Dockerfile`](../sandbox/Dockerfile)
  (Python 3.12, gcc, numpy, sympy, pandas, networkx, Node, and pinned versions of pi, Codex and
  Claude Code). The image is tagged by the Dockerfile's hash and recorded in each run's `run.json`.
- Only a copy of the task's workspace is mounted, at `/workspace`. Hidden tests are never mounted.
- The container has normal internet access, and the agents keep their default tools, including
  web search. The `[prompt] rules` added to every prompt tell the model not to look answers up or
  cheat; nothing enforces it. Check `events.jsonl` if a result looks too good.
- Codex's ChatGPT account connectors (Gmail, GitHub, Drive, ...) and plugins are always disabled,
  so an attempt can't act on your accounts.
- `verify.sh` runs in a fresh container with no network at all.
- Containers left behind by a killed run are removed at the start of the next run.

Limitations:

- The agent can read its login tokens in `/auth`. Keep the benchmark logins separate from accounts
  you care about more.
- Answers that are published online (such as Project Euler's) can be found by a model that ignores
  the rules.
- Results from different agents compare models *and* harnesses. Compare models within one agent.

## Configuration

Settings live in [`benchmax.toml`](../benchmax.toml):

| Section | Settings |
|---|---|
| `[agents.pi]` | `extensions` (provider extensions inside the image, e.g. antigravity), `tools` (pinned tool set) |
| `[agents.codex]` | `config` (extra `-c key=value` overrides) |
| `[agents.claude]` | `disallowed_tools` (e.g. `["WebSearch", "WebFetch"]`; default none) |
| `[prompt]` | `rules` (added to the end of every task prompt) |
| `[judge]` | `agent`, `model` and `thinking` for the `judge` grader |
| `[sandbox]` | `cpus`, `memory`, `pids`, `image` |

## Results

```
results/<timestamp>_<label>/
  run.json                    agent, model, thinking level, sandbox image, task list
  preflight/                  the one-request login check
  results.jsonl               one line per attempt
  <task-id>/r<n>/
    events.jsonl              the agent's full JSON event stream
    answer.txt                final reply
    result.json               status, score, tokens, cost, tool calls, time (total and model)
    workspace/                files as the agent left them
    verify.log | judge.jsonl  grader output
```
