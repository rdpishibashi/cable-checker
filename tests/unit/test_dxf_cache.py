"""dxf_cache（ファイル1つの処理の間だけ DXF の解析結果を使い回す）の単体テスト。"""
import threading

import ezdxf

from model import consistency as C
from model import dxf_cache
from tests.unit.test_app import both_sets

dxf_cache.install()


def _dxf(tmp_path, name='a.dxf'):
    doc = ezdxf.new('R2010')
    doc.modelspace().add_line((0, 0), (1, 1))
    path = tmp_path / name
    doc.saveas(path)
    return str(path)


def test_same_document_is_reused_only_inside_parse_once(tmp_path):
    """守るもの: parse_once の中では同じパスの DXF を1回だけ解析して同じ文書を返し、外では毎回解析し直す（文書を持ち越さない）。"""
    path = _dxf(tmp_path)
    with dxf_cache.parse_once():
        assert ezdxf.readfile(path) is ezdxf.readfile(path)
        with dxf_cache.parse_once():                      # 入れ子は外側のキャッシュを使う
            inner = ezdxf.readfile(path)
        assert inner is ezdxf.readfile(path)
    assert ezdxf.readfile(path) is not ezdxf.readfile(path)
    with dxf_cache.parse_once():
        assert ezdxf.readfile(path) is not inner            # 抜けたあとは捨てている（次の parse_once は新しい文書）


def test_calls_with_extra_arguments_and_other_paths_are_not_mixed_up(tmp_path):
    """守るもの: 引数付きの呼び出しはキャッシュしない。パスが違えば別の文書。"""
    a, b = _dxf(tmp_path, 'a.dxf'), _dxf(tmp_path, 'b.dxf')
    with dxf_cache.parse_once():
        assert ezdxf.readfile(a) is not ezdxf.readfile(b)
        assert ezdxf.readfile(a, encoding='utf-8') is not ezdxf.readfile(a)


def test_cache_is_per_thread(tmp_path):
    """守るもの: キャッシュはスレッドごと（Streamlit は利用者ごとに別のスレッドで動く）。別のスレッドは parse_once の外として動く。"""
    path = _dxf(tmp_path)
    seen = []
    with dxf_cache.parse_once():
        main_doc = ezdxf.readfile(path)
        t = threading.Thread(target=lambda: seen.extend([ezdxf.readfile(path), ezdxf.readfile(path)]))
        t.start()
        t.join()
        assert ezdxf.readfile(path) is main_doc
    assert seen[0] is not main_doc and seen[0] is not seen[1]


def test_analyze_uploads_parses_each_file_once(monkeypatch):
    """守るもの: 1つのファイルは、抽出・393 の読み取り・展開接続図の読み取り・領域検出（共有）の全部をあわせて1回だけ解析する
    （以前は 393 が2回、読む 405 が4回。実データ全体で 289 回 → 147 回）。"""
    calls = []
    original = dxf_cache._original_readfile
    monkeypatch.setattr(dxf_cache, '_original_readfile', lambda path, *a, **k: calls.append(path) or original(path, *a, **k))
    items_405, items_393 = both_sets_bytes()
    drawings, extracts, errors = C.analyze_uploads(items_405, items_393)
    assert errors == []
    assert any(d['schematic'] for d in drawings)           # 展開接続図の読み取りまで通っている
    assert len(calls) == len(items_405) + len(items_393) and len(set(calls)) == len(calls)


def both_sets_bytes():
    import tempfile
    import pathlib
    with tempfile.TemporaryDirectory() as tmp:
        return both_sets(pathlib.Path(tmp))
