const state={token:localStorage.getItem("opspilot_token"),user:null,lastRun:null};
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
async function api(path,options={}){
  const isForm=options.body instanceof FormData;
  const headers={...(isForm?{}:{"Content-Type":"application/json"}),...(options.headers||{})};
  if(state.token)headers.Authorization=`Bearer ${state.token}`;
  const response=await fetch(path,{...options,headers});
  if(response.status===401){logout();throw new Error("Please sign in again.");}
  const data=await response.json();
  if(!response.ok)throw new Error(data.detail||"Request failed");
  return data;
}
function toast(message){const el=$("#toast");el.textContent=message;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2600)}
function logout(){localStorage.removeItem("opspilot_token");state.token=null;$("#app").classList.add("hidden");$("#login").classList.remove("hidden")}
function showView(name){
  $$(".view").forEach(el=>el.classList.add("hidden"));$(`#${name}-view`).classList.remove("hidden");
  $$(".nav-item").forEach(el=>el.classList.toggle("active",el.dataset.view===name));
  const titles={overview:`Good morning, ${state.user?.name.split(" ")[0]||""}`,ask:"Knowledge Agent",documents:"Knowledge Base",connectors:"Connected Sources",approvals:"Approval Queue"};
  $("#page-title").textContent=titles[name];
  if(name==="documents")loadDocuments();if(name==="connectors")loadConnectors();if(name==="approvals")loadApprovals();
}
async function loadDashboard(){
  const data=await api("/api/dashboard"),s=data.summary;
  const cards=[
    ["Knowledge assets",s.documents,"Indexed and authorized"],
    ["Agent runs",s.queries,`${s.pending_approvals} awaiting review`],
    ["Avg. confidence",`${Math.round(s.avg_confidence*100)}%`,"Grounded answer score"],
    ["Avg. latency",`${s.avg_latency_ms} ms`,"End-to-end workflow"]
  ];
  $("#stats").innerHTML=cards.map(c=>`<article class="stat"><label>${c[0]}</label><strong>${c[1]}</strong><small>${c[2]}</small></article>`).join("");
  const pct=Math.round(s.avg_confidence*100);$("#gauge-value").textContent=`${pct}%`;$(".gauge").style.background=`conic-gradient(var(--green) ${pct*3.6}deg,#e6ebe8 0)`;
  $("#approval-badge").textContent=s.pending_approvals;
  $("#recent-runs").innerHTML=data.recent_runs.length?data.recent_runs.map(r=>`<div class="run-item"><p>${escapeHtml(r.question)}</p><span class="pill ${r.status}">${r.status.replace("_"," ")}</span><span>${Math.round(r.confidence*100)}% conf.</span><span>${r.latency_ms} ms</span></div>`).join(""):"No queries yet. Ask the knowledge agent to begin.";
}
async function loadDocuments(){
  const docs=await api("/api/documents");
  $("#document-list").innerHTML=docs.map(d=>`<article class="doc-card"><div class="doc-icon">▤</div><h4>${escapeHtml(d.title)}</h4><p>${escapeHtml(d.source)} source</p><div class="doc-meta"><span>${d.classification}</span><span>${new Date(d.created_at).toLocaleDateString()}</span></div></article>`).join("");
}
async function loadApprovals(){
  try{
    const rows=await api("/api/approvals");
    $("#approval-list").innerHTML=rows.length?rows.map(a=>`<article class="approval-card"><span class="pill pending_approval">${Math.round(a.confidence*100)}% confidence</span><h4>${escapeHtml(a.question)}</h4><p>${escapeHtml(a.answer)}</p>${state.user.role==="admin"?`<div class="approval-actions"><button onclick="decide('${a.id}','rejected')">Reject</button><button class="approve" onclick="decide('${a.id}','approved')">Approve</button></div>`:""}</article>`).join(""):"<div class='panel empty'>Nothing needs review. The queue is clear.</div>";
  }catch(e){$("#approval-list").innerHTML=`<div class="panel empty">${e.message}</div>`}
}
async function loadConnectors(){
  try{
    const rows=await api("/api/connectors");
    $("#connector-list").innerHTML=rows.length?rows.map(c=>`<article class="doc-card"><div class="doc-icon">⇄</div><h4>${escapeHtml(c.name)}</h4><p>${c.provider.replace("_"," ")}</p><div class="doc-meta"><span class="source-status">${c.status}</span><span>${c.last_synced_at?new Date(c.last_synced_at).toLocaleString():"Waiting for first event"}</span></div></article>`).join(""):"<div class='panel empty'>No sources connected yet.</div>";
  }catch(e){$("#connector-list").innerHTML=`<div class="panel empty">${e.message}</div>`}
}
async function decide(id,decision){await api(`/api/approvals/${id}/decision`,{method:"POST",body:JSON.stringify({decision})});toast(`Response ${decision}`);loadApprovals();loadDashboard()}
function escapeHtml(value){const d=document.createElement("div");d.textContent=value;return d.innerHTML}
async function initialize(){
  if(!state.token)return;
  try{
    state.user=await api("/api/auth/me");$("#login").classList.add("hidden");$("#app").classList.remove("hidden");
    $("#user-name").textContent=state.user.name;$("#user-role").textContent=state.user.role;$("#avatar").textContent=state.user.name.split(" ").map(x=>x[0]).join("");
    await loadDashboard();
  }catch(e){logout()}
}
$("#login-form").addEventListener("submit",async e=>{e.preventDefault();$("#login-error").textContent="";try{const data=await api("/api/auth/login",{method:"POST",body:JSON.stringify({email:$("#email").value,password:$("#password").value})});state.token=data.access_token;localStorage.setItem("opspilot_token",state.token);await initialize()}catch(err){$("#login-error").textContent=err.message}});
$$(".nav-item").forEach(b=>b.onclick=()=>showView(b.dataset.view));$$("[data-go]").forEach(b=>b.onclick=()=>showView(b.dataset.go));$("#logout").onclick=logout;$("#refresh").onclick=()=>{loadDashboard();toast("Workspace refreshed")};
$$(".suggestions button").forEach(b=>b.onclick=()=>{$("#question").value=b.textContent});
$("#query-form").addEventListener("submit",async e=>{e.preventDefault();const button=e.target.querySelector("button");button.disabled=true;button.textContent="Running workflow…";try{const r=await api("/api/query",{method:"POST",body:JSON.stringify({question:$("#question").value})});state.lastRun=r.id;$("#answer-panel").classList.remove("hidden");$("#answer-text").textContent=r.answer;$("#answer-status").innerHTML=`<span class="pill ${r.status}">${r.status.replace("_"," ")}</span>`;$("#answer-latency").textContent=`${r.latency_ms} ms`;$("#confidence-value").textContent=`${Math.round(r.confidence*100)}%`;$("#confidence-bar").style.width=`${r.confidence*100}%`;$("#citations").innerHTML=r.citations.map(c=>`<div class="citation"><b>${escapeHtml(c.title)}</b>Relevance ${Math.round(c.score*100)}%</div>`).join("")||"<div class='citation'>No sufficient evidence found</div>";loadDashboard()}catch(err){toast(err.message)}finally{button.disabled=false;button.innerHTML="Run agent <b>⌘↵</b>"}});
$$("[data-rating]").forEach(b=>b.onclick=async()=>{if(!state.lastRun)return;await api("/api/feedback",{method:"POST",body:JSON.stringify({query_run_id:state.lastRun,rating:Number(b.dataset.rating)})});toast("Feedback recorded")});
$("#show-ingest").onclick=()=>$("#ingest-form").classList.remove("hidden");$("#cancel-ingest").onclick=()=>$("#ingest-form").classList.add("hidden");
$("#ingest-form").addEventListener("submit",async e=>{e.preventDefault();try{await api("/api/documents",{method:"POST",body:JSON.stringify({title:$("#doc-title").value,content:$("#doc-content").value,source:$("#doc-source").value,classification:"internal"})});e.target.reset();e.target.classList.add("hidden");toast("Document indexed");loadDocuments();loadDashboard()}catch(err){toast(err.message)}});
$("#show-upload").onclick=()=>$("#upload-form").classList.remove("hidden");$("#cancel-upload").onclick=()=>$("#upload-form").classList.add("hidden");
$("#document-files").onchange=e=>{const count=e.target.files.length;e.target.closest("label").querySelector("span").textContent=count?`${count} file${count>1?"s":""} selected`:"Select or drop files here"};
$("#upload-form").addEventListener("submit",async e=>{e.preventDefault();const button=e.target.querySelector(".primary"),data=new FormData();[...$("#document-files").files].forEach(file=>data.append("files",file));data.append("classification",$("#upload-classification").value);button.disabled=true;button.textContent="Extracting and indexing…";try{const docs=await api("/api/documents/upload",{method:"POST",body:data});e.target.reset();e.target.classList.add("hidden");$(".file-drop span").textContent="Select or drop files here";toast(`${docs.length} document${docs.length>1?"s":""} indexed`);loadDocuments();loadDashboard()}catch(err){toast(err.message)}finally{button.disabled=false;button.textContent="Upload and index"}});
$("#load-samples").onclick=async()=>{const button=$("#load-samples");button.disabled=true;button.textContent="Loading…";try{const docs=await api("/api/demo/load-sample-data",{method:"POST",body:"{}"});toast(`${docs.length} sample documents ready`);loadDocuments();loadDashboard()}catch(err){toast(err.message)}finally{button.disabled=false;button.textContent="Load sample database"}};
$("#show-connector").onclick=()=>$("#connector-form").classList.remove("hidden");$("#cancel-connector").onclick=()=>$("#connector-form").classList.add("hidden");
$("#connector-form").addEventListener("submit",async e=>{e.preventDefault();try{const c=await api("/api/connectors",{method:"POST",body:JSON.stringify({provider:$("#connector-provider").value,name:$("#connector-name").value,config:{mode:"webhook"}})});$("#connector-secret").classList.remove("hidden");$("#connector-secret").innerHTML=`<b>Save this one-time webhook secret</b>${escapeHtml(c.webhook_secret)}<br><br>POST events to ${escapeHtml(c.webhook_url)} using the X-Connector-Secret header.`;e.target.reset();e.target.classList.add("hidden");toast("Source connection created");loadConnectors()}catch(err){toast(err.message)}});
initialize();
