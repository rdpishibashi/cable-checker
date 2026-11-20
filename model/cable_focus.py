"""ケーブルを選んだときの viewer の強調（モデル層。streamlit 非依存）。

展開接続図（405）の viewer でケーブル番号を1つ選ぶと、そのケーブルに関連する図面（ケーブル一覧表・線番設定表・
ケーブル製作図・照合に使った展開接続図）を並べ、関連する文字を色分けする。
 - 照合の根拠になった文字: 照合の記号の色（`consistency.check_consistency()` の行の `marks`）
 - 関連するが照合に使っていない文字: 「関連」（紫。一覧表の行の他の列、線番設定表の選ばなかった行、展開接続図の使わなかった出現など）
同じ文字に複数の記号が付いたら重いほう（× ＞ ？ ＞ △ ＞ ○ ＞ － ＞ 関連。`cable_preview.apply_marks` が決める）。

また、展開接続図の文字から図中の参照 `(-xxx-xx…)` を拾い、アップロードした図面との対応を返す（`drawing_refs`）。
"""
from typing import Dict, List, Optional

from . import cable_preview
from .cable_preview import RELATED
from .consistency import SET_405, SET_393, wire_pattern_regex
from .schematic_reader import find_references

ROLE_SCHEMATIC = '展開接続図'
ROLE_LIST = 'ケーブル一覧表'
ROLE_WIRE = '線番設定表'
ROLE_FAB = 'ケーブル製作図'
NO_CABLE = '（選択しない）'


def cables_in(result, drawing_number: str) -> List[str]:
    """展開接続図 drawing_number に出るケーブル番号（重複なし・昇順）。抽出結果（`extracts`）から。"""
    for r in result.get('extracts', {}).get(SET_405, []):
        if r['drawing_number'] == drawing_number:
            return sorted({c['cable'] for c in r['cables']})
    return []


def _mark(drawing, refs, symbol, item):
    return [{'drawing': drawing, 'anchor': ref, 'text': text, 'symbol': symbol, 'item': item}
            for text, ref in refs or [] if ref is not None]


def _all_refs(refs_by_col):
    return [pair for pairs in (refs_by_col or {}).values() for pair in pairs]


def _end_refs(e):
    refs = (e or {}).get('_refs') or {}
    out = []
    if e and e.get('name') and refs.get('name') is not None:
        out.append((e['name'], refs['name']))
    if e and e.get('pin') and refs.get('pin') is not None:
        out.append((e['pin'], refs['pin']))
    for key in ('regions', 'devices', 'nearby'):
        out += list((refs.get(key) or {}).items())
    return out


def _occurrence_refs(occ):
    """ケーブル記号のラベル・芯線のラベル・両端の名前・ピン・ユニットの根拠の文字（text, anchor）。"""
    out = []
    if occ.get('_label_ref') is not None:
        out.append((occ['cable'], occ['_label_ref']))
    for w in occ['wires']:
        out += list(zip(w['labels'], w.get('_label_refs', [])))
        for e in w['ends'].values():
            out += _end_refs(e)
    return out


def _wire_row(d, table, row):
    return next((r for r in d['wire_rows'] if r['表'] == table and r['行'] == row), None)


def focus(result, cable: str, drawing_number: str) -> List[dict]:
    """選んだ展開接続図 drawing_number のケーブル cable に関連する図面とその強調。

    Returns: 表示する順の [{'role': 役割, 'drawing': 図番, 'marks': [マーク], 'counts': {記号: 文字数}}]。
    先頭は選んだ展開接続図。続いて 一覧表 → 線番設定表 → 製作図 → 照合に使った別の展開接続図（図番順）。
    強調する文字が1件も無い図面は含めない（選んだ展開接続図だけは常に含める）。"""
    by_name = {d['drawing_number']: d for d in result['drawings']}
    rows = [r for r in result['rows'] if r['ケーブル番号'] == cable]
    marks: Dict[tuple, list] = {}
    order = {ROLE_SCHEMATIC: [], ROLE_LIST: [], ROLE_WIRE: [], ROLE_FAB: []}

    def add(role, name, items):
        if name not in order[role]:
            order[role].append(name)
        marks.setdefault((role, name), []).extend(items)

    add(ROLE_SCHEMATIC, drawing_number, [])
    picked = set()
    for r in rows:
        f = r.get('focus')
        for m in r.get('marks', []):
            role = _role_of(by_name.get(m['drawing']), f, m['drawing'])
            if role:
                add(role, m['drawing'], [m])
        if not f:
            continue
        # 照合した行の関連する文字（紫）: 一覧表の行の全文字・線番設定表のこのケーブルの行の全文字
        ld = by_name.get(f['list'])
        lrow = next((x for x in ld['list_rows'] if x['行'] == f['row']), None) if ld else None
        if lrow:
            add(ROLE_LIST, f['list'], _mark(f['list'], _all_refs(lrow.get('_refs')), RELATED, '関連'))
        for dn, table, rno in f['mine']:
            wr = _wire_row(by_name[dn], table, rno)
            if wr:
                add(ROLE_WIRE, dn, _mark(dn, _all_refs(wr.get('_refs')), RELATED, '関連'))
        for dn in f['fabs']:
            add(ROLE_FAB, dn, [])
        if f['schematic']:
            picked.add(f['schematic'])
    # 展開接続図: 選んだ図面と、照合に使った図面の、このケーブルの出現すべて（照合に使った出現以外は紫）
    for dn in [drawing_number] + sorted(picked - {drawing_number}):
        s = (by_name.get(dn) or {}).get('schematic')
        if not s:
            continue
        for occ in s['occurrences']:
            if occ['cable'] == cable:
                add(ROLE_SCHEMATIC, dn, _mark(dn, _occurrence_refs(occ), RELATED, '関連'))
    # 一覧表に無いケーブル: 線番設定表の行（ケーブル番号の文字を×、他は関連）・展開接続図の出現（ラベルを×）
    if any(not r['一覧表の図番'] for r in rows):
        for d in result['drawings']:
            for wr in d['wire_rows']:
                if cable in [c.strip() for c in str(wr.get('ケーブル番号', '')).split('|')]:
                    refs = wr.get('_refs', {})
                    add(ROLE_WIRE, d['drawing_number'], _mark(d['drawing_number'], _all_refs(refs), RELATED, '関連'))
                    add(ROLE_WIRE, d['drawing_number'], _mark(d['drawing_number'], refs.get('ケーブル番号'), '×', '図面の対応｜一覧表'))
            s = d.get('schematic')
            if s and d['drawing_number'] in (drawing_number, *picked):
                for occ in s['occurrences']:
                    if occ['cable'] == cable:
                        add(ROLE_SCHEMATIC, d['drawing_number'],
                            _mark(d['drawing_number'], [(cable, occ.get('_label_ref'))], '×', '図面の対応｜一覧表'))

    sections = []
    for role in (ROLE_SCHEMATIC, ROLE_LIST, ROLE_WIRE, ROLE_FAB):
        for name in sorted(order[role]) if role != ROLE_SCHEMATIC else [drawing_number] + sorted(set(order[role]) - {drawing_number}):
            items = marks.get((role, name), [])
            # 「関連」（照合の範囲）だけで判定の記号が無い図面は、viewer に出しても色が付かないので並べない
            if not any(m['symbol'] != RELATED for m in items) and not (role == ROLE_SCHEMATIC and name == drawing_number):
                continue
            sections.append({'role': role, 'drawing': name, 'marks': items, 'counts': symbol_counts(items)})
    return sections


def _role_of(drawing: Optional[dict], f: Optional[dict], name: str) -> Optional[str]:
    """マークの図面の、この行での役割。"""
    if drawing is None or f is None:
        return None
    if name == f['list']:
        return ROLE_LIST
    if name in f['wires']:
        return ROLE_WIRE
    if name in f['fabs']:
        return ROLE_FAB
    if name == f['schematic']:
        return ROLE_SCHEMATIC
    return None


def symbol_counts(marks) -> Dict[str, int]:
    """マークの記号ごとの文字数（同じ基準点は重いほう1つ。見出しの件数に使う）。"""
    best: Dict[tuple, str] = {}
    for m in marks:
        key = tuple(m['anchor'])
        cur = best.get(key)
        if cur is None or cable_preview._SYMBOL_RANK[m['symbol']] < cable_preview._SYMBOL_RANK[cur]:
            best[key] = m['symbol']
    counts: Dict[str, int] = {}
    for sym in best.values():
        counts[sym] = counts.get(sym, 0) + 1
    return counts


def counts_label(counts: Dict[str, int]) -> str:
    """開閉枠の見出しに付ける件数（例: `×1 ？0 △1 ○9`。0 の記号も出す。関連・対象外は出さない）。"""
    return ' '.join(f'{sym}{counts.get(sym, 0)}' for sym in ('×', '？', '△', '○'))


def drawing_refs(texts, uploaded_names) -> List[dict]:
    """展開接続図の文字から図中の参照 `(-xxx-xx…)` を拾い、アップロードした図面（405・393 の両方）との対応を返す。

    Args:
        texts: その図面の文字（正規化した文字列）のリスト。
        uploaded_names: アップロードした図番（拡張子なし）。
    Returns: 図中の出現順の [{'pattern': '-405-08', 'texts': [図中の文字, ...], 'matches': [一致した図番（昇順）]}]（同じパターンは1件）。
    一致の規則は線番設定表の注記パターンと同じ（`wire_pattern_regex`: 頭の英字2字・数字4桁と末尾の改訂記号は任意）。"""
    out: Dict[str, dict] = {}
    for ref in find_references(list(texts)):
        item = out.setdefault(ref['pattern'], {'pattern': ref['pattern'], 'texts': [], 'matches': []})
        if ref['text'] not in item['texts']:
            item['texts'].append(ref['text'])
    for item in out.values():
        rx = wire_pattern_regex(item['pattern'])
        item['matches'] = sorted(n for n in set(uploaded_names) if rx.match(n.upper()))
    return list(out.values())
