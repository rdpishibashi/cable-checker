"""実データ（405 の23件・393 の106件）を通した、405 と 393 のケーブル比較の期待値。

このテストが守るもの: 実図面から DXF 抽出 → 比較（`compare_extracts`）までを通したときに、
展開接続図とケーブル製作図のケーブルの突き合わせ結果が 2026-11-10 の調査どおりになること。

受入条件:
  Given 405_展開図/（23 DXF）と 393_ケーブル/（106 DXF）の抽出結果（extract_cables）
  When  compare_extracts() で突き合わせる
  Then  - 展開接続図 110 ケーブル、ケーブル製作図 215 ケーブル
        - 両方にある 108、展開接続図だけ 2（M65711・M65959）、ケーブル製作図だけ 107
        - M39001 は 405 の EE6892-405-08A と 393 の EE6892-339-51C に載る
        - 結果はケーブル番号の昇順

数は、ユーザーが実際にアプリで出力した cable_list_405.xlsx・cable_list_393.xlsx の `ケーブル一覧` シートを
別に集計した値と一致する（2026-11-10 確認。この Excel を経由する比較は 2026-11-20 に無くなり、抽出結果から直接比較する）。DXF からの抽出結果（EXPECTED_COUNTS 等）は
test_cable_extraction_real_data*.py が別に守る。

2026-11-18 変更（2）: EE3298-637-02E を差し替えた。最初の変換版は文字がすべて輪郭線に分解されていて文字0件だったが、
元の DWG には文字が残っていたので LibreDWG で個別に変換し直した（Cable-checker の処理用の入力。ユーザー決定）。
線番設定表として読め、ケーブル6件（M39004・M39005・M39138・M57021・M62015・M87004）。
393 にだけ新しく増えたのは M39138・M62015 の2件（他の4件は 637-03R 等に既にある）で、393 213→215・
ケーブル製作図だけ 105→107・全体 215→217。両方にある 108 は変わらない。
グループ分けでは、2026-11-18 の差し替え直後は 637-02E のタイトルが `F` と取れて1図面だけの `F` グループができていた
（この古い図面はタイトル欄ブロック ULVAC_TITLE_05 が図番・タイトルを属性〈ATTRIB。DWG.NO・TITLE〉で持ち、共有
extract_labels() が ATTRIB を拾わないため図番が見つからず、図面枠の区画記号 F を拾っていた）。同日、正本 DXF-extract-labels の
v3.22.4 で属性を図番・タイトルの判定の手がかりにするフォールバックを入れ、全コピーに伝播して解消した。
637-02E のタイトルは `線番設定表`、グループは ケーブル一覧表 12・線番設定表 17 の2つ。
"""
import glob
import os

import pytest

from model.cable_compare import BOTH, ONLY_393, ONLY_405, cable_map, compare_extracts, status
from model.cable_extractor import extract_cables

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SAMPLE_ROOT = os.path.join(PROJECT, 'sample-dxf')
SET_DIR = os.path.join(SAMPLE_ROOT, 'dataset', 'ME25-7103-0_Cu_R1chamber')

pytestmark = pytest.mark.slow


def _results(folder, expected_count):
    files = sorted(glob.glob(os.path.join(SET_DIR, folder, '*.dxf')))
    assert len(files) == expected_count, f'{folder} の DXF が {len(files)} 件（期待 {expected_count} 件）'
    return [extract_cables(f, os.path.basename(f)) for f in files]


@pytest.fixture(scope='module')
def compared():
    if not os.path.isdir(SAMPLE_ROOT):
        pytest.skip('sample-dxf/ がありません（共有データ置き場のない環境）')
    results_393 = _results('393_ケーブル', 106)
    return compare_extracts(_results('405_展開図', 23), results_393), results_393


@pytest.fixture(scope='module')
def rows(compared):
    return compared[0]['rows']


def test_counts(rows):
    in_405 = [r for r in rows if r['drawings_405']]
    in_393 = [r for r in rows if r['drawings_393']]
    assert (len(in_405), len(in_393)) == (110, 215)
    by_status = {s: [r['cable'] for r in rows if status(r) == s] for s in (BOTH, ONLY_405, ONLY_393)}
    assert len(by_status[BOTH]) == 108
    assert by_status[ONLY_405] == ['M65711', 'M65959']
    assert len(by_status[ONLY_393]) == 107
    assert len(rows) == 217


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
