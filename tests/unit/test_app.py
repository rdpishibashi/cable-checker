"""app.py の画面テスト（streamlit.testing.v1.AppTest）。

合成 DXF（ezdxf で作る最小のもの）をアップロードして、画面の分岐を確かめる。
守るもの: a1 単一ファイル／a3 図番でないファイルの無視／a4 有効0件／a5 同名2回／壊れた DXF。
"""
import os

import ezdxf
import pytest
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'app.py')


def dxf_bytes(tmp_path, texts):
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    for i, t in enumerate(texts):
        msp.add_text(t, dxfattribs={'insert': (10, 10 + i * 10), 'height': 3})
    path = tmp_path / 'syn.dxf'
    doc.saveas(str(path))
    return path.read_bytes()


def run_app(files):
    at = AppTest.from_file(APP, default_timeout=30).run()
    if files:
        uploader = at.file_uploader[0]
        for name, content in files:
            uploader.upload(name, content)
        at.run()
    return at


def texts(elements):
    return [e.value for e in elements]


def test_no_upload_shows_info_and_no_button():
    """守るもの: 有効ファイル0件では案内だけを出し、抽出ボタンを出さない（a4）。"""
    at = run_app([])
    assert not at.exception
    assert any('図番形式' in v for v in texts(at.info))
    assert len(at.button) == 0


def test_only_non_target_files_show_no_button():
    at = run_app([('.DS_Store', b'x'), ('memo.txt', b'x')])
    assert not at.exception
    assert len(at.button) == 0
    assert any('図番形式でないファイル 2 件' in c.value for c in at.caption)


def test_extract_single_file(tmp_path):
    """守るもの: 1ファイルを抽出し、結果表・ダウンロード・図番別の展開が出る（a1）。"""
    data = dxf_bytes(tmp_path, ['W No.M39001', 'W No.M65A61 ACCESSORY CABLE', 'MC121A1'])
    at = run_app([('EE1234-405-01A.dxf', data), ('.DS_Store', b'x')])
    assert not at.exception
    assert any('処理対象 1 件' in c.value for c in at.caption)
    at.button[0].click().run()
    assert not at.exception
    assert not at.error
    summary = at.dataframe[0].value
    assert summary['図番'].tolist() == ['EE1234-405-01A']
    assert summary['ケーブル数'].tolist() == [2]
    assert len(at.expander) >= 2  # プログラム説明＋図番別
    per_drawing = at.dataframe[1].value
    assert per_drawing['ケーブル番号'].tolist() == ['M39001', 'M65A61']


def test_duplicate_name_is_skipped_with_warning(tmp_path):
    """守るもの: 同名2回は先の1件だけ処理し、後ろは警告で知らせる（a5）。"""
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', data), ('EE1234-405-01A.dxf', data)])
    assert any('EE1234-405-01A.dxf' in w.value for w in at.warning)
    assert any('処理対象 1 件' in c.value for c in at.caption)


def test_broken_dxf_reports_error_and_keeps_going(tmp_path):
    """守るもの: 壊れた DXF が1件あっても、他の図面は処理して結果を出す。"""
    good = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', good), ('EE1234-405-02A.dxf', b'not a dxf')])
    at.button[0].click().run()
    assert not at.exception
    assert any('EE1234-405-02A.dxf' in e.value for e in at.error)
    assert at.dataframe[0].value['図番'].tolist() == ['EE1234-405-01A']
