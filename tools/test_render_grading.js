#!/usr/bin/env node
/* Render-check season/index.html against the real data.js, in node.
 *
 * check_grade_coverage.py proves data.js is internally consistent. It says
 * nothing about whether the PAGE built from it works -- and this card's whole
 * cost was a rendering behaviour (badgeless frames excluded from the tier
 * filter), not a data shape. So extract the page's own functions and run them
 * over the real records.
 */
const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(REPO, 'season/index.html'), 'utf8');
const dataSrc = fs.readFileSync(path.join(REPO, 'season/data.js'), 'utf8');

let fails = 0;
function check(label, ok, detail) {
  if (!ok) fails++;
  console.log((ok ? 'ok  ' : 'FAIL') + '  ' + label.padEnd(58) + (detail || ''));
}

// ---- the page's script must parse at all ---------------------------------
const bodies = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]);
check('index.html contains an inline script', bodies.length > 0,
      bodies.length + ' block(s)');
let parsed = true;
for (const b of bodies) {
  try { new Function(b); } catch (e) { parsed = false; check('inline script parses', false, e.message); }
}
if (parsed) check('every inline script block parses as JS', true);

// ---- pull the grading predicates out and exercise them -------------------
const src = bodies.join('\n');
const need = ['isGraded', 'gameIsGraded', 'badgeHTML', 'badgeClass', 'badgeText'];
const snip = need.map(fn => {
  const re = new RegExp('function ' + fn + '\\s*\\([^)]*\\)\\s*\\{');
  const m = re.exec(src);
  if (!m) { check('found function ' + fn, false); return ''; }
  // brace-match from the opening {
  let i = m.index + m[0].length - 1, depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (!depth) return src.slice(m.index, j + 1); }
  }
  return '';
}).join('\n');
check('extracted all grading predicates', need.every(fn => snip.includes('function ' + fn)));

const sandbox = { window: {} };
new Function('window', snip + '\nreturn {isGraded, gameIsGraded, badgeHTML};')
  .call(null, sandbox.window);
const F = new Function(snip + '\nreturn {isGraded: isGraded, gameIsGraded: gameIsGraded, badgeHTML: badgeHTML};')();

// ---- the false rationalization must be gone ------------------------------
check('the "too dark or too flat to measure" claim is gone from index.html',
      !/too dark or too flat to measure/.test(html.replace(/that stood here claimed[\s\S]*?had measured them\./, '')),
      'only the historical note explaining it was false may remain');
check('isGraded tests the FRAME, not the gallery',
      /function isGraded\(p\)/.test(html), 'no per-gallery fallback parameter');
check('isGraded honours the explicit `gated` measurement',
      /isGraded\(p\)[\s\S]{0,120}p\.gated/.test(html));

// ---- unit behaviour ------------------------------------------------------
check('a scored frame is graded', F.isGraded({score: 41.2, tier: ''}) === true);
check('a measured 0.0 (gated) is graded',
      F.isGraded({score: 0, tier: '', gated: 1}) === true);
check('an UNMEASURED frame is NOT graded',
      F.isGraded({score: 0, tier: ''}) === false);
check('an unmeasured frame gets the NEW badge, not a silent blank',
      /NEW/.test(F.badgeHTML({score: 0, tier: ''})),
      F.badgeHTML({score: 0, tier: ''}).slice(0, 60));
check('a below-floor measured frame gets NO badge',
      F.badgeHTML({score: 41.2, tier: ''}) === '');
check('a keeper gets its tier badge',
      /B\+/.test(F.badgeHTML({score: 72.1, tier: 'B+'})));

// ---- against the real records -------------------------------------------
const win = {};
new Function('window', dataSrc)(win);
const D = win.SEASON_DATA;
let total = 0, ungraded = 0, newBadges = 0, tierable = 0;
const tabs = [];
for (const g of D.games) {
  let u = 0, t = 0;
  for (const p of g.photos) {
    total++;
    if (!F.isGraded(p)) { ungraded++; u++; }
    if (/NEW/.test(F.badgeHTML(p))) newBadges++;
    if (p.tier) { tierable++; t++; }
  }
  tabs.push([g.id, g.photos.length, u, t, F.gameIsGraded(g)]);
}
console.log('');
console.log('tab'.padEnd(22) + 'frames'.padStart(8) + 'ungraded'.padStart(10)
            + 'keepers'.padStart(9) + '  gameIsGraded');
for (const [id, n, u, t, gg] of tabs) {
  console.log(id.padEnd(22) + String(n).padStart(8) + String(u).padStart(10)
              + String(t).padStart(9) + '  ' + gg);
}
console.log('');
check('ZERO frames render as ungraded on the real data', ungraded === 0,
      ungraded + ' of ' + total);
check('ZERO frames render a misleading NEW badge', newBadges === 0,
      newBadges + ' of ' + total);
check('every non-empty tab reports itself graded',
      tabs.every(([, n, , , gg]) => n === 0 || gg === true));
check('the tier filter can reach keepers in every non-empty tab',
      tabs.every(([, n, , t]) => n === 0 || t > 0));

console.log('');
if (fails) { console.log('FAIL — ' + fails + ' check(s) failed.'); process.exit(1); }
console.log('PASS — the rendered page agrees with the data.');
