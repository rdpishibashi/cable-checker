"""app.py の画面テスト（streamlit.testing.v1.AppTest）。

合成 DXF（ezdxf で作る最小のもの）をアップロードして、画面の分岐を確かめる。
画面は1つ（タブなし）: 展開接続図（405）と ケーブル製作図（393）のアップローダーを左右に並べ、「整合性をチェック」で結果
（分析結果プレビュー・ダウンロード）を出す。守るもの: 有効ファイル0件の案内／図番でないファイルの無視・DWG の警告／同名のスキップ（セット内・セット間）／
壊れた DXF／どちらか片方のセットだけの実行／ボタンの色／結果が入力に追従すること／プレビュー（シート）／図面の確認（開閉枠・ケーブル選択・関連図面・図形）／Excel。
"""
import io
import os
import re

import ezdxf
import openpyxl
import pytest
from streamlit.testing.v1 import AppTest

U405, U393 = 'uploader_405', 'uploader_393'
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'app.py')
CHECK_NAME = 'EE6892-365-61C.dxf'
SCH_NAME = 'EE1234-405-01A.dxf'      # list_with_schematic_pattern_bytes() の接続図パターン EE****-405-01 が指す


def dxf_bytes(tmp_path, texts):
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    for i, t in enumerate(texts):
        msp.add_text(t, dxfattribs={'insert': (10, 10 + i * 10), 'height': 3})
    path = tmp_path / 'syn.dxf'
    doc.saveas(str(path))
    return path.read_bytes()


def cable_list_dxf_bytes(tmp_path):
    """ケーブル一覧表だけの合成図面（M39001・M39002）。製作図は同梱しないので 照合1 は NG になる。"""
    from tests.unit.test_drawing_readers import cable_list_doc
    doc, _ = cable_list_doc()
    path = tmp_path / 'list.dxf'
    doc.saveas(str(path))
    return path.read_bytes()


def list_with_schematic_pattern_bytes(tmp_path):
    """M65206 の1行だけの一覧表（製作図パターン EE****-534-07・接続図パターン EE****-405-01・端末1 RACK/CN1）。"""
    from tests.unit.test_drawing_readers import line, new_doc, text
    doc, msp = new_doc()
    xs, top_xs = [0, 25, 60, 90, 120, 160, 200], [0, 25, 60, 120, 160, 200]
    for x in xs:
        line(msp, (x, 90), (x, 150 if x in top_xs else 140))
    for y in (150, 140, 130, 120, 110, 100, 90):
        line(msp, (0, y), (200, y))
    text(msp, '端末1', 85, 143)
    text(msp, 'ケーブル仕様', 130, 143)
    heads = (('W No.', 2), ('ケーブル製作図図面番号', 27), ('接続先', 62), ('コネクタ名称', 92), ('ケーブル種類', 122),
             ('接続図図面番号', 162))
    for label, x in heads:
        text(msp, label, x, 132, 2.5)
    for value, x in (('M65206', 2), ('EE****-534-07', 27), ('RACK', 62), ('CN1', 92), ('ACCESSORY CABLE', 122),
                     ('EE****-405-01', 162)):
        text(msp, value, x, 122, 2.5)
    path = tmp_path / 'list405.dxf'
    doc.saveas(str(path))
    return path.read_bytes()


def schematic_bytes(tmp_path):
    """M65206 の展開接続図（楕円を横切る2本の芯線。左はコネクタ CN1、右は CN2）。"""
    from tests.unit.test_schematic_reader import build
    return open(build(tmp_path, name='EE1234-405-01A'), 'rb').read()


def run_app(files_405=(), files_393=()):
    at = AppTest.from_file(APP, default_timeout=60).run()
    for key, files in ((U405, files_405), (U393, files_393)):
        for name, content in files:
            at.file_uploader(key=key).upload(name, content)
    at.run()
    return at


def check_button(at):
    return [b for b in at.button if b.key == 'run_check']


def run_check_click(at):
    check_button(at)[0].click().run()
    assert not at.exception
    return at


def values(elements):
    return [e.value for e in elements]


def both_sets(tmp_path):
    """405 のセット（一覧表が指す展開接続図と、指されない図面）と 393 のセット（M65206 の一覧表）。"""
    return ([(SCH_NAME, schematic_bytes(tmp_path)), ('EE5555-999-09A.dxf', schematic_bytes(tmp_path))],
            [(CHECK_NAME, list_with_schematic_pattern_bytes(tmp_path))])


# ---------------------------------------------------------------- 入力

def test_page_is_a_single_screen_with_two_uploaders_side_by_side():
    """守るもの: タブは無く、405 と 393 のアップローダーが別のキーで並ぶ（どちらもフォルダ・複数可）。ファイルが無ければ案内だけでボタンは出さない。"""
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and len(at.tabs) == 0
    assert [t.value for t in at.title] == ['Cable-checker - 展開接続図・ケーブル製作図の整合性チェック']
    assert at.file_uploader(key=U405).label == '展開接続図（405）（複数可，フォルダ可）'
    assert at.file_uploader(key=U393).label == 'ケーブル製作図（393）（複数可，フォルダ可）'
    assert at.file_uploader(key=U405).proto.multiple_files and at.file_uploader(key=U393).proto.multiple_files
    assert any('図番形式' in v for v in values(at.info))
    assert check_button(at) == []


@pytest.mark.parametrize('key', [U405, U393])
def test_non_target_files_are_ignored_and_dwg_gets_a_warning_in_each_set(key):
    """守るもの: 図番形式でないファイルは無視（件数を出す）、DWG は読めないので名指しで警告。有効0件ならボタンを出さない。"""
    at = AppTest.from_file(APP, default_timeout=30).run()
    for name in ('memo.txt', 'EE1234-405-01A.dwg'):
        at.file_uploader(key=key).upload(name, b'x')
    at.run()
    assert check_button(at) == []
    assert any('EE1234-405-01A.dwg はDWGファイルのため読み込めません' in v for v in values(at.warning))
    assert any('処理対象 0 件（図番形式でないファイル 1 件は無視）' in v for v in values(at.caption))


def test_duplicate_name_within_a_set_is_skipped_with_warning(tmp_path):
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', data), ('ee1234-405-01a.DXF', data)])
    assert any('ee1234-405-01a.DXF は同名のファイルが先にあるためスキップしました' in v for v in values(at.warning))
    assert any('処理対象 1 件' in v for v in values(at.caption))


def test_same_name_in_both_sets_is_skipped_on_the_393_side(tmp_path):
    """守るもの: 両方のセットに同じ名前があれば、ケーブル製作図（393）の側をスキップして警告する（展開接続図の側を使う）。"""
    data = dxf_bytes(tmp_path, ['W No.M39001'])
    at = run_app([('EE1234-405-01A.dxf', data)], [('EE1234-405-01A.dxf', data), ('EE1234-339-01A.dxf', data)])
    assert any('EE1234-405-01A.dxf は展開接続図（405）に同じ名前があるため、ケーブル製作図（393）の側をスキップしました' in v
               for v in values(at.warning))
    run_check_click(at)
    result = at.session_state['check_result']['result']
    assert [d['drawing_number'] for d in result['drawings'] if d['set'] == '393'] == ['EE1234-339-01A']
    assert [d['drawing_number'] for d in result['drawings'] if d['set'] == '405'] == ['EE1234-405-01A']


# ---------------------------------------------------------------- 実行と結果

def test_405_set_alone_runs_extraction_and_comparison(tmp_path):
    """守るもの: 展開接続図のセットだけでも実行できる。一覧表が無いので整合性チェックの行は0、ケーブル番号は「展開接続図だけ」として比較に出る。"""
    at = run_app([(SCH_NAME, dxf_bytes(tmp_path, ['W No.M39001', 'W No.M65A61']))])
    assert not at.exception and [b.proto.type for b in check_button(at)] == ['primary']
    run_check_click(at)
    assert not at.error
    sheets = at.selectbox(key='preview_sheet_check')
    assert sheets.options[:7] == ['Summary', '記号の説明', '図面一覧', '図面の関係', 'ケーブル抽出', 'All Diff', '整合性チェック']
    assert any('展開接続図 1 図面・ケーブル製作図 0 図面' in v and 'ケーブル番号 2 件（両方 0・展開接続図だけ 2・ケーブル製作図だけ 0）' in v
               and '一覧表の行 0 行' in v for v in values(at.caption))
    sheets.select('All Diff').run()
    assert at.dataframe[0].value.values.tolist() == [['M39001', 'EE1234-405-01A', ''], ['M65A61', 'EE1234-405-01A', '']]


def test_393_set_alone_runs_the_consistency_check_without_schematics(tmp_path):
    """守るもの: ケーブル製作図のセットだけでも実行できる。展開接続図の列は －、ケーブル番号は「ケーブル製作図だけ」。"""
    at = run_app([], [(CHECK_NAME, cable_list_dxf_bytes(tmp_path))])
    run_check_click(at)
    assert not at.error
    assert any('一覧表の行 2 行（不整合 2 行・要確認 0 行）' in v for v in values(at.caption))
    at.selectbox(key='preview_sheet_check').select('整合性チェック').run()
    frame = at.dataframe[0].value
    assert set(frame['図面の対応｜展開接続図']) == {'－'} and set(frame['ケーブル番号']) == {'M39001', 'M39002'}
    at.selectbox(key='preview_sheet_check').select('All Diff').run()
    assert [r[1] for r in at.dataframe[0].value.values.tolist()] == ['', '']


def test_both_sets_check_the_schematic_pointed_at_by_the_list_and_report_unused(tmp_path):
    """守るもの: 393 の一覧表の「接続図図面番号」が指す 405 は展開接続図として読まれて整合性チェックに入り、指されない 405 は
    「照合に使っていない図面」として info に出る。ケーブル番号の比較は両方にあるものとして出る。"""
    files_405, files_393 = both_sets(tmp_path)
    at = run_app(files_405, files_393)
    run_check_click(at)
    assert not at.error
    assert any('照合した展開接続図 1）' in v for v in values(at.caption))
    assert any('照合に使っていない図面 1 件: EE5555-999-09A' in v for v in values(at.info))
    at.selectbox(key='preview_sheet_check').select('整合性チェック').run()
    row = at.dataframe[0].value.iloc[0]
    assert row['ケーブル番号'] == 'M65206' and row['図面の対応｜展開接続図'] == '○' and row['端末1｜機器符号'] == '○'
    at.selectbox(key='preview_sheet_check').select('展開接続図').run()
    assert set(at.dataframe[0].value['図番']) == {'EE1234-405-01A'}
    at.selectbox(key='preview_sheet_check').select('図面一覧').run()
    index = at.dataframe[0].value
    assert index['セット'].tolist() == ['展開接続図（405）', '展開接続図（405）', 'ケーブル製作図（393）']
    assert index[index['図番'] == 'EE5555-999-09A'].iloc[0]['備考'] == '照合に使っていない'


def test_broken_dxf_in_either_set_reports_error_and_keeps_going(tmp_path):
    at = run_app([('EE0001-405-01A.dxf', b'not a dxf')], [('EE0002-393-01A.dxf', b'not a dxf'), (CHECK_NAME, cable_list_dxf_bytes(tmp_path))])
    run_check_click(at)
    errors = values(at.error)
    assert any('EE0001-405-01A.dxf を処理できませんでした' in e for e in errors)
    assert any('EE0002-393-01A.dxf を処理できませんでした' in e for e in errors)
    assert at.download_button and any('一覧表の行 2 行' in v for v in values(at.caption))


def test_warns_about_drawings_without_text_in_both_sets(tmp_path):
    """守るもの: 文字の無い図面は名指しで警告する（393 は読み取りを飛ばした図面、405 は一覧表が指して読み取った図面）。"""
    doc = ezdxf.new('R2010')
    doc.modelspace().add_line((0, 0), (10, 0))
    path = tmp_path / 'outline.dxf'
    doc.saveas(str(path))
    outline = path.read_bytes()
    at = run_app([(SCH_NAME, outline)], [('EE3298-637-02E.dxf', outline), (CHECK_NAME, list_with_schematic_pattern_bytes(tmp_path))])
    run_check_click(at)
    warnings = values(at.warning)
    assert any('EE3298-637-02E は文字が無いため読み取れません' in v for v in warnings)
    assert any('EE1234-405-01A は文字が無いため読み取れません' in v for v in warnings)
    assert at.download_button


def test_run_button_is_blue_until_there_is_a_result_then_white(tmp_path):
    """守るもの: 今の入力の結果が無いあいだは青（次の操作）、結果が出たら白（再実行はできる）。入力を変えたら青に戻る。"""
    files_405, files_393 = both_sets(tmp_path)
    at = run_app(files_405, files_393)
    assert [b.proto.type for b in check_button(at)] == ['primary']
    run_check_click(at)
    assert [b.proto.type for b in check_button(at)] == ['secondary']
    assert at.download_button[0].proto.type == 'primary'
    at.file_uploader(key=U405).upload('EE7777-405-01A.dxf', schematic_bytes(tmp_path))
    at.run()
    assert [b.proto.type for b in check_button(at)] == ['primary']


@pytest.mark.parametrize('changed', [U405, U393])
def test_result_disappears_when_either_set_changes(changed, tmp_path):
    """守るもの: どちらかのセットの入力（ファイル名・サイズ）が変わったら前回の結果を出さない（署名は2つのセットの組）。"""
    files_405, files_393 = both_sets(tmp_path)
    at = run_app(files_405, files_393)
    run_check_click(at)
    assert at.download_button
    at.file_uploader(key=changed).upload('EE7777-777-01A.dxf', dxf_bytes(tmp_path, ['W No.M39001']))
    at.run()
    assert not at.download_button and 'check_result' in at.session_state and not at.dataframe


def test_downloaded_excel_has_the_same_sheets_as_the_preview(tmp_path):
    """守るもの: ダウンロードする Excel は統合した1つ（cable_consistency.xlsx）で、シートはプレビューと同じ。抽出・比較の個別の Excel は無い。"""
    at = run_app(*both_sets(tmp_path))
    run_check_click(at)
    assert [b.proto.label for b in at.download_button] == ['分析結果ダウンロード']
    book = openpyxl.load_workbook(io.BytesIO(at.session_state['check_result']['xlsx']))
    assert book.sheetnames == at.selectbox(key='preview_sheet_check').options
    assert book.sheetnames[1:7] == ['記号の説明', '図面一覧', '図面の関係', 'ケーブル抽出', 'All Diff', '（タイトルなし） Diff']


# ---------------------------------------------------------------- プレビュー

def test_preview_comes_before_the_download_button_and_stays_open_after_selection(tmp_path):
    """守るもの: 「分析結果プレビュー」（枠は初回は閉じ、操作したら開いたまま）の下に「分析結果ダウンロード」。"""
    at = run_app(*both_sets(tmp_path))
    run_check_click(at)
    assert at.selectbox(key='preview_sheet_check').value == 'Summary'
    assert 'preview_expanded_check' not in at.session_state                 # 初回は閉じている
    types = [c.type for c in at.main.children.values()]
    assert types.index('expander') < types.index('download_button')
    at.selectbox(key='preview_sheet_check').select('ケーブル抽出').run()
    assert at.session_state['preview_expanded_check'] is True


def test_preview_resets_to_summary_when_the_sheet_disappears(tmp_path):
    """守るもの: 入力を差し替えて前回のシート（グループの Diff など）が無くなったら、先頭（Summary）に戻る。"""
    at = run_app(*both_sets(tmp_path))
    run_check_click(at)
    at.selectbox(key='preview_sheet_check').select('（タイトルなし） Diff').run()
    at.file_uploader(key=U393).upload('EE7777-777-01A.dxf', dxf_bytes(tmp_path, ['ケーブル番号なし']))   # 393 にケーブルが無ければグループの Diff は無い
    at.run()
    run_check_click(at)
    assert '（タイトルなし） Diff' not in at.selectbox(key='preview_sheet_check').options
    assert at.selectbox(key='preview_sheet_check').value == 'Summary'


@pytest.fixture
def mounted(monkeypatch):
    """viewer 部品の差し替え。渡された data を記録する（AppTest は同じプロセスで app.py を走らせる）。"""
    from view import cable_viewer
    calls = []
    monkeypatch.setattr(cable_viewer, 'mount_viewer', lambda **kw: calls.append(kw))
    return calls


SECTION = 'drawing_section_check'


def open_section(at, key=SECTION):
    at.session_state[key] = True
    at.run()
    assert not at.exception
    return at


def expander_labels(at):
    return [e.proto.label for e in at.expander]


def test_drawing_section_is_separate_from_the_preview_and_lists_the_drawings_of_both_sets(tmp_path, mounted):
    """守るもの: 図面の viewer は分析結果プレビューの外の「図面の確認」（初回は閉じる）に出る。プレビューに「図面を表示」トグルは無い。
    選べる図面はアップロードした 405 → 393（それぞれ図番順）。選んだ図面は種別のタイトル付きの開閉枠（既定で開く）に出る。"""
    data = dxf_bytes(tmp_path, ['W No.M39001', 'W No.M65A61', 'MC121A1'])
    files_405, files_393 = both_sets(tmp_path)
    at = run_app(files_405 + [('EE0001-405-01A.dxf', data)], files_393)
    run_check_click(at)
    assert not at.toggle and mounted == []
    assert [x for x in expander_labels(at) if x in ('分析結果プレビュー', '図面の確認')] == ['分析結果プレビュー', '図面の確認']
    open_section(at)
    assert not at.warning
    box = at.selectbox(key='drawing_select_check')
    assert box.options == ['EE0001-405-01A（405）', 'EE1234-405-01A（405）', 'EE5555-999-09A（405）', 'EE6892-365-61C（393）']
    assert expander_labels(at)[-1] == '展開接続図 EE0001-405-01A'
    assert mounted[-1]['data']['fname'] == 'EE0001-405-01A.dxf' and mounted[-1]['data']['count'] == 2 and not mounted[-1]['data']['marked']
    box.select('EE6892-365-61C（393）').run()
    assert mounted[-1]['data']['fname'] == 'EE6892-365-61C.dxf'
    assert expander_labels(at)[-1] == 'ケーブル製作図 EE6892-365-61C'
    assert not at.selectbox(key='drawing_select_check').value.endswith('（405）') and len(at.selectbox) == 2   # 393 ではケーブルの選択欄が無い


def test_shapes_only_when_a_cable_is_selected(tmp_path, mounted):
    """守るもの: 図形の強調はケーブルを選んだときだけ（選んでいないときの viewer の data に図形は無い）。"""
    at = run_app(*both_sets(tmp_path))
    run_check_click(at)
    open_section(at)
    assert mounted[-1]['data']['shapes'] == []


def test_viewer_shows_warning_when_drawing_cannot_be_rendered_and_keeps_page(tmp_path, monkeypatch):
    """守るもの: 描けない図面は警告だけで、画面（プレビュー・ダウンロード）は壊れない。"""
    from view import cable_viewer

    def boom(_b):
        raise ValueError('cannot render')
    monkeypatch.setattr(cable_viewer, 'render_base', boom)
    at = run_app(*both_sets(tmp_path))
    run_check_click(at)
    open_section(at)
    assert any('EE1234-405-01A.dxf を表示できませんでした' in w.value for w in at.warning)
    assert at.download_button[0].proto.label == '分析結果ダウンロード' and at.dataframe


def test_drawing_selection_returns_to_the_first_drawing_when_the_uploads_change(tmp_path, mounted):
    """守るもの: 入力を差し替えて前回選んだ図面が無くなったら、先頭の図面に戻る（選択肢外の値でずれない）。"""
    at = run_app(*both_sets(tmp_path))
    run_check_click(at)
    open_section(at)
    at.selectbox(key='drawing_select_check').select('EE6892-365-61C（393）').run()
    at.file_uploader(key=U393).upload('EE7777-777-01A.dxf', dxf_bytes(tmp_path, ['W No.M39001']))
    at.run()
    run_check_click(at)
    open_section(at)
    assert at.selectbox(key='drawing_select_check').value == 'EE1234-405-01A（405）'


def test_selecting_a_cable_lists_the_related_drawings_in_collapsible_windows_with_counts(tmp_path, mounted):
    """守るもの: 展開接続図でケーブルを選ぶと、関連する図面（一覧表）が種別のタイトル付きの開閉枠で並ぶ。見出しに ×？△○ の件数が付く。
    開いている枠だけ描画され、マークの記号の色で強調する（marked）。閉じた枠は描画しない。"""
    at = run_app(*both_sets(tmp_path))
    run_check_click(at)
    open_section(at)
    cables = at.selectbox(key='drawing_cable_check')
    assert cables.options[0] == '（選択しない）' and 'M65206' in cables.options
    n = len(mounted)
    cables.select('M65206').run()
    labels = [x for x in expander_labels(at) if x.startswith(('展開接続図', 'ケーブル一覧表', '線番設定表', 'ケーブル製作図'))]
    assert labels[0].startswith('展開接続図 EE1234-405-01A　×') and any(x.startswith('ケーブル一覧表 EE6892-365-61C　×') for x in labels)
    assert all(re.search(r'×\d+ ？\d+ △\d+ ○\d+$', x) for x in labels)
    opened = mounted[n:]
    assert [m['data']['fname'] for m in opened] == ['EE1234-405-01A.dxf'] and opened[0]['data']['marked']   # 開いているのは選んだ図面だけ
    at.session_state['drawing_exp_check_ケーブル一覧表_EE6892-365-61C_M65206'] = True
    at.run()
    assert mounted[-1]['data']['fname'] == 'EE6892-365-61C.dxf' and mounted[-1]['data']['marked']
    assert mounted[-1]['data']['legend'] and mounted[-1]['data']['count'] > 0
    sch = next(m for m in mounted[n:] if m['data']['fname'] == 'EE1234-405-01A.dxf')['data']
    assert sch['shapes'] and all(p['d'] and p['color'] for p in sch['shapes'])       # 展開接続図の図形（記号・引出線・芯線・端末）


def schematic_with_reference_bytes(tmp_path, text='to POWER LINE (-405-08)'):
    return dxf_bytes(tmp_path, ['W No.M39001', text])


def test_related_drawings_listed_from_references_and_opened_in_collapsible_windows(tmp_path, mounted):
    """守るもの: 展開接続図の図中の参照 `(-xxx-xx)` を「関連図面」として一覧する。アップロード（405・393）に一致する図面は開閉枠（既定で閉じる）で
    表示でき、無ければ「アップロードに無い」。Excel の図番パターン照合は変えない。"""
    other = dxf_bytes(tmp_path, ['EE6892-405-08A'])
    at = run_app([('EE6892-405-01A.dxf', schematic_with_reference_bytes(tmp_path, 'to POWER LINE (-405-08) XRLS1 (-405-09)')),
                  ('EE6892-405-08A.dxf', other)], [])
    run_check_click(at)
    open_section(at)
    text = '\n'.join(values(at.markdown))
    assert '`-405-08`' in text and '→ EE6892-405-08A' in text and '`-405-09`' in text and '→ アップロードに無い' in text
    n = len(mounted)
    assert 'EE6892-405-08A' not in [m['data']['fname'][:-4] for m in mounted[n:]]
    label = '関連図面 -405-08　EE6892-405-08A'
    assert label in expander_labels(at) and not any('-405-09　' in x for x in expander_labels(at))
    at.session_state['drawing_exp_check_ref-405-08_EE6892-405-08A_（選択しない）'] = True
    at.run()
    assert mounted[-1]['data']['fname'] == 'EE6892-405-08A.dxf'


def test_no_related_drawings_message_when_the_schematic_has_no_reference(tmp_path, mounted):
    """守るもの: 図中の参照が無い展開接続図では「関連図面はありません」と出る。"""
    at = run_app([('EE6892-405-01A.dxf', dxf_bytes(tmp_path, ['W No.M39001']))], [])
    run_check_click(at)
    open_section(at)
    assert any('関連図面（図中の参照' in c.value and 'ありません' in c.value for c in at.caption)
