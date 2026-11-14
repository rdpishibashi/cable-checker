"""view/preview_layout.py のテスト。守るもの: シート選択はメニューと分かる狭い幅（最長シート名に合わせる）、表の列幅はデータ長に合わせる。"""
import pandas as pd

from view.preview_layout import MAX_COLUMN_CHARS, chars_to_px, column_widths, sheet_select_width


def test_sheet_select_width_has_a_floor_and_follows_longest_name():
    assert sheet_select_width(['Summary', 'ケーブル一覧']) == 160           # 短い名前は下限
    names = ['Summary', 'ケーブル一覧', 'EE3298-639-02G', 'あ' * 15]
    assert sheet_select_width(names) == chars_to_px(30) + 36                # 全角15字=30 が最長
    assert sheet_select_width(names) < 400                                  # 全幅（数百〜千px）にならない
    assert sheet_select_width([]) == 160


def test_column_widths_fit_data_and_cap_at_max():
    df = pd.DataFrame({'図番': ['EE1234-405-01A'], '備考': ['x' * 200]})
    widths, total = column_widths(df)
    assert widths['図番'] == chars_to_px(14)       # 値 14 文字 > ヘッダー 4（全角2字）
    assert widths['備考'] == chars_to_px(MAX_COLUMN_CHARS)
    assert total == sum(widths.values())
    assert column_widths(pd.DataFrame({'a': []}))[0]['a'] == chars_to_px(1)
