"""1つのファイルの処理中は、同じ DXF を1回だけ解析する（モデル層。最下層で、他の model を import しない）。

共有ファイル（`extract_labels.py`・`region_detector.py`）は、それぞれ `ezdxf.readfile(path)` で DXF を解析し直す。
1つの図面を抽出・読み取り・領域検出の複数の処理にかけると、同じ DXF を何度も解析して時間がかかる（解析が全体の約6割）。
共有ファイルを変えずに、`ezdxf.readfile` を、`parse_once()` の中だけ解析結果を使い回す関数に置き換える。

 - キャッシュは**スレッドごと**（Streamlit は利用者ごとに別のスレッドで動く）。`parse_once()` を抜けたら捨てる（文書を持ち越さない）。
 - 解析結果の文書は読み取り専用として使い回す（読み取り側は文書を書き換えない）。
 - `parse_once()` の外・引数付きの呼び出しは、元の `ezdxf.readfile` と同じ。
"""
import contextlib
import os
import threading

import ezdxf

_original_readfile = ezdxf.readfile
_state = threading.local()
_install_lock = threading.Lock()


def _cached_readfile(filename, *args, **kwargs):
    cache = getattr(_state, 'cache', None)
    if cache is None or args or kwargs:
        return _original_readfile(filename, *args, **kwargs)
    key = os.path.abspath(str(filename))
    if key not in cache:
        cache[key] = _original_readfile(filename)
    return cache[key]


def install():
    """`ezdxf.readfile` を置き換える（何度呼んでも1回だけ）。"""
    with _install_lock:
        if ezdxf.readfile is not _cached_readfile:
            ezdxf.readfile = _cached_readfile


@contextlib.contextmanager
def parse_once():
    """この中（同じスレッド）では、同じパスの DXF を1回だけ解析する。入れ子にしてよい（外側のキャッシュを使う）。"""
    outer = getattr(_state, 'cache', None)
    if outer is None:
        _state.cache = {}
    try:
        yield
    finally:
        if outer is None:
            _state.cache = None
