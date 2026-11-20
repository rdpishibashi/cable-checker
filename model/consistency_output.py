"""整合性チェックの Excel 出力とプレビュー（streamlit 非依存）。1つの Excel に、整合性チェック・ケーブル番号の抽出と比較をまとめる。

シート構成: `Summary`（項目ごとの記号の件数と、図面・ケーブル番号の件数）→ `記号の説明`（記号・項目の判定条件・根拠の語。`legend.py`）→
`図面一覧`（抽出した図面）→ `図面の関係`（展開接続図ごとの関連する一覧表・線番設定表・製作図）→ `ケーブル抽出`（ケーブル番号）→
`All Diff`・`<グループ名> Diff`（ケーブル番号の比較。展開接続図と製作図の図番を並べる）→ `整合性チェック`（1ケーブル1行＝一覧表の行。項目のセルは
記号で、照合明細の該当行へのリンク）→ `照合明細`（セルごとの根拠）→ `芯線照合`（線番接続IDごとの線番・PIN・色）→
`展開接続図`・`ケーブル一覧表`・`線番設定表`・`製作図の接続表`（読み取った明細）→ `製作図の文字`・`図番パターン照合`（根拠）。
画面の「分析結果プレビュー」と Excel は同じ `preview_sheets()` から作る（別実装にしない）。
プレビューの `整合性チェック` は見出し1段（`グループ｜項目`）、Excel は2段見出し（グループを結合）。
記号の色は他の比較ツールと同じ薄い配色（○＝薄い緑、△＝薄い黄、×＝薄いピンク、？＝薄い青、－＝色なし）。
"""
import io

import pandas as pd

from . import cable_list_table, legend
from .compare_output import (ALL_SHEET, compare_frame, count_rows, group_sheet_names, status_formats,
                             write_compare_sheet)
from .consistency import (ITEM_COLUMNS, ITEMS, KIND_FAB, KIND_LIST, KIND_SCHEMATIC, KIND_WIRE, NA, SEP, SET_393,
                          SET_405, SYMBOLS, drawing_relations, fab_text_rows, pattern_resolutions)
from .cell_values import codes as _codes, values as _values
from .excel_output import display_width
from .symbols import EXCEL_COLORS, VIEWER_COLORS
from .wire_number_table import split_unit_terminal

EXCEL_FILENAME = 'cable_consistency.xlsx'

SUMMARY_SHEET = 'Summary'
CHECK_SHEET = '整合性チェック'
DETAIL_SHEET = '照合明細'
WIRE_CHECK_SHEET = '芯線照合'
INDEX_SHEET = '図面一覧'
LEGEND_SHEET = '記号の説明'
RELATION_SHEET = '図面の関係'
RELATION_COLUMNS = ['展開接続図', 'ケーブル一覧表', '線番設定表', 'ケーブル製作図']
EXTRACT_SHEET = 'ケーブル抽出'
LIST_SHEET = 'ケーブル一覧表'
WIRE_SHEET = '線番設定表'
CONN_SHEET = '製作図の接続表'
SCHEMATIC_SHEET = '展開接続図'
PATTERN_SHEET = '図番パターン照合'
TEXT_SHEET = '製作図の文字'
# 固定のシート（並び順）。`All Diff` と `<グループ名> Diff` は `ケーブル抽出` の後ろ、`整合性チェック` の前に入る
FIXED_SHEET_NAMES = [SUMMARY_SHEET, LEGEND_SHEET, INDEX_SHEET, RELATION_SHEET, EXTRACT_SHEET, CHECK_SHEET, DETAIL_SHEET, WIRE_CHECK_SHEET, SCHEMATIC_SHEET,
                     LIST_SHEET, WIRE_SHEET, CONN_SHEET, TEXT_SHEET, PATTERN_SHEET]
SET_LABELS = {SET_405: '展開接続図（405）', SET_393: 'ケーブル製作図（393）'}
INDEX_COLUMNS = ['セット', '図番', 'タイトル', 'サブタイトル', '図面の種類', 'ケーブル数', '備考']
EXTRACT_COLUMNS = ['セット', '図番', 'ケーブル番号', '備考']

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

SYMBOL_COLORS = EXCEL_COLORS     # 記号 → Excel のセル色（背景, 文字）。定義は symbols.py

# 内部リンクは1シートに 65,530 件まで。超えるときはリンクを付けず記号だけを書く
_MAX_LINKS = 60000


def _cell_text(value):
    """列幅の計算用。欠損（空欄）は空文字（`str(pd.NA)` の `<NA>` を数えない）。"""
    return '' if pd.isna(value) else str(value)


def _int_or_blank(values):
    """数値と空文字が混ざる列は Arrow（Streamlit の表）に変換できないので、欠損を許す整数型にそろえる（Excel では空欄）。"""
    return pd.Series([None if v in ('', None) else int(v) for v in values], dtype='Int64')


# ---------------------------------------------------------------- シートの DataFrame

def _cable_count_rows(rows):
    """ケーブル番号の比較の件数（Summary の行）。"""
    counts = count_rows(rows)
    return [
        ('ケーブル番号: 展開接続図', counts['405only'] + counts['both']),
        ('ケーブル番号: ケーブル製作図', counts['393only'] + counts['both']),
        ('ケーブル番号: 両方にある', counts['both']),
        ('ケーブル番号: 展開接続図だけ', counts['405only']),
        ('ケーブル番号: ケーブル製作図だけ', counts['393only']),
    ]


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
        (f'図面: {KIND_SCHEMATIC}（照合に使った）', sum(KIND_SCHEMATIC in d['kinds'] for d in drawings)),
        ('図面: 文字の無い図面', sum(d['no_text'] for d in drawings)),
        ('図面: 照合に使っていない図面', len(result['unused'])),
        ('図面: 全体', len(drawings)),
        ('図面: 展開接続図（405）のセット', sum(d['set'] == SET_405 for d in drawings)),
        ('図面: ケーブル製作図（393）のセット', sum(d['set'] == SET_393 for d in drawings)),
        *_cable_count_rows(result['compare']['rows']),
    ]
    rows += [[name, *[None] * len(SYMBOLS), n] for name, n in kinds]
    # 図面の種類の行は記号の列が空欄（None）。数値と空文字が混ざる object 列にすると Arrow に変換できないので整数型にそろえる
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS).astype({c: 'Int64' for c in SUMMARY_COLUMNS[1:]})


def legend_frame(result=None):
    """記号の説明（記号の意味と色・項目ごとの判定条件・判断根拠の語）。結果に依らない固定の表。"""
    return pd.DataFrame(legend.legend_rows(), columns=legend.COLUMNS)


def relation_frame(result):
    """図面の関係。アップロードした展開接続図（405。図番順）ごとに、関連する ケーブル一覧表・線番設定表・ケーブル製作図 の図番を `、` 区切りで並べる
    （関連の求め方は `consistency.drawing_relations()`）。関連が無ければ空欄。"""
    rows = [[name, *('、'.join(x) for x in rel)] for name, *rel in drawing_relations(result['drawings'])]
    return pd.DataFrame(rows, columns=RELATION_COLUMNS)


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
    columns = ['図面種別', '参照元図面種別', 'パターン', '参照元の図番', '当たった図面', '件数', '備考']
    return pd.DataFrame([[r[c] for c in columns] for r in pattern_resolutions(result['drawings'])], columns=columns)


def text_frame(result):
    return pd.DataFrame(fab_text_rows(result['drawings']), columns=['図番', '文字'])


def index_frame(result):
    """抽出した図面の一覧（展開接続図のセット → ケーブル製作図のセット。それぞれ図番順）。"""
    unused = set(result['unused'])
    extracts = {(drawing_set, r['drawing_number']): r for drawing_set, results in result['extracts'].items() for r in results}
    rows = []
    for drawing_set in (SET_405, SET_393):
        for d in sorted((d for d in result['drawings'] if d['set'] == drawing_set), key=lambda d: d['drawing_number']):
            e = extracts[(drawing_set, d['drawing_number'])]
            remark = [x for x, on in (('文字が無い', d['no_text']), ('照合に使っていない', d['drawing_number'] in unused)) if on]
            rows.append([SET_LABELS[drawing_set], d['drawing_number'], e['title'], e['subtitle'], '、'.join(d['kinds']),
                         len(e['cables']), '、'.join(remark)])
    return pd.DataFrame(rows, columns=INDEX_COLUMNS)


def extract_frame(result):
    """抽出したケーブル番号（図番×ケーブル番号×備考。展開接続図のセット → ケーブル製作図のセット）。"""
    rows = [[SET_LABELS[drawing_set], r['drawing_number'], c['cable'], c['remark']]
            for drawing_set in (SET_405, SET_393) for r in result['extracts'][drawing_set] for c in r['cables']]
    return pd.DataFrame(rows, columns=EXTRACT_COLUMNS)


def sheet_plan(result):
    """出力する全シートを Excel と同じ順で `[(シート名, DataFrame, 比較の行 または None), ...]` にする。

    `All Diff` と `<グループ名> Diff` は第3要素に比較の行（`compare_cables()` の結果）を持つ（Excel では行を区分で色分けして書く）。
    """
    compare = result['compare']
    group_names = group_sheet_names([g['name'] for g in compare['groups']], reserved=FIXED_SHEET_NAMES)
    frames = {SUMMARY_SHEET: summary_frame, LEGEND_SHEET: legend_frame, RELATION_SHEET: relation_frame, CHECK_SHEET: check_frame, DETAIL_SHEET: detail_frame,
              WIRE_CHECK_SHEET: wire_check_frame, INDEX_SHEET: index_frame, EXTRACT_SHEET: extract_frame,
              LIST_SHEET: list_frame, WIRE_SHEET: wire_frame, CONN_SHEET: conn_frame, SCHEMATIC_SHEET: schematic_frame,
              PATTERN_SHEET: pattern_frame, TEXT_SHEET: text_frame}
    compare_sheets = [(ALL_SHEET, compare['rows'])] + [(n, g['rows']) for n, g in zip(group_names, compare['groups'])]
    plan = []
    for name in FIXED_SHEET_NAMES:
        plan.append((name, frames[name](result), None))
        if name == EXTRACT_SHEET:
            plan += [(sheet, compare_frame(rows), rows) for sheet, rows in compare_sheets]
    return plan


def preview_sheets(result):
    """出力する Excel の全シートを、Excel と同じ順で `[(シート名, DataFrame), ...]` にする（`sheet_plan()` から）。"""
    return [(name, frame) for name, frame, _ in sheet_plan(result)]


# ---------------------------------------------------------------- Excel

def build_workbook(result):
    """`run_check()` の結果から Excel のバイト列を作る（シートは `sheet_plan()`＝`preview_sheets()` と同じ）。"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        book = writer.book
        header_fmt = book.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
        sym_fmts = {s: book.add_format({'bg_color': bg, 'font_color': fg, 'align': 'center'}) for s, (bg, fg) in SYMBOL_COLORS.items()}
        sym_fmts[NA] = book.add_format({'align': 'center'})
        row_fmts = {s: book.add_format({'bg_color': bg, 'font_color': fg}) for s, (bg, fg) in SYMBOL_COLORS.items()}
        compare_fmts = status_formats(book)
        viewer_fmts = {k: book.add_format({'bg_color': c, 'font_color': '#000000', 'align': 'center'})
                       for k, c in VIEWER_COLORS.items() if k in SYMBOLS}
        for name, frame, compare_rows in sheet_plan(result):
            if name == CHECK_SHEET:
                _write_check_sheet(writer, result, frame, header_fmt, sym_fmts)
                continue
            if compare_rows is not None:
                write_compare_sheet(writer, name, compare_rows, header_fmt, compare_fmts)
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
            elif name == LEGEND_SHEET:
                sym_col = list(frame.columns).index('記号・語')
                viewer_col = list(frame.columns).index('viewer の文字色')
                for i, values in enumerate(frame.values.tolist(), start=1):
                    if values[0] == '記号' and values[sym_col] in sym_fmts:
                        ws.write(i, sym_col, values[sym_col], sym_fmts[values[sym_col]])
                        ws.write(i, viewer_col, values[viewer_col], viewer_fmts[values[sym_col]])
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
