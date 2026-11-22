"""viewer 用の強調（文字の位置と記号、図形）を、照合の行から作る（モデル層。streamlit 非依存）。

`consistency._evaluate_row()` が、行の照合を終えたあとに `row_marks()`・`row_shapes()` を呼ぶ。照合の記号・根拠は変えない（読むだけ）。
このモジュールは `consistency` を import しない（`consistency` → `highlights` の一方向）。照合のセル（`consistency._Cell`）は、
`comparisons`（[(図面の種別, 記号, 根拠)]）・`symbol`・`decided`（展開接続図でユニットを決めた文字）の属性を読むだけで、型には依存しない。
結果は行の `marks`・`shapes`（Excel には出ない）になり、`cable_focus` が図面ごとにまとめ、`cable_preview` が viewer のデータにする。
"""
import re

from .cell_values import awg_sizes, code_matches_name, codes as _codes, is_color as _is_color, item_column, side as _side, size_key, squash as _squash, values as _values
from .symbols import NA, OK, worst as _worst


def _cmp(cells, col, kind):
    """項目 col のセルのうち、図面の種別 kind の比較の記号（複数なら重いほう）。無ければ None。"""
    syms = [s for c in cells[col] for k, s, _ in c.comparisons if k == kind]
    return _worst(syms) if syms else None


def _combine(*syms):
    syms = [s for s in syms if s]
    return _worst(syms) if syms else NA


def _marks_of(drawing, refs, symbol, item):
    return [{'drawing': drawing, 'anchor': ref, 'text': text, 'symbol': symbol, 'item': item}
            for text, ref in refs or [] if ref is not None]


def row_marks(list_name, row, cells, ch, sel, pick, mat, fab_conns):
    """一覧表の1行の照合結果に対応する、図面上の文字の強調（viewer 用）。

    [{'drawing': 図番, 'anchor': 文字の基準点, 'text': 文字, 'symbol': 記号, 'item': 項目}]。照合の記号・根拠は変えない。
    記号は、その文字を比べた比較（図面の種別ごと）の記号。比べていない文字は含めない（cable_focus が「関連」にする）。"""
    out = []
    col = item_column
    sym_of = lambda *cols: _worst([c.symbol for k in cols for c in cells[k]]) if cols else NA  # noqa: E731
    # --- 一覧表: セルごとの記号（最も重い比較）
    refs = row.get('_refs', {})
    list_map = [(('図面の対応', '一覧表'), ['ケーブル番号']), (('図面の対応', '製作図'), ['製作図パターン']),
                (('図面の対応', '展開接続図'), ['接続図パターン']), (('ケーブル', '種類'), ['ケーブル種類']), (('ケーブル', 'サイズ'), ['サイズ']),
                (('ケーブル', '芯数'), ['芯数'])]
    for i in (1, 2):
        list_map += [((f'端末{i}', n), [f'端末{i}_{n}']) for n in ('ユニット', 'モジュール', '機器符号', '部品種別')]
    for (g, n), fields in list_map:
        for f in fields:
            out += _marks_of(list_name, refs.get(f), sym_of(col(g, n)), f'{g}｜{n}')
    # --- 線番設定表（選んだ行）
    num_wire = _cmp(cells, col('芯線', '線番'), '線番設定表')
    for x in sel:
        dn, wrefs = x['drawing']['drawing_number'], x['row'].get('_refs', {})
        out += _marks_of(dn, wrefs.get('ケーブル番号'), _combine(_cmp(cells, col('図面の対応', '線番設定表'), '線番設定表')), '図面の対応｜線番設定表')
        for i in (1, 2):
            sym = _combine(*(_cmp(cells, col(f'端末{i}', n), '線番設定表') for n in ('ユニット', 'モジュール', '機器符号')))
            out += _marks_of(dn, wrefs.get(f'接続先{_side(x["orient"], i) + 1}'), sym, f'端末{i}｜接続先')
        sym = _combine(num_wire)                 # 線番の値の記号は線番の判定だけ（芯数の判定は混ぜない。一覧表の芯数の文字に出る）
        for k in x['row']:
            if re.fullmatch(r'線番\d+', k) and x['row'][k]:
                out += _marks_of(dn, wrefs.get(k), sym, '芯線｜線番')
    # --- ケーブル製作図
    for j, (d, c) in enumerate(fab_conns):
        r = mat['rows'][j] if j < len(mat['rows']) else None
        dn = d['drawing_number']
        pin_sym = r['判定_PIN'] if r else NA
        num_sym = _combine(r['判定_線番'] if r and r['判定_線番'] != NA else None, num_wire)
        for k in (1, 2):
            r_refs = c.get(f'_端{k}_refs', {})
            out += _marks_of(dn, r_refs.get('PIN'), pin_sym, '芯線｜PIN')
            out += _marks_of(dn, r_refs.get('線番'), num_sym, '芯線｜線番')
        for text, ref in c.get('_label_refs', []):
            out += _marks_of(dn, [(text, ref)], (r['判定_色'] if r and _is_color(text) else NA), '芯線｜色')
    for d in ch['fabs']:
        dn = d['drawing_number']
        for g, n, field in (('端末1', '部品種別', '端末1_部品種別'), ('端末2', '部品種別', '端末2_部品種別'), ('ケーブル', '種類', 'ケーブル種類'),
                            ('端末1', '機器符号', '端末1_機器符号'), ('端末2', '機器符号', '端末2_機器符号')):
            sym = _cmp(cells, col(g, n), '製作図')
            if sym is None:
                continue
            for v in (_codes if n == '機器符号' else _values)(row.get(field)):
                key = _squash(v)
                if n == '機器符号':      # 製作図の文字1つ全体との一致（照合と同じ規則）
                    hit = [(t, ref) for t, ref in d.get('fab_text_refs', []) if code_matches_name(v, t)]
                else:
                    hit = [(t, ref) for t, ref in d.get('fab_text_refs', []) if key and key in _squash(t)]
                out += _marks_of(dn, hit, sym, f'{g}｜{n}')
        # サイズ: ○ のときは一覧表のサイズに合う AWG の文字だけ、× のときは AWG の数字を含む文字すべて（アース線の AWG を含む）
        size_sym = _cmp(cells, col('ケーブル', 'サイズ'), '製作図')
        if size_sym is not None:
            keys = {size_key(v) for v in _values(row.get('サイズ'))}
            hit = [(t, ref) for t, ref in d.get('fab_text_refs', [])
                   if awg_sizes(t) and (size_sym != OK or awg_sizes(t) & keys)]
            out += _marks_of(dn, hit, size_sym, 'ケーブル｜サイズ')
    # --- 展開接続図（照合に使った出現）
    if pick is not None:
        dn, occ, orient = pick['drawing']['drawing_number'], pick['occ'], pick['orient']
        label_sym = _combine(_cmp(cells, col('図面の対応', '展開接続図'), '展開接続図'), _cmp(cells, col('ケーブル', '種類'), '展開接続図'))
        if _cmp(cells, col('ケーブル', 'サイズ'), '展開接続図') and not occ.get('_remark_ref'):
            label_sym = _combine(label_sym, _cmp(cells, col('ケーブル', 'サイズ'), '展開接続図'))   # AWG はラベル自身の文字（番号の後ろ）にある
        out += _marks_of(dn, [(occ['cable'], occ.get('_label_ref'))], label_sym, '図面の対応｜展開接続図')
        # ラベルのすぐ下の別の文字を備考にしたとき（`ACCESSORY CABLE`）は、その文字を「ケーブル｜種類」の展開接続図の比較の記号で強調する
        remark_sym = _cmp(cells, col('ケーブル', '種類'), '展開接続図')
        # 備考の AWG の数字（サイズの比較）: ラベルの下の別の文字にあればその文字を、ラベル自身にあればラベルの強調に重ねる
        size_sym = _cmp(cells, col('ケーブル', 'サイズ'), '展開接続図')
        remark_ref = occ.get('_remark_ref')
        if remark_ref and size_sym and awg_sizes(remark_ref[0]):
            remark_sym = _combine(remark_sym, size_sym)
        if remark_ref and remark_sym:
            out += _marks_of(dn, [remark_ref], remark_sym, 'ケーブル｜種類')
        for i in (1, 2):
            side = orient['sides'][i]
            code_sym = _combine(_cmp(cells, col(f'端末{i}', '機器符号'), '展開接続図'))
            list_codes = _codes(row.get(f'端末{i}_機器符号'))
            for w in occ['wires']:
                e = w['ends'].get(side)
                if not e or e['kind'] is None:
                    continue
                e_refs = e.get('_refs', {})
                if e['name'] and e_refs.get('name') is not None:
                    # 一覧表の機器符号のどれかに一致する名前は ○（セルが × でも、合っている名前まで赤くしない。M06021 の CNCM）。合わない名前はセルの記号
                    hit = code_sym != NA and any(code_matches_name(c, e['name']) for c in list_codes)
                    out += _marks_of(dn, [(e['name'], e_refs['name'])], OK if hit else code_sym, f'端末{i}｜機器符号')
            # ユニットを決めた文字（照合のときに記録したもの。ここで判定し直さない）。色はユニットの判定だけ（モジュールの ？・× は一覧表のモジュールのセルに残す）
            for sym, text in cells[col(f'端末{i}', 'ユニット')][0].decided:
                first = text.split('\n')[0]
                for w in occ['wires']:
                    e = w['ends'].get(side)
                    if not e:
                        continue
                    e_refs = e.get('_refs', {})
                    ref = e_refs.get('regions', {}).get(text) or e_refs.get('devices', {}).get(text) or e_refs.get('nearby', {}).get(first)
                    if ref is not None:
                        out += _marks_of(dn, [(text, ref)], sym, f'端末{i}｜ユニット')
                        break
        for r in mat['rows']:
            w = (r.get('wire') or {}).get('src')
            if not w:
                continue
            for side in '-+':
                e = w['ends'].get(side)
                if e and e.get('_refs', {}).get('pin') is not None:
                    out += _marks_of(dn, [(e['pin'], e['_refs']['pin'])], r['判定_PIN'], '芯線｜PIN')
            for text, ref in list(zip(w['labels'], w.get('_label_refs', [])))[:2]:
                out += _marks_of(dn, [(text, ref)], r['判定_色'] if _is_color(text) else r['判定_線番'], '芯線｜' + ('色' if _is_color(text) else '線番'))
    return out


def _shape(drawing, kind, geom, symbol, item):
    return {'drawing': drawing, 'kind': kind, 'geom': geom, 'symbol': symbol, 'item': item}


def _wire_symbol(r):
    """芯線・接続の図形の記号: PIN・線番・色の判定のうち、－ を除いて最も重いもの（全部 － なら －）。"""
    syms = [r[k] for k in ('判定_PIN', '判定_線番', '判定_色') if r[k] != NA]
    return _worst(syms) if syms else NA


def row_shapes(cells, pick, mat, fab_conns, row=None):
    """一覧表の1行の照合結果に対応する、図面上の図形の強調（viewer 用）。

    [{'drawing', 'kind': 'lines'|'ellipse'|'capsule'|'rect'|'circle', 'geom', 'symbol', 'item'}]（geom は DXF 座標。lines は線分の
    リスト、ellipse・capsule・rect は外接矩形、circle は (中心, 半径)）。照合の記号・根拠は変えない。
    製作図: 接続の線＝その接続の判定。展開接続図: 引出線・記号＝「図面の対応｜展開接続図」、芯線＝割り当てた接続の判定（**両端に端末が
    ある芯線だけ**。端末の無い芯線は塗らない）、端末＝「端末｜機器符号」（端の名前が一覧表の機器符号のどれかに一致すれば ○。文字の強調と同じ規則）。
    機器の箱は塗らない。"""
    out = []
    for j, (d, c) in enumerate(fab_conns):
        r = mat['rows'][j] if j < len(mat['rows']) else None
        if c.get('_segs'):
            out.append(_shape(d['drawing_number'], 'lines', c['_segs'], _wire_symbol(r) if r else NA, '芯線'))
    if pick is None:
        return out
    dn, occ, orient = pick['drawing']['drawing_number'], pick['occ'], pick['orient']
    sym = _combine(_cmp(cells, item_column('図面の対応', '展開接続図'), '展開接続図'))
    if occ.get('_leader'):
        out.append(_shape(dn, 'lines', occ['_leader'], sym, '図面の対応｜展開接続図'))
    for s in occ.get('_symbols', []):
        out.append(_shape(dn, s['shape'], s['box'], sym, '図面の対応｜展開接続図'))
    shield = occ.get('_shield')
    shield_rows = [r for r in mat['rows'] if r.get('shield')]
    if shield and shield_rows:                              # 製作図の SG/SH と一致したシールド（破線と端の楕円。接地記号は含めない）
        sh_sym = _worst([r['判定_線番'] for r in shield_rows])
        out.append(_shape(dn, 'lines', shield['lines'], sh_sym, 'シールド'))
        for box in shield['ellipses']:
            out.append(_shape(dn, 'ellipse', box, sh_sym, 'シールド'))
    assigned = {id(r['wire']['src']): r for r in mat['rows'] if r.get('wire')}
    for w in occ['wires']:
        if not all((w['ends'].get(s) or {}).get('kind') for s in '-+'):
            continue                                       # 端末の無い芯線は塗らない（取りこぼしより誤りを避ける）
        r = assigned.get(id(w))
        if w.get('_path'):
            out.append(_shape(dn, 'lines', w['_path'], _wire_symbol(r) if r else NA, '芯線'))
    for i in (1, 2):
        code_sym = _combine(_cmp(cells, item_column(f'端末{i}', '機器符号'), '展開接続図'))
        list_codes = _codes((row or {}).get(f'端末{i}_機器符号'))
        for w in occ['wires']:
            e = w['ends'].get(orient['sides'][i]) or {}
            shape = e.get('_shape')
            hit = code_sym != NA and bool(e.get('name')) and any(code_matches_name(c, e['name']) for c in list_codes)
            sym = OK if hit else code_sym
            if shape and 'rect' in shape:
                out.append(_shape(dn, 'rect', shape['rect'], sym, f'端末{i}｜機器符号'))
            elif shape and 'circle' in shape:
                out.append(_shape(dn, 'circle', shape['circle'], sym, f'端末{i}｜機器符号'))
    return out
