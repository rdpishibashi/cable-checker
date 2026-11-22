"""仕様: 1つのファイルの処理の中で DXF を1回だけ解析する（`doc=` 引数）。`dxf_cache` は撤去した。

**受入条件**（2026-11-22。DXF-extract-labels v3.23.0 の `doc=` 引数を使う。旧 `dxf_cache`〈`ezdxf.readfile` の置き換え〉の撤去）:

- Given 393 と 405 のアップロード、When `analyze_uploads()`、Then `ezdxf.readfile` は**ファイルの数と同じ回数で、各パス1回だけ**
  （抽出・393 の読み取り・展開接続図の読み取り・領域検出〈共有〉をあわせて。以前は 393 が2回、読む 405 が4回。撤去前は `dxf_cache` で同じ結果を得ていた）。
- Given 壊れたファイルが 393 にも 405 にもある、When `analyze_uploads()`、Then 例外にならず、そのファイルだけが `errors` に
  `ValueError: File '…/input.dxf' is not a DXF file.` の形で入り、残りは処理を続ける（読み込みに失敗したら `doc=None` で各処理が読み直し、従来の文言で失敗する）。
- Given `doc` を渡す、When `load`（393・405）・`extract_cables`・`analyze_drawing`・`analyze_schematic`、Then `path` を読み直さず、`doc=None` と同じ結果。
- `ezdxf.readfile` はどこでも置き換えられていない（グローバルな差し替えをしない。複数の利用者のスレッドが同じプロセスで動くため）。
  `model.dxf_cache` は無い。

文書は読み取りのみで使う（複数の処理が同じ文書を共有するので、1つが書き換えると他の結果が変わる）。それは実データの結果が撤去前と完全に一致することで確かめた
（`docs/VERSION_HISTORY.md` 2026-11-22）。
"""
import importlib

import ezdxf
import pytest

from model import cable_extractor, consistency as C, drawing_geometry, schematic_reader
from tests.unit.test_app import both_sets, schematic_bytes

import pathlib
import tempfile


@pytest.fixture
def readfile_calls(monkeypatch):
    """`ezdxf.readfile` の呼び出しパスを記録する（本物に委譲する。`model` のどこも `ezdxf.readfile` を使う）。"""
    calls = []
    real = ezdxf.readfile

    def counting(path, *args, **kwargs):
        calls.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(ezdxf, 'readfile', counting)
    return calls


def _both_sets():
    with tempfile.TemporaryDirectory() as tmp:
        return both_sets(pathlib.Path(tmp))


def test_analyze_uploads_parses_each_file_once(readfile_calls):
    """守るもの: 1つのファイルは、抽出・393 の読み取り・展開接続図の読み取り・領域検出（共有）の全部をあわせて1回だけ解析する。"""
    items_405, items_393 = _both_sets()
    drawings, _extracts, errors = C.analyze_uploads(items_405, items_393)
    assert errors == []
    assert any(d['schematic'] for d in drawings)           # 展開接続図の読み取り・領域検出まで通っている
    assert len(readfile_calls) == len(items_405) + len(items_393) and len(set(readfile_calls)) == len(readfile_calls)


def test_broken_files_fail_with_the_original_message_and_the_rest_continues():
    """守るもの: 読めないファイルは例外にならず `errors` に入る（`doc=None` で各処理が読み直して、従来の文言で失敗する）。残りは処理を続ける。"""
    items_405, items_393 = _both_sets()
    bad = b'not a dxf file\n'
    drawings, extracts, errors = C.analyze_uploads(
        items_405 + [('EE6000-405-01A.dxf', bad)], items_393 + [('EE6000-393-01A.dxf', bad)])
    assert [name for name, _ in errors] == ['EE6000-393-01A.dxf', 'EE6000-405-01A.dxf']
    for _name, message in errors:
        assert message.startswith("ValueError: File '") and message.endswith("input.dxf' is not a DXF file.")
    assert len(drawings) == 3 and {k: len(v) for k, v in extracts.items()} == {'405': 2, '393': 1}


def test_a_passed_doc_is_never_reparsed_and_gives_the_same_result(tmp_path, readfile_calls):
    """守るもの: `doc=` を渡した読み取りは `ezdxf.readfile` を呼ばず、結果は `doc=None` と同じ。"""
    path = tmp_path / 'EE1234-405-01A.dxf'
    path.write_bytes(schematic_bytes(tmp_path))
    doc = ezdxf.readfile(str(path))
    readfile_calls.clear()
    with_doc = (
        drawing_geometry.load(str(path), doc=doc).error,
        schematic_reader.load(str(path), doc=doc)[1] is doc,
        cable_extractor.extract_cables(str(path), path.name, doc=doc),
        C.analyze_drawing(str(path), 'EE1234-405-01A', doc=doc),
        schematic_reader.analyze_schematic(str(path), 'EE1234-405-01A', doc=doc),
    )
    assert readfile_calls == []
    without_doc = (
        drawing_geometry.load(str(path)).error,
        True,
        cable_extractor.extract_cables(str(path), path.name),
        C.analyze_drawing(str(path), 'EE1234-405-01A'),
        schematic_reader.analyze_schematic(str(path), 'EE1234-405-01A'),
    )
    assert with_doc == without_doc


def test_dxf_cache_is_gone_and_readfile_is_not_replaced():
    """守るもの: 暫定の `dxf_cache`（`ezdxf.readfile` のグローバルな置き換え）は無い。"""
    with pytest.raises(ImportError):
        importlib.import_module('model.dxf_cache')
    assert ezdxf.readfile.__module__.startswith('ezdxf')
