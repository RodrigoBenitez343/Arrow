#!/usr/bin/env python3
"""
Quick threading test for ContextDatabase to validate cross-thread safety.
"""

import threading
import time
import random
from typing import List

from context_database import ContextDatabase


def worker(db: ContextDatabase, thread_idx: int, iterations: int, errors: List[str]) -> None:
    for i in range(iterations):
        try:
            content = f"thread-{thread_idx} item-{i}"
            db.push(
                chain_id='thread_test',
                node_id=f'worker-{thread_idx}',
                key=f'item-{i}',
                value={'worker': thread_idx, 'index': i, 'content': content},
            )
            # Random short sleep to increase interleaving
            time.sleep(random.uniform(0.001, 0.01))
        except Exception as e:
            errors.append(f"t{thread_idx}/i{i}: {e}")


def main() -> None:
    db = ContextDatabase()
    threads = []
    errors: List[str] = []
    thread_count = 5
    iterations = 50

    for t_idx in range(thread_count):
        t = threading.Thread(target=worker, args=(db, t_idx, iterations, errors))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print(f"Completed {thread_count * iterations} inserts across {thread_count} threads.")
    if errors:
        print(f"Encountered {len(errors)} errors:")
        for err in errors[:10]:
            print(err)
    else:
        print("No threading errors detected.")


if __name__ == "__main__":
    main()