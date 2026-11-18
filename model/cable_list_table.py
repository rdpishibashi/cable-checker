"""ケーブル一覧表（393 のサブタイトル「ケーブル一覧表」の図面）の表を読む（streamlit 非依存）。

実現性検討（docs/feasibility/cable_list_table/README.md）の試作の移植。

手順:
  1. 見出し「ケーブル製作図図面番号」を手がかりに表の罫線（縦線・横線）を決める
  2. 見出しの2段（上段=端末1/端末2/ケーブル仕様、下段=接続先 など）から列名を決める
  3. 文字を中心点でセルに割り当てる
  4. 文字の高さの中ほどを横切る横線がある文字は「削除」として捨てる（修正後の値は削除線の上・下・横のどこにあってもよい）
  5. 残った文字がセルに2つ以上あれば複数の値（OR／AND は決めない）。括弧で始まる値は直前の値につなぐ
"""
import re
from collections import Counter

from .drawing_geometry import cluster as _cluster
from .drawing_geometry import norm

FIELDS = [
    'ケーブル番号', '製作図パターン', '接続図パターン',
    '端末1_ユニット', '端末1_モジュール', '端末1_機器符号', '端末1_部品種別', '端末1_数量',
    '端末2_ユニット', '端末2_モジュール', '端末2_機器符号', '端末2_部品種別', '端末2_数量',
    'ケーブル種類', 'サイズ', '芯数', 'ケーブル備考',
]
SEP = ' | '


def fold_notes(vals):
    """括弧で始まる値は直前の値の補足としてつなぐ（例: 'DC PS1' + '(MASTER)' → 'DC PS1 (MASTER)'）。

    それ以外の複数の値は OR／AND を決めずに別の値として残す。先頭が括弧の値はそのまま。
    """
    out = []
    for v in vals:
        if out and v.startswith('('):   # NFKC 後なので全角の（ も半角になっている
            out[-1] = f'{out[-1]} {v}'
        else:
            out.append(v)
    return out


COLMAP = {
    ('', 'W No'): 'ケーブル番号',
    ('', 'ケーブル製作図図面番号'): '製作図パターン',
    ('', '接続図図面番号'): '接続図パターン',
    ('端末1', '接続先'): '端末1_ユニット',
    ('端末1', 'Module No'): '端末1_モジュール',
    ('端末1', 'コネクタ名称'): '端末1_機器符号',
    ('端末1', '端末部品'): '端末1_部品種別',
    ('端末1', '数量'): '端末1_数量',
    ('端末2', '接続先'): '端末2_ユニット',
    ('端末2', 'Module No'): '端末2_モジュール',
    ('端末2', 'コネクタ名称'): '端末2_機器符号',
    ('端末2', '端末部品'): '端末2_部品種別',
    ('端末2', '数量'): '端末2_数量',
    ('ケーブル仕様', 'ケーブル種類'): 'ケーブル種類',
    ('ケーブル仕様', 'サイズ'): 'サイズ',
    ('ケーブル仕様', '芯数'): '芯数',
    ('ケーブル仕様', '長さ'): '長さ',
    ('ケーブル仕様', 'メーカー'): 'メーカー',
    ('ケーブル仕様', 'W No'): 'W No(右)',
    ('', '備考欄'): 'ケーブル備考',
    ('ケーブル仕様', '備考欄'): 'ケーブル備考',
}


def _hdr_key(s):
    s = norm(s).replace('\n', ' ')
    s = re.sub(r'\s+', ' ', s)
    s = s.replace('.', '').strip()
    return s


def _read_table(txts, segs):
    """列・行を読む。表（見出し「ケーブル製作図図面番号」）が無ければ None。"""
    anchor = [t for t in txts if norm(t.s) == 'ケーブル製作図図面番号']
    if not anchor:
        return None
    a = anchor[0]
    hor = [s for s in segs if abs(s.y1 - s.y0) < 0.01 and s.x1 - s.x0 > 1e-6]
    ver = [s for s in segs if abs(s.x1 - s.x0) < 0.01 and s.y1 - s.y0 > 1e-6]
    # 見出しを含む縦線から表の縦範囲を決める（最も長いもの）
    from collections import Counter
    # 表の左右端 = 見出しの真下を通る長い横罫線の、最も多い両端
    spans = Counter((round(h.x0, 1), round(h.x1, 1)) for h in hor
                    if h.x0 <= a.cx <= h.x1 and h.y0 < a.cy and h.x1 - h.x0 > 100)
    left, right = spans.most_common(1)[0][0]
    # 上下端 = 表の内側で見出しを横切る縦罫線
    vcross = [v for v in ver if left + 0.5 < v.x0 < right - 0.5 and v.y0 <= a.cy <= v.y1 and v.y1 - v.y0 > 50]
    top = max(v.y1 for v in vcross)
    bottom = Counter(round(v.y0, 1) for v in vcross).most_common(1)[0][0]
    tv = [v for v in ver if v.y0 <= bottom + 0.5 and v.y1 >= bottom + 50 and left - 0.5 <= v.x0 <= right + 0.5]
    width = right - left
    xs = _cluster([left, right] + [v.x0 for v in tv])
    # 行の区切り = 最左列（符号）を横切る横線。結合セルの列では線が途切れる
    c0 = (xs[0] + xs[1]) / 2
    grid_h = [h for h in hor if h.x0 <= c0 <= h.x1 and bottom - 0.5 <= h.y0 <= top + 0.5]
    ys = _cluster([top, bottom] + [h.y0 for h in grid_h])
    ys = sorted(ys, reverse=True)
    def has_line(y, xm):
        return any(abs(h.y0 - y) < 0.2 and h.x0 - 0.2 <= xm <= h.x1 + 0.2 for h in hor)
    # 見出し帯 = 見出し文字より下の最初の横罫線まで
    sub_hdr = [t for t in txts if norm(t.s) in ('接続先', 'ケーブル種類', '備考欄') and left < t.cx < right]
    hdr_bottom = max(y for y in ys if y < min(t.y0 for t in sub_hdr))
    # 上段の区切り（見出しの最上部まで届く縦線）
    top_split = _cluster([v.x0 for v in tv if v.y1 >= top - 0.5] + [left, right])
    hdr_txt = [t for t in txts if hdr_bottom < t.cy < top and left < t.cx < right]
    mid_y = max(t.cy for t in hdr_txt if norm(t.s) in ('接続先', '備考欄'))
    # 上段の見出し（端末1 等）: 下段見出しより上にある文字
    groups = []
    for x0, x1 in zip(top_split, top_split[1:]):
        g = [norm(t.s) for t in hdr_txt if x0 < t.cx < x1 and t.y0 > mid_y + 0.5]
        groups.append((x0, x1, g[0] if len(g) == 1 and g[0] in ('端末1', '端末2', 'ケーブル仕様') else ''))
    cols = []
    for x0, x1 in zip(xs, xs[1:]):
        g = next((gg for a0, a1, gg in groups if a0 - 0.1 <= x0 and x1 <= a1 + 0.1), '')
        names = [t for t in hdr_txt if x0 < t.cx < x1 and (not g or t.cy <= mid_y + 1)]
        names = [_hdr_key(t.s) for t in names if norm(t.s) not in ('端末1', '端末2', 'ケーブル仕様')]
        sub = ' '.join(names)
        cols.append((x0, x1, g, sub, COLMAP.get((g, sub))))
    rows = [(y1, y0) for y1, y0 in zip(ys, ys[1:]) if y1 <= hdr_bottom + 0.01]

    # 削除線: 罫線でない短い横線が文字の高さの中ほど（25%〜75%）を横切る
    # 罫線（行の区切りの高さ）に載っていない横線。行全体を消す長い線もある
    cand = [h for h in hor if bottom < h.y0 < top and not any(abs(h.y0 - y) < 0.2 for y in ys)]
    for t in txts:
        if not (left < t.cx < right and bottom < t.cy < hdr_bottom):
            continue
        hgt = t.y1 - t.y0
        cx0 = max(x for x in xs if x <= t.cx)
        cx1 = min(x for x in xs if x >= t.cx)
        for h in cand:
            if t.y0 + hgt * 0.25 <= h.y0 <= t.y0 + hgt * 0.75:
                ov = min(h.x1, t.x1) - max(h.x0, t.x0)
                if ov > 0.5 * min(h.x1 - h.x0, t.x1 - t.x0):
                    t.struck = True
                    break

    # 列ごとに、区切り線の無い行どうしを結合したセル（行の添字の範囲）を作る
    nrow = len(rows)
    table = [{'_row': i + 1, '_deleted': {}, '_merged': []} for i in range(nrow)]
    for (x0, x1, g, sub, key) in cols:
        xm = (x0 + x1) / 2
        name = key or f'?{g}/{sub}'
        i = 0
        while i < nrow:
            j = i
            while j + 1 < nrow and not has_line(rows[j][1], xm):
                j += 1
            ry1, ry0 = rows[i][0], rows[j][1]
            cell = [t for t in txts if x0 < t.cx < x1 and ry0 < t.cy < ry1]
            cell.sort(key=lambda t: -t.cy)
            live = [t for t in cell if not t.struck]
            dead = [t for t in cell if t.struck]
            # 修正後の値は削除線の直下・直上・横のいずれにもある。位置は問わず、消されていない文字を採る
            if key == 'ケーブル備考':
                vals = [' '.join(norm(l) for l in t.s.split('\n')) for t in live]
            else:   # 明示的な改行（MTEXT の \P）と、別々の文字はどちらも複数候補
                vals = fold_notes([norm(l) for t in live for l in t.s.split('\n') if norm(l)])
            for k in range(i, j + 1):
                if cell:
                    table[k][name] = vals
                    if dead:
                        table[k]['_deleted'][name] = [norm(t.s) for t in dead]
                if j > i:
                    table[k]['_merged'].append(name)
            i = j + 1
    table = [r for r in table
             if any(v for k, v in r.items() if not k.startswith('_') and k != '?/符号')]
    return {'cols': cols, 'rows': table, 'bbox': (left, bottom, right, top)}


def parse(geo):
    """Geometry から一覧表の行を読む。

    Returns:
        表が無いとき None。あるとき {'rows': [{'行': n, <FIELDS>: ' | ' 区切りの文字列, '削除された値': str}, ...]}
    """
    res = _read_table(geo.copy_txts(), geo.lines)
    if res is None:
        return None
    rows = []
    for r in res['rows']:
        row = {'行': r['_row']}
        for k in FIELDS:
            row[k] = SEP.join(r.get(k, []))
        row['削除された値'] = '; '.join(f"{k}:{'/'.join(v)}" for k, v in r['_deleted'].items())
        if not any(row[k] for k in FIELDS) and r['_deleted']:
            row['削除された値'] = '【行ごと削除】' + row['削除された値']
        rows.append(row)
    return {'rows': rows}
