import os
from pathlib import Path


def write_report(path: Path, text: str):
    """Never overwrite existing evidence; keep newly exported source material private."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
