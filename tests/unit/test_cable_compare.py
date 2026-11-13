"""cable_compare（Excel の読み込みと突き合わせ）の単体テスト。

入力の xlsx は excel_output.build_workbook() で作る（出力の形と入力の仕様が食い違わないことも守る）。
"""
import io

import openpyxl
import pytest

from model.cable_compare import (
    BOTH, ONLY_393, ONLY_405, compare_cables, compare_groups, group_key, read_cable_list,
    read_drawing_titles, status,
)
from model.excel_output import build_workbook


def result(drawing, cables):
    return {'drawing_number': drawing, 'title': '', 'subtitle': '',
            'cables': [{'cable': c, 'remark': 'x'} for c in cables]}


def xlsx(results):
    return io.BytesIO(build_workbook(results))


def raw_xlsx(sheets):
    """{シート名: [行…]} から素の xlsx を作る（出力の形に従わない入力用）。"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_read_cable_list_round_trips_build_workbook():
    """守るもの: build_workbook() の出力を、そのまま比較の入力として読める（出力と入力の仕様のずれ防止）。"""
    got = read_cable_list(xlsx([
        result('EE0001-405A', ['M39001', 'M39002']),
        result('EE0002-405A', ['M39001']),
        result('EE0003-405A', []),
    ]), '展開接続図のExcel')
    assert got == {'M39001': ['EE0001-405A', 'EE0002-405A'], 'M39002': ['EE0001-405A']}


def test_read_cable_list_normalizes_and_skips_blank_rows():
    """守るもの: ケーブル番号は全角→半角・空白除去・大文字化。空の図番・番号の行、重複行は無視。備考は読まない。"""
    f = raw_xlsx({'ケーブル一覧': [
        ['図番', 'ケーブル番号', '備考'],
        ['EE0001-405A', ' ｍ３９００１ ', 'x'],
        ['EE0001-405A', 'M39001', ''],
        ['EE0002-405A', 'm39001', None],
        [None, 'M39002', None],
        ['EE0003-405A', None, None],
        ['EE0003-405A', '', None],
    ]})
    assert read_cable_list(f, 'A') == {'M39001': ['EE0001-405A', 'EE0002-405A']}


def test_read_cable_list_header_only_is_empty_not_error():
    """守るもの: 見出しだけ（ケーブル0件）は正常で、空の辞書になる。"""
    assert read_cable_list(raw_xlsx({'ケーブル一覧': [['図番', 'ケーブル番号', '備考']]}), 'A') == {}


def test_read_cable_list_finds_columns_by_name_not_position():
    f = raw_xlsx({'ケーブル一覧': [['備考', 'ケーブル番号', '図番'], ['r', 'M39001', 'EE0001-405A']]})
    assert read_cable_list(f, 'A') == {'M39001': ['EE0001-405A']}


@pytest.mark.parametrize('make, expected', [
    (lambda: raw_xlsx({'Summary': [['図番']]}), 'ケーブル一覧」シートがありません'),
    (lambda: raw_xlsx({'ケーブル一覧': [['図番', '備考']]}), '「ケーブル番号」列がありません'),
    (lambda: raw_xlsx({'ケーブル一覧': [['ケーブル番号', '備考']]}), '「図番」列がありません'),
    (lambda: raw_xlsx({'ケーブル一覧': []}), '列がありません'),
    (lambda: io.BytesIO(b'not an xlsx'), '読み込めません'),
])
def test_read_cable_list_errors_name_the_file_and_the_problem(make, expected):
    """守るもの: 読めない入力は ValueError で、どのファイルの何が悪いかが分かる文言になる。"""
    with pytest.raises(ValueError) as e:
        read_cable_list(make(), '展開接続図のExcel')
    assert '展開接続図のExcel' in str(e.value)
    assert expected in str(e.value)


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
def titled(drawing, title, subtitle, cables):
    return {'drawing_number': drawing, 'title': title, 'subtitle': subtitle,
            'cables': [{'cable': c, 'remark': ''} for c in cables]}


def test_read_drawing_titles_round_trips_build_workbook():
    """守るもの: Summary シートの図番・タイトル・サブタイトルを読める（出力と入力の仕様のずれ防止）。"""
    src = xlsx([titled('EE0001-339A', 'T', 'ケーブル一覧表', ['M39001']), titled('EE0002-339A', '線番設定表', '', [])])
    assert read_drawing_titles(src, 'x') == {'EE0001-339A': ('T', 'ケーブル一覧表'), 'EE0002-339A': ('線番設定表', '')}


def test_read_drawing_titles_errors():
    with pytest.raises(ValueError, match='「Summary」シートがありません'):
        read_drawing_titles(raw_xlsx({'Sheet1': [['a']]}), '393')
    with pytest.raises(ValueError, match='「サブタイトル」列がありません'):
        read_drawing_titles(raw_xlsx({'Summary': [['図番', 'タイトル']]}), '393')


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
