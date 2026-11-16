"""Tests for the short-lived server-side PDF cache."""

import os
import time

import pytest

from app.services.latex import pdf_cache


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_cache, "cache_dir", lambda: tmp_path)
    return tmp_path


def test_put_then_get():
    pdf_cache.put(1, "tex-v1", b"%PDF one")
    assert pdf_cache.get(1, "tex-v1") == b"%PDF one"


def test_changed_latex_is_a_miss():
    pdf_cache.put(1, "tex-v1", b"%PDF one")
    assert pdf_cache.get(1, "tex-v2") is None


def test_put_from_a_path(tmp_path):
    src = tmp_path / "compiled.bin"
    src.write_bytes(b"%PDF from path")
    pdf_cache.put(3, "tex", str(src))
    assert pdf_cache.get(3, "tex") == b"%PDF from path"


def test_one_live_pdf_per_job(isolated_cache):
    pdf_cache.put(1, "tex-v1", b"%PDF one")
    pdf_cache.put(1, "tex-v2", b"%PDF two")
    assert len(list(isolated_cache.glob("job1_*.pdf"))) == 1
    assert pdf_cache.get(1, "tex-v2") == b"%PDF two"


def test_expired_entries_are_purged(isolated_cache):
    pdf_cache.put(1, "tex", b"%PDF old")
    (path,) = isolated_cache.glob("job1_*.pdf")
    old = time.time() - 24 * 3600
    os.utime(path, (old, old))
    assert pdf_cache.get(1, "tex") is None
    assert not path.exists()


def test_evict_only_touches_that_job():
    pdf_cache.put(1, "a", b"%PDF 1")
    pdf_cache.put(12, "b", b"%PDF 12")
    pdf_cache.evict(1)
    assert pdf_cache.get(1, "a") is None
    assert pdf_cache.get(12, "b") == b"%PDF 12"
