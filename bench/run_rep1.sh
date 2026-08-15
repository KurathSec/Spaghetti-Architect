#!/usr/bin/env bash
# rep1 campaign launcher (see bench/PREREGISTRATION_V2.md; commit this file
# BEFORE any batch runs). One batch = one process; the corpus condition is the
# process's BENCH_CORPUS; everything is namespaced by BENCH_RUN_TAG=rep1 so no
# published or local artifact is ever overwritten.
#
# Usage: bench/run_rep1.sh <model-slug> [concurrency]
#   Run one process per model (they are independent); with several models in
#   parallel, pass a reduced concurrency (e.g. 64) to stay polite to the
#   shared endpoint. Interrupted batches resume from their checkpoints.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${1:?usage: run_rep1.sh <model-slug> [concurrency] [max-cost-per-batch]}"
CONC="${2:-96}"
MAXCOST="${3:-12}"

run() { # task cond extra...
  local task="$1" cond="$2"; shift 2
  echo "=== rep1: ${task} / ${cond} / ${MODEL} ==="
  BENCH_RUN_TAG=rep1 BENCH_CORPUS="$cond" python3 bench/run_bench.py \
    --batch "$task" --model "$MODEL" --k 8 --max-cost "$MAXCOST" \
    --concurrency "$CONC" "$@"
}

for cond in annotated unannotated markers_only comments_only; do
  run refactor  "$cond"
  run comprehend "$cond"
done
run refactor lying

echo "=== rep1: comprehend / cot / ${MODEL} ==="
BENCH_RUN_TAG=rep1 BENCH_CORPUS=annotated BENCH_PROMPT_MODE=cot \
  python3 bench/run_bench.py --batch comprehend --model "$MODEL" \
  --family agg_stats --k 8 --max-tokens 4096 --max-cost "$MAXCOST" \
  --concurrency "$CONC" --parse-floor 0.05

echo "rep1 complete for ${MODEL}"
