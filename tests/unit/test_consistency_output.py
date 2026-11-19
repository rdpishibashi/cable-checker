"""整合性チェックの Excel・プレビュー（consistency_output.py）の合成データテスト。

守るもの: シート名と順、プレビューと Excel の一致、整合性チェックの2段見出し・記号の色・照合明細へのリンク、
照合明細の行色、根拠のシート（パターンの解決・製作図の文字・展開接続図の読み取り・芯線の照合）、Arrow への変換。
"""
import io
import re

import openpyxl
import pandas as pd
import pytest

from model import consistency_output as out
from model.consistency import ITEM_COLUMNS, ITEMS, check_consistency, item_label
from model.consistency_output import (
    CHECK_COLUMNS, DETAIL_COLUMNS, SHEET_NAMES, build_workbook, preview_sheets, summary_frame,
)
from tests.unit.test_consistency import (
    CABLE, FAB_NAME, LIST_NAME, SCH_NAME, WIRE_NAME, by_name, build, wire_row, with_schematic,
)

FILLS = {'○': 'EAF7EC', '△': 'FFF6D5', '×': 'FEF1F5', '？': 'EAF1FB'}


def make_result():
    """393 の連鎖＋展開接続図。色（製作図 (WH) と 405 (BK)）だけ × になる。線番設定表にだけあるケーブルの行も足す。"""
    drawings = with_schematic(build(), keep_wire_table=True, row={'端末2_モジュール': 'R1'})
    by_name(drawings, FAB_NAME).update(
        fab_texts=['R2-3.5', 'CONN', 'ACCESSORY CABLE'],
        fab_conns=[{'端の数': 2, '端1_表': 'CN1', '端1_位置': '左', '端1_行': 1, '端1_線番': '*1', '端1_PIN No.': '',
                    '端2_表': 'CN2', '端2_位置': '右', '端2_行': 1, '端2_線番': '', '端2_PIN No.': 'A',
                    'ラベル': '(BK)', '削除されたラベル': ''},
                   {'端の数': 2, '端1_表': 'CN1', '端1_位置': '左', '端1_行': 2, '端1_線番': '*2', '端1_PIN No.': '',
                    '端2_表': 'CN2', '端2_位置': '右', '端2_行': 2, '端2_線番': '', '端2_PIN No.': 'B',
                    'ラベル': '(WH)', '削除されたラベル': ''}])
    by_name(drawings, WIRE_NAME)['wire_rows'].append(wire_row(cable='M99999', n=(1,)))
    drawings.append(drawing_without_kind())
    return check_consistency(drawings)


def drawing_without_kind():
    from tests.unit.test_consistency import drawing
    return drawing('EE0002-002-02A')


def read_check_sheet(result):
    ws = openpyxl.load_workbook(io.BytesIO(build_workbook(result)))[out.CHECK_SHEET]
    return ws


# ---------------------------------------------------------------- 構成

def test_sheets_are_in_the_documented_order_and_columns():
    sheets = dict(preview_sheets(make_result()))
    assert list(sheets) == SHEET_NAMES
    assert list(sheets[out.CHECK_SHEET].columns) == CHECK_COLUMNS == [
        'ケーブル番号', '一覧表の図番', '一覧表の行', '不整合数', '要確認数', *ITEM_COLUMNS]
    assert ITEM_COLUMNS[0] == '図面の対応｜一覧表' and ITEM_COLUMNS[-1] == '芯線｜色'
    assert list(sheets[out.DETAIL_SHEET].columns) == DETAIL_COLUMNS
    assert {'ケーブル番号', '図番', '行'} <= set(sheets[out.LIST_SHEET].columns)
    wire = sheets[out.WIRE_SHEET]
    assert {'線番1', '線番2', '接続先1_ユニット', '接続先2_端子台'} <= set(wire.columns)
    assert not any('判定' in c for c in wire.columns)         # 判定は照合明細に移った


def test_check_sheet_has_one_row_per_list_row_with_counts():
    check = dict(preview_sheets(make_result()))[out.CHECK_SHEET]
    row = check[check['ケーブル番号'] == CABLE].iloc[0]
    assert (row['一覧表の図番'], row['一覧表の行'], row['不整合数'], row['要確認数']) == (LIST_NAME, 1, 1, 0)
    assert row['芯線｜色'] == '×' and row['図面の対応｜展開接続図'] == '○'
    orphan = check[check['ケーブル番号'] == 'M99999'].iloc[0]
    assert pd.isna(orphan['一覧表の行']) and orphan['図面の対応｜一覧表'] == '×'


def test_preview_sheets_match_workbook_sheets_and_cells():
    """守るもの: 画面のプレビューと出力する Excel が同じ内容。整合性チェックだけ Excel は2段見出しなので別に比べる。"""
    result = make_result()
    sheets = preview_sheets(result)
    book = pd.read_excel(io.BytesIO(build_workbook(result)), sheet_name=None, keep_default_na=False)
    assert list(book) == [n for n, _ in sheets]
    for name, frame in sheets:
        if name == out.CHECK_SHEET:
            continue
        # 欠損（空欄）は Excel から読むと空文字。Int64 の欠損は <NA> と出るので、空文字にそろえて比べる
        shown = frame.astype(object).where(frame.notna(), '')
        assert book[name].astype(str).values.tolist() == shown.astype(str).values.tolist(), name
        assert list(book[name].columns) == list(frame.columns)
    ws = read_check_sheet(result)
    frame = dict(sheets)[out.CHECK_SHEET]
    body = [[c.value for c in row] for row in ws.iter_rows(min_row=3)]
    shown = [[None if v == '' else v for v in row] for row in frame.astype(object).where(frame.notna(), None).values.tolist()]
    assert body == shown                                  # 空文字は Excel では空欄（None）


# ---------------------------------------------------------------- Summary

def test_summary_counts_symbols_per_item_and_drawing_kinds():
    summary = summary_frame(make_result()).set_index('項目')
    # 一覧表の行1本＋線番設定表にだけあるケーブル1本＝2行
    assert summary.loc['図面の対応｜一覧表', ['○', '△', '×', '？', '－', '件数']].tolist() == [1, 0, 1, 0, 0, 2]
    assert summary.loc['芯線｜色', ['○', '×', '－']].tolist() == [0, 1, 1]
    assert summary.loc['図面: ケーブル一覧表', '件数'] == 1
    assert summary.loc['図面: 線番設定表', '件数'] == 1
    assert summary.loc['図面: ケーブル製作図（線番/PIN の表がある）', '件数'] == 1
    assert summary.loc['図面: 展開接続図', '件数'] == 1
    assert summary.loc['図面: 文字の無い図面', '件数'] == 0
    assert summary.loc['図面: 照合に使っていない図面', '件数'] == 1
    assert summary.loc['図面: 全体', '件数'] == 5


# ---------------------------------------------------------------- 整合性チェックシート（Excel）

def test_check_sheet_has_a_two_row_header_with_merged_groups():
    ws = read_check_sheet(make_result())
    merged = {str(r) for r in ws.merged_cells.ranges}
    assert {'A1:A2', 'D1:D2'} <= merged                                  # キー列は2行を結合
    assert 'F1:I1' in merged                                             # 図面の対応（4項目）
    assert ws['F1'].value == '図面の対応' and ws['F2'].value == '一覧表' and ws['I2'].value == '展開接続図'
    assert [ws.cell(row=2, column=6 + k).value for k in range(len(ITEMS))] == [item for _, item in ITEMS]
    assert ws.freeze_panes == 'D3'


def test_symbol_cells_are_colored_and_link_to_the_matching_detail_row():
    """守るもの: ○＝薄い緑・△＝薄い黄・×＝薄いピンク・？＝薄い青・－＝色なし。リンク先は同じケーブル・同じ項目の根拠の行。"""
    result = make_result()
    wb = openpyxl.load_workbook(io.BytesIO(build_workbook(result)))
    ws, detail = wb[out.CHECK_SHEET], wb[out.DETAIL_SHEET]
    seen = set()
    for i, row in enumerate(result['rows']):
        for k, col_name in enumerate(ITEM_COLUMNS):
            c = ws.cell(row=3 + i, column=6 + k)
            seen.add(c.value)
            assert (c.fill.fgColor.rgb[-6:] if c.fill.fill_type else None) == FILLS.get(c.value)
            target = re.fullmatch(r"'照合明細'!A(\d+)", c.hyperlink.location)
            assert target, (row['ケーブル番号'], col_name)
            d = result['details'][int(target.group(1)) - 2]
            assert detail.cell(row=int(target.group(1)), column=1).value == row['ケーブル番号'] == d['ケーブル番号']
            assert d['項目'].startswith(item_label(col_name))
    assert {'○', '×', '－'} <= seen


def test_links_point_at_the_detail_row_of_the_same_item():
    """各セルのリンク先（照合明細の通し番号）は、同じ項目の根拠の行（線番設定表だけ3行の先頭）。"""
    result = make_result()
    for row in result['rows']:
        for col_name in ITEM_COLUMNS:
            label = result['details'][row['links'][col_name]]['項目']
            assert label.startswith(item_label(col_name)), (col_name, label)
            assert result['details'][row['links'][col_name]]['ケーブル番号'] == row['ケーブル番号']


def test_too_many_cells_for_hyperlinks_fall_back_to_plain_symbols(monkeypatch):
    monkeypatch.setattr(out, '_MAX_LINKS', 0)
    ws = read_check_sheet(make_result())
    assert ws['F3'].value == '○' and ws['F3'].hyperlink is None


# ---------------------------------------------------------------- 照合明細・芯線の照合・根拠のシート

def test_detail_rows_are_colored_by_judgment():
    ws = openpyxl.load_workbook(io.BytesIO(build_workbook(make_result())))[out.DETAIL_SHEET]
    header = [c.value for c in ws[1]]
    judge = header.index('判定')
    seen = {}
    for row in ws.iter_rows(min_row=2):
        fills = {c.fill.fgColor.rgb[-6:] if c.fill.fill_type else None for c in row}
        assert len(fills) == 1                                           # 行の中で色が揃っている
        seen.setdefault(row[judge].value, fills.pop())
    assert seen['○'] == 'EAF7EC' and seen['×'] == 'FEF1F5' and seen['－'] is None


def test_detail_rows_show_each_drawings_value_and_the_basis():
    detail = dict(preview_sheets(make_result()))[out.DETAIL_SHEET]
    row = detail[(detail['ケーブル番号'] == CABLE) & (detail['項目'] == '端末1 部品種別')].iloc[0]
    assert row['一覧表の値'] == 'R2-3.5' and row['判定'] == '○' and '製作図 ○ 一致' in row['判断根拠']
    assert FAB_NAME in row['参照した図面']
    color = detail[(detail['ケーブル番号'] == CABLE) & (detail['項目'] == '芯線 色')].iloc[0]
    assert color['判定'] == '×' and '(WH)' in color['判断根拠'] and SCH_NAME in color['参照した図面']


def test_wire_check_sheet_has_one_row_per_fab_connection():
    wires = dict(preview_sheets(make_result()))[out.WIRE_CHECK_SHEET]
    assert wires['線番接続ID'].tolist() == ['*1', '*2']
    assert wires['製作図の端2 PIN'].tolist() == ['A', 'B'] and wires['展開接続図の端+ PIN'].tolist() == ['A', 'B']
    assert wires['判定（色）'].tolist() == ['○', '×'] and wires['判定（PIN）'].tolist() == ['○', '○']


def test_schematic_sheet_lists_the_ends_of_every_wire():
    sch = dict(preview_sheets(make_result()))[out.SCHEMATIC_SHEET]
    assert len(sch) == 2
    row = sch.iloc[0]
    assert (row['図番'], row['ケーブル番号'], row['描き方'], row['ラベル']) == (SCH_NAME, CABLE, 'ellipse', '(BK)')
    assert (row['端-_種類'], row['端-_名前'], row['端-_ピン'], row['端-_領域']) == ('circle', 'TBR1B', '1', 'RACK')
    assert (row['端+_種類'], row['端+_名前'], row['端+_ピン']) == ('box', 'CN1', 'A')


def test_evidence_sheets_show_pattern_resolution_and_fab_text():
    sheets = dict(preview_sheets(make_result()))
    pat = sheets[out.PATTERN_SHEET]
    row = pat[(pat['パターン'] == 'EE****-534-07') & (pat['参照元の図番'] == LIST_NAME)].iloc[0]
    assert row['種別'] == '製作図（一覧表）' and row['当たった図面'] == FAB_NAME and row['件数'] == 1
    assert (pat['種別'] == '線番設定表（製作図の注記）').any() and (pat['当たった図面'] == WIRE_NAME).any()
    sch = pat[pat['種別'] == '展開接続図（一覧表）'].iloc[0]
    assert sch['当たった図面'] == SCH_NAME
    assert sheets[out.TEXT_SHEET].values.tolist() == [[FAB_NAME, t] for t in ['R2-3.5', 'CONN', 'ACCESSORY CABLE']]


# ---------------------------------------------------------------- 形の保証

def test_empty_result_still_builds_all_sheets():
    book = pd.read_excel(io.BytesIO(build_workbook(check_consistency([]))), sheet_name=None)
    assert list(book) == SHEET_NAMES


def test_every_preview_sheet_converts_to_arrow():
    """守るもの: 画面の表（st.dataframe）は Arrow に変換される。数値と空文字が混ざる列は変換に失敗して警告が出る
    （Summary の件数列、線番設定表にだけあるケーブルを含む整合性チェック・照合明細の「一覧表の行」列）。"""
    import pyarrow as pa
    for name, frame in preview_sheets(make_result()):
        pa.Table.from_pandas(frame, preserve_index=False)   # 失敗したら例外


def test_blank_cells_stay_blank_in_excel_and_numbers_stay_numbers():
    result = make_result()
    book = pd.read_excel(io.BytesIO(build_workbook(result)), sheet_name=None, keep_default_na=False)
    summary = book['Summary'].set_index('項目')
    assert summary.loc['図面: 全体', '件数'] == 5 and summary.loc['図面: 全体', '○'] == ''
    detail = book[out.DETAIL_SHEET]
    orphan = detail[detail['ケーブル番号'] == 'M99999']
    assert orphan['一覧表の行'].tolist() == [''] * len(orphan) and len(orphan) == len(ITEM_COLUMNS)
    assert detail[detail['ケーブル番号'] == CABLE]['一覧表の行'].tolist()[0] == 1
    ws = read_check_sheet(result)
    rows = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=3).value for r in range(3, ws.max_row + 1)}
    assert rows['M99999'] is None and rows[CABLE] == 1


@pytest.mark.parametrize('item', ITEMS)
def test_every_item_has_a_summary_row_and_a_check_column(item):
    col = f'{item[0]}｜{item[1]}'
    assert col in ITEM_COLUMNS and col in summary_frame(make_result())['項目'].tolist()
