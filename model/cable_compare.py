"""展開接続図(405)とケーブル製作図(393)から出力した Excel のケーブルを比較するモデル層（streamlit 非依存）。

入力は `excel_output.build_workbook()` が作る Excel の `ケーブル一覧` シート（図番・ケーブル番号）。
シート名・列名は excel_output の定数を使い、出力の形と入力の仕様がずれないようにする。
"""
import unicodedata

import openpyxl

from .excel_output import LIST_COLUMNS, LIST_SHEET

DRAWING_COL, CABLE_COL = LIST_COLUMNS[0], LIST_COLUMNS[1]

ONLY_405, ONLY_393, BOTH = '405only', '393only', 'both'


def _normalize_cable(value):
    """全角→半角（NFKC）・前後の空白除去・大文字化。"""
    return unicodedata.normalize('NFKC', str(value)).strip().upper()


def _text(value):
    return '' if value is None else str(value).strip()


def read_cable_list(source, label):
    """Excel の `ケーブル一覧` シートを {ケーブル番号: [図番, …]} に読む。

    Args:
        source: ファイルオブジェクト（アップロード）またはパス。
        label: エラーメッセージに出す呼び名（例: '展開接続図のExcel'）。
    Returns:
        図番は昇順・重複なし。図番かケーブル番号が空の行は読み飛ばす。0行でも正常（空の辞書）。
    Raises:
        ValueError: 読めない・シートが無い・列名が違うとき（何が悪いかを書いた日本語）。
    """
    try:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - 壊れた xlsx は種類が多い（BadZipFile・InvalidFileException 等）
        raise ValueError(f'{label}: Excelファイル(.xlsx)として読み込めません（{type(e).__name__}）') from e
    try:
        if LIST_SHEET not in wb.sheetnames:
            raise ValueError(
                f'{label}: 「{LIST_SHEET}」シートがありません。'
                'このアプリが出力したExcelファイルを指定してください。')
        rows = wb[LIST_SHEET].iter_rows(values_only=True)
        header = next(rows, None) or ()
        names = [_text(h) for h in header]
        for col in (DRAWING_COL, CABLE_COL):
            if col not in names:
                raise ValueError(f'{label}: 「{LIST_SHEET}」シートに「{col}」列がありません。')
        i_drawing, i_cable = names.index(DRAWING_COL), names.index(CABLE_COL)
        result = {}
        for row in rows:
            drawing = _text(row[i_drawing]) if i_drawing < len(row) else ''
            cable = _normalize_cable(row[i_cable]) if i_cable < len(row) and row[i_cable] is not None else ''
            if drawing and cable:
                result.setdefault(cable, set()).add(drawing)
        return {cable: sorted(drawings) for cable, drawings in result.items()}
    finally:
        wb.close()


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
