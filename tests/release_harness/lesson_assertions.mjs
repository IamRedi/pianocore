import assert from 'node:assert/strict';

// Expectations come from the actual successful detail response for this
// navigation generation, not from page-wide text or a racing second API read.
export function assertLessonDetail(receipt, expected, view, afterSequence) {
  assert(receipt && receipt.sequence > afterSequence, 'fresh selected detail response');
  assert.equal(receipt.status, 200);
  assert.equal(new URL(receipt.url).pathname, '/api/v1/learn/lessons/' + encodeURIComponent(expected.lesson_id));
  const d = receipt.body;
  assert.equal(d.lesson_id, expected.lesson_id, 'canonical lesson identity');
  assert.equal(d.package_id, expected.package_id, 'canonical package identity');
  assert.equal(d.title, expected.title, 'canonical catalog title');
  assert.equal(d.course_id, expected.course_id, 'canonical course identity');
  assert.equal(d.progress_available, true, 'authoritative persisted progress available');
  assert(d.progress && Number.isFinite(d.progress.completions));
  assert.equal(d.progress.completions,d.progress.full_lesson_completions,'qualified full-lesson persisted completion');
  assert(view.visible, 'selected detail panel actually visible');
  const completed = d.progress.completions > 0;
  assert.equal(view.title, (completed ? '✓ ' : '') + d.title, 'scoped completion decoration and exact title');
  assert.equal(view.summary, d.summary || '', 'selected detail summary');
  const levels=['First Notes','Beginner','Elementary','Intermediate','Advanced'];
  // Level labels are canonical UI vocabulary; title/composer/difficulty/duration
  // are delivered Core content. Keep all checks scoped to the selected detail.
  const seconds=Number(d.duration_seconds),minutes=Math.floor(seconds/60),tail=Math.round(seconds%60);
  const duration=!Number.isFinite(seconds)||seconds<=0?'':minutes<=0?`${tail}s`:`${minutes}m ${String(tail).padStart(2,'0')}s`;
  assert.equal(view.subtitle,[d.composer,`L${d.level} ${levels[d.level] || `Level ${d.level}`}`,d.difficulty,duration].filter(Boolean).join(' · '),'canonical composer and subtitle');
  assert.equal(view.license, [d.license?.name, d.license?.attribution].filter(Boolean).join(' · '));
  for (const [key, id] of [['previous', d.previous_lesson_id], ['next', d.next_lesson_id]]) {
    assert.equal(view[key].id, id || '', key + ' selected-lesson navigation');
    assert.equal(view[key].disabled, !id);
  }
  const p = d.progress;
  const progress = Number(p.attempts) > 0
    ? [`${p.attempts} practice run${p.attempts === 1 ? '' : 's'}`,
       p.completions > 0 ? `${p.completions} complete` : null,
       p.note_accuracy != null ? `${Math.round(Number(p.note_accuracy) * 100)}% notes played` : null].filter(Boolean).join(' · ')
    : 'Not practiced yet';
  assert.equal(view.progress, progress, 'rendered progress from delivered persisted snapshot');
  const metrics = d.metrics || {};
  assert.equal(view.metrics, [metrics.note_count != null ? `${metrics.note_count} notes` : null,
    metrics.max_simultaneous != null ? `up to ${metrics.max_simultaneous} together` : null,
    metrics.notes_per_second != null ? `${Number(metrics.notes_per_second).toFixed(1)} notes/s` : null].filter(Boolean).join(' · '));
  return {lesson_id: d.lesson_id, package_id: d.package_id, title: d.title,
          completed, persisted_progress: d.progress, navigation_sequence: receipt.sequence};
}

export const detailSnapshot = `(()=>{
 const panel=document.getElementById('ui008-lesson-detail');
 const text=id=>document.getElementById(id)?.textContent;
 const nav=id=>{const e=document.getElementById(id);return{id:e?.dataset.lessonId,disabled:e?.disabled}};
 const r=panel?.getBoundingClientRect();
 return {visible:!!panel&&!panel.hidden&&r.width>0&&r.height>0&&getComputedStyle(panel).visibility!=='hidden',
 title:text('ui008-lesson-detail-title'),summary:text('ui008-lesson-detail-summary'),
 subtitle:text('ui008-lesson-detail-sub'),
 license:text('ui008-lesson-detail-license'),progress:text('ui008-lesson-detail-progress'),
 metrics:text('ui008-lesson-detail-metrics'),previous:nav('ui008-lesson-prev'),next:nav('ui008-lesson-next')};
})()`;
