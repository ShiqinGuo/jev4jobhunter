"""Bounded DOM-only condition waits inside one Kimi call. Never clicks or fetches."""
import json


def script(observation, predicate, arguments, timeout_ms, stable_samples=2):
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 10000:
        raise ValueError('waitMs-must-be-integer-between-0-and-10000')
    if type(stable_samples) is not int or stable_samples not in (1, 2):
        raise ValueError('stable-samples-must-be-one-or-two')
    return "(async () => {const args=" + json.dumps(arguments,ensure_ascii=False) + ";" + """
const observe=()=>JSON.parse(OBSERVATION);
const accept=page=>{PREDICATE};
// Match the blocking classifications in browsing_safety.classify on every poll.
// Stop immediately: a transient challenge must not disappear in a later sample.
const blocked=page=>{
  const body=page.body||'';
  let signals=body;
  for(const content of [page.detail?.text,...(page.cards||[]).map(c=>c.text)])
    if(content) signals=signals.split(content).join('');
  const normal=!!(page.cards?.length||page.detail||page.controls?.length||page.chat?.rows?.length||page.chat?.recipient||page.filenames?.length);
  return /访问受限|IP存在异常行为|账户存在异常行为|账号存在异常行为|请完成安全验证|请完成验证|滑动验证/.test(signals)||
    (page.url||'').includes('/passport/zp/403')||
    (body.includes('安全检查')&&!normal)||
    (body.includes('登录')&&!page.accountLabel&&!normal)||body.includes('登录查看完整内容');
};
const start=Date.now(); let waitMs=0, polls=0;
let previous=null, stable=0;
while(true) {
  const page=observe(); polls++;
  const finish=ready=>JSON.stringify({ready,page,polls,waitMs});
  if(blocked(page)) return finish(false);
  const accepted=accept(page);
  const signature=JSON.stringify(accepted);
  stable=accepted && signature===previous ? stable+1 : (accepted ? 1 : 0);
  previous=signature;
  if(stable>=STABLE) return finish(true);
  const remaining=TIMEOUT-(Date.now()-start);
  if(remaining<=0) return finish(false);
  const pause=Math.min(200,remaining); const before=Date.now();
  await new Promise(resolve=>setTimeout(resolve,pause)); waitMs+=Date.now()-before;
}
})()""".replace('OBSERVATION',observation).replace('PREDICATE',predicate).replace('TIMEOUT',str(timeout_ms)).replace('STABLE',str(stable_samples))


def observe(engine, observation, predicate, arguments, timeout_ms, stable_samples=2):
    result=engine._call(script(observation,predicate,arguments,timeout_ms,stable_samples),read_only=True)
    if engine.measurement:
        engine.measurement.wait_ms += result['waitMs']
    saved=engine.safety.observe(result['page'],wait={k:result[k] for k in ('ready','polls','waitMs')})
    return {'ready':result['ready'] and saved['classification']=='normal',
            'observation':saved,'polls':result['polls']}
