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


def extract_button(at, tab):
    """そのタブの「ケーブルを抽出」ボタン（図面一覧の「図面を表示」ボタンと区別するためラベルで探す）。"""
    (button,) = [b for b in at.tabs[tab].button if b.label == "ケーブルを抽出"]
    return button


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
    """守るもの: 1ファイルを抽出し、結果表（Excelプレビュー）・ダウンロード・図番別シートが出る（a1）。"""
    data = dxf_bytes(tmp_path, ['W No.M39001', 'W No.M65A61 ACCESSORY CABLE', 'MC121A1'])
    at = run_app([('EE1234-405-01A.dxf', data), ('.DS_Store', b'x')])
    assert not at.exception
    assert any('処理対象 1 件' in c.value for c in at.caption)
    extract_button(at, TAB_405).click().run()
    assert not at.exception
    assert not at.error
    summary = at.dataframe[0].value
    assert summary['図番'].tolist() == ['EE1234-405-01A']
    assert summary['ケーブル数'].tolist() == [2]
    # Excelプレビューのシート選択で図番別のシートに切り替える
    sheet_box = at.selectbox(key='preview_sheet_405')
    assert sheet_box.options == ['Summary', 'ケーブル一覧', 'EE1234-405-01A']
    sheet_box.select('EE1234-405-01A').run()
    per_drawing = at.dataframe[0].value
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
    extract_button(at, TAB_405).click().run()
    assert not at.exception
    assert any('EE1234-405-02A.dxf' in e.value for e in at.error)
    assert at.dataframe[0].value['図番'].tolist() == ['EE1234-405-01A']


def test_393_tab_extracts_and_names_its_own_excel(tmp_path):
    """守るもの: 393（ケーブル製作図）タブでも同じ抽出ができ、Excel は 405 と別の名前（D7・D8）。
    裸の番号（ケーブル一覧表・線番設定表の W No. 列）も拾い、W No. 欄が空欄の図面は0件になる。"""
    table = dxf_bytes(tmp_path, ['W No.', 'M39001', 'M39001', 'M65A61', 'C10004'])
    blank = dxf_bytes(tmp_path, ['W No.', 'Module No.'])
    at = run_app([('EE6892-365-51A.dxf', table), ('EE3298-523-11D.dxf', blank)], tab=TAB_393)
    extract_button(at, TAB_393).click().run()
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
    extract_button(at, TAB_405).click().run()
    assert 'cable_result_405' in at.session_state
    assert 'cable_result_393' not in at.session_state
    assert len(tab_buttons(at, TAB_393)) == 0                      # 393 は未入力のままボタンが無い
    assert any('図番形式' in v for v in tab_elements(at, TAB_393, 'info'))
    # 393 にも別のファイルを入れて抽出しても、405 の結果は消えない
    at.file_uploader[TAB_393].upload('EE9999-393-01A.dxf', dxf_bytes(tmp_path, ['M39002'])).run()
    extract_button(at, TAB_393).click().run()
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
    # 有効ファイル1件 → その図面の「図面を表示」と、抽出ボタンの2つだけ（DWG・図番でないファイルのボタンは無い）
    assert [b.label for b in tab_buttons(at, tab)] == ["図面を表示", "ケーブルを抽出"]


def test_only_dwg_files_show_warning_and_no_button():
    at = run_app([('EE3298-574-01A.DWG', b'AC1018')], tab=TAB_393)
    assert any('DWG' in v for v in tab_elements(at, TAB_393, 'warning'))
    assert len(tab_buttons(at, TAB_393)) == 0


# ---- 比較タブ（D13〜D17） ----------------------------------------------------------------
TAB_COMPARE = 2
COMPARE_405_UPLOADER, COMPARE_393_UPLOADER = 2, 3  # at.file_uploader の並び順（前の2つは DXF 用）


def workbook_bytes(drawing_cables):
    """{図番: [ケーブル番号…]} から、アプリが出力する形の Excel を作る。"""
    from model.excel_output import build_workbook
    return build_workbook([
        {'drawing_number': d, 'title': '', 'subtitle': '',
         'cables': [{'cable': c, 'remark': ''} for c in cables]}
        for d, cables in drawing_cables.items()
    ])


def run_compare(f405=None, f393=None):
    at = AppTest.from_file(APP, default_timeout=30).run()
    if f405 is not None:
        at.file_uploader[COMPARE_405_UPLOADER].upload('cable_list_405.xlsx', f405)
    if f393 is not None:
        at.file_uploader[COMPARE_393_UPLOADER].upload('cable_list_393.xlsx', f393)
    return at.run()


def test_compare_tab_needs_both_files():
    """守るもの: 片方または両方が未入力なら案内を出し、比較ボタンを押せない。"""
    one = workbook_bytes({'EE0001-405A': ['M39001']})
    for at in (run_compare(), run_compare(f405=one), run_compare(f393=one)):
        assert not at.exception
        assert any('両方のExcel' in v for v in tab_elements(at, TAB_COMPARE, 'info'))
        (button,) = tab_buttons(at, TAB_COMPARE)
        assert button.disabled


def test_compare_tab_shows_result_with_blanks_for_one_sided_cables():
    """守るもの: 2つのExcelを比較し、ケーブル番号順の表を出す。片方だけのケーブルは相手側の図番が空白（D15）。"""
    at = run_compare(
        workbook_bytes({'EE0001-405A': ['M39001', 'M65711']}),
        workbook_bytes({'EE0009-339A': ['M39001', 'M05008'], 'EE0008-339A': ['M05008']}),
    )
    tab_buttons(at, TAB_COMPARE)[0].click().run()
    assert not at.exception and not at.error
    frame = at.dataframe[0].value
    assert frame.columns.tolist() == ['ケーブル番号', '展開接続図の図番', 'ケーブル製作図の図番']
    assert frame.values.tolist() == [
        ['M05008', '', 'EE0008-339A、EE0009-339A'],
        ['M39001', 'EE0001-405A', 'EE0009-339A'],
        ['M65711', 'EE0001-405A', ''],
    ]
    assert at.download_button[0].key == 'download_compare'
    assert any('ケーブル 3 件' in v for v in tab_elements(at, TAB_COMPARE, 'caption'))


def test_compare_tab_shows_all_diff_and_group_diff_tabs():
    """守るもの: 結果は「All Diff」と「<グループ名> Diff」のサブタブに分かれ、グループ表の393側はその図面だけ。"""
    from model.excel_output import build_workbook
    f393 = build_workbook([
        {'drawing_number': 'EE0009-339A', 'title': 'SYS', 'subtitle': 'ケーブル一覧表',
         'cables': [{'cable': 'M39001', 'remark': ''}]},
        {'drawing_number': 'EE0005-339A', 'title': '線番設定表', 'subtitle': '',
         'cables': [{'cable': 'M05008', 'remark': ''}]},
    ])
    at = run_compare(workbook_bytes({'EE0001-405A': ['M39001']}), f393)
    tab_buttons(at, TAB_COMPARE)[0].click().run()
    assert not at.exception and not at.error
    labels = [t.label for t in at.tabs]
    assert labels[-3:] == ['All Diff', 'ケーブル一覧表 Diff', '線番設定表 Diff']
    assert [df.value.shape[0] for df in at.dataframe] == [2, 1, 2]
    assert at.dataframe[2].value.values.tolist() == [['M05008', '', 'EE0005-339A'], ['M39001', 'EE0001-405A', '']]


def test_compare_tab_reports_unreadable_workbook_and_shows_no_result():
    """守るもの: このアプリの出力でない Excel は、どのファイルの何が悪いかを st.error で伝え、結果は出さない。"""
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = 'Sheet1'
    buf = io.BytesIO()
    wb.save(buf)
    at = run_compare(buf.getvalue(), workbook_bytes({'EE0009-339A': ['M39001']}))
    tab_buttons(at, TAB_COMPARE)[0].click().run()
    assert not at.exception
    assert any('展開接続図のExcel' in e.value and 'シートがありません' in e.value for e in at.error)
    assert len(at.dataframe) == 0
    assert 'compare_result' not in at.session_state


def test_compare_tab_is_independent_of_extraction_tabs(tmp_path):
    """守るもの: 比較しても DXF 抽出タブの結果に触れず、逆も同じ（キーの衝突が無い）。"""
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', data)], tab=TAB_405)
    extract_button(at, TAB_405).click().run()
    at.file_uploader[COMPARE_405_UPLOADER].upload('cable_list_405.xlsx', workbook_bytes({'EE0001-405A': ['M39001']}))
    at.file_uploader[COMPARE_393_UPLOADER].upload('cable_list_393.xlsx', workbook_bytes({'EE0009-339A': ['M39001']}))
    at.run()
    tab_buttons(at, TAB_COMPARE)[0].click().run()
    assert not at.exception
    assert 'cable_result_405' in at.session_state and 'compare_result' in at.session_state
    assert 'cable_result_393' not in at.session_state
    assert at.session_state['cable_result_405']['results'][0]['drawing_number'] == 'EE1234-405-01A'


# ---- 図面の viewer（ケーブル番号を赤字で表示） ----

@pytest.fixture
def mounted(monkeypatch):
    """viewer 部品の差し替え。渡された data を記録する（AppTest は同じプロセスで app.py を走らせる）。"""
    from view import cable_viewer
    calls = []
    monkeypatch.setattr(cable_viewer, 'mount_viewer', lambda **kw: calls.append(kw))
    return calls


def show_button(at, tab, name):
    (button,) = [b for b in at.tabs[tab].button if b.key == f'show_{"405" if tab == TAB_405 else "393"}_{name}']
    return button


@pytest.mark.parametrize('tab', [TAB_405, TAB_393])
def test_viewer_shows_only_after_pressing_show_button(tab, tmp_path, mounted):
    """守るもの: 一覧に処理対象だけが並び、「図面を表示」を押すまで viewer は出ない。押すと赤字の件数つきで出る。"""
    data = dxf_bytes(tmp_path, ['W No.M39001', 'W No.M65A61', 'MC121A1'])
    at = run_app([('EE1234-405-01A.dxf', data), ('memo.txt', b'x')], tab=tab)
    assert not at.exception
    assert mounted == []
    show_button(at, tab, 'EE1234-405-01A.dxf').click().run()
    assert not at.exception
    assert len(mounted) == 1
    assert mounted[0]['data']['fname'] == 'EE1234-405-01A.dxf'
    assert mounted[0]['data']['count'] == 2
    assert not at.warning


def test_viewer_is_independent_between_tabs(tmp_path, mounted):
    """守るもの: 405 タブで図面を表示しても 393 タブには viewer も表示中の状態も出ない（キーが別）。"""
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', data)], tab=TAB_405)
    at.file_uploader[TAB_393].upload('EE1234-393-01A.dxf', data)
    at.run()
    show_button(at, TAB_405, 'EE1234-405-01A.dxf').click().run()
    assert len(mounted) == 1
    assert at.session_state['viewer_file_405'] == 'EE1234-405-01A.dxf'
    assert 'viewer_file_393' not in at.session_state


def test_viewer_can_be_closed_and_file_removal_hides_it(tmp_path, mounted):
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', data)])
    show_button(at, TAB_405, 'EE1234-405-01A.dxf').click().run()
    (close,) = [b for b in at.tabs[TAB_405].button if b.key == 'close_viewer_405']
    close.click().run()
    assert 'viewer_file_405' not in at.session_state
    assert len(mounted) == 1  # 閉じた後は描き直されない


def test_viewer_shows_warning_for_broken_dxf_and_keeps_page(tmp_path, monkeypatch):
    """守るもの: 描けない図面は警告だけで、画面（抽出ボタンなど）は壊れない。"""
    at = run_app([('EE1234-405-01A.dxf', b'broken')])
    show_button(at, TAB_405, 'EE1234-405-01A.dxf').click().run()
    assert not at.exception
    assert any('EE1234-405-01A.dxf を表示できませんでした' in w.value for w in at.warning)
    extract_button(at, TAB_405)  # 抽出ボタンは残っている


def test_viewer_works_before_extraction_and_does_not_touch_results(tmp_path, mounted):
    """守るもの: 抽出の前でも viewer が使え、抽出結果のセッションには影響しない。"""
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', data)])
    show_button(at, TAB_405, 'EE1234-405-01A.dxf').click().run()
    assert 'cable_result_405' not in at.session_state
    extract_button(at, TAB_405).click().run()
    assert not at.exception
    assert at.session_state['cable_result_405']['results'][0]['cables'][0]['cable'] == 'M39001'


def compare_dxf_uploader(at):
    (u,) = [u for u in at.file_uploader if u.key == 'compare_dxf']
    return u


def test_compare_tab_shows_uploaded_dxf_directly_and_replaces(tmp_path, mounted):
    """守るもの: 比較タブは DXF を1ファイルずつアップロードし、ボタン無しで直接表示する。上げ直すと次の図面になる。"""
    a = dxf_bytes(tmp_path, ['W No.M39001'])
    b = dxf_bytes(tmp_path, ['W No.M39001', 'M06004B'])
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert mounted == []
    assert not compare_dxf_uploader(at).proto.multiple_files
    compare_dxf_uploader(at).upload('a.dxf', a)
    at.run()
    assert not at.exception
    assert mounted[-1]['data']['fname'] == 'a.dxf' and mounted[-1]['data']['count'] == 1
    compare_dxf_uploader(at).upload('b.dxf', b)  # 次の図面
    at.run()
    assert not at.exception
    assert mounted[-1]['data']['fname'] == 'b.dxf' and mounted[-1]['data']['count'] == 2


def test_compare_tab_viewer_is_independent_of_excel_comparison(tmp_path, mounted):
    """守るもの: 図面の表示は Excel の比較（ボタン・結果）と独立。比較ボタンは従来どおり2つの Excel が揃うまで無効。"""
    at = AppTest.from_file(APP, default_timeout=30).run()
    compare_dxf_uploader(at).upload('a.dxf', dxf_bytes(tmp_path, ['W No.M39001']))
    at.run()
    (button,) = [b for b in at.tabs[TAB_COMPARE].button if b.key == 'run_compare']
    assert button.disabled
    assert 'compare_result' not in at.session_state


# ---- Excelプレビュー（出力する Excel の全シートを選択して表示） ----

def two_drawings(tmp_path):
    return [('EE1234-405-01A.dxf', dxf_bytes(tmp_path, ['W No.M39001', 'W No.M65A61 ACCESSORY CABLE'])),
            ('EE1234-405-02A.dxf', dxf_bytes(tmp_path, ['no cables here']))]


def test_excel_preview_lists_all_sheets_in_excel_order_and_defaults_to_summary(tmp_path):
    """守るもの: プレビューはダウンロードする Excel と同じシートを同じ順に並べ、既定は Summary。
    ケーブル0件の図面（02A）のシートは Excel に無いのでプレビューにも無い。"""
    at = run_app(two_drawings(tmp_path))
    extract_button(at, TAB_405).click().run()
    assert not at.exception
    box = at.selectbox(key='preview_sheet_405')
    assert box.options == ['Summary', 'ケーブル一覧', 'EE1234-405-01A']
    assert box.value == 'Summary'
    assert at.dataframe[0].value['図番'].tolist() == ['EE1234-405-01A', 'EE1234-405-02A']
    box.select('ケーブル一覧').run()
    assert at.dataframe[0].value.values.tolist() == [
        ['EE1234-405-01A', 'M39001', ''], ['EE1234-405-01A', 'M65A61', 'ACCESSORY CABLE']]


def test_excel_preview_is_independent_between_tabs(tmp_path):
    """守るもの: 405 のプレビューのシート選択は 393 タブに影響しない（キーが別）。"""
    files = two_drawings(tmp_path)
    at = run_app(files, tab=TAB_405)
    at.file_uploader(key='uploader_393').upload('EE6892-365-51A.dxf', files[0][1])
    at.run()
    extract_button(at, TAB_405).click().run()
    extract_button(at, TAB_393).click().run()
    at.selectbox(key='preview_sheet_405').select('ケーブル一覧').run()
    assert at.selectbox(key='preview_sheet_393').value == 'Summary'
    assert at.selectbox(key='preview_sheet_405').value == 'ケーブル一覧'


def test_excel_preview_resets_to_summary_when_sheet_disappears(tmp_path):
    """守るもの: 入力を差し替えて前回選んだシートが無くなっても、エラーにならず先頭（Summary）に戻る。"""
    files = two_drawings(tmp_path)
    at = run_app(files)
    extract_button(at, TAB_405).click().run()
    at.selectbox(key='preview_sheet_405').select('EE1234-405-01A').run()
    at.file_uploader(key='uploader_405').upload('EE1234-405-02A.dxf', files[1][1])
    at.run()
    extract_button(at, TAB_405).click().run()
    assert not at.exception
    assert at.selectbox(key='preview_sheet_405').value == 'Summary'


# ---- Excelプレビューの表示スタイル（DXF-extract-labels と同じ）: 狭いシート選択・図面を表示トグル ----

def test_expander_is_closed_at_first_and_stays_open_after_selection(tmp_path):
    """守るもの: プレビューの枠は初回は閉じ、シートを選び直したら開いたままになる（無条件に開かない）。"""
    at = run_app(two_drawings(tmp_path))
    extract_button(at, TAB_405).click().run()
    assert not at.exception
    assert 'preview_expanded_405' not in at.session_state  # 初回は閉じたまま
    box = at.selectbox(key='preview_sheet_405')
    box.select('ケーブル一覧').run()
    assert at.session_state['preview_expanded_405'] is True


def test_preview_shows_view_toggle_only_for_drawing_sheets(tmp_path, mounted):
    """守るもの: 「図面を表示」トグルは図番ごとのシートにだけ出る（Summary・ケーブル一覧には出ない）。
    ONにすると、その図面の viewer を（図面一覧のボタンを使わずに）プレビューの中に出す。"""
    at = run_app(two_drawings(tmp_path))
    extract_button(at, TAB_405).click().run()
    assert not at.toggle  # Summary
    at.selectbox(key='preview_sheet_405').select('ケーブル一覧').run()
    assert not at.toggle
    at.selectbox(key='preview_sheet_405').select('EE1234-405-01A').run()
    assert [t.label for t in at.toggle] == ['図面を表示']
    assert mounted == []  # 既定はOFF
    at.toggle[0].set_value(True).run()
    assert not at.exception
    assert mounted[-1]['data']['fname'] == 'EE1234-405-01A.dxf' and mounted[-1]['data']['count'] == 2
    assert at.session_state['preview_expanded_405'] is True
