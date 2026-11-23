"""Write a self-contained HTML report of the offline routing simulation.

    python -m training.report [--mode balanced] [--temperature 400] [--out training/routing-report.html]

Without --temperature the learner's temperature is chosen by cross-validation on the training rows.
"""

import argparse
import json
from pathlib import Path

from dashboard import ladder
from training import simulate, train

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HARP routing simulation</title>
<style>
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d2421;--mute:#68716c;--line:#dedbd2;--accent:#287658;--bad:#b3402e;--good:#287658;--chip:#eef3ef;--warn:#9a6a00}
@media (prefers-color-scheme:dark){:root{--bg:#141816;--card:#1d2321;--ink:#e8ece9;--mute:#99a39d;--line:#303935;--accent:#4fb58d;--bad:#e5806d;--good:#4fb58d;--chip:#26302c;--warn:#e0b04a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}p{margin:0}.mute{color:var(--mute)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card b{display:block;font-size:26px;line-height:1.2}.card span{font-size:13px;color:var(--mute)}
.bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.bar{display:grid;grid-template-columns:130px 1fr 48px;gap:8px;align-items:center;font-size:13px;margin:5px 0}
.bar i{display:block;height:10px;border-radius:5px;background:var(--accent)}
.track{background:var(--chip);border-radius:5px;overflow:hidden}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
select,input{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px}
input{flex:1;min-width:180px}
.tablewrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;min-width:900px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}
th{position:sticky;top:0;background:var(--card);color:var(--mute);font-weight:600}
tr.row{cursor:pointer}tr.row:hover td{background:var(--chip)}
.chip{display:inline-block;padding:1px 8px;border-radius:99px;background:var(--chip);font-size:12px;white-space:nowrap}
.ok{color:var(--good);font-weight:700}.no{color:var(--bad);font-weight:700}.warn{color:var(--warn)}
td.prompt{max-width:320px}
.detail td{background:var(--chip)}.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.detail h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);margin:0 0 4px}
.detail ul{margin:0;padding-left:18px}.full{font-size:15px;margin-bottom:10px}
.note{margin-top:14px;padding:10px 14px;border-left:3px solid var(--warn);background:var(--card);border-radius:0 8px 8px 0;font-size:13px}
.ladder td,.ladder th{font-size:13px}.src{font-size:12px;margin-top:8px}.src a{color:var(--accent)}
</style></head><body><main>
<h1>HARP routing simulation</h1>
<p class="mute" id="sub"></p>
<div class="cards" id="cards"></div>
<div class="note" id="note"></div>
<h2>The cost ladder</h2>
<div class="tablewrap"><table class="ladder"><thead><tr><th>Tier</th><th>Anthropic</th><th>Codex</th><th>Est. cost</th></tr></thead><tbody id="ladder"></tbody></table></div>
<p class="src mute" id="src"></p>
<h2>Where the work went</h2><div class="bars" id="bars"></div>
<h2>Every prompt: input and result</h2>
<div class="filters">
 <select id="fSplit"><option value="">All 500</option><option>held-out</option><option>seen in training</option></select>
 <select id="fGroup"><option value="">All groups</option></select>
 <select id="fProvider"><option value="">Any provider</option><option>anthropic</option><option>openai-codex</option></select>
 <select id="fEffort"><option value="">Any effort</option></select>
 <select id="fOk"><option value="">Correct and wrong</option><option value="ok">Route correct</option><option value="bad">Route wrong</option></select>
 <select id="fSub"><option value="">Any sub-agent count</option><option value="0">No sub-agents</option><option value="1">With sub-agents</option></select>
 <input id="q" placeholder="Search prompt text">
</div>
<p class="mute" id="count"></p>
<div class="tablewrap"><table><thead><tr>
<th>ID</th><th>Prompt (input)</th><th>Quota case</th><th>Tier</th><th>Agent</th><th>Provider</th><th>Model</th><th>Effort</th><th>Cost</th><th>Sub</th><th>Route</th>
</tr></thead><tbody id="rows"></tbody></table></div>
<script>
const DATA=__DATA__, SUMMARY=__SUMMARY__, META=__META__, LADDER=__LADDER__;
const $=id=>document.getElementById(id);
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const pct=a=>a[1]?Math.round(100*a[0]/a[1])+'%':'-';
const short=m=>m.replace('claude-','').replace('-5-5',' 5.5').replace('-5-2',' 5.2').replace('gpt-6.2-','GPT-6.2 ').replace('gpt-6.1-','GPT-6.1 ').replace('gpt-6-','GPT-6 ');
$('sub').textContent=`The local learner was trained on ${META.trained} prompts, then run over all ${SUMMARY.total}. Mode ${META.mode}, temperature ${META.temperature} (picked by cross-validation on the training rows). Only the ${SUMMARY.splits['held-out'].n} held-out prompts are a fair test.`;
for(const name of ['held-out','seen in training']){const s=SUMMARY.splits[name];
 [[pct(s.route),`${name}: provider, model and effort right (${s.route[0]}/${s.route[1]})`],
  [pct(s.tier),`${name}: difficulty tier exactly right`],[pct(s.near),`${name}: tier within one step`],
  [`${s.under[0]} / ${s.over[0]}`,`${name}: too weak / too strong (of ${s.n})`],[pct(s.agent),`${name}: agent role right`]]
 .forEach(([b,t])=>{const c=el('div','card');c.append(el('b','',b),el('span','',t));$('cards').append(c)})}
const cost=SUMMARY.cost;const base=Object.entries(cost.baselines).map(([n,v])=>`${n} ${v.toFixed(0)} (${cost.ladder>v?'+':''}${Math.round(100*(cost.ladder/v-1))}%)`).join('; ');
$('note').textContent=`Held-out cost per prompt: ladder ${cost.ladder.toFixed(1)}, perfect tiers ${cost.perfect.toFixed(1)}. Fixed routes: ${base}. Costs are relative units (output price times an estimated reasoning-token multiplier), not dollars. Seen-in-training numbers only show the learner remembers its lessons. When the learner is unsure it leans one tier up, not to the top. Sub-agent counts come from a rule-based planner and have no ground truth; HARP does not split work. No prompt in the dataset needs the top tiers, so extreme and maximum are never used here.`;
LADDER.tiers.forEach((t,i)=>{const tr=el('tr');tr.append(el('td','',`${i} ${t.name}`),el('td','',`${short(t.anthropic.model)}, ${t.anthropic.effort}`),el('td','',`${short(t.codex.model)}, ${t.codex.effort}`),el('td','',`${t.anthropic.cost} / ${t.codex.cost}`));$('ladder').append(tr)});
$('src').innerHTML='Prices and effort levels from the providers: <a href="https://platform.claude.com/docs/en/about-claude/pricing">Anthropic pricing</a>, <a href="https://platform.claude.com/docs/en/build-with-claude/effort">Anthropic effort</a>, <a href="https://developers.openai.com/api/docs/pricing">OpenAI pricing</a>, <a href="https://developers.openai.com/api/docs/models">OpenAI models</a>. The effort-to-token multipliers ('+Object.entries(LADDER.multipliers).map(([k,v])=>k+' '+v+'x').join(', ')+') are estimates; max is an assumption.';
function bars(title,obj){const box=el('div','card');const h=el('b','',title);h.style.fontSize='15px';box.append(h);
 const max=Math.max(...Object.values(obj),1);
 for(const [k,v] of Object.entries(obj)){const r=el('div','bar');r.append(el('span','',k));const t=el('div','track');const i=el('i');i.style.width=(100*v/max)+'%';t.append(i);r.append(t,el('span','',v));box.append(r)}
 $('bars').append(box)}
bars('Provider',SUMMARY.providers);bars('Model',Object.fromEntries(Object.entries(SUMMARY.models).map(([k,v])=>[short(k),v])));
bars('Effort',SUMMARY.efforts);bars('Predicted tier',SUMMARY.tiers);
bars('Sub-agents per prompt',Object.fromEntries(Object.entries(SUMMARY.sub_agents).map(([k,v])=>[k==='0'?'none':k+' sub-agents',v])));
[...new Set(DATA.map(r=>r.group))].sort().forEach(g=>$('fGroup').append(el('option','',g)));
Object.keys(SUMMARY.efforts).forEach(e=>$('fEffort').append(el('option','',e)));
function quota(r){const a=r.input.accounts;const f=p=>a[p]?a[p].map(x=>x.peak+'% '+x.plan).join(', '):'no data';
 return [`Anthropic: ${f('anthropic')}`,`Codex: ${f('openai-codex')}`,`Heavyweight: ${r.input.heavyweight}`]}
const route=x=>`${x.provider} / ${short(x.model)} / ${x.effort}`;
function detail(r){const td=el('td');td.colSpan=11;td.append(el('p','full',r.prompt));const g=el('div','grid2');
 const add=(h,items)=>{const d=el('div');d.append(el('h3','',h));const u=el('ul');items.forEach(x=>u.append(el('li','',x)));d.append(u);g.append(d)};
 add('Input',[`Language: ${r.language}`,...quota(r),`Usage limit: ${r.input.limit}%`,`Case: ${r.input.case}`]);
 add('Local learner',[`Tier: ${r.learner.tier} ${r.learner.tier_name} (expected value ${r.learner.expected})`,`Agent: ${r.learner.agent}`,`Work type: ${r.learner.work}`,`Confidence: ${Math.round(r.learner.confidence*100)}% (${r.learner.unsure?'unsure, leaned one tier up':'sure'})`]);
 add('Sent to',[`Provider: ${r.route.provider}`,`Model: ${short(r.route.model)}`,`Effort: ${r.route.effort}`,`Estimated cost: ${r.cost}`]);
 add('Expected',[`Tier: ${r.truth.tier} ${LADDER.tiers[r.truth.tier].name}`,`Agent: ${r.truth.group}`,route(r.truth.route),`Estimated cost: ${r.ideal_cost}`]);
 add('Sub-agents: '+r.sub_agents.length,[r.sub_reason,...r.sub_agents.map(s=>`${s.role}: ${route(s)}`)]);
 td.append(g);const tr=el('tr','detail');tr.append(td);return tr}
const mark=v=>el('span',v?'ok':'no',v?'✓':'✗');
function render(){const f={split:$('fSplit').value,group:$('fGroup').value,prov:$('fProvider').value,eff:$('fEffort').value,ok:$('fOk').value,sub:$('fSub').value,q:$('q').value.trim().toLowerCase()};
 const body=$('rows');body.replaceChildren();let n=0;
 for(const r of DATA){
  if(f.split&&r.split!==f.split||f.group&&r.group!==f.group||f.prov&&r.route.provider!==f.prov||f.eff&&r.route.effort!==f.eff)continue;
  if(f.ok==='ok'&&!r.ok.route||f.ok==='bad'&&r.ok.route)continue;
  if(f.sub==='0'&&r.sub_agents.length||f.sub==='1'&&!r.sub_agents.length)continue;
  if(f.q&&!r.prompt.toLowerCase().includes(f.q))continue;
  n++;const tr=el('tr','row');const chip=t=>{const td=el('td');td.append(el('span','chip',t));return td};
  const tier=chip(`${r.truth.tier} → ${r.learner.tier}`);if(r.learner.unsure)tier.append(' ',el('span','warn','?'));
  tr.append(el('td','',r.id.replace('harp-seed-','#')),el('td','prompt',r.prompt.length>90?r.prompt.slice(0,90)+'…':r.prompt),
   chip(r.input.case.replaceAll('_',' ')),tier,el('td','',r.learner.agent),el('td','',r.route.provider==='anthropic'?'Anthropic':'Codex'),
   el('td','',short(r.route.model)),el('td','',r.route.effort),el('td','',String(r.cost)),el('td','',r.sub_agents.length?String(r.sub_agents.length):'-'));
  const ok=el('td');ok.append(mark(r.ok.route));tr.append(ok);
  let open=null;tr.onclick=()=>{if(open){open.remove();open=null}else{open=detail(r);tr.after(open)}};
  body.append(tr)}
 $('count').textContent=`${n} of ${DATA.length} prompts shown. Tier shows expected → predicted; "?" marks an unsure learner. Click a row for the full input, learner output and sub-agents.`}
['fSplit','fGroup','fProvider','fEffort','fOk','fSub'].forEach(i=>$(i).onchange=render);$('q').oninput=render;render();
</script></main></body></html>
"""


def ladder_table():
    tiers = []
    for t, name in enumerate(ladder.TIERS):
        entry = {"name": name}
        for key, provider in (("anthropic", ladder.ANTHROPIC), ("codex", ladder.CODEX)):
            rung = ladder.rung(provider, t)
            entry[key] = {"model": rung["model"], "effort": rung["effort"], "cost": round(ladder.cost_of(rung), 1)}
        tiers.append(entry)
    return {"tiers": tiers, "multipliers": ladder.EFFORT_TOKENS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=ladder.MODE_TOP_TIER, default="balanced")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--out", default=str(Path(__file__).with_name("routing-report.html")))
    args = parser.parse_args()
    results, summary, info = simulate.run(train.load_rows(), args.mode, args.temperature)
    dump = lambda value: json.dumps(value, ensure_ascii=False).replace("</", "<\\/")
    html = (PAGE.replace("__DATA__", dump(results)).replace("__SUMMARY__", dump(summary))
            .replace("__META__", dump(info)).replace("__LADDER__", dump(ladder_table())))
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"Wrote {args.out} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
