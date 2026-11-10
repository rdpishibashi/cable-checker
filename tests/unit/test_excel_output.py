"""excel_output の単体テスト。守るもの: Summary／ケーブル一覧／図番別シートの形と値（D5）。"""
import io

import openpyxl

from model.excel_output import build_workbook

RESULTS = [
    {
        'drawing_number': 'EE6892-610-01B', 'title': 'ELECTRICAL SCHEMATIC DIAGRAM',
        'subtitle': 'ＰＲＯＣＥＳＳ',
        'cables': [
            {'cable': 'M65202', 'remark': 'ACCESSORY CABLE'},
            {'cable': 'M65209', 'remark': ''},
        ],
    },
    # b2: ケーブル0件・タイトル無し
    {'drawing_number': 'EE5207-405A', 'title': '', 'subtitle': '', 'cables': []},
]


def _load(results):
    return openpyxl.load_workbook(io.BytesIO(build_workbook(results)))


def _rows(ws):
    return [[('' if v is None else v) for v in row] for row in ws.iter_rows(values_only=True)]


def test_sheet_order_and_names():
    """守るもの: Summary → ケーブル一覧 → 図番順の図番シート。0件の図面もシートを持つ。"""
    assert _load(RESULTS).sheetnames == [
        'Summary', 'ケーブル一覧', 'EE5207-405A', 'EE6892-610-01B',
    ]


def test_summary_includes_zero_cable_drawings():
    rows = _rows(_load(RESULTS)['Summary'])
    assert rows == [
        ['図番', 'タイトル', 'サブタイトル', 'ケーブル数'],
        ['EE5207-405A', '', '', 0],
        ['EE6892-610-01B', 'ELECTRICAL SCHEMATIC DIAGRAM', 'ＰＲＯＣＥＳＳ', 2],
    ]


def test_cable_list_is_long_format_without_zero_drawings():
    rows = _rows(_load(RESULTS)['ケーブル一覧'])
    assert rows == [
        ['図番', 'ケーブル番号', '備考'],
        ['EE6892-610-01B', 'M65202', 'ACCESSORY CABLE'],
        ['EE6892-610-01B', 'M65209', ''],
    ]


def test_per_drawing_sheets():
    wb = _load(RESULTS)
    assert _rows(wb['EE5207-405A']) == [['ケーブル番号', '備考']]  # 見出しのみ
    assert _rows(wb['EE6892-610-01B']) == [
        ['ケーブル番号', '備考'],
        ['M65202', 'ACCESSORY CABLE'],
        ['M65209', ''],
    ]


def test_empty_results_still_produce_workbook():
    wb = _load([])
    assert wb.sheetnames == ['Summary', 'ケーブル一覧']
