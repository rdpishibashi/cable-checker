"""DXF を1回走査して、表示されている文字・線分・円弧の端点を集める（streamlit 非依存）。

ケーブル一覧表・線番設定表・ケーブル製作図の3つの読み取り（cable_list_table.py・wire_number_table.py・
fab_drawing.py）が共用する。実現性検討の試作（docs/feasibility/）の `collect`・`_collect_segments`・
`_arc_ends` を1回の走査にまとめたもの。

非表示のものは集めない: `invisible` 属性、off／frozen の画層、非表示の INSERT の中身
（ULVAC の改訂運用は古い文字を消さずに隠す。例: 線番設定表の古い注記 `EE****-503-47`）。
図面枠（JZB_*）と改訂記号（SX_DeltaSymbol*）の INSERT は中を見ない。
"""
import unicodedata
from dataclasses import dataclass, replace

import ezdxf

from .text_utils import underscores_to_spaces

TOL = 0.3
_SKIP_BLOCKS = ('SX_DeltaSymbol', 'JZB')


@dataclass
class Txt:
    s: str
    x0: float
    x1: float
    y0: float
    y1: float
    src: str            # 'msp' またはブロック名
    struck: bool = False
    ref: tuple = None   # 文字の基準点（`text_anchor()`。viewer の文字との突き合わせに使う）

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


@dataclass
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    src: str
    p: tuple = None     # 向き付きの両端 ((x, y), (x, y))。表の罫線を合成した線分には無い


@dataclass
class Geometry:
    txts: list
    lines: list         # LINE だけ（一覧表・線番設定表が使う）
    polys: list         # LWPOLYLINE／POLYLINE の辺（製作図が LINE と合わせて電線にする）
    arc_ends: list      # ARC・ELLIPSE の両端点
    error: str = ''

    def copy_txts(self):
        """文字のコピー。読み取りごとに `struck`（削除済み）を別々に書き込むため、共有しない。"""
        return [replace(t) for t in self.txts]


def text_anchor(e):
    """文字（TEXT/MTEXT/ATTRIB）の基準点のワールド座標を、小数3桁に丸めて返す。

    読み取り（INSERT を `virtual_entities()` で展開）と viewer（同じ展開で文字をモデル空間へ移す）で同じ値になるので、
    文字幅の見積もりに頼らずに同じ文字を指せる。TEXT/ATTRIB は位置合わせがあれば `align_point`、無ければ `insert`。"""
    if e.dxftype() in ('TEXT', 'ATTRIB'):
        ha, va = e.dxf.get('halign', 0), e.dxf.get('valign', 0)
        pt = e.dxf.align_point if (ha or va) and e.dxf.hasattr('align_point') else e.dxf.insert
    else:
        pt = e.dxf.insert
    return (round(pt.x, 3), round(pt.y, 3))


def norm(s):
    """全角→半角（NFKC）。単語をつなぐアンダースコアは空白にする。字間を空けた表記（'T B'、'R 1 4 - 5'）は空白を詰める。

    空白で区切った語が2語以上ですべて1文字のときだけ詰める（'DC PS3' や 'D-sub 25P' は詰めない）。
    """
    s = underscores_to_spaces(unicodedata.normalize('NFKC', s)).strip()
    lines = []
    for line in s.split('\n'):
        toks = line.split()
        lines.append(''.join(toks) if len(toks) >= 2 and all(len(t) == 1 for t in toks) else line.strip())
    return '\n'.join(lines)


def cluster(vals, tol=0.3):
    out = []
    for v in sorted(vals):
        if out and abs(v - out[-1]) <= tol:
            continue
        out.append(v)
    return out


def is_h(s):
    return abs(s.y1 - s.y0) < 0.01 and s.x1 - s.x0 > 0.01


def is_v(s):
    return abs(s.x1 - s.x0) < 0.01 and s.y1 - s.y0 > 0.01


def crosses_text_middle(h, txts):
    """横線が文字の高さの中ほど（25〜75%）を横切り、文字と横に重なる → 削除線。該当する文字を返す。"""
    hit = []
    for t in txts:
        hgt = t.y1 - t.y0
        if t.y0 + hgt * 0.25 <= h.y0 <= t.y0 + hgt * 0.75:
            ov = min(h.x1, t.x1) - max(h.x0, t.x0)
            if ov > 0.5 * min(h.x1 - h.x0, t.x1 - t.x0):
                hit.append(t)
    return hit


def merge_segments(segs, horizontal, tol=0.3):
    """同じ高さ（縦線なら同じ x）で接する・重なる線分をつなぐ。"""
    out = []
    key = (lambda s: (round(s.y0, 1), s.x0)) if horizontal else (lambda s: (round(s.x0, 1), s.y0))
    for s in sorted(segs, key=key):
        if horizontal:
            if out and abs(out[-1].y0 - s.y0) < 0.05 and s.x0 <= out[-1].x1 + tol:
                out[-1].x1 = max(out[-1].x1, s.x1)
                continue
        else:
            if out and abs(out[-1].x0 - s.x0) < 0.05 and s.y0 <= out[-1].y1 + tol:
                out[-1].y1 = max(out[-1].y1, s.y1)
                continue
        out.append(Seg(s.x0, s.y0, s.x1, s.y1, s.src))
    return out


def _text_box(e):
    t = e.dxftype()
    if t in ('TEXT', 'ATTRIB'):
        s = e.dxf.text
        h = e.dxf.height
        wf = e.dxf.get('width', 1.0)
        # 全角は h、半角は 0.6h 程度で概算
        w = sum(h if unicodedata.east_asian_width(c) in 'WF' else 0.6 * h for c in s) * wf
        ha, va = e.dxf.get('halign', 0), e.dxf.get('valign', 0)
        ref = e.dxf.align_point if (ha or va) and e.dxf.hasattr('align_point') else e.dxf.insert
        x, y = ref.x, ref.y
        if ha in (1, 4):
            x -= w / 2
        elif ha == 2:
            x -= w
        if va == 2 or ha == 4:
            y -= h / 2
        elif va == 3:
            y -= h
        return s, x, x + w, y, y + h
    s = e.plain_text()
    h = e.dxf.char_height
    lines = s.split('\n')
    w = e.dxf.get('width', 0) or max(len(ln) for ln in lines) * h * 0.8
    hh = h * (1 + 1.6 * (len(lines) - 1))
    x, y = e.dxf.insert.x, e.dxf.insert.y
    ap = e.dxf.get('attachment_point', 1)
    col, row = (ap - 1) % 3, (ap - 1) // 3
    x0 = x - w * col / 2
    y1 = y + hh * row / 2
    return s, x0, x0 + w, y1 - hh, y1


def hidden_layers(doc):
    """off（色番号が負）または frozen の画層名。"""
    return {layer.dxf.name for layer in doc.layers if not layer.is_on() or layer.is_frozen()}


def collect(msp, hidden=frozenset()):
    """文字・LINE・ポリライン辺・弧の端点を1回の走査で集める。"""
    txts, lines, polys, arcs = [], [], [], []

    def is_hidden(e):
        return bool(e.dxf.get('invisible', 0)) or e.dxf.get('layer', '0') in hidden

    def walk(ents, src, depth):
        for e in ents:
            if is_hidden(e):
                continue
            t = e.dxftype()
            if t in ('TEXT', 'MTEXT', 'ATTRIB'):
                s, x0, x1, y0, y1 = _text_box(e)
                if s.strip():
                    txts.append(Txt(s, x0, x1, y0, y1, src, ref=text_anchor(e)))
            elif t == 'LINE':
                a, b = e.dxf.start, e.dxf.end
                lines.append(Seg(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y), src,
                                 ((a.x, a.y), (b.x, b.y))))
            elif t in ('LWPOLYLINE', 'POLYLINE'):
                if t == 'LWPOLYLINE':
                    pts = [(p[0], p[1]) for p in e.get_points('xy')]
                    closed = e.closed
                else:
                    pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                    closed = e.is_closed
                if closed and pts:
                    pts.append(pts[0])
                for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                    polys.append(Seg(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), 'poly',
                                     ((x0, y0), (x1, y1))))
            elif t in ('ARC', 'ELLIPSE'):   # シールド・より線の長円記号（599-40A は ELLIPSE）
                arcs.append((e.start_point.x, e.start_point.y))
                arcs.append((e.end_point.x, e.end_point.y))
            elif t == 'INSERT' and e.dxf.name.startswith(_SKIP_BLOCKS):
                continue   # 改訂記号（△B 等）と図面枠（JZB_）はセルの値でも電線でもない
            elif t == 'INSERT' and depth < 6:
                for a in e.attribs:
                    walk([a], e.dxf.name, depth + 1)
                try:
                    walk(list(e.virtual_entities()), e.dxf.name, depth + 1)
                except Exception:
                    pass
    walk(msp, 'msp', 0)
    return Geometry(txts, lines, polys, arcs)


def load(path):
    """DXF を読んで Geometry を返す。読めないとき・文字も線も無いときは `error` に理由を入れる。"""
    try:
        doc = ezdxf.readfile(path)
    except Exception as e:  # ezdxf は形式不正で IOError／DXFStructureError 等を出す
        return Geometry([], [], [], [], error=f'読み込めません: {e}')
    return collect(doc.modelspace(), hidden_layers(doc))
