"""展開接続図(405)とケーブル製作図(393)から抽出したケーブル番号の比較（モデル層。streamlit 非依存）。

入力は `extract_cables()` の結果のリスト（図番・タイトル・サブタイトル・ケーブル）。ケーブル番号ごとに両方の図番を並べ（`compare_cables`）、
393 の図面をタイトル／サブタイトルでグループ分けしてグループごとにも突き合わせる（`compare_groups`）。整合性チェックの Excel に入る。
"""
import re
import unicodedata
from collections import Counter

ONLY_405, ONLY_393, BOTH = '405only', '393only', 'both'

NO_TITLE_GROUP = '（タイトルなし）'
_DASHES = re.compile('[-\u2010-\u2015\u2212\u30fc\uff70]')  # ハイフン・ダッシュ・長音（ケ－ブル／ケーブルの表記ゆれ）


def cable_map(results):
    """抽出結果のリスト（`extract_cables()` の戻り値）を {ケーブル番号: [図番…]}（図番は昇順・重複なし）にする。ケーブルが0件の図面は現れない。"""
    mapping = {}
    for r in results:
        for c in r['cables']:
            mapping.setdefault(c['cable'], set()).add(r['drawing_number'])
    return {cable: sorted(drawings) for cable, drawings in mapping.items()}


def drawing_titles(results):
    """抽出結果のリストを {図番: (タイトル, サブタイトル)} にする（グループ分け用）。"""
    return {r['drawing_number']: ((r['title'] or '').strip(), (r['subtitle'] or '').strip()) for r in results}


def compare_cables(map_405, map_393):
    """2つの {ケーブル番号: [図番…]} を突き合わせる。

    Returns:
        [{'cable', 'drawings_405', 'drawings_393'}, …] ケーブル番号の昇順（文字コード順）。
        片方にしかないケーブルは、もう一方の図番リストが空。
    """
    return [
        {'cable': cable,
         'drawings_405': sorted(set(map_405.get(cable, ()))),
         'drawings_393': sorted(set(map_393.get(cable, ())))}
        for cable in sorted(set(map_405) | set(map_393))
    ]


def status(row):
    """行の区分: ONLY_405 / ONLY_393 / BOTH。"""
    if row['drawings_405'] and row['drawings_393']:
        return BOTH
    return ONLY_405 if row['drawings_405'] else ONLY_393


def group_key(title, subtitle):
    """グループの識別キー。サブタイトルがあればそれ、無ければタイトル。表記ゆれ（全角半角・長音・空白・大小文字）を吸収する。

    どちらも空なら ''。
    """
    text = subtitle or title
    text = unicodedata.normalize('NFKC', text)
    return _DASHES.sub('ー', re.sub(r'\s+', '', text)).casefold()


def compare_groups(map_405, map_393, titles):
    """ケーブル製作図の図面をタイトル／サブタイトルでグループ分けし、グループごとに 405 全体と突き合わせる。

    Args:
        map_405, map_393: `cable_map()` の結果。
        titles: `drawing_titles()` の結果。無い図面はタイトルなし扱い。
    Returns:
        [{'name': 表示名, 'drawings': [図番…], 'rows': compare_cables() の結果}, …] 表示名の昇順。
        ケーブルのある図面（map_393 に現れる図面）だけが対象。表示名はグループ内で最も多い元の表記（同数なら先の図番）。
    """
    drawings = sorted({d for ds in map_393.values() for d in ds})
    groups = {}  # キー -> {'raw': [表記…], 'drawings': [図番…]}
    for d in drawings:
        title, subtitle = titles.get(d, ('', ''))
        g = groups.setdefault(group_key(title, subtitle), {'raw': [], 'drawings': []})
        g['raw'].append((subtitle or title).strip() or NO_TITLE_GROUP)
        g['drawings'].append(d)
    result = []
    for g in groups.values():
        in_group = set(g['drawings'])
        counts = Counter(g['raw'])
        name = max(g['raw'], key=lambda r: counts[r])  # 同数なら先に出たもの
        sub_393 = {c: [d for d in ds if d in in_group] for c, ds in map_393.items()}
        sub_393 = {c: ds for c, ds in sub_393.items() if ds}
        result.append({'name': name, 'drawings': g['drawings'],
                       'rows': compare_cables(map_405, sub_393)})
    return sorted(result, key=lambda r: r['name'])


def compare_extracts(results_405, results_393):
    """405 と 393 の抽出結果を比較する。

    Returns:
        {'rows': 全体の突き合わせ（`compare_cables`）, 'groups': 393 の図面のグループごとの突き合わせ（`compare_groups`）}
    """
    map_405, map_393 = cable_map(results_405), cable_map(results_393)
    return {'rows': compare_cables(map_405, map_393),
            'groups': compare_groups(map_405, map_393, drawing_titles(results_393))}
