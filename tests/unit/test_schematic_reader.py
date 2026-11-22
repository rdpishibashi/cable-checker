"""展開接続図（405）の読み取り（model/schematic_reader.py）の単体テスト。合成 DXF を ezdxf でその場で組み立てる。

共通の図面（`build()`）: 左右に連結コネクタ（枠＋ピン番号＋コネクタ名）、その間を2本の横の芯線（線番 P021・N244）が通り、
中ほどのケーブル記号（楕円または長円）を、上から引出線（`W No.M65206`・矢印）が指す。
領域抽出（DXF-extract-labels 由来）は図面枠の無い合成図面では空になるので、領域の根拠のテストだけ差し替える。
"""
import ezdxf
import pytest

from model import schematic_reader as sr

CABLE = 'M65206'


def _text(msp, s, x, y, ap=5, rot=0, h=2.5):
    msp.add_mtext(s, dxfattribs={'insert': (x, y), 'char_height': h, 'attachment_point': ap, 'rotation': rot})


def _box(msp, x0, y0, x1, y1, color=2):
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        msp.add_line(a, b, dxfattribs={'color': color})


def _line(msp, a, b, color=4, linetype=None):
    attribs = {'color': color}
    if linetype:
        attribs['linetype'] = linetype
    msp.add_line(a, b, dxfattribs=attribs)


def _arrow(msp, tip, top):
    """先端 tip から上（top の高さ）へ軸を引き、先端に矢じり（短い2本）を描く。軸の上端から右へ下線を引く。"""
    x, y = tip
    _line(msp, tip, (x, top), 7)
    _line(msp, tip, (x - 0.5, y + 2.9), 7)
    _line(msp, tip, (x + 0.5, y + 2.9), 7)
    _line(msp, (x, top), (x + 14, top), 7)


def build(tmp_path, symbol='ellipse', extras=None, name='EE0000-405-01A'):
    """共通の図面を書き出し、パスを返す。`extras(msp)` で図面を足せる。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 0, 0, 9, 11)
    _box(msp, 50, 0, 59, 11)
    for y, pin, label in ((8, '1', 'P021'), (3, '2', 'N244')):
        _line(msp, (9, y), (50, y))
        _text(msp, pin, 4.5, y)
        _text(msp, pin, 54.5, y)
        _text(msp, label, 20, y + 0.5, ap=7)
    _text(msp, 'CN1', 4.5, 13.5)
    _text(msp, 'CN2', 54.5, 13.5)
    if symbol == 'ellipse':
        msp.add_ellipse((30, 5.5, 0), major_axis=(0, 4.5, 0), ratio=0.3, dxfattribs={'color': 2})
        tip = (30, 10)
    else:   # 長円: 向かい合う2つの円弧＋縦線
        msp.add_arc((30, 10), 1.5, 0, 180, dxfattribs={'color': 2})
        msp.add_arc((30, 1), 1.5, 180, 360, dxfattribs={'color': 2})
        _line(msp, (28.5, 1), (28.5, 10), 2)
        _line(msp, (31.5, 1), (31.5, 10), 2)
        tip = (30, 11.5)
    _arrow(msp, tip, 19.5)
    _text(msp, f'W No.{CABLE}', 22, 19.5, ap=7)
    if extras:
        extras(msp)
    path = tmp_path / f'{name}.dxf'
    doc.saveas(path)
    return str(path)


@pytest.fixture(autouse=True)
def no_region_analysis(monkeypatch):
    """既定では領域抽出を空にする（合成図面に図面枠が無い）。領域の根拠のテストは自分で差し替える。"""
    monkeypatch.setattr(sr, 'analyze_dxf_regions', lambda path, cfg, doc=None: {'regions': [], 'minor_regions': []})


def read(path):
    return sr.analyze_schematic(path, 'EE0000-405-01A')


def occurrence(result):
    (occ,) = result['occurrences']
    return occ


def by_label(occ):
    """線番ラベル（P021・N244）→ 芯線。"""
    return {w['labels'][0]: w for w in occ['wires'] if w['labels']}


# ---------------------------------------------------------------- 楕円・長円と芯線の両端

@pytest.mark.parametrize('symbol, how', [('ellipse', 'ellipse'), ('capsule', 'capsule')])
def test_cable_symbol_wires_and_connector_ends(tmp_path, symbol, how):
    """矢印の先の記号（楕円・長円）を横切る2本の芯線が、記号の左右の端のコネクタ名・ピン番号まで読める。"""
    result = read(build(tmp_path, symbol))
    occ = occurrence(result)
    assert (occ['cable'], occ['how']) == (CABLE, how)
    wires = by_label(occ)
    assert set(wires) == {'P021', 'N244'}
    for label, pin in (('P021', '1'), ('N244', '2')):
        ends = wires[label]['ends']
        assert (ends['-']['kind'], ends['-']['name'], ends['-']['pin']) == ('box', 'CN1', pin)
        assert (ends['+']['kind'], ends['+']['name'], ends['+']['pin']) == ('box', 'CN2', pin)


def test_remark_after_cable_number_is_kept(tmp_path):
    """備考（`ACCESSORY CABLE`）は `W No.` と同じ MTEXT の2行目にある。"""
    path = build(tmp_path)
    doc = ezdxf.readfile(path)
    for e in doc.modelspace().query('MTEXT'):
        if e.plain_text().startswith('W No.'):
            e.text = f'W No.{CABLE}\\PACCESSORY CABLE'
    doc.saveas(path)
    occ = occurrence(read(path))
    assert occ['remark'] == 'ACCESSORY CABLE'
    assert occ['_remark_ref'] is None                                   # 同じ文字の中の備考は、ラベルの文字そのものが強調される


def test_dashed_shield_on_symbol_edge_is_not_a_wire_but_dashed_wire_is(tmp_path):
    """記号の縁に接する破線（シールド）は芯線にしない。記号の中を通る破線は芯線（610-01B の ACCESSORY CABLE）。"""
    def extras(msp):
        _line(msp, (9, 10), (50, 10), 3, 'DASHED')       # シールド: 楕円の上端（y=10）に接する
        _line(msp, (9, 5.5), (50, 5.5), 4, 'DASHED')     # 破線の芯線

    wires = occurrence(read(build(tmp_path, extras=extras)))['wires']
    assert len(wires) == 3


def test_overlapping_identical_segments_count_once(tmp_path):
    """同じ位置に重なった同一の線分（2重に描かれた線）は1本にする。残すと芯線が倍になり、たどる途中で分岐に見える。"""
    def extras(msp):
        for y in (8, 3):
            _line(msp, (9, y), (50, y))

    occ = occurrence(read(build(tmp_path, extras=extras)))
    assert len(occ['wires']) == 2
    assert occ['wires'][0]['ends']['-']['name'] == 'CN1'


# ---------------------------------------------------------------- 端末の種類

def test_circle_terminal_gets_terminal_number_and_device_name_and_gnd_end(tmp_path):
    """芯線の端が小さな円（端子台の端子）なら、端子番号と円の列の上の機器名。端が接地記号なら近くの GND。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 0, 0, 9, 11)
    _line(msp, (9, 8), (49, 8))
    _line(msp, (9, 3), (49, 3))
    _text(msp, '1', 4.5, 8)
    _text(msp, '2', 4.5, 3)
    _text(msp, 'CN1', 4.5, 13.5)
    msp.add_circle((50, 8), 1, dxfattribs={'color': 2})
    _text(msp, '1', 50, 5.5)
    _text(msp, 'TBR1B', 50, 12)
    _text(msp, 'GND', 53, 3)
    msp.add_ellipse((30, 5.5, 0), major_axis=(0, 4.5, 0), ratio=0.3, dxfattribs={'color': 2})
    _arrow(msp, (30, 10), 19.5)
    _text(msp, f'W No.{CABLE}', 22, 19.5, ap=7)
    path = tmp_path / 'EE0000-405-01A.dxf'
    doc.saveas(path)
    kinds = {w['ends']['+']['kind']: w['ends']['+'] for w in occurrence(read(str(path)))['wires']}
    assert (kinds['circle']['name'], kinds['circle']['pin']) == ('TBR1B', '1')
    assert kinds['gnd']['name'] == 'GND'


# ---------------------------------------------------------------- 細長いコネクタ枠（多ピン）

def build_tall(tmp_path, label_box=False, name='EE0000-405-01A', bottom=-100):
    """左右のコネクタが高さ 111 の細長い1枠（幅 9）で描かれた図面。横の芯線2本（y=8・3）が、枠の辺（x=9・x=200）に着く。
    label_box=True なら、右側の芯線の途中に、芯線に沿う横長のラベル枠（幅 75 × 高さ 5）を置く（x=20〜95、芯線はその両側で切れる）。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 0, bottom, 9, 11)
    _box(msp, 200, bottom, 209, 11)
    for y, pin, label in ((8, '1', 'P021'), (3, '2', 'N244')):
        if label_box and y == 8:
            _line(msp, (9, y), (20, y))
            _line(msp, (95, y), (200, y))
            _box(msp, 20, 5.5, 95, 10.5, color=4)
        else:
            _line(msp, (9, y), (200, y))
        _text(msp, pin, 4.5, y)
        _text(msp, pin, 204.5, y)
        _text(msp, label, 160, y + 0.5, ap=7)
    _text(msp, 'CN1', 4.5, 13.5)
    _text(msp, 'CN2', 204.5, 13.5)
    msp.add_ellipse((150, 5.5, 0), major_axis=(0, 4.5, 0), ratio=0.3, dxfattribs={'color': 2})
    _arrow(msp, (150, 10), 19.5)
    _text(msp, f'W No.{CABLE}', 142, 19.5, ap=7)
    path = tmp_path / f'{name}.dxf'
    doc.saveas(path)
    return str(path)


def test_tall_narrow_connector_frames_are_terminals_with_pins_and_names(tmp_path):
    """守るもの: 多ピンのコネクタは細長い1枠（幅 9 × 高さ 111。上限 60 を超える）で描かれ、芯線は枠の辺に着く。この枠が端末として読め、
    ピン（芯線と同じ高さの文字）とコネクタ名が取れる（以前は枠が候補から落ち、両端が読めなかった。実図面 EE6888-604-01A の CNESCD・CN1）。"""
    wires = by_label(occurrence(read(build_tall(tmp_path))))
    assert set(wires) == {'P021', 'N244'}
    for label, pin in (('P021', '1'), ('N244', '2')):
        ends = wires[label]['ends']
        assert (ends['-']['kind'], ends['-']['name'], ends['-']['pin']) == ('box', 'CN1', pin)
        assert (ends['+']['kind'], ends['+']['name'], ends['+']['pin']) == ('box', 'CN2', pin)


def test_connector_frame_up_to_350_long_is_a_terminal(tmp_path):
    """守るもの: 37 ピンのコネクタのように、高さ 300 を超える細長い枠（幅 9 × 高さ 311）も端末として読める（上限は 350。
    以前は 250 で、実図面 EE6892-611-01B の M65235 〈S-CNRFA・EXT.CONT。高さ 323〉の両端が読めなかった）。"""
    wires = by_label(occurrence(read(build_tall(tmp_path, bottom=-300))))
    assert wires['P021']['ends']['-']['name'] == 'CN1' and wires['P021']['ends']['+']['name'] == 'CN2'


def test_tall_frame_unit_side_point_follows_the_wire_direction_not_the_frame_center(tmp_path, monkeypatch):
    """守るもの: ユニット領域の根拠は、枠の辺からケーブルと反対側のすぐ外の点で調べる。その向きは芯線の向きで決める
    （細長い枠は、枠の中心への向きで決めると上下を選び、領域の外の点になる）。左のコネクタ CN1（芯線は右へ出る）の左側の領域が取れる。
    領域は、枠の中心（x=4.5）・名前の文字（y=13.5）を含まず、ユニット側の点（x=-0.6）だけを含む形にしてある。"""
    region = {'polygon': [(-20, -110), (3, -110), (3, 12), (-20, 12)], 'area': 2806.0, 'name_candidates': [(1.0, 'UNIT A')]}
    monkeypatch.setattr(sr, 'analyze_dxf_regions', lambda path, cfg, doc=None: {'regions': [region], 'minor_regions': []})
    wires = by_label(occurrence(read(build_tall(tmp_path))))
    assert wires['P021']['ends']['-']['regions'] == ['UNIT A']
    assert wires['P021']['ends']['+']['regions'] == []


def test_wide_label_box_along_the_wire_is_not_a_terminal(tmp_path):
    """守るもの: 芯線に沿う細長い枠（芯線の途中に置いた配線名の枠。幅 75 × 高さ 5）は端末にしない。細長い枠を許すのは芯線に直交する向きだけ
    （横の芯線なら縦長）。許すと配線名の枠（実図面 EE6888-650-01C の CM12）を端末と取り違え、ピンが読めなくなった。"""
    wires = by_label(occurrence(read(build_tall(tmp_path, label_box=True))))
    end = wires['P021']['ends']['-']
    shape = end['_shape']
    assert not shape or shape['rect'] != (20.0, 5.5, 95.0, 10.5)
    assert end['name'] != 'CM12'


# ---------------------------------------------------------------- コネクタの円（端子円）

def build_circles(tmp_path, through=False, dots_only=False, d=3.98, name='EE0000-405-01A'):
    """左右が円のコネクタ（直径 d。名前 CN1・CN2 を円の上に置く）で、中央にケーブル記号（楕円）がある図面。芯線は1本（y=8）。
    through=False: 芯線は円の縁から縁まで。through=True: 1本の長い線（x=-20〜90）が円の中心を通り抜ける（円は線の途中に載る）。
    dots_only=True: 大きなコネクタ円の代わりに、線の途中に接続点の小さな黒丸（直径 1）だけを置く。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    left, right = (0.0, 60.0)
    r = d / 2
    for x, label in ((left, 'CN1'), (right, 'CN2')):
        msp.add_circle((x, 8), 0.5 if dots_only else r, dxfattribs={'color': 4})
        if not dots_only:
            _text(msp, label, x, 11)
    a, b = (-20, 90) if through else (left + r, right - r)
    _line(msp, (a, 8), (b, 8))
    _text(msp, 'P021', 20, 8.5, ap=7)
    msp.add_ellipse((30, 5.5, 0), major_axis=(0, 4.5, 0), ratio=0.3, dxfattribs={'color': 2})
    _arrow(msp, (30, 10), 19.5)
    _text(msp, f'W No.{CABLE}', 22, 19.5, ap=7)
    path = tmp_path / f'{name}.dxf'
    doc.saveas(path)
    return str(path)


def _ends_of(result):
    (wire,) = [w for w in occurrence(result)['wires'] if w['labels'] == ['P021']]
    return {s: (e['kind'], e['name']) for s, e in wire['ends'].items()}


def test_connector_circle_wider_than_3_2_is_a_terminal(tmp_path):
    """守るもの: コネクタの円は直径 3.98 のものがある（端子円の判定の上限は 4.2）。以前は 3.2 までで、芯線が円の縁に着いていても端末が読めず、
    先の長い線の端（TB1）だけが端末になった（EE6888-604-01A の M74003〜M74005）。"""
    assert _ends_of(read(build_circles(tmp_path))) == {'-': ('circle', 'CN1'), '+': ('circle', 'CN2')}


def test_circle_larger_than_the_terminal_limit_is_not_a_terminal(tmp_path):
    """守るもの: 上限（4.2）を超える円は端子にしない（円の大きさの上限が効いている）。"""
    assert _ends_of(read(build_circles(tmp_path, d=5.0))) == {'-': (None, None), '+': (None, None)}


def test_through_line_stops_at_the_first_terminal_circle_on_it(tmp_path):
    """守るもの: コネクタの円が、記号から先まで通る1本の長い線の途中に載る図では、記号から外へ最初に出会う端子円で止める（線の端まで進んで
    先の端子台を拾わない。EE6888-604-01A の M74006: CNHV- の円から CNHVA- の円までが HV- から TB1 まで続く1本の線の一部）。"""
    assert _ends_of(read(build_circles(tmp_path, through=True))) == {'-': ('circle', 'CN1'), '+': ('circle', 'CN2')}


def test_small_junction_dots_on_a_through_line_do_not_stop_the_wire(tmp_path):
    """守るもの: 線の途中の接続点の黒丸（直径 1）は端子円ではないので、そこでは止めない（下限 1.9）。端は線の端のまま（端末なし）。"""
    assert _ends_of(read(build_circles(tmp_path, through=True, dots_only=True))) == {'-': (None, None), '+': (None, None)}


# ---------------------------------------------------------------- 1つのコネクタの端子どうしをつなぐ U 字の線（ジャンパ）

def build_jumpers(tmp_path, name='EE0000-405-01A'):
    """1つの細長いコネクタ CN**（幅 9 × 高さ 61。端子 5・6・8・9 の文字が枠の中）の端子 5-6 と 8-9 を、U 字の線（横→縦→横）でつなぐ図面。
    長円のケーブル記号が、2つの U 字の横線をまとめて横切る（EE6886-614-01A の M65027 と同じ形）。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 0, -61, 9, 0)
    for pin, y in (('5', -5), ('6', -10), ('8', -20), ('9', -25)):
        _text(msp, pin, 4.5, y)
    _text(msp, 'CN**', 4.5, 2.5)
    for ya, yb in ((-5, -10), (-20, -25)):
        _line(msp, (9, ya), (26, ya))
        _line(msp, (26, ya), (26, yb))
        _line(msp, (26, yb), (9, yb))
    msp.add_arc((22, -2), 1.5, 0, 180, dxfattribs={'color': 2})
    msp.add_arc((22, -28), 1.5, 180, 360, dxfattribs={'color': 2})
    _line(msp, (20.5, -28), (20.5, -2), 2)
    _line(msp, (23.5, -28), (23.5, -2), 2)
    _arrow(msp, (22, -0.5), 8)
    _text(msp, f'W No.{CABLE}', 14, 8, ap=7)
    path = tmp_path / f'{name}.dxf'
    doc.saveas(path)
    return str(path)


def test_u_shaped_jumper_wires_read_both_pins_of_the_same_connector_once_each(tmp_path):
    """守るもの: 両端が同じコネクタに着く U 字の線は、1本ずつ数え（同じ線を U 字の2つの横線から二度たどらない）、両端のピン（5/6・8/9）と
    コネクタ名が読める（以前は4本に数えて、U 字の線とコネクタの辺で閉じた小さな長方形を枠と取り違え、名前を隣のピン番号 `7`・`4` にし、
    2つ目の端が1つ目を上書きした。EE6886-614-01A の M65027）。"""
    occ = occurrence(read(build_jumpers(tmp_path)))
    pins = sorted(tuple(sorted(w['ends'][s]['pin'] for s in '-+')) for w in occ['wires'])
    assert pins == [('5', '6'), ('8', '9')]
    assert all(w['ends'][s]['name'] == 'CN**' and w['ends'][s]['kind'] == 'box' for w in occ['wires'] for s in '-+')


# ---------------------------------------------------------------- ユニットの根拠

def test_region_name_candidates_of_every_candidate_are_collected_for_the_end(tmp_path, monkeypatch):
    """端末の位置を含む領域の名称候補は、最有力でなくてもすべて集める。含まれない側は空。"""
    region = {'polygon': [(45, -2), (65, -2), (65, 14), (45, 14)], 'area': 320.0,
              'name_candidates': [(1.0, 'ALS1 R1'), (2.0, 'SHIELD COVER')]}
    monkeypatch.setattr(sr, 'analyze_dxf_regions', lambda path, cfg, doc=None: {'regions': [region], 'minor_regions': []})
    wires = by_label(occurrence(read(build(tmp_path))))
    assert wires['P021']['ends']['+']['regions'] == ['ALS1 R1', 'SHIELD COVER']
    assert wires['P021']['ends']['-']['regions'] == []


def test_minor_regions_are_used_too_and_smaller_region_comes_first(tmp_path, monkeypatch):
    big = {'polygon': [(40, -5), (70, -5), (70, 20), (40, 20)], 'area': 750.0, 'name_candidates': [(1.0, 'RACK')]}
    small = {'polygon': [(45, -2), (65, -2), (65, 14), (45, 14)], 'area': 320.0, 'name_candidates': [(1.0, 'DTT01')]}
    monkeypatch.setattr(sr, 'analyze_dxf_regions', lambda path, cfg, doc=None: {'regions': [big], 'minor_regions': [small]})
    wires = by_label(occurrence(read(build(tmp_path))))
    assert wires['N244']['ends']['+']['regions'] == ['DTT01', 'RACK']


def test_region_analysis_failure_is_reported_and_reading_continues(tmp_path, monkeypatch):
    def boom(path, cfg, doc=None):
        raise RuntimeError('x')

    monkeypatch.setattr(sr, 'analyze_dxf_regions', boom)
    result = read(build(tmp_path))
    assert 'RuntimeError' in result['region_error']
    assert len(occurrence(result)['wires']) == 2


def test_nearby_labels_are_listed_nearest_first_with_distance(tmp_path):
    """機器符号（コネクタ名）のラベルから 50 以内の文字を、距離の近い順に（文字の1行目・距離・全体）で返す。"""
    def extras(msp):
        _text(msp, 'ALS1 R1\\P(GEXUS-ARC2)', 70, 13.5)

    nearby = by_label(occurrence(read(build(tmp_path, extras=extras))))['P021']['ends']['+']['nearby']
    assert nearby[0][:2] == ('CN2', 0.0)
    near = {first: (dist, full) for first, dist, full in nearby}
    assert near['ALS1 R1'][0] == 15.5 and near['ALS1 R1'][1].startswith('ALS1 R1')
    assert [d for _, d, _ in nearby] == sorted(d for _, d, _ in nearby)
    assert all(d <= sr.NEAR_LABEL_DIST for _, d, _ in nearby)


def test_device_beyond_the_connector_is_read_by_following_the_wire(tmp_path):
    """機器そのものがユニットのもの（ヒーター等）: コネクタのケーブルと反対側の辺から出る線をたどり、機器の箱の文字を返す。"""
    def extras(msp):
        _line(msp, (0, 8), (-20, 8))
        _box(msp, -40, 2, -20, 14, 4)
        _text(msp, 'BH12', -30, 8)

    wires = by_label(occurrence(read(build(tmp_path, extras=extras))))
    assert wires['P021']['ends']['-']['devices'] == ['BH12']
    assert wires['P021']['ends']['+']['devices'] == []


# ---------------------------------------------------------------- 引出線なし・文字なし・非表示

def test_label_without_leader_picks_the_cylinder_below_the_vertical_text(tmp_path):
    """引出線が無く、縦書きのラベルを円筒（2つの楕円）の横に置くだけの描き方（633-01B）。文字の下側（縦書きなら右側）の円筒を採る。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 14, 60, 31, 71)
    _box(msp, 14, -11, 31, 0)
    for x in (20, 25):
        _line(msp, (x, 0), (x, 60))
    for y, pin_y in ((65, 65), (-5, -5)):
        _text(msp, '1', 20, pin_y)
        _text(msp, '2', 25, pin_y)
    _text(msp, 'CNE06', 12.5, -5, rot=90)
    _text(msp, 'CNE07', 12.5, 65, rot=90)
    for cy in (40, 20):
        msp.add_ellipse((22.5, cy, 0), major_axis=(7.5, 0, 0), ratio=0.2, dxfattribs={'color': 2})
    _text(msp, f'W No.{CABLE}', 12, 30, rot=90)
    _text(msp, 'P', 18.25, 30, rot=90)
    path = tmp_path / 'EE0000-405-01A.dxf'
    doc.saveas(path)
    occ = occurrence(read(str(path)))
    assert occ['how'] == 'beside' and len(occ['wires']) == 2
    pins = sorted((w['ends']['-']['pin'], w['ends']['+']['pin']) for w in occ['wires'])
    assert pins == [('1', '1'), ('2', '2')]
    assert {w['ends']['+']['name'] for w in occ['wires']} == {'CNE07'}


def test_drawing_without_any_text_is_flagged_and_not_read(tmp_path):
    doc = ezdxf.new('R2010')
    doc.modelspace().add_line((0, 0), (10, 0))
    path = tmp_path / 'EE0000-405-01A.dxf'
    doc.saveas(path)
    result = read(str(path))
    assert result['no_text'] and result['occurrences'] == []


def test_unreadable_file_returns_error_instead_of_raising(tmp_path):
    path = tmp_path / 'EE0000-405-01A.dxf'
    path.write_bytes(b'not a dxf')
    result = read(str(path))
    assert result['error'] and result['occurrences'] == []


def test_invisible_cable_label_is_ignored(tmp_path):
    """非表示（invisible 属性）のケーブル番号は使わない（旧版を消さずに隠す運用）。"""
    path = build(tmp_path)
    doc = ezdxf.readfile(path)
    for e in doc.modelspace().query('MTEXT'):
        if e.plain_text().startswith('W No.'):
            e.dxf.invisible = 1
    doc.saveas(path)
    assert read(path)['occurrences'] == []


# ---------------------------------------------------------------- 図中の参照 (-xxx-xx…)

@pytest.mark.parametrize('text, patterns', [
    ('to POWER LINE (SYSTEM I/F BOX) (-405-08)', ['-405-08']),
    ('XRLS1 (-405-08)', ['-405-08']),
    ('(-405-08) XRLS2', ['-405-08']),
    ('DO21-24 (-300-01 1/2)', ['-300-01']),                       # 後ろにシート番号
    ('to POWER LINE (CB BOX C1) (-405-05 3/4)', ['-405-05']),
    ('Refer to POWER LINE        (-405-01,02) NOTE2', ['-405-01', '-405-02']),  # カンマで続く2桁は頭を引き継ぐ別の参照
    ('(-405-01,02,03)', ['-405-01', '-405-02', '-405-03']),
    ('(-405-01, 02 3/4)', ['-405-01', '-405-02']),                  # カンマの後ろの空白・続くシート番号
    ('(-405-01,-300-05)', ['-405-01', '-300-05']),                   # 頭まで書いてあれば、その頭
    ('(-405-01,SHEET)', ['-405-01']),                                # カンマの後ろが2桁の符号でなければ参照にしない
    ('(-405-01 1/2,02)', ['-405-01']),                               # カンマは続けて書かれたものだけ（シート番号を挟むと対象外）
    ('to DEVICENET    (-405-21)', ['-405-21']),                      # 空白が並んでいてもよい
    ('（-405-08）', ['-405-08']),                                    # 全角括弧
    ('(-ab1-2c)', ['-AB1-2C']),                                     # 英数字（小文字は大文字に）
    ('( -405-08 )', ['-405-08']),                                   # 括弧の内側の空白
    ('to A (-405-08) and B (-405-09)', ['-405-08', '-405-09']),     # 1つの文字に複数
    ('(SYSTEM I/F BOX)', []),                                       # 括弧だけ
    ('(-405-0)', []),                                               # 桁が足りない
    ('(-4050-08)', []),                                             # 桁が多い
    ('405-08', []),                                                 # 括弧なし
    ('(405-08)', []),                                               # 先頭の - が無い
    ('(-405-08', []),                                               # 閉じていない
    ('', []),
])
def test_find_references_picks_the_drawing_number_part_in_parentheses(text, patterns):
    assert [r['pattern'] for r in sr.find_references([text])] == patterns


def test_find_references_keeps_the_source_text_and_drops_duplicates():
    refs = sr.find_references(['to POWER LINE\n(-405-08)', 'to POWER LINE (-405-08)', 'XRLS1 (-405-08)'])
    assert refs == [{'pattern': '-405-08', 'text': 'to POWER LINE (-405-08)'},
                    {'pattern': '-405-08', 'text': 'XRLS1 (-405-08)'}]


def test_analyze_schematic_returns_references_found_in_the_drawing(tmp_path):
    path = build(tmp_path, extras=lambda msp: _text(msp, 'to POWER LINE (SYSTEM I/F BOX) (-405-08)', 30, 40))
    assert read(path)['refs'] == [{'pattern': '-405-08', 'text': 'to POWER LINE (SYSTEM I/F BOX) (-405-08)'}]
    assert read(build(tmp_path, name='EE0000-405-02A'))['refs'] == []


@pytest.mark.parametrize('symbol', ['ellipse', 'capsule'])
def test_shapes_for_the_viewer_symbol_leader_wire_paths_and_connector_frames(tmp_path, symbol):
    """守るもの: viewer の図形の重ね書きに使う座標（`_` 始まり。Excel には出ない）。出現はケーブル記号の種類と外接矩形（`_symbols`）と引出線
    の線分（`_leader`）、芯線は経路の線分（`_path`。芯線の線分を含む）、端はコネクタ枠の矩形（`_shape`）を持つ。判定（how・端末）は変わらない。"""
    occ = occurrence(read(build(tmp_path, symbol)))
    assert [s['shape'] for s in occ['_symbols']] == [symbol] and len(occ['_symbols'][0]['box']) == 4
    assert occ['_leader'] and all(len(seg) == 2 for seg in occ['_leader'])
    for wire in occ['wires']:
        assert wire['_path'] and all(len(seg) == 2 for seg in wire['_path'])
        for side in '-+':
            shape = wire['ends'][side]['_shape']
            assert shape and len(shape['rect']) == 4
    x0, y0, x1, y1 = occ['_symbols'][0]['box']
    path = [p for w in occ['wires'] for seg in w['_path'] for p in seg]
    assert min(p[0] for p in path) < x0 and max(p[0] for p in path) > x1       # 芯線は記号の両側へ伸びる


def test_circle_terminal_shape_has_center_and_radius_and_gnd_has_none(tmp_path):
    """守るもの: 端子の円は {'circle': (中心, 半径)}（半径は円の外接の半分）、接地は図形なし（文字の強調だけ）。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 0, 0, 9, 11)
    _line(msp, (9, 8), (49, 8))
    _line(msp, (9, 3), (49, 3))
    _text(msp, '1', 4.5, 8)
    _text(msp, '2', 4.5, 3)
    _text(msp, 'CN1', 4.5, 13.5)
    msp.add_circle((50, 8), 1, dxfattribs={'color': 2})
    _text(msp, '1', 50, 5.5)
    _text(msp, 'TBR1B', 50, 12)
    _text(msp, 'GND', 53, 3)
    msp.add_ellipse((30, 5.5, 0), major_axis=(0, 4.5, 0), ratio=0.3, dxfattribs={'color': 2})
    _arrow(msp, (30, 10), 19.5)
    _text(msp, f'W No.{CABLE}', 22, 19.5, ap=7)
    path = tmp_path / 'EE0000-405-01A.dxf'
    doc.saveas(path)
    ends = {w['ends']['+']['kind']: w['ends']['+'] for w in occurrence(read(str(path)))['wires']}
    (cx, cy), r = ends['circle']['_shape']['circle']
    assert (round(cx, 1), round(cy, 1), round(r, 1)) == (50.0, 8.0, 1.0)
    assert ends['gnd']['_shape'] is None


# ---------------------------------------------------------------- 長い筒（切れ目の続き印 `*1`）・コネクタ名の選び方

def build_long_pipe(tmp_path, continued=True, name='EE0000-405-01A'):
    """縦に長い筒（幅 4）を横の芯線が貫く図面。上端の丸みの円弧と線の端が 0.2 ずれている。
    continued=True なら、筒は下端が開いて続き印 `*1` が付き、別の場所（x=150）の筒（上端が開いて `*1`、下端が丸み）へ続く。
    芯線: 左の筒に y=60・y=80、右の筒に y=40・y=60。両端は 9 幅のコネクタ枠（左 x=0〜9、右 x=250〜259）。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 0, 0, 9, 100)
    _box(msp, 250, 0, 259, 100)
    msp.add_arc((50, 90), 2, 0, 180, dxfattribs={'color': 2})
    _line(msp, (48, 20), (48, 90.2), 2)
    _line(msp, (52, 20), (52, 90.2), 2)
    for y in (60, 80):
        _line(msp, (9, y), (250, y))
        _text(msp, '1', 4.5, y)
        _text(msp, '1', 254.5, y)
    if continued:
        _text(msp, '*1', 50, 18, h=1.5)
        msp.add_arc((150, 10), 2, 180, 360, dxfattribs={'color': 2})
        _line(msp, (148, 10), (148, 70), 2)
        _line(msp, (152, 10), (152, 70), 2)
        _text(msp, '*1', 150, 72, h=1.5)
        _line(msp, (9, 40), (250, 40))
        _text(msp, '1', 4.5, 40)
        _text(msp, '1', 254.5, 40)
    _arrow(msp, (50, 92), 99.5)
    _text(msp, f'W No.{CABLE}', 42, 99.5, ap=7)
    path = tmp_path / f'{name}.dxf'
    doc.saveas(path)
    return str(path)


def test_long_pipe_with_a_small_gap_between_arc_and_lines_is_one_symbol_with_its_wires(tmp_path):
    """守るもの: 円弧と線の端が 0.2 ずれた細長い筒でも、筒の全体（外接矩形）を1つの記号として読み、貫く芯線が取れる
    （以前は端点の許容 0.1 を超えて円弧の先端 2 だけが記号になり、芯線が0本だった。実図面 EE6892-611-01B の M65235）。"""
    occ = occurrence(read(build_long_pipe(tmp_path, continued=False)))
    assert occ['how'] == 'capsule' and len(occ['_symbols']) == 1
    x0, y0, x1, y1 = occ['_symbols'][0]['box']
    assert (round(y0), round(y1)) == (20, 92) and round(x1 - x0) == 4
    assert len(occ['wires']) == 2


def test_pipe_continued_by_a_star_mark_adds_the_other_pipe_and_its_wires(tmp_path):
    """守るもの: 筒の開いた端に続き印（`*1`）があるとき、同じ続き印が付いた別の筒も同じケーブルの記号にし、そこを貫く芯線も含める
    （長いケーブルを途中で切って別の場所へ続ける描き方。実図面 EE6892-611-01B の M65235）。続き印の無い図面は筒1つのまま。"""
    occ = occurrence(read(build_long_pipe(tmp_path)))
    assert len(occ['_symbols']) == 2
    assert sorted(round(sym['box'][0]) for sym in occ['_symbols']) == [48, 148]
    assert len(occ['wires']) == 3


def test_connector_name_is_the_label_centred_over_the_frame_when_two_labels_are_equally_near(tmp_path):
    """守るもの: コネクタ枠の上に、枠から同じ距離の文字が2つ並ぶとき（枠の真上の `INPUT` と、すぐ右の `CP(25A)`）は、枠の中心に近いほうを名前にする
    （以前は文字の並び順で先の `CP(25A)` になった。実図面 EE6892-611-01B の M65232）。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 50, 0, 59, 11)
    _text(msp, 'CP(25A)', 62, 13.5, ap=5)          # 先に描く（並び順では先）。枠の右にはみ出して並ぶ
    _text(msp, 'INPUT', 54.5, 13.5, ap=5)
    path = tmp_path / 'EE0000-405-01A.dxf'
    doc.saveas(path)
    g, _doc = sr.load(str(path))
    term = sr.terminal_at(g, sr.axis_rects(g), (50, 5), (1.0, 0.0))
    assert term['connector'] == 'INPUT'


# ---------------------------------------------------------------- コネクタ・端子で途切れたユニットの枠

def build_gapped_frame(tmp_path, covered=True, name='EE0000-405-01A'):
    """幅 40 × 高さ 80 の枠。左辺は、端子の円（直径 4.5）・コネクタ名の文字・コネクタ枠で途切れ、下辺は小さな円（直径 3）で途切れる。
    covered=False なら、左辺の隙間（y=35〜47.25）に何も置かない。枠の中の下辺の近く（y=5）に名前 `MBX2 R1`。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _line(msp, (0, 80), (40, 80), 2)
    _line(msp, (40, 0), (40, 80), 2)
    _line(msp, (0, 0), (18, 0), 2)
    _line(msp, (21, 0), (40, 0), 2)
    msp.add_circle((19.5, 0), 1.5, dxfattribs={'color': 4})
    _line(msp, (0, 80), (0, 47.25 if covered else 50), 2)
    if covered:
        msp.add_circle((0, 45), 2.25, dxfattribs={'color': 4})
        _line(msp, (0, 42.75), (0, 35), 2)
        _text(msp, 'AMC', 0, 32, ap=5)
    _line(msp, (0, 25 if covered else 35), (0, 0), 2)
    if covered:
        _box(msp, -4.5, 25, 4.5, 30)
    _text(msp, 'MBX2 R1', 20, 5, ap=5)
    path = tmp_path / f'{name}.dxf'
    doc.saveas(path)
    return str(path)


def test_frame_with_edges_interrupted_by_a_terminal_circle_connector_and_label_is_a_region(tmp_path):
    """守るもの: 辺が端子の円・コネクタ枠・コネクタ名の文字で途切れたユニットの枠は、閉じた矩形の領域として、枠の辺に近い領域名候補（`MBX2 R1`）を持つ
    （領域抽出は円で途切れた枠を閉じず、機器の箱も見つからないので、以前はユニットが読めなかった。実図面 EE6892-611-01B の MBX2 R1）。"""
    g, _doc = sr.load(build_gapped_frame(tmp_path))
    (region,) = sr.gapped_frame_regions(g)
    assert sr._bbox(region['polygon']) == pytest.approx((0, 0, 40, 80), abs=0.7)
    assert [name for _d, name in region['name_candidates']] == ['MBX2 R1']


def test_frame_whose_edge_gap_is_not_filled_by_a_device_is_not_a_region(tmp_path):
    """守るもの: 辺の隙間が部品・文字で埋まらない枠（何も無い隙間）は、閉じた領域にしない（無関係な線をつないで枠にしない）。"""
    g, _doc = sr.load(build_gapped_frame(tmp_path, covered=False))
    assert sr.gapped_frame_regions(g) == []


def test_plain_closed_rectangle_is_not_returned_as_a_gapped_frame(tmp_path):
    """守るもの: 途切れの無い閉じた矩形は、機器の箱（`axis_rects`）と区別がつかないので対象外（領域は足さない）。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    _box(msp, 0, 0, 40, 80)
    _text(msp, 'MBX2 R1', 20, 5, ap=5)
    path = tmp_path / 'EE0000-405-01A.dxf'
    doc.saveas(path)
    g, _doc = sr.load(str(path))
    assert sr.gapped_frame_regions(g) == []


# ---------------------------------------------------------------- シールド（記号の端から伸びる破線）

def test_dashed_lines_from_the_symbol_ends_to_another_ellipse_are_the_shield(tmp_path):
    """守るもの: 楕円のケーブル記号の上下の端から同じ高さで伸びる破線と、その先の閉じた楕円（接地記号側）をシールドとして読む（`_shield`）。
    実図面 EE6892-611-01B の M65232（出現 A）。接地記号・文字は含めない。"""
    def extras(msp):
        _line(msp, (30, 10), (40, 10), 3, 'DASHED')
        _line(msp, (30, 1), (40, 1), 3, 'DASHED')
        msp.add_ellipse((40, 5.5, 0), major_axis=(0, 4.5, 0), ratio=0.3, dxfattribs={'color': 2})
    shield = occurrence(read(build(tmp_path, extras=extras)))['_shield']
    assert len(shield['lines']) == 2 and len(shield['ellipses']) == 1
    assert sorted(round(p[1]) for line in shield['lines'] for p in line) == [1, 1, 10, 10]


def test_dashed_wire_through_the_symbol_centre_is_not_a_shield(tmp_path):
    """守るもの: 芯線そのものが破線の図面（記号の中心を貫く破線。610-01B の ACCESSORY CABLE）は、シールドと取り違えない。"""
    def extras(msp):
        _line(msp, (30, 5.5), (40, 5.5), 3, 'DASHED')
        msp.add_ellipse((40, 5.5, 0), major_axis=(0, 4.5, 0), ratio=0.3, dxfattribs={'color': 2})
    assert occurrence(read(build(tmp_path, extras=extras)))['_shield'] is None


# ---------------------------------------------------------------- ラベルのすぐ下の文字（備考）

def test_text_just_below_the_cable_label_is_the_remark_unless_it_is_another_cable_label(tmp_path):
    """守るもの: ケーブル番号のラベルのすぐ下の別の文字（`ACCESSORY CABLE`）を備考にする（以前は同じ文字の中の後ろだけで、備考が空だった。
    実図面 EE6892-611-01B の M65232・M65239・M65A61）。下の文字がケーブル番号を含む（別のケーブルのラベル。610-01B の M65216 の下の `W No.M65217`）なら足さない。"""
    path = build(tmp_path, extras=lambda msp: _text(msp, 'ACCESSORY CABLE', 22, 16.2, ap=7))
    occ = occurrence(read(path))
    assert occ['remark'] == 'ACCESSORY CABLE'
    assert occ['_remark_ref'][0] == 'ACCESSORY CABLE' and occ['_remark_ref'][1] is not None      # 別の文字は viewer で強調する
    path = build(tmp_path, extras=lambda msp: _text(msp, 'W No.M99999', 22, 16.2, ap=7), name='EE0001-405-01A')
    assert [o['remark'] for o in read(path)['occurrences'] if o['cable'] == CABLE] == ['']


def test_horizontal_pipe_is_continued_by_a_star_mark_too(tmp_path):
    """守るもの: 続き印（`*1`）での筒の連結は、横長の筒でも働く（縦長の筒の向きの判定を取り違えて、横長で斜めの線を拾い連結できなかった）。"""
    doc = ezdxf.new('R2010', setup=True)
    msp = doc.modelspace()
    msp.add_arc((90, 50), 2, -90, 90, dxfattribs={'color': 2})
    _line(msp, (20, 48), (90.2, 48), 2)
    _line(msp, (20, 52), (90.2, 52), 2)
    _text(msp, '*1', 18, 50, h=1.5)
    msp.add_arc((10, 150), 2, 90, 270, dxfattribs={'color': 2})
    _line(msp, (10, 148), (70, 148), 2)
    _line(msp, (10, 152), (70, 152), 2)
    _text(msp, '*1', 72, 150, h=1.5)
    _line(msp, (71, 148), (73, 151), 2)      # 筒の端の近くの斜めの線（筒の線ではない）
    _line(msp, (60, 30), (60, 170))
    _arrow(msp, (92, 50), 59.5)
    _text(msp, f'W No.{CABLE}', 84, 59.5, ap=7)
    path = tmp_path / 'EE0000-405-01A.dxf'
    doc.saveas(path)
    occ = occurrence(read(str(path)))
    assert occ['how'] == 'capsule' and len(occ['_symbols']) == 2


@pytest.mark.parametrize('jog', [1.0, 0.6])
def test_wire_with_a_short_vertical_jog_is_followed_to_the_connector(tmp_path, jog):
    """守るもの（実図面 EE6888-637-01A の M39065。A2–5 の芯線）: 芯線の途中に高さ 1.0 の小さな段差（縦の線分）があっても、芯線は段差を越えて
    コネクタまで追跡する。以前は、次の線分を「長さ 1.2 より長いもの」に限っていたため、段差の手前で止まり、`-` 側の端末が読めなかった。"""
    def extras(msp):
        for e in list(msp.query('LINE')):
            if (round(e.dxf.start.x), round(e.dxf.start.y)) == (9, 8) and round(e.dxf.end.x) == 50:
                msp.delete_entity(e)
        msp.add_lwpolyline([(9, 8), (20, 8), (20, 8 + jog), (50, 8 + jog)], dxfattribs={'color': 4})
    wires = occurrence(read(build(tmp_path, extras=extras)))['wires']
    pins = sorted((w['ends']['-']['pin'], w['ends']['+']['pin']) for w in wires if w['ends'].get('-') and w['ends']['-']['kind'])
    assert pins == [('1', '1'), ('2', '2')], pins
