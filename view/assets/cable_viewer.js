// 図面の viewer（表示専用）。ケーブル番号を含む文字を、元の文字の色を変えて赤で示す。
// pan/zoom・全体表示・全画面・別ウィンドウは DXF-extract-labels の label_viewer.js から複製（共有しない）。
export default function(component) {
  const { data, parentElement } = component;
  const NS = 'http://www.w3.org/2000/svg';
  let S = parentElement.__cv;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  if (!S) {
    S = parentElement.__cv = { sig: null, vb: null, vb0: null, drag: null };
    const wrap = parentElement.querySelector('#cv-wrap');
    wrap.innerHTML =
      '<div class="lv-side" id="cv-side"></div>' +
      '<div class="cv-stage"><svg class="cv-cv" id="cv-cv" xmlns="' + NS + '" preserveAspectRatio="xMidYMid meet">' +
      '<style id="cv-hl"></style><rect id="cv-bg" fill="#000"/><g id="cv-base"></g></svg>' +
      '<div class="cv-note">線幅は拡大しても一定です</div></div>';
    S.wrap = wrap;
    S.svg = parentElement.querySelector('#cv-cv');
    S.side = parentElement.querySelector('#cv-side');
    const svg = S.svg;
    S.apply = () => { svg.setAttribute('viewBox', S.vb.join(' ')); };
    const pt = (e) => {
      const r = svg.getBoundingClientRect();
      const s = Math.max(S.vb[2] / r.width, S.vb[3] / r.height);
      const offx = (r.width - S.vb[2] / s) / 2, offy = (r.height - S.vb[3] / s) / 2;
      return { x: S.vb[0] + (e.clientX - r.left - offx) * s, y: S.vb[1] + (e.clientY - r.top - offy) * s, s };
    };
    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const p = pt(e), k = e.deltaY < 0 ? 0.8 : 1.25;
      const nw = S.vb[2] * k, nh = S.vb[3] * k;
      if (nw > S.vb0[2] * 20 || nw < S.vb0[2] / 2000) return;
      S.vb = [p.x - (p.x - S.vb[0]) * k, p.y - (p.y - S.vb[1]) * k, nw, nh]; S.apply();
    }, { passive: false });
    svg.addEventListener('pointerdown', (e) => {
      S.drag = { x: e.clientX, y: e.clientY, vb: S.vb.slice(), s: pt(e).s };
      svg.setPointerCapture(e.pointerId); svg.classList.add('drag');
    });
    svg.addEventListener('pointermove', (e) => {
      if (!S.drag) return;
      S.vb = [S.drag.vb[0] - (e.clientX - S.drag.x) * S.drag.s, S.drag.vb[1] - (e.clientY - S.drag.y) * S.drag.s, S.drag.vb[2], S.drag.vb[3]];
      S.apply();
    });
    const end = () => { S.drag = null; svg.classList.remove('drag'); };
    svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
    svg.addEventListener('dblclick', () => { S.vb = S.vb0.slice(); S.apply(); });
    document.addEventListener('fullscreenchange', () => { if (S.render) S.render(); });
  }

  S.data = data;
  // 外枠の高さを明示する（v2 部品の中では親の高さが確定せず、100% が効かないため）
  if (data.height) S.wrap.style.height = data.height + 'px';

  // 別ウィンドウ（閲覧専用）: 押した時点の図面を複製して新しい窓に書き込む。ズームは全体表示に戻る
  const openPopup = (note) => {
    const tpl = parentElement.querySelector('#cv-popup');
    const w = window.open('', 'cvPopup' + Date.now(), 'width=1280,height=860');
    if (!w) { note.textContent = 'ポップアップがブロックされました。ブラウザーで許可してください'; return; }
    const cv = S.svg.cloneNode(true);
    cv.removeAttribute('style'); cv.setAttribute('class', 'pcv pcv-lv'); cv.setAttribute('data-popup-cv', '');
    cv.setAttribute('viewBox', S.vb0.join(' '));
    const html = '<!doctype html><html><head><meta charset="utf-8"><title>図面</title><style>' +
      tpl.content.querySelector('style').textContent + '</style></head><body><div class="pwrap"><div class="pside">' +
      (S.legendHtml || '') + '<h4>操作</h4><button id="fit">全体表示</button>' +
      '<div style="color:#888;margin-top:6px;line-height:1.5">ホイール: 拡大縮小<br>ドラッグ: 移動<br>ダブルクリック: 全体表示</div>' +
      '</div><div class="pstage">' + cv.outerHTML + '<div class="pnote">線幅は拡大しても一定です</div></div></div><script>' +
      tpl.content.querySelector('script').textContent + '<\/script></body></html>';
    try { w.document.open(); w.document.write(html); w.document.close(); }
    catch (err) { w.close(); note.textContent = 'この環境では別ウィンドウを開けません'; }
  };

  const render = () => {
    const d = S.data;
    if (d.empty) { S.wrap.innerHTML = '<div style="padding:16px;color:#aaa">描画対象のエンティティがありません</div>'; return; }
    if (S.sig !== d.sig) {
      S.sig = d.sig;
      S.vb0 = [0, 0, d.viewBox[0], d.viewBox[1]]; S.vb = S.vb0.slice();
      S.svg.querySelector('#cv-bg').setAttribute('width', d.viewBox[0]);
      S.svg.querySelector('#cv-bg').setAttribute('height', d.viewBox[1]);
      S.svg.querySelector('#cv-base').innerHTML = d.svg;
    }
    // 元の文字そのものの色を変える（SVG の文字は1件ごとの CSS クラスを持つ）
    const rules = [];
    d.items.forEach((c) => {
      rules.push('#cv-cv .' + c[0] + '{' + c[1] + ':' + d.color + ';' +
        (c[1] === 'fill' ? 'stroke:' + d.color + ';stroke-width:0.6px;vector-effect:non-scaling-stroke' : '') + '}');
    });
    S.svg.querySelector('#cv-hl').textContent = rules.join('\n');
    S.apply();

    S.legendHtml = '<h4>' + esc(d.fname || '図面') + '</h4><div class="lv-row"><span class="lv-sw" style="background:' + d.color +
      '"></span><span>ケーブル番号</span><span class="lv-cnt">' + d.count + ' 件</span></div>';
    let h = S.legendHtml +
      '<h4>操作</h4><button data-act="fit">全体表示</button><button data-act="fs">' +
      (document.fullscreenElement ? '全画面を終了' : '全画面') + '</button><button data-act="pop">別ウィンドウ</button><div id="cv-fsnote" style="color:#e8a"></div>' +
      '<div style="color:#888;margin-top:6px;line-height:1.5">ホイール: 拡大縮小<br>ドラッグ: 移動<br>ダブルクリック: 全体表示</div>' +
      (d.hasFont ? '' : '<div class="lv-warn" style="margin-top:8px">日本語フォントが見つからないため、日本語の文字は表示されません。</div>');
    S.side.innerHTML = h;
    S.side.querySelectorAll('button[data-act]').forEach((b) => b.addEventListener('click', () => {
      const act = b.getAttribute('data-act');
      if (act === 'fit') { S.vb = S.vb0.slice(); S.apply(); return; }
      const note = S.side.querySelector('#cv-fsnote'); note.textContent = '';
      if (act === 'pop') { openPopup(note); return; }
      if (document.fullscreenElement) { document.exitFullscreen(); return; }
      const p = S.wrap.requestFullscreen ? S.wrap.requestFullscreen() : null;
      if (!p) { note.textContent = 'この環境では全画面にできません'; return; }
      p.catch(() => { note.textContent = 'この環境では全画面にできません'; });
    }));
  };
  S.render = render;
  render();
}
