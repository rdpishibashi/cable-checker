"""展開接続図(405)とケーブル図(393)から抽出したケーブル番号の比較（モデル層。streamlit 非依存）。

入力は `extract_cables()` の結果のリスト（図番・タイトル・サブタイトル・ケーブル）。ケーブル番号ごとに両方の図番を並べる（`compare_cables`）。
結果は Summary の件数と画面の要約に使う（シートは `consistency_output` の「ケーブル記載図面」）。
"""
ONLY_405, ONLY_393, BOTH = '405only', '393only', 'both'


def cable_map(results):
    """抽出結果のリスト（`extract_cables()` の戻り値）を {ケーブル番号: [図番…]}（図番は昇順・重複なし）にする。ケーブルが0件の図面は現れない。"""
    mapping = {}
    for r in results:
        for c in r['cables']:
            mapping.setdefault(c['cable'], set()).add(r['drawing_number'])
    return {cable: sorted(drawings) for cable, drawings in mapping.items()}


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


def count_rows(rows):
    """区分ごとの件数 {'405only': n, '393only': n, 'both': n}。"""
    counts = {ONLY_405: 0, ONLY_393: 0, BOTH: 0}
    for r in rows:
        counts[status(r)] += 1
    return counts


def compare_extracts(results_405, results_393):
    """405 と 393 の抽出結果を比較する。Returns: {'rows': 全体の突き合わせ（`compare_cables`）}"""
    return {'rows': compare_cables(cable_map(results_405), cable_map(results_393))}
