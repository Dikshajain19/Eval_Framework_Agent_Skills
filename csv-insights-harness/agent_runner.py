#!/usr/bin/env python3
"""
Adapter contract for your actual agent/model runtime.

Your runtime should:
1. Start a fresh context for EVERY invocation.
2. Read prompt-file.
3. If --skill is non-empty, expose that skill to the agent.
4. If --skill is empty, expose no skill.
5. Allow the agent to read the input files listed in the prompt.
6. Save the final textual response to output-dir/response.txt.
7. Save any generated artifacts under output-dir/.
8. If available, save model token usage by extending harness.py's timing logic.

This file intentionally does not pretend to know how your local agent runtime
loads skills. That part is model/runtime-specific.
"""

import argparse
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--skill", default="")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()

    prompt = Path(args.prompt_file).read_text(encoding="utf-8")

    # TODO:
    # Replace this block with your actual model/agent invocation.
    #
    # The system/developer context should be:
    #   - clean for this test case
    #   - skill available only when args.skill is non-empty
    #
    # Then write the model's final answer:
    #
    # Path(args.output_dir, "response.txt").write_text(
    #     final_text, encoding="utf-8"
    # )

    raise SystemExit(
        "Connect agent_runner.py to your model/runtime. "
        "See the adapter contract at the top of this file."
    )

if __name__ == "__main__":
    main()
