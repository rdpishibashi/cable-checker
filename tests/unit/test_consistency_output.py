"""整合性チェックの Excel・プレビュー（consistency_output.py）の合成データテスト。

守るもの: シート名と順、プレビューと Excel の一致、結果の行色（OK＝薄い緑・NG＝薄いピンク・対象なし＝色なし）、
判定の根拠のシート（パターンの解決・製作図の文字）、線番設定表の接続先ごとの判定列。
"""
import io

import openpyxl
import pandas as pd

from model.consistency import check_consistency
from model.consistency_output import (
    CHECK_COLUMNS, build_workbook, preview_sheets, summary_frame,
)
from tests.unit.test_consistency import (
    FAB_NAME, LIST_NAME, WIRE_NAME, by_name, build, wire_row,
)

SHEET_NAMES = ['Summary', '整合性チェック', 'ケーブル一覧表', '線番設定表', '製作図の接続',
               'パターンの解決', '製作図の文字']


def make_result():
    drawings = build()
    by_name(drawings, FAB_NAME).update(
        kinds=['ケーブル製作図'], fab_texts=['R2-3.5', 'CONN', 'ACCESSORY CABLE'],
        fab_conns=[{'端の数': 2, '端1_表': 'CN1', '端1_位置': '左', '端1_行': 1, '端1_線番': '*1', '端1_PIN No.': 'A',
                    '端2_表': 'CN2', '端2_位置': '右', '端2_行': 1, '端2_線番': '', '端2_PIN No.': '1',
                    'ラベル': '(BK)', '削除されたラベル': ''}])
    by_name(drawings, LIST_NAME)['kinds'] = ['ケーブル一覧表']
    by_name(drawings, WIRE_NAME)['kinds'] = ['線番設定表']
    # 照合が NG になるケーブル（線番設定表にだけある）と、対象なしが出る行を足す
    by_name(drawings, WIRE_NAME)['wire_rows'].append(wire_row(cable='M99999', n=(1,)))
    return check_consistency(drawings)


def test_sheets_are_in_the_documented_order_and_columns():
    sheets = dict(preview_sheets(make_result()))
    assert list(sheets) == SHEET_NAMES
    assert list(sheets['整合性チェック'].columns) == CHECK_COLUMNS
    assert {'ケーブル番号', '図番', '行'} <= set(sheets['ケーブル一覧表'].columns)
    assert {'線番1', '線番2', '接続先1_ユニット判定', '接続先2_端子台判定'} <= set(sheets['線番設定表'].columns)


def test_preview_sheets_match_workbook_sheets_and_cells():
    """守るもの: 画面のプレビューと出力する Excel が同じ内容（既存の excel_output と同じ作り）。"""
    result = make_result()
    sheets = preview_sheets(result)
    book = pd.read_excel(io.BytesIO(build_workbook(result)), sheet_name=None, keep_default_na=False)
    assert list(book) == [n for n, _ in sheets]
    for name, frame in sheets:
        assert book[name].astype(str).values.tolist() == frame.astype(str).values.tolist(), name
        assert list(book[name].columns) == list(frame.columns)


def test_summary_counts_results_per_check_and_drawing_kinds():
    summary = summary_frame(make_result()).set_index('項目')
    assert summary.loc['0 一覧表の記載', ['OK', 'NG', '対象なし']].tolist() == [1, 1, 0]
    assert summary.loc['1 製作図の図面', ['OK', 'NG', '対象なし']].tolist() == [1, 0, 0]
    assert summary.loc['図面: ケーブル一覧表', '件数'] == 1
    assert summary.loc['図面: 線番設定表', '件数'] == 1
    assert summary.loc['図面: ケーブル製作図（線番/PIN の表がある）', '件数'] == 1
    assert summary.loc['図面: 文字の無い図面', '件数'] == 0
    assert summary.loc['図面: 全体', '件数'] == 3


def test_check_rows_are_colored_by_result():
    """守るもの: OK＝薄い緑、NG＝薄いピンク、対象なし＝色なし（行全体）。"""
    result = make_result()
    ws = openpyxl.load_workbook(io.BytesIO(build_workbook(result)))['整合性チェック']
    header = [c.value for c in ws[1]]
    result_col = header.index('結果')
    seen = {}
    for row in ws.iter_rows(min_row=2):
        fills = {c.fill.fgColor.rgb[-6:] if c.fill.fill_type else None for c in row}
        assert len(fills) == 1                       # 行の中で色が揃っている
        seen.setdefault(row[result_col].value, fills.pop())
    assert seen['OK'] == 'EAF7EC' and seen['NG'] == 'FEF1F5'


def test_evidence_sheets_show_pattern_resolution_and_fab_text():
    sheets = dict(preview_sheets(make_result()))
    pat = sheets['パターンの解決']
    row = pat[(pat['パターン'] == 'EE****-534-07') & (pat['参照元の図番'] == LIST_NAME)].iloc[0]
    assert row['種別'] == '製作図（一覧表）' and row['当たった図面'] == FAB_NAME and row['件数'] == 1
    assert (pat['種別'] == '線番設定表（製作図の注記）').any() and (pat['当たった図面'] == WIRE_NAME).any()
    assert sheets['製作図の文字'].values.tolist() == [[FAB_NAME, t] for t in ['R2-3.5', 'CONN', 'ACCESSORY CABLE']]


def test_wire_sheet_shows_per_connection_judgments():
    wire = dict(preview_sheets(make_result()))['線番設定表']
    row = wire[wire['ケーブル番号'] == 'M65206'].iloc[0]
    assert (row['接続先1_ユニット'], row['接続先1_端子台'], row['接続先1_ユニット判定'], row['接続先1_端子台判定']) == \
        ('RACK', 'TBR1B', '一致', '一致')
    other = wire[wire['ケーブル番号'] == 'M99999'].iloc[0]
    assert other['接続先1_ユニット判定'] == '一覧表に無い'


def test_empty_result_still_builds_all_sheets():
    result = check_consistency([])
    book = pd.read_excel(io.BytesIO(build_workbook(result)), sheet_name=None)
    assert list(book) == SHEET_NAMES
