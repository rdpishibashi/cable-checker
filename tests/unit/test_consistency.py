"""整合性チェックの照合 0〜7（`check_consistency()`）の合成データテスト。

図面の dict を直接組み立てて渡す（DXF は読まない）。定義は docs/handoff_consistency_check.md の表。
基準の図面セット `build()` は、全照合が OK になる連鎖（一覧表 → 製作図 → 線番設定表）:
  一覧表 EE6892-365-61C の M65206 →（製作図パターン EE****-534-07）→ 製作図 EE3298-534-07D
  →（注記 -634-07）→ 線番設定表 EE3298-634-07E。各テストはこれを1か所だけ壊して、その照合の結果を見る。
"""
import copy

import pytest

from model.cable_list_table import FIELDS
from model.consistency import (
    C0, C1, C2, C3, C4, C5, C6A, C6B, C7, CHECK_NAMES, NA, NG, OK, check_consistency,
    fab_pattern_regex, wire_pattern_regex,
)

CABLE = 'M65206'
LIST_NAME, FAB_NAME, WIRE_NAME = 'EE6892-365-61C', 'EE3298-534-07D', 'EE3298-634-07E'


def list_row(**kw):
    # 実際の行（cable_list_table.parse）と同じく、全項目と「削除された値」を必ず持つ
    row = {**dict.fromkeys(FIELDS, ''), '行': 1, '削除された値': '',
           'ケーブル番号': CABLE, '製作図パターン': 'EE****-534-07',
           '端末1_ユニット': 'RACK', '端末1_機器符号': 'TBR1B', '端末1_部品種別': 'R2-3.5',
           '端末2_ユニット': 'ALS1', '端末2_部品種別': 'CONN', 'ケーブル種類': 'ACCESSORY CABLE'}
    row.update(kw)
    return row


def wire_row(cable=CABLE, n=(1, 2), lines1=('RACK', 'TBR1B'), lines2=('ALS1',), row=1):
    r = {'表': 1, '行': row, 'ケーブル番号': cable, '削除された値': '',
         '_lines': {'接続先1': list(lines1), '接続先2': list(lines2)}}
    r.update({f'線番{i}': 'x' for i in n})
    return r


def drawing(name, **kw):
    d = {'drawing_number': name, 'no_text': False, 'kinds': [], 'list_rows': [], 'wire_rows': [],
         'wire_notes': [], 'fab_wire_numbers': set(), 'fab_notes': [], 'fab_texts': [], 'fab_text': '',
         'fab_conns': []}
    d.update(kw)
    return d


def build():
    return [
        drawing(LIST_NAME, list_rows=[list_row()]),
        drawing(FAB_NAME, fab_text='R2-3.5CONNACCESSORYCABLE', fab_notes=['-634-07'], fab_wire_numbers={1, 2}),
        drawing(WIRE_NAME, wire_rows=[wire_row()], wire_notes=['EE****-534-07']),
    ]


def results(drawings, cable=CABLE, row=None):
    """{照合: (結果, 内容)}。一覧表の行を指定しなければ最初の行。"""
    res = check_consistency(drawings)
    rows = [c for c in res['checks'] if c['ケーブル番号'] == cable and (row is None or c['一覧表の行'] == row)]
    first = rows[0]['一覧表の行']
    return {c['照合']: (c['結果'], c['内容']) for c in rows if c['一覧表の行'] == first}


def by_name(drawings, name):
    return next(d for d in drawings if d['drawing_number'] == name)


def test_baseline_chain_is_all_ok_and_shows_drawing_numbers():
    """守るもの: 連鎖が正しければ 0〜7 がすべて OK。製作図・線番設定表の図番が行に出る。"""
    drawings = build()
    r = results(drawings)
    assert list(r) == CHECK_NAMES
    assert {v[0] for v in r.values()} == {OK}, r
    row = check_consistency(drawings)['checks'][0]
    assert (row['一覧表の図番'], row['一覧表の行'], row['ケーブル製作図の図番'], row['線番設定表の図番']) == \
        (LIST_NAME, 1, FAB_NAME, WIRE_NAME)


def test_0_cable_only_in_wire_table_is_ng_without_other_checks():
    """守るもの: 線番設定表にだけあるケーブル番号は 0 が NG（1〜7 は出さない）。"""
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'].append(wire_row(cable='M99999'))
    checks = [c for c in check_consistency(drawings)['checks'] if c['ケーブル番号'] == 'M99999']
    assert [(c['照合'], c['結果'], c['一覧表の図番'], c['線番設定表の図番']) for c in checks] == \
        [(C0, NG, '', WIRE_NAME)]


# ---- 1 製作図の図面 ----

def test_1_empty_pattern_is_na_and_downstream_checks_are_na():
    drawings = build()
    by_name(drawings, LIST_NAME)['list_rows'][0]['製作図パターン'] = ''
    r = results(drawings)
    assert r[C1][0] == NA
    assert {r[c][0] for c in [*C2, C3, C4, C5, C6A, C6B, C7]} == {NA}


@pytest.mark.parametrize('mutate, expect', [
    (lambda ds: ds.remove(by_name(ds, FAB_NAME)), '0件'),
    (lambda ds: ds.append(drawing('EE3298-534-07E', fab_text='X')), '2件'),
    (lambda ds: by_name(ds, FAB_NAME).update(no_text=True), '文字が無い'),
], ids=['no_hit', 'two_hits', 'no_text'])
def test_1_ng_cases(mutate, expect):
    """守るもの: 0件／2件以上／当たった図面に文字が無い、はいずれも NG。後続は対象なし。"""
    drawings = build()
    mutate(drawings)
    r = results(drawings)
    assert r[C1][0] == NG and expect in r[C1][1]
    assert {r[c][0] for c in [*C2, C3, C4, C5, C6A, C6B, C7]} == {NA}


def test_1_star_matches_any_character_and_revision_letter_is_optional():
    assert fab_pattern_regex('EE****-534-07').match('EE3298-534-07D')
    assert fab_pattern_regex('EE****-534-07').match('EE3298-534-07')
    assert not fab_pattern_regex('EE****-534-07').match('EE3298-534-08D')
    assert wire_pattern_regex('-634-07').match('EE3298-634-07E')
    assert wire_pattern_regex('-634-07').match('EE3805-634-07')
    assert not wire_pattern_regex('-634-07').match('EE3298-634-071E')


# ---- 2 製作図の記載 ----

def test_2_missing_value_is_ng_and_lists_it():
    drawings = build()
    by_name(drawings, FAB_NAME)['fab_text'] = 'R2-3.5CONN'
    r = results(drawings)
    assert r[C2[2]][0] == NG and 'ACCESSORY CABLE' in r[C2[2]][1]
    assert r[C2[0]][0] == OK and r[C2[1]][0] == OK


def test_2_empty_value_is_na():
    drawings = build()
    by_name(drawings, LIST_NAME)['list_rows'][0]['端末2_部品種別'] = ''
    assert results(drawings)[C2[1]][0] == NA


def test_2_match_is_literal_ignoring_spaces_and_case_only():
    """守るもの: 空白を除き大文字で、文字どおりに含まれるか（読み替えない）。"""
    drawings = build()
    by_name(drawings, LIST_NAME)['list_rows'][0]['ケーブル種類'] = 'accessory  cable'
    assert results(drawings)[C2[2]][0] == OK
    by_name(drawings, LIST_NAME)['list_rows'][0]['ケーブル種類'] = 'UL1277 TC'   # 図面は 'UL AWG14-2C' の形（読み替えない）
    assert results(drawings)[C2[2]][0] == NG


# ---- 3 線番設定表の図面 ----

def test_3_no_note_is_na():
    drawings = build()
    by_name(drawings, FAB_NAME)['fab_notes'] = []
    r = results(drawings)
    assert r[C3][0] == NA
    assert {r[c][0] for c in [C4, C5, C6A, C6B, C7]} == {NA}


@pytest.mark.parametrize('mutate, expect', [
    (lambda ds: ds.remove(by_name(ds, WIRE_NAME)), '0件'),
    (lambda ds: ds.append(drawing('EE3298-634-07F', wire_rows=[wire_row()])), '2件'),
    (lambda ds: by_name(ds, WIRE_NAME).update(no_text=True), '文字が無い'),
], ids=['no_hit', 'two_hits', 'no_text'])
def test_3_ng_cases(mutate, expect):
    drawings = build()
    mutate(drawings)
    r = results(drawings)
    assert r[C3][0] == NG and expect in r[C3][1]
    assert {r[c][0] for c in [C4, C5, C6A, C6B, C7]} == {NA}


# ---- 4・5 ----

def test_4_cable_missing_in_wire_table_is_ng():
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'] = [wire_row(cable='M00001')]
    r = results(drawings)
    assert r[C4][0] == NG
    assert {r[c][0] for c in [C6A, C6B, C7]} == {NA}


def test_4_cell_with_two_cable_numbers_counts_for_both():
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'] = [wire_row(cable=f'{CABLE} | M65207')]
    assert results(drawings)[C4][0] == OK


def test_5_note_pattern_must_be_one_of_the_list_patterns():
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_notes'] = ['EE****-534-08']
    r = results(drawings)
    assert r[C5][0] == NG and 'EE****-534-08' in r[C5][1]
    by_name(drawings, WIRE_NAME)['wire_notes'] = []
    assert results(drawings)[C5][0] == NA


# ---- 6a・6b ----

def test_6a_unit_mismatch_is_ng_with_row_and_value():
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'] = [wire_row(lines2=('OSSET',))]
    r = results(drawings)
    assert r[C6A][0] == NG and '表1 行1 接続先2=OSSET' in r[C6A][1]


def test_6a_prefix_match_is_ok_and_says_approximate():
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('MPD RACK1',), lines2=('ALS1 F1',))]
    by_name(drawings, LIST_NAME)['list_rows'][0].update({'端末1_ユニット': 'MPD RACK', '端末1_機器符号': ''})
    r = results(drawings)
    assert r[C6A][0] == OK and '近似' in r[C6A][1]


def test_6a_either_end_of_the_list_row_may_match():
    """守るもの: 接続先1・2 のユニット名は、一覧表の端末1・2 のどちらに合ってもよい。"""
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('ALS1',), lines2=('RACK',))]
    assert results(drawings)[C6A][0] == OK


def test_6b_terminal_must_match_exactly():
    """守るもの: 端子台は完全一致（モジュール位置だけ違う TBR1B と TBF1B は別物）。"""
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('RACK', 'TBF1B'))]
    r = results(drawings)
    assert r[C6B][0] == NG and 'TBF1B' in r[C6B][1]
    assert r[C6A][0] == OK


def test_6b_na_when_no_terminal():
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('RACK',))]
    assert results(drawings)[C6B][0] == NA


# ---- 7 ----

def test_7_wire_number_sets_must_match():
    drawings = build()
    by_name(drawings, WIRE_NAME)['wire_rows'] = [wire_row(n=(1, 2, 3))]
    r = results(drawings)
    assert r[C7][0] == NG and '*1,*2 ' in r[C7][1] and '*1,*2,*3' in r[C7][1]


def test_7_na_without_star_numbers_in_fab_drawing():
    drawings = build()
    by_name(drawings, FAB_NAME)['fab_wire_numbers'] = set()
    assert results(drawings)[C7][0] == NA


# ---- 複数行・並び ----

def test_each_list_row_of_a_cable_is_checked_separately():
    """守るもの: 一覧表でケーブル番号が複数行にあるときは、行ごとに 1〜7 を出す。"""
    drawings = build()
    rows = by_name(drawings, LIST_NAME)['list_rows']
    rows.append(list_row(行=2, 製作図パターン='EE****-999-99'))
    res = check_consistency(drawings)
    first, second = results(drawings, row=1), results(drawings, row=2)
    assert first[C1][0] == OK and second[C1][0] == NG
    assert len([c for c in res['checks'] if c['ケーブル番号'] == CABLE]) == 2 * len(CHECK_NAMES)


def test_rows_are_sorted_by_cable_then_list_drawing_then_row_then_check():
    drawings = build()
    by_name(drawings, LIST_NAME)['list_rows'].append(list_row(行=2, ケーブル番号='M00001'))
    keys = [(c['ケーブル番号'], c['一覧表の行'], CHECK_NAMES.index(c['照合']))
            for c in check_consistency(drawings)['checks']]
    assert keys == sorted(keys)


def test_empty_input_gives_no_rows():
    assert check_consistency([])['checks'] == []
    assert check_consistency(copy.deepcopy([drawing('EE0001-001-01A')]))['checks'] == []
