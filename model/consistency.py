"""393 の整合性チェック: ケーブル一覧表 × 線番設定表 × ケーブル製作図（streamlit 非依存）。

2段に分ける。
  1. `analyze_drawing(path, name)` — DXF を1回だけ読み、3種類の読み取りを走らせて、図面ごとの dict にする
  2. `check_consistency(drawings)` — 全図面の dict から、ケーブル番号ごとの照合 0〜7 の行を作る

照合の定義は docs/handoff_consistency_check.md の表が正。チェーンは
一覧表の行 →（製作図パターン）→ 製作図 →（注記の線番設定表パターン）→ 線番設定表。
`check_consistency()` は dict だけを受け取るので、合成データでそのままテストできる。

図面の dict（`analyze_drawing()` の戻り値）:
  drawing_number, no_text（文字が1つも無い）, kinds（'ケーブル一覧表'・'線番設定表'・'ケーブル製作図'）,
  list_rows（一覧表の行。`cable_list_table.parse()` の rows）,
  wire_rows（線番設定表の行。`'_lines'` に接続先ごとの行）, wire_notes（注記の製作図パターン）,
  fab_wire_numbers（製作図の線番の表にある `*n` の n）, fab_notes（注記の線番設定表パターン）,
  fab_texts（図面の全文字。削除済みを除く）, fab_text（その文字を空白なし・大文字でつなげたもの。照合2が使う）,
  fab_conns（製作図の接続の明細）
"""
import os
import re
import tempfile

from . import cable_list_table, fab_drawing, wire_number_table
from .drawing_geometry import load

OK, NG, NA = 'OK', 'NG', '対象なし'
SEP = cable_list_table.SEP

KIND_LIST = 'ケーブル一覧表'
KIND_WIRE = '線番設定表'
KIND_FAB = 'ケーブル製作図'

C0 = '0 一覧表の記載'
C1 = '1 製作図の図面'
C2_PARTS = [('端末1_部品種別', '端末1 部品種別'), ('端末2_部品種別', '端末2 部品種別'), ('ケーブル種類', 'ケーブル種類')]
C2 = [f'2 製作図の記載（{label}）' for _, label in C2_PARTS]
C3 = '3 線番設定表の図面'
C4 = '4 線番設定表の記載'
C5 = '5 線番設定表の製作図'
C6A = '6a 接続先のユニット名'
C6B = '6b 接続先の端子台'
C7 = '7 線番の列'
CHECK_NAMES = [C0, C1, *C2, C3, C4, C5, C6A, C6B, C7]



# ---------------------------------------------------------------- 1. 図面ごとの解析

def _empty_drawing(name, no_text=False):
    return {'drawing_number': name, 'no_text': no_text, 'kinds': [], 'list_rows': [], 'wire_rows': [],
            'wire_notes': [], 'fab_wire_numbers': set(), 'fab_notes': [], 'fab_texts': [], 'fab_text': '',
            'fab_conns': []}


def analyze_drawing(path, name):
    """DXF を1回読み、図面の dict を返す。読めなければ ValueError。

    Args:
        path: DXF のパス。
        name: 図番（拡張子なしのファイル名）。
    """
    geo = load(path)
    if geo.error:
        raise ValueError(geo.error)
    if not geo.txts:
        # 文字が1つも無い図面（DWG→DXF 変換で文字が輪郭線に分解されたもの等）は、表の見出しが無いので
        # どの読み取りも空になる。輪郭線は数万本の線分になり、製作図の電線の連結（総当たり）が
        # 止まったように遅くなるので、読み取りを走らせない（2026-11-18、EE3298-637-02E の旧変換版で発生）
        return _empty_drawing(name, no_text=True)
    listing = cable_list_table.parse(geo)
    wire = wire_number_table.parse(geo)
    fab = fab_drawing.parse(geo)

    wire_rows = []
    for ti, table in enumerate(wire['tables'], 1):
        for r in table['rows']:
            row = {'表': ti, '行': r['_row'], '_lines': r['_lines'],
                   '削除された値': '; '.join(f'{k}:{v}' for k, v in r['_deleted'].items())}
            row.update({k: v for k, v in r.items() if not k.startswith('_')})
            wire_rows.append(row)

    fab_wire_numbers = set()
    for t in fab['tables']:
        for r in t['rows']:
            for key in ('線番', '線番_2'):
                fab_wire_numbers.update(int(n) for n in re.findall(r'\*(\d+)', r.get(key, '')))

    kinds = []
    if listing is not None:
        kinds.append(KIND_LIST)
    if wire['tables']:
        kinds.append(KIND_WIRE)
    if fab['tables']:
        kinds.append(KIND_FAB)
    return {
        'drawing_number': name,
        'no_text': not geo.txts,
        'kinds': kinds,
        'list_rows': listing['rows'] if listing else [],
        'wire_rows': wire_rows,
        'wire_notes': [p for p, _ in wire['notes']],
        'fab_wire_numbers': fab_wire_numbers,
        'fab_notes': [p for p, _ in fab['notes']],
        'fab_texts': fab['texts'],
        'fab_text': _squash(' '.join(fab['texts'])),
        'fab_conns': fab_drawing.connection_rows(fab),
    }


def analyze_uploads(items, progress=None):
    """アップロードされたファイル群を1件ずつ解析する。

    Args:
        items: [(元のファイル名, bytes), ...]。呼び出し側でフィルタ・同名除去済みであること。
        progress: 省略可。`progress(完了数, 全体数, ファイル名)` を各ファイルの処理前に呼ぶ。

    Returns:
        (図面の dict のリスト, エラーのリスト)。エラーは [(ファイル名, メッセージ), ...]。
        1つのファイルが壊れていても残りは処理を続ける。
    """
    drawings, errors = [], []
    for i, (name, data) in enumerate(items):
        if progress:
            progress(i, len(items), name)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'input.dxf')
            with open(path, 'wb') as f:
                f.write(data)
            try:
                drawings.append(analyze_drawing(path, os.path.splitext(os.path.basename(name))[0]))
            except Exception as e:  # noqa: BLE001 - 壊れた DXF 1件で全体を止めない
                errors.append((name, f'{type(e).__name__}: {e}'))
    return drawings, errors


# ---------------------------------------------------------------- 2. 照合

def _squash(s):
    return re.sub(r'\s+', '', s).upper()


def _values(cell):
    return [v.strip() for v in (cell or '').split(SEP) if v.strip()]


def _codes(cell):
    """機器符号のセルを、複数値（' | '）とカンマで分けた機器符号のリストにする。"""
    return [c.strip() for v in _values(cell) for c in v.split(',') if c.strip()]


def fab_pattern_regex(pattern):
    """一覧表の製作図パターン（`*` は任意の1文字）。末尾に改訂記号（英字1字）があってもよい。"""
    body = ''.join('[0-9A-Z]' if c == '*' else re.escape(c) for c in pattern.upper())
    return re.compile(f'^{body}[A-Z]?$')


def wire_pattern_regex(pattern):
    """製作図の注記の線番設定表パターン（`-634-07`）。頭の `<英字2><数字4>` は任意、末尾の改訂記号も任意。"""
    return re.compile(r'^[A-Z]{2}\d{4}' + re.escape(pattern.upper()) + '[A-Z]?$')


def _resolve(regex, by_name):
    return [d for name, d in by_name.items() if regex.match(name.upper())]


def _res(check, result, content=''):
    return {'照合': check, '結果': result, '内容': content}


def _resolve_all(patterns, make_regex, by_name):
    """パターンごとに図面を探す。Returns: (図面のリスト, 不合格の内容 または None)。"""
    found, problems = [], []
    for p in patterns:
        hits = _resolve(make_regex(p), by_name)
        if len(hits) != 1:
            names = '、'.join(d['drawing_number'] for d in hits)
            problems.append(f'{p}: {len(hits)}件' + (f'（{names}）' if hits else ''))
            continue
        if hits[0]['no_text']:
            problems.append(f'{p}: {hits[0]["drawing_number"]} に文字が無い（読み取れない図面）')
            continue
        found.append(hits[0])
    return found, ('、'.join(problems) if problems else None)


def _cable_wire_rows(cable, wire_drawings):
    return [(d, r) for d in wire_drawings for r in d['wire_rows'] if cable in _values(r.get('ケーブル番号'))]


def _unit_end_matches(unit, ends):
    """接続先のユニット名が一致・近似する端のインデックスと、一致の種類（'一致' > '近似'）。"""
    hits = []
    for i, (units, _codes) in enumerate(ends):
        kinds = [wire_number_table.unit_match(unit, u) for u in units]
        kinds = [k for k in kinds if k]
        if kinds:
            hits.append((i, '一致' if '一致' in kinds else '近似'))
    return hits


def _check_list_row(row, by_name):
    """一覧表の1行について照合 1〜7 を返す。Returns: (結果のリスト, 製作図の図番, 線番設定表の図番)"""
    cable = row['ケーブル番号']
    fab_patterns = _values(row.get('製作図パターン'))
    results = []

    # --- 1 製作図の図面
    fabs, fab_problem = [], None
    if not fab_patterns:
        results.append(_res(C1, NA, '製作図パターンが空'))
        fab_ok = False
    else:
        fabs, fab_problem = _resolve_all(fab_patterns, fab_pattern_regex, by_name)
        fab_ok = fab_problem is None
        results.append(_res(C1, OK if fab_ok else NG,
                            '、'.join(d['drawing_number'] for d in fabs) if fab_ok else fab_problem))
    fab_names = '、'.join(d['drawing_number'] for d in fabs) if fab_ok else ''

    # --- 2 製作図の記載
    for (field, label), check in zip(C2_PARTS, C2):
        vals = _values(row.get(field))
        if not vals:
            results.append(_res(check, NA, '値が空'))
        elif not fab_ok:
            results.append(_res(check, NA, '製作図の図面が特定できない'))
        else:
            missing = [v for v in vals if not any(_squash(v) in d['fab_text'] for d in fabs)]
            results.append(_res(check, NG if missing else OK,
                                f'図面に無い: {"、".join(missing)}' if missing else '、'.join(vals)))

    # --- 3 線番設定表の図面
    wires, wire_ok = [], False
    notes = [p for d in fabs for p in d['fab_notes']] if fab_ok else []
    if not fab_ok:
        results.append(_res(C3, NA, '製作図の図面が特定できない'))
    elif not notes:
        results.append(_res(C3, NA, '製作図の注記に線番設定表が無い'))
    else:
        wires, wire_problem = _resolve_all(notes, wire_pattern_regex, by_name)
        wire_ok = wire_problem is None
        results.append(_res(C3, OK if wire_ok else NG,
                            '、'.join(d['drawing_number'] for d in wires) if wire_ok else wire_problem))
    wire_names = '、'.join(d['drawing_number'] for d in wires) if wire_ok else ''

    # --- 4 線番設定表の記載
    mine = _cable_wire_rows(cable, wires) if wire_ok else []
    if not wire_ok:
        results.append(_res(C4, NA, '線番設定表の図面が特定できない'))
        wire_rows_ok = False
    else:
        wire_rows_ok = bool(mine)
        results.append(_res(C4, OK if mine else NG,
                            f'{len(mine)}行' if mine else f'{wire_names} に {cable} の行が無い'))

    # --- 5 線番設定表の製作図
    if not wire_ok:
        results.append(_res(C5, NA, '線番設定表の図面が特定できない'))
    else:
        wire_fab = [p for d in wires for p in d['wire_notes']]
        if not wire_fab:
            results.append(_res(C5, NA, '線番設定表の注記に製作図が無い'))
        elif set(wire_fab) & set(fab_patterns):
            results.append(_res(C5, OK, '、'.join(wire_fab)))
        else:
            results.append(_res(C5, NG, f'線番設定表の注記 {"、".join(wire_fab)} ≠ 一覧表 {"、".join(fab_patterns)}'))

    # --- 6a・6b 接続先
    if not wire_rows_ok:
        results += [_res(C6A, NA, '線番設定表の記載が確認できない'), _res(C6B, NA, '線番設定表の記載が確認できない')]
    else:
        ends = [(_values(row.get(f'端末{i}_ユニット')), _codes(row.get(f'端末{i}_機器符号'))) for i in (1, 2)]
        all_units = [u for units, _ in ends for u in units]
        all_codes = {c for _, codes in ends for c in codes}
        unit_bad, unit_approx, term_bad, n_term = [], [], [], 0
        for _d, wr in mine:
            for k in ('接続先1', '接続先2'):
                unit, term = wire_number_table.split_unit_terminal(wr['_lines'].get(k, []), all_units, all_codes)
                where = f'表{wr["表"]} 行{wr["行"]} {k}'
                hits = _unit_end_matches(unit, ends)
                if not hits:
                    unit_bad.append(f'{where}={unit or "（空）"}')
                elif all(kind == '近似' for _, kind in hits):
                    unit_approx.append(f'{where}={unit}')
                if term:
                    n_term += 1
                    # 対応する端末 = ユニット名が合う端（無ければどの端でもよい）
                    cands = [ends[i][1] for i, _ in hits] or [ends[0][1], ends[1][1]]
                    parts = [p.strip() for p in term.split(' or ')]
                    if not any(term in codes or all(p in codes for p in parts) for codes in cands):
                        term_bad.append(f'{where}={term}')
        if unit_bad:
            results.append(_res(C6A, NG, '合わない: ' + '、'.join(unit_bad)))
        else:
            results.append(_res(C6A, OK, ('近似: ' + '、'.join(unit_approx)) if unit_approx else '一致'))
        if not n_term:
            results.append(_res(C6B, NA, '端子台に分けられた接続先が無い'))
        elif term_bad:
            results.append(_res(C6B, NG, '合わない: ' + '、'.join(term_bad)))
        else:
            results.append(_res(C6B, OK, f'{n_term}件が一致'))

    # --- 7 線番の列
    fab_n = set().union(*(d['fab_wire_numbers'] for d in fabs)) if fab_ok else set()
    if not wire_rows_ok:
        results.append(_res(C7, NA, '線番設定表の記載が確認できない'))
    elif not fab_n:
        results.append(_res(C7, NA, '製作図に *n が無い'))
    else:
        wire_n = set()
        for _d, wr in mine:
            wire_n.update(int(m.group(1)) for k, v in wr.items()
                          if v and (m := re.fullmatch(r'線番(\d+)', k)))
        fmt = lambda s: ','.join(f'*{n}' for n in sorted(s))  # noqa: E731
        results.append(_res(C7, OK if fab_n == wire_n else NG,
                            f'製作図 {fmt(fab_n)} / 線番設定表 {fmt(wire_n)}'))
    return results, fab_names, wire_names


def check_consistency(drawings):
    """全図面の dict から、ケーブル番号ごとの照合行を作る。

    Returns:
        {'checks': [{'ケーブル番号', '照合', '結果', '一覧表の図番', '一覧表の行',
                     'ケーブル製作図の図番', '線番設定表の図番', '内容'}, ...],
         'drawings': drawings}
        並びはケーブル番号→一覧表の図番→一覧表の行→照合。
    """
    by_name = {d['drawing_number']: d for d in drawings}
    rows, list_cables, wire_only = [], set(), {}
    for d in sorted(drawings, key=lambda d: d['drawing_number']):
        for r in d['list_rows']:
            cable = r['ケーブル番号']
            if not cable:
                continue
            list_cables.add(cable)
            results, fab_names, wire_names = _check_list_row(r, by_name)
            for res in [_res(C0, OK, '一覧表にあり'), *results]:
                rows.append({'ケーブル番号': cable, **res, '一覧表の図番': d['drawing_number'], '一覧表の行': r['行'],
                             'ケーブル製作図の図番': fab_names, '線番設定表の図番': wire_names})
    for d in sorted(drawings, key=lambda d: d['drawing_number']):
        for r in d['wire_rows']:
            for cable in _values(r.get('ケーブル番号')):
                if cable not in list_cables:
                    wire_only.setdefault(cable, set()).add(d['drawing_number'])
    for cable, names in wire_only.items():
        rows.append({'ケーブル番号': cable, **_res(C0, NG, '線番設定表にだけある（一覧表に無い）'),
                     '一覧表の図番': '', '一覧表の行': '', 'ケーブル製作図の図番': '',
                     '線番設定表の図番': '、'.join(sorted(names))})
    order = {name: i for i, name in enumerate(CHECK_NAMES)}
    rows.sort(key=lambda r: (r['ケーブル番号'], r['一覧表の図番'], r['一覧表の行'] or 0, order[r['照合']]))
    return {'checks': rows, 'drawings': drawings}


# ---------------------------------------------------------------- 3. 判定の根拠（明細）

def pattern_resolutions(drawings):
    """図面パターンの解決結果。どのパターンが、どの図面に、何件当たったか（照合 1・3・5 の根拠）。

    Returns: [{'種別', 'パターン', '参照元の図番', '当たった図面', '件数', '備考'}, ...]
        種別: 製作図（一覧表・線番設定表の注記が指す）／線番設定表（製作図の注記が指す）。
    """
    by_name = {d['drawing_number']: d for d in drawings}
    out, seen = [], set()

    def add(kind, pattern, make_regex, source):
        hits = _resolve(make_regex(pattern), by_name)
        key = (kind, pattern, source)
        if key in seen:
            return
        seen.add(key)
        note = '文字が無い図面' if any(h['no_text'] for h in hits) else ''
        out.append({'種別': kind, 'パターン': pattern, '参照元の図番': source,
                    '当たった図面': '、'.join(h['drawing_number'] for h in hits), '件数': len(hits), '備考': note})
    for d in sorted(drawings, key=lambda d: d['drawing_number']):
        src = d['drawing_number']
        for r in d['list_rows']:
            for p in _values(r.get('製作図パターン')):
                add('製作図（一覧表）', p, fab_pattern_regex, src)
        for p in d['fab_notes']:
            add('線番設定表（製作図の注記）', p, wire_pattern_regex, src)
        for p in d['wire_notes']:
            add('製作図（線番設定表の注記）', p, fab_pattern_regex, src)
    return out


def fab_text_rows(drawings):
    """照合 2 が探す対象の製作図の文字。製作図のパターンに当たった図面と、線番/PIN の表がある図面。"""
    hit = {name for r in pattern_resolutions(drawings) if r['種別'].startswith('製作図')
           for name in r['当たった図面'].split('、') if name}
    return [(d['drawing_number'], t) for d in sorted(drawings, key=lambda d: d['drawing_number'])
            if d['drawing_number'] in hit or KIND_FAB in d['kinds'] for t in d.get('fab_texts', [])]


def connection_judgments(wire_row, list_units, list_codes):
    """線番設定表の1行の接続先ごとに、ユニット名・端子台の判定を返す（明細シート用。照合 6 の根拠）。

    一覧表の端（ケーブル番号が同じ全行の端末1・2）と比べる。照合 6 は一覧表の行ごとの判定なので、
    ここは「このケーブルの一覧表のどの端末にも無い」かどうかの目安。
    Returns: {'接続先1': {'ユニット': str, '端子台': str, 'ユニット判定': str, '端子台判定': str}, ...}
    """
    out = {}
    for k in ('接続先1', '接続先2'):
        unit, term = wire_number_table.split_unit_terminal(wire_row['_lines'].get(k, []), list_units, list_codes)
        kinds = [wire_number_table.unit_match(unit, u) for u in list_units]
        if not list_units:
            unit_j = '一覧表に無い'
        else:
            unit_j = '一致' if '一致' in kinds else '近似' if '近似' in kinds else '不一致'
        if not term:
            term_j = ''
        elif not list_codes:
            term_j = '一覧表に無い'
        else:
            parts = [p.strip() for p in term.split(' or ')]
            term_j = '一致' if term in list_codes or all(p in list_codes for p in parts) else '不一致'
        out[k] = {'ユニット': unit, '端子台': term, 'ユニット判定': unit_j, '端子台判定': term_j}
    return out
