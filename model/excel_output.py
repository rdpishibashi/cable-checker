"""ケーブル抽出結果の Excel 出力（streamlit 非依存）。

シート構成: `Summary`（図番ごとの件数）→ `ケーブル一覧`（図番×ケーブル番号の縦持ち）→
図番ごとのシート（シート名は図番。**ケーブルが1件以上ある図面だけ**。0件の図面は Summary にだけ載る）。
"""
import io

import pandas as pd

SUMMARY_SHEET = 'Summary'
LIST_SHEET = 'ケーブル一覧'
# 入力の種類（405＝展開接続図、393＝ケーブル製作図）ごとに別ファイルにする
EXCEL_FILENAMES = {'405': 'cable_list_405.xlsx', '393': 'cable_list_393.xlsx'}

SUMMARY_COLUMNS = ['図番', 'タイトル', 'サブタイトル', 'ケーブル数']
LIST_COLUMNS = ['図番', 'ケーブル番号', '備考']
DRAWING_COLUMNS = ['ケーブル番号', '備考']


def summary_frame(results):
    return pd.DataFrame(
        [[r['drawing_number'], r['title'], r['subtitle'], len(r['cables'])] for r in results],
        columns=SUMMARY_COLUMNS,
    )


def cable_list_frame(results):
    return pd.DataFrame(
        [[r['drawing_number'], c['cable'], c['remark']] for r in results for c in r['cables']],
        columns=LIST_COLUMNS,
    )


def drawing_frame(result):
    return pd.DataFrame(
        [[c['cable'], c['remark']] for c in result['cables']], columns=DRAWING_COLUMNS
    )


def preview_sheets(results):
    """出力する Excel の全シートを、Excel と同じ順で `[(シート名, DataFrame), ...]` にする。

    画面の「Excelプレビュー」と `build_workbook()` が同じ関数を使うので、プレビューと Excel の内容は一致する。
    図番順に並べ替える。図番ごとのシートはケーブルが1件以上ある図面だけ（シート名は31文字まで）。
    """
    ordered = sorted(results, key=lambda r: r['drawing_number'])
    sheets = [(SUMMARY_SHEET, summary_frame(ordered)), (LIST_SHEET, cable_list_frame(ordered))]
    sheets += [(r['drawing_number'][:31], drawing_frame(r)) for r in ordered if r['cables']]
    return sheets


def build_workbook(results):
    """抽出結果（`extract_cables()` の戻り値のリスト）から Excel のバイト列を作る（シートは `preview_sheets()`）。"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        header_fmt = writer.book.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
        for name, frame in preview_sheets(results):
            _write(writer, name, frame, header_fmt)
    return output.getvalue()


def _write(writer, sheet_name, df, header_fmt):
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    ws = writer.sheets[sheet_name]
    for col, name in enumerate(df.columns):
        ws.write(0, col, name, header_fmt)
        values = df[name].astype(str) if len(df) else []
        width = max([len(name) * 2] + [display_width(v) for v in values]) + 2
        ws.set_column(col, col, min(width, 60))
    ws.freeze_panes(1, 0)


def display_width(text):
    """全角を2、半角を1として数えた表示幅の目安。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
