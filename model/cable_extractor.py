"""展開接続図の DXF からケーブル番号を抽出するモデル層（streamlit 非依存）。

ケーブル番号は `W No.M39001` 形式のラベルから拾う。ラベル抽出自体は共有の
`extract_labels.py`（正本は DXF-extract-labels）を無変更で使う。
"""
import os
import re
import unicodedata

from .extract_labels import extract_labels

# 図番（共有 extract_labels.py の DRAWING_NUMBER_PATTERN と同じ）
DRAWING_NUMBER_PATTERN = r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'

# ケーブル番号本体: M＋数字2桁＋英数字3文字＋（末尾英字1文字）。例: M39001 / M06004B / M65A61
_CABLE_BODY = r'M\d{2}[0-9A-Z]{3}[A-Z]?'
# 直後が英数字なら別の語（MC121A1 等）なので不可
_WNO_PATTERN = re.compile(
    r'(?<![0-9A-Z])W\s*NO\.?\s*(' + _CABLE_BODY + r')(?![0-9A-Z])', re.IGNORECASE
)
_BARE_PATTERN = re.compile(_CABLE_BODY, re.IGNORECASE)

REMARK_SEPARATOR = '、'


def _normalize(label):
    """全角→半角（NFKC）。備考は元の大小文字を保つため大文字化しない。"""
    return unicodedata.normalize('NFKC', label)


def find_cables(labels):
    """ラベル列からケーブル番号を拾う。

    Returns:
        [{'cable': 'M39001', 'remark': '...'}, ...] 番号の昇順・ユニーク。
        同一番号の備考は重複を除いて REMARK_SEPARATOR で連結する。
    """
    remarks = {}  # cable -> 備考の出現順リスト
    for label in labels:
        text = _normalize(str(label)).strip()
        if not text:
            continue
        matches = list(_WNO_PATTERN.finditer(text))
        for i, m in enumerate(matches):
            cable = m.group(1).upper()
            # 備考は次の W No. が現れる手前まで
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            rest = text[m.end():end].strip()
            bucket = remarks.setdefault(cable, [])
            if rest and rest not in bucket:
                bucket.append(rest)
        if not matches and _BARE_PATTERN.fullmatch(text):
            remarks.setdefault(text.upper(), [])
    return [
        {'cable': cable, 'remark': REMARK_SEPARATOR.join(remarks[cable])}
        for cable in sorted(remarks)
    ]


def is_target_filename(name):
    """処理対象か: 拡張子 .dxf かつ拡張子なしの名前が図番の形に完全一致。"""
    base, ext = os.path.splitext(os.path.basename(name))
    if ext.lower() != '.dxf':
        return False
    return re.fullmatch(DRAWING_NUMBER_PATTERN, base, re.IGNORECASE) is not None


def dedupe_by_name(names):
    """同名の重複を除く。

    Returns:
        (残すインデックスのリスト, 重複でスキップした名前のリスト)
        先に現れたものを残す。名前の比較は拡張子を除いた大文字小文字無視。
    """
    seen = set()
    keep, skipped = [], []
    for i, name in enumerate(names):
        key = os.path.splitext(os.path.basename(name))[0].upper()
        if key in seen:
            skipped.append(name)
        else:
            seen.add(key)
            keep.append(i)
    return keep, skipped


def extract_cables(dxf_path, original_filename):
    """1つの DXF からケーブル情報を抽出する。

    Returns:
        {'drawing_number': 図番（拡張子なしのファイル名）, 'title', 'subtitle',
         'cables': find_cables() の結果}
        タイトル・サブタイトルが取れなければ空文字。
    """
    labels, info = extract_labels(
        dxf_path,
        extract_drawing_numbers_option=True,
        extract_title_option=True,
        original_filename=original_filename,
    )
    return {
        'drawing_number': os.path.splitext(os.path.basename(original_filename))[0],
        'title': info.get('title') or '',
        'subtitle': info.get('subtitle') or '',
        'cables': find_cables(labels),
    }
