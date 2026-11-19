"""整合性チェックの Excel 出力とプレビュー（streamlit 非依存）。

シート構成: `Summary`（項目ごとの記号の件数と、図面の種類ごとの件数）→ `整合性チェック`（1ケーブル1行＝一覧表の行。項目のセルは
記号で、照合明細の該当行へのリンク）→ `照合明細`（セルごとの根拠）→ `芯線の照合`（線番接続IDごとの線番・PIN・色）→
`ケーブル一覧表`・`線番設定表`・`製作図の接続`・`展開接続図`（読み取った明細）→ `パターンの解決`・`製作図の文字`（根拠）。
画面の「分析結果プレビュー」と Excel は同じ `preview_sheets()` から作る（別実装にしない）。
プレビューの `整合性チェック` は見出し1段（`グループ｜項目`）、Excel は2段見出し（グループを結合）。
記号の色は他の比較ツールと同じ薄い配色（○＝薄い緑、△＝薄い黄、×＝薄いピンク、？＝薄い青、－＝色なし）。
"""
import io

import pandas as pd

from . import cable_list_table
from .consistency import (ITEM_COLUMNS, ITEMS, KIND_FAB, KIND_LIST, KIND_SCHEMATIC, KIND_WIRE, NA, NG, OK, SEP, SYMBOLS,
                          UNREAD, WEAK, _codes, _values, fab_text_rows, pattern_resolutions)
from .excel_output import display_width
from .wire_number_table import split_unit_terminal

EXCEL_FILENAME = 'cable_consistency.xlsx'

SUMMARY_SHEET = 'Summary'
CHECK_SHEET = '整合性チェック'
DETAIL_SHEET = '照合明細'
WIRE_CHECK_SHEET = '芯線の照合'
LIST_SHEET = 'ケーブル一覧表'
WIRE_SHEET = '線番設定表'
CONN_SHEET = '製作図の接続'
SCHEMATIC_SHEET = '展開接続図'
PATTERN_SHEET = 'パターンの解決'
TEXT_SHEET = '製作図の文字'
SHEET_NAMES = [SUMMARY_SHEET, CHECK_SHEET, DETAIL_SHEET, WIRE_CHECK_SHEET, LIST_SHEET, WIRE_SHEET, CONN_SHEET,
               SCHEMATIC_SHEET, PATTERN_SHEET, TEXT_SHEET]

SUMMARY_COLUMNS = ['項目', *SYMBOLS, '件数']
CHECK_KEYS = ['ケーブル番号', '一覧表の図番', '一覧表の行', '不整合数', '要確認数']
CHECK_COLUMNS = [*CHECK_KEYS, *ITEM_COLUMNS]
DETAIL_COLUMNS = ['ケーブル番号', '一覧表の図番', '一覧表の行', '項目', '判定', '一覧表の値', '線番設定表の値', '製作図の値',
                  '展開接続図の値', '判断根拠', '参照した図面']
WIRE_CHECK_COLUMNS = ['ケーブル番号', '一覧表の図番', '一覧表の行', '線番接続ID', '線番設定表の線番', '製作図の端1 PIN', '製作図の端2 PIN',
                      '製作図の線番', '製作図の色', '展開接続図の端- PIN', '展開接続図の端+ PIN', '展開接続図のラベル',
                      '判定（PIN）', '判定（線番）', '判定（色）', '製作図', '線番設定表', '展開接続図']
LIST_COLUMNS = ['図番', '行', *cable_list_table.FIELDS, '削除された値']
CONN_COLUMNS = ['図番', '端の数',
                *[f'端{k}_{x}' for k in (1, 2) for x in ('表', '位置', '行', '線番', 'PIN No.')],
                'ほかの端', 'ラベル', '削除されたラベル', '注記の線番設定表パターン']
_END_FIELDS = ('種類', '名前', 'ピン', '領域', '機器')
SCHEMATIC_COLUMNS = ['図番', 'ケーブル番号', '出現', '描き方', '備考', '芯線', 'ラベル',
                     *[f'端{s}_{x}' for s in '-+' for x in _END_FIELDS]]

# 記号の色（背景, 文字）。Tools/CLAUDE.md「Excel Output Pattern」の明るい配色。－ は色なし
SYMBOL_COLORS = {OK: ('#EAF7EC', '#006100'), WEAK: ('#FFF6D5', '#9C6500'), NG: ('#FEF1F5', '#9C0006'),
                 UNREAD: ('#EAF1FB', '#1F4E79')}

# 内部リンクは1シートに 65,530 件まで。超えるときはリンクを付けず記号だけを書く
_MAX_LINKS = 60000


def _cell_text(value):
    """列幅の計算用。欠損（空欄）は空文字（`str(pd.NA)` の `<NA>` を数えない）。"""
    return '' if pd.isna(value) else str(value)


def _int_or_blank(values):
    """数値と空文字が混ざる列は Arrow（Streamlit の表）に変換できないので、欠損を許す整数型にそろえる（Excel では空欄）。"""
    return pd.Series([None if v in ('', None) else int(v) for v in values], dtype='Int64')


# ---------------------------------------------------------------- シートの DataFrame

def summary_frame(result):
    rows = []
    for (group, item), col in zip(ITEMS, ITEM_COLUMNS):
        counts = {s: 0 for s in SYMBOLS}
        for r in result['rows']:
            counts[r['cells'][col]] += 1
        rows.append([col, *[counts[s] for s in SYMBOLS], sum(counts.values())])
    drawings = result['drawings']
    kinds = [
        (f'図面: {KIND_LIST}', sum(KIND_LIST in d['kinds'] for d in drawings)),
        (f'図面: {KIND_WIRE}', sum(KIND_WIRE in d['kinds'] for d in drawings)),
        (f'図面: {KIND_FAB}（線番/PIN の表がある）', sum(KIND_FAB in d['kinds'] for d in drawings)),
        (f'図面: {KIND_SCHEMATIC}', sum(KIND_SCHEMATIC in d['kinds'] for d in drawings)),
        ('図面: 文字の無い図面', sum(d['no_text'] for d in drawings)),
        ('図面: 照合に使っていない図面', len(result['unused'])),
        ('図面: 全体', len(drawings)),
    ]
    rows += [[name, *[None] * len(SYMBOLS), n] for name, n in kinds]
    # 図面の種類の行は記号の列が空欄（None）。数値と空文字が混ざる object 列にすると Arrow に変換できないので整数型にそろえる
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS).astype({c: 'Int64' for c in SUMMARY_COLUMNS[1:]})


def check_frame(result):
    rows = [[r[k] for k in CHECK_KEYS] + [r['cells'][c] for c in ITEM_COLUMNS] for r in result['rows']]
    frame = pd.DataFrame(rows, columns=CHECK_COLUMNS)
    frame['一覧表の行'] = _int_or_blank(frame['一覧表の行'])     # 一覧表に無いケーブルの行は空
    return frame


def detail_frame(result):
    frame = pd.DataFrame([[d[c] for c in DETAIL_COLUMNS] for d in result['details']], columns=DETAIL_COLUMNS)
    frame['一覧表の行'] = _int_or_blank(frame['一覧表の行'])
    return frame


def wire_check_frame(result):
    return pd.DataFrame([[w[c] for c in WIRE_CHECK_COLUMNS] for w in result['wires']], columns=WIRE_CHECK_COLUMNS)


def list_frame(result):
    rows = [[d['drawing_number'], *[r[k] for k in LIST_COLUMNS[1:]]]
            for d in result['drawings'] for r in d['list_rows']]
    return pd.DataFrame(rows, columns=LIST_COLUMNS)


def _list_ends(drawings):
    """ケーブル番号 → 一覧表の端（ユニット名のリスト・機器符号の集合）。接続先をユニット名＋端子台に分けるのに使う。"""
    units, codes = {}, {}
    for d in drawings:
        for r in d['list_rows']:
            for i in (1, 2):
                units.setdefault(r['ケーブル番号'], []).extend(_values(r.get(f'端末{i}_ユニット')))
                codes.setdefault(r['ケーブル番号'], set()).update(_codes(r.get(f'端末{i}_機器符号')))
    return units, codes


def wire_frame(result):
    """線番設定表の読み取り結果。接続先は、一覧表のユニット名・機器符号を手がかりにユニット名と端子台に分けて並べる（判定は照合明細）。"""
    drawings = result['drawings']
    units, codes = _list_ends(drawings)
    n_max = max([int(k[2:]) for d in drawings for r in d['wire_rows'] for k in r if k.startswith('線番')] or [0])
    wire_cols = [f'線番{n}' for n in range(1, n_max + 1)]
    columns = (['図番', '表', '行', 'ケーブル番号'] + wire_cols + ['接続先1', '接続先2']
               + [f'{k}_{x}' for k in ('接続先1', '接続先2') for x in ('ユニット', '端子台')]
               + ['削除された値', '注記の製作図パターン'])
    rows = []
    for d in drawings:
        for r in d['wire_rows']:
            cabs = _values(r.get('ケーブル番号'))
            lu = [u for c in cabs for u in units.get(c, [])]
            lc = set().union(*(codes.get(c, set()) for c in cabs)) if cabs else set()
            row = {'図番': d['drawing_number'], '表': r['表'], '行': r['行'],
                   '削除された値': r['削除された値'], '注記の製作図パターン': SEP.join(d['wire_notes'])}
            row.update({k: v for k, v in r.items() if k == 'ケーブル番号' or k.startswith(('線番', '接続先'))})
            for k in ('接続先1', '接続先2'):
                unit, term = split_unit_terminal(r['_lines'].get(k, []), lu, lc)
                row[f'{k}_ユニット'], row[f'{k}_端子台'] = unit, term
            rows.append([row.get(c, '') for c in columns])
    return pd.DataFrame(rows, columns=columns)


def conn_frame(result):
    rows = []
    for d in result['drawings']:
        for c in d['fab_conns']:
            rows.append([d['drawing_number'] if k == '図番' else
                         SEP.join(d['fab_notes']) if k == '注記の線番設定表パターン' else c.get(k, '')
                         for k in CONN_COLUMNS])
    return pd.DataFrame(rows, columns=CONN_COLUMNS)


def schematic_frame(result):
    """展開接続図の読み取り結果。ケーブル番号の出現ごと・芯線ごとに、両端（`-` と `+`）の端末と、ユニットの根拠を並べる。"""
    rows = []
    for d in result['drawings']:
        s = d.get('schematic')
        if not s:
            continue
        for n, occ in enumerate(s['occurrences'], 1):
            for k, w in enumerate(occ['wires'], 1):
                row = [d['drawing_number'], occ['cable'], n, occ['how'], occ['remark'].replace('\n', ' '), k, SEP.join(w['labels'])]
                for side in '-+':
                    e = w['ends'].get(side)
                    row += ['', '', '', '', ''] if not e else [
                        e['kind'] or '', e['name'] or '', e['pin'] or '', SEP.join(e['regions'][:5]), SEP.join(e['devices'][:5])]
                rows.append(row)
    return pd.DataFrame(rows, columns=SCHEMATIC_COLUMNS)


def pattern_frame(result):
    columns = ['種別', 'パターン', '参照元の図番', '当たった図面', '件数', '備考']
    return pd.DataFrame([[r[c] for c in columns] for r in pattern_resolutions(result['drawings'])], columns=columns)


def text_frame(result):
    return pd.DataFrame(fab_text_rows(result['drawings']), columns=['図番', '文字'])


def preview_sheets(result):
    """出力する Excel の全シートを、Excel と同じ順で `[(シート名, DataFrame), ...]` にする。"""
    frames = {SUMMARY_SHEET: summary_frame, CHECK_SHEET: check_frame, DETAIL_SHEET: detail_frame,
              WIRE_CHECK_SHEET: wire_check_frame, LIST_SHEET: list_frame, WIRE_SHEET: wire_frame, CONN_SHEET: conn_frame,
              SCHEMATIC_SHEET: schematic_frame, PATTERN_SHEET: pattern_frame, TEXT_SHEET: text_frame}
    return [(name, frames[name](result)) for name in SHEET_NAMES]


# ---------------------------------------------------------------- Excel

def build_workbook(result):
    """`check_consistency()` の結果から Excel のバイト列を作る（シートは `preview_sheets()`）。"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        book = writer.book
        header_fmt = book.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
        sym_fmts = {s: book.add_format({'bg_color': bg, 'font_color': fg, 'align': 'center'}) for s, (bg, fg) in SYMBOL_COLORS.items()}
        sym_fmts[NA] = book.add_format({'align': 'center'})
        row_fmts = {s: book.add_format({'bg_color': bg, 'font_color': fg}) for s, (bg, fg) in SYMBOL_COLORS.items()}
        for name, frame in preview_sheets(result):
            if name == CHECK_SHEET:
                _write_check_sheet(writer, result, frame, header_fmt, sym_fmts)
                continue
            frame.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            for col, column in enumerate(frame.columns):
                ws.write(0, col, column, header_fmt)
                width = max([display_width(str(column))] + [display_width(_cell_text(v)) for v in frame[column]]) + 2
                ws.set_column(col, col, min(width, 60))
            if name == DETAIL_SHEET:
                judge_col = list(frame.columns).index('判定')
                for i, values in enumerate(frame.values.tolist(), start=1):
                    fmt = row_fmts.get(values[judge_col])
                    for col, value in enumerate(values):
                        ws.write(i, col, None if pd.isna(value) else value, fmt)   # 欠損は空欄（書式だけ付ける）
            elif name == WIRE_CHECK_SHEET:
                cols = [list(frame.columns).index(c) for c in ('判定（PIN）', '判定（線番）', '判定（色）')]
                for i, values in enumerate(frame.values.tolist(), start=1):
                    for col in cols:
                        ws.write(i, col, values[col], sym_fmts.get(values[col]))
            ws.freeze_panes(1, 1)
            if len(frame):
                ws.autofilter(0, 0, len(frame), len(frame.columns) - 1)
    return output.getvalue()


def _write_check_sheet(writer, result, frame, header_fmt, sym_fmts):
    """整合性チェック: 2段見出し（1段目＝グループを結合、2段目＝項目）。項目のセルは記号で、照合明細の該当行へのリンク。"""
    name = CHECK_SHEET
    frame.to_excel(writer, sheet_name=name, index=False, header=False, startrow=2)
    ws = writer.sheets[name]
    n_keys = len(CHECK_KEYS)
    for col, key in enumerate(CHECK_KEYS):
        ws.merge_range(0, col, 1, col, key, header_fmt)
        width = max([display_width(key)] + [display_width(_cell_text(v)) for v in frame[key]]) + 2
        ws.set_column(col, col, min(width, 24))
    first = {}
    for k, (group, item) in enumerate(ITEMS):
        col = n_keys + k
        first.setdefault(group, [col, col])[1] = col
        ws.write(1, col, item, header_fmt)
        ws.set_column(col, col, max(display_width(item) + 2, 8))
    for group, (c0, c1) in first.items():
        if c0 == c1:
            ws.write(0, c0, group, header_fmt)
        else:
            ws.merge_range(0, c0, 0, c1, group, header_fmt)
    links = len(result['rows']) * len(ITEM_COLUMNS) <= _MAX_LINKS
    for i, r in enumerate(result['rows']):
        for k, col_name in enumerate(ITEM_COLUMNS):
            symbol, col, row = r['cells'][col_name], n_keys + k, 2 + i
            fmt = sym_fmts[symbol]
            if links:
                ws.write_url(row, col, f"internal:'{DETAIL_SHEET}'!A{r['links'][col_name] + 2}", fmt, string=symbol)
            else:
                ws.write(row, col, symbol, fmt)
    ws.freeze_panes(2, 3)
    if len(frame):
        ws.autofilter(1, 0, 1 + len(frame), len(frame.columns) - 1)
