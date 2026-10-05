#!/usr/bin/env python3
"""
Example LLM judge contract.

Replace the implementation of call_model() with your chosen provider.
The important contract is:
  input: judge_payload.json
  output: <output-dir>/grading.json

The judge MUST evaluate every assertion independently and require concrete
evidence from the agent response. It must not infer success merely because
the response sounds plausible.
"""

import argparse
import json
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--payload-file", required=True)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()

    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))

    judge_prompt = f"""
You are grading an Agent Skills evaluation.

Task:
{payload["task"]}

Expected outcome:
{payload["expected_output"]}

Assertions:
{json.dumps(payload["assertions"], indent=2)}

Agent response:
{payload["agent_response"]}

For EACH assertion:
- Decide PASS or FAIL.
- Require concrete evidence from the response.
- Do not give the benefit of the doubt.
- Do not require exact wording when semantically equivalent wording is correct.
- If the response merely mentions a required term without actually satisfying
  the assertion, mark FAIL.
- Keep evidence concise and quote/reference the relevant part of the response.

Return ONLY JSON:
{{
  "assertion_results": [
    {{
      "text": "...",
      "passed": true,
      "evidence": "..."
    }}
  ]
}}
"""

    # TODO: Call your selected LLM provider with judge_prompt.
    # For example, your provider wrapper should return parsed JSON here.
    raise SystemExit(
        "Replace call to your LLM provider in judge_runner.py. "
        "The harness is provider-agnostic by design."
    )

if __name__ == "__main__":
    main()
