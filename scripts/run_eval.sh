#!/usr/bin/env bash
# Runs the Astra benchmark evaluation harness
set -e

python scripts/run_eval.py "$@"
