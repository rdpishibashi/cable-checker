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
  - 537-85C の見出しが逆の表は、製作図の接続表の明細に図面どおり出る（読み取りは図面のとおり）
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

2026-11-19 変更: 整合性チェックを「1ケーブル1行」（一覧表の行ごと）に作り替え、展開接続図（405）の照合を加えた。
照合の4件（分布・代表例・名指しの NG・Excel）は新しい結果の形（`rows`・`details`・`wires`）で書き直し、405 の23件を
393 の106件と一緒に `analyze_uploads()` に入れる（一覧表の「接続図パターン」が指す図面だけが2段目で読まれる）。
読み取りの6件（`drawings` フィクスチャ＝393 だけ）は変えていない。新しい期待値は、移植した 405 の読み取りが試作
（docs/feasibility/schematic_405/）と一致すること（127件の出現のうち一覧表が指す126件・628芯線で、端の名前・ピン・種類・
機器の文字・近傍の文字が差0。領域の根拠だけは共有の `_point_in_polygon`〈境界上を内側とする〉で21の端が増えた）を確認して固定した。
`uploaded` フィクスチャは1分近くかかる（405 を1段目の 393 用の読み取りにもかけるため）。
"""
import glob
import io
import re
import os

import pandas as pd
import pytest

from model.consistency import ITEM_COLUMNS, SYMBOLS, analyze_drawing, pattern_resolutions, run_check
from model.consistency_output import build_workbook, preview_sheets
from model.legend import legend_rows

LEGEND_ROWS = len(legend_rows())   # 記号の説明は結果に依らない固定の表（行数は内容に追従）

pytestmark = pytest.mark.slow

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
DATA_DIR = os.path.join(PROJECT, 'sample-dxf', 'dataset', 'ME25-7103-0_Cu_R1chamber', '393_ケーブル')
DATA_405_DIR = os.path.join(PROJECT, 'sample-dxf', 'dataset', 'ME25-7103-0_Cu_R1chamber', '405_展開図')

# 項目ごとの記号の件数 (○, △, ×, ？, －)。全 228 行＝一覧表の行134＋一覧表に無いケーブル94
# （一覧表の行以外は、一覧表が × で、ほかの項目は －）。部品種別・ケーブル種類の × が多いのは、一覧表の値が図面に文字どおりには
# 無いため（実現性検討の第3弾に記録済み。BOM 化・汎用図面の「一覧表参照」）。分布が変わったら、変更の意図を確かめてから更新する
EXPECTED_DISTRIBUTION = {
    '図面の対応｜一覧表': (134, 0, 94, 0, 0),
    '図面の対応｜製作図': (133, 0, 1, 0, 94),
    '図面の対応｜線番設定表': (38, 0, 2, 0, 188),
    '図面の対応｜展開接続図': (119, 0, 15, 0, 94),
    '端末1｜ユニット': (28, 38, 16, 35, 111),
    '端末1｜モジュール': (2, 0, 0, 13, 213),
    '端末1｜機器符号': (75, 0, 16, 28, 109),
    '端末1｜部品種別': (15, 0, 118, 0, 95),
    '端末2｜ユニット': (38, 20, 11, 34, 125),
    '端末2｜モジュール': (3, 0, 0, 14, 211),
    '端末2｜機器符号': (66, 0, 11, 29, 122),
    '端末2｜部品種別': (17, 0, 104, 0, 107),
    'ケーブル｜種類': (28, 0, 101, 0, 99),
    'ケーブル｜芯数': (51, 6, 45, 0, 126),
    '芯線｜線番': (38, 2, 7, 0, 181),
    '芯線｜PIN': (48, 1, 13, 13, 153),
    '芯線｜色': (1, 0, 2, 0, 225),
}

M65206_ROW = {
    '図面の対応｜一覧表': '○', '図面の対応｜製作図': '○', '図面の対応｜線番設定表': '○', '図面の対応｜展開接続図': '○',
    '端末1｜ユニット': '○', '端末1｜モジュール': '－', '端末1｜機器符号': '○', '端末1｜部品種別': '○',
    '端末2｜ユニット': '△', '端末2｜モジュール': '○', '端末2｜機器符号': '○', '端末2｜部品種別': '○',
    'ケーブル｜種類': '○', 'ケーブル｜芯数': '－', '芯線｜線番': '○', '芯線｜PIN': '○', '芯線｜色': '×',
}


@pytest.fixture(scope='module')
def drawings():
    files = sorted(glob.glob(os.path.join(DATA_DIR, '*.dxf')))
    assert len(files) == 106
    return [analyze_drawing(f, os.path.basename(f)[:-4]) for f in files]


@pytest.fixture(scope='module')
def result():
    """393（106件）と 405（23件）を別のセットとしてアップロードしたときの結果（`run_check()`。抽出・2段の読み取り・照合・比較）。"""
    def items(folder):
        return [(os.path.basename(p), open(p, 'rb').read()) for p in sorted(glob.glob(os.path.join(folder, '*.dxf')))]
    items_405, items_393 = items(DATA_405_DIR), items(DATA_DIR)
    assert (len(items_405), len(items_393)) == (23, 106)
    result, errors = run_check(items_405, items_393)
    assert errors == []
    return result


@pytest.fixture(scope='module')
def uploaded(result):
    return result['drawings']


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
    assert sorted(r['件数'] for r in res if (r['図面種別'], r['参照元図面種別']) == ('線番設定表', '製作図の注記')) == [0] + [1] * 17
    assert [r['パターン'] for r in res if (r['図面種別'], r['参照元図面種別']) == ('線番設定表', '製作図の注記') and r['件数'] == 0] == ['-678-04']
    assert all(r['件数'] == 1 for r in res if (r['図面種別'], r['参照元図面種別']) == ('製作図', '線番設定表の注記'))   # 17枚すべて1件
    assert {(r['図面種別'], r['参照元図面種別']) for r in res} == {
        ('製作図', '一覧表'), ('展開接続図', '一覧表'), ('線番設定表', '製作図の注記'), ('製作図', '線番設定表の注記')}   # 2段目の前なので図中の参照は無い


def test_schematic_patterns_are_resolved_in_the_405_set_only(uploaded):
    """守るもの: 一覧表の接続図パターンは 405 のセットだけで解決する。`EE****-634-**`・`-637-**`・`-639-**` は 393 のセットの線番設定表・製作図
    （EE3298-634-07E・637-02E/03R/50C・639-02G/42B/43L）にも名前が合うが、当たるのは展開接続図 1件ずつ（以前は 2・4・4 件）。"""
    rows = {r['パターン']: r for r in pattern_resolutions(uploaded) if (r['図面種別'], r['参照元図面種別']) == ('展開接続図', '一覧表')}
    assert {p: rows[p]['当たった図面'] for p in ('EE****-634-**', 'EE****-637-**', 'EE****-639-**')} == {
        'EE****-634-**': 'EE6892-634-01C', 'EE****-637-**': 'EE6888-637-01A', 'EE****-639-**': 'EE6888-639-01A'}
    assert all(r['件数'] <= 1 for r in rows.values())


def test_references_written_in_schematics_are_listed_in_the_pattern_sheet(uploaded):
    """展開接続図の図中の `to POWER LINE (SYSTEM I/F BOX) (-405-08)` などの参照を、図番パターン照合に25行（参照元の図面×パターン。`(-405-01,02)` は2つに分ける）足す。
    実データでは 17 ファイル中、2段目で読んだ 14 枚から出て（EE6886-699-01A・EE6892-405-17A・EE6892-514-01B は読まない）、`-405-08` は EE6892-405-08A に当たる（9枚）。アップロードに無い図面は0件。
    照合の記号は変えず、参照で指されただけの図面は「使っていない図面」のまま（EE6892-405-17A 等）。"""
    res = pattern_resolutions(uploaded)
    assert {(r['図面種別'], r['参照元図面種別']) for r in res} >= {('展開接続図', '図中の参照')}
    rows = [r for r in res if r['参照元図面種別'] == '図中の参照']
    assert {r['図面種別'] for r in rows} == {'展開接続図'}
    assert len(rows) == 25
    hits = [r for r in rows if r['件数'] == 1]
    assert len(hits) == 9 and {(r['パターン'], r['当たった図面']) for r in hits} == {('-405-08', 'EE6892-405-08A')}
    assert all(r['件数'] == 0 for r in rows if r['パターン'] != '-405-08')
    row600 = next(r for r in rows if r['参照元の図番'] == 'EE6892-600-01A')
    assert {r['パターン'] for r in rows if r['参照元の図番'] == 'EE6892-617-01B'} == {'-405-01', '-405-02'}   # `(-405-01,02)`
    assert row600['備考'].count('XRLS') == 2                      # `to` で始まらない参照（`(-405-08) XRLS1`）も拾い、同じパターンは1行にまとめる


def test_no_drawing_lacks_text_and_637_02e_is_a_wire_number_table(drawings):
    """637-02E は文字の読める変換版（2026-11-18 差し替え）。旧版は文字が輪郭線で「文字の無い図面」だった。"""
    assert [d['drawing_number'] for d in drawings if d['no_text']] == []
    d = by_name(drawings, 'EE3298-637-02E')
    assert d['kinds'] == ['線番設定表'] and len(d['wire_rows']) == 40 and d['wire_notes'] == ['EE****-537-02']


def test_schematics_are_read_only_when_a_list_row_points_at_them(uploaded):
    """一覧表の接続図パターンが指す 405 の18枚だけが2段目で読まれる。指されない 405（目録・一覧・EE6892-405-17A 等）は読まない。
    `EE****-637-**` のようなパターンが 393 の線番設定表にも当たっても、393 の表が見つかった図面は読まない。"""
    read = sorted(d['drawing_number'] for d in uploaded if d['schematic'] is not None)
    assert len(read) == 18 and all(d.startswith(('EE6', 'EE7')) for d in read)
    assert not any(n.startswith('EE3298') for n in read)
    occ = [o for d in uploaded if d['schematic'] for o in d['schematic']['occurrences']]
    assert len(occ) == 126 and sum(len(o['wires']) for o in occ) == 628
    assert {h: sum(o['how'] == h for o in occ) for h in ('ellipse', 'capsule', 'via-line', 'single', 'beside', 'none')} == {
        'ellipse': 42, 'capsule': 13, 'via-line': 25, 'single': 32, 'beside': 14, 'none': 0}
    assert all('展開接続図' in d['kinds'] for d in uploaded if d['schematic'] is not None)


def test_check_distribution_per_item(result):
    rows = result['rows']
    assert len(rows) == 228 and sum(bool(r['一覧表の図番']) for r in rows) == 134
    counts = {c: tuple(sum(r['cells'][c] == s for r in rows) for s in SYMBOLS) for c in ITEM_COLUMNS}
    assert counts == EXPECTED_DISTRIBUTION
    assert sum(r['不整合数'] > 0 for r in rows) == 227 and sum(r['要確認数'] > 0 for r in rows) == 44
    assert len(result['details']) == 4144 and len(result['wires']) == 1101
    assert result['unused'] == ['EE5207-405A', 'EE5283-300-30D', 'EE5283-393-01B', 'EE6886-699-01A', 'EE6888-660-01A',
                                'EE6892-405-17A', 'EE6892-514-01B']


def row_of(result, cable, list_name=None, line=None):
    (row,) = [r for r in result['rows'] if r['ケーブル番号'] == cable and r['一覧表の図番'] and
              (list_name is None or r['一覧表の図番'] == list_name) and (line is None or r['一覧表の行'] == line)]
    return row


def basis(result, row, column):
    return result['details'][row['links'][column]]['判断根拠']


def test_representative_row_m65206(result):
    """M65206（一覧表 EE6892-365-61C 行3）→ 製作図 EE3298-534-07D → 線番設定表 EE3298-634-07E、展開接続図 EE6892-610-01B。
    端末2 のユニット `ALS1 R1` は領域に無く、機器符号 CN1 のラベルから 33 にあるラベルで △（近傍）。製作図の色 (WH) が
    405 の芯線のラベル (BK) だけに無いので、不整合は色の1件だけ。"""
    row = row_of(result, 'M65206')
    assert row['一覧表の図番'] == 'EE6892-365-61C' and row['一覧表の行'] == 3
    assert row['cells'] == M65206_ROW and (row['不整合数'], row['要確認数']) == (1, 0)
    assert 'EE3298-534-07D' in basis(result, row, '図面の対応｜製作図')
    assert 'EE3298-634-07E' in basis(result, row, '図面の対応｜線番設定表')
    assert 'EE6892-610-01B' in basis(result, row, '図面の対応｜展開接続図')
    assert '領域（RACK）' in basis(result, row, '端末1｜ユニット')
    assert '近傍（ALS1 R1、33）' in basis(result, row, '端末2｜ユニット')
    assert '製作図 *1,*2 / 線番設定表 *1,*2' in basis(result, row, '芯線｜線番')
    wires = [w for w in result['wires'] if w['ケーブル番号'] == 'M65206']
    assert [(w['線番接続ID'], w['線番設定表の線番'], w['製作図の端2 PIN'], w['製作図の色'], w['展開接続図のラベル'],
             w['判定（PIN）'], w['判定（色）']) for w in wires] == [
        ('*1', 'S121C', 'A', '(BK)', '(BK)', '○', '○'), ('*2', 'T121C', 'B', '(WH)', '(BK)', '○', '×')]


def test_wire_row_selection_removes_the_false_wire_number_mismatches_of_the_first_study(result):
    """実現性検討（405）の1回目は、線番設定表のモジュールごとの行のうち先頭の行で `*n` を置き換えたため、610-01B の
    `121`/`081`、617-01B の `127`/`087`、634-01C の `X0205`/`X0205B` を食い違いと誤った。一覧表の端末に合う行を使うと、全部 ○。"""
    for cable, line in (('M65202', 1), ('M65204', 2), ('M65C85', 15), ('M65C86', 16), ('M65C87', 17), ('M65714', 2),
                        ('M39045', 25)):
        row = next(r for r in result['rows'] if r['ケーブル番号'] == cable and r['一覧表の行'] == line)
        assert row['cells']['芯線｜線番'] == '○', (cable, line)


def test_cases_named_in_the_studies(result):
    # 393（実現性検討の第3弾）: -678-04 は線番設定表が0件、M65219 の製作図パターン EE****-525-10 は0件
    wire_x = [r for r in result['rows'] if r['一覧表の図番'] and r['cells']['図面の対応｜線番設定表'] == '×']
    assert sorted(r['ケーブル番号'] for r in wire_x) == ['M39038', 'M65242']
    assert '-678-04: 0件' in basis(result, next(r for r in wire_x if r['ケーブル番号'] == 'M39038'), '図面の対応｜線番設定表')
    fab_x = [r for r in result['rows'] if r['一覧表の図番'] and r['cells']['図面の対応｜製作図'] == '×']
    assert [(r['ケーブル番号'], '0件' in basis(result, r, '図面の対応｜製作図')) for r in fab_x] == [('M65219', True)]
    # 405（第4弾）: 色の食い違い（405 は (BU/RD)・(BU/BK)、製作図は (TQ/RD)・(TQ/BK)）
    row = row_of(result, 'M39664')
    assert row['cells']['芯線｜色'] == '×' and 'TQ' in basis(result, row, '芯線｜色') + ''.join(
        w['製作図の色'] for w in result['wires'] if w['ケーブル番号'] == 'M39664')
    # SG のドレイン線（405 の PIN・線番に SG があるだけ）は △
    row = row_of(result, 'M39613')
    assert (row['cells']['芯線｜PIN'], row['cells']['芯線｜線番']) == ('△', '△')
    assert 'SG/SH を除いて一致' in basis(result, row, '芯線｜PIN')
    # 610-01B の DC PS1 R1(SLAVE) は、領域の2番目の候補（SHIELD COVER が最有力）
    row = row_of(result, 'M65204')
    assert '展開接続図 ○ 領域（DC PS1 R1(SLAVE) (GEXUS15)）' in basis(result, row, '端末2｜ユニット')
    # 展開接続図が無い（アップロードに無い）ケーブルは × で、その項目の根拠に「パターン」が出る
    row = row_of(result, 'M65A06')
    assert row['cells']['図面の対応｜展開接続図'] == '×' and 'EE****-405-14: 0件' in basis(result, row, '図面の対応｜展開接続図')


def test_unit_evidence_kinds_all_occur_in_the_real_data(result):
    """ユニットの根拠（領域・機器・近傍）と GND 線の相手側の対象外が、実データでそれぞれ使われている。"""
    basis_all = [d['判断根拠'] for d in result['details'] if d['項目'].endswith('ユニット')]
    counts = {w: sum(f'展開接続図 {sym} {w}（' in b for b in basis_all for sym in ('○', '△')) for w in ('領域', '機器', '近傍')}
    assert all(n >= 5 for n in counts.values()), counts
    assert sum('GND 線の相手側は対象外' in b for b in basis_all) == 12
    heater = [b for b in basis_all if '機器（BH' in b]
    assert len(heater) == 16                      # 602-01E のヒーター（コネクタの先の配線をたどって機器の箱の名前を読む）


def test_excel_matches_preview_and_has_all_rows(result):
    sheets = dict(preview_sheets(result))
    # シートの並びと行数。All Diff＝ケーブル 217（両方108・展開接続図だけ2・製作図だけ107）、グループは 393 のサブタイトル優先の2つ
    # （405 全体と突き合わせるので、グループの行＝そのグループのケーブル＋405 にだけあるケーブル）、図面一覧＝129図面、ケーブル抽出＝405 の110＋393 の267
    assert {k: len(v) for k, v in sheets.items()} == {
        'Summary': 31, '記号の説明': LEGEND_ROWS, '図面一覧': 129, '図面の関係': 23, 'ケーブル抽出': 377, 'All Diff': 217, 'ケーブル一覧表 Diff': 124, '線番設定表 Diff': 205,
        '整合性チェック': 228, '照合明細': 4144, '芯線照合': 1101, '展開接続図': 628, 'ケーブル一覧表': 134, '線番設定表': 403,
        '製作図の接続表': 456, '製作図の文字': 3852, '図番パターン照合': 174}
    assert list(sheets) == ['Summary', '記号の説明', '図面一覧', '図面の関係', 'ケーブル抽出', 'All Diff', 'ケーブル一覧表 Diff', '線番設定表 Diff', '整合性チェック', '照合明細',
                            '芯線照合', '展開接続図', 'ケーブル一覧表', '線番設定表', '製作図の接続表', '製作図の文字', '図番パターン照合']
    index = sheets['図面一覧']
    assert index.groupby('セット').size().to_dict() == {'展開接続図（405）': 23, 'ケーブル製作図（393）': 106}
    assert (index['備考'] == '照合に使っていない').sum() == 7 and index[index['備考'] != '照合に使っていない']['備考'].eq('').all()
    wb = build_workbook(result)
    book = pd.read_excel(io.BytesIO(wb), sheet_name=None, keep_default_na=False)
    assert list(book) == list(sheets)
    for name, frame in sheets.items():
        if name != '整合性チェック':                 # 2段見出し（行数が2行多く読まれる）
            assert len(book[name]) == len(frame), name
    assert len(book['整合性チェック']) == 228 + 1       # 2段目の見出し（項目名）が1行目のデータとして読まれる


def _dxf_bytes(name):
    for folder in (DATA_405_DIR, DATA_DIR):
        path = os.path.join(folder, name + '.dxf')
        if os.path.exists(path):
            return open(path, 'rb').read()
    raise AssertionError(name)


def test_cable_focus_m65206_acceptance(result):
    """守るもの（ユーザーの目視確認と同じ M65206）: 展開接続図 EE6892-610-01B で M65206 を選ぶと、一覧表 → 線番設定表 → 製作図が並び、
    各図面の強調は照合の結果（×＝製作図の (WH)・展開接続図の (BK) の色、△＝端末2 の ALS1 の近傍）と一致する。全マークが図面の文字に当たる。"""
    from model import cable_focus, cable_preview
    secs = cable_focus.focus(result, 'M65206', 'EE6892-610-01B')
    assert [(s['role'], s['drawing']) for s in secs] == [
        ('展開接続図', 'EE6892-610-01B'), ('ケーブル一覧表', 'EE6892-365-61C'), ('線番設定表', 'EE3298-634-07E'), ('ケーブル製作図', 'EE3298-534-07D')]
    assert [cable_focus.counts_label(s['counts']) for s in secs] == ['×1 ？0 △1 ○9', '×0 ？0 △1 ○10', '×0 ？0 △2 ○8', '×1 ？0 △0 ○8']
    symbols = {s['drawing']: {(m['text'], m['symbol']) for m in s['marks']} for s in secs}
    assert ('(WH)', '×') in symbols['EE3298-534-07D'] and ('(BK)', '×') in symbols['EE6892-610-01B']
    assert ('ALS1', '△') in symbols['EE6892-365-61C'] and ('ALS1 R1', '△') in symbols['EE3298-634-07E']
    assert ('M65206', '○') in symbols['EE6892-365-61C']      # 同じ文字の「関連」も付くが、viewer は重いほう（○）を使う
    for s in secs:
        data = cable_preview.build_viewer_data(cable_preview.render_base_svg(_dxf_bytes(s['drawing'])), s['drawing'], s['marks'])
        assert data['missing'] == 0 and data['marked'] and data['count'] > 0, s['drawing']
    # 「関連」（照合の範囲）は viewer に出さない。選ばなかった他のケーブル（M65202 など）の文字も白のまま
    legend = [l[0] for l in cable_preview.build_viewer_data(
        cable_preview.render_base_svg(_dxf_bytes('EE6892-610-01B')), 'x', secs[0]['marks'])['legend']]
    assert legend == ['× 不整合', '△ 一致（根拠が弱い）', '○ 一致']


def test_cable_focus_every_cable_of_every_schematic_has_matching_texts(result):
    """守るもの: 展開接続図のどのケーブルを選んでも、読み取りで得たマークが図面の文字（基準点）に当たる（missing が出るのは ATTRIB の文字だけ）。
    選択肢はその図面の抽出結果と一致する。"""
    from model import cable_focus, cable_preview
    total = missing = 0
    for d in result['drawings']:
        s = d.get('schematic')
        if d['set'] != '405' or not s:
            continue
        base = cable_preview.render_base_svg(_dxf_bytes(d['drawing_number']))
        cables = cable_focus.cables_in(result, d['drawing_number'])
        assert cables and set(cables) >= {o['cable'] for o in s['occurrences']}
        for cable in cables:
            for sec in cable_focus.focus(result, cable, d['drawing_number']):
                if sec['drawing'] != d['drawing_number']:
                    continue
                data = cable_preview.build_viewer_data(base, d['drawing_number'], sec['marks'])
                total += len(sec['marks'])
                missing += data['missing']
    assert total > 0 and missing / total < 0.02, (missing, total)


def test_drawing_refs_agree_with_the_references_the_schematic_reader_found(result):
    """守るもの: 画面の「関連図面」（描画した文字から拾う）が、展開接続図として読んだ18枚で、読み取りの図中の参照のパターンと一致する。
    EE6892-405-08A に当たる参照は `-405-08`（アップロードした 405 のセット内）。"""
    from model import cable_focus, cable_preview
    names = [d['drawing_number'] for d in result['drawings']]
    read = [d for d in result['drawings'] if d.get('schematic')]
    assert len(read) == 18
    for d in read:
        texts = [t['text'] for t in cable_preview.render_base_svg(_dxf_bytes(d['drawing_number'])).texts]
        got = {r['pattern'] for r in cable_focus.drawing_refs(texts, names)}
        want = {r['pattern'] for r in d['schematic']['refs']}
        assert got == want, d['drawing_number']
    refs = cable_focus.drawing_refs([t['text'] for t in cable_preview.render_base_svg(_dxf_bytes('EE6892-600-01A')).texts], names)
    assert {r['pattern']: r['matches'] for r in refs}['-405-08'] == ['EE6892-405-08A']


def test_relation_sheet_real_data(result):
    """守るもの: 「図面の関係」は展開接続図 23 枚の行で、M65206 の展開接続図 EE6892-610-01B に一覧表 EE6892-365-61C・線番設定表 EE3298-634-07E・
    製作図 EE3298-534-07D が並ぶ。"""
    frame = dict(preview_sheets(result))['図面の関係']
    assert len(frame) == 23
    row = frame[frame['展開接続図'] == 'EE6892-610-01B'].iloc[0]
    assert 'EE6892-365-61C' in row['ケーブル一覧表'] and 'EE3298-634-07E' in row['線番設定表'] and 'EE3298-534-07D' in row['ケーブル製作図']


def test_cable_shapes_m65206_acceptance(result):
    """守るもの（ユーザーの目視確認と同じ M65206）: 図形の強調。展開接続図 EE6892-610-01B は引出線・楕円が○、芯線2本は ○ と ×（色 (WH)≠(BK)）、
    端末は端子の円（TBR1B）2つとコネクタ枠（CN1。2本の芯線で同じ枠）が○。製作図 EE3298-534-07D の接続の線は ○ と ×。一覧表・線番設定表に図形は無い。
    図形はすべて図面の範囲（viewBox）に載る。"""
    from model import cable_focus, cable_preview
    secs = {s['drawing']: s for s in cable_focus.focus(result, 'M65206', 'EE6892-610-01B')}
    sch = secs['EE6892-610-01B']['shapes']
    assert {(s['kind'], s['symbol']) for s in sch if s['item'] == '図面の対応｜展開接続図'} == {('lines', '○'), ('ellipse', '○')}
    assert sorted(s['symbol'] for s in sch if s['item'] == '芯線') == sorted(['○', '×'])
    assert sorted((s['kind'], s['symbol']) for s in sch if s['kind'] in ('rect', 'circle')) == [('circle', '○')] * 2 + [('rect', '○')]    # 同じコネクタ枠 CN1 は1つにまとまる
    assert sorted(s['symbol'] for s in secs['EE3298-534-07D']['shapes']) == sorted(['○', '×'])
    assert secs['EE6892-365-61C']['shapes'] == [] and secs['EE3298-634-07E']['shapes'] == []
    for name in ('EE6892-610-01B', 'EE3298-534-07D'):
        base = cable_preview.render_base_svg(_dxf_bytes(name))
        paths = cable_preview.shape_paths(base, secs[name]['shapes'])
        nums = [float(v) for p in paths for v in re.findall(r'-?\d+\.\d', p['d'])]
        assert paths and 0 <= min(nums) and max(nums) <= max(base.view_box) * 1.001, name


def test_cable_shapes_never_paint_wires_without_both_ends(result):
    """守るもの（S1）: 展開接続図の芯線の図形は、両端に端末がある芯線だけ。端末の無い芯線（EE6888-650-01C の M06019 が区画の枠線を拾う誤り）は塗らない。
    全展開接続図の全ケーブルで、塗る芯線の数は『両端に端末がある芯線』の数を超えない。"""
    from model import cable_focus
    for d in result['drawings']:
        s = d.get('schematic')
        if d['set'] != '405' or not s:
            continue
        for cable in cable_focus.cables_in(result, d['drawing_number']):
            n_ok = sum(all((w['ends'].get(side) or {}).get('kind') for side in '-+')
                       for o in s['occurrences'] if o['cable'] == cable for w in o['wires'])
            painted = sum(len([x for x in sec['shapes'] if x['item'] == '芯線'])
                          for sec in cable_focus.focus(result, cable, d['drawing_number']) if sec['drawing'] == d['drawing_number'])
            assert painted <= n_ok, (d['drawing_number'], cable, painted, n_ok)
    bad = [sec for sec in cable_focus.focus(result, 'M06019', 'EE6888-650-01C') if sec['drawing'] == 'EE6888-650-01C']
    assert all(x['item'] != '芯線' for sec in bad for x in sec['shapes'])


def test_cable_shape_data_does_not_change_the_existing_counts(result):
    """守るもの: 図形のために足した読み取りの座標は、既存の数（接続 456・描き方の分布）を変えない。"""
    assert sum(len(d['fab_conns']) for d in result['drawings']) == 456
    hows = {}
    for d in result['drawings']:
        for o in (d.get('schematic') or {}).get('occurrences', []):
            hows[o['how']] = hows.get(o['how'], 0) + 1
    assert hows == {'ellipse': 42, 'single': 32, 'via-line': 25, 'beside': 14, 'capsule': 13}
