"""model/cable_focus.py と、マークの色づけ（cable_preview.apply_marks / build_viewer_data）のテスト（合成の結果）。

守るもの: 選んだケーブルの関連図面の並び（選んだ展開接続図 → 一覧表 → 線番設定表 → 製作図 → 別の展開接続図）と、強調の種類
（照合の記号＝判定の色、照合に使っていない関連する文字＝関連、一覧表に無いケーブル＝×）、重いほう優先、見出しの件数、
図中の参照の一致（405・393 の両方、改訂記号違い）。
"""
import io

import ezdxf

from model import cable_focus, cable_preview


def mk(drawing, x, text, symbol, item='項目'):
    return {'drawing': drawing, 'anchor': (x, 0.0), 'text': text, 'symbol': symbol, 'item': item}


def synthetic_result(extra_rows=()):
    list_d = {'drawing_number': 'L1', 'set': '393', 'wire_rows': [], 'schematic': None,
              'list_rows': [{'行': 3, 'ケーブル番号': 'M1', '_refs': {'ケーブル番号': [('M1', (1.0, 0.0))], 'ケーブル備考': [('備考', (2.0, 0.0))]}}]}
    wire_d = {'drawing_number': 'W1', 'set': '393', 'list_rows': [], 'schematic': None,
              'wire_rows': [{'表': 1, '行': 5, 'ケーブル番号': 'M1', '_refs': {'ケーブル番号': [('M1', (1.0, 0.0))], '線番1': [('S1', (2.0, 0.0))]}},
                            {'表': 1, '行': 6, 'ケーブル番号': 'M1', '_refs': {'ケーブル番号': [('M1', (1.0, 5.0))], '線番1': [('S2', (2.0, 5.0))]}}]}
    fab_d = {'drawing_number': 'F1', 'set': '393', 'list_rows': [], 'wire_rows': [], 'schematic': None}
    sch = {'occurrences': [
        {'cable': 'M1', '_label_ref': (1.0, 0.0), 'wires': [{'labels': ['(BK)'], '_label_refs': [(2.0, 0.0)],
                                                          'ends': {'-': {'name': 'CN1', 'pin': '1', 'kind': 'box', 'regions': ['RACK'],
                                                                         '_refs': {'name': (3.0, 0.0), 'pin': (4.0, 0.0), 'regions': {'RACK': (5.0, 0.0)}}}}}]},
        {'cable': 'M1', '_label_ref': (1.0, 50.0), 'wires': []}]}
    sch_d = {'drawing_number': 'S1', 'set': '405', 'list_rows': [], 'wire_rows': [], 'schematic': sch}
    row = {'ケーブル番号': 'M1', '一覧表の図番': 'L1', '一覧表の行': 3, 'cells': {}, 'links': {},
           'marks': [mk('L1', 1.0, 'M1', '○'), mk('W1', 2.0, 'S1', '△'), mk('F1', 7.0, '(WH)', '×'), mk('S1', 2.0, '(BK)', '○'),
                     mk('S1', 3.0, 'CN1', '×'), mk('S1', 3.0, 'CN1', '○')],
           'focus': {'list': 'L1', 'row': 3, 'fabs': ['F1'], 'wires': ['W1'], 'mine': [('W1', 1, 5), ('W1', 1, 6)], 'sel': [('W1', 1, 5)],
                     'schematic': 'S1', 'occ': 0}}
    return {'drawings': [list_d, wire_d, fab_d, sch_d], 'rows': [row, *extra_rows],
            'extracts': {'405': [{'drawing_number': 'S1', 'cables': [{'cable': 'M2'}, {'cable': 'M1'}, {'cable': 'M1'}]}], '393': []}}


def test_cables_in_lists_each_cable_once_in_order():
    """守るもの: 選択欄の選択肢は、その展開接続図に出るケーブル番号を重複なし・昇順で（1つの番号＝1つの選択肢）。"""
    assert cable_focus.cables_in(synthetic_result(), 'S1') == ['M1', 'M2'] and cable_focus.cables_in(synthetic_result(), 'nothing') == []


def test_focus_orders_sections_and_marks_related_texts_purple():
    """守るもの: 並びは 選んだ展開接続図 → 一覧表 → 線番設定表 → 製作図。照合した記号は判定の色、照合に使っていない関連する文字
    （一覧表の他の列・線番設定表の選ばなかった行・展開接続図の使わなかった出現）は「関連」。"""
    secs = cable_focus.focus(synthetic_result(), 'M1', 'S1')
    assert [(s['role'], s['drawing']) for s in secs] == [('展開接続図', 'S1'), ('ケーブル一覧表', 'L1'), ('線番設定表', 'W1'), ('ケーブル製作図', 'F1')]
    by = {s['drawing']: s for s in secs}
    syms = lambda d: {(m['text'], m['symbol']) for m in by[d]['marks']}  # noqa: E731
    assert ('M1', '○') in syms('L1') and ('備考', '関連') in syms('L1')
    assert ('S1', '△') in syms('W1') and ('S2', '関連') in syms('W1') and ('M1', '関連') in syms('W1')   # 選ばなかった行 6 は関連
    assert syms('F1') == {('(WH)', '×')}
    assert ('M1', '関連') in syms('S1') and ('CN1', '×') in syms('S1')        # 使わなかった出現2 のラベルは関連


def test_counts_take_the_heaviest_symbol_per_text_and_label_has_all_four_symbols():
    """守るもの: 同じ文字に複数の記号が付いたら重いほう（× ＞ ？ ＞ △ ＞ ○ ＞ － ＞ 関連）。見出しの件数は ×？△○（0 も出す。関連は出さない）。"""
    marks = [mk('a', 1, 'x', '○'), mk('a', 1, 'x', '×'), mk('a', 2, 'y', '関連'), mk('a', 3, 'z', '△'), mk('a', 3, 'z', '関連')]
    counts = cable_focus.symbol_counts(marks)
    assert counts == {'×': 1, '関連': 1, '△': 1}
    assert cable_focus.counts_label(counts) == '×1 ？0 △1 ○0'


def test_cable_missing_from_the_list_is_marked_cross_on_its_cable_number():
    """守るもの: 一覧表に無いケーブル（線番設定表・展開接続図にだけある）は、ケーブル番号の文字を×（図面の対応｜一覧表）、ほかは関連にする。"""
    result = synthetic_result()
    result['rows'] = [{'ケーブル番号': 'M1', '一覧表の図番': '', '一覧表の行': '', 'cells': {}, 'links': {}, 'marks': [], 'focus': None}]
    secs = cable_focus.focus(result, 'M1', 'S1')
    by = {s['drawing']: s for s in secs}
    assert ('M1', '×') in {(m['text'], m['symbol']) for m in by['W1']['marks']} and ('S1', '関連') in {(m['text'], m['symbol']) for m in by['W1']['marks']}
    assert 'L1' not in by and 'F1' not in by


def test_drawing_refs_match_uploaded_names_of_both_sets_with_optional_revision_letter():
    """守るもの: 図中の参照 `(-405-08)` は、アップロードした 405・393 の両方から、頭の英字2字・数字4桁と末尾の改訂記号が任意の規則で一致する。
    同じパターンは1件にまとめ、図中の文字を並べる。一致が無ければ matches は空。"""
    refs = cable_focus.drawing_refs(['to POWER LINE (-405-08)', 'XRLS1 (-405-08)', 'DO21 (-300-01 1/2)', 'NOTE (-405-01,02)'],
                                    ['EE6892-405-08A', 'EE6892-405-08B', 'EE6892-405-01', 'EE6892-634-01C', 'EE6892-405-088'])
    by = {r['pattern']: r for r in refs}
    assert [r['pattern'] for r in refs] == ['-405-08', '-300-01', '-405-01', '-405-02']
    assert by['-405-08']['texts'] == ['to POWER LINE (-405-08)', 'XRLS1 (-405-08)']
    assert by['-405-08']['matches'] == ['EE6892-405-08A', 'EE6892-405-08B'] and by['-405-01']['matches'] == ['EE6892-405-01']
    assert by['-300-01']['matches'] == [] and by['-405-02']['matches'] == []


def _base(texts):
    doc = ezdxf.new('R2010')
    for i, t in enumerate(texts):
        doc.modelspace().add_text(t, dxfattribs={'insert': (10, 10 + i * 10), 'height': 3})
    buf = io.StringIO()
    doc.write(buf)
    return cable_preview.render_base_svg(buf.getvalue().encode('utf-8'))


def test_viewer_data_colors_marked_texts_by_symbol_and_leaves_others_white():
    """守るもの: マークがあるときは、記号の色（○緑・△黄・×赤・？青・－と関連は紫）の文字だけ色が変わり、凡例は記号ごとの件数。
    「関連」のマークは viewer に出さない（Excel に無い区分）。マークがない（None）ときは今までどおりケーブル番号が赤。"""
    base = _base(['W No.M39001', 'AAA', 'BBB', 'W No.M39002'])
    marks = [{'anchor': (10, 40), 'text': 'W No.M39002', 'symbol': '関連'}, {'anchor': (10, 20), 'text': 'AAA', 'symbol': '○'}, {'anchor': (10, 30), 'text': 'BBB', 'symbol': '×'},
             {'anchor': (10, 20), 'text': 'AAA', 'symbol': '△'}, {'anchor': (99, 99), 'text': 'ZZZ', 'symbol': '？'}]
    data = cable_preview.build_viewer_data(base, 'f.dxf', marks, cable='M39001')
    assert {c[2] for c in data['items']} == {'#ffd400', '#ff3030'}          # AAA は同じ文字の ○ と △ のうち重い △（黄）
    assert data['counts'] == {'△': 1, '×': 1} and data['missing'] == 1 and data['marked'] is True     # 「関連」のマークは表示しない
    assert [(l[0], l[2]) for l in data['legend']] == [('× 不整合', 1), ('△ 一致（根拠が弱い）', 1)]   # M39001・M39002 は白のまま
    plain = cable_preview.build_viewer_data(base, 'f.dxf')
    assert plain['marked'] is False and plain['count'] == 2 and {c[2] for c in plain['items']} == {cable_preview.CABLE_COLOR}
    assert plain['legend'] == [['ケーブル番号', cable_preview.CABLE_COLOR, 2]]
