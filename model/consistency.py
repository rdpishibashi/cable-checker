"""整合性チェック: 393（ケーブル一覧表・線番設定表・ケーブル製作図）と展開接続図（405）の照合（streamlit 非依存）。

3段に分ける。
  1. `analyze_drawing(path, name)` — DXF を1回だけ読み、393 の3種類の読み取りを走らせて、図面ごとの dict にする
  2. `analyze_uploads(items_405, items_393)` — 393 のファイルに 1 を行い、405 のファイルは抽出だけを行い、一覧表の「接続図パターン」が指す
     405 の図面だけを `schematic_reader.analyze_schematic()` で読む。結果は図面の dict の `schematic` に入る。ケーブル番号の抽出結果も返す
  3. `check_consistency(drawings)` — 一覧表の行ごとに、項目（図面の対応・端末・ケーブル・芯線）の判定と根拠を作る
`run_check(items_405, items_393)` が 2・3 とケーブル番号の比較（`cable_compare`）をまとめて行う。
パターンの解決はセットの中で行う: 製作図・線番設定表のパターンは 393 のセット、接続図パターンと図中の参照は 405 のセット。

照合の定義（記号・項目・根拠）は docs/CONSISTENCY_CHECK.md が正。チェーンは
一覧表の行 →（製作図パターン）→ 製作図 →（注記の線番設定表パターン）→ 線番設定表、
一覧表の行 →（接続図パターン）→ 展開接続図。`check_consistency()` は dict だけを受け取るので、合成データでそのままテストできる。

判定の記号（重い順）: × 不整合 ＞ ？ 読めない（図面で確認が必要）＞ △ 一致（根拠が弱い）＞ ○ 一致 ＞ － 対象外・根拠なし。
1つの項目に複数の比較（線番設定表・製作図・展開接続図）があるときは、最も重いものをその項目の判定にする。

図面の dict（`analyze_drawing()` の戻り値＋2段目）:
  drawing_number, set（'405'＝展開接続図のセット／'393'＝ケーブル図のセット。必ずある）, no_text（文字が1つも無い）, kinds（'ケーブル一覧表'・'線番設定表'・'ケーブル製作図'・'展開接続図'）,
  list_rows（一覧表の行。`cable_list_table.parse()` の rows）,
  wire_rows（線番設定表の行。`'_lines'` に接続先ごとの行）, wire_notes（注記の製作図パターン）,
  fab_wire_numbers（製作図の線番の表にある `*n` の n）, fab_notes（注記の線番設定表パターン）,
  fab_texts（図面の全文字。削除済みを除く）, fab_text（その文字を空白なし・大文字でつなげたもの）,
  fab_conns（製作図の接続の明細）, schematic（展開接続図として読んだ結果。`schematic_reader.analyze_schematic()`）
"""
import itertools
import os
import re
import tempfile
import unicodedata
from collections import Counter

import ezdxf

from . import cable_list_table, fab_drawing, wire_number_table
from .cable_compare import compare_extracts
from .cable_extractor import extract_cables
from .cell_values import SEP, awg_sizes, code_matches_name, codes as _codes, is_color as _is_color, item_column, nfkc_squash as _nfkc_squash  # noqa: F401
from .cell_values import side as _side, size_key, squash as _squash, values as _values
from . import highlights
from .drawing_geometry import load
from .schematic_reader import analyze_schematic
from .symbols import NA, NG, OK, SEVERITY, SYMBOLS, UNREAD, WEAK  # noqa: F401  (公開名。他のモジュールとテストが consistency から使う)
from .symbols import worst as _worst

# 判定の記号・重さは symbols.py が正（ここから公開名として使えるようにそのまま import する）

KIND_LIST = 'ケーブル一覧表'
KIND_WIRE = '線番設定表'
KIND_FAB = 'ケーブル製作図'
KIND_SCHEMATIC = '展開接続図'
KIND_REF = '図中の参照'  # 図番パターン照合シートの「参照元図面種別」（展開接続図の文字が指す。照合には使わない）

SET_405, SET_393 = '405', '393'     # アップロードのセット（展開接続図／ケーブル図）

PHASE_DRAWINGS = 'ケーブル図の読み取り'
PHASE_EXTRACT = '展開接続図の抽出'
PHASE_SCHEMATIC = '展開接続図の読み取り'

# 項目（グループ, 項目）。整合性チェックシートの列と、Summary の行になる。列名は `グループ｜項目`
ITEMS = [
    ('図面の対応', '一覧表'), ('図面の対応', '製作図'), ('図面の対応', '線番設定表'), ('図面の対応', '展開接続図'),
    *[(f'端末{i}', x) for i in (1, 2) for x in ('ユニット', 'モジュール', '機器符号', '部品種別')],
    ('ケーブル', '種類'), ('ケーブル', 'サイズ'), ('ケーブル', '芯数'),
    ('芯線', '線番'), ('芯線', 'PIN'), ('芯線', '色'),
]


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

def _empty_drawing(name, no_text=False, drawing_set=SET_393):
    return {'drawing_number': name, 'set': drawing_set, 'no_text': no_text, 'kinds': [], 'list_rows': [], 'wire_rows': [],
            'wire_notes': [], 'fab_wire_numbers': set(), 'fab_notes': [], 'fab_texts': [], 'fab_text': '',
            'fab_conns': [], 'fab_text_refs': [], 'schematic': None}


def analyze_drawing(path, name, doc=None):
    """DXF を1回読み、図面の dict を返す。読めなければ ValueError。

    Args:
        path: DXF のパス。
        name: 図番（拡張子なしのファイル名）。
        doc: 解析済みの ezdxf 文書（省略可。渡されたら `path` を読み直さない。読み取りのみ）。
    """
    geo = load(path, doc=doc)
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
            row = {'表': ti, '行': r['_row'], '_lines': r['_lines'], '_refs': r.get('_refs', {}),
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
        'set': SET_393,
        'no_text': not geo.txts,
        'kinds': kinds,
        'list_rows': listing['rows'] if listing else [],
        'wire_rows': wire_rows,
        'wire_notes': [p for p, _ in wire['notes']],
        'fab_wire_numbers': fab_wire_numbers,
        'fab_notes': [p for p, _ in fab['notes']],
        'fab_texts': fab['texts'],
        'fab_text': _squash(' '.join(t for t, _ in fab['text_refs'])),
        'fab_text_refs': fab['text_refs'],
        'fab_conns': fab_drawing.connection_rows(fab),
        'schematic': None,          # 2段目（analyze_uploads）が展開接続図として読んだときだけ入る
    }


def analyze_uploads(items_405, items_393, progress=None):
    """アップロードされた2つのセットを解析する。393 は抽出と 393 の読み取り、405 は抽出だけを行い、そのあと一覧表の「接続図パターン」が指す
    405 の図面だけを展開接続図として読む。

    Args:
        items_405, items_393: [(元のファイル名, bytes), ...]。呼び出し側でフィルタ・同名除去済みであること。
        progress: 省略可。`progress(完了数, 全体数, 図番またはファイル名, 段階)` を各ファイルの処理前に呼ぶ（段階は PHASE_*）。

    Returns:
        (図面の dict のリスト, 抽出結果, エラーのリスト)。抽出結果は {'405': [extract_cables の結果], '393': [...]}（図番順）。
        エラーは [(ファイル名, メッセージ), ...]。1つのファイルが壊れていても残りは処理を続ける（壊れたファイルは図面にも抽出結果にも入らない）。
    """
    drawings, errors = [], []
    extracts = {SET_405: [], SET_393: []}
    for i, (name, data) in enumerate(items_393):
        if progress:
            progress(i, len(items_393), name, PHASE_DRAWINGS)
        stem = os.path.splitext(os.path.basename(name))[0]
        try:
            def read_393(path, name=name, stem=stem):
                doc = _read_doc(path)       # 1つのファイルの抽出と読み取りで、DXF の解析は1回
                return extract_cables(path, name, doc=doc), analyze_drawing(path, stem, doc=doc)
            extract, drawing = _with_temp_file(data, read_393)
        except Exception as e:  # noqa: BLE001 - 壊れた DXF 1件で全体を止めない
            errors.append((name, f'{type(e).__name__}: {e}'))
            continue
        extracts[SET_393].append(extract)
        drawings.append(drawing)
    # 展開接続図として読む 405 の図面は、一覧表の「接続図パターン」が指すもの。ファイル名（図番）だけで決まるので、読む前に決められる
    stems_405 = [os.path.splitext(os.path.basename(name))[0] for name, _ in items_405]
    targets = set(schematic_targets(drawings + [_empty_drawing(stem, drawing_set=SET_405) for stem in stems_405]))
    for i, ((name, data), stem) in enumerate(zip(items_405, stems_405)):
        if progress:
            progress(i, len(items_405), name, PHASE_SCHEMATIC if stem in targets else PHASE_EXTRACT)

        def read_one(path, name=name, stem=stem):
            # 1つのファイルの中で、抽出と展開接続図の読み取りを続けて行う（DXF の解析は1回）
            doc = _read_doc(path)
            extract = extract_cables(path, name, doc=doc)
            if stem not in targets:
                return extract, None, None
            try:
                return extract, analyze_schematic(path, stem, title=extract.get('title', ''), doc=doc), None
            except Exception as e:  # noqa: BLE001 - 読み取りの失敗は抽出結果を残してエラーにする
                return extract, None, f'{type(e).__name__}: {e}'
        try:
            extract, schematic, schematic_error = _with_temp_file(data, read_one)
        except Exception as e:  # noqa: BLE001
            errors.append((name, f'{type(e).__name__}: {e}'))
            continue
        extracts[SET_405].append(extract)
        drawing = _empty_drawing(stem, drawing_set=SET_405)
        if schematic_error:
            errors.append((name, schematic_error))
        elif schematic is not None:
            drawing['schematic'] = schematic
            drawing['kinds'].append(KIND_SCHEMATIC)
            drawing['no_text'] = schematic['no_text']
        drawings.append(drawing)
    for results in extracts.values():
        results.sort(key=lambda r: r['drawing_number'])
    return drawings, extracts, errors


def run_check(items_405, items_393, progress=None):
    """解析（`analyze_uploads`）→ 照合（`check_consistency`）→ ケーブル番号の比較（`compare_extracts`）をまとめて行う。

    Returns:
        (check_consistency() の戻り値に 'extracts'（抽出結果）と 'compare'（`compare_extracts()` の戻り値）を足したもの, エラーのリスト)
    """
    drawings, extracts, errors = analyze_uploads(items_405, items_393, progress)
    result = check_consistency(drawings)
    result['extracts'] = extracts
    result['compare'] = compare_extracts(extracts[SET_405], extracts[SET_393])
    return result, errors


def _read_doc(path):
    """DXF を1回読んで ezdxf 文書を返す（各処理へ `doc=` で渡し、同じファイルを何度も解析しない。読み取りのみで使う）。

    読めないときは None（各処理が `doc=None` で `path` を自分で読み直し、従来どおりの文言で失敗する）。
    """
    try:
        return ezdxf.readfile(path)
    except Exception:  # noqa: BLE001 - 失敗の扱い（エラー文言）は各処理に任せる
        return None


def _with_temp_file(data, func):
    """データを一時ファイルに書き、`func(path)` を呼ぶ。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'input.dxf')
        with open(path, 'wb') as f:
            f.write(data)
        return func(path)


# ---------------------------------------------------------------- 照合の補助（パターンの解決など）

def fab_pattern_regex(pattern):
    """一覧表の製作図パターン（`*` は任意の1文字）。末尾に改訂記号（英字1字）があってもよい。"""
    body = ''.join('[0-9A-Z]' if c == '*' else re.escape(c) for c in pattern.upper())
    return re.compile(f'^{body}[A-Z]?$')


def wire_pattern_regex(pattern):
    """製作図の注記の線番設定表パターン（`-634-07`）。頭の `<英字2><数字4>` は任意、末尾の改訂記号も任意。"""
    return re.compile(r'^[A-Z]{2}\d{4}' + re.escape(pattern.upper()) + '[A-Z]?$')


def _index(drawings, drawing_set):
    """セットごとの {図番: 図面の dict}。パターンの解決はセットの中で行う（製作図・線番設定表は 393、展開接続図は 405）。"""
    return {d['drawing_number']: d for d in drawings if d['set'] == drawing_set}


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

def schematic_targets(drawings):
    """一覧表の「接続図パターン」が指す展開接続図（405 のセット）の図番（昇順）。この図面だけを展開接続図として読む。"""
    by_name = _index(drawings, SET_405)
    patterns = {p for d in drawings for r in d['list_rows'] for p in _values(r.get('接続図パターン'))}
    return sorted({d['drawing_number'] for p in patterns for d in _resolve(fab_pattern_regex(p), by_name)})


# ---------------------------------------------------------------- 照合の部品

def _unit_key(s):
    """ユニット名の比較用: NFKC・括弧の補足と `*`（モジュールの伏せ字）を除く・空白を詰める・大文字。"""
    s = re.sub(r'\([^)]*\)', '', unicodedata.normalize('NFKC', s or ''))
    return _squash(s).replace('*', '')


def _module_key(module):
    return _nfkc_squash(module).replace('*', '')


_MODULE_TOKEN = re.compile(r'(?<![0-9A-Z])[FR]\d(?![0-9A-Z])')
def _module_tokens(text):
    return set(_MODULE_TOKEN.findall(unicodedata.normalize('NFKC', text or '').upper()))


def _core_count(row):
    m = re.search(r'\d+', row.get('芯数') or '')
    return int(m.group()) if m else None


def _code_match(code, names):
    """一覧表の機器符号（`*` は任意の1文字）が、405 の端の名前（1行目・括弧の前）にあるか。`GND` を含めば `GND` を含む名前と一致。"""
    return any(code_matches_name(code, n) for n in names)


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
        self.decided = []            # 展開接続図でユニットを決めた文字 [(記号, 文字)]（viewer の強調が使う。照合を二重にしないため）

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
    """接続図パターンが指す展開接続図（405 のセット。`schematic_targets()` と同じ）。"""
    return sorted({d['drawing_number'] for p in patterns for d in _resolve(fab_pattern_regex(p), ctx.by_name_405)})


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


def _occurrences_of(ctx, row):
    """接続図パターンが当たった図面にある、この行のケーブル番号の出現 [(図面, 読み取り結果, 出現)]。"""
    out = []
    for name in _schematic_names(ctx, _values(row.get('接続図パターン'))):
        d = ctx.by_name_405[name]
        s = d.get('schematic')
        if not s:
            continue
        out += [(d, s, occ) for occ in s['occurrences'] if occ['cable'] == row['ケーブル番号']]
    return out


def _pick_occurrence(ctx, row):
    """接続図パターンが当たった図面にある、このケーブル番号の出現のうち、機器符号の判定が最も良いもの。無ければ None。"""
    cands = [(d, s, occ, _orientation(row, occ)) for d, s, occ in _occurrences_of(ctx, row)]
    if not cands:
        return None
    chosen = ctx.assignment.get(id(row))
    if chosen is not None:      # 同じケーブル番号が一覧表に複数行あるとき: ケーブル番号ごとに決めた行と出現の割り当て
        d, s, occ, orient = next((c for c in cands if c[2] is chosen), cands[0])
    else:
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


def _terminal_cells(ctx, row, i, sel, mine, pick, fabs=(), fab_ok=False):
    """端末 i（1・2）の ユニット・モジュール・機器符号 のセル。fabs・fab_ok は解決した製作図（機器符号を製作図の文字とも比べる）。"""
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
                        unit_cell.decided.append((sym, text))
                if m:
                    title = pick['title']
                    if any(m in _nfkc_squash(t) for t in decided) or (decided and m in _nfkc_squash(title)):
                        mod_cell.add('展開接続図', OK, '一致（ユニットの文字）' if any(m in _nfkc_squash(t) for t in decided)
                                     else '一致（図面タイトル）')
                    elif any('*' in t for t in decided):
                        # 図面のユニット名の `*`（`TMP CONTR **`）はモジュールの伏せ字。文字数はモジュールと一致しないことがあるので、数は問わない
                        mod_cell.add('展開接続図', OK, '一致（ユニットの文字のワイルドカード）')
                    elif decided:
                        others = set().union(*(_module_tokens(t) for t in decided))
                        if others:
                            mod_cell.add('展開接続図', NG, f'別のモジュール（{"、".join(sorted(others))}）')
                        else:
                            mod_cell.add('展開接続図', UNREAD, f'モジュールの記載が見つからない（{"、".join(decided)}）')
                    elif unit_cell.symbol == UNREAD:
                        mod_cell.add('展開接続図', UNREAD, B_UNREAD)
    # --- ケーブル製作図（機器符号は、製作図の文字1つ全体と一致するか。部品種別のような部分一致にはしない）
    # 製作図の多くは機器符号を書かない様式なので、無いことは不整合の根拠にしない: すべて見つかれば ○、そうでなければ比較しない。
    if codes and fab_ok and fabs:
        texts = [t for d in fabs for t, _ in d.get('fab_text_refs', [])]
        if all(_code_match(c, texts) for c in codes):
            code_cell.source(*(d['drawing_number'] for d in fabs))
            code_cell.value('製作図', '、'.join(codes))
            code_cell.add('製作図', OK, B_MATCH)
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


def _size_cell(row, ch, pick):
    """ケーブルの サイズ（一覧表のサイズ ⇔ 図面の AWG の数字）のセル。

    製作図: 記載の文字（注記ブロックを除く）にある AWG の数字のうち、一覧表のサイズの値のすべてが含まれれば ○。AWG はあるが合わなければ ×
    （アース線 GNYE の AWG も数える）。AWG の記載が無ければ比べない。展開接続図: 選んだ出現の備考にある AWG の数字だけを同じ規則で比べる。"""
    values = _values(row.get('サイズ'))
    cell = _Cell('ケーブル サイズ').value('一覧表', SEP.join(values))
    if not values:
        cell.note = B_EMPTY
        return cell
    keys = [size_key(v) for v in values]

    def compare(kind, found):
        ok = all(k in found for k in keys)
        shown = '、'.join(f'AWG{n}' for n in sorted(found, key=lambda n: (len(n), n)))
        cell.add(kind, OK if ok else NG, B_MATCH if ok else f'図面は {shown}')
        cell.value(kind, shown)

    if not ch['fab_ok']:
        cell.note = '製作図の図面が特定できない'
    else:
        found = set()
        for d in ch['fabs']:
            for t, _ in d.get('fab_text_refs', []):
                found |= awg_sizes(t)
        if found:
            compare('製作図', found)
            cell.source(*(d['drawing_number'] for d in ch['fabs']))
        else:
            cell.note = '製作図に AWG の記載が無い'
    found_405 = awg_sizes(pick['occ']['remark']) if pick is not None else set()
    if found_405:
        compare('展開接続図', found_405)
        cell.source(pick['drawing']['drawing_number'])
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

def _conn_vals(c):
    """製作図の接続の左の値・右の値（見出しの「線番」と「PIN No.」は同義。同じ端に両方あれば PIN No.）。`*n` は n 番目の端子（n）として扱う。"""
    vals = []
    for k in (1, 2):
        v = (c.get(f'端{k}_PIN No.') or c.get(f'端{k}_線番') or '').strip().lstrip('*')
        if v:
            vals.append(_squash(v))
    return vals


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
    # 表の見出し「線番」と「PIN No.」は同義。左の値・右の値の組として、芯線の両端のピンに当てる（同じ端に両方あれば PIN No.）。`*n` は n 番目の端子（n）として扱う
    return {'drawing': d['drawing_number'], 'pins': (c.get('端1_PIN No.') or '', c.get('端2_PIN No.') or ''),
            'raw': raw, 'id': conn_id, 'number': resolved, 'colors': [x for x in labels if _is_color(x)], 'vals': _conn_vals(c)}


def _wire_matrix(fab_conns, wire_row, pick, multi):
    """製作図の接続ごとに、展開接続図の芯線と比べた PIN・線番・色の判定を返す。

    Returns: {'rows': [接続ごとの dict], 'extras': [製作図に対応が無い 405 の芯線], 'pin_unread': 405 のピンが1つも読めない}
    """
    conns = [_conn_info(d, c, wire_row) for d, c in fab_conns]
    out = {'rows': [], 'extras': [], 'pin_unread': False}
    wires, labels405 = [], {'number': set(), 'color': set()}
    comparable = pick is not None and not multi
    shield = pick['occ'].get('_shield') if comparable else None   # 展開接続図のケーブル記号のシールド（破線＋端の楕円）
    if comparable:
        for w in pick['occ']['wires']:
            p = {s: ((w['ends'].get(s) or {}).get('pin') or '') for s in '-+'}
            wires.append({'pins': p, 'pool': Counter(_squash(x) for x in p.values() if x), 'labels': w['labels'], 'src': w})
            for x in w['labels'][:2]:
                labels405['color' if _is_color(x) else 'number'].add(_squash(x))
        out['pin_unread'] = not any(w['pool'] for w in wires)
    # 線番の列の値（`1`・`-` など）も、芯線のピンに当たれば一致（見出しの「線番」と「PIN No.」は同義）
    pin_keys = {_squash(x) for w in wires for x in w['pins'].values() if x}
    used, assign = set(), {}
    for j, c in enumerate(conns):
        fp = Counter(c['vals'])
        if not fp or not comparable or all(v in _DRAIN for v in fp):
            continue
        for wi, w in enumerate(wires):
            if wi not in used and not (fp - w['pool']):
                used.add(wi)
                assign[j] = wi
                break
    # ピンの無い接続（線番だけ。表A の `*1`-`*1` など）は、線番（`*n` を置き換えた値）が同じラベルの芯線に割り当てる。判定は変えない
    # （PIN は比べない・線番はラベルで決まる）。割り当てるのは、芯線の図形・ラベルの強調のため。
    loose = set()       # 左右のピンの組が完全には合わず、ラベルまたは一部のピンで割り当てた接続（PIN の判定は ○ にしない）
    if comparable:
        for j, c in enumerate(conns):
            key = _squash(c['number'])
            if j in assign or any(c['pins']) or not key or key in _DRAIN:
                continue
            for wi, w in enumerate(wires):
                if wi not in used and key in {_squash(x) for x in w['labels'][:2] if not _is_color(x)}:
                    used.add(wi)
                    assign[j] = wi
                    loose.add(j)
                    break
    # ピンの組が完全には合わない接続（`GND`-`D` と 4-D など）は、当たり（ピンの一致）が最も大きい芯線に割り当てる（芯線の図形・ピンの文字を、この接続の判定で塗るため）
    if comparable:
        for j, c in enumerate(conns):
            if j in assign or not c['vals'] or all(v in _DRAIN for v in c['vals']):
                continue
            fp = Counter(c['vals'])
            scored = [(sum(min(n, w['pool'][v]) for v, n in fp.items()), wi) for wi, w in enumerate(wires) if wi not in used]
            best = max(scored, default=(0, None))
            if best[0] > 0:
                used.add(best[1])
                assign[j] = best[1]
                loose.add(j)
    for j, c in enumerate(conns):
        has_pins = any(c['pins'])
        pair = bool(c['vals']) and not all(v in _DRAIN for v in c['vals'])      # 左右の値の組を芯線の両端のピンと比べる（SG/SH だけの組は比べない）
        # PIN
        if not pair or not comparable:
            pin = NA
        elif out['pin_unread']:
            pin = UNREAD if has_pins else NA
        elif j in assign and j not in loose:
            pin = OK
        else:
            pin = WEAK if _squash(c['number']) in _DRAIN or _squash(c['raw']) in _DRAIN else NG
        # 線番（製作図 ↔ 405）
        key = _squash(c['number'])
        is_shield = bool(shield) and key in _DRAIN
        if is_shield:
            num = OK                                       # SG/SH は、展開接続図にシールドが描かれていれば一致
        elif not key or not comparable:
            num = NA
        elif key in labels405['number'] or key in pin_keys:
            num = OK
        elif not labels405['number']:
            num = NA
        else:
            num = NA if key in _DRAIN else NG       # SG/SH（シールド）は 405 に線番のラベルが無いので比べない
        # 色
        if not c['colors'] or not comparable or not labels405['color']:
            color = NA
        else:
            color = OK if all(_squash(x) in labels405['color'] for x in c['colors']) else NG
        w = wires[assign[j]] if j in assign else None
        out['rows'].append({**c, 'wire': w, '判定_PIN': pin, '判定_線番': num, '判定_色': color, 'shield': is_shield})
    for wi, w in enumerate(wires):
        if wi not in used:
            out['extras'].append(w)
    return out


def _wire_cells(ctx, row, ch, sel, mine, pick, multi, fab_conns, sibling_sel=()):
    """芯線の 線番・PIN・色 のセルと、芯線照合の行。"""
    num_cell, pin_cell, color_cell = _Cell('芯線 線番'), _Cell('芯線 PIN'), _Cell('芯線 色')
    fab_names = [d['drawing_number'] for d in ch['fabs']]

    # --- 線番設定表 ↔ 製作図: `*n` の集合
    fab_n = set().union(*(d['fab_wire_numbers'] for d in ch['fabs'])) if ch['fab_ok'] else set()
    fmt = lambda s: ','.join(f'*{n}' for n in sorted(s))  # noqa: E731
    if (mine or sibling_sel) and fab_n:
        if not sel and not sibling_sel:
            num_cell.add('線番設定表', NG, B_NO_ROW)
        else:
            wire_n = set()
            for x in (sel or sibling_sel):
                wire_n.update(int(mm.group(1)) for k, v in x['row'].items()
                              if v and (mm := re.fullmatch(r'線番(\d+)', k)))
            same = fab_n == wire_n
            num_cell.add('線番設定表', OK if same else NG,
                         f'{B_MATCH}（製作図 {fmt(fab_n)} / 線番設定表 {fmt(wire_n)}）' if same
                         else f'製作図 {fmt(fab_n)} ≠ 線番設定表 {fmt(wire_n)}')
            num_cell.source(*sorted({x['drawing']['drawing_number'] for x in (sel or sibling_sel)}), *fab_names)
            num_cell.value('線番設定表', f'線番設定表 {fmt(wire_n)}').value('製作図', f'製作図 {fmt(fab_n)}')

    # --- 製作図 ↔ 展開接続図（`*n` は選んだ行の値で置き換える。行ごとに試し、最もよいものを採る）
    candidates = [x['row'] for x in (sel or sibling_sel)] or [None]
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
                    shown = {'PIN': lambda r: '/'.join(r['vals']),
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

_ASSIGN_LIMIT = 5000     # 全探索する割り当ての数の上限（超えたら行ごとに最も判定のよい出現）


def _assign_occurrences(ctx):
    """同じケーブル番号が一覧表に複数行あるとき、行と展開接続図の出現の割り当てを、ケーブル番号ごとに決める。

    判定の悪さ（`_STATUS_RANK`）の合計が最小で、同じ出現を複数の行が使う数がなるべく少ない割り当て（全探索）。出現が行より少なければ重複してよい。
    評価順に早い者勝ちで取ると、あとの行がより合う出現を取れないため。"""
    for cable, rows in ctx.cable_rows.items():
        if len(rows) < 2:
            continue
        options = []
        for row in rows:
            cands = [(occ, _STATUS_RANK[_orientation(row, occ)['status']]) for _d, _s, occ in _occurrences_of(ctx, row)]
            if not cands:
                options = None
                break
            options.append(cands)
        if not options:
            continue
        total = 1
        for cands in options:
            total *= len(cands)
        if total > _ASSIGN_LIMIT:
            continue
        best = None
        for combo in itertools.product(*options):
            cost = sum(c for _o, c in combo)
            dup = len(combo) - len({id(o) for o, _c in combo})
            if best is None or (cost, dup) < best[0]:
                best = ((cost, dup), combo)
        for row, (occ, _c) in zip(rows, best[1]):
            ctx.assignment[id(row)] = occ


class _Ctx:
    def __init__(self, drawings):
        self.drawings = drawings
        self.by_name_393 = _index(drawings, SET_393)
        self.by_name_405 = _index(drawings, SET_405)
        self.schematics_read = any(d.get('schematic') is not None for d in drawings)
        self.unit_keys = _all_unit_keys(drawings)
        self.cable_rows = {}            # ケーブル番号 → 一覧表の行（同じケーブル番号が複数行あるとき、別のケーブルとして行ごとに照合する）
        for d in drawings:
            for lr in d['list_rows']:
                if lr['ケーブル番号']:
                    self.cable_rows.setdefault(lr['ケーブル番号'], []).append(lr)
        self.assignment = {}            # id(一覧表の行) → 割り当てた展開接続図の出現（同じケーブル番号が複数行あるときだけ。`_assign_occurrences`）
        _assign_occurrences(self)


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
            read = [n for n in names if ctx.by_name_405[n].get('schematic') is not None]
            unreadable = [n for n in read if ctx.by_name_405[n]['schematic']['error'] or ctx.by_name_405[n]['schematic']['no_text']]
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


def _conn_score(vals, occ):
    """製作図の接続の左右の値が、出現のどれか1本の芯線にどれだけ当たるか（ピンが当たれば 2、線番のラベルだけなら 1。芯線ごとの合計の最大）。"""
    best = 0
    for w in occ['wires']:
        pins = Counter(_squash(x) for x in ((w['ends'].get(side) or {}).get('pin') or '' for side in '-+') if x)
        labels = {_squash(x) for x in w['labels'][:2] if not _is_color(x)}
        score = 0
        for v, n in Counter(vals).items():
            hit = min(n, pins[v])
            score += 2 * hit + (n - hit if v in labels else 0)
        best = max(best, score)
    return best


def _belongs_to_other_occurrence(ctx, row, pick, conn):
    """製作図の接続が、選んだ出現より、同じケーブル番号の別の行に割り当てた出現に当たるか（当たりが大きいか）。

    1枚の製作図に同じケーブル番号の2つの区間（一覧表の2行）が描かれるとき、別の区間の接続は、この行の PIN・線番の比較から外す。
    当たりは `_conn_score`（ピンの一致＞線番のラベルの一致）。"""
    vals = _conn_vals(conn)
    if not vals or all(v in _DRAIN for v in vals):
        return False
    others = [ctx.assignment[id(x)] for x in ctx.cable_rows.get(row['ケーブル番号'], []) if x is not row and id(x) in ctx.assignment]
    if not others:
        others = [occ for _d, _s, occ in _occurrences_of(ctx, row)]
    own = _conn_score(vals, pick['occ'])
    return any(occ is not pick['occ'] and _conn_score(vals, occ) > own for occ in others)


def _evaluate_row(ctx, list_name, row):
    """一覧表の1行について、全項目のセルと芯線照合の行を作る。Returns: ({項目の列名: [セル]}, 芯線照合の行)"""
    cable = row['ケーブル番号']
    ch = _chain(row, ctx.by_name_393)
    pick = _pick_occurrence(ctx, row)
    fab_conns = [(d, c) for d in ch['fabs'] for c in d['fab_conns'] if c.get('端の数') == 2] if ch['fab_ok'] else []
    siblings = [x for x in ctx.cable_rows.get(cable, []) if x is not row]
    if pick is not None and siblings:
        fab_conns = [fc for fc in fab_conns if not _belongs_to_other_occurrence(ctx, row, pick, fc[1])]
    multi = pick is not None and _is_single_multicore(row, pick['occ'], len(fab_conns))
    sel, _how = _select_wire_rows(row, ch['mine'])
    mine, sibling_sel = ch['mine'], []
    if not sel:
        for x in siblings:
            sibling_sel = _select_wire_rows(x, ch['mine'])[0]
            if sibling_sel:
                # 線番設定表の行は同じケーブル番号の別の行のもの。端末・芯数はこの行では線番設定表と比べない（根拠なし）。
                # 製作図の `*n` は図面（ケーブル番号）単位で共通なので、`*n` の置き換えと `*n` の集合の比較には別の行の行を使う
                mine = []
                break
    cells = {}
    for key, lst in _correspondence_cells(ctx, row, ch, list_name, pick).items():
        cells[item_column('図面の対応', key)] = lst
    for i in (1, 2):
        unit_cell, mod_cell, code_cell = _terminal_cells(ctx, row, i, sel, mine, pick, ch['fabs'], ch['fab_ok'])
        part = _fab_text_cell(f'端末{i} 部品種別', _values(row.get(f'端末{i}_部品種別')), ch['fabs'], ch['fab_ok'])
        for name, c in (('ユニット', unit_cell), ('モジュール', mod_cell), ('機器符号', code_cell), ('部品種別', part)):
            cells[item_column(f'端末{i}', name)] = [c]
    kind_cell, core_cell = _count_cells(row, ch, sel, mine, fab_conns, pick, multi)
    cells[item_column('ケーブル', '種類')] = [kind_cell]
    cells[item_column('ケーブル', 'サイズ')] = [_size_cell(row, ch, pick)]
    cells[item_column('ケーブル', '芯数')] = [core_cell]
    num_cell, pin_cell, color_cell, mat, wr = _wire_cells(ctx, row, ch, sel, mine, pick, multi, fab_conns, sibling_sel)
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
    focus = {'list': list_name, 'row': row['行'], 'fabs': [d['drawing_number'] for d in ch['fabs']] if ch['fab_ok'] else [],
             'wires': [d['drawing_number'] for d in ch['wires']] if ch['wire_ok'] else [],
             'mine': [(d['drawing_number'], r['表'], r['行']) for d, r in ch['mine']],
             'sel': [(x['drawing']['drawing_number'], x['row']['表'], x['row']['行']) for x in sel],
             'schematic': pick['drawing']['drawing_number'] if pick is not None else ''}
    marks = highlights.row_marks(list_name, row, cells, ch, sel, pick, mat, fab_conns)
    shapes = highlights.row_shapes(cells, pick, mat, fab_conns)
    return cells, wire_rows, marks, focus, shapes


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
         'wires': [芯線照合の行],
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
            cells, wire_rows, marks, focus, shapes = _evaluate_row(ctx, list_name, row)
        else:
            cells, wire_rows, marks, focus, shapes = _orphan_cells(cable, sorted(info['wire']), sorted(info['schematic'])), [], [], None, []
        wires += wire_rows
        out = {'ケーブル番号': cable, '一覧表の図番': list_name, '一覧表の行': row_no or '', 'cells': {}, 'links': {},
               'marks': marks, 'focus': focus, 'shapes': shapes}
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

    # 照合に使っていない図面: 種類が無く（393 は表が見つからない・405 は展開接続図として読んでいない）、どのパターンにも当たらない
    # 図中の参照（KIND_REF_IN_SCHEMATIC）で指されただけの図面は、照合には使っていないので「使っていない図面」のまま
    hit = {name for r in pattern_resolutions(drawings) if r['参照元図面種別'] != KIND_REF for name in r['当たった図面'].split('、') if name}
    unused = [d['drawing_number'] for d in sorted(drawings, key=lambda d: d['drawing_number'])
              if not d['kinds'] and d['drawing_number'] not in hit]
    return {'rows': rows, 'details': details, 'wires': wires, 'drawings': drawings, 'unused': unused}


# ---------------------------------------------------------------- 3. 判定の根拠（明細）

def pattern_resolutions(drawings):
    """図面パターンの解決結果。どのパターンが、どの図面に、何件当たったか（照合 1・3・5 の根拠）。

    Returns: [{'図面種別', '参照元図面種別', 'パターン', '参照元の図番', '当たった図面', '件数', '備考'}, ...]
        図面種別: パターンが指す図面の種類（製作図・線番設定表・展開接続図）。
        参照元図面種別: パターンの書かれている場所（一覧表／製作図の注記／線番設定表の注記／図中の参照）。
        一覧表＝ケーブル一覧表の製作図パターン・接続図パターン、図中の参照＝展開接続図の `to … (-405-08)` のような文字（備考に参照元の文字。照合には使わない）。
    """
    by_393, by_405 = _index(drawings, SET_393), _index(drawings, SET_405)
    out, seen = [], set()

    def add(kind, source_kind, pattern, make_regex, source, by_name, remark=''):
        hits = _resolve(make_regex(pattern), by_name)
        key = (kind, source_kind, pattern, source)
        if key in seen:
            return
        seen.add(key)
        note = ' ／ '.join(x for x in (remark, '文字が無い図面' if any(h['no_text'] for h in hits) else '') if x)
        out.append({'図面種別': kind, '参照元図面種別': source_kind, 'パターン': pattern, '参照元の図番': source,
                    '当たった図面': '、'.join(h['drawing_number'] for h in hits), '件数': len(hits), '備考': note})
    for d in sorted(drawings, key=lambda d: d['drawing_number']):
        src = d['drawing_number']
        for r in d['list_rows']:
            for p in _values(r.get('製作図パターン')):
                add('製作図', '一覧表', p, fab_pattern_regex, src, by_393)
            for p in _values(r.get('接続図パターン')):
                add('展開接続図', '一覧表', p, fab_pattern_regex, src, by_405)
        for p in d['fab_notes']:
            add('線番設定表', '製作図の注記', p, wire_pattern_regex, src, by_393)
        for p in d['wire_notes']:
            add('製作図', '線番設定表の注記', p, fab_pattern_regex, src, by_393)
        refs = {}
        for ref in (d.get('schematic') or {}).get('refs', []):
            refs.setdefault(ref['pattern'], []).append(ref['text'])
        for p, texts in refs.items():
            add('展開接続図', KIND_REF, p, wire_pattern_regex, src, by_405, remark=' ／ '.join(texts))
    return out


def fab_drawings_by_cable(drawings):
    """ケーブル番号 → そのケーブルの製作図の図番（一覧表の行の製作図パターンが当たった 393 の図面。0件・複数件もそのまま）。

    製作図にはケーブル番号が書かれていないので、一覧表のパターン経由でだけ分かる。"""
    by_393 = _index(drawings, SET_393)
    out = {}
    for d in drawings:
        for row in d['list_rows']:
            for p in _values(row.get('製作図パターン')):
                for hit in _resolve(fab_pattern_regex(p), by_393):
                    out.setdefault(row['ケーブル番号'], set()).add(hit['drawing_number'])
    return {cable: sorted(names) for cable, names in out.items()}


def drawing_relations(drawings):
    """展開接続図（405。図番順）ごとの、関連する一覧表・線番設定表・製作図の図番。

    Returns: [(展開接続図の図番, [一覧表の図番], [線番設定表の図番], [製作図の図番])]（各リストは昇順。関連が無ければ空）。
    関連は照合と同じチェーン: 一覧表の行の接続図パターンが当たった展開接続図 → その行の製作図パターンが当たった製作図 →
    製作図の注記の線番設定表パターンが当たった線番設定表。"""
    ctx = _Ctx(drawings)
    related = {}
    for d in drawings:
        for row in d['list_rows']:
            names = _schematic_names(ctx, _values(row.get('接続図パターン')))
            if not names:
                continue
            ch = _chain(row, ctx.by_name_393)
            for name in names:
                rel = related.setdefault(name, [set(), set(), set()])
                rel[0].add(d['drawing_number'])
                rel[1].update(w['drawing_number'] for w in ch['wires'] if ch['wire_ok'])
                rel[2].update(f['drawing_number'] for f in ch['fabs'] if ch['fab_ok'])
    return [(name, *(sorted(x) for x in related.get(name, [set(), set(), set()])))
            for name in sorted(d['drawing_number'] for d in drawings if d['set'] == SET_405)]


def fab_text_rows(drawings):
    """照合 2 が探す対象の製作図の文字。製作図のパターンに当たった図面と、線番/PIN の表がある図面。"""
    hit = {name for r in pattern_resolutions(drawings) if r['図面種別'] == '製作図'
           for name in r['当たった図面'].split('、') if name}
    return [(d['drawing_number'], t) for d in sorted(drawings, key=lambda d: d['drawing_number'])
            if d['drawing_number'] in hit or KIND_FAB in d['kinds'] for t in d.get('fab_texts', [])]
