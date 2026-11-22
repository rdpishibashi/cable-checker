"""ケーブル製作図（393）の 線番／PIN No. の表と、それをつなぐ線上のラベルを読む（streamlit 非依存）。

実現性検討（docs/feasibility/fab_drawing/README.md）の試作の移植。

手順:
  1. 見出し「線番」「PIN No.」ごとに列を作る。見出しを横切る左右の縦罫線が列の幅と縦の範囲、
     その間を横切る横罫線がセルの区切り。隣り合う列（同じ行の区切り）は1つの表にまとめる
  2. 表の外側の線分（LINE と LWPOLYLINE の各辺）をつなぐ。端点どうし、または端点が他の線分の上にある
     （T 字の分岐）ときだけつながる。交差しているだけの線はつながない
  3. 各行の左右の端から線をたどり、たどり着いた別の表の行と組にする
  4. たどった横線の少し上にある文字を、その線のラベル（'(BK)' 等）とする。削除線のある文字は除く
  5. 注記の「線番設定表」を含む行から '-634-07' の形（図番の中央部と末部）を抜く
"""
import re
import unicodedata

from .drawing_geometry import Seg
from .drawing_geometry import cluster as _cluster
from .drawing_geometry import is_h as _is_h
from .drawing_geometry import is_v as _is_v
from .drawing_geometry import norm

TOL = 0.3
PORT_X_TOL = 0.5   # 電線の端が表の左右の辺に着いたとみなす x 方向の許容（辺から 0.32 離れた行がある。EE3298-556-51A の GND-D）。線どうしの連結の TOL は上げない


def _key(s):
    return re.sub(r'\s+', '', norm(s)).upper()


def _ends(s):
    """線分の両端（向き付き）。"""
    return s.p


def _merge_v(vs):
    out = []
    for s in sorted(vs, key=lambda s: (round(s.x0, 2), s.y0)):
        if out and abs(out[-1].x0 - s.x0) < 0.02 and s.y0 <= out[-1].y1 + TOL:
            out[-1].y1 = max(out[-1].y1, s.y1)
        else:
            out.append(Seg(s.x0, s.y0, s.x1, s.y1, s.src))
    return out


def _text_cx(t):
    """文字の中心 x を、左端と字数（全角 1.0、半角 0.6 × 文字高）から見積もる。"""
    h = t.y1 - t.y0
    line = max(t.s.split('\n'), key=len)
    w = sum(1.0 if unicodedata.east_asian_width(c) in 'WF' else 0.6 for c in line) * h
    return t.x0 + min(w, t.x1 - t.x0) / 2


def find_columns(txts, segs):
    """見出し「線番」「PIN No.」の列。[{'kind', 'x0','x1','rows':[(y1,y0)], 'hdr':Txt}]"""
    hs = [s for s in segs if _is_h(s)]
    vs = _merge_v([s for s in segs if _is_v(s)])
    cols = []
    for h in txts:
        k = _key(h.s)
        kind = '線番' if k == '線番' else 'PIN' if k in ('PINNO.', 'PINNO') else None
        if not kind:
            continue
        # MTEXT の枠幅は実際の文字幅より広いことがあり、枠の中心が隣の列に入る（537-85C）。
        # 字数から幅を見積もった中心の左右で、最も近い縦線を列の端にする
        cx = _text_cx(h)
        left = [v for v in vs if v.x0 < cx and v.y0 <= h.cy <= v.y1]
        right = [v for v in vs if v.x0 > cx and v.y0 <= h.cy <= v.y1]
        if not left or not right:
            continue
        lv = max(left, key=lambda v: v.x0)
        rv = min(right, key=lambda v: v.x0)
        x0, x1 = lv.x0, rv.x0
        if x1 - x0 > 60:
            continue
        ybot, ytop = max(lv.y0, rv.y0), min(lv.y1, rv.y1)
        ys = _cluster([y for y in [s.y0 for s in hs if s.x0 <= x0 + TOL and s.x1 >= x1 - TOL]
                       if ybot - TOL <= y <= ytop + TOL])
        ys = sorted(ys, reverse=True)
        below = [y for y in ys if y < h.y0]
        if len(below) < 2:
            continue
        rows = list(zip(below, below[1:]))
        cols.append({'kind': kind, 'x0': x0, 'x1': x1, 'rows': rows, 'hdr': h, 'top': ytop})
    return cols + _headerless_columns(cols, hs, vs)


def _headerless_columns(cols, hs, vs):
    """見出しの無い表（見出しのある列と同じ左右の縦線を持ち、上下に離れて置かれた表）。

    599-40A では、見出しのある表の下に、同じ幅で見出しの無い小さな表（CNOSMG・CNWL）が続く。
    """
    extra = []
    for c in cols:
        lefts = [v for v in vs if abs(v.x0 - c['x0']) < 0.05]
        rights = [v for v in vs if abs(v.x0 - c['x1']) < 0.05]
        lo = c['rows'][-1][1]
        for lv in lefts:
            for rv in rights:
                y0, y1 = max(lv.y0, rv.y0), min(lv.y1, rv.y1)
                if y1 - y0 < 1 or not (y1 <= lo + TOL or y0 >= c['top'] - TOL):
                    continue
                if any(abs(e['x0'] - c['x0']) < 0.05 and abs(e['top'] - y1) < TOL for e in cols + extra):
                    continue
                ys = sorted(_cluster([y for y in [s.y0 for s in hs if s.x0 <= c['x0'] + TOL and s.x1 >= c['x1'] - TOL]
                                      if y0 - TOL <= y <= y1 + TOL]), reverse=True)
                if len(ys) < 2:
                    continue
                extra.append({'kind': c['kind'], 'x0': c['x0'], 'x1': c['x1'], 'rows': list(zip(ys, ys[1:])),
                              'hdr': None, 'top': y1})
    return extra


def _cell_text(txts, x0, x1, y1, y0):
    cell = [t for t in txts if x0 < t.cx < x1 and y0 < t.cy < y1]
    live = [norm(t.s).replace('\n', ' ') for t in sorted(cell, key=lambda t: -t.cy) if not t.struck]
    dead = [norm(t.s) for t in cell if t.struck]
    return ' | '.join(live), '/'.join(dead)


def build_tables(txts, cols):
    """隣り合い、行の区切りが上から同じ列を1つの表にまとめ、行ごとに 線番・PIN の値を持たせる。

    列の行数は違ってもよい（EE3298-545-28A の左の表 CNCM: 線番の列だけ下に1行多い）。表の行は最も長い列の行で、その行が無い列の値は空。
    以前は行数が同じ列だけをまとめたため、線番と PIN が別の表に分かれ、電線がつながる PIN の行に線番が無かった。"""
    cols = sorted(cols, key=lambda c: (round(c['rows'][0][0], 1), c['x0']))
    tables = []
    for c in cols:
        for t in tables:
            last = t['cols'][-1]
            if abs(last['x1'] - c['x0']) < TOL and all(abs(a[0] - b[0]) < TOL and abs(a[1] - b[1]) < TOL
                                                       for a, b in zip(last['rows'], c['rows'])):
                t['cols'].append(c)
                break
        else:
            tables.append({'cols': [c]})
    for t in tables:
        t['x0'] = min(c['x0'] for c in t['cols'])
        t['x1'] = max(c['x1'] for c in t['cols'])
        rows = max((c['rows'] for c in t['cols']), key=len)
        t['y0'], t['y1'] = rows[-1][1], t['cols'][0]['top']
        t['rows'] = []
        for i, (y1, y0) in enumerate(rows):
            r = {'i': i + 1, 'y1': y1, 'y0': y0}
            for c in t['cols']:
                if i >= len(c['rows']):        # この列には無い行（下に行が多い列がある）
                    v, dead = '', ''
                else:
                    v, dead = _cell_text(txts, c['x0'], c['x1'], y1, y0)
                key = c['kind'] if c['kind'] not in r else c['kind'] + '_2'
                r[key] = v
                r.setdefault('_refs', {})[key] = [(norm(x.s), x.ref) for x in txts
                                                  if i < len(c['rows']) and c['x0'] < x.cx < c['x1'] and y0 < x.cy < y1 and not x.struck]
                if dead:
                    r.setdefault('_deleted', {})[key] = dead
            t['rows'].append(r)
        # 表の名前（コネクタ名など）: 表のすぐ上にある文字
        above = [x for x in txts if t['x0'] - 2 < x.cx < t['x1'] + 2 and t['y1'] < x.y0 < t['y1'] + 6 and not x.struck]
        t['name'] = norm(min(above, key=lambda x: x.y0).s) if above else ''
    return tables


def _near(p, q):
    return abs(p[0] - q[0]) < TOL and abs(p[1] - q[1]) < TOL


def _on_seg(p, s):
    (ax, ay), (bx, by) = _ends(s)
    if not (min(ax, bx) - TOL <= p[0] <= max(ax, bx) + TOL and min(ay, by) - TOL <= p[1] <= max(ay, by) + TOL):
        return False
    cross = (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
    ln = max(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5, 1e-9)
    return abs(cross) / ln < TOL


def _grid(segs, cell=5.0):
    g = {}
    for i, w in enumerate(segs):
        for gx in range(int(w.x0 // cell) - 1, int(w.x1 // cell) + 2):
            for gy in range(int(w.y0 // cell) - 1, int(w.y1 // cell) + 2):
                g.setdefault((gx, gy), []).append(i)
    return g, cell


def _at_port(p, tables):
    return any(abs(p[0] - x) < PORT_X_TOL and r['y0'] + 0.1 < p[1] < r['y1'] - 0.1
               for t in tables for x in (t['x0'], t['x1']) for r in t['rows'])


def drop_arrows(wires):
    """矢印（短い斜めの2本が1点で会う）と、その先端に端がある線（引出線・寸法線の軸）を除く。

    例: 594-96B の 'UL AWG20' は両端が矢印の縦線で、2つのジャンパ線の上に乗っている。
    """
    def short_diag(w):
        (ax, ay), (bx, by) = _ends(w)
        ln = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        return ln < 4 and abs(bx - ax) > 0.05 and abs(by - ay) > 0.05
    heads = [w for w in wires if short_diag(w)]
    tips = []
    for i, a in enumerate(heads):
        for b in heads[i + 1:]:
            for p in _ends(a):
                if any(_near(p, q) for q in _ends(b)):
                    tips.append(p)
    if not tips:
        return wires
    drop = set()
    for i, w in enumerate(wires):
        if any(_near(p, t) for p in _ends(w) for t in tips):
            drop.add(i)
    return [w for i, w in enumerate(wires) if i not in drop]


def prune_dangling(wires, tables):
    """端が何にも触れていない線分を、なくなるまで繰り返し除く（引出線・外形線を消す）。"""
    alive = list(wires)
    while True:
        g, cell = _grid(alive)

        def touches(p, i):
            for j in g.get((int(p[0] // cell), int(p[1] // cell)), []):
                if j == i:
                    continue
                if any(_near(p, q) for q in _ends(alive[j])) or _on_seg(p, alive[j]):
                    return True
            return _at_port(p, tables)
        keep = [w for i, w in enumerate(alive) if all(touches(p, i) for p in _ends(w))]
        if len(keep) == len(alive):
            return keep
        alive = keep


def trace(tables, segs, txts, arc_ends=()):
    """表の行の端から線をたどって、行どうしの接続と線上のラベルを返す。"""
    def inside_table(s):
        return any(t['x0'] - TOL <= s.x0 and s.x1 <= t['x1'] + TOL and t['y0'] - TOL <= s.y0 and s.y1 <= t['y1'] + TOL
                   for t in tables)
    wires = [s for s in segs if not inside_table(s) and (s.x1 - s.x0) + (s.y1 - s.y0) > 0.05]
    # シールド記号の縦線（端が円弧の端と重なる）は電線ではない（599-40A では隣の電線どうしをつないでしまう）
    if arc_ends:
        g, cell = _grid([Seg(x, y, x, y, 'a') for x, y in arc_ends])

        def at_arc(p):
            return any(_near(p, arc_ends[j]) for j in g.get((int(p[0] // cell), int(p[1] // cell)), []))
        wires = [w for w in wires if not any(at_arc(p) for p in _ends(w))]
    wires = drop_arrows(wires)
    wires = prune_dangling(wires, tables)
    n = len(wires)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        ei = _ends(wires[i])
        for j in range(i + 1, n):
            ej = _ends(wires[j])
            if any(_near(p, q) for p in ei for q in ej) or any(_on_seg(p, wires[j]) for p in ei) \
                    or any(_on_seg(q, wires[i]) for q in ej):
                parent[find(i)] = find(j)
    ports = []   # (table_idx, row, side, comp)
    for ti, t in enumerate(tables):
        for r in t['rows']:
            for side, x in (('L', t['x0']), ('R', t['x1'])):
                for wi, w in enumerate(wires):
                    if any(abs(px - x) < PORT_X_TOL and r['y0'] + 0.1 < py < r['y1'] - 0.1 for px, py in _ends(w)):
                        ports.append((ti, r, side, find(wi)))
                        break
    table_txt = [x for x in txts if any(t['x0'] <= x.cx <= t['x1'] and t['y0'] <= x.cy <= t['y1'] for t in tables)]
    conns, by_comp = [], {}
    for p in ports:
        by_comp.setdefault(p[3], []).append(p)
    for comp, ps in by_comp.items():
        if len(ps) < 2:
            continue
        segs_c = [w for wi, w in enumerate(wires) if find(wi) == comp]
        labels, dead, label_refs = [], [], []
        for x in txts:
            if x in table_txt:
                continue
            if re.fullmatch(r'[☆★△▲]+', norm(x.s).strip()):
                continue   # 改訂の印はラベルではない
            for w in segs_c:
                if _is_h(w) and w.x0 - 0.5 <= x.cx <= w.x1 + 0.5 and -0.2 <= x.y0 - w.y0 <= 2.5:
                    (dead if x.struck else labels).append(norm(x.s))
                    if not x.struck:
                        label_refs.append((norm(x.s), x.ref))
                    break
        conns.append({'ends': ps, 'labels': list(dict.fromkeys(labels)), 'deleted': dead, 'nseg': len(segs_c), 'label_refs': label_refs,
                      'segs': [[tuple(p) for p in (s.p if s.p else ((s.x0, s.y0), (s.x1, s.y1)))] for s in segs_c]})
    return conns, ports


def parse_notes(txts):
    """注記の「線番設定表」を含む行から '-634-07' の形を抜く。[(パターン, 元の行)]"""
    out = []
    for t in txts:
        if t.struck:
            continue
        for line in norm(t.s).split('\n'):
            if '線番設定表' in line:
                for p in re.findall(r'-\d{3}-\d{2}', line):
                    if p not in [o[0] for o in out]:
                        out.append((p, line.strip()))
    return out


def is_note_block(text):
    """注記の文章ブロック（先頭行に「注記」がある。例: `(注記)\n1. 長さ(*)、コネクタ名称…`）。製作図の記載（部品種別・ケーブル種類・機器符号）の
    照合と強調の対象にしない（注記の文中の `Module` が種類 `UL` に部分一致するなど、記載ではない文字を拾うため）。"""
    return '注記' in text.split('\n')[0]


def mark_struck(txts, segs):
    """文字の中ほどを横切る短い横線がある文字を削除済みにする（表の罫線は文字の中を通らない）。"""
    hs = [s for s in segs if _is_h(s) and s.x1 - s.x0 < 80]
    for t in txts:
        hgt = t.y1 - t.y0
        for h in hs:
            if t.y0 + hgt * 0.3 <= h.y0 <= t.y0 + hgt * 0.7:
                ov = min(h.x1, t.x1) - max(h.x0, t.x0)
                if ov > 0.6 * (t.x1 - t.x0):
                    t.struck = True
                    break


def parse(geo):
    """Geometry からケーブル製作図を読む。

    Returns:
        {'tables': 線番/PIN の表, 'conns': 表の行どうしの接続, 'notes': [(線番設定表パターン, 注記の行)],
         'texts': 図面の全文字（削除済みを除き正規化した文字列。図中の参照の探索が使う）,
         'text_refs': 記載の文字と基準点（削除済み・注記ブロックを除く。部品種別・ケーブル種類・機器符号の照合と強調が使う）}
        線番/PIN の表が無い図面は tables が空。
    """
    txts = geo.copy_txts()
    segs = geo.lines + geo.polys
    cols = find_columns(txts, segs)
    tables = build_tables(txts, cols)
    # 表の罫線は削除線の判定から外す
    mark_struck(txts, [s for s in segs if not any(
        t['x0'] - TOL <= s.x0 and s.x1 <= t['x1'] + TOL and t['y0'] - TOL <= s.y0 <= t['y1'] + TOL for t in tables)])
    tables = build_tables(txts, cols)   # 削除の判定後に値を取り直す
    # 表が無ければ接続の端（ports）が無く、conns は必ず空になる。電線の連結（重い）を省く（展開接続図 405 を 393 として読む1段目が主な対象）
    conns = trace(tables, segs, txts, geo.arc_ends)[0] if tables else []
    return {'tables': tables, 'conns': conns, 'notes': parse_notes(txts),
            'texts': [norm(t.s) for t in txts if not t.struck],
            'text_refs': [(norm(t.s), t.ref) for t in txts if not t.struck and not is_note_block(norm(t.s))]}


def _side_of(t, tables):
    """表が図面の左右どちら寄りか（製作図は左が端末1）。"""
    mid = (min(tt['x0'] for tt in tables) + max(tt['x1'] for tt in tables)) / 2
    return '左' if (t['x0'] + t['x1']) / 2 < mid else '右'


def connection_rows(res):
    """接続を明細の行（dict）にする。`端N_…` は2つの端まで。3つ目以降は `ほかの端` にまとめる。"""
    tables = res['tables']
    out = []
    for c in res['conns']:
        vals = []
        for ti, r, side, _ in c['ends']:
            t = tables[ti]
            vals.append({'表': t['name'] or f'表{ti + 1}', '位置': _side_of(t, tables), '行': r['i'],
                         '線番': r.get('線番', ''), 'PIN No.': r.get('PIN', '')})
        rec = {'端の数': len(vals)}
        rec['_label_refs'] = c.get('label_refs', [])
        rec['_segs'] = c.get('segs', [])
        for k, (ti, r, _s, _c) in enumerate(c['ends'][:2], 1):
            rec[f'_端{k}_refs'] = r.get('_refs', {})
        for k, v in enumerate(vals[:2], 1):
            for kk, vv in v.items():
                rec[f'端{k}_{kk}'] = vv
        if len(vals) > 2:
            rec['ほかの端'] = '; '.join(f"{v['表']}[{v['行']}] 線番={v['線番']} PIN={v['PIN No.']}" for v in vals[2:])
        rec['ラベル'] = ' | '.join(c['labels'])
        rec['削除されたラベル'] = ' | '.join(c['deleted'])
        out.append(rec)
    return out
