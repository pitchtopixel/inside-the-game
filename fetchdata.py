"""
Fetch Metrica Sports Sample Game 1 (tracking + events, synced).
Source: https://github.com/metrica-sports/sample-data
Saves to data/raw/metrica/ and prints a short summary.
"""

import os
import urllib.request

import pandas as pd

BASE_URL = "https://raw.githubusercontent.com/metrica-sports/sample-data/master/data/Sample_Game_1"
FILES = [
    "Sample_Game_1_RawEventsData.csv",
    "Sample_Game_1_RawTrackingData_Home_Team.csv",
    "Sample_Game_1_RawTrackingData_Away_Team.csv",
]
OUT_DIR = os.path.join("data", "raw", "metrica")


def download(fname):
    path = os.path.join(OUT_DIR, fname)
    if os.path.exists(path):
        print(f"skip (exists): {path}")
        return path
    url = f"{BASE_URL}/{fname}"
    print(f"downloading: {url}")
    urllib.request.urlretrieve(url, path)
    return path


def load_tracking(path):
    # Rows 0-1 are team / jersey headers; row 2 has column names.
    # Each player has two columns (x, y); the second is unnamed in the raw file.
    raw_cols = pd.read_csv(path, skiprows=2, nrows=0).columns.tolist()
    cols = []
    for i, c in enumerate(raw_cols):
        if c.startswith("Unnamed"):
            prev = cols[-1].replace("_x", "")
            cols.append(f"{prev}_y")
        elif c.startswith("Player") or c == "Ball":
            cols.append(f"{c}_x")
        else:
            cols.append(c)
    return pd.read_csv(path, skiprows=3, names=cols)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = [download(f) for f in FILES]

    events = pd.read_csv(paths[0])
    print("\n== Events ==")
    print(f"rows: {len(events)}")
    print(f"columns: {events.columns.tolist()}")
    print(f"event types: {events['Type'].value_counts().to_dict()}")

    for label, p in [("Home tracking", paths[1]), ("Away tracking", paths[2])]:
        trk = load_tracking(p)
        n_players = sum(c.endswith("_x") and c.startswith("Player") for c in trk.columns)
        print(f"\n== {label} ==")
        print(f"frames: {len(trk)}  players: {n_players}  (25 fps, coords 0-1)")
        print(f"first columns: {trk.columns[:7].tolist()}")


if __name__ == "__main__":
    main()