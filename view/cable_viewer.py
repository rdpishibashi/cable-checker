"""図面の viewer（streamlit 依存のビュー層）。ケーブル番号を含む文字を赤字で示す表示専用の部品。

別ウィンドウ・全画面で表示できる（部品の JS が持つ）。描画（`model/cable_preview.render_base_svg()`）は
`st.cache_data` で、同じ DXF なら1回だけ。部品の HTML/CSS/JS は `view/assets/cable_viewer.{html,css,js}`
（別ウィンドウ用は `viewer_popup.*`）。
"""
from functools import lru_cache
from pathlib import Path

import streamlit as st

from model import cable_preview
from view import viewer_popup

CABLE_VIEWER_HEIGHT_PX = 720
PREPARING_TEXT = "図面を表示する準備をしています…（大きな図面は数秒〜数十秒かかることがあります）"
ASSET_DIR = Path(__file__).resolve().parent / 'assets'
_ASSET_FILES = {'HTML': 'cable_viewer.html', 'CSS': 'cable_viewer.css', 'JS': 'cable_viewer.js'}


@lru_cache(maxsize=None)
def _read_asset(name):
    return (ASSET_DIR / _ASSET_FILES[name]).read_text(encoding='utf-8')


@st.cache_data(show_spinner=False, max_entries=20)
def render_base(dxf_bytes: bytes, version: int = cable_preview.BASE_RENDER_VERSION):
    """図面全体の白 SVG（DXF バイト列と `BaseRender` の版をキーにキャッシュ）。"""
    return cable_preview.render_base_svg(dxf_bytes)


# 部品の定義（HTML/CSS/JS）が変わったときだけ登録し直す（同名の再登録は警告になる）
@st.cache_resource(show_spinner=False)
def _get_cable_viewer(definition_sig):
    return st.components.v2.component(
        "cable_viewer",
        html=viewer_popup.definition_html(_read_asset('HTML'), 'cv'),
        css=_read_asset('CSS'),
        js=_read_asset('JS'),
    )


def mount_viewer(**kwargs):
    """viewer 部品を描く。テストは、この関数を差し替えて渡された data を調べる。"""
    html = viewer_popup.definition_html(_read_asset('HTML'), 'cv')
    sig = hash((html, _read_asset('CSS'), _read_asset('JS')))
    return _get_cable_viewer(sig)(**kwargs)


def render_cable_viewer(fname, dxf_bytes, key):
    """図面 `dxf_bytes` を描き、ケーブル番号を含む文字を赤字にして表示する。
    読めない・描けない図面は警告だけを出す（ほかの図面の表示には影響しない）。"""
    try:
        with st.spinner(PREPARING_TEXT):
            base = render_base(dxf_bytes)
            data = cable_preview.build_viewer_data(base, fname)
        data['height'] = CABLE_VIEWER_HEIGHT_PX
        mount_viewer(key=key, data=data, height=CABLE_VIEWER_HEIGHT_PX)
    except Exception as e:
        st.warning(f"{fname} を表示できませんでした: {e}")
