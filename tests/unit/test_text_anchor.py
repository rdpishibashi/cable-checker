"""文字の基準点（text_anchor）が、読み取り（drawing_geometry・schematic_reader）と viewer（cable_preview）で一致する。

守るもの: viewer の強調は「基準点＋文字」で読み取りの文字を指す。msp の文字・位置合わせ付き TEXT・MTEXT・ブロック内・入れ子ブロック・
回転した INSERT の中の文字で、3つの経路が同じ基準点を出すこと（文字幅の見積もりに頼らない）。
"""
import io
import unicodedata

import ezdxf

from model import cable_preview, drawing_geometry, schematic_reader


def _doc():
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    msp.add_text('PLAIN', dxfattribs={'insert': (10, 10), 'height': 3})
    t = msp.add_text('CENTER', dxfattribs={'height': 3, 'halign': 1, 'valign': 2})
    t.dxf.insert = (50, 50)
    t.dxf.align_point = (60.5, 51.25)
    msp.add_mtext('MTEXT', dxfattribs={'insert': (20, 30), 'char_height': 3})
    inner = doc.blocks.new('INNER')
    inner.add_text('INNERTXT', dxfattribs={'insert': (1, 2), 'height': 2})
    outer = doc.blocks.new('OUTER')
    outer.add_text('OUTERTXT', dxfattribs={'insert': (3, 4), 'height': 2})
    outer.add_blockref('INNER', (10, 0))
    msp.add_blockref('OUTER', (100, 100), dxfattribs={'rotation': 30, 'xscale': 1.5, 'yscale': 1.5})
    return doc


def _bytes(doc):
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode('utf-8')


def test_anchor_is_identical_in_geometry_schematic_and_viewer(tmp_path):
    """守るもの: 3つの経路（393 の読み取り・405 の読み取り・viewer の描画）が同じ文字に同じ基準点を付ける。"""
    data = _bytes(_doc())
    path = tmp_path / 'x.dxf'
    path.write_bytes(data)
    geo = drawing_geometry.load(str(path))
    sch, _doc_ = schematic_reader.load(str(path))
    base = cable_preview.render_base_svg(data)
    want = {unicodedata.normalize('NFKC', t.s).strip(): t.ref for t in geo.txts}
    assert set(want) == {'PLAIN', 'CENTER', 'MTEXT', 'OUTERTXT', 'INNERTXT'}
    assert all(ref is not None for ref in want.values())
    assert {t.s: t.ref for t in sch.txts} == want
    assert {t['text']: tuple(t['anchor']) for t in base.texts} == want
    assert want['CENTER'] == (60.5, 51.25)       # 位置合わせ付きは align_point


def test_anchor_matching_ignores_text_width_estimates(tmp_path):
    """守るもの: viewer 側への対応づけ（apply_marks）が基準点で当たり、重なる別の文字があれば文字列で絞る。"""
    base = cable_preview.render_base_svg(_bytes(_doc()))
    items, counts, missing = cable_preview.apply_marks(
        base, [{'anchor': (10, 10), 'text': 'PLAIN', 'symbol': '○'}, {'anchor': (99, 99), 'text': 'X', 'symbol': '×'}])
    assert counts == {'○': 1} and missing == 1
    assert items and all(c[2] == cable_preview.SYMBOL_TEXT_COLORS['○'] for c in items)
