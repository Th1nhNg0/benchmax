# benchmax

A small, personal benchmark for evaluating new AI models on **your own tasks**, using the
[pi](https://github.com/earendil-works/pi), [Codex](https://github.com/openai/codex) or
[Claude Code](https://github.com/anthropics/claude-code) agents.

Public leaderboards are useful, but they are gamed, contaminated, and rarely measure the work you
actually do. benchmax runs every model through the same agent harness, in the same sandbox, on
tasks you choose, and reports how often each model succeeds, how reliably, and at what cost.

```
 #  agent:model:thinking            score   pass  stable  t/o  err    cost$  $/pass      tokens  median   wall
 1  pi:gpt-6.1-sol:medium             81%    78%   4/6      1    0    1.787   0.128     388,170    4.3m   1.0h
 2  pi:gpt-6-luna:max                 46%    44%   1/6      7    0    0.570   0.071   1,894,461   58.3m   2.3h

task (passed/graded)                    #1         #2   all runs
euler-1001-connectivity                3/3        3/3       100%
euler-986-token-game                   3/3      0/3 T        50%
euler-995-poly-divisibility          2/3 T      1/3 T        50%
```

## Features

- **Three agent harnesses.** Run models through pi (any provider it supports), Codex, or Claude
  Code. Each agent is stripped of user configuration (instructions files, skills, MCP servers,
  plugins), so you compare models and harnesses, not personal setups.
- **Docker sandbox.** Every attempt runs in a fresh container: the agent and all its tools inside,
  only the task workspace mounted, and limited CPU, memory and processes.
- **Shared rules.** Every prompt ends with the same rules (`[prompt] rules`), e.g. not to look up
  answers online. Agents otherwise keep their default tools and internet access.
- **Separate logins.** The agents log in once into a Docker volume, independent of your everyday
  pi, Codex and Claude logins.
- **Four graders.** `script` (hidden test script, with partial credit), `exact`, `regex`, and
  `judge` (rubric scored by another model).
- **Repeats and reliability.** Run each task *k* times; the report shows the average score, pass
  rate, and how many tasks passed on *every* attempt.
- **Infrastructure errors are not failures.** Quota, rate-limit and network errors are reported
  separately and excluded from scores. A one-request preflight catches a missing login or a wrong
  model name before any attempt starts.
- **Traceable results.** Each run records the agent, model, thinking level, and sandbox image tag
  (which pins the agent versions), and keeps the full event log, final answer, and workspace of
  every attempt.
- **No dependencies** beyond Python 3.11+ and Docker.

## Requirements

- Python 3.11+
- Docker (Docker Engine on Linux, Docker Desktop or OrbStack on macOS)

The agents themselves are installed in the sandbox image, so they don't need to be installed on
your machine.

## Quick start

```bash
git clone https://github.com/Th1nhNg0/benchmax.git
cd benchmax
python3 bench.py login pi            # once per agent you want to use: pi, codex, claude
python3 bench.py run -m openai-codex/gpt-6-luna -t high -k 3 -j 3
python3 bench.py report
```

The sandbox image is built automatically on first use.

### Logging in

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

`report` ranks runs by score, works on runs that are still going, and adds a task matrix
(passed/graded per run, with `T` for timeouts, `E` for errors, and an "all runs" column showing
how hard each task is). `report --task 'euler-9*'` also lists every attempt of those tasks.

| Column | Meaning |
|---|---|
| `score` | Mean score across graded attempts, including partial credit |
| `pass` | Fraction of graded attempts that passed |
| `stable` | Tasks that passed on every attempt, a measure of reliability |
| `t/o` | Attempts that hit the task's time limit (scored as failures) |
| `err` | Attempts that failed for infrastructure reasons, excluded from scores |
| `cost$`, `$/pass` | Total cost and cost per passed attempt (`-` when the agent reports no cost, e.g. Codex) |
| `median`, `wall` | Median time per attempt, and how long the whole run took |

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

### Graders

| Grader | How it works |
|---|---|
| `script` | `verify.sh` runs in the workspace after the agent finishes. Exit code 0 means pass. Print `SCORE=<0..1>` for partial credit. `$TASK_DIR` points to the task folder, for reading original data. |
| `exact` | Compares the last `ANSWER: ...` line of the reply with `expected`, ignoring case and surrounding whitespace. The prompt must ask for that line. |
| `regex` | Matches the last `ANSWER: ...` line against `pattern`. |
| `judge` | The judge model from `benchmax.toml` scores the reply (or `judge_file`) from 0 to 10 against `rubric`. Use it only when automated checks are impossible. |

### Task images

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

### Writing good tasks

- Take tasks from your real work: bugs you fixed, features you shipped, analyses you ran.
- Prefer automated checks (`script`, `exact`) over `judge`.
- Keep some tasks that no current model can solve, so new models have room to show progress.
- Aim for at least 5 to 10 tasks per category, so category averages aren't dominated by one task.
- Changing a task or the sandbox image (which pins the agent versions) makes older runs not
  directly comparable.

## Sandbox

- Each attempt runs the agent CLI in a fresh container from [`sandbox/Dockerfile`](sandbox/Dockerfile)
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

Settings live in [`benchmax.toml`](benchmax.toml):

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
    result.json               status, score, tokens, cost, tool calls, time
    workspace/                files as the agent left them
    verify.log | judge.jsonl  grader output
```

`results/` is git-ignored.

## Attribution

The tasks in `tasks/algorithms/euler-*` are taken from [Project Euler](https://projecteuler.net)
and used under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Reference
answers are from [lucky-bai/projecteuler-solutions](https://github.com/lucky-bai/projecteuler-solutions).

## License

The code is released under the [MIT License](LICENSE). Task content keeps its original license (see
[Attribution](#attribution)).
