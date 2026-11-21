"""DXF の図面を SVG にし、文字と図形を色で強調する viewer のデータ（モデル層。streamlit 非依存）。

DXF-extract-labels の `model/region_preview.py`（領域確認 viewer の描画）から、図面の描画に必要な部分だけを
移植した独立の派生（共有ファイルではない。同期しない）。領域検出・ラベル位置の突き合わせは持たない。

仕組み: ブロック内の文字だけをモデル空間へ移し、文字1件ごとに一意の「目印の色」を付けて描く。ezdxf は色ごとに
CSS クラスを作るので、そのクラスを文字の正規化テキストと対応づけて控え（`BaseRender.text_classes`）、SVG の
中身は白に書き換える。viewer は、ケーブル番号を含む文字（`find_cables()` が1件以上返すもの）のクラスの
色だけを CSS で赤に差し替える（描き直さない）。赤くなるのは**その文字全体**（番号の部分だけではない）。

ケーブルを選んだとき（`build_viewer_data` に marks・shapes を渡す）は、照合の記号の色（`SYMBOL_TEXT_COLORS`）で塗る:
文字は基準点（`drawing_geometry.text_anchor`）でマークに当てて色を変え（`apply_marks`）、図形（芯線・記号・端末・製作図の接続線）は
DXF 座標を viewBox 座標に写して（`to_svg`）重ね書きの SVG パスにする（`shape_paths`）。詳細は docs/VIEWER.md。
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
from .drawing_geometry import text_anchor
from .text_utils import underscores_to_spaces
from .extract_labels import extract_text_from_entity
from .symbols import BY_SEVERITY, LABELS, LEGEND_ORDER, RELATED, VIEWER_COLORS, rank

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
BASE_RENDER_VERSION = 3

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
    # 文字1件ごと: {'text': 正規化した文字列, 'classes': [[クラス名, 'fill'|'stroke'], ...], 'anchor': 基準点（text_anchor）}
    texts: List[dict] = field(default_factory=list)
    # DXF 座標 → viewBox 座標: svg_x = (x - ext_min_x)*scale, svg_y = (ext_max_y - y)*scale（`to_svg`）
    ext_min_x: float = 0.0
    ext_max_y: float = 0.0
    scale: float = 1.0


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


def _assign_marker_colors(doc, marker_to_anchor=None) -> Dict[str, Optional[str]]:
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
        if marker_to_anchor is not None:
            marker_to_anchor["#%02x%02x%02x" % rgb] = text_anchor(e)
    return marker_to_text


def _rewrite_marker_styles(root, marker_to_text, marker_to_anchor=None, texts_out=None) -> List[Tuple[str, list]]:
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
    if texts_out is not None:
        for mk, cls in per_marker.items():
            if marker_to_text[mk]:
                texts_out.append({'text': marker_to_text[mk], 'classes': cls, 'anchor': (marker_to_anchor or {}).get(mk)})
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
    marker_to_anchor: Dict[str, tuple] = {}
    marker_to_text = _assign_marker_colors(doc, marker_to_anchor)

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

    ext = backend.player().bbox()      # get_xml_root_element の後は SVG 座標になるので、先に DXF 座標の範囲を控える
    width = ext.extmax.x - ext.extmin.x
    root = backend.get_xml_root_element(layout.Page(0, 0, layout.Units.mm))
    texts: List[dict] = []
    text_classes = _rewrite_marker_styles(root, marker_to_text, marker_to_anchor, texts)
    vb = [float(v) for v in root.get("viewBox", "0 0 0 0").split()]
    parts: List[str] = []
    for child in root:
        if child.tag.split("}")[-1] in ("defs", "g"):
            parts.append(ET.tostring(child, encoding="unicode"))
    return BaseRender(
        inner_svg="".join(parts), view_box=(vb[2], vb[3]),
        cjk_font=cjk_font, text_classes=text_classes, texts=texts,
        ext_min_x=ext.extmin.x, ext_max_y=ext.extmax.y, scale=(vb[2] / width if width else 1.0),
    )


def to_svg(base: BaseRender, x: float, y: float) -> Tuple[float, float]:
    """DXF 座標を viewBox 座標にする（viewer の重ね書き用）。"""
    return ((x - base.ext_min_x) * base.scale, (base.ext_max_y - y) * base.scale)


def cable_classes(base: BaseRender) -> Tuple[list, int]:
    """ケーブル番号を含む文字の CSS クラス `[[クラス名, 'fill'|'stroke'], ...]` と、その文字の数を返す。
    ケーブル番号を含むとは、その文字1件のテキストに `find_cables()` が1件以上返ること。"""
    classes, n = [], 0
    for text, cls in base.text_classes:
        if find_cables([text]):
            n += 1
            classes.extend(cls)
    return classes, n


# 照合の記号ごとの文字の色（Excel のセル色と対応: ○緑・△黄・×赤・？青。－ と「関連（照合に使っていない）」は紫）
SYMBOL_TEXT_COLORS = VIEWER_COLORS          # 記号 → 文字・図形の色（定義は symbols.py）


def _squash(s: str) -> str:
    """文字の突き合わせ用（NFKC・単語をつなぐ `_` を空白にしてから空白除去・大文字）。読み取り側の `norm()` と同じ規則にそろえる。"""
    return re.sub(r"\s+", "", underscores_to_spaces(unicodedata.normalize('NFKC', s or ''))).upper()


def apply_marks(base: BaseRender, marks) -> Tuple[list, dict, int]:
    """マーク（[{'anchor': (x, y), 'text': 文字, 'symbol': 記号|'関連'}]）を、描画した文字のクラスに対応づける。

    突き合わせは基準点（`text_anchor`）。同じ基準点に文字が複数あるときだけ文字列（空白除去・大文字）で絞る。
    Returns: (クラスと色 [[クラス名, 'fill'|'stroke', 色], ...], 記号ごとの文字の件数, 突き合わせできなかったマークの数)
    同じ文字に複数の記号が付いたら重いほう。"""
    by_anchor: Dict[tuple, list] = {}
    for t in base.texts:
        if t['anchor'] is not None:
            by_anchor.setdefault(tuple(t['anchor']), []).append(t)
    best: Dict[int, str] = {}
    missing = 0
    for m in marks:
        if m['symbol'] == RELATED:   # 「関連」は照合の範囲を示す内部の区分で、Excel に無いので viewer には出さない
            continue
        cands = by_anchor.get(tuple(round(v, 3) for v in m['anchor']), [])
        if len(cands) > 1:
            want = _squash(m['text'])
            exact = [t for t in cands if _squash(t['text']) == want or want in _squash(t['text'])]
            cands = exact or cands
        if not cands:
            missing += 1
            continue
        for t in cands:
            cur = best.get(id(t))
            if cur is None or rank(m['symbol']) < rank(cur):
                best[id(t)] = m['symbol']
    items, counts = [], {}
    for t in base.texts:
        sym = best.get(id(t))
        if sym is None:
            continue
        counts[sym] = counts.get(sym, 0) + 1
        items.extend([c[0], c[1], SYMBOL_TEXT_COLORS[sym]] for c in t['classes'])
    return items, counts, missing


def _f(v: float) -> str:
    return f"{v:.1f}"


def shape_paths(base: BaseRender, shapes) -> List[dict]:
    """図形（`consistency` の行の shapes。DXF 座標）を、viewer の重ね書き用の SVG パス（viewBox 座標）にする。

    shapes: [{'kind': 'lines'|'ellipse'|'capsule'|'rect'|'circle', 'geom', 'symbol'}]。
    Returns: 記号ごとに1つにまとめた [{'d': パス, 'color': 色, 'symbol': 記号, 'count': 図形の数}]（重い記号ほど後＝上に描く: － → ○ → △ → ？ → ×）。"""
    by_symbol: Dict[str, list] = {}
    for sh in shapes:
        kind, geom = sh['kind'], sh['geom']
        if kind == 'lines':
            d = ' '.join('M {} {} L {} {}'.format(*(_f(v) for p in seg for v in to_svg(base, *p))) for seg in geom)
        elif kind == 'circle':
            (cx, cy), r = geom
            sx, sy = to_svg(base, cx, cy)
            r *= base.scale
            d = f"M {_f(sx - r)} {_f(sy)} A {_f(r)} {_f(r)} 0 1 0 {_f(sx + r)} {_f(sy)} A {_f(r)} {_f(r)} 0 1 0 {_f(sx - r)} {_f(sy)} Z"
        else:
            (ax, ay), (bx, by) = to_svg(base, geom[0], geom[1]), to_svg(base, geom[2], geom[3])
            x0, x1, y0, y1 = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
            w, h = x1 - x0, y1 - y0
            if kind == 'ellipse':
                rx, ry, cy = w / 2, h / 2, (y0 + y1) / 2
                d = f"M {_f(x0)} {_f(cy)} A {_f(rx)} {_f(ry)} 0 1 0 {_f(x1)} {_f(cy)} A {_f(rx)} {_f(ry)} 0 1 0 {_f(x0)} {_f(cy)} Z"
            elif kind == 'capsule':
                r = min(w, h) / 2
                d = (f"M {_f(x0 + r)} {_f(y0)} L {_f(x1 - r)} {_f(y0)} A {_f(r)} {_f(r)} 0 0 1 {_f(x1)} {_f(y0 + r)} L {_f(x1)} {_f(y1 - r)} "
                     f"A {_f(r)} {_f(r)} 0 0 1 {_f(x1 - r)} {_f(y1)} L {_f(x0 + r)} {_f(y1)} A {_f(r)} {_f(r)} 0 0 1 {_f(x0)} {_f(y1 - r)} "
                     f"L {_f(x0)} {_f(y0 + r)} A {_f(r)} {_f(r)} 0 0 1 {_f(x0 + r)} {_f(y0)} Z")
            else:
                d = f"M {_f(x0)} {_f(y0)} L {_f(x1)} {_f(y0)} L {_f(x1)} {_f(y1)} L {_f(x0)} {_f(y1)} Z"
        by_symbol.setdefault(sh['symbol'], []).append(d)
    order = BY_SEVERITY[::-1]                  # 軽い記号から先に描く（重い記号が上に重なる）
    return [{'d': ' '.join(by_symbol[sym]), 'color': SYMBOL_TEXT_COLORS[sym], 'symbol': sym, 'count': len(by_symbol[sym])}
            for sym in order if sym in by_symbol]


def build_viewer_data(base: BaseRender, fname: str, marks=None, title=None, shapes=None) -> dict:
    """viewer 部品に渡す data（JSON 化できる dict）を作る。

    marks が None: ケーブル番号を含む文字を赤にする（選んだケーブルが無いとき）。
    marks あり: マークの記号の色で文字を塗る（`apply_marks`。「関連」のマークは表示しない）。他の文字は白。
    items: 色を変える文字の CSS クラス [[クラス名, 'fill'|'stroke', 色], ...]／legend: 凡例 [[ラベル, 色, 件数], ...]（○ △ × ？ － の順。左枠は件数を出さない）／shapeLegend: 図形の凡例 [[記号, 色], ...]（同じ順）／
    count: 色を変えた文字の数／missing: 突き合わせできなかったマークの数／counts: 記号ごとの件数（見出しに使う）。"""
    if marks is None:
        classes, count = cable_classes(base)
        items = [[c[0], c[1], CABLE_COLOR] for c in classes]
        legend = [['ケーブル番号', CABLE_COLOR, count]]
        counts, missing = {}, 0
    else:
        items, counts, missing = apply_marks(base, marks)
        count = sum(counts.values())
        legend = [[LABELS[sym], SYMBOL_TEXT_COLORS[sym], counts[sym]] for sym in LEGEND_ORDER if sym in counts]
    data = {
        "fname": fname, "title": title or fname, "viewBox": [base.view_box[0], base.view_box[1]],
        "svg": base.inner_svg, "sig": f"{fname}:{len(base.inner_svg)}:{hash(base.inner_svg) & 0xffffffff}",
        "empty": base.empty, "hasFont": base.cjk_font is not None,
        "color": CABLE_COLOR, "items": items, "legend": legend, "count": count, "counts": counts, "missing": missing,
        "marked": marks is not None, "shapes": shape_paths(base, shapes or []),
    }
    # 左枠（凡例）の図形の行は、描画の順（軽い記号から）ではなく、凡例の順（○ △ × ？ －）で出す
    data["shapeLegend"] = [[s["symbol"], s["color"]] for s in sorted(data["shapes"], key=lambda s: LEGEND_ORDER.index(s["symbol"]))]
    return data
