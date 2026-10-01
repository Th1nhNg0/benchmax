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
