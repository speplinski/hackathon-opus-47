/* ================================================================
   Auditable Design — demo bootstrap
   Loads data.json and populates scroll-narrative sections.
   ================================================================ */

(async function () {
  const res = await fetch('data.json');
  if (!res.ok) {
    console.error('Failed to load data.json:', res.status);
    return;
  }
  const data = await res.json();
  window.__AD_DATA = data; // debug handle

  renderPipeline(data);
  renderCluster(data);
  renderHeuristics(data);
})();

// ==================================================================
function renderPipeline(data) {
  const mount = document.getElementById('pipeline-flow');
  if (!mount) return;

  const baseline = data.meta.baseline_sum;
  const final = data.meta.final_sum;

  const layers = [
    { id: '01', name: 'Signal extraction',        note: 'classify, structure, cluster, label' },
    { id: '02', name: 'Multi-lens audit',         note: 'Norman · WCAG · Kahneman · Osterwalder · Cooper · Garrett' },
    { id: '03', name: 'Reconciliation',           note: `hero cluster: ${data.reconciled.ranked_violations.length} named heuristics · severity sum ${baseline}` },
    { id: '04', name: 'Priority scoring',         note: `hero cluster weighted total ${data.priority.weighted_total.toFixed(1)} (top across 6 clusters)` },
    { id: '05', name: 'Direction generation',     note: 'one decision per priority' },
    { id: '06', name: 'Iterative refinement',     note: `verifier-gated loop · hero cluster converged in ${data.iterations.length} iterations` },
    { id: '07', name: 'Real-product grounding',   note: 'Claude Opus 4.7 checks each hypothesis against screenshots', featured: true },
    { id: '08', name: 'Design brief export',      note: 'ten-section markdown for the designer',                       featured: true },
  ];

  mount.innerHTML = layers.map(layer => `
    <div class="pipe-layer${layer.featured ? ' pipe-featured' : ''}">
      <div class="pipe-id">${layer.id}</div>
      <div>
        <div class="pipe-name">${layer.name}</div>
        <div style="font-size:0.92rem; color:var(--muted); margin-top:4px; font-style:normal;">${layer.note}</div>
      </div>
      <div class="pipe-sum">${layer.featured ? 'opus-4.7' : ''}</div>
    </div>
  `).join('');
}

// ==================================================================
function renderCluster(data) {
  const c = data.cluster;
  if (!c) return;

  document.getElementById('cluster-label').textContent = c.label;
  document.getElementById('cluster-context').textContent = c.ui_context || '';
  document.getElementById('review-count').textContent = c.member_review_ids.length;

  const quotes = document.getElementById('cluster-quotes');
  quotes.innerHTML = c.representative_quotes
    .slice(0, 5)
    .map(q => `<div class="quote">${escapeHtml(q)}</div>`)
    .join('');

  const reviews = document.getElementById('cluster-reviews');
  reviews.innerHTML = c.member_review_ids
    .map(id => `<span class="review-id">${id}</span>`)
    .join('');
}

// ==================================================================
function renderHeuristics(data) {
  const mount = document.getElementById('heuristic-list');
  if (!mount) return;

  const violations = data.reconciled.ranked_violations;
  const ge = (data.verify_on_product || {}).grounded_evidence || {};

  document.getElementById('baseline-count').textContent = violations.length;
  document.getElementById('baseline-sum').textContent = data.meta.baseline_sum;

  // Group violations by severity so all CRITICAL (9) items cluster
  // under one header, then SERIOUS (7), etc. The section is already
  // ranked by severity — grouping just makes the ranking visible.
  const groups = new Map();
  for (const v of violations) {
    if (!groups.has(v.severity)) groups.set(v.severity, []);
    groups.get(v.severity).push(v);
  }
  // Render groups in descending severity (most critical first).
  const sortedSeverities = [...groups.keys()].sort((a, b) => b - a);

  mount.innerHTML = sortedSeverities.map(sev => {
    const groupItems = groups.get(sev);
    const header = `
      <div class="heuristic-group-header">
        <div class="sev-pill sev-${sev}" title="severity ${sev} — ${severityLabel(sev)}">${sev}</div>
        <div class="group-label">${escapeHtml(severityLabel(sev))}</div>
      </div>
    `;
    const items = groupItems.map(v => {
      const entry = ge[v.heuristic];
      const verdictHtml = entry
        ? `<div class="verdict-pill verdict-${entry.confirmed}">${entry.confirmed}</div>`
        : `<div class="verdict-pill" style="color:var(--muted)">not verified</div>`;
      return `
        <div class="heuristic heuristic-in-group">
          <div class="heuristic-body">
            <div class="h-slug">${escapeHtml(humanizeHeuristic(v.heuristic))}</div>
            <div class="h-violation">${escapeHtml(humanizeInlineSlugs(v.violation || ''))}</div>
          </div>
          ${verdictHtml}
        </div>
      `;
    }).join('');
    return `<div class="heuristic-group">${header}${items}</div>`;
  }).join('');
}

// ==================================================================
// helpers
function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}
function truncate(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

// Reconciled heuristic slugs are machine identifiers (e.g.
// ``monetisation_interrupts_value__posture_drift_within_product__skeleton_does_not_honour_priority``).
// The double-underscore separates multiple lens-specific heuristics merged
// into one reconciled entry; single underscores are word separators.
// Render them as human titles joined by a middle-dot, with a small
// dictionary of design-theory abbreviations expanded so acronyms don't
// break into nonsense fragments like "r dollar" or "cr".
// Two glossaries. Title glossary keeps acronyms compact (``CR``,
// ``R$``) so the heading stays short; prose glossary spells them out
// so the violation paragraph reads as full English. Slug and prose
// both start from the same identifier list; divergence is only in
// the replacement string for the compound-slug tokens.
//
// JavaScript ``\b`` treats underscore as a word character, so
// ``\bcr_undermined_by_r_dollar\b`` does not match inside a slug
// like ``cr_undermined_by_r_dollar__pattern_declared_not_implemented``
// — the boundary between ``r`` and ``__`` is between two word chars.
// Anchor the compound-slug entries on start/end of string OR a
// double-underscore boundary. Short acronyms use a non-alphanumeric
// boundary class instead of ``\b``.
const _SHARED_ACRONYMS = [
  [/(^|[^a-zA-Z0-9])a11y(?=[^a-zA-Z0-9]|$)/g,  '$1A11y'],
  [/(^|[^a-zA-Z0-9])wcag(?=[^a-zA-Z0-9]|$)/gi, '$1WCAG'],
  [/(^|[^a-zA-Z0-9])cta(?=[^a-zA-Z0-9]|$)/gi,  '$1CTA'],
  [/(^|[^a-zA-Z0-9])ui(?=[^a-zA-Z0-9]|$)/gi,   '$1UI'],
  [/(^|[^a-zA-Z0-9])ux(?=[^a-zA-Z0-9]|$)/gi,   '$1UX'],
  [/(^|[^a-zA-Z0-9])xp(?=[^a-zA-Z0-9]|$)/gi,   '$1XP'],
  [/(^|[^a-zA-Z0-9])vlm(?=[^a-zA-Z0-9]|$)/gi,  '$1VLM'],
];
const _TITLE_GLOSSARY = [
  [/(^|__)cr_undermined_by_r_dollar(?=__|$)/g, '$1CR undermined by R$'],
  [/(^|__)vp_cs_mismatch(?=__|$)/g,            '$1VP/CS mismatch'],
  ..._SHARED_ACRONYMS,
];
const _PROSE_GLOSSARY = [
  [/(^|[^a-zA-Z0-9])cr_undermined_by_r_dollar(?=[^a-zA-Z0-9]|$)/g, '$1customer-relationship undermined by revenue-stream'],
  [/(^|[^a-zA-Z0-9])vp_cs_mismatch(?=[^a-zA-Z0-9]|$)/g,            '$1value-proposition / customer-segment mismatch'],
  ..._SHARED_ACRONYMS,
];

function _applyGlossary(text, table) {
  let out = String(text);
  for (const [re, repl] of table) {
    out = out.replace(re, repl);
  }
  return out;
}

function humanizeHeuristic(slug) {
  if (!slug) return '';
  const expanded = _applyGlossary(slug, _TITLE_GLOSSARY);
  return expanded
    .split('__')
    .map(part => part.replace(/_/g, ' ').trim())
    .filter(Boolean)
    .map(part => part === 'corroborated' ? null : part)
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' · ');
}

// Violation prose from the reconciled layer often name-drops slug identifiers
// inline. Three kinds to transform:
//   • underscore-joined ("after_snapshot", "upgrade_path_opaque") — always a
//     technical identifier in this corpus, always transform.
//   • dash-joined with 3+ tokens ("monetisation-interrupts-value",
//     "loss-framed-free-exit") — technical slug.
//   • dash-joined with 2 tokens — ambiguous: could be a technical slug
//     ("missing-undo") but also legitimate English ("mid-lesson", "auto-save").
//     Left alone — the cost of occasionally leaving a 2-token slug hyphenated
//     is smaller than the cost of breaking English compound words.
function humanizeInlineSlugs(text) {
  if (!text) return '';
  let out = _applyGlossary(String(text), _PROSE_GLOSSARY);
  // Underscore compounds — always slug-like.
  out = out.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, m => m.replace(/_/g, ' '));
  // Dash compounds with 3+ tokens — slug-like (3+ hyphens in a row isn't English).
  out = out.replace(/\b[a-z]+(?:-[a-z]+){2,}\b/g, m => m.replace(/-/g, ' '));
  return out;
}

// Sev scale legend — word labels are the non-colour cue.
// Colour + number + word together give enough redundancy that the
// severity reads in greyscale / colour-blind modes without extra
// shape icons.
const _SEV_LABELS = { 0: 'absent', 3: 'minor', 5: 'moderate', 7: 'serious', 9: 'critical' };
function severityLabel(s) { return _SEV_LABELS[s] || `severity ${s}`; }
