# CSV Insights Eval Harness

This harness follows the Agent Skills evaluation workflow:

- fresh run per test case/configuration
- same prompt and files for comparable runs
- skill vs no-skill baseline
- iteration-N workspace
- per-run outputs
- timing.json
- grading.json
- benchmark.json
- mechanical checks where reliable
- LLM judge for semantic assertions
- model/runtime adapters kept separate from the evaluation logic

## Important

`agent_runner.py` is intentionally an adapter. Different model runtimes expose
skills differently, so the harness does not fake a universal skill-loading API.

Connect your actual agent runtime there. Every invocation must start a fresh
context.

`judge_runner.py` is similarly an adapter. Connect your preferred judge model
there. It must write the grading JSON specified in the file.

## Run

```bash
python harness.py   --models models.example.json   --judge judge.example.json   --iteration 1
```

The result will look like:

```text
csv-insights-workspace/
└── iteration-1/
    ├── skill-snapshot/
    ├── eval-direct-highest-revenue/
    │   ├── with_skill/
    │   │   ├── outputs/
    │   │   ├── grading.json
    │   │   └── ...
    │   └── without_skill/
    │       ├── outputs/
    │       ├── grading.json
    │       └── ...
    ├── ...
    └── benchmark.json
```

For multiple models, add more entries to `models.json`. Each model can have a
different command, model name, and `use_skill` setting.

For a true model comparison, use the same eval cases, same files, same
temperature/settings where applicable, and a blind judge when doing holistic
comparison. Do not tell the judge which output came from the skill.
