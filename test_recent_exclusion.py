"""Quick test helper for the "recent 3-day exclusion" logic.

Aims to reproduce **Method A + C** described in docs:
1.   Manually insert a record for *yesterday* into `user_history`.
2.   Call the helper `recent_place_ids()` to assert the place is returned.
3.   Call `reply_best()` (in a test context) and watch the DEBUG log – the
     inserted place_id should be skipped.

Run:
$ python test_recent_exclusion.py <USER_ID> <PLACE_ID>
Test data: PLACE_ID=ChIJM-gw3V8VaTQRUdGuftQYCpU, USER_ID=U08e131756f765450d6958547b2cfaeb3, name=阜瑪烤肉飯

Where
    <USER_ID>  – LINE userId you are testing with (e.g. from event.source.user_id)
    <PLACE_ID> – Any existing place_id in `places` table.

Example:
$ python test_recent_exclusion.py U1234567890 ChIJabc123456

NOTE:
This script does **not** require Flask running. It directly imports functions
from `lunch_bot.py`.


"""

import sys
import sqlite3
from datetime import datetime, timedelta
import logging

# Ensure we import from project root
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.append(str(ROOT))

# dynamic import lunch_bot
spec = importlib.util.spec_from_file_location("lunch_bot", str(ROOT / "lunch_bot.py"))
lunch = importlib.util.module_from_spec(spec)  # type: ignore
spec.loader.exec_module(lunch)  # type: ignore

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

def insert_history(user_id: str, place_id: str):
    db = Path("lunch.db")
    if not db.exists():
        print("lunch.db not found – run lunch_bot.py first.")
        sys.exit(1)
    yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO user_history (user_id, place_id, chosen_at) VALUES (?,?,?)",
            (user_id, place_id, yesterday),
        )
        conn.commit()
    print(f"[OK] inserted record for user={user_id} place={place_id} at {yesterday}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python test_recent_exclusion.py <USER_ID> <PLACE_ID>")
        sys.exit(1)
    user_id, place_id = sys.argv[1:3]
    insert_history(user_id, place_id)

    exclude = lunch.recent_place_ids(user_id)
    print("recent_place_ids →", exclude)
    assert place_id in exclude, "Inserted place_id should be in exclusion set"

    # Trigger a sample query (no keyword / category) just to see logging
    rows = lunch.query_places(exclude_ids=exclude)
    for r in rows:
        print("RESULT:", r[1], "(place_id:", r[0], ")")
    print("If the inserted place does NOT appear above, exclusion works.")

if __name__ == "__main__":
    main()

