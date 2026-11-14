"""DXF の図面を SVG にし、ケーブル番号を含む文字を赤字で示す viewer のデータ（モデル層。streamlit 非依存）。

DXF-extract-labels の `model/region_preview.py`（領域確認 viewer の描画）から、図面の描画に必要な部分だけを
移植した独立の派生（共有ファイルではない。同期しない）。領域検出・ラベル位置の突き合わせは持たない。

仕組み: ブロック内の文字だけをモデル空間へ移し、文字1件ごとに一意の「目印の色」を付けて描く。ezdxf は色ごとに
CSS クラスを作るので、そのクラスを文字の正規化テキストと対応づけて控え（`BaseRender.text_classes`）、SVG の
中身は白に書き換える。viewer は、ケーブル番号を含む文字（`find_cables()` が1件以上返すもの）のクラスの
色だけを CSS で赤に差し替える（描き直さない）。赤くなるのは**その文字全体**（番号の部分だけではない）。
"""
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ezdxf import colors as ezdxf_colors
from ezdxf import recover
from ezdxf.addons.drawing import Frontend, RenderContext, config as drawing_config, layout, svg
from ezdxf.fonts import font_manager as ezdxf_font_manager
from ezdxf.fonts import fonts as ezdxf_fonts

from .cable_extractor import find_cables
from .common_utils import is_invisible
from .extract_labels import extract_text_from_entity

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

BUNDLED_FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
BUNDLED_FONT_NAME = "ipaexg.ttf"
CJK_FONT_CANDIDATES = [
    BUNDLED_FONT_NAME, "ipag.ttf", "Arial Unicode.ttf", "ArialUni.ttf",
    "msgothic.ttc", "meiryo.ttc", "NotoSansCJK-Regular.ttc", "NotoSansCJKjp-Regular.otf",
]

BASE_COLOR = "#ffffff"      # 図面全体の色（背景は黒）
CABLE_COLOR = "#ff3030"     # ケーブル番号を含む文字の色（赤）
# `BaseRender` の項目を変えたら上げる（view の cache_data のキーに含める）
BASE_RENDER_VERSION = 1

_TEXT_TYPES = ("TEXT", "MTEXT")
_MTEXT_COLOR_CODE = re.compile(r"\\[Cc][0-9]+;?")  # 本文中の色指定（\C1; \c255;）。目印の色に置き換える
_MAX_INSERT_DEPTH = 20
_STYLE_RULE = re.compile(r"^\.(C[0-9A-Fa-f]+)\s*\{(.*)\}\s*$", re.S)


@dataclass
class BaseRender:
    inner_svg: str = ""
    view_box: Tuple[float, float] = (0.0, 0.0)
    cjk_font: Optional[str] = None
    empty: bool = False
    # 文字ごとの CSS クラス: [(NFKC 正規化した文字列, [[クラス名, 'fill'|'stroke'], ...]), ...]（文字1件につき1要素）
    text_classes: List[Tuple[str, list]] = field(default_factory=list)


def _register_bundled_font() -> None:
    """同梱フォントを ezdxf のフォント管理に登録する（未登録のときだけ）。"""
    manager = ezdxf_fonts.font_manager
    if not (BUNDLED_FONT_DIR / BUNDLED_FONT_NAME).is_file():
        return
    if not manager.has_font(BUNDLED_FONT_NAME):
        manager.build([str(BUNDLED_FONT_DIR)], support_dirs=False)
    if BUNDLED_FONT_NAME not in ezdxf_font_manager.DEFAULT_FONTS:
        ezdxf_font_manager.DEFAULT_FONTS.append(BUNDLED_FONT_NAME)
    manager._fallback_font_name = ""


def find_cjk_font() -> Optional[str]:
    _register_bundled_font()
    manager = ezdxf_fonts.font_manager
    for name in CJK_FONT_CANDIDATES:
        if manager.has_font(name):
            return name
    return None


def _insert_instances(insert):
    """INSERT 1件を描画上の実体に分ける（MINSERT は格子の要素ごと）。"""
    try:
        if (insert.dxf.get("row_count", 1) or 1) > 1 or (insert.dxf.get("column_count", 1) or 1) > 1:
            return list(insert.multi_insert())
    except Exception:
        pass
    return [insert]


def _lift_block_texts(doc) -> int:
    """ブロック内の TEXT/MTEXT を、モデル空間の実体に移す（個別に色を付けられるようにする）。

    線などの図形は INSERT のまま残す。移した文字は、たどった INSERT が参照するブロック定義からだけ取り除く
    （寸法値の無名ブロックの文字は消さない）。非表示（invisible 属性・画層 OFF）の文字は移さない
    （ezdxf も描かないので、見える図面と一致する）。画層 0 の文字は親 INSERT の画層に付け替える。"""
    msp = doc.modelspace()
    traversed = set()

    def collect(insert, parent_layer, depth):
        out = []
        if depth > _MAX_INSERT_DEPTH:
            return out
        traversed.add(insert.dxf.name)
        try:
            for v in insert.virtual_entities():
                if is_invisible(v):
                    continue
                layer = v.dxf.layer if v.dxf.layer != "0" else parent_layer
                t = v.dxftype()
                if t in _TEXT_TYPES:
                    v.dxf.layer = layer
                    out.append(v)
                elif t == "INSERT":
                    for inst in _insert_instances(v):
                        out.extend(collect(inst, layer, depth + 1))
        except Exception:
            pass
        return out

    lifted = []
    for e in list(msp.query("INSERT")):
        if is_invisible(e):
            continue
        for inst in _insert_instances(e):
            lifted.extend(collect(inst, e.dxf.layer, 0))
    for blk in doc.blocks:
        if blk.is_any_layout or blk.name not in traversed:
            continue
        for x in list(blk):
            if x.dxftype() in _TEXT_TYPES:
                blk.delete_entity(x)
    for v in lifted:
        msp.add_entity(v)
    return len(lifted)


def _assign_marker_colors(doc) -> Dict[str, Optional[str]]:
    """モデル空間の TEXT/MTEXT すべてに、1件ごとに一意の「目印の色」を付ける。

    戻り値: {目印の色（'#rrggbb'）: NFKC 正規化した文字列（取れなければ None）}。
    赤成分を 0x80 以上にするのは、暗い色を ezdxf が背景に合わせて置き換えないようにするため。"""
    marker_to_text: Dict[str, Optional[str]] = {}
    n = 0
    for e in doc.modelspace().query("TEXT MTEXT"):
        n += 1
        rgb = (0x80 | ((n >> 16) & 0x7F), (n >> 8) & 0xFF, n & 0xFF)
        text = None
        try:
            _raw, t, _pos = extract_text_from_entity(e)
            text = unicodedata.normalize('NFKC', t).strip() if t else None
        except Exception:
            pass
        e.dxf.true_color = ezdxf_colors.rgb2int(rgb)
        if e.dxftype() == "MTEXT":
            # 本文中の色指定は実体の色を打ち消すので、目印の色（`\c` は BGR の整数）に置き換える
            bgr = (rgb[2] << 16) | (rgb[1] << 8) | rgb[0]
            e.text = _MTEXT_COLOR_CODE.sub(lambda _m: "\\c%d;" % bgr, e.text)
        marker_to_text["#%02x%02x%02x" % rgb] = text or None
    return marker_to_text


def _rewrite_marker_styles(root, marker_to_text) -> List[Tuple[str, list]]:
    """SVG の `<style>` のうち目印の色（fill/stroke）を使うクラスを白に書き換え、
    `[(正規化した文字列, [[クラス名, 'fill'|'stroke'], ...]), ...]` を返す（文字1件につき1要素）。"""
    per_marker: Dict[str, list] = {}
    for el in root.iter():
        if el.tag.split("}")[-1] != "style" or not el.text:
            continue
        m = _STYLE_RULE.match(el.text.strip())
        if not m:
            continue
        cls, body = m.group(1), m.group(2)
        for kind in ("fill", "stroke"):
            km = re.search(r"%s:\s*(#[0-9a-fA-F]{6})\b" % kind, body)
            if not km or km.group(1).lower() not in marker_to_text:
                continue
            marker = km.group(1).lower()
            body = body.replace(km.group(0), "%s: %s" % (kind, BASE_COLOR))
            per_marker.setdefault(marker, []).append([cls, kind])
        el.text = ".%s {%s}" % (cls, body)
    return [(marker_to_text[mk], cls) for mk, cls in per_marker.items() if marker_to_text[mk]]


def render_base_svg(dxf_bytes: bytes) -> BaseRender:
    """元の図面全体（モデル空間）を白一色の SVG にする。dxf_bytes は変更しない。

    見た目は白一色だが、文字（TEXT/MTEXT）は1件ごとに専用の CSS クラスを持つ（`BaseRender.text_classes`）。
    DXF として読めないときは例外（ezdxf のもの）を投げる。"""
    doc, _auditor = recover.read(io.BytesIO(dxf_bytes))
    cjk_font = find_cjk_font()
    if cjk_font:
        for style in doc.styles:
            style.dxf.font = cjk_font
            if style.dxf.hasattr("bigfont"):
                style.dxf.bigfont = ""

    if len(doc.modelspace()) == 0:
        return BaseRender(cjk_font=cjk_font, empty=True)

    _lift_block_texts(doc)
    marker_to_text = _assign_marker_colors(doc)

    def keep_base_color(entity, properties):
        # 文字の色は実体に付けた目印の色。文字以外は白
        if entity.dxftype() not in _TEXT_TYPES:
            properties.color = BASE_COLOR

    cfg = drawing_config.Configuration(
        background_policy=drawing_config.BackgroundPolicy.BLACK,
        color_policy=drawing_config.ColorPolicy.COLOR,
    )
    backend = svg.SVGBackend()
    frontend = Frontend(RenderContext(doc), backend, config=cfg)
    frontend.push_property_override_function(keep_base_color)
    frontend.draw_layout(doc.modelspace())
    if not backend.player().bbox().has_data:
        return BaseRender(cjk_font=cjk_font, empty=True)

    root = backend.get_xml_root_element(layout.Page(0, 0, layout.Units.mm))
    text_classes = _rewrite_marker_styles(root, marker_to_text)
    vb = [float(v) for v in root.get("viewBox", "0 0 0 0").split()]
    parts: List[str] = []
    for child in root:
        if child.tag.split("}")[-1] in ("defs", "g"):
            parts.append(ET.tostring(child, encoding="unicode"))
    return BaseRender(
        inner_svg="".join(parts), view_box=(vb[2], vb[3]),
        cjk_font=cjk_font, text_classes=text_classes,
    )


def cable_classes(base: BaseRender) -> Tuple[list, int]:
    """ケーブル番号を含む文字の CSS クラス `[[クラス名, 'fill'|'stroke'], ...]` と、その文字の数を返す。
    ケーブル番号を含むとは、その文字1件のテキストに `find_cables()` が1件以上返ること。"""
    classes, n = [], 0
    for text, cls in base.text_classes:
        if find_cables([text]):
            n += 1
            classes.extend(cls)
    return classes, n


def build_viewer_data(base: BaseRender, fname: str) -> dict:
    """viewer 部品に渡す data（JSON 化できる dict）を作る。

    items: 赤にする文字の CSS クラス／count: ケーブル番号を含む文字の数／color: 赤の色。"""
    items, count = cable_classes(base)
    return {
        "fname": fname, "viewBox": [base.view_box[0], base.view_box[1]],
        "svg": base.inner_svg, "sig": f"{fname}:{len(base.inner_svg)}:{hash(base.inner_svg) & 0xffffffff}",
        "empty": base.empty, "hasFont": base.cjk_font is not None,
        "color": CABLE_COLOR, "items": items, "count": count,
    }
