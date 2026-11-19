"""compare_output の単体テスト。守るもの: 比較シートの列・値・空白・行色（D15・D17）、グループのシート名の制約。"""
import io

import openpyxl
import pandas as pd

from model.cable_compare import compare_cables, compare_groups
from model.compare_output import (
    ALL_SHEET, COMPARE_COLUMNS, compare_frame, count_rows, group_sheet_names, status_formats, write_compare_sheet,
)

ROWS = compare_cables(
    {'M39001': ['EE0002-405A', 'EE0001-405A'], 'M65711': ['EE0003-405A']},
    {'M39001': ['EE0009-339A'], 'M05008': ['EE0008-339A', 'EE0007-339A']},
)


def _workbook(sheets):
    """{シート名: 比較の行} を `write_compare_sheet` で書いたブックを読み直す。"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        header_fmt = writer.book.add_format({'bold': True})
        fmts = status_formats(writer.book)
        for name, rows in sheets.items():
            write_compare_sheet(writer, name, rows, header_fmt, fmts)
    return openpyxl.load_workbook(io.BytesIO(output.getvalue()))


def _rows(ws):
    return [[('' if v is None else v) for v in row] for row in ws.iter_rows(values_only=True)]


def test_compare_sheet_has_exactly_three_columns_with_blanks():
    """守るもの: 出力は3列だけ。図番は昇順・「、」区切りで1セル。片方だけのケーブルはもう一方が空白（D15・D16）。"""
    assert list(compare_frame(ROWS).columns) == COMPARE_COLUMNS
    assert _rows(_workbook({ALL_SHEET: ROWS})[ALL_SHEET]) == [
        ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番'],
        ['M05008', '', 'EE0007-339A、EE0008-339A'],
        ['M39001', 'EE0001-405A、EE0002-405A', 'EE0009-339A'],
        ['M65711', 'EE0003-405A', ''],
    ]


def test_row_colors_follow_status():
    """守るもの: 行色（393だけ＝青、両方＝緑、405だけ＝ピンク）と、空白セルにも色が付くこと。"""
    ws = _workbook({ALL_SHEET: ROWS})[ALL_SHEET]
    fills = [[c.fill.fgColor.rgb[-6:] for c in ws[r]] for r in (2, 3, 4)]
    assert fills == [['EAF1FB'] * 3, ['EAF7EC'] * 3, ['FEF1F5'] * 3]


def test_count_rows():
    assert count_rows(ROWS) == {'405only': 1, '393only': 1, 'both': 1}
    assert count_rows([]) == {'405only': 0, '393only': 0, 'both': 0}


def test_empty_comparison_still_produces_a_sheet():
    assert _rows(_workbook({ALL_SHEET: []})[ALL_SHEET]) == [['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番']]


def test_group_sheet_has_the_same_columns_and_colors():
    """守るもの: グループのシートは All Diff と同じ列・行色。393 側はグループの図面だけ、405 側は全図面（グループに無いケーブルは405だけ＝ピンク）。"""
    map_405 = {'M39001': ['EE0001-405A'], 'M65711': ['EE0003-405A']}
    map_393 = {'M39001': ['EE0009-339A', 'EE0005-339A'], 'M05008': ['EE0008-339A']}
    titles = {'EE0009-339A': ('SYS', 'ケーブル一覧表'), 'EE0005-339A': ('', '線番設定表'),
              'EE0008-339A': ('線番設定表', '')}
    groups = {g['name']: g['rows'] for g in compare_groups(map_405, map_393, titles)}
    wb = _workbook({f'{name} Diff': rows for name, rows in groups.items()})
    assert wb.sheetnames == ['ケーブル一覧表 Diff', '線番設定表 Diff']
    assert _rows(wb['線番設定表 Diff']) == [
        ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番'],
        ['M05008', '', 'EE0008-339A'],
        ['M39001', 'EE0001-405A', 'EE0005-339A'],
        ['M65711', 'EE0003-405A', ''],
    ]
    fills = [[c.fill.fgColor.rgb[-6:] for c in wb['線番設定表 Diff'][r]] for r in (2, 3, 4)]
    assert fills == [['EAF1FB'] * 3, ['EAF7EC'] * 3, ['FEF1F5'] * 3]


def test_group_sheet_names_respect_excel_limits():
    """守るもの: 使えない文字の置換・31文字以内・重複（All Diff との衝突を含む）の回避。"""
    names = group_sheet_names(['SYSTEM_I/F_BOX', 'All', 'all', 'A' * 40, 'A' * 40])
    assert names[0] == 'SYSTEM_I_F_BOX Diff'
    assert names[1] == 'All_2 Diff' and names[2] == 'all_3 Diff'
    assert all(len(n) <= 31 for n in names) and len(set(n.lower() for n in names)) == 5
    assert names[3].endswith(' Diff')


def test_group_sheet_names_avoid_the_reserved_sheet_names_of_the_same_workbook():
    """守るもの: 同じブックの別のシート名（reserved。大小文字無視）と衝突するときは `_2` を付けて避ける。"""
    assert group_sheet_names(['x', 'y'], reserved=['X Diff']) == ['x_2 Diff', 'y Diff']
