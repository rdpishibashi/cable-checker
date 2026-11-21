"""cable_compare（抽出結果からの突き合わせ）の単体テスト。"""
from model.cable_compare import BOTH, ONLY_393, ONLY_405, cable_map, compare_cables, compare_extracts, count_rows, status


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


def test_count_rows():
    rows = compare_cables({'M1': ['E1'], 'M2': ['E1']}, {'M1': ['D1'], 'M3': ['D1']})
    assert count_rows(rows) == {'405only': 1, '393only': 1, 'both': 1}
    assert count_rows([]) == {'405only': 0, '393only': 0, 'both': 0}


def test_compare_extracts_compares_cables_of_both_sets():
    """守るもの: 抽出結果から、405 と 393 のケーブル番号の突き合わせを作る（図面のグループ分けはしない）。"""
    r405 = [result('E1', ['M1', 'M9'])]
    r393 = [result('D1', ['M1'], title='線番設定表'), result('D2', ['M1', 'M2'], title='SYS', subtitle='ケーブル一覧表'),
            result('D3', [], title='ケーブル製作図')]
    got = compare_extracts(r405, r393)
    assert [(r['cable'], status(r)) for r in got['rows']] == [('M1', BOTH), ('M2', ONLY_393), ('M9', ONLY_405)]
    assert compare_extracts([], []) == {'rows': []}
