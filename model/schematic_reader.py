"""展開接続図（405）のケーブル記号の読み取り（streamlit 非依存）。

実現性検討（docs/feasibility/schematic_405/）の試作 `schematic.py` の移植。規則・閾値は試作のまま。

手順:
  1. ケーブル番号（`W No.M65206` または番号だけの文字）→ 引出線（線の矢印・ポリラインの矢印・LEADER）→ 矢印の先
  2. 先端のケーブル記号（楕円・長円・円筒の2つの楕円・1本の線）→ 記号を横切る芯線
  3. 芯線を端点でたどり、両端のコネクタ枠（ピン番号・コネクタ名）・端子の円・接地記号を読む
  4. 端ごとに、ユニットの根拠を集める: 端末の位置を含む領域の名称候補（DXF-extract-labels の領域抽出）、
     コネクタの先の配線をたどった機器の箱の文字、機器符号のラベルの近くの文字

`drawing_geometry.collect()`（393 用）は使わない: 405 では `JZB_*` が図面枠ではなく普通のブロック
（ケーブル番号と引出線が入っている）で、文字の回転（縦書き）も要るため、この `collect()` が別に集める。
一覧表との突き合わせ（ユニット名の判定）は `consistency.py` が行う。このモジュールは図面から読んだ事実だけを返す。
"""
import math
import re
import unicodedata
from dataclasses import dataclass, field

import ezdxf
import ezdxf.path

from .cable_extractor import CABLE_BODY, extract_cables
from .drawing_geometry import norm, text_anchor
from .label_classifier import REGION_NAME, classify_label
from .region_detector import DEFAULT_REGION_CONFIG, _point_in_polygon, analyze_dxf_regions

WNO = re.compile(r'(?<![0-9A-Z])W\s*NO\.?\s*(' + CABLE_BODY + r')(?![0-9A-Z])', re.I)
BARE = re.compile(CABLE_BODY)
EPS = 0.05
NEAR_LABEL_DIST = 50.0     # 機器符号のラベルから「近傍」とみなす距離（試作の決定: 30 では足りず、80 は遠すぎる）


@dataclass
class T:
    s: str
    x0: float
    y0: float
    x1: float
    y1: float
    rot: float          # 度（0〜360）
    h: float
    src: str
    color: int = 0
    ref: tuple = None   # 文字の基準点（drawing_geometry.text_anchor）

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    @property
    def vertical(self):
        r = self.rot % 180
        return 45 < r < 135


@dataclass
class S:
    a: tuple
    b: tuple
    color: int
    layer: str
    src: str
    linetype: str = ''

    @property
    def length(self):
        return math.dist(self.a, self.b)

    def dir(self):
        L = self.length or 1
        return ((self.b[0] - self.a[0]) / L, (self.b[1] - self.a[1]) / L)


@dataclass
class Curve:
    """ARC・ELLIPSE・CIRCLE。点列で近似して交点を取る。"""
    kind: str
    center: tuple
    pts: list            # 近似点列
    closed: bool
    color: int
    layer: str
    src: str
    size: tuple = (0, 0)  # 外接矩形の幅・高さ


@dataclass
class Geo:
    txts: list = field(default_factory=list)
    segs: list = field(default_factory=list)
    curves: list = field(default_factory=list)
    leaders: list = field(default_factory=list)   # [(頂点列, src)]
    error: str = ''


# ---------------------------------------------------------------- 収集

def _text_box(e):
    t = e.dxftype()
    if t in ('TEXT', 'ATTRIB'):
        s = e.dxf.text
        h = e.dxf.height
        wf = e.dxf.get('width', 1.0)
        w = sum(h if unicodedata.east_asian_width(c) in 'WF' else 0.6 * h for c in s) * wf
        ha, va = e.dxf.get('halign', 0), e.dxf.get('valign', 0)
        ref = e.dxf.align_point if (ha or va) and e.dxf.hasattr('align_point') else e.dxf.insert
        dx = {1: -w / 2, 4: -w / 2, 2: -w}.get(ha, 0)
        dy = -h / 2 if (va == 2 or ha == 4) else (-h if va == 3 else 0)
        rot = e.dxf.get('rotation', 0)
        local = (dx, dy, dx + w, dy + h)
        return s, (ref.x, ref.y), local, rot, h
    s = e.plain_text()
    h = e.dxf.char_height
    lines = s.split('\n')
    w = max(sum(h if unicodedata.east_asian_width(c) in 'WF' else 0.7 * h for c in ln) for ln in lines) if lines else 0
    w = min(w, e.dxf.get('width', 0) or w)
    hh = h * (1 + 1.6 * (len(lines) - 1))
    ap = e.dxf.get('attachment_point', 1)
    col, row = (ap - 1) % 3, (ap - 1) // 3
    x0 = -w * col / 2
    y1 = hh * row / 2
    try:
        rot = e.get_rotation()
    except Exception:
        rot = e.dxf.get('rotation', 0)
    return s, (e.dxf.insert.x, e.dxf.insert.y), (x0, y1 - hh, x0 + w, y1), rot, h


def _rot_box(ref, local, rot):
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    xs, ys = [], []
    for lx, ly in ((local[0], local[1]), (local[2], local[1]), (local[0], local[3]), (local[2], local[3])):
        xs.append(ref[0] + lx * c - ly * s)
        ys.append(ref[1] + lx * s + ly * c)
    return min(xs), min(ys), max(xs), max(ys)


def _hidden_layers(doc):
    return {la.dxf.name for la in doc.layers if not la.is_on() or la.is_frozen()}


def collect(doc):
    msp = doc.modelspace()
    hidden = _hidden_layers(doc)
    g = Geo()

    def is_hidden(e):
        return bool(e.dxf.get('invisible', 0)) or e.dxf.get('layer', '0') in hidden

    def color(e):
        c = e.dxf.get('color', 256)
        if c in (0, 256):
            try:
                c = doc.layers.get(e.dxf.layer).color
            except Exception:
                c = 7
        return c

    def walk(ents, src, depth):
        for e in ents:
            if is_hidden(e):
                continue
            t = e.dxftype()
            lay = e.dxf.get('layer', '0')
            if t in ('TEXT', 'MTEXT', 'ATTRIB'):
                s, ref, local, rot, h = _text_box(e)
                if s.strip():
                    x0, y0, x1, y1 = _rot_box(ref, local, rot)
                    g.txts.append(T(norm(s), x0, y0, x1, y1, rot % 360, h, src, color(e), text_anchor(e)))
            elif t == 'LINE':
                a, b = e.dxf.start, e.dxf.end
                if math.dist((a.x, a.y), (b.x, b.y)) > EPS:
                    g.segs.append(S((a.x, a.y), (b.x, b.y), color(e), lay, src, e.dxf.get('linetype', '')))
            elif t in ('LWPOLYLINE', 'POLYLINE'):
                pts = [(v.x, v.y) for v in ezdxf.path.make_path(e).flattening(0.05)]
                for p, q in zip(pts, pts[1:]):
                    if math.dist(p, q) > EPS:
                        g.segs.append(S(p, q, color(e), lay, 'poly:' + src))
            elif t in ('ARC', 'ELLIPSE', 'CIRCLE'):
                try:
                    pts = [(v.x, v.y) for v in e.flattening(0.02)]
                except Exception:
                    continue
                closed = t == 'CIRCLE' or (t == 'ELLIPSE' and abs((e.dxf.end_param - e.dxf.start_param) % (2 * math.pi)) < 1e-6)
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                c = e.dxf.center
                g.curves.append(Curve(t, (c.x, c.y), pts, closed, color(e), lay, src,
                                      (max(xs) - min(xs), max(ys) - min(ys))))
            elif t == 'LEADER':
                g.leaders.append(([(v[0], v[1]) for v in e.vertices], src))
            elif t == 'INSERT' and depth < 8:
                for a in e.attribs:
                    walk([a], e.dxf.name, depth + 1)
                try:
                    walk(list(e.virtual_entities()), e.dxf.name, depth + 1)
                except Exception:
                    pass
    walk(msp, 'msp', 0)
    # 同じ位置に重なった同一の線分（2重に描かれた線。602-01E のヒーターの配線など）は1本にする。
    # 残すと、たどる途中で「分岐」と見て止まる
    seen, uniq = set(), []
    for sg in g.segs:
        key = tuple(sorted(((round(sg.a[0], 2), round(sg.a[1], 2)), (round(sg.b[0], 2), round(sg.b[1], 2)))))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(sg)
    g.segs = uniq
    return g


def load(path):
    try:
        doc = ezdxf.readfile(path)
    except Exception as e:
        return Geo(error=str(e)), None
    return collect(doc), doc


# ---------------------------------------------------------------- 幾何の補助

def pt_seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.dist(p, a)
    t = max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def box_seg_dist(t, s):
    """文字の外接矩形と線分の距離（0 なら重なる）。"""
    corners = [(t.x0, t.y0), (t.x1, t.y0), (t.x0, t.y1), (t.x1, t.y1)]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    # 線分の端が矩形の中
    for p in (s.a, s.b):
        if t.x0 <= p[0] <= t.x1 and t.y0 <= p[1] <= t.y1:
            return 0.0
    d = min(pt_seg_dist(c, s.a, s.b) for c in corners)
    d = min(d, pt_seg_dist(s.a, *_box_edge_near(t, s.a)), pt_seg_dist(s.b, *_box_edge_near(t, s.b)))
    return d


def _box_edge_near(t, p):
    # 点 p に最も近い矩形の辺
    edges = [((t.x0, t.y0), (t.x1, t.y0)), ((t.x1, t.y0), (t.x1, t.y1)),
             ((t.x1, t.y1), (t.x0, t.y1)), ((t.x0, t.y1), (t.x0, t.y0))]
    return min(edges, key=lambda e: pt_seg_dist(p, *e))


def angle_between(u, v):
    c = abs(u[0] * v[0] + u[1] * v[1])
    return math.degrees(math.acos(max(-1, min(1, c))))


class Grid:
    def __init__(self, segs, cell=5.0):
        self.cell = cell
        self.g = {}
        for i, s in enumerate(segs):
            x0, x1 = sorted((s.a[0], s.b[0]))
            y0, y1 = sorted((s.a[1], s.b[1]))
            for gx in range(int(x0 // cell), int(x1 // cell) + 1):
                for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                    self.g.setdefault((gx, gy), []).append(i)

    def near(self, p, r=0.5):
        out = set()
        c = self.cell
        for gx in range(int((p[0] - r) // c), int((p[0] + r) // c) + 1):
            for gy in range(int((p[1] - r) // c), int((p[1] + r) // c) + 1):
                out.update(self.g.get((gx, gy), ()))
        return out


# ---------------------------------------------------------------- ケーブル番号

def _text_below(g, t):
    """ラベル t のすぐ下（隙間 0〜文字高さの 1.5 倍、左端の差 3 以内、横に重なる）にある、同じブロックの横書きの文字。ケーブル番号を含む文字は除く。"""
    h = t.h or 2.5
    best = None
    for o in g.txts:
        if o is t or o.vertical or o.src != t.src:
            continue
        gap = t.y0 - o.y1
        if -0.2 <= gap <= 1.5 * h and o.x0 < t.x1 and o.x1 > t.x0 and abs(o.x0 - t.x0) <= 3:
            first = o.s.split('\n')[0].strip()
            if WNO.search(o.s) or BARE.fullmatch(first):
                continue
            if best is None or gap < best[0]:
                best = (gap, o)
    return best[1] if best else None


def cable_labels(g):
    """ケーブル番号のラベル。備考は、番号の後ろの文字と、ラベルのすぐ下の文字（`ACCESSORY CABLE` など。別の文字で描かれる）。"""
    out = []
    for t in g.txts:
        first = t.s.split('\n')[0]
        m = WNO.search(t.s)
        if m:
            rest = t.s[m.end():].strip()
            out.append({'cable': m.group(1).upper(), 'txt': t, 'remark': rest})
        elif BARE.fullmatch(first.strip()):
            out.append({'cable': first.strip().upper(), 'txt': t, 'remark': '', 'bare': True})
    for lab in out:
        t = lab['txt']
        if t.vertical:
            continue
        below = _text_below(g, t)
        if below is not None:
            extra = below.s.strip()
            if extra and extra not in lab['remark']:
                lab['remark'] = f"{lab['remark']}\n{extra}".strip() if lab['remark'] else extra
                lab['remark_txt'] = below      # 備考にした別の文字（viewer で強調する）
    return out


# ---------------------------------------------------------------- 矢印と引出線

def find_arrows(g, grid):
    """矢印 = 1点で会う短い2本（1〜4.5、なす角 10〜50°）と、その点で終わる軸の線。

    Returns: [{'tip': (x, y), 'dir': (dx, dy), 'shaft': seg_index, 'heads': (i, j)}]
    """
    segs = g.segs
    ends = {}
    for i, s in enumerate(segs):
        if 0.8 <= s.length <= 4.5:
            for p, q in ((s.a, s.b), (s.b, s.a)):
                ends.setdefault((round(p[0], 1), round(p[1], 1)), []).append((i, q))
    arrows = []
    for key, lst in ends.items():
        if len(lst) < 2:
            continue
        tip = key
        for ii in range(len(lst)):
            for jj in range(ii + 1, len(lst)):
                i, qi = lst[ii]
                j, qj = lst[jj]
                ui = (qi[0] - tip[0], qi[1] - tip[1])
                uj = (qj[0] - tip[0], qj[1] - tip[1])
                li, lj = math.hypot(*ui), math.hypot(*uj)
                if abs(li - lj) > 0.3 * max(li, lj):
                    continue
                cosang = (ui[0] * uj[0] + ui[1] * uj[1]) / (li * lj)
                ang = math.degrees(math.acos(max(-1, min(1, cosang))))
                if not 10 <= ang <= 50:
                    continue
                bis = ((ui[0] / li + uj[0] / lj), (ui[1] / li + uj[1] / lj))
                bl = math.hypot(*bis)
                bis = (bis[0] / bl, bis[1] / bl)       # 先端から後ろ向き
                # 軸: 先端に端があり、後ろ向きに伸びる線（矢じりの2本以外）
                shaft = None
                for k in grid.near(tip, 0.3):
                    if k in (i, j):
                        continue
                    s = segs[k]
                    for p, q in ((s.a, s.b), (s.b, s.a)):
                        if math.dist(p, tip) < 0.15:
                            v = (q[0] - p[0], q[1] - p[1])
                            lv = math.hypot(*v)
                            if lv > 0 and (v[0] * bis[0] + v[1] * bis[1]) / lv > 0.97:
                                shaft = k
                if shaft is not None:
                    arrows.append({'tip': tip, 'dir': (-bis[0], -bis[1]), 'shaft': shaft, 'heads': (i, j)})
    return arrows


def leader_chain(g, grid, arrow, max_segs=8):
    """矢印の軸から端点でつながる線をたどる（分岐したら止める）。たどった線分の番号を返す。"""
    segs = g.segs
    chain = [arrow['shaft']]
    used = set(chain) | set(arrow['heads'])
    s = segs[arrow['shaft']]
    cur = s.b if math.dist(s.a, arrow['tip']) < 0.15 else s.a
    for _ in range(max_segs):
        nxt = []
        for k in grid.near(cur, 0.2):
            if k in used:
                continue
            t = segs[k]
            if math.dist(t.a, cur) < 0.1:
                nxt.append((k, t.b))
            elif math.dist(t.b, cur) < 0.1:
                nxt.append((k, t.a))
        if len(nxt) != 1:
            # 分岐（下線の両側に伸びる）の場合は両方を加えて止める
            for k, _ in nxt:
                chain.append(k)
                used.add(k)
            break
        k, cur = nxt[0]
        chain.append(k)
        used.add(k)
    return chain


def link_labels(g, labels, arrows, grid):
    """各ケーブル番号に、引出線でつながる矢印を割り当てる。LEADER エンティティも使う。"""
    chains = []
    for a in arrows:
        chains.append((a, leader_chain(g, grid, a)))
    for lab in labels:
        t = lab['txt']
        best = None
        for a, ch in chains:
            d = min(box_seg_dist(t, g.segs[k]) for k in ch)
            if d < 2.6 and (best is None or d < best[0]):
                best = (d, a, ch)
        for verts, src in g.leaders:
            if len(verts) < 2:
                continue
            d = min(box_seg_dist(t, S(p, q, 0, '', '')) for p, q in zip(verts, verts[1:]))
            if d < 3.5 and (best is None or d < best[0]):
                tip = verts[0]
                v = (verts[0][0] - verts[1][0], verts[0][1] - verts[1][1])
                L = math.hypot(*v) or 1
                best = (d, {'tip': tip, 'dir': (v[0] / L, v[1] / L), 'leader_entity': True, 'verts': list(verts)}, [])
        if best:
            lab['arrow'] = best[1]
            lab['chain'] = best[2]
    return labels


# ---------------------------------------------------------------- ケーブル記号と芯線

def _bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _near_curves(g, P, tol=0.4):
    return [c for c in g.curves if min(math.dist(P, q) for q in c.pts) < tol]


def _arc_mid_dir(c):
    m = c.pts[len(c.pts) // 2]
    v = (m[0] - c.center[0], m[1] - c.center[1])
    L = math.hypot(*v) or 1
    return (v[0] / L, v[1] / L)


def capsule_from_arc(g, grid, arc):
    """長円 = 向かい合う2つの円弧（同じ半径・同じ軸上）。相方の円弧を探して外接矩形を返す。"""
    m = _arc_mid_dir(arc)
    r = math.dist(arc.center, arc.pts[0])
    best = None
    for c in g.curves:
        if c is arc or c.kind != 'ARC':
            continue
        if abs(math.dist(c.center, c.pts[0]) - r) > 0.05:
            continue
        mc = _arc_mid_dir(c)
        if mc[0] * m[0] + mc[1] * m[1] > -0.95:
            continue
        v = (c.center[0] - arc.center[0], c.center[1] - arc.center[1])
        along = -(v[0] * m[0] + v[1] * m[1])          # 相方は開いた側（-m 方向）にある
        perp = abs(v[0] * m[1] - v[1] * m[0])
        if along <= 0 or perp > 0.1:
            continue
        if best is None or along < best[0]:
            best = (along, c)
    if best:
        return _bbox(list(arc.pts) + list(best[1].pts))
    return _capsule_bfs(g, grid, arc)


CAPSULE_JOIN_TOL = 0.3     # 円弧と線の端点のずれの許容（実データで 0.2 ずれる筒がある）


def _trace_outline(g, grid, color, seeds, pts, seen_c, limit=40):
    """同じ色の円弧・線を、端点（ずれ CAPSULE_JOIN_TOL 以内）でたどった点の集まり（limit 要素まで）。"""
    todo = list(seeds)
    seen_s = set()
    while todo and len(seen_c) + len(seen_s) < limit:
        p = todo.pop()
        for k in grid.near(p, CAPSULE_JOIN_TOL + 0.1):
            s = g.segs[k]
            if k in seen_s or s.color != color or is_dashed(s):
                continue
            for a, b in ((s.a, s.b), (s.b, s.a)):
                if math.dist(a, p) < CAPSULE_JOIN_TOL:
                    seen_s.add(k)
                    pts += [a, b]
                    todo.append(b)
        for c in g.curves:
            if id(c) in seen_c or c.kind != 'ARC' or c.color != color:
                continue
            for q in (c.pts[0], c.pts[-1]):
                if math.dist(q, p) < CAPSULE_JOIN_TOL:
                    seen_c.add(id(c))
                    pts += c.pts
                    todo += [c.pts[0], c.pts[-1]]
    return pts


def _capsule_bfs(g, grid, arc, limit=40):
    """角を丸めた長円（四分円＋線）: 同じ色の円弧・線を端点でたどった外接矩形（limit 要素まで）。"""
    pts = _trace_outline(g, grid, arc.color, [arc.pts[0], arc.pts[-1]], list(arc.pts), {id(arc)}, limit)
    return _bbox(pts)


CONTINUATION = re.compile(r'\*\d+')   # 筒の切れ目の続き印（`*1`）


def continued_capsules(g, grid, box, color, end_tol=4.0):
    """細長い筒（長円）の開いた端に続き印（`*1`）があるとき、同じ続き印が付いた別の筒の外接矩形を返す。

    図面が長いケーブルを途中で切って別の場所に続きを描く描き方（M65235）。続き印は筒の幅と同じ間隔の2本の線の端にある。
    Returns: [(x0, y0, x1, y1)]
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if min(w, h) <= 0 or max(w, h) < 3 * min(w, h):
        return []
    vertical = h >= w
    ends = [((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1)] if vertical else [(x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2)]
    marks = [t for t in g.txts if CONTINUATION.fullmatch(t.s.strip())
             and any(math.dist((t.cx, t.cy), e) <= end_tol for e in ends)]
    width = min(w, h)
    out = []
    for m in marks:
        for o in g.txts:
            if o is m or o.s.strip() != m.s.strip() or math.dist((o.cx, o.cy), (m.cx, m.cy)) < 1.0:
                continue
            seeds = []
            for k, s in enumerate(g.segs):
                if s.color != color or is_dashed(s):
                    continue
                d = s.dir()
                if not ((abs(d[0]) < 0.01) if vertical else (abs(d[1]) < 0.01)):      # 筒の長辺の向き（縦長なら縦線、横長なら横線）だけ
                    continue
                near = min((s.a, s.b), key=lambda p: math.dist(p, (o.cx, o.cy)))
                if math.dist(near, (o.cx, o.cy)) <= end_tol:
                    seeds.append((k, near))
            for i, (k1, p1) in enumerate(seeds):
                for k2, p2 in seeds[i + 1:]:
                    gap = abs(p1[0] - p2[0]) if vertical else abs(p1[1] - p2[1])
                    if 1.0 <= gap <= width + 0.2:      # 筒の幅（切れ目の印で外接矩形がやや広がるので、上限だけ見る）
                        pts = _trace_outline(g, grid, color, [p1, p2], [p1, p2, g.segs[k1].a, g.segs[k1].b,
                                                                         g.segs[k2].a, g.segs[k2].b], set())
                        out.append(_bbox(pts))
    return out


def _symbol_at(g, grid, P):
    """点 P にある楕円（閉じた ELLIPSE）か長円（ARC＋線）の外接矩形。"""
    for c in _near_curves(g, P):
        if c.kind == 'ELLIPSE' and c.closed:
            return _bbox(c.pts), 'ellipse', c.color
    for c in _near_curves(g, P):
        if c.kind == 'ARC':
            return capsule_from_arc(g, grid, c), 'capsule', c.color
    return None


def _collinear_run(g, grid, k, gap=6.0):
    """線分 k と同じ直線上で、隙間 gap 以内につながる線分（破線）をまとめた両端点。"""
    s = g.segs[k]
    d = s.dir()
    n = (-d[1], d[0])
    off = s.a[0] * n[0] + s.a[1] * n[1]
    proj = lambda p: p[0] * d[0] + p[1] * d[1]
    cands = []
    for j, t in enumerate(g.segs):
        if sc_angle(t.dir(), d) < 2 and abs(t.a[0] * n[0] + t.a[1] * n[1] - off) < 0.1:
            lo, hi = sorted((proj(t.a), proj(t.b)))
            cands.append((lo, hi))
    lo, hi = sorted((proj(s.a), proj(s.b)))
    changed = True
    while changed:
        changed = False
        for a, b in cands:
            if a <= hi + gap and b >= lo - gap and (a < lo or b > hi):
                lo, hi = min(lo, a), max(hi, b)
                changed = True
    pt = lambda t: (d[0] * t + n[0] * off, d[1] * t + n[1] * off)
    return pt(lo), pt(hi)


def sc_angle(u, v):
    return angle_between(u, v)


def find_symbols(g, grid, lab):
    """ケーブル番号の矢印の先から、ケーブル記号の外接矩形の一覧と種類を返す。

    Returns: (symbols, how)  symbols=[(x0,y0,x1,y1)]、how='ellipse'/'capsule'/'via-line'/'single'/'none'
    """
    a = lab.get('arrow')
    if not a:
        return nearby_symbols(g, lab)
    P = a['tip']
    hit = _symbol_at(g, grid, P)
    if hit:
        syms = [{'box': hit[0], 'color': hit[2], 'shape': hit[1]}]
        if hit[1] == 'capsule':
            syms += [{'box': b, 'color': hit[2], 'shape': 'capsule'} for b in continued_capsules(g, grid, hit[0], hit[2])]
        return syms, hit[1]
    skip = set(lab.get('chain', [])) | set(a.get('heads', ()))
    lines = [k for k in grid.near(P, 0.5) if k not in skip and pt_seg_dist(P, g.segs[k].a, g.segs[k].b) < 0.3]
    # 矢印に垂直な線（シールドの破線・円筒の側面・芯線）を優先
    lines.sort(key=lambda k: -sc_angle(g.segs[k].dir(), a['dir']))
    for k in lines:
        e0, e1 = _collinear_run(g, grid, k)
        syms = []
        for e in (e0, e1):
            for r in (0.4, 1.5):
                h = _symbol_at(g, grid, e)
                if h is None:
                    # 端の近くの楕円
                    for c in g.curves:
                        if c.kind == 'ELLIPSE' and c.closed and min(math.dist(e, q) for q in c.pts) < r:
                            h = (_bbox(c.pts), 'ellipse', c.color)
                            break
                if h:
                    syms.append({'box': h[0], 'color': h[2], 'shape': h[1]})
                    break
        if syms:
            return syms, 'via-line'
    if lines:
        return [{'wire': lines[0]}], 'single'
    return [], 'none'


def is_dashed(s):
    return 'DASH' in (s.linetype or '').upper() or 'HIDDEN' in (s.linetype or '').upper()


def wires_through(g, sym, min_out=0.3, sym_color=None):
    """外接矩形 sym の長い方向に沿う中心線を、垂直に横切る線分（芯線）の番号。

    楕円・長円の芯線は記号の短い方向に貫く。端が記号の中で止まる線（ドレイン線の点など）も、
    中心線に届いていれば採る。
    """
    x0, y0, x1, y1 = sym
    w, h = x1 - x0, y1 - y0
    out = []
    if h >= w:   # 縦長 → 横の芯線
        cx = (x0 + x1) / 2
        for k, s in enumerate(g.segs):
            if s.color == sym_color:
                continue
            if is_dashed(s) and min(abs(s.a[1] - y0), abs(s.a[1] - y1)) < 0.3:
                continue
            if abs(s.a[1] - s.b[1]) < 0.05 and y0 - 0.05 <= s.a[1] <= y1 + 0.05:
                lo, hi = sorted((s.a[0], s.b[0]))
                if lo <= cx <= hi and (lo < x0 - min_out or hi > x1 + min_out):
                    out.append(k)
    else:
        cy = (y0 + y1) / 2
        for k, s in enumerate(g.segs):
            if s.color == sym_color:
                continue
            if is_dashed(s) and min(abs(s.a[0] - x0), abs(s.a[0] - x1)) < 0.3:
                continue
            if abs(s.a[0] - s.b[0]) < 0.05 and x0 - 0.05 <= s.a[0] <= x1 + 0.05:
                lo, hi = sorted((s.a[1], s.b[1]))
                if lo <= cy <= hi and (lo < y0 - min_out or hi > y1 + min_out):
                    out.append(k)
    return out


# ---------------------------------------------------------------- 芯線の両端

def axis_rects(g, min_size=2.0, max_size=80.0):
    """軸に平行な線分4本で閉じる矩形（コネクタの枠・端子台など）。(x0, y0, x1, y1) の一覧。"""
    hs, vs = [], []
    for s in g.segs:
        if is_dashed(s):
            continue
        if abs(s.a[1] - s.b[1]) < 0.02:
            hs.append((min(s.a[0], s.b[0]), max(s.a[0], s.b[0]), s.a[1]))
        elif abs(s.a[0] - s.b[0]) < 0.02:
            vs.append((min(s.a[1], s.b[1]), max(s.a[1], s.b[1]), s.a[0]))
    vby = {}
    for lo, hi, x in vs:
        vby.setdefault(round(x, 1), []).append((lo, hi))

    def vcover(x, y0, y1):
        for key in (round(x, 1), round(x + 0.1, 1), round(x - 0.1, 1)):
            for lo, hi in vby.get(key, ()):
                if lo <= y0 + 0.05 and hi >= y1 - 0.05:
                    return True
        return False
    groups = {}
    for lo, hi, y in hs:
        if min_size <= hi - lo <= max_size:
            groups.setdefault((round(lo, 1), round(hi, 1)), []).append(y)
    rects = set()
    for (x0, x1), ys in groups.items():
        ys = sorted(set(round(y, 2) for y in ys))
        for i, ya in enumerate(ys):
            for yb in ys[i + 1:]:
                if yb - ya > max_size:
                    break
                if yb - ya >= min_size and vcover(x0, ya, yb) and vcover(x1, ya, yb):
                    rects.add((x0, ya, x1, yb))
                    break       # ya から見て最初に閉じる矩形だけ
    return sorted(rects)


NARROW_MAX = 15.0      # 細長い枠の短辺の上限
NARROW_LONG_MAX = 350.0  # 細長い枠の長辺の上限


def narrow_rects(g, min_size=2.0, short_max=NARROW_MAX, long_max=NARROW_LONG_MAX, plain_max=60.0):
    """`axis_rects` の上限（plain_max）を超える、細長い矩形のコネクタ枠。(x0, y0, x1, y1) の一覧。

    多ピンのコネクタは、幅 9 × 高さ 175 のような細長い1つの枠で描かれる（EE6888-604-01A の CNESCD・CN1）。
    短辺が short_max 以下・長辺が plain_max 超〜long_max 以下のものだけを返す。どちらの向きの枠も返すので、
    芯線との向きの関係（直交する枠だけを端末にする）は `terminal_at` が見る。"""
    out = []
    for r in axis_rects(g, min_size, long_max):
        w, h = r[2] - r[0], r[3] - r[1]
        if min(w, h) <= short_max and max(w, h) > plain_max:
            out.append(r)
    return out


def trace_wire(g, grid, k, start_pt=None, max_steps=60):
    """芯線 k を両方向に端点でつながる線分でたどり、両端点と経路を返す。分岐点で止める。

    Returns: [(端点, 止まった理由, 経路の線分番号)] × 2
    """
    s = g.segs[k]
    out = []
    for cur, other in ((s.a, s.b), (s.b, s.a)):
        path = [k]
        used = {k}
        reason = 'end'
        for _ in range(max_steps):
            nxt = []
            for j in grid.near(cur, 0.2):
                if j in used:
                    continue
                t = g.segs[j]
                if is_dashed(t) != is_dashed(s):
                    continue
                if math.dist(t.a, cur) < 0.08:
                    nxt.append((j, t.b))
                elif math.dist(t.b, cur) < 0.08:
                    nxt.append((j, t.a))
            # 同じ色の線を優先（コネクタの矢じりなど別の色の短い線に入らない）
            same = [(j, q) for j, q in nxt if g.segs[j].color == s.color and g.segs[j].length > 1.2]
            if len(same) == 1:
                j, cur = same[0]
                path.append(j)
                used.add(j)
                continue
            if len(same) > 1:
                reason = 'branch'
            break
        out.append((cur, reason, path))
    return out


def text_in(g, box, pad=0.0):
    x0, y0, x1, y1 = box
    return [t for t in g.txts if x0 - pad <= t.cx <= x1 + pad and y0 - pad <= t.cy <= y1 + pad]


def terminal_at(g, rects, E, wire_dir, narrow=()):
    """芯線の端点 E のコネクタ枠（E が枠の辺から 1.5 以内）、ピン番号、コネクタ名。

    narrow は `narrow_rects()` の細長い枠。**芯線に直交する向きの枠だけ**を候補に加える（横の芯線なら縦長の枠）。
    芯線に沿う細長い枠は、芯線の途中に置いた配線名の枠（`CM12` など）で、端末ではない。"""
    horiz_wire = abs(wire_dir[0]) > abs(wire_dir[1])
    rects = list(rects) + [r for r in narrow if ((r[3] - r[1]) > (r[2] - r[0])) == horiz_wire]
    best = None
    for r in rects:
        x0, y0, x1, y1 = r
        if not (x0 - 1.5 <= E[0] <= x1 + 1.5 and y0 - 1.5 <= E[1] <= y1 + 1.5):
            continue
        area = (x1 - x0) * (y1 - y0)
        if best is None or area < best[0]:
            best = (area, r)
    if not best:
        return circle_terminal(g, E) or gnd_terminal(g, E)
    r = best[1]
    x0, y0, x1, y1 = r
    horiz = abs(wire_dir[0]) > abs(wire_dir[1])
    inside = text_in(g, r)
    # ピン番号: 枠の中で、芯線の延長線上（横の芯線なら同じ高さ）に最も近い文字
    pin, pin_t = None, None
    if inside:
        key = (lambda t: abs(t.cy - E[1])) if horiz else (lambda t: abs(t.cx - E[0]))
        cand = min(inside, key=key)
        row = (cand.y1 - cand.y0) if horiz else (cand.x1 - cand.x0)
        if key(cand) <= max(1.5, 0.6 * row):
            pin = cand.s
            pin_t = cand
    # コネクタ名: 枠の外で、枠のすぐ上（縦の芯線なら左）にある文字
    name = None
    cands = []
    for t in g.txts:
        if x0 <= t.cx <= x1 and y0 <= t.cy <= y1:
            continue
        if horiz:      # 枠の上
            if t.y0 >= y1 - 0.5 and t.y0 - y1 < 4 and t.x0 < x1 + 2 and t.x1 > x0 - 6:
                cands.append((t.y0 - y1, t))
        else:          # 枠の左（90°の文字）または上
            if t.x1 <= x0 + 0.5 and x0 - t.x1 < 4 and t.y0 < y1 + 2 and t.y1 > y0 - 6:
                cands.append((x0 - t.x1, t))
    name_pt, name_t = None, None
    if cands:
        # 枠からの距離が同じなら、枠の中心に近い文字（`INPUT` の枠の上に `INPUT` と `CP(25A)` が並ぶ図面）
        centre = (lambda t: abs(t.cx - (x0 + x1) / 2)) if horiz else (lambda t: abs(t.cy - (y0 + y1) / 2))
        nt = min(cands, key=lambda c: (round(c[0], 1), centre(c[1])))[1]
        name, name_pt, name_t = nt.s, (nt.cx, nt.cy), nt
    return {'rect': r, 'pin': pin, 'connector': name, 'kind': 'box', 'name_pt': name_pt, 'pin_t': pin_t, 'name_t': name_t}


TERMINAL_CIRCLE_MAX = 4.2   # 端子円の直径の上限（コネクタの円は直径 3.98 のものがある）
TERMINAL_CIRCLE_MIN = 1.9   # 芯線の途中で止める端子円の直径の下限（接続点の黒丸 0.5〜1.0 は除く）


def clip_at_terminal_circle(g, E, side, horiz, box, tol=0.3):
    """芯線の端 E より手前（ケーブル記号 box の側）に、線の上に載った端子円があれば、その円の縁（記号に向いた側）を端として返す。無ければ E。

    コネクタの円が、記号から端子台まで通る1本の長い線の途中に載る図がある（EE6888-604-01A の M74006: CNHV- の円から CNHVA- の円までが
    HV- から TB1 まで続く1本の線の一部）。線の端まで進むと、ケーブルの端末ではなく先の端子台（TB1）を拾うので、記号から外へ向かって
    最初に出会う端子円で止める。side は '-'（記号の左／下）か '+'（右／上）、horiz は芯線が横向きか。"""
    x0, y0, x1, y1 = box
    along = 0 if horiz else 1                 # 芯線に沿う軸
    lo, hi = (x0, x1) if horiz else (y0, y1)
    found = []
    for c in g.curves:
        d = c.size[0]
        if c.kind != 'CIRCLE' or not (TERMINAL_CIRCLE_MIN <= d <= TERMINAL_CIRCLE_MAX):
            continue
        if abs(c.center[1 - along] - E[1 - along]) > tol:
            continue
        a = c.center[along]
        if side == '-' and E[along] - tol <= a < lo:
            found.append((-a, a + d / 2))
        elif side == '+' and hi < a <= E[along] + tol:
            found.append((a, a - d / 2))
    if not found:
        return E
    edge = min(found)[1]
    return (edge, E[1]) if horiz else (E[0], edge)


def circle_terminal(g, E):
    """芯線の端が小さな円（端子台の端子・ブレーカーの極）に触れるとき、端子番号と機器名。

    端子番号 = 円から 4 以内の3文字以下の文字。機器名 = 同じ x に並ぶ円の列の一番上の円の上 6 以内の、
    英字を含む3文字以上の文字（括弧で始まる定格 `(75A)` は除く）。
    """
    circ = None
    for c in g.curves:
        if c.kind == 'CIRCLE' and c.size[0] <= TERMINAL_CIRCLE_MAX:
            r = c.size[0] / 2
            if abs(math.dist(E, c.center) - r) < 0.3 or math.dist(E, c.center) < r:
                circ = c
                break
    if circ is None:
        return None
    cx, cy = circ.center
    near = [t for t in g.txts if len(t.s) <= 3 and math.dist((t.cx, t.cy), (cx, cy)) < 4]
    pin_t = min(near, key=lambda t: math.dist((t.cx, t.cy), (cx, cy))) if near else None
    pin = pin_t.s if pin_t else None
    col = [c for c in g.curves if c.kind == 'CIRCLE' and abs(c.center[0] - cx) < 0.15 and c.size[0] <= TERMINAL_CIRCLE_MAX]
    col.sort(key=lambda c: c.center[1])
    top = circ
    for c in col:                       # 上へ間隔 7 以内で続く円
        if c.center[1] > top.center[1] and c.center[1] - top.center[1] <= 7.5:
            top = c
    tx, ty = top.center
    cands = [t for t in g.txts if len(t.s) >= 3 and re.search('[A-Z]', t.s) and not t.s.startswith('(')
             and 0 <= t.y0 - ty <= 6 and t.x0 - 3 <= tx <= t.x1 + 3]
    nt = min(cands, key=lambda t: t.y0 - ty) if cands else None
    return {'rect': None, 'pin': pin, 'connector': nt.s if nt else None, 'kind': 'circle', 'center': (cx, cy), 'radius': circ.size[0] / 2,
            'name_pt': (nt.cx, nt.cy) if nt else None, 'pin_t': pin_t, 'name_t': nt}


def wire_label_texts(g, k, sym_box=None):
    """芯線 k の上（横の線）・左（縦の線）にある文字（線番・色）。記号の近くを優先して、近い順。文字 T のリスト。"""
    s = g.segs[k]
    horiz = abs(s.a[1] - s.b[1]) < 0.05
    out = []
    for t in g.txts:
        if horiz:
            lo, hi = sorted((s.a[0], s.b[0]))
            if lo - 0.5 <= t.cx <= hi + 0.5 and 0 <= t.y0 - s.a[1] <= 1.5 and not t.vertical:
                out.append((abs(t.cx - (sym_box[0] + sym_box[2]) / 2) if sym_box else 0, t.s, t))
        else:
            lo, hi = sorted((s.a[1], s.b[1]))
            if lo - 0.5 <= t.cy <= hi + 0.5 and 0 <= s.a[0] - t.x1 <= 1.5 and t.vertical:
                out.append((abs(t.cy - (sym_box[1] + sym_box[3]) / 2) if sym_box else 0, t.s, t))
    return [t for _d, _s, t in sorted(out, key=lambda v: (v[0], v[1]))]


def nearby_symbols(g, lab, max_d=12.0):
    """引出線の無いケーブル番号（円筒の横に置くだけ）: 文字の中心から max_d 以内で最も近い閉じた楕円と、
    同じ中心線上の同じ大きさの楕円（円筒のもう一方の端）。"""
    t = lab['txt']
    P = (t.cx, t.cy)
    ells = [c for c in g.curves if c.kind == 'ELLIPSE' and c.closed]
    if not ells:
        return [], 'none'
    def dist(c):
        x0, y0, x1, y1 = _bbox(c.pts)
        dx = max(x0 - P[0], 0, P[0] - x1)
        dy = max(y0 - P[1], 0, P[1] - y1)
        return math.hypot(dx, dy)
    # 円筒の端の楕円は文字の上下にある。文字の向きと平行に並ぶ楕円の組を探す
    best = None
    down = (math.sin(math.radians(t.rot)), -math.cos(math.radians(t.rot)))   # 文字の「下」の向き
    for c in ells:
        if (c.center[0] - P[0]) * down[0] + (c.center[1] - P[1]) * down[1] <= 0:
            continue
        for c2 in ells:
            if c2 is c:
                continue
            same = abs(c.size[0] - c2.size[0]) < 0.2 and abs(c.size[1] - c2.size[1]) < 0.2
            if not same:
                continue
            if t.vertical and abs(c.center[0] - c2.center[0]) < 0.2 and c.center[1] > c2.center[1]:
                ylo, yhi = c2.center[1], c.center[1]
                xs = _bbox(c.pts)
                if ylo <= P[1] <= yhi:
                    d = min(abs(P[0] - xs[0]), abs(P[0] - xs[2]))
                    if d < max_d and (best is None or d < best[0]):
                        best = (d, c, c2)
            if not t.vertical and abs(c.center[1] - c2.center[1]) < 0.2 and c.center[0] > c2.center[0]:
                xlo, xhi = c2.center[0], c.center[0]
                ys = _bbox(c.pts)
                if xlo <= P[0] <= xhi:
                    d = min(abs(P[1] - ys[1]), abs(P[1] - ys[3]))
                    if d < max_d and (best is None or d < best[0]):
                        best = (d, c, c2)
    if best:
        return [{'box': _bbox(best[1].pts), 'color': best[1].color, 'shape': 'ellipse'},
                {'box': _bbox(best[2].pts), 'color': best[2].color, 'shape': 'ellipse'}], 'beside'
    return [], 'none'


def inner_point(term, E, wire_dir=None):
    """コネクタ枠の、ケーブルと反対側のすぐ外の点（ユニットの中）。円の端子は円の中心の反対側。

    枠のどの辺から出るかは、芯線の向き（wire_dir）で決める（細長い枠は中心への向きで決めると、辺の向きを取り違える）。
    wire_dir が無いときは、枠の中心への向きで決める。"""
    if term and term.get('rect'):
        x0, y0, x1, y1 = term['rect']
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = cx - E[0], cy - E[1]
        horiz = abs(wire_dir[0]) > abs(wire_dir[1]) if wire_dir is not None else abs(dx) >= abs(dy)
        if horiz:
            return (x1 + 0.6, E[1]) if dx > 0 else (x0 - 0.6, E[1])
        return (E[0], y1 + 0.6) if dy > 0 else (E[0], y0 - 0.6)
    if term and term.get('center'):
        cx, cy = term['center']
        return (2 * cx - E[0], 2 * cy - E[1])
    return E


def gnd_terminal(g, E, r=7.0):
    """接地記号の端: 端点の近く（r 以内）に文字 `GND`・`PE`・`FG` がある。"""
    cands = [t for t in g.txts if t.s.strip() in ('GND', 'PE', 'FG', 'E')
             and math.dist((t.cx, t.cy), E) < r]
    if not cands:
        return None
    t = min(cands, key=lambda t: math.dist((t.cx, t.cy), E))
    return {'rect': None, 'pin': None, 'connector': t.s.strip(), 'kind': 'gnd', 'name_pt': (t.cx, t.cy), 'pin_t': None, 'name_t': t}


# ---------------------------------------------------------------- 機器（コネクタの先の箱）

def device_names(g, grid, rects, term, E, max_box=150.0):
    """コネクタのケーブルと反対側の辺から出る線をたどり、行き着いた機器の箱の中・すぐ外の文字を返す。

    センサー・ヒーター・モーターなど、機器そのものがユニットのものは領域抽出の対象（黄色の太線の枠）に
    ならないため、配線をたどって機器の箱を見つける（2026-11-19 ユーザー決定 (a)）。
    Returns: [(文字の1行目, 経路の端点, 文字 T)]（重複なし）
    """
    r = term.get('rect') if term else None
    if not r:
        return []
    x0, y0, x1, y1 = r
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = cx - E[0], cy - E[1]
    horiz = abs(dx) >= abs(dy)
    # 反対側の辺
    if horiz:
        far = x1 if dx > 0 else x0
        on_far = lambda p: abs(p[0] - far) < 2.5 and y0 - 0.5 <= p[1] <= y1 + 0.5
        outward = lambda p, q: (q[0] - p[0]) * (1 if dx > 0 else -1) > 0.5
    else:
        far = y1 if dy > 0 else y0
        on_far = lambda p: abs(p[1] - far) < 2.5 and x0 - 0.5 <= p[0] <= x1 + 0.5
        outward = lambda p, q: (q[1] - p[1]) * (1 if dy > 0 else -1) > 0.5
    starts = []
    for k, s in enumerate(g.segs):
        if s.length < 1.5 or is_dashed(s):
            continue
        for p, q in ((s.a, s.b), (s.b, s.a)):
            if on_far(p) and outward(p, q):
                starts.append(k)
    out, seen = [], set()

    def box_at(pt, tol):
        best = None
        for rr in rects:
            a0, b0, a1, b1 = rr
            if a1 - a0 > max_box or b1 - b0 > max_box or rr == r:
                continue
            if a0 - tol <= pt[0] <= a1 + tol and b0 - tol <= pt[1] <= b1 + tol:
                area = (a1 - a0) * (b1 - b0)
                if best is None or area < best[0]:
                    best = (area, rr)
        return best and best[1]

    for k in starts:
        for end, _reason, path in trace_wire(g, grid, k):
            # 経路の点を順に見て、最初に機器の箱に触れた点で止める（2本の線が箱の縁の1点で会い、
            # そのまま元のコネクタへ戻る描き方がある: 602-01E のヒーター）
            pts = []
            for j in path:
                pts += [g.segs[j].a, g.segs[j].b]
            box, stop = None, end
            for pt in pts:
                if on_far(pt):
                    continue
                box = box_at(pt, 0.6)
                if box:
                    stop = pt
                    break
            if box is None:
                if on_far(end):
                    continue
                box = box_at(end, 1.5)
            if box:
                a0, b0, a1, b1 = box
                texts = text_in(g, (a0 - 6, b0 - 6, a1 + 6, b1 + 6))
            else:
                texts = [t for t in g.txts if math.dist((t.cx, t.cy), stop) < 8]
            for t in texts:
                first = t.s.split('\n')[0]
                if first not in seen:
                    seen.add(first)
                    out.append((first, stop, t))
    return out


# ---------------------------------------------------------------- ケーブル記号のシールド（破線）

SHIELD_END_TOL = 1.5     # 破線の端が記号の端（長い向きの両端の中心）に着いたとみなす距離


def find_shield(g, grid, box):
    """楕円・長円のケーブル記号の長い向きの両端から、同じ直線上に伸びる破線（シールド）と、その先の閉じた楕円を返す。

    シールドの破線は、記号の上下（縦長なら。横長なら左右）の端から出て、隙間 6 以内でつながり、反対側のシールドの端の楕円に着く
    （EE6892-611-01B の M65232）。芯線そのものが破線の図面（610-01B の ACCESSORY CABLE）は、破線が記号の端ではなく中心を通るので対象外。
    接地記号・文字は含めない。
    Returns: {'lines': [[(始点), (終点)], ...], 'ellipses': [(x0, y0, x1, y1), ...]} または None。"""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    vertical = (y1 - y0) >= (x1 - x0)
    ends = [(cx, y0), (cx, y1)] if vertical else [(x0, cy), (x1, cy)]
    lines, ellipses, seen = [], [], set()
    for k, s in enumerate(g.segs):
        if not is_dashed(s):
            continue
        if abs(s.a[1] - s.b[1]) < 0.05 if vertical else abs(s.a[0] - s.b[0]) < 0.05:
            pass
        else:
            continue
        near = min((s.a, s.b), key=lambda p: min(math.dist(p, e) for e in ends))
        if min(math.dist(near, e) for e in ends) > SHIELD_END_TOL:
            continue
        e0, e1 = _collinear_run(g, grid, k)
        key = (round(e0[0], 1), round(e0[1], 1), round(e1[0], 1), round(e1[1], 1))
        if key in seen:
            continue
        seen.add(key)
        far = max((e0, e1), key=lambda p: min(math.dist(p, e) for e in ends))
        for c in g.curves:
            if c.kind == 'ELLIPSE' and c.closed and min(math.dist(far, q) for q in c.pts) < SHIELD_END_TOL:
                lines.append([tuple(e0), tuple(e1)])
                ellipses.append(_bbox(c.pts))
                break
    if not lines:
        return None
    return {'lines': lines, 'ellipses': list(dict.fromkeys(ellipses))}


# ---------------------------------------------------------------- コネクタ・端子で途切れた枠（ユニットの枠）

GAP_FRAME_MIN = 8.0       # 途切れた枠として認める辺の下限
GAP_FRAME_MAX = 150.0     # 同上限（機器の箱と同じ）
GAP_FRAME_TOL = 0.6       # 辺の位置・端のずれの許容
GAP_STEP = 1.5            # 隙間を埋める部品どうしの間の小さな空き（部品の枠と文字の間）の許容
GAP_CIRCLE_MAX = 6.0      # 隙間を埋める端子円の直径の上限（RF IN の円は 4.5）
GAP_NAME_DIST = 8.0       # 枠の名称候補にする文字の、枠の辺からの距離の上限


def _axis_lines(g):
    """軸に平行な線分を (横 [(y, x0, x1)], 縦 [(x, y0, y1)]) にする（破線は除く）。"""
    hs, vs = [], []
    for s in g.segs:
        if is_dashed(s):
            continue
        if abs(s.a[1] - s.b[1]) < 0.02:
            hs.append((round((s.a[1] + s.b[1]) / 2, 2), min(s.a[0], s.b[0]), max(s.a[0], s.b[0])))
        elif abs(s.a[0] - s.b[0]) < 0.02:
            vs.append((round((s.a[0] + s.b[0]) / 2, 2), min(s.a[1], s.b[1]), max(s.a[1], s.b[1])))
    return hs, vs


def _merge_over_devices(lines, spans):
    """同じ直線上の線分をつなぐ。つなぐのは、触れている・重なっているとき、または隙間が部品（コネクタ枠・端子円）で覆われているとき。

    lines: [(レベル, 始点, 終点)]。spans(level) は、そのレベルの線を覆う部品の [(始点, 終点)]。
    Returns: [(レベル, 始点, 終点, 部品で橋渡ししたか)]"""
    by_level = {}
    for lv, a, b in lines:
        by_level.setdefault(round(lv, 1), []).append((a, b))
    out = []
    for lv, items in by_level.items():
        items.sort()
        dev = spans(lv)
        cur_a, cur_b, bridged = items[0][0], items[0][1], False
        for a, b in items[1:]:
            if a <= cur_b + 0.3:
                cur_b = max(cur_b, b)
                continue
            # 隙間 [cur_b, a] が部品の範囲だけで埋まるか
            pos, ok = cur_b, True
            while pos < a - GAP_STEP and ok:
                nxt = [e for (d0, d1) in dev for e in (d1,) if d0 <= pos + GAP_STEP and d1 > pos + 0.3]
                if nxt:
                    pos = max(nxt)
                else:
                    ok = False
            if ok:
                cur_b, bridged = max(cur_b, b), True
            else:
                out.append((lv, cur_a, cur_b, bridged))
                cur_a, cur_b, bridged = a, b, False
        out.append((lv, cur_a, cur_b, bridged))
    return out


def gapped_frame_regions(g, known_regions=()):
    """コネクタ枠・端子円で辺が途切れたユニットの枠を、領域（名称候補つき）として返す。

    領域抽出（DXF-extract-labels 由来）は、辺の途中が円（端子）で途切れた枠を閉じた領域にしない（配線ループを除くため）。また
    長方形の部品で途切れた辺の橋渡しは横線だけ。ユニットの枠がコネクタや端子の円に重なって描かれる図（EE6892-611-01B の
    `MBX2 R1`）では、領域も機器の箱も見つからず、ユニットが読めない。ここでは、辺の隙間が部品・コネクタ名の文字だけで埋まるときに限って、枠を閉じた矩形とする。
    部品で橋渡しした辺が1本もない（途切れのない）矩形は、機器の箱（`axis_rects`）と区別がつかないので返さない。
    名称候補は、枠の中にあり枠の辺から GAP_NAME_DIST 以内で、部品の枠の中でない領域名候補の文字（辺に近い順。1行目）。
    Returns: 領域の dict（`polygon`・`area`・`name_candidates`）のリスト。`known_regions` と外接矩形が同じものは除く。
    """
    devices = list(axis_rects(g, 2.0, 60.0))
    for c in g.curves:
        if c.kind == 'CIRCLE' and max(c.size) <= GAP_CIRCLE_MAX:
            devices.append((c.center[0] - c.size[0] / 2, c.center[1] - c.size[1] / 2,
                            c.center[0] + c.size[0] / 2, c.center[1] + c.size[1] / 2))
    # 辺の隙間を埋めるのは、部品（コネクタ枠・端子円）のほか、辺に載ったコネクタ名の文字（`AMC`）
    covers = devices + [(t.x0 - 0.3, t.y0 - 0.3, t.x1 + 0.3, t.y1 + 0.3) for t in g.txts if t.y1 - t.y0 <= 8 and t.x1 - t.x0 <= 40]
    hs, vs = _axis_lines(g)
    h_spans = lambda y: [(r[0], r[2]) for r in covers if r[1] - 0.5 <= y <= r[3] + 0.5]   # noqa: E731
    v_spans = lambda x: [(r[1], r[3]) for r in covers if r[0] - 0.5 <= x <= r[2] + 0.5]   # noqa: E731
    H = [h for h in _merge_over_devices(hs, h_spans) if GAP_FRAME_MIN <= h[2] - h[1] <= GAP_FRAME_MAX]
    V = [v for v in _merge_over_devices(vs, v_spans) if GAP_FRAME_MIN <= v[2] - v[1] <= GAP_FRAME_MAX]
    tol = GAP_FRAME_TOL
    h_by = {}
    for h in H:
        h_by.setdefault((round(h[1]), round(h[2])), []).append(h)
    v_at = {}
    for v in V:
        v_at.setdefault(round(v[0]), []).append(v)

    def vside(x, y0, y1):
        for key in (round(x) - 1, round(x), round(x) + 1):
            for v in v_at.get(key, ()):
                if abs(v[0] - x) <= tol and v[1] <= y0 + tol and v[2] >= y1 - tol:
                    return v
        return None

    frames = {}
    for h in H:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for h2 in h_by.get((round(h[1]) + dx, round(h[2]) + dy), ()):
                    if h2[0] - h[0] < GAP_FRAME_MIN or h2[0] - h[0] > GAP_FRAME_MAX:
                        continue
                    if abs(h[1] - h2[1]) > tol or abs(h[2] - h2[2]) > tol:
                        continue
                    x0, x1, y0, y1 = (h[1] + h2[1]) / 2, (h[2] + h2[2]) / 2, h[0], h2[0]
                    left, right = vside(x0, y0, y1), vside(x1, y0, y1)
                    if left and right and (h[3] or h2[3] or left[3] or right[3]):
                        frames[(round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1))] = (x0, y0, x1, y1)
    known = [_bbox(r['polygon']) for r in known_regions if len(r.get('polygon') or []) >= 3]
    out = []
    for (x0, y0, x1, y1) in sorted(frames.values()):
        if any(max(abs(a - b) for a, b in zip(k, (x0, y0, x1, y1))) <= 1.0 for k in known):
            continue
        cands = []
        for t in g.txts:
            if not (x0 < t.cx < x1 and y0 < t.cy < y1):
                continue
            d = min(t.cx - x0, x1 - t.cx, t.cy - y0, y1 - t.cy)
            first = t.s.split('\n')[0].strip()
            if d <= GAP_NAME_DIST and first and classify_label(first) == REGION_NAME \
                    and not any(r[0] <= t.cx <= r[2] and r[1] <= t.cy <= r[3] for r in devices):
                cands.append((round(d, 2), first))
        if cands:
            out.append({'polygon': [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 'area': (x1 - x0) * (y1 - y0),
                        'name_candidates': sorted(set(cands))})
    return out


# ---------------------------------------------------------------- 1枚の 405 を読む

REGION_CFG = {**DEFAULT_REGION_CONFIG, 'detect_minor_regions': True, 'local_rotation': True}
# min_face_ratio は既定（0.5%）のまま。0.02% まで下げても機器の箱は領域にならず、結果が同じだった（試作の実測）。


def _regions_at(regions, pt):
    """点を含む、名称候補を持つ領域（面積の小さい順）。"""
    hits = [r for r in regions if r.get('name_candidates') and len(r.get('polygon') or []) >= 3
            and _point_in_polygon(pt, r['polygon'])]
    return sorted(hits, key=lambda r: r.get('area', 0))


def _term_points(term, inner):
    """端末の位置の候補: ラベル・コネクタ枠（端子の円）の中心・枠のユニット側の点。

    コネクタはユニット枠の線の上に載るので、ラベルだけだと領域の外に出ることがある（405-08A の CN100IN）。
    """
    pts = []
    if term.get('name_pt'):
        pts.append(term['name_pt'])
    if term.get('rect'):
        x0, y0, x1, y1 = term['rect']
        pts.append(((x0 + x1) / 2, (y0 + y1) / 2))
    if term.get('center'):
        pts.append(term['center'])
    if inner:
        pts.append(inner)
    return pts


def _empty_end():
    return {'kind': None, 'name': None, 'pin': None, 'regions': [], 'devices': [], 'nearby': [], '_refs': {}, '_shape': None}


def _term_shape(term):
    """端末の図形（viewer の重ね書き用）: コネクタ枠は {'rect': (x0, y0, x1, y1)}、端子の円は {'circle': ((cx, cy), 半径)}。接地などは None。"""
    if term.get('rect'):
        return {'rect': tuple(term['rect'])}
    if term.get('center') and term.get('radius'):
        return {'circle': (tuple(term['center']), term['radius'])}
    return None


def _segs_of(g, ks):
    return [[tuple(g.segs[k].a), tuple(g.segs[k].b)] for k in ks]


def _end_info(g, grid, device_rects, regions, E, term, inner):
    """芯線の1つの端の事実: 端末の種類・名前・ピンと、ユニットの根拠（領域の名称候補・機器の文字・近傍の文字）。"""
    if not term:
        return _empty_end()
    names = []
    for pt in _term_points(term, inner):
        for r in _regions_at(regions, pt):
            for _d, cand in r['name_candidates']:     # 最有力候補でなくても、候補のどれかが合えばよい
                if cand not in names:
                    names.append(cand)
    devices, device_refs = [], {}
    for txt, _stop, dt in device_names(g, grid, device_rects, term, E):
        if txt not in devices:
            devices.append(txt)
            device_refs[txt] = dt.ref
    nearby, nearby_refs = [], {}
    if term.get('name_pt'):
        for t in g.txts:
            d = math.dist((t.cx, t.cy), term['name_pt'])
            if d <= NEAR_LABEL_DIST:
                nearby.append((t.s.split('\n')[0], round(d, 1), t.s, t.ref))
        nearby.sort(key=lambda x: x[1])
        for first, _d, _full, ref in nearby:
            nearby_refs.setdefault(first, ref)
        nearby = [(first, d, full) for first, d, full, _ref in nearby]
    # 領域の名称候補は文字列しか持たないので、端の位置に最も近い同じ文字を探す（見つからなければ位置なし）
    at = term.get('name_pt') or (inner if inner else None)
    region_refs = {}
    for cand in names:
        same = [t for t in g.txts if cand in (t.s, t.s.split('\n')[0].strip(), t.s.strip())]
        if same and at:
            region_refs[cand] = min(same, key=lambda t: math.dist((t.cx, t.cy), at)).ref
        elif same:
            region_refs[cand] = same[0].ref
    refs = {'name': term['name_t'].ref if term.get('name_t') else None,
            'pin': term['pin_t'].ref if term.get('pin_t') else None,
            'regions': region_refs, 'devices': device_refs, 'nearby': nearby_refs}
    return {'kind': term['kind'], 'name': term['connector'], 'pin': term['pin'],
            'regions': names, 'devices': devices, 'nearby': nearby, '_refs': refs, '_shape': _term_shape(term)}


# 図中の参照: 「to POWER LINE (SYSTEM I/F BOX) (-405-08)」のように、括弧の中の `-英数字3-英数字2` が参照先の図番（の一部）を示す。
# 括弧の中はその後ろに任意の文字が続いてよい（`(-405-05 3/4)` のシート番号など）。
# `(-405-01,02)` のようにカンマで続く2桁は、頭の `-405-` を引き継ぐ別の参照（`-405-01` と `-405-02`）。`-405-01,-405-02` も同じ。
_REF_RE = re.compile(r'\(\s*(-[0-9A-Z]{3}-[0-9A-Z]{2})([^()]*)\)')
_REF_NEXT_RE = re.compile(r'\s*[,、]\s*(-[0-9A-Z]{3}-[0-9A-Z]{2}|[0-9A-Z]{2})(?![0-9A-Z])')


def find_references(texts):
    """図中の文字から、他の図面への参照 `(-xxx-xx…)` を拾う。

    Args:
        texts: 文字列のリスト（改行・全角は問わない。NFKC で正規化して見る）。`(-405-01,02)` は `-405-01` と `-405-02` の2件になる。
    Returns:
        [{'pattern': '-405-08', 'text': 参照を含む文字（空白を1つに詰めたもの）}, ...]（文字の出現順、(pattern, text) の重複は除く）。
    """
    out, seen = [], set()
    for raw in texts:
        text = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', raw or '')).strip()
        for m in _REF_RE.finditer(text.upper()):
            patterns, rest, prefix = [m.group(1)], m.group(2), m.group(1)[:-2]
            while (n := _REF_NEXT_RE.match(rest)):
                patterns.append(n.group(1) if n.group(1).startswith('-') else prefix + n.group(1))
                rest = rest[n.end():]
            for pattern in patterns:
                if (pattern, text) not in seen:
                    seen.add((pattern, text))
                    out.append({'pattern': pattern, 'text': text})
    return out


def _rect_made_by(r, segs, tol=0.05):
    """矩形 r の辺のどれかに、segs（芯線自身の線分）のどれかが全部載っているか。載っていれば、この矩形は芯線が作ったもの
    （U 字の線とコネクタの辺で閉じた長方形）で、コネクタの枠ではない。"""
    x0, y0, x1, y1 = r
    for s in segs:
        (ax, ay), (bx, by) = s.a, s.b
        if abs(ay - by) < tol and (abs(ay - y0) < tol or abs(ay - y1) < tol) and x0 - tol <= min(ax, bx) and max(ax, bx) <= x1 + tol:
            return True
        if abs(ax - bx) < tol and (abs(ax - x0) < tol or abs(ax - x1) < tol) and y0 - tol <= min(ay, by) and max(ay, by) <= y1 + tol:
            return True
    return False


def _loop_ends(g, grid, device_rects, regions, rects, narrow, traces, path_all, side):
    """両端が記号の同じ側に着く芯線（U 字のジャンパ線。EE6886-614-01A の M65027: 1つのコネクタの端子 5 と 6 をつなぐ）の両端。

    芯線自身が作る長方形（U 字の線＋コネクタの辺）はコネクタの枠にしない。両端のコネクタが見つかったときだけ、2つ目の端を反対側のキーに入れて
    返す（同じキーだと後の端が前の端を上書きし、ピンが片方しか残らない）。どちらかの端が見つからなければ None（従来の読み方に戻す）。"""
    own = [g.segs[j] for j in path_all]
    wire_rects = [r for r in rects if not _rect_made_by(r, own)]
    infos = []
    for E, _reason, path_ in traces:
        wire_dir = g.segs[path_[-1]].dir()
        term = terminal_at(g, wire_rects, E, wire_dir, narrow)
        if not term or term.get('kind') != 'box':
            return None
        infos.append(_end_info(g, grid, device_rects, regions, E, term, inner_point(term, E, wire_dir)))
    other = '+' if side == '-' else '-'
    return {side: infos[0], other: infos[1]}


def analyze_schematic(path, name):
    """1枚の展開接続図を読み、ケーブル番号の出現ごとの芯線と両端の事実を返す。

    Returns:
        {'title': タイトル, 'error': 読めなかった理由（無ければ ''）, 'no_text': 文字が1つも無い,
         'region_error': 領域抽出の失敗理由（無ければ ''）,
         'refs': [{'pattern': '-405-08', 'text': 'to POWER LINE (-405-08)'}]（図中の「(-xxx-xx…)」＝他の図面への参照）,
         'occurrences': [{'cable', 'remark', 'how', 'wires': [{'labels': [...], 'ends': {'-': END, '+': END}}]}]}
        `how`: ellipse（楕円）・capsule（長円）・via-line（シールドや円筒の側面の線経由）・single（1本の線）・
        beside（引出線なしで円筒の横に置いたラベル）・none（引出線が見つからない）。
        `ends` のキーは、記号の中心から見て負の側が '-'、正の側が '+'（端が片側にしか無ければ1つだけ）。
        END: {'kind': 'box'|'circle'|'gnd'|None, 'name', 'pin', 'regions', 'devices', 'nearby'}。
        `nearby` は [(文字の1行目, 距離, 文字の全体), ...]（機器符号のラベルから NEAR_LABEL_DIST 以内。近い順）。
    """
    out = {'title': '', 'error': '', 'no_text': False, 'region_error': '', 'refs': [], 'occurrences': []}
    g, _doc = load(path)
    if g.error:
        out['error'] = g.error
        return out
    try:
        out['title'] = extract_cables(path, name).get('title', '')
    except Exception:  # noqa: BLE001 - タイトルが取れなくても読み取りは続ける
        pass
    if not g.txts:
        out['no_text'] = True
        return out
    out['refs'] = find_references([t.s for t in g.txts])
    try:
        analysis = analyze_dxf_regions(path, REGION_CFG)
        regions = list(analysis.get('regions', [])) + list(analysis.get('minor_regions', []))
    except Exception as e:  # noqa: BLE001 - 領域が取れなくても、機器・近傍の根拠で判定できる
        out['region_error'] = f'{type(e).__name__}: {e}'
        regions = []
    regions += gapped_frame_regions(g, regions)      # コネクタ・端子で辺が途切れたユニットの枠（領域抽出が閉じない）

    grid = Grid(g.segs)
    rects = axis_rects(g, 2.0, 60.0)             # コネクタ枠・端子台
    narrow = narrow_rects(g)                     # 細長いコネクタ枠（芯線に直交するものだけ端末にする）
    device_rects = axis_rects(g, 2.0, 150.0)     # 機器の箱
    labels = link_labels(g, cable_labels(g), find_arrows(g, grid), grid)
    for lab in labels:
        symbols, how = find_symbols(g, grid, lab)
        wires, seen = [], set()
        arrow = lab.get('arrow') or {}
        leader = (_segs_of(g, list(lab.get('chain', [])) + list(arrow.get('heads', ())))
                  + [[tuple(p), tuple(q)] for p, q in zip(arrow.get('verts', []), arrow.get('verts', [])[1:])])
        sym_shapes = [{'shape': sym.get('shape', 'ellipse'), 'box': tuple(sym['box'])} for sym in symbols if 'box' in sym]
        for sym in symbols:
            if 'wire' in sym:
                ks, box = [sym['wire']], None
                center = lab['arrow']['tip']
            else:
                ks, box = wires_through(g, sym['box'], sym_color=sym['color']), sym['box']
                center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            for k in ks:
                if k in seen:
                    continue
                seen.add(k)
                d = g.segs[k].dir()
                horiz = abs(d[0]) >= abs(d[1])
                traces = trace_wire(g, grid, k)
                path_all = []
                for _E, _reason, path_ in traces:
                    path_all += [j for j in path_ if j not in path_all]
                seen.update(path_all)           # 同じ芯線（U 字の線など）を、別の線分から二度たどらない
                sides = ['-' if ((E[0] - center[0]) if horiz else (E[1] - center[1])) < 0 else '+' for E, _r, _p in traces]
                ends = None
                if len(traces) == 2 and sides[0] == sides[1]:
                    ends = _loop_ends(g, grid, device_rects, regions, rects, narrow, traces=traces, path_all=path_all, side=sides[0])
                if ends is None:
                    ends = {}
                    for (E, _reason, path_), side in zip(traces, sides):
                        if box:
                            E = clip_at_terminal_circle(g, E, side, horiz, box)
                        term = terminal_at(g, rects, E, g.segs[path_[-1]].dir(), narrow)   # 曲がった芯線は端の線分の向き
                        ends[side] = _end_info(g, grid, device_rects, regions, E, term, inner_point(term, E, g.segs[path_[-1]].dir()))
                wl = wire_label_texts(g, k, box)
                wires.append({'labels': [t.s for t in wl], 'ends': ends, '_label_refs': [t.ref for t in wl],
                              '_path': _segs_of(g, path_all)})
        shield = None
        if how in ('ellipse', 'capsule'):
            for sym in symbols:
                if 'box' in sym:
                    shield = find_shield(g, grid, sym['box'])
                    if shield:
                        break
        out['occurrences'].append({'cable': lab['cable'], 'remark': lab['remark'], 'how': how, 'wires': wires,
                                   '_label_ref': lab['txt'].ref, '_leader': leader, '_symbols': sym_shapes, '_shield': shield,
                                   '_remark_ref': ((lab['remark_txt'].s.split('\n')[0], lab['remark_txt'].ref) if lab.get('remark_txt') else None)})
    return out
