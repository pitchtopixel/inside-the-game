"""
Groundedness report (13g): every saved answer in data/cache/answers.json is judged by the Foundry Groundedness
evaluator (azure-ai-evaluation, gpt-4.1-mini as judge) against exactly what its agent was given:
explainer / narrator answers against the match facts, personalizer rewrites against the overlay they rewrote
(so translations are checked for meaning too - in every language). Offline: live viewers pay nothing.

    pip install -r requirements-eval.txt
    python eval_groundedness.py --log             # needs .env (Foundry endpoint + model) and `az login`
    python eval_groundedness.py --limit 5         # quick try on 5 answers

Writes docs/groundedness_report.md. Score 1-5 per answer; pass = score >= 4.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from azure.ai.evaluation import AzureOpenAIModelConfiguration, GroundednessEvaluator
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from app import ANSWER_CACHE_PATH
from testlog import start_log

sys.stdout.reconfigure(encoding="utf-8")
REPORT = os.path.join("docs", "groundedness_report.md")
PASS = 4


def cases(path):
    """(agent, task, language, context, response) per saved answer."""
    for agent, _fp, task, answer in json.load(open(path, encoding="utf-8"))["answers"]:
        p = json.loads(task)
        if p["task"] == "personalize":
            context, lang = p["overlay"], p["profile"]["language_code"]
            label = f"personalize ({p['profile']['mode']})"
        else:
            context, lang, label = {k: v for k, v in p.items() if k != "task"}, "en", p["task"]
        yield {"agent": agent, "task": label, "language": lang,
               "context": json.dumps(context, ensure_ascii=False),
               "response": json.dumps(answer, ensure_ascii=False)}


def judge(evaluator, c):
    try:
        r = evaluator(response=c["response"], context=c["context"])
        return {**c, "score": r.get("groundedness"), "reason": r.get("groundedness_reason", "")}
    except Exception as e:  # one failed judgement must not stop the report
        return {**c, "score": None, "reason": f"error: {e}"[:300]}


def report(results, model):
    scored = [r for r in results if r["score"] is not None]
    passed = [r for r in scored if r["score"] >= PASS]
    groups = defaultdict(list)
    for r in scored:
        groups[(r["agent"], r["task"], r["language"])].append(r)
    lines = [
        "# Groundedness report",
        "",
        f"Every saved answer (`{ANSWER_CACHE_PATH.replace(os.sep, '/')}`) judged by the Foundry **Groundedness evaluator**",
        f"(azure-ai-evaluation, judge `{model}`) against what its agent was given. Score 1-5; pass = {PASS}+.",
        "Personalizer rewrites are judged against the overlay they rewrote, so translations are checked for meaning.",
        "",
        f"**{len(passed)}/{len(scored)} answers grounded ({len(passed) / max(len(scored), 1):.0%})** · "
        f"average score {sum(r['score'] for r in scored) / max(len(scored), 1):.2f} · "
        f"judge errors {len(results) - len(scored)}",
        "",
        "| Agent | Task | Language | Answers | Grounded | Avg score |",
        "|---|---|---|---|---|---|",
    ]
    for (agent, task, lang), rs in sorted(groups.items()):
        ok = sum(r["score"] >= PASS for r in rs)
        lines.append(f"| {agent} | {task} | {lang} | {len(rs)} | {ok} | {sum(r['score'] for r in rs) / len(rs):.2f} |")
    low = sorted((r for r in scored if r["score"] < PASS), key=lambda r: r["score"])
    lines += ["", f"## Below {PASS} ({len(low)})", ""]
    for r in low:
        resp = json.loads(r["response"])
        text = " | ".join(str(v) for k, v in resp.items() if isinstance(v, str))
        lines += [f"- **{r['score']}** · {r['agent']} / {r['task']} / {r['language']}: {text[:300]}",
                  f"  - judge: {r['reason'][:400]}"]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="judge only the first N answers (quick try)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if args.log:
        start_log("eval_groundedness")
    load_dotenv()
    model = os.getenv("FOUNDRY_MODEL")
    host = urlparse(os.environ["FOUNDRY_PROJECT_ENDPOINT"])
    config = AzureOpenAIModelConfiguration(azure_endpoint=f"{host.scheme}://{host.netloc}", azure_deployment=model,
                                           api_version="2024-10-21")
    evaluator = GroundednessEvaluator(config, credential=DefaultAzureCredential())
    todo = list(cases(ANSWER_CACHE_PATH))
    if args.limit:
        todo = todo[:args.limit]
    print(f"judging {len(todo)} answers with {model}")
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda c: judge(evaluator, c), todo))
    md = report(results, model)
    print(md)
    if not args.limit:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"-> {REPORT}")


if __name__ == "__main__":
    main()
