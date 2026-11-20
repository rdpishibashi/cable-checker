"""model/symbols.py（照合の記号の定義）のテスト。

守るもの: 記号・重さ・色の定義が1か所（symbols）にあり、重い順は × ＞ ？ ＞ △ ＞ ○ ＞ － ＞ 関連。Excel のセル色・viewer の文字色・色の名前・
凡例の文言が全記号に揃っている。consistency・cable_preview は同じ値を再定義せず、symbols のものを使う。
"""
from model import cable_preview, consistency, symbols


def test_severity_order_and_worst_and_rank():
    """守るもの: 重い順は × ？ △ ○ － 関連。worst() は最も重い記号（空なら既定の －）、rank() は小さいほど重い。"""
    assert symbols.BY_SEVERITY == ['×', '？', '△', '○', '－', '関連']
    assert symbols.worst(['○', '△', '－']) == '△' and symbols.worst(['○', '×', '？']) == '×' and symbols.worst([]) == '－'
    assert symbols.worst(['関連', '－']) == '－'
    assert [symbols.rank(s) for s in symbols.BY_SEVERITY] == list(range(6))


def test_every_symbol_has_colors_names_and_labels():
    """守るもの: 全記号に viewer の色・凡例の文言があり、Excel のセル色は ○△×？（－は色なし）に、色の名前は全記号にある。"""
    for s in symbols.SYMBOLS:
        assert s in symbols.VIEWER_COLORS and s in symbols.LABELS and s in symbols.EXCEL_COLOR_NAMES and s in symbols.VIEWER_COLOR_NAMES
    assert set(symbols.EXCEL_COLORS) == {'○', '△', '×', '？'}
    assert symbols.VIEWER_COLORS['関連'] == symbols.VIEWER_COLORS['－'] and symbols.LABELS['関連']


def test_other_modules_use_the_symbols_definitions():
    """守るもの: consistency・cable_preview は記号・重さ・色を再定義せず、symbols と同じ（同一の）オブジェクトを使う。"""
    assert consistency.SYMBOLS is symbols.SYMBOLS and consistency.SEVERITY is symbols.SEVERITY and consistency.OK is symbols.OK
    assert cable_preview.SYMBOL_TEXT_COLORS is symbols.VIEWER_COLORS and cable_preview.RELATED is symbols.RELATED
