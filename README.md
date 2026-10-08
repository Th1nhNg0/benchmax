# benchmax

A small, personal benchmark for evaluating new AI models on **your own tasks**, using the
[pi](https://github.com/earendil-works/pi), [Codex](https://github.com/openai/codex) or
[Claude Code](https://github.com/anthropics/claude-code) agents.

Public leaderboards are useful, but they are gamed, contaminated, and rarely measure the work you
actually do. benchmax runs every model through the same agent harness, in the same sandbox, on
tasks you choose, and reports how often each model succeeds, how reliably, and at what cost.

```
 #  agent:model:thinking            score   pass  stable  t/o  err    cost$  $/pass       input     output  median   wall
 1  pi:gpt-6.1-sol:medium             81%    78%   4/6      1    0    1.787   0.128     350,170     38,000    4.3m   1.0h
 2  pi:gpt-6-luna:max                 46%    44%   1/6      7    0    0.570   0.071   1,700,461    194,000   58.3m   2.3h

task (passed/graded)                    #1         #2   all runs
euler-1001-connectivity                3/3        3/3       100%
euler-986-token-game                   3/3      0/3 T        50%
euler-995-poly-divisibility          2/3 T      1/3 T        50%
```

## Results so far

All models run on the same tasks, 3 attempts each. Ranked by score, then pass rate, time, tokens and
cost. Full numbers are in [`results.tsv`](results.tsv); refresh both with `python3 bench.py export`.

<!-- results:start -->
| # | Agent : model : thinking | Score | Cost | Requests | Input tokens | Output tokens | Median time | Total time |
|--:|---|--:|--:|--:|--:|--:|--:|--:|
| 1 | claude:claude-opus-5-5:medium | 100% | $1.01 | 37 | 69,886 | 19,687 | 13s | 6.3m |
| 2 | claude:claude-sonnet-5-5:xhigh | 100% | $0.60 | 44 | 44,002 | 34,817 | 13s | 6.6m |
| 3 | claude:claude-haiku-5-5:medium | 100% | $3.85 | 52 | 134,644 | 129,122 | 32s | 17.7m |
| 4 | claude:claude-haiku-5-5:max | 100% | $7.84 | 85 | 258,416 | 267,805 | 1.0m | 22.5m |
| 5 | pi:gpt-6.1-sol:medium | 100% | $0.54 | 76 | 105,140 | 32,524 | 1.4m | 32.1m |
| 6 | codex:gpt-6.1-sol:medium | 100% | $0.96 | 18 | 967,555 | 32,517 | 1.6m | 31.9m |
| 7 | claude:claude-sonnet-5-5:medium | 94% | $0.69 | 38 | 102,542 | 23,098 | 18s | 10.8m |
| 8 | pi:gemini-3.8-flash:high | 94% | $0.40 | 277 | 2,210,324 | 260,516 | 1.7m | 35.2m |
| 9 | pi:gpt-6-luna:max | 78% | $0.06 | 62 | 154,247 | 85,055 | 1.4m | 40.4m |
| 10 | pi:deepseek-v4.1-flash:max | 72% | $0.21 | 125 | 283,023 | 270,786 | 1.7m | 43.0m |
<!-- results:end -->

## Features

- **Three agent harnesses:** pi (any provider), Codex and Claude Code, stripped of user config.
- **Docker sandbox:** every attempt runs in a fresh container with only the task workspace mounted.
- **Four graders:** hidden script (with partial credit), exact, regex, or a judge model.
- **Repeats and reliability:** run each task *k* times and see score, pass rate and stability.
- **Infrastructure errors are not failures:** quota and network errors are reported separately.
- **Traceable results:** every run keeps the event log, answer and workspace of each attempt.
- **No dependencies** beyond Python 3.11+ and Docker.

## Quick start

Requires Python 3.11+ and Docker. The agents are installed in the sandbox image, which is built on
first use.

```bash
git clone https://github.com/Th1nhNg0/benchmax.git
cd benchmax
python3 bench.py login pi            # once per agent: pi, codex, claude
python3 bench.py run -m openai-codex/gpt-6-luna -t high -k 3 -j 3
python3 bench.py report
```

## More

See the [guide](docs/GUIDE.md) for logging in, all commands and options, writing tasks and
graders, the sandbox, configuration and the results layout.

## Attribution

The tasks in `tasks/algorithms/euler-*` are taken from [Project Euler](https://projecteuler.net)
and used under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Reference
answers are from [lucky-bai/projecteuler-solutions](https://github.com/lucky-bai/projecteuler-solutions).

## License

The code is released under the [MIT License](LICENSE). Task content keeps its original license (see
[Attribution](#attribution)).
