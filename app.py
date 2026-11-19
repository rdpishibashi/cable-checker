"""Cable-checker: 展開接続図（405）とケーブル製作図（393）の DXF から、ケーブル番号の抽出・比較と、
ケーブル一覧表・線番設定表・ケーブル製作図・展開接続図の整合性チェックを1回で行う（View 層）。

処理は model/ に置き、この画面は入力の受け取り・表示・ダウンロードだけを担う。
画面は1つ（タブなし）。405 と 393 のアップローダーを左右に並べ、「整合性をチェック」で結果（分析結果プレビュー・Excel）を出す。
"""
import os

import streamlit as st

from model.cable_extractor import dedupe_by_name, is_dwg_filename, is_target_filename
from model.consistency import KIND_FAB, KIND_LIST, KIND_SCHEMATIC, KIND_WIRE, SET_393, SET_405, run_check
from model.consistency_output import EXCEL_FILENAME as CHECK_FILENAME
from model.consistency_output import build_workbook as build_check_workbook
from model.consistency_output import preview_sheets as check_preview_sheets
from view import cable_viewer
from view.preview_layout import column_widths, sheet_select_width

st.set_page_config(page_title="Cable-checker", page_icon="🔌", layout="wide")

CHECK_RESULT_KEY = 'check_result'  # {'sig': 入力の署名（405 の署名, 393 の署名）, 'result': run_check() の結果, 'errors': [...], 'xlsx': bytes}
SET_LABELS = {SET_405: '展開接続図（405）', SET_393: 'ケーブル製作図（393）'}
SPINNER_TEXT = "図面を読み取っています…（図面の数によっては数十秒かかります）"
SECTION_STATUS_TEXT = "結果をまとめています…"


def _target_files(uploaded):
    """アップロードから処理対象（図番形式の .dxf・同名は先の1件）を選ぶ。

    Returns: (対象の UploadedFile リスト, 無視した件数, 同名でスキップした名前, DWG の名前)
    DWG（図番形式の .dwg）は DXF でないので処理できない。無視した件数には含めない。
    """
    dwg = [f.name for f in uploaded if is_dwg_filename(f.name)]
    targets = [f for f in uploaded if is_target_filename(f.name)]
    keep, skipped = dedupe_by_name([f.name for f in targets])
    ignored = len(uploaded) - len(targets) - len(dwg)
    return [targets[i] for i in keep], ignored, skipped, dwg


def _stem(name):
    return os.path.splitext(name)[0]


def _drop_names_of(files, other):
    """`files` のうち、`other` と同じ名前（拡張子・大小文字無視）のものを除く。Returns: (残り, 除いた名前)"""
    taken = {_stem(f.name).lower() for f in other}
    kept = [f for f in files if _stem(f.name).lower() not in taken]
    return kept, [f.name for f in files if _stem(f.name).lower() in taken]


def _signature(files):
    return tuple((f.name, f.size) for f in files)


def _render_header():
    st.title("Cable-checker - 展開接続図・ケーブル製作図の整合性チェック")
    st.write(
        "展開接続図（405）とケーブル製作図（393）のDXFファイルをアップロードし、ケーブル番号の抽出・比較と、"
        "ケーブル一覧表・線番設定表・ケーブル製作図・展開接続図の整合性チェックを行います。"
    )
    with st.expander("ℹ️ プログラム説明", expanded=False):
        st.info("\n".join([
            "**使用手順：**",
            "1. 展開接続図（405）とケーブル製作図（393）のDXFファイルを、それぞれのアップロード欄にアップロードします"
            "（複数可・フォルダ可。図番の形式でないファイルは無視されます。DWGファイルは読み込めないので、"
            "DXFに変換してください。両方に同じ名前のファイルがあるときは、展開接続図の側を使います）。"
            "どちらか片方だけでも実行できます",
            "2. 「整合性をチェック」ボタンを押し、結果を「分析結果プレビュー」で確認して、Excelをダウンロードします"
            "（cable_consistency.xlsx）",
            "",
            "**チェックの内容：** ケーブル一覧表の1行ごとに、図面の対応・端末（ユニット・モジュール・機器符号・部品種別）・"
            "ケーブル（種類・芯数）・芯線（線番・PIN・色）を照合します。393 の図面（ケーブル一覧表・線番設定表・ケーブル製作図）は"
            "内容から見分け、一覧表の「接続図図面番号」が指す展開接続図を読んで照合します。"
            "各項目の判定は ○一致・△一致（根拠が弱い）・×不整合・？読めない・－対象外 の記号で、根拠は「照合明細」シートに出ます。",
            "",
            "**ケーブル番号：** `M` または `C`＋数字2桁＋英数字3文字（末尾に英字1文字を許す。例: M39001、M06004B、M65A61、C10021）。"
            "全角は半角にそろえます。「W No.」に続く番号は、番号の後ろの文字を「備考」に入れます。同じ図面内の重複は1つにまとめます。"
            "展開接続図と製作図のケーブル番号は「All Diff」シート（と、製作図のタイトル別の「…Diff」シート）で突き合わせます。",
            "",
            "**Excelファイルの内容：** Summary／整合性チェック／照合明細／芯線の照合／All Diff・各グループの Diff／"
            "図面一覧／ケーブル抽出／ケーブル一覧表／線番設定表／製作図の接続／展開接続図／パターンの解決／製作図の文字",
        ]))


def _render_uploader(drawing_set, label):
    """1つのセットのアップローダーと、受け取った内容の件数・警告。Returns: 処理対象の UploadedFile リスト。"""
    uploaded = st.file_uploader(f"{label}（複数可，フォルダ可）", accept_multiple_files=True,
                                key=f'uploader_{drawing_set}')
    files, ignored, skipped, dwg = _target_files(uploaded or [])
    if uploaded:
        st.caption(f"処理対象 {len(files)} 件（図番形式でないファイル {ignored} 件は無視）")
    for name in skipped:
        st.warning(f"{name} は同名のファイルが先にあるためスキップしました。")
    for name in dwg:
        st.warning(f"{name} はDWGファイルのため読み込めません。DXFに変換してアップロードしてください。")
    return files


def _run_check(files_405, files_393, sig):
    """2つのセットを読んで照合・比較し、結果と Excel をセッションに保存する。進捗は円形の spinner と、段階・ファイル名の caption。"""
    with st.spinner(SPINNER_TEXT):
        status = st.empty()

        def on_progress(done, total, name, phase):
            status.caption(f"{phase} ({done + 1}/{total}): {name}")

        result, errors = run_check([(f.name, f.getvalue()) for f in files_405],
                                   [(f.name, f.getvalue()) for f in files_393], progress=on_progress)
        status.caption(SECTION_STATUS_TEXT)
        xlsx = build_check_workbook(result)
        status.empty()
    st.session_state[CHECK_RESULT_KEY] = {'sig': sig, 'result': result, 'errors': errors, 'xlsx': xlsx}


def _drawing_options(files_405, files_393):
    """プレビューの「図面を表示」で選べる図面 {表示名: UploadedFile}（展開接続図 → 製作図の順、それぞれ図番順）。"""
    options = {}
    for drawing_set, files in ((SET_405, files_405), (SET_393, files_393)):
        for f in sorted(files, key=lambda f: _stem(f.name)):
            options[f"{_stem(f.name)}（{drawing_set}）"] = f
    return options


def _render_check_preview(result, files_405, files_393):
    """出力する Excel の中身のプレビュー（シートの組み立ては `preview_sheets()`。ダウンロードする Excel と同じ関数）。
    シートの選択のほかに、アップロードした図面を選んで viewer で見る「図面を表示」トグルを持つ（ケーブル番号は赤字）。
    シート・トグル・図面を操作したら、枠を開いたままにする（on_change。無条件に開くと初回から開いてしまう）。"""
    sheets = dict(check_preview_sheets(result))
    names = list(sheets)
    key, open_key = 'preview_sheet_check', 'preview_expanded_check'
    if st.session_state.get(key) not in names:
        st.session_state.pop(key, None)  # 入力を差し替えて前回のシートが無いときは先頭（Summary）に戻す

    def mark_expanded():
        st.session_state[open_key] = True

    with st.expander("分析結果プレビュー", expanded=st.session_state.get(open_key, False)):
        sheet = st.selectbox("シートを選択", names, key=key, on_change=mark_expanded, width=sheet_select_width(names))
        options = _drawing_options(files_405, files_393)
        if st.session_state.get('preview_drawing_check') not in options:
            st.session_state.pop('preview_drawing_check', None)
        if options and st.toggle("図面を表示", value=False, key='preview_viewer_check', on_change=mark_expanded):
            label = st.selectbox("図面を選択", list(options), key='preview_drawing_check', on_change=mark_expanded,
                                 width=sheet_select_width(list(options)))
            uf = options[label]
            cable_viewer.render_cable_viewer(uf.name, uf.getvalue(), key='preview_viewer_mount_check')
        widths, table_width = column_widths(sheets[sheet])
        column_config = {col: st.column_config.Column(width=px) for col, px in widths.items()}
        st.dataframe(sheets[sheet], width=table_width, hide_index=True, column_config=column_config,
                     key='preview_table_check')


def _render_check_result(state, files_405, files_393):
    for name, message in state['errors']:
        st.error(f"{name} を処理できませんでした: {message}")
    result = state['result']
    for d in result['drawings']:
        if d['no_text']:
            st.warning(f"{d['drawing_number']} は文字が無いため読み取れません（文字が線に分解されている可能性があります。"
                       "元のDWGから文字を残してDXFに変換し直してください）。")
        sch = d.get('schematic')
        if sch and sch['region_error']:
            st.warning(f"{d['drawing_number']} は領域を検出できませんでした。ユニットは機器・近傍のラベルだけで判定しています"
                       f"（{sch['region_error']}）。")
    if result['unused']:
        st.info(f"照合に使っていない図面 {len(result['unused'])} 件: {'、'.join(result['unused'])}"
                "（ケーブル一覧表・線番設定表・ケーブル製作図のどれにも当たらず、一覧表の図面パターン〈製作図・接続図〉も指していません）")
    st.subheader("チェック結果")
    st.caption(_summary_caption(result))
    _render_check_preview(result, files_405, files_393)
    st.download_button(
        "分析結果ダウンロード", data=state['xlsx'], file_name=CHECK_FILENAME,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", key='download_check',
    )


def _summary_caption(result):
    """結果の件数の要約（図面・ケーブル番号の比較・一覧表の行ごとの照合）。"""
    drawings, rows = result['drawings'], result['rows']
    count = lambda kind: sum(kind in d['kinds'] for d in drawings)  # noqa: E731
    compare = result['compare']['rows']
    both = sum(bool(r['drawings_405'] and r['drawings_393']) for r in compare)
    only_405 = sum(bool(r['drawings_405'] and not r['drawings_393']) for r in compare)
    return (f"展開接続図 {sum(d['set'] == SET_405 for d in drawings)} 図面・ケーブル製作図 {sum(d['set'] == SET_393 for d in drawings)} 図面"
            f"（一覧表 {count(KIND_LIST)}・線番設定表 {count(KIND_WIRE)}・製作図 {count(KIND_FAB)}・"
            f"照合した展開接続図 {count(KIND_SCHEMATIC)}）／"
            f"ケーブル番号 {len(compare)} 件（両方 {both}・展開接続図だけ {only_405}・ケーブル製作図だけ {len(compare) - both - only_405}）／"
            f"一覧表の行 {len(rows)} 行（不整合 {sum(r['不整合数'] > 0 for r in rows)} 行・要確認 {sum(r['要確認数'] > 0 for r in rows)} 行）")


def main():
    _render_header()
    col_405, col_393 = st.columns(2)
    with col_405:
        files_405 = _render_uploader(SET_405, SET_LABELS[SET_405])
    with col_393:
        files_393 = _render_uploader(SET_393, SET_LABELS[SET_393])
        files_393, same_name = _drop_names_of(files_393, files_405)
        for name in same_name:
            st.warning(f"{name} は展開接続図（405）に同じ名前があるため、ケーブル製作図（393）の側をスキップしました。")

    if not files_405 and not files_393:
        st.info("図番形式（例: EE6892-610-01B.dxf）のDXFファイルを、展開接続図・ケーブル製作図のどちらか（または両方）にアップロードしてください。")
        return

    sig = (_signature(files_405), _signature(files_393))
    state = st.session_state.get(CHECK_RESULT_KEY)
    done = bool(state and state['sig'] == sig)
    # 今の入力の結果が出ているときは、再実行できるが「次の操作」ではないので白（結果を出したら青はダウンロード）
    if st.button("整合性をチェック", type="secondary" if done else "primary", key='run_check'):
        _run_check(files_405, files_393, sig)
        st.rerun()
    if done:
        _render_check_result(state, files_405, files_393)


main()
