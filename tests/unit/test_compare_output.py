"""compare_output の単体テスト。守るもの: 比較 Excel の列・値・空白・行色・Summary の件数（D15・D17）。"""
import io

import openpyxl

from model.cable_compare import compare_cables
from model.compare_output import COMPARE_FILENAME, build_compare_workbook, group_sheet_names
from model.cable_compare import compare_groups

ROWS = compare_cables(
    {'M39001': ['EE0002-405A', 'EE0001-405A'], 'M65711': ['EE0003-405A']},
    {'M39001': ['EE0009-339A'], 'M05008': ['EE0008-339A', 'EE0007-339A']},
)


def _wb():
    return openpyxl.load_workbook(io.BytesIO(build_compare_workbook(ROWS, 'a405.xlsx', 'b393.xlsx')))


def _rows(ws):
    return [[('' if v is None else v) for v in row] for row in ws.iter_rows(values_only=True)]


def test_sheets_and_filename():
    assert _wb().sheetnames == ['Summary', 'All Diff']
    assert COMPARE_FILENAME == 'cable_comparison.xlsx'


def test_compare_sheet_has_exactly_three_columns_with_blanks():
    """守るもの: 出力は3列だけ。図番は昇順・「、」区切りで1セル。片方だけのケーブルはもう一方が空白（D15・D16）。"""
    assert _rows(_wb()['All Diff']) == [
        ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番'],
        ['M05008', '', 'EE0007-339A、EE0008-339A'],
        ['M39001', 'EE0001-405A、EE0002-405A', 'EE0009-339A'],
        ['M65711', 'EE0003-405A', ''],
    ]


def test_row_colors_follow_status():
    """守るもの: 行色（393だけ＝青、両方＝緑、405だけ＝ピンク）と、空白セルにも色が付くこと。"""
    ws = _wb()['All Diff']
    fills = [[c.fill.fgColor.rgb[-6:] for c in ws[r]] for r in (2, 3, 4)]
    assert fills == [['EAF1FB'] * 3, ['EAF7EC'] * 3, ['FEF1F5'] * 3]


def test_summary_counts():
    rows = _rows(_wb()['Summary'])
    assert rows == [
        ['項目', '値'],
        ['展開接続図のExcel', 'a405.xlsx'],
        ['ケーブル製作図のExcel', 'b393.xlsx'],
        ['展開接続図のケーブル数', 2],
        ['ケーブル製作図のケーブル数', 2],
        ['両方にあるケーブル数', 1],
        ['展開接続図だけにあるケーブル数', 1],
        ['ケーブル製作図だけにあるケーブル数', 1],
    ]


def test_empty_comparison_still_produces_workbook():
    wb = openpyxl.load_workbook(io.BytesIO(build_compare_workbook([], 'a.xlsx', 'b.xlsx')))
    assert _rows(wb['All Diff']) == [['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番']]


def test_group_sheets_follow_all_diff_with_same_columns_and_colors():
    """守るもの: All Diff の後ろに `<グループ名> Diff` が並び、列・行色は All Diff と同じ。
    393 側はグループの図面だけ、405 側は全図面（グループに無いケーブルは405だけ＝ピンク）。"""
    map_405 = {'M39001': ['EE0001-405A'], 'M65711': ['EE0003-405A']}
    map_393 = {'M39001': ['EE0009-339A', 'EE0005-339A'], 'M05008': ['EE0008-339A']}
    titles = {'EE0009-339A': ('SYS', 'ケーブル一覧表'), 'EE0005-339A': ('', '線番設定表'),
              'EE0008-339A': ('線番設定表', '')}
    groups = compare_groups(map_405, map_393, titles)
    wb = openpyxl.load_workbook(io.BytesIO(build_compare_workbook(
        compare_groups(map_405, map_393, titles)[0]['rows'], 'a', 'b', groups)))
    assert wb.sheetnames == ['Summary', 'All Diff', 'ケーブル一覧表 Diff', '線番設定表 Diff']
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
