"""ケーブル製作図セット（ME25-7103-0 Cu R1chamber の 393）100件での実データ期待値。

このテストが守るもの: ケーブル製作図セットに対しても、405 と同じ抽出規則（D10）で
ケーブル一覧表・線番設定表の W No. 列の番号が拾え、W No. 欄が空欄の製作図は0件になること。

受入条件:
  Given sample-dxf/dataset/ME25-7103-0_Cu_R1chamber/393_ケーブル/ の100 DXF
  When  extract_cables() を各図面に実行する
  Then  - ケーブルがある図面は EXPECTED_COUNTS の28図面で、図面ごとのケーブル数が一致する（計258件）
        - 残り72図面（ケーブル製作図70＋注意事項・ラベル仕様書2）は0件
        - W No. 列の M 始まりでない番号（C10004・X03110 等）は拾わない
        - 改訂注記（M06004B追加/KURIHARA）は拾わない

期待値の数は、共有 extract_labels() の出力を別の緩い正規表現で独立に数えた結果と
全100図面で一致することを確認してから固定した（2026-11-10）。
フォルダを集合として直接参照する（sample-dxf/README.md 規則4。EE5283-300-30D.dxf だけは
339 のセットに同名・別内容があり、名前引きでは当たらない）。DWG 7件は DXF でないのでデータに無い。
"""
import glob
import os

import pytest

from model.cable_extractor import extract_cables

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SAMPLE_ROOT = os.path.join(PROJECT, 'sample-dxf')
DATA_DIR = os.path.join(SAMPLE_ROOT, 'dataset', 'ME25-7103-0_Cu_R1chamber', '393_ケーブル')

EXPECTED_DXF_COUNT = 100

# ケーブルがある図面（線番設定表16＋ケーブル一覧表系12）。それ以外の72図面は0件
EXPECTED_COUNTS = {
    'EE3298-625-97B': 2, 'EE3298-634-07E': 3, 'EE3298-637-03R': 7, 'EE3298-637-50C': 5,
    'EE3298-639-02G': 3, 'EE3298-639-42B': 1, 'EE3298-639-43L': 7, 'EE3298-651-08C': 3,
    'EE3298-651-91B': 2, 'EE3298-653-58A': 1, 'EE3298-656-51A': 1, 'EE3298-656-55A': 1,
    'EE3298-656-65A': 1, 'EE3298-678-03K': 12, 'EE3298-695-23A': 2, 'EE3805-638-01D': 83,
    'EE6888-306-51D': 14, 'EE6888-374-51A': 6, 'EE6892-339-51C': 20, 'EE6892-339-52B': 28,
    'EE6892-339-53A': 10, 'EE6892-365-51A': 1, 'EE6892-365-59A': 4, 'EE6892-365-61C': 17,
    'EE6892-365-62G': 13, 'EE6892-365-80A': 3, 'EE6892-365-86B': 5, 'EE6892-365-88B': 3,
}

pytestmark = pytest.mark.slow


@pytest.fixture(scope='module')
def results():
    if not os.path.isdir(SAMPLE_ROOT):
        pytest.skip('sample-dxf/ がありません（共有データ置き場のない環境）')
    # sample-dxf があるのにフォルダ・件数が違うときは、スキップせず失敗させる
    files = sorted(glob.glob(os.path.join(DATA_DIR, '*.dxf')))
    assert len(files) == EXPECTED_DXF_COUNT, f'{DATA_DIR} の DXF が {len(files)} 件（期待 {EXPECTED_DXF_COUNT} 件）'
    out = {}
    for f in files:
        r = extract_cables(f, os.path.basename(f))
        out[r['drawing_number']] = r
    return out


def test_cable_counts_per_drawing(results):
    got = {k: len(r['cables']) for k, r in results.items() if r['cables']}
    assert got == EXPECTED_COUNTS
    assert sum(got.values()) == 258


def test_all_other_drawings_have_no_cables(results):
    zero = [k for k, r in results.items() if not r['cables']]
    assert len(zero) == EXPECTED_DXF_COUNT - len(EXPECTED_COUNTS) == 72


def test_cable_production_drawings_have_zero_cables(results):
    """ケーブル製作図（W No. 欄が空欄）70件はすべて0件。表記ゆれ（ケ－ブル）も含む。"""
    production = [r for r in results.values() if (r['title'] or '').replace('－', 'ー') == 'ケーブル製作図']
    assert len(production) == 70
    assert all(not r['cables'] for r in production)


def test_non_m_ids_in_wno_column_are_not_cables(results):
    """線番設定表の W No. 列には C10004・X03110 等もあるが、ケーブル番号は M＋5桁の規則に従い拾わない。"""
    all_cables = {c['cable'] for r in results.values() for c in r['cables']}
    assert all(c.startswith('M') for c in all_cables)
    assert not {'C10004', 'C10021', 'C35045', 'X03110', 'X01310A'} & all_cables


def test_bare_and_wno_forms_are_both_picked(results):
    """裸の番号（M39014）と W No. 付き（W No. M06004）の両方を拾う。"""
    assert [c['cable'] for c in results['EE3298-653-58A']['cables']] == ['M39014']
    assert 'M06004' in [c['cable'] for c in results['EE3298-637-50C']['cables']]


def test_revision_note_is_not_a_cable(results):
    cables = [c['cable'] for c in results['EE6888-306-51D']['cables']]
    assert 'M06004B' in cables  # 番号そのもの（別のラベル）は拾う
    assert all(len(c) in (6, 7) for c in cables)
    assert all('追加' not in c['remark'] for c in results['EE6888-306-51D']['cables'])


def test_two_columns_of_cable_list_are_collapsed(results):
    """ケーブル一覧表は W No. 列が左右に2回あるが、同じ番号は1件にまとまる。"""
    cables = [c['cable'] for c in results['EE6892-365-61C']['cables']]
    assert len(cables) == len(set(cables)) == 17
