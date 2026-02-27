"""
Quick console inspection of current error records in the database.
Usage: uv run python inspect.py
"""
from storage import Database
from storage.models import ErrorStatus

db = Database()
db.initialize()

errors = db.get_all_active()

if not errors:
    print("No active errors in the database.")
else:
    print(f"{len(errors)} active error(s)\n")
    for i, e in enumerate(errors, 1):
        print(f"{'─' * 60}")
        print(f"[{i}] {e.logger_name}")
        print(f"    fingerprint : {e.fingerprint}")
        print(f"    status      : {e.status.value}")
        print(f"    occurrences : {e.occurrence_count}")
        print(f"    first seen  : {e.first_seen}")
        print(f"    last seen   : {e.last_seen}")
        print(f"    file        : {e.file_path}:{e.line_number}")
        print(f"    message     :\n")
        for line in e.message_template.splitlines():
            print(f"        {line}")
        if e.sample_traceback:
            print(f"\n    traceback   :\n")
            for line in e.sample_traceback.splitlines():
                print(f"        {line}")
        print()
