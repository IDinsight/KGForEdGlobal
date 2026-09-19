"""Render validated LP evaluation reports as linked workbooks and offline HTML."""

# Standard Library
import base64
import gzip
import hashlib
import io
import json
import math
import platform

from collections import defaultdict
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

# Third Party Library
import xlsxwriter

from pydantic import TypeAdapter
from xlsxwriter.format import Format
from xlsxwriter.worksheet import Worksheet

# Package Library
from kgfeg.evals.lp_eval.judge import (
    _store_path,
    _store_read,
    canonicalize_classification,
    validate_judge_response,
)
from kgfeg.evals.lp_eval.sampling import _frozen_output_boundary
from kgfeg.evals.lp_eval.schemas import (
    ClassificationJudgment,
    DiscoveryInventory,
    EvaluationReportArtifacts,
    ScheduledRequest,
)
from kgfeg.evals.lp_eval.utils import _publish_files, _validated_report_manifest
from kgfeg.kgs.lp_requests import canonical_lp_json, lp_material_content_hash

_GUIDE = (
    (
        "Start here",
        (
            "Open report.html for six interactive views. Filter the workbook detail"
            " sheets. J = scheduled judgment, P = unique pair/case, C = concern "
            "group, X = comparison, R = rationale claim. References are stable "
            "within this report; original IDs are retained."
        ),
    ),
    (
        "Counting",
        (
            "A member judgment is one scheduled assessment, including its condition"
            " and repetition. P references count unique curriculum pairs/cases. One"
            " pair can have many judgments and belong to several overlapping "
            "concerns, tags and sampling routes. Do not sum overlapping groups."
        ),
    ),
    (
        "Reading decisions",
        (
            "Production classifications, independent blind classifications, "
            "original-evidence rationale critiques and constructed synthetic "
            "controls are separate. Fully grounded means all material rationale "
            "claims were supported; it says nothing about relationship agreement."
        ),
    ),
    (
        "Direction",
        (
            "A and B denote the canonical endpoint UUID order in this report, not "
            "learning order. A → B and B → A preserve developmental direction. "
            "Judge first/second order may be swapped; evidence citation indexes "
            "still refer to the saved payload. See each judgment's complete input "
            "and endpoint mapping."
        ),
    ),
    (
        "Evidence comparisons",
        (
            "Comparisons show starting and changed payloads with exact JSON-pointer"
            " differences and endpoint evidence summaries. Text, Learning "
            "Components, hierarchy and metadata are compared separately. Metadata-"
            "only expansion does not establish extra educational substance. A "
            "repeated presentation may use a different tracking ID; prompt byte "
            "equality is checked separately."
        ),
    ),
    (
        "Denominators",
        (
            "Coverage uses independently sampled judge-positive pairs under the "
            "common reconstructed evidence condition. Uniform selections and "
            "diagnostic selections remain separate and may overlap. Matrices and "
            "grounding use original production evidence, split by replicate. "
            "Missing or zero-denominator results are unavailable."
        ),
    ),
    (
        "Interpretation",
        (
            "Agreement is not accuracy. Sampled judge-positive coverage is not "
            "established curriculum-wide recall. Diagnostic oversamples are not "
            "population estimates. No composite score, curriculum correctness "
            "ranking, uncertainty estimate or automatic passing threshold is "
            "supplied. Larger defaults do not establish ground truth."
        ),
    ),
    (
        "Personal notes",
        (
            "Personal notes are blank editable annotations in Concerns. They never "
            "update official dispositions. Official status here is the status saved"
            " in the source report; later disposition records are not inferred or "
            "automatically merged."
        ),
    ),
    (
        "Full text and sources",
        (
            "Long spreadsheet cells are explicitly shortened for reading; use the "
            "J/X links in report.html for full statements, rationale, evidence, "
            "prompts, response schemas and source IDs. presentation_data.json "
            "preserves every detail table and saved request. Local HTML links may "
            "not survive Google Sheets import; search the same short reference in "
            "the HTML report."
        ),
    ),
    (
        "Preservation",
        (
            "These are derivative presentation files. Their manifest binds the "
            "immutable source report, actual material hashes and renderer "
            "source/dependency identity. They cannot change evaluation evidence, "
            "official dispositions or production results and do not certify "
            "completion."
        ),
    ),
)
_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'">
<title>Learning progressions evaluation</title><style>
:root{font-family:Arial,sans-serif;color:#20354a;background:#f3f5f8;font-size:15px}*{box-sizing:border-box}body{margin:0}header{background:#20354a;color:white;padding:28px 36px 22px}h1{font-size:28px;font-weight:600;margin:0 0 10px}h2{font-size:22px;margin:0 0 12px}h3{font-size:17px}p{line-height:1.5}header p{max-width:1100px;margin:8px 0;color:#dce6ef}.strip{display:flex;gap:24px;flex-wrap:wrap;padding:16px 0 0}.stat strong{display:block;font-size:24px}.stat span{font-size:12px;color:#dce6ef}main{padding:24px 36px;max-width:1800px;margin:auto}nav{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 20px}button,select,input{font:inherit;border:1px solid #bdc9d5;border-radius:5px;padding:9px;background:white;color:#20354a}button{cursor:pointer}button:hover{background:#e7eef5}button.active{background:#315b7b;color:white;border-color:#315b7b}button:focus-visible,a:focus-visible,select:focus-visible{outline:3px solid #d99c3d}label{display:flex;flex-direction:column;gap:6px;font-size:12px;color:#425970}.filters{display:flex;gap:16px;flex-wrap:wrap;margin:18px 0}select{max-width:850px}#slice{width:min(900px,75vw)}.card{background:white;padding:22px;border:1px solid #dce3ea;border-radius:8px;margin:18px 0}.muted{color:#526579;font-size:13px}.scroll{overflow:auto;max-height:680px}table{border-collapse:collapse;width:100%;font-size:13px}th{background:#edf2f7;text-align:left;position:sticky;top:0;z-index:1}th,td{padding:12px;border-bottom:1px solid #dce3ea;vertical-align:top;min-width:110px}th:first-child,td:first-child{min-width:230px}td.num{min-width:155px}.cell{display:block;width:100%;text-align:left;border:0;padding:8px;background:transparent}.cell strong{font-size:16px}.cell small{display:block;color:#435d71;margin-top:5px}.track{height:7px;background:#e5ebf1;border-radius:3px;margin:6px 0}.fill{height:7px;background:#507ba0;border-radius:3px}.unavailable{color:#687989;background:#f4f5f6}.examples{font-size:11px;padding:5px}.pill{display:inline-block;border:1px solid #bccddd;padding:5px;margin:3px;border-radius:4px;font-size:12px}details{margin:12px 0}summary{cursor:pointer;font-weight:bold;padding:10px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f9;padding:15px;font-size:12px;max-height:420px;overflow:auto}dialog{border:1px solid #b5c5d4;border-radius:9px;width:min(1200px,95vw);max-height:93vh;padding:24px}dialog::backdrop{background:#12243699}.dialoghead{display:flex;justify-content:space-between;position:sticky;top:0;background:white;z-index:3}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}.pair article{background:#edf2f7;padding:16px}.ref{color:#21547f;text-decoration:underline;border:0;background:transparent;padding:3px}a{color:#21547f}.note{background:#fff7e9;border-left:3px solid #c59036;padding:12px}.pager{display:flex;gap:12px;align-items:center;margin:12px 0}.nowrap{white-space:nowrap}@media(max-width:700px){header,main{padding:18px}.pair{grid-template-columns:1fr}.strip{gap:16px}h1{font-size:24px}}@media print{nav,.filters,button{display:none}.scroll{max-height:none;overflow:visible}header{background:white;color:#20354a}}
</style></head><body><header><h1>Learning progressions evaluation</h1><p>Explore recorded decisions, rationale grounding and evaluator behavior. Agreement is not accuracy; sampled judge-positive coverage does not establish curriculum-wide recall.</p><div id="strip" class="strip"></div></header><main>
<nav id="nav" aria-label="Evaluation views"></nav><div class="filters"><label>Curriculum<select id="curriculum"></select></label><label>Separate cohort / condition / repetitions<select id="slice"></select></label></div>
<section class="card"><h2 id="viewTitle"></h2><p id="viewNote" class="muted"></p><div class="scroll" id="chart"></div></section>
<section class="card" id="gridSection" hidden><h2>Pair-by-condition grid</h2><p class="muted">Every recorded outcome is shown, with a separate link for each repetition. No majority replaces the full distribution. A/B are canonical endpoints. Missing scheduled judgments stay visible.</p><div class="filters"><label>Component<select id="gridComponent"></select></label><label>Condition<select id="gridCondition"></select></label><label>Find pair or statement<input id="gridSearch" type="search"></label></div><div class="scroll" id="grid"></div></section>
<section class="card"><h2>Underlying examples</h2><div class="filters"><label>Detail table<select id="tableSelect"></select></label><label>Find a reference, statement or finding<input id="search" type="search" placeholder="J0001, P0001, fraction…"></label></div><p id="selectedMembers" class="muted"></p><button id="clearMembers" hidden>Clear chart membership filter</button><div class="scroll" id="examples"></div><div class="pager"><button id="previous">Previous</button><span id="page"></span><button id="next">Next</button></div></section>
<details class="card"><summary>How to interpret and navigate this report</summary><div id="guide"></div></details>
<details class="card"><summary>Run details, source identities and complete saved data</summary><p>Source reports remain immutable. Official concern status is the status saved in this report; later disposition records are not automatically merged. Personal workbook notes never alter that status.</p><button id="download">Download complete presentation data</button><pre id="identity"></pre></details>
</main><dialog id="detail"><div class="dialoghead"><h2 id="detailTitle"></h2><button id="close">Close</button></div><div id="detailBody"></div></dialog><script id="data" type="application/octet-stream">__DATA__</script><script>
'use strict';
(async()=>{
const bytes=Uint8Array.from(atob(document.getElementById('data').textContent),c=>c.charCodeAt(0));
const decoded=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
const data=JSON.parse(decoded), tables=data.tables, views=data.views;
const $=id=>document.getElementById(id), esc=v=>String(v??'Unavailable').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const str=v=>typeof v==='object'?JSON.stringify(v):String(v??'Unavailable');
const pretty=v=>esc(JSON.stringify(v,null,2));
const refs=new Map(Object.values(tables).flat().filter(r=>r.Ref).map(r=>[r.Ref,r]));
const names=[...new Set(tables.Judgments.map(r=>r.Curriculum))].sort();
let view=0,page=0,membership=null;
function options(id,values){$(id).innerHTML=values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('')}
function link(ref){return `<button class="ref" data-ref="${esc(ref)}">${esc(ref)}</button>`}
function block(title,value){return `<details><summary>${esc(title)}</summary><pre>${pretty(value)}</pre></details>`}
function show(ref){const row=refs.get(ref);if(!row)return; $('detailTitle').textContent=ref+' · '+(row.Curriculum||'');let html='';
if(ref.startsWith('J')){html=`<p>${esc(row.Component)} / ${esc(row.Task)} / ${esc(row.Condition)} / repetition ${esc(row.Repetition)} / ${esc(row.Presentation)}</p><div class="pair"><article><h3>A · ${esc(row['Grade/stage A'])}</h3><p>${esc(row['Statement A'])}</p><small>${esc(row['Canonical A UUID'])}</small></article><article><h3>B · ${esc(row['Grade/stage B'])}</h3><p>${esc(row['Statement B'])}</p><small>${esc(row['Canonical B UUID'])}</small></article></div><p><b>Production:</b> ${esc(row['Production outcome'])}<br><b>Evaluator:</b> ${esc(row['Evaluator outcome'])}<br><b>Shown to judge:</b> ${esc(row['Judge-facing outcome'])}</p><p>${esc(row.Explanation)}</p><p>Concerns: ${(row.Concerns||[]).map(link).join(' ')}<br>Claims: ${(row.Claims||[]).map(link).join(' ')}<br>Comparisons: ${(row.Comparisons||[]).map(link).join(' ')}</p>`;
const payload=JSON.parse(row['Saved request'].evidence.payload_json);
html+=block('Operative production rationale',row['Rationale assessed']);
html+='<h3>Cited shown evidence</h3>'+(row['Source references']||[]).map(pointer=>{let value=payload;try{for(const token of pointer.slice(1).split('/'))value=value[token.replace(/~1/g,'/').replace(/~0/g,'~')]}catch{value='Unavailable'}return block(pointer,value)}).join('');
html+=block('Complete saved input: evidence and endpoint mapping',JSON.parse(row['Saved request'].prompt.user_message));html+=block('Complete saved system prompt',row['Saved request'].prompt.system_message);html+=block('Complete saved request, schema and material hashes',row['Saved request']);
}else if(ref.startsWith('X')){html=`<p>${esc(row.Kind)} · ${link(row['First judgment'])} → ${link(row['Second judgment'])}</p><p>${esc(row['First outcome'])} → ${esc(row['Second outcome'])}</p><p>Baseline repetition comparisons: ${(row['Baseline repetition comparisons']||[]).map(link).join(' ')||'Unavailable'}</p><p>Payload identical: ${esc(row['Payload identical'])}. Model-visible messages byte-identical: ${esc(row['Model-visible messages byte-identical'])}.</p><p class="note">Changed endpoint families: ${esc(str(row['Changed endpoint families']))}. Differences identify saved content changes, not additional pedagogical substance. Complete payload differences include metadata and batch-boundary changes.</p>`;
html+=block('Starting endpoint evidence',row['Starting endpoint evidence'])+block('Changed endpoint evidence',row['Changed endpoint evidence'])+block('Endpoint evidence differences by family',row['Endpoint differences'])+block('Complete exact payload differences',row['Complete payload differences']);
}else if(ref.startsWith('C')){html=`<p><b>${esc(row.Concern)}</b></p><p>${esc(row['Member judgments'])} member judgments; ${esc(row['Unique pairs/cases'])} unique pairs/cases. Concern groups overlap.</p><p>Official status in source report: ${esc(row['Official status in source report'])}</p><p>${row.Judgments.map(link).join(' ')}</p>`;
}else if(ref.startsWith('R')){html=`<p>${link(row.Judgment)} · ${esc(row.Pair)}</p><h3>${esc(row.Support)}</h3><p>${esc(row.Claim)}</p><p>${esc(row.Explanation)}</p>`}
html+=block('Complete detail record',row);$('detailBody').innerHTML=html;if(!$('detail').open)$('detail').showModal();history.replaceState(null,'','#'+ref)}
function strip(){const r=data.report,u=r.usage_summary,failed=tables.Judgments.reduce((n,r)=>n+r['Failed attempts'],0),recovered=tables.Judgments.filter(r=>r['Recovered failure']).length; const cost=u.cost.total_by_currency===null?'Unknown':str(u.cost.total_by_currency);
$('strip').innerHTML=[['Planned judgments',r.planned_judgments],['Valid / missing',r.valid_judgments+' / '+r.missing_judgments],['Failed attempts / recovered requests',failed+' / '+recovered],['Pending groups in saved report',tables.Concerns.filter(r=>r['Official status in source report']==='pending_user').length],['Total attempts',u.attempts],['Available cost',cost],['Excluded runs',r.excluded_runs]].map(([k,v])=>`<div class="stat"><strong>${esc(v)}</strong><span>${esc(k)}</span></div>`).join('');}
function selectView(index){view=index;membership=null;page=0;document.querySelectorAll('nav button').forEach((b,i)=>b.classList.toggle('active',i===index));options('slice',views[view].panels.map((p,i)=>i+' — '+p.label));$('gridSection').hidden=view!==3;draw();}
function draw(){const selected=Number($('slice').value.split(' — ')[0]),panel=views[view].panels[selected];$('viewTitle').textContent=views[view].title;
if(!panel){$('viewNote').textContent='Unavailable: no applicable recorded assessments.';$('chart').textContent='No applicable rows.';return}
$('viewNote').textContent=panel.note;
const rows=panel.rows.filter(r=>$('curriculum').value==='All curricula'||r.curriculum===$('curriculum').value),columns=panel.column_labels||panel.columns;
$('chart').innerHTML=`<table><thead><tr><th>Curriculum / production outcome</th>${columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map((r,ri)=>`<tr><td><b>${esc(r.label)}</b>${r.assessed!==undefined?`<p class="muted">Assessed ${r.assessed} / planned ${r.planned}${r.positive!==undefined?`; judge-positive ${r.positive}`:''}</p>`:''}</td>${r.cells.map((c,ci)=>`<td class="num ${c.rate===null?'unavailable':''}" style="${view===4&&c.rate!==null?`background:rgba(80,123,160,${0.08+c.rate*0.45})`:''}"><button class="cell" data-cell="${ri},${ci}" data-set="refs"><strong>${c.rate===null?'Unavailable':`${c.count} / ${c.denominator}`}</strong><div class="track"><div class="fill" style="width:${c.rate===null?0:c.rate*100}%"></div></div><small>${c.rate===null?'No valid denominator':(c.rate*100).toFixed(1)+'%'} · ${c.unique_pairs} numerator pairs/cases</small></button><button class="examples" data-cell="${ri},${ci}" data-set="denominator_refs">Denominator examples (${c.denominator})</button><button class="examples" data-cell="${ri},${ci}" data-set="planned_refs">Planned (${c.planned})</button></td>`).join('')}</tr>`).join('')}</tbody></table>`;
$('chart').onclick=e=>{const b=e.target.closest('[data-cell]');if(!b)return;const [ri,ci]=b.dataset.cell.split(',').map(Number),cell=rows[ri].cells[ci];membership=new Set(cell[b.dataset.set]);$('tableSelect').value=view===3?'Comparisons':'Judgments';page=0;examples();$('selectedMembers').scrollIntoView({block:'center',behavior:'smooth'})};grid();examples();}
function examples(){const table=$('tableSelect').value,search=$('search').value.toLowerCase();let records=tables[table].filter(r=>($('curriculum').value==='All curricula'||!r.Curriculum||r.Curriculum===$('curriculum').value)&&(!membership||membership.has(r.Ref))&&(!search||Object.entries(r).filter(([k])=>k!=='Saved request').some(([,v])=>str(v).toLowerCase().includes(search))));
const cols={Concerns:['Ref','Curriculum','Concern','Member judgments','Unique pairs/cases','Official status in source report'],Judgments:['Ref','Curriculum','Pair','Statement A','Statement B','Production outcome','Evaluator outcome','Component','Condition','Repetition'],Comparisons:['Ref','Curriculum','Pair','Kind','First judgment','Second judgment','First outcome','Second outcome','Changed endpoint families'], 'Rationale claims':['Ref','Curriculum','Judgment','Claim','Support','Explanation']}[table]||Object.keys(records[0]||{}).slice(0,9);
page=Math.max(0,Math.min(page,Math.ceil(records.length/30)-1));const shown=records.slice(page*30,page*30+30);$('examples').innerHTML=`<table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${shown.map(r=>`<tr>${cols.map(c=>`<td>${c==='Ref'?link(r[c]):esc(str(r[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table>`;$('page').textContent=`${records.length} matching records · page ${page+1} of ${Math.max(1,Math.ceil(records.length/30))}`;$('selectedMembers').textContent=membership?`Chart membership filter: ${membership.size} linked records. Other filters still apply.`:'Select a chart cell or denominator to inspect exact members.';$('clearMembers').hidden=!membership;$('previous').disabled=page===0;$('next').disabled=(page+1)*30>=records.length;}
function grid(){const rows=tables.Judgments.filter(r=>($('curriculum').value==='All curricula'||r.Curriculum===$('curriculum').value)&&($('gridComponent').value==='All components'||r.Component===$('gridComponent').value)&&($('gridCondition').value==='All conditions'||r.Condition===$('gridCondition').value)&&str([r.Pair,r['Statement A'],r['Statement B']]).toLowerCase().includes($('gridSearch').value.toLowerCase()));
const columns=[...new Set(rows.map(r=>r.Condition+' / '+r.Presentation))].sort(),groups=new Map();for(const r of rows){const key=r.Curriculum+' / '+r.Pair+' / '+r.Component;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r)}
$('grid').innerHTML=`<table><thead><tr><th>Curriculum / pair / component</th>${columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${[...groups].sort(([a],[b])=>a.localeCompare(b)).map(([k,rs])=>`<tr><td>${esc(k)}</td>${columns.map(c=>`<td>${rs.filter(r=>r.Condition+' / '+r.Presentation===c).map(r=>`<span class="pill">${link(r.Ref)} r${r.Repetition}: ${esc(r['Evaluator outcome'])}</span>`).join('')||'Not scheduled'}</td>`).join('')}</tr>`).join('')}</tbody></table>`;}
options('curriculum',['All curricula',...names]);options('tableSelect',['Concerns','Judgments','Rationale claims','Comparisons','Sampling','Metrics','Baselines','Run details']);options('gridComponent',['All components',...new Set(tables.Judgments.map(r=>r.Component))]);options('gridCondition',['All conditions',...new Set(tables.Judgments.map(r=>r.Condition))]);
$('nav').innerHTML=views.map((v,i)=>`<button data-view="${i}">${i+1}. ${esc(v.title)}</button>`).join('');$('nav').onclick=e=>{const b=e.target.closest('[data-view]');if(b)selectView(Number(b.dataset.view))};$('slice').onchange=()=>{membership=null;page=0;draw()};$('curriculum').onchange=()=>{membership=null;page=0;draw()};$('tableSelect').onchange=()=>{membership=null;page=0;examples()};$('search').oninput=()=>{page=0;examples()};$('previous').onclick=()=>{page--;examples()};$('next').onclick=()=>{page++;examples()};$('clearMembers').onclick=()=>{membership=null;examples()};['gridComponent','gridCondition','gridSearch'].forEach(id=>$(id).oninput=grid);$('close').onclick=()=>$('detail').close();document.addEventListener('click',e=>{const b=e.target.closest('[data-ref]');if(b)show(b.dataset.ref)});
$('guide').innerHTML=tables.Overview.map(r=>`<h3>${esc(r.Topic)}</h3><p>${esc(r.Explanation)}</p>`).join('');$('identity').textContent=JSON.stringify({source:data.identity,usage:data.report.usage_summary,settings:data.report.settings,model:data.report.judge,excluded_inputs:data.report.discovery},null,2);
$('download').onclick=()=>{const a=document.createElement('a');const url=URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'}));a.href=url;a.download='presentation_data.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};strip();selectView(0);if(location.hash)show(decodeURIComponent(location.hash.slice(1)));
})().catch(error=>{document.getElementById('viewTitle').textContent='Unable to open presentation data';document.getElementById('viewNote').textContent='Use a current browser with DecompressionStream support. '+error.message;});
</script></body></html>"""


def _baseline_comparison_refs(
    *, comparisons: list[dict[str, Any]], judgments: list[dict[str, Any]]
) -> dict[tuple[str, str, str], list[str]]:
    """Index repetition variability under each cohort's original baseline evidence.

    Parameters
    ----------
    comparisons
        Saved paired comparisons, including repeats within evidence variants.
    judgments
        Judgment records with exact schedule roles and presentations.

    Returns
    -------
    dict[tuple[str, str, str], list[str]]
        Baseline comparison references by unique pair, component and task. Repeated
        expanded, reduced or reordered evidence is excluded from baseline variability.
    """

    baseline = {
        row["Ref"]
        for row in judgments
        if row["Role"] in {"base", "identical_repeat", "critique", "control"}
        and row["Presentation"] == "canonical"
    }
    references: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    for row in comparisons:
        if (
            row["Kind"] == "identical_repetition"
            and row["First judgment"] in baseline
            and row["Second judgment"] in baseline
        ):
            references[(row["Pair"], row["Component"], row["Task"])].append(row["Ref"])

    return dict(references)


def _baseline_rows(
    *, names: dict[str, str], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build baseline rows while retaining saved memberships.

    Parameters
    ----------
    names
        Curriculum titles indexed by source document key.
    report
        Validated immutable score report.

    Returns
    -------
    list[dict[str, Any]]
        Flat records with exact references and unavailable values preserved.
    """

    baselines = []

    for item in report["baselines"]:
        base = {
            "Curriculum": names[item["dimensions"]["doc_key"]],
            **item["dimensions"],
        }

        for key, value in item.items():
            if key != "dimensions":
                baselines.extend(_value_rows(base=base, path=key, value=value))

    return baselines


def _claim_rows(
    *, by_id: dict[str, dict[str, Any]], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build claim rows while retaining saved memberships.

    Parameters
    ----------
    by_id
        Readable judgment records indexed by original request ID.
    report
        Validated immutable score report.

    Returns
    -------
    list[dict[str, Any]]
        Flat records with exact references and unavailable values preserved.
    """

    claims: list[dict[str, Any]] = []

    for row in report["assessments"]:
        for index, claim in enumerate((row["assessment"] or {}).get("claims", []), 1):
            parent = by_id[row["request_id"]]
            claims.append(
                {
                    "Ref": f"R{len(claims) + 1:05d}",
                    "Curriculum": parent["Curriculum"],
                    "Judgment": parent["Ref"],
                    "Pair": parent["Pair"],
                    "Claim number": index,
                    "Claim": claim["claim"],
                    "Support": claim["support"],
                    "Explanation": claim["explanation"],
                    "Component": row["component"],
                    "Condition": row["condition"],
                    "Repetition": row["replicate"],
                    "Source references": claim["evidence_references"],
                    "Request ID": row["request_id"],
                }
            )

    return claims


def _columns(records: list[dict[str, Any]]) -> list[str]:
    """Keep readable details ahead of technical identifiers and nested payloads.

    Parameters
    ----------
    records
        Complete flat presentation rows.

    Returns
    -------
    list[str]
        Ordered columns suitable for filtering and Google Sheets import.
    """

    omitted = {
        "Saved request",
        "Endpoint differences",
        "Complete payload differences",
        "Starting endpoint evidence",
        "Changed endpoint evidence",
    }
    keys = list(
        dict.fromkeys(key for row in records for key in row if key not in omitted)
    )
    technical = {
        "doc_key",
        "Document key",
        "Request ID",
        "Pair ID",
        "Concern ID",
        "Source",
        "Canonical A UUID",
        "Canonical B UUID",
        "Judge first endpoint",
        "Judge second endpoint",
        "Evidence hash",
    }
    return [key for key in keys if key not in technical] + [
        key for key in keys if key in technical
    ]


def _comparison_rows(
    *, judgments: list[dict[str, Any]], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Join saved comparisons to navigable judgments and actual input differences.

    Parameters
    ----------
    judgments
        Presentation records retaining complete saved requests.
    report
        Validated saved score report.

    Returns
    -------
    list[dict[str, Any]]
        Comparisons preserving the scorer's outcomes and missing states.
    """

    by_id = {row["Request ID"]: row for row in judgments}
    results = []

    for index, item in enumerate(report["comparisons"], 1):
        first = by_id[item["first_request_id"]]
        second = by_id[item["second_request_id"]]
        before = json.loads(first["Saved request"]["evidence"]["payload_json"])
        after = json.loads(second["Saved request"]["evidence"]["payload_json"])
        prompts = (first["Saved request"]["prompt"], second["Saved request"]["prompt"])
        changes = _differences(after=after, before=before)
        families = {
            key: _differences(
                after=_evidence_parts(
                    endpoint_uuids=first["Saved request"]["canonical_endpoint_uuids"],
                    payload=after,
                )[key],
                before=_evidence_parts(
                    endpoint_uuids=first["Saved request"]["canonical_endpoint_uuids"],
                    payload=before,
                )[key],
            )
            for key in _evidence_parts(
                endpoint_uuids=first["Saved request"]["canonical_endpoint_uuids"],
                payload=before,
            )
        }
        results.append(
            {
                "Ref": f"X{index:04d}",
                "Curriculum": first["Curriculum"],
                "Pair": first["Pair"],
                "Kind": item["kind"],
                "Task": first["Task"],
                "Component": item["component"],
                "First judgment": first["Ref"],
                "Second judgment": second["Ref"],
                "First outcome": first["Evaluator outcome"],
                "Second outcome": second["Evaluator outcome"],
                "Outcome changed": item["changed"],
                "First condition": item["first_condition"],
                "Second condition": item["second_condition"],
                "First presentation": item["first_presentation"],
                "Second presentation": item["second_presentation"],
                "First repetition": item["first_replicate"],
                "Second repetition": item["second_replicate"],
                "Payload identical": not changes,
                "Model-visible messages byte-identical": all(
                    prompts[0][k] == prompts[1][k]
                    for k in ("system_message", "user_message")
                ),
                "Changed endpoint families": [
                    key for key, value in families.items() if value
                ],
                "Starting endpoint evidence": _evidence_parts(
                    endpoint_uuids=first["Saved request"]["canonical_endpoint_uuids"],
                    payload=before,
                ),
                "Changed endpoint evidence": _evidence_parts(
                    endpoint_uuids=first["Saved request"]["canonical_endpoint_uuids"],
                    payload=after,
                ),
                "Endpoint differences": families,
                "Complete payload differences": changes,
                "Unavailable reason": item["unavailable_reason"],
                "First availability": first["Evidence availability"],
                "Second availability": second["Evidence availability"],
                "Pair ID": item["pair_id"],
                "First request ID": item["first_request_id"],
                "Second request ID": item["second_request_id"],
                "Source": f"lp_eval_report.json#/comparisons/{index - 1}",
            }
        )
    baseline_refs = _baseline_comparison_refs(comparisons=results, judgments=judgments)

    for row in results:
        row["Baseline repetition comparisons"] = baseline_refs.get(
            (row["Pair"], row["Component"], row["Task"]), []
        )

    return results


def _concern_rows(
    *, by_id: dict[str, dict[str, Any]], names: dict[str, str], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build concern rows while retaining saved memberships.

    Parameters
    ----------
    by_id
        Readable judgment records indexed by original request ID.
    names
        Curriculum titles indexed by source document key.
    report
        Validated immutable score report.

    Returns
    -------
    list[dict[str, Any]]
        Flat records with exact references and unavailable values preserved.
    """

    concerns = []

    for index, item in enumerate(report["concerns"], 1):
        members = [by_id[m["request_id"]] for m in item["members"]]
        concerns.append(
            {
                "Ref": f"C{index:04d}",
                "Curriculum": names[item["doc_key"]],
                "Concern": item["category"],
                "Member judgments": len(members),
                "Unique pairs/cases": len({m["Pair"] for m in members}),
                "Judgments": [m["Ref"] for m in members],
                "Pairs": sorted({m["Pair"] for m in members}),
                "Official status in source report": item["disposition"],
                "Authority": item["authority"],
                "Personal notes (not a disposition)": "",
                "Concern ID": item["concern_id"],
                "Source": f"lp_eval_report.json#/concerns/{index - 1}",
            }
        )

    return concerns


def _control_consistency(
    cases: list[tuple[tuple[Any, ...], list[dict[str, Any]]]],
) -> dict[str, Any]:
    """Compare all recorded repetitions of each unique synthetic case.

    Parameters
    ----------
    cases
        Case groups retaining missing scheduled judgments.

    Returns
    -------
    dict[str, Any]
        Consistency counts for fully assessed cases with at least two repetitions.
    """

    eligible_cases = [
        rows
        for _, rows in cases
        if len(rows) >= 2 and all(r["Evaluator outcome"] != "Unavailable" for r in rows)
    ]
    stable = [
        rows
        for rows in eligible_cases
        if len({r["Evaluator outcome"] for r in rows}) == 1
    ]
    return {
        "count": len(stable),
        "denominator": len(eligible_cases),
        "rate": (len(stable) / len(eligible_cases) if eligible_cases else None),
        "planned": len(cases),
        "unique_pairs": len(stable),
        "refs": [r["Ref"] for rows in stable for r in rows],
        "denominator_refs": [r["Ref"] for rows in eligible_cases for r in rows],
        "planned_refs": [r["Ref"] for _, rows in cases for r in rows],
    }


def _control_panels(
    *, judgments: list[dict[str, Any]], names: list[str]
) -> list[dict[str, Any]]:
    """Build control panels from the exact saved observation slices.

    Parameters
    ----------
    judgments
        Full readable judgment ledger.
    names
        Consistent curriculum display order.

    Returns
    -------
    list[dict[str, Any]]
        Panels preserving counts, denominators, repetitions and unavailable cells.
    """

    controls = [r for r in judgments if r["Component"] == "controls"]
    families = sorted({r["Control family"] for r in controls})
    panels: list[dict[str, Any]] = []

    for (repetition,), rows in _group(rows=controls, fields=("Repetition",)):
        records = []

        for name in names:
            cells = []

            for family in families:
                planned = [
                    r
                    for r in rows
                    if r["Curriculum"] == name and r["Control family"] == family
                ]
                valid = [r for r in planned if r["Control match"] is not None]
                cells.append(
                    _view_cell(
                        denominator=valid,
                        members=[r for r in valid if r["Control match"]],
                        planned=planned,
                    )
                )

            records.append({"curriculum": name, "label": name, "cells": cells})

        panels.append(
            {
                "label": f"Constructed-expectation matches · repetition {repetition}",
                "columns": families,
                "rows": records,
                ("note"): (
                    "Counts are synthetic case judgments within one replicate; these are "
                    "constructed expectations, never real-curriculum accuracy. Click "
                    "denominators to inspect nonmatches too."
                ),
            }
        )

    records = []

    for name in names:
        cells = []

        for family in families:
            cases = _group(
                rows=[
                    r
                    for r in controls
                    if r["Curriculum"] == name and r["Control family"] == family
                ],
                fields=("Pair",),
            )
            cells.append(_control_consistency(cases))

        records.append({"curriculum": name, "label": name, "cells": cells})

    panels.append(
        {
            "label": "Repeated-assessment consistency · unique synthetic cases",
            "columns": families,
            "rows": records,
            ("note"): (
                "Numerator: cases with the same recorded outcome on every repetition. "
                "Denominator: cases with at least two planned judgments and all "
                "judgments valid. One repetition or missing judgments makes consistency"
                " unavailable. Examples retain all judgments; no majority answer "
                "replaces them."
            ),
        }
    )
    return panels


def _coverage(*, names: list[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Separate independent uniform and overlapping diagnostic coverage by replicate.

    Parameters
    ----------
    names
        Consistent curriculum order.
    rows
        Readable judgments.

    Returns
    -------
    list[dict[str, Any]]
        Four exclusive publication categories per judge-positive denominator.
    """

    panels: list[dict[str, Any]] = []
    base = [
        r
        for r in rows
        if r["Component"] == "independent"
        and r["Role"] == "base"
        and r["Condition"] == "reconstructed_bounded_upstream"
    ]
    categories = [
        "Never nominated",
        "Nominated but unpublished",
        "Published differing relation/direction",
        "Published matching",
    ]

    for repetition in sorted({r["Repetition"] for r in base}):
        for cohort in (
            "Uniform probability sample",
            "Diagnostic selections (overlapping union)",
        ):
            records = []

            for name in names:
                selected = [
                    r
                    for r in base
                    if r["Curriculum"] == name
                    and r["Repetition"] == repetition
                    and any(
                        (
                            route == "independent/uniform"
                            if cohort.startswith("Uniform")
                            else route.startswith("independent/tag/")
                        )
                        for route in r["Routes"]
                    )
                ]
                valid = [r for r in selected if r["Evaluator outcome"] != "Unavailable"]
                positive = [
                    r
                    for r in valid
                    if r["Evaluator outcome"].startswith(("buildsTowards", "relatesTo"))
                ]
                buckets: dict[str, list[dict[str, Any]]] = {
                    key: [] for key in categories
                }

                for row in positive:
                    if row["Production outcome"] == "Never nominated":
                        category = categories[0]
                    elif row["Published relationship UUID"] is None:
                        category = categories[1]
                    elif row["Production outcome"] != row["Evaluator outcome"]:
                        category = categories[2]
                    else:
                        category = categories[3]
                    buckets[category].append(row)

                records.append(
                    {
                        "curriculum": name,
                        "label": name,
                        "assessed": len(valid),
                        "planned": len(selected),
                        "positive": len(positive),
                        "cells": [
                            _view_cell(
                                denominator=positive,
                                members=buckets[key],
                                planned=selected,
                            )
                            for key in categories
                        ],
                    }
                )

            panels.append(
                {
                    "label": f"{cohort} · base repetition {repetition}",
                    "columns": categories,
                    "rows": records,
                    ("note"): (
                        "Each cell divides by valid judge-positive unique pairs in this "
                        "cohort/repetition. Assessed includes positives, negatives and "
                        "ambiguity. Diagnostic selections can overlap the uniform sample; never"
                        " add the cohorts."
                    ),
                }
            )

    return panels


def _decision_panels(
    *, judgments: list[dict[str, Any]], names: list[str]
) -> list[dict[str, Any]]:
    """Build decision panels from the exact saved observation slices.

    Parameters
    ----------
    judgments
        Full readable judgment ledger.
    names
        Consistent curriculum display order.

    Returns
    -------
    list[dict[str, Any]]
        Panels preserving counts, denominators, repetitions and unavailable cells.
    """

    decisions = [
        "buildsTowards: A → B",
        "buildsTowards: B → A",
        "relatesTo",
        "no_relation",
        "Ambiguous",
    ]
    panels: list[dict[str, Any]] = []
    production = [
        r
        for r in judgments
        if r["Component"] == "production"
        and r["Role"] == "base"
        and r["Condition"] == "original_production_blind"
    ]

    for (repetition,), rows in _group(rows=production, fields=("Repetition",)):
        records = []

        for name in names:
            for outcome in decisions[:-1] + ["needs_review"]:
                selected = [
                    r
                    for r in rows
                    if r["Curriculum"] == name and r["Production outcome"] == outcome
                ]
                record = _distribution(
                    columns=decisions,
                    field="Evaluator outcome",
                    names=[name],
                    rows=selected,
                )[0]
                record["label"] = name + " / " + outcome
                records.append(record)

        panels.append(
            {
                "label": f"Original production evidence · base repetition {repetition}",
                "columns": decisions,
                "rows": records,
                ("note"): (
                    "Rows are production decisions; columns are blind evaluator decisions. "
                    "Each denominator is the valid assessed production row, not all "
                    "curriculum pairs. A/B use canonical UUID order, not learning order. "
                    "Empty production rows are unavailable."
                ),
            }
        )

    return panels


def _destination(
    *,
    identity: dict[str, Any],
    output_directory: Path | None,
    report: dict[str, Any],
    source: Path,
) -> Path:
    """Reject presentation paths that overlap immutable evaluation or production evidence.

    Parameters
    ----------
    identity
        Source and rendered output identity for the default content-addressed
        destination.
    output_directory
        Optional isolated destination selected by the caller.
    report
        Saved report with discovery boundaries.
    source
        Immutable report directory.

    Returns
    -------
    Path
        Unaliased, disjoint presentation destination.

    Raises
    ------
    ValueError
        If output aliases or overlaps evidence.
    """

    default = (
        source.parent.parent
        / "presentations"
        / source.name
        / lp_material_content_hash(identity)
    )
    destination = (
        output_directory.absolute() if output_directory is not None else default
    )
    _store_path(destination)
    inventory = TypeAdapter(DiscoveryInventory).validate_json(
        canonical_lp_json(report["discovery"])
    )
    _frozen_output_boundary(inventory=inventory, output=destination)

    evaluation_root = inventory.evaluation_root

    if destination.is_relative_to(evaluation_root) and destination != default:
        raise ValueError(
            "Custom presentation destinations must be outside the immutable evaluation tree."
        )

    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Presentation output overlaps source report.")

    return destination


def _diagnostic_cells(
    *, measure: str, name: str, rows: list[dict[str, Any]], tags: list[str], task: str
) -> list[dict[str, Any]]:
    """Count an overlapping tag slice without treating absent comparisons as negatives.

    Parameters
    ----------
    measure
        Named grounding or disagreement diagnostic.
    name
        Curriculum display name.
    rows
        One exact cohort, condition and replicate slice.
    tags
        Available and absent tag columns.
    task
        Classification or rationale critique.

    Returns
    -------
    list[dict[str, Any]]
        Membership-bound cells, unavailable when no valid denominator exists.
    """

    cells = []

    for tag in tags:
        planned = [r for r in rows if r["Curriculum"] == name and tag in r["Tags"]]
        valid = [
            r
            for r in planned
            if r["Evaluator outcome"] != "Unavailable"
            and (task == "critique" or r["Production outcome"] != "Never nominated")
        ]
        members = (
            [r for r in valid if r["Evaluator outcome"] == measure]
            if task == "critique"
            else [r for r in valid if r["Evaluator outcome"] != r["Production outcome"]]
        )
        cells.append(_view_cell(denominator=valid, members=members, planned=planned))

    return cells


def _diagnostic_panels(
    *, judgments: list[dict[str, Any]], names: list[str], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build diagnostic panels from the exact saved observation slices.

    Parameters
    ----------
    judgments
        Full readable judgment ledger.
    names
        Consistent curriculum display order.
    report
        Saved report with sampling population metadata.

    Returns
    -------
    list[dict[str, Any]]
        Panels preserving counts, denominators, repetitions and unavailable cells.
    """

    grounding = ["Fully grounded", "Partly grounded", "Unsupported", "Ambiguous"]
    panels: list[dict[str, Any]] = []
    eligible = [
        r
        for r in judgments
        if r["Component"] != "controls" and r["Role"] in ("base", "critique")
    ]
    tags = sorted(
        {tag for r in eligible for tag in r["Tags"]}
        | {
            cell["route"].split("/tag/", 1)[1]
            for sample in report["sampling"]
            for cell in sample["cells"]
            if "/tag/" in cell["route"]
        }
    )

    for key, rows in _group(
        rows=eligible, fields=("Component", "Task", "Condition", "Repetition")
    ):
        measures = grounding if key[1] == "critique" else ["Production disagreement"]

        for measure in measures:
            records = []

            for name in names:
                cells = _diagnostic_cells(
                    measure=measure, name=name, rows=rows, tags=tags, task=key[1]
                )
                records.append({"curriculum": name, "label": name, "cells": cells})

            panels.append(
                {
                    "label": " · ".join(map(str, (*key, measure))),
                    "columns": tags,
                    "rows": records,
                    "note": (
                        "Tags overlap. Rates describe assessed tagged pairs in this "
                        "exact cohort/condition/repetition, not population estimates. "
                        "Production-disagreement denominators exclude never-nominated "
                        "pairs. No members or no valid comparison is unavailable, not "
                        "zero performance."
                    ),
                }
            )

    return panels


def _differences(*, after: Any, before: Any, path: str = "") -> list[dict[str, Any]]:
    """Retain exact field changes, including list order and absent versus null values.

    Parameters
    ----------
    after
        Later saved value.
    before
        Starting saved value.
    path
        JSON pointer prefix.

    Returns
    -------
    list[dict[str, Any]]
        Complete changed values; no semantic significance is inferred from a change.
    """

    if _json_equal(after=after, before=before):
        return []

    if isinstance(before, dict) and isinstance(after, dict):
        changes = []

        for key in sorted(before.keys() | after.keys()):
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")

            if key not in before or key not in after:
                changes.append(
                    {
                        "path": pointer,
                        "before_present": key in before,
                        "after_present": key in after,
                        "before": before.get(key),
                        "after": after.get(key),
                    }
                )
            else:
                changes.extend(
                    _differences(after=after[key], before=before[key], path=pointer)
                )

        return changes

    return [
        {
            "path": path or "/",
            "before_present": True,
            "after_present": True,
            "before": before,
            "after": after,
        }
    ]


def _distribution(
    *, columns: list[Any], field: str, names: list[str], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Count outcomes for each curriculum with an explicit valid denominator.

    Parameters
    ----------
    columns
        Categories in semantic display order.
    field
        Outcome field in each source row.
    names
        Consistent curriculum order.
    rows
        One exact cohort/condition/replicate slice.

    Returns
    -------
    list[dict[str, Any]]
        Curriculum rows retaining missing-data counts and examples.
    """

    records = []

    for name in names:
        planned = [r for r in rows if r["Curriculum"] == name]
        valid = [
            r for r in planned if r[field] != "Unavailable" and r[field] is not None
        ]
        records.append(
            {
                "curriculum": name,
                "label": name,
                "assessed": len(valid),
                "planned": len(planned),
                "cells": [
                    _view_cell(
                        denominator=valid,
                        members=[r for r in valid if r[field] == category],
                        planned=planned,
                    )
                    for category in columns
                ],
            }
        )

    return records


def _evidence_parts(
    *, endpoint_uuids: list[str], payload: dict[str, Any]
) -> dict[str, Any]:
    """Describe endpoint evidence separately from transport and structural metadata.

    Parameters
    ----------
    endpoint_uuids
        Assessed canonical endpoints; other batch members remain in the full diff.
    payload
        Exact saved evidence, possibly wrapped for rationale critique.

    Returns
    -------
    dict[str, Any]
        Text, LC, hierarchy and remaining endpoint metadata keyed by stable UUID.
    """

    result: dict[str, Any] = {
        key: {}
        for key in (
            "Statement text",
            "Learning Components",
            "Hierarchy",
            "Metadata and source evidence (may contain substantive text)",
        )
    }

    for sfi in payload.get("original_request", payload).get("sfis", []):
        context = sfi.get("context", sfi)
        identifier = context["sfi_uuid"]

        if identifier not in endpoint_uuids:
            continue

        description = context.get("description")
        result["Statement text"][identifier] = (
            description.get("text") if isinstance(description, dict) else description
        )
        result["Learning Components"][identifier] = (
            [
                {
                    "identifier": lc.get("identifier"),
                    "description": _shown_text(lc.get("description")),
                }
                for lc in sfi.get("learning_components", [])
            ]
            if "learning_components" in sfi
            else None
        )
        result["Hierarchy"][identifier] = {
            "ancestors": [
                {
                    key: _shown_text(ancestor.get(key))
                    for key in (
                        "sfi_uuid",
                        "description",
                        "statement_code",
                        "statement_type",
                    )
                }
                for ancestor in sfi.get("ancestors", [])
            ],
            "paths": sfi.get("ancestor_paths"),
            "parents": sfi.get("parent_sfi_uuids"),
        }
        result["Metadata and source evidence (may contain substantive text)"][
            identifier
        ] = {key: value for key, value in sfi.items() if key not in ("description",)}

        if (
            "context"
            in result["Metadata and source evidence (may contain substantive text)"][
                identifier
            ]
        ):
            result["Metadata and source evidence (may contain substantive text)"][
                identifier
            ]["context"] = {
                key: value for key, value in context.items() if key != "description"
            }

    return result


def _grounding_panels(
    *, judgments: list[dict[str, Any]], names: list[str]
) -> list[dict[str, Any]]:
    """Build grounding panels from the exact saved observation slices.

    Parameters
    ----------
    judgments
        Full readable judgment ledger.
    names
        Consistent curriculum display order.

    Returns
    -------
    list[dict[str, Any]]
        Panels preserving counts, denominators, repetitions and unavailable cells.
    """

    grounding = ["Fully grounded", "Partly grounded", "Unsupported", "Ambiguous"]
    critiques = [
        r
        for r in judgments
        if r["Component"] == "production"
        and r["Task"] == "critique"
        and r["Condition"] == "original_production_critique"
    ]
    panels = [
        {
            "label": f"Original rationale critique · repetition {key[0]}",
            "columns": grounding,
            "rows": _distribution(
                columns=grounding, field="Evaluator outcome", names=names, rows=rows
            ),
            ("note"): (
                "Grounding of the operative rationale against original shown production"
                " evidence. Counts are critiques, distinct from relationship agreement."
                " Claim-support detail is available through each judgment."
            ),
        }
        for key, rows in _group(rows=critiques, fields=("Repetition",))
    ]
    return panels


def _group(
    *, fields: tuple[str, ...], rows: list[dict[str, Any]]
) -> list[tuple[tuple[Any, ...], list[dict[str, Any]]]]:
    """Partition rows by all supplied dimensions, without combining repetitions.

    Parameters
    ----------
    fields
        Complete dimensions defining an interpretable comparison.
    rows
        Saved presentation records.

    Returns
    -------
    list[tuple[tuple[Any, ...], list[dict[str, Any]]]]
        Deterministically ordered partitions.
    """

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        groups[tuple(row[field] for field in fields)].append(row)

    return sorted(groups.items(), key=lambda item: str(item[0]))


def _json_equal(*, after: Any, before: Any) -> bool:
    """Compare saved JSON recursively without equating Booleans and numbers.

    Parameters
    ----------
    after
        Later saved JSON value.
    before
        Starting saved JSON value.

    Returns
    -------
    bool
        Whether values agree, preserving list order and ignoring object key order.
    """

    if isinstance(before, bool) or isinstance(after, bool):
        return type(before) is type(after) and before == after

    if isinstance(before, dict) and isinstance(after, dict):
        return before.keys() == after.keys() and all(
            _json_equal(after=after[key], before=before[key]) for key in before
        )

    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(
            _json_equal(after=later, before=earlier)
            for earlier, later in zip(before, after)
        )

    return before == after


def _judgment_row(
    *,
    index: int,
    pair_ref: str,
    request: dict[str, Any],
    row: dict[str, Any],
    title: str,
) -> dict[str, Any]:
    """Present one recorded judgment in canonical and judge-facing coordinates.

    Parameters
    ----------
    index
        Stable ordinal in the saved assessment ledger.
    pair_ref
        Navigation reference for the unique curriculum pair.
    request
        Full saved request.
    row
        Saved scored judgment.
    title
        Human-readable curriculum name.

    Returns
    -------
    dict[str, Any]
        Readable detail with original identities and full saved input.
    """

    payload = json.loads(request["evidence"]["payload_json"])
    sfis = {
        sfi.get("context", sfi)["sfi_uuid"]: sfi
        for sfi in payload.get("original_request", payload).get("sfis", [])
    }
    first, second = (sfis[identifier] for identifier in row["endpoint_uuids"])
    assessment = row["assessment"] or {}
    production = row["production"] or {}
    endpoints = []

    for sfi in (first, second):
        context = sfi.get("context", sfi)
        description = context.get("description")
        endpoints.append(
            (
                (
                    description.get("text")
                    if isinstance(description, dict)
                    else description
                ),
                sfi.get("coordinate", {}).get("canonical_value"),
            )
        )

    return {
        "Ref": f"J{index:04d}",
        "Curriculum": title,
        "Pair": pair_ref,
        "Statement A": endpoints[0][0],
        "Grade/stage A": endpoints[0][1],
        "Statement B": endpoints[1][0],
        "Grade/stage B": endpoints[1][1],
        "Production outcome": (
            _outcome(
                decision=production.get("outcome"),
                direction=production.get("direction"),
            )
            if production
            else (
                "Synthetic control"
                if row["component"] == "controls"
                else "Never nominated"
            )
        ),
        "Evaluator outcome": _outcome(
            decision=assessment.get("decision", assessment.get("grounding")),
            direction=assessment.get("direction"),
        ),
        "Explanation": assessment.get("explanation"),
        "Component": row["component"],
        "Task": row["task"],
        "Condition": row["condition"],
        "Repetition": row["replicate"],
        "Presentation": row["presentation"],
        "Role": row["role"],
        "Rationale assessed": payload.get("operative_judgment", {}).get("rationale"),
        "Source references": assessment.get(
            "evidence_references",
            [
                ref
                for claim in assessment.get("claims", [])
                for ref in claim["evidence_references"]
            ],
        ),
        "Judge first endpoint": request["evidence"]["endpoint_uuids"][0],
        "Judge second endpoint": request["evidence"]["endpoint_uuids"][1],
        "Judge-facing outcome": _outcome(
            decision=(row["displayed_assessment"] or {}).get(
                "decision", (row["displayed_assessment"] or {}).get("grounding")
            ),
            direction=(row["displayed_assessment"] or {}).get("direction"),
            labels=("Judge first", "Judge second"),
        ),
        "Canonical A UUID": row["endpoint_uuids"][0],
        "Canonical B UUID": row["endpoint_uuids"][1],
        "Attempts": row["attempts"],
        "Failed attempts": row["failed_attempts"],
        "Execution state": row["execution_state"],
        "Recovered failure": row["failed_attempts"] > 0
        and row["assessment"] is not None,
        "Routes": row["routes"],
        "Tags": row["tags"],
        "Evidence availability": row["evidence_availability"],
        "Control family": row["control_family"],
        "Constructed expectation": row["control_expectation"],
        "Control match": row["control_match"],
        "Checker outcome": production.get("checker_outcome"),
        "Producer outcome": _outcome(
            decision=production.get("producer_outcome"),
            direction=production.get("producer_direction"),
        ),
        "Producer-to-final changes": production.get("producer_to_final_changes"),
        "Published relationship UUID": production.get("published_relationship_uuid"),
        "Request ID": row["request_id"],
        "Pair ID": row["pair_id"],
        "Document key": row["doc_key"],
        "Evidence hash": row["evidence_content_hash"],
        "Production request ID": production.get("request_id"),
        "Source": f"lp_eval_report.json#/assessments/{index - 1}",
        "Saved request": request,
    }


def _judgment_rows(
    *, report: dict[str, Any], requests: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build a readable ledger with canonical and judge-facing directions intact.

    Parameters
    ----------
    report
        Saved scored assessment ledger.
    requests
        Validated saved requests indexed by original ID.

    Returns
    -------
    list[dict[str, Any]]
        Navigable judgment records, including missing scheduled assessments.
    """

    pairs = sorted({(r["doc_key"], r["pair_id"]) for r in report["assessments"]})
    pair_refs = {key: f"P{index:04d}" for index, key in enumerate(pairs, 1)}
    titles = {}

    for request in requests.values():
        payload = json.loads(request["evidence"]["payload_json"])
        title = payload.get("original_request", payload).get("framework_title")

        if title:
            titles[request["doc_key"]] = (
                title.get("text") if isinstance(title, dict) else title
            )

    rows = []

    for index, row in enumerate(report["assessments"], 1):
        title = titles.get(row["doc_key"], row["framework_uuid"])

        if sum(value == title for value in titles.values()) > 1:
            title += " (" + row["doc_key"] + ")"

        rows.append(
            _judgment_row(
                index=index,
                pair_ref=pair_refs[(row["doc_key"], row["pair_id"])],
                request=requests[row["request_id"]],
                row=row,
                title=title,
            )
        )

    return rows


def _load_report(
    reference: EvaluationReportArtifacts,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Load hash-checked saved report material without execution-store effects.

    Parameters
    ----------
    reference
        Pinned immutable report directory and manifest digest.

    Returns
    -------
    tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]
        Verified manifest, report and requests.

    Raises
    ------
    ValueError
        If the source changes or saved material is inconsistent.
    """

    manifest = _validated_report_manifest(reference)
    files = {
        name: _store_read(reference.directory / name) for name in manifest["files"]
    }

    if any(
        hashlib.sha256(payload).hexdigest() != manifest["files"][name]["sha256"]
        for name, payload in files.items()
    ):
        raise ValueError("Report changed while being loaded.")

    report = json.loads(files["lp_eval_report.json"])
    requests = _validate_requests(
        report=report,
        requests=[
            json.loads(line) for line in files["lp_eval_requests.jsonl"].splitlines()
        ],
    )

    if (
        report["schedule_content_hash"] != manifest["schedule_content_hash"]
        or report["cache_content_hash"] != manifest["cache_content_hash"]
    ):
        raise ValueError("Report material identity differs from its manifest.")

    judgments = [
        json.loads(line) for line in files["lp_eval_judgments.jsonl"].splitlines()
    ]

    if judgments != [
        row for row in report["assessments"] if row["assessment"] is not None
    ]:
        raise ValueError("Saved judgment projection differs from the report.")

    report["saved_failure_events"] = [
        json.loads(line) for line in files["lp_eval_failures.jsonl"].splitlines()
    ]
    return manifest, report, requests


def _metric_rows(
    *, names: dict[str, str], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build metric rows while retaining saved memberships.

    Parameters
    ----------
    names
        Curriculum titles indexed by source document key.
    report
        Validated immutable score report.

    Returns
    -------
    list[dict[str, Any]]
        Flat records with exact references and unavailable values preserved.
    """

    metrics = []

    for item in report["metrics"]:
        dimensions = item["dimensions"]
        base = {
            "Curriculum": names[dimensions["doc_key"]],
            **dimensions,
            "Planned": item["planned"],
            "Valid": item["valid"],
            "Missing": item["missing"],
        }

        for key, value in item.items():
            if key in ("dimensions", "planned_request_ids"):
                continue

            metrics.extend(_value_rows(base=base, path=key, value=value))

    return metrics


def _outcome(
    *, decision: str | None, direction: str | None, labels: tuple[str, str] = ("A", "B")
) -> str:
    """Format a saved decision without implying order for symmetric relations.

    Parameters
    ----------
    decision
        Relation, grounding category or unavailable judgment.
    direction
        Direction in the supplied endpoint coordinate system.
    labels
        Names for first and second endpoints.

    Returns
    -------
    str
        Explicit outcome and direction.
    """

    if decision is None:
        return "Unavailable"

    if direction:
        source, target = labels if direction == "first_to_second" else labels[::-1]
        return f"{decision}: {source} → {target}"

    return {
        "grounded": "Fully grounded",
        "partially_grounded": "Partly grounded",
        "unsupported": "Unsupported",
        "ambiguous": "Ambiguous",
    }.get(decision, decision)


def _robustness_panels(
    *, names: list[str], tables: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Build robustness panels from the exact saved observation slices.

    Parameters
    ----------
    names
        Consistent curriculum display order.
    tables
        Linked comparison and detail tables.

    Returns
    -------
    list[dict[str, Any]]
        Panels preserving counts, denominators, repetitions and unavailable cells.
    """

    panels: list[dict[str, Any]] = []
    fields = (
        "Component",
        "Kind",
        "Task",
        "First condition",
        "Second condition",
        "First presentation",
        "Second presentation",
        "First repetition",
        "Second repetition",
    )

    for key, rows in _group(rows=tables["Comparisons"], fields=fields):
        panels.append(
            {
                "label": " · ".join(map(str, key)),
                "columns": [False, True],
                "column_labels": ["Same outcome", "Changed outcome"],
                "rows": _distribution(
                    columns=[False, True],
                    field="Outcome changed",
                    names=names,
                    rows=rows,
                ),
                ("note"): (
                    "Paired comparisons, split by cohort, condition, presentation and both "
                    "repetition numbers. Each X example links its baseline repetition "
                    "comparisons and exact before/after evidence. A changed outcome is not "
                    "proof of a production defect."
                ),
            }
        )

    return panels


def _row(
    *,
    body: Format,
    index: int,
    layout: tuple[list[str], list[int]],
    record: dict[str, Any],
    sheet: Worksheet,
) -> None:
    """Write a compact wrapped row with typed values and local HTML navigation.

    Parameters
    ----------
    body
        Workbook cell format.
    index
        One-based data row in zero-based worksheet coordinates.
    layout
        Ordered detail columns and corresponding display widths.
    record
        Complete presentation record.
    sheet
        Destination worksheet.
    """

    columns, widths = layout
    height = 30

    for col, key in enumerate(columns):
        value = _workbook_cell(value=record.get(key), width=widths[col])
        height = max(
            height, 15 * (math.ceil(len(str(value)) / max(1, widths[col] - 3)) + 1)
        )

        if isinstance(value, bool):
            sheet.write_boolean(boolean=value, cell_format=body, col=col, row=index)
        elif isinstance(value, (int, float)):
            sheet.write_number(cell_format=body, col=col, number=value, row=index)
        else:
            sheet.write_string(cell_format=body, col=col, row=index, string=str(value))

        if key == "Ref":
            sheet.write_url(
                cell_format=body,
                col=col,
                row=index,
                string=str(value),
                url="external:report.html#" + str(value),
            )

    sheet.set_row(height=min(120, height), row=index)


def _sampling_rows(
    *, names: dict[str, str], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Expose sampling populations, shortfalls and overlapping selections.

    Parameters
    ----------
    names
        Curriculum names keyed by document identity.
    report
        Saved sampling cells and memberships.

    Returns
    -------
    list[dict[str, Any]]
        Flat sampling records with exact inclusion probabilities where available.
    """

    sampling = []

    for item in report["sampling"]:
        for cell in item["cells"] + item["diagnostic_cells"]:
            sampling.append(
                {
                    "Curriculum": names[item["doc_key"]],
                    **cell,
                    "Production/independent overlap pair IDs": item["overlap_pair_ids"],
                }
            )

    return sampling


def _shown_text(value: Any) -> Any:
    """Extract bounded text without mistaking its hash metadata for added content.

    Parameters
    ----------
    value
        Plain or bounded text from the saved payload.

    Returns
    -------
    Any
        Shown text, preserving missing values.
    """

    return value.get("text") if isinstance(value, dict) else value


def _tables(
    *,
    judgments: list[dict[str, Any]],
    manifest: dict[str, Any],
    report: dict[str, Any],
    source_hash: str,
) -> dict[str, list[dict[str, Any]]]:
    """Create flat spreadsheet tables and complete HTML detail records.

    Parameters
    ----------
    judgments
        Human-readable ledger.
    manifest
        Source manifest with material identities.
    report
        Saved score report.
    source_hash
        Source report manifest hash.

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        Ordered sheets without any new official dispositions or scores.
    """

    names = {row["Document key"]: row["Curriculum"] for row in judgments}
    concerns = _concern_rows(
        by_id={row["Request ID"]: row for row in judgments}, names=names, report=report
    )
    claims = _claim_rows(
        by_id={row["Request ID"]: row for row in judgments}, report=report
    )
    comparisons = _comparison_rows(judgments=judgments, report=report)

    for row in judgments:
        row["Concerns"] = [c["Ref"] for c in concerns if row["Ref"] in c["Judgments"]]
        row["Claims"] = [c["Ref"] for c in claims if c["Judgment"] == row["Ref"]]
        row["Comparisons"] = [
            c["Ref"]
            for c in comparisons
            if row["Ref"] in (c["First judgment"], c["Second judgment"])
        ]

    sampling = _sampling_rows(names=names, report=report)
    metrics = _metric_rows(names=names, report=report)
    baselines = _baseline_rows(names=names, report=report)
    overview = [
        {"Topic": topic, "Explanation": explanation} for topic, explanation in _GUIDE
    ]
    overview[1:1] = [
        {"Topic": key.replace("_", " ").capitalize(), "Explanation": report[key]}
        for key in (
            "planned_judgments",
            "valid_judgments",
            "missing_judgments",
            "execution_complete",
            "excluded_runs",
        )
    ]
    overview.append({"Topic": "Source manifest SHA-256", "Explanation": source_hash})
    details = []

    for key in (
        "settings",
        "judge",
        "usage_summary",
        "discovery",
        "provenance",
        "limitations",
        "execution_errors",
        "saved_failure_events",
    ):
        details.extend(
            _value_rows(
                base={
                    "Source": (
                        "lp_eval_failures.jsonl"
                        if key == "saved_failure_events"
                        else "lp_eval_report.json"
                    )
                },
                path=key,
                value=report.get(key),
            )
        )

    details.extend(
        _value_rows(
            base={"Source": "lp_eval_manifest.json"}, path="manifest", value=manifest
        )
    )
    return {
        "Overview": overview,
        "Concerns": concerns,
        "Judgments": judgments,
        "Rationale claims": claims,
        "Comparisons": comparisons,
        "Sampling": sampling,
        "Metrics": metrics,
        "Baselines": baselines,
        "Run details": details,
    }


def _validate_assessment(*, request: ScheduledRequest, row: dict[str, Any]) -> None:
    """Revalidate the saved displayed response and its canonical direction mapping.

    Parameters
    ----------
    request
        Typed saved request with its shown evidence and references.
    row
        Saved assessment, including explicit missing-response state.

    Raises
    ------
    ValueError
        If the response, grounding, citations or canonical mapping is inconsistent.
    """

    if row["assessment"] is not None:
        judgment = validate_judge_response(
            request=request,
            response_json=canonical_lp_json(row["displayed_assessment"]),
        )
        canonical = (
            canonicalize_classification(judgment=judgment, request=request)
            if isinstance(judgment, ClassificationJudgment)
            else judgment
        )

        if canonical.model_dump(mode="json") != row["assessment"]:
            raise ValueError(
                "Saved canonical judgment differs from displayed response."
            )
    elif row["displayed_assessment"] is not None:
        raise ValueError("Missing judgment has an inconsistent displayed assessment.")


def _validate_requests(
    *, report: dict[str, Any], requests: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Reconcile saved rows and revalidate shown outputs without loading an execution
    store.

    Parameters
    ----------
    report
        Hash-validated saved report.
    requests
        Hash-validated saved schedule records.

    Returns
    -------
    dict[str, dict[str, Any]]
        Requests indexed by exact original identifiers.

    Raises
    ------
    ValueError
        If membership, payload, prompt, response or canonical direction differs.
    """

    indexed = {item["prompt"]["request_id"]: item for item in requests}
    rows = report["assessments"]
    ids = [row["request_id"] for row in rows]

    if (
        len(indexed) != len(requests)
        or len(set(ids)) != len(ids)
        or set(ids) != set(indexed)
    ):
        raise ValueError("Saved report/request coverage differs.")

    valid = sum(row["assessment"] is not None for row in rows)

    if (len(rows), valid, len(rows) - valid) != (
        report["planned_judgments"],
        report["valid_judgments"],
        report["missing_judgments"],
    ):
        raise ValueError("Saved report judgment totals differ.")

    for row in rows:
        raw = {
            key: value
            for key, value in indexed[row["request_id"]].items()
            if key != "doc_key"
        }
        request = TypeAdapter(ScheduledRequest).validate_json(canonical_lp_json(raw))

        for material in (raw, raw["evidence"], raw["prompt"]):
            expected = lp_material_content_hash(
                {
                    key: value
                    for key, value in material.items()
                    if key != "material_content_hash"
                }
            )

            if material["material_content_hash"] != expected:
                raise ValueError("Saved request material hash differs.")

        expected_dimensions = {
            key: raw[key] for key in ("component", "replicate", "role")
        }
        expected_dimensions.update(
            {
                key: raw["evidence"][key]
                for key in ("pair_id", "condition", "presentation")
            }
        )
        expected_dimensions.update(
            doc_key=indexed[row["request_id"]]["doc_key"],
            endpoint_uuids=raw["canonical_endpoint_uuids"],
            evidence_content_hash=raw["evidence"]["material_content_hash"],
            task=raw["prompt"]["task"],
        )

        if {key: row[key] for key in expected_dimensions} != expected_dimensions:
            raise ValueError("Saved report schedule dimensions differ.")

        prompt = raw["prompt"]

        if hashlib.sha256(raw["evidence"]["payload_json"].encode()).hexdigest() != raw[
            "evidence"
        ]["payload_sha256"] or json.loads(prompt["user_message"])[
            "evidence"
        ] != json.loads(
            raw["evidence"]["payload_json"]
        ):
            raise ValueError("Saved payload differs from shown evidence.")

        if prompt["messages_content_hash"] != lp_material_content_hash(
            {key: prompt[key] for key in ("system_message", "user_message")}
        ):
            raise ValueError("Saved model-visible messages hash differs.")

        _validate_assessment(request=request, row=row)

    return indexed


def _value_rows(*, base: dict[str, Any], path: str, value: Any) -> list[dict[str, Any]]:
    """Flatten structured settings and metrics, retaining rate membership and denominators.

    Parameters
    ----------
    base
        Shared record dimensions.
    path
        Explicit source field path.
    value
        Saved metric or setting.

    Returns
    -------
    list[dict[str, Any]]
        Flat scalar or rate rows, with no zero substitution.
    """

    if isinstance(value, dict) and "numerator" in value and "denominator" in value:
        return [{**base, "Field": path, **value}]

    if isinstance(value, dict):
        return [
            row
            for key, child in value.items()
            for row in _value_rows(base=base, path=path + "/" + key, value=child)
        ]

    if isinstance(value, list) and value and isinstance(value[0], dict):
        return [
            row
            for index, child in enumerate(value)
            for row in _value_rows(base=base, path=path + "/" + str(index), value=child)
        ]

    return [{**base, "Field": path, "Value": value}]


def _view_cell(
    *,
    denominator: list[dict[str, Any]],
    members: list[dict[str, Any]],
    planned: list[dict[str, Any]],
) -> dict[str, Any]:
    """Expose counts, pair counts and exact examples for a chart cell.

    Parameters
    ----------
    denominator
        Available assessments/comparisons for the cell's stated rate.
    members
        Numerator members.
    planned
        All scheduled members, including missing assessments.

    Returns
    -------
    dict[str, Any]
        Counts with unavailable rate when the denominator is empty.
    """

    return {
        "count": len(members),
        "denominator": len(denominator),
        "rate": len(members) / len(denominator) if denominator else None,
        "planned": len(planned),
        "unique_pairs": len({r["Pair"] for r in members}),
        "refs": [r["Ref"] for r in members],
        "denominator_refs": [r["Ref"] for r in denominator],
        "planned_refs": [r["Ref"] for r in planned],
    }


def _width(*, key: str, name: str) -> int:
    """Choose useful widths for text, references and counts.

    Parameters
    ----------
    key
        Column field.
    name
        Sheet name.

    Returns
    -------
    int
        Readable width in approximate characters.
    """

    if name == "Overview":
        return 32 if key == "Topic" else 100

    if key in {
        "Ref",
        "Pair",
        "Judgment",
        "Repetition",
        "Valid",
        "Planned",
        "Missing",
        "Claim number",
        "Member judgments",
        "Unique pairs/cases",
    }:
        return 17

    if key in {
        "Explanation",
        "Statement A",
        "Statement B",
        "Claim",
        "Rationale assessed",
        "Value",
    }:
        return 64

    return 32


def _workbook_cell(*, value: Any, width: int) -> Any:
    """Keep scalars typed and explicitly shorten long prose to a readable cell.

    Parameters
    ----------
    value
        Complete source value retained in the companion JSON/HTML.
    width
        Column character width used to bound the visible excerpt.

    Returns
    -------
    Any
        Portable spreadsheet scalar with a marked excerpt when needed.
    """

    if value is None:
        return "Unavailable"

    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)

    if isinstance(value, str) and len(value) > width * 5:
        return value[: width * 5 - 35] + " … [full text in HTML / JSON]"

    return value


def build_presentation_tables(
    *,
    manifest: dict[str, Any],
    report: dict[str, Any],
    requests: dict[str, dict[str, Any]],
    source_hash: str,
) -> dict[str, list[dict[str, Any]]]:
    """Build linked detail tables from saved, validated report content.

    Parameters
    ----------
    manifest
        Validated source report manifest.
    report
        Complete saved score report.
    requests
        Validated saved requests keyed by original request ID.
    source_hash
        Source report manifest SHA-256.

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        Plain-language overview and filterable detail tables.
    """

    tables = _tables(
        judgments=_judgment_rows(report=report, requests=requests),
        manifest=manifest,
        report=report,
        source_hash=source_hash,
    )

    for records in tables.values():
        if records and "Curriculum" in records[0]:
            records.sort(key=lambda row: row["Curriculum"])

    return tables


def build_views(
    *,
    judgments: list[dict[str, Any]],
    report: dict[str, Any],
    tables: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Construct six inspectable views without rescoring or semantic thresholds.

    Parameters
    ----------
    judgments
        Full judgment ledger in canonical endpoint coordinates.
    report
        Saved report used to enumerate absent diagnostic tags.
    tables
        Concern, claim and paired comparison tables.

    Returns
    -------
    list[dict[str, Any]]
        Filterable chart panels with exact membership and visible denominators.
    """

    names = sorted({row["Curriculum"] for row in judgments})
    return [
        {
            "title": "Candidate and publication coverage",
            "panels": _coverage(names=names, rows=judgments),
        },
        {
            "title": "Production–evaluator decision comparison",
            "panels": _decision_panels(judgments=judgments, names=names),
        },
        {
            "title": "Explanation grounding",
            "panels": _grounding_panels(judgments=judgments, names=names),
        },
        {
            "title": "Evaluator robustness",
            "panels": _robustness_panels(names=names, tables=tables),
        },
        {
            "title": "Diagnostic-group patterns",
            "panels": _diagnostic_panels(
                judgments=judgments, names=names, report=report
            ),
        },
        {
            "title": "Synthetic controls",
            "panels": _control_panels(judgments=judgments, names=names),
        },
    ]


def render_evaluation_presentations(
    *, output_directory: Path | None = None, report_directory: Path
) -> Path:
    """Render derivative Excel/HTML from an immutable report without scoring or calls.

    Parameters
    ----------
    output_directory
        Optional fresh isolated destination. Existing identical output can be reused.
    report_directory
        Exact saved content-addressed report directory.

    Returns
    -------
    Path
        Published presentation directory with workbook, HTML, complete data and manifest.

    Raises
    ------
    ValueError
        If source identity, saved results or output isolation fails validation.
    """

    source = report_directory.absolute()
    _store_path(source)
    reference = EvaluationReportArtifacts(directory=source, manifest_sha256=source.name)
    manifest, report, requests = _load_report(reference)
    tables = build_presentation_tables(
        manifest=manifest,
        report=report,
        requests=requests,
        source_hash=reference.manifest_sha256,
    )
    identity = {
        "source_report_directory": str(source),
        "source_manifest_sha256": reference.manifest_sha256,
        "source_files": manifest["files"],
        "dependencies": {
            "python": platform.python_version(),
            "xlsxwriter": version("xlsxwriter"),
            "pydantic": version("pydantic"),
        },
        "material": {
            key: manifest[key]
            for key in (
                "schedule_content_hash",
                "cache_content_hash",
                "inputs",
            )
        },
    }
    data = {
        "identity": identity,
        "tables": tables,
        "views": build_views(
            judgments=tables["Judgments"], report=report, tables=tables
        ),
        "report": report,
    }
    artifacts = {
        "learning_progressions_evaluation.xlsx": render_workbook(tables),
        "report.html": render_html(data).encode(),
        "presentation_data.json": canonical_lp_json(data).encode(),
    }
    identity["outputs"] = {
        name: {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for name, payload in artifacts.items()
    }
    destination = _destination(
        identity=identity,
        output_directory=output_directory,
        report=report,
        source=source,
    )
    artifacts["presentation_manifest.json"] = canonical_lp_json(identity).encode()
    _validated_report_manifest(reference)

    _publish_files(directory=destination, files=artifacts)
    return destination


def render_html(data: dict[str, Any]) -> str:
    """Embed escaped presentation data in an offline report with no network resources.

    Parameters
    ----------
    data
        Complete detail tables, chart memberships and saved input records.

    Returns
    -------
    str
        Standalone HTML with accessible tables and example navigation.
    """

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    compressed = gzip.compress(data=payload, mtime=0)
    return _PAGE.replace("__DATA__", base64.b64encode(compressed).decode("ascii"))


def render_workbook(tables: dict[str, list[dict[str, Any]]]) -> bytes:
    """Write flat filterable tables without macros or external data connections.

    Parameters
    ----------
    tables
        Complete detail records, also available in companion HTML and JSON.

    Returns
    -------
    bytes
        Deterministic XLSX bytes with frozen headers and explicit missing values.

    Raises
    ------
    ValueError
        If worksheet capacity would be exceeded.
    """

    stream = io.BytesIO()

    with xlsxwriter.Workbook(
        filename=stream,
        options={
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    ) as book:
        book.set_properties(
            {
                "title": "Learning progressions evaluation",
                "created": datetime(2000, 1, 1),
            }
        )
        body = book.add_format(
            {"font_name": "Arial", "font_size": 10, "text_wrap": True, "valign": "top"}
        )
        header = book.add_format(
            {
                "font_name": "Arial",
                "font_size": 10,
                "bold": True,
                "font_color": "white",
                "bg_color": "#233D56",
                "text_wrap": True,
                "valign": "vcenter",
            }
        )

        for name, original in tables.items():
            records = original or [{"Status": "No records in this saved report."}]
            columns = _columns(records)

            if len(records) > 1048575 or len(columns) > 16384:
                raise ValueError(f"{name} exceeds Excel worksheet capacity.")

            sheet = book.add_worksheet(name)
            sheet.hide_gridlines(2)
            sheet.freeze_panes(col=1, row=1)
            sheet.set_row(height=36, row=0)
            widths = [_width(key=key, name=name) for key in columns]

            for col, key in enumerate(columns):
                sheet.set_column(
                    cell_format=body, first_col=col, last_col=col, width=widths[col]
                )
                sheet.write_string(
                    cell_format=header, col=col, row=0, string=key.replace("_", " ")
                )

            for index, record in enumerate(records, 1):
                _row(
                    body=body,
                    index=index,
                    record=record,
                    sheet=sheet,
                    layout=(columns, widths),
                )

            sheet.autofilter(
                first_col=0,
                first_row=0,
                last_col=len(columns) - 1,
                last_row=len(records),
            )

    return stream.getvalue()
