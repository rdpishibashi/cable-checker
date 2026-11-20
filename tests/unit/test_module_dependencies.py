"""モジュールの依存の向きを守るテスト（ソースの import を読む。循環・逆流を作らない）。

守るもの: 記号・文字列の補助（symbols・cell_values）は照合の本体に依存しない／viewer 用の強調（highlights）は consistency を import しない
（consistency → highlights の一方向）／Excel を作る consistency_output は描画（cable_preview）と consistency の非公開名に依存しない／
viewer のデータ（cable_preview）は照合（consistency・highlights）に依存しない。
"""
import ast
import os

MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'model')


def imports(module):
    """model/<module>.py が相対 import するモジュール名の集合と、`from .x import _private` の非公開名の対応。"""
    tree = ast.parse(open(os.path.join(MODEL, module + '.py'), encoding='utf-8').read())
    mods, private = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                mods.add(node.module)
                private[node.module] = [a.name for a in node.names if a.name.startswith('_') and not a.name.startswith('__')]
            else:
                mods.update(a.name for a in node.names)
    return mods, private


def test_foundation_modules_do_not_depend_on_the_checks():
    """守るもの: symbols・cell_values は照合・出力・viewer のどれにも依存しない（最下層）。"""
    low = {'consistency', 'highlights', 'consistency_output', 'cable_focus', 'cable_preview', 'legend', 'schematic_reader'}
    assert not (imports('symbols')[0] & low) and not (imports('cell_values')[0] & low)


def test_highlights_does_not_import_consistency():
    """守るもの: viewer 用の強調は照合の本体を import しない（循環を作らない。consistency → highlights の一方向）。"""
    mods, _ = imports('highlights')
    assert 'consistency' not in mods and mods <= {'cell_values', 'symbols'}


def test_excel_output_does_not_depend_on_rendering_or_private_names_of_consistency():
    """守るもの: Excel を作るモジュールは描画（cable_preview）に依存せず、consistency の非公開名（_で始まる名前）も使わない。"""
    mods, private = imports('consistency_output')
    assert 'cable_preview' not in mods and private.get('consistency', []) == []


def test_viewer_data_does_not_depend_on_the_checks():
    """守るもの: viewer のデータ（cable_preview）は照合（consistency・highlights・cable_focus）に依存しない。symbols は共通。"""
    mods, _ = imports('cable_preview')
    assert not (mods & {'consistency', 'highlights', 'cable_focus', 'consistency_output'})
    assert 'cable_preview' not in imports('cable_focus')[0]
