from pathlib import Path

import pytest

from order_intake.config import ROOT
from order_intake.domain.inbox import UnusableInput, list_request_files, parse_request_file

pytestmark = pytest.mark.unit


def test_all_request_files_load_except_the_unusable_one():
    files = list_request_files(ROOT / "data" / "requests")
    assert [f.stem for f in files][:5] == ["R1", "R2", "R3", "R4", "R5"]
    loaded, broken = [], []
    for f in files:
        try:
            loaded.append(parse_request_file(f, ROOT))
        except UnusableInput:
            broken.append(f.stem)
    assert broken == ["R12"]
    by_id = {r.request_id: r for r in loaded}
    assert by_id["R1"].body_hash == by_id["R4"].body_hash
    assert by_id["R5"].order_ref == by_id["R9"].order_ref and by_id["R5"].body_hash != by_id["R9"].body_hash
    assert by_id["R11"].attachment_path.endswith("R11-order-form.png")


def test_attachment_outside_folder_is_rejected(tmp_path: Path):
    (tmp_path / "X1.txt").write_text("Request-ID: X1\nOrder-Ref: OX\nAttachment: ../../etc/passwd\n\nHi\n")
    with pytest.raises(UnusableInput, match="outside"):
        parse_request_file(tmp_path / "X1.txt", tmp_path)


@pytest.mark.parametrize("text, problem", [
    ("Order-Ref: OX\n\nHi\n", "missing Request-ID"),
    ("Request-ID: X1\n\nHi\n", "missing Order-Ref"),
    ("Request-ID: X1\nOrder-Ref: OX\n\n  \n", "empty body"),
    ("Request-ID: X1\nOrder-Ref: OX\nAttachment: missing.png\n\nHi\n", "attachment not found"),
])
def test_unusable_request_names_the_problem(tmp_path: Path, text, problem):
    (tmp_path / "X1.txt").write_text(text)
    with pytest.raises(UnusableInput, match=problem):
        parse_request_file(tmp_path / "X1.txt", tmp_path)


@pytest.mark.parametrize("raw", ["Request-ID: X1\nOrder-Ref: OX\n\n2 CAB-1\n",
                                 "Request-ID: X1\r\nOrder-Ref: OX\r\n\r\n2 CAB-1  \r\n\r\n"])
def test_line_endings_and_trailing_space_do_not_change_body_hash(tmp_path: Path, raw):
    (tmp_path / "X1.txt").write_bytes(raw.encode())
    request = parse_request_file(tmp_path / "X1.txt", tmp_path)
    assert request.body == "2 CAB-1"
    assert (request.request_id, request.order_ref) == ("X1", "OX")
