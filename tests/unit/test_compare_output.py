"""compare_output の単体テスト。守るもの: 比較 Excel の列・値・空白・行色・Summary の件数（D15・D17）。"""
import io

import openpyxl

from model.cable_compare import compare_cables
from model.compare_output import COMPARE_FILENAME, build_compare_workbook

ROWS = compare_cables(
    {'M39001': ['EE0002-405A', 'EE0001-405A'], 'M65711': ['EE0003-405A']},
    {'M39001': ['EE0009-339A'], 'M05008': ['EE0008-339A', 'EE0007-339A']},
)


def _wb():
    return openpyxl.load_workbook(io.BytesIO(build_compare_workbook(ROWS, 'a405.xlsx', 'b393.xlsx')))


def _rows(ws):
    return [[('' if v is None else v) for v in row] for row in ws.iter_rows(values_only=True)]


def test_sheets_and_filename():
    assert _wb().sheetnames == ['Summary', 'ケーブル比較']
    assert COMPARE_FILENAME == 'cable_comparison.xlsx'


def test_compare_sheet_has_exactly_three_columns_with_blanks():
    """守るもの: 出力は3列だけ。図番は昇順・「、」区切りで1セル。片方だけのケーブルはもう一方が空白（D15・D16）。"""
    assert _rows(_wb()['ケーブル比較']) == [
        ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番'],
        ['M05008', '', 'EE0007-339A、EE0008-339A'],
        ['M39001', 'EE0001-405A、EE0002-405A', 'EE0009-339A'],
        ['M65711', 'EE0003-405A', ''],
    ]


def test_row_colors_follow_status():
    """守るもの: 行色（393だけ＝青、両方＝緑、405だけ＝ピンク）と、空白セルにも色が付くこと。"""
    ws = _wb()['ケーブル比較']
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
    assert _rows(wb['ケーブル比較']) == [['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番']]
