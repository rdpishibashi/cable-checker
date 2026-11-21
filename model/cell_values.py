"""照合で使う小さな文字列の補助（モデル層。streamlit 非依存）。

セルの値（`一覧表` の ` | ` 区切り）の分解、比較用の正規化（空白除去・大文字・NFKC）、色のラベルの判定、線番設定表の接続先の側、
項目の列名。`consistency`（照合）・`highlights`（viewer の強調）・`consistency_output`（Excel）が共有する。
"""
import re
import unicodedata

from .cable_list_table import SEP


def item_column(group, item):
    """整合性チェックの列名（`グループ｜項目`）。"""
    return f'{group}｜{item}'


def squash(s):
    """空白を除いて大文字にする（比較用）。"""
    return re.sub(r'\s+', '', s).upper()



def nfkc_squash(s):
    """NFKC で半角にそろえてから `squash`。"""
    return squash(unicodedata.normalize('NFKC', s or ''))


def code_matches_name(code, name):
    """一覧表の機器符号 code が、図面の文字 name（1行目・括弧の前）と一致するか。`*` は任意の1文字で、**どちらの側にあってもよい**
    （一覧表の `TBR1*`、図面の `CNSHP**`〈モジュールの伏せ字。一覧表は `CNSHPR1`〉）。`GND` を含む符号は `GND` を含む文字と一致。
    展開接続図の端の名前・製作図の文字の両方の照合と、viewer の強調が使う（規則を1か所に置く）。"""
    c = squash(code)
    if 'GND' in c:
        return 'GND' in squash(name)

    def same(a, b):
        return len(a) == len(b) and all(x == y or x == '*' or y == '*' for x, y in zip(a, b))
    first = squash(name.split('\n')[0])
    return same(c, first) or same(c, first.split('(')[0])


def values(cell):
    """一覧表のセルを、複数値（` | `）に分けたリストにする（空は除く）。"""
    return [v.strip() for v in (cell or '').split(SEP) if v.strip()]


def codes(cell):
    """機器符号のセルを、複数値（' | '）とカンマで分けた機器符号のリストにする。"""
    return [c.strip() for v in values(cell) for c in v.split(',') if c.strip()]


_COLOR_CODE = r'(?:BK|BN|RD|OG|YE|GN|BU|VT|GY|WH|PK|TQ|GNYE)'
_COLOR_RE = re.compile(rf'^{_COLOR_CODE}(?:[/-]{_COLOR_CODE})*$')


def is_color(label):
    """色のラベル: 括弧書き（`(BK)`・`(PK/BK)`・`桃(赤)`）か、色の記号だけ（`BU`・`GNYE`）。"""
    s = unicodedata.normalize('NFKC', label or '').strip().upper()
    return '(' in s or bool(_COLOR_RE.match(s))


def side(orient, i):
    """線番設定表の接続先のうち、端末 i（1・2）に対応する側（0=接続先1・1=接続先2）。fwd=接続先1が端末1、rev=接続先1が端末2。"""
    return (i - 1) if orient == 'fwd' else (2 - i)
