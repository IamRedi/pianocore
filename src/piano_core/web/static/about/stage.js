/* A silent musical illustration using the app's renderer, geometry and sprites.
 * This module has no device or API client and never schedules sound/MIDI. */
import { FallingNotesRenderer } from "./assets/native/visualizer.js";
import * as model from "./assets/native/stage-model.js";

export function mountCrystalIllustrations() {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  const button = document.querySelector(".motion-toggle");
  let paused = false, mode = "music", raf = 0, elapsed = 0, last = 0;
  const sources = [48,55,60,64,67,64,60,55,50,57,62,65,69,65,62,57];
  const notes = Array.from({length:96}, (_,i) => ({index:i,midi:sources[i % sources.length],onset_seconds:i*.46,duration_seconds:i % 4 === 0 ? 1.5 : .72,velocity:76,channel:1}));
  // Static sample data for the illustration only; no actual device state.
  const advice = new Map(notes.map(n=>[n.index,{hand:n.midi<60?"LEFT":"RIGHT",finger:1+n.index%5}]));
  const low=model.PIANO_KEYS.find(k=>k.midi===48).whiteIndex;
  const high=model.PIANO_KEYS.find(k=>k.midi===77).whiteIndex+1;
  const mappers={...model,visualCanvasSize:(w,h,dpr,scale)=>model.visualCanvasSize(w,h,Math.min(2,dpr),scale)};
  const scenes=[{canvas:document.querySelector("#crystal-stage"),practice:false,visible:true},{canvas:document.querySelector("#practice-stage"),practice:true,visible:false}].filter(s=>s.canvas);
  for (const scene of scenes) scene.renderer=new FallingNotesRenderer(scene.canvas,mappers,{resolutionScale:1,effects:true});
  function draw() {
    const position=4+(elapsed*.7)%30;
    for (const scene of scenes) {
      if (!scene.visible) continue;
      const practice=scene.practice || mode==="practice";
      const point=practice?8.28:position;
      const active=notes.filter(n=>n.onset_seconds<=point&&n.onset_seconds+n.duration_seconds>point);
      scene.renderer.render({position:point,status:"ready",range:{left:low/52,right:high/52},keyboardNotes:notes,maxSpanDuration:1.5,keyboardSources:new Map(active.map(n=>[n.midi,"playback"])),fingeringByIndex:advice,clippedSection:null,learning:{active:practice,targetOnset:practice?8.28:null,targetNotes:practice?[60]:[]}});
    }
  }
  function tick(now) {
    raf=0;
    if (document.hidden || paused || reduce.matches || !scenes.some(s=>s.visible&&!s.practice)) return;
    if (now-last>=1000/30) { elapsed+=Math.min(.05,(now-last)/1000)||0; last=now; draw(); }
    raf=requestAnimationFrame(tick);
  }
  function sync() {
    if (raf) cancelAnimationFrame(raf);raf=0;last=performance.now();draw();
    if (!document.hidden&&!paused&&!reduce.matches&&scenes.some(s=>s.visible&&!s.practice)) raf=requestAnimationFrame(tick);
    button.hidden=reduce.matches;
    button.setAttribute("aria-pressed",String(paused));
    button.setAttribute("aria-label",paused?button.dataset.resume:button.dataset.pause);
    button.firstElementChild.textContent=paused?"▷":"Ⅱ";
  }
  button.addEventListener("click",()=>{paused=!paused;sync();});
  for(const choice of document.querySelectorAll("[data-stage-mode]")) choice.addEventListener("click",()=>{
    mode=choice.dataset.stageMode;
    for(const item of document.querySelectorAll("[data-stage-mode]")) item.setAttribute("aria-pressed",String(item===choice));
    sync();
  });
  const observer=new IntersectionObserver(entries=>{for(const entry of entries){const scene=scenes.find(s=>s.canvas===entry.target);if(scene)scene.visible=entry.isIntersecting;}sync();},{threshold:.05});
  for(const scene of scenes)observer.observe(scene.canvas);
  new ResizeObserver(sync).observe(document.querySelector(".hero-art"));
  document.addEventListener("visibilitychange",sync);
  reduce.addEventListener("change",sync);
  sync();
}
