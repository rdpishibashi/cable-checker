"""整合性チェック: 393（ケーブル一覧表・線番設定表・ケーブル製作図）と展開接続図（405）の照合（streamlit 非依存）。

3段に分ける。
  1. `analyze_drawing(path, name)` — DXF を1回だけ読み、393 の3種類の読み取りを走らせて、図面ごとの dict にする
  2. `analyze_uploads(items)` — 全ファイルに 1 を行い、一覧表の「接続図パターン」が指す図面だけを
     `schematic_reader.analyze_schematic()` で読む（2段目）。結果は図面の dict の `schematic` に入る
  3. `check_consistency(drawings)` — 一覧表の行ごとに、項目（図面の対応・端末・ケーブル・芯線）の判定と根拠を作る

定義は docs/handoff_schematic_405_check.md の「照合の定義」が正。チェーンは
一覧表の行 →（製作図パターン）→ 製作図 →（注記の線番設定表パターン）→ 線番設定表、
一覧表の行 →（接続図パターン）→ 展開接続図。`check_consistency()` は dict だけを受け取るので、合成データでそのままテストできる。

判定の記号（重い順）: × 不整合 ＞ ？ 読めない（図面で確認が必要）＞ △ 一致（根拠が弱い）＞ ○ 一致 ＞ － 対象外・根拠なし。
1つの項目に複数の比較（線番設定表・製作図・展開接続図）があるときは、最も重いものをその項目の判定にする。

図面の dict（`analyze_drawing()` の戻り値＋2段目）:
  drawing_number, no_text（文字が1つも無い）, kinds（'ケーブル一覧表'・'線番設定表'・'ケーブル製作図'・'展開接続図'）,
  list_rows（一覧表の行。`cable_list_table.parse()` の rows）,
  wire_rows（線番設定表の行。`'_lines'` に接続先ごとの行）, wire_notes（注記の製作図パターン）,
  fab_wire_numbers（製作図の線番の表にある `*n` の n）, fab_notes（注記の線番設定表パターン）,
  fab_texts（図面の全文字。削除済みを除く）, fab_text（その文字を空白なし・大文字でつなげたもの）,
  fab_conns（製作図の接続の明細）, schematic（展開接続図として読んだ結果。`schematic_reader.analyze_schematic()`）
"""
import os
import re
import tempfile
import unicodedata
from collections import Counter

from . import cable_list_table, fab_drawing, wire_number_table
from .drawing_geometry import load
from .schematic_reader import analyze_schematic

# 判定の記号
OK, WEAK, NG, UNREAD, NA = '○', '△', '×', '？', '－'
SYMBOLS = [OK, WEAK, NG, UNREAD, NA]
SEVERITY = {NA: 0, OK: 1, WEAK: 2, UNREAD: 3, NG: 4}
SEP = cable_list_table.SEP

KIND_LIST = 'ケーブル一覧表'
KIND_WIRE = '線番設定表'
KIND_FAB = 'ケーブル製作図'
KIND_SCHEMATIC = '展開接続図'
KIND_REF = '展開接続図（図中の参照）'  # パターンの解決シートの種別

PHASE_DRAWINGS = '図面の読み取り'
PHASE_SCHEMATIC = '展開接続図の読み取り'

# 項目（グループ, 項目）。整合性チェックシートの列と、Summary の行になる。列名は `グループ｜項目`
ITEMS = [
    ('図面の対応', '一覧表'), ('図面の対応', '製作図'), ('図面の対応', '線番設定表'), ('図面の対応', '展開接続図'),
    *[(f'端末{i}', x) for i in (1, 2) for x in ('ユニット', 'モジュール', '機器符号', '部品種別')],
    ('ケーブル', '種類'), ('ケーブル', '芯数'),
    ('芯線', '線番'), ('芯線', 'PIN'), ('芯線', '色'),
]


def item_column(group, item):
    return f'{group}｜{item}'


def item_label(column):
    """整合性チェックの列名（`グループ｜項目`）に対応する、照合明細の「項目」。図面の対応は項目だけ、ほかは `グループ 項目`。"""
    group, item = column.split('｜')
    return item if group == '図面の対応' else f'{group} {item}'


ITEM_COLUMNS = [item_column(g, i) for g, i in ITEMS]

# 判断根拠に書く決まった語
B_MATCH = '一致'
B_APPROX = '近似'
B_REGION = '領域'
B_REGION_APPROX = '領域・近似'
B_DEVICE = '機器'
B_DEVICE_APPROX = '機器・近似'
B_NEARBY = '近傍'
B_EXCLUDING_SG = 'SG/SH を除いて一致'
B_GND = 'GND を含む'
B_GND_EXCLUDED = 'GND 線の相手側は対象外'
B_DIRECTION = '向きが決まらない'
B_UNREAD = '読めない'
B_MISSING = '図面に無い'
B_NO_ROW = '対応する行が無い'
B_EMPTY = '値が空'

# SG・SH（シールド・ドレイン線）の線番
_DRAIN = {'SG', 'SH'}


# ---------------------------------------------------------------- 1. 図面ごとの解析

def _empty_drawing(name, no_text=False):
    return {'drawing_number': name, 'no_text': no_text, 'kinds': [], 'list_rows': [], 'wire_rows': [],
            'wire_notes': [], 'fab_wire_numbers': set(), 'fab_notes': [], 'fab_texts': [], 'fab_text': '',
            'fab_conns': [], 'schematic': None}


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
        'schematic': None,          # 2段目（analyze_uploads）が展開接続図として読んだときだけ入る
    }


def analyze_uploads(items, progress=None):
    """アップロードされたファイル群を解析する。1段目で全ファイルを 393 として読み、2段目で一覧表の「接続図パターン」が指す図面だけを
    展開接続図（405）として読む。

    Args:
        items: [(元のファイル名, bytes), ...]。呼び出し側でフィルタ・同名除去済みであること。
        progress: 省略可。`progress(完了数, 全体数, 図番またはファイル名, 段階)` を各ファイルの処理前に呼ぶ（段階は PHASE_*）。

    Returns:
        (図面の dict のリスト, エラーのリスト)。エラーは [(ファイル名, メッセージ), ...]。
        1つのファイルが壊れていても残りは処理を続ける。
    """
    drawings, errors, files = [], [], {}
    for i, (name, data) in enumerate(items):
        if progress:
            progress(i, len(items), name, PHASE_DRAWINGS)
        stem = os.path.splitext(os.path.basename(name))[0]
        files[stem] = (name, data)
        try:
            drawings.append(_analyze_bytes(data, stem))
        except Exception as e:  # noqa: BLE001 - 壊れた DXF 1件で全体を止めない
            errors.append((name, f'{type(e).__name__}: {e}'))
    by_name = {d['drawing_number']: d for d in drawings}
    targets = schematic_targets(drawings)
    for i, stem in enumerate(targets):
        if progress:
            progress(i, len(targets), stem, PHASE_SCHEMATIC)
        name, data = files[stem]
        try:
            by_name[stem]['schematic'] = _with_temp_file(data, lambda path: analyze_schematic(path, stem))
            by_name[stem]['kinds'].append(KIND_SCHEMATIC)
        except Exception as e:  # noqa: BLE001
            errors.append((name, f'{type(e).__name__}: {e}'))
    return drawings, errors


def _with_temp_file(data, func):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'input.dxf')
        with open(path, 'wb') as f:
            f.write(data)
        return func(path)


def _analyze_bytes(data, stem):
    return _with_temp_file(data, lambda path: analyze_drawing(path, stem))



# ---------------------------------------------------------------- 照合の補助（パターンの解決など）

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


# ---------------------------------------------------------------- 展開接続図の読み取り（2段目）

_KINDS_393 = (KIND_LIST, KIND_WIRE, KIND_FAB)


def schematic_targets(drawings):
    """一覧表の「接続図パターン」が指す図面の図番（昇順）。2段目はこの図面だけを展開接続図として読む。

    393 の表（一覧表・線番設定表・製作図の線番/PIN）が見つかった図面は除く。`EE****-637-**` のようなパターンは、
    展開接続図 EE6888-637-01A だけでなく、同じ `-637-` を持つ 393 の線番設定表にも当たるため（読むと、393 の図面にある
    ケーブル番号の文字が展開接続図の出現として数えられてしまう）。
    """
    by_name = {d['drawing_number']: d for d in drawings}
    patterns = {p for d in drawings for r in d['list_rows'] for p in _values(r.get('接続図パターン'))}
    return sorted({d['drawing_number'] for p in patterns for d in _resolve(fab_pattern_regex(p), by_name)
                   if not set(d['kinds']) & set(_KINDS_393)})


# ---------------------------------------------------------------- 照合の部品

def _nfkc_squash(s):
    return _squash(unicodedata.normalize('NFKC', s or ''))


def _unit_key(s):
    """ユニット名の比較用: NFKC・括弧の補足と `*`（モジュールの伏せ字）を除く・空白を詰める・大文字。"""
    s = re.sub(r'\([^)]*\)', '', unicodedata.normalize('NFKC', s or ''))
    return _squash(s).replace('*', '')


def _module_key(module):
    return _nfkc_squash(module).replace('*', '')


_MODULE_TOKEN = re.compile(r'(?<![0-9A-Z])[FR]\d(?![0-9A-Z])')
_COLOR_CODE = r'(?:BK|BN|RD|OG|YE|GN|BU|VT|GY|WH|PK|TQ|GNYE)'
_COLOR_RE = re.compile(rf'^{_COLOR_CODE}(?:[/-]{_COLOR_CODE})*$')


def _is_color(label):
    """色のラベル: 括弧書き（`(BK)`・`(PK/BK)`・`桃(赤)`）か、色の記号だけ（`BU`・`GNYE`）。"""
    s = unicodedata.normalize('NFKC', label or '').strip().upper()
    return '(' in s or bool(_COLOR_RE.match(s))


def _module_tokens(text):
    return set(_MODULE_TOKEN.findall(unicodedata.normalize('NFKC', text or '').upper()))


def _core_count(row):
    m = re.search(r'\d+', row.get('芯数') or '')
    return int(m.group()) if m else None


def _code_match(code, names):
    """一覧表の機器符号（`*` は任意の1文字）が、405 の端の名前（1行目・括弧の前）にあるか。`GND` を含めば `GND` を含む名前と一致。"""
    c = _squash(code)
    if 'GND' in c:
        return any('GND' in _squash(n) for n in names)
    rx = re.compile('^' + ''.join('.' if ch == '*' else re.escape(ch) for ch in c) + '$')
    for n in names:
        first = _squash(n.split('\n')[0])
        if rx.match(first) or rx.match(first.split('(')[0]):
            return True
    return False


def _term_match(term, codes):
    """線番設定表の端子台が、一覧表の機器符号と完全一致するか（`ELB087 or CB087` は各部品が揃うこと）。"""
    parts = [p.strip() for p in term.split(' or ')]
    return term in codes or all(p in codes for p in parts)


def _unit_kind(unit, units):
    """線番設定表の接続先のユニット名と、一覧表のユニット名（複数値のどれか）の一致の種類: '一致'・'近似'・''。"""
    kinds = [wire_number_table.unit_match(unit, u) for u in units]
    kinds = [k for k in kinds if k]
    return '一致' if '一致' in kinds else ('近似' if kinds else '')


def _all_unit_keys(drawings):
    """一覧表に出てくるユニット名（とユニット名＋モジュール）の比較用キー。近傍のラベルが「ユニット名のラベル」かの判定に使う。"""
    keys = set()
    for d in drawings:
        for r in d['list_rows']:
            for i in (1, 2):
                module = _module_key(r.get(f'端末{i}_モジュール'))
                for unit in _values(r.get(f'端末{i}_ユニット')):
                    for part in unit.split(','):
                        k = _unit_key(part)
                        if len(k) >= 2:
                            keys.add(k)
                            if module:
                                keys.add(k + module)
    return keys


def _best(items, key):
    """最も軽い（一致に近い）ものを返す。同じなら先のもの。"""
    return min(items, key=key) if items else None


class _Cell:
    """1つの項目（整合性チェックの1セル）。比較（図面の種別・記号・根拠）を集め、最も重い記号をセルの判定にする。"""

    def __init__(self, label, note=''):
        self.label = label           # 照合明細の「項目」
        self.comparisons = []        # [(図面の種別, 記号, 根拠)]
        self.values = {}             # {'一覧表': ..., '線番設定表': ..., '製作図': ..., '展開接続図': ...}
        self.sources = []            # 参照した図面の図番
        self.note = note             # 比較が1つも無いときの理由

    def add(self, kind, symbol, basis):
        self.comparisons.append((kind, symbol, basis))
        return self

    def value(self, kind, text):
        if text:
            self.values[kind] = text
        return self

    def source(self, *names):
        for n in names:
            if n and n not in self.sources:
                self.sources.append(n)
        return self

    @property
    def symbol(self):
        return max((s for _, s, _ in self.comparisons), key=lambda s: SEVERITY[s], default=NA)

    @property
    def basis(self):
        if not self.comparisons:
            return self.note
        return '／'.join(f'{k} {s} {b}' for k, s, b in self.comparisons)


def _worst(symbols):
    return max(symbols, key=lambda s: SEVERITY[s], default=NA)


# ---------------------------------------------------------------- 一覧表の行 → 図面のチェーン

def _chain(row, by_name):
    """一覧表の行 → 製作図 → 線番設定表 の解決。"""
    cable = row['ケーブル番号']
    fab_patterns = _values(row.get('製作図パターン'))
    ch = {'fab_patterns': fab_patterns, 'fabs': [], 'fab_problem': None, 'notes': [], 'wires': [], 'wire_problem': None}
    if fab_patterns:
        ch['fabs'], ch['fab_problem'] = _resolve_all(fab_patterns, fab_pattern_regex, by_name)
    ch['fab_ok'] = bool(fab_patterns) and ch['fab_problem'] is None
    if ch['fab_ok']:
        ch['notes'] = [p for d in ch['fabs'] for p in d['fab_notes']]
    if ch['notes']:
        ch['wires'], ch['wire_problem'] = _resolve_all(ch['notes'], wire_pattern_regex, by_name)
    ch['wire_ok'] = bool(ch['notes']) and ch['wire_problem'] is None
    ch['mine'] = _cable_wire_rows(cable, ch['wires']) if ch['wire_ok'] else []
    return ch


def _side(orient, i):
    """線番設定表の接続先のうち、端末 i（1・2）に対応する側（0=接続先1・1=接続先2）。fwd=接続先1が端末1、rev=接続先1が端末2。"""
    return (i - 1) if orient == 'fwd' else (2 - i)


def _select_wire_rows(row, mine):
    """線番設定表のこのケーブルの行のうち、この一覧表の行の端末に合うもの（行の選び方。決定7）。

    1. 接続先1・2 の端子台が、端末1・2 の機器符号に（どちらかの向きで）一致する行（端子台が片方だけの行は、その片方が一致すればよい）
    2. 1 が無ければ、接続先1・2 のユニット名が端末1・2 のユニット名に（どちらかの向きで）一致・近似する行
    Returns: ([{'drawing', 'row', 'orient', 'sides': [(ユニット名, 端子台), ...]}], 'terminal'|'unit'|'')
    """
    units = {i: _values(row.get(f'端末{i}_ユニット')) for i in (1, 2)}
    codes = {i: _codes(row.get(f'端末{i}_機器符号')) for i in (1, 2)}
    all_units = units[1] + units[2]
    all_codes = set(codes[1]) | set(codes[2])
    parsed = []
    for d, wr in mine:
        sides = [wire_number_table.split_unit_terminal(wr['_lines'].get(k, []), all_units, all_codes)
                 for k in ('接続先1', '接続先2')]
        parsed.append((d, wr, sides))

    def rule_terminal(sides, orient):
        terms = {i: sides[_side(orient, i)][1] for i in (1, 2)}
        given = [i for i in (1, 2) if terms[i]]
        return bool(given) and all(_term_match(terms[i], codes[i]) for i in given)

    def rule_unit(sides, orient):
        given = [i for i in (1, 2) if units[i]]
        return bool(given) and all(_unit_kind(sides[_side(orient, i)][0], units[i]) for i in given)

    for how, rule in (('terminal', rule_terminal), ('unit', rule_unit)):
        picked = []
        for d, wr, sides in parsed:
            for orient in ('fwd', 'rev'):
                if rule(sides, orient):
                    picked.append({'drawing': d, 'row': wr, 'orient': orient, 'sides': sides})
                    break
        if picked:
            return picked, how
    return [], ''


def _wire_where(sel):
    return f'{sel["drawing"]["drawing_number"]} 表{sel["row"]["表"]} 行{sel["row"]["行"]}'


# ---------------------------------------------------------------- 一覧表の行 → 展開接続図

def _schematic_names(ctx, patterns):
    """接続図パターンが指す図面（393 の表が見つかった図面は展開接続図ではないので除く。`schematic_targets()` と同じ）。"""
    return sorted({d['drawing_number'] for p in patterns for d in _resolve(fab_pattern_regex(p), ctx.by_name)
                   if not set(d['kinds']) & set(_KINDS_393)})


def _side_names(occ, side):
    return [e['name'] for w in occ['wires'] for e in [w['ends'].get(side)] if e and e['name']]


def _orientation(row, occ):
    """機器符号が合う向きを決める。

    Returns: {'status': 'OK'|'片側'|'NG'|'読めない'|'符号なし', 'sides': {1: '-'|'+', 2: ...}, 'known': 向きが決まったか,
              'match': {i: True|False|None（符号が空）}, 'names': {'-': [...], '+': [...]}}
    """
    names = {s: _side_names(occ, s) for s in '-+'}
    codes = {i: _codes(row.get(f'端末{i}_機器符号')) for i in (1, 2)}
    fwd, rev = {1: '-', 2: '+'}, {1: '+', 2: '-'}

    def match(sides):
        return {i: (all(_code_match(c, names[sides[i]]) for c in codes[i]) if codes[i] else None) for i in (1, 2)}

    out = {'status': '読めない', 'sides': fwd, 'known': False, 'match': match(fwd), 'names': names}
    if not occ['wires'] or not (names['-'] or names['+']):
        return out
    mf, mr = match(fwd), match(rev)
    nf, nr = sum(v is True for v in mf.values()), sum(v is True for v in mr.values())
    sides, m = (fwd, mf) if nf >= nr else (rev, mr)
    given = [v for v in m.values() if v is not None]
    out.update(sides=sides, match=m, known=max(nf, nr) >= 1)
    if not given:
        out['status'] = '符号なし'
    elif all(given):
        out['status'] = 'OK'
    else:
        out['status'] = '片側' if max(nf, nr) >= 1 else 'NG'
    return out


_STATUS_RANK = {'OK': 0, '片側': 1, '符号なし': 2, 'NG': 3, '読めない': 4}


def _pick_occurrence(ctx, row):
    """接続図パターンが当たった図面にある、このケーブル番号の出現のうち、機器符号の判定が最も良いもの。無ければ None。"""
    cable = row['ケーブル番号']
    cands = []
    for name in _schematic_names(ctx, _values(row.get('接続図パターン'))):
        d = ctx.by_name[name]
        s = d.get('schematic')
        if not s:
            continue
        for occ in s['occurrences']:
            if occ['cable'] == cable:
                cands.append((d, s, occ, _orientation(row, occ)))
    if not cands:
        return None
    d, s, occ, orient = min(cands, key=lambda c: _STATUS_RANK[c[3]['status']])
    return {'drawing': d, 'schematic': s, 'occ': occ, 'orient': orient, 'title': s.get('title', '')}


def _side_evidence(occ, side):
    """記号のこちら側の端すべての、ユニットの根拠（領域の名称候補・機器の文字・近傍の文字）を集める。"""
    regions, devices, nearby, labelled = [], [], {}, False
    for w in occ['wires']:
        e = w['ends'].get(side)
        if not e or e['kind'] is None:
            continue
        labelled = labelled or bool(e['name'])
        regions += [n for n in e['regions'] if n not in regions]
        devices += [t for t in e['devices'] if t not in devices]
        for first, dist, full in e['nearby']:
            if first not in nearby or dist < nearby[first][0]:
                nearby[first] = (dist, full)
    return {'regions': regions, 'devices': devices, 'nearby': nearby, 'labelled': labelled}


def _is_single_multicore(row, occ, n_fab_conns):
    """多芯ケーブルを1本の線で描いたもの（ピン・線番は図面に無い。機器符号だけ照合する）。"""
    if occ['how'] != 'single':
        return False
    return ((_core_count(row) or 0) > 1 or n_fab_conns > 1
            or 'ACCESSORY' in (row.get('ケーブル種類') or '').upper())


# ---------------------------------------------------------------- 項目ごとの判定

def _unit_405_one(ctx, u, m, ev, gnd):
    """展開接続図で、ユニット名 u（＋モジュール m）が端末の位置に当たるか。上から順に、最初に決まったもの。

    Returns: (記号, 根拠, ユニットを決めた文字 または None)
    """
    want = {u} | ({u + m} if m else set())
    for n in ev['regions']:
        if _unit_key(n) in want:
            return OK, f'{B_REGION}（{n}）', n
    for n in ev['regions']:
        if _unit_key(n).startswith(u):
            return WEAK, f'{B_REGION_APPROX}（{n}）', n
    for t in ev['devices']:
        if _unit_key(t) in want:
            return WEAK, f'{B_DEVICE}（{t}）', t
    for t in ev['devices']:
        if len(u) >= 3 and _unit_key(t).startswith(u):
            return WEAK, f'{B_DEVICE_APPROX}（{t}）', t
    # 最終判断: 機器符号のラベルの近くにある、一覧表に出てくるユニット名のラベルのうち最も近いもの
    near = [(dist, first, full) for first, (dist, full) in ev['nearby'].items() if _unit_key(first) in ctx.unit_keys]
    nearest = min(near) if near else None
    if nearest and _unit_key(nearest[1]) in want:
        return WEAK, f'{B_NEARBY}（{nearest[1]}、{nearest[0]:.0f}）', nearest[2]
    if gnd:
        return NA, B_GND_EXCLUDED, None
    detail = [f'端末を含む領域: {"、".join(ev["regions"][:4]) or "なし"}']
    if ev['devices']:
        detail.append(f'機器: {"、".join(ev["devices"][:4])}')
    if nearest:
        detail.append(f'最も近いユニット名: {nearest[1]}（{nearest[0]:.0f}）')
    return NG, '合わない（' + '／'.join(detail) + '）', None


def _terminal_cells(ctx, row, i, sel, mine, pick):
    """端末 i（1・2）の ユニット・モジュール・機器符号 のセル。"""
    unit_cell, mod_cell, code_cell = _Cell(f'端末{i} ユニット'), _Cell(f'端末{i} モジュール'), _Cell(f'端末{i} 機器符号')
    units = _values(row.get(f'端末{i}_ユニット'))
    module = row.get(f'端末{i}_モジュール') or ''
    m = _module_key(module)
    codes = _codes(row.get(f'端末{i}_機器符号'))
    gnd = 'GND' in _nfkc_squash(row.get(f'端末{i}_機器符号'))
    unit_cell.value('一覧表', SEP.join(units))
    mod_cell.value('一覧表', module)
    code_cell.value('一覧表', SEP.join(codes))

    # --- 線番設定表（選んだ行の、この端末に対応する接続先）
    if mine:
        for c in (unit_cell, mod_cell, code_cell):
            c.source(*(sorted({x['drawing']['drawing_number'] for x in sel}) or sorted({d['drawing_number'] for d, _ in mine})))
        if not sel:
            where = '、'.join(f'表{r["表"]} 行{r["行"]}' for _d, r in mine[:6])
            if units:
                unit_cell.add('線番設定表', NG, f'{B_NO_ROW}（このケーブルの行: {where}）')
            if codes:
                code_cell.add('線番設定表', NG, f'{B_NO_ROW}（このケーブルの行: {where}）')
        else:
            unit_kinds, mod_marks, code_marks, shown = [], [], [], []
            for x in sel:
                unit, term = x['sides'][_side(x['orient'], i)]
                shown.append(f'{unit}{" " + term if term else ""}（{_wire_where(x)}）')
                if units:
                    unit_kinds.append((_unit_kind(unit, units), _wire_where(x), unit))
                if m:
                    tokens = _module_tokens(unit)
                    if m in tokens:
                        mod_marks.append((OK, _wire_where(x)))
                    elif tokens:
                        mod_marks.append((NG, _wire_where(x) + f'（{"、".join(sorted(tokens))}）'))
                if term and codes:
                    code_marks.append((OK if _term_match(term, codes) else NG, _wire_where(x), term))
            unit_cell.value('線番設定表', ' ／ '.join(shown))
            code_cell.value('線番設定表', ' ／ '.join(f'{x["sides"][_side(x["orient"], i)][1]}' for x in sel
                                                      if x['sides'][_side(x["orient"], i)][1]))
            if unit_kinds:
                best = _best(unit_kinds, key=lambda k: {'一致': 0, '近似': 1, '': 2}[k[0]])
                sym = {'一致': OK, '近似': WEAK, '': NG}[best[0]]
                unit_cell.add('線番設定表', sym, f'{ {"一致": B_MATCH, "近似": B_APPROX, "": "合わない"}[best[0]] }（{best[1]} {best[2]}）')
            if mod_marks:
                best = _best(mod_marks, key=lambda k: SEVERITY[k[0]])
                mod_cell.add('線番設定表', best[0], f'{"一致" if best[0] == OK else "別のモジュール"}（{best[1]}）')
            if code_marks:
                best = _best(code_marks, key=lambda k: SEVERITY[k[0]])
                code_cell.add('線番設定表', best[0], f'{B_MATCH if best[0] == OK else "合わない"}（端子台 {best[2]}、{best[1]}）')

    # --- 展開接続図
    if pick is not None:
        orient, occ = pick['orient'], pick['occ']
        src = pick['drawing']['drawing_number']
        side = orient['sides'][i]
        names = orient['names'][side]
        for c in (unit_cell, mod_cell, code_cell):
            c.source(src)
        code_cell.value('展開接続図', SEP.join(dict.fromkeys(names)))
        if codes:
            if not orient['names']['-'] and not orient['names']['+']:
                code_cell.add('展開接続図', UNREAD, f'{B_UNREAD}（端のコネクタ名・機器名が取れない）')
            elif not names:
                code_cell.add('展開接続図', UNREAD, f'{B_UNREAD}（この側の端のコネクタ名・機器名が取れない）')
            elif orient['match'][i]:
                code_cell.add('展開接続図', OK, B_GND if gnd else B_MATCH)
            else:
                code_cell.add('展開接続図', NG, f'合わない（{"、".join(dict.fromkeys(names))}）')
        ev = _side_evidence(occ, side)
        shown = [n for n in (ev['regions'][:3] + ev['devices'][:3]) if n]
        unit_cell.value('展開接続図', '／'.join(shown))
        decided = []
        if units:
            if not orient['known']:
                unit_cell.add('展開接続図', UNREAD, B_DIRECTION)
                mod_cell.add('展開接続図', UNREAD, B_DIRECTION)
            elif not ev['labelled'] and not ev['regions']:
                unit_cell.add('展開接続図', UNREAD, f'{B_UNREAD}（端末のラベルが取れない）')
                mod_cell.add('展開接続図', UNREAD, f'{B_UNREAD}（端末のラベルが取れない）')
            else:
                for uv in units:
                    sym, basis, text = _unit_405_one(ctx, _unit_key(uv), m, ev, gnd)
                    unit_cell.add('展開接続図', sym, basis if len(units) == 1 else f'{uv}: {basis}')
                    if text:
                        decided.append(text)
                if m:
                    title = pick['title']
                    if any(m in _nfkc_squash(t) for t in decided) or (decided and m in _nfkc_squash(title)):
                        mod_cell.add('展開接続図', OK, '一致（ユニットの文字）' if any(m in _nfkc_squash(t) for t in decided)
                                     else '一致（図面タイトル）')
                    elif decided:
                        others = set().union(*(_module_tokens(t) for t in decided))
                        if others:
                            mod_cell.add('展開接続図', NG, f'別のモジュール（{"、".join(sorted(others))}）')
                        else:
                            mod_cell.add('展開接続図', UNREAD, f'モジュールの記載が見つからない（{"、".join(decided)}）')
                    elif unit_cell.symbol == UNREAD:
                        mod_cell.add('展開接続図', UNREAD, B_UNREAD)
    if not units:
        unit_cell.comparisons = []
        unit_cell.note = B_EMPTY
    if not m:
        mod_cell.comparisons = []
        mod_cell.note = B_EMPTY
    if not codes:
        code_cell.comparisons = []
        code_cell.note = B_EMPTY
    if not unit_cell.comparisons and not unit_cell.note:
        unit_cell.note = '根拠となる図面が無い'
    if not mod_cell.comparisons and not mod_cell.note:
        mod_cell.note = '根拠が無い'
    if not code_cell.comparisons and not code_cell.note:
        code_cell.note = '根拠が無い'
    return unit_cell, mod_cell, code_cell


def _fab_text_cell(label, values, fabs, fab_ok):
    """製作図の文字に値があるか（部品種別・ケーブル種類。文字どおりの一致。空白を除き大文字で比べる）。"""
    cell = _Cell(label).value('一覧表', SEP.join(values))
    if not values:
        cell.note = B_EMPTY
    elif not fab_ok:
        cell.note = '製作図の図面が特定できない'
    else:
        missing = [v for v in values if not any(_squash(v) in d['fab_text'] for d in fabs)]
        cell.source(*(d['drawing_number'] for d in fabs))
        cell.value('製作図', '、'.join(v for v in values if v not in missing))
        cell.add('製作図', NG if missing else OK, f'図面に無い: {"、".join(missing)}' if missing else B_MATCH)
    return cell


def _count_cells(row, ch, sel, mine, fab_conns, pick, multi):
    """ケーブルの 種類（405 の備考との比較）・芯数 のセル。"""
    kind_cell = _fab_text_cell('ケーブル 種類', _values(row.get('ケーブル種類')), ch['fabs'], ch['fab_ok'])
    kinds = _values(row.get('ケーブル種類'))
    if pick is not None and pick['occ']['remark'] and kinds:
        rem = _squash(pick['occ']['remark'])
        ok = any(rem in _squash(v) or _squash(v) in rem for v in kinds)
        kind_cell.add('展開接続図', OK if ok else NG, B_MATCH if ok else f'合わない（{pick["occ"]["remark"]}）')
        kind_cell.value('展開接続図', pick['occ']['remark']).source(pick['drawing']['drawing_number'])

    core = _core_count(row)
    cell = _Cell('ケーブル 芯数').value('一覧表', row.get('芯数') or '')
    if core is None:
        cell.note = B_EMPTY
        return kind_cell, cell

    def judge(n, n_excl):
        if n == core:
            return OK, f'{n}本'
        if n_excl == core:
            return WEAK, f'{B_EXCLUDING_SG}（{n}本、SG/SH を除くと {n_excl}本）'
        return NG, f'{n}本'

    if mine:
        if not sel:
            cell.add('線番設定表', NG, B_NO_ROW)
        else:
            marks = []
            for x in sel:
                vals = [v for k, v in x['row'].items() if re.fullmatch(r'線番\d+', k) and v]
                n_excl = sum(_squash(v) not in _DRAIN for v in vals)
                marks.append(judge(len(vals), n_excl) + (_wire_where(x),))
            best = _best(marks, key=lambda mk: SEVERITY[mk[0]])
            cell.add('線番設定表', best[0], f'{best[1]}（{best[2]}）')
            cell.value('線番設定表', best[1]).source(*sorted({x['drawing']['drawing_number'] for x in sel}))
    if fab_conns:
        n = len(fab_conns)
        # `*n` は線番設定表の行の値に置き換えてから SG/SH を判定する（行ごとに試し、最もよいものを採る）
        options = [judge(n, sum(_squash(_conn_info(d, c, wr)['number']) not in _DRAIN for d, c in fab_conns))
                   for wr in ([x['row'] for x in sel] or [None])]
        sym, basis = _best(options, key=lambda o: SEVERITY[o[0]])
        cell.add('製作図', sym, basis).value('製作図', f'{n}本').source(*(d['drawing_number'] for d in ch['fabs']))
    if pick is not None and not multi:
        wires = pick['occ']['wires']
        n = len(wires)

        def drained(w):
            """ドレイン線: ラベルかピンが SG/SH、または片方の端に端末が無い。"""
            labels = {_squash(x) for x in w['labels'][:2]}
            pins = {_squash((w['ends'].get(s) or {}).get('pin') or '') for s in '-+'}
            both = all(w['ends'].get(s) and w['ends'][s]['kind'] for s in '-+')
            return bool((labels | pins) & _DRAIN) or not both

        sym, basis = judge(n, sum(not drained(w) for w in wires))
        cell.add('展開接続図', sym, basis).value('展開接続図', f'{n}本').source(pick['drawing']['drawing_number'])
    elif pick is not None and multi:
        cell.add('展開接続図', NA, '多芯ケーブルを1本の線で描いた図面のため比べない')
    if not cell.comparisons:
        cell.note = '根拠が無い'
    return kind_cell, cell


# ---------------------------------------------------------------- 芯線（線番・PIN・色）

def _conn_info(d, c, wire_row):
    """製作図の端が2つの接続1本の事実。`*n` は線番設定表の行の値で置き換える。"""
    raw = c.get('端1_線番') or c.get('端2_線番') or ''
    m = re.fullmatch(r'\*(\d+)', raw.strip())
    resolved = raw
    conn_id = ''
    if m:
        conn_id = raw.strip()
        resolved = (wire_row or {}).get(f'線番{m.group(1)}', '') if wire_row else ''
    labels = [x.strip() for x in (c.get('ラベル') or '').split(' | ') if x.strip()]
    return {'drawing': d['drawing_number'], 'pins': (c.get('端1_PIN No.') or '', c.get('端2_PIN No.') or ''),
            'raw': raw, 'id': conn_id, 'number': resolved, 'colors': [x for x in labels if _is_color(x)]}


def _wire_matrix(fab_conns, wire_row, pick, multi):
    """製作図の接続ごとに、展開接続図の芯線と比べた PIN・線番・色の判定を返す。

    Returns: {'rows': [接続ごとの dict], 'extras': [製作図に対応が無い 405 の芯線], 'pin_unread': 405 のピンが1つも読めない}
    """
    conns = [_conn_info(d, c, wire_row) for d, c in fab_conns]
    out = {'rows': [], 'extras': [], 'pin_unread': False}
    wires, labels405 = [], {'number': set(), 'color': set()}
    comparable = pick is not None and not multi
    if comparable:
        for w in pick['occ']['wires']:
            p = {s: ((w['ends'].get(s) or {}).get('pin') or '') for s in '-+'}
            wires.append({'pins': p, 'pool': Counter(x for x in p.values() if x), 'labels': w['labels']})
            for x in w['labels'][:2]:
                labels405['color' if _is_color(x) else 'number'].add(_squash(x))
        out['pin_unread'] = not any(w['pool'] for w in wires)
    used, assign = set(), {}
    for j, c in enumerate(conns):
        fp = Counter(p for p in c['pins'] if p)
        if not fp or not comparable:
            continue
        for wi, w in enumerate(wires):
            if wi not in used and not (fp - w['pool']):
                used.add(wi)
                assign[j] = wi
                break
    for j, c in enumerate(conns):
        has_pins = any(c['pins'])
        # PIN
        if not has_pins or not comparable:
            pin = NA
        elif out['pin_unread']:
            pin = UNREAD
        elif j in assign:
            pin = OK
        else:
            pin = WEAK if _squash(c['number']) in _DRAIN or _squash(c['raw']) in _DRAIN else NG
        # 線番（製作図 ↔ 405）
        key = _squash(c['number'])
        if not key or not comparable or not labels405['number']:
            num = NA
        elif key in labels405['number']:
            num = OK
        else:
            num = WEAK if key in _DRAIN else NG
        # 色
        if not c['colors'] or not comparable or not labels405['color']:
            color = NA
        else:
            color = OK if all(_squash(x) in labels405['color'] for x in c['colors']) else NG
        w = wires[assign[j]] if j in assign else None
        out['rows'].append({**c, 'wire': w, '判定_PIN': pin, '判定_線番': num, '判定_色': color})
    for wi, w in enumerate(wires):
        if wi not in used:
            out['extras'].append(w)
    return out


def _wire_cells(ctx, row, ch, sel, mine, pick, multi, fab_conns):
    """芯線の 線番・PIN・色 のセルと、芯線の照合の行。"""
    num_cell, pin_cell, color_cell = _Cell('芯線 線番'), _Cell('芯線 PIN'), _Cell('芯線 色')
    fab_names = [d['drawing_number'] for d in ch['fabs']]

    # --- 線番設定表 ↔ 製作図: `*n` の集合
    fab_n = set().union(*(d['fab_wire_numbers'] for d in ch['fabs'])) if ch['fab_ok'] else set()
    fmt = lambda s: ','.join(f'*{n}' for n in sorted(s))  # noqa: E731
    if mine and fab_n:
        if not sel:
            num_cell.add('線番設定表', NG, B_NO_ROW)
        else:
            wire_n = set()
            for x in sel:
                wire_n.update(int(mm.group(1)) for k, v in x['row'].items()
                              if v and (mm := re.fullmatch(r'線番(\d+)', k)))
            same = fab_n == wire_n
            num_cell.add('線番設定表', OK if same else NG,
                         f'{B_MATCH}（製作図 {fmt(fab_n)} / 線番設定表 {fmt(wire_n)}）' if same
                         else f'製作図 {fmt(fab_n)} ≠ 線番設定表 {fmt(wire_n)}')
            num_cell.source(*sorted({x['drawing']['drawing_number'] for x in sel}), *fab_names)
            num_cell.value('線番設定表', f'線番設定表 {fmt(wire_n)}').value('製作図', f'製作図 {fmt(fab_n)}')

    # --- 製作図 ↔ 展開接続図（`*n` は選んだ行の値で置き換える。行ごとに試し、最もよいものを採る）
    candidates = [x['row'] for x in sel] or [None]
    best = None
    for wr in candidates:
        mat = _wire_matrix(fab_conns, wr, pick, multi)
        score = _worst([r['判定_線番'] for r in mat['rows']])
        if best is None or SEVERITY[score] < SEVERITY[best[0]]:
            best = (score, mat, wr)
    mat, wr = best[1], best[2]
    src = pick['drawing']['drawing_number'] if pick is not None else ''

    if pick is not None and fab_conns:
        if multi:
            for c in (num_cell, pin_cell, color_cell):
                c.add('展開接続図', NA, '多芯ケーブルを1本の線で描いた図面のため比べない')
        else:
            rows = mat['rows']
            for cell, key, label in ((pin_cell, '判定_PIN', 'PIN'), (num_cell, '判定_線番', '線番'), (color_cell, '判定_色', '色')):
                syms = [r[key] for r in rows if r[key] != NA]
                if not syms:
                    if label == 'PIN' and any(any(r['pins']) for r in rows):
                        cell.add('展開接続図', UNREAD, f'{B_UNREAD}（ピンが取れない）' if mat['pin_unread'] else '根拠なし')
                    continue
                sym = _worst(syms)
                n_ok = sum(s == OK for s in syms)
                if sym == WEAK:
                    basis = f'{B_EXCLUDING_SG}（{n_ok}/{len(syms)} 本）'
                elif sym == OK:
                    basis = f'{B_MATCH}（{n_ok}/{len(syms)} 本）'
                else:
                    bad = [r for r in rows if r[key] == sym]
                    shown = {'PIN': lambda r: '/'.join(p for p in r['pins'] if p),
                             '線番': lambda r: r['number'], '色': lambda r: '、'.join(r['colors'])}[label]
                    basis = f'合わない: {"、".join(shown(r) for r in bad[:6])}' if sym == NG else B_UNREAD
                cell.add('展開接続図', sym, basis)
        for c in (num_cell, pin_cell, color_cell):
            c.source(src, *fab_names)
        pin_cell.value('製作図', '、'.join('/'.join(p for p in r['pins'] if p) for r in mat['rows'] if any(r['pins'])))
        num_cell.value('製作図', ' '.join(r['number'] or r['raw'] for r in mat['rows']))
        color_cell.value('製作図', '、'.join(x for r in mat['rows'] for x in r['colors']))
        if not multi:
            occ = pick['occ']
            pin_cell.value('展開接続図', '、'.join('/'.join(x for x in ((w['ends'].get(s) or {}).get('pin') for s in '-+') if x)
                                                  for w in occ['wires']))
            labels = [x for w in occ['wires'] for x in w['labels'][:2]]
            num_cell.value('展開接続図', '、'.join(dict.fromkeys(x for x in labels if not _is_color(x))))
            color_cell.value('展開接続図', '、'.join(dict.fromkeys(x for x in labels if _is_color(x))))
    for cell in (num_cell, pin_cell, color_cell):
        if not cell.comparisons:
            cell.note = '根拠が無い' if ch['fab_ok'] else '製作図の図面が特定できない'
    return num_cell, pin_cell, color_cell, mat, wr


# ---------------------------------------------------------------- 一覧表の行を評価する

class _Ctx:
    def __init__(self, drawings):
        self.drawings = drawings
        self.by_name = {d['drawing_number']: d for d in drawings}
        self.schematics_read = any(d.get('schematic') is not None for d in drawings)
        self.unit_keys = _all_unit_keys(drawings)


def _correspondence_cells(ctx, row, ch, list_name, pick):
    """図面の対応（一覧表・製作図・線番設定表・展開接続図）のセル。線番設定表は3つの比較（図面・記載・注記の製作図）に分ける。"""
    cable = row['ケーブル番号']
    list_cell = _Cell('一覧表').add('一覧表', OK, f'{list_name} 行{row["行"]}').source(list_name)
    fab_cell = _Cell('製作図').value('一覧表', SEP.join(ch['fab_patterns']))
    if not ch['fab_patterns']:
        fab_cell.note = '製作図パターンが空'
    elif ch['fab_ok']:
        names = '、'.join(d['drawing_number'] for d in ch['fabs'])
        fab_cell.add('製作図', OK, f'パターン {len(ch["fab_patterns"])}件が各1件に当たる（{names}）').value('製作図', names)
        fab_cell.source(*(d['drawing_number'] for d in ch['fabs']))
    else:
        fab_cell.add('製作図', NG, f'パターン {ch["fab_problem"]}')
        fab_cell.source(*(d['drawing_number'] for d in ch['fabs']))

    w_fig, w_row, w_note = _Cell('線番設定表 図面の特定'), _Cell('線番設定表 記載'), _Cell('線番設定表 注記の製作図')
    wire_names = '、'.join(d['drawing_number'] for d in ch['wires']) if ch['wire_ok'] else ''
    if not ch['fab_ok']:
        for c in (w_fig, w_row, w_note):
            c.note = '製作図の図面が特定できない'
    elif not ch['notes']:
        for c in (w_fig, w_row, w_note):
            c.note = '製作図の注記に線番設定表が無い'
    else:
        w_fig.value('製作図', '、'.join(ch['notes']))
        if ch['wire_ok']:
            w_fig.add('線番設定表', OK, f'パターン {"、".join(ch["notes"])} が各1件に当たる（{wire_names}）')
            w_fig.value('線番設定表', wire_names).source(*(d['drawing_number'] for d in ch['wires']))
            w_row.source(*(d['drawing_number'] for d in ch['wires']))
            if ch['mine']:
                w_row.add('線番設定表', OK, f'{len(ch["mine"])}行').value('線番設定表', f'{len(ch["mine"])}行')
            else:
                w_row.add('線番設定表', NG, f'{wire_names} に {cable} の行が無い')
            wire_fab = [p for d in ch['wires'] for p in d['wire_notes']]
            w_note.source(*(d['drawing_number'] for d in ch['wires']))
            w_note.value('一覧表', SEP.join(ch['fab_patterns'])).value('線番設定表', SEP.join(wire_fab))
            if not wire_fab:
                w_note.note = '線番設定表の注記に製作図が無い'
            elif set(wire_fab) & set(ch['fab_patterns']):
                w_note.add('線番設定表', OK, f'{B_MATCH}（{"、".join(wire_fab)}）')
            else:
                w_note.add('線番設定表', NG, f'線番設定表の注記 {"、".join(wire_fab)} ≠ 一覧表 {"、".join(ch["fab_patterns"])}')
        else:
            w_fig.add('線番設定表', NG, f'パターン {ch["wire_problem"]}')
            w_row.note = w_note.note = '線番設定表の図面が特定できない'
            w_fig.source(*(d['drawing_number'] for d in ch['wires']))

    sch_cell = _Cell('展開接続図').value('一覧表', SEP.join(_values(row.get('接続図パターン'))))
    patterns = _values(row.get('接続図パターン'))
    if not ctx.schematics_read:
        sch_cell.note = '展開接続図の図面が読み取られていない（アップロードに無い）'
    elif not patterns:
        sch_cell.note = '接続図パターンが空'
    else:
        names = _schematic_names(ctx, patterns)
        if not names:
            sch_cell.add('展開接続図', NG, f'アップロードに無い（パターン {"、".join(patterns)}: 0件）')
        else:
            read = [n for n in names if ctx.by_name[n].get('schematic') is not None]
            unreadable = [n for n in read if ctx.by_name[n]['schematic']['error'] or ctx.by_name[n]['schematic']['no_text']]
            sch_cell.source(*names).value('展開接続図', '、'.join(names))
            if pick is not None:
                sch_cell.add('展開接続図', OK, f'{pick["drawing"]["drawing_number"]} に出現あり（{pick["occ"]["how"]}）')
            elif unreadable and len(unreadable) == len(read):
                sch_cell.add('展開接続図', UNREAD, f'{B_UNREAD}（{"、".join(unreadable)} は文字が無い・読めない図面）')
            elif not read:
                sch_cell.add('展開接続図', UNREAD, f'{B_UNREAD}（{"、".join(names)}）')
            else:
                sch_cell.add('展開接続図', NG, f'{B_MISSING}（{"、".join(names)} に {cable} の出現が無い）')
    return {'一覧表': [list_cell], '製作図': [fab_cell], '線番設定表': [w_fig, w_row, w_note], '展開接続図': [sch_cell]}


def _evaluate_row(ctx, list_name, row):
    """一覧表の1行について、全項目のセルと芯線の照合の行を作る。Returns: ({項目の列名: [セル]}, 芯線の照合の行)"""
    cable = row['ケーブル番号']
    ch = _chain(row, ctx.by_name)
    pick = _pick_occurrence(ctx, row)
    fab_conns = [(d, c) for d in ch['fabs'] for c in d['fab_conns'] if c.get('端の数') == 2] if ch['fab_ok'] else []
    multi = pick is not None and _is_single_multicore(row, pick['occ'], len(fab_conns))
    sel, _how = _select_wire_rows(row, ch['mine'])

    cells = {}
    for key, lst in _correspondence_cells(ctx, row, ch, list_name, pick).items():
        cells[item_column('図面の対応', key)] = lst
    for i in (1, 2):
        unit_cell, mod_cell, code_cell = _terminal_cells(ctx, row, i, sel, ch['mine'], pick)
        part = _fab_text_cell(f'端末{i} 部品種別', _values(row.get(f'端末{i}_部品種別')), ch['fabs'], ch['fab_ok'])
        for name, c in (('ユニット', unit_cell), ('モジュール', mod_cell), ('機器符号', code_cell), ('部品種別', part)):
            cells[item_column(f'端末{i}', name)] = [c]
    kind_cell, core_cell = _count_cells(row, ch, sel, ch['mine'], fab_conns, pick, multi)
    cells[item_column('ケーブル', '種類')] = [kind_cell]
    cells[item_column('ケーブル', '芯数')] = [core_cell]
    num_cell, pin_cell, color_cell, mat, wr = _wire_cells(ctx, row, ch, sel, ch['mine'], pick, multi, fab_conns)
    cells[item_column('芯線', '線番')] = [num_cell]
    cells[item_column('芯線', 'PIN')] = [pin_cell]
    cells[item_column('芯線', '色')] = [color_cell]

    wire_rows = []
    if ch['fab_ok']:
        wire_name = '、'.join(d['drawing_number'] for d in ch['wires']) if ch['wire_ok'] else ''
        sch_name = pick['drawing']['drawing_number'] if pick is not None else ''
        fab_name = '、'.join(d['drawing_number'] for d in ch['fabs'])
        for r in mat['rows']:
            w = r['wire']
            wire_rows.append({
                'ケーブル番号': cable, '一覧表の図番': list_name, '一覧表の行': row['行'], '線番接続ID': r['id'],
                '線番設定表の線番': r['number'] if r['id'] else '', '製作図の端1 PIN': r['pins'][0], '製作図の端2 PIN': r['pins'][1],
                '製作図の線番': r['raw'], '製作図の色': '、'.join(r['colors']),
                '展開接続図の端- PIN': w['pins']['-'] if w else '', '展開接続図の端+ PIN': w['pins']['+'] if w else '',
                '展開接続図のラベル': '、'.join(w['labels']) if w else '',
                '判定（PIN）': r['判定_PIN'], '判定（線番）': r['判定_線番'], '判定（色）': r['判定_色'],
                '製作図': fab_name, '線番設定表': wire_name, '展開接続図': sch_name})
        for w in mat['extras']:
            wire_rows.append({
                'ケーブル番号': cable, '一覧表の図番': list_name, '一覧表の行': row['行'], '線番接続ID': '',
                '線番設定表の線番': '', '製作図の端1 PIN': '', '製作図の端2 PIN': '', '製作図の線番': '', '製作図の色': '',
                '展開接続図の端- PIN': w['pins']['-'], '展開接続図の端+ PIN': w['pins']['+'],
                '展開接続図のラベル': '、'.join(w['labels']),
                '判定（PIN）': NA, '判定（線番）': NA, '判定（色）': NA,
                '製作図': fab_name, '線番設定表': wire_name, '展開接続図': sch_name})
    return cells, wire_rows


def _orphan_cells(cable, wire_drawings, schematic_drawings):
    """一覧表に無いケーブル（線番設定表・展開接続図にだけある）のセル。一覧表だけ ×、ほかは －。"""
    cells = {col: [_Cell(item_label(col), note='一覧表に無いケーブル')] for col in ITEM_COLUMNS}
    parts = []
    if wire_drawings:
        parts.append(f'線番設定表（{"、".join(wire_drawings)}）')
    if schematic_drawings:
        parts.append(f'展開接続図（{"、".join(schematic_drawings)}）')
    c = _Cell('一覧表').add('一覧表', NG, f'どの一覧表の行にも無い: {"、".join(parts)}')
    c.source(*wire_drawings, *schematic_drawings)
    c.value('線番設定表', '、'.join(wire_drawings)).value('展開接続図', '、'.join(schematic_drawings))
    cells[item_column('図面の対応', '一覧表')] = [c]
    return cells


# ---------------------------------------------------------------- 照合の本体

def check_consistency(drawings):
    """全図面の dict から、一覧表の行ごとの照合結果を作る。

    Returns:
        {'rows': [{'ケーブル番号', '一覧表の図番', '一覧表の行', '不整合数', '要確認数', 'cells': {列名: 記号}, 'links': {列名: 照合明細の通し番号}}],
         'details': [照合明細の行],            # 整合性チェックの全セルに1行ずつ（線番設定表だけ3行）
         'wires': [芯線の照合の行],
         'drawings': drawings, 'unused': [照合に使っていない図面の図番]}
        行の並びはケーブル番号→一覧表の図番→一覧表の行。一覧表に無いケーブル（線番設定表・展開接続図にだけある）は
        ケーブル番号の並びの中に、一覧表の図番・行を空にして入る。
    """
    ctx = _Ctx(drawings)
    work = []                       # (ケーブル番号, 一覧表の図番, 行番号, 一覧表の行 または None, 一覧表に無いケーブルの情報)
    list_cables = set()
    for d in drawings:
        for r in d['list_rows']:
            if r['ケーブル番号']:
                list_cables.add(r['ケーブル番号'])
                work.append((r['ケーブル番号'], d['drawing_number'], r['行'] or 0, r, None))
    orphan = {}
    for d in drawings:
        for r in d['wire_rows']:
            for cable in _values(r.get('ケーブル番号')):
                if cable not in list_cables:
                    orphan.setdefault(cable, {'wire': set(), 'schematic': set()})['wire'].add(d['drawing_number'])
        s = d.get('schematic')
        if s:
            for occ in s['occurrences']:
                if occ['cable'] not in list_cables:
                    orphan.setdefault(occ['cable'], {'wire': set(), 'schematic': set()})['schematic'].add(d['drawing_number'])
    for cable, info in orphan.items():
        work.append((cable, '', 0, None, info))
    work.sort(key=lambda w: (w[0], w[1], w[2]))

    rows, details, wires = [], [], []
    for cable, list_name, row_no, row, info in work:
        if row is not None:
            cells, wire_rows = _evaluate_row(ctx, list_name, row)
        else:
            cells, wire_rows = _orphan_cells(cable, sorted(info['wire']), sorted(info['schematic'])), []
        wires += wire_rows
        out = {'ケーブル番号': cable, '一覧表の図番': list_name, '一覧表の行': row_no or '', 'cells': {}, 'links': {}}
        n_ng = n_unread = 0
        for col in ITEM_COLUMNS:
            group = cells[col]
            sym = _worst([c.symbol for c in group])
            out['cells'][col] = sym
            out['links'][col] = len(details)
            n_ng += sym == NG
            n_unread += sym == UNREAD
            for c in group:
                details.append({
                    'ケーブル番号': cable, '一覧表の図番': list_name, '一覧表の行': row_no or '', '項目': c.label, '判定': c.symbol,
                    '一覧表の値': c.values.get('一覧表', ''), '線番設定表の値': c.values.get('線番設定表', ''),
                    '製作図の値': c.values.get('製作図', ''), '展開接続図の値': c.values.get('展開接続図', ''),
                    '判断根拠': c.basis, '参照した図面': '、'.join(c.sources)})
        out['不整合数'], out['要確認数'] = int(n_ng), int(n_unread)
        rows.append(out)

    # 照合に使っていない図面: 393 の種類が無く、どのパターンにも当たらない
    # 図中の参照（KIND_REF_IN_SCHEMATIC）で指されただけの図面は、照合には使っていないので「使っていない図面」のまま
    hit = {name for r in pattern_resolutions(drawings) if r['種別'] != KIND_REF for name in r['当たった図面'].split('、') if name}
    unused = [d['drawing_number'] for d in sorted(drawings, key=lambda d: d['drawing_number'])
              if not d['kinds'] and d['drawing_number'] not in hit]
    return {'rows': rows, 'details': details, 'wires': wires, 'drawings': drawings, 'unused': unused}


# ---------------------------------------------------------------- 3. 判定の根拠（明細）

def pattern_resolutions(drawings):
    """図面パターンの解決結果。どのパターンが、どの図面に、何件当たったか（照合 1・3・5 の根拠）。

    Returns: [{'種別', 'パターン', '参照元の図番', '当たった図面', '件数', '備考'}, ...]
        種別: 製作図（一覧表・線番設定表の注記が指す）／線番設定表（製作図の注記が指す）／展開接続図（一覧表の接続図パターンが指す）／
        展開接続図（図中の参照: 展開接続図の `to … (-405-08)` のような文字が指す。備考に参照元の文字。照合には使わない）。
    """
    by_name = {d['drawing_number']: d for d in drawings}
    out, seen = [], set()

    def add(kind, pattern, make_regex, source, remark=''):
        hits = _resolve(make_regex(pattern), by_name)
        key = (kind, pattern, source)
        if key in seen:
            return
        seen.add(key)
        note = ' ／ '.join(x for x in (remark, '文字が無い図面' if any(h['no_text'] for h in hits) else '') if x)
        out.append({'種別': kind, 'パターン': pattern, '参照元の図番': source,
                    '当たった図面': '、'.join(h['drawing_number'] for h in hits), '件数': len(hits), '備考': note})
    for d in sorted(drawings, key=lambda d: d['drawing_number']):
        src = d['drawing_number']
        for r in d['list_rows']:
            for p in _values(r.get('製作図パターン')):
                add('製作図（一覧表）', p, fab_pattern_regex, src)
            for p in _values(r.get('接続図パターン')):
                add('展開接続図（一覧表）', p, fab_pattern_regex, src)
        for p in d['fab_notes']:
            add('線番設定表（製作図の注記）', p, wire_pattern_regex, src)
        for p in d['wire_notes']:
            add('製作図（線番設定表の注記）', p, fab_pattern_regex, src)
        refs = {}
        for ref in (d.get('schematic') or {}).get('refs', []):
            refs.setdefault(ref['pattern'], []).append(ref['text'])
        for p, texts in refs.items():
            add(KIND_REF, p, wire_pattern_regex, src, remark=' ／ '.join(texts))
    return out


def fab_text_rows(drawings):
    """照合 2 が探す対象の製作図の文字。製作図のパターンに当たった図面と、線番/PIN の表がある図面。"""
    hit = {name for r in pattern_resolutions(drawings) if r['種別'].startswith('製作図')
           for name in r['当たった図面'].split('、') if name}
    return [(d['drawing_number'], t) for d in sorted(drawings, key=lambda d: d['drawing_number'])
            if d['drawing_number'] in hit or KIND_FAB in d['kinds'] for t in d.get('fab_texts', [])]
