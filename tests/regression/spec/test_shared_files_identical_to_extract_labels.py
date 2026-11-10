"""
このテストが守るもの: DXF処理の共有ファイルが DXF-extract-labels（primary）とバイト一致であること。

受入条件（Given/When/Then）:
  Given Cable-checker と DXF-extract-labels が同じ親フォルダにチェックアウトされている
  When  下記 SHARED_FILES を比較する
  Then  全ファイルの MD5 が一致する

DXF-extract-labels のフォルダ自体が隣に無い環境（Streamlit Cloud・単独クローン）でだけ skip する。
フォルダがあるのに共有ファイルが無い（正本の改名・削除）ときは skip せず失敗させる。
共有ファイルを変更するときは必ず DXF-extract-labels 側で行い、そのままコピーすること
（Tools/CLAUDE.md「Shared DXF Processing Library Pattern」）。
"""
import hashlib
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
PRIMARY = os.path.abspath(os.path.join(PROJECT, '..', 'DXF-extract-labels'))

SHARED_FILES = (
    'model/extract_labels.py',
    'model/common_utils.py',
)


def _md5(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


@pytest.mark.parametrize('rel_path', SHARED_FILES)
def test_shared_file_identical_to_primary(rel_path):
    if not os.path.isdir(PRIMARY):
        pytest.skip(f'DXF-extract-labels が隣にありません: {PRIMARY}')
    primary = os.path.join(PRIMARY, rel_path)
    assert os.path.isfile(primary), f'正本に {rel_path} がありません（改名・削除された可能性）'
    assert _md5(os.path.join(PROJECT, rel_path)) == _md5(primary), (
        f'{rel_path} が DXF-extract-labels とバイト一致しません。'
        'DXF-extract-labels 側から再コピーしてください。')
