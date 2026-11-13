"""Cable-checker: 展開接続図・ケーブル製作図の DXF からケーブル番号を抽出して Excel に出力し、
両者の Excel を比較する（View 層）。

処理は model/ に置き、この画面は入力の受け取り・表示・ダウンロードだけを担う。
入力の種類（405 展開接続図／393 ケーブル製作図）ごとにタブを分け、ウィジェットのキー・
セッションのキーも種類ごとに別にする（片方の操作が他方の結果に影響しない）。
"""
import streamlit as st

from model.cable_compare import compare_cables, compare_groups, read_cable_list, read_drawing_titles
from model.cable_extractor import (
    dedupe_by_name, extract_from_uploads, is_dwg_filename, is_target_filename,
)
from model.compare_output import (
    ALL_SHEET, COMPARE_FILENAME, build_compare_workbook, compare_frame, count_rows, group_sheet_names,
)
from model.excel_output import (
    EXCEL_FILENAMES, build_workbook, drawing_frame, summary_frame,
)

st.set_page_config(page_title="Cable-checker", page_icon="🔌", layout="wide")

# (種類ID, タブ名, 図面の呼び名, 説明に足す一文)
KINDS = [
    ('405', '展開接続図（405）', '展開接続図',
     "ケーブル番号は図面内の「W No.M39001」形式のラベルから拾います。"),
    ('393', 'ケーブル製作図（393）', 'ケーブル製作図',
     "ケーブル番号は、ケーブル一覧表・線番設定表のW No.列に書かれた番号から拾います。"
     "個々のケーブル製作図はW No.欄が空欄のため、ケーブル0件になります。"),
]


COMPARE_TAB = '比較（405と393）'
COMPARE_RESULT_KEY = 'compare_result'  # {'sig': 入力の署名, 'rows': [...], 'groups': [...], 'xlsx': bytes, 'names': (名前405, 名前393)}


def _result_key(kind):
    return f'cable_result_{kind}'  # {'sig': 入力の署名, 'results': [...], 'errors': [...], 'xlsx': bytes}


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


def _signature(files):
    return tuple((f.name, f.size) for f in files)


def _render_header():
    st.title("Cable-checker - ケーブル番号の抽出")
    st.write(
        "展開接続図またはケーブル製作図のDXFファイルから、図番ごとのケーブル番号"
        "（`M39001` など）を抽出し、Excelに出力します。"
    )
    with st.expander("ℹ️ プログラム説明", expanded=False):
        st.info("\n".join([
            "図番はファイル名から、タイトル・サブタイトルは図面から取得します。",
            "ケーブル番号は `M`＋数字2桁＋英数字3文字（末尾に英字1文字を許す。"
            "例: M39001、M06004B、M65A61）です。全角は半角にそろえます。"
            "「W No.」に続く番号は、番号の後ろの文字を「備考」に入れます。"
            "同じ図面内の重複は1つにまとめます。",
            "",
            "**使用手順：**",
            "1. 入力する図面の種類のタブを選び、DXFファイルをアップロードします（複数可・フォルダ可。"
            "図番の形式でないファイルは無視されます。DWGファイルは読み込めないので、"
            "DXFに変換してください）",
            "2. 「ケーブルを抽出」ボタンを押し、結果をExcelでダウンロードします"
            "（展開接続図は cable_list_405.xlsx、ケーブル製作図は cable_list_393.xlsx）",
            "",
            "3. 「比較」タブでは、1・2で出力した2つのExcelファイル（展開接続図とケーブル製作図）を"
            "アップロードし、ケーブル番号ごとに、そのケーブルがある図面の図番を並べて比較します"
            "（片方にしかないケーブルは、もう一方の図番欄が空白になります）",
            "",
            "**Excelファイルの内容：** Summary（図番・タイトル・サブタイトル・ケーブル数。"
            "ケーブル0件の図面も載せる）／ケーブル一覧（図番×ケーブル番号）／"
            "図番ごとのシート（ケーブルがある図面だけ）",
        ]))


def _run(kind, files):
    progress = st.progress(0.0, text="準備中...")

    def on_progress(done, total, name):
        progress.progress(done / total, text=f"抽出中 ({done + 1}/{total}): {name}")

    items = [(f.name, f.getvalue()) for f in files]
    results, errors = extract_from_uploads(items, progress=on_progress)
    progress.empty()
    st.session_state[_result_key(kind)] = {
        'sig': _signature(files),
        'results': results,
        'errors': errors,
        'xlsx': build_workbook(results),
    }


def _render_result(kind, state):
    for name, message in state['errors']:
        st.error(f"{name} を処理できませんでした: {message}")
    results = sorted(state['results'], key=lambda r: r['drawing_number'])
    total = sum(len(r['cables']) for r in results)
    st.subheader("抽出結果")
    st.caption(f"{len(results)} 図面・ケーブル {total} 件（図面内の重複は1件に集約）")
    st.dataframe(summary_frame(results), hide_index=True, key=f'summary_{kind}')
    st.download_button(
        "Excelをダウンロード", data=state['xlsx'], file_name=EXCEL_FILENAMES[kind],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", key=f'download_{kind}',
    )
    st.subheader("図番ごとのケーブル")
    for r in results:
        label = f"{r['drawing_number']}　{r['subtitle']}（{len(r['cables'])} 件）"
        with st.expander(label):
            if r['cables']:
                st.dataframe(drawing_frame(r), hide_index=True,
                             key=f"cables_{kind}_{r['drawing_number']}")
            else:
                st.write("ケーブルはありません。")


def _render_tab(kind, drawing_name, hint):
    st.write(hint)
    uploaded = st.file_uploader(
        f"{drawing_name}（複数可，フォルダ可）", accept_multiple_files=True,
        key=f'uploader_{kind}',
    )
    files, ignored, skipped, dwg = _target_files(uploaded or [])

    if uploaded:
        st.caption(f"処理対象 {len(files)} 件（図番形式でないファイル {ignored} 件は無視）")
    for name in skipped:
        st.warning(f"{name} は同名のファイルが先にあるためスキップしました。")
    for name in dwg:
        st.warning(f"{name} はDWGファイルのため読み込めません。DXFに変換してアップロードしてください。")

    if not files:
        st.info("図番形式（例: EE6892-405-08A.dxf）のDXFファイルをアップロードしてください。")
        return

    if st.button("ケーブルを抽出", type="primary", key=f'run_{kind}'):
        _run(kind, files)

    state = st.session_state.get(_result_key(kind))
    if state and state['sig'] == _signature(files):
        _render_result(kind, state)


def _compare_signature(f405, f393):
    return ((f405.name, f405.size), (f393.name, f393.size))


def _run_compare(f405, f393):
    """2つのExcelを読んで比較し、結果をセッションに保存する。読めなければ ValueError。"""
    map_405 = read_cable_list(f405, "展開接続図のExcel")
    map_393 = read_cable_list(f393, "ケーブル製作図のExcel")
    rows = compare_cables(map_405, map_393)
    groups = compare_groups(map_405, map_393, read_drawing_titles(f393, "ケーブル製作図のExcel"))
    st.session_state[COMPARE_RESULT_KEY] = {
        'sig': _compare_signature(f405, f393),
        'rows': rows,
        'groups': groups,
        'names': (f405.name, f393.name),
        'xlsx': build_compare_workbook(rows, f405.name, f393.name, groups),
    }


def _render_compare_result(state):
    rows = state['rows']
    counts = count_rows(rows)
    st.subheader("比較結果")
    st.caption(
        f"ケーブル {len(rows)} 件（両方にある {counts['both']} 件・"
        f"展開接続図だけ {counts['405only']} 件・ケーブル製作図だけ {counts['393only']} 件）"
    )
    groups = state['groups']
    sheet_names = [ALL_SHEET, *group_sheet_names([g['name'] for g in groups])]
    for i, (tab, sheet) in enumerate(zip(st.tabs(sheet_names), [rows, *[g['rows'] for g in groups]])):
        with tab:
            if i:
                st.caption(f"グループの図面 {len(groups[i - 1]['drawings'])} 件: "
                           + "、".join(groups[i - 1]['drawings']))
            st.dataframe(compare_frame(sheet), hide_index=True, key=f'compare_table_{i}')
    st.download_button(
        "Excelをダウンロード", data=state['xlsx'], file_name=COMPARE_FILENAME,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", key='download_compare',
    )


def _render_compare_tab():
    st.write("展開接続図から出力したExcelと、ケーブル製作図から出力したExcelを比較します。")
    f405 = st.file_uploader("展開接続図のExcel（cable_list_405.xlsx）", type=['xlsx'], key='compare_405')
    f393 = st.file_uploader("ケーブル製作図のExcel（cable_list_393.xlsx）", type=['xlsx'], key='compare_393')

    ready = f405 is not None and f393 is not None
    if not ready:
        st.info("展開接続図とケーブル製作図、両方のExcelファイルをアップロードしてください。")
    if st.button("比較", type="primary", key='run_compare', disabled=not ready):
        try:
            _run_compare(f405, f393)
        except ValueError as e:
            st.session_state.pop(COMPARE_RESULT_KEY, None)
            st.error(str(e))

    state = st.session_state.get(COMPARE_RESULT_KEY)
    if ready and state and state['sig'] == _compare_signature(f405, f393):
        _render_compare_result(state)


def main():
    _render_header()
    tabs = st.tabs([k[1] for k in KINDS] + [COMPARE_TAB])
    for tab, (kind, _, drawing_name, hint) in zip(tabs, KINDS):
        with tab:
            _render_tab(kind, drawing_name, hint)
    with tabs[-1]:
        _render_compare_tab()


main()
