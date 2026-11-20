"""線番設定表（393 の図面。見出し「線番(*1)」の表）と注記の製作図パターンを読む（streamlit 非依存）。

実現性検討（docs/feasibility/wire_number_table/README.md）の試作の移植。

手順:
  1. 見出し「線番(*1)」を手がかりに表を探す（1枚に2表あることがある）
  2. 見出しのすぐ下の横罫線（途切れた線はつないでから）で表の左右端を決め、
     その範囲で見出しを横切る縦罫線を列の区切り、最左列を横切る横罫線を行の区切りにする
  3. 見出しの文字で列名を決める（W No.／線番(*n)／接続先 左・右）。線番は (*4)(*5) などある分だけ列にする
  4. 文字を中心点でセルに割り当て、削除線のある文字を除く
  5. 注記の中で「ケーブル製作図」を含む行から EE で始まる図番パターンを抜く

`split_unit_terminal()`・`unit_match()` は一覧表との照合（consistency.py）が使う。
"""
import re
from collections import Counter

from .drawing_geometry import cluster as _cluster
from .drawing_geometry import crosses_text_middle as _crosses_text_middle
from .drawing_geometry import merge_segments as _merge
from .drawing_geometry import norm

DRAWING_PATTERN = re.compile(r'EE[0-9*]{4}-[0-9*]{3}(?:-[0-9*]{2})?[A-Z*]?')


def _key(s):
    return re.sub(r'\s+', '', norm(s)).upper()


def _col_name(hdr):
    k = _key(hdr).replace('.', '')
    if k == 'WNO':
        return 'ケーブル番号'
    m = re.fullmatch(r'線番\(\*(\d+)\)', k)
    if m:
        return f'線番{m.group(1)}'
    if k == '接続先':
        return '接続先'
    return None


def _value(name, live):
    """セルの値。ケーブル番号・線番は改行／別の文字を複数の値（' | '）、接続先は空白でつなぐ。

    接続先の改行は折り返し（'SYSTEM I/F⏎BOX'）か、ユニット名＋端子台（'RACK⏎TBF1B'）で、1つの値。
    """
    lines = [norm(l) for t in live for l in t.s.split('\n') if norm(l)]
    return ' '.join(lines) if name.startswith('接続先') else ' | '.join(lines)


def parse_tables(txts, segs):
    hor = _merge([s for s in segs if abs(s.y1 - s.y0) < 0.01 and s.x1 > s.x0], True)
    ver = _merge([s for s in segs if abs(s.x1 - s.x0) < 0.01 and s.y1 > s.y0], False)
    tables, warns = [], []
    for a in [t for t in txts if _key(t.s) == '線番(*1)']:
        below = [h for h in hor if h.x0 <= a.cx <= h.x1 and h.y0 < a.y0]
        above = [h for h in hor if h.x0 <= a.cx <= h.x1 and h.y0 > a.y1]
        if not below or not above:
            warns.append(f'見出し {a.s!r} の上下に罫線が無い')
            continue
        hb = max(below, key=lambda h: h.y0)              # 見出しの下の線
        htop = min(h.y0 for h in above)                   # 見出しの上の線
        left, right = hb.x0, hb.x1
        width = right - left
        vs = [v for v in ver if left - 0.3 <= v.x0 <= right + 0.3 and v.y0 < hb.y0 and v.y1 >= a.cy]
        xs = _cluster([left, right] + [v.x0 for v in vs])
        bottom = Counter(round(v.y0, 1) for v in vs).most_common(1)[0][0]
        inside = [t for t in txts if left < t.cx < right and bottom < t.cy < hb.y0]
        # 表の中の横線を「削除線」と「罫線」に分ける。文字の中ほどを横切るものは削除線
        grid = []
        for h in hor:
            if not (bottom - 0.3 <= h.y0 <= hb.y0 + 0.3) or h.x1 < left or h.x0 > right:
                continue
            hit = _crosses_text_middle(h, inside)
            if hit:
                for t in hit:
                    t.struck = True
            else:
                grid.append(h)

        def cover(y):
            return sum(min(h.x1, right) - max(h.x0, left) for h in grid if abs(h.y0 - y) < 0.05)
        # 行の区切り = 表の幅の半分以上を覆う罫線の高さ（結合セルの列では途切れていてよい）
        row_ys = sorted(_cluster([hb.y0, bottom] + [h.y0 for h in grid if cover(h.y0) >= width * 0.5]),
                        reverse=True)

        def has_line(y, xm):
            return any(abs(h.y0 - y) < 0.2 and h.x0 - 0.2 <= xm <= h.x1 + 0.2 for h in grid)
        cols, n_conn = [], 0
        for x0, x1 in zip(xs, xs[1:]):
            hdr = ' '.join(t.s for t in txts if x0 < t.cx < x1 and hb.y0 < t.cy < htop)
            name = _col_name(hdr)
            if name == '接続先':
                n_conn += 1
                name = f'接続先{n_conn}'
            cols.append((x0, x1, name, norm(hdr)))
        rows_band = list(zip(row_ys, row_ys[1:]))
        n = len(rows_band)
        recs = [{'_row': i + 1, '_deleted': {}} for i in range(n)]
        for x0, x1, name, hdr in cols:
            if not name:
                continue
            xm = (x0 + x1) / 2
            i = 0
            while i < n:               # 区切り線の無い行どうしを結合したセル
                j = i
                while j + 1 < n and not has_line(rows_band[j][1], xm):
                    j += 1
                y1, y0 = rows_band[i][0], rows_band[j][1]
                cell = sorted([t for t in inside if x0 < t.cx < x1 and y0 < t.cy < y1], key=lambda t: -t.cy)
                live = [t for t in cell if not t.struck]
                dead = [t for t in cell if t.struck]
                for k in range(i, j + 1):
                    recs[k][name] = _value(name, live)
                    recs[k].setdefault('_refs', {})[name] = [(t.s, t.ref) for t in live]   # 文字の位置（viewer の強調に使う）
                    if name.startswith('接続先'):   # 一覧表との照合でユニット名／端子台に分けるため行を残す
                        recs[k].setdefault('_lines', {})[name] = [
                            norm(l) for t in live for l in t.s.split('\n') if norm(l)]
                    if dead:
                        recs[k]['_deleted'][name] = '/'.join(norm(t.s) for t in dead)
                i = j + 1
        for r in recs:
            r.setdefault('_lines', {})
        rows = [r for r in recs if any(v for k, v in r.items() if not k.startswith('_'))]
        whole = [r['_row'] for r in recs
                 if r['_deleted'] and not any(v for k, v in r.items() if not k.startswith('_'))]
        tables.append({'bbox': (left, bottom, right, htop), 'cols': cols, 'rows': rows, 'deleted_rows': whole})
    tables.sort(key=lambda t: (t['bbox'][0], -t['bbox'][3]))
    return tables, warns


def parse_notes(txts, hor=()):
    """注記のうち「ケーブル製作図」を含む行から EE 図番パターンを抜く。

    注記は「注記)」の見出しと本文が1つの MTEXT のことも、別の文字のこともある。
    見出しを含む文字と、見出しの下（左端がほぼ同じ・80 以内）にある文字を本文とみなす。
    Returns: ([(パターン, 元の行)], 注記見出しの数)
    """
    titles = [t for t in txts if re.match(r'^\(?注記\)?', norm(t.s))]
    body = []
    for ti in titles:
        body.append(ti)
        body += [t for t in txts if t is not ti and t.y1 <= ti.y1 and ti.y0 - t.y1 < 80
                 and abs(t.x0 - ti.x0) < 15]
    # 注記の中の1行の文字に削除線があれば除く（例: 634-07E の '(EE****-507-01)'）
    for t in body:
        if '\n' not in t.s and any(_crosses_text_middle(h, [t]) for h in hor):
            t.struck = True
    found, seen = [], set()
    for t in body:
        if id(t) in seen:
            continue
        seen.add(id(t))
        if t.struck:
            continue
        for line in norm(t.s).split('\n'):
            if 'ケーブル製作図' in line:
                for p in DRAWING_PATTERN.findall(line):
                    if p not in [f[0] for f in found]:
                        found.append((p, line.strip()))
    return found, len(titles)


def parse(geo):
    """Geometry から線番設定表を読む。

    Returns:
        {'tables': [{'rows': [{'_row', ケーブル番号, 線番n, 接続先1, 接続先2, '_lines', '_deleted'}], ...}],
         'notes': [(製作図パターン, 注記の行)], 'warnings': [str]}
        表が無い図面は tables が空。
    """
    txts = geo.copy_txts()
    tables, warns = parse_tables(txts, geo.lines)
    hor = [s for s in geo.lines if abs(s.y1 - s.y0) < 0.01 and s.x1 > s.x0]
    notes, n_titles = parse_notes(txts, hor)
    if not n_titles:
        warns.append('注記の見出しが無い')
    return {'tables': tables, 'notes': notes, 'warnings': warns}


# ---- 一覧表との照合に使う（consistency.py） ----

def _base(u):
    """ユニット名の比較用: 括弧書きの補足と空白を除いて大文字に。"""
    return re.sub(r'\s+', '', re.sub(r'\(.*?\)', '', u)).upper()


def unit_match(wire_unit, list_unit):
    """'一致'／'近似'／''。近似は一方が他方の先頭部分（'MPD RACK'と'MPD RACK1'、'ALS1 F1'と'ALS1'）。"""
    a, b = _base(wire_unit), _base(list_unit)
    if not a or not b:
        return ''
    if a == b and wire_unit.upper() == list_unit.upper():
        return '一致'
    if a == b or a.startswith(b) or b.startswith(a):
        return '近似'
    return ''


# 端子台らしい行: 英字2字以上で始まり数字を含む語（'TBF1B'・'CB081AB'・'CNPS1F1CIN'）、'or' で並べたもの
# （'ELB087 or CB087'）。'F1'・'R2' のような英字1字＋数字はモジュール位置なのでユニット名側に残す。
_TERM = r'[A-Z]{2,}[0-9A-Z*]*\d[0-9A-Z*]*'
TERMINAL_RE = re.compile(rf'{_TERM}(?: or {_TERM})*')


def split_unit_terminal(lines, list_units, list_codes=()):
    """接続先の行を (ユニット名, 端子台) に分ける。

    - 括弧で始まる行は直前の行の補足（'DC PS1 F1⏎(MASTER)' → ユニット 'DC PS1 F1 (MASTER)'）
    - 全行をつないだ名前が一覧表のユニット名と同じなら、折り返し（'SYSTEM I/F⏎BOX'）でユニット名だけ
    - 全行が端子台らしい（'BS03⏎LS03'）ときは機器の並びなのでユニット名側
    - 2行以上で最後の行が端子台らしい（TERMINAL_RE）か一覧表の機器符号なら、最後の行を端子台、
      残りをユニット名（'RACK⏎TBF1B'、'MPD RACK⏎TBPS1F1A'）
    - それ以外はすべてユニット名（'GAS BOX⏎F1' のモジュール位置など）
    """
    folded = []
    for ln in lines:
        if folded and ln.startswith('('):
            folded[-1] += ' ' + ln
        else:
            folded.append(ln)
    whole = ' '.join(folded)
    if len(folded) <= 1 or any(_base(whole) == _base(u) for u in list_units):
        return whole, ''
    last = folded[-1]
    if all(TERMINAL_RE.fullmatch(x) for x in folded):
        return whole, ''
    if last in list_codes or TERMINAL_RE.fullmatch(last):
        return ' '.join(folded[:-1]), last
    return whole, ''
