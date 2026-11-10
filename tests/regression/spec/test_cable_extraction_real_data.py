"""展開接続図23件（ME25-7103-0 Cu R1chamber の 405）での実データ期待値。

このテストが守るもの: 実図面で、ケーブル番号の抽出規則（D2・D3）とタイトル取得が
2026-11-10 の調査結果どおりに働くこと。

受入条件:
  Given sample-dxf/dataset/ME25-7103-0_Cu_R1chamber/405_展開図/ の23 DXF
  When  extract_cables() を各図面に実行する
  Then  - 全図面でタイトルが取れる（表記ゆれ ELECTRICAL_SCHEMATIC_DIAGRAM / DAGRAM はそのまま）
        - ケーブル0件の図面は目録 EE5207-405A ほか3件だけ
        - 図面ごとのケーブル数が下の EXPECTED_COUNTS と一致する
        - 末尾英字（M06004B。M06004 とは別）・中間英字（M65A61）が拾われる
        - 後続テキスト（ACCESSORY CABLE、(CC050VZRB)）が備考に入る
        - 改訂注記（…追加/KURIHARA）は拾われない

期待値の数は、共有 extract_labels() の出力を別の緩い正規表現で独立に数えた結果と
全図面で一致することを確認してから固定した。
このフォルダの図面は dataset/405_展開接続図/ と同名・別内容なので、名前引き（find_sample）は
使わずフォルダを集合として直接参照する（sample-dxf/README.md 規則4）。
"""
import glob
import os

import pytest

from model.cable_extractor import extract_cables

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SAMPLE_ROOT = os.path.join(PROJECT, 'sample-dxf')
DATA_DIR = os.path.join(SAMPLE_ROOT, 'dataset', 'ME25-7103-0_Cu_R1chamber', '405_展開図')

EXPECTED_COUNTS = {
    'EE5207-405A': 0, 'EE6676-601-02A': 2, 'EE6886-614-01A': 1, 'EE6886-699-01A': 0,
    'EE6888-604-01A': 6, 'EE6888-637-01A': 1, 'EE6888-639-01A': 3, 'EE6888-650-01C': 13,
    'EE6888-660-01A': 0, 'EE6889-635-01A': 3, 'EE6892-405-08A': 4, 'EE6892-405-17A': 1,
    'EE6892-514-01B': 0, 'EE6892-600-01A': 3, 'EE6892-602-01E': 7, 'EE6892-610-01B': 17,
    'EE6892-611-01B': 7, 'EE6892-612-01B': 5, 'EE6892-617-01B': 4, 'EE6892-631-01B': 1,
    'EE6892-633-01B': 18, 'EE6892-634-01C': 13, 'EE7150-641-01A': 1,
}

pytestmark = pytest.mark.slow


@pytest.fixture(scope='module')
def results():
    if not os.path.isdir(SAMPLE_ROOT):
        pytest.skip('sample-dxf/ がありません（共有データ置き場のない環境）')
    # sample-dxf があるのにフォルダ・件数が違うときは、スキップせず失敗させる
    files = sorted(glob.glob(os.path.join(DATA_DIR, '*.dxf')))
    assert len(files) == len(EXPECTED_COUNTS), f'{DATA_DIR} の DXF が {len(files)} 件（期待 23 件）'
    out = {}
    for f in files:
        r = extract_cables(f, os.path.basename(f))
        out[r['drawing_number']] = r
    return out


def test_drawing_numbers_come_from_filenames(results):
    assert set(results) == set(EXPECTED_COUNTS)


def test_all_drawings_have_title(results):
    missing = [k for k, r in results.items() if not r['title'] or not r['subtitle']]
    assert missing == []
    assert results['EE6892-405-17A']['title'] == 'ELECTRICAL SCHEMATIC DAGRAM'  # 表記ゆれはそのまま


def test_cable_counts_per_drawing(results):
    assert {k: len(r['cables']) for k, r in results.items()} == EXPECTED_COUNTS


def test_drawings_without_cables(results):
    assert sorted(k for k, r in results.items() if not r['cables']) == [
        'EE5207-405A', 'EE6886-699-01A', 'EE6888-660-01A', 'EE6892-514-01B',
    ]


def test_suffix_and_middle_letter_cables(results):
    c650 = [c['cable'] for c in results['EE6888-650-01C']['cables']]
    assert 'M06004' in c650 and 'M06004B' in c650  # 別ケーブル（D2）
    assert 'M65A61' in [c['cable'] for c in results['EE6892-611-01B']['cables']]


def test_remarks(results):
    remarks_610 = {c['cable']: c['remark'] for c in results['EE6892-610-01B']['cables']}
    assert remarks_610['M65202'] == 'ACCESSORY CABLE'
    remarks_650 = {c['cable']: c['remark'] for c in results['EE6888-650-01C']['cables']}
    assert remarks_650['M06008'] == '(CC050VZRB)'


def test_revision_notes_are_not_cables(results):
    for r in results.values():
        for c in r['cables']:
            assert '追加' not in c['remark'] and 'KURIHARA' not in c['remark']
            assert len(c['cable']) in (6, 7)
