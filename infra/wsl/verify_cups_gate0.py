#!/usr/bin/env python3
"""Human-run CUPS 2.x authorization and observer verification harness.

This is a local verification script, not a Relay endpoint. It prompts for the
observer password without storing it, reads permitted state through pycups,
and sends exact IPP operation probes directly to the local CUPS scheduler.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import http.client
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import cups

IPP_VERSION_MAJOR = 1
IPP_VERSION_MINOR = 1
IPP_TAG_OPERATION = 0x01
IPP_TAG_END = 0x03
IPP_TAG_INTEGER = 0x21
IPP_TAG_BOOLEAN = 0x22
IPP_TAG_TEXT = 0x41
IPP_TAG_NAME = 0x42
IPP_TAG_URI = 0x45
IPP_TAG_CHARSET = 0x47
IPP_TAG_LANGUAGE = 0x48
IPP_TAG_MIMETYPE = 0x49
IPP_OP_PRINT_JOB = 0x0002
IPP_OP_CREATE_JOB = 0x0005
IPP_OP_SEND_DOCUMENT = 0x0006
IPP_OP_CANCEL_JOB = 0x0008
IPP_OP_HOLD_JOB = 0x000C
IPP_OP_RELEASE_JOB = 0x000D
IPP_OP_RESTART_JOB = 0x000E
IPP_OP_CUPS_ADD_MODIFY_PRINTER = 0x4003
IPP_OP_CUPS_GET_DEVICES = 0x400B
IPP_OP_CUPS_GET_DOCUMENT = 0x4027
IPP_STATUS_FORBIDDEN = 0x0401
IPP_STATUS_NOT_AUTHORIZED = 0x0403


@dataclass(frozen=True)
class IppResponse:
    http_status: int
    ipp_status: int | None


def _value_bytes(value_tag: int, value: object) -> bytes:
    if value_tag == IPP_TAG_INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError("IPP integer attribute requires an integer")
        return struct.pack("!i", value)
    if value_tag == IPP_TAG_BOOLEAN:
        if not isinstance(value, bool):
            raise TypeError("IPP boolean attribute requires a boolean")
        return b"\\x01" if value else b"\\x00"
    if not isinstance(value, str):
        raise TypeError("IPP text attributes require strings")
    return value.encode("utf-8")


def _attribute(value_tag: int, name: str, value: object) -> bytes:
    name_bytes = name.encode("utf-8")
    value_bytes = _value_bytes(value_tag, value)
    return (
        bytes((value_tag,))
        + struct.pack("!H", len(name_bytes))
        + name_bytes
        + struct.pack("!H", len(value_bytes))
        + value_bytes
    )


def _request(
    operation: int,
    request_id: int,
    attributes: Iterable[tuple[int, str, object]],
    document: bytes = b"",
) -> bytes:
    header = struct.pack("!BBHI", IPP_VERSION_MAJOR, IPP_VERSION_MINOR, operation, request_id)
    operation_attributes = b"".join(
        _attribute(value_tag, name, value) for value_tag, name, value in attributes
    )
    return (
        header
        + bytes((IPP_TAG_OPERATION,))
        + operation_attributes
        + bytes((IPP_TAG_END,))
        + document
    )


def _send(
    *,
    host: str,
    port: int,
    path: str,
    username: str,
    password: str,
    operation: int,
    request_id: int,
    attributes: Iterable[tuple[int, str, object]],
    document: bytes = b"",
) -> IppResponse:
    body = _request(operation, request_id, attributes, document)
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Length": str(len(body)),
                "Content-Type": "application/ipp",
            },
        )
        response = connection.getresponse()
        payload = response.read()
    finally:
        connection.close()
    ipp_status = int.from_bytes(payload[2:4], "big") if len(payload) >= 4 else None
    return IppResponse(response.status, ipp_status)


def _printer_uri(host: str, port: int, queue: str) -> str:
    return f"ipp://{host}:{port}/printers/{quote(queue, safe='')}"


def _common_attributes(printer_uri: str, username: str) -> list[tuple[int, str, object]]:
    return [
        (IPP_TAG_CHARSET, "attributes-charset", "utf-8"),
        (IPP_TAG_LANGUAGE, "attributes-natural-language", "en"),
        (IPP_TAG_URI, "printer-uri", printer_uri),
        (IPP_TAG_NAME, "requesting-user-name", username),
    ]


def _denied(response: IppResponse) -> bool:
    return response.http_status in {401, 403} or response.ipp_status in {
        IPP_STATUS_FORBIDDEN,
        IPP_STATUS_NOT_AUTHORIZED,
    }


def _expect_denied(label: str, response: IppResponse) -> None:
    if not _denied(response):
        raise RuntimeError(
            f"observer authorization failed for {label}: "
            f"HTTP {response.http_status}, IPP {response.ipp_status}"
        )
    print(f"PASS: relay-observer denied {label}")


def _read_observer_state(args: argparse.Namespace, password: str) -> None:
    cups.setUser(args.user)
    cups.setPasswordCB(lambda _prompt: password)
    connection = cups.Connection(host=args.host, port=args.port)
    printers = connection.getPrinters()
    if args.queue not in printers:
        raise RuntimeError(f"configured queue is missing: {args.queue}")
    jobs = connection.getJobs(which_jobs="all")
    connection.getPrinterAttributes(name=args.queue)
    connection.getJobAttributes(args.job_id)
    print(f"PASS: relay-observer read queue={args.queue} jobs_visible={len(jobs)}")


def _send_document_denial_probe(args: argparse.Namespace, password: str) -> None:
    probe_document = b"BRAILLE-ERRATA-RELAY-SEND-DOCUMENT-DENIAL-PROBE\r\n"
    cups.setUser(args.user)
    cups.setPasswordCB(lambda _prompt: password)
    connection = cups.Connection(host=args.host, port=args.port)
    try:
        statuses = [
            connection.startDocument(
                args.queue,
                args.send_document_job_id,
                "relay-observer-probe.brf",
                "application/vnd.cups-raw",
                1,
            ),
            connection.writeRequestData(probe_document, len(probe_document)),
            connection.finishDocument(args.queue),
        ]
    except cups.IPPError as exc:
        status = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        if status not in {
            cups.IPP_FORBIDDEN,
            cups.IPP_NOT_AUTHENTICATED,
            cups.IPP_NOT_AUTHORIZED,
        }:
            raise RuntimeError(
                f"observer authorization failed for Send-Document: IPP {status}"
            ) from exc
        print("PASS: relay-observer denied Send-Document")
        return

    denied_statuses = {
        401,
        403,
        cups.IPP_FORBIDDEN,
        cups.IPP_NOT_AUTHENTICATED,
        cups.IPP_NOT_AUTHORIZED,
    }
    if any(status in denied_statuses for status in statuses):
        print("PASS: relay-observer denied Send-Document")
        return
    raise RuntimeError(
        "observer authorization failed for Send-Document: "
        f"request accepted with binding statuses {statuses}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=631)
    parser.add_argument("--queue", default="Braille-Embosser-Sim")
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument(
        "--send-document-job-id",
        type=int,
        required=True,
        help="an empty CUPS job created independently by relay-operator for this probe",
    )
    parser.add_argument(
        "--restart-job-id",
        type=int,
        required=True,
        help="a completed or cancelled CUPS job created independently by relay-operator",
    )
    parser.add_argument("--brf", type=Path, required=True)
    parser.add_argument("--user", default="relay-observer")
    parser.add_argument(
        "--probe-admin-mutation",
        action="store_true",
        help="also probe CUPS-Add-Modify-Printer with a reserved name; never use a production name",
    )
    args = parser.parse_args()
    if args.port < 1 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")
    if args.job_id < 1:
        parser.error("--job-id must be positive")
    if args.send_document_job_id < 1:
        parser.error("--send-document-job-id must be positive")
    if args.restart_job_id < 1:
        parser.error("--restart-job-id must be positive")
    if not args.brf.is_file():
        parser.error(f"BRF file does not exist: {args.brf}")
    document = args.brf.read_bytes()
    password = getpass.getpass(f"Password for {args.user}: ")
    printer_uri = _printer_uri(args.host, args.port, args.queue)
    try:
        _read_observer_state(args, password)
        base = _common_attributes(printer_uri, args.user)
        job = [(IPP_TAG_INTEGER, "job-id", args.job_id)]
        restart_job = [(IPP_TAG_INTEGER, "job-id", args.restart_job_id)]
        probes = (
            (
                "Print-Job",
                IPP_OP_PRINT_JOB,
                base
                + [
                    (IPP_TAG_NAME, "job-name", "relay-observer-probe"),
                    (IPP_TAG_MIMETYPE, "document-format", "application/vnd.cups-raw"),
                ],
                document,
                "/printers/" + quote(args.queue, safe=""),
            ),
            (
                "Create-Job",
                IPP_OP_CREATE_JOB,
                base + [(IPP_TAG_NAME, "job-name", "relay-observer-probe")],
                b"",
                "/printers/" + quote(args.queue, safe=""),
            ),
            (
                "Hold-Job",
                IPP_OP_HOLD_JOB,
                base + job,
                b"",
                "/printers/" + quote(args.queue, safe=""),
            ),
            (
                "Release-Job",
                IPP_OP_RELEASE_JOB,
                base + job,
                b"",
                "/printers/" + quote(args.queue, safe=""),
            ),
            (
                "Cancel-Job",
                IPP_OP_CANCEL_JOB,
                base + job,
                b"",
                "/printers/" + quote(args.queue, safe=""),
            ),
            (
                "Restart-Job",
                IPP_OP_RESTART_JOB,
                base + restart_job,
                b"",
                "/printers/" + quote(args.queue, safe=""),
            ),
            ("CUPS-Get-Devices", IPP_OP_CUPS_GET_DEVICES, base[:2] + [base[3]], b"", "/"),
            (
                "CUPS-Get-Document",
                IPP_OP_CUPS_GET_DOCUMENT,
                base + job + [(IPP_TAG_INTEGER, "document-number", 1)],
                b"",
                "/printers/" + quote(args.queue, safe=""),
            ),
        )
        for request_id, (label, operation, attributes, request_document, path) in enumerate(
            probes, start=1
        ):
            response = _send(
                host=args.host,
                port=args.port,
                path=path,
                username=args.user,
                password=password,
                operation=operation,
                request_id=request_id,
                attributes=attributes,
                document=request_document,
            )
            _expect_denied(label, response)
        _send_document_denial_probe(args, password)
        if args.probe_admin_mutation:
            probe_name = "relay-observer-admin-probe"
            if probe_name in cups.Connection(host=args.host, port=args.port).getPrinters():
                raise RuntimeError(f"reserved admin probe queue already exists: {probe_name}")
            probe_uri = _printer_uri(args.host, args.port, probe_name)
            response = _send(
                host=args.host,
                port=args.port,
                path="/admin/",
                username=args.user,
                password=password,
                operation=IPP_OP_CUPS_ADD_MODIFY_PRINTER,
                request_id=100,
                attributes=_common_attributes(probe_uri, args.user)
                + [
                    (IPP_TAG_URI, "device-uri", "relay-capture://admin-probe"),
                    (IPP_TAG_TEXT, "printer-info", "Relay authorization probe"),
                ],
            )
            _expect_denied("CUPS-Add-Modify-Printer", response)
        print("PASS: observer authorization floor")
        return 0
    finally:
        cups.setPasswordCB(lambda _prompt: "")


if __name__ == "__main__":
    raise SystemExit(main())
