import {test} from "node:test";
import assert from "node:assert/strict";
import {bindInstall} from "../../src/piano_core/web/static/ui008/install.js";

function fixture({secure=true, standalone=false}={}) {
  const windowRef=new EventTarget();
  windowRef.isSecureContext=secure;
  const display=new EventTarget(); display.matches=standalone;
  windowRef.matchMedia=()=>display;
  windowRef.navigator={};
  const button=new EventTarget(); button.hidden=false;
  const status={textContent:""};
  const cleanup=bindInstall({windowRef,button,status});
  function offer({reject=false, choice="dismissed"}={}) {
    const event=new Event("beforeinstallprompt",{cancelable:true});
    let calls=0;
    event.prompt=async()=>{calls++; if(reject) throw new Error("browser refused");};
    event.userChoice=Promise.resolve({outcome:choice});
    windowRef.dispatchEvent(event);
    return {event,calls:()=>calls};
  }
  return {windowRef,button,status,display,cleanup,offer};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test("no fake install offer before browser eligibility",()=>{
  const f=fixture(); assert.equal(f.button.hidden,true); f.cleanup();
});
test("insecure origin never captures or displays a native offer",()=>{
  const f=fixture({secure:false});const o=f.offer();
  assert.equal(o.event.defaultPrevented,false);assert.equal(f.button.hidden,true); f.cleanup();
});
test("already installed app never offers installation",()=>{
  const f=fixture({standalone:true});const o=f.offer();
  assert.equal(o.event.defaultPrevented,false); assert.equal(f.button.hidden,true);f.cleanup();
});
test("eligible event only prompts on click, once, and dismissal consumes it",async()=>{
  const f=fixture(); const o=f.offer();
  assert.equal(o.event.defaultPrevented,true); assert.equal(o.calls(),0); assert.equal(f.button.hidden,false);
  f.button.dispatchEvent(new Event("click")); f.button.dispatchEvent(new Event("click")); await tick();
  assert.equal(o.calls(),1);assert.equal(f.button.hidden,true);assert.equal(f.button.disabled,false);f.cleanup();
});
test("failed native prompt gives useful browser fallback and permits a fresh offer",async()=>{
  const f=fixture();f.offer({reject:true});f.button.dispatchEvent(new Event("click"));await tick();
  assert.match(f.status.textContent,/browser/);assert.equal(f.button.hidden,true);
  f.offer();assert.equal(f.button.hidden,false);f.cleanup();
});
test("appinstalled clears a pending offer",()=>{
  const f=fixture();f.offer();f.windowRef.dispatchEvent(new Event("appinstalled"));assert.equal(f.button.hidden,true);f.cleanup();
});
test("display-mode change hides the offer and teardown removes handlers",()=>{
  const f=fixture();f.offer();f.display.matches=true;f.display.dispatchEvent(new Event("change"));assert.equal(f.button.hidden,true);
  f.cleanup();f.display.matches=false;const o=f.offer();assert.equal(o.event.defaultPrevented,false);
});
