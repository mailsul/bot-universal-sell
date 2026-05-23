"""produk_lock.py — Cross-process file lock untuk produk.json.
Digunakan oleh web.py (Flask) dan main.py (bot) agar tidak race condition."""

import fcntl
import contextlib

LOCK_PATH = "produk.lock"


@contextlib.contextmanager
def produk_lock():
    fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
