#!/usr/bin/env bash
# End-to-end check against a running stack: upload a PDF, wait for the job, fetch the result.
# Usage: service/scripts/smoke_test.sh path/to/paper.pdf [base_url]
set -euo pipefail

pdf="$1"
base="${2:-http://localhost:8080}"

for _ in $(seq 1 90); do
  curl -sf "$base/health" >/dev/null && break
  sleep 1
done
curl -sf "$base/health" >/dev/null

json_field() { python3 -c "import json,sys; print(json.load(sys.stdin)[\"$1\"])"; }

job_id=$(curl -sf -F "file=@$pdf" "$base/jobs" | json_field id)
echo "job $job_id submitted"

status=queued
for _ in $(seq 1 60); do
  status=$(curl -sf "$base/jobs/$job_id" | json_field status)
  [[ "$status" == succeeded || "$status" == dead ]] && break
  sleep 1
done
echo "job $job_id status: $status"
[[ "$status" == succeeded ]]

repeat_code=$(curl -s -o /dev/null -w '%{http_code}' -F "file=@$pdf" "$base/jobs")
[[ "$repeat_code" == 200 ]] || { echo "repeat upload returned $repeat_code, expected 200"; exit 1; }

slides=$(curl -sf "$base/jobs/$job_id/result" | python3 -c \
  "import json,sys; print(len(json.load(sys.stdin)['slide_deck']['slides']))")
curl -sf "$base/jobs/$job_id/result/files/06_slides.tex" | grep -q '\\begin{document}'
echo "result: $slides slides, Beamer file ok, repeat upload deduplicated"
