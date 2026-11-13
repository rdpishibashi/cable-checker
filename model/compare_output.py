"""ケーブル比較結果の Excel 出力（streamlit 非依存）。

シート構成: `Summary`（入力ファイル名と件数）→ `All Diff`（ケーブル番号・展開接続図の図番・ケーブル製作図の図番）→
グループごとの `<グループ名> Diff`（同じ3列。ケーブル製作図側はそのグループの図面だけ）。
行の色は他の比較ツールと同じ薄い配色（展開接続図だけ＝ピンク、ケーブル製作図だけ＝青、両方＝緑）。
"""
import io
import re

import pandas as pd

from .cable_compare import BOTH, ONLY_393, ONLY_405, status
from .excel_output import _display_width

SUMMARY_SHEET = 'Summary'
ALL_SHEET = 'All Diff'
GROUP_SHEET_SUFFIX = ' Diff'
SHEET_NAME_MAX = 31
COMPARE_FILENAME = 'cable_comparison.xlsx'

COMPARE_COLUMNS = ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番']
DRAWING_SEPARATOR = '、'

# 行の色（背景, 文字）。Tools/CLAUDE.md「Excel Output Pattern」の明るい配色
STATUS_COLORS = {
    ONLY_405: ('#FEF1F5', '#9C0006'),
    ONLY_393: ('#EAF1FB', '#1F4E79'),
    BOTH: ('#EAF7EC', '#006100'),
}


def group_sheet_names(names):
    """グループ名から `<グループ名> Diff` のシート名を作る。

    Excel の制約に合わせる: 使えない文字 `[]:*?/\\` は `_` に置換、31 文字に収める、
    重複（大小文字無視。`All Diff` との衝突を含む）は `_2`・`_3` を付けて避ける。
    """
    used = {ALL_SHEET.lower()}
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


def summary_frame(rows, name_405, name_393):
    counts = count_rows(rows)
    return pd.DataFrame([
        ['展開接続図のExcel', name_405],
        ['ケーブル製作図のExcel', name_393],
        ['展開接続図のケーブル数', counts[ONLY_405] + counts[BOTH]],
        ['ケーブル製作図のケーブル数', counts[ONLY_393] + counts[BOTH]],
        ['両方にあるケーブル数', counts[BOTH]],
        ['展開接続図だけにあるケーブル数', counts[ONLY_405]],
        ['ケーブル製作図だけにあるケーブル数', counts[ONLY_393]],
    ], columns=['項目', '値'])


def build_compare_workbook(rows, name_405, name_393, groups=()):
    """`compare_cables()` の結果（All Diff）と `compare_groups()` の結果（グループごと）から Excel のバイト列を作る。"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        book = writer.book
        header_fmt = book.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})

        summary = summary_frame(rows, name_405, name_393)
        summary.to_excel(writer, sheet_name=SUMMARY_SHEET, index=False)
        ws = writer.sheets[SUMMARY_SHEET]
        for col, name in enumerate(summary.columns):
            ws.write(0, col, name, header_fmt)
            width = max(_display_width(str(v)) for v in [name, *summary[name]]) + 2
            ws.set_column(col, col, min(width, 60))

        row_fmts = {
            key: book.add_format({'bg_color': bg, 'font_color': fg, 'border': 1})
            for key, (bg, fg) in STATUS_COLORS.items()
        }
        sheets = [(ALL_SHEET, rows)] + list(zip(group_sheet_names([g['name'] for g in groups]),
                                                [g['rows'] for g in groups]))
        for sheet_name, sheet_rows in sheets:
            _write_compare_sheet(writer, sheet_name, sheet_rows, header_fmt, row_fmts)
    return output.getvalue()


def _write_compare_sheet(writer, sheet_name, rows, header_fmt, row_fmts):
    frame = compare_frame(rows)
    frame.to_excel(writer, sheet_name=sheet_name, index=False)
    ws = writer.sheets[sheet_name]
    for col, name in enumerate(frame.columns):
        ws.write(0, col, name, header_fmt)
        width = max(_display_width(str(v)) for v in [name, *frame[name]]) + 2
        ws.set_column(col, col, min(width, 80))
    for i, (r, values) in enumerate(zip(rows, frame.values.tolist()), start=1):
        fmt = row_fmts[status(r)]
        for col, value in enumerate(values):
            ws.write(i, col, value, fmt)
    ws.freeze_panes(1, 0)
