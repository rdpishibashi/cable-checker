"""excel_output（列幅に使う表示幅）の単体テスト。"""
import pytest

from model.excel_output import display_width


@pytest.mark.parametrize('text, width', [('', 0), ('M39001', 6), ('ケーブル', 8), ('M39001 備考', 11), ('ＡＢ', 4)])
def test_display_width_counts_full_width_as_two(text, width):
    """守るもの: 全角を2・半角を1として数える（Excel の列幅の目安）。"""
    assert display_width(text) == width
