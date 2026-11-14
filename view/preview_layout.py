"""Excelプレビューの表示幅の計算（streamlit 非依存。DXF-extract-labels の「抽出結果プレビュー」と同じ換算）。

- シート選択の selectbox は最長のシート名に合わせた幅にする（既定の全幅だと長すぎてメニューと認識されない）。
- 表の各列はデータの長さに合わせる（Excel出力と同じ表示幅の数え方。全角=2）。
"""
from model.excel_output import display_width

PX_PER_CHAR = 8    # 表の列幅 px = 表示幅（全角=2）×8 + 余白
PX_PADDING = 28
MAX_COLUMN_CHARS = 60
MIN_SELECT_WIDTH_PX = 160


def chars_to_px(chars):
    return int(chars * PX_PER_CHAR + PX_PADDING)


def sheet_select_width(names):
    """シート選択 selectbox の幅 px（最長のシート名に合わせる。下限 160px）。"""
    return max(MIN_SELECT_WIDTH_PX, chars_to_px(max((display_width(n) for n in names), default=0)) + 36)


def column_widths(df):
    """プレビュー表の列幅 `({列名: px}, 表の幅px)`。各列はヘッダーと値の最長に合わせる（最大 60 文字分）。"""
    widths = {}
    for col in df.columns:
        chars = max([display_width(str(col))] + [display_width(str(v)) for v in df[col]])
        widths[col] = chars_to_px(min(chars, MAX_COLUMN_CHARS))
    return widths, sum(widths.values())
