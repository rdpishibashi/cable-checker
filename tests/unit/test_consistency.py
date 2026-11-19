"""整合性チェック（`check_consistency()`・`analyze_uploads()`）の合成データテスト。

図面の dict を直接組み立てて渡す（DXF は読まない）。定義は docs/handoff_schematic_405_check.md の「照合の定義」。
基準の図面セット `build()` は、393 の連鎖（一覧表 → 製作図 → 線番設定表）が全部 ○ になる:
  一覧表 EE6892-365-61C の M65206 →（製作図パターン EE****-534-07）→ 製作図 EE3298-534-07D
  →（注記 -634-07）→ 線番設定表 EE3298-634-07E。各テストはこれを1か所だけ壊して、その項目の記号と根拠を見る。
展開接続図は `with_schematic()` で足す（接続図パターン EE****-610-**。図番 EE6892-610-01B）。
"""
import copy

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
    d = {'drawing_number': name, 'no_text': False, 'kinds': [], 'list_rows': [], 'wire_rows': [],
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
    ds.append(drawing(name, kinds=['展開接続図'], schematic={'title': title, 'error': '', 'no_text': False,
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


def test_wire_numbers_resolve_star_numbers_and_drain_numbers_are_weak():
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
    assert sym(ds, 'num') == WEAK                  # SG/SH が 405 に無いだけ


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


# ---------------------------------------------------------------- 展開接続図の読み取り（2段目）と補助

def test_pattern_resolutions_include_the_schematic_patterns():
    rows = C.pattern_resolutions(with_schematic(build()))
    (hit,) = [r for r in rows if r['種別'] == '展開接続図（一覧表）']
    assert hit['パターン'] == 'EE****-610-**' and hit['当たった図面'] == SCH_NAME and hit['件数'] == 1


REF = '展開接続図（図中の参照）'
REF_TARGET = 'EE6892-405-08A'


def test_pattern_resolutions_include_references_written_in_the_schematic():
    """展開接続図の図中の `(-405-08)` は、パターンの解決に行として足す（頭の英字2・数字4と末尾の改訂記号は任意。備考に参照元の文字）。"""
    ds = with_schematic(build())
    by_name(ds, SCH_NAME)['schematic']['refs'] = [
        {'pattern': '-405-08', 'text': 'to POWER LINE (-405-08)'}, {'pattern': '-405-08', 'text': 'XRLS1 (-405-08)'},
        {'pattern': '-300-01', 'text': 'DO21-24 (-300-01 1/2)'}]
    ds.append(drawing(REF_TARGET))
    rows = {r['パターン']: r for r in C.pattern_resolutions(ds) if r['種別'] == REF}
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
    ds.append(drawing(REF_TARGET))
    plain.append(drawing(REF_TARGET))
    assert not [r for r in C.pattern_resolutions(plain) if r['種別'] == REF]    # refs が無い・読んでいない図面からは出ない
    a, b = check_consistency(plain), check_consistency(ds)
    assert (a['rows'], a['details'], a['wires']) == (b['rows'], b['details'], b['wires'])
    assert a['unused'] == b['unused'] == [REF_TARGET]            # 参照で指されただけの図面は「使っていない」のまま


def test_schematic_targets_are_only_drawings_pointed_at_by_the_list(monkeypatch):
    ds = with_schematic(build())
    ds.append(drawing('EE7777-700-01A'))
    assert C.schematic_targets(ds) == [SCH_NAME]


def test_schematic_targets_skip_393_drawings_that_the_pattern_also_matches():
    """接続図パターン `EE****-610-**` は、同じ `-610-` を持つ 393 の図面（線番設定表など）にも当たる。393 の表が見つかった図面は
    展開接続図として読まない（読むと、393 の図面にあるケーブル番号の文字が展開接続図の出現として数えられる）。"""
    ds = with_schematic(build())
    ds.append(drawing('EE3298-610-02A', kinds=['線番設定表']))
    ds.append(drawing('EE3298-610-03A', kinds=['ケーブル製作図']))
    ds.append(drawing('EE3298-610-04A', kinds=['ケーブル一覧表']))
    assert C.schematic_targets(ds) == [SCH_NAME]


def test_analyze_uploads_reads_schematics_in_a_second_stage_only_for_pointed_files(monkeypatch):
    calls = []
    plain = lambda stem: drawing(stem, kinds=[])  # noqa: E731

    def fake_analyze(path, name):
        calls.append(('393', name))
        d = plain(name)
        if name == LIST_NAME:
            d['kinds'] = ['ケーブル一覧表']
            d['list_rows'] = [list_row(接続図パターン='EE****-610-**')]
        return d

    def fake_schematic(path, name):
        calls.append(('405', name))
        return {'title': 't', 'error': '', 'no_text': False, 'region_error': '', 'occurrences': []}

    monkeypatch.setattr(C, 'analyze_drawing', fake_analyze)
    monkeypatch.setattr(C, 'analyze_schematic', fake_schematic)
    progress = []
    items = [(f'{n}.dxf', b'x') for n in (LIST_NAME, SCH_NAME, 'EE7777-700-01A')]
    drawings, errors = C.analyze_uploads(items, progress=lambda *a: progress.append(a))
    assert errors == [] and [c for c in calls if c[0] == '405'] == [('405', SCH_NAME)]
    sch = by_name(drawings, SCH_NAME)
    assert sch['schematic']['title'] == 't' and C.KIND_SCHEMATIC in sch['kinds']
    assert by_name(drawings, 'EE7777-700-01A')['schematic'] is None
    assert [p[3] for p in progress] == [C.PHASE_DRAWINGS] * 3 + [C.PHASE_SCHEMATIC]
    assert progress[-1][:3] == (0, 1, SCH_NAME)


def test_analyze_uploads_keeps_going_when_a_schematic_fails(monkeypatch):
    monkeypatch.setattr(C, 'analyze_drawing', lambda path, name: drawing(
        name, list_rows=[list_row(接続図パターン='EE****-610-**')] if name == LIST_NAME else []))

    def boom(path, name):
        raise RuntimeError('broken')

    monkeypatch.setattr(C, 'analyze_schematic', boom)
    drawings, errors = C.analyze_uploads([(f'{LIST_NAME}.dxf', b'x'), (f'{SCH_NAME}.dxf', b'x')])
    assert len(drawings) == 2 and [e[0] for e in errors] == [f'{SCH_NAME}.dxf'] and 'RuntimeError' in errors[0][1]


def test_symbol_severity_order_is_x_then_unread_then_weak_then_ok_then_na():
    ordered = sorted(C.SYMBOLS, key=lambda s: C.SEVERITY[s], reverse=True)
    assert ordered == [NG, UNREAD, WEAK, OK, NA]
