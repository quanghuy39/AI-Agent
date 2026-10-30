"""Kiem tra answers/ khong bao gio duoc ghi vao cache/ (storage/r2.py). R2 gia; khong goi mang.

Chay: python -m pytest tests/test_storage_answers.py -v
"""

import pytest

from storage import r2

FILES = {"answers/bai9/answer.csv": b"a,b\n1,2\n", "input/bai9/T.csv": b"x\n1\n"}


class FakeClient:
    def head_object(self, Bucket, Key):
        return {"ETag": '"e1"'}

    def download_file(self, bucket, key, path):
        with open(path, "wb") as f:
            f.write(FILES[key])


@pytest.fixture
def cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(r2, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(r2, "_read_client", lambda: FakeClient())
    monkeypatch.setattr(r2, "get_r2_settings", lambda: {"R2_BUCKET": "b", "R2_INPUT_PREFIX": "input/",
                                                        "R2_OUTPUT_PREFIX": "output/"})
    return cache_dir


def answers_paths(cache_dir):
    return [p for p in cache_dir.rglob("*") if "answers" in (x.lower() for x in p.parts)] if cache_dir.exists() else []


@pytest.mark.parametrize("key", ["answers/bai9/answer.csv", "Answers/bai9/answer.csv"])
def test_download_with_cache_refuses_answers_into_cache(cache, key):
    with pytest.raises(ValueError, match="cache/"):
        r2.download_with_cache(key, grader=True)  # mac dinh: cache/
    with pytest.raises(ValueError, match="cache/"):
        r2.download_with_cache(key, cache_dir=cache / "sub", grader=True)  # cache_dir nam trong cache/
    assert answers_paths(cache) == []


def test_download_with_cache_still_needs_grader(cache, tmp_path):
    with pytest.raises(ValueError, match="Không được đọc"):
        r2.download_with_cache("answers/bai9/answer.csv", cache_dir=tmp_path / "tmp")
    assert r2.download_with_cache("input/bai9/T.csv").read_bytes() == FILES["input/bai9/T.csv"]  # input/ van vao cache


def test_grader_download_uses_temp_and_cleans_up(cache):
    with r2.grader_download("answers/bai9/answer.csv") as path:
        assert path.read_bytes() == FILES["answers/bai9/answer.csv"]
        assert not str(path.resolve()).startswith(str(cache.resolve()))
        tmp = path.parents[2]  # <tmp>/answers/bai9/answer.csv
    assert not tmp.exists()

    with pytest.raises(RuntimeError):
        with r2.grader_download("answers/bai9/answer.csv") as path:
            tmp = path.parents[2]
            raise RuntimeError("lỗi giữa chừng")
    assert not tmp.exists()  # xoa ca khi loi
    assert answers_paths(cache) == []


def test_read_table_answers_goes_through_temp(cache):
    df = r2.read_table("answers/bai9/answer.csv", grader=True, dtype=str)
    assert list(df.columns) == ["a", "b"] and df.iloc[0].tolist() == ["1", "2"]
    assert answers_paths(cache) == []
    with pytest.raises(ValueError, match="Không được đọc"):
        r2.read_table("answers/bai9/answer.csv")


def test_cli_head_grader_does_not_write_cache(cache, capsys):
    assert r2.main(["head", "answers/bai9/answer.csv", "--grader"]) == 0
    assert "Số dòng: 1, số cột: 2" in capsys.readouterr().out
    assert answers_paths(cache) == []
