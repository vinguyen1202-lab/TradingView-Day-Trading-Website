(() => {
const $ = id => document.getElementById(id);
const safe = value => String(value ?? '—').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = obj => {
  if (obj === null || typeof obj !== 'object') return safe(obj);
  if (Array.isArray(obj)) return obj.map(pretty).join(', ');
  return Object.entries(obj).map(([key,value])=>'<div class="kv"><span>'+safe(key.replaceAll('_',' '))+'</span><b>'+ (value && typeof value==='object' ? safe(JSON.stringify(value)) : safe(value))+'</b></div>').join('');
};
const config = {risk:['SPY','QQQ','IWM','TLT','GLD'],pairs:['KO','PEP'],crossover:['SPY']};
async function run() {
  const project=$('quantProject').value, symbols=$('quantSymbols').value.toUpperCase().split(',').map(s=>s.trim()).filter(Boolean);
  const out=$('quantOutput'), button=$('quantRun');
  button.disabled=true;out.textContent='Calculating historical research…';
  try {
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),42000);
    let response;
    try {response=await fetch('/api/quant/'+project+'?symbols='+encodeURIComponent(symbols.join(',')),{signal:controller.signal,cache:'no-store'});}
    finally {clearTimeout(timer);}
    const data=await response.json();
    if (!response.ok) throw new Error(data.detail||'Research unavailable');
    out.innerHTML='<p class="muted small">Historical research · '+safe(data.calculated_at)+' · '+safe(data.cache)+'</p>'+pretty(data.data)+'<p class="muted small">'+safe(data.notice)+'</p>';
  } catch(e) {out.textContent='No result: '+e.message+'. Historical data must be available; no estimate is substituted.';}
  finally {button.disabled=false;}
}
$('quantProject').addEventListener('change',()=>{$('quantSymbols').value=config[$('quantProject').value].join(',');$('quantOutput').textContent='Select Run Quant Research to calculate.'});
$('quantRun').addEventListener('click',run);
})();
