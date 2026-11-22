"""model/cell_values.py（照合の小さな文字列の補助）のテスト。

守るもの: セルの複数値の分解（` | `・カンマ）、比較用の正規化（空白除去・大文字・NFKC）、色のラベルの判定、線番設定表の接続先の側。
consistency はこれらを再定義せず、cell_values のものを使う。
"""
import pytest

from model import cell_values as cv, consistency


def test_values_and_codes_split_multi_values():
    """守るもの: values は ` | ` で、codes は ` | ` とカンマで分け、空は除く。"""
    assert cv.values('A | B |  | C') == ['A', 'B', 'C'] and cv.values(None) == [] and cv.values('') == []
    assert cv.codes('TBR1B, TBR2B | CN1') == ['TBR1B', 'TBR2B', 'CN1'] and cv.codes('') == []


def test_code_matches_name_wildcard_on_either_side():
    """守るもの: 機器符号の `*` は任意の1文字で、一覧表の側（`TBR1*`）にも図面の側（`CNSHP**`。モジュールの伏せ字。一覧表は `CNSHPR1`）にもあってよい。
    長さが違えば一致しない。括弧の前・1行目で比べ、`GND` を含む符号は `GND` を含む文字と一致（以前は一覧表の側の `*` だけで、`CNSHP**` が `CNSHPR1` と ×）。"""
    assert cv.code_matches_name('CNSHPR1', 'CNSHP**') and cv.code_matches_name('TBR1*', 'TBR1B')
    assert cv.code_matches_name('CNSHPR1', 'CNSHPR1(NOTE)') and cv.code_matches_name('CNSHPR1', 'CNSHP**\nSHIELD')
    assert not cv.code_matches_name('CNSHPR1', 'CNSHP*') and not cv.code_matches_name('CNSHPR1', 'CNSHQ**')
    assert cv.code_matches_name('GND BAR', 'GND') and not cv.code_matches_name('GND BAR', 'PE')


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


def test_underscores_joining_words_become_spaces():
    """守るもの: 単語をつなぐアンダースコアは空白にする（図面のラベルは空白の代わりに `＿` を使うことがある。`ＡＣＣＥＳＳＯＲＹ＿ＣＡＢＬＥ`・
    `ＳＹＳＴＥＭ＿Ｉ／Ｆ＿ＢＯＸ＿Ｒ１`）。語の端の `_`・語でない文字の隣の `_` はそのまま。実図面 EE3298-556-51A）。"""
    import unicodedata
    from model.text_utils import underscores_to_spaces
    assert underscores_to_spaces(unicodedata.normalize('NFKC', 'ＡＣＣＥＳＳＯＲＹ＿ＣＡＢＬＥ')) == 'ACCESSORY CABLE'
    assert underscores_to_spaces('SYSTEM_I/F_BOX_R1') == 'SYSTEM I/F BOX R1'
    assert underscores_to_spaces('_A_ (_B)') == '_A_ (_B)'


@pytest.mark.parametrize('text, expected', [
    ('AWG16', {'16'}), ('UL AWG14-2C', {'14'}), ('AWG2-4C', {'2'}), ('AWG 24', {'24'}), ('ＡＷＧ２４', {'24'}),
    ('UL1007 AWG18', {'18'}),                       # AWG に接していない 1007 は拾わない
    ('UL444 type CM 18AWG-15C', {'18'}), ('8AWG', {'8'}),   # 数字が前
    ('AWG20(GNYE)', {'20'}), ('AWG16~AWG10', {'16', '10'}), ('AWG1~AWG4/0', {'1', '4/0'}),
    ('AWG', set()), ('AWG-15C', set()), ('※ケーブルサイズの単位はAWG、長さの単位はmとする。', set()), ('AWG≒mm2^ 配線径変換表', set()), ('', set()), (None, set()),
])
def test_awg_sizes_reads_the_number_next_to_awg(text, expected):
    """守るもの: 図面の文字から、AWG の前後に接した数字（ケーブルのサイズ）だけを取る。前後に数字が続く場合は最長（`AWG2-4C` は 2、`AWG24` は 24）。
    AWG に接していない数字・数字の無い AWG（単位の説明）は取らない。"""
    assert cv.awg_sizes(text) == expected


def test_size_key_drops_a_leading_awg_and_spaces():
    assert cv.size_key('AWG 16') == '16' and cv.size_key(' 24 ') == '24' and cv.size_key('') == ''


def test_device_code_wildcard_is_one_character_per_star_so_a_wire_number_star_does_not_match():
    """守るもの: 機器符号の `*` は1つで1文字（文字数を問わない形にしない）。製作図の `*2`（線番の記号）・`***`（伏せ字）が、一覧表の `CN2`・`RS-232C` に
    一致してはいけない（文字数を問わない形にして全データで測ると、6セルが誤って一致した。M39004・M39005・M65260・M65A06）。モジュールの `*`
    （consistency の展開接続図のモジュールの照合）は別の規則で、数を問わない。"""
    assert not cv.code_matches_name('CN2', '*2') and not cv.code_matches_name('RS-232C', '***')
    assert cv.code_matches_name('CNSHPR1', 'CNSHP**') and not cv.code_matches_name('CNSHPR1', 'CNSHP*')

