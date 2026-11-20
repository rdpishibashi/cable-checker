"""照合の記号（○△×？－）の定義を1か所に集める（モデル層。streamlit 非依存）。

記号・重さ・色（Excel のセル色と viewer の文字色）・凡例の文言は、ここだけで決める。`consistency`（照合）・`consistency_output`（Excel）・
`legend`（記号の説明）・`cable_focus`・`cable_preview`（viewer）は、ここから import する（同じ値を別の場所に書き直さない）。

`RELATED`（関連）は、選んだケーブルに関連するが照合に使っていない文字を示す**内部の区分**で、Excel には無く、viewer にも出さない
（照合の範囲を示すだけ）。どの記号よりも軽い。
"""

# 判定の記号
OK, WEAK, NG, UNREAD, NA = '○', '△', '×', '？', '－'
SYMBOLS = [OK, WEAK, NG, UNREAD, NA]
RELATED = '関連'

# 重さ（大きいほど重い。× ＞ ？ ＞ △ ＞ ○ ＞ － ＞ 関連）
SEVERITY = {RELATED: -1, NA: 0, OK: 1, WEAK: 2, UNREAD: 3, NG: 4}
BY_SEVERITY = sorted(SEVERITY, key=SEVERITY.get, reverse=True)     # 重い順（× ？ △ ○ － 関連）

# Excel のセル色（背景, 文字）。Tools/CLAUDE.md「Excel Output Pattern」の明るい配色。－ は色なし
EXCEL_COLORS = {OK: ('#EAF7EC', '#006100'), WEAK: ('#FFF6D5', '#9C6500'), NG: ('#FEF1F5', '#9C0006'), UNREAD: ('#EAF1FB', '#1F4E79')}
# viewer の文字・図形の色（Excel のセル色に対応: ○緑・△黄・×赤・？青・－紫）
VIEWER_COLORS = {OK: '#33dd66', WEAK: '#ffd400', NG: '#ff3030', UNREAD: '#4da6ff', NA: '#c080ff', RELATED: '#c080ff'}

# 記号の説明シートの色の名前（Excel のセル色・viewer の文字色）
EXCEL_COLOR_NAMES = {OK: '薄い緑', WEAK: '薄い黄', NG: '薄いピンク', UNREAD: '薄い青', NA: '色なし'}
VIEWER_COLOR_NAMES = {OK: '緑', WEAK: '黄', NG: '赤', UNREAD: '青', NA: '紫'}

# viewer の左枠（凡例）の表示順: ○ → △ → × → ？ → －（図形の行も同じ順。重さの順 BY_SEVERITY とは別）
LEGEND_ORDER = [OK, WEAK, NG, UNREAD, NA, RELATED]

# viewer の凡例の文言
LABELS = {OK: '○ 一致', WEAK: '△ 一致（根拠が弱い）', NG: '× 不整合', UNREAD: '？ 読めない', NA: '－ 対象外', RELATED: '関連（照合に使っていない）'}


def worst(symbols, default=NA):
    """記号のうち最も重いもの。空なら default。"""
    return max(symbols, key=SEVERITY.__getitem__, default=default)


def rank(symbol):
    """重い順の位置（小さいほど重い。同じ文字に複数の記号が付いたら小さいほうを使う）。"""
    return BY_SEVERITY.index(symbol)
