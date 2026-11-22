"""整合性チェック（`check_consistency()`・`analyze_uploads()`）の合成データテスト。

図面の dict を直接組み立てて渡す（DXF は読まない）。定義は docs/CONSISTENCY_CHECK.md。
基準の図面セット `build()` は、393 の連鎖（一覧表 → 製作図 → 線番設定表）が全部 ○ になる:
  一覧表 EE6892-365-61C の M65206 →（製作図パターン EE****-534-07）→ 製作図 EE3298-534-07D
  →（注記 -634-07）→ 線番設定表 EE3298-634-07E。各テストはこれを1か所だけ壊して、その項目の記号と根拠を見る。
展開接続図は `with_schematic()` で足す（接続図パターン EE****-610-**。図番 EE6892-610-01B）。
"""
import copy
import os

import pytest

from model import consistency as C
from model.cable_list_table import FIELDS
from model.consistency import ITEM_COLUMNS, NA, NG, OK, UNREAD, WEAK, check_consistency, item_column

CABLE = 'M65206'
LIST_NAME, FAB_NAME, WIRE_NAME, SCH_NAME = 'EE6892-365-61C', 'EE3298-534-07D', 'EE3298-634-07E', 'EE6892-610-01B'

COL = {k: item_column(*g) for k, g in {
    'list': ('図面の対応', '一覧表'), 'fab': ('図面の対応', '製作図'), 'wire': ('図面の対応', '線番設定表'),
    'sch': ('図面の対応', '展開接続図'),
    'u1': ('端末1', 'ユニット'), 'm1': ('端末1', 'モジュール'), 'c1': ('端末1', '機器符号'), 'p1': ('端末1', '部品種別'),
    'u2': ('端末2', 'ユニット'), 'm2': ('端末2', 'モジュール'), 'c2': ('端末2', '機器符号'), 'p2': ('端末2', '部品種別'),
    'kind': ('ケーブル', '種類'), 'core': ('ケーブル', '芯数'),
    'num': ('芯線', '線番'), 'pin': ('芯線', 'PIN'), 'color': ('芯線', '色'),
}.items()}


# ---------------------------------------------------------------- 図面の組み立て

def list_row(**kw):
    # 実際の行（cable_list_table.parse）と同じく、全項目と「削除された値」を必ず持つ
    row = {**dict.fromkeys(FIELDS, ''), '行': 1, '削除された値': '',
           'ケーブル番号': CABLE, '製作図パターン': 'EE****-534-07',
           '端末1_ユニット': 'RACK', '端末1_機器符号': 'TBR1B', '端末1_部品種別': 'R2-3.5',
           '端末2_ユニット': 'ALS1', '端末2_部品種別': 'CONN', 'ケーブル種類': 'ACCESSORY CABLE'}
    row.update(kw)
    return row


def wire_row(cable=CABLE, n=(1, 2), lines1=('RACK', 'TBR1B'), lines2=('ALS1',), row=1, values=None):
    r = {'表': 1, '行': row, 'ケーブル番号': cable, '削除された値': '',
         '_lines': {'接続先1': list(lines1), '接続先2': list(lines2)}}
    r.update({f'線番{i}': (values or {}).get(i, 'x') for i in n})
    return r


def drawing(name, **kw):
    d = {'drawing_number': name, 'set': '393', 'no_text': False, 'kinds': [], 'list_rows': [], 'wire_rows': [],
         'wire_notes': [], 'fab_wire_numbers': set(), 'fab_notes': [], 'fab_texts': [], 'fab_text': '',
         'fab_conns': [], 'schematic': None}
    d.update(kw)
    return d


def build():
    return [
        drawing(LIST_NAME, kinds=['ケーブル一覧表'], list_rows=[list_row()]),
        drawing(FAB_NAME, kinds=['ケーブル製作図'], fab_text='R2-3.5CONNACCESSORYCABLE', fab_notes=['-634-07'],
                fab_wire_numbers={1, 2}),
        drawing(WIRE_NAME, kinds=['線番設定表'], wire_rows=[wire_row()], wire_notes=['EE****-534-07']),
    ]


def by_name(drawings, name):
    return next(d for d in drawings if d['drawing_number'] == name)


def end(kind='box', name=None, pin=None, regions=(), devices=(), nearby=()):
    return {'kind': kind, 'name': name, 'pin': pin, 'regions': list(regions), 'devices': list(devices), 'nearby': list(nearby)}


def wire(minus=None, plus=None, labels=()):
    ends = {}
    if minus is not None:
        ends['-'] = minus
    if plus is not None:
        ends['+'] = plus
    return {'labels': list(labels), 'ends': ends}


def occurrence(wires, cable=CABLE, how='ellipse', remark=''):
    return {'cable': cable, 'remark': remark, 'how': how, 'wires': wires}


def standard_wires():
    """M65206 の 405: 左が端子台 TBR1B（領域 RACK）、右がコネクタ CN1（近くに ALS1 R1 のラベル）。2本の芯線のピンは 1/A・2/B。"""
    near = [('CN1', 0.0, 'CN1'), ('ALS1 R1', 33.0, 'ALS1 R1\n(GEXUS-ARC2)')]
    return [wire(end('circle', 'TBR1B', str(n), regions=['RACK']), end('box', 'CN1', pin, nearby=near), labels=['(BK)'])
            for n, pin in (('1', 'A'), ('2', 'B'))]


def with_schematic(drawings, wires=None, title='', occ=None, row=None, name=SCH_NAME, pattern='EE****-610-**',
                   keep_wire_table=False, **occ_kw):
    """基準の図面セットに、展開接続図 SCH_NAME（と一覧表の接続図パターン）を足して返す。

    既定では製作図の注記（線番設定表への参照）を外す。端末の項目が線番設定表と 405 の両方で比べられて最も重い記号になるので、
    405 の根拠だけを見るテストでは線番設定表を使わない。`keep_wire_table=True` で両方を使う。
    """
    ds = copy.deepcopy(drawings)
    if not keep_wire_table:
        by_name(ds, FAB_NAME)['fab_notes'] = []
    by_name(ds, LIST_NAME)['list_rows'][0].update({'接続図パターン': pattern, **(row or {})})
    occs = [occ] if occ is not None else [occurrence(standard_wires() if wires is None else wires, **occ_kw)]
    ds.append(drawing(name, set='405', kinds=['展開接続図'], schematic={'title': title, 'error': '', 'no_text': False,
                                                        'region_error': '', 'occurrences': occs}))
    return ds


def run(drawings, cable=CABLE, row_no=None):
    res = check_consistency(drawings)
    rows = [r for r in res['rows'] if r['ケーブル番号'] == cable and (row_no is None or r['一覧表の行'] == row_no)]
    return res, rows[0]


def cell(drawings, key, sub=0, **kw):
    """(記号, 判断根拠) — 整合性チェックの1セルの記号と、リンク先の照合明細の行の判断根拠。

    `sub` はリンク先から数える明細の行（線番設定表だけ3行: 0=図面の特定・1=記載・2=注記の製作図）。
    """
    res, row = run(drawings, **kw)
    col = COL[key]
    return row['cells'][col], res['details'][row['links'][col] + sub]['判断根拠']


def sym(drawings, key, **kw):
    return cell(drawings, key, **kw)[0]


# ---------------------------------------------------------------- 基準・形

def test_baseline_393_chain_and_all_items_have_a_cell_and_a_detail_row():
    res, row = run(build())
    assert set(row['cells']) == set(ITEM_COLUMNS) and set(row['links']) == set(ITEM_COLUMNS)
    assert [sym(build(), k) for k in ('list', 'fab', 'wire')] == [OK, OK, OK]
    assert sym(build(), 'sch') == NA                 # 展開接続図が1枚も読まれていない
    assert row['不整合数'] == 0 and row['要確認数'] == 0
    detail = res['details'][row['links'][COL['wire']]]
    assert detail['ケーブル番号'] == CABLE and detail['一覧表の図番'] == LIST_NAME and detail['一覧表の行'] == 1
    assert FAB_NAME in res['details'][row['links'][COL['fab']]]['参照した図面']
    assert WIRE_NAME in detail['参照した図面']


def test_details_have_one_row_per_cell_and_three_for_the_wire_table_and_links_point_at_them():
    res, row = run(build())
    assert len(res['details']) == len(ITEM_COLUMNS) + 2          # 線番設定表だけ3行（図面の特定・記載・注記の製作図）
    labels = [d['項目'] for d in res['details']]
    assert labels[row['links'][COL['wire']]:][:3] == ['線番設定表 図面の特定', '線番設定表 記載', '線番設定表 注記の製作図']
    # 各セルの記号と、リンク先の明細の判定が一致する（線番設定表は3行の最も重いもの）
    for col in ITEM_COLUMNS:
        assert res['details'][row['links'][col]]['判定'] in ('○', '△', '×', '？', '－')


def test_cable_only_in_wire_table_gets_a_row_with_list_x_and_others_na():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'].append(wire_row(cable='M99999'))
    res, row = run(ds, cable='M99999')
    assert row['一覧表の図番'] == '' and row['一覧表の行'] == ''
    assert row['cells'][COL['list']] == NG and row['不整合数'] == 1
    assert all(row['cells'][c] == NA for c in ITEM_COLUMNS if c != COL['list'])
    detail = res['details'][row['links'][COL['list']]]
    assert WIRE_NAME in detail['判断根拠'] and detail['線番設定表の値'] == WIRE_NAME


def test_rows_are_sorted_by_cable_then_list_drawing_then_row_and_each_list_row_is_checked_separately():
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'] = [list_row(行=2, 端末1_部品種別='NOPE'), list_row(行=1)]
    ds.append(drawing('EE0001-001-01A', list_rows=[list_row(ケーブル番号='M00001')]))
    res = check_consistency(ds)
    assert [(r['ケーブル番号'], r['一覧表の行']) for r in res['rows']] == [('M00001', 1), (CABLE, 1), (CABLE, 2)]
    assert sym(ds, 'p1', row_no=1) == OK and sym(ds, 'p1', row_no=2) == NG


def test_empty_input_gives_no_rows():
    res = check_consistency([])
    assert res['rows'] == res['details'] == res['wires'] == res['unused'] == []


def test_drawing_without_a_kind_or_pattern_hit_is_unused():
    """どの種類にも当たらず、どのパターンも指さない図面だけが「照合に使っていない図面」。パターンが指す図面は種類が無くても使われている。"""
    assert check_consistency(build())['unused'] == []
    ds = build() + [drawing('EE0002-002-02A')]
    assert check_consistency(ds)['unused'] == ['EE0002-002-02A']
    ds = build()
    by_name(ds, FAB_NAME)['kinds'] = []                   # 汎用の製作図のように、表が無くても一覧表のパターンが指している
    assert check_consistency(ds)['unused'] == []


# ---------------------------------------------------------------- 図面の対応（旧 照合1・3・4・5）

def test_fab_pattern_empty_is_na_and_downstream_items_have_no_basis():
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'][0]['製作図パターン'] = ''
    assert sym(ds, 'fab') == NA
    assert sym(ds, 'wire') == NA and sym(ds, 'p1') == NA and sym(ds, 'num') == NA


@pytest.mark.parametrize('mutate, expect', [
    (lambda ds: by_name(ds, LIST_NAME)['list_rows'][0].update({'製作図パターン': 'EE****-999-99'}), '0件'),
    (lambda ds: ds.append(drawing('EE9999-534-07A', fab_text='X')), '2件'),
    (lambda ds: by_name(ds, FAB_NAME).update({'no_text': True}), '文字が無い'),
])
def test_fab_pattern_ng_cases(mutate, expect):
    ds = build()
    mutate(ds)
    s, basis = cell(ds, 'fab')
    assert s == NG and expect in basis


def test_pattern_star_matches_any_character_and_revision_letter_is_optional():
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'][0]['製作図パターン'] = 'EE3298-534-07D'   # 改訂記号つきの完全指定
    assert sym(ds, 'fab') == OK
    by_name(ds, LIST_NAME)['list_rows'][0]['製作図パターン'] = 'EE32**-534-07'
    assert sym(ds, 'fab') == OK


def test_wire_table_note_absent_is_na_and_missing_pattern_or_row_is_ng():
    ds = build()
    by_name(ds, FAB_NAME)['fab_notes'] = []
    assert sym(ds, 'wire') == NA
    ds = build()
    by_name(ds, FAB_NAME)['fab_notes'] = ['-999-99']
    s, basis = cell(ds, 'wire')                       # 図面の特定（1行目）
    assert s == NG and '0件' in basis
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(cable='M11111')]
    s, basis = cell(ds, 'wire', sub=1)                # 記載（2行目）
    assert s == NG and 'の行が無い' in basis


def test_wire_table_row_with_two_cable_numbers_counts_for_both():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(cable=f'{CABLE} | M11111')]
    assert sym(ds, 'wire') == OK


def test_wire_table_note_must_name_one_of_the_list_fab_patterns():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_notes'] = ['EE****-999-99']
    s, basis = cell(ds, 'wire', sub=2)                # 注記の製作図（3行目）
    assert s == NG and '≠' in basis
    ds = build()
    by_name(ds, WIRE_NAME)['wire_notes'] = []
    assert sym(ds, 'wire') == OK                 # 注記に製作図が無い比較は根拠なし（他の2つは ○）


# ---------------------------------------------------------------- 部品種別・ケーブル種類（製作図の文字）

def test_part_type_missing_in_fab_text_is_ng_and_lists_it():
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'][0]['端末1_部品種別'] = 'R9-9'
    s, basis = cell(ds, 'p1')
    assert s == NG and 'R9-9' in basis


def test_part_type_empty_is_na():
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'][0]['端末1_部品種別'] = ''
    assert sym(ds, 'p1') == NA


def test_part_type_match_is_literal_ignoring_spaces_and_case_only():
    ds = build()
    by_name(ds, FAB_NAME)['fab_text'] = 'D-SUB25PACCESSORYCABLE'
    by_name(ds, LIST_NAME)['list_rows'][0].update({'端末1_部品種別': 'd-sub 25P', 'ケーブル種類': 'AWG20'})
    assert sym(ds, 'p1') == OK
    assert sym(ds, 'kind') == NG                  # サイズ・芯数の形への読み替えはしない


# ---------------------------------------------------------------- 機器符号と製作図の文字

def _fab_refs(*texts):
    return [(t, (float(i), 0.0)) for i, t in enumerate(texts)]


def test_device_code_found_in_fab_text_adds_an_ok_fab_comparison_and_marks_the_text():
    """守るもの: 機器符号が製作図の文字1つ全体と一致すれば ○（製作図の根拠）。viewer はその文字を強調する（以前は製作図の機器符号に色が付かなかった）。"""
    ds = build()
    by_name(ds, FAB_NAME)['fab_text_refs'] = _fab_refs('X', 'TBR1B', 'TBR1BX')
    s, basis = cell(ds, 'c1')
    assert s == OK and '製作図' in basis
    marks = check_consistency(ds)['rows'][0]['marks']
    fab = [m for m in marks if m['drawing'] == FAB_NAME and m['item'] == '端末1｜機器符号']
    assert [(m['text'], m['symbol']) for m in fab] == [('TBR1B', OK)]      # 部分一致の `TBR1BX` は強調しない


def test_device_code_absent_from_fab_text_is_not_a_fab_comparison():
    """守るもの: 製作図は機器符号を書かない様式が多い（実データで × にすると、○ だった機器符号のセル122件が × に変わった）ので、無いことは × にしない。比較自体を加えない。"""
    ds = build()
    by_name(ds, FAB_NAME)['fab_text_refs'] = _fab_refs('OTHER')
    s, basis = cell(ds, 'c1')
    assert s == OK and '製作図' not in basis                                # 線番設定表の ○ のまま
    assert not [m for m in check_consistency(ds)['rows'][0]['marks'] if m['drawing'] == FAB_NAME and m['item'] == '端末1｜機器符号']


def test_device_code_fab_match_supports_wildcard_and_needs_all_values():
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'][0]['端末1_機器符号'] = 'TBR1*'
    by_name(ds, FAB_NAME)['fab_text_refs'] = _fab_refs('TBR1B')
    assert '製作図' in cell(ds, 'c1')[1]
    by_name(ds, LIST_NAME)['list_rows'][0]['端末1_機器符号'] = 'TBR1B,TBR2B'      # 複数値は全部が見つからなければ比較しない
    assert '製作図' not in cell(ds, 'c1')[1]


# ---------------------------------------------------------------- 線番設定表との照合（端末のユニット・機器符号・モジュール）

def test_unit_must_match_the_wire_table_destination_and_prefix_is_weak():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('RACK', 'TBR1B'), lines2=('MPD RACK',))]
    by_name(ds, LIST_NAME)['list_rows'][0].update({'端末2_ユニット': 'MPD RACK1'})
    s, basis = cell(ds, 'u2')
    assert s == WEAK and '近似' in basis
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('RACK', 'TBR1B'), lines2=('GAS BOX',))]
    s, basis = cell(ds, 'u2')
    assert s == NG and '合わない' in basis


def test_terminal_block_must_match_exactly():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('RACK', 'TBF1B'))]
    by_name(ds, LIST_NAME)['list_rows'][0]['端末1_機器符号'] = 'TBR1B'
    assert sym(ds, 'c1') == NG                    # 行の選び方（端子台→ユニット）でユニット RACK が合う行を採るので ×（TBR1B≠TBF1B）
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('RACK',), lines2=('ALS1',))]
    assert sym(ds, 'c1') == NA or sym(ds, 'c1') == NG


def test_no_terminal_in_the_wire_table_gives_no_code_basis():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('RACK',), lines2=('ALS1',))]
    by_name(ds, LIST_NAME)['list_rows'][0]['端末1_機器符号'] = ''
    assert sym(ds, 'c1') == NA


def test_either_orientation_of_the_wire_table_may_match_the_list_row():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('ALS1',), lines2=('RACK', 'TBR1B'))]
    assert sym(ds, 'u1') == OK and sym(ds, 'u2') == OK and sym(ds, 'c1') == OK


def test_wire_row_is_selected_by_the_list_terminals_among_module_rows():
    """M65206 は線番設定表にモジュールごとの行がある。一覧表の端末（TBR1B・ALS1 R1）に合う行だけを使う。"""
    ds = build()
    rows = [wire_row(row=1, lines1=('RACK', 'TBF1B'), lines2=('ALS1', 'F1'), values={1: 'S081C', 2: 'T081C'}),
            wire_row(row=5, lines1=('RACK', 'TBR1B'), lines2=('ALS1', 'R1'), values={1: 'S121C', 2: 'T121C'})]
    by_name(ds, WIRE_NAME)['wire_rows'] = rows
    by_name(ds, LIST_NAME)['list_rows'][0].update({'端末2_モジュール': 'R1', '端末2_ユニット': 'ALS1'})
    s, basis = cell(ds, 'c1')
    assert s == OK and '行5' in basis and '行1' not in basis
    assert sym(ds, 'm2') == OK


def test_terminal_rule_has_priority_over_unit_rule_in_row_selection():
    """端子台が合う行があれば、ユニット名だけが合う行（別のモジュール）は使わない。複数行は「どれか1行で合えば」なので、
    間違った行を混ぜると、その行の結果で ○ になってしまう（行1は F1 の行で、線番が3本あるので芯数 3 に合ってしまう）。"""
    ds = build()
    rows = [wire_row(row=1, n=(1, 2, 3), lines1=('RACK', 'TBF1B'), lines2=('ALS1', 'F1')),
            wire_row(row=5, n=(1, 2), lines1=('RACK', 'TBR1B'), lines2=('ALS1', 'R1'))]
    by_name(ds, WIRE_NAME)['wire_rows'] = rows
    by_name(ds, LIST_NAME)['list_rows'][0]['芯数'] = '3'
    s, basis = cell(ds, 'core')
    assert s == NG and '線番設定表 × 2本（EE3298-634-07E 表1 行5）' in basis and '行1' not in basis


def test_no_wire_row_fits_the_list_terminals_is_ng_with_the_rows_listed():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('FOO',), lines2=('BAR',))]
    s, basis = cell(ds, 'u1')
    assert s == NG and '対応する行が無い' in basis
    assert sym(ds, 'c1') == NG and sym(ds, 'num') == NG


def test_wire_module_other_than_the_list_module_is_ng():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('RACK', 'TBR1B'), lines2=('ALS1', 'F1'))]
    by_name(ds, LIST_NAME)['list_rows'][0]['端末2_モジュール'] = 'R1'
    s, basis = cell(ds, 'm2')
    assert s == NG and 'F1' in basis


# ---------------------------------------------------------------- 線番の列（旧 照合7）

def test_wire_number_sets_must_match_between_fab_and_wire_table():
    ds = build()
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(n=(1, 2, 3))]
    s, basis = cell(ds, 'num')
    assert s == NG and '*1,*2' in basis and '*1,*2,*3' in basis


def test_wire_number_check_needs_star_numbers_in_the_fab_drawing():
    ds = build()
    by_name(ds, FAB_NAME)['fab_wire_numbers'] = set()
    assert sym(ds, 'num') == NA


# ---------------------------------------------------------------- 展開接続図: 図面の対応

def test_schematic_cell_states():
    ds = with_schematic(build())
    s, basis = cell(ds, 'sch')
    assert s == OK and SCH_NAME in basis
    # パターンが当たらない → アップロードに無い
    ds = with_schematic(build(), pattern='EE****-999-**')
    s, basis = cell(ds, 'sch')
    assert s == NG and 'アップロードに無い' in basis
    # 当たるが、このケーブル番号の出現が無い
    ds = with_schematic(build(), occ=occurrence([], cable='M11111'))
    s, basis = cell(ds, 'sch')
    assert s == NG and '図面に無い' in basis
    # パターンが空
    ds = with_schematic(build(), pattern='')
    assert sym(ds, 'sch') == NA
    # 文字の無い展開接続図は読めない
    ds = with_schematic(build())
    by_name(ds, SCH_NAME)['schematic'].update({'no_text': True, 'occurrences': []})
    assert sym(ds, 'sch') == UNREAD


def test_pattern_that_only_matches_a_393_drawing_is_not_a_schematic():
    """接続図パターンが 393 の図面（製作図など）にだけ当たるとき、展開接続図は無い（アップロードに無い）。「読めない」ではない。"""
    ds = with_schematic(build(), pattern='EE****-534-**')            # 製作図 EE3298-534-07D にだけ当たる
    by_name(ds, SCH_NAME)['kinds'] = ['展開接続図']
    s, basis = cell(ds, 'sch')
    assert s == NG and 'アップロードに無い' in basis


def test_cable_only_in_a_schematic_gets_a_row_with_list_x():
    ds = with_schematic(build(), occ=occurrence([], cable='M12345'))
    res, row = run(ds, cable='M12345')
    assert row['cells'][COL['list']] == NG and SCH_NAME in res['details'][row['links'][COL['list']]]['判断根拠']


# ---------------------------------------------------------------- 展開接続図: 機器符号・向き

def test_code_matches_and_orientation_is_decided_by_the_codes():
    ds = with_schematic(build(), row={'端末2_機器符号': 'CN1'})
    assert sym(ds, 'c1') == OK and sym(ds, 'c2') == OK
    # 左右が逆に描かれていても、符号が合う向きで照合する
    flipped = [wire(w['ends']['+'], w['ends']['-'], w['labels']) for w in standard_wires()]
    ds = with_schematic(build(), wires=flipped, row={'端末2_機器符号': 'CN1'})
    assert sym(ds, 'c1') == OK and sym(ds, 'c2') == OK


def test_code_wildcard_and_gnd_rules():
    ds = with_schematic(build(), row={'端末2_機器符号': 'C*1'})
    assert sym(ds, 'c2') == OK
    gnd = [wire(end('gnd', 'GND'), end('box', 'CN1', 'A'))]
    ds = with_schematic(build(), wires=gnd, row={'端末1_機器符号': 'GND BAR', '端末2_機器符号': 'CN1'})
    s, basis = cell(ds, 'c1')
    assert s == OK and 'GND を含む' in basis


def test_code_mismatch_is_ng_and_unreadable_names_are_unread():
    ds = with_schematic(build(), row={'端末2_機器符号': 'CN9'})
    s, basis = cell(ds, 'c2')
    assert s == NG and 'CN1' in basis and sym(ds, 'c1') == OK
    nameless = [wire(end('box', None, 'A'), end('box', 'CN1', 'B'))]
    ds = with_schematic(build(), wires=nameless, row={'端末2_機器符号': 'CN1'})
    assert sym(ds, 'c1') == UNREAD


def test_direction_unknown_makes_unit_and_module_unread():
    ds = with_schematic(build(), row={'端末1_機器符号': 'ZZ9', '端末2_機器符号': 'YY8', '端末2_モジュール': 'R1'})
    s, basis = cell(ds, 'u1')
    assert s == UNREAD and '向きが決まらない' in basis
    assert sym(ds, 'm2') == UNREAD


# ---------------------------------------------------------------- 展開接続図: ユニットの根拠

@pytest.mark.parametrize('end_kw, word, symbol', [
    (dict(regions=['RACK']), '領域', OK),
    (dict(regions=['RACK2 (X)']), '領域・近似', WEAK),
    (dict(devices=['RACK']), '機器', WEAK),
    (dict(devices=['RACK1']), '機器・近似', WEAK),
    (dict(nearby=[('RACK', 12.0, 'RACK')]), '近傍', WEAK),
])
def test_unit_evidence_words_and_symbols(end_kw, word, symbol):
    wires = [wire(end('box', 'TBR1B', '1', **end_kw), end('box', 'CN1', 'A'))]
    ds = with_schematic(build(), wires=wires)
    s, basis = cell(ds, 'u1')
    assert s == symbol and word in basis.split('展開接続図')[-1]


def test_unit_region_wins_over_device_and_all_region_candidates_are_used():
    wires = [wire(end('box', 'TBR1B', '1', regions=['SHIELD COVER', 'RACK'], devices=['RACK']), end('box', 'CN1', 'A'))]
    s, basis = cell(with_schematic(build(), wires=wires), 'u1')
    assert s == OK and '領域（RACK）' in basis


def test_nearby_label_counts_only_when_it_is_the_nearest_unit_name_in_the_list():
    """近傍は「一覧表に出てくるユニット名のラベルのうち最も近いもの」が期待値のときだけ。隣のものが近ければ ×。"""
    other = list_row(行=2, ケーブル番号='M22222', 端末1_ユニット='HEATER2')
    ds = with_schematic(build())
    by_name(ds, LIST_NAME)['list_rows'].append(other)
    near = [('HEATER2', 8.0, 'HEATER2'), ('RACK', 20.0, 'RACK')]
    by_name(ds, SCH_NAME)['schematic']['occurrences'][0]['wires'] = [wire(end('box', 'TBR1B', '1', nearby=near), end('box', 'CN1', 'A'))]
    s, basis = cell(ds, 'u1')
    assert s == NG and 'HEATER2' in basis
    near = [('RACK', 8.0, 'RACK'), ('HEATER2', 20.0, 'HEATER2')]
    by_name(ds, SCH_NAME)['schematic']['occurrences'][0]['wires'] = [wire(end('box', 'TBR1B', '1', nearby=near), end('box', 'CN1', 'A'))]
    assert sym(ds, 'u1') == WEAK


def test_gnd_counterpart_without_unit_is_excluded_but_other_unit_failures_are_ng():
    wires = [wire(end('gnd', 'GND'), end('box', 'CN1', 'A'))]
    ds = with_schematic(build(), wires=wires, row={'端末1_機器符号': 'GND BAR', '端末1_ユニット': 'CHAMBER'})
    s, basis = cell(ds, 'u1')
    assert s == NA and 'GND 線の相手側は対象外' in basis
    wires = [wire(end('box', 'TBR1B', '1', regions=['OTHER']), end('box', 'CN1', 'A'))]
    assert sym(with_schematic(build(), wires=wires), 'u1') == NG


def test_unit_without_any_label_is_unread():
    wires = [wire(end('box', None, '1'), end('box', None, 'A'))]
    assert sym(with_schematic(build(), wires=wires), 'u1') == UNREAD


def test_multi_valued_unit_needs_every_value_in_the_schematic():
    wires = [wire(end('box', 'TBR1B', '1', regions=['RACK']), end('box', 'CN1', 'A'))]
    ds = with_schematic(build(), wires=wires, row={'端末1_ユニット': 'RACK | CHAMBER'})
    assert sym(ds, 'u1') == NG                    # CHAMBER の根拠が無い


# ---------------------------------------------------------------- 展開接続図: モジュール

def test_module_in_the_decided_text_or_title_is_ok_and_other_module_is_ng():
    wires = [wire(end('box', 'TBR1B', '1', regions=['ALS1 R1 (GEXUS)']), end('box', 'CN1', 'A'))]
    row = {'端末1_ユニット': 'ALS1', '端末1_モジュール': 'R1'}
    s, basis = cell(with_schematic(build(), wires=wires, row=row), 'm1')
    assert s == OK and 'ユニットの文字' in basis
    wires = [wire(end('box', 'TBR1B', '1', regions=['ALS1']), end('box', 'CN1', 'A'))]
    s, basis = cell(with_schematic(build(), wires=wires, row=row, title='POWER LINE(SYSTEM I/F BOX R1)'), 'm1')
    assert s == OK and '図面タイトル' in basis
    wires = [wire(end('box', 'TBR1B', '1', regions=['ALS1 F2']), end('box', 'CN1', 'A'))]
    s, basis = cell(with_schematic(build(), wires=wires, row=row), 'm1')
    assert s == NG and 'F2' in basis
    wires = [wire(end('box', 'TBR1B', '1', regions=['ALS1']), end('box', 'CN1', 'A'))]
    assert sym(with_schematic(build(), wires=wires, row=row), 'm1') == UNREAD   # どこにも記載が無い


def test_module_wildcard_or_empty_is_na():
    ds = with_schematic(build(), row={'端末1_モジュール': '**'})
    assert sym(ds, 'm1') == NA


# ---------------------------------------------------------------- 展開接続図: 芯数・種類

def test_core_count_exact_weak_excluding_drain_and_ng():
    row = {'芯数': '2'}
    ds = with_schematic(build(), row=row)
    s, basis = cell(ds, 'core')
    assert s == OK and '展開接続図 ○ 2本' in basis
    drain = standard_wires() + [wire(end('box', 'CN9', 'SG'), end('box', 'CN9', 'SG'), labels=['SG'])]
    s, basis = cell(with_schematic(build(), wires=drain, row=row), 'core')
    assert s == WEAK and 'SG/SH を除いて一致' in basis
    assert sym(with_schematic(build(), row={'芯数': '5'}), 'core') == NG
    assert sym(with_schematic(build(), row={'芯数': ''}), 'core') == NA


def test_core_count_of_fab_resolves_star_numbers_before_excluding_sg():
    """製作図の線番は `*n`。線番設定表の値が SG の線を除いてから数える。"""
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'][0]['芯数'] = '2'
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(n=(1, 2, 3), values={1: 'S121C', 2: 'T121C', 3: 'SG'})]
    conns = [{'端の数': 2, '端1_線番': f'*{n}', '端2_PIN No.': str(n)} for n in (1, 2, 3)]
    by_name(ds, FAB_NAME)['fab_conns'] = conns
    by_name(ds, FAB_NAME)['fab_wire_numbers'] = {1, 2, 3}
    s, basis = cell(ds, 'core')
    assert s == WEAK and basis.count('SG/SH を除いて一致') == 2        # 線番設定表と製作図の両方


def test_single_line_multicore_is_not_compared_for_core_count_wire_numbers_pins_and_colors():
    one = [wire(end('box', 'TBR1B', None), end('box', 'CN1', None))]
    ds = with_schematic(build(), wires=one, how='single', row={'芯数': '4'})
    for key in ('core', 'num', 'pin', 'color'):
        s, basis = cell(ds, key)
        assert '1本の線で描いた' in basis or s == NA, key
    assert sym(ds, 'c1') == OK                    # 機器符号だけは照合する


def test_cable_type_remark_is_compared_with_the_list():
    assert sym(with_schematic(build(), remark='ACCESSORY CABLE'), 'kind') == OK
    s, basis = cell(with_schematic(build(), remark='OTHER'), 'kind')
    assert s == NG and 'OTHER' in basis


# ---------------------------------------------------------------- 芯線: 線番・PIN・色（製作図 ↔ 展開接続図）

def with_fab_conns(ds, conns, numbers=None):
    by_name(ds, FAB_NAME)['fab_conns'] = conns
    if numbers is not None:
        by_name(ds, FAB_NAME)['fab_wire_numbers'] = numbers
    return ds


def test_pins_assign_each_fab_connection_to_a_405_wire_and_unmatched_is_ng():
    ds = with_schematic(build())
    with_fab_conns(ds, [{'端の数': 2, '端2_PIN No.': 'A'}, {'端の数': 2, '端2_PIN No.': 'B'}])
    s, basis = cell(ds, 'pin')
    assert s == OK and '2/2' in basis
    with_fab_conns(ds, [{'端の数': 2, '端2_PIN No.': 'A'}, {'端の数': 2, '端2_PIN No.': 'Z'}])
    s, basis = cell(ds, 'pin')
    assert s == NG and 'Z' in basis


def test_unmatched_sg_connection_is_weak_and_unreadable_pins_are_unread():
    ds = with_schematic(build())
    with_fab_conns(ds, [{'端の数': 2, '端2_PIN No.': 'A'}, {'端の数': 2, '端1_線番': 'SG', '端2_PIN No.': 'Z'}])
    assert sym(ds, 'pin') == WEAK
    nopins = [wire(end('box', 'TBR1B', None), end('box', 'CN1', None))]
    ds = with_schematic(build(), wires=nopins)
    with_fab_conns(ds, [{'端の数': 2, '端2_PIN No.': 'A'}])
    assert sym(ds, 'pin') == UNREAD


def test_wire_numbers_resolve_star_numbers_and_drain_numbers_are_not_compared():
    ds = with_schematic(build(), keep_wire_table=True,
                        wires=[wire(end('box', 'TBR1B', '1'), end('box', 'CN1', 'A'), labels=['S121C']),
                               wire(end('box', 'TBR1B', '2'), end('box', 'CN1', 'B'), labels=['T121C'])])
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(values={1: 'S121C', 2: 'T121C'})]
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': '*1'}, {'端の数': 2, '端1_線番': '*2'}])
    s, basis = cell(ds, 'num')
    assert s == OK and '展開接続図 ○ 一致（2/2 本）' in basis
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(values={1: 'S121C', 2: 'T999C'})]
    s, basis = cell(ds, 'num')
    assert s == NG and 'T999C' in basis
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': 'S121C'}, {'端の数': 2, '端1_線番': 'SG'}], numbers={1, 2})
    s, basis = cell(ds, 'num')
    # SG/SH（シールド）は 405 に線番のラベルが無いので比べない（以前は △。実図面 EE6892-611-01B の M65232 の `SH`）。残りが一致なら ○
    assert s == OK and '1/1 本' in basis


def test_fab_table_left_and_right_values_are_paired_and_matched_with_both_end_pins_whatever_the_heading():
    """守るもの: 製作図の表の見出し「線番」と「PIN No.」は同義。左の値と右の値を組にして、展開接続図の芯線の両端のピンに当てる（見出しの種類に関係なく。
    `*n` は n 番目の端子 n。展開接続図の左右が逆でもよい）。線番の `*1`・ピンの `A` の組は、ピン 1 と A の芯線に当たる。組の一方（`GND` と 4）が合わなければ ×
    （実図面 EE6892-611-01B の M65232: 製作図 `GND`-`GND` と 展開接続図 4-4）。"""
    wires = [wire(end('box', 'TBR1B', '1'), end('box', 'CN1', 'A')), wire(end('box', 'TBR1B', '2'), end('box', 'CN1', 'B'))]
    ds = with_schematic(build(), wires=wires)
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': '*1', '端2_PIN No.': 'A'}, {'端の数': 2, '端1_線番': '*2', '端2_PIN No.': 'B'}])
    s, basis = cell(ds, 'pin')
    assert s == OK and '2/2' in basis
    with_fab_conns(ds, [{'端の数': 2, '端1_PIN No.': 'A', '端2_線番': '*1'}, {'端の数': 2, '端1_PIN No.': 'B', '端2_PIN No.': '2'}])
    assert sym(ds, 'pin') == OK                                # 見出しが逆でも、同じ見出しの組でも、左右が逆でも
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': '*1', '端2_線番': '*1'}])
    assert sym(ds, 'pin') == NG                                # 線番どうしの組 1-1 は、ピン 1-A の芯線に当たらない
    wires = [wire(end('box', 'TBR1B', '1'), end('box', 'CN1', '1')), wire(end('box', 'TBR1B', '4'), end('box', 'CN1', '4'))]
    ds = with_schematic(build(), wires=wires)
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': '*1', '端2_線番': '*1'}, {'端の数': 2, '端1_線番': 'GND', '端2_線番': 'GND'}])
    s, basis = cell(ds, 'pin')
    assert s == NG and 'GND' in basis                          # `*1`-`*1` は 1-1 ○、`GND`-`GND` は 4-4 ×
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': 'SH', '端2_線番': 'SH'}])
    assert sym(ds, 'pin') == NA                                # SG/SH だけの組は比べない


def test_fab_connection_goes_to_the_row_whose_occurrence_matches_it_better_even_when_neither_matches_fully():
    """守るもの: 1枚の製作図の2つの区間（2行）で、`GND`-`GND`（表A）は出現 A（4-4・ラベル GND）、`GND`-`D`（表B）は出現 B（4-D）に当たりが大きい（ピンの一致＞ラベルの一致）。
    どちらの出現にも完全には合わない（GND と 4）ので、以前の「完全に合うか」の判定では両方の行に残り、別の区間の接続でも × になった（実図面 EE6892-611-01B の M65232）。
    各行には当たりの大きい接続だけが残り、その接続は ×（GND と 4 は一致しない）。"""
    ds = two_section_cable()
    occ_a, occ_b = by_name(ds, SCH_NAME)['schematic']['occurrences']
    occ_a['wires'].append(wire(end('circle', 'TBPS2R1', '4', regions=['MPD RACK1']), end('circle', 'TBR1C', '4', regions=['RACK']), labels=['GND']))
    occ_b['wires'].append(wire(end('circle', 'TBR1C', '4', regions=['RACK']), end('box', 'INPUT', 'D', regions=['RF PS2'])))
    by_name(ds, FAB_NAME)['fab_conns'] += [{'端の数': 2, '端1_線番': 'GND', '端2_線番': 'GND'}, {'端の数': 2, '端1_線番': 'GND', '端2_PIN No.': 'D'}]
    res = check_consistency(ds)
    rows = lambda n: [w for w in res['wires'] if w['一覧表の行'] == n and w['製作図の線番'] == 'GND']   # noqa: E731
    assert [(w['製作図の端2 PIN'], w['判定（PIN）']) for w in rows(1)] == [('', NG)]      # 1行目: GND-GND だけ（4-4 と GND は不一致 ×）
    assert [(w['製作図の端2 PIN'], w['判定（PIN）']) for w in rows(2)] == [('D', NG)]     # 2行目: GND-D だけ


def test_remark_text_below_the_label_is_marked_with_the_cable_kind_judgement_of_405():
    """守るもの: ラベルのすぐ下の別の文字を備考にしたとき（`ACCESSORY CABLE`）、その文字を「ケーブル｜種類」の展開接続図の比較の記号で強調する
    （以前は強調されなかった。実図面 EE6892-611-01B の M65232 の出現 B）。"""
    ds = with_schematic(build(), row={'ケーブル種類': 'ACCESSORY CABLE'})
    occ = by_name(ds, SCH_NAME)['schematic']['occurrences'][0]
    occ.update({'remark': 'ACCESSORY CABLE', '_remark_ref': ('ACCESSORY CABLE', (9.0, 9.0))})
    res = check_consistency(ds)
    marks = [m for m in res['rows'][0]['marks'] if m['drawing'] == SCH_NAME and m['anchor'] == (9.0, 9.0)]
    assert [(m['text'], m['symbol'], m['item']) for m in marks] == [('ACCESSORY CABLE', OK, 'ケーブル｜種類')]
    occ['remark'] = 'OTHER CABLE'                                  # 合わなければ ×
    assert [m['symbol'] for m in check_consistency(ds)['rows'][0]['marks'] if m['anchor'] == (9.0, 9.0)] == [NG]


def test_fab_wire_number_column_value_also_matches_a_405_pin():
    """守るもの: 「線番」の列の値（`1`・`-`）が展開接続図の芯線のピンに当たれば、線番も一致（見出しの「線番」と「PIN No.」は同義）。どちらにも無ければ ×。"""
    wires = [wire(end('box', 'TBR1B', '1'), end('box', 'CN1', 'A'), labels=['S121C'])]
    ds = with_schematic(build(), wires=wires)
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': 'A'}])
    assert sym(ds, 'num') == OK
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': 'Q999'}])
    assert sym(ds, 'num') == NG


def test_wire_numbers_are_not_compared_when_405_has_only_color_labels():
    """405 の芯線のラベルが色だけ（`(BK)`）なら、線番の比較は根拠なし（M65206）。"""
    ds = with_schematic(build(), keep_wire_table=True)
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': '*1'}, {'端の数': 2, '端1_線番': '*2'}])
    s, basis = cell(ds, 'num')
    assert s == OK and '展開接続図' not in basis


def test_colors_must_all_be_in_the_405_labels():
    ds = with_schematic(build())
    with_fab_conns(ds, [{'端の数': 2, 'ラベル': '(BK)'}, {'端の数': 2, 'ラベル': '(WH)'}])
    s, basis = cell(ds, 'color')
    assert s == NG and '(WH)' in basis
    with_fab_conns(ds, [{'端の数': 2, 'ラベル': '(BK)'}])
    assert sym(ds, 'color') == OK
    with_fab_conns(ds, [{'端の数': 2}])
    assert sym(ds, 'color') == NA


def test_wire_check_sheet_rows_one_per_fab_connection_plus_405_only_wires():
    ds = with_schematic(build(), wires=standard_wires() + [wire(end('box', 'CN9', 'Z'), end('box', 'CN9', 'Y'))])
    with_fab_conns(ds, [{'端の数': 2, '端1_線番': '*1', '端2_PIN No.': 'A', 'ラベル': '(BK)'},
                        {'端の数': 2, '端1_線番': '*2', '端2_PIN No.': 'B', 'ラベル': '(WH)'}])
    res = check_consistency(ds)
    rows = res['wires']
    assert [r['線番接続ID'] for r in rows] == ['*1', '*2', '']
    assert [r['判定（PIN）'] for r in rows] == [OK, OK, NA]
    assert [r['判定（色）'] for r in rows] == [OK, NG, NA]
    assert rows[0]['製作図の端2 PIN'] == 'A' and rows[0]['展開接続図の端+ PIN'] == 'A' and rows[0]['製作図'] == FAB_NAME


# ---------------------------------------------------------------- 展開接続図の出現の選び方

def test_best_occurrence_by_code_match_is_used_when_the_cable_appears_several_times():
    bad = occurrence([wire(end('box', 'ZZ1', '1'), end('box', 'ZZ2', 'A'))])
    good = occurrence(standard_wires())
    ds = with_schematic(build(), occ=bad, row={'端末2_機器符号': 'CN1'})
    by_name(ds, SCH_NAME)['schematic']['occurrences'].append(good)
    assert sym(ds, 'c1') == OK and sym(ds, 'c2') == OK


def test_two_list_rows_of_one_cable_use_different_occurrences_when_the_match_is_equal():
    """守るもの: 一覧表に同じケーブル番号が2行（端末1・端末2が別）あり、展開接続図にも2つの出現があるとき、判定が同点なら
    2行目はまだ使っていない出現に割り当てる（以前は先頭の出現が両方の行に選ばれ、2つ目の出現の文字が強調されなかった。実図面 EE6892-611-01B の M65232）。"""
    a = occurrence([wire(end('box', 'AA1', '1'), end('box', 'AA2', 'A'))])
    b = occurrence([wire(end('box', 'BB1', '1'), end('box', 'BB2', 'A'))])
    ds = with_schematic(build(), occ=a, row={'端末1_機器符号': 'ZZ1', '端末2_機器符号': 'ZZ2'})
    by_name(ds, SCH_NAME)['schematic']['occurrences'].append(b)
    second = copy.deepcopy(by_name(ds, LIST_NAME)['list_rows'][0])
    second['行'] = 2
    by_name(ds, LIST_NAME)['list_rows'].append(second)
    for d in ds:
        for occ in (d.get('schematic') or {}).get('occurrences', []):
            occ['_label_ref'] = (0.0, 0.0) if occ is a else (1.0, 1.0)
    res = check_consistency(ds)
    used = [{(m['anchor']) for m in r['marks'] if m['drawing'] == SCH_NAME and m['text'] == CABLE} for r in res['rows']]
    assert used == [{(0.0, 0.0)}, {(1.0, 1.0)}]


def two_section_cable():
    """1枚の製作図・1枚の線番設定表に、同じケーブル番号の2つの区間（一覧表の2行）が描かれた図面セット（実図面 EE6892-611-01B の M65232 の形）。
    1行目: 端末1 MPD RACK1 TBPS2R1 ― 端末2 RACK TBR1C（線番設定表の行・展開接続図の出現 A〈ピン 1/2〉）。
    2行目: 端末1 RACK TBR1C ― 端末2 RF PS2 INPUT（出現 B〈ピン 1/2 ― A/B〉。線番設定表には行が無い）。
    製作図の接続: `*1`-A・`*2`-B（2行目のピン付き）と、`*1`・`*2` どうし（ピンなし）。"""
    row1 = list_row(**{'端末1_ユニット': 'MPD RACK1', '端末1_機器符号': 'TBPS2R1', '端末2_ユニット': 'RACK', '端末2_機器符号': 'TBR1C',
                       '接続図パターン': 'EE****-610-**'})
    row2 = list_row(**{'行': 2, '端末1_ユニット': 'RACK', '端末1_機器符号': 'TBR1C', '端末2_ユニット': 'RF PS2', '端末2_機器符号': 'INPUT',
                       '接続図パターン': 'EE****-610-**'})
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'] = [row1, row2]
    by_name(ds, FAB_NAME)['fab_notes'] = ['-634-07']
    by_name(ds, WIRE_NAME)['wire_rows'] = [wire_row(lines1=('MPD RACK', 'TBPS2R1'), lines2=('RACK', 'TBR1C'), values={1: 'U1', 2: 'V1'})]
    by_name(ds, FAB_NAME)['fab_conns'] = [
        {'端の数': 2, '端1_線番': '*1', '端2_PIN No.': 'A'}, {'端の数': 2, '端1_線番': '*2', '端2_PIN No.': 'B'},
        {'端の数': 2, '端1_線番': '*1', '端2_線番': '*1'}, {'端の数': 2, '端1_線番': '*2', '端2_線番': '*2'}]
    occ_a = occurrence([wire(end('circle', 'TBPS2R1', str(n), regions=['MPD RACK1']), end('circle', 'TBR1C', str(n), regions=['RACK']),
                             labels=[lab]) for n, lab in ((1, 'U1'), (2, 'V1'))])
    occ_b = occurrence([wire(end('circle', 'TBR1C', str(n), regions=['RACK']), end('box', 'INPUT', pin, regions=['RF PS2']))
                        for n, pin in ((1, 'A'), (2, 'B'))])
    ds.append(drawing(SCH_NAME, set='405', kinds=['展開接続図'], schematic={
        'title': '', 'error': '', 'no_text': False, 'region_error': '', 'occurrences': [occ_a, occ_b]}))
    return ds


def test_pinned_fab_connections_of_the_other_section_are_not_compared_with_this_rows_occurrence():
    """守るもの: 1枚の製作図に2つの区間が描かれるとき、2行目の区間のピン付きの接続（`*1`-A・`*2`-B）は、1行目の出現（ピン 1/2）と比べて × にしない。
    別の出現（B）で満たされる接続は、その行（2行目）だけで比べる（以前は1行目が PIN × になり、2行目の ○ を隠した。実図面 EE6892-611-01B の M65232）。"""
    ds = two_section_cable()
    assert sym(ds, 'pin', row_no=1) != NG
    s, basis = cell(ds, 'pin', row_no=2)
    assert s == OK and '2/2' in basis
    by_name(ds, SCH_NAME)['schematic']['occurrences'].pop()          # もう一方の出現が無ければ、1行目は従来どおり × になる
    assert sym(ds, 'pin', row_no=1) == NG


def test_pinless_fab_connections_are_assigned_to_the_405_wire_with_the_same_wire_number():
    """守るもの: ピンの無い製作図の接続（`*1`-`*1`）は、`*n` を線番設定表の行で置き換えた線番（U1）と同じラベルの展開接続図の芯線に割り当てる
    （図形・ラベルの強調のため）。左右の値の組 1-1 が芯線の両端のピン 1-1 にも当たるので、PIN も ○。以前は割り当てが無く、出現 A の芯線とラベルが強調されなかった
    （実図面 EE6892-611-01B の M65232）。"""
    res = check_consistency(two_section_cable())
    rows = {w['線番接続ID']: w for w in res['wires'] if w['一覧表の行'] == 1 and w['線番接続ID']}
    assert rows['*1']['展開接続図のラベル'] == 'U1' and rows['*2']['展開接続図のラベル'] == 'V1'
    assert rows['*1']['判定（PIN）'] == OK and rows['*1']['判定（線番）'] == OK


def test_sh_connection_matches_when_405_draws_the_shield_and_its_shapes_are_marked():
    """守るもの: 製作図の SH の接続は、展開接続図にシールド（`_shield`）が描かれていれば線番が ○（描かれていなければ比べない＝－）。シールドの破線と楕円は
    viewer の図形（○）になる（実図面 EE6892-611-01B の M65232 出現 A の SH。接地記号は含めない）。"""
    ds = two_section_cable()
    by_name(ds, FAB_NAME)['fab_conns'].append({'端の数': 2, '端1_線番': 'SH', '端2_線番': 'SH'})
    sh = lambda res: next(w for w in res['wires'] if w['一覧表の行'] == 1 and w['製作図の線番'] == 'SH')   # noqa: E731
    assert sh(check_consistency(ds))['判定（線番）'] == NA
    occ_a = by_name(ds, SCH_NAME)['schematic']['occurrences'][0]
    occ_a['_shield'] = {'lines': [[(0.0, 0.0), (9.0, 0.0)]], 'ellipses': [(8.0, -1.0, 10.0, 1.0)]}
    res = check_consistency(ds)
    assert sh(res)['判定（線番）'] == OK
    shapes = [x for x in res['rows'][0]['shapes'] if x['item'] == 'シールド' and x['drawing'] == SCH_NAME]
    assert any(x['kind'] == 'ellipse' and x['symbol'] == OK for x in shapes)


def test_star_numbers_of_the_row_without_wire_table_row_use_the_sibling_rows_wire_table_row():
    """守るもの: 製作図の `*n` は図面単位で共通なので、線番設定表に行が無い2行目も、`*n` の置き換えと `*n` の集合の比較には別の行（1行目）が選んだ行を使う
    （端末・芯数は使わない）。2行目の `*1`-A は線番 U1 になり、線番設定表との比較は ○（以前は根拠なしで、製作図の表B の `*1` が強調されなかった）。"""
    res = check_consistency(two_section_cable())
    row2 = next(r for r in res['rows'] if r['一覧表の行'] == 2)
    detail = res['details'][row2['links'][COL['num']]]
    assert row2['cells'][COL['num']] == OK and '線番設定表 ○ 一致' in detail['判断根拠']
    pinned = [w for w in res['wires'] if w['一覧表の行'] == 2 and w['線番接続ID'] == '*1' and w['製作図の端2 PIN'] == 'A']
    assert pinned and pinned[0]['線番設定表の線番'] == 'U1'


def test_wire_table_wire_number_marks_follow_the_wire_number_judgement_only():
    """守るもの: viewer の線番設定表の線番の値（U1 など）の記号は、線番の判定だけで決める。芯数の判定（ここでは一覧表 9 本 ≠ 線番設定表 2 本で ×）を混ぜない
    （以前は混ぜて、一致した U122 などが × で表示された。実図面 EE6892-611-01B の M65232。D7）。"""
    ds = two_section_cable()
    by_name(ds, LIST_NAME)['list_rows'][0]['芯数'] = '9'
    by_name(ds, WIRE_NAME)['wire_rows'][0]['_refs'] = {'線番1': [('U1', (1.0, 1.0))], '線番2': [('V1', (2.0, 2.0))]}
    res = check_consistency(ds)
    assert sym(ds, 'core', row_no=1) == NG
    marks = [m for m in res['rows'][0]['marks'] if m['drawing'] == WIRE_NAME and m['item'] == '芯線｜線番']
    assert marks and {m['symbol'] for m in marks} == {OK}


def test_row_without_its_own_wire_table_row_is_not_compared_with_the_wire_table_of_the_sibling_row():
    """守るもの: 同じケーブル番号の別の行が線番設定表の行を使っているとき、この行（線番設定表に行が無い区間）は、線番設定表では比べない（根拠なし）。
    ○ の根拠は展開接続図だけで、「対応する行が無い」の × で 405 の ○ を隠さない。線番設定表の線番（U1・V1）の比較は1行目で ○。
    別の行が線番設定表の行を使っていなければ、従来どおり「対応する行が無い」× （根拠: `test_no_wire_row_fits_the_list_terminals_is_ng_with_the_rows_listed`）。"""
    ds = two_section_cable()
    for key in ('u1', 'c1', 'c2'):
        s, basis = cell(ds, key, row_no=2)
        assert s == OK and '線番設定表' not in basis, (key, basis)
    assert sym(ds, 'num', row_no=1) != NG
    s, basis = cell(ds, 'num', row_no=2)
    assert s != NG and '対応する行が無い' not in basis
    by_name(ds, LIST_NAME)['list_rows'].pop(0)                         # 別の行が無ければ、2行目は線番設定表の行が無い ×
    assert sym(ds, 'u1', row_no=2) == NG


def test_occurrences_are_assigned_to_rows_as_a_whole_not_first_come_first_served():
    """守るもの: 同じケーブル番号の2行と2つの出現があるとき、割り当ては全体で決める。1行目はどの出現にも同じ程度に合い、2行目は出現 A にしか合わないとき、
    先着順（1行目が先頭の A を取る）だと両方の行が A になり B が使われない。全体で決めれば 1行目＝B・2行目＝A（以前の貪欲法の不具合。D4）。"""
    near = [('CN1', 0.0, 'CN1')]
    occ_a = occurrence([wire(end('circle', 'TBR1B', '1', regions=['RACK']), end('box', 'CN1', 'A', nearby=near))])
    occ_b = occurrence([wire(end('circle', 'TBR1B', '1', regions=['RACK']), end('box', 'ZZ9', 'A'))])
    occ_a['_label_ref'], occ_b['_label_ref'] = (0.0, 0.0), (1.0, 1.0)
    ds = with_schematic(build(), occ=occ_a, row={'端末2_機器符号': ''})
    by_name(ds, SCH_NAME)['schematic']['occurrences'].append(occ_b)
    second = copy.deepcopy(by_name(ds, LIST_NAME)['list_rows'][0])
    second.update({'行': 2, '端末2_機器符号': 'CN1'})
    by_name(ds, LIST_NAME)['list_rows'].append(second)
    res = check_consistency(ds)
    used = [{m['anchor'] for m in r['marks'] if m['drawing'] == SCH_NAME and m['text'] == CABLE} for r in res['rows']]
    assert used == [{(1.0, 1.0)}, {(0.0, 0.0)}]


# ---------------------------------------------------------------- 展開接続図の読み取り（2段目）と補助

def test_pattern_resolutions_include_the_schematic_patterns():
    rows = C.pattern_resolutions(with_schematic(build()))
    (hit,) = [r for r in rows if (r['図面種別'], r['参照元図面種別']) == ('展開接続図', '一覧表')]
    assert hit['パターン'] == 'EE****-610-**' and hit['当たった図面'] == SCH_NAME and hit['件数'] == 1


REF = '図中の参照'
REF_TARGET = 'EE6892-405-08A'


def test_pattern_resolutions_include_references_written_in_the_schematic():
    """展開接続図の図中の `(-405-08)` は、図番パターン照合に行として足す（頭の英字2・数字4と末尾の改訂記号は任意。備考に参照元の文字）。"""
    ds = with_schematic(build())
    by_name(ds, SCH_NAME)['schematic']['refs'] = [
        {'pattern': '-405-08', 'text': 'to POWER LINE (-405-08)'}, {'pattern': '-405-08', 'text': 'XRLS1 (-405-08)'},
        {'pattern': '-300-01', 'text': 'DO21-24 (-300-01 1/2)'}]
    ds.append(drawing(REF_TARGET, set='405'))
    rows = {r['パターン']: r for r in C.pattern_resolutions(ds) if r['参照元図面種別'] == REF}
    assert list(rows) == ['-405-08', '-300-01']                   # 同じパターンは1行（備考に参照元の文字をまとめる）
    hit = rows['-405-08']
    assert (hit['参照元の図番'], hit['当たった図面'], hit['件数']) == (SCH_NAME, REF_TARGET, 1)
    assert hit['備考'] == 'to POWER LINE (-405-08) ／ XRLS1 (-405-08)'
    miss = rows['-300-01']
    assert (miss['当たった図面'], miss['件数'], miss['備考']) == ('', 0, 'DO21-24 (-300-01 1/2)')


def test_references_are_read_only_from_schematics_and_do_not_change_the_check():
    """図中の参照は展開接続図（2段目で読んだ図面）だけから拾い、照合の記号・不整合数・「使っていない図面」は変えない。"""
    plain = with_schematic(build())
    ds = copy.deepcopy(plain)
    by_name(ds, SCH_NAME)['schematic']['refs'] = [{'pattern': '-405-08', 'text': 'to X (-405-08)'}]
    ds.append(drawing(REF_TARGET, set='405'))
    plain.append(drawing(REF_TARGET, set='405'))
    assert not [r for r in C.pattern_resolutions(plain) if r['参照元図面種別'] == REF]    # refs が無い・読んでいない図面からは出ない
    a, b = check_consistency(plain), check_consistency(ds)
    assert (a['rows'], a['details'], a['wires']) == (b['rows'], b['details'], b['wires'])
    assert a['unused'] == b['unused'] == [REF_TARGET]            # 参照で指されただけの図面は「使っていない」のまま


def test_schematic_targets_are_only_drawings_pointed_at_by_the_list(monkeypatch):
    ds = with_schematic(build())
    ds.append(drawing('EE7777-700-01A', set='405'))
    assert C.schematic_targets(ds) == [SCH_NAME]


def test_patterns_are_resolved_inside_their_own_set_of_drawings():
    """守るもの: 接続図パターン `EE****-610-**` は、同じ `-610-` を持つ 393 のセットの図面（線番設定表など）には当たらず、展開接続図（405 のセット）だけに当たる。
    逆に、製作図パターンは 405 のセットの図面に当たらない（セットを分けて上げるので、名前が似ていても取り違えない）。"""
    ds = with_schematic(build())
    ds.append(drawing('EE3298-610-02A', kinds=['線番設定表']))
    ds.append(drawing('EE3298-610-03A', kinds=['ケーブル製作図']))
    ds.append(drawing('EE3298-610-04A', kinds=['ケーブル一覧表']))
    ds.append(drawing('EE9999-534-07A', set='405'))          # 一覧表の製作図パターン EE****-534-07 に名前が合う 405 の図面
    assert C.schematic_targets(ds) == [SCH_NAME]
    rows = C.pattern_resolutions(ds)
    (sch,) = [r for r in rows if (r['図面種別'], r['参照元図面種別']) == ('展開接続図', '一覧表')]
    assert (sch['件数'], sch['当たった図面']) == (1, SCH_NAME)
    (fab,) = [r for r in rows if (r['図面種別'], r['参照元図面種別'], r['パターン']) == ('製作図', '一覧表', 'EE****-534-07')]
    assert (fab['件数'], fab['当たった図面']) == (1, FAB_NAME)


def fake_extract(path, name, doc=None):
    return {'drawing_number': os.path.splitext(name)[0], 'title': '', 'subtitle': '', 'cables': []}


def test_analyze_uploads_reads_each_set_its_own_way_and_the_schematics_in_a_last_stage(monkeypatch):
    """守るもの: 393 は抽出＋393 の読み取り、405 は抽出だけ、その後に一覧表が指す 405 の図面だけを展開接続図として読む。
    進捗の段階は 393 → 405（読む図面は「展開接続図の読み取り」、ほかは「抽出」。1つのファイルで続けて行う）。"""
    calls = []

    def fake_analyze(path, name, doc=None):
        calls.append(('393', name))
        d = drawing(name, kinds=[])
        if name == LIST_NAME:
            d['kinds'] = ['ケーブル一覧表']
            d['list_rows'] = [list_row(接続図パターン='EE****-610-**')]
        return d

    def fake_schematic(path, name, title=None, doc=None):
        calls.append(('405', name, title))
        return {'title': 't', 'error': '', 'no_text': False, 'region_error': '', 'occurrences': []}

    monkeypatch.setattr(C, 'analyze_drawing', fake_analyze)
    monkeypatch.setattr(C, 'analyze_schematic', fake_schematic)
    monkeypatch.setattr(C, 'extract_cables', fake_extract)
    progress = []
    items_405 = [(f'{n}.dxf', b'x') for n in (SCH_NAME, 'EE7777-700-01A')]
    items_393 = [(f'{n}.dxf', b'x') for n in (LIST_NAME, 'EE8888-800-01A')]
    drawings, extracts, errors = C.analyze_uploads(items_405, items_393, progress=lambda *a: progress.append(a))
    assert errors == [] and calls == [('393', LIST_NAME), ('393', 'EE8888-800-01A'), ('405', SCH_NAME, fake_extract(None, SCH_NAME)['title'])]
    assert {d['drawing_number']: d['set'] for d in drawings} == {
        LIST_NAME: '393', 'EE8888-800-01A': '393', SCH_NAME: '405', 'EE7777-700-01A': '405'}
    sch = by_name(drawings, SCH_NAME)
    assert sch['schematic']['title'] == 't' and C.KIND_SCHEMATIC in sch['kinds']
    assert by_name(drawings, 'EE7777-700-01A')['schematic'] is None       # 一覧表が指さない 405 は読まない
    assert [r['drawing_number'] for r in extracts['405']] == [SCH_NAME, 'EE7777-700-01A']     # 図番順
    assert [r['drawing_number'] for r in extracts['393']] == [LIST_NAME, 'EE8888-800-01A']
    # 405 は1つのファイルの中で抽出と展開接続図の読み取りを続けて行う（DXF の解析を1回にする）。段階は、読む図面は「展開接続図の読み取り」、そうでなければ「抽出」
    assert progress == [(0, 2, f'{LIST_NAME}.dxf', C.PHASE_DRAWINGS), (1, 2, 'EE8888-800-01A.dxf', C.PHASE_DRAWINGS),
                        (0, 2, f'{SCH_NAME}.dxf', C.PHASE_SCHEMATIC), (1, 2, 'EE7777-700-01A.dxf', C.PHASE_EXTRACT)]


def test_analyze_uploads_keeps_going_when_a_file_or_a_schematic_fails(monkeypatch):
    """守るもの: 壊れた1件（抽出か読み取りのどちらかが失敗）は図面にも抽出結果にも入らずエラーになり、残りは続く。
    展開接続図の読み取りだけが失敗した 405 は、抽出結果と図面には残り、エラーになる。"""
    def analyze(path, name, doc=None):
        if name == 'EE0001-393-01A':
            raise ValueError('broken 393')
        return drawing(name, list_rows=[list_row(接続図パターン='EE****-610-**')] if name == LIST_NAME else [])

    def extract(path, name, doc=None):
        if name.startswith('EE0002-405-01A'):
            raise ValueError('broken 405')
        return fake_extract(path, name)

    def boom(path, name, title=None, doc=None):
        raise RuntimeError('broken schematic')

    monkeypatch.setattr(C, 'analyze_drawing', analyze)
    monkeypatch.setattr(C, 'extract_cables', extract)
    monkeypatch.setattr(C, 'analyze_schematic', boom)
    drawings, extracts, errors = C.analyze_uploads(
        [(f'{SCH_NAME}.dxf', b'x'), ('EE0002-405-01A.dxf', b'x')],
        [(f'{LIST_NAME}.dxf', b'x'), ('EE0001-393-01A.dxf', b'x')])
    assert sorted(d['drawing_number'] for d in drawings) == sorted([LIST_NAME, SCH_NAME])
    assert [r['drawing_number'] for r in extracts['393']] == [LIST_NAME] and [r['drawing_number'] for r in extracts['405']] == [SCH_NAME]
    assert sorted(e[0] for e in errors) == ['EE0001-393-01A.dxf', 'EE0002-405-01A.dxf', f'{SCH_NAME}.dxf']
    assert by_name(drawings, SCH_NAME)['schematic'] is None


def test_schematic_without_text_marks_the_drawing_as_having_no_text(monkeypatch):
    """守るもの: 展開接続図として読んだ結果が「文字が無い」なら、図面の no_text が立つ（画面の警告・Summary の「文字の無い図面」に出る）。"""
    monkeypatch.setattr(C, 'analyze_drawing', lambda path, name, doc=None: drawing(
        name, list_rows=[list_row(接続図パターン='EE****-610-**')]))
    monkeypatch.setattr(C, 'extract_cables', fake_extract)
    monkeypatch.setattr(C, 'analyze_schematic', lambda path, name, title=None, doc=None: {
        'title': '', 'error': '', 'no_text': True, 'region_error': '', 'occurrences': [], 'refs': []})
    drawings, _extracts, errors = C.analyze_uploads([(f'{SCH_NAME}.dxf', b'x')], [(f'{LIST_NAME}.dxf', b'x')])
    assert errors == [] and by_name(drawings, SCH_NAME)['no_text'] and not by_name(drawings, LIST_NAME)['no_text']


def test_run_check_adds_extracts_and_the_cable_comparison_to_the_result(monkeypatch):
    """守るもの: run_check は照合の結果に、抽出結果（セットごと）とケーブル番号の比較（全体）を足す。405 だけでも動く。"""
    monkeypatch.setattr(C, 'analyze_drawing', lambda path, name, doc=None: drawing(name))
    monkeypatch.setattr(C, 'extract_cables', lambda path, name, doc=None: {
        'drawing_number': os.path.splitext(name)[0], 'title': 'T', 'subtitle': '',
        'cables': [{'cable': 'M39001', 'remark': ''}]})
    result, errors = C.run_check([(f'{SCH_NAME}.dxf', b'x')], [('EE0001-393-01A.dxf', b'x')])
    assert errors == [] and set(result['extracts']) == {'405', '393'}
    assert [(r['cable'], r['drawings_405'], r['drawings_393']) for r in result['compare']['rows']] == [
        ('M39001', [SCH_NAME], ['EE0001-393-01A'])]
    result, errors = C.run_check([(f'{SCH_NAME}.dxf', b'x')], [])
    assert errors == [] and result['rows'] == [] and result['extracts']['393'] == []
    assert [r['drawings_393'] for r in result['compare']['rows']] == [[]]
    assert result['unused'] == [SCH_NAME]                                  # 一覧表が無いので、どの接続図パターンにも指されない


def test_symbol_severity_order_is_x_then_unread_then_weak_then_ok_then_na():
    ordered = sorted(C.SYMBOLS, key=lambda s: C.SEVERITY[s], reverse=True)
    assert ordered == [NG, UNREAD, WEAK, OK, NA]


def _marked(drawings):
    res, row = run(drawings)
    return res, row, {(m['drawing'], m['text']): m['symbol'] for m in row['marks']}


def test_marks_follow_each_comparison_symbol_and_wrong_values_turn_the_text_cross():
    """守るもの: 行の `marks`（viewer の強調）は、その文字を比べた比較の記号を持つ。一覧表の文字は項目のセルの記号、線番設定表の選んだ行の
    接続先は端末のユニット・機器符号の比較の記号。機器符号を食い違わせると、一覧表の文字と線番設定表の接続先が × になる（照合の記号と一致）。
    `focus` は一覧表・製作図・線番設定表の図番と選んだ行を持つ。"""
    ds = build()
    by_name(ds, LIST_NAME)['list_rows'][0]['_refs'] = {'ケーブル番号': [(CABLE, (1.0, 1.0))], '端末1_機器符号': [('TBR1B', (2.0, 2.0))],
                                                       'ケーブル備考': [('備考', (3.0, 3.0))]}
    by_name(ds, WIRE_NAME)['wire_rows'][0]['_refs'] = {'ケーブル番号': [(CABLE, (4.0, 4.0))], '接続先1': [('RACK\nTBR1B', (5.0, 5.0))]}
    _res, row, marks = _marked(ds)
    assert marks[(LIST_NAME, CABLE)] == OK and marks[(LIST_NAME, 'TBR1B')] == OK
    assert (LIST_NAME, '備考') not in marks                              # 項目に対応しない列は marks に無い（cable_focus が「関連」にする）
    assert marks[(WIRE_NAME, CABLE)] == OK and marks[(WIRE_NAME, 'RACK\nTBR1B')] == OK
    assert row['focus']['list'] == LIST_NAME and row['focus']['fabs'] == [FAB_NAME] and row['focus']['wires'] == [WIRE_NAME]
    assert row['focus']['sel'] == [(WIRE_NAME, 1, 1)] and row['focus']['schematic'] == ''

    ds = build()
    by_name(ds, LIST_NAME)['list_rows'][0]['端末1_機器符号'] = 'TBF9Z'
    by_name(ds, LIST_NAME)['list_rows'][0]['_refs'] = {'端末1_機器符号': [('TBF9Z', (2.0, 2.0))]}
    by_name(ds, WIRE_NAME)['wire_rows'][0]['_refs'] = {'接続先1': [('RACK\nTBR1B', (5.0, 5.0))]}
    _res, row, marks = _marked(ds)
    assert row['cells'][item_column('端末1', '機器符号')] == NG
    assert marks[(LIST_NAME, 'TBF9Z')] == NG and marks[(WIRE_NAME, 'RACK\nTBR1B')] == NG


def _conn(pin1, pin2, label, segs):
    return {'端の数': 2, '端1_線番': '', '端1_PIN No.': pin1, '端2_PIN No.': pin2, 'ラベル': label, '_segs': segs}


def _shaped_schematic(tmp_conn_labels=('(BK)', '(WH)'), third_wire_without_end=False):
    """図形つきの 405 の出現（ケーブル記号1つ・引出線・芯線2本〈ピン 1/A・2/B〉）と、製作図の接続2本（A/1・B/2）を持つ図面セット。"""
    ds = build()
    by_name(ds, FAB_NAME)['fab_notes'] = []
    by_name(ds, FAB_NAME)['fab_conns'] = [_conn('A', '1', tmp_conn_labels[0], [[(0.0, 0.0), (10.0, 0.0)]]),
                                          _conn('B', '2', tmp_conn_labels[1], [[(0.0, 5.0), (10.0, 5.0)]])]
    near = [('CN1', 0.0, 'CN1')]
    wires = []
    for n, pin, y in (('1', 'A', 0.0), ('2', 'B', 5.0)):
        w = wire(end('circle', 'TBR1B', n, regions=['RACK']), end('box', 'CN1', pin, nearby=near), labels=['(BK)'])
        w['_path'] = [[(100.0, y), (120.0, y)]]
        w['ends']['-']['_shape'] = {'circle': ((100.0, y), 1.0)}
        w['ends']['+']['_shape'] = {'rect': (120.0, y - 1, 130.0, y + 1)}
        wires.append(w)
    if third_wire_without_end:
        w = wire(end('circle', 'TBR1B', '3', regions=['RACK']), end(kind=None), labels=['(BK)'])
        w['_path'] = [[(100.0, 9.0), (120.0, 9.0)]]
        wires.append(w)
    occ = occurrence(wires)
    occ['_leader'] = [[(110.0, 20.0), (110.0, 8.0)]]
    occ['_symbols'] = [{'shape': 'ellipse', 'box': (108.0, 0.0, 112.0, 6.0)}]
    return with_schematic(ds, occ=occ)


def test_shapes_for_the_viewer_follow_the_wire_judgements_and_skip_wires_without_both_ends():
    """守るもの: 行の `shapes`（viewer の図形）。製作図の接続の線と展開接続図の芯線は、割り当てた接続の PIN・線番・色の判定のうち最も重い記号
    （色 (WH)≠(BK) の芯線は ×、合う芯線は ○）。引出線・記号は「図面の対応｜展開接続図」、端末の枠・円は機器符号の記号。
    端末の無い芯線は塗らない（取りこぼしより誤りを避ける）。"""
    _res, row = run(_shaped_schematic())
    kinds = [(s['drawing'], s['kind'], s['symbol']) for s in row['shapes']]
    fab = [(k, s) for d, k, s in kinds if d == FAB_NAME]
    assert sorted(fab) == sorted([('lines', OK), ('lines', NG)])                     # (BK) の接続 ○、(WH) は 405 のラベルに無く ×
    wires = [s for s in row['shapes'] if s['drawing'] == SCH_NAME and s['item'] == '芯線']
    assert sorted(s['symbol'] for s in wires) == sorted([NG, OK])
    leader = [s for s in row['shapes'] if s['item'] == '図面の対応｜展開接続図']
    assert {s['kind'] for s in leader} == {'lines', 'ellipse'} and {s['symbol'] for s in leader} == {OK}
    ends = [s for s in row['shapes'] if s['kind'] in ('rect', 'circle')]
    assert sorted((s['kind'], s['item']) for s in ends) == [('circle', '端末1｜機器符号')] * 2 + [('rect', '端末2｜機器符号')] * 2

    _res, row = run(_shaped_schematic(third_wire_without_end=True))
    paths = [s for s in row['shapes'] if s['drawing'] == SCH_NAME and s['item'] == '芯線']
    assert len(paths) == 2                                                   # 端末の無い3本目は出ない
    _res, row = run(with_schematic(build()))
    assert [s for s in row['shapes'] if s['drawing'] == SCH_NAME] == []      # `_path` などが無い（旧形式）ときは図形なし


def test_drawing_relations_lists_related_drawings_per_schematic_in_order():
    """守るもの: `drawing_relations()`（図面の関係シートの元）は、展開接続図（405。図番順）ごとに、接続図パターンが当たった一覧表・その行の製作図・
    注記の線番設定表を返す。関連が無い展開接続図は空のリスト。"""
    from model.consistency import drawing_relations
    ds = with_schematic(build(), keep_wire_table=True)
    ds.append(drawing('EE0000-405-99A', set='405', kinds=[]))
    rel = drawing_relations(ds)
    assert [r[0] for r in rel] == sorted(r[0] for r in rel) and len(rel) == 2
    assert dict((r[0], r[1:]) for r in rel) == {SCH_NAME: ([LIST_NAME], [WIRE_NAME], [FAB_NAME]), 'EE0000-405-99A': ([], [], [])}
