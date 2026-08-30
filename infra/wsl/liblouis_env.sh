#!/usr/bin/env bash
# Source from WSL before running Relay commands that require Liblouis 3.38.0.
export PYTHONPATH="/opt/liblouis-python-3.38.0${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="/opt/liblouis-3.38.0/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LIBLOUIS_TABLEPATH="/opt/liblouis-3.38.0/share/liblouis/tables"
