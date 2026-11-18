"""整合性チェック（393）の Excel 出力とプレビュー（streamlit 非依存）。

シート構成: `Summary`（照合ごとの OK／NG／対象なし の件数と、図面の種類ごとの件数）→ `整合性チェック`
（ケーブル番号ごとの照合 0〜7）→ `ケーブル一覧表`・`線番設定表`・`製作図の接続`（読み取った明細）→
`パターンの解決`・`製作図の文字`（照合 1・2・3・5 の根拠）。
画面の「分析結果プレビュー」と Excel は同じ `preview_sheets()` から作る（別実装にしない）。
結果の行色は他の比較ツールと同じ薄い配色（OK＝薄い緑、NG＝薄いピンク、対象なし＝色なし）。
"""
import io

import pandas as pd

from . import cable_list_table, wire_number_table
from .consistency import (CHECK_NAMES, KIND_FAB, KIND_LIST, KIND_WIRE, NA, NG, OK, SEP, _codes, _values,
                          connection_judgments, fab_text_rows, pattern_resolutions)
from .excel_output import display_width

EXCEL_FILENAME = 'cable_consistency_393.xlsx'

SUMMARY_SHEET = 'Summary'
CHECK_SHEET = '整合性チェック'
LIST_SHEET = 'ケーブル一覧表'
WIRE_SHEET = '線番設定表'
CONN_SHEET = '製作図の接続'
PATTERN_SHEET = 'パターンの解決'
TEXT_SHEET = '製作図の文字'

SUMMARY_COLUMNS = ['項目', OK, NG, NA, '件数']
CHECK_COLUMNS = ['ケーブル番号', '照合', '結果', '一覧表の図番', '一覧表の行',
                 'ケーブル製作図の図番', '線番設定表の図番', '内容']
LIST_COLUMNS = ['図番', '行', *cable_list_table.FIELDS, '削除された値']
CONN_COLUMNS = ['図番', '端の数',
                *[f'端{k}_{x}' for k in (1, 2) for x in ('表', '位置', '行', '線番', 'PIN No.')],
                'ほかの端', 'ラベル', '削除されたラベル', '注記の線番設定表パターン']

# 結果の行色（背景, 文字）。Tools/CLAUDE.md「Excel Output Pattern」の明るい配色。対象なしは色なし
RESULT_COLORS = {OK: ('#EAF7EC', '#006100'), NG: ('#FEF1F5', '#9C0006')}


def _cell_text(value):
    """列幅の計算用。欠損（空欄）は空文字（`str(pd.NA)` の `<NA>` を数えない）。"""
    return '' if pd.isna(value) else str(value)


def summary_frame(result):
    rows = []
    for name in CHECK_NAMES:
        counts = {r: 0 for r in (OK, NG, NA)}
        for c in result['checks']:
            if c['照合'] == name:
                counts[c['結果']] += 1
        rows.append([name, counts[OK], counts[NG], counts[NA], sum(counts.values())])
    drawings = result['drawings']
    kinds = [
        (f'図面: {KIND_LIST}', sum(KIND_LIST in d['kinds'] for d in drawings)),
        (f'図面: {KIND_WIRE}', sum(KIND_WIRE in d['kinds'] for d in drawings)),
        (f'図面: {KIND_FAB}（線番/PIN の表がある）', sum(KIND_FAB in d['kinds'] for d in drawings)),
        ('図面: 文字の無い図面', sum(d['no_text'] for d in drawings)),
        ('図面: 全体', len(drawings)),
    ]
    rows += [[name, None, None, None, n] for name, n in kinds]
    # 図面の種類の行は結果の列が空欄（None）。数値と空文字が混ざる object 列にすると、Streamlit の表（Arrow）に変換できないので
    # 欠損を許す整数型にそろえる（Excel では空欄、表では数値のまま並べ替えられる）
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS).astype({c: 'Int64' for c in SUMMARY_COLUMNS[1:]})


def check_frame(result):
    frame = pd.DataFrame([[c[k] for k in CHECK_COLUMNS] for c in result['checks']], columns=CHECK_COLUMNS)
    # 線番設定表にだけあるケーブルの行は一覧表の行が空（''）。数値と混ざる object 列は Arrow に変換できないので整数型にそろえる
    frame['一覧表の行'] = frame['一覧表の行'].replace('', None).astype('Int64')
    return frame


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
    drawings = result['drawings']
    units, codes = _list_ends(drawings)
    n_max = max([int(k[2:]) for d in drawings for r in d['wire_rows'] for k in r if k.startswith('線番')] or [0])
    wire_cols = [f'線番{n}' for n in range(1, n_max + 1)]
    columns = (['図番', '表', '行', 'ケーブル番号'] + wire_cols
               + ['接続先1', '接続先2']
               + [f'{k}_{x}' for k in ('接続先1', '接続先2') for x in ('ユニット', '端子台', 'ユニット判定', '端子台判定')]
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
            for k, j in connection_judgments(r, lu, lc).items():
                row.update({f'{k}_{x}': v for x, v in j.items()})
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


def pattern_frame(result):
    columns = ['種別', 'パターン', '参照元の図番', '当たった図面', '件数', '備考']
    return pd.DataFrame([[r[c] for c in columns] for r in pattern_resolutions(result['drawings'])], columns=columns)


def text_frame(result):
    return pd.DataFrame(fab_text_rows(result['drawings']), columns=['図番', '文字'])


def preview_sheets(result):
    """出力する Excel の全シートを、Excel と同じ順で `[(シート名, DataFrame), ...]` にする。"""
    return [(SUMMARY_SHEET, summary_frame(result)), (CHECK_SHEET, check_frame(result)),
            (LIST_SHEET, list_frame(result)), (WIRE_SHEET, wire_frame(result)), (CONN_SHEET, conn_frame(result)),
            (PATTERN_SHEET, pattern_frame(result)), (TEXT_SHEET, text_frame(result))]


def build_workbook(result):
    """`check_consistency()` の結果から Excel のバイト列を作る（シートは `preview_sheets()`）。"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        header_fmt = writer.book.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
        row_fmts = {key: writer.book.add_format({'bg_color': bg, 'font_color': fg})
                    for key, (bg, fg) in RESULT_COLORS.items()}
        for name, frame in preview_sheets(result):
            frame.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            for col, column in enumerate(frame.columns):
                ws.write(0, col, column, header_fmt)
                width = max([display_width(str(column))] + [display_width(_cell_text(v)) for v in frame[column]]) + 2
                ws.set_column(col, col, min(width, 60))
            if name == CHECK_SHEET:
                result_col = list(frame.columns).index('結果')
                for i, values in enumerate(frame.values.tolist(), start=1):
                    fmt = row_fmts.get(values[result_col])
                    for col, value in enumerate(values):
                        ws.write(i, col, None if pd.isna(value) else value, fmt)   # 欠損は空欄（書式だけ付ける）
            ws.freeze_panes(1, 1)
            if len(frame):
                ws.autofilter(0, 0, len(frame), len(frame.columns) - 1)
    return output.getvalue()
