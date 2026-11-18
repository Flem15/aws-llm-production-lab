from pathlib import Path

import pytest

from bots.errors import SafetyHalt
from bots.singleton import SingleInstanceLock


def test_second_process_lock_is_rejected(tmp_path: Path):
    path = tmp_path / "bot.lock"
    first = SingleInstanceLock(str(path))
    second = SingleInstanceLock(str(path))
    first.acquire()
    try:
        with pytest.raises(SafetyHalt):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
