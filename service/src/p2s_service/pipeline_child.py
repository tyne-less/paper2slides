"""Entry point of the child process that runs the pipeline for one attempt.

The worker kills this process on timeout, lease loss, or shutdown, so a hung stage
(for example a stuck pdftotext call) cannot block the worker. Messages sent to the parent:
("ready", None) once started, ("stage", StageTiming) after each stage, then ("ok", paths)
or ("error", (type, message, tb)).
"""

import signal
import traceback
from multiprocessing.connection import Connection
from pathlib import Path

from .pipeline_adapter import StageError


class _StreamingTimings(list):
    def __init__(self, conn: Connection):
        super().__init__()
        self.conn = conn

    def append(self, timing) -> None:
        super().append(timing)
        self.conn.send(("stage", timing))


def main(pipeline, input_pdf: str, run_dir: str, pdftotext_mode: str, conn: Connection) -> None:
    # The parent decides when this process stops; it sends SIGKILL when needed.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    conn.send(("ready", None))
    try:
        paths = pipeline(Path(input_pdf), Path(run_dir), pdftotext_mode, _StreamingTimings(conn))
    except Exception as exc:
        cause = exc.__cause__ if isinstance(exc, StageError) and exc.__cause__ else exc
        message = f"{exc}: {cause}" if cause is not exc else str(exc)
        conn.send(("error", (type(cause).__name__, message, traceback.format_exc())))
    else:
        conn.send(("ok", {name: str(path) for name, path in paths.items()}))
    finally:
        conn.close()
