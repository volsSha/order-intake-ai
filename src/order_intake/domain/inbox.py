"""Load email-style request files (headers, blank line, body)."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class UnusableInput(Exception):
    pass


@dataclass(frozen=True)
class IncomingRequest:
    request_id: str
    order_ref: str
    body: str
    headers: dict[str, str]
    raw_text: str
    source_path: str
    attachment_path: str | None

    @property
    def body_hash(self) -> str:
        return hashlib.sha256(normalize_body(self.body).encode()).hexdigest()

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.raw_text.encode()).hexdigest()


def normalize_body(body: str) -> str:
    lines = [line.rstrip() for line in body.replace("\r\n", "\n").strip().split("\n")]
    return "\n".join(lines)


def natural_key(path: Path) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", path.stem))


def list_request_files(directory: Path) -> list[Path]:
    return sorted((p for p in directory.glob("*.txt") if p.is_file()), key=natural_key)


def parse_headers(text: str) -> tuple[dict[str, str], str]:
    text = text.replace("\r\n", "\n")
    head, sep, body = text.partition("\n\n")
    headers = {}
    for line in head.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return headers, body if sep else ""


def request_id_for(path: Path, raw_text: str | None = None) -> str:
    if raw_text:
        headers, _ = parse_headers(raw_text)
        if headers.get("request-id"):
            return headers["request-id"]
    return path.stem


def parse_request_file(path: Path, root: Path) -> IncomingRequest:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UnusableInput(f"cannot read file: {exc}") from exc
    headers, body = parse_headers(raw)
    problems = []
    if not headers.get("request-id"):
        problems.append("missing Request-ID header")
    if not headers.get("order-ref"):
        problems.append("missing Order-Ref header")
    attachment = None
    if headers.get("attachment"):
        candidate = (path.parent / headers["attachment"]).resolve()
        if path.parent.resolve() not in candidate.parents:
            problems.append("attachment path outside the request folder")
        elif not candidate.is_file():
            problems.append(f"attachment not found: {headers['attachment']}")
        elif candidate.suffix.lower() not in IMAGE_TYPES:
            problems.append(f"unsupported attachment type: {candidate.suffix}")
        else:
            attachment = str(candidate.relative_to(root))
    if not normalize_body(body) and not attachment:
        problems.append("empty body and no attachment")
    if problems:
        raise UnusableInput("; ".join(problems))
    return IncomingRequest(
        request_id=headers["request-id"],
        order_ref=headers["order-ref"],
        body=normalize_body(body),
        headers=headers,
        raw_text=raw,
        source_path=str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
        attachment_path=attachment,
    )
