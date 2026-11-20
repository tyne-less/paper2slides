"""Pipelines injected into Worker(pipeline=...). They run in the spawned child process, so
they must be importable module-level functions."""

import os
import time

from p2s_service.pipeline_adapter import _timed, run_stages


def fail_in_extract(input_pdf, run_dir, mode, timings):
    _timed("extract_text", timings, lambda: 1 / 0)


def hang_after_extract(input_pdf, run_dir, mode, timings):
    _timed("extract_text", timings, lambda: None)
    time.sleep(600)


def crash(input_pdf, run_dir, mode, timings):
    os._exit(137)


def slow(input_pdf, run_dir, mode, timings):
    time.sleep(float(os.environ.get("P2S_TEST_PIPELINE_DELAY", "3")))
    return run_stages(input_pdf, run_dir, mode, timings)
