"""excel_output の単体テスト。守るもの: Summary／ケーブル一覧／図番別シートの形と値（D5・D9）。"""
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
    """守るもの: Summary → ケーブル一覧 → 図番順の図番シート。
    図番シートはケーブルが1件以上ある図面だけ（D9。0件の図面は Summary にだけ載る）。"""
    assert _load(RESULTS).sheetnames == ['Summary', 'ケーブル一覧', 'EE6892-610-01B']


def test_sheets_follow_drawing_number_order_and_skip_zero_cable_drawings():
    """守るもの: 図番シートは図番順に並び、0件の図面は混ざっても飛ばされる（D9）。"""
    def r(n, cables):
        return {'drawing_number': n, 'title': '', 'subtitle': '',
                'cables': [{'cable': c, 'remark': ''} for c in cables]}
    wb = _load([r('EE0002-393-01A', ['M39002']), r('EE0001-393-01A', []), r('EE0000-393-01A', ['M39001'])])
    assert wb.sheetnames == ['Summary', 'ケーブル一覧', 'EE0000-393-01A', 'EE0002-393-01A']
    assert [row[0] for row in _rows(wb['Summary'])[1:]] == [
        'EE0000-393-01A', 'EE0001-393-01A', 'EE0002-393-01A']  # Summary には0件の図面も載る


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
    assert 'EE5207-405A' not in wb.sheetnames  # 0件の図面のシートは作らない（D9）
    assert _rows(wb['EE6892-610-01B']) == [
        ['ケーブル番号', '備考'],
        ['M65202', 'ACCESSORY CABLE'],
        ['M65209', ''],
    ]


def test_empty_results_still_produce_workbook():
    wb = _load([])
    assert wb.sheetnames == ['Summary', 'ケーブル一覧']


def test_excel_filenames_differ_by_kind():
    """守るもの: 405（展開接続図）と393（ケーブル製作図）は別の Excel ファイルになる（D8）。"""
    from model.excel_output import EXCEL_FILENAMES
    assert EXCEL_FILENAMES == {'405': 'cable_list_405.xlsx', '393': 'cable_list_393.xlsx'}
