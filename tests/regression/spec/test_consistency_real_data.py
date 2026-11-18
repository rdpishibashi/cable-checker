"""整合性チェック（393）の実データ期待値: ケーブル製作図セット（ME25-7103-0 Cu R1chamber の 393）106件。

固定日: 2026-11-18。値は、移植した読み取りを実現性検討の試作（docs/feasibility/*/run_all.py）と
同じ106件で突き合わせて**差0**を確認したうえで、実測して固定した。

このテストが守るもの: 3種類の読み取り（一覧表・線番設定表・製作図）と照合 0〜7 が、実データで
実現性検討の実測と同じ件数になること。
  - 一覧表 12枚・134行・ケーブル番号120種（試作の Excel も 120 種。試作 README の「121種」は誤記）
  - 線番設定表 17枚・403行・133種。注記の製作図パターンは17枚すべてで1件に当たる
  - 製作図（線番/PIN の表がある）72枚・接続456件（端が3つ: 6件）。
    実現性検討 README の「71枚」は、DWG から変換した6件を足す前の数え方
  - 製作図の注記の線番設定表パターン 18件、うち17件が図面1件に当たる（`-678-04` は0件）。文字の無い図面は無い
  - 代表例: M65206（一覧表 EE6892-365-61C）→ 製作図 EE3298-534-07D → 線番設定表 EE3298-634-07E。#7 は `*1,*2` と線番1・2 で OK
  - 537-85C の見出しが逆の表は、製作図の接続の明細に図面どおり出る（読み取りは図面のとおり）
照合の結果の分布は、読み取りの件数と違い「一覧表と図面の書き方の差」を含む（#2 は一覧表の部品種別・ケーブル種類が
図面に文字どおりには無い、#6 は一覧表がケーブル単位・線番設定表が電線単位で粒度が違う。どちらも各 README に記録済み）。
分布が変わったら、変更の意図を確かめてから更新する。

2026-11-18 変更: EE3298-637-02E を差し替えた（最初の変換版は文字がすべて輪郭線に分解されて文字0件。元の DWG には文字が
残っていたので LibreDWG で個別に変換し直した。Cable-checker の処理用の入力。ユーザー決定）。637-02E が線番設定表
（40行・ケーブル6種、注記 EE****-537-02）として読めるようになり、次が変わった:
  線番設定表 16→17枚・363→403行・131→133種、文字の無い図面 1→0枚、ケーブル番号 210→212種。
  一覧表の M39004（EE6892-339-51C 行4）・M39005（同 行5）の2行: #3 NG→OK、#4・#5・#7 対象なし→OK、
  #6a 対象なし→NG（`CN PANEL1` 等が一覧表の端末と合わない。637-03R の行と同じ傾向）。
  #0 NG 90→92（線番設定表にだけある M39138・M62015）。#3 の NG は M39038（`-678-04` が0件）だけになった。
データはフォルダを集合として直接参照する（sample-dxf/README.md 規則4）。
"""
import glob
import io
import os

import pandas as pd
import pytest

from model.consistency import analyze_drawing, check_consistency, pattern_resolutions
from model.consistency_output import build_workbook, preview_sheets

pytestmark = pytest.mark.slow

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
DATA_DIR = os.path.join(PROJECT, 'sample-dxf', 'dataset', 'ME25-7103-0_Cu_R1chamber', '393_ケーブル')

# 照合ごとの (OK, NG, 対象なし)。一覧表の行134（0 は線番設定表にだけあるケーブル92を含む226）
EXPECTED_DISTRIBUTION = {
    '0 一覧表の記載': (134, 92, 0),
    '1 製作図の図面': (133, 1, 0),
    '2 製作図の記載（端末1 部品種別）': (15, 118, 1),
    '2 製作図の記載（端末2 部品種別）': (17, 104, 13),
    '2 製作図の記載（ケーブル種類）': (27, 101, 6),
    '3 線番設定表の図面': (39, 1, 94),
    '4 線番設定表の記載': (38, 1, 95),
    '5 線番設定表の製作図': (39, 0, 95),
    '6a 接続先のユニット名': (17, 21, 96),
    '6b 接続先の端子台': (0, 9, 125),
    '7 線番の列': (36, 2, 96),
}


@pytest.fixture(scope='module')
def drawings():
    files = sorted(glob.glob(os.path.join(DATA_DIR, '*.dxf')))
    assert len(files) == 106
    return [analyze_drawing(f, os.path.basename(f)[:-4]) for f in files]


@pytest.fixture(scope='module')
def result(drawings):
    return check_consistency(drawings)


def by_name(drawings, name):
    return next(d for d in drawings if d['drawing_number'] == name)


def test_list_tables(drawings):
    lists = [d for d in drawings if d['list_rows']]
    assert len(lists) == 12
    assert sum(len(d['list_rows']) for d in lists) == 134
    assert len({r['ケーブル番号'] for d in lists for r in d['list_rows']}) == 120


def test_wire_number_tables(drawings):
    wires = [d for d in drawings if d['wire_rows']]
    assert len(wires) == 17 and all(d['wire_notes'] for d in wires)
    assert sum(len(d['wire_rows']) for d in wires) == 403
    cables = {c for d in wires for r in d['wire_rows'] for c in r['ケーブル番号'].split(' | ') if c}
    assert len(cables) == 133


def test_fab_drawings_and_connections(drawings):
    fabs = [d for d in drawings if 'ケーブル製作図' in d['kinds']]
    assert len(fabs) == 72
    conns = [c for d in fabs for c in d['fab_conns']]
    assert len(conns) == 456 and sum(c['端の数'] > 2 for c in conns) == 6


def test_reversed_header_table_is_read_as_drawn(drawings):
    """537-85C は見出しが逆の表。読み取りは図面のとおり（線番とPINを入れ替えない）。"""
    d = next(d for d in drawings if d['drawing_number'].endswith('-537-85C'))
    first = d['fab_conns'][0]
    assert (first['端1_線番'], first['端1_PIN No.'], first['端2_線番'], first['端2_PIN No.']) == ('1', '', 'X0100', '2')


def test_note_patterns_resolve_as_in_the_feasibility_study(drawings):
    notes = [p for d in drawings for p in d['fab_notes']]
    assert len(notes) == 18
    res = pattern_resolutions(drawings)
    assert sorted(r['件数'] for r in res if r['種別'] == '線番設定表（製作図の注記）') == [0] + [1] * 17
    assert [r['パターン'] for r in res if r['種別'] == '線番設定表（製作図の注記）' and r['件数'] == 0] == ['-678-04']
    assert all(r['件数'] == 1 for r in res if r['種別'] == '製作図（線番設定表の注記）')   # 17枚すべて1件


def test_no_drawing_lacks_text_and_637_02e_is_a_wire_number_table(drawings):
    """637-02E は文字の読める変換版（2026-11-18 差し替え）。旧版は文字が輪郭線で「文字の無い図面」だった。"""
    assert [d['drawing_number'] for d in drawings if d['no_text']] == []
    d = by_name(drawings, 'EE3298-637-02E')
    assert d['kinds'] == ['線番設定表'] and len(d['wire_rows']) == 40 and d['wire_notes'] == ['EE****-537-02']


def test_check_distribution_per_check(result):
    counts = {}
    for c in result['checks']:
        counts.setdefault(c['照合'], {'OK': 0, 'NG': 0, '対象なし': 0})[c['結果']] += 1
    assert {k: tuple(v.values()) for k, v in counts.items()} == EXPECTED_DISTRIBUTION
    assert len({c['ケーブル番号'] for c in result['checks']}) == 212


def test_representative_chain_m65206(result):
    rows = {c['照合']: c for c in result['checks'] if c['ケーブル番号'] == 'M65206'}
    assert rows['0 一覧表の記載']['一覧表の図番'] == 'EE6892-365-61C'
    assert rows['1 製作図の図面']['ケーブル製作図の図番'] == 'EE3298-534-07D'
    assert rows['3 線番設定表の図面']['線番設定表の図番'] == 'EE3298-634-07E'
    for check in ('1 製作図の図面', '3 線番設定表の図面', '4 線番設定表の記載', '5 線番設定表の製作図', '7 線番の列'):
        assert rows[check]['結果'] == 'OK', check
    assert '製作図 *1,*2 / 線番設定表 *1,*2' in rows['7 線番の列']['内容']


def test_ng_cases_named_in_the_study(result):
    """-678-04（0件）は 3 が NG、M65219 の製作図パターン EE****-525-10 は 0件で 1 が NG。"""
    ng3 = [c for c in result['checks'] if c['照合'] == '3 線番設定表の図面' and c['結果'] == 'NG']
    assert sorted({c['ケーブル番号'] for c in ng3}) == ['M39038']
    ng1 = [c for c in result['checks'] if c['照合'] == '1 製作図の図面' and c['結果'] == 'NG']
    assert [(c['ケーブル番号'], '0件' in c['内容']) for c in ng1] == [('M65219', True)]


def test_excel_matches_preview_and_has_all_rows(result):
    sheets = dict(preview_sheets(result))
    assert {k: len(v) for k, v in sheets.items()} == {
        'Summary': 16, '整合性チェック': 1566, 'ケーブル一覧表': 134, '線番設定表': 403,
        '製作図の接続': 456, 'パターンの解決': 119, '製作図の文字': 3852}
    book = pd.read_excel(io.BytesIO(build_workbook(result)), sheet_name=None, keep_default_na=False)
    assert {k: len(v) for k, v in book.items()} == {k: len(v) for k, v in sheets.items()}
