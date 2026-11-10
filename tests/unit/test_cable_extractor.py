"""cable_extractor（ケーブル番号の判定・入力フィルタ・同名除去）の単体テスト。

合成ラベルだけを使う。実データでの確認は tests/regression/spec/ にある。
"""
import pytest

from model.cable_extractor import (
    dedupe_by_name,
    extract_cables,
    find_cables,
    is_dwg_filename,
    is_target_filename,
)


def cables(labels):
    return [c['cable'] for c in find_cables(labels)]


@pytest.mark.parametrize('label, expected', [
    # b1 通常・空白ゆれ
    ('W No.M39672', ['M39672']),
    ('W No. M39001', ['M39001']),
    ('W No.  M39001', ['M39001']),
    # b3 全角（NFKC で半角になる）
    ('Ｗ　Ｎｏ．Ｍ６５２５４', ['M65254']),
    # b4 末尾英字・中間英字
    ('W No.M06004B', ['M06004B']),
    ('W No.M65A61', ['M65A61']),
    ('W No.M65C85', ['M65C85']),
    # ラベル全体が番号だけ
    ('M39001', ['M39001']),
    ('M06004B', ['M06004B']),
    # b6 改訂注記は拾わない（W No. なし・全体一致しない）
    ('M06004B追加/KURIHARA', []),
    ('M06030追加/SUZUKI', []),
    # b8 M 始まりの非ケーブル
    ('MC121A1', []),
    ('M1 DRV', []),
    ('MONITOR', []),
    # 番号の直後が英数字続きなら別の語
    ('W No.M39001XYZ', []),
    # DWG No. はケーブルではない
    ('DWG No.', []),
    ('', []),
])
def test_label_patterns(label, expected):
    """守るもの: ケーブル番号と、それに似た非ケーブルラベルの判別規則。"""
    assert cables([label]) == expected


def test_m06004b_and_m06004_are_distinct_cables():
    """守るもの: M06004B と M06004 は別のケーブル（D2）。"""
    assert cables(['W No.M06004', 'W No.M06004B']) == ['M06004', 'M06004B']


def test_duplicates_in_one_drawing_are_unique():
    """守るもの: 同一図面内の重複は1行にまとまる（D3、b7）。"""
    assert cables(['W No. M39009'] * 3) == ['M39009']


def test_result_is_sorted():
    assert cables(['W No.M39002', 'W No.M06004', 'W No.M39001']) == ['M06004', 'M39001', 'M39002']


@pytest.mark.parametrize('label, remark', [
    ('W No.M65202 ACCESSORY CABLE', 'ACCESSORY CABLE'),
    ('W No.M06008（CC050VZRB）', '(CC050VZRB)'),  # 全角括弧は NFKC で半角になる
    ('W No.M39001', ''),
])
def test_remark_is_text_after_number(label, remark):
    """守るもの: 番号の後ろに続く文字列が備考列に入る（b5）。"""
    assert find_cables([label]) == [{'cable': label_cable(label), 'remark': remark}]


def label_cable(label):
    return find_cables([label])[0]['cable']


def test_remarks_of_same_cable_are_deduplicated_and_joined():
    """守るもの: 同一番号の備考は重複除去して「、」で連結する。"""
    result = find_cables([
        'W No.M65202 ACCESSORY CABLE',
        'W No.M65202 ACCESSORY CABLE',
        'W No.M65202 (X)',
        'W No.M65202',
    ])
    assert result == [{'cable': 'M65202', 'remark': 'ACCESSORY CABLE、(X)'}]


def test_remark_keeps_original_case():
    assert find_cables(['W No.M65202 accessory cable'])[0]['remark'] == 'accessory cable'


def test_two_cables_in_one_label_split_remarks():
    """守るもの: 1ラベルに複数あっても全部拾い、備考は次の W No. の手前まで。"""
    assert find_cables(['W No.M39001 AAA W No.M39002 BBB']) == [
        {'cable': 'M39001', 'remark': 'AAA'},
        {'cable': 'M39002', 'remark': 'BBB'},
    ]


@pytest.mark.parametrize('name, ok', [
    ('EE6892-405-08A.dxf', True),
    ('EE5207-405A.dxf', True),
    ('ee6892-405-08a.DXF', True),
    ('.DS_Store', False),
    ('readme.txt', False),
    ('EE6892-405-08A.pdf', False),
    ('EE6892-405-08A_copy.dxf', False),
    ('図面.dxf', False),
    ('sub/EE6892-405-08A.dxf', True),
])
def test_is_target_filename(name, ok):
    """守るもの: 入力フィルタ（a3）。図番に完全一致する .dxf だけを対象にする。"""
    assert is_target_filename(name) is ok


@pytest.mark.parametrize('name, ok', [
    ('EE3298-574-01A.DWG', True),
    ('EE3298-574-01A.dwg', True),
    ('sub/EE3298-574-01A.DWG', True),
    ('memo.dwg', False),                 # 図番形式でない名前は DWG 警告の対象にしない
    ('EE3298-574-01A.dxf', False),
    ('EE3298-574-01A', False),
])
def test_is_dwg_filename(name, ok):
    """守るもの: 図番形式の .dwg だけを見分ける（D11）。DWG は読めないので警告を出すために使う。
    DXF 用の is_target_filename とは排他的（同じ名前が両方に当たらない）。"""
    assert is_dwg_filename(name) is ok
    assert not (is_dwg_filename(name) and is_target_filename(name))


def test_dedupe_by_name_keeps_first():
    """守るもの: 同名は先の1件だけ残し、後ろはスキップ名として返す（a5）。"""
    keep, skipped = dedupe_by_name(['A.dxf', 'B.dxf', 'a.DXF', 'dir/A.dxf', 'C.dxf'])
    assert keep == [0, 1, 4]
    assert skipped == ['a.DXF', 'dir/A.dxf']


def test_extract_cables_uses_filename_and_blank_title(monkeypatch, tmp_path):
    """守るもの: 図番は元ファイル名（拡張子なし）。タイトル未取得は空文字（b9）。"""
    import model.cable_extractor as ce

    seen = {}

    def fake_extract(path, **kwargs):
        seen.update(kwargs)
        return ['W No.M39001'], {'title': None, 'subtitle': None}

    monkeypatch.setattr(ce, 'extract_labels', fake_extract)
    result = extract_cables(str(tmp_path / 'tmp123.dxf'), 'EE6892-405-08A.dxf')
    assert result == {
        'drawing_number': 'EE6892-405-08A',
        'title': '',
        'subtitle': '',
        'cables': [{'cable': 'M39001', 'remark': ''}],
    }
    assert seen['extract_drawing_numbers_option'] is True
    assert seen['extract_title_option'] is True
    assert seen['original_filename'] == 'EE6892-405-08A.dxf'


def test_extract_cables_raises_when_dxf_unreadable(monkeypatch, tmp_path):
    """守るもの: 共有の extract_labels() は読み込み失敗を例外にせず info['error'] で返す。
    それを「ケーブル0件」として黙って出さず、例外にして呼び出し側にエラー表示させる。"""
    import model.cable_extractor as ce

    monkeypatch.setattr(ce, 'extract_labels', lambda path, **kw: ([], {'error': 'not a DXF'}))
    with pytest.raises(ValueError, match='not a DXF'):
        extract_cables(str(tmp_path / 'x.dxf'), 'EE1234-405-01A.dxf')
