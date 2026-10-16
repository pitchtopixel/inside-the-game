"""Mirror console output to logs/<name>_<date-time>.txt (used by the test scripts' --log option)."""

import os
import sys
from datetime import datetime


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)

    def flush(self):
        for s in self.streams:
            s.flush()


def start_log(name):
    os.makedirs("logs", exist_ok=True)
    path = os.path.join("logs", f"{name}_{datetime.now():%Y-%m-%d_%H%M}.txt")
    f = open(path, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, f)
    print(f"log: {path}")
    return path
