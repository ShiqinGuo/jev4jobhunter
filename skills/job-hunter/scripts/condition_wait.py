"""Bounded DOM-only condition waits inside one Kimi call. Never clicks or fetches."""
import json
import uuid
import store
from browsing_safety import classify


def script(observation, predicate, arguments, timeout_ms, stable_samples=2):
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 10000:
        raise ValueError('waitMs-must-be-integer-between-0-and-10000')
    if type(stable_samples) is not int or stable_samples not in (1, 2):
        raise ValueError('stable-samples-must-be-one-or-two')
    return "(async () => {const args=" + json.dumps(arguments,ensure_ascii=False) + ";" + """
const observe=()=>JSON.parse(OBSERVATION);
const accept=page=>{PREDICATE};
const samples=[]; const start=Date.now(); let waitMs=0;
let previous=null, stable=0;
while(true) {
  const page=observe(); samples.push(page);
  const accepted=accept(page);
  const signature=JSON.stringify(accepted);
  stable=accepted && signature===previous ? stable+1 : (accepted ? 1 : 0);
  previous=signature;
  if(stable>=STABLE) return JSON.stringify({ready:true,samples,waitMs});
  const remaining=TIMEOUT-(Date.now()-start);
  if(remaining<=0) return JSON.stringify({ready:false,samples,waitMs});
  const pause=Math.min(200,remaining); const before=Date.now();
  await new Promise(resolve=>setTimeout(resolve,pause)); waitMs+=Date.now()-before;
}
})()""".replace('OBSERVATION',observation).replace('PREDICATE',predicate).replace('TIMEOUT',str(timeout_ms)).replace('STABLE',str(stable_samples))


def observe(engine, observation, predicate, arguments, timeout_ms, stable_samples=2):
    result=engine._call(script(observation,predicate,arguments,timeout_ms,stable_samples),read_only=True)
    if engine.measurement:
        engine.measurement.wait_ms += result['waitMs']
    relative='logs/browsing/wait-'+uuid.uuid4().hex+'.json'
    (engine.safety.root/'logs'/'browsing').mkdir(parents=True,exist_ok=True)
    store.write_json(engine.safety.root/relative,{'at':store.stamp(),'session':engine.safety.session,**result})
    # Preserve every raw poll once, but update durable flow once. A transient
    # access restriction must still win over a later normal-looking snapshot.
    blocked=next((p for p in result['samples'] if classify(p) in
                  ('access-restricted','security-check','login-required')),None)
    saved=engine.safety.observe(blocked or result['samples'][-1])
    return {'ready':result['ready'] and blocked is None and saved['classification']=='normal','observation':saved,
            'observations':[relative,saved['evidence']], 'polls':len(result['samples'])}
