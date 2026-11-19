"""ケーブル番号の比較結果の表とシートの書き込み（streamlit 非依存）。整合性チェックの Excel（`consistency_output`）が使う。

シート: `All Diff`（ケーブル番号・展開接続図の図番・ケーブル製作図の図番）→ グループごとの `<グループ名> Diff`（同じ3列。
ケーブル製作図側はそのグループの図面だけ）。行の色は他の比較ツールと同じ薄い配色（展開接続図だけ＝ピンク、ケーブル製作図だけ＝青、両方＝緑）。
"""
import re

import pandas as pd

from .cable_compare import BOTH, ONLY_393, ONLY_405, status
from .excel_output import display_width

ALL_SHEET = 'All Diff'
GROUP_SHEET_SUFFIX = ' Diff'
SHEET_NAME_MAX = 31

COMPARE_COLUMNS = ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番']
DRAWING_SEPARATOR = '、'

# 行の色（背景, 文字）。Tools/CLAUDE.md「Excel Output Pattern」の明るい配色
STATUS_COLORS = {
    ONLY_405: ('#FEF1F5', '#9C0006'),
    ONLY_393: ('#EAF1FB', '#1F4E79'),
    BOTH: ('#EAF7EC', '#006100'),
}


def group_sheet_names(names, reserved=()):
    """グループ名から `<グループ名> Diff` のシート名を作る。

    Excel の制約に合わせる: 使えない文字 `[]:*?/\\` は `_` に置換、31 文字に収める、
    重複（大小文字無視。`All Diff` と、同じブックの別のシート名 `reserved` との衝突を含む）は `_2`・`_3` を付けて避ける。
    """
    used = {ALL_SHEET.lower(), *(r.lower() for r in reserved)}
    result = []
    for name in names:
        base = re.sub(r'[\[\]:*?/\\]', '_', name).strip("' ") or '_'
        base = base[:SHEET_NAME_MAX - len(GROUP_SHEET_SUFFIX)]
        candidate, n = base, 1
        while (candidate + GROUP_SHEET_SUFFIX).lower() in used:
            n += 1
            tag = f'_{n}'
            candidate = base[:SHEET_NAME_MAX - len(GROUP_SHEET_SUFFIX) - len(tag)] + tag
        sheet = candidate + GROUP_SHEET_SUFFIX
        used.add(sheet.lower())
        result.append(sheet)
    return result


def compare_frame(rows):
    return pd.DataFrame(
        [[r['cable'], DRAWING_SEPARATOR.join(r['drawings_405']),
          DRAWING_SEPARATOR.join(r['drawings_393'])] for r in rows],
        columns=COMPARE_COLUMNS,
    )


def count_rows(rows):
    """区分ごとの件数 {'405only': n, '393only': n, 'both': n}。"""
    counts = {ONLY_405: 0, ONLY_393: 0, BOTH: 0}
    for r in rows:
        counts[status(r)] += 1
    return counts


def write_compare_sheet(writer, sheet_name, rows, header_fmt, row_fmts):
    """比較のシート（`compare_frame(rows)`）を書く。行は区分（`status`）で色を付ける。`row_fmts` は `status_formats(book)` の戻り値。"""
    frame = compare_frame(rows)
    frame.to_excel(writer, sheet_name=sheet_name, index=False)
    ws = writer.sheets[sheet_name]
    for col, name in enumerate(frame.columns):
        ws.write(0, col, name, header_fmt)
        width = max(display_width(str(v)) for v in [name, *frame[name]]) + 2
        ws.set_column(col, col, min(width, 80))
    for i, (r, values) in enumerate(zip(rows, frame.values.tolist()), start=1):
        fmt = row_fmts[status(r)]
        for col, value in enumerate(values):
            ws.write(i, col, value, fmt)
    ws.freeze_panes(1, 0)


def status_formats(book):
    """区分（`STATUS_COLORS`）ごとの行の書式 {区分: xlsxwriter の書式}。"""
    return {key: book.add_format({'bg_color': bg, 'font_color': fg, 'border': 1}) for key, (bg, fg) in STATUS_COLORS.items()}
