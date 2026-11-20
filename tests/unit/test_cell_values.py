"""model/cell_values.py（照合の小さな文字列の補助）のテスト。

守るもの: セルの複数値の分解（` | `・カンマ）、比較用の正規化（空白除去・大文字・NFKC）、色のラベルの判定、線番設定表の接続先の側。
consistency はこれらを再定義せず、cell_values のものを使う。
"""
from model import cell_values as cv, consistency


def test_values_and_codes_split_multi_values():
    """守るもの: values は ` | ` で、codes は ` | ` とカンマで分け、空は除く。"""
    assert cv.values('A | B |  | C') == ['A', 'B', 'C'] and cv.values(None) == [] and cv.values('') == []
    assert cv.codes('TBR1B, TBR2B | CN1') == ['TBR1B', 'TBR2B', 'CN1'] and cv.codes('') == []


def test_squash_normalises_width_space_and_case():
    """守るもの: squash は空白除去・大文字、nfkc_squash は全角を半角にそろえてから同じ。"""
    assert cv.squash(' ab c\n') == 'ABC' and cv.nfkc_squash('ＡＢ ｃ') == 'ABC'


def test_is_color_label_and_side_and_item_column():
    """守るもの: 色のラベルは括弧書きか色の記号だけ。side は fwd で接続先1=端末1、rev で接続先1=端末2。"""
    assert all(cv.is_color(x) for x in ('(BK)', '(PK/BK)', '桃(赤)', 'BU', 'GNYE', 'BK-WH'))
    assert not any(cv.is_color(x) for x in ('S121C', 'CN1', '1', ''))
    assert (cv.side('fwd', 1), cv.side('fwd', 2), cv.side('rev', 1), cv.side('rev', 2)) == (0, 1, 1, 0)
    assert cv.item_column('端末1', '機器符号') == '端末1｜機器符号'


def test_consistency_uses_the_shared_helpers():
    """守るもの: consistency は同じ補助を再定義せず、cell_values のものを使う（同一のオブジェクト）。"""
    assert consistency._values is cv.values and consistency._codes is cv.codes and consistency._squash is cv.squash
    assert consistency._is_color is cv.is_color and consistency._side is cv.side and consistency.item_column is cv.item_column
