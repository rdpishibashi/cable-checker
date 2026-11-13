"""実データ（405 の23件・393 の100件）を通した、405 と 393 のケーブル比較の期待値。

このテストが守るもの: 実図面から DXF 抽出 → Excel 出力 → Excel 読み込み → 比較、までを通したときに、
展開接続図とケーブル製作図のケーブルの突き合わせ結果が 2026-11-10 の調査どおりになること。

受入条件:
  Given 405_展開図/（23 DXF）と 393_ケーブル/（100 DXF）から作った 2 つの Excel（build_workbook）
  When  read_cable_list() で読み compare_cables() で突き合わせる
  Then  - 展開接続図 110 ケーブル、ケーブル製作図 210 ケーブル
        - 両方にある 108、展開接続図だけ 2（M65711・M65959）、ケーブル製作図だけ 102
        - M39001 は 405 の EE6892-405-08A と 393 の EE6892-339-51C に載る
        - 結果はケーブル番号の昇順

数は、ユーザーが実際にアプリで出力した cable_list_405.xlsx・cable_list_393.xlsx の `ケーブル一覧` シートを
別に集計した値と一致する（2026-11-10 確認）。DXF からの抽出結果（EXPECTED_COUNTS 等）は
test_cable_extraction_real_data*.py が別に守る。
"""
import glob
import io
import os

import pytest

from model.cable_compare import BOTH, ONLY_393, ONLY_405, compare_cables, read_cable_list, status
from model.cable_extractor import extract_cables
from model.excel_output import build_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SAMPLE_ROOT = os.path.join(PROJECT, 'sample-dxf')
SET_DIR = os.path.join(SAMPLE_ROOT, 'dataset', 'ME25-7103-0_Cu_R1chamber')

pytestmark = pytest.mark.slow


def _cable_map(folder, expected_count):
    files = sorted(glob.glob(os.path.join(SET_DIR, folder, '*.dxf')))
    assert len(files) == expected_count, f'{folder} の DXF が {len(files)} 件（期待 {expected_count} 件）'
    results = [extract_cables(f, os.path.basename(f)) for f in files]
    # 実際の使い方どおり、Excel に書き出して読み直す
    return read_cable_list(io.BytesIO(build_workbook(results)), folder)


@pytest.fixture(scope='module')
def rows():
    if not os.path.isdir(SAMPLE_ROOT):
        pytest.skip('sample-dxf/ がありません（共有データ置き場のない環境）')
    return compare_cables(_cable_map('405_展開図', 23), _cable_map('393_ケーブル', 100))


def test_counts(rows):
    in_405 = [r for r in rows if r['drawings_405']]
    in_393 = [r for r in rows if r['drawings_393']]
    assert (len(in_405), len(in_393)) == (110, 210)
    by_status = {s: [r['cable'] for r in rows if status(r) == s] for s in (BOTH, ONLY_405, ONLY_393)}
    assert len(by_status[BOTH]) == 108
    assert by_status[ONLY_405] == ['M65711', 'M65959']
    assert len(by_status[ONLY_393]) == 102
    assert len(rows) == 212


def test_rows_are_sorted_by_cable_number(rows):
    cables = [r['cable'] for r in rows]
    assert cables == sorted(cables)
    assert cables.index('M06004') + 1 == cables.index('M06004B')


def test_drawings_of_a_cable_on_each_side(rows):
    m39001 = next(r for r in rows if r['cable'] == 'M39001')
    assert m39001['drawings_405'] == ['EE6892-405-08A']
    assert m39001['drawings_393'] == ['EE6892-339-51C']


def test_one_sided_cables_have_blank_other_side(rows):
    only_405 = next(r for r in rows if r['cable'] == 'M65711')
    assert only_405['drawings_393'] == [] and only_405['drawings_405']
    only_393 = next(r for r in rows if r['cable'] == 'M05008')
    assert only_393['drawings_405'] == [] and only_393['drawings_393']


def test_group_diff_on_real_data():
    """守るもの: 393 実データの28図面（ケーブル数≠0）が、サブタイトル優先で「線番設定表」16図面と
    「ケーブル一覧表」12図面（`ケ－ブル一覧表` の表記ゆれを含む）の2グループになり、
    2グループのケーブル製作図ケーブルの和集合が All Diff の393側（210）と一致する。"""
    from model.cable_compare import compare_groups, read_drawing_titles
    if not os.path.isdir(SAMPLE_ROOT):
        pytest.skip('sample-dxf/ がありません（共有データ置き場のない環境）')
    files = sorted(glob.glob(os.path.join(SET_DIR, '393_ケーブル', '*.dxf')))
    results = [extract_cables(f, os.path.basename(f)) for f in files]
    xlsx = build_workbook(results)
    map_393 = read_cable_list(io.BytesIO(xlsx), '393')
    groups = compare_groups({}, map_393, read_drawing_titles(io.BytesIO(xlsx), '393'))
    assert [(g['name'], len(g['drawings'])) for g in groups] == [('ケーブル一覧表', 12), ('線番設定表', 16)]
    union = {r['cable'] for g in groups for r in g['rows'] if r['drawings_393']}
    assert union == set(map_393) and len(union) == 210
