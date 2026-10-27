// Collect only canonical Gold detail/Learning response bodies by exact CDP ID.
export function canonicalReceipts(driver,{onError=()=>{}}={}) {
 const requests=new Map(),responses=[];
 const relevant=url=>{const p=new URL(url).pathname;return p==='/api/v1/learning'||p.startsWith('/api/v1/library/songs/');};
 function observe(m){
  const p=m.params;
  if(m.method==='Network.requestWillBeSent'&&relevant(p.request.url))requests.set(p.requestId,{url:p.request.url,sequence:driver.sequence});
  if(m.method==='Network.responseReceived'&&requests.has(p.requestId))requests.get(p.requestId).status=p.response.status;
  if(m.method==='Network.loadingFailed')requests.delete(p.requestId);
  if(m.method==='Network.loadingFinished'){
   const request=requests.get(p.requestId);requests.delete(p.requestId);
   if(!request||request.status!==200||driver.tearingDown)return;
   const task=driver.send('Network.getResponseBody',{requestId:p.requestId}).then(r=>{responses.push({...request,requestId:p.requestId,body:JSON.parse(r.base64Encoded?Buffer.from(r.body,'base64').toString():r.body)});}).catch(error=>{if(!driver.tearingDown)onError(error);});
   driver.track(task);
  }
 }
 return {observe,responses};
}
