"""DXF の読み取り（drawing_geometry・cable_list_table・wire_number_table・fab_drawing）の合成 DXF テスト。

ezdxf で作る最小の図面で、読み取り規則（実現性検討 docs/feasibility/*/README.md の「方式」）を確かめる。
実データでの値は tests/regression/spec/test_consistency_real_data.py が守る。
"""
import ezdxf
import pytest

from model import cable_list_table, drawing_geometry, fab_drawing, wire_number_table


def load(doc, tmp_path):
    path = tmp_path / 'syn.dxf'
    doc.saveas(str(path))
    return drawing_geometry.load(str(path))


def new_doc():
    doc = ezdxf.new('R2010')
    return doc, doc.modelspace()


def text(msp, s, x, y, h=3, **attribs):
    return msp.add_text(s, dxfattribs={'insert': (x, y), 'height': h, **attribs})


def mtext(msp, s, x, y, h=2.5):
    return msp.add_mtext(s, dxfattribs={'insert': (x, y), 'char_height': h})


def line(msp, a, b, **attribs):
    return msp.add_line(a, b, dxfattribs=attribs)


# ---------------------------------------------------------------- drawing_geometry

def test_norm_halfwidth_and_joins_spaced_single_characters_only():
    n = drawing_geometry.norm
    assert n('ＴＢＲ１Ｂ') == 'TBR1B'
    assert n('T B') == 'TB' and n('R 1 4 - 5') == 'R14-5'
    assert n('DC PS3') == 'DC PS3' and n('D-sub 25P') == 'D-sub 25P'   # 語がすべて1文字のときだけ詰める


def test_hidden_entities_are_not_collected(tmp_path):
    """守るもの: invisible 属性・off／frozen の画層・非表示の INSERT の中身は文字にも線にも入れない。"""
    doc, msp = new_doc()
    doc.layers.add('OFF').off()
    doc.layers.add('FROZEN').freeze()
    text(msp, 'SHOWN', 0, 0)
    text(msp, 'INVIS', 0, 10, invisible=1)
    text(msp, 'OFFLAYER', 0, 20, layer='OFF')
    text(msp, 'FROZENLAYER', 0, 30, layer='FROZEN')
    line(msp, (0, 0), (10, 0), layer='OFF')
    block = doc.blocks.new('B')
    block.add_text('INBLOCK', dxfattribs={'insert': (0, 0), 'height': 3})
    msp.add_blockref('B', (0, 40))
    msp.add_blockref('B', (0, 50), dxfattribs={'invisible': 1})
    geo = load(doc, tmp_path)
    assert sorted(t.s for t in geo.txts) == ['INBLOCK', 'SHOWN']
    assert geo.lines == []


def test_frame_and_revision_inserts_are_skipped(tmp_path):
    """守るもの: 図面枠（大きな JZB ブロック）と改訂記号（SX_DeltaSymbol）の中の文字は読まない。図面枠は一辺 150 以上（実データの枠は 175 以上）。
    小さな JZB ブロック（記載）の文字は読む（`test_small_jzb_block_text_is_read_but_a_large_one_is_a_drawing_frame`）。"""
    doc, msp = new_doc()
    for name in ('JZB_A3', 'SX_DeltaSymbolB', 'OTHER'):
        block = doc.blocks.new(name)
        block.add_text(name, dxfattribs={'insert': (0, 0), 'height': 3})
        if name == 'JZB_A3':
            block.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)   # A3 の図面枠
        msp.add_blockref(name, (0, 0))
    assert [t.s for t in load(doc, tmp_path).txts] == ['OTHER']


def _jzb(doc, msp, name, size, word, scale=1.0):
    """文字 word と、一辺 size の線2本（外接矩形が size×size）を持つ JZB ブロックを置く。"""
    block = doc.blocks.new(name)
    block.add_text(word, dxfattribs={'insert': (1, 1), 'height': 3})
    block.add_line((0, 0), (size, 0))
    block.add_line((0, 0), (0, size))
    block.add_lwpolyline([(0, 0), (size, 0), (size, size)])
    msp.add_blockref(name, (0, 0), dxfattribs={'xscale': scale, 'yscale': scale})


@pytest.mark.parametrize('size, scale, read', [(100, 1.0, True), (110, 1.0, True), (149, 1.0, True), (175, 1.0, False), (200, 1.0, False),
                                               (100, 2.0, False)])
def test_small_jzb_block_text_is_read_but_a_large_one_is_a_drawing_frame(tmp_path, size, scale, read):
    """守るもの（ユーザー報告 2026-11-22。EE3298-553-12D の M65123: 製作図の `ACCESSORY CABLE`・`付属コネクタ` は小さな JZB ブロックの中にある）:
    ブロック `JZB_*` の外接矩形の長辺（×尺度）が 150 未満なら記載のブロックなので中の文字を読み、150 以上なら図面枠・情報枠なので読まない。
    ケーブル図 106枚の JZB は 110 以下（記載）と 175 以上（枠）に分かれる。以前は JZB をすべて図面枠として読み飛ばし、記載の文字が製作図に入らなかった。"""
    doc, msp = new_doc()
    _jzb(doc, msp, 'JZB_0003', size, 'ACCESSORY CABLE', scale)
    assert [t.s for t in load(doc, tmp_path).txts] == (['ACCESSORY CABLE'] if read else [])


def test_lines_inside_a_small_jzb_block_are_not_collected(tmp_path):
    """守るもの: 小さな JZB ブロックは文字だけを読み、中の線・ポリライン・弧は電線の連結に混ぜない。混ぜると製作図の接続が 457→456、芯線が
    791→789 に減り、○ だった PIN が × になる判定の悪化が5件出た（EE6888-639-01A ほか。実データで測った）。"""
    doc, msp = new_doc()
    _jzb(doc, msp, 'JZB_0003', 100, '付属コネクタ')
    geo = load(doc, tmp_path)
    assert [t.s for t in geo.txts] == ['付属コネクタ'] and geo.lines == [] and geo.polys == []


def test_text_in_a_block_nested_in_a_small_jzb_block_is_read_and_its_lines_are_not(tmp_path):
    doc, msp = new_doc()
    inner = doc.blocks.new('INNER')
    inner.add_text('NESTED', dxfattribs={'insert': (0, 0), 'height': 3})
    inner.add_line((0, 0), (5, 0))
    outer = doc.blocks.new('JZB_0004')
    outer.add_blockref('INNER', (2, 2))
    outer.add_line((0, 0), (80, 0))
    msp.add_blockref('JZB_0004', (0, 0))
    geo = load(doc, tmp_path)
    assert [t.s for t in geo.txts] == ['NESTED'] and geo.lines == []


def test_polyline_edges_and_arc_ends_are_collected_separately(tmp_path):
    doc, msp = new_doc()
    line(msp, (0, 0), (10, 0))
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5)])
    msp.add_arc((0, 0), 4, 0, 90)
    geo = load(doc, tmp_path)
    assert len(geo.lines) == 1 and len(geo.polys) == 2 and len(geo.arc_ends) == 2


def test_unreadable_file_reports_error(tmp_path):
    bad = tmp_path / 'bad.dxf'
    bad.write_text('not a dxf')
    assert drawing_geometry.load(str(bad)).error.startswith('読み込めません')


# ---------------------------------------------------------------- 線番設定表

def wire_table_doc():
    """W No.／線番(*1)／線番(*2)／接続先／接続先 の表（行2つ）と、注記。"""
    doc, msp = new_doc()
    xs = [10, 40, 60, 80, 110, 140]
    for x in xs:
        line(msp, (x, 90), (x, 120))
    for y in (120, 110, 100, 90):
        line(msp, (xs[0], y), (xs[-1], y))
    for s, x in (('W No.', 12), ('線番(*1)', 42), ('線番(*2)', 62), ('接続先', 82), ('接続先', 112)):
        text(msp, s, x, 113, 2.5)
    text(msp, 'M39001', 12, 103, 2.5)
    text(msp, 'a', 45, 103, 2.5)
    text(msp, 'b', 65, 103, 2.5)
    mtext(msp, 'RACK\\PTBF1B', 82, 108)
    text(msp, 'ALS1', 112, 103, 2.5)
    text(msp, 'M39002', 12, 93, 2.5)
    text(msp, 'c', 45, 93, 2.5)
    text(msp, 'd', 65, 93, 2.5)
    text(msp, 'GAS BOX', 82, 93, 2.5)
    text(msp, 'OSSET', 112, 93, 2.5)
    mtext(msp, '注記)\\P1. ケーブル製作図は EE****-534-07 による', 10, 70)
    return doc, msp


def test_wire_table_columns_rows_and_note_pattern(tmp_path):
    doc, _ = wire_table_doc()
    res = wire_number_table.parse(load(doc, tmp_path))
    (table,) = res['tables']
    assert [c[2] for c in table['cols']] == ['ケーブル番号', '線番1', '線番2', '接続先1', '接続先2']
    rows = table['rows']
    assert [(r['ケーブル番号'], r['線番1'], r['線番2'], r['接続先1'], r['接続先2']) for r in rows] == [
        ('M39001', 'a', 'b', 'RACK TBF1B', 'ALS1'), ('M39002', 'c', 'd', 'GAS BOX', 'OSSET')]
    assert rows[0]['_lines']['接続先1'] == ['RACK', 'TBF1B']        # 接続先の改行はユニット名＋端子台の分割用に残る
    assert [p for p, _ in res['notes']] == ['EE****-534-07']


def test_wire_table_struck_text_is_removed_and_reported(tmp_path):
    """守るもの: 文字の高さの中ほどを横切る横線は削除線。その文字を除き、削除された値に残す。"""
    doc, msp = wire_table_doc()
    line(msp, (43, 94.2), (50, 94.2))      # 'c'（y 93〜95.5）の中ほど
    (table,) = wire_number_table.parse(load(doc, tmp_path))['tables']
    row = table['rows'][1]
    assert row['線番1'] == '' and row['_deleted'] == {'線番1': 'c'}
    assert row['線番2'] == 'd'


def test_wire_table_note_line_with_strike_is_ignored(tmp_path):
    doc, msp = wire_table_doc()
    text(msp, '(注記) ケーブル製作図 EE****-999-99', 10, 60, 2.5)
    line(msp, (9, 61.2), (60, 61.2))
    res = wire_number_table.parse(load(doc, tmp_path))
    assert [p for p, _ in res['notes']] == ['EE****-534-07']


def test_wire_table_hidden_old_note_is_not_read(tmp_path):
    doc, msp = wire_table_doc()
    mtext(msp, '注記)\\P1. ケーブル製作図は EE****-503-47 による', 10, 50).dxf.invisible = 1
    res = wire_number_table.parse(load(doc, tmp_path))
    assert [p for p, _ in res['notes']] == ['EE****-534-07']


def test_no_wire_table_gives_empty_tables(tmp_path):
    doc, msp = new_doc()
    text(msp, 'hello', 0, 0)
    assert wire_number_table.parse(load(doc, tmp_path))['tables'] == []


@pytest.mark.parametrize('lines, terminals, expect', [
    (['RACK', 'TBF1B'], [], ('RACK', 'TBF1B')),
    (['GAS BOX', 'F1'], [], ('GAS BOX F1', '')),                    # F1 はモジュール位置でユニット名側
    (['SYSTEM I/F', 'BOX'], ['SYSTEM I/F BOX'], ('SYSTEM I/F BOX', '')),
    (['BS03', 'LS03'], [], ('BS03 LS03', '')),                      # 全行が機器の名前ならユニット名側
    (['DC PS1 F1', '(MASTER)'], [], ('DC PS1 F1 (MASTER)', '')),    # 括弧で始まる行は直前の補足
    (['MPD RACK', 'TBPS1F1A'], [], ('MPD RACK', 'TBPS1F1A')),
    (['ELB087 or CB087'], [], ('ELB087 or CB087', '')),
])
def test_split_unit_terminal(lines, terminals, expect):
    assert wire_number_table.split_unit_terminal(lines, terminals) == expect


def test_unit_match_exact_prefix_and_none():
    m = wire_number_table.unit_match
    assert m('RACK', 'RACK') == '一致'
    assert m('MPD RACK1', 'MPD RACK') == '近似' and m('ALS1 F1', 'ALS1') == '近似'
    assert m('DC PS1 (MASTER)', 'DC PS1') == '近似'     # 括弧の補足は比べない
    assert m('OSSET', 'RACK') == '' and m('', 'RACK') == ''


# ---------------------------------------------------------------- ケーブル一覧表

def cable_list_doc():
    """W No／製作図番号／端末1 接続先・コネクタ名称／ケーブル仕様 ケーブル種類 の表（データ行4つ）。"""
    doc, msp = new_doc()
    xs = [0, 25, 60, 90, 120, 160]
    top_xs = [0, 25, 60, 120, 160]       # 上段（端末1 は 60〜120 にまたがる）まで届く縦線
    for x in xs:
        line(msp, (x, 90), (x, 150 if x in top_xs else 140))
    for y in (150, 140, 130, 120, 110, 100, 90):
        line(msp, (0, y), (160, y))
    text(msp, '端末1', 85, 143)
    text(msp, 'ケーブル仕様', 130, 143)
    for s, x in (('W No.', 2), ('ケーブル製作図図面番号', 27), ('接続先', 62), ('コネクタ名称', 92), ('ケーブル種類', 122)):
        text(msp, s, x, 132, 2.5)
    text(msp, 'M39001', 2, 122, 2.5)
    text(msp, 'EE****-534-07', 27, 122, 2.5)
    text(msp, 'RACK', 62, 124, 2.5)
    text(msp, 'PANEL', 62, 121, 2.5)             # 同じセルに2つ → 複数の値
    text(msp, 'TBR1B', 92, 122, 2.5)
    text(msp, 'SHIELD', 122, 122, 2.5)
    text(msp, 'M39002', 2, 112, 2.5)
    text(msp, 'EE****-534-08', 27, 112, 2.5)
    text(msp, 'OLD', 122, 113, 2.5)              # 削除線の文字
    text(msp, 'NEW', 140, 112, 2.5)              # 修正後の値（削除線の横）
    line(msp, (121, 114.3), (130, 114.3))
    return doc, msp


def test_cable_list_rows_columns_multi_values_and_strike(tmp_path):
    doc, _ = cable_list_doc()
    res = cable_list_table.parse(load(doc, tmp_path))
    rows = res['rows']
    assert [r['ケーブル番号'] for r in rows] == ['M39001', 'M39002']
    first, second = rows
    assert first['製作図パターン'] == 'EE****-534-07'
    assert first['端末1_ユニット'] == 'RACK | PANEL' and first['端末1_機器符号'] == 'TBR1B'
    assert first['ケーブル種類'] == 'SHIELD' and first['削除された値'] == ''
    assert second['ケーブル種類'] == 'NEW' and second['削除された値'] == 'ケーブル種類:OLD'


def test_cable_list_fold_notes_joins_parenthesised_value():
    assert cable_list_table.fold_notes(['DC PS1', '(MASTER)', 'RACK']) == ['DC PS1 (MASTER)', 'RACK']
    assert cable_list_table.fold_notes(['(A)', 'B']) == ['(A)', 'B']


def test_no_cable_list_table_returns_none(tmp_path):
    doc, msp = new_doc()
    text(msp, 'hello', 0, 0)
    assert cable_list_table.parse(load(doc, tmp_path)) is None


# ---------------------------------------------------------------- ケーブル製作図

def draw_table(msp, xs, headers, cells):
    """線番／PIN の表（行2つ）。xs は列の境界、headers は列ごとの見出し、cells は列ごとの2行分の値。"""
    for x in xs:
        line(msp, (x, 82), (x, 105))
    for y in (105, 98, 90, 82):
        line(msp, (xs[0], y), (xs[-1], y))
    for (x0, x1), head, vals in zip(zip(xs, xs[1:]), headers, cells):
        text(msp, head, x0 + 4, 100)
        for v, y in zip(vals, (93, 85)):
            if v:
                text(msp, v, x0 + 4, y)


def fab_doc():
    """左の表（線番｜PIN）と右の表（PIN｜線番）。2行（y=94, 86）を電線で結べるようにする。"""
    doc, msp = new_doc()
    draw_table(msp, [10, 30, 50], ['線番', 'PIN No.'], [('*1', '*2'), ('A', 'B')])
    draw_table(msp, [100, 120, 140], ['PIN No.', '線番'], [('1', '2'), ('', '')])
    return doc, msp


def conns(tmp_path, doc):
    res = fab_drawing.parse(load(doc, tmp_path))
    return res, sorted(sorted((ti, r['i']) for ti, r, _s, _c in c['ends']) for c in res['conns'])


def test_fab_tables_and_straight_connections_with_label(tmp_path):
    doc, msp = fab_doc()
    line(msp, (50, 94), (100, 94))
    line(msp, (50, 86), (100, 86))
    text(msp, '(BK)', 70, 95, 2.5)
    res, pairs = conns(tmp_path, doc)
    assert len(res['tables']) == 2 and [len(t['rows']) for t in res['tables']] == [2, 2]
    assert pairs == [[(0, 1), (1, 1)], [(0, 2), (1, 2)]]
    rows = fab_drawing.connection_rows(res)
    first = next(r for r in rows if r['端1_行'] == 1)
    assert (first['端1_線番'], first['端1_PIN No.'], first['端2_PIN No.'], first['ラベル']) == ('*1', 'A', '1', '(BK)')
    assert first['端1_位置'] == '左' and first['端2_位置'] == '右'


def test_fab_columns_with_different_row_counts_form_one_table(tmp_path):
    """守るもの（ユーザー報告 2026-11-23。EE3298-545-28A の左の表 CNCM: 線番の列だけ下に1行多い）: 隣り合う列の行の区切りが上から同じなら、
    行数が違っても1つの表にまとめ、電線がつながる行が 線番 と PIN の両方を持つ。以前は行数が同じ列だけをまとめたため、線番の列が別の表になり、
    接続の端に線番が無かった（viewer で左の表の線番が強調されなかった）。行が無い列の値は空。"""
    doc, msp = new_doc()
    for x, bottom in ((10, 74), (30, 74), (50, 82)):
        line(msp, (x, bottom), (x, 105))
    for y in (105, 98, 90, 82):
        line(msp, (10, y), (50, y))
    line(msp, (10, 74), (30, 74))                 # 線番の列だけ1行多い
    text(msp, '線番', 14, 100)
    text(msp, 'PIN No.', 34, 100)
    for v, x, y in (('*1', 14, 93), ('*2', 14, 85), ('SH', 14, 77), ('A', 34, 93), ('B', 34, 85)):
        text(msp, v, x, y)
    draw_table(msp, [100, 120, 140], ['PIN No.', '線番'], [('1', '2'), ('', '')])
    line(msp, (50, 94), (100, 94))
    res, _pairs = conns(tmp_path, doc)
    left = min(res['tables'], key=lambda t: t['x0'])
    assert [c['kind'] for c in left['cols']] == ['線番', 'PIN'] and len(left['rows']) == 3
    assert (left['rows'][2]['線番'], left['rows'][2]['PIN']) == ('SH', '')
    (row,) = fab_drawing.connection_rows(res)
    assert (row['端1_線番'], row['端1_PIN No.'], row['端2_PIN No.']) == ('*1', 'A', '1')
    assert row['_端1_refs']['線番'][0][0] == '*1'


def test_fab_wire_ending_up_to_0_5_short_of_the_table_edge_still_connects_that_row(tmp_path):
    """守るもの: 電線の端が表の左右の辺から 0.32 離れていても（0.5 以内）その行につながる（以前は許容 0.3 で、その行の接続が落ちた。
    実図面 EE3298-556-51A の GND-D）。線どうしの連結の許容（0.3）は変えない。"""
    doc, msp = fab_doc()
    line(msp, (50, 94), (99.68, 94))        # 右の表の辺（x=100）から 0.32 手前
    line(msp, (50, 86), (100, 86))
    res, pairs = conns(tmp_path, doc)
    assert pairs == [[(0, 1), (1, 1)], [(0, 2), (1, 2)]]


def test_fab_note_block_is_not_a_matching_text_but_stays_in_all_texts(tmp_path):
    """守るもの: 注記の文章ブロック（先頭行に「注記」）は、記載の照合と強調に使う `text_refs` に入れない（文中の `Module` が種類 `UL` に部分一致して
    注記ブロック全体が ○ になった。EE3298-574-72C）。図中の参照を探す全文字 `texts` には残す。"""
    doc, msp = fab_doc()
    text(msp, '(注記)\n1. Module No.はケーブル一覧表参照の事。', 60, 60, 2.5)
    text(msp, 'UL1007 AWG18', 60, 40, 2.5)
    res = fab_drawing.parse(load(doc, tmp_path))
    refs = [t for t, _ in res['text_refs']]
    assert 'UL1007 AWG18' in refs and not any('注記' in t for t in refs)
    assert any('注記' in t for t in res['texts'])
    assert fab_drawing.is_note_block('(注記)\n1. x') and not fab_drawing.is_note_block('1. x\n(注記)')


def test_fab_struck_label_goes_to_deleted_labels(tmp_path):
    doc, msp = fab_doc()
    line(msp, (50, 94), (100, 94))
    text(msp, '(BK)', 70, 95, 2.5)
    line(msp, (69, 96.3), (78, 96.3))
    res, _ = conns(tmp_path, doc)
    (c,) = res['conns']
    assert c['labels'] == [] and c['deleted'] == ['(BK)']


def test_fab_t_branch_connects_three_ends(tmp_path):
    """守るもの: 端点が他の線の上にある T 字の分岐はつながる（端が3つの接続になる）。"""
    doc, msp = fab_doc()
    line(msp, (50, 94), (100, 94))
    line(msp, (75, 94), (75, 86))      # 縦線の上端は上の横線の途中（T 字）
    line(msp, (75, 86), (100, 86))
    res, pairs = conns(tmp_path, doc)
    assert pairs == [[(0, 1), (1, 1), (1, 2)]]
    assert fab_drawing.connection_rows(res)[0]['端の数'] == 3


def test_fab_crossing_wires_are_not_connected(tmp_path):
    """守るもの: 交差しているだけの線（端点が触れない）はつながない。"""
    doc, msp = fab_doc()
    line(msp, (50, 94), (100, 86))
    line(msp, (50, 86), (100, 94))
    _, pairs = conns(tmp_path, doc)
    assert pairs == [[(0, 1), (1, 2)], [(0, 2), (1, 1)]]


def test_fab_vertical_line_on_arc_ends_is_not_a_wire(tmp_path):
    """守るもの: シールド・より線の長円記号（縦線＋円弧）の縦線は、2本の電線をつながない。"""
    doc, msp = fab_doc()
    line(msp, (50, 94), (100, 94))
    line(msp, (50, 86), (100, 86))
    line(msp, (75, 94), (75, 86))
    msp.add_arc((75, 90), 4, 90, 270)       # 端点が (75,94) と (75,86)
    _, pairs = conns(tmp_path, doc)
    assert pairs == [[(0, 1), (1, 1)], [(0, 2), (1, 2)]]


def test_fab_arrow_and_its_shaft_are_not_wires(tmp_path):
    """守るもの: 矢印（短い斜め2本が1点で会う）と、その先端に端がある線は電線から除く。"""
    doc, msp = fab_doc()
    line(msp, (50, 94), (100, 94))
    line(msp, (50, 86), (100, 86))
    line(msp, (75, 94), (75, 86))           # 引出線（先端が上の電線の上）
    line(msp, (73, 91), (75, 94))
    line(msp, (77, 91), (75, 94))
    _, pairs = conns(tmp_path, doc)
    assert pairs == [[(0, 1), (1, 1)], [(0, 2), (1, 2)]]


def test_fab_note_pattern_texts_and_no_table(tmp_path):
    doc, msp = fab_doc()
    text(msp, '1. 線番設定表は -634-07 による', 10, 60)
    res = fab_drawing.parse(load(doc, tmp_path))
    assert [p for p, _ in res['notes']] == ['-634-07']
    assert any('-634-07' in t for t in res['texts'])
    doc, msp = new_doc()
    text(msp, 'hello', 0, 0)
    assert fab_drawing.parse(load(doc, tmp_path))['tables'] == []


def test_fab_without_tables_skips_wire_tracing_and_has_no_connections(tmp_path, monkeypatch):
    """線番/PIN の表が無い図面（展開接続図など）は、重い電線の連結（trace）を呼ばず、接続は空のまま（速度のための省略。結果は変わらない）。"""
    doc, msp = new_doc()
    text(msp, 'hello', 0, 0)
    line(msp, (0, 0), (50, 0))
    monkeypatch.setattr(fab_drawing, 'trace', lambda *a, **k: (_ for _ in ()).throw(AssertionError('trace called')))
    res = fab_drawing.parse(load(doc, tmp_path))
    assert res['tables'] == [] and res['conns'] == []


def test_reading_does_not_share_struck_flags_between_readers(tmp_path):
    """守るもの: 3つの読み取りは同じ Geometry を共用するが、文字は読み取りごとにコピーする（削除済みの印が混ざらない）。"""
    doc, msp = wire_table_doc()
    line(msp, (43, 94.2), (50, 94.2))
    geo = load(doc, tmp_path)
    wire_number_table.parse(geo)
    assert not any(t.struck for t in geo.txts)


# ---------------------------------------------------------------- 文字の無い図面

def test_drawing_without_text_skips_all_readers(tmp_path, monkeypatch):
    """守るもの: 文字が1つも無い図面は読み取りを走らせない（輪郭線に分解された文字は数万本の線分になり、
    製作図の電線の連結が止まったように遅くなる。2026-11-18、EE3298-637-02E の旧変換版）。"""
    from model import consistency

    def boom(_geo):
        raise AssertionError('読み取りは呼ばれないはず')
    for module in (cable_list_table, wire_number_table, fab_drawing):
        monkeypatch.setattr(module, 'parse', boom)
    doc, msp = new_doc()
    msp.add_lwpolyline([(0, 0), (1, 1), (2, 0)])
    line(msp, (0, 0), (10, 0))
    path = tmp_path / 'outline.dxf'
    doc.saveas(str(path))
    d = consistency.analyze_drawing(str(path), 'EE0001-001-01A')
    assert d['no_text'] and d['kinds'] == []
    assert consistency.check_consistency([d])['rows'] == []


def test_fab_connection_keeps_its_wire_segments_for_the_viewer(tmp_path):
    """守るもの: 接続（`_segs`。Excel には出ない）は、つないだ電線の線分そのもの（viewer の図形の重ね書き）。折れ曲がった電線は全線分を持つ。"""
    doc, msp = fab_doc()
    line(msp, (50, 94), (100, 94))
    line(msp, (50, 86), (75, 86))
    line(msp, (75, 86), (75, 80))
    line(msp, (75, 80), (100, 80))
    line(msp, (100, 80), (100, 86))
    res, _pairs = conns(tmp_path, doc)
    rows = {r['端1_行']: r for r in fab_drawing.connection_rows(res)}
    assert rows[1]['_segs'] == [[(50.0, 94.0), (100.0, 94.0)]]
    assert len(rows[2]['_segs']) == 4 and all(len(seg) == 2 for seg in rows[2]['_segs'])
