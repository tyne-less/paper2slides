"""Worker entry point: python -m p2s_service worker [--drain]

  (no flag)  poll the queue until SIGTERM (local compose).
  --drain    process every job that is ready, then exit (one Cloud Run Job execution).

multiprocessing re-runs the main module in every pipeline process unless its name ends in
".__main__". Starting the worker from this package __main__ (rather than with
`python -m p2s_service.worker`) avoids re-importing the worker's dependencies in each
pipeline process, which cost about 0.3-0.5 s per attempt.
"""

import argparse

parser = argparse.ArgumentParser(prog="python -m p2s_service")
commands = parser.add_subparsers(dest="command", required=True)
worker = commands.add_parser("worker", help="run the job worker")
worker.add_argument(
    "--drain", action="store_true", help="exit once no job is ready instead of polling"
)
args = parser.parse_args()

from p2s_service.worker import main  # noqa: E402

main(drain=args.drain)
