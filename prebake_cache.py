"""
Prebake the answer cache: play every match once through the real model with the app's preset viewers
and save the checked answers to data/cache/answers.json. The app preloads that file, so judges who use
the presets cost no model calls; only custom viewers (other languages, clubs, players) call the model.

    python prebake_cache.py --log        # needs .env (Foundry endpoint + model) and `az login`
"""

import argparse
import asyncio
import glob
import os
import sys

from agents.common import load_answer_cache, save_answer_cache
from app import ANSWER_CACHE_PATH, TIMELINE, Session, make_profile
from llm import get_chat_client
from orchestrator import Orchestrator
from testlog import start_log

sys.stdout.reconfigure(encoding="utf-8")


async def bake(path, client):
    session = Session(path, 1.0, client)
    profiles = [TIMELINE] + [make_profile(p["profile"]) for p in session.meta_msg()["presets"]]
    orch = Orchestrator(session.meta, profiles, client=client)
    for ev in session.events:
        await orch.process(ev)
    await orch.drain()
    s = orch.summary()
    print(f"{os.path.basename(path)}: {len(profiles)} viewers ({', '.join(p['profile_id'] for p in profiles)})")
    print(f"  model calls {sum(s['llm_calls'].values())}, cache hits {s['cache_hits']}, fallbacks {s['fallbacks']}")


async def bake_all(client):
    for path in sorted(glob.glob(os.path.join("data", "synthetic", "match_*.json"))):
        await bake(path, client)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    args = ap.parse_args()
    if args.log:
        start_log("prebake_cache")
    model = os.getenv("FOUNDRY_MODEL")
    client = get_chat_client("foundry")
    print(f"reusing {load_answer_cache(client, ANSWER_CACHE_PATH, model)} saved answers (only new tasks call the model)")
    asyncio.run(bake_all(client))
    n = save_answer_cache(client, ANSWER_CACHE_PATH, model)
    print(f"\nsaved {n} checked answers ({model}) -> {ANSWER_CACHE_PATH}")


if __name__ == "__main__":
    main()
