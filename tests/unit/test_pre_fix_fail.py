from __future__ import annotations

from pathlib import Path
import sys
import pytest

from mesa_qa.repair.verification import RepairVerifier


def test_missing_file_rejected(tmp_path):
    verifier = RepairVerifier(python_bin=Path("/bin/true"))
    ok, msg = verifier.verify_pre_fix_failure(tmp_path, "tests/missing.py")
    assert not ok
    assert "not found" in msg


def test_empty_or_pseudo_test_rejected(tmp_path):
    verifier = RepairVerifier(python_bin=Path("/bin/true"))
    
    empty_test = tmp_path / "test_empty.py"
    empty_test.write_text("", encoding="utf-8")
    ok, msg = verifier.verify_pre_fix_failure(tmp_path, "test_empty.py")
    assert not ok
    assert "empty" in msg
    
    pseudo_test = tmp_path / "test_pseudo.py"
    pseudo_test.write_text("assert captured_expected == captured_actual", encoding="utf-8")
    ok, msg = verifier.verify_pre_fix_failure(tmp_path, "test_pseudo.py")
    assert not ok
    assert "pseudo-test" in msg


def test_pre_fix_unexpected_pass_rejected(tmp_path):
    verifier = RepairVerifier(python_bin=Path("/bin/true"))
    
    real_test = tmp_path / "test_real.py"
    real_test.write_text("def test_real(): pass", encoding="utf-8")
    
    # /bin/true returns 0 (pass)
    ok, msg = verifier.verify_pre_fix_failure(tmp_path, "test_real.py")
    assert not ok
    assert "unexpectedly PASSED" in msg


def test_pre_fix_genuine_fail_accepted(tmp_path):
    verifier = RepairVerifier(python_bin=Path(sys.executable))
    
    real_test = tmp_path / "test_real.py"
    real_test.write_text("def test_real(): assert 1 == 2", encoding="utf-8")
    
    ok, msg = verifier.verify_pre_fix_failure(tmp_path, "test_real.py")
    assert ok


def test_pre_fix_non_pytest_failure_is_rejected(tmp_path):
    verifier = RepairVerifier(python_bin=Path("/bin/false"))
    real_test = tmp_path / "test_real.py"
    real_test.write_text("def test_real(): assert 1 == 2", encoding="utf-8")

    ok, msg = verifier.verify_pre_fix_failure(tmp_path, "test_real.py")

    assert not ok
    assert "without proving collection" in msg


def test_pre_fix_path_escape_is_rejected(tmp_path):
    verifier = RepairVerifier(python_bin=Path(sys.executable))

    ok, msg = verifier.verify_pre_fix_failure(tmp_path, "../outside.py")

    assert not ok
    assert "escapes candidate" in msg
