from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.download_kaggle_kernel_critic_artifacts import (
    EXPECTED_NAMES,
    parse_kernel_ref,
    select_expected_urls,
)


def _file(name: str, url: str) -> SimpleNamespace:
    return SimpleNamespace(file_name=name, url=url)


def test_parse_kernel_ref_requires_username_and_slug() -> None:
    assert parse_kernel_ref("user/kernel") == ("user", "kernel")
    for value in ("kernel", "/kernel", "user/", "user/a/b"):
        with pytest.raises(Exception, match="exactly"):
            parse_kernel_ref(value)


def test_select_expected_urls_ignores_source_snapshot_and_requires_closed_set() -> None:
    files = [_file(f"AI_guru/{name}", f"https://example.test/{name}") for name in EXPECTED_NAMES]
    files.append(_file("AI_guru/ai_guru_source.tar.gz", "https://example.test/source"))
    selected = select_expected_urls(files)
    assert set(selected) == EXPECTED_NAMES
    assert all(url.startswith("https://example.test/") for url in selected.values())

    with pytest.raises(ValueError, match="missing expected artifact"):
        select_expected_urls(files[:-2])
    duplicate = files + [_file("other/qwen14_grounded_critic_results_v2.jsonl", "https://example.test/other")]
    with pytest.raises(ValueError, match="ambiguous artifact"):
        select_expected_urls(duplicate)
