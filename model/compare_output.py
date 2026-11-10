"""ケーブル比較結果の Excel 出力（streamlit 非依存）。

シート構成: `Summary`（入力ファイル名と件数）→ `ケーブル比較`（ケーブル番号・展開接続図の図番・ケーブル製作図の図番）。
行の色は他の比較ツールと同じ薄い配色（展開接続図だけ＝ピンク、ケーブル製作図だけ＝青、両方＝緑）。
"""
import io

import pandas as pd

from .cable_compare import BOTH, ONLY_393, ONLY_405, status
from .excel_output import _display_width

SUMMARY_SHEET = 'Summary'
COMPARE_SHEET = 'ケーブル比較'
COMPARE_FILENAME = 'cable_comparison.xlsx'

COMPARE_COLUMNS = ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番']
DRAWING_SEPARATOR = '、'

# 行の色（背景, 文字）。Tools/CLAUDE.md「Excel Output Pattern」の明るい配色
STATUS_COLORS = {
    ONLY_405: ('#FEF1F5', '#9C0006'),
    ONLY_393: ('#EAF1FB', '#1F4E79'),
    BOTH: ('#EAF7EC', '#006100'),
}


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


def build_compare_workbook(rows, name_405, name_393):
    """`compare_cables()` の結果から Excel のバイト列を作る。"""
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

        frame = compare_frame(rows)
        frame.to_excel(writer, sheet_name=COMPARE_SHEET, index=False)
        ws = writer.sheets[COMPARE_SHEET]
        row_fmts = {
            key: book.add_format({'bg_color': bg, 'font_color': fg, 'border': 1})
            for key, (bg, fg) in STATUS_COLORS.items()
        }
        for col, name in enumerate(frame.columns):
            ws.write(0, col, name, header_fmt)
            width = max(_display_width(str(v)) for v in [name, *frame[name]]) + 2
            ws.set_column(col, col, min(width, 80))
        for i, (r, values) in enumerate(zip(rows, frame.values.tolist()), start=1):
            fmt = row_fmts[status(r)]
            for col, value in enumerate(values):
                ws.write(i, col, value, fmt)
        ws.freeze_panes(1, 0)
    return output.getvalue()
