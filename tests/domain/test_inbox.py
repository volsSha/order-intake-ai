from pathlib import Path

import pytest

from order_intake.config import ROOT
from order_intake.domain.inbox import UnusableInput, list_request_files, parse_request_file


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
