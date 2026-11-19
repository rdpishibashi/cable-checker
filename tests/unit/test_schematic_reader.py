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
    monkeypatch.setattr(sr, 'analyze_dxf_regions', lambda path, cfg: {'regions': [], 'minor_regions': []})


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
    assert occurrence(read(path))['remark'] == 'ACCESSORY CABLE'


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


# ---------------------------------------------------------------- ユニットの根拠

def test_region_name_candidates_of_every_candidate_are_collected_for_the_end(tmp_path, monkeypatch):
    """端末の位置を含む領域の名称候補は、最有力でなくてもすべて集める。含まれない側は空。"""
    region = {'polygon': [(45, -2), (65, -2), (65, 14), (45, 14)], 'area': 320.0,
              'name_candidates': [(1.0, 'ALS1 R1'), (2.0, 'SHIELD COVER')]}
    monkeypatch.setattr(sr, 'analyze_dxf_regions', lambda path, cfg: {'regions': [region], 'minor_regions': []})
    wires = by_label(occurrence(read(build(tmp_path))))
    assert wires['P021']['ends']['+']['regions'] == ['ALS1 R1', 'SHIELD COVER']
    assert wires['P021']['ends']['-']['regions'] == []


def test_minor_regions_are_used_too_and_smaller_region_comes_first(tmp_path, monkeypatch):
    big = {'polygon': [(40, -5), (70, -5), (70, 20), (40, 20)], 'area': 750.0, 'name_candidates': [(1.0, 'RACK')]}
    small = {'polygon': [(45, -2), (65, -2), (65, 14), (45, 14)], 'area': 320.0, 'name_candidates': [(1.0, 'DTT01')]}
    monkeypatch.setattr(sr, 'analyze_dxf_regions', lambda path, cfg: {'regions': [big], 'minor_regions': [small]})
    wires = by_label(occurrence(read(build(tmp_path))))
    assert wires['N244']['ends']['+']['regions'] == ['DTT01', 'RACK']


def test_region_analysis_failure_is_reported_and_reading_continues(tmp_path, monkeypatch):
    def boom(path, cfg):
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
