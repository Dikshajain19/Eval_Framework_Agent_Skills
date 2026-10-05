#!/usr/bin/env python3
"""
Skill eval harness following the Agent Skills evaluation loop.

What it does:
1. Loads evals/evals.json.
2. Runs every eval with the skill and without the skill.
3. Supports multiple model/agent runners through a command template.
4. Gives each run a clean subprocess/context.
5. Copies input files into an isolated run directory.
6. Saves outputs, timing.json, grading.json, and benchmark.json.
7. Grades mechanical assertions with Python when possible and all other
   assertions with an LLM judge.
8. Produces an iteration-N workspace so runs are comparable.

This harness deliberately does NOT assume a particular agent runtime.
Instead, each model is configured with a command template that receives:
  --prompt, --skill-path, --output-dir, --model

Your command should execute one clean agent session and write its final
text response to <output-dir>/response.txt. Any generated files should
also be placed under <output-dir>/.

Example:
  python harness.py --models models.json --iteration 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_EVALS = ROOT / "evals" / "evals.json"
DEFAULT_WORKSPACE = ROOT / "csv-insights-workspace"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-")[:100]


def copy_inputs(eval_case: dict, run_dir: Path, skill_root: Path) -> list[str]:
    copied = []
    input_dir = run_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)

    for rel in eval_case.get("files", []):
        src = (skill_root / rel).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Input file not found: {src}")

        # Preserve the relative basename safely. For a small demo suite this
        # is sufficient and avoids exposing the original tree to the runner.
        dst = input_dir / Path(rel).name
        shutil.copy2(src, dst)
        copied.append(str(dst))

    return copied


def build_prompt(eval_case: dict, input_files: list[str]) -> str:
    files_text = "\n".join(f"- {p}" for p in input_files) or "- None"

    return f"""Execute this evaluation task in a clean context.

Task:
{eval_case["prompt"]}

Input files available for this run:
{files_text}

Important:
- Complete the user's task, not the evaluation itself.
- Do not discuss this harness unless the task requires it.
- Save any files you create under the output directory provided by the harness.
- Return a concise final response describing the result and relevant evidence.
"""


def run_agent(
    model_cfg: dict,
    prompt: str,
    skill_path: Path | None,
    output_dir: Path,
) -> dict:
    """
    Run one clean agent process.

    Command templates can reference:
      {prompt_file}
      {skill_path}
      {output_dir}
      {model}

    The prompt is written to a file rather than interpolated directly into a
    shell command, avoiding quoting/injection problems.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = output_dir / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    template = model_cfg["command"]
    command = template.format(
        prompt_file=str(prompt_file.resolve()),
        skill_path=str(skill_path.resolve()) if skill_path else "",
        output_dir=str(output_dir.resolve()),
        model=model_cfg.get("model", ""),
    )

    started = time.perf_counter()
    proc = subprocess.run(
        command,
        shell=True,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)

    # The agent runner should write response.txt. As a convenience, fall back
    # to stdout when it did not.
    response_file = output_dir / "response.txt"
    if response_file.exists():
        response = response_file.read_text(encoding="utf-8")
    else:
        response = proc.stdout

    (output_dir / "runner_stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (output_dir / "runner_stderr.txt").write_text(proc.stderr, encoding="utf-8")

    result = {
        "return_code": proc.returncode,
        "duration_ms": duration_ms,
        "response_file": str(response_file),
        "response": response,
        "command": command,
    }

    # timing.json follows the Agent Skills workspace convention. If your
    # runtime knows actual model token usage, add total_tokens here.
    save_json(
        output_dir / "timing.json",
        {
            "duration_ms": duration_ms,
            "duration_seconds": duration_ms / 1000,
            "total_tokens": None,
        },
    )

    return result


def mechanical_grade(assertion: str, response: str, output_dir: Path) -> dict | None:
    """
    Conservative mechanical grader.

    It only handles assertions that are objectively recognizable from the
    assertion text. Returning None delegates grading to the LLM judge.

    This keeps code checks reliable and avoids pretending that arbitrary
    natural-language assertions are deterministic.
    """
    a = assertion.lower()

    # File existence assertions.
    if "output includes" in a and "file" in a:
        # Only handle explicit common file extensions.
        extensions = re.findall(r"\.(png|jpg|jpeg|csv|json|pdf|xlsx|txt)\b", a)
        if extensions:
            files = [
                p for p in output_dir.rglob("*")
                if p.is_file() and p.name not in {
                    "prompt.txt", "response.txt",
                    "runner_stdout.txt", "runner_stderr.txt",
                    "timing.json", "grading.json"
                }
            ]
            found = any(p.suffix.lower().lstrip(".") in extensions for p in files)
            return {
                "passed": found,
                "evidence": f"Found files: {[p.name for p in files]}",
                "grader": "python",
            }

    return None


def llm_grade(
    judge_cfg: dict,
    eval_case: dict,
    response: str,
    output_dir: Path,
) -> dict:
    """
    Invoke a configurable LLM judge.

    The judge command receives a JSON payload in judge_payload.json and must
    write grading.json with the schema described in the prompt below.

    This is intentionally runtime-agnostic: your team can point it at an
    OpenAI, Claude, Gemini, or internal judge wrapper.
    """
    payload = {
        "task": eval_case["prompt"],
        "expected_output": eval_case.get("expected_output", ""),
        "assertions": eval_case.get("assertions", []),
        "agent_response": response,
    }

    payload_file = output_dir / "judge_payload.json"
    save_json(payload_file, payload)

    command = judge_cfg["command"].format(
        payload_file=str(payload_file.resolve()),
        output_dir=str(output_dir.resolve()),
        model=judge_cfg.get("model", ""),
    )

    started = time.perf_counter()
    proc = subprocess.run(
        command,
        shell=True,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)

    grading_file = output_dir / "grading.json"

    if grading_file.exists():
        grading = load_json(grading_file)
    else:
        grading = {
            "assertion_results": [
                {
                    "text": a,
                    "passed": False,
                    "evidence": (
                        "Judge did not produce grading.json. "
                        f"stderr: {proc.stderr[-1000:]}"
                    ),
                    "grader": "llm",
                }
                for a in eval_case.get("assertions", [])
            ]
        }

    grading["judge_duration_ms"] = duration_ms
    grading["judge_return_code"] = proc.returncode
    save_json(grading_file, grading)
    return grading


def grade_run(
    eval_case: dict,
    run_result: dict,
    run_dir: Path,
    judge_cfg: dict | None,
) -> dict:
    response = run_result["response"]
    assertion_results = []

    for assertion in eval_case.get("assertions", []):
        mechanical = mechanical_grade(assertion, response, run_dir)

        if mechanical is not None:
            assertion_results.append({
                "text": assertion,
                **mechanical,
            })
        else:
            assertion_results.append(None)

    unresolved = [i for i, x in enumerate(assertion_results) if x is None]

    if unresolved:
        if not judge_cfg:
            for i in unresolved:
                assertion_results[i] = {
                    "text": eval_case["assertions"][i],
                    "passed": False,
                    "evidence": "No LLM judge configured.",
                    "grader": "ungraded",
                }
        else:
            llm_result = llm_grade(judge_cfg, eval_case, response, run_dir)
            llm_results = llm_result.get("assertion_results", [])

            # Match judge results by assertion text rather than list position.
            by_text = {x.get("text"): x for x in llm_results}
            for i in unresolved:
                assertion = eval_case["assertions"][i]
                assertion_results[i] = by_text.get(
                    assertion,
                    {
                        "text": assertion,
                        "passed": False,
                        "evidence": "Judge omitted this assertion.",
                        "grader": "llm",
                    },
                )

    passed = sum(bool(x.get("passed")) for x in assertion_results)
    total = len(assertion_results)

    grading = {
        "assertion_results": assertion_results,
        "summary": {
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "pass_rate": (passed / total) if total else 0.0,
        },
    }
    save_json(run_dir / "grading.json", grading)
    return grading


def summarize(values: list[float]) -> dict:
    if not values:
        return {"mean": 0, "stddev": 0}
    return {
        "mean": statistics.mean(values),
        "stddev": statistics.stdev(values) if len(values) > 1 else 0,
    }


def aggregate(results: dict) -> dict:
    summary = {}

    for config, runs in results.items():
        pass_rates = [r["pass_rate"] for r in runs]
        durations = [r["duration_seconds"] for r in runs]
        tokens = [r["total_tokens"] for r in runs if r["total_tokens"] is not None]

        summary[config] = {
            "pass_rate": summarize(pass_rates),
            "time_seconds": summarize(durations),
            "tokens": summarize(tokens),
            "evaluations": len(runs),
        }

    if "with_skill" in summary and "without_skill" in summary:
        summary["delta"] = {
            "pass_rate": (
                summary["with_skill"]["pass_rate"]["mean"]
                - summary["without_skill"]["pass_rate"]["mean"]
            ),
            "time_seconds": (
                summary["with_skill"]["time_seconds"]["mean"]
                - summary["without_skill"]["time_seconds"]["mean"]
            ),
            "tokens": (
                summary["with_skill"]["tokens"]["mean"]
                - summary["without_skill"]["tokens"]["mean"]
                if summary["with_skill"]["tokens"]["mean"] is not None
                and summary["without_skill"]["tokens"]["mean"] is not None
                else None
            ),
        }

    return {"run_summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", default=str(ROOT))
    parser.add_argument("--evals", default=str(DEFAULT_EVALS))
    parser.add_argument("--models", required=True,
                        help="JSON config containing model runner commands.")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--judge", default=None,
                        help="Optional JSON config for the LLM judge runner.")
    args = parser.parse_args()

    skill_root = Path(args.skill).resolve()
    evals_path = Path(args.evals).resolve()
    workspace = Path(args.workspace).resolve()
    models_cfg = load_json(Path(args.models).resolve())
    eval_data = load_json(evals_path)
    judge_cfg = load_json(Path(args.judge).resolve()) if args.judge else None

    iteration_dir = workspace / f"iteration-{args.iteration}"
    iteration_dir.mkdir(parents=True, exist_ok=True)

    # Snapshot the skill for reproducibility, as recommended by the eval guide.
    snapshot = iteration_dir / "skill-snapshot"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    shutil.copytree(
        skill_root,
        snapshot,
        ignore=shutil.ignore_patterns(
            "*workspace*", "__pycache__", ".git"
        ),
    )

    aggregate_results = {name: [] for name in models_cfg["models"]}

    for eval_case in eval_data["evals"]:
        eval_name = f"eval-{safe_name(eval_case['id'])}"
        eval_dir = iteration_dir / eval_name
        eval_dir.mkdir(parents=True, exist_ok=True)

        for model_name, model_cfg in models_cfg["models"].items():
            # Model names can be used for cross-model comparisons.
            model_dir = eval_dir / model_name
            model_dir.mkdir(parents=True, exist_ok=True)

            # The baseline is explicitly "without skill". The skill-enabled
            # run gets the snapshot from this exact iteration.
            use_skill = model_cfg.get("use_skill", True)
            skill_path = snapshot if use_skill else None

            input_files = copy_inputs(eval_case, model_dir, skill_root)
            prompt = build_prompt(eval_case, input_files)

            result = run_agent(
                model_cfg=model_cfg,
                prompt=prompt,
                skill_path=skill_path,
                output_dir=model_dir / "outputs",
            )

            grading = grade_run(
                eval_case=eval_case,
                run_result=result,
                run_dir=model_dir,
                judge_cfg=judge_cfg,
            )

            timing = load_json(model_dir / "outputs" / "timing.json")
            aggregate_results[model_name].append({
                "eval_id": eval_case["id"],
                "pass_rate": grading["summary"]["pass_rate"],
                "duration_seconds": timing["duration_seconds"],
                "total_tokens": timing.get("total_tokens"),
            })

    benchmark = aggregate(aggregate_results)
    save_json(iteration_dir / "benchmark.json", benchmark)

    # Human-readable console summary.
    print("\n=== Agent Skills Evaluation ===")
    print(f"Iteration: {args.iteration}")
    print(f"Workspace: {iteration_dir}")
    print()

    for model_name, stats in benchmark["run_summary"].items():
        if model_name == "delta":
            continue
        print(
            f"{model_name:20} "
            f"pass_rate={stats['pass_rate']['mean']:.2%} "
            f"time={stats['time_seconds']['mean']:.2f}s"
        )

    if "delta" in benchmark["run_summary"]:
        d = benchmark["run_summary"]["delta"]
        print(
            f"\nDelta: pass_rate={d['pass_rate']:+.2%}, "
            f"time={d['time_seconds']:+.2f}s"
        )

    print("\nResults written to benchmark.json and per-eval directories.")


if __name__ == "__main__":
    main()
