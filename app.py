"""Cable-checker: 展開接続図の DXF からケーブル番号を抽出して Excel に出力する（View 層）。

処理は model/ に置き、この画面は入力の受け取り・表示・ダウンロードだけを担う。
"""
import streamlit as st

from model.cable_extractor import (
    dedupe_by_name, extract_from_uploads, is_target_filename,
)
from model.excel_output import (
    EXCEL_FILENAME, build_workbook, drawing_frame, summary_frame,
)

st.set_page_config(page_title="Cable-checker", page_icon="🔌", layout="wide")

RESULT_KEY = 'cable_result'  # {'sig': 入力の署名, 'results': [...], 'errors': [...], 'xlsx': bytes}


def _target_files(uploaded):
    """アップロードから処理対象（図番形式の .dxf・同名は先の1件）を選ぶ。

    Returns: (対象の UploadedFile リスト, 図番形式でないため除外した件数, 同名でスキップした名前)
    """
    targets = [f for f in uploaded if is_target_filename(f.name)]
    keep, skipped = dedupe_by_name([f.name for f in targets])
    return [targets[i] for i in keep], len(uploaded) - len(targets), skipped


def _signature(files):
    return tuple((f.name, f.size) for f in files)


def _render_header():
    st.title("Cable-checker - ケーブル番号の抽出")
    st.write(
        "展開接続図のDXFファイルから、図番ごとのケーブル番号（`W No.M39001` など）を抽出し、"
        "Excelに出力します。"
    )
    with st.expander("ℹ️ プログラム説明", expanded=False):
        st.info("\n".join([
            "図番はファイル名から、タイトル・サブタイトルは図面から取得します。",
            "ケーブル番号は「W No.」に続く `M`＋数字2桁＋英数字3文字（末尾に英字1文字を許す。"
            "例: M39001、M06004B、M65A61）のラベルです。全角は半角にそろえます。"
            "番号の後ろに文字があれば「備考」に入れます。同じ図面内の重複は1つにまとめます。",
            "",
            "**使用手順：**",
            "1. 展開接続図のDXFファイルをアップロードします（複数可・フォルダ可。"
            "図番の形式でないファイルは無視されます）",
            "2. 「ケーブルを抽出」ボタンを押し、結果をExcelでダウンロードします",
            "",
            "**Excelファイルの内容：** Summary（図番・タイトル・サブタイトル・ケーブル数）／"
            "ケーブル一覧（図番×ケーブル番号）／図番ごとのシート",
        ]))


def _run(files):
    progress = st.progress(0.0, text="準備中...")

    def on_progress(done, total, name):
        progress.progress(done / total, text=f"抽出中 ({done + 1}/{total}): {name}")

    items = [(f.name, f.getvalue()) for f in files]
    results, errors = extract_from_uploads(items, progress=on_progress)
    progress.empty()
    st.session_state[RESULT_KEY] = {
        'sig': _signature(files),
        'results': results,
        'errors': errors,
        'xlsx': build_workbook(results),
    }


def _render_result(state):
    for name, message in state['errors']:
        st.error(f"{name} を処理できませんでした: {message}")
    results = sorted(state['results'], key=lambda r: r['drawing_number'])
    total = sum(len(r['cables']) for r in results)
    st.subheader("抽出結果")
    st.caption(f"{len(results)} 図面・ケーブル {total} 件（図面内の重複は1件に集約）")
    st.dataframe(summary_frame(results), hide_index=True)
    st.download_button(
        "Excelをダウンロード", data=state['xlsx'], file_name=EXCEL_FILENAME,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
    st.subheader("図番ごとのケーブル")
    for r in results:
        label = f"{r['drawing_number']}　{r['subtitle']}（{len(r['cables'])} 件）"
        with st.expander(label):
            if r['cables']:
                st.dataframe(drawing_frame(r), hide_index=True)
            else:
                st.write("ケーブルはありません。")


def main():
    _render_header()
    st.subheader("DXFファイルのアップロード")
    uploaded = st.file_uploader("展開接続図（複数可，フォルダ可）", accept_multiple_files=True)
    files, ignored, skipped = _target_files(uploaded or [])

    if uploaded:
        st.caption(f"処理対象 {len(files)} 件（図番形式でないファイル {ignored} 件は無視）")
    for name in skipped:
        st.warning(f"{name} は同名のファイルが先にあるためスキップしました。")

    if not files:
        st.info("図番形式（例: EE6892-405-08A.dxf）のDXFファイルをアップロードしてください。")
        return

    if st.button("ケーブルを抽出", type="primary"):
        _run(files)

    state = st.session_state.get(RESULT_KEY)
    if state and state['sig'] == _signature(files):
        _render_result(state)


main()
