"""Serialize dataset writers and replace complete files for concurrent readers."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile

import pandas as pd


@contextmanager
def dataset_lock(processed_dir: Path):
    processed_dir.mkdir(parents=True, exist_ok=True)
    with (processed_dir / ".dataset.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def write_master(frame: pd.DataFrame, processed_dir: Path) -> dict[str, Path]:
    processed_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "master_csv": "ibay_rentals_master.csv",
        "master_parquet": "ibay_rentals_master.parquet",
        "master_csv_gzip": "ibay_rentals_master.csv.gz",
    }
    # Finish serialization before publishing; gzip is the dashboard's preferred
    # file and is replaced last. Readers never see a partially written file.
    with tempfile.TemporaryDirectory(dir=processed_dir) as temp:
        staging = Path(temp)
        frame.to_csv(staging / names["master_csv"], index=False)
        frame.to_parquet(staging / names["master_parquet"], index=False, engine="pyarrow")
        frame.to_csv(staging / names["master_csv_gzip"], index=False, compression="gzip")
        for name in names.values():
            os.replace(staging / name, processed_dir / name)
    return {key: processed_dir / name for key, name in names.items()}
