"""app.py の画面テスト（streamlit.testing.v1.AppTest）。

合成 DXF（ezdxf で作る最小のもの）をアップロードして、画面の分岐を確かめる。
守るもの: a1 単一ファイル／a3 図番でないファイルの無視／a4 有効0件／a5 同名2回／壊れた DXF、
タブ（405 展開接続図・393 ケーブル製作図）の独立、DWG の警告、Excel ファイル名（D7・D8・D11）。
画面はタブ2つ。TAB_405 / TAB_393 は at.tabs・at.file_uploader の並び順の添字。
"""
import os

import ezdxf
import pytest
from streamlit.testing.v1 import AppTest

TAB_405, TAB_393 = 0, 1
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'app.py')


def dxf_bytes(tmp_path, texts):
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    for i, t in enumerate(texts):
        msp.add_text(t, dxfattribs={'insert': (10, 10 + i * 10), 'height': 3})
    path = tmp_path / 'syn.dxf'
    doc.saveas(str(path))
    return path.read_bytes()


def run_app(files, tab=TAB_405):
    at = AppTest.from_file(APP, default_timeout=30).run()
    if files:
        uploader = at.file_uploader[tab]
        for name, content in files:
            uploader.upload(name, content)
        at.run()
    return at


def tab_buttons(at, tab):
    return at.tabs[tab].button


def tab_elements(at, tab, kind):
    """そのタブの中にある kind（'info' 'caption' 'warning' 'error'）の値のリスト。"""
    return [e.value for e in getattr(at.tabs[tab], kind)]


def texts(elements):
    return [e.value for e in elements]


def test_no_upload_shows_info_and_no_button():
    """守るもの: 有効ファイル0件では案内だけを出し、抽出ボタンを出さない（a4）。"""
    at = run_app([])
    assert not at.exception
    for tab in (TAB_405, TAB_393):
        assert any('図番形式' in v for v in tab_elements(at, tab, 'info'))
        assert len(tab_buttons(at, tab)) == 0


def test_only_non_target_files_show_no_button():
    at = run_app([('.DS_Store', b'x'), ('memo.txt', b'x')])
    assert not at.exception
    assert len(tab_buttons(at, TAB_405)) == 0
    assert any('図番形式でないファイル 2 件' in v for v in tab_elements(at, TAB_405, 'caption'))


def test_extract_single_file(tmp_path):
    """守るもの: 1ファイルを抽出し、結果表・ダウンロード・図番別の展開が出る（a1）。"""
    data = dxf_bytes(tmp_path, ['W No.M39001', 'W No.M65A61 ACCESSORY CABLE', 'MC121A1'])
    at = run_app([('EE1234-405-01A.dxf', data), ('.DS_Store', b'x')])
    assert not at.exception
    assert any('処理対象 1 件' in c.value for c in at.caption)
    tab_buttons(at, TAB_405)[0].click().run()
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
    assert any('EE1234-405-01A.dxf' in v for v in tab_elements(at, TAB_405, 'warning'))
    assert any('処理対象 1 件' in c.value for c in at.caption)


def test_broken_dxf_reports_error_and_keeps_going(tmp_path):
    """守るもの: 壊れた DXF が1件あっても、他の図面は処理して結果を出す。"""
    good = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', good), ('EE1234-405-02A.dxf', b'not a dxf')])
    tab_buttons(at, TAB_405)[0].click().run()
    assert not at.exception
    assert any('EE1234-405-02A.dxf' in e.value for e in at.error)
    assert at.dataframe[0].value['図番'].tolist() == ['EE1234-405-01A']


def test_393_tab_extracts_and_names_its_own_excel(tmp_path):
    """守るもの: 393（ケーブル製作図）タブでも同じ抽出ができ、Excel は 405 と別の名前（D7・D8）。
    裸の番号（ケーブル一覧表・線番設定表の W No. 列）も拾い、W No. 欄が空欄の図面は0件になる。"""
    table = dxf_bytes(tmp_path, ['W No.', 'M39001', 'M39001', 'M65A61', 'C10004'])
    blank = dxf_bytes(tmp_path, ['W No.', 'Module No.'])
    at = run_app([('EE6892-365-51A.dxf', table), ('EE3298-523-11D.dxf', blank)], tab=TAB_393)
    tab_buttons(at, TAB_393)[0].click().run()
    assert not at.exception and not at.error
    summary = at.dataframe[0].value
    assert summary['図番'].tolist() == ['EE3298-523-11D', 'EE6892-365-51A']
    assert summary['ケーブル数'].tolist() == [0, 2]  # 重複は1件・C10004 型は拾わない
    # AppTest はダウンロードのファイル名を公開しない。393 タブのボタンであること（キー）だけ確かめ、
    # 名前の対応は test_excel_output.py::test_excel_filenames_differ_by_kind が守る。
    assert at.download_button[0].key == 'download_393'
    assert at.download_button[0].proto.label == 'Excelをダウンロード'


def test_tabs_are_independent(tmp_path):
    """守るもの: 一方のタブで抽出しても、もう一方のタブに結果や警告が出ない（D7）。
    ウィジェットキー・セッションキーがタブごとに別であること。"""
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', data)], tab=TAB_405)
    tab_buttons(at, TAB_405)[0].click().run()
    assert 'cable_result_405' in at.session_state
    assert 'cable_result_393' not in at.session_state
    assert len(tab_buttons(at, TAB_393)) == 0                      # 393 は未入力のままボタンが無い
    assert any('図番形式' in v for v in tab_elements(at, TAB_393, 'info'))
    # 393 にも別のファイルを入れて抽出しても、405 の結果は消えない
    at.file_uploader[TAB_393].upload('EE9999-393-01A.dxf', dxf_bytes(tmp_path, ['M39002'])).run()
    tab_buttons(at, TAB_393)[0].click().run()
    assert not at.exception
    assert at.session_state['cable_result_405']['results'][0]['drawing_number'] == 'EE1234-405-01A'
    assert at.session_state['cable_result_393']['results'][0]['drawing_number'] == 'EE9999-393-01A'


@pytest.mark.parametrize('tab', [TAB_405, TAB_393])
def test_dwg_files_get_a_warning_in_each_tab(tab, tmp_path):
    """守るもの: DWG は黙って無視せず、名前を出して DXF への変換を促す（D11）。
    図番形式でないファイルは従来どおり警告なしで無視、DXF は処理対象に残る。"""
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE3298-574-01A.DWG', b'AC1018'), ('memo.dwg', b'x'),
                  ('EE1234-405-01A.dxf', data)], tab=tab)
    assert not at.exception
    warnings = tab_elements(at, tab, 'warning')
    assert any('EE3298-574-01A.DWG' in v and 'DXF' in v for v in warnings)
    assert not any('memo.dwg' in v for v in warnings)
    captions = tab_elements(at, tab, 'caption')
    assert any('処理対象 1 件（図番形式でないファイル 1 件は無視）' in v for v in captions)
    assert len(tab_buttons(at, tab)) == 1


def test_only_dwg_files_show_warning_and_no_button():
    at = run_app([('EE3298-574-01A.DWG', b'AC1018')], tab=TAB_393)
    assert any('DWG' in v for v in tab_elements(at, TAB_393, 'warning'))
    assert len(tab_buttons(at, TAB_393)) == 0
