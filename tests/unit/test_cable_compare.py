"""cable_compare（抽出結果からの突き合わせとグループ分け）の単体テスト。"""
import pytest

from model.cable_compare import (
    BOTH, ONLY_393, ONLY_405, cable_map, compare_cables, compare_extracts, compare_groups, drawing_titles, group_key,
    status,
)


def result(drawing, cables, title='', subtitle=''):
    return {'drawing_number': drawing, 'title': title, 'subtitle': subtitle,
            'cables': [{'cable': c, 'remark': 'x'} for c in cables]}


def test_cable_map_lists_drawings_sorted_and_skips_drawings_without_cables():
    """守るもの: ケーブル番号 → 図番（昇順・重複なし）。ケーブルが0件の図面はどこにも現れない。"""
    got = cable_map([
        result('EE0002-405A', ['M39001']),
        result('EE0001-405A', ['M39001', 'M39002', 'M39002']),
        result('EE0003-405A', []),
    ])
    assert got == {'M39001': ['EE0001-405A', 'EE0002-405A'], 'M39002': ['EE0001-405A']}
    assert cable_map([]) == {}


def test_drawing_titles_strips_and_treats_none_as_empty():
    got = drawing_titles([result('D1', [], ' T ', ' S '), {'drawing_number': 'D2', 'title': None, 'subtitle': None, 'cables': []}])
    assert got == {'D1': ('T', 'S'), 'D2': ('', '')}


def test_compare_both_and_only_sides_with_blank_lists():
    """守るもの: 片方にしかないケーブルは、もう一方の図番リストが空（D15）。"""
    rows = compare_cables(
        {'M39001': ['EE0001-405A'], 'M65711': ['EE0002-405A']},
        {'M39001': ['EE0009-339A'], 'M05008': ['EE0008-339A']},
    )
    assert rows == [
        {'cable': 'M05008', 'drawings_405': [], 'drawings_393': ['EE0008-339A']},
        {'cable': 'M39001', 'drawings_405': ['EE0001-405A'], 'drawings_393': ['EE0009-339A']},
        {'cable': 'M65711', 'drawings_405': ['EE0002-405A'], 'drawings_393': []},
    ]
    assert [status(r) for r in rows] == [ONLY_393, BOTH, ONLY_405]


def test_compare_sorts_cables_in_abc_order():
    """守るもの: ケーブル番号の昇順（文字コード順）。M06004 は M06004B の前、数字は英字の前。"""
    cables = ['M65C85', 'M06004B', 'M65A61', 'M06004', 'M39001']
    rows = compare_cables({c: ['EE0001-405A'] for c in cables}, {})
    assert [r['cable'] for r in rows] == ['M06004', 'M06004B', 'M39001', 'M65A61', 'M65C85']


def test_compare_sorts_and_dedupes_drawings():
    rows = compare_cables({'M39001': ['EE0002-405A', 'EE0001-405A', 'EE0002-405A']}, {})
    assert rows[0]['drawings_405'] == ['EE0001-405A', 'EE0002-405A']


def test_compare_with_empty_side():
    """守るもの: 片方が0件でも、もう一方のケーブルはすべて「だけ」になる。"""
    rows = compare_cables({}, {'M39001': ['EE0001-339A']})
    assert [status(r) for r in rows] == [ONLY_393]
    assert compare_cables({}, {}) == []


# ---- グループ分け ----------------------------------------------------------------------------
@pytest.mark.parametrize('a, b', [
    (('', 'ケ－ブル一覧表'), ('', 'ケーブル一覧表')),   # 全角ハイフン／長音
    (('', 'ＡＢＣ 一覧'), ('', 'abc一覧')),             # 全角・空白・大小文字
    (('SYS1', 'ケーブル一覧表'), ('SYS2', 'ケーブル一覧表')),  # サブタイトルがあればタイトルは見ない
])
def test_group_key_absorbs_notation_variants(a, b):
    assert group_key(*a) == group_key(*b)


def test_group_key_title_used_only_without_subtitle():
    assert group_key('線番設定表', '') != group_key('線番設定表', 'ケーブル一覧表')
    assert group_key('', '') == ''


def test_compare_groups_subtitle_first_each_drawing_in_one_group():
    """守るもの: サブタイトル優先で、各図面が1グループだけに入る。表記ゆれは1グループ。0件の図面は対象外。"""
    titles = {'D1': ('線番設定表', ''), 'D2': ('SYS_A', 'ケーブル一覧表'), 'D3': ('SYS_A', 'ケ－ブル一覧表'),
              'D4': ('ケーブル製作図', ''), 'D5': ('', '')}
    map_393 = {'M1': ['D1', 'D2'], 'M2': ['D3'], 'M3': ['D5']}  # D4 はケーブル0件
    groups = compare_groups({'M1': ['E1']}, map_393, titles)
    assert [(g['name'], g['drawings']) for g in groups] == [
        ('ケーブル一覧表', ['D2', 'D3']), ('線番設定表', ['D1']), ('（タイトルなし）', ['D5'])]
    by = {g['name']: g['rows'] for g in groups}
    assert [(r['cable'], r['drawings_405'], r['drawings_393']) for r in by['ケーブル一覧表']] == [
        ('M1', ['E1'], ['D2']), ('M2', [], ['D3'])]
    assert [r['cable'] for r in by['線番設定表']] == ['M1']


def test_compare_groups_unknown_drawing_and_no_cables():
    """守るもの: Summary に無い図面はタイトルなしグループ。ケーブルが全く無ければグループなし。"""
    assert [g['name'] for g in compare_groups({}, {'M1': ['D9']}, {})] == ['（タイトルなし）']
    assert compare_groups({'M1': ['E1']}, {}, {}) == []


def test_compare_extracts_compares_cables_and_groups_the_393_drawings():
    """守るもの: 抽出結果から、全体の突き合わせと、393 のサブタイトル優先のグループ別の突き合わせを1回で作る（405 は全体と突き合わせる）。"""
    r405 = [result('E1', ['M1', 'M9'])]
    r393 = [result('D1', ['M1'], title='線番設定表'), result('D2', ['M1', 'M2'], title='SYS', subtitle='ケーブル一覧表'),
            result('D3', [], title='ケーブル製作図')]
    got = compare_extracts(r405, r393)
    assert [(r['cable'], status(r)) for r in got['rows']] == [('M1', BOTH), ('M2', ONLY_393), ('M9', ONLY_405)]
    assert [(g['name'], g['drawings']) for g in got['groups']] == [('ケーブル一覧表', ['D2']), ('線番設定表', ['D1'])]
    assert [r['cable'] for r in got['groups'][0]['rows']] == ['M1', 'M2', 'M9']        # グループの比較は 405 全体と
    assert compare_extracts([], []) == {'rows': [], 'groups': []}
