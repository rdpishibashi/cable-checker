"""図面 viewer の赤字（ケーブル番号を含む文字）が、抽出結果のケーブルと実データで一致すること。

受入条件:
  Given sample-dxf/dataset/ME25-7103-0_Cu_R1chamber/ の 405_展開図（23 DXF）と、393_ケーブル のうちケーブルがある図面（28 DXF）
  When  render_base_svg() で描き、赤字の対象の文字から find_cables() で番号を集める
  Then  extract_cables() が拾ったケーブル番号の集合と一致する（見える図面に無い番号を赤くしない・拾った番号を赤字から漏らさない）

フォルダを集合として直接参照する（405 は同名・別内容の既存セットがあり名前引き禁止。見つからなければ失敗させ skip しない）。
"""
import glob
import os

import pytest

from model import cable_preview
from model.cable_extractor import extract_cables, find_cables

PROJECT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
BASE = os.path.join(PROJECT, 'sample-dxf', 'dataset', 'ME25-7103-0_Cu_R1chamber')


@pytest.mark.slow
@pytest.mark.parametrize('folder, expected_files', [('405_展開図', 23), ('393_ケーブル', 28)])
def test_highlighted_cables_match_extraction(folder, expected_files):
    files = sorted(glob.glob(os.path.join(BASE, folder, '*.dxf')))
    checked = 0
    for path in files:
        extracted = {c['cable'] for c in extract_cables(path, os.path.basename(path))['cables']}
        if folder.startswith('393') and not extracted:
            continue  # 393 のケーブル製作図などケーブル0件の図面は対象外（赤字も0件になるが描画が遅い）
        with open(path, 'rb') as f:
            base = cable_preview.render_base_svg(f.read())
        highlighted = {c['cable'] for text, _ in base.text_classes for c in find_cables([text])}
        assert highlighted == extracted, os.path.basename(path)
        checked += 1
    assert checked == expected_files
