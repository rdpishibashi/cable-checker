"""model/cable_preview.py のテスト（合成 DXF）。

守るもの: ケーブル番号を含む文字だけが赤字の対象になる（W No. 付き・単独番号・ブロック内の文字を含み、
MC121A1 のような非ケーブルや非表示の文字は含まない）／空の図面／読めない DXF は例外。
"""
import io

import ezdxf
import pytest

from model import cable_preview


def to_bytes(doc):
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode('utf-8')


def make_dxf(texts, block_texts=(), hidden_layer_texts=()):
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    for i, t in enumerate(texts):
        msp.add_text(t, dxfattribs={'insert': (10, 10 + i * 10), 'height': 3})
    if block_texts:
        blk = doc.blocks.new('B1')
        for i, t in enumerate(block_texts):
            blk.add_text(t, dxfattribs={'insert': (0, i * 10), 'height': 3})
        msp.add_blockref('B1', (100, 0))
    if hidden_layer_texts:
        doc.layers.add('HID', dxfattribs={'flags': 1})  # frozen
        for i, t in enumerate(hidden_layer_texts):
            msp.add_text(t, dxfattribs={'insert': (200, i * 10), 'height': 3, 'layer': 'HID'})
    return to_bytes(doc)


def highlighted_texts(base):
    return sorted(t for t, _ in base.text_classes if cable_preview.find_cables([t]))


def test_only_cable_texts_are_highlighted():
    """守るもの: 赤字の対象は find_cables() が拾う文字だけ（非ケーブルの MC121A1・DWG No. は対象外）。"""
    base = cable_preview.render_base_svg(make_dxf(['W No.M39001', 'M06004B', 'MC121A1', 'DWG No.', 'ＷＮｏ．Ｍ６５２５４']))
    classes, count = cable_preview.cable_classes(base)
    assert count == 3
    assert highlighted_texts(base) == ['M06004B', 'W No.M39001', 'WNo.M65254']
    assert classes and all(c[1] in ('fill', 'stroke') for c in classes)


def test_block_texts_are_highlighted():
    """守るもの: INSERT のブロック内の文字も個別に赤字にできる（モデル空間へ移して描く）。"""
    base = cable_preview.render_base_svg(make_dxf(['W No.M39001'], block_texts=['W No.M10001', 'ABC']))
    assert cable_preview.cable_classes(base)[1] == 2


def test_invisible_texts_are_not_counted():
    """守るもの: 画層 OFF/凍結の文字は描かれないので、赤字の対象にも数えない（見える図面と一致）。"""
    base = cable_preview.render_base_svg(make_dxf(['W No.M39001'], hidden_layer_texts=['W No.M10001']))
    assert cable_preview.cable_classes(base)[1] == 1


def test_base_svg_is_white_and_highlight_classes_exist_in_svg():
    base = cable_preview.render_base_svg(make_dxf(['W No.M39001']))
    classes, _ = cable_preview.cable_classes(base)
    assert not base.empty and base.view_box[0] > 0
    for name, _kind in classes:
        assert name in base.inner_svg  # クラス名は SVG の中の文字要素が使っている


def test_empty_drawing():
    base = cable_preview.render_base_svg(to_bytes(ezdxf.new('R2010')))
    assert base.empty
    assert cable_preview.build_viewer_data(base, 'x.dxf')['count'] == 0


def test_unreadable_dxf_raises():
    with pytest.raises(Exception):
        cable_preview.render_base_svg(b'not a dxf at all')


def test_build_viewer_data_is_json_serializable():
    import json
    data = cable_preview.build_viewer_data(cable_preview.render_base_svg(make_dxf(['W No.M39001'])), 'EE1234-405-01A.dxf')
    assert data['count'] == 1 and data['color'] == cable_preview.CABLE_COLOR
    json.dumps(data)
