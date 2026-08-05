from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def workspace_temp_directory(repository_root: Path) -> Iterator[Path]:
    base = (repository_root / ".test_tmp").resolve()
    base.mkdir(parents=True, exist_ok=True)
    path = (base / uuid.uuid4().hex).resolve()
    if path.parent != base:
        raise RuntimeError("Refusing to create a test directory outside .test_tmp.")
    path.mkdir()
    try:
        yield path
    finally:
        if path.parent == base and path.exists():
            shutil.rmtree(path)
