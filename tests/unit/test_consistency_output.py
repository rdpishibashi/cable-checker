"""整合性チェックの Excel・プレビュー（consistency_output.py）の合成データテスト。

守るもの: シート名と順、プレビューと Excel の一致、整合性チェックの2段見出し・記号の色・照合明細へのリンク、
照合明細の行色、根拠のシート（図番パターン照合・製作図の文字・展開接続図の読み取り・芯線照合）、ケーブル番号の抽出・比較のシート、Arrow への変換。
"""
import io
import re

import openpyxl
import pandas as pd
import pytest

from model import consistency_output as out
from model.cable_compare import compare_extracts
from model.consistency import ITEM_COLUMNS, ITEMS, check_consistency, item_label
from model.consistency_output import (
    CHECK_COLUMNS, DETAIL_COLUMNS, FIXED_SHEET_NAMES, build_workbook, preview_sheets, summary_frame,
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
    return with_extracts(check_consistency(drawings), {
        '405': [extract(SCH_NAME, [CABLE, 'M70001'], title='ELECTRICAL SCHEMATIC DIAGRAM')],
        '393': [extract('EE0002-002-02A', []), extract(FAB_NAME, []), extract(WIRE_NAME, [CABLE, 'M99999'], title='線番設定表'),
                extract(LIST_NAME, [CABLE], subtitle='ケーブル一覧表')]})            # 図番順（`analyze_uploads` と同じ）


def extract(name, cables, title='', subtitle=''):
    return {'drawing_number': name, 'title': title, 'subtitle': subtitle,
            'cables': [{'cable': c, 'remark': f'備考{c}'} for c in cables]}


def with_extracts(result, extracts):
    """照合の結果に、`run_check()` と同じ形で抽出結果とケーブル番号の比較を足す。"""
    result['extracts'] = extracts
    result['compare'] = compare_extracts(extracts['405'], extracts['393'])
    return result


def drawing_without_kind():
    from tests.unit.test_consistency import drawing
    return drawing('EE0002-002-02A')


def read_check_sheet(result):
    ws = openpyxl.load_workbook(io.BytesIO(build_workbook(result)))[out.CHECK_SHEET]
    return ws


# ---------------------------------------------------------------- 構成

def test_sheets_are_in_the_documented_order_and_columns():
    sheets = dict(preview_sheets(make_result()))
    assert list(sheets) == [
        'Summary', '記号の説明', '図面一覧', 'ケーブル抽出', 'All Diff', 'ケーブル一覧表 Diff', '線番設定表 Diff', '整合性チェック', '照合明細',
        '芯線照合', '展開接続図', 'ケーブル一覧表', '線番設定表', '製作図の接続表', '製作図の文字', '図番パターン照合']
    assert [n for n in sheets if n in FIXED_SHEET_NAMES] == FIXED_SHEET_NAMES                 # 固定のシートは並びを保つ
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
    assert summary.loc['図面: 展開接続図（照合に使った）', '件数'] == 1
    assert summary.loc['図面: 文字の無い図面', '件数'] == 0
    assert summary.loc['図面: 照合に使っていない図面', '件数'] == 1
    assert summary.loc['図面: 全体', '件数'] == 5
    assert summary.loc['図面: 展開接続図（405）のセット', '件数'] == 1 and summary.loc['図面: ケーブル製作図（393）のセット', '件数'] == 4
    # ケーブル番号の比較: 405 に CABLE・M70001、393 に CABLE・M99999
    assert summary.loc['ケーブル番号: 展開接続図', '件数'] == 2 and summary.loc['ケーブル番号: ケーブル製作図', '件数'] == 2
    assert summary.loc['ケーブル番号: 両方にある', '件数'] == 1
    assert summary.loc['ケーブル番号: 展開接続図だけ', '件数'] == 1 and summary.loc['ケーブル番号: ケーブル製作図だけ', '件数'] == 1


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


# ---------------------------------------------------------------- 照合明細・芯線照合・根拠のシート

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
    # 「図面種別」（パターンが指す図面）と「参照元図面種別」（パターンの書かれた場所）は別の列
    assert list(pat.columns) == ['図面種別', '参照元図面種別', 'パターン', '参照元の図番', '当たった図面', '件数', '備考']
    row = pat[(pat['パターン'] == 'EE****-534-07') & (pat['参照元の図番'] == LIST_NAME)].iloc[0]
    assert (row['図面種別'], row['参照元図面種別']) == ('製作図', '一覧表') and row['当たった図面'] == FAB_NAME and row['件数'] == 1
    assert ((pat['図面種別'] == '線番設定表') & (pat['参照元図面種別'] == '製作図の注記')).any() and (pat['当たった図面'] == WIRE_NAME).any()
    sch = pat[(pat['図面種別'] == '展開接続図') & (pat['参照元図面種別'] == '一覧表')].iloc[0]
    assert sch['当たった図面'] == SCH_NAME
    assert sheets[out.TEXT_SHEET].values.tolist() == [[FAB_NAME, t] for t in ['R2-3.5', 'CONN', 'ACCESSORY CABLE']]


# ---------------------------------------------------------------- ケーブル番号の抽出と比較のシート

def test_index_sheet_lists_the_drawings_of_both_sets_with_titles_kinds_and_remarks():
    """守るもの: 図面一覧は展開接続図のセット → ケーブル製作図のセットの順（それぞれ図番順）で、タイトル・図面の種類・ケーブル数・備考を持つ。"""
    index = dict(preview_sheets(make_result()))[out.INDEX_SHEET]
    assert list(index.columns) == out.INDEX_COLUMNS
    assert index['図番'].tolist() == [SCH_NAME, 'EE0002-002-02A', FAB_NAME, WIRE_NAME, LIST_NAME]
    sch = index.iloc[0]
    assert (sch['セット'], sch['タイトル'], sch['図面の種類'], sch['ケーブル数']) == (
        '展開接続図（405）', 'ELECTRICAL SCHEMATIC DIAGRAM', '展開接続図', 2)
    lst = index[index['図番'] == LIST_NAME].iloc[0]
    assert (lst['セット'], lst['サブタイトル'], lst['図面の種類'], lst['ケーブル数']) == ('ケーブル製作図（393）', 'ケーブル一覧表', 'ケーブル一覧表', 1)
    assert index[index['図番'] == 'EE0002-002-02A'].iloc[0]['備考'] == '照合に使っていない'
    assert (index['備考'] == '').sum() == 4


def test_index_sheet_marks_drawings_without_text():
    result = make_result()
    by_name(result['drawings'], WIRE_NAME)['no_text'] = True
    index = dict(preview_sheets(result))[out.INDEX_SHEET]
    assert index[index['図番'] == WIRE_NAME].iloc[0]['備考'] == '文字が無い'


def test_extract_sheet_lists_every_cable_of_both_sets_with_remarks():
    extract_sheet = dict(preview_sheets(make_result()))[out.EXTRACT_SHEET]
    assert list(extract_sheet.columns) == out.EXTRACT_COLUMNS
    assert extract_sheet.values.tolist() == [
        ['展開接続図（405）', SCH_NAME, CABLE, f'備考{CABLE}'], ['展開接続図（405）', SCH_NAME, 'M70001', '備考M70001'],
        ['ケーブル製作図（393）', WIRE_NAME, CABLE, f'備考{CABLE}'], ['ケーブル製作図（393）', WIRE_NAME, 'M99999', '備考M99999'],
        ['ケーブル製作図（393）', LIST_NAME, CABLE, f'備考{CABLE}']]


def test_compare_sheets_are_all_diff_and_one_per_group_after_the_extract_sheet():
    """守るもの: All Diff と、393 のサブタイトル優先のグループごとの比較シートが、ケーブル抽出のあと・整合性チェックの前に入り、中身は今の比較と同じ列・値。"""
    sheets = dict(preview_sheets(make_result()))
    assert list(sheets)[3:8] == ['ケーブル抽出', 'All Diff', 'ケーブル一覧表 Diff', '線番設定表 Diff', '整合性チェック']
    assert sheets['All Diff'].values.tolist() == [
        [CABLE, SCH_NAME, f'{WIRE_NAME}、{LIST_NAME}'], ['M70001', SCH_NAME, ''], ['M99999', '', WIRE_NAME]]
    assert list(sheets['All Diff'].columns) == ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番']
    assert sheets['ケーブル一覧表 Diff'].values.tolist() == [[CABLE, SCH_NAME, LIST_NAME], ['M70001', SCH_NAME, '']]


def test_compare_sheets_are_colored_by_status_in_the_workbook():
    """守るもの: Excel の比較シートの行色（405だけ＝ピンク・393だけ＝青・両方＝緑）。"""
    ws = openpyxl.load_workbook(io.BytesIO(build_workbook(make_result())))['All Diff']
    assert [[c.fill.fgColor.rgb[-6:] for c in ws[r]][0] for r in (2, 3, 4)] == ['EAF7EC', 'FEF1F5', 'EAF1FB']
    assert ws.freeze_panes == 'A2'


def test_group_sheet_names_do_not_collide_with_fixed_sheets():
    """守るもの: グループ名が固定のシート名と衝突するとき（グループ名が `All` など）は、`_2` を付けて別名にする。"""
    ds = build()
    result = with_extracts(check_consistency(ds), {'405': [], '393': [
        extract(n, [CABLE] if n == LIST_NAME else [], title='All') for n in sorted(d['drawing_number'] for d in ds)]})
    assert [n for n, _ in preview_sheets(result)][4:6] == ['All Diff', 'All_2 Diff']


# ---------------------------------------------------------------- 形の保証

def test_empty_result_still_builds_all_sheets():
    result = with_extracts(check_consistency([]), {'405': [], '393': []})
    book = pd.read_excel(io.BytesIO(build_workbook(result)), sheet_name=None)
    assert list(book) == [*FIXED_SHEET_NAMES[:4], 'All Diff', *FIXED_SHEET_NAMES[4:]]


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


def test_legend_sheet_explains_every_symbol_item_and_basis_word():
    """守るもの: 「記号の説明」は Summary の次にあり、全記号・全項目・全根拠語（consistency.B_*）の説明を載せる（項目や語を足したときの漏れを検出する）。"""
    from model import consistency as C
    sheets = dict(preview_sheets(make_result()))
    assert list(sheets)[:2] == ['Summary', '記号の説明']
    frame = sheets['記号の説明']
    assert list(frame.columns) == ['区分', '項目', '記号・語', '意味・条件', 'Excel のセル色', 'viewer の文字色']
    symbols = frame[(frame['区分'] == '記号') & frame['記号・語'].isin(C.SYMBOLS)]
    assert symbols['記号・語'].tolist() == C.SYMBOLS
    # viewer の色は Excel のセル色に対応（○緑・△黄・×赤・？青・－紫）
    assert dict(zip(symbols['記号・語'], symbols['viewer の文字色'])) == {'○': '緑', '△': '黄', '×': '赤', '？': '青', '－': '紫'}
    assert set(frame[frame['区分'] == '項目の判定']['項目']) == set(C.ITEM_COLUMNS)
    words = set(frame[frame['区分'] == '判断根拠の語']['記号・語'])
    assert words == {getattr(C, n) for n in dir(C) if n.startswith('B_') and isinstance(getattr(C, n), str)}
    rules = frame[frame['区分'] == '項目の判定']
    assert rules['意味・条件'].str.len().gt(0).all()
    # Excel のセル色（記号の列）が書かれている
    book = openpyxl.load_workbook(io.BytesIO(build_workbook(make_result())))
    ws = book['記号の説明']
    assert ws['C2'].value == '○' and ws['C2'].fill.fgColor.rgb.endswith('EAF7EC')
