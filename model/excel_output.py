"""Excel 出力の共通の補助（streamlit 非依存）。列幅の計算に使う表示幅だけを持つ。

Excel を作る処理は `consistency_output.py`（整合性チェックの Excel。抽出・比較のシートを含む）にある。
"""


def display_width(text):
    """全角を2、半角を1として数えた表示幅の目安。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
