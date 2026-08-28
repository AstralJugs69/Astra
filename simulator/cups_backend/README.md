# CUPS capture backend

`relay_capture_backend.py` is a stdlib-first CUPS backend for the simulated
physical endpoint. It accepts only `relay-capture://demo-embosser`, derives
paths from the numeric CUPS job ID under a fixed root, validates CRLF/form-feed
BRF geometry, and records received bytes plus hash-chained simulated page
events.

CUPS scheduler state remains real and human-controlled. A terminated capture
is partial endpoint evidence, not proof of a physical embosser stop or output
isolation. WSL/CUPS installation and policy-denial tests are intentionally not
claimed on hosts where WSL or CUPS is unavailable.

