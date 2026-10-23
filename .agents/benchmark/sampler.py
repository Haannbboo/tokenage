"""On bench-server: python sampler.py --db-url "$TOKENAGE_DB_URL".

Uses the server venv (SQLAlchemy + psycopg); prints unix_ts queue_depth usage_rows.
"""

import argparse
import os
import sqlite3
import time
from pathlib import Path

from sqlalchemy import create_engine, text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-url", default=os.environ.get("TOKENAGE_DB_URL"))
    p.add_argument("--interval", type=float, default=2, help="seconds between samples")
    args = p.parse_args()
    if not args.db_url:
        p.error(
            "provide --db-url or TOKENAGE_DB_URL (SQLite: sqlite:////absolute/path)"
        )
    if not os.environ.get("TOKENAGE_HOME"):
        p.error("set TOKENAGE_HOME to the server's home; its state.db holds the queue")
    url = args.db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url)
    state = Path(os.environ["TOKENAGE_HOME"]) / "state.db"
    with sqlite3.connect(state.resolve().as_uri() + "?mode=ro", uri=True) as queue:
        while True:
            started = time.monotonic()
            depth = queue.execute("select count(*) from events").fetchone()[0]
            with engine.connect() as db:
                rows = db.execute(text("select count(*) from usage")).scalar_one()
            print(int(time.time()), depth, rows, flush=True)
            time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
