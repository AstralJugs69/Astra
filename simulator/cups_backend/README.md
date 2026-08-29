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


The installed WSL backend reads only `/etc/cups/relay-capture.conf`, which is
root-owned and group-readable by the CUPS service. It fixes capture storage and
BRF geometry and accepts only a bounded page-delay setting. Arbitrary Relay
environment variables are deliberately ignored. CUPS places the queue name in
the executable's first argument, which a Python `#!` interpreter consumes;
Python therefore receives the numeric CUPS job ID first. The device URI comes
through `DEVICE_URI`, and the backend rejects any value other than
`relay-capture://demo-embosser`.
